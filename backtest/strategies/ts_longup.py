"""
ts_longup: Long-Term Uptrend Stock Picker Strategy

Detects stocks at the EARLY STAGE of a long-term upward trend, not at peak momentum.
Unlike ts_dc/ts_ai which chase hot sectors, this strategy identifies:
1. MA Alignment: MA20 > MA60 > MA120, all trending up
2. Price near MAs (early stage, not overextended)
3. Volume expanding from consolidation
4. ADX rising (new trend forming, not exhausted)
5. Pullback-recovery pattern (bounce off MA20)
6. Relative strength vs CSI300 index

Key difference from other strategies:
- Targets TRANSITION from consolidation → uptrend (not peak momentum)
- Designed for 20-30 day holds (not 3-10 days)
- Wider TP/SL to let winners run

Usage:
    python ts_longup.py YYYYMMDD [--lookahead]
"""

import os
import sys
import json
from datetime import datetime
import pandas as pd
import numpy as np
import warnings
from typing import Optional

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
LOOKBACK_DAYS = 150  # Need 120+ for MA120
MAX_GAIN_FROM_LOW_PCT = 50.0  # Skip stocks already up >50% from 120d low
MA_PROXIMITY_MAX_PCT = 15.0   # Price must be within 15% above MA20 (not overextended)
ADX_MIN = 20   # Minimum ADX for trending
ADX_MAX = 50   # Maximum ADX (avoid exhausted trends)


def calculate_ma(close: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average."""
    return close.rolling(window=period).mean()


def calculate_ema(close: pd.Series, period: int) -> pd.Series:
    """Calculate Exponential Moving Average."""
    return close.ewm(span=period, adjust=False).mean()


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> dict:
    """
    Calculate ADX (Average Directional Index) with +DI and -DI.
    
    Returns:
        dict with 'adx', 'plus_di', 'minus_di' as Series
    """
    # True Range
    high_low = high - low
    high_close = np.abs(high - close.shift(1))
    low_close = np.abs(low - close.shift(1))
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)
    
    # Directional Movement
    up_move = high - high.shift(1)
    down_move = low.shift(1) - low
    
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=close.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=close.index)
    
    # Smooth using EMA
    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)
    
    # DX and ADX
    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = dx.ewm(span=period, adjust=False).mean()
    
    return {
        'adx': adx,
        'plus_di': plus_di,
        'minus_di': minus_di
    }


def calculate_slope(series: pd.Series, window: int = 10) -> pd.Series:
    """Calculate slope (rate of change) over a rolling window."""
    return (series - series.shift(window)) / series.shift(window) * 100


def analyze_stock_longup(ts_code: str, df: pd.DataFrame,
                         index_returns_20d: float = 0.0) -> Optional[dict]:
    """
    Analyze a single stock for early-stage long-term uptrend signals.
    
    Args:
        ts_code: Stock code
        df: Pre-fetched OHLCV DataFrame
        index_returns_20d: CSI300 returns over last 20 days (for relative strength)
    
    Returns:
        dict with analysis results or None
    """
    try:
        if df is None or df.empty or len(df) < 80:
            return None
        
        # Sort by date ascending
        df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
        
        close = df['close'].astype(float)
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        vol = df['vol'].astype(float)
        
        # Need at least 120 bars for MA120
        if len(close) < 120:
            # Fallback: use shorter MAs if we have 60+ bars
            if len(close) < 60:
                return None
            ma_long = calculate_ma(close, 60)
            ma_mid = calculate_ma(close, 30)
            ma_short = calculate_ma(close, 10)
            ma_label = "MA10/MA30/MA60"
        else:
            ma_long = calculate_ma(close, 120)
            ma_mid = calculate_ma(close, 60)
            ma_short = calculate_ma(close, 20)
            ma_label = "MA20/MA60/MA120"
        
        latest = close.iloc[-1]
        latest_ma_short = ma_short.iloc[-1]
        latest_ma_mid = ma_mid.iloc[-1]
        latest_ma_long = ma_long.iloc[-1]
        
        if pd.isna(latest_ma_short) or pd.isna(latest_ma_mid) or pd.isna(latest_ma_long):
            return None
        
        # ─── QUICK FILTERS (reject early) ───
        
        # Skip if price below MA_long (not in uptrend territory)
        if latest < latest_ma_long * 0.98:
            return None
        
        # Skip stocks already up >50% from 120-day low
        low_120d = low.tail(120).min() if len(low) >= 120 else low.min()
        gain_from_low = (latest - low_120d) / low_120d * 100
        if gain_from_low > MAX_GAIN_FROM_LOW_PCT:
            return None
        
        # ─── SCORING SYSTEM (0-100) ───
        score = 0.0
        signals = []
        
        # 1. MA ALIGNMENT (25 pts): MA_short > MA_mid > MA_long + all trending up
        ma_aligned = (latest_ma_short > latest_ma_mid > latest_ma_long)
        
        # MA slopes (positive = trending up)
        slope_short = calculate_slope(ma_short, 10).iloc[-1]
        slope_mid = calculate_slope(ma_mid, 10).iloc[-1]
        slope_long = calculate_slope(ma_long, 10).iloc[-1]
        
        if pd.isna(slope_short) or pd.isna(slope_mid) or pd.isna(slope_long):
            return None
        
        all_slopes_up = (slope_short > 0 and slope_mid > 0 and slope_long > 0)
        
        if ma_aligned and all_slopes_up:
            score += 25.0
            signals.append(f"MA_aligned_up({ma_label})")
        elif ma_aligned:
            score += 15.0
            signals.append(f"MA_aligned({ma_label})")
        elif latest > latest_ma_mid and slope_mid > 0:
            score += 8.0
            signals.append("MA_partial_align")
        
        # 2. PRICE POSITION (20 pts): Above MAs but not overextended
        price_above_all_ma = (latest > latest_ma_short > latest_ma_mid)
        price_distance_from_ma20 = (latest - latest_ma_short) / latest_ma_short * 100
        
        if price_above_all_ma:
            if 0 <= price_distance_from_ma20 <= 5:
                # Sweet spot: just above MA20, close support
                score += 20.0
                signals.append(f"price_near_MA20({price_distance_from_ma20:.1f}%)")
            elif 5 < price_distance_from_ma20 <= MA_PROXIMITY_MAX_PCT:
                score += 12.0
                signals.append(f"price_above_MA20({price_distance_from_ma20:.1f}%)")
            # If > MA_PROXIMITY_MAX_PCT, overextended, no points
        elif latest > latest_ma_mid:
            score += 5.0
            signals.append("price_above_MA60")
        
        # 3. VOLUME PATTERN (20 pts): Expanding volume from consolidation
        if len(vol) >= 60:
            vol_5d = vol.tail(5).mean()
            vol_20d = vol.tail(20).mean()
            vol_60d = vol.tail(60).mean()
            
            # Recent volume expansion vs 20d average
            vol_ratio_short = vol_5d / vol_20d if vol_20d > 0 else 1.0
            # 20d volume vs 60d average (is volume trending up?)
            vol_ratio_trend = vol_20d / vol_60d if vol_60d > 0 else 1.0
            
            if vol_ratio_short > 1.3 and vol_ratio_trend > 1.1:
                score += 20.0
                signals.append(f"vol_expanding(5d/20d={vol_ratio_short:.2f},20d/60d={vol_ratio_trend:.2f})")
            elif vol_ratio_short > 1.1 and vol_ratio_trend > 1.0:
                score += 12.0
                signals.append(f"vol_moderate_up(5d/20d={vol_ratio_short:.2f})")
            elif vol_ratio_trend > 1.0:
                score += 5.0
                signals.append("vol_trend_up")
        
        # 4. ADX TREND STRENGTH (15 pts)
        adx_data = calculate_adx(high, low, close, period=14)
        latest_adx = adx_data['adx'].iloc[-1]
        latest_plus_di = adx_data['plus_di'].iloc[-1]
        latest_minus_di = adx_data['minus_di'].iloc[-1]
        
        if not pd.isna(latest_adx):
            # ADX rising from low level = new trend forming
            adx_10d_ago = adx_data['adx'].iloc[-10] if len(adx_data['adx']) >= 10 else latest_adx
            adx_rising = latest_adx > adx_10d_ago
            di_bullish = latest_plus_di > latest_minus_di
            
            if ADX_MIN <= latest_adx <= ADX_MAX and adx_rising and di_bullish:
                score += 15.0
                signals.append(f"ADX_rising({latest_adx:.1f},+DI>{int(latest_plus_di)},-DI={int(latest_minus_di)})")
            elif di_bullish and latest_adx > 15:
                score += 8.0
                signals.append(f"DI_bullish(ADX={latest_adx:.1f})")
        
        # 5. PULLBACK-RECOVERY (10 pts): Recent bounce off MA20
        if len(close) >= 20:
            # Check if price touched MA20 in last 10 days and bounced
            low.tail(10).min()
            close.tail(10)
            ma_short.tail(10)
            
            # Check if any recent low touched MA20 (within 2%) 
            touched_ma20 = False
            for i in range(-10, 0):
                if len(close) + i >= 0 and not pd.isna(ma_short.iloc[i]):
                    distance_to_ma20 = abs(low.iloc[i] - ma_short.iloc[i]) / ma_short.iloc[i] * 100
                    if distance_to_ma20 < 2.0 and close.iloc[-1] > ma_short.iloc[-1]:
                        touched_ma20 = True
                        break
            
            if touched_ma20:
                score += 10.0
                signals.append("pullback_bounce_MA20")
            else:
                # Alternative: breaking above recent consolidation range
                if len(close) >= 30:
                    range_20d_high = high.iloc[-30:-5].max()
                    if latest > range_20d_high and latest < range_20d_high * 1.05:
                        score += 7.0
                        signals.append("breakout_consolidation")
        
        # 6. RELATIVE STRENGTH (10 pts): Outperforming CSI300
        if len(close) >= 20:
            stock_returns_20d = (close.iloc[-1] / close.iloc[-20] - 1) * 100
            excess_return = stock_returns_20d - index_returns_20d
            
            if excess_return > 5:
                score += 10.0
                signals.append(f"rel_strength(+{excess_return:.1f}%)")
            elif excess_return > 2:
                score += 5.0
                signals.append(f"moderate_rel_str(+{excess_return:.1f}%)")
        
        # ─── Minimum score threshold ───
        if score < 30:
            return None
        
        return {
            'ts_code': ts_code,
            'close': latest,
            'ma_short': latest_ma_short,
            'ma_mid': latest_ma_mid,
            'ma_long': latest_ma_long,
            'ma_aligned': ma_aligned,
            'all_slopes_up': all_slopes_up,
            'adx': latest_adx if not pd.isna(latest_adx) else 0,
            'plus_di': latest_plus_di if not pd.isna(latest_plus_di) else 0,
            'minus_di': latest_minus_di if not pd.isna(latest_minus_di) else 0,
            'gain_from_low_pct': round(gain_from_low, 2),
            'price_dist_ma20': round(price_distance_from_ma20, 2),
            'composite_score': round(score, 2),
            'signals': signals,
        }
        
    except Exception as e:
        logger.warning(f"Error analyzing {ts_code}: {e}")
        return None


def get_index_returns(end_date: str, period: int = 20) -> float:
    """Get CSI300 index returns over the given period."""
    try:
        start_date = get_trading_days_before(end_date, period + 5)
        df = data_provider.get_index_data('000300.SH', start_date, end_date)
        if df is not None and not df.empty:
            df = df.sort_values('trade_date', ascending=True)
            if len(df) >= period:
                return (df['close'].iloc[-1] / df['close'].iloc[-period] - 1) * 100
    except Exception as e:
        logger.warning(f"Failed to get index returns: {e}")
    return 0.0


def get_vol_turnover_regime(end_date: str, index_code: str = '000905.SH') -> dict:
    """
    Calculate the market regime based on Volatility and Turnover Rate vs a 1-year baseline.
    Returns dict with regime, volatility, turnover, and baseline stats.
    Quadrants:
    - Bull (牛市): Vol > Base, Turn > Base
    - Bear (熊市): Vol > Base, Turn < Base
    - Upward (上升市): Vol < Base, Turn > Base
    - Volatile (震荡市): Vol < Base, Turn < Base
    """
    try:
        # 1. Calculate Baselines (1 year / 252 trading days)
        base_start = get_trading_days_before(end_date, 252)
        
        # Base OHLC for Volatility
        df_base_ohlc = data_provider.get_index_data(index_code, base_start, end_date)
        if df_base_ohlc is None or len(df_base_ohlc) < 50:
            logger.warning(f"Not enough index data for baseline vol calculation for {index_code}")
            return {"regime": "unknown", "vol": 0, "turn": 0, "base_vol": 0, "base_turn": 0}
            
        df_base_ohlc = df_base_ohlc.sort_values('trade_date').reset_index(drop=True)
        base_vol = df_base_ohlc['close'].pct_change().std() * 100 * np.sqrt(252)
        
        # Base Turnover
        df_base_basic = data_provider.pro.index_dailybasic(ts_code=index_code, start_date=base_start.replace('-', ''), end_date=end_date.replace('-', ''))
        if df_base_basic.empty or 'turnover_rate' not in df_base_basic.columns:
            logger.warning(f"No turnover data for baseline calculation for {index_code}")
            return {"regime": "unknown", "vol": 0, "turn": 0, "base_vol": 0, "base_turn": 0}
            
        base_turn = df_base_basic['turnover_rate'].mean()
        
        # 2. Calculate Current Metrics (20 days)
        curr_start = get_trading_days_before(end_date, 20)
        
        # Current OHLC for Volatility
        df_curr_ohlc = data_provider.get_index_data(index_code, curr_start, end_date)
        df_curr_ohlc = df_curr_ohlc.sort_values('trade_date').reset_index(drop=True)
        curr_vol = df_curr_ohlc['close'].pct_change().std() * 100 * np.sqrt(252)
        
        # Current Turnover
        df_curr_basic = data_provider.pro.index_dailybasic(ts_code=index_code, start_date=curr_start.replace('-', ''), end_date=end_date.replace('-', ''))
        curr_turn = df_curr_basic['turnover_rate'].mean()
        
        # 3. Determine Quadrant
        vol_up = curr_vol > base_vol
        turn_up = curr_turn > base_turn
        
        if vol_up and turn_up:
            regime = "bull"
        elif vol_up and not turn_up:
            regime = "bear"
        elif not vol_up and turn_up:
            regime = "upward"
        else:
            regime = "volatile"
            
        return {
            "regime": regime,
            "vol": curr_vol,
            "turn": curr_turn,
            "base_vol": base_vol,
            "base_turn": base_turn
        }
        
    except Exception as e:
        logger.error(f"Error calculating Vol/Turnover regime: {e}")
        return {"regime": "unknown", "vol": 0, "turn": 0, "base_vol": 0, "base_turn": 0}


def pick_longup_stocks(end_date: str, max_picks: int = 10) -> pd.DataFrame:
    """
    Pick stocks showing early-stage long-term uptrend signals.
    
    Args:
        end_date: Reference date for analysis (YYYYMMDD)
        max_picks: Maximum number of stocks to return
    
    Returns:
        DataFrame with picked stocks
    """
    logger.info(f"[ts_longup] Starting long-term uptrend stock picking for {end_date}")
    
    # Get stock universe
    stock_basic = data_provider.get_basic_information_api()
    if stock_basic.empty:
        raise ValueError("No basic stock information found")
    
    # Filter to mainboard only
    total_stocks = len(stock_basic)
    risky_free_list = no_risky_stocks(stock_basic)
    stock_basic = stock_basic[stock_basic['ts_code'].isin(risky_free_list)].reset_index(drop=True)
    logger.info(f"[ts_longup] Universe: {total_stocks} total -> {len(stock_basic)} mainboard stocks")
    
    # --- Advanced Volatility/Turnover Regime Detection ---
    # Use CSI500 (broader market representation for breakouts)
    vt_regime_data = get_vol_turnover_regime(end_date, index_code='000905.SH')
    vt_regime = vt_regime_data['regime']
    logger.info(f"[ts_longup] Vol/Turn Regime: {vt_regime.upper()} "
                f"(Vol: {vt_regime_data['vol']:.1f} vs Base {vt_regime_data['base_vol']:.1f}, "
                f"Turn: {vt_regime_data['turn']:.2f} vs Base {vt_regime_data['base_turn']:.2f})")
    
    # Fast-fail for Bear Market
    if vt_regime == 'bear':
        logger.warning("[ts_longup] 🛑 CIRCUIT BREAKER TRIGGERED: Bear Market detected (High Volatility + Shrinking Turnover). Picking 0 stocks to protect capital.")
        return pd.DataFrame()

    # Dynamic scaling based on Vol/Turnover quadrant
    if vt_regime == 'bull':
        # High liquidity, safe to maximize picks
        max_picks = 12
        min_score = 40
    elif vt_regime == 'upward':
        # Steady market, solid trends
        max_picks = 8
        min_score = 45
    elif vt_regime == 'volatile':
        # Shrinking liquidity, fake breakouts likely. Extreme caution.
        max_picks = 3
        min_score = 55
    else:
        # Fallback (unknown)
        max_picks = 5
        min_score = 50

    logger.info(f"[ts_longup] Applied Adaptive Constraints -> Max Picks: {max_picks}, Min Score: {min_score}")
    
    # Legacy regime fetch for fallback dependencies (like late_trend_filter)
    detect_market_regime(end_date)
    
    # Get index returns for relative strength calculation
    index_returns_20d = get_index_returns(end_date, 20)
    logger.info(f"[ts_longup] CSI300 20d return: {index_returns_20d:.2f}%")
    
    # === BULK DATA FETCH ===
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS)
    logger.info(f"[ts_longup] Bulk fetching OHLCV data from {start_date} to {end_date}...")
    all_stock_data = data_provider.get_bulk_ohlcv_by_date_range(start_date, end_date)
    logger.info(f"[ts_longup] Bulk fetch complete: {len(all_stock_data)} stocks with data")
    
    # Analyze all stocks
    results = []
    total = len(stock_basic)
    analyzed = 0
    
    for idx, row in stock_basic.iterrows():
        if idx % 500 == 0:
            logger.info(f"[ts_longup] Analyzing stocks: {idx}/{total}")
        
        ts_code = row['ts_code']
        stock_df = all_stock_data.get(ts_code)
        
        if stock_df is not None:
            analysis = analyze_stock_longup(ts_code, stock_df, index_returns_20d)
            if analysis:
                analysis['name'] = row['name']
                results.append(analysis)
                analyzed += 1
    
    logger.info(f"[ts_longup] Analyzed {analyzed} stocks passed initial filters")
    
    if not results:
        logger.warning("[ts_longup] No stocks passed the analysis criteria")
        return pd.DataFrame()
    
    # Create DataFrame
    df = pd.DataFrame(results)
    
    # Sort by composite score descending
    df = df.sort_values('composite_score', ascending=False)
    
    before_filter = len(df)
    df = df[df['composite_score'] >= min_score]
    logger.info(f"[ts_longup] Score filter (>={min_score}): {before_filter} -> {len(df)} stocks")
    
    if len(df) == 0 and before_filter > 0:
        logger.warning(f"[ts_longup] Min score {min_score} filtered all stocks. Picking 0 stocks.")
    
    # === Late-trend filter moved to backtest_orders.py centrally ===
    # Evaluate priority FIRST across the whole passing universe
    # Prefer stocks with MA alignment + slopes up (strongest signal)
    if 'ma_aligned' in df.columns and 'all_slopes_up' in df.columns:
        df['priority'] = df.apply(
            lambda r: 2 if (r.get('ma_aligned') and r.get('all_slopes_up')) 
                      else (1 if r.get('ma_aligned') else 0), axis=1
        )
        df = df.sort_values(['priority', 'composite_score'], ascending=[False, False])
        df = df.drop(columns=['priority'])
    
    # Limit to max picks
    df = df.head(max_picks)
    
    # Add rank
    df = df.reset_index(drop=True)
    df['rank'] = range(1, len(df) + 1)
    
    logger.info(f"[ts_longup] Selected {len(df)} stocks for vt_regime={vt_regime}")
    
    # Log top picks
    for _, row in df.head(8).iterrows():
        signals_str = ', '.join(row.get('signals', [])[:3])
        logger.info(
            f"[ts_longup] #{row['rank']}: {row['name']}({row['ts_code']}) "
            f"score={row['composite_score']:.1f} ADX={row.get('adx', 0):.0f} "
            f"gain_from_low={row.get('gain_from_low_pct', 0):.0f}% "
            f"| {signals_str}"
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
    
    logger.info(f"[ts_longup] Picking stocks for target date {target_date} with reference date: {date}")
    
    df = pick_longup_stocks(end_date=date)
    
    # Output to standard format
    output_file = '/tmp/tmp'
    selected_stocks = []
    
    for _, stock in df.iterrows():
        selected_stocks.append({
            'rank': int(stock['rank']),
            'symbol': stock['ts_code'],
            'name': stock.get('name', ''),
            'score': float(stock['composite_score']),
        })
    
    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)
    
    logger.info(f"[ts_longup] Saved {len(selected_stocks)} picked stocks to {output_file}")
