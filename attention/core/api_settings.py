"""
API 设置持久化模块

将用户在 Web 页面配置的 API key 和提供商偏好持久化到本地 JSON 文件。
支持加载、保存、更新、测试 API key 等操作。
"""
import json
import logging
import threading
from pathlib import Path
from typing import Optional, Dict, Any

from attention.config import Config
from attention.core.llm_provider import get_llm_provider, LLMProvider

logger = logging.getLogger(__name__)

SETTINGS_FILE = Config.DATA_DIR / "api_settings.json"


class APISettingsManager:
    """API 设置管理器 — 读写 api_settings.json"""

    def __init__(self):
        self._lock = threading.Lock()
        self._load()

    def _load(self):
        """从磁盘加载设置并同步到 LLMProvider"""
        Config.ensure_dirs()
        if not SETTINGS_FILE.exists():
            return

        try:
            data = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"读取 API 设置失败: {e}")
            return

        provider_client = get_llm_provider()

        # 恢复各提供商的 API key
        providers = data.get("providers", {})
        for prov_name, prov_data in providers.items():
            api_key = prov_data.get("api_key", "")
            if api_key:
                provider_client.set_api_key(prov_name, api_key)

            # 恢复自定义配置
            custom = {}
            for field in ("api_base", "text_model", "vision_model"):
                if field in prov_data and prov_data[field]:
                    custom[field] = prov_data[field]
            if custom:
                provider_client.update_provider_config(prov_name, **custom)

        # 恢复激活的提供商
        active = data.get("active_provider", "")
        if active:
            provider_client.set_active_provider(active)

        logger.info(f"API 设置已加载，激活提供商: {provider_client.get_active_provider()}")

    def save(self):
        """持久化当前设置到磁盘"""
        provider_client = get_llm_provider()
        Config.ensure_dirs()

        data = {
            "active_provider": provider_client.get_active_provider(),
            "providers": {},
        }

        for prov in LLMProvider:
            cfg = provider_client.get_config(prov)
            if cfg:
                data["providers"][prov] = cfg.to_dict_with_key()

        with self._lock:
            try:
                SETTINGS_FILE.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                logger.debug("API 设置已保存")
            except Exception as e:
                logger.error(f"保存 API 设置失败: {e}")

    def set_api_key(self, provider: str, api_key: str) -> bool:
        """设置 API key 并持久化"""
        client = get_llm_provider()
        ok = client.set_api_key(provider, api_key)
        if ok:
            self.save()
        return ok

    def set_active_provider(self, provider: str) -> bool:
        """设置激活提供商并持久化"""
        client = get_llm_provider()
        ok = client.set_active_provider(provider)
        if ok:
            self.save()
        return ok

    def test_api_key(self, provider: str, api_key: Optional[str] = None) -> Dict[str, Any]:
        """测试 API key 连通性"""
        client = get_llm_provider()
        return client.test_api_key(provider, api_key)

    def get_all_configs(self):
        """获取所有提供商配置（前端安全）"""
        client = get_llm_provider()
        configs = client.get_all_configs()
        # 标记当前激活的
        active = client.get_active_provider()
        for c in configs:
            c["is_active"] = c["provider"] == active
        return configs


# 单例
_manager: Optional[APISettingsManager] = None


def get_api_settings() -> APISettingsManager:
    global _manager
    if _manager is None:
        _manager = APISettingsManager()
    return _manager
