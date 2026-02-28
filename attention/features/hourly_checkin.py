"""
每小时签到模块
每隔一小时通过悬浮对话框询问用户当前在做什么，收集自我报告数据。
"""
import json
import logging
import platform
import subprocess
import threading
import time
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List, Callable
from dataclasses import dataclass, asdict, field
from pathlib import Path

from attention.config import Config

logger = logging.getLogger(__name__)

SYSTEM = platform.system()

CHECKIN_DIR = Config.DATA_DIR / "checkins"


def ensure_dirs():
    CHECKIN_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# 数据结构
# ============================================================

@dataclass
class CheckinEntry:
    """单条签到记录"""
    id: str = ""
    timestamp: str = ""
    hour: int = 0
    doing: str = ""
    feeling: str = "normal"          # great / good / normal / tired / bad
    category: str = "work"
    skipped: bool = False
    auto_app: str = ""
    auto_title: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = datetime.now().strftime("%Y%m%d%H%M%S")
        if not self.timestamp:
            self.timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if not self.hour:
            self.hour = datetime.now().hour

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckinEntry":
        valid = {k: v for k, v in data.items() if k in cls.__dataclass_fields__}
        return cls(**valid)


@dataclass
class CheckinSettings:
    """签到设置"""
    enabled: bool = True
    interval_minutes: int = 60
    start_hour: int = 9
    end_hour: int = 23
    sound_enabled: bool = True
    skip_if_idle: bool = True
    idle_threshold: int = 300

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckinSettings":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


# ============================================================
# 类别和感受推断
# ============================================================

CATEGORY_KEYWORDS = {
    "编程": "coding", "代码": "coding", "code": "coding", "debug": "coding",
    "写": "writing", "文档": "writing", "论文": "writing", "笔记": "writing",
    "会议": "meeting", "讨论": "meeting", "meeting": "meeting", "开会": "meeting",
    "学习": "learning", "看书": "learning", "课程": "learning", "教程": "learning",
    "阅读": "reading", "文章": "reading", "新闻": "reading",
    "邮件": "communication", "微信": "communication", "聊天": "communication",
    "休息": "rest", "摸鱼": "rest", "刷": "entertainment", "看视频": "entertainment",
    "B站": "entertainment", "bilibili": "entertainment", "游戏": "entertainment",
    "运动": "exercise", "锻炼": "exercise", "健身": "exercise",
    "吃饭": "meal", "午餐": "meal", "晚餐": "meal", "外卖": "meal",
}

FEELING_KEYWORDS = {
    "great": ["极佳", "超好", "爽", "高效", "专注", "状态好", "很好", "太棒", "顺畅", "顺手"],
    "good":  ["不错", "还好", "挺好", "良好", "可以", "ok", "okay", "fine"],
    "tired": ["累", "疲惫", "困", "乏", "没劲", "有点累", "头疼", "头痛", "撑不住"],
    "bad":   ["很差", "糟糕", "难受", "烦", "崩溃", "焦虑", "心情差", "状态差", "低落", "很累"],
}


def infer_category(text: str) -> str:
    text_lower = text.lower()
    for keyword, category in CATEGORY_KEYWORDS.items():
        if keyword in text_lower:
            return category
    return "other"


def infer_feeling_from_text(text: str) -> str:
    text_lower = text.lower()
    for feeling, keywords in FEELING_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return feeling
    return "normal"


# ============================================================
# 弹窗（降级方案）
# ============================================================

def show_checkin_dialog_macos() -> Optional[Dict[str, str]]:
    script_doing = '''
    tell application "System Events"
        activate
        set dialogResult to display dialog "⏰ 整点签到

过去一小时你在做什么？" with title "Attention OS · 每小时签到" default answer "" buttons {"跳过", "提交"} default button "提交" with icon note giving up after 120
        set btn to button returned of dialogResult
        set txt to text returned of dialogResult
        return btn & "|" & txt
    end tell
    '''
    try:
        result = subprocess.run(
            ['osascript', '-e', script_doing],
            capture_output=True, text=True, timeout=130
        )
        output = result.stdout.strip()
        if not output or "|" not in output:
            return None

        btn, doing_text = output.split("|", 1)
        if btn == "跳过" or not doing_text.strip():
            return {"skipped": "true", "doing": "", "feeling": "normal"}

        return {"skipped": "false", "doing": doing_text.strip(), "feeling": "normal"}

    except subprocess.TimeoutExpired:
        return {"skipped": "true", "doing": "", "feeling": "normal"}
    except Exception as e:
        logger.error(f"macOS签到弹窗失败: {e}")
        return None


def show_checkin_dialog_windows() -> Optional[Dict[str, str]]:
    try:
        import tkinter as tk
        from tkinter import simpledialog

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)

        doing = simpledialog.askstring(
            "Attention OS · 每小时签到",
            "⏰ 整点签到\n\n过去一小时你在做什么？",
            parent=root
        )

        root.destroy()
        if not doing:
            return {"skipped": "true", "doing": "", "feeling": "normal"}
        return {"skipped": "false", "doing": doing.strip(), "feeling": "normal"}

    except Exception as e:
        logger.error(f"Windows签到弹窗失败: {e}")
        return None


def show_checkin_dialog_linux() -> Optional[Dict[str, str]]:
    try:
        result = subprocess.run(
            ['zenity', '--entry',
             '--title=Attention OS · 每小时签到',
             '--text=⏰ 整点签到\n\n过去一小时你在做什么？',
             '--timeout=120'],
            capture_output=True, text=True, timeout=130
        )
        if result.returncode != 0:
            return {"skipped": "true", "doing": "", "feeling": "normal"}

        doing = result.stdout.strip()
        if not doing:
            return {"skipped": "true", "doing": "", "feeling": "normal"}
        return {"skipped": "false", "doing": doing, "feeling": "normal"}

    except FileNotFoundError:
        logger.warning("zenity 未安装")
        return None
    except Exception as e:
        logger.error(f"Linux签到弹窗失败: {e}")
        return None


def show_checkin_dialog() -> Optional[Dict[str, str]]:
    if SYSTEM == "Darwin":
        return show_checkin_dialog_macos()
    elif SYSTEM == "Windows":
        return show_checkin_dialog_windows()
    elif SYSTEM == "Linux":
        return show_checkin_dialog_linux()
    return None


def play_checkin_sound():
    try:
        if SYSTEM == "Darwin":
            subprocess.Popen(
                ['afplay', '/System/Library/Sounds/Tink.aiff'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        elif SYSTEM == "Windows":
            import winsound
            winsound.MessageBeep(winsound.MB_ICONASTERISK)
        elif SYSTEM == "Linux":
            subprocess.Popen(
                ['paplay', '/usr/share/sounds/freedesktop/stereo/message.oga'],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
    except Exception:
        pass


# ============================================================
# 持久化
# ============================================================

def _get_today_file() -> Path:
    return CHECKIN_DIR / f"checkin_{datetime.now().strftime('%Y-%m-%d')}.json"


def _load_today_entries() -> List[CheckinEntry]:
    ensure_dirs()
    fp = _get_today_file()
    if not fp.exists():
        return []
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [CheckinEntry.from_dict(d) for d in data]
    except Exception as e:
        logger.error(f"加载签到数据失败: {e}")
        return []


def _save_today_entries(entries: List[CheckinEntry]):
    ensure_dirs()
    fp = _get_today_file()
    try:
        with open(fp, 'w', encoding='utf-8') as f:
            json.dump([e.to_dict() for e in entries], f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存签到数据失败: {e}")


def load_entries_by_date(date_str: str) -> List[CheckinEntry]:
    ensure_dirs()
    fp = CHECKIN_DIR / f"checkin_{date_str}.json"
    if not fp.exists():
        return []
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            return [CheckinEntry.from_dict(d) for d in json.load(f)]
    except Exception:
        return []


# ============================================================
# 签到管理器
# ============================================================

class HourlyCheckin:
    """每小时签到管理器"""

    def __init__(self, settings: Optional[CheckinSettings] = None):
        self.settings = settings or CheckinSettings()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._next_checkin: Optional[datetime] = None
        self._showing_dialog = False
        self._on_checkin: Optional[Callable] = None

        self.stats = {
            "checkins_today": 0,
            "skipped_today": 0,
        }

        self.settings_file = Config.DATA_DIR / "checkin_settings.json"
        self._load_settings()
        self._sync_stats()

    def _load_settings(self):
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    self.settings = CheckinSettings.from_dict(json.load(f))
            except Exception as e:
                logger.warning(f"加载签到设置失败: {e}")

    def save_settings(self):
        Config.ensure_dirs()
        try:
            with open(self.settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings.to_dict(), f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"保存签到设置失败: {e}")

    def _sync_stats(self):
        entries = _load_today_entries()
        self.stats["checkins_today"] = len([e for e in entries if not e.skipped])
        self.stats["skipped_today"] = len([e for e in entries if e.skipped])

    def start(self):
        if self._running:
            return
        if not self.settings.enabled:
            logger.info("每小时签到未启用")
            return

        self._running = True
        self._schedule_next()
        self._thread = threading.Thread(target=self._checkin_loop, daemon=True)
        self._thread.start()
        logger.info(
            f"每小时签到已启动，间隔: {self.settings.interval_minutes}分钟，"
            f"活跃时段: {self.settings.start_hour}:00-{self.settings.end_hour}:00"
        )

    def stop(self):
        self._running = False
        logger.info("每小时签到已停止")

    def _schedule_next(self):
        now = datetime.now()
        interval = self.settings.interval_minutes
        if interval >= 60:
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            self._next_checkin = next_hour
        else:
            self._next_checkin = now + timedelta(minutes=interval)

        if self._next_checkin.hour < self.settings.start_hour:
            self._next_checkin = self._next_checkin.replace(
                hour=self.settings.start_hour, minute=0, second=0
            )
        elif self._next_checkin.hour >= self.settings.end_hour:
            tomorrow = self._next_checkin + timedelta(days=1)
            self._next_checkin = tomorrow.replace(
                hour=self.settings.start_hour, minute=0, second=0
            )

        logger.info(f"下次签到: {self._next_checkin.strftime('%H:%M:%S')}")

    def _checkin_loop(self):
        while self._running:
            now = datetime.now()

            if (self._next_checkin and now >= self._next_checkin
                    and not self._showing_dialog):
                current_hour = now.hour
                if self.settings.start_hour <= current_hour < self.settings.end_hour:
                    if self.settings.skip_if_idle and self._is_user_idle():
                        logger.debug("用户空闲，跳过签到")
                        self._schedule_next()
                    else:
                        self._next_checkin = None
                        self._do_checkin()
                else:
                    self._schedule_next()

            time.sleep(10)

    def _is_user_idle(self) -> bool:
        try:
            from attention.core.activity_monitor import get_activity_monitor
            monitor = get_activity_monitor()
            if monitor._running:
                return monitor.get_idle_duration() > self.settings.idle_threshold
        except Exception:
            pass
        return False

    def _do_checkin(self):
        """执行签到：优先通过悬浮对话框，不可用时降级到系统弹窗"""
        self._showing_dialog = True
        logger.info("触发每小时签到...")

        if self.settings.sound_enabled:
            play_checkin_sound()

        try:
            from attention.ui.chat_overlay import get_chat_overlay
            overlay = get_chat_overlay()
            if overlay.is_ready():
                self._do_checkin_via_overlay(overlay)
                return
        except Exception as e:
            logger.debug(f"ChatOverlay 不可用，降级到弹窗: {e}")

        self._do_checkin_via_dialog()

    def _do_checkin_via_overlay(self, overlay):
        auto_app, auto_title = self._get_current_app()

        def on_user_reply(text: str):
            try:
                entry = CheckinEntry(
                    hour=datetime.now().hour,
                    doing=text,
                    feeling=infer_feeling_from_text(text),
                    category=infer_category(text),
                    auto_app=auto_app,
                    auto_title=auto_title,
                )
                entries = _load_today_entries()
                entries.append(entry)
                _save_today_entries(entries)
                self.stats["checkins_today"] += 1
                logger.info(f"对话签到完成: {entry.doing} [{entry.category}]")

                if self._on_checkin:
                    self._on_checkin(entry.to_dict())

                overlay._send_ai_message("✅ 记下了！继续加油～", msg_type="status")
            except Exception as e:
                logger.error(f"保存对话签到失败: {e}")
            finally:
                self._showing_dialog = False
                self._schedule_next()

        overlay.show_checkin_prompt(on_user_reply)

    def _do_checkin_via_dialog(self):
        auto_app, auto_title = self._get_current_app()
        try:
            result = show_checkin_dialog()

            if result is None:
                logger.warning("签到弹窗未能显示")
                self._schedule_next()
                return

            entry = CheckinEntry(
                hour=datetime.now().hour,
                auto_app=auto_app,
                auto_title=auto_title,
            )

            if result.get("skipped") == "true":
                entry.skipped = True
                self.stats["skipped_today"] += 1
                logger.info("用户跳过签到")
            else:
                entry.doing = result.get("doing", "")
                entry.feeling = result.get("feeling", "normal")
                entry.category = infer_category(entry.doing)
                self.stats["checkins_today"] += 1
                logger.info(f"弹窗签到完成: {entry.doing} [{entry.category}]")

            entries = _load_today_entries()
            entries.append(entry)
            _save_today_entries(entries)

            if self._on_checkin:
                self._on_checkin(entry.to_dict())

        except Exception as e:
            logger.error(f"签到异常: {e}")
        finally:
            self._showing_dialog = False
            self._schedule_next()

    def _get_current_app(self) -> tuple:
        try:
            from attention.core.activity_monitor import get_activity_monitor
            monitor = get_activity_monitor()
            snap = monitor.get_latest_snapshot()
            if snap:
                return (snap.active_window_app, snap.active_window_title[:80])
        except Exception:
            pass
        return ("", "")

    # ==================== 公开 API ====================

    def trigger_now(self):
        if not self._showing_dialog:
            threading.Thread(target=self._do_checkin, daemon=True).start()

    def add_entry_from_web(self, doing: str, feeling: str = "normal") -> CheckinEntry:
        auto_app, auto_title = self._get_current_app()
        entry = CheckinEntry(
            hour=datetime.now().hour,
            doing=doing,
            feeling=feeling,
            category=infer_category(doing),
            auto_app=auto_app,
            auto_title=auto_title,
        )
        entries = _load_today_entries()
        entries.append(entry)
        _save_today_entries(entries)
        self.stats["checkins_today"] += 1
        logger.info(f"Web签到: {doing}")
        return entry

    def get_today_entries(self) -> List[Dict[str, Any]]:
        return [e.to_dict() for e in _load_today_entries()]

    def get_status(self) -> Dict[str, Any]:
        minutes_until = None
        if self._next_checkin:
            delta = (self._next_checkin - datetime.now()).total_seconds()
            minutes_until = max(0, int(delta / 60))

        return {
            "enabled": self.settings.enabled,
            "running": self._running,
            "interval_minutes": self.settings.interval_minutes,
            "start_hour": self.settings.start_hour,
            "end_hour": self.settings.end_hour,
            "next_checkin": self._next_checkin.strftime("%H:%M:%S") if self._next_checkin else None,
            "minutes_until_next": minutes_until,
            "stats": self.stats,
            "settings": self.settings.to_dict(),
        }

    def update_settings(self, **kwargs):
        for key, value in kwargs.items():
            if value is not None and hasattr(self.settings, key):
                setattr(self.settings, key, value)
        self.save_settings()
        if self._running:
            self._schedule_next()


# ============================================================
# 单例
# ============================================================

_checkin: Optional[HourlyCheckin] = None


def get_hourly_checkin() -> HourlyCheckin:
    global _checkin
    if _checkin is None:
        _checkin = HourlyCheckin()
    return _checkin


def start_hourly_checkin() -> HourlyCheckin:
    checkin = get_hourly_checkin()
    if checkin.settings.enabled:
        checkin.start()
    return checkin


def stop_hourly_checkin():
    global _checkin
    if _checkin:
        _checkin.stop()
