from datetime import datetime
import logging
from utils.db_utils import with_db_connection, execute_query
from utils.text_utils import analyze_sentiment, get_sentiment_type

logger = logging.getLogger(__name__)


class Article:
    """文章模型 - 对应articles表"""

    def __init__(self, id=None, event_id=None, author=None, content=None,
                 publish_time=None, like_count=0, repost_count=0, comment_count=0,
                 article_id=None, crawl_time=None, sentiment_score=None, hot_title=None,
                 **kwargs):
        # 数据库原生字段
        self.id = id
        self.event_id = event_id
        self.author = author or ""
        self.content = content or ""
        self.publish_time = self._validate_datetime(publish_time)
        self.like_count = int(like_count) if like_count is not None else 0
        self.repost_count = int(repost_count) if repost_count is not None else 0
        self.comment_count = int(comment_count) if comment_count is not None else 0
        self.article_id = article_id or ""
        self.crawl_time = self._validate_datetime(crawl_time)
        self.sentiment_score = self._validate_sentiment_score(sentiment_score)
        self.hot_title = hot_title or ""

        # 业务计算字段
        self.sentiment_type = get_sentiment_type(self.sentiment_score)

    def _validate_datetime(self, dt_value):
        """校验并格式化日期时间对象"""
        if isinstance(dt_value, datetime):
            return dt_value
        if isinstance(dt_value, str):
            try:
                return datetime.strptime(dt_value, '%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                return None
        return None

    def _validate_sentiment_score(self, score):
        """校验情感分，强制限制在0-1区间"""
        try:
            score_float = float(score) if score is not None else 0.5
            return max(0.0, min(1.0, round(score_float, 3)))
        except (ValueError, TypeError):
            return 0.5

    @classmethod
    def create(cls, article_data):
        """新增文章入库"""

        @with_db_connection
        def _insert(conn):
            sql = """
                INSERT INTO articles 
                (event_id, author, content, publish_time, like_count, 
                 repost_count, comment_count, article_id, crawl_time, sentiment_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            params = (
                article_data.get('event_id'),
                article_data.get('author', ''),
                article_data.get('content', ''),
                article_data.get('publish_time', datetime.now()),
                article_data.get('like_count', 0),
                article_data.get('repost_count', 0),
                article_data.get('comment_count', 0),
                article_data.get('article_id', ''),
                article_data.get('crawl_time', datetime.now()),
                cls._validate_sentiment_score_static(
                    analyze_sentiment(article_data.get('content', ''))
                    if article_data.get('content', '').strip() else 0.5
                )
            )

            execute_query(conn, sql, params)
            conn.execute("SELECT LAST_INSERT_ID() as id")
            return conn.fetchone()['id']

        try:
            article_id = _insert()
            return cls(id=article_id, **article_data)
        except Exception as e:
            logger.error(f"文章入库失败：{e}")
            return None

    @classmethod
    def get_by_event_id(cls, event_id, limit=50):
        """根据事件ID获取文章"""

        @with_db_connection
        def _get(conn):
            sql = """
                SELECT * FROM articles 
                WHERE event_id = %s 
                ORDER BY publish_time DESC
                LIMIT %s
            """
            results = execute_query(conn, sql, (event_id, limit))
            return [cls(**data) for data in results]

        return _get()

    @classmethod
    def get_by_id(cls, article_id):
        """根据文章ID获取单条文章"""

        @with_db_connection
        def _get(conn):
            sql = "SELECT * FROM articles WHERE id = %s"
            result = execute_query(conn, sql, (article_id,), fetch_one=True)
            return cls(**result) if result else None

        return _get()

    def to_dict(self):
        """转换为字典"""
        return {
            'id': self.id,
            'event_id': self.event_id,
            'author': self.author,
            'content': self.content[:200] + "..." if self.content and len(self.content) > 200 else self.content,
            'publish_time': self.publish_time.strftime('%Y-%m-%d %H:%M:%S') if self.publish_time else None,
            'like_count': self.like_count,
            'repost_count': self.repost_count,
            'comment_count': self.comment_count,
            'sentiment_score': round(self.sentiment_score, 3),
            'sentiment_type': self.sentiment_type
        }

    @staticmethod
    def _validate_sentiment_score_static(score):
        """静态辅助方法：校验情感分"""
        try:
            score_float = float(score) if score is not None else 0.5
            return max(0.0, min(1.0, round(score_float, 3)))
        except (ValueError, TypeError):
            return 0.5