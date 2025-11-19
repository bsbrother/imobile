"""
Custom exceptions for ibacktest package.
"""


class IBacktestError(Exception):
    """Base exception for ibacktest package."""
    pass


class DataProviderError(IBacktestError):
    """Errors related to data acquisition and processing."""
    pass


class TushareAPIError(DataProviderError):
    """Specific errors from Tushare API."""
    pass


class TradeValidationError(IBacktestError):
    """Errors in trade validation (T+1, short-selling violations)."""
    pass


class StrategyError(IBacktestError):
    """Errors in strategy execution."""
    pass


class MarketPatternError(IBacktestError):
    """Errors in market pattern detection."""
    pass


class ConfigurationError(IBacktestError):
    """Errors in configuration management."""
    pass


class StockPickerError(IBacktestError):
    """Errors in stock selection process."""
    pass