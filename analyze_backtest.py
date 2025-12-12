import os
import json
import pandas as pd
import glob
import re

BACKTEST_DIR = './backtest/backtest_results/20251001_20251031_ts_combine'

def load_pick_stocks():
    files = glob.glob(os.path.join(BACKTEST_DIR, 'pick_stocks_*.json'))
    all_picks = []
    for f in files:
        date_str = re.search(r'pick_stocks_(\d+).json', f).group(1)
        try:
            with open(f, 'r') as file:
                data = json.load(file)
                # Handle dict structure
                if isinstance(data, dict) and 'selected_stocks' in data:
                    items = data['selected_stocks']
                elif isinstance(data, list):
                    items = data
                else:
                    items = []
                    
                for item in items:
                    item['date'] = date_str
                    all_picks.append(item)
        except Exception as e:
            print(f"Error loading {f}: {e}")
    return pd.DataFrame(all_picks)

def load_smart_orders():
    files = glob.glob(os.path.join(BACKTEST_DIR, 'smart_orders_*.json'))
    all_orders = []
    for f in files:
        date_str = re.search(r'smart_orders_(\d+).json', f).group(1)
        try:
            with open(f, 'r') as file:
                data = json.load(file)
                # Handle dict structure
                if isinstance(data, dict) and 'smart_orders' in data:
                    items = data['smart_orders']
                elif isinstance(data, list):
                    items = data
                else:
                    items = []

                for item in items:
                    item['date'] = date_str
                    all_orders.append(item)
        except Exception as e:
            print(f"Error loading {f}: {e}")
    return pd.DataFrame(all_orders)

def parse_daily_reports():
    files = sorted(glob.glob(os.path.join(BACKTEST_DIR, 'report_orders_*.md')))
    daily_stats = []
    
    current_cash = 600000.0
    
    for f in files:
        date_str = re.search(r'report_orders_(\d+).md', f).group(1)
        with open(f, 'r') as file:
            content = file.read()
            
        # Split by stock sections
        sections = content.split('### ')[1:]
        
        holdings_value = 0.0
        
        for section in sections:
            # Extract Quantity
            qty_match = re.search(r'Quantity:\s*(\d+)\s*shares', section)
            qty = int(qty_match.group(1)) if qty_match else 0
            
            if 'BUY ORDER FILLED' in section:
                # Extract Cost
                cost_match = re.search(r'Cost:\s*¥([\d,]+\.?\d*)', section)
                cost = float(cost_match.group(1).replace(',', '')) if cost_match else 0.0
                current_cash -= cost
                
                # For buy day, we value it at Close Price (Current Price)
                price_match = re.search(r'Current Price:\s*¥([\d,]+\.?\d*)', section)
                price = float(price_match.group(1).replace(',', '')) if price_match else 0.0
                holdings_value += qty * price
                
            elif 'SELL ORDER FILLED' in section:
                # Extract Exit Price
                exit_price_match = re.search(r'Exit Price:\s*¥([\d,]+\.?\d*)', section)
                exit_price = float(exit_price_match.group(1).replace(',', '')) if exit_price_match else 0.0
                revenue = qty * exit_price
                current_cash += revenue
                # Sold stock has 0 holding value
                
            else:
                # Holding
                price_match = re.search(r'Current Price:\s*¥([\d,]+\.?\d*)', section)
                price = float(price_match.group(1).replace(',', '')) if price_match else 0.0
                holdings_value += qty * price
        
        total_value = current_cash + holdings_value
        daily_stats.append({
            'date': date_str,
            'cash': current_cash,
            'holdings_value': holdings_value,
            'total_value': total_value
        })
        
    return pd.DataFrame(daily_stats)

def parse_trades():
    files = sorted(glob.glob(os.path.join(BACKTEST_DIR, 'report_orders_*.md')))
    trades = []
    
    # Track open positions: symbol -> {buy_date, buy_price, qty}
    open_positions = {}
    
    for f in files:
        date_str = re.search(r'report_orders_(\d+).md', f).group(1)
        with open(f, 'r') as file:
            content = file.read()
            
        sections = content.split('### ')[1:]
        
        for section in sections:
            lines = section.split('\n')
            symbol_match = re.search(r'(\d{6}\.[A-Z]{2})', lines[0])
            symbol = symbol_match.group(1) if symbol_match else "Unknown"
            
            # Extract Quantity
            qty_match = re.search(r'Quantity:\s*(\d+)\s*shares', section)
            qty = int(qty_match.group(1)) if qty_match else 0
            
            if 'BUY ORDER FILLED' in section:
                # Extract Cost
                cost_match = re.search(r'Cost:\s*¥([\d,]+\.?\d*)', section)
                cost = float(cost_match.group(1).replace(',', '')) if cost_match else 0.0
                price = cost / qty if qty > 0 else 0
                
                open_positions[symbol] = {
                    'buy_date': date_str,
                    'buy_price': price,
                    'qty': qty
                }
                
            elif 'SELL ORDER FILLED' in section:
                # Extract Exit Price
                exit_price_match = re.search(r'Exit Price:\s*¥([\d,]+\.?\d*)', section)
                exit_price = float(exit_price_match.group(1).replace(',', '')) if exit_price_match else 0.0
                
                # Extract P&L - handle potential markdown bolding
                pnl_match = re.search(r'Realized P&L:\*?\*?\s*([+-]?¥[\d,]+\.?\d*)', section)
                pnl_str = pnl_match.group(1).replace('¥', '').replace(',', '') if pnl_match else "0"
                pnl = float(pnl_str)
                
                # Extract Exit Reason (from notes usually, but here we infer or look for specific text if available)
                # The report format might have "Exit Reason: ..."
                reason_match = re.search(r'Exit Reason:\s*(\w+)', section)
                reason = reason_match.group(1) if reason_match else "Unknown"
                
                buy_info = open_positions.get(symbol, {})
                buy_date = buy_info.get('buy_date', 'Unknown')
                buy_price = buy_info.get('buy_price', 0)
                
                # Calculate hold days
                hold_days = -1
                if buy_date != 'Unknown':
                    from datetime import datetime
                    d1 = datetime.strptime(buy_date, '%Y%m%d')
                    d2 = datetime.strptime(date_str, '%Y%m%d')
                    hold_days = (d2 - d1).days
                
                return_pct = (exit_price - buy_price) / buy_price * 100 if buy_price > 0 else 0
                
                trades.append({
                    'symbol': symbol,
                    'buy_date': buy_date,
                    'sell_date': date_str,
                    'hold_days': hold_days,
                    'buy_price': buy_price,
                    'sell_price': exit_price,
                    'pnl': pnl,
                    'return_pct': return_pct,
                    'reason': reason
                })
                
                if symbol in open_positions:
                    del open_positions[symbol]
                    
    return pd.DataFrame(trades)

def analyze():
    print(f"Analyzing results in: {BACKTEST_DIR}")
    
    # 1. Daily Portfolio Value
    print("\n--- Daily Portfolio Performance ---")
    df_daily = parse_daily_reports()
    if not df_daily.empty:
        initial_capital = 600000.0
        df_daily['return'] = (df_daily['total_value'] - initial_capital) / initial_capital * 100
        print(df_daily[['date', 'total_value', 'return']].to_string(index=False))
        
        final_return = df_daily.iloc[-1]['return']
        print(f"\nFinal Return: {final_return:.2f}%")
    
    # 2. Trade Analysis
    print("\n--- Completed Trades Analysis ---")
    df_trades = parse_trades()
    if not df_trades.empty:
        # Sort by sell date
        df_trades = df_trades.sort_values('sell_date')
        
        # Print details
        print(df_trades[['symbol', 'buy_date', 'sell_date', 'reason', 'return_pct', 'pnl']].to_string(index=False))
        
        # Statistics
        print("\n--- Strategy Statistics ---")
        total_trades = len(df_trades)
        wins = df_trades[df_trades['pnl'] > 0]
        losses = df_trades[df_trades['pnl'] <= 0]
        win_rate = len(wins) / total_trades * 100
        
        avg_win = wins['return_pct'].mean() if not wins.empty else 0
        avg_loss = losses['return_pct'].mean() if not losses.empty else 0
        
        print(f"Total Trades: {total_trades}")
        print(f"Win Rate: {win_rate:.2f}% ({len(wins)} Wins, {len(losses)} Losses)")
        print(f"Avg Win: {avg_win:.2f}%")
        print(f"Avg Loss: {avg_loss:.2f}%")
        print(f"Max Win: {df_trades['return_pct'].max():.2f}%")
        print(f"Max Loss: {df_trades['return_pct'].min():.2f}%")
        
    else:
        print("No completed trades found.")

if __name__ == "__main__":
    analyze()
