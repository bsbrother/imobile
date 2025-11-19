"""
Base interfaces and abstract classes for ibacktest components.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Any, Union
import pandas as pd


class DataProvider(ABC):
    """Abstract interface for data providers."""

    @abstractmethod
    def get_basic_information(self, symbol: str | None = None) -> pd.DataFrame:
        """Retrieve basic information for specified stock or all stocks if <symbol> is None."""
        pass

    @abstractmethod
    def get_kline(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None, adj: str = "qfq", freq: str = "D") -> pd.DataFrame:
        """ Retrieve k-line(as OHLCV) for specified stock by adj,freq in the specified range."""

    @abstractmethod
    def get_ohlcv_data(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Retrieve daily OHLCV(open, high, low, close, volume etc.) data without adj for specified stock."""
        pass

    @abstractmethod
    def get_fundamental_data(self, symbol: str | None = None, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Retrieve daily fundamental data (turnover_rate, volume_ratio, pe, dv_ttm, circ_mv etc.) for specified stock."""
        pass

    @abstractmethod
    def get_stock_data(self, symbols: Union[str, List[str]], start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Retrieve basic information, daily OHLCV and fundamental data for specified stock(s)."""
        pass

    @abstractmethod
    def get_index_data(self, index_code: str, start_date: str | None = None, end_date: str | None = None) -> pd.DataFrame:
        """Retrieve index data for benchmarking."""
        pass

    @abstractmethod
    def get_trading_calendar(self, start_date: str | None = None, end_date: str | None = None) -> List[str]:
        """Get list of trading dates in the specified range."""
        pass


class Strategy(ABC):
    """Abstract base class for trading strategies."""

    @abstractmethod
    def init(self):
        """Initialize strategy with data and parameters."""
        pass

    @abstractmethod
    def next(self):
        """Execute strategy logic for current bar."""
        pass

    @abstractmethod
    def should_buy(self, symbol: str, data: pd.DataFrame) -> bool:
        """Determine if should buy a stock."""
        pass

    @abstractmethod
    def should_sell(self, symbol: str, data: pd.DataFrame, position: Any) -> bool:
        """Determine if should sell a position."""
        pass


class MarketPatternDetector(ABC):
    """Abstract interface for market pattern detection."""

    @abstractmethod
    def detect_pattern(self, market_data: pd.DataFrame, trade_date: str) -> str:
        """Detect current market pattern."""
        pass

    @abstractmethod
    def get_confidence(self, pattern: str, market_data: pd.DataFrame) -> float:
        """Get confidence score for detected pattern."""
        pass


class StockPicker(ABC):
    """Abstract interface for stock selection."""

    @abstractmethod
    def pick_stocks(self, trade_date: str, max_size: int = 10) -> List[str]:
        """Select top stocks for given trade date."""
        pass

    @abstractmethod
    def calculate_score(self, symbol: str, trade_date: str) -> float:
        """Calculate score for a stock."""
        pass


class PerformanceAnalyzer(ABC):
    """Abstract interface for performance analysis."""

    @abstractmethod
    def analyze(self, backtest_result: Any) -> Dict[str, Any]:
        """Analyze backtest results and return performance metrics."""
        pass

    @abstractmethod
    def compare_to_benchmark(self, returns: pd.Series, benchmark: str) -> Dict[str, float]:
        """Compare strategy returns to benchmark."""
        pass
