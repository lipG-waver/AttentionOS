"""
测试 LLM 服务商故障切换（Failover）功能

测试场景：
  1. 某服务商连续失败两次（retries=1）后，自动切换到下一个服务商
  2. 使用假 API Key 遍历所有已配置的服务商（触发 401 认证失败）
  3. 验证遍历顺序：激活的主服务商排在最前，其余按配置顺序依次尝试
  4. 验证回退链的构建逻辑（过滤无 key、过滤不支持视觉的服务商）
  5. 验证全部失败时抛出 RuntimeError
  6. 验证视觉请求的服务商切换（DeepSeek 因无视觉模型被跳过）

说明：
  retries=1 → 每个服务商最多尝试 2 次（1 次 + 1 次重试）
  "失败两次后切换" = retries=1 时的标准行为
"""

import logging
import pytest
from unittest.mock import patch, MagicMock

from attention.core.llm_provider import (
    MultiLLMClient,
    LLMProvider,
)


# ================================================================== #
#  假 API Key（不含真实凭证，全部为占位符）
# ================================================================== #

FAKE_KEYS = {
    LLMProvider.MODELSCOPE: "fake-ms-api-key-1234567890",
    LLMProvider.DASHSCOPE:  "fake-ds-api-key-1234567890",
    LLMProvider.DEEPSEEK:   "fake-sk-deepseek-1234567890",
    LLMProvider.OPENAI:     "fake-sk-openai-1234567890",
    LLMProvider.CLAUDE:     "fake-sk-ant-api-1234567890",
}


# ================================================================== #
#  辅助函数
# ================================================================== #

def make_client_with_all_providers():
    """创建配置了所有 5 个服务商假 key 的客户端（激活 ModelScope）"""
    client = MultiLLMClient()
    for provider, key in FAKE_KEYS.items():
        client.set_api_key(provider, key)
    client.set_active_provider(LLMProvider.MODELSCOPE)
    return client


def make_openai_success_response(content="Success!"):
    """创建 OpenAI 兼容格式的模拟成功响应（用于 ModelScope/DashScope/DeepSeek/OpenAI）"""
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": content}}]
    }
    return mock_resp


def make_claude_success_response(content="Success from Claude!"):
    """创建 Claude 原生格式的模拟成功响应"""
    mock_resp = MagicMock()
    mock_resp.ok = True
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "content": [{"type": "text", "text": content}]
    }
    return mock_resp


def make_auth_failure_response():
    """创建模拟 401 认证失败响应（假 API Key 的典型返回）"""
    mock_resp = MagicMock()
    mock_resp.ok = False
    mock_resp.status_code = 401
    mock_resp.json.return_value = {
        "error": {"message": "Unauthorized - Invalid API key"}
    }
    return mock_resp


def patch_session():
    """返回 requests.Session 的 patch 上下文 + session mock 对象"""
    return patch("attention.core.llm_provider.requests.Session")


def setup_mock_session(mock_session_cls):
    """将 mock_session_cls 配置为上下文管理器，返回 session mock"""
    mock_session = MagicMock()
    mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_session)
    mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)
    return mock_session


# ================================================================== #
#  测试类 1：连续失败两次后切换到下一个服务商
# ================================================================== #

class TestFailoverAfterTwoFailures:
    """场景：主服务商失败两次（retries=1），自动切换到备用服务商"""

    def setup_method(self):
        self.client = make_client_with_all_providers()

    @patch("attention.core.llm_provider.requests.Session")
    def test_switches_to_second_provider_after_two_failures(self, mock_session_cls):
        """主服务商（modelscope）失败 2 次后，切换到第二个服务商（dashscope）并成功"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()
        ok   = make_openai_success_response("Response from dashscope!")

        # modelscope: 失败第1次、失败第2次；dashscope: 成功
        mock_session.post.side_effect = [fail, fail, ok]

        with patch("time.sleep"):  # 跳过重试间隔
            result = self.client.chat("hello", retries=1)

        assert result == "Response from dashscope!"
        # 共 3 次 HTTP 请求：2次（modelscope）+ 1次（dashscope）
        assert mock_session.post.call_count == 3

    @patch("attention.core.llm_provider.requests.Session")
    def test_no_failover_when_primary_succeeds_first_try(self, mock_session_cls):
        """主服务商第一次就成功时，不触发切换"""
        mock_session = setup_mock_session(mock_session_cls)

        mock_session.post.return_value = make_openai_success_response("Quick win!")

        with patch("time.sleep"):
            result = self.client.chat("hello", retries=1)

        assert result == "Quick win!"
        # 只有 1 次请求，不触发切换
        assert mock_session.post.call_count == 1

    @patch("attention.core.llm_provider.requests.Session")
    def test_no_failover_when_primary_succeeds_on_retry(self, mock_session_cls):
        """主服务商第一次失败、重试成功时，不触发切换（重试 ≠ 切换）"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()
        ok   = make_openai_success_response("Retry succeeded!")

        # 第1次失败，第2次（重试）成功 → 仍在同一服务商
        mock_session.post.side_effect = [fail, ok]

        with patch("time.sleep"):
            result = self.client.chat("hello", retries=1)

        assert result == "Retry succeeded!"
        # 2 次请求都打向同一个服务商
        assert mock_session.post.call_count == 2

    @patch("attention.core.llm_provider.requests.Session")
    def test_failure_log_contains_provider_name(self, mock_session_cls, caplog):
        """切换服务商时，日志中应含有失败服务商的名称"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()
        ok   = make_openai_success_response("OK")

        mock_session.post.side_effect = [fail, fail, ok]

        with patch("time.sleep"):
            with caplog.at_level(logging.WARNING, logger="attention.core.llm_provider"):
                self.client.chat("hello", retries=1)

        # 日志中应出现 MODELSCOPE（失败的服务商）
        # 注意：Python 3.11 中 LLMProvider.MODELSCOPE 在日志中格式化为 "LLMProvider.MODELSCOPE"
        assert any("MODELSCOPE" in record.message for record in caplog.records)


# ================================================================== #
#  测试类 2：遍历所有服务商（全部使用假 Key）
# ================================================================== #

class TestFailoverTraversesAllProviders:
    """场景：使用假 API Key，验证系统正确遍历全部已配置的服务商"""

    def setup_method(self):
        self.client = make_client_with_all_providers()

    @patch("attention.core.llm_provider.requests.Session")
    def test_all_providers_attempted_when_all_fail(self, mock_session_cls):
        """所有服务商均失败时，每个都被尝试 2 次（retries=1），共 10 次 HTTP 请求"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()
        # 5 个服务商 × 每个失败 2 次 = 10 次
        mock_session.post.side_effect = [fail] * 10

        with patch("time.sleep"):
            with pytest.raises(RuntimeError, match="所有提供商均调用失败"):
                self.client.chat("hello", retries=1)

        assert mock_session.post.call_count == 10

    @patch("attention.core.llm_provider.requests.Session")
    def test_active_provider_is_tried_first(self, mock_session_cls):
        """激活的服务商是回退链中的第一个被尝试的"""
        # 改变激活服务商为 openai
        self.client.set_active_provider(LLMProvider.OPENAI)

        mock_session = setup_mock_session(mock_session_cls)

        called_urls = []

        def capture_url(url, **kwargs):
            called_urls.append(url)
            resp = make_auth_failure_response()
            return resp

        mock_session.post.side_effect = capture_url

        with patch("time.sleep"):
            with pytest.raises(RuntimeError):
                self.client.chat("hello", retries=1)

        # openai 的 API base 包含 "openai.com"，第一次调用应是 openai 的端点
        assert "openai" in called_urls[0]

    @patch("attention.core.llm_provider.requests.Session")
    def test_third_provider_succeeds_after_first_two_fail(self, mock_session_cls):
        """前两个服务商各失败 2 次，第三个服务商成功"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()
        ok   = make_openai_success_response("Third time lucky!")

        # modelscope: 2次失败; dashscope: 2次失败; deepseek: 成功
        mock_session.post.side_effect = [fail, fail, fail, fail, ok]

        with patch("time.sleep"):
            result = self.client.chat("hello", retries=1)

        assert result == "Third time lucky!"
        assert mock_session.post.call_count == 5

    @patch("attention.core.llm_provider.requests.Session")
    def test_fourth_provider_succeeds_after_first_three_fail(self, mock_session_cls):
        """前三个服务商各失败 2 次，第四个服务商成功"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()
        ok   = make_openai_success_response("Fourth provider succeeded!")

        # 3 × 2 = 6次失败，第7次成功
        mock_session.post.side_effect = [fail] * 6 + [ok]

        with patch("time.sleep"):
            result = self.client.chat("hello", retries=1)

        assert result == "Fourth provider succeeded!"
        assert mock_session.post.call_count == 7

    @patch("attention.core.llm_provider.requests.Session")
    def test_only_three_providers_configured(self, mock_session_cls):
        """只配置 3 个服务商时，最多发起 3×2=6 次请求后抛出异常"""
        limited = MultiLLMClient()
        limited.set_api_key(LLMProvider.MODELSCOPE, FAKE_KEYS[LLMProvider.MODELSCOPE])
        limited.set_api_key(LLMProvider.DASHSCOPE,  FAKE_KEYS[LLMProvider.DASHSCOPE])
        limited.set_api_key(LLMProvider.OPENAI,     FAKE_KEYS[LLMProvider.OPENAI])
        limited.set_active_provider(LLMProvider.MODELSCOPE)

        mock_session = setup_mock_session(mock_session_cls)
        fail = make_auth_failure_response()
        mock_session.post.side_effect = [fail] * 6

        with patch("time.sleep"):
            with pytest.raises(RuntimeError):
                limited.chat("hello", retries=1)

        # 只有 3 个服务商，每个 2 次，共 6 次
        assert mock_session.post.call_count == 6


# ================================================================== #
#  测试类 3：回退链构建逻辑
# ================================================================== #

class TestFallbackChainConstruction:
    """验证 _get_fallback_chain 方法的构建逻辑"""

    def setup_method(self):
        self.client = MultiLLMClient()

    def test_empty_chain_when_no_api_keys(self):
        """没有配置任何 API key 时，回退链为空"""
        chain = self.client._get_fallback_chain(LLMProvider.MODELSCOPE)
        assert chain == []

    def test_chain_excludes_providers_without_keys(self):
        """回退链只包含配置了 API key 的服务商"""
        self.client.set_api_key(LLMProvider.OPENAI,   "fake-sk-openai")
        self.client.set_api_key(LLMProvider.DEEPSEEK, "fake-sk-deepseek")
        # 其余三个服务商无 key

        chain = self.client._get_fallback_chain(LLMProvider.MODELSCOPE)

        assert LLMProvider.OPENAI   in chain
        assert LLMProvider.DEEPSEEK in chain
        # 无 key 的三个不应出现
        assert LLMProvider.MODELSCOPE not in chain
        assert LLMProvider.DASHSCOPE  not in chain
        assert LLMProvider.CLAUDE     not in chain

    def test_primary_provider_is_first_in_chain(self):
        """主服务商（有 key）应排在回退链的第一位"""
        self.client.set_api_key(LLMProvider.OPENAI,     "fake-sk-openai")
        self.client.set_api_key(LLMProvider.DEEPSEEK,   "fake-sk-deepseek")
        self.client.set_api_key(LLMProvider.MODELSCOPE, "fake-ms-key")

        chain = self.client._get_fallback_chain(LLMProvider.OPENAI)
        assert chain[0] == LLMProvider.OPENAI

    def test_primary_without_key_not_in_chain(self):
        """主服务商没有 API key 时，不应出现在回退链中"""
        self.client.set_api_key(LLMProvider.DASHSCOPE, "fake-ds-key")

        # modelscope 无 key，不应进入链
        chain = self.client._get_fallback_chain(LLMProvider.MODELSCOPE)
        assert LLMProvider.MODELSCOPE not in chain
        assert LLMProvider.DASHSCOPE in chain

    def test_vision_chain_excludes_deepseek(self):
        """视觉请求的回退链应过滤掉没有视觉模型的 DeepSeek"""
        self.client.set_api_key(LLMProvider.MODELSCOPE, "fake-ms-key")
        self.client.set_api_key(LLMProvider.DEEPSEEK,   "fake-sk-deepseek")
        self.client.set_api_key(LLMProvider.OPENAI,     "fake-sk-openai")

        chain = self.client._get_fallback_chain(LLMProvider.MODELSCOPE, requires_vision=True)

        assert LLMProvider.DEEPSEEK   not in chain   # 无视觉模型，被过滤
        assert LLMProvider.MODELSCOPE in chain
        assert LLMProvider.OPENAI     in chain

    def test_all_five_providers_in_chain(self):
        """配置了所有 5 个服务商时，回退链长度为 5，主服务商排首位"""
        for provider, key in FAKE_KEYS.items():
            self.client.set_api_key(provider, key)

        chain = self.client._get_fallback_chain(LLMProvider.MODELSCOPE)

        assert len(chain) == 5
        assert chain[0] == LLMProvider.MODELSCOPE

    def test_chat_raises_when_chain_is_empty(self):
        """回退链为空时（无任何 key），chat 应立即抛出 RuntimeError"""
        with pytest.raises(RuntimeError, match="没有可用的提供商"):
            self.client.chat("hello")


# ================================================================== #
#  测试类 4：假 API Key 的完整切换流程
# ================================================================== #

class TestFakeApiKeyFailover:
    """使用假 API Key（触发 401），端到端验证切换流程"""

    def setup_method(self):
        self.client = make_client_with_all_providers()

    @patch("attention.core.llm_provider.requests.Session")
    def test_fake_key_401_triggers_failover_to_third(self, mock_session_cls):
        """假 key 导致的 401 错误驱动服务商切换（前两个各失败 2 次，第三个成功）"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()   # 模拟假 key 触发的 401
        ok   = make_openai_success_response("切换成功！")

        # 服务商1: 2次 401; 服务商2: 2次 401; 服务商3: 成功
        mock_session.post.side_effect = [fail, fail, fail, fail, ok]

        with patch("time.sleep"):
            result = self.client.chat("测试切换", retries=1)

        assert result == "切换成功！"
        assert mock_session.post.call_count == 5

    @patch("attention.core.llm_provider.requests.Session")
    def test_all_fake_keys_exhausted_raises_error(self, mock_session_cls):
        """所有假 key 全部触发 401，遍历结束后抛出 RuntimeError"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()
        mock_session.post.side_effect = [fail] * 20  # 足量的失败响应

        with patch("time.sleep"):
            with pytest.raises(RuntimeError) as exc_info:
                self.client.chat("全部失败测试", retries=1)

        assert "所有提供商均调用失败" in str(exc_info.value)
        # 确认 5 个服务商 × 2 次 = 10 次均被尝试
        assert mock_session.post.call_count == 10

    @patch("attention.core.llm_provider.requests.Session")
    def test_network_error_also_triggers_failover(self, mock_session_cls):
        """网络错误（非 HTTP 错误，如连接超时）同样触发服务商切换"""
        mock_session = setup_mock_session(mock_session_cls)

        ok = make_openai_success_response("网络恢复，连接成功!")

        # 前两个服务商各遇到 2 次网络错误，第三个成功
        mock_session.post.side_effect = [
            ConnectionError("Connection refused"),
            ConnectionError("Connection refused"),
            ConnectionError("Connection refused"),
            ConnectionError("Connection refused"),
            ok,
        ]

        with patch("time.sleep"):
            result = self.client.chat("hello", retries=1)

        assert result == "网络恢复，连接成功!"
        assert mock_session.post.call_count == 5

    @patch("attention.core.llm_provider.requests.Session")
    def test_timeout_error_triggers_failover(self, mock_session_cls):
        """请求超时同样触发服务商切换"""
        import socket
        mock_session = setup_mock_session(mock_session_cls)

        ok = make_openai_success_response("超时后切换成功!")

        mock_session.post.side_effect = [
            TimeoutError("Request timed out"),
            TimeoutError("Request timed out"),
            ok,
        ]

        with patch("time.sleep"):
            result = self.client.chat("hello", retries=1)

        assert result == "超时后切换成功!"
        assert mock_session.post.call_count == 3

    @patch("attention.core.llm_provider.requests.Session")
    def test_each_provider_endpoint_is_actually_called(self, mock_session_cls):
        """验证每个服务商的真实 API 端点都被调用到"""
        mock_session = setup_mock_session(mock_session_cls)

        called_urls = []

        def capture_and_fail(url, **kwargs):
            called_urls.append(url)
            return make_auth_failure_response()

        mock_session.post.side_effect = capture_and_fail

        with patch("time.sleep"):
            with pytest.raises(RuntimeError):
                self.client.chat("hello", retries=1)

        # 5 个服务商各被调用 2 次，共 10 个 URL
        assert len(called_urls) == 10

        unique_bases = set()
        for url in called_urls:
            # 每个 URL 都包含对应提供商的域名特征
            for keyword in ["modelscope", "dashscope", "deepseek", "openai", "anthropic"]:
                if keyword in url:
                    unique_bases.add(keyword)
                    break

        # 5 个不同的 API base 都被调用到（注意 Claude 用 anthropic.com）
        assert len(unique_bases) == 5, f"期望 5 个不同端点，实际调用: {unique_bases}"


# ================================================================== #
#  测试类 5：视觉请求的服务商切换
# ================================================================== #

class TestVisionFailover:
    """验证视觉请求的回退行为（自动跳过无视觉模型的服务商）"""

    def setup_method(self):
        self.client = make_client_with_all_providers()

    @patch("attention.core.llm_provider.requests.Session")
    def test_vision_skips_deepseek_no_vision_model(self, mock_session_cls):
        """视觉请求中 DeepSeek（无视觉模型）被跳过，直接从 OpenAI 开始尝试"""
        # 仅配置 deepseek 和 openai
        client = MultiLLMClient()
        client.set_api_key(LLMProvider.DEEPSEEK, "fake-sk-deepseek")
        client.set_api_key(LLMProvider.OPENAI,   "fake-sk-openai")
        client.set_active_provider(LLMProvider.DEEPSEEK)  # 激活无视觉模型的服务商

        mock_session = setup_mock_session(mock_session_cls)
        mock_session.post.return_value = make_openai_success_response("图片分析完成!")

        # DeepSeek 无视觉模型，应直接跳到 OpenAI
        result = client.vision("描述图片", "base64data")

        assert result == "图片分析完成!"
        # 只调用 OpenAI（DeepSeek 直接被过滤，未发出 HTTP 请求）
        assert mock_session.post.call_count == 1

    @patch("attention.core.llm_provider.requests.Session")
    def test_vision_failover_after_two_failures(self, mock_session_cls):
        """视觉主服务商失败 2 次后，切换到下一个支持视觉的服务商"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()
        ok   = make_openai_success_response("视觉切换成功!")

        # modelscope 视觉失败 2 次 → 切换到 dashscope 成功
        mock_session.post.side_effect = [fail, fail, ok]

        with patch("time.sleep"):
            result = self.client.vision("描述", "base64img", retries=1)

        assert result == "视觉切换成功!"
        assert mock_session.post.call_count == 3

    @patch("attention.core.llm_provider.requests.Session")
    def test_vision_all_fail_raises_error(self, mock_session_cls):
        """所有视觉服务商均失败时，抛出 RuntimeError"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()
        # 视觉链有 4 个服务商（DeepSeek 被过滤），每个 2 次 = 8 次
        mock_session.post.side_effect = [fail] * 8

        with patch("time.sleep"):
            with pytest.raises(RuntimeError, match="所有视觉提供商均调用失败"):
                self.client.vision("描述", "base64img", retries=1)

    @patch("attention.core.llm_provider.requests.Session")
    def test_vision_no_providers_raises_error(self, mock_session_cls):
        """没有配置视觉能力的服务商时，应立即抛出 RuntimeError"""
        client = MultiLLMClient()
        # 只配置 DeepSeek（无视觉模型）
        client.set_api_key(LLMProvider.DEEPSEEK, "fake-sk-deepseek")

        with pytest.raises(RuntimeError, match="没有支持视觉的可用提供商"):
            client.vision("描述", "base64img")


# ================================================================== #
#  测试类 6：指定 provider 时不启用回退
# ================================================================== #

class TestNoFailoverWhenProviderExplicit:
    """当显式指定 provider 时，不启用自动回退"""

    def setup_method(self):
        self.client = make_client_with_all_providers()

    @patch("attention.core.llm_provider.requests.Session")
    def test_explicit_provider_no_fallback(self, mock_session_cls):
        """显式指定的服务商失败后，不切换，直接抛出异常"""
        mock_session = setup_mock_session(mock_session_cls)

        fail = make_auth_failure_response()
        mock_session.post.side_effect = [fail] * 10

        with patch("time.sleep"):
            with pytest.raises(RuntimeError, match="调用失败"):
                self.client.chat("hello", provider=LLMProvider.OPENAI, retries=1)

        # 只有 openai 被调用（2次），不会切换到其他服务商
        assert mock_session.post.call_count == 2

    def test_explicit_provider_without_key_raises(self):
        """显式指定未配置 key 的服务商时，应立即抛出异常"""
        client = MultiLLMClient()  # 空配置，无任何 key

        with pytest.raises(RuntimeError, match="未配置 API key"):
            client.chat("hello", provider=LLMProvider.OPENAI)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
