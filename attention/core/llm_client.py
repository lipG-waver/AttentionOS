"""
统一的 ModelScope LLM 调用客户端

整合文本模型（Qwen2.5-72B-Instruct）和视觉模型（Qwen2.5-VL-72B-Instruct）
的调用逻辑，提供统一的错误处理、重试、超时和 JSON 解析能力。

各业务模块通过 get_llm_client() 获取单例即可调用，无需重复实现 API 交互。
"""
import json
import logging
import time
from typing import Optional, Dict, Any

import requests

from attention.config import Config

logger = logging.getLogger(__name__)


class LLMClient:
    """支持文本和视觉模型的统一客户端"""

    def __init__(self):
        self.api_base = Config.QWEN_API_BASE
        self.api_key = Config.QWEN_API_KEY
        self.text_model = Config.TEXT_MODEL_NAME
        self.vision_model = Config.MODEL_NAME

    # ------------------------------------------------------------------ #
    #  文本对话
    # ------------------------------------------------------------------ #

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

        Returns:
            模型生成的文本内容
        """
        model = model or self.text_model
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        last_err = None
        for attempt in range(1 + retries):
            try:
                resp = self._post(
                    model=model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    timeout=timeout,
                )
                return resp
            except Exception as e:
                last_err = e
                logger.warning(f"LLM chat 第 {attempt+1} 次失败: {e}")
                if attempt < retries:
                    time.sleep(2)
        raise RuntimeError(f"LLM chat 调用失败（已重试 {retries} 次）: {last_err}")

    def chat_json(self, prompt: str, **kwargs) -> dict:
        """
        文本对话，自动解析返回的 JSON。

        若模型返回 markdown code block 包裹的 JSON，也能正确解析。
        """
        text = self.chat(prompt, **kwargs)
        text = text.strip()
        # 清理 markdown code block
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()
        return json.loads(text)

    # ------------------------------------------------------------------ #
    #  视觉分析
    # ------------------------------------------------------------------ #

    def vision(
        self,
        prompt: str,
        image_base64: str,
        image_type: str = "image/jpeg",
        max_tokens: int = 800,
        timeout: int = 30,
    ) -> str:
        """
        视觉分析：接收一张图片 + 文本提示，返回模型输出。

        Args:
            prompt:       文本提示
            image_base64: 图像的 base64 编码字符串
            image_type:   MIME 类型
            max_tokens:   最大生成 token 数
            timeout:      请求超时秒数

        Returns:
            模型生成的文本内容
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
        return self._post(
            model=self.vision_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
            timeout=timeout,
        )

    # ------------------------------------------------------------------ #
    #  内部
    # ------------------------------------------------------------------ #

    def _post(
        self,
        model: str,
        messages: list,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        """统一的 HTTP POST 调用"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

        with requests.Session() as session:
            session.trust_env = False  # 忽略系统代理
            response = session.post(
                f"{self.api_base}/chat/completions",
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
