"""
ibacktest - China A-shares Backtesting Framework

A specialized backtesting system designed for China A-shares market with T+1 trading rules,
no short-selling constraints, and integrated Tushare data provider.
"""

__version__ = "0.1.0"
__author__ = "ibacktest"

import os
from loguru import logger
from dotenv import load_dotenv

from .utils.config import ConfigManager
from .utils.logging_config import configure_logger

if not os.path.exists('.env'):
    raise FileNotFoundError("Error: .env file not found. Please create it in the home directory.")
load_dotenv('.env', verbose=True, override=True)

"""Module-level configuration and lazy singletons.

Import order is important to avoid circular import issues. We intentionally
delay importing heavy submodules (like data provider) until after all
configuration constants are defined so that utility modules importing these
constants during their own import phase don't trigger circular attribute
lookup failures ("partially initialized module").
"""

# Log level
LOG_LEVEL = os.getenv("LOG_LEVEL", default="DEBUG")

# API Keys and Tokens (must be defined BEFORE importing provider/basic info)
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", default=None)
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN", default=None)

# Paths / Files
CONFIG_FILE = os.getenv("CONFIG_FILE", default="/tmp/ibacktest.json")
LOG_PATH = os.getenv("LOG_PATH", default="/tmp/ibacktest_logs")  # treat as directory
CACHE_PATH = os.getenv("CACHE_PATH", default="/tmp/ibacktest_cache")
CAL_PICKLE_FILE = os.path.expanduser(os.getenv("CAL_PICKLE_FILE", default="/tmp/cal.pkl"))
BASIC_INFO_PICKLE_FILE = os.path.expanduser(os.getenv("BASIC_INFO_PICKLE_FILE", default="/tmp/basic_info.pkl"))
DB_CACHE_FILE = os.getenv("DB_CACHE_FILE", default="/tmp/ibacktest_cache.db")
DB_CACHE_FILE = os.getenv("DB_CACHE_FILE", default="/tmp/ibacktest_cache.db")
WORKING_PROXY_FILE = os.path.expanduser(os.getenv("WORKING_PROXY_FILE", default="/tmp/working_proxies.txt"))

# AI provider
if not GOOGLE_API_KEY:
    raise ValueError("Error: GOOGLE_API_KEY not found. Please set it in your .env file.")

# Logging setup using centralized configuration
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)

global_cm = ConfigManager(config_file=CONFIG_FILE)
provider = global_cm.get('init_info.data_provider')
logger.debug(f"The data provider is {provider}")
if provider == 'tushare':
    if not TUSHARE_TOKEN:
        raise ValueError("Error: TUSHARE_TOKEN not found. Please set it in your .env file or switch provider to 'akshare'.")
    # Import provider AFTER constants to avoid circular import with utils.basic_information
    from .data.provider import TushareDataProvider  # type: ignore  # noqa: E402
    # Global provider singleton
    data_provider = TushareDataProvider(TUSHARE_TOKEN)
elif provider == 'akshare':
    from .data.provider import AkshareDataProvider  # type: ignore  # noqa: E402
    data_provider = AkshareDataProvider()
elif provider == 'tdx':
    from .data.provider import TdxDataProvider  # type: ignore  # noqa: E402
    data_provider = TdxDataProvider()
else:
    raise ValueError(f"Unsupported data provider: {provider}. Supported providers: tushare, akshare.")

# NOTE:
# Importing ibacktest.utils.trading_calendar at package import time caused a RuntimeWarning
# when executing `python -m ibacktest.utils.trading_calendar` because runpy first imports the
# package (executing this file) which re-imported the target module before runpy executed it.
# To eliminate that warning and reduce import side-effects, we lazily initialize the calendar.

_calendar_instance = None

def get_calendar():
    """Lazily obtain (and cache) the global TradingCalendar instance.

    Returns:
        TradingCalendar: initialized trading calendar
    """
    global _calendar_instance
    if _calendar_instance is None:
        # Local import to avoid runpy double-import warning when module executed as a script
        from .utils.trading_calendar import initialize_trading_calendar  # type: ignore
        logger.debug("Creating trading calendar instance (lazy init) with pickle caching support.")
        _calendar_instance = initialize_trading_calendar(
            cache_years=5,
            try_pickle_first=True,
        )
    return _calendar_instance

class _CalendarProxy:
    """Proxy object so existing `from . import calendar` code keeps working.

    Attribute access triggers lazy initialization of the underlying calendar instance.
    """
    def __getattr__(self, item):  # pragma: no cover - trivial delegation
        cal = get_calendar()
        return getattr(cal, item)

    def __repr__(self):  # pragma: no cover - convenience
        status = "initialized" if _calendar_instance else "uninitialized"
        return f"<CalendarProxy {status}>"

calendar = _CalendarProxy()

_basic_info_cache_instance = None

def get_basic_info_cache():  # noqa: D401
    """Lazily obtain (and cache) the global BasicInformationCache instance."""
    global _basic_info_cache_instance
    if _basic_info_cache_instance is None:
        from .utils.basic_information import get_basic_info_cache as _real_get  # type: ignore  # noqa: E402
        _basic_info_cache_instance = _real_get()
        try:
            _basic_info_cache_instance.ensure_daily_refresh()
        except Exception:  # pragma: no cover - best-effort refresh
            logger.debug("Deferred basic info daily refresh during import phase")
    return _basic_info_cache_instance

class _BasicInfoProxy:
    def __getattr__(self, item):  # pragma: no cover - simple delegation
        return getattr(get_basic_info_cache(), item)
    def __repr__(self):  # pragma: no cover
        status = "initialized" if _basic_info_cache_instance else "uninitialized"
        return f"<BasicInfoProxy {status}>"

basic_info_cache = _BasicInfoProxy()

__all__ = [
    # Configuration constants
    "LOG_LEVEL",
    "GOOGLE_API_KEY",
    "TUSHARE_TOKEN",
    "LOG_PATH",
    "CACHE_PATH",
    "CAL_PICKLE_FILE",
    "BASIC_INFO_PICKLE_FILE",
    "DB_CACHE_FILE",
    "data_provider",
    "calendar",
    "get_calendar",
    "basic_info_cache",
    "global_cm",
]
