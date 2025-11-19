"""
Proxy Configuration Management for Chinese A-shares Data Provider

This module manages proxy configuration and integration with the data providers.
"""

import os
import json
import time
from typing import List, Dict, Optional
from loguru import logger
from dataclasses import dataclass, asdict
from .proxy_fetcher import ProxyFetcher, ProxyInfo, get_working_proxies

from .. import WORKING_PROXY_FILE

@dataclass
class ProxyConfig:
    """Proxy configuration settings"""
    enabled: bool = True
    auto_refresh: bool = True
    refresh_interval: int = 3600  # 1 hour
    max_proxies: int = 1000
    validation_timeout: int = 10
    concurrent_validation: int = 20
    test_on_startup: bool = True
    save_to_file: bool = True
    proxy_file: str = WORKING_PROXY_FILE

    # Rate limiting for proxy usage
    rate_limit_delay: float = 1.0
    max_requests_per_window: int = 50
    window_duration: int = 60

    # Fallback options
    fallback_to_direct: bool = True
    fallback_to_sync: bool = True


class ProxyManager:
    """
    Proxy Manager for handling proxy lifecycle and integration

    This class manages proxy fetching, validation, rotation, and integration
    with the stock data providers for accessing Chinese financial APIs.
    """

    def __init__(self, config: ProxyConfig = None):
        self.config = config or ProxyConfig()
        self.working_proxies: List[ProxyInfo] = []
        self.last_refresh: float = 0
        self.proxy_stats = {
            "total_fetched": 0,
            "total_working": 0,
            "last_refresh": 0,
            "success_rate": 0.0
        }

    async def initialize(self) -> bool:
        """
        Initialize the proxy manager

        Returns:
            bool: True if initialization successful, False otherwise
        """
        try:
            logger.info("Initializing ProxyManager...")

            # Step 1: Try to load existing proxies from file first
            if self.config.save_to_file and os.path.exists(self.config.proxy_file):
                self._load_proxies_from_file()
                logger.info(f"Loaded {len(self.working_proxies)} proxies from file")

            # Step 2: Test existing proxies if configured and we have proxies
            if self.config.test_on_startup and self.working_proxies:
                logger.info("Testing existing proxies from file...")
                await self._test_existing_proxies()
                logger.info(f"Found {len(self.working_proxies)} working proxies after testing")

            # Step 3: Only refresh if we don't have enough working proxies (minimum 3)
            min_proxies = 3
            if len(self.working_proxies) < min_proxies:
                logger.info(f"Need more proxies (have {len(self.working_proxies)}, want at least {min_proxies})")
                logger.info("Refreshing proxies from internet sources...")
                await self.refresh_proxies()
            else:
                logger.info(f"Sufficient working proxies available ({len(self.working_proxies)} >= {min_proxies}), skipping refresh")

            logger.info(f"ProxyManager initialized with {len(self.working_proxies)} working proxies")
            return True

        except Exception as e:
            logger.error(f"Failed to initialize ProxyManager: {e}")
            return False

    def _should_refresh_proxies(self) -> bool:
        """Check if proxies should be refreshed"""
        if not self.config.auto_refresh:
            return False

        if not self.working_proxies:
            return True

        time_since_refresh = time.time() - self.last_refresh
        return time_since_refresh > self.config.refresh_interval

    async def refresh_proxies(self) -> bool:
        """
        Refresh the proxy list by fetching new proxies

        Returns:
            bool: True if refresh successful, False otherwise
        """
        try:
            logger.info("Refreshing proxy list...")

            async with ProxyFetcher(
                timeout=self.config.validation_timeout,
                max_retries=2
            ) as fetcher:

                # Fetch new proxies
                new_proxies = await fetcher.fetch_all_proxies(
                    validate=True,
                    max_proxies=self.config.max_proxies
                )

                if new_proxies:
                    self.working_proxies = new_proxies
                    self.last_refresh = time.time()

                    # Update stats
                    self.proxy_stats.update({
                        "total_working": len(new_proxies),
                        "last_refresh": self.last_refresh,
                        "success_rate": len(new_proxies) / self.config.max_proxies
                    })

                    # Save to file if configured
                    if self.config.save_to_file:
                        self._save_proxies_to_file()

                    logger.info(f"Successfully refreshed {len(new_proxies)} working proxies")
                    return True
                else:
                    logger.warning("No working proxies found during refresh")
                    return False

        except Exception as e:
            logger.error(f"Failed to refresh proxies: {e}")
            return False

    async def _test_existing_proxies(self) -> None:
        """Test existing proxies to remove non-working ones"""
        if not self.working_proxies:
            return

        logger.info(f"Testing {len(self.working_proxies)} existing proxies...")

        async with ProxyFetcher(timeout=5) as fetcher:
            working_proxies = await fetcher.validate_proxies(
                self.working_proxies,
                max_concurrent=self.config.concurrent_validation
            )

            removed_count = len(self.working_proxies) - len(working_proxies)
            self.working_proxies = working_proxies

            if removed_count > 0:
                logger.info(f"Removed {removed_count} non-working proxies")

                # Save updated list
                if self.config.save_to_file:
                    self._save_proxies_to_file()

    def get_proxy_urls(self) -> List[str]:
        """Get list of working proxy URLs"""
        return [proxy.url for proxy in self.working_proxies]

    def get_best_proxies(self, count: int = 10) -> List[str]:
        """
        Get the best performing proxies

        Args:
            count: Number of proxies to return

        Returns:
            List of best proxy URLs
        """
        # Sort by response time (lower is better)
        sorted_proxies = sorted(
            self.working_proxies,
            key=lambda p: p.response_time or 999
        )

        return [proxy.url for proxy in sorted_proxies[:count]]

    def _save_proxies_to_file(self) -> None:
        """Save working proxies to file"""
        try:
            with open(self.config.proxy_file, 'w') as f:
                f.write(f"# Working proxies - Generated at {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# Total count: {len(self.working_proxies)}\n")
                f.write("# Format: protocol://host:port\n\n")

                for proxy in self.working_proxies:
                    f.write(f"{proxy.url}\n")

            logger.debug(f"Saved {len(self.working_proxies)} proxies to {self.config.proxy_file}")

        except Exception as e:
            logger.error(f"Failed to save proxies to file: {e}")

    def _load_proxies_from_file(self) -> None:
        """Load proxies from file"""
        try:
            proxies = []

            with open(self.config.proxy_file, 'r') as f:
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

            self.working_proxies = proxies
            logger.info(f"Loaded {len(proxies)} proxies from {self.config.proxy_file}")

        except Exception as e:
            logger.warning(f"Failed to load proxies from file: {e}")

    def get_stats(self) -> Dict:
        """Get proxy manager statistics"""
        return {
            **self.proxy_stats,
            "current_working": len(self.working_proxies),
            "config": asdict(self.config),
            "last_refresh_ago": time.time() - self.last_refresh if self.last_refresh else 0
        }

    async def add_custom_proxy(self, proxy_url: str) -> bool:
        """
        Add and test a custom proxy

        Args:
            proxy_url: Proxy URL in format protocol://host:port

        Returns:
            bool: True if proxy is working and added, False otherwise
        """
        try:
            # Parse proxy URL
            protocol, rest = proxy_url.split('://', 1)
            host, port = rest.split(':', 1)

            proxy = ProxyInfo(
                host=host,
                port=int(port),
                protocol=protocol
            )

            # Test the proxy
            async with ProxyFetcher(timeout=self.config.validation_timeout) as fetcher:
                if await fetcher.validate_proxy(proxy):
                    self.working_proxies.append(proxy)
                    logger.info(f"Added custom proxy: {proxy_url}")

                    # Save to file if configured
                    if self.config.save_to_file:
                        self._save_proxies_to_file()

                    return True
                else:
                    logger.warning(f"Custom proxy failed validation: {proxy_url}")
                    return False

        except Exception as e:
            logger.error(f"Failed to add custom proxy {proxy_url}: {e}")
            return False


# Global proxy manager instance
_proxy_manager: Optional[ProxyManager] = None

async def get_proxy_manager(config: ProxyConfig = None) -> ProxyManager:
    """
    Get the global proxy manager instance

    Args:
        config: Optional proxy configuration

    Returns:
        ProxyManager instance
    """
    global _proxy_manager

    if _proxy_manager is None:
        _proxy_manager = ProxyManager(config)
        await _proxy_manager.initialize()

    return _proxy_manager


def get_proxy_urls_sync(max_proxies: int = 10) -> List[str]:
    """
    Synchronous function to get proxy URLs

    Args:
        max_proxies: Maximum number of proxy URLs to return

    Returns:
        List of proxy URLs
    """
    try:
        # Try to get from existing working proxies file
        proxy_config = ProxyConfig()
        if os.path.exists(proxy_config.proxy_file):
            manager = ProxyManager(proxy_config)
            manager._load_proxies_from_file()

            if manager.working_proxies:
                return manager.get_best_proxies(max_proxies)

        # Fallback to fetching new proxies
        return get_working_proxies(validate=True, max_proxies=max_proxies)

    except Exception as e:
        logger.error(f"Failed to get proxy URLs: {e}")
        return []
