from flask import Blueprint, render_template, session, redirect, url_for, jsonify, request
from utils.db_utils import with_db_connection, execute_query, create_db_connection
from utils.text_utils import extract_keywords_bert, analyze_sentiment
from services.redis_service import redis_service
from services.cache_service import cache_service
import logging
from math import ceil

logger = logging.getLogger(__name__)

# 注意：这里使用 /dashboard 前缀，与前端调用保持一致
keywords_bp = Blueprint('keywords', __name__, url_prefix='/keywords')

# ------------------------------
# 页面渲染
# ------------------------------
@keywords_bp.route('/')
def index():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    username = session.get('username', '未知用户')
    return render_template('keywords.html', username=username)


# ------------------------------
# 热词详情页面
# ------------------------------
@keywords_bp.route('/hotword/<keyword>')
def hotword(keyword):
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    username = session.get('username', '未知用户')
    from urllib.parse import unquote
    keyword = unquote(keyword)
    return render_template('hotword.html', username=username, keyword=keyword)


# ------------------------------
# API：获取热词详情数据
# ------------------------------
@keywords_bp.route('/api/hotword/<keyword>')
def get_hotword_data(keyword):
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401
    from urllib.parse import unquote
    keyword = unquote(keyword)

    @with_db_connection
    def _get(conn):
        like_pattern = f'%{keyword}%'

        # 1. 获取关键词相关的时间线数据（按时间升序，左旧右新）
        sql = """
            SELECT DATE(crawl_time) as date, COUNT(*) as count
            FROM hot_events
            WHERE title LIKE %s
            GROUP BY DATE(crawl_time)
            ORDER BY date ASC
            LIMIT 30
        """
        rows = execute_query(conn, sql, (like_pattern,)) or []
        timeline = [{'date': str(row['date']), 'count': row['count']} for row in rows]

        # 2. 获取情感趋势（按时间升序）
        sql_sentiment = """
            SELECT DATE(crawl_time) as date,
                   SUM(CASE WHEN sentiment_score > 0.6 THEN 1 ELSE 0 END) as positive,
                   SUM(CASE WHEN sentiment_score < 0.4 THEN 1 ELSE 0 END) as negative,
                   SUM(CASE WHEN sentiment_score BETWEEN 0.4 AND 0.6 THEN 1 ELSE 0 END) as neutral
            FROM hot_events
            WHERE title LIKE %s AND sentiment_score IS NOT NULL
            GROUP BY DATE(crawl_time)
            ORDER BY date ASC
            LIMIT 30
        """
        sentiment_rows = execute_query(conn, sql_sentiment, (like_pattern,)) or []
        sentiment_trend = [{'date': str(row['date']), 'positive': int(row['positive'] or 0), 'negative': int(row['negative'] or 0), 'neutral': int(row['neutral'] or 0)} for row in sentiment_rows]

        # 3. 获取共现关键词 - 从 articles 表中搜索
        sql_cooccurrence = """
            SELECT a.content, he.title
            FROM articles a
            INNER JOIN hot_events he ON a.event_id = he.id
            WHERE he.title LIKE %s
            LIMIT 100
        """
        article_contents = execute_query(conn, sql_cooccurrence, (like_pattern,)) or []

        # 从内容中提取关键词
        cooccurrence = {}
        for item in article_contents:
            text = (item.get('content') or '') + ' ' + (item.get('title') or '')
            # 简单分词
            words = text.replace('，', ' ').replace('。', ' ').replace('！', ' ').replace('？', ' ').replace(',', ' ').replace('.', ' ').replace('\"', ' ').replace('\"', ' ').replace('"', ' ').split()
            for word in words:
                if len(word) >= 2 and word != keyword and word not in ['的是', '是的', '这个', '那个', '什么', '怎么', '如何', '为什么', '因为', '所以', '但是', '而且', '或者', '如果']:
                    cooccurrence[word] = cooccurrence.get(word, 0) + 1

        # 排序并取前20个
        cooccurrence_list = sorted(cooccurrence.items(), key=lambda x: x[1], reverse=True)[:20]
        cooccurrence = [{'name': k, 'count': v} for k, v in cooccurrence_list]

        # 4. 获取相关观点 - 从 articles 表获取内容
        sql_opinions = """
            SELECT a.id, a.content, a.publish_time, a.sentiment_score, he.heat
            FROM articles a
            INNER JOIN hot_events he ON a.event_id = he.id
            WHERE he.title LIKE %s
            ORDER BY he.heat DESC, a.like_count DESC
            LIMIT 20
        """
        opinion_rows = execute_query(conn, sql_opinions, (like_pattern,)) or []
        opinions = []
        for row in opinion_rows:
            sentiment = 'positive'
            score = float(row.get('sentiment_score') or 0.5)
            if score > 0.6:
                sentiment = 'positive'
            elif score < 0.4:
                sentiment = 'negative'
            else:
                sentiment = 'neutral'

            content = row.get('content', '')
            if content and len(content) > 10:
                opinions.append({
                    'id': row['id'],
                    'summary': content[:200] + '...' if len(content) > 200 else content,
                    'sentiment': sentiment,
                    'publish_time': str(row['publish_time']) if row.get('publish_time') else None
                })

        return {
            'keyword': keyword,
            'timeline': timeline,
            'sentiment_trend': sentiment_trend,
            'cooccurrence': cooccurrence,
            'opinions': opinions,
            'total_count': sum(item['count'] for item in timeline) if timeline else 0
        }

    return jsonify(_get())


# ------------------------------
# API：获取相关热词
# ------------------------------
@keywords_bp.route('/api/hotword/related/<keyword>')
def get_related_hotwords(keyword):
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401
    from urllib.parse import unquote
    keyword = unquote(keyword)

    @with_db_connection
    def _get(conn):
        like_pattern = f'%{keyword}%'

        # 获取与当前热词相关的其他热搜事件（排除当前关键词本身）
        sql = """
            SELECT id, title, heat, crawl_time
            FROM hot_events
            WHERE title LIKE %s AND title != %s
            ORDER BY heat DESC
            LIMIT 20
        """
        related_events = execute_query(conn, sql, (like_pattern, keyword)) or []

        # 直接返回热搜事件标题作为相关热词
        related = [{'keyword': row['title'], 'count': row['heat'], 'frequency': row['heat'], 'id': row['id']} for row in related_events]
        return {'related': related}

    return jsonify(_get())


# ------------------------------
# API：获取最新事件列表（用于综合分析）
# ------------------------------
# 前端调用 /api/events
@keywords_bp.route('/api/events')
def get_events():
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    @with_db_connection
    def _get(conn):
        # 获取最新的事件，按热度排序
        sql = "SELECT id, title, heat, sentiment_score FROM hot_events ORDER BY crawl_time DESC LIMIT 20"
        rows = execute_query(conn, sql) or []
        return [{'id': row['id'], 'title': row['title'], 'heat': row['heat'], 'sentiment_score': row.get('sentiment_score', 0.5)} for row in rows]

    events = _get()
    return jsonify({'events': events})


# ------------------------------
# API：获取综合分析的最新事件ID
# ------------------------------
@keywords_bp.route('/api/events/latest')
def get_latest_events_for_analysis():
    """获取用于综合分析的最新事件ID列表"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    count = request.args.get('count', 10, type=int)

    # 尝试从缓存获取
    cache_key = f'keywords_latest_events_{count}'
    cached = cache_service.get(cache_key)
    if cached:
        return jsonify({'event_ids': cached})

    @with_db_connection
    def _get(conn):
        sql = f"SELECT id FROM hot_events ORDER BY crawl_time DESC LIMIT {count}"
        rows = execute_query(conn, sql) or []
        return [row['id'] for row in rows]

    event_ids = _get()

    # 存入缓存
    cache_service.set(cache_key, event_ids)

    return jsonify({'event_ids': event_ids})


# ------------------------------
# API：获取关键词数据（支持多事件）
# ------------------------------
@keywords_bp.route('/api/keywords')
def get_keywords():
    """
    获取关键词数据（支持多事件综合分析）
    前端参数: event_ids (逗号分隔的事件ID列表), days, keyword_count
    """
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    # 支持两种参数格式：event_id (单个) 或 event_ids (多个)
    event_ids_str = request.args.get('event_ids', '')
    event_id = request.args.get('event_id', type=int)

    if event_ids_str:
        # 解析逗号分隔的事件ID列表
        try:
            event_ids = [int(eid) for eid in event_ids_str.split(',') if eid.strip()]
        except ValueError:
            event_ids = []
    elif event_id:
        event_ids = [event_id]
    else:
        # 默认获取最近的事件
        event_ids = get_latest_event_ids(10)

    if not event_ids:
        return jsonify({'error': '请选择事件'}), 400

    days = request.args.get('days', 30, type=int)
    keyword_count = request.args.get('keyword_count', 30, type=int)
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 30, type=int)

    # 缓存键
    cache_key = f"keywords:events:{','.join(map(str, event_ids))}:{days}:{keyword_count}:{page}"

    # 尝试从 Redis 获取缓存
    cached = redis_service.get_cached_analysis(cache_key)
    if cached:
        logger.debug(f"关键词缓存命中: {cache_key}")
        return jsonify(cached)

    # 从数据库获取文本列表（支持多事件）
    texts, total = get_texts_by_events(event_ids, days, page, per_page)

    if not texts:
        result_data = {
            'keywords': [],
            'total': 0,
            'page': page,
            'per_page': per_page,
            'total_pages': 0
        }
        redis_service.cache_analysis_results(cache_key, result_data, expire_hours=0.083)
        return jsonify(result_data)

    # 使用 BERT 提取关键词
    keywords_with_weight = extract_keywords_bert(texts, top_k=keyword_count, with_weight=True)

    # 分析每个关键词的情感倾向
    keywords_with_sentiment = analyze_keywords_sentiment(keywords_with_weight, texts)

    # 转换为前端需要的格式
    result = [
        {
            'keyword': word,
            'frequency': round(weight * 100, 2),  # 转换为出现频率
            'sentiment': sentiment
        }
        for word, weight, sentiment in keywords_with_sentiment
    ]

    result_data = {
        'keywords': result,
        'total': total,
        'page': page,
        'per_page': per_page,
        'total_pages': ceil(total / per_page) if total else 0
    }

    # 缓存30分钟
    redis_service.cache_analysis_results(cache_key, result_data, expire_hours=0.5)

    return jsonify(result_data)


def get_texts_by_events(event_ids, days=30, page=1, per_page=30):
    """根据事件ID列表、获取文本列表（评论内容 + 文章内容）"""
    if not event_ids:
        return [], 0

    @with_db_connection
    def _get(conn):
        # 构建事件ID列表
        event_ids_str = ','.join(str(eid) for eid in event_ids)

        base_where = f"content IS NOT NULL AND content != '' AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY) AND event_id IN ({event_ids_str})"
        # 需要传递两个参数，因为 UNION ALL 会复制 WHERE 条件到两个子查询
        params = [days, days]

        comments_sql = f"""
            SELECT CONVERT(content USING utf8mb4) AS content, publish_time
            FROM comments
            WHERE {base_where}
        """
        articles_sql = f"""
            SELECT CONVERT(content USING utf8mb4) AS content, publish_time
            FROM articles
            WHERE {base_where}
        """
        # UNION ALL
        base_sql = f"({comments_sql}) UNION ALL ({articles_sql})"

        # 查询总数
        count_sql = f"SELECT COUNT(*) as total FROM ({base_sql}) AS tmp"
        total_result = execute_query(conn, count_sql, tuple(params * 2), fetch_one=True)
        total = total_result['total'] if total_result else 0

        # 分页查询
        offset = (page - 1) * per_page
        paginated_sql = base_sql + " ORDER BY publish_time DESC LIMIT %s OFFSET %s"
        full_params = tuple(params * 2 + [per_page, offset])
        rows = execute_query(conn, paginated_sql, full_params) or []
        texts = [row['content'] for row in rows]

        return texts, total

    return _get()


def analyze_keywords_sentiment(keywords_with_weight, texts):
    """
    分析关键词的情感倾向
    通过统计关键词在正面/负面文本中的出现频率来判断
    """
    if not keywords_with_weight or not texts:
        return [(word, weight, 'neutral') for word, weight in keywords_with_weight]

    # 将文本分为正面和负面两组
    positive_texts = []
    negative_texts = []
    neutral_texts = []

    for text in texts:
        if not text:
            continue
        sentiment_score = analyze_sentiment(text)
        if sentiment_score > 0.6:
            positive_texts.append(text)
        elif sentiment_score < 0.4:
            negative_texts.append(text)
        else:
            neutral_texts.append(text)

    # 统计每个关键词在各情感类型文本中的出现次数
    results = []
    for word, weight in keywords_with_weight:
        pos_count = sum(1 for t in positive_texts if word in t)
        neg_count = sum(1 for t in negative_texts if word in t)
        neu_count = sum(1 for t in neutral_texts if word in t)

        total_count = pos_count + neg_count + neu_count

        if total_count == 0:
            sentiment = 'neutral'
        elif pos_count > neg_count and pos_count > neu_count:
            sentiment = 'positive'
        elif neg_count > pos_count and neg_count > neu_count:
            sentiment = 'negative'
        else:
            # 如果差距不大，根据比例判断
            if pos_count / total_count > 0.5:
                sentiment = 'positive'
            elif neg_count / total_count > 0.5:
                sentiment = 'negative'
            else:
                sentiment = 'neutral'

        results.append((word, weight, sentiment))

    return results


# ------------------------------
# API：关键词监控 - 添加监控
# ------------------------------
@keywords_bp.route('/api/monitor/add', methods=['POST'])
def add_keyword_monitor():
    """添加关键词到监控列表"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录', 'success': False}), 401

    user_id = session.get('user_id')
    data = request.get_json()
    keyword = data.get('keyword', '').strip()
    event_id = data.get('event_id', type=int)
    sentiment = data.get('sentiment', 'neutral')

    if not keyword:
        return jsonify({'error': '关键词不能为空', 'success': False}), 400

    # 确保监控表存在
    try:
        ensure_monitor_table()
    except Exception as e:
        logger.error(f"创建监控表失败: {e}")
        return jsonify({'error': '系统错误', 'success': False}), 500

    # 检查是否已存在
    conn = create_db_connection()
    if not conn:
        return jsonify({'error': '数据库连接失败', 'success': False}), 500

    try:
        # 检查是否已监控
        sql = "SELECT id FROM keyword_monitors WHERE user_id = %s AND keyword = %s"
        existing = execute_query(conn, sql, (user_id, keyword), fetch_one=True)

        if existing:
            return jsonify({'message': '关键词已在监控中', 'success': True, 'already_exists': True})

        # 添加监控
        sql = """
            INSERT INTO keyword_monitors (user_id, keyword, event_id, sentiment, created_at)
            VALUES (%s, %s, %s, %s, NOW())
        """
        execute_query(conn, sql, (user_id, keyword, event_id, sentiment))
        conn.commit()

        return jsonify({'message': '关键词已添加到监控', 'success': True})
    except Exception as e:
        logger.error(f"添加关键词监控失败: {e}")
        conn.rollback()
        return jsonify({'error': '添加失败', 'success': False}), 500
    finally:
        conn.close()


# ------------------------------
# API：关键词监控 - 移除监控
# ------------------------------
@keywords_bp.route('/api/monitor/remove', methods=['POST'])
def remove_keyword_monitor():
    """从监控列表移除关键词"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录', 'success': False}), 401

    user_id = session.get('user_id')
    data = request.get_json()
    keyword = data.get('keyword', '').strip()

    if not keyword:
        return jsonify({'error': '关键词不能为空', 'success': False}), 400

    conn = create_db_connection()
    if not conn:
        return jsonify({'error': '数据库连接失败', 'success': False}), 500

    try:
        sql = "DELETE FROM keyword_monitors WHERE user_id = %s AND keyword = %s"
        execute_query(conn, sql, (user_id, keyword))
        conn.commit()

        return jsonify({'message': '关键词已从监控中移除', 'success': True})
    except Exception as e:
        logger.error(f"移除关键词监控失败: {e}")
        conn.rollback()
        return jsonify({'error': '移除失败', 'success': False}), 500
    finally:
        conn.close()


# ------------------------------
# API：关键词监控 - 获取用户监控列表
# ------------------------------
@keywords_bp.route('/api/monitor/list')
def get_monitor_list():
    """获取用户监控的关键词列表"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    user_id = session.get('user_id')

    @with_db_connection
    def _get(conn):
        sql = """
            SELECT id, keyword, event_id, sentiment, created_at
            FROM keyword_monitors
            WHERE user_id = %s
            ORDER BY created_at DESC
            LIMIT 50
        """
        return execute_query(conn, sql, (user_id,)) or []

    monitors = _get()
    return jsonify(monitors)


# ------------------------------
# API：关键词监控 - 检查关键词是否已监控
# ------------------------------
@keywords_bp.route('/api/monitor/check')
def check_keyword_monitor():
    """检查关键词是否已被当前用户监控"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    user_id = session.get('user_id')
    keyword = request.args.get('keyword', '').strip()

    if not keyword:
        return jsonify({'is_monitored': False})

    @with_db_connection
    def _get(conn):
        sql = "SELECT id FROM keyword_monitors WHERE user_id = %s AND keyword = %s"
        result = execute_query(conn, sql, (user_id, keyword), fetch_one=True)
        return result is not None

    is_monitored = _get()
    return jsonify({'is_monitored': is_monitored})


def ensure_monitor_table():
    """确保关键词监控表存在"""
    conn = create_db_connection()
    if not conn:
        raise Exception("数据库连接失败")

    try:
        sql = """
            CREATE TABLE IF NOT EXISTS keyword_monitors (
                id INT AUTO_INCREMENT PRIMARY KEY,
                user_id INT NOT NULL,
                keyword VARCHAR(255) NOT NULL,
                event_id INT,
                sentiment VARCHAR(20) DEFAULT 'neutral',
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                UNIQUE KEY unique_user_keyword (user_id, keyword),
                INDEX idx_user_id (user_id),
                INDEX idx_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
        """
        execute_query(conn, sql)
        conn.commit()
        logger.info("关键词监控表已确保存在")
    finally:
        conn.close()


# ------------------------------
# 核心计算函数（供定时预热任务调用，不经过 Flask session 校验）
# ------------------------------
def compute_keywords_evolution(days=180, keyword_count=10):
    """
    计算关键词演化数据，返回结果字典（不含 Flask jsonify）
    供定时预热任务调用
    """
    # 直接查询所有最近的文本数据，不按事件ID过滤
    texts_data = get_all_recent_texts(days, fallback_days=365)

    if not texts_data:
        return {
            'keywords': [],
            'timeline': [],
            'burst_keywords': [],
            'longtail_keywords': [],
            'sentiment_trend': [],
            'date_range': [],
            'total_events': 0,
            'total_texts': 0,
            'message': '暂无评论或文章数据，请先爬取数据'
        }

    texts = [t['content'] for t in texts_data if t['content']]

    # 如果文本数量太少，扩大时间范围获取更多数据
    if len(texts) < 100:
        more_days = max(days * 2, 180)
        more_texts_data = get_all_recent_texts(more_days, fallback_days=365)
        if more_texts_data:
            more_texts = [t['content'] for t in more_texts_data if t['content']]
            texts.extend(more_texts)
            texts_data.extend(more_texts_data)

    texts = list(set(texts))

    # 从热词榜获取关键词
    hot_words_list = get_hot_words_from_db(period='week', top_k=keyword_count * 2)

    if not hot_words_list:
        return {
            'keywords': [],
            'timeline': [],
            'burst_keywords': [],
            'longtail_keywords': [],
            'sentiment_trend': [],
            'date_range': [],
            'total_events': 0,
            'total_texts': len(texts_data) if texts_data else 0,
            'message': '热词榜暂无数据，请先爬取热点事件',
            'keyword_source': 'hot_words'
        }

    # 按 keyword_count 取前一半和后一半
    half = keyword_count // 2
    if len(hot_words_list) >= keyword_count:
        top_keywords = [item['word'] for item in hot_words_list[:half]] + [item['word'] for item in hot_words_list[-half:]]
    else:
        top_keywords = [item['word'] for item in hot_words_list]
    keywords_with_weight = [(item['word'], item['weight']) for item in hot_words_list]
    keyword_source = 'hot_words'

    # 计算时间线
    timeline = calculate_keyword_timeline(top_keywords, days=days)

    # 分析爆发词和长尾词
    burst_keywords, longtail_keywords = analyze_burst_longtail(timeline, top_keywords)

    # 日期范围
    date_range = [entry['date'] for entry in timeline]

    # 计算情感趋势
    sentiment_trend = calculate_sentiment_trend(texts_data, date_range)

    # 关键词情感分析
    max_for_sentiment = max(keyword_count, len(keywords_with_weight))
    half_for_sentiment = keyword_count // 2
    keywords_for_sentiment = keywords_with_weight[:half_for_sentiment] + keywords_with_weight[-half_for_sentiment:] if len(keywords_with_weight) >= keyword_count else keywords_with_weight
    keywords_with_sentiment = analyze_keywords_sentiment(keywords_for_sentiment, texts)

    return {
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


# ------------------------------
# API：自动获取关键词演化数据（页面初始化）
# ------------------------------
@keywords_bp.route('/api/keywords/evolution')
def get_keywords_evolution():
    """
    自动获取关键词演化数据 - 页面初始化时调用
    无需用户选择，自动分析时间维度上的热门关键词变化
    支持使用热词榜前N个词作为关键词
    """
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    days = request.args.get('days', 180, type=int)  # 默认180天（6个月），确保能获取到历史数据
    keyword_count = request.args.get('keyword_count', 10, type=int)

    # 尝试从 Redis 获取（优先）
    redis_key = f'keywords_evolution:{days}:{keyword_count}'
    try:
        from services.redis_service import redis_service
        if redis_service.is_connected():
            redis_cached = redis_service.client.get(redis_key)
            if redis_cached:
                import json
                return jsonify(json.loads(redis_cached))
    except Exception as e:
        logger.warning(f"Redis 读取失败，降级到内存缓存: {e}")

    # 再查内存缓存（兼容旧逻辑）
    cache_key = f'keywords_evolution_{days}_{keyword_count}'
    cached = cache_service.get(cache_key)
    if cached:
        # 回填 Redis
        try:
            import json
            if redis_service.is_connected():
                redis_service.client.setex(redis_key, 7200, json.dumps(cached, ensure_ascii=False))
        except Exception:
            pass
        return jsonify(cached)

    # 调用核心计算函数（计算逻辑已抽取到 compute_keywords_evolution）
    result = compute_keywords_evolution(days, keyword_count)

    # 只有当有关键词数据时才缓存
    if result.get('keywords'):
        cache_service.set(cache_key, result)
        # 同步写入 Redis（2小时 TTL）
        try:
            import json
            if redis_service.is_connected():
                redis_service.client.setex(redis_key, 7200, json.dumps(result, ensure_ascii=False))
        except Exception:
            pass

    return jsonify(result)


def calculate_sentiment_trend(texts_data, date_range):
    """计算情感趋势"""
    from datetime import datetime

    # 按日期分组文本
    date_texts = {}
    # 调试：查看publish_time的值
    sample_times = []
    for item in texts_data:
        publish_time = item.get('publish_time')
        if not publish_time:
            continue
        # 只记录前5个样本
        if len(sample_times) < 5:
            sample_times.append(str(publish_time))
        if hasattr(publish_time, 'strftime'):
            date_key = publish_time.strftime('%Y-%m-%d')
        else:
            date_key = str(publish_time)[:10]

        if date_key not in date_texts:
            date_texts[date_key] = []
        date_texts[date_key].append(item.get('content', ''))

    logger.debug(f"calculate_sentiment_trend: publish_time样本: {sample_times}")
    logger.debug(f"calculate_sentiment_trend: date_texts keys (有数据的日期): {sorted(date_texts.keys())}")
    logger.debug(f"calculate_sentiment_trend: date_range 长度: {len(date_range)}")

    # 计算每天的情感分布
    trend = []
    for date in date_range:
        texts = date_texts.get(date, [])
        if not texts:
            trend.append({'date': date, 'positive': 0, 'negative': 0, 'neutral': 0})
            continue

        pos = neg = neu = 0
        for text in texts:
            if not text:
                continue
            score = analyze_sentiment(text)
            if score > 0.6:
                pos += 1
            elif score < 0.4:
                neg += 1
            else:
                neu += 1

        trend.append({
            'date': date,
            'positive': pos,
            'negative': neg,
            'neutral': neu
        })

    return trend


# ------------------------------
# API：关键词时间轴演化分析（综合多个事件）
# ------------------------------
@keywords_bp.route('/api/keywords/timeline')
def get_keywords_timeline():
    """获取关键词在时间维度上的热度变化（综合分析）"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    days = request.args.get('days', 180, type=int)  # 默认180天（6个月）
    top_n = request.args.get('top_n', 10, type=int)

    # 支持 event_ids (逗号分隔) 或 event_count
    event_ids_str = request.args.get('event_ids', '')
    event_count = request.args.get('event_count', 10, type=int)

    if event_ids_str:
        try:
            event_ids = [int(eid) for eid in event_ids_str.split(',') if eid.strip()]
        except ValueError:
            event_ids = []
    else:
        event_ids = get_latest_event_ids(event_count)

    if not event_ids:
        return jsonify({
            'timeline': [],
            'burst_keywords': [],
            'longtail_keywords': [],
            'date_range': [],
            'event_count': 0
        })

    # 缓存键
    cache_key = f"timeline:events:{','.join(map(str, event_ids))}:days:{days}:top_n:{top_n}"

    cached = redis_service.get_cached_analysis(cache_key)
    if cached:
        return jsonify(cached)

    # 获取所有文本数据（综合多个事件）
    texts_data = get_texts_from_multiple_events(event_ids, days)

    if not texts_data:
        return jsonify({
            'timeline': [],
            'burst_keywords': [],
            'longtail_keywords': [],
            'date_range': [],
            'event_count': len(event_ids)
        })

    # 提取关键词
    texts = [t['content'] for t in texts_data]
    keywords_with_weight = extract_keywords_bert(texts, top_k=top_n * 2, with_weight=True)
    top_keywords = [kw[0] for kw in keywords_with_weight[:top_n]]

    # 按日期统计每个关键词的出现频率
    timeline = calculate_keyword_timeline(top_keywords, days=days)

    # 分析爆发词和长尾词
    burst_keywords, longtail_keywords = analyze_burst_longtail(timeline, top_keywords)

    # 从时间线获取完整的日期范围
    date_range = [entry['date'] for entry in timeline]

    result = {
        'timeline': timeline,
        'burst_keywords': [{'keyword': kw['name'], 'coefficient_variation': kw['cv']} for kw in burst_keywords],
        'longtail_keywords': [{'keyword': kw['name'], 'coefficient_variation': kw['cv']} for kw in longtail_keywords],
        'date_range': date_range,
        'event_count': len(event_ids)
    }

    # 缓存30分钟
    redis_service.cache_analysis_results(cache_key, result, expire_hours=0.5)

    return jsonify(result)


# ------------------------------
# API：获取爆发词和长尾词
# ------------------------------
@keywords_bp.route('/api/keywords/burst')
def get_keywords_burst():
    """获取爆发词和长尾词列表"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    days = request.args.get('days', 30, type=int)
    keyword_count = request.args.get('keyword_count', 10, type=int)

    # 支持 event_ids (逗号分隔) 或 event_count
    event_ids_str = request.args.get('event_ids', '')
    event_count = request.args.get('event_count', 10, type=int)

    if event_ids_str:
        try:
            event_ids = [int(eid) for eid in event_ids_str.split(',') if eid.strip()]
        except ValueError:
            event_ids = []
    else:
        event_ids = get_latest_event_ids(event_count)

    if not event_ids:
        return jsonify({'burst_keywords': [], 'longtail_keywords': []})

    # 缓存键
    cache_key = f"burst:events:{','.join(map(str, event_ids))}:days:{days}:kw:{keyword_count}"

    cached = redis_service.get_cached_analysis(cache_key)
    if cached:
        return jsonify(cached)

    # 获取文本数据
    texts_data = get_texts_from_multiple_events(event_ids, days)
    if not texts_data:
        return jsonify({'burst_keywords': [], 'longtail_keywords': []})

    # 提取关键词
    texts = [t['content'] for t in texts_data]
    keywords_with_weight = extract_keywords_bert(texts, top_k=keyword_count * 3, with_weight=True)
    top_keywords = [kw[0] for kw in keywords_with_weight[:keyword_count]]

    # 按日期统计
    timeline = calculate_keyword_timeline(top_keywords, days=days)

    # 分析爆发词和长尾词
    burst_keywords, longtail_keywords = analyze_burst_longtail(timeline, top_keywords)

    result = {
        'burst_keywords': [{'keyword': kw['name'], 'coefficient_variation': kw['cv'], 'total': kw['total']} for kw in burst_keywords],
        'longtail_keywords': [{'keyword': kw['name'], 'coefficient_variation': kw['cv'], 'total': kw['total']} for kw in longtail_keywords]
    }

    # 缓存30分钟
    redis_service.cache_analysis_results(cache_key, result, expire_hours=0.5)

    return jsonify(result)


def get_latest_event_ids(count=10):
    """获取最新的N个事件ID"""
    @with_db_connection
    def _get(conn):
        sql = f"SELECT id FROM hot_events ORDER BY crawl_time DESC LIMIT {count}"
        rows = execute_query(conn, sql) or []
        return [row['id'] for row in rows]
    return _get()


def get_texts_from_multiple_events(event_ids, days, fallback_days=365):
    """
    从多个事件获取文本数据
    如果指定天数范围内没有数据，会自动尝试使用更大的时间范围
    """
    if not event_ids:
        return []

    # 先尝试指定天数的数据
    texts = _fetch_texts(event_ids, days)

    # 如果没有数据，尝试使用更大的时间范围
    if not texts and fallback_days > days:
        texts = _fetch_texts(event_ids, fallback_days)

    return texts


# ------------------------------
# 通用文本查询辅助函数
# ------------------------------
def _build_where_clause(event_ids, days, keyword_pattern=None):
    """构建通用的WHERE子句和参数

    Returns:
        tuple: (where_clause, params_tuple)
    """
    event_ids_str = ','.join(str(eid) for eid in event_ids)
    base = "content IS NOT NULL AND content != '' AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY) AND event_id IN ({})".format(event_ids_str)

    if keyword_pattern:
        base += " AND content LIKE %s"
        params = (days, keyword_pattern, days, keyword_pattern) if keyword_pattern else (days, days)
    else:
        params = (days, days)

    return base, params


def _fetch_texts_base(event_ids, days, keyword_pattern=None, limit=5000):
    """通用文本获取函数

    Args:
        event_ids: 事件ID列表
        days: 查询天数
        keyword_pattern: 可选的关键词匹配模式
        limit: 返回结果数量限制

    Returns:
        list: 文本数据列表，每个元素包含content和publish_time
    """
    if not event_ids:
        return []

    @with_db_connection
    def _get(conn):
        base_where, params = _build_where_clause(event_ids, days, keyword_pattern)

        comments_sql = f"SELECT CONVERT(content USING utf8mb4) AS content, publish_time FROM comments WHERE {base_where}"
        articles_sql = f"SELECT CONVERT(content USING utf8mb4) AS content, publish_time FROM articles WHERE {base_where}"
        base_sql = f"({comments_sql}) UNION ALL ({articles_sql})"

        # 添加 LIMIT 防止数据量过大，同时使用 RAND() 确保随机采样不同日期的数据
        sql = f"{base_sql} ORDER BY publish_time DESC LIMIT {limit}"
        rows = execute_query(conn, sql, params) or []

        return [{'content': row['content'], 'publish_time': row['publish_time']} for row in rows]

    return _get()


def _fetch_texts(event_ids, days):
    """从多个事件获取文本数据（内部方法）"""
    return _fetch_texts_base(event_ids, days)


def get_all_recent_texts(days, fallback_days=365):
    """
    获取所有最近的文本数据（不限制event_id）
    当按事件查询没有数据时使用此方法
    """
    # 先尝试指定天数的数据
    texts = _fetch_all_texts(days)

    # 如果没有数据，尝试使用更大的时间范围
    if not texts and fallback_days > days:
        texts = _fetch_all_texts(fallback_days)

    return texts


def _fetch_all_texts(days):
    """内部方法：从所有评论和文章获取文本数据（不限制event_id）"""
    @with_db_connection
    def _get(conn):
        base_where = "content IS NOT NULL AND content != '' AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY)"
        params = (days, days)

        # 直接查询所有数据，不限制数量，确保覆盖整个时间范围
        sql = f"""
            SELECT CONVERT(content USING utf8mb4) AS content, publish_time
            FROM comments
            WHERE {base_where}
            UNION ALL
            SELECT CONVERT(content USING utf8mb4) AS content, publish_time
            FROM articles
            WHERE {base_where}
        """
        rows = execute_query(conn, sql, params) or []

        return [{'content': row['content'], 'publish_time': row['publish_time']} for row in rows]

    return _get()


def calculate_keyword_timeline(keywords, days=None):
    """
    使用SQL预筛选，直接在数据库层统计关键词每天的出现次数
    性能优化：避免全量分词，直接用SQL COUNT + LIKE 聚合
    """
    import datetime

    if not keywords:
        # 生成空日期范围
        if days and days > 0:
            end_date = datetime.datetime.now().date()
            start_date = end_date - datetime.timedelta(days=days-1)
            date_range = []
            current = start_date
            while current <= end_date:
                date_range.append(current.strftime('%Y-%m-%d'))
                current += datetime.timedelta(days=1)
        else:
            date_range = []

        return [{'date': d, **{k: 0 for k in keywords}} for d in date_range]

    # 生成日期范围
    if days and days > 0:
        end_date = datetime.datetime.now().date()
        start_date = end_date - datetime.timedelta(days=days-1)
    else:
        end_date = datetime.datetime.now().date()
        start_date = end_date - datetime.timedelta(days=29)

    # 初始化日期统计
    date_stats = {}
    current = start_date
    while current <= end_date:
        date_key = current.strftime('%Y-%m-%d')
        date_stats[date_key] = {kw: 0 for kw in keywords}
        current += datetime.timedelta(days=1)

    logger.debug(f"calculate_keyword_timeline: 关键词数量: {len(keywords)}, 天数: {days}")

    # 为每个关键词执行SQL统计
    for keyword in keywords:
        try:
            # 构建LIKE模式
            like_pattern = f"%{keyword}%"

            @with_db_connection
            def _count_keyword(conn):
                # 统计comments表
                comments_sql = """
                    SELECT DATE(publish_time) as date, COUNT(*) as cnt
                    FROM comments
                    WHERE publish_time >= %s AND publish_time <= %s AND content LIKE %s
                    GROUP BY DATE(publish_time)
                """
                # 统计articles表
                articles_sql = """
                    SELECT DATE(publish_time) as date, COUNT(*) as cnt
                    FROM articles
                    WHERE publish_time >= %s AND publish_time <= %s AND content LIKE %s
                    GROUP BY DATE(publish_time)
                """
                start_dt = datetime.datetime.combine(start_date, datetime.time.min)
                end_dt = datetime.datetime.combine(end_date, datetime.time.max)

                c_rows = execute_query(conn, comments_sql, (start_dt, end_dt, like_pattern)) or []
                a_rows = execute_query(conn, articles_sql, (start_dt, end_dt, like_pattern)) or []

                # 合并结果
                result = {}
                for row in c_rows + a_rows:
                    d = row['date'].strftime('%Y-%m-%d') if hasattr(row['date'], 'strftime') else str(row['date'])[:10]
                    result[d] = result.get(d, 0) + row['cnt']
                return result

            keyword_counts = _count_keyword()
            for date_key, count in keyword_counts.items():
                if date_key in date_stats:
                    date_stats[date_key][keyword] = count

        except Exception as e:
            logger.error(f"统计关键词 {keyword} 失败: {e}")
            continue

    # 转换为前端格式
    timeline = []
    for date in sorted(date_stats.keys()):
        entry = {'date': date}
        entry.update(date_stats[date])
        timeline.append(entry)

    logger.debug(f"calculate_keyword_timeline: 完成，timeline长度: {len(timeline)}")

    return timeline


def analyze_burst_longtail(timeline_data, keywords):
    """分析爆发词和长尾词"""
    if not timeline_data:
        return [], []

    # 计算每个关键词的总出现次数和方差
    keyword_stats = {}
    for keyword in keywords:
        values = [entry.get(keyword, 0) for entry in timeline_data]
        total = sum(values)
        if total == 0:
            continue

        # 计算方差（衡量波动程度）
        avg = total / len(values)
        variance = sum((v - avg) ** 2 for v in values) / len(values)
        std_dev = variance ** 0.5
        coefficient_of_variation = std_dev / avg if avg > 0 else 0

        keyword_stats[keyword] = {
            'total': total,
            'cv': coefficient_of_variation,  # 变异系数
            'values': values
        }

    # 爆发词：变异系数高（波动大）且总出现次数较多
    burst_keywords = sorted(
        [(kw, stats) for kw, stats in keyword_stats.items() if stats['cv'] > 0.5 and stats['total'] > 5],
        key=lambda x: x[1]['cv'],
        reverse=True
    )[:5]

    # 长尾词：变异系数低（持续稳定）且总出现次数较多
    longtail_keywords = sorted(
        [(kw, stats) for kw, stats in keyword_stats.items() if stats['cv'] < 0.3 and stats['total'] > 3],
        key=lambda x: x[1]['total'],
        reverse=True
    )[:5]

    return [
        {'name': kw, 'total': stats['total'], 'cv': round(stats['cv'], 2)}
        for kw, stats in burst_keywords
    ], [
        {'name': kw, 'total': stats['total'], 'cv': round(stats['cv'], 2)}
        for kw, stats in longtail_keywords
    ]


# ------------------------------
# API：关键词共现网络（综合分析）
# ------------------------------
@keywords_bp.route('/api/keywords/cooccurrence')
def get_keywords_cooccurrence():
    """获取关键词共现关系网络（综合分析）"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    days = request.args.get('days', 30, type=int)
    top_n = request.args.get('top_n', 15, type=int)

    # 支持 event_ids (逗号分隔) 或 event_count
    event_ids_str = request.args.get('event_ids', '')
    event_count = request.args.get('event_count', 10, type=int)

    if event_ids_str:
        try:
            event_ids = [int(eid) for eid in event_ids_str.split(',') if eid.strip()]
        except ValueError:
            event_ids = []
    else:
        event_ids = get_latest_event_ids(event_count)

    if not event_ids:
        return jsonify({'nodes': [], 'links': []})

    cache_key = f"cooccurrence:events:{','.join(map(str, event_ids))}:days:{days}:top_n:{top_n}"

    cached = redis_service.get_cached_analysis(cache_key)
    if cached:
        return jsonify(cached)

    # 获取文本数据（综合多个事件）
    texts_data = get_texts_from_multiple_events(event_ids, days)
    texts = [t['content'] for t in texts_data if t['content']]

    if not texts:
        return jsonify({'nodes': [], 'links': []})

    # 提取关键词
    keywords_with_weight = extract_keywords_bert(texts, top_k=top_n, with_weight=True)
    top_keywords = [kw[0] for kw in keywords_with_weight]

    # 计算共现关系
    cooccurrence = calculate_cooccurrence(texts, top_keywords)

    # 转换为节点和连线格式
    nodes = []
    links = []

    # 添加节点（带情感分析）
    keywords_with_sentiment = analyze_keywords_sentiment(keywords_with_weight, texts)

    for word, weight, sentiment in keywords_with_sentiment:
        nodes.append({
            'name': word,
            'value': round(weight, 4),
            'sentiment': sentiment,
            'symbolSize': max(15, min(60, int(weight * 100)))
        })

    # 添加连线
    for (kw1, kw2), count in cooccurrence.items():
        if count >= 2:  # 至少共现2次
            links.append({
                'source': kw1,
                'target': kw2,
                'value': count
            })

    result = {'nodes': nodes, 'links': links}

    # 缓存30分钟
    redis_service.cache_analysis_results(cache_key, result, expire_hours=0.5)

    return jsonify(result)


def calculate_cooccurrence(texts, keywords):
    """计算关键词共现关系"""
    cooccurrence = {}

    for text in texts:
        # 找出本文中出现的关键词
        present_keywords = [kw for kw in keywords if kw in text]

        # 统计两两共现
        for i in range(len(present_keywords)):
            for j in range(i + 1, len(present_keywords)):
                pair = tuple(sorted([present_keywords[i], present_keywords[j]]))
                cooccurrence[pair] = cooccurrence.get(pair, 0) + 1

    return cooccurrence


# ------------------------------
# API：驱动因素分析（综合分析）
# ------------------------------
@keywords_bp.route('/api/keywords/drivers')
def get_keyword_drivers():
    """分析指定关键词的驱动因素（伴随词，综合分析）"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    keyword = request.args.get('keyword', '').strip()
    days = request.args.get('days', 30, type=int)

    # 支持 event_ids (逗号分隔) 或 event_count
    event_ids_str = request.args.get('event_ids', '')
    event_count = request.args.get('event_count', 10, type=int)

    if event_ids_str:
        try:
            event_ids = [int(eid) for eid in event_ids_str.split(',') if eid.strip()]
        except ValueError:
            event_ids = []
    else:
        event_ids = get_latest_event_ids(event_count)

    if not keyword:
        return jsonify({'error': '请输入关键词'}), 400

    cache_key = f"drivers:{keyword}:events:{','.join(map(str, event_ids))}:days:{days}"

    cached = redis_service.get_cached_analysis(cache_key)
    if cached:
        return jsonify(cached)

    if not event_ids:
        return jsonify({
            'main_keyword': keyword,
            'drivers': [],
            'sentiment_distribution': {'positive': 0, 'negative': 0, 'neutral': 0}
        })

    # 获取包含该关键词的文本（综合多个事件）
    texts_data = get_texts_with_keyword_from_events(event_ids, keyword, days)
    texts = [t['content'] for t in texts_data if t['content']]

    if not texts:
        return jsonify({
            'main_keyword': keyword,
            'drivers': [],
            'sentiment_distribution': {'positive': 0, 'negative': 0, 'neutral': 0}
        })

    # 提取驱动词（排除主关键词）
    driver_keywords = extract_keywords_bert(texts, top_k=20, with_weight=True)
    driver_keywords = [(kw, w) for kw, w in driver_keywords if kw != keyword][:10]

    # 分析每个驱动词与主关键词的情感关系
    drivers = []
    for driver, weight in driver_keywords:
        pos_count = sum(1 for t in texts if driver in t and analyze_sentiment(t) > 0.6)
        neg_count = sum(1 for t in texts if driver in t and analyze_sentiment(t) < 0.4)
        total = pos_count + neg_count

        if total > 0:
            sentiment = 'positive' if pos_count > neg_count else 'negative'
            drivers.append({
                'keyword': driver,
                'weight': round(weight, 4),
                'sentiment': sentiment,
                'positive_count': pos_count,
                'negative_count': neg_count
            })

    # 情感分布
    sentiment_dist = {'positive': 0, 'negative': 0, 'neutral': 0}
    for text in texts:
        score = analyze_sentiment(text)
        if score > 0.6:
            sentiment_dist['positive'] += 1
        elif score < 0.4:
            sentiment_dist['negative'] += 1
        else:
            sentiment_dist['neutral'] += 1

    result = {
        'main_keyword': keyword,
        'drivers': drivers,
        'sentiment_distribution': sentiment_dist,
        'total_texts': len(texts)
    }

    redis_service.cache_analysis_results(cache_key, result, expire_hours=0.5)

    return jsonify(result)


# ------------------------------
# API：典型观点抽样（综合分析）
# ------------------------------
@keywords_bp.route('/api/keywords/opinions')
def get_keyword_opinions():
    """获取包含关键词的典型观点（综合分析）"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    keyword = request.args.get('keyword', '').strip()
    days = request.args.get('days', 30, type=int)
    limit = request.args.get('limit', 10, type=int)
    event_count = request.args.get('event_count', 10, type=int)

    if not keyword:
        return jsonify({'error': '请输入关键词'}), 400

    # 获取最新的事件ID
    event_ids = get_latest_event_ids(event_count)

    if not event_ids:
        return jsonify({'opinions': []})

    # 获取包含关键词的文本（综合 texts_data = get多个事件）
    # 获取包含关键词的文本（综合多个事件）
    texts_data = get_texts_with_keyword_from_events(event_ids, keyword, days)

    if not texts_data:
        return jsonify({'opinions': []})

    # 对每条文本进行情感分析，并提取摘要
    opinions = []
    for item in texts_data[:limit * 3]:  # 多取一些，后面筛选
        text = item['content']
        if not text or len(text) < 10:
            continue

        sentiment_score = analyze_sentiment(text)
        if sentiment_score > 0.6:
            sentiment = 'positive'
        elif sentiment_score < 0.4:
            sentiment = 'negative'
        else:
            sentiment = 'neutral'

        # 提取包含关键词的句子作为摘要
        summary = extract_keyword_sentence(text, keyword)

        if summary:
            opinions.append({
                'summary': summary[:200] + '...' if len(summary) > 200 else summary,
                'sentiment': sentiment,
                'sentiment_score': sentiment_score,
                'publish_time': str(item['publish_time']) if item['publish_time'] else None
            })

        if len(opinions) >= limit:
            break

    # 按情感分布返回
    return jsonify({'opinions': opinions})


def extract_keyword_sentence(text, keyword):
    """提取包含关键词的句子"""
    sentences = text.replace('！', '。').replace('？', '。').replace('!', '.').replace('?', '.').split('。')
    for sentence in sentences:
        if keyword in sentence and len(sentence) >= 10:
            return sentence.strip()
    # 如果没找到包含关键词的完整句子，返回文本开头
    return text[:100] if len(text) >= 100 else text


def get_texts_with_keyword_from_events(event_ids, keyword, days):
    """从多个事件中获取包含指定关键词的文本"""
    if not event_ids:
        return []

    @with_db_connection
    def _get(conn):
        event_ids_str = ','.join(str(eid) for eid in event_ids)
        base_where = f"content IS NOT NULL AND content != '' AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY) AND event_id IN ({event_ids_str}) AND content LIKE %s"
        # 需要传递4个参数，因为 UNION ALL 会复制 WHERE 条件到两个子查询，每个子查询有2个占位符
        keyword_pattern = f'%{keyword}%'
        params = (days, keyword_pattern, days, keyword_pattern)

        comments_sql = f"""
            SELECT CONVERT(content USING utf8mb4) AS content, publish_time
            FROM comments
            WHERE {base_where}
        """
        articles_sql = f"""
            SELECT CONVERT(content USING utf8mb4) AS content, publish_time
            FROM articles
            WHERE {base_where}
        """
        base_sql = f"({comments_sql}) UNION ALL ({articles_sql})"

        sql = f"{base_sql} ORDER BY publish_time DESC LIMIT 300"
        rows = execute_query(conn, sql, params) or []

        return [{'content': row['content'], 'publish_time': row['publish_time']} for row in rows]

    return _get()


# ------------------------------
# API：获取热点事件列表（用于事件选择）
# ------------------------------
@keywords_bp.route('/api/events/heatmap')
def get_heatmap_events():
    """获取热度最高的事件列表供选择"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    limit = request.args.get('limit', 10, type=int)
    days = request.args.get('days', 30, type=int)

    @with_db_connection
    def _get(conn):
        sql = """
            SELECT id, title, heat, crawl_time,
                   (SELECT COUNT(*) FROM comments WHERE event_id = hot_events.id) as comment_count,
                   (SELECT COUNT(*) FROM articles WHERE event_id = hot_events.id) as article_count
            FROM hot_events
            WHERE crawl_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ORDER BY heat DESC
            LIMIT %s
        """
        rows = execute_query(conn, sql, (days, limit)) or []
        return [{
            'id': row['id'],
            'title': row['title'],
            'heat': row['heat'],
            'comment_count': row['comment_count'],
            'article_count': row['article_count'],
            'crawl_time': row['crawl_time'].strftime('%Y-%m-%d') if row['crawl_time'] else None
        } for row in rows]

    return jsonify({'events': _get()})


# ------------------------------
# API：关键词每日情感趋势
# ------------------------------
@keywords_bp.route('/api/keywords/trend')
def get_keyword_trend():
    """获取关键词每日的情感分布趋势"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    keyword = request.args.get('keyword', '').strip()
    event_ids = request.args.get('event_ids', '')
    days = request.args.get('days', 30, type=int)

    if not keyword:
        return jsonify({'error': '请输入关键词'}), 400

    # 解析事件ID
    if event_ids:
        event_ids = [int(eid) for eid in event_ids.split(',') if eid.strip()]
    else:
        event_ids = get_latest_event_ids(10)

    if not event_ids:
        return jsonify({'trend': []})

    @with_db_connection
    def _get(conn):
        event_ids_str = ','.join(str(eid) for eid in event_ids)
        # 统计每日正负面情感数量
        sql = f"""
            SELECT
                DATE(publish_time) as date,
                SUM(CASE WHEN sentiment_score > 0.1 THEN 1 ELSE 0 END) as positive,
                SUM(CASE WHEN sentiment_score < -0.1 THEN 1 ELSE 0 END) as negative,
                SUM(CASE WHEN sentiment_score >= -0.1 AND sentiment_score <= 0.1 THEN 1 ELSE 0 END) as neutral,
                COUNT(*) as total
            FROM (
                SELECT CONVERT(content USING utf8mb4) AS content, publish_time, sentiment_score
                FROM comments
                WHERE content LIKE %s AND event_id IN ({event_ids_str}) AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
                UNION ALL
                SELECT CONVERT(content USING utf8mb4) AS content, publish_time, sentiment_score
                FROM articles
                WHERE content LIKE %s AND event_id IN ({event_ids_str}) AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ) as combined
            GROUP BY DATE(publish_time)
            ORDER BY date
        """
        keyword_pattern = f'%{keyword}%'
        # 4 placeholders total: 2 in comments, 2 in articles
        params = (keyword_pattern, days, keyword_pattern, days)
        rows = execute_query(conn, sql, params) or []

        return [{
            'date': row['date'].strftime('%Y-%m-%d') if row['date'] else '',
            'positive': row['positive'],
            'negative': row['negative'],
            'neutral': row['neutral'],
            'total': row['total']
        } for row in rows]

    return jsonify({'trend': _get()})


# ------------------------------
# API：检测关键词热度转折点
# ------------------------------
@keywords_bp.route('/api/keywords/burst_points')
def get_burst_points():
    """检测关键词热度突变日期"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    keyword = request.args.get('keyword', '').strip()
    event_ids = request.args.get('event_ids', '')
    days = request.args.get('days', 30, type=int)
    threshold = request.args.get('threshold', 2.0, type=float)  # 变化率阈值

    if not keyword:
        return jsonify({'error': '请输入关键词'}), 400

    # 解析事件ID
    if event_ids:
        event_ids = [int(eid) for eid in event_ids.split(',') if eid.strip()]
    else:
        event_ids = get_latest_event_ids(10)

    if not event_ids:
        return jsonify({'burst_points': []})

    @with_db_connection
    def _get(conn):
        event_ids_str = ','.join(str(eid) for eid in event_ids)
        sql = f"""
            SELECT
                DATE(publish_time) as date,
                COUNT(*) as count
            FROM (
                SELECT publish_time FROM comments
                WHERE content LIKE %s AND event_id IN ({event_ids_str}) AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
                UNION ALL
                SELECT publish_time FROM articles
                WHERE content LIKE %s AND event_id IN ({event_ids_str}) AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ) as combined
            GROUP BY DATE(publish_time)
            ORDER BY date
        """
        keyword_pattern = f'%{keyword}%'
        params = (keyword_pattern, days, keyword_pattern, days)
        rows = execute_query(conn, sql, params) or []

        if len(rows) < 3:
            return []

        # 计算变化率
        burst_points = []
        counts = [row['count'] for row in rows]
        dates = [row['date'].strftime('%Y-%m-%d') for row in rows]

        for i in range(1, len(counts)):
            if counts[i-1] > 0:
                change_rate = counts[i] / counts[i-1]
                if change_rate >= threshold:
                    burst_points.append({
                        'date': dates[i],
                        'count': counts[i],
                        'prev_count': counts[i-1],
                        'change_rate': round(change_rate, 2),
                        'type': 'burst'  # 爆发
                    })
            elif counts[i] > 0:
                # 从0到有，也是突变
                burst_points.append({
                    'date': dates[i],
                    'count': counts[i],
                    'prev_count': 0,
                    'change_rate': float('inf'),
                    'type': 'emergence'  # 新出现
                })

        return burst_points

    return jsonify({'burst_points': _get()})


# ------------------------------
# API：多关键词对比
# ------------------------------
@keywords_bp.route('/api/keywords/compare', methods=['POST'])
def compare_keywords():
    """对比多个关键词的时间序列数据"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    data = request.get_json() or {}
    keywords = data.get('keywords', [])
    event_ids = data.get('event_ids', [])
    days = data.get('days', 30)

    if not keywords or len(keywords) < 2:
        return jsonify({'error': '请至少提供2个关键词'}), 400

    # 解析事件ID
    if not event_ids:
        event_ids = get_latest_event_ids(10)

    if not event_ids:
        return jsonify({'comparison': []})

    @with_db_connection
    def _get(conn):
        event_ids_str = ','.join(str(eid) for eid in event_ids)
        keyword_patterns = [f'%{kw}%' for kw in keywords]

        # 构建动态SQL
        date_sql = f"""
            SELECT DATE(publish_time) as date
            FROM (
                SELECT publish_time FROM comments
                WHERE event_id IN ({event_ids_str}) AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
                UNION ALL
                SELECT publish_time FROM articles
                WHERE event_id IN ({event_ids_str}) AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ) as combined
            GROUP BY DATE(publish_time)
            ORDER BY date
        """
        params = (days, days)
        date_rows = execute_query(conn, date_sql, params) or []
        dates = [row['date'].strftime('%Y-%m-%d') for row in date_rows]

        # 为每个关键词计算时间序列
        comparison = []
        for keyword in keywords:
            kw_pattern = f'%{keyword}%'
            kw_sql = f"""
                SELECT DATE(publish_time) as date, COUNT(*) as count
                FROM (
                    SELECT publish_time FROM comments
                    WHERE content LIKE %s AND event_id IN ({event_ids_str}) AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
                    UNION ALL
                    SELECT publish_time FROM articles
                    WHERE content LIKE %s AND event_id IN ({event_ids_str}) AND publish_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
                ) as combined
                GROUP BY DATE(publish_time)
                ORDER BY date
            """
            kw_params = (kw_pattern, days, kw_pattern, days)
            kw_rows = execute_query(conn, kw_sql, kw_params) or []

            # 转换为日期映射
            date_count = {row['date'].strftime('%Y-%m-%d'): row['count'] for row in kw_rows}

            # 填充完整时间序列
            keyword_data = []
            for date in dates:
                keyword_data.append({
                    'date': date,
                    'count': date_count.get(date, 0)
                })

            comparison.append({
                'keyword': keyword,
                'data': keyword_data,
                'total': sum(date_count.values())
            })

        return comparison

    return jsonify({'comparison': _get()})


# ========================= 新增：热度排行榜和热词榜 API =========================
# 导入分类函数（放在模块级别避免循环导入问题）
from utils.text_utils import categorize_event, extract_keywords, DEFAULT_EVENT_CATEGORIES


@keywords_bp.route('/api/heat-rank')
def get_heat_rank():
    """获取热度排行榜（支持分类筛选，带缓存避免重复查询）"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    category = request.args.get('category', 'all')
    limit = request.args.get('limit', 20, type=int)

    # 尝试从缓存获取全部分类好的事件（缓存键：heat_rank_categorized）
    categorized_cache = cache_service.get('heat_rank_categorized')
    if categorized_cache is None:
        # 缓存未命中：查询数据库 + 逐条分类
        @with_db_connection
        def _build_cache(conn):
            sql = "SELECT id, title, heat, crawl_time FROM hot_events ORDER BY heat DESC LIMIT 1000"
            rows = execute_query(conn, sql) or []

            # 按分类聚合：{ '社会民生': [...events], '政治时事': [...], ... }
            categorized = {}
            for row in rows:
                event_cat = categorize_event(row['title'])
                if event_cat not in categorized:
                    categorized[event_cat] = []
                categorized[event_cat].append({
                    'id': row['id'],
                    'title': row['title'],
                    'heat': row['heat'],
                    'category': event_cat,
                    'crawl_time': row['crawl_time'].strftime('%Y-%m-%d %H:%M') if row['crawl_time'] else None
                })
            return categorized

        categorized_cache = _build_cache()
        # 缓存 5 分钟（热度数据频繁变化，不宜太长）
        cache_service.set('heat_rank_categorized', categorized_cache)

    # 从缓存中提取指定分类
    if category == 'all' or category == '全部':
        events = []
        for cat_events in categorized_cache.values():
            events.extend(cat_events)
        events = events[:limit]
    else:
        events = (categorized_cache.get(category) or [])[:limit]

    return jsonify({'events': events, 'category': category})


@keywords_bp.route('/api/hot-words')
def get_hot_words():
    """获取热词榜（按周或月）"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    period = request.args.get('period', 'week')  # week 或 month
    top_k = request.args.get('top_k', 30, type=int)

    # 根据周期确定时间范围
    days = 7 if period == 'week' else 30

    # 缓存键
    cache_key = f"hot_words:{period}:{top_k}"

    cached = redis_service.get_cached_analysis(cache_key)
    if cached:
        return jsonify(cached)

    @with_db_connection
    def _get(conn):
        # 获取最近的事件标题作为热词来源
        sql = """
            SELECT title FROM hot_events
            WHERE crawl_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ORDER BY heat DESC
            LIMIT 200
        """
        rows = execute_query(conn, sql, (days,)) or []
        return [row['title'] for row in rows if row['title']]

    titles = _get()

    if not titles:
        return jsonify({'words': [], 'period': period})

    # 使用关键词提取函数获取热词
    hot_words = extract_keywords(titles, top_k=top_k, with_stopwords_filter=True, with_weight=True)

    result = {
        'words': [{'word': word, 'weight': round(weight, 4)} for word, weight in hot_words],
        'period': period,
        'total_events': len(titles)
    }

    # 缓存30分钟
    redis_service.cache_analysis_results(cache_key, result, expire_hours=0.5)

    return jsonify(result)


def get_hot_words_from_db(period='week', top_k=10):
    """从数据库获取热词榜关键词（供内部调用）"""
    from utils.text_utils import extract_keywords

    days = 7 if period == 'week' else 30

    @with_db_connection
    def _get(conn):
        # 获取最近的事件标题作为热词来源
        sql = """
            SELECT title FROM hot_events
            WHERE crawl_time >= DATE_SUB(NOW(), INTERVAL %s DAY)
            ORDER BY heat DESC
            LIMIT 200
        """
        rows = execute_query(conn, sql, (days,)) or []
        return [row['title'] for row in rows if row['title']]

    titles = _get()
    if not titles:
        return []

    # 使用关键词提取函数获取热词
    hot_words = extract_keywords(titles, top_k=top_k, with_stopwords_filter=True, with_weight=True)

    return [{'word': word, 'weight': weight} for word, weight in hot_words]


@keywords_bp.route('/api/categories')
def get_categories():
    """获取所有分类及其热度统计（带缓存）"""
    if not session.get('logged_in'):
        return jsonify({'error': '未登录'}), 401

    # 尝试从缓存获取（与 heat-rank 共享同一个分类好的数据缓存）
    categorized_cache = cache_service.get('heat_rank_categorized')
    if categorized_cache is None:
        # 缓存不存在，先生成缓存
        @with_db_connection
        def _build_cache(conn):
            sql = "SELECT id, title, heat FROM hot_events ORDER BY heat DESC LIMIT 1000"
            rows = execute_query(conn, sql) or []
            categorized = {}
            for row in rows:
                event_cat = categorize_event(row['title'])
                if event_cat not in categorized:
                    categorized[event_cat] = []
                categorized[event_cat].append(row['heat'] or 0)
            return categorized

        categorized_cache = _build_cache()
        cache_service.set('heat_rank_categorized', categorized_cache)

    # 基于分类好的数据计算统计
    category_stats = {cat: {'count': 0, 'total_heat': 0} for cat in DEFAULT_EVENT_CATEGORIES.keys()}
    category_stats['其他'] = {'count': 0, 'total_heat': 0}

    for cat, heats in categorized_cache.items():
        count = len(heats)
        total = sum(heats)
        if cat in category_stats:
            category_stats[cat]['count'] += count
            category_stats[cat]['total_heat'] += total
        else:
            category_stats['其他']['count'] += count
            category_stats['其他']['total_heat'] += total

    categories = [
        {
            'name': cat,
            'count': stats['count'],
            'total_heat': stats['total_heat'],
            'avg_heat': round(stats['total_heat'] / stats['count'], 2) if stats['count'] > 0 else 0
        }
        for cat, stats in category_stats.items()
    ]
    categories.sort(key=lambda x: x['total_heat'], reverse=True)

    return jsonify({'categories': categories})