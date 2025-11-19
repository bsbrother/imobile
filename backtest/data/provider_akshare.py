import asyncio
import aiohttp
import pandas as pd
import random
import time
from typing import Dict, List, Optional, Union
from loguru import logger
from itertools import cycle
import json
import os

from .. import WORKING_PROXY_FILE
from ..utils.proxy_config import ProxyManager, ProxyConfig, get_proxy_manager, get_proxy_urls_sync
from ..utils.proxy_fetcher import ProxyInfo

"""
TODO: Every refresh proxies, fixed the long-term proxies at front.
http://140.238.184.182:3128
http://84.247.188.39:8888
http://47.245.117.43:80
"""

class ProxyRotatingStockDataProvider:
    """
    Enhanced stock data provider with async capabilities, fallback mechanisms
    and integrated proxy management system, automatic proxy refresh every hour.
    - https://proxymist.com/proxy-basics/free-proxy-apis-for-developers/
    - https://deepwiki.com/akfamily/akshare/7-troubleshooting

    Modern asynchronous approach using aiohttp with concurrent execution, Robustness & Error Handling.
    - Concurrent requests: Fetches multiple pages simultaneously instead of sequentially
    - Async I/O: Non-blocking network operations
    - Optimized timeouts: Fine-tuned connection and read timeouts
      - Better timeout configuration
        client_timeout = aiohttp.ClientTimeout(total=180, connect=30, sock_read=120, sock_connect=30)
      - Page limit protection
        total_pages = min(total_pages, 100)  # Prevents excessive requests
      - Graceful error handling in concurrent context

    For eastmoney site frequently access block IP, so proxy rotation, user agent rotation, and rate limiting.
    - Proxy Rotation: Automatically rotates through proxy servers
    - User Agent Rotation: Randomizes user agents to avoid detection
    - Enhanced Rate Limiting:
      - Configurable request delays
      - Request per window limits
      - Adaptive backoff strategies
    - Robust Error Handling:
      - Proxy failure detection
      - IP blocking detection (429, 403, 451 status codes)
      - Automatic proxy switching
    - Monitoring: Proxy usage statistics and health tracking
    - Fallback Mechanisms: Falls back to sync method if async fails

    Integrates with the proxy_config.py system for:
    - Automatic proxy fetching and validation
    - Intelligent proxy rotation and health monitoring
    - Enhanced rate limiting and error handling
    - Fallback mechanisms with sync/async support

    Autom refresh proxies:
    - Automatic background proxy refresh with configurable intervals
    - Health monitoring and automatic proxy rotation
    - Graceful shutdown handling
    - Enhanced statistics and monitoring
    """

    def __init__(self,
                 concurrent_limit: int = 3,  # Conservative for rate limiting
                 rate_limit_delay: float = 1.5,
                 max_retries: int = 3,
                 proxy_config: Optional[ProxyConfig] = None,
                 enable_proxy_rotation: bool = True,
                 auto_refresh_proxies: bool = True):

        self.concurrent_limit = concurrent_limit
        self.rate_limit_delay = rate_limit_delay
        self.max_retries = max_retries
        self.enable_proxy_rotation = enable_proxy_rotation
        self.auto_refresh_proxies = auto_refresh_proxies
        self.semaphore = asyncio.Semaphore(concurrent_limit)

        # Initialize proxy management system
        self.proxy_config = proxy_config or ProxyConfig(
            max_proxies=30,
            auto_refresh=auto_refresh_proxies,
            refresh_interval=3600,  # 1 hour
            validation_timeout=8,
            concurrent_validation=20,
            rate_limit_delay=rate_limit_delay,
            fallback_to_sync=True,
            proxy_file=WORKING_PROXY_FILE,
            test_on_startup=True  # Always test proxies on startup
        )

        self.proxy_manager: Optional[ProxyManager] = None
        self.current_proxy_urls: List[str] = []
        self.proxy_cycle = None
        self.failed_proxies = set()
        self.proxy_stats = {
            "requests_made": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "proxy_switches": 0,
            "last_refresh": 0,
            "refresh_count": 0,
            "auto_refresh_errors": 0
        }

        # Auto-refresh task management
        self._refresh_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()
        self._is_refreshing = False

        # User agent rotation
        self.user_agents = self._get_user_agents()

        # Rate limiting tracking
        self.last_request_time = 0
        self.request_count = 0
        self.window_start = time.time()
        self.max_requests_per_window = self.proxy_config.max_requests_per_window
        self.window_duration = self.proxy_config.window_duration

    async def initialize(self) -> bool:
        """
        Initialize the provider with enhanced proxy management:
        1. First read proxy_config and load default ./working_proxies.txt file
        2. Test if the working proxies are actually working
        3. If no working proxies, refresh proxies and load again
        4. If still no working proxies, fall back to sync method
        """
        try:
            logger.info("Initializing ProxyRotatingStockDataProvider...")

            if self.enable_proxy_rotation:
                # Initialize proxy manager (now handles loading, testing, and conditional refresh)
                self.proxy_manager = await get_proxy_manager(self.proxy_config)

                # Get working proxy count from the initialized manager
                working_proxy_count = len(self.proxy_manager.working_proxies) if self.proxy_manager else 0

                if working_proxy_count > 0:
                    self.current_proxy_urls = self.proxy_manager.get_proxy_urls()
                    self.proxy_cycle = cycle(self.current_proxy_urls)
                    # Set last_refresh timestamp to indicate proxies are fresh
                    self.proxy_stats["last_refresh"] = time.time()
                    logger.info(f"Initialized with {working_proxy_count} working proxies")

                    # Start automatic proxy refresh task
                    if self.auto_refresh_proxies:
                        self._start_auto_refresh_task()
                else:
                    logger.warning("No working proxies available after all attempts")
                    if self.proxy_config.fallback_to_sync:
                        logger.info("Will fall back to sync method when needed")
                    else:
                        logger.warning("Proxy rotation disabled due to no working proxies")
                        self.enable_proxy_rotation = False

            else:
                logger.info("Proxy rotation disabled, using direct connection")

            return True

        except Exception as e:
            logger.error(f"Failed to initialize provider: {e}")
            # Enhanced fallback mechanism
            if self.enable_proxy_rotation and self.proxy_config.fallback_to_sync:
                try:
                    logger.info("Attempting sync proxy fallback...")
                    self.current_proxy_urls = get_proxy_urls_sync(max_proxies=10)
                    if self.current_proxy_urls:
                        self.proxy_cycle = cycle(self.current_proxy_urls)
                        logger.info(f"Fallback initialization with {len(self.current_proxy_urls)} proxies")

                        # Still try to start auto-refresh even with fallback
                        if self.auto_refresh_proxies:
                            self._start_auto_refresh_task()
                        return True
                    else:
                        logger.warning("Sync fallback also found no working proxies")
                except Exception as sync_error:
                    logger.warning(f"Sync proxy fallback also failed: {sync_error}")

            # Final fallback: disable proxy rotation
            logger.info("Disabling proxy rotation, will use direct connection")
            self.enable_proxy_rotation = False
            return True  # Still return True to allow operation without proxies

    def _start_auto_refresh_task(self):
        """Start the automatic proxy refresh background task"""
        if self._refresh_task is None or self._refresh_task.done():
            self._refresh_task = asyncio.create_task(self._auto_refresh_loop())
            logger.info(f"Started automatic proxy refresh task (interval: {self.proxy_config.refresh_interval}s)")

    async def _auto_refresh_loop(self):
        """Background task that automatically refreshes proxies at set intervals"""
        try:
            while not self._shutdown_event.is_set():
                try:
                    # Wait for the refresh interval or shutdown event
                    await asyncio.wait_for(
                        self._shutdown_event.wait(),
                        timeout=self.proxy_config.refresh_interval
                    )
                    # If we get here, shutdown was requested
                    break
                except asyncio.TimeoutError:
                    # Timeout reached, time to refresh proxies
                    if not self._is_refreshing:
                        logger.info("Auto-refresh timer triggered, refreshing proxies...")
                        await self._perform_auto_refresh()
                    else:
                        logger.debug("Skipping auto-refresh - refresh already in progress")

        except asyncio.CancelledError:
            logger.info("Auto-refresh task cancelled")
        except Exception as e:
            logger.error(f"Auto-refresh loop error: {e}")
            self.proxy_stats["auto_refresh_errors"] += 1
            # Try to restart the task after a delay
            await asyncio.sleep(60)  # Wait 1 minute before restarting
            if not self._shutdown_event.is_set():
                logger.info("Restarting auto-refresh task after error")
                self._start_auto_refresh_task()

    async def _perform_auto_refresh(self):
        """Perform automatic proxy refresh with error handling"""
        if self._is_refreshing:
            return

        self._is_refreshing = True
        try:
            success = await self.refresh_proxies()
            if success:
                self.proxy_stats["refresh_count"] += 1
                logger.info(f"Auto-refresh #{self.proxy_stats['refresh_count']} completed successfully")
            else:
                logger.warning("Auto-refresh failed, will retry at next interval")
        except Exception as e:
            logger.error(f"Auto-refresh error: {e}")
            self.proxy_stats["auto_refresh_errors"] += 1
        finally:
            self._is_refreshing = False

    async def refresh_proxies(self) -> bool:
        """Refresh the proxy list using the proxy manager"""
        if not self.proxy_manager:
            return False

        try:
            logger.info("Refreshing proxy list...")
            success = await self.proxy_manager.refresh_proxies()

            if success:
                # Update current proxy URLs
                new_proxy_urls = self.proxy_manager.get_proxy_urls()

                if new_proxy_urls:
                    old_count = len(self.current_proxy_urls)
                    self.current_proxy_urls = new_proxy_urls
                    self.proxy_cycle = cycle(self.current_proxy_urls)
                    self.failed_proxies.clear()  # Reset failed proxies
                    self.proxy_stats["last_refresh"] = time.time()

                    logger.info(f"Refreshed proxies: {old_count} → {len(new_proxy_urls)} working proxies")
                    return True
                else:
                    logger.warning("No working proxies after refresh")

            return False

        except Exception as e:
            logger.error(f"Failed to refresh proxies: {e}")
            return False

    async def force_refresh_proxies(self) -> bool:
        """Manually force a proxy refresh outside of the normal schedule"""
        logger.info("Force refreshing proxies...")
        return await self._perform_auto_refresh()

    def _get_user_agents(self) -> List[str]:
        """Get list of common user agents for rotation"""
        return [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Edge/120.0.0.0",
            "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0"
        ]

    def _get_random_user_agent(self) -> str:
        """Get random user agent"""
        return random.choice(self.user_agents)

    def _get_next_proxy(self) -> Optional[str]:
        """Get next proxy using the managed proxy system"""
        if not self.enable_proxy_rotation or not self.proxy_cycle:
            return None

        # Get next proxy from rotation, excluding failed ones
        for _ in range(len(self.current_proxy_urls)):
            proxy = next(self.proxy_cycle)
            if proxy not in self.failed_proxies:
                return proxy

        # If all proxies failed, try to get best proxies from manager
        if self.proxy_manager and self.failed_proxies:
            best_proxies = self.proxy_manager.get_best_proxies(count=5)
            if best_proxies:
                logger.info("All proxies failed, switching to best available proxies")
                self.current_proxy_urls = best_proxies
                self.proxy_cycle = cycle(self.current_proxy_urls)
                self.failed_proxies.clear()
                return next(self.proxy_cycle)

        # If we still have no working proxies and enough time has passed, trigger refresh
        if self.proxy_manager and self.auto_refresh_proxies:
            current_time = time.time()
            time_since_refresh = current_time - self.proxy_stats["last_refresh"]

            # Only refresh if it's been a while AND we have failed proxies
            if (time_since_refresh > self.proxy_config.refresh_interval / 2 and  # Half the normal interval
                len(self.failed_proxies) > len(self.current_proxy_urls) / 2):  # More than half failed
                logger.info("Many proxies failed and enough time passed, triggering refresh")
                asyncio.create_task(self.refresh_proxies())

        return None

    async def _rate_limit_check(self):
        """Enhanced rate limiting with adaptive delays"""
        current_time = time.time()

        # Reset window if needed
        if current_time - self.window_start > self.window_duration:
            self.window_start = current_time
            self.request_count = 0

        # Check if we're hitting rate limits
        if self.request_count >= self.max_requests_per_window:
            sleep_time = self.window_duration - (current_time - self.window_start)
            if sleep_time > 0:
                logger.warning(f"Rate limit reached, sleeping for {sleep_time:.2f} seconds")
                await asyncio.sleep(sleep_time)
                self.window_start = time.time()
                self.request_count = 0

        # Adaptive delay based on recent failures
        base_delay = self.rate_limit_delay
        if self.proxy_stats["failed_requests"] > 5:
            # Increase delay if we're seeing many failures
            base_delay *= 1.5

        # Ensure minimum delay between requests
        time_since_last = current_time - self.last_request_time
        if time_since_last < base_delay:
            await asyncio.sleep(base_delay - time_since_last)

        self.last_request_time = time.time()
        self.request_count += 1
        self.proxy_stats["requests_made"] += 1

    async def fetch_stock_data_async(self, use_fallback: bool = False) -> pd.DataFrame:
        """
        Fetch stock data with integrated proxy management and enhanced fallback
        """
        # Initialize if not already done
        if self.proxy_manager is None and self.enable_proxy_rotation:
            init_success = await self.initialize()
            if not init_success:
                logger.warning("Initialization failed, using fallback method")
                use_fallback = True

        # Check if we have working proxies when proxy rotation is enabled
        if self.enable_proxy_rotation and not self.current_proxy_urls:
            logger.warning("No working proxies available, switching to fallback method")
            use_fallback = True

        try:
            if use_fallback:
                logger.info("Using synchronous fallback method")
                return self._fetch_stock_data_sync()
            else:
                proxy_info = f" with {len(self.current_proxy_urls)} proxies" if self.current_proxy_urls else " without proxies"
                logger.info(f"Using asynchronous method{proxy_info}")
                return await self._fetch_stock_data_async()
        except Exception as e:
            logger.warning(f"Async method failed: {e}")
            if self.proxy_config.fallback_to_sync:
                logger.info("Falling back to sync method")
                return self._fetch_stock_data_sync()
            else:
                raise

    async def _fetch_stock_data_async(self) -> pd.DataFrame:
        """Enhanced async implementation with managed proxy system"""
        url = "https://82.push2.eastmoney.com/api/qt/clist/get"
        base_params = {
            "pn": "1",
            "pz": "100",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
                     "f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152",
        }

        # Enhanced timeout configuration
        timeout = aiohttp.ClientTimeout(
            total=300,
            connect=60,
            sock_read=180,
            sock_connect=60
        )

        connector = aiohttp.TCPConnector(
            limit=self.concurrent_limit * 2,
            limit_per_host=self.concurrent_limit,
            ttl_dns_cache=300,
            use_dns_cache=True,
            ssl=False,
        )

        # Get proxy for this session
        current_proxy = self._get_next_proxy()

        session_kwargs = {
            "timeout": timeout,
            "connector": connector,
            "headers": {"User-Agent": self._get_random_user_agent()}
        }

        if current_proxy:
            session_kwargs["connector"] = aiohttp.TCPConnector(ssl=False)
            logger.info(f"Using managed proxy: {current_proxy}")

        async with aiohttp.ClientSession(**session_kwargs) as session:

            # Get first page to determine total pages
            first_page = await self._fetch_single_page_with_retry(
                session, url, {**base_params, "pn": "1"}, current_proxy
            )

            if not first_page or first_page.get("rc") != 0:
                error_msg = first_page.get("rt") if first_page else "Unknown error"
                raise Exception(f"Failed to fetch first page: {error_msg}")

            total = first_page["data"]["total"]
            page_size = int(base_params["pz"])
            total_pages = min((total + page_size - 1) // page_size, 30)  # Reduced for stability

            logger.info(f"Fetching {total_pages} pages with {total} total records")

            # Create semaphore-controlled tasks for concurrent fetching
            tasks = []
            for page in range(1, total_pages + 1):
                params = {**base_params, "pn": str(page)}
                task = self._fetch_single_page_with_semaphore(session, url, params, current_proxy)
                tasks.append(task)

            # Execute all tasks with progress tracking
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # Filter successful results
            successful_results = []
            failed_count = 0

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.warning(f"Page {i+1} failed: {result}")
                    failed_count += 1
                elif result and result.get("rc") == 0:
                    successful_results.append(result)

            # Update statistics
            self.proxy_stats["successful_requests"] += len(successful_results)
            self.proxy_stats["failed_requests"] += failed_count

            if not successful_results:
                raise Exception("No successful page results obtained")

            return self._process_async_results(successful_results)

    async def _fetch_single_page_with_semaphore(self, session, url, params, proxy):
        """Fetch single page with semaphore control and enhanced rate limiting"""
        async with self.semaphore:
            await self._rate_limit_check()
            return await self._fetch_single_page_with_retry(session, url, params, proxy)

    async def _fetch_single_page_with_retry(self, session, url, params, proxy):
        """Enhanced retry logic with proxy health monitoring"""
        current_proxy = proxy

        for attempt in range(self.max_retries):
            try:
                # Enhanced headers with randomization
                headers = {
                    "User-Agent": self._get_random_user_agent(),
                    "Referer": random.choice([
                        "https://quote.eastmoney.com/",
                        "https://data.eastmoney.com/",
                        "https://www.eastmoney.com/"
                    ]),
                    "Accept": "application/json, text/plain, */*",
                    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                    "Accept-Encoding": "gzip, deflate, br", # Remove ', br' avoid the Brotli issue.
                    "Cache-Control": "no-cache",
                    "Pragma": "no-cache"
                }

                request_kwargs = {
                    "url": url,
                    "params": params,
                    "headers": headers,
                    "ssl": False
                }

                if current_proxy:
                    request_kwargs["proxy"] = current_proxy

                async with session.get(**request_kwargs) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("rc") == 0:
                            return data
                        else:
                            logger.warning(f"API error: {data.get('rt', 'Unknown')}")
                    elif response.status == 429:
                        # Rate limited - longer backoff
                        logger.warning(f"Rate limited (429), attempt {attempt + 1}")
                        await asyncio.sleep((2 ** attempt) * 3)
                    elif response.status in [403, 451, 503]:
                        # IP blocked or service unavailable
                        logger.warning(f"Service blocked ({response.status}), attempt {attempt + 1}")
                        if current_proxy:
                            self.failed_proxies.add(current_proxy)
                            new_proxy = self._get_next_proxy()
                            if new_proxy != current_proxy:
                                current_proxy = new_proxy
                                self.proxy_stats["proxy_switches"] += 1
                                logger.info(f"Switched to new proxy: {current_proxy}")
                        await asyncio.sleep((2 ** attempt) * 4)
                    else:
                        logger.warning(f"HTTP {response.status} on attempt {attempt + 1}")

            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(f"Request error on attempt {attempt + 1}: {e}")

                # Handle proxy-specific errors
                if current_proxy and ("proxy" in str(e).lower() or "connect" in str(e).lower()):
                    self.failed_proxies.add(current_proxy)
                    new_proxy = self._get_next_proxy()
                    if new_proxy != current_proxy:
                        current_proxy = new_proxy
                        self.proxy_stats["proxy_switches"] += 1
                        logger.info(f"Switched proxy due to error: {current_proxy}")

                if attempt == self.max_retries - 1:
                    raise e

                # Exponential backoff with jitter
                backoff_time = (2 ** attempt) + random.uniform(0, 2)
                await asyncio.sleep(backoff_time)

        return None

    def _fetch_stock_data_sync(self) -> pd.DataFrame:
        """Enhanced synchronous fallback with proxy support"""
        try:
            # Try akshare first if available
            import akshare as ak
            logger.info("Using akshare fallback method")

            for attempt in range(self.max_retries):
                try:
                    df = ak.stock_zh_a_spot_em()
                    if not df.empty:
                        return self._normalize_sync_data(df)
                except Exception as e:
                    logger.warning(f"Akshare attempt {attempt + 1} failed: {e}")
                    if attempt < self.max_retries - 1:
                        time.sleep(2 ** attempt)

        except ImportError:
            logger.info("Akshare not available, using requests with proxy support")

        # Use requests with proxy support
        return self._fetch_with_requests()

    def _fetch_with_requests(self) -> pd.DataFrame:
        """Fetch using requests library with proxy support"""
        import requests

        url = "https://82.push2.eastmoney.com/api/qt/clist/get"
        params = {
            "pn": "1",
            "pz": "3000",  # More data in single request
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": "m:0 t:6,m:0 t:80,m:1 t:2,m:1 t:23,m:0 t:81 s:2048",
            "fields": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f12,f13,f14,f15,f16,f17,f18,"
                     "f20,f21,f23,f24,f25,f22,f11,f62,f128,f136,f115,f152",
        }

        current_proxy = self._get_next_proxy()

        for attempt in range(self.max_retries):
            try:
                headers = {
                    "User-Agent": self._get_random_user_agent(),
                    "Referer": "https://quote.eastmoney.com/",
                    "Accept": "application/json, text/plain, */*"
                }

                # Setup proxy for requests
                proxies = {}
                if current_proxy:
                    proxies = {
                        "http": current_proxy,
                        "https": current_proxy
                    }
                    logger.info(f"Sync request using proxy: {current_proxy}")

                time.sleep(self.rate_limit_delay)

                response = requests.get(
                    url,
                    params=params,
                    headers=headers,
                    timeout=30,
                    proxies=proxies if proxies else None
                )

                if response.status_code == 200:
                    data = response.json()
                    if data.get("rc") == 0 and data.get("data"):
                        return self._process_sync_result(data["data"]["diff"])
                elif response.status_code in [403, 429, 451]:
                    # Try next proxy
                    if current_proxy:
                        self.failed_proxies.add(current_proxy)
                        current_proxy = self._get_next_proxy()

                logger.warning(f"Sync HTTP {response.status_code} on attempt {attempt + 1}")

            except Exception as e:
                logger.warning(f"Sync request error on attempt {attempt + 1}: {e}")
                if attempt < self.max_retries - 1:
                    time.sleep(2 ** attempt)

        raise Exception("All sync attempts failed")

    def _process_async_results(self, results: List[Dict]) -> pd.DataFrame:
        """Process async results into DataFrame"""
        all_data = []
        for result in results:
            if result.get("data") and result["data"].get("diff"):
                page_data = result["data"]["diff"]
                all_data.extend(page_data)
        return self._normalize_data(all_data)

    def _process_sync_result(self, data: List[Dict]) -> pd.DataFrame:
        """Process sync result into normalized format"""
        return self._normalize_data(data)

    def _normalize_sync_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize akshare data format"""
        return df

    def _normalize_data(self, data: List[Dict]) -> pd.DataFrame:
        """Normalize data to standard format"""
        if not data:
            return pd.DataFrame()

        df = pd.DataFrame(data)

        # Enhanced column mapping
        column_map = {
            "f1": "原序号", "f2": "最新价", "f3": "涨跌幅", "f4": "涨跌额",
            "f5": "成交量", "f6": "成交额", "f7": "振幅", "f8": "换手率",
            "f9": "市盈率-动态", "f10": "量比", "f11": "5分钟涨跌", "f12": "代码",
            "f14": "名称", "f15": "最高", "f16": "最低", "f17": "今开", "f18": "昨收",
            "f20": "总市值", "f21": "流通市值", "f22": "涨速", "f23": "市净率",
            "f24": "60日涨跌幅", "f25": "年初至今涨跌幅",
        }

        df = df.rename(columns=column_map)

        desired_columns = [
            "代码", "名称", "最新价", "涨跌幅", "涨跌额", "成交量", "成交额",
            "振幅", "最高", "最低", "今开", "昨收", "量比", "换手率",
            "市盈率-动态", "市净率", "总市值", "流通市值", "涨速",
            "5分钟涨跌", "60日涨跌幅", "年初至今涨跌幅"
        ]

        available_columns = [col for col in desired_columns if col in df.columns]
        df = df[available_columns].copy()

        # Convert numeric columns
        numeric_columns = [col for col in available_columns if col not in ["代码", "名称"]]
        for col in numeric_columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

        df = df.sort_values(by="涨跌幅", ascending=False, na_position='last')
        df.reset_index(drop=True, inplace=True)
        df.insert(0, "序号", df.index + 1)

        return df

    async def add_custom_proxy(self, proxy_url: str) -> bool:
        """Add and validate a custom proxy through the proxy manager"""
        if self.proxy_manager:
            success = await self.proxy_manager.add_custom_proxy(proxy_url)
            if success:
                # Update current proxy list
                self.current_proxy_urls = self.proxy_manager.get_proxy_urls()
                self.proxy_cycle = cycle(self.current_proxy_urls)
                return True
        return False

    async def cleanup(self):
        """Cleanup resources and stop background tasks"""
        logger.info("Shutting down ProxyRotatingStockDataProvider...")

        # Signal shutdown to auto-refresh task
        self._shutdown_event.set()

        # Cancel and wait for refresh task to complete
        if self._refresh_task and not self._refresh_task.done():
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass

        logger.info("ProxyRotatingStockDataProvider cleanup completed")

    async def __aenter__(self):
        """Async context manager entry"""
        await self.initialize()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.cleanup()

    def get_comprehensive_stats(self) -> Dict[str, any]:
        """Get comprehensive statistics including proxy manager stats"""
        current_time = time.time()
        last_refresh_ago = current_time - self.proxy_stats["last_refresh"] if self.proxy_stats["last_refresh"] else 0
        next_refresh_in = self.proxy_config.refresh_interval - last_refresh_ago if last_refresh_ago < self.proxy_config.refresh_interval else 0

        stats = {
            "provider_stats": self.proxy_stats.copy(),
            "current_proxies": len(self.current_proxy_urls),
            "failed_proxies": len(self.failed_proxies),
            "proxy_rotation_enabled": self.enable_proxy_rotation,
            "auto_refresh_enabled": self.auto_refresh_proxies,
            "auto_refresh_interval": self.proxy_config.refresh_interval,
            "last_refresh_ago_seconds": int(last_refresh_ago),
            "next_refresh_in_seconds": int(max(0, next_refresh_in)),
            "refresh_task_running": self._refresh_task is not None and not self._refresh_task.done(),
            "is_refreshing": self._is_refreshing
        }

        if self.proxy_manager:
            stats["proxy_manager_stats"] = self.proxy_manager.get_stats()

        return stats

    def get_refresh_status(self) -> Dict[str, any]:
        """Get detailed refresh status information"""
        current_time = time.time()
        last_refresh = self.proxy_stats.get("last_refresh", 0)
        last_refresh_ago = current_time - last_refresh if last_refresh else 0
        next_refresh_in = self.proxy_config.refresh_interval - last_refresh_ago if last_refresh_ago < self.proxy_config.refresh_interval else 0

        return {
            "auto_refresh_enabled": self.auto_refresh_proxies,
            "refresh_interval_seconds": self.proxy_config.refresh_interval,
            "last_refresh_timestamp": last_refresh,
            "last_refresh_ago_seconds": int(last_refresh_ago),
            "next_refresh_in_seconds": int(max(0, next_refresh_in)),
            "refresh_count": self.proxy_stats.get("refresh_count", 0),
            "auto_refresh_errors": self.proxy_stats.get("auto_refresh_errors", 0),
            "refresh_task_running": self._refresh_task is not None and not self._refresh_task.done(),
            "is_currently_refreshing": self._is_refreshing
        }

# Enhanced factory function
async def create_enhanced_provider(
    proxy_config: Optional[ProxyConfig] = None,
    concurrent_limit: int = 3,
    enable_proxy_rotation: bool = True,
    auto_refresh_interval: int = 3600  # 1 hour
) -> ProxyRotatingStockDataProvider:
    """
    Factory function to create and initialize an enhanced provider with auto-refresh

    Args:
        proxy_config: Optional proxy configuration
        concurrent_limit: Number of concurrent requests
        enable_proxy_rotation: Whether to enable proxy rotation
        auto_refresh_interval: Auto-refresh interval in seconds (default: 1 hour)

    Returns:
        Initialized ProxyRotatingStockDataProvider
    """
    if proxy_config is None:
        proxy_config = ProxyConfig(
            max_proxies=30,
            auto_refresh=True,
            refresh_interval=auto_refresh_interval,
            validation_timeout=8,
            rate_limit_delay=1.5,
            fallback_to_sync=True
        )

    provider = ProxyRotatingStockDataProvider(
        concurrent_limit=concurrent_limit,
        proxy_config=proxy_config,
        enable_proxy_rotation=enable_proxy_rotation,
        auto_refresh_proxies=True
    )

    await provider.initialize()
    return provider

# Usage example with auto-refresh monitoring
async def main():
    """Test the enhanced provider with automatic proxy refresh"""

    # Create provider with 30-minute auto-refresh for testing
    proxy_config = ProxyConfig(
        max_proxies=20,
        auto_refresh=True,
        refresh_interval=1800,  # 30 minutes for testing
        validation_timeout=8,
        rate_limit_delay=1.5,
        fallback_to_sync=True
    )

    async with ProxyRotatingStockDataProvider(
        concurrent_limit=3,
        proxy_config=proxy_config,
        enable_proxy_rotation=True,
        auto_refresh_proxies=True
    ) as provider:

        logger.info("Testing enhanced provider with automatic proxy refresh...")

        # Initial data fetch
        df = await provider.fetch_stock_data_async()
        logger.info(f"Successfully retrieved {len(df)} stock records")

        # Display refresh status
        refresh_status = provider.get_refresh_status()
        logger.info(f"Refresh status: {json.dumps(refresh_status, indent=2)}")

        # Display comprehensive statistics
        stats = provider.get_comprehensive_stats()
        logger.info(f"Comprehensive stats: {json.dumps(stats, indent=2, default=str)}")

        if not df.empty:
            print("\nTop 10 stocks by price change:")
            print(df[["序号", "代码", "名称", "最新价", "涨跌幅", "涨跌额"]].head(10))

        # Demonstrate manual refresh
        #logger.info("Testing manual proxy refresh...")
        #manual_refresh_success = await provider.force_refresh_proxies()
        #logger.info(f"Manual refresh result: {manual_refresh_success}")

        return df


if __name__ == "__main__":
    asyncio.run(main())
