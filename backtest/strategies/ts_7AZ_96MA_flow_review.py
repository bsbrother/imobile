"""
ts_7AZ_96MA_flow_review: v2 stock picking + vibe-astock style post-market review.

Delegates stock picking to ts_7AZ_96MA_flow_v2 (regime-adaptive LHB + volume boost).
After picking (which uses yesterday's close data = no lookahead), runs a post-market
review that examines the backtest DB for recent wins/losses and repick continuity.

The review computes four sentiment metrics (vibe-astock style):
1. 赚钱效应 (win_rate): % of recent sells that are profitable
2. 晋级率 (advance_rate): % of yesterday's picks re-picked today
3. 梯队断层 (echelon_gap): avg hold days — <2 = fragmenting
4. 情绪周期 (sentiment): ice / recovery / fermenting / frenzy / cooling

These feed into next-day strategy adjustments:
- Position count (ice: -50%, frenzy: +30%)
- Holding days multiplier (ice: ×0.5, frenzy: ×1.3)
- TP aggressiveness (ice: take profits faster, frenzy: let runners run)
- SL tightness (ice: -50%, frenzy: +50%)
- LHB filter looseness
- Regime bias (ice/recovery → force bear; frenzy → force bull)

The adjustments are written to /tmp/review_adjustments.json, which the engine
picks up before creating smart orders for the day (no lookahead).

Usage:
    python backtest/strategies/ts_7AZ_96MA_flow_review.py YYYYMMDD [--lookahead]
"""

import os
import sys
import json
import pandas as pd
from loguru import logger
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest.utils.trading_calendar import get_trading_days_before, convert_trade_date
from backtest.utils.logging_config import configure_logger

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)

REVIEW_OUTPUT = '/tmp/review_adjustments.json'


def _run_review_and_write(target_date: str):
    """Run daily review and write adjustments for the engine to consume."""
    try:
        from backtest.strategies.post_market_review import get_reviewer
        reviewer = get_reviewer()
        adjustments = reviewer.daily_review(today=target_date)

        # Add metadata
        adjustments['review_date'] = target_date
        adjustments['strategy'] = 'ts_7AZ_96MA_flow_review'

        with open(REVIEW_OUTPUT, 'w') as f:
            json.dump(adjustments, f, ensure_ascii=False, indent=2)
        logger.info(f"[review] Wrote adjustments to {REVIEW_OUTPUT}")
    except Exception as e:
        logger.warning(f"[review] Review failed: {e} — writing fallback")
        fallback = {
            'review_date': target_date,
            'sentiment': 'fermenting',
            'win_rate': 0.5,
            'advance_rate': 0.0,
            'avg_hold_days': 0,
            'fragmenting': False,
            'max_positions_override': None,
            'holding_days_mult': 1.0,
            'tp_aggressiveness': 1.0,
            'sl_tightness': 1.0,
            'lhb_loosen': True,
            'regime_bias': None,
            'note': 'review failed, using fermenting defaults'
        }
        with open(REVIEW_OUTPUT, 'w') as f:
            json.dump(fallback, f, ensure_ascii=False, indent=2)


def _write_output(df: pd.DataFrame) -> None:
    """Write pick output to /tmp/tmp (standard format for engine)."""
    selected_stocks = []
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            selected_stocks.append({
                'rank': int(row['rank']),
                'symbol': row['ts_code'],
                'name': row.get('name', ''),
                'score': float(row.get('score', 0) or 0),
            })
    output_file = '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_7AZ_tmp.json'
    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)
    logger.info(f"[ts_7AZ_96MA_flow_review] Saved {len(selected_stocks)} picks to {output_file}")


if __name__ == "__main__":
    # ── 1. Parse args (same as v2) ────────────────────────────────────────
    argv = sys.argv[1:]
    lookahead = False
    if '--lookahead' in argv:
        lookahead = True
        argv.remove('--lookahead')

    if len(argv) >= 1:
        target_date = convert_trade_date(argv[0])
    else:
        target_date = str(pd.Timestamp.today().strftime('%Y%m%d'))

    date = target_date
    if not lookahead:
        date = get_trading_days_before(target_date, 1)

    logger.info(f"[ts_7AZ_96MA_flow_review] target {target_date} ref {date}")

    # ── 2. Run review: analyze yesterday's results ─────────────────────────
    _run_review_and_write(target_date)

    # ── 3. Delegate stock picking to v2 ────────────────────────────────────
    # Re-use the v2 module's functions directly (same process = same imports =
    # faster than subprocess, and env vars from review carry through).
    from backtest.strategies.ts_7AZ_96MA_flow_v2 import (
        _regime_96ma, _in_crash, _apply_flow_filter_v2)
    from backtest.strategies.ts_7AZ import pick_strong_stocks
    from backtest.strategies.ts_96MA import pick_96mv_stocks

    use_96 = _regime_96ma(date)
    if use_96:
        df = pick_96mv_stocks(end_date=date)
        logger.info(f"[ts_7AZ_96MA_flow_review] used ts_96MA -> {len(df)} candidates")
    else:
        df = pick_strong_stocks(date, date, src='ts_7AZ')
        logger.info(f"[ts_7AZ_96MA_flow_review] used ts_7AZ -> {len(df)} candidates")

    if (df is None or df.empty) and _in_crash(date):
        from backtest.strategies.ts_hma import pick_hma_stocks
        logger.warning("[ts_7AZ_96MA_flow_review] 0 picks + confirmed crash -> defensive ts_hma fallback")
        df = pick_hma_stocks(end_date=date)
        logger.info(f"[ts_7AZ_96MA_flow_review] hma defensive fallback -> {len(df)} candidates")

    df = _apply_flow_filter_v2(df, date)

    _write_output(df)