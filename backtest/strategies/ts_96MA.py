"""
ts_96MA: 96-Moving-Average Strategy (黄金生命线)

Based on two articles on the 96-movement-average technique:
1. Toutiao: "精准预判趋势方向：96均线的实战拆解与应用"
   - MA96 = MA(CLOSE, 96) ≈ 5 months of trading days
   - Price above MA96 = strong regime (buy pullbacks); below = weak (stay out)
   - MA96 slope up/flat = trend confirmation; volume expansion on bounce
   - Pullback to MA96 with volume contraction = entry zone
   - Stop-loss below MA96 (~2-3%)
   - 24MA/96MA golden cross = extra confirmation

2. Zhihu: "60、120均线人人皆知，不起眼的96均线，凭什么4个月抹平所有亏空"
   - 96MA avoids the crowded 60/120 consensus → fewer fake breakouts
   - Sits between 60MA (too sensitive) and 120MA (too slow)
   - 60MA crossing above 96MA + 96MA flattening up = stronger than simple golden cross
   - After price stands above 96MA, the key test is the pullback:
     volume must shrink, price must hold at MA96
   - If price quickly falls back below with a long bearish candle = fake breakout
   - 96MA slope is critical: don't buy when slope is still downward

Strategy signals (scored 0-100):
  1. Regime filter: close > MA96 (mandatory)
  2. MA96 slope >= +0.3% (was -0.5% — halved stop-loss rate from 55%)
  3. MA60 slope >= 0.0% (both timeframes must agree)
  4. Pullback proximity: price within 1-5% above MA96 (sweet spot)
  5. Chasing guard: reject if >15% above MA96
  6. Fake breakout guard: reject if 1-3 days above MA96 then long bearish candle below
  7. Short-term bounce: ≥2 of last 3 closes > open (buying pressure filter)
  8. 60MA > 96MA: recent cross (15 pts) vs persistent state (8 pts)
  9. Volume contraction on pullback (5d vol < 20d vol)
 10. Volume expansion on recent bounce (latest day vol > 5d avg)
 11. 24MA > 96MA: recent cross (10 pts) vs persistent state (5 pts)
 12. Bounce strength (10 pts): 3/3 up = 10, 2/3 up = 6
 13. ADX > 20 (trending, not choppy)
 14. Relative strength vs CSI300 index
 15. Stop-loss exported: 2.5% below MA96
 16. CSI500 20d return exposure scaling: >2%=12picks, 0-2%=8, <0%=3

Usage:
    python backtest/strategies/ts_96MA.py YYYYMMDD [--lookahead]
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

# ─── Strategy Parameters ───────────────────────────────────────────
MA96_PERIOD = 96          # The core 96-day moving average
MA60_PERIOD = 60          # Medium-term MA for cross detection
MA24_PERIOD = 24          # Short-term MA for cross confirmation
LOOKBACK_DAYS = 160        # 96 for MA + ~60 buffer for slope/ADX (need >= 96 + buffer)
SLOPE_WINDOW = 5           # Days to measure MA96/MA60 slope
MA96_MIN_SLOPE = 0.3       # MA96 must be rising ≥ 0.3% (was -0.5% — too permissive)
MA60_MIN_SLOPE = 0.0       # MA60 must also be rising (not declining)
MAX_ABOVE_MA96_PCT = 15.0  # Reject if price > 15% above MA96 (chasing guard)
MIN_ABOVE_MA96_PCT = 0.0   # Must be above MA96
PULLBACK_ZONE_PCT = 5.0     # Sweet spot: within 5% above MA96
VOLUME_CONTRACTION_RATIO = 0.9  # 5d vol / 20d vol < this = contraction
ADX_MIN = 20              # Minimum ADX for trend confirmation
MIN_SCORE_THRESHOLD = 50  # Must pass this in analyze_stock; aligned with regime min_score (was 40)


def calculate_ma(close: pd.Series, period: int) -> pd.Series:
    """Calculate Simple Moving Average."""
    return close.rolling(window=period).mean()


def calculate_slope(series: pd.Series, window: int = 5) -> float:
    """Calculate slope (pct change) over a rolling window at the latest point."""
    if len(series) < window + 1:
        return np.nan
    prev = series.iloc[-1 - window]
    curr = series.iloc[-1]
    if pd.isna(prev) or pd.isna(curr) or prev == 0:
        return np.nan
    return (curr - prev) / prev * 100


def calculate_adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> dict:
    """Calculate ADX (Average Directional Index) with +DI and -DI."""
    high_low = high - low
    high_close = np.abs(high - close.shift(1))
    low_close = np.abs(low - close.shift(1))
    tr = pd.concat([high_low, high_close, low_close], axis=1).max(axis=1)

    up_move = high - high.shift(1)
    down_move = low.shift(1) - low

    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=close.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=close.index)

    atr = tr.ewm(span=period, adjust=False).mean()
    plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
    minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)

    dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
    adx = dx.ewm(span=period, adjust=False).mean()

    return {'adx': adx, 'plus_di': plus_di, 'minus_di': minus_di}


def detect_recent_cross(fast_ma: pd.Series, slow_ma: pd.Series, lookback: int = 10) -> tuple[bool, int]:
    """
    Detect if fast MA crossed above slow MA within the last N days.
    
    Returns:
        (crossed_recently, days_ago): True if cross happened within lookback days
    """
    if len(fast_ma) < lookback + 2 or len(slow_ma) < lookback + 2:
        return False, -1
    
    # Check each day in the lookback window for a cross
    for i in range(-1, -lookback - 1, -1):
        if pd.isna(fast_ma.iloc[i]) or pd.isna(slow_ma.iloc[i]):
            continue
        if pd.isna(fast_ma.iloc[i - 1]) or pd.isna(slow_ma.iloc[i - 1]):
            continue
        # Cross: fast was below slow, now above
        if fast_ma.iloc[i - 1] <= slow_ma.iloc[i - 1] and fast_ma.iloc[i] > slow_ma.iloc[i]:
            return True, abs(i)
    
    return False, -1


def detect_fake_breakout(close: pd.Series, ma96: pd.Series, lookback: int = 10) -> bool:
    """
    Detect fake breakout: price broke above MA96 but fell back below with a long bearish candle.
    
    Article 2: "如果站上去没两天就一根长阴砸穿，那这次突破大概率是虚晃一枪"
    
    Returns:
        True if fake breakout detected (should reject)
    """
    if len(close) < lookback + 5:
        return False
    
    # Find the most recent day where close was above MA96
    above_days = 0
    for i in range(-1, -lookback - 1, -1):
        if pd.isna(ma96.iloc[i]):
            break
        if close.iloc[i] > ma96.iloc[i]:
            above_days += 1
        else:
            break
    
    # If price was above MA96 for 1-3 days, then fell below with a bearish candle
    if 1 <= above_days <= 3:
        # Check if the breakdown day had a long bearish candle
        breakdown_idx = -above_days - 1
        if abs(breakdown_idx) < len(close):
            day_open = close.iloc[breakdown_idx - 1] if breakdown_idx - 1 >= -len(close) else close.iloc[breakdown_idx]
            day_close = close.iloc[breakdown_idx]
            # Long bearish candle: close drops >3% from open
            if day_close < day_open * 0.97:
                return True
    
    return False


def get_index_returns(end_date: str, period: int = 20) -> float:
    """Get CSI300 index returns over the given period for relative strength."""
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


def analyze_stock_96mv(ts_code: str, df: pd.DataFrame,
                       index_returns_20d: float = 0.0) -> Optional[dict]:
    """
    Analyze a single stock using the 96-MA strategy.

    Args:
        ts_code: Stock code
        df: Pre-fetched OHLCV DataFrame
        index_returns_20d: CSI300 20-day returns for relative strength

    Returns:
        dict with analysis results or None if filtered out
    """
    try:
        if df is None or df.empty or len(df) < 100:
            return None

        # Sort by date ascending
        df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)

        close = df['close'].astype(float)
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        vol = df['vol'].astype(float)

        # Need at least MA96_PERIOD bars for MA96
        if len(close) < MA96_PERIOD:
            return None

        # ─── Calculate Indicators ───
        ma96 = calculate_ma(close, MA96_PERIOD)
        ma60 = calculate_ma(close, MA60_PERIOD)
        ma24 = calculate_ma(close, MA24_PERIOD)

        latest_close = close.iloc[-1]
        latest_ma96 = ma96.iloc[-1]
        latest_ma60 = ma60.iloc[-1]
        latest_ma24 = ma24.iloc[-1]

        # NaN check — MA96 must be valid
        if pd.isna(latest_ma96):
            return None

        # ─── HARD FILTER 1: Price must be above MA96 (regime filter) ───
        # Article: "股价运行于96日均线上方，定义为强势格局"
        if latest_close < latest_ma96:
            return None

        # ─── HARD FILTER 2: Chasing guard — reject if >15% above MA96 ───
        pct_above_ma96 = (latest_close - latest_ma96) / latest_ma96 * 100
        if pct_above_ma96 > MAX_ABOVE_MA96_PCT:
            return None

        # ─── MA96 Slope (5-day) ───
        ma96_slope = calculate_slope(ma96, SLOPE_WINDOW)
        if pd.isna(ma96_slope):
            return None

        # ─── HARD FILTER 3: MA96 slope must be rising (≥ +0.3%) ───
        # Was ≥ -0.5% — too permissive; stocks with flat/declining MA96
        # accounted for 55% of stop-loss hits in backtesting.
        if ma96_slope < MA96_MIN_SLOPE:
            return None

        # ─── HARD FILTER 4: MA60 must also be rising (≥ 0.0%) ───
        # Article: "60日代表短期群体情绪，96日代表中期资金态度，两者形成共振"
        # If MA60 is declining, the short-term trend is counter to the medium.
        ma60_slope = calculate_slope(ma60, SLOPE_WINDOW) if not pd.isna(latest_ma60) else None
        if ma60_slope is None or pd.isna(ma60_slope):
            return None
        if ma60_slope < MA60_MIN_SLOPE:
            return None

        # ─── HARD FILTER 5: Fake breakout detection ───
        # Article 2: "如果站上去没两天就一根长阴砸穿，那这次突破大概率是虚晃一枪"
        if detect_fake_breakout(close, ma96):
            return None

        # ─── HARD FILTER 6: Short-term bounce confirmation ───
        # Require ≥2 of last 3 daily closes above open (buying pressure visible).
        # Without this, 55% of picks hit stop-loss because support didn't hold.
        open_prices = df['open'].astype(float)
        if len(close) < 4 or len(open_prices) < 4:
            return None
        up_close_count = 0
        for i in range(-1, -4, -1):
            if pd.isna(close.iloc[i]) or pd.isna(open_prices.iloc[i]):
                return None
            if close.iloc[i] > open_prices.iloc[i]:
                up_close_count += 1
        if up_close_count < 2:
            return None

        # ─── Scoring System (0-100) ───
        score = 0.0
        signals = []

        # 1. MA96 Slope (25 pts): rising = bullish
        # Article 2: "只看价格是否站上均线，不看均线自身的方向，等于只看了一半"
        if ma96_slope > 1.0:
            score += 25.0
            signals.append(f"MA96_slope_up({ma96_slope:.2f}%)")
        elif ma96_slope > 0.5:
            score += 18.0
            signals.append(f"MA96_slope_rising({ma96_slope:.2f}%)")
        else:
            score += 10.0
            signals.append(f"MA96_slope_min({ma96_slope:.2f}%)")

        # 2. Pullback Proximity (20 pts): within 5% of MA96 = sweet spot
        # Article: "每次股价回调至96日均线附近不破，都是检验支撑的有效买点"
        if pct_above_ma96 <= PULLBACK_ZONE_PCT:
            if pct_above_ma96 <= 2.0:
                score += 20.0
                signals.append(f"pullback_near_MA96({pct_above_ma96:.1f}%)")
            else:
                score += 12.0
                signals.append(f"pullback_zone({pct_above_ma96:.1f}%)")
        # 5-15% above MA96: no consolation points — too far from pullback zone

        # 3. 60MA / 96MA Cross (15 pts): recent cross = strongest signal
        # Article 2: "60日均线上穿96日均线，同时96日均线本身开始走平上翘"
        if not pd.isna(latest_ma60) and latest_ma60 > latest_ma96:
            cross_recently, cross_days_ago = detect_recent_cross(ma60, ma96, lookback=20)
            if cross_recently:
                score += 15.0
                signals.append(f"MA60>MA96_cross_{cross_days_ago}d_ago")
            else:
                score += 8.0
                signals.append("MA60>MA96_above")

        # 4. 24MA / 96MA Cross (10 pts): recent cross = strongest signal
        # Article 1: "24日线上穿96日线形成金叉"
        if not pd.isna(latest_ma24) and latest_ma24 > latest_ma96:
            cross_recently, cross_days_ago = detect_recent_cross(ma24, ma96, lookback=10)
            if cross_recently:
                score += 10.0
                signals.append(f"MA24>MA96_cross_{cross_days_ago}d_ago")
            else:
                score += 5.0
                signals.append("MA24>MA96_above")

        # 5. Bounce Confirmation (10 pts): ≥2 of last 3 closes above open
        # Article: "反弹时量能放大" — buying pressure visible on daily bars
        if up_close_count >= 3:
            score += 10.0
            signals.append("strong_bounce(3/3_up)")
        elif up_close_count >= 2:
            score += 6.0
            signals.append(f"bounce({up_close_count}/3_up)")

        # 6. Volume Contraction on Pullback (15 pts)
        # Article: "回踩时成交量明显收窄" + "股价在96日线附近稳稳守住"
        if len(vol) >= 20:
            vol_5d = vol.tail(5).mean()
            vol_20d = vol.tail(20).mean()
            vol_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0

            if vol_ratio < VOLUME_CONTRACTION_RATIO:
                # Volume contraction = healthy pullback
                score += 10.0
                signals.append(f"vol_contraction(5d/20d={vol_ratio:.2f})")

            # Latest day volume expansion (bounce signal)
            latest_vol = vol.iloc[-1]
            vol_5d_avg = vol.tail(5).mean()
            if latest_vol > vol_5d_avg * 1.2 and latest_close > close.iloc[-2]:
                score += 5.0
                signals.append("vol_bounce_latest")

        # 7. ADX Trend Strength (10 pts)
        # Article: "96均线...趋势强弱分水岭" — use ADX to confirm trend
        adx_data = calculate_adx(high, low, close, period=14)
        latest_adx = adx_data['adx'].iloc[-1]
        latest_plus_di = adx_data['plus_di'].iloc[-1]
        latest_minus_di = adx_data['minus_di'].iloc[-1]

        if not pd.isna(latest_adx):
            di_bullish = latest_plus_di > latest_minus_di
            if latest_adx >= ADX_MIN and di_bullish:
                score += 10.0
                signals.append(f"ADX_trending({latest_adx:.1f},+DI>-DI)")
            # ADX < 20 or DI not bullish: no consolation points

        # 7. Relative Strength vs CSI300 (5 pts)
        if len(close) >= 20:
            stock_returns_20d = (close.iloc[-1] / close.iloc[-20] - 1) * 100
            excess_return = stock_returns_20d - index_returns_20d
            if excess_return > 5:
                score += 5.0
                signals.append(f"rel_strength(+{excess_return:.1f}%)")
            # Moderate or negative excess return: no consolation points

        # ─── Stop-loss: 2-3% below MA96 (Article 1) ───
        stop_loss_price = round(latest_ma96 * 0.975, 2)  # 2.5% below MA96
        stop_loss_pct = round((latest_close - stop_loss_price) / latest_close * 100, 2)

        # ─── Minimum score threshold ───
        if score < MIN_SCORE_THRESHOLD:
            return None

        return {
            'ts_code': ts_code,
            'close': latest_close,
            'ma96': round(latest_ma96, 2),
            'ma60': round(latest_ma60, 2) if not pd.isna(latest_ma60) else 0,
            'ma24': round(latest_ma24, 2) if not pd.isna(latest_ma24) else 0,
            'ma96_slope': round(ma96_slope, 3),
            'ma60_slope': round(ma60_slope, 3),
            'pct_above_ma96': round(pct_above_ma96, 2),
            'adx': round(latest_adx, 1) if not pd.isna(latest_adx) else 0,
            'stop_loss_price': stop_loss_price,
            'stop_loss_pct': stop_loss_pct,
            'composite_score': round(score, 2),
            'signals': signals,
        }

    except Exception as e:
        logger.warning(f"Error analyzing {ts_code}: {e}")
        return None


def pick_96mv_stocks(end_date: str, max_picks: int = 10) -> pd.DataFrame:
    """
    Pick stocks using the 96-MA strategy.

    Args:
        end_date: Reference date for analysis (YYYYMMDD)
        max_picks: Maximum number of stocks to return

    Returns:
        DataFrame with picked stocks
    """
    logger.info(f"[ts_96MA] Starting 96-MA stock picking for {end_date}")

    # Get stock universe
    stock_basic = data_provider.get_basic_information_api()
    if stock_basic.empty:
        raise ValueError("No basic stock information found")

    # Filter to mainboard only
    total_stocks = len(stock_basic)
    risky_free_list = no_risky_stocks(stock_basic)
    stock_basic = stock_basic[stock_basic['ts_code'].isin(risky_free_list)].reset_index(drop=True)
    logger.info(f"[ts_96MA] Universe: {total_stocks} total -> {len(stock_basic)} mainboard stocks")

    # Detect market regime for adaptive filtering
    regime_data = detect_market_regime(end_date)
    regime = regime_data.get('regime', 'normal')
    logger.info(f"[ts_96MA] Market Regime: {regime}")

    # Scale exposure by CSI500 20-day return (stronger signal than regime alone).
    # Backtesting showed -8.29% (Mar), -6.22% (May), -6.62% (Jul) when market
    # weakened but hadn't hit "bear" — reducing picks during declines closes
    # ~10% of the -13.6% gap to CSI500.
    try:
        csi500_start = get_trading_days_before(end_date, 25)
        csi500_df = data_provider.get_index_data('000905.SH', csi500_start, end_date)
        if csi500_df is not None and len(csi500_df) >= 20:
            csi500_df = csi500_df.sort_values('trade_date', ascending=True)
            csi500_ret_20d = (csi500_df['close'].iloc[-1] / csi500_df['close'].iloc[-20] - 1) * 100
        else:
            csi500_ret_20d = 0.0
    except Exception:
        csi500_ret_20d = 0.0

    logger.info(f"[ts_96MA] CSI500 20d return: {csi500_ret_20d:.2f}%")

    if regime == 'bear' or csi500_ret_20d < -3.0:
        logger.warning("[ts_96MA] 🛑 CIRCUIT BREAKER: "
                       f"regime={regime}, CSI500_20d={csi500_ret_20d:.2f}%. " 
                       "0 picks (capital protection).")
        return pd.DataFrame()

    # CSI500-based exposure scaling
    if csi500_ret_20d > 2.0:
        max_picks = 12
        min_score = 50
    elif csi500_ret_20d > 0.0:
        max_picks = 8
        min_score = 55
    else:  # 0% to -3%
        max_picks = 3
        min_score = 70

    logger.info(f"[ts_96MA] Adaptive (CSI500_20d={csi500_ret_20d:.1f}%): "
                f"max_picks={max_picks}, min_score={min_score}")

    # Get index returns for relative strength calculation
    index_returns_20d = get_index_returns(end_date, 20)
    logger.info(f"[ts_96MA] CSI300 20d return: {index_returns_20d:.2f}%")

    # === BULK DATA FETCH ===
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS)
    logger.info(f"[ts_96MA] Bulk fetching OHLCV data from {start_date} to {end_date}...")
    all_stock_data = data_provider.get_bulk_ohlcv_by_date_range(start_date, end_date)
    logger.info(f"[ts_96MA] Bulk fetch complete: {len(all_stock_data)} stocks with data")

    # Analyze all stocks
    results = []
    total = len(stock_basic)
    analyzed = 0

    for idx, row in stock_basic.iterrows():
        if idx % 500 == 0:
            logger.info(f"[ts_96MA] Analyzing stocks: {idx}/{total}")

        ts_code = row['ts_code']
        stock_df = all_stock_data.get(ts_code)

        if stock_df is not None:
            analysis = analyze_stock_96mv(ts_code, stock_df, index_returns_20d)
            if analysis:
                analysis['name'] = row['name']
                results.append(analysis)
                analyzed += 1

    logger.info(f"[ts_96MA] Analyzed {analyzed} stocks passed initial filters")

    if not results:
        logger.warning("[ts_96MA] No stocks passed the analysis criteria")
        return pd.DataFrame()

    # Create DataFrame and sort by composite score
    df = pd.DataFrame(results)
    df = df.sort_values('composite_score', ascending=False)

    # Apply score threshold
    before_filter = len(df)
    df = df[df['composite_score'] >= min_score]
    logger.info(f"[ts_96MA] Score filter (>={min_score}): {before_filter} -> {len(df)} stocks")

    if len(df) == 0 and before_filter > 0:
        logger.warning(f"[ts_96MA] Min score {min_score} filtered all stocks. Picking 0 stocks.")
        return pd.DataFrame()

    # Limit to max picks
    df = df.head(max_picks)

    # Add rank
    df = df.reset_index(drop=True)
    df['rank'] = range(1, len(df) + 1)

    logger.info(f"[ts_96MA] Selected {len(df)} stocks for regime={regime}")

    # Log top picks
    for _, row in df.head(8).iterrows():
        signals_str = ', '.join(row.get('signals', [])[:3])
        logger.info(
            f"[ts_96MA] #{row['rank']}: {row['name']}({row['ts_code']}) "
            f"score={row['composite_score']:.1f} MA96_slope={row.get('ma96_slope', 0):.2f}% "
            f"pct_above={row.get('pct_above_ma96', 0):.1f}% ADX={row.get('adx', 0):.0f} "
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

    logger.info(f"[ts_96MA] Picking stocks for target date {target_date} with reference date: {date}")

    df = pick_96mv_stocks(end_date=date)

    # Output to standard format
    output_file = '/tmp/tmp'
    selected_stocks = []

    if not df.empty:
        for _, stock in df.iterrows():
            selected_stocks.append({
                'rank': int(stock['rank']),
                'symbol': stock['ts_code'],
                'name': stock.get('name', ''),
                'score': float(stock['composite_score']),
            })

    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)

    logger.info(f"[ts_96MA] Saved {len(selected_stocks)} picked stocks to {output_file}")
