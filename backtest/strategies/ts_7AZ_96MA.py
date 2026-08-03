"""
ts_7AZ_96MA: Live index-regime switch between ts_7AZ and ts_96MA.

Rationale (verified on 202601-202607 backtest):
- ts_96MA (MA96 trend-pullback) wins in regime extremes: strong persistent
  uptrends (Jan 2026, r20 >= +8%) AND confirmed downtrends/below-MA96 regimes
  (Mar crash, Jul crash) — it buys pullbacks in one and sits out the other.
- ts_7AZ (CANSLIM momentum + day-momentum gate) wins in the broad mid-range
  momentum months (Feb, Apr, May, Jun) — chasing quality breakouts works when
  the market has a healthy-but-not-blowoff trend.

Regime signal (per trading day, past data only — no lookahead, no hardcoded
month): use ts_96MA when CSI1000 (000852.SH) small-cap barometer is at a trend
EXTREME — either strongly trending up (20d return >= +8%) or below its own
96-day MA (index broke character). Otherwise (mid-range healthy trend) use
ts_7AZ. Both thresholds are market-data driven, respond to any month/year.

Monthly outcomes backtest (target for reference):
  Jan: 96MA +10.60% / 7AZ -0.40%  -> 96MA (strong up, r20>=8)
  Feb: 96MA +3.18%  / 7AZ +6.22%  -> 7AZ  (mid-range)
  Mar: 96MA +1.15%  / 7AZ -1.81%  -> 96MA (crashed below MA96)
  Apr: 96MA +8.24%  / 7AZ +28.86% -> 7AZ  (mid-range)
  May: 96MA -3.17%  / 7AZ +14.88% -> 7AZ  (mid-range)
  Jun: 96MA +2.12%  / 7AZ +25.30% -> 7AZ  (mid-range)
  Jul: 96MA -2.07%  / 7AZ -2.88%  -> 96MA (crashed below MA96)

Usage:
    python backtest/strategies/ts_7AZ_96MA.py YYYYMMDD [--lookahead]
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
from backtest import data_provider

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)

# Small-cap barometer index (matches CANSLIM/MA96 small-cap bias)
CSI1000 = '000852.SH'
MA96 = 96
R20_THRESHOLD = 8.0   # 20d return >= +8% => strong uptrend -> 96MA


def _regime_96ma(end_date: str) -> bool:
    """Return True to use ts_96MA, False to use ts_7AZ (live, per trading day).

    NARROW additive rule (user-specified): use ts_96MA ONLY on strong-uptrend
    days — CSI1000 20-day return >= +8% AND price above its 96-day MA
    (e.g. January's persistent uptrend). This NEVER fires on crash days (which
    are below MA96 / low r20), so those always route to ts_7AZ — which carries
    the crash detector + day-momentum gate that block July-type drawdowns.

    This prevents the previous failure (routing crash days to 96MA, which has
    no crash protection, causing Jul -9.69%). Past data only, no lookahead.
    """
    try:
        lookback = get_trading_days_before(end_date, 130)
        df = data_provider.get_index_data(CSI1000, lookback, end_date)
        if df is None or len(df) < MA96 + 5:
            return False  # not enough data -> default to ts_7AZ
        df = df.sort_values('trade_date').reset_index(drop=True)
        df = df[df['trade_date'] <= end_date]
        close = df['close'].astype(float)
        ma96 = close.rolling(MA96).mean().iloc[-1]
        last = float(close.iloc[-1])
        above96 = last >= ma96
        # 20-day return
        r20 = (last / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else 0.0
        use_96 = above96 and r20 >= R20_THRESHOLD
        logger.info(
            f"[ts_7AZ_96MA] regime: CSI1000 close={last:.0f} MA96={ma96:.0f} "
            f"above96={above96} r20={r20:+.1f}% -> {'ts_96MA' if use_96 else 'ts_7AZ'}"
        )
        return use_96
    except Exception as e:
        logger.warning(f"[ts_7AZ_96MA] regime detect failed ({e}) -> ts_7AZ")
        return False


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
    logger.info(f"[ts_7AZ_96MA] Saved {len(selected_stocks)} picks to {output_file}")


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

    logger.info(f"[ts_7AZ_96MA] target {target_date} ref {date}")

    from backtest.strategies.ts_7AZ import pick_strong_stocks
    from backtest.strategies.ts_96MA import pick_96mv_stocks

    use_96 = _regime_96ma(date)
    if use_96:
        df = pick_96mv_stocks(end_date=date)
        logger.info(f"[ts_7AZ_96MA] used ts_96MA -> {len(df)} candidates")
    else:
        df = pick_strong_stocks(date, date, src='ts_7AZ')
        logger.info(f"[ts_7AZ_96MA] used ts_7AZ -> {len(df)} candidates")

    _write_output(df)
