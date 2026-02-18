"""
每小时签到模块
每隔一小时弹窗询问用户当前在做什么，收集自我报告数据。
晚间自动汇总生成当日回顾报告。

设计理念：
- 自动化截图分析是"第三视角"，而每小时签到是"第一视角"
- 两者结合才能真正理解用户的注意力分配
- 晚间报告将签到数据与自动监控数据融合，形成完整的日回顾
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

# 数据文件
CHECKIN_DIR = Config.DATA_DIR / "checkins"
SUMMARY_DIR = Config.DATA_DIR / "evening_summaries"


def ensure_dirs():
    CHECKIN_DIR.mkdir(parents=True, exist_ok=True)
    SUMMARY_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================
# LLM 调用（用于晚间总结）
# ============================================================

def _build_summary_prompt(entries: list, cat_counts: dict, feel_counts: dict, date_str: str) -> str:
    """构建发送给 LLM 的晚间总结 prompt"""
    timeline_parts = []
    for e in entries:
        time_str = e.timestamp.split(" ")[1][:5] if " " in e.timestamp else f"{e.hour}:00"
        if e.skipped:
            timeline_parts.append(f"  {time_str} — (跳过签到)")
        else:
            feel_label = FEELING_LABELS.get(e.feeling, e.feeling)
            cat_label = CATEGORY_LABELS.get(e.category, e.category)
            timeline_parts.append(f"  {time_str} — {e.doing} [{cat_label}] 状态: {feel_label}")
    timeline_text = "\n".join(timeline_parts)

    cat_text = ", ".join(f"{CATEGORY_LABELS.get(k, k)}: {v}次" for k, v in cat_counts.items())
    feel_text = ", ".join(f"{FEELING_LABELS.get(k, k)}: {v}次" for k, v in feel_counts.items())

    prompt = f"""你是一位个人效率教练和注意力管理专家。以下是用户 {date_str} 的每小时签到记录。
请根据这些数据，生成一份温暖且有洞察力的晚间总结。

## 签到时间线
{timeline_text}

## 统计概览
- 类别分布: {cat_text}
- 状态分布: {feel_text}
- 总签到数: {len(entries)}，其中跳过: {sum(1 for e in entries if e.skipped)}

## 请你输出以下内容（使用中文）：

1. **一日叙事**（narrative）: 用2-3句话描述用户这一天的工作和生活节奏，像朋友一样自然地总结。
2. **亮点**（highlights）: 列出2-3个值得注意的点（好的或需要改善的），每条一句话。
3. **反思问题**（reflection）: 给出1-2个引导用户反思的问题，帮助用户改善明天的状态。

请直接输出 JSON 格式：
{{
  "narrative": "...",
  "highlights": ["...", "..."],
  "reflection": "..."
}}

注意：只输出 JSON，不要输出其他内容。"""
    return prompt


def call_llm_for_summary(prompt: str) -> Optional[Dict[str, Any]]:
    """
    调用 Summarizer Agent（Qwen2.5-72B-Instruct）生成晚间总结。

    Returns:
        解析后的 JSON dict，或 None（调用失败时）
    """
    try:
        from attention.core.agents import call_agent_json
        parsed = call_agent_json(
            "summarizer",
            prompt,
            max_tokens=1000,
            temperature=0.7,
            timeout=30,
        )
        logger.info("LLM 晚间总结生成成功")
        return parsed

    except json.JSONDecodeError as e:
        logger.error(f"LLM 返回内容解析失败: {e}")
        return None
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return None


# ============================================================
# 数据结构
# ============================================================

@dataclass
class CheckinEntry:
    """单条签到记录"""
    id: str = ""
    timestamp: str = ""
    hour: int = 0                    # 0-23
    doing: str = ""                  # 用户输入：在做什么
    feeling: str = "normal"          # 感受: great / good / normal / tired / bad
    category: str = "work"           # 自动推断或用户选择的类别
    skipped: bool = False            # 是否跳过
    auto_app: str = ""               # 签到时自动采集的当前应用
    auto_title: str = ""             # 签到时自动采集的窗口标题

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
    interval_minutes: int = 60       # 签到间隔（分钟）
    start_hour: int = 9              # 几点开始签到
    end_hour: int = 23               # 几点结束签到
    sound_enabled: bool = True       # 播放提示音
    evening_summary_hour: int = 22   # 几点生成晚间总结
    skip_if_idle: bool = True        # 空闲时跳过
    idle_threshold: int = 300        # 空闲阈值（秒）

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CheckinSettings":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class EveningSummary:
    """晚间总结"""
    date: str = ""
    generated_at: str = ""
    total_checkins: int = 0
    skipped_checkins: int = 0
    entries: List[Dict] = field(default_factory=list)
    category_breakdown: Dict[str, int] = field(default_factory=dict)
    feeling_breakdown: Dict[str, int] = field(default_factory=dict)
    timeline_narrative: str = ""     # 一段文字总结
    highlights: List[str] = field(default_factory=list)
    reflection_prompt: str = ""      # 引导反思的问题

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ============================================================
# 弹窗实现（跨平台）
# ============================================================

# 感受选项
FEELING_OPTIONS = {
    "great": "🔥 状态极佳",
    "good":  "😊 不错",
    "normal": "😐 一般",
    "tired": "😴 有点累",
    "bad":   "😫 很差",
}

# 类别关键词映射
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


def infer_category(text: str) -> str:
    """根据用户输入推断类别"""
    text_lower = text.lower()
    for keyword, category in CATEGORY_KEYWORDS.items():
        if keyword in text_lower:
            return category
    return "other"


def show_checkin_dialog_macos() -> Optional[Dict[str, str]]:
    """macOS: AppleScript 签到弹窗"""
    # 第一步：询问过去一小时在做什么
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

        # 第二步：询问感受
        script_feeling = '''
        tell application "System Events"
            activate
            set chosen to choose from list {"🔥 状态极佳", "😊 不错", "😐 一般", "😴 有点累", "😫 很差"} with title "过去一小时的状态" with prompt "过去一小时你感觉怎么样？" default items {"😐 一般"} OK button name "确定" cancel button name "跳过"
            if chosen is false then
                return "normal"
            else
                return item 1 of chosen
            end if
        end tell
        '''
        result2 = subprocess.run(
            ['osascript', '-e', script_feeling],
            capture_output=True, text=True, timeout=30
        )
        feeling_text = result2.stdout.strip()

        # 映射回标识符
        feeling_map = {
            "🔥 状态极佳": "great",
            "😊 不错": "good",
            "😐 一般": "normal",
            "😴 有点累": "tired",
            "😫 很差": "bad",
        }
        feeling = feeling_map.get(feeling_text, "normal")

        return {"skipped": "false", "doing": doing_text.strip(), "feeling": feeling}

    except subprocess.TimeoutExpired:
        return {"skipped": "true", "doing": "", "feeling": "normal"}
    except Exception as e:
        logger.error(f"macOS签到弹窗失败: {e}")
        return None


def show_checkin_dialog_windows() -> Optional[Dict[str, str]]:
    """Windows: 使用 tkinter 对话框"""
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

        if not doing:
            root.destroy()
            return {"skipped": "true", "doing": "", "feeling": "normal"}

        # 简单的感受选择
        import tkinter.messagebox as mb
        feel = mb.askquestion("过去一小时的状态", "你这一小时状态不错吗？", parent=root)
        feeling = "good" if feel == "yes" else "normal"

        root.destroy()
        return {"skipped": "false", "doing": doing.strip(), "feeling": feeling}

    except Exception as e:
        logger.error(f"Windows签到弹窗失败: {e}")
        return None


def show_checkin_dialog_linux() -> Optional[Dict[str, str]]:
    """Linux: zenity 弹窗"""
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

        # 感受
        result2 = subprocess.run(
            ['zenity', '--list', '--title=过去一小时的状态',
             '--text=过去一小时你感觉怎么样？',
             '--column=感受',
             '🔥 状态极佳', '😊 不错', '😐 一般', '😴 有点累', '😫 很差',
             '--timeout=30'],
            capture_output=True, text=True, timeout=35
        )
        feeling_map = {
            "🔥 状态极佳": "great", "😊 不错": "good", "😐 一般": "normal",
            "😴 有点累": "tired", "😫 很差": "bad"
        }
        feeling = feeling_map.get(result2.stdout.strip(), "normal")

        return {"skipped": "false", "doing": doing, "feeling": feeling}

    except FileNotFoundError:
        logger.warning("zenity 未安装")
        return None
    except Exception as e:
        logger.error(f"Linux签到弹窗失败: {e}")
        return None


def show_checkin_dialog() -> Optional[Dict[str, str]]:
    """跨平台签到弹窗"""
    if SYSTEM == "Darwin":
        return show_checkin_dialog_macos()
    elif SYSTEM == "Windows":
        return show_checkin_dialog_windows()
    elif SYSTEM == "Linux":
        return show_checkin_dialog_linux()
    return None


def play_checkin_sound():
    """播放签到提示音"""
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
    """加载指定日期的签到数据"""
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
# 晚间总结生成
# ============================================================

FEELING_LABELS = {
    "great": "🔥 极佳", "good": "😊 不错",
    "normal": "😐 一般", "tired": "😴 疲惫", "bad": "😫 很差"
}

CATEGORY_LABELS = {
    "coding": "💻 编程", "writing": "✍️ 写作", "meeting": "🤝 会议",
    "learning": "📚 学习", "reading": "📖 阅读",
    "communication": "💬 沟通", "rest": "☕ 休息",
    "entertainment": "🎮 娱乐", "exercise": "🏃 运动",
    "meal": "🍜 用餐", "other": "📌 其他", "work": "💼 工作",
}


def generate_evening_summary(date_str: Optional[str] = None, use_llm: bool = True) -> Optional[EveningSummary]:
    """
    生成晚间总结

    融合签到数据，生成一天的叙事总结和反思提示。
    当 use_llm=True 时，会调用大语言模型生成更有洞察力的总结内容。
    LLM 调用失败时自动 fallback 到本地模板生成。
    """
    if date_str is None:
        date_str = datetime.now().strftime("%Y-%m-%d")

    entries = load_entries_by_date(date_str)
    if not entries:
        return None

    # 基础统计
    actual = [e for e in entries if not e.skipped]
    skipped = [e for e in entries if e.skipped]

    # 类别分布
    cat_counts: Dict[str, int] = {}
    for e in actual:
        cat_counts[e.category] = cat_counts.get(e.category, 0) + 1

    # 感受分布
    feel_counts: Dict[str, int] = {}
    for e in actual:
        feel_counts[e.feeling] = feel_counts.get(e.feeling, 0) + 1

    # 尝试调用 LLM 生成智能总结
    llm_result = None
    if use_llm and actual:
        try:
            prompt = _build_summary_prompt(entries, cat_counts, feel_counts, date_str)
            llm_result = call_llm_for_summary(prompt)
        except Exception as e:
            logger.warning(f"LLM 总结生成失败，使用本地模板: {e}")

    # 时间线叙事（本地生成，作为基础数据）
    narrative_parts = []
    for e in entries:
        time_str = e.timestamp.split(" ")[1][:5] if " " in e.timestamp else f"{e.hour}:00"
        if e.skipped:
            narrative_parts.append(f"{time_str} — (跳过)")
        else:
            feel_icon = FEELING_LABELS.get(e.feeling, "")
            cat_icon = CATEGORY_LABELS.get(e.category, "")
            narrative_parts.append(f"{time_str} — {e.doing}  [{cat_icon}] {feel_icon}")

    local_narrative = "\n".join(narrative_parts)

    # 本地高光时刻
    local_highlights = []
    great_moments = [e for e in actual if e.feeling == "great"]
    if great_moments:
        local_highlights.append(f"🔥 你在 {', '.join(e.timestamp.split(' ')[1][:5] for e in great_moments)} 状态极佳")
    if cat_counts:
        top_cat = max(cat_counts, key=cat_counts.get)
        top_label = CATEGORY_LABELS.get(top_cat, top_cat)
        local_highlights.append(f"⏱ 最多时间花在了「{top_label}」上 ({cat_counts[top_cat]} 次签到)")
    tired_moments = [e for e in actual if e.feeling in ("tired", "bad")]
    if len(tired_moments) >= 2:
        local_highlights.append(f"⚠️ 有 {len(tired_moments)} 个时段感到疲惫，注意休息")

    # 本地反思提示
    local_prompts = _generate_reflection_prompt(actual, cat_counts, feel_counts)

    # 融合 LLM 结果与本地结果
    if llm_result:
        # LLM 成功，使用 LLM 生成的叙事，并保留本地时间线作为详细数据
        narrative = llm_result.get("narrative", local_narrative)
        # 时间线详情 + LLM 叙事
        full_narrative = f"{narrative}\n\n📋 详细时间线:\n{local_narrative}"
        highlights = llm_result.get("highlights", local_highlights)
        if isinstance(highlights, str):
            highlights = [highlights]
        reflection = llm_result.get("reflection", local_prompts)
    else:
        full_narrative = local_narrative
        highlights = local_highlights
        reflection = local_prompts

    summary = EveningSummary(
        date=date_str,
        generated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        total_checkins=len(entries),
        skipped_checkins=len(skipped),
        entries=[e.to_dict() for e in entries],
        category_breakdown=cat_counts,
        feeling_breakdown=feel_counts,
        timeline_narrative=full_narrative,
        highlights=highlights,
        reflection_prompt=reflection,
    )

    # 保存
    _save_summary(summary)

    return summary


def _generate_reflection_prompt(
    entries: List[CheckinEntry],
    cat_counts: Dict[str, int],
    feel_counts: Dict[str, int]
) -> str:
    """生成引导反思的问题"""
    prompts = []

    # 根据感受分布
    total = len(entries)
    if total == 0:
        return "今天没有签到记录，明天试试每小时记录一下自己在做什么吧。"

    good_ratio = (feel_counts.get("great", 0) + feel_counts.get("good", 0)) / total
    bad_ratio = (feel_counts.get("tired", 0) + feel_counts.get("bad", 0)) / total

    if good_ratio > 0.6:
        prompts.append("今天整体状态不错！是什么让你保持了好状态？能否把这种条件复制到明天？")
    elif bad_ratio > 0.4:
        prompts.append("今天似乎有些累。是睡眠不足、任务太重、还是其他原因？明天可以怎样调整？")
    else:
        prompts.append("今天状态起伏不大。回顾一下，有哪个时段你觉得特别投入？那个时候你在做什么？")

    # 根据类别分布
    entertainment_count = cat_counts.get("entertainment", 0) + cat_counts.get("rest", 0)
    if entertainment_count >= 3:
        prompts.append("今天休闲娱乐的时间不少，是计划内的放松还是不自觉的？")

    coding_count = cat_counts.get("coding", 0) + cat_counts.get("work", 0)
    if coding_count >= 5:
        prompts.append("今天深度工作的时间很长，记得适当休息。明天最重要的一件事是什么？")

    return "\n".join(prompts)


def _save_summary(summary: EveningSummary):
    ensure_dirs()
    fp = SUMMARY_DIR / f"summary_{summary.date}.json"
    try:
        with open(fp, 'w', encoding='utf-8') as f:
            json.dump(summary.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"晚间总结已保存: {fp}")
    except Exception as e:
        logger.error(f"保存晚间总结失败: {e}")


def get_summary_by_date(date_str: str) -> Optional[Dict[str, Any]]:
    """获取指定日期的晚间总结"""
    fp = SUMMARY_DIR / f"summary_{date_str}.json"
    if not fp.exists():
        return None
    try:
        with open(fp, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None


def get_latest_summary() -> Optional[Dict[str, Any]]:
    """获取最新的晚间总结"""
    ensure_dirs()
    files = sorted(SUMMARY_DIR.glob("summary_*.json"), reverse=True)
    if files:
        try:
            with open(files[0], 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return None


# ============================================================
# 签到管理器
# ============================================================

class HourlyCheckin:
    """每小时签到管理器"""

    def __init__(self, settings: Optional[CheckinSettings] = None):
        self.settings = settings or CheckinSettings()
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        self._next_checkin: Optional[datetime] = None
        self._showing_dialog = False
        self._summary_generated_today = False

        # 回调
        self._on_checkin: Optional[Callable] = None

        # 统计
        self.stats = {
            "checkins_today": 0,
            "skipped_today": 0,
        }

        # 配置持久化
        self.settings_file = Config.DATA_DIR / "checkin_settings.json"
        self._load_settings()
        self._sync_stats()

    def _load_settings(self):
        if self.settings_file.exists():
            try:
                with open(self.settings_file, 'r', encoding='utf-8') as f:
                    self.settings = CheckinSettings.from_dict(json.load(f))
                logger.info(f"已加载签到设置: 间隔{self.settings.interval_minutes}分钟")
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
        """同步今日统计"""
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
        """计算下一次签到时间"""
        now = datetime.now()
        # 对齐到下一个整点（或按间隔计算）
        interval = self.settings.interval_minutes
        if interval >= 60:
            # 整点模式：下一个整点
            next_hour = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            self._next_checkin = next_hour
        else:
            # 非整点模式
            self._next_checkin = now + timedelta(minutes=interval)

        # 确保在活跃时段内
        if self._next_checkin.hour < self.settings.start_hour:
            self._next_checkin = self._next_checkin.replace(
                hour=self.settings.start_hour, minute=0, second=0
            )
        elif self._next_checkin.hour >= self.settings.end_hour:
            # 推迟到明天
            tomorrow = self._next_checkin + timedelta(days=1)
            self._next_checkin = tomorrow.replace(
                hour=self.settings.start_hour, minute=0, second=0
            )

        logger.info(f"下次签到: {self._next_checkin.strftime('%H:%M:%S')}")

    def _checkin_loop(self):
        while self._running:
            now = datetime.now()

            # 检查是否到签到时间
            if (self._next_checkin and now >= self._next_checkin
                    and not self._showing_dialog):
                current_hour = now.hour
                if self.settings.start_hour <= current_hour < self.settings.end_hour:
                    # 检查空闲
                    if self.settings.skip_if_idle and self._is_user_idle():
                        logger.debug("用户空闲，跳过签到")
                        self._schedule_next()
                    else:
                        self._next_checkin = None
                        self._do_checkin()
                else:
                    self._schedule_next()

            # 检查晚间总结
            if (not self._summary_generated_today
                    and now.hour >= self.settings.evening_summary_hour):
                self._generate_evening_summary()

            # 日期切换重置
            if now.hour < self.settings.start_hour:
                self._summary_generated_today = False

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
        """执行签到"""
        self._showing_dialog = True
        logger.info("触发每小时签到...")

        # 播放提示音
        if self.settings.sound_enabled:
            play_checkin_sound()

        # 采集当前应用
        auto_app, auto_title = self._get_current_app()

        try:
            result = show_checkin_dialog()

            if result is None:
                # 弹窗失败
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
                logger.info(f"签到完成: {entry.doing} [{entry.category}] ({entry.feeling})")

            # 保存
            entries = _load_today_entries()
            entries.append(entry)
            _save_today_entries(entries)

            # 回调
            if self._on_checkin:
                self._on_checkin(entry.to_dict())

        except Exception as e:
            logger.error(f"签到异常: {e}")
        finally:
            self._showing_dialog = False
            self._schedule_next()

    def _get_current_app(self) -> tuple:
        """获取当前活跃应用"""
        try:
            from attention.core.activity_monitor import get_activity_monitor
            monitor = get_activity_monitor()
            snap = monitor.get_latest_snapshot()
            if snap:
                return (snap.active_window_app, snap.active_window_title[:80])
        except Exception:
            pass
        return ("", "")

    def _generate_evening_summary(self):
        """生成晚间总结（调用 LLM）"""
        today = datetime.now().strftime("%Y-%m-%d")
        existing = get_summary_by_date(today)
        if existing:
            self._summary_generated_today = True
            return

        logger.info("正在生成晚间总结（调用 LLM）...")
        summary = generate_evening_summary(today, use_llm=True)
        self._summary_generated_today = True

        if summary:
            logger.info(f"晚间总结已生成: {summary.total_checkins} 条签到")
            # 弹窗通知
            self._show_summary_notification(summary)

    def _show_summary_notification(self, summary: EveningSummary):
        """弹窗展示晚间总结摘要"""
        actual = summary.total_checkins - summary.skipped_checkins
        msg = f"今日签到 {actual} 次"
        if summary.highlights:
            msg += f"\n\n{summary.highlights[0]}"

        try:
            if SYSTEM == "Darwin":
                script = f'''
                display notification "{msg}" with title "🌙 Attention OS · 今日回顾" sound name "Glass"
                '''
                subprocess.Popen(
                    ['osascript', '-e', script],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
            elif SYSTEM == "Linux":
                subprocess.Popen(
                    ['notify-send', '🌙 今日回顾', msg],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )
        except Exception:
            pass

    # ==================== 公开 API ====================

    def trigger_now(self):
        """手动触发签到（测试用）"""
        if not self._showing_dialog:
            threading.Thread(target=self._do_checkin, daemon=True).start()

    def add_entry_from_web(self, doing: str, feeling: str = "normal") -> CheckinEntry:
        """从 Web 端手动添加签到（不弹窗）"""
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


# ============================================================
# 测试
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    print("测试每小时签到弹窗...")
    result = show_checkin_dialog()
    print(f"结果: {result}")

    if result and result.get("skipped") != "true":
        entry = CheckinEntry(
            doing=result["doing"],
            feeling=result["feeling"],
            category=infer_category(result["doing"]),
        )
        print(f"\n签到记录:")
        print(f"  内容: {entry.doing}")
        print(f"  感受: {entry.feeling}")
        print(f"  类别: {entry.category}")
