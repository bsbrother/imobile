"""Rebuild the LHB institutional-flow cache used by the *_flow strategies.

`backtest/strategies/ts_7AZ_96MA_flow_v2.py` reads a single CSV at
shared/data/lhb/lhb_institutional_2026.csv and passes every candidate through
`_apply_flow_filter_v2`. When a candidate has no LHB record in the lookback
window that filter is a PASSTHROUGH, so the file must cover the whole backtest
window: a cache that stops mid-window silently removes the strategy's flow
signal for the uncovered dates.

Source: akshare `stock_lhb_jgmmtj_em` (机构买卖每日统计), same schema as the
existing cache (序号,代码,名称,...,上榜日期).

Usage:
  .venv/bin/python trading_test/fetch_lhb_institutional.py --start 20250101 --end 20260930 \
      --out shared/data/lhb/lhb_institutional_2026.csv --replace
  # add to the existing file instead of replacing it:
  .venv/bin/python trading_test/fetch_lhb_institutional.py --start 20260801 --end 20260930 --append
"""
from __future__ import annotations

import argparse
import os
import sys
import time

import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_OUT = os.path.join(ROOT, 'shared', 'data', 'lhb', 'lhb_institutional_2026.csv')

# akshare output column order (kept verbatim so the cache matches what the
# strategy expects — it looks up 代码 and 上榜日期 by name).
COLS = ['序号', '代码', '名称', '收盘价', '涨跌幅', '买方机构数', '卖方机构数',
        '机构买入总额', '机构卖出总额', '机构买入净额', '市场总成交额',
        '机构净买额占总成交额比', '换手率', '流通市值', '上榜原因', '上榜日期']


def fetch_month(ak, ym: str) -> pd.DataFrame:
    """One calendar month. akshare iterates internally; a month keeps each call short."""
    start = f"{ym}01"
    end = (pd.Timestamp(ym + "01") + pd.offsets.MonthEnd(0)).strftime("%Y%m%d")
    end = min(end, pd.Timestamp.today().strftime("%Y%m%d"))
    df = ak.stock_lhb_jgmmtj_em(start_date=start, end_date=end)
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS)
    return df


def months_between(start: str, end: str):
    cur = pd.Timestamp(start).to_period('M')
    last = pd.Timestamp(end).to_period('M')
    while cur <= last:
        yield cur.strftime('%Y%m')
        cur += 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--start', required=True, help='YYYYMMDD')
    ap.add_argument('--end', required=True, help='YYYYMMDD')
    ap.add_argument('--out', default=DEFAULT_OUT)
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--replace', action='store_true', help='write ONLY the fetched range')
    g.add_argument('--append', action='store_true', help='merge the fetched range into --out')
    ap.add_argument('--dry-run', action='store_true')
    args = ap.parse_args(argv)

    import akshare as ak

    frames = []
    for ym in months_between(args.start, args.end):
        t0 = time.time()
        try:
            df = fetch_month(ak, ym)
        except Exception as exc:  # noqa: BLE001
            print(f"  {ym}: FAILED {type(exc).__name__}: {str(exc)[:120]}", flush=True)
            continue
        print(f"  {ym}: {len(df):4d} rows  ({time.time()-t0:.1f}s)", flush=True)
        if len(df):
            frames.append(df)

    if not frames:
        print("nothing fetched; leaving the cache untouched", file=sys.stderr)
        return 2

    new = pd.concat(frames, ignore_index=True)
    for c in COLS:
        if c not in new.columns:
            new[c] = pd.NA
    new = new[COLS]

    old = pd.DataFrame(columns=COLS)
    if args.append and os.path.exists(args.out):
        old = pd.read_csv(args.out, dtype={'代码': str})

    merged = pd.concat([old, new], ignore_index=True)
    merged['代码'] = merged['代码'].astype(str).str.zfill(6)
    merged['上榜日期'] = merged['上榜日期'].astype(str)
    before = len(merged)
    merged = merged.drop_duplicates(subset=['代码', '上榜日期'], keep='last')
    merged = merged.sort_values(['上榜日期', '代码']).reset_index(drop=True)

    print(f"\nfetched {len(new)} rows, {before - len(merged)} duplicates dropped, "
          f"final {len(merged)} rows")
    print("coverage:", merged['上榜日期'].min(), "..", merged['上榜日期'].max())
    print(merged['上榜日期'].str[:7].value_counts().sort_index().to_string())

    if args.dry_run:
        print("\n--dry-run: not writing")
        return 0
    merged.to_csv(args.out, index=False)
    print(f"\nwrote {args.out}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
