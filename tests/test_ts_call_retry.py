"""`_ts_call` must retry a transient EMPTY response, not hand it back to the caller.

A single empty frame from Tushare for a symbol-day that does have data used to
abort a multi-hour backtest: `get_fundamental_data` returns the empty frame, then
`_fetch_single` raises "No fundamental data found for symbol: 688041.SH in date
range 20240801-20240801" from generate_daily_report. The same symbol-day returned a
row on the next invocation, so retrying is the correct fix — inventing defaults
would silently change the strategy.
"""

import pandas as pd
import pytest

from backtest.data.provider import TushareDataProvider


class Stub:
    """Just enough of a provider to exercise the retry loop without an API session."""

    rate_limit_delay = 0
    _ts_call = TushareDataProvider._ts_call


def frame(rows=1):
    return pd.DataFrame({"ts_code": ["600000.SH"] * rows, "trade_date": ["20240801"] * rows})


def test_retries_a_transient_empty_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def flaky(**kwargs):
        calls["n"] += 1
        return pd.DataFrame() if calls["n"] == 1 else frame()

    out = Stub()._ts_call(flaky)
    assert calls["n"] == 2, "should have retried once"
    assert len(out) == 1


def test_gives_up_after_the_configured_attempts(monkeypatch):
    monkeypatch.setenv("TS_CALL_EMPTY_ATTEMPTS", "3")
    calls = {"n": 0}

    def always_empty(**kwargs):
        calls["n"] += 1
        return pd.DataFrame()

    out = Stub()._ts_call(always_empty)
    assert calls["n"] == 3, "should try exactly TS_CALL_EMPTY_ATTEMPTS times"
    assert out.empty


def test_allow_empty_does_not_retry(monkeypatch):
    """Holidays and genuinely-empty interfaces keep the single attempt."""
    calls = {"n": 0}

    def always_empty(**kwargs):
        calls["n"] += 1
        return pd.DataFrame()

    Stub()._ts_call(always_empty, allow_empty=True)
    assert calls["n"] == 1


def test_non_frame_response_still_raises():
    """A non-DataFrame response must not be returned as data.

    `_ts_call` is also wrapped by tenacity, which retries a raised DataProviderError
    and finally surfaces `RetryError` — so assert on raising, not on the exact class.
    """
    with pytest.raises(Exception) as ei:
        Stub()._ts_call(lambda **kw: None)
    assert "DataProviderError" in repr(ei.value) or "RetryError" in type(ei.value).__name__
