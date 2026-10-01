"""Measure how optimistic a backtest run's fill assumptions were.

Re-prices the run's own per-day order reports (`report_orders_*.md`) and reports what the same
trades would have returned under realistic execution. Nothing is re-simulated: picks, sizes and
dates are taken as-is, so the deltas isolate the fill model.

Why this exists: with `SELL_OPEN_PRICE=true` (the default) the engine books a stop-loss or
take-profit exit at the exact trigger price even when the day gapped through it and never traded
there. On the 193.58% run, 143 of 575 stops were booked above that day's own high. This script
quantifies that, plus slippage and commission, over the measured turnover.

Usage:
    python backtest/analysis/execution_realism.py backtest/results/20260101_20260930_ts_7AZ_96MA_flow_review
"""

from __future__ import annotations

import glob
import os
import re
import sys
from collections import defaultdict

NUM = r"¥?\s*(-?[\d,]+(?:\.\d+)?)"
COMMISSION_DEFAULT = 0.0000341      # config.json portfolio_config.commission
COMMISSION_MIN = 5.0
STAMP_DUTY = 0.0005                 # 0.05%, sells only


def _num(s: str | None) -> float | None:
    return float(s.replace(",", "")) if s else None


def parse_round_trips(results_dir: str) -> list[dict]:
    """One record per closed position, with the trade's TP/SL and that day's OHLC."""
    out: list[dict] = []
    for path in sorted(glob.glob(os.path.join(results_dir, "report_orders_*.md"))):
        text = open(path, encoding="utf-8", errors="replace").read()
        day = re.search(r"\*\*Trading Date:\*\*\s*(\d{8})", text)
        if not day:
            continue
        for block in re.split(r"\n### ", text)[1:]:
            def grab(pattern: str) -> float | None:
                m = re.search(pattern, block)
                return _num(m.group(1)) if m else None

            reason = re.search(r"Exit Reason:\s*\**\s*([A-Za-z_ ]+)", block)
            rec = {
                "date": day.group(1),
                "symbol": block.split(" - ")[0].strip(),
                "tp": grab(r"Take Profit:\s*" + NUM),
                "sl": grab(r"Stop Loss:\s*" + NUM),
                "qty": grab(r"Quantity:\s*" + NUM),
                "entry": grab(r"Entry Price:\s*" + NUM) or grab(r"Fill Price:\s*" + NUM),
                "exit": grab(r"Exit Price:\s*" + NUM),
                "reason": reason.group(1).strip() if reason else None,
                "open": grab(r"(?<!Prev )Open:\s*" + NUM),
                "high": grab(r"(?<!recent )High:\s*" + NUM),
                "low": grab(r"(?<!recent )Low:\s*" + NUM),
                "close": grab(r"(?<!Prev )Close:\s*" + NUM),
            }
            if rec["exit"] is not None and rec["entry"] is not None and rec["qty"]:
                out.append(rec)
    return out


def real_fill(rec: dict) -> float:
    """What the fill would have been, mirroring engine.py's SELL_OPEN_PRICE=false branch."""
    if rec["reason"] == "STOP_LOSS":
        # A stop order triggers at the open when the day gapped through the stop.
        return rec["open"] if rec["open"] <= rec["sl"] else rec["sl"]
    if rec["reason"] == "ORDER_EXPIRED_BEFORE_SELL":
        return rec["open"]           # already sells at the open
    return rec["close"]              # trend / max-hold exits already sell at the close


def pnl(rec: dict, sell_fill: float, slip_sell: float = 0.0, slip_buy: float = 0.0,
        commission: float = COMMISSION_DEFAULT) -> float:
    sell = sell_fill * (1 - slip_sell)
    buy = rec["entry"] * (1 + slip_buy)
    qty = rec["qty"]
    gross = sell * qty
    net = gross - max(gross * commission, COMMISSION_MIN) - gross * STAMP_DUTY
    cost = buy * qty + max(buy * qty * commission, COMMISSION_MIN)
    return net - cost


def report(results_dir: str, initial_cash: float = 600_000.0) -> None:
    trips = parse_round_trips(results_dir)
    if not trips:
        print(f"no report_orders_*.md found under {results_dir}")
        return
    print(f"{results_dir}\n{len(trips)} closed round-trips\n")

    stops = [r for r in trips if r["reason"] == "STOP_LOSS"]
    never = [r for r in stops if r["high"] < r["sl"] - 1e-9]
    gapped = [r for r in stops if r["open"] < r["sl"] - 1e-9]
    buy_notional = sum(r["entry"] * r["qty"] for r in trips)
    sell_notional = sum(r["exit"] * r["qty"] for r in trips)

    print("fill-model optimism")
    print(f"  stops booked at a price the day never traded : {len(never):>4} / {len(stops)}"
          f"  ({len(never)/len(stops):.1%})" if stops else "")
    print(f"  stops that opened below their stop          : {len(gapped):>4} / {len(stops)}"
          f"  ({len(gapped)/len(stops):.1%})" if stops else "")
    print(f"  take-profit exits                           : "
          f"{sum(1 for r in trips if r['reason'] == 'take_profit'):>4} / {len(trips)}")
    print(f"  turnover                                    : "
          f"¥{(buy_notional + sell_notional)/1e6:.0f}M on ¥{initial_cash:,.0f} "
          f"({(buy_notional + sell_notional)/initial_cash:.0f}x)\n")

    scenarios = [
        ("as booked (SELL_OPEN_PRICE=true)", lambda r: (r["exit"], 0.0, 0.0, COMMISSION_DEFAULT)),
        ("gap-aware stops (SELL_OPEN_PRICE=false)", lambda r: (real_fill(r), 0.0, 0.0, COMMISSION_DEFAULT)),
        ("+ 0.2% sell / 0.1% buy slippage", lambda r: (real_fill(r), 0.002, 0.001, COMMISSION_DEFAULT)),
        ("+ retail commission (0.025%)", lambda r: (real_fill(r), 0.002, 0.001, 0.00025)),
        ("+ 0.5% sell / 0.2% buy slippage", lambda r: (real_fill(r), 0.005, 0.002, 0.00025)),
    ]
    print(f"{'scenario':<42}{'realized P&L':>15}{'return':>10}")
    for name, fn in scenarios:
        total = sum(pnl(r, *fn(r)) for r in trips)
        print(f"{name:<42}{total:>15,.0f}{(initial_cash + total)/initial_cash - 1:>10.2%}")

    by_reason: dict[str, float] = defaultdict(float)
    for r in trips:
        by_reason[r["reason"]] += pnl(r, r["exit"])
    print("\nP&L by exit type")
    for reason in sorted(by_reason, key=lambda k: -by_reason[k]):
        print(f"  {reason:<28}{by_reason[reason]:>+13,.0f}")
    scheduled = sum(v for k, v in by_reason.items() if k != "STOP_LOSS")
    print(f"  -> the scheduled exits carry ¥{scheduled:,.0f}; a TP/SL-only bot forfeits them")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    report(sys.argv[1])
