# hot_search_crawler.py
import time
import logging
from datetime import datetime

from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from selenium.common.exceptions import NoSuchElementException, TimeoutException
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from config import config
from utils.kafka_utils import send_weibo_data_to_kafka

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s.%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger("HotSearchCrawler")


class HotSearchCrawler:
    """仅爬取微博热搜榜，发送全量榜单到Kafka"""

    def __init__(self):
        self.driver = None
        self.hot_search_url = config.WEIBO_HOT_URL
        self.last_rank_data = None  # 可选：用于判断变化（目前发送全量）

    def setup_driver(self):
        """复用原项目的Edge配置，带用户目录免登录"""
        edge_options = Options()
        edge_options.add_argument(f"--user-data-dir={config.EDGE_USER_DATA_DIR_HOT_SEARCH}")
        edge_options.add_argument('--disable-gpu')
        edge_options.add_argument('--no-sandbox')
        edge_options.add_argument('--disable-dev-shm-usage')
        edge_options.add_argument('--disable-blink-features=AutomationControlled')
        edge_options.add_argument('--start-maximized')
        edge_options.add_argument('--headless=new')
        edge_options.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
        )
        try:
            service = Service(executable_path=config.EDGE_DRIVER_PATH)
            self.driver = webdriver.Edge(service=service, options=edge_options)
            logger.info("Edge浏览器启动成功（带用户目录免登录）")
            return True
        except Exception as e:
            logger.error(f"浏览器启动失败: {e}")
            return False

    def fetch_hot_searches(self):
        """获取热搜榜单，返回列表"""
        try:
            self.driver.get(self.hot_search_url)
            time.sleep(5)  # 等待页面加载

            # 检查登录状态（仅第一次可能需要手动登录）
            try:
                self.driver.find_element(By.CSS_SELECTOR, '.login-btn')
                logger.warning("请手动登录微博，登录后按回车继续...")
                input()
                self.driver.refresh()
                time.sleep(3)
            except NoSuchElementException:
                pass

            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'tbody'))
            )

            rows = self.driver.find_elements(By.CSS_SELECTOR, 'tbody tr')
            hot_list = []

            for row in rows:
                try:
                    # 排名
                    rank_elem = row.find_element(By.CSS_SELECTOR, '.td-01.ranktop')
                    rank = rank_elem.text.strip()

                    # 标题和链接
                    title_elem = row.find_element(By.CSS_SELECTOR, '.td-02 a')
                    title = title_elem.text.strip()
                    url = title_elem.get_attribute('href')

                    # 热度值
                    heat = 0
                    try:
                        td02 = row.find_element(By.CSS_SELECTOR, '.td-02')
                        heat_spans = td02.find_elements(By.CSS_SELECTOR, 'span')
                        if heat_spans:
                            heat_text = heat_spans[0].text.strip().replace(',', '')
                            if '万' in heat_text:
                                heat = int(float(heat_text.replace('万', '')) * 10000)
                            elif '亿' in heat_text:
                                heat = int(float(heat_text.replace('亿', '')) * 100000000)
                            elif heat_text.isdigit():
                                heat = int(heat_text)
                            else:
                                import re
                                numbers = re.findall(r'[\d,]+', heat_text)
                                if numbers:
                                    heat = int(numbers[0].replace(',', ''))
                    except Exception as e:
                        logger.debug(f"热度提取失败: {e}")

                    if url and "javascript" not in url and title and rank.isdigit():
                        hot_list.append({
                            'rank': int(rank),
                            'title': title,
                            'url': url,
                            'heat': heat,
                            'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                        })
                        logger.info(f"抓取: 第{rank}名 {title} 热度{heat}")
                except Exception as e:
                    logger.debug(f"跳过无效行: {e}")
                    continue

            logger.info(f"本次共抓取 {len(hot_list)} 条热搜")
            return hot_list

        except Exception as e:
            logger.error(f"抓取热搜榜失败: {e}", exc_info=True)
            return []

    def send_to_kafka(self, hot_list):
        """发送完整榜单到Kafka（单条记录结构，也可发整个列表）"""
        if not hot_list:
            logger.warning("无热搜数据，跳过发送")
            return
        try:
            # 发送整个榜单数组（Kafka工具支持批量）
            send_weibo_data_to_kafka(config.KAFKA_HOT_RANK_TOPIC, hot_list)
            logger.info(f"已发送 {len(hot_list)} 条热搜数据到Kafka topic: {config.KAFKA_HOT_RANK_TOPIC}")
        except Exception as e:
            logger.error(f"Kafka发送失败: {e}")

    def run_forever(self, interval_seconds=60):
        """定时循环抓取"""
        if not self.setup_driver():
            logger.error("浏览器初始化失败，程序终止")
            return

        logger.info(f"实时热搜爬虫启动，抓取间隔 {interval_seconds} 秒")
        try:
            while True:
                start_time = time.time()
                hot_data = self.fetch_hot_searches()
                if hot_data:
                    self.send_to_kafka(hot_data)
                elapsed = time.time() - start_time
                sleep_time = max(1, interval_seconds - elapsed)
                logger.info(f"本轮耗时 {elapsed:.2f}秒，休眠 {sleep_time:.2f}秒")
                time.sleep(sleep_time)
        except KeyboardInterrupt:
            logger.info("收到停止信号，爬虫退出")
        finally:
            if self.driver:
                self.driver.quit()
                logger.info("浏览器已关闭")


if __name__ == "__main__":
    crawler = HotSearchCrawler()
    crawler.run_forever(interval_seconds=120)