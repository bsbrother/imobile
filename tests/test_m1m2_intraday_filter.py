"""Tests for the M1/M2 intraday features and the veto.

Properties that matter: features are computed from the right bars, the veto only
removes (never reorders), and no failure can empty or reshape the book.
"""

import pandas as pd

from backtest.strategies.m1m2_intraday_filter import (
    apply_m1m2_rerank,
    apply_m1m2_veto,
    regime_allows,
    session_features,
)

PRIOR = "2026-09-25"
REF = "2026-09-28"


def _bars(sessions: dict[str, dict[str, tuple]]):
    """Build a frame from {date: {(hh:mm, open, high, low, close, volume)}}."""
    rows = []
    for day, bars in sessions.items():
        for clock, (o, h, lo, cl, v) in bars.items():
            rows.append({"day": pd.Timestamp(f"{day} {clock}:00"),
                         "open": o, "high": h, "low": lo, "close": cl, "volume": v})
    return pd.DataFrame(rows).sort_values("day").reset_index(drop=True)


def _session(open_px, last_open, last_close, last_vol, open_vol=100.0, other_vol=50.0):
    """A session with an opening bar, filler bars, and a closing bar."""
    b = {"10:00": (open_px, open_px, open_px, open_px, open_vol)}
    for clock in ("10:30", "11:00", "11:30", "13:30", "14:00", "14:30"):
        b[clock] = (10.0, 10.0, 10.0, 10.0, other_vol)
    b["15:00"] = (last_open, last_open, last_close, last_close, last_vol)
    return b


def _candidates(codes=("A.SZ", "B.SZ", "C.SZ")):
    return pd.DataFrame({"rank": list(range(1, len(codes) + 1)), "ts_code": list(codes)})


def _features(late_ret=None, auction_gap=None):
    f = {}
    f["A.SZ"] = {"late_ret": late_ret if late_ret is not None else 0.01,
                 "auction_gap": auction_gap if auction_gap is not None else 0.01,
                 "late_vol_share": 0.2, "auction_vol_share": 0.1}
    f["B.SZ"] = {"late_ret": 0.02, "auction_gap": 0.0, "late_vol_share": 0.2,
                 "auction_vol_share": 0.1}
    f["C.SZ"] = {"late_ret": 0.03, "auction_gap": -0.001, "late_vol_share": 0.2,
                 "auction_vol_share": 0.1}
    return f


# ---------------------------------------------------------------- features ====

def test_m1_and_m2_features_are_computed_from_the_right_bars():
    bars = _bars({
        PRIOR: _session(9.0, 9.5, 9.6, 100.0),
        REF: _session(10.0, 10.0, 10.5, 200.0),   # opens 10.0, last bar 10.0 -> 10.5
    })
    f = session_features(bars, "20260928")
    assert f is not None
    assert abs(f["late_ret"] - 0.05) < 1e-9, "M2: close/open of the 14:30-15:00 bar"
    assert abs(f["auction_gap"] - (10.0 / 9.6 - 1.0)) < 1e-9, "M1: open vs prior close"
    # session volume: opening 100 + six filler bars at 50 + closing 200 = 600
    assert abs(f["late_vol_share"] - 200.0 / 600.0) < 1e-9


def test_missing_session_returns_none():
    bars = _bars({PRIOR: _session(9.0, 9.5, 9.6, 100.0)})
    assert session_features(bars, "20260928") is None


def test_missing_closing_bar_returns_none():
    b = _session(10.0, 10.0, 10.5, 200.0)
    del b["15:00"]
    assert session_features(_bars({PRIOR: _session(9.0, 9.5, 9.6, 100.0), REF: b}),
                            "20260928") is None


def test_empty_or_none_bars_are_handled():
    assert session_features(None, "20260928") is None
    assert session_features(pd.DataFrame(), "20260928") is None


# -------------------------------------------------------------------- veto ====

def test_late_weakness_drops_only_that_candidate():
    out = apply_m1m2_veto(_candidates(), "20260928",
                          features=_features(late_ret=-0.02))
    assert list(out["ts_code"]) == ["B.SZ", "C.SZ"], "only A showed late weakness"


def test_veto_never_reorders_the_survivors():
    out = apply_m1m2_veto(_candidates(), "20260928",
                          features=_features(late_ret=-0.02))
    assert list(out["ts_code"]) == ["B.SZ", "C.SZ"], "original relative order kept"


def test_rank_is_renumbered_after_a_drop():
    out = apply_m1m2_veto(_candidates(), "20260928",
                          features=_features(late_ret=-0.02))
    assert list(out["rank"]) == [1, 2]


def test_weak_auction_gap_is_also_vetoed():
    f = _features()
    f["B.SZ"]["auction_gap"] = -0.05   # opened 5% below the prior close
    out = apply_m1m2_veto(_candidates(), "20260928", features=f, late_ret_min=-1.0)
    assert list(out["ts_code"]) == ["A.SZ", "C.SZ"]


def test_auction_check_can_be_disabled():
    f = _features()
    f["B.SZ"]["auction_gap"] = -0.05
    out = apply_m1m2_veto(_candidates(), "20260928", features=f, late_ret_min=-1.0,
                          auction_gap_min=None)
    assert list(out["ts_code"]) == ["A.SZ", "B.SZ", "C.SZ"]


def test_healthy_pool_is_untouched():
    out = apply_m1m2_veto(_candidates(), "20260928", features=_features())
    assert list(out["ts_code"]) == ["A.SZ", "B.SZ", "C.SZ"]


def test_over_broad_veto_is_abandoned_rather_than_emptying_the_book():
    f = {c: {"late_ret": -0.5, "auction_gap": -0.5} for c in ("A.SZ", "B.SZ", "C.SZ")}
    out = apply_m1m2_veto(_candidates(), "20260928", features=f, max_drop_frac=0.5)
    assert len(out) == 3, "dropping 100% must be refused"


def test_no_features_leaves_the_pool_untouched():
    out = apply_m1m2_veto(_candidates(), "20260928", features={})
    assert list(out["ts_code"]) == ["A.SZ", "B.SZ", "C.SZ"]


def test_candidates_without_features_are_kept():
    out = apply_m1m2_veto(_candidates(), "20260928",
                          features={"C.SZ": {"late_ret": -0.9, "auction_gap": 0.0}})
    assert list(out["ts_code"]) == ["A.SZ", "B.SZ"], "uncovered names stay, C is dropped"


def test_fetch_failure_leaves_the_pool_untouched(monkeypatch):
    def boom(df, ref_date):
        raise ConnectionError("sina down")

    monkeypatch.setattr("backtest.strategies.m1m2_intraday_filter._collect", boom)
    out = apply_m1m2_veto(_candidates(), "20260928")   # features=None -> collect -> raises
    assert list(out["ts_code"]) == ["A.SZ", "B.SZ", "C.SZ"]


def test_empty_or_malformed_frames_pass_through():
    assert apply_m1m2_veto(pd.DataFrame(), "20260928", features=_features()).empty
    no_code = pd.DataFrame({"rank": [1]})
    out = apply_m1m2_veto(no_code, "20260928", features=_features())
    assert list(out.columns) == ["rank"]


# ---------------------------------------------------------------- re-rank =====

def _rank_features(gaps: dict[str, float], lates: dict[str, float] | None = None):
    lates = lates or {}
    return {c: {"auction_gap": g, "late_ret": lates.get(c, 0.0)} for c, g in gaps.items()}


def test_rerank_puts_the_strongest_auction_first():
    f = _rank_features({"A.SZ": 0.0, "B.SZ": 0.03, "C.SZ": 0.01}, None)
    out = apply_m1m2_rerank(_candidates(), "20260928", features=f)
    assert list(out["ts_code"]) == ["B.SZ", "C.SZ", "A.SZ"]


def test_rerank_preserves_the_pool_size():
    f = _rank_features({"A.SZ": 0.0, "B.SZ": 0.03, "C.SZ": 0.01})
    out = apply_m1m2_rerank(_candidates(), "20260928", features=f)
    assert len(out) == 3 and set(out["ts_code"]) == {"A.SZ", "B.SZ", "C.SZ"}
    assert list(out["rank"]) == [1, 2, 3]


def test_rerank_treats_missing_features_as_neutral():
    """A name with no auction data must not be pushed to either end arbitrarily."""
    f = _rank_features({"A.SZ": -0.05, "B.SZ": 0.05})   # C has none
    out = apply_m1m2_rerank(_candidates(), "20260928", features=f)
    assert list(out["ts_code"]) == ["B.SZ", "C.SZ", "A.SZ"]


def test_rerank_secondary_weight_can_break_ties():
    f = _rank_features({"A.SZ": 0.02, "B.SZ": 0.02}, {"A.SZ": -0.01, "B.SZ": 0.01})
    out = apply_m1m2_rerank(_candidates(), "20260928", features=f, w_m2=1.0)
    assert list(out["ts_code"])[0] == "B.SZ", "equal M1, M2 decides"


def test_rerank_is_a_noop_when_all_gaps_are_equal():
    f = _rank_features({"A.SZ": 0.01, "B.SZ": 0.01, "C.SZ": 0.01})
    out = apply_m1m2_rerank(_candidates(), "20260928", features=f)
    assert list(out["ts_code"]) == ["A.SZ", "B.SZ", "C.SZ"]


def test_rerank_no_features_leaves_the_order_alone():
    out = apply_m1m2_rerank(_candidates(), "20260928", features={})
    assert list(out["ts_code"]) == ["A.SZ", "B.SZ", "C.SZ"]


def test_rerank_fetch_failure_leaves_the_order_alone(monkeypatch):
    def boom(df, ref_date):
        raise ConnectionError("sina down")

    monkeypatch.setattr("backtest.strategies.m1m2_intraday_filter._collect", boom)
    out = apply_m1m2_rerank(_candidates(), "20260928")
    assert list(out["ts_code"]) == ["A.SZ", "B.SZ", "C.SZ"]


# ----------------------------------------------------------- regime gate ======

def _fake_regime(monkeypatch, regime):
    monkeypatch.setattr("backtest.utils.market_regime.detect_market_regime",
                        lambda d, *a, **k: {"regime": regime})


def test_gate_absent_means_always_allowed(monkeypatch):
    assert regime_allows("20260928", None) is True
    assert regime_allows("20260928", set()) is True


def test_gate_allows_matching_regime(monkeypatch):
    _fake_regime(monkeypatch, "bear")
    assert regime_allows("20260928", {"bear", "volatile"}) is True


def test_gate_blocks_non_matching_regime(monkeypatch):
    _fake_regime(monkeypatch, "bull")
    assert regime_allows("20260928", {"bear", "volatile"}) is False


def test_gate_is_case_insensitive(monkeypatch):
    _fake_regime(monkeypatch, "BEAR")
    assert regime_allows("20260928", {"bear"}) is True


def test_gate_fails_closed_when_the_regime_lookup_raises(monkeypatch):
    def boom(d, *a, **k):
        raise ValueError("insufficient index data")

    monkeypatch.setattr("backtest.utils.market_regime.detect_market_regime", boom)
    assert regime_allows("20260928", {"bear"}) is False, "must not intervene blind"


def test_gate_fails_closed_without_a_reference_date(monkeypatch):
    _fake_regime(monkeypatch, "bear")
    assert regime_allows("", {"bear"}) is False
