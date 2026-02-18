"""
屏幕截图模块
支持多显示器环境，可选择保存截图或仅返回内存数据
"""
import io
import logging
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

from attention.config import Config

logger = logging.getLogger(__name__)


class ScreenCapture:
    """屏幕截图类"""
    
    def __init__(self):
        self.config = Config
        self._check_dependencies()
    
    def _check_dependencies(self):
        """检查截图依赖"""
        if ImageGrab is None and mss is None:
            raise ImportError(
                "需要安装截图库。请运行: pip install pillow mss"
            )
    
    def capture(self, save: bool = None) -> Tuple[Optional[bytes], Optional[Path]]:
        """
        截取屏幕
        
        Args:
            save: 是否保存截图文件，None则使用配置默认值
            
        Returns:
            Tuple[图像字节数据, 保存路径（如果保存了的话）]
        """
        if save is None:
            save = self.config.SAVE_SCREENSHOTS
        
        try:
            # 优先使用mss（跨平台且性能更好）
            if mss is not None:
                return self._capture_with_mss(save)
            else:
                return self._capture_with_pil(save)
        except Exception as e:
            logger.error(f"截图失败: {e}")
            return None, None
    
    def _capture_with_mss(self, save: bool) -> Tuple[Optional[bytes], Optional[Path]]:
        """使用mss库截图"""
        with mss.mss() as sct:
            # 获取所有显示器的边界
            monitor = sct.monitors[0]  # 0是所有显示器的组合
            screenshot = sct.grab(monitor)
            
            # 转换为PIL Image
            img = Image.frombytes("RGB", screenshot.size, screenshot.bgra, "raw", "BGRX")
            
            return self._process_image(img, save)
    
    def _capture_with_pil(self, save: bool) -> Tuple[Optional[bytes], Optional[Path]]:
        """使用PIL库截图"""
        # PIL的ImageGrab在Windows上可以捕获所有显示器
        img = ImageGrab.grab(all_screens=True)
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
