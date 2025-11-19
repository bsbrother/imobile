"""
Technical indicators for market pattern detection.
"""

import numpy as np
import pandas as pd
from typing import Tuple
from ..utils.util import dfs_concat


class TechnicalIndicators:
    """Collection of technical indicators for market analysis."""

    @staticmethod
    def rsi(data: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate Relative Strength Index (RSI).

        Args:
            data: Price series (typically close prices)
            period: RSI calculation period

        Returns:
            RSI values as pandas Series
        """
        delta = data.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi

    @staticmethod
    def bollinger_bands(data: pd.Series, period: int = 20, std_dev: float = 2.0) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calculate Bollinger Bands.

        Args:
            data: Price series (typically close prices)
            period: Moving average period
            std_dev: Standard deviation multiplier

        Returns:
            Tuple of (upper_band, middle_band, lower_band)
        """
        middle_band = data.rolling(window=period).mean()
        std = data.rolling(window=period).std()
        upper_band = middle_band + (std * std_dev)
        lower_band = middle_band - (std * std_dev)

        return upper_band, middle_band, lower_band

    @staticmethod
    def moving_average(data: pd.Series, period: int) -> pd.Series:
        """
        Calculate Simple Moving Average.

        Args:
            data: Price series
            period: Moving average period

        Returns:
            Moving average as pandas Series
        """
        return data.rolling(window=period).mean()

    @staticmethod
    def exponential_moving_average(data: pd.Series, period: int) -> pd.Series:
        """
        Calculate Exponential Moving Average.

        Args:
            data: Price series
            period: EMA period

        Returns:
            EMA as pandas Series
        """
        return data.ewm(span=period).mean()

    @staticmethod
    def volatility(data: pd.Series, period: int = 20) -> pd.Series:
        """
        Calculate rolling volatility (standard deviation of returns).

        Args:
            data: Price series
            period: Volatility calculation period

        Returns:
            Volatility as pandas Series
        """
        returns = data.pct_change()
        return returns.rolling(window=period).std() * np.sqrt(252)  # Annualized

    @staticmethod
    def macd(data: pd.Series, fast_period: int = 12, slow_period: int = 26, signal_period: int = 9) -> Tuple[pd.Series, pd.Series, pd.Series]:
        """
        Calculate MACD (Moving Average Convergence Divergence).

        Args:
            data: Price series
            fast_period: Fast EMA period
            slow_period: Slow EMA period
            signal_period: Signal line EMA period

        Returns:
            Tuple of (macd_line, signal_line, histogram)
        """
        ema_fast = TechnicalIndicators.exponential_moving_average(data, fast_period)
        ema_slow = TechnicalIndicators.exponential_moving_average(data, slow_period)

        macd_line = ema_fast - ema_slow
        signal_line = TechnicalIndicators.exponential_moving_average(macd_line, signal_period)
        histogram = macd_line - signal_line

        return macd_line, signal_line, histogram

    @staticmethod
    def average_true_range(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
        """
        Calculate Average True Range (ATR).

        Args:
            high: High price series
            low: Low price series
            close: Close price series
            period: ATR calculation period

        Returns:
            ATR as pandas Series
        """
        prev_close = close.shift(1)

        tr1 = high - low
        tr2 = abs(high - prev_close)
        tr3 = abs(low - prev_close)

        true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = true_range.rolling(window=period).mean()

        return atr

    @staticmethod
    def stochastic_oscillator(high: pd.Series, low: pd.Series, close: pd.Series,
                            k_period: int = 14, d_period: int = 3) -> Tuple[pd.Series, pd.Series]:
        """
        Calculate Stochastic Oscillator.

        Args:
            high: High price series
            low: Low price series
            close: Close price series
            k_period: %K calculation period
            d_period: %D smoothing period

        Returns:
            Tuple of (%K, %D)
        """
        lowest_low = low.rolling(window=k_period).min()
        highest_high = high.rolling(window=k_period).max()

        k_percent = 100 * ((close - lowest_low) / (highest_high - lowest_low))
        d_percent = k_percent.rolling(window=d_period).mean()

        return k_percent, d_percent
