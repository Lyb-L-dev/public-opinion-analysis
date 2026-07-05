import matplotlib
matplotlib.use('Agg', force=True)
matplotlib.rcParams.update({
    'backend': 'Agg',
    'interactive': False,
    'axes.unicode_minus': False,  # 解决负号显示问题（可选）
    'figure.max_open_warning': 0  # 关闭图表打开过多警告（可选）
})
import matplotlib.pyplot as plt
import numpy as np
import base64
from io import BytesIO
from utils.db_utils import with_db_connection, execute_query
import datetime
import logging
from typing import List, Optional, Dict, Tuple

logger = logging.getLogger(__name__)

# -------------------------- 全局配置优化（提升兼容性和美观度）--------------------------
# 设置中文字体（环境兼容，避免乱码，适配Windows）
FONT_CANDIDATES = [
    'SimHei',  # Windows 黑体
    'Microsoft YaHei',  # Windows 微软雅黑
    'DejaVu Sans'  # 兜底无衬线字体
]
plt.rcParams['font.sans-serif'] = FONT_CANDIDATES
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示为方块的问题
plt.rcParams['figure.facecolor'] = 'white'  # 背景白色，避免透明问题
plt.rcParams['figure.dpi'] = 100  # 默认分辨率
plt.rcParams['savefig.dpi'] = 150  # 保存图表分辨率（提升清晰度）
plt.rcParams['axes.spines.top'] = False  # 隐藏顶部边框
plt.rcParams['axes.spines.right'] = False  # 隐藏右侧边框
plt.rcParams['grid.alpha'] = 0.3  # 网格透明度
plt.rcParams['grid.linestyle'] = '--'  # 网格线型

# -------------------------- 通用常量配置（方便统一调整样式）--------------------------
DEFAULT_CHART_SIZE = (8, 4)  # 默认图表大小
DEFAULT_PIE_SIZE = (6, 6)  # 默认饼图大小
DEFAULT_HBAR_SIZE = (8, 5)  # 默认横向柱状图大小
DEFAULT_COLOR_PALETTE = {
    'primary': '#3B82F6',  # 主色（蓝色）
    'success': '#10B981',  # 成功色（绿色）
    'warning': '#F59E0B',  # 警告色（黄色）
    'danger': '#EF4444',  # 危险色（红色）
    'purple': '#8B5CF6',  # 紫色
    'cyan': '#06B6D4'  # 青色
}
DEFAULT_FONT_SIZES = {
    'title': 12,
    'label': 10,
    'tick': 9,
    'annotation': 8
}


# -------------------------- 通用工具函数（增强容错和复用性）--------------------------
def plot_to_base64(fig) -> Optional[str]:
    """
    将matplotlib图表转换为Base64编码（增强版：强化资源释放，容错处理）
    :param fig: matplotlib图表对象
    :return: Base64编码字符串或None
    """
    if not fig:
        logger.warning("图表转Base64失败：传入的图表对象为空")
        return None

    try:
        buf = BytesIO()
        fig.savefig(
            buf,
            format='png',
            bbox_inches='tight',  # 自动裁剪边缘空白
            dpi=150,  # 提升图片清晰度
            facecolor='white',
            edgecolor='none',
            pad_inches=0.1  # 裁剪边缘空白的间距
        )
        buf.seek(0)
        img_base64 = base64.b64encode(buf.getvalue()).decode('utf-8')
        return img_base64
    except Exception as e:
        logger.error(f"图表转Base64失败: {str(e)}", exc_info=True)
        return None
    finally:
        # 强制释放资源（避免内存泄漏）
        try:
            buf.close()
        except:
            pass
        try:
            plt.close(fig)
        except:
            pass


def validate_chart_data(x_data: List, y_data: List) -> bool:
    """
    校验图表数据有效性（避免空数据、长度不匹配导致图表生成失败）
    :param x_data: X轴数据
    :param y_data: Y轴数据
    :return: 数据是否有效
    """
    if not isinstance(x_data, list) or not isinstance(y_data, list):
        logger.warning("图表数据校验失败：数据类型非列表")
        return False
    if len(x_data) == 0 or len(y_data) == 0:
        logger.warning("图表数据校验失败：数据列表为空")
        return False
    if len(x_data) != len(y_data):
        logger.warning(f"图表数据校验失败：X轴数据长度({len(x_data)})与Y轴数据长度({len(y_data)})不匹配")
        return False
    # 修复笔误：之前返回False，导致所有有效数据都被判定为无效
    return True




# -------------------------- 基础图表生成函数（优化样式，增强容错，添加标注）--------------------------
def create_line_chart(
        x_data: List,
        y_data: List,
        title: str = "折线图",
        x_label: str = "X轴",
        y_label: str = "Y轴",
        color: str = None,
        fill_area: bool = False,  # 新增：是否填充折线下方区域
        annotate_values: bool = False  # 新增：是否标注数值
) -> Optional[str]:
    """
    生成折线图（增强版：优化样式，添加填充和数值标注，强化容错）
    """
    # 数据校验
    if not validate_chart_data(x_data, y_data):
        return None

    # 兼容默认颜色
    color = color or DEFAULT_COLOR_PALETTE['primary']

    try:
        fig, ax = plt.subplots(figsize=DEFAULT_CHART_SIZE)

        # 绘制折线
        line = ax.plot(
            x_data, y_data,
            marker='o',
            color=color,
            linewidth=2,
            alpha=0.8,
            markersize=4  # 优化标记点大小
        )

        # 新增：填充折线下方区域（提升美观度）
        if fill_area:
            ax.fill_between(
                x_data, y_data,
                alpha=0.2,
                color=color
            )

        # 新增：标注数值（提升可读性）
        if annotate_values:
            for x, y in zip(x_data, y_data):
                ax.annotate(
                    f"{y:.2f}",
                    (x, y),
                    textcoords="offset points",
                    xytext=(0, 5),
                    ha='center',
                    fontsize=DEFAULT_FONT_SIZES['annotation']
                )

        # 样式优化（统一配置，更美观）
        ax.set_title(title, fontsize=DEFAULT_FONT_SIZES['title'], fontweight='bold', pad=15)
        ax.set_xlabel(x_label, fontsize=DEFAULT_FONT_SIZES['label'])
        ax.set_ylabel(y_label, fontsize=DEFAULT_FONT_SIZES['label'])
        ax.grid(True, axis='y')  # 仅显示Y轴网格，更整洁
        ax.tick_params(axis='both', labelsize=DEFAULT_FONT_SIZES['tick'])

        # 优化X轴标签（避免重叠，自动旋转）
        if len(x_data) > 8:
            plt.xticks(rotation=45, ha='right')

        fig.tight_layout()  # 自动调整布局，避免标签重叠
        return plot_to_base64(fig)
    except Exception as e:
        logger.error(f"生成折线图失败: {str(e)}", exc_info=True)
        return None


def create_bar_chart(
        x_data: List,
        y_data: List,
        title: str = "柱状图",
        x_label: str = "X轴",
        y_label: str = "Y轴",
        color: str = None,
        annotate_values: bool = True  # 新增：默认标注数值
) -> Optional[str]:
    """
    生成柱状图（增强版：优化样式，默认标注数值，强化容错）
    """
    # 数据校验
    if not validate_chart_data(x_data, y_data):
        return None

    # 兼容默认颜色
    color = color or DEFAULT_COLOR_PALETTE['primary']

    try:
        fig, ax = plt.subplots(figsize=DEFAULT_CHART_SIZE)

        # 绘制柱状图
        bars = ax.bar(
            x_data, y_data,
            color=color,
            alpha=0.8,
            edgecolor='white',
            linewidth=1,
            width=0.6  # 优化柱宽，更美观
        )

        # 新增：标注数值（默认开启，提升可读性）
        if annotate_values:
            for bar in bars:
                height = bar.get_height()
                ax.annotate(
                    f"{height:.1f}",
                    xy=(bar.get_x() + bar.get_width() / 2, height),
                    xytext=(0, 3),
                    textcoords="offset points",
                    ha='center',
                    va='bottom',
                    fontsize=DEFAULT_FONT_SIZES['annotation']
                )

        # 样式优化
        ax.set_title(title, fontsize=DEFAULT_FONT_SIZES['title'], fontweight='bold', pad=15)
        ax.set_xlabel(x_label, fontsize=DEFAULT_FONT_SIZES['label'])
        ax.set_ylabel(y_label, fontsize=DEFAULT_FONT_SIZES['label'])
        ax.grid(True, axis='y')  # 仅显示Y轴网格
        ax.tick_params(axis='both', labelsize=DEFAULT_FONT_SIZES['tick'])

        # 优化X轴标签（避免重叠）
        if len(x_data) > 8:
            plt.xticks(rotation=45, ha='right')

        fig.tight_layout()
        return plot_to_base64(fig)
    except Exception as e:
        logger.error(f"生成柱状图失败: {str(e)}", exc_info=True)
        return None


def create_pie_chart(
        labels: List,
        sizes: List,
        title: str = "饼图",
        colors: List = None,
        explode: List = None  # 新增：是否突出显示某一部分
) -> Optional[str]:
    """
    生成饼图（增强版：优化样式，支持突出显示，强化容错）
    """
    # 数据校验
    if not isinstance(labels, list) or not isinstance(sizes, list):
        logger.warning("饼图数据校验失败：数据类型非列表")
        return None
    if len(labels) == 0 or len(sizes) == 0:
        logger.warning("饼图数据校验失败：数据列表为空")
        return None
    if len(labels) != len(sizes):
        logger.warning(f"饼图数据校验失败：标签长度({len(labels)})与数值长度({len(sizes)})不匹配")
        return None

    # 兼容默认配色和突出显示
    colors = colors or list(DEFAULT_COLOR_PALETTE.values())[:len(labels)]
    explode = explode or [0.05 if size == max(sizes) else 0 for size in sizes]  # 默认突出显示最大部分

    try:
        fig, ax = plt.subplots(figsize=DEFAULT_PIE_SIZE)

        # 绘制饼图
        wedges, texts, autotexts = ax.pie(
            sizes,
            labels=labels,
            colors=colors,
            autopct='%1.1f%%',
            startangle=90,
            textprops={'fontsize': DEFAULT_FONT_SIZES['tick']},
            wedgeprops={'edgecolor': 'white', 'linewidth': 1},
            explode=explode,
            shadow=True,  # 新增：添加阴影，提升立体感
            pctdistance=0.85  # 优化百分比文字位置
        )

        # 优化百分比文字样式
        for autotext in autotexts:
            autotext.set_color('white')
            autotext.set_fontweight('bold')
            autotext.set_fontsize(DEFAULT_FONT_SIZES['annotation'])

        # 样式优化
        ax.set_title(title, fontsize=DEFAULT_FONT_SIZES['title'], fontweight='bold', pad=15)
        ax.axis('equal')  # 保证饼图是正圆形

        fig.tight_layout()
        return plot_to_base64(fig)
    except Exception as e:
        logger.error(f"生成饼图失败: {str(e)}", exc_info=True)
        return None


def create_horizontal_bar_chart(
        x_data: List,
        y_data: List,
        title: str = "横向柱状图",
        x_label: str = "数值",
        y_label: str = "分类",
        color: str = None,
        annotate_values: bool = True  # 新增：默认标注数值
) -> Optional[str]:
    """
    生成横向柱状图（增强版：优化样式，默认标注数值，强化容错）
    """
    # 数据校验
    if not validate_chart_data(x_data, y_data):
        return None

    # 兼容默认颜色
    color = color or DEFAULT_COLOR_PALETTE['purple']

    try:
        fig, ax = plt.subplots(figsize=DEFAULT_HBAR_SIZE)

        # 绘制横向柱状图
        bars = ax.barh(
            y_data, x_data,
            color=color,
            alpha=0.8,
            edgecolor='white',
            linewidth=1,
            height=0.6  # 优化柱高
        )

        # 新增：标注数值（默认开启）
        if annotate_values:
            for bar in bars:
                width = bar.get_width()
                ax.annotate(
                    f"{width:.1f}",
                    xy=(width, bar.get_y() + bar.get_height() / 2),
                    xytext=(3, 0),
                    textcoords="offset points",
                    ha='left',
                    va='center',
                    fontsize=DEFAULT_FONT_SIZES['annotation']
                )

        # 样式优化
        ax.set_title(title, fontsize=DEFAULT_FONT_SIZES['title'], fontweight='bold', pad=15)
        ax.set_xlabel(x_label, fontsize=DEFAULT_FONT_SIZES['label'])
        ax.set_ylabel(y_label, fontsize=DEFAULT_FONT_SIZES['label'])
        ax.grid(True, axis='x')  # 仅显示X轴网格
        ax.tick_params(axis='both', labelsize=DEFAULT_FONT_SIZES['tick'])

        # 反转Y轴（让排名第一显示在顶部）
        ax.invert_yaxis()

        fig.tight_layout()
        return plot_to_base64(fig)
    except Exception as e:
        logger.error(f"生成横向柱状图失败: {str(e)}", exc_info=True)
        return None


# -------------------------- 业务图表生成函数（核心修改：读取所有历史数据，移除24小时限制）--------------------------
def generate_sentiment_spread_chart(
        title: str = "舆情情感&传播趋势（历史全量数据）"
) -> Optional[str]:
    """
    生成情感+传播趋势双轴图（修改版：读取数据库所有历史数据，无时间范围限制）
    """
    try:
        # 1. 获取所有历史数据（按「日期+小时」分组，避免跨天小时重叠，保留数据唯一性）
        data = get_all_historical_data()
        if not data:
            logger.warning("暂无历史情感数据（数据库无有效数据），跳过图表生成")
            return None

        # 2. 提取数据（直接使用真实分组数据，无需补全小时，贴合数据库已有数据）
        time_labels = [item['time_label'] for item in data]
        sentiment_scores = [float(item['avg_sentiment'] or 0.5) for item in data]
        spread_counts = [int(item['spread_count'] or 0) for item in data]

        # 3. 创建双轴图（加宽图表，适配历史数据的时间标签）
        fig, ax1 = plt.subplots(figsize=(12, 5))

        # 3.1 情感分曲线（左轴）
        color1 = DEFAULT_COLOR_PALETTE['primary']
        ax1.set_xlabel('时间（日期+小时）', fontsize=DEFAULT_FONT_SIZES['label'])
        ax1.set_ylabel('平均情感分', color=color1, fontsize=DEFAULT_FONT_SIZES['label'])
        line1 = ax1.plot(
            time_labels, sentiment_scores,
            color=color1, marker='o', linewidth=2, alpha=0.8, markersize=4,
            label='平均情感分'
        )
        ax1.tick_params(axis='y', labelcolor=color1, labelsize=DEFAULT_FONT_SIZES['tick'])
        ax1.set_ylim(0, 1)  # 情感分限制在0-1区间，符合逻辑
        ax1.grid(True, axis='y', alpha=0.2)

        # 3.2 传播量柱状图（右轴）
        color2 = DEFAULT_COLOR_PALETTE['danger']
        ax2 = ax1.twinx()
        ax2.set_ylabel('传播量（评论+点赞）', color=color2, fontsize=DEFAULT_FONT_SIZES['label'])
        bars2 = ax2.bar(
            time_labels, spread_counts,
            color=color2, alpha=0.3, edgecolor='none',
            label='传播量'
        )
        ax2.tick_params(axis='y', labelcolor=color2, labelsize=DEFAULT_FONT_SIZES['tick'])

        # 3.3 样式优化（解决标签重叠，合并图例）
        fig.suptitle(title, fontsize=DEFAULT_FONT_SIZES['title'] + 1, fontweight='bold', y=0.98)
        plt.xticks(rotation=45, ha='right')  # 旋转时间标签，避免重叠
        fig.tight_layout()

        # 3.4 合并双轴图图例，提升可读性
        lines1, labels1 = ax1.get_legend_handles_labels()
        lines2, labels2 = ax2.get_legend_handles_labels()
        ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left', fontsize=DEFAULT_FONT_SIZES['annotation'])

        # 4. 转换为Base64返回
        return plot_to_base64(fig)
    except Exception as e:
        logger.error(f"生成情感&传播趋势图失败: {str(e)}", exc_info=True)
        return None


def generate_sentiment_distribution_chart(
        event_id: Optional[int] = None,
        title: str = "舆情情感分布饼图"
) -> Optional[str]:
    """
    生成情感分布饼图（对接Comment模型，直接展示正面/中性/负面占比，保留原有功能）
    :param event_id: 事件ID（可选，默认统计所有评论）
    :param title: 图表标题
    :return: Base64编码字符串或None
    """
    from models.comment import Comment  # 延迟导入，避免循环依赖

    try:
        # 获取情感统计数据
        sentiment_count = Comment.get_sentiment_count(event_id=event_id, only_valid_content=True)
        if not sentiment_count or sentiment_count['total'] == 0:
            logger.warning("暂无情感统计数据，跳过饼图生成")
            return None

        # 整理数据
        labels = ['正面', '中性', '负面']
        sizes = [
            sentiment_count['positive'],
            sentiment_count['neutral'],
            sentiment_count['negative']
        ]
        colors = [
            DEFAULT_COLOR_PALETTE['success'],
            DEFAULT_COLOR_PALETTE['warning'],
            DEFAULT_COLOR_PALETTE['danger']
        ]

        # 生成饼图
        return create_pie_chart(
            labels=labels,
            sizes=sizes,
            title=title,
            colors=colors,
            explode=[0.05, 0, 0.05]  # 突出显示正面和负面
        )
    except Exception as e:
        logger.error(f"生成情感分布饼图失败: {str(e)}", exc_info=True)
        return None


def generate_event_heat_ranking_chart(
        limit: int = 10,
        title: str = "事件热度排名TOP10"
) -> Optional[str]:
    """
    生成事件热度排行横向柱状图（对接Event模型，展示TOP10热点事件，保留原有功能）
    :param limit: 排名数量，默认10
    :param title: 图表标题
    :return: Base64编码字符串或None
    """
    from models.event import Event  # 延迟导入，避免循环依赖

    try:
        # 获取事件热度排名
        top_events = Event.get_heat_ranking(limit=limit)
        if not top_events:
            logger.warning("暂无事件热度数据，跳过横向柱状图生成")
            return None

        # 整理数据
        event_titles = [event.title[:15] + "..." if len(event.title) > 15 else event.title for event in top_events]
        event_heats = [event.heat or 0 for event in top_events]

        # 生成横向柱状图
        return create_horizontal_bar_chart(
            x_data=event_heats,
            y_data=event_titles,
            title=title,
            x_label="热度值",
            y_label="事件名称",
            color=DEFAULT_COLOR_PALETTE['cyan']
        )
    except Exception as e:
        logger.error(f"生成事件热度排行图失败: {str(e)}", exc_info=True)
        return None


# -------------------------- 新增：获取历史趋势原始数据（供前端交互式图表使用）--------------------------
def get_sentiment_trend_raw_data() -> Optional[Dict]:
    """
    返回情感&传播趋势的原始数据（不生成图片，供前端ECharts等交互式图表渲染）
    """
    try:
        data = get_all_historical_data()
        if not data:
            logger.warning("暂无历史情感数据，无法返回原始趋势数据")
            return None

        # 提取原始数据，格式适配前端交互式图表
        return {
            "time_labels": [item['time_label'] for item in data],
            "sentiment_scores": [float(item['avg_sentiment'] or 0.5) for item in data],
            "spread_counts": [int(item['spread_count'] or 0) for item in data]
        }
    except Exception as e:
        logger.error(f"获取情感趋势原始数据失败: {str(e)}", exc_info=True)
        return None


# -------------------------- 新增：获取所有历史数据（核心修改，移除时间筛选）--------------------------
def get_all_historical_data() -> List[Dict]:
    """
    查询comments表中所有有效历史数据，按「日期+小时」分组，无时间范围限制
    """

    @with_db_connection
    def _get(conn):
        # SQL：移除24小时时间筛选，按「YYYY-MM-DD HH」分组，兼容数据库已有数据
        sql = """
            SELECT 
                DATE_FORMAT(publish_time, '%%Y-%%m-%%d %%H') as time_label,
                AVG(IFNULL(sentiment_score, 0.5)) as avg_sentiment,
                (COUNT(*) + COALESCE(SUM(IFNULL(like_count, 0)), 0)) as spread_count
            FROM comments
            WHERE content IS NOT NULL AND content != ''
            GROUP BY DATE_FORMAT(publish_time, '%%Y-%%m-%%d %%H')
            ORDER BY time_label ASC
        """
        results = execute_query(conn, sql)
        return results or []

    return _get()


# -------------------------- 保留原有函数（方便后续开发实时功能后切换）--------------------------
def get_hourly_data(hours_range: int = 24) -> List[Dict]:
    """
    原有24小时数据查询函数（保留，后续开发实时爬取后可复用）
    """
    end_time = datetime.datetime.now()
    start_time = end_time - datetime.timedelta(hours=hours_range)

    @with_db_connection
    def _get(conn):
        sql = """
            SELECT 
                DATE_FORMAT(publish_time, '%%H') as hour,
                AVG(IFNULL(sentiment_score, 0.5)) as avg_sentiment,
                (COUNT(*) + COALESCE(SUM(IFNULL(like_count, 0)), 0)) as spread_count
            FROM comments
            WHERE publish_time BETWEEN %s AND %s
              AND content IS NOT NULL AND content != ''
            GROUP BY DATE_FORMAT(publish_time, '%%H')
            ORDER BY hour
        """
        results = execute_query(conn, sql, (start_time, end_time))
        return results or []

    return _get()


def _complete_hourly_data(data: List[Dict], hours_range: int = 24) -> Tuple[Dict, Dict]:
    """
    原有补全小时数据函数（保留，后续开发实时功能后可复用）
    """
    end_hour = datetime.datetime.now().hour
    hours = []
    for i in range(hours_range):
        hour = (end_hour - i) % 24
        hours.append(str(hour).zfill(2))

    raw_sentiment = {item['hour'].zfill(2): float(item['avg_sentiment'] or 0.5) for item in data}
    raw_spread = {item['hour'].zfill(2): int(item['spread_count'] or 0) for item in data}

    complete_sentiment = {hour: raw_sentiment.get(hour, 0.5) for hour in hours}
    complete_spread = {hour: raw_spread.get(hour, 0) for hour in hours}

    return complete_sentiment, complete_spread