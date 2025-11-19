"""
Comprehensive reporting system for backtest results.
"""

import json
import csv
from typing import Dict, Any, List, Optional, Union
from datetime import datetime
from pathlib import Path
import pandas as pd
from dataclasses import dataclass, asdict
import jinja2
from io import StringIO

from .performance import PerformanceReport
from .benchmark import BenchmarkComparison
from .plots import PlotGenerator


@dataclass
class ReportConfig:
    """Configuration for report generation."""
    
    title: str = "Backtesting Report"
    subtitle: str = "China A-Shares Strategy Analysis"
    author: str = "iBacktest"
    include_plots: bool = True
    include_trade_details: bool = True
    include_benchmark_comparison: bool = True
    output_formats: List[str] = None
    
    def __post_init__(self):
        if self.output_formats is None:
            self.output_formats = ['html', 'json']


class ReportGenerator:
    """Generates comprehensive reports for backtest results."""
    
    def __init__(self, config: Optional[ReportConfig] = None):
        """
        Initialize report generator.
        
        Args:
            config: Report configuration
        """
        self.config = config or ReportConfig()
        self.plot_generator = PlotGenerator()
        
        # Initialize Jinja2 environment for HTML templates
        self.jinja_env = jinja2.Environment(
            loader=jinja2.DictLoader(self._get_templates()),
            autoescape=jinja2.select_autoescape(['html', 'xml'])
        )
    
    def generate_report(self, performance_report: PerformanceReport,
                       benchmark_comparisons: Optional[Dict[str, BenchmarkComparison]] = None,
                       output_dir: str = "reports") -> Dict[str, str]:
        """
        Generate comprehensive report in multiple formats.
        
        Args:
            performance_report: Performance analysis results
            benchmark_comparisons: Optional benchmark comparison results
            output_dir: Output directory for reports
            
        Returns:
            Dictionary mapping format names to file paths
        """
        # Create output directory
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        
        # Generate timestamp for unique filenames
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        
        generated_files = {}
        
        # Generate plots if requested
        plots = {}
        plot_files = {}
        if self.config.include_plots:
            plots = self.plot_generator.generate_performance_plots(
                performance_report, benchmark_comparisons
            )
            plot_files = self.plot_generator.save_plots(
                plots, f"{output_dir}/plots_{timestamp}"
            )
        
        # Prepare report data
        report_data = self._prepare_report_data(
            performance_report, benchmark_comparisons, plot_files
        )
        
        # Generate reports in requested formats
        for format_type in self.config.output_formats:
            try:
                if format_type.lower() == 'html':
                    filepath = self._generate_html_report(
                        report_data, f"{output_dir}/report_{timestamp}.html"
                    )
                elif format_type.lower() == 'json':
                    filepath = self._generate_json_report(
                        report_data, f"{output_dir}/report_{timestamp}.json"
                    )
                elif format_type.lower() == 'csv':
                    filepath = self._generate_csv_report(
                        report_data, f"{output_dir}/report_{timestamp}.csv"
                    )
                elif format_type.lower() == 'excel':
                    filepath = self._generate_excel_report(
                        report_data, f"{output_dir}/report_{timestamp}.xlsx"
                    )
                else:
                    print(f"Warning: Unsupported format {format_type}")
                    continue
                
                generated_files[format_type] = filepath
                print(f"Generated {format_type.upper()} report: {filepath}")
                
            except Exception as e:
                print(f"Error generating {format_type} report: {str(e)}")
        
        return generated_files
    
    def _prepare_report_data(self, performance_report: PerformanceReport,
                           benchmark_comparisons: Optional[Dict[str, BenchmarkComparison]],
                           plot_files: Dict[str, str]) -> Dict[str, Any]:
        """Prepare data structure for report generation."""
        
        # Convert performance report to dictionary
        perf_dict = asdict(performance_report)
        
        # Convert pandas series to lists for JSON serialization
        for key, value in perf_dict.items():
            if isinstance(value, pd.Series):
                perf_dict[key] = {
                    'dates': value.index.strftime('%Y-%m-%d').tolist(),
                    'values': value.tolist()
                }
        
        # Prepare benchmark data
        benchmark_data = {}
        if benchmark_comparisons:
            for name, comparison in benchmark_comparisons.items():
                benchmark_data[name] = asdict(comparison)
        
        # Calculate additional summary statistics
        summary_stats = self._calculate_summary_statistics(performance_report)
        
        # Prepare trade analysis
        trade_analysis = self._analyze_trades(performance_report.trade_history)
        
        # Convert plot file paths to relative paths for HTML reports
        relative_plot_files = {}
        for plot_name, absolute_path in plot_files.items():
            # Convert absolute path to relative path from HTML file location
            # e.g., /path/to/backtest_results/plots_timestamp/file.png -> ./plots_timestamp/file.png
            import os
            relative_path = "./" + os.path.basename(os.path.dirname(absolute_path)) + "/" + os.path.basename(absolute_path)
            relative_plot_files[plot_name] = relative_path
        
        # Create comprehensive report data
        report_data = {
            'metadata': {
                'title': self.config.title,
                'subtitle': self.config.subtitle,
                'author': self.config.author,
                'generated_at': datetime.now().isoformat(),
                'period': f"{performance_report.start_date} to {performance_report.end_date}",
                'trading_days': performance_report.trading_days
            },
            'performance': perf_dict,
            'benchmarks': benchmark_data,
            'summary_statistics': summary_stats,
            'trade_analysis': trade_analysis,
            'plots': relative_plot_files,
            'config': asdict(self.config)
        }
        
        return report_data
    
    def _calculate_summary_statistics(self, performance_report: PerformanceReport) -> Dict[str, Any]:
        """Calculate additional summary statistics."""
        
        daily_returns = performance_report.daily_returns
        
        return {
            'performance_summary': {
                'total_return_pct': f"{performance_report.total_return:.2%}",
                'annualized_return_pct': f"{performance_report.annualized_return:.2%}",
                'volatility_pct': f"{performance_report.volatility:.2%}",
                'sharpe_ratio': f"{performance_report.sharpe_ratio:.2f}",
                'max_drawdown_pct': f"{performance_report.max_drawdown:.2%}",
                'calmar_ratio': f"{performance_report.calmar_ratio:.2f}",
                'sortino_ratio': f"{performance_report.sortino_ratio:.2f}"
            },
            'risk_metrics': {
                'var_95_pct': f"{performance_report.var_95:.2%}",
                'cvar_95_pct': f"{performance_report.cvar_95:.2%}",
                'best_day_pct': f"{performance_report.best_day:.2%}",
                'worst_day_pct': f"{performance_report.worst_day:.2%}",
                'positive_days': performance_report.positive_days,
                'negative_days': performance_report.negative_days,
                'positive_days_pct': f"{performance_report.positive_days / performance_report.trading_days:.1%}" if performance_report.trading_days > 0 else "0.0%"
            },
            'trade_statistics': {
                'total_trades': performance_report.total_trades,
                'winning_trades': performance_report.winning_trades,
                'losing_trades': performance_report.losing_trades,
                'win_rate_pct': f"{performance_report.win_rate:.1%}",
                'avg_win_pct': f"{performance_report.avg_win:.2%}" if performance_report.avg_win else "0.00%",
                'avg_loss_pct': f"{performance_report.avg_loss:.2%}" if performance_report.avg_loss else "0.00%",
                'profit_factor': f"{performance_report.profit_factor:.2f}",
                'max_consecutive_wins': performance_report.max_consecutive_wins,
                'max_consecutive_losses': performance_report.max_consecutive_losses
            }
        }
    
    def _analyze_trades(self, trade_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Analyze trade history for additional insights."""
        
        if not trade_history:
            return {'message': 'No trade history available'}
        
        # Group trades by symbol
        symbol_trades = {}
        monthly_trades = {}
        
        for trade in trade_history:
            symbol = trade.get('symbol', 'UNKNOWN')
            date_str = trade.get('date', '')
            
            # Group by symbol
            if symbol not in symbol_trades:
                symbol_trades[symbol] = []
            symbol_trades[symbol].append(trade)
            
            # Group by month
            if date_str:
                try:
                    trade_date = pd.to_datetime(date_str)
                    month_key = trade_date.strftime('%Y-%m')
                    if month_key not in monthly_trades:
                        monthly_trades[month_key] = 0
                    monthly_trades[month_key] += 1
                except:
                    pass
        
        # Calculate symbol statistics
        symbol_stats = {}
        for symbol, trades in symbol_trades.items():
            buy_trades = [t for t in trades if t.get('action', '').upper() == 'BUY']
            sell_trades = [t for t in trades if t.get('action', '').upper() == 'SELL']
            
            symbol_stats[symbol] = {
                'total_trades': len(trades),
                'buy_trades': len(buy_trades),
                'sell_trades': len(sell_trades),
                'avg_quantity': sum(t.get('quantity', 0) for t in trades) / len(trades) if trades else 0,
                'total_volume': sum(t.get('quantity', 0) * t.get('price', 0) for t in trades)
            }
        
        # Find most traded symbols
        most_traded = sorted(symbol_stats.items(), 
                           key=lambda x: x[1]['total_trades'], reverse=True)[:10]
        
        return {
            'total_symbols_traded': len(symbol_trades),
            'trades_per_month': monthly_trades,
            'symbol_statistics': symbol_stats,
            'most_traded_symbols': [{'symbol': symbol, **stats} for symbol, stats in most_traded],
            'avg_trades_per_symbol': sum(len(trades) for trades in symbol_trades.values()) / len(symbol_trades) if symbol_trades else 0
        }
    
    def _generate_html_report(self, report_data: Dict[str, Any], filepath: str) -> str:
        """Generate HTML report."""
        
        template = self.jinja_env.get_template('html_report')
        html_content = template.render(**report_data)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write(html_content)
        
        return filepath
    
    def _generate_json_report(self, report_data: Dict[str, Any], filepath: str) -> str:
        """Generate JSON report."""
        
        # Create a JSON-serializable copy
        json_data = self._make_json_serializable(report_data)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(json_data, f, indent=2, ensure_ascii=False, default=str)
        
        return filepath
    
    def _generate_csv_report(self, report_data: Dict[str, Any], filepath: str) -> str:
        """Generate CSV report with key metrics."""
        
        # Prepare data for CSV
        csv_data = []
        
        # Performance metrics
        perf_summary = report_data['summary_statistics']['performance_summary']
        for metric, value in perf_summary.items():
            csv_data.append(['Performance', metric, value])
        
        # Risk metrics
        risk_metrics = report_data['summary_statistics']['risk_metrics']
        for metric, value in risk_metrics.items():
            csv_data.append(['Risk', metric, value])
        
        # Trade statistics
        trade_stats = report_data['summary_statistics']['trade_statistics']
        for metric, value in trade_stats.items():
            csv_data.append(['Trading', metric, value])
        
        # Benchmark comparisons
        if report_data['benchmarks']:
            for benchmark_name, benchmark_data in report_data['benchmarks'].items():
                csv_data.append(['Benchmark', f'{benchmark_name}_total_return', f"{benchmark_data['benchmark_total_return']:.2%}"])
                csv_data.append(['Benchmark', f'{benchmark_name}_excess_return', f"{benchmark_data['excess_return']:.2%}"])
                csv_data.append(['Benchmark', f'{benchmark_name}_correlation', f"{benchmark_data['correlation']:.3f}"])
        
        # Write CSV
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow(['Category', 'Metric', 'Value'])
            writer.writerows(csv_data)
        
        return filepath
    
    def _generate_excel_report(self, report_data: Dict[str, Any], filepath: str) -> str:
        """Generate Excel report with multiple sheets."""
        
        with pd.ExcelWriter(filepath, engine='openpyxl') as writer:
            
            # Summary sheet
            summary_data = []
            perf_summary = report_data['summary_statistics']['performance_summary']
            for metric, value in perf_summary.items():
                summary_data.append(['Performance', metric, value])
            
            risk_metrics = report_data['summary_statistics']['risk_metrics']
            for metric, value in risk_metrics.items():
                summary_data.append(['Risk', metric, value])
            
            trade_stats = report_data['summary_statistics']['trade_statistics']
            for metric, value in trade_stats.items():
                summary_data.append(['Trading', metric, value])
            
            summary_df = pd.DataFrame(summary_data, columns=['Category', 'Metric', 'Value'])
            summary_df.to_excel(writer, sheet_name='Summary', index=False)
            
            # Performance time series
            if 'daily_returns' in report_data['performance']:
                returns_data = report_data['performance']['daily_returns']
                if isinstance(returns_data, dict) and 'dates' in returns_data:
                    returns_df = pd.DataFrame({
                        'Date': returns_data['dates'],
                        'Daily_Return': returns_data['values']
                    })
                    returns_df.to_excel(writer, sheet_name='Daily_Returns', index=False)
            
            if 'equity_curve' in report_data['performance']:
                equity_data = report_data['performance']['equity_curve']
                if isinstance(equity_data, dict) and 'dates' in equity_data:
                    equity_df = pd.DataFrame({
                        'Date': equity_data['dates'],
                        'Portfolio_Value': equity_data['values']
                    })
                    equity_df.to_excel(writer, sheet_name='Equity_Curve', index=False)
            
            # Trade history
            if self.config.include_trade_details and report_data['performance']['trade_history']:
                trades_df = pd.DataFrame(report_data['performance']['trade_history'])
                trades_df.to_excel(writer, sheet_name='Trade_History', index=False)
            
            # Benchmark comparisons
            if report_data['benchmarks']:
                benchmark_rows = []
                for benchmark_name, benchmark_data in report_data['benchmarks'].items():
                    row = {'Benchmark': benchmark_name}
                    row.update(benchmark_data)
                    benchmark_rows.append(row)
                
                benchmark_df = pd.DataFrame(benchmark_rows)
                benchmark_df.to_excel(writer, sheet_name='Benchmark_Comparison', index=False)
        
        return filepath
    
    def _make_json_serializable(self, obj: Any) -> Any:
        """Convert object to JSON-serializable format."""
        
        if isinstance(obj, dict):
            return {key: self._make_json_serializable(value) for key, value in obj.items()}
        elif isinstance(obj, list):
            return [self._make_json_serializable(item) for item in obj]
        elif isinstance(obj, pd.Series):
            return {
                'dates': obj.index.strftime('%Y-%m-%d').tolist() if hasattr(obj.index, 'strftime') else obj.index.tolist(),
                'values': obj.tolist()
            }
        elif isinstance(obj, pd.DataFrame):
            return obj.to_dict('records')
        elif hasattr(obj, 'isoformat'):  # datetime objects
            return obj.isoformat()
        elif isinstance(obj, (int, float, str, bool)) or obj is None:
            return obj
        else:
            return str(obj)
    
    def _get_templates(self) -> Dict[str, str]:
        """Get HTML templates for report generation."""
        
        html_template = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{{ metadata.title }}</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            line-height: 1.6;
            margin: 0;
            padding: 20px;
            background-color: #f5f5f5;
        }
        .container {
            max-width: 1200px;
            margin: 0 auto;
            background-color: white;
            padding: 30px;
            border-radius: 10px;
            box-shadow: 0 0 20px rgba(0,0,0,0.1);
        }
        .header {
            text-align: center;
            border-bottom: 3px solid #2E86AB;
            padding-bottom: 20px;
            margin-bottom: 30px;
        }
        .header h1 {
            color: #2E86AB;
            margin: 0;
            font-size: 2.5em;
        }
        .header h2 {
            color: #666;
            margin: 10px 0 0 0;
            font-weight: normal;
        }
        .metadata {
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 5px;
            margin-bottom: 30px;
        }
        .section {
            margin-bottom: 40px;
        }
        .section h3 {
            color: #2E86AB;
            border-bottom: 2px solid #2E86AB;
            padding-bottom: 10px;
            margin-bottom: 20px;
        }
        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }
        .metric-card {
            background-color: #f8f9fa;
            padding: 20px;
            border-radius: 8px;
            border-left: 4px solid #2E86AB;
        }
        .metric-card h4 {
            margin: 0 0 15px 0;
            color: #2E86AB;
            font-size: 1.1em;
        }
        .metric-row {
            display: flex;
            justify-content: space-between;
            margin-bottom: 8px;
            padding: 5px 0;
            border-bottom: 1px solid #e9ecef;
        }
        .metric-row:last-child {
            border-bottom: none;
        }
        .metric-label {
            font-weight: 500;
            color: #495057;
        }
        .metric-value {
            font-weight: bold;
            color: #212529;
        }
        .positive { color: #28a745; }
        .negative { color: #dc3545; }
        .neutral { color: #6c757d; }
        
        .benchmark-table {
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }
        .benchmark-table th,
        .benchmark-table td {
            padding: 12px;
            text-align: left;
            border-bottom: 1px solid #dee2e6;
        }
        .benchmark-table th {
            background-color: #2E86AB;
            color: white;
            font-weight: 600;
        }
        .benchmark-table tr:hover {
            background-color: #f8f9fa;
        }
        
        .plot-section {
            margin: 30px 0;
        }
        .plot-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(400px, 1fr));
            gap: 20px;
        }
        .plot-item {
            text-align: center;
            background-color: #f8f9fa;
            padding: 15px;
            border-radius: 8px;
        }
        .plot-item img {
            max-width: 100%;
            height: auto;
            border-radius: 5px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
        }
        
        .footer {
            text-align: center;
            margin-top: 50px;
            padding-top: 20px;
            border-top: 1px solid #dee2e6;
            color: #6c757d;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>{{ metadata.title }}</h1>
            <h2>{{ metadata.subtitle }}</h2>
        </div>
        
        <div class="metadata">
            <strong>Report Period:</strong> {{ metadata.period }} ({{ metadata.trading_days }} trading days)<br>
            <strong>Generated:</strong> {{ metadata.generated_at }}<br>
            <strong>Author:</strong> {{ metadata.author }}
        </div>
        
        <div class="section">
            <h3>Performance Summary</h3>
            <div class="metrics-grid">
                <div class="metric-card">
                    <h4>Returns</h4>
                    {% for metric, value in summary_statistics.performance_summary.items() %}
                    <div class="metric-row">
                        <span class="metric-label">{{ metric.replace('_', ' ').title() }}</span>
                        <span class="metric-value">{{ value }}</span>
                    </div>
                    {% endfor %}
                </div>
                
                <div class="metric-card">
                    <h4>Risk Metrics</h4>
                    {% for metric, value in summary_statistics.risk_metrics.items() %}
                    <div class="metric-row">
                        <span class="metric-label">{{ metric.replace('_', ' ').title() }}</span>
                        <span class="metric-value">{{ value }}</span>
                    </div>
                    {% endfor %}
                </div>
                
                <div class="metric-card">
                    <h4>Trading Statistics</h4>
                    {% for metric, value in summary_statistics.trade_statistics.items() %}
                    <div class="metric-row">
                        <span class="metric-label">{{ metric.replace('_', ' ').title() }}</span>
                        <span class="metric-value">{{ value }}</span>
                    </div>
                    {% endfor %}
                </div>
            </div>
        </div>
        
        {% if benchmarks %}
        <div class="section">
            <h3>Benchmark Comparison</h3>
            <table class="benchmark-table">
                <thead>
                    <tr>
                        <th>Benchmark</th>
                        <th>Strategy Return</th>
                        <th>Benchmark Return</th>
                        <th>Excess Return</th>
                        <th>Correlation</th>
                        <th>Beta</th>
                        <th>Alpha</th>
                        <th>Information Ratio</th>
                    </tr>
                </thead>
                <tbody>
                    {% for benchmark_name, benchmark_data in benchmarks.items() %}
                    <tr>
                        <td><strong>{{ benchmark_name }}</strong></td>
                        <td>{{ "%.2f%%" | format(benchmark_data.strategy_total_return * 100) }}</td>
                        <td>{{ "%.2f%%" | format(benchmark_data.benchmark_total_return * 100) }}</td>
                        <td class="{% if benchmark_data.excess_return > 0 %}positive{% else %}negative{% endif %}">
                            {{ "%.2f%%" | format(benchmark_data.excess_return * 100) }}
                        </td>
                        <td>{{ "%.3f" | format(benchmark_data.correlation) }}</td>
                        <td>{{ "%.2f" | format(benchmark_data.beta) }}</td>
                        <td class="{% if benchmark_data.alpha > 0 %}positive{% else %}negative{% endif %}">
                            {{ "%.2f%%" | format(benchmark_data.alpha * 100) }}
                        </td>
                        <td>{{ "%.2f" | format(benchmark_data.information_ratio) }}</td>
                    </tr>
                    {% endfor %}
                </tbody>
            </table>
        </div>
        {% endif %}
        
        {% if plots and config.include_plots %}
        <div class="section">
            <h3>Performance Visualization</h3>
            <div class="plot-grid">
                {% for plot_name, plot_path in plots.items() %}
                <div class="plot-item">
                    <h4>{{ plot_name.replace('_', ' ').title() }}</h4>
                    <img src="{{ plot_path }}" alt="{{ plot_name }}">
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}
        
        {% if trade_analysis.most_traded_symbols %}
        <div class="section">
            <h3>Trade Analysis</h3>
            <div class="metric-card">
                <h4>Most Traded Symbols</h4>
                {% for symbol_data in trade_analysis.most_traded_symbols[:5] %}
                <div class="metric-row">
                    <span class="metric-label">{{ symbol_data.symbol }}</span>
                    <span class="metric-value">{{ symbol_data.total_trades }} trades</span>
                </div>
                {% endfor %}
            </div>
        </div>
        {% endif %}
        
        <div class="footer">
            <p>Generated by {{ metadata.author }} on {{ metadata.generated_at }}</p>
            <p>This report is for informational purposes only and should not be considered as investment advice.</p>
        </div>
    </div>
</body>
</html>
        """
        
        return {
            'html_report': html_template
        }


def generate_quick_report(performance_report: PerformanceReport,
                         benchmark_comparisons: Optional[Dict[str, BenchmarkComparison]] = None,
                         output_dir: str = "reports",
                         formats: List[str] = None) -> Dict[str, str]:
    """
    Quick function to generate a report with default settings.
    
    Args:
        performance_report: Performance analysis results
        benchmark_comparisons: Optional benchmark comparison results
        output_dir: Output directory for reports
        formats: List of formats to generate (defaults to ['html', 'json'])
        
    Returns:
        Dictionary mapping format names to file paths
    """
    if formats is None:
        formats = ['html', 'json']
    
    config = ReportConfig(output_formats=formats)
    generator = ReportGenerator(config)
    
    return generator.generate_report(
        performance_report, benchmark_comparisons, output_dir
    )