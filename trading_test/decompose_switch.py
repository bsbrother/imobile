#!/usr/bin/env python3
"""Attribute a switch run month by month: was the arm it picked the arm that worked?

For every month of the switch run this prints the delegated arm (from the decisions csv)
next to what each static arm actually returned that month. The regret column is the gap
to the better arm — an unattainable oracle, useful only as the size of the opportunity the
rule was trying to capture.

Usage:
    python trading_test/decompose_switch.py [--audit CSV] [--switch DIR]

Defaults resolve the flatten run and its decisions csv.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RESULTS = os.path.join(ROOT, 'backtest', 'results')
NAV_RE = re.compile(r'\*\*True Total Portfolio Value:\*\*\s*¥([\d,]+\.?\d*)')
DAY_RE = re.compile(r'report_orders_(\d{8})\.md$')

# Static arms, split by the year they were run in (each run starts from ¥600k cash).
ARMS = {
    'flow': {
        '2025': '20250101_20251231_ts_7AZ_96MA_flow_review',
        '2026': '20260101_20260930_ts_7AZ_96MA_flow_review',
    },
    'turn': {
        '2025': '20250101_20251231_ts_turning_point_conc2',
        '2026': '20260101_20260930_ts_turning_point_conc2',
    },
}


def monthly(path: str, init: float = 600000.0) -> dict[str, float]:
    navs: list[tuple[str, float]] = []
    for name in sorted(os.listdir(path)):
        m = DAY_RE.search(name)
        if not m:
            continue
        try:
            hit = NAV_RE.search(open(os.path.join(path, name)).read())
        except OSError:
            continue
        if hit:
            navs.append((m.group(1), float(hit.group(1).replace(',', ''))))
    out: dict[str, float] = {}
    prev = init
    for month in sorted({d[:6] for d, _ in navs}):
        last = [v for d, v in navs if d[:6] == month][-1]
        out[month] = (last / prev - 1) * 100
        prev = last
    return out


def arm_months() -> dict[str, dict[str, float]]:
    table: dict[str, dict[str, float]] = {}
    for arm, by_year in ARMS.items():
        merged: dict[str, float] = {}
        for dirname in by_year.values():
            path = os.path.join(RESULTS, dirname)
            if os.path.isdir(path):
                merged.update(monthly(path))
        table[arm] = merged
    return table


def pct(v: float | None, width: int = 9) -> str:
    return f'{v:+{width - 1}.2f}%' if v is not None else f'{"-":>{width}s}'


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--switch', default=os.path.join(RESULTS, '20250101_20260930_ts_switch_momentum'))
    ap.add_argument('--audit', nargs='+', default=[
        '/tmp/momentum_decisions_flatten_20261031_174846.csv',
        '/tmp/momentum_decisions_resume_20261031_225613.csv',
    ], help='one or more ts_switch_momentum decision csvs (a resumed run writes a second one)')
    args = ap.parse_args(argv)

    if not os.path.isdir(args.switch):
        print(f'no switch run at {args.switch}')
        return 1
    sw = monthly(args.switch)
    arms = arm_months()

    chosen: dict[str, str] = {}
    for path in args.audit:
        if not os.path.exists(path):
            continue
        with open(path, newline='') as f:
            for row in csv.DictReader(f):
                chosen.setdefault(row['month'], row['delegate'])

    def short(name: str) -> str:
        return 'turn' if 'turning' in name else ('flow' if 'flow' in name else name[:4])

    print(f'{"month":7s} {"arm":5s} {"switch":>9s} {"flow":>9s} {"turn":>9s} '
          f'{"better":>7s} {"regret":>8s}')
    regret = 0.0
    for month in sorted(sw):
        arm = short(chosen[month]) if month in chosen else '?'
        f_ret = arms['flow'].get(month)
        t_ret = arms['turn'].get(month)
        s_ret = sw[month]
        cands = [('flow', f_ret), ('turn', t_ret)]
        cands = [(n, v) for n, v in cands if v is not None]
        if cands:
            best_name, best = max(cands, key=lambda kv: kv[1])
            gap = best - s_ret
            regret += gap
            tail = f'{best_name:>7s} {gap:+7.2f}'
        else:
            tail = f'{"-":>7s} {"-":>8s}'
        print(f'{month:7s} {arm:5s} {pct(s_ret)} {pct(f_ret)} {pct(t_ret)} {tail}')
    print(f'\nsum of monthly regret vs per-month best arm: {regret:+.1f}pp')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
