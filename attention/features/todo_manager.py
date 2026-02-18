"""
TodoList 模块
任务管理：支持CRUD、截止日期、优先级、完成状态
支持自然语言/语音输入，通过 LLM 智能解析为结构化任务
数据持久化到本地JSON
"""
import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
from pathlib import Path
from dataclasses import dataclass, asdict

from attention.config import Config

logger = logging.getLogger(__name__)

TODO_FILE = Config.DATA_DIR / "todos.json"


# ============================================================
# 自然语言解析 — 本地规则引擎（作为 LLM 的 fallback）
# ============================================================

# 优先级关键词
PRIORITY_KEYWORDS = {
    "urgent": ["紧急", "urgent", "立刻", "马上", "ASAP", "尽快", "火急"],
    "high":   ["重要", "高优先", "high", "优先", "关键"],
    "low":    ["低优先", "不急", "有空再", "low", "闲了", "以后"],
}

# 标签关键词
TAG_KEYWORDS = {
    "工作": ["工作", "项目", "需求", "上线", "部署", "代码", "开发", "bug", "review"],
    "学习": ["学习", "课程", "论文", "阅读", "看书", "教程", "研究"],
    "生活": ["买", "购物", "打扫", "预约", "挂号", "取件", "交费", "缴费", "水电"],
    "会议": ["会议", "开会", "讨论", "同步", "对齐", "meeting"],
    "健康": ["运动", "健身", "跑步", "体检", "看医生", "吃药"],
}


def _parse_time_from_text(text: str) -> Optional[str]:
    """从文本中提取时间，返回 HH:MM 或 None"""
    import re
    # "21:30" / "21：30" / "9:00"
    m = re.search(r"(\d{1,2})\s*[:：]\s*(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"

    # "下午3点" / "上午10点" / "晚上8点半" / "21点" / "今晚8点"
    m = re.search(r"(上午|早上|下午|晚上|傍晚|中午|今晚|今早|夜里?)?\s*(\d{1,2})\s*[点时](?:\s*(\d{1,2})\s*分?|半)?", text)
    if m:
        period = m.group(1) or ""
        h = int(m.group(2))
        mi = int(m.group(3)) if m.group(3) else 0
        if "半" in (m.group(0) or ""):
            mi = 30
        # 上下午转换
        if period in ("下午", "晚上", "傍晚", "今晚", "夜", "夜里") and h < 12:
            h += 12
        elif period in ("上午", "早上", "今早") and h == 12:
            h = 0
        # 如果没有上下午标识，且小时数 <= 7，推测为下午
        if not period and 1 <= h <= 7:
            h += 12
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return f"{h:02d}:{mi:02d}"

    return None


def _parse_date_from_text(text: str) -> Optional[str]:
    """从文本中解析日期（和可选时间），返回 YYYY-MM-DD 或 YYYY-MM-DD HH:MM"""
    import re
    now = datetime.now()

    date_str = None  # YYYY-MM-DD

    # === 解析日期部分 ===

    # "今天"
    if "今天" in text or "今晚" in text:
        date_str = now.strftime("%Y-%m-%d")
    # "明天"
    elif "明天" in text:
        date_str = (now + timedelta(days=1)).strftime("%Y-%m-%d")
    # "后天"
    elif "后天" in text:
        date_str = (now + timedelta(days=2)).strftime("%Y-%m-%d")
    # "大后天"
    elif "大后天" in text:
        date_str = (now + timedelta(days=3)).strftime("%Y-%m-%d")

    # "X天后" / "X天内"
    if date_str is None:
        m = re.search(r"(\d+)\s*天[后内以]", text)
        if m:
            date_str = (now + timedelta(days=int(m.group(1)))).strftime("%Y-%m-%d")

    # "下周X"
    weekday_map = {"一": 0, "二": 1, "三": 2, "四": 3, "五": 4, "六": 5, "日": 6, "天": 6}
    if date_str is None:
        m = re.search(r"下周([一二三四五六日天])", text)
        if m:
            target_wd = weekday_map[m.group(1)]
            days_ahead = (target_wd - now.weekday()) % 7
            if days_ahead == 0:
                days_ahead = 7
            # 确保是下周
            if days_ahead <= (6 - now.weekday()):
                days_ahead += 7
            date_str = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # "这周X" / "本周X" / "周X"
    if date_str is None:
        m = re.search(r"(?:这|本)?周([一二三四五六日天])", text)
        if m:
            target_wd = weekday_map[m.group(1)]
            days_ahead = (target_wd - now.weekday()) % 7
            date_str = (now + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    # "X月X日/号"
    if date_str is None:
        m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", text)
        if m:
            month, day = int(m.group(1)), int(m.group(2))
            try:
                d = datetime(now.year, month, day)
                if d.date() < now.date():
                    d = datetime(now.year + 1, month, day)
                date_str = d.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # "YYYY-MM-DD" 或 "YYYY/MM/DD"
    if date_str is None:
        m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
        if m:
            try:
                d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                date_str = d.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # "X号" (当月)
    if date_str is None:
        m = re.search(r"(\d{1,2})\s*[号日]", text)
        if m:
            day = int(m.group(1))
            try:
                d = datetime(now.year, now.month, day)
                if d.date() < now.date():
                    if now.month == 12:
                        d = datetime(now.year + 1, 1, day)
                    else:
                        d = datetime(now.year, now.month + 1, day)
                date_str = d.strftime("%Y-%m-%d")
            except ValueError:
                pass

    # === 解析时间部分 ===
    time_str = _parse_time_from_text(text)

    # === 合并 ===
    if date_str and time_str:
        return f"{date_str} {time_str}"
    elif date_str:
        return date_str
    elif time_str:
        # 有时间没日期 → 假定今天
        return f"{now.strftime('%Y-%m-%d')} {time_str}"
    return None


def _infer_priority_from_text(text: str) -> str:
    """从文本推断优先级（先检查更具体的关键词）"""
    text_lower = text.lower()
    # 按优先级顺序：先检查 urgent，再 low（避免"低优先"被"优先"匹配为 high）
    for priority in ("urgent", "low", "high"):
        keywords = PRIORITY_KEYWORDS[priority]
        for kw in keywords:
            if kw.lower() in text_lower:
                return priority
    return "normal"


def _infer_tags_from_text(text: str) -> List[str]:
    """从文本推断标签"""
    tags = []
    text_lower = text.lower()
    for tag, keywords in TAG_KEYWORDS.items():
        for kw in keywords:
            if kw.lower() in text_lower:
                if tag not in tags:
                    tags.append(tag)
                break
    return tags


def _clean_title(text: str) -> str:
    """从输入文本中清除日期/时间/优先级修饰词，提取干净的任务标题"""
    import re
    cleaned = text
    # 移除日期相关短语
    patterns = [
        r"截止[到]?\s*(?:今天|今晚|明天|后天|大后天|(?:下|这|本)?周[一二三四五六日天]|\d+\s*天[后内以]|\d{1,2}\s*月\s*\d{1,2}\s*[日号]?|\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}\s*[号日])",
        r"在?\s*\d{4}[-/]\d{1,2}[-/]\d{1,2}\s*[之前以前]*",
        r"在?\s*\d{1,2}\s*月\s*\d{1,2}\s*[日号]?\s*[之前以前]*",
        r"在?\s*(?:今天|今晚|明天|后天|大后天)\s*[之前以前]*",
        r"在?\s*(?:下|这|本)?周[一二三四五六日天]\s*[之前以前]*",
        r"在?\s*\d+\s*天[后内以]\s*",
        r"在?\s*\d{1,2}\s*[号日]\s*[之前以前]*",
        r"[，,]\s*(?:deadline)",
    ]
    # 移除时间相关短语
    time_patterns = [
        r"(?:上午|早上|下午|晚上|傍晚|中午)?\s*\d{1,2}\s*[:：]\s*\d{2}",      # 21:30 / 下午3:00
        r"(?:上午|早上|下午|晚上|傍晚|中午)\s*\d{1,2}\s*[点时]\s*(?:\d{1,2}\s*分?|半)?",  # 晚上8点半
        r"(?:上午|早上|下午|晚上|傍晚|中午)",  # 单独的时段词（如"今天晚上"中的"晚上"）
    ]
    for p in patterns + time_patterns:
        cleaned = re.sub(p, "", cleaned)
    # 移除优先级词
    for keywords in PRIORITY_KEYWORDS.values():
        for kw in keywords:
            cleaned = cleaned.replace(kw, "")
    # 清理多余标点和空格
    cleaned = re.sub(r"[，,。！\s]+$", "", cleaned)
    cleaned = re.sub(r"^[，,。！\s]+", "", cleaned)
    cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()
    return cleaned if cleaned else text.strip()


def parse_todo_local(text: str) -> Dict[str, Any]:
    """
    本地规则引擎解析自然语言任务输入。
    作为 LLM 的 fallback。
    """
    deadline = _parse_date_from_text(text)
    priority = _infer_priority_from_text(text)
    tags = _infer_tags_from_text(text)
    title = _clean_title(text)

    return {
        "title": title,
        "deadline": deadline,
        "priority": priority,
        "tags": tags,
    }


# ============================================================
# 自然语言解析 — LLM 增强
# ============================================================

def _build_todo_parse_prompt(text: str) -> str:
    """构建发送给 LLM 的待办解析 prompt"""
    today = datetime.now().strftime("%Y-%m-%d")
    weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday = weekday_names[datetime.now().weekday()]

    return f"""你是一个任务解析助手。请将用户的自然语言输入解析为结构化的待办事项。

当前时间：{today}（{weekday}）

用户输入："{text}"

请解析并输出 JSON 格式，包含以下字段：
- title: 任务的简洁标题（去掉时间、优先级等修饰词，只保留核心任务描述）
- deadline: 截止日期时间，如果有具体时间用 "YYYY-MM-DD HH:MM" 格式，只有日期用 "YYYY-MM-DD" 格式，如果没有提到就设为 null
- priority: 优先级，可选值 "urgent" / "high" / "normal" / "low"，根据语气和用词判断
- tags: 标签数组，从以下类别中选取：["工作", "学习", "生活", "会议", "健康"]，也可以为空数组

规则：
1. "明天" = {(datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")}
2. "后天" = {(datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")}
3. "下周X" 表示下一个周X的日期
4. 包含"紧急""马上""ASAP"等词 → priority = "urgent"
5. 包含"重要""优先"等词 → priority = "high"
6. 包含"不急""有空再"等词 → priority = "low"
7. 其他情况 → priority = "normal"

只输出 JSON，不要输出其他内容。"""


def parse_todo_with_llm(text: str) -> Optional[Dict[str, Any]]:
    """
    调用 Parser Agent（Qwen2.5-72B-Instruct）解析自然语言待办事项。

    Returns:
        解析后的 dict（含 title, deadline, priority, tags），或 None（调用失败）
    """
    prompt = _build_todo_parse_prompt(text)

    try:
        from attention.core.agents import call_agent_json
        parsed = call_agent_json(
            "parser",
            prompt,
            max_tokens=300,
            temperature=0.1,
        )

        # 校验必要字段
        if "title" not in parsed or not parsed["title"]:
            return None

        # 规范化
        parsed.setdefault("deadline", None)
        parsed.setdefault("priority", "normal")
        parsed.setdefault("tags", [])
        if parsed["priority"] not in ("urgent", "high", "normal", "low"):
            parsed["priority"] = "normal"
        if isinstance(parsed["tags"], str):
            parsed["tags"] = [parsed["tags"]]

        logger.info(f"LLM 解析待办成功: {parsed['title']}")
        return parsed

    except json.JSONDecodeError as e:
        logger.error(f"LLM 返回待办解析 JSON 失败: {e}")
        return None
    except Exception as e:
        logger.error(f"LLM 待办解析调用失败: {e}")
        return None


def parse_natural_language_todo(text: str, use_llm: bool = True) -> Dict[str, Any]:
    """
    解析自然语言输入为结构化待办事项。

    优先使用 LLM，失败时 fallback 到本地规则引擎。
    始终返回有效的结构化结果。

    Args:
        text: 用户的自然语言输入
        use_llm: 是否尝试使用 LLM

    Returns:
        dict with keys: title, deadline, priority, tags
    """
    text = text.strip()
    if not text:
        return {"title": "", "deadline": None, "priority": "normal", "tags": []}

    # 尝试 LLM
    if use_llm:
        try:
            llm_result = parse_todo_with_llm(text)
            if llm_result and llm_result.get("title"):
                return llm_result
        except Exception as e:
            logger.warning(f"LLM 待办解析异常，使用本地规则: {e}")

    # Fallback 到本地规则
    return parse_todo_local(text)


@dataclass
class TodoItem:
    """单条待办事项"""
    id: str
    title: str
    deadline: Optional[str] = None      # 格式: YYYY-MM-DD 或 YYYY-MM-DD HH:MM
    created_at: str = ""
    completed: bool = False
    completed_at: Optional[str] = None
    priority: str = "normal"            # low / normal / high / urgent
    tags: Optional[List[str]] = None

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())[:8]
        if not self.created_at:
            self.created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if self.tags is None:
            self.tags = []

    @staticmethod
    def _parse_deadline(deadline_str: str) -> Optional[datetime]:
        """解析 deadline 字符串为 datetime 对象"""
        if not deadline_str:
            return None
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(deadline_str, fmt)
            except ValueError:
                continue
        return None

    def _get_deadline_dt(self) -> Optional[datetime]:
        """获取截止 datetime，纯日期的默认为当天 23:59:59"""
        if not self.deadline:
            return None
        dt = self._parse_deadline(self.deadline)
        if dt is None:
            return None
        # 如果只有日期没有时间（即 HH:MM 为 00:00 且原字符串不含空格），
        # 则视为当天结束(23:59:59)
        if " " not in self.deadline:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        now = datetime.now()
        dl_dt = self._get_deadline_dt()
        if dl_dt:
            # days_until_deadline: 基于日期部分计算
            dl_date = dl_dt.date() if " " in (self.deadline or "") else datetime.strptime(self.deadline, "%Y-%m-%d").date()
            delta_days = (dl_date - now.date()).days
            d["days_until_deadline"] = delta_days
            # is_overdue: 只有真正过了截止时间才算逾期
            d["is_overdue"] = now > dl_dt
            # 提取时间部分供前端显示
            d["deadline_time"] = self.deadline.split(" ")[1] if " " in (self.deadline or "") else None
        else:
            d["days_until_deadline"] = None
            d["is_overdue"] = False
            d["deadline_time"] = None
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TodoItem":
        # 过滤掉计算属性
        valid_keys = cls.__dataclass_fields__.keys()
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class TodoManager:
    """待办事项管理器"""

    def __init__(self):
        self._todos: List[TodoItem] = []
        self._load()

    def _load(self):
        """从文件加载"""
        Config.ensure_dirs()
        if TODO_FILE.exists():
            try:
                with open(TODO_FILE, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self._todos = [TodoItem.from_dict(d) for d in data]
                logger.info(f"加载了 {len(self._todos)} 个待办事项")
            except Exception as e:
                logger.error(f"加载待办事项失败: {e}")
                self._todos = []
        else:
            self._todos = []

    def _save(self):
        """保存到文件"""
        Config.ensure_dirs()
        try:
            with open(TODO_FILE, 'w', encoding='utf-8') as f:
                json.dump(
                    [t.to_dict() for t in self._todos],
                    f, ensure_ascii=False, indent=2
                )
        except Exception as e:
            logger.error(f"保存待办事项失败: {e}")

    def add(self, title: str, deadline: Optional[str] = None,
            priority: str = "normal", tags: Optional[List[str]] = None) -> TodoItem:
        """添加待办事项"""
        todo = TodoItem(
            id="",
            title=title.strip(),
            deadline=deadline,
            priority=priority,
            tags=tags or [],
        )
        self._todos.append(todo)
        self._save()
        logger.info(f"添加待办: {todo.title} (截止: {todo.deadline}, 优先级: {todo.priority})")
        return todo

    def smart_add(self, text: str, use_llm: bool = True) -> Dict[str, Any]:
        """
        智能添加待办事项：接受自然语言输入，解析后创建任务。

        Args:
            text: 自然语言输入，如 "明天下午前完成项目报告，优先级高"
            use_llm: 是否使用 LLM 解析

        Returns:
            dict with keys: todo (创建的任务), parsed (解析结果)
        """
        parsed = parse_natural_language_todo(text, use_llm=use_llm)
        if not parsed.get("title"):
            parsed["title"] = text.strip()

        todo = self.add(
            title=parsed["title"],
            deadline=parsed.get("deadline"),
            priority=parsed.get("priority", "normal"),
            tags=parsed.get("tags", []),
        )
        return {
            "todo": todo.to_dict(),
            "parsed": parsed,
            "original_text": text,
        }

    def update(self, todo_id: str, **kwargs) -> Optional[TodoItem]:
        """更新待办事项"""
        for todo in self._todos:
            if todo.id == todo_id:
                for key, value in kwargs.items():
                    if hasattr(todo, key) and value is not None:
                        setattr(todo, key, value)
                self._save()
                return todo
        return None

    def toggle_complete(self, todo_id: str) -> Optional[TodoItem]:
        """切换完成状态"""
        for todo in self._todos:
            if todo.id == todo_id:
                todo.completed = not todo.completed
                todo.completed_at = (
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                    if todo.completed else None
                )
                self._save()
                return todo
        return None

    def delete(self, todo_id: str) -> bool:
        """删除待办事项"""
        original_len = len(self._todos)
        self._todos = [t for t in self._todos if t.id != todo_id]
        if len(self._todos) < original_len:
            self._save()
            return True
        return False

    def get_all(self, include_completed: bool = True) -> List[Dict[str, Any]]:
        """获取所有待办事项"""
        todos = self._todos
        if not include_completed:
            todos = [t for t in todos if not t.completed]

        # 排序：未完成在前，按优先级和截止日期排序
        priority_order = {"urgent": 0, "high": 1, "normal": 2, "low": 3}

        def sort_key(t):
            completed = 1 if t.completed else 0
            pri = priority_order.get(t.priority, 2)
            # 有截止日期的排在前面，越近的越靠前
            dl_dt = t._get_deadline_dt()
            if dl_dt:
                dl_score = (dl_dt - datetime.now()).total_seconds() / 86400
            else:
                dl_score = 9999
            return (completed, pri, dl_score)

        todos_sorted = sorted(todos, key=sort_key)
        return [t.to_dict() for t in todos_sorted]

    def get_stats(self) -> Dict[str, Any]:
        """获取待办统计"""
        total = len(self._todos)
        completed = sum(1 for t in self._todos if t.completed)
        now = datetime.now()
        overdue = 0
        due_today = 0
        for t in self._todos:
            if t.completed:
                continue
            dl_dt = t._get_deadline_dt()
            if dl_dt is None:
                continue
            if now > dl_dt:
                overdue += 1
            # 判断是否今天到期（日期部分是今天）
            dl_date_str = t.deadline.split(" ")[0] if t.deadline else ""
            if dl_date_str == now.strftime("%Y-%m-%d"):
                due_today += 1

        return {
            "total": total,
            "completed": completed,
            "pending": total - completed,
            "overdue": overdue,
            "due_today": due_today,
        }


# ==================== 单例 ====================

_manager: Optional[TodoManager] = None


def get_todo_manager() -> TodoManager:
    """获取 TodoManager 单例"""
    global _manager
    if _manager is None:
        _manager = TodoManager()
    return _manager
