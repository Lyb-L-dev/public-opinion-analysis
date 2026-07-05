"""
统一异常处理中间件
为关键词分析API提供标准的异常处理机制
"""

import functools
import logging
import traceback
from flask import jsonify, request
import pymysql

logger = logging.getLogger(__name__)


class ErrorCode:
    """标准错误码定义"""
    # 参数错误类 (400xx)
    PARAM_REQUIRED = 40001  # 参数缺失
    PARAM_INVALID = 40002   # 参数无效
    PARAM_TYPE_ERROR = 40003  # 参数类型错误
    PARAM_RANGE_ERROR = 40004  # 参数范围错误

    # 认证授权类 (401xx)
    AUTH_REQUIRED = 40101   # 需要登录
    AUTH_FAILED = 40102     # 认证失败
    PERMISSION_DENIED = 40301  # 权限不足

    # 资源类 (404xx)
    RESOURCE_NOT_FOUND = 40401  # 资源不存在
    KEYWORD_NOT_FOUND = 40402   # 关键词不存在
    EVENT_NOT_FOUND = 40403     # 事件不存在

    # 业务逻辑类 (409xx)
    CONFLICT = 40901           # 资源冲突
    ALREADY_EXISTS = 40902     # 已存在

    # 系统错误类 (500xx)
    DB_ERROR = 50001           # 数据库错误
    CACHE_ERROR = 50002        # 缓存错误
    ALGORITHM_ERROR = 50003    # 算法错误
    EXTERNAL_SERVICE_ERROR = 50004  # 外部服务错误
    UNKNOWN_ERROR = 50099      # 未知错误


def error_response(code, message, details=None):
    """
    统一错误响应格式

    Args:
        code: 错误码
        message: 错误消息
        details: 错误详情（可选）

    Returns:
        Flask Response对象
    """
    error_data = {
        'success': False,
        'error': {
            'code': code,
            'message': message
        }
    }

    if details:
        error_data['error']['details'] = details

    # 根据错误码设置HTTP状态码
    status_code = 500
    if 40000 <= code < 50000:
        status_code = code // 100  # 400, 401, 403, 404, 409等

    return jsonify(error_data), status_code


def handle_api_exceptions(func):
    """
    异常处理装饰器
    捕获并处理API函数中的异常，返回标准化的错误响应

    Args:
        func: 被装饰的API函数

    Returns:
        包装后的函数
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)

        # 参数验证错误
        except ValueError as e:
            logger.warning(f"参数验证失败: {e}", extra={
                'endpoint': request.endpoint,
                'method': request.method,
                'args': dict(request.args),
                'user_id': request.headers.get('X-User-Id')
            })
            return error_response(ErrorCode.PARAM_INVALID, f"参数错误: {str(e)}")

        # 数据库错误
        except pymysql.Error as e:
            logger.error(f"数据库错误: {e}\n{traceback.format_exc()}", extra={
                'endpoint': request.endpoint,
                'method': request.method,
                'args': dict(request.args)
            })
            return error_response(ErrorCode.DB_ERROR, "数据库操作失败")

        # 认证错误
        except PermissionError as e:
            logger.warning(f"权限不足: {e}", extra={
                'endpoint': request.endpoint,
                'method': request.method,
                'user_id': request.headers.get('X-User-Id')
            })
            return error_response(ErrorCode.PERMISSION_DENIED, "权限不足")

        # 资源不存在
        except KeyError as e:
            logger.warning(f"资源不存在: {e}", extra={
                'endpoint': request.endpoint,
                'method': request.method,
                'args': dict(request.args)
            })
            return error_response(ErrorCode.RESOURCE_NOT_FOUND, f"资源不存在: {str(e)}")

        # 算法错误
        except (RuntimeError, ArithmeticError) as e:
            logger.error(f"算法错误: {e}\n{traceback.format_exc()}", extra={
                'endpoint': request.endpoint,
                'method': request.method,
                'args': dict(request.args)
            })
            return error_response(ErrorCode.ALGORITHM_ERROR, "算法处理失败")

        # 其他未预期的异常
        except Exception as e:
            logger.error(f"未处理异常: {e}\n{traceback.format_exc()}", extra={
                'endpoint': request.endpoint,
                'method': request.method,
                'args': dict(request.args),
                'user_id': request.headers.get('X-User-Id')
            })
            return error_response(ErrorCode.UNKNOWN_ERROR, "系统内部错误")

    return wrapper


def validate_params(**validators):
    """
    参数验证装饰器

    Args:
        **validators: 参数验证规则，格式为 {参数名: (类型, 是否必填, 验证函数)}

    Example:
        @validate_params(
            days=(int, True, lambda x: 1 <= x <= 365),
            keyword_count=(int, False, lambda x: 1 <= x <= 100)
        )
        def get_keywords_evolution(days, keyword_count=10):
            pass
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            # 获取请求参数
            request_args = request.args if request.method == 'GET' else request.get_json() or {}

            for param_name, (param_type, required, validator) in validators.items():
                # 获取参数值
                param_value = request_args.get(param_name)

                # 检查必填参数
                if required and param_value is None:
                    return error_response(
                        ErrorCode.PARAM_REQUIRED,
                        f"缺少必要参数: {param_name}"
                    )

                # 如果参数存在，进行类型转换和验证
                if param_value is not None:
                    try:
                        # 类型转换
                        if param_type == int:
                            converted_value = int(param_value)
                        elif param_type == float:
                            converted_value = float(param_value)
                        elif param_type == bool:
                            converted_value = param_value.lower() in ('true', '1', 'yes')
                        elif param_type == str:
                            converted_value = str(param_value).strip()
                        else:
                            converted_value = param_value

                        # 验证参数
                        if validator and not validator(converted_value):
                            return error_response(
                                ErrorCode.PARAM_INVALID,
                                f"参数无效: {param_name}",
                                details=f"参数值 {param_value} 不符合要求"
                            )

                        # 更新kwargs中的参数值
                        kwargs[param_name] = converted_value

                    except (ValueError, TypeError) as e:
                        return error_response(
                            ErrorCode.PARAM_TYPE_ERROR,
                            f"参数类型错误: {param_name}",
                            details=f"期望类型 {param_type.__name__}, 实际值 {param_value}"
                        )

            return func(*args, **kwargs)
        return wrapper
    return decorator


def log_api_request(func):
    """
    API请求日志装饰器
    记录API调用的开始和结束
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        logger.info(f"API请求开始: {func.__name__}", extra={
            'endpoint': request.endpoint,
            'method': request.method,
            'path': request.path,
            'args': dict(request.args),
            'user_id': request.headers.get('X-User-Id') or 'anonymous'
        })

        try:
            result = func(*args, **kwargs)

            logger.info(f"API请求完成: {func.__name__}", extra={
                'endpoint': request.endpoint,
                'method': request.method,
                'success': True
            })

            return result

        except Exception as e:
            logger.error(f"API请求失败: {func.__name__}", extra={
                'endpoint': request.endpoint,
                'method': request.method,
                'success': False,
                'error': str(e)
            })
            raise  # 重新抛出，由异常处理装饰器捕获

    return wrapper


# 组合装饰器：日志 + 异常处理
def api_endpoint(func):
    """
    组合装饰器：包含日志记录和异常处理的API端点装饰器
    推荐用于所有API函数
    """
    @functools.wraps(func)
    @log_api_request
    @handle_api_exceptions
    def wrapper(*args, **kwargs):
        return func(*args, **kwargs)
    return wrapper