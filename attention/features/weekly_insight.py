"""
周数据洞察模块

聚合过去 7 天的效率数据，使用 Reviewer Agent（LLM）分析行为模式，
发现用户的高效时段、常见分心诱因，并给出具体改进建议。

API: GET /api/weekly-insight
"""
import json
import logging
from datetime import date, timedelta
from typing import Dict, Any, Optional

from attention.config import Config

logger = logging.getLogger(__name__)


def _collect_weekly_data(days: int = 7) -> Dict[str, Any]:
    """
    收集过去 N 天的效率数据。

    Returns:
        {
            "days": [
                {
                    "date": "2026-02-08",
                    "productive_ratio": 0.65,
                    "distracted_ratio": 0.15,
                    "total_records": 42,
                    "goal_count": 3,
                    "goal_completed": 2,
                    "pomo_count": 4,
                    "work_start": "09:15"
                }, ...
            ]
        }
    """
    today = date.today()
    daily_data = []

    # Briefing 数据（目标）
    briefing_data = {}
    try:
        briefing_file = Config.DATA_DIR / "daily_briefing.json"
        if briefing_file.exists():
            with open(briefing_file, "r", encoding="utf-8") as f:
                briefing_data = json.load(f)
    except Exception:
        pass

    # 开工时间数据
    work_start_data = {}
    try:
        ws_file = Config.DATA_DIR / "work_start_times.json"
        if ws_file.exists():
            with open(ws_file, "r", encoding="utf-8") as f:
                work_start_data = json.load(f)
    except Exception:
        pass

    # 专注会话数据
    focus_data = {}
    try:
        focus_file = Config.DATA_DIR / "focus_sessions.json"
        if focus_file.exists():
            with open(focus_file, "r", encoding="utf-8") as f:
                focus_data = json.load(f)
    except Exception:
        pass

    # 监控记录
    db = None
    try:
        from attention.core.database import get_database
        db = get_database()
    except Exception:
        pass

    for i in range(days):
        d = today - timedelta(days=i)
        day_key = d.isoformat()
        entry = {"date": day_key}

        # Briefing 目标
        if day_key in briefing_data:
            goals = briefing_data[day_key].get("goals", [])
            entry["goal_count"] = len(goals)
            entry["goal_completed"] = sum(1 for g in goals if g.get("done"))
        else:
            entry["goal_count"] = 0
            entry["goal_completed"] = 0

        # 开工时间
        if day_key in work_start_data:
            entry["work_start"] = work_start_data[day_key].get("start_time", "未记录")
        else:
            entry["work_start"] = "未记录"

        # 专注会话
        day_sessions = focus_data.get(day_key, [])
        entry["pomo_count"] = len(day_sessions)
        entry["focus_minutes"] = sum(s.get("duration_minutes", 0) for s in day_sessions)

        # 效率数据
        if db:
            try:
                records = db.get_records_for_date(day_key)
                if records:
                    stats = db.get_statistics(records)
                    entry["total_records"] = len(records)
                    entry["productive_ratio"] = round(stats.get("productive_ratio", 0), 2)
                    entry["distracted_ratio"] = round(stats.get("distracted_ratio", 0), 2)
                else:
                    entry["total_records"] = 0
                    entry["productive_ratio"] = 0
                    entry["distracted_ratio"] = 0
            except Exception:
                entry["total_records"] = 0
                entry["productive_ratio"] = 0
                entry["distracted_ratio"] = 0
        else:
            entry["total_records"] = 0
            entry["productive_ratio"] = 0
            entry["distracted_ratio"] = 0

        daily_data.append(entry)

    return {"days": list(reversed(daily_data))}  # 按时间正序


def generate_weekly_insight(days: int = 7) -> Dict[str, Any]:
    """
    生成过去 N 天的效率洞察。

    优先使用 Reviewer Agent（LLM）分析模式，
    失败时返回纯数据统计。
    """
    weekly_data = _collect_weekly_data(days)

    # 基础统计
    all_days = weekly_data["days"]
    active_days = [d for d in all_days if d["total_records"] > 0]

    stats = {
        "total_days": len(all_days),
        "active_days": len(active_days),
        "avg_productive_ratio": 0,
        "avg_distracted_ratio": 0,
        "total_pomo": sum(d["pomo_count"] for d in all_days),
        "total_focus_minutes": sum(d.get("focus_minutes", 0) for d in all_days),
        "total_goals_set": sum(d["goal_count"] for d in all_days),
        "total_goals_completed": sum(d["goal_completed"] for d in all_days),
    }

    if active_days:
        stats["avg_productive_ratio"] = round(
            sum(d["productive_ratio"] for d in active_days) / len(active_days), 2
        )
        stats["avg_distracted_ratio"] = round(
            sum(d["distracted_ratio"] for d in active_days) / len(active_days), 2
        )

    # 尝试用 LLM 分析行为模式
    llm_insight = None
    try:
        llm_insight = _analyze_with_llm(weekly_data, stats)
    except Exception as e:
        logger.debug(f"LLM 周洞察生成失败: {e}")

    return {
        "period": f"{all_days[0]['date']} ~ {all_days[-1]['date']}" if all_days else "",
        "daily_data": all_days,
        "stats": stats,
        "insight": llm_insight,
    }


def _analyze_with_llm(weekly_data: Dict, stats: Dict) -> Optional[Dict[str, Any]]:
    """用 Reviewer Agent 分析周数据，发现行为模式"""
    from attention.core.agents import call_agent_json

    prompt = f"""分析这位用户过去 7 天的效率数据，发现行为模式：

每日数据：
{json.dumps(weekly_data['days'], ensure_ascii=False, indent=2)}

汇总统计：
- 活跃天数：{stats['active_days']}/{stats['total_days']}
- 平均生产率：{stats['avg_productive_ratio']:.0%}
- 平均分心率：{stats['avg_distracted_ratio']:.0%}
- 番茄钟总数：{stats['total_pomo']}
- 目标完成：{stats['total_goals_completed']}/{stats['total_goals_set']}

请找出：
1. 哪天效率最高，可能的原因
2. 常见的分心时段或模式
3. 一个具体、可执行的改进建议

输出 JSON：
{{
  "best_day": "YYYY-MM-DD",
  "best_day_reason": "可能原因",
  "pattern": "发现的行为模式",
  "suggestion": "具体改进建议",
  "trend": "overall_improving / stable / declining"
}}
只输出 JSON。"""

    return call_agent_json(
        "reviewer",
        prompt,
        max_tokens=400,
        temperature=0.7,
        timeout=15,
    )
