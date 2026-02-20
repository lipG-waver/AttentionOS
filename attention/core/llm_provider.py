"""
多源 LLM 统一调用模块

支持的 API 提供商：
  1. 阿里云百炼 (DashScope) — 通义千问系列
  2. DeepSeek API
  3. OpenAI (ChatGPT) API
  4. Anthropic Claude API
  5. ModelScope API（原有，兼容保留）

所有提供商均通过 OpenAI 兼容接口调用（Claude 除外，使用原生接口）。
各业务模块通过 get_llm_provider() 获取单例即可，无需关心底层 API 差异。
"""
import json
import logging
import time
from enum import Enum
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict

import requests

logger = logging.getLogger(__name__)


# ================================================================== #
#  提供商定义
# ================================================================== #

class LLMProvider(str, Enum):
    """支持的 LLM 提供商"""
    MODELSCOPE = "modelscope"
    DASHSCOPE = "dashscope"       # 阿里云百炼
    DEEPSEEK = "deepseek"
    OPENAI = "openai"
    CLAUDE = "claude"


@dataclass
class ProviderConfig:
    """单个提供商的配置"""
    provider: str
    api_key: str = ""
    api_base: str = ""
    text_model: str = ""
    vision_model: str = ""
    enabled: bool = False
    display_name: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        # 隐藏 API key，仅返回是否已配置
        d["api_key_set"] = bool(self.api_key)
        d["api_key"] = ""  # 不暴露给前端
        return d

    def to_dict_with_key(self) -> dict:
        """内部使用，包含完整 API key"""
        return asdict(self)


# 默认提供商配置
DEFAULT_CONFIGS: Dict[str, ProviderConfig] = {
    LLMProvider.MODELSCOPE: ProviderConfig(
        provider=LLMProvider.MODELSCOPE,
        api_base="https://api-inference.modelscope.cn/v1",
        text_model="Qwen/Qwen2.5-72B-Instruct",
        vision_model="Qwen/Qwen2.5-VL-72B-Instruct",
        display_name="ModelScope 魔搭",
    ),
    LLMProvider.DASHSCOPE: ProviderConfig(
        provider=LLMProvider.DASHSCOPE,
        api_base="https://dashscope.aliyuncs.com/compatible-mode/v1",
        text_model="qwen-plus",
        vision_model="qwen-vl-max",
        display_name="阿里云百炼",
    ),
    LLMProvider.DEEPSEEK: ProviderConfig(
        provider=LLMProvider.DEEPSEEK,
        api_base="https://api.deepseek.com/v1",
        text_model="deepseek-chat",
        vision_model="",  # DeepSeek 暂无视觉模型
        display_name="DeepSeek",
    ),
    LLMProvider.OPENAI: ProviderConfig(
        provider=LLMProvider.OPENAI,
        api_base="https://api.openai.com/v1",
        text_model="gpt-4o-mini",
        vision_model="gpt-4o",
        display_name="OpenAI ChatGPT",
    ),
    LLMProvider.CLAUDE: ProviderConfig(
        provider=LLMProvider.CLAUDE,
        api_base="https://api.anthropic.com",
        text_model="claude-sonnet-4-20250514",
        vision_model="claude-sonnet-4-20250514",
        display_name="Anthropic Claude",
    ),
}


# ================================================================== #
#  统一客户端
# ================================================================== #

class MultiLLMClient:
    """
    多源 LLM 统一客户端。

    自动选择当前激活的提供商发起请求，
    支持文本对话、JSON 解析对话、视觉分析。
    """

    def __init__(self):
        self._configs: Dict[str, ProviderConfig] = {}
        self._active_provider: str = LLMProvider.MODELSCOPE
        self._load_defaults()

    def _load_defaults(self):
        """加载默认配置"""
        for key, cfg in DEFAULT_CONFIGS.items():
            self._configs[key] = ProviderConfig(**asdict(cfg))

        # 从环境变量兼容旧配置
        import os
        ms_key = os.getenv("MODELSCOPE_ACCESS_TOKEN", "")
        if ms_key:
            self._configs[LLMProvider.MODELSCOPE].api_key = ms_key
            self._configs[LLMProvider.MODELSCOPE].enabled = True

    # ---------------------------------------------------------------- #
    #  配置管理
    # ---------------------------------------------------------------- #

    def get_all_configs(self) -> List[dict]:
        """获取所有提供商配置（不含 API key 明文）"""
        return [cfg.to_dict() for cfg in self._configs.values()]

    def get_config(self, provider: str) -> Optional[ProviderConfig]:
        """获取指定提供商配置"""
        return self._configs.get(provider)

    def set_api_key(self, provider: str, api_key: str) -> bool:
        """设置指定提供商的 API key"""
        cfg = self._configs.get(provider)
        if not cfg:
            return False
        cfg.api_key = api_key
        cfg.enabled = bool(api_key)
        return True

    def set_active_provider(self, provider: str) -> bool:
        """设置当前激活的提供商"""
        if provider not in self._configs:
            return False
        cfg = self._configs[provider]
        if not cfg.api_key:
            return False
        self._active_provider = provider
        return True

    def get_active_provider(self) -> str:
        return self._active_provider

    def update_provider_config(self, provider: str, **kwargs) -> bool:
        """更新提供商配置（api_base, text_model, vision_model 等）"""
        cfg = self._configs.get(provider)
        if not cfg:
            return False
        for key, val in kwargs.items():
            if hasattr(cfg, key) and key != "provider":
                setattr(cfg, key, val)
        return True

    # ---------------------------------------------------------------- #
    #  文本对话
    # ---------------------------------------------------------------- #

    def chat(
        self,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int = 500,
        temperature: float = 0.7,
        timeout: int = 15,
        retries: int = 2,
        provider: Optional[str] = None,
    ) -> str:
        """
        文本对话，返回模型生成的纯文本。

        Args:
            prompt:      用户消息
            system:      可选的 system prompt
            model:       覆盖默认文本模型
            max_tokens:  最大生成 token 数
            temperature: 采样温度
            timeout:     请求超时秒数
            retries:     失败重试次数
            provider:    指定提供商（默认使用当前激活的）

        Returns:
            模型生成的文本内容
        """
        provider = provider or self._active_provider
        cfg = self._configs.get(provider)
        if not cfg or not cfg.api_key:
            raise RuntimeError(f"提供商 {provider} 未配置 API key")

        model = model or cfg.text_model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_err = None
        for attempt in range(1 + retries):
            try:
                resp = self._post(
                    cfg=cfg,
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                )
                return resp
            except Exception as e:
                last_err = e
                logger.warning(f"LLM chat 第 {attempt+1} 次失败 [{provider}]: {e}")
                if attempt < retries:
                    time.sleep(2)
        raise RuntimeError(f"LLM chat 调用失败（{provider}，已重试 {retries} 次）: {last_err}")

    def chat_json(self, prompt: str, **kwargs) -> dict:
        """文本对话，自动解析返回的 JSON"""
        text = self.chat(prompt, **kwargs)
        text = text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        return json.loads(text)

    # ---------------------------------------------------------------- #
    #  视觉分析
    # ---------------------------------------------------------------- #

    def vision(
        self,
        prompt: str,
        image_base64: str,
        image_type: str = "image/jpeg",
        max_tokens: int = 800,
        timeout: int = 30,
        provider: Optional[str] = None,
    ) -> str:
        """
        视觉分析：接收一张图片 + 文本提示，返回模型输出。
        """
        provider = provider or self._active_provider
        cfg = self._configs.get(provider)
        if not cfg or not cfg.api_key:
            raise RuntimeError(f"提供商 {provider} 未配置 API key")

        if not cfg.vision_model:
            raise RuntimeError(f"提供商 {provider} 不支持视觉模型")

        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image_type};base64,{image_base64}"
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ]
        return self._post(
            cfg=cfg,
            model=cfg.vision_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
            timeout=timeout,
        )

    # ---------------------------------------------------------------- #
    #  API key 连通性测试
    # ---------------------------------------------------------------- #

    def test_api_key(self, provider: str, api_key: Optional[str] = None) -> Dict[str, Any]:
        """
        测试指定提供商的 API key 连通性。

        Returns:
            {"success": True/False, "message": "...", "model": "...", "latency_ms": ...}
        """
        cfg = self._configs.get(provider)
        if not cfg:
            return {"success": False, "message": f"未知的提供商: {provider}"}

        test_key = api_key or cfg.api_key
        if not test_key:
            return {"success": False, "message": "API key 为空"}

        # 创建临时配置
        test_cfg = ProviderConfig(**asdict(cfg))
        test_cfg.api_key = test_key

        start = time.time()
        try:
            result = self._post(
                cfg=test_cfg,
                model=test_cfg.text_model,
                messages=[{"role": "user", "content": "Hi, reply with OK"}],
                max_tokens=10,
                temperature=0,
                timeout=15,
            )
            latency = int((time.time() - start) * 1000)
            return {
                "success": True,
                "message": f"连接成功！模型回复: {result[:50]}",
                "model": test_cfg.text_model,
                "latency_ms": latency,
            }
        except Exception as e:
            latency = int((time.time() - start) * 1000)
            return {
                "success": False,
                "message": f"连接失败: {str(e)}",
                "model": test_cfg.text_model,
                "latency_ms": latency,
            }

    # ---------------------------------------------------------------- #
    #  内部 — HTTP 调用
    # ---------------------------------------------------------------- #

    def _post(
        self,
        cfg: ProviderConfig,
        model: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        """统一的 HTTP POST 调用，自动适配不同提供商"""
        if cfg.provider == LLMProvider.CLAUDE:
            return self._post_claude(cfg, model, messages, max_tokens, temperature, timeout)
        else:
            return self._post_openai_compatible(cfg, model, messages, max_tokens, temperature, timeout)

    def _post_openai_compatible(
        self,
        cfg: ProviderConfig,
        model: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        """OpenAI 兼容接口调用（ModelScope, DashScope, DeepSeek, OpenAI）"""
        headers = {
            "Authorization": f"Bearer {cfg.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                f"{cfg.api_base}/chat/completions",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        response.raise_for_status()
        result = response.json()
        content = (
            result.get("choices", [{}])[0]
            .get("message", {})
            .get("content", "")
        )
        if not content:
            raise ValueError("LLM 返回内容为空")
        return content

    def _post_claude(
        self,
        cfg: ProviderConfig,
        model: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        """Anthropic Claude 原生接口调用"""
        headers = {
            "x-api-key": cfg.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

        # 分离 system 和 user/assistant 消息
        system_text = ""
        claude_messages = []
        for msg in messages:
            if msg["role"] == "system":
                system_text = msg["content"] if isinstance(msg["content"], str) else ""
            else:
                claude_messages.append(msg)

        payload = {
            "model": model,
            "messages": claude_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system_text:
            payload["system"] = system_text

        with requests.Session() as session:
            session.trust_env = False
            response = session.post(
                f"{cfg.api_base}/v1/messages",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
        response.raise_for_status()
        result = response.json()

        # Claude 返回格式不同
        content_blocks = result.get("content", [])
        if content_blocks:
            text_parts = [b.get("text", "") for b in content_blocks if b.get("type") == "text"]
            content = "".join(text_parts)
        else:
            content = ""

        if not content:
            raise ValueError("Claude 返回内容为空")
        return content


# ================================================================== #
#  单例
# ================================================================== #

_client: Optional[MultiLLMClient] = None


def get_llm_provider() -> MultiLLMClient:
    """获取 MultiLLMClient 单例"""
    global _client
    if _client is None:
        _client = MultiLLMClient()
    return _client
