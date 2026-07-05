import os
import logging
from flask import Blueprint, render_template, request, jsonify, send_file, session, redirect, url_for, flash
from datetime import datetime
from services.csv_export_service import csv_export_service
from services.system_config_service import SystemConfigService
from services.auth_service import AuthService
from services.favorite_service import FavoriteService
from config import config

logger = logging.getLogger(__name__)

csv_export_bp = Blueprint('csv_export', __name__, url_prefix='/csv')


def login_required(f):
    """登录检查装饰器"""
    from functools import wraps
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


@csv_export_bp.route('/')
@login_required
def index():
    """CSV导出页面"""
    # 获取已导出的文件列表
    files = csv_export_service.get_export_files()
    username = session.get('username')
    user = AuthService.get_user_by_username(username)
    user_id = session.get('user_id')
    favorites = FavoriteService.get_user_favorites(user_id, limit=100)
    favorites_count = len(favorites) if favorites else 0
    return render_template('csv_export.html',
                          files=files,
                          user=user,
                          favorites_count=favorites_count,
                          role=session.get('role'))


@csv_export_bp.route('/settings', methods=['GET'])
@login_required
def get_settings():
    """获取CSV导出设置"""
    try:
        csv_enabled = config.CSV_EXPORT_ENABLED
        export_dir = config.CSV_EXPORT_DIR

        return jsonify({
            'success': True,
            'data': {
                'csv_export_enabled': csv_enabled,
                'csv_export_dir': export_dir
            }
        })
    except Exception as e:
        logger.error(f"获取CSV设置失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@csv_export_bp.route('/settings', methods=['POST'])
@login_required
def update_settings():
    """更新CSV导出设置"""
    try:
        data = request.get_json()
        csv_enabled = data.get('csv_export_enabled', True)

        # 保存到数据库配置
        SystemConfigService.save_config('CSV_EXPORT_ENABLED', str(csv_enabled).lower())

        # 立即应用到config对象
        config.CSV_EXPORT_ENABLED = csv_enabled

        logger.info(f"CSV导出设置已更新: CSV_EXPORT_ENABLED = {csv_enabled}")

        return jsonify({
            'success': True,
            'message': '设置已保存'
        })
    except Exception as e:
        logger.error(f"更新CSV设置失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@csv_export_bp.route('/export', methods=['POST'])
@login_required
def export_data():
    """
    导出数据到CSV/ZIP

    请求参数 (JSON):
        - start_time: str, 开始时间 'YYYY-MM-DD'
        - end_time: str, 结束时间 'YYYY-MM-DD'
        - data_type: str, 'hot_events' / 'comments' / 'articles' / 'all' (默认 'all')
    """
    try:
        data = request.get_json()
        start_time = data.get('start_time')
        end_time = data.get('end_time')
        data_type = data.get('data_type', 'all')

        if not start_time or not end_time:
            return jsonify({
                'success': False,
                'message': '请指定开始时间和结束时间'
            }), 400

        # 验证日期格式
        try:
            datetime.strptime(start_time, '%Y-%m-%d')
            datetime.strptime(end_time, '%Y-%m-%d')
        except ValueError:
            return jsonify({
                'success': False,
                'message': '日期格式错误，请使用 YYYY-MM-DD 格式'
            }), 400

        # 导出数据
        if data_type == 'all':
            result = csv_export_service.export_all(start_time, end_time)
        elif data_type == 'hot_events':
            result = csv_export_service.export_hot_events(start_time, end_time)
        elif data_type == 'comments':
            result = csv_export_service.export_comments(start_time, end_time)
        elif data_type == 'articles':
            result = csv_export_service.export_articles(start_time, end_time)
        else:
            return jsonify({
                'success': False,
                'message': f'不支持的数据类型: {data_type}'
            }), 400

        if result:
            filename = os.path.basename(result)
            return jsonify({
                'success': True,
                'message': '导出成功',
                'filename': filename,
                'filepath': result
            })
        else:
            return jsonify({
                'success': False,
                'message': '指定时间范围内没有数据'
            }), 404

    except Exception as e:
        logger.error(f"导出数据失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@csv_export_bp.route('/download/<filename>', methods=['GET'])
@login_required
def download_file(filename):
    """下载CSV/ZIP文件"""
    try:
        # 安全检查：只允许下载export_dir下的文件
        safe_path = os.path.join(config.CSV_EXPORT_DIR, filename)

        # 规范化路径，防止目录遍历攻击
        real_export_dir = os.path.realpath(config.CSV_EXPORT_DIR)
        real_file_path = os.path.realpath(safe_path)

        if not real_file_path.startswith(real_export_dir):
            logger.warning(f"非法文件路径访问尝试: {filename}")
            return jsonify({
                'success': False,
                'message': '非法文件路径'
            }), 403

        if not os.path.exists(real_file_path):
            return jsonify({
                'success': False,
                'message': '文件不存在'
            }), 404

        # 根据文件类型设置MIME类型
        if filename.endswith('.zip'):
            mimetype = 'application/zip'
        elif filename.endswith('.csv'):
            mimetype = 'text/csv'
        else:
            mimetype = 'application/octet-stream'

        return send_file(
            real_file_path,
            mimetype=mimetype,
            as_attachment=True,
            download_name=filename
        )

    except Exception as e:
        logger.error(f"下载文件失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@csv_export_bp.route('/files', methods=['GET'])
@login_required
def list_files():
    """获取已导出文件列表"""
    try:
        files = csv_export_service.get_export_files()

        # 格式化文件大小
        for f in files:
            size = f['size']
            if size < 1024:
                f['size_str'] = f"{size} B"
            elif size < 1024 * 1024:
                f['size_str'] = f"{size / 1024:.1f} KB"
            else:
                f['size_str'] = f"{size / (1024 * 1024):.1f} MB"

        return jsonify({
            'success': True,
            'files': files
        })
    except Exception as e:
        logger.error(f"获取文件列表失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500


@csv_export_bp.route('/preview', methods=['POST'])
@login_required
def preview_data():
    """
    预览导出数据（获取数据条数）

    请求参数 (JSON):
        - start_time: str, 开始时间 'YYYY-MM-DD'
        - end_time: str, 结束时间 'YYYY-MM-DD'
    """
    try:
        data = request.get_json()
        start_time = data.get('start_time')
        end_time = data.get('end_time')

        if not start_time or not end_time:
            return jsonify({
                'success': False,
                'message': '请指定开始时间和结束时间'
            }), 400

        from utils.db_utils import create_db_connection
        conn = create_db_connection()
        if not conn:
            return jsonify({
                'success': False,
                'message': '数据库连接失败'
            }), 500

        start_ts = f"{start_time} 00:00:00"
        end_ts = f"{end_time} 23:59:59"

        try:
            with conn.cursor() as cursor:
                # 统计各表数据条数
                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM hot_events WHERE crawl_time BETWEEN %s AND %s",
                    (start_ts, end_ts)
                )
                hot_events_count = cursor.fetchone()['cnt']

                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM comments WHERE crawl_time BETWEEN %s AND %s",
                    (start_ts, end_ts)
                )
                comments_count = cursor.fetchone()['cnt']

                cursor.execute(
                    "SELECT COUNT(*) as cnt FROM articles WHERE crawl_time BETWEEN %s AND %s",
                    (start_ts, end_ts)
                )
                articles_count = cursor.fetchone()['cnt']

            return jsonify({
                'success': True,
                'data': {
                    'hot_events_count': hot_events_count,
                    'comments_count': comments_count,
                    'articles_count': articles_count,
                    'total_count': hot_events_count + comments_count + articles_count
                }
            })
        finally:
            conn.close()

    except Exception as e:
        logger.error(f"预览数据失败: {e}")
        return jsonify({
            'success': False,
            'message': str(e)
        }), 500
