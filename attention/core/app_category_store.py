"""
应用分类存储模块
持久化用户自定义的应用分类覆盖，存储于 data/app_categories.json
"""
import json
import logging
import threading
from typing import Dict, Optional

from attention.config import Config
from attention.core.state_fusion import categorize_app

logger = logging.getLogger(__name__)

CATEGORIES_FILE = Config.DATA_DIR / "app_categories.json"

VALID_CATEGORIES = {"work", "communication", "learning", "entertainment", "unknown"}

CATEGORY_DISPLAY = {
    "work": "工作",
    "communication": "沟通",
    "learning": "学习",
    "entertainment": "娱乐",
    "unknown": "其他",
}


class AppCategoryStore:
    """用户自定义应用分类存储（覆盖自动分类）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._overrides: Dict[str, str] = {}
        self._load()

    def _load(self):
        Config.ensure_dirs()
        if not CATEGORIES_FILE.exists():
            return
        try:
            data = json.loads(CATEGORIES_FILE.read_text(encoding="utf-8"))
            self._overrides = data.get("overrides", {})
        except Exception as e:
            logger.warning(f"读取 app_categories.json 失败: {e}")

    def _save(self):
        Config.ensure_dirs()
        with self._lock:
            try:
                CATEGORIES_FILE.write_text(
                    json.dumps({"overrides": self._overrides}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as e:
                logger.error(f"保存 app_categories.json 失败: {e}")

    def get_category(self, app_name: str, window_title: str = "") -> str:
        """获取应用分类，优先使用用户覆盖，否则自动推断"""
        key = app_name.lower().strip()
        if key in self._overrides:
            return self._overrides[key]
        return categorize_app(app_name, window_title)

    def set_category(self, app_name: str, category: str) -> bool:
        """设置用户自定义应用分类"""
        if category not in VALID_CATEGORIES:
            return False
        key = app_name.lower().strip()
        self._overrides[key] = category
        self._save()
        return True

    def get_all_overrides(self) -> Dict[str, str]:
        return dict(self._overrides)


_store: Optional[AppCategoryStore] = None
_store_lock = threading.Lock()


def get_app_category_store() -> AppCategoryStore:
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = AppCategoryStore()
    return _store
