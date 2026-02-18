"""
状态融合模块
将截图分析结果与本地活动信号融合，得出更准确的用户状态判断
"""
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional, Dict, Any, List
from enum import Enum

from attention.core.analyzer import AnalysisResult
from attention.core.activity_monitor import ActivityState, ActivitySnapshot

logger = logging.getLogger(__name__)


class UserEngagement(Enum):
    """用户参与度级别"""
    ACTIVE_WORKING = "主动工作"      # 有输入 + 工作类应用
    PASSIVE_CONSUMING = "被动消费"    # 无输入 + 娱乐类应用（看视频等）
    READING_THINKING = "阅读思考"     # 无输入 + 工作类应用（看文档、思考）
    DISTRACTED = "分心离开"           # 无输入 + 长时间空闲
    COMMUNICATING = "沟通交流"        # 有输入 + 沟通类应用
    MIXED = "混合状态"                # 无法明确判断


class AttentionLevel(Enum):
    """注意力级别"""
    FOCUSED = "专注"          # 高度集中
    ENGAGED = "投入"          # 正常工作状态
    DRIFTING = "游离"         # 开始分心
    DISTRACTED = "分心"       # 明显分心
    AWAY = "离开"             # 不在电脑前


# 应用分类（可扩展）
APP_CATEGORIES = {
    # 工作类
    "work": [
        "code", "vscode", "visual studio", "pycharm", "intellij", "xcode",
        "sublime", "atom", "vim", "nvim", "emacs",
        "word", "excel", "powerpoint", "pages", "numbers", "keynote",
        "notion", "obsidian", "typora", "markdown",
        "terminal", "iterm", "hyper", "powershell", "cmd",
        "figma", "sketch", "photoshop", "illustrator", "affinity",
        "postman", "insomnia", "datagrip", "sequel", "mysql", "pgadmin",
        "github", "gitlab", "sourcetree", "gitkraken",
    ],
    # 沟通类
    "communication": [
        "slack", "teams", "zoom", "meet", "webex", "skype",
        "微信", "wechat", "钉钉", "dingtalk", "飞书", "lark", "feishu",
        "mail", "outlook", "thunderbird", "spark", "airmail",
        "discord", "telegram",
    ],
    # 学习类
    "learning": [
        "kindle", "pdf", "reader", "preview",
        "coursera", "udemy", "edx", "khan",
        "arxiv", "scholar", "research",
        "documentation", "docs", "mdn", "devdocs",
        "stackoverflow", "github.com", "medium",
    ],
    # 娱乐类
    "entertainment": [
        "bilibili", "b站", "youtube", "netflix", "爱奇艺", "优酷", "腾讯视频",
        "抖音", "tiktok", "快手", "小红书",
        "微博", "weibo", "twitter", "x.com", "instagram", "facebook",
        "steam", "epic", "游戏", "game",
        "spotify", "music", "网易云", "qq音乐",
        "reddit", "v2ex", "知乎娱乐",
        "旦挞", "danta",  # 旦挞 - 校园社交/娱乐
    ],
    # 浏览器（中性，需要结合窗口标题判断）
    "browser": [
        "chrome", "firefox", "safari", "edge", "brave", "arc",
    ],
}


def categorize_app(app_name: str, window_title: str = "") -> str:
    """
    判断应用类别
    
    Args:
        app_name: 应用名称
        window_title: 窗口标题（用于浏览器等需要进一步判断的应用）
        
    Returns:
        类别: work/communication/learning/entertainment/unknown
    """
    app_lower = app_name.lower()
    title_lower = window_title.lower()
    combined = f"{app_lower} {title_lower}"
    
    # 遍历各类别
    for category, keywords in APP_CATEGORIES.items():
        if category == "browser":
            continue  # 浏览器单独处理
        for keyword in keywords:
            if keyword in combined:
                return category
    
    # 如果是浏览器，根据标题判断
    for keyword in APP_CATEGORIES["browser"]:
        if keyword in app_lower:
            # 浏览器，需要根据标题进一步判断
            for cat, kws in APP_CATEGORIES.items():
                if cat == "browser":
                    continue
                for kw in kws:
                    if kw in title_lower:
                        return cat
            # 浏览器标题无法识别具体内容时，标记为 unknown 而非直接算 work
            # 这样避免无法判断的浏览器使用被误判为生产性工作
            return "unknown"
    
    return "unknown"


@dataclass
class FusedState:
    """融合后的用户状态"""
    timestamp: datetime
    
    # 来自截图分析
    screen_work_status: str = ""           # 截图分析的工作状态
    screen_applications: List[str] = None  # 检测到的应用
    screen_content_type: str = ""          # 内容类型
    
    # 来自本地活动监控
    activity_ratio: float = 0.0            # 活动比例
    engagement_level: str = ""             # 参与度级别
    keyboard_events: int = 0               # 键盘事件数
    mouse_events: int = 0                  # 鼠标事件数
    window_switches: int = 0               # 窗口切换次数
    idle_duration: int = 0                 # 空闲时长（秒）
    active_window_app: str = ""            # 当前焦点应用
    active_window_title: str = ""          # 当前焦点窗口标题
    
    # 融合判断结果
    user_engagement: str = ""              # 用户参与类型
    attention_level: str = ""              # 注意力级别
    app_category: str = ""                 # 应用类别
    is_productive: bool = False            # 是否处于生产性状态
    is_distracted: bool = False            # 是否分心
    needs_intervention: bool = False       # 是否需要介入提醒
    intervention_reason: str = ""          # 介入原因
    
    # 置信度
    confidence: float = 0.0                # 判断置信度 (0-1)
    
    def __post_init__(self):
        if self.screen_applications is None:
            self.screen_applications = []
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "screen_work_status": self.screen_work_status,
            "screen_applications": self.screen_applications,
            "screen_content_type": self.screen_content_type,
            "activity_ratio": round(self.activity_ratio, 2),
            "engagement_level": self.engagement_level,
            "keyboard_events": self.keyboard_events,
            "mouse_events": self.mouse_events,
            "window_switches": self.window_switches,
            "idle_duration": self.idle_duration,
            "active_window_app": self.active_window_app,
            "active_window_title": self.active_window_title,
            "user_engagement": self.user_engagement,
            "attention_level": self.attention_level,
            "app_category": self.app_category,
            "is_productive": self.is_productive,
            "is_distracted": self.is_distracted,
            "needs_intervention": self.needs_intervention,
            "intervention_reason": self.intervention_reason,
            "confidence": round(self.confidence, 2),
        }


class StateFusion:
    """
    状态融合器
    结合截图分析和本地活动信号，产出更准确的用户状态判断
    """
    
    def __init__(self, config: Optional[Dict] = None):
        """
        Args:
            config: 配置参数
        """
        self.config = config or {}
        
        # 阈值配置
        self.idle_threshold = self.config.get("idle_threshold", 120)  # 空闲超过120秒判定为离开
        self.distraction_threshold = self.config.get("distraction_threshold", 300)  # 娱乐超过5分钟判定为分心
        self.low_activity_threshold = self.config.get("low_activity_threshold", 0.1)  # 活动比例低于10%判定为低活动
        self.high_switch_threshold = self.config.get("high_switch_threshold", 10)  # 窗口切换超过10次判定为注意力分散
    
    def fuse(
        self,
        screen_analysis: Optional[AnalysisResult],
        activity_state: Optional[ActivityState],
        idle_duration: int = 0
    ) -> FusedState:
        """
        融合截图分析和活动状态
        
        Args:
            screen_analysis: 截图分析结果
            activity_state: 活动状态
            idle_duration: 当前空闲时长（秒）
            
        Returns:
            融合后的状态
        """
        now = datetime.now()
        
        # 初始化融合状态
        fused = FusedState(timestamp=now)
        
        # 填充截图分析数据
        if screen_analysis:
            fused.screen_work_status = screen_analysis.work_status
            fused.screen_applications = screen_analysis.applications_detected
            fused.screen_content_type = screen_analysis.content_type
        
        # 填充活动状态数据
        if activity_state:
            fused.activity_ratio = activity_state.activity_ratio
            fused.engagement_level = activity_state.engagement_level
            fused.keyboard_events = activity_state.keyboard_events
            fused.mouse_events = activity_state.mouse_events
            fused.window_switches = activity_state.window_switches
            fused.active_window_app = activity_state.primary_window_app
            fused.active_window_title = activity_state.primary_window_title
        
        fused.idle_duration = idle_duration
        
        # 判断应用类别
        fused.app_category = categorize_app(
            fused.active_window_app or "",
            fused.active_window_title or ""
        )
        
        # 核心融合逻辑
        fused = self._determine_engagement(fused)
        fused = self._determine_attention(fused)
        fused = self._determine_productivity(fused)
        fused = self._check_intervention_needed(fused)
        fused = self._calculate_confidence(fused)
        
        return fused
    
    def _determine_engagement(self, fused: FusedState) -> FusedState:
        """判断用户参与类型"""
        is_active = fused.activity_ratio > self.low_activity_threshold
        is_idle = fused.idle_duration > self.idle_threshold
        is_zero_activity = fused.activity_ratio == 0  # 完全没有活动
        
        app_cat = fused.app_category
        screen_status = fused.screen_work_status
        
        # 判断逻辑矩阵
        if is_idle:
            fused.user_engagement = UserEngagement.DISTRACTED.value
        elif is_zero_activity:
            # 活动率为0 → 用户不在交互，判定为分心离开
            # （即使 idle_duration 尚未超过阈值，0活动率说明一段时间没有任何操作）
            fused.user_engagement = UserEngagement.DISTRACTED.value
        elif is_active and app_cat in ["work", "learning"]:
            fused.user_engagement = UserEngagement.ACTIVE_WORKING.value
        elif is_active and app_cat == "communication":
            fused.user_engagement = UserEngagement.COMMUNICATING.value
        elif not is_active and app_cat == "entertainment":
            fused.user_engagement = UserEngagement.PASSIVE_CONSUMING.value
        elif not is_active and app_cat in ["work", "learning"]:
            fused.user_engagement = UserEngagement.READING_THINKING.value
        elif is_active and app_cat == "entertainment":
            # 有输入的娱乐活动（比如打游戏、发弹幕）
            fused.user_engagement = UserEngagement.PASSIVE_CONSUMING.value
        else:
            fused.user_engagement = UserEngagement.MIXED.value
        
        return fused
    
    def _determine_attention(self, fused: FusedState) -> FusedState:
        """判断注意力级别"""
        # 基于多个信号综合判断
        
        # 信号1: 空闲时长
        if fused.idle_duration > self.idle_threshold:
            fused.attention_level = AttentionLevel.AWAY.value
            return fused
        
        # 信号1.5: 完全零活动（即使 idle_duration 未到阈值）
        if fused.activity_ratio == 0:
            fused.attention_level = AttentionLevel.AWAY.value
            return fused
        
        # 信号2: 窗口切换频率
        high_switching = fused.window_switches > self.high_switch_threshold
        
        # 信号3: 应用类别
        productive_app = fused.app_category in ["work", "learning", "communication"]
        
        # 信号4: 活动强度
        high_activity = fused.activity_ratio > 0.5
        medium_activity = fused.activity_ratio > 0.2
        
        # 综合判断
        if productive_app and high_activity and not high_switching:
            fused.attention_level = AttentionLevel.FOCUSED.value
        elif productive_app and medium_activity:
            fused.attention_level = AttentionLevel.ENGAGED.value
        elif high_switching or (not productive_app and medium_activity):
            fused.attention_level = AttentionLevel.DRIFTING.value
        elif not productive_app:
            fused.attention_level = AttentionLevel.DISTRACTED.value
        else:
            fused.attention_level = AttentionLevel.ENGAGED.value
        
        return fused
    
    def _determine_productivity(self, fused: FusedState) -> FusedState:
        """判断是否处于生产性状态"""
        productive_engagements = [
            UserEngagement.ACTIVE_WORKING.value,
            UserEngagement.READING_THINKING.value,
            UserEngagement.COMMUNICATING.value,
        ]
        
        productive_attention = [
            AttentionLevel.FOCUSED.value,
            AttentionLevel.ENGAGED.value,
        ]
        
        fused.is_productive = (
            fused.user_engagement in productive_engagements and
            fused.attention_level in productive_attention
        )
        
        # 分心判断
        distracted_engagements = [
            UserEngagement.PASSIVE_CONSUMING.value,
            UserEngagement.DISTRACTED.value,
        ]
        
        distracted_attention = [
            AttentionLevel.DISTRACTED.value,
            AttentionLevel.AWAY.value,
        ]
        
        fused.is_distracted = (
            fused.user_engagement in distracted_engagements or
            fused.attention_level in distracted_attention
        )
        
        return fused
    
    def _check_intervention_needed(self, fused: FusedState) -> FusedState:
        """检查是否需要介入提醒"""
        reasons = []
        
        # 条件1: 长时间娱乐
        if (fused.user_engagement == UserEngagement.PASSIVE_CONSUMING.value and
            fused.activity_ratio > 0.3):  # 有在主动操作（不是单纯看视频走开了）
            reasons.append("持续处于娱乐状态")
        
        # 条件2: 高频窗口切换
        if fused.window_switches > self.high_switch_threshold:
            reasons.append(f"频繁切换窗口({fused.window_switches}次)")
        
        # 条件3: 分心状态
        if fused.attention_level == AttentionLevel.DISTRACTED.value:
            if fused.app_category == "entertainment":
                reasons.append("注意力分散到娱乐内容")
        
        # 条件4: 从工作切换到娱乐（需要历史状态，这里简化处理）
        # 这个逻辑需要在更上层实现，记录状态变化
        
        if reasons:
            fused.needs_intervention = True
            fused.intervention_reason = "；".join(reasons)
        
        return fused
    
    def _calculate_confidence(self, fused: FusedState) -> FusedState:
        """计算判断置信度"""
        confidence = 0.5  # 基础置信度
        
        # 有截图分析结果，+0.2
        if fused.screen_work_status:
            confidence += 0.2
        
        # 有足够的活动样本，+0.2
        if fused.keyboard_events + fused.mouse_events > 10:
            confidence += 0.2
        
        # 截图分析和活动状态一致，+0.1
        screen_productive = fused.screen_work_status in ["高效工作", "学习研究", "沟通协调"]
        activity_productive = fused.app_category in ["work", "learning", "communication"]
        if screen_productive == activity_productive:
            confidence += 0.1
        
        fused.confidence = min(confidence, 1.0)
        return fused


# 全局融合器实例
_fusion: Optional[StateFusion] = None


def get_state_fusion(config: Optional[Dict] = None) -> StateFusion:
    """获取状态融合器单例"""
    global _fusion
    if _fusion is None:
        _fusion = StateFusion(config)
    return _fusion


def fuse_state(
    screen_analysis: Optional[AnalysisResult],
    activity_state: Optional[ActivityState],
    idle_duration: int = 0
) -> FusedState:
    """融合状态的便捷函数"""
    return get_state_fusion().fuse(screen_analysis, activity_state, idle_duration)


# 测试代码
if __name__ == "__main__":
    from attention.core.analyzer import AnalysisResult
    from attention.core.activity_monitor import ActivityState
    from datetime import datetime
    
    # 模拟数据
    screen = AnalysisResult(
        work_status="休闲娱乐",
        applications_detected=["Chrome", "Bilibili"],
        content_type="视频网站"
    )
    
    activity = ActivityState(
        period_start=datetime.now(),
        period_end=datetime.now(),
        keyboard_events=2,
        mouse_events=15,
        total_snapshots=60,
        primary_window_app="Google Chrome",
        primary_window_title="【bilibili】某个视频标题",
        window_switches=3
    )
    
    # 融合
    fusion = StateFusion()
    result = fuse_state(screen, activity, idle_duration=0)
    
    print("融合结果:")
    for k, v in result.to_dict().items():
        print(f"  {k}: {v}")
