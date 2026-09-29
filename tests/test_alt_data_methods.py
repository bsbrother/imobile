"""Tests for M1/M2/M3 — the alternative-data methods and the no-limit-up ranking.

Nothing here touches the network. The Tushare client is replaced with a
SimpleNamespace so the suite stays deterministic, and tenacity's retry is bypassed
via __wrapped__ wherever a failure is the expected outcome (otherwise a test that
asserts on an error would spend ~5 backoff rounds proving it).
"""

from types import SimpleNamespace

import akshare as ak
import pandas as pd
import pytest

from backtest.data.provider import AkshareDataProvider, TushareDataProvider, _is_retryable
from backtest.utils.exceptions import DataProviderError
from backtest.utils.leader_rank import LimitUpCountError, rank_leaders_no_limit_up

_raw_ts_call = TushareDataProvider._ts_call.__wrapped__  # type: ignore[attr-defined]


class _MemoryCache:
    """Stand-in for SQLiteDataCache so tests never touch the real cache DB."""

    def __init__(self) -> None:
        self.store: dict = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


@pytest.fixture
def provider():
    """A provider with no live Tushare client and no real cache.

    The interface attributes must exist because `self.pro.<api>` is evaluated as an
    argument *before* the patched `_ts_call` runs -- same reason
    test_stock_basic_cache.py pre-populates `pro.stock_basic`. Each raises, so a test
    that forgets to patch the call fails loudly instead of silently passing.
    """
    def _unpatched(*args, **kwargs):
        raise AssertionError("test reached the live API client instead of patching _ts_call")

    p = object.__new__(TushareDataProvider)
    p.rate_limit_delay = 0
    p.cache = _MemoryCache()  # type: ignore[assignment]
    p.pro = SimpleNamespace(  # type: ignore[assignment]
        stk_auction_o=_unpatched,
        stk_mins=_unpatched,
        ths_hot=_unpatched,
        moneyflow_dc=_unpatched,
        moneyflow_ind_dc=_unpatched,
        top_list=_unpatched,
        top_inst=_unpatched,
    )
    return p


# ============================================================ retry policy ====

@pytest.mark.parametrize(
    "message,expected",
    [
        ("抱歉，您没有接口(stk_auction_o)访问权限", False),
        ("抱歉，您访问接口(stk_mins)频率超限(1次/小时)", False),
        ("抱歉，您的积分不足", False),
        ("Connection aborted.", True),
        ("502 Bad Gateway", True),
    ],
)
def test_only_transient_failures_are_retried(message, expected):
    """A denial must not be retried: tenacity's backoff made the rate cap worse."""
    assert _is_retryable(Exception(message)) is expected


# ======================================================= date normalisation ====

def test_timestamps_survive_date_normalisation(provider):
    """stk_mins needs 'YYYY-MM-DD HH:MM:SS'; convert_trade_date would truncate the clock."""
    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return pd.DataFrame({"trade_date": ["20260615"], "close": [1.0]})

    _raw_ts_call(
        provider, capture,
        start_date="2026-06-15 14:30:00", end_date="2026-06-15 15:00:00",
    )
    assert seen["start_date"] == "2026-06-15 14:30:00"
    assert seen["end_date"] == "2026-06-15 15:00:00"


def test_plain_dates_are_still_normalised(provider):
    seen = {}

    def capture(**kwargs):
        seen.update(kwargs)
        return pd.DataFrame({"trade_date": ["20260615"], "close": [1.0]})

    _raw_ts_call(provider, capture, start_date="2026-06-15", end_date="20260615")
    assert seen["start_date"] == "20260615"
    assert seen["end_date"] == "20260615"


def test_empty_frame_allowed_only_when_asked(provider):
    """A holiday is not a malformed response, but it is still a bad answer by default."""

    def empty(**kwargs):
        return pd.DataFrame(columns=["ts_code", "close"])  # type: ignore[arg-type]

    out = _raw_ts_call(provider, empty, required_columns=["ts_code"], allow_empty=True)
    assert out.empty

    with pytest.raises(DataProviderError, match="unusable frame"):
        _raw_ts_call(provider, empty, required_columns=["ts_code"])


# ================================================================== M1 ========

def test_auction_data_needs_a_selector(provider):
    with pytest.raises(DataProviderError, match="at least one of"):
        provider.get_auction_data()


def test_auction_permission_denial_names_the_interface(provider, monkeypatch):
    """The real account state: stk_auction_o is refused outright."""

    def denied(func=None, **kwargs):
        raise Exception("抱歉，您没有接口(stk_auction_o)访问权限")

    monkeypatch.setattr(provider, "_ts_call", denied)
    with pytest.raises(DataProviderError, match="stk_auction_o"):
        provider.get_auction_data(trade_date="20260928")


def test_auction_data_is_cached(provider, monkeypatch):
    calls = []

    def fake(func, **kwargs):
        calls.append(kwargs)
        return pd.DataFrame({
            "ts_code": ["600000.SH"], "trade_date": ["20260928"],
            "open": [1.0], "vwap": [1.1], "close": [1.2], "extra_col": ["dropped"],
        })

    monkeypatch.setattr(provider, "_ts_call", fake)
    first = provider.get_auction_data(ts_code="600000.SH", trade_date="20260928")
    second = provider.get_auction_data(ts_code="600000.SH", trade_date="20260928")

    assert len(calls) == 1, "repeat call re-fetched instead of using the cache"
    assert "extra_col" not in first.columns, "uncontracted columns leaked through"
    assert list(second.columns) == list(first.columns)


# ================================================================== M2 ========

def test_intraday_bars_filter_to_the_window_and_sort_oldest_first(provider, monkeypatch):
    """stk_mins answers newest-first and ignores nothing; window and order are ours to enforce."""
    descending = pd.DataFrame({
        "ts_code": ["600000.SH"] * 6,
        "trade_time": [
            "2026-06-15 15:00:00", "2026-06-15 14:45:00", "2026-06-15 14:30:00",
            "2026-06-15 14:29:00", "2026-06-15 09:31:00", "2026-06-15 09:30:00",
        ],
        "close": [10.0, 10.1, 10.2, 10.3, 10.4, 10.5],
    })
    monkeypatch.setattr(provider, "_ts_call", lambda func, **kwargs: descending.copy())

    out = provider.get_intraday_bars("600000.SH", "20260615")

    assert list(out["trade_time"]) == [
        "2026-06-15 14:30:00", "2026-06-15 14:45:00", "2026-06-15 15:00:00",
    ]
    assert list(out["close"]) == [10.2, 10.1, 10.0], "rows are not oldest-first"


def test_intraday_bars_request_the_clock_formatted_window(provider, monkeypatch):
    seen = {}

    def capture(func, **kwargs):
        seen.update(kwargs)
        return pd.DataFrame({
            "ts_code": ["600000.SH"], "trade_time": ["2026-06-15 14:30:00"], "close": [1.0],
        })

    monkeypatch.setattr(provider, "_ts_call", capture)
    provider.get_intraday_bars("600000.SH", "2026-06-15", start_time="14:35", end_time="14:55")

    assert seen["start_date"] == "2026-06-15 14:35:00"
    assert seen["end_date"] == "2026-06-15 14:55:00"
    assert seen["freq"] == "1min"


def test_intraday_bars_reject_an_unknown_freq(provider):
    with pytest.raises(DataProviderError, match="Unsupported freq"):
        provider.get_intraday_bars("600000.SH", "20260615", freq="7min")


def test_intraday_bars_require_code_and_date(provider):
    with pytest.raises(DataProviderError, match="requires both"):
        provider.get_intraday_bars("600000.SH", "")


def test_intraday_rate_limit_names_the_interface(provider, monkeypatch):
    def limited(func=None, **kwargs):
        raise Exception("抱歉，您访问接口(stk_mins)频率超限(1次/小时)")

    monkeypatch.setattr(provider, "_ts_call", limited)
    with pytest.raises(DataProviderError, match="rate limit"):
        provider.get_intraday_bars("600000.SH", "20260615")


def test_intraday_bars_are_cached_per_window(provider, monkeypatch):
    calls = []

    def fake(func, **kwargs):
        calls.append((kwargs["start_date"], kwargs["end_date"]))
        return pd.DataFrame({
            "ts_code": ["600000.SH"], "trade_time": ["2026-06-15 14:30:00"], "close": [1.0],
        })

    monkeypatch.setattr(provider, "_ts_call", fake)
    provider.get_intraday_bars("600000.SH", "20260615")
    provider.get_intraday_bars("600000.SH", "20260615")
    provider.get_intraday_bars("600000.SH", "20260615", start_time="14:00", end_time="14:30")

    assert len(calls) == 2, "identical queries should have been served from cache"


# ================================================================== M3 ========

def _signals() -> pd.DataFrame:
    return pd.DataFrame({
        "ts_code": ["A.SZ", "B.SZ", "C.SZ", "D.SZ"],
        "hot_rank": [1.0, 2.0, 3.0, None],
        "mf_net_amount_rate": [5.0, 20.0, -3.0, 10.0],
        "inst_net_buy": [1e6, None, None, 5e5],
    })


def test_get_leader_signals_merges_sources_and_sums_institutional_seats(provider, monkeypatch):
    provider.pro = SimpleNamespace(  # type: ignore[assignment]
        ths_hot=lambda **kw: pd.DataFrame({
            "data_type": ["热股", "热股", "港股"],
            "ts_code": ["600000.SH", "000001.SZ", "00700.HK"],
            "rank": [1, 2, 1],
            "hot": [9.0, 8.0, 7.0],
            "concept": ['["a"]', '["b"]', None],
        }),
        moneyflow_dc=lambda **kw: pd.DataFrame({
            "ts_code": ["600000.SH"], "net_amount": [1.0], "net_amount_rate": [2.0],
            "buy_elg_amount": [3.0], "buy_elg_amount_rate": [4.0],
        }),
        top_list=lambda **kw: pd.DataFrame({
            "ts_code": ["600000.SH", "600000.SH"], "net_amount": [1.0, 2.0],
            "net_rate": [5.0, 6.0], "reason": ["r1", "r2"],
        }),
        top_inst=lambda **kw: pd.DataFrame({
            "ts_code": ["600000.SH", "600000.SH"], "net_buy": [100.0, 50.0],
        }),
    )
    monkeypatch.setattr(provider, "_ts_call", lambda func, **kwargs: func(**kwargs))

    out = provider.get_leader_signals("20260928")
    row = out[out["ts_code"] == "600000.SH"].iloc[0]

    assert row["hot_rank"] == 1
    assert row["mf_net_amount_rate"] == 2.0
    assert row["lhb_net_amount"] == 1.0, "duplicate LHB reasons were not collapsed"
    assert row["inst_net_buy"] == 150.0, "institutional seats were not summed"
    assert "00700.HK" not in set(out["ts_code"]), "non-A-share heat rows leaked in"


def test_leader_ranking_prefers_flow_and_attention():
    out = rank_leaders_no_limit_up(_signals())
    assert out.iloc[0]["ts_code"] == "B.SZ"
    assert list(out["leader_rank"]) == list(range(1, len(out) + 1))
    assert out["leader_score"].is_monotonic_decreasing


def test_hot_rank_is_inverted():
    """Rank 1 in the App heat list is the strongest signal, not the smallest number."""
    frame = pd.DataFrame({"ts_code": ["hot.SZ", "cold.SZ"], "hot_rank": [1.0, 90.0]})
    out = rank_leaders_no_limit_up(frame)
    assert out.iloc[0]["ts_code"] == "hot.SZ"


def test_limit_up_columns_are_refused():
    """The whole point of M3: it must not quietly rank on the thing it exists to avoid."""
    for column in ("limit_up_score", "consecutive_limit_up", "涨停数"):
        frame = _signals()
        frame[column] = [1, 2, 3, 4]
        with pytest.raises(LimitUpCountError, match=column):
            rank_leaders_no_limit_up(frame)


def test_missing_signal_is_excluded_not_scored_as_worst():
    """A stock with no LHB seat must not be treated as if its institutional net buy were the day's worst."""
    others = pd.DataFrame({
        "ts_code": ["P.SZ", "Q.SZ"],
        "mf_net_amount_rate": [30.0, 1.0],
        "inst_net_buy": [5e6, -5e6],
    })
    base = pd.DataFrame({"ts_code": ["X.SZ"], "mf_net_amount_rate": [10.0]})

    missing = rank_leaders_no_limit_up(
        pd.concat([base, others], ignore_index=True)
    )
    worst = rank_leaders_no_limit_up(
        pd.concat([base.assign(inst_net_buy=-5e6), others], ignore_index=True)
    )

    x_missing = missing[missing["ts_code"] == "X.SZ"].iloc[0]["leader_score"]
    x_worst = worst[worst["ts_code"] == "X.SZ"].iloc[0]["leader_score"]
    assert x_missing > x_worst, "a missing signal was scored as the worst value instead of excluded"


def test_min_signals_drops_thin_rows():
    frame = pd.DataFrame({
        "ts_code": ["thin.SZ", "thick.SZ"],
        "mf_net_amount_rate": [50.0, 1.0],
        "inst_net_buy": [None, 1e6],
    })
    out = rank_leaders_no_limit_up(frame, min_signals=2)
    assert list(out["ts_code"]) == ["thick.SZ"]


def test_top_n_limits_rows():
    out = rank_leaders_no_limit_up(_signals(), top_n=2)
    assert len(out) == 2


def test_weights_can_select_a_signal_subset():
    out = rank_leaders_no_limit_up(_signals(), weights={"mf_net_amount_rate": 1.0})
    assert "score_mf_net_amount_rate" in out.columns
    assert not any(c.startswith("score_hot") for c in out.columns)


def test_unknown_signal_names_raise():
    with pytest.raises(ValueError, match="none of the requested signals"):
        rank_leaders_no_limit_up(_signals(), weights={"not_a_column": 1.0})


def test_empty_input_returns_an_empty_ranking():
    out = rank_leaders_no_limit_up(pd.DataFrame())
    assert out.empty
    assert "leader_score" in out.columns


def test_ranking_requires_a_ts_code_column():
    with pytest.raises(ValueError, match="ts_code"):
        rank_leaders_no_limit_up(pd.DataFrame({"something": [1, 2]}))


# ================================================== M1/M2 via akshare ========

@pytest.fixture
def ak_provider():
    """AkshareDataProvider with no real cache and no live network."""
    p = object.__new__(AkshareDataProvider)
    p.rate_limit_delay = 0
    p.cache = _MemoryCache()  # type: ignore[assignment]
    return p


def _sina_minutes() -> pd.DataFrame:
    """Shape returned by ak.stock_zh_a_minute: `day`, oldest first."""
    return pd.DataFrame({
        "day": [
            "2026-09-28 14:29:00", "2026-09-28 14:30:00", "2026-09-28 14:45:00",
            "2026-09-28 15:00:00", "2026-09-29 14:30:00", "2026-09-29 14:31:00",
        ],
        "open": [9.0, 9.1, 9.2, 9.3, 9.4, 9.5],
        "high": [9.0, 9.1, 9.2, 9.3, 9.4, 9.5],
        "low": [9.0, 9.1, 9.2, 9.3, 9.4, 9.5],
        "close": [9.0, 9.1, 9.2, 9.3, 9.4, 9.5],
        "volume": [1, 2, 3, 4, 5, 6],
    })


def _tencent_ticks() -> pd.DataFrame:
    """Shape returned by ak.stock_zh_a_tick_tx_js -- note row 0 is the 09:25 auction."""
    return pd.DataFrame({
        "成交时间": ["09:25:00", "09:30:00", "09:30:03"],
        "成交价格": [9.15, 9.15, 9.16],
        "价格变动": [-0.02, 0.0, 0.01],
        "成交量": [2142, 794, 2517],
        "成交金额": [1959930, 727216, 2303724],
        "性质": ["卖盘", "买盘", "买盘"],
    })


def test_ak_prefixed_symbol_mapping():
    assert AkshareDataProvider._ts_to_ak_prefixed("600519.SH") == "sh600519"
    assert AkshareDataProvider._ts_to_ak_prefixed("000001.SZ") == "sz000001"
    assert AkshareDataProvider._ts_to_ak_prefixed("600519") == "sh600519"


def test_ak_intraday_filters_window_sorts_and_tags(ak_provider, monkeypatch):
    monkeypatch.setattr(ak_provider, "_ak_call", lambda func, **kw: _sina_minutes())
    out = ak_provider.get_intraday_bars("600000", start_time="14:30", end_time="15:00")

    assert list(out["trade_time"]) == [
        "2026-09-28 14:30:00", "2026-09-28 14:45:00", "2026-09-28 15:00:00",
        "2026-09-29 14:30:00", "2026-09-29 14:31:00",
    ], "14:29 must be excluded and the series must be oldest-first"
    assert list(out["ts_code"].unique()) == ["600000.SH"]
    assert list(out.columns) == [
        "ts_code", "trade_time", "open", "high", "low", "close", "volume",
    ]


def test_ak_intraday_trade_date_filter(ak_provider, monkeypatch):
    monkeypatch.setattr(ak_provider, "_ak_call", lambda func, **kw: _sina_minutes())
    out = ak_provider.get_intraday_bars("600000", trade_date="20260928")
    assert set(out["trade_time"].str.slice(0, 10)) == {"2026-09-28"}
    assert len(out) == 3


def test_ak_intraday_rejects_unknown_period(ak_provider):
    with pytest.raises(DataProviderError, match="Unsupported intraday period"):
        ak_provider.get_intraday_bars("600000", period="7")


def test_ak_auction_uses_tencent_print_when_no_proxy(ak_provider, monkeypatch):
    """The 09:25 print IS the auction result, and it must be flagged latest-day-only."""
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("https_proxy", raising=False)
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    monkeypatch.delenv("http_proxy", raising=False)
    monkeypatch.setattr(ak_provider, "_ak_call", lambda func, **kw: _tencent_ticks())

    out = ak_provider.get_auction_data("600000")
    row = out.iloc[0]

    assert row["source"] == "tencent_tick"
    assert row["auction_time"] == "09:25:00"
    assert row["auction_price"] == 9.15
    assert row["auction_volume"] == 2142
    assert row["auction_amount"] == 1959930
    assert bool(row["is_latest_only"]) is True


def test_ak_auction_prefers_eastmoney_when_a_proxy_is_set(ak_provider, monkeypatch):
    """EastMoney is the only historical 09:15-09:25 source, so a proxy must switch to it."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:60001")
    em_frame = pd.DataFrame({
        "时间": ["09:15", "09:25"],
        "最新价": [9.10, 9.15],
        "成交量": [1000, 2142],
        "成交额": [910000, 1959930],
    })

    def dispatch(func, **kwargs):
        if func is ak.stock_zh_a_hist_pre_min_em:
            return em_frame
        raise AssertionError(f"unexpected source with a proxy set: {func}")

    monkeypatch.setattr(ak_provider, "_ak_call", dispatch)
    out = ak_provider.get_auction_data("600000", trade_date="20260928")
    row = out.iloc[0]

    assert row["source"] == "eastmoney_pre_min"
    assert row["auction_price"] == 9.15, "the 09:25 bar is the auction result, not 09:15"
    assert bool(row["is_latest_only"]) is False


def test_ak_auction_falls_back_to_tencent_when_eastmoney_fails(ak_provider, monkeypatch):
    """A configured proxy that EastMoney still refuses must degrade, not explode."""
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:60001")

    def dispatch(func, **kwargs):
        if func is ak.stock_zh_a_hist_pre_min_em:
            raise ConnectionError("RemoteDisconnected")
        return _tencent_ticks()

    monkeypatch.setattr(ak_provider, "_ak_call", dispatch)
    out = ak_provider.get_auction_data("600000")
    assert out.iloc[0]["source"] == "tencent_tick"


def test_ak_auction_raises_when_every_source_fails(ak_provider, monkeypatch):
    def boom(func, **kwargs):
        raise ConnectionError("RemoteDisconnected")

    monkeypatch.setattr(ak_provider, "_ak_call", boom)
    with pytest.raises(DataProviderError, match="auction data unavailable"):
        ak_provider.get_auction_data("600000")


def test_ak_intraday_is_cached(ak_provider, monkeypatch):
    calls = []

    def fake(func, **kwargs):
        calls.append(kwargs)
        return _sina_minutes()

    monkeypatch.setattr(ak_provider, "_ak_call", fake)
    ak_provider.get_intraday_bars("600000")
    ak_provider.get_intraday_bars("600000")
    assert len(calls) == 1, "repeat call re-fetched instead of using the cache"
