# routes/analysis_routes.py
from flask import Blueprint, jsonify, session, request
from services.analysis_service import analysis_service

analysis_bp = Blueprint('analysis', __name__)


@analysis_bp.route('/api/stats')
def get_stats():
    """获取统计信息API"""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({'error': '未登录'}), 401

    try:
        stats = analysis_service.get_opinion_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@analysis_bp.route('/api/events')
def get_events():
    """获取事件列表API"""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({'error': '未登录'}), 401

    try:
        limit = request.args.get('limit', default=10, type=int)
        search = request.args.get('search', default='', type=str)

        events = Event.get_all(limit=limit, search_query=search)
        for event in events:
            event.calculate_sentiment()

        return jsonify({
            'events': [event.to_dict() for event in events],
            'count': len(events)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@analysis_bp.route('/api/event/<int:event_id>/comments')
def get_event_comments(event_id):
    """获取事件评论API"""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({'error': '未登录'}), 401

    try:
        limit = request.args.get('limit', default=100, type=int)

        comments = Comment.get_by_event_id(event_id, limit=limit)
        for comment in comments:
            comment.analyze_sentiment()

        return jsonify({
            'comments': [comment.to_dict() for comment in comments],
            'count': len(comments)
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@analysis_bp.route('/api/analysis/sentiment')
def analyze_sentiment():
    """情感分析API"""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({'error': '未登录'}), 401

    try:
        text = request.args.get('text', default='', type=str)
        if not text:
            return jsonify({'error': '请输入文本'}), 400

        from utils.text_utils import analyze_sentiment, get_sentiment_type
        score = analyze_sentiment(text)
        sentiment_type = get_sentiment_type(score)

        return jsonify({
            'text': text,
            'score': round(score, 4),
            'sentiment': sentiment_type
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@analysis_bp.route('/api/analysis/keywords')
def analyze_keywords():
    """关键词分析API"""
    if 'logged_in' not in session or not session['logged_in']:
        return jsonify({'error': '未登录'}), 401

    try:
        text = request.args.get('text', default='', type=str)
        if not text:
            return jsonify({'error': '请输入文本'}), 400

        from utils.text_utils import extract_keywords
        keywords = extract_keywords([text], top_k=10)

        return jsonify({
            'text': text,
            'keywords': [{'word': word, 'weight': round(weight, 4)} for word, weight in keywords]
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500