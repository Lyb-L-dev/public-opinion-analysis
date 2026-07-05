"""
评论表重复数据清理脚本

功能：
- 预览重复数据（统计数量和示例）
- 分批删除重复数据（保留每组最小 id）

使用方法：
    python utils/cleanup_duplicate_comments.py
"""

import sys
import logging

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def get_db_config():
    """获取数据库配置"""
    try:
        from config import config
        return {
            'host': config.DB_HOST or 'localhost',
            'port': int(config.DB_PORT) if config.DB_PORT else 3306,
            'database': config.DB_NAME or 'public_opinion_db',
            'user': config.DB_USER or 'root',
            'password': config.DB_PASSWORD or 'root',
            'charset': 'utf8mb4',
        }
    except Exception as e:
        logger.error(f"获取数据库配置失败: {e}")
        # 使用默认配置
        return {
            'host': 'localhost',
            'port': 3306,
            'database': 'public_opinion_db',
            'user': 'root',
            'password': 'root',
            'charset': 'utf8mb4',
        }


def create_connection():
    """创建数据库连接"""
    import pymysql
    return pymysql.connect(**get_db_config())


def preview_duplicates():
    """预览重复数据统计"""
    import pymysql

    logger.info("=" * 50)
    logger.info("开始预览重复数据...")
    logger.info("=" * 50)

    conn = create_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # 1. 统计重复组数和总重复条数
            sql_count = """
                SELECT COUNT(*) as group_count,
                       IFNULL(SUM(cnt - 1), 0) as total_duplicates
                FROM (
                    SELECT COUNT(*) as cnt
                    FROM comments
                    GROUP BY event_id, user_id, content
                    HAVING COUNT(*) > 1
                ) AS groups_with_duplicates
            """
            cursor.execute(sql_count)
            result = cursor.fetchone()

            group_count = result['group_count'] or 0
            total_duplicates = result['total_duplicates'] or 0

            logger.info(f"\n重复数据统计:")
            logger.info(f"  - 重复组数: {group_count}")
            logger.info(f"  - 重复条目数: {total_duplicates}")

            if total_duplicates == 0:
                logger.info("\n没有发现重复数据，无需清理！")
                return False

            # 2. 显示重复组示例
            sql_example = """
                SELECT event_id, user_id, content, COUNT(*) as cnt
                FROM comments
                GROUP BY event_id, user_id, content
                HAVING COUNT(*) > 1
                LIMIT 5
            """
            cursor.execute(sql_example)
            examples = cursor.fetchall()

            logger.info(f"\n前 {len(examples)} 条重复示例:")
            for i, row in enumerate(examples, 1):
                content_preview = row['content'][:50] + '...' if row['content'] and len(row['content']) > 50 else row['content']
                logger.info(f"  {i}. event_id={row['event_id']}, user_id={row['user_id']}")
                logger.info(f"     content: {content_preview}")
                logger.info(f"     重复次数: {row['cnt']}")

            return True

    except Exception as e:
        logger.error(f"预览重复数据失败: {e}")
        return False
    finally:
        conn.close()


def delete_duplicates(batch_size=1000):
    """分批删除重复数据，保留每组最小 id"""
    import pymysql

    deleted_total = 0
    batch_count = 0

    conn = create_connection()
    try:
        while True:
            # 使用子查询删除重复数据（保留每组最小 id）
            sql_delete = """
                DELETE FROM comments
                WHERE id NOT IN (
                    SELECT * FROM (
                        SELECT MIN(id)
                        FROM comments
                        GROUP BY event_id, user_id, content
                    ) AS keep_ids
                )
                LIMIT %s
            """

            with conn.cursor() as cursor:
                cursor.execute(sql_delete, (batch_size,))
                conn.commit()
                affected = cursor.rowcount

            if affected == 0:
                break

            deleted_total += affected
            batch_count += 1
            logger.info(f"批次 {batch_count}: 已删除 {affected} 条，当前累计 {deleted_total} 条")

        logger.info("=" * 50)
        logger.info(f"删除完成！共删除 {deleted_total} 条重复数据")
        logger.info("=" * 50)
        return deleted_total

    except Exception as e:
        logger.error(f"删除重复数据失败: {e}")
        conn.rollback()
        return deleted_total
    finally:
        conn.close()


def verify_cleanup():
    """验证清理结果"""
    import pymysql

    logger.info("\n验证清理结果...")

    conn = create_connection()
    try:
        with conn.cursor(pymysql.cursors.DictCursor) as cursor:
            # 检查是否还有重复数据
            sql_verify = """
                SELECT COUNT(*) as group_count
                FROM (
                    SELECT COUNT(*) as cnt
                    FROM comments
                    GROUP BY event_id, user_id, content
                    HAVING COUNT(*) > 1
                ) AS groups_with_duplicates
            """
            cursor.execute(sql_verify)
            result = cursor.fetchone()
            remaining = result['group_count'] or 0

            if remaining == 0:
                logger.info("验证通过: 没有发现重复数据！")
            else:
                logger.warning(f"警告: 仍有 {remaining} 组重复数据未清理")

            # 统计清理后的总数据量
            cursor.execute("SELECT COUNT(*) as total FROM comments")
            result = cursor.fetchone()
            logger.info(f"当前评论表总数据量: {result['total']}")

    except Exception as e:
        logger.error(f"验证失败: {e}")
    finally:
        conn.close()


def main():
    """主函数"""
    logger.info("=" * 50)
    logger.info("评论表重复数据清理工具")
    logger.info("=" * 50)

    # 1. 预览重复数据
    has_duplicates = preview_duplicates()

    if not has_duplicates:
        logger.info("\n任务结束，无需清理。")
        return

    # 2. 确认删除
    print("\n" + "=" * 50)
    confirm = input("确认删除重复数据? (y/n): ").strip().lower()
    print("=" * 50)

    if confirm != 'y':
        logger.info("用户取消操作，任务结束。")
        return

    # 3. 执行删除
    print("\n开始删除重复数据...\n")
    delete_duplicates()

    # 4. 验证结果
    verify_cleanup()


if __name__ == '__main__':
    main()
