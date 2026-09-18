"""
ts_7AZ_96MA_flow_review_longterm — NLP-augmented strategy (Long-term Project).

Identical to ts_7AZ_96MA_flow_review EXCEPT it delegates the flow filter to
ts_7AZ_96MA_flow_longterm.apply_longterm_flow_filter (which wraps the base
filter + NLP hooks). All NLP gates default OFF, so current behaviour is
exactly the base flow filter until the NLP pipeline matures.

STATUS: SKELETON — ready to register with the engine but produces identical
        results to the base review strategy while NLP gates are OFF.
        Enable via env NLP_SENTIMENT_GATE=true etc. once the NLP models
        are built.

Usage (engine invocation):
  .venv/bin/python backtest/engine.py 20260101 20260831 \\
    ts_7AZ_96MA_flow_review_longterm --no-search --no-ai

For NLP-enabled runs (once pipeline is built):
  env NLP_SENTIMENT_GATE=true NLP_IRM_EVASION_GATE=true \\
    .venv/bin/python backtest/engine.py 20260101 20260831 \\
    ts_7AZ_96MA_flow_review_longterm --no-search --no-ai
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

# ── Production defaults (same as base review strategy) ───────────────────
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
        adjustments['strategy'] = 'ts_7AZ_96MA_flow_review_longterm'
        with open(REVIEW_OUTPUT, 'w') as f:
            json.dump(adjustments, f, ensure_ascii=False, indent=2)
        logger.info(f"[review_longterm] Wrote adjustments to {REVIEW_OUTPUT}")
    except Exception as e:
        logger.warning(f"[review_longterm] Review failed: {e} — writing fallback")
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
    logger.info(f"[longterm] Saved {len(selected_stocks)} picks to {output_file}")


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

    logger.info(f"[longterm] target {target_date} ref {date}")

    # ── Step 1: Pick stocks (same as base) ──────────────────────────────
    from backtest.strategies.ts_7AZ_96MA_flow_v2 import _regime_96ma, _in_crash
    from backtest.strategies.ts_7AZ import pick_strong_stocks
    from backtest.strategies.ts_96MA import pick_96mv_stocks
    # ← Key difference: import from LONGTERM flow filter, not base v2
    from backtest.strategies.ts_7AZ_96MA_flow_longterm import apply_longterm_flow_filter

    use_96 = _regime_96ma(date)
    if use_96:
        df = pick_96mv_stocks(end_date=date)
        logger.info(f"[longterm] used ts_96MA -> {len(df)} candidates")
    else:
        df = pick_strong_stocks(date, date, src='ts_7AZ')
        logger.info(f"[longterm] used ts_7AZ -> {len(df)} candidates")

    if (df is None or df.empty) and _in_crash(date):
        from backtest.strategies.ts_hma import pick_hma_stocks
        logger.warning("[longterm] 0 picks + confirmed crash -> defensive ts_hma fallback")
        df = pick_hma_stocks(end_date=date)
        logger.info(f"[longterm] hma defensive fallback -> {len(df)} candidates")

    # ← Key difference: use long-term flow filter (currently identical to base)
    df = apply_longterm_flow_filter(df, date)

    # ── Step 2: Write picks ─────────────────────────────────────────────
    _write_output(df)

    # Also write to REVIEW_RESULTS_DIR
    _review_dir = os.environ.get('REVIEW_RESULTS_DIR')
    if _review_dir:
        try:
            os.makedirs(_review_dir, exist_ok=True)
            import shutil
            src = '/tmp/tmp' if os.path.isfile('/tmp/tmp') else '/tmp/ts_7AZ_tmp.json'
            dst = os.path.join(_review_dir, f'pick_stocks_{target_date}.json')
            if os.path.exists(src):
                shutil.copy2(src, dst)
                logger.info(f"[longterm] Copied picks to {dst}")
        except Exception as e:
            logger.warning(f"[longterm] Failed to copy picks to results dir: {e}")

    # ── Step 3: Post-market review (same as base) ────────────────────────
    _run_review_and_write(target_date)
