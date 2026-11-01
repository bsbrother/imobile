#!/usr/bin/env python3
"""Monthly market-state features, so "which strategy for this tape" can be tested
against something measurable instead of against the calendar.

Reads the same OHLCV panel the turning-point picker uses (read-only; safe while a
backtest runs) and writes one row per month with only information available AT THAT
MONTH'S CLOSE:

    eq_ret       equal-weight market return, %            (tape direction)
    breadth20    % of stocks above their 20d mean         (participation)
    cs_vol       cross-sectional std of daily returns, %  (dispersion)
    dd_from_peak equal-weight drawdown from running peak  (how far into a hole)
    trend_up     eq_ret_3m > 0 and price > 60d mean       (persistent tape)
    n_stocks     universe size, sanity only

Output: backtest/results/market_state_features.csv
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.strategies import ts_turning_point as tp  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   'backtest', 'results', 'market_state_features.csv')


def main(end_date: str = '20260930') -> None:
    panel = tp.load_panel(end_date)
    df = panel['df'] if isinstance(panel, dict) else panel
    df = df[['trade_date', 'symbol', 'close']].copy()
    df['trade_date'] = df['trade_date'].astype(str)
    df = df.sort_values(['symbol', 'trade_date'])

    g = df.groupby('symbol')['close']
    df['ma20'] = g.transform(lambda s: s.rolling(20, min_periods=15).mean())
    df['ma60'] = g.transform(lambda s: s.rolling(60, min_periods=40).mean())
    df['ret'] = g.transform(lambda s: s.pct_change())

    df['above20'] = (df['close'] > df['ma20']).astype(float)
    daily = df.groupby('trade_date').agg(
        eq_ret=('ret', 'mean'),
        cs_vol=('ret', 'std'),
        breadth20=('above20', 'mean'),
        n_stocks=('symbol', 'nunique'),
    ).reset_index()

    # equal-weight index, dividends/costs ignored — it is a state descriptor, not a P&L
    daily['eq'] = (1 + daily['eq_ret'].fillna(0)).cumprod() * 600000
    daily['peak'] = daily['eq'].cummax()
    daily['dd_from_peak'] = (daily['eq'] / daily['peak'] - 1) * 100
    daily['m3'] = daily['eq'] / daily['eq'].shift(63) - 1

    daily['month'] = daily['trade_date'].str[:6]
    rows = []
    prev_eq = 600000.0
    for month, part in daily.groupby('month'):
        last = part.iloc[-1]
        rows.append({
            'month': month,
            'eq_ret': round((last['eq'] / prev_eq - 1) * 100, 3),
            'breadth20': round(last['breadth20'] * 100, 1),
            'cs_vol': round(float(part['cs_vol'].mean()) * 100, 3),
            'dd_from_peak': round(last['dd_from_peak'], 2),
            'trend_up_3m': bool(last['m3'] > 0),
            'n_stocks': int(last['n_stocks']),
        })
        prev_eq = last['eq']

    out = pd.DataFrame(rows)
    out.to_csv(OUT, index=False)
    print(out.to_string(index=False))
    print(f'\nwrote {OUT}')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else '20260930')
