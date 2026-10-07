#!/usr/bin/env python3
"""
Main CLI entry point for mobile app trading workflow.

Orchestrates 3-phase daily trading:
- pre-market (< 09:30): sync check, stock picking, create smart orders, submit to app
- market (09:30-15:00): app auto-executes, periodic sync
- post-market (> 15:00): sync app to DB, generate trading report

Usage:
    python trading/runner.py                              # Auto-detect phase
    python trading/runner.py 20260627 --phase pre-market
    python trading/runner.py --phase all                  # All 3 phases
    python trading/runner.py --phase pre-market --submit  # Submit orders to app
    python trading/runner.py --dry-run                    # No mobile app ops
    python trading/runner.py --sync-only                  # Legacy: sync only
"""

import os, sys, json, asyncio, argparse, shutil
from datetime import datetime
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.utils.trading_calendar import calendar
from backtest.utils.limit_board import (
    board_band, board_state, buy_block_reason, fill_block_reason, limit_prices,
    gap_exit_enabled, gap_exit_reason,
)
from backtest.utils.strategy_env import apply_strategy_env, default_strategy, redact
import dotenv
dotenv.load_dotenv(os.path.expanduser('.env'), verbose=True)

from trading.guotai import GUOTAI_PACKAGE_NAME, login
from trading.sync_app_to_db import cron_sync_app_to_db, check_app_vs_db


# ─── Live strategy selection ─────────────────────────────────
# Same source of truth as the backtest CLI: .env DEFAULT_STRATEGY, then force that strategy's
# .env section into the environment. The live path previously hardcoded its strategy and never
# called apply_strategy_env, so the per-strategy sections governed backtests only — while the
# section values still reached this process by accident, because python-dotenv reads every line
# flat and so leaked REVIEW_COMPOUND_SIZING=true in from the ts_7AZ_96MA_flow_review section no
# matter which strategy was meant to run. Resolving it here makes live config deterministic and
# scopes out keys owned only by other strategies' sections.
# Wrapped in try/except on purpose: a malformed .env must never stop live trading.
LIVE_STRATEGY = 'ts_7AZ_96MA_flow_review'
_SCOPED_OUT: list[str] = []
try:
    LIVE_STRATEGY = default_strategy(fallback=LIVE_STRATEGY)
    _applied = apply_strategy_env(LIVE_STRATEGY, neutralized=_SCOPED_OUT)
    logger.info(f"Live strategy from .env DEFAULT_STRATEGY: {LIVE_STRATEGY}")
    if _applied:
        logger.info("Applied .env section [{}]: {}".format(
            LIVE_STRATEGY,
            ", ".join(f"{k}=<redacted>" if redact(k) else f"{k}={os.environ.get(k)}" for k in _applied),
        ))
    if _SCOPED_OUT:
        logger.info(f"Scoped out keys owned by other strategy sections: {', '.join(sorted(set(_SCOPED_OUT)))}")
except Exception as e:
    logger.warning(f"Could not apply .env strategy section ({e}); using {LIVE_STRATEGY}")


# ─── Auction submission ──────────────────────────────────────
# A-share call-auction timeline (exchange rules):
#   09:15-09:20  place AND cancel allowed. Because cancels are allowed, the displayed indicative
#                price/volume can be FAKE — large players place and pull orders to probe demand.
#                A quote read in this window is not evidence of anything.
#   09:20-09:25  place only; cancellation is LOCKED. The indicative price printed here is real
#                committed demand, and it is what converges to the open.
#   09:25:00     a single match of the 09:20-09:25 book at ONE price = the opening price. Orders
#                placed after this do NOT join the auction.
#   09:25-09:30  orders are accepted but do not enter the trading system; they queue for the 09:30
#                continuous session. Being early in that window buys TIME priority at 09:30, not
#                the auction's single price.
#
# Consequences for this module:
#   * quotes are only read from TRADING_QUOTE_TRUST_FROM (default 09:20), after the cancel lock;
#   * 09:15-09:25 is DORMANT — nothing is submitted, and no order is priced off the indicative
#     quote, which before the 09:20 cancel lock can be placed and pulled;
#   * the default is TRADING_SUBMIT_MODE=confirmed_open: wait for the 09:25 print, then create and
#     submit every order in 09:25-09:30 priced off that auction price — which IS the 09:30 open, the
#     price the backtest fills at, so the two stay comparable;
#   * TRADING_SUBMIT_MODE=auction is the legacy path: submit by TRADING_SUBMIT_BY (default 09:20) to
#     join the auction itself, bidding from the indicative quote.
SUBMIT_BY = os.getenv('TRADING_SUBMIT_BY', '0920')
SUBMIT_MODE = os.getenv('TRADING_SUBMIT_MODE', 'confirmed_open').strip().lower()  # confirmed_open | auction
QUOTE_TRUST_FROM = os.getenv('TRADING_QUOTE_TRUST_FROM', '0920')
AUCTION_CONFIRM_AT = os.getenv('TRADING_AUCTION_CONFIRM_AT', '0925')        # 09:25 print + settle delay
AUCTION_BUFFER_PCT = float(os.getenv('TRADING_AUCTION_BUFFER_PCT', '0.005'))
# 'indicative' (conservative bid) or 'limit_up' (guaranteed participation) — see _auction_buy_price.
BUY_LIMIT_MODE = os.getenv('TRADING_BUY_LIMIT_MODE', 'indicative').strip().lower()


def _hhmm_secs(hhmm: str, default: int) -> int:
    """'0920' -> seconds since midnight; `default` when unparseable."""
    try:
        return int(hhmm[:2]) * 3600 + int(hhmm[2:4]) * 60
    except (ValueError, IndexError, TypeError):
        return default


def _now_secs() -> int:
    n = datetime.now()
    return n.hour * 3600 + n.minute * 60 + n.second


def _quote_is_trustworthy(now_secs: int | None = None) -> bool:
    """False during 09:15-09:20, when the indicative price can be spoofed by cancellation."""
    t = _now_secs() if now_secs is None else now_secs
    lock = _hhmm_secs(QUOTE_TRUST_FROM, 9 * 3600 + 20 * 60)
    return t >= lock or t < 9 * 3600          # before the auction opens there is no quote anyway


def _submit_target_secs() -> int:
    """Clock time (seconds since midnight) at which orders may be created and submitted.

    Confirmed-open mode (the default) targets the 09:25 print plus a 5s settle — the 09:15-09:25
    window is dormant. Legacy auction mode targets TRADING_SUBMIT_BY so the order joins the auction.
    """
    if SUBMIT_MODE == 'confirmed_open':
        return _hhmm_secs(AUCTION_CONFIRM_AT, 9 * 3600 + 25 * 60) + 5
    default = '0920'
    return _hhmm_secs((SUBMIT_BY or default).strip(), _hhmm_secs(default, 9 * 3600 + 20 * 60))


def _wait_for_auction_window(dry_run: bool = False) -> None:
    """Sleep until the submission time: the 09:25 auction print by default, never 09:15-09:25."""
    import time
    now = datetime.now()
    target_secs = _submit_target_secs()
    target = now.replace(hour=target_secs // 3600, minute=target_secs % 3600 // 60, second=target_secs % 60,
                         microsecond=0)
    if now < target and now.hour < 12:
        wait_seconds = (target - now).total_seconds()
        logger.info(f"⏳ Waiting {wait_seconds:.0f}s until {target:%H:%M:%S} to submit "
                    f"(mode={SUBMIT_MODE}, auction closes 09:25:00 — orders after that do not join it)...")
        if dry_run:
            logger.info("[DRY RUN] Skipping actual time.sleep wait.")
        else:
            time.sleep(wait_seconds)


def _daily_band_pct(symbol: str) -> float:
    """Price-limit band for the board a symbol trades on — see backtest/utils/limit_board.py."""
    return board_band(symbol)


def _auction_buy_price(order: dict, rt_price, now_secs: int | None = None) -> tuple[str | None, str]:
    """(limit price, reason). A None price means SKIP the order — do not submit it.

    The bid is capped at the real limit-up, which is computed from the PREVIOUS CLOSE (``current_price``
    in the cli order). Two skips are decided here, both from the price-limit board policy:

      * the auction indicates a LIMIT-UP board — you cannot buy a sealed board (no sellers), and an
        open at the top of the band is a +10%/+20% entry versus the previous close;
      * the auction indicates a LIMIT-DOWN board — a crash open, which voids the momentum premise
        the pick was made on.

    A quote read before the 09:20 cancel lock is refused rather than trusted, because it can be spoofed.

    TRADING_BUY_LIMIT_MODE picks how the bid is sized. In an auction you pay the CLEARING price, not
    your limit, so the limit only decides whether you are IN the match:

      * ``indicative`` — indicative quote + buffer, floored at the plan. Fills the auction whenever it
        clears near the quote, including gap-up days the backtest refused;
      * ``limit_up`` — the whole band off the prev close: guaranteed participation, same gap-up cost;
      * ``plan_capped`` — never above the engine's own planned limit, so live trades the list the
        backtest traded (461/1052 = 43.8% of historical orders faced an open above the previous close
        and so never filled in the backtest). The price is matching misses.
    """
    symbol = order.get('symbol', '')
    suggested = float(order.get('buy_price') or 0)
    prev_close = float(order.get('current_price') or 0)   # cli stores the previous close here
    limit_up = prev_close * (1 + _daily_band_pct(symbol)) if prev_close > 0 else None

    if not _quote_is_trustworthy(now_secs) and BUY_LIMIT_MODE != 'limit_up':
        return None, (f"quote taken before {QUOTE_TRUST_FROM} is spoofable (09:15-09:20 cancels are "
                      f"allowed) and no limit-up bid mode is set")

    # Board gate on whatever the auction is showing right now.
    if rt_price and rt_price > 0 and prev_close > 0:
        state = board_state(rt_price, rt_price, rt_price, prev_close, symbol)
        block = buy_block_reason(state)
        if block:
            return None, f"auction board {state} at {rt_price:.2f} (prev {prev_close:.2f}): {block}"

    if BUY_LIMIT_MODE == 'limit_up' and limit_up:
        bid = limit_up
    elif BUY_LIMIT_MODE == 'plan_capped':
        # Match the backtest's own discipline: never bid above the strategy's planned limit. The
        # engine fills a BUY only when the open is at or below that limit, so bidding above it buys
        # days the backtest refused — 461/1052 (43.8%) of historical orders faced an open above the
        # previous close and so never filled there. Capping keeps live and backtest on the same
        # trade list; the price is that an order whose auction clears above the plan now misses,
        # which is the miss the backtest also takes.
        if rt_price and rt_price > 0:
            aggressive = rt_price * (1 + AUCTION_BUFFER_PCT)
            bid = min(aggressive, suggested) if suggested > 0 else aggressive
            if bid < rt_price:
                logger.info(f"Bid {bid:.2f} is below the indicative {rt_price:.2f} — this order only "
                            f"fills if the auction clears at or under the plan (backtest parity).")
        else:
            bid = suggested
    elif rt_price and rt_price > 0:
        bid = max(rt_price * (1 + AUCTION_BUFFER_PCT), suggested)
        if bid > suggested:
            logger.info(f"Bid {bid:.2f} to clear the auction (indicative {rt_price:.2f}, suggested {suggested:.2f})")
    else:
        logger.warning(f"No auction quote for {symbol}; falling back to suggested {suggested:.2f}")
        bid = suggested

    if limit_up:
        bid = min(bid, limit_up)
    return f"{bid:.2f}", f"bid {bid:.2f} (mode={BUY_LIMIT_MODE})"


def _confirmed_open_buy_price(order: dict, code: str) -> tuple[str | None, str]:
    """(limit price, reason) for TRADING_SUBMIT_MODE=confirmed_open.

    Uses the REAL opening price printed at 09:25, so both gates are certain rather than indicative:
    the regime `max_open_gap_pct` cap and the price-limit board. Orders go out in 09:25-09:30, which
    puts them at the front of the 09:30 continuous-session queue rather than in the auction.
    """
    try:
        from trading.pre_market_run import get_confirmed_open
        from backtest.utils.market_regime import detect_market_regime
    except Exception as e:                                     # pragma: no cover - import wiring
        return None, f"confirmed-open mode unavailable ({e})"

    symbol = order.get('symbol', '')
    prev_close = float(order.get('current_price') or 0)
    open_price = get_confirmed_open(code)
    if open_price is None or open_price <= 0:
        return None, "no confirmed open at 09:25 (quote backend unavailable)"
    if prev_close > 0:
        state = board_state(open_price, open_price, open_price, prev_close, symbol)
        block = buy_block_reason(state)
        if block:
            return None, f"confirmed open {open_price:.2f} board {state}: {block}"
        try:
            cap = float(detect_market_regime(datetime.now().strftime('%Y%m%d')).get('max_open_gap_pct', 0.05))
        except Exception:
            cap = 0.05
        gap = (open_price - prev_close) / prev_close
        if gap > cap:
            return None, (f"confirmed open {open_price:.2f} gaps {gap:.1%} > regime cap {cap:.1%}")
        limit_up = round(prev_close * (1 + _daily_band_pct(symbol)), 2)
        bid = min(round(open_price * (1 + AUCTION_BUFFER_PCT), 2), limit_up)
        return f"{bid:.2f}", f"confirmed open {open_price:.2f} -> bid {bid:.2f}"
    return f"{open_price:.2f}", f"confirmed open {open_price:.2f} (no prev close to gate on)"


# ─── Phase time guards ──────────────────────────────────────
def check_phase_time_allowed(phase: str, date: str) -> bool:
    """Check if trading day + current time allow this phase.
    Returns True if allowed, False if not (warns + skip).
    """
    if not calendar.is_trading_day(date):
        logger.warning(f"⚠️ {date} is not a trading day. Skipping {phase}.")
        return False

    now = datetime.now()
    today_str = now.strftime('%Y%m%d')
    # Only enforce time checks when date == today
    if date != today_str:
        return True

    if phase == 'pre-market':
        if now.hour > 9 or (now.hour == 9 and now.minute >= 30):
            logger.warning(f"⚠️ Pre-market not allowed after 09:30. Now: {now:%H:%M:%S}")
            return False
    elif phase == 'market':
        morning = (9, 30) <= (now.hour, now.minute) <= (11, 30)
        afternoon = (13, 0) <= (now.hour, now.minute) <= (15, 0)
        if not (morning or afternoon):
            logger.warning(f"⚠️ Market phase outside 09:30-11:30/13:00-15:00. Now: {now:%H:%M:%S}")
            return False
    elif phase == 'post-market':
        if now.hour < 15:
            logger.warning(f"⚠️ Post-market not allowed before 15:00. Now: {now:%H:%M:%S}")
            return False
    return True


# ─── Submit orders to app ────────────────────────────────────
def submit_orders_to_app(smart_orders_file: str, submit: bool = False, market_pattern: str = 'normal', dry_run: bool = False):
    """Read smart_orders JSON and submit orders to broker app via ADB."""
    from trading.create_order_tp_sl import create_tp_sl_order
    from trading.create_order_ordinary import create_ordinary_order
    from utils.tools import get_realtime_quote

    with open(smart_orders_file, 'r') as f:
        data = json.load(f)

    all_orders = data.get('smart_orders', [])
    total_new_buys = data.get('total_new_BUY_orders', data.get('total_orders', 0))

    buy_orders = all_orders[:total_new_buys]
    tp_sl_orders = all_orders[total_new_buys:]

    # 1. Wait for the submission window. auction mode (default): 09:20, so (a) the indicative quote
    # read below is post-cancel-lock and therefore real, and (b) every order still joins the auction
    # that matches at 09:25 and fills at the open. confirmed_open mode: wait for the 09:25 print
    # instead, then gate each BUY on the REAL open (see _confirmed_open_buy_price).
    if buy_orders or tp_sl_orders:
        logger.info(f"Submit mode: {SUBMIT_MODE} | buy limit mode: {BUY_LIMIT_MODE} | "
                    f"quote trust from: {QUOTE_TRUST_FROM}")
        _wait_for_auction_window(dry_run=dry_run)

    # 2. Held-position orders: the daily bracket, plus the scheduled exits. The engine emits a
    # scheduled exit with both trigger prices set to the auction/open price and a name ending in
    # _expired (expired / stagnation cut / ER trend / max-hold). These are the strategy's real
    # exits — they are not brackets, they are a sell instruction. The app holds them server-side
    # until they trigger or the day's validity expires.
    for order in tp_sl_orders:
        code = order['symbol'].split('.')[0]
        tp = str(order.get('sell_take_profit_price', 0))
        sl = str(order.get('sell_stop_loss_price', 0))
        qty = str(order.get('buy_quantity', 0))
        label = 'SCHEDULED EXIT' if str(order.get('name', '')).endswith('_expired') else 'TP/SL'
        # Pre-open gap exit, for every held name (not just scheduled exits): if the confirmed 09:25
        # print is already at/below the bracket's stop — or within GAP_EXIT_NEAR_PCT of it — sell at
        # that print. Carrying the position only lets a stop that the market has already passed fill
        # lower. A print at the limit-down asks for the same exit and cannot get it (no buyers); the
        # warnings below report that and the position carries.
        if label != 'SCHEDULED EXIT' and gap_exit_enabled():
            try:
                from trading.pre_market_run import get_confirmed_open
                _auction = get_confirmed_open(code)
                if _auction and float(_auction) > 0:
                    _why = gap_exit_reason(float(_auction),
                                           float(order.get('sell_stop_loss_price') or 0),
                                           float(order.get('current_price') or 0), order['symbol'])
                    if _why:
                        logger.warning(f"  ⚠️ {label} {code}: {_why} — replacing the bracket with a "
                                       f"sell at the auction price {float(_auction):.2f}.")
                        tp = sl = f"{float(_auction):.2f}"
            except Exception:
                pass
        # A scheduled exit means "sell at the auction price". The pre-market plan priced it off the
        # previous close; now that the 09:25 print is in, re-price it at that print so the order the
        # app holds is the price we actually expect. Brackets (TP/SL) are strategy levels relative to
        # cost and are deliberately left alone.
        if label == 'SCHEDULED EXIT' and SUBMIT_MODE == 'confirmed_open':
            try:
                from trading.pre_market_run import get_confirmed_open
                _auction = get_confirmed_open(code)
                _prev_for_band = float(order.get('current_price') or 0)
                if _auction and float(_auction) > 0 and _prev_for_band > 0:
                    _dn, _up = limit_prices(_prev_for_band, order['symbol'])
                    _px = min(max(float(_auction), _dn), _up)
                    if f"{_px:.2f}" != tp:
                        logger.info(f"  ℹ️ {label} {code}: re-priced at the 09:25 auction {_px:.2f} "
                                    f"(plan said TP={tp} SL={sl})")
                        tp = sl = f"{_px:.2f}"
            except Exception:
                pass
        # A SELL needs a buyer at the price it would print at. At the limit-down there are none, so
        # the exit cannot fill today and the position carries — whatever the trigger says. Checked
        # for the live quote (a scheduled exit is priced at the open) and for the order's own
        # triggers, since a stop sitting at/below the limit-down can never be reached either.
        try:
            from utils.tools import get_realtime_quote as _rtq
            _rt = _rtq(code)
            _prev = float(order.get('current_price') or 0)
            if _prev > 0:
                _limit_down, _limit_up = limit_prices(_prev, order['symbol'])
                for _px, _what in ((tp, 'take-profit'), (sl, 'stop-loss')):
                    try:
                        if 0 < float(_px) <= _limit_down:
                            logger.warning(f"  ⚠️ {label} {code}: {_what} {float(_px):.2f} sits at/below "
                                           f"the limit-down {_limit_down:.2f} — it can never fill, so the "
                                           f"position will carry instead.")
                    except (TypeError, ValueError):
                        pass
                if _rt and float(_rt) > 0:
                    _why = fill_block_reason('sell', float(_rt), _prev, order['symbol'])
                    if _why:
                        logger.warning(f"  ⚠️ {label} {code}: {_why} — this exit cannot fill today; "
                                       f"the position carries to the next session.")
        except Exception:
            pass
        try:
            create_tp_sl_order(code=code, tp_price=tp, sl_price=sl, quantity=qty, submit=submit, dry_run=dry_run)
            logger.info(f"  {'✅' if submit else 'ℹ️'} {label} {'submitted' if submit else 'filled (dry-run)'}: {code} TP={tp} SL={sl} x{qty}")
        except Exception as e:
            logger.error(f"  ❌ {label} failed: {code} — {e}")

    # 3. Submit BUY orders
    skipped_buys: list[tuple[str, str]] = []
    for order in buy_orders:
        quantity = str(order['buy_quantity'])
        if quantity == '0':
            continue
        code = order['symbol'].split('.')[0]

        # Price rule: indicative auction price + buffer in auction mode (and never above the real
        # limit-up); the confirmed 09:25 open in confirmed_open mode. A None price means the order
        # must NOT be submitted — the price-limit board or the gap gate refused it.
        if SUBMIT_MODE == 'confirmed_open':
            price, why = _confirmed_open_buy_price(order, code)
        else:
            price, why = _auction_buy_price(order, get_realtime_quote(code))

        if price is None:
            logger.warning(f"  ⏭️  SKIP BUY {code} {order.get('name', '')}: {why}")
            skipped_buys.append((code, why))
            continue
        logger.info(f"  BUY {code} {order.get('name', '')}: {why}")

        try:
            # All regimes: use ordinary limit buy order during the call auction to execute exactly at Open price
            create_ordinary_order(code=code, price=price, quantity=quantity, action='buy', submit=submit, dry_run=dry_run, skip_dup_check=True)
            logger.info(f"  {'✅' if submit else 'ℹ️'} Ordinary BUY {'submitted' if submit else 'filled (dry-run)'}: {code} @{price} x{quantity}")
        except Exception as e:
            logger.error(f"  ❌ BUY failed: {code} — {e}")

    if skipped_buys:
        logger.warning(f"  {len(skipped_buys)} BUY order(s) skipped on price-limit/gap rules: "
                       + "; ".join(f"{c} ({w})" for c, w in skipped_buys))


# ─── Pre-market phase ────────────────────────────────────────
async def run_pre_market(this_date, user_id, submit, dry_run, app_package_name):
    """Pre-market: sync check → pick stocks → create orders → (submit to app)."""
    import time
    logger.info(f"═══ PRE-MARKET for {this_date} ═══")
    start_time = time.perf_counter()

    _daily_dir = os.path.join('backtest', 'results', 'daily')
    os.makedirs(_daily_dir, exist_ok=True)
    os.environ['REPORT_DIR'] = _daily_dir

    # Step 1-2: Check app vs DB (fetches app state), sync if mismatch
    app_cash = None
    app_positions = []
    app_running_orders = []

    if not dry_run:
        check_result = await check_app_vs_db(user_id)
        app_cash = check_result.get('app_cash')
        app_positions = check_result.get('app_positions', [])
        app_running_orders = check_result.get('app_running_orders', [])

        if not check_result['db_matches_app']:
            logger.warning("DB does not match App — syncing app→DB...")
            await cron_sync_app_to_db(check_trading_day_and_time=False)

        logger.info(f"App: Cash={app_cash}, Positions={len(app_positions)}, "
                     f"RunningOrders={len(app_running_orders)}")

    # Step 3: Pick stocks + create smart orders
    # LIVE_STRATEGY comes from .env DEFAULT_STRATEGY (see the top of this module). The engine now
    # also runs its held-position block in live mode, so this call emits the daily bracket for
    # every holding plus a scheduled exit for any position whose hold window has closed.
    from backtest.engine import pick_orders_trading
    pick_orders_trading(
        start_date=this_date, end_date=this_date,
        user_id=user_id, src=LIVE_STRATEGY,
        backtest_search=False, backtest_ai=False,
        resume=False, is_live=True,
        app_cash=app_cash if app_cash is not None else (600000.0 if dry_run else None),
        app_positions=app_positions if app_positions else None,
        app_running_orders=app_running_orders if app_running_orders else None
    )

    # Step 4: Log summary
    smart_output_file = os.path.join(_daily_dir, f'smart_orders_{this_date}.json')
    if os.path.exists(smart_output_file):
        with open(smart_output_file, 'r') as f:
            data = json.load(f)

        all_orders = data.get('smart_orders', [])
        new_buys = data.get('total_new_BUY_orders', data.get('total_orders', 0))
        tp_sl_count = len(all_orders) - new_buys
        market_pattern = data.get('market_pattern', 'normal')

        logger.info(f"Regime: {market_pattern.upper()}")
        logger.info(f"BUY orders: {new_buys}, TP/SL orders: {tp_sl_count}")
        for o in all_orders[:new_buys]:
            if int(o.get('buy_quantity', 0)) > 0:
                logger.info(f"  BUY {o['symbol']}_{o['name']}: @{o['buy_price']} x{o['buy_quantity']}")
            else:
                logger.info(f"  SKIP BUY {o['symbol']}_{o['name']}: Already held or insufficient cash")
        for o in all_orders[new_buys:]:
            logger.info(f"  TP/SL {o['symbol']}_{o['name']}: "
                         f"TP={o['sell_take_profit_price']} SL={o['sell_stop_loss_price']} "
                         f"x{o['buy_quantity']}")

        # Step 5: Submit or dry-run orders to app
        submit_orders_to_app(smart_output_file, submit=submit, market_pattern=market_pattern, dry_run=dry_run)


    elapsed_time = time.perf_counter() - start_time
    logger.info(f"⏱️ Pre-market phase execution completed in {elapsed_time:.2f} seconds.")
    logger.info("✅ Pre-market complete")


# ─── Market phase ─────────────────────────────────────────────
async def run_market(this_date, user_id, dry_run, app_package_name):
    """Market: app auto-executes orders. Sync app→DB once."""
    logger.info(f"═══ MARKET for {this_date} ═══")
    logger.info("App is auto-executing smart orders via broker server-side triggers.")

    if not dry_run:
        logger.info("Syncing app → DB...")
        await cron_sync_app_to_db(check_trading_day_and_time=False)
        logger.info("✅ Market sync complete")
    else:
        logger.info("[DRY RUN] Skipping market sync")


# ─── Post-market phase ───────────────────────────────────────
async def run_post_market(this_date, user_id, dry_run, app_package_name):
    """Post-market: sync app→DB + backtest-style analysis + suggestions."""
    logger.info(f"═══ POST-MARKET for {this_date} ═══")

    # Step 1: Final sync — capture end-of-day state from app
    if not dry_run:
        logger.info("Final sync: app → DB...")
        await cron_sync_app_to_db(check_trading_day_and_time=False)
        logger.info("✅ Sync complete")

    # Step 2: Generate enhanced trading report with analysis + suggestions
    from trading.post_market_report import generate_trading_report
    report_file = generate_trading_report(this_date, user_id)
    logger.info(f"✅ Post-market complete. Report: {report_file}")


# ─── Phase router ────────────────────────────────────────────
async def run_daily_trading(this_date, phase, user_id, dry_run, submit, app_package_name):
    """Route to the correct phase function(s).

    ## Daily Automation Commands
    
    ### Crontab (recommended)
    - Pre-market: 09:00 (30 min before open)
    0 9 * * 1-5 cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py --phase pre-market --submit >> /tmp/cron_trading.log 2>&1
    
    - Market sync: every 60 min during session
    0 10-11,13-14 * * 1-5 cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py --phase market >> /tmp/cron_trading.log 2>&1
    
    - Post-market: 15:10 (10 min after close)
    10 15 * * 1-5 cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py --phase post-market >> /tmp/cron_trading.log 2>&1
    
    ## Manual (per-phase)
    - Pre-market (before 09:30)
    cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py --phase pre-market
    With submit:
    cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py --phase pre-market --submit
    
    - Market-hours sync
    cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py --phase market
    
    - Post-market
    cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py --phase post-market
    
    - All three phases sequentially
    cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py --phase all
    
    - Auto-detect phase by current time
    cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py
    
    - Specific date
    cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py 20260630 --phase pre-market
    
    - Dry run (no mobile app ops)
    cd /home/kasm-user/apps/imobile && .venv/bin/python trading/runner.py --phase pre-market --dry-run
    
    ## Reports generated
    | Phase       | Output                                         |
    |-------------|------------------------------------------------|
    | Pre-market  | backtest/results/daily/pre_market_YYYYMMDD.md        |
    | Post-market | backtest/results/daily/post_market_YYYYMMDD.md       |
    | Logs        | /tmp/cron_trading.log    
    """

    phases_to_run = []
    if phase == 'all':
        phases_to_run = ['pre-market', 'market', 'post-market']
    elif phase == 'auto':
        # Auto-detect based on current time
        now = datetime.now()
        if now.hour < 9 or (now.hour == 9 and now.minute < 30):
            phases_to_run = ['pre-market']
        elif now.hour < 15:
            phases_to_run = ['market']
        else:
            phases_to_run = ['post-market']
    else:
        phases_to_run = [phase]

    results = {}
    for p in phases_to_run:
        if not check_phase_time_allowed(p, this_date):
            results[p] = 'skipped_time_check'
            continue

        if p == 'pre-market':
            await run_pre_market(this_date, user_id, submit, dry_run, app_package_name)
        elif p == 'market':
            await run_market(this_date, user_id, dry_run, app_package_name)
        elif p == 'post-market':
            await run_post_market(this_date, user_id, dry_run, app_package_name)
        results[p] = 'ok'

    return {'status': 'ok', 'phases': results, 'date': this_date}


# ─── Utility ─────────────────────────────────────────────────
def cleanup_empty_trajectories():
    """Remove all empty directories at ./trajectories/."""
    for root, dirs, files in os.walk('trajectories'):
        for d in dirs:
            dirpath = os.path.join(root, d)
            if not os.listdir(dirpath):
                shutil.rmtree(dirpath)


# ─── Main ─────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description='Run daily trading workflow (3-phase: pre-market/market/post-market)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python trading/runner.py                              # Auto-detect phase
  python trading/runner.py 20260627 --phase pre-market  # Pre-market only
  python trading/runner.py --phase all                  # All 3 phases
  python trading/runner.py --phase pre-market --submit  # Submit to app
  python trading/runner.py --dry-run                    # No mobile app ops
  python trading/runner.py --sync-only                  # Legacy: sync only
        """
    )
    parser.add_argument('date', nargs='?', default=None,
                        help='Trading date YYYYMMDD (default: today)')
    parser.add_argument('--phase',
                        choices=['pre-market', 'market', 'post-market', 'auto', 'all'],
                        default='auto', help='Phase to run (default: auto)')
    parser.add_argument('--user-id', type=int, default=1)
    parser.add_argument('--submit', action='store_true', default=False,
                        help='Submit orders to broker app (default: no-submit)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Log only, no mobile app operations')
    parser.add_argument('--sync-only', action='store_true',
                        help='Legacy: sync data only')

    args = parser.parse_args()
    if not args.date:
        args.date = datetime.now().strftime('%Y%m%d')

    cleanup_empty_trajectories()
    if not args.dry_run:
        login()


    if args.sync_only:
        asyncio.run(cron_sync_app_to_db(check_trading_day_and_time=False))
    else:
        this_date = args.date
        if not calendar.is_trading_day(this_date):
            next_td = calendar.get_next_trading_day(this_date)
            logger.warning(f"⚠️ {this_date} is not a trading day. Using {next_td}")
            this_date = next_td
            if args.phase == 'auto':
                args.phase = 'pre-market'

        result = asyncio.run(run_daily_trading(
            this_date=this_date,
            phase=args.phase,
            user_id=args.user_id,
            dry_run=args.dry_run,
            submit=args.submit,
            app_package_name=GUOTAI_PACKAGE_NAME,
        ))

        print("\n" + "=" * 80)
        print("TRADING RESULT SUMMARY")
        print("=" * 80)
        print(json.dumps(result, indent=2, default=str))

if __name__ == '__main__':
    main()
