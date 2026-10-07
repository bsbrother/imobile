#!/usr/bin/env python3
"""Audit every SELL the backtest actually booked: could that fill have happened?

The engine writes executed trades to `transactions`. This takes each sell's booked price and checks
it against the day's limit-down (from the previous close and the board band): at or below the
limit-down there are no buyers, so the exit is impossible — the position was trapped and the sell was
invented. That is the failure the fill-level rule in `execute_sell_order` now refuses, and this
counts how often it was happening before.

Also reports the mirror case for buys, and both sides of the "trigger inside the untradeable zone".

Run:  .venv/bin/python trading_test/check_executed_sells.py [--range 20260101 20260930] [--out PATH]
"""
import argparse
import os
import sqlite3
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for p in (ROOT, os.path.join(ROOT, 'trading_test')):
    if p not in sys.path:
        sys.path.insert(0, p)

from bars import BarStore                                              # noqa: E402
from backtest.utils.limit_board import fill_block_reason, limit_prices  # noqa: E402
from backtest.utils.trading_calendar import get_trading_days_before     # noqa: E402

DB_PATH = os.path.join(ROOT, 'shared', 'db', 'test_imobile.db')


def load_trades_typed(date_from, date_to):
    con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    try:
        rows = con.execute("""
            SELECT transaction_date, code, name, transaction_type, price, quantity,
                   COALESCE(notes, '')
            FROM transactions ORDER BY transaction_date
        """).fetchall()
    except sqlite3.Error as e:
        print(f"Cannot read {DB_PATH}: {e}")
        return []
    finally:
        con.close()
    out = []
    for d, code, name, ttype, price, qty, notes in rows:
        day = str(d).replace('-', '').replace(' ', '')[:8]
        if not day.isdigit():
            continue
        if (date_from and day < date_from) or (date_to and day > date_to):
            continue
        out.append({'day': day, 'code': code, 'name': name, 'side': str(ttype).lower(),
                    'price': float(price or 0), 'qty': qty, 'notes': notes})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--range', nargs=2, metavar=('FROM', 'TO'), default=['20260101', '20261231'])
    ap.add_argument('--out', default=os.path.join(ROOT, 'trading_test', 'results', 'executed_fill_audit.md'))
    args = ap.parse_args()

    trades = load_trades_typed(args.range[0], args.range[1])
    if not trades:
        print(f"No trades in {DB_PATH} for {args.range[0]}..{args.range[1]}")
        return 1

    store = BarStore()
    tally = Counter()
    impossible = []
    no_prev = 0
    for t in trades:
        side = 'sell' if t['side'].startswith('sell') else 'buy'
        tally[side] += 1
        prev_day = get_trading_days_before(t['day'], 1)
        prev_close = store.prev_close(t['code'], prev_day)
        if not prev_close:
            no_prev += 1
            continue
        why = fill_block_reason(side, t['price'], prev_close, t['code'])
        if why:
            ld, lu = limit_prices(prev_close, t['code'])
            tally[f'{side}_impossible'] += 1
            impossible.append({**t, 'side': side, 'prev_close': prev_close,
                               'limit_down': ld, 'limit_up': lu, 'why': why})

    n_sell = tally.get('sell', 0)
    n_buy = tally.get('buy', 0)
    lines = [f"# Executed-fill audit — {args.range[0]}..{args.range[1]}", '',
             f"{len(trades)} executed trades from `transactions` ({n_buy} buys, {n_sell} sells); "
             f"{no_prev} had no previous close available and were skipped.", '',
             '| check | count |', '|---|---|',
             f'| SELLs booked at/below the limit-down (no buyers — impossible) | {tally.get("sell_impossible", 0)} |',
             f'| BUYs booked at/above the limit-up (no sellers — impossible) | {tally.get("buy_impossible", 0)} |', '']
    if impossible:
        lines += ['| date | code | name | side | booked price | prev close | limit | why impossible |',
                  '|---|---|---|---|---|---|---|---|']
        for t in sorted(impossible, key=lambda x: (x['day'], x['code']))[:60]:
            lim = t['limit_down'] if t['side'] == 'sell' else t['limit_up']
            lines.append(f"| {t['day']} | {t['code']} | {t['name']} | {t['side']} | {t['price']:.2f} | "
                         f"{t['prev_close']:.2f} | {lim:.2f} | {t['why']} |")
        if len(impossible) > 60:
            lines.append(f"| ... | | | | | | | {len(impossible) - 60} more |")
        lines.append('')
    else:
        lines += ['Every executed fill sat inside its day\'s band — no impossible exit in this sample.',
                  '']
    lines += ['_Caveat: `transactions` may span more than one backtest run if the DB was not wiped, so',
              'treat these as historical bookings, not as one run\'s ledger._', '']

    report = '\n'.join(lines)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, 'w', encoding='utf-8') as f:
        f.write(report)
    print(report)
    print(f"written: {args.out}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
