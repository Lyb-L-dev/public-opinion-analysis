"""
评论爬虫 - 用于重新爬取指定热搜的评论数据
直接访问热搜详情页，然后爬取评论
"""

import logging
import random
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import time
import re
from datetime import datetime
import hashlib

# 导入配置和工具
try:
    from config import config
    from utils.kafka_utils import send_weibo_data_to_kafka, close_kafka_producer
    from crawlers.weibo_crawler import WeiboHotSearchCommentCrawler
except ImportError as e:
    logging.error(f"配置/ Kafka工具导入失败，程序终止：{e}")
    raise SystemExit(1)

# 初始化日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s,%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 兜底情感分析
try:
    from utils.text_utils import analyze_sentiment, get_sentiment_type
except ImportError as e:
    logger.warning(f"情感分析工具导入失败，启用兜底实现：{e}")

    def analyze_sentiment(content):
        return 0.5

    def get_sentiment_type(score):
        return "中性"


class CommentCrawler(WeiboHotSearchCommentCrawler):
    """评论爬虫类 - 直接访问热搜页爬取评论"""
    def __init__(self):
        super().__init__()
        self.comment_only_mode = True  # 标记只爬取评论


    def close_driver(self):
        """关闭浏览器"""
        if self.driver:
            try:
                self.driver.quit()
                logger.info("浏览器已关闭")
            except Exception as e:
                logger.error(f"关闭浏览器失败：{e}")



    # ========== 提取单条评论信息 ==========

    # ========== 爬取可见评论 ==========

    # ========== 滚动爬取评论 ==========

    def crawl_comments_on_page(self):
        """
        在当前页面点击第一个卡片的评论按钮，然后进入完整评论页，最后滚动爬取
        """
        try:
            # 1. 找到第一个卡片的评论按钮并点击
            logger.info("点击第一个卡片的评论按钮...")
            comment_selector = 'a[action-type="feed_list_comment"]'

            # 尝试找到第一个可见的评论按钮
            try:
                first_btn = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, comment_selector))
                )
                if first_btn and first_btn.is_displayed():
                    logger.info("找到第一个卡片的评论按钮，点击展开评论区")
                    self.driver.execute_script("arguments[0].click();", first_btn)
                    time.sleep(8)
                else:
                    logger.warning("未找到可见的评论按钮")
                    return None
            except TimeoutException:
                logger.warning("未找到评论按钮")
                return None

            # 2. 滚动到评论区
            logger.info("滚动到评论区...")
            comment_area_selectors = ['.comment_list', '[node-type="comment_list"]', '.list_box']
            comment_area = None
            for selector in comment_area_selectors:
                try:
                    comment_area = self.driver.find_element(By.CSS_SELECTOR, selector)
                    break
                except:
                    continue

            if comment_area:
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'start', behavior: 'smooth'});", comment_area)
                time.sleep(3)
                logger.info("已滚动到评论区顶部")
                self.current_scroll_position = self.driver.execute_script("return window.pageYOffset;")
            else:
                logger.warning("未找到评论区元素")

            # 3. 点击"后面还有"进入完整评论页
            logger.info("寻找完整评论页按钮...")
            more_comment_xpath = '//a[contains(text(), "后面还有") and contains(text(), "点击查看")]'
            try:
                more_btn = WebDriverWait(self.driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, more_comment_xpath))
                )
                comment_url = more_btn.get_attribute('href')
                if not comment_url.startswith('http'):
                    comment_url = f"https:{comment_url}" if comment_url.startswith('//') else f"https://{comment_url}"
                logger.info(f"找到完整评论页URL: {comment_url}")
                self.driver.get(comment_url)
                time.sleep(8)
                logger.info("已进入完整评论页")
                self.driver.execute_script("window.scrollTo(0, 500);")
                time.sleep(3)
                self.current_scroll_position = 500
            except TimeoutException:
                logger.info("未找到完整评论页按钮，使用当前热搜页的评论区")
            except Exception as e:
                logger.warning(f"进入完整评论页失败: {e}")

            return self.driver.current_url

        except Exception as e:
            logger.error(f"点击评论按钮失败: {e}")
            return None

    def search_and_crawl_by_title(self, title, event_id, max_scrolls=None):
        """根据标题搜索并爬取评论"""
        # 使用config中的配置，如果没有传入则使用默认值
        if max_scrolls is None:
            max_scrolls = config.CRAWL_MAX_SCROLLS

        self.current_hot_title = title

        try:
            if not self.setup_driver():
                return {
                    'success': False,
                    'message': '浏览器初始化失败',
                    'comment_count': 0
                }

            # 加载数据库中已有的评论ID用于去重
            try:
                from models.comment import Comment
                existing_comments = Comment.get_by_event_id(event_id)
                existing_ids = {comment.comment_id for comment in existing_comments if comment.comment_id}
                # 将已有ID加入到去重集合
                self.processed_comment_ids.update(existing_ids)
                logger.info(f"已加载 {len(existing_ids)} 条已有评论ID到去重集合")
            except Exception as e:
                logger.warning(f"加载已有评论ID失败: {e}")

            # 访问微博搜索页面，通过搜索功能搜索热搜
            search_url = f"https://s.weibo.com/weibo?q={title}&wvr=6&b=1"
            logger.info(f"访问微博搜索: {search_url}")
            self.driver.get(search_url)
            time.sleep(5)

            # 等待页面加载
            try:
                WebDriverWait(self.driver, 15).until(
                    EC.presence_of_element_located((By.CSS_SELECTOR, 'div.woo-panel-main'))
                )
            except:
                pass

            logger.info(f"当前页面: {self.driver.current_url}")

            # 直接在搜索页面点击第一个卡片的评论按钮
            logger.info("开始点击评论按钮...")
            self.crawl_comments_on_page()

            # 滚动爬取评论（包含数据库去重）
            comments = self.crawl_comments_with_scroll(max_scrolls)

            # 添加event_id到评论
            for comment in comments:
                comment['event_id'] = event_id

            # 发送评论到Kafka
            if comments:
                try:
                    send_weibo_data_to_kafka(config.KAFKA_COMMENTS_TOPIC, comments)
                    logger.info(f"成功发送评论数据到Kafka：{len(comments)}条")
                except Exception as e:
                    logger.error(f"发送Kafka失败：{e}")

            self.close_driver()
            return {
                'success': True,
                'message': '爬取成功',
                'comment_count': len(comments)
            }

        except Exception as e:
            logger.error(f"爬取评论异常：{e}")
            self.close_driver()
            return {
                'success': False,
                'message': f'爬取异常: {str(e)}',
                'comment_count': 0
            }

    def _find_and_click_hot_link(self, title):
        """在搜索结果中找到热搜链接并点击"""
        try:
            # 清理标题
            clean_title = re.sub(r'[^\w\u4e00-\u9fa5]', '', title).lower()

            # 查找所有链接
            all_links = self.driver.find_elements(By.CSS_SELECTOR, 'a[href*="weibo.com"]')

            for link in all_links:
                try:
                    href = link.get_attribute('href')
                    link_text = link.text.strip()

                    if not href or not link_text or len(link_text) < 2:
                        continue

                    # 跳过无关链接
                    if any(x in href for x in ['login', 'account', 'javascript', 'weibo.com/p', 'weibo.com/pub', 'weibo.com/u']):
                        continue

                    # 清理链接文本
                    clean_link_text = re.sub(r'[^\w\u4e00-\u9fa5]', '', link_text).lower()

                    # 匹配标题
                    if (title in link_text or link_text in title or
                        (len(clean_title) >= 3 and len(clean_link_text) >= 3 and
                         sum(1 for c in clean_title[:6] if c in clean_link_text) >= min(4, len(clean_title) // 2))):

                        # 检查是否是详情页链接
                        if 'weibo.com' in href and ('detail' in href or 'tv/show' in href or 'article' in href):
                            logger.info(f"找到热搜详情页链接: {link_text}，点击进入")
                            self.driver.execute_script("arguments[0].click();", link)
                            time.sleep(3)
                            return True
                except:
                    continue

            # 如果没找到详情页，返回第一个看起来像热搜的链接
            for link in all_links[:15]:
                try:
                    href = link.get_attribute('href')
                    link_text = link.text.strip()

                    if not href or not link_text or len(link_text) < 3:
                        continue
                    if any(x in href for x in ['login', 'account', 'javascript', 'weibo.com/p', 'weibo.com/pub', 'weibo.com/u']):
                        continue
                    if 'weibo.com' in href:
                        logger.info(f"使用搜索结果链接: {link_text}")
                        self.driver.execute_script("arguments[0].click();", link)
                        time.sleep(3)
                        return True
                except:
                    continue

            return False

        except Exception as e:
            logger.error(f"查找热搜链接失败: {e}")
            return False


def crawl_comments_for_event(event_id, title, max_scrolls=15):
    """爬取指定事件的评论"""
    crawler = CommentCrawler()
    return crawler.search_and_crawl_by_title(title, event_id, max_scrolls)


if __name__ == '__main__':
    result = crawl_comments_for_event(1, "测试热搜")
    print(result)
