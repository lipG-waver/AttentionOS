"""
对话日志模块 — 将 Attention OS 对话记录导出为 Markdown 文件

每日自动生成一个 Markdown 文件，包含：
- 对话记录（用户消息、AI 回复、系统事件）
- 思维捕捉（专注模式下记录的想法）
- 分心介入记录
- 时间线标注

文件命名: chat_log_YYYY-MM-DD.md
"""
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Any

from attention.config import Config

logger = logging.getLogger(__name__)

CHAT_LOG_DIR = Config.DATA_DIR / "chat_logs"


def ensure_dir():
    CHAT_LOG_DIR.mkdir(parents=True, exist_ok=True)


def _msg_type_icon(msg_type: str) -> str:
    return {
        "chat": "💬",
        "thought_capture": "💭",
        "nudge": "🔔",
        "status": "📢",
        "action": "⚡",
    }.get(msg_type, "💬")


def _role_label(role: str) -> str:
    return {
        "user": "**你**",
        "assistant": "**Attention OS**",
        "system_event": "**系统**",
    }.get(role, role)


def export_chat_to_markdown(
    messages: List[Dict[str, Any]],
    date_str: str = "",
    focus_sessions: List[Dict] = None,
    goals: List[str] = None,
) -> str:
    """
    将对话消息列表导出为 Markdown 格式字符串。

    Args:
        messages: 消息列表（来自 DialogueAgent.get_history_for_export()）
        date_str: 日期字符串，默认今天
        focus_sessions: 可选的专注记录
        goals: 可选的今日目标

    Returns:
        Markdown 格式的字符串
    """
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    lines = []
    lines.append(f"# 📓 Attention OS 对话日志 — {date_str}\n")

    # 今日目标
    if goals:
        lines.append("## 🎯 今日目标\n")
        for g in goals:
            lines.append(f"- [ ] {g}")
        lines.append("")

    # 对话记录
    lines.append("## 💬 对话记录\n")

    # 按时间分组（按小时）
    current_hour = ""
    thoughts = []

    for msg in messages:
        ts = msg.get("timestamp", "")
        role = msg.get("role", "user")
        content = msg.get("content", "")
        msg_type = msg.get("msg_type", "chat")

        # 时间分隔
        if ts and len(ts) >= 13:
            hour = ts[11:13] + ":00"
            if hour != current_hour:
                current_hour = hour
                lines.append(f"\n### ⏰ {hour}\n")

        # 收集思维捕捉
        if msg_type == "thought_capture" and role == "user":
            thoughts.append({"time": ts[11:16] if len(ts) >= 16 else "", "text": content})

        # 渲染消息
        icon = _msg_type_icon(msg_type)
        label = _role_label(role)
        time_str = ts[11:16] if len(ts) >= 16 else ""

        if msg_type == "status":
            lines.append(f"> {icon} *{time_str}* — {content}\n")
        elif msg_type == "nudge":
            lines.append(f"> {icon} *{time_str}* {label}: {content}\n")
        else:
            lines.append(f"{icon} *{time_str}* {label}: {content}\n")

    # 思维捕捉汇总
    if thoughts:
        lines.append("\n## 💭 思维捕捉\n")
        lines.append("专注期间快速记录的想法：\n")
        for t in thoughts:
            lines.append(f"- **{t['time']}** — {t['text']}")
        lines.append("")

    # 专注记录
    if focus_sessions:
        lines.append("\n## 🍅 专注记录\n")
        lines.append("| 时间 | 任务 | 时长 |")
        lines.append("|------|------|------|")
        for s in focus_sessions:
            lines.append(
                f"| {s.get('completed_at', '')} "
                f"| {s.get('task', '自由专注')} "
                f"| {s.get('duration_minutes', 0)}min |"
            )
        lines.append("")

    # 页脚
    lines.append("---")
    lines.append(f"*由 Attention OS 自动生成 · {datetime.now().strftime('%Y-%m-%d %H:%M')}*")

    return "\n".join(lines)


def save_chat_log(
    messages: List[Dict[str, Any]],
    date_str: str = "",
    **kwargs,
) -> Path:
    """
    保存对话日志到 Markdown 文件。

    Returns:
        文件路径
    """
    ensure_dir()
    if not date_str:
        date_str = datetime.now().strftime("%Y-%m-%d")

    md_content = export_chat_to_markdown(messages, date_str, **kwargs)
    filepath = CHAT_LOG_DIR / f"chat_log_{date_str}.md"
    filepath.write_text(md_content, encoding="utf-8")
    logger.info(f"对话日志已保存: {filepath}")
    return filepath


def get_today_log_path() -> Path:
    """获取今日对话日志路径"""
    ensure_dir()
    date_str = datetime.now().strftime("%Y-%m-%d")
    return CHAT_LOG_DIR / f"chat_log_{date_str}.md"
