import os
import re
import glob

results_dir = "backtest/results_backups/20260101_20260619_ts_7AZ_70.60_baseline"
out_file = "backtest/results/report_trigger_orders.md"

md_files = sorted(glob.glob(os.path.join(results_dir, "report_orders_*.md")))

output_lines = []
output_lines.append("# Trigger Orders Report")
output_lines.append("")

total_buy = 0
total_sell = 0
total_buy_ratio = []
total_sell_ratio = []

for file_path in md_files:
    date_str = re.search(r'report_orders_(\d{8})\.md', file_path).group(1)

    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()

    stocks = re.split(r'^### ', content, flags=re.MULTILINE)[1:]

    day_buys = []
    day_sells = []

    for stock_block in stocks:
        lines = stock_block.strip().split("\n")
        symbol_name = lines[0].strip()

        buy_target = None
        tp_target = None
        sl_target = None
        action = None
        fill_price = None
        exit_reason = None
        ohlc = {}

        for line in lines:
            if "- Buy Price Target:" in line:
                m = re.search(r'¥([\d\.]+)', line)
                if m: buy_target = float(m.group(1))
            elif "- Take Profit:" in line:
                m = re.search(r'¥([\d\.]+)', line)
                if m: tp_target = float(m.group(1))
            elif "- Stop Loss:" in line:
                m = re.search(r'¥([\d\.]+)', line)
                if m: sl_target = float(m.group(1))
            elif "**Execution:** ✅ **BUY ORDER FILLED**" in line:
                action = 'BUY'
            elif "**Execution:** ✅ **SELL ORDER FILLED**" in line:
                action = 'SELL'
            elif "- Fill Price:" in line:
                m = re.search(r'¥([\d\.]+)', line)
                if m: fill_price = float(m.group(1))
            elif "- Exit Price:" in line:
                m = re.search(r'¥([\d\.]+)', line)
                if m: fill_price = float(m.group(1))
            elif "- Exit Reason:" in line:
                exit_reason = line.split(":")[-1].strip()
            elif "Open:" in line and "High:" in line and "Low:" in line and "Close:" in line:
                m = re.search(r'Open: ¥?([\d\.]+), High: ¥?([\d\.]+), Low: ¥?([\d\.]+), Close: ¥?([\d\.]+)', line)
                if m:
                    ohlc = {
                        'O': float(m.group(1)),
                        'H': float(m.group(2)),
                        'L': float(m.group(3)),
                        'C': float(m.group(4))
                    }

        if action and fill_price is not None and ohlc:
            high = ohlc['H']
            low = ohlc['L']
            if high > low:
                ratio = (fill_price - low) / (high - low)
            else:
                ratio = 0.5

            ohlc_str = f"O:{ohlc['O']} H:{ohlc['H']} L:{ohlc['L']} C:{ohlc['C']}"
            ratio_str = f"{ratio*100:.1f}%"

            if action == 'BUY':
                day_buys.append(f"- **{symbol_name}**: Recommend Buy: ¥{buy_target}, OHLC: [{ohlc_str}], Real Trigger (Fill): ¥{fill_price}, Pos in Range: {ratio_str}")
                total_buy += 1
                total_buy_ratio.append(ratio)
            elif action == 'SELL':
                reason_str = f" ({exit_reason})" if exit_reason else ""
                day_sells.append(f"- **{symbol_name}**: TP Target: ¥{tp_target}, SL Target: ¥{sl_target}, OHLC: [{ohlc_str}], Real Exit: ¥{fill_price}{reason_str}, Pos in Range: {ratio_str}")
                total_sell += 1
                total_sell_ratio.append(ratio)

    if day_buys or day_sells:
        output_lines.append(f"## {date_str}")
        if day_buys:
            output_lines.append("### New BUY Orders")
            output_lines.extend(day_buys)
        if day_sells:
            output_lines.append("### TP & SL (Holding Stocks)")
            output_lines.extend(day_sells)
        output_lines.append("")

output_lines.append("## Summary Analysis")
avg_buy_ratio = sum(total_buy_ratio)/len(total_buy_ratio) if total_buy_ratio else 0
avg_sell_ratio = sum(total_sell_ratio)/len(total_sell_ratio) if total_sell_ratio else 0

output_lines.append(f"- **Total BUY Orders Executed**: {total_buy}")
output_lines.append(f"- **Total SELL Orders Executed**: {total_sell}")
output_lines.append(f"- **Average BUY Fill Position in Daily Range**: {avg_buy_ratio*100:.1f}% (0% means bought at the absolute bottom of the day, 100% means bought at the top)")
output_lines.append(f"- **Average SELL Fill Position in Daily Range**: {avg_sell_ratio*100:.1f}% (0% means sold at the bottom, 100% means sold at the top)")
output_lines.append("")
output_lines.append("### Observations on Execution:")
output_lines.append("1. **BUY Executions**: The backtest executes buys typically at the opening price or the trigger price. If it executes near the low, the model captures dips effectively. If it averages around 50%, it executes around the mid-point of the daily volatility.")
output_lines.append("2. **SELL Executions**: For Stop Loss (SL), the exit is expected to be closer to the low of the day (or below). For Take Profit (TP), it should be near the high. If the reason is ORDER_EXPIRED, it likely executes at the opening price of the expiration day.")
output_lines.append("3. **Real Ratio (Fill Price vs Daily High-Low)**: This ratio demonstrates the realism of the backtest. Consistent 0% or 100% ratios would indicate a look-ahead bias, whereas distributed ratios indicate robust, realistic intra-day triggering.")

with open(out_file, "w", encoding="utf-8") as f:
    f.write("\n".join(output_lines))
print(f"Report generated at {out_file}")
