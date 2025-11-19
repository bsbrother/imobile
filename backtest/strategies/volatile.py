"""
Volatile market strategy implementation.
"""

from typing import Dict, Any
import pandas as pd
import numpy as np
from ..core.strategy import ASharesStrategy
from ..analysis.indicators import TechnicalIndicators


class VolatileMarketStrategy(ASharesStrategy):
    """Strategy for volatile market conditions with quick entry/exit and tight stops."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize volatile market strategy."""
        # Default configuration for volatile market - quick reactions
        default_config = {
            'position_sizing': {
                'max_position_pct': 0.08,  # Moderate position sizes
                'max_positions': 8,  # Moderate number of positions
                'min_lot_size': 100,
                'allow_averaging_down': False,  # No averaging in volatile markets
                'averaging_down_threshold': 0.08,
                'max_averaging_positions': 1
            },
            'risk_management': {
                'stop_loss_pct': 0.06,  # Tight stops for volatility
                'profit_target_pct': 0.12,  # Quick profit taking
                'max_holding_days': 10,  # Very short holding period
                'volatility_adjustment': True
            },
            'buy_signals': {
                'rsi_oversold': 25,  # Oversold but not extreme
                'rsi_overbought': 70,
                'bb_lower_touch': True,
                'macd_bullish_cross': True,
                'volume_surge_threshold': 1.8,  # High volume required
                'momentum_threshold': 0.025,  # Strong momentum required
                'volatility_breakout': True,
                'mean_reversion': True
            },
            'sell_signals': {
                'rsi_overbought_exit': 70,  # Quick exit on overbought
                'bb_upper_touch': True,  # Exit on BB upper touch
                'macd_bearish_cross': True,
                'momentum_reversal': -0.025,  # Quick reversal exit
                'volatility_spike_exit': True,
                'quick_profit_pct': 0.06  # Take quick profits
            }
        }
        
        # Merge with provided config
        merged_config = self._merge_configs(default_config, config)
        super().__init__(merged_config)
        
    def _merge_configs(self, default: Dict, provided: Dict) -> Dict:
        """Merge provided config with defaults."""
        result = default.copy()
        for key, value in provided.items():
            if key in result and isinstance(result[key], dict) and isinstance(value, dict):
                result[key].update(value)
            else:
                result[key] = value
        return result
    
    def should_buy(self, symbol: str, data: pd.DataFrame) -> bool:
        """
        Determine if should buy a stock based on volatile market conditions.
        
        Uses quick reaction approach for volatile markets:
        - Mean reversion on oversold bounces
        - Volatility breakouts with volume
        - Quick momentum signals
        - Bollinger Band reversals
        """
        if len(data) < 50:  # Need sufficient data for indicators
            return False
        
        try:
            # Get current values
            current_idx = len(data) - 1
            close_prices = data['Close'] if 'Close' in data.columns else data.iloc[:, 3]
            high_prices = data['High'] if 'High' in data.columns else data.iloc[:, 1]
            low_prices = data['Low'] if 'Low' in data.columns else data.iloc[:, 2]
            volume = data['Volume'] if 'Volume' in data.columns else None
            
            # Calculate indicators
            rsi = TechnicalIndicators.rsi(close_prices, 14)
            bb_upper, bb_middle, bb_lower = TechnicalIndicators.bollinger_bands(close_prices, 20, 2.0)
            macd_line, macd_signal, macd_hist = TechnicalIndicators.macd(close_prices)
            volatility = TechnicalIndicators.volatility(close_prices, 10)  # Shorter period for volatile markets
            ma_10 = TechnicalIndicators.moving_average(close_prices, 10)  # Shorter MA
            
            # Get current and previous values
            current_rsi = rsi.iloc[current_idx] if not pd.isna(rsi.iloc[current_idx]) else 50
            current_price = close_prices.iloc[current_idx]
            current_bb_lower = bb_lower.iloc[current_idx] if not pd.isna(bb_lower.iloc[current_idx]) else current_price
            current_bb_middle = bb_middle.iloc[current_idx] if not pd.isna(bb_middle.iloc[current_idx]) else current_price
            current_bb_upper = bb_upper.iloc[current_idx] if not pd.isna(bb_upper.iloc[current_idx]) else current_price
            current_macd = macd_line.iloc[current_idx] if not pd.isna(macd_line.iloc[current_idx]) else 0
            current_macd_signal = macd_signal.iloc[current_idx] if not pd.isna(macd_signal.iloc[current_idx]) else 0
            prev_macd = macd_line.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_line.iloc[current_idx-1]) else 0
            prev_macd_signal = macd_signal.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_signal.iloc[current_idx-1]) else 0
            current_volatility = volatility.iloc[current_idx] if not pd.isna(volatility.iloc[current_idx]) else 0.2
            current_ma10 = ma_10.iloc[current_idx] if not pd.isna(ma_10.iloc[current_idx]) else current_price
            
            buy_config = self.strategy_config.config.get('buy_signals', {})
            
            # Buy signal conditions - quick reactions for volatile market
            buy_signals = []
            
            # 1. RSI oversold but not extreme (mean reversion)
            rsi_oversold = buy_config.get('rsi_oversold', 25)
            if current_rsi < rsi_oversold and current_rsi > 15:  # Oversold but not panic
                buy_signals.append('rsi_oversold_bounce')
            
            # 2. Bollinger Band mean reversion
            if buy_config.get('mean_reversion', True):
                bb_width = (current_bb_upper - current_bb_lower) / current_bb_middle
                if (current_price <= current_bb_lower * 1.02 and  # Near lower band
                    bb_width > 0.1):  # Bands are wide (volatile)
                    buy_signals.append('bb_mean_reversion')
            
            # 3. Volatility breakout with volume
            if buy_config.get('volatility_breakout', True) and len(volatility) > 5:
                avg_volatility = volatility.rolling(5).mean().iloc[current_idx]
                if (current_volatility > avg_volatility * 1.5 and  # High volatility
                    current_price > current_ma10):  # Price above short MA
                    buy_signals.append('volatility_breakout')
            
            # 4. MACD bullish crossover in volatile environment
            if buy_config.get('macd_bullish_cross', True):
                if (current_macd > current_macd_signal and 
                    prev_macd <= prev_macd_signal):
                    buy_signals.append('macd_bullish_cross')
            
            # 5. Strong momentum with volume confirmation
            if len(close_prices) > 5:
                momentum = (current_price - close_prices.iloc[current_idx-3]) / close_prices.iloc[current_idx-3]
                momentum_threshold = buy_config.get('momentum_threshold', 0.025)
                
                if momentum > momentum_threshold:
                    buy_signals.append('strong_momentum')
                    
                    # Volume confirmation for momentum
                    if volume is not None and len(volume) > 10:
                        avg_volume = volume.rolling(10).mean().iloc[current_idx]
                        current_volume = volume.iloc[current_idx]
                        volume_threshold = buy_config.get('volume_surge_threshold', 1.8)
                        if current_volume > avg_volume * volume_threshold:
                            buy_signals.append('volume_momentum')
            
            # 6. Quick reversal from oversold (volatile market specialty)
            if len(close_prices) > 3:
                recent_low = low_prices.iloc[current_idx-3:current_idx+1].min()
                if (current_price > recent_low * 1.03 and  # 3% bounce from recent low
                    current_rsi < 35):  # Still oversold
                    buy_signals.append('quick_reversal')
            
            # 7. Intraday strength (price near high of range)
            if len(high_prices) > 1:
                daily_range = high_prices.iloc[current_idx] - low_prices.iloc[current_idx]
                if daily_range > 0:
                    price_position = (current_price - low_prices.iloc[current_idx]) / daily_range
                    if price_position > 0.7:  # Price in upper 30% of daily range
                        buy_signals.append('intraday_strength')
            
            # Volatile market requires quick confirmation
            # Need at least 2 signals with either momentum or mean reversion
            has_momentum = any(signal in buy_signals for signal in ['strong_momentum', 'volatility_breakout', 'volume_momentum'])
            has_mean_reversion = any(signal in buy_signals for signal in ['rsi_oversold_bounce', 'bb_mean_reversion', 'quick_reversal'])
            
            # Volatile market - quick entry (need momentum or mean reversion)
            return len(buy_signals) >= 1 and (has_momentum or has_mean_reversion)
            
        except Exception as e:
            # Return False if any calculation fails
            return False
    
    def should_sell(self, symbol: str, data: pd.DataFrame, position: Any) -> bool:
        """
        Determine if should sell a position based on volatile market conditions.
        
        Uses very quick exit strategy for volatile markets:
        - Quick profit taking
        - Tight stops
        - Volatility spike exits
        - Mean reversion exits
        """
        if len(data) < 50:
            return False
        
        try:
            # Get current values
            current_idx = len(data) - 1
            close_prices = data['Close'] if 'Close' in data.columns else data.iloc[:, 3]
            high_prices = data['High'] if 'High' in data.columns else data.iloc[:, 1]
            low_prices = data['Low'] if 'Low' in data.columns else data.iloc[:, 2]
            
            # Calculate indicators
            rsi = TechnicalIndicators.rsi(close_prices, 14)
            bb_upper, bb_middle, bb_lower = TechnicalIndicators.bollinger_bands(close_prices, 20, 2.0)
            macd_line, macd_signal, macd_hist = TechnicalIndicators.macd(close_prices)
            volatility = TechnicalIndicators.volatility(close_prices, 10)
            ma_10 = TechnicalIndicators.moving_average(close_prices, 10)
            
            # Get current and previous values
            current_rsi = rsi.iloc[current_idx] if not pd.isna(rsi.iloc[current_idx]) else 50
            current_price = close_prices.iloc[current_idx]
            current_bb_upper = bb_upper.iloc[current_idx] if not pd.isna(bb_upper.iloc[current_idx]) else current_price
            current_bb_middle = bb_middle.iloc[current_idx] if not pd.isna(bb_middle.iloc[current_idx]) else current_price
            current_macd = macd_line.iloc[current_idx] if not pd.isna(macd_line.iloc[current_idx]) else 0
            current_macd_signal = macd_signal.iloc[current_idx] if not pd.isna(macd_signal.iloc[current_idx]) else 0
            prev_macd = macd_line.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_line.iloc[current_idx-1]) else 0
            prev_macd_signal = macd_signal.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_signal.iloc[current_idx-1]) else 0
            current_volatility = volatility.iloc[current_idx] if not pd.isna(volatility.iloc[current_idx]) else 0.2
            current_ma10 = ma_10.iloc[current_idx] if not pd.isna(ma_10.iloc[current_idx]) else current_price
            
            sell_config = self.strategy_config.config.get('sell_signals', {})
            risk_config = self.strategy_config.config.get('risk_management', {})
            
            # Sell signal conditions - very quick for volatile market
            sell_signals = []
            
            # 1. Quick profit taking (lower threshold in volatile market)
            if position and 'buy_price' in position:
                buy_price = position['buy_price']
                profit_pct = (current_price - buy_price) / buy_price
                quick_profit_pct = sell_config.get('quick_profit_pct', 0.06)
                if profit_pct >= quick_profit_pct:
                    sell_signals.append('quick_profit')
            
            # 2. RSI overbought (quick exit)
            rsi_overbought = sell_config.get('rsi_overbought_exit', 70)
            if current_rsi > rsi_overbought:
                sell_signals.append('rsi_overbought')
            
            # 3. Bollinger Band upper touch (mean reversion exit)
            if sell_config.get('bb_upper_touch', True):
                if current_price >= current_bb_upper * 0.98:  # Near upper band
                    sell_signals.append('bb_upper_exit')
            
            # 4. MACD bearish crossover
            if sell_config.get('macd_bearish_cross', True):
                if (current_macd < current_macd_signal and 
                    prev_macd >= prev_macd_signal):
                    sell_signals.append('macd_bearish_cross')
            
            # 5. Quick momentum reversal
            if len(close_prices) > 3:
                momentum = (current_price - close_prices.iloc[current_idx-2]) / close_prices.iloc[current_idx-2]
                momentum_threshold = sell_config.get('momentum_reversal', -0.025)
                if momentum < momentum_threshold:
                    sell_signals.append('momentum_reversal')
            
            # 6. Volatility spike exit (protect against sudden moves)
            if sell_config.get('volatility_spike_exit', True) and len(volatility) > 5:
                avg_volatility = volatility.rolling(5).mean().iloc[current_idx]
                if current_volatility > avg_volatility * 2.0:  # Extreme volatility spike
                    sell_signals.append('volatility_spike')
            
            # 7. Price below short MA (trend change)
            if current_price < current_ma10:
                sell_signals.append('trend_change')
            
            # 8. Intraday weakness (price near low of range)
            if len(high_prices) > 1:
                daily_range = high_prices.iloc[current_idx] - low_prices.iloc[current_idx]
                if daily_range > 0:
                    price_position = (current_price - low_prices.iloc[current_idx]) / daily_range
                    if price_position < 0.3:  # Price in lower 30% of daily range
                        sell_signals.append('intraday_weakness')
            
            # 9. Maximum holding period (very short for volatile markets)
            if position and 'buy_date' in position:
                holding_days = (data.index[current_idx] - position['buy_date']).days
                max_holding_days = risk_config.get('max_holding_days', 10)
                if holding_days > max_holding_days:
                    sell_signals.append('max_holding_period')
            
            # 10. Profit protection after quick gains
            if position and 'buy_price' in position:
                buy_price = position['buy_price']
                profit_pct = (current_price - buy_price) / buy_price
                if profit_pct > 0.08:  # After 8% gain, protect profits
                    if current_rsi > 65 or current_price < current_ma10:
                        sell_signals.append('profit_protection')
            
            # Volatile market: exit quickly on any significant signal
            # Need only 1 signal, prioritize profit taking and risk management
            quick_exit_signals = ['quick_profit', 'volatility_spike', 'momentum_reversal', 'profit_protection']
            has_quick_exit = any(signal in sell_signals for signal in quick_exit_signals)
            
            return len(sell_signals) >= 1 or has_quick_exit
            
        except Exception as e:
            # Return False if any calculation fails
            return False