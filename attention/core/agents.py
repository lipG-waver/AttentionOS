"""
轻量级 Multi-Agent 角色定义

本项目采用 Multi-Agent 架构，包含以下 Agent 角色：
- analyzer  : 屏幕内容分析 Agent（视觉模型）
- coach     : 注意力教练 Agent（生成提醒、鼓励）
- parser    : 任务解析 Agent（自然语言 → 结构化任务）

每个 Agent 拥有独立的 system prompt，通过统一的 LLMClient 调用模型。
"""
import json
import logging
from typing import Optional, Dict, Any

from attention.core.llm_client import get_llm_client

logger = logging.getLogger(__name__)


# ================================================================== #
#  Agent System Prompts
# ================================================================== #

AGENT_PROMPTS: Dict[str, str] = {
    "analyzer": (
        "你是屏幕内容分析专家。只分析截图中可见的内容，不要编造或猜测不存在的应用程序。"
        "请按指定 JSON 格式输出分析结果。"
    ),
    "coach": (
        "你是一个温和的注意力教练。你的职责是在用户偏离目标时，"
        "用共情和鼓励帮助用户回到正轨。你说话简短、友好、像朋友一样，"
        "不说教、不啰嗦。"
    ),
    "dialogue": (
        "你是 Attention OS 的内置对话助手，一个温暖、简洁、像朋友一样的注意力教练。"
        "说话简短有力，每条回复不超过 2-3 句话。用 emoji 增加亲和力但不过度。"
        "专注模式下极度简洁，分心提醒时先共情再轻推，不说教。"
    ),
    "parser": (
        "你是一个精确的任务解析助手。将用户的自然语言输入解析为结构化任务信息。"
        "只输出 JSON，不输出任何其他文字。"
    ),
}


# ================================================================== #
#  Agent 调用函数
# ================================================================== #

def call_agent(
    role: str,
    user_message: str,
    *,
    max_tokens: int = 500,
    temperature: float = 0.7,
    timeout: int = 15,
) -> str:
    """
    调用指定角色的 Agent。

    Args:
        role:         Agent 角色名（analyzer / coach / dialogue / parser）
        user_message: 用户消息 / 上下文
        max_tokens:   最大生成 token 数
        temperature:  采样温度
        timeout:      请求超时秒数

    Returns:
        Agent 的文本响应
    """
    system_prompt = AGENT_PROMPTS.get(role)
    if not system_prompt:
        raise ValueError(f"未知 Agent 角色: {role}（可用: {list(AGENT_PROMPTS.keys())}）")

    client = get_llm_client()
    return client.chat(
        prompt=user_message,
        system=system_prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )


def call_agent_json(
    role: str,
    user_message: str,
    **kwargs,
) -> dict:
    """
    调用指定角色的 Agent，返回 JSON 对象。
    """
    text = call_agent(role, user_message, **kwargs)
    text = text.strip()
    if text.startswith("```json"):
        text = text[7:]
    if text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return json.loads(text.strip())
