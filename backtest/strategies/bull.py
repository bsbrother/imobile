"""
Bull market strategy implementation.
"""

from typing import Dict, Any
import pandas as pd
import numpy as np
from ..core.strategy import ASharesStrategy
from ..analysis.indicators import TechnicalIndicators


class BullMarketStrategy(ASharesStrategy):
    """Strategy for bull market conditions with aggressive growth-focused rules."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize bull market strategy."""
        # Default configuration for bull market - more aggressive
        default_config = {
            'position_sizing': {
                'max_position_pct': 0.15,  # Larger positions in bull market
                'max_positions': 12,  # More positions
                'min_lot_size': 100,
                'allow_averaging_down': True,
                'averaging_down_threshold': 0.03,  # More aggressive averaging
                'max_averaging_positions': 3
            },
            'risk_management': {
                'stop_loss_pct': 0.12,  # Wider stops in bull market
                'profit_target_pct': 0.25,  # Higher profit targets
                'max_holding_days': 45  # Longer holding period
            },
            'buy_signals': {
                'rsi_oversold': 35,  # Less oversold required
                'rsi_overbought': 75,
                'bb_lower_touch': True,
                'macd_bullish_cross': True,
                'volume_surge_threshold': 1.3,  # Lower volume threshold
                'momentum_threshold': 0.01,  # Lower momentum threshold
                'trend_strength_min': 0.02  # Require strong uptrend
            },
            'sell_signals': {
                'rsi_overbought_exit': 80,  # Higher RSI exit
                'bb_upper_touch': False,  # Don't exit on BB upper touch
                'macd_bearish_cross': True,
                'momentum_reversal': -0.05,  # Deeper reversal required
                'profit_protection_pct': 0.15  # Protect profits after 15% gain
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
        Determine if should buy a stock based on bull market conditions.
        
        Uses aggressive approach optimized for rising markets:
        - Lower RSI thresholds (trend following)
        - Strong momentum signals
        - Volume confirmation
        - Trend strength validation
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
            ema_12 = TechnicalIndicators.exponential_moving_average(close_prices, 12)
            
            # Get current and previous values
            current_rsi = rsi.iloc[current_idx] if not pd.isna(rsi.iloc[current_idx]) else 50
            current_price = close_prices.iloc[current_idx]
            current_bb_lower = bb_lower.iloc[current_idx] if not pd.isna(bb_lower.iloc[current_idx]) else current_price
            current_macd = macd_line.iloc[current_idx] if not pd.isna(macd_line.iloc[current_idx]) else 0
            current_macd_signal = macd_signal.iloc[current_idx] if not pd.isna(macd_signal.iloc[current_idx]) else 0
            prev_macd = macd_line.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_line.iloc[current_idx-1]) else 0
            prev_macd_signal = macd_signal.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_signal.iloc[current_idx-1]) else 0
            current_ma20 = ma_20.iloc[current_idx] if not pd.isna(ma_20.iloc[current_idx]) else current_price
            current_ma50 = ma_50.iloc[current_idx] if not pd.isna(ma_50.iloc[current_idx]) else current_price
            current_ema12 = ema_12.iloc[current_idx] if not pd.isna(ema_12.iloc[current_idx]) else current_price
            
            buy_config = self.strategy_config.config.get('buy_signals', {})
            
            # Buy signal conditions - more aggressive for bull market
            buy_signals = []
            
            # 1. RSI not extremely overbought (trend following in bull market)
            rsi_oversold = buy_config.get('rsi_oversold', 35)
            if current_rsi > rsi_oversold and current_rsi < 75:  # Not too overbought
                buy_signals.append('rsi_favorable')
            
            # 2. Strong uptrend confirmation (price above both MAs)
            if current_price > current_ma20 and current_ma20 > current_ma50:
                buy_signals.append('strong_uptrend')
            
            # 3. MACD bullish momentum
            if buy_config.get('macd_bullish_cross', True):
                if current_macd > current_macd_signal:
                    buy_signals.append('macd_bullish')
                    
                # Extra signal for fresh bullish crossover
                if (current_macd > current_macd_signal and 
                    prev_macd <= prev_macd_signal):
                    buy_signals.append('macd_bullish_cross')
            
            # 4. Price momentum (bull markets favor momentum)
            if len(close_prices) > 10:
                short_momentum = (current_price - close_prices.iloc[current_idx-5]) / close_prices.iloc[current_idx-5]
                medium_momentum = (current_price - close_prices.iloc[current_idx-10]) / close_prices.iloc[current_idx-10]
                momentum_threshold = buy_config.get('momentum_threshold', 0.01)
                
                if short_momentum > momentum_threshold and medium_momentum > 0:
                    buy_signals.append('positive_momentum')
            
            # 5. Volume confirmation
            if volume is not None and len(volume) > 20:
                avg_volume = volume.rolling(20).mean().iloc[current_idx]
                current_volume = volume.iloc[current_idx]
                volume_threshold = buy_config.get('volume_surge_threshold', 1.3)
                if current_volume > avg_volume * volume_threshold:
                    buy_signals.append('volume_surge')
            
            # 6. Price above EMA12 (short-term trend)
            if current_price > current_ema12:
                buy_signals.append('short_term_trend')
            
            # 7. Trend strength validation
            if len(close_prices) > 20:
                trend_strength = (current_ma20 - ma_20.iloc[current_idx-10]) / ma_20.iloc[current_idx-10]
                trend_strength_min = buy_config.get('trend_strength_min', 0.02)
                if trend_strength > trend_strength_min:
                    buy_signals.append('trend_strength')
            
            # Bull market requires fewer signals but stronger momentum
            # Require at least 3 signals including trend confirmation
            has_trend = 'strong_uptrend' in buy_signals or 'trend_strength' in buy_signals
            # Bull market - more aggressive entry (need fewer signals)
            return len(buy_signals) >= 1 and has_trend
            
        except Exception as e:
            # Return False if any calculation fails
            return False
    
    def should_sell(self, symbol: str, data: pd.DataFrame, position: Any) -> bool:
        """
        Determine if should sell a position based on bull market conditions.
        
        Uses less aggressive exit strategy to ride trends longer:
        - Higher RSI exit thresholds
        - Profit protection after significant gains
        - Trend reversal detection
        - Momentum breakdown signals
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
            ma_50 = TechnicalIndicators.moving_average(close_prices, 50)
            
            # Get current and previous values
            current_rsi = rsi.iloc[current_idx] if not pd.isna(rsi.iloc[current_idx]) else 50
            current_price = close_prices.iloc[current_idx]
            current_macd = macd_line.iloc[current_idx] if not pd.isna(macd_line.iloc[current_idx]) else 0
            current_macd_signal = macd_signal.iloc[current_idx] if not pd.isna(macd_signal.iloc[current_idx]) else 0
            prev_macd = macd_line.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_line.iloc[current_idx-1]) else 0
            prev_macd_signal = macd_signal.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_signal.iloc[current_idx-1]) else 0
            current_ma20 = ma_20.iloc[current_idx] if not pd.isna(ma_20.iloc[current_idx]) else current_price
            current_ma50 = ma_50.iloc[current_idx] if not pd.isna(ma_50.iloc[current_idx]) else current_price
            
            sell_config = self.strategy_config.config.get('sell_signals', {})
            risk_config = self.strategy_config.config.get('risk_management', {})
            
            # Sell signal conditions - less aggressive for bull market
            sell_signals = []
            
            # 1. Extreme RSI overbought (higher threshold than normal market)
            rsi_overbought = sell_config.get('rsi_overbought_exit', 80)
            if current_rsi > rsi_overbought:
                sell_signals.append('rsi_extreme_overbought')
            
            # 2. MACD bearish crossover (trend reversal)
            if sell_config.get('macd_bearish_cross', True):
                if (current_macd < current_macd_signal and 
                    prev_macd >= prev_macd_signal):
                    sell_signals.append('macd_bearish_cross')
            
            # 3. Trend breakdown (price below both MAs)
            if current_price < current_ma20 and current_ma20 < current_ma50:
                sell_signals.append('trend_breakdown')
            
            # 4. Strong momentum reversal
            if len(close_prices) > 10:
                momentum = (current_price - close_prices.iloc[current_idx-5]) / close_prices.iloc[current_idx-5]
                momentum_threshold = sell_config.get('momentum_reversal', -0.05)
                if momentum < momentum_threshold:
                    sell_signals.append('momentum_reversal')
            
            # 5. Profit protection (protect gains after significant profit)
            if position and 'buy_price' in position:
                buy_price = position['buy_price']
                profit_pct = (current_price - buy_price) / buy_price
                profit_protection_pct = sell_config.get('profit_protection_pct', 0.15)
                
                if profit_pct > profit_protection_pct:
                    # Check for signs of weakness after significant gains
                    if current_rsi > 70 and current_price < current_ma20:
                        sell_signals.append('profit_protection')
            
            # 6. Maximum holding period (longer in bull market)
            if position and 'buy_date' in position:
                holding_days = (data.index[current_idx] - position['buy_date']).days
                max_holding_days = risk_config.get('max_holding_days', 45)
                if holding_days > max_holding_days:
                    sell_signals.append('max_holding_period')
            
            # Bull market requires stronger sell signals
            # Need at least 2 signals or 1 strong signal (trend breakdown)
            strong_signals = ['trend_breakdown', 'macd_bearish_cross', 'momentum_reversal']
            has_strong_signal = any(signal in sell_signals for signal in strong_signals)
            
            return len(sell_signals) >= 2 or has_strong_signal
            
        except Exception as e:
            # Return False if any calculation fails
            return False