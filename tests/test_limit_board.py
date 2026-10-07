"""Price-limit board policy: bands, state classification, and the fill verdicts.

Pure functions — no cache, no DB. These pin the rule that a limit-up board cannot be BOUGHT at the
open and a sealed limit-down board cannot be SOLD, which is what stops the backtest from booking
trades the exchange would never have matched.
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from backtest.utils.limit_board import (  # noqa: E402
    DOWN_OPEN, DOWN_SEALED, NORMAL, UP_OPEN, UP_SEALED,
    board_band, board_guard_enabled, board_state, buy_block_reason, fill_block_reason,
    gap_exit_enabled, gap_exit_near_pct, gap_exit_reason,
    is_buy_blocked, is_sell_blocked,
    limit_prices, sell_block_reason,
)

PREV = 10.00          # main board bands: 9.00 .. 11.00
PREV_WIDE = 100.00    # ChiNext/STAR: 80.00 .. 120.00


# ─── bands ───────────────────────────────────────────────────
def test_band_by_board():
    assert board_band("600000.SH") == 0.10
    assert board_band("000001.SZ") == 0.10
    assert board_band("002415.SZ") == 0.10
    assert board_band("300308.SZ") == 0.20
    assert board_band("301165.SZ") == 0.20
    assert board_band("688183.SH") == 0.20
    assert board_band("689009.SH") == 0.20
    assert board_band("430047.BJ") == 0.30
    assert board_band("830799.BJ") == 0.30


def test_limit_prices_round_to_the_tick():
    assert limit_prices(PREV, "600000.SH") == (9.00, 11.00)
    assert limit_prices(PREV_WIDE, "300308.SZ") == (80.00, 120.00)
    assert limit_prices(12.34, "301165.SZ") == (9.87, 14.81)


# ─── state classification ────────────────────────────────────
def test_normal_session():
    assert board_state(10.10, 10.50, 9.90, PREV, "600000.SH") == NORMAL


def test_sealed_limit_up_needs_no_trade_away_from_the_limit():
    assert board_state(11.00, 11.00, 11.00, PREV, "600000.SH") == UP_SEALED


def test_limit_up_open_that_then_trades_is_not_sealed():
    assert board_state(11.00, 11.00, 10.20, PREV, "600000.SH") == UP_OPEN


def test_sealed_limit_down():
    assert board_state(9.00, 9.00, 9.00, PREV, "600000.SH") == DOWN_SEALED


def test_limit_down_open_that_then_trades():
    assert board_state(9.00, 9.60, 8.90, PREV, "600000.SH") == DOWN_OPEN


def test_wide_board_uses_its_own_limits():
    # +8% is nowhere near a ChiNext limit; on the main board it would be a different story
    assert board_state(108.00, 109.00, 107.00, PREV_WIDE, "300308.SZ") == NORMAL
    assert board_state(120.00, 120.00, 120.00, PREV_WIDE, "300308.SZ") == UP_SEALED


def test_missing_prev_close_never_blocks():
    assert board_state(10.10, 10.50, 9.90, 0, "600000.SH") == NORMAL
    assert board_state(10.10, 10.50, 9.90, None, "600000.SH") == NORMAL


# ─── the policy ──────────────────────────────────────────────
def test_buy_is_blocked_in_every_limit_state():
    for state in (UP_SEALED, UP_OPEN, DOWN_SEALED, DOWN_OPEN):
        assert is_buy_blocked(state), f"{state} must block a BUY"
        assert buy_block_reason(state)
    assert not is_buy_blocked(NORMAL)
    assert buy_block_reason(NORMAL) is None


def test_only_a_sealed_limit_down_blocks_an_exit():
    assert is_sell_blocked(DOWN_SEALED)
    assert sell_block_reason(DOWN_SEALED)
    assert not is_sell_blocked(NORMAL)
    # a sealed limit-UP is good for a seller: buyers queue, we fill at the limit
    assert not is_sell_blocked(UP_SEALED)
    assert not is_sell_blocked(UP_OPEN)
    assert not is_sell_blocked(DOWN_OPEN)


def test_the_reasons_name_the_mechanism():
    up_sealed = buy_block_reason(UP_SEALED)
    up_open = buy_block_reason(UP_OPEN)
    dn_sealed = sell_block_reason(DOWN_SEALED)
    assert up_sealed and "no sellers" in up_sealed
    assert up_open and "top of the band" in up_open
    assert dn_sealed and "no buyers" in dn_sealed


# ─── The fill-price rule: the market fact, at the price a fill would print at ──
def test_a_buy_fill_at_the_limit_up_is_impossible():
    """No sellers at the limit-up, whatever the order asked for. prev close 10 -> limit-up 11."""
    why = fill_block_reason('buy', 11.00, 10.0, '600000.SH')
    assert why and 'no sellers' in why
    # just under the band is fine
    assert fill_block_reason('buy', 10.99, 10.0, '600000.SH') is None
    assert fill_block_reason('buy', 10.50, 10.0, '600000.SH') is None


def test_a_sell_fill_at_the_limit_down_is_impossible():
    """No buyers at the limit-down. prev close 10 -> limit-down 9."""
    why = fill_block_reason('sell', 9.00, 10.0, '600000.SH')
    assert why and 'no buyers' in why
    assert fill_block_reason('sell', 9.01, 10.0, '600000.SH') is None
    assert fill_block_reason('sell', 10.50, 10.0, '600000.SH') is None


def test_the_rule_is_symmetric_about_the_side():
    """A buy at the limit-DOWN is allowed (sellers are desperate) — that is a policy call, not physics.
    A sell at the limit-UP is allowed too (buyers queue there)."""
    assert fill_block_reason('buy', 9.00, 10.0, '600000.SH') is None
    assert fill_block_reason('sell', 11.00, 10.0, '600000.SH') is None


def test_the_bands_differ_per_board_for_the_fill_rule():
    """688347.SH moves +-20% (limit-up 12.00), so 11.00 is not a limit for it; 830799.BJ moves +-30%."""
    assert fill_block_reason('buy', 11.00, 10.0, '688347.SH') is None
    assert fill_block_reason('buy', 12.00, 10.0, '688347.SH')
    assert fill_block_reason('buy', 12.50, 10.0, '830799.BJ') is None
    assert fill_block_reason('buy', 13.00, 10.0, '830799.BJ')
    why = fill_block_reason('sell', 8.00, 10.0, '688347.SH')      # -20% band -> limit-down 8.00
    assert why and 'no buyers' in why


def test_the_fill_rule_uses_the_tick_rounded_band():
    """The exchange rounds the limit to 0.01, so the test must use that same number."""
    limit_down, limit_up = limit_prices(12.34, '600000.SH')
    assert (limit_down, limit_up) == (11.11, 13.57)
    assert fill_block_reason('buy', 13.57, 12.34, '600000.SH')
    assert not fill_block_reason('buy', 13.56, 12.34, '600000.SH')


def test_degenerate_inputs_do_not_block_a_fill():
    """A missing previous close must not silently refuse every trade."""
    assert fill_block_reason('buy', 11.0, None, '600000.SH') is None
    assert fill_block_reason('sell', 9.0, 0, '600000.SH') is None
    assert fill_block_reason('sell', 9.0, -5, '600000.SH') is None
    assert fill_block_reason('buy', None, 10.0, '600000.SH') is None


def test_the_guard_switch_defaults_on_and_can_be_turned_off(monkeypatch):
    monkeypatch.delenv('LIMIT_BOARD_GUARD', raising=False)
    assert board_guard_enabled() is True
    for off in ('0', 'false', 'no'):
        monkeypatch.setenv('LIMIT_BOARD_GUARD', off)
        assert board_guard_enabled() is False
    for on in ('1', 'true', 'yes'):
        monkeypatch.setenv('LIMIT_BOARD_GUARD', on)
        assert board_guard_enabled() is True


# ─── pre-open gap exit ───────────────────────────────────────
def test_an_open_at_or_below_the_stop_forces_the_exit_at_the_auction_price():
    """A stop the market has already gapped past is fiction: exit at the print."""
    sl = 9.50                     # prev close 10.00 -> limit-down 9.00
    why = gap_exit_reason(9.40, sl, PREV, '600000.SH')
    assert why and 'auction price' in why and '9.50' in why
    assert gap_exit_reason(9.50, sl, PREV, '600000.SH')                    # exactly at the stop
    assert gap_exit_reason(9.62, sl, PREV, '600000.SH', near_pct=0.02)     # 9.62 <= 9.69
    assert gap_exit_reason(9.70, sl, PREV, '600000.SH', near_pct=0.02) is None
    assert gap_exit_reason(10.50, sl, PREV, '600000.SH') is None


def test_the_gap_exit_ignores_an_open_comfortably_above_the_stop():
    assert gap_exit_reason(10.00, 9.50, PREV, '600000.SH', near_pct=0.005) is None
    assert gap_exit_reason(9.53, 9.50, PREV, '600000.SH', near_pct=0.005)   # 9.50*1.005 = 9.5475


def test_an_open_at_the_limit_down_is_requested_but_named_unfillable():
    """At the floor there are no buyers — the reason must say so, not promise a fill."""
    why = gap_exit_reason(9.00, 9.50, PREV, '600000.SH')
    assert why and 'limit-down' in why and 'unfillable' in why
    assert fill_block_reason('sell', 9.00, PREV, '600000.SH')               # and the fill is refused
    assert 'limit-down' in gap_exit_reason(80.00, 95.0, PREV_WIDE, '300750.SZ')    # 20% board
    assert 'limit-down' in gap_exit_reason(70.00, 95.0, PREV_WIDE, '830799.BJ')    # 30% board


def test_no_stop_means_only_the_floor_can_force_an_exit():
    assert gap_exit_reason(10.00, None, PREV, '600000.SH') is None
    assert gap_exit_reason(10.00, 0, PREV, '600000.SH') is None
    assert 'limit-down' in gap_exit_reason(9.00, None, PREV, '600000.SH')


def test_the_gap_exit_switch_and_its_buffer(monkeypatch):
    monkeypatch.delenv('GAP_EXIT', raising=False)
    monkeypatch.delenv('GAP_EXIT_NEAR_PCT', raising=False)
    assert gap_exit_enabled() is True
    assert gap_exit_near_pct() == 0.005
    monkeypatch.setenv('GAP_EXIT', '0')
    assert gap_exit_enabled() is False
    monkeypatch.setenv('GAP_EXIT_NEAR_PCT', '0.02')
    assert gap_exit_near_pct() == 0.02
    monkeypatch.setenv('GAP_EXIT_NEAR_PCT', 'not-a-number')
    assert gap_exit_near_pct() == 0.005
    # a zero buffer leaves only the at/below-stop case
    assert gap_exit_reason(9.55, 9.50, PREV, '600000.SH', near_pct=0.0) is None
    assert gap_exit_reason(9.50, 9.50, PREV, '600000.SH', near_pct=0.0)
