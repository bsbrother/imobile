#!/usr/bin/python3
"""
Backtest Monthly Results Analyzer

Usage:
  python utils/result_ts_month_src.py [dir]

Arguments:
  dir   Path to a backtest results directory (e.g. backups/2025_noai_search)
        or a parent directory containing multiple result subdirectories.
        Default: backtest/backtest_results

Examples:
  python utils/result_ts_month_src.py
  python utils/result_ts_month_src.py backups/2025_noai_search
  python utils/result_ts_month_src.py backtest/backtest_results/20250101_20251231_ts_month_src
"""
import os
import sys
import re
import glob
import json
from collections import defaultdict

DEFAULT_RESULTS_DIR = 'backtest/backtest_results'
CONFIG_PATH = 'backtest/config.json'

def parse_currency(value_str):
    """Parses a currency string like '¥600,000.00' to a float."""
    try:
        # Remove ¥ and commas, strip whitespace
        return float(value_str.replace('¥', '').replace(',', '').strip())
    except ValueError:
        return None

def parse_percentage(value_str):
    """Parses a percentage string like '1.85%' or '-1.52%' to a float."""
    try:
        return float(value_str.replace('%', '').strip())
    except ValueError:
        return None

def extract_monthly_stats(file_path):
    """Extract stats from a single report file."""
    with open(file_path, 'rb') as f:
        content = f.read().decode('utf-8', errors='ignore')
    
    # Extract date from filename
    filename = os.path.basename(file_path)
    date_match = re.search(r'report_orders_(\d+)\.md', filename)
    if not date_match:
        return None
    date_str = date_match.group(1)  # YYYYMMDD
    year_month = date_str[:6]  # YYYYMM
    
    # Initialize defaults
    stats = {
        'date': date_str,
        'year_month': year_month,
        'true_total_portfolio': None,  # Includes Unrealized P&L
        'cumulative_realized_pnl': None,
        'total_unrealized_pnl': None,
        'total_pnl': None  # computed later
    }
    
    # True Total Portfolio Value: **True Total Portfolio Value:** ¥601,577.00 *(Includes Unrealized P&L)*
    true_match = re.search(r'\*\*True Total Portfolio Value:\*\*\s*[^0-9]*([-]?[\d,]+\.?\d*)', content)
    if true_match:
        stats['true_total_portfolio'] = parse_currency(true_match.group(1))
    
    # Total Unrealized P&L: **Total Unrealized P&L:** ¥1,577.00
    unrealized_match = re.search(r'\*\*Total Unrealized P&L:\*\*\s*[^0-9]*([-]?[\d,]+\.?\d*)', content)
    if unrealized_match:
        stats['total_unrealized_pnl'] = parse_currency(unrealized_match.group(1))
    
    # Cumulative Realized P&L: **Cumulative Realized P&L:** ¥0.00
    realized_match = re.search(r'\*\*Cumulative Realized P&L:\*\*\s*[^0-9]*([-]?[\d,]+\.?\d*)', content)
    if realized_match:
        stats['cumulative_realized_pnl'] = parse_currency(realized_match.group(1))
    
    # Compute total P&L (realized + unrealized)
    if stats['cumulative_realized_pnl'] is not None and stats['total_unrealized_pnl'] is not None:
        stats['total_pnl'] = stats['cumulative_realized_pnl'] + stats['total_unrealized_pnl']
    
    return stats

def calculate_monthly_end_stats(dir_path):
    """Calculate end-of-month stats from daily reports."""
    files = sorted(glob.glob(os.path.join(dir_path, 'report_orders_*.md')))
    if not files:
        return None
    
    # Group by year_month and take the last file of each month
    monthly_files = {}
    for f in files:
        stats = extract_monthly_stats(f)
        if stats is None:
            continue
        ym = stats['year_month']
        # Keep the latest date for each month
        if ym not in monthly_files or stats['date'] > monthly_files[ym]['date']:
            monthly_files[ym] = stats
    
    # Sort months chronologically
    sorted_months = sorted(monthly_files.keys())
    monthly_stats = [monthly_files[m] for m in sorted_months]
    
    return monthly_stats

def main():
    target_dir = DEFAULT_RESULTS_DIR
    if len(sys.argv) > 1:
        target_dir = sys.argv[1]
    
    if not os.path.exists(target_dir):
        print(f"Directory not found: {target_dir}")
        return
    
    # Load initial cash from config
    initial_cash = 600000.0  # default
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                config = json.load(f)
            initial_cash = float(config.get('init_info', {}).get('initial_cash', 600000))
        except Exception as e:
            print(f"Warning: could not read initial_cash from config: {e}")
    
    # Find result directories
    dirs_to_process = []
    dir_pattern = re.compile(r'^(\d{4}-?\d{2}-?\d{2})_(\d{4}-?\d{2}-?\d{2})_(ts_.+)$')
    basename = os.path.basename(os.path.normpath(target_dir))
    
    if dir_pattern.match(basename):
        # Arg is a single result directory matching the standard pattern
        dirs_to_process.append(os.path.normpath(target_dir))
    elif glob.glob(os.path.join(target_dir, 'report_orders_*.md')):
        # Arg is a directory directly containing report files (e.g. backups/2025_noai_search)
        dirs_to_process.append(os.path.normpath(target_dir))
    else:
        # Arg is a parent dir; scan for subdirectories matching the pattern
        for entry in os.listdir(target_dir):
            full_path = os.path.join(target_dir, entry)
            if os.path.isdir(full_path) and dir_pattern.match(entry):
                dirs_to_process.append(full_path)
    
    if not dirs_to_process:
        print(f"No valid backtest result directories found in {target_dir}")
        return
    
    # Process each directory
    for full_path in dirs_to_process:
        entry = os.path.basename(full_path)
        match = dir_pattern.match(entry)
        if match:
            start_date, end_date, method = match.groups()
            start_date = start_date.replace('-', '')
            end_date = end_date.replace('-', '')
        else:
            # Directory doesn't match pattern (e.g. backups/2025_noai_search)
            # Extract dates from the report files themselves
            report_files = sorted(glob.glob(os.path.join(full_path, 'report_orders_*.md')))
            if not report_files:
                print(f"No report files found in {full_path}")
                continue
            first_date = re.search(r'report_orders_(\d+)\.md', os.path.basename(report_files[0])).group(1)
            last_date = re.search(r'report_orders_(\d+)\.md', os.path.basename(report_files[-1])).group(1)
            start_date = first_date
            end_date = last_date
            # Try to read method/strategy from the period report
            period_report = os.path.join(full_path, f'report_period_{first_date}_{last_date}.md')
            if os.path.exists(period_report):
                with open(period_report, 'r') as f:
                    pcontent = f.read(500)
                m = re.search(r'\*\*Strategy:\*\*\s*(\S+)', pcontent)
                method = m.group(1) if m else 'unknown'
            else:
                method = 'unknown'
        period_key = f"{start_date}_{end_date}"
        
        print(f"\n{'='*60}")
        print(f"Backtest Period: {start_date} to {end_date} | Method: {method}")
        print(f"{'='*60}")
        
        monthly_stats = calculate_monthly_end_stats(full_path)
        if monthly_stats is None:
            print("No monthly data found.")
            continue
        
        # Print header
        print(f"{'Month':<8} | {'Initial Cap':>12} | {'End Value':>12} | {'Return %':>8} | {'Realized P&L':>12} | {'Unrealized P&L':>14} | {'Total P&L':>10}")
        print("-" * 100)
        
        prev_end_value = initial_cash  # starting capital for first month
        for stats in monthly_stats:
            month = stats['year_month']
            end_value = stats['true_total_portfolio'] if stats['true_total_portfolio'] is not None else 0.0
            realized = stats['cumulative_realized_pnl'] if stats['cumulative_realized_pnl'] is not None else 0.0
            unrealized = stats['total_unrealized_pnl'] if stats['total_unrealized_pnl'] is not None else 0.0
            total_pnl = stats['total_pnl'] if stats['total_pnl'] is not None else (realized + unrealized)
            initial_cap = prev_end_value
            return_pct = ((end_value - initial_cap) / initial_cap * 100) if initial_cap != 0 else 0.0
            
            print(f"{month:<8} | {initial_cap:>12.2f} | {end_value:>12.2f} | {return_pct:>8.2f}% | {realized:>12.2f} | {unrealized:>14.2f} | {total_pnl:>10.2f}")
            
            prev_end_value = end_value  # for next month
        
        # Overall period summary
        if monthly_stats:
            first_month = monthly_stats[0]['year_month']
            last_month = monthly_stats[-1]['year_month']
            last_end = monthly_stats[-1]['true_total_portfolio'] if monthly_stats[-1]['true_total_portfolio'] is not None else 0.0
            total_return_pct = ((last_end - initial_cash) / initial_cash * 100) if initial_cash != 0 else 0.0
            print("-" * 100)
            print(f"OVERALL {start_date} to {end_date}:")
            print(f"  Initial Capital: ¥{initial_cash:,.2f}")
            print(f"  End Portfolio Value: ¥{last_end:,.2f}")
            print(f"  Total Return: {total_return_pct:.2f}%")
            print(f"  Realized P&L: ¥{(monthly_stats[-1]['cumulative_realized_pnl'] or 0):,.2f}")
            print(f"  Unrealized P&L: ¥{(monthly_stats[-1]['total_unrealized_pnl'] or 0):,.2f}")
            print(f"  Total P&L: ¥{(monthly_stats[-1]['total_pnl'] or 0):,.2f}")

if __name__ == "__main__":
    main()