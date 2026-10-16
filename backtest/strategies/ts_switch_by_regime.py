"""
ts_switch_by_regime: regime-conditional strategy switching.

Built from `trading_test/regime_switch_study.py`. Per-YEAR strategy selection fails (2023 and
2026 were both down years with opposite winners), but the per-REGIME switch is a real signal:
`ts_ao_er` is a divergence/mean-reversion picker that wins the oversold bear tape, while the
production default chases LHB institutional flow and wins the trending bull tape.

RULE (defaults): 3+ consecutive BEAR sessions -> `ts_ao_er`; a BULL tape -> the production
default; everything else -> the production default.

Measured by the study (daily-return compounding proxy): in-sample 2023-2026 +78.23% vs the
static default's -15.98%; walk-forward (fit on 2023-24, apply to 2025-26) +65.18% vs +43.90%.
This script is the *real* version of that proxy: one continuous engine run, so fills, carry
and turnover are those of an actual portfolio.

STATE IS LAGGED. The regime and the bear-run are read `SWITCH_LAG_DAYS` (default 1) trading
days back, matching LIVE: the 08:30 pre-market pick cannot see today's close. (The engine's
own `market_pattern` in the pick file stays unlagged and still drives exits — unchanged from
every other run, so results remain comparable to the existing baselines.)

The delegate runs as a SUBPROCESS with its own `.env` section forced in
(`apply_strategy_env(..., environ=child_env)`), so it behaves exactly as in a native run of
that strategy — its picks are byte-identical for the same date.

Env knobs (read at the point of use; no `.env` section is needed — keys owned by no section
pass through untouched):
    SWITCH_BEAR_STRATEGY    default ts_ao_er
    SWITCH_BULL_STRATEGY    default ts_7AZ_96MA_flow_review
    SWITCH_NORMAL_STRATEGY  default ts_7AZ_96MA_flow_review
    SWITCH_BEAR_RUN_MIN     default 3
    SWITCH_BEAR_MODE        bear_run | regime   (default bear_run)
    SWITCH_LAG_DAYS         default 1
    SWITCH_DROP_TRIGGER_PCT e.g. -4 — ALSO fire the bear delegate when the trailing 5-session
                            index change is at or below this (default: unset = off, rule unchanged)
    SWITCH_DROP_LOOKBACK    default 5 (the framing of that drop)
    SWITCH_AUDIT_CSV        default /tmp/switch_decisions.csv

Usage:
    python backtest/strategies/ts_switch_by_regime.py YYYYMMDD [--no-search] [--no-ai]
"""

import os
import sys
import json
import subprocess
from datetime import datetime

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest.utils.trading_calendar import get_trading_days_before, convert_trade_date
from backtest.utils.market_regime import detect_market_regime
from backtest.utils.strategy_env import apply_strategy_env
from backtest.utils.logging_config import configure_logger

load_dotenv()
configure_logger(log_level=os.getenv("LOG_LEVEL", default="INFO"),
                 log_path=os.getenv("LOG_PATH", default="./logs"))

BEAR_STRATEGY = os.getenv('SWITCH_BEAR_STRATEGY', 'ts_ao_er')
BULL_STRATEGY = os.getenv('SWITCH_BULL_STRATEGY', 'ts_7AZ_96MA_flow_review')
NORMAL_STRATEGY = os.getenv('SWITCH_NORMAL_STRATEGY', 'ts_7AZ_96MA_flow_review')
BEAR_RUN_MIN = int(os.getenv('SWITCH_BEAR_RUN_MIN', '3'))
BEAR_MODE = os.getenv('SWITCH_BEAR_MODE', 'bear_run')
LAG_DAYS = int(os.getenv('SWITCH_LAG_DAYS', '1'))
# Optional second trigger. Unset (the default) keeps the decision byte-identical to the
# validated rule — this is an opt-in bolt-on, not a change to the shipped behaviour.
DROP_TRIGGER_PCT = os.getenv('SWITCH_DROP_TRIGGER_PCT')      # e.g. '-4' == a -4% drop
DROP_LOOKBACK = int(os.getenv('SWITCH_DROP_LOOKBACK', '5'))  # sessions framed by that drop

REGIME_CACHE = os.getenv('SWITCH_REGIME_CACHE', '/tmp/switch_regime_cache.json')
AUDIT_CSV = os.getenv('SWITCH_AUDIT_CSV', '/tmp/switch_decisions.csv')
MAX_PROBE = 10  # how far back to walk looking for the start of a bear run

DELEGATE_SCRIPTS = {
    'ts_ao_er': 'backtest/strategies/ts_ao_er.py',
    'ts_hma': 'backtest/strategies/ts_hma.py',
    'ts_longup': 'backtest/strategies/ts_longup.py',
    'ts_multi_swing_defensive': 'backtest/strategies/ts_multi_swing_defensive.py',
    'ts_7AZ_96MA_flow_review': 'backtest/strategies/ts_7AZ_96MA_flow_review.py',
    'ts_7AZ_96MA_flow_review_longterm': 'backtest/strategies/ts_7AZ_96MA_flow_review_longterm.py',
}


def _load_cache() -> dict:
    try:
        with open(REGIME_CACHE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save_cache(cache: dict) -> None:
    try:
        with open(REGIME_CACHE, 'w') as f:
            json.dump(cache, f, indent=0, sort_keys=True)
    except Exception as e:  # a cache miss only costs a re-detect, never correctness
        logger.debug(f"[switch] regime cache write failed: {e}")


def _regime_for(date: str, cache: dict) -> str:
    """The engine's own regime label for `date` (cached across sessions)."""
    if date in cache:
        return cache[date]
    try:
        regime = detect_market_regime(date).get('regime', 'normal')
    except Exception as e:
        logger.warning(f"[switch] regime detect failed for {date} ({e}); assuming normal")
        regime = 'normal'
    cache[date] = regime
    return regime


def _bear_run_ending(date: str, cache: dict) -> int:
    """Consecutive bear sessions ending at `date` (0 when `date` is not bear)."""
    n = 0
    cursor = date
    for _ in range(MAX_PROBE):
        if _regime_for(cursor, cache) != 'bear':
            break
        n += 1
        cursor = get_trading_days_before(cursor, 1)
    return n


def _index_drop_pct(date: str) -> "float | None":
    """Trailing DROP_LOOKBACK-session SSE change (%) at `date`, or None if unavailable."""
    from backtest import data_provider
    try:
        start = get_trading_days_before(date, DROP_LOOKBACK + 5)
        df = data_provider.get_index_data('000001.SH', start, date)
        if df is None or len(df) <= DROP_LOOKBACK:
            return None
        close = df['close'].astype(float)
        return (close.iloc[-1] / close.iloc[-(DROP_LOOKBACK + 1)] - 1) * 100
    except Exception as e:
        logger.warning(f"[switch] index drop lookup failed for {date}: {e}")
        return None


def _choose(regime: str, bear_run: int, drop_pct: "float | None" = None) -> str:
    if BEAR_MODE == 'regime':
        if regime == 'bear':
            return BEAR_STRATEGY
        return BULL_STRATEGY if regime == 'bull' else NORMAL_STRATEGY
    if bear_run >= BEAR_RUN_MIN:
        return BEAR_STRATEGY
    if DROP_TRIGGER_PCT is not None and drop_pct is not None and drop_pct <= float(DROP_TRIGGER_PCT):
        return BEAR_STRATEGY
    return BULL_STRATEGY if regime == 'bull' else NORMAL_STRATEGY


def _audit(target_date: str, state_date: str, regime: str, bear_run: int, delegate: str,
           drop_pct: "float | None" = None) -> None:
    try:
        fresh = not os.path.exists(AUDIT_CSV)
        drop = '' if drop_pct is None else f'{drop_pct:.2f}'
        with open(AUDIT_CSV, 'a') as f:
            if fresh:
                f.write('target_date,state_date,regime,bear_run,delegate,mode,drop_pct\n')
            f.write(f'{target_date},{state_date},{regime},{bear_run},{delegate},{BEAR_MODE},{drop}\n')
    except Exception as e:
        logger.debug(f"[switch] audit write failed: {e}")


def run_delegate(delegate: str, this_date: str, flags: list) -> list:
    """Run the chosen strategy as its own process and return its picks."""
    script = DELEGATE_SCRIPTS.get(delegate)
    if not script or not os.path.exists(script):
        raise SystemExit(f"[switch] unknown or missing delegate strategy: {delegate!r}")

    tmp = f'/tmp/switch_{delegate}_{this_date}_{os.getpid()}.json'
    child_env = dict(os.environ)
    child_env['PICK_OUTPUT'] = tmp
    try:
        # Force the DELEGATE's .env section in, so it behaves like a native run of it.
        applied = apply_strategy_env(delegate, environ=child_env, neutralized=[])
        logger.info(f"[switch] delegate {delegate}: .env section keys applied: {applied}")
    except Exception as e:
        logger.warning(f"[switch] could not apply {delegate}'s .env section: {e}")

    cmd = [sys.executable, script, this_date, *flags]
    logger.info(f"[switch] running delegate: {' '.join(cmd)}")
    res = subprocess.run(cmd, env=child_env, capture_output=True, text=True)

    if res.returncode != 0 or not os.path.exists(tmp):
        logger.error(f"[switch] delegate {delegate} rc={res.returncode}")
        logger.error(f"[switch] stdout tail:\n{res.stdout[-3000:]}")
        logger.error(f"[switch] stderr tail:\n{res.stderr[-3000:]}")
        raise SystemExit(f"[switch] delegate {delegate} failed for {this_date}")

    with open(tmp) as f:
        picks = json.load(f).get('selected_stocks', [])
    try:
        os.remove(tmp)
    except OSError:
        pass
    logger.info(f"[switch] delegate {delegate} returned {len(picks)} picks")
    return picks


if __name__ == "__main__":
    argv = sys.argv[1:]
    flags = [a for a in argv if a.startswith('--')]
    args = [a for a in argv if not a.startswith('--')]

    _raw = args[0] if args else datetime.now().strftime('%Y%m%d')
    this_date = convert_trade_date(_raw) or _raw  # `convert_trade_date` may give None

    output_file = os.environ.get('PICK_OUTPUT') or '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_switch_by_regime_tmp.json'

    cache = _load_cache()

    # The STATE is lagged: on the pick for D we may only use sessions up to D-1.
    state_date = this_date
    for _ in range(max(0, LAG_DAYS)):
        state_date = get_trading_days_before(state_date, 1)

    regime = _regime_for(state_date, cache)
    bear_run = _bear_run_ending(state_date, cache)
    drop_pct = _index_drop_pct(state_date) if DROP_TRIGGER_PCT is not None else None
    delegate = _choose(regime, bear_run, drop_pct)

    logger.info(f"[switch] {this_date}: state={state_date} regime={regime} "
                f"bear_run={bear_run} drop={drop_pct} mode={BEAR_MODE} -> {delegate}")

    picks = run_delegate(delegate, this_date, flags)

    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': picks}, f)

    _save_cache(cache)
    _audit(this_date, state_date, regime, bear_run, delegate)
    logger.info(f"[switch] saved {len(picks)} picks to {output_file} (via {delegate})")
