"""Comparable stats from a saved backtest period report (no engine re-run needed).

The engine's period report has portfolio totals but no max drawdown and no
per-trade stats, which makes cross-strategy comparison awkward. This parses the
same markdown instead:

  * total return, final value, transaction counts   (from the summary table)
  * max drawdown                                   (from the daily value series)
  * round-trip win rate, mean/median trade %, P&L   (FIFO matching of the
                                                     transaction history)

Usage:
    python trading_test/report_stats.py DIR [DIR ...]
"""
from __future__ import annotations

import glob
import os
import re
import sys
from dataclasses import dataclass, field

NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def _num(s: str) -> float | None:
    m = NUM.search(s.replace(",", ""))
    return float(m.group()) if m else None


@dataclass
class Stats:
    name: str
    total_return: float | None = None
    final_value: float | None = None
    txns: int | None = None
    max_dd: float | None = None
    round_trips: int = 0
    wins: int = 0
    mean_pct: float = 0.0
    median_pct: float = 0.0
    gross_pnl: float = 0.0
    daily: list[float] = field(default_factory=list)


def _report_path(d: str) -> str:
    hits = sorted(glob.glob(os.path.join(d, "report_period_*.md")))
    if not hits:
        raise SystemExit(f"no report_period_*.md in {d}")
    return hits[0]


def parse(d: str) -> Stats:
    path = _report_path(d)
    st = Stats(name=os.path.basename(d.rstrip("/")))
    rows: list[list[str]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if "| **Total Return** |" in line:
                st.total_return = _num(line.split("|")[2])
            elif "| **Final Portfolio Value** |" in line:
                st.final_value = _num(line.split("|")[2])
            elif "| **Total Transactions** |" in line:
                v = _num(line.split("|")[2])
                st.txns = int(v) if v is not None else None
            elif line.startswith("| 20"):
                # Transaction rows start with an ISO timestamp ("| 2025-01-02 00:00:00"),
                # daily rows with a bare YYYYMMDD. Only the latter is the value series.
                if re.match(r"^\| \d{8} \|", line):
                    rows.append([c.strip() for c in line.strip("|").split("|")])

    # daily rows: Date | Txns | Sells | Realized | Unrealized | Total | Value (x%) | Pos
    for r in rows:
        if len(r) < 7:
            continue
        v = _num(r[6])
        if v is not None:
            st.daily.append(v)
    peak, dd = float("-inf"), 0.0
    for v in st.daily:
        peak = max(peak, v)
        if peak > 0:
            dd = min(dd, v / peak - 1.0)
    st.max_dd = dd * 100 if st.daily else None

    # transaction history: Date | Type | Symbol | Name | Qty | Price | Amount | Notes
    lots: dict[str, list[list[float]]] = {}
    pcts: list[float] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if "🟢 BUY" not in line and "🔴 SELL" not in line:
                continue
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) < 7:
                continue
            sym, qty, px = c[2], _num(c[4]), _num(c[5])
            if not sym or not qty or not px:
                continue
            if "BUY" in line:
                lots.setdefault(sym, []).append([qty, px])
            else:
                left = qty
                while left > 1e-9 and lots.get(sym):
                    lot = lots[sym][0]
                    take = min(left, lot[0])
                    pcts.append((px / lot[1] - 1.0) * 100.0)
                    st.gross_pnl += (px - lot[1]) * take
                    lot[0] -= take
                    left -= take
                    if lot[0] <= 1e-9:
                        lots[sym].pop(0)
    st.round_trips = len(pcts)
    st.wins = sum(1 for p in pcts if p > 0)
    if pcts:
        pcts.sort()
        st.mean_pct = sum(pcts) / len(pcts)
        mid = len(pcts) // 2
        st.median_pct = pcts[mid] if len(pcts) % 2 else (pcts[mid - 1] + pcts[mid]) / 2
    return st


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(__doc__)
    allst = [parse(d) for d in argv]
    hdr = f"{'run':<52}{'ret%':>8}{'maxDD%':>8}{'txns':>7}{'trips':>7}{'win%':>7}{'mean%':>8}{'med%':>8}{'PnL':>12}"
    print(hdr)
    print("-" * len(hdr))
    for s in allst:
        win = 100.0 * s.wins / s.round_trips if s.round_trips else 0.0
        print(f"{s.name[:51]:<52}"
              f"{(s.total_return or 0):>8.2f}{(s.max_dd or 0):>8.2f}{(s.txns or 0):>7}"
              f"{s.round_trips:>7}{win:>7.1f}{s.mean_pct:>8.2f}{s.median_pct:>8.2f}{s.gross_pnl:>12,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
