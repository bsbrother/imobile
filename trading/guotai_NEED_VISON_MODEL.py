import os
import sys
import time
import asyncio
import dotenv
import re
import subprocess
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from loguru import logger
import unicodedata

from mobilerun import (
    AndroidDriver,
    MobileAgent, MobileConfig,
    AgentConfig, ExecutorConfig, LoggingConfig, TracingConfig
)
from llama_index.llms.google_genai import GoogleGenAI


# Add the parent directory to Python path so we can import from utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from trading.adb import get_device_connectivity, check_app_exist
from utils.gemini_free_api import create_free_llm
from shared.db.db import DB
from backtest.utils.trading_calendar import calendar
from utils.trading_time import get_market_open_times_refresh_interval
from backtest.utils.logging_config import configure_logger
from utils.ocr_screenshot import ocr_screenshot2file

import logging
logging.getLogger("google.genai._api_client").setLevel(logging.ERROR)

# Load environment variables (shell env takes priority over .env)
dotenv.load_dotenv(os.path.expanduser('.env'), override=False, verbose=False)
# https://aistudio.google.com/app/apikey
# GEMINI_API_KEY:  for raw google.genai SDK (stock analysis)
# GOOGLE_API_KEY: for llama_index GoogleGenAI + DroidRun vision agent
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY') or os.getenv('GEMINI_API_KEY')
# gemini-3.1-flash-lite-preview: free tier, llama_index compatible, vision
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")
GEMINI_THINKING_BUDGET = os.getenv("GEMINI_THINKING_BUDGET", "0")
if not GOOGLE_API_KEY:
    raise ValueError("❌ GOOGLE_API_KEY not set. Skipping MobileAgent test.")
GUOTAI_PACKAGE_NAME = os.getenv('GUOTAI_PACKAGE_NAME')
GUOTAI_PASSWORD = os.getenv('GUOTAI_PASSWORD')
if not GUOTAI_PACKAGE_NAME or not GUOTAI_PASSWORD:
    raise ValueError("❌ GUOTAI_PACKAGE_NAME or GUOTAI_PASSWORD not set. Please set them in the .env file.")

LOG_LEVEL = os.getenv("LOG_LEVEL", default="DEBUG")
LOG_PATH = os.getenv("LOG_PATH", default="/tmp/ibacktest_logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)


def verify_screen_contains(keywords: List[str], screenshot_path: str = '/tmp/screenshot.png',
                                 ocr_output_path: str = '/tmp/screenshot.txt') -> bool:
    """Check if expected UI elements(keywords) are visible on screen.

    Takes a screenshot via ADB, runs PaddleOCR, then greps keywords from the
    OCR output file.
    """
    logger.info(f'Checking screenshot for keywords: {keywords}')

    # 1. Take screenshot via ADB
    result = subprocess.run(
        f"adb exec-out screencap -p > {screenshot_path}",
        shell=True, capture_output=True, text=True
    )
    if result.returncode != 0:
        logger.warning(f"Screenshot capture failed: {result.stderr}")
        return False

    # 2. OCR with PaddleOCR (PaddleOCR prints each line to stdout)
    ocr_screenshot2file(screenshot_path, ocr_output_path)

    # 3. Check all keywords present in OCR output
    with open(ocr_output_path, 'r', encoding='utf-8') as f:
        content = f.read()
    missing = [k for k in keywords if k not in content]
    if missing:
        logger.warning(f"Screenshot check failed — missing keywords: {missing}")
        return False
    logger.info('Screenshot check passed — all keywords found.')
    return True


def close_app(app_package_name: str = GUOTAI_PACKAGE_NAME) -> None:
    """Force stop the specified app on the connected device. not keep running on background."""

    logger.info(f"Force stopping app {app_package_name}...")
    subprocess.run(f"adb shell am force-stop {app_package_name}", shell=True)
    time.sleep(1)
    logger.info(f"Force stop app {app_package_name} completed.")


def open_app(app_package_name: str = GUOTAI_PACKAGE_NAME) -> None:
    """Open the specified app on the connected device. """

    # Check app main activity:
    # adb shell dumpsys package 'com.guotai.dazhihui' | grep -A 5 "android.intent.action.MAIN"
    # List all processes and filter by package name
    # adb shell ps | grep 'com.guotai.dazhihui'
    # List all user apps
    # adb shell "ps -A | grep 'u0_a' | awk '{print \\$9}' | sort | uniq"
    # Get process ID if running (returns empty if not running)
    result = subprocess.run(f"adb shell pidof {app_package_name}", shell=True, capture_output=True, text=True)
    if result.returncode == 0:
        logger.info(f"App {app_package_name} is already running.")
    else:
        logger.info(f"App {app_package_name} is not running, start now ...")
        subprocess.run(f"adb shell am start -W -n {app_package_name}/com.gtja.home.InitScreen", shell=True)
        time.sleep(5)
        subprocess.run(f"adb shell pidof {app_package_name} && echo 'App is running' || echo 'App is NOT running'", shell=True)


def restart_app(app_package_name: str = GUOTAI_PACKAGE_NAME) -> None:
    """Restart app then at homepage, but need re-login. """

    subprocess.run(f"adb shell am start -S -n {app_package_name}/com.gtja.home.InitScreen --activity-clear-task", shell=True)


def goto_homepage(app_package_name: str = GUOTAI_PACKAGE_NAME) -> None:
    """Goto the app's homepage(not home screen)."""

    # close_app, open_app or restart_app will lose session/task, need re-login.
    # Force clear entire task and restart activity (keeps app process/login).
    subprocess.run(f"""
        adb shell am start -n {app_package_name}/com.gtja.home.InitScreen --activity-clear-task \
        && sleep 1 && \
        adb shell monkey -p {app_package_name} -c android.intent.category.LAUNCHER 1 \
        && sleep 3
    """, shell=True)


def login() -> None:
    """login to trading account via MobileAgent vision navigation.

    mobilerun run "Tap '立即登录', then tap '{GUOTAI_PASSWORD}' one by one, then tap '登录'" \
        --provider GoogleGenAI \
        --model gemini-3.1-flash-lite-preview \
        --save-trajectory step
    """
    # Goto to app homepage then check login status, login if not logged in.
    goto_homepage()
    result = subprocess.run(
        "adb exec-out screencap -p > /tmp/screenshot.png && tesseract /tmp/screenshot.png stdout -l chi_sim >/tmp/screenshot.txt && grep -qE '登录' /tmp/screenshot.txt",
        shell=True
    )
    if result.returncode != 0:
        logger.info("✅ Already logged in")
        return

    logger.info(f"Login Guotai trading account...")
    replay_page(['立即登录'])
    time.sleep(8)
    logger.info("✅ Login successful")


async def pre_requirements(app_package_name: str = GUOTAI_PACKAGE_NAME) -> tuple[AndroidDriver, GoogleGenAI, MobileConfig]:
    """Check device connectivity and app existence."""

    llm = create_free_llm(
        api_key=GOOGLE_API_KEY,
        model=GEMINI_MODEL,
        temperature=0.01
    )
    """
    # https://docs.droidrun.ai/v3/concepts/agent#planning-mode, reflection mode need vision=True, need gemini-2.5-flash or gpt-4o.
    llm = create_free_llm(
        api_key=GOOGLE_API_KEY,
        model='gemini-2.5-flash',
        thinking_budget=0,
        temperature=0.1,
    )
    """

    tools = await get_device_connectivity()
    if not tools:
        raise ValueError("❌ No connected Android device found. Please connect a device via ADB.")
    await check_app_exist(tools, app_package_name)

    # https://docs.droidrun.ai/sdk/configuration
    # Flow: Goal → Planning → Execution → Reflection → Re-planning (if needed) → Result, 50+ steps. need vision=True and gemini-2.5-flash or gpt-4o.
    config = MobileConfig(
        agent=AgentConfig(
            max_steps=60,
            reasoning=True,
            after_sleep_action=1.5,
            executor=ExecutorConfig(vision=True)
        ),
        #device=DeviceConfig(
        #    serial="127.0.0.1:6555",
        #    platform="android",
        #    use_tcp=False
        #),
        logging=LoggingConfig(
            debug=False,
            save_trajectory='none', #"action",
            trajectory_gifs=False
        ),
        tracing=TracingConfig(enabled=False),
    )

    return tools, llm, config
def _toggle_trajectory_password(folder: str, real_password: str, to_real: bool) -> None:
    """Replace password placeholders with real value or vice versa in trajectory files."""
    import glob
    json_files = glob.glob(os.path.join(folder, "*.json"))
    json_files += glob.glob(os.path.join(folder, "**/*.json"), recursive=True)

    placeholder = "'{GUOTAI_PASSWORD}'"

    for file_path in json_files:
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()

            if to_real:
                new_content = content.replace(placeholder, real_password)
            else:
                new_content = content.replace(real_password, placeholder)

            if new_content != content:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                logger.info(f"Updated password placeholders in {os.path.basename(file_path)} (to_real={to_real})")
        except Exception as e:
            logger.error(f"Failed to update password in {file_path}: {e}")


def replay_page(description: List[str] = ['行情', '我的持仓']) -> None:
    """
    Replay guotai app navigation using pre-recorded trajectory.
    if no matching trajectory found, raise ValueError.

    Pre-requisites (record new trajectory):
      You must start app or goto app's homepage.
      mobilerun run "Tap '行情', then tap '我的持仓'" \\
        --provider GoogleGenAI --model gemini-3.1-flash-lite-preview \\
        --save-trajectory step

    Args:
        description: List of keywords to match in trajectory description
    """
    TRAJ_DIR = 'trading/trajectory'
    try:
        result = subprocess.run(
            f'mobilerun macro list {TRAJ_DIR}',
            capture_output=True, text=True, check=True, shell=True
        )
        output = result.stdout
        lines = output.split('\n')

        trajectories = []
        current_folder = ""
        current_description = []

        for line in lines:
            if '│' in line and not line.strip().startswith('┃') and not line.strip().startswith('┡') and not line.strip().startswith('└'):
                parts = [p.strip() for p in line.split('│')]
                if len(parts) >= 4:
                    folder_name = parts[1]
                    desc_part = parts[2]
                    if folder_name:
                        if current_folder:
                            trajectories.append((current_folder, ' '.join(current_description)))
                        current_folder = folder_name
                        current_description = [desc_part]
                    else:
                        if current_folder:
                            current_description.append(desc_part)

        if current_folder:
            trajectories.append((current_folder, ' '.join(current_description)))

        matched_folder = None
        for folder, full_desc in trajectories:
            if all(keyword in full_desc for keyword in description):
                matched_folder = f'{TRAJ_DIR}/{folder}'
                logger.info(f'✅ Found matching trajectory: {matched_folder}')
                break

        if not matched_folder or 'Error loading' in output:
            raise ValueError(f'No valid trajectory found for {description}')

    except Exception as e:
        raise ValueError(f'Replay lookup failed: {e}')

    logger.info(f'🔄 Replaying trajectory {description} from {matched_folder}...')

    # Temporarily restore the real password
    _toggle_trajectory_password(matched_folder, GUOTAI_PASSWORD, to_real=True)
    try:
        result = subprocess.run(f'mobilerun macro replay {matched_folder} --state-threshold 0.54', shell=True)
        if result.returncode != 0:
            raise ValueError(f'❌ Replay failed , check {matched_folder} is valid trajectory.')
    finally:
        # Always restore the secure placeholder
        _toggle_trajectory_password(matched_folder, GUOTAI_PASSWORD, to_real=False)

    logger.info('✅ Replay completed successfully')


def get_agent(config: MobileConfig | None = None, llm: GoogleGenAI | None = None, tools: AndroidDriver | None = None, goal: str | None = None, output_model=None) -> MobileAgent:
    """
    Create a MobileAgent instance with the specified goal, LLM, and AndroidDriver.

    Args:
        config: MobileConfig instance
        llm: GoogleGenAI instance
        tools: AndroidDriver instance
        goal: Goal for the agent

    Returns:
        MobileAgent instance
    """

    if not goal:
        raise ValueError("❌ Goal not set. Please provide a goal.")
    if not llm:
        raise ValueError("❌ LLM not provided. Please provide a GoogleGenAI instance.")
    if not tools:
        raise ValueError("❌ AndroidDriver not provided. Please connect a device via ADB.")

    agent = MobileAgent(
        goal=goal,
        config=config,
        llms={"default": llm},
        driver=tools,
        timeout=10000,
        output_model=output_model,
    )

    return agent


def get_format_output(output:str) -> str:
    """
    Get CSV format string from doridrun output to extract relevant data.

    output: 'I have successfully extracted all indices and stocks from the \'我的持仓\' page, formatted them into CSV format, and stored the result. Here is the final CSV data:\n\nIndex Name,Number,Ratio\n沪,3890.45,"+0.36%"\n深,
    """
    output = output.lower().strip()
    SPLIT_STR = ['data is:', 'data:', 'result:']
    for s in SPLIT_STR:
        if s in output:
            output = output.split(s)[-1].strip()
            break
    # Regex to remove 'name,:' or 'Indices Data:' or 'Stocks Data:' lines.
    output = re.sub(r'.*name,:\s*', '', output, flags=re.IGNORECASE)
    output = re.sub(r'.*indices:\s*', '', output, flags=re.IGNORECASE)
    output = re.sub(r'.*stocks:\s*', '', output, flags=re.IGNORECASE)
    output = re.sub(r'.*summary:\s*', '', output, flags=re.IGNORECASE)
    output = re.sub(r'.*indices data:\s*', '', output, flags=re.IGNORECASE)
    output = re.sub(r'.*stocks data:\s*', '', output, flags=re.IGNORECASE)
    output = re.sub(r'.*summary data:\s*', '', output, flags=re.IGNORECASE)
    return output


async def get_order_from_app_smart_order_page(config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver) -> str:
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
    goto_homepage()
    replay_page(['智能订单', '查看详情'])
    goal = """
    Tap '已结束' tab.
    Extract all visible orders (name,code,trigger_condition,buy_or_sell_price_type,buy_or_sell_quantity,valid_until,order_number,reason_of_ending) from the list. Store the extracted orders in memory using remember().
    Continue scroll down the order list with resource id "com.guotai.dazhihui:id/table_view_body" to load more orders until '全部加载完成' visible.
    When the order list has been scrolled down, then extract all newly visible orders and append them to the 'Extracted Orders' in memory until '全部加载完成' visible.
    Format the last 'Extracted Orders' in memory into CSV format, and return the final CSV data in your response starting with 'result:'.
    """
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal)
    result = await agent.run()
    if not result.success or not result.reason:
        raise ValueError(f"❌ Goal get orders not completed: {result.reason}")
    output = get_format_output(result.reason.strip())
    return output


async def get_index_stock_from_app_quote_page(config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver) -> str:
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
    goto_homepage()
    replay_page(['行情','我的持仓'])
    goal = """
    Extract the 3 indices (3 values: name, number, ratio) from the top of the screen and all visible stocks (5 values: name, code, latest price, increase percentage, increase amount) from the list. Store both sets of data in memory using remember().
    loop scroll down to got more stocks in list, then extract all newly visible stocks and append them to the 'Extracted Stocks' in memory until no more new stocks.
    Format the last 'Extracted Indices' and 'Extracted Stocks' in memory into CSV format, combine them with two new lines separator, return the final CSV data in your response starting with 'result:'.
    """
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal)
    result = await agent.run()
    if not result.success or not result.reason:
        raise ValueError(f"❌ Goal get index and stock not completed: {result.reason}")
    output = get_format_output(result.reason.strip())
    return output


async def get_summary_position_from_app_position_page(config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver) -> str:
    """
    Get real-time summary and position data from mobile guotai app position page.

    浮动盈亏
    Floating Profit/Loss
    帐户资产                     总市值                   仓位
    Account Assets              Market Cap              Positions
    可用 Available               可取 Desirable

    Return:
    result:
    Extracted Summary: or other strings
    floating_profit_loss,account_assets,market_cap,positions,available,desirable
    -361757.86,855169.66,814839.00,95.28%,40330.66,40330.66


    Extracted Stocks: or other strings.
    name,market_cap,open,available,current_price,cost,floating_profit,floating_loss_percentage
    深振业Ａ,385875.000,37500,37500,10.290,13.361,-115165.77,-22.99%
    ...
    """
    goto_homepage()
    replay_page(['交易','持仓'])
    goal = """
    Extract the 1 account summary (6 numeric values: floating_profit_loss, account_assets, market_cap, positions, available, desirable) from the top of the screen and all visible stocks (8 values: name, market_cap, position_number, available, current_price, cost, floating_profit, floating_loss_percentage) from the list. Store both sets of data in memory using remember().
    Continue scroll down to got more stocks in list, then extract all newly visible stocks and append them to the 'Extracted Stocks' in memory until no more new stocks.
    Format the last 'Extracted Summary' and 'Extracted Stocks' in memory into CSV format, combine them with two new lines separator, remove all lines like 'Extracted xx' and only return the final CSV data in your response starting with 'result:', DO NOT return summary content, just final result CSV data.
    """
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal)
    result = await agent.run()
    if not result.success or not result.reason:
        raise ValueError(f"❌ Goal get summary and position not completed: {result.reason}")
    return get_format_output(result.reason.strip())


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

    # Some time AI return is no header, so we need to check if the first line is header
    # if first line has format 'xx, yy, ... has more than 1 numberic in it...' then it has no header.
    if len(lines[0].split(',')) >=2 and any(item.replace('.', '', 1).isdigit() for item in lines[0].split(',')):
        header = []
        data_rows = []
        for line in lines:
            row = [cell.strip() for cell in line.split(',')]
            data_rows.append(row)
    else:
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


def normalize_stock_name(name: str) -> str:
    """Normalize stock name by converting full-width characters to half-width.

    Args:
        name: Stock name string (e.g., '深振业Ａ')

    Returns:
        Normalized string (e.g., '深振业A')
    """
    if not name:
        return ""
    return unicodedata.normalize('NFKC', name)


def clean_stock_name(name: str) -> str:
    """Clean stock name by removing prefixes/suffixes (e.g., *ST, ST, N, U, W, V)."""
    if not name:
        return ""
    # Strip common prefixes (case-insensitive)
    name = re.sub(r'^(\*ST|ST|NST|PT|N|C|R|kr|KR|S|XD|XR|DR)\s*', '', name, flags=re.IGNORECASE)
    # Strip common suffixes in parentheses or at the end
    name = re.sub(r'\s*(\(U\)|\(W\)|\(V\)|U|W|V)$', '', name, flags=re.IGNORECASE)
    return name


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
    if not quote_data:
        raise ValueError(f"Quote data is missing: {quote_data}.")
    sections = quote_data.split('\n\n')
    if len(sections) != 2:
        raise ValueError(f"Quote data must has indexes \\n\\n stocks. But got: {sections}")
    index_code = index_name = ''
    stock_code = stock_name = ''
    result: Dict[str, Any] = {
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

        # Section 1: Market indices
        header, index_rows = parse_csv_data(sections[0])
        for row in index_rows:
            if len(row) != 3:
                raise ValueError(f"Index row {row} is invalid.")
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
            index_code = index_code_map.get(index_name.lower(), '').upper()

            # Skip rows that don't map to a valid index code
            if not index_code or not re.match(r'^\d{6}\.[A-Z]{2}$', index_code):
                logger.warning(f"Skipping invalid index: name={index_name}, code={index_code}")
                continue

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
        header, stock_rows = parse_csv_data(sections[1])
        for row in stock_rows:
            if len(row) != 5:
                raise ValueError(f"Invalid stock row: {row}")
            stock_name = normalize_stock_name(row[0])
            stock_code = row[1]
            # stock_code must 6 number, if not, prefix fill with 0.
            if re.match(r'^\d{1,6}$', stock_code):
                if len(stock_code) < 6:
                    stock_code = stock_code.zfill(6)
            current_price = parse_number(row[2])
            change_percent = parse_percentage(row[3])
            change_amount = parse_number(row[4])

            # Check stock_code validity
            if not stock_code or not re.match(r'^\d{6}$', stock_code):
                raise ValueError(f"Invalid stock code format: {stock_code} for stock {stock_name}. row: {row}")
            if not stock_name or not re.match(r'^[\w\uFF21-\uFF3A*]+$', stock_name):
                raise ValueError(f"Invalid stock name format: {stock_name}")

            # Track this stock code
            source_stock_codes.add(stock_code)

            # Match holding stock by code, name, or cleaned name
            db_holdings = cursor.execute("SELECT code, name FROM holding_stocks WHERE user_id = ?", (user_id,)).fetchall()
            matched_db_code = None
            matched_db_name = None

            # 1. Match by code
            for db_code, db_name in db_holdings:
                if db_code == stock_code:
                    matched_db_code = db_code
                    matched_db_name = db_name
                    break

            # 2. Match by exact name
            if not matched_db_code:
                for db_code, db_name in db_holdings:
                    if db_name == stock_name:
                        matched_db_code = db_code
                        matched_db_name = db_name
                        break

            # 3. Match by cleaned name
            if not matched_db_code:
                cleaned_target = clean_stock_name(stock_name)
                for db_code, db_name in db_holdings:
                    if clean_stock_name(db_name) == cleaned_target:
                        matched_db_code = db_code
                        matched_db_name = db_name
                        break

            if matched_db_code:
                # Update existing record
                if matched_db_code != '000000':
                    cursor.execute("""
                        UPDATE holding_stocks
                        SET code = ?,
                            name = ?,
                            current_price = ?,
                            change = ?,
                            change_percent = ?,
                            last_updated = ?
                        WHERE user_id = ? AND code = ?
                    """, (stock_code, stock_name, current_price, change_amount, change_percent, current_time, user_id, matched_db_code))
                else:
                    cursor.execute("""
                        UPDATE holding_stocks
                        SET code = ?,
                            name = ?,
                            current_price = ?,
                            change = ?,
                            change_percent = ?,
                            last_updated = ?
                        WHERE user_id = ? AND name = ?
                    """, (stock_code, stock_name, current_price, change_amount, change_percent, current_time, user_id, matched_db_name))
            else:
                # Insert new record
                cursor.execute("""
                    INSERT INTO holding_stocks (user_id, code, name, current_price, change, change_percent, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
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
    if not position_data:
        raise ValueError(f"Position data is missing: {position_data}.")
    sections = position_data.split('\n\n')
    if len(sections) != 2:
        raise ValueError(f"""Position data must has sumary \\n\\n stocks, but got: {sections}.""")

    account_assets = market_cap = 0.0
    stock_name = ''
    result: Dict[str, Any] = {
        'success': True,
        'message': [],
        'total_updated': False,
        'stocks_updated': 0,
        'stocks_removed': 0  # Track removed records
    }
    # Track stock names from source data
    source_stock_names = set()
    has_exceptions = True
    with DB.cursor() as cursor:
        current_time = datetime.now().isoformat()
        # Process position page data (summary and stock positions)
        # Section 1: Portfolio summary
        header, summary_rows = parse_csv_data(sections[0])
        if not summary_rows or len(summary_rows[0]) != 6:
            raise ValueError("Position summary data is missing or invalid: {summary_rows}")
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

        # Fetch current holdings from DB to match in memory
        db_holdings = cursor.execute("SELECT code, name FROM holding_stocks WHERE user_id = ?", (user_id,)).fetchall()

        # Load code resolution map
        db_codes = {}
        # 1. From stocks.index.json
        import json
        json_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "stocks.index.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data:
                        if len(item) >= 7 and item[6] == 'CN':
                            db_codes[normalize_stock_name(item[2])] = item[1]
            except Exception as e:
                logger.warning(f"Failed to load stocks.index.json in position sync: {e}")
        # 2. From database tables
        for row_tx in cursor.execute("SELECT name, code FROM transactions WHERE code != '000000'").fetchall():
            db_codes[row_tx[0]] = row_tx[1]
        for row_so in cursor.execute("SELECT name, code FROM smart_orders WHERE code != '000000'").fetchall():
            db_codes[row_so[0]] = row_so[1]
        for row_hs in cursor.execute("SELECT name, code FROM holding_stocks WHERE code != '000000'").fetchall():
            db_codes[row_hs[0]] = row_hs[1]

        def resolve_code(name_to_lookup: str) -> str:
            if name_to_lookup in db_codes:
                return db_codes[name_to_lookup]
            cleaned_target = clean_stock_name(name_to_lookup)
            for name_key, code_val in db_codes.items():
                if clean_stock_name(name_key) == cleaned_target:
                    return code_val
            return ""

        # Section 2: Stock positions
        header, position_rows = parse_csv_data(sections[1])
        for row in position_rows:
            if len(row) != 8:
                raise ValueError(f"Invalid stock position data format: {row}")
            stock_name = normalize_stock_name(row[0])
            market_value = parse_number(row[1])
            holdings = int(parse_number(row[2]))
            available_shares = int(parse_number(row[3]))
            current_price = parse_number(row[4])
            cost_basis = parse_number(row[5])
            pnl_float = parse_number(row[6])
            pnl_float_percent = parse_percentage(row[7])

            if not stock_name or not re.match(r'^[\w\uFF21-\uFF3A*]+$', stock_name):
                raise ValueError(f"Invalid stock name format: {stock_name}")

            # Track this stock name
            source_stock_names.add(stock_name)

            # Match holding stock by code, name, or cleaned name
            matched_db_code = None
            matched_db_name = None

            resolved_code = resolve_code(stock_name)

            # 1. Match by resolved code
            if resolved_code and resolved_code != "000000":
                for db_code, db_name in db_holdings:
                    if db_code == resolved_code:
                        matched_db_code = db_code
                        matched_db_name = db_name
                        break

            # 2. Match by exact name
            if not matched_db_code:
                for db_code, db_name in db_holdings:
                    if db_name == stock_name:
                        matched_db_code = db_code
                        matched_db_name = db_name
                        break

            # 3. Match by cleaned name
            if not matched_db_code:
                cleaned_target = clean_stock_name(stock_name)
                for db_code, db_name in db_holdings:
                    if clean_stock_name(db_name) == cleaned_target:
                        matched_db_code = db_code
                        matched_db_name = db_name
                        break

            if matched_db_code:
                # Update existing record
                if matched_db_code != '000000':
                    cursor.execute("""
                        UPDATE holding_stocks
                        SET name = ?,
                            market_value = ?,
                            holdings = ?,
                            available_shares = ?,
                            current_price = ?,
                            cost_basis_diluted = ?,
                            cost_basis_total = ?,
                            pnl_float = ?,
                            pnl_float_percent = ?,
                            last_updated = ?
                        WHERE user_id = ? AND code = ?
                    """, (stock_name, market_value, holdings, available_shares, current_price,
                        cost_basis, cost_basis, pnl_float, pnl_float_percent,
                        current_time, user_id, matched_db_code))
                else:
                    cursor.execute("""
                        UPDATE holding_stocks
                        SET name = ?,
                            market_value = ?,
                            holdings = ?,
                            available_shares = ?,
                            current_price = ?,
                            cost_basis_diluted = ?,
                            cost_basis_total = ?,
                            pnl_float = ?,
                            pnl_float_percent = ?,
                            last_updated = ?
                        WHERE user_id = ? AND name = ?
                    """, (stock_name, market_value, holdings, available_shares, current_price,
                        cost_basis, cost_basis, pnl_float, pnl_float_percent,
                        current_time, user_id, matched_db_name))
                result['stocks_updated'] += 1

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

        if not all(list(source_stock_names)):
            raise ValueError(f"""No valid stock data found in the provided data.
                             source_stock_names: {source_stock_names}.
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
    result: Dict[str, Any] = {
        'success': True,
        'message': [],
        'orders_updated': 0,
        'orders_removed': 0  # Track removed records
    }
    has_exceptions = True
    with DB.cursor() as cursor:
        current_time = datetime.now().isoformat()
        # Track order numbers from source data
        source_order_numbers = set()

        # Process order data
        if order_data:
            header, order_rows = parse_csv_data(order_data)
            for row in order_rows:
                if len(row) >= 8:
                    name = normalize_stock_name(row[0])
                    code = row[1]
                    trigger_condition = row[2]
                    buy_or_sell_price_type = row[3]
                    buy_or_sell_quantity = parse_number(row[4])
                    valid_until = row[5]
                    order_number = row[6]
                    reason_of_ending = row[7]

                    if not name and not code: #,,,,xxx,xxx,,
                        continue
                    if not name or not re.match(r'^[\w\uFF21-\uFF3A*]+$', name):
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
                    """, (user_id, code, name, trigger_condition, buy_or_sell_price_type, buy_or_sell_quantity, valid_until, order_number, reason_of_ending, current_time))
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

        result['message'] = ' | '.join(result['message'])

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


async def get_transactions_from_app_history_page(config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver) -> str:
    """Get transaction history from mobile guotai app history page.

    成交时间    名称        买卖类型    成交价    成交量    成交金额
    2026-05-05  中科三环    证券买入    14.17     100      1417.00
    ...

    Return: CSV format string
    """
    goto_homepage()
    goal = (
        "在当前页面，点击底部'交易'标签，然后找到并点击'历史成交'。"
        "提取所有交易记录（向上滚动加载更多），每条包含："
        "名称、成交时间、成交价、成交量、买卖类型、成交金额。"
        "完成后返回。"
    )
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal)
    result = await agent.run()
    if not result.success or not result.reason:
        raise ValueError(f"❌ Goal get transactions not completed: {result.reason}")
    return get_format_output(result.reason.strip())
