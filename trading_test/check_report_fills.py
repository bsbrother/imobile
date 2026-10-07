"""Audit the fills a period report booked against the day's real bars.

The question this answers: *could that price have been the one the exchange matched?* A period report
records every transaction with its reason, so each SELL can be tested against the day's OHLC:

  stop_loss     the stop is a DOWNWARD trigger. If the day OPENED below the trigger, the order fires at
                the open — booking the trigger price is impossible, because the stock was never at the
                trigger after the session began. Overstatement = (booked - open) * qty.
  take_profit   an UPWARD trigger. If the day's HIGH never reached the booked price, the fill is
                impossible. (The mirror image — the market gapping ABOVE the take-profit, so the booked
                price is too LOW — is conservative and reported separately.)
  _expired      a scheduled exit sells at the open; any other price is not the open.

Usage:
  .venv/bin/python trading_test/check_report_fills.py backtest/results_backups/*/report_period_*.md
  .venv/bin/python trading_test/check_report_fills.py <report.md> ... --samples 5

Exit code is 0 always: this is a measurement, not a gate.
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from typing import Any

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import bars as B  # noqa: E402

ROW = re.compile(
    r'^\|\s*(?P<day>\d{4}-\d{2}-\d{2})[\d: ]*\|\s*(?P<side>🟢 BUY|🔴 SELL)\s*\|'
    r'\s*(?P<code>[\d.A-Z]+)\s*\|\s*(?P<name>[^|]+?)\s*\|\s*(?P<qty>\d+)\s*\|'
    r'\s*¥(?P<price>[\d.]+)\s*\|\s*¥(?P<amount>[\d,.]+)\s*\|\s*(?P<reason>[^|]*)\|'
)
RETURN = re.compile(r'\*\*Total Return\*\* \| (?P<ret>-?[\d.]+)%')

_bars_cache: dict[tuple[str, str], list | None] = {}


def day_bars(code: str, day: str):
    key = (code, day)
    if key not in _bars_cache:
        try:
            _bars_cache[key] = B.load_intraday(code, day)[0] or None
        except Exception:
            _bars_cache[key] = None
    return _bars_cache[key]


def audit(path: str, samples: int = 3) -> dict[str, Any]:
    text = open(path, encoding='utf-8').read().split('\n')
    ret = next((float(m.group('ret')) for line in text if (m := RETURN.search(line))), None)

    res: dict[str, Any] = dict(path=path, ret=ret, sells=0, stops=0, tps=0, expired=0, no_bars=0,
                               stop_impossible=0, stop_over=0.0, tp_impossible=0, tp_under=0.0,
                               expired_not_open=0, samples=[])
    for line in text:
        m = ROW.match(line)
        if not m or 'SELL' not in m.group('side'):
            continue
        g = m.groupdict()
        res['sells'] += 1
        reason = g['reason']
        price, qty = float(g['price']), int(g['qty'])
        day = g['day'].replace('-', '')
        bl = day_bars(g['code'], day)
        if not bl:
            res['no_bars'] += 1
            continue
        op = bl[0]['open']
        hi = max(b['high'] for b in bl)
        lo = min(b['low'] for b in bl)

        if 'stop_loss' in reason:
            res['stops'] += 1
            if op < price - 1e-6:                      # the stop was already breached at the open
                res['stop_impossible'] += 1
                res['stop_over'] += (price - op) * qty
                if len(res['samples']) < samples:
                    res['samples'].append((day, g['code'], g['name'].strip()[:6], op, price,
                                           price - op, lo, hi))
        elif 'take_profit' in reason:
            res['tps'] += 1
            if hi < price - 1e-6:                      # the day never traded at the booked price
                res['tp_impossible'] += 1
            elif op > price + 1e-6:                    # gapped above the take-profit: under-booked
                res['tp_under'] += (op - price) * qty
        elif '_expired' in reason or 'expired' in reason:
            res['expired'] += 1
            if abs(price - op) > 1e-6:
                res['expired_not_open'] += 1
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('reports', nargs='+', help='period report markdown file(s); globs are expanded')
    ap.add_argument('--samples', type=int, default=3, help='impossible fills to show per report')
    args = ap.parse_args(argv)

    paths: list[str] = []
    for pat in args.reports:
        paths.extend(sorted(glob.glob(pat)) or [pat])

    rows = [audit(p, args.samples) for p in paths]
    rows.sort(key=lambda r: (r['ret'] is None, r['ret']))

    print(f"{'return':>8s} {'sells':>6s} {'stops':>6s} {'impossible':>11s} {'¥ overstated':>14s} "
          f"{'tp bad':>7s} {'¥ under':>10s} {'no bars':>8s}  run")
    for r in rows:
        name = os.path.basename(os.path.dirname(r['path']))[:52]
        ret = f"{r['ret']:.2f}" if r['ret'] is not None else '?'
        print(f"{ret:>8s} {r['sells']:6d} {r['stops']:6d} {r['stop_impossible']:11d} "
              f"{r['stop_over']:14,.0f} {r['tp_impossible']:7d} {r['tp_under']:10,.0f} "
              f"{r['no_bars']:8d}  {name}")

    for r in rows:
        if r['samples']:
            print(f"\n[{r['ret']:.2f}%] {os.path.basename(os.path.dirname(r['path']))}")
            print(f"    {'day':9s} {'code':10s} {'name':7s} {'open':>8s} {'booked':>8s} {'+/share':>8s} "
                  f"{'low':>8s} {'high':>8s}")
            for day, code, nm, op, p, d, lo, hi in r['samples']:
                print(f"    {day:9s} {code:10s} {nm:7s} {op:8.2f} {p:8.2f} {d:+8.2f} {lo:8.2f} {hi:8.2f}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
