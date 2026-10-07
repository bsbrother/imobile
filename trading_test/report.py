"""Report rendering: one file per trading day, plus the period result_report.md.

The per-day file deliberately mirrors the backtest's `report_orders_<date>.md` headlines
(Orders Executed / Total Invested / Realized P&L (Today) / Cash Remaining) so the two can be read
side by side, and adds what a simulation needs: where the prices came from, which positions could
not be priced, and why each order filled where it did.
"""
from __future__ import annotations

import glob
import os
import re
from typing import Any


def _money(x: float) -> str:
    return f"¥{x:,.2f}"


def _pv(row: dict) -> float:
    return float(row.get("equity") or 0.0)


# ─── per day ─────────────────────────────────────────────────
def day_report(day: str, base: str, regime: str, max_hold: int, hold_src: str,
               pick_path: str | None, plan_path: str | None, plan_notes: list[str],
               positions: list, buys: list, res, row: dict, state, day_bars: dict,
               store, state_in: dict) -> str:
    L: list[str] = []
    add = L.append
    add(f"# Trading Simulation Report — {day}")
    add("")
    add(f"- **Base Date (pre-market prices):** {base}")
    add(f"- **Regime:** {regime}   - **Max Hold Days:** {max_hold} ({hold_src})")
    add(f"- **Picks:** from `{_rel(pick_path)}`" if pick_path else "- **Picks:** none found")
    invested = sum(f["price"] * f["qty"] for f in res.fills if f["side"] == "BUY")
    buys_planned = len(buys)
    executed = sum(1 for f in res.fills if f["side"] == "BUY")
    add(f"- **Orders Executed:** {executed}/{buys_planned}")
    add(f"- **Total Invested:** {_money(invested)}")
    add(f"- **Realized P&L (Today):** {_money(res.realized)}")
    add(f"- **Fees (Today):** {_money(res.fees_paid)}")
    add(f"- **Cash Remaining:** {_money(state.cash)}")
    ret = (row["equity"] / state.assets_in - 1) * 100 if state.assets_in else 0.0
    add(f"- **Total Assets:** {_money(row['equity'])}  ({ret:+.2f}% vs assets in {_money(state.assets_in)})")
    add("")

    add("## Pre-market plan")
    add("")
    if plan_notes:
        for n in plan_notes:
            add(f"- {n}")
    if buys:
        add("")
        add("| Buy | Name | Qty | Limit (auction) | TP | SL | Prev close |")
        add("|---|---|---|---|---|---|---|")
        for b in buys:
            prev = store.prev_close(b.code, base)
            add(f"| {b.code} | {b.name} | {b.qty} | {b.limit:.2f} | {b.tp} | {b.sl} | "
                f"{f'{prev:.2f}' if prev else '—'} |")
    if positions:
        add("")
        add("| Hold | Name | Qty | Sellable | Cost | TP | SL | Exit order |")
        add("|---|---|---|---|---|---|---|---|")
        for p in positions:
            kind = "scheduled (force-sell at open)" if p.scheduled_exit else "TP/SL bracket"
            add(f"| {p.code} | {p.name} | {p.qty} | {p.sellable} | {p.cost:.3f} | {p.tp} | {p.sl} | {kind} |")
    if not buys and not positions:
        add("- nothing to do today")
    add("")

    add("## Fills")
    add("")
    if res.fills:
        add("| Time | Side | Code | Name | Qty | Price | Fees | Reason |")
        add("|---|---|---|---|---|---|---|---|")
        for f in res.fills:
            add(f"| {f['dt'][11:19]} | {f['side']} | {f['code']} | {f['name']} | {f['qty']} | "
                f"{f['price']:.2f} | {f['fees']:.2f} | {f['reason']} |")
    else:
        add("- no fills today")
    add("")

    add("## Holdings (end of day)")
    add("")
    if res.holdings:
        add("| Code | Name | Qty | Entry | Sold? | Cost | Close | Unrealized | Priced? |")
        add("|---|---|---|---|---|---|---|---|---|")
        for code, h in sorted(res.holdings.items()):
            priced = code in day_bars
            up = (h["last_price"] - h["cost"]) * h["qty"] if priced else 0.0
            add(f"| {code} | {h['name']} | {h['qty']} | {h.get('entry_date') or 'legacy'} | "
                f"{'T+1 hold' if h.get('bought_today') else 'sellable'} | {h['cost']:.3f} | "
                f"{h['last_price']:.2f} | {up:+,.2f} | {'yes' if priced else 'NO — at cost'} |")
    else:
        add("- flat")
    add("")

    add("## Notes")
    add("")
    for n in res.notes:
        add(f"- {n}")
    for a in res.same_bar_ambiguity:
        add(f"- caveat: {a} (TP assumed first)")
    per_day = max((store.bars_per_day(c) for c in day_bars), default=0)
    add(f"- bars: {per_day} per day ({', '.join(f'{c}:{len(b)}' for c, b in sorted(day_bars.items()))})")
    missing = [c for c in {*(p.code for p in positions), *(b.code for b in buys)} if c not in day_bars]
    if missing:
        add(f"- no intraday bars (not tradeable today): {', '.join(sorted(missing))}")
    add(f"- account source: {state_in.get('source')} — {state_in.get('note')}")
    add("- exit fills are gap-aware (`min(sl, open)` / `max(tp, open)`), NOT booked at the exact "
        "trigger price the way the backtest books them.")
    add("")
    return "\n".join(L)


# ─── period ──────────────────────────────────────────────────
def result_report(result: dict, repo_root: str) -> str:
    L: list[str] = []
    add = L.append
    days = result["days"]
    initial = float(result["initial_cash"])
    assets_in = float(result["assets_in"])
    final = float(result["final_equity"])
    total_ret = (final / assets_in - 1) * 100 if assets_in else 0.0
    sells = [f for f in result["ledger"] if f["side"] == "SELL"]
    buys = [f for f in result["ledger"] if f["side"] == "BUY"]
    unpriced_total = sum(len(v) for v in result["unpriced_days"].values())

    add(f"# Trading Test Result — {result['start']} to {result['end']}")
    add("")
    add(f"Simulated real trading over {len(days)} trading days, one account carried start to finish "
        f"({result['calendar_method']}). Prices: previous close for the pre-market plan, intraday "
        f"bars for the session.")
    add("")
    add("## Headline")
    add("")
    add("| Metric | Value |")
    add("|---|---|")
    add(f"| **Initial cash** | {_money(initial)} |")
    add(f"| **Assets in** (cash + starting holdings at cost) | {_money(assets_in)} |")
    add(f"| **Final equity** | {_money(final)} |")
    add(f"| **Total return** | **{total_ret:+.2f}%** |")
    add(f"| **Max drawdown** | {result['max_drawdown_pct']:+.2f}% |")
    add(f"| **Trading days** | {len(days)} |")
    add(f"| **Trades** | {len(result['ledger'])} ({len(buys)} buys / {len(sells)} sells) |")
    add(f"| **Realized P&L** | {_money(result['realized_total'])} |")
    add(f"| **Fees paid** | {_money(result['fees_total'])} |")
    add(f"| **Final cash** | {_money(result['final_cash'])} |")
    add(f"| **Positions left** | {result['daily'][-1]['holdings'] if result['daily'] else 0} |")
    add("")
    if unpriced_total:
        add(f"> **Partial run:** holdings without intraday bars were carried at cost on "
            f"{len(result['unpriced_days'])} day(s) ({unpriced_total} position-days). Their P&L is "
            f"NOT in the figures above.")
        add("")

    add("## Daily performance")
    add("")
    add("| Date | Buys | Sells | Realized P&L | Fees | Equity | Return | Positions | Unpriced |")
    add("|---|---|---|---|---|---|---|---|---|")
    for r in result["daily"]:
        ret = (r["equity"] / assets_in - 1) * 100 if assets_in else 0.0
        add(f"| {r['day']} | {r['n_buys']} | {r['n_sells']} | {r['realized_day']:+,.2f} | "
            f"{r['fees_day']:,.2f} | {r['equity']:,.2f} | {ret:+.2f}% | {r['holdings']} | "
            f"{r['unpriced'] or ''} |")
    add("")

    add("## Exit attribution")
    add("")
    groups: dict[str, dict[str, Any]] = {}
    for f in sells:
        key = _exit_kind(f["reason"])
        g = groups.setdefault(key, {"n": 0, "qty": 0, "realized": 0.0})
        g["n"] += 1
        g["qty"] += f["qty"]
        g["realized"] += f.get("realized", 0.0)
    add("| Exit | Count | Shares | Realized |")
    add("|---|---|---|---|")
    for k, g in sorted(groups.items(), key=lambda kv: -kv[1]["n"]):
        add(f"| {k} | {g['n']} | {g['qty']:,} | {g['realized']:+,.2f} |")
    if not groups:
        add("| — | 0 | 0 | 0.00 |")
    add("")

    add("## Comparison — backtest vs this simulation, same window")
    add("")
    bt = backtest_slice(repo_root, result["start"], result["end"])
    if bt:
        add("| Metric | Backtest (as booked) | This simulation |")
        add("|---|---|---|")
        add(f"| Total return | {bt['return']:+.2f}% | {total_ret:+.2f}% |")
        add(f"| Final portfolio | {_money(bt['final'])} on {_money(bt['base'])} | {_money(final)} on {_money(assets_in)} |")
        add(f"| Max drawdown | {bt['maxdd']:+.2f}% | {result['max_drawdown_pct']:+.2f}% |")
        add(f"| Trades | {bt['txns']} | {len(result['ledger'])} |")
        add(f"| Source | `{bt['path']}` | this run |")
        add("")
        add("Why they differ: the backtest books every exit at its exact trigger price; this run "
            "fills a gap-through at the open, charges commission both sides plus stamp duty on "
            "sells, and refuses to trade symbols with no intraday bars. Same picks, different "
            "fills — the difference is the realism gap, not a bug.")
    else:
        add("- no backtest period report found for this window (expected "
            "`backtest/results/*_ts_7AZ_96MA_flow_review/report_period_*.md`)")
    add("")

    add("## Data limits — read before quoting any number")
    add("")
    per_day = sorted({v for v in result["bars"].values() if v})
    add(f"- **Intraday granularity: {per_day[0] if per_day else 'n/a'} bars/day.** The cache holds "
        f"30-minute bars (8/day); there is no 1-minute and no 09:15-09:25 auction data reachable on "
        f"this host, so the call auction is modelled as a limit checked against the 09:30 open.")
    add("- **The order of high and low inside a bar is unknown.** A bar touching both TP and SL is "
        "resolved TP-first (the engine's priority) and flagged in that day's report.")
    add("- **No partial fills, no queue position, no cap or liquidity model, no slippage beyond the "
        "gap rule.** A planned quantity trades in full or not at all.")
    if result["missing_bars"]:
        add(f"- **{len(result['missing_bars'])} watched symbol(s) have no cached bars at all** "
            f"({', '.join(sorted(result['missing_bars'])[:12])}"
            f"{' …' if len(result['missing_bars']) > 12 else ''}). Those positions were carried at "
            f"cost and are excluded from P&L.")
    add(f"- Account state came from `{result['state_in'].get('source')}` "
        f"({result['state_in'].get('note')}).")
    add(f"- Calendar: {result['calendar_method']}.")
    if result.get("warnings"):
        add("")
        add(f"**Bookkeeping warnings ({len(result['warnings'])})** — a position the account held was "
            f"not passed to the matcher on these days and was carried unchanged:")
        for w in result["warnings"][:20]:
            add(f"  - {w}")
        if len(result["warnings"]) > 20:
            add(f"  - … and {len(result['warnings']) - 20} more")
    add("")
    return "\n".join(L)


def _exit_kind(reason: str) -> str:
    r = (reason or "").lower()
    if "scheduled exit" in r:
        return "scheduled exit (force-sell / expiry)"
    if "take-profit" in r:
        return "take-profit" + (" (gap fill)" if "gap" in r else "")
    if "stop-loss" in r:
        return "stop-loss" + (" (gap fill)" if "gap" in r else "")
    return reason or "other"


def _rel(path: str | None) -> str:
    return os.path.basename(path) if path else "n/a"


# ─── backtest comparison ─────────────────────────────────────
_ROW_RE = re.compile(r"^\|\s*(\d{8})\s*\|(.*?)\|\s*$")
_PV_RE = re.compile(r"¥\s*([\d,]+\.\d+)")


def backtest_slice(repo_root: str, start: str, end: str) -> dict[str, Any] | None:
    """The backtest's own numbers for [start, end], parsed from its period report's daily table."""
    cands = [p for p in glob.glob(os.path.join(repo_root, "backtest", "results", "*",
                                               "report_period_*.md"))
             if "results_backups" not in p and "trading_test" not in p]
    if not cands:
        return None
    path = max(cands, key=os.path.getmtime)
    rows: list[tuple[str, float, int]] = []
    for line in open(path, encoding="utf-8"):
        m = _ROW_RE.match(line)
        if not m:
            continue
        day, rest = m.group(1), m.group(2)
        cells = [c.strip() for c in rest.split("|")]
        # columns: Txns | Sells | Realized | Unrealized | Total | Portfolio Value | Positions
        if len(cells) < 6:
            continue
        pv = _PV_RE.search(cells[5])
        if not pv:
            continue
        try:
            txns = int(cells[0]) if cells[0].isdigit() else 0
        except ValueError:
            txns = 0
        rows.append((day, float(pv.group(1).replace(",", "")), txns))
    if not rows:
        return None
    rows.sort()
    prior = [r for r in rows if r[0] < start]
    window = [r for r in rows if start <= r[0] <= end]
    if not window:
        return None
    base = prior[-1][1] if prior else window[0][1]
    final = window[-1][1]
    peak, worst = 0.0, 0.0
    for _, pv, _ in window:
        peak = max(peak, pv)
        if peak > 0:
            worst = min(worst, (pv - peak) / peak)
    return {"path": os.path.relpath(path, repo_root), "base": base, "final": final,
            "return": (final / base - 1) * 100 if base else 0.0,
            "maxdd": worst * 100, "txns": sum(r[2] for r in window),
            "days": len(window)}
