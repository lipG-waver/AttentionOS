"""
统一的 LLM 调用客户端（兼容层）

此模块保留原有的 get_llm_client() 接口，底层委托给
attention.core.llm_provider.MultiLLMClient 实现多提供商调用。

各业务模块无需修改即可透明切换 AI 提供商。
"""
import logging
from typing import Optional

from attention.core.llm_provider import get_llm_provider, MultiLLMClient

logger = logging.getLogger(__name__)


class LLMClient:
    """兼容层：接口不变，底层使用 MultiLLMClient"""

    def __init__(self):
        self._provider: MultiLLMClient = get_llm_provider()

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
    ) -> str:
        return self._provider.chat(
            prompt,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            retries=retries,
        )

    def chat_json(self, prompt: str, **kwargs) -> dict:
        return self._provider.chat_json(prompt, **kwargs)

    def vision(
        self,
        prompt: str,
        image_base64: str,
        image_type: str = "image/jpeg",
        max_tokens: int = 800,
        timeout: int = 30,
    ) -> str:
        return self._provider.vision(
            prompt,
            image_base64,
            image_type=image_type,
            max_tokens=max_tokens,
            timeout=timeout,
        )


# ================================================================== #
#  单例
# ================================================================== #

_client: Optional[LLMClient] = None


def get_llm_client() -> LLMClient:
    """获取 LLMClient 单例"""
    global _client
    if _client is None:
        _client = LLMClient()
    return _client
