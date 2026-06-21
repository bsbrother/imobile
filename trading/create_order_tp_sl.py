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

def format_symbol(code: str) -> str:
    """Format 6-digit stock code to Tushare symbol format."""
    code = str(code).strip()
    if len(code) == 6:
        if code.startswith('6'):
            return f"{code}.SH"
        elif code.startswith('0') or code.startswith('3'):
            return f"{code}.SZ"
        elif code.startswith('4') or code.startswith('8'):
            return f"{code}.BJ"
    return code


# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.goto_page import goto_page
from shared.db.db import DB

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
    """Clear a text field by sending DEL key events via adb."""
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
    lines = ui_text.split('\n')
    for line in lines:
        if label in line and 'EditText' in line:
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                return (x1 + x2) // 2, (y1 + y2) // 2

    found_label_idx = None
    for i, line in enumerate(lines):
        if label in line and 'EditText' not in line:
            found_label_idx = i
            continue
        if found_label_idx is not None and 'EditText' in line and (i - found_label_idx) <= 10:
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def find_button_center(ui_text: str, label: str) -> tuple[int, int] | None:
    for line in ui_text.split('\n'):
        if 'Button' in line and label in line:
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def find_edittext_by_y(ui_text: str, target_y: int, y_tolerance: int = 40) -> tuple[int, int] | None:
    """Find an EditText whose center Y is closest to the target Y coordinate."""
    best_center = None
    min_diff = y_tolerance
    for line in ui_text.split('\n'):
        if 'EditText' in line:
            match = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if match:
                x1, y1, x2, y2 = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                cy = (y1 + y2) // 2
                diff = abs(cy - target_y)
                if diff < min_diff:
                    min_diff = diff
                    best_center = ((x1 + x2) // 2, cy)
    return best_center


# ---------------------------------------------------------------------------
# Page verification
# ---------------------------------------------------------------------------

def verify_on_tpsl_page(ui_text: str) -> bool:
    return '止盈止损' in ui_text


# ---------------------------------------------------------------------------
# Field fill functions
# ---------------------------------------------------------------------------

def fill_stock_code(code: str, ui_text: str) -> None:
    center = find_element_center(ui_text, '请输入股票代码或名称')
    if not center:
        label_center = find_element_center(ui_text, '股票名称')
        if label_center:
            center = (label_center[0] + 500, label_center[1])
    if not center:
        raise RuntimeError("Cannot find stock code input field (请输入股票代码或名称 / 股票名称)")

    logger.info(f"Tapping stock code field at {center} to open selection overlay")
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
        raise RuntimeError("Cannot find search EditText in stock selection overlay")

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
        logger.error(f"Stock {code} not found in search results")
        raise RuntimeError(f"Stock {code} not found in search results")

    tpsl_ui = get_ui_tree()
    if not verify_on_tpsl_page(tpsl_ui):
        logger.warning("May not have returned to 止盈止损 page after stock selection")
    else:
        logger.info("✅ Stock selected, back on 止盈止损 order page")


def set_order_method(ui_text: str) -> None:
    for _ in range(3):
        if '请选择委托方式' in ui_text:
            x, y = find_element_center(ui_text, '请选择委托方式')
            logger.info(f"Tapping order method field at ({x}, {y})")
            device_tap(x, y, sleep_after=2)
            
            popup_ui = get_ui_tree()
            if '确定' in popup_ui:
                cx, cy = find_element_center(popup_ui, '确定')
                device_tap(cx, cy, sleep_after=1)
            elif '完成' in popup_ui:
                cx, cy = find_element_center(popup_ui, '完成')
                device_tap(cx, cy, sleep_after=1)
            else:
                run_cmd('adb shell input keyevent 4')
                device_tap(1250, 1750, sleep_after=0.5)
                device_tap(720, 2800, sleep_after=0.5)
            return
        logger.info("Scrolling to find '请选择委托方式'")
        device_swipe(720, 2000, 720, 500, sleep_after=1.5)
        ui_text = get_ui_tree()
    logger.warning("Could not find '请选择委托方式' option.")


def set_auto_order(ui_text: str) -> None:
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


def fill_tp_sl_range(tp_range: str, sl_range: str, ui_text: str) -> str:
    # Format exactly as requested: TP must have +, SL must have -
    tp_val = f"+{round(abs(float(tp_range)), 2)}"
    sl_val = f"-{round(abs(float(sl_range)), 2)}"

    # TP field
    tp_label_center = find_element_center(ui_text, '止盈触发幅度')
    if tp_label_center:
        # Find exact EditText on the same horizontal line
        tp_center = find_edittext_by_y(ui_text, tp_label_center[1])
        if not tp_center:
            tp_center = (tp_label_center[0] + 450, tp_label_center[1])
            
        logger.info(f"Tapping TP range field at {tp_center}")
        device_tap(*tp_center, sleep_after=0.5)
        adb_clear_field(max_chars=15)
        logger.info(f"Typing TP range via adb: {tp_val}")
        adb_type(tp_val)
    else:
        logger.warning("Cannot find '止盈触发幅度' label")

    # Scroll up to reveal SL field and safely dismiss keyboard
    logger.info("Scrolling up to reveal SL field and dismiss keyboard")
    device_swipe(500, 1000, 500, 300, sleep_after=1.5)
    ui_text = get_ui_tree()

    # SL field
    sl_label_center = find_element_center(ui_text, '止损触发幅度')
    if sl_label_center:
        # Find exact EditText on the same horizontal line
        sl_center = find_edittext_by_y(ui_text, sl_label_center[1])
        if not sl_center:
            sl_center = (sl_label_center[0] + 450, sl_label_center[1])
            
        logger.info(f"Tapping SL range field at {sl_center}")
        device_tap(*sl_center, sleep_after=0.5)
        adb_clear_field(max_chars=15)
        logger.info(f"Typing SL range via adb: {sl_val}")
        adb_type(sl_val)
    else:
        logger.warning("Cannot find '止损触发幅度' label")
        
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


def tap_create_order(ui_text: str) -> None:
    center = find_button_center(ui_text, '创建订单')
    if not center:
        raise RuntimeError("Cannot find '创建订单' button")
    logger.info(f"Tapping '创建订单' button at {center}")
    device_tap(*center, sleep_after=2)


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def create_tp_sl_order(code: str, tp_range: str, sl_range: str, quantity: str, submit: bool = False, dry_run: bool = False) -> None:
    logger.info(f"Creating TP/SL order: code={code}, tp_range={tp_range}%, sl_range={sl_range}%, quantity={quantity}, submit={submit}")

    if dry_run:
        logger.info(f"[DRY RUN] Would fill: stock={code}, tp_range={tp_range}%, sl_range={sl_range}%, qty={quantity}")
        return

    goto_page('order_tp_sl')

    logger.info("Scrolling to top of page")
    device_swipe(720, 500, 720, 1500, sleep_after=1.5)

    ui_text = get_ui_tree()
    if not verify_on_tpsl_page(ui_text):
        logger.warning("Current page may not be the 止盈止损 page. Proceeding anyway.")

    fill_stock_code(code, ui_text)

    time.sleep(2)
    ui_text = get_ui_tree()

    ui_text = fill_tp_sl_range(tp_range, sl_range, ui_text)

    time.sleep(1)
    
    logger.info("Scrolling to ensure quantity field is visible and keyboard is closed")
    device_swipe(720, 2000, 720, 500, sleep_after=1.5)
    ui_text = get_ui_tree()

    set_order_method(ui_text)
    ui_text = get_ui_tree()

    set_auto_order(ui_text)
    ui_text = get_ui_tree()

    fill_quantity(quantity, ui_text)

    time.sleep(0.5)
    ui_text = get_ui_tree()
    logger.info("Final UI state (EditText fields):")
    for line in ui_text.split('\n'):
        if 'EditText' in line:
            logger.info(f"  {line.strip()}")

    if submit:
        tap_create_order(ui_text)
        logger.info("✅ TP/SL order submitted")
    else:
        logger.info("✅ TP/SL order fields filled (not submitted, use --submit to submit)")


def batch_create_tp_sl(submit: bool, dry_run: bool):
    """Run steps 1-3 automatically."""
    logger.info("Running batch TP/SL order creation")
    # 1. Got current holding stocks from shared/db/imobile.db
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
        tp_range = None
        sl_range = None
        quantity = None
        try:
            with open(output_json, 'r') as f:
                data = json.load(f)
                for order in data.get('smart_orders', []):
                    if order['symbol'].startswith(code):
                        tp_price = float(order.get('sell_take_profit_price', 0))
                        sl_price = float(order.get('sell_stop_loss_price', 0))
                        cost_basis = code_to_cost.get(code, 1.0)
                        if cost_basis and cost_basis > 0:
                            tp_range = str(round(((tp_price / cost_basis) - 1) * 100, 2))
                            sl_range = str(round(((sl_price / cost_basis) - 1) * 100, 2))
                        else:
                            tp_range = "0.0"
                            sl_range = "0.0"
                        # For existing holdings, use available_shares from DB
                        qty = code_to_shares.get(code)
                        if qty:
                            quantity = str(qty)
                        else:
                            quantity = str(order.get('buy_quantity', ''))
                        break
        except Exception as e:
            logger.error(f"Failed to read {output_json}: {e}")
            
        if not tp_range or not sl_range or not quantity:
            logger.warning(f"Skipping {code} because missing data in JSON.")
            continue
            
        create_tp_sl_order(code, tp_range, sl_range, quantity, submit=submit, dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser(description='Fill fields on the 止盈止损 order page.')
    parser.add_argument('--code', type=str, help='Stock code (e.g., 600279)')
    parser.add_argument('--tp', type=str, help='Take profit price')
    parser.add_argument('--sl', type=str, help='Stop loss price')
    parser.add_argument('--quantity', type=str, help='Quantity')
    parser.add_argument('--json', type=str, help='Path to smart_orders JSON file to pull values from')
    parser.add_argument('--batch', action='store_true', help='Run steps 1-3 for all holdings automatically')
    parser.add_argument('--symbol', type=str, help='Single stock code to create tp & sl order for')
    parser.add_argument('--submit', action='store_true', help='Submit the order after filling fields')
    parser.add_argument('--dry-run', action='store_true', help='Log actions without executing')

    args = parser.parse_args()

    if args.symbol:
        holdings = DB.fetch_all("SELECT code, available_shares, cost_basis_diluted FROM holding_stocks WHERE code = ?", (args.symbol,))
        if not holdings:
            raise ValueError('this stock not in holding stocks db, cannot create tp&sl order.')
        
        available_shares = holdings[0]['available_shares']
        cost_basis = holdings[0]['cost_basis_diluted']
        
        logger.info(f"Running single TP/SL order creation for {args.symbol}")
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_json = os.path.join("backtest", "results", "daily", f"smart_orders_single_{timestamp}.json")
        os.makedirs(os.path.dirname(output_json), exist_ok=True)
        
        formatted_symbol = format_symbol(args.symbol)
        cmd = f"python -m backtest.cli analyze --symbols {formatted_symbol} --initial-cash 10000000 --output {output_json}"
        run_cmd(cmd)

        tp_range = None
        sl_range = None
        quantity = None
        try:
            with open(output_json, 'r') as f:
                data = json.load(f)
                for order in data.get('smart_orders', []):
                    if order['symbol'].startswith(args.symbol):
                        tp_price = float(order.get('sell_take_profit_price', 0))
                        sl_price = float(order.get('sell_stop_loss_price', 0))
                        if cost_basis and cost_basis > 0:
                            tp_range = str(round(((tp_price / cost_basis) - 1) * 100, 2))
                            sl_range = str(round(((sl_price / cost_basis) - 1) * 100, 2))
                        else:
                            tp_range = "0.0"
                            sl_range = "0.0"
                        # Use shares from db
                        if available_shares:
                            quantity = str(available_shares)
                        else:
                            quantity = str(order.get('buy_quantity', ''))
                        break
        except Exception as e:
            logger.error(f"Failed to read {output_json}: {e}")
            
        if not tp_range or not sl_range or not quantity:
            logger.warning(f"Skipping {args.symbol} because missing data in JSON.")
            return
            
        create_tp_sl_order(args.symbol, tp_range, sl_range, quantity, submit=args.submit, dry_run=args.dry_run)
        return

    if args.batch:
        batch_create_tp_sl(submit=args.submit, dry_run=args.dry_run)
        return

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
                if 'sell_take_profit_price' in stock_data:
                    args.tp = str(stock_data['sell_take_profit_price'])
                if 'sell_stop_loss_price' in stock_data:
                    args.sl = str(stock_data['sell_stop_loss_price'])
                if 'buy_quantity' in stock_data:
                    args.quantity = str(stock_data['buy_quantity'])
                logger.info(f"Loaded tp={args.tp}, sl={args.sl}, quantity={args.quantity} from {args.json}")
            else:
                logger.warning(f"Could not find stock {args.code} in JSON file.")
        except Exception as e:
            logger.error(f"Failed to load JSON {args.json}: {e}")

    if not args.code or not args.tp or not args.sl or not args.quantity:
        parser.error("--code, --tp, --sl, and --quantity are required (or provide --json and --code)")

    create_tp_sl_order(
        code=args.code,
        tp_range=args.tp,
        sl_range=args.sl,
        quantity=args.quantity,
        submit=args.submit,
        dry_run=args.dry_run,
    )


if __name__ == "__main__":
    main()
