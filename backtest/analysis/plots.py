"""
Plot generation for performance visualization.
"""

import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from typing import Dict, Any, List, Optional, Tuple
import pandas as pd
import numpy as np
import seaborn as sns

from .performance import PerformanceReport
from .benchmark import BenchmarkComparison


class PlotGenerator:
    """Creates visualization plots for backtest results."""
    
    def __init__(self, style: str = 'seaborn-v0_8', figsize: Tuple[int, int] = (12, 8)):
        """
        Initialize plot generator.
        
        Args:
            style: Matplotlib style to use
            figsize: Default figure size (width, height)
        """
        self.style = style
        self.figsize = figsize
        self.colors = {
            'strategy': '#2E86AB',      # Blue
            'benchmark1': '#A23B72',    # Purple
            'benchmark2': '#F18F01',    # Orange
            'positive': '#43AA8B',      # Green
            'negative': '#F94144',      # Red
            'neutral': '#90A4AE'        # Gray
        }
        
        # Set style
        try:
            plt.style.use(self.style)
        except Exception:
            plt.style.use('default')
        
        # Set up Chinese font support
        self._setup_chinese_fonts()
    
    def _setup_chinese_fonts(self):
        """Setup font support for matplotlib with proper Chinese fonts."""
        try:
            # Setting Global Fonts for Chinese support - using available fonts in the system
            plt.rcParams['font.sans-serif'] = ['WenQuanYi Zen Hei', 'Noto Sans CJK JP', 'WenQuanYi Micro Hei', 'Arial', 'DejaVu Sans']
            plt.rcParams['font.family'] = ['sans-serif']
            plt.rcParams['axes.unicode_minus'] = False  # Solve the negative sign display issue
        except Exception as e:
            print(f"Warning: Could not set up Chinese fonts: {e}")
            # Fallback to English fonts only
            plt.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans']
    
    def _format_date_axis(self, ax, date_series: pd.Series):
        """
        Format date axis based on the time span of the data.
        
        Args:
            ax: Matplotlib axis object
            date_series: Series with datetime index to determine appropriate formatting
        """
        if len(date_series) == 0:
            return
            
        time_span = (date_series.index[-1] - date_series.index[0]).days
        
        if time_span <= 7:  # Less than a week - use daily
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
            ax.xaxis.set_major_locator(mdates.DayLocator(interval=1))
        elif time_span <= 30:  # Less than a month - use weekly
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m-%d'))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
        elif time_span <= 90:  # Less than 3 months - use weekly
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
            ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=2))
        elif time_span <= 365:  # Less than a year - use monthly
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=1))
        else:  # More than a year - use quarterly
            ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m'))
            ax.xaxis.set_major_locator(mdates.MonthLocator(interval=3))
        
        plt.setp(ax.xaxis.get_majorticklabels(), rotation=45)
    
    def generate_performance_plots(self, performance_report: PerformanceReport, 
                                 benchmark_comparisons: Optional[Dict[str, BenchmarkComparison]] = None,
                                 trade_markers: bool = True) -> Dict[str, plt.Figure]:
        """
        Generate comprehensive performance visualization plots.
        
        Args:
            performance_report: Performance analysis results
            benchmark_comparisons: Optional benchmark comparison results
            trade_markers: Whether to show trade markers on equity curve
            
        Returns:
            Dictionary of plot names to matplotlib figures
        """
        plots = {}
        
        try:
            # 1. Cumulative returns comparison
            plots['cumulative_returns'] = self.plot_cumulative_returns_comparison(
                performance_report, benchmark_comparisons
            )
            
            # 2. Drawdown visualization
            plots['drawdown'] = self.plot_drawdown(performance_report.drawdown_series)
            
            # 3. Equity curve with trade markers
            plots['equity_curve'] = self.plot_equity_curve(
                performance_report.equity_curve, 
                performance_report.trade_history if trade_markers else None
            )
            
            # 4. Rolling performance metrics
            plots['rolling_metrics'] = self.plot_rolling_metrics(performance_report.daily_returns)
            
            # 5. Returns distribution
            plots['returns_distribution'] = self.plot_returns_distribution(performance_report.daily_returns)
            
            # 6. Monthly returns heatmap
            plots['monthly_returns'] = self.plot_monthly_returns_heatmap(performance_report.daily_returns)
            
            # 7. Risk-return scatter (if benchmarks available)
            if benchmark_comparisons:
                plots['risk_return_scatter'] = self.plot_risk_return_scatter(
                    performance_report, benchmark_comparisons
                )
            
        except Exception as e:
            print(f"Warning: Error generating plots: {str(e)}")
        
        return plots
    
    def plot_cumulative_returns_comparison(self, performance_report: PerformanceReport,
                                         benchmark_comparisons: Optional[Dict[str, BenchmarkComparison]] = None) -> plt.Figure:
        """
        Plot cumulative returns comparison between strategy and benchmarks.
        
        Args:
            performance_report: Performance analysis results
            benchmark_comparisons: Optional benchmark comparison results
            
        Returns:
            Matplotlib figure
        """
        fig, ax = plt.subplots(figsize=self.figsize)
        
        # Plot strategy cumulative returns
        cumulative_returns = performance_report.cumulative_returns
        strategy_final_return = cumulative_returns.iloc[-1] * 100
        
        ax.plot(cumulative_returns.index, cumulative_returns * 100, 
                label=f'Strategy ({strategy_final_return:.2f}%)', 
                color=self.colors['strategy'], linewidth=3.0)
        
        # Plot benchmark comparisons if available
        if benchmark_comparisons:
            benchmark_colors = {
                'SSE': self.colors['benchmark1'],
                'CSI300': self.colors['benchmark2'], 
                'CSI500': self.colors['positive'],
                'CSIA500': self.colors['neutral']
            }
            
            for benchmark_name, comparison in benchmark_comparisons.items():
                try:
                    # Handle BenchmarkComparison object
                    if hasattr(comparison, 'benchmark_total_return'):
                        benchmark_total_return = comparison.benchmark_total_return
                        correlation = getattr(comparison, 'correlation', 0.0)
                    else:
                        print(f"Warning: Unknown benchmark comparison format for {benchmark_name}")
                        continue
                    
                    # Create benchmark cumulative returns aligned with strategy dates
                    benchmark_dates = cumulative_returns.index
                    
                    # Calculate daily return for geometric progression
                    days = len(benchmark_dates)
                    if days > 1:
                        # More accurate way to calculate daily return for realistic benchmark progression
                        daily_return = (1 + benchmark_total_return) ** (1/(days-1)) - 1
                        benchmark_cumulative = pd.Series(
                            [(1 + daily_return) ** i - 1 for i in range(days)],
                            index=benchmark_dates
                        )
                    else:
                        benchmark_cumulative = pd.Series([0], index=benchmark_dates)
                    
                    # Choose color based on benchmark name
                    color = benchmark_colors.get(benchmark_name, self.colors['neutral'])
                    
                    # Format benchmark return as percentage
                    benchmark_return_pct = benchmark_total_return * 100
                    
                    ax.plot(benchmark_cumulative.index, benchmark_cumulative * 100,
                           label=f'{benchmark_name} ({benchmark_return_pct:.2f}%, ρ={correlation:.3f})', 
                           color=color, 
                           linewidth=2.0, alpha=0.8, linestyle='--')
                
                except Exception as e:
                    print(f"Error plotting benchmark {benchmark_name}: {e}")
                    continue
        
        # Formatting - English only
        ax.set_title('Cumulative Returns Comparison: Strategy vs Benchmarks', 
                    fontsize=16, fontweight='bold', pad=20)
        ax.set_xlabel('Date', fontsize=12)
        ax.set_ylabel('Cumulative Return (%)', fontsize=12)
        
        # Improve legend
        legend = ax.legend(loc='upper left', frameon=True, fancybox=True, shadow=True, 
                          fontsize=10, framealpha=0.95)
        legend.get_frame().set_facecolor('white')
        legend.get_frame().set_edgecolor('gray')
        legend.get_frame().set_linewidth(0.5)
        
        # Add grid
        ax.grid(True, alpha=0.3, linestyle='-', linewidth=0.5)
        ax.grid(True, alpha=0.15, linestyle=':', linewidth=0.3, which='minor')
        
        # Format x-axis dates
        self._format_date_axis(ax, cumulative_returns)
        
        # Add zero line
        ax.axhline(y=0, color='black', linestyle='-', alpha=0.4, linewidth=0.8)
        
        # Add performance summary box
        if benchmark_comparisons:
            # Create summary text
            summary_lines = [f'Strategy: {strategy_final_return:.2f}%']
            for name, comparison in benchmark_comparisons.items():
                if hasattr(comparison, 'benchmark_total_return'):
                    bench_return = comparison.benchmark_total_return * 100
                    summary_lines.append(f'{name}: {bench_return:.2f}%')
            
            summary_text = '\n'.join(summary_lines)
            
            # Add text box in upper right
            ax.text(0.98, 0.98, summary_text, transform=ax.transAxes,
                   fontsize=9, verticalalignment='top', horizontalalignment='right',
                   bbox=dict(boxstyle='round,pad=0.5', facecolor='lightgray', alpha=0.8))
        
        # Improve y-axis formatting
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'{x:.0f}%'))
        
        # Set reasonable y-axis limits with padding
        all_returns = [strategy_final_return]
        if benchmark_comparisons:
            for comparison in benchmark_comparisons.values():
                if hasattr(comparison, 'benchmark_total_return'):
                    all_returns.append(comparison.benchmark_total_return * 100)
        
        y_max = max(all_returns) * 1.1
        y_min = min(cumulative_returns.min() * 100 * 1.1, -5)
        ax.set_ylim(y_min, y_max)
        
        plt.tight_layout()
        return fig
    
    def plot_drawdown(self, drawdown_series: pd.Series) -> plt.Figure:
        """
        Plot drawdown visualization with underwater curve.
        
        Args:
            drawdown_series: Series of drawdown values
            
        Returns:
            Matplotlib figure
        """
        fig, ax = plt.subplots(figsize=self.figsize)
        
        # Plot drawdown as area chart
        ax.fill_between(drawdown_series.index, drawdown_series * 100, 0, 
                       color=self.colors['negative'], alpha=0.6, label='Drawdown')
        
        # Add line for drawdown
        ax.plot(drawdown_series.index, drawdown_series * 100, 
               color=self.colors['negative'], linewidth=1.5)
        
        # Highlight maximum drawdown
        max_dd_idx = drawdown_series.idxmin()
        max_dd_value = drawdown_series.min()
        ax.scatter(max_dd_idx, max_dd_value * 100, color='red', s=100, zorder=5)
        ax.annotate(f'Max DD: {max_dd_value:.2%}', 
                   xy=(max_dd_idx, max_dd_value * 100),
                   xytext=(10, 10), textcoords='offset points',
                   bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.8),
                   arrowprops=dict(arrowstyle='->', color='red'))
        
        # Formatting
        ax.set_title('Drawdown Analysis', fontsize=16, fontweight='bold', pad=20)
        ax.set_xlabel('Date', fontsize=12)
        ax.set_ylabel('Drawdown (%)', fontsize=12)
        ax.grid(True, alpha=0.3)
        
        # Format x-axis dates
        self._format_date_axis(ax, drawdown_series)
        
        # Set y-axis to show negative values properly
        ax.set_ylim(min(drawdown_series.min() * 100 * 1.1, -1), 1)
        
        plt.tight_layout()
        return fig
    
    def plot_equity_curve(self, equity_curve: pd.Series, 
                         trade_history: Optional[List[Dict[str, Any]]] = None) -> plt.Figure:
        """
        Plot equity curve with optional trade markers.
        
        Args:
            equity_curve: Series of portfolio values over time
            trade_history: Optional list of trade records for markers
            
        Returns:
            Matplotlib figure
        """
        fig, ax = plt.subplots(figsize=self.figsize)
        
        # Plot equity curve
        ax.plot(equity_curve.index, equity_curve, 
               color=self.colors['strategy'], linewidth=2, label='Portfolio Value')
        
        # Add trade markers if provided
        if trade_history:
            buy_dates = []
            sell_dates = []
            buy_prices = []
            sell_prices = []
            
            for trade in trade_history:
                trade_date = pd.to_datetime(trade.get('date'))
                action = trade.get('action', '').upper()
                
                # Find closest equity curve value for this date
                if trade_date in equity_curve.index:
                    equity_value = equity_curve[trade_date]
                else:
                    # Find nearest date
                    nearest_idx = equity_curve.index.get_indexer([trade_date], method='nearest')[0]
                    if nearest_idx >= 0 and nearest_idx < len(equity_curve):
                        equity_value = equity_curve.iloc[nearest_idx]
                        trade_date = equity_curve.index[nearest_idx]
                    else:
                        continue
                
                if action == 'BUY':
                    buy_dates.append(trade_date)
                    buy_prices.append(equity_value)
                elif action == 'SELL':
                    sell_dates.append(trade_date)
                    sell_prices.append(equity_value)
            
            # Plot buy markers
            if buy_dates:
                ax.scatter(buy_dates, buy_prices, color=self.colors['positive'], 
                          marker='^', s=50, alpha=0.7, label='Buy', zorder=5)
            
            # Plot sell markers
            if sell_dates:
                ax.scatter(sell_dates, sell_prices, color=self.colors['negative'], 
                          marker='v', s=50, alpha=0.7, label='Sell', zorder=5)
        
        # Formatting
        ax.set_title('Portfolio Equity Curve', fontsize=16, fontweight='bold', pad=20)
        ax.set_xlabel('Date', fontsize=12)
        ax.set_ylabel('Portfolio Value', fontsize=12)
        ax.legend(loc='upper left', frameon=True, fancybox=True, shadow=True)
        ax.grid(True, alpha=0.3)
        
        # Format x-axis dates
        self._format_date_axis(ax, equity_curve)
        
        # Format y-axis with currency
        ax.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, p: f'¥{x:,.0f}'))
        
        plt.tight_layout()
        return fig
    
    def plot_rolling_metrics(self, daily_returns: pd.Series, window: int = 30) -> plt.Figure:
        """
        Plot rolling performance metrics.
        
        Args:
            daily_returns: Series of daily returns
            window: Rolling window size in days
            
        Returns:
            Matplotlib figure
        """
        fig, ((ax1, ax2), (ax3, ax4)) = plt.subplots(2, 2, figsize=(15, 10))
        
        # Rolling returns
        rolling_returns = daily_returns.rolling(window).mean() * 252  # Annualized
        ax1.plot(rolling_returns.index, rolling_returns * 100, 
                color=self.colors['strategy'], linewidth=1.5)
        ax1.set_title(f'{window}-Day Rolling Annualized Return', fontweight='bold')
        ax1.set_ylabel('Return (%)')
        ax1.grid(True, alpha=0.3)
        ax1.axhline(y=0, color='black', linestyle='-', alpha=0.3)
        
        # Rolling volatility
        rolling_vol = daily_returns.rolling(window).std() * np.sqrt(252)
        ax2.plot(rolling_vol.index, rolling_vol * 100, 
                color=self.colors['benchmark1'], linewidth=1.5)
        ax2.set_title(f'{window}-Day Rolling Volatility', fontweight='bold')
        ax2.set_ylabel('Volatility (%)')
        ax2.grid(True, alpha=0.3)
        
        # Rolling Sharpe ratio
        rolling_sharpe = (daily_returns.rolling(window).mean() / daily_returns.rolling(window).std()) * np.sqrt(252)
        ax3.plot(rolling_sharpe.index, rolling_sharpe, 
                color=self.colors['benchmark2'], linewidth=1.5)
        ax3.set_title(f'{window}-Day Rolling Sharpe Ratio', fontweight='bold')
        ax3.set_ylabel('Sharpe Ratio')
        ax3.grid(True, alpha=0.3)
        ax3.axhline(y=0, color='black', linestyle='-', alpha=0.3)
        
        # Rolling maximum drawdown
        cumulative = (1 + daily_returns).cumprod()
        rolling_max = cumulative.rolling(window, min_periods=1).max()
        rolling_dd = (cumulative - rolling_max) / rolling_max
        rolling_max_dd = rolling_dd.rolling(window).min()
        
        ax4.plot(rolling_max_dd.index, rolling_max_dd * 100, 
                color=self.colors['negative'], linewidth=1.5)
        ax4.set_title(f'{window}-Day Rolling Max Drawdown', fontweight='bold')
        ax4.set_ylabel('Max Drawdown (%)')
        ax4.grid(True, alpha=0.3)
        
        # Format all x-axes
        for ax in [ax1, ax2, ax3, ax4]:
            self._format_date_axis(ax, daily_returns)
        
        plt.suptitle('Rolling Performance Metrics', fontsize=16, fontweight='bold', y=0.98)
        plt.tight_layout()
        return fig
    
    def plot_returns_distribution(self, daily_returns: pd.Series) -> plt.Figure:
        """
        Plot returns distribution with statistics.
        
        Args:
            daily_returns: Series of daily returns
            
        Returns:
            Matplotlib figure
        """
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(15, 6))
        
        # Histogram with normal distribution overlay
        returns_pct = daily_returns * 100
        
        ax1.hist(returns_pct, bins=50, density=True, alpha=0.7, 
                color=self.colors['strategy'], edgecolor='black', linewidth=0.5)
        
        # Overlay normal distribution
        mu, sigma = returns_pct.mean(), returns_pct.std()
        x = np.linspace(returns_pct.min(), returns_pct.max(), 100)
        normal_dist = (1/(sigma * np.sqrt(2 * np.pi))) * np.exp(-0.5 * ((x - mu) / sigma) ** 2)
        ax1.plot(x, normal_dist, 'r--', linewidth=2, label='Normal Distribution')
        
        ax1.axvline(mu, color='red', linestyle='-', alpha=0.8, label=f'Mean: {mu:.2f}%')
        ax1.axvline(mu + sigma, color='orange', linestyle='--', alpha=0.8, label=f'+1σ: {mu+sigma:.2f}%')
        ax1.axvline(mu - sigma, color='orange', linestyle='--', alpha=0.8, label=f'-1σ: {mu-sigma:.2f}%')
        
        ax1.set_title('Daily Returns Distribution', fontweight='bold')
        ax1.set_xlabel('Daily Return (%)')
        ax1.set_ylabel('Density')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # Q-Q plot
        try:
            from scipy import stats
            stats.probplot(returns_pct, dist="norm", plot=ax2)
            ax2.set_title('Q-Q Plot (Normal Distribution)', fontweight='bold')
            ax2.grid(True, alpha=0.3)
        except ImportError:
            # Fallback if scipy is not available
            ax2.text(0.5, 0.5, 'Q-Q Plot\n(scipy required)', 
                    ha='center', va='center', transform=ax2.transAxes,
                    fontsize=12, alpha=0.6)
            ax2.set_title('Q-Q Plot (scipy not available)', fontweight='bold')
            ax2.grid(True, alpha=0.3)
        
        plt.tight_layout()
        return fig
    
    def plot_monthly_returns_heatmap(self, daily_returns: pd.Series) -> plt.Figure:
        """
        Plot monthly returns heatmap.
        
        Args:
            daily_returns: Series of daily returns
            
        Returns:
            Matplotlib figure
        """
        fig, ax = plt.subplots(figsize=(12, 8))
        
        # Calculate monthly returns
        monthly_returns = daily_returns.resample('ME').apply(lambda x: (1 + x).prod() - 1)
        
        # If no monthly data, create a simple message plot
        if len(monthly_returns) == 0:
            ax.text(0.5, 0.5, 'Insufficient data for monthly returns heatmap', 
                   ha='center', va='center', transform=ax.transAxes, fontsize=14)
            ax.set_title('Monthly Returns Heatmap', fontsize=16, fontweight='bold', pad=20)
            plt.tight_layout()
            return fig
        
        # Create pivot table for heatmap
        monthly_returns_df = pd.DataFrame({
            'Year': monthly_returns.index.year,
            'Month': monthly_returns.index.month,
            'Return': monthly_returns.values * 100
        })
        
        pivot_table = monthly_returns_df.pivot(index='Year', columns='Month', values='Return')
        
        # Create heatmap
        sns.heatmap(pivot_table, annot=True, fmt='.1f', cmap='RdYlGn', center=0,
                   ax=ax, cbar_kws={'label': 'Monthly Return (%)'})
        
        # Customize month labels - only show months that have data
        all_month_labels = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                           'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
        
        # Get the actual month columns from the pivot table
        actual_months = pivot_table.columns.tolist()
        month_labels = [all_month_labels[month - 1] for month in actual_months]
        
        # Set only the labels for months that have data
        ax.set_xticklabels(month_labels)
        
        ax.set_title('Monthly Returns Heatmap', fontsize=16, fontweight='bold', pad=20)
        ax.set_xlabel('Month', fontsize=12)
        ax.set_ylabel('Year', fontsize=12)
        
        plt.tight_layout()
        return fig
    
    def plot_risk_return_scatter(self, performance_report: PerformanceReport,
                               benchmark_comparisons: Dict[str, BenchmarkComparison]) -> plt.Figure:
        """
        Plot risk-return scatter plot comparing strategy to benchmarks.
        
        Args:
            performance_report: Performance analysis results
            benchmark_comparisons: Benchmark comparison results
            
        Returns:
            Matplotlib figure
        """
        fig, ax = plt.subplots(figsize=self.figsize)
        
        # Strategy point
        strategy_return = performance_report.annualized_return * 100
        strategy_vol = performance_report.volatility * 100
        
        ax.scatter(strategy_vol, strategy_return, 
                  color=self.colors['strategy'], s=200, alpha=0.8, 
                  label='Strategy', marker='o', edgecolors='black', linewidth=2)
        
        # Benchmark points
        colors = [self.colors['benchmark1'], self.colors['benchmark2'], self.colors['positive']]
        benchmark_data = []
        
        for i, (benchmark_name, comparison) in enumerate(benchmark_comparisons.items()):
            if i < len(colors):
                try:
                    # Handle BenchmarkComparison object
                    if hasattr(comparison, 'benchmark_annualized_return'):
                        benchmark_return = comparison.benchmark_annualized_return * 100
                        benchmark_vol = comparison.benchmark_volatility * 100
                    else:
                        print(f"Warning: Could not access benchmark data for {benchmark_name}")
                        continue
                    
                    benchmark_data.append((benchmark_vol, benchmark_return))
                    
                    ax.scatter(benchmark_vol, benchmark_return,
                              color=colors[i], s=150, alpha=0.8,
                              label=f'{benchmark_name}', marker='s', 
                              edgecolors='black', linewidth=1)
                
                except Exception as e:
                    print(f"Error processing benchmark {benchmark_name}: {e}")
                    continue
        
        # Add Sharpe ratio lines if we have data
        if benchmark_data:
            x_max = max(strategy_vol, max([vol for vol, ret in benchmark_data]))
            sharpe_ratios = [0.5, 1.0, 1.5, 2.0]
            
            for sharpe in sharpe_ratios:
                x_line = np.linspace(0, x_max * 1.1, 100)
                y_line = sharpe * x_line  # Assuming risk-free rate ≈ 0 for simplicity
                ax.plot(x_line, y_line, '--', alpha=0.3, color='gray', linewidth=1)
                if x_max > 0:
                    ax.text(x_max * 1.05, sharpe * x_max * 1.05, f'Sharpe = {sharpe}', 
                           rotation=45, alpha=0.6, fontsize=9)
        
        # Formatting
        ax.set_title('Risk-Return Analysis', 
                    fontsize=16, fontweight='bold', pad=20)
        ax.set_xlabel('Volatility (% per year)', fontsize=12)
        ax.set_ylabel('Annualized Return (% per year)', fontsize=12)
        ax.legend(loc='upper left', frameon=True, fancybox=True, shadow=True)
        ax.grid(True, alpha=0.3)
        
        # Set axis limits
        if benchmark_data:
            x_max = max(strategy_vol, max([vol for vol, ret in benchmark_data]))
            y_min = min(strategy_return, min([ret for vol, ret in benchmark_data]))
            y_max = max(strategy_return, max([ret for vol, ret in benchmark_data]))
        else:
            x_max = strategy_vol
            y_min = strategy_return
            y_max = strategy_return
        
        ax.set_xlim(0, x_max * 1.15)
        y_range = max(y_max - y_min, 10)  # Minimum range of 10%
        ax.set_ylim(y_min - y_range * 0.1, y_max + y_range * 0.1)
        
        plt.tight_layout()
        return fig
    
    def save_plots(self, plots: Dict[str, plt.Figure], output_dir: str = "plots", 
                  format: str = 'png', dpi: int = 300) -> Dict[str, str]:
        """
        Save plots to files.
        
        Args:
            plots: Dictionary of plot names to figures
            output_dir: Output directory for plots
            format: File format (png, pdf, svg)
            dpi: Resolution for raster formats
            
        Returns:
            Dictionary mapping plot names to file paths
        """
        import os
        
        # Create output directory if it doesn't exist
        os.makedirs(output_dir, exist_ok=True)
        
        saved_files = {}
        
        for plot_name, fig in plots.items():
            filename = f"{plot_name}.{format}"
            filepath = os.path.join(output_dir, filename)
            
            try:
                fig.savefig(filepath, dpi=dpi, bbox_inches='tight', 
                           facecolor='white', edgecolor='none')
                saved_files[plot_name] = filepath
                print(f"Saved plot: {filepath}")
            except Exception as e:
                print(f"Error saving plot {plot_name}: {str(e)}")
        
        return saved_files