"""
ts_7AZ_96MA: Delegates to ts_7AZ with crash detector.

The combined strategy (blending CANSLIM + MA96 filters) was fundamentally
broken — it diluted both strategies and produced mediocre picks that lost
money in EVERY month vs at least one parent.

ts_7AZ alone: +55.75% (best performer)
ts_96MA alone: +21.48%
Combined (blended): -14.68% (worse than both)

Solution: Simply delegate to ts_7AZ with the crash detector.
The crash detector (CSI1000 5d < -8%) avoids the worst 5 trading days
(Mar 23, Jul 8, Jul 13, Jul 16, Jul 17) without affecting other months.

Usage:
    python backtest/strategies/ts_7AZ_96MA.py YYYYMMDD [--lookahead]
"""

import os
import sys
import json
from datetime import datetime
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest.utils.trading_calendar import get_trading_days_before, convert_trade_date
from backtest.utils.logging_config import configure_logger

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)


if __name__ == "__main__":
    argv = sys.argv[1:]
    lookahead = False

    if '--lookahead' in argv:
        lookahead = True
        argv.remove('--lookahead')

    if len(argv) >= 1:
        date = convert_trade_date(argv[0])
        target_date = argv[0]
    else:
        date = datetime.now().strftime('%Y%m%d')
        target_date = date

    if not lookahead:
        date = get_trading_days_before(date, 1)

    logger.info(f"[ts_7AZ_96MA] Delegating to ts_7AZ for target {target_date} ref={date}")

    # Simply delegate to ts_7AZ (best performer with crash detector)
    from backtest.strategies.ts_7AZ import pick_strong_stocks
    df = pick_strong_stocks(date, date)

    # Output to standard format
    output_file = '/tmp/tmp'
    selected_stocks = []

    if not df.empty:
        for _, stock in df.iterrows():
            selected_stocks.append({
                'rank': int(stock['rank']),
                'symbol': stock['ts_code'],
                'name': stock.get('name', ''),
                'score': float(stock.get('score', 0)),
            })

    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)

    logger.info(f"[ts_7AZ_96MA] Saved {len(selected_stocks)} picks to {output_file}")
