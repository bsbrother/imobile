"""
Pytest configuration and fixtures for iMobile.

Registers custom markers and provides reusable fixtures so tests do not
hit real Tushare/Akshare/Gemini endpoints by default.

Tests that require live external APIs should be marked:

    @pytest.mark.integration
    def test_tushare_real_call(): ...

They will be skipped unless --run-integration is passed on the CLI.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pandas as pd
import pytest

# Make the project root importable so `from backtest...` works from tests/.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── CLI flag: --run-integration ─────────────────────────────────────────────
def pytest_addoption(parser):
    parser.addoption(
        "--run-integration",
        action="store_true",
        default=False,
        help="Run integration tests that hit real external APIs (Tushare, Akshare, Gemini).",
    )


# ── Marker registration ────────────────────────────────────────────────────
def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "integration: tests that access network or external services — skipped without --run-integration",
    )
    config.addinivalue_line(
        "markers",
        "benchmark: performance benchmarks (utils/daily_stock_analysis)",
    )


def pytest_collection_modifyitems(config, items):
    """Auto-skip @pytest.mark.integration tests unless --run-integration is set."""
    if config.getoption("--run-integration"):
        return
    skip_integration = pytest.mark.skip(
        reason="Integration test — needs --run-integration (hits real external APIs)."
    )
    for item in items:
        if "integration" in item.keywords:
            item.add_marker(skip_integration)


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def tmp_db_path(tmp_path) -> Path:
    """Empty in-memory-capable SQLite path under tmp_path."""
    return tmp_path / "test_imobile.db"


@pytest.fixture
def sample_ohlcv() -> pd.DataFrame:
    """Small OHLCV panel for three dates, two symbols — pure-Python, no API."""
    rows = [
        # AAA.SZ — steady uptrend
        ("2025-10-23", "AAA.SZ", 10.0, 10.5,  9.8, 10.2, 1_000_000, 0.025),
        ("2025-10-24", "AAA.SZ", 10.2, 11.0, 10.0, 10.8, 1_200_000, 0.028),
        ("2025-10-27", "AAA.SZ", 10.8, 12.0, 10.5, 11.5, 1_500_000, 0.030),
        # BBB.SH — flat
        ("2025-10-23", "BBB.SH", 20.0, 20.2, 19.8, 20.0,   500_000, 0.010),
        ("2025-10-24", "BBB.SH", 20.0, 20.1, 19.9, 20.0,   480_000, 0.011),
        ("2025-10-27", "BBB.SH", 20.0, 20.3, 19.7, 20.1,   520_000, 0.012),
    ]
    columns = [
        "trade_date", "ts_code", "open", "high", "low", "close", "vol", "turnover_rate"
    ]
    df = pd.DataFrame(rows)
    df.columns = columns
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.strftime("%Y%m%d")
    return df


@pytest.fixture
def sample_picks_json(tmp_path) -> Path:
    """A pick_stocks_YYYYMMDD.json fixture matching engine.py's expected schema."""
    data = {
        "pick_date": "2025-10-22 09:30:00",
        "base_date": "20251021",
        "target_trading_date": "20251022",
        "market_pattern": "normal",
        "regime_data": {
            "regime": "normal",
            "take_profit_pct": 0.22,
            "stop_loss_pct": 0.025,
            "max_hold_days": 10,
        },
        "selected_stocks": [
            {"rank": 1, "symbol": "AAA.SZ", "score": 6.0},
            {"rank": 2, "symbol": "BBB.SH", "score": 5.0},
        ],
    }
    import json
    p = tmp_path / "pick_stocks_20251022.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    return p
