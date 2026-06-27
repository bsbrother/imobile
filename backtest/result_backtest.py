#!/usr/bin/python3
"""
Backtest Results Analyzer — monthly P&L breakdown + index comparison.

Usage:
  python backtest/result_backtest.py [dir]

Arguments:
  dir   Path to a backtest results directory
        or a parent directory containing multiple result subdirectories.
        Default: backtest/results

Examples:
  python backtest/result_backtest.py
  python backtest/result_backtest.py backtest/results/20250101_20260612_ts_auto
"""
import os
import sys
import re
import glob
import json

DEFAULT_RESULTS_DIR = 'backtest/results'
CONFIG_PATH = 'backtest/config.json'


def parse_currency(value_str):
    try:
        return float(value_str.replace('¥', '').replace(',', '').strip())
    except ValueError:
        return None


def parse_percentage(value_str):
    try:
        return float(value_str.replace('%', '').strip())
    except ValueError:
        return None


def extract_monthly_stats(file_path):
    """Extract portfolio stats from a single report_orders file."""
    with open(file_path, 'rb') as f:
        content = f.read().decode('utf-8', errors='ignore')

    filename = os.path.basename(file_path)
    date_match = re.search(r'report_orders_(\d+)\.md', filename)
    if not date_match:
        return None
    date_str = date_match.group(1)
    year_month = date_str[:6]

    stats = {
        'date': date_str,
        'year_month': year_month,
        'true_total_portfolio': None,
        'cumulative_realized_pnl': None,
        'total_unrealized_pnl': None,
        'total_pnl': None,
    }

    m = re.search(r'\*\*True Total Portfolio Value:\*\*\s*[^0-9]*([-]?[\d,]+\.?\d*)', content)
    if m:
        stats['true_total_portfolio'] = parse_currency(m.group(1))

    m = re.search(r'\*\*Total Unrealized P&L:\*\*\s*[^0-9]*([-]?[\d,]+\.?\d*)', content)
    if m:
        stats['total_unrealized_pnl'] = parse_currency(m.group(1))

    m = re.search(r'\*\*Cumulative Realized P&L:\*\*\s*[^0-9]*([-]?[\d,]+\.?\d*)', content)
    if m:
        stats['cumulative_realized_pnl'] = parse_currency(m.group(1))

    if stats['cumulative_realized_pnl'] is not None and stats['total_unrealized_pnl'] is not None:
        stats['total_pnl'] = stats['cumulative_realized_pnl'] + stats['total_unrealized_pnl']

    return stats


def calculate_monthly_end_stats(dir_path):
    """Calculate end-of-month portfolio stats."""
    files = sorted(glob.glob(os.path.join(dir_path, 'report_orders_*.md')))
    if not files:
        return None

    monthly_files = {}
    for f in files:
        stats = extract_monthly_stats(f)
        if stats is None:
            continue
        ym = stats['year_month']
        if ym not in monthly_files or stats['date'] > monthly_files[ym]['date']:
            monthly_files[ym] = stats

    sorted_months = sorted(monthly_files.keys())
    return [monthly_files[m] for m in sorted_months]


def parse_index_comparison(report_file):
    """Parse benchmark comparison table from period report.
    Returns dict with strategy/sse/csi300/csi500 returns or None."""
    if not os.path.exists(report_file):
        return None
    with open(report_file, 'r') as f:
        content = f.read()

    # Try 4-column format first (SSE + CSI300 + CSI500)
    # | **Total Return** | 185.58% | 21.58% | 24.33% | -0.31% |
    pattern4 = re.compile(
        r'\|\s*\*\*Total Return\*\*\s*\|\s*([^|\n]+)\|\s*([^|\n]+)\|\s*([^|\n]+)\|\s*([^|\n]+)\|'
    )
    # Fallback to 3-column format (SSE + CSI300 only)
    # | **Total Return** | 185.58% | 21.58% | 24.33% |
    pattern3 = re.compile(
        r'\|\s*\*\*Total Return\*\*\s*\|\s*([^|\n]+)\|\s*([^|\n]+)\|\s*([^|\n]+)\|'
    )
    
    # Try 4-column first
    m = pattern4.search(content)
    if m:
        return {
            'strategy': m.group(1).strip(),
            'sse': m.group(2).strip(),
            'csi300': m.group(3).strip(),
            'csi500': m.group(4).strip(),
        }
    
    # Fallback to 3-column
    m = pattern3.search(content)
    if m:
        return {
            'strategy': m.group(1).strip(),
            'sse': m.group(2).strip(),
            'csi300': m.group(3).strip(),
            'csi500': 'N/A',
        }
    
    return None


def main():
    target_dir = DEFAULT_RESULTS_DIR
    if len(sys.argv) > 1:
        target_dir = sys.argv[1]

    if not os.path.exists(target_dir):
        print(f"Directory not found: {target_dir}")
        return

    # Load initial cash
    initial_cash = 600000.0
    if os.path.exists(CONFIG_PATH):
        try:
            with open(CONFIG_PATH, 'r') as f:
                config = json.load(f)
            initial_cash = float(config.get('init_info', {}).get('initial_cash', 600000))
        except Exception:
            pass

    # Find result directories
    dirs_to_process = []
    dir_pattern = re.compile(r'^(\d{4}-?\d{2}-?\d{2})_(\d{4}-?\d{2}-?\d{2})_(ts_.+)$')
    basename = os.path.basename(os.path.normpath(target_dir))

    if dir_pattern.match(basename):
        dirs_to_process.append(os.path.normpath(target_dir))
    elif glob.glob(os.path.join(target_dir, 'report_orders_*.md')):
        dirs_to_process.append(os.path.normpath(target_dir))
    else:
        for entry in os.listdir(target_dir):
            if entry == 'daily':
                continue  # daily trading output, not a backtest result
            full_path = os.path.join(target_dir, entry)
            if os.path.isdir(full_path) and dir_pattern.match(entry):
                dirs_to_process.append(full_path)

    if not dirs_to_process:
        print(f"No valid backtest result directories found in {target_dir}")
        return

    for full_path in dirs_to_process:
        entry = os.path.basename(full_path)
        match = dir_pattern.match(entry)
        if match:
            start_date, end_date, method = match.groups()
            start_date = start_date.replace('-', '')
            end_date = end_date.replace('-', '')
        else:
            report_files = sorted(glob.glob(os.path.join(full_path, 'report_orders_*.md')))
            if not report_files:
                print(f"No report files found in {full_path}")
                continue
            first_date = re.search(r'report_orders_(\d+)\.md', os.path.basename(report_files[0])).group(1)
            last_date = re.search(r'report_orders_(\d+)\.md', os.path.basename(report_files[-1])).group(1)
            start_date = first_date
            end_date = last_date
            period_report = os.path.join(full_path, f'report_period_{first_date}_{last_date}.md')
            if os.path.exists(period_report):
                with open(period_report, 'r') as f:
                    pcontent = f.read(500)
                m = re.search(r'\*\*Strategy:\*\*\s*(\S+)', pcontent)
                method = m.group(1) if m else 'unknown'
            else:
                method = 'unknown'
        period_key = f"{start_date}_{end_date}"

        # ── Index comparison ──
        report_file = os.path.join(full_path, f"report_period_{period_key}.md")
        index_data = parse_index_comparison(report_file)

        print(f"\n{'='*70}")
        print(f"Backtest: {start_date} → {end_date}  |  Strategy: {method}")
        if index_data:
            print(f"Index:  SSE {index_data['sse']}  |  CSI300 {index_data['csi300']}  |  CSI500 {index_data['csi500']}  |  Strategy {index_data['strategy']}")
        print(f"{'='*70}")

        # ── Monthly breakdown ──
        monthly_stats = calculate_monthly_end_stats(full_path)
        if monthly_stats is None:
            print("No monthly data found.")
            continue

        print(f"{'Month':<8} {'Start Value':>13} {'End Value':>13} {'Return%':>8} {'Realized':>12} {'Unrealized':>12} {'Total P&L':>10}")
        print("-" * 90)

        prev_end_value = initial_cash
        for stats in monthly_stats:
            month = stats['year_month']
            end_value = stats['true_total_portfolio'] or 0.0
            realized = stats['cumulative_realized_pnl'] or 0.0
            unrealized = stats['total_unrealized_pnl'] or 0.0
            total_pnl = stats['total_pnl'] or (realized + unrealized)
            initial_cap = prev_end_value
            return_pct = ((end_value - initial_cap) / initial_cap * 100) if initial_cap != 0 else 0.0

            print(f"{month:<8} ¥{initial_cap:>11,.0f} ¥{end_value:>11,.0f} {return_pct:>7.2f}% ¥{realized:>10,.0f} ¥{unrealized:>10,.0f} ¥{total_pnl:>8,.0f}")
            prev_end_value = end_value

        # ── Overall summary ──
        if monthly_stats:
            last_end = monthly_stats[-1]['true_total_portfolio'] or 0.0
            total_return_pct = ((last_end - initial_cash) / initial_cash * 100) if initial_cash != 0 else 0.0
            print("-" * 90)
            print(f"OVERALL: {start_date} → {end_date}")
            print(f"  Initial:  ¥{initial_cash:,.0f}")
            print(f"  Final:    ¥{last_end:,.0f}")
            print(f"  Return:   {total_return_pct:.2f}%")
            if index_data:
                # Use the script's own computed return (from daily reports)
                # instead of the period report's rounded value for consistency
                strat_ret = total_return_pct
                sse_ret = parse_percentage(index_data['sse'])
                csi300_ret = parse_percentage(index_data['csi300'])
                csi500_ret = parse_percentage(index_data['csi500'])
                if sse_ret is not None:
                    print(f"  vs SSE:    +{strat_ret - sse_ret:.2f}% (strat {strat_ret:.2f}% - SSE {sse_ret:.2f}%)")
                if csi300_ret is not None:
                    print(f"  vs CSI300:  +{strat_ret - csi300_ret:.2f}% (strat {strat_ret:.2f}% - CSI300 {csi300_ret:.2f}%)")
                if csi500_ret is not None:
                    print(f"  vs CSI500:  +{strat_ret - csi500_ret:.2f}% (strat {strat_ret:.2f}% - CSI500 {csi500_ret:.2f}%)")


if __name__ == "__main__":
    main()
