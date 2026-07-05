from flask import Blueprint, render_template, request, redirect, url_for, session, flash
from services.auth_service import AuthService
from services.system_config_service import SystemConfigService
from services.favorite_service import FavoriteService

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')

# 确保配置表存在（会在应用初始化时调用，此处可保留以备单独调试）
SystemConfigService.ensure_table()

# -------------------------- 个人中心 --------------------------
@auth_bp.route('/profile', methods=['GET', 'POST'], endpoint='profile')
def profile():
    """个人中心页面"""
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))

    username = session.get('username')
    user_id = session.get('user_id')
    user = AuthService.get_user_by_username(username)  # 需在 auth_service 中实现

    favorites = FavoriteService.get_user_favorites(user_id, limit=20)
    if request.method == 'POST':
        old_password = request.form.get('old_password', '').strip()
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        security_question = request.form.get('security_question', '').strip()
        security_answer = request.form.get('security_answer', '').strip()
        old_security_answer = request.form.get('old_security_answer', '').strip()

        # 修改密码
        if new_password or confirm_password or old_password:
            if not old_password:
                flash('修改密码需要输入当前密码', 'danger')
                return redirect(url_for('auth.profile'))
            if new_password != confirm_password:
                flash('两次输入的新密码不一致', 'danger')
                return redirect(url_for('auth.profile'))
            if len(new_password) < 6:
                flash('新密码长度至少为6位', 'danger')
                return redirect(url_for('auth.profile'))
            if not AuthService.verify_password(username, old_password):
                flash('当前密码错误', 'danger')
                return redirect(url_for('auth.profile'))
            if not AuthService.update_password(username, new_password):
                flash('密码更新失败', 'danger')
                return redirect(url_for('auth.profile'))
            flash('密码修改成功', 'success')

        # 修改安全问题
        if security_question or security_answer or old_security_answer:
            if not old_security_answer:
                flash('修改安全问题需要输入当前安全答案', 'danger')
                return redirect(url_for('auth.profile'))
            if not AuthService.verify_security_answer(username, old_security_answer):
                flash('当前安全答案错误', 'danger')
                return redirect(url_for('auth.profile'))
            if not AuthService.update_security(username, security_question, security_answer):
                flash('安全问题更新失败', 'danger')
                return redirect(url_for('auth.profile'))
            flash('安全问题修改成功', 'success')

        return redirect(url_for('auth.profile'))

    security_questions = AuthService.get_security_questions()
    user_security_question = user.security_question if user else ''
    user_security_answer = user.security_answer if user else ''  # 建议不显示明文答案，但保留占位
    created_at = user.created_at.strftime('%Y-%m-%d') if user and user.created_at else '未知'

    return render_template(
        'profile.html',
        security_questions=security_questions,
        user_security_question=user_security_question,
        user_security_answer=user_security_answer,
        favorites=favorites,
        created_at=created_at,
        role=user.role if user else 'user'
    )


# -------------------------- 系统设置 --------------------------
@auth_bp.route('/settings', methods=['GET', 'POST'], endpoint='settings')
def settings():
    """系统设置页面（仅管理员可访问）"""
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))

    # 权限检查：非管理员跳转并提示
    if session.get('role') != 'admin':
        flash('您没有权限访问系统设置', 'danger')
        return redirect(url_for('dashboard.dashboard'))

    if request.method == 'POST':
        config_data = {
            'KAFKA_BROKER_LIST': request.form.get('kafka_broker', '').strip(),
            'KAFKA_CONSUMER_GROUP': request.form.get('kafka_group', '').strip(),
            'SPARK_MASTER': request.form.get('spark_master', '').strip(),
            'SPARK_APP_NAME': request.form.get('spark_app_name', '').strip(),
            'SPARK_CHECKPOINT_ROOT': request.form.get('spark_checkpoint', '').strip(),
            'EDGE_DRIVER_PATH': request.form.get('edge_driver', '').strip(),
            'EDGE_USER_DATA_DIR': request.form.get('edge_user_data', '').strip(),
            'WEIBO_HOT_URL': request.form.get('weibo_hot_url', '').strip(),
            'CRAWL_MAX_SCROLLS': request.form.get('crawl_max_scrolls', '').strip(),
            'CRAWL_NUM_HOT_SEARCHES': request.form.get('crawl_num_hot_searches', '').strip(),
            'CSV_EXPORT_ENABLED': 'true' if request.form.get('csv_export_enabled') else 'false',
            'MINIMAX_API_URL': request.form.get('minimax_api_url', 'https://api.minimaxi.com/anthropic').strip(),
            'MINIMAX_API_KEY': request.form.get('minimax_api_key', '').strip(),
            'MINIMAX_MODEL_NAME': request.form.get('minimax_model_name', 'MiniMax-M2.7').strip(),
        }
        if SystemConfigService.save_multiple(config_data):
            flash('配置保存成功，部分配置可能需要重启服务生效', 'success')
            # 立即应用到当前 config 对象
            SystemConfigService.apply_to_config()
        else:
            flash('配置保存失败', 'danger')
        return redirect(url_for('auth.settings'))

    # GET 请求：从 config 对象读取当前值（已被数据库配置覆盖）
    from config import config
    config_dict = {
        'KAFKA_BROKER_LIST': config.KAFKA_BROKER_LIST,
        'KAFKA_CONSUMER_GROUP': config.KAFKA_CONSUMER_GROUP,
        'SPARK_MASTER': config.SPARK_MASTER,
        'SPARK_APP_NAME': config.SPARK_APP_NAME,
        'SPARK_CHECKPOINT_ROOT': config.SPARK_CHECKPOINT_ROOT,
        'EDGE_DRIVER_PATH': config.EDGE_DRIVER_PATH,
        'EDGE_USER_DATA_DIR': config.EDGE_USER_DATA_DIR,
        'WEIBO_HOT_URL': config.WEIBO_HOT_URL,
        'CRAWL_MAX_SCROLLS': config.CRAWL_MAX_SCROLLS,
        'CRAWL_NUM_HOT_SEARCHES': config.CRAWL_NUM_HOT_SEARCHES,
    }
    # 获取系统配置（包括 LSTM 训练开关和 MiniMax API Key）
    system_config = SystemConfigService.get_all_config()
    # MiniMax API Key 单独从 system_config 读取（避免循环引用）
    minimax_api_key = system_config.get('MINIMAX_API_KEY', '')
    minimax_model_name = system_config.get('MINIMAX_MODEL_NAME', 'abab6-chat')
    username = session.get('username')
    user = AuthService.get_user_by_username(username)
    user_id = session.get('user_id')
    favorites = FavoriteService.get_user_favorites(user_id, limit=100)
    favorites_count = len(favorites) if favorites else 0
    return render_template('settings.html',
                           config=config_dict,
                           system_config=system_config,
                           minimax_api_key=minimax_api_key,
                           minimax_model_name=minimax_model_name,
                           user=user,
                           favorites_count=favorites_count)


# -------------------------- 原有登录、注册等路由保持不变 --------------------------
# 注意：原有的 @auth_bp.route('/')、login、register、forgot_password_step1、forgot_password_step2、logout 均保留，无需修改
@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    """登录页面"""
    # 已登录用户直接跳转到仪表盘
    if 'logged_in' in session and session['logged_in']:
        return redirect(url_for('dashboard.dashboard'))

    # 获取首页统计数据
    try:
        from utils.db_utils import execute_query, with_db_connection
        import datetime

        @with_db_connection
        def get_today_comments(conn):
            """获取今日评论数量"""
            today = datetime.date.today()
            sql = "SELECT COUNT(*) as count FROM comments WHERE DATE(publish_time) = %s"
            result = execute_query(conn, sql, (today,), fetch_one=True)
            return result['count'] if result and result['count'] else 0

        @with_db_connection
        def get_high_risk_hotsearch_count(conn):
            """获取今日高风险热搜数量（热度高且有负面情绪）"""
            today = datetime.date.today()
            sql = """
                SELECT COUNT(DISTINCT he.id) as count
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
                WHERE DATE(he.crawl_time) = %s
                  AND he.heat >= 5000
                  AND (c.sentiment_score < 0.4 OR c.sentiment_score IS NULL)
            """
            result = execute_query(conn, sql, (today,), fetch_one=True)
            return result['count'] if result and result['count'] else 0

        @with_db_connection
        def get_burst_keywords_count(conn):
            """获取爆发词数量（简化版：基于热度波动）"""
            from utils.text_utils import extract_keywords

            # 获取近期事件标题
            sql = """
                SELECT title FROM hot_events
                WHERE crawl_time >= DATE_SUB(NOW(), INTERVAL 7 DAY)
                LIMIT 100
            """
            rows = execute_query(conn, sql) or []
            titles = [r['title'] for r in rows if r.get('title')]

            if not titles:
                return 0

            # 提取关键词
            keywords = extract_keywords(titles, top_k=20, with_stopwords_filter=True, with_weight=True)

            # 返回提取的关键词数量
            return len(keywords)

        home_stats = {
            'today_comments': get_today_comments(),
            'risk_count': get_high_risk_hotsearch_count(),
            'burst_count': get_burst_keywords_count()
        }
    except Exception as e:
        print(f"获取首页统计失败: {e}")
        home_stats = {'today_comments': 0, 'risk_count': 0, 'burst_count': 0}

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()

        if not username or not password:
            return render_template('index.html', error='用户名和密码不能为空', home_stats=home_stats)

        user = AuthService.authenticate(username, password)
        if user:
            session['logged_in'] = True
            session['username'] = username
            session['user_id'] = user.id
            session['role'] = user.role
            session.permanent = True  # 会话持久化（默认31天过期）

            # 登录成功后预热页面缓存（后台执行，不阻塞跳转）
            try:
                from routes.dashboard_routes import warmup_all_caches
                warmup_all_caches()
            except Exception as e:
                print(f"缓存预热启动失败: {e}")

            return redirect(url_for('dashboard.dashboard'))
        else:
            return render_template('index.html', error='用户名或密码错误', home_stats=home_stats)

    return render_template('index.html', home_stats=home_stats)

@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """注册页面"""
    # 已登录用户直接跳转到仪表盘
    if 'logged_in' in session and session['logged_in']:
        return redirect(url_for('dashboard.dashboard'))

    security_questions = AuthService.get_security_questions()
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        security_question = request.form.get('security_question', '').strip()
        security_answer = request.form.get('security_answer', '').strip()

        # 表单校验
        if not all([username, password, confirm_password, security_question, security_answer]):
            return render_template('register.html', error='所有字段都必须填写', questions=security_questions)
        if password != confirm_password:
            return render_template('register.html', error='两次输入的密码不一致', questions=security_questions)
        if len(password) < 6:
            return render_template('register.html', error='密码长度至少为6位', questions=security_questions)
        if security_question not in security_questions:
            return render_template('register.html', error='请选择有效的密保问题', questions=security_questions)

        # 调用服务层完成注册
        success, message = AuthService.register(username, password, security_question, security_answer)
        if success:
            return render_template('register.html', success=True, questions=security_questions, message=message)
        else:
            return render_template('register.html', error=message, questions=security_questions)

    return render_template('register.html', questions=security_questions)

@auth_bp.route('/forgot_password_step1', methods=['GET', 'POST'])
def forgot_password_step1():
    """忘记密码第一步 - 验证用户名"""
    # 已登录用户直接跳转到仪表盘
    if 'logged_in' in session and session['logged_in']:
        return redirect(url_for('dashboard.dashboard'))

    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        if not username:
            return render_template('forgot_password_step1.html', error='请输入用户名')

        # 获取用户密保问题（验证用户是否存在）
        security_question = AuthService.get_security_question(username)
        if not security_question:
            return render_template('forgot_password_step1.html', error='用户不存在')

        # 存储需要重置密码的用户名到会话
        session['reset_username'] = username
        return render_template('forgot_password_step2.html', username=username, security_question=security_question)

    return render_template('forgot_password_step1.html')

@auth_bp.route('/forgot_password_step2', methods=['POST'])
def forgot_password_step2():
    """忘记密码第二步 - 验证密保并重置密码"""
    # 未经过第一步验证，直接跳转到第一步
    if 'reset_username' not in session:
        return redirect(url_for('auth.forgot_password_step1'))

    username = session['reset_username']
    security_answer = request.form.get('security_answer', '').strip()
    new_password = request.form.get('password', '').strip()
    confirm_password = request.form.get('confirm_password', '').strip()
    security_question = request.form.get('security_question')

    # 表单校验
    if not all([security_answer, new_password, confirm_password]):
        return render_template('forgot_password_step2.html', username=username, security_question=security_question, error='所有字段都必须填写')
    if new_password != confirm_password:
        return render_template('forgot_password_step2.html', username=username, security_question=security_question, error='两次输入的密码不一致')
    if len(new_password) < 6:
        return render_template('forgot_password_step2.html', username=username, security_question=security_question, error='密码长度至少为6位')

    # 调用服务层重置密码
    success, message = AuthService.reset_password(username, security_answer, new_password)
    if success:
        # 清除会话中的重置用户名
        session.pop('reset_username', None)
        return render_template('reset_password_success.html', message=message)
    else:
        return render_template('forgot_password_step2.html', username=username, security_question=security_question, error=message)

# 个人中心首页 → 重定向到个人信息页
@auth_bp.route('/profile')
def profile_redirect():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    return redirect(url_for('auth.profile_info'))

# 修改个人信息页
@auth_bp.route('/profile/info', methods=['GET', 'POST'], endpoint='profile_info')
def profile_info():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    username = session.get('username')
    user = AuthService.get_user_by_username(username)
    user_id = session.get('user_id')
    favorites = FavoriteService.get_user_favorites(user_id, limit=100)
    favorites_count = len(favorites) if favorites else 0
    if request.method == 'POST':
        # 处理头像上传、部门、个人简介等信息更新
        department = request.form.get('department', '').strip()
        bio = request.form.get('bio', '').strip()

        # 处理头像上传
        avatar_path = None
        if 'avatar' in request.files:
            avatar_file = request.files['avatar']
            if avatar_file and avatar_file.filename:
                # 验证文件类型
                allowed_extensions = {'png', 'jpg', 'jpeg', 'gif'}
                if '.' in avatar_file.filename and avatar_file.filename.rsplit('.', 1)[1].lower() in allowed_extensions:
                    # 生成安全的文件名
                    import os
                    import uuid
                    from werkzeug.utils import secure_filename

                    filename = secure_filename(avatar_file.filename)
                    # 添加唯一标识符防止文件名冲突
                    unique_filename = f"{uuid.uuid4().hex}_{filename}"

                    # 确保上传目录存在
                    upload_dir = os.path.join('static', 'avatars')
                    os.makedirs(upload_dir, exist_ok=True)

                    # 保存文件
                    file_path = os.path.join(upload_dir, unique_filename)
                    avatar_file.save(file_path)

                    # 保存相对路径到数据库
                    avatar_path = f"avatars/{unique_filename}"
                else:
                    flash('不支持的文件类型，请上传 PNG、JPG 或 GIF 格式的图片', 'danger')
                    return redirect(url_for('auth.profile_info'))

        # 调用服务层更新用户信息
        if AuthService.update_user_profile(username, department=department, bio=bio, avatar_path=avatar_path):
            # 如果更新了头像，更新会话中的头像信息
            if avatar_path:
                session['user_avatar'] = avatar_path
            flash('个人信息更新成功', 'success')
        else:
            flash('更新失败，请稍后重试', 'danger')
        return redirect(url_for('auth.profile_info'))

    return render_template('profile_info.html',
                           user=user,
                           favorites_count=favorites_count,
                           role=session.get('role'),
                           created_at=user.created_at.strftime('%Y-%m-%d') if user.created_at else '未知')

# 修改密码页
@auth_bp.route('/profile/password', methods=['GET', 'POST'], endpoint='profile_password')
def profile_password():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    username = session.get('username')
    user = AuthService.get_user_by_username(username)

    if request.method == 'POST':
        old_pwd = request.form.get('old_password')
        new_pwd = request.form.get('new_password')
        confirm = request.form.get('confirm_password')

        # 验证逻辑
        if not old_pwd or not new_pwd or not confirm:
            flash('请填写所有必填项', 'danger')
            return redirect(url_for('auth.profile_password'))

        if new_pwd != confirm:
            flash('两次密码输入不一致', 'danger')
            return redirect(url_for('auth.profile_password'))

        if len(new_pwd) < 8:
            flash('密码长度至少为8位', 'danger')
            return redirect(url_for('auth.profile_password'))

        # 验证原密码并更新
        if not AuthService.verify_password(username, old_pwd):
            flash('原密码错误', 'danger')
            return redirect(url_for('auth.profile_password'))

        if AuthService.update_password(username, new_pwd):
            flash('密码修改成功', 'success')
        else:
            flash('密码修改失败', 'danger')

        return redirect(url_for('auth.profile_password'))

    return render_template('profile_password.html',
                           user=user,
                           role=session.get('role'))

# 收藏历史页
@auth_bp.route('/profile/favorites', endpoint='profile_favorites')
def profile_favorites():
    if not session.get('logged_in'):
        return redirect(url_for('auth.login'))
    username = session.get('username')
    user = AuthService.get_user_by_username(username)
    user_id = session.get('user_id')
    favorites = FavoriteService.get_user_favorites(user_id, limit=50)  # 获取更多收藏
    favorites_count = len(favorites) if favorites else 0
    return render_template('profile_favorites.html',
                           user=user,
                           favorites=favorites,
                           favorites_count=favorites_count,
                           role=session.get('role'))

@auth_bp.route('/logout')
def logout():
    """退出登录 - 清空会话并跳转到登录页"""
    session.clear()
    return redirect(url_for('auth.login'))