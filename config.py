import os
from datetime import timedelta
import secrets

# Flask配置
class Config:
    SECRET_KEY = secrets.token_hex(16)
    PERMANENT_SESSION_LIFETIME = timedelta(hours=1)

    # 数据库配置
    DB_HOST = os.getenv('DB_HOST', 'localhost')
    DB_PORT = int(os.getenv('DB_PORT', 3306))
    DB_NAME = os.getenv('DB_NAME', 'public_opinion_db')
    DB_USER = os.getenv('DB_USER', 'root')
    DB_PASSWORD = os.getenv('DB_PASSWORD', 'root')

    # 日志配置
    LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')

    # 缓存配置（2小时）
    CACHE_TIMEOUT = timedelta(hours=2)

    # 字体配置
    FONTS = ['SimHei', 'Microsoft YaHei', 'PingFang SC', 'STHeiti', 'WenQuanYi Micro Hei']

    # 安全配置
    SECURITY_QUESTIONS = [
        "您的出生地是哪里？",
        "您母亲的姓名是什么？",
        "您的小学名称是什么？",
        "您的第一个宠物名字是什么？",
        "您最喜欢的电影是什么？"
    ]

    # ===== Kafka配置（纯多主题模式） =====
    KAFKA_BROKER_LIST = os.getenv('KAFKA_BROKER', 'localhost:9092')
    KAFKA_BOOTSTRAP_SERVERS = KAFKA_BROKER_LIST  # 别名，兼容不同命名
    KAFKA_CONSUMER_GROUP = os.getenv('KAFKA_GROUP', 'weibo_hot_consumer_group')

    # 微博热搜多主题配置
    KAFKA_HOT_EVENTS_TOPIC = 'weibo.hot.events'  # 热搜事件主题
    KAFKA_ARTICLES_TOPIC = 'weibo.hot.articles'  # 文章主题
    KAFKA_COMMENTS_TOPIC = 'weibo.hot.comments'  # 评论主题
    KAFKA_HOT_RANK_TOPIC = 'weibo.hot.rank'

    # ===== 新增：Spark配置（本地测试用，生产改集群地址） =====
    SPARK_MASTER = os.getenv('SPARK_MASTER', 'local[2]')
    SPARK_APP_NAME = os.getenv('SPARK_APP_NAME', 'WeiboHotStreamingAnalysis')
    SPARK_CHECKPOINT_ROOT = os.getenv('SPARK_CHECKPOINT_ROOT', './spark_checkpoint')

    # ===== 新增：爬虫配置（解决硬编码，方便修改） =====
    EDGE_USER_DATA_DIR = os.getenv('EDGE_USER_DATA_DIR', 'C:/Users/刘烨宝/AppData/Local/Microsoft/Edge/User Data/Default')
    EDGE_USER_DATA_DIR_HOT_SEARCH = os.getenv('EDGE_USER_DATA_DIR_HOT_SEARCH', 'C:/Users/刘烨宝/AppData/Local/Microsoft/Edge/User Data/HotSearch')
    EDGE_DRIVER_PATH = os.getenv('EDGE_DRIVER_PATH', r'D:\edgedriver\msedgedriver.exe')
    WEIBO_HOT_URL = os.getenv('WEIBO_HOT_URL', 'https://s.weibo.com/top/summary?cate=socialevent')
    CRAWL_MAX_SCROLLS = int(os.getenv('CRAWL_MAX_SCROLLS', 50))
    CRAWL_NUM_HOT_SEARCHES = int(os.getenv('CRAWL_NUM_HOT_SEARCHES', 10))

    # ===== CSV导出配置 =====
    CSV_EXPORT_ENABLED = os.getenv('CSV_EXPORT_ENABLED', 'true').lower() == 'true'
    CSV_EXPORT_DIR = os.getenv('CSV_EXPORT_DIR', 'weibo_crawl_backup')

config = Config()