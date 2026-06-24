import os
import sys
import asyncio
import re
from datetime import datetime, timedelta
from typing import Dict, List, Tuple, Optional, Any
from loguru import logger
import unicodedata
from pydantic import BaseModel, Field

# Add the parent directory to Python path so we can import from shared/utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mobilerun import MobileAgent, MobileConfig, AndroidDriver
from llama_index.llms.google_genai import GoogleGenAI
from shared.db.db import DB
from backtest.utils.trading_calendar import calendar
from utils.trading_time import get_market_open_times_refresh_interval

# Import functions and requirements from trading.guotai
from trading.guotai import (
    pre_requirements,
    goto_homepage,
    replay_page,
    get_agent,
    parse_csv_data,
    parse_number,
    parse_percentage,
    normalize_stock_name,
    extract_stock_code,
    sync_index_quote_data_to_db,
    sync_summary_position_data_to_db,
    sync_order_data_to_db,
    login
)


# ==========================================
# 1. Pydantic Structured Output Models
# ==========================================

class IndexItem(BaseModel):
    name: str = Field(description="Name of the index (e.g. 上证指数, 上海 (沪), shanghai)")
    value: float = Field(description="Current numeric value of the index")
    change_percent: str = Field(description="Index change percentage string (e.g. +0.52%, -0.12%)")


class StockQuoteItem(BaseModel):
    name: str = Field(description="Name of the stock")
    code: str = Field(description="Stock code (e.g. 000970)")
    price: float = Field(description="Latest price of the stock")
    change_percent: str = Field(description="Stock price change percentage string (e.g. +1.21%)")
    change_amount: float = Field(description="Stock price change amount value")


class IndexStockQuoteData(BaseModel):
    indices: List[IndexItem] = Field(description="List of index quotes shown on the screen")
    stocks: List[StockQuoteItem] = Field(description="List of stock quotes visible in the quote list")


class AccountSummary(BaseModel):
    floating_pnl: float = Field(description="Account floating profit/loss amount (浮动盈亏)")
    account_assets: float = Field(description="Total account assets (帐户资产)")
    market_cap: float = Field(description="Total market value of stock holdings (总市值)")
    position_percent: str = Field(description="Position occupancy percentage (仓位), e.g. 95.28%")
    available: float = Field(description="Available cash balance (可用)")
    withdrawable: float = Field(description="Withdrawable cash amount (可取)")


class PositionItem(BaseModel):
    name: str = Field(description="Name of the stock holding")
    market_value: float = Field(description="Market value of the position (市值)")
    holdings: int = Field(description="Total quantity of shares held (持仓/数量)")
    available: int = Field(description="Available shares for selling (可用)")
    current_price: float = Field(description="Current price of the stock (现价)")
    cost: float = Field(description="Cost basis of the position (成本)")
    floating_pnl: float = Field(description="Floating profit/loss amount for the stock (浮动盈亏)")
    floating_pnl_percent: str = Field(description="Floating profit/loss percentage string (e.g. -22.99%)")


class SummaryPositionData(BaseModel):
    summary: AccountSummary = Field(description="Overall account and portfolio asset summary")
    positions: List[PositionItem] = Field(description="List of individual stock positions currently held")


class SmartOrderItem(BaseModel):
    name: str = Field(description="Stock name of the smart order")
    code: str = Field(description="Stock code of the smart order")
    trigger_condition: str = Field(description="Trigger condition of the order")
    buy_or_sell_price_type: str = Field(description="Execution price type (e.g. market_price, limit_price)")
    buy_or_sell_quantity: float = Field(description="Order quantity")
    valid_until: str = Field(description="Validity duration/date")
    order_number: str = Field(description="Unique order number or ID")
    reason_of_ending: str = Field(description="Order ending status/reason")


class SmartOrdersData(BaseModel):
    orders: List[SmartOrderItem] = Field(description="List of completed or active smart orders")


class TransactionItem(BaseModel):
    time: str = Field(description="Execution date/time string, format YYYY-MM-DD or YYYY-MM-DD HH:MM:SS")
    name: str = Field(description="Stock name involved in transaction")
    type: str = Field(description="Type of transaction (e.g. 证券买入, 证券卖出)")
    price: float = Field(description="Execution price")
    quantity: int = Field(description="Executed quantity")
    amount: float = Field(description="Total executed amount value")


class TransactionsData(BaseModel):
    transactions: List[TransactionItem] = Field(description="List of historical transaction records")


# ==========================================
# 2. Structured Extraction Wrappers
# ==========================================

async def get_index_stock_from_app_quote_page_structured(
    config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver
) -> str:
    """
    Get real-time index and stock data from mobile guotai app quote page using Pydantic structured output.
    """
    goto_homepage()
    replay_page(['行情', '我的持仓'])
    
    goal = """
    Extract the 3 indices (name, current value, change percentage) from the top of the screen,
    then keep scrolling down until you reach the very bottom of the stock list, extract ALL stocks from the stock list.
    """
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal, output_model=IndexStockQuoteData)
    result = await agent.run()
    if not result.success or not result.structured_output:
        raise ValueError(f"❌ Goal get index and stock structured failed: {result.reason}")
        
    data: IndexStockQuoteData = result.structured_output
    
    # Serialize back to CSV string required by sync_index_quote_data_to_db
    lines1 = ["index_name,index_number,index_ratio"]
    for item in data.indices:
        lines1.append(f"{item.name},{item.value},{item.change_percent}")
    
    lines2 = ["name,code,latest_price,increase_percentage,increase_amount"]
    for item in data.stocks:
        lines2.append(f"{item.name},{item.code},{item.price},{item.change_percent},{item.change_amount}")
        
    return "\n".join(lines1) + "\n\n" + "\n".join(lines2)


async def get_summary_position_from_app_position_page_structured(
    config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver
) -> str:
    """
    Get real-time summary and position data from mobile guotai app position page using Pydantic structured output.
    """
    goto_homepage()
    replay_page(['交易', '持仓'])
    
    goal = """
    Extract the account summary (floating profit/loss, account assets, market cap, position percentage, available cash, withdrawable cash) from the top of the screen,
    then scroll down 8-9 times until the end of the page, extract ALL stocks (floating_profit_loss, account_assets, market_cap, positions, available, desirable) from the stock list.
    """
    
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal, output_model=SummaryPositionData)
    result = await agent.run()
    if not result.success or not result.structured_output:
        raise ValueError(f"❌ Goal get summary and position structured failed: {result.reason}")
        
    data: SummaryPositionData = result.structured_output
    
    # Serialize back to CSV string required by sync_summary_position_data_to_db
    lines1 = ["floating_profit_loss,account_assets,market_cap,positions,available,desirable"]
    s = data.summary
    lines1.append(f"{s.floating_pnl},{s.account_assets},{s.market_cap},{s.position_percent},{s.available},{s.withdrawable}")
    
    lines2 = ["name,market_cap,open,available,current_price,cost,floating_profit,floating_loss_percentage"]
    for item in data.positions:
        lines2.append(f"{item.name},{item.market_value},{item.holdings},{item.available},{item.current_price},{item.cost},{item.floating_pnl},{item.floating_pnl_percent}")
        
    return "\n".join(lines1) + "\n\n" + "\n".join(lines2)


async def get_order_from_app_smart_order_page_structured(
    config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver
) -> str:
    """
    Get real-time smart order data from mobile guotai app smart order page using Pydantic structured output.
    """
    goto_homepage()
    replay_page(['智能订单', '查看详情'])
    
    goal = """
    Tap '已结束' tab.
    Extract the initial visible smart orders and store them in memory using remember().
    Loop scroll down the orders list. After each scroll, extract all newly visible smart orders and append them to the 'Extracted Orders' list in memory using remember().
    Continue scrolling and appending until no more new orders are loaded or '全部加载完成' is visible.
    Populate the final SmartOrdersData output model with the complete accumulated list of orders from memory.
    
    Important planning instruction: Ensure that all steps in your proposed `<plan>` tag are explicitly marked as completed, for example:
    <plan>
    1. Tap '已结束' tab, scroll and extract all smart orders. (Completed)
    </plan>
    Do not include any uncompleted steps in your plan when you call request_accomplished. Then immediately call `<request_accomplished success="true">` to complete the request.
    """
    
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal, output_model=SmartOrdersData)
    result = await agent.run()
    if not result.success or not result.structured_output:
        raise ValueError(f"❌ Goal get orders structured failed: {result.reason}")
        
    data: SmartOrdersData = result.structured_output
    
    # Serialize back to CSV string required by sync_order_data_to_db
    lines = ["name,code,trigger_condition,buy_or_sell_price_type,buy_or_sell_quantity,valid_until,order_number,reason_of_ending"]
    for item in data.orders:
        lines.append(f"{item.name},{item.code},{item.trigger_condition},{item.buy_or_sell_price_type},{item.buy_or_sell_quantity},{item.valid_until},{item.order_number},{item.reason_of_ending}")
    return "\n".join(lines)


async def get_transactions_from_app_history_page_structured(
    config: MobileConfig, llm: GoogleGenAI, tools: AndroidDriver
) -> str:
    """
    Get transaction history from mobile guotai app history page using Pydantic structured output.
    """
    goto_homepage()
    goal = """
    在当前页面，点击底部'交易'标签，然后找到并点击'历史成交'。
    使用 remember() 将初始可见的交易记录存在内存中。
    循环向下滚动交易记录列表。每次滚动后，提取所有新可见的交易记录，并使用 remember() 追加到内存中的“Extracted Transactions”列表中。
    持续滚动并追加，直到最底部（没有更多新记录加载）。
    用内存中累积的完整交易记录列表填充 TransactionsData 输出模型。
    
    Important planning instruction: Ensure that all steps in your proposed `<plan>` tag are explicitly marked as completed, for example:
    <plan>
    1. Scroll and extract all transaction history records. (Completed)
    </plan>
    Do not include any uncompleted steps in your plan when you call request_accomplished. Then immediately call `<request_accomplished success="true">` to complete the request.
    """
    
    agent = get_agent(config=config, llm=llm, tools=tools, goal=goal, output_model=TransactionsData)
    result = await agent.run()
    if not result.success or not result.structured_output:
        raise ValueError(f"❌ Goal get transactions structured failed: {result.reason}")
        
    data: TransactionsData = result.structured_output
    
    # Serialize back to CSV string
    lines = ["成交时间,名称,买卖类型,成交价,成交量,成交金额"]
    for item in data.transactions:
        lines.append(f"{item.time},{item.name},{item.type},{item.price},{item.quantity},{item.amount}")
    return "\n".join(lines)


# ==========================================
# 3. Database Helpers & Logic
# ==========================================

def get_stock_code_by_name(name: str, user_id: int = 1) -> str:
    """Query database to find stock code for a given stock name."""
    with DB.cursor() as cursor:
        # 1. Search in current holdings
        row = cursor.execute("SELECT code FROM holding_stocks WHERE user_id = ? AND name = ?", (user_id, name)).fetchone()
        if row:
            return row[0]
        # 2. Search in smart orders
        row = cursor.execute("SELECT code FROM smart_orders WHERE user_id = ? AND name = ?", (user_id, name)).fetchone()
        if row:
            return row[0]
        # 3. Search in existing transactions
        row = cursor.execute("SELECT code FROM transactions WHERE user_id = ? AND name = ?", (user_id, name)).fetchone()
        if row:
            return row[0]
    return ""


def sync_transactions_to_db(transactions_data: str, user_id: int = 1) -> dict:
    """
    Sync transaction history from mobile app to database.
    
    Args:
        transactions_data: CSV format string containing transaction records
        user_id: User ID for database records (default: 1)
        
    Returns:
        Dict with success status and message
    """
    if not transactions_data:
        return {'success': True, 'message': 'No transaction data to sync', 'added': 0}
        
    header, transaction_rows = parse_csv_data(transactions_data)
    added_count = 0
    
    with DB.cursor() as cursor:
        for row in transaction_rows:
            if len(row) < 6:
                continue
            tx_date = row[0].strip()
            name = normalize_stock_name(row[1])
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
                
            # Try to resolve code
            code = get_stock_code_by_name(name, user_id)
            if not code:
                # Attempt to extract code from name if format is 中科三环(000970)
                name_clean, ext_code = extract_stock_code(name)
                if ext_code:
                    code = ext_code
                    name = name_clean
                else:
                    code = "000000" # fallback if still unknown
                    
            # Check if this exact transaction already exists to avoid duplication
            cursor.execute("""
                SELECT id FROM transactions
                WHERE user_id = ? AND name = ? AND transaction_type = ? 
                  AND transaction_date = ? AND price = ? AND quantity = ? AND amount = ?
            """, (user_id, name, norm_type, tx_date, price, quantity, amount))
            
            if cursor.fetchone() is None:
                cursor.execute("""
                    INSERT INTO transactions (user_id, code, name, transaction_type, transaction_date, price, quantity, amount)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """, (user_id, code, name, norm_type, tx_date, price, quantity, amount))
                added_count += 1
                
    return {
        'success': True,
        'message': f"Synced transactions: {added_count} new transactions added",
        'added': added_count
    }


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

    logger.info("Starting cron job using structured Pydantic extraction...")
    tools, llm, config = await pre_requirements()
    
    # 1. Sync Index Quote Data
    logger.info("Extracting indices and stock quotes...")
    quote_csv = await get_index_stock_from_app_quote_page_structured(config=config, llm=llm, tools=tools)
    result_quote = sync_index_quote_data_to_db(quote_csv, user_id=1)
    if not result_quote.get('success'):
        raise ValueError(f"sync_index_quote_data_to_db failed: {result_quote}")
    logger.info(f"Synced indices & quotes: {result_quote}")
    exit(0)
    # 2. Sync Summary Position Data
    logger.info("Extracting account summary and positions...")
    position_csv = await get_summary_position_from_app_position_page_structured(config=config, llm=llm, tools=tools)
    result_position = sync_summary_position_data_to_db(position_csv, user_id=1)
    if not result_position.get('success'):
        raise ValueError(f"sync_summary_position_data_to_db failed: {result_position}")
    logger.info(f"Synced portfolio: {result_position}")

    # 3. Sync Smart Orders
    logger.info("Extracting smart orders...")
    order_csv = await get_order_from_app_smart_order_page_structured(config=config, llm=llm, tools=tools)
    result_order = sync_order_data_to_db(order_csv, user_id=1)
    if not result_order.get('success'):
        raise ValueError(f"sync_order_data_to_db failed: {result_order}")
    logger.info(f"Synced smart orders: {result_order}")

    # 4. Sync Transactions
    logger.info("Extracting transaction history...")
    tx_csv = await get_transactions_from_app_history_page_structured(config=config, llm=llm, tools=tools)
    result_tx = sync_transactions_to_db(tx_csv, user_id=1)
    if not result_tx.get('success'):
        raise ValueError(f"sync_transactions_to_db failed: {result_tx}")
    logger.info(f"Synced transactions: {result_tx}")

    result = {
        'quote_sync_result': result_quote,
        'position_sync_result': result_position,
        'order_sync_result': result_order,
        'transaction_sync_result': result_tx
    }
    logger.info("Cron job completed successfully.")
    return result


if __name__ == "__main__":
    login()
    asyncio.run(cron_sync_app_to_db(check_trading_day_and_time=False))
