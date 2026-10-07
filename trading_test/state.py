"""Portfolio state carried across the simulated month.

The day loop keeps ONE account alive from `start` to `end`: cash, holdings with their entry dates,
and the T+1 shelf-life of each position. Rebuilding this from the matcher's own result each day
keeps a single source of truth — the fills — rather than a parallel bookkeeping path that can drift.

Two rules live here because they are cross-day by nature:

  * **T+1.** Shares bought on day N are unsellable on N and sellable from the next trading day.
    The matcher enforces it inside a day; the roll below carries it between days.
  * **Hold expiry.** The engine force-sells at the OPEN once a position has been held longer than
    the regime's `max_hold_days`, counted EXCLUSIVELY (a buy on N has held-after = 0 on N). With
    `max_hold_days` pinned to 1 that means: bought on N, sold at the open of N+2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def trading_days_after(entry_date: str, today: str, calendar=None) -> int:
    """Trading sessions strictly after `entry_date`, up to and including `today`.

    0 means "bought today". Falls back to counting weekdays when the project calendar is
    unavailable, and says so through the caller's report rather than silently guessing.
    """
    if not entry_date or entry_date >= today:
        return 0
    if calendar is not None:
        try:
            days = calendar.get_trading_days_between(entry_date, today)
            if days:
                return max(len(days) - 1, 0)      # exclusive of the entry day
        except Exception:  # noqa: BLE001
            pass
    from datetime import date, timedelta
    a = date(int(entry_date[:4]), int(entry_date[4:6]), int(entry_date[6:]))
    b = date(int(today[:4]), int(today[4:6]), int(today[6:]))
    n = 0
    d = a + timedelta(days=1)
    while d <= b:
        if d.weekday() < 5:
            n += 1
        d += timedelta(days=1)
    return n


@dataclass
class Holding:
    code: str
    name: str
    qty: int
    cost: float
    entry_date: str = ""
    sellable: int = 0
    tp: float | None = None
    sl: float | None = None
    last_price: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {"code": self.code, "name": self.name, "qty": self.qty, "cost": round(self.cost, 4),
                "entry_date": self.entry_date, "sellable": self.sellable,
                "tp": self.tp, "sl": self.sl, "last_price": round(self.last_price, 4)}


@dataclass
class SimState:
    cash: float
    holdings: dict[str, Holding] = field(default_factory=dict)
    initial_cash: float = 0.0
    assets_in: float = 0.0
    realized_total: float = 0.0
    fees_total: float = 0.0
    ledger: list[dict[str, Any]] = field(default_factory=list)
    daily: list[dict[str, Any]] = field(default_factory=list)
    unpriced_days: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    # ── construction ────────────────────────────────────────
    @classmethod
    def from_account(cls, cash: float, holdings: list[dict], initial_cash: float | None = None) -> "SimState":
        st = cls(cash=float(cash), initial_cash=float(initial_cash if initial_cash is not None else cash))
        st.holdings = {
            h["code"]: Holding(code=h["code"], name=h.get("name", h["code"]),
                               qty=int(h.get("qty", 0)), cost=float(h.get("cost") or 0.0),
                               sellable=int(h.get("sellable") or h.get("qty", 0)))
            for h in holdings if h.get("code") and int(h.get("qty", 0)) > 0
        }
        st.assets_in = st.cash + sum(h.qty * h.cost for h in st.holdings.values())
        return st

    # ── per-day ─────────────────────────────────────────────
    def roll_from_result(self, day: str, res, prices: dict[str, float]) -> dict[str, Any]:
        """Adopt the matcher's end-of-day book, stamp entry dates, roll T+1, record the day."""
        self.cash = res.cash
        self.realized_total += res.realized
        self.fees_total += res.fees_paid

        for f in res.fills:
            self.ledger.append({"day": day, **f})

        new: dict[str, Holding] = {}
        for code, h in res.holdings.items():
            prev = self.holdings.get(code)
            bought_today = bool(h.get("bought_today"))
            entry = day if bought_today else (prev.entry_date if prev else h.get("entry_date", ""))
            new[code] = Holding(code=code, name=h.get("name", code), qty=int(h["qty"]),
                                cost=float(h["cost"]), entry_date=entry,
                                sellable=int(h["qty"]),   # T+1 satisfied from tomorrow
                                tp=h.get("tp"), sl=h.get("sl"), last_price=h.get("last_price") or h["cost"])
        # A position the account holds but the matcher was never handed must not disappear from the
        # book silently. A position that WAS handed over and is now missing was sold out — that is
        # normal and must NOT be resurrected.
        known = getattr(res, "known", set())
        for code, h in self.holdings.items():
            if code in new or code in known:
                continue
            new[code] = h
            self.warnings.append(
                f"{day}: {code} {h.name or ''} was held by the account but not passed to the "
                f"matcher — carried unchanged (check the day's position list)")
        self.holdings = new

        unpriced = sorted(c for c in new if c not in prices)
        for code, h in self.holdings.items():
            px = prices.get(code)
            if px is not None:
                h.last_price = float(px)
        if unpriced:
            self.unpriced_days[day] = unpriced

        equity = self.equity(prices)
        row = {"day": day, "cash": round(self.cash, 2), "equity": round(equity, 2),
               "realized_day": round(res.realized, 2), "fees_day": round(res.fees_paid, 2),
               "n_buys": sum(1 for f in res.fills if f["side"] == "BUY"),
               "n_sells": sum(1 for f in res.fills if f["side"] == "SELL"),
               "holdings": len(self.holdings),
               "unpriced": len(unpriced)}
        self.daily.append(row)
        return row

    def equity(self, prices: dict[str, float] | None = None) -> float:
        prices = prices or {}
        total = self.cash
        for code, h in self.holdings.items():
            px = prices.get(code)
            total += h.qty * (px if px is not None else (h.last_price or h.cost))
        return total

    def peak_equity(self) -> float:
        return max((r["equity"] for r in self.daily), default=self.equity())

    def max_drawdown_pct(self) -> float:
        peak, worst = 0.0, 0.0
        for r in self.daily:
            peak = max(peak, r["equity"])
            if peak > 0:
                worst = min(worst, (r["equity"] - peak) / peak)
        return worst * 100.0

    def snapshot(self) -> dict[str, Any]:
        return {"cash": round(self.cash, 2), "equity": round(self.equity(), 2),
                "initial_cash": self.initial_cash, "assets_in": round(self.assets_in, 2),
                "realized_total": round(self.realized_total, 2),
                "fees_total": round(self.fees_total, 2),
                "holdings": [h.as_dict() for h in self.holdings.values()],
                "warnings": list(self.warnings)}
