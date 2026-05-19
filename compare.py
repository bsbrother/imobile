import glob
import json
import os

dir_curr = 'backtest/backtest_results/20250101_20260430_ts_month_src'
dir_back = 'backups/20250101_20260430_ts_month_src_01_return_22.12'

def extract_details(d):
    summary = {}
    files = sorted(glob.glob(os.path.join(d, 'smart_orders_*.json')))
    for f in files:
        date = os.path.basename(f).split('_')[2].split('.')[0]
        with open(f, 'r') as file:
            try:
                data = json.load(file)
            except:
                continue
                
        # Also grab pick_stocks to see the strategy/source if possible, or just the stocks
        pick_file = os.path.join(d, f'pick_stocks_{date}.json')
        stocks = []
        if os.path.exists(pick_file):
            with open(pick_file, 'r') as pf:
                try:
                    pdata = json.load(pf)
                    stocks = [s['symbol'] for s in pdata.get('selected_stocks', [])]
                except:
                    pass
                    
        orders = [o['symbol'] for o in data.get('smart_orders', [])]
        summary[date] = {
            'pattern': data.get('market_pattern', ''),
            'orders': orders,
            'picks': stocks
        }
    return summary

curr = extract_details(dir_curr)
back = extract_details(dir_back)

print("=== DIFFERENCES ===")
dates = sorted(set(curr.keys()) | set(back.keys()))
for date in dates:
    c = curr.get(date)
    b = back.get(date)
    
    if not c or not b:
        print(f"{date}: Missing in one of the directories. Curr={bool(c)}, Back={bool(b)}")
        continue
        
    diffs = []
    if c['pattern'] != b['pattern']:
        diffs.append(f"Regime: {b['pattern']} -> {c['pattern']}")
        
    if set(c['picks']) != set(b['picks']):
        diffs.append(f"Picks: {b['picks']} -> {c['picks']}")
        
    if set(c['orders']) != set(b['orders']):
        diffs.append(f"Orders: {len(b['orders'])} -> {len(c['orders'])}")
        
    if diffs:
        print(f"{date}: " + " | ".join(diffs))
