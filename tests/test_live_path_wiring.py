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
    assert "TRADING_SUBMIT_BY', '0915'" in src, "submission must start in the auction window"
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


def test_auction_price_bids_indicative_plus_buffer(runner_mod):
    price = runner_mod._auction_buy_price({'symbol': '600000.SH', 'buy_price': 10.0}, 10.0)
    assert float(price) == pytest.approx(10.0 * (1 + runner_mod.AUCTION_BUFFER_PCT), abs=0.01)


def test_auction_price_honours_a_suggested_price_above_the_indicative(runner_mod):
    """The plan's own limit is a floor while it stays inside the board's band."""
    price = runner_mod._auction_buy_price({'symbol': '600000.SH', 'buy_price': 10.5}, 10.0)
    assert float(price) == pytest.approx(10.5)


def test_auction_price_is_capped_at_the_boards_daily_band(runner_mod):
    """A stale plan above the real limit-up must not produce an out-of-band bid."""
    # 600xxx = ±10%; previous close 10 -> limit-up 11
    price = runner_mod._auction_buy_price(
        {'symbol': '600000.SH', 'buy_price': 30.0, 'current_price': 10.0}, 10.0)
    assert float(price) == pytest.approx(11.0)
    # 688xxx = ±20% band (科创板), the board of the sample live order 688347.SH
    price = runner_mod._auction_buy_price(
        {'symbol': '688347.SH', 'buy_price': 30.0, 'current_price': 10.0}, 10.0)
    assert float(price) == pytest.approx(12.0)
    assert runner_mod._daily_band_pct('300308.SZ') == pytest.approx(0.20)
    assert runner_mod._daily_band_pct('600919.SH') == pytest.approx(0.10)


def test_indicative_mode_bids_just_over_the_quote(runner_mod):
    """Default mode stays conservative — the real sample row (688347.SH, prev close 324)."""
    order = {'symbol': '688347.SH', 'buy_price': 279.18, 'current_price': 324.0}
    price = runner_mod._auction_buy_price(order, 324.0)
    assert float(price) == pytest.approx(324.0 * (1 + runner_mod.AUCTION_BUFFER_PCT), abs=0.01)
    assert float(price) < 324.0 * 1.20, "default mode must not bid the whole band"


def test_limit_up_mode_bids_the_full_band(runner_mod, monkeypatch):
    """TRADING_BUY_LIMIT_MODE=limit_up trades the miss risk for guaranteed participation."""
    monkeypatch.setattr(runner_mod, 'BUY_LIMIT_MODE', 'limit_up')
    order = {'symbol': '688347.SH', 'buy_price': 279.18, 'current_price': 324.0}
    price = runner_mod._auction_buy_price(order, 324.0)
    assert float(price) == pytest.approx(324.0 * 1.20)


def test_auction_price_falls_back_when_no_quote(runner_mod):
    assert runner_mod._auction_buy_price({'symbol': '600000.SH', 'buy_price': 9.87}, None) == '9.87'
    assert runner_mod._auction_buy_price({'symbol': '600000.SH', 'buy_price': 9.87}, 0) == '9.87'
