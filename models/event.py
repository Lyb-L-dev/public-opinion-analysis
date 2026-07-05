from datetime import datetime
import logging
from utils.db_utils import with_db_connection, execute_query
from utils.text_utils import analyze_sentiment, get_sentiment_type
from models.comment import Comment  # 显式导入Comment，强化关联逻辑

# 初始化日志器
logger = logging.getLogger(__name__)

class Event:
    """事件模型 - 精准匹配数据库字段，强化与评论的关联分析"""

    # 配置常量（可根据业务调整）
    HIGH_HEAT_THRESHOLD = 1000  # 高热度事件阈值
    DEFAULT_SENTIMENT_SCORE = 0.5  # 默认情感分

    # 参数名严格匹配hot_events表字段
    def __init__(self, id=None, title=None, crawl_time=None,
                 heat=None, comment_count=0, sentiment_score=None, sentiment_type=None):
        # 实例属性与数据库字段一一对应，添加兜底和类型校验
        self.id = id  # 主键id
        self.title = title or ""  # 标题兜底为空字符串
        self.crawl_time = self._validate_datetime(crawl_time)  # 校验日期时间
        self.heat = self._validate_heat(heat)  # 校验热度值
        # 关联字段（评论数，非数据库原生）
        self.comment_count = int(comment_count) if comment_count is not None else 0
        # 业务字段（情感分析），校验并兜底
        self.sentiment_score = self._validate_sentiment_score(sentiment_score)
        self.sentiment_type = sentiment_type or get_sentiment_type(self.sentiment_score)

    def _validate_datetime(self, dt_value):
        """辅助方法：校验并格式化日期时间对象，增加容错"""
        if isinstance(dt_value, datetime):
            return dt_value
        # 若为字符串，尝试解析常见格式
        if isinstance(dt_value, str):
            try:
                return datetime.strptime(dt_value, '%Y-%m-%d %H:%M:%S')
            except (ValueError, TypeError):
                try:
                    return datetime.strptime(dt_value, '%Y-%m-%d')
                except (ValueError, TypeError):
                    return None
        # 非有效类型返回None
        return None

    def _validate_heat(self, heat):
        """辅助方法：校验热度值，确保为非负整数"""
        try:
            heat_int = int(heat) if heat is not None else 0
            return max(0, heat_int)  # 热度不能为负数
        except (ValueError, TypeError):
            return 0

    def _validate_sentiment_score(self, score):
        """辅助方法：校验情感分，强制限制在0-1区间"""
        try:
            score_float = float(score) if score is not None else self.DEFAULT_SENTIMENT_SCORE
            return max(0.0, min(1.0, round(score_float, 3)))
        except (ValueError, TypeError):
            return self.DEFAULT_SENTIMENT_SCORE

    @classmethod
    def create(cls, event_data):
        """新增事件入库（匹配hot_events表字段，支持自动关联评论数初始值）"""
        @with_db_connection
        def _insert(conn):
            # 提取数据并兜底，匹配数据库字段类型
            title = event_data.get('title', "")
            crawl_time = event_data.get('crawl_time', datetime.now())
            heat = event_data.get('heat', 0)
            # 可选：入库时可预先计算初始情感分（基于标题，或默认0.5）
            sentiment_score = event_data.get('sentiment_score', cls.DEFAULT_SENTIMENT_SCORE)
            sentiment_score = cls._validate_sentiment_score_static(sentiment_score)

            # 参数化SQL（避免注入风险，严格匹配数据库表字段）
            sql = """
                INSERT INTO hot_events 
                (title, crawl_time, heat, sentiment_score)
                VALUES (%s, %s, %s, %s)
            """
            params = (title, crawl_time, cls._validate_heat_static(heat), sentiment_score)

            # 执行入库并返回主键id
            execute_query(conn, sql, params)
            conn.execute("SELECT LAST_INSERT_ID() as id")
            return conn.fetchone()['id']

        try:
            event_id = _insert()
            if not event_id:
                return None
            # 实例化事件，自动补充评论数（初始为0，后续可通过calculate_comment_count更新）
            event_instance = cls(id=event_id, **event_data)
            logger.info(f"事件入库成功（ID：{event_id}，标题：{event_instance.title[:20]}...）")
            return event_instance
        except Exception as e:
            logger.error(f"事件入库失败：{e}")
            return None

    @classmethod
    def batch_create(cls, event_data_list):
        """批量新增事件入库（提升大量爬虫数据入库效率）"""
        if not isinstance(event_data_list, list) or len(event_data_list) == 0:
            logger.warning("批量入库失败：传入的事件数据列表为空或非列表类型")
            return 0

        @with_db_connection
        def _batch_insert(conn):
            # 构建批量插入SQL和参数列表
            sql = """
                INSERT INTO hot_events 
                (title, crawl_time, heat, sentiment_score)
                VALUES (%s, %s, %s, %s)
            """
            params_list = []
            for event_data in event_data_list:
                title = event_data.get('title', "")
                crawl_time = event_data.get('crawl_time', datetime.now())
                heat = event_data.get('heat', 0)
                sentiment_score = event_data.get('sentiment_score', cls.DEFAULT_SENTIMENT_SCORE)

                # 数据校验
                heat_valid = cls._validate_heat_static(heat)
                sentiment_score_valid = cls._validate_sentiment_score_static(sentiment_score)

                params_list.append((title, crawl_time, heat_valid, sentiment_score_valid))

            # 执行批量插入
            conn.executemany(sql, params_list)
            return len(params_list)

        try:
            success_count = _batch_insert()
            logger.info(f"事件批量入库成功，共新增 {success_count} 条事件")
            return success_count
        except Exception as e:
            logger.error(f"事件批量入库失败：{e}")
            return 0

    @classmethod
    def get_all(cls, limit=None, search_query=None, offset=None, only_high_heat=False):
        """
        获取所有事件（增强版：支持分页、关键词搜索、高热度筛选）
        :param limit: 返回条数限制
        :param search_query: 标题关键词搜索
        :param offset: 分页偏移量
        :param only_high_heat: 是否仅返回高热度事件（默认False）
        :return: Event实例列表
        """
        @with_db_connection
        def _get(conn):
            sql = """
                SELECT he.*, COUNT(c.id) as comment_count
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
            """
            params = []
            where_conditions = []

            # 关键词搜索（标题模糊匹配）
            if search_query:
                where_conditions.append("he.title LIKE %s")
                params.append(f"%{search_query}%")

            # 高热度事件筛选
            if only_high_heat:
                where_conditions.append(f"he.heat >= %s")
                params.append(cls.HIGH_HEAT_THRESHOLD)

            # 拼接WHERE条件
            if where_conditions:
                sql += " WHERE " + " AND ".join(where_conditions)

            # 分组、排序、分页
            sql += " GROUP BY he.id ORDER BY he.heat DESC"
            if limit:
                sql += " LIMIT %s"
                params.append(limit)
                if offset:
                    sql = sql.replace("LIMIT %s", "LIMIT %s, %s")
                    params.insert(-1, offset)

            # 修复原有SQL注入风险（limit改为参数化）
            results = execute_query(conn, sql, params)

            events = []
            for data in results:
                # 补全缺失字段并实例化
                events.append(cls(**data))
            return events

        return _get()

    @classmethod
    def get_by_id(cls, event_id):
        """根据ID获取事件（增强容错，补充情感统计）"""
        if not event_id:
            logger.warning("获取事件失败：事件ID为空")
            return None

        @with_db_connection
        def _get(conn):
            sql = """
                SELECT he.*, COUNT(c.id) as comment_count
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
                WHERE he.id = %s
                GROUP BY he.id
            """
            data = execute_query(conn, sql, (event_id,), fetch_one=True)
            return cls(**data) if data else None

        try:
            event = _get()
            if event:
                # 自动补全情感类型（若缺失）
                if not event.sentiment_type:
                    event.sentiment_type = get_sentiment_type(event.sentiment_score)
            return event
        except Exception as e:
            logger.warning(f"获取事件（ID：{event_id}）失败：{e}")
            return None

    @classmethod
    def get_by_title(cls, title, exact_match=False):
        """按标题查询事件（支持精准/模糊匹配）"""
        if not title:
            return None

        @with_db_connection
        def _get(conn):
            if exact_match:
                sql = """
                    SELECT he.*, COUNT(c.id) as comment_count
                    FROM hot_events he
                    LEFT JOIN comments c ON he.id = c.event_id
                    WHERE he.title = %s
                    GROUP BY he.id
                """
                params = (title,)
            else:
                sql = """
                    SELECT he.*, COUNT(c.id) as comment_count
                    FROM hot_events he
                    LEFT JOIN comments c ON he.id = c.event_id
                    WHERE he.title LIKE %s
                    GROUP BY he.id
                """
                params = (f"%{title}%",)

            data = execute_query(conn, sql, params, fetch_one=True)
            return cls(**data) if data else None

        return _get()

    @classmethod
    def get_by_crawl_date_range(cls, start_date, end_date, limit=None, only_high_heat=False):
        """按爬取日期范围筛选事件（适配时间维度分析）"""
        if not (start_date and end_date):
            logger.warning("日期范围查询失败：开始日期或结束日期为空")
            return []

        @with_db_connection
        def _get(conn):
            sql = """
                SELECT he.*, COUNT(c.id) as comment_count
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
                WHERE DATE(he.crawl_time) BETWEEN %s AND %s
            """
            params = [start_date, end_date]
            where_conditions = []

            # 高热度筛选
            if only_high_heat:
                where_conditions.append(f"he.heat >= %s")
                params.append(cls.HIGH_HEAT_THRESHOLD)

            if where_conditions:
                sql += " AND " + " AND ".join(where_conditions)

            # 分组、排序、分页
            sql += " GROUP BY he.id ORDER BY he.heat DESC"
            if limit:
                sql += " LIMIT %s"
                params.append(limit)

            results = execute_query(conn, sql, params)
            return [cls(**data) for data in results]

        return _get()

    @classmethod
    def get_high_risk_events(cls, limit=10, threshold=0.3):
        """获取高风险事件（情感分低于阈值，适配仪表盘风险预警）"""
        @with_db_connection
        def _get(conn):
            sql = """
                SELECT he.*, COUNT(c.id) as comment_count, AVG(c.sentiment_score) as avg_sent
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
                GROUP BY he.id
                HAVING avg_sent < %s OR he.sentiment_score < %s
                ORDER BY he.heat DESC
                LIMIT %s
            """
            params = (threshold, threshold, limit)
            results = execute_query(conn, sql, params)
            return [cls(**data) for data in results]

        return _get()

    def calculate_comment_count(self):
        """更新事件关联的评论数（从数据库同步，确保数据准确）"""
        try:
            self.comment_count = Comment.get_count(event_id=self.id)
            logger.info(f"事件（ID：{self.id}）评论数更新完成：{self.comment_count} 条")
        except Exception as e:
            logger.warning(f"事件（ID：{self.id}）评论数更新失败：{e}")

    def calculate_sentiment(self, use_db_stat=True, limit=100):
        """
        计算事件情感倾向（增强版：优先数据库统计，可选手动重算）
        :param use_db_stat: 是否使用数据库统计（默认True，高效准确）
        :param limit: 手动重算时的评论条数限制
        """
        if not self.id:
            logger.warning("计算事件情感失败：事件ID为空")
            self.sentiment_score = self.DEFAULT_SENTIMENT_SCORE
            self.sentiment_type = "中性"
            return

        # 优先使用数据库统计（更高效，适合大量数据）
        if use_db_stat:
            try:
                avg_sent = Comment.get_avg_sentiment(event_id=self.id)
                self.sentiment_score = self._validate_sentiment_score(avg_sent)
                self.sentiment_type = get_sentiment_type(self.sentiment_score)
                logger.info(f"事件（ID：{self.id}）情感分通过数据库统计更新完成")
                return
            except Exception as e:
                logger.warning(f"事件（ID：{self.id}）数据库情感统计失败，切换为手动重算：{e}")

        # 手动重算（备用方案，适配特殊场景）
        try:
            comments = Comment.get_by_event_id(self.id, limit=limit)
            if not comments:
                self.sentiment_score = self.DEFAULT_SENTIMENT_SCORE
                self.sentiment_type = "中性"
                return

            sentiment_scores = []
            for comment in comments:
                if comment.content and comment.content.strip():
                    try:
                        score = analyze_sentiment(comment.content)
                        sentiment_scores.append(score)
                    except Exception as e:
                        continue

            if sentiment_scores:
                self.sentiment_score = self._validate_sentiment_score(sum(sentiment_scores) / len(sentiment_scores))
                self.sentiment_type = get_sentiment_type(self.sentiment_score)
            else:
                self.sentiment_score = self.DEFAULT_SENTIMENT_SCORE
                self.sentiment_type = "中性"
        except Exception as e:
            self.sentiment_score = self.DEFAULT_SENTIMENT_SCORE
            self.sentiment_type = "中性"
            logger.error(f"事件（ID：{self.id}）手动重算情感失败：{e}")

    def to_dict(self):
        """转换为字典（增强版：加固容错，补充业务字段，适配前端展示）"""
        return {
            'id': self.id,
            'title': self.title,
            'summary': self.title[:50] + "..." if self.title and len(self.title) > 50 else self.title,
            'crawl_time': self.crawl_time.strftime('%Y-%m-%d %H:%M:%S') if self.crawl_time else None,
            'heat': self.heat or 0,
            'is_high_heat': self.heat >= self.HIGH_HEAT_THRESHOLD,  # 新增：是否高热度
            'comment_count': self.comment_count,
            'sentiment_score': round(self.sentiment_score, 3),
            'sentiment_type': self.sentiment_type,
            'risk_level': self._get_risk_level()  # 新增：风险等级
        }

    def _get_risk_level(self):
        """辅助方法：根据情感分判断风险等级"""
        if self.sentiment_score < 0.3:
            return "高"
        elif self.sentiment_score < 0.6:
            return "中"
        else:
            return "低"

    @staticmethod
    def _validate_heat_static(heat):
        """静态辅助方法：校验热度值（供类方法调用）"""
        try:
            heat_int = int(heat) if heat is not None else 0
            return max(0, heat_int)
        except (ValueError, TypeError):
            return 0

    @staticmethod
    def _validate_sentiment_score_static(score):
        """静态辅助方法：校验情感分（供类方法调用）"""
        try:
            score_float = float(score) if score is not None else Event.DEFAULT_SENTIMENT_SCORE
            return max(0.0, min(1.0, round(score_float, 3)))
        except (ValueError, TypeError):
            return Event.DEFAULT_SENTIMENT_SCORE

    @staticmethod
    def get_count(search_query=None, only_high_heat=False):
        """获取事件总数（增强版：支持关键词搜索、高热度筛选）"""
        @with_db_connection
        def _get(conn):
            sql = "SELECT COUNT(*) as count FROM hot_events"
            params = []
            where_conditions = []

            if search_query:
                where_conditions.append("title LIKE %s")
                params.append(f"%{search_query}%")
            if only_high_heat:
                where_conditions.append(f"heat >= %s")
                params.append(Event.HIGH_HEAT_THRESHOLD)

            if where_conditions:
                sql += " WHERE " + " AND ".join(where_conditions)

            result = execute_query(conn, sql, params, fetch_one=True)
            return result['count'] if result else 0
        return _get()

    @staticmethod
    def get_avg_heat():
        """新增：获取所有事件的平均热度（适配舆情整体分析）"""
        @with_db_connection
        def _get(conn):
            sql = "SELECT AVG(IFNULL(heat, 0)) as avg_heat FROM hot_events"
            result = execute_query(conn, sql, fetch_one=True)
            return round(float(result['avg_heat'] or 0), 0) if result else 0
        return _get()

    @staticmethod
    def get_heat_ranking(limit=10):
        """新增：获取事件热度排名（适配热点事件榜单）"""
        @with_db_connection
        def _get(conn):
            sql = """
                SELECT he.*, COUNT(c.id) as comment_count
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
                GROUP BY he.id
                ORDER BY he.heat DESC
                LIMIT %s
            """
            results = execute_query(conn, sql, (limit,))
            return [Event(**data) for data in results]
        return _get()