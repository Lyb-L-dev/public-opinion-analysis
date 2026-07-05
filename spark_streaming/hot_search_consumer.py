import asyncio
import json
import logging
import signal
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import websockets
from confluent_kafka import Consumer, KafkaError

from config import config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("HotSearchConsumer")

# 全局WebSocket客户端集合
WEBSOCKET_CLIENTS = set()
executor = ThreadPoolExecutor(max_workers=1)


async def broadcast_hot_rank(data):
    """向所有客户端广播，并自动移除失效连接"""
    if not WEBSOCKET_CLIENTS:
        return
    message = json.dumps(data, ensure_ascii=False)
    disconnected = set()
    for client in WEBSOCKET_CLIENTS:
        try:
            await client.send(message)
        except Exception as e:
            logger.warning(f"客户端发送失败，即将移除: {e}")
            disconnected.add(client)
    for client in disconnected:
        WEBSOCKET_CLIENTS.remove(client)
    logger.info(f"广播完成，在线 {len(WEBSOCKET_CLIENTS)} 个，移除 {len(disconnected)} 个")


async def websocket_handler(websocket):
    """处理WebSocket连接"""
    # 可选：简单token认证（从请求头获取）
    # token = websocket.request_headers.get("Authorization", "")
    # if token != config.WEBSOCKET_TOKEN:  # 需在config中定义WEBSOCKET_TOKEN
    #     await websocket.close(code=1008, reason="unauthorized")
    #     return

    WEBSOCKET_CLIENTS.add(websocket)
    logger.info(f"新客户端连接，当前在线: {len(WEBSOCKET_CLIENTS)}")
    try:
        await websocket.wait_closed()
    finally:
        WEBSOCKET_CLIENTS.remove(websocket)
        logger.info(f"客户端断开，当前在线: {len(WEBSOCKET_CLIENTS)}")


async def kafka_consumer_loop():
    """Kafka消费者协程（非阻塞版，使用线程池）"""
    conf = {
        'bootstrap.servers': config.KAFKA_BOOTSTRAP_SERVERS,  # 现在可以正确读取
        'group.id': config.KAFKA_CONSUMER_GROUP,
        'auto.offset.reset': 'latest',
        'enable.auto.commit': True,
    }
    consumer = Consumer(conf)
    consumer.subscribe([config.KAFKA_HOT_RANK_TOPIC])  # 新增的专属topic
    logger.info(f"已订阅 Kafka topic: {config.KAFKA_HOT_RANK_TOPIC}")

    loop = asyncio.get_running_loop()
    try:
        while True:
            # 在线程池中执行阻塞的poll，避免阻塞事件循环
            msg = await loop.run_in_executor(executor, consumer.poll, 1.0)
            if msg is None:
                await asyncio.sleep(0.01)
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                else:
                    logger.error(f"Kafka消费错误: {msg.error()}")
                    await asyncio.sleep(5)  # 避免疯狂重试
                    continue

            try:
                hot_rank_data = json.loads(msg.value().decode('utf-8'))
                if isinstance(hot_rank_data, list):
                    await broadcast_hot_rank({
                        "timestamp": datetime.now().isoformat(),
                        "data": hot_rank_data
                    })
                else:
                    logger.warning("非数组格式，直接透传")
                    await broadcast_hot_rank(hot_rank_data)
            except Exception as e:
                logger.error(f"处理消息异常: {e}")
    except asyncio.CancelledError:
        logger.info("消费者任务被取消")
    finally:
        consumer.close()
        logger.info("Kafka消费者已关闭")


async def main():
    """主协程：启动WebSocket服务 + Kafka消费者"""
    # 启动WebSocket服务器
    ws_server = await websockets.serve(
        websocket_handler,
        "0.0.0.0",        # 生产环境可改为127.0.0.1
        8765,
        ping_interval=30,
        ping_timeout=10
    )
    logger.info("WebSocket服务已启动，端口 8765")

    # 创建消费者任务
    consumer_task = asyncio.create_task(kafka_consumer_loop())

    try:
        # 等待WebSocket服务器关闭（通常是永不关闭，直到收到信号）
        await ws_server.wait_closed()
    finally:
        # 优雅取消消费者任务
        consumer_task.cancel()
        await consumer_task
        # 关闭WebSocket服务器
        ws_server.close()
        await ws_server.wait_closed()
        logger.info("WebSocket服务已关闭")


if __name__ == "__main__":
    asyncio.run(main())