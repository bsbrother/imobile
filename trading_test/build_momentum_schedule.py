#!/usr/bin/env python3
"""Build the month -> delegate schedule for `ts_switch_momentum`.

RULE (fixed in advance, one parameter): at the end of month M-1, run in month M whichever
arm has the higher REALISED return in month M-1. Ties and the first month (no history) go to
`--default`. Nothing else is consulted — no index, no regime, no calendar.

This is deliberately the dumbest rule that fit the screen: over 2025-01..2026-09 the
trailing-1-month rule compounded +110.7% on stitched monthly returns, against +47.5% for
always-turning and +40.9% for always-flow (perfect foresight: +183.4%). A rule that needs
more inputs than that has more room to be wrong.

The trailing number is each arm's own standalone equity for that month — the shadow book a
live desk would keep. Caveat, stated because it matters: the shadow arm's month is measured
with the whole account, while the running arm may hold carried-over positions, so the first
days after a switch are under-invested in the real portfolio. The switch run measures that.

Usage:
    python trading_test/build_momentum_schedule.py --out /tmp/momentum_schedule.csv
"""
from __future__ import annotations

import argparse
import csv
import glob
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, 'backtest', 'results')

# Explicit windows, one dir per (strategy, year): a `_prev*` / `_fill` / `_insert7` sibling is
# a different reading of the same window and must never be mixed in.
ARMS = {
    'ts_7AZ_96MA_flow_review': ['20250101_20251231_ts_7AZ_96MA_flow_review',
                                '20260101_20260930_ts_7AZ_96MA_flow_review'],
    'ts_turning_point':        ['20250101_20251231_ts_turning_point_conc2',
                                '20260101_20260930_ts_turning_point_conc2'],
}


def monthly_equity(directory: str) -> dict[str, float]:
    """Month -> last True Total Portfolio Value of that month, from the order reports."""
    out: dict[str, float] = {}
    for p in sorted(glob.glob(os.path.join(directory, 'report_orders_*.md'))):
        day = os.path.basename(p)[len('report_orders_'):len('report_orders_') + 8]
        text = open(p, encoding='utf-8', errors='replace').read()
        m = re.search(r'\*\*True Total Portfolio Value:\*\* ¥([\d,\.]+)', text)
        if m:
            out[day[:6]] = float(m.group(1).replace(',', ''))
    return out


def arm_months(dirs: list[str]) -> dict[str, float]:
    """Month -> return %, per window.

    Each window restarts from ¥600k, so the chain must restart there too — chaining a 2026
    month onto the 2025 window's closing equity turned January's -1.2% into -28.4%.
    """
    rets: dict[str, float] = {}
    for name in dirs:
        d = os.path.join(RESULTS, name)
        equity = monthly_equity(d)
        if not equity:
            raise SystemExit(f'missing monthly equity in {d} — run that arm first')
        prev = 600000.0
        for m in sorted(equity):
            rets[m] = (equity[m] / prev - 1) * 100
            prev = equity[m]
    return rets


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default='/tmp/momentum_schedule.csv')
    ap.add_argument('--default', default='ts_turning_point',
                    help='delegate for the first month and any month without history')
    args = ap.parse_args()

    per_arm = {name: arm_months(dirs) for name, dirs in ARMS.items()}
    months = sorted(set().union(*per_arm.values()))
    if not months:
        raise SystemExit('no monthly equity found — run the standalone arms first')

    rows = []
    for i, month in enumerate(months):
        prev = months[i - 1] if i else None
        trail = {name: per_arm[name].get(prev) for name in ARMS} if prev else {}
        if not prev or any(v is None for v in trail.values()):
            delegate = args.default
            why = 'no history'
        else:
            numeric = {k: v for k, v in trail.items() if v is not None}
            best = max(numeric, key=lambda k: numeric[k])
            delegate = best
            why = 'trailing 1m'
        rows.append({
            'month': month,
            'delegate': delegate,
            'trailing_month': prev or '',
            'trail_flow_pct': '' if trail.get('ts_7AZ_96MA_flow_review') is None
                              else f"{trail['ts_7AZ_96MA_flow_review']:.2f}",
            'trail_turn_pct': '' if trail.get('ts_turning_point') is None
                              else f"{trail['ts_turning_point']:.2f}",
            'reason': why,
        })

    with open(args.out, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)

    switches = sum(1 for a, b in zip(rows, rows[1:]) if a['delegate'] != b['delegate'])
    print(f'{len(rows)} months, {switches} switches -> {args.out}')
    for r in rows:
        print(f"  {r['month']}  {r['delegate']:24s} trail flow {r['trail_flow_pct'] or '-':>7s}"
              f"  turn {r['trail_turn_pct'] or '-':>7s}  ({r['reason']})")


if __name__ == '__main__':
    sys.exit(main())
