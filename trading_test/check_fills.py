"""Verify that every BUY/SELL price a simulation booked could have happened in the real market.

For each fill in a run's `trades.json` this checks the price against the actual cached bars for
that code and day:

  1. RANGE     the price lies inside the day's real low..high — if it does not, no trade could
               have printed there at all
  2. BAR       the price lies inside the low..high of the bar the fill is attributed to
  3. AUCTION   a fill recorded as `auction fill at open` equals the day's opening bar's open
  4. TICK      the price is a valid 0.01 tick
  5. LIMIT     the price is inside the day's price limit band (10%, or 20% for ChiNext/STAR),
               and a BUY never fills above its own declared limit price

Checks 1-4 are about the price being *possible*; 5 is about it being *legal*.

Usage:
    .venv/bin/python trading_test/check_fills.py --run strategy-only
    .venv/bin/python trading_test/check_fills.py --run-with-history
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from bars import load_all  # noqa: E402

RUNS = {
    "with-history": "20260901_20260930_trading",
    "strategy-only": "20260901_20260930_trading_strategy-only",
}

_SERIES: dict[str, list] = {}


def series(code: str) -> list:
    if code not in _SERIES:
        _SERIES[code] = load_all(code)[0]
    return _SERIES[code]


def day_bars(code: str, day: str) -> list:
    prefix = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    return [b for b in series(code) if b["dt"][:10] == prefix]


def prev_close(code: str, day: str) -> float | None:
    prefix = f"{day[:4]}-{day[4:6]}-{day[6:]}"
    prior = [b for b in series(code) if b["dt"][:10] < prefix]
    return prior[-1]["close"] if prior else None


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.utils.limit_board import board_band, fill_block_reason  # noqa: E402


def limit_pct(code: str) -> float:
    """Board band, shared with the engine and the live path (this file used to carry its own copy)."""
    return board_band(code)


def check_fill(f: dict, planned: dict[tuple[str, str], float]) -> list[str]:
    """Return a list of failure strings (empty = every check passed)."""
    bad: list[str] = []
    code, day, price = f["code"], f["day"], float(f["price"])
    side = f["side"]

    if not series(code):
        return [f"NO-DATA: no cached bars for {code} at all"]
    bars = day_bars(code, day)
    if not bars:
        return [f"NO-DATA: {code} has no bars on {day} — this fill could not have happened"]

    lo = min(b["low"] for b in bars)
    hi = max(b["high"] for b in bars)
    if not (lo - 1e-9 <= price <= hi + 1e-9):
        bad.append(f"RANGE: {price:.2f} outside the day's real {lo:.2f}..{hi:.2f}")

    # the bar the fill claims
    same = [b for b in bars if b["dt"] == f["dt"]]
    bar = same[0] if same else None
    if bar is None:
        bad.append(f"BAR: no bar labelled {f['dt']} for {code} on {day}")
    elif not (bar["low"] - 1e-9 <= price <= bar["high"] + 1e-9):
        bad.append(f"BAR: {price:.2f} outside bar {bar['dt']} {bar['low']:.2f}..{bar['high']:.2f}")

    if "auction fill at open" in f.get("reason", ""):
        op = bars[0]["open"]
        if abs(price - op) > 1e-6:
            bad.append(f"AUCTION: fill {price:.2f} != day open {op:.2f}")

    if abs(price * 100 - round(price * 100)) > 1e-6:
        bad.append(f"TICK: {price} is not a 0.01 tick")

    pc = prev_close(code, day)
    if pc:
        up = round(pc * (1 + limit_pct(code)), 2)
        dn = round(pc * (1 - limit_pct(code)), 2)
        if not (dn - 1e-9 <= price <= up + 1e-9):
            bad.append(f"LIMIT: {price:.2f} outside limit band {dn:.2f}..{up:.2f} (prev {pc:.2f})")
        else:
            # Inside the band is not enough. At the limit-up there are no sellers and at the
            # limit-down no buyers, so a fill printed exactly ON the band is impossible — the same
            # rule the engine enforces in execute_buy_order/execute_sell_order.
            impossible = fill_block_reason("sell" if side == "SELL" else "buy", price, pc, code)
            if impossible:
                bad.append(f"BAND: {impossible}")

    if side == "BUY":
        lim = planned.get((day, code))
        if lim is not None and price > lim + 1e-9:
            bad.append(f"ORDER: BUY filled {price:.2f} above its own limit {lim:.2f}")

    # liquidity: the quantity must at least fit inside the real traded volume of that bar
    vol = bar["volume"] if bar else 0.0
    if vol:
        f["_participate"] = f["qty"] / vol * 100.0
        if f["qty"] > vol:
            bad.append(f"LIQUIDITY: qty {f['qty']} exceeds the bar's traded volume {vol:.0f}")
    else:
        f["_participate"] = None

    return bad


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", choices=sorted(RUNS), default="strategy-only")
    ap.add_argument("--results-root", default=os.path.join(HERE, "results"))
    args = ap.parse_args(argv)

    run_dir = os.path.join(args.results_root, RUNS[args.run])
    trades = json.load(open(os.path.join(run_dir, "trades.json"), encoding="utf-8"))

    # declared BUY limits, so a fill can be checked against its own order
    planned: dict[tuple[str, str], float] = {}
    for name in os.listdir(run_dir):
        if name.startswith("smart_orders_") and name.endswith(".json"):
            d = json.load(open(os.path.join(run_dir, name), encoding="utf-8"))
            for o in d.get("smart_orders", []):
                if o.get("side") == "BUY":
                    planned[(d["date"], o["symbol"])] = float(o["buy_price"])

    by_day: dict[str, list] = {}
    failures = 0
    for f in trades:
        bad = check_fill(f, planned)
        failures += 1 if bad else 0
        by_day.setdefault(f["day"], []).append((f, bad))

    print(f"run: {args.run}   fills: {len(trades)}   failures: {failures}")
    parts = sorted(p for f in trades if (p := f.get("_participate")) is not None)
    if parts:
        med = parts[len(parts) // 2]
        over = sum(1 for p in parts if p > 10)
        print(f"liquidity: qty as % of the filled bar's real volume — median {med:.3f}%, "
              f"max {parts[-1]:.3f}%, {over} of {len(parts)} above 10%\n")
    else:
        print()
    for day in sorted(by_day):
        rows = by_day[day]
        mark = "OK  " if all(not b for _, b in rows) else "FAIL"
        print(f"[{mark}] {day}  {len(rows)} fills")
        for f, bad in rows:
            tag = f"{f['side']:<4} {f['code']:<10} {f['qty']:>5} @ {f['price']:>9.2f}  {f['reason']}"
            print(f"        {tag}")
            for b in bad:
                print(f"          !! {b}")

    out = os.path.join(run_dir, "fills_realizability.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(f"# Fill realizability — {args.run} ({RUNS[args.run]})\n\n")
        fh.write("Every BUY/SELL price checked against the real cached bars for that code and day.\n\n")
        fh.write(f"- fills checked: **{len(trades)}**\n- fills failing a check: **{failures}**\n")
        parts = sorted(p for f in trades if (p := f.get("_participate")) is not None)
        if parts:
            fh.write(f"- size as % of the filled bar's real volume: median **{parts[len(parts)//2]:.3f}%**, "
                     f"max **{parts[-1]:.3f}%**\n")
        fh.write("\n")
        fh.write("Checks: RANGE (price inside the day's real low..high), BAR (inside the bar it is\n"
                 "attributed to), AUCTION (open fills equal the day's open), TICK (valid 0.01 step),\n"
                 "LIMIT (inside the day's price-limit band; BUY never above its own limit),\n"
                 "LIQUIDITY (qty fits inside the filled bar's real volume).\n\n")
        fh.write("| Date | Fills | Failing | Detail |\n|---|---|---|---|\n")
        for day in sorted(by_day):
            rows = by_day[day]
            bad_rows = [(f, b) for f, b in rows if b]
            detail = "; ".join(f"{f['side']} {f['code']}: {' / '.join(b)}" for f, b in bad_rows)
            fh.write(f"| {day} | {len(rows)} | {len(bad_rows)} | {detail or '—'} |\n")
    print(f"\n[written] {out}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
