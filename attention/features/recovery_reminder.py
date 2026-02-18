"""
智能回归提醒模块

当用户摸鱼超过5分钟后，用神经科学视角提醒用户可以回归工作。
核心理念：5分钟的休息恰好足够恢复神经递质、清空注意力残留、不会破坏任务上下文。

理论基础：
- Kaplan, S. (1995). "The restorative benefits of nature: Toward an
  integrative framework." Journal of Environmental Psychology, 15(3), 169-182.
  → 注意力恢复理论 (Attention Restoration Theory, ART)

- Mark, G., Gudith, D., & Klocke, U. (2008). "The cost of interrupted work:
  More speed and stress." Proceedings of the SIGCHI Conference on Human Factors
  in Computing Systems (CHI '08), 107-110.
  → 注意力残留效应：中断后平均需要 23 分钟才能完全恢复任务上下文

- Ariga, A., & Lleras, A. (2011). "Brief and rare mental 'breaks' keep you
  focused: Deactivation and reactivation of task goals preempt vigilance
  decrements." Cognition, 118(3), 439-443.
  → 短暂休息对维持持续注意力的正面效果

模型设计：
  三维恢复追踪 = 神经递质恢复 × 注意力残留清除 × 任务上下文衰减
  综合恢复指数 > 0.8 时触发「恢复就绪」提醒
"""
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
from dataclasses import dataclass, asdict

from attention.config import Config

logger = logging.getLogger(__name__)


@dataclass
class RecoveryState:
    """恢复状态追踪"""
    slacking_start: Optional[str] = None        # 开始摸鱼时间
    slacking_duration_seconds: int = 0            # 已摸鱼秒数
    is_slacking: bool = False                     # 是否在摸鱼
    recovery_ready: bool = False                  # 是否已恢复（>=5分钟）
    reminder_shown: bool = False                  # 提醒是否已显示
    last_work_context: str = ""                   # 最后工作上下文
    neurotransmitter_recovery: float = 0.0        # 神经递质恢复进度 (0-1)
    attention_residue_cleared: float = 0.0        # 注意力残留清除进度 (0-1)
    context_integrity: float = 1.0                # 任务上下文完整度 (1→0)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# 神经科学恢复模型参数
RECOVERY_MODEL = {
    "neurotransmitter_half_recovery": 150,  # 多巴胺/去甲肾上腺素半恢复时间(秒)
    "attention_residue_clear_time": 200,    # 注意力残留清除时间(秒)
    "context_decay_start": 600,             # 上下文开始衰减的时间(秒)
    "context_half_life": 1800,              # 上下文半衰期(秒)
    "optimal_break_min": 300,               # 最佳休息时长下限(秒) = 5分钟
    "optimal_break_max": 600,               # 最佳休息时长上限(秒) = 10分钟
}


def calculate_recovery_metrics(elapsed_seconds: int) -> Dict[str, float]:
    """
    根据摸鱼时长计算神经恢复指标

    基于注意力恢复理论 (Attention Restoration Theory):
    - 多巴胺/去甲肾上腺素在2-3分钟开始恢复
    - 注意力残留在3-5分钟基本清除
    - 工作记忆上下文在10分钟后开始衰减

    Returns:
        neurotransmitter: 神经递质恢复程度 (0-1)
        residue_cleared: 注意力残留清除程度 (0-1)
        context_integrity: 任务上下文完整度 (1-0)
    """
    m = RECOVERY_MODEL

    # 神经递质恢复 - 指数恢复曲线
    nt_recovery = 1 - (0.5 ** (elapsed_seconds / m["neurotransmitter_half_recovery"]))
    nt_recovery = min(1.0, nt_recovery)

    # 注意力残留清除 - S型曲线
    import math
    residue_midpoint = m["attention_residue_clear_time"] / 2
    residue_steepness = 0.03
    residue_cleared = 1 / (1 + math.exp(-residue_steepness * (elapsed_seconds - residue_midpoint)))

    # 任务上下文完整度 - 先保持后衰减
    if elapsed_seconds < m["context_decay_start"]:
        context = 1.0
    else:
        decay_time = elapsed_seconds - m["context_decay_start"]
        context = 0.5 ** (decay_time / m["context_half_life"])

    return {
        "neurotransmitter_recovery": round(nt_recovery, 3),
        "attention_residue_cleared": round(residue_cleared, 3),
        "context_integrity": round(context, 3),
    }


def get_recovery_message(elapsed_seconds: int) -> Dict[str, Any]:
    """
    根据恢复阶段生成提醒消息

    Args:
        elapsed_seconds: 摸鱼持续秒数

    Returns:
        消息数据（标题、正文、恢复指标、建议行动）
    """
    metrics = calculate_recovery_metrics(elapsed_seconds)
    minutes = elapsed_seconds / 60

    if minutes < 3:
        # 太早，不提醒
        return {
            "should_remind": False,
            "phase": "too_early",
        }
    elif minutes < 5:
        # 接近最佳恢复点
        return {
            "should_remind": False,
            "phase": "recovering",
            "title": "🧠 神经递质正在恢复中...",
            "body": f"再休息 {5 - minutes:.0f} 分钟效果更佳",
            "metrics": metrics,
        }
    elif minutes < 10:
        # ✅ 最佳回归窗口
        return {
            "should_remind": True,
            "phase": "optimal",
            "title": "✨ 最佳回归时刻",
            "body": "你的大脑已经准备好了",
            "detail_lines": [
                f"🔋 神经递质恢复: {metrics['neurotransmitter_recovery']:.0%}",
                f"🧹 注意力残留清除: {metrics['attention_residue_cleared']:.0%}",
                f"📌 任务上下文保持: {metrics['context_integrity']:.0%}",
            ],
            "suggestion": "现在回归工作，你能以最佳状态无缝衔接之前的任务。",
            "metrics": metrics,
        }
    elif minutes < 20:
        # 上下文开始衰减
        return {
            "should_remind": True,
            "phase": "context_fading",
            "title": "⏳ 任务上下文正在消退",
            "body": "你的神经已完全恢复，但工作记忆开始模糊",
            "detail_lines": [
                f"🔋 神经递质: 充分恢复 ✓",
                f"🧹 注意力残留: 已清除 ✓",
                f"📌 任务上下文: {metrics['context_integrity']:.0%} ⚠️",
            ],
            "suggestion": "建议尽快回归，否则需要更多时间重新进入工作状态。",
            "metrics": metrics,
        }
    else:
        # 长时间摸鱼
        return {
            "should_remind": True,
            "phase": "deep_break",
            "title": "🌙 长时间休息",
            "body": f"已经休息 {minutes:.0f} 分钟，任务上下文可能需要重建",
            "detail_lines": [
                f"🔋 神经递质: 充分恢复 ✓",
                f"🧹 注意力残留: 已清除 ✓",
                f"📌 任务上下文: {metrics['context_integrity']:.0%} ❌",
            ],
            "suggestion": "回到工作时，建议先花2分钟回顾之前的进度，再开始新的工作。",
            "metrics": metrics,
        }


class RecoveryReminder:
    """
    智能回归提醒器
    持续追踪用户状态，在摸鱼5分钟后触发最佳回归提醒
    """

    def __init__(self):
        self._state = RecoveryState()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # 冷却：提醒后至少等10分钟再提醒
        self._last_reminder_time: Optional[datetime] = None
        self._cooldown_seconds = 600

        # 回调
        self._on_reminder = None  # 用于WebSocket推送

    def start(self):
        """启动追踪"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._track_loop, daemon=True)
        self._thread.start()
        logger.info("智能回归提醒已启动")

    def stop(self):
        """停止追踪"""
        self._running = False
        logger.info("智能回归提醒已停止")

    def update_user_state(self, is_productive: bool, is_distracted: bool,
                          active_app: str = "", work_status: str = ""):
        """
        由主监控循环调用，更新用户工作/摸鱼状态

        Args:
            is_productive: 是否在高效工作
            is_distracted: 是否在摸鱼/分心
            active_app: 当前活跃应用
            work_status: 工作状态描述
        """
        with self._lock:
            if is_distracted and not self._state.is_slacking:
                # 开始摸鱼
                self._state.is_slacking = True
                self._state.slacking_start = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                self._state.reminder_shown = False
                self._state.recovery_ready = False
                self._state.last_work_context = work_status
                logger.debug(f"检测到摸鱼开始: {active_app}")

            elif is_productive and self._state.is_slacking:
                # 回归工作
                self._state.is_slacking = False
                self._state.slacking_duration_seconds = 0
                self._state.recovery_ready = False
                self._state.reminder_shown = False
                logger.debug("用户已回归工作")

            elif is_distracted and self._state.is_slacking:
                # 持续摸鱼 → 更新时长
                if self._state.slacking_start:
                    start = datetime.strptime(self._state.slacking_start, "%Y-%m-%d %H:%M:%S")
                    elapsed = (datetime.now() - start).total_seconds()
                    self._state.slacking_duration_seconds = int(elapsed)

                    # 计算恢复指标
                    metrics = calculate_recovery_metrics(int(elapsed))
                    self._state.neurotransmitter_recovery = metrics["neurotransmitter_recovery"]
                    self._state.attention_residue_cleared = metrics["attention_residue_cleared"]
                    self._state.context_integrity = metrics["context_integrity"]

                    # 检查是否到达恢复点
                    if elapsed >= 300 and not self._state.recovery_ready:
                        self._state.recovery_ready = True
                        logger.info("用户摸鱼已满5分钟，进入最佳回归窗口")

    def get_state(self) -> Dict[str, Any]:
        """获取当前恢复状态"""
        with self._lock:
            state = self._state.to_dict()

            # 附加实时消息
            if self._state.is_slacking and self._state.slacking_duration_seconds > 0:
                msg = get_recovery_message(self._state.slacking_duration_seconds)
                state["recovery_message"] = msg
            else:
                state["recovery_message"] = None

            return state

    def _track_loop(self):
        """后台追踪循环（主要用于检测是否需要推送提醒）"""
        while self._running:
            time.sleep(10)  # 每10秒检查一次

            with self._lock:
                if not self._state.is_slacking:
                    continue

                if self._state.reminder_shown:
                    continue

                # 检查冷却
                if self._last_reminder_time:
                    cooldown_remaining = (
                        datetime.now() - self._last_reminder_time
                    ).total_seconds()
                    if cooldown_remaining < self._cooldown_seconds:
                        continue

                # 检查是否到达提醒点
                elapsed = self._state.slacking_duration_seconds
                msg = get_recovery_message(elapsed)

                if msg.get("should_remind", False) and not self._state.reminder_shown:
                    self._state.reminder_shown = True
                    self._last_reminder_time = datetime.now()
                    logger.info(f"触发回归提醒: {msg['title']}")

                    if self._on_reminder:
                        self._on_reminder(msg)


# ==================== 单例 ====================

_recovery: Optional[RecoveryReminder] = None


def get_recovery_reminder() -> RecoveryReminder:
    """获取回归提醒器单例"""
    global _recovery
    if _recovery is None:
        _recovery = RecoveryReminder()
    return _recovery


def start_recovery_reminder() -> RecoveryReminder:
    """启动回归提醒"""
    reminder = get_recovery_reminder()
    reminder.start()
    return reminder


def stop_recovery_reminder():
    """停止回归提醒"""
    global _recovery
    if _recovery:
        _recovery.stop()
