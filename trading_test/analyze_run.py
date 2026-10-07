"""Summarise a backtest period report: headline, monthly, sell reasons, fill honesty.

Usage:
  .venv/bin/python trading_test/analyze_run.py <report_period_*.md> [--baseline <other.md>]

Reads only the report, so it works on backtest/results/ or results_backups/ alike.
"""
from __future__ import annotations

import argparse
import collections
import os
import re
import sys

ROW = re.compile(
    r'^\|\s*(?P<day>\d{8})\s*\|\s*(?P<txns>\d+)\s*\|\s*(?P<sells>\d+)\s*\|'
    r'\s*¥\s*(?P<real>[-\d,.]+)\s*\|\s*¥\s*(?P<unreal>[-\d,.]+)\s*\|\s*¥\s*(?P<tot>[-\d,.]+)\s*\|'
    r'\s*¥\s*(?P<pv>[\d,]+\.\d\d)\s*\(\s*(?P<pct>[-\d.]+)%\)\s*\|\s*(?P<pos>\d+)\s*\|'
)
SELL = re.compile(
    r'^\|\s*\d{4}-\d{2}-\d{2}[^|]*\|\s*🔴 SELL\s*\|\s*[\d.A-Z]+\s*\|\s*[^|]+?\s*\|'
    r'\s*\d+\s*\|\s*¥[\d.]+\s*\|\s*¥[\d,.]+\s*\|\s*(?P<reason>.*?)\s*\|'
)
KEYS = ('Total Return', 'Realized P&L', 'Max Drawdown', 'Final Portfolio Value',
        'Total Transactions', 'Sell Transactions', 'Initial Capital')


def head(text: str) -> dict:
    out = {}
    for k in KEYS:
        m = re.search(r'\|\s*\*\*' + re.escape(k) + r'\*\*\s*\|\s*([^|]+)\|', text)
        if m:
            out[k] = m.group(1).strip()
    return out


def monthly(text: str):
    mon = collections.OrderedDict()
    for line in text.split('\n'):
        m = ROW.match(line)
        if m:
            mon.setdefault(m.group('day')[:6], []).append(float(m.group('pv').replace(',', '')))
    out, prev = {}, float((head(text).get('Initial Capital') or '¥600,000').replace('¥', '').replace(',', ''))
    for k, vals in mon.items():
        out[k] = (vals[-1] / prev - 1) * 100
        prev = vals[-1]
    return out, prev


def reasons(text: str) -> collections.Counter:
    c = collections.Counter()
    for line in text.split('\n'):
        m = SELL.match(line)
        if not m:
            continue
        r = m.group('reason')
        key = next((k for k in ('stop_loss', 'gap_exit', 'er_trend', 'strict_ma',
                                'take_profit', 'order_expired') if k in r), 'other/expired')
        c[key] += 1
    return c


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('report')
    ap.add_argument('--baseline', help='optional second report to diff against')
    args = ap.parse_args(argv)

    def show(path, tag):
        text = open(path, encoding='utf-8').read()
        h = head(text)
        mon, final = monthly(text)
        print(f"=== {tag}: {os.path.basename(os.path.dirname(path))} ===")
        for k in ('Initial Capital', 'Final Portfolio Value', 'Total Return',
                  'Realized P&L', 'Max Drawdown', 'Total Transactions', 'Sell Transactions'):
            if k in h:
                print(f"  {k:24s} {h[k]}")
        print("  monthly: " + "  ".join(f"{k[4:]}:{v:+.2f}%" for k, v in mon.items()))
        neg = [k[4:] for k, v in mon.items() if v < 0]
        print(f"  negative months: {neg or 'none'}")
        c = reasons(text)
        print("  sell reasons: " + ", ".join(f"{k}={v}" for k, v in c.most_common()))
        return mon

    m1 = show(args.report, "RUN")
    if args.baseline:
        m2 = show(args.baseline, "BASELINE")
        print("\n  delta (RUN - BASELINE):")
        for k in m1:
            if k in m2:
                print(f"    {k}: {m1[k]-m2[k]:+.2f}pp")
        print(f"    TOTAL: {sum(m1.values())-sum(m2.values()):+.2f}pp (approx, monthly sum)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
