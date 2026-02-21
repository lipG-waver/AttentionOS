"""
屏幕截图模块
跨平台支持：Windows / macOS / Linux / WSL2

截图优先级（自动按平台选择最优方案）：
  Windows / macOS : mss  →  PIL ImageGrab  →  pyscreenshot
  Linux 原生       : pyscreenshot  →  mss  →  PIL ImageGrab
  WSL2            : PowerShell（调用 Windows GDI+）→ pyscreenshot → mss → PIL
"""
import io
import logging
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

try:
    from PIL import ImageGrab, Image
except ImportError:
    ImageGrab = None
    Image = None

try:
    import mss
    import mss.tools
except ImportError:
    mss = None

try:
    import pyscreenshot
except ImportError:
    pyscreenshot = None

from attention.config import Config

logger = logging.getLogger(__name__)

# 平台判断
_IS_LINUX = sys.platform.startswith("linux")
_IS_WSL2  = _IS_LINUX and Path("/proc/version").exists() and \
            "microsoft" in Path("/proc/version").read_text().lower()


class ScreenCapture:
    """屏幕截图类，自动适配当前平台"""

    def __init__(self):
        self.config = Config
        self._methods = self._build_method_list()
        if not self._methods:
            raise ImportError("找不到可用的截图库，请运行: pip install pillow mss pyscreenshot")

    def _build_method_list(self) -> list:
        """
        按平台构建截图方法优先级列表。
        - WSL2: mss/PIL 的 XGetImage 在 WSLg 虚拟 X 下失败；
                grim 的 wlr-screencopy 协议也不被 WSLg 支持。
                最可靠的方式是调用 powershell.exe 走 Windows GDI+ 截图。
        - Linux 原生: pyscreenshot 自动探测系统工具（scrot 等）。
        """
        if _IS_WSL2:
            candidates = [
                ("PowerShell",   self._capture_with_powershell,  shutil.which("powershell.exe")),
                ("pyscreenshot", self._capture_with_pyscreenshot, pyscreenshot),
                ("mss",          self._capture_with_mss,          mss),
                ("PIL",          self._capture_with_pil,          ImageGrab),
            ]
        elif _IS_LINUX:
            candidates = [
                ("pyscreenshot", self._capture_with_pyscreenshot, pyscreenshot),
                ("mss",          self._capture_with_mss,          mss),
                ("PIL",          self._capture_with_pil,          ImageGrab),
            ]
        else:  # Windows / macOS
            candidates = [
                ("mss",          self._capture_with_mss,          mss),
                ("PIL",          self._capture_with_pil,          ImageGrab),
                ("pyscreenshot", self._capture_with_pyscreenshot, pyscreenshot),
            ]
        return [(name, fn) for name, fn, dep in candidates if dep is not None]

    def capture(self, save: bool = None) -> Tuple[Optional[bytes], Optional[Path]]:
        """截取屏幕，自动使用当前平台最优方式，失败时依次回退。"""
        if save is None:
            save = self.config.SAVE_SCREENSHOTS

        last_error = None
        for name, method in self._methods:
            try:
                image_bytes, save_path = method(save)
                if image_bytes is not None:
                    logger.debug(f"截图成功（后端: {name}）")
                    return image_bytes, save_path
            except Exception as e:
                logger.debug(f"截图后端 [{name}] 失败: {e}")
                last_error = e

        logger.error(f"所有截图方式均失败，最后错误: {last_error}")
        return None, None

    # ------------------------------------------------------------------ #
    #  各截图后端实现                                                       #
    # ------------------------------------------------------------------ #

    def _capture_with_mss(self, save: bool) -> Tuple[Optional[bytes], Optional[Path]]:
        """mss：跨平台，Windows/macOS 首选，Linux 下偶尔受 X 环境限制"""
        with mss.mss() as sct:
            monitor = sct.monitors[0]  # 0 = 所有显示器合并区域
            shot = sct.grab(monitor)
            img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        return self._process_image(img, save)

    def _capture_with_powershell(self, save: bool) -> Tuple[Optional[bytes], Optional[Path]]:
        """
        PowerShell：调用 Windows 实机的 GDI+ 截图，WSL2 首选方案。
        不依赖任何 Linux 截图工具，通过 /mnt/c 读回截图文件。
        """
        tmp_win = r"C:\Temp\attentionos_shot.png"
        tmp_wsl = Path("/mnt/c/Temp/attentionos_shot.png")
        tmp_wsl.parent.mkdir(parents=True, exist_ok=True)

        ps_script = (
            "Add-Type -AssemblyName System.Drawing, System.Windows.Forms; "
            "$s = [System.Windows.Forms.SystemInformation]::VirtualScreen; "
            "$bmp = New-Object System.Drawing.Bitmap($s.Width, $s.Height); "
            "$g = [System.Drawing.Graphics]::FromImage($bmp); "
            "$g.CopyFromScreen($s.Left, $s.Top, 0, 0, $bmp.Size); "
            f"$bmp.Save('{tmp_win}'); "
            "$g.Dispose(); $bmp.Dispose()"
        )
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", ps_script],
            check=True,
            capture_output=True,
            timeout=15,
        )
        img = Image.open(tmp_wsl).convert("RGB")
        result = self._process_image(img, save)
        tmp_wsl.unlink(missing_ok=True)
        return result

    def _capture_with_pyscreenshot(self, save: bool) -> Tuple[Optional[bytes], Optional[Path]]:
        """pyscreenshot：自动探测后端（scrot 等系统工具），Linux 备选"""
        img = pyscreenshot.grab().convert("RGB")
        return self._process_image(img, save)

    def _capture_with_pil(self, save: bool) -> Tuple[Optional[bytes], Optional[Path]]:
        """PIL ImageGrab：Windows/macOS 可靠，Linux 下依赖 X11 环境"""
        img = ImageGrab.grab(all_screens=True).convert("RGB")
        return self._process_image(img, save)
    
    def _process_image(self, img: "Image.Image", save: bool) -> Tuple[Optional[bytes], Optional[Path]]:
        """处理截图图像"""
        # 转换为JPEG字节
        buffer = io.BytesIO()
        img.save(buffer, format="JPEG", quality=self.config.SCREENSHOT_QUALITY)
        image_bytes = buffer.getvalue()
        
        save_path = None
        if save:
            save_path = self._save_screenshot(img)
        
        return image_bytes, save_path
    
    def _save_screenshot(self, img: "Image.Image") -> Path:
        """保存截图到文件"""
        self.config.ensure_dirs()
        
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        filename = f"{timestamp}.jpg"
        filepath = self.config.SCREENSHOT_DIR / filename
        
        img.save(filepath, format="JPEG", quality=self.config.SCREENSHOT_QUALITY)
        logger.debug(f"截图已保存: {filepath}")
        
        return filepath
    
    def cleanup_old_screenshots(self):
        """清理过期的截图文件"""
        if not self.config.SCREENSHOT_DIR.exists():
            return
        
        from datetime import timedelta
        cutoff = datetime.now() - timedelta(days=self.config.MAX_SCREENSHOT_AGE_DAYS)
        
        for file in self.config.SCREENSHOT_DIR.glob("*.jpg"):
            try:
                # 从文件名解析时间
                time_str = file.stem  # 格式: 2024-01-20_14-30-00
                file_time = datetime.strptime(time_str, "%Y-%m-%d_%H-%M-%S")
                
                if file_time < cutoff:
                    file.unlink()
                    logger.debug(f"已删除过期截图: {file}")
            except (ValueError, OSError) as e:
                logger.warning(f"清理截图时出错: {file}, {e}")


# 模块级便捷函数
_capturer = None

def get_capturer() -> ScreenCapture:
    """获取截图器单例"""
    global _capturer
    if _capturer is None:
        _capturer = ScreenCapture()
    return _capturer

def capture_screen(save: bool = None) -> Tuple[Optional[bytes], Optional[Path]]:
    """截取屏幕的便捷函数"""
    return get_capturer().capture(save)
