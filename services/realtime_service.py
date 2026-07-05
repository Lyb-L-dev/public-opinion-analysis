import json
import logging
from datetime import datetime, timedelta
from flask import jsonify
from models.event import Event
from models.comment import Comment
from models.article import Article
from services.redis_service import redis_service
from utils.db_utils import with_db_connection, execute_query

logger = logging.getLogger(__name__)


class RealtimeService:
    """实时数据服务"""

    def __init__(self):
        self.cache_expiry = 300  # 缓存过期时间（秒）

    def get_realtime_hot_events(self, limit=20, use_cache=True):
        """获取实时热搜事件"""
        # 尝试从Redis缓存获取
        if use_cache:
            cached_events = redis_service.get_cached_hot_events()
            if cached_events:
                logger.info("从Redis缓存获取实时热搜")
                return cached_events[:limit]

        # 从数据库获取最新热搜
        @with_db_connection
        def _get_realtime_events(conn):
            sql = """
                SELECT he.*, 
                       COUNT(DISTINCT c.id) as comment_count,
                       COUNT(DISTINCT a.id) as article_count,
                       AVG(COALESCE(c.sentiment_score, 0.5)) as avg_sentiment
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
                LEFT JOIN articles a ON he.id = a.event_id
                WHERE he.crawl_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
                GROUP BY he.id
                ORDER BY he.heat DESC, he.crawl_time DESC
                LIMIT %s
            """
            results = execute_query(conn, sql, (limit,))

            events = []
            for row in results:
                event = {
                    'id': row['id'],
                    'title': row['title'],
                    'crawl_time': row['crawl_time'].strftime('%Y-%m-%d %H:%M:%S'),
                    'heat': row['heat'] or 0,
                    'sentiment_score': float(row['avg_sentiment'] or 0.5),
                    'comment_count': row['comment_count'] or 0,
                    'article_count': row['article_count'] or 0,
                    'is_realtime': True
                }
                # 计算情感类型
                score = event['sentiment_score']
                if score > 0.7:
                    event['sentiment_type'] = '正面'
                elif score < 0.3:
                    event['sentiment_type'] = '负面'
                else:
                    event['sentiment_type'] = '中性'
                events.append(event)

            return events

        try:
            events = _get_realtime_events()
            # 缓存到Redis
            redis_service.cache_hot_events(events)
            return events
        except Exception as e:
            logger.error(f"获取实时热搜失败: {e}")
            return []

    def get_event_realtime_updates(self, event_id):
        """获取事件的实时更新（评论、情感变化等）"""
        # 检查Redis缓存
        cache_key = f"event:updates:{event_id}"
        if redis_service.is_connected():
            cached = redis_service.client.get(cache_key)
            if cached:
                return json.loads(cached)

        @with_db_connection
        def _get_event_updates(conn):
            # 获取事件基本信息
            event_sql = "SELECT * FROM hot_events WHERE id = %s"
            event = execute_query(conn, event_sql, (event_id,), fetch_one=True)

            if not event:
                return None

            # 获取最近评论
            comments_sql = """
                SELECT id, username, content, publish_time, like_count, sentiment_score
                FROM comments 
                WHERE event_id = %s 
                ORDER BY publish_time DESC 
                LIMIT 20
            """
            recent_comments = execute_query(conn, comments_sql, (event_id,))

            # 获取情感趋势（最近2小时）
            trend_sql = """
                SELECT 
                    DATE_FORMAT(publish_time, '%Y-%m-%d %H:%i:00') as time_window,
                    COUNT(*) as comment_count,
                    AVG(sentiment_score) as avg_sentiment
                FROM comments 
                WHERE event_id = %s 
                    AND publish_time >= DATE_SUB(NOW(), INTERVAL 2 HOUR)
                GROUP BY time_window
                ORDER BY time_window
            """
            sentiment_trend = execute_query(conn, trend_sql, (event_id,))

            # 获取热词
            keywords_sql = """
                SELECT content
                FROM comments 
                WHERE event_id = %s 
                    AND publish_time >= DATE_SUB(NOW(), INTERVAL 1 HOUR)
                LIMIT 100
            """
            comments_for_keywords = execute_query(conn, keywords_sql, (event_id,))

            # 简单热词提取（这里可以调用更复杂的NLP处理）
            keywords = self._extract_keywords([c['content'] for c in comments_for_keywords])

            updates = {
                'event_id': event_id,
                'title': event['title'],
                'current_heat': event['heat'],
                'current_sentiment': float(event['sentiment_score'] or 0.5),
                'recent_comments': [
                    {
                        'id': c['id'],
                        'username': c['username'],
                        'content': c['content'][:100] + '...' if len(c['content']) > 100 else c['content'],
                        'time': c['publish_time'].strftime('%H:%M'),
                        'sentiment': float(c['sentiment_score'] or 0.5)
                    }
                    for c in recent_comments
                ],
                'sentiment_trend': [
                    {
                        'time': t['time_window'],
                        'sentiment': float(t['avg_sentiment'] or 0.5)
                    }
                    for t in sentiment_trend
                ],
                'top_keywords': keywords[:10],
                'update_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }

            return updates

        try:
            updates = _get_event_updates()
            if updates and redis_service.is_connected():
                # 缓存1分钟
                redis_service.client.setex(
                    cache_key,
                    60,
                    json.dumps(updates, ensure_ascii=False)
                )
            return updates
        except Exception as e:
            logger.error(f"获取事件实时更新失败: {e}")
            return None

    def _extract_keywords(self, texts, top_k=10):
        """简单热词提取"""
        from collections import Counter
        import jieba

        all_words = []
        for text in texts:
            if text:
                words = jieba.lcut(text)
                # 过滤停用词和单字
                words = [w for w in words if len(w) > 1 and not w.isdigit()]
                all_words.extend(words)

        word_count = Counter(all_words)
        return [{'word': w, 'count': c} for w, c in word_count.most_common(top_k)]

    def get_system_stats(self):
        """获取系统实时统计"""

        @with_db_connection
        def _get_stats(conn):
            stats = {}

            # 事件统计
            event_sql = """
                SELECT 
                    COUNT(*) as total_events,
                    SUM(CASE WHEN heat > 1000000 THEN 1 ELSE 0 END) as high_heat_events,
                    AVG(heat) as avg_heat
                FROM hot_events 
                WHERE crawl_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            """
            event_stats = execute_query(conn, event_sql, fetch_one=True)
            stats.update(event_stats or {})

            # 评论统计
            comment_sql = """
                SELECT 
                    COUNT(*) as total_comments,
                    COUNT(DISTINCT username) as unique_users,
                    AVG(sentiment_score) as avg_sentiment
                FROM comments 
                WHERE publish_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            """
            comment_stats = execute_query(conn, comment_sql, fetch_one=True)
            stats.update(comment_stats or {})

            # 情感分布
            sentiment_sql = """
                SELECT 
                    SUM(CASE WHEN sentiment_score > 0.7 THEN 1 ELSE 0 END) as positive_count,
                    SUM(CASE WHEN sentiment_score BETWEEN 0.3 AND 0.7 THEN 1 ELSE 0 END) as neutral_count,
                    SUM(CASE WHEN sentiment_score < 0.3 THEN 1 ELSE 0 END) as negative_count
                FROM comments 
                WHERE publish_time >= DATE_SUB(NOW(), INTERVAL 24 HOUR)
            """
            sentiment_stats = execute_query(conn, sentiment_sql, fetch_one=True)
            stats.update(sentiment_stats or {})

            # 最近活跃事件
            recent_events_sql = """
                SELECT title, heat, crawl_time
                FROM hot_events
                ORDER BY crawl_time DESC
                LIMIT 5
            """
            recent_events = execute_query(conn, recent_events_sql)
            stats['recent_events'] = [
                {
                    'title': e['title'],
                    'heat': e['heat'],
                    'time': e['crawl_time'].strftime('%H:%M')
                }
                for e in recent_events
            ]

            stats['last_updated'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            return stats

        try:
            return _get_stats()
        except Exception as e:
            logger.error(f"获取系统统计失败: {e}")
            return {}


# 创建全局实时服务实例
realtime_service = RealtimeService()