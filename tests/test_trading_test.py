"""Tests for the trading-test simulation (trading_test/, the multi-day real-trading replay).

All bars are synthetic: these pin the FILL SEMANTICS and the cross-day state rules, not the data.
The matcher and the state module are deliberately pure so every rule is checkable without touching
shared/data_cache.
"""
from __future__ import annotations

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
SIM_DIR = os.path.join(os.path.dirname(HERE), "trading_test")
if SIM_DIR not in sys.path:
    sys.path.insert(0, SIM_DIR)

import bars as bars_mod          # noqa: E402
import plan as plan_mod          # noqa: E402
import report as report_mod      # noqa: E402
from matcher import BuyOrder, Fees, Position, run_day  # noqa: E402
from state import SimState, trading_days_after  # noqa: E402

D = "20260901"
T1, T2, T3 = f"{D[:4]}-{D[4:6]}-{D[6:]} 09:30:00", f"{D[:4]}-{D[4:6]}-{D[6:]} 10:00:00", f"{D[:4]}-{D[4:6]}-{D[6:]} 10:30:00"


def bar(dt, o, h, lo, c):
    return {"dt": dt, "open": o, "high": h, "low": lo, "close": c, "volume": 1000.0}


# ─── buys: auction ───────────────────────────────────────────
def test_auction_fills_at_the_open_when_the_limit_covers_it():
    bars = {"600000.SH": [bar(T1, 10.50, 10.80, 10.40, 10.60)]}
    buys = [BuyOrder("600000.SH", "浦发银行", 100, limit=11.00)]
    res = run_day(D, buys, [], bars, cash=10_000.0)
    assert len(res.fills) == 1
    f = res.fills[0]
    assert f["side"] == "BUY" and f["price"] == 10.50 and f["qty"] == 100
    assert res.cash == pytest.approx(10_000 - 1050 - 5.0)   # 5.0 = minimum commission
    assert res.holdings["600000.SH"]["bought_today"] is True


def test_no_auction_fill_when_the_open_is_above_the_limit():
    bars = {"600000.SH": [bar(T1, 11.50, 11.90, 11.40, 11.60), bar(T2, 11.60, 11.70, 11.30, 11.50)]}
    buys = [BuyOrder("600000.SH", "浦发银行", 100, limit=11.00)]
    res = run_day(D, buys, [], bars, cash=10_000.0)
    assert res.fills == []
    assert any("never reached" in n for n in res.notes)
    assert res.cash == 10_000.0


def test_limit_order_fills_intraday_at_min_of_limit_and_bar_open():
    bars = {"600000.SH": [bar(T1, 11.50, 11.90, 11.40, 11.60),
                          bar(T2, 9.90, 10.20, 9.60, 10.00)]}
    buys = [BuyOrder("600000.SH", "浦发银行", 100, limit=10.00)]
    res = run_day(D, buys, [], bars, cash=10_000.0)
    assert len(res.fills) == 1
    assert res.fills[0]["price"] == 9.90        # gaps below the limit -> better fill
    assert res.fills[0]["reason"] == "limit touched intraday"


# ─── exits ───────────────────────────────────────────────────
def _pos(**kw):
    base = dict(code="600000.SH", name="浦发银行", qty=100, cost=10.0, sellable=100,
                tp=12.0, sl=9.0)
    base.update(kw)
    return Position(**base)


def test_take_profit_gap_up_fills_better_than_the_trigger():
    bars = {"600000.SH": [bar(T1, 12.50, 13.00, 12.20, 12.80)]}
    res = run_day(D, [], [_pos()], bars, cash=0.0)
    assert res.fills[0]["side"] == "SELL"
    assert res.fills[0]["price"] == 12.50      # max(tp, open)
    assert res.fills[0]["reason"] == "take-profit"
    assert "600000.SH" not in res.holdings


def test_stop_loss_gap_down_fills_worse_than_the_trigger():
    bars = {"600000.SH": [bar(T1, 8.00, 8.10, 7.50, 7.80)]}
    res = run_day(D, [], [_pos()], bars, cash=0.0)
    assert res.fills[0]["price"] == 8.00       # min(sl, open) — the gap is real money
    # sl 9.00 sits ABOVE this open, so the bracket was already through the market: flagged
    assert res.fills[0]["reason"].startswith("stop-loss")
    assert "stale cost basis" in res.fills[0]["reason"]


def test_stop_loss_not_flagged_when_the_bracket_is_still_intact():
    bars = {"600000.SH": [bar(T1, 10.50, 10.60, 8.90, 9.00)]}   # dips to the stop from above
    res = run_day(D, [], [_pos()], bars, cash=0.0)
    assert res.fills[0]["price"] == 9.00
    assert res.fills[0]["reason"] == "stop-loss"


def test_scheduled_exit_fills_at_the_open():
    bars = {"600000.SH": [bar(T1, 9.80, 10.40, 9.70, 10.20)]}
    res = run_day(D, [], [_pos(scheduled_exit=True, tp=10.0, sl=10.0)], bars, cash=0.0)
    assert res.fills[0]["price"] == 9.80
    assert "scheduled exit" in res.fills[0]["reason"]


def test_tp_wins_when_one_bar_touches_both_and_is_flagged():
    bars = {"600000.SH": [bar(T1, 10.50, 13.00, 8.50, 11.00)]}
    res = run_day(D, [], [_pos()], bars, cash=0.0)
    assert res.fills[0]["price"] == 12.00
    assert res.same_bar_ambiguity and "both TP and SL" in res.same_bar_ambiguity[0]


def test_stop_loss_is_not_live_when_tp_is_still_out_of_reach():
    bars = {"600000.SH": [bar(T1, 10.50, 11.50, 9.50, 11.00)]}   # low dips to 9.50, sl is 9.00
    res = run_day(D, [], [_pos()], bars, cash=0.0)
    assert res.fills == []
    assert res.holdings["600000.SH"]["last_price"] == 11.00      # marked to market


# ─── T+1 and cash ────────────────────────────────────────────
def test_t_plus_1_blocks_a_same_day_sell():
    bars = {"600000.SH": [bar(T1, 9.00, 13.00, 8.90, 12.90)]}    # buy fills at 9, TP 11 reachable
    buys = [BuyOrder("600000.SH", "浦发银行", 100, limit=9.50, tp=11.0, sl=8.5)]
    res = run_day(D, buys, [], bars, cash=10_000.0)
    assert [f["side"] for f in res.fills] == ["BUY"]
    assert res.holdings["600000.SH"]["qty"] == 100


def test_unaffordable_buy_is_skipped_with_a_reason():
    bars = {"600000.SH": [bar(T1, 10.00, 10.20, 9.90, 10.10)]}
    buys = [BuyOrder("600000.SH", "浦发银行", 200, limit=11.00)]   # 2,000 + fees > 100
    res = run_day(D, buys, [], bars, cash=100.0)
    assert res.fills == []
    assert any("skipped buy" in n for n in res.notes)
    assert res.cash == 100.0


# ─── costs ───────────────────────────────────────────────────
def test_sell_charges_commission_plus_stamp_duty_and_realized_nets_them():
    bars = {"600000.SH": [bar(T1, 20.00, 20.50, 19.80, 20.20)]}   # TP 20 -> fill at 20
    res = run_day(D, [], [_pos(tp=20.0, sl=9.0)], bars, cash=0.0)
    f = res.fills[0]
    assert f["price"] == 20.00
    assert f["fees"] == pytest.approx(5.0 + 2000 * 0.0005)        # min commission + 0.05% stamp
    assert res.realized == pytest.approx(1000 - f["fees"])
    assert res.cash == pytest.approx(2000 - f["fees"])


def test_buy_commission_is_per_order_with_a_minimum():
    fees = Fees()
    assert fees.buy_charges(1_000.0) == 5.0                       # 0.25 < 5 -> min applies
    assert fees.buy_charges(100_000.0) == pytest.approx(25.0)     # 万2.5
    assert fees.sell_charges(100_000.0) == pytest.approx(25.0 + 50.0)


# ─── plan helpers ────────────────────────────────────────────
def test_band_matches_the_live_runner():
    runner = pytest.importorskip("trading.runner")
    for code in ("600000.SH", "688347.SH", "300750.SZ", "830799.BJ"):
        assert plan_mod.daily_band_pct(code) == runner._daily_band_pct(code)


def test_auction_limit_modes():
    assert plan_mod.auction_limit("600000.SH", 10.0, "limit_up") == 11.0
    assert plan_mod.auction_limit("688347.SH", 10.0, "limit_up") == 12.0
    assert plan_mod.auction_limit("600000.SH", 10.0, "prev_close_buffer", 0.005) == 10.05
    assert plan_mod.auction_limit("600000.SH", 10.0, "order_price", order_price=9.5) == 9.5


def test_positions_from_account_applies_brackets_and_force_exit():
    holdings = [{"code": "600000.SH", "name": "浦发银行", "qty": 200, "sellable": 200, "cost": 10.0}]
    cfg = {"tp_mult": 3.0, "sl_pct": 0.025, "force_exit_codes": ["600000.SH"]}
    p = plan_mod.positions_from_account(holdings, cfg)[0]
    assert p.tp == 30.0 and p.sl == 9.75 and p.scheduled_exit is True


def test_positions_skip_a_position_with_no_cost_basis():
    holdings = [{"code": "600000.SH", "name": "x", "qty": 100, "sellable": 100, "cost": 0.0}]
    assert plan_mod.positions_from_account(holdings, {}) == []


def test_derive_plan_skips_held_and_min_lot_infeasible(monkeypatch):
    picks = [{"symbol": "600000.SH", "name": "held"}, {"symbol": "000001.SZ", "name": "too dear"},
             {"symbol": "300750.SZ", "name": "ok"}]
    monkeypatch.setattr(plan_mod, "load_picks", lambda date, prefer="live": (picks, "fake.json"))
    prev = {"600000.SH": 10.0, "000001.SZ": 1000.0, "300750.SZ": 100.0}
    cfg = {"max_positions": 3, "buy_limit_mode": "limit_up", "tp_mult": 3.0, "sl_pct": 0.025}
    buys, notes, _ = plan_mod.derive_plan(D, 300_000.0, cfg, prev, already_held={"600000.SH"})
    assert [b.code for b in buys] == ["300750.SZ"]
    assert any("already held" in n for n in notes)
    assert any("min lot" in n for n in notes)
    assert buys[0].qty == 800                    # per_slot 100,000 / limit 120 (300xxx = 20% band) -> 8 lots
    assert buys[0].limit == 120.0


# ─── bar loading (synthetic pickle in a temp cache) ──────────
def test_load_intraday_parses_and_filters_by_date(tmp_path, monkeypatch):
    pd = pytest.importorskip("pandas")
    monkeypatch.setattr(bars_mod, "CACHE_DIR", str(tmp_path))
    idx = pd.to_datetime([f"{D[:4]}-{D[4:6]}-{D[6:]} 09:30:00", f"{D[:4]}-{D[4:6]}-{D[6:]} 10:00:00",
                          "2026-08-31 15:00:00"])
    pd.DataFrame({"open": [1.0, 2.0, 3.0], "high": [1.1, 2.1, 3.1], "low": [0.9, 1.9, 2.9],
                  "close": [1.05, 2.05, 3.05], "volume": [10, 20, 30]},
                 index=idx).to_pickle(tmp_path / "600000.SH.pkl")
    bars, info = bars_mod.load_intraday("600000.SH", D)
    assert [b["open"] for b in bars] == [1.0, 2.0]     # the 08-31 row is filtered out
    assert info["error"] is None and info["file"] == "600000.SH.pkl"
    prev, pinfo = bars_mod.load_prev_close("600000.SH", "20260831")
    assert prev == 3.05 and "2026-08-31" in pinfo["source"]


def test_load_intraday_reports_a_missing_code_instead_of_silence():
    bars, info = bars_mod.load_intraday("999999.SH", D)
    assert bars == [] and "no cache file" in info["error"]


# ─── cross-day state ─────────────────────────────────────────
def test_trading_days_after_counts_exclusively():
    assert trading_days_after("20260901", "20260901") == 0        # bought today
    assert trading_days_after("20260901", "20260902") == 1        # next session
    assert trading_days_after("20260903", "20260901") == 0        # entry after today -> 0
    assert trading_days_after("", "20260902") == 0                # legacy holding, no entry date


def test_sim_state_stamps_entry_and_rolls_t_plus_1():
    bars = {"600000.SH": [bar(T1, 10.00, 10.20, 9.90, 10.10)]}
    buys = [BuyOrder("600000.SH", "浦发银行", 100, limit=11.0)]
    res = run_day(D, buys, [], bars, cash=10_000.0)
    st = SimState.from_account(10_000.0, [{"code": "000001.SZ", "name": "legacy", "qty": 500,
                                           "sellable": 500, "cost": 12.0}])
    st.roll_from_result(D, res, {"600000.SH": 10.10, "000001.SZ": 12.5})
    assert st.holdings["600000.SH"].entry_date == D               # stamped with the buy day
    assert st.holdings["600000.SH"].sellable == 100               # T+1 satisfied from tomorrow
    assert st.holdings["000001.SZ"].entry_date == ""              # legacy: no date known
    assert st.daily[-1]["n_buys"] == 1 and st.daily[-1]["holdings"] == 2


def test_sim_state_marks_unpriced_holdings_and_excludes_them():
    st = SimState.from_account(1_000.0, [{"code": "000001.SZ", "name": "legacy", "qty": 100,
                                          "sellable": 100, "cost": 12.0}])
    bars = {"600000.SH": [bar(T1, 10.00, 10.20, 9.90, 10.00)]}
    st.roll_from_result(D, run_day(D, [], [], bars, cash=1_000.0), {"600000.SH": 10.0})
    assert st.unpriced_days[D] == ["000001.SZ"]
    assert st.daily[-1]["unpriced"] == 1
    assert st.equity() == pytest.approx(1_000.0 + 100 * 12.0)     # carried at cost, not invented


def test_sim_state_max_drawdown_from_the_daily_curve():
    st = SimState.from_account(1_000.0, [])
    st.daily = [{"equity": 1_000.0}, {"equity": 1_200.0}, {"equity": 900.0}, {"equity": 1_100.0}]
    assert st.max_drawdown_pct() == pytest.approx(-25.0)          # 1200 -> 900


# ─── reports ─────────────────────────────────────────────────
def test_exit_kind_buckets_the_reasons():
    assert report_mod._exit_kind("stop-loss") == "stop-loss"
    assert report_mod._exit_kind("take-profit") == "take-profit"
    assert "scheduled" in report_mod._exit_kind("scheduled exit (force-sell) at the open")


def test_backtest_slice_reads_the_portfolio_column_not_the_pnl(tmp_path):
    rows = ["| Date | Txns | Sells | Realized P&L | Unrealized P&L | Total P&L | Portfolio Value | Positions |",
            "|---|---|---|---|---|---|---|---|",
            "| 20260828 | 10 | 0 | ¥      0.00 | ¥ 0.00 | ¥     0.00 | ¥   600,000.00 (  0.00%) | 0 |",
            "| 20260901 | 12 | 8 | ¥  4,999.51 | ¥ 0.00 | ¥ 4,999.51 | ¥   604,999.51 (  0.83%) | 0 |",
            "| 20260902 | 11 | 4 | ¥  7,514.99 | ¥ 0.00 | ¥ 7,514.99 | ¥   590,000.00 ( -2.48%) | 0 |"]
    d = tmp_path / "backtest" / "results" / "20260101_20260930_ts_7AZ_96MA_flow_review"
    d.mkdir(parents=True)
    (d / "report_period_20260101_20260930.md").write_text("\n".join(rows), encoding="utf-8")
    bt = report_mod.backtest_slice(str(tmp_path), "20260901", "20260930")
    assert bt is not None
    assert bt["base"] == 600_000.00                               # the last day BEFORE the window
    assert bt["final"] == 590_000.00
    assert bt["return"] == pytest.approx((590_000 / 600_000 - 1) * 100)
    assert bt["maxdd"] == pytest.approx((590_000 / 604_999.51 - 1) * 100)
    assert bt["txns"] == 23


# ─── fill realizability checker ───────────────────────────────
def _fake_feed(monkeypatch, bars_by_code, prev=None):
    """Point check_fills at synthetic bars: no cache reads, no I/O."""
    import check_fills as cf
    monkeypatch.setattr(cf, "series", lambda code: bars_by_code.get(code, []))
    monkeypatch.setattr(cf, "day_bars", lambda code, d: bars_by_code.get(code, []))
    monkeypatch.setattr(cf, "prev_close", lambda code, d: prev)
    return cf


def _fill(code="600000.SH", price=10.55, qty=100, reason="", side="BUY", dt=T2):
    return {"day": D, "dt": dt, "code": code, "name": "浦发银行", "side": side,
            "qty": qty, "price": price, "reason": reason}


RANGE_BARS = {"600000.SH": [bar(T2, 10.50, 10.80, 10.40, 10.60)]}


def test_realizability_accepts_a_fill_the_day_really_traded(monkeypatch):
    cf = _fake_feed(monkeypatch, RANGE_BARS)
    assert cf.check_fill(_fill(), {(D, "600000.SH"): 11.00}) == []


def test_realizability_rejects_a_price_the_day_never_traded(monkeypatch):
    cf = _fake_feed(monkeypatch, RANGE_BARS)
    bad = cf.check_fill(_fill(price=12.00), {})
    assert any(b.startswith("RANGE") for b in bad)


def test_realizability_rejects_a_fill_outside_the_bar_it_claims(monkeypatch):
    cf = _fake_feed(monkeypatch, {"600000.SH": [bar(T2, 10.50, 10.80, 10.40, 10.60),
                                               bar(T3, 10.60, 10.70, 10.55, 10.65)]})
    bad = cf.check_fill(_fill(price=10.45, dt=T3), {})   # valid for the day, not for that bar
    assert any(b.startswith("BAR") for b in bad)


def test_realizability_pins_an_auction_fill_to_the_open(monkeypatch):
    cf = _fake_feed(monkeypatch, RANGE_BARS)
    ok = cf.check_fill(_fill(price=10.50, reason="auction fill at open"), {})
    assert ok == []
    bad = cf.check_fill(_fill(price=10.60, reason="auction fill at open"), {})
    assert any(b.startswith("AUCTION") for b in bad)


def test_realizability_rejects_a_buy_above_its_own_limit(monkeypatch):
    cf = _fake_feed(monkeypatch, RANGE_BARS)
    bad = cf.check_fill(_fill(price=10.55), {(D, "600000.SH"): 10.00})
    assert any(b.startswith("ORDER") for b in bad)


def test_realizability_rejects_a_size_bigger_than_the_real_volume(monkeypatch):
    cf = _fake_feed(monkeypatch, RANGE_BARS)      # the synthetic bar trades 1,000 shares
    bad = cf.check_fill(_fill(qty=5_000), {})
    assert any(b.startswith("LIQUIDITY") for b in bad)


def test_realizability_flags_a_price_outside_the_limit_band(monkeypatch):
    bars = {"600000.SH": [bar(T2, 10.50, 12.00, 10.40, 11.80)]}
    cf = _fake_feed(monkeypatch, bars, prev=10.00)          # band 9.00 .. 11.00
    bad = cf.check_fill(_fill(price=11.50), {})
    assert any(b.startswith("LIMIT") for b in bad)


def test_realizability_refuses_a_fill_printed_exactly_on_the_band(monkeypatch):
    """Inside the band is not enough. At the limit-up there are no sellers and at the limit-down no
    buyers, so a print exactly ON the band could not have happened — the same rule the engine
    enforces in execute_buy_order/execute_sell_order."""
    bars = {"600000.SH": [bar(T2, 10.50, 12.00, 9.00, 11.80)]}
    cf = _fake_feed(monkeypatch, bars, prev=10.00)          # band 9.00 .. 11.00
    assert any(b.startswith("BAND") for b in cf.check_fill(_fill(price=11.00), {}))
    assert any(b.startswith("BAND") for b in cf.check_fill(_fill(price=9.00, side="SELL"), {}))
    # a fill away from the band is clean
    assert not any(b.startswith("BAND") for b in cf.check_fill(_fill(price=10.50), {}))


def test_realizability_reports_missing_data_instead_of_passing_silently(monkeypatch):
    cf = _fake_feed(monkeypatch, {})
    bad = cf.check_fill(_fill(), {})
    assert bad and bad[0].startswith("NO-DATA")
