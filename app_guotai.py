import os
import sys
import time
import asyncio
import dotenv
import sqlite3
import re
from datetime import datetime
from typing import Dict, List, Tuple, Optional

# Add the parent directory to Python path so we can import from utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from droidrun import DroidAgent, AdbTools
from gm_emulate_adb import get_device_connectivity, check_app_exist
from utils.gemini_thinking import create_gemini_with_thinking

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


async def droid_run(app_package_name: str | None, tools: AdbTools, goal: str | None):
    if not goal:
        raise ValueError("❌ Goal not set. Please provide a goal.")

    print(f'Get data from app({app_package_name}), 'f'Goal: {goal}')
    llm = create_gemini_with_thinking(
        model=GEMINI_MODEL,
        api_key=GOOGLE_API_KEY,
        thinking_budget=int(GEMINI_THINKING_BUDGET),
        temperature=0.1
    )

    agent = DroidAgent(
        goal=goal,
        llm=llm,
        tools=tools,
        vision=True,         # Set to True for vision models, False for text-only
        reasoning=True,      # Optional: enable planning/reasoning
        timeout=10000,
        max_steps=60,
        enable_tracing=False,  # Requires running 'phoenix serve' in a separate terminal first
    )

    try_num = 2
    while try_num > 0:
        try_num -= 1
        result = await agent.run()
        #Completely close this app instead of just leaving it in the background
        print(f"Force stopping app {app_package_name}...")
        os.system(f"adb shell am force-stop {app_package_name}")
        print(f"Success: {result['success']}")
        output = result.get('output', '')
        if not result['success'] or not output or len(output) < 100:
            continue
        print(f"Output:\n{output}")
        return output
    print("No output produced or output too shorti(try 2 times).")
    return None


def get_from_app_quote_page(tools: AdbTools, app_package_name: str = GUOTAI_PACKAGE_NAME):
    """
    Get real-time data from mobile guotai app quote page.

    Return:
    - All is CSV format: first line is header, then each line is one data.
    - First is index data on top page, Second is stock quote data table.
    Index Name,Index Number,Index Ratio
    Shanghai (沪),3882.78,+0.52%
    Shenzhen (深),13526.51,+0.35%
    Chi (创),3238.16,0.00%

    stock_name,code,latest_price,increase_percentage,increase_amount
    中科三环,000970,14.17,+1.21%,+0.17
    ...
    """
    goal = f"""
    Open '国泰海通君弘' app, then tap '我的', then tap '登录查看', then on the login page bypass account filed, just only password filed input '{GUOTAI_PASSWORD}', then tap '登录', then tap '行情', then tap '我的持仓'.
    On top page fetch below indexs data:
    shanghai index number                                 shenzhen index number                                chi index number
    shanghai index name(沪) shanghai index ratio(+0.52%)  shenzhen index name(深) shenzhen index ratio(+0.35%)  chi index name(沪) chi index ratio(+0.00%)
    Output all indexs in CSV format with header: Index Name, Index Number, Index Ratio.

    Below is a table of stock positions. Each line contains 4 columns of data: stock_name/code, latest_price, increase_percentage, increase_amount.
    Reveal current screen stock data, saved them to memory. then swipe up to reveal more stocks on the screen, more than 15 stocks will be abandoned, so you need swipe up only once or twice.
    Output all stocks in CSV format with header: stock_name,code,latest_price,increase_percentage,increase_amount.

    Last step, ** result output must be whole CSV format data(include indexs data, one blank row, all stocks data) in detail, no other such as summary text **.
    """
    return asyncio.run(droid_run(app_package_name=app_package_name, tools=tools, goal=goal))



def get_from_app_position_page(tools: AdbTools, app_package_name: str = GUOTAI_PACKAGE_NAME):
    """
    Get real-time data from mobile guotai app position page.

    Return:
    - All is CSV format: first line is header, then each line is one data.
    - First is summary data on top page, Second is stock position data table.
    Floating Profit/Loss,Account Assets,Market Cap,Positions,Available,Desirable
    -361757.86,855169.66,814839.00,95.28%,40330.66,40330.66

    stock_name,market_cap,open,available,current_price,cost,floating_profit,floating_loss(%)
    深振业Ａ,385875.000,37500,37500,10.290,13.361,-115165.77,-22.99%
    ...
    """
    goal = f"""
    Open '国泰海通君弘' app, then tap '我的', then tap '登录查看', then on the login page bypass account filed, just only password filed input '{GUOTAI_PASSWORD}', then tap '登录', then tap '交易', then tap '持仓'.
    First step, On top page fetch below summary data:
    浮动盈亏
    Floating Profit/Loss
    帐户资产                     总市值                   仓位
    Account Assets              Market Cap              Positions
    可用 Available               可取 Desirable
    Output all summarize values in CSV format with header: Floating Profit/Loss, Account Assets, Market Cap, Positions, Available, Desirable.

    Second step, Below is a table of stock positions. Each line contains 4 columns of data: stock_name/market_cap, open/available, current_price/cost, floating_profit/floating_loss(%)
    Reveal current screen stock data, saved them to memory. then swipe up to reveal more stocks on the screen, more than 15 stocks will be abandoned, so you need swipe up only once or twice.
    Output all stocks in CSV format with header: stock_name,market_cap,open,available,current_price,cost,floating_profit,floating_loss(%).

    Last step, ** result output must be whole CSV format data(include summary data, one blank row, all stocks data) in detail, no other such as summary text **.
    """
    return asyncio.run(droid_run(app_package_name=app_package_name, tools=tools, goal=goal))


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
    """Convert number string like '+0.17' or '-115165.77' to float.

    Args:
        num_str: Number string with or without + sign

    Returns:
        Float value
    """
    cleaned = num_str.replace('+', '').strip()
    try:
        return float(cleaned)
    except ValueError:
        return 0.0


def get_or_create_db_connection(db_path: Optional[str] = None) -> sqlite3.Connection:
    """Get SQLite database connection.

    Args:
        db_path: Path to database file. If None, uses default imobile.db

    Returns:
        SQLite connection object
    """
    if db_path is None:
        # Use the reflex default database
        db_path = os.path.join(os.path.dirname(__file__), 'imobile.db')

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def sync_app_data_to_db(quote_data: Optional[str] = None, position_data: Optional[str] = None, user_id: int = 1, db_path: Optional[str] = None) -> Dict:
    """Sync real-time data from mobile app to database.

    This function synchronizes CSV format data from get_from_app_quote_page() and
    get_from_app_position_page() with the SQLite database, ensuring the mobile app
    and database have the same data.

    Note: Automatically ignores head and tail blank lines in quote_data and position_data.

    Args:
        quote_data: CSV format string from get_from_app_quote_page()
                   Contains index data and stock quote data
                   Format: stock_name,code,latest_price,increase_percentage,increase_amount
        position_data: CSV format string from get_from_app_position_page()
                      Contains summary data and stock position data
        user_id: User ID for database records (default: 1)
        db_path: Path to database file (default: imobile.db)

    Returns:
        Dict with success status and message
    """
    conn = None
    try:
        conn = get_or_create_db_connection(db_path)
        cursor = conn.cursor()
        current_time = datetime.now().isoformat()

        result = {
            'success': True,
            'message': [],
            'indices_updated': 0,
            'stocks_updated': 0,
            'total_updated': False
        }

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
                            '上证指数': 'sh000001',
                            '深证成指': 'sz399001',
                            '创业板指': 'sz399006'
                        }
                        index_name = index_name_map.get(index_name.lower(), f'idx_{index_name.lower()}')
                        index_code = index_code_map.get(index_name.lower(), f'idx_{index_name.lower()}')

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
            # New format: stock_name,code,latest_price,increase_percentage,increase_amount
            if len(sections) >= 2:
                header, stock_rows = parse_csv_data(sections[1])
                for row in stock_rows:
                    if len(row) >= 5:
                        stock_name = row[0]
                        stock_code = row[1]
                        current_price = parse_number(row[2])
                        change_percent = parse_percentage(row[3])
                        change_amount = parse_number(row[4])

                        # First try to update by name (in case stock exists with different code format)
                        cursor.execute("""
                            UPDATE stocks_table
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
                                INSERT INTO stocks_table (user_id, code, name, current_price, change, change_percent, last_updated)
                                VALUES (?, ?, ?, ?, ?, ?, ?)
                                ON CONFLICT(user_id, code) DO UPDATE SET
                                    name = excluded.name,
                                    current_price = excluded.current_price,
                                    change = excluded.change,
                                    change_percent = excluded.change_percent,
                                    last_updated = excluded.last_updated
                            """, (user_id, stock_code, stock_name, current_price, change_amount, change_percent, current_time))

                        result['stocks_updated'] += 1

            result['message'].append(f"Updated {result['indices_updated']} indices and {result['stocks_updated']} stock quotes")

        # Process position page data (summary and stock positions)
        if position_data:
            sections = position_data.strip().split('\n\n')

            # Section 1: Portfolio summary
            if len(sections) >= 1:
                header, summary_rows = parse_csv_data(sections[0])
                if summary_rows and len(summary_rows[0]) >= 6:
                    row = summary_rows[0]
                    floating_pnl = parse_number(row[0])  # 浮动盈亏
                    account_assets = parse_number(row[1])  # 账户资产
                    market_cap = parse_number(row[2])  # 总市值
                    position_percent = parse_percentage(row[3])  # 仓位
                    available = parse_number(row[4])  # 可用
                    withdrawable = parse_number(row[5])  # 可取

                    # Calculate floating_pnl_percent
                    floating_pnl_percent = (floating_pnl / market_cap * 100) if market_cap > 0 else 0.0

                    # Upsert into total_table
                    cursor.execute("""
                        INSERT INTO total_table (
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

                        # Update existing stock by name
                        # Note: Position data doesn't have stock codes, so we can only update existing stocks
                        cursor.execute("""
                            UPDATE stocks_table
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

                        if cursor.rowcount > 0:
                            result['stocks_updated'] += 1

                result['message'].append(f"Updated {result.get('stocks_updated', 0)} stock positions from {len(position_rows)} records")

        conn.commit()
        result['message'] = ' | '.join(result['message'])
        return result

    except Exception as e:
        if conn:
            conn.rollback()
        return {
            'success': False,
            'message': f'Error saving data to database: {str(e)}',
            'error': str(e)
        }
    finally:
        if conn:
            conn.close()


def cron_sync_app_data_to_db():
    quote_data = get_from_app_quote_page(tools=tools)
    time.sleep(2)
    position_data = get_from_app_position_page(tools=tools)
    time.sleep(2)
    sync_app_data_to_db(quote_data, position_data, user_id=1, db_path='imobile.db')


if __name__ == "__main__":
    tools = get_device_connectivity()
    app_package_name = GUOTAI_PACKAGE_NAME
    check_app_exist(tools, app_package_name)

    #goal="Open Chrome and search for weather",  # Google network or check bot.
    #goal="Open Settings and check battery level, then go back to home screen"

    cron_sync_app_data_to_db()
