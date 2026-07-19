#!/usr/bin/env python3
"""
Pre-Start Real Trading Initialization

Prepares the production database for real trading starting 2026-06-29.

Three-phase setup:
  1. Re-create empty shared/db/imobile.db from imobile.sql schema
  2. Sync current app data (positions, orders, quotes, transactions) to the fresh DB
     using incremental sync (cutoff = last trading date = 2026-06-26)
  3. For each holding stock in holding_stocks, create a synthetic BUY transaction
     dated 2026-06-25 with price/quantity from the holding record.
     This ensures the backtest engine can calculate holding_days >= MAX_HOLDING_DAYS
     and trigger force-sells for old positions.

Usage:
    python trading/pre-start-real-trading.py          # Full 3-step init
    python trading/pre-start-real-trading.py --dry-run  # Print what would happen
    python trading/pre-start-real-trading.py --skip-sync  # Skip app→DB sync (DB already populated)
"""

import os
import sys
import asyncio
import argparse
from datetime import datetime, timedelta
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dotenv
dotenv.load_dotenv(os.path.expanduser('.env'), override=False, verbose=False)

from shared.db.db import DB, DB_IMOBILE_FILE, DB_IMOBILE_SQL, backup_reinit_db
from backtest.utils.trading_calendar import calendar
from trading.guotai import login
from trading.sync_app_to_db import (
    set_sync_cutoff_date,
    cron_sync_app_to_db,
    check_app_vs_db,
)


# ─── Configuration ────────────────────────────────────────────

# Real trading start date from .env (default 2026-06-29)
START_REAL_TRADING_DATE = os.getenv('START_REAL_TRADING_DATE', '2026-06-29')

# The last trading day before real trading starts
LAST_TRADING_DATE = calendar.get_trading_days_before(START_REAL_TRADING_DATE, 1)

# Synthetic BUY date for holdings: one day before last trading date
SYNTHETIC_BUY_DATE = calendar.get_trading_days_before(LAST_TRADING_DATE, 1)

USER_ID = 1


# ─── Step 1: Re-create empty DB ──────────────────────────────

def recreate_db():
    """Drop and recreate shared/db/imobile.db from imobile.sql schema."""
    sql_path = DB_IMOBILE_SQL
    if not sql_path or not os.path.exists(sql_path):
        raise FileNotFoundError(f"Schema file not found: {sql_path}")

    logger.info(f"Re-creating empty database from: {DB_IMOBILE_SQL}")
    backup_reinit_db()
    logger.info("✅ Database re-created successfully.")

    # Verify
    if not os.path.exists(DB_IMOBILE_FILE):
        raise FileNotFoundError(f"Database was not created: {DB_IMOBILE_FILE}")

    # Log table counts
    with DB.cursor() as cursor:
        tables = ['smart_orders', 'holding_stocks', 'transactions',
                  'summary_account', 'market_indices', 'portfolio_history']
        for t in tables:
            count = cursor.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            logger.info(f"  {t}: {count} rows")


# ─── Step 2: Sync app data → empty DB ────────────────────────

async def sync_app_to_db():
    """Sync current app data to the fresh database, using incremental cutoff."""
    # Set cutoff to last trading date so we only get data from 2026-06-26 onwards
    set_sync_cutoff_date(LAST_TRADING_DATE)

    logger.info(f"Syncing app data to DB (cutoff: {LAST_TRADING_DATE})...")
    result = await cron_sync_app_to_db(check_trading_day_and_time=False)

    # Validate sync
    for key in ['quote_sync_result', 'position_sync_result',
                'order_sync_result', 'transaction_sync_result']:
        r = result.get(key, {})
        if not r.get('success'):
            logger.error(f"❌ {key} failed: {r}")

    logger.info("✅ App→DB sync complete.")
    return result


# ─── Step 3: Create synthetic BUY transactions for holdings ──

def create_synthetic_buy_transactions():
    """For each stock in holding_stocks, create a synthetic BUY transaction
    dated SYNTHETIC_BUY_DATE using its cost/quantity from the holding record.

    This is needed because the fresh DB only has data from 2026-06-26 onwards,
    so existing holdings have no historical buy transactions. Without a buy
    record, the backtest engine's holding_days calculation starts from 0,
    meaning positions would never trigger force-sell for MAX_HOLDING_DAYS.

    Each synthetic buy uses:
      - transaction_date: SYNTHETIC_BUY_DATE 00:00:00
      - transaction_type: 'buy'
      - price: cost_basis_diluted from holding_stocks
      - quantity: holdings from holding_stocks
      - amount: price × quantity
      - commission: 0.0
      - tax: 0.0
      - net_amount: amount
      - notes: 'synthetic_buy_for_real_trading_init'
    """
    with DB.cursor() as cursor:
        holdings = cursor.execute(
            """SELECT code, name, holdings, available_shares,
                      cost_basis_diluted, cost_basis_total, current_price
               FROM holding_stocks
               WHERE user_id = ? AND holdings > 0""",
            (USER_ID,)
        ).fetchall()

        if not holdings:
            logger.info("No holdings to create synthetic transactions for.")
            return 0

        synthetic_date = f"{SYNTHETIC_BUY_DATE} 00:00:00"
        created_count = 0

        for h in holdings:
            code = h[0]
            name = h[1]
            quantity = h[2]
            cost_basis = h[4] if h[4] else 0.0
            current_price = h[6] if h[6] else cost_basis

            if quantity <= 0 or cost_basis <= 0:
                logger.warning(f"Skipping {name} ({code}): holdings={quantity}, cost={cost_basis}")
                continue

            amount = cost_basis * quantity
            commission = 0.0
            tax = 0.0
            net_amount = amount

            # Check if a synthetic buy already exists for this stock
            existing = cursor.execute(
                """SELECT id FROM transactions
                   WHERE user_id = ? AND code = ? AND transaction_type = 'buy'
                   AND transaction_date = ? AND notes = 'synthetic_buy_for_real_trading_init'""",
                (USER_ID, code, synthetic_date)
            ).fetchone()

            if existing:
                logger.info(f"Synthetic buy already exists for {name} ({code}), skipping.")
                continue

            cursor.execute(
                """INSERT INTO transactions
                   (user_id, code, name, transaction_type, transaction_date,
                    price, quantity, amount, commission, tax, net_amount, notes)
                   VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, ?, ?, ?, ?)""",
                (USER_ID, code, name, synthetic_date,
                 cost_basis, quantity, amount,
                 commission, tax, net_amount,
                 'synthetic_buy_for_real_trading_init')
            )
            created_count += 1
            logger.info(f"  ✅ Synthetic BUY: {name} ({code}) — "
                        f"{quantity} shares @ ¥{cost_basis:.2f} = ¥{amount:,.2f} "
                        f"(current: ¥{current_price:.2f})")

    logger.info(f"✅ Created {created_count} synthetic BUY transactions "
                f"for {len(holdings)} holdings.")
    return created_count


# ─── Main ─────────────────────────────────────────────────────

async def main(dry_run: bool = False, skip_sync: bool = False):
    logger.info("=" * 60)
    logger.info("  PRE-START REAL TRADING INITIALIZATION")
    logger.info(f"  Start date: 2026-06-29 (Monday)")
    logger.info(f"  Last trading date: {LAST_TRADING_DATE}")
    logger.info(f"  Synthetic buy date: {SYNTHETIC_BUY_DATE}")
    logger.info("=" * 60)

    if dry_run:
        logger.info("[DRY RUN] No changes will be made.")
        return

    # Step 1: Re-create empty DB
    logger.info("\n─── Step 1/3: Re-create empty database ───")
    recreate_db()

    if skip_sync:
        logger.info("\n─── Step 2/3: App→DB sync SKIPPED ───")
    else:
        # Step 2: Sync app data to fresh DB
        logger.info("\n─── Step 2/3: Sync app data to DB ───")
        login()
        sync_result = await sync_app_to_db()

        # Verify DB matches app after sync
        logger.info("\n─── Verification: check_app_vs_db() ───")
        check_result = await check_app_vs_db(user_id=USER_ID)
        if not check_result['db_matches_app']:
            logger.warning("⚠️ DB does not match App after sync. "
                           "Review mismatches before proceeding.")
            logger.warning(f"Mismatches: {check_result['mismatches']}")
        else:
            logger.info("✅ DB matches App — ready for synthetic transactions.")

    # Step 3: Create synthetic BUY transactions for holdings
    logger.info("\n─── Step 3/3: Create synthetic BUY transactions ───")
    created = create_synthetic_buy_transactions()

    # Final summary
    logger.info("\n" + "=" * 60)
    logger.info("  PRE-START INITIALIZATION COMPLETE")
    logger.info("=" * 60)
    with DB.cursor() as cursor:
        holdings = cursor.execute(
            "SELECT COUNT(*) FROM holding_stocks WHERE user_id=? AND holdings > 0",
            (USER_ID,)
        ).fetchone()[0]
        orders = cursor.execute(
            "SELECT COUNT(*) FROM smart_orders WHERE user_id=?",
            (USER_ID,)
        ).fetchone()[0]
        txns = cursor.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id=?",
            (USER_ID,)
        ).fetchone()[0]
        synth = cursor.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id=? AND notes='synthetic_buy_for_real_trading_init'",
            (USER_ID,)
        ).fetchone()[0]
        cash_row = cursor.execute(
            "SELECT cash, total_assets FROM summary_account WHERE user_id=?", (USER_ID,)
        ).fetchone()
        db_cash = float(cash_row[0]) if cash_row else 0.0
        db_assets = float(cash_row[1]) if cash_row else 0.0

    logger.info(f"  Holdings:      {holdings}")
    logger.info(f"  Smart Orders:  {orders}")
    logger.info(f"  Transactions:  {txns} ({synth} synthetic)")
    logger.info(f"  Cash:          ¥{db_cash:,.2f}")
    logger.info(f"  Total Assets:  ¥{db_assets:,.2f}")
    logger.info("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Pre-start real trading initialization',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Three-phase setup:
  1. Re-create empty shared/db/imobile.db from imobile.sql
  2. Sync current app data (positions, orders, quotes, transactions) to DB
  3. Create synthetic BUY transactions for existing holdings

After this script, the DB is ready for python trading/runner.py --phase pre-market
on 2026-06-29 real trading day.
        """
    )
    parser.add_argument('--dry-run', action='store_true',
                        help='Print what would happen without making changes')
    parser.add_argument('--skip-sync', action='store_true',
                        help='Skip app→DB sync (step 2) — useful if DB already populated')
    args = parser.parse_args()

    asyncio.run(main(dry_run=args.dry_run, skip_sync=args.skip_sync))
