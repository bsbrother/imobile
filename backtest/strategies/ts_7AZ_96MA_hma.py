"""
ts_7AZ_96MA_hma: defensive-fallback regime switch.

Runs ts_7AZ_96MA (the canonical 96MA-uptrend / ts_7AZ-else regime switch, 97.54%
full-period). When ts_7AZ_96MA produces ZERO picks — which happens on
defensive / crash / day-momentum-gated days (e.g. Jul 2026 where CSI1000 broke
its 96-day MA and 7AZ's crash detector + momentum gate blocked entries) — this
strategy falls back to ts_hma's defensive picks.

Rationale / evidence (202601-202607 full-year backtests):
- ts_hma (HMA + SuperTrend reversal) is a large-cap-defensive picker that holds
  steady through drawdowns. It returned +7.05% in Jul 2026 — exactly the month
  ts_7AZ_96MA coughed up -5.13%.
- When ts_7AZ_96MA DOES pick (>0), it dominates (97.54% full year) and hma is
  weaker, so hma must NOT override those days.

Delegation rule (per trading day, past data only — no lookahead, no hardcoded
month):
- df = ts_7AZ_96MA picks (via its own 96MA-vs-7AZ regime switch)
- if df is empty/0 picks  -> df = ts_hma.pick_hma_stocks(end_date)
- else                     -> use df unchanged (ts_7AZ_96MA wins)

Usage:
    python backtest/strategies/ts_7AZ_96MA_hma.py YYYYMMDD [--lookahead]
"""

import os
import sys
import json
import pandas as pd
from loguru import logger
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest.utils.trading_calendar import get_trading_days_before, convert_trade_date
from backtest.utils.logging_config import configure_logger

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)


def pick_7az_96ma(end_date: str) -> pd.DataFrame:
    """Delegate to ts_7AZ_96MA's core logic. Returns DataFrame (maybe empty)."""
    from backtest.strategies.ts_7AZ import pick_strong_stocks
    from backtest.strategies.ts_96MA import pick_96mv_stocks
    from backtest.strategies.ts_7AZ_96MA import _regime_96ma

    use_96 = _regime_96ma(end_date)
    if use_96:
        logger.info(f"[ts_7AZ_96MA_hma] ts_7AZ_96MA -> ts_96MA (strong/persistent uptrend)")
        return pick_96mv_stocks(end_date=end_date)
    logger.info(f"[ts_7AZ_96MA_hma] ts_7AZ_96MA -> ts_7AZ (mid-range)")
    return pick_strong_stocks(end_date, end_date, src='ts_7AZ')


def pick_hma_defensive(end_date: str) -> pd.DataFrame:
    """Delegate to ts_hma (defensive large-cap reversal picks)."""
    from backtest.strategies.ts_hma import pick_hma_stocks
    logger.info(f"[ts_7AZ_96MA_hma] falling back to ts_hma defensive picks")
    return pick_hma_stocks(end_date=end_date)


def pick_combined(end_date: str) -> pd.DataFrame:
    """Core: ts_7AZ_96MA, falling back to ts_hma when 7AZ96MA returns 0 picks."""
    df = pick_7az_96ma(end_date)
    if df is not None and not df.empty:
        logger.info(f"[ts_7AZ_96MA_hma] ts_7AZ_96MA produced {len(df)} picks (no hma fallback)")
        return df
    logger.warning(f"[ts_7AZ_96MA_hma] ts_7AZ_96MA empty -> using ts_hma defensive picks")
    return pick_hma_defensive(end_date)


def _write_output(df: pd.DataFrame) -> None:
    selected_stocks = []
    if df is not None and not df.empty:
        for _, row in df.iterrows():
            selected_stocks.append({
                'rank': int(row['rank']),
                'symbol': row['ts_code'],
                'name': row.get('name', ''),
                'score': float(row.get('score', 0) or 0),
            })
    output_file = '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_7AZ_tmp.json'
    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)
    logger.info(f"[ts_7AZ_96MA_hma] Saved {len(selected_stocks)} picks to {output_file}")


if __name__ == "__main__":
    argv = sys.argv[1:]
    lookahead = False
    if '--lookahead' in argv:
        lookahead = True
        argv.remove('--lookahead')

    if len(argv) >= 1:
        target_date = convert_trade_date(argv[0])
    else:
        target_date = str(pd.Timestamp.today().strftime('%Y%m%d'))

    date = target_date
    if not lookahead:
        date = get_trading_days_before(target_date, 1)

    logger.info(f"[ts_7AZ_96MA_hma] target {target_date} ref {date}")
    df = pick_combined(date)
    _write_output(df)
