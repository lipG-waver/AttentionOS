"""
系统托盘模块
提供系统托盘图标和菜单，管理应用生命周期

关键修复：
- macOS 上 pystray 依赖 AppKit/NSApplication，必须在主线程运行
- run_with_tray() 将其他服务放入后台线程，pystray.Icon.run() 在主线程阻塞
- 增加健壮的降级逻辑：如果 pystray 导入失败或运行失败，自动回退到无托盘模式
- 防止 tkinter 在 macOS 主进程中被意外导入（Python 3.13 + Tk 8.6 崩溃问题）
"""
import logging
import os
import sys
import platform
import threading
import webbrowser
from typing import Optional, Callable

logger = logging.getLogger(__name__)

SYSTEM = platform.system()

# ============================================================
# macOS tkinter 保护：设置环境变量标记，防止意外导入
# ============================================================
if SYSTEM == "Darwin":
    os.environ["ATTENTION_OS_NO_TKINTER"] = "1"

# 托盘图标依赖
TRAY_AVAILABLE = False
try:
    import pystray
    from pystray import MenuItem as item
    from PIL import Image, ImageDraw
    TRAY_AVAILABLE = True
except ImportError:
    logger.warning("pystray 未安装，托盘图标功能不可用")
    logger.warning("运行: pip install pystray pillow")


class TrayIcon:
    """系统托盘图标"""

    def __init__(
        self,
        on_open_dashboard: Optional[Callable] = None,
        on_toggle_monitoring: Optional[Callable] = None,
        on_quit: Optional[Callable] = None,
    ):
        self.on_open_dashboard = on_open_dashboard
        self.on_toggle_monitoring = on_toggle_monitoring
        self.on_quit = on_quit

        self.icon: Optional["pystray.Icon"] = None
        self.monitoring = True

    def create_icon_image(self, color: str = "#4ade80") -> "Image.Image":
        """创建托盘图标图像"""
        size = 64
        image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse([4, 4, size - 4, size - 4], fill=color)
        draw.ellipse([20, 20, size - 20, size - 20], fill="white")
        draw.ellipse([26, 26, size - 26, size - 26], fill=color)
        return image

    def _create_menu(self):
        return pystray.Menu(
            item("打开仪表盘", self._on_open_dashboard, default=True),
            item("─────────", None, enabled=False),
            item(
                lambda text: "⏸ 暂停监控" if self.monitoring else "▶ 恢复监控",
                self._on_toggle_monitoring,
            ),
            item("─────────", None, enabled=False),
            item("退出", self._on_quit),
        )

    def _on_open_dashboard(self, icon, item):
        if self.on_open_dashboard:
            self.on_open_dashboard()
        else:
            webbrowser.open("http://127.0.0.1:5000")

    def _on_toggle_monitoring(self, icon, item):
        self.monitoring = not self.monitoring
        color = "#4ade80" if self.monitoring else "#f87171"
        if self.icon:
            self.icon.icon = self.create_icon_image(color)
        if self.on_toggle_monitoring:
            self.on_toggle_monitoring(self.monitoring)

    def _on_quit(self, icon, item):
        self.stop()
        if self.on_quit:
            self.on_quit()

    def _build_icon(self) -> "pystray.Icon":
        """创建 pystray.Icon 实例（尚未 run）"""
        self.icon = pystray.Icon(
            name="AttentionAgent",
            icon=self.create_icon_image(),
            title="注意力管理Agent",
            menu=self._create_menu(),
        )
        return self.icon

    # ---------- 启动方式 ----------

    def run_on_main_thread(self, setup_callback: Optional[Callable] = None):
        """
        在当前（主）线程运行托盘图标。
        setup_callback 会在图标就绪后被调用（在 macOS 上是从主线程内部），
        可在里面启动后台服务线程。
        """
        if not TRAY_AVAILABLE:
            logger.warning("托盘图标不可用")
            return

        icon = self._build_icon()

        def on_setup(icon_ref):
            """pystray 图标就绪后的回调"""
            logger.info("托盘图标已就绪")
            if setup_callback:
                setup_callback()

        # icon.run(setup=on_setup) 在 macOS 上会阻塞主线程（运行 NSApplication RunLoop）
        icon.run(setup=on_setup)

    def start_in_thread(self):
        """
        在后台线程运行（仅 Windows/Linux 可靠使用）。
        macOS 上 pystray 需要主线程，不要用此方法。
        """
        if not TRAY_AVAILABLE:
            logger.warning("托盘图标不可用")
            return

        icon = self._build_icon()
        thread = threading.Thread(target=icon.run, daemon=True)
        thread.start()
        logger.info("托盘图标已在后台线程启动")

    def stop(self):
        if self.icon:
            try:
                self.icon.stop()
            except Exception:
                pass
            logger.info("托盘图标已停止")

    def update_status(self, status: str):
        if not self.icon:
            return
        color_map = {
            "productive": "#4ade80",
            "distracted": "#f87171",
            "paused": "#fbbf24",
        }
        color = color_map.get(status, "#60a5fa")
        try:
            self.icon.icon = self.create_icon_image(color)
        except Exception:
            pass

    def show_notification(self, title: str, message: str):
        if self.icon and hasattr(self.icon, "notify"):
            try:
                self.icon.notify(message, title)
            except Exception:
                pass


# ==================================================================
# AppManager — 管理所有后台服务的生命周期
# ==================================================================

class AppManager:
    def __init__(self):
        self.tray: Optional[TrayIcon] = None
        self.monitoring_enabled = True
        self.web_port = 5000
        self.agent = None

    # ---------- 启动后台服务（可以从任何线程调用） ----------

    def _setup_autostart_on_first_launch(self):
        """首次启动时自动设置开机自启（后台静默执行）"""
        try:
            from attention.core.app_settings import get_app_settings
            from attention.core.autostart_manager import AutoStartManager

            settings = get_app_settings()
            if not settings.has_launched:
                logger.info("首次启动，正在自动设置开机自启...")
                mgr = AutoStartManager()
                success = mgr.enable()
                settings.auto_start_enabled = success
                settings.mark_launched()
                if success:
                    logger.info("开机自启已自动配置")
                else:
                    logger.warning("开机自启自动配置失败（可在设置中手动开启）")
            else:
                settings.mark_launched()
        except Exception as e:
            logger.warning(f"首次启动自启动设置异常: {e}")

    def _start_background_services(self):
        """在后台线程中启动 Web、Agent、Break、Checkin、Overlay 等服务"""
        import time
        from attention.ui.web_server import run_server
        from attention.main import AttentionAgent

        # 首次启动自动配置开机自启（后台）
        threading.Thread(target=self._setup_autostart_on_first_launch, daemon=True).start()

        # Web 服务器
        web_thread = threading.Thread(
            target=run_server,
            kwargs={"host": "127.0.0.1", "port": self.web_port},
            daemon=True,
        )
        web_thread.start()
        logger.info(f"Web服务已启动: http://127.0.0.1:{self.web_port}")

        # 监控 Agent
        self.agent = AttentionAgent()
        agent_thread = threading.Thread(target=self.agent.start, daemon=True)
        agent_thread.start()

        # 番茄钟（尽早初始化，让浮窗立即显示）
        try:
            from attention.features.pomodoro import get_pomodoro
            get_pomodoro()
            logger.info("番茄钟已初始化（浮窗已启动）")
        except Exception as e:
            logger.warning(f"番茄钟初始化失败: {e}")

        # 休息提醒（通过对话悬浮窗，不再弹出原生对话框）
        try:
            from attention.features.break_reminder import start_break_reminder
            start_break_reminder()
            logger.info("休息提醒已启动")
        except Exception as e:
            logger.warning(f"休息提醒启动失败: {e}")

        # 每小时签到
        try:
            from attention.features.hourly_checkin import start_hourly_checkin
            start_hourly_checkin()
            logger.info("每小时签到已启动")
        except Exception as e:
            logger.warning(f"每小时签到启动失败: {e}")

        # 统一对话悬浮窗（替代原桌面小精灵 + 番茄钟浮窗）
        try:
            from attention.ui.chat_overlay import start_chat_overlay
            start_chat_overlay()
            logger.info("对话悬浮窗已启动")
        except Exception as e:
            logger.warning(f"对话悬浮窗启动失败: {e}")

        # 自动打开浏览器
        time.sleep(1)
        webbrowser.open(f"http://127.0.0.1:{self.web_port}")

    # ---------- 带托盘启动（主线程被 pystray 阻塞） ----------

    def start_with_tray(self):
        """
        在主线程运行 pystray，后台服务在 setup 回调中启动。
        这样 macOS 的 AppKit RunLoop 可以正常工作。
        """
        self.tray = TrayIcon(
            on_open_dashboard=self._open_dashboard,
            on_toggle_monitoring=self._toggle_monitoring,
            on_quit=self._quit,
        )

        def setup_services():
            # setup 回调可能在主线程中被调用（macOS），
            # 所以把真正的服务启动放到一个新线程里
            threading.Thread(target=self._start_background_services, daemon=True).start()

        try:
            # 这会阻塞主线程（macOS 上运行 NSApplication RunLoop）
            self.tray.run_on_main_thread(setup_callback=setup_services)
        except Exception as e:
            logger.error(f"托盘图标运行失败: {e}，回退到无托盘模式")
            self._start_background_services()
            self._keep_alive()

    # ---------- 无托盘启动 ----------

    def start_without_tray(self):
        self._start_background_services()
        self._keep_alive()

    # ---------- 主线程保活 ----------

    def _keep_alive(self):
        import time
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self._quit()

    # ---------- 回调 ----------

    def _open_dashboard(self):
        webbrowser.open(f"http://127.0.0.1:{self.web_port}")

    def _toggle_monitoring(self, enabled: bool):
        self.monitoring_enabled = enabled
        if self.agent:
            if enabled:
                if not self.agent.running:
                    threading.Thread(target=self.agent.start, daemon=True).start()
            else:
                self.agent.stop()
        status = "运行中" if enabled else "已暂停"
        logger.info(f"监控状态: {status}")
        if self.tray:
            self.tray.show_notification("注意力管理Agent", f"监控已{'恢复' if enabled else '暂停'}")

    def _quit(self):
        logger.info("正在退出应用...")
        if self.agent:
            self.agent.stop()
        try:
            from attention.core.activity_monitor import stop_activity_monitoring
            stop_activity_monitoring()
        except Exception:
            pass
        try:
            from attention.features.hourly_checkin import stop_hourly_checkin
            stop_hourly_checkin()
        except Exception:
            pass
        try:
            from attention.ui.chat_overlay import stop_chat_overlay
            stop_chat_overlay()
        except Exception:
            pass
        if self.tray:
            self.tray.stop()
        sys.exit(0)


# ==================================================================
# 公开入口函数
# ==================================================================

def run_with_tray():
    """带托盘图标运行应用（主入口）"""
    from attention.utils import setup_logging
    setup_logging(logging.INFO)
    logger.info("启动注意力管理Agent（带托盘图标）")
    app = AppManager()
    app.start_with_tray()


def run_without_tray():
    """不带托盘图标运行（调试模式）"""
    from attention.utils import setup_logging
    setup_logging(logging.INFO)

    print("\n" + "=" * 60)
    print("注意力管理Agent - 调试模式（无托盘）")
    print("=" * 60)
    print(f"仪表盘地址: http://127.0.0.1:5000")
    print("按 Ctrl+C 退出")
    print("=" * 60 + "\n")

    app = AppManager()
    app.start_without_tray()


if __name__ == "__main__":
    if TRAY_AVAILABLE:
        run_with_tray()
    else:
        print("pystray 未安装，使用调试模式启动")
        run_without_tray()
