#!/usr/bin/env python3
"""Simulate real trading across a date range, one trading day at a time.

    python trading_test/run_trading_test.py --start 20260901 --end 20260930
    python trading_test/run_trading_test.py --start 20260901 --end 20260930 --cash 300000 --no-app

ONE account carries from `start` to `end`: cash, holdings, entry dates, T+1. Each trading day:

  1. PRE-MARKET (09:15-09:30)  prices from the PREVIOUS TRADING DAY'S CLOSE. Build the buys for
                               that day's picks (auction limit off the previous close) and the
                               held positions' brackets / scheduled exits.
  2. MARKET (09:30-11:30, 13:00-15:00)  walk the day's intraday bars through matcher.run_day:
                               auction fill at the open, limit fills intraday, gap-aware TP/SL,
                               scheduled exits at the open, T+1, retail fees.
  3. POST-MARKET               mark to market at the last bar's close; write the day's artifacts.

Per-day artifacts land in `trading_test/results/<start>_<end>_trading/` in the same shapes the
backtest uses — `report_trading_<date>.md`, `smart_orders_<date>.json`, `day_state_<date>.json` —
plus `daily_pv.json` and `trades.json` for the whole run. `result_report.md` summarises the period
and compares it against the backtest for the same window.

FILL REALISM: this deliberately does NOT book exits at the exact trigger price the way the backtest
does — a stop that gaps through fills at the open. Expect a lower number than the backtest's own
report for the same dates. That gap is the point of running it.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date as _date, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))


def _find_repo_root() -> str:
    """Walk up to the directory holding both `backtest/` and `trading/`.

    A fixed `dirname()` count here pointed one level too high (`apps/`), which made `import
    backtest` fail silently behind its try/except: the calendar and regime fell back, the strategy
    config never loaded, and the run still looked like it worked.
    """
    d = HERE
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, "backtest")) and os.path.isdir(os.path.join(d, "trading")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(HERE)


REPO_ROOT = _find_repo_root()
for _p in (HERE, REPO_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)      # REPO_ROOT too: `import backtest.*` needs it when run as a script

import app_state              # noqa: E402
import bars as bars_mod       # noqa: E402
import plan as plan_mod       # noqa: E402
import report as report_mod   # noqa: E402
from matcher import Fees, Position, run_day  # noqa: E402
from state import SimState, trading_days_after  # noqa: E402

CONFIG_PATH = os.path.join(HERE, "sim_config.json")


def load_config() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ─── calendar ────────────────────────────────────────────────
def get_calendar():
    try:
        from backtest.utils.trading_calendar import calendar
        return calendar
    except Exception:  # noqa: BLE001
        return None


def trading_days(start: str, end: str, cal) -> tuple[list[str], str]:
    """Every trading day in [start, end]. Returns (days, how) where `how` says which method."""
    if cal is not None:
        try:
            days = cal.get_trading_days_between(start, end)
            days = [str(d).replace("-", "")[:8] for d in days]
            days = sorted(d for d in days if start <= d <= end)
            if days:
                return days, "project trading calendar"
        except Exception:  # noqa: BLE001
            pass
    out, d = [], _date(int(start[:4]), int(start[4:6]), int(start[6:]))
    last = _date(int(end[:4]), int(end[4:6]), int(end[6:]))
    while d <= last:
        if d.weekday() < 5:                       # weekdays only — Chinese holidays NOT handled
            out.append(d.strftime("%Y%m%d"))
        d += timedelta(days=1)
    return out, "weekday fallback (holidays not excluded)"


def prev_trading_day(day: str, cal) -> str:
    if cal is not None:
        try:
            prev = cal.get_previous_trading_day(day)
            if prev:
                return str(prev).replace("-", "")[:8]
        except Exception:  # noqa: BLE001
            pass
    d = _date(int(day[:4]), int(day[4:6]), int(day[6:])) - timedelta(days=1)
    return d.strftime("%Y%m%d")


def regime_max_hold(day: str, cal, fallback: int = 1) -> tuple[int, str, str]:
    """The engine's max_hold_days for this date: config base x HOLD_DAYS_MULT, floored at 1."""
    regime = "normal"
    try:
        from backtest.utils.market_regime import detect_market_regime
        detected = detect_market_regime(day)
        # It logs "Market regime detected: BEAR" but does not hand back a plain string — pull the
        # name out of whatever shape it returns, or every lookup silently lands on the fallback.
        if isinstance(detected, str):
            regime = detected
        elif isinstance(detected, dict):
            regime = detected.get("regime") or detected.get("pattern") or detected.get("name") or regime
        else:
            regime = (getattr(detected, "regime", None) or getattr(detected, "pattern", None)
                      or getattr(detected, "value", None) or regime)
        regime = str(regime).strip().lower()
    except Exception:  # noqa: BLE001
        pass
    try:
        cfg = json.load(open(os.path.join(REPO_ROOT, "backtest", "config.json"), encoding="utf-8"))
        rules = cfg.get("trading_rules", {}).get("risk_reward_ratios", {})
        base = rules.get(f"{regime}_market", rules.get(regime, {})).get("max_hold_days")
        if base is None:
            base = fallback
        mult = float(os.getenv("HOLD_DAYS_MULT", "1") or 1)
        return max(1, int(float(base) * mult)), regime, "config.json x HOLD_DAYS_MULT"
    except Exception as e:  # noqa: BLE001
        return fallback, regime, f"fallback ({type(e).__name__})"


# ─── the range ───────────────────────────────────────────────
def run_range(cfg: dict, args) -> dict:
    cal = get_calendar()
    days, how = trading_days(args.start, args.end, cal)
    out_dir = os.path.join(HERE, "results",
                           f"{args.start}_{args.end}_trading" + (f"_{args.tag}" if args.tag else ""))
    os.makedirs(out_dir, exist_ok=True)

    state_in = app_state.load_state(prefer_app=not args.no_app, user_id=args.user_id)
    cash = args.cash if args.cash is not None else (state_in.get("cash") or cfg.get("cash", 300000.0))
    holdings_in = [] if args.no_holdings else state_in.get("holdings", [])
    if args.no_holdings:
        state_in = {**state_in, "holdings": [],
                    "note": f"{state_in.get('note')} — holdings EXCLUDED by --no-holdings "
                            f"(strategy-only run; the account's existing positions are ignored)"}
    state = SimState.from_account(cash, holdings_in, initial_cash=cfg.get("cash", cash))

    fees_cfg = cfg.get("fees", {})
    fees = Fees(commission_pct=float(fees_cfg.get("commission_pct", 0.00025)),
                commission_min=float(fees_cfg.get("commission_min", 5.0)),
                stamp_duty_pct=float(fees_cfg.get("stamp_duty_pct", 0.0005)))

    store = bars_mod.BarStore()
    day_summaries: list[dict] = []
    prices: dict[str, float] = {}

    print(f"[run] {args.start}..{args.end}  {len(days)} trading days ({how})")
    print(f"[run] cash {cash:,.2f} · {len(state.holdings)} holdings from {state_in.get('source')}")
    print(f"[run] out {out_dir}")

    for i, day in enumerate(days, 1):
        base = prev_trading_day(day, cal)
        picks, pick_path = plan_mod.load_picks(day, cfg.get("pick_source", "live"))
        pick_codes = [c for c in (p.get("symbol") for p in picks) if c]
        watch = sorted(set(pick_codes) | set(state.holdings))

        # pre-market reference prices: the previous trading day's close
        prev_closes: dict[str, float] = {}
        for code in watch:
            px = store.prev_close(code, base)
            if px:
                prev_closes[code] = px

        # the day's bars
        day_bars = {c: b for c in watch if (b := store.intraday(c, day))}
        closes = {c: b[-1]["close"] for c, b in day_bars.items()}

        # hold expiry -> scheduled exits, exactly the engine's exclusive counting
        max_hold, regime, hold_src = regime_max_hold(day, cal)
        if cfg.get("max_hold_days") is not None:
            max_hold, hold_src = int(cfg["max_hold_days"]), "sim_config.json override"
        positions: list[Position] = []
        for code, h in state.holdings.items():
            held_after = trading_days_after(h.entry_date, day, cal) if h.entry_date else 0
            scheduled = bool(h.entry_date) and held_after > max_hold
            if code.upper() in {c.upper() for c in cfg.get("force_exit_codes", [])}:
                scheduled = True
            positions.append(Position(
                code=code, name=h.name, qty=h.qty, cost=h.cost, sellable=h.sellable,
                tp=round(h.cost * float(cfg.get("tp_mult", 3.0)), 2),
                sl=round(h.cost * (1 - float(cfg.get("sl_pct", 0.025))), 2),
                scheduled_exit=scheduled, entry_date=h.entry_date))

        # the day's buys, sized off this simulation's cash
        held = {c.upper() for c in state.holdings}
        room = max(0, int(cfg.get("max_positions", 10)) - len(state.holdings))
        buys, plan_notes, plan_path = plan_mod.derive_plan(
            day, state.cash, {**cfg, "max_positions": int(cfg.get("max_positions", 10))},
            prev_closes, already_held=held,
            names=app_state.names_for(pick_codes, day, args.user_id))
        buys = buys[:room]

        res = run_day(day, buys, positions, day_bars, state.cash, fees)

        # pre-market plan file, shaped like the backtest's smart_orders_<date>.json
        orders = [{"symbol": b.code, "name": b.name, "buy_quantity": b.qty,
                   "buy_price": b.limit, "current_price": prev_closes.get(b.code),
                   "sell_take_profit_price": b.tp, "sell_stop_loss_price": b.sl,
                   "side": "BUY"} for b in buys]
        orders += [{"symbol": p.code, "name": p.name + ("_expired" if p.scheduled_exit else ""),
                    "buy_quantity": p.sellable, "current_price": prev_closes.get(p.code),
                    "sell_take_profit_price": p.tp, "sell_stop_loss_price": p.sl,
                    "side": "SELL"} for p in positions if p.sellable > 0]
        with open(os.path.join(out_dir, f"smart_orders_{day}.json"), "w", encoding="utf-8") as f:
            json.dump({"date": day, "base_date": base, "cash": round(state.cash, 2),
                       "regime": regime, "max_hold_days": max_hold,
                       "total_new_BUY_orders": len(buys),
                       "total_orders": len(orders), "smart_orders": orders,
                       "notes": plan_notes}, f, ensure_ascii=False, indent=2)

        prices = {c: closes[c] for c in day_bars}
        row = state.roll_from_result(day, res, prices)

        md = report_mod.day_report(day, base, regime, max_hold, hold_src, pick_path, plan_path,
                                   plan_notes, positions, buys, res, row, state,
                                   day_bars, store, state_in)
        with open(os.path.join(out_dir, f"report_trading_{day}.md"), "w", encoding="utf-8") as f:
            f.write(md)
        with open(os.path.join(out_dir, f"day_state_{day}.json"), "w", encoding="utf-8") as f:
            json.dump(state.snapshot(), f, ensure_ascii=False, indent=2)

        day_summaries.append({"day": day, "regime": regime, "max_hold": max_hold,
                              "picks": len(picks), "buys": len(buys),
                              "sells": row["n_sells"], "equity": row["equity"],
                              "realized": row["realized_day"], "fees": row["fees_day"],
                              "unpriced": row["unpriced"],
                              "notes": plan_notes})
        print(f"[{i:>2}/{len(days)}] {day} {regime:<8} picks {len(picks):>2} buys {len(buys):>2} "
              f"sells {row['n_sells']:>2}  equity {row['equity']:>13,.2f}  "
              f"pnl {row['realized_day']:>+12,.2f}")

    with open(os.path.join(out_dir, "daily_pv.json"), "w", encoding="utf-8") as f:
        json.dump(state.daily, f, ensure_ascii=False, indent=2)
    with open(os.path.join(out_dir, "trades.json"), "w", encoding="utf-8") as f:
        json.dump(state.ledger, f, ensure_ascii=False, indent=2)

    diag = store.diagnostics()
    result = {"start": args.start, "end": args.end, "days": days, "calendar_method": how,
              "state_in": {k: v for k, v in state_in.items() if k != "holdings"},
              "initial_cash": state.initial_cash, "assets_in": state.assets_in,
              "final_cash": state.cash, "final_equity": state.equity(prices),
              "realized_total": state.realized_total, "fees_total": state.fees_total,
              "max_drawdown_pct": state.max_drawdown_pct(),
              "daily": state.daily, "ledger": state.ledger,
              "day_summaries": day_summaries,
              "unpriced_days": state.unpriced_days,
              "warnings": state.warnings,
              "bars": {c: diag["bars_per_day"].get(c) for c in diag["bars_per_day"]},
              "missing_bars": diag["missing"],
              "config": {k: v for k, v in cfg.items() if not k.startswith("_")}}
    md = report_mod.result_report(result, REPO_ROOT)
    with open(os.path.join(out_dir, "result_report.md"), "w", encoding="utf-8") as f:
        f.write(md)
    with open(os.path.join(out_dir, "run.json"), "w", encoding="utf-8") as f:
        json.dump({k: v for k, v in result.items() if k not in ("ledger", "daily")},
                  f, ensure_ascii=False, indent=2)

    print(f"\n[result] {md.splitlines()[0]}")
    print(f"[written] {out_dir}/result_report.md")
    return result


def main(argv=None) -> int:
    cfg = load_config()
    ap = argparse.ArgumentParser(description="Simulate real trading across a date range.")
    ap.add_argument("--start", default=cfg.get("start"))
    ap.add_argument("--end", default=cfg.get("end"))
    ap.add_argument("--cash", type=float, default=None)
    ap.add_argument("--no-app", action="store_true", help="skip the app read, use the DB")
    ap.add_argument("--no-holdings", action="store_true",
                    help="ignore the account's existing positions: strategy-only run")
    ap.add_argument("--tag", default="", help="suffix for the output directory (keeps runs side by side)")
    ap.add_argument("--user-id", type=int, default=1)
    ap.add_argument("--max-hold-days", type=int, default=None, help="override the regime max_hold")
    args = ap.parse_args(argv)
    if args.max_hold_days is not None:
        cfg["max_hold_days"] = args.max_hold_days
    if not args.start or not args.end:
        ap.error("--start and --end are required (or set them in sim_config.json)")
    run_range(cfg, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
