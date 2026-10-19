"""Regression tests for the paper-sim BUY fill / SL-TP re-basing.

Covers the three defects found on 2026-10-19 that produced the October drawdown:

1. BUY filled at ANY open price — a limit plan of 203.94 filled at 219.90
   (+7.8%), so the planned 1.5% stop became an 8.65% stop
   (603268 松发股份, -7,668).
2. SL/TP kept the plan's ABSOLUTE price instead of re-basing on the fill,
   so risk drifted with the fill price.
3. TP was a 3x (+200%) placeholder and could never trigger.

The tests stub the OHLC provider and regime lookup so they are offline and
deterministic.

Run: env -u PYTHONPATH .venv/bin/python -m pytest tests/test_paper_sim_fill_risk.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, "/home/kasm-user")

if not os.path.exists("/home/kasm-user/stock_cron_tasks.py"):
    pytest.skip("paper-sim script is not deployed on this host",
                allow_module_level=True)

import stock_cron_tasks as sim  # noqa: E402


BARS = {}


def _make_order(code="603268.SH", qty=500, bp=203.94, sl=200.88, tp=611.82,
                name="松发股份"):
    return {"code": code, "name": name, "qty": qty, "buy_price": bp,
            "sl": sl, "tp": tp}


def _state(orders, cash=200000.0, holdings=None):
    return {"paper": {"cash": cash, "realized": 0.0, "fees": 0.0,
                      "orders": orders, "holdings": holdings or {}}}


@pytest.fixture(autouse=True)
def _stub(monkeypatch):
    BARS.clear()
    monkeypatch.setattr(sim, "_get_daily_ohlc", lambda codes, day: dict(BARS))
    monkeypatch.setattr(sim, "_regime_config",
                        lambda day: {"stop_loss_pct": 0.015,
                                     "take_profit_pct": 0.02})
    monkeypatch.setattr(sim, "_regime_max_hold", lambda day: 1)
    monkeypatch.setattr(sim, "_holding_trading_days", lambda a, b: 0 if str(a) == str(b) else 2)
    monkeypatch.setattr(sim, "save_state", lambda s: None)
    yield


def _bar(open_, high, low, close, pre_close=None):
    return {"open": open_, "high": high, "low": low, "close": close,
            "pre_close": pre_close if pre_close is not None else open_}


def test_no_fill_when_open_and_low_are_above_the_limit():
    """603268 on 2026-10-09: plan 203.94, opened 219.90, never traded down.

    OLD behaviour: filled 500 @ 219.90 -> stop at 200.88 = -8.65% -> -7,668.
    NEW behaviour: order stays pending, no position, no loss.
    """
    BARS["603268.SH"] = _bar(219.90, 221.00, 205.00, 218.75, pre_close=218.75)
    st = _state({"603268.SH": _make_order()})

    res = sim.simulate_trading_day("20261009", st)

    assert res["buys"] == []
    assert "603268.SH" not in st["paper"]["holdings"]
    assert st["paper"]["orders"], "unfilled limit order must stay pending"
    assert st["paper"]["cash"] == pytest.approx(200000.0)


def test_fill_at_open_rebases_risk_to_plan_geometry():
    """Open at/below the limit -> fill at the open, 1.5% stop, 2% TP."""
    BARS["603268.SH"] = _bar(203.00, 210.00, 200.00, 205.00, pre_close=203.00)
    st = _state({"603268.SH": _make_order()})

    res = sim.simulate_trading_day("20261009", st)

    assert len(res["buys"]) == 1
    name, code, qty, fill, tp, sl = res["buys"][0]
    assert fill == pytest.approx(203.00)
    # SL keeps the plan's -1.50% geometry, anchored on the real fill
    assert sl == pytest.approx(round(203.00 * 0.985, 2))
    assert (sl / fill - 1.0) == pytest.approx(-0.015, abs=1e-4)
    # 3x placeholder TP (611.82) is rejected -> regime +2%
    assert tp == pytest.approx(round(203.00 * 1.02, 2))
    assert (tp / fill - 1.0) == pytest.approx(0.02, abs=1e-4)
    # stored holding matches the reported fill/levels
    h = st["paper"]["holdings"]["603268.SH"]
    assert h["cost"] == pytest.approx(fill)
    assert h["sl"] == pytest.approx(sl)
    assert h["tp"] == pytest.approx(tp)
    assert h["plan_buy_price"] == pytest.approx(203.94)


def test_limit_tagged_intraday_fills_at_the_limit_not_the_open():
    """Open above the limit but the low tags it -> fill at the limit price."""
    BARS["603268.SH"] = _bar(210.00, 212.00, 203.00, 208.00, pre_close=210.00)
    st = _state({"603268.SH": _make_order()})

    res = sim.simulate_trading_day("20261009", st)

    assert len(res["buys"]) == 1
    _, _, _, fill, tp, sl = res["buys"][0]
    assert fill == pytest.approx(203.94)
    assert (sl / fill - 1.0) == pytest.approx(-0.015, abs=1e-4)
    assert (tp / fill - 1.0) == pytest.approx(0.02, abs=1e-4)


def test_realised_stop_matches_planned_risk():
    """End-to-end: enter at 203.94, gap below the stop next day -> ~-1.5%."""
    BARS["603268.SH"] = _bar(203.94, 206.00, 203.00, 205.00, pre_close=203.94)
    st = _state({"603268.SH": _make_order()})
    sim.simulate_trading_day("20261009", st)

    h = st["paper"]["holdings"]["603268.SH"]
    entry, sl = h["cost"], h["sl"]

    # Next trading day: gap through the stop
    BARS["603268.SH"] = _bar(sl - 1.00, sl - 0.50, sl - 2.00, sl - 1.00,
                             pre_close=205.00)
    res = sim.simulate_trading_day("20261012", st)

    assert len(res["sells"]) == 1
    _, _, _, exit_px, reason, pnl = res["sells"][0]
    assert "止损" in reason
    realised_pct = (exit_px / entry - 1.0) * 100
    # gap-down fills at the open, so allow a little past the level
    assert -2.0 <= realised_pct <= -1.3
    assert pnl < 0


def test_missing_bar_leaves_order_pending():
    st = _state({"603268.SH": _make_order()})
    res = sim.simulate_trading_day("20261009", st)
    assert res["buys"] == []
    assert "603268.SH" in st["paper"]["orders"]
