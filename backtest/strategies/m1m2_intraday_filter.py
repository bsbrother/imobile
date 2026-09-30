"""M1 (auction) and M2 (late-session) features from historical 30-minute bars.

WHY 30-MINUTE BARS
Sina's `stock_zh_a_minute` returns a fixed 1970-bar window, so the granularity sets
how far back it reaches. Measured on this host:

    1-min   -> 2026-09-16 (9 sessions)      30-min  -> 2025-09-22 (247 sessions)
    5-min   -> 2026-07-31 (42 sessions)     60-min  -> 2024-09-18 (493 sessions)
    15-min  -> 2026-04-01 (124 sessions)

30-min is the finest granularity that covers the whole 2026 backtest range, and its
8 bars/session include both ends we need: the opening bar (09:30-10:00) and the
closing bar (14:30-15:00). EastMoney - the only source with true 1-minute and
09:15-09:25 pre-open data - refuses this host outright, and Tushare's `stk_auction_o`
is not permitted on this account.

WHAT THE FEATURES ARE (both read from the REFERENCE session = the closed session the
strategy already selects on, i.e. target_date - 1 trading day; never target_date):

    M1 auction_gap      open of the 09:30-10:00 bar / prior session close - 1
                        The 09:30 print IS the call-auction clearing price, so this
                        recovers the auction result - lagged one session, because a
                        historical feed cannot show *today's* 09:15-09:25 window
                        before the open.
    M1 auction_vol_share  volume of the opening bar / session volume
    M2 late_ret         close of the 14:30-15:00 bar / its open - 1
    M2 late_vol_share   volume of the closing bar / session volume

THE INTERVENTION - A VETO, NOT A RE-RANK
An earlier experiment re-ranked the pool by M3 leader score and cost 40.44pp
(117.08% vs 157.52%), because promoting names displaced better picks. So this only
*removes* names that show weakness and leaves the strategy's own ordering of the
survivors intact. A veto cannot reorder anything; it can only drop.

Failure policy: any error leaves the candidates untouched, and the veto is skipped
entirely if it would remove more than `max_drop_frac` of the pool - a data hiccup or
an over-broad rule must never empty the book.
"""

from __future__ import annotations

import os
import pickle
import time

import pandas as pd
from loguru import logger

_CACHE_DIR = os.path.join("shared", "data_cache", "m1m2_min30")
_CACHE_TTL_SECONDS = 12 * 3600
_FIRST_BAR = "10:00"   # spans 09:30-10:00; its open is the auction clearing price
_LAST_BAR = "15:00"    # spans 14:30-15:00


def _cache_path(ts_code: str, period: str) -> str:
    return os.path.join(_CACHE_DIR, f"{ts_code}_{period}.pkl")


def load_reference_bars(ts_code: str, period: str = "30"):
    """Whole 1970-bar window for a symbol, disk-cached (one Sina call per symbol, ever).

    The backtest spawns a fresh strategy subprocess per date, so an in-process cache
    would refetch every symbol on every date; this one persists across processes.
    """
    import akshare as ak

    path = _cache_path(ts_code, period)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < _CACHE_TTL_SECONDS:
        try:
            return pickle.load(open(path, "rb"))
        except Exception:
            pass

    code, mkt = ts_code.split(".") if "." in ts_code else (ts_code, "SH")
    sina_symbol = ("sh" if mkt.upper() == "SH" else "sz") + code
    try:
        raw = ak.stock_zh_a_minute(symbol=sina_symbol, period=period)
        if raw is None or raw.empty:
            return None
        raw = raw.copy()
        raw["day"] = pd.to_datetime(raw["day"])
        for c in ("open", "high", "low", "close", "volume"):
            raw[c] = pd.to_numeric(raw[c], errors="coerce")
        raw = raw.dropna(subset=["open", "close"])
        os.makedirs(_CACHE_DIR, exist_ok=True)
        pickle.dump(raw, open(path, "wb"))
        time.sleep(0.3)
        return raw
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"[m1m2] fetch failed for {ts_code}: {type(exc).__name__}")
        return None


def session_features(bars, ref_date: str) -> dict[str, float] | None:
    """M1 + M2 features for one session. Returns None when the session is missing.

    Args:
        bars: full frame from load_reference_bars (day/open/high/low/close/volume).
        ref_date: 'YYYYMMDD' of the closed reference session.
    """
    if bars is None or bars.empty:
        return None
    day = f"{ref_date[:4]}-{ref_date[4:6]}-{ref_date[6:]}"
    today = bars[bars["day"].dt.strftime("%Y-%m-%d") == day]
    if today.empty:
        return None

    clock = today["day"].dt.strftime("%H:%M")
    bar_open = today[clock == _FIRST_BAR]
    bar_last = today[clock == _LAST_BAR]
    if bar_open.empty or bar_last.empty:
        return None

    day_vol = float(today["volume"].sum())
    first, last = bar_open.iloc[0], bar_last.iloc[0]
    if not first["open"] or not last["open"] or not day_vol:
        return None

    # prior session close, for the auction gap
    prior = bars[bars["day"].dt.strftime("%Y-%m-%d") < day]
    prior_close = float(prior["close"].iloc[-1]) if not prior.empty else None

    feats = {
        "late_ret": float(last["close"]) / float(last["open"]) - 1.0,      # M2
        "late_vol_share": float(last["volume"]) / day_vol,                 # M2
        "auction_vol_share": float(first["volume"]) / day_vol,             # M1
    }
    if prior_close:
        feats["auction_gap"] = float(first["open"]) / prior_close - 1.0    # M1
    return feats


def _collect(df: pd.DataFrame, ref_date: str) -> dict[str, dict]:
    out = {}
    for ts_code in df["ts_code"]:
        if ts_code in out:
            continue
        f = session_features(load_reference_bars(ts_code), ref_date)
        if f:
            out[ts_code] = f
    return out


def regime_allows(ref_date: str, allowed: set[str] | None) -> bool:
    """Is `ref_date`'s detected regime in `allowed`?

    The regime is read for the CLOSED reference session, so it is known before the
    picks are acted on. Fails CLOSED: if the regime cannot be determined, the
    intervention is skipped rather than applied blind.
    """
    if not allowed:
        return True                      # no gate configured
    if not ref_date:
        return False
    try:
        from backtest.utils.market_regime import detect_market_regime

        regime = str(detect_market_regime(ref_date).get("regime", "")).lower()
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[m1m2] regime lookup failed for {ref_date}: "
                       f"{type(exc).__name__}: {str(exc)[:80]} - skipping intervention")
        return False
    allowed_hit = regime in allowed
    logger.info(f"[m1m2] {ref_date}: regime={regime} gate={'fire' if allowed_hit else 'skip'}")
    return allowed_hit


def apply_m1m2_rerank(
    df: pd.DataFrame,
    ref_date: str,
    features: dict[str, dict] | None = None,
    w_m1: float = 1.0,
    w_m2: float = 0.25,
) -> pd.DataFrame:
    """Reorder candidates by M1/M2 strength, preserving the pool size exactly.

    Why a re-rank here when an earlier M3 re-rank cost 40.44pp: that one promoted on
    an *anti*-predictive signal (money-flow). Here the signal is positively and
    monotonically predictive (auction_gap quartiles 0.655/0.913/2.223/3.589% mean trade
    return; corr +0.2933), so promoting the strong names is the point.

    Why not a veto: the pool runs 18-33 against position caps of 12/10/8/5. Dropping
    a large share leaves the book under-filled, and idle capital costs return
    regardless of how good the survivors are. Keeping the count fixed tests the signal
    itself rather than a position-sizing side effect.

    Weights follow the measured evidence (corr +0.2933 vs +0.0787, roughly 4:1). Both
    features are standardised *within the day's pool*, so the rule is relative to the
    day rather than to an absolute level that drifts across regimes.
    """
    if df is None or df.empty or "ts_code" not in df.columns:
        return df

    try:
        feats = features if features is not None else _collect(df, ref_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[m1m2] feature collection failed for {ref_date}: "
                       f"{type(exc).__name__}: {str(exc)[:100]} - order untouched")
        return df

    if not feats:
        logger.info(f"[m1m2] no features for {ref_date}; order untouched")
        return df

    m1 = pd.Series([(feats.get(c) or {}).get("auction_gap") for c in df["ts_code"]],
                   index=df.index, dtype="float64")
    m2 = pd.Series([(feats.get(c) or {}).get("late_ret") for c in df["ts_code"]],
                   index=df.index, dtype="float64")
    covered = int(m1.notna().sum())
    if covered == 0:
        logger.info(f"[m1m2] no M1 coverage for {ref_date}; order untouched")
        return df

    def z(col: pd.Series) -> pd.Series:
        vals = col.astype("float64")
        sd = float(vals.std())
        if sd == 0.0 or sd != sd:      # zero or NaN spread -> no information
            return vals * 0.0
        return (vals - float(vals.mean())) / sd

    score = w_m1 * z(m1).fillna(0.0) + w_m2 * z(m2).fillna(0.0)
    ordered = (
        df.assign(_s=score, _o=range(len(df)))
        .sort_values(["_s", "_o"], ascending=[False, True])
        .drop(columns=["_s", "_o"])
        .reset_index(drop=True)
    )
    if "rank" in ordered.columns:
        ordered["rank"] = range(1, len(ordered) + 1)

    moved = sum(1 for a, b in zip(ordered["ts_code"], df["ts_code"]) if a != b)
    logger.info(f"[m1m2] {ref_date}: re-ranked {len(ordered)} by M1/M2 "
                f"({covered} with auction data, {moved} positions changed)")
    return ordered


def apply_m1m2_veto(
    df: pd.DataFrame,
    ref_date: str,
    features: dict[str, dict] | None = None,
    late_ret_min: float = 0.0,
    auction_gap_min: float | None = -0.005,
    max_drop_frac: float = 0.6,
) -> pd.DataFrame:
    """Drop candidates showing auction or late-session weakness; never reorder.

    Args:
        df: candidate frame with `ts_code`.
        ref_date: the CLOSED reference session (target_date - 1). Never target_date.
        features: injectable ts_code -> feature dict, for tests.
        late_ret_min: drop when M2 late_ret is below this (late-session selling).
        auction_gap_min: drop when M1 auction_gap is below this; None disables M1.
        max_drop_frac: abort the veto if it would drop more than this share of the pool.
    """
    if df is None or df.empty or "ts_code" not in df.columns:
        return df

    try:
        feats = features if features is not None else _collect(df, ref_date)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"[m1m2] feature collection failed for {ref_date}: "
                       f"{type(exc).__name__}: {str(exc)[:100]} - candidates untouched")
        return df

    if not feats:
        logger.info(f"[m1m2] no features for {ref_date}; candidates untouched")
        return df

    drop_idx = []
    for i, ts_code in enumerate(df["ts_code"]):
        f = feats.get(ts_code)
        if not f:
            continue
        if f.get("late_ret") is not None and f["late_ret"] < late_ret_min:
            drop_idx.append(i)
        elif (auction_gap_min is not None and f.get("auction_gap") is not None
              and f["auction_gap"] < auction_gap_min):
            drop_idx.append(i)

    if not drop_idx:
        logger.info(f"[m1m2] {ref_date}: no candidate shows weakness; {len(df)} kept")
        return df

    if len(drop_idx) / len(df) > max_drop_frac:
        logger.warning(f"[m1m2] {ref_date}: veto would drop {len(drop_idx)}/{len(df)} "
                       f"(> {max_drop_frac:.0%}); keeping the pool intact")
        return df

    dropped = set(drop_idx)
    kept = df.iloc[[i for i in range(len(df)) if i not in dropped]].reset_index(drop=True)
    if "rank" in kept.columns:
        kept["rank"] = range(1, len(kept) + 1)
    logger.info(f"[m1m2] {ref_date}: vetoed {len(drop_idx)}/{len(df)} "
                f"(late or weak auction), {len(kept)} kept")
    return kept
