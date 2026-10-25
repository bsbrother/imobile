"""ts_flow_turning: the filler merge, and the wiring that makes it not-inert.

The strategy's whole point is that the flow picks survive untouched and the
turning-point picks only occupy slots the flow strategy left empty. Two failure
modes are worth pinning:

1. the merge reordering / overwriting base picks (it would silently change the
   68.42% baseline instead of adding to it), and
2. the wrapper being registered everywhere except where it matters — the review
   allowlist. That exact omission cost the `ts_switch_by_regime` wrapper ~35pp on
   a 2026 window (review adjustments went unapplied for 908 sessions).
"""
import re
from pathlib import Path

import pytest

from backtest.strategies.ts_flow_turning import merge_picks

REPO = Path(__file__).resolve().parent.parent


def _pick(sym, score, rank=1):
    return {"symbol": sym, "name": sym, "score": score, "rank": rank}


BASE = [_pick("600000.SH", 80.0, 1), _pick("600001.SH", 70.0, 2), _pick("600002.SH", 60.0, 3)]


def test_base_picks_are_untouched_when_slots_are_full():
    extras = [_pick("000001.SZ", 99.0)]
    assert merge_picks(BASE, extras, limit=3, extra_max=2) == BASE


def test_extras_fill_only_the_free_slots():
    extras = [_pick(s, 90.0) for s in ("000001.SZ", "000002.SZ", "000003.SZ")]
    out = merge_picks(BASE, extras, limit=4, extra_max=5)
    assert [p["symbol"] for p in out][:3] == [p["symbol"] for p in BASE]
    assert len(out) == 4                      # one free slot, one extra


def test_extra_max_caps_the_filler():
    extras = [_pick(s, 90.0) for s in ("000001.SZ", "000002.SZ", "000003.SZ")]
    out = merge_picks(BASE, extras, limit=12, extra_max=2)
    assert len(out) == 5


def test_extras_never_duplicate_a_base_symbol():
    extras = [_pick("600000.SH", 90.0), _pick("000001.SZ", 90.0)]
    out = merge_picks(BASE, extras, limit=12, extra_max=2)
    syms = [p["symbol"] for p in out]
    assert syms.count("600000.SH") == 1
    assert "000001.SZ" in syms


def test_extra_scores_rank_below_every_base_pick():
    extras = [_pick("000001.SZ", 999.0), _pick("000002.SZ", 999.0)]
    out = merge_picks(BASE, extras, limit=12, extra_max=2)
    base_min = min(p["score"] for p in out[:3])
    for p in out[3:]:
        assert 0 < p["score"] < base_min, f"extra outranks a base pick: {p}"


def test_ranks_continue_the_sequence():
    extras = [_pick("000001.SZ", 90.0), _pick("000002.SZ", 90.0)]
    out = merge_picks(BASE, extras, limit=12, extra_max=2)
    assert [p["rank"] for p in out] == [1, 2, 3, 4, 5]


def test_zero_extra_max_disables_the_merge():
    assert merge_picks(BASE, [_pick("000001.SZ", 90.0)], limit=12, extra_max=0) == BASE


def test_empty_inputs_are_safe():
    assert merge_picks([], [], limit=12, extra_max=2) == []
    assert [p["symbol"] for p in merge_picks([], [_pick("000001.SZ", 1.0)], 12, 2)] == ["000001.SZ"]


# ── wiring ────────────────────────────────────────────────────
# Source-level on purpose: engine.py cannot be imported without a data stack, and
# the failure being guarded is a missing registration line, not logic.

def _engine_src() -> str:
    return (REPO / "backtest" / "engine.py").read_text(encoding="utf-8")


def test_registered_in_the_dispatch_table():
    assert "'ts_flow_turning':" in _engine_src(), "not in _STRATEGY_SCRIPTS — engine cannot run it"


def test_listed_as_a_valid_source():
    src = _engine_src()
    assert re.search(r"_valid_sources\s*=\s*\[[^\]]*'ts_flow_turning'", src, re.S), \
        "not in _valid_sources — argparse rejects the src"


def test_in_the_review_allowlist():
    """It delegates to a review strategy; without this the review never applies."""
    src = _engine_src()
    block = re.search(r"if src in \(([^)]*)\)", src[src.index("review_adjustments"):], re.S)
    assert block and "'ts_flow_turning'" in block.group(1), \
        "missing from the review allowlist — every review adjustment silently skipped"


def test_delegates_resolve_to_real_scripts():
    from backtest.strategies import ts_flow_turning as mod
    for name, path in mod.DELEGATE_SCRIPTS.items():
        assert (REPO / path).exists(), f"{name} -> {path} does not exist"


@pytest.mark.parametrize("regime,expected", [("bull", 12), ("normal", 10), ("volatile", 8), ("bear", 5)])
def test_regime_limits_match_the_engine_defaults(regime, expected):
    from backtest.strategies import ts_flow_turning as mod
    assert mod.REGIME_LIMITS[regime] == expected


# ── replace mode ──────────────────────────────────────────────
# The flow picker emits 23-27 names for a 12-slot budget, so in `fill` mode the
# extras queue behind picks that will never be funded. `replace` gives them the
# last slots by dropping the base tail.

def test_replace_keeps_the_base_head_and_drops_the_tail():
    from backtest.strategies.ts_flow_turning import merge_replace
    base = [_pick(f"6000{i:02d}.SH", 80.0 - i, i + 1) for i in range(20)]
    extras = [_pick("000001.SZ", 90.0), _pick("000002.SZ", 90.0)]
    out = merge_replace(base, extras, limit=12, extra_max=2)
    assert [p["symbol"] for p in out[:10]] == [p["symbol"] for p in base[:10]]
    assert [p["symbol"] for p in out[10:]] == ["000001.SZ", "000002.SZ"]
    assert len(out) == 12


def test_replace_never_duplicates_a_kept_base_symbol():
    from backtest.strategies.ts_flow_turning import merge_replace
    extras = [_pick("600000.SH", 90.0), _pick("000001.SZ", 90.0)]
    out = merge_replace(BASE, extras, limit=3, extra_max=2)
    syms = [p["symbol"] for p in out]
    assert syms.count("600000.SH") == 1 and "000001.SZ" in syms


def test_replace_is_a_noop_without_extras_or_budget():
    from backtest.strategies.ts_flow_turning import merge_replace
    assert merge_replace(BASE, [], limit=12, extra_max=2) == BASE
    assert merge_replace(BASE, [_pick("000001.SZ", 90.0)], 12, 0) == BASE


# ── insert mode ───────────────────────────────────────────────
# Funding follows LIST ORDER (`cli.py` never reads `score` when sizing) and cash runs
# out before the end of the list, so the only way an extra is actually bought is to
# put it at the funding frontier. These pin that placement.

def test_insert_puts_extras_at_the_requested_rank():
    from backtest.strategies.ts_flow_turning import merge_insert
    base = [_pick(f"6000{i:02d}.SH", 80.0 - i) for i in range(12)]
    extras = [_pick("000001.SZ", 99.0), _pick("000002.SZ", 99.0)]
    out = merge_insert(base, extras, limit=12, extra_max=2, at=7)
    syms = [p["symbol"] for p in out]
    assert syms[:6] == [p["symbol"] for p in base[:6]], "base head must be untouched"
    assert syms[6:8] == ["000001.SZ", "000002.SZ"], "extras must land at rank 7"
    assert syms[8:] == [p["symbol"] for p in base[6:]], "base continues after the extras"


def test_insert_leaves_the_truncation_to_the_engine():
    """The engine's slot budget is review-adjusted; truncating here would strip base
    names the control arm still funds, so the A/B would measure the truncation."""
    from backtest.strategies.ts_flow_turning import merge_insert
    base = [_pick(f"6000{i:02d}.SH", 80.0 - i) for i in range(30)]
    extras = [_pick("000001.SZ", 99.0), _pick("000002.SZ", 99.0)]
    out = merge_insert(base, extras, limit=12, extra_max=2, at=7)
    assert len(out) == 32, "every base name must survive the merge"
    assert [p["symbol"] for p in out][:8] == (
        [p["symbol"] for p in base[:6]] + ["000001.SZ", "000002.SZ"])
    assert out[-1]["symbol"] == base[-1]["symbol"], "the base tail must still be there"


def test_insert_never_duplicates_anything_in_the_base():
    from backtest.strategies.ts_flow_turning import merge_insert
    base = [_pick(f"6000{i:02d}.SH", 80.0 - i) for i in range(12)]
    # the second extra collides with a TAIL name, which replace mode would re-admit
    extras = [_pick("000001.SZ", 99.0), _pick("600010.SH", 99.0)]
    out = merge_insert(base, extras, limit=12, extra_max=2, at=7)
    syms = [p["symbol"] for p in out]
    assert syms.count("600010.SH") == 1
    assert "000001.SZ" in syms


def test_insert_clamps_the_position_into_the_budget():
    from backtest.strategies.ts_flow_turning import merge_insert
    extras = [_pick("000001.SZ", 99.0)]
    assert merge_insert(BASE, extras, limit=3, extra_max=1, at=99)[2]["symbol"] == "000001.SZ"
    assert merge_insert(BASE, extras, limit=3, extra_max=1, at=0)[0]["symbol"] == "000001.SZ"


def test_insert_is_a_noop_without_extras_or_budget():
    from backtest.strategies.ts_flow_turning import merge_insert
    assert merge_insert(BASE, [], 12, 2, 7) == BASE
    assert merge_insert(BASE, [_pick("000001.SZ", 90.0)], 12, 0, 7) == BASE


def test_extra_pass_is_skipped_when_fill_has_no_free_slot():
    """Running the extra picker costs ~20s/session; in fill mode with a full base it
    can only be discarded. Measured: base >= limit on 134 of 183 2026 sessions."""
    from backtest.strategies.ts_flow_turning import wants_extras
    assert wants_extras("fill", 23, 12, 2) is False
    assert wants_extras("fill", 11, 12, 2) is True
    assert wants_extras("insert", 23, 12, 2) is True
    assert wants_extras("replace", 23, 12, 2) is True
    assert wants_extras("insert", 23, 12, 0) is False      # disabled


def test_insert_on_a_short_base_appends_after_it():
    from backtest.strategies.ts_flow_turning import merge_insert
    out = merge_insert(BASE, [_pick("000001.SZ", 90.0)], limit=12, extra_max=2, at=7)
    assert [p["symbol"] for p in out] == [p["symbol"] for p in BASE] + ["000001.SZ"]
