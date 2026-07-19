"""
Multi-Factor Momentum stock picking strategy for A-Shares.

Inspired by BigQuant's multi-factor momentum framework:
  - Volume acceleration (5d vs prior 5d > 7%)
  - Short-term momentum (3d return 0-17%, 5d slope top 20%)
  - Trend confirmation (60d MA proximity)
  - Market cap ranking

Combined with quality/value safety gates from 6-factors framework:
  - PE < 100, PB < 10 (loose, just prevent garbage)
  - Market cap 20B-500B (mid-cap sweet spot)
  - Active turnover > 1%

Uses batch daily data fetching for speed (~100 stocks in one PRO.daily call),
then computes all metrics via pandas vectorized operations.

Usage:
  python -m backtest.strategies.ts_multi_factors <date YYYYMMDD> [ts_multi_factors]

Output:
  /tmp/tmp: {"selected_stocks": [{"rank": 1, "symbol": "000001.SZ", "score": 8.5},...]}
"""

import os
import sys
import json
from datetime import datetime
from loguru import logger
import pandas as pd
import numpy as np
import warnings
import time as time_mod

import tushare as ts
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before
from backtest.utils.util import convert_trade_date
from backtest.utils.market_regime import detect_market_regime
from backtest.strategies.ts_ths_dc import no_risky_stocks

warnings.filterwarnings("ignore", category=UserWarning, module='py_mini_racer')

load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
if not TUSHARE_TOKEN:
    raise ValueError("Please set the TUSHARE_TOKEN environment variable.")
PRO = ts.pro_api(TUSHARE_TOKEN)

# ── Multi-Factor Parameters ─────────────────────────────────────
# Quality gates (safety nets, not primary scoring)
QF_PE_MAX = 100                  # PE < 100
QF_PB_MAX = 10                   # PB < 10
QF_TURNOVER_MIN = 1.0            # Turnover > 1%
QF_PRICE_MIN = 5                 # Price > 5 yuan
QF_PRICE_MAX = 200               # Price < 200 yuan
QF_MCAP_MIN = 20                 # Market cap ≥ 20 billion
QF_MCAP_MAX = 500                # Market cap ≤ 500 billion

# Momentum thresholds (BigQuant-inspired)
VOL_ACCEL_MIN = 1.07             # 5d vol / prior 5d vol > 1.07 (+7%)
RET_3D_MIN = 0.0                 # 3-day return > 0
RET_3D_MAX = 0.17                # 3-day return < 17% (avoid chasing parabolic)
SLOPE_5D_TOP_PCT = 0.20          # 5-day slope top 20%

LOOKBACK_DAYS = 70               # 60 trading days + buffer
MAX_PICKS = 30                   # Top N stocks to output


def get_stock_pool() -> pd.DataFrame:
    """Get A-share stock pool, filtering out ST, delisted, Beijing exchange."""
    stock_basic = data_provider.get_basic_information_api()
    risky_free = no_risky_stocks(stock_basic=stock_basic)
    pool = stock_basic[stock_basic['ts_code'].isin(risky_free)].reset_index(drop=True)
    return pool


def pre_filter(pool: pd.DataFrame, end_date: str) -> pd.DataFrame:
    """
    Phase 1: Pre-filter via daily_basic.
    Quality gates: market cap, PE, PB, turnover, price.
    Returns DataFrame with ts_code, name.
    """
    try:
        basic = PRO.daily_basic(trade_date=end_date)
        if basic is None or basic.empty:
            logger.warning("daily_basic returned empty")
            return pd.DataFrame()

        pool = pool.merge(
            basic[['ts_code', 'total_mv', 'circ_mv', 'turnover_rate', 'pe', 'pb', 'close']],
            on='ts_code', how='inner'
        )

        # Market cap (万元 → 亿)
        pool['total_mv_yi'] = pool['total_mv'] / 10000
        pool = pool[(pool['total_mv_yi'] >= QF_MCAP_MIN) & (pool['total_mv_yi'] <= QF_MCAP_MAX)]
        logger.info(f"  After mcap {QF_MCAP_MIN}-{QF_MCAP_MAX}亿: {len(pool)}")

        # PE/PB safety gates
        pool = pool[pool['pe'].notna() & (pool['pe'] > 0) & (pool['pe'] < QF_PE_MAX)]
        pool = pool[pool['pb'].notna() & (pool['pb'] > 0) & (pool['pb'] < QF_PB_MAX)]
        logger.info(f"  After PE<{QF_PE_MAX} PB<{QF_PB_MAX}: {len(pool)}")

        # Active stocks
        pool = pool[pool['turnover_rate'].notna() & (pool['turnover_rate'] >= QF_TURNOVER_MIN)]
        logger.info(f"  After turnover > {QF_TURNOVER_MIN}%: {len(pool)}")

        # Price range
        pool = pool[pool['close'].notna() & (pool['close'] >= QF_PRICE_MIN) & (pool['close'] <= QF_PRICE_MAX)]
        logger.info(f"  After price {QF_PRICE_MIN}-{QF_PRICE_MAX}: {len(pool)}")

    except Exception as e:
        logger.warning(f"Pre-filter failed: {e}")
        return pd.DataFrame()

    return pool[['ts_code', 'name', 'close', 'total_mv_yi', 'pe', 'pb']].reset_index(drop=True)


def compute_stock_metrics(daily_df: pd.DataFrame, ts_code: str) -> dict | None:
    """
    Compute momentum metrics for a single stock from its daily OHLCV data.
    Returns dict with metrics or None if insufficient data.
    """
    if daily_df is None or daily_df.empty or len(daily_df) < 25:
        return None

    df = daily_df.sort_values('trade_date', ascending=False).reset_index(drop=True)
    close = df['close'].astype(float)
    vol = df['vol'].astype(float)

    if len(close) < 25:
        return None

    # 5-day volume acceleration: avg(vol[0:5]) / avg(vol[5:10])
    vol_5d = vol.iloc[0:5].mean()
    vol_prior_5d = vol.iloc[5:10].mean()
    vol_accel = vol_5d / vol_prior_5d if vol_prior_5d > 0 else 0.0

    # 3-day cumulative return
    ret_3d = (close.iloc[0] - close.iloc[2]) / close.iloc[2] if close.iloc[2] > 0 else 0.0

    # 5-day return slope (linear regression on close)
    if len(close) >= 5:
        closes_5d = close.iloc[0:5].values
        x = np.arange(5)
        slope, _ = np.polyfit(x, closes_5d, 1)
        slope_5d = slope / closes_5d.mean()  # normalize by mean price
    else:
        slope_5d = 0.0

    # 60-day MA proximity
    ma60 = float(close.rolling(60).mean().iloc[0]) if len(close) >= 60 else float(close.mean())
    ma60_dist = (close.iloc[0] - ma60) / ma60 if ma60 > 0 else 0.0

    # 20-day volatility
    returns = close.pct_change().dropna().tail(20)
    vol_20d = float(returns.std() * np.sqrt(252)) if len(returns) >= 5 else 999.0

    return {
        'ts_code': ts_code,
        'vol_accel': float(vol_accel),
        'ret_3d': float(ret_3d),
        'slope_5d': float(slope_5d),
        'ma60_dist': float(ma60_dist),
        'vol_20d': vol_20d,
    }


def multi_factor_screener(end_date: str) -> pd.DataFrame:
    """
    Phase 2: Fetch daily OHLCV for all pre-filtered stocks, compute momentum metrics,
    rank by composite score.
    """
    logger.info(f"Multi-Factor Momentum screener for {end_date}")

    pool = get_stock_pool()
    logger.info(f"Stock pool: {len(pool)} stocks")

    # ── Phase 1: Pre-filter ──
    candidates = pre_filter(pool, end_date)
    if candidates.empty:
        logger.warning("No stocks after pre-filter")
        return pd.DataFrame()

    logger.info(f"Candidates after pre-filter: {len(candidates)} stocks")

    # ── Phase 2: Fetch daily data in batch ──
    lookback_start = get_trading_days_before(end_date, LOOKBACK_DAYS - 1)
    ts_codes = candidates['ts_code'].tolist()

    results = []
    batch_size = 50
    for batch_start in range(0, len(ts_codes), batch_size):
        batch_codes = ts_codes[batch_start:batch_start + batch_size]
        try:
            # Fetch all stocks in this batch with one API call
            daily = PRO.daily(
                ts_code=','.join(batch_codes),
                start_date=lookback_start,
                end_date=end_date
            )
            if daily is None or daily.empty:
                continue

            for code in batch_codes:
                stock_daily = daily[daily['ts_code'] == code]
                metrics = compute_stock_metrics(stock_daily, code)
                if metrics is not None:
                    # Merge with pre-filter data
                    cand_row = candidates[candidates['ts_code'] == code]
                    if not cand_row.empty:
                        metrics['name'] = cand_row.iloc[0]['name']
                        metrics['price'] = float(cand_row.iloc[0]['close'])
                        metrics['total_mv_yi'] = float(cand_row.iloc[0]['total_mv_yi'])
                        metrics['pe'] = float(cand_row.iloc[0]['pe'])
                        metrics['pb'] = float(cand_row.iloc[0]['pb'])
                        results.append(metrics)

        except Exception as e:
            logger.warning(f"Batch {batch_start}-{batch_start+batch_size} failed: {e}")
            continue

        time_mod.sleep(0.3)  # Rate limit

    logger.info(f"Stocks with valid metrics: {len(results)}")
    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)

    # ── Phase 3: Apply hard momentum filters FIRST, then rank-based scoring ──
    # BigQuant step 3: 近3日累计涨幅 0%-17% — HARD FILTER (exclude negative)
    df = df[df['ret_3d'] >= RET_3D_MIN].reset_index(drop=True)
    logger.info(f"  After ret_3d ≥ {RET_3D_MIN}: {len(df)} stocks")

    if df.empty:
        return pd.DataFrame()

    # Volume acceleration hard filter
    df['vol_pass'] = df['vol_accel'] >= VOL_ACCEL_MIN
    vol_pass_count = df['vol_pass'].sum()
    logger.info(f"  Volume acceleration ≥ {VOL_ACCEL_MIN}: {vol_pass_count} stocks")

    # 5-day slope top 20% filter
    slope_cutoff = df['slope_5d'].quantile(1.0 - SLOPE_5D_TOP_PCT)
    df['slope_pass'] = df['slope_5d'] >= slope_cutoff
    logger.info(f"  Slope ≥ {slope_cutoff:.4f} (top {SLOPE_5D_TOP_PCT*100:.0f}%): {df['slope_pass'].sum()} stocks")

    # Final: must pass vol OR slope (at least one momentum confirmation)
    df = df[df['vol_pass'] | df['slope_pass']].reset_index(drop=True)
    # Volume acceleration: higher is better (stronger volume push)
    df['vol_accel_rank'] = df['vol_accel'].rank(pct=True)

    # 3-day return: sweet spot 0-17%, penalize both negative and >17%
    df['ret_3d_quality'] = df['ret_3d'].apply(
        lambda x: 1.0 if RET_3D_MIN <= x <= RET_3D_MAX 
        else (0.5 if x > RET_3D_MAX else max(0, 1.0 + x * 2))  # linear penalty for negative
    )

    # 5-day slope: higher is better
    df['slope_5d_rank'] = df['slope_5d'].rank(pct=True)

    # MA60 proximity: prefer close to MA60 (not extended), penalize too far above or below
    df['ma60_score'] = 1.0 - abs(df['ma60_dist']).clip(0, 0.3) / 0.3

    # Volatility: prefer moderate vol (10-35%), penalize extremes
    df['vol_score'] = df['vol_20d'].apply(
        lambda x: 1.0 if 0.10 <= x <= 0.35
        else max(0, 1.0 - abs(x - 0.25) / 0.25)
    )

    # ── Composite score ──
    df['composite'] = (
        df['vol_accel_rank'] * 0.25 +       # Volume confirmation
        df['ret_3d_quality'] * 0.20 +        # Return sweet-spot
        df['slope_5d_rank'] * 0.25 +         # Trend strength
        df['ma60_score'] * 0.15 +            # Entry timing
        df['vol_score'] * 0.15               # Stability
    )

    df = df.sort_values('composite', ascending=False).reset_index(drop=True)
    df['rank'] = df.index + 1

    logger.info(f"Final picks: {len(df)} stocks, composite {df['composite'].min():.2f}-{df['composite'].max():.2f}")
    return df


def pick_strong_stocks(start_date: str, end_date: str, src: str = 'ts_multi_factors') -> pd.DataFrame:
    """
    Main entry point — compatible with ts_ths_dc / ts_7AZ interface.
    """
    regime_data = detect_market_regime(end_date)
    regime = regime_data.get('regime', 'normal')
    logger.info(f"Market Regime: {regime}")

    df = multi_factor_screener(end_date)

    if df.empty:
        return df

    # Take top MAX_PICKS
    df = df.head(MAX_PICKS).reset_index(drop=True)
    df['rank'] = df.index + 1

    # Save to /tmp/tmp in standard format
    selected_stocks = []
    for _, row in df.iterrows():
        # Scale composite to 1-10 range for consistency with other strategies
        scaled_score = round(row['composite'] * 10, 1)
        selected_stocks.append({
            'rank': int(row['rank']),
            'symbol': row['ts_code'],
            'score': float(scaled_score)
        })

    output_file = '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_multi_factors_tmp.json'
    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)

    logger.info(f"Saved {len(selected_stocks)} Multi-Factor picks to {output_file}")
    return df


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) >= 1:
        date = convert_trade_date(argv[0])
    else:
        date = datetime.now().strftime('%Y%m%d')

    src = argv[1] if len(argv) >= 2 else 'ts_multi_factors'

    date = get_trading_days_before(date, 1)
    start_date = get_trading_days_before(date, 5)
    end_date = date

    df = pick_strong_stocks(start_date=start_date, end_date=end_date, src=src)

    if not df.empty:
        logger.info("=== TOP 10 Multi-Factor Momentum Picks ===")
        for _, row in df.head(10).iterrows():
            logger.info(
                f"  {row['rank']}. {row['name']}({row['ts_code']}) "
                f"Score={row['composite']:.3f} "
                f"VolAccel={row['vol_accel']:.2f} "
                f"Ret3d={row['ret_3d']:.1%} "
                f"MA60Dist={row['ma60_dist']:.1%}"
            )
