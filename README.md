# 舆情分析系统 - Public Opinion Analysis

基于 Flask + Kafka + Spark 的微博热搜舆情分析平台，实现从数据采集、流式处理、情感分析到可视化展示的全链路舆情监控。

## 系统架构

```
┌─────────────┐    ┌──────────┐    ┌───────────────┐    ┌─────────┐    ┌──────────┐
│  爬虫层       │───▶│  Kafka   │───▶│  Spark Streaming │───▶│  MySQL  │───▶│  Flask   │
│  Selenium   │    │  消息队列  │    │  流式数据处理     │    │  数据存储 │    │  Web 仪表盘│
└─────────────┘    └──────────┘    └───────────────┘    └─────────┘    └──────────┘
                                                              │
                                                        ┌─────┴─────┐
                                                        │   Redis   │
                                                        │   缓存层   │
                                                        └───────────┘
```

## 核心功能

### 数据采集
- **微博热搜爬虫**：Selenium + Edge WebDriver 自动抓取热搜榜单
- **文章 & 评论采集**：针对每个热搜话题，深入抓取相关文章和用户评论
- **定时抓取**：APScheduler 定时任务 + 独立热榜爬虫，持续更新数据
- **CSV 本地备份**：爬取数据自动保存 CSV 备份

### 数据处理
- **Kafka 消息队列**：多主题模式（events / articles / comments / hot_rank），解耦生产与消费
- **Spark Structured Streaming**：实时消费 Kafka 数据，解析 JSON，批写入 MySQL，自动关联事件与更新情感评分
- **WebSocket 实时推送**：热搜排行通过 WebSocket 服务广播至前端（端口 8765）

### NLP & 情感分析
- **情感分析**：SnowNLP + 自定义 150+ 词情感词典校准，多进程批处理
- **关键词提取**：jieba TF-IDF + 词性过滤 + 停用词处理，支持 KeyBERT 备用
- **事件分类**：基于正则匹配的 7 大类别自动归类（社会/政治/财经/娱乐/科技/体育/文旅）

### 机器学习
- **异常检测**：Isolation Forest 识别舆情异常事件
- **影响力评分**：五因子加权模型（热度 / 评论数 / 互动量 / 情感倾向 / 时间衰减）
- **趋势预测**：加权线性回归预测情感趋势走向

### Web 仪表盘
- 实时舆情概览：风险指数、情感趋势、热搜排行
- 事件深度分析：单事件情感分布、评论分析、关键词词云
- 数据可视化：Chart.js 前端图表 + Matplotlib 服务端渲染
- 数据导出：CSV / Excel 格式导出

### 用户系统
- 注册/登录/注销，密保问题找回密码
- 用户收藏夹，个人资料管理

## 技术栈

| 层级 | 技术 |
|------|------|
| 前端 | HTML/CSS/JS, Jinja2 模板, Chart.js |
| 后端 | Python 3, Flask 3.1 |
| 数据库 | MySQL (PyMySQL), Redis |
| 消息队列 | Apache Kafka (kafka-python) |
| 流处理 | Apache Spark 3.5 (PySpark Structured Streaming) |
| 爬虫 | Selenium 4.38, Edge WebDriver |
| NLP | jieba, SnowNLP, KeyBERT |
| 机器学习 | scikit-learn (IsolationForest) |
| 可视化 | matplotlib, wordcloud |
| 调度 | APScheduler |
| 实时推送 | WebSocket (websockets / trio-websocket) |

## 项目结构

```
project/
├── app.py                    # Flask 应用入口
├── config.py                 # 全局配置（环境变量覆盖）
├── requirements.txt          # Python 依赖
│
├── crawlers/                 # 爬虫模块
│   ├── weibo_crawler.py      # 热搜文章 & 评论爬虫
│   ├── hot_search_crawler.py # 热榜定时爬虫
│   └── comment_crawler.py    # 按需评论爬虫
│
├── kafka_producer/           # Kafka 生产者
│   └── weibo_hot_producer.py
│
├── spark_streaming/          # Spark 流处理消费者
│   ├── weibo_hot_consumer.py
│   └── hot_search_consumer.py # WebSocket 热榜推送
│
├── routes/                   # Flask 路由（Blueprint）
│   ├── auth_routes.py        # 用户认证
│   ├── dashboard_routes.py   # 仪表盘 & 事件详情
│   ├── analysis_routes.py    # 数据分析 API
│   ├── visualization_routes.py
│   ├── keywords_routes.py
│   ├── csv_export_routes.py
│   └── favorite_routes.py
│
├── services/                 # 业务逻辑层
│   ├── ml_service.py         # ML：异常检测/影响力评分/趋势预测
│   ├── analysis_service.py   # 情感分析/关键词/分类
│   ├── realtime_service.py   # 实时数据服务
│   └── redis_service.py      # Redis 缓存服务
│
├── models/                   # 数据模型（MySQL ORM 风格）
│   ├── event.py, comment.py, article.py, user.py
│
├── utils/                    # 工具函数
│   ├── text_utils.py         # NLP：情感分析 / 关键词 / 分类
│   ├── db_utils.py           # 数据库连接池
│   ├── kafka_utils.py        # Kafka 工具
│   └── chart_utils.py        # Matplotlib 图表生成
│
└── templates/                # Jinja2 前端模板
```

## 快速开始

### 环境要求

- Python 3.9+
- MySQL 5.7+
- Apache Kafka 2.13+
- Apache Spark 3.5+
- Microsoft Edge 浏览器 + Edge WebDriver
- Redis（可选，缓存服务）

### 安装

```bash
# 克隆仓库
git clone https://github.com/Lyb-L-dev/public-opinion-analysis.git
cd public-opinion-analysis

# 创建虚拟环境
python -m venv venv
venv\Scripts\activate   # Windows
# source venv/bin/activate  # Linux/Mac

# 安装依赖
pip install -r requirements.txt
# 部分库需要单独安装
pip install jieba snownlp kafka-python redis APScheduler scikit-learn wordcloud keybert websockets confluent_kafka
```

### 配置

通过环境变量配置（或直接修改 `config.py`）：

```bash
# 数据库
export DB_HOST=localhost
export DB_USER=root
export DB_PASSWORD=your_password
export DB_NAME=public_opinion_db

# Kafka
export KAFKA_BROKER=localhost:9092
export KAFKA_GROUP=weibo_hot_consumer_group

# 爬虫（Edge 浏览器配置）
export EDGE_USER_DATA_DIR=C:/Users/xxx/AppData/Local/Microsoft/Edge/User Data/Default
export EDGE_DRIVER_PATH=D:/edgedriver/msedgedriver.exe
```

### 启动

依次启动以下服务：

```bash
# 1. 确保 MySQL、Kafka、Redis 已启动

# 2. 启动 Spark 流处理消费者
python spark_streaming/weibo_hot_consumer.py

# 3. 启动 WebSocket 热榜推送服务（可选）
python spark_streaming/hot_search_consumer.py

# 4. 启动热榜定时爬虫（可选）
python crawlers/hot_search_crawler.py

# 5. 启动 Flask Web 服务
python app.py

# 访问 http://localhost:5000
```

### 数据采集

在仪表盘中手动触发爬虫，或通过 API：

```python
# 运行主爬虫（采集热搜 + 文章 + 评论）
python crawlers/weibo_crawler.py
```

## 数据库表结构

- `users` — 用户账号信息
- `hot_events` — 热搜事件（标题、热度、类别、情感评分、风险等级）
- `articles` — 关联文章（内容、作者、点赞/转发/评论数、情感评分）
- `comments` — 用户评论（内容、用户名、点赞数、情感评分）

## License

MIT License
