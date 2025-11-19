"""
Trading calendar utility for proper handling of trading days vs calendar days.
Optimized for high-performance operations with advanced caching mechanisms.
"""
import bisect
from typing import List, Optional, Dict, Any, Tuple
from datetime import datetime, timedelta, date
from functools import lru_cache
from loguru import logger

from .. import data_provider, calendar
from ..utils.util import convert_trade_date
from ..data.cache import get_global_cache


def load_calendar_from_pickle() -> Optional['TradingCalendar']:
    """
    Load trading calendar data from cache.

    Returns:
        TradingCalendar instance if load successful, None otherwise
    """

    try:
        cache = get_global_cache()
        calendar_data = cache.get("trading_calendar")

        if calendar_data is None:
            logger.info("Trading calendar cache not found")
            return None

        if not data_provider:
            logger.info("Data provider is required to load trading calendar")
            return None

        # Validate data structure
        required_keys = ['trading_dates_list', 'trading_dates_set', 'cache_start_date',
                        'cache_end_date', 'cache_years']
        if not all(key in calendar_data for key in required_keys):
            logger.error("Invalid cache format - missing required keys")
            return None

        trading_calendar = TradingCalendar.__new__(TradingCalendar)
        trading_calendar.cache_years = calendar_data['cache_years']

        # Restore cached data
        trading_calendar._trading_dates_list = calendar_data['trading_dates_list']
        trading_calendar._trading_dates_set = calendar_data['trading_dates_set']
        trading_calendar._cache_start_date = calendar_data['cache_start_date']
        trading_calendar._cache_end_date = calendar_data['cache_end_date']

        # Initialize performance tracking
        trading_calendar._cache_hits = 0
        trading_calendar._cache_misses = 0
        trading_calendar._api_calls = 0

        saved_timestamp = calendar_data.get('saved_timestamp', 'unknown')
        logger.debug("Trading calendar loaded from cache")
        logger.debug(f"Loaded {len(trading_calendar._trading_dates_list)} trading days "
                   f"from {trading_calendar._cache_start_date} to {trading_calendar._cache_end_date}")
        logger.debug(f"Calendar was saved at: {saved_timestamp}")

        return trading_calendar

    except Exception as e:
        logger.error(f"Failed to load trading calendar from cache: {str(e)}")
        return None


def is_pickle_cache_fresh(max_age_days: int = 30) -> bool:
    """
    Check if cache exists and is fresh enough.

    Args:
        max_age_days: Maximum age in days for cache to be considered fresh

    Returns:
        True if cache exists and is fresh, False otherwise
    """

    try:
        cache = get_global_cache()
        # Check cache statistics to see if we have any trading calendar data
        stats = cache.get_cache_stats()

        if stats['total_entries'] == 0:
            return False

        # For now, assume cache is fresh if it exists
        # TODO: Could add more sophisticated freshness checking
        calendar_data = cache.get("trading_calendar")
        if calendar_data is None:
            return False

        # Check if cache has timestamp and age
        saved_timestamp = calendar_data.get('saved_timestamp')
        if saved_timestamp:
            try:
                saved_time = datetime.fromisoformat(saved_timestamp)
                age_days = (datetime.now() - saved_time).days
                is_fresh = age_days <= max_age_days
                logger.debug(f"Cache age: {age_days} days, fresh: {is_fresh}")
                return is_fresh
            except Exception:
                pass

        return True  # If no timestamp, assume fresh

    except Exception as e:
        logger.error(f"Failed to check cache freshness: {str(e)}")
        return False


class TradingCalendar:
    """
    High-performance utility class for managing trading calendar and calculating trading day offsets.

    Optimizations:
    - Uses sorted list + binary search for O(log n) date lookups
    - Set-based membership testing for O(1) trading day checks
    - LRU cache for frequently accessed calculations
    - Lazy loading with intelligent cache extension
    - Memory-efficient data structures
    """

    def __init__(self, cache_years: int = 5, try_pickle_first: bool = True):
        """
        Initialize trading calendar with advanced caching.

        Args:
            cache_years: Number of years to cache trading calendar data
            try_pickle_first: Whether to try loading from pickle cache first
        """
        self.cache_years = cache_years

        # Optimized data structures for fast operations
        self._trading_dates_list: List[str] = []  # Sorted list for binary search
        self._trading_dates_set: set = set()      # Set for O(1) membership testing
        self._cache_start_date: Optional[str] = None
        self._cache_end_date: Optional[str] = None

        # Performance tracking
        self._cache_hits = 0
        self._cache_misses = 0
        self._api_calls = 0

        # Try to load from cache first if requested
        if try_pickle_first and is_pickle_cache_fresh():
            logger.debug("Attempting to load trading calendar from cache")
            if self._load_from_pickle_data():
                logger.debug("Successfully loaded trading calendar from cache")
                return
            else:
                logger.debug("Failed to load from cache, falling back to API initialization")

        # Initialize cache from API
        self._initialize_cache()

    def _load_from_pickle_data(self) -> bool:
        """
        Load trading calendar data from cache into current instance.

        Returns:
            True if successful, False otherwise
        """
        try:
            cache = get_global_cache()
            calendar_data = cache.get("trading_calendar")

            if calendar_data is None:
                logger.debug("Trading calendar not found in cache")
                return False

            # Validate data structure
            required_keys = ['trading_dates_list', 'trading_dates_set', 'cache_start_date',
                            'cache_end_date', 'cache_years']
            if not all(key in calendar_data for key in required_keys):
                logger.error("Invalid cache data format - missing required keys")
                return False

            # Restore cached data
            self._trading_dates_list = calendar_data['trading_dates_list']
            self._trading_dates_set = calendar_data['trading_dates_set']
            self._cache_start_date = calendar_data['cache_start_date']
            self._cache_end_date = calendar_data['cache_end_date']

            logger.debug(f"Loaded {len(self._trading_dates_list)} trading days from cache")
            return True

        except Exception as e:
            logger.error(f"Failed to load from cache data: {str(e)}")
            return False

    def save_to_pickle(self) -> bool:
        """
        Save current trading calendar data to cache.

        Returns:
            True if successful, False otherwise
        """
        return save_calendar_to_pickle(self)

    def _initialize_cache(self):
        """Initialize trading calendar cache with optimized data structures."""
        # Calculate cache date range (cache_years before and after current date)
        current_date = datetime.now()
        start_date = current_date - timedelta(days=self.cache_years * 365)
        end_date = current_date + timedelta(days=self.cache_years * 365)

        self._cache_start_date = start_date.strftime('%Y%m%d')
        self._cache_end_date = end_date.strftime('%Y%m%d')

        # Fetch trading calendar
        trading_dates = data_provider.get_trading_calendar(
            self._cache_start_date, self._cache_end_date
        )
        self._api_calls += 1

        # Build optimized data structures
        self._update_cache_structures(trading_dates)

        logger.info(f"Trading calendar initialized with {len(self._trading_dates_list)} "
                        f"trading days from {self._cache_start_date} to {self._cache_end_date}")

        # Save to cache for future fast loading
        self.save_to_pickle()
        logger.info("Trading calendar saved to cache for future use")


    def _update_cache_structures(self, trading_dates: List[str]):
        """Update both list and set data structures for optimal performance."""
        # Ensure dates are sorted for binary search
        self._trading_dates_list = sorted(trading_dates)
        # Build set for O(1) membership testing
        self._trading_dates_set = set(trading_dates)

    @lru_cache(maxsize=1000)
    def _normalize_date(self, date_str: str) -> Tuple[str, str]:
        """Normalize date format with caching for repeated conversions."""
        if len(date_str) == 10 and '-' in date_str:
            return date_str.replace('-', ''), 'YYYY-MM-DD'
        elif len(date_str) == 8 and date_str.isdigit():
            return date_str, 'YYYYMMDD'
        else:
            raise ValueError(f"Invalid date format: {date_str}. Expected YYYY-MM-DD or YYYYMMDD")

    def _ensure_date_in_cache(self, date_str: str):
        """Optimized cache extension with minimal API calls."""
        if not self._trading_dates_list:
            self._initialize_cache()
            return

        normalized_date, _ = self._normalize_date(date_str)

        # Ensure cache bounds exist before comparison
        if self._cache_start_date is None or self._cache_end_date is None:
            self._initialize_cache()

        # Fast check using existing cache bounds
        start = self._cache_start_date
        end = self._cache_end_date
        if start is not None and end is not None and (start <= normalized_date <= end):
            self._cache_hits += 1
            return

        self._cache_misses += 1
        logger.info(f"Date {date_str} outside cache range, extending cache")
        self._extend_cache(normalized_date)

    def _extend_cache(self, target_date: str):
        """Optimized cache extension with intelligent range calculation."""
        try:
            target_dt = datetime.strptime(target_date, '%Y%m%d')

            # Ensure cache bounds are initialized and not None
            start_str = self._cache_start_date
            end_str = self._cache_end_date
            if start_str is None or end_str is None:
                self._initialize_cache()
                start_str = self._cache_start_date
                end_str = self._cache_end_date
                if start_str is None or end_str is None:
                    raise RuntimeError("Trading calendar cache bounds are not initialized")

            current_start = datetime.strptime(start_str, '%Y%m%d')
            current_end = datetime.strptime(end_str, '%Y%m%d')

            # Extend range by 2 years in the needed direction only
            if target_date < start_str:
                new_start = target_dt - timedelta(days=2*365)
                new_end = current_end
            else:
                new_start = current_start
                new_end = target_dt + timedelta(days=2*365)

            new_start_str = new_start.strftime('%Y%m%d')
            new_end_str = new_end.strftime('%Y%m%d')

            # Fetch extended calendar
            extended_dates = data_provider.get_trading_calendar(new_start_str, new_end_str)
            self._api_calls += 1

            # Update optimized data structures
            self._update_cache_structures(extended_dates)
            self._cache_start_date = new_start_str
            self._cache_end_date = new_end_str

            logger.info(f"Extended trading calendar cache to {len(self._trading_dates_list)} days "
                           f"from {new_start_str} to {new_end_str}")

        except Exception as e:
            logger.error(f"Failed to extend trading calendar cache: {str(e)}")

    def _find_closest_trading_date(self, normalized_date: str, direction: str = 'before') -> str:
        """Use binary search to find closest trading date efficiently."""
        if direction == 'before':
            # Find largest trading date <= normalized_date
            pos = bisect.bisect_right(self._trading_dates_list, normalized_date)
            if pos > 0:
                return self._trading_dates_list[pos - 1]
            else:
                raise ValueError(f"No trading date found before {normalized_date}")
        else:  # direction == 'after'
            # Find smallest trading date >= normalized_date
            pos = bisect.bisect_left(self._trading_dates_list, normalized_date)
            if pos < len(self._trading_dates_list):
                return self._trading_dates_list[pos]
            else:
                raise ValueError(f"No trading date found after {normalized_date}")

    def _format_date(self, date_str: str, return_format: str) -> str:
        """Convert date format efficiently."""
        if return_format == 'YYYY-MM-DD':
            return f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
        return date_str

    def get_trading_days_before(self, reference_date: str, trading_days: int) -> str:
        """
        Get date that is 'trading_days' trading days before reference_date.
        Optimized with binary search and efficient data structures.

        Args:
            reference_date: Reference date in YYYY-MM-DD or YYYYMMDD format
            trading_days: Number of trading days to go back

        Returns:
            Date string in same format as reference_date

        Raises:
            ValueError: If reference date is invalid or not enough trading days available
            RuntimeError: If trading calendar is not properly initialized
        """
        if not reference_date:
            raise ValueError("Reference date cannot be empty")
        if trading_days <= 0:
            raise ValueError("Trading days must be positive")
        for _ in range(trading_days):
            prev_day = self.get_previous_trading_day(reference_date)
            if not prev_day:
                raise ValueError(f"Not enough trading days before {reference_date}")
            reference_date = prev_day
        return reference_date

    def get_trading_days_after(self, reference_date: str, trading_days: int) -> str:
        """
        Get date that is 'trading_days' trading days after reference_date.
        Optimized with binary search and efficient data structures.

        Args:
            reference_date: Reference date in YYYY-MM-DD or YYYYMMDD format
            trading_days: Number of trading days to go forward

        Returns:
            Date string in same format as reference_date

        Raises:
            ValueError: If reference date is invalid or not enough trading days available
            RuntimeError: If trading calendar is not properly initialized
        """
        if not reference_date:
            raise ValueError("Reference date cannot be empty")
        if trading_days <= 0:
            raise ValueError("Trading days must be positive")
        for _ in range(trading_days):
            next_day = self.get_next_trading_day(reference_date)
            if not next_day:
                raise ValueError(f"Not enough trading days after {reference_date}")
            reference_date = next_day
        return reference_date

    def is_trading_day(self, date_str: str) -> bool:
        """
        Check if given date is a trading day using O(1) set lookup.

        Args:
            date_str: Date in YYYY-MM-DD or YYYYMMDD format

        Returns:
            True if date is a trading day
        """
        try:
            normalized_date, _ = self._normalize_date(date_str)
            self._ensure_date_in_cache(normalized_date)
            # O(1) lookup using set
            return normalized_date in self._trading_dates_set

        except Exception as e:
            logger.error(f"Failed to check if trading day: {str(e)}")
            return False

    def get_next_trading_day(self, date_str: str) -> str:
        """
        Get next trading day after given date using binary search.

        Args:
            date_str: Date in YYYY-MM-DD or YYYYMMDD format

        Returns:
            Next trading day in same format

        Raises:
            ValueError: If date is invalid or no next trading day available
            RuntimeError: If trading calendar is not properly initialized
        """
        try:
            normalized_date, return_format = self._normalize_date(date_str)
            self._ensure_date_in_cache(normalized_date)

            if not self._trading_dates_list:
                raise RuntimeError("Trading calendar not properly initialized - cache is empty")

            # Use binary search to find next trading day
            pos = bisect.bisect_right(self._trading_dates_list, normalized_date)
            if pos >= len(self._trading_dates_list):
                raise ValueError(f"No trading date found after {date_str} in trading calendar")

            next_trading_day = self._trading_dates_list[pos]
            return self._format_date(next_trading_day, return_format)

        except (ValueError, RuntimeError):
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to get next trading day after {date_str}: {str(e)}")

    def get_previous_trading_day(self, date_str: str) -> str:
        """
        Get previous trading day before given date using binary search.

        Args:
            date_str: Date in YYYY-MM-DD or YYYYMMDD format

        Returns:
            Previous trading day in same format

        Raises:
            ValueError: If date is invalid or no previous trading day available
            RuntimeError: If trading calendar is not properly initialized
        """
        try:
            normalized_date, return_format = self._normalize_date(date_str)
            self._ensure_date_in_cache(normalized_date)

            if not self._trading_dates_list:
                raise RuntimeError("Trading calendar not properly initialized - cache is empty")

            # Use binary search to find previous trading day
            pos = bisect.bisect_left(self._trading_dates_list, normalized_date)
            if pos == 0:
                raise ValueError(f"No trading date found before {date_str} in trading calendar")

            previous_trading_day = self._trading_dates_list[pos - 1]
            return self._format_date(previous_trading_day, return_format)

        except (ValueError, RuntimeError):
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to get previous trading day before {date_str}: {str(e)}")

    def count_trading_days_between(self, start_date: str, end_date: str) -> int:
        """
        Count trading days between two dates (inclusive) using binary search.

        Args:
            start_date: Start date in YYYY-MM-DD or YYYYMMDD format
            end_date: End date in YYYY-MM-DD or YYYYMMDD format

        Returns:
            Number of trading days between dates
        """
        start_date = convert_trade_date(start_date)
        end_date = convert_trade_date(end_date)
        try:
            start_normalized, _ = self._normalize_date(start_date)
            end_normalized, _ = self._normalize_date(end_date)

            self._ensure_date_in_cache(start_normalized)
            self._ensure_date_in_cache(end_normalized)

            # Use binary search to find range efficiently
            start_pos = bisect.bisect_left(self._trading_dates_list, start_normalized)
            end_pos = bisect.bisect_right(self._trading_dates_list, end_normalized)

            return end_pos - start_pos

        except Exception as e:
            logger.error(f"Failed to count trading days between dates: {str(e)}")
            return 0

    def get_trading_days_between(self, start_date: str, end_date: str) -> list[str]:
        """
        Get trading days from start_date to end_date (inclusive).
        Optimized version using binary search for range queries.

        Args:
            start_date: Start date in YYYY-MM-DD or YYYYMMDD format
            end_date: End date in YYYY-MM-DD or YYYYMMDD format

        Returns:
            List of trading day strings in the same format as input dates

        Raises:
            RuntimeError: If trading calendar is not initialized
            ValueError: If dates are invalid or start_date > end_date
        """
        if not start_date or not end_date:
            raise ValueError("Start date and end date cannot be empty")

        try:
            start_normalized, return_format = self._normalize_date(start_date)
            end_normalized, _ = self._normalize_date(end_date)

            # Validate date order
            if start_normalized > end_normalized:
                raise ValueError(f"Start date {start_date} cannot be after end date {end_date}")

            # Ensure dates are in cache
            self._ensure_date_in_cache(start_normalized)
            self._ensure_date_in_cache(end_normalized)

            if not self._trading_dates_list:
                raise RuntimeError("Trading calendar not properly initialized - cache is empty")

            # Use binary search to find range efficiently
            start_pos = bisect.bisect_left(self._trading_dates_list, start_normalized)
            end_pos = bisect.bisect_right(self._trading_dates_list, end_normalized)

            # Extract trading days in range and format them
            trading_days = []
            for i in range(start_pos, end_pos):
                date = self._trading_dates_list[i]
                formatted_date = self._format_date(date, return_format)
                trading_days.append(formatted_date)
            return trading_days
        except (ValueError, RuntimeError):
            raise
        except Exception as e:
            raise RuntimeError(f"Failed to get trading days between {start_date} and {end_date}: {str(e)}")


    def get_cache_info(self) -> Dict[str, Any]:
        """
        Get information about the trading calendar cache with performance metrics.

        Returns:
            Dictionary with cache statistics
        """
        return {
            'total_trading_days': len(self._trading_dates_list),
            'cache_start_date': self._cache_start_date,
            'cache_end_date': self._cache_end_date,
            'cache_years': self.cache_years,
            'cache_hits': self._cache_hits,
            'cache_misses': self._cache_misses,
            'api_calls': self._api_calls,
            'hit_ratio': self._cache_hits / (self._cache_hits + self._cache_misses) if (self._cache_hits + self._cache_misses) > 0 else 0
        }


def save_calendar_to_pickle(cal: Optional['TradingCalendar'] = None) -> bool:
    """Persist a TradingCalendar instance to the cache.

    Args:
        cal: TradingCalendar instance to persist.

    Returns:
        True if save successful, False otherwise
    """
    try:
        if cal is None:
            logger.error("TradingCalendar instance is None; cannot persist.")
            return False

        calendar_data = {
            'trading_dates_list': cal._trading_dates_list,
            'trading_dates_set': cal._trading_dates_set,
            'cache_start_date': cal._cache_start_date,
            'cache_end_date': cal._cache_end_date,
            'cache_years': cal.cache_years,
            'saved_timestamp': datetime.now().isoformat(),
            'version': '1.0'
        }

        cache = get_global_cache()
        # Cache for 30 days (30 * 24 * 3600 seconds)
        success = cache.set("trading_calendar", calendar_data, ttl=30*24*3600)

        if success:
            logger.info("Trading calendar saved to cache")
            logger.info(
                f"Cached {len(cal._trading_dates_list)} trading days from {cal._cache_start_date} to {cal._cache_end_date}"
            )
        else:
            logger.error("Failed to save trading calendar to cache")

        return success
    except Exception as e:
        logger.error(f"Failed to save trading calendar to cache: {str(e)}")
        return False


def initialize_trading_calendar(cache_years: int = 5, try_pickle_first: bool = True) -> Optional[TradingCalendar]:
    """
    Initialize trading calendar instance with caching support.

    This should be called at application startup.

    Args:
        cache_years: Number of years to cache
        try_pickle_first: Whether to try loading from cache first

    Returns:
        Trading calendar instance or None if initialization failed
    """
    # Try to load from cache first if requested and cache is fresh
    if try_pickle_first and is_pickle_cache_fresh():
        logger.debug("Attempting to load trading calendar from cache")
        _trading_calendar = load_calendar_from_pickle()
        if _trading_calendar:
            logger.debug("Successfully loaded trading calendar from cache")
            return _trading_calendar
        else:
            logger.info("Failed to load from cache, falling back to API initialization")

    # Initialize from API
    logger.info("Initializing trading calendar from API")
    return TradingCalendar(cache_years, try_pickle_first=False)


def get_trading_days_before(reference_date: str, trading_days: int) -> str:
    """
    Convenience function to get trading days before using calendar.

    Args:
        reference_date: Reference date
        trading_days: Number of trading days to go back

    Returns:
        Date string in same format as reference_date

    Raises:
        RuntimeError: If trading calendar is not initialized
        ValueError: If reference date is invalid or not enough trading days available
    """
    return calendar.get_trading_days_before(reference_date, trading_days)


def get_trading_days_after(reference_date: str, trading_days: int) -> str:
    """
    Convenience function to get trading days after using calendar.

    Args:
        reference_date: Reference date
        trading_days: Number of trading days to go forward

    Returns:
        Date string in same format as reference_date

    Raises:
        RuntimeError: If trading calendar is not initialized
        ValueError: If reference date is invalid or not enough trading days available
    """
    return calendar.get_trading_days_after(reference_date, trading_days)

def get_trading_days_between(start_date: str, end_date: str) -> list[str]:
    """
    Get trading days from start_date to end_date (inclusive).
    """
    return calendar.get_trading_days_between(start_date, end_date)

def count_trading_days_between(start_date: str, end_date: str) -> int:
    """
    Count trading days from start_date to end_date.
    """
    return calendar.count_trading_days_between(start_date, end_date)

def is_trading_day(date_str: str) -> bool:
    return calendar.is_trading_day(date_str)


def main():
    """Main function for testing trading calendar functionality and performance."""

    start_date = '2025-06-01'
    end_date = '2025-06-14'
    print(f'{start_date} is trading day: {calendar.is_trading_day(start_date)}')
    print(f'{start_date} previous trading day: {calendar.get_previous_trading_day(start_date)}, {get_trading_days_before(start_date, 1)}')
    print(f'{start_date} next trading day: {calendar.get_next_trading_day(start_date)}, {get_trading_days_after(start_date, 1)}')

    # Example trading days between
    days_num = count_trading_days_between(start_date, end_date)
    days = get_trading_days_between(start_date, end_date)
    print(f'\nTrading days {days_num},{len(days)} between {start_date} and {end_date}: {days}')

    exit(0)
    # Example usage of date conversion (works without token)
    print("\nDate conversion examples:")
    print(f"convert_trade_date('2016-01-01') = {convert_trade_date('2016-01-01')}")
    print(f"convert_trade_date('20160101') = {convert_trade_date('20160101')}")
    print(f"convert_trade_date('2016/01/01') = {convert_trade_date('2016/01/01')}")
    print(f"convert_trade_date(date(2016, 1, 1)) = {convert_trade_date(date(2016, 1, 1))}")
    print(f"convert_trade_date(datetime(2016, 1, 1)) = {convert_trade_date(datetime(2016, 1, 1, 12, 0, 0))}")


if __name__ == "__main__":
    main()
