"""
主动规划引擎 — Attention OS v5.2 核心新增

核心理念：
  从"你分心了"变为"你现在应该做 X，但在做 Y，要切过去还是休息一会？"

职责：
  1. 每个监控周期比较"当前屏幕" vs "推荐计划"
  2. 匹配时静默，不匹配时主动发起对话
  3. 管理合法休息模式（sanctioned rest）
  4. 跟踪连续偏离周期数，超过容忍阈值才介入
  5. 生成上下文感知的引导对话（不是说教，是确认意图）
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from dataclasses import dataclass, asdict

from attention.config import Config

logger = logging.getLogger(__name__)


@dataclass
class RestSession:
    """合法休息会话"""
    started_at: datetime
    duration_minutes: int
    reason: str = ""  # 用户声明的原因
    ended_at: Optional[datetime] = None

    @property
    def is_active(self) -> bool:
        if self.ended_at:
            return False
        elapsed = (datetime.now() - self.started_at).total_seconds()
        return elapsed < self.duration_minutes * 60

    @property
    def remaining_seconds(self) -> int:
        if not self.is_active:
            return 0
        elapsed = (datetime.now() - self.started_at).total_seconds()
        return max(0, int(self.duration_minutes * 60 - elapsed))

    @property
    def remaining_minutes(self) -> int:
        return self.remaining_seconds // 60

    def to_dict(self) -> dict:
        return {
            "started_at": self.started_at.strftime("%Y-%m-%d %H:%M:%S"),
            "duration_minutes": self.duration_minutes,
            "reason": self.reason,
            "is_active": self.is_active,
            "remaining_seconds": self.remaining_seconds,
            "remaining_minutes": self.remaining_minutes,
        }


class ActivePlanner:
    """
    主动规划引擎。

    核心流程（每个监控周期调用）：
    1. 检查是否在合法休息中 → 是则静默
    2. 获取 GoalManager 的推荐任务
    3. 比较当前屏幕活动与推荐任务
    4. 匹配 → 静默
    5. 不匹配 → 累计偏离计数 → 超过容忍阈值 → 发起对话
    """

    def __init__(self):
        self._lock = threading.Lock()

        # 合法休息
        self._rest_session: Optional[RestSession] = None

        # 偏离追踪
        self._off_plan_count = 0  # 连续偏离周期计数
        self._off_plan_threshold = 3  # 连续偏离多少个周期触发对话（3×60s=3分钟）

        # 对话冷却
        self._last_plan_nudge: Optional[datetime] = None
        self._plan_nudge_cooldown = 600  # 10分钟冷却

        # 当前活跃计划（缓存）
        self._current_plan: Optional[Dict] = None
        self._plan_override: Optional[str] = None  # 用户临时改变的计划
        self._plan_override_until: Optional[datetime] = None

    # ================================================================ #
    #  合法休息管理
    # ================================================================ #

    def declare_rest(self, minutes: int = 15, reason: str = "") -> Dict[str, Any]:
        """
        用户声明合法休息。

        Args:
            minutes: 休息时长（分钟），默认 15，上限由配置决定
            reason: 休息原因（可选）

        Returns:
            休息会话信息
        """
        max_rest = Config.ACTIVE_PLANNER.get("max_rest_minutes", 30)
        minutes = min(minutes, max_rest)

        with self._lock:
            self._rest_session = RestSession(
                started_at=datetime.now(),
                duration_minutes=minutes,
                reason=reason,
            )
            self._off_plan_count = 0  # 重置偏离计数

        logger.info(f"用户声明休息 {minutes} 分钟: {reason}")

        # 通知 BreakReminder 开始追踪休息时间，以便到时间发送结束提醒
        try:
            from attention.features.break_reminder import get_break_reminder
            get_break_reminder().start_rest_tracking(override_minutes=minutes)
        except Exception as e:
            logger.debug(f"通知 BreakReminder 开始休息追踪失败: {e}")

        return self._rest_session.to_dict()

    def end_rest(self) -> Dict[str, Any]:
        """用户主动结束休息"""
        with self._lock:
            if self._rest_session and self._rest_session.is_active:
                self._rest_session.ended_at = datetime.now()
                result = self._rest_session.to_dict()
                logger.info("用户主动结束休息")

                # 通知 BreakReminder 停止休息追踪
                try:
                    from attention.features.break_reminder import get_break_reminder
                    get_break_reminder().stop_rest_tracking()
                except Exception as e:
                    logger.debug(f"通知 BreakReminder 停止休息追踪失败: {e}")

                return result
            return {"is_active": False}

    def is_resting(self) -> bool:
        """是否在合法休息中"""
        with self._lock:
            return self._rest_session is not None and self._rest_session.is_active

    def get_rest_status(self) -> Optional[Dict]:
        """获取当前休息状态"""
        with self._lock:
            if self._rest_session and self._rest_session.is_active:
                return self._rest_session.to_dict()
            return None

    # ================================================================ #
    #  计划变更
    # ================================================================ #

    def override_plan(self, task_description: str, duration_minutes: int = 60):
        """
        用户声明临时计划变更："我现在改做 X"

        Args:
            task_description: 用户想做的事
            duration_minutes: 持续多长时间（默认 60 分钟）
        """
        with self._lock:
            self._plan_override = task_description
            self._plan_override_until = datetime.now() + timedelta(minutes=duration_minutes)
            self._off_plan_count = 0
        logger.info(f"用户变更计划: {task_description} ({duration_minutes}分钟)")

    def clear_override(self):
        """清除计划变更"""
        with self._lock:
            self._plan_override = None
            self._plan_override_until = None

    def get_active_plan(self) -> Dict[str, Any]:
        """获取当前活跃计划（考虑用户 override）"""
        with self._lock:
            if (self._plan_override and self._plan_override_until
                    and datetime.now() < self._plan_override_until):
                remaining = (self._plan_override_until - datetime.now()).total_seconds() / 60
                return {
                    "source": "user_override",
                    "task_title": self._plan_override,
                    "remaining_minutes": round(remaining),
                }
            else:
                self._plan_override = None
                self._plan_override_until = None

        # 从 GoalManager 获取推荐
        try:
            from attention.features.goal_manager import get_goal_manager
            rec = get_goal_manager().what_should_i_do_now()
            if rec["has_recommendation"]:
                return {
                    "source": "goal_manager",
                    **rec["recommended_task"],
                    "overdue_tasks": rec.get("overdue_tasks", []),
                    "upcoming_deadlines": rec.get("upcoming_deadlines", []),
                }
        except Exception as e:
            logger.debug(f"获取推荐任务失败: {e}")

        return {"source": "none", "task_title": None}

    # ================================================================ #
    #  核心：监控周期检查
    # ================================================================ #

    def check_cycle(
        self,
        current_app: str,
        window_title: str,
        is_productive: bool,
        is_distracted: bool,
        app_category: str,
    ) -> Optional[Dict[str, Any]]:
        """
        每个监控周期调用。返回需要发起的对话，或 None（静默）。

        Returns:
            None: 不需要干预
            Dict: 需要发起对话，包含:
                - action: "plan_check" | "rest_ending" | "plan_suggestion"
                - message_context: 对话上下文
        """
        now = datetime.now()

        # 1. 合法休息中 → 检查是否即将结束
        if self.is_resting():
            rest = self._rest_session
            if rest.remaining_seconds <= 60 and rest.remaining_seconds > 0:
                # 休息快结束了，提前提醒
                return {
                    "action": "rest_ending",
                    "message_context": {
                        "remaining_seconds": rest.remaining_seconds,
                        "reason": rest.reason,
                    }
                }
            return None  # 休息中，静默

        # 检查休息刚刚结束
        with self._lock:
            if self._rest_session and not self._rest_session.is_active and not self._rest_session.ended_at:
                self._rest_session.ended_at = now
                plan = self.get_active_plan()
                return {
                    "action": "rest_over",
                    "message_context": {
                        "plan": plan,
                    }
                }

        # 2. 获取当前计划
        plan = self.get_active_plan()
        if plan["source"] == "none" or not plan.get("task_title"):
            # 没有计划 → 不干扰
            self._off_plan_count = 0
            return None

        # 3. 用户 override 的计划不检查匹配
        if plan["source"] == "user_override":
            self._off_plan_count = 0
            return None

        # 4. 比对屏幕活动与计划
        try:
            from attention.features.goal_manager import get_goal_manager
            match_result = get_goal_manager().match_screen_to_plan(
                current_app, window_title
            )
        except Exception as e:
            logger.debug(f"屏幕-计划匹配失败: {e}")
            return None

        if match_result["matches_plan"]:
            # 匹配 → 重置偏离计数
            self._off_plan_count = 0
            return None

        # 5. 不匹配 → 累计偏离
        with self._lock:
            self._off_plan_count += 1

            if self._off_plan_count < self._off_plan_threshold:
                return None  # 还没到阈值

            # 冷却检查
            if self._last_plan_nudge:
                elapsed = (now - self._last_plan_nudge).total_seconds()
                if elapsed < self._plan_nudge_cooldown:
                    return None

            # 触发对话
            self._last_plan_nudge = now
            self._off_plan_count = 0

        return {
            "action": "plan_check",
            "message_context": {
                "current_app": current_app,
                "window_title": window_title,
                "app_category": app_category,
                "is_distracted": is_distracted,
                "recommended_task": plan,
                "off_plan_minutes": self._off_plan_threshold,
            }
        }

    # ================================================================ #
    #  对话生成
    # ================================================================ #

    def generate_plan_check_message(self, context: Dict) -> str:
        """
        生成计划确认对话消息。

        风格：不是"你分心了"，而是"你在做X，计划是Y，怎么安排？"
        """
        rec = context.get("recommended_task", {})
        task_title = rec.get("task_title", "")
        current_app = context.get("current_app", "")
        app_category = context.get("app_category", "")

        # 尝试 LLM 生成
        try:
            from attention.core.agents import call_agent
            prompt = self._build_plan_check_prompt(context)
            msg = call_agent(
                "planner", prompt,
                max_tokens=120,
                temperature=0.8,
                timeout=8,
            )
            msg = msg.strip().strip('"').strip("'")
            if msg and 5 < len(msg) < 100:
                return msg
        except Exception as e:
            logger.debug(f"Planner Agent 生成失败: {e}")

        # Fallback 模板
        if app_category == "entertainment":
            return (
                f"🎯 你现在在看 {current_app}，但计划里这个时间是「{task_title}」。\n"
                f"要切回去继续吗？还是想休息一会儿？"
            )
        else:
            return (
                f"💡 注意到你在用 {current_app}，当前计划是「{task_title}」。\n"
                f"要切过去吗？或者告诉我你在做什么~"
            )

    def generate_rest_ending_message(self, context: Dict) -> str:
        """生成休息即将结束的提醒"""
        remaining = context.get("remaining_seconds", 0)
        plan = self.get_active_plan()
        task = plan.get("task_title", "")

        if task:
            return f"⏰ 休息快结束了（还剩 {remaining // 60 + 1} 分钟）。准备好回到「{task}」了吗？"
        return f"⏰ 休息快结束了（还剩 {remaining // 60 + 1} 分钟），准备继续了吗？"

    def generate_rest_over_message(self, context: Dict) -> str:
        """生成休息结束的消息"""
        plan = context.get("plan", {})
        task = plan.get("task_title", "")
        if task:
            return f"☕ 休息结束了！接下来推荐做「{task}」，准备好了吗？💪"
        return "☕ 休息结束了！准备继续工作了吗？💪"

    def generate_plan_suggestion_message(self) -> str:
        """生成计划建议消息（主动告知用户该做什么）"""
        plan = self.get_active_plan()
        if not plan.get("task_title"):
            return "📋 目前没有待办目标，要不要设定一个？"

        task = plan["task_title"]
        source = plan["source"]

        if source == "user_override":
            remaining = plan.get("remaining_minutes", 0)
            return f"🎯 你说要做「{task}」，还有 {remaining} 分钟。继续加油！"

        # 来自 goal manager 的推荐
        deadline = plan.get("deadline")
        reason = plan.get("reason", "")
        overdue = plan.get("overdue_tasks", [])

        parts = [f"📋 当前推荐：「{task}」"]
        if reason:
            parts.append(f"（{reason}）")
        if overdue:
            parts.append(f"\n⚠️ 还有 {len(overdue)} 个任务已逾期！")

        return "".join(parts)

    def _build_plan_check_prompt(self, context: Dict) -> str:
        """构建 planner Agent 的 prompt"""
        rec = context.get("recommended_task", {})
        return f"""用户当前状态：
- 正在使用：{context.get('current_app', '未知')}
- 窗口标题：{context.get('window_title', '')[:50]}
- 应用类别：{context.get('app_category', '未知')}

用户当前的计划是：
- 任务：{rec.get('task_title', '未设定')}
- 截止时间：{rec.get('deadline', '无')}
- 原因：{rec.get('reason', '')}

请用 1-2 句话友好地确认用户的意图。
不要说教，像朋友一样。
语气选择：
- 如果用户在做娱乐内容 → 轻松问是不是想休息一下
- 如果用户在做其他工作 → 好奇地问是不是在忙别的事
提供两个选项：切回计划 或 休息一会儿。
不超过 60 字。直接输出，不要前缀。"""

    # ================================================================ #
    #  状态摘要
    # ================================================================ #

    def get_status(self) -> Dict[str, Any]:
        """获取规划引擎的完整状态"""
        plan = self.get_active_plan()
        rest = self.get_rest_status()

        return {
            "current_plan": plan,
            "is_resting": self.is_resting(),
            "rest_status": rest,
            "off_plan_count": self._off_plan_count,
            "off_plan_threshold": self._off_plan_threshold,
            "last_nudge": (
                self._last_plan_nudge.strftime("%H:%M:%S")
                if self._last_plan_nudge else None
            ),
        }


# ============================================================
# 单例
# ============================================================

_planner: Optional[ActivePlanner] = None


def get_active_planner() -> ActivePlanner:
    global _planner
    if _planner is None:
        _planner = ActivePlanner()
    return _planner
