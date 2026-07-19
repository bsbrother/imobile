#!/usr/bin/env python3
"""
Fill all fields on the Ordinary Buy/Sell (普通买入/普通卖出) order page.

Usage:
  python trading/create_order_ordinary.py --code 600279 --price 4.25 --quantity 900 --action buy
  python trading/create_order_ordinary.py --code 600279 --price 4.25 --quantity 900 --action buy --submit
  python trading/create_order_ordinary.py --code 600279 --price 4.25 --quantity 900 --action buy --dry-run
"""

import os
import sys
import time
import argparse
import re
import json
from loguru import logger

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.tools import (
    run_cmd, device_tap, device_swipe, get_ui_tree,
    find_element_center, find_edittext_near_label, find_button_center,
    adb_clear_field, adb_type, device_type, tap_create_order,
    goto_ordinary_trade_page, dismiss_custom_keyboard
)


def verify_on_ordinary_page(ui_text: str, action: str) -> bool:
    """Verify that we are on the ordinary buy/sell page."""
    label = '普通交易'
    return label in ui_text and (('买入' if action == 'buy' else '卖出') in ui_text)


def fill_stock_code_ordinary(code: str, ui_text: str) -> None:
    """Tap the stock code field, open overlay, search, and select the stock."""
    center = find_element_center(ui_text, '输入证券') or find_element_center(ui_text, 'stock_code_et')
    if not center:
        # Fallback to verified bounds center
        center = (483, 552)
        
    logger.info(f"Tapping stock code input field at {center} to open search overlay")
    device_tap(*center, sleep_after=2)

    overlay_ui = get_ui_tree()
    search_center = find_edittext_near_label(overlay_ui, '股票名称')
    if not search_center:
        for line in overlay_ui.split('\n'):
            if 'EditText' in line:
                match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                if match:
                    x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                    if y1 < 500:
                        search_center = ((x1 + x2) // 2, (y1 + y2) // 2)
                        break
    if not search_center:
        search_center = (483, 150)  # fallback search EditText Y-coord

    logger.info(f"Tapping search field at {search_center}")
    device_tap(*search_center, sleep_after=0.5)

    logger.info(f"Typing stock code: {code}")
    device_type(code, clear=True)
    time.sleep(2)

    results_ui = get_ui_tree()
    stock_center = find_element_center(results_ui, code, exclude_edittext=True)
    if stock_center:
        logger.info(f"Selecting stock {code} at {stock_center}")
        device_tap(*stock_center, sleep_after=3)
    else:
        # Check if code is already loaded in the input field or is present on page
        if f'stock_code_et", "{code}"' in results_ui or code in results_ui:
            logger.info(f"Stock {code} is already loaded on the page.")
        else:
            logger.error(f"Stock {code} not found in search results")
            raise RuntimeError(f"Stock {code} not found in search results")


def find_price_field(ui_text: str) -> tuple[int, int]:
    """Find the coordinates of the Price EditText."""
    candidates = []
    for line in ui_text.split('\n'):
        if 'et_price' in line:
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                candidates.append(((x1 + x2) // 2, (y1 + y2) // 2))
    if candidates:
        candidates.sort(key=lambda c: c[1])
        return candidates[0]
    return (483, 856)  # fallback Y-coord based on verified bounds


def find_quantity_field(ui_text: str) -> tuple[int, int]:
    """Find the coordinates of the Quantity EditText."""
    candidates = []
    for line in ui_text.split('\n'):
        if 'et_price' in line:
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                candidates.append(((x1 + x2) // 2, (y1 + y2) // 2))
    if len(candidates) >= 2:
        candidates.sort(key=lambda c: c[1])
        return candidates[1]
    return (483, 1070)  # fallback Y-coord based on verified bounds


def fill_price(price: str, ui_text: str) -> None:
    """Enter price in the Price field."""
    center = find_price_field(ui_text)
    logger.info(f"Tapping price field at {center}")
    device_tap(*center, sleep_after=1.0)
    
    logger.info(f"Typing price: {price}")
    device_type(price, clear=True)
    time.sleep(1.0)


def fill_quantity_ordinary(quantity: str, ui_text: str, action: str) -> None:
    """Enter quantity in the Quantity field."""
    center = find_quantity_field(ui_text)
    logger.info(f"Tapping quantity field at {center}")
    device_tap(*center, sleep_after=1.0)
    
    logger.info(f"Typing quantity: {quantity}")
    device_type(quantity, clear=True)
    time.sleep(1.0)


def find_submit_button(ui_text: str, label: str) -> tuple[int, int]:
    """Find the coordinates of the submit button (买入 / 卖出)."""
    for line in ui_text.split('\n'):
        if 'flash_operation' in line and label in line:
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                return (x1 + x2) // 2, (y1 + y2) // 2
    return (483, 1366)  # fallback Y-coord based on verified bounds


def submit_ordinary_order(ui_text: str, action: str) -> None:
    """Tap the submit button and handle secondary confirmation popups."""
    # First close the custom keyboard if it is active
    dismiss_custom_keyboard(ui_text)
    
    # Also check if system soft keyboard is open (keyboardVisible is True),
    # and send BACK keyevent to dismiss it.
    keyboard_visible = False
    for line in ui_text.split('\n'):
        if 'Phone state:' in line:
            if "'keyboardVisible': True" in line or '"keyboardVisible": true' in line:
                keyboard_visible = True
                
    if keyboard_visible:
        logger.info("Soft keyboard detected. Sending BACK keyevent to close keyboard.")
        run_cmd("adb shell input keyevent 4")
        time.sleep(1.0)
        
    ui_text = get_ui_tree()

    label = '买入' if action == 'buy' else '卖出'
    center = find_submit_button(ui_text, label)
    logger.info(f"Tapping submit button '{label}' at {center}")
    device_tap(*center, sleep_after=2)
    
    # Handle confirmation popups
    for popup_idx in range(2):
        popup_ui = get_ui_tree()
        confirm_center = None
        found_label = None
        
        for p_label in ['确定', '确认', '继续', '同意']:
            import re
            elements = []
            for line in popup_ui.split('\n'):
                if f'"{p_label}"' in line:
                    match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                    if match:
                        x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                        # Skip if it looks like the keyboard button
                        if p_label == '确定' and x1 > 1000 and y1 > 2500:
                            continue
                        elements.append(((x1 + x2) // 2, (y1 + y2) // 2))
            if elements:
                confirm_center = elements[0]
                found_label = p_label
                break
                
        if confirm_center:
            logger.info(f"Found popup confirmation button '{found_label}' at {confirm_center}. Tapping it.")
            device_tap(*confirm_center, sleep_after=2)
        else:
            break


def create_ordinary_order(code: str, price: str, quantity: str, action: str = 'buy', submit: bool = False, dry_run: bool = False, skip_dup_check: bool = False) -> None:
    """Fill and optionally submit an ordinary trade order using static coordinates."""
    logger.info(f"Ordinary {action.upper()} order: code={code}, price={price}, quantity={quantity}, submit={submit}, dry_run={dry_run}, skip_dup_check={skip_dup_check}")
    
    if dry_run:
        logger.info(f"[DRY RUN] Would create ordinary {action} order for {code} @ {price} x {quantity}")
        return

    # Step 1: Navigate to the Ordinary Buy/Sell page
    goto_ordinary_trade_page(action)
    time.sleep(1)

    # Step 2: Enter Stock Code
    logger.info("Tapping Stock Code Field at (483, 552)")
    device_tap(483, 552, sleep_after=1.0)
    
    logger.info("Tapping Overlay Search Bar at (483, 150)")
    device_tap(483, 150, sleep_after=0.5)
    
    logger.info(f"Typing stock code: {code}")
    device_type(code, clear=True)
    time.sleep(1.5)
    
    # We will just tap the first result location (719, 717) which works across overlays
    logger.info("Selecting First Result at (719, 717)")
    device_tap(719, 717, sleep_after=1.5)

    # Step 3: Enter Price
    logger.info("Tapping Price Field at (483, 856)")
    device_tap(483, 856, sleep_after=0.5)
    
    logger.info(f"Typing price: {price}")
    device_type(price, clear=True)
    time.sleep(0.5)

    # Dismiss keyboard
    device_swipe(720, 2000, 720, 500, sleep_after=0.5)

    # Step 4: Enter Quantity
    logger.info("Tapping Quantity Field at (483, 1070)")
    device_tap(483, 1070, sleep_after=0.5)
    
    logger.info(f"Typing quantity: {quantity}")
    device_type(quantity, clear=True)
    time.sleep(0.5)

    # Dismiss keyboard
    device_swipe(720, 2000, 720, 500, sleep_after=0.5)

    # Step 5: Submit
    if submit:
        logger.info("Tapping Submit Button at (483, 1366)")
        device_tap(483, 1366, sleep_after=2.0)
        
        # Confirmation popups
        logger.info("Tapping confirmation popup 1 at (720, 1600)")
        device_tap(720, 1600, sleep_after=1.0)
        logger.info("Tapping confirmation popup 2 at (720, 1600)")
        device_tap(720, 1600, sleep_after=1.0)
        
        logger.info(f"✅ Ordinary {action} order submitted")
    else:
        logger.info(f"✅ Ordinary {action} order fields populated (dry-run, not submitted)")


def main():
    parser = argparse.ArgumentParser(description='Fill fields on Ordinary Buy/Sell page.')
    parser.add_argument('--code', type=str, required=True, help='Stock code (e.g. 600279)')
    parser.add_argument('--price', type=str, required=True, help='Limit price')
    parser.add_argument('--quantity', type=str, required=True, help='Quantity')
    parser.add_argument('--action', type=str, choices=['buy', 'sell'], default='buy', help='Action type (buy/sell)')
    parser.add_argument('--submit', action='store_true', help='Submit order')
    parser.add_argument('--dry-run', action='store_true', help='Simulate order without app interaction')

    args = parser.parse_args()
    create_ordinary_order(
        code=args.code,
        price=args.price,
        quantity=args.quantity,
        action=args.action,
        submit=args.submit,
        dry_run=args.dry_run
    )


if __name__ == "__main__":
    main()
