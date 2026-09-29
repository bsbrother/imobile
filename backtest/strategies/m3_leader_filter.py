"""M3 leader re-rank for the ts_7AZ_96MA_flow family.

Reorders the day's candidates so names with strong demand-side evidence come
first. It changes *priority only* — the pool is never enlarged or shrunk — which
keeps an A/B honest: the same universe, the same count, a different order.

Why order is the lever: `engine.py` takes `selected_stocks[:MAX_POSITIONS]` in
file order, so whatever sits at the front of the list is what gets bought. The
position cap is 12/10/8/5 by regime while the candidate pool runs 0-12 (median
10), so this reorder has real effect exactly when the pool exceeds the cap —
mostly normal/bear days, not bull.

NO LOOK-AHEAD. This uses the *reference* date the caller passes, which the review
strategy sets to `target_date - 1` trading day, i.e. the last closed session
before the picks are acted on. Its inputs (`moneyflow_dc`, `ths_hot`, `top_list`,
`top_inst`) are end-of-day data for that closed session. It must never be handed
`target_date`.

Failure policy: any error here leaves the candidate frame exactly as it was. A
data hiccup must not silently reshape a backtest.
"""

from __future__ import annotations

import os

import pandas as pd
from loguru import logger

from backtest.utils.leader_rank import rank_leaders_no_limit_up


def _leader_scores(ref_date: str) -> dict[str, float]:
    """ts_code -> leader_score for a closed session. Empty dict if unavailable."""
    token = os.getenv('TUSHARE_TOKEN')
    if not token:
        logger.warning("[m3_leader] no TUSHARE_TOKEN; skipping M3 re-rank")
        return {}

    from backtest.data.provider import TushareDataProvider

    provider = TushareDataProvider(token=token)
    signals = provider.get_leader_signals(ref_date)
    if signals is None or signals.empty:
        logger.warning(f"[m3_leader] no leader signals for {ref_date}")
        return {}

    ranked = rank_leaders_no_limit_up(signals)
    return dict(zip(ranked["ts_code"], ranked["leader_score"]))


def apply_m3_leader_rerank(
    df: pd.DataFrame,
    ref_date: str,
    scores: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Move candidates with a higher M3 leader score to the front of the list.

    Args:
        df: candidate frame with at least `ts_code`. Carries `rank`/`score` by convention.
        ref_date: the CLOSED session to read signals from (target_date - 1). Never pass
            the date the picks will be traded on.
        scores: injectable ts_code -> leader_score map, for tests.
    Returns:
        The same frame, reordered (and `rank` renumbered). Unchanged on any failure,
        and unchanged when no candidate has a score.
    """
    if df is None or df.empty or "ts_code" not in df.columns:
        return df

    try:
        if scores is None:
            scores = _leader_scores(ref_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[m3_leader] signal fetch failed for {ref_date}: "
                       f"{type(exc).__name__}: {str(exc)[:100]} — leaving order untouched")
        return df

    if not scores:
        return df

    scored = df["ts_code"].map(lambda code: scores.get(code))
    covered = int(scored.notna().sum())
    if covered == 0:
        logger.info(f"[m3_leader] none of {len(df)} candidates have signals for {ref_date}; "
                    "order unchanged")
        return df

    # Score descending; uncovered candidates keep their original relative order
    # AFTER the covered ones rather than being dropped, so the pool size is stable.
    ordered = (
        df.assign(_m3=scored, _orig=range(len(df)))
        .sort_values(["_m3", "_orig"], ascending=[False, True], na_position="last")
        .drop(columns=["_m3", "_orig"])
        .reset_index(drop=True)
    )

    if "rank" in ordered.columns:
        ordered["rank"] = range(1, len(ordered) + 1)

    moved = sum(1 for a, b in zip(ordered["ts_code"], df["ts_code"]) if a != b)
    logger.info(f"[m3_leader] {ref_date}: re-ranked {len(ordered)} candidates "
                f"({covered} with signals, {moved} positions changed)")
    return ordered
