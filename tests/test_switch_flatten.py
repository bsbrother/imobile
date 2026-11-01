"""SWITCH_FLATTEN: close the inherited book on the first session of a new arm.

The switch run showed the cost of NOT doing this — a 2-slot turning book cannot become a
12-slot flow book until its two ¥300k positions exit on their own rules, so every arm change
left the book under-invested. These tests pin the trigger (once, on the boundary session, and
only when the arm actually changed) rather than any return.
"""
import csv
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest import engine  # noqa: E402


@pytest.fixture
def schedule(tmp_path):
    p = tmp_path / 'schedule.csv'
    rows = [
        {'month': '202501', 'delegate': 'ts_turning_point'},
        {'month': '202502', 'delegate': 'ts_turning_point'},
        {'month': '202503', 'delegate': 'ts_7AZ_96MA_flow_review'},
        {'month': '202504', 'delegate': 'ts_7AZ_96MA_flow_review'},
    ]
    with open(p, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=['month', 'delegate'])
        w.writeheader()
        w.writerows(rows)
    return str(p)


@pytest.fixture(autouse=True)
def fresh_state(monkeypatch):
    monkeypatch.setattr(engine, '_FLATTEN_STATE', {}, raising=False)
    yield


def test_off_by_default(schedule, monkeypatch):
    monkeypatch.setenv('SWITCH_SCHEDULE_CSV', schedule)
    monkeypatch.delenv('SWITCH_FLATTEN', raising=False)
    assert engine._switch_flatten_today('20250303') is False


def test_fires_once_on_the_boundary_session(schedule, monkeypatch):
    monkeypatch.setenv('SWITCH_SCHEDULE_CSV', schedule)
    monkeypatch.setenv('SWITCH_FLATTEN', '1')
    assert engine._switch_flatten_today('20250303') is True     # arm changed -> close
    assert engine._switch_flatten_today('20250304') is False    # already flat
    assert engine._switch_flatten_today('20250328') is False


def test_silent_when_the_arm_did_not_change(schedule, monkeypatch):
    monkeypatch.setenv('SWITCH_SCHEDULE_CSV', schedule)
    monkeypatch.setenv('SWITCH_FLATTEN', '1')
    assert engine._switch_flatten_today('20250102') is False    # first month, nothing to close
    assert engine._switch_flatten_today('20250203') is False    # same arm as January
    assert engine._switch_flatten_today('20250401') is False    # same arm as March


def test_missing_schedule_disables_it(tmp_path, monkeypatch):
    monkeypatch.setenv('SWITCH_SCHEDULE_CSV', str(tmp_path / 'nope.csv'))
    monkeypatch.setenv('SWITCH_FLATTEN', '1')
    assert engine._switch_flatten_today('20250303') is False


def test_month_absent_from_the_schedule_does_not_fire(schedule, monkeypatch):
    monkeypatch.setenv('SWITCH_SCHEDULE_CSV', schedule)
    monkeypatch.setenv('SWITCH_FLATTEN', '1')
    assert engine._switch_flatten_today('20260706') is False


def test_resume_does_not_re_fire_mid_month(schedule, tmp_path, monkeypatch):
    """A killed run leaves earlier reports; resuming mid-month must not flatten again."""
    monkeypatch.setenv('SWITCH_SCHEDULE_CSV', schedule)
    monkeypatch.setenv('SWITCH_FLATTEN', '1')
    monkeypatch.setattr(engine, 'REPORT_PATH', str(tmp_path))
    for d in ('20250303', '20250304'):
        (tmp_path / f'report_orders_{d}.md').write_text('x')
    assert engine._switch_flatten_today('20250305') is False


def test_prior_month_reports_do_not_block_a_new_boundary(schedule, tmp_path, monkeypatch):
    monkeypatch.setenv('SWITCH_SCHEDULE_CSV', schedule)
    monkeypatch.setenv('SWITCH_FLATTEN', '1')
    monkeypatch.setattr(engine, 'REPORT_PATH', str(tmp_path))
    (tmp_path / 'report_orders_20250203.md').write_text('x')
    assert engine._switch_flatten_today('20250303') is True
