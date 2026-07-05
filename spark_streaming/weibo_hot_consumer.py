import pymysql
import logging
import os
import sys
from datetime import datetime
import builtins  # 确保内置round可用

# 强制设置Spark临时目录（避开中文用户目录）
os.environ['SPARK_LOCAL_DIRS'] = r"D:\spark\spark_temp"
os.environ['IVY_CACHE'] = r"D:\spark\ivy_cache"
os.environ['IVY_HOME'] = r"D:\spark\ivy_cache"
# Hadoop配置
os.environ['HADOOP_HOME'] = r"D:\Hadoop\hadoop-3.4.2"
os.environ['PATH'] = r"D:\Hadoop\hadoop-3.4.2\bin;" + os.environ['PATH']
# 禁用Hadoop本地IO的JVM参数
os.environ['JAVA_OPTS'] = "-Djava.library.path=D:/Hadoop/hadoop-3.4.2/bin -Dhadoop.io.native.lib.available=false"
from pyspark.sql import SparkSession
from pyspark.sql import functions as F   # 规范导入
from pyspark.sql.types import *
from pyspark.sql.utils import AnalysisException
from config import config

# ===================== 初始化日志 =====================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s,%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("WeiboHotSparkConsumer")

# ===================== 全局常量 =====================
CHECKPOINT_ROOT = r"D:\spark\spark_checkpoint"
BATCH_SIZE = 100
TIMEZONE = 'Asia/Shanghai'


class WeiboHotConsumer:
    def __init__(self):
        """初始化SparkSession（生产级配置，完全读取项目config）"""
        os.makedirs(CHECKPOINT_ROOT, exist_ok=True)
        logger.info(f"Spark Checkpoint根路径：{CHECKPOINT_ROOT}")

        # 核心优化：增加资源配置 + 限制Kafka消费速率
        self.spark = SparkSession.builder \
            .appName(config.SPARK_APP_NAME) \
            .master(config.SPARK_MASTER) \
            .config("spark.driver.host", "127.0.0.1")\
            .config("spark.driver.bindAddress", "127.0.0.1") \
            .config("spark.driver.memory", "2g") \
            .config("spark.executor.memory", "4g") \
            .config("spark.sql.shuffle.partitions", "8")\
            .config("spark.streaming.kafka.maxRatePerPartition", "50") \
            .config("spark.sql.streaming.backpressure.enabled", "true") \
            .config("spark.jars.packages",
                "org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0,"
                "mysql:mysql-connector-java:8.0.33") \
            .config("spark.sql.session.timeZone", TIMEZONE) \
            .config("spark.sql.adaptive.enabled", "false") \
            .getOrCreate()

        self.spark.sparkContext.setLogLevel("WARN")
        logger.info(f"SparkSession初始化成功 | 应用名：{config.SPARK_APP_NAME} | Master：{config.SPARK_MASTER}")

        # Kafka主题模式：仅分主题模式（统一主题已彻底移除）
        self.USE_MULTI_TOPIC = True
        self.KAFKA_BROKER = config.KAFKA_BROKER_LIST
        self.KAFKA_TOPICS = {
            'hot_event': getattr(config, 'KAFKA_HOT_EVENTS_TOPIC', 'weibo.hot.events'),
            'article': getattr(config, 'KAFKA_ARTICLES_TOPIC', 'weibo.hot.articles'),
            'comment': getattr(config, 'KAFKA_COMMENTS_TOPIC', 'weibo.hot.comments')
        }

        # MySQL配置
        self.MYSQL_CONFIG = {
            'host': config.DB_HOST,
            'port': config.DB_PORT,
            'user': config.DB_USER,
            'password': config.DB_PASSWORD,
            'database': config.DB_NAME,
            'charset': 'utf8mb4',
            'connect_timeout': 30
        }
        logger.info(f"Kafka模式：分主题 | MySQL主机：{self.MYSQL_CONFIG['host']}")

        self.define_schemas()

    def define_schemas(self):
        """定义数据Schema，与Kafka发送的字段、MySQL表字段1:1匹配"""
        # 热点事件Schema
        self.event_schema = StructType([
            StructField("title", StringType(), nullable=False),
            StructField("crawl_time", StringType(), nullable=False),
            StructField("heat", IntegerType(), nullable=True),
            StructField("sentiment_score", DoubleType(), nullable=True)
        ])

        # 文章Schema
        self.article_schema = StructType([
            StructField("article_id", StringType(), nullable=False),
            StructField("author", StringType(), nullable=True),
            StructField("content", StringType(), nullable=True),
            StructField("publish_time", StringType(), nullable=False),
            StructField("like_count", IntegerType(), nullable=True),
            StructField("repost_count", IntegerType(), nullable=True),
            StructField("comment_count", IntegerType(), nullable=True),
            StructField("crawl_time", StringType(), nullable=False),
            StructField("hot_rank", IntegerType(), nullable=True),
            StructField("hot_title", StringType(), nullable=False),
            StructField("sentiment_score", DoubleType(), nullable=True),
            StructField("sentiment_type", StringType(), nullable=True)
        ])

        # 评论Schema
        self.comment_schema = StructType([
            StructField("comment_id", StringType(), nullable=False),
            StructField("username", StringType(), nullable=True),
            StructField("user_id", StringType(), nullable=True),
            StructField("content", StringType(), nullable=True),
            StructField("publish_time", StringType(), nullable=False),
            StructField("location", StringType(), nullable=True),
            StructField("like_count", IntegerType(), nullable=True),
            StructField("crawl_time", StringType(), nullable=False),
            StructField("hot_rank", IntegerType(), nullable=True),
            StructField("hot_title", StringType(), nullable=False),
            StructField("sentiment_score", DoubleType(), nullable=True),
            StructField("sentiment_type", StringType(), nullable=True),
            StructField("event_id", IntegerType(), nullable=True)  # 评论爬虫添加的event_id字段
        ])

        # ===== Kafka消息外层通用Schema（所有分主题消息格式一致）=====
        self.kafka_outer_schema = StructType([
            StructField("send_time", StringType(), True),
            StructField("data", ArrayType(StringType()), True)
        ])

    def create_kafka_stream(self, topic):
        """创建Kafka流（必须指定主题）"""
        try:
            kafka_stream = self.spark.readStream \
                .format("kafka") \
                .option("kafka.bootstrap.servers", self.KAFKA_BROKER) \
                .option("subscribe", topic) \
                .option("startingOffsets", "latest") \
                .option("failOnDataLoss", "false") \
                .option("kafka.session.timeout.ms", 30000) \
                .load()
            logger.info(f"成功创建Kafka流 | 主题：{topic}")
            return kafka_stream
        except Exception as e:
            logger.error(f"创建Kafka流失败：{e}", exc_info=True)
            raise

    # -------------------- 热点事件流 --------------------
    def process_hot_event_stream(self):
        """处理热点事件流：解析外层JSON，展开data数组，清洗后写入MySQL"""
        kafka_stream = self.create_kafka_stream(self.KAFKA_TOPICS['hot_event'])

        event_df = kafka_stream \
            .select(F.from_json(F.col("value").cast(StringType()), self.kafka_outer_schema).alias("outer")) \
            .select(F.explode("outer.data").alias("json_str")) \
            .select(F.from_json(F.col("json_str"), self.event_schema).alias("data")) \
            .select("data.*") \
            .filter(F.col("title").isNotNull())

        # 保持字符串类型，不转换为timestamp
        processed_event_df = event_df \
            .filter(F.col("crawl_time").isNotNull()) \
            .fillna({"heat": 0, "sentiment_score": 0.5})

        checkpoint_path = os.path.join(CHECKPOINT_ROOT, "hot_event")
        mysql_config = self.MYSQL_CONFIG  # 闭包捕获，可序列化

        def write_event_batch(df, epoch_id):
            if df.count() > 0:
                def save_partition(partition):
                    WeiboHotConsumer._batch_save_event_to_mysql(partition, mysql_config)

                df.foreachPartition(save_partition)
                logger.info(f"Epoch {epoch_id} | 热点事件处理完成，写入行数：{df.count()}")

        event_query = processed_event_df.writeStream \
            .foreachBatch(write_event_batch) \
            .option("checkpointLocation", checkpoint_path) \
            .outputMode("append") \
            .trigger(processingTime='5 seconds') \
            .start()
        logger.info("热点事件流处理已启动")
        return event_query

    # -------------------- 文章流 --------------------
    def process_article_stream(self):
        """处理文章流：自动关联/插入热点事件，保证每篇文章都有event_id"""
        kafka_stream = self.create_kafka_stream(self.KAFKA_TOPICS['article'])

        article_df = kafka_stream \
            .select(F.from_json(F.col("value").cast(StringType()), self.kafka_outer_schema).alias("outer")) \
            .select(F.explode("outer.data").alias("json_str")) \
            .select(F.from_json(F.col("json_str"), self.article_schema).alias("data")) \
            .select("data.*") \
            .filter(F.col("hot_title").isNotNull())

        # ==================== 数据清洗强化 ====================
        # 1. 过滤掉唯一键为空的行（防止主键冲突）
        article_df = article_df.filter(F.col("article_id").isNotNull())

        # 2. 时间字段处理：保持字符串类型，不转换为timestamp（避免序列化错误）
        # 直接使用 Kafka 中的字符串时间，格式已经是 yyyy-MM-dd HH:mm:ss
        processed_article_df = article_df \
            .filter(F.col("publish_time").isNotNull()) \
            .filter(F.col("crawl_time").isNotNull()) \
            .fillna({
            "like_count": 0,
            "repost_count": 0,
            "comment_count": 0,
            "sentiment_score": 0.5,
            "sentiment_type": "中性",
            "author": "未知",
            "content": "无内容"  # 避免content为null
        })
        # ====================================================

        checkpoint_path = os.path.join(CHECKPOINT_ROOT, "article")
        mysql_config = self.MYSQL_CONFIG

        def write_article_batch(df, epoch_id):
            # 添加入口日志，实时监控数据质量
            logger.info(f"📥【文章流】epoch={epoch_id}, 原始行数={df.count()}, "
                        f"publish_time为空数={df.filter(F.col('publish_time').isNull()).count()}, "
                        f"crawl_time为空数={df.filter(F.col('crawl_time').isNull()).count()}")
            if df.count() == 0:
                return

            # 1. 实时读取 hot_events 表，获取 title -> id 映射
            event_df = self.spark.read \
                .format("jdbc") \
                .option("url",
                        f"jdbc:mysql://{self.MYSQL_CONFIG['host']}:{self.MYSQL_CONFIG['port']}/{self.MYSQL_CONFIG['database']}?useSSL=false&serverTimezone={TIMEZONE}") \
                .option("dbtable", "hot_events") \
                .option("user", self.MYSQL_CONFIG['user']) \
                .option("password", self.MYSQL_CONFIG['password']) \
                .option("driver", "com.mysql.cj.jdbc.Driver") \
                .load() \
                .select(F.col("title").alias("hot_title"), F.col("id").alias("event_id"))

            # 2. 左连接
            joined_df = df.join(event_df, on="hot_title", how="left")

            # 3. 找出缺失的标题
            missing_titles_df = joined_df.filter(F.col("event_id").isNull()).select("hot_title").distinct()
            missing_titles = [row.hot_title for row in missing_titles_df.collect()]

            if missing_titles:
                logger.info(f"发现缺失的热点事件标题 {len(missing_titles)} 个，即将自动插入")
                WeiboHotConsumer._batch_insert_hot_events(mysql_config, missing_titles)

                # 重新读取更新后的 hot_events 表
                event_df_updated = self.spark.read \
                    .format("jdbc") \
                    .option("url",
                            f"jdbc:mysql://{self.MYSQL_CONFIG['host']}:{self.MYSQL_CONFIG['port']}/{self.MYSQL_CONFIG['database']}?useSSL=false&serverTimezone={TIMEZONE}") \
                    .option("dbtable", "hot_events") \
                    .option("user", self.MYSQL_CONFIG['user']) \
                    .option("password", self.MYSQL_CONFIG['password']) \
                    .option("driver", "com.mysql.cj.jdbc.Driver") \
                    .load() \
                    .select(F.col("title").alias("hot_title"), F.col("id").alias("event_id"))

                joined_df = df.join(event_df_updated, on="hot_title", how="left")

            # 4. 过滤掉仍为 null 的行（极少数插入失败的情况）
            final_df = joined_df.filter(F.col("event_id").isNotNull()).drop("hot_rank")

            if final_df.count() > 0:
                final_df.foreachPartition(
                    lambda partition: WeiboHotConsumer._batch_save_article_to_mysql(partition, mysql_config)
                )
                logger.info(f"✅ Epoch {epoch_id} | 文章处理完成，写入行数：{final_df.count()}")

        article_query = processed_article_df.writeStream \
            .foreachBatch(write_article_batch) \
            .option("checkpointLocation", checkpoint_path) \
            .outputMode("append") \
            .trigger(processingTime='5 seconds') \
            .start()

        logger.info("文章流处理已启动（自动补全热点事件 + 空值防御）")
        return article_query

    # -------------------- 评论流 --------------------
    def process_comment_stream(self):
        """处理评论流：自动关联/插入热点事件，保证每条评论都有event_id"""
        kafka_stream = self.create_kafka_stream(self.KAFKA_TOPICS['comment'])

        comment_df = kafka_stream \
            .select(F.from_json(F.col("value").cast(StringType()), self.kafka_outer_schema).alias("outer")) \
            .select(F.explode("outer.data").alias("json_str")) \
            .select(F.from_json(F.col("json_str"), self.comment_schema).alias("data")) \
            .select("data.*") \
            .filter(F.col("hot_title").isNotNull())

        # ==================== 数据清洗强化 ====================
        # 1. 过滤掉唯一键为空的行
        comment_df = comment_df.filter(F.col("comment_id").isNotNull())

        # 2. 时间字段处理：保持字符串类型，不转换为timestamp（避免序列化错误）
        # 直接使用 Kafka 中的字符串时间，格式已经是 yyyy-MM-dd HH:mm:ss
        processed_comment_df = comment_df \
            .filter(F.col("publish_time").isNotNull()) \
            .filter(F.col("crawl_time").isNotNull()) \
            .fillna({
            "like_count": 0,
            "sentiment_score": 0.5,
            "sentiment_type": "中性",
            "username": "未知",
            "user_id": "未知",
            "location": "未知",
            "content": "无内容"
        })
        # ====================================================

        checkpoint_path = os.path.join(CHECKPOINT_ROOT, "comment")
        mysql_config = self.MYSQL_CONFIG

        def write_comment_batch(df, epoch_id):
            logger.info(f"📥【评论流】epoch={epoch_id}, 原始行数={df.count()}, "
                        f"publish_time为空数={df.filter(F.col('publish_time').isNull()).count()}, "
                        f"crawl_time为空数={df.filter(F.col('crawl_time').isNull()).count()}")
            if df.count() == 0:
                return

            # 1. 实时读取 hot_events 表
            event_df = self.spark.read \
                .format("jdbc") \
                .option("url",
                        f"jdbc:mysql://{self.MYSQL_CONFIG['host']}:{self.MYSQL_CONFIG['port']}/{self.MYSQL_CONFIG['database']}?useSSL=false&serverTimezone={TIMEZONE}") \
                .option("dbtable", "hot_events") \
                .option("user", self.MYSQL_CONFIG['user']) \
                .option("password", self.MYSQL_CONFIG['password']) \
                .option("driver", "com.mysql.cj.jdbc.Driver") \
                .load() \
                .select(F.col("title").alias("hot_title"), F.col("id").alias("event_id_lookup"))

            # 2. 左连接（通过hot_title查找event_id作为备用）
            joined_df = df.join(event_df, on="hot_title", how="left")

            # 3. 如果评论已经有event_id（从Kafka传入），则优先使用；否则使用通过hot_title查找到的event_id
            # 使用coalesce：优先使用data中的event_id，如果为null则使用查找到的event_id_lookup
            final_df = joined_df.withColumn(
                "event_id",
                F.coalesce(F.col("event_id"), F.col("event_id_lookup"))
            ).drop("event_id_lookup", "hot_rank")

            # 4. 找出缺失的标题（只有当event_id为null时才需要处理）
            missing_titles_df = final_df.filter(F.col("event_id").isNull()).select("hot_title").distinct()
            missing_titles = [row.hot_title for row in missing_titles_df.collect()]

            if missing_titles:
                logger.info(f"发现缺失的热点事件标题 {len(missing_titles)} 个，即将自动插入")
                WeiboHotConsumer._batch_insert_hot_events(mysql_config, missing_titles)

                # 重新读取
                event_df_updated = self.spark.read \
                    .format("jdbc") \
                    .option("url",
                            f"jdbc:mysql://{self.MYSQL_CONFIG['host']}:{self.MYSQL_CONFIG['port']}/{self.MYSQL_CONFIG['database']}?useSSL=false&serverTimezone={TIMEZONE}") \
                    .option("dbtable", "hot_events") \
                    .option("user", self.MYSQL_CONFIG['user']) \
                    .option("password", self.MYSQL_CONFIG['password']) \
                    .option("driver", "com.mysql.cj.jdbc.Driver") \
                    .load() \
                    .select(F.col("title").alias("hot_title"), F.col("id").alias("event_id_lookup"))

                # 重新关联：过滤event_id为null的行，drop后重命名
                final_df_null = final_df.filter(F.col("event_id").isNull()).drop("event_id") \
                    .join(event_df_updated, on="hot_title", how="left") \
                    .withColumnRenamed("event_id_lookup", "event_id")

                # 合并：原来有event_id的（从final_df取，schema一致） + 重新关联的
                final_df = final_df.filter(F.col("event_id").isNotNull()).union(final_df_null)

            # 5. 过滤并写入
            final_df = final_df.filter(F.col("event_id").isNotNull())
            if final_df.count() > 0:
                final_df.foreachPartition(
                    lambda partition: WeiboHotConsumer._batch_save_comment_to_mysql(partition, mysql_config)
                )
                logger.info(f"✅ Epoch {epoch_id} | 评论处理完成，写入行数：{final_df.count()}")

        comment_query = processed_comment_df.writeStream \
            .foreachBatch(write_comment_batch) \
            .option("checkpointLocation", checkpoint_path) \
            .outputMode("append") \
            .trigger(processingTime='5 seconds') \
            .start()

        logger.info("评论流处理已启动（自动补全热点事件 + 空值防御）")
        return comment_query

    # -------------------- 静态方法：批量插入缺失的热点事件 --------------------
    @staticmethod
    def _batch_insert_hot_events(mysql_config, titles):
        """
        批量插入缺失的热点事件（仅标题，其他字段使用默认值）
        :param mysql_config: MySQL连接配置字典
        :param titles: list of str, 热点标题列表
        """
        if not titles:
            return
        conn = None
        try:
            conn = pymysql.connect(**mysql_config)
            cursor = conn.cursor()
            sql = """
            INSERT IGNORE INTO hot_events (title, crawl_time, heat, sentiment_score)
            VALUES (%s, %s, %s, %s)
            """
            now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            params = [(title, now_str, 0, 0.5) for title in titles]
            cursor.executemany(sql, params)
            conn.commit()
            logger.info(f"自动插入 {len(titles)} 条热点事件：{titles}")
        except pymysql.MySQLError as e:
            if conn:
                conn.rollback()
            logger.error(f"批量插入热点事件失败：{e}", exc_info=True)
        finally:
            if conn:
                cursor.close()
                conn.close()

    # -------------------- 静态方法：批量写入MySQL（完全可序列化）--------------------
    @staticmethod
    def _batch_save_event_to_mysql(partition, mysql_config):
        """批量保存热点事件到MySQL"""
        conn = None
        try:
            conn = pymysql.connect(**mysql_config)
            cursor = conn.cursor()
            sql = """
            INSERT IGNORE INTO hot_events (title, crawl_time, heat, sentiment_score)
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                heat = VALUES(heat),
                sentiment_score = VALUES(sentiment_score),
                crawl_time = VALUES(crawl_time)
            """
            params = []
            for row in partition:
                # crawl_time 现在是字符串类型
                crawl_time_str = row.crawl_time if row.crawl_time else datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                params.append((
                    row.title,
                    crawl_time_str,
                    row.heat,
                    row.sentiment_score
                ))
                if len(params) >= BATCH_SIZE:
                    cursor.executemany(sql, params)
                    conn.commit()
                    params.clear()
            if params:
                cursor.executemany(sql, params)
                conn.commit()
        except pymysql.MySQLError as e:
            if conn: conn.rollback()
            logger.error(f"批量保存热点事件失败：{e}", exc_info=True)
        finally:
            if conn:
                cursor.close()
                conn.close()

    @staticmethod
    def _batch_save_article_to_mysql(partition, mysql_config):
        """批量保存文章到MySQL（含空值兜底）并自动更新事件情感分"""
        conn = None
        try:
            conn = pymysql.connect(**mysql_config)
            cursor = conn.cursor()
            sql = """
            INSERT IGNORE INTO articles (
                event_id, author, content, publish_time, like_count,
                repost_count, comment_count, article_id, crawl_time,
                sentiment_score, hot_title, sentiment_type
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                like_count = VALUES(like_count),
                repost_count = VALUES(repost_count),
                comment_count = VALUES(comment_count),
                crawl_time = VALUES(crawl_time),
                sentiment_score = VALUES(sentiment_score),
                sentiment_type = VALUES(sentiment_type),
                hot_title = VALUES(hot_title)
            """
            params = []
            event_ids = set()  # 收集涉及的事件ID
            for row in partition:
                # 时间字段现在是字符串类型，直接使用
                publish_time_str = row.publish_time if row.publish_time else "1970-01-01 00:00:00"
                crawl_time_str = row.crawl_time if row.crawl_time else "1970-01-01 00:00:00"

                # 收集event_id用于后续更新事件情感分
                if row.event_id is not None:
                    event_ids.add(row.event_id)

                params.append((
                    row.event_id,
                    row.author,
                    row.content,
                    publish_time_str,
                    row.like_count,
                    row.repost_count,
                    row.comment_count,
                    row.article_id,
                    crawl_time_str,
                    row.sentiment_score,
                    row.hot_title,
                    row.sentiment_type
                ))
                if len(params) >= BATCH_SIZE:
                    cursor.executemany(sql, params)
                    conn.commit()
                    params.clear()
            if params:
                cursor.executemany(sql, params)
                conn.commit()

            # 写入完成后，更新涉及的事件的情感分数
            for event_id in event_ids:
                WeiboHotConsumer._update_event_sentiment(mysql_config, event_id)
                logger.info(f"文章写入后自动更新事件ID {event_id} 的情感分数")

        except pymysql.MySQLError as e:
            if conn:
                conn.rollback()
            logger.error(f"批量保存文章失败：{e}", exc_info=True)
        finally:
            if conn:
                cursor.close()
                conn.close()

    # -------------------- 静态方法：更新事件情感分数（根据关联评论计算平均分） --------------------
    @staticmethod
    def _update_event_sentiment(mysql_config, event_id):
        """
        根据关联评论的平均情感分数更新事件情感分
        :param mysql_config: MySQL连接配置字典
        :param event_id: 事件ID
        """
        conn = None
        try:
            conn = pymysql.connect(**mysql_config)
            cursor = conn.cursor()
            # 计算该事件关联评论的平均情感分
            sql_avg = "SELECT AVG(sentiment_score) as avg_score FROM comments WHERE event_id = %s AND sentiment_score IS NOT NULL"
            cursor.execute(sql_avg, (event_id,))
            result = cursor.fetchone()

            if result and result[0] is not None:
                # 使用内置的round函数（已通过builtins确保）
                avg_score = builtins.round(float(result[0]), 3)
                # 更新事件的情感分数
                sql_update = "UPDATE hot_events SET sentiment_score = %s WHERE id = %s"
                cursor.execute(sql_update, (avg_score, event_id))
                conn.commit()
                logger.debug(f"事件ID {event_id} 情感分已更新为 {avg_score}")
            else:
                # 如果没有评论，使用事件标题计算情感分
                sql_title = "SELECT title FROM hot_events WHERE id = %s"
                cursor.execute(sql_title, (event_id,))
                title_result = cursor.fetchone()
                if title_result and title_result[0]:
                    # 尝试导入情感分析函数（如果可用）
                    try:
                        from utils.text_utils import analyze_sentiment
                        score = analyze_sentiment(title_result[0])
                        sql_update = "UPDATE hot_events SET sentiment_score = %s WHERE id = %s"
                        cursor.execute(sql_update, (score, event_id))
                        conn.commit()
                        logger.debug(f"事件ID {event_id} 情感分已根据标题更新为 {score}")
                    except ImportError:
                        # 如果无法导入情感分析，保持默认0.5
                        pass
        except pymysql.MySQLError as e:
            logger.error(f"更新事件情感分失败：{e}", exc_info=True)
        finally:
            if conn:
                cursor.close()
                conn.close()

    @staticmethod
    def _batch_save_comment_to_mysql(partition, mysql_config):
        """批量保存评论到MySQL（含空值兜底）并自动更新事件情感分"""
        conn = None
        try:
            conn = pymysql.connect(**mysql_config)
            cursor = conn.cursor()
            sql = """
            INSERT IGNORE INTO comments (
                username, user_id, content, publish_time, location,
                like_count, comment_id, crawl_time, event_id, hot_title,
                sentiment_score, sentiment_type
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                like_count = VALUES(like_count),
                crawl_time = VALUES(crawl_time),
                sentiment_score = VALUES(sentiment_score),
                sentiment_type = VALUES(sentiment_type),
                hot_title = VALUES(hot_title)
            """
            params = []
            event_ids = set()  # 收集涉及的事件ID
            for row in partition:
                # 时间字段现在是字符串类型，直接使用
                publish_time_str = row.publish_time if row.publish_time else "1970-01-01 00:00:00"
                crawl_time_str = row.crawl_time if row.crawl_time else "1970-01-01 00:00:00"
                content = row.content if row.content is not None else "无内容"

                # 收集event_id用于后续更新事件情感分
                if row.event_id is not None:
                    event_ids.add(row.event_id)

                params.append((
                    row.username,
                    row.user_id,
                    content,
                    publish_time_str,
                    row.location,
                    row.like_count,
                    row.comment_id,
                    crawl_time_str,
                    row.event_id,
                    row.hot_title,
                    row.sentiment_score,
                    row.sentiment_type
                ))
                if len(params) >= BATCH_SIZE:
                    cursor.executemany(sql, params)
                    conn.commit()
                    params.clear()
            if params:
                cursor.executemany(sql, params)
                conn.commit()

            # 写入完成后，更新涉及的事件的情感分数
            for event_id in event_ids:
                WeiboHotConsumer._update_event_sentiment(mysql_config, event_id)
                logger.info(f"评论写入后自动更新事件ID {event_id} 的情感分数")

        except pymysql.MySQLError as e:
            if conn:
                conn.rollback()
            logger.error(f"批量保存评论失败：{e}", exc_info=True)
        finally:
            if conn:
                cursor.close()
                conn.close()

    def run(self):
        """启动所有流处理（仅分主题模式）"""
        try:
            queries = [
                self.process_hot_event_stream(),
                self.process_article_stream(),
                self.process_comment_stream()
            ]

            logger.info("===== 微博热点Spark Streaming消费程序已全部启动 =====")
            logger.info(f"===== 共启动{len(queries)}个流查询，等待数据消费... =====")
            for query in queries:
                query.awaitTermination()
        except KeyboardInterrupt:
            logger.info("接收到停止信号，正在优雅关闭流处理...")
            self.spark.stop()
            logger.info("SparkSession已关闭，消费程序终止")
        except Exception as e:
            logger.error(f"流处理运行异常：{e}", exc_info=True)
            self.spark.stop()


if __name__ == "__main__":
    consumer = WeiboHotConsumer()
    consumer.run()