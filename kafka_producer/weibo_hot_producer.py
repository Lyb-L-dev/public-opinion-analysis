import json
import logging
from config import config
from utils.kafka_utils import get_weibo_kafka_producer, send_weibo_data_to_kafka, close_kafka_producer

# 初始化日志（和项目其他模块格式统一，显示毫秒）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s,%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

class WeiboHotProducer:
    """
    微博热点Kafka生产者（项目统一入口）
    特性：1. 复用单例生产者 2. 兼容分主题/统一主题 3. 异步发送+异常捕获 4. API向前兼容
    配置切换：修改KAFKA_USE_MULTI_TOPIC为True/False即可切换多主题/统一主题
    """
    def __init__(self):
        # 【修复1】匹配config.py的配置键名，补充多主题开关配置
        self.KAFKA_BROKER_LIST = config.KAFKA_BROKER_LIST
        # 多主题开关：True=分3个主题发送（原有设计），False=统一主题+data_type（爬虫原方案）
        self.KAFKA_USE_MULTI_TOPIC = getattr(config, 'KAFKA_USE_MULTI_TOPIC', True)
        # 【修复2】分主题配置（保留原有设计，主题名可通过环境变量覆盖）
        self.topics = {
            'hot_events': getattr(config, 'KAFKA_HOT_EVENTS_TOPIC', 'weibo.hot.events'),
            'articles': getattr(config, 'KAFKA_ARTICLES_TOPIC', 'weibo.hot.articles'),
            'comments': getattr(config, 'KAFKA_COMMENTS_TOPIC', 'weibo.hot.comments')
        }
        # 【核心优化】复用utils/kafka_utils的单例生产者，不重复创建
        self.producer = get_weibo_kafka_producer()
        # 统一主题（从config读取，和爬虫保持一致）
        self.unified_topic = config.KAFKA_HOT_TOPIC
        logger.info(f"Kafka生产者初始化完成 | 多主题模式：{self.KAFKA_USE_MULTI_TOPIC}")
        if self.KAFKA_USE_MULTI_TOPIC:
            logger.info(f"分主题配置：{self.topics}")
        else:
            logger.info(f"统一主题配置：{self.unified_topic}")

    def _send_multi_topic(self, topic, key, data):
        """
        分主题发送私有方法（适配原有设计）：异步发送+key指定+异常捕获
        :param topic: 目标主题
        :param key: 消息key（保证相同key入相同分区）
        :param data: 发送数据（字典）
        """
        if not self.producer:
            logger.error("无有效Kafka单例生产者，发送失败")
            return False
        try:
            # 核心修复：key手动序列化为bytes（处理None情况），value保持原始字典（交给value_serializer）
            key_bytes = str(key).encode('utf-8') if key is not None else None
            future = self.producer.send(
                topic=topic,
                key=key_bytes,
                value=data  # 交给kafka_utils的value_serializer自动序列化
            )
            # 异步回调：成功/失败日志
            def on_success(record_metadata):
                logger.info(f"分主题发送成功 | 主题：{record_metadata.topic} | 分区：{record_metadata.partition} | 偏移量：{record_metadata.offset}")
            def on_error(excp):
                logger.error(f"分主题发送失败 | 主题：{topic} | 错误：{excp}", exc_info=True)
            future.add_callback(on_success)
            future.add_errback(on_error)
            return True
        except Exception as e:
            logger.error(f"分主题发送异常 | 主题：{topic} | 错误：{e}", exc_info=True)
            return False

    def send_event(self, event_data):
        """发送热点事件到Kafka【API兼容：保留原有方法名/入参】"""
        if not event_data or not isinstance(event_data, dict):
            logger.warning("发送事件失败：无效数据（非字典/空）")
            return False
        if self.KAFKA_USE_MULTI_TOPIC:
            return self._send_multi_topic(
                topic=self.topics['hot_events'],
                key=event_data.get('title'),
                data=event_data
            )
        else:
            return send_weibo_data_to_kafka(data_type='hot_event', data=event_data)

    def send_article(self, article_data):
        """发送文章/微博到Kafka【API兼容：保留原有方法名/入参】"""
        if not article_data or not isinstance(article_data, dict):
            logger.warning("发送文章失败：无效数据（非字典/空）")
            return False
        if self.KAFKA_USE_MULTI_TOPIC:
            return self._send_multi_topic(
                topic=self.topics['articles'],
                key=article_data.get('article_id'),
                data=article_data
            )
        else:
            return send_weibo_data_to_kafka(data_type='article', data=article_data)

    def send_comment(self, comment_data):
        """发送评论到Kafka【API兼容：保留原有方法名/入参】"""
        if not comment_data or not isinstance(comment_data, dict):
            logger.warning("发送评论失败：无效数据（非字典/空）")
            return False
        if self.KAFKA_USE_MULTI_TOPIC:
            return self._send_multi_topic(
                topic=self.topics['comments'],
                key=comment_data.get('comment_id'),
                data=comment_data
            )
        else:
            return send_weibo_data_to_kafka(data_type='comment', data=comment_data)

    def flush(self):
        """刷新缓冲区：确保所有待发送数据提交【保留原有方法】"""
        if self.producer:
            try:
                self.producer.flush()
                logger.info("Kafka生产者缓冲区已刷新")
            except Exception as e:
                logger.error(f"刷新缓冲区失败：{e}", exc_info=True)

    def close(self):
        """优雅关闭生产者【保留原有方法，复用kafka_utils的关闭逻辑】"""
        close_kafka_producer()
        logger.info("Kafka生产者已优雅关闭（统一入口）")
