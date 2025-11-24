"""
Trailing stop loss calculation for protecting profits.
"""
from typing import Tuple
from loguru import logger
import pandas as pd


def calculate_trailing_stop(
    entry_price: float,
    current_price: float,
    initial_stop_loss: float,
    trailing_enabled: bool = True
) -> Tuple[float, str]:
    """
    Calculate trailing stop loss that locks in profits as price rises.
    
    Args:
        entry_price: Original buy price
        current_price: Current market price
        initial_stop_loss: Initial stop loss price
        trailing_enabled: Whether to use trailing stop
    
    Returns:
        Tuple of (new_stop_loss, reason)
    """
    if not trailing_enabled:
        return initial_stop_loss, 'initial_stop'
    
    profit_pct = (current_price - entry_price) / entry_price * 100
    
    # Aggressive trailing for large profits
    if profit_pct > 20:
        # Lock in 15% profit
        new_stop = entry_price * 1.15
        reason = 'trailing_lock_15pct'
    
    elif profit_pct > 15:
        # Lock in 10% profit
        new_stop = entry_price * 1.10
        reason = 'trailing_lock_10pct'
    
    elif profit_pct > 10:
        # Lock in 5% profit
        new_stop = entry_price * 1.05
        reason = 'trailing_lock_5pct'

    elif profit_pct > 8:
        # Lock in 3% profit
        new_stop = entry_price * 1.03
        reason = 'trailing_lock_3pct'
    
    elif profit_pct > 5:
        # Lock in 1% profit
        new_stop = entry_price * 1.01
        reason = 'trailing_lock_1pct'

    elif profit_pct > 3:
        # Move to break-even
        new_stop = entry_price * 1.00
        reason = 'trailing_breakeven'
    
    else:
        # Keep initial stop loss
        new_stop = initial_stop_loss
        reason = 'initial_stop'
    
    # Never lower the stop loss
    new_stop = max(new_stop, initial_stop_loss)
    
    logger.debug(f"Trailing stop: profit={profit_pct:.2f}%, "
                f"stop={new_stop:.2f}, reason={reason}")
    
    return new_stop, reason


def calculate_atr_stops(
    df,
    atr_period: int = 14,
    tp_multiplier: float = 3.0,
    sl_multiplier: float = 2.0
) -> Tuple[float, float]:
    """
    Calculate ATR-based dynamic stops for volatility adjustment.
    
    Args:
        df: DataFrame with OHLC data
        atr_period: Period for ATR calculation
        tp_multiplier: Multiplier for take profit
        sl_multiplier: Multiplier for stop loss
    
    Returns:
        Tuple of (take_profit_price, stop_loss_price)
    """
    # Calculate True Range
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    close = df['close'].astype(float)
    
    tr1 = high - low
    tr2 = abs(high - close.shift())
    tr3 = abs(low - close.shift())
    
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.rolling(atr_period).mean().iloc[-1]
    
    current_price = close.iloc[-1]
    
    take_profit = current_price + (atr * tp_multiplier)
    stop_loss = current_price - (atr * sl_multiplier)
    
    logger.debug(f"ATR stops: ATR={atr:.2f}, TP={take_profit:.2f}, SL={stop_loss:.2f}")
    
    return take_profit, stop_loss
