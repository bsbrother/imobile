"""M3 — a leader ranking that never counts limit-ups.

The sector-momentum picker in `ts_ths_dc` decides leadership with
`consecutive_limit_up` / `limit_up_score`: a name leads because it has strung
together the most boards. That conflates "has already gone up a lot" with "is
being bought right now", and it goes blind on days when nothing is near the
board — which is most days.

This module ranks on demand-side evidence instead, which needs no limit-up count
at all: money flow, extra-large order flow, App attention, and LHB institutional
seats. Every input is optional. A signal that is missing for a stock is left out
of that stock's average rather than scored as zero, so a stock is never punished
for a source simply not covering it, and a thin day degrades to the signals that
do exist.

Usage:
    signals = provider.get_leader_signals("20260928")
    ranked = rank_leaders_no_limit_up(signals, top_n=20)
"""
from __future__ import annotations

import pandas as pd

# Column -> weight, all demand-side or attention measures. None is a limit-up
# count. Percentile scoring keeps them comparable without relying on the units
# each Tushare interface happens to return.
DEFAULT_WEIGHTS: dict[str, float] = {
    "mf_net_amount_rate": 1.0,      # 主力净流入 as % of turnover
    "mf_buy_elg_amount_rate": 0.8,  # extra-large order inflow, as % of turnover
    "hot_rank": 0.7,                # 同花顺 App attention (inverted: rank 1 is best)
    "inst_net_buy": 0.6,            # LHB institutional seat net buy
    "lhb_net_rate": 0.5,            # LHB net as % of turnover
}

# Columns where a SMALLER number is better.
_INVERTED = {"hot_rank"}

# Any column matching these is a limit-up count, and is refused outright. This
# guard is the reason the module can be trusted to do what its name says: it
# cannot quietly drift back into ranking on the thing it exists to avoid.
_FORBIDDEN_PATTERNS = ("limit_up", "limitup", "连板", "涨停数", "consecutive_limit")


class LimitUpCountError(ValueError):
    """Raised when a caller tries to rank on a limit-up-derived column."""


def _assert_no_limit_up_columns(signals: pd.DataFrame) -> None:
    """Refuse a frame carrying limit-up-derived columns."""
    offenders = [
        column
        for column in signals.columns
        if any(pattern in str(column).lower() for pattern in _FORBIDDEN_PATTERNS)
    ]
    if offenders:
        raise LimitUpCountError(
            f"rank_leaders_no_limit_up refuses limit-up-derived columns: {offenders}. "
            "M3 exists precisely to rank without them — pass a frame from "
            "get_leader_signals(), or drop those columns first."
        )


def _numeric(series: pd.Series | pd.DataFrame) -> pd.Series:
    """`pd.to_numeric` is typed as returning a union; pin it to a Series.

    Without this, every chained `.notna()` / `.rank()` / `.fillna()` below yields one
    pyright error per member of that union, which buries any real problem in noise.
    A duplicated column label makes `frame[label]` a DataFrame, so flatten that first
    rather than handing it downstream and failing somewhere less obvious.
    """
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    converted = pd.to_numeric(series, errors="coerce")
    if isinstance(converted, pd.Series):
        return converted
    return pd.Series(converted, index=series.index)


def _pct_score(series: pd.Series | pd.DataFrame, invert: bool = False) -> pd.Series:
    """Percentile-rank a series into [0, 1]; NaN stays NaN.

    Missing values are preserved rather than imputed, so "no data" never gets
    scored as "worst" and drags a stock down for a source that simply does not
    cover it.
    """
    numeric = _numeric(series)
    if numeric.notna().sum() == 0:
        return pd.Series(float("nan"), index=series.index, dtype="float64")
    scored = numeric.rank(pct=True, ascending=not invert)
    return scored.where(numeric.notna())


def rank_leaders_no_limit_up(
    signals: pd.DataFrame,
    weights: dict[str, float] | None = None,
    top_n: int | None = None,
    min_signals: int = 1,
) -> pd.DataFrame:
    """Rank stocks as leaders on demand-side evidence alone.

    Args:
        signals: one row per ts_code, e.g. from `TushareDataProvider.get_leader_signals()`.
        weights: column -> weight. Defaults to DEFAULT_WEIGHTS. Columns absent from
            `signals` are skipped for every stock, so a source being missing cannot
            silently become a zero score.
        top_n: keep only the first N rows of the result.
        min_signals: a stock must have at least this many non-null signals to be
            ranked. Guards against a name present in one thin source topping the
            table on a single fluke.
    Returns:
        `leader_rank`, `ts_code`, `leader_score`, `signals_used`, the contributing
        signals, and one `score_<column>` per signal — sorted by score, descending.
        Rows with fewer than `min_signals` signals are dropped.
    """
    if signals is None or signals.empty:
        return pd.DataFrame(columns=["leader_rank", "ts_code", "leader_score"])  # type: ignore[arg-type]
    if "ts_code" not in signals.columns:
        raise ValueError("signals must contain a ts_code column")

    _assert_no_limit_up_columns(signals)

    weights = dict(DEFAULT_WEIGHTS if weights is None else weights)
    active = [
        (column, weight)
        for column, weight in weights.items()
        if weight and column in signals.columns
    ]
    if not active:
        raise ValueError(
            "none of the requested signals are present in the frame; "
            f"looked for {sorted(weights)}"
        )

    used: list[str] = []
    score_columns: dict[str, pd.Series] = {}
    for column, _ in active:
        score = _pct_score(signals[column], invert=column in _INVERTED)
        if score.notna().sum() == 0:
            # Column exists but is entirely empty for this date: contributing it
            # would add a weight it can never earn.
            continue
        used.append(column)
        score_columns[f"score_{column}"] = score

    if not used:
        raise ValueError("every requested signal is empty for this date; nothing to rank")

    present = pd.concat(
        [_numeric(signals[c]).notna() for c in used], axis=1
    ).sum(axis=1)

    # Weighted mean over the signals each stock actually has, so sparse coverage
    # neither fabricates a zero nor is rewarded as if it were agreement. Missing
    # scores are zero-filled in the numerator *only* because the denominator stops
    # counting that weight too; leaving the NaN in place would poison the entire
    # sum, since adding Series propagates NaN.
    weights_by_column = dict(active)
    numerator = pd.Series(0.0, index=signals.index)
    denominator = pd.Series(0.0, index=signals.index)
    for column in used:
        weight = weights_by_column[column]
        numerator = numerator + score_columns[f"score_{column}"].fillna(0) * weight
        denominator = denominator + _numeric(signals[column]).notna() * weight

    out = pd.DataFrame({
        "ts_code": signals["ts_code"].values,
        "leader_score": (numerator / denominator).values,
        "signals_used": present.values,
    })

    for column in ("hot_rank", "concept", "hot", "mf_net_amount_rate",
                   "lhb_net_amount", "inst_net_buy"):
        if column in signals.columns:
            out[column] = signals[column].values
    for name, score in score_columns.items():
        out[name] = score.values

    out = out[out["signals_used"] >= min_signals]
    out = out.sort_values("leader_score", ascending=False).reset_index(drop=True)  # type: ignore[call-overload]
    out.insert(0, "leader_rank", range(1, len(out) + 1))  # type: ignore[arg-type]
    if top_n:
        out = out.head(top_n).reset_index(drop=True)
    return out
