
import tushare as ts
import os
from dotenv import load_dotenv

load_dotenv()
token = os.getenv("TUSHARE_TOKEN")
if not token:
    print("No token found")
    exit(1)

pro = ts.pro_api(token)

def test_api(api_name, **kwargs):
    try:
        method = getattr(pro, api_name)
        print(f"Testing {api_name} with kwargs={kwargs}...")
        df = method(**kwargs)
        print(f"Success! {api_name} returned {len(df)} rows.")
        if not df.empty:
            print(df.head())
    except Exception as e:
        print(f"Failed {api_name}: {e}")

# Test concept with different src
test_api('concept', src='ts')
test_api('concept', src='10jqka')
test_api('concept', src='eastmoney')
test_api('concept', src='sina')

# Test other potential names
test_api('theme', src='eastmoney')

# Test cyq_chips (daily chip distribution)
test_api('cyq_chips', ts_code='603130.SH', start_date='20251215', end_date='20251215')

# Test custom TushareFetcher get_chip_distribution under a frozen target date context (with Akshare fallback)
try:
    import sys
    sys.path.insert(0, 'utils/daily_stock_analysis')
    from src.services.history_loader import set_frozen_target_date, reset_frozen_target_date
    from data_provider.tushare_fetcher import TushareFetcher
    from datetime import date

    print("\nTesting TushareFetcher.get_chip_distribution with historical frozen date & Akshare Fallback...")
    fetcher = TushareFetcher()
    token = set_frozen_target_date(date(2025, 12, 15))
    try:
        # First call: will attempt Tushare, fail, set self._has_chips_privilege=False, and fallback to Akshare
        print("--- First Call (triggering Tushare fail & Akshare fallback) ---")
        chip1 = fetcher.get_chip_distribution("603130.SH")
        print("TushareFetcher get_chip_distribution response 1:", chip1)
        if chip1:
            print(f"Success! Date={chip1.date}, Avg Cost={chip1.avg_cost}, Profit={chip1.profit_ratio:.2%}")
            
        # Second call: will skip Tushare completely and fallback to Akshare instantly
        print("\n--- Second Call (skipping Tushare completely & calling Akshare instantly) ---")
        chip2 = fetcher.get_chip_distribution("603130.SH")
        print("TushareFetcher get_chip_distribution response 2:", chip2)
        if chip2:
            print(f"Success! Date={chip2.date}, Avg Cost={chip2.avg_cost}, Profit={chip2.profit_ratio:.2%}")
    finally:
        reset_frozen_target_date(token)
except Exception as e:
    print("TushareFetcher get_chip_distribution test failed:", e)
