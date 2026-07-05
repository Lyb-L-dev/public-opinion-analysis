from flask import Blueprint, jsonify, session
from services.favorite_service import FavoriteService

favorite_bp = Blueprint('favorite', __name__, url_prefix='/favorite')

@favorite_bp.route('/toggle/<int:event_id>', methods=['POST'])
def toggle_favorite(event_id):
    """切换收藏状态"""
    if not session.get('logged_in'):
        return jsonify({'success': False, 'message': '请先登录'}), 401

    user_id = session.get('user_id')
    success, new_state = FavoriteService.toggle_favorite(user_id, event_id)
    if success:
        return jsonify({
            'success': True,
            'favorited': new_state,
            'message': '收藏成功' if new_state else '取消收藏成功'
        })
    else:
        return jsonify({'success': False, 'message': '操作失败'}), 500