import json
import redis
import logging
from datetime import datetime, timedelta
from config import config

logger = logging.getLogger(__name__)


class RedisService:
    """Redis缓存服务，用于存储实时热搜数据"""

    def __init__(self):
        self.client = None
        self.connect()

    def connect(self):
        """连接Redis"""
        try:
            # 从配置读取Redis连接信息
            redis_host = getattr(config, 'REDIS_HOST', 'localhost')
            redis_port = getattr(config, 'REDIS_PORT', 6379)
            redis_db = getattr(config, 'REDIS_DB', 0)
            redis_password = getattr(config, 'REDIS_PASSWORD', None)

            self.client = redis.Redis(
                host=redis_host,
                port=redis_port,
                db=redis_db,
                password=123456,
                decode_responses=True,  # 自动解码
                socket_timeout=5,
                socket_connect_timeout=5
            )
            # 测试连接
            self.client.ping()
            logger.info(f"Redis连接成功: {redis_host}:{redis_port}/{redis_db}")
        except Exception as e:
            logger.error(f"Redis连接失败: {e}")
            self.client = None

    def is_connected(self):
        """检查Redis连接状态"""
        if not self.client:
            return False
        try:
            return self.client.ping()
        except:
            return False

    def cache_hot_events(self, events, expire_minutes=5):
        """缓存热搜事件列表"""
        if not self.is_connected():
            logger.warning("Redis未连接，跳过缓存")
            return False

        try:
            cache_key = "hot_events:realtime"
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            # 准备缓存数据
            cache_data = {
                'timestamp': timestamp,
                'events': events,
                'count': len(events)
            }

            # 存储到Redis
            self.client.setex(
                cache_key,
                timedelta(minutes=expire_minutes),
                json.dumps(cache_data, ensure_ascii=False)
            )
            logger.info(f"热搜事件已缓存: {len(events)}条")
            return True
        except Exception as e:
            logger.error(f"缓存热搜事件失败: {e}")
            return False

    def get_cached_hot_events(self):
        """获取缓存的实时热搜"""
        if not self.is_connected():
            return None

        try:
            cache_key = "hot_events:realtime"
            cached_data = self.client.get(cache_key)

            if cached_data:
                data = json.loads(cached_data)
                # 检查是否过期
                cache_time = datetime.strptime(data['timestamp'], '%Y-%m-%d %H:%M:%S')
                if (datetime.now() - cache_time).seconds < 300:  # 5分钟内有效
                    return data['events']
            return None
        except Exception as e:
            logger.error(f"获取缓存热搜失败: {e}")
            return None

    def cache_event_details(self, event_id, details, expire_hours=24):
        """缓存事件详情"""
        if not self.is_connected():
            return False

        try:
            cache_key = f"event:details:{event_id}"
            self.client.setex(
                cache_key,
                timedelta(hours=expire_hours),
                json.dumps(details, ensure_ascii=False)
            )
            return True
        except Exception as e:
            logger.error(f"缓存事件详情失败: {e}")
            return False

    def get_cached_event_details(self, event_id):
        """获取缓存的事件详情"""
        if not self.is_connected():
            return None

        try:
            cache_key = f"event:details:{event_id}"
            cached_data = self.client.get(cache_key)
            return json.loads(cached_data) if cached_data else None
        except Exception as e:
            logger.error(f"获取缓存事件详情失败: {e}")
            return None

    def cache_analysis_results(self, analysis_type, data, expire_hours=1):
        """缓存分析结果"""
        if not self.is_connected():
            return False

        try:
            cache_key = f"analysis:{analysis_type}"
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

            cache_data = {
                'timestamp': timestamp,
                'data': data
            }

            self.client.setex(
                cache_key,
                timedelta(hours=expire_hours),
                json.dumps(cache_data, ensure_ascii=False)
            )
            return True
        except Exception as e:
            logger.error(f"缓存分析结果失败: {e}")
            return False

    def get_cached_analysis(self, analysis_type):
        """获取缓存的分析结果"""
        if not self.is_connected():
            return None

        try:
            cache_key = f"analysis:{analysis_type}"
            cached_data = self.client.get(cache_key)

            if cached_data:
                data = json.loads(cached_data)
                cache_time = datetime.strptime(data['timestamp'], '%Y-%m-%d %H:%M:%S')
                if (datetime.now() - cache_time).seconds < 3600:  # 1小时内有效
                    return data['data']
            return None
        except Exception as e:
            logger.error(f"获取缓存分析失败: {e}")
            return None

    def clear_cache(self, pattern="*"):
        """清除缓存"""
        if not self.is_connected():
            return 0

        try:
            keys = self.client.keys(pattern)
            if keys:
                count = self.client.delete(*keys)
                logger.info(f"清除缓存: 删除{count}个键")
                return count
            return 0
        except Exception as e:
            logger.error(f"清除缓存失败: {e}")
            return 0

    def get_cache_stats(self):
        """获取缓存统计信息"""
        if not self.is_connected():
            return {}

        try:
            stats = {
                'total_keys': self.client.dbsize(),
                'hot_events_cached': bool(self.client.exists("hot_events:realtime")),
                'memory_info': self.client.info('memory'),
                'connected': True
            }
            return stats
        except Exception as e:
            logger.error(f"获取缓存统计失败: {e}")
            return {'connected': False}


# 创建全局Redis服务实例
redis_service = RedisService()