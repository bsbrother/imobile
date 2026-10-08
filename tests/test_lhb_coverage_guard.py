"""The LHB coverage guard must fail a run whose cache doesn't match the window.

`_apply_flow_filter_v2` is a PASSTHROUGH for a candidate with no LHB record, so a
cache that does not span the run window silently removes the flow signal for the
uncovered dates — the run then measures a different strategy and produces a number
that cannot be compared with a covered one.

Observed for real: `lhb_institutional_2026.csv` covered 2026-01..2026-07 only, so a
20260101-20260930 run traded Aug/Sep without the flow signal, and it carried 956
duplicate (代码, 上榜日期) rows which double-counted the summed net-buy.
"""

import pytest

from backtest.strategies import ts_7AZ_96MA_flow_v2 as V


def write_cache(path, rows):
    path.write_text("代码,上榜日期,机构买入净额\n" +
                    "".join(f"{c},{d},{n}\n" for c, d, n in rows), encoding="utf-8")


@pytest.fixture
def cache(tmp_path, monkeypatch):
    """Point the loader at a temp CSV and clear its memoised state."""
    p = tmp_path / "lhb.csv"
    monkeypatch.setattr(V, "LHB_CACHE", str(p))
    for name in ("_lhb_inst", "_lhb_start", "_lhb_end", "_lhb_dupes"):
        monkeypatch.setattr(V, name, None if name == "_lhb_inst" else 0)
    monkeypatch.setattr(V, "LHB_COVERAGE_GUARD", "strict")
    return p


def test_covered_window_passes(cache):
    write_cache(cache, [("600000", "2026-01-05", 1e8), ("600000", "2026-09-30", 1e8)])
    assert V.check_lhb_coverage("20260101", "20260930") == []


def test_window_ending_after_the_cache_raises(cache):
    """The real bug: cache stops 2026-07, run ends 2026-09."""
    write_cache(cache, [("600000", "2026-01-05", 1e8), ("600000", "2026-07-31", 1e8)])
    with pytest.raises(RuntimeError, match="has NO flow data"):
        V.check_lhb_coverage("20260101", "20260930")


def test_window_starting_before_the_cache_raises(cache):
    write_cache(cache, [("600000", "2026-03-02", 1e8), ("600000", "2026-09-30", 1e8)])
    with pytest.raises(RuntimeError, match="has NO flow data"):
        V.check_lhb_coverage("20260101", "20260930")


def test_duplicate_keys_raise(cache):
    write_cache(cache, [("600000", "2026-01-05", 1e8),
                        ("600000", "2026-01-05", 1e8),      # duplicate
                        ("600000", "2026-09-30", 1e8)])
    with pytest.raises(RuntimeError, match="duplicate"):
        V.check_lhb_coverage("20260101", "20260930")


def test_empty_cache_raises(cache):
    cache.write_text("代码,上榜日期,机构买入净额\n", encoding="utf-8")
    with pytest.raises(RuntimeError):
        V.check_lhb_coverage("20260101", "20260930")


def test_warn_mode_reports_without_raising(cache, monkeypatch):
    monkeypatch.setattr(V, "LHB_COVERAGE_GUARD", "warn")
    write_cache(cache, [("600000", "2026-07-31", 1e8)])
    problems = V.check_lhb_coverage("20260101", "20260930")
    assert problems and any("has NO flow data" in p for p in problems)


def test_off_mode_skips_the_check(cache, monkeypatch):
    monkeypatch.setattr(V, "LHB_COVERAGE_GUARD", "off")
    write_cache(cache, [])
    assert V.check_lhb_coverage("20260101", "20260930") == []


def test_coverage_reports_span_and_duplicates(cache):
    write_cache(cache, [("600000", "2026-01-05", 1e8),
                        ("600000", "2026-01-05", 1e8),
                        ("600001", "2026-09-30", 1e8)])
    cov = V.lhb_cache_coverage()
    assert cov["start"] == 20260105 and cov["end"] == 20260930
    assert cov["rows"] == 3 and cov["duplicates"] == 1
