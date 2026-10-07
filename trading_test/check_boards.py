#!/usr/bin/env python3
"""Did the price-limit board actually decide any of the strategy's buys?

`check_fills.py` asks whether each printed fill COULD have happened. This asks the prior question:
for the stocks the strategy picked, was the day a limit-board day at all — i.e. does the policy in
`backtest/utils/limit_board.py` change any decision, or is it a guard nobody ever hits?

Reads the live pick files (`backtest/results/daily/pick_stocks_*.json`, one per session, each naming
the `target_trading_date` it buys on) and classifies that day for every picked symbol from the
cached bars:

    normal       trades inside the band — nothing to do
    up_sealed    low  >= limit-up   — SEALED at the top: no sellers, a BUY cannot fill
    up_open      open >= limit-up   — open at the top of the band: a +10%/+20% entry
    down_sealed  high <= limit-down — SEALED at the bottom: no buyers, a SELL cannot fill
    down_open    open <= limit-down — crash open: voids the momentum premise

Run:  .venv/bin/python trading_test/check_boards.py [--range 20260901 20260930] [--out PATH]
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, 'trading_test')):
    if p not in sys.path:
        sys.path.insert(0, p)

from bars import BarStore                                            # noqa: E402
from backtest.utils.limit_board import board_state, buy_block_reason  # noqa: E402
from backtest.utils.trading_calendar import get_trading_days_before   # noqa: E402

PICK_GLOB = os.path.join(ROOT, 'backtest', 'results', 'daily', 'pick_stocks_*.json')


def load_picks(date_from: str | None, date_to: str | None) -> list[tuple[str, str]]:
    """[(buy_date, symbol)] from the pick files, filtered to the range."""
    out: list[tuple[str, str]] = []
    for path in sorted(glob.glob(PICK_GLOB)):
        try:
            with open(path, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        day = str(d.get('target_trading_date') or '').replace('-', '')
        if not day:
            continue
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        for s in d.get('selected_stocks') or []:
            sym = s.get('symbol') if isinstance(s, dict) else s
            if sym:
                out.append((day, sym))
    return out


def classify(store: BarStore, day: str, code: str):
    """(state, detail dict, block reason or None) for one pick on the day it would be bought."""
    bars = store.intraday(code, day)
    if not bars:
        return 'no-bars', {}, None
    prev_day = get_trading_days_before(day, 1)
    prev_close = store.prev_close(code, prev_day)
    o = bars[0]['open']
    h = max(b['high'] for b in bars)
    lo = min(b['low'] for b in bars)
    state = board_state(o, h, lo, prev_close, code)
    gap = (o - prev_close) / prev_close if prev_close else 0.0
    return state, {'open': o, 'high': h, 'low': lo, 'prev_close': prev_close, 'gap': gap}, buy_block_reason(state)


def load_orders(orders_dir: str, date_from: str | None, date_to: str | None) -> list[dict]:
    """The EXECUTED book: one smart_orders_<target_trading_date>.json per session."""
    out = []
    for path in sorted(glob.glob(os.path.join(orders_dir, 'smart_orders_*.json'))):
        try:
            with open(path, encoding='utf-8') as f:
                d = json.load(f)
        except (OSError, ValueError):
            continue
        day = str(d.get('target_trading_date') or '').replace('-', '')
        if not day or (date_from and day < date_from) or (date_to and day > date_to):
            continue
        for o in d.get('smart_orders') or []:
            if o.get('symbol'):
                out.append({'day': day, 'code': o['symbol'], 'prev_close': o.get('current_price'),
                            'planned_limit': o.get('buy_price'), 'name': o.get('name', ''),
                            'take_profit': o.get('sell_take_profit_price')})
    return out


def population_control(store: BarStore, days: list[str], sample: int) -> Counter:
    """Base rate over the whole cache. If the classifier could not see a board, the pick-universe
    zeros below would be an artefact rather than a finding."""
    cache = os.path.join(ROOT, 'shared', 'data_cache', 'm1m2_min30')
    codes = sorted(f[:-4] for f in os.listdir(cache) if f.endswith('.pkl') and not f.endswith('_30.pkl'))
    tally: Counter[str] = Counter()
    step = max(1, len(codes) // max(1, sample))
    for code in codes[::step]:
        for day in days:
            state, _, _ = classify(store, day, code)
            tally[state] += 1
    return tally


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--range', nargs=2, metavar=('FROM', 'TO'), default=['20260101', '20261231'])
    ap.add_argument('--orders-dir', default=None,
                    help='measure the EXECUTED book from smart_orders_*.json in this run directory')
    ap.add_argument('--population', type=int, default=0, metavar='N',
                    help='also classify N sampled cache symbols x every session, as a control')
    ap.add_argument('--out', default=os.path.join(ROOT, 'trading_test', 'results', 'limit_board_exposure.md'))
    args = ap.parse_args()

    store = BarStore()
    if args.orders_dir:
        items = load_orders(args.orders_dir, args.range[0], args.range[1])
        picks: list[tuple[str, str]] = [(i['day'], i['code']) for i in items]
        source = f"executed book, {os.path.basename(args.orders_dir.rstrip('/'))}/smart_orders_*.json"
    else:
        items = []
        picks = load_picks(args.range[0], args.range[1])
        source = os.path.basename(PICK_GLOB)
    if not picks:
        print(f"No picks for {args.range[0]}..{args.range[1]}")
        return 1

    tally: Counter[str] = Counter()
    blocked: list[tuple[str, str, str, dict]] = []
    limit_gap_up: list[tuple[str, str, float, float]] = []
    for day, code in picks:
        state, detail, reason = classify(store, day, code)
        tally[state] += 1
        if reason:
            blocked.append((day, code, state, detail))
        # Did the engine's own limit even look at a gap-up open? Its planned limit normally sits
        # BELOW the previous close (buy the dip), so a gap-up open cannot fill in the backtest —
        # which is exactly what a live limit_up bid (the whole band) would buy anyway.
        if detail.get('prev_close') and detail.get('open'):
            if detail['open'] > detail['prev_close']:
                limit_gap_up.append((day, code, detail['prev_close'], detail['open']))

    lines = [f"# Price-limit board exposure — {args.range[0]}..{args.range[1]}", '',
             f"{len(picks)} pick-days from {source} ({len({d for d, _ in picks})} sessions)", '',
             '| state | meaning | count |', '|---|---|---|',
             '| normal | trades inside the band — no guard fires | %d |' % tally.get('normal', 0),
             '| up_sealed | sealed at limit-up: no sellers, BUY impossible | %d |' % tally.get('up_sealed', 0),
             '| up_open | open at limit-up: entry at the top of the band | %d |' % tally.get('up_open', 0),
             '| down_sealed | sealed at limit-down: no buyers, SELL impossible | %d |' % tally.get('down_sealed', 0),
             '| down_open | crash open at limit-down | %d |' % tally.get('down_open', 0),
             '| no-bars | no cached bars for the day | %d |' % tally.get('no-bars', 0), '']
    n_block = sum(tally.get(s, 0) for s in ('up_sealed', 'up_open', 'down_sealed', 'down_open'))
    lines += [f"**{n_block} of {len(picks)} pick-days ({n_block / len(picks):.1%}) sit on a limit board** "
              f"a BUY would be refused on.", '']
    if blocked:
        lines += ['| buy date | code | state | prev close | open | gap | why refused |',
                  '|---|---|---|---|---|---|---|']
        for day, code, state, d in sorted(blocked):
            lines.append(f"| {day} | {code} | {state} | {d.get('prev_close')} | {d.get('open')} | "
                         f"{d.get('gap', 0):+.2%} | {buy_block_reason(state)} |")
        lines.append('')
    else:
        lines += ['No pick-day sat on a limit board, so the new guard changes no historical decision.',
                  'It is a correctness guard for the tail (a sealed board, a crash open), not a',
                  'performance lever on this sample.', '']
    if items:
        gapped = [i for i in items if i['planned_limit'] and i['prev_close']
                  and i['prev_close'] > i['planned_limit']]
        open_up = [i for i in limit_gap_up]
        lines += ['## Sanity: does the engine plan to buy BELOW the previous close?', '',
                  f"- {len(gapped)}/{len(items)} orders have a planned limit below the previous close "
                  f"(a dip-buy limit) — mean "
                  f"{(sum((i['prev_close'] - i['planned_limit']) / i['prev_close'] for i in gapped) / len(gapped) if gapped else 0):.2%} below.",
                  f"- {len(open_up)}/{len(items)} orders faced an open ABOVE the previous close; the "
                  f"backtest only fills when open <= planned limit, so those never fill there.",
                  '- A live `TRADING_BUY_LIMIT_MODE=limit_up` bid covers the WHOLE band, so it fills '
                  'those gap-up days anyway — at the open. That is the divergence to watch.', '']
    if args.population:
        days = sorted({d for d, _ in picks})
        pop = population_control(store, days, args.population)
        n_pop = sum(pop.values())
        n_pop_board = sum(pop.get(s, 0) for s in ('up_sealed', 'up_open', 'down_sealed', 'down_open'))
        lines += ['## Control: base rate over the whole cache', '',
                  f"Same sessions, {n_pop} sampled symbol-days: **{n_pop_board} ({n_pop_board / max(1, n_pop):.1%})** "
                  'on a board.',
                  '| state | count |', '|---|---|']
        lines += [f"| {k} | {v} |" for k, v in pop.most_common()]
        lines.append('')
    lines += ['_What this does NOT measure: the engine also drops buys on cash limits, already-held',
              'names and liquidity caps, so this is the board exposure of the order universe, not of',
              'the filled book. A `no-bars` day is missing cache, not a board state._', '']

    report = '\n'.join(lines)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(report)
    print(report)
    print(f"written: {args.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
