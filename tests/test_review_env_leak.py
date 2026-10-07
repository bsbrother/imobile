"""`apply_review_env_overrides` must CLEAR its keys, not just skip them.

The review layer writes per-date multipliers into the environment, and
`market_regime.get_regime_config` reads `REVIEW_HOLD_MULT or HOLD_DAYS_MULT`
(and REVIEW_SL_TIGHT multiplies stop_loss_pct).

The original code only wrote a key when the value was != 1.0 and never removed
it, so the first date whose review asked for, say, holding_days_mult=0.7 left
REVIEW_HOLD_MULT=0.7 in os.environ for the remainder of the process. Every later
date then read 0.7 instead of the configured HOLD_DAYS_MULT — which silently
invalidates any HOLD_DAYS_MULT or SL A/B run against that process.

Observed: a HOLD_DAYS_MULT=1.0 A/B logged "HOLD_DAYS_MULT=0.7: bear max_hold 1 -> 1d"
for bear, i.e. the flag under test was not the value in force.
"""

import pytest

from backtest.engine import apply_review_env_overrides

KEYS = ("REVIEW_HOLD_MULT", "REVIEW_TP_AGGRESSIVE", "REVIEW_SL_TIGHT")


@pytest.fixture
def env():
    """Isolated environ dict, so the real process environment is never touched."""
    return {}


def test_non_default_values_are_written(env):
    apply_review_env_overrides(0.7, 1.5, 0.5, environ=env)
    assert env["REVIEW_HOLD_MULT"] == "0.7"
    assert env["REVIEW_TP_AGGRESSIVE"] == "1.5"
    assert env["REVIEW_SL_TIGHT"] == "0.5"


def test_default_values_do_not_write(env):
    apply_review_env_overrides(1.0, 1.0, 1.0, environ=env)
    for key in KEYS:
        assert key not in env


def test_stale_override_is_cleared(env):
    """The regression: a later date returning 1.0 must drop the earlier override."""
    apply_review_env_overrides(0.7, 1.0, 1.0, environ=env)
    assert env["REVIEW_HOLD_MULT"] == "0.7"

    apply_review_env_overrides(1.0, 1.0, 1.0, environ=env)
    assert "REVIEW_HOLD_MULT" not in env, (
        "REVIEW_HOLD_MULT leaked from an earlier date; get_regime_config would "
        "keep reading it in preference to HOLD_DAYS_MULT"
    )


def test_each_date_reflects_that_dates_review(env):
    """A mixed sequence ends with the last date's values, nothing older."""
    for hold, tp, sl in [(0.7, 1.0, 1.0), (1.0, 1.0, 0.5), (1.0, 1.0, 1.0)]:
        apply_review_env_overrides(hold, tp, sl, environ=env)
    for key in KEYS:
        assert key not in env


def test_default_environ_is_os_environ(monkeypatch):
    """Called without `environ`, it acts on the process environment (the real call site)."""
    monkeypatch.delenv("REVIEW_HOLD_MULT", raising=False)
    monkeypatch.setenv("REVIEW_HOLD_MULT", "0.7")
    apply_review_env_overrides(1.0, 1.0, 1.0)
    import os
    assert "REVIEW_HOLD_MULT" not in os.environ
