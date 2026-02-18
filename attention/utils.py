"""
工具函数模块
提供日志配置、通知、报告生成等工具函数
"""
import logging
import sys
from datetime import datetime
from typing import Optional, Dict, Any

from attention.config import Config


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """
    配置日志系统
    
    Args:
        level: 日志级别
        
    Returns:
        根日志器
    """
    # 创建格式化器
    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    
    # 控制台处理器
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    
    # 文件处理器
    Config.ensure_dirs()
    log_file = Config.DATA_DIR / "attention_agent.log"
    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.DEBUG)
    
    # 配置根日志器
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.DEBUG)
    
    # 清除已有handler，避免重复
    root_logger.handlers.clear()
    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)
    
    return root_logger


def send_notification(title: str, message: str):
    """
    发送系统通知
    
    Args:
        title: 通知标题
        message: 通知内容
    """
    try:
        # Windows通知
        if sys.platform == "win32":
            try:
                from win10toast import ToastNotifier
                toaster = ToastNotifier()
                toaster.show_toast(title, message, duration=5, threaded=True)
                return
            except ImportError:
                pass
            
            try:
                from plyer import notification
                notification.notify(
                    title=title,
                    message=message,
                    timeout=5
                )
                return
            except ImportError:
                pass
        
        # macOS通知
        elif sys.platform == "darwin":
            import subprocess
            subprocess.run([
                "osascript", "-e",
                f'display notification "{message}" with title "{title}"'
            ])
            return
        
        # Linux通知
        elif sys.platform.startswith("linux"):
            import subprocess
            subprocess.run(["notify-send", title, message])
            return
        
        # 回退到控制台输出
        print(f"\n[通知] {title}: {message}\n")
        
    except Exception as e:
        logging.warning(f"发送通知失败: {e}")
        print(f"\n[通知] {title}: {message}\n")


def format_duration(minutes: int) -> str:
    """
    格式化时长显示
    
    Args:
        minutes: 分钟数
        
    Returns:
        格式化的时长字符串
    """
    if minutes < 60:
        return f"{minutes}分钟"
    
    hours = minutes // 60
    mins = minutes % 60
    
    if mins == 0:
        return f"{hours}小时"
    return f"{hours}小时{mins}分钟"


def format_seconds(seconds: int) -> str:
    """
    格式化秒数显示
    
    Args:
        seconds: 秒数
        
    Returns:
        格式化的时长字符串
    """
    if seconds < 60:
        return f"{seconds}秒"
    elif seconds < 3600:
        mins = seconds // 60
        secs = seconds % 60
        if secs == 0:
            return f"{mins}分钟"
        return f"{mins}分{secs}秒"
    else:
        hours = seconds // 3600
        mins = (seconds % 3600) // 60
        if mins == 0:
            return f"{hours}小时"
        return f"{hours}小时{mins}分钟"


def get_status_emoji(work_status: str) -> str:
    """
    获取工作状态对应的emoji
    
    Args:
        work_status: 工作状态
        
    Returns:
        对应的emoji
    """
    emoji_map = {
        "高效工作": "💻",
        "沟通协调": "💬",
        "学习研究": "📚",
        "休闲娱乐": "🎮",
        "混合状态": "🔄",
        "未知": "❓"
    }
    return emoji_map.get(work_status, "❓")


def get_engagement_emoji(engagement_level: str) -> str:
    """
    获取参与度对应的emoji
    
    Args:
        engagement_level: 参与度级别
        
    Returns:
        对应的emoji
    """
    emoji_map = {
        "高度活跃": "🔥",
        "中度活跃": "⚡",
        "低度活跃": "💤",
        "空闲": "😴",
    }
    return emoji_map.get(engagement_level, "❓")


def get_attention_color(attention_level: str) -> str:
    """
    获取注意力级别对应的彩色标记
    
    Args:
        attention_level: 注意力级别
        
    Returns:
        带颜色的标记
    """
    color_map = {
        "专注": "🟢",
        "投入": "🟢",
        "游离": "🟡",
        "分心": "🔴",
        "离开": "⚫",
    }
    return color_map.get(attention_level, "⚪")


def get_user_engagement_emoji(user_engagement: str) -> str:
    """
    获取用户参与类型对应的emoji
    
    Args:
        user_engagement: 用户参与类型
        
    Returns:
        对应的emoji
    """
    emoji_map = {
        "主动工作": "💪",
        "被动消费": "📺",
        "阅读思考": "🤔",
        "分心离开": "🚶",
        "沟通交流": "🗣️",
        "混合状态": "🔀",
    }
    return emoji_map.get(user_engagement, "❓")


def generate_daily_report(statistics: Dict[str, Any]) -> str:
    """
    生成每日报告
    
    Args:
        statistics: 统计数据
        
    Returns:
        报告文本
    """
    report_lines = [
        "=" * 60,
        f"每日工作状态报告 - {datetime.now().strftime('%Y-%m-%d')}",
        "=" * 60,
        "",
        f"📊 总记录数: {statistics.get('total_records', 0)} 条",
        f"✅ 生产效率: {statistics.get('productive_ratio', 0):.0%}",
        f"⚠️  分心比例: {statistics.get('distracted_ratio', 0):.0%}",
        "",
    ]
    
    # 工作状态分布
    distribution = statistics.get("work_status_distribution", {})
    if distribution:
        report_lines.append("📈 工作状态分布:")
        total = sum(distribution.values())
        for status, count in sorted(distribution.items(), key=lambda x: -x[1]):
            percentage = (count / total) * 100 if total else 0
            bar = "█" * int(percentage / 5)
            emoji = get_status_emoji(status)
            report_lines.append(f"  {emoji} {status}: {count}次 ({percentage:.1f}%) {bar}")
    
    # 参与度分布
    engagement_dist = statistics.get("engagement_distribution", {})
    if engagement_dist:
        report_lines.append("")
        report_lines.append("🎯 参与类型分布:")
        total = sum(engagement_dist.values())
        for eng, count in sorted(engagement_dist.items(), key=lambda x: -x[1]):
            percentage = (count / total) * 100 if total else 0
            emoji = get_user_engagement_emoji(eng)
            report_lines.append(f"  {emoji} {eng}: {count}次 ({percentage:.1f}%)")
    
    # 注意力分布
    attention_dist = statistics.get("attention_distribution", {})
    if attention_dist:
        report_lines.append("")
        report_lines.append("🧠 注意力分布:")
        total = sum(attention_dist.values())
        for att, count in sorted(attention_dist.items(), key=lambda x: -x[1]):
            percentage = (count / total) * 100 if total else 0
            color = get_attention_color(att)
            report_lines.append(f"  {color} {att}: {count}次 ({percentage:.1f}%)")
    
    # 时间范围
    time_range = statistics.get("time_range", {})
    if time_range and time_range.get("start"):
        report_lines.extend([
            "",
            f"⏰ 时间范围: {time_range.get('start')} ~ {time_range.get('end')}"
        ])
    
    report_lines.append("=" * 60)
    
    return "\n".join(report_lines)


def generate_hourly_insight(hourly_pattern: Dict[int, Dict[str, float]]) -> str:
    """
    生成每小时效率洞察
    
    Args:
        hourly_pattern: 每小时统计数据
        
    Returns:
        洞察文本
    """
    lines = [
        "",
        "📊 每小时效率模式",
        "-" * 40,
    ]
    
    # 找出高效时段和低效时段
    productive_hours = []
    distracted_hours = []
    
    for hour, data in hourly_pattern.items():
        if data["sample_count"] < 5:  # 样本太少不统计
            continue
        if data["productive_ratio"] >= 0.7:
            productive_hours.append((hour, data["productive_ratio"]))
        if data["distracted_ratio"] >= 0.5:
            distracted_hours.append((hour, data["distracted_ratio"]))
    
    if productive_hours:
        productive_hours.sort(key=lambda x: -x[1])
        hours_str = ", ".join([f"{h}:00" for h, _ in productive_hours[:3]])
        lines.append(f"✅ 高效时段: {hours_str}")
    
    if distracted_hours:
        distracted_hours.sort(key=lambda x: -x[1])
        hours_str = ", ".join([f"{h}:00" for h, _ in distracted_hours[:3]])
        lines.append(f"⚠️  易分心时段: {hours_str}")
    
    # 生成小时图表
    lines.append("")
    lines.append("时段效率图 (9:00-22:00):")
    
    for hour in range(9, 23):
        data = hourly_pattern.get(hour, {})
        prod_ratio = data.get("productive_ratio", 0)
        dist_ratio = data.get("distracted_ratio", 0)
        sample = data.get("sample_count", 0)
        
        # 用字符表示
        if sample < 3:
            bar = "  ···"  # 样本不足
        elif prod_ratio >= 0.7:
            bar = "  ████"  # 高效
        elif prod_ratio >= 0.5:
            bar = "  ███░"  # 较高效
        elif dist_ratio >= 0.5:
            bar = "  ░░██"  # 分心
        else:
            bar = "  ██░░"  # 一般
        
        lines.append(f"  {hour:02d}:00 {bar}")
    
    lines.append("")
    lines.append("图例: ████高效  ███░较好  ██░░一般  ░░██分心  ···样本不足")
    
    return "\n".join(lines)


def check_intervention_needed(
    fused_state: Dict[str, Any],
    distraction_streak: int = 0,
    config: Optional[Dict] = None
) -> tuple:
    """
    检查是否需要介入提醒
    
    Args:
        fused_state: 融合后的状态
        distraction_streak: 连续分心次数
        config: 配置
        
    Returns:
        Tuple[是否需要介入, 原因说明, 严重程度(1-3)]
    """
    if config is None:
        config = Config.INTERVENTION
    
    reasons = []
    severity = 0
    
    # 检查是否分心
    if fused_state.get("is_distracted", False):
        severity += 1
        
        # 连续分心
        if distraction_streak >= 5:  # 连续5分钟
            reasons.append(f"已连续{distraction_streak}分钟处于分心状态")
            severity += 1
        
        if distraction_streak >= 10:
            severity += 1
    
    # 检查注意力级别
    attention = fused_state.get("attention_level", "")
    if attention == "分心":
        if "分心" not in str(reasons):
            reasons.append("注意力分散")
    
    # 检查窗口切换
    switches = fused_state.get("window_switches", 0)
    if switches > 15:
        reasons.append(f"频繁切换窗口({switches}次)")
        severity = max(severity, 1)
    
    # 检查是否在娱乐
    engagement = fused_state.get("user_engagement", "")
    if engagement == "被动消费":
        if distraction_streak >= 3:
            reasons.append("持续处于娱乐状态")
    
    if reasons and severity > 0:
        return True, "；".join(reasons), min(severity, 3)
    
    return False, "", 0


def get_encouragement_message(severity: int = 1, context: str = "") -> str:
    """
    获取鼓励性提醒消息
    
    Args:
        severity: 严重程度 (1-3)
        context: 上下文（如当前在做什么）
        
    Returns:
        鼓励消息
    """
    # 轻度提醒
    mild_messages = [
        "休息一下也不错，但别忘了你的目标哦 💪",
        "大家都会分心，关键是能及时回来 🎯",
        "短暂放松后，继续前进吧 🚀",
        "你已经做得很好了，再坚持一下？",
        "专注力是可以训练的，每次回归都是进步 ✨",
    ]
    
    # 中度提醒
    moderate_messages = [
        "已经休息了一会儿了，是时候回到正轨了 💡",
        "你的目标还在等着你，我们继续？",
        "分心是正常的，但你比大多数人更能掌控自己 💪",
        "想想今天想完成什么，然后行动起来 🎯",
        "每一次选择专注，都是在投资未来的自己",
    ]
    
    # 强提醒
    strong_messages = [
        "已经过去很长时间了，你的计划还记得吗？",
        "今天的目标完成了多少？现在回来还不晚 ⏰",
        "时间是最公平的资源，你想怎么使用它？",
        "深呼吸，重新开始。你可以的 💪",
        "与其后悔浪费时间，不如现在就行动",
    ]
    
    import random
    
    if severity <= 1:
        return random.choice(mild_messages)
    elif severity == 2:
        return random.choice(moderate_messages)
    else:
        return random.choice(strong_messages)


def get_focus_bar(ratio: float, length: int = 10) -> str:
    """
    生成专注度进度条
    
    Args:
        ratio: 专注度比例 (0-1)
        length: 进度条长度
        
    Returns:
        进度条字符串
    """
    filled = int(ratio * length)
    empty = length - filled
    
    if ratio >= 0.7:
        char = "█"
    elif ratio >= 0.4:
        char = "▓"
    else:
        char = "░"
    
    percentage = int(ratio * 100)
    return f"[{char * filled}{'·' * empty}] {percentage}%"
