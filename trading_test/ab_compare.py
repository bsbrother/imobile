"""Compare saved backtest arms side by side.

Reads each arm's own artifacts — `report_period_*.md` for the headline numbers and
`ledger.db` (a snapshot of `shared/db/test_imobile.db` taken right after that arm
ran) for trade-level stats and exit reasons.

Why the ledger snapshot matters: the per-sale exit reason (`executed: stop_loss`,
`stagnation_cut`, ...) exists ONLY in the run's SQLite ledger. `report_orders_*.md`
carries counts and P&L but not the reason, and the next run wipes the DB, so an arm
without a `ledger.db` can still be compared on return/drawdown but its exit mix is
gone for good.

Usage:
    python trading_test/ab_compare.py DIR [DIR ...]
    python trading_test/ab_compare.py DIR --db /path/to/other.db
"""
from __future__ import annotations

import glob
import os
import re
import sqlite3
import sys

import pandas as pd

ROW_RE = re.compile(
    r"\| (20\d{6}) \| *(\d+) \| *(\d+) \| *¥\s*([-,\d.]+) \|.+?¥\s*([-,\d.]+) "
    r"\|.+?¥\s*([-,\d.]+) \|.+?¥\s*([-,\d.]+) \(\s*([-\d.]+)%\)"
)
SELL_NOTE_RE = re.compile(r"executed: (\w+), P&L: ¥(-?[\d.]+) \((-?[\d.]+)%\)")


def load_period(arm_dir: str) -> pd.DataFrame | None:
    reports = sorted(glob.glob(os.path.join(arm_dir, "report_period_*.md")))
    if not reports:
        return None
    txt = open(reports[-1]).read()
    rows = ROW_RE.findall(txt)
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["date", "txns", "sells", "realized",
                                     "unrealized", "total", "value", "ret"])
    for c in ("realized", "unrealized", "total", "value"):
        df[c] = df[c].str.replace(",", "").astype(float)
    df["ret"] = df["ret"].astype(float)
    df["txns"] = df["txns"].astype(int)
    df["sells"] = df["sells"].astype(int)
    peak = df["value"].cummax()
    df["dd"] = (df["value"] - peak) / peak * 100.0
    return df


def fifo_trades(db_path: str) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        t = pd.read_sql(
            "select code, name, transaction_type as typ, transaction_date as d, "
            "price, quantity, net_amount, notes from transactions", con)
    finally:
        con.close()
    t["d"] = pd.to_datetime(t["d"].astype(str).str[:10])
    trips = []
    for code, g in t.groupby("code"):
        g = g.sort_values("d")
        lots: list[list[float]] = []
        entry = None
        for _, r in g.iterrows():
            if str(r.typ).upper().startswith("B"):
                lots.append([r.quantity, r.price, r.net_amount])
                entry = entry or r.d
            else:
                qty, cost = r.quantity, 0.0
                while qty > 0 and lots:
                    lot = lots[0]
                    take = min(qty, lot[0])
                    cost += take / lot[0] * lot[2]
                    lot[0] -= take
                    qty -= take
                    if lot[0] == 0:
                        lots.pop(0)
                if cost:
                    reason = "na"
                    if isinstance(r.notes, str):
                        m = SELL_NOTE_RE.search(r.notes)
                        if m:
                            reason = m.group(1)
                    trips.append(dict(code=code, date=r.d, entry=entry, reason=reason,
                                      pnl=r.net_amount - cost,
                                      ret=(r.net_amount / cost - 1) * 100,
                                      invested=cost))
    return pd.DataFrame(trips)


def summarize(arm_dir: str, db_path: str | None) -> dict:
    period = load_period(arm_dir)
    out: dict[str, object] = {"arm": os.path.basename(arm_dir.rstrip("/")), "dir": arm_dir}
    if period is not None and not period.empty:
        out["ret"] = period["ret"].iloc[-1]
        out["final"] = period["value"].iloc[-1]
        out["maxdd"] = period["dd"].min()
        out["txns"] = int(period["txns"].sum())
        out["sessions"] = len(period)
        out["active_days"] = int((period["txns"] > 0).sum())
    else:
        out["ret"] = float("nan")
    db_path = db_path or os.path.join(arm_dir, "ledger.db")
    if os.path.exists(db_path):
        trips = fifo_trades(db_path)
        if not trips.empty:
            out["trades"] = len(trips)
            out["win"] = (trips.pnl > 0).mean() * 100
            out["avg_ret"] = trips.ret.mean()
            out["med_ret"] = trips.ret.median()
            out["avg_size"] = trips.invested.mean()
            out["pnl"] = trips.pnl.sum()
            out["reasons"] = trips.reason.value_counts().to_dict()
            out["reason_pnl"] = trips.groupby("reason").pnl.sum().round(0).to_dict()
    return out


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    dirs, explicit_db, i = [], None, 0
    while i < len(argv):
        if argv[i] == "--db":
            explicit_db = argv[i + 1]
            i += 2
            continue
        dirs.append(argv[i])
        i += 1

    rows = [summarize(d, explicit_db) for d in dirs]
    head = f"{'arm':<26}{'ret%':>8}{'maxDD%':>8}{'txns':>7}{'trades':>7}{'win%':>7}{'avg%':>7}{'med%':>7}{'avgSize':>10}{'PnL':>11}"
    print(head)
    print("-" * len(head))
    for r in rows:
        print(f"{r['arm']:<26}{r.get('ret', float('nan')):>8.2f}{r.get('maxdd', 0):>8.2f}"
              f"{r.get('txns', 0):>7}{r.get('trades', 0):>7}{r.get('win', 0):>7.1f}"
              f"{r.get('avg_ret', 0):>7.2f}{r.get('med_ret', 0):>7.2f}"
              f"{r.get('avg_size', 0):>10,.0f}{r.get('pnl', 0):>11,.0f}")
        if "reasons" in r:
            detail = ", ".join(f"{k} {v}" for k, v in sorted(r["reasons"].items(),
                                                            key=lambda kv: -kv[1])[:5])
            pnl = ", ".join(f"{k} {v:,.0f}" for k, v in sorted(r["reason_pnl"].items(),
                                                               key=lambda kv: kv[1])[:4])
            print(f"{'':<26}exits: {detail}")
            print(f"{'':<26}pnl:   {pnl}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
