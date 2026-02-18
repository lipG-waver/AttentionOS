"""
每日开工时间追踪模块
记录每天早上6点后第一次打开电脑（启动监控）的时间
支持工作日/休息日区分和历史对比
"""
import json
import logging
from datetime import datetime, timedelta, date
from pathlib import Path
from typing import Optional, Dict, Any, List

from attention.config import Config

logger = logging.getLogger(__name__)

WORK_START_FILE = Config.DATA_DIR / "work_start_times.json"

# 工作日: 周一(0) ~ 周五(4)
WORKDAY_WEEKDAYS = {0, 1, 2, 3, 4}


def _is_workday(d: date) -> bool:
    """判断是否为工作日"""
    return d.weekday() in WORKDAY_WEEKDAYS


def _load_data() -> Dict[str, Any]:
    """加载历史数据"""
    try:
        if WORK_START_FILE.exists():
            with open(WORK_START_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
    except (json.JSONDecodeError, IOError):
        pass
    return {}


def _save_data(data: Dict[str, Any]):
    """保存数据"""
    Config.ensure_dirs()
    with open(WORK_START_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


class WorkStartTracker:
    """每日开工时间追踪器"""

    def __init__(self):
        self.data = _load_data()

    def record_start(self):
        """
        记录当前时间为今日开工时间。
        规则：仅在6:00之后、且当日尚未记录时写入。
        """
        now = datetime.now()
        if now.hour < 6:
            return  # 早于6点不记录

        today_str = now.strftime("%Y-%m-%d")
        if today_str in self.data:
            return  # 已有记录

        self.data[today_str] = {
            "start_time": now.strftime("%H:%M:%S"),
            "timestamp": now.strftime("%Y-%m-%d %H:%M:%S"),
            "is_workday": _is_workday(now.date()),
            "weekday": now.strftime("%A"),
        }
        _save_data(self.data)
        logger.info(f"记录今日开工时间: {now.strftime('%H:%M:%S')}")

    def get_today(self) -> Dict[str, Any]:
        """获取今天的开工信息"""
        today_str = datetime.now().strftime("%Y-%m-%d")
        entry = self.data.get(today_str)
        if not entry:
            return {"date": today_str, "recorded": False, "start_time": None}
        return {"date": today_str, "recorded": True, **entry}

    def get_history(self, days: int = 30) -> List[Dict[str, Any]]:
        """
        获取最近N天的开工时间历史，包含平均值对比。
        """
        today = date.today()
        history = []

        workday_times = []
        weekend_times = []

        for i in range(days):
            d = today - timedelta(days=i)
            d_str = d.strftime("%Y-%m-%d")
            entry = self.data.get(d_str)
            is_wd = _is_workday(d)

            if entry:
                record = {
                    "date": d_str,
                    "start_time": entry["start_time"],
                    "is_workday": is_wd,
                    "weekday": d.strftime("%A"),
                }
                history.append(record)
                # 收集用于计算平均值
                parts = entry["start_time"].split(":")
                minutes_since_midnight = int(parts[0]) * 60 + int(parts[1])
                if is_wd:
                    workday_times.append(minutes_since_midnight)
                else:
                    weekend_times.append(minutes_since_midnight)
            else:
                history.append({
                    "date": d_str,
                    "start_time": None,
                    "is_workday": is_wd,
                    "weekday": d.strftime("%A"),
                })

        # 计算平均开工时间
        def avg_time(minutes_list):
            if not minutes_list:
                return None
            avg = sum(minutes_list) / len(minutes_list)
            h, m = divmod(int(avg), 60)
            return f"{h:02d}:{m:02d}"

        return {
            "days": history,
            "avg_workday": avg_time(workday_times),
            "avg_weekend": avg_time(weekend_times),
            "workday_count": len(workday_times),
            "weekend_count": len(weekend_times),
        }


# 单例
_tracker: Optional[WorkStartTracker] = None


def get_work_start_tracker() -> WorkStartTracker:
    global _tracker
    if _tracker is None:
        _tracker = WorkStartTracker()
    return _tracker


def record_work_start():
    """便捷函数：记录开工时间"""
    get_work_start_tracker().record_start()
