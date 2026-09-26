"""Regression tests for get_regime_config idempotency.

`ConfigManager.get()` returns the live nested dict by reference, so
`get_regime_config`'s in-place overrides used to persist into the manager's
cache and compound on every call for the same regime. The multiplicative
overrides — HOLD_DAYS_MULT / REVIEW_HOLD_MULT on max_hold_days, and
REVIEW_SL_TIGHT on stop_loss_pct — were the ones that drifted.

Observed in a real backtest run before the fix (logs/app.log):
    normal max_hold 5 -> 3 -> 2      (multiplied twice)
    bull   max_hold 7 -> 3 -> 1

Left unfixed, the effective holding period depends on how many times each
regime occurs, i.e. on the date range — which breaks run-to-run
reproducibility.
"""

import pytest

from backtest.utils.market_regime import get_regime_config
from backtest.utils.config import ConfigManager

CONFIG_FILE = "backtest/config.json"
REGIMES = ("bull", "normal", "volatile", "bear")


@pytest.fixture
def cm():
    return ConfigManager(config_file=CONFIG_FILE)


@pytest.mark.parametrize("regime", REGIMES)
def test_hold_mult_is_idempotent(monkeypatch, cm, regime):
    """Repeated calls with a constant multiplier must not drift."""
    monkeypatch.setenv("HOLD_DAYS_MULT", "0.5")
    monkeypatch.delenv("REVIEW_HOLD_MULT", raising=False)
    seen = []
    for _ in range(3):
        cfg = get_regime_config(regime, cm)
        seen.append((float(cfg["stop_loss_pct"]), int(cfg["max_hold_days"])))
    assert len(set(seen)) == 1, f"{regime} compounded across calls: {seen}"


@pytest.mark.parametrize("regime", REGIMES)
def test_review_sl_tight_applies_once(monkeypatch, cm, regime):
    """REVIEW_SL_TIGHT must scale the base SL exactly once per call."""
    monkeypatch.setenv("REVIEW_SL_TIGHT", "0.5")
    first = float(get_regime_config(regime, cm)["stop_loss_pct"])
    second = float(get_regime_config(regime, cm)["stop_loss_pct"])
    third = float(get_regime_config(regime, cm)["stop_loss_pct"])
    assert first == pytest.approx(second) == pytest.approx(third), (
        f"{regime} REVIEW_SL_TIGHT compounded: {first}, {second}, {third}"
    )


def test_multiplier_does_not_leak_into_cache(monkeypatch, cm):
    """Switching the multiplier back must restore the base value."""
    monkeypatch.setenv("HOLD_DAYS_MULT", "1.0")
    base = int(get_regime_config("bull", cm)["max_hold_days"])

    monkeypatch.setenv("HOLD_DAYS_MULT", "0.5")
    halved = int(get_regime_config("bull", cm)["max_hold_days"])

    monkeypatch.setenv("HOLD_DAYS_MULT", "1.0")
    restored = int(get_regime_config("bull", cm)["max_hold_days"])

    assert halved < base, "0.5 multiplier had no effect"
    # Without the copy fix this returns `halved` again, not `base`.
    assert restored == base, (
        f"multiplier leaked into ConfigManager cache: base={base}, "
        f"halved={halved}, restored={restored}"
    )


def test_late_trend_filter_is_not_shared(monkeypatch, cm):
    """The handed-out late_trend_filter must not alias the cached dict."""
    cfg = get_regime_config("bull", cm)
    filt = cfg.get("late_trend_filter")
    if not filt:
        pytest.skip("no late_trend_filter configured for bull")
    filt["ma_threshold"] = 999.0  # mutate what we were given
    fresh = get_regime_config("bull", cm)["late_trend_filter"]
    assert fresh.get("ma_threshold") != 999.0, "late_trend_filter aliases the cache"