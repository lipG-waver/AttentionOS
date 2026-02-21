"""
对话式 Agent — Attention OS 的统一对话引擎

核心理念：
  所有用户交互通过对话完成。本模块维护对话上下文（session memory），
  能根据用户当前工作状态生成回复，并在检测到分心时主动发起对话。

职责：
  1. 维护多轮对话上下文（最近 N 条消息）
  2. 根据状态上下文（专注/分心/休息）调整对话风格
  3. 主动发起对话（分心提醒、休息建议、恢复鼓励）
  4. 快速思维捕捉（专注模式下秒回确认，异步整理）
  5. 将对话路由到子 Agent（parser、reviewer 等）
"""
import json
import logging
import time
import threading
from datetime import datetime
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, field, asdict

from attention.core.llm_client import get_llm_client
from attention.core.agents import AGENT_PROMPTS

logger = logging.getLogger(__name__)


# ================================================================== #
#  数据结构
# ================================================================== #

@dataclass
class ChatMessage:
    """单条对话消息"""
    role: str           # "user" | "assistant" | "system_event"
    content: str        # 消息内容
    timestamp: str = "" # ISO 格式时间戳
    msg_type: str = "chat"  # chat | thought_capture | nudge | status | action
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class SessionContext:
    """会话上下文 — 当前用户状态"""
    is_focus_mode: bool = False
    focus_task: str = ""
    focus_remaining_seconds: int = 0
    today_goals: List[str] = field(default_factory=list)
    current_app: str = ""
    is_productive: bool = False
    is_distracted: bool = False
    distraction_duration_seconds: int = 0
    attention_level: str = "medium"
    productivity_ratio: float = 0.0


# ================================================================== #
#  对话 Agent
# ================================================================== #

DIALOGUE_SYSTEM_PROMPT = """你是 Attention OS 的内置对话助手，一个温暖、简洁、像朋友一样的注意力教练。

你的核心原则：
1. 说话简短有力，每条回复不超过 2-3 句话
2. 用 emoji 增加亲和力，但不要过度
3. 专注模式下：极度简洁，优先确认"已记录"，不要展开话题
4. 分心提醒时：共情 → 好奇原因 → 轻推回归，不说教
5. 用户分享想法时：肯定 → 记录 → 引导回到任务

你能看到用户当前的工作状态上下文。根据不同场景调整风格：
- 🎯 专注中：惜字如金，像安静的助手
- ⚠️ 分心时：像关心你的朋友，问"怎么了"
- ☕ 休息中：轻松聊天，鼓励真正放松
- 📋 规划时：帮忙梳理思路，有条理

重要：永远不要长篇大论。你是桌面小球里弹出的对话框，空间有限。"""


class DialogueAgent:
    """
    对话式 Agent — 维护多轮上下文，支持主动对话和思维捕捉。
    """

    def __init__(self, max_history: int = 20):
        self._history: List[ChatMessage] = []
        self._max_history = max_history
        self._context = SessionContext()
        self._lock = threading.Lock()
        self._pending_thoughts: List[str] = []  # 待整理的快速想法

    # ---- 上下文管理 ----

    def update_context(self, **kwargs):
        """更新当前工作状态上下文"""
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self._context, k):
                    setattr(self._context, k, v)

    def get_context(self) -> SessionContext:
        with self._lock:
            return SessionContext(**asdict(self._context))

    # ---- 对话接口 ----

    def user_message(self, text: str) -> str:
        """
        处理用户消息，返回 AI 回复。

        在专注模式下，短消息被视为"思维捕捉"，秒回确认。
        """
        text = text.strip()
        if not text:
            return ""

        ctx = self.get_context()

        # 专注模式下的思维捕捉
        if ctx.is_focus_mode and len(text) < 100 and not text.startswith("/"):
            return self._handle_thought_capture(text, ctx)

        # 命令处理
        if text.startswith("/"):
            return self._handle_command(text, ctx)

        # v5.2: 检测自然语言中的休息/计划变更意图
        rest_response = self._detect_rest_intent(text)
        if rest_response:
            return rest_response

        # 正常对话 → 调用 LLM
        return self._chat_with_llm(text, ctx)

    def proactive_nudge(self, reason: str, fused_state: Optional[dict] = None) -> str:
        """
        系统主动发起的分心提醒对话。
        返回 AI 生成的开场白。
        """
        ctx = self.get_context()

        # 构建提示
        prompt = self._build_nudge_prompt(reason, ctx, fused_state)

        try:
            client = get_llm_client()
            response = client.chat(
                prompt=prompt,
                system=DIALOGUE_SYSTEM_PROMPT,
                max_tokens=150,
                temperature=0.8,
                timeout=10,
            )
            response = response.strip()
        except Exception as e:
            logger.warning(f"LLM 提醒生成失败: {e}")
            response = self._fallback_nudge(reason)

        # 记录到历史
        self._add_message("assistant", response, msg_type="nudge",
                         metadata={"reason": reason})
        return response

    def proactive_break_chat(self) -> str:
        """休息时间的主动对话开场"""
        ctx = self.get_context()
        prompts = [
            "休息时间到了 ☕ 站起来走动走动？",
            "该休息了！你已经专注了很长时间，眼睛也需要放松一下 🌿",
            "辛苦了！休息几分钟，回来效率更高 💪",
        ]
        import random
        msg = random.choice(prompts)
        self._add_message("assistant", msg, msg_type="status")
        return msg

    def proactive_plan_check(self, plan_context: Dict[str, Any]) -> str:
        """
        系统主动发起的计划确认对话。(v5.2)
        
        根据 ActivePlanner 检测到的不匹配情况，生成引导性对话。
        """
        action = plan_context.get("action", "")
        msg_ctx = plan_context.get("message_context", {})

        try:
            from attention.features.active_planner import get_active_planner
            planner = get_active_planner()

            if action == "plan_check":
                msg = planner.generate_plan_check_message(msg_ctx)
            elif action == "rest_ending":
                msg = planner.generate_rest_ending_message(msg_ctx)
            elif action == "rest_over":
                msg = planner.generate_rest_over_message(msg_ctx)
            elif action == "plan_suggestion":
                msg = planner.generate_plan_suggestion_message()
            else:
                msg = "📋 有个计划相关的提醒~"
        except Exception as e:
            logger.warning(f"计划对话生成失败: {e}")
            msg = "📋 注意到你的活动和计划有些不同，要调整一下吗？"

        self._add_message("assistant", msg, msg_type="nudge",
                         metadata={"action": action})
        return msg

    def focus_start_message(self, task: str, duration_min: int) -> str:
        """专注开始时的对话消息"""
        msg = f"🎯 专注模式已开启 — {task}（{duration_min}分钟）\n有什么想法随时告诉我，我帮你记着。"
        self._add_message("assistant", msg, msg_type="status",
                         metadata={"task": task, "duration": duration_min})
        return msg

    def focus_end_message(self, task: str, duration_min: int, completed: bool) -> str:
        """专注结束时的对话消息"""
        if completed:
            msg = f"🎉 太棒了！{duration_min}分钟专注完成！"
            if self._pending_thoughts:
                msg += f"\n📝 专注期间你记录了 {len(self._pending_thoughts)} 条想法，已保存到日志。"
                self._pending_thoughts.clear()
        else:
            msg = f"⏹ 专注已停止（{duration_min}分钟）"
        self._add_message("assistant", msg, msg_type="status")
        return msg

    def capture_thought(self, text: str) -> str:
        """
        专注模式思维捕捉（公开接口）— 不调用 LLM，立即返回确认。
        供外部在用户选择"专注"模式标签时直接调用。
        """
        text = text.strip()
        if not text:
            return ""
        ctx = self.get_context()

        self._add_message("user", text, msg_type="thought_capture")
        with self._lock:
            self._pending_thoughts.append(text)

        remaining = ctx.focus_remaining_seconds
        if remaining > 0:
            mins = remaining // 60
            confirm = f"📌 已记录！继续专注，还剩 {mins} 分钟 💪"
        else:
            confirm = "📌 已记录！"

        self._add_message("assistant", confirm, msg_type="thought_capture")
        return confirm

    # ---- 历史管理 ----

    def get_history(self) -> List[Dict]:
        """获取对话历史"""
        with self._lock:
            return [m.to_dict() for m in self._history]

    def get_history_for_export(self) -> List[Dict]:
        """获取导出用的完整历史"""
        with self._lock:
            return [m.to_dict() for m in self._history]

    def clear_history(self):
        """清空对话历史"""
        with self._lock:
            self._history.clear()
            self._pending_thoughts.clear()

    # ---- 内部方法 ----

    def _handle_thought_capture(self, text: str, ctx: SessionContext) -> str:
        """
        专注模式下的思维捕捉 — 不调用 LLM，秒回确认。
        """
        self._add_message("user", text, msg_type="thought_capture")

        with self._lock:
            self._pending_thoughts.append(text)
            count = len(self._pending_thoughts)

        remaining = ctx.focus_remaining_seconds
        if remaining > 0:
            mins = remaining // 60
            confirm = f"📌 已记录！继续专注，还剩 {mins} 分钟 💪"
        else:
            confirm = "📌 已记录！"

        self._add_message("assistant", confirm, msg_type="thought_capture")
        return confirm

    def _detect_rest_intent(self, text: str) -> Optional[str]:
        """
        检测自然语言中的休息意图。(v5.2)
        
        识别类似："我想摆烂"、"休息一下"、"刷会儿手机"、"我想歇会儿" 等表达。
        """
        import re
        text_lower = text.lower()

        rest_patterns = [
            r"摆烂", r"休息", r"歇[会一]", r"刷[会一]", r"放松",
            r"不想[干做工]", r"偷[会个]懒", r"玩[会一]",
            r"看[会一][儿]?视频", r"看[会一][儿]?手机",
            r"take a break", r"chill", r"relax",
        ]

        matched = False
        for pat in rest_patterns:
            if re.search(pat, text_lower):
                matched = True
                break

        if not matched:
            return None

        # 尝试提取时长
        minutes = 15  # 默认
        m = re.search(r"(\d+)\s*分钟", text)
        if m:
            minutes = min(int(m.group(1)), 30)
        elif "半小时" in text or "半个小时" in text:
            minutes = 30
        elif "一小时" in text or "一个小时" in text:
            minutes = 30  # cap at 30

        try:
            from attention.features.active_planner import get_active_planner
            planner = get_active_planner()
            planner.declare_rest(minutes, reason=text)
            msg = f"☕ 收到，休息 {minutes} 分钟！到时间我叫你~ ⏰"
            self._add_message("user", text)
            self._add_message("assistant", msg, msg_type="status")
            return msg
        except Exception as e:
            logger.debug(f"自动休息声明失败: {e}")
            return None

    def _handle_command(self, text: str, ctx: SessionContext) -> str:
        """处理斜杠命令"""
        cmd = text.lower().strip()
        if cmd in ("/help", "/帮助"):
            return ("💡 可用命令：\n"
                    "• 直接输入想法 → 快速记录\n"
                    "• /plan → 查看当前计划与推荐任务\n"
                    "• /goals → 查看今日目标\n"
                    "• /rest [分钟] → 声明合法休息（默认15分钟）\n"
                    "• /back → 结束休息，回到工作\n"
                    "• /switch [任务] → 临时切换到其他任务\n"
                    "• /status → 当前状态\n"
                    "• /deadlines → 查看即将到期的deadline\n"
                    "• /thoughts → 查看已记录的想法\n"
                    "• /export → 导出今日对话")
        elif cmd in ("/goals", "/目标"):
            if ctx.today_goals:
                goals_text = "\n".join(f"  {'✅' if i < 0 else '🔲'} {g}"
                                       for i, g in enumerate(ctx.today_goals))
                return f"📋 今日目标：\n{goals_text}"
            return "📋 还没有设定今日目标。"
        elif cmd.startswith("/plan") or cmd.startswith("/计划"):
            return self._handle_plan_command()
        elif cmd.startswith("/rest") or cmd.startswith("/休息") or cmd.startswith("/摆烂"):
            return self._handle_rest_command(text)
        elif cmd in ("/back", "/回来", "/结束休息"):
            return self._handle_end_rest()
        elif cmd.startswith("/switch") or cmd.startswith("/切换"):
            return self._handle_switch_command(text)
        elif cmd.startswith("/deadlines") or cmd.startswith("/deadline") or cmd.startswith("/截止"):
            return self._handle_deadlines_command()
        elif cmd in ("/status", "/状态"):
            if ctx.is_focus_mode:
                mins = ctx.focus_remaining_seconds // 60
                return f"🎯 专注中 — {ctx.focus_task}（剩余 {mins} 分钟）"
            # 增加计划和休息状态
            parts = [f"📊 当前状态：注意力 {ctx.attention_level} | "
                     f"生产率 {ctx.productivity_ratio:.0%}"]
            try:
                from attention.features.active_planner import get_active_planner
                planner = get_active_planner()
                if planner.is_resting():
                    rest = planner.get_rest_status()
                    parts.append(f"\n☕ 休息中（还剩 {rest['remaining_minutes']} 分钟）")
                else:
                    plan = planner.get_active_plan()
                    if plan.get("task_title"):
                        parts.append(f"\n📋 当前计划：{plan['task_title']}")
            except Exception:
                pass
            return "".join(parts)
        elif cmd in ("/thoughts", "/想法"):
            if self._pending_thoughts:
                items = "\n".join(f"  💭 {t}" for t in self._pending_thoughts)
                return f"📝 本次专注记录的想法：\n{items}"
            return "📝 暂时没有记录的想法。"
        else:
            return f"❓ 未知命令: {text}。输入 /help 查看可用命令。"

    def _handle_plan_command(self) -> str:
        """查看当前计划"""
        try:
            from attention.features.active_planner import get_active_planner
            planner = get_active_planner()
            return planner.generate_plan_suggestion_message()
        except Exception as e:
            logger.debug(f"获取计划失败: {e}")
            return "📋 暂时无法获取计划信息。"

    def _handle_rest_command(self, text: str) -> str:
        """处理休息声明"""
        import re
        # 解析分钟数
        minutes = 15  # 默认
        m = re.search(r"(\d+)", text)
        if m:
            minutes = min(int(m.group(1)), 30)

        reason = ""
        # 尝试提取原因（在数字之后的文本）
        parts = text.split(maxsplit=2)
        if len(parts) > 2:
            reason = parts[2] if not parts[2].isdigit() else ""
        elif len(parts) > 1 and not parts[1].isdigit():
            reason = parts[1]

        try:
            from attention.features.active_planner import get_active_planner
            planner = get_active_planner()
            result = planner.declare_rest(minutes, reason)
            msg = f"☕ 好的，休息 {minutes} 分钟！"
            if reason:
                msg += f"（{reason}）"
            msg += f"\n到时间我会提醒你 ⏰"
            self._add_message("assistant", msg, msg_type="status")
            return msg
        except Exception as e:
            logger.debug(f"声明休息失败: {e}")
            return "暂时无法设置休息，稍后再试。"

    def _handle_end_rest(self) -> str:
        """结束休息"""
        try:
            from attention.features.active_planner import get_active_planner
            planner = get_active_planner()
            if not planner.is_resting():
                return "你现在不在休息状态哦~"
            planner.end_rest()
            plan = planner.get_active_plan()
            task = plan.get("task_title", "")
            if task:
                return f"💪 休息结束！推荐接下来做「{task}」，冲！"
            return "💪 休息结束！准备好继续了吗？"
        except Exception as e:
            logger.debug(f"结束休息失败: {e}")
            return "好的，继续工作！"

    def _handle_switch_command(self, text: str) -> str:
        """临时切换到其他任务"""
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            return "❓ 用法：/switch 任务描述\n例如：/switch 回复邮件"
        task = parts[1].strip()
        try:
            from attention.features.active_planner import get_active_planner
            planner = get_active_planner()
            planner.override_plan(task, duration_minutes=60)
            return f"🔄 好的，当前计划切换为「{task}」（60分钟）。\n完成后输入 /plan 查看下一步。"
        except Exception as e:
            logger.debug(f"切换计划失败: {e}")
            return f"📝 已记录：{task}"

    def _handle_deadlines_command(self) -> str:
        """查看即将到期的deadline"""
        try:
            from attention.features.goal_manager import get_goal_manager
            deadlines = get_goal_manager().get_upcoming_deadlines(hours=72)
            if not deadlines:
                return "📅 接下来 3 天内没有 deadline。"
            lines = ["📅 即将到期的 Deadline："]
            for dl in deadlines[:5]:
                hours = dl["hours_left"]
                if hours <= 2:
                    urgency = "🔴"
                elif hours <= 24:
                    urgency = "🟡"
                else:
                    urgency = "🟢"
                lines.append(f"  {urgency} {dl['task_title']} — {dl['deadline']}（还剩 {hours:.0f}h）")
            return "\n".join(lines)
        except Exception as e:
            logger.debug(f"获取deadline失败: {e}")
            return "📅 暂时无法获取 deadline 信息。"

    def _chat_with_llm(self, text: str, ctx: SessionContext) -> str:
        """调用 LLM 生成多轮对话回复"""
        self._add_message("user", text)

        # 构建上下文
        context_info = self._build_context_string(ctx)
        messages_for_llm = self._build_llm_messages(context_info)

        try:
            client = get_llm_client()
            # 手动构建 messages 列表，支持多轮
            response = client.chat(
                prompt=self._format_messages_as_prompt(messages_for_llm),
                system=DIALOGUE_SYSTEM_PROMPT,
                max_tokens=200,
                temperature=0.7,
                timeout=12,
            )
            response = response.strip()
        except Exception as e:
            logger.warning(f"LLM 对话失败: {e}")
            response = "抱歉，我暂时无法回复。不过你的消息已记录 📝"

        self._add_message("assistant", response)
        return response

    def _build_context_string(self, ctx: SessionContext) -> str:
        """构建给 LLM 的状态上下文"""
        parts = []
        now = datetime.now().strftime("%H:%M")
        parts.append(f"当前时间：{now}")

        if ctx.is_focus_mode:
            mins = ctx.focus_remaining_seconds // 60
            parts.append(f"状态：专注模式（任务：{ctx.focus_task}，剩余{mins}分钟）")
        elif ctx.is_distracted:
            parts.append(f"状态：注意力分散（已持续{ctx.distraction_duration_seconds // 60}分钟）")
            if ctx.current_app:
                parts.append(f"当前应用：{ctx.current_app}")
        else:
            parts.append(f"状态：{ctx.attention_level} 注意力")

        if ctx.today_goals:
            parts.append(f"今日目标：{', '.join(ctx.today_goals[:3])}")

        return "\n".join(parts)

    def _build_llm_messages(self, context_info: str) -> List[Dict]:
        """构建发送给 LLM 的消息列表（含上下文和历史）"""
        messages = []

        # 加入最近的对话历史（最多 6 条）
        with self._lock:
            recent = [m for m in self._history
                      if m.role in ("user", "assistant") and m.msg_type == "chat"]
            recent = recent[-6:]

        for m in recent:
            messages.append({"role": m.role, "content": m.content})

        # 在最后一条用户消息前注入上下文
        if messages:
            last_user = messages[-1]
            last_user["content"] = f"[用户状态] {context_info}\n\n[用户说] {last_user['content']}"

        return messages

    def _format_messages_as_prompt(self, messages: List[Dict]) -> str:
        """将多轮消息格式化为单轮 prompt（兼容当前 LLM Client 接口）"""
        parts = []
        for m in messages[:-1]:  # 排除最后一条（因为 chat() 会自己加 user message）
            if m["role"] == "user":
                parts.append(f"用户: {m['content']}")
            elif m["role"] == "assistant":
                parts.append(f"助手: {m['content']}")

        # 最后一条是用户消息
        if messages:
            last = messages[-1]
            if parts:
                parts.append(f"\n用户: {last['content']}")
                parts.append("\n请作为助手回复：")
                return "\n".join(parts)
            return last["content"]

        return ""

    def _build_nudge_prompt(self, reason: str, ctx: SessionContext,
                           fused_state: Optional[dict] = None) -> str:
        """构建分心提醒的 prompt"""
        parts = [f"[系统事件] 检测到用户注意力分散。"]
        parts.append(f"原因：{reason}")

        if ctx.current_app:
            parts.append(f"当前应用：{ctx.current_app}")
        if ctx.distraction_duration_seconds > 0:
            parts.append(f"已偏离 {ctx.distraction_duration_seconds // 60} 分钟")
        if ctx.today_goals:
            parts.append(f"今日目标：{', '.join(ctx.today_goals[:3])}")
        if ctx.is_focus_mode:
            parts.append(f"正在专注任务：{ctx.focus_task}")

        parts.append("\n请用 1-2 句话温和地提醒用户。先共情，再轻推。不要说教。")
        return "\n".join(parts)

    def _fallback_nudge(self, reason: str) -> str:
        """LLM 不可用时的回退提醒"""
        import random
        templates = [
            "👀 嘿，好像跑偏了哦~ 要不要回来继续？",
            "💡 注意到你在休息，差不多了的话可以继续啦~",
            "🎯 你的目标还在等你呢，回来吧！",
            "⏰ 已经偏离一会儿了，准备好的话随时继续 💪",
        ]
        return random.choice(templates)

    def _add_message(self, role: str, content: str, msg_type: str = "chat",
                    metadata: Optional[Dict] = None):
        """添加消息到历史"""
        msg = ChatMessage(
            role=role, content=content, msg_type=msg_type,
            metadata=metadata or {}
        )
        with self._lock:
            self._history.append(msg)
            if len(self._history) > self._max_history:
                self._history = self._history[-self._max_history:]


# ================================================================== #
#  单例
# ================================================================== #

_dialogue_agent: Optional[DialogueAgent] = None


def get_dialogue_agent() -> DialogueAgent:
    global _dialogue_agent
    if _dialogue_agent is None:
        _dialogue_agent = DialogueAgent()
    return _dialogue_agent
