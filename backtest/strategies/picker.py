"""
Stock picker system for China A-shares.
Implements filtering and ranking algorithms for stock selection.
"""

from loguru import logger
from typing import List, Dict, Optional, Any, TYPE_CHECKING
import pandas as pd
import numpy as np

from ..core.interfaces import StockPicker, DataProvider
from .. import global_cm
from ..analysis.indicators import TechnicalIndicators
from ..utils.util import create_dataframe_filter, _safe_fillna

if TYPE_CHECKING:
    # Avoid runtime circular imports
    from .manager import StrategyManager


class RankingAlgorithm:
    """Implements stock ranking algorithms based on multiple scoring methods."""

    def __init__(self, weights: Optional[Dict[str, float]] = None):
        """
        Initialize ranking algorithm with configurable weights.

        Args:
            weights: Dictionary of scoring weights for different factors
        """
        self.weights = weights or {
            'momentum': 0.4,
            'volume': 0.3,
            'technical': 0.3
        }

        # Validate weights sum to 1.0
        total_weight = sum(self.weights.values())
        if abs(total_weight - 1.0) > 0.01:
            logger.warning(f"Weights sum to {total_weight:.3f}, normalizing to 1.0")
            self.weights = {k: v/total_weight for k, v in self.weights.items()}

    def set_weights(self, weights: Dict[str, float]):
        """Set and normalize weights dynamically."""
        try:
            total = sum(weights.values())
            if total <= 0:
                raise ValueError("Weights must sum to a positive value")
            self.weights = {k: v/total for k, v in weights.items()}
            logger.debug(f"Updated ranking weights: {self.weights}")
        except Exception as e:
            logger.error(f"Failed to set weights: {e}")

    def calculate_momentum_score(self, data: pd.DataFrame, lookback_days: int = 20) -> float:
        """
        Calculate momentum score based on price and volume patterns.

        Args:
            data: OHLCV data for a stock
            lookback_days: Number of days to look back for momentum calculation

        Returns:
            Momentum score (0-100)
        """
        if len(data) < lookback_days:
            return 0.0

        try:
            # Sort by date to ensure proper order
            data = data.sort_values('trade_date')

            # Price momentum (rate of change)
            close_prices = data['close'].tail(lookback_days)
            price_change = (close_prices.iloc[-1] - close_prices.iloc[0]) / close_prices.iloc[0]

            # Volume momentum (recent volume vs average)
            volumes = data['vol'].tail(lookback_days)
            recent_volume = volumes.tail(5).mean()
            avg_volume = volumes.mean()
            volume_momentum = (recent_volume - avg_volume) / avg_volume if avg_volume > 0 else 0

            # Combine price and volume momentum
            momentum_score = (price_change * 50) + (volume_momentum * 30)

            # Normalize to 0-100 range
            momentum_score = max(0.0, min(100.0, momentum_score * 100 + 50))

            return float(momentum_score)

        except Exception as e:
            logger.error(f"Momentum score calculation failed: {str(e)}")
            return 0.0

    def calculate_volume_score(self, data: pd.DataFrame, lookback_days: int = 20) -> float:
        """
        Calculate volume-based score indicating trading activity and interest.

        Args:
            data: OHLCV data for a stock
            lookback_days: Number of days for volume analysis

        Returns:
            Volume score (0-100)
        """
        if len(data) < lookback_days:
            return 0.0

        try:
            # Sort by date
            data = data.sort_values('trade_date')
            volumes = data['vol'].tail(lookback_days)

            # Volume trend (increasing volume)
            volume_trend = np.polyfit(range(len(volumes)), volumes, 1)[0]
            volume_trend_score = max(0, min(50, volume_trend / volumes.mean() * 1000))

            # Volume consistency (lower volatility is better)
            volume_cv = volumes.std() / volumes.mean() if volumes.mean() > 0 else 1
            consistency_score = max(0, 50 - volume_cv * 25)

            # Recent volume surge
            recent_avg = volumes.tail(5).mean()
            historical_avg = volumes.head(15).mean()
            surge_score = max(0, min(50, (recent_avg - historical_avg) / historical_avg * 100)) if historical_avg > 0 else 0

            volume_score = (volume_trend_score + consistency_score + surge_score) / 1.5
            return float(max(0.0, min(100.0, volume_score)))

        except Exception as e:
            logger.error(f"Volume score calculation failed: {str(e)}")
            return 0.0

    def calculate_technical_score(self, data: pd.DataFrame) -> float:
        """
        Calculate technical analysis score using multiple indicators.

        Args:
            data: OHLCV data for a stock

        Returns:
            Technical score (0-100)
        """
        if len(data) < global_cm.get('pattern_detector.lookback_days'):
            return 0.0

        try:
            # Sort by date
            data = data.sort_values('trade_date')
            close_prices = data['close']
            high_prices = data['high']
            low_prices = data['low']

            scores = []

            # RSI score (prefer RSI between 30-70, avoid overbought/oversold)
            rsi = TechnicalIndicators.rsi(close_prices)
            latest_rsi = rsi.iloc[-1] if not pd.isna(rsi.iloc[-1]) else 50
            if 30 <= latest_rsi <= 70:
                rsi_score = 100 - abs(latest_rsi - 50) * 2  # Higher score for RSI near 50
            else:
                rsi_score = max(0, 100 - abs(latest_rsi - 50) * 3)  # Penalize extreme RSI
            scores.append(rsi_score)

            # Bollinger Bands score (prefer prices near middle band)
            upper_bb, middle_bb, lower_bb = TechnicalIndicators.bollinger_bands(close_prices)
            latest_price = close_prices.iloc[-1]
            latest_middle = middle_bb.iloc[-1]
            latest_upper = upper_bb.iloc[-1]
            latest_lower = lower_bb.iloc[-1]

            if not (pd.isna(latest_middle) or pd.isna(latest_upper) or pd.isna(latest_lower)):
                bb_position = (latest_price - latest_lower) / (latest_upper - latest_lower)
                bb_score = 100 - abs(bb_position - 0.5) * 200  # Prefer middle of bands
                bb_score = max(0, bb_score)
                scores.append(bb_score)

            # Moving average score (price above MA is positive)
            ma_20 = TechnicalIndicators.moving_average(close_prices, 20)
            if not pd.isna(ma_20.iloc[-1]):
                ma_score = 50 + ((latest_price - ma_20.iloc[-1]) / ma_20.iloc[-1]) * 500
                ma_score = max(0, min(100, ma_score))
                scores.append(ma_score)

            # MACD score (positive MACD is bullish)
            macd_line, signal_line, histogram = TechnicalIndicators.macd(close_prices)
            if not pd.isna(histogram.iloc[-1]):
                macd_score = 50 + histogram.iloc[-1] * 1000  # Scale histogram
                macd_score = max(0, min(100, macd_score))
                scores.append(macd_score)

            # Stochastic score (prefer %K between 20-80)
            k_percent, d_percent = TechnicalIndicators.stochastic_oscillator(high_prices, low_prices, close_prices)
            if not pd.isna(k_percent.iloc[-1]):
                k_value = k_percent.iloc[-1]
                if 20 <= k_value <= 80:
                    stoch_score = 100 - abs(k_value - 50) * 1.5
                else:
                    stoch_score = max(0, 100 - abs(k_value - 50) * 2.5)
                scores.append(stoch_score)

            # Return average of all technical scores
            technical_score = np.mean(scores) if scores else 0.0
            return float(max(0.0, min(100.0, technical_score)))

        except Exception as e:
            logger.error(f"Technical score calculation failed: {str(e)}")
            return 0.0

    def calculate_composite_score(self, symbol: str, data: pd.DataFrame) -> float:
        """
        Calculate composite score combining all scoring methods.

        Args:
            symbol: Stock symbol
            data: OHLCV data for the stock

        Returns:
            Composite score (0-100)
        """
        try:
            momentum_score = self.calculate_momentum_score(data)
            volume_score = self.calculate_volume_score(data)
            technical_score = self.calculate_technical_score(data)

            composite_score = (
                momentum_score * self.weights['momentum'] +
                volume_score * self.weights['volume'] +
                technical_score * self.weights['technical']
            )

            logger.debug(f"Scores for {symbol}: momentum={momentum_score:.1f}, "
                            f"volume={volume_score:.1f}, technical={technical_score:.1f}, "
                            f"composite={composite_score:.1f}")

            return composite_score

        except Exception as e:
            logger.error(f"Composite score calculation failed for {symbol}: {str(e)}")
            return 0.0


class StockPool:
    """
    Manages the candidate stock pool with maximum size constraint.
    """

    def __init__(self):
        """
        Initialize stock pool.
        """
        self.max_pick = global_cm.get('stock_picker.max_pick')
        self.current_pool: List[str] = []
        self.pool_scores: Dict[str, float] = {}
        self.last_update_date: Optional[str] = None

    def update_pool(self, new_candidates: Dict[str, float], trade_date: str) -> List[str]:
        """
        Update the stock pool with new candidates and their scores.

        Args:
            new_candidates: Dictionary of {symbol: score}
            trade_date: Date of the update

        Returns:
            Updated list of stocks in the pool
        """
        if not new_candidates:
            logger.warning(f"No candidates provided for pool update on {trade_date}")
            return self.current_pool

        # Sort candidates by score (descending)
        sorted_candidates = sorted(new_candidates.items(), key=lambda x: x[1], reverse=True)

        # Select top candidates up to max_pick
        selected_candidates = sorted_candidates[:self.max_pick]

        # Update pool
        self.current_pool = [symbol for symbol, score in selected_candidates]
        self.pool_scores = dict(selected_candidates)
        self.last_update_date = trade_date

        logger.info(f"Updated stock pool on {trade_date}: {len(self.current_pool)} stocks")
        return self.current_pool

    def get_current_pool(self) -> List[str]:
        """Get the current stock pool."""
        return self.current_pool.copy()

    def get_pool_with_scores(self) -> Dict[str, float]:
        """Get the current pool with scores."""
        return self.pool_scores.copy()

    def is_in_pool(self, symbol: str) -> bool:
        """Check if a symbol is in the current pool."""
        return symbol in self.current_pool

    def get_pool_size(self) -> int:
        """Get current pool size."""
        return len(self.current_pool)

    def get_pool_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        if not self.current_pool:
            return {
                'size': 0,
                'max_pick': self.max_pick,
                'last_update': self.last_update_date,
                'avg_score': 0.0,
                'min_score': 0.0,
                'max_score': 0.0
            }

        scores = list(self.pool_scores.values())
        return {
            'size': len(self.current_pool),
            'max_pick': self.max_pick,
            'last_update': self.last_update_date,
            'avg_score': np.mean(scores),
            'min_score': min(scores),
            'max_score': max(scores)
        }


class ASharesStockPicker(StockPicker):
    """
    Main stock picker implementation for China A-shares market.
    Integrates filtering and ranking algorithms for daily stock selection.
    """

    def __init__(self, data_provider: DataProvider, strategy_manager: Optional["StrategyManager"] = None):
        """
        Initialize A-shares stock picker.

        Args:
            data_provider: Data provider for market data
            strategy_manager: Optional strategy manager for handling market patterns
        """
        self.data_provider = data_provider
        self.strategy_manager = strategy_manager
        self.lookback_days = global_cm.get('pattern_detector.lookback_days')
        self.confidence_threshold = global_cm.get('pattern_detector.confidence_threshold')
        self.max_pick = global_cm.get('stock_picker.max_pick')
        self.strategy_mode = global_cm.get('init_info.strategy')
        self.pattern = 'normal_market' if self.strategy_mode in ('auto', 'simple') else self.strategy_mode
        self.ranking_algorithm = RankingAlgorithm(global_cm.get(f'strategies.{self.pattern}.ranking.weights'))
        self.market_cap_range = global_cm.get(f'strategies.{self.pattern}.market_cap_range')
        self.price_range = global_cm.get(f'strategies.{self.pattern}.price_range')
        self.stock_pool = StockPool()


    def pick_stocks(self, trade_date: str) -> List[str]:
        """
        Select top stocks for given trade date based on short-term potential.
        Updates the candidate pool and returns the top stocks from the pool.

        Args:
            trade_date: Date for stock selection (YYYYMMDD format)

        Returns:
            List of selected stock symbols (maximum 10)
        """
        from ..utils.trading_calendar import get_trading_days_before
        logger.info(f"Starting daily stock selection for {trade_date} ...")

        prev_date = get_trading_days_before(trade_date, 1)
        logger.info(f'Use {prev_date} previous trading day data to analysis...')
        # 1. Query provider for all active A-shares
        df = self.data_provider.get_basic_information()
        if df.empty:
            raise ValueError("No basic info returned")

        # Filter high-risk stocks using regex patterns
        name_pattern = r'^(?:C|N|\*?ST|S)|退'
        ts_code_pattern = r'^(?:C|N|\*|4|9|8|30|688)|ST'
        exclude_conditions = (
            df['name'].str.contains(name_pattern, regex=True) |
            df['ts_code'].str.contains(ts_code_pattern, regex=True)
        )
        df = df[~exclude_conditions]
        logger.debug(f"After basic risk filter then retain {len(df)} stocks.")
        df = self.data_provider.get_stock_data(df['ts_code'].tolist(), prev_date, prev_date)

        # 2. Adapt ranking weights and market cap range based on detected pattern and config, strategy mode:
        # auto: will use strategy_manager to detect market pattern
        # simple: will use previous trade day data calculate gain rate <> config.pattern_detector.simple_market_thresholds
        # ohter strategies: fixed in config.strategies, e.g. 'normal_market', 'bull_market', 'bear_market', 'volatile_market', ...
        if self.strategy_mode == 'auto' and self.strategy_manager:
            start_date = get_trading_days_before(prev_date, self.lookback_days)
            index_df = self.data_provider.get_index_data('000300.SH', start_date, prev_date)
            self.pattern = self.strategy_manager.pattern_detector.detect_pattern(index_df, prev_date)
            confidence = self.strategy_manager.pattern_detector.get_confidence(self.pattern, index_df)
            if not (self.pattern and confidence >= self.confidence_threshold):
                logger.warning(f"Market unknown {self.pattern} {confidence}:{self.confidence_threshold}; use normal_market(default)")
                self.pattern = 'normal_market'
        elif self.strategy_mode == 'simple':
            up_count = (df['pct_chg'] > 0).sum()
            total_count = len(df)
            up_ratio = up_count / total_count if total_count > 0 else 0
            if up_ratio >= global_cm.get('pattern_detector.simple_market_thresholds.bull'):
                self.pattern = 'bull_market'
                factors = global_cm.get('stock_picker.simple_gain_volume_turnover.bull')
            elif up_ratio >= global_cm.get('pattern_detector.simple_market_thresholds.normal'):
                self.pattern = 'normal_market'
                factors = global_cm.get('stock_picker.simple_gain_volume_turnover.normal')
            elif up_ratio >= global_cm.get('pattern_detector.simple_market_thresholds.volatile'):
                self.pattern = 'volatile_market'
                factors = global_cm.get('stock_picker.simple_gain_volume_turnover.volatile')
            else:
                self.pattern = 'bear_market'
                factors = global_cm.get('stock_picker.simple_gain_volume_turnover.bear')
            logger.info(f"Simple market pattern: {self.pattern} ({up_ratio:.1%} up)")
        else:
            self.pattern = self.strategy_mode
        logger.info(f"Strategy mode: {self.strategy_mode}, Market pattern {self.pattern}")
        if not global_cm.get(f'strategies.{self.pattern}'):
            raise ValueError(f'Invalid strategy config for pattern: {self.pattern}')
        self.ranking_algorithm.set_weights(global_cm.get(f'strategies.{self.pattern}.ranking.weights'))
        self.market_cap_range = tuple(global_cm.get(f'strategies.{self.pattern}.market_cap_range'))
        self.price_range = tuple(global_cm.get(f'strategies.{self.pattern}.price_range'))

        # Filter obvious bad stocks
        if self.strategy_mode == 'simple':
            df = df[create_dataframe_filter(df, factors, {})]
        else:
            df = df[create_dataframe_filter(df, global_cm.get('stock_picker.remove_obvious_bad'), {
                'min_price': self.price_range[0],
                'max_price': self.price_range[1],
                'min_market_cap': self.market_cap_range[0],
                'max_market_cap': self.market_cap_range[1]
            })]
        logger.debug(f"After obvious bad filter then retain {len(df)} stocks.")

        # 3. Update the candidate pool with fresh analysis
        self.refresh_candidate_pool(df, prev_date)

        # Get current pool
        current_pool = self.stock_pool.get_current_pool()

        if not current_pool:
            logger.warning("No stocks in candidate pool")
            return []

        # Return top stocks from pool (limited by max_pick and pool size)
        selected_stocks = current_pool[:self.max_pick]

        logger.info(f"Selected {len(selected_stocks)} stocks from pool of {len(current_pool)}")

        # Log selection details
        pool_with_scores = self.stock_pool.get_pool_with_scores()
        for i, symbol in enumerate(selected_stocks):
            score = pool_with_scores.get(symbol)
            logger.info(f"Selected rank {i+1}: {symbol} (score: {score:.2f})")

        return selected_stocks


    def refresh_candidate_pool(self, df: pd.DataFrame, prev_date: str) -> bool:
        """
        Refresh the candidate pool with new analysis for the given trade date.

        Args:
            df: DataFrame containing candidate stocks
            prev_date: Date for pool refresh (YYYYMMDD format)

        Returns:
            True if pool was successfully refreshed, False otherwise
        """
        logger.info(f"Refreshing candidate pool for {prev_date}")

        # Clear current pool
        self.stock_pool.current_pool = []
        self.stock_pool.pool_scores = {}
        stock_scores = {}

        if df is None or df.empty:
            logger.warning("No candidate stocks available for pool refresh")
            return False

        # Ensure required factor columns exist and are numeric
        for col, default in [( 'pct_chg', 0.0 ), ( 'volume_ratio', 100.0 ), ( 'turnover_rate', 0.0 ), ( 'pe', 50.0 )]:
            if col not in df.columns:
                df[col] = default
            else:
                df[col] = pd.to_numeric(df[col], errors='coerce')
                fill_val = df[col].median()
                if pd.isna(fill_val):
                    fill_val = default
                df[col] = _safe_fillna(df[col], fill_val)

        if self.strategy_mode == 'simple' or self.pattern in ('normal_market', 'bull_market', 'bear_market', 'volatile_market'):
            # Simple percentile-based normalization (fastest)
            def quick_norm(series):
                rank = series.rank(pct=True)
                return rank

            # Normalize key factors ->configi: ranking.weights
            df['gain_score'] = quick_norm(df['pct_chg'])
            df['volume_score'] = quick_norm(df['volume_ratio'])
            df['turnover_score'] = quick_norm(df['turnover_rate'])
            df['value_score'] = quick_norm(-df['pe'])  # Lower PE is better

            if self.pattern == 'bull_market':
                weights = {'gain_score': 0.40, 'volume_score': 0.30, 'turnover_score': 0.20, 'value_score': 0.10}
            elif self.pattern == 'bear_market':
                weights = {'gain_score': 0.30, 'volume_score': 0.20, 'turnover_score': 0.20, 'value_score': 0.30}
            else:  # normal or volatile
                weights = {'gain_score': 0.35, 'volume_score': 0.25, 'turnover_score': 0.25, 'value_score': 0.15}

            # Calculate final score
            df['trading_score'] = sum(df[factor] * weight for factor, weight in weights.items())

            # Sort by score
            df = df.sort_values('trading_score', ascending=False)
            for symbol in df['ts_code'].tolist():
                score = df.loc[df['ts_code'] == symbol, 'trading_score'].values[0]
                stock_scores[symbol] = max(0, score)

        else:
            # Get historical data for scoring
            from ..utils.trading_calendar import get_trading_days_before
            start_date = get_trading_days_before(prev_date, self.lookback_days)
            end_date = prev_date
            # Fetch full columns for all candidates
            stock_data = self.data_provider.get_stock_data(df['ts_code'].tolist(), start_date, end_date)
            # Calculate scores for each stock
            for symbol in stock_data['ts_code'].tolist():
                symbol_data = stock_data[stock_data['ts_code'] == symbol].copy()
                score = self.calculate_score(symbol, symbol_data)
                stock_scores[symbol] = max(0, score)

        # Update the pool with new scores
        if stock_scores:
            self.stock_pool.update_pool(stock_scores, prev_date)
            logger.info(f"Pool refreshed with {len(stock_scores)} scored candidates")
            return True
        else:
            logger.warning("No stocks received valid scores for pool update")
            return False


    def calculate_score(self, symbol: str, data: pd.DataFrame) -> float:
        """
        Calculate score for a stock using the ranking algorithm.

        Args:
            symbol: Stock symbol
            data: Historical OHLCV data for the stock

        Returns:
            Stock score (0-100)
        """
        if data.empty:
            return 0.0

        return self.ranking_algorithm.calculate_composite_score(symbol, data)


    def get_pool_status(self) -> Dict[str, Any]:
        """
        Get current status of the candidate pool.

        Returns:
            Dictionary with pool status information
        """
        return self.stock_pool.get_pool_stats()

    def get_current_pool(self) -> List[str]:
        """
        Get the current candidate pool.

        Returns:
            List of symbols in the current pool
        """
        return self.stock_pool.get_current_pool()

    def get_pool_with_scores(self) -> Dict[str, float]:
        """
        Get the current pool with scores.

        Returns:
            Dictionary mapping symbols to their scores
        """
        return self.stock_pool.get_pool_with_scores()

    def is_in_pool(self, symbol: str) -> bool:
        """
        Check if a symbol is in the current candidate pool.

        Args:
            symbol: Stock symbol to check

        Returns:
            True if symbol is in pool, False otherwise
        """
        return self.stock_pool.is_in_pool(symbol)

    def get_pool_ranking(self, symbol: str) -> Optional[int]:
        """
        Get the ranking of a symbol in the current pool.

        Args:
            symbol: Stock symbol

        Returns:
            Ranking (1-based) or None if not in pool
        """
        current_pool = self.stock_pool.get_current_pool()
        try:
            return current_pool.index(symbol) + 1
        except ValueError:
            return None

    def analyze_pool_changes(self, previous_date: str, current_date: str) -> Dict[str, Any]:
        """
        Analyze changes in the pool between two dates.
        This would require storing historical pool states in a production system.

        Args:
            previous_date: Previous date for comparison
            current_date: Current date

        Returns:
            Dictionary with change analysis
        """
        # This is a placeholder implementation
        # In a full system, you'd store historical pool states
        current_pool = self.stock_pool.get_current_pool()

        return {
            'current_date': current_date,
            'previous_date': previous_date,
            'current_pool_size': len(current_pool),
            'current_pool': current_pool,
            'note': 'Historical pool comparison requires persistent storage'
        }


if __name__ == "__main__":
    from .. import data_provider
    from .manager import StrategyManager
    from ..analysis.pattern_detector import ChinaMarketPatternDetector

    #picker = ASharesStockPicker(data_provider) # Always normal_market
    picker = ASharesStockPicker(data_provider, StrategyManager(pattern_detector=ChinaMarketPatternDetector()))

    trade_date = '20250130'
    selected_stocks = picker.pick_stocks(trade_date)

    print(f"Selected {len(selected_stocks)} stocks for {trade_date}")
    print(selected_stocks)
    print("Current pool status:", picker.get_pool_status())
