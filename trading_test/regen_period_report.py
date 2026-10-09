"""Regenerate a backtest period report from the database, without re-running.

`OrderAnalyzer.generate_period_report` reads ONLY the database (plus OHLCV for the
held names and index data for the benchmark), so a period report can be rebuilt for
a run whose process died at the report step -- as long as its DB is intact. The
2025 no-stop arm (tag `2025clean`) died exactly there: every daily report was
written, then the process vanished inside `generate_period_report` with no traceback
and left no `report_period_*.md` and no backup.

`engine.py` runs its backtest only under `if __name__ == '__main__'`, so importing it
is safe -- it gives `OrderAnalyzer` and the module-level `DB` (which is DBTEST, the
same `shared/db/test_imobile.db` the engine traded into).

usage:
  .venv/bin/python trading_test/regen_period_report.py <start YYYYMMDD> <end YYYYMMDD> <out.md> \
      [--user-id 1] [--smart-orders path/smart_orders_YYYYMMDD.json]
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _default_smart_orders(start: str, end: str) -> str | None:
    pat = os.path.join(ROOT, 'backtest', 'results', f'{start}_{end}_*', 'smart_orders_*.json')
    cands = sorted(glob.glob(pat))
    return cands[-1] if cands else None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    ap.add_argument('start', help='YYYYMMDD')
    ap.add_argument('end', help='YYYYMMDD')
    ap.add_argument('out', help='path to write report_period_*.md')
    ap.add_argument('--user-id', type=int, default=1)
    ap.add_argument('--smart-orders', default=None,
                    help='any smart_orders_*.json of the run; OrderAnalyzer reads its metadata')
    args = ap.parse_args(argv)

    so = args.smart_orders or _default_smart_orders(args.start, args.end)
    if not so:
        print('no smart_orders_*.json found for that range; pass --smart-orders', file=sys.stderr)
        return 2

    from backtest.engine import OrderAnalyzer

    print(f'smart_orders: {os.path.relpath(so, ROOT)}')
    print(f'rebuilding period report {args.start}..{args.end} -> {args.out}')
    analyzer = OrderAnalyzer(smart_orders_file=so, user_id=args.user_id)
    analyzer.generate_period_report(args.start, args.end, args.out)
    print(f'wrote {args.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
