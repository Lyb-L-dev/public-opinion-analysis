# routes/enhanced_visualization_routes.py
"""
增强版可视化路由 - 提供机器学习驱动的舆情分析API
"""

from flask import Blueprint, render_template, jsonify, request, session, redirect, url_for
import logging
from datetime import datetime
from functools import wraps

from services.ml_service import ml_service
from services.cache_service import cache_service

logger = logging.getLogger(__name__)

enhanced_viz_bp = Blueprint('enhanced_visualization', __name__, url_prefix='/enhanced')

CACHE_TTL = {
    'diagnostic': 300,
    'comprehensive_report': 300,
    'ai_report': 600,
    'anomaly_detection': 300,
    'influence_scoring': 300,
    'data_diagnostic': 600,
    'ai_insight': 300
}

def api_response(data=None, error=None, status=200):
    """统一的API响应格式"""
    if error:
        return jsonify({'error': error}), status
    if data is None:
        return jsonify({}), status
    return jsonify(data), status

def handle_errors(f):
    """统一的错误处理装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        except Exception as e:
            logger.error(f"{f.__name__} 失败: {e}", exc_info=True)
            return api_response(error=f'服务错误: {str(e)}', status=500)
    return decorated_function

def check_auth():
    """检查用户是否已登录"""
    if not session.get('logged_in'):
        return False
    return True

# ==================== 缓存管理API ====================

@enhanced_viz_bp.route('/api/ml/cache/clear', methods=['POST'])
@handle_errors
def ml_cache_clear_api():
    """清空分析缓存API - 用于强制刷新分析结果"""
    if not check_auth():
        return api_response(error='未登录', status=401)

    # 清空综合报告缓存
    cache_service.delete('ml_comprehensive_report_7')
    cache_service.delete('ml_comprehensive_report_30')
    cache_service.delete('ml_comprehensive_report_90')

    logger.info("[缓存] 已清空分析缓存")
    return api_response(data={'message': '缓存已清空，请刷新页面查看最新分析'})


# ==================== 诊断API ====================

@enhanced_viz_bp.route('/api/diagnostic')
@handle_errors
def diagnostic_api():
    """诊断API - 测试增强版功能是否正常工作"""
    logger.info("访问诊断API")

    cached = cache_service.get('ml_diagnostic_api')
    if cached:
        return jsonify(cached)

    diagnostic_info = {
        'status': 'ok',
        'service_available': True,
        'ml_service_available': ml_service is not None,
        'session_logged_in': session.get('logged_in', False),
        'username': session.get('username', 'unknown'),
        'timestamp': datetime.now().isoformat()
    }

    if ml_service:
        diagnostic_info['ml_models'] = {
            'sentiment_lstm': ml_service.sentiment_lstm_model is not None,
            'vectorizer': ml_service.vectorizer is not None,
            'tokenizer': ml_service.tokenizer is not None
        }

    cache_service.set('ml_diagnostic_api', diagnostic_info, CACHE_TTL['diagnostic'])
    return api_response(data=diagnostic_info)

# ==================== 机器学习分析API ====================

@enhanced_viz_bp.route('/ml_dashboard')
def ml_dashboard():
    """机器学习增强的舆情分析仪表盘"""
    logger.info("访问AI仪表盘页面")
    if not check_auth():
        logger.warning("用户未登录，重定向到登录页")
        return redirect(url_for('auth.login'))

    username = session.get('username', '未知用户')
    logger.info(f"用户 {username} 访问AI仪表盘")
    return render_template('ml_dashboard.html', username=username)


@enhanced_viz_bp.route('/api/ml/data-diagnostic')
@handle_errors
def ml_data_diagnostic_api():
    """数据诊断API - 查看可用的历史数据量"""
    if not check_auth():
        return api_response(error='未登录', status=401)

    cached = cache_service.get('ml_diagnostic_full')
    if cached:
        return jsonify(cached)

    if not ml_service:
        return api_response(error='ML服务未启用', status=503)

    results = {}
    for days in [7, 14, 30, 60, 90, 180]:
        data = ml_service._get_historical_sentiment_data(days)
        results[f'{days}天'] = len(data)

    recent_data = ml_service._get_historical_sentiment_data(14)
    recent_details = []
    for item in recent_data[-7:]:
        recent_details.append({
            'date': item['date'].strftime('%Y-%m-%d') if hasattr(item['date'], 'strftime') else str(item['date']),
            'avg_sentiment': float(item['avg_sentiment']),
            'comment_count': int(item['comment_count'])
        })

    response_data = {
        'available_data_points': results,
        'recent_details': recent_details,
        'total_days_available': len(recent_data),
        'recommendation': '需要至少30天数据才能获得可靠的LSTM预测结果' if len(recent_data) < 30 else '数据量充足'
    }

    cache_service.set('ml_diagnostic_full', response_data, CACHE_TTL['data_diagnostic'])
    return api_response(data=response_data)


@enhanced_viz_bp.route('/api/ml/comprehensive-report')
@handle_errors
def ml_comprehensive_report_api():
    """综合分析报告API"""
    if not check_auth():
        return api_response(error='未登录', status=401)

    days = request.args.get('days', 30, type=int)
    force_refresh = request.args.get('force_refresh', 'false').lower() == 'true'
    if days < 1 or days > 365:
        return api_response(error='分析天数必须在1-365之间', status=400)

    if not ml_service:
        return api_response(error='ML服务未启用', status=503)

    cache_key = f'ml_comprehensive_report_{days}'

    # 强制刷新时跳过缓存
    if not force_refresh:
        cached = cache_service.get(cache_key)
        if cached:
            cached['_from_cache'] = True
            return api_response(data=cached)

    report = ml_service.generate_comprehensive_report(days=days)
    if not report or 'error' in report:
        return api_response(error=report.get('error', '报告生成失败'), status=500)

    report['_from_cache'] = False
    cache_service.set(cache_key, report, CACHE_TTL['comprehensive_report'])
    return api_response(data=report)


# ==================== MiniMax大模型增强报告API ====================

@enhanced_viz_bp.route('/api/ml/ai-report')
def ai_enhanced_report():
    """AI大模型增强版分析报告（MiniMax驱动）"""
    if not check_auth():
        return jsonify({'error': '未登录'}), 401

    try:
        from services.system_config_service import SystemConfigService

        days = request.args.get('days', 30, type=int)
        if days < 1 or days > 365:
            return jsonify({'error': '分析天数必须在1-365之间'}), 400

        cache_key = f'ai_report_{days}'
        cached = cache_service.get(cache_key)
        if cached:
            return jsonify(cached)

        if not ml_service:
            result = {
                'summary': {
                    'total_events': 0,
                    'total_comments': 0,
                    'avg_sentiment': 0.5,
                    'sentiment_trend': '中性'
                },
                'ai_analysis': {
                    'ai_powered': False,
                    'key_findings': ['ML服务未启用，请检查服务配置'],
                    'risk_level': '低',
                    'risk_reason': 'ML服务初始化失败',
                    'recommendations': [],
                    'summary_text': 'ML服务暂未启用，无法提供智能分析。'
                },
                'recommendations': []
            }
            return jsonify(result)

        report_data = ml_service.generate_comprehensive_report(days=days)
        if not report_data:
            report_data = {
                'summary': {
                    'total_events': 0,
                    'total_comments': 0,
                    'avg_sentiment': 0.5,
                    'sentiment_trend': '中性'
                },
                'recommendations': []
            }

        if 'error' in report_data:
            return jsonify({'error': report_data['error']}), 500

        system_config = SystemConfigService.get_all_config()
        api_key = system_config.get('MINIMAX_API_KEY', '').strip()
        model_name = system_config.get('MINIMAX_MODEL_NAME', 'abab6-chat').strip()
        api_url = system_config.get('MINIMAX_API_URL', 'https://api.minimaxi.com/anthropic').strip()

        if not api_key:
            report_data['ai_analysis'] = {
                'ai_powered': False,
                'key_findings': ['请先在设置中配置 MiniMax API Key'],
                'risk_level': '低',
                'risk_reason': 'AI增强暂未启用',
                'recommendations': report_data.get('recommendations', []),
                'summary_text': '当前舆情态势平稳，请查看详细数据。'
            }
            cache_service.set(cache_key, report_data, CACHE_TTL['ai_report'])
            return jsonify(report_data)

        try:
            ai_result = ml_service.generate_ai_report_with_llm(report_data, api_key, model_name, api_url)
            report_data['ai_analysis'] = ai_result
        except Exception as e:
            logger.error(f"AI报告生成失败: {e}")
            report_data['ai_analysis'] = {
                'ai_powered': False,
                'key_findings': ['暂无AI分析，请检查API配置'],
                'risk_level': '低',
                'risk_reason': str(e),
                'recommendations': report_data.get('recommendations', []),
                'summary_text': 'AI分析服务暂时不可用，请检查API配置后重试。'
            }
        
        cache_service.set(cache_key, report_data, CACHE_TTL['ai_report'])
        return jsonify(report_data)

    except Exception as e:
        logger.error(f"AI增强报告接口失败: {e}", exc_info=True)
        return jsonify({'error': f'AI报告加载失败: {str(e)}'}), 500


# ==================== 异常检测API ====================

@enhanced_viz_bp.route('/api/ml/anomaly-detection')
@handle_errors
def ml_anomaly_detection_api():
    """基于孤立森林的舆情异常检测API"""
    if not check_auth():
        return api_response(error='未登录', status=401)

    days = request.args.get('days', 7, type=int)
    if days < 1 or days > 90:
        return api_response(error='分析天数必须在1-90之间', status=400)

    cache_key = f'ml_anomaly_detection_{days}'
    cached = cache_service.get(cache_key)
    if cached:
        return jsonify(cached)

    if not ml_service:
        return api_response(error='ML服务未启用', status=503)

    anomalies = ml_service.detect_anomalies(days=days)

    # 汇总统计
    total = len(anomalies)
    severity_stats = {
        'high': sum(1 for a in anomalies if a.get('severity') == '高'),
        'medium': sum(1 for a in anomalies if a.get('severity') == '中'),
        'low': sum(1 for a in anomalies if a.get('severity') == '低')
    }
    anomaly_types_count = {}
    for a in anomalies:
        for t in a.get('anomaly_types', []):
            anomaly_types_count[t] = anomaly_types_count.get(t, 0) + 1

    result = {
        'anomalies': anomalies,
        'summary': {
            'total_anomalies': total,
            'severity': severity_stats,
            'anomaly_types': anomaly_types_count,
            'analysis_days': days
        }
    }

    cache_service.set(cache_key, result, CACHE_TTL['anomaly_detection'])
    return api_response(data=result)


# ==================== 影响力评分API ====================

@enhanced_viz_bp.route('/api/ml/influence-scoring')
@handle_errors
def ml_influence_scoring_api():
    """事件影响力评分API"""
    if not check_auth():
        return api_response(error='未登录', status=401)

    limit = request.args.get('limit', 10, type=int)
    days = request.args.get('days', 7, type=int)

    if limit < 1 or limit > 50:
        return api_response(error='数量限制必须在1-50之间', status=400)

    cache_key = f'ml_influence_scoring_{limit}_{days}'
    cached = cache_service.get(cache_key)
    if cached:
        return jsonify(cached)

    if not ml_service:
        return api_response(error='ML服务未启用', status=503)

    influential_events = ml_service.get_influential_events(limit=limit, days=days)

    # 整理返回数据
    events_data = []
    for item in influential_events:
        event = item.get('event')
        influence = item.get('influence', {})
        if not event:
            continue

        event_crawl_time = getattr(event, 'crawl_time', None)
        crawl_time_str = event_crawl_time.isoformat() if event_crawl_time else None

        events_data.append({
            'event_id': event.id,
            'title': getattr(event, 'title', '未知事件'),
            'heat': getattr(event, 'heat', 0),
            'sentiment': getattr(event, 'sentiment_score', 0.5),
            'comment_count': getattr(event, 'comment_count', 0),
            'crawl_time': crawl_time_str,
            'influence_score': influence.get('score', 0),
            'factors': influence.get('factors', {}),
            'factor_breakdown': {
                'heat': {'value': influence.get('factors', {}).get('heat_factor', 0), 'label': '热度', 'weight': 0.25},
                'comment': {'value': influence.get('factors', {}).get('comment_count_factor', 0), 'label': '评论', 'weight': 0.20},
                'engagement': {'value': influence.get('factors', {}).get('engagement_factor', 0), 'label': '互动', 'weight': 0.25},
                'sentiment': {'value': influence.get('factors', {}).get('sentiment_factor', 0), 'label': '情感', 'weight': 0.15},
                'time': {'value': influence.get('factors', {}).get('time_factor', 0), 'label': '时效', 'weight': 0.15}
            }
        })

    avg_score = round(sum(e['influence_score'] for e in events_data) / len(events_data), 2) if events_data else 0

    result = {
        'events': events_data,
        'summary': {
            'total_events': len(events_data),
            'avg_influence_score': avg_score,
            'max_score': events_data[0]['influence_score'] if events_data else 0,
            'top_event': events_data[0]['title'] if events_data else None
        }
    }

    cache_service.set(cache_key, result, CACHE_TTL['influence_scoring'])
    return api_response(data=result)


# ==================== AI 洞察接口 ====================

@enhanced_viz_bp.route('/api/ai-insight', methods=['GET', 'POST'])
def get_ai_insight():
    """
    AI洞察接口 - 调用MiniMax API生成关键发现

    输入参数：
    - positive_count: 正面评论数量
    - negative_count: 负面评论数量
    - neutral_count: 中性评论数量
    - health_score: 健康度分数 (0-100)
    - risk_level: 当前风险等级 (低/中/高)

    返回：
    - insights: 3条关键发现数组
    - ai_powered: 是否由AI生成
    """
    try:
        # 获取请求参数
        if request.method == 'POST':
            data = request.get_json() or {}
            positive_count = data.get('positive_count', 0)
            negative_count = data.get('negative_count', 0)
            neutral_count = data.get('neutral_count', 0)
            health_score = data.get('health_score', 50)
            risk_level = data.get('risk_level', '低')
            event_id = data.get('event_id')
        else:
            positive_count = request.args.get('positive_count', type=int, default=0)
            negative_count = request.args.get('negative_count', type=int, default=0)
            neutral_count = request.args.get('neutral_count', type=int, default=0)
            health_score = request.args.get('health_score', type=int, default=50)
            risk_level = request.args.get('risk_level', default='低')
            event_id = request.args.get('event_id', type=int)

        # 检查缓存
        cache_key = f'ai_insight_{positive_count}_{negative_count}_{neutral_count}_{health_score}_{risk_level}'
        cached = cache_service.get(cache_key)
        if cached:
            return jsonify(cached)

        # 获取系统配置
        system_config = SystemConfigService.get_all_config()
        api_key = system_config.get('MINIMAX_API_KEY', '').strip()
        model_name = system_config.get('MINIMAX_MODEL_NAME', 'abab6-chat').strip()
        api_url = system_config.get('MINIMAX_API_URL', 'https://api.minimaxi.com/anthropic').strip()

        # 如果没有配置API Key，使用规则生成默认洞察
        if not api_key:
            logger.info("MiniMax API Key未配置，使用规则生成默认洞察")
            default_insights = _generate_rule_based_insights(
                positive_count, negative_count, neutral_count, health_score, risk_level
            )
            result = {
                'insights': default_insights,
                'ai_powered': False,
                'source': 'rule_based'
            }
            cache_service.set(cache_key, result, CACHE_TTL['ai_insight'])
            return jsonify(result)

        # 构建prompt
        prompt = f"""你是专业舆情分析师，请根据以下舆情数据生成3条关键发现，每条不超过30字，返回JSON数组格式。

【舆情数据】
- 正面评论数量：{positive_count}条
- 负面评论数量：{negative_count}条
- 中性评论数量：{neutral_count}条
- 健康度分数：{health_score}分（0-100）
- 当前风险等级：{risk_level}

请生成3条简洁专业的关键发现，例如：
["正面情绪占比略高，舆情态势良好", "负面评论需关注，建议持续监控", "中性用户占主导，舆论保持理性"]

只返回JSON数组，不要包含任何其他文字。"""

        import requests
        response = requests.post(
            api_url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            },
            json={
                "model": model_name,
                "messages": [
                    {"role": "system", "content": "你是专业舆情分析师，只输出JSON数组，不输出任何其他内容。"},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 300
            },
            timeout=15
        )

        if response.status_code != 200:
            logger.warning(f"MiniMax API返回错误: {response.status_code}")
            default_insights = _generate_rule_based_insights(
                positive_count, negative_count, neutral_count, health_score, risk_level
            )
            result = {
                'insights': default_insights,
                'ai_powered': False,
                'source': 'api_error_fallback'
            }
            cache_service.set(cache_key, result, CACHE_TTL['ai_insight'])
            return jsonify(result)

        # 解析响应
        resp_data = response.json()
        content = resp_data.get('choices', [{}])[0].get('messages', [{}])[0].get('content', '')

        # 清理并解析JSON
        import json
        try:
            # 尝试提取JSON数组
            content = content.strip()
            if content.startswith('```'):
                content = content.split('```')[1]
                if content.startswith('json'):
                    content = content[4:]
            insights = json.loads(content)
            if not isinstance(insights, list):
                insights = [str(insights)]
            insights = [str(i)[:50] for i in insights][:3]  # 确保最多3条
        except json.JSONDecodeError:
            logger.warning(f"AI返回内容解析失败: {content[:100]}")
            insights = _generate_rule_based_insights(
                positive_count, negative_count, neutral_count, health_score, risk_level
            )

        result = {
            'insights': insights,
            'ai_powered': True,
            'source': 'minimax_api',
            'model': model_name
        }
        cache_service.set(cache_key, result, CACHE_TTL['ai_insight'])
        return jsonify(result)

    except requests.exceptions.Timeout:
        logger.warning("MiniMax API调用超时")
        return jsonify({
            'insights': _generate_rule_based_insights(
                request.get_json().get('positive_count', 0) if request.method == 'POST' else 0,
                request.get_json().get('negative_count', 0) if request.method == 'POST' else 0,
                request.get_json().get('neutral_count', 0) if request.method == 'POST' else 0,
                request.get_json().get('health_score', 50) if request.method == 'POST' else 50,
                request.get_json().get('risk_level', '低') if request.method == 'POST' else '低'
            ),
            'ai_powered': False,
            'source': 'timeout_fallback'
        })
    except Exception as e:
        logger.error(f"AI洞察生成失败: {e}")
        return jsonify({
            'insights': [
                '数据采集中，请稍后刷新页面',
                '舆情分析系统运行正常',
                '持续监控中...'
            ],
            'ai_powered': False,
            'source': 'error_fallback'
        })


def _generate_rule_based_insights(positive, negative, neutral, health_score, risk_level):
    """基于规则生成默认洞察"""
    total = positive + negative + neutral
    if total == 0:
        return [
            '暂无评论数据，请先采集舆情',
            '等待数据更新中...',
            '系统监控运行正常'
        ]

    insights = []

    # 情感分布洞察
    if positive > negative * 1.5:
        insights.append('正面情绪占主导，舆情态势积极向好')
    elif negative > positive * 1.5:
        insights.append('负面情绪占比高，建议关注舆情走向')
    elif negative > 0 and negative / total > 0.4:
        insights.append('负面评论比例较高，需持续监控')
    else:
        insights.append('情感分布均衡，舆论整体理性')

    # 健康度洞察
    if health_score >= 70:
        insights.append('舆情健康度良好，系统运行正常')
    elif health_score >= 40:
        insights.append('舆情存在波动，建议保持关注')
    else:
        insights.append('舆情健康度偏低，需重点关注')

    # 风险等级洞察
    if risk_level in ['高', '高危', '危机']:
        insights.append(f'{risk_level}风险预警，建议启动应对机制')
    elif risk_level in ['中', '中危']:
        insights.append('中等风险等级，持续监控中')
    else:
        insights.append('风险等级较低，舆情态势平稳')

    return insights[:3]
