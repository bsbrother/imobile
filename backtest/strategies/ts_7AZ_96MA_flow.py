"""
ts_7AZ_96MA_flow: ts_7AZ_96MA + Dragon-Tiger (龙虎榜) institutional-flow filter.

EXPERIMENT (b): adds a market-microstructure filter on top of the production
ts_7AZ_96MA (v98.10) picker. Rationale: CANSLIM/momentum picks are pure price
+ growth screens; they ignore WHO is accumulating. The LHB (Dragon-Tiger list)
tracks daily institutional-seat net buying. A momentum pick that has fresh
institutional net-buy is backed by real money ("smart money confirmation"), a
pick with heavy institutional net-SELL is being distributed by institutions
into the breakout.

Signal (lookahead-safe): for each candidate, read institutional net-buy from
the cached LHB institutional ledger for records with 上榜日 in the prior N
trading days BEFORE the reference date (never on/after the ref date — no
lookahead). Re-rank candidates: boost score for positive institutional flow,
penalize/screen heavily-negative institutional net-SELL.

Uses cached CSV at backtest/data/lhb_institutional_2026.csv (fetched via
akshare stock_lhb_jgmmtj_em) so the backtest loop makes NO network calls.

Usage:
    python backtest/strategies/ts_7AZ_96MA_flow.py YYYYMMDD [--lookahead]
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

# Copy regime constants from ts_7AZ_96MA
CSI1000 = '000852.SH'
MA96 = 96
R20_THRESHOLD = 8.0
R60_THRESHOLD = 8.0
CRASH_THRESHOLD = -8.0

# ── LHB institutional-flow filter config ─────────────────────────
LHB_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                        'backtest', 'data', 'lhb_institutional_2026.csv')
LHB_LOOKBACK_DAYS = 10     # institutional activity in the prior N calendar days
SCREEN_NEG_INST = -50_000_000   # screen picks with institutional net-SELL below this (¥50M out)
BOOST_POS_INST = 8              # score boost per +¥100M institutional net-buy
_lhb_inst = None                # loaded once


def _load_lhb_inst():
    global _lhb_inst
    if _lhb_inst is not None:
        return _lhb_inst
    if not os.path.exists(LHB_CACHE):
        logger.warning(f"[ts_7AZ_96MA_flow] LHB cache missing at {LHB_CACHE} -> flow filter disabled")
        _lhb_inst = pd.DataFrame()
        return _lhb_inst
    df = pd.read_csv(LHB_CACHE)
    df['代码'] = df['代码'].astype(str).str.zfill(6)
    df['上榜日期'] = df['上榜日期'].astype(str)
    df['_d'] = df['上榜日期'].str.replace('-', '').astype(int)
    df['inst_net'] = pd.to_numeric(df['机构买入净额'], errors='coerce').fillna(0.0)
    _lhb_inst = df
    logger.info(f"[ts_7AZ_96MA_flow] loaded LHB institutional cache: {len(df)} records")
    return _lhb_inst


def _institutional_flow(code6: str, ref_date: str) -> float:
    """Sum institutional net-buy (¥) for `code6` with 上榜日 in the prior
    LHB_LOOKBACK_DAYS before `ref_date`. Past records only -> no lookahead."""
    inst = _load_lhb_inst()
    if inst.empty:
        return 0.0
    ref = convert_trade_date(ref_date)
    ref_int = int(ref)
    lo = ref_int - LHB_LOOKBACK_DAYS
    sub = inst[(inst['代码'] == code6) & (inst['_d'] >= lo) & (inst['_d'] < ref_int)]
    if len(sub) == 0:
        return 0.0
    return float(sub['inst_net'].sum())


def _apply_flow_filter(df: pd.DataFrame, ref_date: str) -> pd.DataFrame:
    """Re-rank candidates by institutional flow: screen heavy net-sell, boost positive flow."""
    if df is None or df.empty:
        return df
    inst = _load_lhb_inst()
    if inst.empty:
        logger.warning("[ts_7AZ_96MA_flow] no LHB data -> passthrough")
        return df
    rows = []
    for _, row in df.iterrows():
        ts_code = row['ts_code']
        code6 = str(ts_code).split('.')[0].zfill(6)
        flow = _institutional_flow(code6, ref_date)
        score = float(row.get('score', 0) or 0)
        # boost: +BOOST per 100M institutional net-buy
        boosted = score + BOOST_POS_INST * (flow / 100_000_000.0)
        rows.append({'row': row, 'code6': code6, 'flow': flow, 'boosted': boosted})
    # Screen: drop candidates with heavy institutional net-SELL (distribution)
    kept = [r for r in rows if r['flow'] >= SCREEN_NEG_INST]
    screened = len(rows) - len(kept)
    if screened:
        logger.info(f"[ts_7AZ_96MA_flow] screened {screened}/{len(rows)} picks with inst net-SELL < {SCREEN_NEG_INST/1e6:.0f}M")
    # Re-rank remaining by boosted score
    kept.sort(key=lambda r: r['boosted'], reverse=True)
    out_rows = []
    for i, r in enumerate(kept):
        new = r['row'].copy()
        new['rank'] = i + 1
        new['score'] = r['boosted']
        out_rows.append(new)
    out = pd.DataFrame(out_rows)
    if 'rank' not in out.columns:
        out['rank'] = range(1, len(out) + 1)
    logger.info(f"[ts_7AZ_96MA_flow] {len(out)} picks after flow filter (screen {screened})")
    return out


# ─────────────────────────────────────────────────────────────────
# Functions below are copied verbatim from ts_7AZ_96MA (regime + crash logic)
# ─────────────────────────────────────────────────────────────────

def _in_crash(end_date: str) -> bool:
    try:
        lookback = get_trading_days_before(end_date, 10)
        df = data_provider.get_index_data(CSI1000, lookback, end_date)
        if df is None or len(df) < 6:
            return False
        df = df.sort_values('trade_date').reset_index(drop=True)
        df = df[df['trade_date'] <= end_date]
        close = df['close'].astype(float)
        crash_count = 0
        for i in range(len(close) - 1, max(len(close) - 6, -1), -1):
            if i - 5 >= 0:
                r5 = (float(close.iloc[i]) / float(close.iloc[i - 5]) - 1) * 100
                if r5 < CRASH_THRESHOLD:
                    crash_count += 1
        crash = crash_count >= 2
        return crash
    except Exception:
        return False


def _regime_96ma(end_date: str) -> bool:
    try:
        lookback = get_trading_days_before(end_date, 130)
        df = data_provider.get_index_data(CSI1000, lookback, end_date)
        if df is None or len(df) < MA96 + 5:
            return False
        df = df.sort_values('trade_date').reset_index(drop=True)
        df = df[df['trade_date'] <= end_date]
        close = df['close'].astype(float)
        ma96 = close.rolling(MA96).mean().iloc[-1]
        last = float(close.iloc[-1])
        above96 = last >= ma96
        r20 = (last / float(close.iloc[-21]) - 1) * 100 if len(close) >= 21 else 0.0
        r60 = (last / float(close.iloc[-61]) - 1) * 100 if len(close) >= 61 else 0.0
        return above96 and r20 >= R20_THRESHOLD and r60 >= R60_THRESHOLD
    except Exception:
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
    logger.info(f"[ts_7AZ_96MA_flow] Saved {len(selected_stocks)} picks to {output_file}")


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

    logger.info(f"[ts_7AZ_96MA_flow] target {target_date} ref {date}")

    from backtest.strategies.ts_7AZ import pick_strong_stocks
    from backtest.strategies.ts_96MA import pick_96mv_stocks

    use_96 = _regime_96ma(date)
    if use_96:
        df = pick_96mv_stocks(end_date=date)
        logger.info(f"[ts_7AZ_96MA_flow] used ts_96MA -> {len(df)} candidates")
    else:
        df = pick_strong_stocks(date, date, src='ts_7AZ')
        logger.info(f"[ts_7AZ_96MA_flow] used ts_7AZ -> {len(df)} candidates")

    if (df is None or df.empty) and _in_crash(date):
        from backtest.strategies.ts_hma import pick_hma_stocks
        logger.warning("[ts_7AZ_96MA_flow] 0 picks + confirmed crash -> defensive ts_hma fallback")
        df = pick_hma_stocks(end_date=date)
        logger.info(f"[ts_7AZ_96MA_flow] hma defensive fallback -> {len(df)} candidates")

    df = _apply_flow_filter(df, date)

    _write_output(df)
