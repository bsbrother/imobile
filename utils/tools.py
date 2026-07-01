#!/usr/bin/env python3
"""
Consolidated ADB/device utility tools and page navigation helper for Guotai App.
"""

import os, sys, time, argparse, subprocess, asyncio, re
from loguru import logger

# Add project root to path for imports to work
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PAGE_LABELS = {
    'order_buy':   '到价买入',
    'order_sell':  '到价卖出',
    'order_tp_sl': '止盈止损',
}


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
    """Find an element by label text in the UI tree and return its center (x, y)."""
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
    """Find the EditText element that appears near a given label in the UI tree."""
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
    """Find a Button element by label text in the UI tree and return its center."""
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
# Business-logic Shared Helpers
# ---------------------------------------------------------------------------

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


def check_stock_in_orders_list(code: str) -> bool:
    """Scroll down and search the stock code in the orders list."""
    last_ui = ""
    for scroll_idx in range(8):  # Scroll max 8 times
        ui = get_ui_tree()
        if code in ui:
            logger.info(f"Found stock code {code} in orders list.")
            return True
        if "暂无数据" in ui:
            logger.info("No data in this tab.")
            break
        current_view_lines = "\n".join(line for line in ui.split('\n') if 'View' in line)
        if current_view_lines == last_ui:
            logger.info("Scroll reached bottom of list.")
            break
        last_ui = current_view_lines
        
        logger.info("Scrolling down to load more orders...")
        device_swipe(720, 2000, 720, 500, sleep_after=1.5)
    return False


def check_duplicate_orders(code: str) -> None:
    """Navigate to running/triggered orders and check if code exists."""
    from trading.guotai import replay_page
    logger.info(f"Checking if order for {code} already exists...")
    replay_page(['智能订单', '查看详情'])
    
    time.sleep(3)
    found = False
    
    # Check '运行中' tab
    ui = get_ui_tree()
    running_center = find_element_center(ui, '"运行中"') or find_element_center(ui, '运行中')
    if running_center:
        logger.info(f"Tapping '运行中' tab at {running_center}")
        device_tap(*running_center, sleep_after=2.0)
        if check_stock_in_orders_list(code):
            found = True
    else:
        logger.warning("Could not find '运行中' tab.")
        
    if not found:
        # Check '今日已触发' tab
        ui = get_ui_tree()
        triggered_center = find_element_center(ui, '"今日已触发"') or find_element_center(ui, '今日已触发') or find_element_center(ui, '今日触发')
        if triggered_center:
            logger.info(f"Tapping '今日已触发' tab at {triggered_center}")
            device_tap(*triggered_center, sleep_after=2.0)
            if check_stock_in_orders_list(code):
                found = True
        else:
            logger.warning("Could not find '今日已触发' tab.")
            
    if found:
        print(f"WARNNING: the stock({code}) order exist, not need do again.")
        logger.warning(f"WARNNING: the stock({code}) order exist, not need do again.")
        sys.exit(0)

    logger.info(f"No existing order for {code} found on either tab. Proceeding.")


def _find_element_context(ui_text: str, center: tuple) -> str | None:
    """Extract the text content of the element at the given center position."""
    x, y = center
    for line in ui_text.split('\n'):
        if 'text=' in line:
            m = re.search(r'text="([^"]*)"', line)
            b = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if m and b:
                x1, y1, x2, y2 = int(b.group(1)), int(b.group(2)), int(b.group(3)), int(b.group(4))
                if x1 <= x <= x2 and y1 <= y <= y2:
                    return m.group(1)
    return None


def _is_exact_text(ctx: str, target: str) -> bool:
    """Check if ctx is exactly the target text (not a substring match)."""
    return ctx.strip() == target.strip()


def set_valid_until_today(ui_text: str) -> None:
    """Set valid_until to 1 trading day: today if <15:00, next trading day if >=15:00.
    
    Opens the date picker, finds the target day in the calendar grid using
    Mobilerun's AndroidStateProvider (exact text match with real coordinates),
    taps it, and confirms with "确定".
    """
    from datetime import datetime
    from backtest.utils.trading_calendar import calendar
    from mobilerun.tools import AndroidDriver
    from mobilerun.tools.ui.provider import AndroidStateProvider
    from mobilerun.tools.filters.concise_filter import ConciseFilter
    from mobilerun.tools.formatters.indexed_formatter import IndexedFormatter
    
    now = datetime.now()
    today_str = now.strftime('%Y%m%d')
    
    if now.hour < 15:
        target_date_str = today_str
    else:
        target_date_str = calendar.get_trading_days_after(today_str, 1)
    
    target_day = int(target_date_str[6:8])
    target_day_str = str(target_day)
    
    logger.info(f"set_valid_until: now={now:%H:%M}, target_date={target_date_str}, "
                f"target_day={target_day_str} (1 trading day)")

    # ── Open the date picker using AndroidStateProvider ──
    picker_opened = False
    try:
        driver = AndroidDriver()
        asyncio.get_event_loop().run_until_complete(driver.connect())
        provider = AndroidStateProvider(driver, ConciseFilter(), IndexedFormatter())
        
        for attempt in range(5):
            ui_state = asyncio.get_event_loop().run_until_complete(provider.get_state())
            for i, elem in enumerate(ui_state.elements):
                text = elem.get("text", "")
                # Find the date value on the form (e.g. "2026-09-29收盘前")
                if text and ("有效期至" in text or "收盘前" in text or text.startswith("2026-")):
                    try:
                        x, y = ui_state.get_element_coords(i)
                        logger.info(f"Found date field '{text[:30]}' at ({x}, {y})")
                        asyncio.get_event_loop().run_until_complete(driver.tap(x, y))
                        time.sleep(2.0)
                        # Verify picker opened
                        ui2 = asyncio.get_event_loop().run_until_complete(provider.get_state())
                        for i2, e2 in enumerate(ui2.elements):
                            if e2.get("text", "") in ("确定", "确认"):
                                logger.info("Date picker opened")
                                picker_opened = True
                                break
                        if picker_opened:
                            break
                    except (ValueError, Exception):
                        continue
            if picker_opened:
                break
            time.sleep(0.5)
    except Exception as e:
        logger.warning(f"AndroidStateProvider picker-open failed: {e}")
    
    if not picker_opened:
        logger.error("Date picker failed to open.")
        return

    # ── Find and tap the target day using AndroidStateProvider ──
    day_tapped = False
    try:
        driver = AndroidDriver()
        asyncio.get_event_loop().run_until_complete(driver.connect())
        provider = AndroidStateProvider(driver, ConciseFilter(), IndexedFormatter())
        
        for attempt in range(5):
            ui_state = asyncio.get_event_loop().run_until_complete(provider.get_state())
            for i, elem in enumerate(ui_state.elements):
                text = elem.get("text", "")
                if text == target_day_str or text == target_day_str.zfill(2):
                    try:
                        x, y = ui_state.get_element_coords(i)
                        logger.info(f"Found day '{target_day_str}' at ({x}, {y})")
                        device_tap(x, y, sleep_after=1.0)
                        day_tapped = True
                        break
                    except ValueError:
                        continue
            if day_tapped:
                break
            time.sleep(0.5)
    except Exception as e:
        logger.warning(f"AndroidStateProvider failed: {e}. Falling back to UI-tree...")
    
    if not day_tapped:
        logger.warning(f"Could not find day '{target_day_str}' on calendar.")

    # ── Confirm with "确定" using AndroidStateProvider ──
    confirm_tapped = False
    try:
        time.sleep(0.5)
        for attempt in range(3):
            ui_state = asyncio.get_event_loop().run_until_complete(provider.get_state())
            for i, elem in enumerate(ui_state.elements):
                text = elem.get("text", "")
                if text in ("确定", "确认"):
                    try:
                        x, y = ui_state.get_element_coords(i)
                        logger.info(f"Found confirm '{text}' at ({x}, {y})")
                        device_tap(x, y, sleep_after=1.5)
                        confirm_tapped = True
                        break
                    except ValueError:
                        continue
            if confirm_tapped:
                break
            time.sleep(0.5)
    except Exception as e:
        logger.warning(f"Confirm via AndroidStateProvider failed: {e}")

    if day_tapped and confirm_tapped:
        logger.info(f"valid_until set to {target_date_str} ({target_day_str}th, 1 trading day) successfully.")
    else:
        logger.warning(f"valid_until may NOT be set correctly (day={'yes' if day_tapped else 'no'}, confirm={'yes' if confirm_tapped else 'no'})")


def set_order_method(ui_text: str) -> None:
    """Tap order method (委托方式) and confirm the default popup."""
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


def tap_create_order(ui_text: str) -> None:
    """Tap the '创建订单' button and handle popups."""
    center = (find_button_center(ui_text, '创建订单') or 
              find_button_center(ui_text, '提交') or 
              find_button_center(ui_text, '买入') or 
              find_button_center(ui_text, '卖出') or
              find_element_center(ui_text, '创建订单') or 
              find_element_center(ui_text, '提交') or 
              find_element_center(ui_text, '买入') or 
              find_element_center(ui_text, '卖出'))
    if not center:
        raise RuntimeError("Cannot find '创建订单' button")

    logger.info(f"Tapping '创建订单' button at {center}")
    device_tap(*center, sleep_after=2)
    
    for popup_idx in range(2):
        popup_ui = get_ui_tree()
        confirm_center = None
        found_label = None
        
        for label in ['确定', '确认', '继续', '同意']:
            confirm_center = find_button_center(popup_ui, label) or find_element_center(popup_ui, label)
            if confirm_center:
                found_label = label
                break
                
        if confirm_center:
            logger.info(f"Found popup confirmation button '{found_label}' at {confirm_center}. Tapping it.")
            device_tap(*confirm_center, sleep_after=2)
        else:
            break


def set_trigger_condition_ge(ui_text: str) -> None:
    """Set the trigger condition to '当股价 >='."""
    center = find_element_center(ui_text, "当股价 ≥")
    if center:
        logger.info(f"Setting trigger condition to '当股价 ≥' at {center}")
        device_tap(*center)
    else:
        logger.warning("Could not find '当股价 ≥' option.")


def fill_trigger_price(price: str, ui_text: str) -> None:
    """Fill in the trigger price field."""
    center = find_edittext_near_label(ui_text, '触发价格')
    if not center:
        raise RuntimeError("Cannot find trigger price EditText. Is a stock selected?")

    logger.info(f"Tapping trigger price field at {center}")
    device_tap(*center, sleep_after=0.5)

    logger.info("Clearing trigger price field via DEL keys")
    adb_clear_field(max_chars=15)

    logger.info(f"Typing trigger price via adb: {price}")
    adb_type(price)


def fill_stock_code(code: str, ui_text: str, verify_page_text: str = None) -> None:
    """Generic helper to enter stock code via selection overlay."""
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

    if verify_page_text:
        post_ui = get_ui_tree()
        if verify_page_text not in post_ui:
            logger.warning(f"May not have returned to page containing '{verify_page_text}' after stock selection")
        else:
            logger.info(f"✅ Stock selected, back on page containing '{verify_page_text}'")


# ---------------------------------------------------------------------------
# Navigation Logic
# ---------------------------------------------------------------------------

def _tap_order_page(page: str) -> None:
    """Tap the button for the requested page (到价买入 / 到价卖出 / 止盈止损)."""
    label = PAGE_LABELS.get(page)
    if label is None:
        raise ValueError(f"Unknown page '{page}'. Valid: {list(PAGE_LABELS)}")

    ui = get_ui_tree()
    center = find_element_center(ui, label)
    if not center:
        # try scrolling to find it
        device_swipe(720, 1500, 720, 500, sleep_after=1.5)
        ui = get_ui_tree()
        center = find_element_center(ui, label)

    if not center:
        raise RuntimeError(f"Cannot find '{label}' button on smart order page.")

    logger.info(f"Tapping '{label}' at {center}")
    device_tap(*center, sleep_after=2)

    ui = get_ui_tree()
    verify_texts = {
        'order_buy':   '到价买入',
        'order_sell':  '到价卖出',
        'order_tp_sl': '止盈止损',
    }
    if verify_texts[page] not in ui:
        logger.warning(f"Could not confirm arrival on '{label}' page. Proceeding anyway.")
    else:
        logger.info(f"✅ Now on '{label}' page.")


def goto_page(page: str = 'order_buy') -> None:
    """Full navigation: open_app → login → homepage → replay_page(['今日触发']) → target page."""
    from trading.guotai import open_app, login, goto_homepage, replay_page
    if page not in PAGE_LABELS:
        raise ValueError(f"Unknown page '{page}'. Valid: {list(PAGE_LABELS)}")

    logger.info(f"[goto_page] Navigating to '{page}' ({PAGE_LABELS[page]})")

    open_app()
    login()
    goto_homepage()
    
    logger.info("Replaying '今日触发' to reach smart order select page...")
    replay_page(['今日触发'])
    time.sleep(2)
    
    _tap_order_page(page)


def get_available_cash_from_homepage() -> float:
    """Read available cash from the '资金资产' field on the homepage (交易 tab main view)."""
    from trading.guotai import open_app, login, goto_homepage
    
    logger.info("Extracting available cash from app homepage...")
    open_app()
    login()
    goto_homepage()
    
    # Check if "资金资产" is visible
    ui = get_ui_tree()
    if "资金资产" not in ui:
        # Tap the "交易" tab on the bottom menu
        # Bottom menu "交易" tab is typically around (720, 2880)
        trading_tab = find_element_center(ui, "交易")
        if trading_tab:
            logger.info(f"Tapping '交易' tab at {trading_tab}")
            device_tap(*trading_tab, sleep_after=2)
        else:
            logger.info("Tapping default '交易' tab coordinates (720, 2880)")
            device_tap(720, 2880, sleep_after=2)
        ui = get_ui_tree()
        
    # Check if there is a clearing warning popup like "当前为我司交易系统清算期间..."
    # If "我知道了" exists, tap it to dismiss and refresh UI tree
    p = find_element_center(ui, "我知道了")
    if p:
        logger.info(f"Dismissing clearing warning popup at {p}")
        device_tap(*p, sleep_after=1.5)
        ui = get_ui_tree()

    # Parse available cash
    # The TextView tv_available contains the cash amount
    lines = ui.split('\n')
    cash_value = None
    for i, line in enumerate(lines):
        if "资金资产" in line:
            # Look at the next few lines for tv_available or numbers
            for j in range(i + 1, min(i + 10, len(lines))):
                next_line = lines[j]
                # Look for a number pattern (like 365277.14)
                match = re.search(r'"(\d+\.\d+)"', next_line)
                if match:
                    cash_value = float(match.group(1))
                    break
            if cash_value is not None:
                break
                
    if cash_value is None:
        # Fallback regex search for tv_available in the entire UI dump
        for line in lines:
            if "tv_available" in line:
                match = re.search(r'text - "(\d+\.\d+)"', line) or re.search(r'"(\d+\.\d+)"', line)
                if match:
                    cash_value = float(match.group(1))
                    break
                    
    if cash_value is None:
        raise RuntimeError("Failed to extract available cash ('资金资产') from app homepage.")
        
    logger.info(f"Successfully extracted cash from app homepage: ¥{cash_value:,.2f}")
    return cash_value


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Consolidated UI tools and page navigation CLI.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Pages:
  order_buy   - 到价买入 (default)
  order_sell  - 到价卖出
  order_tp_sl - 止盈止损

Examples:
  python -m utils.tools --page order_sell
  python -m utils.tools --page order_tp_sl
        """
    )
    parser.add_argument(
        '--page',
        type=str,
        choices=list(PAGE_LABELS),
        help='Navigate to target page',
    )
    args = parser.parse_args()
    if args.page:
        goto_page(args.page)


if __name__ == '__main__':
    main()
