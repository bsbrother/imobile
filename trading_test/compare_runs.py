#!/usr/bin/env python3
"""Compare backtest runs on the same footing: total return, max drawdown, monthly returns.

Reads `report_orders_*.md` (one per session, written by the engine) instead of the summary
file, so it works on a run that was interrupted as well as on a finished one, and so two
runs over different windows can be lined up month by month.

Usage:
    python trading_test/compare_runs.py DIR [DIR ...] [--audit CSV] [--init 600000]

DIR may be a result directory name (resolved under backtest/results) or a full path.
`--audit` prints the delegated arm per month from a ts_switch_momentum decisions csv.
"""
from __future__ import annotations

import argparse
import os
import re
import sys

RESULTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'backtest', 'results')

NAV_RE = re.compile(r'\*\*True Total Portfolio Value:\*\*\s*¥([\d,]+\.?\d*)')
MONTH_RE = re.compile(r'report_orders_(\d{8})\.md$')


def load_equity(path: str) -> list[tuple[str, float]]:
    """[(yyyymmdd, NAV)] in session order; missing/unparsable days are skipped."""
    rows: list[tuple[str, float]] = []
    for name in sorted(os.listdir(path)):
        m = MONTH_RE.search(name)
        if not m:
            continue
        try:
            with open(os.path.join(path, name)) as f:
                hit = NAV_RE.search(f.read())
        except OSError:
            continue
        if hit:
            rows.append((m.group(1), float(hit.group(1).replace(',', ''))))
    return rows


def stats(rows: list[tuple[str, float]], init: float) -> dict:
    if not rows:
        return {}
    navs = [v for _, v in rows]
    total = (navs[-1] / init - 1) * 100
    peak, mdd = navs[0], 0.0
    for v in navs:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1)
    monthly: dict[str, float] = {}
    prev = init
    for k in sorted({d[:6] for d, _ in rows}):
        last = [v for d, v in rows if d[:6] == k][-1]
        monthly[k] = (last / prev - 1) * 100
        prev = last
    return {'first': rows[0][0], 'last': rows[-1][0], 'n': len(rows),
            'nav': navs[-1], 'total': total, 'mdd': mdd * 100, 'monthly': monthly}


def resolve(name: str) -> str:
    return name if os.path.isdir(name) else os.path.join(RESULTS, name)


def label(path: str) -> str:
    """Short name for the table: drop the window prefix and the ts_/conc2 decorations."""
    name = os.path.basename(os.path.normpath(path))
    name = re.sub(r'^\d{8}_\d{8}_', '', name)
    return name.replace('ts_7AZ_96MA_flow_review', 'flow').replace('ts_', '')


def full_label(path: str) -> str:
    return os.path.basename(os.path.normpath(path))


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('dirs', nargs='+')
    ap.add_argument('--init', type=float, default=600000.0)
    ap.add_argument('--audit', default='')
    args = ap.parse_args(argv)

    runs = []
    for d in args.dirs:
        p = resolve(d)
        rows = load_equity(p)
        if not rows:
            print(f'! {full_label(p)}: no report_orders_*.md found — skipped')
            continue
        runs.append((label(p), stats(rows, args.init)))
    if not runs:
        return 1

    print(f'{"run":24s} {"window":21s} {"n":>4s} {"total":>9s} {"maxDD":>8s}')
    for name, s in runs:
        print(f'{name:24.24s} {s["first"]}→{s["last"]} {s["n"]:4d} '
              f'{s["total"]:+8.2f}% {s["mdd"]:+7.2f}%')

    width = max(10, max(len(n) for n, _ in runs) + 1)
    months = sorted({m for _, s in runs for m in s['monthly']})
    print(f'\n{"month":7s} ' + ' '.join(f'{n:>{width}s}' for n, _ in runs))
    for m in months:
        cells = []
        for _, s in runs:
            v = s['monthly'].get(m)
            cells.append(f'{v:+{width - 1}.2f}%' if v is not None else f'{"-":>{width}s}')
        print(f'{m:7s} ' + ' '.join(cells))

    if args.audit and os.path.exists(args.audit):
        import csv as _csv
        print(f'\narm per month ({os.path.basename(args.audit)}):')
        seen: dict[str, str] = {}
        with open(args.audit, newline='') as f:
            for row in _csv.DictReader(f):
                seen.setdefault(row['month'], row['delegate'])
        for m in sorted(seen):
            print(f'  {m}  {seen[m]}')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
