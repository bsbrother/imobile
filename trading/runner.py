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
# An order placed during the 09:15-09:25 call auction clears at the auction price, which is the
# open — the fill the backtest assumes on both sides. Submission used to start at 09:24:00,
# leaving ~60s for 15-20 ADB-driven orders at 5-10s each, so the tail missed the auction and
# filled in continuous trading instead. TRADING_SUBMIT_BY (HHMM) sets when submission starts.
SUBMIT_BY = os.getenv('TRADING_SUBMIT_BY', '0915')
AUCTION_BUFFER_PCT = float(os.getenv('TRADING_AUCTION_BUFFER_PCT', '0.005'))


def _wait_for_auction_window(dry_run: bool = False) -> None:
    """Sleep until the configured auction submission time (default 09:15)."""
    import time
    now = datetime.now()
    hhmm = (SUBMIT_BY or '0915').strip()
    try:
        target = now.replace(hour=int(hhmm[:2]), minute=int(hhmm[2:4]), second=0, microsecond=0)
    except (ValueError, IndexError):
        logger.warning(f"Invalid TRADING_SUBMIT_BY={SUBMIT_BY!r}; falling back to 09:15")
        target = now.replace(hour=9, minute=15, second=0, microsecond=0)
    if now < target and now.hour < 12:
        wait_seconds = (target - now).total_seconds()
        logger.info(f"⏳ Waiting {wait_seconds:.0f}s until {target:%H:%M} to submit inside the call auction...")
        if dry_run:
            logger.info("[DRY RUN] Skipping actual time.sleep wait.")
        else:
            time.sleep(wait_seconds)


def _daily_band_pct(symbol: str) -> float:
    """Price-limit band for the board a symbol trades on.

    科创板 688/689 and 创业板 300/301 move ±20%, 北交所 ±30%, everything else ±10%. ST names are
    ±5% but that is not discoverable from the code. This is only a sanity bound on the auction bid
    (in an auction you pay the clearing price, not your limit, so the limit only decides whether
    you are in the match); the real ceiling is the exchange's and the broker rejects beyond it.
    """
    c = (symbol or '').split('.')[0]
    if c.startswith(('688', '689', '300', '301')):
        return 0.20
    if c.startswith(('4', '8')):
        return 0.30
    return 0.10


def _auction_buy_price(order: dict, rt_price) -> str:
    """Limit price for a BUY that must clear in the call auction.

    Bid the indicative auction price plus a small buffer, so convergence drift does not leave the
    order unfilled. Never below the engine's suggested price, never above the board's daily band.
    Falls back to the suggested price if no quote is available.
    """
    suggested = float(order.get('buy_price') or 0)
    if rt_price and rt_price > 0:
        cap = rt_price * (1 + _daily_band_pct(order.get('symbol', '')))
        bid = min(max(rt_price * (1 + AUCTION_BUFFER_PCT), suggested), cap)
        if bid > suggested:
            logger.info(f"Bid {bid:.2f} to clear the auction (indicative {rt_price:.2f}, suggested {suggested:.2f})")
        return f"{bid:.2f}"
    logger.warning(f"No auction quote for {order.get('symbol')}; falling back to suggested {suggested:.2f}")
    return str(order['buy_price'])


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

    # 1. Wait for the call-auction window (default 09:15) so everything below clears at the
    # auction price = the open. Previously the wait targeted 09:24:00 and only guarded the BUYs,
    # which left roughly a minute for 15-20 ADB-driven orders.
    if buy_orders or tp_sl_orders:
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
        try:
            create_tp_sl_order(code=code, tp_price=tp, sl_price=sl, quantity=qty, submit=submit, dry_run=dry_run)
            logger.info(f"  {'✅' if submit else 'ℹ️'} {label} {'submitted' if submit else 'filled (dry-run)'}: {code} TP={tp} SL={sl} x{qty}")
        except Exception as e:
            logger.error(f"  ❌ {label} failed: {code} — {e}")

    # 3. Submit BUY orders
    for order in buy_orders:
        quantity = str(order['buy_quantity'])
        if quantity == '0':
            continue
        code = order['symbol'].split('.')[0]

        # Price to clear in the auction: indicative open + buffer, never above limit-up.
        price = _auction_buy_price(order, get_realtime_quote(code))

        try:
            # All regimes: use ordinary limit buy order during the call auction to execute exactly at Open price
            create_ordinary_order(code=code, price=price, quantity=quantity, action='buy', submit=submit, dry_run=dry_run, skip_dup_check=True)
            logger.info(f"  {'✅' if submit else 'ℹ️'} Ordinary BUY {'submitted' if submit else 'filled (dry-run)'}: {code} @{price} x{quantity}")
        except Exception as e:
            logger.error(f"  ❌ BUY failed: {code} — {e}")


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
