"""
Normal market strategy implementation.
"""

from typing import Dict, Any
import pandas as pd
from ..core.strategy import ASharesStrategy
from ..analysis.indicators import TechnicalIndicators


class NormalMarketStrategy(ASharesStrategy):
    """Strategy for normal market conditions with balanced buy/sell signals."""
    
    def __init__(self, config: Dict[str, Any]):
        """Initialize normal market strategy."""
        # Default configuration for normal market
        default_config = {
            'position_sizing': {
                'max_position_pct': 0.1,
                'max_positions': 10,
                'min_lot_size': 100,
                'allow_averaging_down': True,
                'averaging_down_threshold': 0.05,
                'max_averaging_positions': 2
            },
            'risk_management': {
                'stop_loss_pct': 0.08,
                'profit_target_pct': 0.15,
                'max_holding_days': 30
            },
            'buy_signals': {
                'rsi_oversold': 30,
                'rsi_overbought': 70,
                'bb_lower_touch': True,
                'macd_bullish_cross': True,
                'volume_surge_threshold': 1.5,
                'momentum_threshold': 0.02
            },
            'sell_signals': {
                'rsi_overbought_exit': 75,
                'bb_upper_touch': True,
                'macd_bearish_cross': True,
                'momentum_reversal': -0.03
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
        Determine if should buy a stock based on normal market conditions.
        
        Uses balanced approach with multiple technical indicators:
        - RSI oversold conditions
        - Bollinger Band lower touch
        - MACD bullish crossover
        - Volume surge
        - Positive momentum
        """
        if len(data) < 50:  # Need sufficient data for indicators
            return False
        
        try:
            # Get current values
            current_idx = len(data) - 1
            close_prices = data['Close'] if 'Close' in data.columns else data.iloc[:, 3]  # Assume OHLC format
            high_prices = data['High'] if 'High' in data.columns else data.iloc[:, 1]
            low_prices = data['Low'] if 'Low' in data.columns else data.iloc[:, 2]
            volume = data['Volume'] if 'Volume' in data.columns else None
            
            # Calculate indicators
            rsi = TechnicalIndicators.rsi(close_prices, 14)
            bb_upper, bb_middle, bb_lower = TechnicalIndicators.bollinger_bands(close_prices, 20, 2.0)
            macd_line, macd_signal, macd_hist = TechnicalIndicators.macd(close_prices)
            ma_20 = TechnicalIndicators.moving_average(close_prices, 20)
            
            # Get current and previous values
            current_rsi = rsi.iloc[current_idx] if not pd.isna(rsi.iloc[current_idx]) else 50
            current_price = close_prices.iloc[current_idx]
            current_bb_lower = bb_lower.iloc[current_idx] if not pd.isna(bb_lower.iloc[current_idx]) else current_price
            current_macd = macd_line.iloc[current_idx] if not pd.isna(macd_line.iloc[current_idx]) else 0
            current_macd_signal = macd_signal.iloc[current_idx] if not pd.isna(macd_signal.iloc[current_idx]) else 0
            prev_macd = macd_line.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_line.iloc[current_idx-1]) else 0
            prev_macd_signal = macd_signal.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_signal.iloc[current_idx-1]) else 0
            current_ma20 = ma_20.iloc[current_idx] if not pd.isna(ma_20.iloc[current_idx]) else current_price
            
            # Buy signal conditions
            buy_signals = []
            
            buy_config = self.strategy_config.config.get('buy_signals', {})
            
            # 1. RSI oversold condition
            rsi_oversold = buy_config.get('rsi_oversold', 30)
            if current_rsi < rsi_oversold:
                buy_signals.append('rsi_oversold')
            
            # 2. Price near Bollinger Band lower
            if buy_config.get('bb_lower_touch', True):
                bb_threshold = 1.02  # Within 2% of lower band
                if current_price <= current_bb_lower * bb_threshold:
                    buy_signals.append('bb_lower_touch')
            
            # 3. MACD bullish crossover
            if buy_config.get('macd_bullish_cross', True):
                if (current_macd > current_macd_signal and 
                    prev_macd <= prev_macd_signal):
                    buy_signals.append('macd_bullish_cross')
            
            # 4. Price above 20-day MA (trend confirmation)
            if current_price > current_ma20:
                buy_signals.append('trend_confirmation')
            
            # 5. Volume surge (if volume data available)
            if volume is not None and len(volume) > 20:
                avg_volume = volume.rolling(20).mean().iloc[current_idx]
                current_volume = volume.iloc[current_idx]
                volume_threshold = buy_config.get('volume_surge_threshold', 1.5)
                if current_volume > avg_volume * volume_threshold:
                    buy_signals.append('volume_surge')
            
            # 6. Positive momentum
            if len(close_prices) > 5:
                momentum = (current_price - close_prices.iloc[current_idx-5]) / close_prices.iloc[current_idx-5]
                momentum_threshold = buy_config.get('momentum_threshold', 0.02)
                if momentum > momentum_threshold:
                    buy_signals.append('positive_momentum')
            
            # More practical buy logic - require at least 1 strong signal
            # This makes trading more likely to happen
            required_signals = 1
            return len(buy_signals) >= required_signals
            
        except Exception as e:
            # Return False if any calculation fails
            return False
    
    def should_sell(self, symbol: str, data: pd.DataFrame, position: Any) -> bool:
        """
        Determine if should sell a position based on normal market conditions.
        
        Uses balanced approach with multiple exit signals:
        - RSI overbought conditions
        - Bollinger Band upper touch
        - MACD bearish crossover
        - Momentum reversal
        - Maximum holding period
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
            
            # Get current and previous values
            current_rsi = rsi.iloc[current_idx] if not pd.isna(rsi.iloc[current_idx]) else 50
            current_price = close_prices.iloc[current_idx]
            current_bb_upper = bb_upper.iloc[current_idx] if not pd.isna(bb_upper.iloc[current_idx]) else current_price
            current_macd = macd_line.iloc[current_idx] if not pd.isna(macd_line.iloc[current_idx]) else 0
            current_macd_signal = macd_signal.iloc[current_idx] if not pd.isna(macd_signal.iloc[current_idx]) else 0
            prev_macd = macd_line.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_line.iloc[current_idx-1]) else 0
            prev_macd_signal = macd_signal.iloc[current_idx-1] if current_idx > 0 and not pd.isna(macd_signal.iloc[current_idx-1]) else 0
            
            sell_config = self.strategy_config.config.get('sell_signals', {})
            risk_config = self.strategy_config.config.get('risk_management', {})
            
            # Sell signal conditions
            sell_signals = []
            
            # 1. RSI overbought condition
            rsi_overbought = sell_config.get('rsi_overbought_exit', 75)
            if current_rsi > rsi_overbought:
                sell_signals.append('rsi_overbought')
            
            # 2. Price near Bollinger Band upper
            if sell_config.get('bb_upper_touch', True):
                bb_threshold = 0.98  # Within 2% of upper band
                if current_price >= current_bb_upper * bb_threshold:
                    sell_signals.append('bb_upper_touch')
            
            # 3. MACD bearish crossover
            if sell_config.get('macd_bearish_cross', True):
                if (current_macd < current_macd_signal and 
                    prev_macd >= prev_macd_signal):
                    sell_signals.append('macd_bearish_cross')
            
            # 4. Momentum reversal
            if len(close_prices) > 5:
                momentum = (current_price - close_prices.iloc[current_idx-5]) / close_prices.iloc[current_idx-5]
                momentum_threshold = sell_config.get('momentum_reversal', -0.03)
                if momentum < momentum_threshold:
                    sell_signals.append('momentum_reversal')
            
            # 5. Maximum holding period
            if position and 'buy_date' in position:
                holding_days = (data.index[current_idx] - position['buy_date']).days
                max_holding_days = risk_config.get('max_holding_days', 30)
                if holding_days > max_holding_days:
                    sell_signals.append('max_holding_period')
            
            # Require at least 1 sell signal for normal market
            return len(sell_signals) >= 1
            
        except Exception as e:
            # Return False if any calculation fails
            return False