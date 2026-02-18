"""
todo_manager.py 单元测试

覆盖范围：
  1. 数据结构 (TodoItem)
  2. CRUD 操作 (add / update / toggle / delete / get_all / get_stats)
  3. 本地自然语言解析 (parse_todo_local, 日期/优先级/标签/标题清理)
  4. LLM 解析 (parse_todo_with_llm, mock 网络)
  5. 统一入口 (parse_natural_language_todo, LLM + fallback)
  6. 智能添加 (smart_add)
  7. 边界情况

运行:  python3 -m unittest test_todo_manager -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from unittest.mock import patch, MagicMock

# ================================================================
# 环境准备
# ================================================================

_tmpdir = tempfile.mkdtemp(prefix="todo_test_")


class _FakeConfig:
    DATA_DIR = Path(_tmpdir) / "data"
    BASE_DIR = Path(_tmpdir)

    @classmethod
    def ensure_dirs(cls):
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)


sys.modules.setdefault("dotenv", MagicMock())
sys.modules["attention.config"] = MagicMock()
sys.modules["attention.config"].Config = _FakeConfig

import attention.features.todo_manager as tm

# 重定向文件路径
tm.TODO_FILE = _FakeConfig.DATA_DIR / "todos.json"


def _clean():
    if _FakeConfig.DATA_DIR.exists():
        shutil.rmtree(_FakeConfig.DATA_DIR)
    _FakeConfig.DATA_DIR.mkdir(parents=True, exist_ok=True)
    tm._manager = None


# ================================================================
# 1. 数据结构测试
# ================================================================

class TestTodoItem(unittest.TestCase):

    def test_auto_id(self):
        t = tm.TodoItem(id="", title="test")
        self.assertNotEqual(t.id, "")

    def test_auto_created_at(self):
        t = tm.TodoItem(id="x", title="test")
        self.assertIn(datetime.now().strftime("%Y-%m-%d"), t.created_at)

    def test_to_dict_basic(self):
        t = tm.TodoItem(id="abc", title="写代码", priority="high", tags=["工作"])
        d = t.to_dict()
        self.assertEqual(d["title"], "写代码")
        self.assertEqual(d["priority"], "high")
        self.assertEqual(d["tags"], ["工作"])
        self.assertFalse(d["is_overdue"])

    def test_to_dict_with_deadline(self):
        future = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        t = tm.TodoItem(id="x", title="test", deadline=future)
        d = t.to_dict()
        self.assertIn(d["days_until_deadline"], [2, 3])  # depends on time of day
        self.assertFalse(d["is_overdue"])
        self.assertIsNone(d["deadline_time"])

    def test_to_dict_overdue(self):
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        t = tm.TodoItem(id="x", title="test", deadline=yesterday)
        d = t.to_dict()
        self.assertTrue(d["is_overdue"])

    def test_to_dict_today_date_only_not_overdue(self):
        """今天的纯日期任务（无时间）不应该逾期，因为默认到 23:59:59"""
        today = datetime.now().strftime("%Y-%m-%d")
        t = tm.TodoItem(id="x", title="test", deadline=today)
        d = t.to_dict()
        self.assertFalse(d["is_overdue"])
        self.assertEqual(d["days_until_deadline"], 0)

    def test_to_dict_with_datetime(self):
        """包含时间的 deadline"""
        future_dt = (datetime.now() + timedelta(hours=2)).strftime("%Y-%m-%d %H:%M")
        t = tm.TodoItem(id="x", title="test", deadline=future_dt)
        d = t.to_dict()
        self.assertFalse(d["is_overdue"])
        self.assertIsNotNone(d["deadline_time"])

    def test_to_dict_past_time_today_is_overdue(self):
        """今天但已过的具体时间 → 逾期"""
        past_dt = (datetime.now() - timedelta(hours=1)).strftime("%Y-%m-%d %H:%M")
        t = tm.TodoItem(id="x", title="test", deadline=past_dt)
        d = t.to_dict()
        self.assertTrue(d["is_overdue"])

    def test_from_dict_round_trip(self):
        t = tm.TodoItem(id="x", title="hello", priority="urgent", tags=["工作", "学习"])
        restored = tm.TodoItem.from_dict(t.to_dict())
        self.assertEqual(restored.title, "hello")
        self.assertEqual(restored.priority, "urgent")
        self.assertEqual(restored.tags, ["工作", "学习"])

    def test_from_dict_ignores_extra(self):
        t = tm.TodoItem.from_dict({"id": "x", "title": "t", "extra": 123})
        self.assertEqual(t.title, "t")


# ================================================================
# 2. CRUD 测试
# ================================================================

class TestTodoManagerCRUD(unittest.TestCase):

    def setUp(self):
        _clean()

    def test_add(self):
        mgr = tm.TodoManager()
        t = mgr.add("写代码", priority="high", tags=["工作"])
        self.assertEqual(t.title, "写代码")
        self.assertEqual(t.priority, "high")
        self.assertEqual(t.tags, ["工作"])
        self.assertEqual(len(mgr.get_all()), 1)

    def test_add_persists(self):
        mgr = tm.TodoManager()
        mgr.add("task1")
        mgr2 = tm.TodoManager()
        self.assertEqual(len(mgr2.get_all()), 1)

    def test_toggle_complete(self):
        mgr = tm.TodoManager()
        t = mgr.add("task")
        mgr.toggle_complete(t.id)
        items = mgr.get_all()
        self.assertTrue(items[0]["completed"])

    def test_toggle_twice(self):
        mgr = tm.TodoManager()
        t = mgr.add("task")
        mgr.toggle_complete(t.id)
        mgr.toggle_complete(t.id)
        items = mgr.get_all()
        self.assertFalse(items[0]["completed"])

    def test_delete(self):
        mgr = tm.TodoManager()
        t = mgr.add("task")
        self.assertTrue(mgr.delete(t.id))
        self.assertEqual(len(mgr.get_all()), 0)

    def test_delete_nonexistent(self):
        mgr = tm.TodoManager()
        self.assertFalse(mgr.delete("nonexistent"))

    def test_update(self):
        mgr = tm.TodoManager()
        t = mgr.add("old title")
        mgr.update(t.id, title="new title", priority="urgent")
        items = mgr.get_all()
        self.assertEqual(items[0]["title"], "new title")
        self.assertEqual(items[0]["priority"], "urgent")

    def test_get_stats(self):
        mgr = tm.TodoManager()
        mgr.add("t1")
        t2 = mgr.add("t2")
        mgr.toggle_complete(t2.id)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        mgr.add("t3", deadline=yesterday)
        stats = mgr.get_stats()
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["completed"], 1)
        self.assertEqual(stats["pending"], 2)
        self.assertEqual(stats["overdue"], 1)

    def test_sort_order(self):
        mgr = tm.TodoManager()
        mgr.add("low", priority="low")
        mgr.add("urgent", priority="urgent")
        mgr.add("normal", priority="normal")
        items = mgr.get_all()
        self.assertEqual(items[0]["priority"], "urgent")
        self.assertEqual(items[-1]["priority"], "low")


# ================================================================
# 3. 本地自然语言解析测试
# ================================================================

class TestParseTimeFromText(unittest.TestCase):

    def test_colon_format(self):
        self.assertEqual(tm._parse_time_from_text("21:30开会"), "21:30")

    def test_colon_format_morning(self):
        self.assertEqual(tm._parse_time_from_text("9:00上班"), "09:00")

    def test_chinese_colon(self):
        self.assertEqual(tm._parse_time_from_text("21：30开会"), "21:30")

    def test_afternoon_dian(self):
        self.assertEqual(tm._parse_time_from_text("下午3点开会"), "15:00")

    def test_evening_dian_ban(self):
        self.assertEqual(tm._parse_time_from_text("晚上8点半"), "20:30")

    def test_morning_10(self):
        self.assertEqual(tm._parse_time_from_text("上午10点"), "10:00")

    def test_24h_dian(self):
        self.assertEqual(tm._parse_time_from_text("21点开会"), "21:00")

    def test_dian_fen(self):
        self.assertEqual(tm._parse_time_from_text("下午2点45分"), "14:45")

    def test_no_time(self):
        self.assertIsNone(tm._parse_time_from_text("写代码"))

    def test_ambiguous_small_hour(self):
        """1-7点无上下午标识时，推测为下午"""
        self.assertEqual(tm._parse_time_from_text("3点开会"), "15:00")


class TestParseDateFromText(unittest.TestCase):

    def test_today(self):
        result = tm._parse_date_from_text("今天完成")
        self.assertTrue(result.startswith(datetime.now().strftime("%Y-%m-%d")))

    def test_tomorrow(self):
        result = tm._parse_date_from_text("明天交报告")
        expected = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertTrue(result.startswith(expected))

    def test_day_after_tomorrow(self):
        result = tm._parse_date_from_text("后天开会")
        expected = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        self.assertTrue(result.startswith(expected))

    def test_n_days_later(self):
        result = tm._parse_date_from_text("3天后提交")
        expected = (datetime.now() + timedelta(days=3)).strftime("%Y-%m-%d")
        self.assertTrue(result.startswith(expected))

    def test_n_days_within(self):
        result = tm._parse_date_from_text("5天内完成")
        expected = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
        self.assertTrue(result.startswith(expected))

    def test_month_day(self):
        result = tm._parse_date_from_text("3月15日前完成")
        self.assertIsNotNone(result)
        self.assertIn("-03-15", result)

    def test_day_number(self):
        """"X号" 解析"""
        result = tm._parse_date_from_text("25号交作业")
        self.assertIsNotNone(result)
        self.assertIn("-25", result)

    def test_iso_date(self):
        result = tm._parse_date_from_text("2026-06-15 完成")
        self.assertTrue(result.startswith("2026-06-15"))

    def test_no_date(self):
        result = tm._parse_date_from_text("写代码")
        self.assertIsNone(result)

    def test_next_week(self):
        result = tm._parse_date_from_text("下周五提交")
        self.assertIsNotNone(result)
        d = datetime.strptime(result.split(" ")[0], "%Y-%m-%d")
        self.assertEqual(d.weekday(), 4)  # Friday
        self.assertGreater(d, datetime.now())

    def test_this_week(self):
        """本周X 解析"""
        result = tm._parse_date_from_text("周三开会")
        if result:
            d = datetime.strptime(result.split(" ")[0], "%Y-%m-%d")
            self.assertEqual(d.weekday(), 2)  # Wednesday

    # ---- 日期+时间 ----

    def test_today_with_time(self):
        """今天晚上21:30 → 今天日期 + 21:30"""
        result = tm._parse_date_from_text("今天晚上21:30开会")
        self.assertIsNotNone(result)
        self.assertIn(datetime.now().strftime("%Y-%m-%d"), result)
        self.assertIn("21:30", result)

    def test_tonight_with_time(self):
        """今晚8点 → 今天 + 20:00"""
        result = tm._parse_date_from_text("今晚8点开会")
        self.assertIsNotNone(result)
        self.assertIn(datetime.now().strftime("%Y-%m-%d"), result)
        self.assertIn("20:00", result)

    def test_tomorrow_with_time(self):
        """明天下午3点 → 明天 + 15:00"""
        result = tm._parse_date_from_text("明天下午3点开会")
        self.assertIsNotNone(result)
        tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertIn(tomorrow, result)
        self.assertIn("15:00", result)

    def test_time_only_assumes_today(self):
        """只有时间没有日期 → 假定今天"""
        result = tm._parse_date_from_text("21:30开组会")
        self.assertIsNotNone(result)
        self.assertIn(datetime.now().strftime("%Y-%m-%d"), result)
        self.assertIn("21:30", result)

    def test_date_without_time(self):
        """只有日期没有时间 → 不含时间部分"""
        result = tm._parse_date_from_text("明天交报告")
        self.assertIsNotNone(result)
        self.assertNotIn(":", result)


class TestInferPriority(unittest.TestCase):

    def test_urgent(self):
        for text in ["紧急修复bug", "ASAP发布", "马上处理"]:
            self.assertEqual(tm._infer_priority_from_text(text), "urgent", f"failed: {text}")

    def test_high(self):
        for text in ["重要任务", "高优先级", "优先处理"]:
            self.assertEqual(tm._infer_priority_from_text(text), "high", f"failed: {text}")

    def test_low(self):
        for text in ["不急", "有空再做", "低优先"]:
            self.assertEqual(tm._infer_priority_from_text(text), "low", f"failed: {text}")

    def test_normal(self):
        self.assertEqual(tm._infer_priority_from_text("写代码"), "normal")
        self.assertEqual(tm._infer_priority_from_text("开会"), "normal")


class TestInferTags(unittest.TestCase):

    def test_work_tags(self):
        tags = tm._infer_tags_from_text("完成项目代码review")
        self.assertIn("工作", tags)

    def test_study_tags(self):
        tags = tm._infer_tags_from_text("学习PyTorch教程")
        self.assertIn("学习", tags)

    def test_life_tags(self):
        tags = tm._infer_tags_from_text("买牙膏")
        self.assertIn("生活", tags)

    def test_meeting_tags(self):
        tags = tm._infer_tags_from_text("团队会议")
        self.assertIn("会议", tags)

    def test_health_tags(self):
        tags = tm._infer_tags_from_text("去健身房运动")
        self.assertIn("健康", tags)

    def test_multiple_tags(self):
        tags = tm._infer_tags_from_text("开会讨论项目代码")
        self.assertIn("工作", tags)
        self.assertIn("会议", tags)

    def test_no_tags(self):
        tags = tm._infer_tags_from_text("发呆")
        self.assertEqual(tags, [])


class TestCleanTitle(unittest.TestCase):

    def test_remove_date_phrase(self):
        title = tm._clean_title("明天完成报告")
        self.assertNotIn("明天", title)
        self.assertIn("完成报告", title)

    def test_remove_deadline(self):
        title = tm._clean_title("截止下周五提交论文")
        self.assertNotIn("截止", title)

    def test_remove_priority_word(self):
        title = tm._clean_title("紧急修复线上bug")
        self.assertNotIn("紧急", title)
        self.assertIn("修复", title)

    def test_plain_title_unchanged(self):
        title = tm._clean_title("写一篇博客文章")
        self.assertEqual(title, "写一篇博客文章")


class TestParseTodoLocal(unittest.TestCase):
    """本地规则引擎完整测试"""

    def test_full_parsing(self):
        result = tm.parse_todo_local("明天紧急完成项目代码review")
        self.assertIsNotNone(result["deadline"])
        self.assertEqual(result["priority"], "urgent")
        self.assertIn("工作", result["tags"])
        self.assertTrue(len(result["title"]) > 0)
        self.assertNotIn("明天", result["title"])

    def test_simple_task(self):
        result = tm.parse_todo_local("写代码")
        self.assertEqual(result["title"], "写代码")
        self.assertIsNone(result["deadline"])
        self.assertEqual(result["priority"], "normal")

    def test_with_deadline_only(self):
        result = tm.parse_todo_local("后天交作业")
        expected = (datetime.now() + timedelta(days=2)).strftime("%Y-%m-%d")
        self.assertEqual(result["deadline"], expected)

    def test_with_priority_only(self):
        result = tm.parse_todo_local("重要的设计文档")
        self.assertEqual(result["priority"], "high")

    def test_empty_input(self):
        result = tm.parse_todo_local("")
        self.assertEqual(result["title"], "")


# ================================================================
# 4. LLM 解析测试（mock 网络）
# ================================================================

class TestParseTodoWithLLM(unittest.TestCase):

    def _make_mock_session(self, content_str):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": content_str}}]
        }
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.post.return_value = mock_resp
        return mock_session

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_successful_parse(self):
        llm_json = json.dumps({
            "title": "完成项目报告",
            "deadline": "2026-02-10",
            "priority": "high",
            "tags": ["工作"]
        })
        mock_session = self._make_mock_session(llm_json)
        with patch("requests.Session", return_value=mock_session):
            result = tm.parse_todo_with_llm("明天完成项目报告，重要")
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "完成项目报告")
        self.assertEqual(result["priority"], "high")

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_markdown_wrapped_json(self):
        content = '```json\n{"title": "买菜", "deadline": null, "priority": "low", "tags": ["生活"]}\n```'
        mock_session = self._make_mock_session(content)
        with patch("requests.Session", return_value=mock_session):
            result = tm.parse_todo_with_llm("有空买菜")
        self.assertIsNotNone(result)
        self.assertEqual(result["title"], "买菜")

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": ""})
    def test_no_api_key(self):
        result = tm.parse_todo_with_llm("test")
        self.assertIsNone(result)

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_invalid_json(self):
        mock_session = self._make_mock_session("这不是JSON")
        with patch("requests.Session", return_value=mock_session):
            result = tm.parse_todo_with_llm("test")
        self.assertIsNone(result)

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_missing_title(self):
        """LLM 返回没有 title 的 JSON"""
        llm_json = json.dumps({"deadline": "2026-03-01", "priority": "normal"})
        mock_session = self._make_mock_session(llm_json)
        with patch("requests.Session", return_value=mock_session):
            result = tm.parse_todo_with_llm("test")
        self.assertIsNone(result)

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_timeout(self):
        import requests as real_requests
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.post.side_effect = real_requests.exceptions.Timeout()
        with patch("requests.Session", return_value=mock_session):
            result = tm.parse_todo_with_llm("test")
        self.assertIsNone(result)

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_normalizes_invalid_priority(self):
        llm_json = json.dumps({"title": "task", "priority": "super_high", "tags": "工作"})
        mock_session = self._make_mock_session(llm_json)
        with patch("requests.Session", return_value=mock_session):
            result = tm.parse_todo_with_llm("task")
        self.assertIsNotNone(result)
        self.assertEqual(result["priority"], "normal")
        # tags as string should be converted to list
        self.assertEqual(result["tags"], ["工作"])


class TestBuildTodoParsePrompt(unittest.TestCase):

    def test_contains_text(self):
        prompt = tm._build_todo_parse_prompt("明天写报告")
        self.assertIn("明天写报告", prompt)

    def test_contains_today_date(self):
        prompt = tm._build_todo_parse_prompt("test")
        self.assertIn(datetime.now().strftime("%Y-%m-%d"), prompt)

    def test_contains_json_instruction(self):
        prompt = tm._build_todo_parse_prompt("test")
        self.assertIn("JSON", prompt)
        self.assertIn("title", prompt)
        self.assertIn("deadline", prompt)


# ================================================================
# 5. 统一入口测试
# ================================================================

class TestParseNaturalLanguageTodo(unittest.TestCase):

    def test_llm_success(self):
        llm_result = {"title": "LLM解析", "deadline": "2026-03-01", "priority": "high", "tags": ["工作"]}
        with patch("todo_manager.parse_todo_with_llm", return_value=llm_result):
            result = tm.parse_natural_language_todo("明天写报告", use_llm=True)
        self.assertEqual(result["title"], "LLM解析")

    def test_llm_failure_fallback(self):
        with patch("todo_manager.parse_todo_with_llm", return_value=None):
            result = tm.parse_natural_language_todo("明天写代码", use_llm=True)
        # Fallback to local
        self.assertIsNotNone(result["deadline"])
        self.assertIn("工作", result["tags"])

    def test_llm_exception_fallback(self):
        with patch("todo_manager.parse_todo_with_llm", side_effect=Exception("boom")):
            result = tm.parse_natural_language_todo("写代码", use_llm=True)
        self.assertEqual(result["title"], "写代码")

    def test_use_llm_false(self):
        with patch("todo_manager.parse_todo_with_llm") as mock_llm:
            result = tm.parse_natural_language_todo("写代码", use_llm=False)
            mock_llm.assert_not_called()
        self.assertEqual(result["title"], "写代码")

    def test_empty_input(self):
        result = tm.parse_natural_language_todo("")
        self.assertEqual(result["title"], "")
        self.assertIsNone(result["deadline"])


# ================================================================
# 6. 智能添加测试
# ================================================================

class TestSmartAdd(unittest.TestCase):

    def setUp(self):
        _clean()

    def test_smart_add_with_local(self):
        mgr = tm.TodoManager()
        result = mgr.smart_add("明天紧急完成代码review", use_llm=False)
        self.assertIn("todo", result)
        self.assertIn("parsed", result)
        self.assertEqual(result["original_text"], "明天紧急完成代码review")
        todo = result["todo"]
        self.assertTrue(len(todo["title"]) > 0)
        self.assertIsNotNone(todo["deadline"])
        self.assertEqual(todo["priority"], "urgent")
        # Verify persisted
        self.assertEqual(len(mgr.get_all()), 1)

    def test_smart_add_with_llm(self):
        mgr = tm.TodoManager()
        llm_result = {"title": "完成代码审查", "deadline": "2026-02-10", "priority": "high", "tags": ["工作"]}
        with patch("todo_manager.parse_todo_with_llm", return_value=llm_result):
            result = mgr.smart_add("明天完成代码审查，重要", use_llm=True)
        self.assertEqual(result["todo"]["title"], "完成代码审查")
        self.assertEqual(result["todo"]["priority"], "high")

    def test_smart_add_empty_title_fallback(self):
        """如果解析结果 title 为空，使用原始文本"""
        mgr = tm.TodoManager()
        with patch("todo_manager.parse_natural_language_todo", return_value={"title": "", "deadline": None, "priority": "normal", "tags": []}):
            result = mgr.smart_add("something weird", use_llm=False)
        self.assertEqual(result["todo"]["title"], "something weird")

    def test_smart_add_multiple(self):
        mgr = tm.TodoManager()
        mgr.smart_add("买牛奶", use_llm=False)
        mgr.smart_add("写代码", use_llm=False)
        mgr.smart_add("开会", use_llm=False)
        self.assertEqual(len(mgr.get_all()), 3)


# ================================================================
# 7. 边界情况
# ================================================================

class TestEdgeCases(unittest.TestCase):

    def setUp(self):
        _clean()

    def test_unicode_task(self):
        mgr = tm.TodoManager()
        t = mgr.add("写代码 ✅ 🎉", tags=["测试"])
        items = mgr.get_all()
        self.assertIn("✅", items[0]["title"])

    def test_very_long_input(self):
        long_text = "完成" * 500 + "代码"
        result = tm.parse_todo_local(long_text)
        self.assertTrue(len(result["title"]) > 0)

    def test_special_chars(self):
        result = tm.parse_todo_local('写代码 "hello" <script> & 🎉')
        self.assertTrue(len(result["title"]) > 0)

    def test_only_date(self):
        result = tm.parse_todo_local("明天")
        self.assertIsNotNone(result["deadline"])

    def test_only_priority(self):
        result = tm.parse_todo_local("紧急")
        self.assertEqual(result["priority"], "urgent")

    def test_smart_add_preserves_tags(self):
        mgr = tm.TodoManager()
        result = mgr.smart_add("去健身房运动", use_llm=False)
        self.assertIn("健康", result["todo"]["tags"])


# ================================================================
# 运行
# ================================================================

if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(_tmpdir, ignore_errors=True)
