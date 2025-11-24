"""
SQLite-based caching system optimized for financial market data with granular storage.
"""

from loguru import logger
import sqlite3
import time
import pickle
import fnmatch
from typing import Optional, Dict, Any, List, Tuple
import pandas as pd
from datetime import datetime
from tqdm import tqdm

from ..utils.exceptions import DataProviderError
from ..utils.util import convert_trade_date, dfs_concat

# Cache saved in db, others like trading_calendar, base_info, benchmark use pkl cache at data_cache/.
SUPPORTED_DATA_TYPES = ['ohlcv_data', 'fundamental_data', 'stock_data', 'index_data']

class SQLiteDataCache:
    """SQLite-based caching mechanism for market data with granular daily storage."""

    def __init__(self, db_path: str):
        """
        Initialize SQLite data cache.

        Args:
            db_path: Path to SQLite database file
        """
        self.db_path = db_path
        self._init_database()

    def _init_database(self):
        """Initialize SQLite database with required tables."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Create single granular cache table for daily financial data
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS daily_data (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        data_type TEXT NOT NULL,
                        symbol TEXT NOT NULL,
                        trade_date TEXT NOT NULL,
                        data BLOB NOT NULL,
                        created_at REAL NOT NULL,
                        updated_at REAL NOT NULL,
                        UNIQUE(data_type, symbol, trade_date)
                    )
                ''')

                # Create indexes for faster lookups
                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_daily_symbol_date
                    ON daily_data(symbol, trade_date)
                ''')

                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_daily_type_symbol
                    ON daily_data(data_type, symbol)
                ''')

                cursor.execute('''
                    CREATE INDEX IF NOT EXISTS idx_daily_type_symbol_date
                    ON daily_data(data_type, symbol, trade_date)
                ''')

                conn.commit()
                logger.debug(f"SQLite cache database initialized: {self.db_path}")

        except Exception as e:
            raise DataProviderError(f"Failed to initialize SQLite cache database: {str(e)}")

    def _parse_cache_key(self, key: str) -> Tuple[str, str, str, str]:
        """Parse cache key to extract components.

        Returns:
            Tuple of (data_type, symbol, start_date, end_date)
        """
        parts = key.split('_')
        if len(parts) >= 4:
            data_type = '_'.join(parts[:-3])  # e.g., 'ohlcv_data', 'fundamental_data', 'stock_data', 'index_data'
            symbol = parts[-3]
            start_date = parts[-2]
            end_date = parts[-1]
            return data_type, symbol, start_date, end_date
        return key, '', '', ''

    def _is_single_day_request(self, start_date: str, end_date: str) -> bool:
        """Check if this is a single day request."""
        return start_date == end_date

    def remove(self, key: str) -> bool:
        """Remove cached data by key."""
        try:
            data_type, symbol, start_date, end_date = self._parse_cache_key(key)

            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                if self._is_single_day_request(start_date, end_date):
                    cursor.execute(
                        """
                        DELETE FROM daily_data
                        WHERE data_type = ? AND symbol = ? AND trade_date = ?
                        """,
                        (data_type, symbol, start_date)
                    )
                else:
                    cursor.execute(
                        """
                        DELETE FROM daily_data
                        WHERE data_type = ? AND symbol = ? AND trade_date BETWEEN ? AND ?
                        """,
                        (data_type, symbol, start_date, end_date)
                    )

                deleted_count = cursor.rowcount
                conn.commit()

            logger.debug(f"Removed {deleted_count} cache entries for key: {key}")
            return deleted_count > 0

        except Exception as e:
            logger.error(f"Failed to remove cache for key {key}: {str(e)}")
            return False

    def get(self, key: str) -> Optional[pd.DataFrame]:
        """Get cached data by key."""
        from ..utils.trading_calendar import get_trading_days_between
        try:
            data_type, symbol, start_date, end_date = self._parse_cache_key(key)

            if not symbol or data_type not in SUPPORTED_DATA_TYPES:
                logger.debug(f"Cache miss for unsupported data type: {key}")
                return None

            # Single day request
            if self._is_single_day_request(start_date, end_date):
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()
                    cursor.execute(
                        """
                        SELECT data FROM daily_data
                        WHERE data_type = ? AND symbol = ? AND trade_date = ?
                        """,
                        (data_type, symbol, start_date)
                    )
                    result = cursor.fetchone()

                    if result:
                        data_blob = result[0]
                        df = pickle.loads(data_blob)
                        logger.debug(f"Daily cache hit: {key}")
                        return df

            # Multi-day request - build from individual daily records
            if start_date and end_date:
                daily_dfs = []
                trade_dates = get_trading_days_between(start_date, end_date)

                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()

                    for trade_date in trade_dates:
                        cursor.execute(
                            """
                            SELECT data FROM daily_data
                            WHERE data_type = ? AND symbol = ? AND trade_date = ?
                            """,
                            (data_type, symbol, trade_date)
                        )
                        result = cursor.fetchone()

                        if result:
                            data_blob = result[0]
                            df = pickle.loads(data_blob)
                            daily_dfs.append(df)

                # Return combined data if we have records
                if daily_dfs:
                    combined_df = dfs_concat(daily_dfs, ignore_index=True)
                    combined_df = combined_df.sort_values('trade_date').reset_index(drop=True)
                    logger.debug(f"Built from daily cache: {key} ({len(daily_dfs)} days)")
                    return combined_df

            logger.debug(f"Cache miss: {key}")
            return None

        except Exception as e:
            logger.warning(f"Failed to retrieve cache for key {key}: {str(e)}")
            return None

    def set(self, key: str, data: pd.DataFrame) -> bool:
        """Set cached data with upsert behavior."""
        try:
            if data is None or data.empty:
                logger.debug(f"Attempted to cache empty data for key: {key}")
                return False

            current_time = time.time()

            data_type, symbol, start_date, end_date = self._parse_cache_key(key)

            # Only handle financial data types with symbols and trade_date column
            if (symbol and data_type in SUPPORTED_DATA_TYPES and
                'trade_date' in data.columns and not data.empty):

                success_count = 0
                with sqlite3.connect(self.db_path) as conn:
                    cursor = conn.cursor()

                    # Store each day's data separately with upsert behavior
                    for _, row in data.iterrows():
                        trade_date = str(row['trade_date'])
                        row_df = pd.DataFrame([row])
                        data_blob = pickle.dumps(row_df)

                        try:
                            # Check if record exists
                            cursor.execute(
                                """
                                SELECT id FROM daily_data
                                WHERE data_type = ? AND symbol = ? AND trade_date = ?
                                """,
                                (data_type, symbol, trade_date)
                            )
                            exists = cursor.fetchone()

                            if exists:
                                # Update existing record
                                cursor.execute(
                                    """
                                    UPDATE daily_data
                                    SET data = ?, updated_at = ?
                                    WHERE data_type = ? AND symbol = ? AND trade_date = ?
                                    """,
                                    (data_blob, current_time, data_type, symbol, trade_date)
                                )
                            else:
                                # Insert new record
                                cursor.execute(
                                    """
                                    INSERT INTO daily_data
                                    (data_type, symbol, trade_date, data, created_at, updated_at)
                                    VALUES (?, ?, ?, ?, ?, ?)
                                    """,
                                    (data_type, symbol, trade_date, data_blob, current_time, current_time)
                                )
                            success_count += 1
                        except Exception as e:
                            logger.warning(f"Failed to cache daily data for {symbol} {trade_date}: {e}")

                    conn.commit()

                logger.debug(f"Cached {success_count} daily records for {key}")
                return success_count > 0

            # Unsupported data type
            logger.debug(f"Skipping cache for unsupported data type: {key}")
            return False

        except Exception as e:
            logger.error(f"Failed to cache data for key {key}: {str(e)}")
            return False

    def get_cached_dates(self, data_type: str, symbol: str) -> List[str]:
        """Get list of cached trade dates for a specific symbol and data type."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    SELECT trade_date FROM daily_data
                    WHERE data_type = ? AND symbol = ?
                    ORDER BY trade_date
                    """,
                    (data_type, symbol)
                )
                return [row[0] for row in cursor.fetchall()]
        except Exception as e:
            logger.warning(f"Failed to get cached dates for {data_type}_{symbol}: {str(e)}")
            return []

    def invalidate(self, pattern: str) -> int:
        """Invalidate cached data matching pattern."""
        try:
            total_deleted = 0
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Parse pattern to extract components
                if '_' in pattern and not pattern.endswith('*'):
                    data_type, symbol, start_date, end_date = self._parse_cache_key(pattern)
                    if symbol:
                        cursor.execute(
                            "DELETE FROM daily_data WHERE data_type = ? AND symbol = ?",
                            (data_type, symbol)
                        )
                        total_deleted += cursor.rowcount
                    elif data_type:
                        cursor.execute(
                            "DELETE FROM daily_data WHERE data_type = ?",
                            (data_type,)
                        )
                        total_deleted += cursor.rowcount
                else:
                    # Pattern matching for wildcards
                    cursor.execute("SELECT id, data_type, symbol FROM daily_data")
                    results = cursor.fetchall()

                    ids_to_delete = []
                    for row_id, data_type, symbol in results:
                        synthetic_key = f"{data_type}_{symbol}_daily"
                        if fnmatch.fnmatch(synthetic_key, pattern):
                            ids_to_delete.append(row_id)

                    if ids_to_delete:
                        placeholders = ",".join("?" * len(ids_to_delete))
                        cursor.execute(
                            f"DELETE FROM daily_data WHERE id IN ({placeholders})",
                            ids_to_delete
                        )
                        total_deleted += cursor.rowcount

                conn.commit()

            logger.info(f"Invalidated {total_deleted} cache entries matching pattern: {pattern}")
            return total_deleted

        except Exception as e:
            logger.error(f"Failed to invalidate cache with pattern {pattern}: {str(e)}")
            return 0

    def clear_all(self) -> int:
        """Clear all cached data."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Count entries before deletion
                cursor.execute("SELECT COUNT(*) FROM daily_data")
                total_deleted = cursor.fetchone()[0]

                # Delete all data
                cursor.execute("DELETE FROM daily_data")
                conn.commit()

                # Vacuum to reclaim space
                cursor.execute("VACUUM")

            logger.info(f"Cleared {total_deleted} cache entries")
            return total_deleted

        except Exception as e:
            logger.error(f"Failed to clear cache: {str(e)}")
            return 0

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Total entries
                cursor.execute("SELECT COUNT(*) FROM daily_data")
                total_entries = cursor.fetchone()[0]

                # Unique symbols
                cursor.execute("SELECT COUNT(DISTINCT symbol) FROM daily_data WHERE symbol != ''")
                unique_symbols = cursor.fetchone()[0]

                # Data type breakdown
                cursor.execute("SELECT data_type, COUNT(*) FROM daily_data GROUP BY data_type")
                data_type_counts = dict(cursor.fetchall())

                # Database size
                cursor.execute("PRAGMA page_size")
                page_size = cursor.fetchone()[0]
                cursor.execute("PRAGMA page_count")
                page_count = cursor.fetchone()[0]
                db_size_bytes = page_size * page_count

                return {
                    'total_entries': total_entries,
                    'unique_symbols': unique_symbols,
                    'data_type_counts': data_type_counts,
                    'db_size_bytes': db_size_bytes,
                    'db_size_mb': round(db_size_bytes / (1024 * 1024), 2),
                    'db_path': self.db_path
                }

        except Exception as e:
            logger.error(f"Failed to get cache stats: {str(e)}")
            return {
                'total_entries': 0,
                'unique_symbols': 0,
                'data_type_counts': {},
                'db_size_bytes': 0,
                'db_size_mb': 0,
                'db_path': self.db_path
            }

    def get_cache_keys(self, pattern: Optional[str] = None) -> List[str]:
        """Get list of cache keys, optionally filtered by pattern."""
        try:
            keys = []
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()

                # Get all distinct combinations
                cursor.execute("SELECT DISTINCT data_type, symbol FROM daily_data")
                for data_type, symbol in cursor.fetchall():
                    key = f"{data_type}_{symbol}_daily"
                    keys.append(key)

                if pattern:
                    keys = [key for key in keys if fnmatch.fnmatch(key, pattern)]

                return keys

        except Exception as e:
            logger.error(f"Failed to get cache keys: {str(e)}")
            return []

    def vacuum(self):
        """Vacuum the database to reclaim space after deletions."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.cursor()
                cursor.execute("VACUUM")
                logger.debug("Database vacuumed successfully")
        except Exception as e:
            logger.warning(f"Failed to vacuum database: {str(e)}")

    def clear_cache(self, pattern: Optional[str] = None) -> int:
        """Alias for invalidate to maintain compatibility.

        Args:
            pattern: Pattern to match keys for invalidation. If None, clears all cache. e.g., 'stock_data_000001.SZ_*', 'index_data_*'
        Returns:
            Number of cache entries removed.
        """
        if pattern:
            return self.invalidate(pattern)
        else:
            return self.clear_all()

    def bulk_populate_daily_data(self, data_provider, start_date: Optional[str] = None, end_date: Optional[str] = None):
        """
        Incrementally populate cache with all stocks' daily data for the specified date range.
        Only fetches data for dates that are not already cached.

        - stock_data: Combined stock data (basic information + OHLCV + fundamentals)
          - ohlcv_data: OHLCV market data
          - fundamental_data: Financial fundamental indicators
        - TODO: index_data: Stock index data, Now benchmark only needs to be implemented.
        """
        from ..utils.trading_calendar import get_trading_days_between, get_trading_days_before
        from .. import global_cm
        # Configure logger to reduce output during batch processing
        #logger.remove()  # Remove default handler
        #logger.add(lambda msg: None if "Retrieved" in msg else print(msg), level="WARNING")

        if not start_date:
            start_date = get_trading_days_before('2025-01-01', global_cm.get('pattern_detector.lookback_days'))
        if not end_date:
            end_date = datetime.now().strftime("%Y-%m-%d")

        logger.info(f"Starting incremental bulk population from {start_date} to {end_date}")
        benchmark_codes = global_cm.get('init_info.benchmark_codes')
        indexes = [v for k, v in benchmark_codes.items()]

        # Get all stocks (not cached, direct API call)
        basic_info = data_provider.get_basic_information()
        if basic_info.empty:
            logger.error("No basic information found")
            return

        # Filter out risky stocks (ST, *ST, etc.)
        name_pattern = r'^(?:C|N|\*?ST|S)|退'
        ts_code_pattern = r'^(?:C|N|\*|4|9|8|30|688)|ST'
        exclude_conditions = (
            basic_info['name'].str.contains(name_pattern, regex=True, na=False) |
            basic_info['ts_code'].str.contains(ts_code_pattern, regex=True, na=False)
        )
        df = basic_info[~exclude_conditions]
        symbols = df['ts_code'].tolist()
        symbols += indexes

        # Get required trading dates
        start_date = convert_trade_date(start_date)
        end_date = convert_trade_date(end_date)
        if not start_date or not end_date:
            raise DataProviderError("Invalid start_date or end_date for bulk population")
        required_dates = set(get_trading_days_between(start_date, end_date))

        logger.info(f"Processing {len(symbols)} symbols(len({len(indexes)} indexes)) for {len(required_dates)} trade dates")

        # Process each symbol incrementally
        errors = []
        total_fetched = 0
        total_skipped = 0

        for i, stock_code in enumerate(tqdm(symbols, desc="Processing stocks", unit="stock")):
            try:
                # Check what dates are already cached for this symbol
                if stock_code in indexes:
                    cached_dates = set(self.get_cached_dates('index_data', stock_code))
                else:
                    cached_dates = set(self.get_cached_dates('stock_data', stock_code))

                # Find missing dates
                missing_dates = required_dates - cached_dates

                if not missing_dates:
                    # All data already cached for this symbol
                    total_skipped += 1
                    continue

                # Sort missing dates and fetch in chunks to minimize API calls
                missing_dates_list = sorted(missing_dates)

                # Find continuous date ranges to minimize API calls
                date_ranges = []
                if missing_dates_list:
                    range_start = missing_dates_list[0]
                    range_end = missing_dates_list[0]

                    for date in missing_dates_list[1:]:
                        # Check if this date is consecutive to the current range
                        next_trading_day = get_trading_days_between(range_end, date)
                        if len(next_trading_day) <= 2:  # Allow 1 day gap
                            range_end = date
                        else:
                            # End current range and start new one
                            date_ranges.append((range_start, range_end))
                            range_start = date
                            range_end = date

                    # Add the last range
                    date_ranges.append((range_start, range_end))

                # Fetch data for each date range
                for range_start, range_end in date_ranges:
                    try:
                        if stock_code in indexes:
                            data_provider.get_index_data(stock_code, range_start, range_end)
                        else:
                            data_provider.get_stock_data(stock_code, range_start, range_end)
                        total_fetched += 1
                    except Exception as e:
                        errors.append((stock_code, f"{range_start}-{range_end}", str(e)))
                        continue

            except Exception as e:
                errors.append((stock_code, "all_dates", str(e)))
                continue

        logger.info("Incremental population completed:")
        logger.info(f"  - Symbols processed: {len(symbols)}, {len(indexes)} benchmark indexes")
        logger.info(f"  - Symbols with new data fetched: {total_fetched}")
        logger.info(f"  - Symbols completely cached (skipped): {total_skipped}")
        logger.info(f"  - Errors: {len(errors)}")

        if errors:
            logger.debug("Errors occurred:")
            for stock_code, date_range, error_msg in errors[:10]:  # Show first 10 errors
                logger.debug(f"  - {stock_code} ({date_range}): {error_msg}")
            if len(errors) > 10:
                logger.debug(f"  ... and {len(errors) - 10} more errors")

        # Show final cache statistics
        stats = self.get_cache_stats()
        logger.info(f"Final cache stats: {stats['total_entries']} entries, {stats['unique_symbols']} symbols, {stats['db_size_mb']} MB")
