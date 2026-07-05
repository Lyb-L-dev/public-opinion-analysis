from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
from utils.db_utils import (
    db_connection,
    execute_query,
    create_db_connection
)
import logging

# 配置日志（开启详细输出，便于调试）
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class User:
    """用户模型（最终修复版）"""

    def __init__(self, id=None, username=None, password=None,
                 security_question=None, security_answer=None, created_at=None, role=None,
                 department=None, bio=None, avatar_path=None):
        """
        初始化用户对象
        :param id: 用户ID
        :param username: 用户名
        :param password: 哈希后的密码
        :param security_question: 密保问题
        :param security_answer: 哈希后的密保答案
        :param created_at: 创建时间
        :param role: 用户角色，默认为 'user'
        :param department: 部门/组织
        :param bio: 个人简介
        :param avatar_path: 头像路径
        """
        self.id = id  # 匹配数据库的id字段
        self.username = username
        self.password = password
        self.security_question = security_question
        self.security_answer = security_answer
        self.role = role or 'user'  # 提供默认值
        self.department = department
        self.bio = bio
        self.avatar_path = avatar_path

        # 统一created_at类型为datetime（如果是字符串则转换）
        if created_at and isinstance(created_at, str):
            try:
                self.created_at = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S')
            except ValueError:
                logger.warning(f"日期格式转换失败: {created_at}")
                self.created_at = None
        else:
            self.created_at = created_at or datetime.now()

    def to_dict(self):
        """转换为字典，用于接口返回"""
        return {
            'id': self.id,
            'username': self.username,
            'security_question': self.security_question,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M:%S') if self.created_at else None,
            'role': self.role,
            'department': self.department,
            'bio': self.bio,
            'avatar_path': self.avatar_path
        }

    @classmethod
    def create(cls, username, password, security_question, security_answer):
        """
        创建新用户（最终修复版）
        核心改进：
        1. 禁用连接池确保数据即时可见
        2. 手动执行插入获取自增ID
        3. 通过ID查询用户（避免用户名查询坑）
        4. 详细日志输出便于调试
        """
        # 1. 基础参数校验
        if not all([username, password, security_question, security_answer]):
            logger.error(f"创建用户失败：必填字段为空 | 用户名：{username}")
            return None

        # 2. 密码与答案加密
        try:
            hashed_password = generate_password_hash(password)
            hashed_answer = generate_password_hash(security_answer)
            logger.info(f"用户 {username} 密码/答案加密完成")
        except Exception as e:
            logger.error(f"密码加密失败 | 用户名：{username} | 错误：{e}", exc_info=True)
            return None

        # 3. 数据库操作（禁用连接池，确保数据即时读取）
        conn = None
        try:
            # 手动创建连接（不使用连接池）
            conn = create_db_connection()
            if not conn:
                logger.error(f"创建用户失败：数据库连接失败 | 用户名：{username}")
                return None

            # 3.1 先检查用户名是否已存在
            check_sql = "SELECT id FROM users WHERE username = %s LIMIT 1"
            exists = execute_query(conn, check_sql, (username,), fetch_one=True)
            if exists:
                logger.error(f"创建用户失败：用户名已存在 | 用户名：{username} | 已存在ID：{exists['id']}")
                # 返回已存在的用户
                existing_user = cls.get_by_id(exists['id'])
                return existing_user

            # 3.2 执行插入并获取自增ID（核心修复）
            insert_sql = """
                INSERT INTO users 
                (username, password, security_question, security_answer, created_at, role)
                VALUES (%s, %s, %s, %s, NOW(), DEFAULT)
            """
            with conn.cursor() as cursor:
                # 手动执行INSERT，不依赖execute_update
                cursor.execute(insert_sql, (username, hashed_password, security_question, hashed_answer))
                user_id = cursor.lastrowid  # 获取插入后的自增ID
                affected_rows = cursor.rowcount  # 获取受影响行数

            # 强制提交事务
            conn.commit()
            logger.info(f"用户插入成功 | 用户名：{username} | 自增ID：{user_id} | 受影响行数：{affected_rows}")

            # 3.3 验证插入结果（直接查询）
            verify_sql = "SELECT * FROM users WHERE id = %s"
            verify_data = execute_query(conn, verify_sql, (user_id,), fetch_one=True)
            logger.info(f"插入后验证结果 | ID：{user_id} | 数据：{verify_data}")

            # 3.4 手动构造User对象返回（避免字段映射问题）
            if verify_data:
                new_user = cls(
                    id=verify_data.get('id'),
                    username=verify_data.get('username'),
                    password=verify_data.get('password'),
                    security_question=verify_data.get('security_question'),
                    security_answer=verify_data.get('security_answer'),
                    created_at=verify_data.get('created_at'),
                    role=verify_data.get('role', 'user'),
                    department=verify_data.get('department'),
                    bio=verify_data.get('bio'),
                    avatar_path=verify_data.get('avatar_path')
                )
                logger.info(f"用户创建成功 | 用户名：{username} | ID：{new_user.id}")
                return new_user

            logger.error(f"创建用户失败：插入成功但验证不到数据 | 用户名：{username} | ID：{user_id}")
            return None

        except Exception as e:
            logger.error(f"创建用户异常 | 用户名：{username} | 错误：{e}", exc_info=True)
            # 异常时回滚
            if conn:
                try:
                    conn.rollback()
                    logger.info("事务已回滚")
                except Exception as rollback_err:
                    logger.error(f"事务回滚失败：{rollback_err}")
            return None
        finally:
            # 确保连接关闭
            if conn:
                try:
                    conn.close()
                    logger.debug(f"数据库连接已关闭 | 用户名：{username}")
                except Exception as close_err:
                    logger.error(f"关闭连接失败：{close_err}")

    @classmethod
    def get_by_username(cls, username):
        """
        根据用户名获取用户（修复版）
        核心改进：禁用连接池 + 手动构造对象
        """
        if not username:
            logger.warning("查询用户失败：用户名为空")
            return None

        try:
            # 禁用连接池，确保读取最新数据
            with db_connection(use_pool=False) as conn:
                sql = "SELECT * FROM users WHERE username = %s"
                data = execute_query(conn, sql, (username,), fetch_one=True)
                logger.info(f"按用户名查询结果 | 用户名：{username} | 数据：{data}")

                if data:
                    # 手动构造User对象，避免字段映射问题
                    user = cls(
                        id=data.get('id'),
                        username=data.get('username'),
                        password=data.get('password'),
                        security_question=data.get('security_question'),
                        security_answer=data.get('security_answer'),
                        created_at=data.get('created_at'),
                        role=data.get('role', 'user'),
                        department=data.get('department'),
                        bio=data.get('bio'),
                        avatar_path=data.get('avatar_path')
                    )

                    return user
                logger.info(f"未找到用户：{username}")
                return None
        except Exception as e:
            logger.error(f"查询用户失败 | 用户名：{username} | 错误：{e}", exc_info=True)
            return None

    @classmethod
    def get_by_id(cls, user_id):
        """
        根据ID获取用户（修复版）
        核心改进：禁用连接池 + 手动构造对象
        """
        if not user_id:
            logger.warning("查询用户失败：用户ID为空")
            return None

        try:
            # 禁用连接池，确保读取最新数据
            with db_connection(use_pool=False) as conn:
                sql = "SELECT * FROM users WHERE id = %s"
                data = execute_query(conn, sql, (user_id,), fetch_one=True)
                logger.info(f"按ID查询结果 | ID：{user_id} | 数据：{data}")

                if data:
                    # 手动构造User对象
                    user = cls(
                        id=data.get('id'),
                        username=data.get('username'),
                        password=data.get('password'),
                        security_question=data.get('security_question'),
                        security_answer=data.get('security_answer'),
                        created_at=data.get('created_at'),
                        role=data.get('role', 'user'),
                        department=data.get('department'),
                        bio=data.get('bio'),
                        avatar_path=data.get('avatar_path')
                    )

                    return user
                logger.info(f"未找到ID为{user_id}的用户")
                return None
        except Exception as e:
            logger.error(f"查询用户失败 | ID：{user_id} | 错误：{e}", exc_info=True)
            return None

    def verify_password(self, password):
        """验证密码"""
        if not self.password or not password:
            logger.warning(f"密码验证失败：密码为空 | 用户：{self.username}")
            return False
        try:
            return check_password_hash(self.password, password)
        except Exception as e:
            logger.error(f"密码验证失败 | 用户：{self.username} | 错误：{e}", exc_info=True)
            return False

    def verify_security_answer(self, answer):
        """验证密保答案"""
        if not self.security_answer or not answer:
            logger.warning(f"密保答案验证失败：答案为空 | 用户：{self.username}")
            return False
        try:
            return check_password_hash(self.security_answer, answer)
        except Exception as e:
            logger.error(f"密保答案验证失败 | 用户：{self.username} | 错误：{e}", exc_info=True)
            return False

    def update_password(self, new_password):
        """更新密码（修复版）"""
        if not new_password or not self.id:
            logger.warning(f"更新密码失败：参数为空 | 用户：{self.username} | ID：{self.id}")
            return False

        try:
            hashed_password = generate_password_hash(new_password)
        except Exception as e:
            logger.error(f"新密码加密失败 | 用户：{self.username} | 错误：{e}", exc_info=True)
            return False

        try:
            # 禁用连接池
            with db_connection(use_pool=False) as conn:
                sql = "UPDATE users SET password = %s WHERE id = %s"
                with conn.cursor() as cursor:
                    cursor.execute(sql, (hashed_password, self.id))
                    affected_rows = cursor.rowcount
                conn.commit()

                if affected_rows > 0:
                    self.password = hashed_password
                    logger.info(f"密码更新成功 | 用户：{self.username} | ID：{self.id}")
                    return True
                logger.warning(f"密码更新失败：无匹配记录 | 用户：{self.username} | ID：{self.id}")
                return False
        # 补充缺失的数据库异常处理（关键！）
        except Exception as e:
            logger.error(f"密码更新数据库操作失败 | 用户：{self.username} | 错误：{e}", exc_info=True)
            # 数据库异常时回滚（防止脏数据）
            if 'conn' in locals() and not conn.closed:
                conn.rollback()
            return False

    def save(self):
        """保存用户信息更新"""
        if not self.id:
            logger.warning(f"保存用户失败：用户ID为空 | 用户名：{self.username}")
            return False

        try:
            with db_connection(use_pool=False) as conn:
                sql = """
                    UPDATE users
                    SET department = %s, bio = %s, avatar_path = %s
                    WHERE id = %s
                """
                with conn.cursor() as cursor:
                    cursor.execute(sql, (self.department, self.bio, self.avatar_path, self.id))
                    affected_rows = cursor.rowcount
                conn.commit()

                if affected_rows > 0:
                    logger.info(f"用户信息保存成功 | 用户：{self.username} | ID：{self.id}")
                    return True
                logger.warning(f"用户信息保存失败：无匹配记录 | 用户：{self.username} | ID：{self.id}")
                return False
        except Exception as e:
            logger.error(f"保存用户信息失败 | 用户：{self.username} | 错误：{e}", exc_info=True)
            return False