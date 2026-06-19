#!/usr/bin/env python3
"""
Main CLI entry point for mobile app trading workflow.

Orchestrates base on short-term, hot sectors turn on A-Shares real-time market trading, sync data from mobile app to DB for web.
- pre-market: stock picking, analysis create/adjust smart orders.
- market session: order execution
- post-market: reporting,
- mobile-to-DB data sync.

Notes:
- The trading date(last_updated) < 2026-02-24 in holding_stocks and smart_orders table in DB are all legacy(history) stocks.
  They follow the strategy in backtest/strategies/ts_history.py.
- From 2026-02-04, will be start new stocks use normal strategie ts_auto(auto-select ts_ai, ts_dc, ts_go, ts_hma, ts_longup) to pick stocks.
- Current(last_updated < 2026-02-24) in holding_stocks are all legacy(history) stocks,
  they follow the strategy in backtest/strategies/ts_history.py to trading(SELL).

Usage:
    python trading/runner.py                          # Auto-detect phase
    python trading/runner.py 20260214 --phase pre-market
    python trading/runner.py --dry-run                # No mobile app operations
    python trading/runner.py --sync-only              # Legacy: sync data only

Crontab examples:
    # Runs every 30 minutes on weekdays (Mon-Fri) during market hours
    30 9   * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py >> /tmp/cron_trading.log 2>&1 &
    0,30 10-11 * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py >> /tmp/cron_trading.log 2>&1 &
    0,30 13-14 * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py >> /tmp/cron_trading.log 2>&1 &
    0,30 15  * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py >> /tmp/cron_trading.log 2>&1 &
    0    16  * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py >> /tmp/cron_trading.log 2>&1 &
"""

import os
import sys
import json
import asyncio
import argparse
import shutil
from datetime import datetime
from loguru import logger

# Ensure project root is in path (must come before any project imports)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.utils.trading_calendar import calendar

import dotenv
dotenv.load_dotenv(os.path.expanduser('.env'), verbose=True)

from trading.guotai import (
    GUOTAI_PACKAGE_NAME,
    login,
    cron_sync_app_to_db,
)


async def run_daily_trading(this_date, phase, user_id, dry_run, app_package_name):
    """Run daily trading workflow for a specific phase."""
    # Route daily trading output to backtest/results/daily/ to avoid
    # polluting backtest result directories.
    _daily_dir = os.path.join('backtest', 'results', 'daily')
    os.makedirs(_daily_dir, exist_ok=True)
    os.environ['REPORT_DIR'] = _daily_dir

    from backtest.engine import pick_orders_trading

    logger.info(f"Starting {phase} for {this_date} (user={user_id}, dry_run={dry_run})")

    if phase == 'pre-market':
        # Pick stocks and create smart orders
        logger.info("Picking stocks and creating smart orders...")
        try:
            pick_orders_trading(
                start_date=this_date,
                end_date=this_date,
                user_id=user_id,
                src='ts_7AZ',
                backtest_search=False,
                backtest_ai=False,
                resume=False,
            )
            logger.info("✅ Pre-market stock picking + smart orders complete")
        except Exception as e:
            logger.error(f"❌ Pre-market failed: {e}")
            raise

    elif phase == 'market':
        # Execute pending smart orders
        logger.info("Executing pending smart orders...")
        # TODO: call order execution

    elif phase == 'post-market':
        # Sync results from mobile app to DB
        logger.info("Syncing post-market data...")
        await cron_sync_app_to_db(check_trading_day_and_time=False)

    return {
        "status": "ok",
        "phase": phase,
        "date": this_date,
        "user_id": user_id,
        "dry_run": dry_run,
    }


def cleanup_empty_trajectories():
    """Remove all empty directories at ./trajectories/."""
    for root, dirs, files in os.walk('trajectories'):
        for d in dirs:
            dirpath = os.path.join(root, d)
            if not os.listdir(dirpath):
                shutil.rmtree(dirpath)


def main():
    parser = argparse.ArgumentParser(
        description='Run daily trading workflow (mobile app → DB sync + automated trading)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python trading/runner.py                           # Auto-detect phase based on time
  python trading/runner.py 20260214                  # Specify date, auto phase
  python trading/runner.py 20260214 --phase pre-market  # Pre-market only
  python trading/runner.py --phase all               # Run all phases sequentially
  python trading/runner.py --dry-run                 # Log only, no mobile app
  python trading/runner.py --sync-only               # Legacy mode: sync data only
        """
    )
    parser.add_argument('date', nargs='?', default=None,
                        help='Trading date in YYYYMMDD format (default: today)')
    parser.add_argument('--phase', choices=['pre-market', 'market', 'post-market', 'auto', 'all'],
                        default='auto', help='Trading phase to run (default: auto-detect)')
    parser.add_argument('--user-id', type=int, default=1,
                        help='User ID for trading account (default: 1)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Dry run mode - log actions without mobile app operations')
    parser.add_argument('--sync-only', action='store_true',
                        help='Legacy mode - only sync data from mobile app (no trading phases)')

    args = parser.parse_args()

    # Cleanup empty trajectory directories
    cleanup_empty_trajectories()
    # Ensure app is Open to homepage and then logged in
    login()

    if args.sync_only:
        # Legacy behavior — just sync data using Guotai extractor
        asyncio.run(cron_sync_app_to_db(check_trading_day_and_time=False))
    else:
        # Determine trading date
        this_date = args.date
        if not this_date:
            this_date = datetime.now().strftime('%Y%m%d')

        # Check if trading day, if not, get next trading day
        if not calendar.is_trading_day(this_date):
            next_trading_date = calendar.get_next_trading_day(this_date)
            print(f"⚠️ {this_date} is not a trading day. Skipping to next trading day: {next_trading_date}")
            this_date = next_trading_date

            # If we skipped to a future date, we almost certainly want to run pre-market (preparation)
            if args.phase == 'auto':
                 print(f"   ℹ️ Preparing for future trading day {this_date}. Defaulting to 'pre-market' phase.")
                 args.phase = 'pre-market'

        # New trading workflow
        result = asyncio.run(run_daily_trading(
            this_date=this_date,
            phase=args.phase,
            user_id=args.user_id,
            dry_run=args.dry_run,
            app_package_name=GUOTAI_PACKAGE_NAME,
        ))

        # Print summary
        print("\n" + "=" * 80)
        print("TRADING RESULT SUMMARY")
        print("=" * 80)
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
