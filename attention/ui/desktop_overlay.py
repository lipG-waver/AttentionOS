"""
桌面悬浮窗模块 — 已废弃

此模块的所有功能已迁移到 ChatOverlay：

  - show_intervention() / 介入提醒  → ChatOverlay.show_nudge()
  - 番茄钟浮窗                       → ChatOverlay 计时器区域
  - 全屏休息遮罩                     → ChatOverlay.show_break_reminder()
                                       + break_overlay_process.py（保留备用）

保留此模块仅为历史兼容，**不应再扩展**。
如需全屏强制休息遮罩，参见 break_overlay_process.py。
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class DesktopOverlay:
    """已废弃 — 所有功能已迁移至 ChatOverlay。"""

    def start(self):
        logger.debug("DesktopOverlay.start() 已废弃，忽略")

    def stop(self):
        pass

    def get_state(self) -> dict:
        return {}


# ============================================================
# 单例（保留接口，避免旧引用崩溃）
# ============================================================

_overlay: Optional[DesktopOverlay] = None


def get_desktop_overlay() -> DesktopOverlay:
    global _overlay
    if _overlay is None:
        _overlay = DesktopOverlay()
    return _overlay


def start_desktop_overlay() -> DesktopOverlay:
    overlay = get_desktop_overlay()
    overlay.start()
    return overlay


def stop_desktop_overlay():
    global _overlay
    if _overlay:
        _overlay.stop()
