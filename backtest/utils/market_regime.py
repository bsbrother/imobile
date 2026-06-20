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
    # Get 120 trading days of index data (approx 6 months)
    date = date.replace('-', '')
    start_date = get_trading_days_before(date, 120) 
    df = data_provider.get_index_data(index_code, start_date, date)

    if df is None or df.empty or len(df) < 30:
        error_msg = f"Insufficient data for regime detection (got {len(df) if df is not None else 0} records, need at least 30). Index: {index_code}"
        logger.error(error_msg)
        raise ValueError(error_msg)
    else:
        # Calculate indicators
        close = df['close'].astype(float)

        ma60 = close.rolling(60, min_periods=30).mean().iloc[-1]
        ma120 = close.rolling(120, min_periods=60).mean().iloc[-1]
        current_price = close.iloc[-1]

        # Calculate volatility (annualized)
        returns = close.pct_change()
        volatility = returns.std() * 100  # Daily volatility as percentage

        # Calculate trend strength
        trend_60d = (current_price - ma60) / ma60 * 100
        trend_120d = (current_price - ma120) / ma120 * 100

        # Regime classification
        logger.debug(f"Regime indicators - Price: {current_price:.2f}, MA60: {ma60:.2f}, "
                    f"MA120: {ma120:.2f}, Vol: {volatility:.2f}%, "
                    f"Trend60d: {trend_60d:.2f}%, Trend120d: {trend_120d:.2f}%")

        # Bull market: Strong uptrend, low volatility
        if current_price > ma60 > ma120 and trend_60d > 0 and volatility < 2.0:
            regime = 'bull'

        # Bear market: Downtrend
        elif current_price < ma60 < ma120 and trend_60d < 0:
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


def get_regime_config(regime: MarketRegime, config_manager) -> dict:
    """
    Get trading configuration for specific market regime.

    Args:
        regime: Market regime
        config_manager: ConfigManager instance

    Environment overrides (set in .env for easy SL testing):
        SL_BULL, SL_NORMAL, SL_VOLATILE, SL_BEAR
    """
    import os as _os
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

    # Override stop_loss_pct from env var if set (for easy SL testing)
    _sl_env = _os.getenv(f'SL_{regime.upper()}')
    if _sl_env is not None:
        try:
            _sl_val = float(_sl_env)
            logger.info(f"SL_{regime.upper()}={_sl_val:.1%} (from .env, overriding config)")
            config['stop_loss_pct'] = _sl_val
        except ValueError:
            logger.warning(f"Invalid SL_{regime.upper()}={_sl_env}, using config value")

    # Add late_trend_filter config
    filter_path = f'trading_rules.late_trend_filter.{regime}_market'
    filter_config = config_manager.get(filter_path, {})
    if filter_config:
        config['late_trend_filter'] = filter_config
    
    return config
