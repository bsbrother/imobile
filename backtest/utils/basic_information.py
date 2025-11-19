"""
Stock basic information utility for retrieving and caching stock metadata.
Optimized for high-performance operations with advanced caching mechanisms.

Every day 8:00 AM, use [Tushare API](tushare.pro/document/2?doc_id=25) to fetch newlest and cache stock basic information.
"""

from loguru import logger
from typing import List, Optional, Dict, Any
from datetime import datetime
import pandas as pd

from ..data.cache import get_global_cache

def _is_pickle_fresh(max_age_hours: int = 24) -> bool:
	"""Check if basic info cache exists and is fresh.

	Args:
		max_age_hours: Maximum age for cache freshness
	"""
	try:
		cache = get_global_cache()
		basic_info_data = cache.get("basic_information")
		
		if basic_info_data is None:
			return False
			
		# Check if cache has timestamp and age
		saved_timestamp = basic_info_data.get('saved_at')
		if saved_timestamp:
			try:
				saved_time = datetime.fromisoformat(saved_timestamp)
				age_hours = (datetime.now() - saved_time).total_seconds() / 3600.0
				return age_hours <= max_age_hours
			except Exception:
				pass
		return True  # If no timestamp, assume fresh
	except Exception as e:  # pragma: no cover - defensive
		logger.warning(f"Failed checking basic info cache freshness: {e}")
		return False


def _load_from_pickle() -> Optional[pd.DataFrame]:
	"""Load DataFrame from cache if present."""
	try:
		cache = get_global_cache()
		basic_info_data = cache.get("basic_information")
		
		if basic_info_data is None:
			return None
			
		if isinstance(basic_info_data, dict) and 'data' in basic_info_data:  # new format
			df = basic_info_data['data']
		else:  # legacy direct DF
			df = basic_info_data
			
		if not isinstance(df, pd.DataFrame):
			logger.error("Basic info cache content is not a DataFrame")
			return None
			
		logger.info(f"Loaded basic information cache from cache ({len(df)} rows)")
		return df
	except Exception as e:
		logger.error(f"Failed to load basic info from cache: {e}")
		return None


def _save_to_pickle(df: pd.DataFrame) -> bool:
	"""Persist DataFrame to cache."""
	try:
		if not len(df):
			logger.error("Cannot save empty DataFrame to cache")
			return False
			
		cache_data = {
			'data': df, 
			'saved_at': datetime.now().isoformat(), 
			'version': '1.0'
		}
		
		cache = get_global_cache()
		# Cache for 24 hours by default (24 * 3600 seconds)
		success = cache.set("basic_information", cache_data, ttl=24*3600)
		
		if success:
			logger.info(f"Saved basic information cache to cache ({len(df)} rows)")
		else:
			logger.error("Failed to save basic information to cache")
			
		return success
	except Exception as e:
		logger.error(f"Failed to save basic info to cache: {e}")
		return False


class BasicInformationCache:
	"""High-performance in-memory + pickle cached access to stock basic information.

	Data Source: Tushare Pro stock_basic endpoint
	Docs: https://tushare.pro/document/2?doc_id=25

	Fetch Policy:
		- At initialization tries to load pickle (if fresh)
		- If stale or missing, loads from API (full universe) once
		- Supports on-demand incremental refresh (force)
		- Daily auto-refresh helper (after 08:00 local time) via ensure_daily_refresh()

	Provided helpers:
		- get(symbol) -> dict row
		- search(name_substr)
		- filter(**criteria)
		- list_all() -> DataFrame (copy)
		- ensure_daily_refresh()
		- stats()
	"""

	REQUIRED_COLUMNS = [
		'ts_code', 'symbol', 'name', 'area', 'industry', 'cnspell', 'market', 'list_date', 'act_name', 'act_ent_type', 'fullname',
		'enname', 'exchange', 'curr_type', 'list_status', 'delist_date', 'is_hs'
	]

	def __init__(self, try_pickle_first: bool = True, max_age_hours: int = 24):
		self._df: Optional[pd.DataFrame] = None
		self._index: Dict[str, Dict[str, Any]] = {}
		self._last_refresh: Optional[datetime] = None
		self._max_age_hours = max_age_hours

		if try_pickle_first and _is_pickle_fresh(max_age_hours):
			df = _load_from_pickle()
			if df is not None:
				self._initialize(df)
		if self._df is None:
			self.refresh(force=True)

	# ---------------- Core internal helpers -----------------
	def _initialize(self, df: pd.DataFrame):
		# Normalize columns to lower-case expectation
		# (Tushare already provides expected names; just ensure presence)
		missing = [c for c in self.REQUIRED_COLUMNS if c not in df.columns]
		if missing:
			logger.warning(f"Basic info data missing expected columns: {missing}")
		# Convert list_date to datetime for easier filtering (keep original column as string too)
		if 'list_date' in df.columns:
			try:
				df['list_date_dt'] = pd.to_datetime(df['list_date'], errors='coerce')
			except Exception:
				pass
		self._df = df.copy()
		# Build symbol indices (ts_code unique)
		self._index.clear()
		for _, row in self._df.iterrows():
			self._index[str(row['ts_code'])] = row.to_dict()
		self._last_refresh = datetime.now()
		logger.info(f"Basic info cache initialized with {len(self._index)} symbols")

	def _fetch_from_api(self) -> pd.DataFrame:
		logger.info("Fetching full stock basic information from Tushare API ...")
		# Local import to avoid circular dependency at module import time
		from .. import data_provider  # type: ignore
		df = data_provider.get_basic_information_api()
		if df is None or df.empty:
			raise RuntimeError("Received empty basic information from data provider")
		return df

	# ---------------- Public API -----------------
	def refresh(self, force: bool = False) -> bool:
		"""Refresh cache from API if force or stale."""
		if not force and self._last_refresh:
			age_hours = (datetime.now() - self._last_refresh).total_seconds() / 3600.0
			if age_hours <= self._max_age_hours:
				return False  # not refreshed
		try:
			df = self._fetch_from_api()
			self._initialize(df)
			_save_to_pickle(self._df)
			return True
		except Exception as e:
			logger.error(f"Failed refreshing basic info: {e}")
			return False

	def ensure_daily_refresh(self, refresh_hour: int = 8) -> bool:
		"""Refresh once per day after specified hour (local time)."""
		now = datetime.now()
		if self._last_refresh is None:
			return self.refresh(force=True)
		if self._last_refresh.date() != now.date() and now.hour >= refresh_hour:
			return self.refresh(force=True)
		return False

	def get(self, symbol: str) -> Optional[Dict[str, Any]]:
		if not symbol:
			return None
		# Accept raw '000001.SZ' or ts_code; Tushare uses ts_code with suffix.
		return self._index.get(symbol) or self._index.get(symbol.upper())

	def list_all(self) -> pd.DataFrame:
		return self._df.copy() if self._df is not None else pd.DataFrame()

	def search(self, name_substr: str) -> List[Dict[str, Any]]:
		if not name_substr:
			return []
		name_substr = name_substr.lower()
		return [row for row in self._index.values() if str(row.get('name', '')).lower().find(name_substr) >= 0]

	def filter(self, **criteria) -> List[Dict[str, Any]]:
		if not criteria:
			return list(self._index.values())
		results = []
		for row in self._index.values():
			ok = True
			for k, v in criteria.items():
				if isinstance(v, (list, tuple, set)):
					if row.get(k) not in v:
						ok = False
						break
				else:
					if row.get(k) != v:
						ok = False
						break
			if ok:
				results.append(row)
		return results

	def stats(self) -> Dict[str, Any]:
		return {
			'symbols': len(self._index),
			'last_refresh': self._last_refresh.isoformat() if self._last_refresh else None,
			'cache_enabled': True,
		}


_basic_info_cache: Optional[BasicInformationCache] = None


def initialize_basic_info_cache(try_pickle_first: bool = True) -> BasicInformationCache:
	global _basic_info_cache
	if _basic_info_cache is None:
		_basic_info_cache = BasicInformationCache(try_pickle_first=try_pickle_first)
	return _basic_info_cache


def get_basic_info_cache() -> BasicInformationCache:
	return initialize_basic_info_cache(try_pickle_first=True)


# Convenience functions mirroring trading_calendar util style
def get_basic_info(symbol: str) -> Optional[Dict[str, Any]]:
	return get_basic_info_cache().get(symbol)


def search_basic_info(name_substr: str) -> List[Dict[str, Any]]:
	return get_basic_info_cache().search(name_substr)


def filter_basic_info(**criteria) -> List[Dict[str, Any]]:
	return get_basic_info_cache().filter(**criteria)


def refresh_basic_info(force: bool = False) -> bool:
	return get_basic_info_cache().refresh(force=force)


def main():  # pragma: no cover - manual test helper
	from .. import data_provider, basic_info_cache as cache

	#print(cache.stats())
	print('---------------------')
	ts_code = '600519.SH'
	ts_code = '000006.SZ'
	df_a_row = data_provider.get_basic_information(ts_code)
	print(f'{ts_code} - cache,api -: {df_a_row}')
	row_dict = cache.get(ts_code)
	print(f'{ts_code} - cache     -: {row_dict}')
	df = data_provider.get_basic_information()
	print('All stocks basic info:',df.shape)
	exit(0)
	print('Search 银行:', len(cache.search('银行')))
	print('Filter market=主板:', len(cache.filter(market='主板')))
	print('All stocks:', len(cache.list_all()))


if __name__ == '__main__':  # pragma: no cover
	main()

