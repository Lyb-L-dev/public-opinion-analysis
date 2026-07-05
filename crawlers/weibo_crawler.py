
import random
# import logger  # 注释掉此行，避免导入错误，使用下方的logging
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
from selenium.common.exceptions import TimeoutException, NoSuchElementException
import time
import pandas as pd
import re
import os
import hashlib
import logging
from datetime import datetime
import json

# ===== 必须导入：项目配置 + Kafka工具（真实实现，非兜底）=====
try:
    from config import config
    from utils.kafka_utils import send_weibo_data_to_kafka
except ImportError as e:
    logging.error(f"配置/ Kafka工具导入失败，程序终止：{e}")
    raise SystemExit(1)

# 初始化日志（显示毫秒，方便流式排查）
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s,%(msecs)03d - %(name)s - %(levelname)s - %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
logger = logging.getLogger(__name__)

# 兜底项目工具类（情感分析/关键词提取，无utils仍可运行）
try:
    from utils.text_utils import analyze_sentiment, get_sentiment_type, extract_keywords
except ImportError as e:
    logger.warning(f"情感分析工具导入失败，启用兜底实现：{e}")


    # 兜底情感分析（返回中性分，Spark可后续重写）
    def analyze_sentiment(content):
        return 0.5


    def get_sentiment_type(score):
        return "中性"


    def extract_keywords(content, top_k=5):
        return content[:5].split() if content else []


class WeiboHotSearchCommentCrawler:
    def __init__(self):
        self.driver = None
        self.all_comments = []  # 当前热搜评论（清洗后）
        self.all_articles = []  # 当前热搜文章（清洗后）
        self.processed_comment_ids = set()  # 评论去重
        self.processed_article_ids = set()  # 文章去重
        self.target_url = None
        self.hot_search_url = config.WEIBO_HOT_URL  # 微博热搜URL（配置化）
        self.scroll_ratio = 0.8  # 滚动比例（屏幕80%）
        self.scroll_step = None  # 动态计算滚动步长
        self.current_scroll_position = 0
        self.hot_searches = []  # 热搜列表
        self.current_hot_rank = 0  # 当前热搜排名
        self.current_hot_title = ""  # 当前热搜标题（清洗后）
        self.save_csv = config.CSV_EXPORT_ENABLED  # 使用配置文件的开关
        self.send_kafka = True  # 是否发送Kafka（核心）
        self.comment_batch_size = 500
        self.article_batch_size = 100

    def setup_driver(self):
        """初始化Edge浏览器（配置化，带用户目录免登录）"""
        edge_options = Options()
        edge_options.add_argument(f"--user-data-dir={config.EDGE_USER_DATA_DIR}")
        edge_options.add_argument('--disable-gpu')
        edge_options.add_argument('--no-sandbox')
        edge_options.add_argument('--headless=new')
        edge_options.add_argument('--disable-dev-shm-usage')
        edge_options.add_argument('--disable-blink-features=AutomationControlled')
        edge_options.add_argument('--start-maximized')
        edge_options.add_argument(
            'user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36'
        )
        try:
            service = Service(executable_path=config.EDGE_DRIVER_PATH)
            self.driver = webdriver.Edge(service=service, options=edge_options)
            logger.info("Edge浏览器启动成功（带用户目录免登录）")
            # 动态计算滚动步长（基于当前窗口高度）
            self.scroll_step = int(self.driver.execute_script("return window.innerHeight;") * self.scroll_ratio)
            logger.info(f"动态计算滚动步长：{self.scroll_step}px（屏幕高度的80%）")
            return True
        except Exception as e:
            logger.error(f"浏览器启动失败：{e}", exc_info=True)
            return False

    def get_hot_searches(self):
        """获取热搜列表并存储到实例变量中"""
        try:
            logger.info(f"访问微博热搜榜：{self.hot_search_url}")
            self.driver.get(self.hot_search_url)
            time.sleep(5)

            # 检查登录状态
            try:
                self.driver.find_element(By.CSS_SELECTOR, '.login-btn')
                logger.warning("请在浏览器中手动登录微博，登录后按回车继续...")
                input()
                self.driver.refresh()
                time.sleep(3)
            except NoSuchElementException:
                logger.info("已登录微博，继续爬取热搜")

            # 等待热搜表格加载
            WebDriverWait(self.driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, 'tbody'))
            )

            hot_searches = []
            rows = self.driver.find_elements(By.CSS_SELECTOR, 'tbody tr')

            for row in rows:
                try:
                    # 提取排名
                    rank_elem = row.find_element(By.CSS_SELECTOR, '.td-01.ranktop')
                    rank = rank_elem.text.strip()

                    # 提取标题和URL
                    title_elem = row.find_element(By.CSS_SELECTOR, '.td-02 a')
                    title = title_elem.text.strip()
                    url = title_elem.get_attribute('href')

                    # 提取热度
                    heat = 0
                    try:
                        td02_elem = row.find_element(By.CSS_SELECTOR, '.td-02')
                        heat_spans = td02_elem.find_elements(By.CSS_SELECTOR, 'span')

                        if heat_spans:
                            heat_text = heat_spans[0].text.strip()
                            if heat_text:
                                heat_text = heat_text.replace(',', '')
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
                        logger.debug(f"提取热度失败，使用默认值0: {e}")
                        heat = 0

                    if url and "javascript" not in url and title and rank.isdigit():
                        hot_searches.append({
                            'rank': int(rank),
                            'title': title,
                            'url': url,
                            'heat': heat
                        })
                        logger.info(f"提取热搜：第{rank}名 - {title} - 热度：{heat}")
                except Exception as e:
                    logger.debug(f"跳过一行热搜（提取失败）：{e}")
                    continue

            logger.info(f"共获取到{len(hot_searches)}条有效热搜")
            self.hot_searches = hot_searches
            return hot_searches

        except Exception as e:
            logger.error(f"获取热搜列表失败：{e}", exc_info=True)
            return []

    def parse_weibo_time(self, time_str):
        """解析微博杂乱时间，返回标准字符串：%Y-%m-%d %H:%M:%S"""
        if not time_str or time_str.strip() == "":
            raise ValueError(f"时间为空，无法解析: {repr(time_str)}")
        raw_clean = re.sub(r'[\n\t\r<>\"\'\\/]', '', time_str).strip()
        raw_clean = re.sub(r'\s+', ' ', raw_clean)
        time_pattern = r'(\d{2}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2})'
        match = re.search(time_pattern, raw_clean)
        if not match:
            date_pattern = r'(\d{2}-\d{1,2}-\d{1,2})'
            date_match = re.search(date_pattern, raw_clean)
            if not date_match:
                raise ValueError(f"未提取到有效时间，原始输入: {time_str}")
            date_part = date_match.group(1)
            time_part = "00:00"
        else:
            date_part, time_part = match.group(1).split(' ', 1)
        try:
            yy, m, d = date_part.split('-')
            h, mm = time_part.split(':')
            standard_time = f"20{yy.zfill(2)}-{m.zfill(2)}-{d.zfill(2)} {h.zfill(2)}:{mm.zfill(2)}"
            return standard_time
        except Exception as e:
            raise ValueError(f"解析时间失败: {e} | 原始时间: {time_str}")

    def clean_like_count(self, like_str):
        """清洗互动数（万/千转数字，提取纯数字）"""
        if not like_str or str(like_str).strip() == "":
            return 0
        like_str = str(like_str).strip()
        try:
            if '万' in like_str:
                return int(float(like_str.replace('万', '')) * 10000)
            elif '千' in like_str:
                return int(float(like_str.replace('千', '')) * 1000)
            pure_digit = ''.join([c for c in like_str if c.isdigit()])
            return int(pure_digit) if pure_digit else 0
        except Exception as e:
            logger.warning(f"清洗互动数失败，返回0：{like_str} | 错误：{e}")
            return 0

    def extract_article_info(self, article_element):
        """提取单条微博文章信息，基于成功的文章爬取代码修改"""
        try:
            # 1. 文章作者
            author = ""
            try:
                WebDriverWait(self.driver, 3).until(
                    lambda d: article_element.find_elements(By.CSS_SELECTOR, 'div._body_m3n8j_63 header.woo-box-flex')
                )

                author_selectors = [
                    'div._body_m3n8j_63 header.woo-box-flex a._default_fvu9w_3',
                    'header.woo-box-flex a._default_fvu9w_3',
                    'div._body_m3n8j_63 header.woo-box-flex a._link_ygi5b_124 span',
                    'a[usercard]',
                    '.WB_info a',
                    '.woo-user-name',
                    '.text a',
                    'header a',
                    '.name'
                ]

                for selector in author_selectors:
                    try:
                        elem = article_element.find_element(By.CSS_SELECTOR, selector)
                        if not elem:
                            continue

                        if 'a' in selector:
                            aria_label = elem.get_attribute('aria-label')
                            if aria_label and aria_label.strip() and len(aria_label.strip()) >= 2:
                                author = aria_label.strip()
                                break

                        text = elem.text.strip()
                        if text and len(text.strip()) >= 2:
                            author = text
                            break

                    except:
                        continue

                if not author:
                    logger.debug("未提取到作者")
            except Exception as e:
                logger.debug(f"提取作者时出现异常: {str(e)[:50]}，作者字段为空")

            # 2. 文章正文内容
            content = ""
            try:
                content_selectors = [
                    '._wbtext_1psp9_14',
                    '.WB_text',
                    '[node-type="feed_list_content"]',
                    '.text',
                    '.woo-feed-content',
                    '.content',
                    '.weibo-text'
                ]

                for selector in content_selectors:
                    try:
                        if article_element.find_elements(By.CSS_SELECTOR, selector):
                            content_elem = article_element.find_element(By.CSS_SELECTOR, selector)
                            content = content_elem.get_attribute('textContent') or content_elem.text.strip()
                            if content:
                                content = re.sub(r'\s+', ' ', content)
                                break
                    except:
                        continue

                if not content:
                    all_text = article_element.text.strip()
                    if all_text:
                        content = re.sub(r'\s+', ' ', all_text)

            except Exception as e:
                logger.warning(f"正文提取异常（已兜底）：{str(e)[:50]}")

            # 3. 文章发布时间
            post_time_str = ""
            time_selectors = ['.WB_from', '.woo-feed-time', '.time', '._time_1tpft_33', '.info > div:first-child']
            for selector in time_selectors:
                if article_element.find_elements(By.CSS_SELECTOR, selector):
                    post_time_str = article_element.find_element(By.CSS_SELECTOR, selector).text.strip()
                    # 去掉"来自xxx"部分
                    if '来自' in post_time_str:
                        post_time_str = post_time_str.split('来自')[0].strip()
                    if post_time_str:
                        break

            if not post_time_str:
                raise ValueError("无法提取文章发布时间，所有选择器均未匹配到时间元素")

            post_time = self.parse_weibo_time(post_time_str)

            # 4. 文章互动数据
            like_count, repost_count, comment_count = 0, 0, 0

            try:
                like_elem = article_element.find_element(By.CSS_SELECTOR, '.woo-like-count')
                like_count = self.clean_like_count(like_elem.text.strip())
            except:
                try:
                    like_elems = article_element.find_elements(By.CSS_SELECTOR, '._num_198pe_46')
                    if len(like_elems) >= 3:
                        like_count = self.clean_like_count(like_elems[2].text.strip())
                except:
                    pass

            try:
                repost_elems = article_element.find_elements(By.CSS_SELECTOR, '._num_198pe_46')
                if repost_elems:
                    repost_count = self.clean_like_count(repost_elems[0].text.strip())
            except:
                try:
                    repost_elem = article_element.find_element(By.CSS_SELECTOR, '.forward_num, .woo-forward-count')
                    repost_count = self.clean_like_count(repost_elem.text.strip())
                except:
                    pass

            try:
                comment_elems = article_element.find_elements(By.CSS_SELECTOR, '._num_198pe_46')
                if len(comment_elems) >= 2:
                    comment_count = self.clean_like_count(comment_elems[1].text.strip())
            except:
                try:
                    comment_elem = article_element.find_element(By.CSS_SELECTOR, '.comment_num, .woo-comment-count')
                    comment_count = self.clean_like_count(comment_elem.text.strip())
                except:
                    pass

            # 5. 生成文章唯一ID
            unique_key = f"{author}_{content}"
            article_id = hashlib.md5(unique_key.encode('utf-8')).hexdigest()

            # 6. 情感分析
            sentiment_score = analyze_sentiment(content) if content else 0.5
            sentiment_type = get_sentiment_type(sentiment_score)

            return {
                'article_id': article_id,
                'author': author,
                'content': content,
                'publish_time': post_time,
                'like_count': like_count,
                'repost_count': repost_count,
                'comment_count': comment_count,
                'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'hot_title': self.current_hot_title,
                'sentiment_score': sentiment_score,
                'sentiment_type': sentiment_type
            }
        except Exception as e:
            logger.warning(f"提取单篇文章信息失败：{e}")
            return None

    def crawl_visible_articles(self):
        """爬取可见文章"""
        try:
            article_selectors = [
                'article.woo-panel-main._feed_zsq3w_24',
                '.feed_list .WB_feed',
                '[node-type="feed_list_item"]',
                '.feed-item',
                '.list_box .item',
                '.woo-feed-card',
                '.WB_feed_type'
            ]

            article_elements = []
            for selector in article_selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                    if elements:
                        article_elements = elements
                        logger.info(f"当前页面找到 {len(article_elements)} 篇文章（选择器: {selector}）")
                        break
                except:
                    continue

            if not article_elements:
                logger.warning("未找到文章元素，跳过文章爬取")
                return []

            visible_articles = []
            for elem in article_elements:
                try:
                    if elem.is_displayed():
                        article = self.extract_article_info(elem)
                        if article and article['content'] and article['article_id'] not in self.processed_article_ids:
                            visible_articles.append(article)
                            self.processed_article_ids.add(article['article_id'])
                except Exception as e:
                    logger.error(f"处理文章元素时出错: {e}")
                    continue

            logger.info(f"爬取可见文章：{len(visible_articles)}篇（去重后）")
            return visible_articles
        except Exception as e:
            logger.error(f"爬取可见文章失败：{e}", exc_info=True)
            return []

    def extract_comment_info(self, comment_element):
        """
        修复后的评论提取逻辑：
        1. 优先获取原生ID（最稳健）
        2. 兜底ID生成移除时间参数，防止重复
        """
        try:
            # 1. 用户名+用户ID
            username = ""
            user_id = ""
            try:
                user_elem = comment_element.find_element(By.CSS_SELECTOR, '.name')
                username = user_elem.text.strip()
                user_href = user_elem.get_attribute('href')
                if user_href:
                    user_id_match = re.search(r'/u/(\d+)', user_href)
                    user_id = user_id_match.group(1) if user_id_match else ""
            except:
                try:
                    user_elem = comment_element.find_element(By.CSS_SELECTOR, 'a[usercard]')
                    username = user_elem.text.strip()
                    user_id = user_elem.get_attribute('usercard').strip()
                except:
                    logger.debug("未提取到用户名/用户ID")

            # 2. 评论内容
            content = ""
            try:
                content_elem = comment_element.find_element(By.CSS_SELECTOR, '.text')
                content = content_elem.text.strip()
                if ':' in content:
                    content = content.split(':', 1)[1].strip()
            except:
                try:
                    content_elem = comment_element.find_element(By.CSS_SELECTOR, 'p')
                    content = content_elem.text.strip()
                except:
                    logger.debug("未提取到评论内容")

            # 3. 发布时间（仅用于展示，不再参与ID计算）
            post_time = ""
            time_found = False
            time_selectors = [
                '.info > div:first-child',
                '.info div:first-child',
                '.WB_from',
                '.woo-box-flex.woo-box-alignCenter.woo-box-justifyBetween > div:first-child'
            ]
            for selector in time_selectors:
                try:
                    time_elem = comment_element.find_element(By.CSS_SELECTOR, selector)
                    post_time_str = time_elem.text.strip()
                    # 去掉"来自xxx"部分，只保留时间
                    if '来自' in post_time_str:
                        post_time_str = post_time_str.split('来自')[0].strip()
                    if post_time_str:
                        post_time = self.parse_weibo_time(post_time_str)
                        time_found = True
                        break
                except:
                    continue

            if not time_found:
                raise ValueError(f"无法解析评论发布时间，CSS选择器未能匹配到时间元素")

            # 4. 点赞数
            like_count = 0
            try:
                like_elem = comment_element.find_element(By.CSS_SELECTOR, '.like_num')
                like_count = self.clean_like_count(like_elem.text.strip())
            except:
                try:
                    like_elem = comment_element.find_element(By.CSS_SELECTOR, 'span.woo-like-count')
                    like_count = self.clean_like_count(like_elem.text.strip())
                except:
                    like_count = 0

            # 5. 评论地点提取
            location = "未知"
            try:
                location_selectors = ['.info', '.from', '.WB_from', '.time', '.WB_func', '.pub']
                for selector in location_selectors:
                    try:
                        elem = comment_element.find_element(By.CSS_SELECTOR, selector)
                        elem_text = elem.text.strip()
                        if "来自" in elem_text:
                            parts = elem_text.split("来自")
                            if len(parts) > 1:
                                location = parts[1].strip()
                                location = re.sub(r'[，。！？\s]+$', '', location)
                                location = re.sub(r'\d+', '', location).strip()
                                break
                    except:
                        continue
            except Exception as e:
                logger.debug(f"地点提取失败：{e}")

            # === 关键修复：优化ID生成策略 ===
            comment_id = ""

            # 策略A：尝试从DOM属性中获取微博原生ID（最稳健）
            try:
                # 微博评论通常带有 id="C_xxxxx" 或其他属性
                elem_dom_id = comment_element.get_attribute('id')
                if elem_dom_id and elem_dom_id.startswith('C_'):
                    comment_id = elem_dom_id.replace('C_', '')

                # 如果没有ID，尝试获取 mid 属性
                if not comment_id:
                    mid = comment_element.get_attribute('omid')  # 或 mid
                    if mid:
                        comment_id = mid
            except:
                pass

            # 策略B：兜底生成ID（移除时间参数，确保稳定性）
            if not comment_id:
                # 使用 user_id + content 生成哈希
                # 即使同一用户发了两次相同内容，通常被视为同一有效数据，或可接受
                unique_key = f"{user_id}_{content}"
                comment_id = hashlib.md5(unique_key.encode('utf-8')).hexdigest()

            # 6. 情感分析
            sentiment_score = analyze_sentiment(content) if content else 0.5
            sentiment_type = get_sentiment_type(sentiment_score)

            return {
                'comment_id': comment_id,
                'username': username,
                'user_id': user_id,
                'content': content,
                'publish_time': post_time,
                'location': location,
                'like_count': like_count,
                'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'hot_title': self.current_hot_title,
                'sentiment_score': sentiment_score,
                'sentiment_type': sentiment_type
            }
        except Exception as e:
            logger.warning(f"提取单条评论信息失败：{e}")
            return None

    def crawl_visible_comments(self):
        """爬取可见评论"""
        try:
            comment_selectors = [
                '.list_box .comment_item',
                'div.con1',
                '[node-type="feed_list_comment"]',
                '.comment_list .item',
                '.item1',
                '.WB_text'
            ]

            comment_elements = []
            for selector in comment_selectors:
                elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                if elements:
                    comment_elements = elements
                    logger.info(f"当前页面找到 {len(comment_elements)} 条评论（选择器: {selector}）")
                    break

            if not comment_elements:
                raise RuntimeError("未找到任何评论元素，爬取失败")

            visible_comments = []
            for elem in comment_elements:
                try:
                    if not elem.is_displayed():
                        continue
                    comment = self.extract_comment_info(elem)
                    if comment and comment['content'] and comment['comment_id'] not in self.processed_comment_ids:
                        visible_comments.append(comment)
                except Exception as e:
                    continue
            return visible_comments
        except Exception as e:
            logger.error(f"爬取可见评论失败：{e}", exc_info=True)
            return []

    def crawl_comments_with_scroll(self, max_scrolls=15):
        """滚动爬取评论"""
        self.processed_comment_ids.clear()  # 每条热搜独立去重
        self.all_comments = []

        self.current_scroll_position = self.driver.execute_script("return window.pageYOffset;")
        scroll_count = 0
        no_new_comment_count = 0
        max_no_new = 10
        logger.info("\n=== 提取初始可见评论 ===")
        initial_comments = self.crawl_visible_comments()
        if initial_comments:
            for comment in initial_comments:
                self.all_comments.append(comment)
                self.processed_comment_ids.add(comment['comment_id'])
            logger.info(f"初始评论提取完成，共 {len(self.all_comments)} 条")
        else:
            logger.warning("未提取到初始评论，尝试滚动后再提取")

        page_height = self.driver.execute_script("return document.body.scrollHeight")

        while scroll_count < max_scrolls:
            self.current_scroll_position += self.scroll_step
            logger.info(f"\n=== 第 {scroll_count + 1} 次滚动（位置: {self.current_scroll_position}px） ===")
            self.driver.execute_script(f"window.scrollTo(0, {self.current_scroll_position});")
            time.sleep(5)

            current_comments = self.crawl_visible_comments()
            new_count = 0

            if current_comments:
                for comment in current_comments:
                    if comment['comment_id'] not in self.processed_comment_ids:
                        self.all_comments.append(comment)
                        self.processed_comment_ids.add(comment['comment_id'])
                        new_count += 1
                logger.info(f"本次新增 {new_count} 条评论，累计 {len(self.all_comments)} 条")
                no_new_comment_count = 0 if new_count > 0 else no_new_comment_count + 1
            else:
                no_new_comment_count += 1
                logger.info(f"未获取到新评论，连续无新：{no_new_comment_count}/{max_no_new}")

            updated_page_height = self.driver.execute_script("return document.body.scrollHeight")
            near_bottom = (updated_page_height - self.current_scroll_position) < self.scroll_step * 1.2
            if no_new_comment_count >= max_no_new or near_bottom:
                logger.info(f"连续{max_no_new}次无新评论或已接近页面底部，停止滚动")
                break

            page_height = updated_page_height
            scroll_count += 1

        # 最终去重
        self.all_comments = [dict(t) for t in {tuple(d.items()) for d in self.all_comments}]
        logger.info(f"评论爬取完成 | 最终去重后有效评论：{len(self.all_comments)}条")
        return self.all_comments

    def access_hot_page_and_crawl_article(self, hot_search_url):
        """访问热搜页并爬取文章"""
        try:
            logger.info(f"正在访问热搜页面: {hot_search_url}")
            self.driver.get(hot_search_url)
            time.sleep(10)

            # 点击评论按钮
            comment_selector = 'a[action-type="feed_list_comment"]'
            comment_btns = self.driver.find_elements(By.CSS_SELECTOR, comment_selector)
            found_comment_btn = False
            for btn in comment_btns:
                if btn.is_displayed():
                    logger.info(f"找到可见评论按钮，点击展开评论区")
                    self.driver.execute_script("arguments[0].click();", btn)
                    time.sleep(8)
                    found_comment_btn = True
                    break
            if not found_comment_btn:
                logger.warning("未找到可见的评论按钮，跳过当前热搜")
                return None

            # 滚动到评论区
            comment_area_selectors = ['.comment_list', '[node-type="comment_list"]', '.list_box']
            comment_area = None
            for selector in comment_area_selectors:
                try:
                    comment_area = self.driver.find_element(By.CSS_SELECTOR, selector)
                    break
                except:
                    continue
            if comment_area:
                self.driver.execute_script("arguments[0].scrollIntoView({block: 'start', behavior: 'smooth'});",
                                           comment_area)
                time.sleep(3)
                logger.info("已滚动到评论区顶部")
                self.current_scroll_position = self.driver.execute_script("return window.pageYOffset;")
            else:
                logger.warning("未找到评论区元素，使用默认滚动位置")

            # 尝试进入完整评论页
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
                logger.warning(f"提取完整评论页URL失败，使用当前页: {e}")

            # 爬取文章
            self.processed_article_ids.clear()
            self.all_articles = []
            article_data = self.crawl_visible_articles()
            self.all_articles.extend(article_data)
            logger.info(f"热搜关联文章爬取完成 | 有效文章：{len(self.all_articles)}篇")

            return self.driver.current_url
        except Exception as e:
            logger.error(f"访问热搜页并展开评论区失败：{e}", exc_info=True)
            return None

    def save_data_to_csv(self):
        """保存文章/评论到本地CSV"""
        if not self.save_csv or (not self.all_articles and not self.all_comments):
            logger.warning("未开启CSV保存/无数据，跳过本地保存")
            return

        save_dir = config.CSV_EXPORT_DIR
        if not os.path.exists(save_dir):
            os.makedirs(save_dir, exist_ok=True)

        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        hot_title_clean = re.sub(r'[\\/*?:"<>|]', '', self.current_hot_title)[:10]
        file_prefix = f"weibo_rank{self.current_hot_rank}_{hot_title_clean}_{timestamp}"

        if self.all_comments:
            df_comment = pd.DataFrame(self.all_comments)
            comment_path = os.path.join(save_dir, f"{file_prefix}_comments.csv")
            df_comment.to_csv(comment_path, index=False, encoding='utf_8_sig')
            logger.info(f"评论数据已备份到CSV：{comment_path} | 条数：{len(self.all_comments)}")

        if self.all_articles:
            df_article = pd.DataFrame(self.all_articles)
            article_path = os.path.join(save_dir, f"{file_prefix}_articles.csv")
            df_article.to_csv(article_path, index=False, encoding='utf_8_sig')
            logger.info(f"文章数据已备份到CSV：{article_path} | 篇数：{len(self.all_articles)}")

    def send_data_to_kafka(self):
        """发送爬取数据到Kafka"""
        if not self.send_kafka or (not self.all_articles and not self.all_comments):
            logger.warning("未开启Kafka发送/无数据，跳过Kafka生产")
            return

        try:
            current_hot_heat = 0
            for hot_search in self.hot_searches:
                if hot_search['title'] == self.current_hot_title:
                    current_hot_heat = hot_search.get('heat', 0)
                    break
            hot_event_data = {
                'title': self.current_hot_title,
                'crawl_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                'heat': current_hot_heat,
                'hot_rank': self.current_hot_rank
            }

            if self.all_articles:
                send_weibo_data_to_kafka(config.KAFKA_ARTICLES_TOPIC, self.all_articles)
                logger.info(f"成功发送文章数据到Kafka：{len(self.all_articles)}条")

            if self.all_comments:
                send_weibo_data_to_kafka(config.KAFKA_COMMENTS_TOPIC, self.all_comments)
                logger.info(f"成功发送评论数据到Kafka：{len(self.all_comments)}条")

            send_weibo_data_to_kafka(config.KAFKA_HOT_EVENTS_TOPIC, [hot_event_data])
            logger.info(f"成功发送热点事件数据到Kafka：{hot_event_data}")

            logger.info(f"=== 第{self.current_hot_rank}条热搜数据全部发送到Kafka完成 ===")
        except Exception as e:
            logger.error(f"发送数据到Kafka失败：{e}", exc_info=True)
            raise

    def process_single_hot_search(self, hot_search, max_scrolls=15):
        """处理单条热搜流程"""
        self.current_hot_rank = hot_search['rank']
        self.current_hot_title = hot_search['title'].strip()
        logger.info(f"\n===== 开始处理第{self.current_hot_rank}条热搜：{self.current_hot_title} =====")
        try:
            detail_url = self.access_hot_page_and_crawl_article(hot_search['url'])
            if not detail_url:
                logger.error(f"第{self.current_hot_rank}条热搜：访问详情页失败，跳过")
                return

            self.crawl_comments_with_scroll(max_scrolls)
            self.save_data_to_csv()
            self.send_data_to_kafka()
        except Exception as e:
            logger.error(f"第{self.current_hot_rank}条热搜处理异常：{e}", exc_info=True)
        finally:
            self.all_articles.clear()
            self.all_comments.clear()
            self.processed_article_ids.clear()
            self.processed_comment_ids.clear()
            logger.info(f"第{self.current_hot_rank}条热搜处理完成，返回热搜榜")
            self.driver.get(self.hot_search_url)
            time.sleep(5)

    def run(self, max_scrolls=None, num_hot_searches=None, save_csv=True, send_kafka=True):
        """爬虫主运行方法"""
        self.save_csv = save_csv
        self.send_kafka = send_kafka
        max_scrolls = max_scrolls or config.CRAWL_MAX_SCROLLS
        num_hot_searches = num_hot_searches or config.CRAWL_NUM_HOT_SEARCHES

        logger.info(
            f"\n===== 微博热搜爬虫启动 | 爬取{num_hot_searches}条 | 滚动{max_scrolls}次 | CSV备份：{save_csv} | Kafka发送：{send_kafka} =====")

        if not self.setup_driver():
            logger.error("浏览器初始化失败，爬虫程序终止")
            return
        try:
            hot_searches = self.get_hot_searches()
            if not hot_searches:
                logger.error("未提取到有效热搜列表，爬虫程序终止")
                return

            num_to_crawl = min(num_hot_searches, len(hot_searches))
            logger.info(f"开始爬取前{num_to_crawl}条微博热搜（共{len(hot_searches)}条有效）")

            for i in range(num_to_crawl):
                self.process_single_hot_search(hot_searches[i], max_scrolls)
                if i < num_to_crawl - 1:
                    time.sleep(3)
        except Exception as e:
            logger.error(f"爬虫主流程异常终止：{e}", exc_info=True)
        finally:
            if self.driver:
                self.driver.quit()
                logger.info("Edge浏览器已安全关闭")
            logger.info("===== 微博热搜爬虫执行完毕 =====")


def main():
    """主函数"""
    crawler = WeiboHotSearchCommentCrawler()
    crawler.run(
        max_scrolls=getattr(config, 'CRAWL_MAX_SCROLLS', 30),
        num_hot_searches=50,
        save_csv=True,
        send_kafka=True
    )


if __name__ == "__main__":
    main()
