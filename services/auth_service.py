# services/auth_service.py
from models.user import User
from config import config
import logging

logger = logging.getLogger(__name__)

class AuthService:
    """认证服务"""

    @staticmethod
    def authenticate(username, password):
        """用户认证"""
        try:
            user = User.get_by_username(username)
            if user and user.verify_password(password):
                return user
            return None
        except Exception as e:
            logger.error(f"认证失败: {e}")
            return None

    @staticmethod
    def register(username, password, security_question, security_answer):
        """用户注册"""
        try:
            # 检查用户名是否已存在
            if User.get_by_username(username):
                return False, "用户名已存在"

            # 创建用户
            user = User.create(username, password, security_question, security_answer)
            if user:
                return True, "注册成功"
            else:
                return False, "注册失败"
        except Exception as e:
            logger.error(f"注册失败: {e}")
            return False, f"注册失败: {str(e)}"

    @staticmethod
    def reset_password(username, security_answer, new_password):
        """重置密码"""
        try:
            user = User.get_by_username(username)
            if not user:
                return False, "用户不存在"

            if not user.verify_security_answer(security_answer):
                return False, "密保答案不正确"

            if user.update_password(new_password):
                return True, "密码重置成功"
            else:
                return False, "密码更新失败"
        except Exception as e:
            logger.error(f"重置密码失败: {e}")
            return False, f"重置失败: {str(e)}"

    @staticmethod
    def get_security_question(username):
        """获取密保问题"""
        try:
            user = User.get_by_username(username)
            if user:
                return user.security_question
            return None
        except Exception as e:
            logger.error(f"获取密保问题失败: {e}")
            return None

    @staticmethod
    def get_security_questions():
        """获取所有密保问题"""
        return config.SECURITY_QUESTIONS

    @staticmethod
    def update_user_profile(username, department=None, bio=None, avatar_path=None):
        """更新用户个人信息"""
        try:
            user = User.get_by_username(username)
            if not user:
                return False

            # 更新用户信息
            if department is not None:
                user.department = department
            if bio is not None:
                user.bio = bio
            if avatar_path is not None:
                user.avatar_path = avatar_path

            return user.save()
        except Exception as e:
            logger.error(f"更新用户信息失败: {e}")
            return False

    @staticmethod
    def verify_password(username, password):
        """验证密码"""
        try:
            user = User.get_by_username(username)
            if user and user.verify_password(password):
                return True
            return False
        except Exception as e:
            logger.error(f"密码验证失败: {e}")
            return False

    @staticmethod
    def update_password(username, new_password):
        """更新密码"""
        try:
            user = User.get_by_username(username)
            if user and user.update_password(new_password):
                return True
            return False
        except Exception as e:
            logger.error(f"密码更新失败: {e}")
            return False

    @staticmethod
    def get_user_by_username(username):
        """根据用户名获取用户"""
        try:
            return User.get_by_username(username)
        except Exception as e:
            logger.error(f"获取用户失败: {e}")
            return None

    @staticmethod
    def verify_security_answer(username, answer):
        """验证密保答案"""
        try:
            user = User.get_by_username(username)
            if user and user.verify_security_answer(answer):
                return True
            return False
        except Exception as e:
            logger.error(f"密保答案验证失败: {e}")
            return False

    @staticmethod
    def update_security(username, security_question, security_answer):
        """更新密保问题和答案"""
        try:
            user = User.get_by_username(username)
            if not user:
                return False

            # 更新密保问题和答案
            user.security_question = security_question
            user.security_answer = security_answer

            return user.save()
        except Exception as e:
            logger.error(f"更新密保信息失败: {e}")
            return False