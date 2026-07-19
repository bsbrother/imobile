"""
HMA + SuperTrend Stock Picker Strategy

Based on docs/HMA_SuperTrend.md, this strategy combines:
1. HMA (Hull Moving Average) - reduces lag for trend detection
2. SuperTrend - ATR-based trend confirmation with support/resistance
3. Volatility Filter - avoids high-volatility stocks

Buy Signal = HMA turning up + SuperTrend bullish + Low volatility

Output: JSON to /tmp/tmp matching backtest_orders.py format
"""

import os
import sys
import json
from datetime import datetime
import pandas as pd
import numpy as np
import warnings
from typing import Any

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before, convert_trade_date
from backtest.utils.market_regime import detect_market_regime
from backtest.utils.logging_config import configure_logger
from backtest.strategies.ts_ths_dc import no_risky_stocks

warnings.filterwarnings("ignore", category=UserWarning)

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)

# Strategy parameters
HMA_PERIOD = 20
SUPERTREND_PERIOD = 10
SUPERTREND_MULTIPLIER = 3
VOLATILITY_PERIOD = 20
MAX_VOLATILITY = 0.4  # 40% annualized
LOOKBACK_DAYS = 60  # Days of data to fetch


def calculate_wma(series: pd.Series, period: int) -> pd.Series:
    """Calculate Weighted Moving Average."""
    weights = np.arange(1, period + 1)
    return series.rolling(window=period).apply(
        lambda x: np.sum(x * weights) / np.sum(weights),
        raw=True
    )


def calculate_hma(close: pd.Series, period: int = 20) -> pd.Series:
    """
    Calculate Hull Moving Average (HMA).
    
    HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))
    
    Reduces lag while maintaining smoothness.
    """
    half_length = int(period / 2)
    sqrt_length = int(np.sqrt(period))
    
    wma_half = calculate_wma(close, half_length)
    wma_full = calculate_wma(close, period)
    
    hma_series = 2 * wma_half - wma_full
    hma = calculate_wma(hma_series, sqrt_length)
    
    return hma


def calculate_supertrend(high: pd.Series, low: pd.Series, close: pd.Series,
                         period: int = 10, multiplier: float = 3) -> tuple[pd.Series, pd.Series]:
    """
    Calculate SuperTrend indicator.
    
    Returns:
        tuple: (supertrend values, direction: 1=bullish, -1=bearish)
    """
    # Calculate ATR
    high_low = high - low
    high_close = np.abs(high - close.shift(1))
    low_close = np.abs(low - close.shift(1))
    
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    atr = tr.rolling(window=period).mean()
    
    # Calculate bands
    hl2 = (high + low) / 2
    upper_band = hl2 + (multiplier * atr)
    lower_band = hl2 - (multiplier * atr)
    
    # Initialize SuperTrend
    supertrend = pd.Series(np.nan, index=close.index)
    direction = pd.Series(1, index=close.index)  # 1=bullish, -1=bearish
    
    for i in range(period, len(close)):
        if close.iloc[i] > upper_band.iloc[i-1]:
            supertrend.iloc[i] = lower_band.iloc[i]
            direction.iloc[i] = 1
        elif close.iloc[i] < lower_band.iloc[i-1]:
            supertrend.iloc[i] = upper_band.iloc[i]
            direction.iloc[i] = -1
        else:
            supertrend.iloc[i] = supertrend.iloc[i-1]
            direction.iloc[i] = direction.iloc[i-1]
            
            if direction.iloc[i] == 1 and lower_band.iloc[i] < supertrend.iloc[i]:
                supertrend.iloc[i] = lower_band.iloc[i]
            elif direction.iloc[i] == -1 and upper_band.iloc[i] > supertrend.iloc[i]:
                supertrend.iloc[i] = upper_band.iloc[i]
    
    return supertrend, direction


def calculate_volatility(close: pd.Series, period: int = 20) -> pd.Series:
    """Calculate annualized volatility."""
    returns = close.pct_change()
    volatility = returns.rolling(window=period).std() * np.sqrt(252)
    return volatility


def analyze_stock_with_data(ts_code: str, df: pd.DataFrame) -> dict | None:
    """
    Analyze a single stock using HMA + SuperTrend strategy with pre-fetched data.
    
    Args:
        ts_code: Stock code
        df: Pre-fetched OHLCV DataFrame (must have trade_date, open, high, low, close, vol columns)
    
    Returns:
        dict with analysis results or None if insufficient data
    """
    try:
        if df is None or df.empty or len(df) < 30:
            return None
        
        # Sort by date ascending for calculations
        df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
        
        close = df['close'].astype(float)
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        
        # Calculate indicators
        hma = calculate_hma(close, HMA_PERIOD)
        supertrend, st_direction = calculate_supertrend(high, low, close, SUPERTREND_PERIOD, SUPERTREND_MULTIPLIER)
        volatility = calculate_volatility(close, VOLATILITY_PERIOD)
        
        # Get latest values
        latest_close = close.iloc[-1]
        latest_hma = hma.iloc[-1]
        latest_st = supertrend.iloc[-1]
        latest_st_dir = st_direction.iloc[-1]
        latest_vol = volatility.iloc[-1]
        
        if pd.isna(latest_hma) or pd.isna(latest_st) or pd.isna(latest_vol):
            return None
        
        # HMA signal: 1 if trending up, -1 if trending down
        hma_signal = 1 if latest_hma > hma.iloc[-2] else -1
        prev_hma_signal = 1 if hma.iloc[-2] > hma.iloc[-3] else -1
        
        # HMA turning up = just started trending up
        hma_turning_up = (hma_signal == 1) and (prev_hma_signal == -1)
        
        # SuperTrend bullish
        st_bullish = latest_st_dir == 1
        
        # Low volatility
        low_volatility = latest_vol < MAX_VOLATILITY
        
        # Buy signal
        buy_signal = hma_turning_up and st_bullish and low_volatility
        
        # Trend strength (distance from SuperTrend)
        trend_strength = abs(latest_close - latest_st) / latest_st if latest_st > 0 else 0
        
        # Scoring system (0-5 points, then scaled to 0-100)
        score = 0
        
        # 1. HMA above price indicates support
        if latest_hma < latest_close:
            score += 1
        
        # 2. Trend direction (SuperTrend bullish)
        if st_bullish:
            score += 1
        
        # 3. Low volatility (< 30%)
        if latest_vol < 0.3:
            score += 1
        
        # 4. Buy signal generated
        if buy_signal:
            score += 1
        
        # 5. Strong trend (> 2% distance from SuperTrend)
        if trend_strength > 0.02:
            score += 1
        
        # Scale score to 0-100
        composite_score = (score / 5) * 100
        
        # Additional signal: HMA momentum (looking at recent trend)
        hma_momentum = (hma.iloc[-1] / hma.iloc[-5] - 1) * 100 if len(hma.dropna()) >= 5 else 0
        
        # Boost score for stocks with positive HMA momentum
        if hma_momentum > 2:
            composite_score += 10
        elif hma_momentum > 0:
            composite_score += 5
        
        return {
            'ts_code': ts_code,
            'close': latest_close,
            'hma': latest_hma,
            'supertrend': latest_st,
            'st_direction': 'Bullish' if st_bullish else 'Bearish',
            'volatility': latest_vol,
            'trend_strength': trend_strength,
            'hma_momentum': hma_momentum,
            'buy_signal': buy_signal,
            'score': score,
            'composite_score': round(composite_score, 2)
        }
        
    except Exception as e:
        logger.warning(f"Error analyzing {ts_code}: {e}")
        return None


def analyze_stock(ts_code: str, end_date: str) -> dict | None:
    """
    Analyze a single stock using HMA + SuperTrend strategy (legacy API with per-stock fetch).
    For bulk analysis, use analyze_stock_with_data() with pre-fetched data.
    
    Returns:
        dict with analysis results or None if insufficient data
    """
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS)
    
    try:
        df = data_provider.get_ohlcv_data(symbol=ts_code, start_date=start_date, end_date=end_date)
        return analyze_stock_with_data(ts_code, df)
    except Exception as e:
        logger.warning(f"Error fetching data for {ts_code}: {e}")
        return None


# no_risky_stocks is imported from ts_ths_dc (unified pick_filter from config.json)


def pick_hma_stocks(end_date: str, max_picks: int = 10) -> pd.DataFrame:
    """
    Pick stocks using HMA + SuperTrend strategy.
    
    Optimized to use bulk data fetching - fetches all stocks' data once,
    then analyzes each stock from pre-fetched data.
    
    Args:
        end_date: Reference date for analysis (YYYYMMDD)
        max_picks: Maximum number of stocks to return
    
    Returns:
        DataFrame with picked stocks
    """
    logger.info(f"[ts_hma] Starting HMA + SuperTrend stock picking for {end_date}")
    
    # Get stock universe
    stock_basic = data_provider.get_basic_information_api()
    if stock_basic.empty:
        raise ValueError("No basic stock information found")
    
    # Filter to mainboard only
    total_stocks = len(stock_basic)
    risky_free_list = no_risky_stocks(stock_basic)
    stock_basic = stock_basic[stock_basic['ts_code'].isin(risky_free_list)].reset_index(drop=True)
    logger.info(f"[ts_hma] Universe: {total_stocks} total -> {len(stock_basic)} mainboard stocks")
    
    # Detect market regime for adaptive filtering
    regime_data: Any = detect_market_regime(end_date)
    regime = regime_data.get('regime', 'normal')
    logger.info(f"[ts_hma] Market Regime: {regime}")
    
    # Adjust max picks based on regime
    regime_max_picks = {
        'bull': 15,
        'normal': 15,
        'volatile': 5,
        'bear': 3
    }
    max_picks = regime_max_picks.get(regime, max_picks)
    
    # === BULK DATA FETCH OPTIMIZATION ===
    # Fetch all stocks' data in one go instead of per-stock API calls
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS)
    logger.info(f"[ts_hma] Bulk fetching OHLCV data from {start_date} to {end_date}...")
    all_stock_data = data_provider.get_bulk_ohlcv_by_date_range(start_date, end_date)
    logger.info(f"[ts_hma] Bulk fetch complete: {len(all_stock_data)} stocks with data")
    
    # Analyze all stocks using pre-fetched data
    results = []
    total = len(stock_basic)
    analyzed = 0
    
    for idx, row in stock_basic.iterrows():
        if idx % 500 == 0:
            logger.info(f"[ts_hma] Analyzing stocks: {idx}/{total}")
        
        ts_code = row['ts_code']
        stock_df = all_stock_data.get(ts_code)
        
        if stock_df is not None:
            analysis = analyze_stock_with_data(ts_code, stock_df)
            if analysis:
                analysis['name'] = row['name']
                results.append(analysis)
                analyzed += 1
    
    logger.info(f"[ts_hma] Analyzed {analyzed} stocks with valid data")
    
    if not results:
        logger.warning("[ts_hma] No stocks passed the analysis criteria")
        return pd.DataFrame()
    
    # Create DataFrame and filter
    df = pd.DataFrame(results)
    
    # Filter: Only bullish stocks with reasonable volatility
    df = df[df['st_direction'] == 'Bullish']
    df = df[df['volatility'] < MAX_VOLATILITY]
    
    # Sort by composite score
    df = df.sort_values('composite_score', ascending=False)
    
    # Apply score threshold based on regime
    min_score_thresholds = {
        'bull': 40,
        'normal': 50,
        'volatile': 55,
        'bear': 60
    }
    min_score = min_score_thresholds.get(regime, 50)
    
    before_filter = len(df)
    df = df[df['composite_score'] >= min_score]
    
    if len(df) == 0 and before_filter > 0:
        logger.warning(f"[ts_hma] Min score {min_score} filtered all stocks, using top by score")
        df = pd.DataFrame(results)
        df = df[df['st_direction'] == 'Bullish']
        df = df.sort_values('composite_score', ascending=False).head(max_picks)
    
    # === Late-trend filter moved to backtest_orders.py centrally ===
    
    # Limit to max picks
    df = df.head(max_picks)
    
    # Add rank
    df['rank'] = range(1, len(df) + 1)
    
    logger.info(f"[ts_hma] Selected {len(df)} stocks for regime={regime}")
    
    # Log top picks
    for i, row in df.head(5).iterrows():
        logger.info(
            f"[ts_hma] #{row['rank']}: {row['name']}({row['ts_code']}) "
            f"score={row['composite_score']:.1f} vol={row['volatility']:.1%} "
            f"trend={row['st_direction']}"
        )
    
    return df


if __name__ == "__main__":
    argv = sys.argv[1:]
    lookahead = False
    
    if '--lookahead' in argv:
        lookahead = True
        argv.remove('--lookahead')
    
    if len(argv) >= 1:
        date = convert_trade_date(argv[0])
        target_date = argv[0]
    else:
        date = datetime.now().strftime('%Y%m%d')
        target_date = date
    
    # Use previous trading day unless lookahead is enabled
    if not lookahead:
        date = get_trading_days_before(date, 1)
    
    logger.info(f"[ts_hma] Picking stocks for target date with reference date: {date}")
    
    df = pick_hma_stocks(end_date=date)
    
    # Output to standard format
    output_file = '/tmp/tmp'
    selected_stocks = []
    
    for _, stock in df.iterrows():
        selected_stocks.append({
            'rank': int(stock['rank']),
            'symbol': stock['ts_code'],
            'score': float(stock['composite_score'])
        })
    
    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)
    
    logger.info(f"[ts_hma] Saved {len(selected_stocks)} picked stocks to {output_file}")
