#!/usr/bin/env python3
"""
Command-line interface for backtest.

This module provides a command-line interface for running China A-shares backtests
with various configuration options and output formats.
"""

import os
import sys
from loguru import logger
import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Tuple, Optional, List
import pandas as pd

from .core.backtest import ASharesBacktestWrapper
from .strategies.manager import StrategyManager
from .strategies.normal import NormalMarketStrategy
from .strategies.bull import BullMarketStrategy
from .strategies.bear import BearMarketStrategy
from .strategies.volatile import VolatileMarketStrategy
from .analysis.pattern_detector import ChinaMarketPatternDetector
from .strategies.picker import ASharesStockPicker
from .analysis.performance import PerformanceAnalyzer
from .analysis.reporting import generate_quick_report
from .utils.exceptions import IBacktestError

from . import CONFIG_FILE, data_provider, global_cm
from .utils.config import ConfigManager
from .utils.util import convert_trade_date
from .utils.trading_calendar import get_trading_days_after, get_trading_days_before
from .analysis.indicators import TechnicalIndicators

def create_parser() -> argparse.ArgumentParser:
    """Create and configure the argument parser."""
    parser = argparse.ArgumentParser(
        description='China A-shares Backtesting Framework',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
** Notes **: All not default value, if config.json exist, then loaded it. Examples:
  # Run a basic backtest
  ibacktest run --start-date 2023-01-01 --end-date 2023-12-31

  # Run with custom configuration
  ibacktest run --config config.json --output-dir results/

  # Run with specific strategy
  ibacktest run --start-date 2023-01-01 --end-date 2023-12-31 --strategy bull_market

  # Generate reports in multiple formats
  ibacktest run --start-date 2023-01-01 --end-date 2023-12-31 --formats json,html,csv

  # Pick top 10 stocks for next trading date (from today)
  ibacktest pick

  # Pick top 10 stocks for next trading date after a specific date
  ibacktest pick --date 2024-03-15
        """
    )

    subparsers = parser.add_subparsers(dest='command', help='Available commands')

    # Run backtest command
    run_parser = subparsers.add_parser('run', help='Run a backtest')
    run_parser.add_argument(
        '--start-date', '-s',
        type=str,
        help='Start date in YYYY-MM-DD format'
    )
    run_parser.add_argument(
        '--end-date', '-e',
        type=str,
        help='End date in YYYY-MM-DD format'
    )
    run_parser.add_argument(
        '--initial-cash', '-c',
        type=float,
        help='Initial cash amount.'
    )
    run_parser.add_argument(
        '--commission',
        type=float,
        help='Commission rate'
    )
    run_parser.add_argument(
        '--max-positions', '-p',
        type=int,
        help='Maximum number of positions'
    )
    run_parser.add_argument(
        '--strategy',
        type=str,
        choices=['auto', 'simple', 'normal_market', 'bull_market', 'bear_market', 'volatile_market'],
        help='Trading strategy to use'
    )
    run_parser.add_argument(
        '--config',
        type=str,
        help='Path to configuration file (JSON format)'
    )
    run_parser.add_argument(
        '--output-dir', '-o',
        type=str,
        help='Output directory for results'
    )
    run_parser.add_argument(
        '--formats', '-f',
        type=str,
        help='Output formats: json,html,csv'
    )
    run_parser.add_argument(
        '--benchmarks',
        type=str,       # Benchmark Indexes: '000001.SH,...'
        help='Benchmarking options'
    )

    # Config command
    config_parser = subparsers.add_parser('config', help='Manage configuration')
    config_subparsers = config_parser.add_subparsers(dest='config_action')
    # Create default config
    create_config_parser = config_subparsers.add_parser('create', help='Create default configuration file')
    create_config_parser.add_argument(
        '--output', '-o',
        type=str,
        help='Output file path.'
    )
    # Validate config
    validate_config_parser = config_subparsers.add_parser('validate', help='Validate configuration file')
    validate_config_parser.add_argument(
        'config_file',
        type=str,
        help='Path to configuration file'
    )

    # Pick command - Pick top 10 stocks for next trading date
    pick_parser = subparsers.add_parser('pick', help='Pick top 10 stocks for next trading date')
    pick_parser.add_argument(
        '--date',
        type=str,
        help='Date in YYYY-MM-DD format. Will pick stocks for next trading date after this date.'
    )
    pick_parser.add_argument(
        '--config',
        type=str,
        help='Path to configuration file (JSON format)'
    )
    pick_parser.add_argument(
        '--output', '-o',
        type=str,
        help='Output file path for picked stocks JSON'
    )

    # Analyze command - Analyze stocks and generate smart orders
    analyze_parser = subparsers.add_parser('analyze', help='Analyze stocks and generate smart orders')
    analyze_parser.add_argument(
        '--stocks-file',
        type=str,
        help='Path to JSON file with picked stocks'
    )
    analyze_parser.add_argument(
        '--symbols',
        type=str,
        help='Comma-separated list of stock symbols to analyze (e.g., 000001.SZ,600519.SH)'
    )
    analyze_parser.add_argument(
        '--config',
        type=str,
        help='Path to configuration file (JSON format)'
    )
    analyze_parser.add_argument(
        '--output', '-o',
        type=str,
        help='Output file path for smart orders JSON'
    )

    subparsers.add_parser('version', help='Show version information')

    return parser


def validate_date_format(date_str: str) -> bool:
    """Validate date string format."""
    try:
        datetime.strptime(date_str, '%Y-%m-%d')
        return True
    except ValueError:
        return False


def validate_date_range(start_date: str, end_date: str) -> bool:
    """Validate that end_date is after start_date."""
    try:
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        return end >= start
    except ValueError:
        return False


def create_components() -> Tuple[ChinaMarketPatternDetector, ASharesStockPicker, StrategyManager]:
    """Create and initialize all backtest components."""
    logger.debug("Initializing components...")

    pattern_detector = ChinaMarketPatternDetector(
        lookback_days=global_cm.get('pattern_detector.lookback_days'),
        confidence_threshold=global_cm.get('pattern_detector.confidence_threshold')
    )

    strategy_manager = StrategyManager(pattern_detector=pattern_detector)
    stock_picker = ASharesStockPicker(data_provider=data_provider, strategy_manager=strategy_manager)

    # Register strategies with actual instances
    strategy_configs = global_cm.get('strategies')
    strategy_classes = {
        'normal_market': NormalMarketStrategy,
        'bull_market': BullMarketStrategy,
        'bear_market': BearMarketStrategy,
        'volatile_market': VolatileMarketStrategy
    }
    for pattern, strategy_config in strategy_configs.items():
        if pattern in strategy_classes:
            strategy_class = strategy_classes[pattern]
            strategy_instance = strategy_class(strategy_config)
            strategy_manager.register_strategy(pattern, strategy_instance)
        else:
            raise ValueError(f"Unknown strategy pattern '{pattern}' in configuration.")

    logger.debug(f"✓ Registered {len([p for p in strategy_configs.keys() if p in strategy_classes])} strategies")
    logger.debug(f"✓ Pattern detector configured with {global_cm.get('pattern_detector.lookback_days')} day lookback")
    logger.debug(f"✓ Stock picker configured for max {global_cm.get('stock_picker.max_pick')} stocks")

    return pattern_detector, stock_picker, strategy_manager

def combined_args_and_config(args: argparse.Namespace) -> argparse.Namespace:
    """Combine command line arguments and configuration settings."""
    args.config = args.config or CONFIG_FILE
    global_cm = ConfigManager(args.config)

    date = global_cm.get('init_info.start_date', None)
    start_date = datetime.now().strftime('%Y-01-01') if not date or 'year-01-01' in date else date
    date = global_cm.get('init_info.end_date', None)
    end_date = datetime.now().strftime('%Y-%m-%d') if not date or 'today' in date else date

    args.start_date = args.start_date or start_date
    args.end_date = args.end_date or end_date
    args.initial_cash = args.initial_cash or global_cm.get('init_info.initial_cash')
    args.commission = args.commission or global_cm.get('init_info.commission')
    args.max_positions = args.max_positions or global_cm.get('init_info.max_positions')
    args.strategy = args.strategy or global_cm.get('init_info.strategy')
    args.output_dir = args.output_dir or global_cm.get('reporting.output_dir')
    args.formats = args.formats or global_cm.get('reporting.formats')
    args.benchmarks = args.benchmarks or global_cm.get('reporting.benchmarks')

    if not validate_date_format(args.start_date):
        raise IBacktestError(f"Invalid start date format: {args.start_date}. Use YYYY-MM-DD")
    if not validate_date_format(args.end_date):
        raise IBacktestError(f"Invalid end date format: {args.end_date}. Use YYYY-MM-DD")
    if not validate_date_range(args.start_date, args.end_date):
        raise IBacktestError("End date must be after start date")
    return args

def run_backtest(args) -> Dict[str, Any]:
    """Run the backtest with given arguments."""

    logger.debug(f"Starting backtest from {args.start_date} to {args.end_date}")
    logger.debug(f"Initial cash: ¥{args.initial_cash:,.2f}")
    logger.debug(f"Commission: {args.commission:.3%}")
    logger.debug(f"Max positions: {args.max_positions}")
    logger.debug(f"Strategy: {args.strategy}")

    pattern_detector, stock_picker, strategy_manager = create_components()
    backtest = ASharesBacktestWrapper(
        data_provider=data_provider,
        strategy_manager=strategy_manager,
        pattern_detector=pattern_detector,
        stock_picker=stock_picker
    )

    logger.debug("Running backtest...")

    import time
    start_time = time.time()

    results = backtest.run_portfolio_backtest(
        start_date=args.start_date,
        end_date=args.end_date,
        initial_cash=args.initial_cash,
        commission=args.commission,
        max_positions=args.max_positions,
    )

    backtest_time = time.time() - start_time
    logger.debug(f"✓ Backtest completed in {backtest_time:.2f} seconds")

    # Add benchmark comparison
    logger.debug("Running benchmark comparison...")

    benchmark_start = time.time()
    try:
        # Create performance analyzer
        analyzer = PerformanceAnalyzer()

        # Get benchmark comparison (沪深300 and 中证A500)
        benchmark_results = analyzer.analyze_with_benchmarks(
            results,
            benchmarks=args.benchmarks,
            data_provider=data_provider
        )

        # Merge benchmark results into main results
        results['benchmark_analysis'] = benchmark_results.get('benchmark_comparisons', {})
        results['performance_metrics'] = benchmark_results.get('performance', {})

        benchmark_time = time.time() - benchmark_start
        logger.debug(f"✓ Benchmark comparison completed in {benchmark_time:.2f} seconds")

    except Exception as e:
        benchmark_time = time.time() - benchmark_start
        logger.warning(f"Benchmark comparison failed in {benchmark_time:.2f} seconds: {e}")

    logger.debug("✓ Backtest completed successfully")

    return results


def save_results(results: Dict[str, Any], output_dir: str, formats: str):
    """Save backtest results in specified formats."""
    # Create output directory
    Path(output_dir).mkdir(parents=True, exist_ok=True)

    # Parse formats
    format_list = [f.strip().lower() for f in formats.split(',')]

    logger.info(f"Saving results to {output_dir} in formats: {', '.join(format_list)}")

    # Generate timestamp for unique filenames
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')

    saved_files = {}

    # Save in requested formats
    if 'json' in format_list:
        json_path = os.path.join(output_dir, f'backtest_results_{timestamp}.json')
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(results, f, indent=2, default=str)
        saved_files['json'] = json_path
        logger.debug(f"✓ JSON results saved to {json_path}")

    # For HTML and CSV, we need to use the reporting system
    if 'html' in format_list or 'csv' in format_list:
        try:
            # Generate performance report using proper analysis
            from .analysis.performance import PerformanceAnalyzer
            analyzer = PerformanceAnalyzer()
            performance_report = analyzer.analyze(results)

            # Extract benchmark comparisons from results
            benchmark_comparisons = results.get('benchmark_analysis', {})

            # Generate reports using the reporting system
            report_files = generate_quick_report(
                performance_report,
                benchmark_comparisons=benchmark_comparisons,
                output_dir=output_dir,
                formats=format_list
            )

            saved_files.update(report_files)

            for fmt, path in report_files.items():
                logger.debug(f"✓ {fmt.upper()} report saved to {path}")

        except Exception as e:
            logger.warning(f"Could not generate HTML/CSV reports: {e}")

    return saved_files


def print_summary(results: Dict[str, Any]):
    """Print a summary of backtest results."""
    logger.info("BACKTEST RESULTS SUMMARY")
    logger.info("="*60)

    # Basic metrics
    initial_cash = results.get('initial_cash', 0)
    final_value = results.get('final_portfolio_value', 0)
    total_return = results.get('total_return', 0)

    logger.info(f"Initial Cash:        ¥{initial_cash:,.2f}")
    logger.info(f"Final Portfolio:     ¥{final_value:,.2f}")
    logger.info(f"Total Return:        {total_return:.2%}")
    logger.info(f"Absolute Gain/Loss:  ¥{final_value - initial_cash:,.2f}")

    # Trading activity
    compliance = results.get('ashares_compliance', {})
    total_trades = compliance.get('total_trades', 0)
    compliance_rate = compliance.get('compliance_rate', 1.0)

    logger.info("Trading Activity:")
    logger.info(f"Total Trades:        {total_trades}")
    logger.info(f"A-shares Compliance: {compliance_rate:.1%}")

    if compliance.get('t_plus_one_violations', 0) > 0:
        logger.info(f"T+1 Violations:      {compliance['t_plus_one_violations']}")

    if compliance.get('short_selling_violations', 0) > 0:
        logger.info(f"Short Sell Attempts: {compliance['short_selling_violations']}")

    # Benchmark comparison
    benchmark_analysis = results.get('benchmark_analysis', {})
    if benchmark_analysis:
        logger.info("Benchmark Comparison:")
        for benchmark_name, comparison in benchmark_analysis.items():
            if hasattr(comparison, 'benchmark_name'):
                logger.info(f"{comparison.benchmark_name}:")
                logger.info(f"  Strategy Return:   {comparison.strategy_total_return:.2%}")
                logger.info(f"  Benchmark Return:  {comparison.benchmark_total_return:.2%}")
                logger.info(f"  Excess Return:     {comparison.excess_return:.2%}")
                logger.info(f"  Correlation:       {comparison.correlation:.3f}")
                logger.info(f"  Beta:              {comparison.beta:.2f}")
                logger.info(f"  Alpha:             {comparison.alpha:.2%}")
            else:
                logger.info(f"{benchmark_name}: Comparison data unavailable")

    # Period information
    start_date = results.get('start_date', 'N/A')
    end_date = results.get('end_date', 'N/A')
    trading_days = len(results.get('trading_dates', []))

    logger.info("Period Information:")
    logger.info(f"Start Date:          {start_date}")
    logger.info(f"End Date:            {end_date}")
    logger.info(f"Trading Days:        {trading_days}")


def create_default_config(output_path: str):
    """Create a default configuration file."""
    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(global_cm.config, f, indent=2)
        logger.debug(f"✓ Default configuration created at {output_path}")
        logger.debug("Edit this file to customize your backtest settings.")
    except IOError as e:
        raise IBacktestError(f"Failed to create configuration file: {e}")


def validate_config_file(config_path: str):
    """Validate a configuration file."""
    try:
        # Load and validate the configuration file
        config_manager = ConfigManager(config_path)
        config = config_manager.config

        # Ensure config is a dictionary
        if not isinstance(config, dict):
            raise ValueError(f"Configuration must be a dictionary, got {type(config)}")

        logger.debug(f"✓ Configuration file {config_path} is valid")

        # Print configuration summary
        logger.debug("Configuration Summary:")
        logger.debug(f"- Data provider settings: {len(config.get('data_provider', {}))} options")
        logger.debug(f"- Pattern detector settings: {len(config.get('pattern_detector', {}))} options")
        logger.debug(f"- Stock picker settings: {len(config.get('stock_picker', {}))} options")
        logger.debug(f"- Strategies configured: {len(config.get('strategies', {}))}")
        logger.debug(f"- Reporting settings: {len(config.get('reporting', {}))} options")

    except Exception as e:
        logger.error(f"✗ Configuration validation failed: {e}")
        sys.exit(1)


def show_version():
    """Show version information."""
    try:
        from . import __version__
        version = __version__
    except ImportError:
        version = "unknown"

    logger.info(f"ibacktest version {version}")
    logger.info("China A-shares Backtesting Framework")
    logger.info("Built with backtesting.py and Tushare integration")


def pick_next_trading_date_stocks(config_path: Optional[str] = None,
                                   output_file: Optional[str] = None,
                                   base_date: Optional[str] = None) -> Dict[str, Any]:
    """
    Pick top 10 stocks for the next trading date based on current market strategy.

    Args:
        config_path: Path to configuration file. If None, uses default config.
        output_file: Path to save picked stocks JSON. If None, uses default location.
        base_date: Base date in YYYY-MM-DD format. Will pick stocks for next trading date after this date.
                   If None, uses current date. If is not trading date, uses previous trading date.

    Returns:
        Dictionary containing picked stocks and metadata

    Raises:
        IBacktestError: If stock picking fails
    """
    try:
        # Load configuration
        config_path = config_path or CONFIG_FILE
        config_manager = ConfigManager(config_path)

        # Get current date and next trading date, Validate and use base_date if provided
        if base_date:
            current_date = convert_trade_date(base_date)
        else:
            current_date = convert_trade_date(datetime.now().strftime('%Y-%m-%d'))
        current_date = get_trading_days_before(current_date, 1) # pyright: ignore
        next_trading_date = get_trading_days_after(current_date, 1)
        logger.info(f"Base date: {current_date}")
        logger.info(f"Picking stocks for next trading date: {next_trading_date}")

        # Create components for stock picking
        pattern_detector, stock_picker, strategy_manager = create_components()

        # Pick stocks for next trading date
        selected_stocks = stock_picker.pick_stocks(next_trading_date)

        # Get detailed information about picked stocks
        pool_with_scores = stock_picker.get_pool_with_scores()
        pool_stats = stock_picker.get_pool_status()

        # Prepare result data
        result = {
            'pick_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'base_date': current_date,
            'target_trading_date': next_trading_date,
            'market_pattern': stock_picker.pattern,
            'strategy_mode': stock_picker.strategy_mode,
            'total_candidates': pool_stats.get('size', 0),
            'selected_count': len(selected_stocks),
            'selected_stocks': []
        }

        # Add detailed stock information
        for rank, symbol in enumerate(selected_stocks, 1):
            score = pool_with_scores.get(symbol, 0.0)
            stock_info = {
                'rank': rank,
                'symbol': symbol,
                'score': round(score, 2)
            }
            result['selected_stocks'].append(stock_info)

        # Save to file
        if output_file is None:
            output_dir = config_manager.get('reporting.output_dir', './backtest_results')
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = os.path.join(output_dir, f'picked_stocks_{timestamp}.json')

        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)

        logger.info(f"✓ Picked {len(selected_stocks)} stocks for {next_trading_date}")
        logger.info(f"✓ Results saved to {output_file}")

        # Print summary
        logger.info("Picked Stocks Summary:")
        logger.info(f"Base Date: {current_date}")
        logger.info(f"Target Date: {next_trading_date}")
        logger.info(f"Market Pattern: {stock_picker.pattern}")
        logger.info(f"Strategy Mode: {stock_picker.strategy_mode}")
        logger.info("Top 10 Selected Stocks:")
        for stock_info in result['selected_stocks']:
            logger.info(f"  {stock_info['rank']}. {stock_info['symbol']} (score: {stock_info['score']:.2f})")

        return result
    except Exception as e:
        raise IBacktestError(f"Stock picking failed: {str(e)}")


def analyze_stocks_and_generate_orders(stocks_file: Optional[str] = None,
                                       symbols: List[str] = [],
                                       config_path: Optional[str] = None,
                                       output_file: Optional[str] = None) -> Dict[str, Any]:
    """
    Analyze picked stocks and generate smart orders with entry/exit prices and quantities.

    This function performs comprehensive technical and fundamental analysis to generate
    smart trading orders including:
    - Buy price (entry point based on support levels and technical indicators)
    - Sell take-profit price (target exit based on resistance and profit targets)
    - Sell stop-loss price (risk management based on volatility and support)
    - Buy/sell quantity (position sizing based on risk management and portfolio balance)

    Args:
        stocks_file: Path to JSON file with picked stocks. If None, must provide symbols.
        symbols: List of stock symbols to analyze. Used if stocks_file is None.
        config_path: Path to configuration file. If None, uses default config.
        output_file: Path to save smart orders JSON. If None, uses default location.

    Returns:
        Dictionary containing smart orders for each stock

    Raises:
        IBacktestError: If analysis fails or inputs are invalid
    """
    try:
        # Validate inputs
        if not stocks_file and not symbols:
            raise IBacktestError("Must provide either stocks_file or symbols list")

        # Load configuration
        config_path = config_path or CONFIG_FILE
        config_manager = ConfigManager(config_path)

        # Get analysis parameters from config
        initial_cash = config_manager.get('init_info.initial_cash', 600000)
        max_positions = config_manager.get('init_info.max_positions', 10)
        lookback_days = config_manager.get('pattern_detector.lookback_days', 20)

        # Get stock symbols to analyze
        market_pattern = None
        target_trading_date = datetime.now().strftime('%Y%m%d')
        base_date = get_trading_days_before(target_trading_date, 1)

        if stocks_file:
            logger.info(f"Loading picked stocks from {stocks_file}")
            with open(stocks_file, 'r', encoding='utf-8') as f:
                picked_data = json.load(f)

            symbols = [s['symbol'] for s in picked_data.get('selected_stocks', [])]
            market_pattern = picked_data.get('market_pattern')
            base_date = picked_data.get('base_date')
            target_trading_date = picked_data.get('target_trading_date')
        else:
            logger.info("Using provided symbols list for analysis, current date is today.")
            # Determine market pattern if not provided
            pattern_detector, _, strategy_manager = create_components()
            start_date = get_trading_days_before(base_date, lookback_days)
            index_df = data_provider.get_index_data('000300.SH', start_date, base_date)
            market_pattern = pattern_detector.detect_pattern(index_df, base_date)

        if not symbols:
            logger.warning("No symbols to analyze")
        logger.info(f"Analyzing {len(symbols)} stocks for smart order generation, market pattern: {market_pattern}")

        # Get strategy configuration for current market pattern
        strategy_config = config_manager.get(f'strategies.{market_pattern}',
                                             config_manager.get('strategies.normal_market'))
        # Extract profit/loss parameters from strategy
        sell_signals = strategy_config.get('sell_signals', [])
        profit_target_pct = 5.0  # default 5%
        stop_loss_pct = 3.0  # default 3%

        for signal in sell_signals:
            if 'profit_target' in signal:
                try:
                    profit_target_pct = float(signal.split('_')[-1].replace('pct', ''))
                except (ValueError, IndexError):
                    pass
            if 'stop_loss' in signal:
                try:
                    stop_loss_pct = float(signal.split('_')[-1].replace('pct', ''))
                except (ValueError, IndexError):
                    pass
        logger.debug(f"Using profit target: {profit_target_pct}%, stop loss: {stop_loss_pct}%")

        start_date = get_trading_days_before(base_date, lookback_days)
        end_date = base_date
        logger.info(f"Fetching historical data from {start_date} to {end_date}")
        # Analyze each stock and generate smart orders
        smart_orders = []
        remaining_cash = float(initial_cash)
        remaining_slots = int(max_positions)

        for symbol in symbols:
            try:
                logger.info(f"Analyzing {symbol}...")
                target_data = data_provider.get_stock_data(symbol, target_trading_date, target_trading_date)
                # Get historical data
                stock_data = data_provider.get_stock_data(symbol, start_date, end_date)
                if stock_data.empty:
                    logger.warning(f"No data found for {symbol}, skipping")
                    continue

                # Get latest data point
                latest = stock_data.iloc[-1]
                close_price = float(latest['close'])
                # Calculate technical indicators for entry/exit points
                close_prices = stock_data['close']
                high_prices = stock_data['high']
                low_prices = stock_data['low']

                # RSI for entry timing
                rsi = TechnicalIndicators.rsi(close_prices)
                latest_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50

                # Bollinger Bands for price levels
                upper_bb, middle_bb, lower_bb = TechnicalIndicators.bollinger_bands(close_prices)
                latest_upper = upper_bb.iloc[-1] if not pd.isna(upper_bb.iloc[-1]) else close_price * 1.02
                latest_middle = middle_bb.iloc[-1] if not pd.isna(middle_bb.iloc[-1]) else close_price
                latest_lower = lower_bb.iloc[-1] if not pd.isna(lower_bb.iloc[-1]) else close_price * 0.98

                # Support and resistance levels
                recent_high = high_prices.tail(5).max()
                recent_low = low_prices.tail(5).min()

                # Average True Range (ATR) for volatility-based stop loss
                atr = TechnicalIndicators.average_true_range(high_prices, low_prices, close_prices)
                latest_atr = atr.iloc[-1] if not pd.isna(atr.iloc[-1]) else close_price * 0.02

                # Calculate buy price (entry point)
                # Use lower Bollinger Band or recent support, adjusted for RSI
                if latest_rsi < 30:  # Oversold
                    buy_price = min(close_price * 0.98, latest_lower)
                elif latest_rsi < 50:  # Mild weakness
                    buy_price = min(close_price * 0.99, latest_middle * 0.98)
                else:  # Normal or strong
                    buy_price = close_price  # Market price

                buy_price = max(buy_price, recent_low * 0.99)  # Don't go below recent support
                buy_price = round(buy_price, 2)

                # 2025.10.27 no-RSI rule: use target_trading_date open price
                # This aligns smart orders with framework backtest (next-day open entry).
                buy_price = float(target_data['open'].iloc[-1])

                # Calculate sell take-profit price
                # Use upper Bollinger Band or profit target
                profit_target_price = close_price * (1 + profit_target_pct / 100)
                sell_take_profit = max(profit_target_price, latest_upper * 0.99)
                sell_take_profit = min(sell_take_profit, recent_high * 1.02)  # Cap at reasonable resistance
                sell_take_profit = round(sell_take_profit, 2)

                # Calculate sell stop-loss price
                # Use ATR-based stop or percentage-based stop, whichever is tighter
                atr_stop = close_price - (latest_atr * 2)
                pct_stop = close_price * (1 - stop_loss_pct / 100)
                sell_stop_loss = max(atr_stop, pct_stop, latest_lower * 0.99)
                sell_stop_loss = min(sell_stop_loss, recent_low * 0.98)  # Must be below recent support
                sell_stop_loss = round(sell_stop_loss, 2)

                # TODO:
                # Each trading day has profit_target_pcty by market pattern
                # See backtest/cli.py 659 line, market pattern in backtest/config.json
                # profit_target_pct = float(signal.split('_')[-1].replace('pct', ''))
                # 2025.10.27 no-rule sell_take_profit and stop loss with fix ratio, as bull market.
                sell_take_profit = round(buy_price * (1 + 0.10), 2)
                sell_stop_loss = round(buy_price * (1 - 0.10), 2)

                # Calculate position size (buy quantity)
                # Use equal-weight allocation with risk management
                position_sizing = strategy_config.get('position_sizing', {})
                max_position_pct = position_sizing.get('max_position_pct', 0.15)
                equal_weight = position_sizing.get('equal_weight', True)

                if remaining_slots <= 0 or remaining_cash <= 0:
                    logger.info(f"Skip {symbol}: no remaining cash/slots for new positions.")
                    continue

                if equal_weight:
                    # Equal weight across remaining positions, using remaining_cash
                    position_value = remaining_cash / remaining_slots
                else:
                    # Risk-adjusted position sizing based on remaining cash
                    position_value = remaining_cash * max_position_pct

                # Adjust for risk (stop loss distance)
                risk_per_share = buy_price - sell_stop_loss
                if risk_per_share > 0:
                    # Risk-based position sizing: don't risk more than 2% of portfolio per position
                    max_risk_amount = remaining_cash * 0.02
                    risk_adjusted_shares = int(max_risk_amount / risk_per_share)
                    value_based_shares = int(position_value / buy_price)
                    buy_quantity = min(risk_adjusted_shares, value_based_shares)
                else:
                    buy_quantity = int(position_value / buy_price)

                # Ensure minimum lot size (100 shares in China A-shares)
                buy_quantity = (buy_quantity // 100) * 100

                # Ensure at least 1 lot when affordable; otherwise skip
                if buy_quantity < 100:
                    logger.info(
                        f"Skip {symbol}: computed position too small (qty={buy_quantity}) "
                        f"for buy_price={buy_price:.2f}, remaining_cash={remaining_cash:.2f}."
                    )
                    continue

                # Calculate expected metrics
                potential_gain_pct = ((sell_take_profit - buy_price) / buy_price) * 100
                potential_loss_pct = ((sell_stop_loss - buy_price) / buy_price) * 100
                risk_reward_ratio = abs(potential_gain_pct / potential_loss_pct) if potential_loss_pct != 0 else 0

                # Update remaining cash/slots using this order's planned value
                used_value = buy_price * buy_quantity
                remaining_cash = max(0.0, remaining_cash - used_value)
                remaining_slots = max(0, remaining_slots - 1)

                # Create smart order
                smart_order = {
                    'symbol': symbol,
                    'name': latest.get('name', ''),
                    'analysis_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                    'current_price': round(close_price, 2),
                    'buy_price': buy_price,
                    'sell_take_profit_price': sell_take_profit,
                    'sell_stop_loss_price': sell_stop_loss,
                    'buy_quantity': buy_quantity,
                    'position_value': round(buy_price * buy_quantity, 2),
                    'technical_indicators': {
                        'rsi': round(latest_rsi, 2),
                        'bb_position': round((close_price - latest_lower) / (latest_upper - latest_lower) * 100, 2),
                        'atr': round(latest_atr, 2),
                        'recent_high': round(recent_high, 2),
                        'recent_low': round(recent_low, 2)
                    },
                    'risk_metrics': {
                        'potential_gain_pct': round(potential_gain_pct, 2),
                        'potential_loss_pct': round(potential_loss_pct, 2),
                        'risk_reward_ratio': round(risk_reward_ratio, 2),
                        'position_risk_pct': round((buy_quantity * buy_price / initial_cash) * 100, 2)
                    },
                    'market_data': {
                        'turnover_rate': round(float(latest.get('turnover_rate', 0)), 2),
                        'volume_ratio': round(float(latest.get('volume_ratio', 1)), 2),
                        'pe': round(float(latest.get('pe', 0)), 2)
                    }
                }

                smart_orders.append(smart_order)

                logger.info(
                    f"✓ {symbol}: Buy@{buy_price:.2f} → TP@{sell_take_profit:.2f} / "
                    f"SL@{sell_stop_loss:.2f}, Qty={buy_quantity}, "
                    f"R/R={risk_reward_ratio:.2f}, remaining_cash={remaining_cash:.2f}, "
                    f"remaining_slots={remaining_slots}"
                )

            except Exception as e:
                logger.error(f"Failed to analyze {symbol}: {str(e)}")
                continue

        # Prepare result
        result = {
            'analysis_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'target_trading_date': target_trading_date,
            'market_pattern': market_pattern,
            'strategy_config': {
                'profit_target_pct': profit_target_pct,
                'stop_loss_pct': stop_loss_pct,
                'max_position_pct': strategy_config.get('position_sizing', {}).get('max_position_pct', 0.15)
            },
            'portfolio_config': {
                'initial_cash': initial_cash,
                'max_positions': max_positions,
                'total_allocated': sum(order['position_value'] for order in smart_orders)
            },
            'total_orders': len(smart_orders),
            'smart_orders': smart_orders
        }

        if not output_file:
            output_dir = config_manager.get('reporting.output_dir', './backtest_results')
            Path(output_dir).mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = os.path.join(output_dir, f'smart_orders_{timestamp}.json')
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(result, f, indent=2, ensure_ascii=False)
        logger.info(f"Generated {len(smart_orders)} smart orders, saved to {output_file}")

        # Print summary
        logger.info("Smart Orders Summary:")
        logger.info(f"Market Pattern: {market_pattern}")
        logger.info(f"Total Capital: ¥{initial_cash:,.2f}")
        logger.info(f"Total Allocated: ¥{result['portfolio_config']['total_allocated']:,.2f}")
        logger.info(f"Generated {len(smart_orders)} Smart Orders:")

        for order in smart_orders:
            logger.info(f"{order['symbol']} - {order['name']}")
            logger.info(f"  Current Price: ¥{order['current_price']}")
            logger.info(f"  Buy Price: ¥{order['buy_price']}")
            logger.info(f"  Take Profit: ¥{order['sell_take_profit_price']} (+{order['risk_metrics']['potential_gain_pct']}%)")
            logger.info(f"  Stop Loss: ¥{order['sell_stop_loss_price']} ({order['risk_metrics']['potential_loss_pct']}%)")
            logger.info(f"  Quantity: {order['buy_quantity']} shares (¥{order['position_value']:,.2f})")
            logger.info(f"  Risk/Reward: {order['risk_metrics']['risk_reward_ratio']:.2f}")

        return result

    except IBacktestError:
        raise
    except Exception as e:
        raise IBacktestError(f"Order analysis failed: {str(e)}")


def main():
    """Main CLI entry point."""
    parser = create_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    try:
        if args.command == 'run':
            args = combined_args_and_config(args)
            results = run_backtest(args)

            save_results(
                results,
                args.output_dir,
                args.formats,
            )
            print_summary(results)

        elif args.command == 'pick':
            # Pick top 10 stocks for next trading date
            pick_next_trading_date_stocks(
                config_path=args.config if hasattr(args, 'config') else None,
                output_file=args.output if hasattr(args, 'output') else None,
                base_date=args.date if hasattr(args, 'date') else None
            )

        elif args.command == 'analyze':
            # Analyze stocks and generate smart orders
            symbols_list = []
            if hasattr(args, 'symbols') and args.symbols:
                symbols_list = [s.strip() for s in args.symbols.split(',')]

            analyze_stocks_and_generate_orders(
                stocks_file=args.stocks_file if hasattr(args, 'stocks_file') else None,
                symbols=symbols_list,
                config_path=args.config if hasattr(args, 'config') else None,
                output_file=args.output if hasattr(args, 'output') else None
            )

        elif args.command == 'config':
            if args.config_action == 'create':
                create_default_config(args.output)
            elif args.config_action == 'validate':
                validate_config_file(args.config_file)
            else:
                parser.print_help()

        elif args.command == 'version':
            show_version()

        else:
            parser.print_help()

    except IBacktestError as e:
        logger.error(f"Error: {e}")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.error("Backtest interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
