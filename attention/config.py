"""
配置管理模块
个人注意力管理Agent配置
"""
import os
from datetime import time
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()


class Config:
    # API配置 — 统一使用 ModelScope 生态模型
    QWEN_API_BASE = "https://api-inference.modelscope.cn/v1"  # 魔塔社区 API Inference
    QWEN_API_KEY = os.getenv("MODELSCOPE_ACCESS_TOKEN", "")   # 统一使用 MODELSCOPE_ACCESS_TOKEN
    MODEL_NAME = "Qwen/Qwen3-VL-235B-A22B-Instruct"             # 视觉模型（截屏分析）
    TEXT_MODEL_NAME = "Qwen/Qwen2.5-72B-Instruct"             # 文本模型（任务解析、提醒生成、回顾等）
    SENSEVOICE_MODEL = "iic/SenseVoiceSmall"                   # 语音模型（语音识别 + 情感检测）
    
    # 监控配置
    CHECK_INTERVAL = 60  # 截图分析间隔（秒）
    WORK_HOURS = {
        "start": time(9, 0),
        "end": time(18, 0)
    }
    
    # 活动监控配置
    ACTIVITY_MONITOR = {
        "enabled": True,              # 是否启用活动监控
        "sample_interval": 1.0,       # 活动采样间隔（秒）
        "history_size": 120,          # 保留的历史快照数量
        "aggregation_window": 60,     # 聚合窗口大小（秒）
    }
    
    # 状态融合配置
    STATE_FUSION = {
        "idle_threshold": 120,        # 空闲超过N秒判定为离开
        "distraction_threshold": 300, # 娱乐超过N秒判定为分心
        "low_activity_threshold": 0.1,# 活动比例低于此值判定为低活动
        "high_switch_threshold": 10,  # 窗口切换超过N次判定为注意力分散
    }
    
    # 介入提醒配置
    INTERVENTION = {
        "enabled": True,              # 是否启用介入提醒
        "min_entertainment_duration": 300,  # 最小娱乐时长触发（秒）
        "cooldown": 600,              # 提醒冷却时间（秒）
        "style": "encouraging",       # 提醒风格: encouraging/neutral/strict
    }
    
    # 主动规划配置 (v5.2)
    ACTIVE_PLANNER = {
        "enabled": True,              # 是否启用主动规划引擎
        "off_plan_threshold": 3,      # 连续偏离多少个周期才介入（3×CHECK_INTERVAL）
        "nudge_cooldown": 600,        # 计划提醒冷却时间（秒）
        "max_rest_minutes": 30,       # 最大合法休息时长（分钟）
        "default_rest_minutes": 15,   # 默认休息时长（分钟）
        "show_plan_on_start": True,   # 启动时是否主动告知计划
    }
    
    # 自动启动配置
    AUTO_START = {
        "enabled": False,  # 是否开机自启动
        "minimize": True,  # 启动时是否最小化
        "app_name": "AttentionAgent",
    }
    
    # 路径配置
    BASE_DIR = Path(__file__).resolve().parent.parent  # project root (parent of attention/)
    DATA_DIR = BASE_DIR / "data"
    SCREENSHOT_DIR = BASE_DIR / "screenshots"
    DATABASE_FILE = DATA_DIR / "work_logs.json"
    ACTIVITY_LOG_FILE = DATA_DIR / "activity_logs.json"  # 活动日志
    
    # API调用配置
    MAX_RETRIES = 3  # 最大重试次数
    RETRY_DELAY = 5  # 重试延迟（秒）
    REQUEST_TIMEOUT = 30  # 请求超时（秒）
    
    # 截图配置
    SAVE_SCREENSHOTS = True  # 是否保存截图文件
    SCREENSHOT_QUALITY = 85  # JPEG质量（1-100）
    MAX_SCREENSHOT_AGE_DAYS = 7  # 截图保留天数
    
    @classmethod
    def ensure_dirs(cls):
        """确保必要的目录存在"""
        cls.DATA_DIR.mkdir(parents=True, exist_ok=True)
        cls.SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    
    @classmethod
    def is_work_hours(cls) -> bool:
        """检查当前是否在工作时间"""
        from datetime import datetime
        now = datetime.now().time()
        return cls.WORK_HOURS["start"] <= now <= cls.WORK_HOURS["end"]
    
    @classmethod
    def validate(cls) -> bool:
        """验证配置是否有效"""
        if not cls.QWEN_API_KEY:
            raise ValueError("MODELSCOPE_ACCESS_TOKEN 未设置，请在 .env 文件中配置")
        return True
    
    @classmethod
    def get_fusion_config(cls) -> dict:
        """获取状态融合配置"""
        return cls.STATE_FUSION.copy()
    
    @classmethod
    def get_activity_config(cls) -> dict:
        """获取活动监控配置"""
        return cls.ACTIVITY_MONITOR.copy()
