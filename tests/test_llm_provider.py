"""
测试多源 LLM 统一调用模块

测试内容：
  1. 提供商配置管理（增/删/改/查）
  2. API key 设置与切换
  3. 各提供商的请求格式正确性
  4. Claude 与 OpenAI 兼容接口的区别处理
  5. 重试机制
  6. API key 连通性测试
  7. API 设置持久化
"""

import json
import os
import pytest
import tempfile
from unittest.mock import patch, MagicMock, PropertyMock
from pathlib import Path

# 在导入模块前设置环境变量，避免初始化依赖
os.environ.setdefault("MODELSCOPE_ACCESS_TOKEN", "test-token-12345")

from attention.core.llm_provider import (
    MultiLLMClient, LLMProvider, ProviderConfig, DEFAULT_CONFIGS, get_llm_provider,
)


class TestProviderConfig:
    """测试 ProviderConfig 数据结构"""

    def test_to_dict_hides_api_key(self):
        """to_dict() 不应暴露 API key 明文"""
        cfg = ProviderConfig(
            provider="openai",
            api_key="sk-secret-key-12345",
            api_base="https://api.openai.com/v1",
            text_model="gpt-4o-mini",
        )
        d = cfg.to_dict()
        assert d["api_key"] == ""  # 隐藏
        assert d["api_key_set"] is True  # 但标记已配置

    def test_to_dict_no_key(self):
        """未配置 key 时 api_key_set 应为 False"""
        cfg = ProviderConfig(provider="openai")
        d = cfg.to_dict()
        assert d["api_key_set"] is False

    def test_to_dict_with_key_exposes_key(self):
        """to_dict_with_key() 应包含完整 key（内部用）"""
        cfg = ProviderConfig(provider="openai", api_key="sk-123")
        d = cfg.to_dict_with_key()
        assert d["api_key"] == "sk-123"


class TestDefaultConfigs:
    """测试默认配置"""

    def test_all_providers_have_defaults(self):
        """所有枚举的提供商都应有默认配置"""
        for provider in LLMProvider:
            assert provider in DEFAULT_CONFIGS, f"缺少 {provider} 的默认配置"

    def test_default_api_bases(self):
        """验证各提供商的默认 API 地址"""
        assert "modelscope" in DEFAULT_CONFIGS[LLMProvider.MODELSCOPE].api_base
        assert "dashscope" in DEFAULT_CONFIGS[LLMProvider.DASHSCOPE].api_base
        assert "deepseek" in DEFAULT_CONFIGS[LLMProvider.DEEPSEEK].api_base
        assert "openai" in DEFAULT_CONFIGS[LLMProvider.OPENAI].api_base
        assert "anthropic" in DEFAULT_CONFIGS[LLMProvider.CLAUDE].api_base

    def test_default_models_not_empty(self):
        """各提供商的默认文本模型不应为空"""
        for provider in LLMProvider:
            cfg = DEFAULT_CONFIGS[provider]
            assert cfg.text_model, f"{provider} 的 text_model 为空"

    def test_all_providers_have_display_name(self):
        """各提供商应有可读的 display_name"""
        for provider in LLMProvider:
            cfg = DEFAULT_CONFIGS[provider]
            assert cfg.display_name, f"{provider} 的 display_name 为空"


class TestMultiLLMClient:
    """测试 MultiLLMClient 核心功能"""

    def setup_method(self):
        self.client = MultiLLMClient()

    def test_init_loads_modelscope_from_env(self):
        """初始化时应从环境变量加载 ModelScope token"""
        cfg = self.client.get_config(LLMProvider.MODELSCOPE)
        assert cfg is not None
        assert cfg.api_key == "test-token-12345"
        assert cfg.enabled is True

    def test_get_all_configs(self):
        """获取所有配置应返回 5 个提供商"""
        configs = self.client.get_all_configs()
        assert len(configs) == 5
        providers = {c["provider"] for c in configs}
        assert "modelscope" in providers
        assert "dashscope" in providers
        assert "deepseek" in providers
        assert "openai" in providers
        assert "claude" in providers

    def test_set_api_key(self):
        """设置 API key 应成功"""
        ok = self.client.set_api_key("openai", "sk-test-key")
        assert ok is True
        cfg = self.client.get_config("openai")
        assert cfg.api_key == "sk-test-key"
        assert cfg.enabled is True

    def test_set_api_key_invalid_provider(self):
        """设置不存在的提供商应返回 False"""
        ok = self.client.set_api_key("nonexistent", "key")
        assert ok is False

    def test_set_active_provider(self):
        """设置激活提供商应成功"""
        self.client.set_api_key("openai", "sk-test-key")
        ok = self.client.set_active_provider("openai")
        assert ok is True
        assert self.client.get_active_provider() == "openai"

    def test_set_active_provider_without_key(self):
        """没有 API key 的提供商不能激活"""
        ok = self.client.set_active_provider("deepseek")
        assert ok is False

    def test_update_provider_config(self):
        """更新提供商配置"""
        ok = self.client.update_provider_config("openai", text_model="gpt-4o", api_base="https://custom.api.com/v1")
        assert ok is True
        cfg = self.client.get_config("openai")
        assert cfg.text_model == "gpt-4o"
        assert cfg.api_base == "https://custom.api.com/v1"

    def test_update_provider_config_invalid(self):
        """更新不存在的提供商应返回 False"""
        ok = self.client.update_provider_config("nonexistent", text_model="x")
        assert ok is False

    def test_default_active_is_modelscope(self):
        """默认激活的提供商应为 ModelScope"""
        assert self.client.get_active_provider() == LLMProvider.MODELSCOPE


class TestMultiLLMClientChat:
    """测试 chat 方法"""

    def setup_method(self):
        self.client = MultiLLMClient()

    def test_chat_without_api_key_raises(self):
        """没有 API key 时 chat 应抛异常"""
        self.client.set_api_key(LLMProvider.MODELSCOPE, "")
        self.client._configs[LLMProvider.MODELSCOPE].enabled = False
        # 切换到一个没有 key 的提供商
        with pytest.raises(RuntimeError, match="未配置 API key"):
            self.client.chat("hello", provider="deepseek")

    @patch("attention.core.llm_provider.requests.Session")
    def test_chat_openai_compatible_format(self, mock_session_cls):
        """OpenAI 兼容接口应发送正确格式"""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "Hello!"}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_session.post.return_value = mock_response

        result = self.client.chat("hi", system="Be helpful")

        # 验证请求参数
        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        assert payload["messages"][0]["role"] == "system"
        assert payload["messages"][0]["content"] == "Be helpful"
        assert payload["messages"][1]["role"] == "user"
        assert payload["messages"][1]["content"] == "hi"
        assert "Authorization" in call_args[1]["headers"]
        assert result == "Hello!"

    @patch("attention.core.llm_provider.requests.Session")
    def test_chat_claude_format(self, mock_session_cls):
        """Claude 接口应使用原生格式（非 OpenAI 兼容）"""
        self.client.set_api_key("claude", "sk-ant-test")
        self.client.set_active_provider("claude")

        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "content": [{"type": "text", "text": "Hello from Claude!"}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_session.post.return_value = mock_response

        result = self.client.chat("hi", system="Be helpful")

        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        headers = call_args[1]["headers"]

        # Claude 使用 x-api-key 而非 Authorization
        assert "x-api-key" in headers
        assert headers["x-api-key"] == "sk-ant-test"
        # system 应单独传递
        assert payload.get("system") == "Be helpful"
        # messages 不应包含 system
        for msg in payload["messages"]:
            assert msg["role"] != "system"
        assert result == "Hello from Claude!"

    @patch("attention.core.llm_provider.requests.Session")
    def test_chat_retry_on_failure(self, mock_session_cls):
        """chat 失败时应重试"""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        # 前两次失败，第三次成功
        mock_fail = MagicMock(side_effect=ConnectionError("timeout"))
        mock_success = MagicMock()
        mock_success.json.return_value = {"choices": [{"message": {"content": "OK"}}]}
        mock_success.raise_for_status = MagicMock()

        mock_session.post.side_effect = [ConnectionError("fail1"), ConnectionError("fail2"), mock_success]

        # retries=2 意味着最多尝试 3 次（1 + 2）
        with patch("time.sleep"):  # 跳过重试延迟
            result = self.client.chat("hi", retries=2)
        assert result == "OK"
        assert mock_session.post.call_count == 3

    @patch("attention.core.llm_provider.requests.Session")
    def test_chat_exhausted_retries_raises(self, mock_session_cls):
        """重试耗尽后应抛异常"""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.post.side_effect = ConnectionError("always fail")

        with patch("time.sleep"):
            with pytest.raises(RuntimeError, match="调用失败"):
                self.client.chat("hi", retries=1)

    def test_chat_json_parses_code_block(self):
        """chat_json 应正确解析 markdown code block"""
        with patch.object(self.client, "chat", return_value='```json\n{"key": "value"}\n```'):
            result = self.client.chat_json("parse this")
            assert result == {"key": "value"}

    def test_chat_json_parses_plain_json(self):
        """chat_json 应正确解析纯 JSON"""
        with patch.object(self.client, "chat", return_value='{"a": 1}'):
            result = self.client.chat_json("parse this")
            assert result == {"a": 1}


class TestMultiLLMClientVision:
    """测试视觉分析"""

    def setup_method(self):
        self.client = MultiLLMClient()

    def test_vision_without_model_raises(self):
        """提供商无视觉模型时应抛异常"""
        self.client.set_api_key("deepseek", "sk-test")
        with pytest.raises(RuntimeError, match="不支持视觉模型"):
            self.client.vision("describe", "base64data", provider="deepseek")

    @patch("attention.core.llm_provider.requests.Session")
    def test_vision_sends_image(self, mock_session_cls):
        """vision 应正确发送图片数据"""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "A cat"}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_session.post.return_value = mock_response

        result = self.client.vision("describe", "base64imagedata")

        call_args = mock_session.post.call_args
        payload = call_args[1]["json"]
        user_content = payload["messages"][0]["content"]
        assert any("image_url" in item for item in user_content if isinstance(item, dict))
        assert result == "A cat"


class TestMultiLLMClientTestAPIKey:
    """测试 API key 连通性测试"""

    def setup_method(self):
        self.client = MultiLLMClient()

    def test_test_unknown_provider(self):
        """未知提供商应返回失败"""
        result = self.client.test_api_key("unknown_provider")
        assert result["success"] is False
        assert "未知" in result["message"]

    def test_test_empty_key(self):
        """空 key 应返回失败"""
        result = self.client.test_api_key("openai", "")
        assert result["success"] is False
        assert "为空" in result["message"]

    @patch("attention.core.llm_provider.requests.Session")
    def test_test_success(self, mock_session_cls):
        """成功的连通性测试"""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "OK"}}]
        }
        mock_response.raise_for_status = MagicMock()
        mock_session.post.return_value = mock_response

        result = self.client.test_api_key("openai", "sk-test-key")
        assert result["success"] is True
        assert "成功" in result["message"]
        assert result["latency_ms"] >= 0

    @patch("attention.core.llm_provider.requests.Session")
    def test_test_failure(self, mock_session_cls):
        """失败的连通性测试"""
        mock_session = MagicMock()
        mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
        mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
        mock_session.post.side_effect = ConnectionError("Connection refused")

        result = self.client.test_api_key("openai", "sk-bad-key")
        assert result["success"] is False
        assert "失败" in result["message"]


class TestAPISettingsManager:
    """测试 API 设置持久化"""

    def test_save_and_load(self):
        """保存和加载 API 设置"""
        import attention.core.api_settings as api_mod
        import attention.core.llm_provider as lp

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = Path(tmpdir) / "api_settings.json"

            orig_file = api_mod.SETTINGS_FILE
            orig_data_dir = api_mod.Config.DATA_DIR
            try:
                api_mod.SETTINGS_FILE = settings_file
                api_mod.Config.DATA_DIR = Path(tmpdir)
                lp._client = None

                mgr = api_mod.APISettingsManager()
                mgr.set_api_key("openai", "sk-test-persist")
                mgr.save()

                assert settings_file.exists()
                data = json.loads(settings_file.read_text())
                assert data["providers"]["openai"]["api_key"] == "sk-test-persist"
            finally:
                api_mod.SETTINGS_FILE = orig_file
                api_mod.Config.DATA_DIR = orig_data_dir
                lp._client = None

    def test_get_all_configs_marks_active(self):
        """get_all_configs 应标记当前激活的提供商"""
        import attention.core.api_settings as api_mod
        import attention.core.llm_provider as lp

        with tempfile.TemporaryDirectory() as tmpdir:
            settings_file = Path(tmpdir) / "api_settings.json"
            orig_file = api_mod.SETTINGS_FILE
            orig_data_dir = api_mod.Config.DATA_DIR
            try:
                api_mod.SETTINGS_FILE = settings_file
                api_mod.Config.DATA_DIR = Path(tmpdir)
                lp._client = None

                mgr = api_mod.APISettingsManager()
                configs = mgr.get_all_configs()

                active_count = sum(1 for c in configs if c.get("is_active"))
                assert active_count == 1
            finally:
                api_mod.SETTINGS_FILE = orig_file
                api_mod.Config.DATA_DIR = orig_data_dir
                lp._client = None


class TestProviderEnum:
    """测试 LLMProvider 枚举"""

    def test_enum_values(self):
        assert LLMProvider.MODELSCOPE == "modelscope"
        assert LLMProvider.DASHSCOPE == "dashscope"
        assert LLMProvider.DEEPSEEK == "deepseek"
        assert LLMProvider.OPENAI == "openai"
        assert LLMProvider.CLAUDE == "claude"

    def test_enum_is_string(self):
        """枚举应可作为字符串使用"""
        assert isinstance(LLMProvider.OPENAI, str)
        assert LLMProvider.OPENAI.value == "openai"
        assert str(LLMProvider.OPENAI.value) == "openai"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
