"""Tests for the stock_basic universe fetch/cache path.

`get_basic_information_api()` takes no date argument, so every call returns the
same current-universe snapshot. The backtest engine runs one strategy subprocess
per trading date and 8 strategies call this, so a run issued ~160 identical
full-universe fetches. It is now cached in the shared pickle cache.

There are two behaviours under test:
  1. the cache actually suppresses repeat fetches, and
  2. a malformed/column-less response is rejected *inside* the retried call, so
     it surfaces as a clear DataProviderError instead of a KeyError in an
     unrelated module. That is how it failed in practice: `KeyError: 'name'`
     raised in ts_ths_dc.no_risky_stocks aborted a 3-hour backtest run.
"""

import pandas as pd
import pytest
from types import SimpleNamespace

from backtest.data import cache as cache_mod
from backtest.data.cache import DataCache
from backtest.data.provider import TushareDataProvider
from backtest.utils.exceptions import DataProviderError

# Call the underlying logic directly, bypassing tenacity's retry/backoff, so
# validation failures surface immediately instead of after ~5 attempts.
_raw_ts_call = TushareDataProvider._ts_call.__wrapped__  # type: ignore[attr-defined]

GOOD_FRAME = pd.DataFrame(
    {
        "ts_code": ["000001.SZ", "600519.SH"],
        "symbol": ["000001", "600519"],
        "name": ["平安银行", "贵州茅台"],
    }
)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    """Point the shared cache at a throwaway directory."""
    monkeypatch.setattr(cache_mod, "_global_cache_instance", DataCache(cache_dir=str(tmp_path)))
    yield tmp_path


@pytest.fixture
def provider():
    """A provider instance without constructing a live Tushare client."""
    p = object.__new__(TushareDataProvider)
    p.rate_limit_delay = 0
    # `self.pro.stock_basic` is evaluated as an argument before the (patched)
    # _ts_call runs, so the attribute must exist even though it is never called.
    p.pro = SimpleNamespace(stock_basic=lambda **kwargs: GOOD_FRAME.copy())  # type: ignore[assignment]
    return p


# ---------------------------------------------------------------- caching ----

def test_repeat_calls_issue_only_one_fetch(monkeypatch, provider, isolated_cache):
    calls = []

    def fake_ts_call(func, required_columns=None, **kwargs):
        calls.append(required_columns)
        return GOOD_FRAME.copy()

    monkeypatch.setattr(provider, "_ts_call", fake_ts_call)

    first = provider.get_basic_information_api()
    second = provider.get_basic_information_api()
    third = provider.get_basic_information_api()

    assert len(calls) == 1, f"expected 1 fetch for 3 calls, got {len(calls)}"
    assert calls[0] == ["ts_code", "name"], "shape validation was not requested"
    for other in (second, third):
        assert list(other.columns) == list(first.columns)
        assert len(other) == len(first)


def test_cache_is_shared_across_provider_instances(provider, isolated_cache, monkeypatch):
    """A fresh provider (i.e. the next date's subprocess) must reuse the cache."""
    calls = []

    def fake_ts_call(func, required_columns=None, **kwargs):
        calls.append(1)
        return GOOD_FRAME.copy()

    monkeypatch.setattr(provider, "_ts_call", fake_ts_call)
    provider.get_basic_information_api()

    other = object.__new__(TushareDataProvider)
    other.rate_limit_delay = 0
    monkeypatch.setattr(other, "_ts_call", fake_ts_call)
    other.get_basic_information_api()

    assert len(calls) == 1, f"cross-instance cache miss: {len(calls)} fetches"


def test_failure_is_not_cached(monkeypatch, provider, isolated_cache):
    """A failed fetch must not poison the cache with an empty frame."""
    def boom(func, required_columns=None, **kwargs):
        raise DataProviderError("simulated transient API failure")

    monkeypatch.setattr(provider, "_ts_call", boom)
    with pytest.raises(DataProviderError):
        provider.get_basic_information_api()

    assert cache_mod.get_global_cache().get("stock_basic_all") is None

    # ...and a later successful call still works and gets cached
    def ok(func, required_columns=None, **kwargs):
        return GOOD_FRAME.copy()

    monkeypatch.setattr(provider, "_ts_call", ok)
    assert len(provider.get_basic_information_api()) == 2


# ------------------------------------------------------------- validation ----

def test_missing_required_column_is_rejected(provider):
    """The exact production failure: a frame with no 'name' column."""
    def column_less(**kwargs):
        return pd.DataFrame({"ts_code": ["000001.SZ"]})

    with pytest.raises(DataProviderError, match="unusable frame"):
        _raw_ts_call(provider, column_less, required_columns=["ts_code", "name"])


def test_empty_frame_is_rejected(provider):
    def empty(**kwargs):
        return pd.DataFrame()

    with pytest.raises(DataProviderError, match="unusable frame"):
        _raw_ts_call(provider, empty, required_columns=["ts_code", "name"])


def test_good_frame_passes_validation(provider):
    def good(**kwargs):
        return GOOD_FRAME.copy()

    out = _raw_ts_call(provider, good, required_columns=["ts_code", "name"])
    assert len(out) == 2
    assert "name" in out.columns


def test_validation_is_opt_in(provider):
    """Callers that pass no required_columns keep the old lenient behaviour."""
    def column_less(**kwargs):
        return pd.DataFrame({"anything": [1]})

    out = _raw_ts_call(provider, column_less)
    assert list(out.columns) == ["anything"]