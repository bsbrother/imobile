
import tushare as ts
import os
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
if TUSHARE_TOKEN:
    ts.set_token(TUSHARE_TOKEN)
    pro = ts.pro_api()
    
    print("Testing dc_index...")
    try:
        # Fetch DC concepts
        df_dc = pro.dc_index(exchange='A', type='N')
        print(f"DC Concepts count: {len(df_dc)}")
        print(df_dc.head())
        
        first_dc_code = df_dc.iloc[0]['ts_code']
        print(f"First DC Code: {first_dc_code}")
        
        # Try to fetch daily data for this code
        print(f"Testing daily data for {first_dc_code}...")
        # Try generic index_daily
        try:
            df_daily = pro.index_daily(ts_code=first_dc_code, start_date='20231101', end_date='20231120')
            print(f"index_daily result: {len(df_daily)} rows")
        except Exception as e:
            print(f"index_daily failed: {e}")
            
        # Try dc_daily if it exists (guessing)
        try:
            df_daily_dc = pro.dc_daily(ts_code=first_dc_code, start_date='20231101', end_date='20231120')
            print(f"dc_daily result: {len(df_daily_dc)} rows")
        except Exception as e:
            print(f"dc_daily failed: {e}")

    except Exception as e:
        print(f"dc_index failed: {e}")

    print("\nTesting ths_daily limit...")
    try:
        # Fetch THS concepts count
        df_ths = pro.ths_index(exchange='A', type='N')
        print(f"THS Concepts count: {len(df_ths)}")
        
        # Try to fetch one day of data for ALL THS concepts
        trade_date = '20231120'
        df_ths_daily = pro.ths_daily(trade_date=trade_date)
        print(f"ths_daily for {trade_date} count: {len(df_ths_daily)}")
        
        if len(df_ths_daily) >= 3000:
            print("HIT LIMIT of 3000!")
        else:
            print("Under limit.")
            
    except Exception as e:
        print(f"ths test failed: {e}")

else:
    print("TUSHARE_TOKEN not found")
