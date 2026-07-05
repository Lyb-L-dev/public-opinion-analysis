"""
关键词分析API请求模型
使用Pydantic进行参数验证和类型转换
"""

from typing import List, Optional, Union
from pydantic import BaseModel, Field, validator
import re


class BaseRequest(BaseModel):
    """基础请求模型"""
    days: int = Field(default=30, ge=1, le=365, description="查询天数，1-365天")
    event_count: int = Field(default=10, ge=1, le=50, description="事件数量，1-50个")
    page: int = Field(default=1, ge=1, le=1000, description="页码，1-1000")
    page_size: int = Field(default=20, ge=1, le=100, description="每页大小，1-100")

    @validator('days')
    def validate_days(cls, v):
        if v < 1 or v > 365:
            raise ValueError('days必须在1-365之间')
        return v

    @validator('event_count')
    def validate_event_count(cls, v):
        if v < 1 or v > 50:
            raise ValueError('event_count必须在1-50之间')
        return v


class EvolutionRequest(BaseRequest):
    """关键词演化分析请求"""
    keyword_count: int = Field(default=10, ge=1, le=100, description="关键词数量，1-100个")
    event_ids: Optional[List[int]] = Field(default=None, description="事件ID列表")

    @validator('keyword_count')
    def validate_keyword_count(cls, v):
        if v < 1 or v > 100:
            raise ValueError('keyword_count必须在1-100之间')
        return v

    @validator('event_ids')
    def validate_event_ids(cls, v):
        if v is not None and len(v) > 20:
            raise ValueError('最多支持20个事件')
        return v


class TimelineRequest(BaseRequest):
    """时间轴演化分析请求"""
    top_n: int = Field(default=10, ge=1, le=50, description="关键词数量，1-50个")
    event_ids: Optional[List[int]] = Field(default=None, description="事件ID列表")

    @validator('top_n')
    def validate_top_n(cls, v):
        if v < 1 or v > 50:
            raise ValueError('top_n必须在1-50之间')
        return v


class BurstRequest(BaseRequest):
    """爆发词分析请求"""
    keyword_count: int = Field(default=10, ge=1, le=50, description="关键词数量，1-50个")
    event_ids: Optional[List[int]] = Field(default=None, description="事件ID列表")

    @validator('keyword_count')
    def validate_keyword_count(cls, v):
        if v < 1 or v > 50:
            raise ValueError('keyword_count必须在1-50之间')
        return v


class CooccurrenceRequest(BaseRequest):
    """共现分析请求"""
    top_n: int = Field(default=15, ge=1, le=100, description="关键词数量，1-100个")
    event_ids: Optional[List[int]] = Field(default=None, description="事件ID列表")
    min_cooccur: int = Field(default=2, ge=1, le=10, description="最小共现次数，1-10次")

    @validator('top_n')
    def validate_top_n(cls, v):
        if v < 1 or v > 100:
            raise ValueError('top_n必须在1-100之间')
        return v


class DriversRequest(BaseRequest):
    """驱动因素分析请求"""
    keyword: str = Field(..., min_length=1, max_length=100, description="主关键词")
    event_ids: Optional[List[int]] = Field(default=None, description="事件ID列表")

    @validator('keyword')
    def validate_keyword(cls, v):
        # 去除前后空格
        v = v.strip()
        if not v:
            raise ValueError('关键词不能为空')
        if len(v) > 100:
            raise ValueError('关键词长度不能超过100个字符')
        # 检查是否包含特殊字符
        if re.search(r'[<>"\'\`\\]', v):
            raise ValueError('关键词包含非法字符')
        return v


class OpinionsRequest(BaseRequest):
    """典型观点抽样请求"""
    keyword: str = Field(..., min_length=1, max_length=100, description="关键词")
    limit: int = Field(default=10, ge=1, le=100, description="返回数量，1-100个")

    @validator('keyword')
    def validate_keyword(cls, v):
        v = v.strip()
        if not v:
            raise ValueError('关键词不能为空')
        if len(v) > 100:
            raise ValueError('关键词长度不能超过100个字符')
        if re.search(r'[<>"\'\`\\]', v):
            raise ValueError('关键词包含非法字符')
        return v

    @validator('limit')
    def validate_limit(cls, v):
        if v < 1 or v > 100:
            raise ValueError('limit必须在1-100之间')
        return v


class BurstPointsRequest(BaseRequest):
    """爆发点检测请求"""
    keyword: str = Field(..., min_length=1, max_length=100, description="关键词")
    event_ids: Optional[List[int]] = Field(default=None, description="事件ID列表")
    threshold: float = Field(default=2.0, ge=1.0, le=10.0, description="变化率阈值，1.0-10.0")

    @validator('keyword')
    def validate_keyword(cls, v):
        v = v.strip()
        if not v:
            raise ValueError('关键词不能为空')
        if len(v) > 100:
            raise ValueError('关键词长度不能超过100个字符')
        if re.search(r'[<>"\'\`\\]', v):
            raise ValueError('关键词包含非法字符')
        return v

    @validator('threshold')
    def validate_threshold(cls, v):
        if v < 1.0 or v > 10.0:
            raise ValueError('threshold必须在1.0-10.0之间')
        return v


class CompareRequest(BaseModel):
    """多关键词对比请求"""
    keywords: List[str] = Field(..., min_items=2, max_items=10, description="关键词列表，2-10个")
    event_ids: Optional[List[int]] = Field(default=None, description="事件ID列表")
    days: int = Field(default=30, ge=1, le=365, description="查询天数，1-365天")

    @validator('keywords')
    def validate_keywords(cls, v):
        if len(v) < 2:
            raise ValueError('至少需要2个关键词')
        if len(v) > 10:
            raise ValueError('最多支持10个关键词')

        # 验证每个关键词
        validated_keywords = []
        for kw in v:
            kw = kw.strip()
            if not kw:
                raise ValueError('关键词不能为空')
            if len(kw) > 100:
                raise ValueError(f'关键词"{kw}"长度不能超过100个字符')
            if re.search(r'[<>"\'\`\\]', kw):
                raise ValueError(f'关键词"{kw}"包含非法字符')
            validated_keywords.append(kw)

        # 检查重复
        if len(set(validated_keywords)) != len(validated_keywords):
            raise ValueError('关键词不能重复')

        return validated_keywords

    @validator('event_ids')
    def validate_event_ids(cls, v):
        if v is not None and len(v) > 20:
            raise ValueError('最多支持20个事件')
        return v


class HeatRankRequest(BaseModel):
    """热度排行榜请求"""
    limit: int = Field(default=10, ge=1, le=100, description="返回数量，1-100个")
    days: int = Field(default=30, ge=1, le=365, description="查询天数，1-365天")
    category: Optional[str] = Field(default=None, max_length=50, description="事件分类")

    @validator('limit')
    def validate_limit(cls, v):
        if v < 1 or v > 100:
            raise ValueError('limit必须在1-100之间')
        return v


def parse_event_ids(event_ids_str: Optional[str]) -> Optional[List[int]]:
    """解析事件ID字符串（逗号分隔）为列表"""
    if not event_ids_str:
        return None

    try:
        event_ids = [int(eid.strip()) for eid in event_ids_str.split(',') if eid.strip()]
        return event_ids if event_ids else None
    except ValueError:
        raise ValueError('事件ID格式错误，应为逗号分隔的整数')


def create_request_model(request_data: dict, model_class):
    """
    创建请求模型实例

    Args:
        request_data: 请求数据字典
        model_class: 请求模型类

    Returns:
        模型实例
    """
    # 处理event_ids参数（可能来自字符串或列表）
    if 'event_ids' in request_data:
        event_ids = request_data['event_ids']
        if isinstance(event_ids, str):
            request_data['event_ids'] = parse_event_ids(event_ids)

    return model_class(**request_data)