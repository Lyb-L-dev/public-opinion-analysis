# services/analysis_service.py
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from io import BytesIO
import base64
import os
import logging

# 统一顶部导入，移除函数内重复导入
from models.event import Event
from models.comment import Comment
from utils.text_utils import (
    analyze_sentiment, extract_keywords, categorize_event,
    get_sentiment_type
)
from utils.chart_utils import (
    create_line_chart, create_bar_chart, create_pie_chart,
    create_horizontal_bar_chart, plot_to_base64
)
from services.cache_service import cache_service
from wordcloud import WordCloud

logger = logging.getLogger(__name__)


class AnalysisService:
    """分析服务"""

    def __init__(self):
        self.cache_timeout = 300  # 5分钟

    def get_opinion_stats(self):
        """获取舆情统计信息"""
        cache_key = 'opinion_stats'
        cached = cache_service.get(cache_key)
        if cached:
            return cached

        try:
            stats = {
                'total_events': Event.get_count() if Event.get_count() else 0,
                'total_comments': Comment.get_count() if Comment.get_count() else 0,
                'avg_likes': Comment.get_avg_likes() if Comment.get_avg_likes() else 0.0,
                'avg_sentiment': self._calculate_avg_sentiment()
            }

            cache_service.set(cache_key, stats, timeout=self.cache_timeout)
            return stats
        except Exception as e:
            logger.error(f"获取统计信息失败: {e}", exc_info=True)
            return {}

    def _calculate_avg_sentiment(self, limit=500):
        """计算平均情感分"""
        cache_key = 'avg_sentiment'
        cached = cache_service.get(cache_key)
        if cached:
            return cached

        try:
            comments = Comment.get_all(limit=limit) or []
            sentiment_scores = []

            for comment in comments:
                if comment and comment.content and comment.content.strip():
                    try:
                        score = analyze_sentiment(comment.content)
                        if 0 <= score <= 1:  # 确保情感分在合理区间
                            sentiment_scores.append(score)
                    except Exception as e:
                        logger.warning(f"单条评论情感分析失败: {e}")
                        continue

            avg_sentiment = round(sum(sentiment_scores) / len(sentiment_scores), 2) if sentiment_scores else 0.5
            cache_service.set(cache_key, avg_sentiment, timeout=self.cache_timeout)
            return avg_sentiment
        except Exception as e:
            logger.error(f"计算平均情感分失败: {e}", exc_info=True)
            return 0.5

    def analyze_sentiment_trend(self, limit=2000):
        """分析情感趋势"""
        cache_key = f'sentiment_trend_{limit}'
        cached = cache_service.get(cache_key)
        if cached:
            return cached

        try:
            comments = Comment.get_all(limit=limit) or []
            if not comments:
                return None

            # 转换为DataFrame
            df = pd.DataFrame([c.to_dict() for c in comments if c and hasattr(c, 'to_dict')])
            if df.empty:
                return None

            df['publish_time'] = pd.to_datetime(df['publish_time'], errors='coerce')
            df = df.dropna(subset=['publish_time'])
            if df.empty:
                return None

            df['date'] = df['publish_time'].dt.date

            # 计算情感分（过滤空内容，确保分数合理）
            def safe_analyze_sentiment(x):
                if not x or not x.strip():
                    return 0.5
                try:
                    score = analyze_sentiment(x)
                    return score if 0 <= score <= 1 else 0.5
                except:
                    return 0.5

            df['sentiment'] = df['content'].apply(safe_analyze_sentiment)

            # 按日期分组
            daily_sentiment = df.groupby('date')['sentiment'].mean().reset_index()

            if len(daily_sentiment) < 2:
                return None

            # 创建图表
            plt = create_line_chart(
                x_data=daily_sentiment['date'],
                y_data=daily_sentiment['sentiment'],
                title='每日评论情感趋势',
                x_label='日期',
                y_label='平均情感分',
                color='#3498db'
            )

            img_base64 = plot_to_base64(plt)
            cache_service.set(cache_key, img_base64, timeout=self.cache_timeout)
            return img_base64

        except Exception as e:
            logger.error(f"情感趋势分析失败: {e}", exc_info=True)
            return None

    def analyze_event_categories(self):
        """分析事件分类分布"""
        cache_key = 'event_categories'
        cached = cache_service.get(cache_key)
        if cached:
            return cached

        try:
            events = Event.get_all() or []
            if not events:
                return None

            # 定义分类规则 - 根据事件标题分类
            categories_patterns = {
                '政治': r'(政策|政府|官员|选举|法律|制度|政治|外交)',
                '经济': r'(经济|股市|企业|就业|工资|物价|金融|市场)',
                '社会': r'(社会|民生|教育|医疗|住房|交通|安全|疫情)',
                '文化': r'(文化|娱乐|电影|音乐|艺术|文学|体育|游戏)',
                '科技': r'(科技|互联网|人工智能|手机|电脑|航天|5G|芯片)',
                '体育': r'(体育|足球|篮球|比赛|运动员|奥运会|冠军)',
                '国际': r'(国际|外国|美国|日本|欧洲|英国|俄罗斯|外交)'
            }

            # 分类统计
            category_counts = {}
            for event in events:
                if event and event.title and event.title.strip():
                    try:
                        category = categorize_event(event.title, categories_patterns)
                        category_counts[category] = category_counts.get(category, 0) + 1
                    except Exception as e:
                        logger.warning(f"单条事件分类失败: {e}")
                        continue

            # 创建饼图
            categories = list(category_counts.keys())
            counts = list(category_counts.values())

            if not categories:
                return None

            plt = create_pie_chart(
                labels=categories,
                sizes=counts,
                title='热点事件分类分布'
            )

            img_base64 = plot_to_base64(plt)
            cache_service.set(cache_key, img_base64, timeout=self.cache_timeout)
            return img_base64

        except Exception as e:
            logger.error(f"事件分类分析失败: {e}", exc_info=True)
            return None

    def analyze_sentiment_distribution(self, limit=1000):
        """分析情感分布"""
        cache_key = f'sentiment_distribution_{limit}'
        cached = cache_service.get(cache_key)
        if cached:
            return cached

        try:
            comments = Comment.get_all(limit=limit) or []
            if not comments:
                return None

            # 计算情感分
            sentiment_types = {'正面': 0, '中性': 0, '负面': 0}
            for comment in comments:
                if comment and comment.content and comment.content.strip():
                    try:
                        score = analyze_sentiment(comment.content)
                        if 0 <= score <= 1:
                            sentiment_type = get_sentiment_type(score)
                            sentiment_types[sentiment_type] += 1
                    except Exception as e:
                        logger.warning(f"单条评论情感分类失败: {e}")
                        continue

            # 创建饼图
            labels = list(sentiment_types.keys())
            sizes = list(sentiment_types.values())
            colors = ['#2ecc71', '#f39c12', '#e74c3c']

            plt = create_pie_chart(
                labels=labels,
                sizes=sizes,
                title='评论情感分布',
                colors=colors
            )

            img_base64 = plot_to_base64(plt)
            cache_service.set(cache_key, img_base64, timeout=self.cache_timeout)
            return img_base64

        except Exception as e:
            logger.error(f"情感分布分析失败: {e}", exc_info=True)
            return None

    def analyze_comment_time_distribution(self, limit=2000):
        """分析评论时间分布"""
        cache_key = f'comment_time_distribution_{limit}'
        cached = cache_service.get(cache_key)
        if cached:
            return cached

        try:
            comments = Comment.get_all(limit=limit) or []
            if not comments:
                return None

            # 转换为DataFrame
            df = pd.DataFrame([c.to_dict() for c in comments if c and hasattr(c, 'to_dict')])
            if df.empty:
                return None

            df['publish_time'] = pd.to_datetime(df['publish_time'], errors='coerce')
            df = df.dropna(subset=['publish_time'])
            if df.empty:
                return None

            df['hour'] = df['publish_time'].dt.hour

            # 统计每小时评论数
            hour_counts = df['hour'].value_counts().sort_index()

            # 确保有0-23小时的数据
            for hour in range(24):
                if hour not in hour_counts.index:
                    hour_counts[hour] = 0
            hour_counts = hour_counts.sort_index()

            # 创建柱状图
            plt = create_bar_chart(
                categories=hour_counts.index,
                values=hour_counts.values,
                title='评论时间分布（按小时）',
                x_label='小时',
                y_label='评论数量',
                color='#9b59b6'
            )

            img_base64 = plot_to_base64(plt)
            cache_service.set(cache_key, img_base64, timeout=self.cache_timeout)
            return img_base64

        except Exception as e:
            logger.error(f"评论时间分布分析失败: {e}", exc_info=True)
            return None

    def analyze_hot_words(self, event_limit=50, comment_limit=1000, top_k=20):
        """分析热点关键词"""
        cache_key = f'hot_words_{event_limit}_{comment_limit}_{top_k}'
        cached = cache_service.get(cache_key)
        if cached:
            return cached.get('chart'), cached.get('words'), cached.get('wordcloud')

        try:
            events = Event.get_all(limit=event_limit) or []
            comments = Comment.get_all(limit=comment_limit) or []

            if not events and not comments:
                return None, [], None

            # 合并文本
            texts = []
            for event in events:
                if event and event.title and event.title.strip():
                    texts.append(event.title)

            for comment in comments:
                if comment and comment.content and comment.content.strip():
                    texts.append(comment.content)

            if not texts:
                return None, [], None

            # 提取关键词
            keywords = extract_keywords(texts, top_k=top_k)
            if not keywords:
                return None, [], None

            # 生成权重图
            words, weights = zip(*keywords) if keywords else ([], [])

            plt = create_horizontal_bar_chart(
                labels=words,
                values=weights,
                title='热点关键词权重',
                x_label='权重'
            )

            chart_base64 = plot_to_base64(plt)

            # 生成词云
            wordcloud_base64 = self._generate_wordcloud(keywords)

            result = {
                'chart': chart_base64,
                'words': keywords,
                'wordcloud': wordcloud_base64
            }

            cache_service.set(cache_key, result, timeout=self.cache_timeout)
            return chart_base64, keywords, wordcloud_base64

        except Exception as e:
            logger.error(f"热点关键词分析失败: {e}", exc_info=True)
            return None, [], None

    def _generate_wordcloud(self, keywords):
        """生成词云图"""
        if not keywords:
            return None

        try:
            # 创建词频字典
            word_freq = {word: weight * 1000 for word, weight in keywords}

            # 字体路径（增加容错，避免中文乱码）
            font_paths = [
                'simhei.ttf',
                '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
                '/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc',
                'C:/Windows/Fonts/simhei.ttf',
                'C:/Windows/Fonts/msyh.ttc',
                '/System/Library/Fonts/PingFang.ttc',
                '/System/Library/Fonts/Heiti.ttc'
            ]

            font_path = None
            for path in font_paths:
                if os.path.exists(path):
                    font_path = path
                    break

            if font_path is None:
                logger.warning("未找到中文字体，生成的词云可能出现中文乱码")

            # 生成词云
            wc = WordCloud(
                font_path=font_path,
                background_color='white',
                width=800,
                height=400,
                max_words=50,
                max_font_size=150,
                min_font_size=10,
                random_state=42,
                colormap='viridis'
            )

            wc.generate_from_frequencies(word_freq)

            # 保存为base64
            img = BytesIO()
            wc.to_image().save(img, format='PNG')
            img.seek(0)

            return base64.b64encode(img.getvalue()).decode('utf-8')

        except Exception as e:
            logger.error(f"词云生成失败: {e}", exc_info=True)
            return None

    def clear_cache(self):
        """清除缓存"""
        try:
            cache_service.clear()
            logger.info("缓存清除成功")
        except Exception as e:
            logger.error(f"缓存清除失败: {e}", exc_info=True)


# 创建全局分析服务实例
analysis_service = AnalysisService()