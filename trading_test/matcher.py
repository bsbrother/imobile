"""Pure fill engine for one trading day. No I/O, no pandas, no network — fully unit-testable.

WHAT IT MODELS
--------------
A-share fills, at the granularity the available data supports:

1. PRE-MARKET / AUCTION (09:15-09:30). Buy orders are placed with a limit price. The auction
   clears at a single price, which is the day's open, so an order whose limit is at or above
   the open is filled at the open — exactly the fill the backtest assumes. The first bar of the
   day (09:30) carries that open.

2. CONTINUOUS SESSION (09:30-11:30, 13:00-15:00). An unfilled buy stays live and fills on the
   first bar whose low reaches its limit, at min(limit, that bar's open) — a limit order never
   pays more than its limit, but a bar that gaps below it fills better.

3. EXITS. A held position's exit orders are server-side conditional orders, so they fire on a
   price touch:
       scheduled exit (tp == sl, the engine's `_expired` force-sell) -> sold on the touch
       take-profit   (high >= tp) -> filled at max(tp, bar open)   <- a gap up fills BETTER
       stop-loss     (low  <= sl) -> filled at min(sl, bar open)   <- a gap down fills WORSE
   The gap handling is the whole point: it is what the backtest omits when it books the exact
   trigger price on 143 of 575 stops that the stock never traded that day.

4. T+1. Shares bought today are not sellable today. Pre-existing positions start sellable.

HONEST LIMITS OF THIS MODEL (carried into every report, not just this docstring):
   * With N-minute bars, the order of the high and the low INSIDE a bar is unknown. When both
     the take-profit and the stop-loss are touched in the same bar, TP is assumed first — the
     same priority the engine uses — and the day is flagged `same_bar_ambiguity`.
   * No partial fills, no queue position, no cap/liquidity impact: the planned quantity always
     trades in full or not at all.
   * The 09:15-09:25 indicative auction prints are not in the data, so the auction limit is
     derived from the previous close (see plan.py) and the fill is taken from the 09:30 open.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# ─── Costs ───────────────────────────────────────────────────
@dataclass(frozen=True)
class Fees:
    """Retail A-share costs. Defaults match the paper sim in stock_cron_tasks.py."""
    commission_pct: float = 0.00025      # 万2.5, both sides
    commission_min: float = 5.0          # ¥ per order
    stamp_duty_pct: float = 0.0005       # 0.05%, sells only

    def buy_charges(self, amount: float) -> float:
        return max(amount * self.commission_pct, self.commission_min)

    def sell_charges(self, amount: float) -> float:
        return max(amount * self.commission_pct, self.commission_min) + amount * self.stamp_duty_pct


# ─── Inputs ──────────────────────────────────────────────────
@dataclass
class BuyOrder:
    code: str
    name: str
    qty: int
    limit: float                 # auction limit price
    tp: float | None = None
    sl: float | None = None
    reason: str = ""


@dataclass
class Position:
    code: str
    name: str
    qty: int
    cost: float
    sellable: int                # T+1-available shares at the start of the day
    tp: float | None = None
    sl: float | None = None
    scheduled_exit: bool = False     # engine force-sell: both triggers sit at one level
    entry_date: str = ""


@dataclass
class DayResult:
    fills: list[dict[str, Any]] = field(default_factory=list)
    holdings: dict[str, dict[str, Any]] = field(default_factory=dict)
    cash: float = 0.0
    realized: float = 0.0
    fees_paid: float = 0.0
    notes: list[str] = field(default_factory=list)
    same_bar_ambiguity: list[str] = field(default_factory=list)
    known: set[str] = field(default_factory=set)   # every code this day was TOLD about

    @property
    def equity(self) -> float:
        return self.cash + sum(h["qty"] * h["last_price"] for h in self.holdings.values())


# ─── Engine ──────────────────────────────────────────────────
def run_day(day: str, buys: Iterable[BuyOrder], positions: Iterable[Position],
            bars: dict[str, list[dict[str, Any]]], cash: float,
            fees: Fees | None = None) -> DayResult:
    """Match one day. `bars[code]` must be time-sorted dicts with open/high/low/close."""
    fees = fees or Fees()
    buys = list(buys)
    positions = list(positions)
    res = DayResult(cash=float(cash))
    # `known` lets the caller tell "sold out today" apart from "never handed to the matcher".
    # Without it a fully-sold position looks identical to one that was never passed in, and a
    # carry-over rule resurrects it — reselling it every day and double-counting the proceeds.
    res.known = {p.code for p in positions} | {b.code for b in buys}

    for p in positions:
        res.holdings[p.code] = {
            "name": p.name, "qty": int(p.qty), "cost": float(p.cost),
            "sellable": int(min(p.sellable, p.qty)), "tp": p.tp, "sl": p.sl,
            "scheduled_exit": p.scheduled_exit, "entry_date": p.entry_date,
            "last_price": float(p.cost), "bought_today": False,
        }

    pending_buys: list[BuyOrder] = list(buys)
    first_bar_seen: set[str] = set()

    # Every code we must watch: held positions plus ordered buys.
    watch = sorted({c for c in res.holdings} | {b.code for b in pending_buys})
    if not watch:
        res.notes.append("nothing to watch: no holdings and no orders")
        return res

    # Walk bars in time order across all codes. Bars are per-code, so build a merged timeline.
    timeline: list[str] = sorted({b["dt"] for c in watch for b in bars.get(c, [])})
    if not timeline:
        res.notes.append("no intraday bars for any watched code — nothing can fill")
        return res

    for dt in timeline:
        # ── buys ──
        still_pending: list[BuyOrder] = []
        for b in pending_buys:
            bar = _bar_at(bars.get(b.code, []), dt)
            if bar is None:
                still_pending.append(b)
                continue
            is_first = b.code not in first_bar_seen
            if is_first:
                # Auction result: the day's open. Fills only if the limit covers it.
                if bar["open"] <= b.limit:
                    _fill_buy(res, b, price=bar["open"], dt=dt, fees=fees,
                              reason="auction fill at open")
                    continue
                still_pending.append(b)
                continue
            # Continuous session: a limit order fills once the bar trades down to it.
            if bar["low"] <= b.limit:
                _fill_buy(res, b, price=min(b.limit, bar["open"]), dt=dt, fees=fees,
                          reason="limit touched intraday")
                continue
            still_pending.append(b)
        pending_buys = still_pending
        first_bar_seen.update(c for c in watch if _bar_at(bars.get(c, []), dt) is not None)

        # ── exits ──
        for code in list(res.holdings):
            h = res.holdings[code]
            bar = _bar_at(bars.get(code, []), dt)
            if bar is None:
                h["last_price"] = h["last_price"]
                continue
            h["last_price"] = bar["close"]
            if h["qty"] <= 0 or h["sellable"] <= 0 or h["bought_today"]:
                continue
            hit = _exit_decision(h, bar)
            if hit is None:
                continue
            price, reason, also_hit = hit
            if also_hit:
                res.same_bar_ambiguity.append(f"{code} {dt}: both TP and SL touched in one bar")
            _fill_sell(res, code, price=price, dt=dt, fees=fees, reason=reason)

    for b in pending_buys:
        if not bars.get(b.code):
            res.notes.append(f"unfilled buy {b.code} {b.name}: no intraday bars — not tradeable today")
        else:
            res.notes.append(f"unfilled buy {b.code} {b.name}: limit {b.limit} never reached")
    return res


def _bar_at(bars: list[dict[str, Any]], dt: str) -> dict[str, Any] | None:
    for b in bars:
        if b["dt"] == dt:
            return b
    return None


def _exit_decision(h: dict[str, Any], bar: dict[str, Any]):
    """Priority: scheduled exit, then take-profit, then stop-loss. Returns (price, reason, ambiguous)."""
    tp, sl = h.get("tp"), h.get("sl")
    if h.get("scheduled_exit") and tp:
        # Engine force-sell: tp and sl both sit at the day's auction price, so whichever side
        # the market opens through, the app's conditional order fires at 09:30 and the fill is
        # the open. That is exactly the "expired / max-hold sells at the open" exit the
        # backtest books, and it is the exit carrying most of its profit.
        return bar["open"], "scheduled exit (force-sell) at the open", False
    tp_hit = bool(tp) and bar["high"] >= tp
    sl_hit = bool(sl) and bar["low"] <= sl
    if tp_hit and sl_hit:
        return max(tp, bar["open"]), "take-profit (TP priority in-bar)", True
    if tp_hit:
        return max(tp, bar["open"]), "take-profit", False
    if sl_hit:
        note = "stop-loss"
        if sl >= bar["open"]:
            # The stop is ABOVE the market at the open: the position was already past it before the
            # day began. That happens when the bracket is built off a cost basis staler than the
            # price move (exactly the case for a holding whose DB row last synced weeks ago), and
            # it exits at the open whatever the rest of the day does.
            note += (" (trigger already through the market at the open — bracket built off a "
                     "stale cost basis, not this day's trading)")
        return min(sl, bar["open"]), note, False
    return None


def _fill_buy(res: DayResult, b: BuyOrder, price: float, dt: str, fees: Fees, reason: str) -> None:
    qty = int(b.qty)
    amount = price * qty
    charge = fees.buy_charges(amount)
    if amount + charge > res.cash:
        res.notes.append(
            f"skipped buy {b.code} {b.name}: needs {amount + charge:,.2f} "
            f"({price:.2f} x {qty} + {charge:,.2f} fees), cash {res.cash:,.2f}")
        return
    res.cash -= amount + charge
    res.fees_paid += charge
    h = res.holdings.get(b.code)
    if h and h["qty"] > 0:
        total = h["qty"] + qty
        h["cost"] = (h["cost"] * h["qty"] + amount) / total
        h["qty"] = total
        h["tp"] = h["tp"] if h["tp"] is not None else b.tp
        h["sl"] = h["sl"] if h["sl"] is not None else b.sl
    else:
        res.holdings[b.code] = {
            "name": b.name, "qty": qty, "cost": price, "sellable": 0,
            "tp": b.tp, "sl": b.sl, "scheduled_exit": False,
            # Stamp the entry date here: without it a position bought today reports as "legacy"
            # and can never age into a force-sell.
            "entry_date": dt[:10].replace("-", ""),
            "last_price": price, "bought_today": True,
        }
    res.fills.append({"dt": dt, "code": b.code, "name": b.name, "side": "BUY", "qty": qty,
                      "price": round(price, 4), "fees": round(charge, 2), "reason": reason,
                      "cash_after": round(res.cash, 2)})


def _fill_sell(res: DayResult, code: str, price: float, dt: str, fees: Fees, reason: str) -> None:
    h = res.holdings[code]
    qty = int(h["sellable"])
    amount = price * qty
    charge = fees.sell_charges(amount)
    res.cash += amount - charge
    res.fees_paid += charge
    res.realized += (price - h["cost"]) * qty - charge
    res.fills.append({"dt": dt, "code": code, "name": h["name"], "side": "SELL", "qty": qty,
                      "price": round(price, 4), "cost": round(h["cost"], 4), "fees": round(charge, 2),
                      "reason": reason, "cash_after": round(res.cash, 2),
                      "realized": round((price - h["cost"]) * qty - charge, 2)})
    h["qty"] -= qty
    h["sellable"] = 0
    if h["qty"] <= 0:
        del res.holdings[code]
