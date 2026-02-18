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
    interval_minutes: int = 45              # 提醒间隔（分钟）
    break_duration_minutes: int = 5         # 建议休息时长（分钟）
    sound_enabled: bool = True              # 是否播放提示音
    skip_if_idle: bool = True               # 如果用户空闲则跳过提醒
    idle_threshold_seconds: int = 300       # 空闲阈值（秒）
    
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
        self._next_reminder: Optional[datetime] = None
        self._snooze_until: Optional[datetime] = None
        self._showing_dialog = False
        
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
        self._next_reminder = datetime.now() + timedelta(minutes=self.settings.interval_minutes)
        self._thread = threading.Thread(target=self._reminder_loop, daemon=True)
        self._thread.start()
        logger.info(f"休息提醒已启动，间隔: {self.settings.interval_minutes}分钟，下次提醒: {self._next_reminder.strftime('%H:%M:%S')}")
    
    def stop(self):
        """停止休息提醒"""
        self._running = False
        logger.info("休息提醒已停止")
    
    def _reminder_loop(self):
        """提醒循环"""
        while self._running:
            now = datetime.now()
            
            # 检查是否到达提醒时间
            if self._next_reminder and now >= self._next_reminder:
                # 检查是否正在显示对话框
                if self._showing_dialog:
                    time.sleep(5)
                    continue
                
                # 检查是否在贪睡期间
                if self._snooze_until and now < self._snooze_until:
                    time.sleep(10)
                    continue
                
                # 检查用户是否空闲
                if self.settings.skip_if_idle:
                    idle_seconds = self._get_idle_seconds()
                    if idle_seconds > self.settings.idle_threshold_seconds:
                        logger.debug(f"用户空闲 {idle_seconds}秒，跳过本次提醒")
                        self._reset_timer()
                        continue
                
                # 【关键】先清空触发条件，再显示提醒
                # 防止 _show_reminder 执行期间循环再次判定 now >= _next_reminder
                self._next_reminder = None
                
                # 显示提醒（内部各分支会调用 _reset_timer 设置新的 _next_reminder）
                self._show_reminder()
            
            time.sleep(5)  # 每5秒检查一次
    
    def _show_reminder(self):
        """显示提醒"""
        self._showing_dialog = True
        self.stats["reminders_shown"] += 1
        
        logger.info("显示休息提醒...")
        
        try:
            result = show_reminder_dialog(self.settings)
            
            if result == "break":
                self._on_take_break()
            elif result == "snooze":
                self._on_snooze()
            else:
                self._on_skip()
        except Exception as e:
            logger.error(f"显示提醒异常: {e}")
            # 异常时也必须重置，否则会无限弹窗
            self._reset_timer()
        finally:
            self._showing_dialog = False
    
    def _on_take_break(self):
        """用户选择休息 → 启动全屏遮罩"""
        self.stats["breaks_taken"] += 1
        logger.info("用户开始休息，启动全屏遮罩")
        
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
        self._reset_timer()
        # 播放提示音
        if self.settings.sound_enabled:
            play_sound()
    
    def _on_break_skipped(self):
        """用户跳过休息"""
        logger.info("用户跳过休息遮罩")
        self._reset_timer()
    
    def _on_skip(self):
        """用户跳过"""
        self.stats["skipped"] += 1
        logger.info("用户跳过休息提醒")
        self._reset_timer()
    
    def _on_snooze(self):
        """用户选择稍后提醒"""
        self.stats["snoozed"] += 1
        logger.info("用户选择10分钟后提醒")
        self._snooze_until = datetime.now() + timedelta(minutes=10)
        self._next_reminder = self._snooze_until
    
    def _reset_timer(self, delay_minutes: int = 0):
        """重置计时器"""
        base_time = datetime.now() + timedelta(minutes=delay_minutes)
        self._next_reminder = base_time + timedelta(minutes=self.settings.interval_minutes)
        self._snooze_until = None
        logger.info(f"下次提醒时间: {self._next_reminder.strftime('%H:%M:%S')}")
    
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
        skip_if_idle: Optional[bool] = None
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
        
        # 保存设置
        self.save_settings()
        
        # 如果正在运行，重置计时器
        if self._running:
            self._reset_timer()
    
    def get_status(self) -> Dict[str, Any]:
        """获取当前状态"""
        minutes_until = None
        if self._next_reminder:
            delta = (self._next_reminder - datetime.now()).total_seconds()
            minutes_until = max(0, int(delta / 60))
        
        return {
            "enabled": self.settings.enabled,
            "running": self._running,
            "interval_minutes": self.settings.interval_minutes,
            "break_duration_minutes": self.settings.break_duration_minutes,
            "sound_enabled": self.settings.sound_enabled,
            "next_reminder": self._next_reminder.strftime("%H:%M:%S") if self._next_reminder else None,
            "minutes_until_next": minutes_until,
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
