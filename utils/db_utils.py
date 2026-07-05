import pymysql
from pymysql import Error
import logging
from contextlib import contextmanager
from functools import wraps
from queue import Queue
from threading import Lock
from config import config

# 初始化日志器（补充更详细的日志格式）
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

# 连接池配置（可根据服务器性能调整）
DB_POOL_SIZE = 25  # 最大连接数（从10扩展至25，提升高并发性能）
DB_POOL_TIMEOUT = 30  # 连接超时时间（秒）
db_connection_pool = Queue(maxsize=DB_POOL_SIZE)
pool_lock = Lock()


def get_db_config():
    """获取数据库配置（补充端口类型转换，避免类型错误）"""
    try:
        return {
            'host': config.DB_HOST or 'localhost',
            'port': int(config.DB_PORT) if config.DB_PORT else 3306,
            'database': config.DB_NAME or '',
            'user': config.DB_USER or 'root',
            'password': config.DB_PASSWORD or '',
            'charset': 'utf8mb4',
            'cursorclass': pymysql.cursors.DictCursor,
            'connect_timeout': DB_POOL_TIMEOUT,
            'autocommit': False  # 关闭自动提交，统一手动管理事务
        }
    except (ValueError, AttributeError) as e:
        logger.error(f"获取数据库配置失败：{e}，使用默认配置")
        return {
            'host': 'localhost',
            'port': 3306,
            'database': '',
            'user': 'root',
            'password': '',
            'charset': 'utf8mb4',
            'cursorclass': pymysql.cursors.DictCursor,
            'connect_timeout': DB_POOL_TIMEOUT,
            'autocommit': False
        }


def _create_single_connection():
    """创建单个数据库连接（内部方法，供连接池/直接调用）"""
    try:
        connection = pymysql.connect(**get_db_config())
        logger.debug("数据库连接创建成功")
        return connection
    except Error as e:
        logger.error(f"数据库连接创建失败：{e}")
        return None


def init_db_pool():
    """初始化数据库连接池（提升高并发下的性能，减少连接创建开销）"""
    with pool_lock:
        # 清空现有连接池（避免重复初始化）
        while not db_connection_pool.empty():
            try:
                conn = db_connection_pool.get()
                if conn:
                    conn.close()
            except Exception as e:
                logger.warning(f"清空连接池连接失败：{e}")

        # 填充连接池
        for _ in range(DB_POOL_SIZE):
            conn = _create_single_connection()
            if conn:
                db_connection_pool.put(conn)
            else:
                logger.warning("连接池初始化：部分连接创建失败，跳过该连接")
        logger.info(f"数据库连接池初始化完成，当前可用连接数：{db_connection_pool.qsize()}")


def get_connection_from_pool():
    """从连接池获取连接（无可用连接时阻塞等待，超时则创建临时连接）"""
    try:
        # 尝试从连接池获取连接（非阻塞，无可用则返回None）
        conn = db_connection_pool.get(block=True, timeout=DB_POOL_TIMEOUT)
        # 校验连接是否有效，无效则重新创建
        if conn and conn.open:
            try:
                # 执行简单查询校验连接
                with conn.cursor() as cursor:
                    cursor.execute("SELECT 1")
                return conn
            except Exception as e:
                logger.warning(f"连接池连接失效，重新创建：{e}")
                conn.close()
        # 连接无效/已关闭，重新创建
        return _create_single_connection()
    except Exception as e:
        logger.warning(f"从连接池获取连接失败，创建临时连接：{e}")
        return _create_single_connection()


def release_connection_to_pool(conn):
    """将连接释放回连接池（超出池大小则关闭连接）"""
    if not conn:
        return
    try:
        with pool_lock:
            if db_connection_pool.qsize() < DB_POOL_SIZE and conn.open:
                db_connection_pool.put(conn)
                logger.debug("连接已释放回连接池")
            else:
                conn.close()
                logger.debug("连接池已满，关闭多余连接")
    except Exception as e:
        logger.warning(f"释放连接到连接池失败：{e}，直接关闭连接")
        try:
            conn.close()
        except Exception as e2:
            logger.error(f"关闭连接失败：{e2}")


@contextmanager
def db_connection(use_pool=True):
    """
    数据库连接上下文管理器（增强版：支持连接池/直接连接）
    :param use_pool: 是否使用连接池（默认True，高并发推荐开启）
    """
    connection = None
    try:
        # 获取连接（优先连接池）
        if use_pool:
            connection = get_connection_from_pool()
        else:
            connection = _create_single_connection()

        if not connection:
            raise Exception("无法获取有效的数据库连接")

        yield connection
    except Error as e:
        logger.error(f"数据库操作异常：{e}")
        # 出错时回滚事务
        if connection and connection.open:
            try:
                connection.rollback()
                logger.debug("数据库事务已回滚")
            except Exception as e2:
                logger.error(f"事务回滚失败：{e2}")
        raise
    finally:
        # 释放/关闭连接
        if connection:
            if use_pool:
                release_connection_to_pool(connection)
            else:
                try:
                    connection.close()
                    logger.debug("数据库连接已关闭（非连接池模式）")
                except Exception as e:
                    logger.error(f"关闭数据库连接失败：{e}")


def with_db_connection(use_pool=True):
    """
    数据库连接装饰器（增强版：支持连接池配置，兼容原有无参调用）
    :param use_pool: 是否使用连接池
    """
    # 兼容原有无参调用方式（@with_db_connection）
    if callable(use_pool):
        func = use_pool
        use_pool = True

        @wraps(func)
        def wrapper(*args, **kwargs):
            with db_connection(use_pool=use_pool) as conn:
                return func(conn, *args, **kwargs)

        return wrapper
    # 带参调用方式（@with_db_connection(use_pool=False)）
    else:
        def decorator(func):
            @wraps(func)
            def wrapper(*args, **kwargs):
                with db_connection(use_pool=use_pool) as conn:
                    return func(conn, *args, **kwargs)

            return wrapper

        return decorator


# 修复原有函数重定义问题，拆分职责
def execute_query(conn, sql, params=None, fetch_one=False, fetch_all=True):
    """
    执行SQL查询（仅处理SELECT，不修改数据，不提交事务）
    保留原有调用逻辑，修复日志缺失问题
    """
    if not conn or not conn.open:
        logger.error("执行查询失败：数据库连接无效")
        return None if fetch_one else []

    try:
        with conn.cursor() as cursor:
            logger.debug(f"执行查询SQL：{sql}，参数：{params or ()}")
            cursor.execute(sql, params or ())
            if fetch_one:
                result = cursor.fetchone()
                logger.debug(f"查询结果（单条）：{result}")
                return result
            elif fetch_all:
                result = cursor.fetchall()
                logger.debug(f"查询结果（多条）：共 {len(result)} 条记录")
                return result
            else:
                # 查询语句一般不返回lastrowid，此处保留原有逻辑兼容
                return cursor.lastrowid
    except Error as e:
        logger.error(f"执行查询SQL失败：{e}，SQL：{sql}，参数：{params or ()}")
        return None if fetch_one else []


def execute_update(conn, sql, params=None):
    """
    执行单条增/删/改 SQL 语句（返回受影响行数，自动提交事务）
    保留原有调用逻辑，强化事务处理
    """
    if not conn or not conn.open:
        logger.error("执行更新失败：数据库连接无效")
        return 0

    try:
        with conn.cursor() as cursor:
            logger.debug(f"执行更新SQL：{sql}，参数：{params or ()}")
            cursor.execute(sql, params or ())
        conn.commit()
        logger.debug(f"事务提交成功，受影响行数：{cursor.rowcount}")
        return cursor.rowcount
    except Error as e:
        if conn:
            try:
                conn.rollback()
                logger.debug("更新操作失败，事务已回滚")
            except Error as e2:
                logger.error(f"更新操作失败且事务回滚失败：{e2}")
        logger.error(f"执行更新SQL失败：{e}，SQL：{sql}，参数：{params or ()}")
        return 0


def execute_many_update(conn, sql, params_list=None):
    """
    批量执行增/删/改 SQL 语句（修复原有函数重定义问题，明确职责）
    替换原有被覆盖的execute_many，保留批量更新功能
    """
    if not conn or not conn.open:
        logger.error("批量执行更新失败：数据库连接无效")
        return 0

    if not isinstance(params_list, list) or len(params_list) == 0:
        logger.warning("批量执行更新失败：参数列表为空或非列表类型")
        return 0

    try:
        with conn.cursor() as cursor:
            logger.debug(f"执行批量更新SQL：{sql}，参数列表长度：{len(params_list)}")
            cursor.executemany(sql, params_list or ())
        conn.commit()
        logger.debug(f"批量事务提交成功，受影响行数：{cursor.rowcount}")
        return cursor.rowcount
    except Error as e:
        if conn:
            try:
                conn.rollback()
                logger.debug("批量更新操作失败，事务已回滚")
            except Error as e2:
                logger.error(f"批量更新操作失败且事务回滚失败：{e2}")
        logger.error(f"批量执行SQL失败：{e}，SQL：{sql}，参数列表长度：{len(params_list)}")
        return 0


def execute_many_query(conn, sql, params_list=None):
    """批量执行查询语句（补充缺失的批量查询能力，与批量更新区分）"""
    if not conn or not conn.open:
        logger.error("批量执行查询失败：数据库连接无效")
        return []

    if not isinstance(params_list, list) or len(params_list) == 0:
        logger.warning("批量执行查询失败：参数列表为空或非列表类型")
        return []

    results = []
    try:
        with conn.cursor() as cursor:
            logger.debug(f"执行批量查询SQL：{sql}，参数列表长度：{len(params_list)}")
            for params in params_list:
                cursor.execute(sql, params or ())
                results.extend(cursor.fetchall())
        logger.debug(f"批量查询完成，共返回 {len(results)} 条记录")
        return results
    except Error as e:
        logger.error(f"批量执行查询SQL失败：{e}，SQL：{sql}，参数列表长度：{len(params_list)}")
        return []


def create_db_connection():
    """创建数据库连接（保留原有方法，兼容旧代码，内部调用_single_connection）"""
    conn = _create_single_connection()
    if conn:
        logger.debug("create_db_connection：数据库连接创建成功")
    return conn


def test_db_connection():
    """测试数据库连接是否有效（运维/调试辅助方法）"""
    try:
        with db_connection(use_pool=False) as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT VERSION() as mysql_version")
                result = cursor.fetchone()
                logger.info(f"数据库连接测试成功，MySQL版本：{result.get('mysql_version', '未知')}")
                return True
    except Exception as e:
        logger.error(f"数据库连接测试失败：{e}")
        return False


# 兼容原有代码：保留旧的execute_many别名（避免修改上层调用）
execute_many = execute_many_update