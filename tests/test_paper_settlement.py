"""Unit tests for the paper-sim daily settlement in stock_cron_tasks.py.

Covers the two fixes to the as-backtest settlement:
  1. Gap-aware TP/SL fills — a bar that gaps through a level fills at the OPEN
     (better than TP, worse than SL) with 跳空开盘 annotated in the reason.
  2. Retail A-share transaction costs — commission on both sides (min ¥5),
     stamp duty on sells only, tracked in paper['fees'].

The pure helper _settle_holding_price() is exercised directly; the fee
accounting is exercised through simulate_trading_day() with the data/calendar
dependencies monkeypatched out.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# stock_cron_tasks.py lives outside the repo (in the user's home).
CRON_DIR = Path("/home/kasm-user")
if str(CRON_DIR) not in sys.path:
    sys.path.insert(0, str(CRON_DIR))

import stock_cron_tasks as sct  # noqa: E402


def _bar(open_p, high, low, close, pre_close=10.0):
    return {"open": open_p, "high": high, "low": low, "close": close,
            "pre_close": pre_close}


# ── _settle_holding_price: gap-aware TP/SL ──────────────────────────────────

class TestSettleHoldingPrice:
    def test_normal_tp_hit_fills_at_tp(self):
        bar = _bar(10.0, 11.5, 9.9, 11.2)
        assert sct._settle_holding_price(bar, tp=11.0, sl=9.0) == (11.0, "触发止盈")

    def test_tp_gapped_up_fills_at_open_better(self):
        bar = _bar(11.8, 12.0, 11.5, 11.9)
        price, reason = sct._settle_holding_price(bar, tp=11.0, sl=9.0)
        assert price == 11.8                    # open, better than tp=11.0
        assert price > 11.0
        assert reason == "触发止盈(跳空开盘)"

    def test_normal_sl_hit_fills_at_sl(self):
        bar = _bar(10.2, 10.4, 8.9, 9.1)
        assert sct._settle_holding_price(bar, tp=12.0, sl=9.0) == (9.0, "触发止损")

    def test_sl_gapped_down_fills_at_open_worse(self):
        bar = _bar(8.5, 8.8, 8.4, 8.6)
        price, reason = sct._settle_holding_price(bar, tp=12.0, sl=9.0)
        assert price == 8.5                    # open, worse than sl=9.0
        assert price < 9.0
        assert reason == "触发止损(跳空开盘)"

    def test_both_touched_tp_wins(self):
        # open below tp, so not a gap; high clears TP, low clears SL -> TP wins.
        bar = _bar(10.0, 12.0, 8.5, 11.0)
        assert sct._settle_holding_price(bar, tp=11.0, sl=9.0) == (11.0, "触发止盈")

    def test_neither_triggered_returns_none(self):
        bar = _bar(10.0, 10.5, 9.6, 10.2)
        assert sct._settle_holding_price(bar, tp=11.0, sl=9.0) is None

    def test_missing_levels_returns_none(self):
        bar = _bar(10.0, 10.5, 9.6, 10.2)
        assert sct._settle_holding_price(bar, tp=None, sl=None) is None


# ── Fees: constants, commission, stamp duty ─────────────────────────────────

def _buy_fee(amount):
    return max(amount * sct.COMMISSION_PCT, sct.COMMISSION_MIN)


def _sell_fee(amount):
    return max(amount * sct.COMMISSION_PCT, sct.COMMISSION_MIN) + amount * sct.STAMP_DUTY_PCT


class TestFeeConstants:
    def test_commission_min_applies_on_small_trade(self):
        # ¥10,000 trade: pct commission ¥2.5 < ¥5 minimum.
        assert _buy_fee(10_000) == sct.COMMISSION_MIN == 5.0

    def test_commission_pct_applies_on_large_trade(self):
        amount = 1_000_000  # ¥1,000,000 → 0.025% = ¥250 > ¥5
        assert _buy_fee(amount) == amount * sct.COMMISSION_PCT

    def test_stamp_duty_sells_only_adds_0_05pct(self):
        amount = 1_000_000
        assert amount * sct.STAMP_DUTY_PCT == 500.0
        assert _sell_fee(amount) == amount * sct.COMMISSION_PCT + amount * sct.STAMP_DUTY_PCT

    def test_sell_net_cash_is_gross_minus_commission_minus_stamp(self):
        gross = 10_200.0                       # 1000 sh @ ¥10.20
        commission = _buy_fee(gross)           # ¥5 min
        stamp = gross * sct.STAMP_DUTY_PCT
        net = gross - commission - stamp
        assert net == gross - commission - stamp
        assert net < gross


# ── simulate_trading_day: end-to-end fee accounting ─────────────────────────

def _base_state():
    return {
        "paper": {
            "baseline": "20260811",
            "cash": 200_000.0,
            "realized": 0.0,
            "fees": 0.0,
            "frozen": 0.0,
            "orders": {},
            "holdings": {},
        },
        "per_day": {},
    }


def test_simulate_charges_buy_fee(monkeypatch):
    state = _base_state()
    state["paper"]["orders"]["000001.SZ"] = {
        "name": "PINGAN", "code": "000001", "qty": 1000, "buy_price": 10.0,
        "tp": 20.0, "sl": 5.0,
    }
    monkeypatch.setattr(sct, "_get_daily_ohlc",
                        lambda codes, day: {"000001": _bar(10.0, 10.5, 9.8, 10.2)})
    monkeypatch.setattr(sct, "_regime_max_hold", lambda day: 5)
    monkeypatch.setattr(sct, "_holding_trading_days", lambda e, d: 3)
    monkeypatch.setattr(sct, "save_state", lambda s: None)

    sct.simulate_trading_day("20251023", state)
    paper = state["paper"]
    expected_fee = _buy_fee(1000 * 10.0)       # ¥5 minimum
    assert expected_fee == 5.0
    assert paper["fees"] == expected_fee
    assert paper["cash"] == 200_000.0 - 10_000.0 - expected_fee
    assert paper["holdings"]["000001"]["cost"] == 10.0


def test_simulate_charges_sell_fee_and_gap_fill(monkeypatch):
    state = _base_state()
    state["paper"]["holdings"]["600000"] = {
        "name": "PUFA", "qty": 1000, "cost": 9.0, "buy_price": 9.0,
        "tp": 10.0, "sl": 8.0, "entry_date": "20251020",
    }
    # open 10.20 gaps above tp=10.0 -> fill at the OPEN.
    monkeypatch.setattr(sct, "_get_daily_ohlc",
                        lambda codes, day: {"600000": _bar(10.2, 10.5, 10.0, 10.4)})
    monkeypatch.setattr(sct, "_regime_max_hold", lambda day: 5)
    monkeypatch.setattr(sct, "_holding_trading_days", lambda e, d: 3)
    monkeypatch.setattr(sct, "save_state", lambda s: None)

    result = sct.simulate_trading_day("20251023", state)
    paper = state["paper"]
    assert "600000" not in paper["holdings"]

    name, code, qty, fill, reason, pnl = result["sells"][0]
    assert fill == 10.2
    assert "跳空开盘" in reason and "触发止盈" in reason

    gross = 10.2 * 1000
    fee = _sell_fee(gross)                     # commission(¥5 min) + stamp duty
    assert paper["fees"] == fee
    assert paper["cash"] == 200_000.0 + gross - fee
    assert pnl == (10.2 - 9.0) * 1000 - fee


def test_simulate_reads_existing_state_without_fees_key(monkeypatch):
    # An existing state file predates the 'fees' field — must not break.
    state = _base_state()
    del state["paper"]["fees"]
    monkeypatch.setattr(sct, "_get_daily_ohlc", lambda codes, day: {})
    monkeypatch.setattr(sct, "_regime_max_hold", lambda day: 5)
    monkeypatch.setattr(sct, "save_state", lambda s: None)

    sct.simulate_trading_day("20251023", state)
    assert state["paper"]["fees"] == 0.0
