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

def create_buy_order(code: str, price: str, quantity: str, submit: bool = False, dry_run: bool = False) -> None:
    """Fill all fields on the buy order page.

    Args:
        code: Stock code (e.g., '600279')
        price: Trigger price (e.g., '3.97')
        quantity: Buy quantity (e.g., '900')
        submit: If True, tap the '创建订单' button to submit
        dry_run: If True, only log actions without executing
    """
    logger.info(f"Creating buy order: code={code}, price={price}, quantity={quantity}, submit={submit}, dry_run={dry_run}")

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
    
    # Wait for the select page to load, then tap '到价买入'
    time.sleep(2)
    ui_text = get_ui_tree()
    center = find_element_center(ui_text, '到价买入')
    if not center:
        # try scrolling to find it
        device_swipe(720, 1500, 720, 500, sleep_after=1.5)
        ui_text = get_ui_tree()
        center = find_element_center(ui_text, '到价买入')
    if not center:
        raise RuntimeError("Cannot find '到价买入' button on smart order page")
    logger.info(f"Tapping '到价买入' at {center}")
    device_tap(*center, sleep_after=2)
    
    # Scroll to top of page to ensure stock code is visible
    device_swipe(720, 500, 720, 1500, sleep_after=1.5)
    ui_text = get_ui_tree()
    
    # Verify we are on buy page
    if not verify_on_buy_page(ui_text):
        logger.warning("Current page may not be the buy order page (到价买入). Proceeding anyway.")
        
    # Step 6: Fill stock code
    fill_stock_code(code, ui_text)
    time.sleep(2)
    ui_text = get_ui_tree()
    
    # Set trigger condition (当股价 >=)
    set_trigger_condition_ge(ui_text)
    
    # Fill trigger price
    fill_trigger_price(price, ui_text)
    time.sleep(1)
    
    # Scroll up to close keyboard
    device_swipe(720, 2000, 720, 500, sleep_after=1.5)
    ui_text = get_ui_tree()
    
    # Set order method (委托方式)
    set_order_method(ui_text)
    ui_text = get_ui_tree()
    
    # Set order type to auto (自动下单)
    set_auto_order(ui_text)
    ui_text = get_ui_tree()
    
    # Set valid until date to Today
    set_valid_until_today(ui_text)
    ui_text = get_ui_tree()
    
    # Fill quantity
    fill_quantity(quantity, ui_text)
    
    # Close keyboard
    device_swipe(720, 2000, 720, 500, sleep_after=1.5)
    ui_text = get_ui_tree()
    
    # Verify final fields
    time.sleep(0.5)
    ui_text = get_ui_tree()
    logger.info("Final UI state (EditText fields):")
    for line in ui_text.split('\n'):
        if 'EditText' in line:
            logger.info(f"  {line.strip()}")

    # Step 8: Optionally submit
    if submit:
        tap_create_order(ui_text)
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
