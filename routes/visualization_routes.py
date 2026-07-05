from flask import Blueprint, render_template, jsonify, request, session, redirect, url_for
from services.visualization_service import (
    get_wordcloud_data,
    get_event_sentiment_distribution,
    get_comment_trend,
    get_avg_sentiment, get_map_distribution, get_comment_like_distribution,
    get_event_heat_trend  # 从本服务导入，不再依赖 dashboard_routes
)
from models.event import Event
from models.comment import Comment
import logging

from utils import with_db_connection, execute_query

logger = logging.getLogger(__name__)

visualization_bp = Blueprint('visualization', __name__, url_prefix='/visualization')

def get_system_stats():
    """获取系统统计信息（与仪表盘保持一致）"""
    return {
        'total_events': Event.get_count() or 0,
        'total_comments': Comment.get_count() or 0,
        'avg_sentiment': get_avg_sentiment()
    }

@visualization_bp.route('/')
def index():
    """渲染舆情可视化主页"""
    # 手动检查登录状态（开发调试时可临时注释）
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))

    username = session.get('username', '未知用户')
    stats = get_system_stats()
    return render_template('visualization.html',
                           username=username,
                           stats=stats)

@visualization_bp.route('/api/wordcloud')
def wordcloud_api():
    """词云数据接口，返回 [{word, weight}, ...]"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    days = request.args.get('days', 30, type=int)
    data = get_wordcloud_data(days)
    return jsonify(data)

@visualization_bp.route('/api/event_sentiment_dist')
def event_sentiment_api():
    """事件情感分布接口，返回 {"正面": x, "中性": y, "负面": z}"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    data = get_event_sentiment_distribution()
    return jsonify(data)

@visualization_bp.route('/api/comment_trend')
def comment_trend_api():
    """评论情感趋势接口，返回 {"dates": [...], "scores": [...]}"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    range_type = request.args.get('range', 'week')
    data = get_comment_trend(range_type)
    return jsonify(data)

@visualization_bp.route('/api/map_data')
def map_data_api():
    """舆情地域分布数据接口
    支持 type=event（评论数量）或 type=sentiment（平均情感分）
    返回格式：[{"name": "北京", "value": 120}, ...]
    """
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    map_type = request.args.get('type', 'event')
    try:
        data = get_map_distribution(map_type)
        return jsonify(data)
    except Exception as e:
        logger.error(f"地图数据获取失败: {e}", exc_info=True)
        return jsonify({'error': '数据加载失败'}), 500


@visualization_bp.route('/api/event_heat_trend')
def event_heat_trend_api():
    """事件热度趋势接口"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    range_type = request.args.get('range', 'week')
    data = get_event_heat_trend(range_type)
    return jsonify(data)


@visualization_bp.route('/api/comment_like_distribution')
def comment_like_distribution_api():
    """评论点赞分布接口"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    data = get_comment_like_distribution()
    return jsonify(data)

@visualization_bp.route('/api/hot_rank')
def hot_rank_api():
    """返回热点事件排行，支持按时间范围筛选"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    range_type = request.args.get('range', 'week')
    limit = request.args.get('limit', 10, type=int)

    # 计算日期范围
    if range_type == 'week':
        days = 7
    else:  # month
        days = 30

    try:
        @with_db_connection
        def _fetch_hot_rank(conn):
            sql = """
                SELECT title, heat, crawl_time
                FROM hot_events
                WHERE heat IS NOT NULL
                AND crawl_time >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                ORDER BY heat DESC
                LIMIT %s
            """
            results = execute_query(conn, sql, (days, limit))
            return [{'title': row['title'], 'heat': int(row['heat'] or 0)} for row in results]

        data = _fetch_hot_rank()
        return jsonify(data)
    except Exception as e:
        logger.error(f"获取热点排行失败: {e}", exc_info=True)
        return jsonify({'error': '数据加载失败'}), 500

@visualization_bp.route('/api/comment_time_dist')
def comment_time_dist_api():
    """返回评论发布时间分布（按小时统计）"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    range_type = request.args.get('range', 'week')

    if range_type == 'week':
        days = 7
    else:  # month
        days = 30

    try:
        @with_db_connection
        def _fetch_time_dist(conn):
            sql = """
                SELECT HOUR(publish_time) as hour, COUNT(*) as count
                FROM comments
                WHERE publish_time IS NOT NULL
                AND publish_time >= DATE_SUB(CURDATE(), INTERVAL %s DAY)
                GROUP BY HOUR(publish_time)
                ORDER BY hour
            """
            results = execute_query(conn, sql, (days,))
            # 补齐24小时数据
            hour_map = {row['hour']: row['count'] for row in results}
            return [{'hour': h, 'count': hour_map.get(h, 0)} for h in range(24)]

        data = _fetch_time_dist()
        return jsonify(data)
    except Exception as e:
        logger.error(f"获取评论时间分布失败: {e}", exc_info=True)
        return jsonify({'error': '数据加载失败'}), 500

@visualization_bp.route('/api/heat_sentiment')
def heat_sentiment_api():
    """返回事件热度与情感关联数据（散点图）"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    limit = request.args.get('limit', 100, type=int)

    try:
        @with_db_connection
        def _fetch_heat_sentiment(conn):
            sql = """
                SELECT title, heat, sentiment_score
                FROM hot_events
                WHERE heat IS NOT NULL AND heat > 0
                ORDER BY heat DESC
                LIMIT %s
            """
            results = execute_query(conn, sql, (limit,))
            return [{
                'title': row['title'],
                'heat': int(row['heat'] or 0),
                'sentiment_score': float(row['sentiment_score']) if row['sentiment_score'] else 0.5
            } for row in results]

        data = _fetch_heat_sentiment()
        return jsonify(data)
    except Exception as e:
        logger.error(f"获取热度与情感关联失败: {e}", exc_info=True)
        return jsonify({'error': '数据加载失败'}), 500

@visualization_bp.route('/api/top_events')
def top_events_api():
    """返回热度最高的前N个事件，用于排行榜"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    limit = request.args.get('limit', 10, type=int)
    try:
        # 从 hot_events 表查询热度最高的记录，按 heat 降序
        @with_db_connection
        def _fetch_top_events(conn):
            sql = """
                SELECT title, heat
                FROM hot_events
                WHERE heat IS NOT NULL
                ORDER BY heat DESC
                LIMIT %s
            """
            results = execute_query(conn, sql, (limit,))
            return [{'title': row['title'], 'heat': int(row['heat'])} for row in results]

        data = _fetch_top_events()
        return jsonify(data)
    except Exception as e:
        logger.error(f"获取热度排行失败: {e}", exc_info=True)
        return jsonify({'error': '数据加载失败'}), 500