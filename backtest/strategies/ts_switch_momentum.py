"""
ts_switch_momentum: hold whichever arm has been working, month by month.

WHY THIS EXISTS. Turning point and the flow pipeline disagree by more than 50pp a year, and
they disagree in opposite directions:

    2025   ts_turning_point (2-slot)  +37.98%  (DD -5.33)   ts_7AZ_96MA_flow_review  -15.72%  (DD -28.37)
    2026   ts_turning_point (2-slot)   +6.86%  (DD -12.44)  ts_7AZ_96MA_flow_review  +67.12%  (DD -10.44)

Picking by year is fitting the calendar. Picking by market regime was already measured not to
separate them (both years are regime-mixed; the flow arm wins 7 of 9 months in 2026, turning
wins 9 of 12 in 2025, and neither flips with the regime label). The rule that does separate
them is their own recent P&L:

    at the end of month M-1, run in month M whichever arm made more money in M-1

On stitched monthly returns over 2025-01..2026-09 that rule compounded +110.7% against
+47.5% always-turning, +40.9% always-flow, and +183.4% perfect foresight. This script is the
real version: one continuous engine run, so carried positions, cash and fills are a single
portfolio rather than stitched curves.

STATE IS LAGGED BY CONSTRUCTION. The schedule (`trading_test/build_momentum_schedule.py`)
is derived from month M-1's realised equity of each arm and is applied to all of month M, so
no session can see its own month. There is no parameter to tune here beyond that one month.

The delegate runs as a SUBPROCESS with its own `.env` section forced in — including the
scoped-out list, so a delegate cannot re-add another strategy's sizing/TP on import (see
`strategy_env.export_scoped_out`).

Env knobs:
    SWITCH_SCHEDULE_CSV     default /tmp/momentum_schedule.csv
    SWITCH_FALLBACK         delegate for a month absent from the schedule
                            default ts_7AZ_96MA_flow_review (the production default)
    SWITCH_AUDIT_CSV        default /tmp/momentum_switch_decisions.csv

Usage:
    python backtest/strategies/ts_switch_momentum.py YYYYMMDD [--no-search] [--no-ai]
"""

import csv
import json
import os
import subprocess
import sys
from datetime import datetime

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest.utils.trading_calendar import convert_trade_date
from backtest.utils.strategy_env import apply_strategy_env, export_scoped_out
from backtest.utils.logging_config import configure_logger

load_dotenv()
configure_logger(log_level=os.getenv("LOG_LEVEL", default="INFO"),
                 log_path=os.getenv("LOG_PATH", default="./logs"))

SCHEDULE_CSV = os.getenv('SWITCH_SCHEDULE_CSV', '/tmp/momentum_schedule.csv')
FALLBACK = os.getenv('SWITCH_FALLBACK', 'ts_7AZ_96MA_flow_review')
AUDIT_CSV = os.getenv('SWITCH_AUDIT_CSV', '/tmp/momentum_switch_decisions.csv')

DELEGATE_SCRIPTS = {
    'ts_7AZ_96MA_flow_review': 'backtest/strategies/ts_7AZ_96MA_flow_review.py',
    'ts_turning_point': 'backtest/strategies/ts_turning_point.py',
}


def load_schedule(path: "str | None" = None) -> dict:
    """{month(YYYYMM): row} — recomputed on every call; the file is tiny."""
    path = path or SCHEDULE_CSV
    try:
        with open(path, newline='') as f:
            return {row['month']: row for row in csv.DictReader(f)}
    except FileNotFoundError:
        logger.error(f"[momentum] schedule not found at {path}; every month uses {FALLBACK}")
        return {}


def delegate_for(month: str, schedule: dict) -> tuple:
    """(delegate, row) for a YYYYMM month. Missing month -> FALLBACK, loudly."""
    row = schedule.get(month)
    if not row:
        logger.warning(f"[momentum] {month} is not in the schedule -> fallback {FALLBACK}")
        return FALLBACK, {}
    return row['delegate'], row


def run_delegate(delegate: str, this_date: str, flags: list) -> list:
    """Run the chosen strategy as its own process and return its picks."""
    script = DELEGATE_SCRIPTS.get(delegate)
    if not script or not os.path.exists(script):
        raise SystemExit(f"[momentum] unknown or missing delegate strategy: {delegate!r}")

    tmp = f'/tmp/momentum_{delegate}_{this_date}_{os.getpid()}.json'
    child_env = dict(os.environ)
    child_env['PICK_OUTPUT'] = tmp
    child_env.pop('IMOB_SCOPED_OUT', None)
    try:
        # Force the DELEGATE's .env section in, and tell the child which keys to drop again
        # after its own flat load_dotenv() — otherwise it inherits the other arm's sizing.
        neutralized = []
        applied = apply_strategy_env(delegate, environ=child_env, neutralized=neutralized)
        export_scoped_out(neutralized, child_env)
        logger.info(f"[momentum] delegate {delegate}: .env section applied: {applied}"
                    f"{f'; scoped out {neutralized}' if neutralized else ''}")
    except Exception as e:
        logger.warning(f"[momentum] could not apply {delegate}'s .env section: {e}")

    cmd = [sys.executable, script, this_date, *flags]
    logger.info(f"[momentum] running delegate: {' '.join(cmd)}")
    res = subprocess.run(cmd, env=child_env, capture_output=True, text=True)

    if res.returncode != 0 or not os.path.exists(tmp):
        logger.error(f"[momentum] delegate {delegate} rc={res.returncode}")
        logger.error(f"[momentum] stdout tail:\n{res.stdout[-3000:]}")
        logger.error(f"[momentum] stderr tail:\n{res.stderr[-3000:]}")
        raise SystemExit(f"[momentum] delegate {delegate} failed for {this_date}")

    with open(tmp) as f:
        picks = json.load(f).get('selected_stocks', [])
    try:
        os.remove(tmp)
    except OSError:
        pass
    logger.info(f"[momentum] delegate {delegate} returned {len(picks)} picks")
    return picks


def _audit(this_date: str, month: str, delegate: str, row: dict) -> None:
    try:
        fresh = not os.path.exists(AUDIT_CSV)
        with open(AUDIT_CSV, 'a', newline='') as f:
            w = csv.writer(f)
            if fresh:
                w.writerow(['target_date', 'month', 'delegate', 'trailing_month',
                            'trail_flow_pct', 'trail_turn_pct', 'reason'])
            w.writerow([this_date, month, delegate, row.get('trailing_month', ''),
                        row.get('trail_flow_pct', ''), row.get('trail_turn_pct', ''),
                        row.get('reason', 'fallback')])
    except Exception as e:      # an audit miss must never fail a pick
        logger.debug(f"[momentum] audit write failed: {e}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    flags = [a for a in argv if a.startswith('--')]
    args = [a for a in argv if not a.startswith('--')]

    _raw = args[0] if args else datetime.now().strftime('%Y%m%d')
    this_date = convert_trade_date(_raw) or _raw
    month = this_date[:6]

    output_file = os.environ.get('PICK_OUTPUT') or '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_switch_momentum_tmp.json'

    delegate, row = delegate_for(month, load_schedule())
    logger.info(f"[momentum] {this_date}: month={month} -> {delegate}"
                f" (trailing {row.get('trailing_month', '-')}: flow {row.get('trail_flow_pct', '-')}%,"
                f" turning {row.get('trail_turn_pct', '-')}%)")

    picks = run_delegate(delegate, this_date, flags)

    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': picks}, f)
    _audit(this_date, month, delegate, row)
    logger.info(f"[momentum] saved {len(picks)} picks to {output_file} (via {delegate})")
