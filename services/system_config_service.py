import logging
from utils.db_utils import create_db_connection
from config import config as default_config

logger = logging.getLogger(__name__)

class SystemConfigService:
    """系统配置服务（持久化到数据库）"""

    @staticmethod
    def ensure_table():
        """确保 system_config 表存在"""
        conn = create_db_connection()
        if not conn:
            logger.error("无法连接数据库，无法创建配置表")
            return
        try:
            with conn.cursor() as cursor:
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS system_config (
                        `key` VARCHAR(100) PRIMARY KEY,
                        `value` TEXT,
                        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
                    )
                """)
                conn.commit()
                logger.info("system_config 表已确保存在")
        except Exception as e:
            logger.error(f"创建 system_config 表失败: {e}")
        finally:
            conn.close()

    @staticmethod
    def ensure_indexes():
        """确保数据库索引存在，提升查询性能"""
        conn = create_db_connection()
        if not conn:
            logger.error("无法连接数据库，无法创建索引")
            return
        try:
            with conn.cursor() as cursor:
                indexes = [
                    # comments表索引
                    ("idx_comments_event_id", "CREATE INDEX idx_comments_event_id ON comments(event_id)"),
                    ("idx_comments_publish_time", "CREATE INDEX idx_comments_publish_time ON comments(publish_time)"),
                    ("idx_comments_sentiment_score", "CREATE INDEX idx_comments_sentiment_score ON comments(sentiment_score)"),
                    ("idx_comments_content", "CREATE INDEX idx_comments_content ON comments(content(100))"),
                    # hot_events表索引
                    ("idx_hot_events_crawl_time", "CREATE INDEX idx_hot_events_crawl_time ON hot_events(crawl_time)"),
                    ("idx_hot_events_heat", "CREATE INDEX idx_hot_events_heat ON hot_events(heat)"),
                    ("idx_hot_events_title", "CREATE INDEX idx_hot_events_title ON hot_events(title(100))"),
                ]

                for idx_name, create_sql in indexes:
                    try:
                        cursor.execute(f"SHOW INDEX FROM comments WHERE Key_name = '{idx_name}'")
                        if not cursor.fetchall():
                            if "comments" in create_sql:
                                cursor.execute(create_sql)
                                logger.info(f"索引 {idx_name} 创建成功")
                    except Exception as e:
                        try:
                            cursor.execute(f"SHOW INDEX FROM hot_events WHERE Key_name = '{idx_name}'")
                            if not cursor.fetchall():
                                if "hot_events" in create_sql:
                                    cursor.execute(create_sql)
                                    logger.info(f"索引 {idx_name} 创建成功")
                        except:
                            pass

                conn.commit()
                logger.info("数据库索引检查完成")
        except Exception as e:
            logger.error(f"创建索引失败: {e}")
        finally:
            conn.close()

    @staticmethod
    def get_all_config():
        """从数据库读取所有配置项，若不存在则返回默认值"""
        conn = create_db_connection()
        if not conn:
            return {}
        config_dict = {}
        try:
            with conn.cursor() as cursor:
                cursor.execute("SELECT `key`, `value` FROM system_config")
                rows = cursor.fetchall()
                for row in rows:
                    config_dict[row['key']] = row['value']
        except Exception as e:
            logger.error(f"读取配置失败: {e}")
        finally:
            conn.close()
        return config_dict

    @staticmethod
    def save_config(key, value):
        """保存或更新单个配置项"""
        conn = create_db_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO system_config (`key`, `value`) VALUES (%s, %s) "
                    "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)",
                    (key, value)
                )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"保存配置 {key} 失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def save_multiple(config_dict):
        """批量保存配置项"""
        conn = create_db_connection()
        if not conn:
            return False
        try:
            with conn.cursor() as cursor:
                for key, value in config_dict.items():
                    cursor.execute(
                        "INSERT INTO system_config (`key`, `value`) VALUES (%s, %s) "
                        "ON DUPLICATE KEY UPDATE `value` = VALUES(`value`)",
                        (key, value)
                    )
                conn.commit()
            return True
        except Exception as e:
            logger.error(f"批量保存配置失败: {e}")
            return False
        finally:
            conn.close()

    @staticmethod
    def merge_with_defaults():
        """
        合并数据库配置和默认配置，数据库配置优先
        返回一个包含所有配置的字典
        """
        db_config = SystemConfigService.get_all_config()
        # 从默认 config 对象中提取所有属性（以大写字母开头的常量）
        default_dict = {k: v for k, v in vars(default_config).items() if k.isupper()}
        merged = default_dict.copy()
        merged.update(db_config)
        return merged

    @staticmethod
    def apply_to_config():
        """将数据库配置应用到全局 config 对象（动态修改属性）"""
        merged = SystemConfigService.merge_with_defaults()
        for key, value in merged.items():
            # 尝试转换类型为原始类型
            original_value = getattr(default_config, key, None)
            if original_value is not None:
                try:
                    if isinstance(original_value, bool):
                        value = value.lower() in ('true', '1', 'yes')
                    elif isinstance(original_value, int):
                        value = int(value)
                    elif isinstance(original_value, float):
                        value = float(value)
                except:
                    pass  # 转换失败则保留字符串
            setattr(default_config, key, value)
        logger.info("已应用数据库配置到 config 对象")