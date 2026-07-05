import logging
from datetime import datetime, timedelta
from utils.db_utils import with_db_connection, execute_query
from utils.text_utils import extract_keywords
from models.comment import Comment  # 用于 get_avg_sentiment

logger = logging.getLogger(__name__)

def get_wordcloud_data(days=30, limit=2000):
    """
    从comments表获取最近days天的评论内容，提取关键词及权重
    返回格式：[{"word": "疫情", "weight": 78.5}, ...]
    """
    cutoff = datetime.now() - timedelta(days=days)

    @with_db_connection
    def _fetch_comments(conn):
        sql = """
            SELECT content
            FROM comments
            WHERE publish_time >= %s
              AND content IS NOT NULL AND content != ''
            ORDER BY publish_time DESC
            LIMIT %s
        """
        results = execute_query(conn, sql, (cutoff, limit))
        return [row['content'] for row in results]

    try:
        texts = _fetch_comments()
        if not texts:
            return []

        # 调用 text_utils 中的 extract_keywords 提取关键词及权重
        keywords_with_weight = extract_keywords(texts, top_k=50, with_weight=True)

        # 转换为前端需要的格式
        result = [{"word": word, "weight": round(weight, 2)} for word, weight in keywords_with_weight]
        return result
    except Exception as e:
        logger.error(f"获取词云数据失败: {e}", exc_info=True)
        return []

def get_event_sentiment_distribution():
    """
    统计所有热点事件的情感分类（基于 sentiment_score 字段）
    返回格式：{"正面": 12, "中性": 34, "负面": 8}
    """
    @with_db_connection
    def _fetch_stats(conn):
        sql = """
            SELECT
                SUM(CASE WHEN sentiment_score > 0.65 THEN 1 ELSE 0 END) AS positive,
                SUM(CASE WHEN sentiment_score BETWEEN 0.35 AND 0.65 THEN 1 ELSE 0 END) AS neutral,
                SUM(CASE WHEN sentiment_score < 0.35 THEN 1 ELSE 0 END) AS negative
            FROM hot_events
        """
        result = execute_query(conn, sql, fetch_one=True)
        return {
            "正面": result.get("positive", 0) or 0,
            "中性": result.get("neutral", 0) or 0,
            "负面": result.get("negative", 0) or 0
        }

    try:
        return _fetch_stats()
    except Exception as e:
        logger.error(f"获取事件情感分布失败: {e}", exc_info=True)
        return {"正面": 0, "中性": 0, "负面": 0}

def get_comment_trend(range_type='week'):
    """
    按日期分组统计评论平均情感分（一次性查询）
    返回格式：{"dates": ["02-08", "02-09", ...], "scores": [0.65, 0.72, ...]}
    """
    @with_db_connection
    def _fetch_trend(conn):
        days = 7 if range_type == 'week' else 30
        cutoff = datetime.now() - timedelta(days=days)

        sql = """
            SELECT DATE(publish_time) as date, AVG(sentiment_score) as avg_sentiment
            FROM comments
            WHERE publish_time >= %s
              AND content IS NOT NULL AND content != ''
            GROUP BY DATE(publish_time)
            ORDER BY date ASC
        """
        results = execute_query(conn, sql, (cutoff,))
        dates = []
        scores = []
        for row in results:
            if row['date']:
                dates.append(row['date'].strftime('%m-%d'))
                scores.append(round(float(row['avg_sentiment'] or 0.5), 2))
        return {"dates": dates, "scores": scores}

    try:
        return _fetch_trend()
    except Exception as e:
        logger.error(f"获取评论情感趋势失败: {e}", exc_info=True)
        return {"dates": [], "scores": []}

def get_avg_sentiment():
    """全局平均情感分（复用 Comment 模型）"""
    return Comment.get_avg_sentiment()

def get_map_distribution(map_type='event'):
    """
    从 comments 表按 location 统计各省份数据
    location 字段存储格式如 '北京', '上海', '广东' 等（需清洗）
    """
    @with_db_connection
    def _fetch_data(conn):
        # 过滤掉 location 为空或 '未知' 的记录
        if map_type == 'event':
            # 统计每个省份的评论数量
            sql = """
                SELECT location, COUNT(*) as value
                FROM comments
                WHERE location IS NOT NULL AND location != '' AND location != '未知'
                GROUP BY location
                ORDER BY value DESC
            """
        else:  # sentiment
            # 统计每个省份的平均情感分
            sql = """
                SELECT location, AVG(sentiment_score) as value
                FROM comments
                WHERE location IS NOT NULL AND location != '' AND location != '未知'
                  AND sentiment_score IS NOT NULL
                GROUP BY location
                ORDER BY value DESC
            """
        results = execute_query(conn, sql)
        # 转换为 ECharts 地图需要的格式
        data = []
        for row in results:
            location = row['location']
            # 简单处理：将省份名称标准化（如 '新疆' -> '新疆维吾尔自治区'），但 ECharts 地图支持简称
            # 此处直接使用原名称，ECharts 中国地图支持省份简称
            value = float(row['value']) if map_type == 'sentiment' else int(row['value'])
            data.append({"name": location, "value": value})
        return data

    try:
        return _fetch_data()
    except Exception as e:
        logger.error(f"获取地图分布失败: {e}", exc_info=True)
        return []
def get_event_heat_trend(range_type='week'):
    """按日期统计热点事件热度总和或平均值"""
    @with_db_connection
    def _fetch(conn):
        days = 7 if range_type == 'week' else 30
        cutoff = datetime.now() - timedelta(days=days)

        sql = """
            SELECT DATE(crawl_time) as date, SUM(heat) as total_heat, AVG(heat) as avg_heat
            FROM hot_events
            WHERE crawl_time >= %s AND heat IS NOT NULL
            GROUP BY DATE(crawl_time)
            ORDER BY date ASC
        """
        results = execute_query(conn, sql, (cutoff,))
        dates = []
        total_heat = []
        avg_heat = []
        for row in results:
            if row['date']:
                dates.append(row['date'].strftime('%m-%d'))
                total_heat.append(int(row['total_heat'] or 0))
                avg_heat.append(round(float(row['avg_heat'] or 0), 2))
        return {"dates": dates, "total": total_heat, "avg": avg_heat}

    try:
        return _fetch()
    except Exception as e:
        logger.error(f"获取事件热度趋势失败: {e}", exc_info=True)
        return {"dates": [], "total": [], "avg": []}


def get_comment_like_distribution():
    """统计评论点赞数分布（区间）"""
    @with_db_connection
    def _fetch(conn):
        sql = """
            SELECT
                SUM(CASE WHEN like_count = 0 THEN 1 ELSE 0 END) AS zero,
                SUM(CASE WHEN like_count BETWEEN 1 AND 10 THEN 1 ELSE 0 END) AS low,
                SUM(CASE WHEN like_count BETWEEN 11 AND 100 THEN 1 ELSE 0 END) AS medium,
                SUM(CASE WHEN like_count > 100 THEN 1 ELSE 0 END) AS high
            FROM comments
            WHERE like_count IS NOT NULL
        """
        result = execute_query(conn, sql, fetch_one=True)
        return {
            "0": result.get("zero", 0) or 0,
            "1-10": result.get("low", 0) or 0,
            "11-100": result.get("medium", 0) or 0,
            ">100": result.get("high", 0) or 0
        }

    try:
        return _fetch()
    except Exception as e:
        logger.error(f"获取评论点赞分布失败: {e}", exc_info=True)
        return {"0": 0, "1-10": 0, "11-100": 0, ">100": 0}