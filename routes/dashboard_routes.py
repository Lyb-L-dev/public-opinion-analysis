from typing import Any
from flask import Blueprint, render_template, jsonify, request, session, redirect, url_for
from models.event import Event
from models.comment import Comment
from models.article import Article
from utils.db_utils import with_db_connection, execute_query
from utils.text_utils import analyze_sentiment, get_sentiment_type, extract_keywords
from utils.chart_utils import generate_sentiment_spread_chart, get_sentiment_trend_raw_data
from services.cache_service import cache_service
from services.favorite_service import FavoriteService
import datetime
import logging
from math import ceil

# 初始化日志
logger = logging.getLogger(__name__)

dashboard_bp = Blueprint('dashboard', __name__, url_prefix='/dashboard')


def get_event_sentiment_dist(event_id):
    @with_db_connection
    def _get(conn):
        sql = """
            SELECT
                SUM(CASE WHEN sentiment_score > 0.7 THEN 1 ELSE 0 END) as positive,
                SUM(CASE WHEN sentiment_score BETWEEN 0.3 AND 0.7 THEN 1 ELSE 0 END) as neutral,
                SUM(CASE WHEN sentiment_score < 0.3 THEN 1 ELSE 0 END) as negative,
                COUNT(*) as total
            FROM comments 
            WHERE event_id = %s
        """
        result = execute_query(conn, sql, (event_id,), fetch_one=True)
        if not result or result['total'] == 0:
            return {
                'positive': 0, 'neutral': 0, 'negative': 0, 'total': 0,
                'pos_pct': 0, 'neu_pct': 100, 'neg_pct': 0
            }
        total = result['total']
        pos = result['positive'] or 0
        neu = result['neutral'] or 0
        neg = result['negative'] or 0
        return {
            'positive': pos, 'neutral': neu, 'negative': neg, 'total': total,
            'pos_pct': round(pos / total * 100, 1),
            'neu_pct': round(neu / total * 100, 1),
            'neg_pct': round(neg / total * 100, 1)
        }

    return _get()


def get_avg_sentiment():
    @with_db_connection
    def _get(conn):
        sql = """
            SELECT AVG(sentiment_score) as avg_sent 
            FROM comments 
            WHERE content IS NOT NULL AND content != ''
        """
        result = execute_query(conn, sql, fetch_one=True)
        # 直接返回数据库真实平均值，不重新计算，增加容错
        if not result or not result['avg_sent']:
            return 0.5
        try:
            return round(float(result['avg_sent']), 2)
        except (ValueError, TypeError):
            return 0.5

    return _get()


def get_negative_events_count():
    @with_db_connection
    def _get(conn):
        sql = """
            SELECT COUNT(DISTINCT he.id) as count
            FROM hot_events he
            LEFT JOIN comments c ON he.id = c.event_id
            WHERE c.sentiment_score < 0.3
              AND he.id IS NOT NULL
        """
        result = execute_query(conn, sql, fetch_one=True)
        return result['count'] if result and result['count'] else 0

    return _get()


def calculate_risk_index():
    total_events = Event.get_count() if Event.get_count() else 0
    if total_events == 0:
        return 0.0
    negative_events = get_negative_events_count()
    try:
        return round((negative_events / total_events) * 100, 0)
    except ZeroDivisionError:
        return 0.0


def get_risk_level():
    risk_index = calculate_risk_index()
    if risk_index >= 70:
        return '高'
    elif risk_index >= 30:
        return '中'
    else:
        return '低'


def get_new_negative_events_hour():
    one_hour_ago = datetime.datetime.now() - datetime.timedelta(hours=1)

    @with_db_connection
    def _get(conn):
        sql = """
            SELECT COUNT(DISTINCT he.id) as count
            FROM hot_events he
            LEFT JOIN comments c ON he.id = c.event_id
            WHERE c.sentiment_score < 0.3
              AND he.crawl_time >= %s
              AND he.id IS NOT NULL
        """
        result = execute_query(conn, sql, (one_hour_ago,), fetch_one=True)
        return result['count'] if result and result['count'] else 0

    return _get()


def get_risk_desc():
    risk_level = get_risk_level()
    new_negative = get_new_negative_events_hour()
    risk_index = calculate_risk_index()
    if risk_level == '高':
        return f'近1小时新增{new_negative}个高风险负面事件'
    elif risk_level == '中':
        return f'当前负面事件占比约{risk_index}%，整体风险中等'
    else:
        return '暂无明显高风险事件，整体舆情稳定'


def get_new_events_today():
    today = datetime.date.today()

    @with_db_connection
    def _get(conn):
        sql = "SELECT COUNT(*) as count FROM hot_events WHERE DATE(crawl_time) = %s"
        result = execute_query(conn, sql, (today,), fetch_one=True)
        return result['count'] if result and result['count'] else 0

    return _get()


def get_covered_events_count():
    @with_db_connection
    def _get(conn):
        sql = "SELECT COUNT(DISTINCT event_id) as count FROM comments WHERE event_id IS NOT NULL"
        result = execute_query(conn, sql, fetch_one=True)
        return result['count'] if result and result['count'] else 0

    return _get()


def get_likes_trend():
    try:
        today_avg = Comment.get_avg_likes() if Comment.get_avg_likes() else 0.0
        yesterday = datetime.date.today() - datetime.timedelta(days=1)

        @with_db_connection
        def _get_yesterday(conn):
            sql = "SELECT AVG(like_count) as avg FROM comments WHERE DATE(publish_time) = %s"
            result = execute_query(conn, sql, (yesterday,), fetch_one=True)
            if not result or not result['avg']:
                return 0.0
            return round(float(result['avg']), 1)

        yesterday_avg = _get_yesterday()

        if yesterday_avg == 0.0:
            return "暂无历史数据"
        if today_avg > yesterday_avg:
            return f"上升{round(float(today_avg) - float(yesterday_avg), 1)}"
        elif today_avg < yesterday_avg:
            return f"下降{round(float(yesterday_avg) - float(today_avg), 1)}"
        else:
            return "持平"
    except Exception as e:
        logger.error(f"计算点赞趋势失败: {str(e)}", exc_info=True)
        return "暂无数据"


def get_event_comment_count(event_id):
    @with_db_connection
    def _get(conn):
        sql = "SELECT COUNT(*) as count FROM comments WHERE event_id = %s"
        result = execute_query(conn, sql, (event_id,), fetch_one=True)
        return result['count'] if result and result['count'] else 0

    return _get()


def get_event_risk_level(sentiment_score):
    if not sentiment_score:
        return '低'
    try:
        score = float(sentiment_score)
        if score < 0.3:
            return '高'
        elif score < 0.6:
            return '中'
        else:
            return '低'
    except (ValueError, TypeError):
        return '低'


def extract_high_risk_keywords(limit=10):
    @with_db_connection
    def _get(conn):
        sql = """
            SELECT c.content FROM comments c
            LEFT JOIN hot_events he ON c.event_id = he.id
            WHERE c.sentiment_score < 0.3
              AND c.content IS NOT NULL
              AND c.content != ''
            LIMIT 1000
        """
        results = execute_query(conn, sql) or []
        texts = [str(r['content']).strip() for r in results if r.get('content') and str(r['content']).strip()]
        keywords = extract_keywords(texts, top_k=limit) if texts else []
        return [k[0] for k in keywords] if keywords else []

    return _get()


def get_real_time_risk_warning():
    try:
        high_risk_events = get_high_risk_events(limit=2) or []
        if not high_risk_events:
            return None
        event_titles = [e['title'] for e in high_risk_events[:2] if e.get('title') and e['title'].strip()]
        if not event_titles:
            return None
        # 容错：避免事件ID为空
        first_event_id = high_risk_events[0].get('id') if high_risk_events[0] else None
        negative_ratio = get_negative_ratio(first_event_id) if first_event_id else 0
        spread_count = get_spread_count(first_event_id) if first_event_id else 0
        return {
            'content': f"当前监测到{len(high_risk_events)}个高风险事件（{','.join(event_titles)}），其中{event_titles[0]}负面占比{negative_ratio}%，传播量{spread_count}+"
        }
    except Exception as e:
        logger.error(f"生成风险预警失败: {str(e)}", exc_info=True)
        return None


def get_high_risk_events(limit=5):
    @with_db_connection
    def _get(conn):
        sql = """
            SELECT he.id, he.title 
            FROM hot_events he
            LEFT JOIN (
                SELECT event_id, AVG(sentiment_score) as avg_score
                FROM comments
                WHERE event_id IS NOT NULL
                GROUP BY event_id
            ) c ON he.id = c.event_id
            WHERE c.avg_score < 0.3
            ORDER BY he.heat DESC 
            LIMIT %s
        """
        results = execute_query(conn, sql, (limit,)) or []
        return results

    return _get()


def get_negative_ratio(event_id):
    if not event_id:
        return 0.0

    @with_db_connection
    def _get(conn):
        sql = """
            SELECT 
                SUM(CASE WHEN sentiment_score < 0.3 THEN 1 ELSE 0 END) as negative,
                COUNT(*) as total
            FROM comments WHERE event_id = %s
        """
        result = execute_query(conn, sql, (event_id,), fetch_one=True)
        if not result or result['total'] == 0:
            return 0.0
        negative = result['negative'] or 0
        return round((negative / result['total']) * 100, 1)

    return _get()


def get_spread_count(event_id):
    if not event_id:
        return 0

    @with_db_connection
    def _get(conn):
        sql = """
            SELECT COUNT(*) as comment_count, COALESCE(SUM(IFNULL(like_count, 0)), 0) as like_count
            FROM comments WHERE event_id = %s
        """
        result = execute_query(conn, sql, (event_id,), fetch_one=True)
        if not result:
            return 0
        comment_count = result['comment_count'] or 0
        like_count = result['like_count'] or 0
        return comment_count + like_count

    return _get()


def get_sentiment_trend_data(time_range='week'):
    """根据时间范围查询情感趋势数据（已修复：读取数据库实际存在的日期，不再生成当前空日期）"""
    try:
        # 步骤1：先查询comments表中所有存在的有效日期（去重、排序）
        @with_db_connection
        def _get_existing_dates(conn):
            sql = """
                SELECT DISTINCT DATE(publish_time) as date
                FROM comments
                WHERE content IS NOT NULL AND content != ''
                ORDER BY date ASC
            """
            results = execute_query(conn, sql) or []
            return [item['date'] for item in results] if results else []

        existing_dates = _get_existing_dates()
        if not existing_dates:
            # 无有效数据时，返回默认值（保持原有容错逻辑）
            today = datetime.date.today()
            default_labels = [(today - datetime.timedelta(days=6 - i)).strftime('%m-%d') for i in range(7)]
            return ([0.5] * 7, default_labels)

        # 步骤2：根据时间范围筛选最近的有效日期（避免空数据）
        if time_range == 'week':
            target_dates = existing_dates[-7:]  # 最近7个有效日期
        elif time_range == 'month':
            target_dates = existing_dates[-30:]  # 最近30个有效日期
        elif time_range == 'quarter':
            target_dates = existing_dates[-90:]  # 最近90个有效日期
        else:
            target_dates = existing_dates[-7:]  # 默认取最近7天

        # 步骤3：查询每个有效日期对应的真实平均情感分
        trend_data = []
        trend_labels = []
        for date in target_dates:
            trend_labels.append(date.strftime('%m-%d'))  # 格式化日期标签（月-日）

            @with_db_connection
            def _get_daily_avg(conn, target_date=date):
                sql = """
                    SELECT AVG(c.sentiment_score) as avg_sentiment
                    FROM comments c
                    WHERE DATE(c.publish_time) = %s
                      AND c.content IS NOT NULL
                      AND c.content != ''
                """
                result = execute_query(conn, sql, (target_date,), fetch_one=True)
                if not result or not result['avg_sentiment']:
                    return 0.5  # 无数据时兜底（仅理论上存在）
                try:
                    return round(float(result['avg_sentiment']), 2)  # 保留2位小数
                except (ValueError, TypeError):
                    return 0.5

            trend_data.append(_get_daily_avg())

        return (trend_data, trend_labels)
    except Exception as e:
        logger.error(f"查询情感趋势失败: {str(e)}", exc_info=True)
        # 异常时返回默认值，保证页面不崩溃
        today = datetime.date.today()
        default_labels = [(today - datetime.timedelta(days=6 - i)).strftime('%m-%d') for i in range(7)]
        return ([0.5] * 7, default_labels)


def get_sentiment_dist_data():
    """查询整体情感分布数据（基于数据库真实 sentiment_score）"""
    try:
        @with_db_connection
        def _get_dist(conn):
            sql = """
                SELECT
                    SUM(CASE WHEN sentiment_score >= 0.7 THEN 1 ELSE 0 END) as positive,
                    SUM(CASE WHEN sentiment_score >= 0.3 AND sentiment_score < 0.7 THEN 1 ELSE 0 END) as neutral,
                    SUM(CASE WHEN sentiment_score < 0.3 THEN 1 ELSE 0 END) as negative,
                    COUNT(*) as total
                FROM comments
                WHERE content IS NOT NULL AND content != ''
            """
            result = execute_query(conn, sql, fetch_one=True)
            if not result or result['total'] == 0:
                return [35, 50, 15]  # 默认合理分布
            total = result['total']
            positive = round((result['positive'] or 0) / total * 100, 0)
            neutral = round((result['neutral'] or 0) / total * 100, 0)
            negative = round((result['negative'] or 0) / total * 100, 0)
            # 确保总和为100（避免四舍五入误差）
            total_pct = positive + neutral + negative
            if total_pct != 100:
                neutral += (100 - total_pct)
            return [int(positive), int(neutral), int(negative)]

        return _get_dist()
    except Exception as e:
        logger.error(f"查询情感分布数据失败: {str(e)}", exc_info=True)
        return [35, 50, 15]


# -------------------------- 核心路由：仪表盘（修复核心问题+新增历史趋势数据） --------------------------
@dashboard_bp.route('/', endpoint='dashboard')
def dashboard():
    try:
        if not session.get('logged_in'):
            return redirect(url_for('auth.login'))

        username = session.get('username', '未知用户')

        # 尝试从缓存获取数据（避免每次刷新都查询数据库）
        cached_data = cache_service.get('dashboard_data')
        if cached_data:
            # 缓存命中，直接使用缓存数据
            stats, hot_events, hot_words, charts, high_risk_words, risk_warning, historical_sentiment_trend = cached_data
        else:
            # 缓存不存在，执行查询并缓存结果

            # 补全：新增数据库真实平均情感分
            stats = {
                'risk_index': calculate_risk_index(),
                'risk_level': get_risk_level(),
                'risk_desc': get_risk_desc(),
                'total_events': Event.get_count() if Event.get_count() else 0,
                'new_events_today': get_new_events_today(),
                'total_comments': Comment.get_count() if Comment.get_count() else 0,
                'covered_events': get_covered_events_count(),
                'avg_likes': Comment.get_avg_likes() if Comment.get_avg_likes() else 0.0,
                'avg_sentiment': get_avg_sentiment(),  # 新增：数据库真实平均情感分
                'likes_trend': get_likes_trend(),
                'sentiment_trend_data': get_sentiment_trend_data()[0],
                'sentiment_trend_labels': get_sentiment_trend_data()[1],
                'sentiment_dist_data': get_sentiment_dist_data(),
                'total_events_max': max(Event.get_count() or 1, 1),
                'total_comments_max': max(Comment.get_count() or 1, 1),
                'avg_likes_max': 100
            }

            # 热门事件处理：禁用情感分重算，补全真实情感分布（核心修改：limit=5 → limit=6）
            hot_events = Event.get_all(limit=6) or []
            for event in hot_events:
                event.comment_count = get_event_comment_count(event.id) if event.id else 0
                event_sentiment_score = getattr(event, 'sentiment_score', 0.5)
                event.risk_level = get_event_risk_level(event_sentiment_score)
                event.heat = getattr(event, 'heat', 0)
                event.crawl_time = getattr(event, 'crawl_time', datetime.datetime.now())
                event.title = getattr(event, 'title', '未知事件')
                event.total_likes = round(Comment.get_avg_likes() * (event.comment_count or 1),
                                          0) if Comment.get_avg_likes() else 0
                # 补全：从数据库查询单个事件的真实情感分布
                event.sentiment_dist = get_event_sentiment_dist(event.id) if event.id else {
                    'pos_pct': 0, 'neu_pct': 100, 'neg_pct': 0
                }

            # 关键词云格式处理（增加容错，避免索引越界）
            high_risk_words_list = extract_high_risk_keywords(limit=10)
            hot_words = []
            for idx, word in enumerate(high_risk_words_list):
                if word and word.strip():
                    weight = round(1.0 - (idx * 0.05), 2) if idx < 10 else 0.5
                    hot_words.append((word.strip(), weight))

            # 图表数据（容错：避免图表函数不存在报错）
            try:
                sentiment_spread_chart = generate_sentiment_spread_chart() or ''
                # 新增：获取历史趋势原始数据，传递给前端交互式图表
                historical_sentiment_trend = get_sentiment_trend_raw_data() or {}
            except Exception as e:
                logger.warning(f"生成情感扩散图表失败: {e}")
                sentiment_spread_chart = ''
                historical_sentiment_trend = {}

            charts = {
                'sentiment_spread_trend': sentiment_spread_chart,
                'sentiment_trend': sentiment_spread_chart,
                'event_category': '',
                'sentiment_dist': '',
                'time_dist': ''
            }

            high_risk_words = extract_high_risk_keywords(limit=10)
            risk_warning = get_real_time_risk_warning()

            # 存入缓存（5分钟内有效）
            cached_data = (stats, hot_events, hot_words, charts, high_risk_words, risk_warning, historical_sentiment_trend)
            cache_service.set('dashboard_data', cached_data)

        return render_template(
            'dashboard.html',
            username=username,
            stats=stats,
            hot_events=hot_events,
            hot_words=hot_words,
            charts=charts,
            high_risk_words=high_risk_words,
            risk_warning=risk_warning,
            # 新增：传递历史趋势原始数据给前端
            historical_sentiment_trend=historical_sentiment_trend,
            skip_url_render=True
        )
    except Exception as e:
        logger.error(f"仪表板加载失败: {str(e)}", exc_info=True)
        fallback_username = session.get('username', '未知用户')
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <title>舆情分析平台 - 加载失败</title>
            <meta charset="utf-8">
            <style>
                body {{ font-family: Arial, sans-serif; margin: 50px; text-align: center; }}
                a {{ color: #0066cc; text-decoration: none; }}
                a:hover {{ text-decoration: underline; }}
            </style>
        </head>
        <body>
            <h1>舆情分析平台</h1>
            <p>用户名：{fallback_username} </p>
            <p>风险指数：0（数据加载失败，可尝试刷新页面）</p>
            <p> <a href="/auth/logout">退出登录</a> | <a href="/dashboard">刷新仪表盘</a> </p>
        </body>
        </html>
        """, 200


# -------------------------- 事件详情路由（优化：按Event ID查询，更稳定高效） --------------------------
@dashboard_bp.route('/event/detail/<int:event_id>')
def event_detail(event_id):
    try:
        if not session.get('logged_in'):
            return redirect(url_for('auth.login'))

        username = session.get('username', '未知用户')

        # 步骤1：查询事件基本信息（从hot_events表）
        @with_db_connection
        def get_event_base_info(conn):
            sql = """
                SELECT id, title, crawl_time, heat, sentiment_score
                FROM hot_events
                WHERE id = %s
            """
            return execute_query(conn, sql, (event_id,), fetch_one=True)

        event_info = get_event_base_info()
        if not event_info:  # 事件不存在，返回友好提示
            return render_template(
                'error_page.html',
                username=username,
                error_title="事件不存在",
                error_msg="该热点事件已被删除或不存在，请返回仪表盘重新选择",
                back_url=url_for('dashboard.dashboard')
            )

        # 步骤2：查询该事件关联的所有评论（使用非连接池查询，确保获取最新数据）
        @with_db_connection(use_pool=False)
        def get_event_comments(conn):
            sql = """
                SELECT c.username, c.content, c.publish_time, c.like_count as likes, c.sentiment_score
                FROM comments c
                WHERE c.event_id = %s
                ORDER BY c.publish_time DESC
            """
            return execute_query(conn, sql, (event_id,)) or []

        comments = get_event_comments()
        total_comments = len(comments)

        # 步骤3：查询该事件关联的所有文章（新增）
        @with_db_connection
        def get_event_articles(conn):
            sql = """
                SELECT id, author, content, publish_time, like_count, repost_count, comment_count,
                       article_id, sentiment_score, sentiment_type
                FROM articles
                WHERE event_id = %s
                ORDER BY publish_time DESC
            """
            return execute_query(conn, sql, (event_id,)) or []

        articles = get_event_articles()

        # 步骤4：统计文章情感分布（新增）
        article_pos = 0
        article_neu = 0
        article_neg = 0
        for art in articles:
            score = art.get('sentiment_score')
            if score is None:
                article_neu += 1
            elif score > 0.7:
                article_pos += 1
            elif score < 0.3:
                article_neg += 1
            else:
                article_neu += 1

        # 步骤5：计算评论情感统计（原有）
        pos_count = 0
        neu_count = 0
        neg_count = 0
        for c in comments:
            try:
                score = float(c.get('sentiment_score', 0.5))
                if score > 0.7:
                    pos_count += 1
                elif 0.3 <= score <= 0.7:
                    neu_count += 1
                else:
                    neg_count += 1
            except (ValueError, TypeError):
                neu_count += 1

        # 步骤6：获取事件情感分布（和首页保持一致）
        event_sentiment_dist = get_event_sentiment_dist(event_id)
        event_risk_level = get_event_risk_level(event_info.get('sentiment_score', 0.5))

        # 步骤7：传递数据到模板（新增文章相关变量）
        return render_template(
            'event_detail.html',
            username=username,
            event_info=event_info,  # 事件基本信息
            event_risk_level=event_risk_level,  # 风险等级
            event_sentiment_dist=event_sentiment_dist,  # 情感分布
            comments=comments,  # 评论列表
            total_comments=total_comments,  # 总评论数
            pos_count=pos_count,  # 正面评论数
            neu_count=neu_count,  # 中性评论数
            neg_count=neg_count,  # 负面评论数
            # 新增文章数据
            articles=articles,
            article_pos=article_pos,
            article_neu=article_neu,
            article_neg=article_neg
        )
    except Exception as e:
        logger.error(f"事件详情页加载失败: {str(e)}", exc_info=True)
        fallback_username = session.get('username', '未知用户')
        return render_template(
            'error_page.html',
            username=fallback_username,
            error_title="加载失败",
            error_msg="事件详情数据加载异常，请稍后重试",
            back_url=url_for('dashboard.dashboard')
        ), 500

# -------------------------- 其他路由 --------------------------
@dashboard_bp.route('/events', endpoint='events')
def events():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    username = session.get('username', '未知用户')
    hot_events = Event.get_all(limit=10) if Event.get_all(limit=10) else []
    for event in hot_events:
        event.comment_count = get_event_comment_count(event.id) if event.id else 0
        event.risk_level = get_event_risk_level(getattr(event, 'sentiment_score', 0.5))
    return render_template(
        'dashboard.html',
        username=username,
        stats={'risk_index': 0, 'sentiment_trend_data': [], 'sentiment_dist_data': [], 'sentiment_trend_labels': []},
        hot_events=hot_events,
        hot_words=[],
        charts={},
        skip_url_render=True
    )


@dashboard_bp.route('/comments', endpoint='comments')
def comments():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    username = session.get('username', '未知用户')
    return render_template(
        'dashboard.html',
        username=username,
        stats={'risk_index': 0, 'sentiment_trend_data': [], 'sentiment_dist_data': [], 'sentiment_trend_labels': []},
        hot_events=[],
        hot_words=[],
        charts={},
        skip_url_render=True
    )


@dashboard_bp.route('/history', endpoint='history')
def history():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    username = session.get('username', '未知用户')
    return render_template(
        'dashboard.html',
        username=username,
        stats={'risk_index': 0, 'sentiment_trend_data': [], 'sentiment_dist_data': [], 'sentiment_trend_labels': []},
        hot_events=[],
        hot_words=[],
        charts={},
        skip_url_render=True
    )


# -------------------------- 辅助接口 --------------------------
@dashboard_bp.route('/refresh', methods=['POST'])
def refresh():
    try:
        # 清除分析服务缓存
        from services.analysis_service import analysis_service
        analysis_service.clear_cache()
        # 清除 dashboard 缓存（确保刷新后能看到新数据）
        cache_service.delete('dashboard_data')
        # 清除关键词和智能分析相关缓存
        cache_service.delete('keywords_events')
        cache_service.delete('keywords_evolution_7_10')
        cache_service.delete('ml_diagnostic_data')
        cache_service.delete('ml_trend_prediction')
        cache_service.delete('ml_diagnostic_full')
        return jsonify({'success': True, 'msg': '数据刷新成功'})
    except Exception as e:
        logger.error(f"刷新数据失败: {str(e)}", exc_info=True)
        return jsonify({'success': False, 'msg': f"刷新失败：{str(e)}"})


# -------------------------- 评论爬取接口 --------------------------
@dashboard_bp.route('/api/crawl-comments/<int:event_id>', methods=['POST'])
def crawl_event_comments(event_id):
    """
    重新爬取指定事件的评论数据

    Args:
        event_id: 事件ID

    Returns:
        JSON: {
            'success': True/False,
            'message': '信息',
            'comment_count': 爬取的评论数
        }
    """
    try:
        # 检查登录状态
        if not session.get('logged_in'):
            return jsonify({'success': False, 'message': '请先登录'}), 401

        # 获取热搜标题
        @with_db_connection
        def get_event_title(conn):
            sql = "SELECT title FROM hot_events WHERE id = %s"
            result = execute_query(conn, sql, (event_id,), fetch_one=True)
            return result['title'] if result else None

        event_title = get_event_title()
        if not event_title:
            return jsonify({
                'success': False,
                'message': '事件不存在'
            }), 404

        # 调用爬虫爬取评论
        try:
            from crawlers.comment_crawler import crawl_comments_for_event

            # 在后台线程中运行爬虫（避免阻塞请求）
            import threading

            result = {'success': False, 'message': '爬取中...', 'comment_count': 0}

            def run_crawler():
                try:
                    crawl_result = crawl_comments_for_event(event_id, event_title, max_scrolls=15)
                    result.update(crawl_result)
                except Exception as e:
                    logger.error(f"爬取评论失败: {e}", exc_info=True)
                    result.update({
                        'success': False,
                        'message': f'爬取失败: {str(e)}',
                        'comment_count': 0
                    })

            # 启动爬虫线程
            crawler_thread = threading.Thread(target=run_crawler)
            crawler_thread.start()
            crawler_thread.join()  # 等待爬取完成（同步等待结果返回）

            return jsonify(result)

        except ImportError as e:
            logger.error(f"导入爬虫模块失败: {e}")
            return jsonify({
                'success': False,
                'message': f'爬虫模块加载失败: {str(e)}',
                'comment_count': 0
            })

    except Exception as e:
        logger.error(f"爬取评论接口异常: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': f'服务器异常: {str(e)}',
            'comment_count': 0
        }), 500



@dashboard_bp.route('/get_sentiment_trend')
def get_sentiment_trend():
    try:
        time_range = request.args.get('time_range', 'week')
        # 验证时间范围参数合法性
        valid_time_ranges = ['week', 'month', 'quarter']
        if time_range not in valid_time_ranges:
            time_range = 'week'
        trend_data, trend_labels = get_sentiment_trend_data(time_range)
        return jsonify({
            'success': True,
            'sentimentTrend': trend_data,
            'trendLabels': trend_labels
        })
    except Exception as e:
        logger.error(f"获取情感趋势接口失败: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'msg': f"获取失败：{str(e)}"
        })
    # 热点事件页面 - 完整路由


@dashboard_bp.route('/hot_events', endpoint='hot_events')
def hot_events():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))

    username = session.get('username', '未知用户')

    # 查询所有热点事件（按热度降序，关联评论数）
    @with_db_connection
    def get_all_hot_events(conn: object) -> list[Any] | None | Any:
        sql = """
             SELECT 
                 he.id, 
                 he.title, 
                 he.crawl_time, 
                 he.heat, 
                 he.sentiment_score,
                 COUNT(c.id) as comment_count
             FROM hot_events he
             LEFT JOIN comments c ON he.id = c.event_id
             GROUP BY he.id, he.title, he.crawl_time, he.heat, he.sentiment_score
             ORDER BY he.heat DESC
         """
        return execute_query(conn, sql) or []

    all_events = get_all_hot_events()

    # 补全每个事件的情感分布和风险等级
    for event in all_events:
        event['sentiment_dist'] = get_event_sentiment_dist(event['id'])
        event['risk_level'] = get_event_risk_level(event['sentiment_score'])
        # 处理时间格式（避免模板中报错）
        if event['crawl_time']:
            event['crawl_time_str'] = event['crawl_time'].strftime('%Y-%m-%d %H:%M')
        else:
            event['crawl_time_str'] = '未知时间'

    return render_template(
        'hot_events.html',
        username=username,
        all_events=all_events
    )


# 热点事件列表页面
@dashboard_bp.route('/hot-event-list', endpoint='hot_event_list')
def hot_event_list():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))

    username = session.get('username', '未知用户')
    keyword = request.args.get('keyword', '').strip()  # 新增关键词参数

    # 获取分页参数，默认第1页，每页20条
    page = request.args.get('page', 1, type=int)
    per_page = 20
    offset = (page - 1) * per_page

    # 根据关键词构造查询条件
    @with_db_connection
    def get_paginated_hot_events(conn):
        if keyword:
            # 如果有关键词，需要关联评论和文章，去重后查询事件
            sql = """
                SELECT DISTINCT he.id, he.title, he.crawl_time, he.heat, he.sentiment_score,
                       (SELECT COUNT(*) FROM comments WHERE event_id = he.id) as comment_count
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
                LEFT JOIN articles a ON he.id = a.event_id
                WHERE (c.content LIKE %s OR a.content LIKE %s)
                ORDER BY he.heat DESC
                LIMIT %s OFFSET %s
            """
            pattern = f'%{keyword}%'
            return execute_query(conn, sql, (pattern, pattern, per_page, offset)) or []
        else:
            # 原有逻辑（无关键词）
            sql = """
                SELECT he.id, he.title, he.crawl_time, he.heat, he.sentiment_score,
                       COUNT(c.id) as comment_count
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
                GROUP BY he.id, he.title, he.crawl_time, he.heat, he.sentiment_score
                ORDER BY he.heat DESC
                LIMIT %s OFFSET %s
            """
            return execute_query(conn, sql, (per_page, offset)) or []

    # 查询总事件数（同样需要考虑关键词）
    @with_db_connection
    def get_total_events(conn):
        if keyword:
            sql = """
                SELECT COUNT(DISTINCT he.id) as total
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
                LEFT JOIN articles a ON he.id = a.event_id
                WHERE (c.content LIKE %s OR a.content LIKE %s)
            """
            pattern = f'%{keyword}%'
            result = execute_query(conn, sql, (pattern, pattern), fetch_one=True)
        else:
            sql = "SELECT COUNT(*) as total FROM hot_events"
            result = execute_query(conn, sql, fetch_one=True)
        return result['total'] if result else 0

    all_events = get_paginated_hot_events()
    total_events = get_total_events()
    total_pages = ceil(total_events / per_page)

    # 补全每个事件的情感分布和风险等级
    for event in all_events:
        event['sentiment_dist'] = get_event_sentiment_dist(event['id'])
        event['risk_level'] = get_event_risk_level(event['sentiment_score'])
        if event['crawl_time']:
            event['crawl_time_str'] = event['crawl_time'].strftime('%Y-%m-%d %H:%M')
        else:
            event['crawl_time_str'] = '未知时间'

    return render_template(
        'hot_events.html',
        username=username,
        all_events=all_events,
        page=page,
        per_page=per_page,
        total_pages=total_pages,
        keyword=keyword  # 传递关键词，用于搜索框回显
    )
@dashboard_bp.route('/event/deep_analysis/<int:event_id>')
def event_deep_analysis(event_id):
    """单个事件深度分析页，使用模型方法和真实数据"""
    try:
        # 1. 登录验证
        if not session.get('logged_in'):
            return redirect(url_for('auth.login'))
        username = session.get('username', '管理员')

        # 2. 使用模型方法查询数据（替代原始SQL）
        event = Event.get_by_id(event_id)
        if not event:
            return render_template('event_deep_analysis.html', username=username, event=None, stats={},
                                   comments=[], hot_words=[], articles=[], article_pos=0, article_neu=0,
                                   article_neg=0, wordcloud_base64=None, insights=None, ml_insights=None,
                                   pagination=None, event_id=event_id)

        comments = Comment.get_by_event_id(event_id, limit=None, only_valid_content=False)
        articles = Article.get_by_event_id(event_id, limit=50)

        # 3. 构建事件字典（兼容模板，crawl_time已在to_dict中格式化）
        event_dict = event.to_dict()

        # 4. 统计文章情感分布（使用Article对象的sentiment_type属性）
        article_pos = sum(1 for a in articles if a.sentiment_type == '正面')
        article_neu = sum(1 for a in articles if a.sentiment_type == '中性')
        article_neg = sum(1 for a in articles if a.sentiment_type == '负面')

        # 5. 统计评论核心数据
        stats = {
            'total_comments': len(comments),
            'total_likes': 0,
            'positive_count': 0,
            'neutral_count': 0,
            'negative_count': 0,
            'positive_pct': 0.0,
            'neutral_pct': 0.0,
            'negative_pct': 0.0,
            'avg_comment_sentiment': 0.5,
            'sentiment_trend': 'stable',
            'sentiment_trend_data': [],
            'sentiment_trend_labels': [],
            'sentiment_dist_data': [],
            'comment_time_data': [],
            'comment_time_labels': []
        }

        if len(comments) > 0:
            total_sentiment = 0.0
            for comment in comments:
                stats['total_likes'] += comment.like_count
                score = comment.sentiment_score
                total_sentiment += score

                # 使用Comment对象的sentiment_type进行情感分类
                if comment.sentiment_type == '正面':
                    stats['positive_count'] += 1
                elif comment.sentiment_type == '负面':
                    stats['negative_count'] += 1
                else:
                    stats['neutral_count'] += 1

            # 占比计算
            total = stats['total_comments']
            if total > 0:
                stats['positive_pct'] = round((stats['positive_count'] / total) * 100, 1)
                stats['neutral_pct'] = round((stats['neutral_count'] / total) * 100, 1)
                stats['negative_pct'] = round((stats['negative_count'] / total) * 100, 1)
                stats['avg_comment_sentiment'] = round(total_sentiment / total, 3)

            # 情感分布数据（环形图）
            stats['sentiment_dist_data'] = [
                stats['positive_count'],
                stats['neutral_count'],
                stats['negative_count']
            ]

            # 情感趋势数据（按小时分组）
            trend_dict = {}
            time_dict = {}
            for comment in comments:
                if not comment.publish_time:
                    continue
                time_key = comment.publish_time.strftime('%Y-%m-%d %H')
                score = comment.sentiment_score

                if time_key not in trend_dict:
                    trend_dict[time_key] = {'sum': 0.0, 'count': 0}
                trend_dict[time_key]['sum'] += score
                trend_dict[time_key]['count'] += 1

                if time_key not in time_dict:
                    time_dict[time_key] = 0
                time_dict[time_key] += 1

            # 整理趋势数据
            sorted_time_keys = sorted(trend_dict.keys())
            stats['sentiment_trend_labels'] = sorted_time_keys
            stats['sentiment_trend_data'] = [
                round(trend_dict[k]['sum'] / trend_dict[k]['count'], 3)
                for k in sorted_time_keys if trend_dict[k]['count'] > 0
            ]

            # 时间分布数据
            stats['comment_time_labels'] = sorted(time_dict.keys())
            stats['comment_time_data'] = [time_dict[k] for k in sorted_time_keys]

            # 判定情感趋势方向
            if len(stats['sentiment_trend_data']) >= 2:
                first = stats['sentiment_trend_data'][0]
                last = stats['sentiment_trend_data'][-1]
                if last - first > 0.1:
                    stats['sentiment_trend'] = 'up'
                elif last - first < -0.1:
                    stats['sentiment_trend'] = 'down'
                else:
                    stats['sentiment_trend'] = 'stable'

        # 6. 真实关键词提取（替代硬编码mock数据）
        hot_words = []
        wordcloud_base64 = None
        if len(comments) > 0:
            comment_texts = [c.content for c in comments if c.content and c.content.strip()]
            all_texts = comment_texts + ([event.title] if event and event.title else [])

            if all_texts:
                try:
                    hot_words = extract_keywords(all_texts, top_k=30, with_weight=True)
                except Exception as ke:
                    logger.warning(f"关键词提取失败: {ke}")
                    hot_words = []

                # 生成词云图
                if hot_words:
                    try:
                        from services.analysis_service import analysis_service
                        wordcloud_base64 = analysis_service._generate_wordcloud(hot_words)
                    except Exception as we:
                        logger.warning(f"词云生成失败: {we}")
                        wordcloud_base64 = None

        # 7. 评论分页
        page = request.args.get('page', 1, type=int)
        per_page = 20
        total_comments = len(comments)
        total_pages = max(1, ceil(total_comments / per_page))
        page = max(1, min(page, total_pages))

        paged_comments = comments[(page - 1) * per_page: page * per_page]
        pagination = {
            'page': page,
            'per_page': per_page,
            'total_pages': total_pages,
            'total_comments': total_comments
        }

        # 8. 构建智能洞察
        insights = {
            'risk_assessment': {
                'level': event_dict.get('risk_level', '低'),
                'reasoning': []
            },
            'key_findings': [],
            'trend_direction': '上升' if stats['sentiment_trend'] == 'up' else '下降' if stats['sentiment_trend'] == 'down' else '平稳',
            'trend_reasoning': ''
        }

        # 风险理由
        if stats['negative_pct'] > 50:
            insights['risk_assessment']['reasoning'].append(f"负面评论占比达{stats['negative_pct']}%，超过半数")
        if stats['total_comments'] > 100 and stats['avg_comment_sentiment'] < 0.4:
            insights['risk_assessment']['reasoning'].append(
                f"大量评论（{stats['total_comments']}条）中平均情感分偏低（{stats['avg_comment_sentiment']}）")
        if event.heat > 1000:
            insights['risk_assessment']['reasoning'].append(f"事件热度较高（{event.heat}），传播风险大")
        if not insights['risk_assessment']['reasoning']:
            insights['risk_assessment']['reasoning'].append("当前事件风险可控，舆论整体稳定")

        # 关键发现
        if stats['total_comments'] > 0:
            if stats['positive_count'] > stats['negative_count']:
                insights['key_findings'].append(
                    f"正面评论（{stats['positive_count']}条）多于负面（{stats['negative_count']}条），舆论整体偏正面")
            elif stats['negative_count'] > stats['positive_count']:
                insights['key_findings'].append(
                    f"负面评论（{stats['negative_count']}条）占比较高，需关注舆论走向")
            else:
                insights['key_findings'].append("正负面评论数量相当，舆论态度分化")

            if stats['total_likes'] > 0:
                insights['key_findings'].append(f"评论互动活跃，总点赞数达{stats['total_likes']}")

        if hot_words and len(hot_words) >= 3:
            kw_names = [w[0] for w in hot_words[:5]]
            insights['key_findings'].append(f"舆论焦点集中在：{'、'.join(kw_names)}")

        # 趋势分析
        trend_map = {'up': '上升', 'down': '下降', 'stable': '平稳'}
        if stats['sentiment_trend'] == 'up':
            insights[
                'trend_reasoning'] = f"情感趋势整体呈上升态势，从{stats['sentiment_trend_data'][0]:.3f}升至{stats['sentiment_trend_data'][-1]:.3f}"
        elif stats['sentiment_trend'] == 'down':
            insights[
                'trend_reasoning'] = f"情感趋势呈下降态势，从{stats['sentiment_trend_data'][0]:.3f}降至{stats['sentiment_trend_data'][-1]:.3f}，建议持续关注"
        else:
            insights['trend_reasoning'] = "情感趋势整体平稳，舆情态势无明显波动"

        # 9. ML增强洞察（可选，失败不影响页面展示）
        ml_insights = None
        try:
            from services.ml_service import ml_service
            if ml_service:
                anomalies = ml_service.detect_anomalies(days=30)
                event_anomaly = None
                for a in anomalies if anomalies else []:
                    if a.get('event_id') == event_id:
                        event_anomaly = a
                        break

                influence = ml_service.calculate_influence_score(event_id)

                ml_insights = {
                    'is_anomaly': event_anomaly is not None,
                    'anomaly_severity': event_anomaly.get('severity', '低') if event_anomaly else None,
                    'anomaly_score': event_anomaly.get('score', 0) if event_anomaly else 0,
                    'influence_score': influence.get('score', 0) if influence and isinstance(influence, dict) else 0,
                }

                if event_anomaly:
                    insights['key_findings'].append(
                        f"ML模型检测到该事件存在异常（严重度: {ml_insights['anomaly_severity']}）")
        except Exception as mle:
            logger.warning(f"ML洞察获取失败（event_id={event_id}）: {mle}")
            ml_insights = None

        # 10. 传递数据到模板
        return render_template(
            'event_deep_analysis.html',
            username=username,
            event=event_dict,
            stats=stats,
            comments=paged_comments,
            hot_words=hot_words,
            wordcloud_base64=wordcloud_base64,
            articles=articles,
            article_pos=article_pos,
            article_neu=article_neu,
            article_neg=article_neg,
            insights=insights,
            ml_insights=ml_insights,
            pagination=pagination,
            event_id=event_id
        )

    except Exception as e:
        logger.error(f"查询深度分析数据失败：{str(e)}", exc_info=True)
        username = session.get('username', '管理员')
        return render_template('event_deep_analysis.html', username=username, event=None, stats={},
                               comments=[], hot_words=[], articles=[], article_pos=0, article_neu=0, article_neg=0,
                               wordcloud_base64=None, insights=None, ml_insights=None, pagination=None,
                               event_id=event_id)


# ---------------------- 趋势图切换接口（使用模型方法） ----------------------
@dashboard_bp.route('/event/deep_analysis/<int:event_id>/get_trend')
def get_event_trend(event_id):
    """获取事件情感趋势数据（按小时/按日期）"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'msg': '请先登录后操作'})

    range_type = request.args.get('range', 'hour')
    valid_ranges = ['hour', 'day']
    if range_type not in valid_ranges:
        range_type = 'hour'

    try:
        comments = Comment.get_by_event_id(event_id, limit=None, only_valid_content=False)

        trend_dict = {}
        for comment in comments:
            if not comment.publish_time:
                continue

            if range_type == 'day':
                time_key = comment.publish_time.strftime('%Y-%m-%d')
            else:
                time_key = comment.publish_time.strftime('%Y-%m-%d %H')

            if time_key not in trend_dict:
                trend_dict[time_key] = {'sum': 0.0, 'count': 0}
            trend_dict[time_key]['sum'] += comment.sentiment_score
            trend_dict[time_key]['count'] += 1

        sorted_time_keys = sorted(trend_dict.keys())
        sentiment_trend = [
            round(trend_dict[k]['sum'] / trend_dict[k]['count'], 3)
            for k in sorted_time_keys if trend_dict[k]['count'] > 0
        ]

        return jsonify({
            'success': True,
            'sentimentTrend': sentiment_trend,
            'trendLabels': sorted_time_keys
        })

    except Exception as e:
        logger.error(f"获取趋势数据失败：{str(e)}", exc_info=True)
        return jsonify({'success': False, 'msg': f'服务器异常：{str(e)}'})


# ---------------------- 关键词刷新接口（AJAX） ----------------------
@dashboard_bp.route('/event/deep_analysis/<int:event_id>/refresh_keywords')
def refresh_event_keywords(event_id):
    """AJAX端点：刷新事件关键词云"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'msg': '请先登录后操作'})

    try:
        comments = Comment.get_by_event_id(event_id, limit=None, only_valid_content=False)
        event = Event.get_by_id(event_id)

        comment_texts = [c.content for c in comments if c.content and c.content.strip()]
        all_texts = comment_texts + ([event.title] if event and event.title else [])

        if not all_texts:
            return jsonify({'success': False, 'msg': '无文本数据可提取关键词'})

        hot_words = extract_keywords(all_texts, top_k=30, with_weight=True)

        wordcloud_base64 = None
        if hot_words:
            try:
                from services.analysis_service import analysis_service
                wordcloud_base64 = analysis_service._generate_wordcloud(hot_words)
            except Exception as we:
                logger.warning(f"词云生成失败: {we}")

        return jsonify({
            'success': True,
            'hot_words': hot_words,
            'wordcloud_base64': wordcloud_base64
        })
    except Exception as e:
        logger.error(f"刷新关键词失败：{str(e)}", exc_info=True)
        return jsonify({'success': False, 'msg': f'服务器异常：{str(e)}'})


# -------------------------- 缓存预热函数 --------------------------
def warmup_all_caches():
    """
    后台预热所有页面的缓存
    在用户登录后调用，提升首次访问各页面的速度
    """
    import threading
    import time

    def _warmup():
        try:
            logger.info("开始预热页面缓存...")

            # 1. 预热 dashboard 缓存
            if not cache_service.get('dashboard_data'):
                # 执行 dashboard 的数据查询逻辑
                stats = {
                    'risk_index': calculate_risk_index(),
                    'risk_level': get_risk_level(),
                    'risk_desc': get_risk_desc(),
                    'total_events': Event.get_count() if Event.get_count() else 0,
                    'new_events_today': get_new_events_today(),
                    'total_comments': Comment.get_count() if Comment.get_count() else 0,
                    'covered_events': get_covered_events_count(),
                    'avg_likes': Comment.get_avg_likes() if Comment.get_avg_likes() else 0.0,
                    'avg_sentiment': get_avg_sentiment(),
                    'likes_trend': get_likes_trend(),
                    'sentiment_trend_data': get_sentiment_trend_data()[0],
                    'sentiment_trend_labels': get_sentiment_trend_data()[1],
                    'sentiment_dist_data': get_sentiment_dist_data(),
                    'total_events_max': max(Event.get_count() or 1, 1),
                    'total_comments_max': max(Comment.get_count() or 1, 1),
                    'avg_likes_max': 100
                }

                hot_events = Event.get_all(limit=6) or []
                for event in hot_events:
                    event.comment_count = get_event_comment_count(event.id) if event.id else 0
                    event_sentiment_score = getattr(event, 'sentiment_score', 0.5)
                    event.risk_level = get_event_risk_level(event_sentiment_score)
                    event.heat = getattr(event, 'heat', 0)
                    event.crawl_time = getattr(event, 'crawl_time', datetime.datetime.now())
                    event.title = getattr(event, 'title', '未知事件')
                    event.total_likes = round(Comment.get_avg_likes() * (event.comment_count or 1),
                                              0) if Comment.get_avg_likes() else 0
                    event.sentiment_dist = get_event_sentiment_dist(event.id) if event.id else {
                        'pos_pct': 0, 'neu_pct': 100, 'neg_pct': 0
                    }

                high_risk_words_list = extract_high_risk_keywords(limit=10)
                hot_words = []
                for idx, word in enumerate(high_risk_words_list):
                    if word and word.strip():
                        weight = round(1.0 - (idx * 0.05), 2) if idx < 10 else 0.5
                        hot_words.append((word.strip(), weight))

                try:
                    sentiment_spread_chart = generate_sentiment_spread_chart() or ''
                    historical_sentiment_trend = get_sentiment_trend_raw_data() or {}
                except Exception as e:
                    logger.warning(f"生成情感扩散图表失败: {e}")
                    sentiment_spread_chart = ''
                    historical_sentiment_trend = {}

                charts = {
                    'sentiment_spread_trend': sentiment_spread_chart,
                    'sentiment_trend': sentiment_spread_chart,
                    'event_category': '',
                    'sentiment_dist': '',
                    'time_dist': ''
                }

                high_risk_words = extract_high_risk_keywords(limit=10)
                risk_warning = get_real_time_risk_warning()

                cached_data = (stats, hot_events, hot_words, charts, high_risk_words, risk_warning, historical_sentiment_trend)
                cache_service.set('dashboard_data', cached_data)
                logger.info("Dashboard 缓存预热完成")

            # 2. 预热关键词页面数据
            try:
                if not cache_service.get('keywords_events'):
                    @with_db_connection
                    def _get_events(conn):
                        sql = "SELECT id, title, crawl_time, heat, sentiment_score FROM hot_events ORDER BY crawl_time DESC LIMIT 20"
                        return execute_query(conn, sql) or []
                    events = _get_events()
                    cache_service.set('keywords_events', events)
                    logger.info("关键词页面事件列表缓存预热完成")
            except Exception as e:
                logger.warning(f"关键词页面缓存预热失败: {e}")

            # 3. 预热智能分析页面数据 (ML Dashboard)
            try:
                from services.ml_service import ml_service
                if ml_service and not cache_service.get('ml_diagnostic_data'):
                    # 预热 ML 诊断数据
                    ml_data = {}
                    for days in [7, 14, 30]:
                        try:
                            data = ml_service._get_historical_sentiment_data(days)
                            ml_data[f'{days}天'] = len(data)
                        except:
                            ml_data[f'{days}天'] = 0
                    cache_service.set('ml_diagnostic_data', ml_data)
                    logger.info("智能分析页面缓存预热完成")
            except Exception as e:
                logger.warning(f"智能分析页面缓存预热失败: {e}")

            # 4. 预热关键词热度演化数据（最慢的API，优先预热）
            try:
                # 前端默认使用 days=7，所以预热 7 天的数据
                cache_key = 'keywords_evolution_7_10'
                if not cache_service.get(cache_key):
                    # 调用关键词热度演化逻辑
                    from routes.keywords_routes import get_keywords_evolution
                    # 使用与前端相同的默认参数
                    days = 7
                    keyword_count = 10

                    from routes.keywords_routes import (
                        get_all_recent_texts, get_hot_words_from_db,
                        calculate_keyword_timeline, analyze_burst_longtail,
                        calculate_sentiment_trend, analyze_keywords_sentiment
                    )

                    texts_data = get_all_recent_texts(days, fallback_days=365)
                    if texts_data:
                        texts = [t['content'] for t in texts_data if t['content']]

                        if len(texts) < 100:
                            more_days = max(days * 2, 30)
                            more_texts_data = get_all_recent_texts(more_days, fallback_days=365)
                            if more_texts_data:
                                more_texts = [t['content'] for t in more_texts_data if t['content']]
                                texts.extend(more_texts)
                                texts_data.extend(more_texts_data)

                        texts = list(set(texts))
                        hot_words_list = get_hot_words_from_db(period='week', top_k=10)

                        if hot_words_list:
                            if len(hot_words_list) >= 6:
                                top_keywords = [item['word'] for item in hot_words_list[:3]] + [item['word'] for item in hot_words_list[-3:]]
                            else:
                                top_keywords = [item['word'] for item in hot_words_list]

                            keywords_with_weight = [(item['word'], item['weight']) for item in hot_words_list]
                            keyword_source = 'hot_words'

                            timeline = calculate_keyword_timeline(top_keywords, days=days)
                            burst_keywords, longtail_keywords = analyze_burst_longtail(timeline, top_keywords)
                            date_range = [entry['date'] for entry in timeline]
                            sentiment_trend = calculate_sentiment_trend(texts_data, date_range)

                            keywords_for_sentiment = keywords_with_weight[:3] + keywords_with_weight[-3:] if len(keywords_with_weight) >= 6 else keywords_with_weight
                            keywords_with_sentiment = analyze_keywords_sentiment(keywords_for_sentiment, texts)

                            result = {
                                'keywords': [
                                    {'keyword': word, 'frequency': round(weight * 100, 2), 'sentiment': sentiment}
                                    for word, weight, sentiment in keywords_with_sentiment
                                ],
                                'timeline': timeline,
                                'burst_keywords': [{'keyword': kw['name'], 'coefficient_variation': kw['cv'], 'total': kw['total']} for kw in burst_keywords],
                                'longtail_keywords': [{'keyword': kw['name'], 'coefficient_variation': kw['cv'], 'total': kw['total']} for kw in longtail_keywords],
                                'sentiment_trend': sentiment_trend,
                                'date_range': date_range,
                                'total_events': 0,
                                'total_texts': len(texts),
                                'keyword_source': keyword_source
                            }

                            # 只有当有关键词数据时才缓存
                            if result.get('keywords'):
                                cache_service.set(cache_key, result)
                                logger.info("关键词热度演化缓存预热完成")
            except Exception as e:
                logger.warning(f"关键词热度演化缓存预热失败: {e}")

            logger.info("页面缓存预热完成")
        except Exception as e:
            logger.error(f"缓存预热失败: {str(e)}", exc_info=True)

    # 在后台线程中执行预热，避免阻塞登录响应
    warmup_thread = threading.Thread(target=_warmup, daemon=True)
    warmup_thread.start()