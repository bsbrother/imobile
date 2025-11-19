"""
Free Proxy Fetcher for Chinese A-shares Data Provider

This module fetches free proxy servers from various sources to help bypass
rate limiting and IP blocking when accessing financial data APIs like eastmoney.

Sources:
- https://github.com/proxifly/free-proxy-list
- https://vakhov.github.io/fresh-proxy-list/
- Multiple additional free proxy APIs
"""

import asyncio
import aiohttp
import requests
import json
import re
import time
from typing import List, Dict, Optional, Set
from loguru import logger
from dataclasses import dataclass
from urllib.parse import urljoin
import random

from .. import WORKING_PROXY_FILE

@dataclass
class ProxyInfo:
    """Proxy information structure"""
    host: str
    port: int
    protocol: str = "http"
    country: str = ""
    anonymity: str = ""
    https: bool = False
    last_checked: float = 0
    response_time: float = 0

    @property
    def url(self) -> str:
        """Get proxy URL"""
        return f"{self.protocol}://{self.host}:{self.port}"

    def __str__(self) -> str:
        return self.url


class ProxyFetcher:
    """
    Fetch free proxies from multiple sources and validate them

    This class specifically targets sources that provide HTTP/HTTPS proxies
    suitable for accessing Chinese financial data APIs.
    """

    def __init__(self, timeout: int = 8, max_retries: int = 3):  # Reduced timeout for faster testing
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = None

        # Test URLs for proxy validation (Chinese financial sites)
        self.test_urls = [
            "https://httpbin.org/ip",  # Most reliable test URL
            "https://www.baidu.com",   # Chinese site for connectivity test
            "https://82.push2.eastmoney.com/api/qt/clist/get?pn=1&pz=1&po=1&np=1&ut=bd1d9ddb04089700cf9c27f6f7426281&fltt=2&invt=2&fid=f12&fs=m:0+t:6&fields=f12",
        ]

    async def __aenter__(self):
        """Async context manager entry"""
        connector = aiohttp.TCPConnector(
            limit=100,  # Increased connection limit
            limit_per_host=20,
            ttl_dns_cache=300,
            use_dns_cache=True,
            ssl=False
        )

        timeout = aiohttp.ClientTimeout(
            total=self.timeout,
            connect=3,  # Faster connection timeout
            sock_read=self.timeout
        )

        self.session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout,
            headers={
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.session:
            await self.session.close()

    async def fetch_proxifly_proxies(self) -> List[ProxyInfo]:
        """
        Fetch proxies from proxifly/free-proxy-list GitHub repository

        This fetches the raw proxy list files from the GitHub repository.
        """
        proxies = []

        try:
            # GitHub raw content URLs for different proxy types
            proxy_files = [
                "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/http/data.txt",
                "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/https/data.txt",
                "https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/all/data.txt",
            ]

            for url in proxy_files:
                try:
                    logger.info(f"Fetching proxies from: {url}")

                    async with self.session.get(url) as response:
                        if response.status == 200:
                            content = await response.text()

                            # Parse proxy list (format: host:port)
                            for line in content.strip().split('\n'):
                                line = line.strip()
                                if line and ':' in line and not line.startswith('#'):
                                    try:
                                        host, port = line.split(':', 1)
                                        proxy = ProxyInfo(
                                            host=host.strip(),
                                            port=int(port.strip()),
                                            protocol="http",
                                            anonymity="unknown"
                                        )
                                        proxies.append(proxy)
                                    except ValueError:
                                        continue
                        else:
                            logger.warning(f"Failed to fetch {url}: HTTP {response.status}")

                except Exception as e:
                    logger.warning(f"Error fetching from {url}: {e}")
                    continue

                # Small delay between requests
                await asyncio.sleep(0.2)  # Reduced delay

        except Exception as e:
            logger.error(f"Error in fetch_proxifly_proxies: {e}")

        logger.info(f"Fetched {len(proxies)} proxies from proxifly")
        return proxies

    async def fetch_fresh_proxy_list(self) -> List[ProxyInfo]:
        """
        Fetch proxies from vakhov.github.io/fresh-proxy-list/

        This site provides fresh proxy lists in various formats.
        """
        proxies = []

        try:
            base_url = "https://vakhov.github.io/fresh-proxy-list/"

            # Try different proxy list endpoints
            endpoints = [
                "http.txt",
                "https.txt",
                "socks4.txt",
                "socks5.txt"
            ]

            for endpoint in endpoints:
                try:
                    url = urljoin(base_url, endpoint)
                    logger.info(f"Fetching proxies from: {url}")

                    async with self.session.get(url) as response:
                        if response.status == 200:
                            content = await response.text()

                            # Determine protocol from filename
                            protocol = endpoint.split('.')[0]
                            if protocol not in ['http', 'https', 'socks4', 'socks5']:
                                protocol = 'http'

                            # Parse proxy list
                            for line in content.strip().split('\n'):
                                line = line.strip()
                                if line and ':' in line and not line.startswith('#'):
                                    try:
                                        host, port = line.split(':', 1)
                                        proxy = ProxyInfo(
                                            host=host.strip(),
                                            port=int(port.strip()),
                                            protocol=protocol,
                                            anonymity="unknown"
                                        )
                                        proxies.append(proxy)
                                    except ValueError:
                                        continue
                        else:
                            logger.warning(f"Failed to fetch {url}: HTTP {response.status}")

                except Exception as e:
                    logger.warning(f"Error fetching from {url}: {e}")
                    continue

                # Small delay between requests
                await asyncio.sleep(0.2)

        except Exception as e:
            logger.error(f"Error in fetch_fresh_proxy_list: {e}")

        logger.info(f"Fetched {len(proxies)} proxies from fresh-proxy-list")
        return proxies

    async def fetch_additional_sources(self) -> List[ProxyInfo]:
        """
        Fetch proxies from additional free proxy sources

        This includes other reliable free proxy APIs and lists.
        """
        proxies = []

        # Expanded free proxy sources - no signup required
        sources = [
            {
                "url": "https://api.proxyscrape.com/v2/?request=get&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
                "format": "text"
            },
            {
                "url": "https://api.proxyscrape.com/v2/?request=get&protocol=https&timeout=10000&country=all&ssl=all&anonymity=all",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list-raw.txt",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-https.txt",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.txt",
                "format": "text"
            },
            {
                "url": "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc&protocols=http%2Chttps",
                "format": "json"
            }
        ]

        for source in sources:
            try:
                logger.info(f"Fetching proxies from: {source['url']}")

                async with self.session.get(source['url']) as response:
                    if response.status == 200:
                        content = await response.text()

                        if source['format'] == 'text':
                            # Parse text format (host:port per line)
                            for line in content.strip().split('\n'):
                                line = line.strip()
                                if line and ':' in line and not line.startswith('#'):
                                    try:
                                        host, port = line.split(':', 1)
                                        proxy = ProxyInfo(
                                            host=host.strip(),
                                            port=int(port.strip()),
                                            protocol="http",
                                            anonymity="unknown"
                                        )
                                        proxies.append(proxy)
                                    except ValueError:
                                        continue

                        elif source['format'] == 'json':
                            # Parse JSON format from geonode
                            try:
                                data = json.loads(content)
                                if 'data' in data:
                                    for item in data['data']:
                                        try:
                                            proxy = ProxyInfo(
                                                host=item['ip'],
                                                port=int(item['port']),
                                                protocol="http",
                                                country=item.get('country', ''),
                                                anonymity=item.get('anonymityLevel', 'unknown')
                                            )
                                            proxies.append(proxy)
                                        except (KeyError, ValueError):
                                            continue
                            except json.JSONDecodeError:
                                logger.warning(f"Failed to parse JSON from {source['url']}")
                    else:
                        logger.warning(f"Failed to fetch {source['url']}: HTTP {response.status}")

            except Exception as e:
                logger.warning(f"Error fetching from {source['url']}: {e}")
                continue

            # Small delay between requests
            await asyncio.sleep(0.2)

        logger.info(f"Fetched {len(proxies)} proxies from additional sources")
        return proxies

    async def fetch_more_proxy_sources(self) -> List[ProxyInfo]:
        """
        Fetch from even more proxy sources for better coverage
        """
        proxies = []

        # Additional sources with different APIs
        sources = [
            {
                "url": "https://www.proxy-list.download/api/v1/get?type=http",
                "format": "text"
            },
            {
                "url": "https://www.proxy-list.download/api/v1/get?type=https",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/fate0/proxylist/master/proxy.list",
                "format": "jsonlines"
            },
            {
                "url": "https://raw.githubusercontent.com/clarketm/proxy-list/master/proxy-list.txt",
                "format": "text"
            },
            {
                "url": "https://raw.githubusercontent.com/opsxcq/proxy-list/master/list.txt",
                "format": "text"
            }
        ]

        for source in sources:
            try:
                logger.info(f"Fetching proxies from: {source['url']}")

                async with self.session.get(source['url']) as response:
                    if response.status == 200:
                        content = await response.text()

                        if source['format'] == 'text':
                            for line in content.strip().split('\n'):
                                line = line.strip()
                                if line and ':' in line and not line.startswith('#'):
                                    try:
                                        host, port = line.split(':', 1)
                                        proxy = ProxyInfo(
                                            host=host.strip(),
                                            port=int(port.strip()),
                                            protocol="http",
                                            anonymity="unknown"
                                        )
                                        proxies.append(proxy)
                                    except ValueError:
                                        continue

                        elif source['format'] == 'jsonlines':
                            # Parse JSON lines format
                            for line in content.strip().split('\n'):
                                if line.strip():
                                    try:
                                        data = json.loads(line)
                                        if 'host' in data and 'port' in data:
                                            proxy = ProxyInfo(
                                                host=data['host'],
                                                port=int(data['port']),
                                                protocol=data.get('type', 'http').lower(),
                                                country=data.get('country', ''),
                                                anonymity=data.get('anonymity', 'unknown')
                                            )
                                            proxies.append(proxy)
                                    except (json.JSONDecodeError, KeyError, ValueError):
                                        continue

            except Exception as e:
                logger.warning(f"Error fetching from {source['url']}: {e}")
                continue

            await asyncio.sleep(0.2)

        logger.info(f"Fetched {len(proxies)} proxies from additional sources")
        return proxies

    async def validate_proxy(self, proxy: ProxyInfo, test_url: str = None) -> bool:
        """
        Validate a single proxy by testing connectivity

        Args:
            proxy: ProxyInfo object to test
            test_url: URL to test against (defaults to httpbin.org)

        Returns:
            bool: True if proxy is working, False otherwise
        """
        if not test_url:
            test_url = self.test_urls[0]  # Use httpbin.org as primary test

        try:
            start_time = time.time()

            # Configure proxy for aiohttp
            connector = aiohttp.TCPConnector(ssl=False)
            timeout = aiohttp.ClientTimeout(total=self.timeout)

            async with aiohttp.ClientSession(
                connector=connector,
                timeout=timeout
            ) as session:

                async with session.get(
                    test_url,
                    proxy=proxy.url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"
                    }
                ) as response:

                    response_time = time.time() - start_time

                    if response.status == 200:
                        proxy.response_time = response_time
                        proxy.last_checked = time.time()
                        logger.debug(f"✓ Proxy {proxy.url} is working (response time: {response_time:.2f}s)")
                        return True
                    else:
                        logger.debug(f"✗ Proxy {proxy.url} returned HTTP {response.status}")
                        return False

        except Exception as e:
            logger.debug(f"✗ Proxy {proxy.url} failed: {e}")
            return False

    async def validate_proxies(self, proxies: List[ProxyInfo], max_concurrent: int = 50) -> List[ProxyInfo]:
        """
        Validate multiple proxies concurrently

        Args:
            proxies: List of ProxyInfo objects to validate
            max_concurrent: Maximum number of concurrent validations

        Returns:
            List of working ProxyInfo objects
        """
        working_proxies = []

        # Create semaphore for concurrent validation
        semaphore = asyncio.Semaphore(max_concurrent)

        async def validate_with_semaphore(proxy):
            async with semaphore:
                # Try primary test URL first
                if await self.validate_proxy(proxy, self.test_urls[0]):
                    return proxy
                # If that fails, try backup URL
                await asyncio.sleep(0.1)
                if await self.validate_proxy(proxy, self.test_urls[1]):
                    return proxy
                return None

        logger.info(f"Validating {len(proxies)} proxies with {max_concurrent} concurrent connections...")

        # Create validation tasks
        tasks = [validate_with_semaphore(proxy) for proxy in proxies]

        # Execute with progress tracking
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Filter working proxies
        for result in results:
            if isinstance(result, ProxyInfo):
                working_proxies.append(result)

        logger.info(f"Found {len(working_proxies)} working proxies out of {len(proxies)} tested")
        return working_proxies

    async def fetch_all_proxies(self, validate: bool = True, max_proxies: int = 1000) -> List[ProxyInfo]:
        """
        Fetch proxies from all sources and optionally validate them

        Args:
            validate: Whether to validate proxies before returning
            max_proxies: Maximum number of proxies to return

        Returns:
            List of ProxyInfo objects
        """
        all_proxies = []

        logger.info("Starting to fetch proxies from all sources...")

        # Fetch from all sources including the new ones
        sources = [
            self.fetch_fresh_proxy_list(),    # 2
            self.fetch_additional_sources(),  # 3
            #self.fetch_proxifly_proxies(),    # nothing
            #self.fetch_more_proxy_sources(),   # nothing
        ]

        results = await asyncio.gather(*sources, return_exceptions=True)

        # Combine results
        for result in results:
            if isinstance(result, list):
                all_proxies.extend(result)
            elif isinstance(result, Exception):
                logger.warning(f"Source failed: {result}")

        # Remove duplicates
        unique_proxies = []
        seen = set()

        for proxy in all_proxies:
            proxy_key = f"{proxy.host}:{proxy.port}"
            if proxy_key not in seen:
                seen.add(proxy_key)
                unique_proxies.append(proxy)

        logger.info(f"Found {len(unique_proxies)} unique proxies from all sources")

        # Shuffle for better distribution
        random.shuffle(unique_proxies)

        # Limit the number of proxies to test
        if len(unique_proxies) > max_proxies:
            unique_proxies = unique_proxies[:max_proxies]
            logger.info(f"Limited to {max_proxies} proxies for testing")

        if validate:
            working_proxies = await self.validate_proxies(unique_proxies)
            return working_proxies
        else:
            return unique_proxies

    def save_proxies_to_file(self, proxies: List[ProxyInfo], filename: str = WORKING_PROXY_FILE):
        """Save working proxies to a file"""
        try:
            with open(filename, 'w') as f:
                f.write(f"# Working proxies - Generated at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# Total count: {len(proxies)}\n")
                f.write("# Format: protocol://host:port\n\n")

                for proxy in proxies:
                    f.write(f"{proxy.url}\n")

            logger.info(f"Saved {len(proxies)} working proxies to {filename}")

        except Exception as e:
            logger.error(f"Failed to save proxies to file: {e}")

    def load_proxies_from_file(self, filename: str = WORKING_PROXY_FILE) -> List[ProxyInfo]:
        """Load proxies from a file"""
        proxies = []

        try:
            with open(filename, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '://' in line:
                        try:
                            # Parse protocol://host:port format
                            protocol, rest = line.split('://', 1)
                            host, port = rest.split(':', 1)

                            proxy = ProxyInfo(
                                host=host,
                                port=int(port),
                                protocol=protocol
                            )
                            proxies.append(proxy)

                        except ValueError:
                            continue

            logger.info(f"Loaded {len(proxies)} proxies from {filename}")
            return proxies

        except FileNotFoundError:
            logger.warning(f"Proxy file {filename} not found")
            return []
        except Exception as e:
            logger.error(f"Failed to load proxies from file: {e}")
            return []


async def main():
    """Test the proxy fetcher"""
    async with ProxyFetcher(timeout=8, max_retries=2) as fetcher:

        # Fetch and validate proxies
        logger.info("Fetching proxies from all sources...")
        working_proxies = await fetcher.fetch_all_proxies(
            validate=True,
            max_proxies=300  # Test 300 proxies
        )

        if working_proxies:
            logger.info(f"Found {len(working_proxies)} working proxies!")

            # Sort by response time (fastest first)
            working_proxies.sort(key=lambda x: x.response_time)

            # Display top 15 working proxies
            print("\n🚀 Top 15 Working Proxies (fastest first):")
            print("-" * 60)
            for i, proxy in enumerate(working_proxies[:15]):
                print(f"{i+1:2d}. {proxy.url} (响应时间: {proxy.response_time:.2f}s)")

            # Save to file
            fetcher.save_proxies_to_file(working_proxies, WORKING_PROXY_FILE)

            # Return proxy URLs for use in other modules
            proxy_urls = [proxy.url for proxy in working_proxies]
            return proxy_urls

        else:
            logger.warning("No working proxies found!")
            return []


def get_working_proxies(validate: bool = True, max_proxies: int = 1000) -> List[str]:
    """
    Synchronous wrapper to get working proxy URLs

    Args:
        validate: Whether to validate proxies
        max_proxies: Maximum number of proxies to test

    Returns:
        List of working proxy URLs (strings)
    """
    try:
        return asyncio.run(_fetch_proxies_async(validate, max_proxies))
    except Exception as e:
        logger.error(f"Failed to fetch proxies: {e}")
        return []


async def _fetch_proxies_async(validate: bool, max_proxies: int) -> List[str]:
    """Internal async function for getting proxies"""
    async with ProxyFetcher() as fetcher:
        working_proxies = await fetcher.fetch_all_proxies(
            validate=validate,
            max_proxies=max_proxies
        )
        return [proxy.url for proxy in working_proxies]


if __name__ == "__main__":
    # Install required packages
    print("Installing required packages...")
    import subprocess
    import sys

    packages = ["aiohttp", "requests", "loguru"]
    for package in packages:
        try:
            subprocess.check_call([sys.executable, "-m", "pip", "install", package])
        except subprocess.CalledProcessError:
            print(f"Failed to install {package}")

    # Run the main function
    asyncio.run(main())
