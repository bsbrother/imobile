"""
Strategy manager for coordinating different trading strategies.
"""

from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
import pandas as pd
from dataclasses import dataclass, field

from ..core.interfaces import MarketPatternDetector
from ..core.strategy import BaseStrategy
from ..utils.exceptions import StrategyError


@dataclass
class StrategyPerformance:
    """Track performance metrics for a strategy."""
    pattern: str
    strategy_name: str
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    total_return: float = 0.0
    total_days_active: int = 0
    last_used: Optional[datetime] = None
    trade_history: List[Dict[str, Any]] = field(default_factory=list)
    
    @property
    def win_rate(self) -> float:
        """Calculate win rate percentage."""
        if self.total_trades == 0:
            return 0.0
        return (self.winning_trades / self.total_trades) * 100
    
    @property
    def average_return_per_trade(self) -> float:
        """Calculate average return per trade."""
        if self.total_trades == 0:
            return 0.0
        return self.total_return / self.total_trades
    
    @property
    def average_return_per_day(self) -> float:
        """Calculate average return per day active."""
        if self.total_days_active == 0:
            return 0.0
        return self.total_return / self.total_days_active


@dataclass
class StrategySwitch:
    """Record of strategy switches."""
    timestamp: datetime
    from_pattern: str
    to_pattern: str
    from_strategy: str
    to_strategy: str
    reason: str


class StrategyManager:
    """
    Enhanced strategy manager with automatic switching, performance tracking, and validation.
    """
    
    def __init__(self, pattern_detector: Optional[MarketPatternDetector] = None):
        self.strategies: Dict[str, BaseStrategy] = {}
        self.default_strategy = "normal_market"
        self.pattern_detector = pattern_detector
        
        # Performance tracking
        self.performance_history: Dict[str, StrategyPerformance] = {}
        self.switch_history: List[StrategySwitch] = []
        self.current_strategy: Optional[str] = None
        self.current_pattern: Optional[str] = None
        
        # Configuration
        self.auto_switching_enabled = True
        self.min_pattern_confidence = 0.6
        self.pattern_stability_days = 3  # Days to wait before switching
        self.performance_tracking_enabled = True
        
        # Pattern tracking for stability
        self.recent_patterns: List[Tuple[datetime, str, float]] = []
    
    def register_strategy(self, pattern: str, strategy: BaseStrategy):
        """
        Register a strategy for a specific market pattern.
        
        Args:
            pattern: Market pattern name (normal_market, bull_market, bear_market, volatile_market)
            strategy: Strategy instance
        """
        if not isinstance(strategy, BaseStrategy):
            raise StrategyError(f"Strategy must be instance of BaseStrategy, got {type(strategy)}")
        
        self.strategies[pattern] = strategy
        
        # Initialize performance tracking
        if pattern not in self.performance_history:
            self.performance_history[pattern] = StrategyPerformance(
                pattern=pattern,
                strategy_name=strategy.__class__.__name__
            )
    
    def get_strategy(self, market_pattern: str) -> BaseStrategy:
        """
        Get strategy for the given market pattern.
        
        Args:
            market_pattern: Current market pattern
            
        Returns:
            Strategy instance for the pattern
            
        Raises:
            StrategyError: If strategy not found for pattern
        """
        if market_pattern in self.strategies:
            return self.strategies[market_pattern]
        elif self.default_strategy in self.strategies:
            return self.strategies[self.default_strategy]
        else:
            raise StrategyError(f"No strategy found for pattern '{market_pattern}' and no default strategy available")
    
    def get_current_strategy(self, market_data: pd.DataFrame, current_date: str) -> Tuple[BaseStrategy, str]:
        """
        Get the current strategy based on market pattern detection and switching logic.
        
        Args:
            market_data: Market data for pattern detection
            current_date: Current trading date
            
        Returns:
            Tuple of (strategy_instance, pattern_name)
        """
        if not self.auto_switching_enabled:
            # Use current strategy or default
            pattern = self.current_pattern or self.default_strategy
            return self.get_strategy(pattern), pattern
        
        # Detect current market pattern
        detected_pattern = self._detect_market_pattern(market_data, current_date)
        
        # Check if we should switch strategies
        should_switch, new_pattern = self._should_switch_strategy(detected_pattern, current_date)
        
        if should_switch and new_pattern:
            self._switch_strategy(new_pattern, current_date, "pattern_change")
        
        # Return current strategy
        current_pattern = self.current_pattern or self.default_strategy
        return self.get_strategy(current_pattern), current_pattern
    
    def _detect_market_pattern(self, market_data: pd.DataFrame, current_date: str) -> Optional[str]:
        """Detect current market pattern using pattern detector."""
        if not self.pattern_detector:
            return None
        
        try:
            pattern = self.pattern_detector.detect_pattern(market_data, current_date)
            confidence = self.pattern_detector.get_confidence(pattern, market_data)
            
            # Record pattern for stability tracking
            self.recent_patterns.append((
                datetime.strptime(current_date, '%Y-%m-%d'),
                pattern,
                confidence
            ))
            
            # Keep only recent patterns (last 10 trading days)
            from ..utils.trading_calendar import get_trading_days_before
            
            # Use trading days for more accurate pattern stability calculation
            cutoff_date_str = get_trading_days_before(current_date, 10)
            cutoff_date = datetime.strptime(cutoff_date_str, '%Y-%m-%d')
                
            self.recent_patterns = [
                (date, pat, conf) for date, pat, conf in self.recent_patterns
                if date >= cutoff_date
            ]
            
            return pattern if confidence >= self.min_pattern_confidence else None
            
        except Exception:
            return None
    
    def _should_switch_strategy(self, detected_pattern: Optional[str], current_date: str) -> Tuple[bool, Optional[str]]:
        """
        Determine if strategy should be switched based on pattern stability.
        
        Args:
            detected_pattern: Newly detected pattern
            current_date: Current date
            
        Returns:
            Tuple of (should_switch, new_pattern)
        """
        if not detected_pattern or detected_pattern == self.current_pattern:
            return False, None
        
        # Check pattern stability - require consistent pattern for several days
        if len(self.recent_patterns) < self.pattern_stability_days:
            return False, None
        
        # Check if pattern has been stable for required days
        recent_stable_patterns = self.recent_patterns[-self.pattern_stability_days:]
        stable_pattern = all(pat == detected_pattern for _, pat, _ in recent_stable_patterns)
        
        if stable_pattern and detected_pattern in self.strategies:
            return True, detected_pattern
        
        return False, None
    
    def _switch_strategy(self, new_pattern: str, current_date: str, reason: str):
        """
        Switch to a new strategy and record the switch.
        
        Args:
            new_pattern: New market pattern
            current_date: Current date
            reason: Reason for switch
        """
        old_pattern = self.current_pattern
        old_strategy = self.strategies[old_pattern].__class__.__name__ if old_pattern else "None"
        new_strategy = self.strategies[new_pattern].__class__.__name__
        
        # Record the switch
        switch_record = StrategySwitch(
            timestamp=datetime.strptime(current_date, '%Y-%m-%d'),
            from_pattern=old_pattern or "None",
            to_pattern=new_pattern,
            from_strategy=old_strategy,
            to_strategy=new_strategy,
            reason=reason
        )
        self.switch_history.append(switch_record)
        
        # Update current strategy
        self.current_pattern = new_pattern
        self.current_strategy = new_pattern
        
        # Update performance tracking
        if new_pattern in self.performance_history:
            self.performance_history[new_pattern].last_used = switch_record.timestamp
    
    def record_trade_performance(self, pattern: str, trade_return: float, trade_data: Dict[str, Any]):
        """
        Record trade performance for a strategy.
        
        Args:
            pattern: Market pattern/strategy used
            trade_return: Return from the trade (positive for profit, negative for loss)
            trade_data: Additional trade information
        """
        if not self.performance_tracking_enabled or pattern not in self.performance_history:
            return
        
        perf = self.performance_history[pattern]
        perf.total_trades += 1
        perf.total_return += trade_return
        
        if trade_return > 0:
            perf.winning_trades += 1
        else:
            perf.losing_trades += 1
        
        # Record trade details
        trade_record = {
            'timestamp': datetime.now(),
            'return': trade_return,
            'data': trade_data
        }
        perf.trade_history.append(trade_record)
        
        # Keep only recent trades (last 100)
        if len(perf.trade_history) > 100:
            perf.trade_history = perf.trade_history[-100:]
    
    def update_strategy_activity(self, pattern: str, days_active: int):
        """
        Update the number of days a strategy has been active.
        
        Args:
            pattern: Market pattern/strategy
            days_active: Number of days the strategy was active
        """
        if pattern in self.performance_history:
            self.performance_history[pattern].total_days_active += days_active
    
    def get_strategy_performance(self, pattern: str) -> Optional[StrategyPerformance]:
        """
        Get performance metrics for a strategy.
        
        Args:
            pattern: Market pattern/strategy name
            
        Returns:
            StrategyPerformance object or None if not found
        """
        return self.performance_history.get(pattern)
    
    def get_all_performance_metrics(self) -> Dict[str, StrategyPerformance]:
        """Get performance metrics for all strategies."""
        return self.performance_history.copy()
    
    def get_switch_history(self, days: Optional[int] = None) -> List[StrategySwitch]:
        """
        Get strategy switch history.
        
        Args:
            days: Number of recent days to include (None for all)
            
        Returns:
            List of strategy switches
        """
        if days is None:
            return self.switch_history.copy()
        
        # For switch history timestamps, we can use trading days for more accurate business context
        from ..utils.trading_calendar import get_trading_days_before
        
        try:
            current_date_str = datetime.now().strftime('%Y-%m-%d')
            cutoff_date_str = get_trading_days_before(current_date_str, days)
            cutoff_date = datetime.strptime(cutoff_date_str, '%Y-%m-%d')
        except (ValueError, RuntimeError):
            # If trading calendar fails, use calendar days as fallback for timestamp comparison
            cutoff_date = datetime.now() - timedelta(days=days)
            
        return [switch for switch in self.switch_history if switch.timestamp >= cutoff_date]
    
    def get_performance_summary(self) -> Dict[str, Any]:
        """
        Get a summary of all strategy performance.
        
        Returns:
            Dictionary with performance summary
        """
        summary = {
            'total_strategies': len(self.strategies),
            'total_switches': len(self.switch_history),
            'current_pattern': self.current_pattern,
            'strategies': {}
        }
        
        for pattern, perf in self.performance_history.items():
            summary['strategies'][pattern] = {
                'strategy_name': perf.strategy_name,
                'total_trades': perf.total_trades,
                'win_rate': perf.win_rate,
                'total_return': perf.total_return,
                'avg_return_per_trade': perf.average_return_per_trade,
                'avg_return_per_day': perf.average_return_per_day,
                'days_active': perf.total_days_active,
                'last_used': perf.last_used.isoformat() if perf.last_used else None
            }
        
        return summary
    
    def validate_strategies(self) -> Dict[str, List[str]]:
        """
        Validate all registered strategies.
        
        Returns:
            Dictionary with validation results
        """
        results = {
            'valid': [],
            'invalid': [],
            'warnings': []
        }
        
        required_patterns = ['normal_market', 'bull_market', 'bear_market', 'volatile_market']
        
        for pattern in required_patterns:
            if pattern not in self.strategies:
                results['warnings'].append(f"Missing strategy for pattern: {pattern}")
        
        for pattern, strategy in self.strategies.items():
            try:
                # Basic validation
                if not hasattr(strategy, 'should_buy') or not hasattr(strategy, 'should_sell'):
                    results['invalid'].append(f"{pattern}: Missing required methods")
                    continue
                
                # Check if strategy has valid configuration
                if not hasattr(strategy, 'strategy_config'):
                    results['warnings'].append(f"{pattern}: No strategy configuration found")
                
                results['valid'].append(pattern)
                
            except Exception as e:
                results['invalid'].append(f"{pattern}: Validation error - {str(e)}")
        
        return results
    
    def list_strategies(self) -> Dict[str, str]:
        """
        List all registered strategies.
        
        Returns:
            Dictionary mapping pattern names to strategy class names
        """
        return {pattern: strategy.__class__.__name__ for pattern, strategy in self.strategies.items()}
    
    def set_default_strategy(self, pattern: str):
        """
        Set the default strategy pattern.
        
        Args:
            pattern: Pattern name to use as default
            
        Raises:
            StrategyError: If pattern not registered
        """
        if pattern not in self.strategies:
            raise StrategyError(f"Cannot set default strategy: pattern '{pattern}' not registered")
        self.default_strategy = pattern
    
    def enable_auto_switching(self, enabled: bool = True):
        """Enable or disable automatic strategy switching."""
        self.auto_switching_enabled = enabled
    
    def set_pattern_detector(self, detector: MarketPatternDetector):
        """Set the market pattern detector."""
        self.pattern_detector = detector
    
    def reset_performance_tracking(self):
        """Reset all performance tracking data."""
        self.performance_history.clear()
        self.switch_history.clear()
        self.recent_patterns.clear()
        self.current_strategy = None
        self.current_pattern = None