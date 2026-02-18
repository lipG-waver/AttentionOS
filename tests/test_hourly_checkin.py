"""
hourly_checkin.py 单元测试 (unittest)

覆盖范围：
  1. 数据结构 (CheckinEntry, CheckinSettings, EveningSummary)
  2. 类别推断 (infer_category)
  3. 持久化 (load / save entries, load / save summary)
  4. 晚间总结生成 (generate_evening_summary, _generate_reflection_prompt)
  5. HourlyCheckin 管理器 (start / stop / schedule / settings / web checkin)
  6. 单例与模块级函数
  7. 边界情况与鲁棒性

运行:  python3 test_hourly_checkin.py
"""

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

# Ensure project root is in sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from unittest.mock import patch, MagicMock

# ================================================================
# 环境准备：mock config 让 DATA_DIR 指向临时目录
# ================================================================

_tmpdir = tempfile.mkdtemp(prefix="checkin_test_")


class _FakeConfig:
    DATA_DIR = Path(_tmpdir) / "data"
    BASE_DIR = Path(_tmpdir)

    @classmethod
    def ensure_dirs(cls):
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)


# 注入 mock 模块
sys.modules.setdefault("dotenv", MagicMock())
sys.modules["attention.config"] = MagicMock()
sys.modules["attention.config"].Config = _FakeConfig

# 被测模块
import attention.features.hourly_checkin as hc

# 让模块级路径指向临时目录
hc.CHECKIN_DIR = _FakeConfig.DATA_DIR / "checkins"
hc.SUMMARY_DIR = _FakeConfig.DATA_DIR / "evening_summaries"


# ================================================================
# 辅助工具
# ================================================================

def _clean_dirs():
    """清空临时数据目录"""
    for sub in ("checkins", "evening_summaries"):
        d = _FakeConfig.DATA_DIR / sub
        if d.exists():
            shutil.rmtree(d)
    hc.CHECKIN_DIR.mkdir(parents=True, exist_ok=True)
    hc.SUMMARY_DIR.mkdir(parents=True, exist_ok=True)


def _make_sample_entries():
    """构造一组典型签到条目"""
    base = "2026-02-02"
    return [
        hc.CheckinEntry(id="20260202090000", timestamp=f"{base} 09:00:00", hour=9,
                         doing="写晨间日记", feeling="good", category="writing"),
        hc.CheckinEntry(id="20260202100000", timestamp=f"{base} 10:00:00", hour=10,
                         doing="写代码实现新功能", feeling="great", category="coding"),
        hc.CheckinEntry(id="20260202110000", timestamp=f"{base} 11:00:00", hour=11,
                         doing="团队周会讨论", feeling="normal", category="meeting"),
        hc.CheckinEntry(id="20260202120000", timestamp=f"{base} 12:00:00", hour=12,
                         doing="午餐", feeling="good", category="meal"),
        hc.CheckinEntry(id="20260202130000", timestamp=f"{base} 13:00:00", hour=13,
                         doing="", feeling="normal", category="other", skipped=True),
        hc.CheckinEntry(id="20260202140000", timestamp=f"{base} 14:00:00", hour=14,
                         doing="debug一个诡异的bug", feeling="tired", category="coding"),
        hc.CheckinEntry(id="20260202150000", timestamp=f"{base} 15:00:00", hour=15,
                         doing="刷B站摸鱼", feeling="bad", category="entertainment"),
    ]


def _write_entries(entries, date_str="2026-02-02"):
    fp = hc.CHECKIN_DIR / f"checkin_{date_str}.json"
    with open(fp, 'w', encoding='utf-8') as f:
        json.dump([e.to_dict() for e in entries], f, ensure_ascii=False)


def _make_manager(**overrides):
    """创建一个干净的 HourlyCheckin 实例（不读磁盘配置）"""
    settings = hc.CheckinSettings(**overrides)
    mgr = hc.HourlyCheckin.__new__(hc.HourlyCheckin)
    mgr.settings = settings
    mgr._running = False
    mgr._thread = None
    mgr._lock = threading.Lock()
    mgr._next_checkin = None
    mgr._showing_dialog = False
    mgr._summary_generated_today = False
    mgr._on_checkin = None
    mgr.stats = {"checkins_today": 0, "skipped_today": 0}
    mgr.settings_file = _FakeConfig.DATA_DIR / "checkin_settings.json"
    return mgr


# ================================================================
# 1. 数据结构测试
# ================================================================

class TestCheckinEntry(unittest.TestCase):

    def setUp(self):
        _clean_dirs()
        hc._checkin = None

    def test_default_values_auto_populated(self):
        entry = hc.CheckinEntry(doing="test")
        self.assertNotEqual(entry.id, "")
        self.assertNotEqual(entry.timestamp, "")
        self.assertIsInstance(entry.hour, int)

    def test_explicit_values_preserved(self):
        entry = hc.CheckinEntry(
            id="custom_id", timestamp="2026-01-01 12:00:00", hour=12,
            doing="阅读论文", feeling="great", category="reading")
        self.assertEqual(entry.id, "custom_id")
        self.assertEqual(entry.hour, 12)
        self.assertEqual(entry.feeling, "great")

    def test_to_dict_round_trip(self):
        entry = hc.CheckinEntry(doing="写代码", feeling="good", category="coding")
        d = entry.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["doing"], "写代码")

        restored = hc.CheckinEntry.from_dict(d)
        self.assertEqual(restored.doing, entry.doing)
        self.assertEqual(restored.feeling, entry.feeling)

    def test_from_dict_ignores_extra_keys(self):
        data = {"doing": "hello", "feeling": "good", "unknown_field": 42}
        entry = hc.CheckinEntry.from_dict(data)
        self.assertEqual(entry.doing, "hello")
        self.assertNotIn("unknown_field", entry.to_dict())

    def test_skipped_default_false(self):
        entry = hc.CheckinEntry(doing="work")
        self.assertFalse(entry.skipped)


class TestCheckinSettings(unittest.TestCase):

    def test_defaults(self):
        s = hc.CheckinSettings()
        self.assertTrue(s.enabled)
        self.assertEqual(s.interval_minutes, 60)
        self.assertEqual(s.start_hour, 9)
        self.assertEqual(s.end_hour, 23)
        self.assertEqual(s.evening_summary_hour, 22)

    def test_round_trip(self):
        s = hc.CheckinSettings(interval_minutes=30, start_hour=8)
        s2 = hc.CheckinSettings.from_dict(s.to_dict())
        self.assertEqual(s2.interval_minutes, 30)
        self.assertEqual(s2.start_hour, 8)

    def test_from_dict_partial(self):
        s = hc.CheckinSettings.from_dict({"interval_minutes": 45})
        self.assertEqual(s.interval_minutes, 45)
        self.assertTrue(s.enabled)


class TestEveningSummaryDataclass(unittest.TestCase):

    def test_defaults(self):
        s = hc.EveningSummary()
        self.assertEqual(s.entries, [])
        self.assertEqual(s.category_breakdown, {})

    def test_to_dict(self):
        s = hc.EveningSummary(date="2026-02-02", total_checkins=5)
        d = s.to_dict()
        self.assertEqual(d["date"], "2026-02-02")
        self.assertEqual(d["total_checkins"], 5)


# ================================================================
# 2. 类别推断测试
# ================================================================

class TestInferCategory(unittest.TestCase):

    def test_coding_keywords(self):
        for text in ("写代码", "code review", "debug segfault", "编程中"):
            self.assertEqual(hc.infer_category(text), "coding", f"failed: {text}")

    def test_writing_keywords(self):
        for text in ("写论文", "文档整理", "做笔记"):
            self.assertEqual(hc.infer_category(text), "writing", f"failed: {text}")

    def test_meeting_keywords(self):
        for text in ("团队会议", "讨论需求", "meeting with PM", "开会"):
            self.assertEqual(hc.infer_category(text), "meeting", f"failed: {text}")

    def test_learning_keywords(self):
        for text in ("学习PyTorch", "看书", "在线课程", "教程"):
            self.assertEqual(hc.infer_category(text), "learning", f"failed: {text}")

    def test_reading_keywords(self):
        for text in ("阅读文章", "看新闻"):
            self.assertEqual(hc.infer_category(text), "reading", f"failed: {text}")

    def test_communication_keywords(self):
        for text in ("回邮件", "微信消息", "聊天"):
            self.assertEqual(hc.infer_category(text), "communication", f"failed: {text}")

    def test_entertainment_keywords(self):
        for text in ("刷B站", "bilibili", "看视频", "玩游戏"):
            self.assertEqual(hc.infer_category(text), "entertainment", f"failed: {text}")

    def test_rest_keywords(self):
        for text in ("休息一会", "摸鱼"):
            self.assertEqual(hc.infer_category(text), "rest", f"failed: {text}")

    def test_exercise_keywords(self):
        for text in ("去运动", "健身房锻炼"):
            self.assertEqual(hc.infer_category(text), "exercise", f"failed: {text}")

    def test_meal_keywords(self):
        for text in ("吃饭", "午餐", "点外卖", "晚餐"):
            self.assertEqual(hc.infer_category(text), "meal", f"failed: {text}")

    def test_unknown_returns_other(self):
        self.assertEqual(hc.infer_category("发呆"), "other")
        self.assertEqual(hc.infer_category("散步"), "other")

    def test_case_insensitive_english(self):
        self.assertEqual(hc.infer_category("CODE review"), "coding")
        self.assertEqual(hc.infer_category("MEETING notes"), "meeting")

    def test_empty_string(self):
        self.assertEqual(hc.infer_category(""), "other")


# ================================================================
# 3. 持久化测试
# ================================================================

class TestPersistence(unittest.TestCase):

    def setUp(self):
        _clean_dirs()
        hc._checkin = None

    def test_save_and_load_today(self):
        entries = [
            hc.CheckinEntry(doing="test1", feeling="good", category="coding"),
            hc.CheckinEntry(doing="test2", feeling="normal", category="writing"),
        ]
        hc._save_today_entries(entries)
        loaded = hc._load_today_entries()
        self.assertEqual(len(loaded), 2)
        self.assertEqual(loaded[0].doing, "test1")
        self.assertEqual(loaded[1].category, "writing")

    def test_load_empty_returns_list(self):
        self.assertEqual(hc._load_today_entries(), [])

    def test_load_entries_by_date(self):
        _write_entries([
            hc.CheckinEntry(id="1", timestamp="2026-02-02 10:00:00", hour=10,
                             doing="hello", feeling="good", category="coding"),
        ], "2026-02-02")
        entries = hc.load_entries_by_date("2026-02-02")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].doing, "hello")

    def test_load_entries_missing_date(self):
        self.assertEqual(hc.load_entries_by_date("1999-01-01"), [])

    def test_load_corrupted_file(self):
        fp = hc._get_today_file()
        fp.parent.mkdir(parents=True, exist_ok=True)
        with open(fp, 'w') as f:
            f.write("{bad json!!")
        self.assertEqual(hc._load_today_entries(), [])

    def test_summary_save_and_load(self):
        s = hc.EveningSummary(
            date="2026-02-02", generated_at="2026-02-02 22:00:00",
            total_checkins=5, highlights=["🔥 极佳"])
        hc._save_summary(s)
        loaded = hc.get_summary_by_date("2026-02-02")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["total_checkins"], 5)

    def test_get_summary_missing(self):
        self.assertIsNone(hc.get_summary_by_date("1999-12-31"))

    def test_get_latest_summary(self):
        for d in ("2026-02-01", "2026-02-02"):
            hc._save_summary(hc.EveningSummary(date=d, total_checkins=3))
        latest = hc.get_latest_summary()
        self.assertEqual(latest["date"], "2026-02-02")

    def test_get_latest_summary_empty(self):
        self.assertIsNone(hc.get_latest_summary())

    def test_unicode_persistence(self):
        entries = [
            hc.CheckinEntry(doing="调试 ✅", feeling="good", category="coding"),
            hc.CheckinEntry(doing="讨论 📞", feeling="normal", category="meeting"),
        ]
        hc._save_today_entries(entries)
        loaded = hc._load_today_entries()
        self.assertIn("✅", loaded[0].doing)
        self.assertIn("📞", loaded[1].doing)


# ================================================================
# 4. 晚间总结生成测试
# ================================================================

class TestEveningSummaryGeneration(unittest.TestCase):

    def setUp(self):
        _clean_dirs()
        hc._checkin = None

    def test_basic_summary(self):
        _write_entries(_make_sample_entries())
        s = hc.generate_evening_summary("2026-02-02", use_llm=False)
        self.assertIsNotNone(s)
        self.assertEqual(s.date, "2026-02-02")
        self.assertEqual(s.total_checkins, 7)
        self.assertEqual(s.skipped_checkins, 1)

    def test_category_breakdown(self):
        _write_entries(_make_sample_entries())
        s = hc.generate_evening_summary("2026-02-02", use_llm=False)
        self.assertEqual(s.category_breakdown["coding"], 2)
        self.assertEqual(s.category_breakdown.get("meal"), 1)

    def test_feeling_breakdown(self):
        _write_entries(_make_sample_entries())
        s = hc.generate_evening_summary("2026-02-02", use_llm=False)
        self.assertEqual(s.feeling_breakdown["good"], 2)
        self.assertIn("great", s.feeling_breakdown)

    def test_skipped_excluded_from_stats(self):
        _write_entries(_make_sample_entries())
        s = hc.generate_evening_summary("2026-02-02", use_llm=False)
        total = sum(s.category_breakdown.values())
        self.assertEqual(total, 6)

    def test_timeline_narrative(self):
        _write_entries(_make_sample_entries())
        s = hc.generate_evening_summary("2026-02-02", use_llm=False)
        self.assertIn("09:00", s.timeline_narrative)
        self.assertIn("写晨间日记", s.timeline_narrative)
        self.assertIn("(跳过)", s.timeline_narrative)

    def test_highlights_great_moments(self):
        _write_entries(_make_sample_entries())
        s = hc.generate_evening_summary("2026-02-02", use_llm=False)
        great_hl = [h for h in s.highlights if "🔥" in h]
        self.assertEqual(len(great_hl), 1)
        self.assertIn("10:00", great_hl[0])

    def test_highlights_top_category(self):
        _write_entries(_make_sample_entries())
        s = hc.generate_evening_summary("2026-02-02", use_llm=False)
        top_hl = [h for h in s.highlights if "⏱" in h]
        self.assertEqual(len(top_hl), 1)
        self.assertIn("编程", top_hl[0])

    def test_highlights_fatigue_warning(self):
        _write_entries(_make_sample_entries())
        s = hc.generate_evening_summary("2026-02-02", use_llm=False)
        fatigue = [h for h in s.highlights if "⚠️" in h]
        self.assertEqual(len(fatigue), 1)
        self.assertIn("2", fatigue[0])

    def test_summary_persisted(self):
        _write_entries(_make_sample_entries())
        hc.generate_evening_summary("2026-02-02", use_llm=False)
        fp = hc.SUMMARY_DIR / "summary_2026-02-02.json"
        self.assertTrue(fp.exists())

    def test_no_entries_returns_none(self):
        self.assertIsNone(hc.generate_evening_summary("2026-12-31", use_llm=False))

    def test_all_skipped(self):
        entries = [
            hc.CheckinEntry(id=f"s{i}", timestamp=f"2026-02-02 {9+i}:00:00",
                             hour=9+i, skipped=True)
            for i in range(3)
        ]
        _write_entries(entries)
        s = hc.generate_evening_summary("2026-02-02", use_llm=False)
        self.assertIsNotNone(s)
        self.assertEqual(s.total_checkins, 3)
        self.assertEqual(s.skipped_checkins, 3)
        self.assertEqual(s.category_breakdown, {})


# ================================================================
# 4b. LLM 晚间总结测试
# ================================================================

class TestBuildSummaryPrompt(unittest.TestCase):
    """测试 LLM prompt 构建"""

    def setUp(self):
        _clean_dirs()

    def test_prompt_contains_date(self):
        entries = _make_sample_entries()
        actual = [e for e in entries if not e.skipped]
        cat_counts = {"coding": 2, "writing": 1}
        feel_counts = {"good": 2, "great": 1}
        prompt = hc._build_summary_prompt(entries, cat_counts, feel_counts, "2026-02-02")
        self.assertIn("2026-02-02", prompt)

    def test_prompt_contains_timeline(self):
        entries = _make_sample_entries()
        cat_counts = {"coding": 2}
        feel_counts = {"good": 2}
        prompt = hc._build_summary_prompt(entries, cat_counts, feel_counts, "2026-02-02")
        self.assertIn("09:00", prompt)
        self.assertIn("写晨间日记", prompt)
        self.assertIn("跳过签到", prompt)

    def test_prompt_contains_stats(self):
        entries = _make_sample_entries()
        cat_counts = {"coding": 2, "meeting": 1}
        feel_counts = {"good": 2, "tired": 1}
        prompt = hc._build_summary_prompt(entries, cat_counts, feel_counts, "2026-02-02")
        self.assertIn("编程", prompt)
        self.assertIn("2次", prompt)
        self.assertIn("JSON", prompt)

    def test_prompt_requests_json_output(self):
        entries = [hc.CheckinEntry(id="1", timestamp="2026-02-02 10:00:00", hour=10,
                                     doing="写代码", feeling="good", category="coding")]
        prompt = hc._build_summary_prompt(entries, {"coding": 1}, {"good": 1}, "2026-02-02")
        self.assertIn("narrative", prompt)
        self.assertIn("highlights", prompt)
        self.assertIn("reflection", prompt)

    def test_prompt_empty_entries(self):
        """即使有全是 skipped 的 entries 也能构建 prompt"""
        entries = [hc.CheckinEntry(id="s1", timestamp="2026-02-02 09:00:00", hour=9, skipped=True)]
        prompt = hc._build_summary_prompt(entries, {}, {}, "2026-02-02")
        self.assertIn("跳过签到", prompt)


class TestCallLLMForSummary(unittest.TestCase):
    """测试 LLM 调用（mock 网络）"""

    def setUp(self):
        _clean_dirs()

    def _mock_llm_response(self, content_str):
        """构造 mock 的 API 响应"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "choices": [{
                "message": {
                    "content": content_str
                }
            }]
        }
        return mock_resp

    def _make_mock_session(self, mock_resp):
        """构造 mock Session 上下文管理器"""
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.post.return_value = mock_resp
        return mock_session

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_successful_llm_call(self):
        """LLM 返回正确 JSON"""
        llm_json = json.dumps({
            "narrative": "今天是充实的一天，上午高效编程，下午有些疲惫。",
            "highlights": ["上午状态极佳", "下午需要更多休息"],
            "reflection": "明天如何保持上午的好状态？"
        }, ensure_ascii=False)

        mock_session = self._make_mock_session(self._mock_llm_response(llm_json))

        with patch("requests.Session", return_value=mock_session):
            result = hc.call_llm_for_summary("test prompt")

        self.assertIsNotNone(result)
        self.assertIn("narrative", result)
        self.assertIn("highlights", result)
        self.assertIn("reflection", result)
        self.assertEqual(result["narrative"], "今天是充实的一天，上午高效编程，下午有些疲惫。")

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_llm_returns_markdown_wrapped_json(self):
        """LLM 返回 ```json 包裹的内容"""
        llm_json = '```json\n{"narrative": "好", "highlights": ["a"], "reflection": "b"}\n```'

        mock_session = self._make_mock_session(self._mock_llm_response(llm_json))

        with patch("requests.Session", return_value=mock_session):
            result = hc.call_llm_for_summary("test prompt")

        self.assertIsNotNone(result)
        self.assertEqual(result["narrative"], "好")

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": ""})
    def test_no_api_key_returns_none(self):
        """没有 API key 时返回 None"""
        result = hc.call_llm_for_summary("test prompt")
        self.assertIsNone(result)

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_llm_returns_invalid_json(self):
        """LLM 返回无效 JSON"""
        mock_session = self._make_mock_session(self._mock_llm_response("这不是 JSON 内容"))

        with patch("requests.Session", return_value=mock_session):
            result = hc.call_llm_for_summary("test prompt")

        self.assertIsNone(result)

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_llm_empty_content(self):
        """LLM 返回空内容"""
        mock_session = self._make_mock_session(self._mock_llm_response(""))

        with patch("requests.Session", return_value=mock_session):
            result = hc.call_llm_for_summary("test prompt")

        self.assertIsNone(result)

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_llm_timeout(self):
        """LLM 调用超时"""
        import requests as real_requests
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.post.side_effect = real_requests.exceptions.Timeout("timeout")

        with patch("requests.Session", return_value=mock_session):
            result = hc.call_llm_for_summary("test prompt")

        self.assertIsNone(result)

    @patch.dict(os.environ, {"MODELSCOPE_ACCESS_TOKEN": "test-token"})
    def test_llm_network_error(self):
        """LLM 调用网络错误"""
        import requests as real_requests
        mock_session = MagicMock()
        mock_session.__enter__ = MagicMock(return_value=mock_session)
        mock_session.__exit__ = MagicMock(return_value=False)
        mock_session.post.side_effect = real_requests.exceptions.ConnectionError("no network")

        with patch("requests.Session", return_value=mock_session):
            result = hc.call_llm_for_summary("test prompt")

        self.assertIsNone(result)


class TestGenerateEveningSummaryWithLLM(unittest.TestCase):
    """测试 generate_evening_summary 的 LLM 集成"""

    def setUp(self):
        _clean_dirs()
        hc._checkin = None

    def test_summary_with_llm_success(self):
        """LLM 成功时，总结使用 LLM 内容"""
        _write_entries(_make_sample_entries())
        llm_result = {
            "narrative": "LLM生成的叙事总结",
            "highlights": ["LLM亮点1", "LLM亮点2"],
            "reflection": "LLM反思问题"
        }
        with patch("hourly_checkin.call_llm_for_summary", return_value=llm_result):
            s = hc.generate_evening_summary("2026-02-02", use_llm=True)

        self.assertIsNotNone(s)
        self.assertIn("LLM生成的叙事总结", s.timeline_narrative)
        # 本地时间线也保留
        self.assertIn("09:00", s.timeline_narrative)
        self.assertEqual(s.highlights, ["LLM亮点1", "LLM亮点2"])
        self.assertEqual(s.reflection_prompt, "LLM反思问题")

    def test_summary_with_llm_failure_fallback(self):
        """LLM 失败时，fallback 到本地模板"""
        _write_entries(_make_sample_entries())
        with patch("hourly_checkin.call_llm_for_summary", return_value=None):
            s = hc.generate_evening_summary("2026-02-02", use_llm=True)

        self.assertIsNotNone(s)
        # 使用本地时间线
        self.assertIn("09:00", s.timeline_narrative)
        self.assertIn("写晨间日记", s.timeline_narrative)
        # 本地 highlights
        great_hl = [h for h in s.highlights if "🔥" in h]
        self.assertGreaterEqual(len(great_hl), 1)

    def test_summary_with_llm_exception_fallback(self):
        """LLM 抛出异常时，fallback 到本地模板"""
        _write_entries(_make_sample_entries())
        with patch("hourly_checkin.call_llm_for_summary", side_effect=Exception("boom")):
            s = hc.generate_evening_summary("2026-02-02", use_llm=True)

        self.assertIsNotNone(s)
        self.assertIn("09:00", s.timeline_narrative)

    def test_summary_use_llm_false(self):
        """use_llm=False 时不调用 LLM"""
        _write_entries(_make_sample_entries())
        with patch("hourly_checkin.call_llm_for_summary") as mock_llm:
            s = hc.generate_evening_summary("2026-02-02", use_llm=False)
            mock_llm.assert_not_called()

        self.assertIsNotNone(s)
        # 纯本地总结
        self.assertIn("09:00", s.timeline_narrative)

    def test_summary_llm_highlights_as_string(self):
        """LLM 返回 highlights 为字符串时自动转为列表"""
        _write_entries(_make_sample_entries())
        llm_result = {
            "narrative": "总结",
            "highlights": "单个亮点字符串",
            "reflection": "反思"
        }
        with patch("hourly_checkin.call_llm_for_summary", return_value=llm_result):
            s = hc.generate_evening_summary("2026-02-02", use_llm=True)

        self.assertIsInstance(s.highlights, list)
        self.assertEqual(s.highlights, ["单个亮点字符串"])

    def test_summary_all_skipped_no_llm_call(self):
        """全部跳过时不调用 LLM（actual 为空）"""
        entries = [
            hc.CheckinEntry(id=f"s{i}", timestamp=f"2026-02-02 {9+i}:00:00",
                             hour=9+i, skipped=True)
            for i in range(3)
        ]
        _write_entries(entries)
        with patch("hourly_checkin.call_llm_for_summary") as mock_llm:
            s = hc.generate_evening_summary("2026-02-02", use_llm=True)
            mock_llm.assert_not_called()

        self.assertIsNotNone(s)
        self.assertEqual(s.skipped_checkins, 3)

    def test_summary_persisted_with_llm(self):
        """LLM 生成的总结也正确持久化"""
        _write_entries(_make_sample_entries())
        llm_result = {
            "narrative": "持久化测试叙事",
            "highlights": ["持久化亮点"],
            "reflection": "持久化反思"
        }
        with patch("hourly_checkin.call_llm_for_summary", return_value=llm_result):
            hc.generate_evening_summary("2026-02-02", use_llm=True)

        fp = hc.SUMMARY_DIR / "summary_2026-02-02.json"
        self.assertTrue(fp.exists())
        with open(fp, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        self.assertIn("持久化测试叙事", saved["timeline_narrative"])
        self.assertEqual(saved["highlights"], ["持久化亮点"])


# ================================================================
# 5. 反思提示生成测试
# ================================================================

class TestReflectionPrompt(unittest.TestCase):

    def test_good_day(self):
        entries = [hc.CheckinEntry(doing="x", feeling=f) for f in ("great", "good", "good")]
        p = hc._generate_reflection_prompt(entries, {"coding": 3}, {"great": 1, "good": 2})
        self.assertIn("不错", p)

    def test_tired_day(self):
        entries = [hc.CheckinEntry(doing="x", feeling=f) for f in ("tired", "bad", "normal")]
        p = hc._generate_reflection_prompt(entries, {"coding": 3}, {"tired": 1, "bad": 1, "normal": 1})
        self.assertTrue("累" in p or "调整" in p)

    def test_neutral_day(self):
        entries = [hc.CheckinEntry(doing="x", feeling="normal")] * 2
        p = hc._generate_reflection_prompt(entries, {"coding": 1, "reading": 1}, {"normal": 2})
        self.assertTrue("投入" in p or "起伏" in p)

    def test_empty_entries(self):
        p = hc._generate_reflection_prompt([], {}, {})
        self.assertIn("没有签到", p)

    def test_lots_of_entertainment(self):
        entries = [hc.CheckinEntry(doing="x", feeling="normal")] * 5
        p = hc._generate_reflection_prompt(
            entries, {"entertainment": 2, "rest": 2, "coding": 1}, {"normal": 5})
        self.assertTrue("休闲" in p or "娱乐" in p)

    def test_lots_of_deep_work(self):
        entries = [hc.CheckinEntry(doing="x", feeling="good")] * 7
        p = hc._generate_reflection_prompt(
            entries, {"coding": 5, "work": 1, "meeting": 1}, {"good": 7})
        self.assertTrue("休息" in p or "不错" in p or "深度" in p)


# ================================================================
# 6. HourlyCheckin 管理器测试
# ================================================================

class TestHourlyCheckinManager(unittest.TestCase):

    def setUp(self):
        _clean_dirs()
        hc._checkin = None

    def tearDown(self):
        hc._checkin = None

    # --- start / stop ---

    def test_start_sets_running(self):
        mgr = _make_manager(enabled=True)
        mgr.start()
        self.assertTrue(mgr._running)
        self.assertIsNotNone(mgr._next_checkin)
        mgr.stop()

    def test_start_disabled_noop(self):
        mgr = _make_manager(enabled=False)
        mgr.start()
        self.assertFalse(mgr._running)

    def test_stop(self):
        mgr = _make_manager(enabled=True)
        mgr.start()
        mgr.stop()
        self.assertFalse(mgr._running)

    def test_double_start_idempotent(self):
        mgr = _make_manager(enabled=True)
        mgr.start()
        t1 = mgr._thread
        mgr.start()
        self.assertIs(mgr._thread, t1)
        mgr.stop()

    # --- schedule ---

    def test_schedule_hourly_aligns_to_hour(self):
        mgr = _make_manager(interval_minutes=60, start_hour=0, end_hour=23)
        mgr._running = True
        mgr._schedule_next()
        self.assertEqual(mgr._next_checkin.minute, 0)
        self.assertEqual(mgr._next_checkin.second, 0)

    def test_schedule_short_interval(self):
        mgr = _make_manager(interval_minutes=30, start_hour=0, end_hour=23)
        mgr._running = True
        mgr._schedule_next()
        diff = (mgr._next_checkin - datetime.now()).total_seconds()
        self.assertGreater(diff, 25 * 60)
        self.assertLess(diff, 35 * 60)

    def test_schedule_before_start_hour_logic(self):
        """凌晨 → 推到 start_hour"""
        now = datetime(2026, 2, 2, 3, 0, 0)
        next_h = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if next_h.hour < 9:
            next_h = next_h.replace(hour=9, minute=0, second=0)
        self.assertEqual(next_h.hour, 9)

    def test_schedule_after_end_hour_logic(self):
        """超过 end_hour → 推到次日"""
        now = datetime(2026, 2, 2, 22, 30, 0)
        next_h = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        if next_h.hour >= 22:
            tomorrow = next_h + timedelta(days=1)
            next_h = tomorrow.replace(hour=9, minute=0, second=0)
        self.assertEqual(next_h.day, 3)
        self.assertEqual(next_h.hour, 9)

    # --- web checkin ---

    def test_add_entry_from_web(self):
        mgr = _make_manager()
        entry = mgr.add_entry_from_web("写代码", "great")
        self.assertEqual(entry.doing, "写代码")
        self.assertEqual(entry.feeling, "great")
        self.assertEqual(entry.category, "coding")
        self.assertEqual(mgr.stats["checkins_today"], 1)
        self.assertEqual(len(hc._load_today_entries()), 1)

    def test_add_multiple_entries(self):
        mgr = _make_manager()
        mgr.add_entry_from_web("写代码", "good")
        mgr.add_entry_from_web("开会", "normal")
        mgr.add_entry_from_web("午餐", "good")
        self.assertEqual(mgr.stats["checkins_today"], 3)
        self.assertEqual(len(hc._load_today_entries()), 3)

    # --- get_status ---

    def test_status_stopped(self):
        mgr = _make_manager(enabled=True)
        s = mgr.get_status()
        self.assertTrue(s["enabled"])
        self.assertFalse(s["running"])
        self.assertIsNone(s["next_checkin"])

    def test_status_running(self):
        mgr = _make_manager(enabled=True)
        mgr.start()
        s = mgr.get_status()
        self.assertTrue(s["running"])
        self.assertIsNotNone(s["next_checkin"])
        self.assertIn("minutes_until_next", s)
        mgr.stop()

    # --- settings ---

    def test_update_settings(self):
        mgr = _make_manager(interval_minutes=60)
        mgr._running = True
        mgr._schedule_next()
        mgr.update_settings(interval_minutes=30, start_hour=8)
        self.assertEqual(mgr.settings.interval_minutes, 30)
        self.assertEqual(mgr.settings.start_hour, 8)

    def test_update_settings_ignores_none(self):
        mgr = _make_manager(interval_minutes=60)
        mgr.update_settings(interval_minutes=None, start_hour=7)
        self.assertEqual(mgr.settings.interval_minutes, 60)
        self.assertEqual(mgr.settings.start_hour, 7)

    def test_save_settings_to_disk(self):
        mgr = _make_manager(interval_minutes=45)
        _FakeConfig.DATA_DIR.mkdir(parents=True, exist_ok=True)
        mgr.save_settings()
        self.assertTrue(mgr.settings_file.exists())
        with open(mgr.settings_file, 'r') as f:
            data = json.load(f)
        self.assertEqual(data["interval_minutes"], 45)

    # --- get_today_entries ---

    def test_get_today_entries_returns_dicts(self):
        mgr = _make_manager()
        mgr.add_entry_from_web("t1", "good")
        mgr.add_entry_from_web("t2", "normal")
        entries = mgr.get_today_entries()
        self.assertEqual(len(entries), 2)
        self.assertTrue(all(isinstance(e, dict) for e in entries))

    # --- _sync_stats ---

    def test_sync_stats(self):
        entries = [
            hc.CheckinEntry(doing="a", feeling="good"),
            hc.CheckinEntry(doing="b", feeling="normal"),
            hc.CheckinEntry(skipped=True),
        ]
        hc._save_today_entries(entries)
        mgr = _make_manager()
        mgr._sync_stats()
        self.assertEqual(mgr.stats["checkins_today"], 2)
        self.assertEqual(mgr.stats["skipped_today"], 1)

    # --- _do_checkin (mocked dialog) ---

    def test_do_checkin_submitted(self):
        mgr = _make_manager()
        mgr._running = True
        result = {"skipped": "false", "doing": "看论文", "feeling": "good"}
        with patch("hourly_checkin.show_checkin_dialog", return_value=result), \
             patch("hourly_checkin.play_checkin_sound"):
            mgr._do_checkin()
        self.assertEqual(mgr.stats["checkins_today"], 1)
        entries = hc._load_today_entries()
        self.assertEqual(entries[0].doing, "看论文")

    def test_do_checkin_skipped(self):
        mgr = _make_manager()
        mgr._running = True
        result = {"skipped": "true", "doing": "", "feeling": "normal"}
        with patch("hourly_checkin.show_checkin_dialog", return_value=result), \
             patch("hourly_checkin.play_checkin_sound"):
            mgr._do_checkin()
        self.assertEqual(mgr.stats["skipped_today"], 1)
        self.assertTrue(hc._load_today_entries()[0].skipped)

    def test_do_checkin_dialog_failure(self):
        mgr = _make_manager()
        mgr._running = True
        with patch("hourly_checkin.show_checkin_dialog", return_value=None), \
             patch("hourly_checkin.play_checkin_sound"):
            mgr._do_checkin()
        self.assertEqual(len(hc._load_today_entries()), 0)

    def test_do_checkin_fires_callback(self):
        mgr = _make_manager()
        mgr._running = True
        captured = []
        mgr._on_checkin = lambda d: captured.append(d)
        result = {"skipped": "false", "doing": "test", "feeling": "normal"}
        with patch("hourly_checkin.show_checkin_dialog", return_value=result), \
             patch("hourly_checkin.play_checkin_sound"):
            mgr._do_checkin()
        self.assertEqual(len(captured), 1)
        self.assertEqual(captured[0]["doing"], "test")

    # --- trigger_now ---

    def test_trigger_now(self):
        mgr = _make_manager()
        mgr._running = True
        result = {"skipped": "false", "doing": "手动触发", "feeling": "great"}
        with patch("hourly_checkin.show_checkin_dialog", return_value=result), \
             patch("hourly_checkin.play_checkin_sound"):
            mgr.trigger_now()
            time.sleep(0.5)
        self.assertEqual(len(hc._load_today_entries()), 1)

    def test_trigger_blocked_while_showing(self):
        mgr = _make_manager()
        mgr._showing_dialog = True
        with patch("hourly_checkin.show_checkin_dialog") as mock_dlg:
            mgr.trigger_now()
            time.sleep(0.3)
            mock_dlg.assert_not_called()

    # --- idle detection ---

    def test_idle_no_monitor(self):
        mgr = _make_manager()
        self.assertFalse(mgr._is_user_idle())

    # --- evening summary in manager ---

    def test_evening_summary_sets_flag(self):
        mgr = _make_manager()
        mgr.add_entry_from_web("写代码", "good")
        mgr._generate_evening_summary()
        self.assertTrue(mgr._summary_generated_today)

    def test_evening_summary_no_duplicate(self):
        """已有总结时不重新生成"""
        mgr = _make_manager()
        mgr.add_entry_from_web("test", "good")
        today = datetime.now().strftime("%Y-%m-%d")
        hc.generate_evening_summary(today)
        mgr._generate_evening_summary()
        self.assertTrue(mgr._summary_generated_today)


# ================================================================
# 7. 单例与模块级函数测试
# ================================================================

class TestSingleton(unittest.TestCase):

    def setUp(self):
        _clean_dirs()
        hc._checkin = None

    def tearDown(self):
        hc._checkin = None

    def test_singleton(self):
        c1 = hc.get_hourly_checkin()
        c2 = hc.get_hourly_checkin()
        self.assertIs(c1, c2)

    def test_stop_with_instance(self):
        hc._checkin = MagicMock()
        hc.stop_hourly_checkin()
        hc._checkin.stop.assert_called_once()

    def test_stop_none_safe(self):
        hc._checkin = None
        hc.stop_hourly_checkin()


# ================================================================
# 8. 边界情况与鲁棒性
# ================================================================

class TestEdgeCases(unittest.TestCase):

    def setUp(self):
        _clean_dirs()
        hc._checkin = None

    def test_empty_doing_inferred_other(self):
        self.assertEqual(hc.infer_category(""), "other")

    def test_very_long_text(self):
        long_text = "写代码" * 500
        entry = hc.CheckinEntry(doing=long_text)
        self.assertEqual(entry.doing, long_text)
        self.assertEqual(hc.infer_category(long_text), "coding")

    def test_special_characters_round_trip(self):
        text = '写代码 "hello" <script> & 🎉'
        entry = hc.CheckinEntry(doing=text)
        restored = hc.CheckinEntry.from_dict(entry.to_dict())
        self.assertEqual(restored.doing, text)

    def test_ensure_dirs_idempotent(self):
        hc.ensure_dirs()
        hc.ensure_dirs()
        self.assertTrue(hc.CHECKIN_DIR.exists())
        self.assertTrue(hc.SUMMARY_DIR.exists())

    def test_concurrent_web_checkins(self):
        """并发写入不应崩溃"""
        mgr = _make_manager()
        threads = [
            threading.Thread(target=mgr.add_entry_from_web, args=(f"task{i}", "good"))
            for i in range(5)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(mgr.stats["checkins_today"], 5)
        entries = hc._load_today_entries()
        self.assertGreaterEqual(len(entries), 1)

    def test_single_entry_summary(self):
        """只有一条签到也能生成总结"""
        _write_entries([
            hc.CheckinEntry(id="1", timestamp="2026-02-02 10:00:00", hour=10,
                             doing="工作", feeling="normal", category="work"),
        ])
        s = hc.generate_evening_summary("2026-02-02")
        self.assertIsNotNone(s)
        self.assertEqual(s.total_checkins, 1)
        self.assertEqual(s.skipped_checkins, 0)
        self.assertIn("work", s.category_breakdown)


# ================================================================
# 运行
# ================================================================

if __name__ == "__main__":
    # 运行完毕后清理临时目录
    try:
        unittest.main(verbosity=2)
    finally:
        shutil.rmtree(_tmpdir, ignore_errors=True)
