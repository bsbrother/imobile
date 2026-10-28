"""The momentum switcher: month lookup, lag, fallback, and the sizing it hands the engine.

These guard the properties that make the switch run trustworthy rather than the returns it
produces: a session must never see its own month, a month missing from the schedule must fall
back loudly, and the book size must come from the delegate that is actually picking.
"""
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.strategies import ts_switch_momentum as sm  # noqa: E402


def _schedule(tmp_path, rows):
    p = tmp_path / 'schedule.csv'
    with open(p, 'w', newline='') as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    return str(p)


def test_month_lookup_uses_the_row_for_that_month(tmp_path):
    p = _schedule(tmp_path, [
        {'month': '202501', 'delegate': 'ts_turning_point', 'trailing_month': '',
         'trail_flow_pct': '', 'trail_turn_pct': '', 'reason': 'no history'},
        {'month': '202502', 'delegate': 'ts_turning_point', 'trailing_month': '202501',
         'trail_flow_pct': '-5.42', 'trail_turn_pct': '2.46', 'reason': 'trailing 1m'},
    ])
    sched = sm.load_schedule(p)
    assert sm.delegate_for('202501', sched)[0] == 'ts_turning_point'
    assert sm.delegate_for('202502', sched)[0] == 'ts_turning_point'


def test_the_trailing_month_is_always_the_previous_one(tmp_path):
    """The decision for month M may only cite month M-1 — the whole "no look-ahead" claim."""
    p = _schedule(tmp_path, [
        {'month': '202501', 'delegate': 'ts_turning_point', 'trailing_month': '',
         'trail_flow_pct': '', 'trail_turn_pct': '', 'reason': 'no history'},
        {'month': '202502', 'delegate': 'ts_turning_point', 'trailing_month': '202501',
         'trail_flow_pct': '-5.42', 'trail_turn_pct': '2.46', 'reason': 'trailing 1m'},
        {'month': '202503', 'delegate': 'ts_turning_point', 'trailing_month': '202502',
         'trail_flow_pct': '-3.36', 'trail_turn_pct': '10.87', 'reason': 'trailing 1m'},
    ])
    sched = sm.load_schedule(p)
    months = sorted(sched)
    for m in months[1:]:
        assert sched[m]['trailing_month'] == months[months.index(m) - 1]
        assert sched[m]['trailing_month'] < m


def test_missing_month_falls_back_to_the_production_default(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, 'FALLBACK', 'ts_7AZ_96MA_flow_review')
    p = _schedule(tmp_path, [
        {'month': '202501', 'delegate': 'ts_turning_point', 'trailing_month': '',
         'trail_flow_pct': '', 'trail_turn_pct': '', 'reason': 'no history'},
    ])
    delegate, row = sm.delegate_for('202607', sm.load_schedule(p))
    assert delegate == 'ts_7AZ_96MA_flow_review'
    assert row == {}


def test_absent_schedule_file_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(sm, 'SCHEDULE_CSV', str(tmp_path / 'nope.csv'))
    monkeypatch.setattr(sm, 'FALLBACK', 'ts_7AZ_96MA_flow_review')
    assert sm.load_schedule() == {}
    assert sm.delegate_for('202501', {})[0] == 'ts_7AZ_96MA_flow_review'


def test_both_delegates_are_addressable():
    for name in ('ts_7AZ_96MA_flow_review', 'ts_turning_point'):
        assert name in sm.DELEGATE_SCRIPTS
        assert os.path.exists(sm.DELEGATE_SCRIPTS[name])


def test_the_engine_takes_book_size_from_the_delegate(tmp_path):
    """2 slots for turning in every regime, nothing declared for the flow arm."""
    from backtest.utils.strategy_env import section_values
    turning = section_values('ts_turning_point')
    assert {turning[f'MAX_POS_{r}'] for r in ('BULL', 'NORMAL', 'VOLATILE', 'BEAR')} == {'2'}
    flow = section_values('ts_7AZ_96MA_flow_review')
    assert not any(k.startswith('MAX_POS_') for k in flow), \
        'the flow arm must fall through to the engine defaults it was measured with'
