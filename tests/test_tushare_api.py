
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
