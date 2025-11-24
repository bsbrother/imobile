import adata
import pandas as pd
from datetime import datetime

def load_dc_data():
    print("Loading DC Data...")
    try:
        # Get names
        concepts = adata.stock.info.all_concept_code_east()
        code_to_name = dict(zip(concepts['index_code'], concepts['name']))
        print(f"Loaded {len(code_to_name)} concept names.")
        
        # Get market data
        df = adata.stock.market.get_market_concept_current_east()
        if df.empty:
            print("DC Market data is empty.")
            return []
            
        print(f"Loaded {len(df)} market records.")
        print(df.head(2))
        
        # Sort by change_pct desc
        df = df.sort_values('change_pct', ascending=False).head(20)
        
        sectors = []
        for _, row in df.iterrows():
            name = code_to_name.get(row['index_code'], row['index_code'])
            sectors.append({
                "ts_code": row['index_code'],
                "name": name,
                "pct_chg": row['change_pct'],
                "close": row['price'],
                "trade_date": str(row['trade_date']),
                "source": "DC"
            })
        return sectors
    except Exception as e:
        print(f"Error loading DC data: {e}")
        return []

def test_chart_data(code, source):
    print(f"\nTesting Chart Data for {code} ({source})...")
    try:
        clean_code = code.split('.')[0]
        df = pd.DataFrame()
        if source == 'DC' or code.startswith('BK'):
            df = adata.stock.market.get_market_concept_east(index_code=clean_code, k_type=1)
        else:
            df = adata.stock.market.get_market_concept_ths(index_code=clean_code, k_type=1)
            
        if not df.empty:
            print(f"Loaded {len(df)} chart records.")
            print(df.tail(2))
        else:
            print("Chart data is empty.")
    except Exception as e:
        print(f"Error loading chart data: {e}")

if __name__ == "__main__":
    sectors = load_dc_data()
    print(f"\nFetched {len(sectors)} top DC sectors.")
    if sectors:
        top_sector = sectors[0]
        print(f"Top Sector: {top_sector}")
        test_chart_data(top_sector['ts_code'], 'DC')
        
    # Test THS chart too
    test_chart_data('886013', 'THS')
