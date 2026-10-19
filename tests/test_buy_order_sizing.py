"""Pre-market BUY sizing must reserve what the order will actually cost.

The planner reserved cash at `prev_close × 1.02` but stored and settled the
order at `limit_up` (prev_close × 1.10 for main board, × 1.20 for ChiNext/STAR).
So it approved orders the settlement then rejected for cash — and a rejected
order did not error, it just stayed pending forever.

2026-09-22: 603268 reserved ¥106,080 (500 × 212.16) out of ¥111,013 cash, but
the auction opened at ¥228.00, so the charge was ¥114,000 > cash. The order
never filled and the day produced no trade despite 5 candidates.

Run: env -u PYTHONPATH .venv/bin/python -m pytest tests/test_buy_order_sizing.py -q
"""
import os
import sys

import pytest

sys.path.insert(0, "/home/kasm-user")

if not os.path.exists("/home/kasm-user/stock_cron_tasks.py"):
    pytest.skip("paper-sim script is not deployed on this host",
                allow_module_level=True)

import stock_cron_tasks as sim  # noqa: E402


def test_charged_cost_never_exceeds_available_cash():
    """The invariant: price × qty <= remaining, at the SUBMITTED price."""
    qty, cost = sim._plan_buy_qty(remaining=111013.0, submitted_price=228.00,
                                  requested_qty=500, slot_cap=25000.0)
    assert cost <= 111013.0
    assert qty * 228.00 == cost


def test_0922_case_is_now_affordable():
    """500 × 228.00 = 114,000 did not fit in 111,013 -> downsize to 400."""
    qty, cost = sim._plan_buy_qty(111013.0, 228.00, 500, 25000.0)
    assert qty == 400
    assert cost == pytest.approx(91200.0)


def test_order_is_skipped_when_even_one_lot_does_not_fit():
    qty, cost = sim._plan_buy_qty(remaining=10000.0, submitted_price=228.00,
                                  requested_qty=500, slot_cap=25000.0)
    assert qty == 0
    assert cost == 0.0


def test_requested_qty_is_respected_when_it_fits():
    qty, cost = sim._plan_buy_qty(200000.0, 203.94, 500, 25000.0)
    assert qty == 500
    assert cost == pytest.approx(500 * 203.94)


def test_unspecified_qty_uses_the_slot_cap_rounded_to_lots():
    qty, cost = sim._plan_buy_qty(200000.0, 20.00, 0, slot_cap=25000.0)
    assert qty == 1200          # 25000 / 20 = 1250 -> 1200 (whole lots)
    assert cost == pytest.approx(24000.0)


def test_quantities_are_whole_lots_of_100():
    qty, _ = sim._plan_buy_qty(100000.0, 33.33, 777, 25000.0)
    assert qty % 100 == 0


def test_reservation_uses_limit_up_not_the_2pct_proxy():
    """Regression guard on the planner: bp_cash must be limit_up.

    If someone restores `prev_close * 1.02`, the 0922 rejection returns.
    """
    src = open("/home/kasm-user/stock_cron_tasks.py", encoding="utf-8").read()
    assert "bp_cash = limit_up" in src
    assert "bp_cash = round(prev_close * 1.02, 2)" not in src