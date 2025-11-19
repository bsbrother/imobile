"""
Integration test: Show current A-shares market popular sector Top 5.

Priority data source:
- TuShare `ths_daily` (requires env var TUSHARE_TOKEN). Falls back to
    the most recent trading day within the last 10 days if today's data is empty.
- Fallback: AkShare Eastmoney board list (no token) using
  `stock_board_industry_name_em` sorted by today's percentage change.

Run locally (will print a small table):
    pytest -q tests/test_popular_sector.py -s

Notes:
- This test accesses public web APIs; if the network is unavailable or the
  provider rate-limits, the test will be skipped.
- For TuShare, export your token first: export TUSHARE_TOKEN=xxx
"""

from __future__ import annotations

import os
import datetime as dt
from typing import Optional, Any
from pathlib import Path
from datetime import datetime

import pandas as pd
import pytest
from dotenv import load_dotenv

import tushare as ts  # type: ignore
import akshare as ak  # type: ignore


def _load_token_from_env() -> None:
    """Ensure .env is loaded explicitly from project root."""

    project_root = Path(__file__).resolve().parents[1]
    dotenv_path = project_root / ".env"
    if dotenv_path.exists():
        load_dotenv(dotenv_path=dotenv_path)

_load_token_from_env()

def _fmt_pct(x: Any) -> str:
    if x is None:
        return "-"
    try:
        return f"{float(x):.2f}%"
    except Exception:
        return "-"


def _try_tushare_top5() -> Optional[pd.DataFrame]:
    """Return TuShare concept board top 5 by daily percentage gain.

    Uses `ths_daily` (Ths concept index daily data) which is accessible on
    standard TuShare accounts, and enriches the data with board names retrieved
    from `ths_index`. Requires env TUSHARE_TOKEN.
    """
    token = os.getenv("TUSHARE_TOKEN")
    if not token:
        return None

    try:
        pro = ts.pro_api(token)
        date = datetime.today().strftime("%Y%m%d")
        try:
            df = pro.limit_cpt_list(trade_date=date)
            return df
        except Exception as e:
            print(f"Error fetching limit_cpt_list for date {date}: {e}")
        
        # Search backwards up to 10 calendar days to find a trading day with data.
        base = dt.date.today()
        fields = "ts_code,trade_date,close,pct_change,turnover_ratio"
        for i in range(0, 10):
            day = base - dt.timedelta(days=i)
            trade_date = day.strftime("%Y%m%d")
            df_daily = pro.ths_daily(trade_date=trade_date, fields=fields)
            if df_daily is None or df_daily.empty:
                continue

            df_daily = df_daily.dropna(subset=["pct_change"])  # ensure sortable values
            if df_daily.empty:
                continue

            df_info = pro.ths_index(date=trade_date, fields="ts_code,name")
            name_map = dict(zip(df_info["ts_code"], df_info["name"])) if df_info is not None else {}

            df_daily = df_daily.sort_values("pct_change", ascending=False).head(5).reset_index(drop=True)
            df_daily["name"] = df_daily["ts_code"].map(name_map).fillna(df_daily["ts_code"])
            df_daily["pct_chg"] = df_daily["pct_change"].apply(_fmt_pct)
            if "turnover_ratio" in df_daily.columns:
                df_daily["turnover_ratio"] = df_daily["turnover_ratio"].apply(_fmt_pct)
            df_daily = df_daily.rename(columns={"trade_date": "date"})

            cols = ["ts_code", "name", "pct_chg", "close", "date"]
            if "turnover_ratio" in df_daily.columns:
                cols.insert(3, "turnover_ratio")
            return df_daily[cols]
        return None
    except Exception:
        # Any import/network/auth errors cause fallback to AkShare
        return None


def _try_akshare_top5() -> Optional[pd.DataFrame]:
    """Return AkShare Eastmoney industry board top 5 by today's percentage change."""
    try:
        df = ak.stock_board_industry_name_em()
        if df is None or df.empty:
            return None

        # Expected columns include: "板块名称", "涨跌幅", "上涨家数", "下跌家数", etc.
        # Keep safe with fallbacks if some columns are missing.
        name_col = "板块名称" if "板块名称" in df.columns else ("名称" if "名称" in df.columns else df.columns[0])
        pct_col = "涨跌幅" if "涨跌幅" in df.columns else None
        up_col = "上涨家数" if "上涨家数" in df.columns else None
        down_col = "下跌家数" if "下跌家数" in df.columns else None

        work = df.copy()
        if pct_col is None:
            # If percentage column is missing, we can't sort by it.
            # In that case, just take the first 5 rows as a last resort.
            work = work.head(5)
        else:
            # Convert to numeric, sort descending by pct change
            work[pct_col] = pd.to_numeric(work[pct_col], errors="coerce")
            work = work.sort_values(pct_col, ascending=False).head(5)

        out = pd.DataFrame()
        out["name"] = work[name_col].astype(str).values
        if pct_col:
            out["pct_chg"] = work[pct_col].apply(_fmt_pct).values
        if up_col:
            out["up_count"] = work[up_col].values
        if down_col:
            out["down_count"] = work[down_col].values
        out = out.reset_index(drop=True)
        return out
    except Exception:
        return None


@pytest.mark.integration
def test_popular_sector_top5_print_and_sanity():
    """Fetch and print Top 5 popular sectors, asserting non-empty result if network OK.

    - Prefer TuShare ths_daily (requires token)
    - Fallback to AkShare Eastmoney industry boards
    - Skip the test if neither data source yields results (e.g., no Internet)
    """

    df = _try_tushare_top5()
    source = "TuShare ths_daily"
    if df is None or df.empty:
        df = _try_akshare_top5()
        source = "AkShare Eastmoney industry"

    if df is None or df.empty:
        pytest.skip("No sector data available (missing token, API limits, or no network).")

    # Print a compact table for human inspection in CI/logs
    print("\n=== Popular Sectors Top 5 (source: %s) ===" % source)
    print(df.to_string(index=False))

    # Minimal sanity checks so the test is meaningful but robust to daily changes
    assert len(df) <= 5
    assert len(df) > 0


if __name__ == "__main__":
    df = _try_tushare_top5()
    print(df)