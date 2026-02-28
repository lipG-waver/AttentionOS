"""
番茄钟模块
标准番茄工作法：25分钟工作 → 5分钟休息 → 每4个循环一次长休息(15分钟)
支持屏幕模糊提醒、强制休息、用户反对机制
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, Callable
from dataclasses import dataclass, asdict
from pathlib import Path
from enum import Enum

from attention.config import Config

logger = logging.getLogger(__name__)


class PomodoroPhase(Enum):
    """番茄钟阶段"""
    IDLE = "idle"              # 空闲（未启动）
    WORKING = "working"        # 工作中
    SHORT_BREAK = "short_break"  # 短休息
    LONG_BREAK = "long_break"    # 长休息
    PAUSED = "paused"          # 暂停


@dataclass
class PomodoroSettings:
    """番茄钟设置"""
    enabled: bool = True
    work_minutes: int = 25           # 工作时长（分钟）
    short_break_minutes: int = 5     # 短休息时长
    long_break_minutes: int = 15     # 长休息时长
    cycles_before_long: int = 4      # 多少个循环后长休息
    auto_start_work: bool = False    # 休息结束后自动开始工作
    auto_start_break: bool = True    # 工作结束后自动开始休息
    force_break: bool = True         # 是否强制休息（屏幕模糊）
    nudge_if_working: bool = True    # 如果用户持续工作，主动提醒

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PomodoroSettings":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class PomodoroSession:
    """番茄钟会话数据"""
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    phase: str = "idle"
    completed_cycles: int = 0         # 当日已完成的完整番茄钟数
    current_cycle: int = 0            # 当前是第几个番茄（1-4）
    total_work_minutes: int = 0       # 累计工作分钟数
    total_break_minutes: int = 0      # 累计休息分钟数
    skipped_breaks: int = 0           # 跳过休息次数
    override_count: int = 0           # 用户强行继续工作次数

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PomodoroTimer:
    """
    番茄钟计时器
    管理工作/休息循环，发送屏幕模糊信号，记录统计数据
    """

    def __init__(self, settings: Optional[PomodoroSettings] = None):
        self.settings = settings or PomodoroSettings()
        self._phase = PomodoroPhase.IDLE
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # 计时
        self._phase_start: Optional[datetime] = None
        self._phase_end: Optional[datetime] = None
        self._paused_remaining: Optional[float] = None

        # 循环计数
        self._current_cycle = 0       # 当前番茄在1-4中的位置
        self._completed_cycles = 0    # 总完成数
        self._skipped_breaks = 0
        self._override_count = 0
        self._total_work_seconds = 0
        self._total_break_seconds = 0

        # 事件回调（给WebSocket推送用）
        self._on_phase_change: Optional[Callable] = None
        self._on_break_start: Optional[Callable] = None
        self._on_work_start: Optional[Callable] = None
        self._on_nudge: Optional[Callable] = None

        # 专注任务绑定
        self._focus_task: Optional[str] = None       # 当前绑定的任务文本
        self._focus_task_source: Optional[str] = None # "goal" 或 "todo"
        self._focus_sessions: list = []               # 今日完成的专注记录
        self._focus_sessions_file = Config.DATA_DIR / "focus_sessions.json"
        self._load_focus_sessions()  # 从磁盘恢复今日记录

        # 持久化
        self.settings_file = Config.DATA_DIR / "pomodoro_settings.json"
        self.session_file = Config.DATA_DIR / "pomodoro_session.json"
        self._load_settings()

        # 浮窗（始终置顶的迷你计时器）
        self._floating_overlay = None
        self._init_floating_overlay()

    # ==================== 对话悬浮窗集成 ====================

    def _init_floating_overlay(self):
        """初始化对话悬浮窗集成（替代原独立番茄钟浮窗）"""
        try:
            from attention.ui.chat_overlay import get_chat_overlay
            self._floating_overlay = get_chat_overlay()

            # 连接悬浮窗操作回调 → 番茄钟动作
            self._floating_overlay.on_focus_start = self._overlay_action_start
            self._floating_overlay.on_focus_pause = self._overlay_action_pause
            self._floating_overlay.on_focus_resume = self._overlay_action_resume
            self._floating_overlay.on_focus_stop = self._overlay_action_stop
            self._floating_overlay.on_skip_break = self._overlay_action_skip_break

            logger.info("番茄钟已集成到对话悬浮窗")
            # 立即发送初始状态
            self._update_floating_overlay()
        except Exception as e:
            logger.warning(f"对话悬浮窗集成失败（不影响核心功能）: {e}")
            self._floating_overlay = None

    def _overlay_action_start(self):
        """浮窗按钮: 开始专注"""
        self.start_work()

    def _overlay_action_pause(self):
        """浮窗按钮: 暂停"""
        self.pause()

    def _overlay_action_resume(self):
        """浮窗按钮: 继续"""
        self.resume()

    def _overlay_action_stop(self):
        """浮窗按钮: 停止"""
        self.stop()

    def _overlay_action_skip_break(self):
        """浮窗按钮: 跳过休息"""
        self.skip_break()

    def _overlay_action_open_dashboard(self):
        """浮窗按钮: 打开仪表盘"""
        try:
            import webbrowser
            webbrowser.open("http://127.0.0.1:5000")
        except Exception:
            pass

    def _update_floating_overlay(self):
        """根据当前状态更新对话悬浮窗计时器显示"""
        if not self._floating_overlay:
            return
        try:
            # 计算剩余时间
            remaining = 0
            if self._phase == PomodoroPhase.PAUSED and self._paused_remaining:
                remaining = int(self._paused_remaining)
            elif self._phase_end:
                remaining = max(0, int((self._phase_end - datetime.now()).total_seconds()))

            # IDLE 时显示默认工作时长
            if self._phase == PomodoroPhase.IDLE:
                remaining = self.settings.work_minutes * 60

            time_text = self._format_time(remaining)

            # 计算进度 (0~1)
            total_seconds = 0
            if self._phase == PomodoroPhase.WORKING:
                total_seconds = self.settings.work_minutes * 60
            elif self._phase == PomodoroPhase.SHORT_BREAK:
                total_seconds = self.settings.short_break_minutes * 60
            elif self._phase == PomodoroPhase.LONG_BREAK:
                total_seconds = self.settings.long_break_minutes * 60

            progress = 0.0
            if total_seconds > 0:
                progress = max(0.0, min(1.0, 1.0 - remaining / total_seconds))

            # 映射 phase 到 chat_overlay 的格式
            phase_map = {
                PomodoroPhase.IDLE: "idle",
                PomodoroPhase.WORKING: "working",
                PomodoroPhase.SHORT_BREAK: "short_break",
                PomodoroPhase.LONG_BREAK: "long_break",
                PomodoroPhase.PAUSED: "paused",
            }
            phase_str = phase_map.get(self._phase, "idle")

            self._floating_overlay.update_timer(
                time_text=time_text,
                phase=phase_str,
                progress=progress,
            )
        except Exception:
            pass

    # ==================== 生命周期 ====================

    def start_work(self, focus_task: Optional[str] = None, task_source: Optional[str] = None):
        """开始工作阶段
        
        Args:
            focus_task: 本次专注绑定的任务描述
            task_source: 任务来源 - "goal"(今日目标) 或 "todo"(待办事项)
        """
        with self._lock:
            if self._phase == PomodoroPhase.WORKING:
                return

            self._current_cycle += 1
            if self._current_cycle > self.settings.cycles_before_long:
                self._current_cycle = 1

            # 绑定专注任务
            if focus_task:
                self._focus_task = focus_task
                self._focus_task_source = task_source or "manual"
            
            self._set_phase(
                PomodoroPhase.WORKING,
                duration_minutes=self.settings.work_minutes
            )
            task_info = f" → 专注: {self._focus_task}" if self._focus_task else ""
            logger.info(
                f"番茄钟工作开始 - 第{self._current_cycle}个番茄 "
                f"(共{self.settings.cycles_before_long}个为一组){task_info}"
            )

            # 通知对话悬浮窗
            try:
                from attention.ui.chat_overlay import get_chat_overlay
                overlay = get_chat_overlay()
                overlay.on_focus_started(
                    task=self._focus_task or "自由专注",
                    duration_min=self.settings.work_minutes,
                )
            except Exception:
                pass

    def start_break(self, force: bool = False):
        """开始休息阶段"""
        with self._lock:
            is_long = (self._current_cycle >= self.settings.cycles_before_long)
            if is_long:
                phase = PomodoroPhase.LONG_BREAK
                minutes = self.settings.long_break_minutes
                logger.info(f"长休息开始 - {minutes}分钟 (完成一组{self.settings.cycles_before_long}个番茄)")
            else:
                phase = PomodoroPhase.SHORT_BREAK
                minutes = self.settings.short_break_minutes
                logger.info(f"短休息开始 - {minutes}分钟")

            self._set_phase(phase, duration_minutes=minutes)

    def skip_break(self):
        """跳过休息（用户强行继续工作）"""
        with self._lock:
            self._skipped_breaks += 1
            self._override_count += 1
            logger.info(f"用户跳过休息 (第{self._override_count}次)")
            # 直接开始下一个工作阶段
        self.start_work()

    def pause(self):
        """暂停计时器"""
        with self._lock:
            if self._phase in (PomodoroPhase.IDLE, PomodoroPhase.PAUSED):
                return
            remaining = (self._phase_end - datetime.now()).total_seconds()
            self._paused_remaining = max(0, remaining)
            self._previous_phase = self._phase
            self._phase = PomodoroPhase.PAUSED
            logger.info(f"番茄钟已暂停 (剩余{self._paused_remaining:.0f}秒)")

    def resume(self):
        """恢复计时器"""
        with self._lock:
            if self._phase != PomodoroPhase.PAUSED or self._paused_remaining is None:
                return
            self._phase = self._previous_phase
            self._phase_start = datetime.now()
            self._phase_end = datetime.now() + timedelta(seconds=self._paused_remaining)
            self._paused_remaining = None
            logger.info("番茄钟已恢复")

    def stop(self):
        """停止计时器"""
        with self._lock:
            self._running = False
            self._phase = PomodoroPhase.IDLE
            self._phase_start = None
            self._phase_end = None
            self._focus_task = None
            self._focus_task_source = None
            logger.info("番茄钟已停止")
        self._update_floating_overlay()

    def reset(self):
        """重置所有数据"""
        self.stop()
        self._current_cycle = 0
        self._completed_cycles = 0
        self._skipped_breaks = 0
        self._override_count = 0
        self._total_work_seconds = 0
        self._total_break_seconds = 0
        self._update_floating_overlay()

    # ==================== 内部逻辑 ====================

    def _set_phase(self, phase: PomodoroPhase, duration_minutes: float):
        """设置新阶段"""
        now = datetime.now()

        # 统计上一阶段时长
        if self._phase_start and self._phase != PomodoroPhase.IDLE:
            elapsed = (now - self._phase_start).total_seconds()
            if self._phase == PomodoroPhase.WORKING:
                self._total_work_seconds += elapsed
            elif self._phase in (PomodoroPhase.SHORT_BREAK, PomodoroPhase.LONG_BREAK):
                self._total_break_seconds += elapsed

        self._phase = phase
        self._phase_start = now
        self._phase_end = now + timedelta(minutes=duration_minutes)

        # 工作阶段完成时计数
        if phase in (PomodoroPhase.SHORT_BREAK, PomodoroPhase.LONG_BREAK):
            self._completed_cycles += 1

        # 确保后台循环在运行
        if not self._running:
            self._running = True
            self._thread = threading.Thread(target=self._timer_loop, daemon=True)
            self._thread.start()

    def _timer_loop(self):
        """后台计时循环"""
        while self._running:
            time.sleep(1)

            if self._phase == PomodoroPhase.PAUSED:
                self._update_floating_overlay()
                continue

            if self._phase == PomodoroPhase.IDLE:
                self._update_floating_overlay()
                continue

            now = datetime.now()
            if self._phase_end and now >= self._phase_end:
                self._on_phase_complete()

            self._update_floating_overlay()

    def _on_phase_complete(self):
        """阶段完成处理"""
        completed_phase = self._phase

        if completed_phase == PomodoroPhase.WORKING:
            # 工作结束 → 记录专注会话
            if self._focus_task:
                session = {
                    "task": self._focus_task,
                    "source": self._focus_task_source,
                    "duration_minutes": self.settings.work_minutes,
                    "completed_at": datetime.now().strftime("%H:%M:%S"),
                }
                self._focus_sessions.append(session)
                # 持久化到磁盘
                self._save_focus_sessions()
            
            logger.info("工作阶段结束")

            # 通知对话悬浮窗
            try:
                from attention.ui.chat_overlay import get_chat_overlay
                overlay = get_chat_overlay()
                overlay.on_focus_ended(
                    task=self._focus_task or "自由专注",
                    duration_min=self.settings.work_minutes,
                    completed=True,
                )
            except Exception:
                pass
            
            if self.settings.auto_start_break:
                self.start_break()

                # 强制休息模式：启动全屏遮罩
                if self.settings.force_break:
                    self._trigger_break_overlay()

        elif completed_phase in (PomodoroPhase.SHORT_BREAK, PomodoroPhase.LONG_BREAK):
            # 休息结束
            logger.info("休息阶段结束")
            if completed_phase == PomodoroPhase.LONG_BREAK:
                self._current_cycle = 0  # 重置循环计数
            if self.settings.auto_start_work:
                self.start_work()
            else:
                self._phase = PomodoroPhase.IDLE
                self._phase_start = None
                self._phase_end = None

    def _save_focus_sessions(self):
        """将今日专注记录持久化到磁盘"""
        try:
            from datetime import date
            today = date.today().isoformat()
            data = {}
            if self._focus_sessions_file.exists():
                with open(self._focus_sessions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data[today] = self._focus_sessions
            Config.ensure_dirs()
            with open(self._focus_sessions_file, "w", encoding="utf-8") as f:
                json.dump(data, ensure_ascii=False, indent=2, fp=f)
            logger.debug(f"专注记录已保存: {len(self._focus_sessions)} 条")
        except Exception as e:
            logger.warning(f"保存专注记录失败: {e}")

    def _load_focus_sessions(self):
        """从磁盘恢复今日专注记录（重启后不丢失）"""
        try:
            from datetime import date
            today = date.today().isoformat()
            if self._focus_sessions_file.exists():
                with open(self._focus_sessions_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._focus_sessions = data.get(today, [])
                if self._focus_sessions:
                    logger.info(f"恢复今日 {len(self._focus_sessions)} 条专注记录")
        except Exception as e:
            logger.warning(f"加载专注记录失败: {e}")
            self._focus_sessions = []

    def _trigger_break_overlay(self):
        """通过对话悬浮窗提醒休息（替代原全屏遮罩）"""
        try:
            from attention.ui.chat_overlay import get_chat_overlay
            overlay = get_chat_overlay()
            overlay.show_break_reminder()
            
            is_long = (self._phase in (PomodoroPhase.LONG_BREAK,))
            duration = (self.settings.long_break_minutes
                        if is_long
                        else self.settings.short_break_minutes)
            logger.info(f"休息对话提醒已发送 ({duration}分钟)")
        except Exception as e:
            logger.warning(f"发送休息提醒失败: {e}")

    # ==================== 状态查询 ====================

    def get_status(self) -> Dict[str, Any]:
        """获取当前状态"""
        with self._lock:
            remaining_seconds = 0
            total_seconds = 0
            progress = 0

            if self._phase_start and self._phase_end:
                now = datetime.now()
                total_seconds = (self._phase_end - self._phase_start).total_seconds()
                remaining_seconds = max(0, (self._phase_end - now).total_seconds())
                if total_seconds > 0:
                    progress = 1 - (remaining_seconds / total_seconds)

            if self._phase == PomodoroPhase.PAUSED and self._paused_remaining:
                remaining_seconds = self._paused_remaining

            return {
                "phase": self._phase.value,
                "phase_label": self._get_phase_label(),
                "current_cycle": self._current_cycle,
                "cycles_before_long": self.settings.cycles_before_long,
                "completed_cycles": self._completed_cycles,
                "remaining_seconds": int(remaining_seconds),
                "total_seconds": int(total_seconds),
                "progress": round(progress, 3),
                "remaining_display": self._format_time(int(remaining_seconds)),
                "total_work_minutes": int(self._total_work_seconds / 60),
                "total_break_minutes": int(self._total_break_seconds / 60),
                "skipped_breaks": self._skipped_breaks,
                "override_count": self._override_count,
                "is_break": self._phase in (
                    PomodoroPhase.SHORT_BREAK, PomodoroPhase.LONG_BREAK
                ),
                "should_blur": (
                    self.settings.force_break and
                    self._phase in (PomodoroPhase.SHORT_BREAK, PomodoroPhase.LONG_BREAK)
                ),
                "focus_task": self._focus_task,
                "focus_task_source": self._focus_task_source,
                "focus_sessions": self._focus_sessions,
                "settings": self.settings.to_dict(),
            }

    def _get_phase_label(self) -> str:
        """获取阶段中文标签"""
        labels = {
            PomodoroPhase.IDLE: "空闲",
            PomodoroPhase.WORKING: "专注工作中",
            PomodoroPhase.SHORT_BREAK: "短休息",
            PomodoroPhase.LONG_BREAK: "长休息",
            PomodoroPhase.PAUSED: "已暂停",
        }
        return labels.get(self._phase, "未知")

    @staticmethod
    def _format_time(seconds: int) -> str:
        """格式化时间显示"""
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes:02d}:{secs:02d}"

    # ==================== 持久化 ====================

    def _load_settings(self):
        """加载设置"""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.settings = PomodoroSettings.from_dict(data)
            except Exception as e:
                logger.warning(f"加载番茄钟设置失败: {e}")

    def save_settings(self):
        """保存设置"""
        Config.ensure_dirs()
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存番茄钟设置失败: {e}")

    def update_settings(self, **kwargs):
        """更新设置"""
        for key, value in kwargs.items():
            if value is not None and hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.save_settings()


# ==================== 单例 ====================

_pomodoro: Optional[PomodoroTimer] = None


def get_pomodoro() -> PomodoroTimer:
    """获取番茄钟单例"""
    global _pomodoro
    if _pomodoro is None:
        _pomodoro = PomodoroTimer()
    return _pomodoro
