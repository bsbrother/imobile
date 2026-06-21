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
import Levenshtein

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
        result = subprocess.run(f'mobilerun macro replay {matched_folder}', shell=True)
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


def get_format_output(tools: AndroidDriver, output:str, start_str:str = 'csv_format_name,', data_name:str = 'which data') -> str:
    """
    Get CSV format string from doridrun output to extract relevant data.

    output: 'I have successfully extracted all indices and stocks from the \'我的持仓\' page, formatted them into CSV format, and stored the result. Here is the final CSV data:\n\nIndex Name,Number,Ratio\n沪,3890.45,"+0.36%"\n深,
    """
    if not output:
        return output
    output = output.lower().strip()
    SPLIT_STR = ['data is:', 'data:', 'result:']
    for s in SPLIT_STR:
        if s in output:
            output = output.split(s)[-1].strip()
            break
    start_str = start_str.strip().lower()
    start = output.split(',', 1)[0].split(' ', 1)[-1].split('_', 1)[-1].strip().lower() + ','
    # 2026.06.15 compare nearly same xx%.
    if Levenshtein.distance(start_str, start) <= 3:
        return output
    if not output.startswith(start_str) and start != start_str:
        output_save = output
        # Regex to remove 'name,:' or 'Indices Data:' or 'Stocks Data:' lines.
        output = re.sub(r'.*name,:\s*', '', output, flags=re.IGNORECASE)
        output = re.sub(r'.*indices:\s*', '', output, flags=re.IGNORECASE)
        output = re.sub(r'.*stocks:\s*', '', output, flags=re.IGNORECASE)
        output = re.sub(r'.*summary:\s*', '', output, flags=re.IGNORECASE)
        output = re.sub(r'.*indices data:\s*', '', output, flags=re.IGNORECASE)
        output = re.sub(r'.*stocks data:\s*', '', output, flags=re.IGNORECASE)
        output = re.sub(r'.*summary data:\s*', '', output, flags=re.IGNORECASE)
        start = output.split(',', 1)[0].split(' ', 1)[-1].split('_', 1)[-1].strip().lower() + ','
        if Levenshtein.distance(start_str, start) <= 3:
            return output
        if not output.startswith(start_str) and start != start_str:
            raise ValueError(f"FORMAT ERROR: <{data_name}> should start with <{start_str}> ~= <{start}>:\n{output_save}")
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
    goal = """
    replay_page(['创建订单', '查看详情'])
    Extract all visible orders (name,code,trigger_condition,buy_or_sell_price_type,buy_or_sell_quantity,valid_until,order_number,reason_of_ending) from the list. Store the extracted orders in memory using remember().
    Continue scroll down the order list with resource id "com.guotai.dazhihui:id/table_view_body" to load more orders until '全部加载完成' visible.
    When the order list has been scrolled down, then extract all newly visible orders and append them to the 'Extracted Orders' in memory until '全部加载完成' visible.
    Format the last 'Extracted Orders' in memory into CSV format, and return the final CSV data in your response starting with 'result:'.
    """
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal)
    result = await agent.run()
    if not result.success:
        raise ValueError(f"❌ Goal get orders not completed: {result.reason}")
    output = get_format_output(tools, result.reason, 'name,', 'orders data')
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
    Extract the 3 indices (name, number, ratio) from the top of the screen and all visible stocks (name, code, latest price, increase percentage, increase amount) from the list. Store both sets of data in memory using remember().
    Continue scroll down to got more stocks in list, then extract all newly visible stocks and append them to the 'Extracted Stocks' in memory until no more new stocks.
    Format the last 'Extracted Indices' and 'Extracted Stocks' in memory into CSV format, combine them with two new lines separator, and return the final CSV data in your response starting with 'result:'.
    """
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal)
    result = await agent.run()
    if not result.success:
        raise ValueError(f"❌ Goal get index and stock not completed: {result.reason}")
    output = get_format_output(tools, result.reason, 'name,', 'quote data')
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
    Extract the 1 account summary (floating_profit_loss, account_assets, market_cap, positions, available, desirable) from the top of the screen
    and all visible stocks (name, market_cap, open, available, current_price, cost, floating_profit, floating_loss_percentage) from the list. Store both sets of data in memory using remember().
    Continue scroll down to got more stocks in list, then extract all newly visible stocks and append them to the 'Extracted Stocks' in memory until no more new stocks.
    Format the last 'Extracted Summary' and 'Extracted Stocks' in memory into CSV format, combine them with two new lines separator, and return the final CSV data in your response starting with 'result:'.
    """
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal)
    result = await agent.run()
    if not result.success:
        raise ValueError(f"❌ Goal get summary and position not completed: {result.reason}")
    output = get_format_output(tools, result.reason, 'profit_loss,', 'position data')
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
            if len(sections) >= 2:
                header, stock_rows = parse_csv_data(sections[1])
                for row in stock_rows:
                    if len(row) >= 5:
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
    if not result.success:
        raise ValueError(f"❌ Goal get transactions not completed: {result.reason}")
    output = get_format_output(tools, result.reason, 'name,', 'transactions data')
    return output


async def cron_sync_app_to_db(check_trading_day_and_time: bool = True) -> dict:
    """Cron job to sync app data to database.

    $ crontab -l # m h  dom mon dow   command
    # Runs every 30 minutes on weekdays (Mon-Fri) between market open hours(9:30-11:30,13:00-15:00), 11:30-13:00 or after market close + 1 hour(16:00) not included.
    30 9   * * 1-5 export PYENV_ROOT=$HOME/.pyenv/; export PATH=$PYENV_ROOT/bin:$PATH/; cd $HOME/apps/imobile; source venv/bin/activate; nohup $HOME/apps/imobile/trading/guotai.py >> /tmp/cron_guotai_sync.log 2>&1 &
    0,30 10-11 * * 1-5 export PYENV_ROOT=$HOME/.pyenv/; export PATH=$PYENV_ROOT/bin:$PATH/; cd $HOME/apps/imobile; source venv/bin/activate; nohup $HOME/apps/imobile/trading/guotai.py >> /tmp/cron_guotai_sync.log 2>&1 &
    0,30 13-14 * * 1-5 export PYENV_ROOT=$HOME/.pyenv/; export PATH=$PYENV_ROOT/bin:$PATH/; cd $HOME/apps/imobile; source venv/bin/activate; nohup $HOME/apps/imobile/trading/guotai.py >> /tmp/cron_guotai_sync.log 2>&1 &
    0,30 15  * * 1-5 export PYENV_ROOT=$HOME/.pyenv/; export PATH=$PYENV_ROOT/bin:$PATH/; cd $HOME/apps/imobile; source venv/bin/activate; nohup $HOME/apps/imobile/trading/guotai.py >> /tmp/cron_guotai_sync.log 2>&1 &
    0    16  * * 1-5 export PYENV_ROOT=$HOME/.pyenv/; export PATH=$PYENV_ROOT/bin:$PATH/; cd $HOME/apps/imobile; source venv/bin/activate; nohup $HOME/apps/imobile/trading/guotai.py >> /tmp/cron_guotai_sync.log 2>&1 &
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
    tools, llm, config = await pre_requirements()
    logger.info("Navigation complete, starting data extraction...")

    # 1. Sync Index Quote Data
    quote_data = await get_index_stock_from_app_quote_page(config=config, llm=llm, tools=tools)
    result_quote = sync_index_quote_data_to_db(quote_data, user_id=1)
    if not result_quote.get('success'):
        logger.error(f'Error in sync_index_quote_data_to_db: {result_quote}')
        raise ValueError(f"sync_index_quote_data_to_db failed: {result_quote.get('message') or result_quote.get('error')}")
    logger.info(f'sync_index_quote_data_to_db done, result: {result_quote}')

    # 2. Sync Summary Position Data
    position_data = await get_summary_position_from_app_position_page(config=config, llm=llm, tools=tools)
    result_position = sync_summary_position_data_to_db(position_data, user_id=1)
    if not result_position.get('success'):
        logger.error(f'Error in sync_summary_position_data_to_db: {result_position}')
        raise ValueError(f"sync_summary_position_data_to_db failed: {result_position.get('message') or result_position.get('error')}")
    logger.info(f'sync_summary_position_data_to_db done, result: {result_position}')

    result_order = {}

    result = {
        'quote_sync_result': result_quote,
        'position_sync_result': result_position,
        'order_sync_result': result_order
    }
    logger.info("Cron job to sync app data to database completed.\nResult: {}".format(result))
    return result


if __name__ == "__main__":
    """
    close_app()
    open_app()
    restart_app()
    goto_homepage()
    login()

    if verify_screen_contains(["行情", "交易", "我的"]):
        logger.info('Bottom Navigation bar is visiable(maybe at homepage).')
    else:
        logger.warning('Bottom Navigation bar is NOT visiable.')
    """
    login()
    asyncio.run(cron_sync_app_to_db(check_trading_day_and_time=False))

"""
# 2026.6.21
# Because on-screen keyboard input problemss cause not easy to create relate replay templates.
# Replace by trading/order_xx.py.

async def add_order_by_replay_template(app_extractor: AppDataExtractor, order: SmartOrder = None, buy_or_sell: str = "buy"):
    '''
    Add a smart order to the app using replay template.
    1. Read template macro
    2. Replace Code, Price, Quantity with order values
       (Dynamically generate keypad taps for Price/Quantity)
    3. Save to temp and replay
    '''
    if not order:
        raise ValueError("â Order not provided.")
    if buy_or_sell == "buy":
        ORDER_TEMPLATE = 'trajectories/order_buy/macro.json'
    elif buy_or_sell == "sell":
        ORDER_TEMPLATE = 'trajectories/order_sell/macro.json'
    else:
        raise ValueError("â Invalid buy_or_sell value: {buy_or_sell}")

    logger.info(f"   Creating {buy_or_sell} order: {order}")
    if not os.path.exists(ORDER_TEMPLATE):
        raise ValueError(f"â Order template not found: {ORDER_TEMPLATE}")

    # Extract order details
    # SmartOrder: code, trigger_condition (contains price), buy_or_sell_quantity
    stock_code = order.code
    qty = str(int(order.buy_or_sell_quantity))

    # Parse Price from trigger_condition
    # Format: "è¡ä»·>=12.30å(è§¦åä¹°å¥)" or "è¡ä»·>=12.30å(è§¦åæ­¢ç),..."
    # We need the trigger price.
    import re
    price = profit_price = lose_price = "0"
    if 'è§¦åä¹°å¥' in order.trigger_condition:
        # Buy order
        m = re.search(r'è¡ä»·>=([\d\.]+)å', order.trigger_condition)
        if m: price = m.group(1)
    elif 'è§¦åæ­¢ç' in order.trigger_condition and 'è§¦åæ­¢æ' in order.trigger_condition:
        #profit_price = order.trigger_condition.split(',')[0].split('>=')[1].replace('å(è§¦åæ­¢ç)', '')
        #lose_price = order.trigger_condition.split(',')[1].split('<=')[1].replace('å(è§¦åæ­¢æ)', '')
        m = re.search(r'è¡ä»·>=([\d\.]+)å', order.trigger_condition)
        if m: profit_price = m.group(1)
        m = re.search(r'è¡ä»·<=([\d\.]+)å', order.trigger_condition)
        if m: lose_price = m.group(1)
    else:
        raise ValueError(f"â Invalid trigger condition: {order.trigger_condition}")

    # Load template
    with open(ORDER_TEMPLATE, 'r') as f:
        macro_data = json.load(f)

    actions = macro_data.get('actions', [])
    new_actions = []

    # Helper to find keypad coordinates from known mapping
    # Based on trajectories/keypad_num_button_mapping.md
    keypad_map = {
        '1': {'x': 179, 'y': 2295, 'element_index': 87, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '1'},
        '2': {'x': 539, 'y': 2295, 'element_index': 91, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '2'},
        '3': {'x': 900, 'y': 2295, 'element_index': 95, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '3'},
        '4': {'x': 179, 'y': 2487, 'element_index': 88, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '4'},
        '5': {'x': 539, 'y': 2487, 'element_index': 92, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '5'},
        '6': {'x': 900, 'y': 2487, 'element_index': 96, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '6'},
        '7': {'x': 179, 'y': 2679, 'element_index': 89, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '7'},
        '8': {'x': 539, 'y': 2679, 'element_index': 93, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '8'},
        '9': {'x': 900, 'y': 2679, 'element_index': 97, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '9'},
        '0': {'x': 539, 'y': 2870, 'element_index': 94, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '0'},
        '.': {'x': 900, 'y': 2870, 'element_index': 98, 'type': 'TapActionEvent', 'action_type': 'tap', 'element_text': '.'},
    }

    iterator = iter(actions)
    digit_group_count = 0
    skipping_digits = False

    while True:
        try:
            action = next(iterator)
        except StopIteration:
            break

        # 1. Replace Code Input
        if action.get('type') == 'InputTextActionEvent':
            action['text'] = stock_code
            action['description'] = f"Input text: '{stock_code}'"
            new_actions.append(action)
            skipping_digits = False # reset just in case
            continue

        # 2. Check for Digit Event (Price/Qty)
        # Check against string representations of digits
        txt = str(action.get('element_text', ''))
        is_digit = action.get('type') == 'TapActionEvent' and txt in ['0','1','2','3','4','5','6','7','8','9','.']

        if is_digit:
            if skipping_digits:
                continue # Skip old digits in current group
            else:
                # Start of a NEW digit group
                digit_group_count += 1
                skipping_digits = True # Start skipping subsequent old digits

                # Determine value to insert based on order type
                # BUY template: 2 digit groups (Price, Quantity)
                # SELL template: 3 digit groups (TP Price, SL Price, Quantity)
                val_to_insert = None
                field_name = "Unknown"

                if buy_or_sell == "buy":
                    if digit_group_count == 1:
                        val_to_insert = price
                        field_name = "Price"
                    elif digit_group_count == 2:
                        val_to_insert = qty
                        field_name = "Quantity"
                elif buy_or_sell == "sell":
                    if digit_group_count == 1:
                        val_to_insert = profit_price
                        field_name = "TP Price"
                    elif digit_group_count == 2:
                        val_to_insert = lose_price
                        field_name = "SL Price"
                    elif digit_group_count == 3:
                        val_to_insert = qty
                        field_name = "Quantity"

                if val_to_insert is None:
                    logger.warning(f"â ï¸ Found unexpected digit group #{digit_group_count}. Keeping original.")
                    new_actions.append(action)
                    skipping_digits = False
                    continue

                logger.info(f"   Replacing Group #{digit_group_count} ({field_name}) with {val_to_insert}")

                # Insert new sequence
                for char in str(val_to_insert):
                    if char in keypad_map:
                        tap = keypad_map[char].copy()
                        tap['description'] = f"Tap element '{char}' for {field_name}"
                        new_actions.append(tap)
                        '''
                        # Add wait
                        new_actions.append({
                            "type": "WaitEvent",
                            "action_type": "wait",
                            "description": "Wait 0.2s",
                            "duration": 0.2
                        })
                        '''
                    else:
                        logger.warning(f"âš ï¸  Digit '{char}' not found in keypad map. Skipping.")

        elif action.get('type') == 'WaitEvent':
            if skipping_digits:
                continue # Skip waits inside the old digit sequence
            else:
                new_actions.append(action)

        else:
            # Non-digit, non-wait action (e.g. Tap Confirm, Swipe, Tap Box)
            # This marks the end of a digit skipping sequence
            skipping_digits = False
            new_actions.append(action)

    macro_data['actions'] = new_actions

    # Save to temp
    temp_dir = f"trajectories/temp_order_{int(random.random()*10000)}"
    os.makedirs(temp_dir, exist_ok=True)
    temp_file = os.path.join(temp_dir, 'macro.json')
    with open(temp_file, 'w') as f:
        json.dump(macro_data, f, indent=2)
    if buy_or_sell == "buy":
        logger.info(f"   ð Replaying BUY macro for {stock_code} Price={price} Qty={qty}...")
    else:
        logger.info(f"   ð Replaying SELL macro for {stock_code} TP={profit_price} SL={lose_price} Qty={qty}...")

    try:
        subprocess.run(f"droidrun macro replay {temp_dir} --delay 6", shell=True)
        time.sleep(random.randint(8, 15)) # random 6-12 seconds
        add_ok = await check_result_by_screenshot("ç»æè¯¦æ")
        if not add_ok:
            import pdb;pdb.set_trace()
            #raise Exception("â Add order failed")
        # Exit order page, go back to homepage
        subprocess.run("adb shell input keyevent KEYCODE_BACK", shell=True)
        subprocess.run("adb shell input keyevent KEYCODE_BACK", shell=True)
        time.sleep(2)
    except Exception as e:
        raise Exception(f"â Error replaying macro: {e}")
    finally:
        # Cleanup
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)

async def _order_crud(self, orders: list) -> bool:
    '''Create, update or stop smart orders in Guotai app.

    Args:
        orders: Smart orders to create, update or stop

    Returns:
        bool: True if operations completed successfully
    '''
    self.config.agent.max_steps = 25
    for order in orders:
        if isinstance(order, dict):
            code, name, trigger, commission_method, qty, valid = [order[k] for k in order]
        else:
            code, name, trigger, commission_method, qty, valid = order
        code = code.split('.')[0]
        if 'è§¦åä¹°å¥' in trigger or 'è§¦åååº' in trigger:
            price = float(trigger.split('>=')[1].split('å')[0])
            if 'è§¦åä¹°å¥' in trigger:
                order_type = "å°ä»·ä¹°å¥"
            else:
                order_type = "å°ä»·ååº"
            goal = f'''If not on 'æºè½è®¢å' page, tap system BACK until 'äº¤æ' visible on the bottom navigation bar, then tap 'äº¤æ', then tap 'æºè½è®¢å'.
1. Tap "{order_type}", waiting util "{order_type}" page show. Tap on the text box with "è¯·è¾å¥è¡ç¥¨ä»£ç æåç§°", waiting util "éæ©è¡ç¥¨" page show. Tap on the text box with "è¯·è¾å¥è¡ç¥¨ä»£ç æåç§°", type "{code}" into the focused text field, wait 6 seconds then tap on "æç´¢ç»æ" below line which has "{code}", wait 6 seconds.
2. Tap "è¾å¥è§¦åä»·æ ¼" 2 times, with a 2-second interval each time, then tap on the text box "è¾å¥è§¦åä»·æ ¼". Waiting until the keypad visiable, enter the "{price}" by tapping the corresponding buttons on the keypad and then tap "ç¡®å®".
3. Tap on the radio button with text "å½è¡ä»·â¥ {price} è§¦åå§æ".
4. Tap on the "è¯·éæ©å§ææ¹å¼". Not need select anything, just swipe((1321, 1985), (1321, 1985)) in short duration, then tap index 92.
5. Repeat previous step until the "å§ææ¹å¼" field got value, then continue next step.
6. Swipe up until "æææè³" visiable. If "è¯·è¾å¥ [ä¹°å¥/ååº] æ°é" pop-up then tap "ç¡®å®" to close it.
7. Tap "ä¹°å¥æ°é", 2 times, with a 2-second interval each time, then tap on the text box "ä¹°å¥æ°é". Waiting until the keypad visiable, remove older value and enter the "{qty}" by tapping the corresponding buttons on the keypad and then tap "ç¡®å®".
8. Tap "è¯·éæ©ä¸åæ¹å¼" right side the radio button with text "èªå¨ä¸å".
9. Tap "æææè³" below text box, then tap the date "{valid[-2:]}", then swipe((1321, 1156), (1321, 1156)) in short duration, then tap index 93,
10. Repeat previous step unitl the "æææè³" field go value, then continue next step.
11. Tap "åå»ºè®¢å", then tap on the "ç¡®å®".
        '''
        elif 'æ­¢çæ­¢æ' in trigger:
            tp_price = float(trigger.split('è§¦å')[1].split('å')[0])
            ls_price = float(trigger.split('è§¦å')[1].split('å')[0])
            order_type = "æ­¢çæ­¢æ"
            goal = f'''
        '''
        else:
            raise ValueError(f"â Invalid trigger: {trigger}")
        agent = get_agent(config=self.config, llm=self.llm, driver=self.driver, goal=goal)
        try_num = 0
        while try_num < 3:
            result = await agent.run(save_trajectory='none')
            if result.success:
                break
            try_num += 1
        if not result.success:
            raise ValueError(f"â try {try_num} times, but Goal get orders not completed: {result.reason}")
        logger.info(f"   â Order {code} ({name}) trigger={trigger} qty={qty} until={valid} added successfully")
        break

    return True

@staticmethod
async def status_order_in_app(order: SmartOrder) -> str:
    '''Check the status of the order in the app.

    Args:
        order: Smart order to check

    Returns:
        str: The status of the order in the app
            - 'need_add': order not exist in app
            - 'need_update': order is running in app but trigger or quantity not match
            - 'need_stop': order is running in app but not in orders
    '''
    return True

async def stop_order_in_app(self, order_code: str, order_name: str) -> bool:
    '''Stop (cancel) a running smart order in the Guotai app.

    Navigate to smart order list â find order by code â tap stop.
    The app's smart order page has 'è¿è¡ä¸­' tab listing active orders.
    Each order card has a 'åæ­¢' (stop) button.

    Args:
        order_code: Stock code (e.g., '002415')
        order_name: Stock name for logging

    Returns:
        bool: True if order was stopped successfully
    '''
    code = order_code.split('.')[0]
    logger.info(f"   ð Stopping order {code} ({order_name}) in app...")

    goal = f'''If not on 'æºè½è®¢å' page, tap system BACK until 'äº¤æ' visible on the bottom navigation bar, then tap 'äº¤æ', then tap 'æºè½è®¢å'.
1. In the smart order page, tap 'è¿è¡ä¸­' tab to see running orders.
2. Find the order card that contains stock code "{code}" or name "{order_name}".
3. Tap the 'åæ­¢' button on that order card.
4. If a confirmation dialog appears, tap 'ç¡®å®' to confirm stopping the order.
5. Verify the order status changed (no longer in 'è¿è¡ä¸­' list or shows 'å·²åæ­¢').
    '''
    self.config.agent.max_steps = 25
    agent = get_agent(config=self.config, llm=self.llm, driver=self.driver, goal=goal)

    try_num = 0
    while try_num < 3:
        result = await agent.run(save_trajectory='none')
        if result.success:
            logger.info(f"   â Order {code} ({order_name}) stopped successfully")
            return True
        try_num += 1
        logger.warning(f"   â ï¸ Attempt {try_num}/3 to stop order {code} failed: {result.reason}")

    logger.error(f"   â Failed to stop order {code} ({order_name}) after 3 attempts")
    return False
"""
