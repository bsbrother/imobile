"""
Market regime detection for adaptive trading strategies.
"""
from typing import Literal, Dict, Any
from loguru import logger
from backtest import data_provider, global_cm, DB_CACHE_FILE
from backtest.utils.trading_calendar import get_trading_days_before
from backtest.data.sqlite_cache import SQLiteDataCache

MarketRegime = Literal['bull', 'normal', 'volatile', 'bear']
CACHE = SQLiteDataCache(DB_CACHE_FILE)

def detect_market_regime(date: str, index_code: str = '000001.SH') -> Dict[str, Any]:
    """
    Detect current market regime based on index trends and volatility.

    Args:
        date: Trading date in YYYYMMDD or YYYY-MM-DD format
        index_code: Index to analyze (default: SSE Composite)

    Returns:
        Dict containing 'regime' and configuration parameters
    """
    try:
        # Get 60 trading days of index data
        date = date.replace('-', '')
        start_date = get_trading_days_before(date, 60) # not 59, maybe date is not trading date.
        df = data_provider.get_index_data(index_code, start_date, date)

        if df is None or df.empty or len(df) < 60:
            logger.info("Insufficient data for regime detection, retrying after cache clear")
            num = CACHE.clear_cache(pattern=f'index_data_{index_code}_*')
            logger.info(f'clear cache index_data {num}')
            df = data_provider.get_index_data(index_code, start_date, date)
        if df is None or df.empty or len(df) < 60:
            logger.warning("Insufficient data for regime detection, defaulting to 'normal'")
            regime = 'normal'
        else:
            # Calculate indicators
            close = df['close'].astype(float)

            ma20 = close.rolling(20).mean().iloc[-1]
            ma60 = close.rolling(60).mean().iloc[-1]
            current_price = close.iloc[-1]

            # Calculate volatility (annualized)
            returns = close.pct_change()
            volatility = returns.std() * 100  # Daily volatility as percentage

            # Calculate trend strength
            trend_20d = (current_price - ma20) / ma20 * 100
            trend_60d = (current_price - ma60) / ma60 * 100

            # Regime classification
            logger.debug(f"Regime indicators - Price: {current_price:.2f}, MA20: {ma20:.2f}, "
                        f"MA60: {ma60:.2f}, Vol: {volatility:.2f}%, "
                        f"Trend20d: {trend_20d:.2f}%, Trend60d: {trend_60d:.2f}%")

            # Bull market: Strong uptrend, low volatility
            if current_price > ma20 > ma60 and trend_20d > 3 and volatility < 2.0:
                regime = 'bull'

            # Bear market: Downtrend
            elif current_price < ma20 < ma60 and trend_20d < -3:
                regime = 'bear'

            # Volatile market: High volatility regardless of trend
            elif volatility > 3.0:
                regime = 'volatile'

            # Normal market: Default
            else:
                regime = 'normal'

        # Get config for this regime
        config = get_regime_config(regime, global_cm)

        # Merge regime name into config
        result = config.copy()
        result['regime'] = regime

        logger.info(f"Market regime detected: {regime.upper()}")
        return result

    except Exception as e:
        logger.error(f"Error detecting market regime: {e}, defaulting to 'normal'")
        regime = 'normal'
        config = get_regime_config(regime, global_cm)
        result = config.copy()
        result['regime'] = regime
        return result


def get_regime_config(regime: MarketRegime, config_manager) -> dict:
    """
    Get trading configuration for specific market regime.

    Args:
        regime: Market regime
        config_manager: ConfigManager instance

    Returns:
        Dict with regime-specific parameters
    """
    config_path = f'trading_rules.risk_reward_ratios.{regime}_market'
    config = config_manager.get(config_path, {})

    if not config:
        logger.warning(f"No config found for {regime} market, using defaults")
        config = {
            'take_profit_pct': 0.15,
            'stop_loss_pct': 0.06,
            'trailing_stop_enabled': True,
            'max_hold_days': 7,
            'min_hold_days': 2
        }

    return config
