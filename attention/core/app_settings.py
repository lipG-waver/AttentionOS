"""
应用通用设置持久化模块

将用户偏好（开机自启、主题等）持久化到 data/app_settings.json。
"""
import json
import logging
import threading
from pathlib import Path
from typing import Any, Dict

from attention.config import Config

logger = logging.getLogger(__name__)

SETTINGS_FILE = Config.DATA_DIR / "app_settings.json"

_DEFAULTS: Dict[str, Any] = {
    "auto_start_enabled": False,
    "has_launched": False,   # 是否曾经启动过（用于首次启动检测）
    "theme": "dark",         # 界面主题：dark | light
}


class AppSettingsManager:
    """应用设置管理器 — 读写 app_settings.json"""

    def __init__(self):
        self._lock = threading.Lock()
        self._data: Dict[str, Any] = dict(_DEFAULTS)
        self._load()

    # ------------------------------------------------------------------
    # 内部读写
    # ------------------------------------------------------------------

    def _load(self):
        Config.ensure_dirs()
        if not SETTINGS_FILE.exists():
            return
        try:
            raw = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
            self._data.update(raw)
        except Exception as e:
            logger.warning(f"读取 app_settings.json 失败: {e}")

    def _save(self):
        Config.ensure_dirs()
        with self._lock:
            try:
                SETTINGS_FILE.write_text(
                    json.dumps(self._data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                logger.error(f"保存 app_settings.json 失败: {e}")

    # ------------------------------------------------------------------
    # 公开 API
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any):
        self._data[key] = value
        self._save()

    # ------ 开机自启 ------

    @property
    def auto_start_enabled(self) -> bool:
        return bool(self._data.get("auto_start_enabled", False))

    @auto_start_enabled.setter
    def auto_start_enabled(self, value: bool):
        self._data["auto_start_enabled"] = bool(value)
        self._save()

    # ------ 首次启动 ------

    @property
    def has_launched(self) -> bool:
        return bool(self._data.get("has_launched", False))

    def mark_launched(self):
        """标记已完成首次启动"""
        if not self._data.get("has_launched"):
            self._data["has_launched"] = True
            self._save()

    # ------ 界面主题 ------

    @property
    def theme(self) -> str:
        v = self._data.get("theme", "dark")
        return v if v in ("dark", "light") else "dark"

    @theme.setter
    def theme(self, value: str):
        if value in ("dark", "light"):
            self._data["theme"] = value
            self._save()


# 单例
_manager: "AppSettingsManager | None" = None
_manager_lock = threading.Lock()


def get_app_settings() -> AppSettingsManager:
    global _manager
    if _manager is None:
        with _manager_lock:
            if _manager is None:
                _manager = AppSettingsManager()
    return _manager
