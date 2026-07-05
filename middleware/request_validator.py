"""
请求参数验证装饰器
使用Pydantic模型验证API请求参数

[预留模块] 当前未在系统中使用，但提供了更现代的参数验证方式
如计划引入Pydantic验证，可启用此模块
"""

import functools
from flask import request
from typing import Type, Optional
from pydantic import BaseModel, ValidationError

from middleware.exception_handler import error_response, ErrorCode


def validate_request(model_class: Type[BaseModel], source: str = 'auto'):
    """
    请求参数验证装饰器

    Args:
        model_class: Pydantic模型类
        source: 参数来源，可选值: 'query'(GET参数), 'json'(POST JSON), 'form'(表单), 'auto'(自动判断)

    Returns:
        装饰器函数
    """
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                # 确定参数来源
                if source == 'auto':
                    if request.method == 'GET':
                        param_source = request.args
                    elif request.is_json:
                        param_source = request.get_json() or {}
                    else:
                        param_source = request.form
                elif source == 'query':
                    param_source = request.args
                elif source == 'json':
                    param_source = request.get_json() or {}
                elif source == 'form':
                    param_source = request.form
                else:
                    raise ValueError(f"不支持的参数来源: {source}")

                # 将参数转换为字典
                params = dict(param_source)

                # 处理event_ids参数（特殊处理，可能来自字符串或列表）
                if 'event_ids' in params:
                    event_ids = params['event_ids']
                    if isinstance(event_ids, str):
                        from schemas.keywords import parse_event_ids
                        params['event_ids'] = parse_event_ids(event_ids)

                # 验证参数
                model_instance = model_class(**params)

                # 将验证后的参数添加到kwargs
                for field_name, field_value in model_instance.dict().items():
                    kwargs[field_name] = field_value

                return func(*args, **kwargs)

            except ValidationError as e:
                # 提取验证错误信息
                errors = []
                for error in e.errors():
                    field = '.'.join(str(loc) for loc in error['loc'])
                    msg = error['msg']
                    errors.append(f"{field}: {msg}")

                error_msg = "参数验证失败: " + "; ".join(errors)
                return error_response(ErrorCode.PARAM_INVALID, error_msg)

            except ValueError as e:
                return error_response(ErrorCode.PARAM_INVALID, f"参数错误: {str(e)}")

        return wrapper
    return decorator


def validate_query_params(model_class: Type[BaseModel]):
    """验证GET查询参数"""
    return validate_request(model_class, source='query')


def validate_json_params(model_class: Type[BaseModel]):
    """验证POST JSON参数"""
    return validate_request(model_class, source='json')


def get_validated_params(model_class: Type[BaseModel], source: str = 'auto'):
    """
    获取验证后的参数（不依赖装饰器）

    Args:
        model_class: Pydantic模型类
        source: 参数来源

    Returns:
        验证后的模型实例
    """
    try:
        # 确定参数来源
        if source == 'auto':
            if request.method == 'GET':
                param_source = request.args
            elif request.is_json:
                param_source = request.get_json() or {}
            else:
                param_source = request.form
        elif source == 'query':
            param_source = request.args
        elif source == 'json':
            param_source = request.get_json() or {}
        elif source == 'form':
            param_source = request.form
        else:
            raise ValueError(f"不支持的参数来源: {source}")

        # 将参数转换为字典
        params = dict(param_source)

        # 处理event_ids参数
        if 'event_ids' in params:
            event_ids = params['event_ids']
            if isinstance(event_ids, str):
                from schemas.keywords import parse_event_ids
                params['event_ids'] = parse_event_ids(event_ids)

        # 验证参数并返回模型实例
        return model_class(**params)

    except ValidationError as e:
        raise ValueError(f"参数验证失败: {e}")
    except Exception as e:
        raise ValueError(f"参数处理失败: {e}")