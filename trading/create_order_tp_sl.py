#!/usr/bin/env python3
"""
Fill all fields on the "止盈止损" (take profit and stop loss) order page.

Prerequisites:
  - The app must be connected via ADB and logged in (handled automatically via goto_page).
  - Run `mobilerun device ui` first to verify element positions.

Usage:
  python trading/order_tp_sl.py --code 600279 --tp 4.10 --sl 3.80 --quantity 900
  python trading/order_tp_sl.py --code 600279 --json path_to_smart_orders.json
  python trading/order_tp_sl.py --batch  # Run steps 1-3 for all holding stocks
"""

import os
import sys
import time
import argparse
import subprocess
import re
import json
from datetime import datetime

from loguru import logger

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.tools import (
    run_cmd, device_tap, device_swipe, get_ui_tree,
    find_element_center, find_edittext_near_label, find_button_center,
    find_edittext_by_y, format_symbol, check_duplicate_orders,
    set_order_method, set_auto_order, set_valid_until_today,
    tap_create_order, fill_stock_code, adb_clear_field, adb_type
)
from shared.db.db import DB
from trading.guotai import open_app, login, goto_homepage, replay_page


# ---------------------------------------------------------------------------
# Page verification
# ---------------------------------------------------------------------------

def verify_on_tpsl_page(ui_text: str) -> bool:
    return '止盈止损' in ui_text


def set_monitoring_to_price(ui_text: str) -> str:
    center = find_element_center(ui_text, '按价格')
    if center:
        logger.info(f"Tapping '按价格' at {center}")
        device_tap(*center, sleep_after=1.0)
    else:
        logger.warning("Could not find '按价格' option. Tapping '监控条件' right side to reveal it.")
        cond_center = find_element_center(ui_text, '监控条件')
        if cond_center:
            device_tap(cond_center[0] + 400, cond_center[1], sleep_after=1.0)
            ui_text = get_ui_tree()
            center = find_element_center(ui_text, '按价格')
            if center:
                logger.info(f"Tapping '按价格' at {center}")
                device_tap(*center, sleep_after=1.0)
    return get_ui_tree()


def fill_tp_sl_price(tp_price: str, sl_price: str, ui_text: str) -> str:
    tp_val = str(round(float(tp_price), 2))
    sl_val = str(round(float(sl_price), 2))

    # TP field
    tp_label_center = find_element_center(ui_text, '止盈触发')
    if tp_label_center:
        # Find exact EditText on the same horizontal line
        tp_center = find_edittext_by_y(ui_text, tp_label_center[1])
        if not tp_center:
            tp_center = (tp_label_center[0] + 450, tp_label_center[1])
            
        logger.info(f"Tapping TP price field at {tp_center}")
        device_tap(*tp_center, sleep_after=0.5)
        adb_clear_field(max_chars=15)
        logger.info(f"Typing TP price via adb: {tp_val}")
        adb_type(tp_val)
    else:
        logger.warning("Cannot find '止盈触发' label")

    # Scroll up to reveal SL field and safely dismiss keyboard
    logger.info("Scrolling up to reveal SL field and dismiss keyboard")
    device_swipe(500, 1000, 500, 300, sleep_after=1.5)
    ui_text = get_ui_tree()

    # SL field
    sl_label_center = find_element_center(ui_text, '止损触发')
    if sl_label_center:
        # Find exact EditText on the same horizontal line
        sl_center = find_edittext_by_y(ui_text, sl_label_center[1])
        if not sl_center:
            sl_center = (sl_label_center[0] + 450, sl_label_center[1])
            
        logger.info(f"Tapping SL price field at {sl_center}")
        device_tap(*sl_center, sleep_after=0.5)
        adb_clear_field(max_chars=15)
        logger.info(f"Typing SL price via adb: {sl_val}")
        adb_type(sl_val)
    else:
        logger.warning("Cannot find '止损触发' label")
        
    return ui_text


def fill_quantity(quantity: str, ui_text: str) -> None:
    center = find_edittext_near_label(ui_text, '卖出数量')
    if not center:
        center = find_edittext_near_label(ui_text, '委托数量')
    if not center:
        raise RuntimeError("Cannot find quantity EditText")

    logger.info(f"Tapping quantity field at {center}")
    device_tap(*center, sleep_after=0.5)
    adb_clear_field(max_chars=15)
    logger.info(f"Typing quantity via adb: {quantity}")
    adb_type(quantity)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def create_tp_sl_order(code: str, tp_price: str, sl_price: str, quantity: str, submit: bool = False, dry_run: bool = False) -> None:
    logger.info(f"Creating TP/SL order: code={code}, tp_price={tp_price}, sl_price={sl_price}, quantity={quantity}, submit={submit}, dry_run={dry_run}")

    # Step 1: Start app if not running
    open_app()
    
    # Step 2: Login if needed
    login()
    
    # Step 3: Check duplicate orders
    check_duplicate_orders(code)
    
    # Step 4: Go back to homepage
    goto_homepage()
    
    # Step 5: Replay '今日触发' and navigate to subpage
    replay_page(['今日触发'])
    
    # Wait for the select page to load, then tap '止盈止损'
    time.sleep(2)
    ui_text = get_ui_tree()
    center = find_element_center(ui_text, '止盈止损')
    if not center:
        # try scrolling to find it
        device_swipe(720, 1500, 720, 500, sleep_after=1.5)
        ui_text = get_ui_tree()
        center = find_element_center(ui_text, '止盈止损')
    if not center:
        raise RuntimeError("Cannot find '止盈止损' button on smart order page")
    logger.info(f"Tapping '止盈止损' at {center}")
    device_tap(*center, sleep_after=2)
    
    # Scroll to top of page to ensure stock code is visible
    device_swipe(720, 500, 720, 1500, sleep_after=1.5)
    ui_text = get_ui_tree()

    if not verify_on_tpsl_page(ui_text):
        logger.warning("Current page may not be the 止盈止损 page. Proceeding anyway.")

    # Step 6: Fill stock code
    fill_stock_code(code, ui_text)

    time.sleep(2)
    ui_text = get_ui_tree()

    ui_text = set_monitoring_to_price(ui_text)
    ui_text = fill_tp_sl_price(tp_price, sl_price, ui_text)

    time.sleep(1)
    
    logger.info("Scrolling to ensure quantity field is visible and keyboard is closed")
    device_swipe(720, 2000, 720, 500, sleep_after=1.5)
    ui_text = get_ui_tree()

    # Set valid until date to Today
    set_valid_until_today(ui_text)
    ui_text = get_ui_tree()

    fill_quantity(quantity, ui_text)
    
    # Hide keyboard and refresh UI tree
    device_swipe(720, 2000, 720, 500, sleep_after=1.5)
    ui_text = get_ui_tree()

    set_order_method(ui_text)
    ui_text = get_ui_tree()

    set_auto_order(ui_text)
    ui_text = get_ui_tree()
    
    # Hide keyboard again
    device_swipe(720, 2000, 720, 500, sleep_after=1.5)
    ui_text = get_ui_tree()
    
    logger.info("Final UI state (EditText fields):")
    for line in ui_text.split('\n'):
        if 'EditText' in line:
            logger.info(f"  {line.strip()}")

    # Step 7: Optionally submit
    if submit and not dry_run:
        tap_create_order(ui_text)
        logger.info("✅ TP/SL order submitted")
    elif dry_run:
        logger.info(f"[DRY RUN] Would submit TP/SL order: code={code}, tp={tp_price}, sl={sl_price}, quantity={quantity}")
    else:
        logger.info("✅ TP/SL order fields filled (not submitted, use --submit to submit)")


def batch_create_tp_sl(submit: bool, dry_run: bool, codes_list: list[str] = None):
    """Run steps 1-3 automatically."""
    logger.info("Running batch TP/SL order creation")
    # 1. Got current holding stocks from shared/db/imobile.db
    if codes_list:
        placeholders = ','.join(['?'] * len(codes_list))
        query = f"SELECT code, available_shares, cost_basis_diluted FROM holding_stocks WHERE code IN ({placeholders})"
        holdings = DB.fetch_all(query, tuple(codes_list))
        found_codes = {row['code'] for row in holdings}
        missing_codes = set(codes_list) - found_codes
        if missing_codes:
            logger.warning(f"These stocks are not in holding db: {missing_codes}")
    else:
        holdings = DB.fetch_all("SELECT code, available_shares, cost_basis_diluted FROM holding_stocks")
    
    code_to_shares = {row['code']: row['available_shares'] for row in holdings}
    code_to_cost = {row['code']: row['cost_basis_diluted'] for row in holdings}
    codes = list(code_to_shares.keys())
    
    if not codes:
        logger.info("No holding stocks found.")
        return
    formatted_codes = [format_symbol(c) for c in codes]
    stocks_list = ",".join(formatted_codes)
    logger.info(f"Formatted symbols: {stocks_list}")

    # 2. python -m backtest.cli analyze --symbols stocks_list, this will create a smart orders json file.
    logger.info("Running backtest.cli analyze to generate smart orders JSON...")
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_json = os.path.join("backtest", "results", "daily", f"smart_orders_batch_{timestamp}.json")
    os.makedirs(os.path.dirname(output_json), exist_ok=True)
    
    cmd = f"python -m backtest.cli analyze --symbols {stocks_list} --initial-cash 10000000 --output {output_json}"
    run_cmd(cmd)

    if not os.path.exists(output_json):
        # Maybe it output somewhere else if our --output was ignored, but wait, we passed --output.
        # Check if the file is generated
        pass
    
    if os.path.exists(output_json):
        logger.info(f"JSON generated: {output_json}")
    else:
        # Fallback to look for latest json
        logger.warning(f"Expected output file {output_json} not found. Attempting to locate latest JSON.")

    # 3. For each stock_code in stocks_list, run the single-stock logic
    for code in codes:
        logger.info(f"Processing stock: {code}")
        # Run itself via python subprocess or directly call function
        # Since we are in the same script, let's just parse json and call create_tp_sl_order
        tp_price = None
        sl_price = None
        quantity = None
        try:
            with open(output_json, 'r') as f:
                data = json.load(f)
                for order in data.get('smart_orders', []):
                    if order['symbol'].startswith(code):
                        tp_price = str(round(float(order.get('sell_take_profit_price', 0)), 2))
                        sl_price = str(round(float(order.get('sell_stop_loss_price', 0)), 2))
                        # For existing holdings, use available_shares from DB
                        qty = code_to_shares.get(code)
                        if qty:
                            quantity = str(qty)
                        else:
                            quantity = str(order.get('buy_quantity', ''))
                        break
        except Exception as e:
            logger.error(f"Failed to read {output_json}: {e}")
            
        if not tp_price or not sl_price or not quantity:
            logger.warning(f"Skipping {code} because missing data in JSON.")
            continue
            
        create_tp_sl_order(code, tp_price, sl_price, quantity, submit=submit, dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser(description='Fill fields on the 止盈止损 order page.')
    parser.add_argument('--code', type=str, help='Comma-separated list of stock codes (e.g., 600279,600006) or a single code')
    parser.add_argument('--tp', type=str, help='Take profit price')
    parser.add_argument('--sl', type=str, help='Stop loss price')
    parser.add_argument('--quantity', type=str, help='Quantity')
    parser.add_argument('--json', type=str, help='Path to smart_orders JSON file to pull values from')
    parser.add_argument('--batch', action='store_true', help='Run steps 1-3 for all holdings automatically')
    parser.add_argument('--submit', action='store_true', help='Submit the order after filling fields')
    parser.add_argument('--dry-run', action='store_true', help='Log actions without executing')

    args = parser.parse_args()

    if args.batch:
        batch_create_tp_sl(submit=args.submit, dry_run=args.dry_run)
        return

    if args.code and args.json:
        parser.error("Cannot specify both --code and --json.")
    if not args.code and not args.json:
        parser.error("Must specify either --code or --json.")

    orders_to_process = []

    if args.json:
        try:
            with open(args.json, 'r') as f:
                data = json.load(f)
            
            # Query db for holdings to get actual available_shares
            holdings = DB.fetch_all("SELECT code, available_shares FROM holding_stocks")
            code_to_shares = {row['code']: row['available_shares'] for row in holdings}
            
            for order in data.get('smart_orders', []):
                if 'sell_take_profit_price' in order and 'sell_stop_loss_price' in order:
                    code = order['symbol'].split('.')[0]
                    tp = str(order['sell_take_profit_price'])
                    sl = str(order['sell_stop_loss_price'])
                    
                    qty = code_to_shares.get(code)
                    if qty:
                        quantity = str(qty)
                    else:
                        quantity = str(order.get('buy_quantity', ''))
                        
                    if code and tp and sl and quantity:
                        orders_to_process.append({
                            'code': code,
                            'tp': tp,
                            'sl': sl,
                            'quantity': quantity
                        })
            
            if not orders_to_process:
                logger.warning("No valid TP/SL orders found in JSON file.")
                sys.exit(1)
        except Exception as e:
            logger.error(f"Failed to load JSON {args.json}: {e}")
            sys.exit(1)
    else:
        # --code is provided
        if args.tp and args.sl and args.quantity:
            codes_list = [c.strip() for c in args.code.split(',') if c.strip()]
            for code in codes_list:
                orders_to_process.append({
                    'code': code,
                    'tp': args.tp,
                    'sl': args.sl,
                    'quantity': args.quantity
                })
        else:
            # Need to generate JSON first using backtest.cli analyze
            codes_list = [c.strip() for c in args.code.split(',') if c.strip()]
            if not codes_list:
                logger.error("No valid stock codes specified.")
                sys.exit(1)
            
            batch_create_tp_sl(submit=args.submit, dry_run=args.dry_run, codes_list=codes_list)
            return

    if not orders_to_process:
        logger.error("No valid orders to process.")
        sys.exit(1)

    for order in orders_to_process:
        create_tp_sl_order(
            code=order['code'],
            tp_price=order['tp'],
            sl_price=order['sl'],
            quantity=order['quantity'],
            submit=args.submit,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
