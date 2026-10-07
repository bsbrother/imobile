"""Bar + previous-close loading for the one-day trading simulation.

DATA PROVENANCE (inspected 2026-10-01)
--------------------------------------
Intraday history on this box comes from one cache:

    shared/data_cache/m1m2_min30/<CODE>.pkl        e.g. 603268.SH.pkl
    shared/data_cache/m1m2_min30/<CODE>_30.pkl     e.g. 603268.SH_30.pkl

Both variants describe the same instruments; coverage differs per code (600000.SH has only
the `_30` file, 603268.SH has both). The cache was measured earlier in this project as
2025-09-22 .. 2026-09-29 at 8 bars/day, i.e. 30-minute bars: 09:30-10:00, ..., 11:00-11:30,
13:00-13:30, ..., 14:30-15:00. So the finest intraday granularity available for 20260901 is
30 minutes, NOT 1 minute, and the 09:15-09:25 call auction is NOT in the data at all.

There is no reachable 1-minute or auction-tick source for this date: EastMoney is blocked from
this host, Tushare's `stk_auction_o` needs a permission this token lacks, and TDX
`get_security_bars` errors out. Rather than pretend otherwise:

  * `load_intraday` returns whatever granularity the cache holds and reports how many bars it
    found, per code, so a run can never silently claim minute precision it does not have.
  * the auction phase is simulated from the previous close (see `load_prev_close`) and the
    auction *fill* is the first bar's open at 09:30 — which is what the auction clears at.

Parsing is deliberately tolerant (DatetimeIndex or a date column, `datetime`/`trade_time`,
`vol`/`volume`) because the pickle schema was not re-verified in this session: the project
venv probe that would have confirmed it needs your approval to run. Every unparseable file is
reported by name instead of being skipped quietly.
"""
from __future__ import annotations

import os
from typing import Any

def _find_repo_root() -> str:
    """Walk up until the directory that holds both `backtest/` and `trading/`.

    Moving this package up a level silently broke a fixed `dirname()` count — the run found no
    cache, no picks and no account and reported an empty, all-zero month. Anchor on the layout.
    """
    d = os.path.dirname(os.path.abspath(__file__))
    while d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, "backtest")) and os.path.isdir(os.path.join(d, "trading")):
            return d
        d = os.path.dirname(d)
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


REPO_ROOT = _find_repo_root()
CACHE_DIR = os.path.join(REPO_ROOT, "shared", "data_cache", "m1m2_min30")

_OPEN_KEYS = ("open", "o")
_HIGH_KEYS = ("high", "h")
_LOW_KEYS = ("low", "l")
_CLOSE_KEYS = ("close", "c")
_VOL_KEYS = ("volume", "vol", "v")
_TIME_KEYS = ("datetime", "trade_time", "date", "time", "day")


def _bars_per_day(df) -> int:
    """Median number of rows per calendar day — lets us report granularity honestly."""
    try:
        days = df.index.normalize() if hasattr(df.index, "normalize") else None
        if days is not None:
            counts = df.groupby(days).size()
            return int(counts.median()) if len(counts) else 0
    except Exception:
        pass
    return 0


def _rows_to_bars(df, code: str) -> list[dict[str, Any]]:
    """DataFrame -> list of {dt, open, high, low, close, volume}, time-sorted."""
    cols = {str(c).lower(): c for c in df.columns}

    def pick(keys):
        for k in keys:
            if k in cols:
                return cols[k]
        return None

    o, h, lo, c = (pick(_OPEN_KEYS), pick(_HIGH_KEYS), pick(_LOW_KEYS), pick(_CLOSE_KEYS))
    v = pick(_VOL_KEYS)
    tcol = pick(_TIME_KEYS)
    if any(x is None for x in (o, h, lo, c)):
        raise ValueError(f"{code}: cannot find OHLC columns in {list(df.columns)}")

    times = None
    if hasattr(df.index, "to_pydatetime"):
        try:
            times = list(df.index.to_pydatetime())
        except Exception:
            times = None
    if times is None and tcol is not None:
        times = list(df[tcol])
    if times is None:
        raise ValueError(f"{code}: no usable time index or column")

    bars = []
    for i, ts in enumerate(times):
        row = df.iloc[i]
        bars.append({
            "dt": str(ts),
            "open": float(row[o]), "high": float(row[h]),
            "low": float(row[lo]), "close": float(row[c]),
            "volume": float(row[v]) if v is not None else 0.0,
        })
    bars.sort(key=lambda b: b["dt"])
    return bars


def load_intraday(code: str, date: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Bars for `code` on `date` (YYYYMMDD).

    Returns (bars, info). `bars` is empty when the code or date is absent — check `info`
    rather than assuming a working day has data.
    """
    import pandas as pd  # local import: the module must be importable without pandas

    info: dict[str, Any] = {"code": code, "date": date, "file": None, "bars_per_day": 0,
                            "error": None, "variants": []}
    candidates = [os.path.join(CACHE_DIR, f"{code}.pkl"), os.path.join(CACHE_DIR, f"{code}_30.pkl")]
    info["variants"] = [os.path.basename(p) for p in candidates if os.path.exists(p)]
    if not info["variants"]:
        info["error"] = f"no cache file for {code}"
        return [], info

    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            df = pd.read_pickle(path)
            if df is None or len(df) == 0:
                continue
            info["bars_per_day"] = max(info["bars_per_day"], _bars_per_day(df))
            bars = _rows_to_bars(df, code)
            day_prefix = f"{date[:4]}-{date[4:6]}-{date[6:]}"
            sel = [b for b in bars if b["dt"].startswith(day_prefix)]
            if sel:
                info["file"] = os.path.basename(path)
                if info["bars_per_day"] == 0:
                    info["bars_per_day"] = len(sel)
                return sel, info
            if info["file"] is None:
                info["file"] = os.path.basename(path)   # found the file, day absent
                info["error"] = f"no bars for {date} in {os.path.basename(path)}"
        except Exception as e:  # noqa: BLE001 - report, never swallow
            info["error"] = f"{os.path.basename(path)}: {type(e).__name__}: {e}"
    return [], info


def load_all(code: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The whole cached series for `code`, preferring the finer variant when both exist."""
    import pandas as pd

    info: dict[str, Any] = {"code": code, "file": None, "bars_per_day": 0, "error": None,
                            "variants": []}
    candidates = [os.path.join(CACHE_DIR, f"{code}.pkl"), os.path.join(CACHE_DIR, f"{code}_30.pkl")]
    info["variants"] = [os.path.basename(p) for p in candidates if os.path.exists(p)]
    if not info["variants"]:
        info["error"] = f"no cache file for {code}"
        return [], info

    best: tuple[list[dict[str, Any]], str, int] | None = None
    for path in candidates:
        if not os.path.exists(path):
            continue
        try:
            bars = _rows_to_bars(pd.read_pickle(path), code)
            if not bars:
                continue
            per_day = _rows_per_day(bars)
            if best is None or per_day > best[2]:
                best = (bars, os.path.basename(path), per_day)
        except Exception as e:  # noqa: BLE001
            info["error"] = f"{os.path.basename(path)}: {type(e).__name__}: {e}"
    if best is None:
        return [], info
    info["file"], info["bars_per_day"] = best[1], best[2]
    return best[0], info


def _rows_per_day(bars: list[dict[str, Any]]) -> int:
    days: dict[str, int] = {}
    for b in bars:
        days[b["dt"][:10]] = days.get(b["dt"][:10], 0) + 1
    if not days:
        return 0
    counts = sorted(days.values())
    return counts[len(counts) // 2]


class BarStore:
    """Loads each code's series ONCE and serves day slices + previous closes from memory.

    A month-long run touches the same codes on every date; re-reading the pickle per day per code
    is what turns a 30-second job into minutes. Failures are kept and reported, never hidden.
    """

    def __init__(self, verbose: bool = False):
        self._series: dict[str, list[dict[str, Any]]] = {}
        self._info: dict[str, dict[str, Any]] = {}
        self._missing: dict[str, str] = {}
        self.verbose = verbose

    def _ensure(self, code: str) -> None:
        if code in self._series or code in self._missing:
            return
        bars, info = load_all(code)
        self._info[code] = info
        if bars:
            self._series[code] = bars
        else:
            self._missing[code] = info.get("error") or "no bars"

    def has(self, code: str) -> bool:
        self._ensure(code)
        return code in self._series

    def bars_per_day(self, code: str) -> int:
        self._ensure(code)
        return int(self._info.get(code, {}).get("bars_per_day") or 0)

    def intraday(self, code: str, date: str) -> list[dict[str, Any]]:
        self._ensure(code)
        prefix = f"{date[:4]}-{date[4:6]}-{date[6:]}"
        return [b for b in self._series.get(code, []) if b["dt"].startswith(prefix)]

    def prev_close(self, code: str, base_date: str) -> float | None:
        self._ensure(code)
        prefix = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:]}"
        prior = [b for b in self._series.get(code, []) if b["dt"][:10] <= prefix]
        return prior[-1]["close"] if prior else None

    def last_date(self, code: str) -> str:
        self._ensure(code)
        series = self._series.get(code) or []
        return series[-1]["dt"][:10] if series else ""

    def diagnostics(self) -> dict[str, Any]:
        return {"loaded": len(self._series), "missing": dict(self._missing),
                "bars_per_day": {c: self._info.get(c, {}).get("bars_per_day", 0) for c in self._series},
                "files": {c: self._info.get(c, {}).get("file") for c in self._series}}


def load_prev_close(code: str, base_date: str, intraday_by_code: dict[str, list] | None = None
                    ) -> tuple[float | None, dict[str, Any]]:
    """Last close on or before `base_date` — the pre-market reference price.

    Reuses already-loaded bars when handed `intraday_by_code`, otherwise reads the cache.
    """
    import pandas as pd

    info: dict[str, Any] = {"code": code, "base_date": base_date, "source": None, "error": None}
    prefix = f"{base_date[:4]}-{base_date[4:6]}-{base_date[6:]}"

    if intraday_by_code and intraday_by_code.get(code):
        prior = [b for b in intraday_by_code[code] if b["dt"][:10] <= prefix]
        if prior:
            info["source"] = "supplied bars"
            return prior[-1]["close"], info

    path = os.path.join(CACHE_DIR, f"{code}_30.pkl")
    if not os.path.exists(path):
        path = os.path.join(CACHE_DIR, f"{code}.pkl")
    if not os.path.exists(path):
        info["error"] = f"no cache file for {code}"
        return None, info
    try:
        bars = _rows_to_bars(pd.read_pickle(path), code)
        prior = [b for b in bars if b["dt"][:10] <= prefix]
        if not prior:
            info["error"] = f"no bars on or before {base_date}"
            return None, info
        info["source"] = f"{os.path.basename(path)} @ {prior[-1]['dt']}"
        return prior[-1]["close"], info
    except Exception as e:  # noqa: BLE001
        info["error"] = f"{type(e).__name__}: {e}"
        return None, info
