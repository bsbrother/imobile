"""Unit tests for the ts_turning_point signal gates (offline, synthetic bars).

The rule under test (docs/strategies/turning_point.md):
    big-volume turning candle out of a glued-MA consolidation, bought only if the
    NEXT bar holds the breakout level on continuing volume.

The fixture is shape-driven, not copied from a real chart: a wide-range glued
consolidation whose swing amplitude decays into a tight squeeze, a gentle decline
that pushes MACD below its signal line, then the turning candle (T) and the
confirmation candle (the reference date, T+1).
"""

import pandas as pd

import backtest.strategies.ts_turning_point as tp
from backtest.data import cache as cache_mod
from backtest.strategies.ts_turning_point import (
    REJECT,
    _reset_reject,
    analyze_turning_point,
    score_pick,
)
from backtest.utils.trading_calendar import get_trading_days_between

BASE_AMP = 0.25    # +- swing of the consolidation, in yuan
DECLINE_N = 13     # bars of gentle decline just before T
STEP = 0.012       # per-bar decline, in yuan


def _bars(pop=0.048, confirm_close=10.40, confirm_low=10.36, confirm_vol=2800.0):
    """Bars ending at the confirmation candle: [..., T (index -2), T+1 (index -1)]."""
    n_cons = 66
    rows = []
    for i in range(n_cons):
        px = 10.0 + BASE_AMP * ((-1) ** i)
        rows.append((px, px + 0.01, px - 0.01, px))
    iT = n_cons - 1
    for k in range(DECLINE_N):
        px = 10.0 - STEP * (k + 1)
        rows[iT - DECLINE_N + k] = (px, px + 0.01, px - 0.01, px)

    t_open = rows[iT - 1][3]
    t_close = t_open * (1 + pop)
    rows.append((t_open, t_close, t_open * 0.998, t_close))            # T
    rows.append((confirm_close - 0.02, confirm_close + 0.03,            # T+1
                 confirm_low, confirm_close))

    names = ["open", "high", "low", "close"]
    df = pd.DataFrame({name: [r[i] for r in rows] for i, name in enumerate(names)})
    df["vol"] = [1000.0] * (len(rows) - 2) + [3000.0, confirm_vol]
    df["amount"] = 100000.0
    df["pre_close"] = df["close"].shift(1).fillna(df["close"])
    df["trade_date"] = [f"2026{(i // 28) + 1:02d}{(i % 28) + 1:02d}" for i in range(len(rows))]
    return df


def test_confirmed_turning_point_is_picked():
    _reset_reject()
    m = analyze_turning_point(_bars())
    assert m is not None, f"textbook confirmed turning point must pass: {REJECT}"
    assert m["gain_T_pct"] >= 3.0
    assert m["body_ratio"] >= 0.6
    assert 0.0 <= m["squeeze_rank"] < 0.35
    assert 0.0 < score_pick(m, "normal") < 120.0


def test_confirmation_that_closes_below_the_turning_candle_is_rejected():
    """The whole point of the article: no hold on day 2 -> no buy."""
    _reset_reject()
    m = analyze_turning_point(_bars(confirm_close=10.0, confirm_low=9.98))
    assert m is None
    assert REJECT["confirm_hold"] >= 1


def test_confirmation_that_breaks_below_ma5_is_rejected():
    _reset_reject()
    m = analyze_turning_point(_bars(confirm_close=10.41, confirm_low=9.30))
    assert m is None
    assert REJECT["confirm_ma"] == 1


def test_confirmation_volume_collapse_is_rejected():
    _reset_reject()
    m = analyze_turning_point(_bars(confirm_vol=100.0))   # far below 0.8 x turning volume
    assert m is None
    assert REJECT["confirm_vol"] == 1


def test_weak_body_turning_candle_is_rejected():
    """A doji-ish bar is not a turning candle even if the next day rises."""
    _reset_reject()
    m = analyze_turning_point(_bars(pop=0.005))
    assert m is None
    assert REJECT["body"] + REJECT["gain"] >= 1


def test_flat_market_has_no_signal():
    _reset_reject()
    df = _bars()
    df.loc[len(df) - 2, ["open", "high", "low", "close"]] = [10.0, 10.01, 9.99, 10.0]
    assert analyze_turning_point(df) is None


# ── panel cache ────────────────────────────────────────────────
# Regression cover for the bug that silently zeroed a month of a run: a session
# whose reference date moved BACKWARDS sliced the cached panel to that date and
# stored the slice, so every later session saw < 50 bars and dropped every stock
# on the history gate.

class _FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ttl=None):
        self.store[key] = value
        return True


def _fake_fetch(start_date, end_date):
    days = get_trading_days_between(start_date, end_date)
    rows = [{"ts_code": "000001.SZ", "trade_date": d, "open": 10.0, "high": 10.1,
             "low": 9.9, "close": 10.0, "pre_close": 10.0, "vol": 1000.0, "amount": 1e5}
            for d in days]
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=pd.Index(tp.PANEL_COLS))


def _wire(monkeypatch):
    fake = _FakeCache()
    monkeypatch.setattr(cache_mod, "get_global_cache", lambda: fake)
    monkeypatch.setattr(tp, "_fetch_long", _fake_fetch)
    monkeypatch.setattr(tp, "TP_PANEL_CACHE", True)
    return fake


def test_panel_forward_session_fills_a_full_window(monkeypatch):
    _wire(monkeypatch)
    panel = tp.load_panel("20260615")
    bars = len(panel["000001.SZ"])
    assert bars >= tp.LOOKBACK_DAYS * 0.9


def test_panel_backwards_session_neither_shrinks_nor_starves(monkeypatch):
    fake = _wire(monkeypatch)
    tp.load_panel("20260615")
    through_before = fake.store[tp.PANEL_CACHE_KEY]["df"]["trade_date"].max()

    panel = tp.load_panel("20260302")          # earlier reference date
    assert len(panel["000001.SZ"]) >= tp.LOOKBACK_DAYS * 0.9, "backwards session starved of history"
    assert fake.store[tp.PANEL_CACHE_KEY]["df"]["trade_date"].max() >= through_before, \
        "cache must never shrink its tail"


def test_panel_head_backfill_covers_an_earlier_window(monkeypatch):
    _wire(monkeypatch)
    tp.load_panel("20260615")
    panel = tp.load_panel("20260105")          # needs bars from 2025-10
    dates = panel["000001.SZ"]["trade_date"]
    assert len(dates) >= tp.LOOKBACK_DAYS * 0.9
    assert dates.min() < "20251101"


def test_tp_skip_regime_returns_empty(monkeypatch):
    """Bear regime should return empty DataFrame when 'bear' in TP_SKIP_REGIMES."""
    monkeypatch.setattr(tp, "TP_SKIP_REGIMES", {"bear"})
    monkeypatch.setattr("backtest.utils.market_regime.detect_market_regime",
                        lambda *a, **k: {"regime": "bear"})
    monkeypatch.setattr("backtest.strategies.ts_turning_point.data_provider.get_basic_information_api",
                        lambda: __import__("pandas").DataFrame({"ts_code": ["000001.SZ"], "name": ["x"]}))
    monkeypatch.setattr("backtest.strategies.ts_turning_point.no_risky_stocks",
                        lambda *a, **k: ["000001.SZ"])
    monkeypatch.setattr(tp, "load_panel", lambda d: {})
    df = tp.pick_turning_point_stocks("20260105")
    assert df.empty, "bear regime with skip should return empty"