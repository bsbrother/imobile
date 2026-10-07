"""Builds one day's order plan for the simulation.

TWO INPUTS, in the order the request described them:

  pre-market (09:15-09:30)  prices come from the PREVIOUS TRADING DAY'S CLOSE. There are no
                            auction prints for this date anywhere reachable, so the previous
                            close is the only pre-market price that exists. The buy limit is
                            derived from it, and the auction FILL is taken from the first
                            intraday bar's open (09:30) — the price the auction actually clears at.
  market (09:30-15:00)      intraday bars, walked by matcher.run_day.

Two ways to get the day's orders:

  smart   read an existing smart_orders_<date>.json — `live` (backtest/results/daily, what the
          live path actually emitted that day) or `backtest` (the same strategy's orders on the
          backtest's own book). Use this to replay a real day exactly as it was planned.
  derive  build from that day's pick file plus the account holdings, sized to THIS simulation's
          cash. Use this for "what would the strategy have done with a ¥300,000 account".

Both paths produce matcher.BuyOrder / matcher.Position objects. The held positions always come
from the account (see app_state), never from the plan file, so a stale plan cannot invent a
position the account does not have.
"""
from __future__ import annotations

import glob
import json
import math
import os
from typing import Any

from matcher import BuyOrder, Position

def _find_repo_root() -> str:
    """Walk up until the directory that holds both `backtest/` and `trading/` (see bars.py)."""
    d = os.path.dirname(os.path.abspath(__file__))
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, "backtest")) and os.path.isdir(os.path.join(d, "trading")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


REPO_ROOT = _find_repo_root()
DAILY_DIR = os.path.join(REPO_ROOT, "backtest", "results", "daily")
RESULTS_DIR = os.path.join(REPO_ROOT, "backtest", "results")


def daily_band_pct(code: str) -> float:
    """Price-limit band by board. Mirrors trading.runner._daily_band_pct — one rule, one place
    would be better; kept in sync deliberately and asserted equal in the tests."""
    c = (code or "").split(".")[0]
    if c.startswith(("688", "689", "300", "301")):
        return 0.20
    if c.startswith(("4", "8")):
        return 0.30
    return 0.10


def auction_limit(code: str, prev_close: float, mode: str = "limit_up",
                  buffer_pct: float = 0.005, order_price: float | None = None) -> float:
    """Pre-market buy limit, derived from the previous close.

    limit_up          prev_close x (1 + band). Guaranteed >= any possible open, so the order is
                      always in the auction match and fills at the clearing price. This is the
                      convention the live pre_market_run.py uses.
    prev_close_buffer prev_close x (1 + buffer_pct). Conservative: a gap-up above the buffer
                      leaves the order unfilled, which is a real outcome worth being able to see.
    order_price       the plan's own suggested price, for reproducing a plan literally.
    """
    if mode == "order_price":
        return float(order_price or 0.0)
    if mode == "prev_close_buffer":
        return round(prev_close * (1 + buffer_pct), 2)
    return round(prev_close * (1 + daily_band_pct(code)), 2)


def _find(path_glob: str) -> str | None:
    hits = sorted(glob.glob(path_glob))
    return hits[-1] if hits else None


def smart_orders_path(date: str, kind: str = "live") -> str | None:
    if kind == "live":
        return _find(os.path.join(DAILY_DIR, f"smart_orders_{date}.json"))
    hits = [p for p in glob.glob(os.path.join(RESULTS_DIR, "*", f"smart_orders_{date}.json"))
            if "results_backups" not in p]
    return hits[0] if hits else None


def plan_from_smart_orders(date: str, prev_closes: dict[str, float], kind: str = "live",
                           limit_mode: str = "limit_up", buffer_pct: float = 0.005
                           ) -> tuple[list[BuyOrder], list[dict], list[str], str | None]:
    """Read a plan file. Returns (buys, held_order_hints, notes, path).

    The tail entries (name ends `_expired`) are the engine's scheduled exits; they are returned
    as hints only, because whether a position is actually held and sellable comes from the account.
    """
    path = smart_orders_path(date, kind)
    notes: list[str] = []
    if not path:
        notes.append(f"no {kind} smart_orders_{date}.json found")
        return [], [], notes, None
    data = json.load(open(path, encoding="utf-8"))
    n_buys = int(data.get("total_new_BUY_orders", data.get("total_orders", 0)) or 0)
    all_orders = data.get("smart_orders", []) or []
    buys: list[BuyOrder] = []
    for o in all_orders[:n_buys]:
        code, qty = o.get("symbol", ""), int(o.get("buy_quantity", 0) or 0)
        if qty <= 0:
            notes.append(f"plan buy {code} has qty 0 (already held or unfunded) — skipped")
            continue
        prev = prev_closes.get(code) or float(o.get("current_price") or 0.0)
        if not prev:
            notes.append(f"plan buy {code}: no previous close available — skipped")
            continue
        buys.append(BuyOrder(
            code=code, name=o.get("name", code), qty=qty,
            limit=auction_limit(code, prev, limit_mode, buffer_pct, o.get("buy_price")),
            tp=o.get("sell_take_profit_price"), sl=o.get("sell_stop_loss_price"),
            reason=f"plan file ({kind})"))
    hints = []
    for o in all_orders[n_buys:]:
        hints.append({"code": o.get("symbol"), "name": o.get("name", ""),
                      "qty": int(o.get("buy_quantity", 0) or 0),
                      "tp": o.get("sell_take_profit_price"), "sl": o.get("sell_stop_loss_price"),
                      "scheduled_exit": str(o.get("name", "")).endswith("_expired")})
    notes.append(f"plan read from {os.path.relpath(path, REPO_ROOT)}: "
                 f"{len(buys)} buys, {len(hints)} held-position orders")
    return buys, hints, notes, path


def load_picks(date: str, prefer: str = "live") -> tuple[list[dict[str, Any]], str | None]:
    """The strategy's picks for `date` — the live pick file if present, else the backtest run's."""
    cands = []
    if prefer == "live":
        cands.append(os.path.join(DAILY_DIR, f"pick_stocks_{date}.json"))
    cands += [p for p in glob.glob(os.path.join(RESULTS_DIR, "*", f"pick_stocks_{date}.json"))
              if "results_backups" not in p]
    for p in cands:
        if os.path.exists(p):
            try:
                data = json.load(open(p, encoding="utf-8"))
                return data.get("selected_stocks", []) or [], p
            except Exception:  # noqa: BLE001
                continue
    return [], None


def positions_from_account(holdings: list[dict], cfg: dict,
                           entry_dates: dict[str, str] | None = None) -> list[Position]:
    """Account holdings -> matcher positions, with the engine's bracket rules applied.

    tp/sl are the engine's plain ratio rules off the cost basis: tp = cost x tp_mult,
    sl = cost x (1 - sl_pct). A position whose hold window has closed becomes a scheduled exit,
    which the matcher fills at the open — the same exit the backtest books for expired/max-hold.
    """
    tp_mult = float(cfg.get("tp_mult", 3.0))          # +200%, as every order in the run shows
    sl_pct = float(cfg.get("sl_pct", 0.025))          # -2.5%
    force_codes = {c.upper() for c in cfg.get("force_exit_codes", [])}
    out: list[Position] = []
    for h in holdings:
        code = h.get("code")
        if not code or int(h.get("qty", 0)) <= 0:
            continue
        cost = float(h.get("cost") or 0.0)
        if cost <= 0:
            continue
        scheduled = code.upper() in force_codes
        out.append(Position(
            code=code, name=h.get("name", code), qty=int(h["qty"]),
            cost=cost, sellable=int(h.get("sellable") or h["qty"]),
            tp=round(cost * tp_mult, 2), sl=round(cost * (1 - sl_pct), 2),
            scheduled_exit=scheduled,
            entry_date=(entry_dates or {}).get(code, ""),
        ))
    return out


def derive_plan(date: str, cash: float, cfg: dict, prev_closes: dict[str, float],
                already_held: set[str] | None = None, names: dict[str, str] | None = None
                ) -> tuple[list[BuyOrder], list[str], str | None]:
    """Size the day's picks for THIS simulation's cash, the way the live path sizes orders.

    per_slot = cash / max_positions, then whole 100-share lots at the auction limit; a pick whose
    minimum lot does not fit is skipped with a reason, exactly like the live min-lot filter.
    `names` supplies display names — the pick file carries only symbol/rank/score.
    """
    picks, path = load_picks(date, cfg.get("pick_source", "live"))
    names = names or {}
    notes: list[str] = []
    if not picks:
        notes.append(f"no pick file for {date} — no buy orders generated")
        return [], notes, None
    max_pos = int(cfg.get("max_positions", 10))
    per_slot = float(cash) / max(max_pos, 1)
    held = {c.upper() for c in (already_held or set())}
    buys: list[BuyOrder] = []
    for p in picks:
        code = p.get("symbol")
        if not code:
            continue
        name = names.get(code) or p.get("name") or code
        if code.upper() in held:
            notes.append(f"skip {code} {name}: already held (live path re-brackets, does not re-buy)")
            continue
        prev = prev_closes.get(code)
        if not prev:
            notes.append(f"skip {code} {name}: no previous close — cannot price the auction limit")
            continue
        limit = auction_limit(code, prev, cfg.get("buy_limit_mode", "limit_up"),
                              float(cfg.get("auction_buffer_pct", 0.005)), p.get("buy_price"))
        qty = int(math.floor(per_slot / limit / 100.0) * 100)
        if qty < 100:
            notes.append(f"skip {code} {name}: min lot 100 sh at {limit:.2f} = "
                         f"{limit * 100:,.0f} exceeds per-slot cash {per_slot:,.0f}")
            continue
        # The bracket is anchored on the PRE-MARKET REFERENCE PRICE (the previous close), not on
        # the limit: the limit is the board's limit-up, 10-20% above anything the day can open at,
        # so a stop measured from it would sit ABOVE the fill and fire the same morning.
        buys.append(BuyOrder(code=code, name=name, qty=qty, limit=limit,
                             tp=round(prev * float(cfg.get("tp_mult", 3.0)), 2),
                             sl=round(prev * (1 - float(cfg.get("sl_pct", 0.025))), 2),
                             reason=f"derived: per-slot {per_slot:,.0f}"))
    notes.append(f"derived {len(buys)} buys from {os.path.relpath(path, REPO_ROOT) if path else 'picks'} "
                 f"(per-slot {per_slot:,.0f}, max_positions {max_pos})")
    return buys, notes, path
