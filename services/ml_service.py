# services/ml_service.py
"""
机器学习/深度学习服务 - 舆情分析增强模块
提供高级分析功能：情感预测、影响力分析、异常检测等
"""

import numpy as np
from datetime import datetime, timedelta
import logging
from typing import List, Dict
import warnings
warnings.filterwarnings('ignore')

# 机器学习相关导入
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

# 项目内部导入
from utils.db_utils import with_db_connection, execute_query
from models.comment import Comment
from models.event import Event
from services.system_config_service import SystemConfigService

logger = logging.getLogger(__name__)

class MLPublicOpinionService:
    """机器学习舆情分析服务"""

    def __init__(self):
        # =====================================================
        # 孤立森林参数配置（基于 train_models/anomaly_research/
        #   anomaly_experiment.py 真实实验数据调参确定）
        # =====================================================
        # n_estimators: 100棵孤立树
        #   实验结论：F1=0.1850，F1/耗时比最优（0.49）
        #   200棵时F1=0.1965但耗时增加90%，性价比不足
        # contamination: 0.05
        #   实验结论：接近实际异常率2.1%时F1达到峰值0.2365
        #   比默认值0.1提升27.9%（0.1850→0.2365）
        # random_state: 42 固定种子保障结果可复现
        self.IF_N_ESTIMATORS = 100
        self.IF_CONTAMINATION = 0.05   # 实验最优值（原0.1）
        self.IF_RANDOM_STATE = 42

    # ==================== 影响力分析 ====================

    def calculate_influence_score(self, event_id: int) -> Dict:
        """[预留功能] 计算事件影响力分数"""
        try:
            # 获取事件信息
            event = Event.get_by_id(event_id)
            if not event:
                return {'score': 0, 'factors': {}}

            # 获取相关评论
            comments = Comment.get_by_event_id(event_id, limit=1000)

            # 计算影响力因子
            factors = {
                'heat_factor': min(event.heat / 1000, 1.0),  # 热度因子
                'comment_count_factor': min(len(comments) / 100, 1.0),  # 评论数量因子
                'engagement_factor': self._calculate_engagement_factor(comments),  # 互动因子
                'sentiment_factor': self._calculate_sentiment_factor(comments),  # 情感因子
                'time_factor': self._calculate_time_factor(event.crawl_time)  # 时效因子
            }

            # 计算综合影响力分数
            weights = {
                'heat_factor': 0.25,
                'comment_count_factor': 0.20,
                'engagement_factor': 0.25,
                'sentiment_factor': 0.15,
                'time_factor': 0.15
            }

            influence_score = sum(factors[factor] * weights[factor] for factor in factors)

            return {
                'score': round(influence_score * 100, 2),
                'factors': {k: round(v, 3) for k, v in factors.items()}
            }

        except Exception as e:
            logger.error(f"影响力分析失败: {e}")
            return {'score': 0, 'factors': {}}

    def _calculate_engagement_factor(self, comments: List[Comment]) -> float:
        """计算互动因子"""
        if not comments:
            return 0.0

        total_likes = sum(comment.like_count or 0 for comment in comments)
        avg_likes = total_likes / len(comments)

        # 标准化到0-1区间
        return min(avg_likes / 10, 1.0)

    def _calculate_sentiment_factor(self, comments: List[Comment]) -> float:
        """计算情感因子（极端情感更具影响力）"""
        if not comments:
            return 0.5

        sentiment_scores = [comment.sentiment_score or 0.5 for comment in comments]

        # 计算情感极化程度（远离0.5的程度）
        polarization = np.mean([abs(score - 0.5) for score in sentiment_scores])

        # 转换为影响力因子
        return 0.5 + polarization

    def _calculate_time_factor(self, publish_time: datetime) -> float:
        """计算时效因子"""
        if not publish_time:
            return 0.0

        now = datetime.now()
        hours_diff = (now - publish_time).total_seconds() / 3600

        # 指数衰减
        decay_rate = 0.1
        time_factor = np.exp(-decay_rate * hours_diff / 24)  # 按天衰减

        return max(time_factor, 0.1)

    def _batch_calculate_influence(self, events: List) -> Dict[int, Dict]:
        """批量计算事件影响力（避免N+1查询）

        Args:
            events: 事件对象列表

        Returns:
            {event_id: influence_score_dict}
        """
        if not events:
            return {}

        try:
            # 收集所有事件ID
            event_ids = [e.id for e in events]

            # 避免空列表导致SQL错误
            if not event_ids:
                return {}

            # 批量查询评论统计数据
            @with_db_connection
            def _batch_fetch_comments(conn):
                placeholders = ','.join(['%s'] * len(event_ids))
                sql = f"""
                    SELECT
                        event_id,
                        COUNT(*) as comment_count,
                        AVG(IFNULL(sentiment_score, 0.5)) as avg_sentiment,
                        SUM(IFNULL(like_count, 0)) as total_likes
                    FROM comments
                    WHERE event_id IN ({placeholders})
                    GROUP BY event_id
                """
                return execute_query(conn, sql, tuple(event_ids))

            comment_stats = _batch_fetch_comments()

            # 构建评论统计映射
            stats_map = {row['event_id']: row for row in comment_stats}

            # 计算每个事件的影响力
            now = datetime.now()
            influence_results = {}

            for event in events:
                stats = stats_map.get(event.id, {})

                heat = float(event.heat or 0)
                comment_count = int(stats.get('comment_count', 0) or 0)
                avg_sentiment = float(stats.get('avg_sentiment', 0.5) or 0.5)
                total_likes = float(stats.get('total_likes', 0) or 0)

                # 计算各因子
                heat_factor = min(heat / 1000, 1.0)
                comment_count_factor = min(comment_count / 100, 1.0)

                # 互动因子
                avg_likes = total_likes / comment_count if comment_count > 0 else 0
                engagement_factor = min(avg_likes / 10, 1.0)

                # 情感因子
                polarization = abs(avg_sentiment - 0.5)
                sentiment_factor = 0.5 + polarization

                # 时效因子
                if event.crawl_time:
                    # 转换为datetime（处理Decimal等特殊类型）
                    try:
                        crawl_time = datetime.fromisoformat(str(event.crawl_time)) if hasattr(event.crawl_time, 'isoformat') else event.crawl_time
                    except:
                        crawl_time = event.crawl_time
                    try:
                        hours_diff = (now - crawl_time).total_seconds() / 3600
                        time_factor = max(np.exp(-0.1 * hours_diff / 24), 0.1)
                    except TypeError:
                        time_factor = 0.5
                else:
                    time_factor = 0.5

                # 综合影响力
                weights = {
                    'heat_factor': 0.25,
                    'comment_count_factor': 0.20,
                    'engagement_factor': 0.25,
                    'sentiment_factor': 0.15,
                    'time_factor': 0.15
                }
                factors = {
                    'heat_factor': heat_factor,
                    'comment_count_factor': comment_count_factor,
                    'engagement_factor': engagement_factor,
                    'sentiment_factor': sentiment_factor,
                    'time_factor': time_factor
                }
                influence_score = sum(factors[f] * weights[f] for f in weights)

                influence_results[event.id] = {
                    'score': round(influence_score * 100, 2),
                    'factors': {k: round(v, 3) for k, v in factors.items()}
                }

            return influence_results

        except Exception as e:
            logger.error(f"批量计算影响力失败: {e}")
            return {}

    def get_influential_events(self, limit=10, days=7):
        """获取最具影响力的事件（优化版，使用批量查询）"""
        try:
            events = Event.get_all(limit=limit*3)  # 获取更多事件进行筛选
            if not events:
                return []

            # 批量计算影响力（避免N+1查询）
            influences = self._batch_calculate_influence(events)

            # 构建结果列表
            event_influences = []
            for event in events:
                influence = influences.get(event.id, {'score': 0, 'factors': {}})
                event_influences.append({
                    'event': event,
                    'influence': influence
                })

            # 按影响力排序
            event_influences.sort(key=lambda x: x['influence']['score'], reverse=True)

            return event_influences[:limit]

        except Exception as e:
            logger.error(f"获取影响力事件失败: {e}")
            return []

    # ==================== 异常检测 ====================

    def detect_anomalies(self, days=7):
        """
        检测舆情异常（增强版）

        新增功能:
        - 异常类型分类
        - 异常原因解释
        - 多维度信号分析
        """
        try:
            # 获取近期数据
            recent_data = self._get_recent_opinion_data(days)
            if len(recent_data) < 10:
                return []

            # 准备特征
            features = self._extract_anomaly_features(recent_data)

            # 使用孤立森林进行异常检测
            # 参数来源：train_models/anomaly_research/anomaly_experiment.py
            #   n_estimators=100：F1/耗时比最优（0.49）
            #   contamination=0.05：接近实际异常率2.1%，F1达到峰值0.2365
            #   比默认值0.1提升F1 27.9%
            iso_forest = IsolationForest(
                contamination=self.IF_CONTAMINATION,  # 实验最优值 0.05（原0.1）
                random_state=self.IF_RANDOM_STATE,
                n_estimators=self.IF_N_ESTIMATORS   # 100棵孤立树
            )

            anomaly_labels = iso_forest.fit_predict(features)
            anomaly_scores = iso_forest.decision_function(features)

            # 收集异常事件（增强版）
            anomalies = []
            for i, (label, score) in enumerate(zip(anomaly_labels, anomaly_scores)):
                if label == -1:  # 异常点
                    item = recent_data[i]

                    # 分析异常原因
                    anomaly_types, reasons = self._analyze_anomaly_reasons(
                        item, features[i], features
                    )

                    # 计算多维度信号
                    signals = self._calculate_anomaly_signals(item, recent_data)

                    anomalies.append({
                        'event': item,
                        'event_id': item['id'],
                        'event_title': item.get('title', '未知'),
                        'anomaly_score': round(float(score), 4),
                        'severity': self._calculate_anomaly_severity(score),
                        'anomaly_types': anomaly_types,
                        'reasons': reasons,
                        'signals': signals
                    })

            # 按异常分数排序
            anomalies.sort(key=lambda x: x['anomaly_score'])

            return anomalies

        except Exception as e:
            logger.error(f"异常检测失败: {e}")
            return []

    def _analyze_anomaly_reasons(self, item, feature_vector, all_features):
        """分析异常原因"""
        anomaly_types = []
        reasons = []

        feature_names = ['heat', 'comment_count', 'sentiment', 'avg_likes', 'hour']
        feature_values = dict(zip(feature_names, feature_vector))

        # 计算各特征的Z-score
        all_features_array = np.array(all_features)
        means = np.mean(all_features_array, axis=0)
        stds = np.std(all_features_array, axis=0)

        for i, name in enumerate(feature_names):
            if stds[i] > 0:
                z_score = (feature_vector[i] - means[i]) / stds[i]
            else:
                z_score = 0

            # 判断是否为异常特征
            if abs(z_score) > 1.5:
                if name == 'sentiment':
                    if feature_vector[i] < means[i]:
                        anomaly_types.append('sentiment_drop')
                        reasons.append(f"情感分异常偏低 (当前: {feature_vector[i]:.2f}, 均值: {means[i]:.2f})")
                    else:
                        anomaly_types.append('sentiment_spike')
                        reasons.append(f"情感分异常偏高 (当前: {feature_vector[i]:.2f}, 均值: {means[i]:.2f})")
                elif name == 'comment_count':
                    anomaly_types.append('comment_spike')
                    reasons.append(f"评论数异常增多 (当前: {int(feature_vector[i])}, 均值: {int(means[i])})")
                elif name == 'heat':
                    anomaly_types.append('heat_spike')
                    reasons.append(f"热度异常升高 (当前: {int(feature_vector[i])}, 均值: {int(means[i])})")
                elif name == 'avg_likes':
                    anomaly_types.append('engagement_spike')
                    reasons.append(f"互动量异常增多 (当前: {int(feature_vector[i])}, 均值: {int(means[i])})")

        return anomaly_types, reasons

    def _calculate_anomaly_signals(self, item, all_data):
        """计算多维度异常信号"""
        signals = {}

        # 情感信号
        sentiment = float(item.get('avg_sentiment', 0.5))
        signals['sentiment'] = {
            'value': sentiment,
            'level': 'low' if sentiment > 0.7 else ('high' if sentiment < 0.3 else 'medium'),
            'is_risk': sentiment < 0.4
        }

        # 热度信号
        heat = float(item.get('heat', 0))
        signals['heat'] = {
            'value': heat,
            'level': 'high' if heat > 50000 else ('medium' if heat > 10000 else 'low'),
            'is_risk': heat > 80000
        }

        # 评论信号
        comment_count = int(item.get('comment_count', 0))
        signals['comment_count'] = {
            'value': comment_count,
            'level': 'high' if comment_count > 1000 else ('medium' if comment_count > 100 else 'low'),
            'is_risk': comment_count > 2000
        }

        # 综合风险评估
        risk_factors = sum([
            signals['sentiment']['is_risk'],
            signals['heat']['is_risk'],
            signals['comment_count']['is_risk']
        ])
        signals['risk_level'] = {
            'count': risk_factors,
            'level': 'critical' if risk_factors >= 3 else ('high' if risk_factors >= 2 else ('medium' if risk_factors >= 1 else 'low'))
        }

        return signals

    def _get_recent_opinion_data(self, days):
        """获取近期舆情数据"""
        @with_db_connection
        def _fetch_data(conn):
            cutoff = datetime.now() - timedelta(days=days)
            sql = """
                SELECT
                    he.id, he.title, he.heat, he.crawl_time,
                    COUNT(c.id) as comment_count,
                    AVG(c.sentiment_score) as avg_sentiment,
                    AVG(c.like_count) as avg_likes
                FROM hot_events he
                LEFT JOIN comments c ON he.id = c.event_id
                WHERE he.crawl_time >= %s
                GROUP BY he.id, he.title, he.heat, he.crawl_time
                ORDER BY he.crawl_time DESC
            """
            results = execute_query(conn, sql, (cutoff,))
            return results

        return _fetch_data()

    def _extract_anomaly_features(self, data):
        """提取异常检测特征"""
        features = []
        for item in data:
            feature_vector = [
                item['heat'] or 0,
                item['comment_count'] or 0,
                item['avg_sentiment'] or 0.5,
                item['avg_likes'] or 0,
                # 时间特征（小时）
                item['crawl_time'].hour if item['crawl_time'] else 0
            ]
            features.append(feature_vector)

        # 标准化
        features = np.array(features)
        scaler = StandardScaler()
        features_scaled = scaler.fit_transform(features)

        return features_scaled

    def _calculate_anomaly_severity(self, anomaly_score):
        """计算异常严重程度"""
        if anomaly_score < -0.5:
            return "高"
        elif anomaly_score < -0.2:
            return "中"
        else:
            return "低"

    # ==================== 趋势预测 ====================

    def predict_sentiment_trend(self, days_ahead=7):
        """预测情感趋势"""
        try:
            # 获取全部历史情感数据
            historical_data = self._get_historical_sentiment_data(9999)
            if len(historical_data) < 2:  # 至少需要2天数据
                return {'error': '历史数据不足'}

            # 准备时间序列数据（转换为float避免Decimal计算错误）
            dates = [item['date'] for item in historical_data]
            sentiment_scores = [float(item['avg_sentiment']) for item in historical_data]
            comment_counts = [int(item['comment_count']) for item in historical_data]

            # 诊断日志
            data_count = len(historical_data)
            logger.info(f"简单预测: 共{data_count}天数据, 平均情感: {sum(sentiment_scores)/len(sentiment_scores):.3f}")

            # 使用更大的窗口来计算趋势（至少14天或全部数据）
            window_size = min(max(14, data_count // 4), len(sentiment_scores))

            if window_size >= 2:
                last_window = sentiment_scores[-window_size:]

                # 计算多种趋势
                # 1. 线性趋势
                trend = np.polyfit(range(window_size), last_window, 1)[0]

                # 2. 短期趋势（最近7天）
                short_window = min(7, len(sentiment_scores))
                short_trend = np.polyfit(range(short_window), sentiment_scores[-short_window:], 1)[0]

                # 3. 使用加权趋势（短期趋势权重更高）
                combined_trend = trend * 0.3 + short_trend * 0.7

                # 计算基准值（使用加权平均，近期权重更高）
                weights = np.exp(np.linspace(0, 1, window_size))
                weights = weights / weights.sum()
                baseline = np.average(last_window, weights=weights)

                # 预测未来值
                predictions = []
                last_value = sentiment_scores[-1]

                for i in range(1, days_ahead + 1):
                    # 趋势逐渐衰减
                    decay = 1 / (1 + 0.1 * i)
                    predicted_value = baseline + combined_trend * i * decay
                    # 限制在0-1范围内
                    predicted_value = max(0.1, min(0.9, predicted_value))
                    predictions.append(round(predicted_value, 3))

                # 判断趋势
                if combined_trend > 0.02:
                    current_trend = '上升'
                elif combined_trend < -0.02:
                    current_trend = '下降'
                else:
                    current_trend = '平稳'

                # 置信度基于数据量和趋势强度
                confidence = min(0.5 + data_count / 200 + abs(combined_trend) * 5, 0.95)

                return {
                    'current_trend': current_trend,
                    'trend_strength': abs(combined_trend),
                    'predictions': predictions,
                    'confidence': confidence,
                    'data_days': data_count
                }

            return {'error': '无法计算趋势'}

        except Exception as e:
            logger.error(f"情感趋势预测失败: {e}")
            return {'error': str(e)}

    def _get_historical_sentiment_data(self, days):
        """获取历史情感数据"""
        @with_db_connection
        def _fetch_data(conn):
            cutoff = datetime.now() - timedelta(days=days)
            # 优先使用publish_time，如果为空则使用crawl_time
            sql = """
                SELECT
                    COALESCE(DATE(publish_time), DATE(crawl_time)) as date,
                    AVG(IFNULL(sentiment_score, 0.5)) as avg_sentiment,
                    COUNT(*) as comment_count
                FROM comments
                WHERE (publish_time >= %s OR crawl_time >= %s)
                  AND sentiment_score IS NOT NULL
                GROUP BY COALESCE(DATE(publish_time), DATE(crawl_time))
                ORDER BY date ASC
            """
            results = execute_query(conn, sql, (cutoff, cutoff))
            return results

        return _fetch_data()

    def _get_sentiment_distribution(self, days=30):
        """获取情感分布统计"""
        try:
            @with_db_connection
            def _fetch_data(conn):
                cutoff = datetime.now() - timedelta(days=days)
                # 统计正面、中性、负面评论数量
                sql = """
                    SELECT
                        SUM(CASE WHEN sentiment_score > 0.6 THEN 1 ELSE 0 END) as positive,
                        SUM(CASE WHEN sentiment_score >= 0.4 AND sentiment_score <= 0.6 THEN 1 ELSE 0 END) as neutral,
                        SUM(CASE WHEN sentiment_score < 0.4 THEN 1 ELSE 0 END) as negative,
                        COUNT(*) as total
                    FROM comments
                    WHERE publish_time >= %s AND sentiment_score IS NOT NULL
                """
                results = execute_query(conn, sql, (cutoff,))
                if results and results[0]:
                    row = results[0]
                    return {
                        'positive': int(row['positive'] or 0),
                        'neutral': int(row['neutral'] or 0),
                        'negative': int(row['negative'] or 0),
                        'total': int(row['total'] or 0)
                    }
                return {'positive': 0, 'neutral': 0, 'negative': 0, 'total': 0}

            return _fetch_data()
        except Exception as e:
            logger.error(f"获取情感分布失败: {e}")
            return {'positive': 0, 'neutral': 0, 'negative': 0, 'total': 0}

    # ==================== 综合分析报告 ====================

    def generate_comprehensive_report(self, days=30):
        """生成综合舆情分析报告"""
        try:
            report = {
                'period': f'最近{days}天',
                'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'summary': {},
                'topic_analysis': {},
                'influence_analysis': {},
                'anomaly_detection': {},
                'trend_prediction': {},
                'recommendations': []
            }

            # 1. 基础统计
            total_events = Event.get_count()
            total_comments = Comment.get_count()
            avg_sentiment = Comment.get_avg_sentiment()

            report['summary'] = {
                'total_events': total_events,
                'total_comments': total_comments,
                'avg_sentiment': round(avg_sentiment, 3),
                'sentiment_trend': '正面' if avg_sentiment > 0.6 else '负面' if avg_sentiment < 0.4 else '中性'
            }

            # 1.1 情感分布统计
            sentiment_dist = self._get_sentiment_distribution(days)
            report['sentiment_dist'] = sentiment_dist

            # 2. 影响力分析
            influential_events = self.get_influential_events(limit=5)
            report['influence_analysis'] = {
                'top_influential_events': [
                    {
                        'title': item['event'].title[:50],
                        'influence_score': item['influence']['score'],
                        'heat': item['event'].heat
                    }
                    for item in influential_events
                ]
            }

            # 4. 异常检测
            anomalies = self.detect_anomalies(days=7)
            high_severity_anomalies = [a for a in anomalies if a['severity'] == '高']
            report['anomaly_detection'] = {
                'total_anomalies': len(anomalies),
                'high_severity_count': len(high_severity_anomalies),
                'recent_anomalies': [
                    {
                        'title': a['event']['title'][:50],
                        'severity': a['severity'],
                        'anomaly_score': a['anomaly_score']
                    }
                    for a in anomalies[:3]
                ]
            }

            # 5. 趋势预测
            trend_prediction = self.predict_sentiment_trend(days_ahead=7)
            report['trend_prediction'] = trend_prediction

            # 6. 生成建议
            recommendations = self._generate_recommendations(report)
            report['recommendations'] = recommendations

            return report

        except Exception as e:
            logger.error(f"生成综合报告失败: {e}")
            return {'error': str(e)}

    def _generate_recommendations(self, report):
        """基于分析结果生成建议 - 生成更通俗易懂的建议"""
        recommendations = []

        # 基于情感分析的建议
        avg_sentiment = report['summary']['avg_sentiment']
        if avg_sentiment < 0.4:
            recommendations.append({
                'type': '⚠️ 舆论偏负面',
                'priority': '高',
                'content': '最近负面评论较多，建议关注并及时回应公众关切'
            })
        elif avg_sentiment > 0.6:
            recommendations.append({
                'type': '✓ 舆论良好',
                'priority': '低',
                'content': '整体舆论趋势向好，保持当前工作节奏'
            })

        # 基于异常检测的建议
        anomaly_info = report.get('anomaly_detection', {})
        high_count = anomaly_info.get('high_severity_count', 0)
        if high_count > 0:
            recommendations.append({
                'type': '🚨 有风险事件',
                'priority': '高',
                'content': f'发现 {high_count} 个需要重点关注的事件，建议尽快处理'
            })

        # 基于趋势预测的建议
        trend_info = report.get('trend_prediction', {})
        current_trend = trend_info.get('current_trend', '平稳')
        if current_trend == '下降':
            recommendations.append({
                'type': '📉 趋势下滑',
                'priority': '中',
                'content': '舆论热度正在下降，建议适当引导讨论方向'
            })
        elif current_trend == '上升':
            recommendations.append({
                'type': '📈 热度上升',
                'priority': '低',
                'content': '话题热度正在上升，建议做好舆情监控'
            })

        # 基于影响力分析的建议
        influence_info = report.get('influence_analysis', {})
        top_events = influence_info.get('top_influential_events', [])
        if top_events:
            top_event = top_events[0]
            score = top_event.get('influence_score', 0)
            title = top_event.get('title', '热点事件')
            if score > 80:
                # 截断过长的标题
                display_title = title[:15] + '..' if len(title) > 15 else title
                recommendations.append({
                    'type': '⭐ 高热度事件',
                    'priority': '中',
                    'content': f'「{display_title}」关注度很高，建议重点关注'
                })

        # 如果没有建议，给一个默认的
        if not recommendations:
            recommendations.append({
                'type': '📊 数据正常',
                'priority': '低',
                'content': '当前未发现异常情况，舆情态势平稳'
            })

        return recommendations

    def generate_ai_report_with_llm(self, report_data: dict, api_key: str, model_name: str = "MiniMax-M2.7",
                                     api_url: str = "https://api.minimaxi.com/anthropic") -> dict:
        """调用MiniMax大模型生成智能分析报告（使用Anthropic兼容API）

        Args:
            report_data: 报告数据
            api_key: MiniMax API Key
            model_name: MiniMax模型名称，默认 MiniMax-M2.7
            api_url: MiniMax API 地址（Anthropic兼容端点）
        """
        import json

        # 构建给大模型的上下文
        summary = report_data.get('summary', {})
        anomaly = report_data.get('anomaly_detection', {})
        trend = report_data.get('trend_prediction', {})
        influence = report_data.get('influence_analysis', {})
        sentiment_dist = report_data.get('sentiment_dist', {})

        prompt = f"""你是一名专业的社会舆情分析师，请根据以下真实数据生成一份简洁专业的舆情分析报告。

【数据概览】
- 分析周期：{report_data.get('period', '最近30天')}
- 事件总数：{summary.get('total_events', 0)} 条
- 评论总数：{summary.get('total_comments', 0)} 条
- 平均情感值：{summary.get('avg_sentiment', 0.5):.3f}（0-1，越高越正面）
- 情感趋势：{summary.get('sentiment_trend', '中性')}

【情感分布】
- 正面：{sentiment_dist.get('positive', 0)} 条
- 中性：{sentiment_dist.get('neutral', 0)} 条
- 负面：{sentiment_dist.get('negative', 0)} 条

【异常检测】
- 检测到异常事件：{anomaly.get('total_anomalies', 0)} 个
- 高危事件：{anomaly.get('high_severity_count', 0)} 个

【热点影响力TOP3】
{json.dumps(influence.get('top_influential_events', [])[:3], ensure_ascii=False, indent=2)}

【舆情趋势预测】
- 当前走势：{trend.get('current_trend', '平稳')}

请你生成以下内容，必须严格按JSON格式返回，不要有任何多余文字：
{{
  "key_findings": [
    "关键发现1（20字以内）",
    "关键发现2（20字以内）",
    "关键发现3（20字以内）"
  ],
  "risk_level": "低/中/高",
  "risk_reason": "风险等级判断理由（50字以内）",
  "recommendations": [
    {{"type": "建议标题", "priority": "高/中/低", "content": "具体建议内容（40字以内）"}},
    {{"type": "建议标题", "priority": "高/中/低", "content": "具体建议内容（40字以内）"}},
    {{"type": "建议标题", "priority": "高/中/低", "content": "具体建议内容（40字以内）"}}
  ],
  "summary_text": "整体舆情态势的一段专业总结（80字以内）"
}}"""

        try:
            import requests

            # 使用 requests 调用 MiniMax Anthropic 兼容 API
            # API 格式: POST https://api.minimaxi.com/anthropic/v1/messages
            request_url = api_url.rstrip('/') + '/v1/messages'

            response = requests.post(
                request_url,
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json"
                },
                json={
                    "model": model_name,
                    "max_tokens": 1000,
                    "messages": [
                        {"role": "user", "content": prompt}
                    ],
                    "thinking": {
                        "type": "disabled"
                    }
                },
                timeout=30
            )

            if response.status_code != 200:
                logger.error(f"MiniMax API HTTP错误 {response.status_code}: {response.text[:300]}")
                raise Exception(f"API返回 {response.status_code}")

            result = response.json()
            logger.info(f"MiniMax API响应内容: {str(result)[:800]}")

            # 提取 content（Anthropic 格式）
            # 可能的格式:
            # 1. {"content": [{"type": "text", "text": "..."}]}
            # 2. {"content": [{"thinking": "..."}]}  (thinking 模型)
            # 3. {"content": [..."text": "..."]}  (直接文本)
            content_list = result.get('content', [])
            raw_text = ''
            if content_list and isinstance(content_list, list):
                for item in content_list:
                    if item.get('type') == 'text':
                        raw_text = item.get('text', '')
                        break
                    elif 'text' in item:
                        raw_text = item.get('text', '')
                        break

            if not raw_text:
                logger.error(f"MiniMax API无法提取text: result={str(result)[:500]}")
                raise Exception("API返回空内容")

            # 清洗可能的markdown代码块
            raw_text = raw_text.strip()
            if raw_text.startswith("```"):
                parts = raw_text.split("```", 2)
                if len(parts) >= 3:
                    raw_text = parts[1]
                    if raw_text.startswith("json"):
                        raw_text = raw_text[4:]
            raw_text = raw_text.strip()

            ai_result = json.loads(raw_text)
            ai_result['ai_powered'] = True  # 标记是大模型生成
            return ai_result

        except json.JSONDecodeError as e:
            logger.error(f"MiniMax返回非JSON格式: {e}")
        except Exception as e:
            logger.error(f"MiniMax调用失败: {e}")
            # fallback：返回友好提示
            return {
                'ai_powered': False,
                'key_findings': ['暂无AI分析，请检查API配置'],
                'risk_level': '低',
                'risk_reason': 'MiniMax API调用失败',
                'recommendations': report_data.get('recommendations', []),
                'summary_text': 'AI分析服务暂时不可用，请检查API配置后重试。'
            }


# 创建全局ML服务实例
try:
    ml_service = MLPublicOpinionService()
    logger.info("ML舆情分析服务初始化成功")
except Exception as e:
    logger.error(f"ML服务初始化失败: {e}")
    ml_service = None