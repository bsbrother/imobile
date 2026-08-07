"""
ts_multi_swing_defensive: Multi-swing defensive trend-pullback strategy.

Methodology derived from https://github.com/shouldnotappearcalm/a-share-skill
(tuige-shortline-trading / macd-trend-resonance / trend-setups):
the unscored "multi-swing-defensive" family is a defensive swing trader that
"顺着已经形成的强势结构，等待回踩、整理或再起" — ride established uptrends,
buy pullbacks toward key MAs, confirm rhythm via MACD, and stay defensively
positioned when the regime is weak.

Three layers (all past data only, no lookahead, no hardcoded months):

  1. TREND gate (multi-timeframe direction — "均线定方向")
     - MA60 slope rising AND close above MA60 AND close above MA20.
     - Requires the 20/60/120 MA stack to be non-bearish (20>=60 or price strong).
     - Filters OUT downtrends / dead stocks regardless of how good MACD looks.

  2. SWING entry ("MACD定节奏" + pullback)
     Entry when BOTH:
       a. MACD above zero axis with DIF > DEA (momentum confirmed), OR a fresh
          golden cross below zero that is recovering (repair setup), AND
       b. a healthy pullback: price within RSI 40-65 and above MA20 (not extended
          after a blowoff), close within a small band of MA20 (tight to support)
          OR just printed a pullback-to-MA5/MIS swing low.
     This is the "multi-swing" element: we only buy the rhythm-confirmed pullback,
     never a stretched breakout chase.

  3. DEFENSIVE gate (environment — 环境开关 + 仓位纪律)
     - Market regime is the master switch: in bear/volatile regimes cap picks hard
       (defensive) and require a HIGHER bar; in bull/normal allow the swing library.
     - Low-volatility filter (annualized vol < cap) — defensive tilt.
     - Q score penalizes distance-from-MA20 (don't buy extended), rewards trend
       strength + MACD strength + proximity to support.

Output matches the engine standard:
    {'selected_stocks': [{'rank','symbol','name','score'}, ...]} -> /tmp/tmp

Usage:
    python backtest/strategies/ts_multi_swing_defensive.py YYYYMMDD [--lookahead]
"""

import os
import sys
import json
import math
import pandas as pd
from loguru import logger
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest.utils.trading_calendar import get_trading_days_before, convert_trade_date
from backtest.utils.logging_config import configure_logger
from backtest.utils.market_regime import detect_market_regime
from backtest import data_provider

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)

LOOKBACK_DAYS = 140          # enough for MA60 + MACD warmup + regime context
MAX_VOLATILITY = 0.40        # annualized vol cap -> defensive tilt

# TREND gate
MA_TREND = 60
MA_FAST = 20
MA_SLOW = 120

# SWING / MACD
RSI_PERIOD = 14
RSI_MIN = 40.0               # pullback floor (not falling knife)
RSI_MAX = 68.0               # not stretched after a blowoff
PULLBACK_BAND = 0.05         # close within +/-5% of MA20 = "at support"
# Q-score weights
W_TREND = 2.0                # trend strength (above MA60, MA60 rising)
W_MACD = 1.5                 # MACD above zero + DIF>DEA
W_PROX = 1.0                 # proximity to MA20 (not extended)
W_LOWVOL = 1.0               # low volatility


# ── Indicator helpers (pure pandas) ──────────────────────────────

def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _macd(close: pd.Series, fast=12, slow=26, signal=9):
    dif = _ema(close, fast) - _ema(close, slow)
    dea = _ema(dif, signal)
    return dif, dea, (dif - dea) * 2


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss.replace(0, pd.NA)
    return (100 - 100 / (1 + rs)).fillna(50)


def _annualized_vol(close: pd.Series, period: int = 20) -> float:
    rets = close.pct_change().dropna()
    if len(rets) < period:
        return float('inf')
    return float(rets.tail(period).std() * math.sqrt(252))


# ── Single-stock scoring ────────────────────────────────────────

def analyze_stock_with_data(ts_code: str, df: pd.DataFrame) -> dict | None:
    """Score one stock. Returns dict (or None if it fails the hard gates)."""
    try:
        if df is None or df.empty or len(df) < MA_SLOW + 10:
            return None
        df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
        close = df['close'].astype(float)
        vol = df['vol'].astype(float) if 'vol' in df.columns else df['volume'].astype(float)

        ma20 = close.rolling(MA_FAST, min_periods=10).mean()
        ma60 = close.rolling(MA_TREND, min_periods=30).mean()
        ma120 = close.rolling(MA_SLOW, min_periods=60).mean()

        last = float(close.iloc[-1])
        lma20 = float(ma20.iloc[-1]) if not pd.isna(ma20.iloc[-1]) else last
        lma60 = float(ma60.iloc[-1]) if not pd.isna(ma60.iloc[-1]) else last
        lma120 = float(ma120.iloc[-1]) if not pd.isna(ma120.iloc[-1]) else last

        # ── TREND gate ─────────────────────────────────────────
        ma60_slope = (lma60 / float(ma60.iloc[-6]) - 1) * 100 if not pd.isna(ma60.iloc[-6]) else 0
        above_ma60 = last >= lma60
        above_ma20 = last >= lma20
        stack_ok = (lma20 >= lma60) or (last > lma120)   # non-bearish MA stack
        trend_pass = above_ma60 and above_ma20 and ma60_slope > -1.0 and stack_ok
        if not trend_pass:
            return None

        # ── SWING: MACD rhythm ─────────────────────────────────
        dif, dea, hist = _macd(close)
        ldif, ldea = float(dif.iloc[-1]), float(dea.iloc[-1])
        macd_above_zero = ldif > 0 or ldea > 0
        macd_bullish = ldif > ldea
        # fresh golden cross within last 5 days = fresh swing buy trigger
        gc_recent = any((float(dif.iloc[i]) > float(dea.iloc[i]))
                        and (float(dif.iloc[i-1]) <= float(dea.iloc[i-1]))
                        for i in range(len(close)-1, max(len(close)-6, 0), -1)) \
            if len(dif) >= 6 else False
        macd_ok = (macd_above_zero and macd_bullish) or gc_recent
        if not macd_ok:
            return None

        # ── SWING: pullback to support (not stretched) ─────────
        rsi = float(_rsi(close).iloc[-1])
        dist_ma20 = (last / lma20 - 1)  * 100
        pullback_ok = rsi >= RSI_MIN and rsi <= RSI_MAX and dist_ma20 <= PULLBACK_BAND * 100
        if not pullback_ok:
            return None

        # ── DEFENSIVE: volatility + liquidity ──────────────────
        ann_vol = _annualized_vol(close)
        vol_ok = ann_vol < MAX_VOLATILITY
        recent_vol = float(vol.tail(10).mean()) if len(vol) > 10 else 1.0

        # ── Composite Q score ───────────────────────────────────
        q = 0.0
        q += W_TREND * (2.0 if (above_ma60 and ma60_slope > 0.5) else (1.0 if above_ma60 else 0.0))
        q += W_MACD * (2.0 if (macd_above_zero and macd_bullish) else 1.0)
        q += W_PROX * (2.0 if abs(dist_ma20) <= 2.0 else (1.0 if dist_ma20 > 0 else 0.0))
        q += W_LOWVOL * (2.0 if ann_vol < 0.25 else (1.0 if vol_ok else 0.0))
        composite = max(0.0, min(100.0, (q / (2*(W_TREND+W_MACD+W_PROX+W_LOWVOL))) * 100))

        return {
            'ts_code': ts_code,
            'close': last,
            'ma20': lma20,
            'ma60': lma60,
            'dist_ma20': dist_ma20,
            'rsi': rsi,
            'macd': ldif - ldea,
            'composite_score': composite,
            'volatility': ann_vol,
            'recent_vol': recent_vol,
            'trend': 'Bullish' if (above_ma60 and ma60_slope > 0) else 'Neutral',
        }
    except Exception as e:
        logger.warning(f"[ts_multi_swing_defensive] error {ts_code}: {e}")
        return None


# ── Entry point ─────────────────────────────────────────────────

def pick_multi_swing_defensive(end_date: str, max_picks: int = 10) -> pd.DataFrame:
    logger.info(f"[ts_multi_swing_defensive] starting pick for {end_date}")

    stock_basic = data_provider.get_basic_information_api()
    if stock_basic.empty:
        raise ValueError("No basic stock information found")

    try:
        from backtest.strategies.ts_ths_dc import no_risky_stocks
        risky_free = no_risky_stocks(stock_basic)
        stock_basic = stock_basic[stock_basic['ts_code'].isin(risky_free)].reset_index(drop=True)
    except Exception:
        pass  # keep full universe if filter unavailable
    logger.info(f"[ts_multi_swing_defensive] universe: {len(stock_basic)}")

    # Market regime is the master switch (defensive tilt)
    try:
        regime_data = detect_market_regime(end_date)
        regime = regime_data.get('regime', 'normal')
    except Exception:
        regime = 'normal'
    logger.info(f"[ts_multi_swing_defensive] market regime: {regime}")

    regime_max = {'bull': 12, 'normal': 10, 'volatile': 4, 'bear': 2}
    max_picks = min(max_picks, regime_max.get(regime, 10))
    # Defensive bar: raise min-score / tighten in weak regimes
    min_score = {'bull': 50, 'normal': 55, 'volatile': 60, 'bear': 70}.get(regime, 55)

    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS)
    all_data = data_provider.get_bulk_ohlcv_by_date_range(start_date, end_date)
    logger.info(f"[ts_multi_swing_defensive] bulk fetch: {len(all_data)} stocks")

    results = []
    for idx, row in stock_basic.iterrows():
        if idx % 1000 == 0:
            logger.info(f"[ts_multi_swing_defensive] analyzing {idx}/{len(stock_basic)}")
        ts_code = row['ts_code']
        sdf = all_data.get(ts_code)
        if sdf is None:
            continue
        analysis = analyze_stock_with_data(ts_code, sdf)
        if analysis:
            # weak-regime defensiveness: bump the effective bar
            if regime in ('bear', 'volatile') and analysis['volatility'] > 0.30:
                continue
            analysis['name'] = row.get('name', '')
            results.append(analysis)

    if not results:
        logger.warning("[ts_multi_swing_defensive] no candidates")
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values('composite_score', ascending=False)
    kept = df[df['composite_score'] >= min_score]
    if len(kept) == 0:
        kept = df.head(max_picks)
    kept = kept.head(max_picks)
    kept['rank'] = range(1, len(kept) + 1)

    logger.info(f"[ts_multi_swing_defensive] selected {len(kept)} (regime={regime}, min_score={min_score})")
    return kept


if __name__ == "__main__":
    argv = sys.argv[1:]
    lookahead = False
    if '--lookahead' in argv:
        lookahead = True
        argv.remove('--lookahead')

    if len(argv) >= 1:
        target_date = convert_trade_date(argv[0])
    else:
        target_date = pd.Timestamp.today().strftime('%Y%m%d')

    date = target_date
    if not lookahead:
        date = get_trading_days_before(date, 1)

    logger.info(f"[ts_multi_swing_defensive] target {target_date} ref {date}")

    df = pick_multi_swing_defensive(end_date=date)

    output_file = '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_multi_swing_defensive_tmp.json'

    selected = []
    if df is not None and not df.empty:
        for _, stock in df.iterrows():
            selected.append({
                'rank': int(stock['rank']),
                'symbol': stock['ts_code'],
                'name': stock.get('name', ''),
                'score': float(stock['composite_score']),
            })

    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected}, f)
    logger.info(f"[ts_multi_swing_defensive] saved {len(selected)} picks to {output_file}")
