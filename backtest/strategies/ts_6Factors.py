"""
6-Factor stock picking strategy for A-Shares.

Based on the classic 6-factor quantitative framework:
  V - Value      (价值): PE, PB — buy cheap
  G - Growth     (成长): Revenue/Earnings growth — buy future potential
  Q - Quality    (质量): ROE, gross margin — buy good companies
  M - Momentum   (动量): Price trend, MA alignment — buy winners
  L - Low Vol    (波动): Low volatility anomaly — avoid crash-prone stocks
  S - Size       (规模): Mid-small cap — growth elasticity

Each factor scores 0 or 1, max total = 6.
Differs from CANSLIM (ts_7AZ) in replacing "New High" with Value,
adding explicit Volatility factor, and using broader Momentum metrics.

Usage:
  python -m backtest.strategies.ts_6Factors <date YYYYMMDD> [ts_6Factors]

Output:
  /tmp/tmp: {"selected_stocks": [{"rank": 1, "symbol": "000001.SZ", "score": 5.0},...]}
"""

import os
import sys
import json
from datetime import datetime
from loguru import logger
import pandas as pd
import numpy as np
import warnings

import tushare as ts
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before
from backtest.utils.util import convert_trade_date
from backtest.utils.market_regime import detect_market_regime
from backtest.strategies.ts_ths_dc import no_risky_stocks

warnings.filterwarnings("ignore", category=UserWarning, module='py_mini_racer')

load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
if not TUSHARE_TOKEN:
    raise ValueError("Please set the TUSHARE_TOKEN environment variable.")
PRO = ts.pro_api(TUSHARE_TOKEN)

# ── 6-Factor Thresholds ────────────────────────────────────────
# V: Value
V_PE_MAX = 50                    # PE must be < 50 (avoid extreme negatives or inflated)
V_PB_MAX = 5                     # PB must be < 5

# G: Growth
G_REVENUE_YOY_MIN = 0.15         # Revenue YoY growth ≥ 15%
G_PROFIT_YOY_MIN = 0.20          # Net profit YoY growth ≥ 20%

# Q: Quality
Q_ROE_MIN = 0.15                 # ROE ≥ 15%
Q_GROSS_MARGIN_MIN = 0.20        # Gross profit margin ≥ 20%

# M: Momentum (60-day lookback)
M_RETURN_60D_MIN = 0.0           # 60-day return > 0
M_MA60_ABOVE = True              # Price > MA60

# L: Low Volatility
L_VOL20D_MAX = 0.40              # 20-day annualized volatility < 40%

# S: Size (mid-small cap in 亿元)
S_MARKET_CAP_MIN = 20            # Market cap ≥ 20 billion
S_MARKET_CAP_MAX = 500           # Market cap ≤ 500 billion

LOOKBACK_DAYS = 280


def fetch_financial_data(ts_code: str) -> dict:
    """Fetch financial indicators from Tushare: revenue_yoy, profit_yoy, roe, gp_margin."""
    try:
        fina = PRO.fina_indicator(ts_code=ts_code, period_type=0)
        if fina.empty:
            return {}
        latest = fina.sort_values('end_date', ascending=False).iloc[0]
        return {
            'revenue_yoy': latest.get('or_yoy', None),         # 营收同比增长率
            'profit_yoy': latest.get('q_dtprofit_yoy', None),  # 扣非净利润同比增长率
            'roe': latest.get('roe', None),
            'grossprofit_margin': latest.get('grossprofit_margin', None),
        }
    except Exception:
        return {}


def get_stock_pool() -> pd.DataFrame:
    """Get A-share stock pool, filtering out ST, delisted."""
    stock_basic = data_provider.get_basic_information_api()
    risky_free = no_risky_stocks(stock_basic=stock_basic)
    pool = stock_basic[stock_basic['ts_code'].isin(risky_free)].reset_index(drop=True)
    return pool


def compute_volatility(close_series: pd.Series, period: int = 20) -> float:
    """Compute annualized volatility from daily returns."""
    if len(close_series) < period:
        return 999.0
    returns = close_series.pct_change().dropna().tail(period)
    if len(returns) < 5:
        return 999.0
    daily_vol = returns.std()
    annual_vol = daily_vol * np.sqrt(252)
    return float(annual_vol)


def sixfactor_score_stock(ts_code: str, name: str, end_date: str) -> dict | None:
    """
    Score a single stock across all 6 factors.
    Returns dict with factor flags and total score, or None if insufficient data.
    """
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS - 1)
    try:
        df = data_provider.get_ohlcv_data(symbol=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty or len(df) < 60:
            return None
        if 'trade_date' in df.columns:
            df = df.sort_values('trade_date', ascending=False)
    except Exception:
        return None

    close = df['close'].astype(float)
    price = float(close.iloc[0])
    if price <= 0:
        return None

    # ── M: Momentum ──
    ret_60d = (close.iloc[0] - close.iloc[min(59, len(close)-1)]) / close.iloc[min(59, len(close)-1)]
    ma60 = float(close.rolling(60).mean().iloc[0]) if len(close) >= 60 else 0
    m_momentum = (ret_60d > M_RETURN_60D_MIN) and (ma60 > 0 and price > ma60)

    # ── L: Low Volatility ──
    vol20 = compute_volatility(close, 20)
    l_lowvol = vol20 < L_VOL20D_MAX

    # ── V, S, PE/PB: Fetch from daily_basic (daily endpoint lacks these fields) ──
    pe = 999
    pb = 999
    total_mv = 0.0
    try:
        basic = PRO.daily_basic(ts_code=ts_code, trade_date=end_date)
        if basic is not None and not basic.empty:
            row = basic.iloc[0]
            pe = float(row['pe']) if pd.notna(row.get('pe')) else 999
            pb = float(row['pb']) if pd.notna(row.get('pb')) else 999
            total_mv = float(row['total_mv']) if pd.notna(row.get('total_mv')) else 0.0
    except Exception:
        pass

    # ── S: Size ──
    # daily_basic total_mv is in 万元; convert to 亿
    total_mv_yi = total_mv / 10000 if total_mv > 0 else 0
    s_size = S_MARKET_CAP_MIN <= total_mv_yi <= S_MARKET_CAP_MAX

    # ── V: Value ──
    v_value = (pe > 0 and pe < V_PE_MAX) and (pb > 0 and pb < V_PB_MAX)

    # ── Financial (G + Q) ──
    fin = fetch_financial_data(ts_code)
    g_growth = (
        (fin.get('revenue_yoy') is not None and fin['revenue_yoy'] >= G_REVENUE_YOY_MIN) or
        (fin.get('profit_yoy') is not None and fin['profit_yoy'] >= G_PROFIT_YOY_MIN)
    )
    q_quality = (
        (fin.get('roe') is not None and fin['roe'] >= Q_ROE_MIN) and
        (fin.get('grossprofit_margin') is not None and fin['grossprofit_margin'] >= Q_GROSS_MARGIN_MIN)
    )

    score = sum([v_value, g_growth, q_quality, m_momentum, l_lowvol, s_size])

    return {
        'ts_code': ts_code,
        'name': name,
        'price': price,
        'pe': pe if pe != 999 else None,
        'pb': pb if pb != 999 else None,
        'total_mv_yi': total_mv_yi,
        'ret_60d': float(ret_60d),
        'vol_20d': vol20,
        'revenue_yoy': fin.get('revenue_yoy'),
        'profit_yoy': fin.get('profit_yoy'),
        'roe': fin.get('roe'),
        'grossprofit_margin': fin.get('grossprofit_margin'),
        'v_value': v_value,
        'g_growth': g_growth,
        'q_quality': q_quality,
        'm_momentum': m_momentum,
        'l_lowvol': l_lowvol,
        's_size': s_size,
        'score': score,
    }


def sixfactor_screener(end_date: str, top_n: int = 80) -> pd.DataFrame:
    """
    Fast 6-factor screener.
    Phase 1: Pre-filter via daily_basic (S + V/L quick filters).
    Phase 2: Full 6-factor scoring on pre-filtered pool.
    """
    import time as time_mod

    logger.info(f"6-Factor screener for {end_date}")

    pool = get_stock_pool()
    logger.info(f"Stock pool: {len(pool)} stocks")

    # ── Phase 1: Pre-filter via daily_basic ──
    try:
        daily_basic = PRO.daily_basic(trade_date=end_date)
        if daily_basic is not None and not daily_basic.empty:
            pool = pool.merge(
                daily_basic[['ts_code', 'circ_mv', 'total_mv', 'turnover_rate', 'pe', 'pb']],
                on='ts_code', how='left'
            )
            # S: Size pre-filter
            pool = pool[pool['total_mv'].notna()]
            pool['total_mv_yi'] = pool['total_mv'] / 10000
            pool = pool[
                (pool['total_mv_yi'] >= S_MARKET_CAP_MIN) &
                (pool['total_mv_yi'] <= S_MARKET_CAP_MAX)
            ]
            logger.info(f"After S-filter ({S_MARKET_CAP_MIN}~{S_MARKET_CAP_MAX}亿): {len(pool)} stocks")

            # V: Quick PE/PB pre-filter (relaxed, final scoring does strict)
            pool = pool[pool['pe'].notna() & (pool['pe'] > 0) & (pool['pe'] < V_PE_MAX * 2)]
            pool = pool[pool['pb'].notna() & (pool['pb'] > 0) & (pool['pb'] < V_PB_MAX * 2)]
            logger.info(f"After V pre-filter: {len(pool)} stocks")

            # Active stocks only (turnover > 0.5%)
            pool = pool[pool['turnover_rate'].notna() & (pool['turnover_rate'] >= 0.5)]
            logger.info(f"After turnover > 0.5%: {len(pool)} stocks")
    except Exception as e:
        logger.warning(f"daily_basic pre-filter failed: {e}")

    if len(pool) > top_n:
        pool = pool.nlargest(top_n, 'total_mv') if 'total_mv' in pool.columns else pool.head(top_n)
        logger.info(f"Limited to top {top_n} by market cap")

    if pool.empty:
        logger.warning("No stocks after pre-filter")
        return pd.DataFrame()

    # ── Phase 2: Full 6-factor scoring ──
    logger.info(f"Phase 2: Scoring {len(pool)} stocks...")
    results = []
    for i, (_, row) in enumerate(pool.iterrows()):
        ts_code = row['ts_code']
        scored = sixfactor_score_stock(ts_code, row.get('name', ts_code), end_date)
        if scored is None:
            continue
        # Only keep stocks with score >= 2 (at least 2 factors passing)
        if scored['score'] >= 2:
            results.append(scored)

        if (i + 1) % 20 == 0:
            logger.info(f"  Scored {i+1}/{len(pool)}, {len(results)} passing (score ≥ 2)")

    logger.info(f"Passed: {len(results)} stocks")
    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df = df.sort_values('score', ascending=False).reset_index(drop=True)
    df['rank'] = df.index + 1
    logger.info(f"Final: {len(df)} stocks, scores {df['score'].min()}-{df['score'].max()}")
    return df


def pick_strong_stocks(start_date: str, end_date: str, src: str = 'ts_6Factors') -> pd.DataFrame:
    """
    Main entry point — compatible with ts_ths_dc / ts_7AZ interface.
    Runs 6-factor screener and returns scored DataFrame.
    """
    regime_data = detect_market_regime(end_date)
    regime = regime_data.get('regime', 'normal')
    logger.info(f"Market Regime: {regime}")

    df = sixfactor_screener(end_date)

    if df.empty:
        return df

    # Filter for stocks scoring 3+ (at least half of the 6 factors)
    df = df[df['score'] >= 3].reset_index(drop=True)
    df['rank'] = df.index + 1

    # Save to /tmp/tmp in standard format
    selected_stocks = []
    for _, row in df.iterrows():
        selected_stocks.append({
            'rank': int(row['rank']),
            'symbol': row['ts_code'],
            'score': float(f"{row['score']:.1f}")
        })

    output_file = '/tmp/tmp'
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_6Factors_tmp.json'
    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)

    logger.info(f"Saved {len(selected_stocks)} 6-Factor picks to {output_file}")
    return df


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) >= 1:
        date = convert_trade_date(argv[0])
    else:
        date = datetime.now().strftime('%Y%m%d')

    src = argv[1] if len(argv) >= 2 else 'ts_6Factors'

    date = get_trading_days_before(date, 1)
    start_date = get_trading_days_before(date, 5)
    end_date = date

    df = pick_strong_stocks(start_date=start_date, end_date=end_date, src=src)

    if not df.empty:
        logger.info("=== TOP 10 6-Factor Picks ===")
        for _, row in df.head(10).iterrows():
            flags = []
            if row.get('v_value'): flags.append('V')
            if row.get('g_growth'): flags.append('G')
            if row.get('q_quality'): flags.append('Q')
            if row.get('m_momentum'): flags.append('M')
            if row.get('l_lowvol'): flags.append('L')
            if row.get('s_size'): flags.append('S')
            logger.info(
                f"  {row['rank']}. {row['name']}({row['ts_code']}) "
                f"Score={row['score']:.0f} PE={row.get('pe','-')} ROE={row.get('roe','-')} "
                f"Flags={'|'.join(flags)}"
            )
