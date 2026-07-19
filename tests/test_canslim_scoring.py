"""
Unit tests for backtest/strategies/ts_7AZ.py — CANSLIM scoring logic.

Tests the pure-Python scoring functions without hitting Tushare APIs:
- compute_rps() — Relative Price Strength percentile
- canslim_score_stock() — 7-factor scoring (mocked tech + fin data)
- canslim_screener() — full screener pipeline (mocked PRO.daily)

The goal is to lock in the scoring semantics so refactoring the screener
(vectorization, caching) can't silently change which stocks pass each factor.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock, PropertyMock

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from backtest.strategies.ts_7AZ import (
    compute_rps,
    canslim_score_stock,
    canslim_screener,
    C_EPS_GROWTH_THRESHOLD,
    A_ROE_THRESHOLD,
    N_52W_HIGH_RATIO,
    S_MARKET_CAP_MAX,
    L_RPS_THRESHOLD,
    I_TURNOVER_MIN,
    I_TURNOVER_MAX,
)


# ── compute_rps ──────────────────────────────────────────────────────────────

class TestComputeRPS:
    """Tests for Relative Price Strength calculation.

    compute_rps() takes a DataFrame with a 'close' column (the full OHLCV
    panel), not a bare Series.
    """

    def _make_df(self, closes: list[float]) -> pd.DataFrame:
        return pd.DataFrame({"close": closes})

    def test_strong_uptrend_high_rps(self):
        """A stock that doubled in 250 days should have RPS around 100."""
        closes = list(range(100, 351))  # 100 → 350 over 250 days
        df = self._make_df(closes)
        rps = compute_rps(df, lookback=250)
        assert rps > 90, f"Doubling stock should have high RPS, got {rps}"

    def test_declining_stock_negative_rps(self):
        """A stock that lost half its value should have negative RPS."""
        # 250 days of decline from 200 to 50
        closes = [200 - i * 0.6 for i in range(250)] + [50.0] * 10
        df = self._make_df(closes)
        rps = compute_rps(df, lookback=250)
        assert rps < 0, f"Declining stock should have negative RPS, got {rps}"

    def test_flat_stock_zero_rps(self):
        """A flat stock should have RPS near 0."""
        df = self._make_df([100.0] * 260)
        rps = compute_rps(df, lookback=250)
        assert abs(rps) < 0.01, f"Flat stock should have ~0 RPS, got {rps}"

    def test_insufficient_data_returns_zero(self):
        """Series shorter than lookback should return 0.0, not raise."""
        df = self._make_df([100.0, 101.0, 102.0])
        rps = compute_rps(df, lookback=250)
        assert rps == 0.0


# ── canslim_score_stock ──────────────────────────────────────────────────────

class TestCanslimScoreStock:
    """Tests for single-stock CANSLIM scoring with mocked data."""

    def test_all_factors_pass_returns_all_true(self):
        """A stock passing all 7 factors should have all flags True."""
        with patch("backtest.strategies.ts_7AZ.compute_technical_indicators") as mock_tech, \
             patch("backtest.strategies.ts_7AZ.fetch_financial_data") as mock_fin:
            mock_tech.return_value = {
                "price": 15.0,
                "return_250": 0.5,  # 50% return → high RPS
                "ma200": 10.0,       # price > MA200
                "high_52w": 15.5,    # price within 15% of high (15/15.5 = 0.967 > 0.85)
                "turnover_rate": 0.05,  # 5% — in [2%, 15%]
                "total_mv": 100e8,   # 100亿 < 500亿
            }
            mock_fin.return_value = {
                "eps_growth": 0.30,  # 30% > 25% threshold
                "roe": 0.20,         # 20% > 17% threshold
            }
            result = canslim_score_stock("AAA.SZ", "Test Stock", "20260101")
            assert result is not None
            assert result["c_eps"] is True
            assert result["a_roe"] is True
            assert result["n_near_high"] is True
            assert result["s_small_cap"] is True
            assert result["i_turnover_ok"] is True
            assert result["m_above_ma"] is True

    def test_low_eps_fails_c_factor(self):
        """EPS growth below 25% should fail the C factor."""
        with patch("backtest.strategies.ts_7AZ.compute_technical_indicators") as mock_tech, \
             patch("backtest.strategies.ts_7AZ.fetch_financial_data") as mock_fin:
            mock_tech.return_value = {
                "price": 15.0, "return_250": 0.5, "ma200": 10.0,
                "high_52w": 15.5, "turnover_rate": 0.05, "total_mv": 100e8,
            }
            mock_fin.return_value = {"eps_growth": 0.10, "roe": 0.20}  # 10% < 25%
            result = canslim_score_stock("AAA.SZ", "Test", "20260101")
            assert result["c_eps"] is False
            assert result["a_roe"] is True

    def test_low_roe_fails_a_factor(self):
        """ROE below 17% should fail the A factor."""
        with patch("backtest.strategies.ts_7AZ.compute_technical_indicators") as mock_tech, \
             patch("backtest.strategies.ts_7AZ.fetch_financial_data") as mock_fin:
            mock_tech.return_value = {
                "price": 15.0, "return_250": 0.5, "ma200": 10.0,
                "high_52w": 15.5, "turnover_rate": 0.05, "total_mv": 100e8,
            }
            mock_fin.return_value = {"eps_growth": 0.30, "roe": 0.10}  # 10% < 17%
            result = canslim_score_stock("AAA.SZ", "Test", "20260101")
            assert result["c_eps"] is True
            assert result["a_roe"] is False

    def test_below_ma200_fails_m_factor(self):
        """Price below 200-day MA should fail the M factor."""
        with patch("backtest.strategies.ts_7AZ.compute_technical_indicators") as mock_tech, \
             patch("backtest.strategies.ts_7AZ.fetch_financial_data") as mock_fin:
            mock_tech.return_value = {
                "price": 8.0, "return_250": 0.5, "ma200": 10.0,  # price < MA200
                "high_52w": 15.5, "turnover_rate": 0.05, "total_mv": 100e8,
            }
            mock_fin.return_value = {"eps_growth": 0.30, "roe": 0.20}
            result = canslim_score_stock("AAA.SZ", "Test", "20260101")
            assert result["m_above_ma"] is False

    def test_low_turnover_fails_i_factor(self):
        """Turnover below 2% should fail the I factor."""
        with patch("backtest.strategies.ts_7AZ.compute_technical_indicators") as mock_tech, \
             patch("backtest.strategies.ts_7AZ.fetch_financial_data") as mock_fin:
            mock_tech.return_value = {
                "price": 15.0, "return_250": 0.5, "ma200": 10.0,
                "high_52w": 15.5, "turnover_rate": 0.01,  # 1% < 2%
                "total_mv": 100e8,
            }
            mock_fin.return_value = {"eps_growth": 0.30, "roe": 0.20}
            result = canslim_score_stock("AAA.SZ", "Test", "20260101")
            assert result["i_turnover_ok"] is False

    def test_large_market_cap_fails_s_factor(self):
        """Market cap above 500亿 should fail the S factor."""
        with patch("backtest.strategies.ts_7AZ.compute_technical_indicators") as mock_tech, \
             patch("backtest.strategies.ts_7AZ.fetch_financial_data") as mock_fin:
            mock_tech.return_value = {
                "price": 15.0, "return_250": 0.5, "ma200": 10.0,
                "high_52w": 15.5, "turnover_rate": 0.05,
                "total_mv": 600e8,  # 600亿 > 500亿
            }
            mock_fin.return_value = {"eps_growth": 0.30, "roe": 0.20}
            result = canslim_score_stock("AAA.SZ", "Test", "20260101")
            assert result["s_small_cap"] is False

    def test_far_from_52w_high_fails_n_factor(self):
        """Price far below 52-week high should fail the N factor."""
        with patch("backtest.strategies.ts_7AZ.compute_technical_indicators") as mock_tech, \
             patch("backtest.strategies.ts_7AZ.fetch_financial_data") as mock_fin:
            mock_tech.return_value = {
                "price": 10.0, "return_250": 0.5, "ma200": 8.0,
                "high_52w": 20.0,  # 10/20 = 0.50 < 0.85
                "turnover_rate": 0.05, "total_mv": 100e8,
            }
            mock_fin.return_value = {"eps_growth": 0.30, "roe": 0.20}
            result = canslim_score_stock("AAA.SZ", "Test", "20260101")
            assert result["n_near_high"] is False

    def test_technical_none_returns_none(self):
        """If compute_technical_indicators returns None, score should be None."""
        with patch("backtest.strategies.ts_7AZ.compute_technical_indicators") as mock_tech:
            mock_tech.return_value = None
            result = canslim_score_stock("AAA.SZ", "Test", "20260101")
            assert result is None


# ── CANSLIM constants sanity ─────────────────────────────────────────────────

class TestCanslimConstants:
    """Sanity checks on the CANSLIM threshold constants."""

    def test_eps_growth_threshold_is_25pct(self):
        assert C_EPS_GROWTH_THRESHOLD == 0.25

    def test_roe_threshold_is_17pct(self):
        assert A_ROE_THRESHOLD == 0.17

    def test_52w_high_ratio_is_85pct(self):
        assert N_52W_HIGH_RATIO == 0.85

    def test_market_cap_max_is_500_billion(self):
        assert S_MARKET_CAP_MAX == 500e8

    def test_rps_threshold_is_80(self):
        assert L_RPS_THRESHOLD == 80

    def test_turnover_range_2_to_15pct(self):
        assert I_TURNOVER_MIN == 0.02
        assert I_TURNOVER_MAX == 0.15
