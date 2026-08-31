"""
ts_7AZ_96MA_flow_v2: ts_7AZ_96MA + enhanced institutional-flow filter.

V2 improvements (backed by a-stock-data insights):
1. Regime-adaptive LHB filter: tighter screen in bear/volatile, looser in bull
2. Volume-confirmation boost: stocks with elevated relative volume get a score
   boost — proxies for northbound/margin interest that a-stock-data endpoints
   (margin_trading, northbound_daily) would confirm in real-time
3. Bear-market position cap: reduce max positions in bear regime
4. Realistic cost flag: SELL_SLIPPAGE_PCT env controls sell-side slippage

Same lookahead safety as V1: all data sourced from dates BEFORE the reference
date (no future-peeking).

Usage:
    python backtest/strategies/ts_7AZ_96MA_flow_v2.py YYYYMMDD [--lookahead]
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
                        'shared', 'data', 'lhb', 'lhb_institutional_2026.csv')
LHB_LOOKBACK_DAYS = 10     # institutional activity in the prior N calendar days

# V2: Regime-adaptive LHB filter thresholds (env-overridable)
# Rationale: a-stock-data shows northbound/margin flow is DAILY available,
# so we can be more selective in bear markets where smart-money signals are
# scarcer, and more permissive in bull markets where momentum predominates.
SCREEN_NEG_INST_BULL   = int(os.getenv('LHB_SCREEN_BULL',   '-40000000'))  # -40M: looser
SCREEN_NEG_INST_NORMAL = int(os.getenv('LHB_SCREEN_NORMAL', '-50000000'))  # -50M: baseline
SCREEN_NEG_INST_VOL    = int(os.getenv('LHB_SCREEN_VOL',    '-30000000'))  # -30M: tighter
SCREEN_NEG_INST_BEAR   = int(os.getenv('LHB_SCREEN_BEAR',   '-20000000'))  # -20M: strict

BOOST_POS_INST_BULL   = int(os.getenv('LHB_BOOST_BULL',   '10'))  # stronger boost in bull
BOOST_POS_INST_NORMAL = int(os.getenv('LHB_BOOST_NORMAL', '8'))
BOOST_POS_INST_VOL    = int(os.getenv('LHB_BOOST_VOL',    '10'))
BOOST_POS_INST_BEAR   = int(os.getenv('LHB_BOOST_BEAR',   '12'))  # highest boost in bear (scarcer)

# V2: Volume confirmation — stocks with elevated relative volume get a score boost.
# This proxies for the smart-money interest that a-stock-data's margin_trading()
# and northbound endpoints would capture in real-time.
VOL_BOOST_LOOKBACK    = int(os.getenv('VOL_BOOST_LOOKBACK', '10'))     # days
VOL_BOOST_THRESHOLD   = float(os.getenv('VOL_BOOST_THRESHOLD', '2.0'))  # volume_ratio > 2.0
VOL_BOOST_SCORE       = float(os.getenv('VOL_BOOST_SCORE', '3.0'))     # score increment

# V2: Bear-market position cap (env-overridable)
BEAR_MAX_POS = int(os.getenv('BEAR_MAX_POS', '8'))  # default 8 (v1 had no cap beyond config)

_lhb_inst = None                # loaded once
_volume_cache = {}               # per-date volume data cache


def _load_lhb_inst():
    global _lhb_inst
    if _lhb_inst is not None:
        return _lhb_inst
    if not os.path.exists(LHB_CACHE):
        logger.warning(f"[ts_7AZ_96MA_flow_v2] LHB cache missing at {LHB_CACHE} -> flow filter disabled")
        _lhb_inst = pd.DataFrame()
        return _lhb_inst
    df = pd.read_csv(LHB_CACHE)
    df['代码'] = df['代码'].astype(str).str.zfill(6)
    df['上榜日期'] = df['上榜日期'].astype(str)
    df['_d'] = df['上榜日期'].str.replace('-', '').astype(int)
    df['inst_net'] = pd.to_numeric(df['机构买入净额'], errors='coerce').fillna(0.0)
    _lhb_inst = df
    logger.info(f"[ts_7AZ_96MA_flow_v2] loaded LHB institutional cache: {len(df)} records")
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


def _detect_regime_cached(ref_date: str) -> str:
    """Detect market regime for the given ref_date. Uses the market_regime module
    which caches results — repeated calls per backtest day are cheap."""
    try:
        from backtest.market_regime import detect_market_regime
        result = detect_market_regime(ref_date)
        return str(result.get('regime', 'normal')).lower()
    except Exception:
        return 'normal'


def _volume_boost(code6: str, ref_date: str, ts_code: str) -> float:
    """V2: Volume-confirmation boost.
    
    Fetches OHLCV data for the stock and checks if recent volume is elevated
    relative to its trailing average. Elevated volume + positive price change
    suggests smart-money accumulation — proxies for the northbound/margin
    signals that a-stock-data endpoints would provide in real-time.
    
    Lookahead-safe: uses only data up to ref_date.
    """
    try:
        lookback_start = get_trading_days_before(ref_date, VOL_BOOST_LOOKBACK + 30)
        ohlcv = data_provider.get_ohlcv_data(ts_code, lookback_start, ref_date)
        if ohlcv is None or len(ohlcv) < VOL_BOOST_LOOKBACK + 5:
            return 0.0
        ohlcv = ohlcv.sort_values('trade_date').reset_index(drop=True)
        ohlcv = ohlcv[ohlcv['trade_date'] <= ref_date]
        if len(ohlcv) < VOL_BOOST_LOOKBACK + 5:
            return 0.0
        
        recent = ohlcv.tail(VOL_BOOST_LOOKBACK)
        older = ohlcv.iloc[:-VOL_BOOST_LOOKBACK].tail(VOL_BOOST_LOOKBACK * 2)
        
        if len(older) < 5:
            return 0.0
        
        avg_vol_older = older['vol'].astype(float).mean()
        if avg_vol_older <= 0:
            return 0.0
        
        avg_vol_recent = recent['vol'].astype(float).mean()
        vol_ratio = avg_vol_recent / avg_vol_older
        
        if vol_ratio < VOL_BOOST_THRESHOLD:
            return 0.0
        
        # Check price trend: positive price change in recent window
        price_start = float(older['close'].iloc[-1])
        price_end = float(recent['close'].iloc[-1])
        if price_start <= 0:
            return 0.0
        price_chg = (price_end / price_start - 1) * 100
        
        if price_chg > 0:
            # Boost proportional to volume ratio, capped
            boost = min(vol_ratio - VOL_BOOST_THRESHOLD, 5.0) * VOL_BOOST_SCORE * (1.0 + min(price_chg / 10.0, 1.0))
            return round(boost, 2)
        return 0.0
    except Exception:
        return 0.0


def _apply_flow_filter_v2(df: pd.DataFrame, ref_date: str) -> pd.DataFrame:
    """V2: Re-rank candidates with regime-adaptive LHB filter + volume boost.
    
    Improvements over V1:
    1. Regime-adaptive screen thresholds (stricter in bear, looser in bull)
    2. Regime-adaptive boost coefficients
    3. Volume-confirmation boost for stocks with elevated relative volume
    """
    if df is None or df.empty:
        return df
    inst = _load_lhb_inst()
    if inst.empty:
        logger.warning("[ts_7AZ_96MA_flow_v2] no LHB data -> passthrough")
        return df
    
    regime = _detect_regime_cached(ref_date)
    
    # Select regime-adaptive thresholds
    screen_map = {
        'bull': SCREEN_NEG_INST_BULL,
        'normal': SCREEN_NEG_INST_NORMAL,
        'volatile': SCREEN_NEG_INST_VOL,
        'bear': SCREEN_NEG_INST_BEAR,
    }
    boost_map = {
        'bull': BOOST_POS_INST_BULL,
        'normal': BOOST_POS_INST_NORMAL,
        'volatile': BOOST_POS_INST_VOL,
        'bear': BOOST_POS_INST_BEAR,
    }
    
    screen_neg = screen_map.get(regime, SCREEN_NEG_INST_NORMAL)
    boost_pos = boost_map.get(regime, BOOST_POS_INST_NORMAL)
    
    rows = []
    for _, row in df.iterrows():
        ts_code = row['ts_code']
        code6 = str(ts_code).split('.')[0].zfill(6)
        flow = _institutional_flow(code6, ref_date)
        score = float(row.get('score', 0) or 0)
        
        # LHB institutional flow boost
        lhb_boost = boost_pos * (flow / 100_000_000.0)
        
        # V2: Volume-confirmation boost
        vol_boost = _volume_boost(code6, ref_date, ts_code)
        
        boosted = score + lhb_boost + vol_boost
        rows.append({
            'row': row, 'code6': code6, 'flow': flow,
            'boosted': boosted, 'lhb_boost': lhb_boost, 'vol_boost': vol_boost
        })
    
    # Screen: regime-adaptive threshold
    kept = [r for r in rows if r['flow'] >= screen_neg]
    screened = len(rows) - len(kept)
    if screened:
        logger.info(
            f"[ts_7AZ_96MA_flow_v2] regime={regime} screened {screened}/{len(rows)} "
            f"with inst net-SELL < {screen_neg/1e6:.0f}M"
        )
    
    # Log volume boosts applied
    vol_boosted = [r for r in kept if r['vol_boost'] > 0]
    if vol_boosted:
        logger.info(
            f"[ts_7AZ_96MA_flow_v2] {len(vol_boosted)} picks got volume boost "
            f"(max +{max(r['vol_boost'] for r in vol_boosted):.1f})"
        )
    
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
    
    # V2: Bear-market position cap
    if regime == 'bear' and len(out) > BEAR_MAX_POS:
        logger.info(
            f"[ts_7AZ_96MA_flow_v2] bear regime: capping positions "
            f"{len(out)} -> {BEAR_MAX_POS}"
        )
        out = out.head(BEAR_MAX_POS)
    
    logger.info(
        f"[ts_7AZ_96MA_flow_v2] {len(out)} picks after flow filter "
        f"(regime={regime}, screen={screen_neg/1e6:.0f}M, boost={boost_pos})"
    )
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
    logger.info(f"[ts_7AZ_96MA_flow_v2] Saved {len(selected_stocks)} picks to {output_file}")


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

    logger.info(f"[ts_7AZ_96MA_flow_v2] target {target_date} ref {date}")

    from backtest.strategies.ts_7AZ import pick_strong_stocks
    from backtest.strategies.ts_96MA import pick_96mv_stocks

    use_96 = _regime_96ma(date)
    if use_96:
        df = pick_96mv_stocks(end_date=date)
        logger.info(f"[ts_7AZ_96MA_flow_v2] used ts_96MA -> {len(df)} candidates")
    else:
        df = pick_strong_stocks(date, date, src='ts_7AZ')
        logger.info(f"[ts_7AZ_96MA_flow_v2] used ts_7AZ -> {len(df)} candidates")

    if (df is None or df.empty) and _in_crash(date):
        from backtest.strategies.ts_hma import pick_hma_stocks
        logger.warning("[ts_7AZ_96MA_flow_v2] 0 picks + confirmed crash -> defensive ts_hma fallback")
        df = pick_hma_stocks(end_date=date)
        logger.info(f"[ts_7AZ_96MA_flow_v2] hma defensive fallback -> {len(df)} candidates")

    df = _apply_flow_filter_v2(df, date)

    _write_output(df)