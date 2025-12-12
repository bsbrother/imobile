"""
Backtest Trading Script on A-Shares Market with T+1 Compliance,
pick stocks from strong sectors, create smart orders, execute orders, and generate reports.

Architecture Flow:
pick_orders_trading()
├── pick_stocks_to_file()                    # Pick stocks from hot sectors
├── create_smart_orders_from_picks()         # Create/adjust smart orders
│   ├── Add new orders
│   ├── Adjust existing orders
│   └── Force-sell: expires orders when this_date >= valid_until
├── OrderAnalyzer.generate_daily_report()
│   └── check_order_execution()              # Execute buy/sell based on triggers
│       ├── execute_buy_order()              # Write to DB
│       └── execute_sell_order()             # Write to DB
└── OrderAnalyzer.generate_period_report()   # READ ONLY - no trading
    ├── Read from transactions table
    ├── Read from holding_stocks table
    └── Calculate P&L

Usage:
python this_script [start_date end_date], date format: YYYYMMDD, default is today.

TODO
- Monitor execution performance
"""

import os
import sys
import json
import pandas as pd
from datetime import datetime
import time
from typing import Dict, List, Any, Optional
from loguru import logger
from dotenv import load_dotenv

from backtest import data_provider
from backtest.utils.trading_calendar import calendar, convert_trade_date
from backtest.utils.logging_config import configure_logger
from backtest.utils.config import ConfigManager
from backtest.utils.util import convert_to_datetime
from backtest.utils.market_regime import detect_market_regime
from backtest.utils.trailing_stop import calculate_trailing_stop
# Add the parent directory to Python path so we can import from utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from db.db import DBTEST as DB

load_dotenv()
# 1. Load configures from .env, $BACKTEST_PATH/config.json, e.g. REPORT_PATH, initial cash, strategy parameters etc.
CONFIG_FILE = os.getenv("CONFIG_FILE", default="/backtest/config.json")
BACKTEST_PATH = os.getenv('BACKTEST_PATH', './backtest')
REPORT_PATH = os.getenv('REPORT_DIR', os.path.join(BACKTEST_PATH, 'backtest_results'))
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)
global_cm = ConfigManager(config_file=CONFIG_FILE)
MAX_POSITIONS = global_cm.get('trading_rules.position_sizing.max_positions', 10)
INITIAL_CASH = global_cm.get('portfolio_config.initial_cash', 600000)
COMMISSION = global_cm.get('portfolio_config.commission', 0.0000341)  # 10W * 0.000341% = 3.41 # Max 5 yuan
TAX = global_cm.get('portfolio_config.tax', 0.0005)  # 10W * 0.005% = 50 # Only on sell

# Check report path exist, if not,then create it
if not os.path.exists(REPORT_PATH):
    os.makedirs(REPORT_PATH)

def pick_stocks_to_file(this_date: str, src: str = 'ts_ths') -> str:
    """
    Pick stocks and save to a file for a specific date.

    Arguments:
    this_date -- The trading date to pick stocks for (format: YYYYMMDD)

    Returns:
    str -- Path to the output file
    """
    strong_stocks = {}
    logger.info(f"Picking stocks for {this_date} ...")
    pick_output_file = os.path.join(REPORT_PATH, f'pick_stocks_{this_date}.json')
    
    # Detect market regime
    regime_data = detect_market_regime(this_date)
    regime_name = regime_data['regime']
    logger.info(f"Market Regime for {this_date}: {regime_name}")

    """
    # market_pattern picker
    result = os.system(f'python -m backtest.cli pick --date {this_date} -o /tmp/tmp')
    if result != 0:
        raise ValueError(f"Failed to pick stocks for {this_date}.")
    """
    # hot sectors picker
    # Pass regime info to picker if needed, or just let it pick based on sectors
    # For now, we assume picker logic is independent or we will update it later.
    if src == 'ts_combine':
        result = os.system(f'python pick_stocks_from_sector/ts_combine.py {this_date}')
    elif src == 'ts_go':
        # Compile and run the Go stock picker
        # Command: cd utils/go-stock; go build -o pick_stocks cmd/pick_stocks/main.go; ./pick_stocks {this_date}
        cmd = f'cd utils/go-stock && go build -o pick_stocks cmd/pick_stocks/main.go && ./pick_stocks -date {this_date}'
        logger.info(f"Running Go stock picker: {cmd}")
        result = os.system(cmd)
    else:
        result = os.system(f'python pick_stocks_from_sector/ts_ths_dc.py {this_date} {src}')
    if result != 0:
        raise ValueError(f"Failed to pick strong stocks from hot sectors for {this_date} using {src}.")
    with open('/tmp/tmp', 'r') as f:
        strong_stocks = json.load(f)
    data = {
        'pick_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'base_date': calendar.get_trading_days_before(this_date, 1),
        'target_trading_date': this_date,
        'market_pattern': regime_name,
        'regime_data': regime_data,
        'selected_stocks': strong_stocks['selected_stocks'][:MAX_POSITIONS]
    }
    with open(pick_output_file, 'w') as f:
        json.dump(data, f)

    logger.info(f"Picked {MAX_POSITIONS} stocks, saved to {pick_output_file}")
    return pick_output_file


def create_smart_orders_from_picks(pick_input_file: str, user_id: int = 1) -> str:
    """
    Create smart orders based on picked stocks for a specific date.
    # TODO:
    # - not batch sell, avoid exist multi same symbol orders.

    Arguments:
    pick_input_file -- The JSON file containing picked stocks
    user_id -- The user ID for whom to create smart orders

    Returns:
    str -- Path to the output file
    """
    this_date = os.path.basename(pick_input_file).split('_')[-1].replace('.json', '')
    smart_output_file = os.path.join(REPORT_PATH, f'smart_orders_{this_date}.json')
    logger.info(f"Creating smart orders from {pick_input_file}...")
    result = os.system(f'python -m backtest.cli analyze --stocks-file {pick_input_file} -o {smart_output_file}')
    if result != 0:
        raise ValueError(f"Failed to create smart orders from {pick_input_file}.")
    logger.info(f"Created smart orders, saved to {smart_output_file}")

    running_orders = data = {}
    no_buy_cancel_symbols = []
    with open(smart_output_file, 'r') as f:
        data = json.load(f)
    
    # Get holding days from regime data if available, else default to 4
    regime_data = data.get('regime_data', {})
    holding_days = regime_data.get('max_hold_days', 4)

    with DB.cursor() as cursor:
        cursor.execute("""
            SELECT id, code, trigger_condition, valid_until, buy_or_sell_quantity, name, last_updated
            FROM smart_orders
            WHERE status='running' AND user_id=?
        """, (user_id,))
        for order in cursor.fetchall():
            # the order is buy order.
            if '触发买入' in order[2]:
                # Cancel if expired OR if it's a stale buy order from previous days.
                # Relaxed: Allow buy orders to persist for 2 days to avoid churning.
                last_updated = datetime.strptime(order[6], '%Y-%m-%d %H:%M:%S') if isinstance(order[6], str) else order[6]
                days_since_update = (datetime.strptime(this_date, '%Y%m%d') - last_updated).days
                
                if this_date >= order[3] or days_since_update >= 2: 
                    no_buy_cancel_symbols.append(order[1])
                    cursor.execute("""
                        UPDATE smart_orders
                        SET status='cancelled',
                            reason_of_ending='order_expired_or_stale',
                            last_updated=?
                        WHERE id=? AND user_id=?
                    """, (convert_to_datetime(this_date), order[0], user_id))
                    continue
            running_orders[order[1]] = {
                'id': order[0],
                'trigger_condition': order[2],
                'valid_until': order[3],
                'buy_or_sell_quantity': order[4],
                'name': order[5]
            }

        added_orders = adjusted_orders = prev_orders= 0
        logger.info(f"Current running orders: {len(running_orders)}/{MAX_POSITIONS}")
        for order in data['smart_orders']:
            # Append to smart_orders table in db/imobile.db
            if order['symbol'] not in running_orders:
                if added_orders < (MAX_POSITIONS - len(running_orders)):
                    order_number = f"ORD_{this_date}_{order['symbol']}_{user_id}"
                    trigger_condition = f'股价<={order["buy_price"]}元(触发买入)'
                    valid_until = calendar.get_trading_days_after(this_date, holding_days)
                    cursor.execute("""
                        INSERT INTO smart_orders (user_id, code, name, trigger_condition,
                        buy_or_sell_price_type, buy_or_sell_quantity, order_number,
                        status, valid_until, reason_of_ending, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (
                        user_id, order['symbol'], order['name'], trigger_condition,
                        '即时买一价', order['buy_quantity'], order_number,
                        'running', valid_until, '', convert_to_datetime(this_date)
                    ))
                    added_orders += 1
                else:
                    # find by symbol and pop to remove it from data['smart_orders'] if not added.
                    data['smart_orders'] = [o for o in data['smart_orders'] if o['symbol'] != order['symbol']]
                continue

            adjusted_orders += 1
            # If this stock is selected for more than 2 consecutive days,
            # it indicates sustained popularity. Accordingly:
            id = running_orders[order['symbol']]['id']
            trigger_condition = running_orders[order['symbol']]['trigger_condition']
            valid_until = running_orders[order['symbol']]['valid_until']
            buy_quantity = int(running_orders[order['symbol']]['buy_or_sell_quantity'])

            if '触发买入' in trigger_condition:
                # the order is buy order, adjust the buy price lower, keep the same buy quantity.
                buy_price = trigger_condition.split('<=')[1].split('元(触发买入)')[0]
                buy_price = min(float(buy_price), order['buy_price'])
                trigger_condition = f'股价<={buy_price:.2f}元(触发买入)'
                order['buy_quantity'] = buy_quantity # keep original buy quantity for it not buy filled yet.
                order['buy_price'] = round(buy_price, 2)
                cursor.execute("""
                    UPDATE smart_orders
                    SET buy_or_sell_quantity=?, trigger_condition=?, last_updated=?
                    WHERE id=? AND user_id=?
                """, (order['buy_quantity'], trigger_condition, convert_to_datetime(this_date), id, user_id))
            else:
                # increase the take-profit and stop-loss values proportionally, keep the same sell quantity.
                if '触发止盈' not in trigger_condition or '触发止损' not in trigger_condition:
                    # This might happen if we have a different order type, but for now we assume standard TP/SL
                    # If it's a simple "sell at market" or similar, we might want to just update valid_until
                    if '股价>=' in trigger_condition and '触发止盈' not in trigger_condition:
                         # Handle cases where it might be a simple limit sell or force sell from previous day
                         pass 
                    else:
                         # raise ValueError(f"Invalid trigger_condition format for order id {id}: {trigger_condition}")
                         pass

                # Extend holding period if picked again
                new_valid_until = calendar.get_trading_days_after(this_date, holding_days)
                if new_valid_until > valid_until:
                    cursor.execute("""
                        UPDATE smart_orders
                        SET valid_until=?, last_updated=?
                        WHERE id=? AND user_id=?
                    """, (new_valid_until, convert_to_datetime(this_date), id, user_id))
                    order['valid_until'] = new_valid_until

                # Update TP/SL if applicable
                if '触发止盈' in trigger_condition and '触发止损' in trigger_condition:
                    profit_price = trigger_condition.split(',')[0].split('>=')[1].replace('元(触发止盈)', '')
                    lose_price = trigger_condition.split(',')[1].split('<=')[1].replace('元(触发止损)', '')
                    
                    # Increase TP by 10% (let winners run more)
                    profit_price = round(float(profit_price) * 1.10, 2)
                    # Do NOT raise SL blindly to avoid noise-out. Keep original SL or raise very slightly.
                    # lose_price = round(float(lose_price) * 1.02, 2) 
                    # For now, keep SL same to give room for volatility.
                    lose_price = float(lose_price)

                    # the order is about to expire today, force sell at market price next day.
                    reason_of_ending = ''
                    # Check against NEW valid_until
                    current_valid_until = new_valid_until if new_valid_until > valid_until else valid_until
                    
                    if this_date >= current_valid_until:
                        # - 智能订单:限价委托/市价委托, buy/sell default 限价委托:'即时买一价'
                        # TODO: maybe need to change buy_or_sell_price_type to '市价委托' here.
                        reason_of_ending = 'order_expired_before_sell'
                        lose_price = order['current_price'] * 0.90
                        trigger_condition = f'股价>={lose_price:.2f}元' # force sell now.
                    else:
                        trigger_condition = f'股价>={profit_price:.2f}元(触发止盈),股价<={lose_price:.2f}元(触发止损)'
                    
                    cursor.execute("""
                        UPDATE smart_orders
                        SET trigger_condition=?, reason_of_ending=?, last_updated=?
                        WHERE id=? AND user_id=?
                    """, (trigger_condition, reason_of_ending, convert_to_datetime(this_date), id, user_id))
                    order['sell_take_profit_price'] = round(profit_price, 2)
                    order['sell_stop_loss_price'] = round(lose_price, 2)
                
                order['buy_quantity'] = buy_quantity # keep original buy quantity for it not sell filled yet.

            # find the order in data['smart_orders'] and update it.
            for i, existing_order in enumerate(data['smart_orders']):
                if existing_order['symbol'] == order['symbol']:
                    data['smart_orders'][i] = order
                    break

        for i, order in enumerate(data['smart_orders']):
            if order['symbol'] in no_buy_cancel_symbols:
                data['smart_orders'].pop(i)

        # Add not in data['smart_orders'] but in previous smart output file into data['smart_orders'].
        prev_date = calendar.get_trading_days_before(this_date, 1)
        prev_smart_output_file = os.path.join(REPORT_PATH, f'smart_orders_{prev_date}.json')
        if os.path.exists(prev_smart_output_file):
            prev_data = {}
            with open(prev_smart_output_file, 'r') as f:
                prev_data = json.load(f)
            data_keys = [ o['symbol'] for o in data['smart_orders']]
            left_running_orders = [ o for o in prev_data['smart_orders'] if o['symbol'] not in data_keys and o['symbol'] in running_orders ]
            for i, order in enumerate(left_running_orders):
                if order['symbol'] in no_buy_cancel_symbols:
                    left_running_orders.pop(i)
            for order in left_running_orders:
                valid_until = running_orders[order['symbol']]['valid_until']
                # the order is about to expire today, force sell at market price next day.
                if this_date >= valid_until:
                    id = running_orders[order['symbol']]['id']
                    trigger_condition = running_orders[order['symbol']]['trigger_condition']
                    buy_quantity = int(running_orders[order['symbol']]['buy_or_sell_quantity'])
                    profit_price = trigger_condition.split(',')[0].split('>=')[1].replace('元(触发止盈)', '')
                    lose_price = order['current_price'] * 0.90
                    trigger_condition = f'股价>={lose_price:.2f}元' # force sell now.
                    cursor.execute("""
                        UPDATE smart_orders
                        SET trigger_condition=?, reason_of_ending=?, last_updated=?
                        WHERE id=? AND user_id=?
                    """, (trigger_condition, 'order_expired_before_sell', convert_to_datetime(this_date), id, user_id))
                    order['sell_take_profit_price'] = round(lose_price, 2)
                    order['name'] += '_expired'

                data['smart_orders'].append(order)
                prev_orders += 1

    with open(smart_output_file, 'w') as f:
        json.dump(data, f)
    logger.info(f"Added {added_orders}, adjusted {adjusted_orders}, previous {prev_orders} smart orders in db.")
    return smart_output_file


def execute_buy_order(user_id: int, symbol: str, name: str,
                     buy_price: float, quantity: int,
                     take_profit: float, stop_loss: float,
                     transaction_date: str, order_number: str,
                     holding_days: int = 4) -> bool:
    """
    Execute buy order following T+1 rules and update database.

    Args:
        user_id: User ID
        symbol: Stock code
        name: Stock name
        buy_price: Actual buy price
        quantity: Buy quantity
        transaction_date: Trading date (YYYYMMDD)
        order_number: Order number
        holding_days: Max holding days for this order

    Returns:
        bool: True if successful
    """
    has_exceptions = True
    with DB.cursor() as cursor:
        # Calculate transaction costs
        amount = buy_price * quantity
        commission = amount * COMMISSION
        commission = max(commission, 5.0)  # Minimum 5 yuan
        tax = 0.0  # No tax on buy
        net_amount = amount + commission

        # Insert into transactions table
        cursor.execute("""
            INSERT INTO transactions (
                user_id, code, name, transaction_type, transaction_date,
                price, quantity, amount, commission, tax, net_amount,
                notes
            ) VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, symbol, name, convert_to_datetime(transaction_date),
            buy_price, quantity, amount, commission, tax, net_amount,
            f'Order {order_number} executed'
        ))

        # Check if stock already exists in holding_stocks
        cursor.execute("""
            SELECT holdings, cost_basis_total, available_shares
            FROM holding_stocks
            WHERE code = ? AND user_id = ?
        """, (symbol, user_id))
        existing = cursor.fetchone()
        if existing:
            # Update existing holding
            old_holdings, old_cost_total, old_available = existing
            new_holdings = old_holdings + quantity
            new_cost_total = old_cost_total + net_amount
            new_cost_basis = new_cost_total / new_holdings

            # T+1 rule: today's purchase not available until next trading day
            # available_shares remains unchanged
            cursor.execute("""
                UPDATE holding_stocks
                SET holdings = ?,
                    cost_basis_diluted = ?,
                    cost_basis_total = ?,
                    market_value = ?,
                    last_updated = ?
                WHERE code = ? AND user_id = ?
            """, (
                new_holdings, new_cost_basis, new_cost_total,
                new_holdings * buy_price, convert_to_datetime(transaction_date),
                symbol, user_id
            ))
        else:
            # Insert new holding
            cursor.execute("""
                INSERT INTO holding_stocks (
                    user_id, code, name, current_price, holdings,
                    cost_basis_diluted, cost_basis_total, market_value,
                    available_shares, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                user_id, symbol, name, buy_price, quantity,
                net_amount / quantity, net_amount, buy_price * quantity,
                0,  # T+1: not available today
                convert_to_datetime(transaction_date)
            ))

        # Update smart order status
        cursor.execute("""
            UPDATE smart_orders
            SET status = 'completed',
                reason_of_ending = 'buy_order_filled',
                last_updated = ?
            WHERE order_number = ?
        """, (convert_to_datetime(transaction_date), order_number))
        logger.info(f"✓ Buy order executed: {symbol} x{quantity} @ ¥{buy_price}")

        # Create next trading day smart order for selling
        next_date = calendar.get_trading_days_after(transaction_date, 1)
        trigger_condition = f'股价>={take_profit:.2f}元(触发止盈),股价<={stop_loss:.2f}元(触发止损)'
        valid_until = calendar.get_trading_days_after(transaction_date, holding_days)
        cursor.execute("""
            INSERT INTO smart_orders (
                user_id, code, name, trigger_condition,
                buy_or_sell_price_type, buy_or_sell_quantity,
                order_number, status, valid_until,
                last_updated
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, symbol, name, trigger_condition,
            '即时买一价', quantity,
            f"ORD_{next_date}_{symbol}_{user_id}", 'running', valid_until,
            convert_to_datetime(transaction_date)
            ))
        has_exceptions = False

    if has_exceptions:
        logger.error(f"Failed to execute buy order for {symbol}")
        return False
    return True


def execute_sell_order(user_id: int, symbol: str, name: str,
                      sell_price: float, quantity: int,
                      transaction_date: str, order_number: str,
                      reason: str = 'take_profit') -> bool:
    """
    Execute sell order following T+1 rules and update database.

    Args:
        user_id: User ID
        symbol: Stock code
        name: Stock name
        sell_price: Actual sell price
        quantity: Sell quantity
        transaction_date: Trading date (YYYYMMDD)
        order_number: Order number
        reason: Reason for selling ('take_profit', 'stop_loss', 'manual')

    Returns:
        bool: True if successful
    """
    has_exceptions = True
    with DB.cursor() as cursor:
        # Get current holding
        cursor.execute("""
            SELECT holdings, available_shares, cost_basis_diluted, cost_basis_total
            FROM holding_stocks
            WHERE code = ? AND user_id = ?
        """, (symbol, user_id))

        holding = cursor.fetchone()
        if not holding:
            logger.warning(f"No holding found for {symbol}")
            return False

        holdings, available_shares, cost_basis_diluted, cost_basis_total = holding

        # T+1 check: can only sell available shares
        if available_shares < quantity:
            logger.warning(f"Insufficient available shares for {symbol}: need {quantity}, have {available_shares}")
            return False

        # Calculate transaction costs
        amount = sell_price * quantity
        commission = amount * COMMISSION
        commission = max(commission, 5.0)  # Minimum 5 yuan
        tax = amount * TAX
        net_amount = amount - commission - tax

        # Calculate P&L
        cost_basis = cost_basis_diluted * quantity
        pnl = net_amount - cost_basis
        pnl_percent = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

        # Insert into transactions table
        cursor.execute("""
            INSERT INTO transactions (
                user_id, code, name, transaction_type, transaction_date,
                price, quantity, amount, commission, tax, net_amount,
                notes
            ) VALUES (?, ?, ?, 'sell', ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            user_id, symbol, name, convert_to_datetime(transaction_date),
            sell_price, quantity, amount, commission, tax, net_amount,
            f'Order {order_number} executed: {reason}, P&L: ¥{pnl:.2f} ({pnl_percent:.2f}%)'
        ))

        # Update holding_stocks
        new_holdings = holdings - quantity
        new_available = available_shares - quantity

        if new_holdings <= 0:
            # All sold - remove from holding_stocks
            cursor.execute("""
                DELETE FROM holding_stocks
                WHERE code = ? AND user_id = ?
            """, (symbol, user_id))
            logger.info(f"✓ Position closed: {symbol}")
        else:
            # Partial sell - update holding_stocks
            new_cost_total = cost_basis_total - (cost_basis_diluted * quantity)
            new_market_value = new_holdings * sell_price

            cursor.execute("""
                UPDATE holding_stocks
                SET holdings = ?,
                    available_shares = ?,
                    cost_basis_total = ?,
                    market_value = ?,
                    last_updated = ?
                WHERE code = ? AND user_id = ?
            """, (
                new_holdings, new_available, new_cost_total, new_market_value,
                convert_to_datetime(transaction_date), symbol, user_id
            ))
            logger.info(f"✓ Partial sell: {symbol} x{quantity}, remaining {new_holdings}")

        # Update smart order status
        cursor.execute("""
            UPDATE smart_orders
            SET status = 'completed',
                reason_of_ending = ? || ', ' || reason_of_ending,
                last_updated = ?
            WHERE order_number = ?
        """, (reason, convert_to_datetime(transaction_date), order_number))

        logger.info(f"✓ Sell order executed: {symbol} x{quantity} @ ¥{sell_price}, P&L: ¥{pnl:.2f}")
        has_exceptions = False
    if has_exceptions:
        logger.error(f"Failed to execute sell order for {symbol}")
        return False
    return True

def update_available_shares_for_new_day(date: str, user_id: int = 1) -> int:
    """
    Update available_shares at the start of new trading day (T+1 becomes available).

    Args:
        date: Current trading date (YYYY-MM-DD)
        user_id: User ID

    Returns:
        int: Number of holdings updated
    """
    updated = 0
    date = calendar.get_trading_days_before(date, 1)
    with DB.cursor() as cursor:
        # Make all holdings = available_shares (T+1 period passed)
        cursor.execute("""
            UPDATE holding_stocks
            SET available_shares = holdings,
                last_updated = ?
            WHERE user_id = ? AND available_shares < holdings
        """, (convert_to_datetime(date), user_id))
        updated = cursor.rowcount
        logger.info(f"Updated {updated} holdings: T+1 shares now available")
    return updated


class OrderAnalyzer:
    """Analyze smart order execution and performance with T+1 compliance."""

    def __init__(self, smart_orders_file: str, user_id: int = 1):
        """Initialize with smart orders JSON file."""

        self.user_id = user_id
        with open(smart_orders_file, 'r') as f:
            self.smart_orders_data = json.load(f)
        self.target_date = self.smart_orders_data['target_trading_date']
        self.market_pattern = self.smart_orders_data['market_pattern']
        self.strategy_config = self.smart_orders_data.get('strategy_config', {})
        self.orders = self.smart_orders_data['smart_orders']
        self.regime_data = self.smart_orders_data.get('regime_data', {})
        self.holding_days = self.regime_data.get('max_hold_days', 4)

    def get_market_data(self, symbol: str, date: str) -> Optional[pd.Series]:
        """Get OHLCV data for a symbol on a specific date."""

        try:
            # Get data for the date and a few days before for context
            start_date = calendar.get_trading_days_before(date, 5)
            df = data_provider.get_stock_data(symbol, start_date, date)
            if df.empty:
                logger.warning(f"No data found for {symbol} on {date}")
                return None
            # Get the last row (target date)
            return df.iloc[-1]
        except Exception as e:
            logger.error(f"Error fetching data for {symbol}: {e}")
            return None

    def check_order_execution(self, order: Dict, market_data: Optional[pd.Series],
                             date: str) -> Dict[str, Any]:
        """
        Check if an order would have been executed based on market data with T+1 compliance.

        Args:
            order: Order dictionary
            market_data: Market OHLCV data
            date: Current trading date (YYYY-MM-DD)
            user_id: User ID

        Returns:
            Dict with execution results
        """
        if market_data is None:
            return {
                'executed': False,
                'reason': 'No market data available'
            }

        symbol = order['symbol']
        name = order['name']
        buy_price = order['buy_price']          # backtest/cli.py no-rsi-rule, set to open_price
        _current_price = order['current_price']  # prev_close_price
        take_profit = order['sell_take_profit_price']
        stop_loss = order['sell_stop_loss_price']
        quantity = order['buy_quantity']
        order_number = order.get('order_number', f"ORD_{date}_{symbol}_{self.user_id}")

        # Get market prices
        open_price = float(market_data['open'])
        high_price = float(market_data['high'])
        low_price = float(market_data['low'])
        close_price = float(market_data['close'])
        prev_close = float(market_data['pre_close'])

        holding = ()
        with DB.cursor() as cursor:
            # Check if we already hold this stock
            cursor.execute("""
                SELECT holdings, available_shares, cost_basis_diluted, last_updated
                FROM holding_stocks
                WHERE code = ? AND user_id = ?
            """, (symbol, self.user_id))
            holding = cursor.fetchone()

        # T+1 Rule: Can only sell if stock was purchased BEFORE today
        can_sell_today = False
        if holding:
            holdings, available_shares, cost_basis, last_updated = holding
            purchase_date = convert_trade_date(last_updated)
            if not purchase_date:
                raise ValueError(f"Invalid last_updated date for holding {symbol}: {last_updated}")
            can_sell_today = available_shares > 0 and purchase_date < date
            if can_sell_today:
                # Check sell triggers (take-profit or stop-loss)
                tp_hit = high_price >= take_profit
                sl_hit = low_price <= stop_loss

                if tp_hit or sl_hit or '_expired' in name:
                    if '_expired' in name:
                        logger.info(f"Order {order_number} for {symbol} is expired, force sell today.")
                        sell_price = open_price
                        reason = 'order_expired_before_sell'
                    else:
                        # Execute sell order
                        sell_price = take_profit if tp_hit else stop_loss
                        reason = 'take_profit' if tp_hit else 'stop_loss'

                    success = execute_sell_order(
                        self.user_id, symbol, name, sell_price,
                        min(available_shares, quantity), date,
                        order_number, reason
                    )
                    if success:
                        # Calculate P&L
                        cost = cost_basis * quantity
                        exit_value = sell_price * quantity
                        pnl = exit_value - cost
                        pnl_pct = (pnl / cost) * 100
                        return {
                            'executed': True,
                            'action': 'sell',
                            'buy_fill_price': cost_basis,
                            'exit_price': sell_price,
                            'exit_reason': reason,
                            'quantity': quantity,
                            'cost_basis': cost,
                            'exit_value': exit_value,
                            'pnl': pnl,
                            'pnl_pct': pnl_pct,
                            't1_restriction': False,
                            'purchase_date': purchase_date,
                            'holding_days': calendar.get_trading_days_between(purchase_date, date),
                            'market_summary': {
                                'prev_close': prev_close,
                                'open': open_price,
                                'high': high_price,
                                'low': low_price,
                                'close': close_price,
                                'turnover_rate': float(market_data.get('turnover_rate', 0))
                            }
                        }
                    else:
                        return {
                            'executed': False,
                            'reason': 'Sell order execution failed'
                        }
                else:
                    # Holding continues - Update Trailing Stop
                    new_stop_loss, reason = calculate_trailing_stop(
                        entry_price=cost_basis,
                        current_price=high_price,
                        initial_stop_loss=stop_loss
                    )
                    
                    if new_stop_loss > stop_loss:
                        # Update smart order with new stop loss
                        with DB.cursor() as cursor:
                            trigger_condition = f'股价>={take_profit:.2f}元(触发止盈),股价<={new_stop_loss:.2f}元(触发止损)'
                            cursor.execute("""
                                UPDATE smart_orders
                                SET trigger_condition = ?,
                                    last_updated = ?
                                WHERE order_number = ?
                            """, (trigger_condition, convert_to_datetime(date), order_number))
                        logger.info(f"Updated trailing stop for {symbol}: {stop_loss} -> {new_stop_loss} ({reason})")

                    return {
                        'executed': True,
                        'action': 'hold',
                        'exit_reason': 'held',
                        'exit_price': close_price,
                        't1_restriction': False,
                        'purchase_date': purchase_date,
                        'market_summary': {
                            'prev_close': prev_close,
                            'open': open_price,
                            'high': high_price,
                            'low': low_price,
                            'close': close_price
                        }
                    }
            else:
                # T+1 restriction: bought today, cannot sell
                return {
                    'executed': True,
                    'action': 'hold',
                    'exit_reason': 'held_t1',
                    'exit_price': close_price,
                    't1_restriction': True,
                    'purchase_date': purchase_date,
                    'unrealized_pnl': (close_price - cost_basis) * holdings,
                    'unrealized_pnl_pct': ((close_price - cost_basis) / cost_basis) * 100,
                    'market_summary': {
                        'prev_close': prev_close,
                        'open': open_price,
                        'high': high_price,
                        'low': low_price,
                        'close': close_price
                    }
                }

        # add gap‑up + fade filters ...
        gap = (open_price - prev_close) / prev_close if prev_close > 0 else 0.0
        if gap > 0.05:
            #return {"executed": False, "reason": f"gap_up_filter: {gap:.2%}, {prev_close}, {open_price}"}
            pass
        if open_price > prev_close * 1.03 and close_price < open_price:
            #return {"executed": False, "reason": f"gap_up_fade_filter: {(open_price/prev_close):.2%}, {prev_close}, {open_price}, {close_price}"}
            pass

        # No holding - check buy trigger: buy_price is today open_price, current_price is prev_close_price.
        buy_executed = open_price >= prev_close
        if not buy_executed:
            return {
                'executed': False,
                'reason': f'Open price ¥{open_price} < Prev close price ¥{prev_close}',
                'market_summary': {
                    'prev_close': prev_close,
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price
                }
            }

        # Calculate actual fill price
        buy_fill_price = min(buy_price, open_price)

        # Execute buy order
        success = execute_buy_order(
            self.user_id, symbol, name,
            buy_fill_price, quantity,
            take_profit, stop_loss,
            date, order_number,
            holding_days=self.holding_days
        )
        if success:
            return {
                'executed': True,
                'action': 'buy',
                'buy_fill_price': buy_fill_price,
                'exit_price': close_price,
                'exit_reason': 'held_t1',
                'quantity': quantity,
                'cost_basis': buy_fill_price * quantity,
                'exit_value': close_price * quantity,
                'pnl': (close_price - buy_fill_price) * quantity,
                'pnl_pct': ((close_price - buy_fill_price) / buy_fill_price) * 100,
                't1_restriction': True,
                'purchase_date': date,
                'market_summary': {
                    'prev_close': prev_close,
                    'open': open_price,
                    'high': high_price,
                    'low': low_price,
                    'close': close_price,
                    'turnover_rate': float(market_data.get('turnover_rate', 0))
                }
            }
        else:
            return {
                'executed': False,
                'reason': 'Buy order execution failed'
            }


    def generate_daily_report(self, date: str, output_file: str):
        """Generate order completion report for a specific date with T+1 compliance.
        The reports will show:
        - Which orders were executed (filled)
        - P&L for each executed order
        - Whether take-profit or stop-loss was hit
        - Portfolio performance over the 2-day period
        - Recommendations for order adjustments
        """
        logger.info(f"Generating report for {date}...")

        # Update available shares at start of new trading day
        update_available_shares_for_new_day(date=date, user_id=self.user_id)

        results = []
        total_invested = 0
        total_pnl = 0
        executed_count = 0
        t1_restricted_count = 0
        for order in self.orders:
            symbol = order['symbol']
            name = order['name']

            # Get market data
            #market_data = self.get_market_data(symbol, date)
            market_data = data_provider.get_stock_data(symbol, date, date).iloc[-1]

            # Check execution with T+1 compliance
            execution = self.check_order_execution(order, market_data, date)

            results.append({
                'symbol': symbol,
                'name': name,
                'order': order,
                'execution': execution
            })

            if execution.get('executed'):
                if execution.get('action') == 'buy':
                    executed_count += 1
                    total_invested += execution['cost_basis']
                    t1_restricted_count += 1
                elif execution.get('action') == 'sell':
                    executed_count += 1
                    total_pnl += execution.get('pnl', 0)
                elif execution.get('t1_restriction'):
                    t1_restricted_count += 1

        # When date execute finish, read sell actions from transactions table by date, then remove them from this date smart output file.
        smart_output_file = os.path.join(REPORT_PATH, f'smart_orders_{date}.json')
        if not os.path.exists(smart_output_file):
            raise ValueError(f"Smart orders file for {date} not found: {smart_output_file}")    
        data = {}
        with open(smart_output_file, 'r') as f:
            data = json.load(f)
        sold_symbols = set()
        with DB.cursor() as cursor:
            cursor.execute("""
                SELECT code
                FROM transactions
                WHERE transaction_type='sell' AND transaction_date=?
                AND user_id=?
            """, (convert_to_datetime(date), self.user_id))
            for row in cursor.fetchall():
                sold_symbols.add(row[0])
        if sold_symbols:
            data['smart_orders'] = [ o for o in data['smart_orders'] if o['symbol'] not in sold_symbols ]
            with open(smart_output_file, 'w') as f:
                json.dump(data, f)
            logger.info(f"Removed {len(sold_symbols)} sold orders from previous smart orders file.")

        # Generate markdown report
        self._write_markdown_report_t1(date, results, total_invested, total_pnl,
                                       executed_count, t1_restricted_count, output_file)

        logger.info(f"✓ Report saved to {output_file}")
        return results

    def _write_markdown_report_t1(self, date: str, results: List[Dict],
                                   total_invested: float, total_pnl: float,
                                   executed_count: int, t1_restricted_count: int,
                                   output_file: str):
        """Write results to markdown file with T+1 compliance info."""

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"# Smart Orders Completion Report - {date}\n\n")
            f.write(f"**Analysis Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Trading Date:** {date}\n")
            f.write(f"**Market Pattern:** {self.smart_orders_data['market_pattern']}\n")
            f.write("**T+1 Compliance:** ✓ Enforced\n\n")

            f.write("## Portfolio Summary\n\n")
            f.write(f"- **Initial Capital:** ¥{INITIAL_CASH:,.2f}\n")
            f.write(f"- **Orders Executed:** {executed_count}/{len(self.orders)}\n")
            f.write(f"- **T+1 Restricted Positions:** {t1_restricted_count}\n")
            f.write(f"- **Total Invested:** ¥{total_invested:,.2f}\n")
            f.write(f"- **Realized P&L:** ¥{total_pnl:,.2f}\n")
            f.write(f"- **Cash Remaining:** ¥{INITIAL_CASH - total_invested + total_pnl:,.2f}\n\n")

            f.write("## Order Execution Details\n\n")

            for result in results:
                order = result['order']
                execution = result['execution']

                f.write(f"### {result['symbol']} - {result['name']}\n\n")

                f.write("**Order Details:**\n")
                f.write(f"- Buy Price Target: ¥{order['buy_price']}\n")
                f.write(f"- Take Profit: ¥{order['sell_take_profit_price']} (+{order['risk_metrics']['potential_gain_pct']}%)\n")
                f.write(f"- Stop Loss: ¥{order['sell_stop_loss_price']} ({order['risk_metrics']['potential_loss_pct']}%)\n")
                f.write(f"- Quantity: {order['buy_quantity']} shares\n\n")

                if execution.get('executed'):
                    action = execution.get('action', 'unknown')

                    if action == 'buy':
                        f.write("**Execution:** ✅ **BUY ORDER FILLED**\n")
                        f.write(f"- Fill Price: ¥{execution['buy_fill_price']}\n")
                        f.write(f"- Quantity: {execution['quantity']} shares\n")
                        f.write(f"- Cost: ¥{execution['cost_basis']:,.2f}\n")
                        f.write("- ⚠️ **T+1 Restriction:** Cannot sell until next trading day\n")
                        f.write(f"- Current Price: ¥{execution['exit_price']}\n")
                        f.write(f"- Unrealized P&L: ¥{execution['pnl']:,.2f} ({execution['pnl_pct']:.2f}%)\n\n")

                    elif action == 'sell':
                        f.write("**Execution:** ✅ **SELL ORDER FILLED**\n")
                        f.write(f"- Entry Price: ¥{execution['buy_fill_price']}\n")
                        f.write(f"- Exit Price: ¥{execution['exit_price']}\n")
                        f.write(f"- Exit Reason: {execution['exit_reason'].upper()}\n")
                        f.write(f"- Holding Period: {execution['holding_days']} days\n")
                        f.write(f"- **Realized P&L:** ¥{execution['pnl']:,.2f} ({execution['pnl_pct']:.2f}%)\n\n")

                    elif action == 'hold':
                        if execution.get('t1_restriction'):
                            f.write("**Status:** 📌 **HOLDING (T+1 Restricted)**\n")
                            f.write(f"- Purchase Date: {execution['purchase_date']}\n")
                            f.write("- ⚠️ Cannot sell today due to T+1 rule\n")
                            if 'unrealized_pnl' in execution:
                                f.write(f"- Unrealized P&L: ¥{execution['unrealized_pnl']:,.2f} ({execution['unrealized_pnl_pct']:.2f}%)\n")
                        else:
                            f.write("**Status:** 📊 **HOLDING (Available for Sale)**\n")
                            f.write(f"- Purchase Date: {execution['purchase_date']}\n")
                            f.write("- Holding Period: T+1 restriction lifted\n")
                        f.write(f"- Current Price: ¥{execution['exit_price']}\n\n")

                    if 'market_summary' in execution:
                        market = execution['market_summary']
                        f.write("**Market Data:**\n")
                        f.write(f"- Prev Close: {market['prev_close']}, Open: ¥{market['open']}, High: ¥{market['high']}, Low: ¥{market['low']}, Close: ¥{market['close']}\n")
                        if 'turnover_rate' in market:
                            f.write(f"- Turnover Rate: {market['turnover_rate']}%\n")
                        f.write("\n")
                else:
                    f.write(f"**Execution:** ❌ Not Filled - {execution['reason']}\n")
                    if 'market_summary' in execution:
                        market = execution['market_summary']
                        f.write(f"- Market: Prev Close ¥{market['prev_close']}, Open ¥{market['open']}, Low ¥{market['low']}, Close ¥{market['close']}\n")
                    f.write("\n")

                f.write("---\n\n")

            f.write("## T+1 Trading Rules\n\n")
            f.write("- ✅ All buy orders executed with T+1 restriction\n")
            f.write("- ✅ Shares purchased today become available next trading day\n")
            f.write("- ✅ Only available shares can be sold\n")
            f.write("- ✅ No same-day buy-sell violations\n")


    def generate_period_report(self, start_date: str | None, end_date: str | None, output_file: str):
        """
        Generate comprehensive report for a trading period.

        This function ONLY reads historical data from the database.
        It does NOT execute any trades - all trading is done by check_order_execution().

        Includes:
        - Realized P&L from completed sell transactions
        - Unrealized P&L from current holdings at market close
        - Daily breakdown with both realized and unrealized P&L
        """
        start_date = convert_trade_date(start_date) if start_date else None
        end_date = convert_trade_date(end_date) if end_date else None
        if not start_date or not end_date:
            raise ValueError("Start date and end date must be provided for period report.")

        trading_dates = calendar.get_trading_days_between(start_date, end_date)
        logger.info(f"Generating period report from {start_date} to {end_date}...")

        timeline: List[Dict[str, Any]] = []
        cumulative_realized_pnl = 0.0

        def get_holdings_on_date(date: str) -> Dict[str, Dict]:
            """Get holdings snapshot from database for a specific date."""
            holdings = {}
            with DB.cursor() as cursor:
                # Get holdings updated on or before this date
                cursor.execute("""
                    SELECT code, name, holdings, available_shares,
                           cost_basis_diluted, cost_basis_total, last_updated
                    FROM holding_stocks
                    WHERE user_id = ? AND last_updated <= ?
                """, (self.user_id, convert_to_datetime(date)))

                for row in cursor.fetchall():
                    purchase_date = convert_trade_date(row[6])
                    if not purchase_date:
                        raise ValueError(f"Invalid last_updated date for holding {row[0]}: {row[6]}")
                    symbol = row[0]
                    holdings[symbol] = {
                        'name': row[1],
                        'quantity': row[2],
                        'available': row[3],
                        'cost_per_share': row[4],
                        'cost_total': row[5],
                        'purchase_date': purchase_date
                    }
            return holdings

        def get_realized_pnl_for_date(date: str) -> tuple[float, int]:
            """
            Calculate realized P&L from sell transactions on this date.

            Returns:
                Tuple of (pnl, number_of_sells)
            """
            pnl = 0.0
            sell_count = 0

            with DB.cursor() as cursor:
                # Get all sell transactions for this date
                cursor.execute("""
                    SELECT code, quantity, price, net_amount, notes
                    FROM transactions
                    WHERE user_id = ?
                      AND transaction_type = 'sell'
                      AND transaction_date = ?
                """, (self.user_id, convert_to_datetime(date)))

                sells = cursor.fetchall()
                sell_count = len(sells)

                for sell in sells:
                    symbol = sell[0]
                    quantity = int(sell[1])
                    _ = float(sell[2]) # sell price per share
                    net_amount = float(sell[3])
                    notes = sell[4]

                    # Extract P&L from notes (format: "... P&L: ¥123.45 (12.34%)")
                    # Prioritize notes as holding_stocks might be updated (re-entry) or deleted
                    transaction_pnl = 0.0
                    pnl_found = False
                    
                    if 'P&L:' in notes:
                        try:
                            pnl_str = notes.split('P&L: ¥')[1].split(' ')[0]
                            transaction_pnl = float(pnl_str)
                            pnl_found = True
                        except Exception as e:
                            logger.warning(f"Could not parse P&L from notes for {symbol}: {e}")
                    
                    if not pnl_found:
                        # Fallback to cost basis from holding_stocks (RISKY if re-entered)
                        cursor.execute("""
                            SELECT cost_basis_diluted
                            FROM holding_stocks
                            WHERE code = ? AND user_id = ?
                        """, (symbol, self.user_id))

                        cost_row = cursor.fetchone()
                        if cost_row:
                            cost_basis = float(cost_row[0]) * quantity
                            transaction_pnl = net_amount - cost_basis
                        else:
                            transaction_pnl = 0

                    pnl += transaction_pnl

            return pnl, sell_count

        def calculate_unrealized_pnl(holdings: Dict[str, Dict], date: str) -> tuple[float, float, float]:
            """
            Calculate unrealized P&L for all holdings.

            Returns:
                Tuple of (unrealized_pnl, total_cost, total_market_value)
            """
            unrealized_pnl = 0.0
            total_cost = 0.0
            total_market_value = 0.0

            for symbol, holding in holdings.items():
                market_data = self.get_market_data(symbol, date)
                if market_data is None:
                    logger.warning(f"No market data for {symbol} on {date}, using cost basis")
                    current_price = holding['cost_per_share']
                else:
                    current_price = float(market_data['close'])

                quantity = holding['quantity']
                cost_per_share = holding['cost_per_share']

                holding_cost = quantity * cost_per_share
                market_value = quantity * current_price
                holding_unrealized = market_value - holding_cost

                total_cost += holding_cost
                total_market_value += market_value
                unrealized_pnl += holding_unrealized

            return unrealized_pnl, total_cost, total_market_value

        # Process each trading day - READ ONLY
        for trading_day in trading_dates:
            # Get realized P&L from executed sells
            daily_realized_pnl, sell_count = get_realized_pnl_for_date(trading_day)
            cumulative_realized_pnl += daily_realized_pnl

            # Get current holdings
            holdings = get_holdings_on_date(trading_day)

            # Calculate unrealized P&L
            unrealized_pnl, total_held_cost, total_held_market_value = calculate_unrealized_pnl(
                holdings, trading_day
            )

            # Calculate total P&L
            total_pnl = cumulative_realized_pnl + unrealized_pnl
            portfolio_value = INITIAL_CASH + total_pnl

            # Count all transactions (buy + sell) for this day
            with DB.cursor() as cursor:
                cursor.execute("""
                    SELECT COUNT(*)
                    FROM transactions
                    WHERE user_id = ? AND transaction_date = ?
                """, (self.user_id, convert_to_datetime(trading_day)))
                total_transactions = cursor.fetchone()[0]

            timeline.append({
                'date': trading_day,
                'executed_orders': total_transactions,
                'sell_count': sell_count,
                'daily_realized_pnl': daily_realized_pnl,
                'daily_unrealized_pnl': unrealized_pnl,
                'daily_total_pnl': daily_realized_pnl + unrealized_pnl,
                'cumulative_realized_pnl': cumulative_realized_pnl,
                'cumulative_unrealized_pnl': unrealized_pnl,
                'cumulative_total_pnl': total_pnl,
                'total_held_cost': total_held_cost,
                'total_held_market_value': total_held_market_value,
                'portfolio_value': portfolio_value,
                'positions_count': len(holdings),
            })

        if not timeline:
            raise ValueError("Unable to build period timeline from transactions.")

        # Get final holdings for report
        final_holdings = get_holdings_on_date(end_date)

        # --- Benchmark Comparison & Summary ---
        # Create Portfolio DataFrame
        df_portfolio = pd.DataFrame(timeline)
        df_portfolio['date'] = pd.to_datetime(df_portfolio['date'])
        df_portfolio.set_index('date', inplace=True)
        df_portfolio['return'] = df_portfolio['portfolio_value'].pct_change().fillna(0)
        
        # Calculate Strategy Return
        final_day = timeline[-1]
        total_return_pct = (final_day['cumulative_total_pnl'] / INITIAL_CASH * 100) if INITIAL_CASH > 0 else 0
        strat_ret = total_return_pct / 100

        # Fetch Benchmark Data
        benchmarks = {
            'SSE Composite': '000001.SH',
            'CSI 300': '000300.SH',
            'CSI 500': '000905.SH',
        }
        
        benchmark_results = {}
        
        for name, code in benchmarks.items():
            try:
                df_index = data_provider.get_index_data(code, start_date, end_date)
                if not df_index.empty:
                    df_index['trade_date'] = pd.to_datetime(df_index['trade_date'])
                    df_index.set_index('trade_date', inplace=True)
                    df_index['return'] = df_index['close'].pct_change().fillna(0)
                    
                    # Align dates
                    common_dates = df_portfolio.index.intersection(df_index.index)
                    if len(common_dates) > 1:
                        port_returns = df_portfolio.loc[common_dates, 'return']
                        bench_returns = df_index.loc[common_dates, 'return']
                        
                        # Calculate Metrics
                        close_end = pd.to_numeric(df_index.loc[common_dates[-1], 'close'])
                        if 'pre_close' in df_index.columns and pd.to_numeric(df_index.loc[common_dates[0], 'pre_close']) > 0:
                            close_start = pd.to_numeric(df_index.loc[common_dates[0], 'pre_close'])
                        else:
                            close_start = pd.to_numeric(df_index.loc[common_dates[0], 'close'])
                        bench_total_return = (float(close_end) / float(close_start)) - 1
                        excess_return = strat_ret - bench_total_return
                        
                        covariance = float(pd.to_numeric(port_returns.cov(bench_returns)))
                        variance = float(pd.to_numeric(bench_returns.var()))
                        beta = covariance / variance if variance != 0 else 0
                        
                        # Alpha (simple approximation: R_p - Beta * R_b)
                        alpha = float(pd.to_numeric(port_returns.mean())) - beta * float(pd.to_numeric(bench_returns.mean()))
                        
                        correlation = float(pd.to_numeric(port_returns.corr(bench_returns)))
                        
                        benchmark_results[name] = {
                            'return': bench_total_return,
                            'excess': excess_return,
                            'beta': beta,
                            'alpha': alpha,
                            'correlation': correlation
                        }
            except Exception as e:
                logger.warning(f"Failed to calculate metrics for {name}: {e}")

        # Print Summary to Console
        print("\n" + "="* 80)
        print(f"{'BACKTEST RESULTS SUMMARY':^80}")
        print("="*80)
        print(f"{'Metric':<20} {'Strategy':<15} {'SSE Composite':<15} {'CSI 300':<15} {'CSI 500':<15}")
        print("-" * 80)
        
        sse = benchmark_results.get('SSE Composite', {})
        csi300 = benchmark_results.get('CSI 300', {})
        csi500 = benchmark_results.get('CSI 500', {})
        
        print(f"{'Total Return':<20} {strat_ret:>14.2%} {sse.get('return', 0):>14.2%} {csi300.get('return', 0):>14.2%} {csi500.get('return', 0):>14.2%}")
        print(f"{'Excess Return':<20} {'-':>15} {sse.get('excess', 0):>14.2%} {csi300.get('excess', 0):>14.2%} {csi500.get('excess', 0):>14.2%}")
        print(f"{'Beta':<20} {'-':>15} {sse.get('beta', 0):>14.4f} {csi300.get('beta', 0):>14.4f} {csi500.get('beta', 0):>14.4f}")
        print(f"{'Alpha':<20} {'-':>15} {sse.get('alpha', 0):>14.4f} {csi300.get('alpha', 0):>14.4f} {csi500.get('alpha', 0):>14.4f}")
        print(f"{'Correlation':<20} {'-':>15} {sse.get('correlation', 0):>14.4f} {csi300.get('correlation', 0):>14.4f} {csi500.get('correlation', 0):>14.4f}")
        print("="*80 + "\n")

        self._write_period_report(
            start_date, end_date, timeline, final_holdings, output_file, benchmark_results
        )
        logger.info(f"✓ Period report saved to {output_file}")

    def _write_period_report(self, start_date: str, end_date: str,
                             timeline: List[Dict], final_holdings: Dict,
                             output_file: str, benchmark_results: Dict = {}):
        """Write enhanced period report including realized, unrealized, and total P&L."""

        final_day = timeline[-1]
        total_return_pct = (final_day['cumulative_total_pnl'] / INITIAL_CASH * 100) if INITIAL_CASH > 0 else 0
        realized_return_pct = (final_day['cumulative_realized_pnl'] / INITIAL_CASH * 100) if INITIAL_CASH > 0 else 0
        unrealized_return_pct = (final_day['cumulative_unrealized_pnl'] / INITIAL_CASH * 100) if INITIAL_CASH > 0 else 0

        # Calculate statistics
        total_sells = sum(day.get('sell_count', 0) for day in timeline)
        total_transactions = sum(day.get('executed_orders', 0) for day in timeline)

        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"# Backtest Period Report: {start_date} to {end_date}\n\n")
            f.write(f"**Analysis Date:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write(f"**Strategy:** {self.market_pattern}\n")
            f.write(f"**Max Hold Days:** {self.holding_days} days\n")
            f.write("**T+1 Compliance:** ✓ Enforced\n\n")

            f.write("## 📊 Portfolio Performance Summary\n\n")
            f.write("| Metric | Value |\n")
            f.write("|--------|-------|\n")
            f.write(f"| **Initial Capital** | ¥{INITIAL_CASH:,.2f} |\n")
            f.write(f"| **Final Portfolio Value** | ¥{final_day['portfolio_value']:,.2f} |\n")
            f.write(f"| **Total Return** | {total_return_pct:.2f}% |\n")
            f.write(f"| **Realized P&L** | ¥{final_day['cumulative_realized_pnl']:,.2f} ({realized_return_pct:.2f}%) |\n")
            f.write(f"| **Unrealized P&L** | ¥{final_day['cumulative_unrealized_pnl']:,.2f} ({unrealized_return_pct:.2f}%) |\n")
            f.write(f"| **Total P&L** | ¥{final_day['cumulative_total_pnl']:,.2f} |\n")
            f.write(f"| **Active Positions** | {final_day['positions_count']} |\n")
            f.write(f"| **Held Cost Basis** | ¥{final_day['total_held_cost']:,.2f} |\n")
            f.write(f"| **Market Value (Held)** | ¥{final_day['total_held_market_value']:,.2f} |\n")
            f.write(f"| **Total Transactions** | {total_transactions} |\n")
            f.write(f"| **Sell Transactions** | {total_sells} |\n\n")

            if benchmark_results:
                f.write("## 🏆 Benchmark Comparison\n\n")
                f.write("| Metric | Strategy | SSE Composite | CSI 300 |\n")
                f.write("|--------|----------|---------------|---------|\n")
                
                sse = benchmark_results.get('SSE Composite', {})
                csi = benchmark_results.get('CSI 300', {})
                strat_ret = total_return_pct / 100
                
                f.write(f"| **Total Return** | {strat_ret:.2%} | {sse.get('return', 0):.2%} | {csi.get('return', 0):.2%} |\n")
                f.write(f"| **Excess Return** | - | {sse.get('excess', 0):.2%} | {csi.get('excess', 0):.2%} |\n")
                f.write(f"| **Beta** | - | {sse.get('beta', 0):.4f} | {csi.get('beta', 0):.4f} |\n")
                f.write(f"| **Alpha** | - | {sse.get('alpha', 0):.4f} | {csi.get('alpha', 0):.4f} |\n")
                f.write(f"| **Correlation** | - | {sse.get('correlation', 0):.4f} | {csi.get('correlation', 0):.4f} |\n\n")

            f.write("## 📈 Daily Performance Breakdown\n\n")
            f.write("| Date | Txns | Sells | Realized P&L | Unrealized P&L | Total P&L | Portfolio Value | Positions |\n")
            f.write("|------|------|-------|--------------|----------------|-----------|-----------------|----------|\n")

            for day in timeline:
                portfolio_pct = ((day['portfolio_value'] - INITIAL_CASH) / INITIAL_CASH * 100) if INITIAL_CASH > 0 else 0
                f.write(f"| {day['date']} | {day['executed_orders']} | {day['sell_count']} | ")
                f.write(f"¥{day['daily_realized_pnl']:>10,.2f} | ")
                f.write(f"¥{day['daily_unrealized_pnl']:>13,.2f} | ")
                f.write(f"¥{day['daily_total_pnl']:>9,.2f} | ")
                f.write(f"¥{day['portfolio_value']:>13,.2f} ({portfolio_pct:>6.2f}%) | ")
                f.write(f"{day['positions_count']} |\n")

            f.write("\n## 💼 Current Holdings Summary\n\n")
            if final_holdings:
                f.write("| Symbol | Name | Quantity | Avg Cost | Market Price | Market Value | Unrealized P&L | Days Held | Status |\n")
                f.write("|--------|------|----------|----------|--------------|--------------|----------------|-----------|--------|\n")

                for symbol, holding in final_holdings.items():
                    market_data = self.get_market_data(symbol, end_date)
                    market_price = float(market_data['close']) if market_data is not None else holding['cost_per_share']

                    market_value = holding['quantity'] * market_price
                    cost_total = holding['cost_total']
                    unrealized = market_value - cost_total
                    unrealized_pct = (unrealized / cost_total * 100) if cost_total > 0 else 0

                    days_held = len(calendar.get_trading_days_between(holding['purchase_date'], end_date))

                    # Status indicator based on days held
                    status = "✅ Normal"
                    if days_held >= self.holding_days:
                        status = "🔴 Expired"
                    elif days_held >= self.holding_days - 1:
                        status = "⚠️ Expires Next"

                    f.write(f"| {symbol} | {holding['name']} | {holding['quantity']} | ")
                    f.write(f"¥{holding['cost_per_share']:.2f} | ¥{market_price:.2f} | ")
                    f.write(f"¥{market_value:,.2f} | ")
                    f.write(f"¥{unrealized:,.2f} ({unrealized_pct:.2f}%) | ")
                    f.write(f"{days_held} | {status} |\n")
            else:
                f.write("No open positions.\n")

            # Add transaction history
            f.write("\n## 📜 Transaction History\n\n")
            with DB.cursor() as cursor:
                cursor.execute("""
                    SELECT transaction_date, transaction_type, code, name,
                           quantity, price, net_amount, notes
                    FROM transactions
                    WHERE user_id = ?
                      AND transaction_date BETWEEN ? AND ?
                    ORDER BY transaction_date, id
                """, (self.user_id, convert_to_datetime(start_date), convert_to_datetime(end_date)))

                transactions = cursor.fetchall()

                if transactions:
                    f.write("| Date | Type | Symbol | Name | Qty | Price | Amount | Notes |\n")
                    f.write("|------|------|--------|------|-----|-------|--------|-------|\n")

                    for txn in transactions:
                        date, txn_type, code, name, qty, price, amount, notes = txn
                        txn_type_emoji = "🟢" if txn_type == "buy" else "🔴"
                        f.write(f"| {date} | {txn_type_emoji} {txn_type.upper()} | {code} | {name} | ")
                        f.write(f"{qty} | ¥{price:.2f} | ¥{amount:,.2f} | {notes[:50]} |\n")
                else:
                    f.write("No transactions in this period.\n")

            f.write("\n## ⚙️ Strategy Configuration\n\n")
            f.write(f"- **Profit Target:** {self.strategy_config.get('profit_target_pct', 'N/A')}%\n")
            f.write(f"- **Stop Loss:** {self.strategy_config.get('stop_loss_pct', 'N/A')}%\n")
            f.write(f"- **Max Position Size:** {self.strategy_config.get('max_position_pct', 0.08)*100}%\n")
            f.write(f"- **Max Holding Period:** {self.holding_days} days\n")
            f.write(f"- **Commission Rate:** {COMMISSION*100:.4f}%\n")
            f.write(f"- **Tax Rate (Sell):** {TAX*100:.2f}%\n\n")

            f.write("## 📝 Notes\n\n")
            f.write("- **Realized P&L:** Actual gains/losses from completed sell transactions\n")
            f.write("- **Unrealized P&L:** Paper gains/losses from open positions at market close prices\n")
            f.write("- **Total P&L:** Sum of realized and unrealized P&L\n")
            f.write("- **Force-Sell:** Handled by `create_smart_orders_from_picks()` when orders expire\n")
            f.write("- **T+1 Compliance:** Purchases cannot be sold on the same day\n")
            f.write("- **Status Indicators:**\n")
            f.write("  - ✅ Normal: Within holding period\n")
            f.write("  - ⚠️ Expires Next: Will expire next trading day\n")
            f.write("  - 🔴 Expired: Has exceeded max holding period\n")


def pick_orders_trading(start_date: Optional[str]=None, end_date: Optional[str]=None, user_id: int = 1, src: str = 'ts_ths'):
    """
    Pick stocks, create smart orders and trading for the specified date range.

    Arguments:
    start_date -- The start date (format: YYYY-MM-DD), default is today.
    end_date -- The end date (format: YYYY-MM-DD), default is today.
    user_id -- The user ID for the trading account.
    src -- The source of stocks, default is 'ts_ths', or 'ts_dc', or 'ts_combine'
    """
    today = convert_trade_date(datetime.now().strftime('%Y-%m-%d'))
    if not start_date:
        start_date = end_date = today
    if not end_date:
        end_date = today
    # check start_date and end_date format valid and start_date <= end_date
    start_date = convert_trade_date(start_date)
    end_date = convert_trade_date(end_date)
    if start_date > end_date: # pyright: ignore
        end_date = start_date
    analyzer = None
    logger.info(f"Starting picking from {start_date} to {end_date}...")
    dates = calendar.get_trading_days_between(start_date, end_date)
    for this_date in dates:
        # Step 1: Pick stocks
        pick_output_file = pick_stocks_to_file(this_date, src=src)

        # Step 2: Create smart orders from picks and save to database
        smart_output_file = create_smart_orders_from_picks(pick_output_file, user_id=user_id)

        # Step 3: Analyze orders and generate reports
        analyzer = OrderAnalyzer(smart_orders_file=smart_output_file, user_id=user_id)
        # Step 3.1: Generate report for this_date
        analyzer.generate_daily_report(this_date, os.path.join(REPORT_PATH, f'report_orders_{this_date}.md'))

        # Step 3.2: Adjust orders based on this_date close
        #analyzer.adjust_orders(this_date, os.path.join(REPORT_PATH, f'adjusted_orders_{this_date.replace("-", "")}.json'))

        if this_date != dates[-1]:
            time.sleep(30) # Sleep to avoid API rate limits

    if not analyzer:
        logger.info("No orders were processed in the given date range.")
        return
    # Step 4: Generate period report
    analyzer.generate_period_report(start_date, end_date, os.path.join(REPORT_PATH, f'report_period_{start_date}_{end_date}.md'))

    logger.info("All reports generated successfully!")


if __name__ == '__main__':
    user_id = 1
    start_date = '2025-11-01'
    end_date = '2025-11-20'
    src = 'ts_ths'
    if argv := sys.argv:
        if len(argv) >= 2:
            start_date = argv[1]
        if len(argv) >= 3:
            end_date = argv[2]
        if len(argv) >= 4:
            src = argv[3]
        if len(argv) >= 5:
            user_id = int(argv[4])
    if src not in ['ts_ths', 'ts_dc', 'ts_combine', 'ts_go']:
        raise ValueError(f"Invalid source: {src}. Valid sources are 'ts_ths', 'ts_dc', 'ts_combine', and 'ts_go'.")
    
    print(f"DEBUG: Running with start_date={start_date}, end_date={end_date}, src={src}")
    REPORT_PATH = os.path.join(REPORT_PATH, f'{start_date}_{end_date}_{src}')
    os.makedirs(REPORT_PATH, exist_ok=True)
    os.system(f"""
        rm -f db/test_imobile.db; sqlite3 db/test_imobile.db < db/imobile.sql;
        rm -rf {REPORT_PATH}/*; rm -rf /tmp/tmp
    """)

    try:
        pick_orders_trading(start_date=start_date, end_date=end_date, user_id=user_id, src=src)
    except Exception as e:
        import traceback
        traceback.print_exc()
        logger.error(f"Error during automatic order picking: {e}")
        raise e
    finally:
        pass
