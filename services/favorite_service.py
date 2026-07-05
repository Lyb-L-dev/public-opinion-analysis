import logging
from utils.db_utils import create_db_connection, execute_query

logger = logging.getLogger(__name__)

class FavoriteService:
    """收藏服务"""

    @staticmethod
    def is_favorited(user_id, event_id):
        """检查用户是否已收藏某事件"""
        conn = create_db_connection()
        if not conn:
            return False
        try:
            sql = "SELECT id FROM favorites WHERE user_id = %s AND event_id = %s"
            result = execute_query(conn, sql, (user_id, event_id), fetch_one=True)
            return result is not None
        except Exception as e:
            logger.error(f"检查收藏失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def add_favorite(user_id, event_id):
        """添加收藏"""
        conn = create_db_connection()
        if not conn:
            return False
        try:
            sql = "INSERT INTO favorites (user_id, event_id) VALUES (%s, %s)"
            execute_query(conn, sql, (user_id, event_id))  # 不再传递 commit 参数
            conn.commit()  # 手动提交
            return True
        except Exception as e:
            logger.error(f"添加收藏失败: {e}")
            conn.rollback()  # 出错时回滚
            return False
        finally:
            conn.close()

    @staticmethod
    def remove_favorite(user_id, event_id):
        """取消收藏"""
        conn = create_db_connection()
        if not conn:
            return False
        try:
            sql = "DELETE FROM favorites WHERE user_id = %s AND event_id = %s"
            execute_query(conn, sql, (user_id, event_id))  # 不再传递 commit 参数
            conn.commit()  # 手动提交
            return True
        except Exception as e:
            logger.error(f"取消收藏失败: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()

    @staticmethod
    def toggle_favorite(user_id, event_id):
        """切换收藏状态：如果已收藏则取消，否则添加"""
        if FavoriteService.is_favorited(user_id, event_id):
            success = FavoriteService.remove_favorite(user_id, event_id)
            return success, False  # 返回 (操作是否成功, 新状态)
        else:
            success = FavoriteService.add_favorite(user_id, event_id)
            return success, True

    @staticmethod
    def get_user_favorites(user_id, limit=10):
        """获取用户收藏的事件列表（含事件详情）"""
        conn = create_db_connection()
        if not conn:
            return []
        try:
            sql = """
                SELECT f.id as favorite_id, f.created_at as favorited_time,
                       he.id, he.title, he.heat, he.sentiment_score, he.crawl_time,
                       (SELECT COUNT(*) FROM comments WHERE event_id = he.id) as comment_count
                FROM favorites f
                JOIN hot_events he ON f.event_id = he.id
                WHERE f.user_id = %s
                ORDER BY f.created_at DESC
                LIMIT %s
            """
            return execute_query(conn, sql, (user_id, limit)) or []
        except Exception as e:
            logger.error(f"获取用户收藏失败: {e}")
            return []
        finally:
            conn.close()