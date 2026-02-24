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
        vision_model="Qwen/Qwen3-VL-235B-A22B-Instruct",
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
    #  内部 — 提供商回退链
    # ---------------------------------------------------------------- #

    def _get_fallback_chain(self, primary: str, requires_vision: bool = False) -> List[str]:
        """
        构建提供商回退链。

        Args:
            primary:         首选提供商名称
            requires_vision: 若为 True，则只包含配置了视觉模型的提供商

        Returns:
            按优先级排列的提供商名称列表（首个为 primary）
        """
        chain: List[str] = []
        # 首选提供商排首位
        primary_cfg = self._configs.get(primary)
        if primary_cfg and primary_cfg.api_key:
            if not requires_vision or primary_cfg.vision_model:
                chain.append(primary)
        # 其余已配置 API key 的提供商依次追加
        for prov, cfg in self._configs.items():
            if prov == primary:
                continue
            if not cfg.api_key:
                continue
            if requires_vision and not cfg.vision_model:
                continue
            chain.append(prov)
        return chain

    def _chat_with_provider(
        self,
        provider: str,
        prompt: str,
        *,
        system: Optional[str] = None,
        model: Optional[str] = None,
        max_tokens: int,
        temperature: float,
        timeout: int,
        retries: int,
    ) -> str:
        """对指定提供商执行带重试的文本对话，失败则抛出异常。"""
        cfg = self._configs.get(provider)
        if not cfg or not cfg.api_key:
            raise RuntimeError(f"提供商 {provider} 未配置 API key")

        use_model = model or cfg.text_model
        messages: List[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_err: Optional[Exception] = None
        for attempt in range(1 + retries):
            try:
                return self._post(
                    cfg=cfg,
                    model=use_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                )
            except Exception as e:
                last_err = e
                logger.warning(f"LLM chat 第 {attempt+1} 次失败 [{provider}]: {e}")
                if attempt < retries:
                    time.sleep(2)
        raise RuntimeError(
            f"LLM chat 调用失败（{provider}，已重试 {retries} 次）: {last_err}"
        )

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

        若未指定 provider，则在激活提供商失败后自动回退到其他已配置的提供商。

        Args:
            prompt:      用户消息
            system:      可选的 system prompt
            model:       覆盖默认文本模型
            max_tokens:  最大生成 token 数
            temperature: 采样温度
            timeout:     请求超时秒数
            retries:     每个提供商的失败重试次数
            provider:    指定提供商（指定后不启用自动回退）

        Returns:
            模型生成的文本内容
        """
        # 显式指定提供商时，不启用回退
        if provider is not None:
            return self._chat_with_provider(
                provider, prompt,
                system=system, model=model,
                max_tokens=max_tokens, temperature=temperature,
                timeout=timeout, retries=retries,
            )

        # 未指定时，构建回退链
        chain = self._get_fallback_chain(self._active_provider, requires_vision=False)
        if not chain:
            raise RuntimeError("没有可用的提供商，请先在设置中配置 API key")

        last_err: Optional[Exception] = None
        for prov in chain:
            try:
                result = self._chat_with_provider(
                    prov, prompt,
                    system=system, model=model,
                    max_tokens=max_tokens, temperature=temperature,
                    timeout=timeout, retries=retries,
                )
                if prov != self._active_provider:
                    logger.info(f"已切换到备用提供商 [{prov}] 完成 chat 请求")
                return result
            except Exception as e:
                last_err = e
                logger.warning(f"提供商 [{prov}] 全部重试耗尽，尝试下一个备用提供商: {e}")

        raise RuntimeError(f"所有提供商均调用失败: {last_err}")

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
        retries: int = 2,
        provider: Optional[str] = None,
    ) -> str:
        """
        视觉分析：接收一张图片 + 文本提示，返回模型输出。

        若未指定 provider，则在激活提供商失败后自动回退到其他支持视觉的提供商。

        Args:
            prompt:       文本提示
            image_base64: base64 编码的图片数据
            image_type:   图片 MIME 类型（默认 image/jpeg）
            max_tokens:   最大生成 token 数
            timeout:      请求超时秒数
            retries:      每个提供商的失败重试次数
            provider:     指定提供商（指定后不启用自动回退）
        """
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

        def _call_one(prov: str) -> str:
            cfg = self._configs.get(prov)
            if not cfg or not cfg.api_key:
                raise RuntimeError(f"提供商 {prov} 未配置 API key")
            if not cfg.vision_model:
                raise RuntimeError(f"提供商 {prov} 不支持视觉模型")
            last_err: Optional[Exception] = None
            for attempt in range(1 + retries):
                try:
                    return self._post(
                        cfg=cfg,
                        model=cfg.vision_model,
                        messages=messages,
                        max_tokens=max_tokens,
                        temperature=0.3,
                        timeout=timeout,
                    )
                except Exception as e:
                    last_err = e
                    logger.warning(f"vision 第 {attempt+1} 次失败 [{prov}]: {e}")
                    if attempt < retries:
                        time.sleep(2)
            raise RuntimeError(
                f"vision 调用失败（{prov}，已重试 {retries} 次）: {last_err}"
            )

        # 显式指定提供商时，不启用回退
        if provider is not None:
            return _call_one(provider)

        # 构建视觉提供商回退链
        chain = self._get_fallback_chain(self._active_provider, requires_vision=True)
        if not chain:
            raise RuntimeError("没有支持视觉的可用提供商，请先配置 API key")

        last_err: Optional[Exception] = None
        for prov in chain:
            try:
                result = _call_one(prov)
                if prov != self._active_provider:
                    logger.info(f"已切换到备用提供商 [{prov}] 完成 vision 请求")
                return result
            except Exception as e:
                last_err = e
                logger.warning(f"提供商 [{prov}] vision 全部重试耗尽，尝试下一个备用提供商: {e}")

        raise RuntimeError(f"所有视觉提供商均调用失败: {last_err}")

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
        if not response.ok:
            try:
                err_body = response.json()
                err_msg = err_body.get("error", {}).get("message", "") or str(err_body)
            except Exception:
                err_msg = response.text[:500]
            raise RuntimeError(
                f"HTTP {response.status_code}: {err_msg}"
            )
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
        if not response.ok:
            try:
                err_body = response.json()
                err_msg = err_body.get("error", {}).get("message", "") or str(err_body)
            except Exception:
                err_msg = response.text[:500]
            raise RuntimeError(
                f"HTTP {response.status_code}: {err_msg}"
            )
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
