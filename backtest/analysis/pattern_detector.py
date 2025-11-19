"""
Market pattern detection system for China A-shares market.
"""

import pandas as pd
from typing import Dict

from .indicators import TechnicalIndicators
from ..core.interfaces import MarketPatternDetector


class ChinaMarketPatternDetector(MarketPatternDetector):
    """
    Market pattern detector specifically designed for China A-shares market.
    
    Detects four market patterns:
    - normal_market: Balanced market conditions
    - bull_market: Strong upward trend with low volatility
    - bear_market: Downward trend or high uncertainty
    - volatile_market: High volatility with unclear direction
    """
    
    def __init__(self, lookback_days: int = 20, confidence_threshold: float = 0.6):
        """
        Initialize the market pattern detector.
        
        Args:
            lookback_days: Number of days to look back for pattern analysis
            confidence_threshold: Minimum confidence score for pattern detection
        """
        self.lookback_days = lookback_days
        self.confidence_threshold = confidence_threshold
        self.indicators = TechnicalIndicators()
        
        # Pattern classification thresholds
        self.thresholds = {
            'volatility': {
                'low': 0.15,      # Below 15% annualized volatility
                'high': 0.25      # Above 25% annualized volatility
            },
            'trend': {
                'strong_up': 0.02,    # 2% weekly return
                'strong_down': -0.02, # -2% weekly return
            },
            'rsi': {
                'oversold': 30,
                'overbought': 70
            },
            'momentum': {
                'strong': 0.05,   # 5% momentum threshold
                'weak': -0.05
            }
        }
    
    def detect_pattern(self, market_data: pd.DataFrame, trade_date: str) -> str:
        """
        Detect market pattern for the given trade date.
        
        Args:
            market_data: DataFrame with OHLCV data for market index
            trade_date: Date for pattern detection
            Returns:
                Detected pattern: 'normal_market', 'bull_market', 'bear_market', or 'volatile_market'
            """
        # Get data up to trade_date
        trade_date_dt = pd.to_datetime(trade_date)
        mask = pd.to_datetime(market_data.index) <= trade_date_dt
        data = market_data[mask].tail(self.lookback_days * 2)  # Extra buffer for indicators
        
        if len(data) < self.lookback_days:
            return 'normal_market'  # Default to normal if insufficient data
        
        # Calculate technical indicators
        indicators = self._calculate_indicators(data)
        
        # Extract recent values for pattern classification
        recent_data = data.tail(self.lookback_days)
        recent_indicators = {k: v.tail(self.lookback_days) for k, v in indicators.items()}
        
        # Calculate pattern features
        features = self._extract_pattern_features(recent_data, recent_indicators)
        
        # Classify pattern
        pattern = self._classify_pattern(features)
        
        return pattern
    
    def get_confidence(self, pattern: str, market_data: pd.DataFrame) -> float:
        """
        Get confidence score for the detected pattern.
        
        Args:
            pattern: Detected pattern
            market_data: Market data used for detection
            
        Returns:
            Confidence score between 0 and 1
        """
        if len(market_data) < self.lookback_days:
            return 0.5  # Low confidence with insufficient data
        
        # Calculate indicators
        indicators = self._calculate_indicators(market_data)
        recent_data = market_data.tail(self.lookback_days)
        recent_indicators = {k: v.tail(self.lookback_days) for k, v in indicators.items()}
        
        # Extract features
        features = self._extract_pattern_features(recent_data, recent_indicators)
        
        # Calculate confidence based on pattern-specific criteria
        confidence = self._calculate_pattern_confidence(pattern, features)
        
        return min(max(confidence, 0.0), 1.0)  # Clamp between 0 and 1
    
    def _calculate_indicators(self, data: pd.DataFrame) -> Dict[str, pd.Series]:
        """Calculate all technical indicators."""
        close = data['close']
        high = data['high']
        low = data['low']
        
        indicators = {
            'rsi': self.indicators.rsi(close, 14),
            'volatility': self.indicators.volatility(close, 20),
            'sma_20': self.indicators.moving_average(close, 20),
            'sma_50': self.indicators.moving_average(close, 50),
            'ema_12': self.indicators.exponential_moving_average(close, 12),
            'ema_26': self.indicators.exponential_moving_average(close, 26),
            'atr': self.indicators.average_true_range(high, low, close, 14)
        }
        
        # Calculate MACD
        macd_line, signal_line, histogram = self.indicators.macd(close)
        indicators.update({
            'macd': macd_line,
            'macd_signal': signal_line,
            'macd_histogram': histogram
        })
        
        # Calculate Bollinger Bands
        bb_upper, bb_middle, bb_lower = self.indicators.bollinger_bands(close)
        indicators.update({
            'bb_upper': bb_upper,
            'bb_middle': bb_middle,
            'bb_lower': bb_lower
        })
        
        # Calculate Stochastic
        stoch_k, stoch_d = self.indicators.stochastic_oscillator(high, low, close)
        indicators.update({
            'stoch_k': stoch_k,
            'stoch_d': stoch_d
        })
        
        return indicators
    
    def _extract_pattern_features(self, data: pd.DataFrame, indicators: Dict[str, pd.Series]) -> Dict[str, float]:
        """Extract features for pattern classification."""
        close = data['close']
        
        # Price-based features
        returns = close.pct_change().dropna()
        weekly_return = (close.iloc[-1] / close.iloc[-5] - 1) if len(close) >= 5 else 0
        monthly_return = (close.iloc[-1] / close.iloc[0] - 1) if len(close) >= self.lookback_days else 0
        
        # Volatility features
        current_volatility = indicators['volatility'].iloc[-1] if not indicators['volatility'].empty else 0.2
        avg_volatility = indicators['volatility'].mean() if not indicators['volatility'].empty else 0.2
        
        # Trend features
        sma_20 = indicators['sma_20'].iloc[-1] if not indicators['sma_20'].empty else close.iloc[-1]
        sma_50 = indicators['sma_50'].iloc[-1] if not indicators['sma_50'].empty else close.iloc[-1]
        price_vs_sma20 = (close.iloc[-1] / sma_20 - 1) if sma_20 != 0 else 0
        price_vs_sma50 = (close.iloc[-1] / sma_50 - 1) if sma_50 != 0 else 0
        
        # Momentum features
        rsi_current = indicators['rsi'].iloc[-1] if not indicators['rsi'].empty else 50
        rsi_avg = indicators['rsi'].tail(5).mean() if not indicators['rsi'].empty else 50
        
        # MACD features
        macd_current = indicators['macd'].iloc[-1] if not indicators['macd'].empty else 0
        macd_signal = indicators['macd_signal'].iloc[-1] if not indicators['macd_signal'].empty else 0
        macd_histogram = indicators['macd_histogram'].iloc[-1] if not indicators['macd_histogram'].empty else 0
        
        # Bollinger Bands position
        bb_position = 0.5  # Default middle position
        if not indicators['bb_upper'].empty and not indicators['bb_lower'].empty:
            bb_upper = indicators['bb_upper'].iloc[-1]
            bb_lower = indicators['bb_lower'].iloc[-1]
            if bb_upper != bb_lower:
                bb_position = (close.iloc[-1] - bb_lower) / (bb_upper - bb_lower)
        
        # Stochastic features
        stoch_k = indicators['stoch_k'].iloc[-1] if not indicators['stoch_k'].empty else 50
        stoch_d = indicators['stoch_d'].iloc[-1] if not indicators['stoch_d'].empty else 50
        
        return {
            'weekly_return': weekly_return,
            'monthly_return': monthly_return,
            'current_volatility': current_volatility,
            'avg_volatility': avg_volatility,
            'price_vs_sma20': price_vs_sma20,
            'price_vs_sma50': price_vs_sma50,
            'rsi_current': rsi_current,
            'rsi_avg': rsi_avg,
            'macd_current': macd_current,
            'macd_signal': macd_signal,
            'macd_histogram': macd_histogram,
            'bb_position': bb_position,
            'stoch_k': stoch_k,
            'stoch_d': stoch_d,
            'returns_std': returns.std() if len(returns) > 1 else 0.02
        }
    
    def _classify_pattern(self, features: Dict[str, float]) -> str:
        """Classify market pattern based on extracted features."""
        # Calculate pattern scores
        bull_score = self._calculate_bull_score(features)
        bear_score = self._calculate_bear_score(features)
        volatile_score = self._calculate_volatile_score(features)
        
        # Determine pattern based on highest score
        scores = {
            'bull_market': bull_score,
            'bear_market': bear_score,
            'volatile_market': volatile_score
        }
        
        max_pattern = max(scores, key=scores.get)
        max_score = scores[max_pattern]

        # Return pattern if score exceeds threshold, otherwise normal_market
        if max_score > self.confidence_threshold:
            return max_pattern
        else:
            return 'normal_market'

    def _calculate_bull_score(self, features: Dict[str, float]) -> float:
        """Calculate bull market score."""
        score = 0.0
        
        # Positive trend indicators
        if features['weekly_return'] > self.thresholds['trend']['strong_up']:
            score += 0.3
        if features['monthly_return'] > 0.05:  # 5% monthly gain
            score += 0.2
        
        # Price above moving averages
        if features['price_vs_sma20'] > 0.02:  # 2% above SMA20
            score += 0.2
        if features['price_vs_sma50'] > 0.05:  # 5% above SMA50
            score += 0.1
        
        # Low volatility (stable uptrend)
        if features['current_volatility'] < self.thresholds['volatility']['low']:
            score += 0.1
        
        # RSI in healthy range (not overbought)
        if 50 < features['rsi_current'] < self.thresholds['rsi']['overbought']:
            score += 0.1
        
        return score
    
    def _calculate_bear_score(self, features: Dict[str, float]) -> float:
        """Calculate bear market score."""
        score = 0.0
        
        # Negative trend indicators
        if features['weekly_return'] < self.thresholds['trend']['strong_down']:
            score += 0.3
        if features['monthly_return'] < -0.05:  # 5% monthly loss
            score += 0.2
        
        # Price below moving averages
        if features['price_vs_sma20'] < -0.02:  # 2% below SMA20
            score += 0.2
        if features['price_vs_sma50'] < -0.05:  # 5% below SMA50
            score += 0.1
        
        # RSI oversold
        if features['rsi_current'] < self.thresholds['rsi']['oversold']:
            score += 0.1
        
        # MACD bearish
        if features['macd_histogram'] < -0.01:
            score += 0.1
        
        return score
    
    def _calculate_volatile_score(self, features: Dict[str, float]) -> float:
        """Calculate volatile market score."""
        score = 0.0
        
        # High volatility
        if features['current_volatility'] > self.thresholds['volatility']['high']:
            score += 0.4
        
        # High standard deviation of returns
        if features['returns_std'] > 0.03:  # 3% daily std
            score += 0.2
        
        # Extreme RSI readings
        if features['rsi_current'] < 20 or features['rsi_current'] > 80:
            score += 0.2
        
        # Bollinger Bands extremes
        if features['bb_position'] < 0.1 or features['bb_position'] > 0.9:
            score += 0.1
        
        # Conflicting signals (choppy market)
        if abs(features['weekly_return']) < 0.01 and features['current_volatility'] > 0.2:
            score += 0.1
        
        return score
    
    def _calculate_pattern_confidence(self, pattern: str, features: Dict[str, float]) -> float:
        """Calculate confidence score for detected pattern."""
        if pattern == 'bull_market':
            return self._calculate_bull_score(features)
        elif pattern == 'bear_market':
            return self._calculate_bear_score(features)
        elif pattern == 'volatile_market':
            return self._calculate_volatile_score(features)
        else:  # normal_market
            # Confidence is inverse of other pattern scores
            other_scores = [
                self._calculate_bull_score(features),
                self._calculate_bear_score(features),
                self._calculate_volatile_score(features)
            ]
            return 1.0 - max(other_scores)