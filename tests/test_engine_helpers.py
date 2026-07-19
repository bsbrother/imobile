"""
Unit tests for backtest/engine.py pure-Python helpers.

These tests cover the utility functions in engine.py that don't require
database access or external API calls — the helpers that were added or
refactored during the Phase B optimization.

Covers:
- kaufman_efficiency_ratio() — Kaufman ER calculation
- _detect_market_regime_cached() — memoization wrapper
- _run_strategy_script() / _run_cli_command() — subprocess helpers (mocked)
- _REGIME_CACHE behavior
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pandas as pd
import pytest

# Ensure project root is on the path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Import engine helpers ──────────────────────────────────────────────────
# We import the functions directly; engine.py imports heavy dependencies
# (loguru, dotenv, data_provider) but the helpers below are pure Python.

from backtest.engine import (
    kaufman_efficiency_ratio,
    _detect_market_regime_cached,
    _REGIME_CACHE,
    _run_strategy_script,
    _run_cli_command,
)


# ── kaufman_efficiency_ratio ─────────────────────────────────────────────────

class TestKaufmanEfficiencyRatio:
    """Tests for the Kaufman Efficiency Ratio calculation."""

    def test_strong_uptrend_high_er(self):
        """A perfectly monotonic uptrend should have ER close to 1.0."""
        closes = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0])
        er = kaufman_efficiency_ratio(closes, period=5)
        assert er > 0.95, f"Strong uptrend should have ER near 1.0, got {er}"

    def test_strong_downtrend_high_er(self):
        """A perfectly monotonic downtrend should also have ER near 1.0."""
        closes = pd.Series([15.0, 14.0, 13.0, 12.0, 11.0, 10.0])
        er = kaufman_efficiency_ratio(closes, period=5)
        assert er > 0.95, f"Strong downtrend should have ER near 1.0, got {er}"

    def test_choppy_market_low_er(self):
        """A zigzag/oscillating series should have ER well below 1.0."""
        closes = pd.Series([10.0, 12.0, 9.0, 13.0, 8.0, 14.0, 7.0, 15.0])
        er = kaufman_efficiency_ratio(closes, period=7)
        assert er < 0.5, f"Choppy market should have ER < 0.5, got {er}"

    def test_flat_market_returns_nan(self):
        """A completely flat series (zero volatility) should return NaN."""
        closes = pd.Series([10.0] * 6)
        er = kaufman_efficiency_ratio(closes, period=5)
        assert pd.isna(er), f"Flat market should return NaN, got {er}"

    def test_insufficient_data_returns_nan(self):
        """Series shorter than period+1 should return NaN."""
        closes = pd.Series([10.0, 11.0, 12.0])
        er = kaufman_efficiency_ratio(closes, period=5)
        assert pd.isna(er), "Insufficient data should return NaN"

    def test_empty_series_returns_nan(self):
        """Empty series should return NaN, not raise."""
        er = kaufman_efficiency_ratio(pd.Series([], dtype=float), period=5)
        assert pd.isna(er)


# ── _detect_market_regime_cached ─────────────────────────────────────────────

class TestRegimeMemoization:
    """Tests for the market regime memoization wrapper."""

    def setup_method(self):
        """Clear the cache before each test."""
        _REGIME_CACHE.clear()

    def test_cache_hit_avoids_repeated_calls(self):
        """Second call with same date should use cache, not call detect_market_regime."""
        with patch("backtest.engine.detect_market_regime") as mock_detect:
            mock_detect.return_value = {"regime": "bull", "take_profit_pct": 0.25}
            result1 = _detect_market_regime_cached("20260101")
            result2 = _detect_market_regime_cached("20260101")
            assert mock_detect.call_count == 1, "detect_market_regime should only be called once"
            assert result1 == result2 == {"regime": "bull", "take_profit_pct": 0.25}

    def test_different_dates_trigger_separate_calls(self):
        """Different dates should each trigger a separate API call."""
        with patch("backtest.engine.detect_market_regime") as mock_detect:
            mock_detect.side_effect = [
                {"regime": "bull"},
                {"regime": "bear"},
            ]
            r1 = _detect_market_regime_cached("20260101")
            r2 = _detect_market_regime_cached("20260102")
            assert mock_detect.call_count == 2
            assert r1["regime"] == "bull"
            assert r2["regime"] == "bear"

    def test_cache_persists_across_calls(self):
        """Cache should persist across multiple function calls."""
        with patch("backtest.engine.detect_market_regime") as mock_detect:
            mock_detect.return_value = {"regime": "normal"}
            for _ in range(10):
                _detect_market_regime_cached("20260101")
            assert mock_detect.call_count == 1, "10 calls for same date = 1 API call"


# ── _run_strategy_script ─────────────────────────────────────────────────────

class TestRunStrategyScript:
    """Tests for the subprocess.run() wrapper that replaced os.system()."""

    def test_success_does_not_raise(self):
        """A script that exits 0 should not raise."""
        with patch("backtest.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            # Should not raise
            _run_strategy_script("backtest/strategies/ts_7AZ.py", "20260101", "ts_7AZ")

    def test_failure_raises_runtime_error_with_stderr(self):
        """A script that exits non-zero should raise RuntimeError with stderr."""
        with patch("backtest.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=1, stderr="ModuleNotFoundError: No module named 'foo'", stdout=""
            )
            with pytest.raises(RuntimeError, match="ModuleNotFoundError"):
                _run_strategy_script("backtest/strategies/fake.py", "20260101")

    def test_args_passed_as_list_not_string(self):
        """Args should be passed as a list to subprocess.run (no shell=True)."""
        with patch("backtest.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            _run_strategy_script("script.py", "20260101", "--no-search", "--no-ai")
            call_args = mock_run.call_args
            cmd = call_args[0][0] if call_args[0] else call_args[1].get("args", [])
            assert isinstance(cmd, list), "Command should be a list, not a shell string"
            assert "script.py" in cmd
            assert "20260101" in cmd
            assert "--no-search" in cmd

    def test_uses_sys_executable(self):
        """Should use sys.executable as the Python interpreter."""
        with patch("backtest.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            _run_strategy_script("script.py", "20260101")
            cmd = mock_run.call_args[0][0]
            assert cmd[0] == sys.executable, "Should use sys.executable"


# ── _run_cli_command ────────────────────────────────────────────────────────

class TestRunCliCommand:
    """Tests for the CLI subprocess wrapper."""

    def test_success_does_not_raise(self):
        with patch("backtest.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            _run_cli_command("analyze", "--stocks-file", "pick.json")

    def test_failure_includes_command_name_in_error(self):
        with patch("backtest.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                returncode=2, stderr="Argument error: missing --output", stdout=""
            )
            with pytest.raises(RuntimeError, match="analyze"):
                _run_cli_command("analyze", "--stocks-file", "pick.json")

    def test_invokes_backtest_cli_module(self):
        """Should invoke `python -m backtest.cli <args>`."""
        with patch("backtest.engine.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0, stderr="", stdout="")
            _run_cli_command("analyze", "--stocks-file", "pick.json")
            cmd = mock_run.call_args[0][0]
            assert "-m" in cmd
            assert "backtest.cli" in cmd
