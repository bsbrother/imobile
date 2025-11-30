"""
Main backtesting engine for China A-shares market.
"""

from loguru import logger
from typing import Dict, Any, Optional, List, cast
from datetime import datetime
import pandas as pd
from backtesting import Backtest, Strategy

from .interfaces import DataProvider, MarketPatternDetector, StockPicker
from .strategy import ASharesStrategy
from .validator import TradeValidator
from ..strategies.manager import StrategyManager
from ..utils.exceptions import IBacktestError, TradeValidationError

from ..utils.trading_calendar import count_trading_days_between

from .. import LOG_LEVEL

class ChinaASharesBacktest(Backtest):
    """
    Main backtesting class that extends backtesting.py with A-shares specific constraints.
    Enforces T+1 trading rules and no short-selling validation.
    """
    data: pd.DataFrame
    
    def __init__(self, 
                 data: pd.DataFrame,
                 strategy_class: type[Strategy],
                 cash: float = 10000,
                 commission: float = 0.001,
                 margin: float = 1.0,
                 trade_on_close: bool = False,
                 hedging: bool = False,
                 exclusive_orders: bool = False,
                 data_provider: Optional[DataProvider] = None,
                 pattern_detector: Optional[MarketPatternDetector] = None,
                 stock_picker: Optional[StockPicker] = None):
        """
        Initialize the China A-shares backtesting engine.
        
        Args:
            data: OHLCV data for backtesting
            strategy_class: Trading strategy class
            cash: Initial cash amount
            commission: Commission rate
            margin: Margin requirement (always 1.0 for A-shares, no margin trading)
            trade_on_close: Whether to trade on close prices
            hedging: Whether to allow hedging (disabled for A-shares)
            exclusive_orders: Whether orders are exclusive
            data_provider: Data provider for additional market data
            pattern_detector: Market pattern detection system
            stock_picker: Stock selection system
        """
        # Force A-shares specific settings
        margin = 1.0  # No margin trading in A-shares
        hedging = False  # No hedging in A-shares
        
        # Initialize parent Backtest class
        # backtesting.py expects a strategy class, not instance
        from typing import cast
        super().__init__(
            data=data,
            strategy=cast(type[Strategy], strategy_class),
            cash=cash,
            commission=commission,
            margin=margin,
            trade_on_close=trade_on_close,
            hedging=hedging,
            exclusive_orders=exclusive_orders
        )
        
        # Expose underlying data frame for internal access and static type checkers
        self.data = getattr(self, "_data", data)
        
        # A-shares specific components
        self.data_provider = data_provider
        self.pattern_detector = pattern_detector
        self.stock_picker = stock_picker
        self.trade_validator = TradeValidator()
        
        # Track positions for T+1 rule enforcement
        self.position_buy_dates = {}
        self.daily_trades = []
        
        # Inject A-shares components into strategy if it's an ASharesStrategy
        if issubclass(strategy_class, ASharesStrategy):
            strat_cls = cast(Any, strategy_class)
            if pattern_detector is not None:
                strat_cls.pattern_detector = pattern_detector
            if stock_picker is not None:
                strat_cls.stock_picker = stock_picker
            strat_cls.trade_validator = self.trade_validator
    
    def run(self, **kwargs) -> pd.Series:
        """
        Run backtest with A-shares specific validation.
        
        Returns:
            Backtest results with A-shares compliance validation
        """
        try:
            # Run the standard backtest
            results = super().run(**kwargs)
            
            # Add A-shares specific validation results
            validation_results = self._validate_ashares_compliance()
            results._strategy.validation_results = validation_results
            
            return results
            
        except Exception as e:
            raise IBacktestError(f"A-shares backtest execution failed: {str(e)}") from e
    
    def _validate_ashares_compliance(self) -> Dict[str, Any]:
        """
        Validate that all trades comply with A-shares rules.
        
        Returns:
            Dictionary containing validation results
        """
        validation_results = {
            'total_trades': len(self.daily_trades),
            't_plus_one_violations': 0,
            'short_selling_violations': 0,
            'invalid_trades': [],
            'compliance_rate': 0.0
        }
        
        violations = 0
        
        for trade in self.daily_trades:
            try:
                # Validate each trade
                self.trade_validator.validate_trade(trade, trade['date'])
            except TradeValidationError as e:
                violations += 1
                validation_results['invalid_trades'].append({
                    'trade': trade,
                    'error': str(e)
                })
                
                # Categorize violation type
                if 'T+1' in str(e):
                    validation_results['t_plus_one_violations'] += 1
                elif 'short' in str(e).lower():
                    validation_results['short_selling_violations'] += 1
        
        # Calculate compliance rate
        if validation_results['total_trades'] > 0:
            validation_results['compliance_rate'] = (
                (validation_results['total_trades'] - violations) / 
                validation_results['total_trades']
            )
        else:
            validation_results['compliance_rate'] = 1.0
        
        return validation_results
    
    def _process_order(self, order, bar_index):
        """
        Override order processing to enforce A-shares rules.
        
        Args:
            order: Order to process
            bar_index: Current bar index
        """
        # Normalize to a built-in datetime for validator/type checkers
        raw_date = self.data.index[bar_index]
        if isinstance(raw_date, pd.Timestamp):
            current_date: datetime = raw_date.to_pydatetime()
        elif isinstance(raw_date, datetime):
            current_date = raw_date
        else:
            # Cast to Any to satisfy type checkers when Index/unknown scalar appears
            current_date = pd.Timestamp(cast(Any, raw_date)).to_pydatetime()
        
        # Create trade record for validation
        trade_record = {
            'date': current_date,
            'symbol': getattr(order, 'symbol', 'UNKNOWN'),
            'action': 'BUY' if order.size > 0 else 'SELL',
            'quantity': abs(order.size),
            'price': getattr(order, 'price', self.data.Close.iloc[bar_index])
        }
        
        try:
            # Validate trade against A-shares rules
            self.trade_validator.validate_trade(trade_record, current_date)
            
            # If validation passes, process the order normally
            result = super()._process_order(order, bar_index)  # type: ignore[attr-defined]
            
            # Record successful trade
            self.daily_trades.append(trade_record)
            
            # Update position tracking for T+1 rule
            if trade_record['action'] == 'BUY':
                self.trade_validator.record_buy(trade_record['symbol'], current_date)
            elif trade_record['action'] == 'SELL':
                self.trade_validator.record_sell(trade_record['symbol'])
            
            return result
            
        except TradeValidationError as e:
            # Log validation error and reject trade
            trade_record['validation_error'] = str(e)
            self.daily_trades.append(trade_record)
            
            # Return without processing the order
            return None
    
    def _enforce_t_plus_one(self, current_date: datetime, positions: Dict) -> Dict:
        """
        Enforce T+1 trading rule by preventing same-day buy/sell operations.
        
        Args:
            current_date: Current trading date
            positions: Current positions dictionary
            
        Returns:
            Updated positions dictionary with T+1 constraints applied
        """
        sellable_positions = {}
        
        for symbol, position in positions.items():
            if self.trade_validator.can_sell_today(symbol, current_date):
                sellable_positions[symbol] = position
        return sellable_positions
    def get_unsellable_positions(self, current_date: Optional[datetime] = None) -> Dict[str, datetime]:
        """
        Get positions that cannot be sold due to T+1 rule.
        
        Args:
            current_date: Date to check against (defaults to last data date)
            
        Returns:
            Dictionary mapping symbol to buy date for unsellable positions
        """
        if current_date is None:
            raw_date = self.data.index[-1]
            if isinstance(raw_date, pd.Timestamp):
                checked_date: datetime = raw_date.to_pydatetime()
            elif isinstance(raw_date, datetime):
                checked_date = raw_date
            else:
                checked_date = pd.Timestamp(cast(Any, raw_date)).to_pydatetime()
        else:
            checked_date = current_date

        return self.trade_validator.get_unsellable_positions(checked_date)
    
    def validate_strategy_compliance(self, strategy_instance: Strategy) -> Dict[str, Any]:
        """
        Validate that a strategy instance complies with A-shares rules.
        
        Args:
            strategy_instance: Strategy to validate
            
        Returns:
            Dictionary containing compliance check results
        """
        compliance_results = {
            'is_ashares_compatible': False,
            'supports_t_plus_one': False,
            'prevents_short_selling': False,
            'has_position_limits': False,
            'warnings': [],
            'recommendations': []
        }
        
        # Check if strategy is A-shares compatible
        if isinstance(strategy_instance, ASharesStrategy):
            compliance_results['is_ashares_compatible'] = True
            compliance_results['supports_t_plus_one'] = True
            compliance_results['prevents_short_selling'] = True
        else:
            compliance_results['warnings'].append(
                "Strategy is not derived from ASharesStrategy class"
            )
            compliance_results['recommendations'].append(
                "Consider extending ASharesStrategy for better A-shares compliance"
            )
        
        # Check for position management methods
        if hasattr(strategy_instance, 'can_sell_today'):
            compliance_results['supports_t_plus_one'] = True
        else:
            compliance_results['warnings'].append(
                "Strategy does not implement T+1 rule checking"
            )
        
        if hasattr(strategy_instance, 'validate_trade'):
            compliance_results['prevents_short_selling'] = True
        else:
            compliance_results['warnings'].append(
                "Strategy does not implement trade validation"
            )
        
        # Check for position limits
        if hasattr(strategy_instance, 'calculate_position_size'):
            compliance_results['has_position_limits'] = True
        else:
            compliance_results['recommendations'].append(
                "Implement position sizing methods for better risk management"
            )
        
        return compliance_results


class ASharesBacktestWrapper:
    """
    Wrapper class for running backtests on multiple stocks with A-shares constraints.
    Provides a higher-level interface for portfolio backtesting.
    """
    
    def __init__(self, 
                 data_provider: DataProvider,
                 strategy_manager: StrategyManager,
                 pattern_detector: Optional[MarketPatternDetector] = None,
                 stock_picker: Optional[StockPicker] = None):
        """
        Initialize the wrapper for multi-stock backtesting.
        
        Args:
            data_provider: Data provider for market data
            strategy_manager: Manager for trading strategies
            pattern_detector: Market pattern detection system
            stock_picker: Stock selection system
        """
        self.data_provider = data_provider
        self.strategy_manager = strategy_manager
        self.pattern_detector = pattern_detector
        self.stock_picker = stock_picker
        self.trade_validator = TradeValidator()
        
    def run_portfolio_backtest(self, 
                              start_date: str, 
                              end_date: str, 
                              initial_cash: float = 100000,
                              commission: float = 0.001,
                              max_positions: int = 10,
                              verbose: bool = True if LOG_LEVEL == "DEBUG" else False) -> Dict[str, Any]:
        """
        Run portfolio backtest for the specified date range with A-shares constraints.
        
        Args:
            start_date: Start date in YYYY-MM-DD format
            end_date: End date in YYYY-MM-DD format
            initial_cash: Initial cash amount
            commission: Commission rate
            max_positions: Maximum number of positions to hold
            verbose: Whether to print detailed daily logs
            
        Returns:
            Dictionary containing backtest results with A-shares compliance
        """ 
        try:
            import time
            
            # Get trading calendar with timing
            if verbose:
                logger.info(f"Getting trading calendar from {start_date} to {end_date}...")
            
            calendar_start = time.time()
            trading_dates = self.data_provider.get_trading_calendar(start_date, end_date)
            calendar_time = time.time() - calendar_start
            
            if not trading_dates:
                raise IBacktestError(f"No trading dates found between {start_date} and {end_date}")
            
            if verbose:
                logger.info(f"Found {len(trading_dates)} trading days in {calendar_time:.2f}s")
                logger.info(f"Starting with ¥{initial_cash:,.2f} initial cash")
                logger.info(f"Max positions: {max_positions}")
                logger.info("=" * 80)
            
            # Initialize results tracking
            results = {
                'start_date': start_date,
                'end_date': end_date,
                'initial_cash': initial_cash,
                'commission': commission,
                'max_positions': max_positions,
                'trading_dates': trading_dates,
                'daily_results': [],
                'final_portfolio_value': initial_cash,
                'total_return': 0.0,
                'trades': [],
                'ashares_compliance': {
                    'total_trades': 0,
                    't_plus_one_violations': 0,
                    'short_selling_violations': 0,
                    'compliance_rate': 1.0
                }
            }
            
            # Run daily backtesting loop with A-shares constraints
            current_cash = initial_cash
            positions = {}

            for i, trade_date in enumerate(trading_dates, 1):
                trade_date = datetime.strptime(trade_date, '%Y%m%d').strftime('%Y-%m-%d')
                
                if verbose:
                    logger.info(f"\nDay {i}/{len(trading_dates)}: {trade_date}")
                    logger.info(f"Cash: ¥{current_cash:,.2f}")
                    logger.info(f"Positions: {len(positions)}")
                
                day_start_time = time.time()
                daily_result = self._execute_daily_workflow(
                    trade_date, current_cash, positions, max_positions, verbose=verbose
                )
                day_time = time.time() - day_start_time
                
                results['daily_results'].append(daily_result)
                
                # Update cash and positions
                current_cash = daily_result['cash']
                positions = daily_result['positions']
                
                # Collect trades for compliance checking
                results['trades'].extend(daily_result.get('trades', []))
                
                # Calculate daily portfolio value
                portfolio_value = self._calculate_portfolio_value(current_cash, positions, trade_date)
                daily_return = (portfolio_value - initial_cash) / initial_cash
                
                if verbose:
                    logger.info(f"Portfolio Value: ¥{portfolio_value:,.2f}")
                    logger.info(f"Daily Return: {daily_return:.2%}")
                    logger.info(f"Trades: {len(daily_result.get('trades', []))}")
                    if daily_result.get('selected_stocks'):
                        logger.info(f"Selected Stocks: {', '.join(daily_result['selected_stocks'][:5])}" + 
                              (f" (+{len(daily_result['selected_stocks'])-5} more)" if len(daily_result['selected_stocks']) > 5 else ""))
                    logger.info(f"Processing time: {day_time:.2f}s")
                    
                    # Show T+1 constraints
                    if daily_result['ashares_constraints']['t_plus_one_blocked'] > 0:
                        logger.info(f"T+1 blocked positions: {daily_result['ashares_constraints']['t_plus_one_blocked']}")
                    
                    if verbose and len(trading_dates) <= 10:  # Only show detailed trade info for short runs
                        for trade in daily_result.get('trades', []):
                            action = trade.get('action', 'N/A')
                            symbol = trade.get('symbol', 'N/A')
                            quantity = trade.get('quantity', 0)
                            price = trade.get('price', 0)
                            logger.info(f"  {action} {quantity} shares of {symbol} at ¥{price:.2f}")
                
            # Calculate final results
            results['final_portfolio_value'] = self._calculate_portfolio_value(
                current_cash, positions, trading_dates[-1]
            )
            results['total_return'] = (
                (results['final_portfolio_value'] - initial_cash) / initial_cash
            )
            
            # Validate A-shares compliance
            results['ashares_compliance'] = self._validate_portfolio_compliance(results['trades'])
            
            return results
            
        except Exception as e:
            raise IBacktestError(f"Portfolio backtest execution failed: {str(e)}") from e
    
    def _execute_daily_workflow(self, 
                               trade_date: str, 
                               current_cash: float, 
                               positions: Dict[str, Any],
                               max_positions: int = 10,
                               verbose: bool = False) -> Dict[str, Any]:
        """
        Execute the daily backtesting workflow with A-shares constraints.
        
        Args:
            trade_date: Current trading date
            current_cash: Available cash
            positions: Current positions
            max_positions: Maximum number of positions allowed
            
        Returns:
            Dictionary containing daily results with A-shares compliance
        """
        current_date = datetime.strptime(trade_date, '%Y-%m-%d')
        
        daily_result = {
            'date': trade_date,
            'cash': current_cash,
            'positions': positions.copy(),
            'market_pattern': 'normal_market',
            'selected_stocks': [],
            'trades': [],
            'sellable_positions': {},
            'ashares_constraints': {
                'unsellable_positions': [],
                't_plus_one_blocked': 0
            }
        }
        
        try:
            import time
            
            # Apply T+1 constraints - determine which positions can be sold today
            if verbose:
                logger.info("  Applying T+1 constraints...")
            t1_start = time.time()
            sellable_positions = self._enforce_t_plus_one(current_date, positions)
            daily_result['sellable_positions'] = sellable_positions
            
            # Track T+1 blocked positions
            unsellable = self.trade_validator.get_unsellable_positions(current_date)
            daily_result['ashares_constraints']['unsellable_positions'] = list(unsellable.keys())
            daily_result['ashares_constraints']['t_plus_one_blocked'] = len(unsellable)
            t1_time = time.time() - t1_start
            
            if verbose:
                logger.info(f"    T+1 constraints applied in {t1_time:.3f}s, {len(sellable_positions)} sellable positions")
            
            # Detect market pattern if detector available
            if self.pattern_detector:
                if verbose:
                    logger.info("  Detecting market pattern...")
                pattern_start = time.time()
                market_data = self._get_market_data_for_pattern_detection(trade_date)
                if not market_data.empty:
                    daily_result['market_pattern'] = self.pattern_detector.detect_pattern(
                        market_data, trade_date
                    )
                pattern_time = time.time() - pattern_start
                if verbose:
                    logger.info(f"    Pattern detected: {daily_result['market_pattern']} in {pattern_time:.3f}s")
            
            # Select stocks if picker available
            if self.stock_picker:
                if verbose:
                    logger.info("  Selecting stocks...")
                picker_start = time.time()
                daily_result['selected_stocks'] = self.stock_picker.pick_stocks(trade_date)
                picker_time = time.time() - picker_start
                if verbose:
                    logger.info(f"    Selected {len(daily_result['selected_stocks'])} stocks in {picker_time:.3f}s")
            
            # Get appropriate strategy for market pattern
            if verbose:
                logger.info(f"  Getting strategy for {daily_result['market_pattern']} market...")
            strategy = self.strategy_manager.get_strategy(daily_result['market_pattern'])
            
            # Execute A-shares compliant trading logic
            if verbose:
                logger.info("  Executing trading logic...")
            trading_start = time.time()
            trades_executed = self._execute_ashares_trading_logic(
                strategy, trade_date, current_cash, positions, 
                sellable_positions, daily_result['selected_stocks'], max_positions, verbose
            )
            trading_time = time.time() - trading_start
            
            daily_result['trades'] = trades_executed
            
            if verbose:
                logger.info(f"    Executed {len(trades_executed)} trades in {trading_time:.3f}s")
            
            # Update cash and positions based on executed trades
            if verbose:
                logger.info("  Updating portfolio state...")
            update_start = time.time()
            updated_cash, updated_positions = self._update_portfolio_state(
                current_cash, positions, trades_executed
            )
            update_time = time.time() - update_start
            
            daily_result['cash'] = updated_cash
            daily_result['positions'] = updated_positions
            
            # Calculate and store portfolio value
            portfolio_value = self._calculate_portfolio_value(updated_cash, updated_positions, trade_date)
            daily_result['portfolio_value'] = portfolio_value
            
            if verbose:
                logger.info(f"    Portfolio updated in {update_time:.3f}s")
            
        except Exception as e:
            raise IBacktestError(f"Daily workflow execution failed for {trade_date}: {str(e)}")
        
        return daily_result
    
    def _execute_ashares_trading_logic(self,
                                     strategy,
                                     trade_date: str,
                                     current_cash: float,
                                     positions: Dict[str, Any],
                                     sellable_positions: Dict[str, Any],
                                     selected_stocks: List[str],
                                     max_positions: int,
                                     verbose: bool = False) -> List[Dict[str, Any]]:
        """
        Execute trading logic with A-shares constraints.
        
        Args:
            strategy: Trading strategy instance
            trade_date: Current trading date
            current_cash: Available cash
            positions: All current positions
            sellable_positions: Positions that can be sold today (T+1 compliant)
            selected_stocks: Stocks selected by picker
            max_positions: Maximum positions allowed
            verbose: Whether to print detailed logs
            
        Returns:
            List of executed trades
        """
        trades_executed = []
        current_date = datetime.strptime(trade_date, '%Y-%m-%d')
        
        if verbose:
            logger.info("    Trading Logic Details:")
            logger.info(f"      Available cash: ¥{current_cash:,.2f}")
            logger.info(f"      Current positions: {len(positions)}")
            logger.info(f"      Sellable positions: {len(sellable_positions)}")
            logger.info(f"      Selected stocks: {len(selected_stocks)}")
            logger.info(f"      Max positions: {max_positions}")
            logger.info(f"      Strategy type: {type(strategy).__name__}")
        
        # Check if strategy has required methods
        has_should_sell = hasattr(strategy, 'should_sell')
        has_should_buy = hasattr(strategy, 'should_buy')
        
        if verbose:
            logger.info(f"      Strategy methods: should_sell={has_should_sell}, should_buy={has_should_buy}")
        
        # Process sell signals for sellable positions only
        if verbose and sellable_positions:
            logger.info(f"    Checking SELL signals for {len(sellable_positions)} positions...")
            
        for symbol in list(sellable_positions.keys()):
            position = sellable_positions[symbol]
            
            try:
                if verbose:
                    logger.info(f"      Checking sell for {symbol} (shares: {position.get('shares', 0)})")
                
                # Get current stock data for decision making
                stock_data = self._get_stock_data_for_date(symbol, trade_date)
                if stock_data.empty:
                    if verbose:
                        logger.error(f"        No stock data available for {symbol}")
                    continue

                # TODO: Bull/Normal/Bear market strategy
                current_price = stock_data['high'].iloc[-1]
                if verbose:
                    logger.info(f"        Current price: ¥{current_price:.2f}, position: {position['avg_price']}")
                # Pareto rule, trading 30 minutes before ending.
                # Capital flow: main funds, northbound funds
                # Emotional hotspots
                # Plate block rotation
                # 2 days before and 3 days after major holidays are regarded as bear markets, and operate with caution.
                # hold_stock_days = 0
                # stop_win_lose_ratio = 0.0
                # cash_ratio = 0.0

                if has_should_sell:
                    should_sell_decision = strategy.should_sell(symbol, stock_data, position)
                    if should_sell_decision:
                        pass
                    if verbose:
                        logger.info(f"        Strategy sell decision: {should_sell_decision}")
                    
                    # TODO: high price
                    if (current_price >= position['avg_price'] * (1 + 0.15)
                        or current_price <= position['avg_price'] * (1 - 0.08)
                        or count_trading_days_between(
                            position['buy_date'].strftime('%Y-%m-%d') if isinstance(position['buy_date'], datetime) else str(position['buy_date']),
                            current_date.strftime('%Y-%m-%d')
                        ) > 5
                        or should_sell_decision):

                        sell_trade = {
                            'date': current_date,
                            'symbol': symbol,
                            'action': 'SELL',
                            'quantity': position.get('shares', 0),
                            'price': current_price,
                            'reason': 'strategy_signal'
                        }
                        
                        # Validate trade
                        if self._validate_and_execute_trade(sell_trade):
                            trades_executed.append(sell_trade)
                            if verbose:
                                logger.info(f"        SELL trade executed: {position.get('shares', 0)} shares at ¥{current_price:.2f}")
                        elif verbose:
                            logger.error("        SELL trade validation failed")
                    elif verbose:
                        logger.info("        Strategy says don't sell")
                else:
                    if verbose:
                        logger.error("        Strategy has no should_sell method")
                        
            except Exception as e:
                raise IBacktestError(f"Error processing sell signal for {symbol}: {str(e)}")

        # Process buy signals for selected stocks
        if len(positions) < max_positions and selected_stocks:
            available_slots = max_positions - len(positions)
            
            if verbose:
                logger.info(f"    Checking BUY signals for {len(selected_stocks)} selected stocks...")
                logger.info(f"      Available slots: {available_slots}")

            # TODO: Buy each stock max position, must fixed can buy stocks num.
            can_buy_stocks = []
            for symbol in selected_stocks[:available_slots]:
                if verbose:
                    logger.info(f"      Checking buy for {symbol}")

                if symbol in positions:
                    if verbose:
                        logger.info(f"      Skipping {symbol} - already have position")
                    continue

                stock_data = self._get_stock_data_for_date(symbol, trade_date)
                if stock_data.empty:
                    if verbose:
                        logger.error(f"        No stock data available for {symbol}")
                    continue

                # Can buy stocks: current day open price must > previous day close price. 
                current_price = stock_data['open'].iloc[-1]
                pre_price = stock_data['pre_close'].iloc[-1]
                if pre_price >= current_price:
                    if verbose:
                        logger.info(f"        Previous close price ¥{pre_price:.2f} >= current open price ¥{current_price:.2f}, skip buy")
                    continue
                if verbose:
                    logger.info(f"        Current price: ¥{current_price:.2f}")
                    
                can_buy_stocks.append([symbol, current_price, stock_data])

            buyed_num = 0
            for symbol, current_price, stock_data in can_buy_stocks:
                try:
                    if verbose:
                        logger.info(f"      Checking buy for {symbol}")

                    # Check if strategy wants to buy
                    if has_should_buy:
                        should_buy_decision = strategy.should_buy(symbol, stock_data)
                        if verbose:
                            logger.info(f"        Strategy buy decision: {should_buy_decision}")

                        # TODO: force buy if no decision.
                        if True or should_buy_decision:
                            # Calculate position size
                            position_size = self._calculate_position_size(
                                current_cash, current_price, len(can_buy_stocks) - buyed_num
                            )
                            
                            if verbose:
                                logger.info(f"        Calculated position size: {position_size} shares")
                                logger.info(f"        Required cash: ¥{position_size * current_price * 1.001:,.2f}")
                            
                            if position_size > 0:
                                # Create buy trade
                                buy_trade = {
                                    'date': current_date,
                                    'symbol': symbol,
                                    'action': 'BUY',
                                    'quantity': position_size,
                                    'price': current_price,
                                    'reason': 'strategy_signal'
                                }
                                
                                # Validate and execute trade
                                if self._validate_and_execute_trade(buy_trade):
                                    trades_executed.append(buy_trade)
                                    
                                    # Update available cash for next trade
                                    trade_cost = position_size * current_price * (1 + 0.001)  # Include commission
                                    current_cash -= trade_cost
                                    buyed_num += 1
                                    
                                    if verbose:
                                        logger.info(f"        BUY trade executed: {position_size} shares at ¥{current_price:.2f}")
                                        logger.info(f"        Remaining cash: ¥{current_cash:,.2f}")
                                elif verbose:
                                    logger.error("        BUY trade validation failed")
                            elif verbose:
                                logger.warning("        Position size is 0 - insufficient cash or other constraint")
                        elif verbose:
                            logger.info("        Strategy says don't buy")
                    else:
                        if verbose:
                            logger.error("        Strategy has no should_buy method")
                                
                except Exception as e:
                    raise IBacktestError(f"Error processing buy signal for {symbol}: {str(e)}")
        elif verbose:
            if len(positions) >= max_positions:
                logger.warning(f"    No buy signals checked - already at max positions ({len(positions)}/{max_positions})")
            elif not selected_stocks:
                logger.warning("    No buy signals checked - no stocks selected")
        
        if verbose:
            logger.info(f"    Total trades executed: {len(trades_executed)}")
        
        return trades_executed
    
    def _enforce_t_plus_one(self, current_date: datetime, positions: Dict) -> Dict:
        """
        Enforce T+1 trading rule by filtering sellable positions.
        
        Args:
            current_date: Current trading date
            positions: Current positions dictionary
            
        Returns:
            Dictionary of positions that can be sold today
        """
        sellable_positions = {}
        
        for symbol, position in positions.items():
            if self.trade_validator.can_sell_today(symbol, current_date):
                sellable_positions[symbol] = position
        
        return sellable_positions
    
    def _validate_and_execute_trade(self, trade: Dict[str, Any]) -> bool:
        """
        Validate trade against A-shares rules and execute if valid.
        
        Args:
            trade: Trade dictionary to validate
            
        Returns:
            True if trade was validated and executed successfully
        """
        try:
            # Validate against A-shares rules
            self.trade_validator.validate_trade(trade, trade['date'])
            
            # Update position tracking
            if trade['action'] == 'BUY':
                self.trade_validator.record_buy(trade['symbol'], trade['date'])
            elif trade['action'] == 'SELL':
                self.trade_validator.record_sell(trade['symbol'])
            
            return True
            
        except TradeValidationError as e:
            logger.error(f"Trade validation failed: {str(e)}")
            trade['validation_error'] = str(e)
            return False
    
    def _calculate_position_size(self, available_cash: float, price: float, remaining_slots: int) -> int:
        """
        Calculate position size based on available cash and remaining position slots.
        
        Args:
            available_cash: Cash available for investment
            price: Current stock price
            remaining_slots: Number of remaining position slots
            
        Returns:
            Number of shares to buy
        """
        if remaining_slots <= 0 or price <= 0:
            return 0
        
        # TODO: Use equal weight allocation
        #max_investment = available_cash / remaining_slots * 0.95  # Leave 5% buffer
        max_investment = available_cash / remaining_slots * 1
        
        # Calculate shares (minimum lot size is 100 in A-shares)
        shares = int(max_investment / price)
        shares = (shares // 100) * 100  # Round down to nearest 100
        
        return max(0, shares)
    
    def _update_portfolio_state(self, 
                               current_cash: float, 
                               positions: Dict[str, Any], 
                               trades: List[Dict[str, Any]]) -> tuple:
        """
        Update portfolio cash and positions based on executed trades.
        
        Args:
            current_cash: Current cash amount
            positions: Current positions
            trades: List of executed trades
            
        Returns:
            Tuple of (updated_cash, updated_positions)
        """
        updated_cash = current_cash
        updated_positions = positions.copy()
        
        for trade in trades:
            symbol = trade['symbol']
            action = trade['action']
            quantity = trade['quantity']
            price = trade['price']
            commission = price * quantity * 0.001  # 0.1% commission
            
            if action == 'BUY':
                # Deduct cash
                total_cost = price * quantity + commission
                updated_cash -= total_cost
                
                # Add/update position
                if symbol in updated_positions:
                    # Average down
                    existing = updated_positions[symbol]
                    total_shares = existing['shares'] + quantity
                    avg_price = ((existing['shares'] * existing['avg_price']) + 
                               (quantity * price)) / total_shares
                    updated_positions[symbol] = {
                        'shares': total_shares,
                        'avg_price': avg_price,
                        'buy_date': trade['date']
                    }
                else:
                    # New position
                    updated_positions[symbol] = {
                        'shares': quantity,
                        'avg_price': price,
                        'buy_date': trade['date']
                    }
            
            elif action == 'SELL':
                # Add cash
                total_proceeds = price * quantity - commission
                updated_cash += total_proceeds
                
                # Remove/update position
                if symbol in updated_positions:
                    existing = updated_positions[symbol]
                    if existing['shares'] <= quantity:
                        # Sell entire position
                        del updated_positions[symbol]
                    else:
                        # Partial sell
                        updated_positions[symbol]['shares'] -= quantity
        
        return updated_cash, updated_positions
    
    def _validate_portfolio_compliance(self, trades: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Validate portfolio trades for A-shares compliance.
        
        Args:
            trades: List of all trades executed
            
        Returns:
            Dictionary containing compliance metrics
        """
        compliance_results = {
            'total_trades': len(trades),
            't_plus_one_violations': 0,
            'short_selling_violations': 0,
            'invalid_trades': [],
            'compliance_rate': 1.0
        }
        
        violations = 0
        
        for trade in trades:
            if 'validation_error' in trade:
                violations += 1
                compliance_results['invalid_trades'].append(trade)
                
                error_msg = trade['validation_error']
                if 'T+1' in error_msg:
                    compliance_results['t_plus_one_violations'] += 1
                elif 'short' in error_msg.lower():
                    compliance_results['short_selling_violations'] += 1
        
        if compliance_results['total_trades'] > 0:
            compliance_results['compliance_rate'] = (
                (compliance_results['total_trades'] - violations) / 
                compliance_results['total_trades']
            )
        
        return compliance_results
    
    def _get_stock_data_for_date(self, symbol: str, trade_date: str) -> pd.DataFrame:
        """Get stock data for a specific date."""
        try:
            # Get sufficient data for technical analysis (strategies need 50+ days)
            start_date = self._get_lookback_date(trade_date, 60)  # Increased from 5 to 60
            return self.data_provider.get_stock_data([symbol], start_date, trade_date)
        except Exception as e:
            raise IBacktestError(f"Failed to get stock data for {symbol} on {trade_date}: {str(e)}")

    def _get_market_data_for_pattern_detection(self, trade_date: str) -> pd.DataFrame:
        """Get market index data for pattern detection."""
        try:
            # Use CSI 300 index for pattern detection
            lookback_date = self._get_lookback_date(trade_date, 30)
            return self.data_provider.get_index_data('000300.SH', lookback_date, trade_date)
        except Exception as e:
            raise IBacktestError(f"Failed to get market data for pattern detection on {trade_date}: {str(e)}")

    def _get_lookback_date(self, trade_date: str, days: int = 30) -> str:
        """Get date that is 'days' trading days before trade_date."""
        # Import here to avoid circular import
        from ..utils.trading_calendar import get_trading_days_before
        
        # Use trading calendar for accurate trading days calculation
        return get_trading_days_before(trade_date, days)
    
    def _calculate_portfolio_value(self, 
                                 cash: float, 
                                 positions: Dict[str, Any], 
                                 date: str) -> float:
        """
        Calculate total portfolio value including cash and position values.
        
        Args:
            cash: Current cash amount
            positions: Dictionary of current positions
            date: Date for position valuation
            
        Returns:
            Total portfolio value
        """
        total_value = cash
        
        # Add value of all positions
        for symbol, position in positions.items():
            try:
                # Get current price for position valuation
                stock_data = self._get_stock_data_for_date(symbol, date)
                if not stock_data.empty:
                    current_price = stock_data['close'].iloc[-1]
                    position_value = position['shares'] * current_price
                    total_value += position_value
            except Exception:
                import traceback
                traceback.print_exc()
                raise IBacktestError(f"Failed to get price for {symbol} on {date}")
        
        return total_value
    
    def validate_trade(self, trade: Dict[str, Any], current_date: datetime) -> bool:
        """Validate trade against A-shares rules."""
        return self.trade_validator.validate_trade(trade, current_date)
    
    def get_compliance_report(self) -> Dict[str, Any]:
        """
        Generate a comprehensive compliance report for A-shares rules.
        
        Returns:
            Dictionary containing detailed compliance analysis
        """
        return {
            'validator_state': {
                'tracked_positions': len(self.trade_validator.position_buy_dates),
                'position_buy_dates': dict(self.trade_validator.position_buy_dates)
            },
            'total_daily_trades': len(self.daily_trades),
            'compliance_summary': self._validate_portfolio_compliance([])
        }
    
    def reset_validator_state(self):
        """Reset the trade validator state for new backtest runs."""
        self.trade_validator = TradeValidator()
        self.position_buy_dates = {}
        self.daily_trades = []