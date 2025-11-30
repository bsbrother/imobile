import logging
from loguru import logger
import warnings
import time
from tqdm import tqdm
from typing import List, Optional, Dict, Any, Union
import json
import pandas as pd

import tushare as ts
import akshare as ak  # type: ignore  # noqa: E402
from pytdx.hq import TdxHq_API
from tenacity import before_sleep_log, retry, retry_if_exception_type, stop_after_attempt, wait_random_exponential

from .sqlite_cache import SQLiteDataCache
from .validator import DataValidator
from ..core.interfaces import DataProvider
from ..utils.exceptions import DataProviderError, TushareAPIError
from ..utils.util import convert_trade_date, refresh_tdx_config, dfs_concat, _safe_fillna
from .. import DB_CACHE_FILE

# Create a standard logging logger for tenacity
tenacity_logger = logging.getLogger(__name__)
warnings.filterwarnings("ignore", category=UserWarning, module='py_mini_racer')

class TushareDataProvider(DataProvider):
    """
    Data provider implementation using Tushare Pro API.
    Fetching [Tushare Doc & API](https://tushare.pro/document/2)

    The trading_calendar, basic_info use pkl cache, saved at data_cache/. Others use SQLite cache.
    """

    def __init__(self, token: str, rate_limit_delay: float = 0.2):
        """
        Initialize Tushare data provider.

        Args:
            token: Tushare Pro API token
            rate_limit_delay: Delay between API calls in seconds
        """
        self.token = token
        self.rate_limit_delay = rate_limit_delay
        self.cache = SQLiteDataCache(db_path=DB_CACHE_FILE)

        try:
            ts.set_token(token)
            self.pro = ts.pro_api()
            logger.debug("Tushare Pro API initialized successfully")
        except Exception as e:
            raise TushareAPIError(f"Failed to initialize Tushare Pro API: {str(e)}")

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.2, min=1, max=2),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(tenacity_logger, logging.INFO)
    )
    def _ts_call(self, func, **kwargs) -> pd.DataFrame:
        """Make Tushare API call with automatic retry using tenacity decorator."""
        # Convert date formats from 'yyyy-mm-dd' to 'yyyymmdd' for Tushare API compatibility
        for date_param in ['start_date', 'end_date', 'trade_date']:
            if date_param in kwargs and kwargs[date_param]:
                kwargs[date_param] = convert_trade_date(kwargs[date_param])

        time.sleep(self.rate_limit_delay)
        df = func(**kwargs)
        if df is None or not isinstance(df, pd.DataFrame):
            raise DataProviderError("Invalid response from Tushare API")
        if df.empty:
            time.sleep(self.rate_limit_delay)
            df = func(**kwargs)

        # default trade_date sort is ascending false, not as cache save(start_date to end_date).
        if 'trade_date' in df.columns:
            df = df.sort_values(['trade_date'], ascending=[True])
        return df


    def get_kline(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None, adj: str = "qfq", freq: str = "D") -> pd.DataFrame:
        """ Retrieve k-line(as OHLCV) for specified stock with adj,freq in the specified range."""
        if not symbol:
            raise DataProviderError("No symbol provided")
        start_date = convert_trade_date(start_date)
        end_date = convert_trade_date(end_date)
        if not start_date or not end_date:
            raise DataProviderError("Start date and end date are required")
        # TODO: [stk_mins realy k-lines by minutes](https://tushare.pro/document/2?doc_id=370)
        df = self._ts_call(self.pro.bar, ts_code=symbol, start_date=start_date, end_date=end_date, adj=adj, freq=freq)
        # TODO: cache
        return df


    def get_ohlcv_data(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Retrieve daily OHLCV(open, high, low, close, volume etc.) data without adj for specified stock.
        https://tushare.pro/document/2?doc_id=27
        """
        if not symbol:
            raise DataProviderError("No symbol provided")
        logger.debug(f"Fetching daily OHLCV data for symbol: {symbol} from {start_date} to {end_date}")
        start_date = convert_trade_date(start_date)
        end_date = convert_trade_date(end_date)
        if not start_date or not end_date:
            raise DataProviderError("Start date or end date is invalid")
        fields = [
            "ts_code",
            "trade_date",
            "open",
            "high",
            "low",
            "close",
            "pre_close",
            "change",     # Up/Down price, Yuan
            "pct_chg",    # Up/Down range, %
            "vol",        # Trade count, Shou * 100
            "amount"      # Trade sum, Qian_Yuan * 1000
        ]

        cache_key = f"ohlcv_data_{symbol}_{start_date}_{end_date}"
        cached_data = self.cache.get(cache_key)
        if cached_data is not None:
            logger.debug(f"Retrieved ohlcv data from cache for {symbol}")
            return cached_data

        df = self._ts_call(self.pro.daily, ts_code=symbol, start_date=start_date, end_date=end_date, fields=fields)

        self.cache.set(cache_key, df)
        logger.debug(f'Retrieved {len(df)} OHLCV records for {symbol}')
        return df


    def get_fundamental_data(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Retrieve daily fundamental data (turnover_rate, volume_ratio, pe, dv_ttm, circ_mv etc.) for specified stock.
        https://tushare.pro/document/2?doc_id=32
        """
        if not symbol:
            raise DataProviderError("No symbol provided")
        logger.debug(f"Fetching fundamental data for symbol: {symbol} from {start_date} to {end_date}")
        start_date = convert_trade_date(start_date)
        end_date = convert_trade_date(end_date)
        if not start_date or not end_date:
            raise DataProviderError("Start date or end date is invalid")
        fields=[
            "ts_code",
            "trade_date",
            "close",
            "turnover_rate",    # Change owner rate, %
            "turnover_rate_f",  # turnover_rate(free), %
            "volume_ratio",     # Up/Down compare prev 5 days ratio, %
            "pe",               # total_mv/return rate, %
            "pe_ttm",
            "pb",
            "ps",
            "ps_ttm",
            "dv_ratio",
            "dv_ttm",
            "total_share",
            "float_share",
            "free_share",
            "total_mv",       # total_share * close, Wan_Yuan * 1e4
            "circ_mv",        # total_mv(free), Wan_Yuan * 1e4
            "limit_status"
        ]

        cache_key = f"fundamental_data_{symbol}_{start_date}_{end_date}"
        cached_data = self.cache.get(cache_key)
        if cached_data is not None:
            logger.debug(f"Retrieved fundamental data from cache for {symbol}")
            return cached_data

        df = self._ts_call(self.pro.daily_basic, ts_code=symbol, start_date=start_date, end_date=end_date, fields=fields)

        self.cache.set(cache_key, df)
        logger.debug(f'Retrieved {len(df)} fundamental data records for {symbol}')
        return df


    def get_basic_information_api(self) -> pd.DataFrame:
        """Retrieve basic information for all stocks by API"""
        fields=[
            "ts_code",
            "symbol",
            "name",
            "area",
            "industry",
            "cnspell",
            "market",
            "list_date",
            "act_name",
            "act_ent_type",
            "fullname",
            "enname",
            "exchange",
            "curr_type",
            "list_status",
            "delist_date",
            "is_hs"
        ]
        df = self._ts_call(self.pro.stock_basic, fields=fields)
        return df


    def get_basic_information(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """
        Retrieve basic information for a specified stock or all stocks if <symbol> is None.
        https://tushare.pro/document/2?doc_id=25

        Args:
            symbol: Stock symbol (e.g., '000001.SZ'). If None, retrieve for all stocks.

        Returns:
            DataFrame with basic information for the specified stock(s).

        Raises:
            DataProviderError: If data retrieval fails
        """
        from .. import basic_info_cache
        df = pd.DataFrame()
        if symbol:
            logger.debug(f"Retrieved basic information from cache for symbol: {symbol} ...")
            row_dict = basic_info_cache.get(symbol)
            if row_dict:
                # Convert to DataFrame (each key becomes a column)
                df = pd.DataFrame([row_dict])
        else:
            logger.debug("Retrieved basic information from cache for all symbols ...")
            df = basic_info_cache.list_all()

        logger.debug(f"Retrieved {len(df)} basic information records")
        return df


    def get_stock_data(self, symbols: Union[str, List[str]], start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """
        Retrieve basic information, daily OHLCV and fundamental data for specified stock(s).

        Args:
            symbol: Stock symbol or list of symbols (e.g., '000001.SZ' or ['000001.SZ','600519.SH'])
            start_date: Start date in YYYYMMDD or YYYY-MM-DD format
            end_date: End date in YYYYMMDD or YYYY-MM-DD format

        Returns:
            DataFrame with full columns merged from daily, daily_basic, and stock_basic.
            See Tushare docs for field details:
            - daily: https://tushare.pro/document/2?doc_id=27
            - daily_basic: https://tushare.pro/document/2?doc_id=32
            - stock_basic: https://tushare.pro/document/2?doc_id=25

        Raises:
            DataProviderError: If data retrieval fails
        """
        if not symbols:
            raise DataProviderError("No symbol provided")
        start_date = convert_trade_date(start_date)
        end_date = convert_trade_date(end_date)
        if not start_date or not end_date:
            raise DataProviderError("Start date or end date is invalid")

        # Helper to fetch a single symbol with full merge and caching
        def _fetch_single(sym: str) -> pd.DataFrame:
            cache_key = f"stock_data_{sym}_{start_date}_{end_date}"
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

            df_basic = self.get_basic_information(sym)
            if df_basic.empty:
                raise DataProviderError(f"No basic information found for symbol: {sym}")
            df_daily = self.get_ohlcv_data(sym, start_date, end_date)
            if df_daily.empty:
                raise DataProviderError(f"No OHLCV data found for symbol: {sym} in date range {start_date}-{end_date}")
            df_daily_basic = self.get_fundamental_data(sym, start_date, end_date)
            if df_daily_basic.empty:
                raise DataProviderError(f"No fundamental data found for symbol: {sym} in date range {start_date}-{end_date}")

            # Remove overlapping cols between daily and daily_basic except keys
            overlap_cols = list(set(df_daily_basic.columns) & set(df_daily.columns) - {'ts_code', 'trade_date'})
            df_db_filtered = df_daily_basic.drop(columns=overlap_cols)
            merged = df_daily.merge(df_db_filtered, on=['ts_code', 'trade_date'], how='left')

            # Merge stock_basic on ts_code
            overlap_cols2 = list(set(df_basic.columns) & set(merged.columns) - {'ts_code'})
            df_basic_filtered = df_basic.drop(columns=overlap_cols2)
            merged = merged.merge(df_basic_filtered, on='ts_code', how='left')

            if merged.empty:
                raise DataProviderError(f"No stock data found for {sym} in date range {start_date}-{end_date}")

            # Ensure all required fundamental columns exist with proper defaults
            # This must be done after merge to handle cases where merge creates NaN columns or drops them
            fund_cols = ['turnover_rate', 'volume_ratio', 'pe', 'circ_mv']
            for col in fund_cols:
                if col not in merged.columns:
                    merged[col] = 100.0 if col == 'volume_ratio' else 0.0
                else:
                    # Fill NaN values with defaults
                    default_val = 100.0 if col == 'volume_ratio' else 0.0
                    merged[col] = _safe_fillna(merged[col], default_val)

            # Sort by trade_date ascending for downstream indicators
            if 'trade_date' in merged.columns:
                try:
                    merged = merged.sort_values('trade_date')
                except Exception:
                    pass

            self.cache.set(cache_key, merged)
            return merged

        try:
            if isinstance(symbols, list):
                frames = []
                # Use tqdm for progress tracking when processing multiple symbols
                symbol_iter = tqdm(symbols, desc="Fetching stock data", unit="stock") if len(symbols) > 1 else symbols

                for sym in symbol_iter:
                    try:
                        frames.append(_fetch_single(sym))
                    except Exception as e:
                        # Reduce logger output during batch processing
                        if len(symbols) <= 10:  # Only log for small batches
                            logger.warning(f"Skipping {sym} due to error: {e}")
                if not frames:
                    raise DataProviderError("No data retrieved for any symbol")
                return dfs_concat(frames, ignore_index=True)
            else:
                return _fetch_single(symbols)
        except TushareAPIError:
            raise
        except Exception as e:
            if 'No OHLCV data found for symbol' in str(e):
                logger.warning(f"No OHLCV data found for symbol(s): {symbols} in date range {start_date}-{end_date}")
                return pd.DataFrame()
            else:
                raise DataProviderError(f"Failed to retrieve stock data: {str(e)}")


    def get_index_data(self, index_code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """
        Retrieve index data for benchmarking.

        Args:
            index_code: Index code (e.g., '000300.SH' for CSI 300, '000905.SH' for CSI 500)
            start_date: Start date in YYYYMMDD format
            end_date: End date in YYYYMMDD format

        Returns:
            DataFrame with columns: ts_code, trade_date, open, high, low, close, vol, amount

        Raises:
            DataProviderError: If data retrieval fails
        """
        if not index_code:
            raise DataProviderError("No index code provided")
        logger.debug(f"Fetching index data for index: {index_code} from {start_date} to {end_date}")
        start_date = convert_trade_date(start_date)
        end_date = convert_trade_date(end_date)
        cache_key = f"index_data_{index_code}_{start_date}_{end_date}"
        cached_data = self.cache.get(cache_key)
        if cached_data is not None:
            logger.debug(f"Retrieved index data from cache for {index_code}")
            return cached_data

        df = self._ts_call(self.pro.index_daily, ts_code=index_code, start_date=start_date, end_date=end_date)
        if df.empty:
            raise DataProviderError(f"No index data found for {index_code} in date range {start_date}-{end_date}")

        # Normalize and validate data
        #data = self.validator.normalize_ohlcv_data(data)
        #self.validator.validate_ohlcv_data(data)
        #self.validator.validate_date_range(data, start_date, end_date)

        self.cache.set(cache_key, df)
        logger.debug(f"Retrieved {len(df)} index records for {index_code}")
        return df

    def get_trading_calendar(self, start_date: str | None = None, end_date: str | None = None) -> List[str]:
        """
        Get list of trading dates in the specified range.

        Args:
            start_date: Start date in YYYYMMDD format
            end_date: End date in YYYYMMDD format

        Returns:
            List of trading dates in YYYYMMDD format

        Raises:
            DataProviderError: If calendar retrieval fails
        """
        if not start_date or not end_date:
            raise DataProviderError("Start date and end date are required")
        logger.debug(f"Fetching trading calendar from {start_date} to {end_date}")
        start_date = convert_trade_date(start_date)
        end_date = convert_trade_date(end_date)
        calendar_data = self._ts_call(self.pro.trade_cal, exchange='SSE', start_date=start_date, end_date=end_date)

        if calendar_data.empty:
            raise DataProviderError(f"No trading calendar data found for date range {start_date}-{end_date}")

        # Filter for trading days only (is_open == 1)
        trading_days_data = calendar_data[calendar_data['is_open'] == 1].copy()
        trading_days = trading_days_data['cal_date'].tolist()
        trading_days.sort()

        logger.debug(f"Retrieved {len(trading_days)} trading days")
        return trading_days


    def validate_symbols(self, symbols: List[str]) -> Dict[str, bool]:
        """
        Validate if stock symbols exist and are tradeable.

        Args:
            symbols: List of stock symbols to validate

        Returns:
            Dictionary mapping symbols to their validity status
        """
        validation_results = {}
        try:
            # Get basic stock info to validate symbols
            for symbol in symbols:
                try:
                    stock_info = self.get_basic_information(symbol=symbol)
                    validation_results[symbol] = not stock_info.empty
                except Exception as e:
                    logger.warning(f"Failed to validate symbol {symbol}: {str(e)}")
                    validation_results[symbol] = False
            return validation_results
        except Exception as e:
            logger.error(f"Symbol validation failed: {str(e)}")
            # Return all symbols as invalid if validation fails
            return {symbol: False for symbol in symbols}

    def get_data_quality_report(self, data: pd.DataFrame) -> Dict[str, Any]:
        """
        Get data quality report for retrieved data.

        Args:
            data: DataFrame to analyze

        Returns:
            Dictionary with quality metrics and recommendations
        """
        return DataValidator.get_data_quality_score(data)

    def clear_cache(self, pattern: Optional[str] = None) -> int:
        """
        Clear cached data.

        Args:
            pattern: Optional pattern to match cache keys (clears all if None)

        Returns:
            Number of cache entries cleared
        """
        if not self.cache:
            return 0

        if pattern:
            return self.cache.invalidate(pattern)
        else:
            return self.cache.clear_all()

    def get_cache_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        return self.cache.get_cache_stats()

class AkshareDataProvider(DataProvider):
    """
    Data provider implementation using Akshare API.
    Fetching [Aksahre Doc & API](https://akshare.akfamily.xyz/)
    """
    def __init__(self, rate_limit_delay: float = 0.2):
        """Initialize Akshare data provider. Cache default expired at 1 year ago.

        Args:
            rate_limit_delay: Delay between API calls in seconds
        """
        self.rate_limit_delay = rate_limit_delay
        self.cache = SQLiteDataCache(db_path=DB_CACHE_FILE)

    # ------------------------- helpers -------------------------
    @staticmethod
    def _ensure_ts_code(symbol: str | None) -> str:
        """Return ts_code style like 600519.SH for inputs '600519' or '600519.SH'."""
        if not symbol:
            raise DataProviderError("Empty symbol")
        s = symbol.strip().upper()
        if s.endswith('.SH') or s.endswith('.SZ') or s.endswith('.BJ'):
            return s
        # infer exchange by code prefix rules in A-shares
        if s[0] == '6':
            return f"{s}.SH"
        if s[0] in ('0', '3'):
            return f"{s}.SZ"
        if s[0] in ('4', '8'):
            return f"{s}.BJ"
        # default to SZ if unknown but 6-digit
        return f"{s}.SZ"

    @staticmethod
    def _ts_to_ak_symbol(ts_code: str) -> str:
        """Akshare stock_zh_a_hist expects pure 6-digit code."""
        if not ts_code:
            raise DataProviderError("Empty symbol")
        return ts_code.split('.')[0]

    @staticmethod
    def _index_ts_to_ak_symbol(index_code: str) -> str:
        """Convert ts_code index like 000300.SH -> sh000300, 399905.SZ -> sz399905."""
        if not index_code:
            raise DataProviderError("Empty index_code")
        code, exch = index_code.split('.') if '.' in index_code else (index_code, '')
        exch = exch.upper()
        if exch == 'SH' or (not exch and code.startswith('0')):
            return f"sh{code}"
        if exch == 'SZ' or (not exch and code.startswith('3')):
            return f"sz{code}"
        # fallback guess by prefix
        return f"{'sh' if code.startswith('0') else 'sz'}{code}"


    @staticmethod
    def _fmt_trade_date_col(df: pd.DataFrame, src_col: str) -> pd.Series:
        s = pd.to_datetime(df[src_col]).dt.strftime('%Y%m%d')
        s = pd.to_datetime(df[src_col]).dt.strftime('%Y%m%d')
        return s

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.2, min=1, max=2),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(tenacity_logger, logging.INFO)
    )
    def _ak_call(self, func, **kwargs) -> pd.DataFrame:
        for date_param in ['start_date', 'end_date', 'trade_date']:
            if date_param in kwargs and kwargs[date_param]:
                kwargs[date_param] = convert_trade_date(kwargs[date_param])

        time.sleep(self.rate_limit_delay)
        df = func(**kwargs)
        if df is None or not isinstance(df, pd.DataFrame):
            raise DataProviderError("Invalid response from Akshare API")
        if df.empty:
            time.sleep(self.rate_limit_delay)
            df = func(**kwargs)
        return df

    def _enrich_with_spot_data(self, df: pd.DataFrame, code6: str) -> None:
        """Enrich fundamental data with real-time values from ak.stock_zh_a_spot_em().

        This method fills missing fundamental fields like PE, PB, market cap etc.
        using real-time snapshot data. It updates the DataFrame in-place.

        Args:
            df: DataFrame with fundamental data to enrich
            code6: 6-digit stock code (e.g., '000001')
        """
        try:
            # Get real-time snapshot data
            spot = self._ak_call(ak.stock_zh_a_spot_em)
            if spot.empty:
                logger.warning("Empty spot data from ak.stock_zh_a_spot_em()")
                return

            # Find the row for our stock
            stock_row = spot[spot['代码'].astype(str).str.zfill(6) == code6]
            if stock_row.empty:
                logger.debug(f"Stock {code6} not found in spot data")
                return

            stock_data = stock_row.iloc[0]
            last_date = df['trade_date'].max()
            last_idx = df['trade_date'] == last_date

            # Define field mappings with proper unit conversions
            field_mappings = {
                # Field: (spot_column, conversion_func, description)
                'pe': ('市盈率-动态', lambda x: pd.to_numeric(x, errors='coerce'), 'PE ratio'),
                'pb': ('市净率', lambda x: pd.to_numeric(x, errors='coerce'), 'PB ratio'),
                'turnover_rate': ('换手率',
                                  lambda x: pd.to_numeric(str(x).replace('%', ''), errors='coerce'),
                                  'Turnover rate %'),
                'volume_ratio': ('量比',
                                lambda x: pd.to_numeric(str(x).replace('%', ''), errors='coerce') if pd.notna(x) else None,
                                'Volume ratio'),
                'total_mv': ('总市值',
                            lambda x: pd.to_numeric(x, errors='coerce') * 1e4 if pd.notna(x) else None,
                            'Total market cap (万元)'),
                'circ_mv': ('流通市值',
                           lambda x: pd.to_numeric(x, errors='coerce') * 1e4 if pd.notna(x) else None,
                           'Circulating market cap (万元)')
            }

            # Apply field mappings
            for field, (spot_col, converter, desc) in field_mappings.items():
                if spot_col not in spot.columns:
                    continue

                spot_value = stock_data[spot_col]
                # Ensure spot_value is scalar and check if it's invalid
                # Extract scalar value if it's a Series
                if isinstance(spot_value, pd.Series):
                    spot_value = spot_value.iloc[0] if len(spot_value) > 0 else None

                if pd.isna(spot_value) or (isinstance(spot_value, str) and spot_value in ['', '-', '--']):
                    continue

                try:
                    converted_value = converter(spot_value)
                    if pd.notna(converted_value) and converted_value != 0:
                        # For volume_ratio, only fill if current value is NaN
                        if field == 'volume_ratio':
                            current_values = df.loc[last_idx, field]
                            if isinstance(current_values, pd.Series) and current_values.isna().all():
                                df.loc[last_idx, field] = converted_value
                                logger.debug(f"Filled {field} from spot data: {converted_value}")
                            elif pd.isna(current_values):
                                df.loc[last_idx, field] = converted_value
                                logger.debug(f"Filled {field} from spot data: {converted_value}")
                        else:
                            # For other fields, always update with spot data (more current)
                            df.loc[last_idx, field] = converted_value
                            logger.debug(f"Updated {field} from spot data: {converted_value} ({desc})")
                except Exception as e:
                    logger.debug(f"Failed to convert {field} value '{spot_value}': {e}")

            # Additional computed fields using spot data
            try:
                # Update pe_ttm (assume same as pe for now)
                pe_series = df.loc[last_idx, 'pe']
                if isinstance(pe_series, pd.Series) and not pe_series.isna().all():
                    pe_val = pe_series.iloc[0]
                    if pd.notna(pe_val):
                        df.loc[last_idx, 'pe_ttm'] = pe_val
                elif pd.notna(pe_series):
                    # Handle scalar case
                    df.loc[last_idx, 'pe_ttm'] = pe_series

                # Estimate total_share and float_share using market cap and price
                close_val = df.loc[last_idx, 'close'] if 'close' in df.columns else None
                close_price = close_val.iloc[0] if isinstance(close_val, pd.Series) else close_val
                # Convert pandas scalar to Python native type first, then to float
                try:
                    close_price = float(pd.to_numeric(close_price, errors='coerce')) if pd.notna(close_price) and close_price not in ('', None) else None
                except (ValueError, TypeError):
                    close_price = None

                total_mv_series = df.loc[last_idx, 'total_mv']
                total_mv_val = total_mv_series.iloc[0] if isinstance(total_mv_series, pd.Series) and not total_mv_series.isna().all() else (total_mv_series if pd.notna(total_mv_series) else None)
                total_mv_val = pd.to_numeric(total_mv_val, errors='coerce') if pd.notna(total_mv_val) and total_mv_val not in ('', None) else None

                circ_mv_series = df.loc[last_idx, 'circ_mv']
                circ_mv_val = circ_mv_series.iloc[0] if isinstance(circ_mv_series, pd.Series) and not circ_mv_series.isna().all() else (circ_mv_series if pd.notna(circ_mv_series) else None)
                circ_mv_val = pd.to_numeric(circ_mv_val, errors='coerce') if pd.notna(circ_mv_val) and circ_mv_val not in ('', None) else None

                if close_price is not None and total_mv_val is not None and close_price > 0:
                    # total_share (万股) = total_mv (万元) / close_price (元)
                    total_share = total_mv_val / close_price
                    df.loc[last_idx, 'total_share'] = total_share

                if close_price is not None and circ_mv_val is not None and close_price > 0:
                    # float_share (万股) = circ_mv (万元) / close_price (元)
                    float_share = circ_mv_val / close_price
                    df.loc[last_idx, 'float_share'] = float_share
                    df.loc[last_idx, 'free_share'] = float_share  # Assume same as float_share

            except Exception as e:
                logger.debug(f"Failed to compute derived fields: {e}")

        except Exception as e:
            logger.warning(f"Failed to enrich fundamental data with spot data: {e}")
            # Continue without spot data enrichment


    # ------------------------- interface impl -------------------------
    def get_kline(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None, adj: str = "qfq", freq: str = "D") -> pd.DataFrame:
        """ Retrieve k-line(as OHLCV) for specified stock with adj,freq in the specified range."""
        return pd.DataFrame()  # Not implemented yet

    def get_index_data(self, index_code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """
        Retrieve index data for benchmarking using Akshare.
        Supports common indexes like 000001.SH (SSE), 399001.SZ (SZSE), 000300.SH (CSI300).
        """
        if not index_code:
            raise DataProviderError("No index code provided")
        
        ak_symbol = self._index_ts_to_ak_symbol(index_code)
        start = convert_trade_date(start_date)
        end = convert_trade_date(end_date)
        
        cache_key = f"index_data_{index_code}_{start}_{end}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            # Use ak.stock_zh_index_daily for index data
            # symbol format: sh000001
            df = self._ak_call(
                ak.stock_zh_index_daily,
                symbol=ak_symbol
            )
            
            if df.empty:
                raise DataProviderError(f"No index data found for {index_code}")

            # Filter by date range
            if 'date' in df.columns:
                df['trade_date'] = self._fmt_trade_date_col(df, 'date')
                
            if start:
                df = df[df['trade_date'] >= start]
            if end:
                df = df[df['trade_date'] <= end]
                
            if df.empty:
                raise DataProviderError(f"No index data found for {index_code} in range {start}-{end}")

            # Rename columns to match Tushare format
            # Akshare columns: date, open, high, low, close, volume
            rename_map = {
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'vol'
            }
            for k, v in rename_map.items():
                if k in df.columns:
                    df.rename(columns={k: v}, inplace=True)
            
            df['ts_code'] = index_code
            # Ensure required columns exist
            req_cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol']
            for col in req_cols:
                if col not in df.columns:
                    df[col] = 0.0
            
            df = df[req_cols].sort_values('trade_date').reset_index(drop=True)
            
            self.cache.set(cache_key, df)
            logger.debug(f"Retrieved {len(df)} index records for {index_code} via Akshare")
            return df
            
        except Exception as e:
            raise DataProviderError(f"Failed to retrieve index data for {index_code}: {str(e)}")


    def get_ohlcv_data(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Daily OHLCV using ak.stock_zh_a_hist; normalized to Tushare-like schema."""
        ts_code = self._ensure_ts_code(symbol)
        code6 = self._ts_to_ak_symbol(ts_code)
        start = convert_trade_date(start_date)
        end = convert_trade_date(end_date)

        cache_key = f"ohlcv_data_{ts_code}_{start}_{end}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        df = self._ak_call(
            ak.stock_zh_a_hist,
            symbol=code6,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="",
        )
        if df.empty:
            raise DataProviderError(f"No OHLCV data for {ts_code}")

        # Expected columns in ak: 日期 开盘 收盘 最高 最低 成交量 成交额 振幅 涨跌幅 涨跌额 换手率
        rename_map = {
            '日期': 'trade_date',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '成交量': 'vol',
            '成交额': 'amount',
            '涨跌幅': 'pct_chg',
            '涨跌额': 'change',
        }
        for k, v in rename_map.items():
            if k in df.columns:
                df.rename(columns={k: v}, inplace=True)
        # date formatting
        if 'trade_date' in df.columns:
            df['trade_date'] = self._fmt_trade_date_col(df, 'trade_date')
        else:
            # Some ak endpoints return 'date'
            if 'date' in df.columns:
                df['trade_date'] = self._fmt_trade_date_col(df, 'date')
            else:
                raise DataProviderError("Missing date column in Akshare OHLCV")

        # add ts_code and pre_close
        df['ts_code'] = ts_code
        df = df[['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount', 'change', 'pct_chg']].copy()
        df['pre_close'] = df['close'].shift(1)
        # sort asc by trade_date
        df = df.sort_values('trade_date').reset_index(drop=True)

        self.cache.set(cache_key, df)
        logger.debug(f"Retrieved {len(df)} OHLCV records for {ts_code} via Akshare")
        return df

    def get_fundamental_data(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Best-effort daily fundamentals via ak.stock_zh_a_hist (turnover etc.).

        Note: Akshare doesn't provide Tushare's daily_basic fields 1:1 per day.
        We expose a compatible frame with available columns and NaNs for others.
        """
        ts_code = self._ensure_ts_code(symbol)
        code6 = self._ts_to_ak_symbol(ts_code)
        start = convert_trade_date(start_date)
        end = convert_trade_date(end_date)

        cache_key = f"fundamental_data_{ts_code}_{start}_{end}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            return cached

        df = self._ak_call(
            ak.stock_zh_a_hist,
            symbol=code6,
            period="daily",
            start_date=start,
            end_date=end,
            adjust="",
        )
        if df.empty:
            raise DataProviderError(f"No fundamental data for {ts_code}")
        # Enhanced fundamental data enrichment using real-time snapshot from ak.stock_zh_a_spot_em()
        self._enrich_with_spot_data(df, code6)

        # Ensure all expected columns exist (fill with NaN if not available)
        expected_cols = [
            "ts_code","trade_date","close","turnover_rate","turnover_rate_f","volume_ratio","pe","pe_ttm",
            "pb","ps","ps_ttm","dv_ratio","dv_ttm","total_share","float_share","free_share","total_mv","circ_mv","limit_status"
        ]
        for col in expected_cols:
            if col not in df.columns:
                df[col] = pd.NA

        df = df[expected_cols]
        df = df.sort_values('trade_date').reset_index(drop=True)

        self.cache.set(cache_key, df)
        logger.debug(f"Retrieved {len(df)} daily fundamental records for {ts_code} via Akshare")
        return df

    def get_basic_information(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """Retrieve basic stock info using ak stock_info_*_name_code aggregations."""
        if symbol:
            cache_key = f"basic_info_{symbol}"
        else:
            cache_key = "basic_info_all"
        cached_data = self.cache.get(cache_key)
        if cached_data is not None:
            logger.debug(f"Retrieved basic information from cache for symbol: {symbol or 'all'}")
            return cached_data

        frames = []
        sh = self._ak_call(ak.stock_info_sh_name_code)
        frames.append(self._normalize_basic_info(sh, 'SH', 'SSE'))
        sz = self._ak_call(ak.stock_info_sz_name_code)
        frames.append(self._normalize_basic_info(sz, 'SZ', 'SZSE'))
        bj = self._ak_call(ak.stock_info_bj_name_code)
        frames.append(self._normalize_basic_info(bj, 'BJ', 'BSE'))
        if not frames:
            raise DataProviderError("Failed to fetch any basic info from Akshare")
        df_all = dfs_concat(frames, ignore_index=True).drop_duplicates(subset=['ts_code'])
        self.cache.set(cache_key, df_all)
        # Save every symbol to cache
        for _, row in df_all.iterrows():
            code6 = row['symbol']
            self.cache.set(f"basic_info_{code6}", pd.DataFrame([row]))
        if symbol:
            cached_data = self.cache.get(f"basic_info_{symbol}")
            if cached_data is None:
                raise DataProviderError(f"No basic info found for {symbol}")
            return cached_data
        return df_all.reset_index(drop=True)

    @staticmethod
    def _normalize_basic_info(df: pd.DataFrame, suffix: str, exchange_name: str) -> pd.DataFrame:
        # '板块', 'A股代码', 'A股简称', 'A股上市日期', 'A股总股本', 'A股流通股本', '所属行业'
        # Try to find code and name columns across different akshare endpoints
        candidates_code = ['A股代码', '证券代码', '代码', '股票代码', 'code']
        candidates_name = ['A股简称', '证券简称', '名称', '股票简称', 'name']
        code_col = next((c for c in candidates_code if c in df.columns), None)
        name_col = next((c for c in candidates_name if c in df.columns), None)
        if code_col is None or name_col is None:
            raise DataProviderError("Unexpected columns in ak basic info")
        out = pd.DataFrame({
            'symbol': df[code_col].astype(str).str.zfill(6),
            'name': df[name_col].astype(str),
        })
        out['ts_code'] = out['symbol'] + f'.{suffix}'
        out['industry'] = df['所属行业'] if '所属行业' in df.columns else pd.NA
        out['market'] = df['板块'] if '板块' in df.columns else pd.NA
        out['exchange'] = exchange_name
        out['list_status'] = 'L'
        return out

    def get_stock_data(self, symbols: Union[str, List[str]], start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        if not symbols:
            raise DataProviderError("No symbol provided")

        def _fetch_single(ts: str) -> pd.DataFrame:
            ts_code = self._ensure_ts_code(ts)
            start = convert_trade_date(start_date)
            end = convert_trade_date(end_date)
            cache_key = f"stock_data_{ts_code}_{start}_{end}"
            cached = self.cache.get(cache_key)
            if cached is not None:
                return cached

            df_basic = self.get_basic_information(ts_code)
            df_daily = self.get_ohlcv_data(ts_code, start, end)
            df_daily_basic = self.get_fundamental_data(ts_code, start, end)

            # drop overlaps except keys
            overlap1 = list(set(df_daily.columns) & set(df_daily_basic.columns) - {'ts_code','trade_date'})
            df_db = df_daily_basic.drop(columns=overlap1) if overlap1 else df_daily_basic
            merged = df_daily.merge(df_db, on=['ts_code','trade_date'], how='left')

            overlap2 = list(set(df_basic.columns) & set(merged.columns) - {'ts_code'})
            df_b = df_basic.drop(columns=overlap2) if overlap2 else df_basic
            merged = merged.merge(df_b, on='ts_code', how='left')

            if merged.empty:
                raise DataProviderError(f"No stock data for {ts_code}")

            # Ensure all required fundamental columns exist with proper defaults
            # This must be done after merge to handle cases where merge creates NaN columns
            fund_cols = ['turnover_rate', 'volume_ratio', 'pe', 'circ_mv']
            for col in fund_cols:
                if col not in merged.columns:
                    merged[col] = 100.0 if col == 'volume_ratio' else 0.0
                else:
                    # Fill NaN values with defaults
                    default_val = 100.0 if col == 'volume_ratio' else 0.0
                    merged[col] = _safe_fillna(merged[col], default_val)

            try:
                merged = merged.sort_values('trade_date').reset_index(drop=True)
            except Exception:
                pass
            self.cache.set(cache_key, merged)
            return merged

        if isinstance(symbols, list):
            frames = []
            # Use tqdm for progress tracking when processing multiple symbols
            symbol_iter = tqdm(symbols, desc="Fetching stock data", unit="stock") if len(symbols) > 1 else symbols

            for s in symbol_iter:
                try:
                    frames.append(_fetch_single(s))
                except Exception as e:
                    # Reduce logger output during batch processing
                    if len(symbols) <= 10:  # Only log for small batches
                        logger.warning(f"Skipping {s} due to error: {e}")
            if not frames:
                raise DataProviderError("No data retrieved for any symbol")
            return dfs_concat(frames, ignore_index=True)
        else:
            return _fetch_single(symbols)

    def get_index_data(self, index_code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        if not index_code:
            raise DataProviderError("No index code provided")
        start = convert_trade_date(start_date)
        end = convert_trade_date(end_date)
        cache_key = f"index_data_{index_code}_{start}_{end}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Retrieved index data from cache for {index_code}")
            return cached
        symbol = self._index_ts_to_ak_symbol(index_code)
        # Prefer Eastmoney endpoint
        df = self._ak_call(ak.stock_zh_index_daily_em, symbol=symbol)
        if df.empty:
            raise DataProviderError(f"No index data for {index_code}")

        # normalize
        rename_map = {
            '日期': 'trade_date',
            '开盘': 'open',
            '最高': 'high',
            '最低': 'low',
            '收盘': 'close',
            '成交量': 'vol',
            '成交额': 'amount',
        }
        for k, v in rename_map.items():
            if k in df.columns:
                df.rename(columns={k: v}, inplace=True)
        if 'trade_date' in df.columns:
            df['trade_date'] = self._fmt_trade_date_col(df, 'trade_date')
        elif 'date' in df.columns:
            df['trade_date'] = self._fmt_trade_date_col(df, 'date')
        else:
            raise DataProviderError("Missing date column in Akshare index data")

        # filter date range since EM returns all history
        df = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].copy()
        df['ts_code'] = index_code
        cols = ['ts_code','trade_date','open','high','low','close']
        if 'vol' in df.columns:
            cols.append('vol')
        if 'amount' in df.columns:
            cols.append('amount')
        df = df[cols].sort_values('trade_date').reset_index(drop=True)

        self.cache.set(cache_key, df)
        logger.debug(f"Retrieved {len(df)} index rows for {index_code} via Akshare")
        return df

    def get_trading_calendar(self, start_date: str | None = None, end_date: str | None = None) -> List[str]:
        # tool_trade_date_hist_sina returns a list of trade dates
        if not start_date or not end_date:
            raise DataProviderError("Start date and end date are required")
        df = self._ak_call(ak.tool_trade_date_hist_sina)
        # Normalize to DataFrame with cal_date
        if isinstance(df, pd.DataFrame):
            # try common columns
            if 'trade_date' in df.columns:
                cal = pd.to_datetime(df['trade_date']).dt.strftime('%Y%m%d')
            elif '日期' in df.columns:
                cal = pd.to_datetime(df['日期']).dt.strftime('%Y%m%d')
            else:
                # if a single column
                cal = pd.to_datetime(df.iloc[:,0]).dt.strftime('%Y%m%d')
            cal_list = [d for d in cal.tolist() if start_date <= d <= end_date]
        else:
            # Fallback if ak returns list-like
            ser = pd.Series(df)
            cal_list = pd.to_datetime(ser).dt.strftime('%Y%m%d').tolist()
            cal_list = [d for d in cal_list if start_date <= d <= end_date]

        cal_df = pd.DataFrame({'cal_date': sorted(cal_list)})
        logger.debug(f"Retrieved {len(cal_df)} trading days via Akshare")
        return cal_df['cal_date'].tolist()

    def validate_symbols(self, symbols: List[str]) -> Dict[str, bool]:
        try:
            all_info = self.get_basic_information()
            have = set(all_info['ts_code'].tolist())
            result = {}
            for s in symbols:
                ts_code = self._ensure_ts_code(s)
                result[s] = ts_code in have
            return result
        except Exception as e:
            logger.error(f"Akshare symbol validation failed: {e}")
            return {s: False for s in symbols}

    def get_data_quality_report(self, data: pd.DataFrame) -> Dict[str, Any]:
        return DataValidator.get_data_quality_score(data)

    def clear_cache(self, pattern: Optional[str] = None) -> int:
        if not self.cache:
            return 0
        if pattern:
            return self.cache.invalidate(pattern)
        return self.cache.clear_all()

    def get_cache_stats(self) -> Dict[str, Any]:
        return self.cache.get_cache_stats()



class TdxDataProvider(DataProvider):
    """
    Data provider implementation using TDX (Tongdaxin) API.
    Fetching [TDX Docs & API](https://pytdx-docs.readthedocs.io/zh-cn/latest/)
    """
    def __init__(self, rate_limit_delay: float = 0.2, server_config_file: str = "tdx_servers_config.json"):
        """Initialize TDX data provider.

        Args:
            rate_limit_delay: Delay between API calls in seconds
            server_config_file: Path to TDX server configuration file
        """
        self.rate_limit_delay = rate_limit_delay
        self.cache = SQLiteDataCache(db_path=DB_CACHE_FILE)
        self.server_config_file = server_config_file
        self.servers = self._load_server_config()
        self.current_server_index = 0

    def _load_server_config(self) -> List[Dict[str, Any]]:
        """Load TDX server configuration from JSON file."""
        try:
            with open(self.server_config_file, 'r') as f:
                config = json.load(f)
                # Use working_servers with higher priority
                config = json.load(f)
                # Use working_servers with higher priority
                return config.get('working_servers')
        except Exception as e:
            logger.error(f"Failed to load TDX server config: {e}")
            return []

    @retry(
        stop=stop_after_attempt(2),
        wait=wait_random_exponential(multiplier=0.2, min=1, max=2),
        retry=retry_if_exception_type(Exception),
        before_sleep=before_sleep_log(tenacity_logger, logging.INFO)
    )
    def _tdx_call(self, func_lambda):
        time.sleep(self.rate_limit_delay)

        # Track servers tried in this complete call
        servers_tried = 0
        total_servers = len(self.servers)
        last_exc = None

        while servers_tried < total_servers:
            server_info = self.servers[self.current_server_index]
            api = None

            try:
                logger.debug(f"Attempting TDX server: {server_info['name']} ({server_info['ip']}:{server_info['port']})")

                # Create new API instance for each server attempt
                api = TdxHq_API(heartbeat=True, auto_retry=True, raise_exception=True)

                # Connect to server (returns boolean, not context manager)
                if not api.connect(server_info['ip'], server_info['port']):
                    raise DataProviderError(f"Failed to connect to TDX server {server_info['name']}")

                try:
                    # Call the lambda function which will execute the API call
                    df = func_lambda(api)

                    if df is None or not isinstance(df, pd.DataFrame):
                        raise DataProviderError("Invalid response from TDX API")
                    logger.debug(f"✓ TDX server {server_info['name']} responded successfully")
                    return df
                finally:
                    # Ensure disconnection even if API call fails
                    if api and hasattr(api, 'disconnect'):
                        try:
                            api.disconnect()
                        except Exception:
                            pass
            except Exception as e:
                last_exc = e
                logger.warning(f"✗ TDX server {server_info['name']} failed: {e}")

                # Move to next server for subsequent attempts
                self.current_server_index = (self.current_server_index + 1) % len(self.servers)
                servers_tried += 1

                # If this was the last server, refresh config and try again
                if servers_tried >= total_servers:
                    logger.info("🔄 Refreshing TDX server configuration...")
                    try:
                        refresh_tdx_config(self.server_config_file)
                        self.servers = self._load_server_config()
                        self.current_server_index = 0
                        servers_tried = 0  # Reset counter for fresh server list
                    except Exception as refresh_e:
                        logger.error(f"Failed to refresh TDX config: {refresh_e}")
                        break

            finally:
                # Ensure API is properly disconnected
                if api and hasattr(api, 'disconnect'):
                    try:
                        api.disconnect()
                    except Exception:
                        pass

        # If we get here, all servers failed
        raise DataProviderError(f"TDX call failed after trying all {total_servers} servers. Last error: {last_exc}")

    @staticmethod
    def _ensure_ts_code(symbol: str | None) -> str:
        """Return ts_code style like 600519.SH for inputs '600519' or '600519.SH'."""
        if not symbol:
            raise DataProviderError("Empty symbol")
        s = symbol.strip().upper()
        if s.endswith('.SH') or s.endswith('.SZ') or s.endswith('.BJ'):
            return s
        # infer exchange by code prefix rules in A-shares
        if s[0] == '6':
            return f"{s}.SH"
        if s[0] in ('0', '3'):
            return f"{s}.SZ"
        if s[0] in ('4', '8'):
            return f"{s}.BJ"
        # default to SZ if unknown but 6-digit
        if len(s) == 6 and s.isdigit():
            return f"{s}.SZ"
        return s

    @staticmethod
    def _ts_to_tdx_market_code(ts_code: str) -> int:
        """Convert ts_code to TDX market code."""
        if ts_code.endswith('.SH'):
            return 1  # Shanghai
        elif ts_code.endswith('.SZ'):
            return 0  # Shenzhen
        elif ts_code.endswith('.BJ'):
            return 0  # Beijing (treat as Shenzhen)
        else:
            # Try to infer from code
            code = ts_code.split('.')[0]
            if code[0] == '6':
                return 1  # Shanghai
            else:
                return 0  # Shenzhen

    @staticmethod
    def _ts_to_tdx_symbol(ts_code: str) -> str:
        """Convert ts_code to TDX symbol (6-digit code)."""
        return ts_code.split('.')[0]

    @staticmethod
    def _fmt_trade_date_col(df: pd.DataFrame, src_col: str) -> pd.Series:
        """Format trade_date column to YYYYMMDD string."""
        if src_col not in df.columns:
            raise DataProviderError(f"Column {src_col} not found")

        dates = df[src_col]
        if dates.dtype == 'object':
            # Try to parse as string dates
            try:
                dates = pd.to_datetime(dates)
            except Exception:
                return dates

        if hasattr(dates, 'dt'):
            return dates.dt.strftime('%Y%m%d')
        else:
            # Already formatted or numeric
            return dates.astype(str)

    def get_kline(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None, adj: str = "qfq", freq: str = "D") -> pd.DataFrame:
        """ Retrieve k-line(as OHLCV) for specified stock with adj,freq in the specified range."""
        return pd.DataFrame()  # Not implemented yet

    def get_ohlcv_data(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Retrieve daily OHLCV data using TDX API."""
        ts_code = self._ensure_ts_code(symbol)
        market = self._ts_to_tdx_market_code(ts_code)
        stockcode = self._ts_to_tdx_symbol(ts_code)

        start = convert_trade_date(start_date)
        end = convert_trade_date(end_date)

        cache_key = f"ohlcv_data_{ts_code}_{start}_{end}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Retrieved TDX OHLCV data from cache for {ts_code}")
            return cached

        # TDX API call
        bars = self._tdx_call(lambda api: api.get_security_bars(9, market, stockcode, 0, 800))
        if bars is None or (isinstance(bars, pd.DataFrame) and bars.empty):
            logger.warning(f"No OHLCV data found for {ts_code}")
            return pd.DataFrame()

        df = pd.DataFrame(bars)

        # Normalize column names to match Tushare format
        rename_map = {
            'datetime': 'trade_date',
            'open': 'open',
            'high': 'high',
            'low': 'low',
            'close': 'close',
            'vol': 'vol',
            'amount': 'amount'
        }

        df = df.rename(columns=rename_map)

        # Add missing fields
        df['ts_code'] = ts_code
        df['pre_close'] = df['close'].shift(1)
        df['change'] = df['close'] - df['pre_close']
        df['pct_chg'] = (df['change'] / df['pre_close'] * 100).round(2)

        # Format trade_date
        if 'trade_date' in df.columns:
            df['trade_date'] = self._fmt_trade_date_col(df, 'trade_date')

        # Filter by date range
        df = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].copy()

        # Select expected columns
        expected_cols = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'vol', 'amount', 'change', 'pct_chg']
        df = df[[col for col in expected_cols if col in df.columns]]

        df = df.sort_values('trade_date').reset_index(drop=True)

        self.cache.set(cache_key, df)
        logger.debug(f"Retrieved {len(df)} OHLCV records for {ts_code} via TDX")
        return df

    def get_fundamental_data(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """
        Retrieve fundamental data using TDX API.
        Uses multiple TDX API methods to get comprehensive fundamental data:
        1. get_security_bars for historical OHLCV and volume calculations
        2. get_security_quotes for real-time turnover rate and other metrics
        3. get_finance_info for financial data like PE, market cap
        """
        ts_code = self._ensure_ts_code(symbol)
        market = self._ts_to_tdx_market_code(ts_code)
        stockcode = self._ts_to_tdx_symbol(ts_code)

        start = convert_trade_date(start_date)
        end = convert_trade_date(end_date)

        cache_key = f"fundamental_data_{ts_code}_{start}_{end}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Retrieved TDX fundamental data from cache for {ts_code}")
            return cached

        # Get historical K-line data (up to 800 bars to cover the date range)
        bars = self._tdx_call(lambda api: api.get_security_bars(9, market, stockcode, 0, 800))
        if bars is None or (isinstance(bars, pd.DataFrame) and bars.empty):
            logger.warning(f"No historical bars data found for {ts_code}")
            return pd.DataFrame()

        # Convert bars to DataFrame for easier processing
        bars_df = pd.DataFrame(bars)
        if 'datetime' in bars_df.columns:
            bars_df['trade_date'] = pd.to_datetime(bars_df['datetime']).dt.strftime('%Y%m%d')

        # Filter by date range
        bars_df = bars_df[(bars_df['trade_date'] >= start) & (bars_df['trade_date'] <= end)].copy()

        if bars_df.empty:
            logger.warning(f"No bars data in date range {start}-{end} for {ts_code}")
            return pd.DataFrame()

        # Get finance info for PE, market cap, etc.
        finance_info = None
        try:
            finance_info = self._tdx_call(lambda api: api.get_finance_info(market, stockcode))
        except Exception as e:
            logger.debug(f"Could not get finance info for {ts_code}: {e}")

        fundamental_records = []

        # Create fundamental data for each trading day
        for idx, bar in bars_df.iterrows():
            trade_date = bar['trade_date']
            close_price = float(bar.get('close', 0))
            volume = float(bar.get('vol', 0))  # Volume in hands (手)

            # Calculate volume_ratio: current day volume vs previous 5-day average
            volume_ratio = 1.0
            try:
                # Get previous 5 days data for volume calculation
                current_idx = bars_df.index.get_loc(idx)
                # Ensure current_idx is an integer
                if isinstance(current_idx, int):
                    if current_idx >= 4:  # Need at least 4 previous days
                        prev_5_volumes = bars_df.iloc[current_idx-4:current_idx]['vol'].astype(float)
                        avg_vol_5 = prev_5_volumes.mean()
                        if avg_vol_5 > 0:
                            volume_ratio = (volume / avg_vol_5) * 100
            except Exception:
                volume_ratio = 1.0

            # Calculate turnover rate from real-time quotes if available, or estimate
            turnover_rate = 0.0
            if trade_date == bars_df['trade_date'].max():  # Latest date
                # For latest date, calculate turnover from current volume and total shares
                if finance_info is not None and volume > 0:
                    try:
                        # liutongguben is in shares (股), volume is in hands (手, 100 shares each)
                        float_shares_raw = finance_info.get('liutongguben', 0)
                        # Extract scalar from Series if necessary
                        if isinstance(float_shares_raw, pd.Series):
                            float_shares_raw = float_shares_raw.iloc[0] if len(float_shares_raw) > 0 else 0
                        # Coerce to numeric safely
                        try:
                            coerced = pd.to_numeric(float_shares_raw, errors='coerce')
                            if isinstance(coerced, pd.Series):
                                coerced = coerced.iloc[0] if len(coerced) > 0 else None
                            float_shares = float(coerced) if pd.notna(coerced) else 0.0
                        except (ValueError, TypeError):
                            float_shares = 0.0
                        if float_shares > 0:
                            # turnover_rate = (volume_in_shares / float_shares) * 100
                            volume_in_shares = volume * 100  # Convert hands to shares
                            turnover_rate = (volume_in_shares / float_shares) * 100
                    except Exception as e:
                        logger.debug(f"Error calculating turnover rate for latest date: {e}")

            # For historical dates, estimate turnover rate based on volume and float shares
            # Extract scalar value if turnover_rate is a Series
            tr_scalar = turnover_rate.iloc[0] if isinstance(turnover_rate, pd.Series) else turnover_rate
            try:
                tr_scalar = float(pd.to_numeric(tr_scalar, errors='coerce')) if pd.notna(tr_scalar) else 0.0
            except Exception:
                tr_scalar = 0.0
            if tr_scalar == 0 and finance_info is not None and volume > 0:
                try:
                    # Use float shares from finance info to estimate turnover rate
                    float_shares_raw = finance_info.get('liutongguben', 0)
                    # Extract scalar from DataFrame/Series if necessary
                    if isinstance(float_shares_raw, pd.DataFrame):
                        np_vals = float_shares_raw.to_numpy()
                        float_shares_raw = np_vals[0, 0] if np_vals.size > 0 else 0
                    elif isinstance(float_shares_raw, pd.Series):
                        float_shares_raw = float_shares_raw.iloc[0] if not float_shares_raw.empty else 0
                    # Coerce to numeric scalar
                    coerced = pd.to_numeric(float_shares_raw, errors='coerce')
                    if isinstance(coerced, pd.Series):
                        coerced = coerced.iloc[0] if len(coerced) > 0 else None
                    float_shares_val = float(coerced) if pd.notna(coerced) else 0.0
                    if float_shares_val > 0:
                        volume_in_shares = volume * 100  # Convert hands to shares
                        turnover_rate = (volume_in_shares / float_shares_val) * 100
                except Exception as e:
                    logger.debug(f"Error calculating historical turnover rate: {e}")
                    logger.debug(f"Error calculating historical turnover rate: {e}")

            # Calculate market cap metrics
            total_mv = 0.0  # Total market value in 万元
            circ_mv = 0.0   # Circulation market value in 万元
            total_share = 0.0
            float_share = 0.0
            free_share = 0.0
            pe = 0.0
            pe_ttm = 0.0
            pb = 0.0

            if finance_info is not None and close_price > 0:
                try:
                    # Get share information from finance_info
                    # Note: both zongguben and liutongguben are in shares (股)
                    total_shares = finance_info.get('zongguben', 0)  # 总股本 (股)
                    float_shares = finance_info.get('liutongguben', 0)  # 流通股本 (股)

                    # Extract scalar values if they are Series
                    if isinstance(total_shares, pd.Series):
                        total_shares = total_shares.iloc[0] if len(total_shares) > 0 else 0
                    if isinstance(float_shares, pd.Series):
                        float_shares = float_shares.iloc[0] if len(float_shares) > 0 else 0

                    # Ensure numeric types for comparison - convert to float to avoid Series comparison issues
                    try:
                        # Extract scalar if Series before converting to float
                        if isinstance(total_shares, pd.Series):
                            total_shares = total_shares.iloc[0] if len(total_shares) > 0 else 0
                        total_shares = float(pd.to_numeric(total_shares, errors='coerce'))
                    except (ValueError, TypeError):
                        total_shares = 0.0

                    try:
                        # Extract scalar if Series before converting to float
                        if isinstance(float_shares, pd.Series):
                            float_shares = float_shares.iloc[0] if len(float_shares) > 0 else 0
                        float_shares = float(pd.to_numeric(float_shares, errors='coerce'))
                    except (ValueError, TypeError):
                        float_shares = 0.0

                    # Ensure we have valid numeric values
                    if pd.isna(total_shares):
                        total_shares = 0.0
                    if pd.isna(float_shares):
                        float_shares = 0.0

                    # Ensure scalar comparison
                    total_shares_scalar = float(total_shares) if not isinstance(total_shares, (int, float)) else total_shares
                    float_shares_scalar = float(float_shares) if not isinstance(float_shares, (int, float)) else float_shares

                    if total_shares_scalar > 0:
                        total_share = total_shares_scalar  # Already in shares (股)
                        total_mv = close_price * total_shares_scalar / 10000  # Convert to 万元

                        if float_shares_scalar > 0:
                            float_share = float_shares_scalar  # Already in shares (股)
                            circ_mv = close_price * float_shares_scalar / 10000  # Convert to 万元
                            free_share = float_share  # Assume all float shares are free
                        else:
                            # Estimate float shares as 70% of total if not available
                            float_share = total_share * 0.7
                            circ_mv = total_mv * 0.7
                            free_share = float_share

                    # Calculate PE ratio
                    net_profit = finance_info.get('jinglirun', 0)  # 净利润 (yuan)
                    # Extract scalar value if Series
                    if isinstance(net_profit, pd.Series):
                        net_profit = net_profit.iloc[0] if len(net_profit) > 0 else 0
                    try:
                        net_profit = float(pd.to_numeric(net_profit, errors='coerce'))
                    except (ValueError, TypeError):
                        net_profit = 0.0

                    if pd.notna(net_profit) and net_profit > 0 and total_mv > 0:
                        # PE = Market Cap / Net Profit
                        # total_mv is in 万元, net_profit is in yuan
                        pe = (total_mv * 10000) / net_profit
                        pe_ttm = pe

                    # Calculate PB ratio
                    net_assets = finance_info.get('jingzichan', 0)  # 净资产 (yuan)
                    # Extract scalar value if Series
                    if isinstance(net_assets, pd.Series):
                        net_assets = net_assets.iloc[0] if len(net_assets) > 0 else 0
                    try:
                        net_assets = float(pd.to_numeric(net_assets, errors='coerce'))
                    except (ValueError, TypeError):
                        net_assets = 0.0

                    if pd.notna(net_assets) and net_assets > 0 and total_mv > 0:
                        # PB = Market Cap / Net Assets
                        pb = (total_mv * 10000) / net_assets

                except Exception as e:
                    logger.debug(f"Error calculating metrics from finance info: {e}")

            # Fallback calculation using turnover rate if finance info failed
            if circ_mv == 0 and turnover_rate > 0 and volume > 0 and close_price > 0:
                try:
                    # Estimate float shares: volume / (turnover_rate/100)
                    estimated_float_shares = (volume * 100) / (turnover_rate / 100)
                    float_share = estimated_float_shares
                    free_share = estimated_float_shares
                    circ_mv = close_price * estimated_float_shares / 10000  # Convert to 万元

                    # Estimate total shares (assume float is 70% of total)
                    total_share = estimated_float_shares / 0.7
                    total_mv = close_price * total_share / 10000  # Convert to 万元
                except Exception:
                    pass

            # Build fundamental record for this date
            fundamental_record = {
                'ts_code': ts_code,
                'trade_date': trade_date,
                'close': close_price,
                'turnover_rate': round(turnover_rate, 4),
                'turnover_rate_f': round(turnover_rate, 4),
                'volume_ratio': round(volume_ratio, 2),
                'pe': round(pe, 2) if pe > 0 else 0.0,
                'pe_ttm': round(pe_ttm, 2) if pe_ttm > 0 else 0.0,
                'pb': round(pb, 2) if pb > 0 else 0.0,
                'ps': 0.0,  # Price to Sales not readily available
                'ps_ttm': 0.0,
                'dv_ratio': 0.0,  # Dividend ratio not readily available
                'dv_ttm': 0.0,
                'total_share': round(total_share, 0),
                'float_share': round(float_share, 0),
                'free_share': round(free_share, 0),
                'total_mv': round(total_mv, 2),
                'circ_mv': round(circ_mv, 2),
                'limit_status': None
            }

            fundamental_records.append(fundamental_record)

        if not fundamental_records:
            logger.warning(f"No fundamental records generated for {ts_code}")
            return pd.DataFrame()

        df = pd.DataFrame(fundamental_records)

        # Ensure all expected columns exist
        expected_cols = [
            "ts_code","trade_date","close","turnover_rate","turnover_rate_f","volume_ratio","pe","pe_ttm",
            "pb","ps","ps_ttm","dv_ratio","dv_ttm","total_share","float_share","free_share","total_mv","circ_mv","limit_status"
        ]
        for col in expected_cols:
            if col not in df.columns:
                df[col] = 0.0 if col != 'limit_status' else None

        df = df[expected_cols]
        df = df.sort_values('trade_date').reset_index(drop=True)

        self.cache.set(cache_key, df)
        logger.debug(f"Retrieved {len(df)} fundamental data records for {ts_code} via TDX")
        return df

    def get_basic_information(self, symbol: Optional[str] = None) -> pd.DataFrame:
        """Retrieve basic stock information using TDX API."""
        return AkshareDataProvider().get_basic_information(symbol)

    def get_stock_data(self, symbols: Union[str, List[str]], start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Get stock data for single or multiple symbols."""
        if not symbols:
            return pd.DataFrame()

        def _fetch_single(ts: str) -> pd.DataFrame:
            ohlcv = self.get_ohlcv_data(ts, start_date, end_date)
            fundamental = self.get_fundamental_data(ts, start_date, end_date)

            if ohlcv.empty:
                return pd.DataFrame()

            # Merge OHLCV and fundamental data
            if not fundamental.empty:
                merged = pd.merge(ohlcv, fundamental, on=['ts_code', 'trade_date'], how='left', suffixes=('', '_fund'))
                # Keep OHLCV close, remove fundamental close
                if 'close_fund' in merged.columns:
                    merged = merged.drop('close_fund', axis=1)
            else:
                merged = ohlcv

            # Ensure all required fundamental columns exist with proper defaults
            # This must be done after merge to handle cases where merge creates NaN columns
            fund_cols = ['turnover_rate', 'volume_ratio', 'pe', 'circ_mv']
            for col in fund_cols:
                if col not in merged.columns:
                    merged[col] = 100.0 if col == 'volume_ratio' else 0.0
                else:
                    # Fill NaN values with defaults
                    default_val = 100.0 if col == 'volume_ratio' else 0.0
                    merged[col] = _safe_fillna(merged[col], default_val)

            return merged

        if isinstance(symbols, list):
            all_data = []
            # Use tqdm for progress tracking when processing multiple symbols
            symbol_iter = tqdm(symbols, desc="Fetching stock data via TDX", unit="stock") if len(symbols) > 1 else symbols

            for sym in symbol_iter:
                data = _fetch_single(sym)
                if not data.empty:
                    all_data.append(data)

            if all_data:
                result = dfs_concat(all_data, ignore_index=True)
                return result.sort_values(['ts_code', 'trade_date']).reset_index(drop=True)
            else:
                return pd.DataFrame()
        else:
            return _fetch_single(symbols)

    def get_index_data(self, index_code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Get index data using TDX API."""
        if not index_code:
            raise DataProviderError("No index code provided")

        start = convert_trade_date(start_date)
        end = convert_trade_date(end_date)
        if not start or not end:
            raise DataProviderError("Invalid start_date or end_date after conversion")

        cache_key = f"index_data_{index_code}_{start}_{end}"
        cached = self.cache.get(cache_key)
        if cached is not None:
            logger.debug(f"Retrieved TDX index data from cache for {index_code}")
            return cached

        # Map common index codes
        market = 1 if index_code.startswith('000001') or index_code.startswith('sh') else 0
        # Use the index_code directly as symbol since it's already validated as non-None
        symbol = index_code.replace('sh', '').replace('sz', '').replace('.SH', '').replace('.SZ', '')

        try:
            bars = self._tdx_call(lambda api: api.get_index_bars(9, market, symbol, 0, 800))
            if bars is None or (isinstance(bars, pd.DataFrame) and bars.empty):
                logger.warning(f"No index data found for {index_code}")
                return pd.DataFrame()

            df = pd.DataFrame(bars)

            # Normalize columns
            rename_map = {
                'datetime': 'trade_date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'vol': 'vol',
                'amount': 'amount'
            }
            df = df.rename(columns=rename_map)

            # Format trade_date
            if 'trade_date' in df.columns:
                df['trade_date'] = self._fmt_trade_date_col(df, 'trade_date')

            # Filter by date range
            df = df[(df['trade_date'] >= start) & (df['trade_date'] <= end)].copy()
            df['ts_code'] = index_code

            cols = ['ts_code','trade_date','open','high','low','close']
            if 'vol' in df.columns:
                cols.append('vol')
            if 'amount' in df.columns:
                cols.append('amount')

            df = df[cols].sort_values('trade_date').reset_index(drop=True)

            self.cache.set(cache_key, df)
            logger.debug(f"Retrieved {len(df)} index rows for {index_code} via TDX")
            return df
        except Exception as e:
            logger.error(f"Error retrieving index data for {index_code} via TDX: {e}")
            return pd.DataFrame()

    def get_trading_calendar(self, start_date: str | None = None, end_date: str | None = None) -> List[str]:
        """Get trading calendar. TDX doesn't provide calendar API, so use akshare."""
        if not start_date or not end_date:
            raise DataProviderError("Start date and end date are required")
        return AkshareDataProvider().get_trading_calendar(start_date, end_date)

    def validate_symbols(self, symbols: List[str]) -> Dict[str, bool]:
        """Validate symbol existence."""
        result = {}
        basic_info = self.get_basic_information()
        valid_symbols = set(basic_info['ts_code'].tolist())

        for symbol in symbols:
            ts_code = self._ensure_ts_code(symbol)
            result[symbol] = ts_code in valid_symbols

        return result

    def get_data_quality_report(self, data: pd.DataFrame) -> Dict[str, Any]:
        """Generate data quality report."""
        if data.empty:
            return {"error": "Empty dataset"}

        report = {
            "total_records": len(data),
            "date_range": {
                "start": data['trade_date'].min() if 'trade_date' in data.columns else None,
                "end": data['trade_date'].max() if 'trade_date' in data.columns else None
            },
            "missing_values": data.isnull().sum().to_dict(),
            "data_types": data.dtypes.to_dict(),
            "symbols_count": data['ts_code'].nunique() if 'ts_code' in data.columns else 0
        }

        return report

    def clear_cache(self, pattern: Optional[str] = None) -> int:
        """Clear cache entries."""
        return self.cache.clear_cache(pattern)

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get cache statistics."""
        return self.cache.get_cache_stats()


if __name__ == "__main__":
    from .. import data_provider
    #data_provider.cache.clear_all() # Only for rebuild cache db.
    # Incremental population of cache db, default is Y-01-01 to now.
    #data_provider.cache.bulk_populate_daily_data(data_provider)
    #exit(0)

    #df = data_provider.get_stock_data("000001.SZ", "2025-06-01", "2025-07-31")
    #df = data_provider.get_index_data("000001.SH", "2025-06-01", "2025-07-31")
    df = data_provider.get_index_data("000510.SH", "2025-06-01", "2025-07-31")
    print(df.shape)
    print(df.columns)
    print(df.head(1))
