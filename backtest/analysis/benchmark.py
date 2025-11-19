"""
Benchmark comparison utilities for Chinese market indices.
"""

from typing import Dict, List
import pandas as pd
import numpy as np
from dataclasses import dataclass

from ..core.interfaces import DataProvider
from ..utils.exceptions import IBacktestError

@dataclass
class BenchmarkComparison:
    """Data structure for benchmark comparison results."""

    benchmark_name: str
    strategy_total_return: float
    benchmark_total_return: float
    excess_return: float

    strategy_annualized_return: float
    benchmark_annualized_return: float
    annualized_excess_return: float

    strategy_volatility: float
    benchmark_volatility: float

    correlation: float
    beta: float
    alpha: float

    information_ratio: float
    tracking_error: float

    strategy_sharpe: float
    benchmark_sharpe: float

    strategy_max_drawdown: float
    benchmark_max_drawdown: float

    strategy_max_drawdown_duration: int
    benchmark_max_drawdown_duration: int

    outperformance_days: int
    underperformance_days: int
    outperformance_rate: float


class BenchmarkComparator:
    """Handles benchmark data retrieval and performance comparisons for Chinese market indices."""

    def __init__(self, data_provider: DataProvider):
        """
        Initialize benchmark comparator.

        Args:
            data_provider: Data provider for benchmark data
        """
        self.data_provider = data_provider
        self.risk_free_rate = 0.03  # 3% annual risk-free rate

    def get_multiple_benchmarks(self, benchmarks: List[str], start_date: str, end_date: str) -> Dict[str, pd.Series]:
        """
        Get data for multiple benchmarks.

        Args:
            benchmarks: List of benchmark identifiers
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format

        Returns:
            Dictionary mapping benchmark names to price series
        """
        benchmark_data = {}

        for benchmark in benchmarks:
            try:
                # Get benchmark code
                benchmark_code = self._get_benchmark_code(benchmark)

                # Fetch data from provider using get_index_data directly
                raw_data = self.data_provider.get_index_data(
                    benchmark_code, start_date, end_date
                )

                if raw_data.empty:
                    print(f"Warning: No data found for benchmark {benchmark}")
                    continue

                # Process the data to extract price series
                processed_data = raw_data.copy()

                # Set the date index if trade_date column exists
                if 'trade_date' in processed_data.columns:
                    # Convert trade_date to datetime and set as index
                    processed_data['trade_date'] = pd.to_datetime(processed_data['trade_date'], format='%Y%m%d')
                    processed_data = processed_data.set_index('trade_date')

                # Extract closing prices
                if 'close' in processed_data.columns:
                    price_series = processed_data['close']
                elif 'Close' in processed_data.columns:
                    price_series = processed_data['Close']
                else:
                    # Try to find price column
                    price_cols = [col for col in processed_data.columns
                                 if 'close' in col.lower() or 'price' in col.lower()]
                    if price_cols:
                        price_series = processed_data[price_cols[0]]
                    else:
                        print(f"Warning: No price column found in benchmark data for {benchmark}")
                        continue

                # Ensure datetime index
                if not isinstance(price_series.index, pd.DatetimeIndex):
                    price_series.index = pd.to_datetime(price_series.index)

                benchmark_data[benchmark] = price_series

            except Exception as e:
                print(f"Warning: Could not get data for benchmark {benchmark}: {str(e)}")

        return benchmark_data

    def compare_returns(self, strategy_returns: pd.Series, benchmark_returns: pd.Series,
                       benchmark_name: str = "Benchmark") -> BenchmarkComparison:
        """
        Compare strategy returns to benchmark returns.

        Args:
            strategy_returns: Strategy daily returns
            benchmark_returns: Benchmark daily returns
            benchmark_name: Name of the benchmark for labeling

        Returns:
            BenchmarkComparison object with detailed comparison metrics
        """
        try:
            # Align data on common dates
            aligned_data = pd.DataFrame({
                'strategy': strategy_returns,
                'benchmark': benchmark_returns
            }).dropna()

            if aligned_data.empty:
                raise IBacktestError("No overlapping data between strategy and benchmark")

            strategy_aligned = aligned_data['strategy']
            benchmark_aligned = aligned_data['benchmark']

            # Calculate basic return metrics
            strategy_total_return = (1 + strategy_aligned).prod() - 1
            benchmark_total_return = (1 + benchmark_aligned).prod() - 1
            excess_return = strategy_total_return - benchmark_total_return

            # Calculate annualized returns
            trading_days = len(strategy_aligned)
            years = trading_days / 252

            strategy_annualized = (1 + strategy_total_return) ** (1 / years) - 1 if years > 0 else 0
            benchmark_annualized = (1 + benchmark_total_return) ** (1 / years) - 1 if years > 0 else 0
            annualized_excess = strategy_annualized - benchmark_annualized

            # Calculate volatility
            strategy_volatility = strategy_aligned.std() * np.sqrt(252)
            benchmark_volatility = benchmark_aligned.std() * np.sqrt(252)

            # Calculate correlation and beta
            correlation = strategy_aligned.corr(benchmark_aligned)

            if benchmark_volatility > 0:
                beta = strategy_aligned.cov(benchmark_aligned) / benchmark_aligned.var()
            else:
                beta = 0.0

            # Calculate alpha (CAPM)
            daily_risk_free = self.risk_free_rate / 252
            alpha = (strategy_aligned.mean() - daily_risk_free) - beta * (benchmark_aligned.mean() - daily_risk_free)
            alpha_annualized = alpha * 252

            # Calculate information ratio and tracking error
            excess_returns = strategy_aligned - benchmark_aligned
            tracking_error = excess_returns.std() * np.sqrt(252)

            if tracking_error > 0:
                information_ratio = (excess_returns.mean() * 252) / tracking_error
            else:
                information_ratio = 0.0

            # Calculate Sharpe ratios
            strategy_sharpe = self._calculate_sharpe_ratio(strategy_aligned)
            benchmark_sharpe = self._calculate_sharpe_ratio(benchmark_aligned)

            # Calculate maximum drawdowns
            strategy_max_drawdown = self._calculate_max_drawdown_from_returns(strategy_aligned)
            benchmark_max_drawdown = self._calculate_max_drawdown_from_returns(benchmark_aligned)

            # Calculate outperformance statistics
            outperformance_days = (strategy_aligned > benchmark_aligned).sum()
            underperformance_days = (strategy_aligned < benchmark_aligned).sum()
            total_days = len(strategy_aligned)
            outperformance_rate = outperformance_days / total_days if total_days > 0 else 0

            return BenchmarkComparison(
                benchmark_name=benchmark_name,
                strategy_total_return=strategy_total_return,
                benchmark_total_return=benchmark_total_return,
                excess_return=excess_return,

                strategy_annualized_return=strategy_annualized,
                benchmark_annualized_return=benchmark_annualized,
                annualized_excess_return=annualized_excess,

                strategy_volatility=strategy_volatility,
                benchmark_volatility=benchmark_volatility,

                correlation=correlation,
                beta=beta,
                alpha=alpha_annualized,

                information_ratio=information_ratio,
                tracking_error=tracking_error,

                strategy_sharpe=strategy_sharpe,
                benchmark_sharpe=benchmark_sharpe,

                strategy_max_drawdown=strategy_max_drawdown,
                benchmark_max_drawdown=benchmark_max_drawdown,

                strategy_max_drawdown_duration=0,  # Not calculated in this method
                benchmark_max_drawdown_duration=0,  # Not calculated in this method

                outperformance_days=outperformance_days,
                underperformance_days=underperformance_days,
                outperformance_rate=outperformance_rate
            )

        except Exception as e:
            raise IBacktestError(f"Benchmark comparison failed: {str(e)}") from e

    def compare_with_total_return(self, strategy_total_return: float, strategy_returns: pd.Series,
                                benchmark_returns: pd.Series, benchmark_name: str = "Benchmark") -> BenchmarkComparison:
        """
        Compare strategy to benchmark using provided total return for consistency.

        Args:
            strategy_total_return: Pre-calculated strategy total return from backtest
            strategy_returns: Strategy daily returns (for volatility and correlation analysis)
            benchmark_returns: Benchmark daily returns
            benchmark_name: Name of the benchmark for labeling

        Returns:
            BenchmarkComparison object with detailed comparison metrics
        """
        try:
            # Align data on common dates for volatility and correlation calculations
            aligned_data = pd.DataFrame({
                'strategy': strategy_returns,
                'benchmark': benchmark_returns
            }).dropna()

            if aligned_data.empty:
                raise IBacktestError("No overlapping data between strategy and benchmark")

            strategy_aligned = aligned_data['strategy']
            benchmark_aligned = aligned_data['benchmark']

            # Use provided strategy total return for consistency with backtest summary
            benchmark_total_return = (1 + benchmark_aligned).prod() - 1
            excess_return = strategy_total_return - benchmark_total_return

            # Calculate annualized returns
            trading_days = len(strategy_aligned)
            years = trading_days / 252

            strategy_annualized = (1 + strategy_total_return) ** (1 / years) - 1 if years > 0 else 0
            benchmark_annualized = (1 + benchmark_total_return) ** (1 / years) - 1 if years > 0 else 0
            annualized_excess = strategy_annualized - benchmark_annualized

            # Calculate volatility
            strategy_volatility = strategy_aligned.std() * np.sqrt(252)
            benchmark_volatility = benchmark_aligned.std() * np.sqrt(252)

            # Calculate correlation and beta
            correlation = strategy_aligned.corr(benchmark_aligned)

            if benchmark_volatility > 0:
                beta = strategy_aligned.cov(benchmark_aligned) / benchmark_aligned.var()
            else:
                beta = 0.0

            # Calculate alpha (using CAPM: alpha = strategy_return - risk_free_rate - beta * (benchmark_return - risk_free_rate))
            # Assuming risk-free rate is approximately 3% annually (0.03)
            risk_free_rate = 0.03
            alpha = strategy_annualized - risk_free_rate - beta * (benchmark_annualized - risk_free_rate)

            # Calculate information ratio and tracking error
            if len(strategy_aligned) > 1:
                excess_returns = strategy_aligned - benchmark_aligned
                tracking_error = excess_returns.std() * np.sqrt(252)
                information_ratio = annualized_excess / tracking_error if tracking_error > 0 else 0.0
            else:
                tracking_error = 0.0
                information_ratio = 0.0

            # Calculate Sharpe ratios
            strategy_sharpe = (strategy_annualized - risk_free_rate) / strategy_volatility if strategy_volatility > 0 else 0.0
            benchmark_sharpe = (benchmark_annualized - risk_free_rate) / benchmark_volatility if benchmark_volatility > 0 else 0.0

            # Calculate max drawdowns
            strategy_max_drawdown = self._calculate_max_drawdown_from_returns(strategy_aligned)
            benchmark_max_drawdown = self._calculate_max_drawdown_from_returns(benchmark_aligned)

            # Calculate maximum drawdown duration
            strategy_max_drawdown_duration = self._calculate_max_drawdown_duration_from_returns(strategy_aligned)
            benchmark_max_drawdown_duration = self._calculate_max_drawdown_duration_from_returns(benchmark_aligned)

            # Calculate outperformance statistics
            if len(strategy_aligned) > 0:
                outperformance = strategy_aligned > benchmark_aligned
                outperformance_days = int(outperformance.sum())
                underperformance_days = int((~outperformance).sum())
                outperformance_rate = outperformance_days / len(strategy_aligned) if len(strategy_aligned) > 0 else 0.0
            else:
                outperformance_days = 0
                underperformance_days = 0
                outperformance_rate = 0.0

            return BenchmarkComparison(
                benchmark_name=benchmark_name,
                strategy_total_return=strategy_total_return,
                benchmark_total_return=benchmark_total_return,
                excess_return=excess_return,

                strategy_annualized_return=strategy_annualized,
                benchmark_annualized_return=benchmark_annualized,
                annualized_excess_return=annualized_excess,

                strategy_volatility=strategy_volatility,
                benchmark_volatility=benchmark_volatility,

                correlation=correlation,
                beta=beta,
                alpha=alpha,

                information_ratio=information_ratio,
                tracking_error=tracking_error,

                strategy_sharpe=strategy_sharpe,
                benchmark_sharpe=benchmark_sharpe,

                strategy_max_drawdown=strategy_max_drawdown,
                benchmark_max_drawdown=benchmark_max_drawdown,

                strategy_max_drawdown_duration=strategy_max_drawdown_duration,
                benchmark_max_drawdown_duration=benchmark_max_drawdown_duration,

                outperformance_days=outperformance_days,
                underperformance_days=underperformance_days,
                outperformance_rate=outperformance_rate
            )

        except Exception as e:
            raise IBacktestError(f"Benchmark comparison failed: {str(e)}") from e

    def compare_to_multiple_benchmarks_with_total_return(self, strategy_total_return: float,
                                                       strategy_returns: pd.Series,
                                                       benchmarks: List[str],
                                                       start_date: str,
                                                       end_date: str) -> Dict[str, BenchmarkComparison]:
        """
        Compare strategy to multiple benchmarks using provided total return for consistency.

        Args:
            strategy_total_return: Pre-calculated strategy total return from backtest
            strategy_returns: Strategy daily returns (for volatility and correlation analysis)
            benchmarks: List of benchmark identifiers
            start_date: Start date for benchmark data
            end_date: End date for benchmark data

        Returns:
            Dictionary mapping benchmark names to comparison results
        """
        comparisons = {}

        # Get benchmark data
        benchmark_data = self.get_multiple_benchmarks(benchmarks, start_date, end_date)
        if not benchmark_data:
            raise IBacktestError("No benchmark data available for comparison")

        for benchmark_name, benchmark_prices in benchmark_data.items():
            try:
                # Calculate benchmark returns
                benchmark_returns = benchmark_prices.pct_change().dropna()

                # Use the new method that accepts total return directly
                comparison = self.compare_with_total_return(
                    strategy_total_return, strategy_returns, benchmark_returns, benchmark_name
                )
                comparisons[benchmark_name] = comparison

            except Exception as e:
                print(f"Warning: Failed to compare with {benchmark_name}: {str(e)}")
                continue

        return comparisons

    def compare_to_multiple_benchmarks(self, strategy_returns: pd.Series,
                                     benchmarks: List[str],
                                     start_date: str,
                                     end_date: str) -> Dict[str, BenchmarkComparison]:
        """
        Compare strategy to multiple benchmarks.

        Args:
            strategy_returns: Strategy daily returns
            benchmarks: List of benchmark identifiers
            start_date: Start date for benchmark data
            end_date: End date for benchmark data

        Returns:
            Dictionary mapping benchmark names to comparison results
        """
        comparisons = {}

        # Get benchmark data
        benchmark_data = self.get_multiple_benchmarks(benchmarks, start_date, end_date)
        if not benchmark_data:
            raise IBacktestError("No benchmark data available for comparison")

        for benchmark_name, benchmark_prices in benchmark_data.items():
            try:
                # Calculate benchmark returns
                benchmark_returns = benchmark_prices.pct_change().dropna()

                # Compare to strategy
                comparison = self.compare_returns(
                    strategy_returns, benchmark_returns, benchmark_name
                )
                comparisons[benchmark_name] = comparison

            except Exception as e:
                print(f"Warning: Could not compare to benchmark {benchmark_name}: {str(e)}")

        return comparisons

    def get_relative_performance_series(self, strategy_returns: pd.Series,
                                      benchmark_returns: pd.Series) -> pd.Series:
        """
        Calculate relative performance series (strategy vs benchmark).

        Args:
            strategy_returns: Strategy daily returns
            benchmark_returns: Benchmark daily returns

        Returns:
            Series of relative performance (strategy cumulative return / benchmark cumulative return)
        """
        # Align series
        aligned_data = pd.DataFrame({
            'strategy': strategy_returns,
            'benchmark': benchmark_returns
        }).dropna()

        if aligned_data.empty:
            return pd.Series(dtype=float)

        # Calculate cumulative returns
        strategy_cumulative = (1 + aligned_data['strategy']).cumprod()
        benchmark_cumulative = (1 + aligned_data['benchmark']).cumprod()

        # Calculate relative performance
        relative_performance = strategy_cumulative / benchmark_cumulative

        return relative_performance

    def _get_benchmark_code(self, benchmark: str) -> str:
        """
        Get the Tushare code for a benchmark.

        Args:
            benchmark: Benchmark identifier

        Returns:
            Tushare benchmark code
        """
        benchmark_upper = benchmark.upper()
        from .. import global_cm
        BENCHMARK_CODES = global_cm.get('init_info.benchmark_codes')
        if benchmark_upper not in BENCHMARK_CODES:
            raise ValueError(f"ERROR: Unknown benchmark {benchmark}")
        return BENCHMARK_CODES[benchmark_upper]


    def _calculate_sharpe_ratio(self, returns: pd.Series) -> float:
        """Calculate Sharpe ratio for a return series."""
        if returns.empty or returns.std() == 0:
            return 0.0

        daily_risk_free = self.risk_free_rate / 252
        excess_returns = returns - daily_risk_free

        return excess_returns.mean() / excess_returns.std() * np.sqrt(252)

    def _calculate_max_drawdown_from_returns(self, returns: pd.Series) -> float:
        """Calculate maximum drawdown from return series."""
        if returns.empty:
            return 0.0

        # Calculate cumulative returns
        cumulative = (1 + returns).cumprod()

        # Calculate running maximum
        peak = cumulative.expanding().max()

        # Calculate drawdown
        drawdown = (cumulative - peak) / peak

        return abs(drawdown.min())

    def _calculate_max_drawdown_duration_from_returns(self, returns: pd.Series) -> int:
        """Calculate maximum drawdown duration in days from return series."""
        if returns.empty:
            return 0

        # Calculate cumulative returns
        cumulative = (1 + returns).cumprod()

        # Calculate running maximum
        peak = cumulative.expanding().max()

        # Calculate drawdown
        drawdown = (cumulative - peak) / peak

        # Find periods where drawdown is negative (in drawdown)
        in_drawdown = drawdown < 0

        if not in_drawdown.any():
            return 0

        # Calculate consecutive drawdown periods
        drawdown_periods = []
        current_period = 0

        for is_dd in in_drawdown:
            if is_dd:
                current_period += 1
            else:
                if current_period > 0:
                    drawdown_periods.append(current_period)
                current_period = 0

        # Don't forget the last period if it ends in drawdown
        if current_period > 0:
            drawdown_periods.append(current_period)

        return max(drawdown_periods) if drawdown_periods else 0

    def get_available_benchmarks(self) -> Dict[str, str]:
        """
        Get list of available benchmarks.

        Returns:
            Dictionary mapping benchmark names to descriptions
        """
        return {
            'CSI300': '沪深300指数 - 沪深两市300只大盘股',
            'CSI500': '中证500指数 - 中等市值股票',
            'CSIA500': '中证A500指数 - A股500强',
            'SSE50': '上证50指数 - 上海证券交易所50只大盘蓝筹股',
            'SZSE100': '深证100指数 - 深圳证券交易所100只股票',
            'ChiNext': '创业板指数 - 创业板市场代表性股票',
            'STAR50': '科创50指数 - 科创板50只股票'
        }
