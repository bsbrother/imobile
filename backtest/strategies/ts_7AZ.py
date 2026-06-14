"""
CANSLIM 7-letter stock picking strategy for A-Shares.
Based on William O'Neil's CANSLIM methodology, adapted for A-Share market
with Tushare API data. Implements a scoring system across 7 dimensions:
C-A-N-S-L-I-M.

Reference: "威廉·欧奈尔这套七个字母的选股法，我在A股测了一遍（附代码）" by 老余捞鱼

Usage:
  python -m pick_stocks_from_sector.ts_7AZ <date YYYYMMDD> [ts_7AZ]
Output:
  /tmp/tmp: {"selected_stocks": [{"rank": 1, "symbol": "000001.SZ", "score": 6.5},...]}
"""

import os
import sys
import json
import time
from datetime import datetime, timedelta
import logging
from loguru import logger
import pandas as pd
import numpy as np
import warnings
from typing import Any

import tushare as ts
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before, get_trading_days_between
from backtest.utils.util import convert_trade_date
from backtest.utils.market_regime import detect_market_regime
from backtest.strategies.ts_ths_dc import no_risky_stocks

warnings.filterwarnings("ignore", category=UserWarning, module='py_mini_racer')

load_dotenv()
TUSHARE_TOKEN = os.getenv("TUSHARE_TOKEN")
if not TUSHARE_TOKEN:
    raise ValueError("Please set the TUSHARE_TOKEN environment variable.")
PRO = ts.pro_api(TUSHARE_TOKEN)

# ── CANSLIM Parameters ────────────────────────────────────────
C_EPS_GROWTH_THRESHOLD = 0.25    # C: 当季扣非净利润同比增长 ≥ 25%
A_ROE_THRESHOLD = 0.17           # A: ROE ≥ 17%
N_52W_HIGH_RATIO = 0.85          # N: 股价距离52周高点 ≥ 85% (15%以内)
S_MARKET_CAP_MAX = 500e8         # S: 流通市值 ≤ 500亿
L_RPS_THRESHOLD = 80             # L: RPS(250日) ≥ 80
I_TURNOVER_MIN = 0.02            # I: 换手率 2%-15% (proxy for institutional interest)
I_TURNOVER_MAX = 0.15
M_MA200_ABOVE = True            # M: 价格 > 200日均线
LOOKBACK_DAYS = 280              # Days for RPS calculation (250 + buffer)


def compute_rps(stock_data: pd.DataFrame, lookback: int = 250) -> float:
    """Compute Relative Price Strength (RPS) — 250-day return percentile."""
    if len(stock_data) < lookback:
        return 0.0
    close = stock_data['close'].astype(float)
    ret = (close.iloc[-1] - close.iloc[-lookback]) / close.iloc[-lookback]
    return ret * 100


def fetch_financial_data(ts_code: str) -> dict:
    """
    Fetch financial indicators from Tushare.
    Returns dict with eps_growth, roe, or None values on failure.
    """
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


def get_stock_pool() -> pd.DataFrame:
    """Get A-share stock pool, filtering out ST, delisted, and Beijing exchange."""
    stock_basic = data_provider.get_basic_information_api()
    risky_free = no_risky_stocks(stock_basic=stock_basic)
    pool = stock_basic[stock_basic['ts_code'].isin(risky_free)].reset_index(drop=True)
    return pool


def compute_technical_indicators(ts_code: str, end_date: str) -> dict | None:
    """
    Compute technical indicators for one stock:
    - 250-day return (for RPS)
    - 200-day MA
    - 52-week high
    - Current price
    """
    start_date = get_trading_days_before(end_date, LOOKBACK_DAYS - 1)
    try:
        df = data_provider.get_ohlcv_data(symbol=ts_code, start_date=start_date, end_date=end_date)
        if df is None or df.empty or len(df) < 200:
            return None
        if 'trade_date' in df.columns:
            df = df.sort_values('trade_date', ascending=False)
        close = df['close'].astype(float)
        ret_250 = (close.iloc[0] - close.iloc[-1]) / close.iloc[-1] if len(close) >= 250 else 0.0
        ma200 = close.rolling(200).mean().iloc[0] if len(close) >= 200 else 0.0
        high_52w = close.head(250).max()
        return {
            'price': float(close.iloc[0]),
            'return_250': float(ret_250),
            'ma200': float(ma200),
            'high_52w': float(high_52w),
            'turnover_rate': float(df['turnover_rate'].iloc[0]) if 'turnover_rate' in df.columns else 0.0,
            'total_mv': float(df['total_mv'].iloc[0]) if 'total_mv' in df.columns else 0.0,
        }
    except Exception:
        return None


def canslim_score_stock(ts_code: str, name: str, end_date: str) -> dict | None:
    """Score a single stock across all 7 CANSLIM dimensions. Returns dict or None."""
    tech = compute_technical_indicators(ts_code, end_date)
    if tech is None:
        return None

    fin = fetch_financial_data(ts_code)

    # C: EPS growth ≥ 25%
    c = fin.get('eps_growth') is not None and fin['eps_growth'] >= C_EPS_GROWTH_THRESHOLD

    # A: ROE ≥ 17%
    a = fin.get('roe') is not None and fin['roe'] >= A_ROE_THRESHOLD

    # N: Price within 15% of 52-week high
    n = tech['high_52w'] > 0 and (tech['price'] / tech['high_52w']) >= N_52W_HIGH_RATIO

    # S: Market cap ≤ 500 billion
    s = tech['total_mv'] > 0 and tech['total_mv'] <= S_MARKET_CAP_MAX

    # L: RPS ≥ 80
    rps = tech['return_250']  # Will be ranked later
    l = rps is not None  # Placeholder, actual RPS ranking done in batch

    # I: Turnover rate 2% - 15%
    i = I_TURNOVER_MIN <= tech['turnover_rate'] <= I_TURNOVER_MAX

    # M: Price > 200-day MA
    m = tech['ma200'] > 0 and tech['price'] > tech['ma200']

    return {
        'ts_code': ts_code,
        'name': name,
        'price': tech['price'],
        'return_250': tech['return_250'],
        'ma200': tech['ma200'],
        'high_52w': tech['high_52w'],
        'eps_growth': fin.get('eps_growth'),
        'roe': fin.get('roe'),
        'turnover_rate': tech['turnover_rate'],
        'total_mv': tech['total_mv'],
        'c_eps': c,
        'a_roe': a,
        'n_near_high': n,
        's_small_cap': s,
        'l_rps_pass': l,  # placeholder
        'i_turnover_ok': i,
        'm_above_ma': m,
    }


def canslim_screener(end_date: str, top_n: int = 50) -> pd.DataFrame:
    """
    Fast CANSLIM screener — uses stock_basic for pre-filter,
    PRO.daily for technical (small pool), fina_indicator for fundamentals.
    Designed to run under 90s for daily backtest use.
    """
    import time as time_mod
    
    logger.info(f"CANSLIM screener for {end_date}")
    lookback_start = get_trading_days_before(end_date, LOOKBACK_DAYS - 1)

    pool = get_stock_pool()
    logger.info(f"Stock pool: {len(pool)} stocks")

    # ── Phase 1: Quick pre-filter via daily_basic (S + I) ──
    try:
        daily_basic = PRO.daily_basic(trade_date=end_date)
        if daily_basic is not None and not daily_basic.empty:
            pool = pool.merge(daily_basic[['ts_code', 'circ_mv', 'turnover_rate', 'total_mv']], 
                            on='ts_code', how='left')
            pool = pool[pool['total_mv'].notna() & (pool['total_mv'] <= S_MARKET_CAP_MAX * 10000)]
            logger.info(f"After S-filter (≤500亿): {len(pool)} stocks")
            
            pool = pool[pool['turnover_rate'].notna() & 
                        (pool['turnover_rate'] >= I_TURNOVER_MIN * 100) & 
                        (pool['turnover_rate'] <= I_TURNOVER_MAX * 100)]
            logger.info(f"After I-filter (2%-15%): {len(pool)} stocks")
    except Exception as e:
        logger.warning(f"daily_basic failed: {e}")
    
    # Limit pool size for performance
    if len(pool) > top_n:
        pool = pool.nlargest(top_n, 'total_mv') if 'total_mv' in pool.columns else pool.head(top_n)
        logger.info(f"Limited to top {top_n} stocks")
    
    if pool.empty:
        logger.warning("No stocks after pre-filter")
        return pd.DataFrame()

    # ── Phase 2: Technical + Fundamental scoring (combined per stock) ──
    logger.info(f"Phase 2: Scoring {len(pool)} stocks...")
    
    results = []
    for i, (_, row) in enumerate(pool.iterrows()):
        ts_code = row['ts_code']
        
        # Fetch daily data
        try:
            daily = PRO.daily(ts_code=ts_code, start_date=lookback_start, end_date=end_date)
            if daily is None or daily.empty or len(daily) < 200:
                continue
            daily = daily.sort_values('trade_date', ascending=False)
        except:
            continue
        
        close = daily['close'].astype(float)
        price = float(close.iloc[0])
        ret_250 = (close.iloc[0] - close.iloc[-1]) / close.iloc[-1] if len(close) >= 250 else 0
        ma200 = float(close.rolling(200).mean().iloc[0])
        high_52w = float(close.head(250).max())
        turnover = (float(row.get('turnover_rate', 0)) / 100) if 'turnover_rate' in row.index else 0
        total_mv = float(row.get('total_mv', 1e12)) / 10000 if 'total_mv' in row.index else 1e12
        
        n = high_52w > 0 and (price / high_52w) >= N_52W_HIGH_RATIO
        s = total_mv <= S_MARKET_CAP_MAX
        i_ok = I_TURNOVER_MIN <= turnover <= I_TURNOVER_MAX
        m = ma200 > 0 and price > ma200
        
        tech_score = sum([n, s, i_ok, m])
        if tech_score < 3:
            continue
        
        # Fetch fundamentals only for technical qualifiers
        time_mod.sleep(0.2)
        fin = fetch_financial_data(ts_code)
        c = fin.get('eps_growth') is not None and fin['eps_growth'] >= C_EPS_GROWTH_THRESHOLD
        a = fin.get('roe') is not None and fin['roe'] >= A_ROE_THRESHOLD
        
        results.append({
            'ts_code': ts_code, 'name': row.get('name', ts_code),
            'price': price, 'return_250': ret_250,
            'ma200': ma200, 'high_52w': high_52w,
            'turnover_rate': turnover, 'total_mv': total_mv,
            'n_near_high': n, 's_small_cap': s,
            'i_turnover_ok': i_ok, 'm_above_ma': m,
            'tech_score': tech_score,
            'c_eps': c, 'a_roe': a,
            'eps_growth': fin.get('eps_growth'), 'roe': fin.get('roe'),
        })
        
        if (i+1) % 20 == 0:
            logger.info(f"  Scored {i+1}/{len(pool)}, {len(results)} passing")
    
    logger.info(f"Passed: {len(results)} stocks")
    
    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    df['rps'] = df['return_250'].rank(pct=True) * 100
    df['l_rps_pass'] = df['rps'] >= L_RPS_THRESHOLD
    df['score'] = (df['c_eps'].astype(int) + df['a_roe'].astype(int) +
                   df['n_near_high'].astype(int) + df['s_small_cap'].astype(int) +
                   df['l_rps_pass'].astype(int) + df['i_turnover_ok'].astype(int) +
                   df['m_above_ma'].astype(int))
    
    df = df.sort_values('score', ascending=False).reset_index(drop=True)
    df['rank'] = df.index + 1
    logger.info(f"Final: {len(df)} stocks, scores {df['score'].min()}-{df['score'].max()}")
    return df


def pick_strong_stocks(start_date: str, end_date: str, src: str = 'ts_7AZ') -> pd.DataFrame:
    """
    Main entry point — compatible with ts_ths_dc interface.
    Runs CANSLIM screener and returns scored DataFrame.
    """
    regime_data = detect_market_regime(end_date)
    regime = regime_data.get('regime', 'normal')
    logger.info(f"Market Regime: {regime}")

    df = canslim_screener(end_date)

    if df.empty:
        return df

    # Filter for stocks scoring 4+ (most CANSLIM criteria met)
    df = df[df['score'] >= 4].reset_index(drop=True)
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
    # If /tmp/tmp is a directory (pip residue), use alternative
    if os.path.isdir(output_file):
        output_file = '/tmp/ts_7AZ_tmp.json'
    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)

    logger.info(f"Saved {len(selected_stocks)} CANSLIM picks to {output_file}")
    return df


if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) >= 1:
        date = convert_trade_date(argv[0])
    else:
        date = datetime.now().strftime('%Y%m%d')

    src = argv[1] if len(argv) >= 2 else 'ts_7AZ'
    if src != 'ts_7AZ':
        logger.error("Usage: python -m pick_stocks_from_sector.ts_7AZ <date YYYYMMDD> [ts_7AZ]")
        exit(1)

    date = get_trading_days_before(date, 1)
    start_date = get_trading_days_before(date, 5)  # look back 5 days for financial data context
    end_date = date

    df = pick_strong_stocks(start_date=start_date, end_date=end_date, src=src)

    if not df.empty:
        logger.info("=== TOP 10 CANSLIM Picks ===")
        for _, row in df.head(10).iterrows():
            flags = []
            if row.get('c_eps'): flags.append('C')
            if row.get('a_roe'): flags.append('A')
            if row.get('n_near_high'): flags.append('N')
            if row.get('s_small_cap'): flags.append('S')
            if row.get('l_rps_pass'): flags.append('L')
            if row.get('i_turnover_ok'): flags.append('I')
            if row.get('m_above_ma'): flags.append('M')
            logger.info(
                f"  {row['rank']}. {row['name']}({row['ts_code']}) "
                f"Score={row['score']:.0f} RPS={row.get('rps',0):.0f} "
                f"Flags={'|'.join(flags)}"
            )
