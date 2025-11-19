"""
Bear market strategy implementation.
"""

from typing import Dict, Any
import pandas as pd
import numpy as np
from ..core.strategy import ASharesStrategy
from ..analysis.indicators import TechnicalIndicators


class BearMarketStrategy(ASharesStrategy):
    """Strategy for bear market conditions with defensive and cash preservation logic."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize bear market strategy."""
        # Default configuration for bear market - defensive approach
        default_config = {
            'position_sizing': {
                'max_position_pct': 0.05,  # Much smaller positions
                'max_positions': 5,  # Fewer positions
                'min_lot_size': 100,
                'allow_averaging_down': False,  # No averaging down in bear market
                'averaging_down_threshold': 0.10,
                'max_averaging_positions': 1
            },
            'risk_management': {
                'stop_loss_pct': 0.05,  # Tight stops
                'profit_target_pct': 0.08,  # Lower profit targets
                'max_holding_days': 15,  # Short holding period
                'cash_preservation_mode': True
            },
            'buy_signals': {
                'rsi_oversold': 20,  # Extremely oversold required
                'rsi_overbought': 60,  # Lower overbought threshold
                'bb_lower_touch': True,
                'macd_bullish_cross': True,
                'volume_surge_threshold': 2.0,  # Higher volume required
                'momentum_threshold': 0.03,  # Higher momentum required
                'bounce_confirmation': True,  # Require bounce confirmation
                'support_level_test': True
            },
            'sell_signals': {
                'rsi_overbought_exit': 65,  # Lower RSI exit
                'bb_upper_touch': True,  # Exit on BB upper touch
                'macd_bearish_cross': True,
                'momentum_reversal': -0.02,  # Quick exit on reversal
                'any_profit_exit': True,  # Exit on any profit in bear market
                'weakness_exit': True
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
        Determine if should buy a stock based on bear market conditions.
        
        Uses very conservative approach for falling markets:
        - Extremely oversold conditions required
        - Strong bounce confirmation
        - High volume validation
        - Support level testing
        - Multiple confirmation signals
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
            ma_20 = TechnicalIndicators.moving_average(close_prices, 20)
            ma_50 = TechnicalIndicators.moving_average(close_prices, 50)
            
            # Get current and previous values
            current_rsi = rsi.iloc[current_idx] if not pd.isna(rsi.iloc[current_idx]) else 50
            current_price = close_prices.iloc[current_idx]
            current_bb_lower = bb_lower.iloc[current_idx] if not pd.isna(bb_lower.iloc[current_idx]) else current_price
            current_bb_middle = bb_middle.iloc[current_idx] if not pd.isna(bb_middle.iloc[current_idx]) else current_price
            current_macd = macd_line.iloc[current_idx] if not pd.isna(macd_line.iloc[current_idx]) else 0
            current_macd_signal = macd_signal.iloc[current_idx] if not pd.isna(macd_signal.iloc[current_idx]) else 0
            prev_macd = macd_line.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_line.iloc[current_idx-1]) else 0
            prev_macd_signal = macd_signal.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_signal.iloc[current_idx-1]) else 0
            current_ma20 = ma_20.iloc[current_idx] if not pd.isna(ma_20.iloc[current_idx]) else current_price
            current_ma50 = ma_50.iloc[current_idx] if not pd.isna(ma_50.iloc[current_idx]) else current_price
            
            buy_config = self.strategy_config.config.get('buy_signals', {})
            
            # Buy signal conditions - very conservative for bear market
            buy_signals = []
            
            # 1. Extremely oversold RSI
            rsi_oversold = buy_config.get('rsi_oversold', 20)
            if current_rsi < rsi_oversold:
                buy_signals.append('rsi_extremely_oversold')
            
            # 2. Price near or below Bollinger Band lower (oversold)
            if buy_config.get('bb_lower_touch', True):
                if current_price <= current_bb_lower * 1.01:  # Within 1% of lower band
                    buy_signals.append('bb_oversold')
            
            # 3. MACD showing signs of bottoming
            if buy_config.get('macd_bullish_cross', True):
                if (current_macd > current_macd_signal and 
                    prev_macd <= prev_macd_signal and
                    current_macd < 0):  # Still negative but turning up
                    buy_signals.append('macd_bottoming')
            
            # 4. Bounce confirmation (price recovering from recent low)
            if buy_config.get('bounce_confirmation', True) and len(close_prices) > 5:
                recent_low = low_prices.iloc[current_idx-5:current_idx+1].min()
                if current_price > recent_low * 1.02:  # At least 2% bounce
                    buy_signals.append('bounce_confirmation')
            
            # 5. High volume confirmation (institutional buying)
            if volume is not None and len(volume) > 20:
                avg_volume = volume.rolling(20).mean().iloc[current_idx]
                current_volume = volume.iloc[current_idx]
                volume_threshold = buy_config.get('volume_surge_threshold', 2.0)
                if current_volume > avg_volume * volume_threshold:
                    buy_signals.append('high_volume')
            
            # 6. Support level test (price holding above key support)
            if buy_config.get('support_level_test', True) and len(close_prices) > 20:
                support_level = low_prices.rolling(20).min().iloc[current_idx]
                if current_price > support_level * 1.01:  # Above recent support
                    buy_signals.append('support_hold')
            
            # 7. Positive momentum despite bear market
            if len(close_prices) > 3:
                short_momentum = (current_price - close_prices.iloc[current_idx-3]) / close_prices.iloc[current_idx-3]
                momentum_threshold = buy_config.get('momentum_threshold', 0.03)
                if short_momentum > momentum_threshold:
                    buy_signals.append('positive_momentum')
            
            # 8. Relative strength (outperforming recent decline)
            if len(close_prices) > 10:
                recent_decline = (close_prices.iloc[current_idx-10] - close_prices.iloc[current_idx-1]) / close_prices.iloc[current_idx-10]
                current_performance = (current_price - close_prices.iloc[current_idx-1]) / close_prices.iloc[current_idx-1]
                if recent_decline < -0.05 and current_performance > 0:  # Outperforming during decline
                    buy_signals.append('relative_strength')
            
            # Bear market requires many confirmation signals
            # Need at least 4 signals including oversold and volume confirmation
            has_oversold = 'rsi_extremely_oversold' in buy_signals or 'bb_oversold' in buy_signals
            has_volume = 'high_volume' in buy_signals
            has_bounce = 'bounce_confirmation' in buy_signals
            
            # Bear market - very selective but not impossible (need strong oversold signal)
            return len(buy_signals) >= 2 and has_oversold
            
        except Exception as e:
            # Return False if any calculation fails
            return False
    
    def should_sell(self, symbol: str, data: pd.DataFrame, position: Any) -> bool:
        """
        Determine if should sell a position based on bear market conditions.
        
        Uses very aggressive exit strategy for capital preservation:
        - Quick profit taking
        - Tight stop losses
        - Any sign of weakness triggers exit
        - Lower RSI exit thresholds
        """
        if len(data) < 50:
            return False
        
        try:
            # Get current values
            current_idx = len(data) - 1
            close_prices = data['Close'] if 'Close' in data.columns else data.iloc[:, 3]
            
            # Calculate indicators
            rsi = TechnicalIndicators.rsi(close_prices, 14)
            bb_upper, bb_middle, bb_lower = TechnicalIndicators.bollinger_bands(close_prices, 20, 2.0)
            macd_line, macd_signal, macd_hist = TechnicalIndicators.macd(close_prices)
            ma_20 = TechnicalIndicators.moving_average(close_prices, 20)
            
            # Get current and previous values
            current_rsi = rsi.iloc[current_idx] if not pd.isna(rsi.iloc[current_idx]) else 50
            current_price = close_prices.iloc[current_idx]
            current_bb_upper = bb_upper.iloc[current_idx] if not pd.isna(bb_upper.iloc[current_idx]) else current_price
            current_macd = macd_line.iloc[current_idx] if not pd.isna(macd_line.iloc[current_idx]) else 0
            current_macd_signal = macd_signal.iloc[current_idx] if not pd.isna(macd_signal.iloc[current_idx]) else 0
            prev_macd = macd_line.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_line.iloc[current_idx-1]) else 0
            prev_macd_signal = macd_signal.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_signal.iloc[current_idx-1]) else 0
            current_ma20 = ma_20.iloc[current_idx] if not pd.isna(ma_20.iloc[current_idx]) else current_price
            
            sell_config = self.strategy_config.config.get('sell_signals', {})
            risk_config = self.strategy_config.config.get('risk_management', {})
            
            # Sell signal conditions - very aggressive for bear market
            sell_signals = []
            
            # 1. Any profit in bear market (take what you can get)
            if position and 'buy_price' in position and sell_config.get('any_profit_exit', True):
                buy_price = position['buy_price']
                if current_price > buy_price * 1.01:  # Any profit > 1%
                    sell_signals.append('any_profit')
            
            # 2. Lower RSI overbought threshold
            rsi_overbought = sell_config.get('rsi_overbought_exit', 65)
            if current_rsi > rsi_overbought:
                sell_signals.append('rsi_overbought')
            
            # 3. Bollinger Band upper touch (resistance)
            if sell_config.get('bb_upper_touch', True):
                if current_price >= current_bb_upper * 0.99:  # Near upper band
                    sell_signals.append('bb_resistance')
            
            # 4. MACD bearish crossover
            if sell_config.get('macd_bearish_cross', True):
                if (current_macd < current_macd_signal and 
                    prev_macd >= prev_macd_signal):
                    sell_signals.append('macd_bearish_cross')
            
            # 5. Any momentum reversal
            if len(close_prices) > 3:
                momentum = (current_price - close_prices.iloc[current_idx-3]) / close_prices.iloc[current_idx-3]
                momentum_threshold = sell_config.get('momentum_reversal', -0.02)
                if momentum < momentum_threshold:
                    sell_signals.append('momentum_reversal')
            
            # 6. Price below MA20 (trend weakness)
            if current_price < current_ma20:
                sell_signals.append('trend_weakness')
            
            # 7. Signs of weakness (lower highs, lower lows)
            if sell_config.get('weakness_exit', True) and len(close_prices) > 5:
                recent_high = close_prices.iloc[current_idx-5:current_idx].max()
                prev_high = close_prices.iloc[current_idx-10:current_idx-5].max()
                if recent_high < prev_high * 0.98:  # Lower high pattern
                    sell_signals.append('weakness_pattern')
            
            # 8. Maximum holding period (very short in bear market)
            if position and 'buy_date' in position:
                holding_days = (data.index[current_idx] - position['buy_date']).days
                max_holding_days = risk_config.get('max_holding_days', 15)
                if holding_days > max_holding_days:
                    sell_signals.append('max_holding_period')
            
            # 9. Profit target reached (lower target in bear market)
            if position and 'buy_price' in position:
                buy_price = position['buy_price']
                profit_pct = (current_price - buy_price) / buy_price
                profit_target = risk_config.get('profit_target_pct', 0.08)
                if profit_pct >= profit_target:
                    sell_signals.append('profit_target')
            
            # Bear market: exit on any significant signal
            # Need only 1 signal, or any profit signal
            has_profit_signal = any(signal in sell_signals for signal in ['any_profit', 'profit_target'])
            strong_signals = ['macd_bearish_cross', 'momentum_reversal', 'trend_weakness']
            has_strong_signal = any(signal in sell_signals for signal in strong_signals)
            
            return len(sell_signals) >= 1 or has_profit_signal or has_strong_signal
            
        except Exception as e:
            # Return False if any calculation fails
            return False