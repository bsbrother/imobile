import os
import re
import glob
from collections import defaultdict

RESULTS_DIR = '/home/kasm-user/apps/imobile/backtest/backtest_results'

def parse_percentage(value_str):
    """Parses a percentage string like '1.85%' or '-1.52%' to a float."""
    try:
        return float(value_str.replace('%', '').strip())
    except ValueError:
        return None

def calculate_monthly_returns(dir_path):
    files = sorted(glob.glob(os.path.join(dir_path, 'report_orders_*.md')))
    if not files:
        return None
        
    current_cash = 600000.0
    monthly_values = {}
    open_positions = {}
    total_value = current_cash
    
    for f in files:
        date_str = re.search(r'report_orders_(\d+).md', f).group(1)
        month_str = date_str[:6]
        
        with open(f, 'r', encoding='utf-8') as file:
            content = file.read()
            
        sections = content.split('### ')[1:]
        
        for section in sections:
            lines = section.split('\n')
            if not lines: continue
            
            symbol_match = re.search(r'(\d{6}\.[A-Z]{2})', lines[0])
            symbol = symbol_match.group(1) if symbol_match else "UNKNOWN"
            
            qty_match = re.search(r'Quantity:\s*(\d+)\s*shares', section)
            qty = int(qty_match.group(1)) if qty_match else 0
            
            if 'BUY ORDER FILLED' in section:
                cost_match = re.search(r'Cost:\s*¥([\d,]+\.?\d*)', section)
                cost = float(cost_match.group(1).replace(',', '')) if cost_match else 0.0
                current_cash -= cost
                
                price_match = re.search(r'Current Price:\s*¥([\d,]+\.?\d*)', section)
                price = float(price_match.group(1).replace(',', '')) if price_match else (cost/qty if qty > 0 else 0)
                
                open_positions[symbol] = {'qty': qty, 'price': price}
                
            elif 'SELL ORDER FILLED' in section:
                exit_price_match = re.search(r'Exit Price:\s*¥([\d,]+\.?\d*)', section)
                exit_price = float(exit_price_match.group(1).replace(',', '')) if exit_price_match else 0.0
                revenue = qty * exit_price
                current_cash += revenue
                
                if symbol in open_positions:
                    del open_positions[symbol]
                
            else:
                price_match = re.search(r'Current Price:\s*¥([\d,]+\.?\d*)', section)
                if price_match and symbol in open_positions:
                    open_positions[symbol]['price'] = float(price_match.group(1).replace(',', ''))
        
        holdings_value = sum(pos['qty'] * pos['price'] for pos in open_positions.values())
        total_value = current_cash + holdings_value
        monthly_values[month_str] = total_value
        
    initial_capital = 600000.0
    return_pct = (total_value - initial_capital) / initial_capital * 100
    
    monthly_returns = {}
    prev_val = initial_capital
    for m in sorted(monthly_values.keys()):
        val = monthly_values[m]
        pct = (val - prev_val) / prev_val * 100
        monthly_returns[m] = pct
        prev_val = val
        
    return return_pct, monthly_returns

def main():
    if not os.path.exists(RESULTS_DIR):
        print(f"Directory not found: {RESULTS_DIR}")
        return

    # Dictionary to store results: results[period][method] = {strategy_ret, sse_ret, csi300_ret}
    results = defaultdict(dict)
    
    # Regex to parse directory name: yyyymmdd_yyyymmdd_ts_xx
    dir_pattern = re.compile(r'(\d{8})_(\d{8})_(ts_.+)')

    for entry in os.listdir(RESULTS_DIR):
        full_path = os.path.join(RESULTS_DIR, entry)
        if not os.path.isdir(full_path):
            continue

        match = dir_pattern.match(entry)
        if not match:
            continue

        start_date, end_date, method = match.groups()
        period_key = f"{start_date}_{end_date}"
        
        report_file = os.path.join(full_path, f"report_period_{period_key}.md")
        
        monthly_str = ""
        calc_result = calculate_monthly_returns(full_path)
        
        if calc_result is not None:
            partial_ret, monthly_returns = calc_result
            monthly_parts = [f"{m}: {pct:.2f}%" for m, pct in monthly_returns.items()]
            monthly_str = " [" + ", ".join(monthly_parts) + "]"
        
        if os.path.exists(report_file):
            with open(report_file, 'r', encoding='utf-8') as f:
                content = f.read()

            # Parse Benchmark Comparison table
            # | Metric | Strategy | SSE Composite | CSI 300 |
            # | **Total Return** | 0.33% | 1.85% | 1.49% |
            table_pattern = re.compile(r'\|\s*\*\*Total Return\*\*\s*\|\s*([^|\n]+)\|\s*([^|\n]+)\|\s*([^|\n]+)\|')
            table_match = table_pattern.search(content)

            if table_match:
                strategy_ret = table_match.group(1).strip()
                sse_ret = table_match.group(2).strip()
                csi300_ret = table_match.group(3).strip()

                results[period_key][method] = {
                    'strategy': f"{strategy_ret}{monthly_str}",
                    'sse': sse_ret,
                    'csi': csi300_ret
                }
        else:
            if calc_result is not None:
                results[period_key][method] = {
                    'strategy': f"{partial_ret:.2f}% (Running){monthly_str}",
                    'sse': 'N/A',
                    'csi': 'N/A'
                }

    # Sort periods
    sorted_periods = sorted(results.keys())

    print(f"{'Period':<20} | {'Index Returns (SSE, CSI300)':<30} | {'Method Returns'}")
    print("-" * 100)

    for period in sorted_periods:
        period_data = results[period]
        
        # Group methods by index returns
        index_groups = defaultdict(list)
        
        methods = sorted(period_data.keys())
        for m in methods:
            sse = period_data[m]['sse']
            csi = period_data[m]['csi']
            index_groups[(sse, csi)].append(m)
        
        index_display_lines = []
        if len(index_groups) == 1:
            # All consistent
            sse, csi = list(index_groups.keys())[0]
            index_display = f"SSE: {sse}, CSI300: {csi}"
        else:
            # Mismatch found
            parts = []
            for (sse, csi), ms in index_groups.items():
                ms_str = ", ".join(ms)
                parts.append(f"SSE: {sse}, CSI300: {csi} ({ms_str})")
            index_display = f"MISMATCH! {'; '.join(parts)}"

        # Start date to End date formatting
        s_date = period.split('_')[0]
        e_date = period.split('_')[1]
        period_display = f"{s_date} to {e_date}"

        print(f"From {s_date} to {e_date},")
        print(f"- index({index_display}) return.") # Ensure all methods have consistent index returns
        
        method_returns = []
        for m in methods:
            method_returns.append(f"{m}: {period_data[m]['strategy']}")
        
        print(f"- {', '.join(method_returns)}")
        print("-" * 50)

if __name__ == "__main__":
    main()
