#!/usr/bin/env python3
"""
Stop running smart orders or cancel active ordinary limit orders.

Usage:
  # Smart orders (default):
  python trading/stop_order.py --code 600006,600279 --submit
  python trading/stop_order.py --submit              # stop ALL running orders

  # Ordinary limit orders:
  python trading/stop_order.py --code 600279 --quantity 900 --type ordinary --submit
"""

import os, sys, time, argparse, asyncio, re
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.tools import (
    run_cmd, device_tap, device_swipe, get_ui_tree,
    find_element_center, find_button_center, goto_cancel_order_page
)
from trading.guotai import open_app, login, goto_homepage, parse_csv_data
from trading.sync_app_to_db import (
    get_order_from_app_smart_order_page_structured,
    pre_requirements as sync_pre_req,
)


async def get_all_running_codes() -> list[str]:
    """Scrape the app's running orders tab and return all stock codes."""
    tools, llm, config = await sync_pre_req()
    csv = await get_order_from_app_smart_order_page_structured(
        config, llm, tools, target_tabs=['运行中']
    )
    codes = []
    if csv:
        hdr, rows = parse_csv_data(csv)
        for r in rows:
            code = r[1]
            if code and code != '000000':
                codes.append(code)
    return codes


def navigate_to_running_orders():
    logger.info("Navigating to running smart orders...")
    logger.info("Using replay_page(['今日触发']) to navigate to Smart Orders Dashboard...")
    try:
        replay_page(['今日触发'])
        time.sleep(2)
    except Exception as e:
        logger.warning(f"Replay fallback failed: {e}")

    logger.info("Tapping '实时运行中' at (1051, 1556)")
    device_tap(1051, 1556, sleep_after=2.5)


def stop_smart_orders(codes: list[str], submit: bool):
    term_btn_center = None
    confirm_btn_center = None
    
    for code in codes:
        logger.info(f"Looking for stock code {code} in running smart orders...")
        
        device_swipe(720, 500, 720, 2000, sleep_after=1.5)
        device_swipe(720, 500, 720, 2000, sleep_after=1.5)
        
        found = False
        for _ in range(10):
            ui = get_ui_tree()
            center = find_element_center(ui, code)
            if center:
                logger.info(f"Found {code} at {center}, tapping it")
                device_tap(*center, sleep_after=2)
                found = True
                break
            logger.info(f"Scrolling down to find {code}")
            device_swipe(720, 2000, 720, 500, sleep_after=1.5)
            
        if not found:
            logger.warning(f"'{code}' not found in running orders — skipping.")
            continue
            
        if not term_btn_center:
            ui = get_ui_tree()
            center = find_element_center(ui, '终止')
            if not center:
                logger.warning("Could not find '终止' directly, scrolling...")
                device_swipe(720, 2000, 720, 500, sleep_after=1.5)
                ui = get_ui_tree()
                center = find_element_center(ui, '终止')
            term_btn_center = center
            
        if not term_btn_center:
            logger.warning(f"Cannot find '终止' button for {code} — skipping.")
            run_cmd('adb shell input keyevent 4')
            time.sleep(1)
            continue
            
        logger.info(f"Tapping '终止' at {term_btn_center}")
        device_tap(*term_btn_center, sleep_after=2)
        
        if submit:
            if not confirm_btn_center:
                ui = get_ui_tree()
                submit_center = find_button_center(ui, '确定') or find_button_center(ui, '确认') or find_element_center(ui, '确定') or find_element_center(ui, '确认') or find_element_center(ui, '提交')
                confirm_btn_center = submit_center

            if confirm_btn_center:
                logger.info(f"Tapping submit confirmation at {confirm_btn_center}")
                device_tap(*confirm_btn_center, sleep_after=2.5)
                logger.info(f"Successfully stopped smart order for {code}")
                # Go back to the order list
                run_cmd('adb shell input keyevent 4')
                time.sleep(1.5)
            else:
                logger.warning(f"Could not find confirmation popup for {code}")
        else:
            logger.info("Submit flag not set. Exiting.")
            sys.exit(0)


# ---------------------------------------------------------------------------
# Ordinary limit order cancellation workflow
# ---------------------------------------------------------------------------

def find_cancel_button_for_order(ui_text: str, code: str, quantity: str = None) -> tuple[int, int]:
    """Parse the UI tree to locate the '撤单' button belonging to the order."""
    lines = ui_text.split('\n')
    code_y = -1
    qty_y = -1
    
    for line in lines:
        if f'"{code}"' in line:
            m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if m:
                code_y = (int(m.group(2)) + int(m.group(4))) // 2
        if quantity and f'"{quantity}"' in line and 'entrust_number' in line:
            m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if m:
                qty_y = (int(m.group(2)) + int(m.group(4))) // 2
                
    if code_y != -1 and (not quantity or (qty_y != -1 and abs(code_y - qty_y) < 150)):
        target_y = code_y if qty_y == -1 else (code_y + qty_y) // 2
        logger.info(f"Identified target order row for {code} at Y={target_y}")
        
        best_btn_center = None
        min_dist = 999999
        for line in lines:
            if 'summary_cancel_btn' in line or ('"撤单"' in line and 'Button' in line):
                m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                if m:
                    cx = (int(m.group(1)) + int(m.group(3))) // 2
                    cy = (int(m.group(2)) + int(m.group(4))) // 2
                    dist = abs(cy - target_y)
                    if dist < min_dist:
                        min_dist = dist
                        best_btn_center = (cx, cy)
        if best_btn_center and min_dist < 150:
            return best_btn_center

    # Fallback to the first cancel button visible on screen
    logger.warning("Could not associate row coordinates precisely. Falling back to first visible cancel button.")
    for line in lines:
        if 'summary_cancel_btn' in line or ('"撤单"' in line and 'Button' in line):
            m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if m:
                return (int(m.group(1)) + int(m.group(3))) // 2, (int(m.group(2)) + int(m.group(4))) // 2
    return None


def cancel_ordinary_orders(codes: list[str], quantity: str, submit: bool) -> bool:
    logger.info("Initializing app and navigating to Cancel page...")
    open_app()
    login()
    goto_cancel_order_page()
    
    for code in codes:
        logger.info(f"Looking for active ordinary order for {code}...")
        ui_text = get_ui_tree()
        if code not in ui_text:
            logger.warning(f"❌ Target order for {code} not found on '撤单' page — skipping.")
            continue
            
        cancel_btn = find_cancel_button_for_order(ui_text, code, quantity)
        if not cancel_btn:
            logger.warning(f"❌ Failed to find '撤单' button on screen for {code} — skipping.")
            continue
            
        logger.info(f"Tapping '撤单' button at {cancel_btn}")
        device_tap(*cancel_btn, sleep_after=2)
        
        if submit:
            # Handle secondary confirmation popup with static coordinate (720, 1634)
            confirm_center = (720, 1634)
            found_label = '撤单 (static)'
                    
            if confirm_center:
                logger.info(f"Tapping confirmation button '{found_label}' at {confirm_center}")
                device_tap(*confirm_center, sleep_after=2.5)
                logger.info(f"Order cancellation request submitted for {code}.")
            else:
                logger.warning(f"No confirmation dialog detected for {code}, assuming cancellation submitted directly.")
                
            # Tap back key to close success dialog if any
            run_cmd("adb shell input keyevent 4")
            time.sleep(1.0)
            
            # Verify order is gone from the '撤单' tab
            logger.info("Verifying order is gone from the '撤单' tab...")
            goto_cancel_order_page() # reload page
            ui_text = get_ui_tree()
            if code in ui_text and (not quantity or quantity in ui_text):
                logger.error(f"❌ Verification FAILED: Order {code} is still visible on the '撤单' tab after cancellation!")
                return False
            logger.info(f"✅ Verified: '撤单' tab no longer contains order for {code}.")
            
            # Navigate to the '委托' tab and verify that status is '已撤销'
            logger.info("Navigating to '委托' tab to verify cancelled status...")
            entrust_tab = find_element_center(ui_text, "委托") or (1262, 294)
            logger.info(f"Tapping '委托' tab at {entrust_tab}")
            device_tap(*entrust_tab, sleep_after=2.5)
            
            # Check order row text for '已撤销'
            ui_text = get_ui_tree()
            lines = ui_text.split('\n')
            
            order_found = False
            code_y = -1
            qty_y = -1
            state_y = -1
            state_text = ""
            
            for line in lines:
                if f'"{code}"' in line:
                    m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                    if m:
                        code_y = (int(m.group(2)) + int(m.group(4))) // 2
                if quantity and f'"{quantity}"' in line and 'entrust_number' in line:
                    m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                    if m:
                        qty_y = (int(m.group(2)) + int(m.group(4))) // 2
                if 'summary_state' in line:
                    m = re.search(r'"([^"]+)"\s*-\s*\((\d+),(\d+),(\d+),(\d+)\)', line)
                    if m:
                        state_text = m.group(1)
                        state_y = (int(m.group(3)) + int(m.group(5))) // 2
                        if code_y != -1 and abs(state_y - code_y) < 150:
                            order_found = True
                            if '已撤销' in state_text or '撤单' in state_text:
                                logger.info(f"✅ Verified: Order {code} status is '{state_text}' at Y={state_y}")
                                break
                                
            if not order_found:
                logger.error(f"❌ Verification FAILED: Order {code} not found on '委托' page.")
                return False
        else:
            logger.info("Dry-run, not submitting cancellation.")
            
    return True


def main():
    parser = argparse.ArgumentParser(description="Stop running smart orders or cancel ordinary limit orders.")
    parser.add_argument('--code', type=str, default=None,
                        help="Comma-separated stock codes. If omitted, stops ALL running orders.")
    parser.add_argument('--type', type=str, choices=['smart', 'ordinary'], default='smart',
                        help="Order type: 'smart' (default) or 'ordinary'")
    parser.add_argument('--quantity', type=str, default=None,
                        help="Quantity of the ordinary order (optional, used if type is ordinary)")
    parser.add_argument('--submit', action='store_true',
                        help="Confirm and stop the orders")
    args = parser.parse_args()

    codes_list = []
    if args.code:
        codes_list = [c.strip() for c in args.code.split(',') if c.strip()]
        
    if args.type == 'ordinary':
        if not codes_list:
            logger.error("Must specify --code to cancel ordinary orders.")
            sys.exit(1)
        success = cancel_ordinary_orders(codes_list, args.quantity, args.submit)
        if not success:
            sys.exit(1)
        logger.info("Done.")
        return

    # Smart order path
    if not codes_list:
        logger.info("No --code provided. Scraping all running orders from app...")
        codes_list = asyncio.run(get_all_running_codes())
        if not codes_list:
            logger.info("No running orders found in app.")
            return
        logger.info(f"Found {len(codes_list)} running orders: {','.join(codes_list)}")
    
    if not codes_list:
        logger.error("No codes to stop.")
        sys.exit(1)
        
    logger.info("Initializing app and logging in...")
    open_app()
    login()
    goto_homepage()
    
    navigate_to_running_orders()
    stop_smart_orders(codes_list, args.submit)


if __name__ == "__main__":
    main()
