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
LHB_LOOKBACK_DAYS = int(os.getenv('LHB_LOOKBACK', '10'))  # institutional activity in the prior N calendar days

# ── V2.1 LEVER 4: event-anchored LHB (exhaustion-spike rejection) ──────
# 龙虎榜 is a *disclosure* triggered BY a big move, so an institution can
# appear on the list precisely at a blow-off top. Require that the stock had
# NOT already run >= LHB_EXHAUST_RUN5% in the 5 days ending on the 上榜日;
# such records are exhaustion spikes, not accumulation, and are ignored.
# 0 = disabled (baseline behaviour).
LHB_EXHAUST_RUN5 = float(os.getenv('LHB_EXHAUST_RUN5', '0'))

# ── LEVER C: dragon-list (龙虎榜 detail, ALL seats) net-buy screen ─────
# The strategy only used the INSTITUTIONAL ledger (机构买入净额, ¥). The detail
# list adds the all-seats net-buy as a % of market turnover (净买额占总成交比) —
# conviction intensity rather than absolute size. Measured on the verified run's
# 1440 picks: the mild-distribution bucket (ratio sum in [-3,0)) is the worst
# cohort by far (10d fwd -7.03%, 16% win), while accumulation buckets are fine
# (+4.3% / +7.4%). Screening ratio-sum < LHB_DRAGON_NEG removes ~40 picks whose
# mean 10d fwd is -2.69%.
# Screen-ONLY (never re-rank) and no-record = NEUTRAL, per
# references/lhb-institutional-flow-filter.md: a negative screen is additive,
# a positive boost re-rank trades winners for slower confirmed names.
# Unset/'' disables (baseline behaviour).
LHB_DRAGON_SCREEN = os.getenv('LHB_DRAGON_SCREEN', '').lower() in ('true', '1', 'yes')
LHB_DRAGON_NEG = float(os.getenv('LHB_DRAGON_NEG', '0'))
LHB_DRAGON_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                'shared', 'data', 'lhb', 'lhb_dragon_2026.csv')

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

# ── V2.1 LEVER 1: extension cap (env-gated, default OFF = baseline) ────
# Evidence (2026-09 backtest analysis, 1351 picks): picks that had ALREADY run
# >100% in the prior 60 trading days carry a 19.3% chance of -15% within 10
# days, vs 1.2% for picks that ran <=25% - a 16x tail-risk difference. The
# median pick is already at the 92nd percentile of its own 52-week range.
# NOTE: over-extension is MORE common in the winning months (Apr/Jun), so a
# tight cap trades winners for safety. r60>150 is the validated setting: the
# 114 picks it removes have NEGATIVE mean 10-day forward return (-0.23%), so
# trimming them deletes losing trades rather than swapping good names for slow
# ones. Default gate is now ALL regimes (see the firing note at the call site).
V2_EXT_CAP = os.getenv('V2_EXT_CAP', 'false').lower() in ('true', '1', 'yes')
V2_EXT_CAP_R60 = float(os.getenv('V2_EXT_CAP_R60', '150.0'))   # trailing 60d %run ceiling
# <=0 disables the 52w-range condition entirely (the validated trim is r60 alone).
V2_EXT_CAP_RANGE = float(os.getenv('V2_EXT_CAP_RANGE', '0'))   # 52w-range position ceiling
# Default = ALL regimes. The original 'volatile,bear' gate made the cap a no-op
# for Jan-Aug 2026 (no VOLATILE days; BEAR days have 0 candidates).
V2_EXT_CAP_REGIMES = [s.strip().lower() for s in
                      os.getenv('V2_EXT_CAP_REGIME', '').split(',') if s.strip()]
V2_EXT_LOOKBACK = int(os.getenv('V2_EXT_LOOKBACK', '250'))       # 52w window (trading days)

# ── V2.1 LEVER 2: target the 80-99 52w-range band (env-gated, default OFF) ──
# Forward-return by range position, n=1351: <60 -> +1.6% (50% win),
# 80-90 -> +5.0%, 90-95 -> +4.6%, 95-99 -> +4.7%, ==100 -> +4.0% (11.6% tail).
# The mid-high band beats BOTH the low end and the exact-high end.
V2_RANGE_BAND = os.getenv('V2_RANGE_BAND', 'false').lower() in ('true', '1', 'yes')
V2_RANGE_BAND_LO = float(os.getenv('V2_RANGE_BAND_LO', '80'))
V2_RANGE_BAND_HI = float(os.getenv('V2_RANGE_BAND_HI', '99.5'))
V2_RANGE_BAND_REGIMES = [s.strip().lower() for s in
                         os.getenv('V2_RANGE_BAND_REGIME', '').split(',') if s.strip()]

# ── V2.1 TREND-AGE CAP: consecutive days the close has held above MA60 ──────
# Measured 2026-09-17 on the verified run's 1152 picks: picks taken >120 trading
# days into a MA60-defined trend have NEGATIVE mean 10d forward return (-2.45%)
# vs the 135.04% baseline's +4.13% average. This is the ONLY bucket with negative
# expectancy, and the 41-120d band has both the highest stop-out rate (84-85%)
# and the largest average loss when stopped (-3.56 to -5.82%).
# Unlike the four *extension* filters that failed (RPS, 52w-high, trailing run),
# this measures trend DURATION — the Apr/May/Jun winners were extended but YOUNG
# (<=40d above MA60), so a duration cap discriminates where extension did not.
#
# Per-regime thresholds (2026-09-18): Jan cost (-1.5pp) showed full-reject across
# bull regimes damages bull-month returns. Now bull uses a generous 200d cap
# (effectively off), normal keeps 120d, and volatile/bear tighten to 80d — the
# 41-80d band has the worst SL severity and the highest stop-out rate, and those
# are exactly the buckets available with fewer candidates in weak regimes.
# Env-gated, default OFF (baseline unchanged).
V2_TREND_AGE_CAP = os.getenv('V2_TREND_AGE_CAP', 'false').lower() in ('true', '1', 'yes')
V2_TREND_AGE_MAX_BULL      = int(os.getenv('V2_TREND_AGE_MAX_BULL', '200'))
V2_TREND_AGE_MAX_NORMAL    = int(os.getenv('V2_TREND_AGE_MAX_NORMAL', '120'))
V2_TREND_AGE_MAX_VOLATILE  = int(os.getenv('V2_TREND_AGE_MAX_VOLATILE', '80'))
V2_TREND_AGE_MAX_BEAR      = int(os.getenv('V2_TREND_AGE_MAX_BEAR', '80'))
V2_TREND_AGE_REGIMES = [s.strip().lower() for s in
                        os.getenv('V2_TREND_AGE_REGIME', 'normal,volatile,bear').split(',')
                        if s.strip()]

_AGE_MAX_FOR_REGIME = {
    'bull': V2_TREND_AGE_MAX_BULL,
    'normal': V2_TREND_AGE_MAX_NORMAL,
    'volatile': V2_TREND_AGE_MAX_VOLATILE,
    'bear': V2_TREND_AGE_MAX_BEAR,
}

# ── LEVER: 业绩预告 (forecast) structural gate ──────────────────────────────
# The article's three-anchor method for reading 业绩预告: (1) range width
# monster intervals like "预增10%-200%" indicate internal chaos; (2) 首亏/续亏
# are unambiguous warnings; (3) 非经常性损益戳补 growth is unreliable (needs
# fina_indicator, not implemented here). Gate uses the Tushare forecast endpoint
# (ann_date-aligned per the article's data-time discipline). Pre-fetched once
# into shared/data/forecast_2026.csv. No-record = neutral (not all stocks issue
# forecasts). Env-gated, default OFF.
V2_FORECAST_GATE = os.getenv('V2_FORECAST_GATE', 'false').lower() in ('true', '1', 'yes')
V2_FORECAST_RANGE = float(os.getenv('V2_FORECAST_RANGE', '100'))  # pct-pt range width ceiling
V2_FORECAST_LOOKBACK = int(os.getenv('V2_FORECAST_LOOKBACK', '90'))  # calendar days before ref_date
V2_FORECAST_CACHE = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                                 'shared', 'data', 'forecast_2026.csv')
V2_FORECAST_REJECT_TYPES = [s.strip() for s in
                            os.getenv('V2_FORECAST_REJECT_TYPES', '首亏,续亏').split(',') if s.strip()]

_lhb_inst = None                # loaded once
_dragon = None                   # loaded once
_forecast = None                 # loaded once
_volume_cache = {}               # per-date volume data cache
_metrics_cache = {}              # per-(symbol,date) trend metrics cache
_trend_age_cache = {}            # per-(symbol,date) trend age cache
_run5_cache = {}                 # per-(symbol,date) 5-day run cache


def _trend_age(ts_code: str, ref_date: str):
    """Consecutive trading days the close has held above MA60, ending at ref_date.

    Lookahead-safe: the OHLCV frame is truncated at ref_date before the count.
    Returns (days_above, is_currently_above). 200-day lookback gives up to ~140
    consecutive days; returns (0, False) when there is not enough history.
    """
    key = ('age', ts_code, ref_date)
    if key in _trend_age_cache:
        return _trend_age_cache[key]
    out = (0, False)
    try:
        start = get_trading_days_before(ref_date, 200)
        df = data_provider.get_ohlcv_data(ts_code, start, ref_date)
        if df is not None and len(df) >= 70:
            df = df.sort_values('trade_date').reset_index(drop=True)
            df = df[df['trade_date'] <= ref_date]
            c = df['close'].astype(float)
            ma60 = c.rolling(60).mean()
            # count consecutive days where close > MA60, backwards from ref_date
            days = 0
            for i in range(len(c) - 1, -1, -1):
                if not pd.isna(ma60.iloc[i]):
                    try:
                        if float(c.iloc[i]) > float(ma60.iloc[i]):
                            days += 1
                        else:
                            break
                    except (ValueError, TypeError):
                        break
                else:
                    break
            above_now = (not pd.isna(ma60.iloc[-1])
                         and float(c.iloc[-1]) > float(ma60.iloc[-1]))
            out = (days, above_now)
    except Exception:
        out = (0, False)
    _trend_age_cache[key] = out
    return out


def _load_dragon():
    """Load the 龙虎榜 detail list (all seats) — cached, deterministic, offline."""
    global _dragon
    if _dragon is not None:
        return _dragon
    if not os.path.exists(LHB_DRAGON_CACHE):
        logger.warning(f"[ts_7AZ_96MA_flow_v2] dragon cache missing at {LHB_DRAGON_CACHE} -> screen disabled")
        _dragon = pd.DataFrame()
        return _dragon
    df = pd.read_csv(LHB_DRAGON_CACHE)
    df['c6'] = df['代码'].astype(str).str.replace(r'\.0$', '', regex=True).str.zfill(6)
    df['_d'] = df['上榜日'].astype(str).str.replace('-', '').astype(int)
    df['nbr'] = pd.to_numeric(df['净买额占总成交比'], errors='coerce').fillna(0.0)
    _dragon = df
    logger.info(f"[ts_7AZ_96MA_flow_v2] loaded dragon list: {len(df)} records")
    return _dragon


def _load_forecast():
    """Load 业绩预告 cache (Tushare forecast endpoint) — one-time, offline."""
    global _forecast
    if _forecast is not None:
        return _forecast
    if not os.path.exists(V2_FORECAST_CACHE):
        logger.warning(f"[ts_7AZ_96MA_flow_v2] forecast cache missing at {V2_FORECAST_CACHE} -> gate disabled")
        _forecast = pd.DataFrame()
        return _forecast
    df = pd.read_csv(V2_FORECAST_CACHE)
    df['code6'] = df['ts_code'].astype(str).str.split('.').str[0].str.zfill(6)
    df['_d'] = df['ann_date'].astype(str).str.replace('-', '')  # YYYYMMDD
    df['range_width'] = (pd.to_numeric(df['p_change_max'], errors='coerce').fillna(0)
                         - pd.to_numeric(df['p_change_min'], errors='coerce').fillna(0))
    _forecast = df
    logger.info(f"[ts_7AZ_96MA_flow_v2] loaded forecast cache: {len(df)} records")
    return _forecast


def _forecast_is_bad(code6: str, ref_date: str, non_recurring_gap: float = 0):
    """Latest 业绩预告 flags for `code6` with ann_date in [ref_date - LOOKBACK, ref_date).

    Returns None when no forecast exists (neutral). Returns a dict with range_width
    and forecast type when one exists — the caller gates on fields that are too wide
    or bearish. ann_date-aligned per the article's data-time discipline.

    non_recurring_gap (anchor #3): netprofit_yoy - dt_netprofit_yoy > 30pp means
    growth is propped up by non-operating income (subsidies, asset sales). Passed
    from the CANSLIM screener via the df['non_recurring_gap'] column. Zero when
    unavailable (neutral — fina_indicator might not have returned both values).
    """
    fc = _load_forecast()
    if fc.empty:
        return None
    # Proper date arithmetic: ref_date is YYYYMMDD string, LOOKBACK is calendar
    # days. Convert to datetime to compute the window correctly.
    from datetime import datetime, timedelta
    ref_dt = datetime.strptime(str(ref_date), '%Y%m%d')
    lo_dt = ref_dt - timedelta(days=V2_FORECAST_LOOKBACK)
    lo_s = lo_dt.strftime('%Y%m%d')
    sub = fc[(fc['code6'] == code6) & (fc['_d'] >= lo_s) & (fc['_d'] < str(ref_date))]
    if len(sub) == 0:
        return None
    latest = sub.sort_values('_d', ascending=False).iloc[0]
    return dict(
        range_width=float(latest['range_width']),
        ftype=str(latest.get('type', '')),
        ann_date=str(latest['_d']),
        non_recurring_gap=non_recurring_gap,
    )
    """Sum 净买额占总成交比 over dragon records in the prior LHB_LOOKBACK_DAYS.

    Returns None when the stock has NO dragon record — neutral, NOT negative
    (LHB only lists stocks that hit move/turnover thresholds).
    """
    d = _load_dragon()
    if d.empty:
        return None
    ref_int = int(convert_trade_date(ref_date))
    lo = ref_int - LHB_LOOKBACK_DAYS
    sub = d[(d['c6'] == code6) & (d['_d'] >= lo) & (d['_d'] < ref_int)]
    if len(sub) == 0:
        return None
    return float(sub['nbr'].sum())


def _run5_ending(ts_code: str, yyyymmdd: str):
    """5-trading-day %return ending ON `yyyymmdd` (inclusive), past data only."""
    key = (ts_code, yyyymmdd)
    if key in _run5_cache:
        return _run5_cache[key]
    val = None
    try:
        start = get_trading_days_before(yyyymmdd, 20)
        df = data_provider.get_ohlcv_data(ts_code, start, yyyymmdd)
        if df is not None and len(df) >= 6:
            df = df.sort_values('trade_date').reset_index(drop=True)
            df = df[df['trade_date'] <= yyyymmdd]
            c = [float(x) for x in df['close'].astype(float)]
            if len(c) >= 6:
                val = (c[-1] / c[-6] - 1) * 100
    except Exception:
        val = None
    _run5_cache[key] = val
    return val


def _trend_metrics(ts_code: str, ref_date: str):
    """(trailing_60d_%return, 52w_range_position 0-100) using only data <= ref_date.

    Lookahead-safe: the OHLCV frame is truncated at ref_date before any metric is
    computed. Returns (None, None) when there is not enough history.
    """
    key = (ts_code, ref_date)
    if key in _metrics_cache:
        return _metrics_cache[key]
    out = (None, None)
    try:
        start = get_trading_days_before(ref_date, V2_EXT_LOOKBACK + 10)
        df = data_provider.get_ohlcv_data(ts_code, start, ref_date)
        if df is not None and len(df) >= 65:
            df = df.sort_values('trade_date').reset_index(drop=True)
            df = df[df['trade_date'] <= ref_date]
            c = [float(x) for x in df['close'].astype(float)]
            if len(c) >= 65:
                r60 = (c[-1] / c[-61] - 1) * 100
                w = c[-V2_EXT_LOOKBACK:] if len(c) >= V2_EXT_LOOKBACK else c
                hi, lo = max(w), min(w)
                rng = (c[-1] - lo) / (hi - lo) * 100 if hi > lo else None
                out = (r60, rng)
    except Exception:
        out = (None, None)
    _metrics_cache[key] = out
    return out


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


def _institutional_flow(code6: str, ref_date: str, ts_code: str | None = None) -> float:
    """Sum institutional net-buy (¥) for `code6` with 上榜日 in the prior
    LHB_LOOKBACK_DAYS before `ref_date`. Past records only -> no lookahead.

    LEVER 4 (env `LHB_EXHAUST_RUN5`): when set > 0, records whose 上榜日 was
    itself preceded by a >= that % 5-day run are treated as exhaustion spikes
    (the institution is on the list *because* the stock already spiked, often
    distributing) and excluded from the accumulation total.
    """
    inst = _load_lhb_inst()
    if inst.empty:
        return 0.0
    ref = convert_trade_date(ref_date)
    ref_int = int(ref)
    lo = ref_int - LHB_LOOKBACK_DAYS
    sub = inst[(inst['代码'] == code6) & (inst['_d'] >= lo) & (inst['_d'] < ref_int)]
    if len(sub) == 0:
        return 0.0
    if LHB_EXHAUST_RUN5 > 0 and ts_code:
        keep = []
        for _, rec in sub.iterrows():
            rec_date = str(rec['上榜日期']).replace('-', '')
            r5 = _run5_ending(ts_code, rec_date)
            if r5 is not None and r5 >= LHB_EXHAUST_RUN5:
                continue  # exhaustion spike -> ignore this institutional record
            keep.append(rec['inst_net'])
        if not keep:
            return 0.0
        return float(sum(keep))
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
        ts_code = str(row['ts_code'])
        code6 = str(ts_code).split('.')[0].zfill(6)
        flow = _institutional_flow(code6, ref_date, ts_code)
        score = float(row.get('score', 0) or 0)
        
        # LHB institutional flow boost
        lhb_boost = boost_pos * (flow / 100_000_000.0)
        
        # V2: Volume-confirmation boost
        vol_boost = _volume_boost(code6, ref_date, ts_code)

        # V2.1: trend-extension metrics (only fetched when a lever needs them)
        r60, rng_pos = (None, None)
        if V2_EXT_CAP or V2_RANGE_BAND:
            r60, rng_pos = _trend_metrics(ts_code, ref_date)
        
        boosted = score + lhb_boost + vol_boost
        rows.append({
            'row': row, 'code6': code6, 'flow': flow,
            'boosted': boosted, 'lhb_boost': lhb_boost, 'vol_boost': vol_boost,
            'r60': r60, 'rng_pos': rng_pos,
        })
    
    # Screen: regime-adaptive threshold
    kept = [r for r in rows if r['flow'] >= screen_neg]
    screened = len(rows) - len(kept)
    if screened:
        logger.info(
            f"[ts_7AZ_96MA_flow_v2] regime={regime} screened {screened}/{len(rows)} "
            f"with inst net-SELL < {screen_neg/1e6:.0f}M"
        )

    # ── V2.1 LEVER 1: extension cap ────────────────────────────────────
    # NOTE (2026-09-12): the original regime gate (default 'volatile,bear')
    # made this a NO-OP in the Jan-Aug 2026 backtest - that window contains
    # only BULL/NORMAL/BEAR, and BEAR days yield 0 candidates, so the cap had
    # nothing to filter (0 firings across 160 days). Default gate is now ALL
    # regimes; use V2_EXT_CAP_REGIME to restrict it if ever needed.
    # `V2_EXT_CAP_RANGE<=0` disables the 52w-range condition; the validated
    # trim is the trailing-run condition alone (r60>150: removed picks have
    # mean 10d forward return -0.23%, i.e. negative expectancy).
    if V2_EXT_CAP and (not V2_EXT_CAP_REGIMES or regime in V2_EXT_CAP_REGIMES):
        before = len(kept)
        capped = []
        for r in kept:
            if r['r60'] is not None and r['r60'] > V2_EXT_CAP_R60:
                continue
            if (V2_EXT_CAP_RANGE > 0 and r['rng_pos'] is not None
                    and r['rng_pos'] >= V2_EXT_CAP_RANGE):
                continue
            capped.append(r)
        kept = capped
        if before != len(kept):
            _rng_note = f" or range>={V2_EXT_CAP_RANGE:.0f}" if V2_EXT_CAP_RANGE > 0 else ""
            logger.info(
                f"[ts_7AZ_96MA_flow_v2] LEVER1 extension cap (r60>{V2_EXT_CAP_R60:.0f}%"
                f"{_rng_note}) regime={regime}: {before} -> {len(kept)}"
            )

    # ── V2.1 LEVER 2: target the 80-99 52w-range band ─────────────────
    if V2_RANGE_BAND and (not V2_RANGE_BAND_REGIMES or regime in V2_RANGE_BAND_REGIMES):
        before = len(kept)
        kept = [r for r in kept if r['rng_pos'] is None
                or (V2_RANGE_BAND_LO <= r['rng_pos'] < V2_RANGE_BAND_HI)]
        if before != len(kept):
            logger.info(
                f"[ts_7AZ_96MA_flow_v2] LEVER2 range band [{V2_RANGE_BAND_LO:.0f},{V2_RANGE_BAND_HI:.0f}) "
                f"regime={regime}: {before} -> {len(kept)}"
            )

    # ── V2.1 TREND-AGE CAP: reject picks too deep into a MA60-defined trend ────
    if V2_TREND_AGE_CAP and (not V2_TREND_AGE_REGIMES
                             or regime in V2_TREND_AGE_REGIMES):
        before = len(kept)
        age_threshold = _AGE_MAX_FOR_REGIME.get(regime, 120)
        _aged = []
        for r in kept:
            ts_code = str(r['row']['ts_code'])
            days, _ = _trend_age(ts_code, ref_date)
            if days > age_threshold:
                continue
            _aged.append(r)
        kept = _aged
        if before != len(kept):
            logger.info(
                f"[ts_7AZ_96MA_flow_v2] TREND-AGE cap (> {age_threshold}d above MA60 "
                f"regime={regime}): {before} -> {len(kept)}"
            )

    # ── LEVER: 业绩预告 (forecast) structural gate — screen-only ──────────────
    if V2_FORECAST_GATE:
        before = len(kept)
        _fc_kept = []
        for r in kept:
            ngap = float(r['row'].get('non_recurring_gap', 0) or 0)
            fc = _forecast_is_bad(r['code6'], ref_date, non_recurring_gap=ngap)
            if fc is None:   # no forecast = neutral
                _fc_kept.append(r)
                continue
            if fc['ftype'] in V2_FORECAST_REJECT_TYPES:   # 首亏/续亏 etc.
                continue
            if fc['range_width'] > V2_FORECAST_RANGE:     # absurdly wide range
                continue
            if fc['non_recurring_gap'] > 30:              # anchor #3: non-operating income
                continue
            _fc_kept.append(r)
        kept = _fc_kept
        if before != len(kept):
            logger.info(
                f"[ts_7AZ_96MA_flow_v2] FORECAST gate (types={V2_FORECAST_REJECT_TYPES} "
                f"range>{V2_FORECAST_RANGE:.0f}pp gap>30): {before} -> {len(kept)}"
            )
    
    # Log volume boosts applied
    # ── LEVER C: dragon-list (all-seats) net-buy screen — screen-only ─────
    if LHB_DRAGON_SCREEN:
        before = len(kept)
        _kept_c = []
        for r in kept:
            nbr = _dragon_net_buy_ratio(r['code6'], ref_date)
            if nbr is not None and nbr < LHB_DRAGON_NEG:
                continue
            _kept_c.append(r)
        kept = _kept_c
        if before != len(kept):
            logger.info(
                f"[ts_7AZ_96MA_flow_v2] LEVERC dragon screen (净买占比 sum < {LHB_DRAGON_NEG}): "
                f"{before} -> {len(kept)}"
            )

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