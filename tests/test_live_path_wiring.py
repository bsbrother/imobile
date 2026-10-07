"""Regression guards for the live trading path.

The bugs these lock down were all silent: nothing failed, live trading just stopped doing part of
its job. `pick_orders_trading` emits no sell orders at all, and the held-position block in
`create_smart_orders_from_picks` — the only thing that produces a daily bracket or a scheduled
exit — was gated behind `if app_positions is None:`, which is never true when runner.py calls it.
Result: 32 of 34 live pre-market runs emitted no TP/SL, none after 2026-07-13.

Most of that path is not unit-testable (ADB, broker app, live DB), so the wiring itself is
asserted against the source. If someone reintroduces the old gate or the hardcoded strategy,
these fail loudly instead of silently.
"""
import os
import re

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(REPO, 'backtest', 'engine.py')
RUNNER = os.path.join(REPO, 'trading', 'runner.py')


def _src(path):
    with open(path, encoding='utf-8') as f:
        return f.read()


# ─── Held-position block must run in live mode ───────────────────────────────
def test_held_position_block_is_not_gated_on_app_positions():
    """The old gate was `if app_positions is None:` — dead code in live, which broke brackets."""
    src = _src(ENGINE)
    assert 'if app_positions is None or is_live:' in src
    assert not re.search(r'^\s*if app_positions is None:\s*$', src, re.M), \
        "the old backtest-only gate is back"


def test_engine_still_treats_backtest_mode_identically():
    """`app_positions is None or is_live` must stay equivalent to the old gate in backtest mode,
    or the reproducible baseline changes. Parsed via AST because the surrounding comment quotes
    the old gate text on purpose."""
    import ast

    tree = ast.parse(_src(ENGINE))
    gates = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.If) and 'app_positions' in ast.unparse(node.test)
    ]
    assert gates, "no conditional mentions app_positions"
    held = [
        g for g in gates
        if 'app_positions is None' in ast.unparse(g.test) and 'is_live' in ast.unparse(g.test)
    ]
    assert held, "no gate fires the held-position block in both backtest and live mode"


# ─── Strategy resolution ─────────────────────────────────────────────────────
def test_runner_resolves_strategy_from_env_like_the_backtest_cli():
    src = _src(RUNNER)
    assert 'default_strategy(' in src, "runner must read .env DEFAULT_STRATEGY"
    assert 'apply_strategy_env(' in src, "runner must force the strategy's .env section"
    assert "neutralized=" in src, "runner must scope out other strategies' section keys"


def test_runner_does_not_hardcode_a_strategy_in_the_pick_call():
    src = _src(RUNNER)
    call = src[src.index('pick_orders_trading('):]
    call = call[:call.index(')')]
    assert 'src=LIVE_STRATEGY' in call
    assert 'ts_7AZ_96MA_flow_review' not in call, "strategy is hardcoded again"


def test_live_strategy_resolution_is_failure_tolerant():
    """A malformed .env must never stop live trading."""
    src = _src(RUNNER)
    block = src[src.index('LIVE_STRATEGY = '):src.index('# ─── Auction submission')]
    assert 'except Exception' in block


# ─── Auction submission window ───────────────────────────────────────────────
def test_runner_submits_inside_the_call_auction():
    src = _src(RUNNER)
    # 09:20, not 09:15: before the cancel lock the indicative quote can be spoofed, and the order
    # still joins the auction that matches at 09:25 (so it fills at the open).
    assert "TRADING_SUBMIT_BY', '0920'" in src, "submission must start after the 09:20 cancel lock"
    assert "TRADING_SUBMIT_BY', '0915'" not in src, "09:15 means reading a spoofable quote"
    assert 'minute=24' not in src, "the 09:24:00 deadline is back (only ~60s for 15-20 orders)"
    assert '_wait_for_auction_window' in src
    # the wait must cover brackets too, not just BUYs
    body = src[src.index('def submit_orders_to_app'):]
    body = body[:body.index('# 3. Submit BUY orders')]
    assert 'if buy_orders or tp_sl_orders:' in body


def test_runner_labels_scheduled_exits():
    """Scheduled exits are sell instructions, not brackets — they must be visible as such."""
    src = _src(RUNNER)
    assert "_expired" in src
    assert 'SCHEDULED EXIT' in src


# ─── Auction price helper (behavioural, if the module imports) ───────────────
@pytest.fixture(scope='module')
def runner_mod():
    try:
        from trading import runner
    except Exception as e:  # pragma: no cover - depends on ADB deps being installed
        pytest.skip(f"trading.runner not importable here: {e}")
    return runner


# 09:21 — inside the auction, after the 09:20 cancel lock. Passed explicitly so these tests do not
# depend on the wall clock (a run at 09:10 would otherwise refuse the quote and fail).
T_AFTER_LOCK = 9 * 3600 + 21 * 60
T_BEFORE_LOCK = 9 * 3600 + 16 * 60


def test_auction_price_bids_indicative_plus_buffer(runner_mod):
    price, why = runner_mod._auction_buy_price(
        {'symbol': '600000.SH', 'buy_price': 10.0}, 10.0, T_AFTER_LOCK)
    assert float(price) == pytest.approx(10.0 * (1 + runner_mod.AUCTION_BUFFER_PCT), abs=0.01)
    assert why


def test_auction_price_honours_a_suggested_price_above_the_indicative(runner_mod):
    """The plan's own limit is a floor while it stays inside the board's band."""
    price, _ = runner_mod._auction_buy_price(
        {'symbol': '600000.SH', 'buy_price': 10.5}, 10.0, T_AFTER_LOCK)
    assert float(price) == pytest.approx(10.5)


def test_auction_price_is_capped_at_the_boards_daily_band(runner_mod):
    """A stale plan above the real limit-up must not produce an out-of-band bid."""
    # 600xxx = ±10%; previous close 10 -> limit-up 11
    price, _ = runner_mod._auction_buy_price(
        {'symbol': '600000.SH', 'buy_price': 30.0, 'current_price': 10.0}, 10.0, T_AFTER_LOCK)
    assert float(price) == pytest.approx(11.0)
    # 688xxx = ±20% band (科创板), the board of the sample live order 688347.SH
    price, _ = runner_mod._auction_buy_price(
        {'symbol': '688347.SH', 'buy_price': 30.0, 'current_price': 10.0}, 10.0, T_AFTER_LOCK)
    assert float(price) == pytest.approx(12.0)
    assert runner_mod._daily_band_pct('300308.SZ') == pytest.approx(0.20)
    assert runner_mod._daily_band_pct('600919.SH') == pytest.approx(0.10)
    assert runner_mod._daily_band_pct('830799.BJ') == pytest.approx(0.30)


def test_indicative_mode_bids_just_over_the_quote(runner_mod):
    """Default mode stays conservative — the real sample row (688347.SH, prev close 324)."""
    order = {'symbol': '688347.SH', 'buy_price': 279.18, 'current_price': 324.0}
    price, _ = runner_mod._auction_buy_price(order, 324.0, T_AFTER_LOCK)
    assert float(price) == pytest.approx(324.0 * (1 + runner_mod.AUCTION_BUFFER_PCT), abs=0.01)
    assert float(price) < 324.0 * 1.20, "default mode must not bid the whole band"


def test_limit_up_mode_bids_the_full_band(runner_mod, monkeypatch):
    """TRADING_BUY_LIMIT_MODE=limit_up trades the miss risk for guaranteed participation."""
    monkeypatch.setattr(runner_mod, 'BUY_LIMIT_MODE', 'limit_up')
    order = {'symbol': '688347.SH', 'buy_price': 279.18, 'current_price': 324.0}
    price, _ = runner_mod._auction_buy_price(order, 324.0, T_AFTER_LOCK)
    assert float(price) == pytest.approx(324.0 * 1.20)


def test_plan_capped_mode_never_bids_above_the_plan(runner_mod, monkeypatch):
    """The backtest fills a BUY only when open <= its plan limit; capping keeps the same trade list."""
    monkeypatch.setattr(runner_mod, 'BUY_LIMIT_MODE', 'plan_capped')
    order = {'symbol': '600000.SH', 'buy_price': 10.0, 'current_price': 10.0}
    # Auction already above the plan -> bid the plan, so the order misses unless it clears lower.
    price, _ = runner_mod._auction_buy_price(order, 10.50, T_AFTER_LOCK)
    assert float(price) == pytest.approx(10.0)
    # Auction below the plan -> still bid just over the quote so it can fill.
    price, _ = runner_mod._auction_buy_price(order, 9.50, T_AFTER_LOCK)
    assert float(price) == pytest.approx(9.50 * (1 + runner_mod.AUCTION_BUFFER_PCT), abs=0.01)


def test_auction_price_falls_back_when_no_quote(runner_mod):
    price, _ = runner_mod._auction_buy_price({'symbol': '600000.SH', 'buy_price': 9.87}, None, T_AFTER_LOCK)
    assert price == '9.87'
    price, _ = runner_mod._auction_buy_price({'symbol': '600000.SH', 'buy_price': 9.87}, 0, T_AFTER_LOCK)
    assert price == '9.87'


# ─── Auction phase: when a quote may be trusted, and what must not be bought ──
def test_quote_before_the_cancel_lock_is_refused(runner_mod):
    """09:15-09:20 allows cancellation, so the displayed price can be spoofed — never bid on it."""
    assert runner_mod._quote_is_trustworthy(T_BEFORE_LOCK) is False
    assert runner_mod._quote_is_trustworthy(T_AFTER_LOCK) is True
    price, why = runner_mod._auction_buy_price(
        {'symbol': '600000.SH', 'buy_price': 10.0, 'current_price': 10.0}, 10.0, T_BEFORE_LOCK)
    assert price is None, "a pre-lock quote must not become an order"
    assert 'spoof' in why


def test_limit_up_mode_still_works_before_the_lock(runner_mod, monkeypatch):
    """limit_up mode reads no quote at all, so the cancel-lock objection does not apply to it."""
    monkeypatch.setattr(runner_mod, 'BUY_LIMIT_MODE', 'limit_up')
    order = {'symbol': '600000.SH', 'buy_price': 10.0, 'current_price': 10.0}
    price, _ = runner_mod._auction_buy_price(order, 10.0, T_BEFORE_LOCK)
    assert float(price) == pytest.approx(11.0)


def test_a_limit_up_board_is_not_bought(runner_mod):
    """Sealed limit-up: no sellers. A limit-up open: you would pay the top of the band."""
    order = {'symbol': '600000.SH', 'buy_price': 10.0, 'current_price': 10.0}   # limit-up 11.00
    price, why = runner_mod._auction_buy_price(order, 11.00, T_AFTER_LOCK)
    assert price is None and 'no sellers' in why
    # limit-down 9.00: a crash open, which voids the momentum premise the pick was made on — the
    # reason names the crash, not "no buyers" (that wording is for a trapped SELL).
    price, why = runner_mod._auction_buy_price(order, 9.00, T_AFTER_LOCK)
    assert price is None and 'crash open' in why and 'down_sealed' in why


def test_a_normal_quote_still_passes_the_board_gate(runner_mod):
    order = {'symbol': '600000.SH', 'buy_price': 10.0, 'current_price': 10.0}
    price, _ = runner_mod._auction_buy_price(order, 10.20, T_AFTER_LOCK)
    assert price is not None


# ─── Wiring: the gaps these changes close must not silently reopen ───────────
def test_default_submit_window_is_after_the_cancel_lock():
    """09:15 default meant the indicative quote was read while cancels were still allowed."""
    src = _src(RUNNER)
    assert "os.getenv('TRADING_SUBMIT_BY', '0920')" in src
    assert "os.getenv('TRADING_SUBMIT_BY', '0915')" not in src, "back to reading spoofable quotes"
    assert "os.getenv('TRADING_QUOTE_TRUST_FROM', '0920')" in src


def test_the_default_is_the_0925_auction_price_not_an_indicative_quote(runner_mod, monkeypatch):
    """09:15-09:25 does nothing: the default waits for the 09:25 print, then acts in 09:25-09:30."""
    monkeypatch.setattr(runner_mod, 'SUBMIT_MODE', 'confirmed_open')
    monkeypatch.setattr(runner_mod, 'AUCTION_CONFIRM_AT', '0925')
    assert runner_mod._submit_target_secs() == 9 * 3600 + 25 * 60 + 5
    assert "os.getenv('TRADING_SUBMIT_MODE', 'confirmed_open')" in _src(RUNNER), \
        "an indicative-quote submission must not be the default"
    monkeypatch.setattr(runner_mod, 'SUBMIT_MODE', 'auction')      # legacy path still joins the auction
    monkeypatch.setattr(runner_mod, 'SUBMIT_BY', '0920')
    assert runner_mod._submit_target_secs() == 9 * 3600 + 20 * 60


def test_a_scheduled_exit_is_re_priced_at_the_auction():
    """A scheduled exit means "sell at the auction price", so it is re-priced at the 09:25 print."""
    src = _src(RUNNER)
    assert "label == 'SCHEDULED EXIT' and SUBMIT_MODE == 'confirmed_open'" in src
    assert 're-priced at the 09:25 auction' in src


def test_the_backtest_fills_a_gapped_through_exit_at_the_auction_price():
    """A stop at 97 does not fill at 97 when the market opens at 90 — the open IS the auction price."""
    src = _src(ENGINE)
    assert 'SELL_AT_OPEN_WHEN_GAPPED' in src
    assert "reason = 'stop_loss (gap down)'" in src
    assert "reason = 'take_profit (gap up)'" in src
    assert 'sell_price = take_profit if tp_hit else stop_loss' in src      # legacy path kept reachable


def test_the_pre_open_gap_exit_is_wired_on_both_paths():
    """Refresh the bracket before the open, then force-exit what the print already reached."""
    engine = _src(ENGINE)
    runner = _src(RUNNER)
    pmr = _src(os.path.join(REPO, 'trading', 'pre_market_run.py'))
    # one bracket implementation, shared — so live re-calibration cannot drift from the backtest
    assert 'from backtest.utils.tp_sl import adaptive_tp_sl as _adaptive_tp_sl' in engine
    assert 'def _adaptive_tp_sl' not in engine, 'a second bracket implementation came back'
    # backtest: the gap exit fires without the session ever trading through the trigger
    assert "or '_expired' in name or gap_exit" in engine
    assert "reason = 'gap_exit'" in engine
    # live: both entry points replace the bracket with a sell at the auction print
    assert 'gap_exit_reason' in runner and 'gap_exit_reason' in pmr
    assert 'replacing the bracket with a' in runner
    assert 'force-selling at the auction price' in pmr


def test_submit_loop_skips_a_refused_price():
    """A None price must skip the order — never reach create_ordinary_order."""
    src = _src(RUNNER)
    assert 'if price is None:' in src and 'skipped_buys.append' in src
    loop = src.split('for order in buy_orders:')[1].split('# ─── Pre-market phase')[0]
    assert loop.index('if price is None:') < loop.index('create_ordinary_order('), \
        "the skip must be decided before the order is created"


def test_confirmed_open_mode_exists_and_reads_the_real_open():
    src = _src(RUNNER)
    assert '_confirmed_open_buy_price' in src
    assert 'get_confirmed_open' in src
    assert 'max_open_gap_pct' in src, "the confirmed-open gate must include the regime gap cap"


def test_engine_guards_both_sides_with_the_shared_policy():
    """The backtest must refuse the same trades the live path refuses."""
    src = _src(ENGINE)
    assert 'from backtest.utils.limit_board import' in src
    assert src.count('board_state(') >= 2 and src.count('buy_block_reason(') >= 1
    assert src.count('sell_block_reason(') >= 1
    assert src.count('board_guard_enabled()') >= 2, "both sides must read the guard switch"
    assert 'board_guard_enabled' in src.split('import')[-1] or 'board_guard_enabled,' in src
    assert 'buy_block_reason' in src.split('def check_order_execution')[1], \
        "the guards belong inside check_order_execution, the single fill funnel"


def test_legacy_load_guards_survive_behind_the_env_flag():
    """LIMIT_BOARD_GUARD=0 must restore the old behaviour for an A/B against a pinned run."""
    src = _src(ENGINE)
    assert "reason': 'locked_limit_down'" in src
    assert 'hit limit up' in src


# ─── The market rule is enforced at the FILL, not just in the caller's pre-checks ──
def test_engine_refuses_an_impossible_fill_in_the_fill_helpers():
    """A buy fill at the limit-up has no sellers; a sell fill at the limit-down has no buyers.
    Checked inside execute_buy_order/execute_sell_order so no call site can forget it."""
    src = _src(ENGINE)
    assert "fill_block_reason('sell', sell_price, prev_close, symbol)" in src
    assert "fill_block_reason('buy', buy_price, prev_close, symbol)" in src
    assert 'board_guard_enabled()' in src


def test_every_engine_sell_branch_passes_prev_close():
    """A branch that forgets prev_close silently loses the guard — pin the count."""
    src = _src(ENGINE)
    sells = src.count('success = execute_sell_order(')
    assert sells == 6, f"expected the 6 sell branches, found {sells}"
    assert src.count('order_number, reason, prev_close=prev_close') == sells
    assert src.count('prev_close=prev_close') == sells + 1, 'the buy call site must pass it too'


def test_live_exit_flags_a_trigger_that_can_never_fill():
    """At the limit-down there are no buyers: warn on the live quote AND on a trigger that sits
    at/below the floor, so an unfillable daily bracket is visible instead of silently inert."""
    src = _src(RUNNER)
    assert "fill_block_reason('sell'" in src
    assert 'limit_prices(' in src
    assert 'can never fill' in src
    assert 'cannot fill today' in src


def test_pre_market_run_uses_the_shared_band_table():
    """The second live entry point carried its own band logic (and missed 689)."""
    path = os.path.join(REPO, 'trading', 'pre_market_run.py')
    src = _src(path)
    assert 'board_band(sym)' in src
    assert "limit_ratio = 0.20" not in src, 'the duplicated band table is back'
    assert 'buy_block_reason(board)' in src, 'the live BUY must consult the board policy'


def test_pre_market_run_gates_the_board_before_the_open_is_confirmed():
    """get_confirmed_open returns None before 09:25, so gating only on it left the whole 09:20
    submission window — the normal window — unchecked. It must fall back to the live indicative
    quote, and only after the cancel lock, or the gate is decorative."""
    src = _src(os.path.join(REPO, 'trading', 'pre_market_run.py'))
    assert 'gate_price, gate_note = confirmed_open' in src
    assert 'TRADING_QUOTE_TRUST_FROM' in src
    assert 'get_realtime_quote' in src
    assert 'board_state(gate_price' in src
    assert 'no trustworthy price to board-check' in src


def test_pre_market_run_warns_when_a_force_sell_stop_is_untradeable():
    src = _src(os.path.join(REPO, 'trading', 'pre_market_run.py'))
    assert 'limit_prices(' in src
    assert 'it cannot fill at the floor' in src


def test_both_rules_are_enforced_on_both_paths():
    """The whole matrix, in one place: a BUY at the limit-up and a SELL at the limit-down.

    Backtest — refused inside the fill functions, so no branch can book them.
    Live      — a BUY is SKIPPED before submission (both entry points); a SELL cannot fill at the
                floor because the exchange has no buyers there, so the live duty is to warn and let
                the position carry, which is exactly what the backtest now mimics.
    """
    engine = _src(ENGINE)
    runner = _src(RUNNER)
    pmr = _src(os.path.join(REPO, 'trading', 'pre_market_run.py'))

    # backtest: both sides, at the fill
    assert "fill_block_reason('buy', buy_price, prev_close, symbol)" in engine
    assert "fill_block_reason('sell', sell_price, prev_close, symbol)" in engine

    # live BUY: both entry points consult the board, and runner skips rather than submitting
    assert 'buy_block_reason(' in runner and 'buy_block_reason(' in pmr
    assert 'if price is None:' in runner and 'skipped_buys.append' in runner
    assert pmr.count('board_block') >= 2, 'pre_market_run must skip on the board verdict'

    # live SELL: flagged on both entry points, position carried
    assert "fill_block_reason('sell'" in runner
    assert 'limit_prices(' in runner and 'limit_prices(' in pmr
    assert 'carries' in runner and 'carries' in pmr
