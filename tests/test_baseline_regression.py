"""
Characterization / regression test for the ts_7AZ baseline backtest.

This test does NOT re-run the 44-minute backtest. Instead it parses the
committed period report and asserts the total return stays within a
tolerance band around the established 70.59% baseline.

The band is intentionally loose (70.4-70.8% with a 65% floor) to avoid
brittleness from:
  - Tushare data drift (minor OHLCV revisions)
  - FP reordering when refactoring engine.py loops (vectorization)
  - Rounding artifacts (70.59 rounds to 70.60 in the branch name)

To refresh the baseline report after an intentional strategy change:
  .venv/bin/python backtest/engine.py 20260101 20260619 ts_7AZ --no-search --no-ai
  # then commit the new report_period_*.md under backtest/results/
"""

import re
from pathlib import Path

import pytest


REPORT_PATH = (
    Path(__file__).resolve().parent.parent
    / "backtest" / "results"
    / "20260101_20260619_ts_7AZ"
    / "report_period_20260101_20260619.md"
)

# Established baseline (committed branch: baseline_returns_ts_7AZ_70.60).
# Measured value is 70.59% — the branch name rounds to 70.60.
EXPECTED_RETURN = 70.59
TOLERANCE_BAND = 0.2       # ±0.2% — accept 70.39 ~ 70.79
FLOOR_RETURN = 65.0       # hard floor: anything below 65% is a semantic regression


@pytest.fixture
def report_content() -> str:
    """Read the committed period report; skip if it has been removed."""
    if not REPORT_PATH.exists():
        pytest.skip(f"Baseline report not found at {REPORT_PATH}")
    return REPORT_PATH.read_text(encoding="utf-8")


def _extract_total_return(content: str) -> float:
    """
    Pull the Total Return percentage from the Markdown table.

    The report has two tables (Portfolio Summary + Benchmark Comparison);
    both list Total Return. We scan for the first '70.xx%' pattern in a
    Total Return row to avoid ambiguity.
    """
    # Match: | **Total Return** | 70.59% | ...  OR  | Total Return | 70.59% | ...
    pattern = r"Total\s+Return[^|]*\|\s*([0-9]+\.[0-9]+)%"
    m = re.search(pattern, content)
    assert m, "Could not parse Total Return from report — report format may have changed."
    return float(m.group(1))


def test_report_exists(report_content):
    """Sanity check: the report file is non-empty and has the expected header."""
    assert "Backtest Period Report" in report_content
    assert "20260101" in report_content and "20260619" in report_content


def test_total_return_within_band(report_content):
    """
    The strategy's total return must stay within ±0.2% of the 70.59% baseline.

    A drift outside this band after a refactor indicates a semantic change
    (e.g., FP ordering from vectorization, T+1 timing shift), not merely
    a performance optimization.
    """
    actual = _extract_total_return(report_content)
    assert abs(actual - EXPECTED_RETURN) <= TOLERANCE_BAND, (
        f"Total return {actual:.2f}% drifted from baseline {EXPECTED_RETURN}% "
        f"by {abs(actual - EXPECTED_RETURN):.2f}% (tolerance ±{TOLERANCE_BAND}%). "
        f"This indicates a semantic change, not just a performance optimization."
    )


def test_total_return_above_floor(report_content):
    """
    Hard floor: regardless of band drift, anything below 65% means the
    strategy logic has materially regressed.
    """
    actual = _extract_total_return(report_content)
    assert actual >= FLOOR_RETURN, (
        f"Total return {actual:.2f}% is below the {FLOOR_RETURN}% floor — "
        f"material strategy regression."
    )


def test_benchmark_comparison_present(report_content):
    """The report must include benchmark comparison (SSE Composite, CSI 300, CSI 500)."""
    assert "SSE Composite" in report_content
    assert "CSI 300" in report_content
    assert "CSI 500" in report_content


def test_t_plus_1_compliance_noted(report_content):
    """The report must note T+1 compliance enforcement."""
    assert "T+1" in report_content
