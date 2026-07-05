import os
import logging
import pandas as pd
import zipfile
from datetime import datetime
from config import config
from utils.db_utils import create_db_connection

logger = logging.getLogger(__name__)


class CSVExportService:
    """CSV导出服务 - 从数据库导出热搜、评论、文章数据到CSV文件"""

    def __init__(self, export_dir=None):
        self.export_dir = export_dir or config.CSV_EXPORT_DIR
        self._ensure_export_dir()

    def _ensure_export_dir(self):
        """确保导出目录存在"""
        if not os.path.exists(self.export_dir):
            os.makedirs(self.export_dir, exist_ok=True)
            logger.info(f"创建导出目录: {self.export_dir}")

    def _get_connection(self):
        """获取数据库连接"""
        return create_db_connection()

    def _safe_filename(self, filename):
        """生成安全的文件名"""
        # 移除或替换非法字符
        safe_chars = []
        for c in filename:
            if c in '\\/*?:"<>|':
                safe_chars.append('_')
            else:
                safe_chars.append(c)
        return ''.join(safe_chars)

    def export_hot_events(self, start_time, end_time):
        """
        导出指定时间范围的热搜数据到CSV

        Args:
            start_time: 开始时间 (datetime 或 str 'YYYY-MM-DD')
            end_time: 结束时间 (datetime 或 str 'YYYY-MM-DD')

        Returns:
            str: 生成的CSV文件路径，失败返回None
        """
        conn = self._get_connection()
        if not conn:
            logger.error("无法连接数据库，导出热搜数据失败")
            return None

        try:
            with conn.cursor() as cursor:
                # 处理时间参数
                if isinstance(start_time, str):
                    start_time = f"{start_time} 00:00:00"
                if isinstance(end_time, str):
                    end_time = f"{end_time} 23:59:59"

                sql = """
                    SELECT
                        id, title, url, heat, crawl_time,
                        sentiment_score, created_at
                    FROM hot_events
                    WHERE crawl_time BETWEEN %s AND %s
                    ORDER BY crawl_time DESC
                """
                cursor.execute(sql, (start_time, end_time))
                data = cursor.fetchall()

            if not data:
                logger.warning(f"指定时间范围内没有热搜数据: {start_time} ~ {end_time}")
                return None

            df = pd.DataFrame(data)

            # 生成文件名
            start_str = start_time.strftime('%Y%m%d') if isinstance(start_time, datetime) else start_time[:10].replace('-', '')
            end_str = end_time.strftime('%Y%m%d') if isinstance(end_time, datetime) else end_time[:10].replace('-', '')
            filename = f"hot_events_{start_str}_{end_str}.csv"
            filepath = os.path.join(self.export_dir, filename)

            # 保存CSV
            df.to_csv(filepath, index=False, encoding='utf_8_sig')
            logger.info(f"热搜数据已导出: {filepath}，共 {len(df)} 条记录")

            return filepath

        except Exception as e:
            logger.error(f"导出热搜数据失败: {e}")
            return None
        finally:
            conn.close()

    def export_comments(self, start_time, end_time):
        """
        导出指定时间范围的评论数据到CSV

        Args:
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            str: 生成的CSV文件路径，失败返回None
        """
        conn = self._get_connection()
        if not conn:
            logger.error("无法连接数据库，导出评论数据失败")
            return None

        try:
            with conn.cursor() as cursor:
                if isinstance(start_time, str):
                    start_time = f"{start_time} 00:00:00"
                if isinstance(end_time, str):
                    end_time = f"{end_time} 23:59:59"

                sql = """
                    SELECT
                        id, event_id, comment_id, username, user_id,
                        content, publish_time, location, like_count,
                        crawl_time, hot_title, sentiment_score, sentiment_type
                    FROM comments
                    WHERE crawl_time BETWEEN %s AND %s
                    ORDER BY crawl_time DESC
                """
                cursor.execute(sql, (start_time, end_time))
                data = cursor.fetchall()

            if not data:
                logger.warning(f"指定时间范围内没有评论数据: {start_time} ~ {end_time}")
                return None

            df = pd.DataFrame(data)

            start_str = start_time.strftime('%Y%m%d') if isinstance(start_time, datetime) else start_time[:10].replace('-', '')
            end_str = end_time.strftime('%Y%m%d') if isinstance(end_time, datetime) else end_time[:10].replace('-', '')
            filename = f"comments_{start_str}_{end_str}.csv"
            filepath = os.path.join(self.export_dir, filename)

            df.to_csv(filepath, index=False, encoding='utf_8_sig')
            logger.info(f"评论数据已导出: {filepath}，共 {len(df)} 条记录")

            return filepath

        except Exception as e:
            logger.error(f"导出评论数据失败: {e}")
            return None
        finally:
            conn.close()

    def export_articles(self, start_time, end_time):
        """
        导出指定时间范围的文章数据到CSV

        Args:
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            str: 生成的CSV文件路径，失败返回None
        """
        conn = self._get_connection()
        if not conn:
            logger.error("无法连接数据库，导出文章数据失败")
            return None

        try:
            with conn.cursor() as cursor:
                if isinstance(start_time, str):
                    start_time = f"{start_time} 00:00:00"
                if isinstance(end_time, str):
                    end_time = f"{end_time} 23:59:59"

                sql = """
                    SELECT
                        id, event_id, article_id, author, content,
                        publish_time, like_count, repost_count, comment_count,
                        crawl_time, hot_title, sentiment_score, sentiment_type
                    FROM articles
                    WHERE crawl_time BETWEEN %s AND %s
                    ORDER BY crawl_time DESC
                """
                cursor.execute(sql, (start_time, end_time))
                data = cursor.fetchall()

            if not data:
                logger.warning(f"指定时间范围内没有文章数据: {start_time} ~ {end_time}")
                return None

            df = pd.DataFrame(data)

            start_str = start_time.strftime('%Y%m%d') if isinstance(start_time, datetime) else start_time[:10].replace('-', '')
            end_str = end_time.strftime('%Y%m%d') if isinstance(end_time, datetime) else end_time[:10].replace('-', '')
            filename = f"articles_{start_str}_{end_str}.csv"
            filepath = os.path.join(self.export_dir, filename)

            df.to_csv(filepath, index=False, encoding='utf_8_sig')
            logger.info(f"文章数据已导出: {filepath}，共 {len(df)} 条记录")

            return filepath

        except Exception as e:
            logger.error(f"导出文章数据失败: {e}")
            return None
        finally:
            conn.close()

    def export_all(self, start_time, end_time):
        """
        导出所有数据（热搜、评论、文章）为ZIP压缩包

        Args:
            start_time: 开始时间
            end_time: 结束时间

        Returns:
            str: 生成的ZIP文件路径，失败返回None
        """
        exported_files = []

        # 导出各类型数据
        hot_events_file = self.export_hot_events(start_time, end_time)
        if hot_events_file:
            exported_files.append(hot_events_file)

        comments_file = self.export_comments(start_time, end_time)
        if comments_file:
            exported_files.append(comments_file)

        articles_file = self.export_articles(start_time, end_time)
        if articles_file:
            exported_files.append(articles_file)

        if not exported_files:
            logger.warning(f"指定时间范围内没有任何数据: {start_time} ~ {end_time}")
            return None

        # 创建ZIP文件
        start_str = start_time.strftime('%Y%m%d') if isinstance(start_time, datetime) else start_time[:10].replace('-', '')
        end_str = end_time.strftime('%Y%m%d') if isinstance(end_time, datetime) else end_time[:10].replace('-', '')
        zip_filename = f"weibo_data_export_{start_str}_{end_str}.zip"
        zip_filepath = os.path.join(self.export_dir, zip_filename)

        try:
            with zipfile.ZipFile(zip_filepath, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for file_path in exported_files:
                    # 添加文件到ZIP，使用文件名而非完整路径
                    zipf.write(file_path, os.path.basename(file_path))

            logger.info(f"数据已打包为ZIP: {zip_filepath}，包含 {len(exported_files)} 个文件")

            return zip_filepath

        except Exception as e:
            logger.error(f"创建ZIP文件失败: {e}")
            return None

    def get_export_files(self):
        """
        获取所有已导出的文件列表

        Returns:
            list: 文件信息列表 [{'name': str, 'path': str, 'size': int, 'created': str}]
        """
        files = []
        if not os.path.exists(self.export_dir):
            return files

        for filename in os.listdir(self.export_dir):
            filepath = os.path.join(self.export_dir, filename)
            if os.path.isfile(filepath):
                stat = os.stat(filepath)
                created_time = datetime.fromtimestamp(stat.st_mtime)
                files.append({
                    'name': filename,
                    'path': filepath,
                    'size': stat.st_size,
                    'created': created_time.strftime('%Y-%m-%d %H:%M:%S')
                })

        # 按创建时间倒序排列
        files.sort(key=lambda x: x['created'], reverse=True)
        return files


# 创建全局服务实例
csv_export_service = CSVExportService()
