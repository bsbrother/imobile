"""
ts_7AZ_96MA_flow_review: v2 stock picking + vibe-astock style post-market review.

Delegates stock picking to ts_7AZ_96MA_flow_v2 (regime-adaptive LHB + volume boost).
Writes picks first, THEN runs post-market review so _advance_rate can read today's
pick_stocks file (which needs both today's and yesterday's files to exist).
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

# ── Production defaults for the ts_7AZ_96MA_flow family ──────────────────────
# Measured 2026-09-12 on 20260101-20260831: total return 134.98% vs the
# 132.15% baseline (+2.83pp), max drawdown -1.63% vs -2.44%.
#   TS7AZ_RPS_MIN / TS96MA_RPS_MIN  entry RPS gate 80/70 -> 60 (LEVER 3):
#       admits earlier-stage trends. Verified by A/B (run "lev34" 134.98%
#       vs baseline 132.15% with the review layer in legacy mode).
#   LHB_EXHAUST_RUN5=30 (LEVER 4): ignore 龙虎榜 institutional records whose
#       上榜日 was itself preceded by a >=30% 5-day run (exhaustion, not
#       accumulation).
# setdefault (not assignment) so an explicit `env VAR=...` still wins.
# Scoped to this entry point: ts_7AZ/ts_96MA keep RPS 80/70 for all other
# strategies that import them.
os.environ.setdefault('TS7AZ_RPS_MIN', '60')
os.environ.setdefault('TS96MA_RPS_MIN', '60')
os.environ.setdefault('LHB_EXHAUST_RUN5', '30')

REVIEW_OUTPUT = '/tmp/review_adjustments.json'


def _run_review_and_write(target_date: str):
    """Run daily review and write adjustments for the engine to consume."""
    try:
        from backtest.strategies.post_market_review import get_reviewer
        reviewer = get_reviewer()
        adjustments = reviewer.daily_review(today=target_date)
        adjustments['review_date'] = target_date
        adjustments['strategy'] = 'ts_7AZ_96MA_flow_review'
        with open(REVIEW_OUTPUT, 'w') as f:
            json.dump(adjustments, f, ensure_ascii=False, indent=2)
        logger.info(f"[review] Wrote adjustments to {REVIEW_OUTPUT}")
    except Exception as e:
        logger.warning(f"[review] Review failed: {e} — writing fallback")
        fallback = {
            'review_date': target_date, 'sentiment': 'fermenting',
            'win_rate': 0.5, 'advance_rate': 0.0, 'avg_hold_days': 0,
            'fragmenting': False, 'max_positions_override': None,
            'holding_days_mult': 1.0, 'tp_aggressiveness': 1.0,
            'sl_tightness': 1.0, 'lhb_loosen': True, 'regime_bias': None,
        }
        with open(REVIEW_OUTPUT, 'w') as f:
            json.dump(fallback, f, ensure_ascii=False, indent=2)


def _write_output(df: pd.DataFrame) -> None:
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

    # ── Step 1: Pick stocks (identical to v2) ──────────────────────────
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

    # ── Step 2: Write picks to /tmp/tmp (engine copies to results/) ──
    _write_output(df)

    # Also write directly to REVIEW_RESULTS_DIR so _advance_rate can read
    # today's pick_stocks file during the review step below. The engine's
    # pick_stocks_to_file() copies /tmp/tmp → REPORT_PATH after we exit,
    # but the review runs IN-PROCESS before that copy happens.
    _review_dir = os.environ.get('REVIEW_RESULTS_DIR')
    if _review_dir:
        try:
            os.makedirs(_review_dir, exist_ok=True)
            import shutil
            src = '/tmp/tmp' if os.path.isfile('/tmp/tmp') else '/tmp/ts_7AZ_tmp.json'
            dst = os.path.join(_review_dir, f'pick_stocks_{target_date}.json')
            if os.path.exists(src):
                shutil.copy2(src, dst)
                logger.info(f"[ts_7AZ_96MA_flow_review] Copied picks to {dst}")
        except Exception as e:
            logger.warning(f"[ts_7AZ_96MA_flow_review] Failed to copy picks to results dir: {e}")

    # ── Step 3: Run review (pick_stocks files now exist in REVIEW_RESULTS_DIR) ──
    _run_review_and_write(target_date)