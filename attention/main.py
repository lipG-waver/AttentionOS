"""
个人注意力管理Agent主程序
功能：监控屏幕和用户活动，分析工作状态，融合多信号判断，记录数据
"""
import argparse
import logging
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from attention.config import Config
from attention.core.screenshot import capture_screen, get_capturer
from attention.core.analyzer import analyze_screen, AnalysisResult
from attention.core.autostart_manager import AutoStartManager
from attention.core.database import save_to_database, get_database
from attention.core.activity_monitor import (
    get_activity_monitor,
    start_activity_monitoring,
    stop_activity_monitoring,
    ActivityState
)
from attention.core.state_fusion import fuse_state, FusedState
from attention.utils import (
    setup_logging,
    get_status_emoji,
    get_engagement_emoji,
    get_attention_color
)

logger = logging.getLogger(__name__)


def init_auto_start():
    """处理开机自启动命令"""
    if "--enable-auto-start" in sys.argv:
        manager = AutoStartManager()
        if manager.enable():
            print("已启用开机自启动")
        sys.exit(0)

    elif "--disable-auto-start" in sys.argv:
        manager = AutoStartManager()
        if manager.disable():
            print("已禁用开机自启动")
        sys.exit(0)


class AttentionAgent:
    """注意力管理Agent"""

    def __init__(self):
        self.config = Config
        self.running = False
        self.activity_monitor = None
        self._last_away_log: float = 0.0

        self.config.ensure_dirs()

    def start(self):
        """启动监控"""
        self.running = True

        # 启动活动监控
        if self.config.ACTIVITY_MONITOR["enabled"]:
            self.activity_monitor = get_activity_monitor()
            self.activity_monitor.sample_interval = self.config.ACTIVITY_MONITOR["sample_interval"]
            self.activity_monitor.history_size = self.config.ACTIVITY_MONITOR["history_size"]
            start_activity_monitoring()
            logger.info("活动监控已启动")

        logger.info("注意力管理Agent已启动")
        logger.info(f"截图分析间隔: {self.config.CHECK_INTERVAL}秒")
        logger.info(f"数据目录: {self.config.DATA_DIR}")

        print("\n" + "=" * 60)
        print("个人注意力管理Agent")
        print("=" * 60)
        print(f"  截图分析间隔: {self.config.CHECK_INTERVAL}秒")
        print(f"  活动监控: {'启用' if self.config.ACTIVITY_MONITOR['enabled'] else '禁用'}")
        print(f"  状态融合: 启用")
        print(f"  按 Ctrl+C 停止监控")
        print("=" * 60 + "\n")

        try:
            self._main_loop()
        except KeyboardInterrupt:
            self.stop()

    def stop(self):
        """停止监控"""
        self.running = False

        if self.config.ACTIVITY_MONITOR["enabled"]:
            stop_activity_monitoring()
            logger.info("活动监控已停止")

        logger.info("正在停止监控...")
        print("\n注意力管理Agent已停止")

    def _main_loop(self):
        """主循环"""
        while self.running:
            try:
                self._monitor_cycle()
            except Exception as e:
                logger.error(f"监控周期出错: {e}", exc_info=True)

            time.sleep(self.config.CHECK_INTERVAL)

    def _monitor_cycle(self):
        """单次监控周期"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        logger.debug(f"开始监控周期: {timestamp}")

        # 0. 持久离开检测
        if (
            self.activity_monitor
            and self.config.AWAY_SKIP.get("enabled", True)
        ):
            idle_seconds = self.activity_monitor.get_idle_duration()
            away_threshold = self.config.AWAY_SKIP.get("idle_threshold", 300)
            if idle_seconds >= away_threshold:
                self._log_away_status(idle_seconds)
                return

        # 1. 截图
        image_data, screenshot_path = capture_screen()
        if image_data is None:
            logger.warning("截图失败，跳过本次分析")
            return

        # 2. 截图分析
        analysis, raw_response = analyze_screen(image_data)

        # 3. 获取活动状态
        activity_state = None
        idle_duration = 0
        if self.activity_monitor:
            activity_state = self.activity_monitor.get_current_state(
                seconds=self.config.ACTIVITY_MONITOR["aggregation_window"]
            )
            idle_duration = self.activity_monitor.get_idle_duration()

        # 4. 状态融合
        fused = fuse_state(
            screen_analysis=analysis,
            activity_state=activity_state,
            idle_duration=idle_duration
        )

        # 5. 保存记录
        save_to_database(
            analysis=analysis,
            screenshot_path=screenshot_path,
            raw_response=raw_response,
            fused_state=fused.to_dict() if fused else None,
            activity_state=activity_state.to_dict() if activity_state else None
        )

        # 6. 显示结果
        self._display_result(analysis, activity_state, fused)

        # 7. 更新对话悬浮窗上下文
        if fused:
            active_app = activity_state.primary_window_app if activity_state else ""
            try:
                from attention.ui.chat_overlay import get_chat_overlay
                overlay = get_chat_overlay()
                overlay.update_mood(
                    is_productive=fused.is_productive,
                    is_distracted=fused.is_distracted,
                    attention_level=fused.attention_level
                )
                overlay.update_agent_context(
                    current_app=active_app,
                    is_productive=fused.is_productive,
                    is_distracted=fused.is_distracted,
                    attention_level=fused.attention_level,
                    distraction_duration_seconds=int(
                        fused.to_dict().get("distraction_duration", 0)
                    ) if hasattr(fused, "to_dict") else 0,
                )
            except Exception:
                pass

        # 8. 分心时通过对话提醒
        if fused and fused.needs_intervention:
            self._handle_intervention(fused)

    def _log_away_status(self, idle_seconds: int):
        now = time.time()
        log_interval = self.config.AWAY_SKIP.get("log_interval", 300)
        if now - self._last_away_log >= log_interval:
            time_str = datetime.now().strftime('%H:%M:%S')
            minutes = idle_seconds // 60
            print(f"\n[{time_str}] 💤 用户已离开 {minutes} 分钟，暂停截图和LLM分析...")
            logger.info(f"用户已持续空闲 {idle_seconds}s，跳过本次截图和LLM分析")
            self._last_away_log = now

    def _display_result(
        self,
        analysis: AnalysisResult,
        activity_state: Optional[ActivityState],
        fused: Optional[FusedState]
    ):
        """显示分析结果"""
        time_str = datetime.now().strftime('%H:%M:%S')

        status_emoji = get_status_emoji(analysis.work_status)
        print(f"\n[{time_str}] {status_emoji} {analysis.work_status}")

        if analysis.applications_detected:
            print(f"  📱 检测应用: {', '.join(analysis.applications_detected[:3])}")

        if activity_state:
            engagement_emoji = get_engagement_emoji(activity_state.engagement_level)
            print(f"  {engagement_emoji} 活动状态: {activity_state.engagement_level} "
                  f"(活动率 {activity_state.activity_ratio:.0%})")

            if activity_state.primary_window_app:
                print(f"  🪟 焦点窗口: {activity_state.primary_window_app}")

            if activity_state.window_switches > 5:
                print(f"  ⚡ 窗口切换: {activity_state.window_switches}次 (较频繁)")

        if fused:
            attention_color = get_attention_color(fused.attention_level)
            print(f"  {attention_color} 注意力: {fused.attention_level}")

            if fused.is_productive:
                print(f"  ✅ 状态: 生产性工作")
            elif fused.is_distracted:
                print(f"  ⚠️  状态: 注意力分散")

            if fused.needs_intervention:
                print(f"  🔔 需要提醒: {fused.intervention_reason}")

    def _handle_intervention(self, fused: FusedState):
        """处理介入提醒 → 通过对话悬浮窗发起对话"""
        logger.info(f"触发介入提醒: {fused.intervention_reason}")

        try:
            from attention.ui.chat_overlay import get_chat_overlay
            overlay = get_chat_overlay()
            overlay.show_nudge(
                reason=fused.intervention_reason,
                fused_state=fused.to_dict() if hasattr(fused, 'to_dict') else None,
            )
        except Exception as e:
            logger.warning(f"显示介入提醒失败: {e}")


def run_once():
    """执行一次分析（用于测试）"""
    setup_logging(logging.INFO)
    Config.ensure_dirs()

    print("执行单次分析...\n")

    activity_monitor = None
    if Config.ACTIVITY_MONITOR["enabled"]:
        activity_monitor = start_activity_monitoring()
        print("活动监控已启动，等待数据采集...")
        time.sleep(3)

    image_data, screenshot_path = capture_screen()
    if image_data is None:
        print("截图失败")
        return

    print(f"截图成功: {len(image_data)} bytes")
    if screenshot_path:
        print(f"截图保存: {screenshot_path}")

    print("\n正在分析...")
    analysis, raw_response = analyze_screen(image_data)

    activity_state = None
    idle_duration = 0
    if activity_monitor:
        activity_state = activity_monitor.get_current_state(30)
        idle_duration = activity_monitor.get_idle_duration()

    fused = fuse_state(analysis, activity_state, idle_duration)

    print("\n" + "=" * 50)
    print("截图分析结果")
    print("=" * 50)
    print(f"  工作状态: {analysis.work_status}")
    print(f"  活动窗口: {', '.join(analysis.applications_detected)}")
    print(f"  任务栏: {', '.join(analysis.taskbar_apps)}")
    print(f"  内容类型: {analysis.content_type}")
    print(f"  详情: {analysis.details}")

    if activity_state:
        print("\n" + "=" * 50)
        print("活动状态")
        print("=" * 50)
        print(f"  参与度: {activity_state.engagement_level}")
        print(f"  活动比例: {activity_state.activity_ratio:.0%}")
        print(f"  键盘事件: {activity_state.keyboard_events}")
        print(f"  鼠标事件: {activity_state.mouse_events}")
        print(f"  窗口切换: {activity_state.window_switches}次")
        print(f"  焦点应用: {activity_state.primary_window_app}")
        print(f"  空闲时长: {idle_duration}秒")

    print("\n" + "=" * 50)
    print("融合判断结果")
    print("=" * 50)
    print(f"  用户参与: {fused.user_engagement}")
    print(f"  注意力级别: {fused.attention_level}")
    print(f"  是否生产性: {fused.is_productive}")
    print(f"  是否分心: {fused.is_distracted}")
    print(f"  需要介入: {fused.needs_intervention}")
    if fused.needs_intervention:
        print(f"  介入原因: {fused.intervention_reason}")
    print(f"  置信度: {fused.confidence:.0%}")

    save_to_database(
        analysis, screenshot_path, raw_response,
        fused_state=fused.to_dict(),
        activity_state=activity_state.to_dict() if activity_state else None
    )
    print(f"\n记录已保存")

    if activity_monitor:
        stop_activity_monitoring()


def run_activity_test():
    """只测试活动监控（不截图）"""
    setup_logging(logging.INFO)

    print("活动监控测试模式")
    print("=" * 50)
    print("只监控键盘/鼠标活动和焦点窗口")
    print("按 Ctrl+C 停止\n")

    monitor = start_activity_monitoring()

    try:
        while True:
            time.sleep(5)

            state = monitor.get_current_state(30)
            snapshot = monitor.get_latest_snapshot()

            print(f"\n[{datetime.now().strftime('%H:%M:%S')}]")

            if snapshot:
                print(f"  焦点应用: {snapshot.active_window_app}")
                print(f"  窗口标题: {snapshot.active_window_title[:60]}")
                print(f"  键盘活动: {'是' if snapshot.keyboard_active else '否'}")
                print(f"  鼠标活动: {'是' if snapshot.mouse_active else '否'}")

            print(f"  [过去30秒]")
            print(f"  参与度: {state.engagement_level}")
            print(f"  活动比例: {state.activity_ratio:.0%}")
            print(f"  窗口切换: {state.window_switches}次")
            print(f"  空闲时长: {monitor.get_idle_duration()}秒")

    except KeyboardInterrupt:
        print("\n停止监控...")
        stop_activity_monitoring()


def main():
    """主函数"""
    parser = argparse.ArgumentParser(
        description="个人注意力管理Agent - 监控屏幕和活动，分析工作状态"
    )
    parser.add_argument("--once", "-o", action="store_true", help="执行一次分析后退出")
    parser.add_argument("--activity-test", action="store_true", help="只测试活动监控功能")
    parser.add_argument(
        "--interval", "-i", type=int, default=None,
        help=f"监控间隔（秒），默认{Config.CHECK_INTERVAL}"
    )
    parser.add_argument("--no-save", action="store_true", help="不保存截图文件")
    parser.add_argument("--no-activity", action="store_true", help="禁用活动监控")
    parser.add_argument("--verbose", "-v", action="store_true", help="显示详细日志")

    args = parser.parse_args()

    if args.interval:
        Config.CHECK_INTERVAL = args.interval
    if args.no_save:
        Config.SAVE_SCREENSHOTS = False
    if args.no_activity:
        Config.ACTIVITY_MONITOR["enabled"] = False

    log_level = logging.DEBUG if args.verbose else logging.INFO
    setup_logging(log_level)

    if args.activity_test:
        run_activity_test()
    elif args.once:
        run_once()
    else:
        agent = AttentionAgent()

        def signal_handler(sig, frame):
            agent.stop()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        agent.start()


if __name__ == "__main__":
    init_auto_start()
    if Config.AUTO_START.get("enabled"):
        print(f"程序配置为开机自启动 (服务名: {Config.AUTO_START.get('app_name', 'AttentionAgent')})")
    main()
