"""Tests for the M3 leader re-rank.

The properties that matter: ordering changes, pool size never does, and any
failure leaves the candidate list exactly as it was.
"""

import pandas as pd

from backtest.strategies.m3_leader_filter import apply_m3_leader_rerank

_BASE = ["A.SZ", "B.SZ", "C.SZ", "D.SZ"]


def _candidates() -> pd.DataFrame:
    return pd.DataFrame({
        "rank": [1, 2, 3, 4],
        "ts_code": _BASE,
        "score": [9.0, 8.0, 7.0, 6.0],
    })


def test_scores_reorder_the_list_highest_first():
    # D is last in the original order but the strongest leader.
    scores = {"D.SZ": 0.9, "A.SZ": 0.5, "C.SZ": 0.2}
    out = apply_m3_leader_rerank(_candidates(), "20260928", scores=scores)

    assert list(out["ts_code"]) == ["D.SZ", "A.SZ", "C.SZ", "B.SZ"], (
        "covered candidates must sort by score desc, uncovered stay last"
    )


def test_pool_size_is_never_changed():
    """A re-rank must not drop a candidate: that would change the universe, not the priority."""
    out = apply_m3_leader_rerank(_candidates(), "20260928", scores={"D.SZ": 0.9})
    assert len(out) == len(_BASE)
    assert set(out["ts_code"]) == set(_BASE)


def test_uncovered_candidates_keep_their_original_relative_order():
    scores = {"D.SZ": 0.9}  # A, B, C uncovered
    out = apply_m3_leader_rerank(_candidates(), "20260928", scores=scores)
    assert list(out["ts_code"]) == ["D.SZ", "A.SZ", "B.SZ", "C.SZ"]


def test_rank_column_is_renumbered():
    out = apply_m3_leader_rerank(_candidates(), "20260928", scores={"D.SZ": 0.9})
    assert list(out["rank"]) == [1, 2, 3, 4]
    assert out.iloc[0]["ts_code"] == "D.SZ"


def test_other_columns_survive_the_reorder():
    out = apply_m3_leader_rerank(_candidates(), "20260928", scores={"D.SZ": 0.9})
    assert list(out.columns) == ["rank", "ts_code", "score"]
    assert out.iloc[0]["score"] == 6.0, "the score must travel with its row"


def test_no_scores_leaves_the_order_untouched():
    original = _candidates()
    out = apply_m3_leader_rerank(original, "20260928", scores={})
    assert list(out["ts_code"]) == list(original["ts_code"])


def test_fetch_failure_leaves_the_order_untouched(monkeypatch):
    """A Tushare hiccup must not silently reshape a backtest."""
    def boom(ref_date):
        raise ConnectionError("tushare down")

    monkeypatch.setattr("backtest.strategies.m3_leader_filter._leader_scores", boom)
    original = _candidates()
    out = apply_m3_leader_rerank(original, "20260928")  # scores=None -> fetches -> raises
    assert list(out["ts_code"]) == list(original["ts_code"])


def test_empty_or_malformed_frames_are_passed_through():
    assert apply_m3_leader_rerank(pd.DataFrame(), "20260928", scores={"A.SZ": 1.0}).empty
    no_code = pd.DataFrame({"rank": [1], "score": [1.0]})
    out = apply_m3_leader_rerank(no_code, "20260928", scores={"A.SZ": 1.0})
    assert list(out.columns) == ["rank", "score"]


def test_covered_count_logged_frame_still_valid():
    """All candidates covered: pure score ordering."""
    scores = {"A.SZ": 0.1, "B.SZ": 0.4, "C.SZ": 0.3, "D.SZ": 0.2}
    out = apply_m3_leader_rerank(_candidates(), "20260928", scores=scores)
    assert list(out["ts_code"]) == ["B.SZ", "C.SZ", "D.SZ", "A.SZ"]
