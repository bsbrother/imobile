#!/usr/bin/env python3
"""
Fill all fields on the "到价卖出" (sell at target price) order page.

Prerequisites:
  - The app must be connected via ADB and logged in (handled automatically via goto_page).
  - Run `mobilerun device ui` first to verify element positions.

Usage:
  python trading/create_order_sell.py --code 600279 --price 3.97 --quantity 900
  python trading/create_order_sell.py --code 600279 --price 3.97 --quantity 900 --submit
  python trading/create_order_sell.py --code 600279 --price 3.97 --quantity 900 --dry-run

Interaction notes:
  - Stock code: tapping opens a separate "选择股票" overlay with a search EditText.
    Type the code in the overlay's search field, select the matching stock from
    results, and the app auto-returns to the sell order page.
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

from loguru import logger

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.goto_page import goto_page


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

def run_cmd(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command and return the result."""
    logger.debug(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        logger.error(f"Command failed: {cmd}\nstderr: {result.stderr}")
        raise RuntimeError(f"Command failed: {cmd}")
    return result


def device_tap(x: int, y: int, sleep_after: float = 1.0) -> None:
    """Tap at screen coordinates."""
    run_cmd(f"mobilerun device tap {x} {y}")
    time.sleep(sleep_after)


def device_swipe(x1: int, y1: int, x2: int, y2: int, sleep_after: float = 1.0) -> None:
    """Swipe on the screen."""
    run_cmd(f"mobilerun device swipe {x1} {y1} {x2} {y2}")
    time.sleep(sleep_after)


def device_type(text: str, clear: bool = True) -> None:
    """Type text into the currently focused field via mobilerun."""
    clear_flag = " --clear" if clear else ""
    run_cmd(f'mobilerun device type "{text}"{clear_flag}')
    time.sleep(0.5)


def adb_type(text: str) -> None:
    """Type text via adb shell input text (works for unfocused WebView fields)."""
    run_cmd(f'adb shell input text "{text}"')
    time.sleep(0.3)


def adb_clear_field(max_chars: int = 20) -> None:
    """Clear a text field by sending DEL key events via adb.

    Sends enough KEYCODE_DEL (67) events to clear any existing value.
    """
    for _ in range(max_chars):
        run_cmd("adb shell input keyevent 67", check=False)
    time.sleep(0.3)


def get_ui_tree() -> str:
    """Get the current UI accessibility tree."""
    result = run_cmd("mobilerun device ui", check=False)
    return result.stdout


# ---------------------------------------------------------------------------
# UI element finders
# ---------------------------------------------------------------------------

def find_element_center(ui_text: str, label: str, exclude_edittext: bool = False) -> tuple[int, int] | None:
    """Find an element by label text in the UI tree and return its center (x, y).

    Searches for lines containing the label and a bounds pattern like (x1,y1,x2,y2).
    Returns the first match.
    """
    for line in ui_text.split('\n'):
        if label in line:
            if exclude_edittext and 'EditText' in line:
                continue
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def find_edittext_near_label(ui_text: str, label: str) -> tuple[int, int] | None:
    """Find the EditText element that appears near a given label in the UI tree.

    Looks for lines containing *both* the label and 'EditText', then falls back
    to finding the first EditText on a line *after* a line containing the label.
    Returns the center coordinates of the EditText.
    """
    lines = ui_text.split('\n')

    # Pass 1: look for EditText on the same line as the label
    for line in lines:
        if label in line and 'EditText' in line:
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                return (x1 + x2) // 2, (y1 + y2) // 2

    # Pass 2: find first EditText after the label (within 5 lines)
    found_label_idx = None
    for i, line in enumerate(lines):
        if label in line and 'EditText' not in line:
            found_label_idx = i
            continue
        if found_label_idx is not None and 'EditText' in line and (i - found_label_idx) <= 5:
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def find_button_center(ui_text: str, label: str) -> tuple[int, int] | None:
    """Find a Button element by label text in the UI tree and return its center."""
    for line in ui_text.split('\n'):
        if 'Button' in line and label in line:
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                return (x1 + x2) // 2, (y1 + y2) // 2
    return None


# ---------------------------------------------------------------------------
# Page verification
# ---------------------------------------------------------------------------

def verify_on_sell_page(ui_text: str) -> bool:
    """Verify the app is currently on the sell order page (到价卖出)."""
    return '到价卖出' in ui_text


# ---------------------------------------------------------------------------
# Field fill functions
# ---------------------------------------------------------------------------

def fill_stock_code(code: str, ui_text: str) -> None:
    """Fill in the stock code field.

    Flow:
    1. Tap the "请输入股票代码或名称" area to open the stock selection overlay.
    2. In the overlay, find the search EditText and type the stock code.
    3. Wait for search results, then tap the matching stock.
    4. The app auto-returns to the sell order page.
    """
    # Step 1: Tap stock code area to open the selection overlay
    center = find_element_center(ui_text, '请输入股票代码或名称')
    if not center:
        # If a stock is already selected, the hint text is gone.
        # Find the '股票名称' label and tap to the right of it (the actual clickable area).
        label_center = find_element_center(ui_text, '股票名称')
        if label_center:
            center = (label_center[0] + 500, label_center[1])
    if not center:
        raise RuntimeError("Cannot find stock code input field (请输入股票代码或名称 / 股票名称)")

    logger.info(f"Tapping stock code field at {center} to open selection overlay")
    device_tap(*center, sleep_after=2)

    # Step 2: Read UI of the selection overlay
    overlay_ui = get_ui_tree()
    if '选择股票' not in overlay_ui:
        logger.warning("Stock selection overlay may not have opened (missing '选择股票')")

    # Step 3: Find the search EditText in the overlay
    search_center = find_edittext_near_label(overlay_ui, '股票名称')
    if not search_center:
        # Look for any EditText near the top of the overlay
        for line in overlay_ui.split('\n'):
            if 'EditText' in line:
                match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                if match:
                    x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                    # Only use EditTexts in the top portion (search area)
                    if y1 < 500:
                        search_center = ((x1 + x2) // 2, (y1 + y2) // 2)
                        break
    if not search_center:
        raise RuntimeError("Cannot find search EditText in stock selection overlay")

    logger.info(f"Tapping search field at {search_center}")
    device_tap(*search_center, sleep_after=0.5)

    logger.info(f"Typing stock code: {code}")
    device_type(code, clear=True)
    time.sleep(2)  # Wait for search results

    # Step 4: Read UI to find search results and select the matching stock
    results_ui = get_ui_tree()
    stock_center = find_element_center(results_ui, code, exclude_edittext=True)
    if stock_center:
        logger.info(f"Selecting stock {code} at {stock_center}")
        device_tap(*stock_center, sleep_after=3)  # Wait for auto-return to sell page
    else:
        logger.error(f"Stock {code} not found in search results")
        raise RuntimeError(f"Stock {code} not found in search results")

    # Step 5: Verify we're back on the sell page
    sell_ui = get_ui_tree()
    if not verify_on_sell_page(sell_ui):
        logger.warning("May not have returned to sell page after stock selection")
    else:
        logger.info("✅ Stock selected, back on sell order page")


def set_trigger_condition_ge(ui_text: str) -> None:
    """Set the trigger condition to '当股价 >='."""
    center = find_element_center(ui_text, "当股价 ≥")
    if center:
        logger.info(f"Setting trigger condition to '当股价 ≥' at {center}")
        device_tap(*center)
    else:
        logger.warning("Could not find '当股价 ≥' option.")


def set_order_method(ui_text: str) -> None:
    """Tap order method (委托方式) and confirm the default popup."""
    for _ in range(3):
        if '请选择委托方式' in ui_text:
            x, y = find_element_center(ui_text, '请选择委托方式')
            logger.info(f"Tapping order method field at ({x}, {y})")
            device_tap(x, y, sleep_after=2)
            
            # Check for '确定' or '完成'
            popup_ui = get_ui_tree()
            if '确定' in popup_ui:
                cx, cy = find_element_center(popup_ui, '确定')
                logger.info(f"Tapping '确定' at ({cx}, {cy})")
                device_tap(cx, cy, sleep_after=1)
            elif '完成' in popup_ui:
                cx, cy = find_element_center(popup_ui, '完成')
                logger.info(f"Tapping '完成' at ({cx}, {cy})")
                device_tap(cx, cy, sleep_after=1)
            else:
                logger.warning("Could not find '确定' or '完成' in popup. Pressing system back and tapping typical confirm positions.")
                run_cmd('adb shell input keyevent 4') # BACK key to close if it's a dialog and confirm isn't needed, but user said 'tap confirm button'.
                device_tap(1250, 1750, sleep_after=0.5)
                device_tap(720, 2800, sleep_after=0.5)
            return
        logger.info("Scrolling to find '请选择委托方式'")
        device_swipe(720, 2000, 720, 500, sleep_after=1.5)
        ui_text = get_ui_tree()
    logger.warning("Could not find '请选择委托方式' option.")


def set_auto_order(ui_text: str) -> None:
    """Set the order type to '自动下单'."""
    for _ in range(3):
        if '自动下单' in ui_text:
            center = find_element_center(ui_text, "自动下单")
            logger.info(f"Setting order type to '自动下单' at {center}")
            device_tap(*center)
            return
        logger.info("Scrolling to find '自动下单'")
        device_swipe(720, 2000, 720, 500, sleep_after=1.5)
        ui_text = get_ui_tree()
    logger.warning("Could not find '自动下单' option.")

def fill_trigger_price(price: str, ui_text: str) -> None:
    """Fill in the trigger price field.

    This field is a WebView EditText that does NOT get normal keyboard focus.
    Uses adb shell input keyevent (DEL) + adb shell input text.
    """
    center = find_edittext_near_label(ui_text, '触发价格')
    if not center:
        raise RuntimeError("Cannot find trigger price EditText. Is a stock selected?")

    logger.info(f"Tapping trigger price field at {center}")
    device_tap(*center, sleep_after=0.5)

    logger.info("Clearing trigger price field via DEL keys")
    adb_clear_field(max_chars=15)

    logger.info(f"Typing trigger price via adb: {price}")
    adb_type(price)


def fill_quantity(quantity: str, ui_text: str) -> None:
    """Fill in the quantity field.

    Finds the EditText after "卖出数量" (or "买入数量") label.
    This field works with mobilerun device type --clear.
    """
    center = find_edittext_near_label(ui_text, '卖出数量')
    if not center:
        center = find_edittext_near_label(ui_text, '买入数量')
    if not center:
        raise RuntimeError("Cannot find quantity EditText")

    logger.info(f"Tapping quantity field at {center}")
    device_tap(*center, sleep_after=0.5)

    logger.info("Clearing quantity field via DEL keys")
    adb_clear_field(max_chars=15)

    logger.info(f"Typing quantity via adb: {quantity}")
    adb_type(quantity)


def tap_create_order(ui_text: str) -> None:
    """Tap the '创建订单' (Create Order) button."""
    center = find_button_center(ui_text, '创建订单')
    if not center:
        raise RuntimeError("Cannot find '创建订单' button")

    logger.info(f"Tapping '创建订单' button at {center}")
    device_tap(*center, sleep_after=2)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def create_sell_order(code: str, price: str, quantity: str, submit: bool = False, dry_run: bool = False) -> None:
    """Fill all fields on the sell order page.

    Args:
        code: Stock code (e.g., '600279')
        price: Trigger price (e.g., '3.97')
        quantity: Sell quantity (e.g., '900')
        submit: If True, tap the '创建订单' button to submit
        dry_run: If True, only log actions without executing
    """
    logger.info(f"Creating sell order: code={code}, price={price}, quantity={quantity}, submit={submit}")

    if dry_run:
        logger.info("[DRY RUN] Would fill: stock={}, price={}, qty={}".format(code, price, quantity))
        return

    # Step 0: Navigate to homepage, login, then open the 到价卖出 page
    goto_page('order_sell')

    # Scroll to the top of the page to ensure the stock code field is visible
    logger.info("Scrolling to top of page")
    device_swipe(720, 500, 720, 1500, sleep_after=1.5)

    # Step 1: Read current UI state and verify sell page
    ui_text = get_ui_tree()
    if not verify_on_sell_page(ui_text):
        logger.warning("Current page may not be the sell order page (到价卖出). Proceeding anyway.")

    # Step 2: Fill stock code (opens overlay → search → select → auto-return)
    fill_stock_code(code, ui_text)

    # Step 3: Wait for stock data to load, then re-read UI
    time.sleep(2)
    ui_text = get_ui_tree()

    # Step 3.5: Set trigger condition (当股价 >=)
    set_trigger_condition_ge(ui_text)

    # Step 4: Fill trigger price (WebView EditText — uses adb input)
    fill_trigger_price(price, ui_text)

    # Step 5: Re-read UI after price input
    time.sleep(1)
    
    # Scroll up to ensure the custom keyboard is closed and quantity field is visible
    logger.info("Scrolling to ensure quantity field is visible and keyboard is closed")
    device_swipe(720, 2000, 720, 500, sleep_after=1.5)
    ui_text = get_ui_tree()

    # Step 5.2: Set order method (委托方式)
    set_order_method(ui_text)
    ui_text = get_ui_tree()

    # Step 5.5: Set order type to auto (自动下单)
    set_auto_order(ui_text)
    ui_text = get_ui_tree()

    # Step 6: Fill quantity
    fill_quantity(quantity, ui_text)

    # Step 7: Verify all fields
    time.sleep(0.5)
    ui_text = get_ui_tree()
    logger.info("Final UI state (EditText fields):")
    for line in ui_text.split('\n'):
        if 'EditText' in line:
            logger.info(f"  {line.strip()}")

    # Step 8: Optionally submit
    if submit:
        tap_create_order(ui_text)
        logger.info("✅ Sell order submitted")
    else:
        logger.info("✅ Sell order fields filled (not submitted, use --submit to submit)")


def main():
    parser = argparse.ArgumentParser(
        description='Fill fields on the 到价卖出 (sell at target price) order page.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fill fields only (no submit):
  python trading/create_order_sell.py --code 600279 --price 3.97 --quantity 900

  # Fill and submit:
  python trading/create_order_sell.py --code 600279 --price 3.97 --quantity 900 --submit

  # Dry run (log only):
  python trading/create_order_sell.py --code 600279 --price 3.97 --quantity 900 --dry-run

  # Show current UI tree for debugging:
  python trading/create_order_sell.py --show-ui
        """
    )
    parser.add_argument('--code', type=str, help='Stock code (e.g., 600279)')
    parser.add_argument('--price', type=str, help='Trigger price (e.g., 3.97)')
    parser.add_argument("--quantity", type=str, help="Quantity to sell")
    parser.add_argument("--json", type=str, help="Path to smart_orders JSON file to pull values from")
    parser.add_argument('--submit', action='store_true',
                        help='Submit the order after filling fields')
    parser.add_argument('--dry-run', action='store_true',
                        help='Log actions without executing')
    parser.add_argument('--show-ui', action='store_true',
                        help='Show current UI tree and exit')

    args = parser.parse_args()

    if args.json:
        try:
            with open(args.json, 'r') as f:
                data = json.load(f)
            stock_data = None
            for order in data.get('smart_orders', []):
                if order['symbol'].startswith(args.code):
                    stock_data = order
                    break
            
            if stock_data:
                # Use take_profit_price for sell order
                if 'sell_take_profit_price' in stock_data:
                    args.price = str(stock_data['sell_take_profit_price'])
                if 'buy_quantity' in stock_data:
                    args.quantity = str(stock_data['buy_quantity'])
                logger.info(f"Loaded price={args.price} and quantity={args.quantity} from {args.json}")
            else:
                logger.warning(f"Could not find stock {args.code} in JSON file.")
        except Exception as e:
            logger.error(f"Failed to load JSON {args.json}: {e}")

    if args.show_ui:
        print(get_ui_tree())
        return

    if not args.code or not args.price or not args.quantity:
        parser.error("--code, --price, and --quantity are required (unless --show-ui)")

    create_sell_order(
        code=args.code,
        price=args.price,
        quantity=args.quantity,
        submit=args.submit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
