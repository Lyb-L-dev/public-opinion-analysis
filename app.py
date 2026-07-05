import matplotlib
matplotlib.use('Agg')  # 强制使用无 GUI 后端，不加载 tkinter
matplotlib.rcParams['backend'] = 'Agg'  # 额外锁定后端，防止被覆盖
matplotlib.rcParams['interactive'] = False  # 禁用交互式模式，避免触发 GUI
from flask import Flask, render_template, redirect, url_for, jsonify
import logging
import jieba
import traceback
from config import config
from services.cache_service import cache_service
from routes.auth_routes import auth_bp
from routes.dashboard_routes import dashboard_bp
from routes.analysis_routes import analysis_bp
from routes.visualization_routes import visualization_bp
from routes.favorite_routes import favorite_bp
from apscheduler.schedulers.background import BackgroundScheduler
from services.system_config_service import SystemConfigService

import atexit
# 新增：导入数据库连接池相关工具（解决连接池未初始化问题）
from utils.db_utils import create_db_connection, init_db_pool, test_db_connection
from routes.keywords_routes import keywords_bp
from routes.enhanced_visualization_routes import enhanced_viz_bp
from routes.csv_export_routes import csv_export_bp

# 配置日志
logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL),
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
# 创建Flask应用
app = Flask(__name__)
app.config['SECRET_KEY'] = config.SECRET_KEY
app.config['PERMANENT_SESSION_LIFETIME'] = config.PERMANENT_SESSION_LIFETIME

# 注册蓝图
app.register_blueprint(auth_bp, url_prefix='/auth')
app.register_blueprint(dashboard_bp, url_prefix='/dashboard')
app.register_blueprint(analysis_bp, url_prefix='/api')
app.register_blueprint(visualization_bp)
app.register_blueprint(keywords_bp)
app.register_blueprint(favorite_bp)
app.register_blueprint(enhanced_viz_bp)
app.register_blueprint(csv_export_bp)

# 添加路由调试信息
logger.info("已注册的路由:")
for rule in app.url_map.iter_rules():
    logger.info(f"  {rule.rule} -> {rule.endpoint} [{', '.join(rule.methods)}]")
# 根路由重定向
@app.route('/')
def index():
    return redirect(url_for('auth.login'))


# 健康检查
@app.route('/health')
def health_check():
    return jsonify({
        'status': 'healthy',
        'service': 'public_opinion_analysis',
        'version': '1.0.0'
    })


def start_keywords_evolution_preheat_scheduler():
    """每小时预热关键词热度演化 Redis 缓存"""
    scheduler = BackgroundScheduler()

    def _preheat():
        from routes.keywords_routes import compute_keywords_evolution
        from services.redis_service import redis_service
        if not redis_service.is_connected():
            logger.warning("Redis 未连接，跳过预热")
            return
        import json
        for days in [7, 30, 180]:
            key = f'keywords_evolution:{days}:10'
            try:
                existing = redis_service.client.get(key)
                if existing:
                    logger.debug(f"Redis 缓存命中，跳过: {key}")
                    continue
                logger.info(f"预热关键词热度演化缓存: {key}")
                result = compute_keywords_evolution(days, 10)
                if result and result.get('keywords'):
                    redis_service.client.setex(key, 7200, json.dumps(result, ensure_ascii=False))
                    logger.info(f"预热完成: {key}")
                else:
                    logger.debug(f"预热跳过（无数据）: {key}")
            except Exception as e:
                logger.warning(f"预热失败 {key}: {e}")

    scheduler.add_job(
        func=_preheat,
        trigger='interval', hours=1,
        id='preheat_keywords_evolution',
        name='预热关键词热度演化缓存',
        replace_existing=True,
        max_instances=1
    )
    scheduler.start()
    atexit.register(lambda: scheduler.shutdown())
    logger.info("关键词热度演化预热调度器已启动（每小时执行）")


def init_app():
    """初始化应用（新增：连接池初始化，优化数据库测试）"""
    # 初始化jieba
    jieba.initialize()
    logger.info("jieba初始化完成")

    SystemConfigService.ensure_table()
    SystemConfigService.ensure_indexes()
    SystemConfigService.apply_to_config()
    logger.info("数据库配置已加载")
    # 新增1：初始化数据库连接池（优先级最高，解决连接池获取失败问题）
    try:
        init_db_pool()
        logger.info("数据库连接池初始化完成")
    except Exception as e:
        logger.error(f"数据库连接池初始化失败: {e}")
        logger.error(traceback.format_exc())

    # 测试数据库连接
    try:
        conn = create_db_connection()
        if conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
                result = cursor.fetchone()
                logger.info(f"数据库连接成功: {result}")
            conn.close()
        else:
            logger.error("数据库连接失败")

        # 测试查询users表
        conn = create_db_connection()
        if conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT COUNT(*) as count FROM users")
                result = cursor.fetchone()
                logger.info(f"users表记录数: {result['count']}")
            conn.close()
    except Exception as e:
        logger.error(f"数据库连接测试失败: {e}")
        logger.error(traceback.format_exc())

    # 新增2：可选：测试连接池有效性（验证连接池是否能正常工作）
    try:
        test_db_connection()
    except Exception as e:
        logger.error(f"连接池有效性测试失败: {e}")

    # 清除旧缓存
    cache_service.clear()
    logger.info("缓存已清除")

    # 启动关键词热度演化 Redis 预热调度器
    start_keywords_evolution_preheat_scheduler()

    logger.info("应用初始化完成")

if __name__ == '__main__':
    init_app()
    app.run(
        debug=False,  # 修改1：关闭Debug模式（解决重复初始化问题，生产/调试更稳定）
        host='0.0.0.0',
        port=5000,
        threaded=True  # 保留多线程，提升并发处理能力
    )