"""
ts_ao_er: AO (Awesome Oscillator) + ER (Kaufman Efficiency Ratio) Strategy

Based on automated strategy discovery methodology — uses AO falling signal for
entry and ER threshold for exit filtering.

Entry signal: AO falling for 3+ consecutive bars → momentum weakening, potential entry
Exit filter: ER > 0.7 AND price rising → efficient trend detected, do not enter

Adapted for A-Shares with T+1 compliance.

Reference: 数据科学实战 "我把程序挂机跑了一夜，醒来它自己写好了一套交易策略" (2026-06-30)

Usage:
    python backtest/strategies/ts_ao_er.py YYYYMMDD [--no-search] [--no-ai]
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
warnings.filterwarnings("ignore", category=FutureWarning)

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)

# ── AO Parameters ──────────────────────────────────────────────
AO_SHORT_PERIOD = 5        # Short SMA period
AO_LONG_PERIOD = 34        # Long SMA period
AO_FALL_BARS = 3           # Consecutive falling bars for entry signal
AO_FALL_MIN = 3            # Minimum consecutive fall for scoring
AO_FALL_MAX = 8            # Cap for scoring (more than 8 bars = diminishing confidence)

# ── ER Parameters ──────────────────────────────────────────────
ER_PERIOD = 14             # Lookback period for efficiency ratio
ER_THRESHOLD = 0.7         # ER > this means efficient trend (exit zone)
ER_MAX_FOR_ENTRY = 0.65    # ER must be below this to consider entry (with buffer)

# ── Stock Pool Filters ─────────────────────────────────────────
LOOKBACK_DAYS = 60         # Need 34+ for AO long period
MIN_PRICE = 3.0            # Skip penny stocks (<3 yuan)
MIN_VOLUME_RATIO = 0.5     # Recent volume >= 50% of 20-day average
MAX_MARKET_CAP = 1000e8    # Max 1000亿 market cap
MAX_PICKS = 20             # Max stocks to return

# ── Market Regime Parameters ───────────────────────────────────
REGIME_MULTIPLIERS = {
    'bull':    {'ao_fall_weight': 1.0,  'er_weight': 0.8,  'volume_weight': 0.7},
    'normal':  {'ao_fall_weight': 1.0,  'er_weight': 1.0,  'volume_weight': 1.0},
    'volatile':{'ao_fall_weight': 1.3,  'er_weight': 0.6,  'volume_weight': 0.5},
    'bear':    {'ao_fall_weight': 0.6,  'er_weight': 1.3,  'volume_weight': 1.2},
}


def calculate_ao(high: pd.Series, low: pd.Series,
                 short_period: int = AO_SHORT_PERIOD,
                 long_period: int = AO_LONG_PERIOD) -> pd.Series:
    """Calculate Awesome Oscillator (AO).

    AO = SMA(median_price, 5) - SMA(median_price, 34)
    median_price = (high + low) / 2
    """
    median = (high + low) / 2
    sma_short = median.rolling(window=short_period).mean()
    sma_long = median.rolling(window=long_period).mean()
    return sma_short - sma_long


def ao_falling_bars(ao: pd.Series) -> pd.Series:
    """Count consecutive bars where AO is falling (current < previous).
    Returns an integer count for each bar.
    """
    falling = ao < ao.shift(1)
    # Cumulative count of consecutive True values, reset on False
    result = pd.Series(0, index=ao.index, dtype=int)
    count = 0
    for i in range(len(falling)):
        if falling.iloc[i]:
            count += 1
        else:
            count = 0
        result.iloc[i] = count
    return result


def kaufman_efficiency_ratio(close: pd.Series, period: int = ER_PERIOD) -> pd.Series:
    """Calculate Kaufman Efficiency Ratio.

    ER = abs(net_change) / sum(abs(daily_changes))
    Range: 0 (choppy) to 1 (efficient trend).
    """
    net_change = close.diff(period).abs()
    daily_changes = close.diff().abs().rolling(period).sum()
    er = net_change / daily_changes.replace(0, np.nan)
    return er


def get_stock_pool() -> pd.DataFrame:
    """Get A-share stock pool, filtering out ST, delisted, and Beijing exchange."""
    stock_basic = data_provider.get_basic_information_api()
    risky_free = no_risky_stocks(stock_basic=stock_basic)
    pool = stock_basic[stock_basic['ts_code'].isin(risky_free)].reset_index(drop=True)
    return pool


def compute_ao_er_signals(ts_code: str, end_date: str) -> Optional[dict]:
    """Compute AO and ER signals for a single stock.

    Returns dict with signal data or None if insufficient data.
    """
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS - 1)
    try:
        df = data_provider.get_ohlcv_data(symbol=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty or len(df) < 40:
            return None
        df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)
    except Exception:
        return None

    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    volume = df.get('vol', pd.Series(0, index=df.index)).astype(float)

    # Skip penny stocks
    price = close.iloc[-1]
    if price < MIN_PRICE:
        return None

    # AO
    ao = calculate_ao(high, low)
    ao_fall = ao_falling_bars(ao)

    # ER
    er = kaufman_efficiency_ratio(close)
    er_latest = er.iloc[-1]

    # Volume check
    vol_20_avg = volume.rolling(20).mean().iloc[-1] if len(volume) >= 20 else volume.mean()
    vol_latest = volume.iloc[-1]
    vol_ratio = vol_latest / vol_20_avg if vol_20_avg > 0 else 0

    # Price vs MA20
    ma20 = close.rolling(20).mean().iloc[-1]
    price_vs_ma20 = price / ma20 if ma20 > 0 else 1.0

    return {
        'ts_code': ts_code,
        'price': float(price),
        'ao_latest': float(ao.iloc[-1]),
        'ao_fall_bars': int(ao_fall.iloc[-1]),
        'er_latest': float(er_latest),
        'vol_ratio': float(vol_ratio),
        'price_vs_ma20': float(price_vs_ma20),
        'close': close,
        'ao': ao,
        'er': er,
    }


def score_stock(signals: dict, regime: str) -> Optional[dict]:
    """Score a stock based on AO_ER signals and market regime.

    Entry criteria:
        - AO falling for AO_FALL_BARS+ bars (momentum weakening signal)
        - ER < ER_MAX_FOR_ENTRY (not yet in efficient exit zone)
        - Volume ratio >= MIN_VOLUME_RATIO (active stock)

    Scoring weights (regime-adjusted):
        - AO fall strength: deeper/more sustained fall = better entry
        - ER distance from threshold: lower ER = further from exit = more room
        - Volume: higher relative volume = stronger signal
    """
    r = REGIME_MULTIPLIERS.get(regime, REGIME_MULTIPLIERS['normal'])
    ao_fall = signals['ao_fall_bars']
    er_val = signals['er_latest']
    vol_ratio = signals['vol_ratio']

    # Entry criteria
    if ao_fall < AO_FALL_BARS:
        return None
    if er_val > ER_MAX_FOR_ENTRY:
        return None
    if vol_ratio < MIN_VOLUME_RATIO:
        return None

    # ── Scoring ──
    # AO fall score: 0-1, caps at AO_FALL_MAX bars
    ao_score = min(ao_fall, AO_FALL_MAX) / AO_FALL_MAX

    # ER score: inverse — lower ER = further from exit zone = better entry
    er_score = max(0, 1 - (er_val / ER_THRESHOLD))

    # Volume score: higher relative volume = more significant
    vol_score = min(vol_ratio, 3.0) / 3.0

    # Composite score with regime-adjusted weights
    composite = (
        ao_score * 100 * r['ao_fall_weight'] +
        er_score * 100 * r['er_weight'] +
        vol_score * 100 * r['volume_weight']
    )

    # Bonus for negative AO (more oversold)
    if signals['ao_latest'] < 0:
        composite += 10

    # Penalty if price far above MA20 (overextended)
    if signals['price_vs_ma20'] > 1.15:
        composite *= 0.8

    # Bonus if price near MA20 (good pullback entry)
    if 0.95 <= signals['price_vs_ma20'] <= 1.05:
        composite += 15

    return {
        'ts_code': signals['ts_code'],
        'price': signals['price'],
        'ao_latest': round(signals['ao_latest'], 4),
        'ao_fall_bars': ao_fall,
        'er_latest': round(er_val, 4),
        'vol_ratio': round(vol_ratio, 2),
        'price_vs_ma20': round(signals['price_vs_ma20'], 2),
        'composite_score': round(composite, 1),
        'ao_score': round(ao_score, 3),
        'er_score': round(er_score, 3),
        'vol_score': round(vol_score, 3),
    }


def pick_ao_er_stocks(end_date: str, max_picks: int = MAX_PICKS) -> pd.DataFrame:
    """Main stock picking function for AO_ER strategy.

    Args:
        end_date: Trading date in YYYYMMDD format
        max_picks: Maximum number of stocks to return

    Returns:
        DataFrame with scored stocks
    """
    regime_data = detect_market_regime(end_date)
    regime = regime_data.get('regime', 'normal')
    logger.info(f"[ts_ao_er] Market Regime: {regime}")

    pool = get_stock_pool()
    logger.info(f"[ts_ao_er] Stock pool: {len(pool)} stocks")

    # For performance: sample a subset by market cap if pool is huge
    if len(pool) > 300:
        logger.info(f"[ts_ao_er] Pool too large, sampling top 300 (randomized for diversity)")
        pool = pool.sample(n=300, random_state=int(end_date)).reset_index(drop=True)

    results = []
    for i, (_, row) in enumerate(pool.iterrows()):
        ts_code = row['ts_code']
        name = row.get('name', ts_code)

        signals = compute_ao_er_signals(ts_code, end_date)
        if signals is None:
            continue

        scored = score_stock(signals, regime)
        if scored is None:
            continue

        scored['name'] = name
        results.append(scored)

        if (i + 1) % 50 == 0:
            logger.info(f"[ts_ao_er] Scanned {i + 1}/{len(pool)}, {len(results)} passing")

    logger.info(f"[ts_ao_er] Passed filters: {len(results)} stocks")

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values('composite_score', ascending=False).reset_index(drop=True)
    df = df.head(max_picks)
    df['rank'] = range(1, len(df) + 1)

    # Reorder columns for readability
    cols = ['rank', 'name', 'ts_code', 'price', 'composite_score',
            'ao_fall_bars', 'ao_latest', 'er_latest',
            'vol_ratio', 'price_vs_ma20',
            'ao_score', 'er_score', 'vol_score']
    df = df[[c for c in cols if c in df.columns]]

    # Log top picks
    for _, row in df.head(8).iterrows():
        logger.info(
            f"[ts_ao_er] #{row['rank']}: {row['name']}({row['ts_code']}) "
            f"score={row['composite_score']:.1f} "
            f"AO_fall={row['ao_fall_bars']} AO={row['ao_latest']:.4f} "
            f"ER={row['er_latest']:.3f} vol_ratio={row['vol_ratio']:.1f}"
        )

    return df


if __name__ == "__main__":
    argv = sys.argv[1:]
    # Strip optional flags (passed by engine.py)
    flags = [a for a in argv if a.startswith('--')]
    args = [a for a in argv if not a.startswith('--')]

    if len(args) >= 1:
        date = convert_trade_date(args[0])
    else:
        date = datetime.now().strftime('%Y%m%d')

    # Use previous trading day for T+1 compliance
    date = get_trading_days_before(date, 1)
    logger.info(f"[ts_ao_er] Picking stocks for reference date: {date}")

    df = pick_ao_er_stocks(end_date=date)

    # Output to /tmp/tmp in standard format
    output_file = '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_ao_er_tmp.json'

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

    logger.info(f"[ts_ao_er] Saved {len(selected_stocks)} picked stocks to {output_file}")
