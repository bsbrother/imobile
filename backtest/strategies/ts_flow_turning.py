"""ts_flow_turning: the production flow picks, with turning-point picks as a filler.

Why: `ts_turning_point` measured only +1.78% standalone, and the gap decomposition
(docs/strategies/ts_turning_point.md) showed it is a real but small edge — ~0.27%
per round trip on ~125 trips, versus the flow strategy's 0.96% on 647. As a rival
it cannot compete; as an EXTRA ENTRY into a portfolio that already holds the flow
edge, its ~1-2 picks/day cost nothing when the flow strategy has already filled
every slot, and add exposure on the ~36% of 2026 sessions where it had not
(65 of 181 days saw fewer than 10 flow picks, 19 days saw none).

RULE: run the base strategy first. Fill the free slots with the extra strategy's
picks, deduped against the base list, capped at `TURN_EXTRA_MAX` (default 2). The
base picks keep their order and rank; extras are appended with ranks continuing
the sequence and scores mapped BELOW the base minimum, so any score- or
rank-weighted sizing downstream still treats them as fringe positions.

Both delegates run as SUBPROCESSES with their own `.env` section forced in
(`apply_strategy_env(..., environ=child_env)`), so each behaves exactly as in a
native run of that strategy — picks are byte-identical for the same date.

Env knobs (read at the point of use; no `.env` section needed):
    TURN_BASE_STRATEGY   default ts_7AZ_96MA_flow_review
    TURN_EXTRA_STRATEGY  default ts_turning_point
    TURN_EXTRA_MAX       default 2   (extras per session, 0 disables the merge)
    TURN_EXTRA_REGIME    default all  e.g. 'bull,normal' to skip extras in a weak tape
    TURN_MODE            fill | replace | insert (default fill)
                         fill    = append extras only when the base list is SHORTER than the
                                   regime limit (observed: the flow picker usually emits 23-27
                                   names for a 12-slot budget, so this rarely fires)
                         replace = extras DISPLACE the tail of the base list, so the two extras
                                   get bought inside the slot budget instead of queueing behind
                                   12 names that will never all be funded
                         insert  = extras take the slots at TURN_INSERT_AT and the base list
                                   continues after them (tail truncated). Measured on the
                                   2026 baseline: picks are truncated to the regime limit and
                                   only 6.2 of 8.2 average picks are actually FUNDED (cash runs
                                   out first — `smart_orders` carries fewer orders than picks on
                                   120 of 181 sessions), so both `fill` and `replace` put the
                                   extras where no money reaches them. Insert at the funding
                                   FRONTIER (default 7) to make the swap real.
    TURN_INSERT_AT       default 7  (1-based rank the first extra takes, insert mode only)

Usage:
    python backtest/strategies/ts_flow_turning.py YYYYMMDD [--no-search] [--no-ai]
"""

import json
import os
import subprocess
import sys
from datetime import datetime

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest.utils.market_regime import detect_market_regime
from backtest.utils.strategy_env import apply_strategy_env
from backtest.utils.logging_config import configure_logger
from backtest.utils.trading_calendar import convert_trade_date, get_trading_days_before

load_dotenv()
configure_logger(log_level=os.getenv("LOG_LEVEL", default="INFO"),
                 log_path=os.getenv("LOG_PATH", default="./logs"))

BASE_STRATEGY = os.getenv('TURN_BASE_STRATEGY', 'ts_7AZ_96MA_flow_review')
EXTRA_STRATEGY = os.getenv('TURN_EXTRA_STRATEGY', 'ts_turning_point')
EXTRA_MAX = int(os.getenv('TURN_EXTRA_MAX', '2'))
EXTRA_REGIMES = [r.strip().lower() for r in os.getenv('TURN_EXTRA_REGIME', '').split(',') if r.strip()]
MODE = os.getenv('TURN_MODE', 'fill').strip().lower()
INSERT_AT = int(os.getenv('TURN_INSERT_AT', '7'))

DELEGATE_SCRIPTS = {
    'ts_7AZ_96MA_flow_review': 'backtest/strategies/ts_7AZ_96MA_flow_review.py',
    'ts_turning_point': 'backtest/strategies/ts_turning_point.py',
}

# Position limits, same knobs and defaults as engine.py / cli.py.
REGIME_LIMITS = {
    'bull': int(os.getenv('MAX_POS_BULL', '12')),
    'normal': int(os.getenv('MAX_POS_NORMAL', '10')),
    'volatile': int(os.getenv('MAX_POS_VOLATILE', '8')),
    'bear': int(os.getenv('MAX_POS_BEAR', '5')),
}


def wants_extras(mode: str, n_base: int, limit: int, extra_max: int) -> bool:
    """Whether the extra strategy is worth running at all for this session.

    `fill` can only place an extra in a slot the base left EMPTY, so when the base
    already fills the budget the whole turning-point pass is wasted work (~20s per
    session). `insert` and `replace` always have somewhere to put one.
    """
    if extra_max <= 0:
        return False
    if mode == 'fill':
        return n_base < int(limit)
    return True


def merge_picks(base: list, extras: list, limit: int, extra_max: int) -> list:
    """Base picks first, extras filling the free slots. Pure — the unit tests target it.

    * base order/rank/score are preserved untouched
    * extras are deduped against the base list (a name already held needs no second entry)
    * at most `limit - len(base)` extras, and never more than `extra_max`
    * extras' scores are squeezed into (0, min(base score)) so any score-based sizing
      downstream still ranks them below every base pick
    """
    base = list(base or [])
    out = list(base)
    if extra_max <= 0 or not extras:
        return out
    free = max(0, int(limit) - len(base))
    if free <= 0:
        return out
    taken = {p.get('symbol') for p in base}
    cap = min(free, extra_max)

    ceilings = [float(p.get('score') or 0.0) for p in base if p.get('score') is not None]
    ceiling = min(ceilings) if ceilings else 1.0
    ceiling = ceiling if ceiling > 0 else 1.0

    added = 0
    for p in extras:
        if added >= cap:
            break
        sym = p.get('symbol')
        if not sym or sym in taken:
            continue
        q = dict(p)
        q['rank'] = len(out) + 1
        # spread the extras over (0.35, 0.95) x the base minimum, keeping their order
        frac = 0.95 - (0.6 * (added / max(1, cap)))
        q['score'] = round(ceiling * frac, 2)
        out.append(q)
        taken.add(sym)
        added += 1
    return out


def merge_replace(base: list, extras: list, limit: int, extra_max: int) -> list:
    """Extras displace the TAIL of the base list, so they land inside the slot budget.

    The flow picker emits 23-27 names for a 12-slot budget, so appended extras would
    never be funded: they queue behind two dozen names that cannot all be bought.
    Here the base keeps its best `limit - extra_max` names and the extras take the
    last slots. Base order among the kept names is unchanged; the displaced tail is
    dropped from the pick file (those names were never going to be funded anyway).
    """
    base = list(base or [])
    if extra_max <= 0 or not extras:
        return base
    keep = max(0, int(limit) - int(extra_max))
    head = base[:keep]
    taken = {p.get('symbol') for p in head}
    ceilings = [float(p.get('score') or 0.0) for p in head if p.get('score') is not None]
    ceiling = min(ceilings) if ceilings else 1.0
    ceiling = ceiling if ceiling > 0 else 1.0

    out = list(head)
    added = 0
    for p in extras:
        if added >= extra_max:
            break
        sym = p.get('symbol')
        if not sym or sym in taken:
            continue
        q = dict(p)
        q['rank'] = len(out) + 1
        q['score'] = round(ceiling * (0.95 - 0.6 * (added / max(1, extra_max))), 2)
        out.append(q)
        taken.add(sym)
        added += 1
    return out


def merge_insert(base: list, extras: list, limit: int, extra_max: int, at: int = 7) -> list:
    """Extras take the slots at `at`; the base list continues after them.

    Funding is decided by LIST ORDER only — `cli.py` builds `symbols` straight from the
    pick file and never reads `score` for sizing — and cash runs out before the end of
    the list (2026 baseline: 6.2 funded orders against 8.2 picks, fewer orders than
    picks on 120/181 sessions). Appending extras (`fill`) or swapping them into the
    tail (`replace`) therefore parks them where no money reaches them; `at` puts them
    at the funding frontier, where one extra displaces one base pick that a cash-limited
    run would otherwise have bought.
    """
    base = list(base or [])
    if extra_max <= 0 or not extras:
        return base
    pos = max(1, min(int(at), max(1, int(limit))))
    head, tail = base[:pos - 1], base[pos - 1:]
    taken = {p.get('symbol') for p in base}
    ceilings = [float(p.get('score') or 0.0) for p in base if p.get('score') is not None]
    ceiling = min(ceilings) if ceilings else 1.0
    ceiling = ceiling if ceiling > 0 else 1.0

    out = list(head)
    added = 0
    for p in extras:
        if added >= extra_max:
            break
        sym = p.get('symbol')
        if not sym or sym in taken:
            continue
        q = dict(p)
        q['rank'] = len(out) + 1
        q['score'] = round(ceiling * (0.95 - 0.6 * (added / max(1, extra_max))), 2)
        out.append(q)
        taken.add(sym)
        added += 1
    out.extend(tail)
    # Deliberately NOT truncated to `limit`: the engine applies its OWN slot budget after
    # the review overlay may have raised it (`BACKTEST_MAX_POSITIONS x pos_scale` fired 105
    # times in 2026). Truncating here to the static MAX_POS_* default would strip names the
    # control arm still funds, so the A/B would measure the truncation, not the swap.
    return out


def run_delegate(delegate: str, this_date: str, flags: list) -> list:
    """Run a strategy as its own process (its own .env section) and return its picks."""
    script = DELEGATE_SCRIPTS.get(delegate)
    if not script or not os.path.exists(script):
        raise SystemExit(f"[flow_turn] unknown or missing delegate: {delegate!r}")

    tmp = f'/tmp/flow_turn_{delegate}_{this_date}_{os.getpid()}.json'
    child_env = dict(os.environ)
    child_env['PICK_OUTPUT'] = tmp
    try:
        applied = apply_strategy_env(delegate, environ=child_env, neutralized=[])
        logger.info(f"[flow_turn] delegate {delegate}: .env section keys applied: {applied}")
    except Exception as e:
        logger.warning(f"[flow_turn] could not apply {delegate}'s .env section: {e}")

    cmd = [sys.executable, script, this_date, *flags]
    res = subprocess.run(cmd, env=child_env, capture_output=True, text=True)
    if res.returncode != 0 or not os.path.exists(tmp):
        logger.error(f"[flow_turn] delegate {delegate} rc={res.returncode}")
        logger.error(f"[flow_turn] stdout tail:\n{res.stdout[-2000:]}")
        logger.error(f"[flow_turn] stderr tail:\n{res.stderr[-2000:]}")
        raise RuntimeError(f"delegate {delegate} failed for {this_date}")

    with open(tmp) as f:
        picks = json.load(f).get('selected_stocks', [])
    try:
        os.remove(tmp)
    except OSError:
        pass
    return picks


def _run(argv: list) -> int:
    flags = [a for a in argv if a.startswith('--')]
    args = [a for a in argv if not a.startswith('--')]
    raw = args[0] if args else datetime.now().strftime('%Y%m%d')
    this_date = convert_trade_date(raw) or raw

    output_file = os.environ.get('PICK_OUTPUT') or '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_flow_turning_tmp.json'

    base = run_delegate(BASE_STRATEGY, this_date, flags)

    # Extras are optional: a failure here must not cost us the base picks.
    regime, extra = 'normal', []
    if EXTRA_MAX > 0:
        try:
            regime = detect_market_regime(get_trading_days_before(this_date, 1)).get('regime', 'normal')
        except Exception as e:
            logger.warning(f"[flow_turn] regime lookup failed ({e}); assuming normal")
        if EXTRA_REGIMES and regime not in EXTRA_REGIMES:
            logger.info(f"[flow_turn] {this_date}: regime {regime} not in {EXTRA_REGIMES}, extras skipped")
        elif not wants_extras(MODE, len(base), REGIME_LIMITS.get(regime, 10), EXTRA_MAX):
            # `fill` can only add into a FREE slot, and it has none when the base already
            # covers the budget — so skip the whole turning-point pass rather than paying
            # ~20s/session to discard its output. Measured hit rate: 49 of 183 sessions.
            logger.info(f"[flow_turn] {this_date}: {len(base)} base picks >= "
                        f"{REGIME_LIMITS.get(regime, 10)} slots, no free slot — extras skipped")
        else:
            try:
                extra = run_delegate(EXTRA_STRATEGY, this_date, flags)
            except Exception as e:
                logger.warning(f"[flow_turn] {this_date}: extras unavailable ({e}); base picks only")

    limit = REGIME_LIMITS.get(regime, 10)
    if MODE == 'insert':
        merged = merge_insert(base, extra, limit, EXTRA_MAX, INSERT_AT)
    elif MODE == 'replace':
        merged = merge_replace(base, extra, limit, EXTRA_MAX)
    else:
        merged = merge_picks(base, extra, limit, EXTRA_MAX)

    # Count what actually got in, not a formula over the base length: the first version
    # logged "0 extra" for a replace-mode run that had really swapped two names in (and
    # "-1" when the turning picker returned one pick), because it compared the merged
    # length against min(len(base), limit) instead of against the kept base.
    base_syms = {p.get('symbol') for p in base}
    n_extra = sum(1 for p in merged if p.get('symbol') not in base_syms)

    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': merged,
                   'merge': {'base': BASE_STRATEGY, 'extra': EXTRA_STRATEGY, 'mode': MODE,
                             'insert_at': INSERT_AT if MODE == 'insert' else None,
                             'n_base': len(base), 'n_extra': n_extra,
                             'regime': regime, 'limit': limit}}, f)
    logger.info(f"[flow_turn] {this_date}: {len(base)} base + {n_extra} extra = {len(merged)} "
                f"picks (mode {MODE}, limit {limit}, regime {regime})")
    return 0


if __name__ == "__main__":
    sys.exit(_run(sys.argv[1:]))
