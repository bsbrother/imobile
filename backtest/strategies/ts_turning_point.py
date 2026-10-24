"""
ts_turning_point: 转折K线战法 (turning-point candle + one-day confirmation)

Source note: docs/strategies/turning_point.md (今日头条 2026-05-05, 阳生说股).

The claim being encoded: a single big-volume breakout candle out of a low-level
consolidation is NOT a buy signal, because the same day may be a bull trap. Wait
one session; buy only if the NEXT bar holds the breakout level on continuing
volume while MACD has just crossed up and Bollinger bands go from squeeze to
expansion.

Bar semantics (T+1 compliant, no look-ahead):
    Turning candle      = T   (second-to-last closed bar of the reference window)
    Confirmation candle = T+1 (LAST closed bar = the reference date)
    Engine buys         = the session after the reference date, at the open.

So the script is called for trading day D, resolves reference date R = D-1
trading days, and every condition is evaluated on bars closed at R or earlier.
Nothing from D is used.

Article rule -> code:
    prior low-level consolidation, MAs glued   -> consolidation_gate()
    turning candle: fat bullish body           -> TP_GAIN_MIN / TP_BODY_MIN
    closes above MA5/10/20                     -> close_T > ma5_T, ma10_T, ma20_T
    volume expansion                           -> vol_T >= TP_VOL_MULT * mean(vol[T-5:T])
    MACD golden cross                          -> cross within TP_MACD_LOOKBACK bars ending at T
    Bollinger squeeze -> opening               -> width_T > width_T-1, width_T-1 in low percentile
    next day holds breakout, volume continues  -> close_R > close_T, low_R >= ma5_R, vol_R >= TP_CONFIRM_VOL_MIN * vol_T
    next day breaks back below MAs -> skip     -> same gates, mirrored

The strategy only PICKS. Exits belong to the engine (config.json risk_reward
ratios: TP 2%, max_hold_days 1), so the article's multi-week 主升浪 thesis is
truncated by the shared exit model -- see docs/strategies/ts_turning_point.md.

Usage:
    python backtest/strategies/ts_turning_point.py YYYYMMDD [--no-search] [--no-ai]
"""

import os
import sys
import json
from datetime import datetime
import warnings

import numpy as np
import pandas as pd
from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before, convert_trade_date
from backtest.utils.market_regime import detect_market_regime
from backtest.utils.logging_config import configure_logger
from backtest.strategies.ts_ths_dc import no_risky_stocks

warnings.filterwarnings("ignore", category=UserWarning)
warnings.filterwarnings("ignore", category=FutureWarning)

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)


# ── Universe / lookback ────────────────────────────────────────
LOOKBACK_DAYS = int(os.getenv("TP_LOOKBACK_DAYS", "60"))   # bars fetched per session
BASE_WINDOW = 20                                           # consolidation window before T

# ── Turning-candle gates (read at point of use, env-gated) ─────
TP_GAIN_MIN = float(os.getenv("TP_GAIN_MIN", "0.03"))            # (close_T/open_T - 1)
TP_BODY_MIN = float(os.getenv("TP_BODY_MIN", "0.60"))            # body / full range
TP_VOL_MULT = float(os.getenv("TP_VOL_MULT", "1.5"))             # vol_T / mean(vol[T-5:T])
TP_MACD_LOOKBACK = int(os.getenv("TP_MACD_LOOKBACK", "2"))       # cross within N bars ending T
TP_SQUEEZE_PCT = float(os.getenv("TP_SQUEEZE_PCT", "0.35"))      # width percentile ceiling
TP_MA_SPREAD_MAX = float(os.getenv("TP_MA_SPREAD_MAX", "0.03"))  # (maxMA-minMA)/close before T
TP_RANGE_MAX = float(os.getenv("TP_RANGE_MAX", "0.18"))          # 20d high-low range / close

# ── Confirmation gates ─────────────────────────────────────────
TP_CONFIRM_VOL_MIN = float(os.getenv("TP_CONFIRM_VOL_MIN", "0.80"))  # vol_R / vol_T
TP_MA_HOLD_TOL = float(os.getenv("TP_MA_HOLD_TOL", "0.005"))         # low_R >= ma5_R*(1-tol)
TP_MAX_EXTENDED = float(os.getenv("TP_MAX_EXTENDED", "1.15"))        # close_R / ma20_R
TP_LIMIT_UP_PCT = float(os.getenv("TP_LIMIT_UP_PCT", "0.095"))       # skip if R already limit-up

# ── Liquidity / output ─────────────────────────────────────────
TP_MIN_PRICE = float(os.getenv("TP_MIN_PRICE", "3.0"))
TP_MIN_AMOUNT = float(os.getenv("TP_MIN_AMOUNT", "50000"))   # tushare amount in 千元 => 5000万
TP_MAX_PICKS = int(os.getenv("TP_MAX_PICKS", "12"))
TP_MIN_SCORE = float(os.getenv("TP_MIN_SCORE", "45"))

# ── Regime weights ─────────────────────────────────────────────
REGIME_WEIGHTS = {
    # volume confirmation matters most where breakouts fail most often
    'bull':     {'body': 0.15, 'gain': 0.10, 'vol': 0.20, 'squeeze': 0.10, 'break': 0.30, 'confirm': 0.15, 'min_score': 0.0},
    'normal':   {'body': 0.15, 'gain': 0.10, 'vol': 0.25, 'squeeze': 0.15, 'break': 0.20, 'confirm': 0.15, 'min_score': 0.0},
    'volatile': {'body': 0.15, 'gain': 0.10, 'vol': 0.25, 'squeeze': 0.20, 'break': 0.10, 'confirm': 0.20, 'min_score': 5.0},
    'bear':     {'body': 0.10, 'gain': 0.10, 'vol': 0.25, 'squeeze': 0.25, 'break': 0.05, 'confirm': 0.25, 'min_score': 8.0},
}

# Gate counters, reported once per session so a lever's firing count is visible.
REJECT = {k: 0 for k in (
    'no_data', 'short_history', 'price', 'liquidity', 'consolidation',
    'body', 'gain', 'ma_break', 'volume', 'macd', 'boll', 'limit_up',
    'confirm_hold', 'confirm_ma', 'confirm_vol', 'extended',
)}


def _reset_reject() -> None:
    for k in REJECT:
        REJECT[k] = 0


# ── Indicators ────────────────────────────────────────────────
def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    diff = ema_fast - ema_slow
    dea = diff.ewm(span=signal, adjust=False).mean()
    return diff, dea, (diff - dea) * 2


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return upper, mid, lower, width


def _pct_rank(values: np.ndarray, x: float) -> float:
    """Fraction of `values` at or below x (0..1). Tighter squeeze -> lower rank."""
    vals = values[~np.isnan(values)]
    if len(vals) == 0:
        return 1.0
    return float((vals <= x).sum()) / len(vals)


# ── Signal ────────────────────────────────────────────────────
def analyze_turning_point(df: pd.DataFrame, name: str = '') -> dict | None:
    """Evaluate one stock's bars. Returns a scored pick dict, or None on reject.

    `df` must be sorted ascending by trade_date and end at the CONFIRMATION bar.
    Reject reasons are counted in REJECT for the session log.
    """
    if df is None or df.empty:
        REJECT['no_data'] += 1
        return None
    if len(df) < BASE_WINDOW + 30:
        REJECT['short_history'] += 1
        return None

    close = df['close'].astype(float).reset_index(drop=True)
    open_ = df['open'].astype(float).reset_index(drop=True)
    high = df['high'].astype(float).reset_index(drop=True)
    low = df['low'].astype(float).reset_index(drop=True)
    vol = df['vol'].astype(float).reset_index(drop=True) if 'vol' in df else pd.Series(0.0, index=close.index)
    amount = df['amount'].astype(float).reset_index(drop=True) if 'amount' in df else pd.Series(np.inf, index=close.index)

    n = len(close)
    iT, iC = n - 2, n - 1          # turning candle, confirmation candle

    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    _, mid, _, width = bollinger(close)
    diff, dea, hist = macd(close)

    price = close.iloc[iC]
    if price < TP_MIN_PRICE:
        REJECT['price'] += 1
        return None
    if amount.iloc[iC] < TP_MIN_AMOUNT:
        REJECT['liquidity'] += 1
        return None

    # 1. Consolidation before T: MAs glued + price range compressed.
    lo, hi = iT - BASE_WINDOW, iT          # [iT-20, iT-1]
    if lo < 20:
        REJECT['short_history'] += 1
        return None
    spreads = []
    for j in range(lo, hi):
        m = (ma5.iloc[j], ma10.iloc[j], ma20.iloc[j])
        if any(pd.isna(v) for v in m) or close.iloc[j] <= 0:
            spreads.append(1.0)
        else:
            spreads.append((max(m) - min(m)) / close.iloc[j])
    med_spread = float(np.median(spreads))
    base_high = float(high.iloc[lo:hi].max())
    base_low = float(low.iloc[lo:hi].min())
    base_range = (base_high - base_low) / close.iloc[iT - 1] if close.iloc[iT - 1] > 0 else 1.0

    if med_spread > TP_MA_SPREAD_MAX or base_range > TP_RANGE_MAX:
        REJECT['consolidation'] += 1
        return None

    # 2. Turning candle T.
    oT, cT, hT, lT = open_.iloc[iT], close.iloc[iT], high.iloc[iT], low.iloc[iT]
    rng = hT - lT
    body_ratio = (cT - oT) / rng if rng > 0 else 0.0
    gain = (cT / oT - 1.0) if oT > 0 else -1.0

    if body_ratio < TP_BODY_MIN:
        REJECT['body'] += 1
        return None
    if gain < TP_GAIN_MIN:
        REJECT['gain'] += 1
        return None
    if not (cT > ma5.iloc[iT] and cT > ma10.iloc[iT] and cT > ma20.iloc[iT]):
        REJECT['ma_break'] += 1
        return None

    vol_base = float(vol.iloc[max(0, iT - 5):iT].mean())
    vol_ratio = float(vol.iloc[iT] / vol_base) if vol_base > 0 else 0.0
    if vol_ratio < TP_VOL_MULT:
        REJECT['volume'] += 1
        return None

    cross_ok = False
    for j in range(max(1, iT - TP_MACD_LOOKBACK + 1), iT + 1):
        if diff.iloc[j - 1] <= dea.iloc[j - 1] and diff.iloc[j] > dea.iloc[j]:
            cross_ok = True
            break
    if not cross_ok:
        REJECT['macd'] += 1
        return None

    w_prev = width.iloc[iT - 1]
    w_now = width.iloc[iT]
    if pd.isna(w_prev) or pd.isna(w_now) or w_now <= w_prev:
        REJECT['boll'] += 1
        return None
    squeeze_rank = _pct_rank(width.iloc[max(0, iT - BASE_WINDOW):iT].to_numpy(), float(w_prev))
    if squeeze_rank > TP_SQUEEZE_PCT:
        REJECT['boll'] += 1
        return None

    # 3. Confirmation candle (the reference date itself).
    pre_close = float(df['pre_close'].iloc[iC]) if 'pre_close' in df and not pd.isna(df['pre_close'].iloc[iC]) else float(close.iloc[iC - 1])
    if pre_close > 0 and (close.iloc[iC] / pre_close - 1.0) >= TP_LIMIT_UP_PCT:
        REJECT['limit_up'] += 1
        return None

    cC, lC = close.iloc[iC], low.iloc[iC]
    if cC <= cT:
        REJECT['confirm_hold'] += 1
        return None
    if lC < ma5.iloc[iC] * (1 - TP_MA_HOLD_TOL):
        REJECT['confirm_ma'] += 1
        return None
    if float(vol.iloc[iC]) < TP_CONFIRM_VOL_MIN * float(vol.iloc[iT]):
        REJECT['confirm_vol'] += 1
        return None
    if ma20.iloc[iC] > 0 and cC / ma20.iloc[iC] > TP_MAX_EXTENDED:
        REJECT['extended'] += 1
        return None

    # 4. Score.
    gap_up = (cC / cT - 1.0)
    break_pct = (cT - base_high) / base_high if base_high > 0 else 0.0
    return {
        'name': name,
        'price': round(float(price), 2),
        'close_T': round(float(cT), 2),
        'close_R': round(float(cC), 2),
        'gain_T_pct': round(gain * 100, 2),
        'body_ratio': round(float(body_ratio), 3),
        'vol_ratio': round(vol_ratio, 2),
        'confirm_gap_pct': round(gap_up * 100, 2),
        'break_pct': round(break_pct * 100, 2),
        'squeeze_rank': round(squeeze_rank, 2),
        'boll_width_now': round(float(w_now) * 100, 2),
        'macd_hist': round(float(hist.iloc[iT]), 4),
        'ma_spread': round(med_spread * 100, 2),
        'base_range_pct': round(base_range * 100, 1),
        'price_vs_ma20': round(float(cC / ma20.iloc[iC]), 3) if ma20.iloc[iC] > 0 else 1.0,
    }


def score_pick(m: dict, regime: str) -> float:
    """0-100 composite; higher = cleaner turning point with a stronger confirmation."""
    w = REGIME_WEIGHTS.get(regime, REGIME_WEIGHTS['normal'])
    body = min(m['body_ratio'], 1.0) * 100
    gain = min(m['gain_T_pct'] / 6.0, 1.0) * 100
    vol = min(max(m['vol_ratio'] - 1.0, 0.0) / 2.0, 1.0) * 100
    squeeze = (1.0 - m['squeeze_rank']) * 100
    brk = min(max(m['break_pct'], 0.0) / 3.0, 1.0) * 100
    confirm = min(max(m['confirm_gap_pct'], 0.0) / 3.0, 1.0) * 100

    composite = (body * w['body'] + gain * w['gain'] + vol * w['vol'] +
                 squeeze * w['squeeze'] + brk * w['break'] + confirm * w['confirm'])
    # A breakout that clears the consolidation high is materially better than one
    # that only clears the MAs; reward it, and reward positive MACD momentum.
    if m['break_pct'] > 0:
        composite += 5.0
    if m['macd_hist'] > 0:
        composite += 2.5
    return round(composite + w['min_score'], 1)


# ── Bulk OHLCV access ─────────────────────────────────────────
# The engine spawns one subprocess per session, and fetching 60 trading days of
# per-date bulk OHLCV costs ~60s (one cached tushare call per date) against ~30s
# of analysis — for 180 sessions that is ~3h of re-reading the same overlapping
# window. Keep a rolling long-format panel in the cross-process pickle cache and
# extend it by the single NEW date each session (~0.7s). Only bars <= the
# reference date ever enter the panel, so the cache cannot leak future data.
PANEL_CACHE_KEY = 'tp_ohlcv_panel_v1'
TP_PANEL_CACHE = os.getenv('TP_PANEL_CACHE', 'true').lower() in ('1', 'true', 'yes')
PANEL_COLS = ['ts_code', 'trade_date', 'open', 'high', 'low', 'close', 'pre_close', 'vol', 'amount']


def _long_frame(bulk: dict) -> pd.DataFrame:
    """{ts_code: ohlcv DataFrame} -> one long DataFrame of PANEL_COLS."""
    frames = []
    for code, sdf in bulk.items():
        if sdf is None or sdf.empty:
            continue
        sub = sdf.copy()
        sub['ts_code'] = code
        for col in PANEL_COLS:
            if col not in sub.columns:
                sub[col] = np.nan
        frames.append(sub[PANEL_COLS])
    if not frames:
        return pd.DataFrame(columns=pd.Index(PANEL_COLS))
    out = pd.concat(frames, ignore_index=True)
    out['trade_date'] = out['trade_date'].astype(str)
    return out.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')


PANEL_HEADROOM = 10   # extra trading days kept in the cache before the needed window


def _fetch_long(start_date: str, end_date: str) -> pd.DataFrame:
    """Fetch bulk OHLCV for a date range as one long frame (isolated for testing)."""
    if start_date > end_date:
        return pd.DataFrame(columns=pd.Index(PANEL_COLS))
    return _long_frame(data_provider.get_bulk_ohlcv_by_date_range(start_date, end_date))


def load_panel(end_date: str) -> dict:
    """{ts_code: DataFrame} covering at least LOOKBACK_DAYS bars ending at `end_date`.

    The stored panel is a UNION that only ever grows: a request for an EARLIER
    end_date slices the view but never shrinks what is stored (an earlier version
    did, and a backwards session truncated the panel to one day — every stock then
    failed the history gate and the run silently picked nothing for a month).
    Both gaps are backfilled: the tail (dates after the cached max) and, critically,
    the head (dates before the cached min) when a later session needs an older
    window than the cache holds.
    """
    need_start = get_trading_days_before(end_date, LOOKBACK_DAYS)
    cache = None
    if TP_PANEL_CACHE:
        try:
            from backtest.data.cache import get_global_cache
            cache = get_global_cache()
        except Exception as exc:                     # a cache must never break a run
            logger.warning(f"[ts_turning_point] panel cache unavailable: {exc}")

    panel = None
    if cache is not None:
        try:
            panel = cache.get(PANEL_CACHE_KEY)
        except Exception:
            panel = None

    full = None
    if isinstance(panel, dict) and isinstance(panel.get('df'), pd.DataFrame) and not panel['df'].empty:
        full = panel['df'].copy()
        full['trade_date'] = full['trade_date'].astype(str)

    if full is None:
        full = _fetch_long(need_start, end_date)
        logger.info(f"[ts_turning_point] panel built: {len(full)} rows {need_start}..{end_date}")
    else:
        first, through = str(full['trade_date'].min()), str(full['trade_date'].max())
        if through < end_date:                        # tail gap
            tail = _fetch_long(through, end_date)
            full = pd.concat([full, tail], ignore_index=True)
            logger.info(f"[ts_turning_point] panel tail backfill {through}..{end_date} (+{len(tail)} rows)")
        if first > need_start:                        # head gap — the silent-zero bug
            head = _fetch_long(need_start, first)
            full = pd.concat([full, head], ignore_index=True)
            logger.info(f"[ts_turning_point] panel head backfill {need_start}..{first} (+{len(head)} rows)")
        full = full.drop_duplicates(subset=['ts_code', 'trade_date'], keep='last')
        logger.info(f"[ts_turning_point] panel from cache: {len(full)} rows "
                    f"{full['trade_date'].min()}..{full['trade_date'].max()}")

    if full is None or full.empty:
        logger.error(f"[ts_turning_point] NO OHLCV data for {need_start}..{end_date}")
        return {}

    # Store: keep headroom before the needed window, never drop the tail.
    floor = get_trading_days_before(need_start, PANEL_HEADROOM)
    store = full[full['trade_date'] >= floor]
    if cache is not None and not store.empty:
        try:
            cache.set(PANEL_CACHE_KEY, {'df': store, 'through': str(store['trade_date'].max())},
                      ttl=30 * 24 * 3600)
        except Exception as exc:
            logger.warning(f"[ts_turning_point] panel cache write failed: {exc}")

    # View: exactly the window this session may look at — no bar after `end_date`.
    view = store[(store['trade_date'] >= need_start) & (store['trade_date'] <= end_date)]
    n_dates = view['trade_date'].nunique()
    if n_dates < LOOKBACK_DAYS * 0.9:
        logger.error(f"[ts_turning_point] panel coverage {n_dates} dates < {LOOKBACK_DAYS} "
                     f"for {need_start}..{end_date} — picks will be empty; refusing to guess")

    out = {}
    for code, sub in view.groupby('ts_code', sort=False):
        out[code] = sub.sort_values(by='trade_date').reset_index(drop=True)
    return out


def _rows_by_code(day_df: pd.DataFrame) -> dict:
    """A single-date bulk frame -> {ts_code: one-row DataFrame} for _long_frame()."""
    if 'ts_code' not in day_df.columns:
        return {}
    return {code: sub for code, sub in day_df.groupby('ts_code', sort=False)}


def pick_turning_point_stocks(end_date: str, max_picks: int | None = None) -> pd.DataFrame:
    """Pick stocks whose turning candle was confirmed on `end_date` (the reference bar).

    Args:
        end_date: reference trading date YYYYMMDD — the LAST CLOSED bar
        max_picks: cap on returned rows (default TP_MAX_PICKS)
    """
    _reset_reject()
    max_picks = TP_MAX_PICKS if max_picks is None else max_picks
    logger.info(f"[ts_turning_point] Reference date {end_date} (turning candle = prev session)")

    regime_data = detect_market_regime(end_date)
    regime = regime_data.get('regime', 'normal')
    logger.info(f"[ts_turning_point] Market Regime: {regime}")

    stock_basic = data_provider.get_basic_information_api()
    if stock_basic.empty:
        raise ValueError("No basic stock information found")
    risky_free = no_risky_stocks(stock_basic=stock_basic)
    stock_basic = stock_basic[stock_basic['ts_code'].isin(risky_free)].reset_index(drop=True)
    logger.info(f"[ts_turning_point] Universe: {len(stock_basic)} stocks")

    all_data = load_panel(end_date)
    logger.info(f"[ts_turning_point] OHLCV panel: {len(all_data)} stocks through {end_date}")

    picks = []
    for _, row in stock_basic.iterrows():
        ts_code = row['ts_code']
        df = all_data.get(ts_code)
        if df is None or df.empty:
            REJECT['no_data'] += 1
            continue
        df = df.sort_values('trade_date').reset_index(drop=True)
        # Only bars closed at/before the reference date.
        df = df[df['trade_date'].astype(str) <= end_date]
        m = analyze_turning_point(df, name=str(row.get('name', '')))
        if m is None:
            continue
        m['ts_code'] = ts_code
        m['composite_score'] = score_pick(m, regime)
        picks.append(m)

    logger.info("[ts_turning_point] Gate rejections: " +
                ", ".join(f"{k}={v}" for k, v in REJECT.items() if v))

    if not picks:
        logger.warning("[ts_turning_point] No stock passed the turning-point gates")
        return pd.DataFrame()

    df = pd.DataFrame(picks).sort_values('composite_score', ascending=False).reset_index(drop=True)
    before = len(df)
    df = df[df['composite_score'] >= TP_MIN_SCORE]
    if before and df.empty:
        logger.warning(f"[ts_turning_point] TP_MIN_SCORE={TP_MIN_SCORE} filtered all {before} candidates")
        return pd.DataFrame()

    df = df.head(max_picks).reset_index(drop=True)
    df['rank'] = range(1, len(df) + 1)

    for _, r in df.head(8).iterrows():
        logger.info(
            f"[ts_turning_point] #{r['rank']}: {r['name']}({r['ts_code']}) "
            f"score={r['composite_score']:.1f} T_gain={r['gain_T_pct']:.1f}% "
            f"vol_x={r['vol_ratio']:.1f} confirm=+{r['confirm_gap_pct']:.1f}% "
            f"squeeze={r['squeeze_rank']:.2f} break={r['break_pct']:.1f}%"
        )
    return df


if __name__ == "__main__":
    argv = sys.argv[1:]
    flags = [a for a in argv if a.startswith('--')]
    args = [a for a in argv if not a.startswith('--')]

    date = convert_trade_date(args[0]) if args else datetime.now().strftime('%Y%m%d')
    target_date = date
    # Engine passes the SESSION date; picks must be computed on the prior close.
    date = get_trading_days_before(date, 1)
    logger.info(f"[ts_turning_point] Target session {target_date}, reference date {date} ({' '.join(flags)})")

    df = pick_turning_point_stocks(end_date=date)

    output_file = os.environ.get('PICK_OUTPUT') or '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_turning_point_tmp.json'

    selected = []
    for _, s in df.iterrows():
        selected.append({
            'rank': int(s['rank']),
            'symbol': s['ts_code'],
            'name': s.get('name', ''),
            'score': float(s['composite_score']),
        })

    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected}, f)
    logger.info(f"[ts_turning_point] Saved {len(selected)} picks to {output_file}")
