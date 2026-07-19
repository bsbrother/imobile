#!/usr/bin/env python3
"""
Fill all fields on the "到价买入" (buy at target price) order page.

Prerequisites:
  - The app must be connected via ADB and logged in (handled automatically via goto_page).
  - Run `mobilerun device ui` first to verify element positions.

Usage:
  python trading/create_order_buy.py --code 600279 --price 3.97 --quantity 900
  python trading/create_order_buy.py --code 600279 --price 3.97 --quantity 900 --submit
  python trading/create_order_buy.py --code 600279 --price 3.97 --quantity 900 --dry-run

Interaction notes:
  - Stock code: tapping opens a separate "选择股票" overlay with a search EditText.
    Type the code in the overlay's search field, select the matching stock from
    results, and the app auto-returns to the buy order page.
  - Trigger price: WebView EditText that does NOT get keyboard focus via normal tap.
    Must use `adb shell input keyevent` (DEL to clear) + `adb shell input text`
    to type.
  - Quantity: WebView EditText that works with `mobilerun device type --clear`.
  - valid_until: Taps the date field (WebView, bounds=[0,0]) to open calendar picker,
    then taps the target day number (e.g. "30") and confirms "确定".
    Target is 1 trading day: today if <15:00, next trading day if >=15:00.
    BUY form date value at ~(400,1550), TP/SL form at ~(500,2900).
    See utils/tools.py set_valid_until_today() for coordinate fallback logic.
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
    find_element_center, find_edittext_near_label,
    format_symbol, check_duplicate_orders,
    set_trigger_condition_ge, fill_trigger_price, set_order_method,
    set_auto_order, set_valid_until_today, tap_create_order,
    fill_stock_code, adb_clear_field, adb_type
)
from trading.guotai import open_app, login, goto_homepage, replay_page


# ---------------------------------------------------------------------------
# Page verification
# ---------------------------------------------------------------------------

def verify_on_buy_page(ui_text: str) -> bool:
    """Verify the app is currently on the buy order page (到价买入)."""
    return '买入数量' in ui_text


# ---------------------------------------------------------------------------
# Field fill functions
# ---------------------------------------------------------------------------

def fill_quantity(quantity: str, ui_text: str) -> None:
    """Fill in the quantity field.

    Finds the EditText after "买入数量" label.
    This field works with mobilerun device type --clear.
    """
    center = find_edittext_near_label(ui_text, '买入数量')
    if not center:
        raise RuntimeError("Cannot find quantity EditText")

    logger.info(f"Tapping quantity field at {center}")
    device_tap(*center, sleep_after=0.5)

    logger.info("Clearing quantity field via DEL keys")
    adb_clear_field(max_chars=15)

    logger.info(f"Typing quantity via adb: {quantity}")
    adb_type(quantity)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def create_buy_order(code: str, price: str, quantity: str, submit: bool = False, dry_run: bool = False, skip_dup_check: bool = False) -> None:
    """Fill all fields on the buy order page using optimized static coordinates."""
    logger.info(f"Creating buy order: code={code}, price={price}, quantity={quantity}, submit={submit}, dry_run={dry_run}, skip_dup_check={skip_dup_check}")

    # Step 1: Start app if not running & Login if needed
    open_app()
    login()

    # Step 2: Check duplicate orders
    if not skip_dup_check:
        check_duplicate_orders(code)
    else:
        logger.info(f"Skipping duplicate check for {code} to maximize execution speed.")

    # Step 3: Go back to homepage & Replay '今日触发'
    goto_homepage()
    replay_page(['今日触发'])
    time.sleep(1)

    # Step 4: Tap '到价买入'
    logger.info("Tapping '到价买入' at (273, 2240)")
    device_tap(273, 2240, sleep_after=1.0)
    
    # Scroll to top of page to ensure stock code is visible
    device_swipe(720, 500, 720, 1500, sleep_after=0.5)

    # Step 5: Fill stock code
    logger.info("Tapping Stock Code Field at (820, 873)")
    device_tap(820, 873, sleep_after=1.0)
    
    logger.info("Tapping Overlay Search Bar at (806, 369)")
    device_tap(806, 369, sleep_after=0.5)
    
    logger.info(f"Typing stock code: {code}")
    from utils.tools import device_type, adb_clear_field, adb_type
    device_type(code, clear=True)
    time.sleep(1.5)
    
    logger.info("Selecting First Result at (719, 717)")
    device_tap(719, 717, sleep_after=1.5)

    # Step 6: Set trigger condition (当股价 >=)
    logger.info("Setting trigger condition '当股价 ≥' at (695, 1885)")
    device_tap(695, 1885, sleep_after=0.5)

    # Step 7: Fill trigger price
    logger.info("Tapping Trigger Price Field at (939, 1531)")
    device_tap(939, 1531, sleep_after=0.5)
    
    logger.info("Clearing trigger price field via DEL keys")
    adb_clear_field(max_chars=15)
    
    logger.info(f"Typing trigger price via adb: {price}")
    adb_type(price)
    time.sleep(0.5)

    # Scroll up to close keyboard and reveal lower fields
    device_swipe(720, 2000, 720, 500, sleep_after=1.0)

    # Step 8: Set order method (委托方式 -> 最新价)
    logger.info("Tapping Order Method Field at (217, 2319)")
    device_tap(217, 2319, sleep_after=0.5)

    # Step 9: Fill quantity
    logger.info("Tapping Quantity Field at (939, 2609)")
    device_tap(939, 2609, sleep_after=0.5)
    
    logger.info("Clearing quantity field via DEL keys")
    adb_clear_field(max_chars=15)
    
    logger.info(f"Typing quantity via adb: {quantity}")
    adb_type(quantity)
    time.sleep(0.5)

    # Step 10: Set order type to auto (自动下单)
    logger.info("Tapping Order Type (Auto) at (721, 2899)")
    device_tap(721, 2899, sleep_after=0.5)

    # Close keyboard
    device_swipe(720, 2000, 720, 500, sleep_after=0.5)

    # Step 12: Submit
    if submit:
        logger.info("Tapping Submit Button '创建订单' at (902, 2825)")
        device_tap(902, 2825, sleep_after=2.0)
        
        # Tap confirmation popups
        logger.info("Tapping confirmation popup 1 at (1021, 2244)")
        device_tap(1021, 2244, sleep_after=1.0)
        logger.info("Tapping confirmation popup 2 at (1021, 2244)")
        device_tap(1021, 2244, sleep_after=1.0)
        
        logger.info("✅ Buy order submitted")
    else:
        logger.info("✅ Buy order fields filled (not submitted, use --submit to submit)")


def main():
    parser = argparse.ArgumentParser(
        description='Fill fields on the 到价买入 (buy at target price) order page.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fill fields only (no submit):
  python trading/create_order_buy.py --code 600279 --price 3.97 --quantity 900

  # Fill and submit:
  python trading/create_order_buy.py --code 600279 --price 3.97 --quantity 900 --submit

  # Dry run (log only):
  python trading/create_order_buy.py --code 600279 --price 3.97 --quantity 900 --dry-run

  # Show current UI tree for debugging:
  python trading/create_order_buy.py --show-ui
        """
    )
    parser.add_argument('--code', type=str, help='Stock code (e.g., 600279)')
    parser.add_argument('--price', type=str, help='Trigger price (e.g., 3.97)')
    parser.add_argument("--quantity", type=str, help="Quantity to buy")
    parser.add_argument("--json", type=str, help="Path to smart_orders JSON file to pull values from")
    parser.add_argument('--submit', action='store_true',
                        help='Submit the order after filling fields')
    parser.add_argument('--dry-run', action='store_true',
                        help='Log actions without executing')
    parser.add_argument('--show-ui', action='store_true',
                        help='Show current UI tree and exit')

    args = parser.parse_args()

    if args.show_ui:
        print(get_ui_tree())
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

            for order in data.get('smart_orders', []):
                if 'buy_price' in order and 'buy_quantity' in order:
                    code = order['symbol'].split('.')[0]
                    price = str(order['buy_price'])
                    quantity = str(order['buy_quantity'])
                    orders_to_process.append({
                        'code': code,
                        'price': price,
                        'quantity': quantity
                    })

            if not orders_to_process:
                logger.warning("No buy orders found in JSON file.")
        except Exception as e:
            logger.error(f"Failed to load JSON {args.json}: {e}")
            sys.exit(1)
    else:
        # --code is provided
        if args.price and args.quantity:
            codes_list = [c.strip() for c in args.code.split(',') if c.strip()]
            for code in codes_list:
                orders_to_process.append({
                    'code': code,
                    'price': args.price,
                    'quantity': args.quantity
                })
        else:
            # Need to generate JSON first using backtest.cli analyze
            codes_list = [c.strip() for c in args.code.split(',') if c.strip()]
            if not codes_list:
                logger.error("No valid stock codes specified.")
                sys.exit(1)

            formatted_symbols = ",".join(format_symbol(c) for c in codes_list)
            logger.info(f"Generating smart orders JSON for symbols: {formatted_symbols}")

            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_json = os.path.join("backtest", "results", "daily", f"smart_orders_batch_{timestamp}.json")
            os.makedirs(os.path.dirname(output_json), exist_ok=True)

            cmd = f"python -m backtest.cli analyze --symbols {formatted_symbols} --output {output_json}"
            run_cmd(cmd)

            try:
                with open(output_json, 'r') as f:
                    data = json.load(f)

                for order in data.get('smart_orders', []):
                    if 'buy_price' in order and 'buy_quantity' in order:
                        code = order['symbol'].split('.')[0]
                        price = str(order['buy_price'])
                        quantity = str(order['buy_quantity'])
                        orders_to_process.append({
                            'code': code,
                            'price': price,
                            'quantity': quantity
                        })
                if not orders_to_process:
                    logger.warning("No buy orders generated in the JSON file.")
            except Exception as e:
                logger.error(f"Failed to load generated JSON {output_json}: {e}")
                sys.exit(1)

    if not orders_to_process:
        logger.error("No valid orders to process.")
        sys.exit(1)

    for order in orders_to_process:
        create_buy_order(
            code=order['code'],
            price=order['price'],
            quantity=order['quantity'],
            submit=args.submit,
            dry_run=args.dry_run,
        )


if __name__ == "__main__":
    main()
