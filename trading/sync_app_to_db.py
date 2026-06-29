import os
import sys
import asyncio
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from loguru import logger

# Add the parent directory to Python path so we can import from shared/utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mobilerun import MobileConfig, AndroidDriver
from llama_index.llms.google_genai import GoogleGenAI
from shared.db.db import DB
from backtest.utils.trading_calendar import calendar
from utils.trading_time import get_market_open_times_refresh_interval

# Import functions and requirements from trading.guotai
from trading.guotai import (
    pre_requirements,
    goto_homepage,
    replay_page,
    parse_csv_data,
    parse_number,
    parse_percentage,
    normalize_stock_name,
    clean_stock_name,
    extract_stock_code,
    sync_index_quote_data_to_db,
    sync_summary_position_data_to_db,
    sync_order_data_to_db,
    login
)

from utils.tools import (
    get_ui_tree,
    device_tap,
    device_swipe,
    find_element_center
)


# ==========================================
# 1. Pure ADB UI Parsers (No AI agent)
# ==========================================

def extract_stock_code_and_name(raw_name: str) -> Tuple[str, str]:
    """
    Extract stock code and clean name.
    Handles:
      - 6 digits at start: "000670盈方微"
      - 6 digits in parentheses: "盈方微(000670)"
      - 6 digits without parentheses: "盈方微000670"
    """
    n_name = normalize_stock_name(raw_name).strip()
    
    # 1. Check pattern: 6 digits at the beginning, e.g. "000670盈方微"
    m1 = re.match(r'^(\d{6})\s*(.+)$', n_name)
    if m1:
        return m1.group(1), m1.group(2).strip()
        
    # 2. Check pattern: name followed by 6 digits in parentheses, e.g. "盈方微(000670)"
    m2 = re.match(r'^(.+?)\((\d{6})\)$', n_name)
    if m2:
        return m2.group(2), m2.group(1).strip()
        
    # 3. Check pattern: name followed by 6 digits without parentheses, e.g. "盈方微000670"
    m3 = re.match(r'^(.+?)\s*(\d{6})$', n_name)
    if m3:
        return m3.group(2), m3.group(1).strip()
        
    # Fall back to guotai's extract_stock_code
    name_clean, ext_code = extract_stock_code(n_name)
    if ext_code:
        return ext_code, name_clean
        
    return "", n_name

async def get_index_stock_from_app_quote_page_structured(
    config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver
) -> str:
    """
    Get real-time index and stock data from mobile guotai app quote page.
    """
    logger.info("Navigating to quote page...")
    goto_homepage()
    ui = get_ui_tree()
    # Tap "行情" bottom tab
    hangqing_tab = find_element_center(ui, "btm_text2") or find_element_center(ui, "bottom_menu_button2") or (432, 2878)
    device_tap(*hangqing_tab, sleep_after=2)
    
    ui = get_ui_tree()
    # Tap "我的持仓" tab
    holding_tab = find_element_center(ui, "我的持仓")
    if holding_tab:
        logger.info(f"Tapping '我的持仓' tab at {holding_tab}")
        device_tap(*holding_tab, sleep_after=3)
    else:
        logger.warning("Could not find '我的持仓' tab programmatically. Replaying trajectory...")
        replay_page(['行情', '我的持仓'])
        time.sleep(3)
        
    # Tap "同步" button to sync/refresh holdings
    ui = get_ui_tree()
    sync_btn = find_element_center(ui, "同步") or find_element_center(ui, "tv_holdSync")
    if sync_btn:
        logger.info(f"Tapping '同步' button at {sync_btn}")
        device_tap(*sync_btn, sleep_after=4)
    else:
        logger.warning("Could not find '同步' button on holding page.")
    
    indices = []  # list of (name, value, ratio)
    stocks = {}   # dict of code -> (name, price, ratio, change_amount)
    
    last_ui = ""
    for scroll_idx in range(12):  # Scroll max 12 times to load all stocks
        ui = get_ui_tree()
        
        # Check if we should stop scrolling (only on iterations > 0)
        if scroll_idx > 0:
            current_screen_codes = set()
            for line in ui.split('\n'):
                if 'tv_code' in line:
                    match = re.search(r'tv_code", "(\d{6})"', line)
                    if match:
                        current_screen_codes.add(match.group(1))
            if current_screen_codes and current_screen_codes.issubset(set(stocks.keys())):
                logger.info("Scroll reached bottom of stock list (no new stock codes visible).")
                break
                
        # Parse indices only on the first screen
        if not indices:
            prices = []
            zdfs = []
            for line in ui.split('\n'):
                if 'tv_price' in line:
                    match = re.search(r'text - "([^"]+)"|tv_price", "([^"]+)"', line)
                    txt = match.group(1) or match.group(2) if match else None
                    m_bounds = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                    if txt and m_bounds:
                        cx = (int(m_bounds.group(1)) + int(m_bounds.group(3))) // 2
                        cy = (int(m_bounds.group(2)) + int(m_bounds.group(4))) // 2
                        if cy < 400:
                            prices.append((cx, txt))
                elif 'tv_zdf' in line:
                    match = re.search(r'text - "([^"]+)"|tv_zdf", "([^"]+)"', line)
                    txt = match.group(1) or match.group(2) if match else None
                    m_bounds = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                    if txt and m_bounds:
                        cx = (int(m_bounds.group(1)) + int(m_bounds.group(3))) // 2
                        cy = (int(m_bounds.group(2)) + int(m_bounds.group(4))) // 2
                        if cy < 400:
                            zdfs.append((cx, txt))
            
            prices.sort()
            zdfs.sort()
            for (cx1, p_val), (cx2, z_val) in zip(prices, zdfs):
                idx_match = re.search(r'([^\s\d.+-]+)\s*([+\-]?\d+\.?\d*%)', z_val)
                if idx_match:
                    idx_name = idx_match.group(1)
                    idx_ratio = idx_match.group(2)
                    indices.append((idx_name, p_val, idx_ratio))
            logger.info(f"Extracted indices: {indices}")

        # Parse stock quotes
        rows = []
        for line in ui.split('\n'):
            if 'item_main_content' in line:
                match = re.search(r'item_main_content", "([^，]+)，最新价([\d.]+)元，涨幅([+\-]?[\d.]+%?)" - \((\d+),(\d+),(\d+),(\d+)\)', line)
                if match:
                    name, price, ratio, x1, y1, x2, y2 = match.groups()
                    rows.append((int(y1), int(y2), name, float(price), ratio))
        
        # Find codes and change amounts for each row by mapping Y coordinates
        for y1, y2, name, price, ratio in rows:
            row_code = None
            row_change = 0.0
            rightmost_x = -1
            for line in ui.split('\n'):
                m_bounds = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                if m_bounds:
                    cy = (int(m_bounds.group(2)) + int(m_bounds.group(4))) // 2
                    if y1 <= cy <= y2:
                        if 'tv_code' in line:
                            match = re.search(r'tv_code", "(\d{6})"', line)
                            if match:
                                row_code = match.group(1)
                        # Find change amount (TextView float)
                        match_txt = re.search(r'TextView: "[^"]*", "([+\-]?\d+\.\d+)"|TextView: "([+\-]?\d+\.\d+)"', line)
                        if match_txt:
                            txt = match_txt.group(1) or match_txt.group(2)
                            cx = (int(m_bounds.group(1)) + int(m_bounds.group(3))) // 2
                            if cx > rightmost_x:
                                rightmost_x = cx
                                row_change = float(txt)
            
            if row_code:
                stocks[row_code] = (name, price, ratio, row_change)
        
        # Scroll logic
        footer_visible = False
        for line in ui.split('\n'):
            if 'stocklist_footer' in line or '添加自选股' in line:
                m_bounds = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                if m_bounds:
                    y1, y2 = int(m_bounds.group(2)), int(m_bounds.group(4))
                    cy = (y1 + y2) // 2
                    if cy < 2780:
                        footer_visible = True
                        break
        if footer_visible:
            logger.info("Reached bottom of stock list (footer visible on screen).")
            break
        
        logger.info("Scrolling down stock list...")
        device_swipe(720, 2000, 720, 500, sleep_after=1.5)
        
    # Serialize to CSV
    lines1 = ["index_name,index_number,index_ratio"]
    for idx in indices:
        lines1.append(f"{idx[0]},{idx[1]},{idx[2]}")
        
    lines2 = ["name,code,latest_price,increase_percentage,increase_amount"]
    for code, (name, price, ratio, change) in stocks.items():
        lines2.append(f"{name},{code},{price},{ratio},{change}")
        
    return "\n".join(lines1) + "\n\n" + "\n".join(lines2)


async def get_summary_position_from_app_position_page_structured(
    config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver
) -> str:
    """
    Get real-time summary and position data from mobile guotai app position page.
    """
    logger.info("Navigating to position page...")
    goto_homepage()
    ui = get_ui_tree()
    trading_tab_center = find_element_center(ui, "btm_text3") or find_element_center(ui, "bottom_menu_button3") or (720, 2880)
    device_tap(*trading_tab_center, sleep_after=3)
    
    ui = get_ui_tree()
    p = find_element_center(ui, "我知道了")
    if p:
        device_tap(*p, sleep_after=1.5)
        ui = get_ui_tree()
        
    holding_tab = find_element_center(ui, "tv_holding") or find_element_center(ui, "持仓")
    if holding_tab:
        logger.info(f"Tapping '持仓' tab at {holding_tab}")
        device_tap(*holding_tab, sleep_after=3)
    else:
        logger.warning("Could not find '持仓' tab programmatically. Replaying trajectory...")
        replay_page(['交易', '持仓'])
        time.sleep(3)
    
    summary = {
        'floating_pnl': 0.0,
        'account_assets': 0.0,
        'market_cap': 0.0,
        'position_percent': '0.00%',
        'available': 0.0,
        'withdrawable': 0.0
    }
    positions = {}  # dict of name -> (market_cap, open, available, current_price, cost, floating_profit, floating_loss_percentage)
    
    for scroll_idx in range(12):  # Scroll max 12 times to load all positions
        ui = get_ui_tree()
        
        # Check if we should stop scrolling (only on iterations > 0)
        if scroll_idx > 0:
            current_screen_names = set()
            for line in ui.split('\n'):
                if 'item_main_content' in line:
                    match = re.search(r'item_main_content", "([^市]+)市值', line)
                    if match:
                        current_screen_names.add(match.group(1))
            if current_screen_names and current_screen_names.issubset(set(positions.keys())):
                logger.info("Scroll reached bottom of position list (no new stock positions visible).")
                break
        
        # Parse summary on the first screen
        if summary['account_assets'] == 0.0:
            for line in ui.split('\n'):
                if 'tv_profile_loss_value' in line:
                    match = re.search(r'tv_profile_loss_value", "([^"]+)"', line)
                    if match: summary['floating_pnl'] = float(match.group(1))
                elif 'tv_total_assert_value' in line:
                    match = re.search(r'tv_total_assert_value", "([^"]+)"', line)
                    if match: summary['account_assets'] = float(match.group(1))
                elif 'tv_all_value' in line:
                    match = re.search(r'tv_all_value", "([^"]+)"', line)
                    if match: summary['market_cap'] = float(match.group(1))
                elif 'tv_current_position' in line:
                    match = re.search(r'tv_current_position", "([^"]+)"', line)
                    if match: summary['position_percent'] = match.group(1)
                elif 'tv_available' in line:
                    match = re.search(r'tv_available", "([^"]+)"', line)
                    if match: summary['available'] = float(match.group(1))
                elif 'tv_desirable' in line or 'tv_withdraw_value' in line:
                    match = re.search(r'tv_desirable", "([^"]+)"|tv_withdraw_value", "([^"]+)"', line)
                    val = match.group(1) or match.group(2) if match else None
                    if val: summary['withdrawable'] = float(val)
            logger.info(f"Extracted summary: {summary}")
        
        # Parse positions using the detailed content description regex
        for line in ui.split('\n'):
            if 'item_main_content' in line:
                match = re.search(r'item_main_content", "([^市]+)市值([\d.+-]+)元持仓(\d+)可用(\d+)现价([\d.+-]+)元成本([\d.+-]+)元浮动盈亏([\d.+-]+)元浮动盈亏比例([\d.+-]+%)', line)
                if match:
                    name, market_cap, holdings, available, price, cost, pnl, pnl_percent = match.groups()
                    positions[name] = (float(market_cap), int(holdings), int(available), float(price), float(cost), float(pnl), pnl_percent)
        
        logger.info("Scrolling down position list...")
        device_swipe(720, 2000, 720, 500, sleep_after=1.5)
        
    # Serialize to CSV
    lines1 = ["floating_profit_loss,account_assets,market_cap,positions,available,desirable"]
    lines1.append(f"{summary['floating_pnl']},{summary['account_assets']},{summary['market_cap']},{summary['position_percent']},{summary['available']},{summary['withdrawable']}")
    
    lines2 = ["name,market_cap,open,available,current_price,cost,floating_profit,floating_loss_percentage"]
    for name, val in positions.items():
        lines2.append(f"{name},{val[0]},{val[1]},{val[2]},{val[3]},{val[4]},{val[5]},{val[6]}")
        
    return "\n".join(lines1) + "\n\n" + "\n".join(lines2)




async def get_order_from_app_smart_order_page_structured(
    config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver, target_tabs: list = None
) -> str:
    """
    Get real-time smart order data from mobile guotai app smart order page.
    """
    logger.info("Navigating to smart order page...")
    goto_homepage()
    ui = get_ui_tree()
    trading_tab_center = find_element_center(ui, "btm_text3") or find_element_center(ui, "bottom_menu_button3") or (720, 2880)
    device_tap(*trading_tab_center, sleep_after=3)
    
    ui = get_ui_tree()
    p = find_element_center(ui, "我知道了")
    if p:
        device_tap(*p, sleep_after=1.5)
        ui = get_ui_tree()
        
    center = find_element_center(ui, "运行中") or find_element_center(ui, "今日触发")
    if not center:
        # Fall back to replay page if direct tap fails
        logger.warning("Could not navigate directly. Replaying macro trajectory...")
        replay_page(['智能订单', '查看详情'])
    else:
        device_tap(*center, sleep_after=3)
        ui = get_ui_tree()
        center_details = find_element_center(ui, "查看详情")
        if center_details:
            device_tap(*center_details, sleep_after=3)
            
    orders = {}  # dict of order_number -> dict of order details
    current_year = datetime.now().year

    async def scrape_tab(tab_name: str):
        logger.info(f"Tapping tab '{tab_name}'...")
        ui = get_ui_tree()
        tab_btn = find_element_center(ui, tab_name)
        if tab_btn:
            device_tap(*tab_btn, sleep_after=3)
        else:
            logger.warning(f"Could not find tab '{tab_name}' programmatically.")
            return

        tab_orders = set()  # Track orders seen in this tab session
        stop_tab_scrolling = False
        empty_scrolls = 0
        for scroll_idx in range(40):  # Scroll max 40 times to load all orders for this tab
            if stop_tab_scrolling:
                break
            ui = get_ui_tree()
            lines = ui.split('\n')
            
            # Check if we should stop scrolling based on visible orders
            current_screen_orders = set()
            for k, line in enumerate(lines):
                if '订单编号' in line and k + 1 < len(lines):
                    on_match = re.search(r'View: "([^"]+)"', lines[k+1])
                    if on_match:
                        current_screen_orders.add(on_match.group(1).strip())
            
            if not current_screen_orders:
                logger.info(f"No smart orders visible on screen for tab '{tab_name}'.")
                break
                
            if scroll_idx > 0 and current_screen_orders.issubset(tab_orders):
                empty_scrolls += 1
                if empty_scrolls >= 2:
                    logger.info(f"Scroll reached bottom of tab '{tab_name}' (no new orders visible in this tab).")
                    break
            else:
                empty_scrolls = 0
            
            i = 0
            while i < len(lines):
                line = lines[i]
                if 'TextView' in line and ('到价' in line or '止盈' in line or '止损' in line):
                    name = None
                    code = None
                    trigger_condition = None
                    buy_or_sell_price_type = None
                    buy_or_sell_quantity = None
                    valid_until = None
                    order_number = None
                    reason_of_ending = None
                    
                    # Scan up to 40 lines inside this card block
                    card_lines = lines[i:min(i + 40, len(lines))]
                    for j, cline in enumerate(card_lines):
                        if not code:
                            code_match = re.search(r'View: "(\d{6})"', cline)
                            if code_match:
                                code = code_match.group(1)
                                for k in range(j - 1, -1, -1):
                                    prev_line = card_lines[k]
                                    if 'View: "' in prev_line and '到价' not in prev_line and '止盈' not in prev_line and '止损' not in prev_line:
                                        name_match = re.search(r'View: "([^"]+)"', prev_line)
                                        if name_match and not name_match.group(1).isdigit():
                                            name = name_match.group(1)
                                            break
                                            
                        if '触发条件' in cline and j + 1 < len(card_lines):
                            tc_match = re.search(r'View: "([^"]+)"', card_lines[j+1])
                            if tc_match: trigger_condition = tc_match.group(1).replace('\n', ' ')
                            
                        if ('价格' in cline) and j + 1 < len(card_lines):
                            pt_match = re.search(r'View: "([^"]+)"', card_lines[j+1])
                            if pt_match: buy_or_sell_price_type = pt_match.group(1)
                            
                        if ('数量' in cline) and j + 1 < len(card_lines):
                            qty_match = re.search(r'View: "([^"]+)"', card_lines[j+1])
                            if qty_match:
                                qty_str = qty_match.group(1)
                                qty_num = re.search(r'(\d+)', qty_str)
                                if qty_num: buy_or_sell_quantity = float(qty_num.group(1))
                                
                        if '有效期至' in cline and j + 1 < len(card_lines):
                            vu_match = re.search(r'View: "([^"]+)"', card_lines[j+1])
                            if vu_match: valid_until = vu_match.group(1)
                            
                        if '订单编号' in cline and j + 1 < len(card_lines):
                            on_match = re.search(r'View: "([^"]+)"', card_lines[j+1])
                            if on_match: order_number = on_match.group(1)
                            
                        if '结束原因' in cline:
                            re_match = re.search(r'TextView: "结束原因：([^"]+)"', cline) or re.search(r'结束原因：([^"]+)', cline)
                            if re_match: reason_of_ending = re_match.group(1)
                    
                    if order_number:
                        skip_order = False
                        if valid_until:
                            m_year = re.search(r'(\d{4})', valid_until)
                            if m_year:
                                order_year = int(m_year.group(1))
                                if order_year < current_year:
                                    logger.info(f"Skipping order {order_number} with validity before current year: {valid_until}")
                                    skip_order = True
                                    if tab_name == "已结束":
                                        logger.info("Stopping scroll for '已结束' tab.")
                                        stop_tab_scrolling = True
                                        
                        if not skip_order:
                            orders[order_number] = {
                                'name': name or "未知",
                                'code': code or "000000",
                                'trigger_condition': trigger_condition or "",
                                'buy_or_sell_price_type': buy_or_sell_price_type or "",
                                'buy_or_sell_quantity': buy_or_sell_quantity or 0.0,
                                'valid_until': valid_until or "",
                                'order_number': order_number,
                                'reason_of_ending': reason_of_ending or "",
                                'status': tab_name
                            }
                        tab_orders.add(order_number)
                        i += j  # Skip parsed block
                i += 1
                
            if "全部加载完成" in ui or "没有更多" in ui:
                logger.info(f"Reached bottom of smart orders list on tab '{tab_name}'.")
                break
                
            logger.info(f"Scrolling down smart orders list on tab '{tab_name}'...")
            device_swipe(720, 2000, 720, 500, sleep_after=1.5)

    # Scrape target tabs
    if target_tabs is None:
        target_tabs = ["今日已触发", "运行中", "已结束"]
    
    for tab in target_tabs:
        await scrape_tab(tab)

    # Serialize to CSV
    lines = ["name,code,trigger_condition,buy_or_sell_price_type,buy_or_sell_quantity,valid_until,order_number,reason_of_ending,status"]
    for o in orders.values():
        lines.append(f"{o['name']},{o['code']},{o['trigger_condition']},{o['buy_or_sell_price_type']},{o['buy_or_sell_quantity']},{o['valid_until']},{o['order_number']},{o['reason_of_ending']},{o['status']}")
    return "\n".join(lines)


async def get_transactions_from_app_history_page_structured(
    config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver,
    stop_before_date: str | None = None
) -> str:
    """
    Get transaction history from mobile guotai app history page.
    
    Args:
        stop_before_date: If set (format 'YYYY-MM-DD'), stop scrolling when
                          transactions before this date are found.
    """
    logger.info("Navigating to history transactions page...")
    goto_homepage()
    ui = get_ui_tree()
    trading_tab_center = find_element_center(ui, "btm_text3") or find_element_center(ui, "bottom_menu_button3") or (720, 2880)
    device_tap(*trading_tab_center, sleep_after=3)
    
    ui = get_ui_tree()
    p = find_element_center(ui, "我知道了")
    if p:
        device_tap(*p, sleep_after=1.5)
        ui = get_ui_tree()
        
    center_history = find_element_center(ui, "历史成交")
    if not center_history:
        # Fall back to scrolling or typical coordinate
        device_swipe(720, 2000, 720, 500, sleep_after=2)
        ui = get_ui_tree()
        center_history = find_element_center(ui, "历史成交") or (180, 1317)
        
    device_tap(*center_history, sleep_after=3)
    
    # Ensure we are in the "Month" view so we can scroll through the whole year
    ui = get_ui_tree()
    thirty_days = find_element_center(ui, "近30天")
    if thirty_days:
        # Check if it's already in month view (e.g. shows "2026年06月")
        already_month_view = False
        for line in ui.split('\n'):
            if '年' in line and '月' in line:
                m_bounds = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                if m_bounds:
                    cy = (int(m_bounds.group(2)) + int(m_bounds.group(4))) // 2
                    if 290 <= cy <= 410:  # Same height as tabs
                        already_month_view = True
                        break
        
        if not already_month_view:
            logger.info("Switching to month view to load full year transactions...")
            device_tap(thirty_days[0] + 450, thirty_days[1], sleep_after=3)
    
    transactions = {}  # key -> tuple
    last_ui = ""
    stop_scrolling = False
    
    for scroll_idx in range(40):  # Scroll max 40 times
        if stop_scrolling:
            break
        ui = get_ui_tree()
        initial_count = len(transactions)
        
        rows = []
        for line in ui.split('\n'):
            match = re.search(r'View: "android\.view\.View" - \(0,(\d+),480,(\d+)\)', line)
            if match:
                y1, y2 = int(match.group(1)), int(match.group(2))
                if y1 > 200:  # Skip the top filter tabs
                    rows.append((y1, y2))
                    
        for y1, y2 in rows:
            name = ""
            time_str = ""
            price = 0.0
            quantity = 0
            tx_type = ""
            amount = 0.0
            
            for line in ui.split('\n'):
                m_bounds = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
                if m_bounds:
                    cx = (int(m_bounds.group(1)) + int(m_bounds.group(3))) // 2
                    cy = (int(m_bounds.group(2)) + int(m_bounds.group(4))) // 2
                    if y1 <= cy <= y2:
                        match_txt = re.search(r'View: "([^"]+)"|TextView: "[^"]*", "([^"]+)"|TextView: "([^"]+)"', line)
                        if match_txt:
                            text = (match_txt.group(1) or match_txt.group(2) or match_txt.group(3)).strip()
                            if not text or text == "android.view.View":
                                continue
                                
                            if cx < 480:
                                if re.match(r'\d{2}/\d{2} \d{2}:\d{2}:\d{2}', text):
                                    time_str = text
                                else:
                                    name = text
                            elif 480 <= cx < 800:
                                price = parse_number(text)
                            elif 800 <= cx < 1040:
                                quantity = int(parse_number(text))
                            elif 1040 <= cx < 1440:
                                if text in ["证券买入", "证券卖出", "买入", "卖出"]:
                                    tx_type = text
                                else:
                                    amount = parse_number(text)
                                    
            if name and time_str:
                current_year = datetime.now().year
                if len(time_str) == 14:  # "06/23 10:35:56"
                    tx_date = f"{current_year}-{time_str[0:2]}-{time_str[3:5]} {time_str[6:]}"
                else:
                    tx_date = time_str
                    
                # Check if it belongs to current year (e.g. 2026) and >= 2026-01-01
                year_start_prefix = f"{current_year}-01-01 00:00:00"
                if tx_date < year_start_prefix:
                    logger.info(f"Reached transaction before current year start: {tx_date}. Discarding and stopping.")
                    stop_scrolling = True
                    break

                # Check if transaction is before the stop_before_date cutoff
                if stop_before_date:
                    tx_date_prefix = tx_date[:10]  # "YYYY-MM-DD"
                    if tx_date_prefix < stop_before_date:
                        logger.info(f"Reached transaction before cutoff {stop_before_date}: {tx_date}. Stopping.")
                        stop_scrolling = True
                        break
                    
                key = f"{tx_date}_{name}_{tx_type}_{price}_{quantity}"
                transactions[key] = (tx_date, name, tx_type, price, quantity, amount)
                
        if stop_scrolling:
            break
            
        if "没有更多" in ui or "暂无数据" in ui:
            logger.info("Reached bottom of transaction history list.")
            break
            
        if scroll_idx > 0 and len(transactions) == initial_count:
            logger.info("Scroll reached bottom of transaction history list (no new transactions found).")
            break
        
        logger.info("Scrolling down history transactions list...")
        device_swipe(720, 1800, 720, 800, sleep_after=1.5)
        
    # Serialize to CSV
    lines = ["成交时间,名称,买卖类型,成交价,成交量,成交金额"]
    for val in transactions.values():
        lines.append(f"{val[0]},{val[1]},{val[2]},{val[3]},{val[4]},{val[5]}")
    return "\n".join(lines)


# ==========================================
# 2. Database Helpers & Logic
# ==========================================

_stock_name_to_code_map = {}

def init_stock_index_map():
    global _stock_name_to_code_map
    if _stock_name_to_code_map:
        return
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    json_path = os.path.join(base_dir, "utils", "daily_stock_analysis", "apps", "dsa-web", "public", "stocks.index.json")
    if os.path.exists(json_path):
        try:
            import json
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for item in data:
                    if len(item) >= 7 and item[6] == 'CN':
                        code = item[1]
                        name = normalize_stock_name(item[2])
                        _stock_name_to_code_map[name] = code
            logger.info(f"Loaded {len(_stock_name_to_code_map)} stock mappings from stocks.index.json")
        except Exception as e:
            logger.error(f"Failed to parse stocks.index.json: {e}")

def get_stock_code_by_name(name: str, user_id: int = 1) -> str:
    """Query database or json index to find stock code for a given stock name."""
    init_stock_index_map()
    
    # 1. Exact match in index map
    if name in _stock_name_to_code_map:
        return _stock_name_to_code_map[name]
        
    # 2. Cleaned name match in index map
    cleaned_name = clean_stock_name(name)
    if cleaned_name in _stock_name_to_code_map:
        return _stock_name_to_code_map[cleaned_name]
        
    with DB.cursor() as cursor:
        # 3. Search in current holdings (exact and cleaned)
        for n in [name, cleaned_name]:
            row = cursor.execute("SELECT code FROM holding_stocks WHERE user_id = ? AND name = ?", (user_id, n)).fetchone()
            if row:
                return row[0]
                
        # 4. Search in smart orders (exact and cleaned)
        for n in [name, cleaned_name]:
            row = cursor.execute("SELECT code FROM smart_orders WHERE user_id = ? AND name = ?", (user_id, n)).fetchone()
            if row:
                return row[0]
                
        # 5. Search in existing transactions (exact and cleaned)
        for n in [name, cleaned_name]:
            row = cursor.execute("SELECT code FROM transactions WHERE user_id = ? AND name = ?", (user_id, n)).fetchone()
            if row and row[0] != "000000":
                return row[0]
                
        # 6. Check all keys in the index map for a cleaned match
        for k, v in _stock_name_to_code_map.items():
            if clean_stock_name(k) == cleaned_name:
                return v
                
    return ""


def sync_transactions_to_db(transactions_data: str, user_id: int = 1, cutoff_date: str | None = None) -> dict:
    """
    Sync transaction history from mobile app to database.
    
    Args:
        transactions_data: CSV data from app
        user_id: User ID
        cutoff_date: If set (format 'YYYY-MM-DD'), only sync transactions on/after this date.
                     Older records are kept (not deleted). If None, uses global _sync_cutoff_date.
    """
    global _sync_cutoff_date
    if cutoff_date is None:
        cutoff_date = _sync_cutoff_date  # may still be None
    
    if not transactions_data:
        return {'success': True, 'message': 'No transaction data to sync', 'added': 0}
        
    header, transaction_rows = parse_csv_data(transactions_data)
    added_count = 0
    valid_tx_ids = set()
    current_year = datetime.now().year
    year_start = f"{current_year}-01-01 00:00:00"
    
    # Determine deletion boundary: cutoff_date or year_start
    delete_boundary = cutoff_date + " 00:00:00" if cutoff_date else year_start
    
    with DB.cursor() as cursor:
        for row in transaction_rows:
            if len(row) < 6:
                continue
            tx_date = row[0].strip()
            raw_name = row[1]
            tx_type = row[2].strip()
            price = parse_number(row[3])
            quantity = int(parse_number(row[4]))
            amount = parse_number(row[5])
            
            # Normalize type: map '证券买入', '买入', 'buy' to 'buy'
            if tx_type in ['证券买入', '买入', 'buy']:
                norm_type = 'buy'
            elif tx_type in ['证券卖出', '卖出', 'sell']:
                norm_type = 'sell'
            else:
                norm_type = tx_type.lower()
                
            # Extract code and clean name
            code, name = extract_stock_code_and_name(raw_name)
            if not code or code == "000000":
                code = get_stock_code_by_name(name, user_id)
                if not code:
                    code = "000000"  # fallback if still unknown
            
            # Update older records with '000000' in database tables if a valid code is resolved
            if code and code != "000000":
                cleaned_target = clean_stock_name(name)
                # 1. Transactions
                rows = cursor.execute("SELECT id, name FROM transactions WHERE user_id = ? AND code = '000000'", (user_id,)).fetchall()
                for r_id, r_name in rows:
                    if r_name == name or clean_stock_name(r_name) == cleaned_target:
                        cursor.execute("UPDATE transactions SET code = ? WHERE id = ?", (code, r_id))
                # 2. Holdings
                rows = cursor.execute("SELECT name FROM holding_stocks WHERE user_id = ? AND code = '000000'", (user_id,)).fetchall()
                for (r_name,) in rows:
                    if r_name == name or clean_stock_name(r_name) == cleaned_target:
                        cursor.execute("UPDATE holding_stocks SET code = ? WHERE user_id = ? AND name = ?", (code, user_id, r_name))
                # 3. Smart orders
                rows = cursor.execute("SELECT order_number, name FROM smart_orders WHERE user_id = ? AND code = '000000'", (user_id,)).fetchall()
                for r_ord_num, r_name in rows:
                    if r_name == name or clean_stock_name(r_name) == cleaned_target:
                        cursor.execute("UPDATE smart_orders SET code = ? WHERE order_number = ?", (code, r_ord_num))
                    
            # Check if this exact transaction already exists to avoid duplication
            # Prioritize matching by code if code is valid (not '000000'), otherwise match by name
            tx_row = None
            if code and code != "000000":
                tx_row = cursor.execute("""
                    SELECT id FROM transactions
                    WHERE user_id = ? AND code = ? AND transaction_type = ? 
                      AND transaction_date = ? AND price = ? AND quantity = ? AND amount = ?
                """, (user_id, code, norm_type, tx_date, price, quantity, amount)).fetchone()
            else:
                cleaned_target = clean_stock_name(name)
                candidates = cursor.execute("""
                    SELECT id, name FROM transactions
                    WHERE user_id = ? AND transaction_type = ? 
                      AND transaction_date = ? AND price = ? AND quantity = ? AND amount = ?
                """, (user_id, norm_type, tx_date, price, quantity, amount)).fetchall()
                for r_id, r_name in candidates:
                    if r_name == name or clean_stock_name(r_name) == cleaned_target:
                        tx_row = (r_id,)
                        break
            
            if tx_row is None:
                cursor.execute("""
                    INSERT INTO transactions (user_id, code, name, transaction_type, transaction_date, price, quantity, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (user_id, code, name, norm_type, tx_date, price, quantity, amount))
                new_id = cursor.lastrowid
                valid_tx_ids.add(new_id)
                added_count += 1
            else:
                valid_tx_ids.add(tx_row[0])
                # Update the name in case the name prefix has changed (e.g. ST changes)
                cursor.execute("""
                    UPDATE transactions
                    SET name = ?
                    WHERE id = ?
                """, (name, tx_row[0]))
                
        # Delete orphaned transactions from the cutoff date onwards
        if valid_tx_ids:
            placeholders = ','.join(['?'] * len(valid_tx_ids))
            cursor.execute(f"""
                DELETE FROM transactions 
                WHERE user_id = ? AND transaction_date >= ? AND id NOT IN ({placeholders})
            """, [user_id, delete_boundary] + list(valid_tx_ids))
            deleted_count = cursor.rowcount
        else:
            cursor.execute("""
                DELETE FROM transactions 
                WHERE user_id = ? AND transaction_date >= ?
            """, (user_id, delete_boundary))
            deleted_count = cursor.rowcount
                
    return {
        'success': True,
        'message': f"Synced transactions: {added_count} added, {deleted_count} orphaned removed",
        'added': added_count
    }


# ==========================================
# 3. App vs DB Check (self-contained — fetches from app directly)
# ==========================================

# Incremental sync cutoff: only sync data on or after this date.
# Default reads from .env START_REAL_TRADING_DATE (2026-06-29).
# Can be overridden by set_sync_cutoff_date() (e.g. for pre-start init).
_sync_cutoff_date: str | None = os.getenv('START_REAL_TRADING_DATE')


def set_sync_cutoff_date(date_str: str | None):
    """Override the cutoff date for incremental sync. Only data >= this date is synced.
    Set to None to sync all data. Format: 'YYYY-MM-DD'.
    
    Call without arguments to reset to the .env default.
    """
    global _sync_cutoff_date
    if date_str is None:
        _sync_cutoff_date = os.getenv('START_REAL_TRADING_DATE')
    else:
        _sync_cutoff_date = date_str
    if _sync_cutoff_date:
        logger.info(f"Incremental sync cutoff set to: {_sync_cutoff_date}")
    else:
        logger.info("Incremental sync cutoff cleared (full sync).")


async def check_app_vs_db(user_id: int = 1) -> dict:
    """Fetch current state from the broker app and compare against DB.
    
    Fetches positions (cash, holdings count), running orders count,
    and transaction count from the app, then compares each against
    the corresponding DB table.
    
    Returns:
        dict with:
          - db_matches_app: bool — True if all tables match
          - mismatches: list[str] — human-readable descriptions of each mismatch
          - db_state: dict — DB counts (cash, holdings, running_orders, tx_count)
          - app_state: dict — App counts (positions, running_orders, tx_count)
          - app_cash: float | None — available cash from app (¥)
          - app_positions: list[dict] — position records from app
          - app_running_orders: list[dict] — running order records from app
    """
    from shared.db.db import DB
    from trading.guotai import pre_requirements as _pre_req, parse_csv_data

    result = {
        'db_matches_app': True,
        'mismatches': [],
        'db_state': {},
        'app_state': {},
        'app_cash': None,
        'app_positions': [],
        'app_running_orders': [],
    }

    # ── Fetch app state ──
    tools, llm, config = await _pre_req()
    app_positions_count = 0
    app_running_orders_count = 0
    app_tx_count = 0

    # 1. Positions → summary_account.cash + holding_stocks count
    try:
        pos_csv = await get_summary_position_from_app_position_page_structured(config, llm, tools)
        if pos_csv:
            sections = pos_csv.strip().split('\n\n')
            if sections:
                header, summary_rows = parse_csv_data(sections[0])
                if summary_rows:
                    result['app_cash'] = float(summary_rows[0][4])
                    result['app_state']['cash'] = result['app_cash']
            if len(sections) > 1:
                _, pos_rows = parse_csv_data(sections[1])
                app_positions_count = len(pos_rows)
                for row in pos_rows:
                    result['app_positions'].append({
                        'name': row[0], 'market_cap': float(row[1]) if len(row) > 1 else 0,
                        'holdings': int(row[2]) if len(row) > 2 else 0,
                        'available': int(row[3]) if len(row) > 3 else 0,
                        'current_price': float(row[4]) if len(row) > 4 else 0,
                        'cost': float(row[5]) if len(row) > 5 else 0,
                    })
    except Exception as e:
        logger.error(f"Failed to fetch positions from app: {e}")

    # 2. Running orders → smart_orders count
    try:
        order_csv = await get_order_from_app_smart_order_page_structured(
            config, llm, tools, target_tabs=["运行中"]
        )
        if order_csv:
            _, ord_rows = parse_csv_data(order_csv)
            app_running_orders_count = len(ord_rows)
            for row in ord_rows:
                result['app_running_orders'].append({
                    'name': row[0], 'code': row[1],
                    'trigger_condition': row[2],
                    'buy_or_sell_price_type': row[3],
                    'buy_or_sell_quantity': float(row[4]),
                    'valid_until': row[5],
                    'order_number': row[6],
                    'reason_of_ending': row[7] if len(row) > 7 else '',
                    'status': row[8] if len(row) > 8 else '运行中',
                })
    except Exception as e:
        logger.error(f"Failed to fetch orders from app: {e}")

    # 3. Transactions count
    try:
        tx_csv = await get_transactions_from_app_history_page_structured(config, llm, tools)
        if tx_csv:
            _, tx_rows = parse_csv_data(tx_csv)
            app_tx_count = len(tx_rows)
    except Exception as e:
        logger.error(f"Failed to fetch transactions from app: {e}")

    result['app_state']['holdings'] = app_positions_count
    result['app_state']['running_orders'] = app_running_orders_count
    result['app_state']['transactions'] = app_tx_count

    # ── Compare against DB ──
    with DB.cursor() as cursor:
        # 1. summary_account — cash
        db_cash_row = cursor.execute(
            "SELECT cash, total_assets, position_percent FROM summary_account WHERE user_id=?",
            (user_id,)
        ).fetchone()
        db_cash = float(db_cash_row[0]) if db_cash_row and db_cash_row[0] else 0.0
        db_assets = float(db_cash_row[1]) if db_cash_row and db_cash_row[1] else 0.0
        result['db_state']['cash'] = db_cash
        result['db_state']['total_assets'] = db_assets

        if result['app_cash'] is not None:
            cash_diff_pct = abs(db_cash - result['app_cash']) / max(result['app_cash'], 1.0)
            if cash_diff_pct > 0.01:
                result['db_matches_app'] = False
                result['mismatches'].append(
                    f'summary_account.cash: DB=¥{db_cash:,.2f} vs App=¥{result["app_cash"]:,.2f} ({cash_diff_pct:.2%} diff)'
                )

        # 2. holding_stocks — count
        db_holdings = cursor.execute(
            "SELECT COUNT(*) FROM holding_stocks WHERE user_id=? AND holdings > 0",
            (user_id,)
        ).fetchone()[0]
        result['db_state']['holdings'] = db_holdings
        if db_holdings != app_positions_count:
            result['db_matches_app'] = False
            result['mismatches'].append(
                f'holding_stocks: DB={db_holdings} vs App={app_positions_count}'
            )

        # 3. smart_orders — running orders count
        db_running = cursor.execute(
            "SELECT COUNT(*) FROM smart_orders WHERE status='running' AND user_id=?",
            (user_id,)
        ).fetchone()[0]
        db_total_orders = cursor.execute(
            "SELECT COUNT(*) FROM smart_orders WHERE user_id=?",
            (user_id,)
        ).fetchone()[0]
        result['db_state']['running_orders'] = db_running
        result['db_state']['total_orders'] = db_total_orders
        if db_running != app_running_orders_count:
            result['db_matches_app'] = False
            result['mismatches'].append(
                f'smart_orders(running): DB={db_running} vs App={app_running_orders_count}'
            )

        # 4. transactions — count
        current_year = datetime.now().year
        tx_boundary = f"{_sync_cutoff_date} 00:00:00" if _sync_cutoff_date else f"{current_year}-01-01 00:00:00"
        db_tx_count = cursor.execute(
            "SELECT COUNT(*) FROM transactions WHERE user_id=? AND transaction_date >= ?",
            (user_id, tx_boundary)
        ).fetchone()[0]
        result['db_state']['transactions'] = db_tx_count
        if app_tx_count > 0 and db_tx_count != app_tx_count:
            result['db_matches_app'] = False
            result['mismatches'].append(
                f'transactions: DB={db_tx_count} vs App={app_tx_count}'
            )

    # ── Log results ──
    logger.info(f"DB  state: Cash=¥{db_cash:,.2f}, Holdings={db_holdings}, "
                f"RunningOrders={db_running}, TX={db_tx_count}")
    logger.info(f"App state: Cash=¥{result['app_cash'] or 0:,.2f}, Holdings={app_positions_count}, "
                f"RunningOrders={app_running_orders_count}, TX={app_tx_count}")

    if result['db_matches_app']:
        logger.info("✅ DB matches App — all tables in sync.")
    else:
        logger.warning(f"❌ DB vs App MISMATCH: {', '.join(result['mismatches'])}")

    return result


# ==========================================
# 4. Main Orchestration Flow
# ==========================================

async def cron_sync_app_to_db(check_trading_day_and_time: bool = True) -> dict:
    """Cron job to sync app data using structured extractors to database."""
    # Not trading day, exit directly
    date = str(datetime.now().date())
    is_trading_day = calendar.is_trading_day(date)
    if check_trading_day_and_time and not is_trading_day:
        logger.info(f"Today {date} is not a trading day. Exiting cron job.")
        return {'skipped': True, 'reason': 'Not a trading day'}

    # Market open times and refresh interval
    market_open_times_refresh_interval = get_market_open_times_refresh_interval()
    now = datetime.now()
    start1, end1, start2, end2, refresh_interval = market_open_times_refresh_interval.split(',')
    
    start1_time = datetime.strptime(f"{date} {start1.strip()}", "%Y-%m-%d %H:%M:%S")
    end1_time = datetime.strptime(f"{date} {end1.strip()}", "%Y-%m-%d %H:%M:%S")
    start2_time = datetime.strptime(f"{date} {start2.strip()}", "%Y-%m-%d %H:%M:%S")
    
    if check_trading_day_and_time and (now < start1_time or (end1.strip() and start2.strip() and now > end1_time and now < start2_time)):
        logger.info(f"Current time {now} is before market open time {start1_time} or in break period. Exiting.")
        return {'skipped': True, 'reason': 'Outside trading hours'}
        
    end2_time = datetime.strptime(f"{date} {end2.strip()}", "%Y-%m-%d %H:%M:%S") + timedelta(hours=1)
    if check_trading_day_and_time and now > end2_time:
        logger.info(f"Current time {now} is after market close time. Exiting.")
        return {'skipped': True, 'reason': 'After market close + 1 hour'}

    logger.info("Starting cron job using ADB UI parsing extraction...")
    tools, llm, config = await pre_requirements()
    
    # 1. Fetch App Origin Data
    logger.info("Extracting indices and stock quotes from app...")
    quote_csv = await get_index_stock_from_app_quote_page_structured(config=config, llm=llm, tools=tools)
    
    logger.info("Extracting account summary and positions from app...")
    position_csv = await get_summary_position_from_app_position_page_structured(config=config, llm=llm, tools=tools)
    
    logger.info("Extracting smart orders from app...")
    # Only scrape running + triggered tabs; skip 已结束 (historical orders
    # can be enormous and cause timeouts on first sync)
    order_csv = await get_order_from_app_smart_order_page_structured(
        config=config, llm=llm, tools=tools,
        target_tabs=["今日已触发", "运行中"]
    )
    
    logger.info("Extracting transaction history from app...")
    tx_csv = await get_transactions_from_app_history_page_structured(
        config=config, llm=llm, tools=tools,
        stop_before_date=_sync_cutoff_date
    )
    
    # 2. Pre-save app counts
    pos_count = 0
    if position_csv:
        pos_sections = position_csv.strip().split('\n\n')
        if len(pos_sections) >= 2:
            pos_header, pos_rows = parse_csv_data(pos_sections[1])
            pos_count = len(pos_rows)
            
    ord_count = 0
    if order_csv:
        ord_header, ord_rows = parse_csv_data(order_csv)
        ord_count = len(ord_rows)
        
    tx_count = 0
    if tx_csv:
        tx_header, tx_rows = parse_csv_data(tx_csv)
        tx_count = len(tx_rows)
        
    # 3. Sync to Database
    logger.info("Syncing index and stock quotes to DB...")
    result_quote = sync_index_quote_data_to_db(quote_csv, user_id=1)
    if not result_quote.get('success'):
        raise ValueError(f"sync_index_quote_data_to_db failed: {result_quote}")
    logger.info(f"Synced indices & quotes: {result_quote}")
        
    logger.info("Syncing summary position data to DB...")
    result_position = sync_summary_position_data_to_db(position_csv, user_id=1)
    if not result_position.get('success'):
        raise ValueError(f"sync_summary_position_data_to_db failed: {result_position}")
    logger.info(f"Synced portfolio: {result_position}")
        
    logger.info("Syncing order data to DB...")
    # Check if order data has actual rows (not just header)
    _has_orders = False
    if order_csv:
        _ord_header, _ord_rows = parse_csv_data(order_csv)
        _has_orders = len(_ord_rows) > 0
    if _has_orders:
        result_order = sync_order_data_to_db(order_csv, user_id=1)
        if not result_order.get('success'):
            raise ValueError(f"sync_order_data_to_db failed: {result_order}")
        logger.info(f"Synced smart orders: {result_order}")
    else:
        result_order = {'success': True, 'message': 'No orders to sync (app had 0 orders)'}
        logger.info("No smart orders in app — skipping order sync.")
        
    logger.info("Syncing transactions to DB...")
    result_tx = sync_transactions_to_db(tx_csv, user_id=1, cutoff_date=_sync_cutoff_date)
    if not result_tx.get('success'):
        raise ValueError(f"sync_transactions_to_db failed: {result_tx}")
    logger.info(f"Synced transactions: {result_tx}")

    # 4. Query DB Counts Post-Sync
    with DB.cursor() as cursor:
        db_holdings = cursor.execute("SELECT COUNT(*) FROM holding_stocks").fetchone()[0]
        db_orders = cursor.execute("SELECT COUNT(*) FROM smart_orders").fetchone()[0]
        current_year = datetime.now().year
        # Use cutoff date for transaction count if set
        tx_boundary = f"{_sync_cutoff_date} 00:00:00" if _sync_cutoff_date else f"{current_year}-01-01 00:00:00"
        db_transactions = cursor.execute(
            "SELECT COUNT(*) FROM transactions WHERE transaction_date >= ?",
            (tx_boundary,)
        ).fetchone()[0]

    # 5. Run shared/db/sql.sh
    import subprocess
    logger.info("Running shared/db/sql.sh to show current DB records...")
    try:
        sql_res = subprocess.run("bash shared/db/sql.sh", shell=True, capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        logger.info(f"\n[shared/db/sql.sh output]:\n{sql_res.stdout}")
    except Exception as e:
        logger.error(f"Failed to run shared/db/sql.sh: {e}")

    # 6. DB vs App Data Comparison and Validation
    logger.info("\n==================================================")
    logger.info("       REAL-TIME DATA MATCH CHECK (DB VS APP)")
    logger.info("==================================================")
    logger.info(f"Holding Stocks: App = {pos_count}, DB = {db_holdings}")
    logger.info(f"Smart Orders:   App = {ord_count}, DB = {db_orders}")
    logger.info(f"Transactions:   App = {tx_count}, DB = {db_transactions} (>= {current_year}-01-01)")
    
    is_real_sync = (pos_count == db_holdings and ord_count == db_orders and tx_count == db_transactions)
    if is_real_sync:
        logger.info("REAL SYNC VALIDATION: SUCCESS (DB EXACTLY MATCHES APP DATA)")
    else:
        logger.warning("REAL SYNC VALIDATION: FAILURE (DISCREPANCY DETECTED)")
    logger.info("==================================================")

    result = {
        'quote_sync_result': result_quote,
        'position_sync_result': result_position,
        'order_sync_result': result_order,
        'transaction_sync_result': result_tx,
        'real_sync': is_real_sync
    }
    
    summary_msg = (
        "\n==================================================\n"
        "             DATABASE SYNC SUMMARY\n"
        "==================================================\n"
        f"1. Quotes:       {result_quote.get('message', 'No message')}\n"
        f"2. Portfolio:    {result_position.get('message', 'No message')}\n"
        f"3. Smart Orders: {result_order.get('message', 'No message')}\n"
        f"4. Transactions: {result_tx.get('message', 'No message')}\n"
        f"5. Real Sync:    {'SUCCESS (DB == App)' if is_real_sync else 'FAILURE (Discrepancy)'}\n"
        "=================================================="
    )
    logger.info(summary_msg)
    logger.info("Cron job completed successfully.")
    return result


if __name__ == "__main__":
    login()
    asyncio.run(cron_sync_app_to_db(check_trading_day_and_time=False))
