"""
每日报告生成模块
生成综合日报：效率统计、应用使用分布、与平均值对比、个性化建议
支持HTML报告生成，在用户第二天开机时弹出
"""
import json
import logging
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from pathlib import Path

from attention.config import Config
from attention.core.database import get_database

logger = logging.getLogger(__name__)

REPORT_DIR = Config.DATA_DIR / "reports"


def ensure_report_dir():
    """确保报告目录存在"""
    REPORT_DIR.mkdir(parents=True, exist_ok=True)


def generate_daily_report(target_date: Optional[datetime] = None) -> Dict[str, Any]:
    """
    生成指定日期的每日报告数据

    Args:
        target_date: 目标日期，None则为昨天

    Returns:
        报告数据字典
    """
    db = get_database()

    if target_date is None:
        target_date = datetime.now() - timedelta(days=1)

    day_start = target_date.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    # 获取当天记录
    records = db.get_records(start_time=day_start, end_time=day_end)
    stats = db.get_statistics(records)

    if not records:
        return {
            "date": day_start.strftime("%Y-%m-%d"),
            "has_data": False,
            "message": "当日没有记录数据"
        }

    # ========== 1. 基础效率统计 ==========
    total = len(records)
    productive_count = sum(1 for r in records if r.get("fused_state", {}).get("is_productive", False))
    distracted_count = sum(1 for r in records if r.get("fused_state", {}).get("is_distracted", False))
    neutral_count = total - productive_count - distracted_count

    productive_ratio = productive_count / total if total else 0
    distracted_ratio = distracted_count / total if total else 0

    # 活跃时段（有记录的小时段）
    active_hours = set()
    for r in records:
        try:
            ts = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
            active_hours.add(ts.hour)
        except (ValueError, KeyError):
            pass

    first_record_time = records[0]["timestamp"] if records else None
    last_record_time = records[-1]["timestamp"] if records else None

    # ========== 2. 应用使用分布 ==========
    app_usage = {}
    app_category_time = {"work": 0, "communication": 0, "learning": 0, "entertainment": 0, "unknown": 0}

    for r in records:
        fused = r.get("fused_state", {})
        app = fused.get("active_window_app", "未知") or "未知"
        cat = fused.get("app_category", "unknown") or "unknown"

        app_usage[app] = app_usage.get(app, 0) + 1
        if cat in app_category_time:
            app_category_time[cat] += 1
        else:
            app_category_time["unknown"] += 1

    # Top 10 应用
    top_apps = sorted(app_usage.items(), key=lambda x: -x[1])[:10]

    # 分类占比
    category_ratios = {}
    for cat, count in app_category_time.items():
        category_ratios[cat] = round(count / total, 3) if total else 0

    # ========== 3. 注意力分布 ==========
    attention_dist = stats.get("attention_distribution", {})
    engagement_dist = stats.get("engagement_distribution", {})

    # ========== 4. 每小时效率曲线 ==========
    hourly_stats = {}
    for r in records:
        try:
            ts = datetime.strptime(r["timestamp"], "%Y-%m-%d %H:%M:%S")
            hour = ts.hour
            if hour not in hourly_stats:
                hourly_stats[hour] = {"total": 0, "productive": 0, "distracted": 0}
            hourly_stats[hour]["total"] += 1
            fused = r.get("fused_state", {})
            if fused.get("is_productive", False):
                hourly_stats[hour]["productive"] += 1
            if fused.get("is_distracted", False):
                hourly_stats[hour]["distracted"] += 1
        except (ValueError, KeyError):
            pass

    hourly_efficiency = []
    for hour in range(24):
        h_data = hourly_stats.get(hour, {"total": 0, "productive": 0, "distracted": 0})
        h_total = h_data["total"]
        hourly_efficiency.append({
            "hour": hour,
            "total": h_total,
            "productive_ratio": round(h_data["productive"] / h_total, 2) if h_total else 0,
            "distracted_ratio": round(h_data["distracted"] / h_total, 2) if h_total else 0,
        })

    # 找出高效时段和低效时段
    peak_hours = [h for h in hourly_efficiency if h["total"] >= 3 and h["productive_ratio"] >= 0.7]
    low_hours = [h for h in hourly_efficiency if h["total"] >= 3 and h["distracted_ratio"] >= 0.5]

    # ========== 5. 与历史平均对比 ==========
    avg_data = _calculate_weekly_average(db, day_start)

    comparison = {}
    if avg_data["has_data"]:
        comparison = {
            "avg_productive_ratio": avg_data["avg_productive_ratio"],
            "avg_distracted_ratio": avg_data["avg_distracted_ratio"],
            "avg_records_per_day": avg_data["avg_records"],
            "productive_delta": round(productive_ratio - avg_data["avg_productive_ratio"], 3),
            "distracted_delta": round(distracted_ratio - avg_data["avg_distracted_ratio"], 3),
            "records_delta": total - avg_data["avg_records"],
        }

    # ========== 6. 智能建议 ==========
    suggestions = _generate_suggestions(
        productive_ratio=productive_ratio,
        distracted_ratio=distracted_ratio,
        peak_hours=peak_hours,
        low_hours=low_hours,
        top_apps=top_apps,
        category_ratios=category_ratios,
        comparison=comparison,
        total_records=total,
    )

    # ========== 组装报告 ==========
    report = {
        "date": day_start.strftime("%Y-%m-%d"),
        "weekday": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][day_start.weekday()],
        "has_data": True,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),

        # 基础统计
        "summary": {
            "total_records": total,
            "productive_count": productive_count,
            "distracted_count": distracted_count,
            "neutral_count": neutral_count,
            "productive_ratio": round(productive_ratio, 3),
            "distracted_ratio": round(distracted_ratio, 3),
            "active_hours": len(active_hours),
            "first_record": first_record_time,
            "last_record": last_record_time,
        },

        # 应用使用
        "app_usage": {
            "top_apps": [{"app": name, "minutes": count} for name, count in top_apps],
            "category_ratios": category_ratios,
        },

        # 注意力/参与度
        "attention_distribution": attention_dist,
        "engagement_distribution": engagement_dist,

        # 时间段分析
        "hourly_efficiency": hourly_efficiency,
        "peak_hours": [h["hour"] for h in peak_hours],
        "low_hours": [h["hour"] for h in low_hours],

        # 与平均对比
        "comparison": comparison,

        # 建议
        "suggestions": suggestions,
    }

    # 保存报告
    _save_report(report)

    return report


def _calculate_weekly_average(db, target_date: datetime) -> Dict[str, Any]:
    """计算过去7天的平均值（排除目标日期）"""
    total_records = 0
    total_productive = 0
    total_distracted = 0
    days_with_data = 0

    for i in range(1, 8):  # 前7天
        day = target_date - timedelta(days=i)
        day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end = day_start + timedelta(days=1)
        records = db.get_records(start_time=day_start, end_time=day_end)

        if records:
            days_with_data += 1
            count = len(records)
            total_records += count
            total_productive += sum(
                1 for r in records if r.get("fused_state", {}).get("is_productive", False)
            )
            total_distracted += sum(
                1 for r in records if r.get("fused_state", {}).get("is_distracted", False)
            )

    if days_with_data == 0:
        return {"has_data": False}

    avg_records = total_records / days_with_data
    avg_prod = total_productive / total_records if total_records else 0
    avg_dist = total_distracted / total_records if total_records else 0

    return {
        "has_data": True,
        "days_with_data": days_with_data,
        "avg_records": round(avg_records),
        "avg_productive_ratio": round(avg_prod, 3),
        "avg_distracted_ratio": round(avg_dist, 3),
    }


def _generate_suggestions(
    productive_ratio: float,
    distracted_ratio: float,
    peak_hours: list,
    low_hours: list,
    top_apps: list,
    category_ratios: dict,
    comparison: dict,
    total_records: int,
) -> List[Dict[str, str]]:
    """生成个性化建议"""
    suggestions = []

    # 效率建议
    if productive_ratio >= 0.7:
        suggestions.append({
            "type": "positive",
            "icon": "🏆",
            "title": "高效的一天",
            "content": f"你的生产率达到了 {productive_ratio:.0%}，超过了大多数工作日。继续保持这种节奏！"
        })
    elif productive_ratio >= 0.5:
        suggestions.append({
            "type": "neutral",
            "icon": "💪",
            "title": "稳定发挥",
            "content": f"生产率 {productive_ratio:.0%}，属于正常水平。尝试减少中途打断，可以进一步提升。"
        })
    else:
        suggestions.append({
            "type": "warning",
            "icon": "⚡",
            "title": "效率需要关注",
            "content": f"生产率仅 {productive_ratio:.0%}。建议明天尝试番茄工作法，先从2个番茄钟开始。"
        })

    # 分心率建议
    if distracted_ratio > 0.3:
        entertainment_ratio = category_ratios.get("entertainment", 0)
        if entertainment_ratio > 0.2:
            suggestions.append({
                "type": "warning",
                "icon": "📱",
                "title": "娱乐时间过多",
                "content": f"娱乐类应用占比 {entertainment_ratio:.0%}。考虑在工作时段使用专注模式屏蔽社交媒体。"
            })

    # 高效时段建议
    if peak_hours:
        hours_str = "、".join([f"{h['hour']}:00" for h in peak_hours[:3]])
        suggestions.append({
            "type": "insight",
            "icon": "🕐",
            "title": "你的黄金时段",
            "content": f"你在 {hours_str} 效率最高。把重要任务安排在这些时段。"
        })

    # 低效时段建议
    if low_hours:
        hours_str = "、".join([f"{h['hour']}:00" for h in low_hours[:3]])
        suggestions.append({
            "type": "insight",
            "icon": "☕",
            "title": "低效时段预警",
            "content": f"你在 {hours_str} 容易分心。这些时段适合安排轻松的会议或行政事务。"
        })

    # 与平均对比
    if comparison:
        prod_delta = comparison.get("productive_delta", 0)
        if prod_delta > 0.1:
            suggestions.append({
                "type": "positive",
                "icon": "📈",
                "title": "超越平均水平",
                "content": f"比过去一周平均生产率高出 {prod_delta:.0%}。你正在进步！"
            })
        elif prod_delta < -0.1:
            suggestions.append({
                "type": "warning",
                "icon": "📉",
                "title": "低于平均水平",
                "content": f"比过去一周平均低了 {abs(prod_delta):.0%}。每个人都有低谷，明天重新开始。"
            })

    # 应用使用建议
    if top_apps:
        top_app_name, top_app_count = top_apps[0]
        if top_app_count / total_records > 0.5:
            suggestions.append({
                "type": "insight",
                "icon": "🔍",
                "title": "单一应用占比过高",
                "content": f"你在 {top_app_name} 上花费了超过一半的时间。注意适当切换和休息。"
            })

    # 如果记录太少
    if total_records < 10:
        suggestions.append({
            "type": "neutral",
            "icon": "📊",
            "title": "数据量不足",
            "content": "今天的记录较少，建议保持监控运行以获得更准确的分析。"
        })

    return suggestions


def _save_report(report: Dict[str, Any]):
    """保存报告到文件"""
    ensure_report_dir()
    filename = f"daily_report_{report['date']}.json"
    filepath = REPORT_DIR / filename
    try:
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
        logger.info(f"日报已保存: {filepath}")
    except Exception as e:
        logger.error(f"保存日报失败: {e}")


def get_latest_report() -> Optional[Dict[str, Any]]:
    """获取最新的日报"""
    ensure_report_dir()
    reports = sorted(REPORT_DIR.glob("daily_report_*.json"), reverse=True)
    if reports:
        try:
            with open(reports[0], 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取日报失败: {e}")
    return None


def get_report_by_date(date_str: str) -> Optional[Dict[str, Any]]:
    """按日期获取报告"""
    filepath = REPORT_DIR / f"daily_report_{date_str}.json"
    if filepath.exists():
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"读取日报失败: {e}")
    return None


def check_and_generate_yesterday_report() -> Optional[Dict[str, Any]]:
    """
    检查昨天的报告是否已生成，如果没有则生成
    适合在应用启动时调用
    """
    yesterday = datetime.now() - timedelta(days=1)
    date_str = yesterday.strftime("%Y-%m-%d")

    existing = get_report_by_date(date_str)
    if existing:
        return existing

    logger.info(f"生成昨日 ({date_str}) 日报...")
    return generate_daily_report(yesterday)
