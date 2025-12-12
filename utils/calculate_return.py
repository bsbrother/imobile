import sys
import os
import pandas as pd
import argparse
from datetime import datetime

# Add the project root to the python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../../apps/imobile')))

from backtest.data.provider import AkshareDataProvider
from backtest.utils.trading_calendar import calendar

def calculate_return(symbol, start_date, end_date):
    print(f"Fetching data for {symbol} from {start_date} to {end_date}...")
    
    provider = AkshareDataProvider()
    
    # 1. Get all trading days in range
    try:
        trading_days = calendar.get_trading_days_between(start_date, end_date)
        print(f"Total trading days: {len(trading_days)}")
    except Exception as e:
        print(f"Error getting trading days: {e}")
        trading_days = []
    
    # 2. Fetch stock data
    try:
        # Try with .BJ suffix if it looks like a BSE stock (6 digits starting with 92/8/4)
        # But let's just try both or rely on provider
        if symbol.startswith('92') or symbol.startswith('8') or symbol.startswith('4'):
             try:
                 df = provider.get_ohlcv_data(symbol=f"{symbol}.BJ", start_date=start_date, end_date=end_date)
             except:
                 df = provider.get_ohlcv_data(symbol=symbol, start_date=start_date, end_date=end_date)
        else:
             df = provider.get_ohlcv_data(symbol=symbol, start_date=start_date, end_date=end_date)
             
    except Exception as e:
        print(f"Failed to fetch data: {e}")
        df = pd.DataFrame()

    # 3. Merge and Display
    print(f"\nDaily Breakdown for {symbol}:")
    print(f"{'Date':<12} {'Open':<10} {'Close':<10} {'Change':<10} {'Pct Chg %':<10} {'Status'}")
    print("-" * 80)

    if not df.empty:
        df['trade_date'] = df['trade_date'].astype(str)
        df = df.set_index('trade_date')

    for day in trading_days:
        day_str = str(day)
        if not df.empty and day_str in df.index:
            row = df.loc[day_str]
            open_price = row['open']
            close_price = row['close']
            change = row['change']
            pct_chg = row['pct_chg']
            print(f"{day_str:<12} {open_price:<10.2f} {close_price:<10.2f} {change:<10.2f} {pct_chg:<10.2f} {'Trading'}")
        else:
            print(f"{day_str:<12} {'-':<10} {'-':<10} {'-':<10} {'-':<10} {'No Data (Not Listed/Suspended)'}")

    print("-" * 80)

    # Calculate and print summary
    if not df.empty:
        # Filter df to only include rows within the requested date range (should already be done by provider but good to be safe)
        # and ensure we are using the actual fetched data for calculation
        first_day = df.index[0]
        last_day = df.index[-1]
        first_open = df.iloc[0]['open']
        last_close = df.iloc[-1]['close']
        total_return = (last_close - first_open) / first_open * 100
        
        print(f"\nSummary for {symbol} from {start_date} to {end_date}:")
        print(f"Start Date: {first_day}")
        print(f"End Date:   {last_day}")
        print(f"Start Open: {first_open:.2f}")
        print(f"End Close:  {last_close:.2f}")
        print(f"Total Return (Close - Open) / Open: {total_return:.2f}%")
    else:
        print(f"\nNo trading data found for {symbol} in the specified period.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Calculate stock return.')
    parser.add_argument('symbol', type=str, help='Stock code (e.g., 920116)')
    parser.add_argument('start_date', type=str, help='Start date (YYYYMMDD)')
    parser.add_argument('end_date', type=str, help='End date (YYYYMMDD)')
    
    args = parser.parse_args()
    
    calculate_return(args.symbol, args.start_date, args.end_date)
