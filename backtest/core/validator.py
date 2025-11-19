"""
Trade validation for China A-shares market rules.
"""

from datetime import datetime, timedelta
from typing import Dict, Any, Optional
from ..utils.exceptions import TradeValidationError


class TradeValidator:
    """Validates trades against China A-shares market rules."""
    
    def __init__(self):
        self.position_buy_dates = {}  # Track when positions were bought
        
    def validate_trade(self, trade: Dict[str, Any], current_date: datetime) -> bool:
        """
        Validate a trade against A-shares rules.
        
        Args:
            trade: Dictionary containing trade details
            current_date: Current trading date
            
        Returns:
            bool: True if trade is valid
            
        Raises:
            TradeValidationError: If trade violates A-shares rules
        """
        action = trade.get('action', '').upper()
        symbol = trade.get('symbol', '')
        quantity = trade.get('quantity', 0)
        
        # Validate no short selling
        if action == 'SELL' and quantity < 0:
            raise TradeValidationError(
                f"Short selling not allowed in A-shares market. "
                f"Attempted to sell {quantity} shares of {symbol}"
            )
        
        # Validate positive quantities for buys
        if action == 'BUY' and quantity <= 0:
            raise TradeValidationError(
                f"Buy quantity must be positive. "
                f"Attempted to buy {quantity} shares of {symbol}"
            )
        
        # Validate T+1 rule for sells
        if action == 'SELL':
            if not self.can_sell_today(symbol, current_date):
                buy_date = self.position_buy_dates.get(symbol)
                raise TradeValidationError(
                    f"T+1 rule violation: Cannot sell {symbol} on {current_date.date()}. "
                    f"Position was bought on {buy_date.date() if buy_date else 'unknown date'}"
                )
        
        return True
    
    def can_sell_today(self, symbol: str, current_date: datetime) -> bool:
        """
        Check if a position can be sold today based on T+1 rule.
        
        Args:
            symbol: Stock symbol
            current_date: Current trading date
            
        Returns:
            bool: True if position can be sold today
        """
        if symbol not in self.position_buy_dates:
            return True  # No position or can sell existing position
        
        buy_date = self.position_buy_dates[symbol]
        return current_date.date() > buy_date.date()
    
    def record_buy(self, symbol: str, buy_date: datetime):
        """
        Record when a position was bought for T+1 tracking.
        
        Args:
            symbol: Stock symbol
            buy_date: Date when position was bought
        """
        self.position_buy_dates[symbol] = buy_date
    
    def record_sell(self, symbol: str):
        """
        Record when a position was sold (remove from tracking).
        
        Args:
            symbol: Stock symbol
        """
        if symbol in self.position_buy_dates:
            del self.position_buy_dates[symbol]
    
    def get_unsellable_positions(self, current_date: datetime) -> Dict[str, datetime]:
        """
        Get positions that cannot be sold today due to T+1 rule.
        
        Args:
            current_date: Current trading date
            
        Returns:
            Dict mapping symbol to buy date for unsellable positions
        """
        unsellable = {}
        for symbol, buy_date in self.position_buy_dates.items():
            if not self.can_sell_today(symbol, current_date):
                unsellable[symbol] = buy_date
        return unsellable