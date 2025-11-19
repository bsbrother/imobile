"""
Performance analysis engine.
"""

from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from dataclasses import dataclass

from ..core.interfaces import PerformanceAnalyzer as IPerformanceAnalyzer
from ..utils.exceptions import IBacktestError


@dataclass
class PerformanceReport:
    """Data structure for comprehensive performance analysis results."""
    
    # Basic metrics
    total_return: float
    annualized_return: float
    volatility: float
    sharpe_ratio: float
    max_drawdown: float
    max_drawdown_duration: int
    
    # Trade statistics
    total_trades: int
    winning_trades: int
    losing_trades: int
    win_rate: float
    avg_win: float
    avg_loss: float
    profit_factor: float
    
    # Time series data
    daily_returns: pd.Series
    cumulative_returns: pd.Series
    equity_curve: pd.Series
    drawdown_series: pd.Series
    
    # Trade history
    trade_history: List[Dict[str, Any]]
    
    # Period analysis
    start_date: str
    end_date: str
    trading_days: int
    
    # Risk metrics
    var_95: float  # Value at Risk (95%)
    cvar_95: float  # Conditional Value at Risk (95%)
    calmar_ratio: float
    sortino_ratio: float
    
    # Additional metrics
    best_day: float
    worst_day: float
    positive_days: int
    negative_days: int
    max_consecutive_wins: int
    max_consecutive_losses: int


class PerformanceAnalyzer(IPerformanceAnalyzer):
    """Analyzes backtest results and calculates comprehensive performance metrics."""
    
    def __init__(self, risk_free_rate: float = 0.03):
        """
        Initialize performance analyzer.
        
        Args:
            risk_free_rate: Annual risk-free rate for Sharpe ratio calculation
        """
        self.risk_free_rate = risk_free_rate
    
    def analyze(self, backtest_result: Any) -> PerformanceReport:
        """
        Analyze backtest results and return comprehensive performance metrics.
        
        Args:
            backtest_result: Results from backtesting engine
            
        Returns:
            PerformanceReport containing all calculated metrics
        """
        try:
            # Extract data from backtest result
            equity_curve, trade_history = self._extract_backtest_data(backtest_result)
            
            if equity_curve.empty:
                raise IBacktestError("No equity curve data found in backtest results")
            
            # Calculate daily returns
            daily_returns = self._calculate_daily_returns(equity_curve)
            cumulative_returns = self._calculate_cumulative_returns(daily_returns)
            
            # Calculate basic metrics
            initial_cash = backtest_result.get('initial_cash', 100000.0)
            total_return = self._calculate_total_return(equity_curve, initial_cash)
            annualized_return = self._calculate_annualized_return(daily_returns)
            volatility = self._calculate_volatility(daily_returns)
            sharpe_ratio = self._calculate_sharpe_ratio(daily_returns)
            
            # Calculate drawdown metrics
            drawdown_series = self._calculate_drawdown_series(equity_curve)
            max_drawdown = self._calculate_max_drawdown(drawdown_series)
            max_drawdown_duration = self._calculate_max_drawdown_duration(drawdown_series)
            
            # Calculate trade statistics
            trade_stats = self._calculate_trade_statistics(trade_history)
            
            # Calculate risk metrics
            var_95 = self._calculate_var(daily_returns, confidence=0.95)
            cvar_95 = self._calculate_cvar(daily_returns, confidence=0.95)
            calmar_ratio = self._calculate_calmar_ratio(annualized_return, max_drawdown)
            sortino_ratio = self._calculate_sortino_ratio(daily_returns)
            
            # Calculate additional metrics
            best_day = daily_returns.max() if not daily_returns.empty else 0.0
            worst_day = daily_returns.min() if not daily_returns.empty else 0.0
            positive_days = (daily_returns > 0).sum()
            negative_days = (daily_returns < 0).sum()
            
            # Calculate consecutive win/loss streaks
            max_consecutive_wins, max_consecutive_losses = self._calculate_consecutive_streaks(daily_returns)
            
            # Create performance report
            report = PerformanceReport(
                # Basic metrics
                total_return=total_return,
                annualized_return=annualized_return,
                volatility=volatility,
                sharpe_ratio=sharpe_ratio,
                max_drawdown=max_drawdown,
                max_drawdown_duration=max_drawdown_duration,
                
                # Trade statistics
                total_trades=trade_stats['total_trades'],
                winning_trades=trade_stats['winning_trades'],
                losing_trades=trade_stats['losing_trades'],
                win_rate=trade_stats['win_rate'],
                avg_win=trade_stats['avg_win'],
                avg_loss=trade_stats['avg_loss'],
                profit_factor=trade_stats['profit_factor'],
                
                # Time series data
                daily_returns=daily_returns,
                cumulative_returns=cumulative_returns,
                equity_curve=equity_curve,
                drawdown_series=drawdown_series,
                
                # Trade history
                trade_history=trade_history,
                
                # Period analysis
                start_date=equity_curve.index[0].strftime('%Y-%m-%d'),
                end_date=equity_curve.index[-1].strftime('%Y-%m-%d'),
                trading_days=len(equity_curve),
                
                # Risk metrics
                var_95=var_95,
                cvar_95=cvar_95,
                calmar_ratio=calmar_ratio,
                sortino_ratio=sortino_ratio,
                
                # Additional metrics
                best_day=best_day,
                worst_day=worst_day,
                positive_days=positive_days,
                negative_days=negative_days,
                max_consecutive_wins=max_consecutive_wins,
                max_consecutive_losses=max_consecutive_losses
            )
            
            return report
            
        except Exception as e:
            raise IBacktestError(f"Performance analysis failed: {str(e)}") from e
    
    def compare_to_benchmark(self, returns: pd.Series, benchmark: str) -> Dict[str, float]:
        """
        Compare strategy returns to benchmark.
        
        Args:
            returns: Strategy returns series
            benchmark: Benchmark identifier
            
        Returns:
            Dictionary containing comparison metrics
        """
        # Import here to avoid circular imports
        from .benchmark import BenchmarkComparator
        
        # This method provides a simple interface for backward compatibility
        # For full functionality, use BenchmarkComparator directly
        try:
            # Create a basic comparison result
            return {
                'total_return_diff': 0.0,
                'volatility_diff': 0.0,
                'sharpe_diff': 0.0,
                'correlation': 0.0,
                'beta': 0.0,
                'alpha': 0.0
            }
        except Exception as e:
            print(f"Warning: Benchmark comparison failed: {str(e)}")
            return {}
    
    def analyze_with_benchmarks(self, backtest_result: Any, 
                               benchmarks: List[str] = None,
                               data_provider = None) -> Dict[str, Any]:
        """
        Analyze backtest results with benchmark comparisons.
        
        Args:
            backtest_result: Results from backtesting engine
            benchmarks: List of benchmark identifiers (defaults to CSI300 and CSIA500)
            data_provider: Data provider for benchmark data
            
        Returns:
            Dictionary containing performance report and benchmark comparisons
        """
        # Get basic performance analysis
        performance_report = self.analyze(backtest_result)

        result = {
            'performance': performance_report,
            'benchmark_comparisons': {}
        }
        
        if not benchmarks or data_provider is None:
            print("Warning: No config.json benchmarks or No data provider provided, skipping benchmark comparisons")
            return result
        
        try:
            from .benchmark import BenchmarkComparator
            
            # Create benchmark comparator
            comparator = BenchmarkComparator(data_provider)
            
            # Get the total return from backtest results for consistency
            strategy_total_return = backtest_result.get('total_return', 0.0)
            
            # Compare to each benchmark
            # Convert trading dates from YYYYMMDD to YYYY-MM-DD format
            start_date_raw = backtest_result['trading_dates'][0]
            end_date_raw = backtest_result['trading_dates'][-1]
            
            # Convert format if needed
            if len(start_date_raw) == 8 and start_date_raw.isdigit():
                start_date = f"{start_date_raw[:4]}-{start_date_raw[4:6]}-{start_date_raw[6:8]}"
                end_date = f"{end_date_raw[:4]}-{end_date_raw[4:6]}-{end_date_raw[6:8]}"
            else:
                start_date = start_date_raw
                end_date = end_date_raw
            
            # Use the new method that accepts total return for consistency
            comparisons = comparator.compare_to_multiple_benchmarks_with_total_return(
                strategy_total_return,
                performance_report.daily_returns,
                benchmarks,
                start_date,
                end_date
            )
            
            result['benchmark_comparisons'] = comparisons
            
        except Exception as e:
            print(f"Warning: Benchmark comparison failed: {str(e)}")
        
        return result
    
    def _extract_backtest_data(self, backtest_result: Any) -> Tuple[pd.Series, List[Dict[str, Any]]]:
        """
        Extract equity curve and trade history from backtest result.
        
        Args:
            backtest_result: Backtest result object
            
        Returns:
            Tuple of (equity_curve, trade_history)
        """
        equity_curve = pd.Series(dtype=float)
        trade_history = []
        
        try:
            # Handle different types of backtest results
            if hasattr(backtest_result, '_equity_curve'):
                # backtesting.py result
                equity_curve = backtest_result._equity_curve.copy()
            elif 'daily_results' in backtest_result and backtest_result['daily_results']:
                # Portfolio backtest result with daily_results
                dates = []
                values = []
                for daily_result in backtest_result['daily_results']:
                    dates.append(pd.to_datetime(daily_result['date']))
                    
                    # Use stored portfolio_value if available, otherwise calculate
                    if 'portfolio_value' in daily_result:
                        portfolio_value = daily_result['portfolio_value']
                    else:
                        # Calculate portfolio value
                        cash = daily_result['cash']
                        positions_value = sum(
                            pos.get('shares', 0) * pos.get('current_price', pos.get('avg_price', 0))
                            for pos in daily_result['positions'].values()
                        )
                        portfolio_value = cash + positions_value
                    
                    values.append(portfolio_value)
                
                equity_curve = pd.Series(values, index=dates)
                trade_history = backtest_result.get('trades', [])
            
            elif isinstance(backtest_result, dict):
                # Dictionary format result
                if 'equity_curve' in backtest_result:
                    equity_curve = backtest_result['equity_curve']
                elif 'final_portfolio_value' in backtest_result:
                    # Create simple equity curve from final value
                    start_value = backtest_result.get('initial_cash', 100000)
                    final_value = backtest_result['final_portfolio_value']
                    start_date = pd.to_datetime(backtest_result.get('start_date', '2020-01-01'))
                    end_date = pd.to_datetime(backtest_result.get('end_date', '2020-12-31'))
                    
                    equity_curve = pd.Series(
                        [start_value, final_value],
                        index=[start_date, end_date]
                    )
                
                trade_history = backtest_result.get('trades', [])
            
            # Ensure equity curve has datetime index
            if not isinstance(equity_curve.index, pd.DatetimeIndex):
                equity_curve.index = pd.to_datetime(equity_curve.index)
            
        except Exception as e:
            print(f"Warning: Could not extract backtest data: {str(e)}")
        
        return equity_curve, trade_history
    
    def _calculate_daily_returns(self, equity_curve: pd.Series) -> pd.Series:
        """Calculate daily returns from equity curve."""
        if len(equity_curve) < 2:
            return pd.Series(dtype=float)
        
        return equity_curve.pct_change().dropna()
    
    def _calculate_cumulative_returns(self, daily_returns: pd.Series) -> pd.Series:
        """Calculate cumulative returns from daily returns."""
        if daily_returns.empty:
            return pd.Series(dtype=float)
        
        return (1 + daily_returns).cumprod() - 1
    
    def _calculate_total_return(self, equity_curve: pd.Series, initial_cash: float) -> float:
        """Calculate total return over the period."""
        if len(equity_curve) < 1:
            return 0.0
        
        # Use the actual initial cash instead of first equity curve value
        # The first equity curve value already includes transaction costs
        return (equity_curve.iloc[-1] / initial_cash) - 1
    
    def _calculate_annualized_return(self, daily_returns: pd.Series) -> float:
        """Calculate annualized return."""
        if daily_returns.empty:
            return 0.0
        
        total_days = len(daily_returns)
        if total_days == 0:
            return 0.0
        
        # Assume 252 trading days per year
        years = total_days / 252
        if years == 0:
            return 0.0
        
        cumulative_return = (1 + daily_returns).prod() - 1
        return (1 + cumulative_return) ** (1 / years) - 1
    
    def _calculate_volatility(self, daily_returns: pd.Series) -> float:
        """Calculate annualized volatility."""
        if daily_returns.empty:
            return 0.0
        
        return daily_returns.std() * np.sqrt(252)
    
    def _calculate_sharpe_ratio(self, daily_returns: pd.Series) -> float:
        """Calculate Sharpe ratio."""
        if daily_returns.empty:
            return 0.0
        
        excess_returns = daily_returns - (self.risk_free_rate / 252)
        
        if excess_returns.std() == 0:
            return 0.0
        
        return excess_returns.mean() / excess_returns.std() * np.sqrt(252)
    
    def _calculate_drawdown_series(self, equity_curve: pd.Series) -> pd.Series:
        """Calculate drawdown series."""
        if equity_curve.empty:
            return pd.Series(dtype=float)
        
        peak = equity_curve.expanding().max()
        drawdown = (equity_curve - peak) / peak
        return drawdown
    
    def _calculate_max_drawdown(self, drawdown_series: pd.Series) -> float:
        """Calculate maximum drawdown."""
        if drawdown_series.empty:
            return 0.0
        
        return abs(drawdown_series.min())
    
    def _calculate_max_drawdown_duration(self, drawdown_series: pd.Series) -> int:
        """Calculate maximum drawdown duration in days."""
        if drawdown_series.empty:
            return 0
        
        # Find periods where drawdown is negative
        in_drawdown = drawdown_series < 0
        
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
    
    def _calculate_trade_statistics(self, trade_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Calculate trade-based statistics."""
        stats = {
            'total_trades': 0,
            'winning_trades': 0,
            'losing_trades': 0,
            'win_rate': 0.0,
            'avg_win': 0.0,
            'avg_loss': 0.0,
            'profit_factor': 0.0
        }
        
        if not trade_history:
            return stats
        
        # Group trades by symbol to calculate P&L
        positions = {}
        completed_trades = []
        
        for trade in trade_history:
            symbol = trade.get('symbol', 'UNKNOWN')
            action = trade.get('action', '').upper()
            quantity = trade.get('quantity', 0)
            price = trade.get('price', 0)
            
            if action == 'BUY':
                if symbol not in positions:
                    positions[symbol] = {'shares': 0, 'total_cost': 0}
                
                positions[symbol]['shares'] += quantity
                positions[symbol]['total_cost'] += quantity * price
                
            elif action == 'SELL' and symbol in positions:
                if positions[symbol]['shares'] > 0:
                    # Calculate P&L for this sell
                    avg_cost = positions[symbol]['total_cost'] / positions[symbol]['shares']
                    pnl = (price - avg_cost) * quantity
                    
                    completed_trades.append({
                        'symbol': symbol,
                        'pnl': pnl,
                        'return_pct': (price - avg_cost) / avg_cost if avg_cost > 0 else 0
                    })
                    
                    # Update position
                    sold_cost = (quantity / positions[symbol]['shares']) * positions[symbol]['total_cost']
                    positions[symbol]['shares'] -= quantity
                    positions[symbol]['total_cost'] -= sold_cost
                    
                    if positions[symbol]['shares'] <= 0:
                        del positions[symbol]
        
        # Calculate statistics from completed trades
        if completed_trades:
            stats['total_trades'] = len(completed_trades)
            
            winning_trades = [t for t in completed_trades if t['pnl'] > 0]
            losing_trades = [t for t in completed_trades if t['pnl'] < 0]
            
            stats['winning_trades'] = len(winning_trades)
            stats['losing_trades'] = len(losing_trades)
            stats['win_rate'] = len(winning_trades) / len(completed_trades)
            
            if winning_trades:
                stats['avg_win'] = np.mean([t['pnl'] for t in winning_trades])
            
            if losing_trades:
                stats['avg_loss'] = np.mean([t['pnl'] for t in losing_trades])
            
            # Profit factor = gross profit / gross loss
            gross_profit = sum(t['pnl'] for t in winning_trades)
            gross_loss = abs(sum(t['pnl'] for t in losing_trades))
            
            if gross_loss > 0:
                stats['profit_factor'] = gross_profit / gross_loss
        
        return stats
    
    def _calculate_var(self, returns: pd.Series, confidence: float = 0.95) -> float:
        """Calculate Value at Risk."""
        if returns.empty:
            return 0.0
        
        return abs(returns.quantile(1 - confidence))
    
    def _calculate_cvar(self, returns: pd.Series, confidence: float = 0.95) -> float:
        """Calculate Conditional Value at Risk (Expected Shortfall)."""
        if returns.empty:
            return 0.0
        
        var = self._calculate_var(returns, confidence)
        tail_returns = returns[returns <= -var]
        
        if tail_returns.empty:
            return var
        
        return abs(tail_returns.mean())
    
    def _calculate_calmar_ratio(self, annualized_return: float, max_drawdown: float) -> float:
        """Calculate Calmar ratio (annualized return / max drawdown)."""
        if max_drawdown == 0:
            return 0.0
        
        return annualized_return / max_drawdown
    
    def _calculate_sortino_ratio(self, daily_returns: pd.Series) -> float:
        """Calculate Sortino ratio (excess return / downside deviation)."""
        if daily_returns.empty:
            return 0.0
        
        excess_returns = daily_returns - (self.risk_free_rate / 252)
        downside_returns = excess_returns[excess_returns < 0]
        
        if len(downside_returns) == 0:
            return 0.0
        
        downside_deviation = downside_returns.std() * np.sqrt(252)
        
        if downside_deviation == 0:
            return 0.0
        
        return (excess_returns.mean() * 252) / downside_deviation
    
    def _calculate_consecutive_streaks(self, daily_returns: pd.Series) -> Tuple[int, int]:
        """Calculate maximum consecutive winning and losing streaks."""
        if daily_returns.empty:
            return 0, 0
        
        # Convert to win/loss signals
        signals = (daily_returns > 0).astype(int)
        
        max_wins = 0
        max_losses = 0
        current_wins = 0
        current_losses = 0
        
        for signal in signals:
            if signal == 1:  # Win
                current_wins += 1
                current_losses = 0
                max_wins = max(max_wins, current_wins)
            else:  # Loss
                current_losses += 1
                current_wins = 0
                max_losses = max(max_losses, current_losses)
        
        return max_wins, max_losses