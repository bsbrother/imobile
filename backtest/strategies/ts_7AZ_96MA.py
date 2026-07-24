"""
ts_7AZ_96MA: Combined CANSLIM + MA96 Support Strategy

Combines the best of both strategies:
- ts_7AZ (CANSLIM): EPS≥25%, ROE≥17%, RPS≥80, near 52w-high → momentum stocks that RUN
- ts_96MA (MA96): close > MA96, slope ≥ -0.5% → support confirmation

ts_7AZ wins in momentum months (Feb/Apr/May/Jun) — picks rise 15-37% before SL.
ts_96MA wins in pullback months (Jan/Mar) — MA96 support catches dips.

Combined: CANSLIM hard filter (quality) + MA96 support check (timing) + RPS/bounce.

Usage:
    python backtest/strategies/ts_7AZ_96MA.py YYYYMMDD [--lookahead]
"""

import os
import sys
import json
import time as time_mod
from datetime import datetime
import pandas as pd
import numpy as np
import warnings
from typing import Optional

from dotenv import load_dotenv
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before, convert_trade_date
from backtest.utils.market_regime import detect_market_regime
from backtest.utils.logging_config import configure_logger
from backtest.strategies.ts_ths_dc import no_risky_stocks

warnings.filterwarnings("ignore", category=UserWarning)

load_dotenv()
LOG_LEVEL = os.getenv("LOG_LEVEL", default="INFO")
LOG_PATH = os.getenv("LOG_PATH", default="./logs")
configure_logger(log_level=LOG_LEVEL, log_path=LOG_PATH)

# ─── CANSLIM Parameters (from ts_7AZ) ─────────────────────────────
C_EPS_GROWTH_THRESHOLD = 0.25    # EPS growth ≥ 25%
A_ROE_THRESHOLD = 0.17           # ROE ≥ 17%
N_52W_HIGH_RATIO = 0.80          # Price ≥ 80% of 52-week high (relaxed from 85%)
S_MARKET_CAP_MAX = 1000e8        # Market cap ≤ 1000亿 (relaxed from 500亿)
L_RPS_THRESHOLD = 70             # RPS(250d) ≥ 70 (relaxed from 80)
I_TURNOVER_MIN = 0.003           # Turnover 0.3%-30% (A-shares realistic)
I_TURNOVER_MAX = 0.30
M_MA200_ABOVE = False            # Don't require 200MA (too restrictive with MA96)

# ─── MA96 Support Parameters (from ts_96MA) ───────────────────────
MA96_PERIOD = 96
MA60_PERIOD = 60
MA24_PERIOD = 24
SLOPE_WINDOW = 5
MA96_MIN_SLOPE = -0.5
MAX_ABOVE_MA96_PCT = 15.0
PULLBACK_ZONE_PCT = 5.0

# ─── Combined Parameters ──────────────────────────────────────────
LOOKBACK_DAYS = 280
RPS_LOOKBACK = 250
MIN_SCORE_THRESHOLD = 40

# Tushare setup
import tushare as ts
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
PRO = ts.pro_api(TUSHARE_TOKEN) if TUSHARE_TOKEN else None


def calculate_ma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(window=period).mean()


def calculate_slope(series: pd.Series, window: int = 5) -> float:
    if len(series) < window + 1:
        return np.nan
    prev = series.iloc[-1 - window]
    curr = series.iloc[-1]
    if pd.isna(prev) or pd.isna(curr) or prev == 0:
        return np.nan
    return (curr - prev) / prev * 100


def fetch_financial_data(ts_code: str) -> dict:
    """Fetch EPS/ROE from Tushare (same as ts_7AZ)."""
    if PRO is None:
        return {'eps_growth': None, 'roe': None}
    try:
        fina = PRO.fina_indicator(ts_code=ts_code, period_type=0)
        if fina.empty:
            return {'eps_growth': None, 'roe': None}
        latest = fina.sort_values('end_date', ascending=False).iloc[0]
        return {
            'eps_growth': latest.get('q_dtprofit_yoy', None),
            'roe': latest.get('roe', None),
        }
    except Exception:
        return {'eps_growth': None, 'roe': None}


def compute_rps(close: pd.Series, lookback: int = 250) -> float:
    """250-day return percentile."""
    if len(close) < lookback:
        return 0.0
    ret = (close.iloc[-1] - close.iloc[-lookback]) / close.iloc[-lookback]
    return ret * 100


def analyze_stock_combined(ts_code: str, df: pd.DataFrame,
                            daily_basic_row: Optional[pd.Series] = None,
                            index_returns_20d: float = 0.0) -> Optional[dict]:
    """
    Combined analysis: CANSLIM hard filter + MA96 support check + scoring.
    """
    try:
        if df is None or df.empty or len(df) < 100:
            return None

        df = df.sort_values('trade_date', ascending=True).reset_index(drop=True)

        close = df['close'].astype(float)
        high = df['high'].astype(float)
        low = df['low'].astype(float)
        vol = df['vol'].astype(float)

        latest_close = close.iloc[-1]

        # ─── CANSLIM SCORING (not hard filter) ───
        # Hard filters from ts_7AZ (52w-high, turnover, market cap) combined with
        # MA96 support check are too restrictive — only 11 stocks pass, all large-cap.
        # Instead: score them as bonus points, keep hard filters minimal.
        canslim_score = 0
        eps_growth = None
        roe = None

        # N: Near 52-week high (bonus, not hard filter)
        if len(close) >= 250:
            high_52w = float(close.tail(250).max())
            if high_52w > 0 and latest_close / high_52w >= N_52W_HIGH_RATIO:
                canslim_score += 10
        elif len(close) >= 120:
            high_120d = float(close.tail(120).max())
            if high_120d > 0 and latest_close / high_120d >= N_52W_HIGH_RATIO:
                canslim_score += 10

        # I: Turnover rate (bonus)
        if daily_basic_row is not None:
            turnover = daily_basic_row.get('turnover_rate', 0)
            if isinstance(turnover, str):
                turnover = float(turnover.replace('%', '')) / 100 if '%' in turnover else float(turnover) / 100
            elif turnover is None:
                turnover = 0
            if I_TURNOVER_MIN <= turnover <= I_TURNOVER_MAX:
                canslim_score += 5

        # S: Market cap (bonus)
        if daily_basic_row is not None:
            total_mv = daily_basic_row.get('total_mv', 1e12)
            if isinstance(total_mv, str):
                total_mv = float(total_mv.replace('亿', '')) * 1e8 if '亿' in total_mv else float(total_mv)
            elif total_mv is None:
                total_mv = 1e12
            if total_mv <= S_MARKET_CAP_MAX:
                canslim_score += 5

        # ─── MA96 SUPPORT CHECK (from ts_96MA) ───
        if len(close) < MA96_PERIOD:
            return None

        ma96 = calculate_ma(close, MA96_PERIOD)
        ma60 = calculate_ma(close, MA60_PERIOD)
        ma24 = calculate_ma(close, MA24_PERIOD)

        latest_ma96 = ma96.iloc[-1]
        if pd.isna(latest_ma96):
            return None

        # Price must be above MA96 (support intact)
        if latest_close < latest_ma96:
            return None

        # MA96 slope must be flat or rising
        ma96_slope = calculate_slope(ma96, SLOPE_WINDOW)
        if pd.isna(ma96_slope) or ma96_slope < MA96_MIN_SLOPE:
            return None

        # Chasing guard: not >15% above MA96
        pct_above_ma96 = (latest_close - latest_ma96) / latest_ma96 * 100
        if pct_above_ma96 > MAX_ABOVE_MA96_PCT:
            return None

        # ─── RPS (from ts_96MA) ───
        ret_250 = compute_rps(close, RPS_LOOKBACK)

        # ─── Bounce confirmation (from ts_96MA) ───
        open_prices = df['open'].astype(float) if 'open' in df.columns else None
        up_close_count = 0
        if open_prices is not None and len(close) >= 4:
            for i in range(-1, -4, -1):
                if pd.isna(close.iloc[i]) or pd.isna(open_prices.iloc[i]):
                    break
                if close.iloc[i] > open_prices.iloc[i]:
                    up_close_count += 1

        # ─── ADX (trend strength) ───
        def _adx(high, low, close, period=14):
            tr = pd.concat([high - low, np.abs(high - close.shift(1)), np.abs(low - close.shift(1))], axis=1).max(axis=1)
            up_move = high - high.shift(1)
            down_move = low.shift(1) - low
            plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0), index=close.index)
            minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0), index=close.index)
            atr = tr.ewm(span=period, adjust=False).mean()
            plus_di = 100 * (plus_dm.ewm(span=period, adjust=False).mean() / atr)
            minus_di = 100 * (minus_dm.ewm(span=period, adjust=False).mean() / atr)
            dx = 100 * np.abs(plus_di - minus_di) / (plus_di + minus_di + 1e-10)
            return dx.ewm(span=period, adjust=False).mean()

        adx = _adx(high, low, close).iloc[-1]

        # ─── SCORING (0-100 base + CANSLIM bonus) ───
        score = 0.0
        signals = []

        # 1. MA96 Slope (25 pts)
        if ma96_slope > 1.0:
            score += 25.0
            signals.append(f"MA96_slope_up({ma96_slope:.2f}%)")
        elif ma96_slope > 0.0:
            score += 15.0
            signals.append(f"MA96_slope_flat_up({ma96_slope:.2f}%)")
        else:
            score += 5.0
            signals.append(f"MA96_slope_flat({ma96_slope:.2f}%)")

        # 2. Pullback Proximity (20 pts)
        if pct_above_ma96 <= PULLBACK_ZONE_PCT:
            if pct_above_ma96 <= 2.0:
                score += 20.0
                signals.append(f"pullback_near_MA96({pct_above_ma96:.1f}%)")
            else:
                score += 12.0
                signals.append(f"pullback_zone({pct_above_ma96:.1f}%)")

        # 3. 60MA/96MA Cross (15 pts)
        latest_ma60 = ma60.iloc[-1]
        if not pd.isna(latest_ma60) and latest_ma60 > latest_ma96:
            score += 15.0
            signals.append("MA60>MA96_cross")

        # 4. 24MA/96MA Cross (10 pts)
        latest_ma24 = ma24.iloc[-1]
        if not pd.isna(latest_ma24) and latest_ma24 > latest_ma96:
            score += 10.0
            signals.append("MA24>MA96_cross")

        # 5. Bounce (10 pts)
        if up_close_count >= 3:
            score += 10.0
            signals.append("strong_bounce(3/3)")
        elif up_close_count >= 2:
            score += 6.0
            signals.append(f"bounce({up_close_count}/3)")

        # 6. Volume (15 pts)
        if len(vol) >= 20:
            vol_5d = vol.tail(5).mean()
            vol_20d = vol.tail(20).mean()
            vol_ratio = vol_5d / vol_20d if vol_20d > 0 else 1.0
            if vol_ratio < 0.9:
                score += 10.0
                signals.append(f"vol_contract({vol_ratio:.2f})")
            latest_vol = vol.iloc[-1]
            if latest_vol > vol_5d * 1.2 and latest_close > close.iloc[-2]:
                score += 5.0
                signals.append("vol_bounce")

        # 7. ADX (10 pts)
        if not pd.isna(adx) and adx >= 20:
            score += 10.0
            signals.append(f"ADX({adx:.0f})")

        # 8. RPS (5 pts)
        if ret_250 > 50:
            score += 5.0
            signals.append(f"RPS({ret_250:.0f}%)")

        # ─── CANSLIM Bonus (0-20 pts) ───
        canslim_score = 0
        eps_growth = None
        roe = None

        if eps_growth is not None and eps_growth >= C_EPS_GROWTH_THRESHOLD:
            canslim_score += 10
        if roe is not None and roe >= A_ROE_THRESHOLD:
            canslim_score += 10

        # ─── Stop-Loss & Take-Profit (from ts_96MA) ───
        if pct_above_ma96 <= 2.0:
            stop_loss_price = round(latest_ma96 * 0.98, 2)
            sl_percent = round((latest_close - stop_loss_price) / latest_close, 4)
            tp_percent = 0.05
        elif pct_above_ma96 <= 5.0:
            stop_loss_price = round(latest_ma96 * 0.975, 2)
            sl_percent = round((latest_close - stop_loss_price) / latest_close, 4)
            tp_percent = 0.07
        else:
            stop_loss_price = round(latest_ma96 * 0.97, 2)
            sl_percent = round((latest_close - stop_loss_price) / latest_close, 4)
            tp_percent = 0.10

        if up_close_count >= 3:
            tp_percent = min(tp_percent + 0.02, 0.12)

        if score < MIN_SCORE_THRESHOLD:
            return None

        final_score = score + canslim_score

        return {
            'ts_code': ts_code,
            'close': latest_close,
            'ma96': round(latest_ma96, 2),
            'pct_above_ma96': round(pct_above_ma96, 2),
            'ma96_slope': round(ma96_slope, 3),
            'adx': round(adx, 1) if not pd.isna(adx) else 0,
            'ret_250': round(ret_250, 2),
            'stop_loss_price': stop_loss_price,
            'sl_percent': sl_percent,
            'tp_percent': tp_percent,
            'composite_score': round(final_score, 2),
            'base_score': round(score, 2),
            'canslim_score': canslim_score,
            'eps_growth': eps_growth,
            'roe': roe,
            'signals': signals,
        }

    except Exception as e:
        logger.warning(f"Error analyzing {ts_code}: {e}")
        return None


def pick_combined_stocks(end_date: str, max_picks: int = 12) -> pd.DataFrame:
    """
    Combined CANSLIM + MA96 strategy.
    """
    logger.info(f"[ts_7AZ_96MA] Starting combined picking for {end_date}")

    # Get stock universe
    stock_basic = data_provider.get_basic_information_api()
    if stock_basic.empty:
        raise ValueError("No basic stock information found")

    total_stocks = len(stock_basic)
    risky_free_list = no_risky_stocks(stock_basic)
    stock_basic = stock_basic[stock_basic['ts_code'].isin(risky_free_list)].reset_index(drop=True)
    logger.info(f"[ts_7AZ_96MA] Universe: {total_stocks} -> {len(stock_basic)} mainboard")

    # Market regime
    regime_data = detect_market_regime(end_date)
    regime = regime_data.get('regime', 'normal')
    logger.info(f"[ts_7AZ_96MA] Market Regime: {regime}")

    if regime == 'bear':
        logger.warning("[ts_7AZ_96MA] Bear regime — 0 picks.")
        return pd.DataFrame()

    max_picks = 12
    min_score = 40

    # Get daily_basic for turnover/market_cap pre-filter (from ts_7AZ)
    daily_basic_df = None
    try:
        if PRO:
            daily_basic_df = PRO.daily_basic(trade_date=end_date)
            if daily_basic_df is not None and not daily_basic_df.empty:
                daily_basic_df = daily_basic_df.set_index('ts_code')
                logger.info(f"[ts_7AZ_96MA] daily_basic: {len(daily_basic_df)} stocks")
    except Exception as e:
        logger.warning(f"[ts_7AZ_96MA] daily_basic failed: {e}")

    # Get index returns for relative strength
    index_returns_20d = 0.0
    try:
        idx_start = get_trading_days_before(end_date, 25)
        idx_df = data_provider.get_index_data('000300.SH', idx_start, end_date)
        if idx_df is not None and len(idx_df) >= 20:
            idx_df = idx_df.sort_values('trade_date', ascending=True)
            index_returns_20d = (idx_df['close'].iloc[-1] / idx_df['close'].iloc[-20] - 1) * 100
    except Exception:
        pass

    # Bulk fetch OHLCV
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS)
    logger.info(f"[ts_7AZ_96MA] Bulk fetching {start_date} -> {end_date}...")
    all_stock_data = data_provider.get_bulk_ohlcv_by_date_range(start_date, end_date)
    logger.info(f"[ts_7AZ_96MA] Bulk fetch: {len(all_stock_data)} stocks")

    # Analyze
    results = []
    total = len(stock_basic)
    analyzed = 0

    for idx, row in stock_basic.iterrows():
        if idx % 500 == 0:
            logger.info(f"[ts_7AZ_96MA] Analyzing: {idx}/{total}")

        ts_code = row['ts_code']
        stock_df = all_stock_data.get(ts_code)

        if stock_df is None:
            continue

        # Get daily_basic row for turnover/market_cap filter
        db_row = None
        if daily_basic_df is not None and ts_code in daily_basic_df.index:
            db_row = daily_basic_df.loc[ts_code]

        analysis = analyze_stock_combined(ts_code, stock_df, db_row, index_returns_20d)
        if analysis:
            analysis['name'] = row['name']
            results.append(analysis)
            analyzed += 1

    logger.info(f"[ts_7AZ_96MA] Analyzed {analyzed} stocks passed filters")

    if not results:
        logger.warning("[ts_7AZ_96MA] No stocks passed")
        return pd.DataFrame()

    df = pd.DataFrame(results)

    # RPS filter
    if len(df) >= 20 and 'ret_250' in df.columns:
        df['rps'] = df['ret_250'].rank(pct=True) * 100
        before = len(df)
        df = df[df['rps'] >= L_RPS_THRESHOLD].copy()
        logger.info(f"[ts_7AZ_96MA] RPS≥{L_RPS_THRESHOLD}: {before} -> {len(df)}")

    # CANSLIM fundamentals (top 30 only)
    if len(df) > 0 and PRO:
        top = df.sort_values('composite_score', ascending=False).head(min(30, len(df)))
        logger.info(f"[ts_7AZ_96MA] CANSLIM check on top {len(top)}...")
        canslim_count = 0
        for _, row in top.iterrows():
            try:
                fin = fetch_financial_data(str(row['ts_code']))
                bonus = 0
                if fin.get('eps_growth') is not None and fin['eps_growth'] >= C_EPS_GROWTH_THRESHOLD:
                    bonus += 10
                if fin.get('roe') is not None and fin['roe'] >= A_ROE_THRESHOLD:
                    bonus += 10
                if bonus > 0:
                    mask = df['ts_code'] == row['ts_code']
                    df.loc[mask, 'composite_score'] = df.loc[mask, 'composite_score'] + bonus
                    df.loc[mask, 'canslim_score'] = bonus
                    canslim_count += 1
            except Exception:
                pass
        if canslim_count > 0:
            logger.info(f"[ts_7AZ_96MA] CANSLIM bonus: {canslim_count} stocks")

    df = df.sort_values('composite_score', ascending=False)

    # Score threshold
    before = len(df)
    df = df[df['composite_score'] >= min_score]
    logger.info(f"[ts_7AZ_96MA] Score≥{min_score}: {before} -> {len(df)}")

    if len(df) == 0:
        return pd.DataFrame()

    df = df.head(max_picks)
    df = df.reset_index(drop=True)
    df['rank'] = range(1, len(df) + 1)

    logger.info(f"[ts_7AZ_96MA] Selected {len(df)} stocks")
    for _, row in df.head(8).iterrows():
        signals_str = ', '.join(row.get('signals', [])[:3])
        logger.info(
            f"[ts_7AZ_96MA] #{row['rank']}: {row['name']}({row['ts_code']}) "
            f"score={row['composite_score']:.1f} MA96={row.get('ma96_slope',0):.2f}% "
            f"pct={row.get('pct_above_ma96',0):.1f}% canslim={row.get('canslim_score',0)} | {signals_str}"
        )

    return df


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

    logger.info(f"[ts_7AZ_96MA] Picking for target {target_date} ref={date}")

    df = pick_combined_stocks(end_date=date)

    output_file = '/tmp/tmp'
    selected_stocks = []

    if not df.empty:
        for _, stock in df.iterrows():
            selected_stocks.append({
                'rank': int(stock['rank']),
                'symbol': stock['ts_code'],
                'name': stock.get('name', ''),
                'score': float(stock['composite_score']),
                'pct_above_ma96': stock.get('pct_above_ma96'),
                'sl_percent': stock.get('sl_percent'),
                'tp_percent': stock.get('tp_percent'),
                'ma96': stock.get('ma96'),
                'close': stock.get('close'),
            })

    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)

    logger.info(f"[ts_7AZ_96MA] Saved {len(selected_stocks)} picks to {output_file}")
