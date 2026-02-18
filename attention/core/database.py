"""
数据存储模块
将分析结果保存为JSON格式的本地数据库
支持截图分析结果和融合状态的存储
"""
import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any, Optional

from attention.config import Config
from attention.core.analyzer import AnalysisResult

logger = logging.getLogger(__name__)


class WorkLogDatabase:
    """工作日志数据库"""
    
    def __init__(self):
        self.config = Config
        self.db_file = self.config.DATABASE_FILE
        self._lock = threading.Lock()
        self._ensure_db_exists()
    
    def _ensure_db_exists(self):
        """确保数据库文件存在"""
        self.config.ensure_dirs()
        if not self.db_file.exists():
            self._write_data([])
    
    def _read_data(self) -> List[Dict[str, Any]]:
        """读取数据库"""
        try:
            with open(self.db_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return []
    
    def _write_data(self, data: List[Dict[str, Any]]):
        """写入数据库"""
        with open(self.db_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    
    def save_record(
        self,
        analysis: AnalysisResult,
        screenshot_path: Optional[Path] = None,
        raw_response: str = "",
        fused_state: Optional[Dict] = None,
        activity_state: Optional[Dict] = None
    ) -> Dict[str, Any]:
        """
        保存一条分析记录
        
        Args:
            analysis: 截图分析结果
            screenshot_path: 截图文件路径
            raw_response: 模型原始响应
            fused_state: 融合后的状态（可选）
            activity_state: 活动状态（可选）
            
        Returns:
            保存的记录
        """
        record = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "screenshot_path": str(screenshot_path) if screenshot_path else None,
            "analysis": analysis.to_dict(),
            "raw_response": raw_response
        }
        
        # 添加融合状态
        if fused_state:
            record["fused_state"] = fused_state
        
        # 添加活动状态
        if activity_state:
            record["activity_state"] = activity_state
        
        with self._lock:
            data = self._read_data()
            data.append(record)
            self._write_data(data)
        
        logger.debug(f"记录已保存: {record['timestamp']}")
        return record
    
    def get_records(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        limit: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        获取记录
        
        Args:
            start_time: 开始时间
            end_time: 结束时间
            limit: 限制数量
            
        Returns:
            符合条件的记录列表
        """
        with self._lock:
            data = self._read_data()
        
        # 时间过滤
        if start_time or end_time:
            filtered = []
            for record in data:
                try:
                    record_time = datetime.strptime(
                        record["timestamp"], "%Y-%m-%d %H:%M:%S"
                    )
                    if start_time and record_time < start_time:
                        continue
                    if end_time and record_time > end_time:
                        continue
                    filtered.append(record)
                except (ValueError, KeyError):
                    continue
            data = filtered
        
        # 限制数量
        if limit:
            data = data[-limit:]
        
        return data
    
    def get_today_records(self) -> List[Dict[str, Any]]:
        """获取今天的记录"""
        today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        return self.get_records(start_time=today)

    def get_records_for_date(self, date_str: str) -> List[Dict[str, Any]]:
        """
        获取指定日期的记录。

        Args:
            date_str: 日期字符串，格式 "YYYY-MM-DD"

        Returns:
            该日期的所有记录
        """
        try:
            target = datetime.strptime(date_str, "%Y-%m-%d")
            start = target.replace(hour=0, minute=0, second=0, microsecond=0)
            end = target.replace(hour=23, minute=59, second=59, microsecond=999999)
            return self.get_records(start_time=start, end_time=end)
        except ValueError:
            return []

    def get_statistics(self, records: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """
        计算统计数据
        
        Args:
            records: 要统计的记录，None则使用今天的记录
            
        Returns:
            统计结果
        """
        if records is None:
            records = self.get_today_records()
        
        if not records:
            return {
                "total_records": 0,
                "work_status_distribution": {},
                "engagement_distribution": {},
                "attention_distribution": {},
                "productive_ratio": 0,
                "distracted_ratio": 0,
                "time_range": None
            }
        
        # 计算各项统计
        status_counts = {}
        engagement_counts = {}
        attention_counts = {}
        productive_count = 0
        distracted_count = 0
        
        for record in records:
            analysis = record.get("analysis", {})
            fused = record.get("fused_state", {})
            
            # 工作状态分布
            status = analysis.get("work_status", "未知")
            status_counts[status] = status_counts.get(status, 0) + 1
            
            # 参与度分布
            engagement = fused.get("user_engagement", "未知")
            engagement_counts[engagement] = engagement_counts.get(engagement, 0) + 1
            
            # 注意力分布
            attention = fused.get("attention_level", "未知")
            attention_counts[attention] = attention_counts.get(attention, 0) + 1
            
            # 生产力统计
            if fused.get("is_productive", False):
                productive_count += 1
            if fused.get("is_distracted", False):
                distracted_count += 1
        
        total = len(records)
        
        return {
            "total_records": total,
            "work_status_distribution": status_counts,
            "engagement_distribution": engagement_counts,
            "attention_distribution": attention_counts,
            "productive_ratio": round(productive_count / total, 2) if total else 0,
            "distracted_ratio": round(distracted_count / total, 2) if total else 0,
            "time_range": {
                "start": records[0]["timestamp"] if records else None,
                "end": records[-1]["timestamp"] if records else None
            }
        }
    
    def get_recent_entertainment_duration(self) -> int:
        """
        获取最近连续娱乐时长（分钟）
        
        Returns:
            连续娱乐分钟数
        """
        records = self.get_records(limit=30)  # 最近30条记录
        
        if not records:
            return 0
        
        # 从最新记录向前统计
        duration = 0
        for record in reversed(records):
            fused = record.get("fused_state", {})
            engagement = fused.get("user_engagement", "")
            
            # 检查是否是被动消费（娱乐）
            if engagement == "被动消费" or fused.get("is_distracted", False):
                duration += 1
            else:
                break
        
        return duration
    
    def get_recent_distraction_streak(self) -> Dict[str, Any]:
        """
        获取最近的分心连续记录
        
        Returns:
            分心统计信息
        """
        records = self.get_records(limit=60)  # 最近60条记录（约1小时）
        
        if not records:
            return {"streak_minutes": 0, "apps": [], "started_at": None}
        
        streak = 0
        apps = set()
        started_at = None
        
        for record in reversed(records):
            fused = record.get("fused_state", {})
            
            if fused.get("is_distracted", False):
                streak += 1
                if fused.get("active_window_app"):
                    apps.add(fused["active_window_app"])
                started_at = record["timestamp"]
            else:
                break
        
        return {
            "streak_minutes": streak,
            "apps": list(apps),
            "started_at": started_at
        }
    
    def get_hourly_pattern(self, days: int = 7) -> Dict[int, Dict[str, float]]:
        """
        获取每小时的工作模式统计
        
        Args:
            days: 统计最近多少天
            
        Returns:
            每小时的统计数据 {hour: {productive_ratio, distracted_ratio, ...}}
        """
        cutoff = datetime.now() - timedelta(days=days)
        records = self.get_records(start_time=cutoff)
        
        hourly_data: Dict[int, Dict[str, int]] = {h: {"total": 0, "productive": 0, "distracted": 0} for h in range(24)}
        
        for record in records:
            try:
                ts = datetime.strptime(record["timestamp"], "%Y-%m-%d %H:%M:%S")
                hour = ts.hour
                
                hourly_data[hour]["total"] += 1
                
                fused = record.get("fused_state", {})
                if fused.get("is_productive", False):
                    hourly_data[hour]["productive"] += 1
                if fused.get("is_distracted", False):
                    hourly_data[hour]["distracted"] += 1
                    
            except (ValueError, KeyError):
                continue
        
        # 转换为比例
        result = {}
        for hour, data in hourly_data.items():
            total = data["total"]
            if total > 0:
                result[hour] = {
                    "productive_ratio": round(data["productive"] / total, 2),
                    "distracted_ratio": round(data["distracted"] / total, 2),
                    "sample_count": total
                }
            else:
                result[hour] = {
                    "productive_ratio": 0,
                    "distracted_ratio": 0,
                    "sample_count": 0
                }
        
        return result
    
    def cleanup_old_records(self, days: int = 30):
        """
        清理过旧的记录
        
        Args:
            days: 保留最近多少天的记录
        """
        cutoff = datetime.now() - timedelta(days=days)
        
        with self._lock:
            data = self._read_data()
            filtered = []
            
            for record in data:
                try:
                    record_time = datetime.strptime(
                        record["timestamp"], "%Y-%m-%d %H:%M:%S"
                    )
                    if record_time >= cutoff:
                        filtered.append(record)
                except (ValueError, KeyError):
                    continue
            
            self._write_data(filtered)
            
        removed = len(data) - len(filtered)
        if removed > 0:
            logger.info(f"已清理 {removed} 条过期记录")


# 模块级便捷函数
_database = None

def get_database() -> WorkLogDatabase:
    """获取数据库单例"""
    global _database
    if _database is None:
        _database = WorkLogDatabase()
    return _database

def save_to_database(
    analysis: AnalysisResult,
    screenshot_path: Optional[Path] = None,
    raw_response: str = "",
    fused_state: Optional[Dict] = None,
    activity_state: Optional[Dict] = None
) -> Dict[str, Any]:
    """保存记录的便捷函数"""
    return get_database().save_record(
        analysis, screenshot_path, raw_response, fused_state, activity_state
    )
