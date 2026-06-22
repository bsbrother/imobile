#!/usr/bin/env python3
"""
Stop a running smart order.

Prerequisites:
  - The app must be connected via ADB.

Usage:
  python trading/stop_order.py --code 600006,600279 --submit
"""

import os
import sys
import time
import argparse
import subprocess
import re

from loguru import logger

# Ensure project root is in path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.tools import (
    run_cmd, device_tap, device_swipe, get_ui_tree,
    find_element_center, find_button_center
)
from trading.guotai import open_app, login, goto_homepage


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def navigate_to_running_orders():
    logger.info("Navigating to running orders...")
    
    # Find and tap '交易' (exact match to avoid '当日交易')
    ui = get_ui_tree()
    center = find_element_center(ui, '"交易"') or find_element_center(ui, '交易')
    # If we found it, but it's high up, it might be the wrong one if we only did '交易'. Let's trust exact match.
    # To be safe, we'll use exact match only.
    center = None
    for line in ui.split('\n'):
        if '"交易"' in line and 'btm_text' in line:
            import re
            m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if m:
                x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                center = ((x1 + x2) // 2, (y1 + y2) // 2)
                break
    if not center:
        # fallback to exact match without btm_text
        for line in ui.split('\n'):
            if '"交易"' in line:
                import re
                m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                if m:
                    x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                    center = ((x1 + x2) // 2, (y1 + y2) // 2)
                    # Don't break here, we prefer ones at the bottom (y > 2000)
                    if center[1] > 2000:
                        break

    if center:
        logger.info(f"Tapping '交易' at {center}")
        device_tap(*center, sleep_after=2)
    else:
        logger.warning("Could not find '交易', assuming already on trading page or need to scroll.")

    # Find and tap '今日触发, 运行中' under '智能订单' on homepage
    found = False
    for _ in range(3):
        ui = get_ui_tree()
        # The text might be exactly "今日触发", or "运行中"
        center = find_element_center(ui, '"今日触发"') or find_element_center(ui, '"运行中"') or find_element_center(ui, '今日触发') or find_element_center(ui, '运行中')
        if center:
            logger.info(f"Tapping '今日触发 / 运行中' at {center}")
            device_tap(*center, sleep_after=2)
            found = True
            break
        logger.info("Scrolling to find '今日触发 / 运行中'")
        device_swipe(720, 1500, 720, 500, sleep_after=1.5)
        
    if not found:
        raise RuntimeError("Cannot find '今日触发' or '运行中' on homepage")

    # Now we are on the Smart Order dashboard. We need to tap '实时运行中' to see the list.
    found = False
    for _ in range(3):
        ui = get_ui_tree()
        center = find_element_center(ui, '"实时运行中"') or find_element_center(ui, '实时运行中') or find_element_center(ui, '"运行中"') or find_element_center(ui, '运行中')
        if center:
            logger.info(f"Tapping '实时运行中' at {center}")
            device_tap(*center, sleep_after=2)
            found = True
            break
        logger.info("Scrolling to find '实时运行中'")
        device_swipe(720, 1500, 720, 500, sleep_after=1.5)
        
    if not found:
        raise RuntimeError("Cannot find '实时运行中' on Smart Order dashboard")


def stop_orders(codes: list[str], submit: bool):
    for code in codes:
        logger.info(f"Looking for stock code {code} in running orders...")
        
        # Scroll to top to reset the view
        device_swipe(720, 500, 720, 2000, sleep_after=1.5)
        device_swipe(720, 500, 720, 2000, sleep_after=1.5)
        
        found = False
        for _ in range(10):  # scroll max 10 times
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
            raise RuntimeError(f"'{code} not found'")
            
        # Now we are supposedly in the detail view or the item expanded.
        # Tap '终止'
        ui = get_ui_tree()
        center = find_element_center(ui, '终止')
        if not center:
            logger.warning("Could not find '终止' directly, scrolling...")
            device_swipe(720, 2000, 720, 500, sleep_after=1.5)
            ui = get_ui_tree()
            center = find_element_center(ui, '终止')
            
        if not center:
            raise RuntimeError(f"Cannot find '终止' button for {code}")
            
        logger.info(f"Tapping '终止' at {center}")
        device_tap(*center, sleep_after=2)
        
        if submit:
            ui = get_ui_tree()
            # Support multiple possible confirm button texts including exact match for 'submit'
            submit_center = find_button_center(ui, '确定') or find_button_center(ui, '确认') or find_element_center(ui, '确定') or find_element_center(ui, '确认') or find_element_center(ui, '提交')
            if submit_center:
                logger.info(f"Tapping submit confirmation at {submit_center}")
                device_tap(*submit_center, sleep_after=2)
                logger.info(f"Successfully stopped order for {code}")
                # Go back to the list for the next code if any
                run_cmd('adb shell input keyevent 4')
                time.sleep(1)
                ui = get_ui_tree()
                if '运行中' not in ui:
                    run_cmd('adb shell input keyevent 4')
                    time.sleep(1)
            else:
                logger.warning(f"Could not find confirmation popup for {code}")
        else:
            logger.info("Submit flag not set. Exiting.")
            sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="Stop running smart orders.")
    parser.add_argument('--code', type=str, required=True, help="Comma-separated list of stock codes (e.g., 600006,600279)")
    parser.add_argument('--submit', action='store_true', help="Confirm and stop the order, otherwise exit without confirming")
    
    args = parser.parse_args()
    codes_list = [c.strip() for c in args.code.split(',') if c.strip()]
    
    if not codes_list:
        logger.error("No valid codes provided.")
        sys.exit(1)
        
    logger.info("Initializing app and logging in...")
    open_app()
    login()
    goto_homepage()
    
    navigate_to_running_orders()
    stop_orders(codes_list, args.submit)


if __name__ == "__main__":
    main()
