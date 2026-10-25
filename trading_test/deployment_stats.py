"""Capital deployment / turnover for a finished backtest, from its ledger + report.

Answers the question "why did strategy A make 68% and B make 1.8% on the same
window": split the return into (a) how much capital was at work, (b) how often it
cycled, (c) the edge per round trip.

    deployment = mean(open position cost basis) / mean(equity)
    turnover   = total buy notional / mean(equity)
    edge/trip  = FIFO mean round-trip return

Usage:
    python trading_test/deployment_stats.py DIR [DIR ...]
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3
import sys

NUM = re.compile(r"-?\d[\d,]*\.?\d*")


def _num(s: str) -> float | None:
    m = NUM.search(s.replace(",", ""))
    return float(m.group()) if m else None


def equity_by_day(report: str) -> dict[str, float]:
    out: dict[str, float] = {}
    with open(report, encoding="utf-8") as f:
        for line in f:
            if not re.match(r"^\| \d{8} \|", line):
                continue
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) < 7:
                continue
            v = _num(c[6])
            if v is not None:
                out[c[0]] = v
    return out


def txns_from_report(report: str) -> list[tuple[str, str, str, float, float]]:
    """(type, date, code, price, qty, amount) fallback when the run kept no DB."""
    out = []
    with open(report, encoding="utf-8") as f:
        for line in f:
            if "🟢 BUY" not in line and "🔴 SELL" not in line:
                continue
            c = [x.strip() for x in line.strip().strip("|").split("|")]
            if len(c) < 7:
                continue
            out.append((c[1], c[0], c[2], _num(c[5]) or 0.0, _num(c[4]) or 0.0, _num(c[6]) or 0.0))
    return out


def analyse(d: str) -> dict:
    report = sorted(glob.glob(os.path.join(d, "report_period_*.md")))[0]
    eq = equity_by_day(report)
    db = os.path.join(d, "ledger.db")
    rows: list[tuple] = []
    rows2: list[tuple] = []
    if os.path.exists(db):
        con = sqlite3.connect(db)
        for t, td, code, px, qty, amt in con.execute(
            "select transaction_type, transaction_date, code, price, quantity, amount "
            "from transactions order by transaction_date, id"
        ):
            rows.append((t or "", (td or "")[:10], float(px or 0.0), float(qty or 0.0), float(amt or 0.0)))
            rows2.append((t or "", code or "", float(px or 0.0), float(qty or 0.0)))
        con.close()
    else:
        # no DB: use the report's transaction history (dates are ISO already)
        for t, dt, code, px, qty, amt in txns_from_report(report):
            rows.append((t, dt, px, qty, amt))
            rows2.append((t, code, px, qty))

    open_cost = 0.0
    buy_notional = 0.0
    trips: list[float] = []
    lots: dict[str, list[list[float]]] = {}
    daily_cost: dict[str, float] = {}
    for ttype, tdate, price, qty, amount in rows:
        day = (tdate or "")[:10].replace("-", "")[:8]
        amount = amount or (price or 0) * (qty or 0)
        if "BUY" in (ttype or "").upper():
            open_cost += amount
            buy_notional += amount
        else:
            open_cost -= amount
            open_cost = max(open_cost, 0.0)
        daily_cost[day] = open_cost

    # FIFO round trips for the per-trip edge
    for ttype, code, price, qty in rows2:
        if not price or not qty:
            continue
        if "BUY" in (ttype or "").upper():
            lots.setdefault(code, []).append([qty, price])
        else:
            left = qty
            while left > 1e-9 and lots.get(code):
                lot = lots[code][0]
                take = min(left, lot[0])
                trips.append((price / lot[1] - 1.0) * 100.0)
                lot[0] -= take
                left -= take
                if lot[0] <= 1e-9:
                    lots[code].pop(0)

    avg_eq = sum(eq.values()) / len(eq) if eq else 0.0
    deployed = [v for k, v in daily_cost.items() if k in eq]
    avg_cost = sum(deployed) / len(deployed) if deployed else 0.0
    edge = sum(trips) / len(trips) if trips else 0.0
    return {
        "name": os.path.basename(d.rstrip("/")),
        "avg_equity": avg_eq,
        "avg_deployed": avg_cost,
        "deployment_pct": 100.0 * avg_cost / avg_eq if avg_eq else 0.0,
        "turnover_x": buy_notional / avg_eq if avg_eq else 0.0,
        "trips": len(trips),
        "edge_pct": edge,
        "per_trip_pct": 100.0 * (buy_notional / len(trips)) / avg_eq if trips and avg_eq else 0.0,
        # return ~= trips x edge x (per-trip notional / equity)
        "implied_return_pct": (len(trips) * (edge / 100.0) * (buy_notional / len(trips)) / avg_eq * 100.0)
        if trips and avg_eq else 0.0,
    }


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(__doc__)
    hdr = (f"{'run':<48}{'avgEq':>10}{'deployed':>10}{'dep%':>6}{'turn':>6}"
           f"{'trips':>7}{'edge%':>8}{'trip/eq%':>9}{'implied%':>9}")
    print(hdr)
    print("-" * len(hdr))
    for d in argv:
        s = analyse(d)
        print(f"{s['name'][:47]:<48}{s['avg_equity']:>10,.0f}{s['avg_deployed']:>10,.0f}"
              f"{s['deployment_pct']:>6.1f}{s['turnover_x']:>6.1f}{s['trips']:>7}"
              f"{s['edge_pct']:>8.3f}{s['per_trip_pct']:>9.2f}{s['implied_return_pct']:>9.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
