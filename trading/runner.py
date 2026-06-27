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
)
from trading.sync_app_to_db import cron_sync_app_to_db


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
        logger.info("Extracting live app state before generating orders...")
        from trading.guotai import pre_requirements, parse_csv_data
        from trading.sync_app_to_db import (
            get_summary_position_from_app_position_page_structured,
            get_order_from_app_smart_order_page_structured
        )

        tools, llm, config = await pre_requirements(app_package_name)
        
        # 1. Fetch Positions & Cash
        logger.info("Fetching cash and positions from App...")
        pos_csv = await get_summary_position_from_app_position_page_structured(config, llm, tools)
        
        app_cash = 0.0
        app_positions = []
        if pos_csv:
            sections = pos_csv.strip().split('\n\n')
            if len(sections) > 0:
                header, summary_rows = parse_csv_data(sections[0])
                if summary_rows:
                    app_cash = float(summary_rows[0][4]) # available cash
            if len(sections) > 1:
                pos_header, pos_rows = parse_csv_data(sections[1])
                for row in pos_rows:
                    app_positions.append({
                        'name': row[0],
                        'holdings': int(row[2]),
                        'available': int(row[3]),
                        'current_price': float(row[4]),
                        'cost': float(row[5])
                    })

        # 2. Fetch Running Orders
        logger.info("Fetching running orders from App...")
        order_csv = await get_order_from_app_smart_order_page_structured(
            config, llm, tools, target_tabs=["运行中"]
        )
        app_running_orders = []
        if order_csv:
            ord_header, ord_rows = parse_csv_data(order_csv)
            for row in ord_rows:
                app_running_orders.append({
                    'name': row[0],
                    'code': row[1],
                    'trigger_condition': row[2],
                    'buy_or_sell_price_type': row[3],
                    'buy_or_sell_quantity': float(row[4]),
                    'valid_until': row[5],
                    'order_number': row[6],
                    'reason_of_ending': row[7] if len(row) > 7 else '',
                    'status': row[8] if len(row) > 8 else '运行中'
                })

        logger.info(f"App data extracted: Cash: {app_cash}, Positions: {len(app_positions)}, Running Orders: {len(app_running_orders)}")

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
                is_live=True,
                app_cash=app_cash,
                app_positions=app_positions,
                app_running_orders=app_running_orders
            )
            import json
            smart_output_file = os.path.join(_daily_dir, f'smart_orders_{this_date}.json')
            if os.path.exists(smart_output_file):
                with open(smart_output_file, 'r') as f:
                    data = json.load(f)
                
                market_pattern = data.get('market_pattern', 'normal')
                max_positions = data.get('portfolio_config', {}).get('max_positions', 10)
                initial_cash = data.get('portfolio_config', {}).get('initial_cash', app_cash)
                per_slot_cash = initial_cash / max_positions if max_positions else 0
                
                new_buy_orders_count = data.get('total_orders', 0)
                total_allocated = data.get('portfolio_config', {}).get('total_allocated', 0.0)
                
                all_orders = data.get('smart_orders', [])
                buy_orders = all_orders[:new_buy_orders_count]
                tp_sl_orders = all_orders[new_buy_orders_count:]
                
                summary_lines = []
                summary_lines.append(f"Created {smart_output_file}:")
                summary_lines.append(f"Market Regime: {market_pattern.upper()} MAX_POSITIONS: {max_positions}")
                summary_lines.append(f"Total_Cash: {initial_cash:.2f}       Per_Slot_Cash: {per_slot_cash:.2f}")
                summary_lines.append(f"Running_Orders_In_APP: {len(app_running_orders) if app_running_orders else 0}")
                summary_lines.append(f"Orders: {len(all_orders)}")
                
                summary_lines.append(f"Buy_Orders list: total {new_buy_orders_count} sum {total_allocated:.2f}")
                for o in buy_orders:
                    summary_lines.append(f"{o['symbol']}_{o['name']}, {o['buy_price']}, {o['buy_quantity']}")
                
                skipped_buy_orders = data.get('skipped_buy_orders', [])
                if skipped_buy_orders:
                    summary_lines.append(f"Buy Orders Skip List: no enogh cash")
                    for o in skipped_buy_orders:
                        summary_lines.append(f"{o['symbol']}_{o['name']}, {o['buy_price']}, {o['remaining_cash']:.2f}")

                summary_lines.append(f"TP&SL_Orders list: total {len(tp_sl_orders)}")
                for o in tp_sl_orders:
                    summary_lines.append(f"{o['symbol']}_{o['name']}, {o['sell_take_profit_price']}, {o['sell_stop_loss_price']}, {o['buy_quantity']}")
                
                logger.info("\n" + "\n".join(summary_lines))

            logger.info("✅ Pre-market stock picking + smart orders complete")
        except Exception as e:
            logger.error(f"❌ Pre-market failed: {e}")
            raise

    elif phase == 'market':
        # Execute pending smart orders
        logger.info("Executing pending smart orders...")
        # TODO: call order execution

    elif phase == 'post-market':
        # Removed auto background sync as requested by user.
        # Only trading/runner.py --sync-only or trading/sync_app_to_db.py should sync.
        logger.info("Post-market phase completed. Data sync is manual or via --sync-only.")

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
    if not args.date:
        args.date = datetime.now().strftime('%Y%m%d')

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

        # Check if trading day, if not, get next trading day
        if not calendar.is_trading_day(this_date):
            next_trading_date = calendar.get_next_trading_day(this_date)
            print(f"⚠️ {this_date} is not a trading day. Skipping to next trading day: {next_trading_date}")
            this_date = next_trading_date
            args.date = next_trading_date

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
