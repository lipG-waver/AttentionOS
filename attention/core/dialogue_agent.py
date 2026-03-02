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

from openai import OpenAI
from attention.core.llm_client import get_llm_client
from attention.core.llm_provider import get_llm_provider
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

DIALOGUE_SYSTEM_PROMPT = """你是 Attention OS 的内置对话助手——一个温暖、简洁、像朋友一样的注意力教练。

## 关于 Attention OS
Attention OS 是一款桌面端 AI 注意力管理助手，通过持续截图与视觉 AI 分析用户屏幕，实时追踪工作状态。

核心功能：
1. 【屏幕分析】每60秒截图一次，用视觉模型分析当前应用/任务/分心状态
2. 【番茄钟 Pomodoro】支持25/45/90分钟工作+休息循环，可在聊天或Web界面启动/停止
3. 【待办事项 Todo】自然语言添加Todo（自动解析优先级、标签、截止时间）
4. 【每小时签到 Hourly Check-in】每小时询问用户在做什么，记录工作日志
5. 【休息管理 Break Reminder】定时提醒起身休息

Web界面（http://localhost:5000）功能：
- 仪表盘：实时状态、活动率、今日统计
- 待办事项：添加/完成 Todo
- 番茄钟：启动/停止，自定义时长
- 设置：AI模型配置（API Key/模型选择）

对话命令：
- /help → 帮助列表
- /status → 当前注意力/专注状态
- /thoughts → 查看本次专注记录的想法
- /export → 导出今日对话

## 你的回复原则
1. 说话简短有力，每条回复不超过 2-3 句话
2. 用 emoji 增加亲和力，但不要过度
3. 专注模式下：极度简洁，优先确认"已记录"，不要展开话题
4. 分心提醒时：共情 → 好奇原因 → 轻推回归，不说教
5. 用户询问功能用法时：简明告知，推荐具体的命令或Web界面路径
6. 根据系统注入的当前工作状态上下文按场景调整风格：
   - 🎯 专注中：惜字如金，像安静的助手
   - ⚠️ 分心时：像关心你的朋友，问"怎么了"
   - ☕ 休息中：轻松聊天，鼓励真正放松

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
        self._pending_bulk_import: Optional[Dict[str, Any]] = None  # 等待结束日期确认的批量导入

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

        # 优先处理待确认的批量导入（等待用户提供结束日期）
        if self._pending_bulk_import is not None:
            bulk_response = self._handle_pending_bulk_import(text)
            if bulk_response is not None:
                return bulk_response

        # 专注模式下的思维捕捉
        if ctx.is_focus_mode and len(text) < 100 and not text.startswith("/"):
            return self._handle_thought_capture(text, ctx)

        # 命令处理
        if text.startswith("/"):
            return self._handle_command(text, ctx)

        # 检测批量/重复任务导入意图
        bulk_response = self._detect_bulk_import_intent(text)
        if bulk_response is not None:
            return bulk_response

        # 检测待办查询/管理意图（查看、搜索、清空）
        query_response = self._detect_todo_query_intent(text)
        if query_response is not None:
            return query_response

        # 检测待办创建意图
        todo_response = self._detect_todo_intent(text)
        if todo_response:
            return todo_response

        # 检测模型/提供商切换意图
        model_response = self._detect_model_switch_intent(text)
        if model_response is not None:
            return model_response

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

    def proactive_break_chat(self, continuous_minutes: int = 0) -> str:
        """休息时间的主动对话开场，continuous_minutes 为实际连续工作分钟数"""
        duration_str = f"{continuous_minutes} 分钟" if continuous_minutes > 0 else "一段时间"
        prompts = [
            f"你已经连续工作了 {duration_str} 了，站起来走走？☕",
            f"连续工作 {duration_str}，眼睛和脑袋都需要喘口气 🌿",
            f"已经 {duration_str} 没休息了，起来动一动，回来效率更高 💪",
        ]
        import random
        msg = random.choice(prompts)
        self._add_message("assistant", msg, msg_type="status")
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

    def _detect_todo_query_intent(self, text: str) -> Optional[str]:
        """
        检测待办的查询/管理意图：
          - 查看今日待办 / 逾期任务 / 即将到期 / 所有任务
          - 搜索关键词
          - 清空已完成
        返回格式化后的消息，或 None（未命中）。
        """
        import re

        t = text.strip()

        # ---- 清空已完成 ----
        if re.search(r"清[空除掉]?(?:所有)?已完成|清[空除]已完成|删除已完成|清掉已完成|清完成", t):
            try:
                from attention.features.todo_manager import get_todo_manager
                n = get_todo_manager().clear_completed()
                msg = f"🗑️ 已清空 {n} 条完成的待办 ✨" if n else "没有已完成的待办需要清空 👌"
                self._add_message("user", t)
                self._add_message("assistant", msg)
                return msg
            except Exception as e:
                logger.warning(f"清空已完成失败: {e}")
                return None

        # ---- 搜索关键词 ----
        m = re.search(r"(?:搜索|查找|找[一找]?找?|找下)(?:待办|任务)?[「\s:：]*([\w\u4e00-\u9fa5]+)", t)
        if m:
            keyword = m.group(1).strip()
            try:
                from attention.features.todo_manager import get_todo_manager
                results = get_todo_manager().search(keyword, include_completed=False)
                msg = self._format_todo_list(results, f"搜索「{keyword}」")
                self._add_message("user", t)
                self._add_message("assistant", msg)
                return msg
            except Exception as e:
                logger.warning(f"搜索待办失败: {e}")
                return None

        # ---- 查看今日待办 ----
        if re.search(r"今[天日].*?(?:待办|任务|要做|该做|安排)|(?:待办|任务).*?今[天日]|今[天日]有[什哪]", t):
            try:
                from attention.features.todo_manager import get_todo_manager
                results = get_todo_manager().get_due_today()
                msg = self._format_todo_list(results, "今日待办")
                self._add_message("user", t)
                self._add_message("assistant", msg)
                return msg
            except Exception as e:
                logger.warning(f"获取今日待办失败: {e}")
                return None

        # ---- 查看逾期任务 ----
        if re.search(r"逾期|过期|超期|过了.*?截止|没完成.*?(?:任务|待办)", t):
            try:
                from attention.features.todo_manager import get_todo_manager
                results = get_todo_manager().get_overdue()
                msg = self._format_todo_list(results, "逾期待办")
                self._add_message("user", t)
                self._add_message("assistant", msg)
                return msg
            except Exception as e:
                logger.warning(f"获取逾期待办失败: {e}")
                return None

        # ---- 查看即将到期（本周/未来7天）----
        if re.search(r"(?:本|这|即将|快要|最近).*?(?:到期|截止|待办|任务)|(?:待办|任务).*?(?:本|这)周|近期.*?(?:待办|任务)", t):
            try:
                from attention.features.todo_manager import get_todo_manager
                results = get_todo_manager().get_upcoming(days=7)
                msg = self._format_todo_list(results, "近7天待办")
                self._add_message("user", t)
                self._add_message("assistant", msg)
                return msg
            except Exception as e:
                logger.warning(f"获取近期待办失败: {e}")
                return None

        # ---- 查看所有待办 ----
        if re.search(r"(?:查看|看看|列出|显示|show).*?(?:所有|全部|全[部]?|所有的)?(?:待办|任务|todo)|(?:所有|全部).*?(?:待办|任务)", t):
            try:
                from attention.features.todo_manager import get_todo_manager
                mgr = get_todo_manager()
                results = mgr.get_all(include_completed=False)
                stats = mgr.get_stats()
                msg = self._format_todo_list(results, f"全部待办（{stats['pending']} 条未完成）")
                self._add_message("user", t)
                self._add_message("assistant", msg)
                return msg
            except Exception as e:
                logger.warning(f"获取所有待办失败: {e}")
                return None

        return None

    def _format_todo_list(self, todos: List[Dict], title: str) -> str:
        """将待办列表格式化为对话气泡友好的字符串"""
        if not todos:
            return f"📋 {title}：暂时没有任务 🎉"

        lines = [f"📋 {title}（{len(todos)} 条）："]
        priority_icons = {"urgent": "🔴", "high": "🟠", "normal": "🟡", "low": "🔵"}
        for t in todos[:10]:  # 最多显示10条
            icon = priority_icons.get(t.get("priority", "normal"), "🟡")
            title_text = t.get("title", "")
            deadline = t.get("deadline", "")
            dl_str = f" · {deadline}" if deadline else ""
            lines.append(f"  {icon} {title_text}{dl_str}")
        if len(todos) > 10:
            lines.append(f"  … 还有 {len(todos) - 10} 条，详情见 Web 界面")
        return "\n".join(lines)

    def _detect_todo_intent(self, text: str) -> Optional[str]:
        """
        检测自然语言中的待办创建意图，并实际调用 todo_manager 创建任务。
        识别类似："帮我添加一个待办"、"记录一个任务：xxx"、"创建任务 xxx" 等表达。
        """
        import re
        text_stripped = text.strip()

        # 意图触发词
        todo_triggers = [
            r"帮[我]?(?:添加|加|创建|记录|建)+(一个)?(?:待办|任务|todo|To-?Do)",
            r"(?:添加|加|创建|记录|新建)+(一个)?(?:待办|任务|todo|To-?Do)",
            r"(?:待办|任务|todo)[:：\s]",
            r"提醒[我]?[要]?",
        ]

        matched = any(re.search(p, text_stripped, re.IGNORECASE) for p in todo_triggers)
        if not matched:
            return None

        # 提取任务内容：去掉触发词部分，保留后面的描述
        task_text = re.sub(
            r"^(?:帮[我]?|请)?(?:添加|加|创建|记录|新建|提醒我要?)*(一个)?(?:待办|任务|todo|To-?Do)*[:：\s]*",
            "", text_stripped, flags=re.IGNORECASE
        ).strip()

        if not task_text:
            # 没有提取到任务内容，回退到 LLM
            return None

        try:
            from attention.features.todo_manager import get_todo_manager
            mgr = get_todo_manager()
            result = mgr.smart_add(task_text, use_llm=False)
            todo = result.get("todo", {})
            title = todo.get("title", task_text)
            priority = todo.get("priority", "normal")
            deadline = todo.get("deadline", "")

            pri_label = {"urgent": "🔴 紧急", "high": "🟠 重要", "low": "🔵 低优先"}.get(priority, "")
            dl_label = f"，截止 {deadline}" if deadline else ""
            pri_str = f"，{pri_label}" if pri_label else ""

            msg = f"✅ 已添加待办：「{title}」{pri_str}{dl_label}"
            self._add_message("user", text)
            self._add_message("assistant", msg, msg_type="chat")
            logger.info(f"待办已创建: {title}")
            return msg
        except Exception as e:
            logger.warning(f"待办创建失败: {e}")
            return None

    # ---- 批量/重复任务导入 ----

    _CHINESE_MONTH_MAP = {
        "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6,
        "七": 7, "八": 8, "九": 9, "十": 10, "十一": 11, "十二": 12,
    }
    _WEEKDAY_MAP = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    _WEEKDAY_NAMES = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    def _detect_bulk_import_intent(self, text: str) -> Optional[str]:
        """
        检测批量/重复任务导入意图，如"每个月26日去配药"。
        若检测到重复模式：
          - 若同时包含结束日期 → 直接批量创建并返回确认
          - 若缺少结束日期 → 询问结束时间，保存 _pending_bulk_import
        """
        import re

        monthly = re.search(r"每(?:个)?月(?:的?)?(\d{1,2})\s*[号日]", text)
        weekly = re.search(r"每(?:个)?周([一二三四五六日天])", text)

        if not monthly and not weekly:
            return None

        now = datetime.now()
        title = self._extract_bulk_title(text)
        priority = self._infer_priority(text)
        tags = self._infer_tags(text)
        end_date = self._parse_end_date(text, now)

        if monthly:
            day_of_month = int(monthly.group(1))
            if end_date:
                response = self._create_bulk_monthly(title, day_of_month, now, end_date, priority, tags)
                self._add_message("user", text)
                self._add_message("assistant", response)
                return response
            else:
                self._pending_bulk_import = {
                    "type": "monthly",
                    "day_of_month": day_of_month,
                    "title": title,
                    "priority": priority,
                    "tags": tags,
                }
                question = (f'好的！「{title}」每月{day_of_month}日，'
                            f'你想加到什么时候呢？（比如「到8月」、「到2026年底」、「接下来3个月」）')
                self._add_message("user", text)
                self._add_message("assistant", question)
                return question

        if weekly:
            day_of_week = self._WEEKDAY_MAP[weekly.group(1)]
            weekday_name = self._WEEKDAY_NAMES[day_of_week]
            if end_date:
                response = self._create_bulk_weekly(title, day_of_week, now, end_date, priority, tags)
                self._add_message("user", text)
                self._add_message("assistant", response)
                return response
            else:
                self._pending_bulk_import = {
                    "type": "weekly",
                    "day_of_week": day_of_week,
                    "title": title,
                    "priority": priority,
                    "tags": tags,
                }
                question = (f'好的！「{title}」每{weekday_name}，'
                            f'你想加到什么时候呢？（比如「到8月」、「到2026年底」、「接下来3个月」）')
                self._add_message("user", text)
                self._add_message("assistant", question)
                return question

        return None

    def _handle_pending_bulk_import(self, text: str) -> Optional[str]:
        """
        处理待确认批量导入的结束日期回复。
        Returns:
          - 确认/取消/重询消息（字符串）: 已处理，勿继续路由
          - None: 无法解析为结束日期，让后续逻辑正常处理
        """
        import re

        pending = self._pending_bulk_import
        if pending is None:
            return None

        # 取消词
        if re.search(r"算了|取消|不了|不用|不要|停止|放弃", text):
            self._pending_bulk_import = None
            msg = "好的，批量添加已取消 ✌️"
            self._add_message("user", text)
            self._add_message("assistant", msg)
            return msg

        now = datetime.now()
        end_date = self._parse_end_date(text, now)

        if end_date is None:
            # 若看起来是在尝试描述结束时间，但解析失败，提示重试
            if re.search(r"月|年|到|底|末|号|天|周|久", text):
                retry = '我没明白截止时间，可以说「到8月」或者「接下来3个月」？（输入「取消」可以放弃）'
                self._add_message("user", text)
                self._add_message("assistant", retry)
                return retry
            # 否则与批量导入无关，放回正常路由
            return None

        title = pending["title"]
        priority = pending.get("priority", "normal")
        tags = pending.get("tags", [])

        if pending["type"] == "monthly":
            response = self._create_bulk_monthly(title, pending["day_of_month"], now, end_date, priority, tags)
        elif pending["type"] == "weekly":
            response = self._create_bulk_weekly(title, pending["day_of_week"], now, end_date, priority, tags)
        else:
            response = "暂不支持该重复类型 🤔"

        self._pending_bulk_import = None
        self._add_message("user", text)
        self._add_message("assistant", response)
        return response

    def _parse_end_date(self, text: str, now: datetime) -> Optional[datetime]:
        """
        从文本中解析批量任务的结束日期。
        支持：到X月、到YYYY年X月、到年底、接下来N个月、明年X月等。
        """
        import re
        import calendar

        month_pat = r"(?:\d{1,2}|[一二三四五六七八九十]{1,3})"

        # "到YYYY年X月（底/末）"
        m = re.search(rf"到(\d{{4}})年({month_pat})月(?:底|末)?", text)
        if m:
            year, month = int(m.group(1)), self._to_month_int(m.group(2))
            if month:
                last = calendar.monthrange(year, month)[1]
                return datetime(year, month, last)

        # "到X月（底/末）"
        m = re.search(rf"到({month_pat})月(?:底|末)?", text)
        if m:
            month = self._to_month_int(m.group(1))
            if month:
                year = now.year
                if month < now.month or (month == now.month and now.day > 20):
                    year += 1
                last = calendar.monthrange(year, month)[1]
                return datetime(year, month, last)

        # "到年底" / "到年末"
        if re.search(r"到年底|到年末", text):
            return datetime(now.year, 12, 31)

        # "到明年X月"
        m = re.search(rf"到明年({month_pat})月(?:底|末)?", text)
        if m:
            month = self._to_month_int(m.group(1))
            if month:
                last = calendar.monthrange(now.year + 1, month)[1]
                return datetime(now.year + 1, month, last)

        # "接下来N个月" / "未来N个月"
        m = re.search(r"(?:接下来|未来)(\d+|[一二三四五六七八九十]+)(?:个)?月", text)
        if m:
            n = self._to_month_int(m.group(1))
            if n:
                end_m = now.month + n
                end_y = now.year
                while end_m > 12:
                    end_m -= 12
                    end_y += 1
                last = calendar.monthrange(end_y, end_m)[1]
                return datetime(end_y, end_m, last)

        # "接下来几个月" → 默认6个月
        if re.search(r"接下来几个月|未来几个月", text):
            end_m = now.month + 6
            end_y = now.year
            while end_m > 12:
                end_m -= 12
                end_y += 1
            last = calendar.monthrange(end_y, end_m)[1]
            return datetime(end_y, end_m, last)

        return None

    def _to_month_int(self, s: str) -> Optional[int]:
        """将中文或阿拉伯月份字符串转为整数"""
        try:
            n = int(s)
            if 1 <= n <= 12:
                return n
        except ValueError:
            pass
        return self._CHINESE_MONTH_MAP.get(s)

    def _extract_bulk_title(self, text: str) -> str:
        """从批量任务描述中提取任务标题（去掉重复频率、时间范围等修饰词）"""
        import re
        s = text
        s = re.sub(r"每(?:个)?月(?:的?)?(?:\d{1,2})\s*[号日]?", "", s)
        s = re.sub(r"每(?:个)?周[一二三四五六日天]?", "", s)
        s = re.sub(r"每天", "", s)
        s = re.sub(r"(?:接下来|未来)(?:的?)?(?:\d+|几|[一二三四五六七八九十]+)?(?:个)?月", "", s)
        s = re.sub(r"到(?:\d{4}年)?(?:年底|年末|[一二三四五六七八九十]+|\d{1,2})月?(?:底|末)?", "", s)
        s = re.sub(r"到明年\d{1,2}月", "", s)
        s = re.sub(r"都要?|需要|应该", "", s)
        s = re.sub(r"[，,。！？\s]+", " ", s).strip()
        return s if len(s) >= 2 else text.strip()

    def _infer_priority(self, text: str) -> str:
        from attention.features.todo_manager import _infer_priority_from_text
        return _infer_priority_from_text(text)

    def _infer_tags(self, text: str) -> List[str]:
        from attention.features.todo_manager import _infer_tags_from_text
        return _infer_tags_from_text(text)

    def _create_bulk_monthly(self, title: str, day_of_month: int,
                              start: datetime, end: datetime,
                              priority: str = "normal",
                              tags: Optional[List[str]] = None) -> str:
        """批量创建每月重复任务，返回确认消息"""
        from attention.features.todo_manager import get_todo_manager, generate_monthly_dates
        dates = generate_monthly_dates(day_of_month, start, end)
        if not dates:
            return f"在这个时间范围内找不到每月{day_of_month}日的有效日期，请确认一下 🤔"
        mgr = get_todo_manager()
        todos = mgr.bulk_add(title, dates, priority=priority, tags=tags or [])
        summary = self._format_date_summary(dates)
        return (f"✅ 已批量添加 {len(todos)} 条「{title}」待办\n"
                f"📅 {summary}，每月{day_of_month}日一次")

    def _create_bulk_weekly(self, title: str, day_of_week: int,
                             start: datetime, end: datetime,
                             priority: str = "normal",
                             tags: Optional[List[str]] = None) -> str:
        """批量创建每周重复任务，返回确认消息"""
        from attention.features.todo_manager import get_todo_manager, generate_weekly_dates
        dates = generate_weekly_dates(day_of_week, start, end)
        if not dates:
            return "在这个时间范围内找不到有效日期，请确认一下 🤔"
        mgr = get_todo_manager()
        todos = mgr.bulk_add(title, dates, priority=priority, tags=tags or [])
        weekday_name = self._WEEKDAY_NAMES[day_of_week]
        summary = self._format_date_summary(dates)
        return (f"✅ 已批量添加 {len(todos)} 条「{title}」待办\n"
                f"📅 {summary}，每{weekday_name}一次")

    def _format_date_summary(self, dates: List[str]) -> str:
        """将日期列表格式化为简洁的中文摘要"""
        if not dates:
            return ""

        def fmt(d: str) -> str:
            parts = d.split("-")
            return f"{int(parts[1])}月{int(parts[2])}日"

        if len(dates) <= 4:
            return "、".join(fmt(d) for d in dates)
        return f"{fmt(dates[0])} 至 {fmt(dates[-1])}（共{len(dates)}次）"

    def _handle_command(self, text: str, ctx: SessionContext) -> str:
        """处理斜杠命令"""
        cmd = text.lower().strip()
        if cmd in ("/help", "/帮助"):
            return ("💡 可用命令：\n"
                    "• 直接输入想法 → 快速记录（专注模式下）\n"
                    "• /status → 当前注意力/专注状态\n"
                    "• /thoughts → 查看已记录的想法\n"
                    "• /export → 导出今日对话")
        elif cmd in ("/status", "/状态"):
            if ctx.is_focus_mode:
                mins = ctx.focus_remaining_seconds // 60
                return f"🎯 专注中 — {ctx.focus_task}（剩余 {mins} 分钟）"
            return (f"📊 当前状态：注意力 {ctx.attention_level} | "
                    f"生产率 {ctx.productivity_ratio:.0%}")
        elif cmd in ("/thoughts", "/想法"):
            if self._pending_thoughts:
                items = "\n".join(f"  💭 {t}" for t in self._pending_thoughts)
                return f"📝 本次专注记录的想法：\n{items}"
            return "📝 暂时没有记录的想法。"
        else:
            return f"❓ 未知命令: {text}。输入 /help 查看可用命令。"

    def _detect_model_switch_intent(self, text: str) -> Optional[str]:
        """
        检测模型/提供商查询或切换意图。

        支持：
          - 查询当前用的模型/提供商
          - 切换到指定提供商（DeepSeek / 通义 / Claude / OpenAI / ModelScope）
          - 查看所有已配置的提供商

        返回处理完的回复字符串，或 None（未命中）。
        """
        import re
        t = text.strip()

        # ---- 查询当前模型 ----
        if re.search(
            r"(?:用的|用了|在用|当前|现在用|现在是|用什么|用哪个)[^？?]*?(?:模型|AI|提供商|provider)|"
            r"(?:模型|提供商|AI)[^？?]*?(?:是什么|是哪个|是哪家|怎么配|怎么选)|"
            r"(?:哪个|什么)模型|现在.*?模型",
            t,
        ):
            provider = get_llm_provider()
            enabled = provider.get_enabled_providers()
            if not enabled:
                msg = "⚠️ 还没有配置任何 API Key，可以在 Web 设置页面（http://localhost:5000）添加"
            else:
                active = provider.get_active_provider()
                active_cfg = provider.get_config(active)
                if len(enabled) == 1:
                    msg = f"🤖 当前使用：{active_cfg.display_name}（{active_cfg.text_model}）"
                else:
                    names = []
                    for p in enabled:
                        cfg = provider.get_config(p)
                        names.append(f"{cfg.display_name}（{cfg.text_model}）")
                    msg = (
                        f"🤖 已配置 {len(enabled)} 个提供商，正在轮询使用：\n"
                        + "\n".join(f"  • {n}" for n in names)
                    )
            self._add_message("user", t)
            self._add_message("assistant", msg)
            return msg

        # ---- 切换提供商 ----
        if not re.search(r"切换|换[一个]?(?:下|个)?|改[为成用]|切[换到]|用(?!于)", t):
            return None

        _PROVIDER_KEYWORDS = {
            "modelscope": ["modelscope", "魔搭"],
            "dashscope": ["dashscope", "百炼", "通义", "qwen", "千问"],
            "deepseek": ["deepseek", "深度求索"],
            "openai": ["openai", "chatgpt", "gpt"],
            "claude": ["claude", "anthropic"],
        }

        provider = get_llm_provider()
        for prov_id, keywords in _PROVIDER_KEYWORDS.items():
            if any(kw.lower() in t.lower() for kw in keywords):
                cfg = provider.get_config(prov_id)
                if not cfg:
                    continue
                if not cfg.api_key:
                    msg = f"❌ {cfg.display_name} 还没有配置 API Key，可以在 Web 设置页面添加后再切换"
                else:
                    from attention.core.api_settings import get_api_settings
                    get_api_settings().set_active_provider(prov_id)
                    msg = f"✅ 已切换到 {cfg.display_name}（{cfg.text_model}）"
                self._add_message("user", t)
                self._add_message("assistant", msg)
                return msg

        return None

    def _chat_with_llm(self, text: str, ctx: SessionContext) -> str:
        """调用 LLM 生成多轮对话回复（使用 OpenAI 客户端，支持流式）"""
        self._add_message("user", text)

        # 构建上下文 + 消息
        context_info = self._build_context_string(ctx)
        messages_for_llm = self._build_llm_messages(context_info)

        # 将 system prompt 和消息列表组合成标准 messages
        full_messages = [{"role": "system", "content": DIALOGUE_SYSTEM_PROMPT}]
        full_messages.extend(messages_for_llm)

        try:
            provider = get_llm_provider()
            # 多提供商轮询：若配置了多个 API key，则均衡分配请求；否则用当前激活的
            prov_name = provider.next_provider_roundrobin()
            cfg = provider.get_config(prov_name)
            if not cfg or not cfg.api_key:
                # 回退到 active provider
                prov_name = provider.get_active_provider()
                cfg = provider.get_config(prov_name)

            logger.debug(f"对话使用提供商: {prov_name} ({cfg.text_model if cfg else 'N/A'})")
            oai_client = OpenAI(base_url=cfg.api_base, api_key=cfg.api_key)

            stream = oai_client.chat.completions.create(
                model=cfg.text_model,
                messages=full_messages,
                max_tokens=200,
                temperature=0.7,
                stream=True,
                timeout=20,
            )
            chunks = []
            for chunk in stream:
                if chunk.choices:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        chunks.append(delta)
            response = "".join(chunks).strip()
            if not response:
                response = "抱歉，我暂时无法回复。不过你的消息已记录 📝"
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
        elif ctx.focus_task:
            # 专注已暂停（focus_task 非空但 is_focus_mode=False）
            parts.append(f"状态：专注已暂停（任务：{ctx.focus_task}）")
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
