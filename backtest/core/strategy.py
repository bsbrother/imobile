"""
Base strategy classes with China A-shares constraints.
"""

from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
import pandas as pd
from backtesting import Strategy as BacktestingStrategy

from .interfaces import Strategy
from ..utils.exceptions import TradeValidationError


class StrategyConfig:
    """Configuration class for strategy parameters."""

    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.validate_config()

    def validate_config(self):
        """Validate strategy configuration parameters."""
        required_fields = ['position_sizing', 'risk_management']
        for field in required_fields:
            if field not in self.config:
                raise ValueError(f"Missing required config field: {field}")

        # Validate position sizing parameters
        pos_sizing = self.config['position_sizing']
        if 'max_position_pct' not in pos_sizing or not 0 < pos_sizing['max_position_pct'] <= 1:
            raise ValueError("max_position_pct must be between 0 and 1")

        # Validate risk management parameters
        risk_mgmt = self.config['risk_management']
        if 'stop_loss_pct' in risk_mgmt and not 0 < risk_mgmt['stop_loss_pct'] < 1:
            raise ValueError("stop_loss_pct must be between 0 and 1")
        if 'profit_target_pct' in risk_mgmt and risk_mgmt['profit_target_pct'] <= 0:
            raise ValueError("profit_target_pct must be positive")

    def get(self, key: str, default: Any = None) -> Any:
        """Get configuration value."""
        return self.config.get(key, default)

    def get_nested(self, *keys: str, default: Any = None) -> Any:
        """Get nested configuration value."""
        value = self.config
        for key in keys:
            if isinstance(value, dict) and key in value:
                value = value[key]
            else:
                return default
        return value


class BaseStrategy(Strategy):
    """Base strategy class with common functionality."""

    def __init__(self, config: Dict[str, Any]):
        self.strategy_config = StrategyConfig(config)
        self.positions = {}
        self.buy_dates = {}  # Track when positions were bought for T+1 rule
        self.trade_history = []

    @abstractmethod
    def init(self):
        """Initialize strategy - to be implemented by subclasses."""
        pass

    @abstractmethod
    def next(self):
        """Execute strategy logic - to be implemented by subclasses."""
        pass

    def can_sell_today(self, symbol: str, current_date: datetime) -> bool:
        """Check if position can be sold today (T+1 rule)."""
        if symbol not in self.buy_dates:
            return True

        buy_date = self.buy_dates[symbol]
        return current_date > buy_date

    def validate_trade(self, action: str, symbol: str, quantity: int) -> bool:
        """Validate trade against A-shares rules."""
        if action == "SELL" and quantity < 0:
            raise TradeValidationError("Short selling not allowed in A-shares market")

        if action == "BUY" and quantity <= 0:
            raise TradeValidationError("Buy quantity must be positive")

        return True

    def record_buy_date(self, symbol: str, date: datetime):
        """Record when a position was bought for T+1 tracking."""
        self.buy_dates[symbol] = date

    def calculate_position_size(self, symbol: str, available_cash: float, current_price: float) -> int:
        """
        Calculate position size based on strategy configuration.

        Args:
            symbol: Stock symbol
            available_cash: Available cash for investment
            current_price: Current stock price

        Returns:
            Number of shares to buy
        """
        # Use direct dictionary access to avoid method issues
        position_config = self.strategy_config.config.get('position_sizing', {})
        max_position_pct = position_config.get('max_position_pct', 0.1)
        min_lot = position_config.get('min_lot_size', 100)

        max_investment = available_cash * max_position_pct

        # Calculate shares (round down to avoid exceeding available cash)
        shares = int(max_investment / current_price)

        # Ensure minimum lot size (typically 100 shares in A-shares)
        if shares < min_lot:
            return 0

        # Round down to nearest lot
        shares = (shares // min_lot) * min_lot

        return shares

    def should_stop_loss(self, symbol: str, current_price: float, buy_price: float) -> bool:
        """Check if position should be stopped out."""
        risk_config = self.strategy_config.config.get('risk_management', {})
        stop_loss_pct = risk_config.get('stop_loss_pct')
        if stop_loss_pct is None:
            return False

        loss_pct = (buy_price - current_price) / buy_price
        return loss_pct >= stop_loss_pct

    def should_take_profit(self, symbol: str, current_price: float, buy_price: float) -> bool:
        """Check if position should take profit."""
        risk_config = self.strategy_config.config.get('risk_management', {})
        profit_target_pct = risk_config.get('profit_target_pct')
        if profit_target_pct is None:
            return False

        profit_pct = (current_price - buy_price) / buy_price
        return profit_pct >= profit_target_pct

    def should_average_down(self, symbol: str, current_price: float, position_price: float) -> bool:
        """
        Determine if should average down on existing position.

        Args:
            symbol: Stock symbol
            current_price: Current market price
            position_price: Average position price

        Returns:
            True if should average down
        """
        position_config = self.strategy_config.config.get('position_sizing', {})

        # Check if averaging down is enabled
        if not position_config.get('allow_averaging_down', False):
            return False

        # Check minimum decline threshold
        min_decline_pct = position_config.get('averaging_down_threshold', 0.05)
        decline_pct = (position_price - current_price) / position_price

        if decline_pct < min_decline_pct:
            return False

        # Check maximum position size
        max_avg_positions = position_config.get('max_averaging_positions', 2)
        current_positions = self.positions.get(symbol, {}).get('avg_count', 1)

        return current_positions < max_avg_positions

    def record_trade(self, action: str, symbol: str, quantity: int, price: float, date: datetime, reason: str = ""):
        """Record trade for analysis."""
        trade = {
            'date': date,
            'symbol': symbol,
            'action': action,
            'quantity': quantity,
            'price': price,
            'reason': reason
        }
        self.trade_history.append(trade)


class ASharesStrategy(BacktestingStrategy, BaseStrategy):
    """
    Strategy class that integrates with backtesting.py while enforcing A-shares rules.
    """

    def __init__(self, config: Dict[str, Any]):
        BaseStrategy.__init__(self, config)
        self.market_pattern = "normal_market"
        self.stock_picker = None
        self.pattern_detector = None
        self.current_stocks = []
        self.indicators = {}

    def init(self):
        """Initialize strategy with backtesting.py framework."""
        # Initialize indicators and parameters
        self.setup_indicators()

    def next(self):
        """Execute strategy logic for current bar."""
        current_date = self.data.index[-1]

        # Detect market pattern if detector is available
        if self.pattern_detector:
            try:
                self.market_pattern = self.pattern_detector.detect_pattern(
                    self.data, current_date.strftime('%Y-%m-%d')
                )
            except Exception:
                # Fallback to normal_market pattern if detection fails
                self.market_pattern = "normal_market"

        # Apply strategy based on market pattern
        self.apply_strategy_config(self.market_pattern)

        # Execute trading logic
        self.execute_trading_logic()

    def setup_indicators(self):
        """Setup technical indicators used by strategy."""
        # Initialize common indicators
        from ..analysis.indicators import TechnicalIndicators

        close_prices = pd.Series(self.data.Close)
        high_prices = pd.Series(self.data.High)
        low_prices = pd.Series(self.data.Low)

        # Calculate indicators
        self.indicators['rsi'] = TechnicalIndicators.rsi(close_prices, 14)
        self.indicators['ma_20'] = TechnicalIndicators.moving_average(close_prices, 20)
        self.indicators['ma_50'] = TechnicalIndicators.moving_average(close_prices, 50)
        self.indicators['bb_upper'], self.indicators['bb_middle'], self.indicators['bb_lower'] = \
            TechnicalIndicators.bollinger_bands(close_prices, 20, 2.0)
        self.indicators['macd'], self.indicators['macd_signal'], self.indicators['macd_hist'] = \
            TechnicalIndicators.macd(close_prices)
        self.indicators['volatility'] = TechnicalIndicators.volatility(close_prices, 20)

    def apply_strategy_config(self, pattern: str):
        """Apply configuration based on market pattern."""
        pattern_config = self.strategy_config.get(pattern, {})
        if pattern_config:
            self.current_config = pattern_config
        else:
            self.current_config = self.strategy_config.get('default', {})

    def execute_trading_logic(self):
        """Execute buy/sell logic - to be implemented by subclasses."""
        current_date = self.data.index[-1]
        current_price = self.data.Close[-1]

        # Check existing positions for sell signals
        for symbol in list(self.positions.keys()):
            position = self.positions[symbol]

            # Check if we can sell today (T+1 rule)
            if not self.can_sell_today(symbol, current_date):
                continue

            # Check stop loss
            if self.should_stop_loss(symbol, current_price, position.get('buy_price', current_price)):
                self.close_position(symbol, "stop_loss")
                continue

            # Check profit target
            if self.should_take_profit(symbol, current_price, position.get('buy_price', current_price)):
                self.close_position(symbol, "profit_target")
                continue

            # Check strategy-specific sell signals
            if self.should_sell(symbol, self.data, position):
                self.close_position(symbol, "strategy_signal")

        # Check for new buy opportunities
        position_config = self.strategy_config.config.get('position_sizing', {})
        max_positions = position_config.get('max_positions', 10)
        if self.stock_picker and len(self.positions) < max_positions:
            try:
                # Direct trading decisions in Real-time strategy execution, don't need dynamic stock selection.
                """
                candidate_stocks = self.stock_picker.pick_stocks(
                    current_date.strftime('%Y-%m-%d'),
                )
                """
                candidate_stocks = self.stock_picker.stock_pool

                for symbol in candidate_stocks:
                    if symbol not in self.positions and self.should_buy(symbol, self.data):
                        self.open_position(symbol, current_price, "strategy_signal")

            except Exception:
                # Continue without stock picker if it fails
                pass

    def open_position(self, symbol: str, price: float, reason: str):
        """Open a new position."""
        try:
            # Calculate position size
            available_cash = getattr(self, 'cash', 100000)  # Default cash if not available
            shares = self.calculate_position_size(symbol, available_cash, price)

            if shares > 0:
                # Validate trade
                self.validate_trade("BUY", symbol, shares)

                # Record position
                self.positions[symbol] = {
                    'shares': shares,
                    'buy_price': price,
                    'buy_date': self.data.index[-1],
                    'avg_count': 1
                }

                # Record buy date for T+1 tracking
                self.record_buy_date(symbol, self.data.index[-1])

                # Record trade
                self.record_trade("BUY", symbol, shares, price, self.data.index[-1], reason)

                # Execute buy order (if using backtesting.py framework)
                if hasattr(self, 'buy'):
                    self.buy(size=shares)

        except Exception as e:
            # Log error but continue execution
            pass

    def close_position(self, symbol: str, reason: str):
        """Close an existing position."""
        if symbol not in self.positions:
            return

        try:
            position = self.positions[symbol]
            shares = position['shares']
            current_price = self.data.Close[-1]

            # Validate trade
            self.validate_trade("SELL", symbol, shares)

            # Record trade
            self.record_trade("SELL", symbol, shares, current_price, self.data.index[-1], reason)

            # Remove position
            del self.positions[symbol]
            if symbol in self.buy_dates:
                del self.buy_dates[symbol]

            # Execute sell order (if using backtesting.py framework)
            if hasattr(self, 'sell'):
                self.sell(size=shares)

        except Exception as e:
            # Log error but continue execution
            pass

    def should_buy(self, symbol: str, data: pd.DataFrame) -> bool:
        """Default buy logic - to be overridden by subclasses."""
        return False

    def should_sell(self, symbol: str, data: pd.DataFrame, position: Any) -> bool:
        """Default sell logic - to be overridden by subclasses."""
        return False
