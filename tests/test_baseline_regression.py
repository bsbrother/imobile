"""
Characterization / regression test for the ts_7AZ baseline backtest.

This test does NOT re-run the backtest. Instead it parses the committed
period report and asserts the total return stays within a tolerance band
around the established baseline.

Baseline history:
  70.59%  measured under max_hold_days 7/5/4/2 (HOLD_DAYS_MULT=0.5)
  94.84%  measured under max_hold_days 1/1/1/1 (current config)

`trading_rules.risk_reward_ratios` is shared by every strategy, so the 1-day
cap lifted ts_7AZ too (+24.25pp) — see PR #5. The band stays tight (±0.2%) on
purpose: that is what catches a semantic change. The 65% floor is only a
catastrophe guard.

Brittleness the band tolerates:
  - Tushare data drift (minor OHLCV revisions)
  - FP reordering when refactoring engine.py loops (vectorization)

To refresh after an intentional strategy change:
  .venv/bin/python backtest/engine.py 20260101 20260619 ts_7AZ --no-search --no-ai
  git add -f backtest/results/20260101_20260619_ts_7AZ/report_period_20260101_20260619.md
  # `-f` is required: backtest/results/ is gitignored. Without it the report
  # never lands and this test skips instead of guarding anything.
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

# Baseline re-pinned after the shared max_hold_days change (PR #5).
# Previous value: 70.59% under holds 7/5/4/2 (branch
# baseline_returns_ts_7AZ_70.60 — hence the old 70.59 -> 70.60 rounding note).
EXPECTED_RETURN = 94.84
TOLERANCE_BAND = 0.2       # ±0.2% — accept 94.64 ~ 95.04
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
