# services/cache_service.py
from datetime import datetime, timedelta
from typing import Any, Tuple
import threading
from config import config


class CacheService:
    """缓存服务"""

    def __init__(self):
        self._cache = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Any:
        """获取缓存"""
        with self._lock:
            if key in self._cache:
                entry = self._cache[key]
                # 兼容新旧格式：新格式3元素，元组或列表；旧格式2元素
                if len(entry) >= 3:
                    data, timestamp, ttl = entry[0], entry[1], entry[2]
                else:
                    data, timestamp = entry[0], entry[1]
                    ttl = None
                age = datetime.now() - timestamp
                if ttl is None and age < config.CACHE_TIMEOUT:
                    return data
                elif ttl is not None and age < timedelta(seconds=ttl):
                    return data
                else:
                    del self._cache[key]
            return None

    def set(self, key: str, data: Any, ttl: int = None) -> None:
        """设置缓存

        Args:
            key: 缓存键
            data: 缓存数据
            ttl: 过期时间（秒），None表示使用全局默认过期时间
        """
        with self._lock:
            self._cache[key] = (data, datetime.now(), ttl)

    def delete(self, key: str) -> None:
        """删除缓存"""
        with self._lock:
            if key in self._cache:
                del self._cache[key]

    def clear(self) -> None:
        """清空缓存"""
        with self._lock:
            self._cache.clear()

    def get_all_keys(self) -> list:
        """获取所有缓存键"""
        with self._lock:
            return list(self._cache.keys())


# 创建全局缓存实例
cache_service = CacheService()