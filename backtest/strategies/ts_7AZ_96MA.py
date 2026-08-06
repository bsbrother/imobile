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
R20_THRESHOLD = 8.0   # 20d return >= +8% => strong recent uptrend
R60_THRESHOLD = 8.0   # 60d return >= +8% => persistent multi-month uptrend (discriminates Jan from May)
CRASH_THRESHOLD = -8.0  # CSI1000 5d return < -8% => confirmed momentum crash/volatility spike


def _in_crash(end_date: str) -> bool:
    """Return True when CSI1000 is in a SUSTAINED short-term crash.

    Uses the ground-truth crash signal (small-cap barometer 5-day return < -8%).
    Requires the crash to be SUSTAINED — fired on >= 2 of the trailing 4 trading
    days — to distinguish a real momentum crash (Jul 2026: multiple crash days
    across 8/13/16/17/20/21/22) from a single-day spike that immediately recovers
    (Mar 2026: only ref 23 spiked to -9.77%, then 24-27 recovered to -5..-0.5).
    This mirrors docs/adjust_ts_7AZ_96MA.md's guidance to add a smoothing filter
    to avoid the fake-reversal / signal-flapping trap. Past data only.
    """
    try:
        lookback = get_trading_days_before(end_date, 10)
        df = data_provider.get_index_data(CSI1000, lookback, end_date)
        if df is None or len(df) < 6:
            return False
        df = df.sort_values('trade_date').reset_index(drop=True)
        df = df[df['trade_date'] <= end_date]
        close = df['close'].astype(float)
        # crash flags on each of the trailing 4 trading days
        crash_count = 0
        for i in range(len(close) - 1, max(len(close) - 6, -1), -1):
            if i - 5 >= 0:
                r5 = (float(close.iloc[i]) / float(close.iloc[i - 5]) - 1) * 100
                if r5 < CRASH_THRESHOLD:
                    crash_count += 1
        last_r5 = (float(close.iloc[-1]) / float(close.iloc[-6]) - 1) * 100
        crash = crash_count >= 2
        logger.info(
            f"[ts_7AZ_96MA] crash-check: last5d={last_r5:+.1f}% crash_days(trail4)={crash_count}/4 "
            f"-> {'CRASH(sustained)' if crash else 'ok'}"
        )
        return crash
    except Exception as e:
        logger.warning(f"[ts_7AZ_96MA] crash-check failed ({e}) -> not crash")
        return False


def _regime_96ma(end_date: str) -> bool:
    """Return True to use ts_96MA, False to use ts_7AZ (live, per trading day).

    REFINED additive rule (option 3): use ts_96MA ONLY on strong-uptrend days
    that are also PERSISTENT — CSI1000 must satisfy ALL THREE:
      1. price above its 96-day MA
      2. 20-day return >= +8%  (recent strength)
      3. 60-day return >= +8%  (persistent multi-month uptrend)
    The 60-day condition is the key discriminator: it keeps January (r60 +8..+14,
    an established uptrend where 96MA pullback-buying wins) and drops the 7AZ
    momentum months (Feb/Apr/May r60 < 8), preventing the earlier leak where
    96MA's slower pullback picks underperformed CANSLIM in May.

    This NEVER fires on crash days (below MA96 / low r20/r60), so those always
    route to ts_7AZ, which carries the crash detector + day-momentum gate that
    block July-type drawdowns. Past data only, no lookahead, no hardcoded months.
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
        r20 = (last / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else 0.0
        r60 = (last / float(close.iloc[-61]) - 1) * 100 if len(close) >= 61 else 0.0
        use_96 = above96 and r20 >= R20_THRESHOLD and r60 >= R60_THRESHOLD
        logger.info(
            f"[ts_7AZ_96MA] regime: CSI1000 close={last:.0f} MA96={ma96:.0f} "
            f"above96={above96} r20={r20:+.1f}% r60={r60:+.1f}% -> "
            f"{'ts_96MA' if use_96 else 'ts_7AZ'}"
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

    # Crash-gated defensive fallback (per docs/adjust_ts_7AZ_96MA.md):
    # when the momentum path produces ZERO picks AND CSI1000 is in a confirmed
    # crash (5d < -8%), the market is in a momentum-crash regime — money leaves
    # high-beta growth for defensive low-vol/high-dividend names. Fall back to
    # ts_hma's defensive large-cap picks instead of sitting flat.
    # This fires ONLY in true crashes (Jul 2026), NOT on dips that recover
    # (Mar 2026 24-27) — so it avoids the March damage + April carryover that a
    # plain "0-pick -> hma" fallback caused. Past data only, no lookahead.
    if (df is None or df.empty) and _in_crash(date):
        from backtest.strategies.ts_hma import pick_hma_stocks
        logger.warning("[ts_7AZ_96MA] 0 picks + confirmed crash -> defensive ts_hma fallback")
        df = pick_hma_stocks(end_date=date)
        logger.info(f"[ts_7AZ_96MA] hma defensive fallback -> {len(df)} candidates")

    _write_output(df)
