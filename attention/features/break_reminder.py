"""
休息提醒模块
定时提醒用户休息，支持自定义间隔和提醒方式
"""
import logging
import threading
import time
import platform
import subprocess
from datetime import datetime, timedelta
from typing import Optional, Callable, Dict, Any
from dataclasses import dataclass, asdict
import json
from pathlib import Path

from attention.config import Config

logger = logging.getLogger(__name__)

SYSTEM = platform.system()


@dataclass
class BreakSettings:
    """休息提醒设置"""
    enabled: bool = True                    # 是否启用
    interval_minutes: int = 45              # 连续在场多少分钟后提醒
    break_duration_minutes: int = 5         # 建议休息时长（分钟）
    sound_enabled: bool = True              # 是否播放提示音
    skip_if_idle: bool = True               # 保留字段，兼容旧配置（不再直接使用）
    idle_threshold_seconds: int = 300       # 保留字段，兼容旧配置（不再直接使用）
    real_break_threshold_seconds: int = 600 # 真实休息阈值：离开超过此秒数才重置工作会话
    # 休息结束提醒
    rest_end_reminder_enabled: bool = True  # 是否在休息结束时提醒
    rest_end_reminder_minutes: int = 10     # 休息多少分钟后提醒回来工作
    rest_end_sound_enabled: bool = True     # 休息结束时是否播放提示音
    rest_end_chat_enabled: bool = True      # 休息结束时是否通过对话提醒
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BreakSettings":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def show_macos_dialog(settings: BreakSettings) -> str:
    """
    macOS: 使用AppleScript显示原生对话框
    返回: 'break' / 'snooze' / 'skip'
    """
    tips = [
        "站起来走动一下",
        "让眼睛看看远处",
        "做几个深呼吸",
        "喝杯水补充水分",
    ]
    import random
    tip = random.choice(tips)
    
    script = f'''
    tell application "System Events"
        activate
        set dialogResult to display dialog "你已经连续工作了 {settings.interval_minutes} 分钟

💡 {tip}" with title "⏰ 该休息一下了" buttons {{"跳过", "10分钟后", "开始休息"}} default button "开始休息" with icon note giving up after 60
        return button returned of dialogResult
    end tell
    '''
    
    try:
        result = subprocess.run(
            ['osascript', '-e', script],
            capture_output=True,
            text=True,
            timeout=70
        )
        
        response = result.stdout.strip()
        if response == "开始休息":
            return "break"
        elif response == "10分钟后":
            return "snooze"
        else:
            return "skip"
    except subprocess.TimeoutExpired:
        return "skip"
    except Exception as e:
        logger.error(f"显示对话框失败: {e}")
        return "skip"


def show_macos_notification(settings: BreakSettings):
    """macOS: 显示系统通知（非阻塞）"""
    script = f'''
    display notification "你已经连续工作了 {settings.interval_minutes} 分钟，该休息一下了" with title "⏰ 休息提醒" sound name "Glass"
    '''
    try:
        subprocess.Popen(
            ['osascript', '-e', script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    except Exception as e:
        logger.error(f"显示通知失败: {e}")


def show_windows_dialog(settings: BreakSettings) -> str:
    """
    Windows: 使用ctypes显示MessageBox
    返回: 'break' / 'snooze' / 'skip'
    """
    try:
        import ctypes
        
        message = (
            f"你已经连续工作了 {settings.interval_minutes} 分钟\n\n"
            f"建议休息 {settings.break_duration_minutes} 分钟\n\n"
            '点击"是"开始休息，"否"稍后提醒，"取消"跳过'
        )
        
        # MB_YESNOCANCEL = 0x03, MB_ICONINFORMATION = 0x40
        result = ctypes.windll.user32.MessageBoxW(
            0, 
            message, 
            "⏰ 休息提醒", 
            0x03 | 0x40
        )
        
        if result == 6:  # IDYES
            return "break"
        elif result == 7:  # IDNO
            return "snooze"
        else:  # IDCANCEL or other
            return "skip"
            
    except Exception as e:
        logger.error(f"显示对话框失败: {e}")
        return "skip"


def show_linux_dialog(settings: BreakSettings) -> str:
    """
    Linux: 使用zenity或kdialog显示对话框
    返回: 'break' / 'snooze' / 'skip'
    """
    message = f"你已经连续工作了 {settings.interval_minutes} 分钟\\n建议休息 {settings.break_duration_minutes} 分钟"
    
    # 尝试zenity
    try:
        result = subprocess.run(
            [
                'zenity', '--question',
                '--title=休息提醒',
                f'--text={message}',
                '--ok-label=开始休息',
                '--cancel-label=跳过',
                '--extra-button=10分钟后',
                '--timeout=60'
            ],
            capture_output=True,
            text=True,
            timeout=70
        )
        
        if result.returncode == 0:
            return "break"
        elif "10分钟后" in result.stdout:
            return "snooze"
        else:
            return "skip"
            
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return "skip"
    except Exception as e:
        logger.error(f"zenity失败: {e}")
    
    # 尝试kdialog
    try:
        result = subprocess.run(
            [
                'kdialog', '--yesnocancel',
                message,
                '--title', '休息提醒',
                '--yes-label', '开始休息',
                '--no-label', '10分钟后',
                '--cancel-label', '跳过'
            ],
            capture_output=True,
            timeout=70
        )
        
        if result.returncode == 0:
            return "break"
        elif result.returncode == 1:
            return "snooze"
        else:
            return "skip"
            
    except FileNotFoundError:
        pass
    except subprocess.TimeoutExpired:
        return "skip"
    except Exception as e:
        logger.error(f"kdialog失败: {e}")
    
    # 回退到通知
    try:
        subprocess.run([
            'notify-send',
            '休息提醒',
            f'你已经连续工作了 {settings.interval_minutes} 分钟，该休息一下了'
        ])
    except:
        pass
    
    return "skip"


def play_sound():
    """播放提示音"""
    try:
        if SYSTEM == "Darwin":
            subprocess.Popen(
                ['afplay', '/System/Library/Sounds/Glass.aiff'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        elif SYSTEM == "Windows":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        elif SYSTEM == "Linux":
            subprocess.Popen(
                ['paplay', '/usr/share/sounds/freedesktop/stereo/bell.oga'],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
    except Exception as e:
        logger.debug(f"播放提示音失败: {e}")


def show_reminder_dialog(settings: BreakSettings) -> str:
    """
    显示提醒对话框（跨平台）
    返回: 'break' / 'snooze' / 'skip'
    """
    # 播放提示音
    if settings.sound_enabled:
        play_sound()
    
    # 根据平台选择对话框
    if SYSTEM == "Darwin":
        return show_macos_dialog(settings)
    elif SYSTEM == "Windows":
        return show_windows_dialog(settings)
    elif SYSTEM == "Linux":
        return show_linux_dialog(settings)
    else:
        logger.warning(f"不支持的平台: {SYSTEM}")
        return "skip"


class BreakReminder:
    """
    休息提醒管理器
    管理定时提醒的生命周期
    """
    
    def __init__(self, settings: Optional[BreakSettings] = None):
        self.settings = settings or BreakSettings()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        # _work_session_start: 当前连续工作会话的起始时间
        # None = 用户尚未在场（或刚完成一次提醒/真实休息）
        self._work_session_start: Optional[datetime] = None
        self._snooze_until: Optional[datetime] = None
        self._showing_dialog = False

        # 休息结束提醒
        self._rest_started_at: Optional[datetime] = None
        self._rest_end_reminder_sent = False
        self._rest_end_override_minutes: Optional[int] = None

        # 统计
        self.stats = {
            "reminders_shown": 0,
            "breaks_taken": 0,
            "skipped": 0,
            "snoozed": 0
        }
        
        # 配置文件路径
        self.settings_file = Config.DATA_DIR / "break_settings.json"
        
        # 加载保存的设置
        self._load_settings()
    
    def _load_settings(self):
        """从文件加载设置"""
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.settings = BreakSettings.from_dict(data)
                    logger.info(f"已加载休息提醒设置: 间隔{self.settings.interval_minutes}分钟")
            except Exception as e:
                logger.warning(f"加载休息提醒设置失败: {e}")
    
    def save_settings(self):
        """保存设置到文件"""
        Config.ensure_dirs()
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings.to_dict(), f, ensure_ascii=False, indent=2)
            logger.info("休息提醒设置已保存")
        except Exception as e:
            logger.warning(f"保存休息提醒设置失败: {e}")
    
    def start(self):
        """启动休息提醒"""
        if self._running:
            return

        if not self.settings.enabled:
            logger.info("休息提醒未启用")
            return

        self._running = True
        self._work_session_start = None
        self._thread = threading.Thread(target=self._reminder_loop, daemon=True)
        self._thread.start()
        logger.info(
            f"休息提醒已启动，连续在场超过 {self.settings.interval_minutes} 分钟后提醒，"
            f"真实休息阈值: {self.settings.real_break_threshold_seconds} 秒"
        )
    
    def stop(self):
        """停止休息提醒"""
        self._running = False
        logger.info("休息提醒已停止")
    
    def _reminder_loop(self):
        """
        提醒循环（基于连续在场时长，而非墙钟时间）

        核心逻辑：
        - 只要用户在场（idle < real_break_threshold），工作会话计时就持续累积
        - 短暂静止（读代码、思考，< real_break_threshold）不会重置计时
        - 只有真正离开（idle >= real_break_threshold）才视为休息，重置工作会话
        - 连续在场达到 interval_minutes 后，发送休息提醒
        """
        while self._running:
            time.sleep(30)  # 每30秒检查一次

            # 检查休息结束提醒（独立于工作提醒逻辑）
            self._check_rest_end_reminder()

            if self._showing_dialog:
                continue

            now = datetime.now()
            idle_seconds = self._get_idle_seconds()

            # 贪睡到期 → 若用户在场则立即补发提醒
            if self._snooze_until and now >= self._snooze_until:
                self._snooze_until = None
                if idle_seconds < self.settings.real_break_threshold_seconds:
                    self._show_reminder()
                else:
                    # 用户离开了，贪睡期间自然休息，重置会话
                    self._work_session_start = None
                continue

            # 贪睡中，等待
            if self._snooze_until:
                continue

            # 用户真正离开（超过真实休息阈值）→ 视为已休息，重置工作会话
            if idle_seconds >= self.settings.real_break_threshold_seconds:
                if self._work_session_start is not None:
                    away_minutes = idle_seconds / 60
                    logger.debug(
                        f"用户已离开 {away_minutes:.0f} 分钟，"
                        f"视为真实休息，重置连续工作计时"
                    )
                    self._work_session_start = None
                continue

            # 用户在场（idle < real_break_threshold）
            if self._work_session_start is None:
                # 会话刚开始（或刚结束休息/提醒），补偿已有的在场时间
                self._work_session_start = now - timedelta(seconds=idle_seconds)
                logger.debug(
                    f"开始追踪连续工作时长，估算起点: "
                    f"{self._work_session_start.strftime('%H:%M:%S')}"
                )

            # 计算连续在场时长
            session_minutes = (now - self._work_session_start).total_seconds() / 60
            logger.debug(f"连续工作时长: {session_minutes:.0f} 分钟 / {self.settings.interval_minutes} 分钟")

            if session_minutes >= self.settings.interval_minutes:
                logger.info(f"连续工作 {session_minutes:.0f} 分钟，触发休息提醒")
                # 先重置会话，防止重复触发
                self._work_session_start = None
                self._show_reminder(session_minutes=int(session_minutes))
    
    def _show_reminder(self, session_minutes: int = 0):
        """通过对话悬浮窗发送休息提醒（已从原生对话框迁移到悬浮窗）"""
        self._showing_dialog = True
        self.stats["reminders_shown"] += 1

        actual_minutes = session_minutes or self.settings.interval_minutes
        logger.info(f"发送休息提醒到对话悬浮窗（连续工作 {actual_minutes} 分钟）...")

        # 播放提示音
        if self.settings.sound_enabled:
            play_sound()

        try:
            from attention.ui.chat_overlay import get_chat_overlay
            overlay = get_chat_overlay()
            overlay.show_break_reminder(continuous_minutes=actual_minutes)
        except Exception as e:
            logger.warning(f"发送休息提醒失败: {e}")
        finally:
            self._showing_dialog = False
    
    def _on_take_break(self):
        """用户选择休息 → 启动全屏遮罩"""
        self.stats["breaks_taken"] += 1
        logger.info("用户开始休息，启动全屏遮罩")

        # 开始追踪休息时间，用于休息结束提醒
        self.start_rest_tracking()

        # 【关键】立刻重置计时器，防止循环再次触发弹窗
        # 休息结束后 _on_break_finished 会再次重置为正确的下次时间
        self._reset_timer(delay_minutes=self.settings.break_duration_minutes)

        try:
            from attention.ui.chat_overlay import get_chat_overlay
            overlay = get_chat_overlay()
            overlay.show_break_reminder()
        except Exception as e:
            logger.warning(f"发送休息提醒失败: {e}")
    
    def _on_break_finished(self):
        """休息自然结束"""
        logger.info("休息结束，重置计时器")
        self.stop_rest_tracking()
        self._reset_timer()
        # 播放提示音
        if self.settings.sound_enabled:
            play_sound()

    def _on_break_skipped(self):
        """用户跳过休息"""
        logger.info("用户跳过休息遮罩")
        self.stop_rest_tracking()
        self._reset_timer()
    
    def _on_skip(self):
        """用户跳过"""
        self.stats["skipped"] += 1
        logger.info("用户跳过休息提醒")
        self._reset_timer()
    
    def _on_snooze(self):
        """用户选择稍后提醒"""
        self.stats["snoozed"] += 1
        snooze_minutes = 10
        self._snooze_until = datetime.now() + timedelta(minutes=snooze_minutes)
        self._work_session_start = None
        logger.info(f"已贪睡，{snooze_minutes} 分钟后再次提醒")

    def start_rest_tracking(self, override_minutes: Optional[int] = None):
        """
        开始追踪休息时间，到时间后发送休息结束提醒。

        Args:
            override_minutes: 覆盖设置中的提醒分钟数（用于用户自定义休息时长）
        """
        self._rest_started_at = datetime.now()
        self._rest_end_reminder_sent = False
        minutes = override_minutes or self.settings.rest_end_reminder_minutes
        # 临时覆盖本次提醒时间（不修改持久化设置）
        self._rest_end_override_minutes = override_minutes
        logger.info(
            f"开始追踪休息，{minutes} 分钟后提醒回来工作"
        )

        # 如果主循环没有运行，启动单独的定时器线程
        if not self._running:
            threading.Thread(
                target=self._rest_end_timer_thread,
                args=(minutes,),
                daemon=True,
            ).start()

    def _rest_end_timer_thread(self, minutes: int):
        """独立定时器：当主 _reminder_loop 未运行时，等待指定分钟后发送休息结束提醒"""
        target_seconds = minutes * 60
        elapsed = 0
        while elapsed < target_seconds:
            time.sleep(30)
            elapsed += 30
            # 如果用户提前结束了休息，退出
            if self._rest_started_at is None or self._rest_end_reminder_sent:
                return
        # 到时间了
        if not self._rest_end_reminder_sent and self._rest_started_at is not None:
            self._rest_end_reminder_sent = True
            logger.info(f"（独立定时器）休息已达 {minutes} 分钟，发送回来工作提醒")
            self._send_rest_end_reminder()

    def _check_rest_end_reminder(self):
        """检查是否该发送休息结束提醒（在 _reminder_loop 中每次循环调用）"""
        if not self.settings.rest_end_reminder_enabled:
            return
        if self._rest_started_at is None or self._rest_end_reminder_sent:
            return

        target_minutes = (
            self._rest_end_override_minutes
            or self.settings.rest_end_reminder_minutes
        )
        elapsed = (datetime.now() - self._rest_started_at).total_seconds() / 60
        if elapsed >= target_minutes:
            self._rest_end_reminder_sent = True
            logger.info(
                f"休息已达 {target_minutes} 分钟，发送回来工作提醒"
            )
            self._send_rest_end_reminder()

    def _send_rest_end_reminder(self):
        """通过 ChatOverlay 发送休息结束提醒"""
        # 播放提示音
        if self.settings.rest_end_sound_enabled:
            play_sound()

        if not self.settings.rest_end_chat_enabled:
            return

        import random
        minutes = self._rest_end_override_minutes or self.settings.rest_end_reminder_minutes
        messages = [
            f"☕ 已经休息了 {minutes} 分钟，差不多可以回来继续了！💪",
            f"⏰ 休息 {minutes} 分钟到啦～充好电了吗？准备继续！🚀",
            f"🌿 {minutes} 分钟的休息结束了，精神焕发地回来吧！✨",
        ]
        msg = random.choice(messages)

        try:
            from attention.ui.chat_overlay import get_chat_overlay
            overlay = get_chat_overlay()
            overlay._send_ai_message(msg, msg_type="status")
        except Exception as e:
            logger.warning(f"发送休息结束提醒失败: {e}")

    def stop_rest_tracking(self):
        """停止追踪休息时间"""
        self._rest_started_at = None
        self._rest_end_reminder_sent = False
        self._rest_end_override_minutes = None

    def _reset_timer(self, delay_minutes: int = 0):
        """重置工作会话计时（兼容旧调用）"""
        self._work_session_start = None
        self._snooze_until = None
        logger.debug("工作会话计时已重置")
    
    def _get_idle_seconds(self) -> int:
        """获取用户空闲时间"""
        try:
            from attention.core.activity_monitor import get_activity_monitor
            monitor = get_activity_monitor()
            if monitor._running:
                return monitor.get_idle_duration()
        except:
            pass
        return 0
    
    def update_settings(
        self,
        interval_minutes: Optional[int] = None,
        break_duration_minutes: Optional[int] = None,
        enabled: Optional[bool] = None,
        sound_enabled: Optional[bool] = None,
        skip_if_idle: Optional[bool] = None,
        rest_end_reminder_enabled: Optional[bool] = None,
        rest_end_reminder_minutes: Optional[int] = None,
        rest_end_sound_enabled: Optional[bool] = None,
        rest_end_chat_enabled: Optional[bool] = None,
    ):
        """更新设置"""
        if interval_minutes is not None:
            self.settings.interval_minutes = max(1, min(120, interval_minutes))
        if break_duration_minutes is not None:
            self.settings.break_duration_minutes = max(1, min(30, break_duration_minutes))
        if enabled is not None:
            self.settings.enabled = enabled
        if sound_enabled is not None:
            self.settings.sound_enabled = sound_enabled
        if skip_if_idle is not None:
            self.settings.skip_if_idle = skip_if_idle
        if rest_end_reminder_enabled is not None:
            self.settings.rest_end_reminder_enabled = rest_end_reminder_enabled
        if rest_end_reminder_minutes is not None:
            self.settings.rest_end_reminder_minutes = max(1, min(60, rest_end_reminder_minutes))
        if rest_end_sound_enabled is not None:
            self.settings.rest_end_sound_enabled = rest_end_sound_enabled
        if rest_end_chat_enabled is not None:
            self.settings.rest_end_chat_enabled = rest_end_chat_enabled

        # 保存设置
        self.save_settings()

        # 如果正在运行，重置计时器
        if self._running:
            self._reset_timer()
    
    def get_status(self) -> Dict[str, Any]:
        """获取当前状态"""
        now = datetime.now()
        next_reminder_str = None
        minutes_until = None
        session_elapsed_minutes = None

        if self._snooze_until:
            # 贪睡中：下次提醒 = 贪睡到期时间
            delta = (self._snooze_until - now).total_seconds()
            minutes_until = max(0, int(delta / 60))
            next_reminder_str = self._snooze_until.strftime("%H:%M:%S")
        elif self._work_session_start:
            # 工作会话进行中：根据已累计时长推算剩余时间
            elapsed = (now - self._work_session_start).total_seconds() / 60
            session_elapsed_minutes = int(elapsed)
            remaining = max(0, self.settings.interval_minutes - elapsed)
            minutes_until = int(remaining)
            next_remind_at = self._work_session_start + timedelta(minutes=self.settings.interval_minutes)
            next_reminder_str = next_remind_at.strftime("%H:%M:%S")

        return {
            "enabled": self.settings.enabled,
            "running": self._running,
            "interval_minutes": self.settings.interval_minutes,
            "break_duration_minutes": self.settings.break_duration_minutes,
            "sound_enabled": self.settings.sound_enabled,
            "next_reminder": next_reminder_str,
            "minutes_until_next": minutes_until,
            "session_elapsed_minutes": session_elapsed_minutes,
            "stats": self.stats
        }
    
    def trigger_now(self):
        """立即触发提醒（用于测试）"""
        if not self._showing_dialog:
            threading.Thread(target=self._show_reminder, daemon=True).start()


# 单例
_reminder: Optional[BreakReminder] = None


def get_break_reminder() -> BreakReminder:
    """获取休息提醒器单例"""
    global _reminder
    if _reminder is None:
        _reminder = BreakReminder()
    return _reminder


def start_break_reminder():
    """启动休息提醒"""
    reminder = get_break_reminder()
    if reminder.settings.enabled:
        reminder.start()
    return reminder


def stop_break_reminder():
    """停止休息提醒"""
    global _reminder
    if _reminder:
        _reminder.stop()


# 测试
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    
    settings = BreakSettings(
        interval_minutes=1,
        break_duration_minutes=5,
        sound_enabled=True
    )
    
    print(f"测试 {SYSTEM} 平台的休息提醒对话框...")
    result = show_reminder_dialog(settings)
    print(f"用户选择: {result}")
