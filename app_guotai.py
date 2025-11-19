import os
import sys
import time
import asyncio
import dotenv
import re
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional
from loguru import logger

from droidrun import DroidAgent, AdbTools
from llama_index.llms.google_genai import GoogleGenAI


# Add the parent directory to Python path so we can import from utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from gm_emulate_adb import get_device_connectivity, check_app_exist
from utils.gemini_thinking import create_gemini_with_thinking
from db.db import DB
from backtest.utils.trading_calendar import calendar
from utils.trading_time import get_market_open_times_refresh_interval
from backtest.utils.logging_config import configure_logger

# Load environment variables
dotenv.load_dotenv(os.path.expanduser('.env'), verbose=True)
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_THINKING_BUDGET = os.getenv("GEMINI_THINKING_BUDGET", "-1")
if not GOOGLE_API_KEY:
    raise ValueError("❌ GOOGLE_API_KEY not set. Skipping DroidAgent test.")
GUOTAI_PACKAGE_NAME = os.getenv('GUOTAI_PACKAGE_NAME')
GUOTAI_PASSWORD = os.getenv('GUOTAI_PASSWORD')
if not GUOTAI_PACKAGE_NAME or not GUOTAI_PASSWORD:
    raise ValueError("❌ GUOTAI_PACKAGE_NAME or GUOTAI_PASSWORD not set. Please set them in the .env file.")

LOG_LEVEL = os.getenv("LOG_LEVEL", default="DEBUG")
LOG_PATH = os.getenv("LOG_PATH", default="/tmp/ibacktest_logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)

def close_app(app_package_name: str = GUOTAI_PACKAGE_NAME):
    """Force stop the specified app on the connected device. not keep running on background."""

    logger.info(f"Force stopping app {app_package_name}...")
    os.system(f"adb shell am force-stop {app_package_name}")
    time.sleep(3)


def open_app(tools: AdbTools, app_package_name: str = GUOTAI_PACKAGE_NAME):
    """Open the specified app on the connected device.

    # Check app main activity:
    adb shell dumpsys package com.guotai.dazhihui | grep -A 5 "android.intent.action.MAIN"
    # Restart app:
    adb shell am start -S -n com.guotai.dazhihui/com.gtja.home.InitScreen

    # List all processes and filter by package name
    adb shell ps | grep guotai
    # Or list all user apps
    adb shell "ps -A | grep 'u0_a' | awk '{print \\$9}' | sort | uniq"
    # Get process ID if running (returns empty if not running)
    adb shell pidof com.guotai.dazhihui
    """

    logger.info(f"Opening app {app_package_name}...")
    tools.start_app(app_package_name)
    time.sleep(5)  # Wait for app to open


def pre_requirements(app_package_name: str = GUOTAI_PACKAGE_NAME) -> tuple[AdbTools, GoogleGenAI]:
    """Check device connectivity and app existence."""

    llm = create_gemini_with_thinking(
        api_key=GOOGLE_API_KEY,
        model=GEMINI_MODEL,
        thinking_budget=int(GEMINI_THINKING_BUDGET),
        temperature=0.01
    )
    """
    # https://docs.droidrun.ai/v3/concepts/agent#planning-mode, reflection mode need vision=True, need gemini-2.5-flash or gpt-4o.
    llm = create_gemini_with_thinking(
        api_key=GOOGLE_API_KEY,
        model='gemini-2.5-flash',
        thinking_budget=0,
        temperature=0.1,
    )
    """

    tools = get_device_connectivity()
    if not tools:
        raise ValueError("❌ No connected Android device found. Please connect a device via ADB.")
    check_app_exist(tools, app_package_name)
    return tools, llm


def replay_page(description: List[str] = ['行情', '我的持仓']):
    """
    Replay guotai app and navigate to the specified page by finding matching trajectory. default is my quote page.

    Pre-requisites:
    - droidrun "Open '国泰海通君弘', then tap '行情' on the bottom navigation bar, then tap '我的持仓'" --provider GoogleGenAI --model models/gemini-2.5-pro --temperature 0.1 --reasoning --save-trajectory step
    - droidrun "Open '国泰海通君弘', then tap '交易' on the bottom navigation bar, then tap "创建订单', then tap '登录交易帐号,查看订单详情', then input password '817671', then tap '登录'" --provider GoogleGenAI --model models/gemini-2.5-pro --temperature 0.1 --reasoning --save-trajectory step

    Args:
        description: List of keywords to match in trajectory description

    Raises:
        ValueError: If no matching trajectory is found or replay fails
    """
    # Get list of available trajectories
    try:
        result = subprocess.run(
            'stty rows 24 columns 180; droidrun macro list',
            capture_output=True,
            text=True,
            check=True,
            shell=True
        )
        output = result.stdout
        # Parse the output to find matching trajectory, Handle multi-line descriptions in the table
        lines = output.split('\n')
        current_folder = full_description = ''

        for line in lines:
            # Skip header and separator lines
            if '│' in line and not line.strip().startswith('┃') and not line.strip().startswith('┡'):
                parts = [p.strip() for p in line.split('│') if p.strip()]
                if len(parts) >= 1:
                    # Check if first part is a folder name (contains only digits, underscores, and lowercase letters)
                    current_folder = parts[0]
                    if current_folder and '_' in current_folder and all(c.isdigit() or c == '_' or c.islower() for c in current_folder):
                        full_description = parts[1] if len(parts) > 1 else ''
                        if all(keyword in full_description for keyword in description):
                            current_folder = f"trajectories/{current_folder}"
                            logger.info(f"✅ Found matching trajectory: {current_folder}")
                            logger.info(f"   Description: {full_description}")
                            break

        if not current_folder or 'trajectories/' not in current_folder:
            raise ValueError(f"⚠️  No trajectory found matching keywords: {description}")
    except Exception as e:
        raise ValueError(f"⚠️  Failed to replay {description}, ERROR: {e}")

    logger.info(f"🔄 Replaying trajectory {description} from {current_folder}...")
    result = os.system(f"droidrun macro replay {current_folder} --delay 6")
    if result != 0:
        raise ValueError(f"❌ Failed to replay {description}, exit code: {result}")
    logger.info("✅ Replay completed successfully")


async def droid_run(llm: GoogleGenAI | None = None, tools: AdbTools | None = None, goal: str | None = None):
    if not goal:
        raise ValueError("❌ Goal not set. Please provide a goal.")

    if llm is None:
        raise ValueError("❌ LLM not provided. Please provide a GoogleGenAI instance.")

    if tools is None:
        raise ValueError("❌ AdbTools not provided. Please connect a device via ADB.")

    agent = DroidAgent(
        goal=goal,
        llm=llm,
        tools=tools,
        # Flow: Goal → Planning → Execution → Reflection → Re-planning (if needed) → Result, 50+ steps. need vision=True and gemini-2.5-flash or gpt-4o.
        #reflection=True,
        #vision=True,
        # Flow: Goal → Planning → Step-by-step Execution → Result, 15-20 steps.
        reasoning=True,      # Optional: enable planning/reasoning
        timeout=10000,
        max_steps=60,
        enable_tracing=False,       # Requires running 'phoenix serve' in a separate terminal first. Use for LLM response debugging.
        save_trajectories='none',   # Save trajectories to local file for analysis
        debug=False,
    )

    result = await agent.run()
    # Default result: {'success': True, 'reason': 'All stock and index data has been successfully extracted.', 'output': 'All stock and index data has been successfully extracted.', 'steps': 8}
    if not result['success']:
        raise ValueError(f"❌ Goal not completed: {result['reason']}")
    return result['output']


def get_format_output(tools: AdbTools, output:str, start_str:str = 'csv_format_name,', data_name:str = 'which data') -> str:
    """
    Get CSV format string from doridrun output to extract relevant data.
    """
    if not output:
        return output
    # Index Name, Index_Name etc
    start_str = start_str.strip().lower()
    start = output.split(',', 1)[0].split(' ', 1)[-1].split('_', 1)[-1].strip().lower() + ','
    if not output.lower().startswith(start_str) and start != start_str:
        output = str(tools.reason).strip()
        start = output.split(',', 1)[0].split(' ', 1)[-1].split('_', 1)[-1].strip().lower() + ','
    if not output.lower().startswith(start_str) and start != start_str:
        output = tools.get_memory()[-1].strip()
        start = output.split(',', 1)[0].split(' ', 1)[-1].split('_', 1)[-1].strip().lower() + ','
        if not output.lower().startswith(start_str) and start != start_str:
            if ':' not in output:
                # AI analyze: ["Indices: [{'name':x,..},...]\n]Stocks: [{'name':x,...},...]", "Stocks: [{'name':x,...},...]"]
                raise ValueError(f"FORMAT ERROR: need AI to extract {data_name}: {output}")
            output = output.split(':', 1)[1].strip()
    start = output.split(',', 1)[0].split(' ', 1)[-1].split('_', 1)[-1].strip().lower() + ','
    if not output.lower().startswith(start_str) and start != start_str:
        output_save = output
        # Regex to remove 'name,:' or 'Indices Data:' or 'Stocks Data:' lines.
        output = re.sub(r'.*name,:\s*', '', output, flags=re.IGNORECASE)
        output = re.sub(r'.*indices data:\s*', '', output, flags=re.IGNORECASE)
        output = re.sub(r'.*stocks data:\s*', '', output, flags=re.IGNORECASE)
        start = output.split(',', 1)[0].split(' ', 1)[-1].split('_', 1)[-1].strip().lower() + ','
        if not output.lower().startswith(start_str) and start != start_str:
            raise ValueError(f"FORMAT ERROR: {data_name} not start with {start_str} and {start}:\n{output_save}")
    return output


async def get_order_from_app_smart_order_page(llm: GoogleGenAI, tools: AdbTools) -> str:
    """
    Get real-time smart order data from mobile guotai app smart order page.

    到价买入/到价卖出/止赢止损 name code            委托已报

    触发条件 股价>=13.430元(触发止盈)               已触发
            股价<=11.430元(触发止损)
    买入/卖出价格 即时买一价                        自动委托
    买入/卖出数量 4000（股/张/份）
    有效期至 2026-01-12 收盘前
    订单编号 20251014142918W0023684467

    结束原因：到价卖出条件已触发，停止

    Return: str
    """
    goal = """
    If any dialog is visible, tap '取消' or '关闭' to dismiss it.
    Tap BACK, then wait over 3 seconds until '交易' visible on the bottom navigation bar, then tap '交易',
    then tap '创建订单', then wait over 3 seconds until '查看详情' visible, then tap '查看详情', then wait over 3 seconds until '已结束' visible, then tap '已结束'.
    Extract all visible orders (name,code,trigger_condition,buy_or_sell_price_type,buy_or_sell_quantity,valid_until,order_number,reason_of_ending) from the list. Store the extracted orders in memory using remember().
    Continue scroll down the order list with resource id "com.guotai.dazhihui:id/table_view_body" to load more orders until '全部加载完成' visible.
    When the order list has been scrolled down, then extract all newly visible orders and append them to the 'Extracted Orders' in memory until '全部加载完成' visible.
    Format the last 'Extracted Orders' in memory into CSV format, stored the final result in memory using remember() and return it.
    """
    output = await droid_run(llm=llm, tools=tools, goal=goal)
    output = get_format_output(tools, output, 'name,', 'orders data')
    return output


async def get_index_stock_from_app_quote_page(llm: GoogleGenAI, tools: AdbTools) -> str:
    """
    Get real-time index and stock data from mobile guotai app quote page.

    Return:
    index_name,index_number,index_ratio
    Shanghai (沪),3882.78,+0.52%
    Shenzhen (深),13526.51,+0.35%
    Chi (创),3238.16,0.00%

    name,code,latest_price,increase_percentage,increase_amount
    中科三环,000970,14.17,+1.21%,+0.17
    ... until the total count is 12.
    """
    goal = """
    If any dialog is visible, tap '取消' or '关闭' to dismiss it.
    Tap '我的持仓' if '我的持仓' visible else tap BACK -> tap '行情' on the bottom navigation bar -> tap '我的持仓'.
    Extract the 3 indices (name, number, ratio) from the top of the screen and all visible stocks (name, code, latest price, increase percentage, increase amount) from the list. Store both sets of data in memory using remember().
    Continue scroll down the stock list with resource id "com.guotai.dazhihui:id/table_view_body" to load more stocks until '查看持仓' visible.
    When the stock list has been scrolled down, then extract all newly visible stocks and append them to the 'Extracted Stocks' in memory until '查看持仓' visible.
    Format the last 'Extracted Indices' and 'Extracted Stocks' in memory into CSV format, combine them with two new lines separator, stored the final result in memory using remember() and return it.
    """
    output = await droid_run(llm=llm, tools=tools, goal=goal)
    output = get_format_output(tools, output, 'name,', 'quote data')
    return output


async def get_summary_position_from_app_position_page(llm: GoogleGenAI, tools: AdbTools) -> str:
    """
    Get real-time summary and position data from mobile guotai app position page.

    浮动盈亏
    Floating Profit/Loss
    帐户资产                     总市值                   仓位
    Account Assets              Market Cap              Positions
    可用 Available               可取 Desirable

    Return:
    floating_profit_loss,account_assets,market_cap,positions,available,desirable
    -361757.86,855169.66,814839.00,95.28%,40330.66,40330.66
    name,market_cap,open,available,current_price,cost,floating_profit,floating_loss_percentage
    深振业Ａ,385875.000,37500,37500,10.290,13.361,-115165.77,-22.99%
    ...
    """
    goal = """
    If any dialog is visible, tap '取消' or '关闭' to dismiss it.
    Tap '查看持仓' if '查看持仓' visible, then input password '817671' and then tap '登录' if on the login page, then wait over 3 seconds until '浮动盈亏' visible.
    Extract the 1 account summary (floating_profit_loss, account_assets, market_cap, positions, available, desirable) from the top of the screen
    and all visible stocks (name, market_cap, open, available, current_price, cost, floating_profit, floating_loss_percentage) from the list. Store both sets of data in memory using remember().
    Continue scroll down the stock list with resource id "com.guotai.dazhihui:id/table_view_body" to load more stocks until '查看已清仓股票' visible.
    When the stock list has been scrolled down, then extract all newly visible stocks and append them to the 'Extracted Stocks' in memory until '查看已清仓股票' visible.
    Format the last 'Extracted Summary' and 'Extracted Stocks' in memory into CSV format, combine them with two new lines separator, stored the final result in memory using remember() and return it.
    """
    output = await droid_run(llm=llm, tools=tools, goal=goal)
    output = get_format_output(tools, output, 'floating_profit_loss,', 'position data')
    return output


def parse_csv_data(csv_text: str) -> Tuple[List[str], List[List[str]]]:
    """Parse CSV format text into header and data rows.

    Ignores head and tail blank lines automatically.

    Args:
        csv_text: CSV format text with header in first line

    Returns:
        Tuple of (header_list, data_rows_list)
    """
    # Strip head and tail blank lines, keep only non-empty lines
    lines = [line.strip() for line in csv_text.strip().split('\n') if line.strip()]
    if not lines:
        return [], []

    header = [h.strip() for h in lines[0].split(',')]
    data_rows = []
    for line in lines[1:]:
        row = [cell.strip() for cell in line.split(',')]
        data_rows.append(row)

    return header, data_rows


def extract_stock_code(stock_name_with_code: str) -> Tuple[str, str]:
    """Extract stock code and name from format like '中科三环(000970)'.

    Args:
        stock_name_with_code: String like '中科三环(000970)'

    Returns:
        Tuple of (stock_name, stock_code)
    """
    match = re.match(r'^(.+?)\((\w+)\)$', stock_name_with_code)
    if match:
        return match.group(1).strip(), match.group(2).strip()
    return stock_name_with_code, ""


def parse_percentage(percent_str: str) -> float:
    """Convert percentage string like '+1.21%' or '95.28%' to float.

    Args:
        percent_str: Percentage string with or without + sign and % symbol

    Returns:
        Float value (e.g., 1.21 for '+1.21%')
    """
    cleaned = percent_str.replace('%', '').replace('+', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def parse_number(num_str: str) -> float:
    """Convert number string like '+0.17' or '-115165.77' to float. Or use regex cut 100(other characters) to 100.

    Args:
        num_str: Number string with or without + sign, or digital with other characters at end.

    Returns:
        Float value
    """
    cleaned = num_str.replace('+', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        try:
            match = re.match(r'(\d+)(?:.*)', cleaned)
            if match:
                return float(match.group(1))
        except Exception as e:
            raise ValueError(f"❌ Failed to parse number from string: {num_str}, ERROR: {e}")
    return 0.0


def sync_index_quote_data_to_db(quote_data: Optional[str] = None, user_id: int = 1) -> Dict:
    """Sync real-time index and stock quote data from mobile app to database.

    This function synchronizes CSV format data from get_from_app_quote_page() with the SQLite database,
    ensuring the mobile app and database have the same data.
    Note: Automatically ignores head and tail blank lines in quote_data.

    Args:
        quote_data: CSV format string from get_from_app_quote_page()
                   Contains index data and stock quote data
        user_id: User ID for database records (default: 1)

    Returns:
        Dict with success status and message
    """
    index_code = index_name = ''
    stock_code = stock_name = ''
    result = {
        'success': True,
        'message': [],
        'indices_updated': 0,
        'stocks_updated': 0,
        'stocks_removed': 0  # Track removed records
    }
    has_exceptions = True
    with DB.cursor() as cursor:
        current_time = datetime.now().isoformat()
        # Track codes from source data
        source_index_codes = set()
        source_stock_codes = set()

        # Process quote page data (indices and stock quotes)
        if quote_data:
            # Split into index data and stock data sections
            sections = quote_data.strip().split('\n\n')

            # Section 1: Market indices
            if len(sections) >= 1:
                header, index_rows = parse_csv_data(sections[0])
                for row in index_rows:
                    if len(row) >= 3:
                        index_name = row[0]
                        index_value = parse_number(row[1])
                        index_ratio = parse_percentage(row[2])

                        # Generate index_code based on name
                        index_name_map = {
                            'index_1:上证指数': '上证指数',
                            'index_2:深证成指': '深证成指',
                            'index_3:创业板指': '创业板指',
                            'shanghai (沪)': '上证指数',
                            'shenzhen (深)': '深证成指',
                            'chi (创)': '创业板指',
                            'shanghai': '上证指数',
                            'shenzhen': '深证成指',
                            'chinext': '创业板指',
                            '沪': '上证指数',
                            '深': '深证成指',
                            '创': '创业板指'
                        }
                        index_code_map = {
                            '上证指数': '000001.SH',
                            '深证成指': '399001.SZ',
                            '创业板指': '399006.SZ'
                        }
                        index_name = index_name_map.get(index_name.lower(), index_name)
                        index_code = index_code_map.get(index_name.lower(), index_name).upper()

                        # Track this index code
                        source_index_codes.add(index_code)

                        # Upsert into market_indices
                        cursor.execute("""
                            INSERT INTO market_indices (index_code, index_name, current_value, change_percent, last_updated)
                            VALUES (?, ?, ?, ?, ?)
                            ON CONFLICT(index_code) DO UPDATE SET
                                index_name = excluded.index_name,
                                current_value = excluded.current_value,
                                change_percent = excluded.change_percent,
                                last_updated = excluded.last_updated
                        """, (index_code, index_name, index_value, index_ratio, current_time))
                        result['indices_updated'] += 1

            # Section 2: Stock quotes
            if len(sections) >= 2:
                header, stock_rows = parse_csv_data(sections[1])
                for row in stock_rows:
                    if len(row) >= 5:
                        stock_name = row[0]
                        stock_code = row[1]
                        current_price = parse_number(row[2])
                        change_percent = parse_percentage(row[3])
                        change_amount = parse_number(row[4])

                        # Check stock_code validity
                        if not stock_code or not re.match(r'^\d{6}$', stock_code):
                            raise ValueError(f"Invalid stock code format: {stock_code} for stock {stock_name}. row: {row}")
                        if not stock_name or not re.match(r'^[\u4e00-\u9fff\uFF21-\uFF3AA-Z]+$', stock_name):
                            raise ValueError(f"Invalid stock name format: {stock_name}")

                        # Track this stock code
                        source_stock_codes.add(stock_code)

                        # First try to update by name (in case stock exists with different code format)
                        cursor.execute("""
                            UPDATE holding_stocks
                            SET code = ?,
                                current_price = ?,
                                change = ?,
                                change_percent = ?,
                                last_updated = ?
                            WHERE user_id = ? AND name = ?
                        """, (stock_code, current_price, change_amount, change_percent, current_time, user_id, stock_name))

                        if cursor.rowcount == 0:
                            # If no match by name, try upsert by code
                            cursor.execute("""
                                INSERT INTO holding_stocks (user_id, code, name, current_price, change, change_percent, last_updated)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(user_id, code) DO UPDATE SET
                                    name = excluded.name,
                                    current_price = excluded.current_price,
                                    change = excluded.change,
                                    change_percent = excluded.change_percent,
                                    last_updated = excluded.last_updated
                            """, (user_id, stock_code, stock_name, current_price, change_amount, change_percent, current_time))

                        result['stocks_updated'] += 1

            # Remove orphaned stock records (exist in DB but not in source data)
            if source_stock_codes:
                placeholders = ','.join('?' * len(source_stock_codes))
                cursor.execute(f"""
                    DELETE FROM holding_stocks
                    WHERE user_id = ? AND code NOT IN ({placeholders})
                """, [user_id] + list(source_stock_codes))
                result['stocks_removed'] = cursor.rowcount

            result['message'].append(f"Updated {result['indices_updated']} indices and {result['stocks_updated']} stock quotes, removed {result['stocks_removed']} orphaned stocks")

        result['message'] = ' | '.join(result['message'])

        # Analysis result whether data is fully synced
        if not quote_data:
            raise ValueError(f"Quote data is missing: {quote_data}.")
        if not all([index_code, index_name, stock_code, stock_name]):
            raise ValueError(f"""No valid index or stock data found in the provided data.
                             index_code: {index_code}, index_name: {index_name},
                             stock_code: {stock_code}, stock_name: {stock_name},
                             quote_data: {quote_data}""")
        has_exceptions = False

    if has_exceptions:
        return {
            'success': False,
            'message': f'Error saving data to database: {result}',
            'error': 'has exceptions in sync index quote data to db'
        }
    return result


def sync_summary_position_data_to_db(position_data: Optional[str] = None, user_id: int = 1) -> Dict:
    """Sync real-time data from mobile app to database.

    This function synchronizes CSV format data from get_from_app_position_page() with the SQLite database,
    ensuring the mobile app and database have the same data.
    Note: Automatically ignores head and tail blank lines in position_data.

    Args:
        position_data: CSV format string from get_from_app_position_page()
                      Contains summary data and stock position data
        user_id: User ID for database records (default: 1)

    Returns:
        Dict with success status and message
    """
    account_assets = market_cap = 0.0
    stock_name = ''
    result = {
        'success': True,
        'message': [],
        'total_updated': False,
        'stocks_updated': 0,
        'stocks_removed': 0  # Track removed records
    }
    has_exceptions = True
    with DB.cursor() as cursor:
        current_time = datetime.now().isoformat()
        # Track stock names from source data
        source_stock_names = set()

        # Process position page data (summary and stock positions)
        if position_data:
            sections = position_data.strip().split('\n\n')

            # Section 1: Portfolio summary
            if len(sections) >= 1:
                header, summary_rows = parse_csv_data(sections[0])
                if summary_rows and len(summary_rows[0]) >= 6:
                    row = summary_rows[0]
                    floating_pnl = parse_number(row[0])
                    account_assets = parse_number(row[1])
                    market_cap = parse_number(row[2])
                    position_percent = parse_percentage(row[3])
                    available = parse_number(row[4])
                    withdrawable = parse_number(row[5])

                    floating_pnl_percent = (floating_pnl / market_cap * 100) if market_cap > 0 else 0.0

                    cursor.execute("""
                        INSERT INTO summary_account (
                            user_id, total_market_value, floating_pnl_summary,
                            floating_pnl_summary_percent, total_assets, cash,
                            position_percent, withdrawable, last_updated
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(user_id) DO UPDATE SET
                            total_market_value = excluded.total_market_value,
                            floating_pnl_summary = excluded.floating_pnl_summary,
                            floating_pnl_summary_percent = excluded.floating_pnl_summary_percent,
                            total_assets = excluded.total_assets,
                            cash = excluded.cash,
                            position_percent = excluded.position_percent,
                            withdrawable = excluded.withdrawable,
                            last_updated = excluded.last_updated
                    """, (user_id, market_cap, floating_pnl, floating_pnl_percent,
                          account_assets, available, position_percent, withdrawable, current_time))

                    result['total_updated'] = True
                    result['message'].append(f"Updated portfolio summary: Total Assets={account_assets}, Market Value={market_cap}")

            # Section 2: Stock positions
            if len(sections) >= 2:
                header, position_rows = parse_csv_data(sections[1])
                for row in position_rows:
                    if len(row) >= 8:
                        stock_name = row[0]
                        market_value = parse_number(row[1])
                        holdings = int(parse_number(row[2]))
                        available_shares = int(parse_number(row[3]))
                        current_price = parse_number(row[4])
                        cost_basis = parse_number(row[5])
                        pnl_float = parse_number(row[6])
                        pnl_float_percent = parse_percentage(row[7])

                        if not stock_name or not re.match(r'^[\u4e00-\u9fff\uFF21-\uFF3AA-Z]+$', stock_name):
                            raise ValueError(f"Invalid stock name format: {stock_name}")

                        # Track this stock name
                        source_stock_names.add(stock_name)

                        # Try to update existing stock by name
                        cursor.execute("""
                            UPDATE holding_stocks
                            SET market_value = ?,
                                holdings = ?,
                                available_shares = ?,
                                current_price = ?,
                                cost_basis_diluted = ?,
                                cost_basis_total = ?,
                                pnl_float = ?,
                                pnl_float_percent = ?,
                                last_updated = ?
                            WHERE user_id = ? AND name = ?
                        """, (market_value, holdings, available_shares, current_price,
                              cost_basis, cost_basis, pnl_float, pnl_float_percent,
                              current_time, user_id, stock_name))

                        # If stock doesn't exist, we can optionally insert it (though position data lacks code)
                        # For now, we only update existing stocks as per original logic
                        if cursor.rowcount > 0:
                            result['stocks_updated'] += 1

                # Remove orphaned stock records (exist in DB but not in source data)
                # Only remove if we have valid position data
                if source_stock_names:
                    placeholders = ','.join('?' * len(source_stock_names))
                    cursor.execute(f"""
                        DELETE FROM holding_stocks
                        WHERE user_id = ? AND name NOT IN ({placeholders})
                    """, [user_id] + list(source_stock_names))
                    result['stocks_removed'] = cursor.rowcount

                result['message'].append(f"Updated {result.get('stocks_updated', 0)} stock positions from {len(position_rows)} records, removed {result['stocks_removed']} orphaned stocks")

        result['message'] = ' | '.join(result['message'])

        if not position_data:
            raise ValueError(f"Position data is missing: {position_data}.")
        if not all([stock_name,]):
            raise ValueError(f"""No valid stock data found in the provided data.
                             stock_name: {stock_name}.
                             position_data: {position_data}""")
        if account_assets <= 0.0 or market_cap <= 0.0:
            raise ValueError(f"Updated portfolio summary has invalid Total Assets({account_assets}) or Market Value({market_cap}).")
        has_exceptions = False

    if has_exceptions:
        return {
            'success': False,
            'message': f'Error saving data to database: {result}',
            'error': 'has exceptions in sync summary position data to db'
        }
    return result


def sync_order_data_to_db(order_data: Optional[str] = None, user_id: int = 1) -> Dict:
    """Sync real-time order data from mobile app to database.

    This function synchronizes CSV format data from get_order_from_app_smart_order_page()
    with the SQLite database, ensuring the mobile app and database have the same data.

    Note: Automatically ignores head and tail blank lines in order_data.

    Args:
        order_data: CSV format string Contains smart order data
        user_id: User ID for database records (default: 1)

    Returns:
        Dict with success status and message
    """
    name = code = None
    result = {
        'success': True,
        'message': [],
        'orders_updated': 0,
        'orders_removed': 0  # Track removed records
    }
    has_exceptions = True
    with DB.cursor() as cursor:
        # Track order numbers from source data
        source_order_numbers = set()

        # Process order data
        if order_data:
            header, order_rows = parse_csv_data(order_data)
            for row in order_rows:
                if len(row) >= 8:
                    name = row[0]
                    code = row[1]
                    trigger_condition = row[2]
                    buy_or_sell_price_type = row[3]
                    buy_or_sell_quantity = parse_number(row[4])
                    valid_until = row[5]
                    order_number = row[6]
                    reason_of_ending = row[7]

                    if not name and not code: #,,,,xxx,xxx,,
                        continue
                    if not name or not re.match(r'^[\u4e00-\u9fff\uFF21-\uFF3AA-Z]+$', name):
                        raise ValueError(f"Invalid stock name format: {name}")

                    # Track this order number
                    source_order_numbers.add(order_number)

                    # Upsert into smart_orders
                    cursor.execute("""
                        INSERT INTO smart_orders (user_id, code, name, trigger_condition, buy_or_sell_price_type, buy_or_sell_quantity, valid_until, order_number, reason_of_ending, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        ON CONFLICT(order_number) DO UPDATE SET
                            code = excluded.code,
                            name = excluded.name,
                            trigger_condition = excluded.trigger_condition,
                            buy_or_sell_price_type = excluded.buy_or_sell_price_type,
                            buy_or_sell_quantity = excluded.buy_or_sell_quantity,
                            valid_until = excluded.valid_until,
                            reason_of_ending = excluded.reason_of_ending,
                            last_updated = excluded.last_updated
                    """, (user_id, code, name, trigger_condition, buy_or_sell_price_type, buy_or_sell_quantity, valid_until, order_number, reason_of_ending, datetime.now))
                    result['orders_updated'] += 1

            # Remove orphaned order records (exist in DB but not in source data)
            if source_order_numbers:
                placeholders = ','.join('?' * len(source_order_numbers))
                cursor.execute(f"""
                    DELETE FROM smart_orders
                    WHERE user_id = ? AND order_number NOT IN ({placeholders})
                """, [user_id] + list(source_order_numbers))
                result['orders_removed'] = cursor.rowcount

            result['message'].append(f"Updated {result['orders_updated']} smart orders, removed {result['orders_removed']} orphaned orders")

        if not all([name, code]):
            raise ValueError(f"No valid order data found to update. name: {name}, code: {code}")
        has_exceptions = False

    if has_exceptions:
        return {
            'success': False,
            'message': f'Error saving order data to database: {result}',
            'error': 'has exceptions in sync order data to db'
        }
    return result


async def cron_sync_app_data_to_db(check_trading_day_and_time: bool = True) -> dict:
    """Cron job to sync app data to database.

    $ crontab -l # m h  dom mon dow   command
    # Runs every 30 minutes on weekdays (Mon-Fri) between market open hours(9:30-11:30,13:00-15:00), 11:30-13:00 or after market close + 1 hour(16:00) not included.
    30 9   * * 1-5 export PYENV_ROOT=$HOME/.pyenv/; export PATH=$PYENV_ROOT/bin:$PATH/; cd $HOME/apps/imobile; source venv/bin/activate; nohup $HOME/apps/imobile/app_guotai.py >> /tmp/cron_guotai_sync.log 2>&1 &
    0,30 10-11 * * 1-5 export PYENV_ROOT=$HOME/.pyenv/; export PATH=$PYENV_ROOT/bin:$PATH/; cd $HOME/apps/imobile; source venv/bin/activate; nohup $HOME/apps/imobile/app_guotai.py >> /tmp/cron_guotai_sync.log 2>&1 &
    0,30 13-14 * * 1-5 export PYENV_ROOT=$HOME/.pyenv/; export PATH=$PYENV_ROOT/bin:$PATH/; cd $HOME/apps/imobile; source venv/bin/activate; nohup $HOME/apps/imobile/app_guotai.py >> /tmp/cron_guotai_sync.log 2>&1 &
    0,30 15  * * 1-5 export PYENV_ROOT=$HOME/.pyenv/; export PATH=$PYENV_ROOT/bin:$PATH/; cd $HOME/apps/imobile; source venv/bin/activate; nohup $HOME/apps/imobile/app_guotai.py >> /tmp/cron_guotai_sync.log 2>&1 &
    0    16  * * 1-5 export PYENV_ROOT=$HOME/.pyenv/; export PATH=$PYENV_ROOT/bin:$PATH/; cd $HOME/apps/imobile; source venv/bin/activate; nohup $HOME/apps/imobile/app_guotai.py >> /tmp/cron_guotai_sync.log 2>&1 &
    """
    # TODO:
    # - Fixed principal(本金) = 300000, When withdraw or deposit, principal will change, need manual adjust.
    # - After trading date 15:30
    #   - 登录 ->交易 ->当日盈亏 ->summary_account: today_pnl, today_pnl_percent

    # Not trading day, exit directly
    date = str(datetime.now().date())
    is_trading_day = calendar.is_trading_day(date)
    if check_trading_day_and_time and not is_trading_day:
        logger.info(f"Today {date} is not a trading day. Exiting cron job.")
        return {'skipped': True, 'reason': 'Not a trading day'}

    # Market open times and refresh interval: 09:30:00,11:30:00,13:00:00,15:00:00,15 minutes
    market_open_times_refresh_interval = get_market_open_times_refresh_interval()
    now = datetime.now()
    start1, end1, start2, end2, refresh_interval = market_open_times_refresh_interval.split(',')
    # Before market open time or in end1--start2, exit directly
    start1_time = datetime.strptime(f"{date} {start1.strip()}", "%Y-%m-%d %H:%M:%S")
    end1_time = datetime.strptime(f"{date} {end1.strip()}", "%Y-%m-%d %H:%M:%S")
    start2_time = datetime.strptime(f"{date} {start2.strip()}", "%Y-%m-%d %H:%M:%S")
    if check_trading_day_and_time and (now < start1_time or (end1.strip() and start2.strip() and now > end1_time and now < start2_time)):
        logger.info(f"Current time {now} is before market open time {start1_time} or in the break period {end1_time} - {start2_time}. Exiting cron job.")
        return {'skipped': True, 'reason': 'Outside trading hours'}
    # After market close time + 1 hours, exit directly
    end2_time = datetime.strptime(f"{date} {end2.strip()}", "%Y-%m-%d %H:%M:%S") + timedelta(hours=1)
    if check_trading_day_and_time and now > end2_time:
        logger.info(f"Current time {now} is after market close time {end2_time}. Exiting cron job.")
        return {'skipped': True, 'reason': 'After market close + 1 hour'}

    logger.info("Starting cron job to sync app data to database...")
    tools, llm = pre_requirements()
    result_quote = result_position = result_order = {}
    need_index_quote = need_summary_position = need_smart_order = True
    times = 1
    while (need_index_quote or need_summary_position or need_smart_order) and times <= 3:
        quote_data = position_data = order_data = None
        if times == 1:
            close_app()
            replay_page(description=['我的持仓',])

        if need_index_quote:
            quote_data = await get_index_stock_from_app_quote_page(llm=llm, tools=tools)
            result_quote = sync_index_quote_data_to_db(quote_data, user_id=1)
            if result_quote['success']:
                logger.info(f'sync_index_quote_data_to_db done, result: {result_quote}')
                need_index_quote = False
                times = 1
            else:
                logger.error(f'Error in sync_index_quote_data_to_db: {result_quote}')
                times += 1
                if times <= 3:
                    logger.info(f"Retrying... Attempt {times}/3 after 5 seconds.")
                    time.sleep(5)
                    continue
                else:
                    logger.error("Max retry attempts reached. Exiting cron job.")
                    break
        if need_summary_position:
            if times == 1:
                tools, llm = pre_requirements()
            position_data = await get_summary_position_from_app_position_page(llm=llm, tools=tools)
            result_position = sync_summary_position_data_to_db(position_data, user_id=1)
            if result_position['success']:
                logger.info(f'sync_summary_position_data_to_db done, result: {result_position}')
                need_summary_position = False
                times = 1
            else:
                logger.error(f'Error in sync_summary_position_data_to_db: {result_position}')
                times += 1
                if times <= 3:
                    logger.info(f"Retrying... Attempt {times}/3 after 5 seconds.")
                    #os.system("DROIDRUN_TELEMETRY_ENABLED=false droidrun run 'Tab BACK' --provider GoogleGenAI --model gemini-2.5-pro")
                    time.sleep(5)
                    continue
                else:
                    logger.error("Max retry attempts reached. Exiting cron job.")
                    break

        if need_smart_order:
            if times == 1:
                tools, llm = pre_requirements()
            order_data = await get_order_from_app_smart_order_page(llm=llm, tools=tools)
            result_order = sync_order_data_to_db(order_data, user_id=1)
            if result_order['success']:
                logger.info(f'sync_order_data_to_db done, result: {result_order}')
                need_smart_order = False
                break
            else:
                logger.error(f'Error in sync_order_data_to_db: {result_order}')
                times += 1
                if times <= 3:
                    logger.info(f"Retrying... Attempt {times}/3 after 5 seconds.")
                    time.sleep(5)
                else:
                    logger.error("Max retry attempts reached. Exiting cron job.")

    result = {
        'quote_sync_result': result_quote,
        'position_sync_result': result_position,
        'order_sync_result': result_order
    }
    logger.info("Cron job to sync app data to database completed.\nResult: {}".format(result))
    return result


if __name__ == "__main__":
    asyncio.run(cron_sync_app_data_to_db(False))
