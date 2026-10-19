"""Repeat stop-loss blacklist must read the LIVE report format.

The blacklist only fired when it found 2+ stop-loss hits on distinct days while
the market was stressed. It scanned `report_orders_*.md` for the token
`STOP_LOSS` — a format produced only by backtest runs. The live pipeline writes
`daily/post_market_*.md`, which says `触发止损` in Chinese, so in production the
blacklist was ALWAYS empty.

Cost: 603268 松发股份 was stopped out on 2026-10-12, immediately re-bought on
10-14, and stopped out again on 10-15 (-4,846 on the second round alone).

Run: env -u PYTHONPATH .venv/bin/python -m pytest tests/test_repeat_sl_blacklist.py -q
"""
from pathlib import Path

import pytest

from backtest.strategies.ts_7AZ import _load_repeat_sl_blacklist


LIVE_LINE = "  松发股份 603268 500股 @¥189.55 触发止损 盈亏¥-4,846\n"
LIVE_GAP_LINE = "  药明康德 603259 100股 @¥153.34 触发止损(跳空开盘) 盈亏¥-1,100\n"


def _write_daily(root: Path, date: str, body: str) -> None:
    d = root / "daily"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"post_market_{date}.md").write_text(
        f"📑 [收盘报告] {date}\n{body}", encoding="utf-8")


def test_two_live_sl_hits_on_distinct_days_are_blacklisted(tmp_path):
    """The real October case: SL on 10-12 AND 10-15 -> blocked on 10-16."""
    _write_daily(tmp_path, "20261012", LIVE_LINE)
    _write_daily(tmp_path, "20261015", LIVE_LINE)

    bl = _load_repeat_sl_blacklist("20261016", results_dir=str(tmp_path))

    assert bl == {"603268"}


def test_single_live_hit_is_not_blacklisted(tmp_path):
    """One-time SL stocks often recover — must NOT be blocked."""
    _write_daily(tmp_path, "20261012", LIVE_LINE)

    bl = _load_repeat_sl_blacklist("20261016", results_dir=str(tmp_path))

    assert bl == set()


def test_repeated_sl_on_the_same_day_counts_once(tmp_path):
    """A report mentioning the symbol twice on one day is still one hit."""
    body = LIVE_LINE.replace("松发股份", "松发股份") * 2
    _write_daily(tmp_path, "20261012", body)

    bl = _load_repeat_sl_blacklist("20261016", results_dir=str(tmp_path))

    assert bl == set()


def test_gap_down_stop_variant_is_recognised(tmp_path):
    _write_daily(tmp_path, "20261012", LIVE_GAP_LINE)
    _write_daily(tmp_path, "20261013", LIVE_GAP_LINE)

    bl = _load_repeat_sl_blacklist("20261016", results_dir=str(tmp_path))

    assert bl == {"603259"}


def test_hits_outside_the_cooldown_window_are_ignored(tmp_path):
    """cooldown_days=5 calendar days back from end_date."""
    _write_daily(tmp_path, "20261001", LIVE_LINE)
    _write_daily(tmp_path, "20261002", LIVE_LINE)

    bl = _load_repeat_sl_blacklist("20261016", cooldown_days=5,
                                   results_dir=str(tmp_path))

    assert bl == set()


def test_backtest_format_still_recognised(tmp_path):
    """Backtest runs write report_orders_*.md with a STOP_LOSS token."""
    d = tmp_path / "20260101_20260831_ts_7AZ"
    d.mkdir(parents=True)
    for date in ("20260702", "20260703"):
        (d / f"report_orders_{date}.md").write_text(
            f"### 603268.SH 松发股份\nSELL STOP_LOSS @200.88\n",
            encoding="utf-8")

    bl = _load_repeat_sl_blacklist("20260706", results_dir=str(tmp_path))

    assert bl == {"603268.SH"}


def test_missing_results_dir_returns_empty():
    assert _load_repeat_sl_blacklist("20261016",
                                     results_dir="/nonexistent/path") == set()