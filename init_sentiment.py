# init_sentiment.py
import sys
import os

# 添加项目根目录到Python路径
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from utils.db_utils import with_db_connection, execute_query, execute_many
from utils.text_utils import analyze_sentiment, load_stopwords
import logging

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(message)s')
logger = logging.getLogger(__name__)

# 哈工大停用词表路径
HIT_STOPWORDS_PATH = 'hit_stopwords/hit_stopwords.txt'


def initialize_stopwords():
    """初始化加载哈工大停用词表"""
    logger.info("===== 加载哈工大停用词表 =====")
    if os.path.exists(HIT_STOPWORDS_PATH):
        success = load_stopwords(HIT_STOPWORDS_PATH, append=True)
        if success:
            logger.info("✅ 哈工大停用词表加载成功，情感分析将使用完整停用词库")
        else:
            logger.warning("⚠️ 哈工大停用词表加载失败，将使用基础停用词库")
    else:
        logger.warning(f"⚠️ 停用词文件不存在: {HIT_STOPWORDS_PATH}")


def update_comments_sentiment():
    """更新comments表的情感分（使用哈工大停用词表增强精度）"""
    logger.info("===== 开始更新 comments 表情感分 =====")

    @with_db_connection
    def _update(conn):
        sql_select = "SELECT id, content FROM comments"
        comments = execute_query(conn, sql_select)

        if not comments:
            logger.info("📌 comments表中暂无数据")
            return 0

        logger.info(f"📌 共查询到 {len(comments)} 条评论")

        update_sql = "UPDATE comments SET sentiment_score = %s WHERE id = %s"
        params = []
        valid_count = 0
        error_count = 0

        for idx, comment in enumerate(comments):
            if (idx + 1) % 100 == 0:
                logger.info(f"🔄 已处理 {idx + 1}/{len(comments)} 条评论")

            content = comment['content']
            if not content or len(str(content).strip()) == 0:
                params.append((0.5, comment['id']))
                continue

            try:
                score = analyze_sentiment(content)
                params.append((score, comment['id']))
                valid_count += 1
            except Exception as e:
                logger.warning(f"⚠️ 评论ID {comment['id']} 分析失败: {e}")
                params.append((0.5, comment['id']))
                error_count += 1

        if params:
            execute_many(conn, update_sql, params)
            logger.info(f"✅ comments 更新完成：{valid_count} 条有效评论，{error_count} 条处理异常")
            return len(params)
        return 0

    return _update()


def update_articles_sentiment():
    """更新articles表的情感分（使用哈工大停用词表增强精度）"""
    logger.info("===== 开始更新 articles 表情感分 =====")

    @with_db_connection
    def _update(conn):
        sql_select = "SELECT id, content FROM articles"
        articles = execute_query(conn, sql_select)

        if not articles:
            logger.info("📌 articles表中暂无数据")
            return 0

        logger.info(f"📌 共查询到 {len(articles)} 篇文章")

        update_sql = "UPDATE articles SET sentiment_score = %s WHERE id = %s"
        params = []
        valid_count = 0
        error_count = 0

        for idx, article in enumerate(articles):
            if (idx + 1) % 100 == 0:
                logger.info(f"🔄 已处理 {idx + 1}/{len(articles)} 篇文章")

            content = article['content']
            if not content or len(str(content).strip()) == 0:
                params.append((0.5, article['id']))
                continue

            try:
                score = analyze_sentiment(content)
                params.append((score, article['id']))
                valid_count += 1
            except Exception as e:
                logger.warning(f"⚠️ 文章ID {article['id']} 分析失败: {e}")
                params.append((0.5, article['id']))
                error_count += 1

        if params:
            execute_many(conn, update_sql, params)
            logger.info(f"✅ articles 更新完成：{valid_count} 篇有效文章，{error_count} 篇处理异常")
            return len(params)
        return 0

    return _update()


def update_hot_events_sentiment():
    """更新hot_events表的情感分（根据关联的评论计算平均情感）"""
    logger.info("===== 开始更新 hot_events 表情感分 =====")

    @with_db_connection
    def _update(conn):
        # 先获取所有事件
        sql_select = "SELECT id, title FROM hot_events"
        events = execute_query(conn, sql_select)

        if not events:
            logger.info("📌 hot_events表中暂无数据")
            return 0

        logger.info(f"📌 共查询到 {len(events)} 个事件")

        # 为每个事件计算关联评论的平均情感分
        update_sql = "UPDATE hot_events SET sentiment_score = %s WHERE id = %s"
        params = []
        valid_count = 0

        for idx, event in enumerate(events):
            if (idx + 1) % 50 == 0:
                logger.info(f"🔄 已处理 {idx + 1}/{len(events)} 个事件")

            event_id = event['id']

            # 查询该事件的所有评论的平均情感分
            sql_avg = "SELECT AVG(sentiment_score) as avg_score FROM comments WHERE event_id = %s"
            result = execute_query(conn, sql_avg, (event_id,), fetch_one=True)

            if result and result['avg_score']:
                score = round(float(result['avg_score']), 3)
                valid_count += 1
            else:
                # 如果没有评论，尝试用事件标题计算情感
                score = analyze_sentiment(event['title']) if event['title'] else 0.5

            params.append((score, event_id))

        if params:
            execute_many(conn, update_sql, params)
            logger.info(f"✅ hot_events 更新完成：{valid_count} 个事件有评论数据")
            return len(params)
        return 0

    return _update()


if __name__ == '__main__':
    try:
        # 初始化加载哈工大停用词表
        initialize_stopwords()

        total = 0
        total += update_comments_sentiment()
        total += update_articles_sentiment()
        total += update_hot_events_sentiment()

        logger.info(f"===== 所有情感分更新完成，共更新 {total} 条记录 =====")
    except Exception as e:
        logger.error(f"❌ 执行失败：{str(e)}", exc_info=True)
        sys.exit(1)
