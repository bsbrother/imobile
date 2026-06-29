#!/usr/bin/env python3
"""
Stop running smart orders — individual or all.

Usage:
  python trading/stop_order.py --code 600006,600279 --submit
  python trading/stop_order.py --submit              # stop ALL running orders
"""

import os, sys, time, argparse, asyncio, re
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.tools import (
    run_cmd, device_tap, device_swipe, get_ui_tree,
    find_element_center, find_button_center
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


# ---------------------------------------------------------------------------
# Main workflow
# ---------------------------------------------------------------------------

def navigate_to_running_orders():
    logger.info("Navigating to running orders...")
    
    ui = get_ui_tree()
    center = None
    for line in ui.split('\n'):
        if '"交易"' in line and 'btm_text' in line:
            m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if m:
                x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                center = ((x1 + x2) // 2, (y1 + y2) // 2)
                break
    if not center:
        for line in ui.split('\n'):
            if '"交易"' in line:
                m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                if m:
                    x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                    c = ((x1 + x2) // 2, (y1 + y2) // 2)
                    if c[1] > 2000:
                        center = c
                        break

    if center:
        logger.info(f"Tapping '交易' at {center}")
        device_tap(*center, sleep_after=2)
    else:
        logger.warning("Could not find '交易', assuming already on trading page.")

    found = False
    for _ in range(3):
        ui = get_ui_tree()
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
            
        ui = get_ui_tree()
        center = find_element_center(ui, '终止')
        if not center:
            logger.warning("Could not find '终止' directly, scrolling...")
            device_swipe(720, 2000, 720, 500, sleep_after=1.5)
            ui = get_ui_tree()
            center = find_element_center(ui, '终止')
            
        if not center:
            logger.warning(f"Cannot find '终止' button for {code} — skipping.")
            run_cmd('adb shell input keyevent 4')
            time.sleep(1)
            continue
            
        logger.info(f"Tapping '终止' at {center}")
        device_tap(*center, sleep_after=2)
        
        if submit:
            ui = get_ui_tree()
            submit_center = find_button_center(ui, '确定') or find_button_center(ui, '确认') or find_element_center(ui, '确定') or find_element_center(ui, '确认') or find_element_center(ui, '提交')
            if submit_center:
                logger.info(f"Tapping submit confirmation at {submit_center}")
                device_tap(*submit_center, sleep_after=2)
                logger.info(f"Successfully stopped order for {code}")
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
    parser.add_argument('--code', type=str, default=None,
                        help="Comma-separated stock codes. If omitted, stops ALL running orders.")
    parser.add_argument('--submit', action='store_true',
                        help="Confirm and stop the orders")
    args = parser.parse_args()

    codes_list = []
    if args.code:
        codes_list = [c.strip() for c in args.code.split(',') if c.strip()]
    else:
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
    stop_orders(codes_list, args.submit)


if __name__ == "__main__":
    main()
