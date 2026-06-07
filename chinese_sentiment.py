# -*- coding: utf-8 -*-
"""
Chinese Forum Sentiment Scraper
Scrapes Eastmoney (东方财富) and Xueqiu (雪球) for stock sentiment data.

Usage:
    from chinese_sentiment import EastmoneyScraper, XueqiuScraper

    em = EastmoneyScraper()
    posts = em.get_guba_posts("000001", date="2025-01-15", max_pages=5)

    xq = XueqiuScraper()
    posts = xq.get_stock_posts("SZ000001", date="2025-01-15", max_pages=5)
"""

import re
import json
import time
import logging
import hashlib
from datetime import datetime, timedelta
from typing import Optional
from html import unescape

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# Stock code normalization helpers
def normalize_to_eastmoney(code: str) -> str:
    """Convert stock code to Eastmoney format (e.g. 000001.SZ -> 0.000001)."""
    code = code.strip().upper()
    # Remove exchange suffix
    code = re.sub(r'\.(SZ|SH|BZ)$', '', code)
    code = re.sub(r'^(SZ|SH|BZ)', '', code)
    # Determine market: 6xxxxx = Shanghai(1), else Shenzhen(0)
    if code.startswith('6'):
        return f"1.{code}"
    return f"0.{code}"


def normalize_to_xueqiu(code: str) -> str:
    """Convert stock code to Xueqiu format (e.g. 000001.SZ -> SZ000001)."""
    code = code.strip().upper()
    code = re.sub(r'\.(SZ|SH|BZ)$', '', code)
    if code.startswith('6'):
        return f"SH{code}"
    return f"SZ{code}"


def normalize_to_cls(code: str) -> str:
    """Convert stock code to CLS (Cailianpress) format."""
    code = code.strip().upper()
    code = re.sub(r'\.(SZ|SH|BZ)$', '', code)
    if code.startswith('6'):
        return f"SH{code}"
    return f"SZ{code}"


class BaseScraper:
    """Base class for all forum scrapers."""

    def __init__(self, delay: float = 0.5, timeout: int = 15):
        self.delay = delay
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                          '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/html, */*',
            'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
            'Accept-Encoding': 'gzip, deflate',
            'Connection': 'keep-alive',
        })

    def _get(self, url: str, params: dict = None, headers: dict = None) -> requests.Response:
        """Rate-limited GET request."""
        time.sleep(self.delay)
        h = {**self.session.headers}
        if headers:
            h.update(headers)
        resp = self.session.get(url, params=params, headers=h, timeout=self.timeout)
        resp.raise_for_status()
        return resp

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        """Parse various Chinese date formats."""
        if not date_str:
            return None
        # Unix timestamp
        if date_str.isdigit():
            return datetime.fromtimestamp(int(date_str))
        # Common Chinese formats
        for fmt in ['%Y-%m-%d %H:%M:%S', '%Y-%m-%d', '%Y/%m/%d', '%Y年%m月%d日']:
            try:
                return datetime.strptime(date_str.strip(), fmt)
            except ValueError:
                continue
        return None

    @staticmethod
    def _filter_by_date(posts: list, date: str = None, days_range: int = 1) -> list:
        """Filter posts by date. If date is None, returns all."""
        if not date:
            return posts
        target = datetime.strptime(date, '%Y-%m-%d')
        start = target - timedelta(days=days_range)
        end = target + timedelta(days=days_range + 1)
        filtered = []
        for p in posts:
            post_date = p.get('_date')
            if post_date and start <= post_date < end:
                filtered.append(p)
        return filtered


class EastmoneyScraper(BaseScraper):
    """
    Scraper for Eastmoney (东方财富) forum data.
    
    Eastmoney Stock Bar (股吧) is the largest Chinese retail investor forum.
    Provides posts, comments, read counts, and timestamps.
    
    API endpoints:
    - Stock post list: https://guba.eastmoney.com/list,{code}.html
    - Reply API: https://guba.eastmoney.com/interface/GetData.aspx
    - News: https://np-listapi.eastmoney.com/comm/web/getNewsByColumns
    - Hot stocks: https://push2.eastmoney.com/api/qt/clist/get
    """

    # Known stock name mapping for common tickers
    STOCK_NAME_MAP = {
        '000001': '平安银行',
        '000002': '万科A',
        '600519': '贵州茅台',
        '601318': '中国平安',
        '000858': '五粮液',
        '002415': '海康威视',
        '300750': '宁德时代',
        '600036': '招商银行',
        '601398': '工商银行',
        '600276': '恒瑞医药',
    }

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.session.headers.update({
            'Referer': 'https://guba.eastmoney.com/',
            'Origin': 'https://guba.eastmoney.com',
        })

    def get_stock_name(self, code: str) -> str:
        """Get stock Chinese name from ticker code."""
        code_clean = re.sub(r'\.(SZ|SH|BZ)$', '', code.strip().upper())
        return self.STOCK_NAME_MAP.get(code_clean, code_clean)

    def get_guba_posts(self, code: str, date: str = None, max_pages: int = 3) -> list:
        """
        Fetch stock bar (股吧) posts for a given stock.
        
        Args:
            code: Stock code e.g. "000001.SZ" or "000001"
            date: Filter by date "YYYY-MM-DD" (None for all)
            max_pages: Number of pages to fetch
            
        Returns:
            List of post dicts with keys: title, content, author, 
            read_count, comment_count, date, url, sentiment_hint
        """
        # URL format: guba.eastmoney.com/list,000001.html (plain code, NOT 0.000001)
        code_clean = re.sub(r'\.(SZ|SH|BZ)$', '', code.strip().upper())
        
        all_posts = []
        for page in range(1, max_pages + 1):
            url = f"https://guba.eastmoney.com/list,{code_clean}_{page}.html"
            try:
                resp = self._get(url)
                posts = self._parse_guba_list(resp.text, code)
                if not posts:
                    break
                all_posts.extend(posts)
                logger.info(f"Eastmoney guba page {page}: {len(posts)} posts")
            except Exception as e:
                logger.warning(f"Eastmoney guba page {page} failed: {e}")
                break

        return self._filter_by_date(all_posts, date)

    def _parse_guba_list(self, html: str, code: str) -> list:
        """Parse Eastmoney guba post list from HTML table."""
        soup = BeautifulSoup(html, 'lxml')
        posts = []

        table = soup.find('table')
        if not table:
            logger.warning("Eastmoney guba: no table found in HTML")
            return posts

        rows = table.find_all('tr')
        for row in rows[1:]:  # skip header row
            try:
                cells = row.find_all('td')
                if len(cells) < 4:
                    continue

                read_count = int(cells[0].get_text(strip=True) or '0')
                comment_count = int(cells[1].get_text(strip=True) or '0')

                # Title: <a> tag inside 3rd cell
                title_a = cells[2].find('a')
                title = title_a.get_text(strip=True) if title_a else cells[2].get_text(strip=True)
                href = title_a.get('href', '') if title_a else ''
                url = f"https://guba.eastmoney.com{href}" if href.startswith('/') else href

                # Author
                author_el = cells[3].find('a') or cells[3]
                author = author_el.get_text(strip=True)

                # Date: last cell
                date_text = cells[4].get_text(strip=True) if len(cells) > 4 else ''
                post_date = self._parse_eastmoney_date(date_text)

                if title:
                    posts.append({
                        'title': title,
                        'url': url,
                        'author': author,
                        'read_count': read_count,
                        'comment_count': comment_count,
                        'date_raw': date_text,
                        '_date': post_date,
                        'source': 'eastmoney_guba',
                        'sentiment_hint': self._estimate_sentiment(title),
                    })
            except Exception:
                continue

        return posts

    def _parse_eastmoney_date(self, date_text: str) -> Optional[datetime]:
        """Parse Eastmoney's date format like '01-15 14:30', '昨天 09:30', etc."""
        now = datetime.now()
        date_text = date_text.strip()
        
        # "昨天" = yesterday, "今天" = today
        if '今天' in date_text:
            date_text = date_text.replace('今天', now.strftime('%m-%d'))
        elif '昨天' in date_text:
            yesterday = now - timedelta(days=1)
            date_text = date_text.replace('昨天', yesterday.strftime('%m-%d'))
        
        # Try "MM-DD HH:MM"
        try:
            dt = datetime.strptime(date_text, '%m-%d %H:%M')
            return dt.replace(year=now.year)
        except ValueError:
            pass
        
        try:
            return datetime.strptime(date_text, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            pass
        
        return None

    def get_guba_replies(self, post_id: str, max_pages: int = 3) -> list:
        """Fetch replies for a specific guba post."""
        replies = []
        for page in range(1, max_pages + 1):
            url = "https://guba.eastmoney.com/interface/GetData.aspx"
            params = {
                'path': 'reply/api/Article/ArticleReplyListPost',
                'postid': post_id,
                'ps': 20,
                'p': page,
                'sort': 1,
            }
            try:
                resp = self._get(url, params=params)
                data = resp.json()
                items = data.get('result', {}).get('reply_list', [])
                if not items:
                    break
                for item in items:
                    replies.append({
                        'content': unescape(item.get('Content', '')),
                        'author': item.get('user_nickname', ''),
                        'date_raw': item.get('reply_publish_time', ''),
                        'source': 'eastmoney_guba_reply',
                        'sentiment_hint': self._estimate_sentiment(
                            unescape(item.get('Content', ''))
                        ),
                    })
            except Exception as e:
                logger.warning(f"Eastmoney replies page {page} failed: {e}")
                break
        return replies

    def get_news(self, code: str = None, date: str = None, max_pages: int = 3) -> list:
        """
        Fetch financial news from Eastmoney.
        
        Uses two APIs:
        1. Stock-specific announcements (earnings, filings, events)
        2. General financial news filtered by stock name
        
        Args:
            code: Stock code (None for general market news)
            date: Filter by date "YYYY-MM-DD"
            max_pages: Pages to fetch
            
        Returns:
            List of news dicts
        """
        news = []
        stock_name = self.get_stock_name(code) if code else ""
        code_clean = re.sub(r'\.(SZ|SH|BZ)$', '', code.strip().upper()) if code else ""

        # 1. Stock-specific announcements (most reliable for historical data)
        if code_clean:
            for page in range(1, max_pages + 1):
                url = "https://np-anotice-stock.eastmoney.com/api/security/ann"
                params = {
                    'sr': -1,
                    'page_size': 20,
                    'page_index': page,
                    'ann_type': 'A',
                    'client_source': 'web',
                    'stock_list': code_clean,
                }
                try:
                    resp = self._get(url, params=params)
                    data = resp.json()
                    items = data.get('data', {}).get('list', [])
                    if not items:
                        break
                    for item in items:
                        title = item.get('title', '')
                        display_time = item.get('display_time', '')
                        post_date = None
                        try:
                            # Format: "2026-05-22 18:54:10:641"
                            dt_str = display_time.split(':')[0] if ':' in str(display_time) else display_time
                            post_date = datetime.strptime(dt_str[:19], '%Y-%m-%d %H:%M:%S')
                        except (ValueError, IndexError):
                            pass
                        news.append({
                            'title': title,
                            'summary': title,
                            'url': f"https://data.eastmoney.com/notices/detail/{code_clean}/{item.get('art_code', '')}.html",
                            'source': 'eastmoney_announcements',
                            'date_raw': display_time,
                            '_date': post_date,
                            'sentiment_hint': self._estimate_sentiment(title),
                        })
                except Exception as e:
                    logger.warning(f"Eastmoney announcements page {page} failed: {e}")
                    break

        # 2. General financial news (broader, may not be stock-specific)
        for page in range(1, min(max_pages, 2) + 1):
            url = "https://np-listapi.eastmoney.com/comm/web/getNewsByColumns"
            params = {
                'client': 'web',
                'biz': 'web_home_category',
                'column': 350,
                'order': 1,
                'needInteractData': 0,
                'page_index': page,
                'page_size': 20,
                'req_trace': f"news{page}",
            }
            try:
                resp = self._get(url, params=params)
                data = resp.json()
                items = data.get('data', {}).get('list', [])
                if not items:
                    break
                for item in items:
                    title = item.get('title', '') or item.get('summary', '')[:100]
                    summary = item.get('summary', '')
                    # Only include if stock name mentioned
                    if stock_name and stock_name not in title and stock_name not in summary:
                        continue
                    show_time = item.get('showTime', '')
                    post_date = None
                    try:
                        if isinstance(show_time, str) and len(show_time) >= 10:
                            post_date = datetime.strptime(show_time[:19], '%Y-%m-%d %H:%M:%S')
                    except ValueError:
                        pass
                    news.append({
                        'title': title,
                        'summary': summary,
                        'url': f"https://finance.eastmoney.com/a/{item.get('code', '')}.html",
                        'source': 'eastmoney_news',
                        'date_raw': show_time,
                        '_date': post_date,
                        'sentiment_hint': self._estimate_sentiment(title + summary),
                    })
            except Exception as e:
                logger.warning(f"Eastmoney news page {page} failed: {e}")
                break

        return self._filter_by_date(news, date)

    @staticmethod
    def _estimate_sentiment(text: str) -> str:
        """Simple keyword-based sentiment estimation for Chinese text."""
        if not text:
            return 'neutral'
        bullish = ['涨', '上涨', '涨停', '看好', '买入', '加仓', '利好', '反弹',
                    '拉升', '突破', '强势', '龙头', '黑马', '翻倍', '牛市', '牛股',
                    '推荐', '抄底', '低位', '低估', '潜力', '机会', '加仓', '满仓',
                    '发财', '赚钱', '盈利', '收益', '飙升', '暴涨', '起飞']
        bearish = ['跌', '下跌', '跌停', '看空', '卖出', '减仓', '利空', '跳水',
                    '暴跌', '套牢', '割肉', '清仓', '空仓', '崩盘', '暴雷', '暴雷',
                    '退市', '风险', '警告', '泡沫', '高估', '回避', '垃圾', '亏',
                    '亏损', '腰斩', '暴跌', '崩', '跑路']
        
        text = text.lower()
        bull_count = sum(1 for w in bullish if w in text)
        bear_count = sum(1 for w in bearish if w in text)
        
        if bull_count > bear_count + 1:
            return 'bullish'
        elif bear_count > bull_count + 1:
            return 'bearish'
        return 'neutral'


class XueqiuScraper(BaseScraper):
    """
    Scraper for Xueqiu (雪球) - Chinese investment social network.
    
    Xueqiu has richer structured data than Eastmoney including:
    - Stock posts (tweets about specific stocks)
    - User comments with like counts
    - Sentiment indicators via user following patterns
    
    Note: Xueqiu requires a valid xq_a_token cookie for API access.
    The scraper attempts to extract the token from the homepage first.
    Without a valid token, Xueqiu API calls will fail (returns HTML WAF page).
    
    For production use, obtain a token by:
    1. Logging into xueqiu.com in a browser
    2. Extracting xq_a_token from cookies
    3. Passing it as cookie='xq_a_token=...' to the constructor
    """

    def __init__(self, cookie: str = None, **kwargs):
        super().__init__(**kwargs)
        self.cookie = cookie
        self._token = None
        if cookie:
            self.session.headers['Cookie'] = cookie
            # Extract token from cookie string
            m = re.search(r'xq_a_token=([a-f0-9]+)', cookie)
            if m:
                self._token = m.group(1)
        self.session.headers.update({
            'Referer': 'https://xueqiu.com/',
            'Origin': 'https://xueqiu.com',
        })

    def _get_token(self) -> Optional[str]:
        """Try to get xq_a_token from Xueqiu homepage."""
        if self._token:
            return self._token
        try:
            resp = self._get("https://xueqiu.com/", headers={
                'Accept': 'text/html',
                'Accept-Language': 'zh-CN,zh;q=0.9',
            })
            # Token is set as a cookie on first visit
            for cookie in resp.cookies:
                if cookie.name == 'xq_a_token':
                    self._token = cookie.value
                    self.session.headers['Cookie'] = f'xq_a_token={cookie.value}'
                    return self._token
            # Also try from response body
            m = re.search(r"xq_a_token\s*=\s*['\"]([a-f0-9]+)['\"]", resp.text)
            if m:
                self._token = m.group(1)
                self.session.headers['Cookie'] = f'xq_a_token={self._token}'
                return self._token
        except Exception as e:
            logger.debug(f"Xueqiu token fetch failed: {e}")
        return None

    def get_stock_posts(self, code: str, date: str = None, max_pages: int = 3) -> list:
        """
        Fetch posts about a specific stock from Xueqiu timeline.
        
        Args:
            code: Stock code e.g. "000001.SZ" or "SZ000001"
            date: Filter by date "YYYY-MM-DD"
            max_pages: Pages to fetch
            
        Returns:
            List of post dicts with keys: title/content, author, date, 
            like_count, reply_count, sentiment_hint
        """
        xq_code = normalize_to_xueqiu(code)
        all_posts = []

        # Try to get token first
        self._get_token()

        for page in range(1, max_pages + 1):
            url = "https://xueqiu.com/statuses/original/timeline.json"
            params = {
                'user_id': 0,
                'symbol_id': xq_code,
                'page': page,
            }
            try:
                resp = self._get(url, params=params)
                # Check if we got JSON or WAF HTML
                content_type = resp.headers.get('content-type', '')
                if 'json' not in content_type and 'text/html' in content_type:
                    logger.warning("Xueqiu returned HTML (WAF blocked), skipping")
                    break
                data = resp.json()
                items = data.get('list', [])
                if not items:
                    break
                for item in items:
                    post = self._parse_xueqiu_post(item)
                    if post:
                        all_posts.append(post)
                logger.info(f"Xueqiu timeline page {page}: {len(items)} posts")
            except Exception as e:
                logger.warning(f"Xueqiu timeline page {page} failed: {e}")
                break
        
        return self._filter_by_date(all_posts, date)

    def search_posts(self, query: str, date: str = None, max_pages: int = 3) -> list:
        """
        Search Xueqiu posts by keyword.
        
        Args:
            query: Search query (stock name/code + optional keywords)
            date: Filter by date "YYYY-MM-DD"
            max_pages: Pages to fetch
        """
        all_posts = []
        for page in range(1, max_pages + 1):
            url = "https://xueqiu.com/statuses/search.json"
            params = {
                'sort': 'time',
                'source': 'all',
                'q': query,
                'count': 20,
                'page': page,
            }
            try:
                resp = self._get(url, params=params)
                data = resp.json()
                items = data.get('list', [])
                if not items:
                    break
                for item in items:
                    post = self._parse_xueqiu_post(item)
                    if post:
                        all_posts.append(post)
            except Exception as e:
                logger.warning(f"Xueqiu search page {page} failed: {e}")
                break
        
        return self._filter_by_date(all_posts, date)

    def _parse_xueqiu_post(self, item: dict) -> Optional[dict]:
        """Parse a single Xueqiu post from JSON API response."""
        try:
            title = item.get('title', '') or item.get('description', '')
            if not title:
                return None
            
            retweet_count = item.get('retweet_count', 0)
            reply_count = item.get('reply_count', 0)
            like_count = item.get('like_count', 0)
            # engagement score
            engagement = retweet_count + reply_count + like_count
            
            # Parse timestamp
            created_at = item.get('created_at', 0)
            post_date = None
            if created_at:
                try:
                    post_date = datetime.fromtimestamp(created_at / 1000)
                except (ValueError, OSError):
                    pass
            
            user = item.get('user', {})
            author = user.get('screen_name', item.get('user', {}).get('name', 'unknown'))
            
            text = item.get('text', item.get('description', ''))
            # Strip HTML tags
            text = re.sub(r'<[^>]+>', '', text)
            
            return {
                'title': title[:200],
                'content': text,
                'author': author,
                'url': f"https://xueqiu.com{item.get('target', '')}",
                'date_raw': str(created_at),
                '_date': post_date,
                'source': 'xueqiu',
                'like_count': like_count,
                'reply_count': reply_count,
                'retweet_count': retweet_count,
                'engagement': engagement,
                'sentiment_hint': EastmoneyScraper._estimate_sentiment(title + text),
            }
        except Exception:
            return None

    def _scrape_xueqiu_html(self, xq_code: str, page: int = 1) -> list:
        """Fallback: scrape Xueqiu stock page HTML."""
        url = f"https://xueqiu.com/S/{xq_code}"
        posts = []
        try:
            resp = self._get(url)
            soup = BeautifulSoup(resp.text, 'lxml')
            # Xueqiu posts in HTML - usually rendered by JS, limited in static HTML
            items = soup.select('.timeline__item') or soup.select('[class*="status-item"]')
            for item in items:
                title_el = item.select_one('.timeline__item-title') or item.select_one('a')
                if not title_el:
                    continue
                posts.append({
                    'title': title_el.get_text(strip=True),
                    'url': title_el.get('href', ''),
                    'source': 'xueqiu_html',
                    'sentiment_hint': EastmoneyScraper._estimate_sentiment(
                        title_el.get_text(strip=True)
                    ),
                })
        except Exception:
            pass
        return posts

    def get_hot_stocks(self, date: str = None) -> list:
        """
        Fetch hot/trending stocks from Xueqiu.
        
        Returns:
            List of dicts with stock code, name, and heat score
        """
        url = "https://xueqiu.com/stock/hotstock.json"
        params = {
            'size': 20,
            '_type': 10,
            'type': 10,
        }
        try:
            resp = self._get(url, params=params)
            data = resp.json()
            stocks = []
            for item in data.get('items', []):
                stocks.append({
                    'code': item.get('code', ''),
                    'name': item.get('name', ''),
                    'value': item.get('value', 0),
                    'increment': item.get('increment', 0),
                    'source': 'xueqiu_hot',
                })
            return stocks
        except Exception as e:
            logger.warning(f"Xueqiu hot stocks failed: {e}")
            return []


class CLSNewsScraper(BaseScraper):
    """
    Scraper for CLS (财联社) - Chinese financial wire service.
    
    NOTE: CLS has implemented strict WAF protection and their API endpoints
    frequently change. The scraper attempts multiple known endpoints but may
    not work without proper authentication.
    
    For reliable CLS data, consider:
    1. Using their official API with a paid subscription
    2. Scrolling their website with a headless browser
    3. Using the RSS feed at https://www.cls.cn/rss
    """

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.session.headers.update({
            'Referer': 'https://www.cls.cn/',
            'Origin': 'https://www.cls.cn',
        })

    def search_news(self, query: str, date: str = None, max_pages: int = 3) -> list:
        """
        Search CLS news by keyword.
        
        NOTE: CLS API is heavily protected. This method attempts multiple
        endpoints but may return empty results.
        """
        news = []
        
        # Try the telegraph list API (may require auth)
        for page in range(max_pages):
            url = "https://www.cls.cn/api/telegraph/v3/list"
            params = {
                'app': 'CailianpressWeb',
                'os': 'web',
                'sv': '8.8.6',
                'sign': '0ea6d',
                'rn': 20,
                'last_time': page * 20,
            }
            try:
                resp = self._get(url, params=params)
                if resp.status_code != 200:
                    break
                data = resp.json()
                items = data.get('data', {}).get('roll_data', [])
                if not items:
                    break
                for item in items:
                    ts = item.get('ctime', 0)
                    post_date = None
                    if ts:
                        try:
                            post_date = datetime.fromtimestamp(ts)
                        except (ValueError, OSError):
                            pass
                    title = item.get('title', '')
                    news.append({
                        'title': title,
                        'content': item.get('content', ''),
                        'url': f"https://www.cls.cn/detail/{item.get('id', '')}",
                        'date_raw': str(ts),
                        '_date': post_date,
                        'source': 'cls_news',
                        'sentiment_hint': EastmoneyScraper._estimate_sentiment(title),
                    })
            except Exception as e:
                logger.debug(f"CLS news page {page} failed: {e}")
                break
        
        return self._filter_by_date(news, date)


# Convenience functions for SearXNG integration
def search_stock_sentiment(code: str, date: str = None, max_pages: int = 3) -> dict:
    """
    Comprehensive stock sentiment search across all Chinese sources.
    
    Args:
        code: Stock code e.g. "000001.SZ"
        date: Filter date "YYYY-MM-DD" (None for recent)
        max_pages: Pages per source
        
    Returns:
        Dict with keys: eastmoney_guba, eastmoney_news, xueqiu_posts, cls_news
    """
    result = {
        'code': code,
        'date': date,
        'eastmoney_guba': [],
        'eastmoney_news': [],
        'xueqiu_posts': [],
        'cls_news': [],
    }
    
    # Eastmoney
    em = EastmoneyScraper()
    result['eastmoney_guba'] = em.get_guba_posts(code, date=date, max_pages=max_pages)
    result['eastmoney_news'] = em.get_news(code, date=date, max_pages=max_pages)
    
    # Xueqiu
    xq = XueqiuScraper()
    result['xueqiu_posts'] = xq.get_stock_posts(code, date=date, max_pages=max_pages)
    
    # CLS
    cls = CLSNewsScraper()
    stock_name = em.get_stock_name(code)
    xq_code = normalize_to_cls(code)
    result['cls_news'] = cls.search_news(
        stock_name or code, date=date, max_pages=max_pages
    )
    
    # Summary
    total_posts = sum(len(v) for v in [result['eastmoney_guba'], 
                                        result['eastmoney_news'],
                                        result['xueqiu_posts'], 
                                        result['cls_news']])
    
    sentiments = []
    for source in ['eastmoney_guba', 'eastmoney_news', 'xueqiu_posts', 'cls_news']:
        for post in result[source]:
            s = post.get('sentiment_hint', 'neutral')
            if s != 'neutral':
                sentiments.append(s)
    
    bullish = sentiments.count('bullish')
    bearish = sentiments.count('bearish')
    total_scored = bullish + bearish
    
    result['summary'] = {
        'total_posts': total_posts,
        'sources_queried': 4,
        'bullish_count': bullish,
        'bearish_count': bearish,
        'overall_sentiment': (
            'bullish' if bullish > bearish * 1.5
            else 'bearish' if bearish > bullish * 1.5
            else 'neutral'
        ),
        'sentiment_ratio': (
            f"{bullish}:{bearish}" if total_scored > 0 else "0:0"
        ),
    }
    
    return result


if __name__ == "__main__":
    import sys
    
    code = sys.argv[1] if len(sys.argv) > 1 else "000001.SZ"
    date = sys.argv[2] if len(sys.argv) > 2 else None
    
    print(f"=== Sentiment scan for {code}" + (f" on {date}" if date else " (all dates)") + " ===\n")
    result = search_stock_sentiment(code, date=date, max_pages=2)
    
    print(f"Total posts found: {result['summary']['total_posts']}")
    print(f"Overall sentiment: {result['summary']['overall_sentiment']}")
    print(f"Sentiment ratio: {result['summary']['sentiment_ratio']}")
    
    for source in ['eastmoney_guba', 'eastmoney_news', 'xueqiu_posts', 'cls_news']:
        items = result[source]
        if items:
            print(f"\n--- {source} ({len(items)} items) ---")
            for i, item in enumerate(items[:5], 1):
                print(f"  {i}. [{item.get('sentiment_hint', '?')}] {item.get('title', item.get('content', ''))[:80]}")
                if item.get('author'):
                    print(f"     by {item['author']}")
