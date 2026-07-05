import datetime
import logging
from utils.db_utils import with_db_connection, execute_query
from utils.text_utils import analyze_sentiment, get_sentiment_type

# 初始化日志器（替换原有print，方便线上问题排查）
logger = logging.getLogger(__name__)

class Comment:
    def __init__(self, id=None, username=None, user_id=None, content=None,
                 publish_time=None, like_count=None, comment_id=None, crawl_time=None,
                 event_id=None, sentiment_score=None, location=None, hot_title=None,
                 sentiment_type=None):
        # 数据库原生字段（一一对应，不做额外修改）
        self.id = id  # 主键 int auto_increment
        self.username = username or ""  # varchar(100) 非NULL
        self.user_id = user_id or ""  # varchar(50) 非NULL
        self.content = content or ""  # text 非NULL，兜底处理空字符串
        # 日期字段：数据库返回 datetime 对象，直接赋值无需转换，增加容错
        self.publish_time = self._validate_datetime(publish_time)
        self.crawl_time = self._validate_datetime(crawl_time)
        # like_count：允许NULL，默认0，兜底为整数0，强制转换避免非数字类型
        self.like_count = int(like_count) if like_count is not None else 0
        self.comment_id = comment_id or ""  # varchar(50) 唯一键 非NULL
        self.event_id = event_id  # int 允许NULL，有索引
        # 情感分：直接读取数据库 decimal(4,3)，转换为浮点数，强制限制0-1区间
        self.sentiment_score = self._validate_sentiment_score(sentiment_score)
        # location: varchar(50) 允许NULL，存储评论者地理位置
        self.location = location or ""
        # hot_title: varchar(255) 允许NULL，存储关联的热搜标题
        self.hot_title = hot_title or ""

        # 业务计算字段：基于数据库真实情感分动态生成，不覆盖原数据
        # 注意：sentiment_type 是计算字段，不从数据库读取，由 get_sentiment_type 自动计算
        self.sentiment_type = get_sentiment_type(self.sentiment_score)

    def _validate_datetime(self, dt_value):
        """辅助方法：校验并格式化日期时间对象，增加容错"""
        if isinstance(dt_value, datetime.datetime):
            return dt_value
        # 若为字符串，尝试解析（兼容部分异常数据）
        if isinstance(dt_value, str):
            try:
                return datetime.datetime.strptime(dt_value, '%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                return None
        # 非有效类型返回None
        return None

    def _validate_sentiment_score(self, score):
        """辅助方法：校验情感分，强制限制在0-1区间"""
        try:
            score_float = float(score) if score is not None else 0.5
            # 限制上下限，避免异常值影响统计
            return max(0.0, min(1.0, round(score_float, 3)))
        except (ValueError, TypeError):
            return 0.5

    def analyze_sentiment(self):
        """重新计算评论情感（仅手动调用，默认不触发，避免覆盖真实值）"""
        try:
            if not self.content.strip():
                self.sentiment_score = 0.5
                self.sentiment_type = "中性"
                return
            # 仅手动调用时重算，查询数据时不触发
            self.sentiment_score = analyze_sentiment(self.content)
            # 重算后仍需校验区间
            self.sentiment_score = self._validate_sentiment_score(self.sentiment_score)
            self.sentiment_type = get_sentiment_type(self.sentiment_score)
        except Exception as e:
            self.sentiment_score = 0.5
            self.sentiment_type = "中性"
            logger.warning(f"评论（ID：{self.id}）情感分析失败：{e}")

    @classmethod
    def create(cls, comment_data):
        """新增评论入库（仅入库真实计算的情感分，无 sentiment_type 字段）"""
        @with_db_connection
        def _insert(conn):
            # 提取数据并兜底，匹配数据库字段类型
            username = comment_data.get('username', "")
            user_id = comment_data.get('user_id', "")
            content = comment_data.get('content', "")
            publish_time = comment_data.get('publish_time', datetime.datetime.now())
            like_count = comment_data.get('like_count', 0)
            comment_id = comment_data.get('comment_id', "")
            crawl_time = comment_data.get('crawl_time', datetime.datetime.now())
            event_id = comment_data.get('event_id', None)

            # 计算情感分（仅入库，不影响查询后的真实值），增加内容非空判断
            sentiment_score = analyze_sentiment(content) if content.strip() else 0.5
            # 入库前校验情感分区间
            sentiment_score = cls._validate_sentiment_score_static(sentiment_score)

            # 参数化SQL（严格匹配数据库表字段）
            sql = """
                INSERT INTO comments 
                (username, user_id, content, publish_time, like_count, 
                 comment_id, crawl_time, event_id, sentiment_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            params = (
                username, user_id, content, publish_time, like_count,
                comment_id, crawl_time, event_id, sentiment_score
            )

            # 执行入库并返回主键id
            execute_query(conn, sql, params)
            conn.execute("SELECT LAST_INSERT_ID() as id")
            return conn.fetchone()['id']

        try:
            comment_id = _insert()
            return cls(id=comment_id, **comment_data)
        except Exception as e:
            logger.error(f"评论入库失败：{e}")
            return None

    @classmethod
    def batch_create(cls, comment_data_list):
        """批量新增评论入库（提升大量数据入库效率，避免循环单条插入）"""
        if not isinstance(comment_data_list, list) or len(comment_data_list) == 0:
            logger.warning("批量入库失败：传入的评论数据列表为空或非列表类型")
            return []

        @with_db_connection
        def _batch_insert(conn):
            # 构建批量插入SQL和参数列表
            sql = """
                INSERT INTO comments 
                (username, user_id, content, publish_time, like_count, 
                 comment_id, crawl_time, event_id, sentiment_score)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """
            params_list = []
            for comment_data in comment_data_list:
                username = comment_data.get('username', "")
                user_id = comment_data.get('user_id', "")
                content = comment_data.get('content', "")
                publish_time = comment_data.get('publish_time', datetime.datetime.now())
                like_count = comment_data.get('like_count', 0)
                comment_id = comment_data.get('comment_id', "")
                crawl_time = comment_data.get('crawl_time', datetime.datetime.now())
                event_id = comment_data.get('event_id', None)
                sentiment_score = analyze_sentiment(content) if content.strip() else 0.5
                sentiment_score = cls._validate_sentiment_score_static(sentiment_score)

                params_list.append((
                    username, user_id, content, publish_time, like_count,
                    comment_id, crawl_time, event_id, sentiment_score
                ))

            # 执行批量插入
            conn.executemany(sql, params_list)
            # 返回新增数据的主键（依赖数据库支持，MySQL可通过LAST_INSERT_ID() + 行数推导，此处简化返回成功标识）
            return len(params_list)

        try:
            success_count = _batch_insert()
            logger.info(f"批量入库成功，共新增 {success_count} 条评论")
            # 若需要返回实例列表，可后续补充查询逻辑
            return success_count
        except Exception as e:
            logger.error(f"评论批量入库失败：{e}")
            return 0

    @classmethod
    def get_all(cls, limit=None, event_id=None, offset=None, only_valid_content=True):
        """
        获取所有评论（增强版：支持分页、筛选有效内容）
        :param limit: 返回条数限制
        :param event_id: 按事件ID筛选
        :param offset: 分页偏移量（用于翻页）
        :param only_valid_content: 是否仅返回非空有效内容（默认True，过滤空评论）
        :return: Comment实例列表
        """
        @with_db_connection
        def _get(conn):
            sql = "SELECT * FROM comments"
            params = []
            where_conditions = []

            # 筛选有效内容（非空）
            if only_valid_content:
                where_conditions.append("content IS NOT NULL AND content != ''")
            # 按事件ID筛选
            if event_id:
                where_conditions.append("event_id = %s")
                params.append(event_id)
            # 拼接WHERE条件
            if where_conditions:
                sql += " WHERE " + " AND ".join(where_conditions)

            # 排序（按发布时间倒序）
            sql += " ORDER BY publish_time DESC"

            # 分页与条数限制
            if limit:
                sql += " LIMIT %s"
                params.append(limit)
                if offset:
                    sql = sql.replace("LIMIT %s", "LIMIT %s, %s")
                    params.insert(-1, offset)

            results = execute_query(conn, sql, params)
            # 实例化时直接使用数据库真实值，生成业务字段
            return [cls(**data) for data in results]

        return _get()

    @classmethod
    def get_by_id(cls, comment_id):
        """按评论主键ID获取单条评论（补充单条查询能力）"""
        if not comment_id:
            return None

        @with_db_connection
        def _get(conn):
            sql = "SELECT * FROM comments WHERE id = %s"
            result = execute_query(conn, sql, (comment_id,), fetch_one=True)
            return cls(**result) if result else None

        try:
            return _get()
        except Exception as e:
            logger.warning(f"获取评论（ID：{comment_id}）失败：{e}")
            return None

    @classmethod
    def get_by_event_id(cls, event_id, limit=100, only_valid_content=True):
        """根据事件ID获取评论（复用增强版get_all，保持逻辑统一）"""
        return cls.get_all(limit=limit, event_id=event_id, only_valid_content=only_valid_content)

    @classmethod
    def get_by_date_range(cls, start_date, end_date, limit=None, only_valid_content=True):
        """按发布日期范围筛选评论（补充日期范围查询能力）"""
        if not (start_date and end_date):
            logger.warning("日期范围查询失败：开始日期或结束日期为空")
            return []

        @with_db_connection
        def _get(conn):
            sql = "SELECT * FROM comments WHERE DATE(publish_time) BETWEEN %s AND %s"
            params = [start_date, end_date]

            # 筛选有效内容
            if only_valid_content:
                sql += " AND content IS NOT NULL AND content != ''"

            # 排序与条数限制
            sql += " ORDER BY publish_time DESC"
            if limit:
                sql += " LIMIT %s"
                params.append(limit)

            results = execute_query(conn, sql, params)
            return [cls(**data) for data in results]

        return _get()

    @classmethod
    def get_by_sentiment_range(cls, min_score, max_score, limit=None, only_valid_content=True):
        """按情感分区间筛选评论（补充情感分查询能力，适配舆情分析）"""
        # 校验情感分区间合法性
        min_score = max(0.0, float(min_score)) if min_score else 0.0
        max_score = min(1.0, float(max_score)) if max_score else 1.0
        if min_score > max_score:
            min_score, max_score = max_score, min_score

        @with_db_connection
        def _get(conn):
            sql = "SELECT * FROM comments WHERE sentiment_score BETWEEN %s AND %s"
            params = [min_score, max_score]

            # 筛选有效内容
            if only_valid_content:
                sql += " AND content IS NOT NULL AND content != ''"

            # 排序与条数限制
            sql += " ORDER BY publish_time DESC"
            if limit:
                sql += " LIMIT %s"
                params.append(limit)

            results = execute_query(conn, sql, params)
            return [cls(**data) for data in results]

        return _get()

    def to_dict(self):
        """转换为字典（增强版：加固日期格式化容错，字段更完整）"""
        return {
            'id': self.id,
            'event_id': self.event_id,
            'content': self.content,
            'username': self.username,
            'publish_time': self.publish_time.strftime('%Y-%m-%d %H:%M:%S')
            if self.publish_time else None,
            'crawl_time': self.crawl_time.strftime('%Y-%m-%d %H:%M:%S')
            if self.crawl_time else None,
            'like_count': self.like_count,
            'comment_id': self.comment_id,
            'sentiment_score': round(self.sentiment_score, 3),  # 匹配数据库 decimal(4,3) 格式
            'sentiment_type': self.sentiment_type,
            'location': self.location,
            'hot_title': self.hot_title,
            'is_valid_content': bool(self.content.strip())  # 新增：标记是否为有效内容
        }

    @staticmethod
    def _validate_sentiment_score_static(score):
        """静态辅助方法：校验情感分（供类方法调用）"""
        try:
            score_float = float(score) if score is not None else 0.5
            return max(0.0, min(1.0, round(score_float, 3)))
        except (ValueError, TypeError):
            return 0.5

    @staticmethod
    def get_count(event_id=None, only_valid_content=True):
        """获取评论总数（增强版：支持按事件ID、有效内容筛选）"""
        @with_db_connection
        def _get(conn):
            sql = "SELECT COUNT(*) as count FROM comments"
            params = []
            where_conditions = []

            if only_valid_content:
                where_conditions.append("content IS NOT NULL AND content != ''")
            if event_id:
                where_conditions.append("event_id = %s")
                params.append(event_id)

            if where_conditions:
                sql += " WHERE " + " AND ".join(where_conditions)

            result = execute_query(conn, sql, params, fetch_one=True)
            return result['count'] if result else 0
        return _get()

    @staticmethod
    def get_avg_likes(event_id=None, only_valid_content=True):
        """获取平均点赞数（增强版：支持按事件ID、有效内容筛选）"""
        @with_db_connection
        def _get(conn):
            sql = "SELECT AVG(IFNULL(like_count, 0)) as avg_likes FROM comments"
            params = []
            where_conditions = []

            if only_valid_content:
                where_conditions.append("content IS NOT NULL AND content != ''")
            if event_id:
                where_conditions.append("event_id = %s")
                params.append(event_id)

            if where_conditions:
                sql += " WHERE " + " AND ".join(where_conditions)

            result = execute_query(conn, sql, params, fetch_one=True)
            return round(result['avg_likes'] or 0, 1) if result else 0
        return _get()

    @staticmethod
    def get_avg_sentiment(event_id=None, only_valid_content=True):
        """新增：获取平均情感分（直接从数据库统计，高效准确，适配上层分析）"""
        @with_db_connection
        def _get(conn):
            sql = "SELECT AVG(IFNULL(sentiment_score, 0.5)) as avg_sent FROM comments"
            params = []
            where_conditions = []

            if only_valid_content:
                where_conditions.append("content IS NOT NULL AND content != ''")
            if event_id:
                where_conditions.append("event_id = %s")
                params.append(event_id)

            if where_conditions:
                sql += " WHERE " + " AND ".join(where_conditions)

            result = execute_query(conn, sql, params, fetch_one=True)
            return round(float(result['avg_sent'] or 0.5), 3) if result else 0.5
        return _get()

    @staticmethod
    def get_sentiment_count(event_id=None, only_valid_content=True):
        """新增：按情感类型统计数量（正面/中性/负面，适配舆情分布分析）"""
        @with_db_connection
        def _get(conn):
            sql = """
                SELECT
                    SUM(CASE WHEN sentiment_score > 0.7 THEN 1 ELSE 0 END) as positive,
                    SUM(CASE WHEN sentiment_score BETWEEN 0.3 AND 0.7 THEN 1 ELSE 0 END) as neutral,
                    SUM(CASE WHEN sentiment_score < 0.3 THEN 1 ELSE 0 END) as negative,
                    COUNT(*) as total
                FROM comments
            """
            params = []
            where_conditions = []

            if only_valid_content:
                where_conditions.append("content IS NOT NULL AND content != ''")
            if event_id:
                where_conditions.append("event_id = %s")
                params.append(event_id)

            if where_conditions:
                sql += " WHERE " + " AND ".join(where_conditions)

            result = execute_query(conn, sql, params, fetch_one=True)
            if not result or result['total'] == 0:
                return {'positive': 0, 'neutral': 0, 'negative': 0, 'total': 0}

            return {
                'positive': result['positive'] or 0,
                'neutral': result['neutral'] or 0,
                'negative': result['negative'] or 0,
                'total': result['total'] or 0
            }
        return _get()