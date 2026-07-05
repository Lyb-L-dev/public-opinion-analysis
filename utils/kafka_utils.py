import json
import logging
import datetime  # 核心修复：添加datetime导入（必加）
from kafka import KafkaProducer
from kafka.errors import KafkaError  # 优化：导入Kafka专属异常，精准捕获
from config import config

# 初始化日志 + 基础配置（优化：无全局配置时也能打印日志）
logger = logging.getLogger(__name__)
if not logger.handlers:  # 避免重复添加处理器
    handler = logging.StreamHandler()
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

# 优化：创建全局生产者单例（缓存，避免频繁创建/关闭）
WEIBO_KAFKA_PRODUCER = None

def get_weibo_kafka_producer():
    """
    获取微博爬虫专用Kafka生产者实例（单例模式+解决中文乱码）
    首次调用创建实例，后续直接返回缓存的单例，提升性能
    """
    global WEIBO_KAFKA_PRODUCER
    # 若已有可用生产者，直接返回（核心优化：单例缓存）
    if WEIBO_KAFKA_PRODUCER:
        return WEIBO_KAFKA_PRODUCER

    try:
        producer = KafkaProducer(
            bootstrap_servers=config.KAFKA_BROKER_LIST,  # 从配置文件读取broker
            value_serializer=lambda v: json.dumps(v, ensure_ascii=False).encode('utf-8'),  # 解决中文乱码
            retries=3,  # 发送失败重试3次
            batch_size=16384,  # 批量发送阈值（16K）
            linger_ms=100,  # 延迟100ms批量发送，提升吞吐量
            api_version=(0, 10, 2),  # 优化：指定Kafka API版本，避免自动检测版本的兼容问题
            # 移除不支持的 connection_max_idle_ms 参数（核心修复）
        )
        # 缓存生产者单例
        WEIBO_KAFKA_PRODUCER = producer
        logger.info("Kafka生产者初始化成功（单例模式）")
        return producer
    except KafkaError as e:  # 优化：精准捕获Kafka专属异常
        logger.error(f"Kafka生产者初始化失败（Kafka专属异常）：{e}")
        return None
    except Exception as e:  # 兜底捕获其他异常
        logger.error(f"Kafka生产者初始化失败（未知异常）：{e}")
        return None

def send_weibo_data_to_kafka(topic, data):
    """
    发送微博数据到指定Kafka主题（多主题模式）
    :param topic: 目标Kafka主题名称（如config.KAFKA_COMMENTS_TOPIC）
    :param data: 结构化数据（字典/列表），需非空
    :return: bool - 发送请求是否提交成功
    """
    # 非空校验（保留）
    if not isinstance(topic, str) or not topic.strip():
        logger.warning("Kafka主题无效（非字符串/空字符串），跳过发送")
        return False
    if not data:
        logger.warning(f"[{topic}] 无有效数据（空/None），跳过发送")
        return False
    if isinstance(data, list):
        data = [item for item in data if item]
        if not data:
            logger.warning(f"[{topic}] 数据列表全为空，过滤后无有效数据，跳过发送")
            return False

    producer = get_weibo_kafka_producer()
    if not producer:
        logger.error(f"[{topic}] 无有效Kafka生产者，跳过发送")
        return False

    try:
        # 简化消息体（删除data_type，多主题无需在消息内区分）
        message = {
            "send_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "data": data
        }

        # 异步发送到指定主题
        future = producer.send(
            topic=topic,  # 改用传入的多主题名称
            value=message
        )

        # 回调函数（调整日志标识为topic）
        def on_send_success(record_metadata):
            logger.info(
                f"[{topic}] 数据异步发送成功 | "
                f"主题：{record_metadata.topic} | 分区：{record_metadata.partition} | "
                f"偏移量：{record_metadata.offset} | 数据量：{len(data) if isinstance(data, list) else 1}"
            )

        def on_send_error(excp):
            logger.error(f"[{topic}] 数据异步发送失败：{excp}", exc_info=True)

        future.add_callback(on_send_success)
        future.add_errback(on_send_error)

        return True
    except KafkaError as e:
        logger.error(f"[{topic}] 发送数据到Kafka失败（Kafka专属异常）：{e}", exc_info=True)
        return False
    except Exception as e:
        logger.error(f"[{topic}] 发送数据到Kafka失败（未知异常）：{e}", exc_info=True)
        return False

def close_kafka_producer():
    """
    优雅关闭Kafka生产者（爬虫程序退出时调用，仅执行一次）
    用于程序正常终止时释放连接，避免资源泄漏
    """
    global WEIBO_KAFKA_PRODUCER
    if WEIBO_KAFKA_PRODUCER:
        try:
            WEIBO_KAFKA_PRODUCER.flush()  # 刷新缓冲区，确保所有待发送数据都提交
            WEIBO_KAFKA_PRODUCER.close()
            logger.info("Kafka生产者优雅关闭成功")
            WEIBO_KAFKA_PRODUCER = None
        except Exception as e:
            logger.error(f"Kafka生产者关闭失败：{e}", exc_info=True)

# 优化：程序退出时自动优雅关闭生产者（避免爬虫强制退出导致数据丢失）
import atexit
atexit.register(close_kafka_producer)