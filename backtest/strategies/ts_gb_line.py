"""
Pick stocks based on Guppy Trend Line (GB Line) Breakout Strategy.
Reference: docs/gb_line_trend.md

Criteria:
1. Price Breakout: Close > GMMA Long Term Group (EMA 30, 35, 40, 45, 50, 60).
2. Volume Confirmation: Volume > 1.5 * MA20_Vol.
3. Trend Filter: GMMA Long Group should not be steeply limit down.
"""
import os
import sys
import json
import pandas as pd
import numpy as np
import warnings
from datetime import datetime
from loguru import logger
from typing import List, Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest import data_provider
from backtest.utils.trading_calendar import get_trading_days_before, get_trading_days_between
from backtest.utils.util import convert_trade_date
from backtest.utils.market_regime import detect_market_regime

# Suppress warnings
warnings.filterwarnings("ignore")

# Constants
GMMA_LONG = [30, 35, 40, 45, 50, 60]
LOOKBACK_DAYS = 80 # Need at least 60 for EMA60, plus buffer

def calculate_gmma(close_series: pd.Series) -> pd.DataFrame:
    """Calculate GMMA Long-term group values."""
    gmma = pd.DataFrame(index=close_series.index)
    for span in GMMA_LONG:
        gmma[f'EMA_{span}'] = close_series.ewm(span=span, adjust=False).mean()
    
    gmma['MAX_LONG'] = gmma.max(axis=1)
    gmma['MIN_LONG'] = gmma.min(axis=1)
    return gmma

def pick_gb_line_stocks(target_date: str, vol_multiplier: float = 1.5, lookback_days: int = 80, lookahead: bool = False) -> pd.DataFrame:
    """
    Pick stocks that break out of GB Line (GMMA Long Group) on target_date.
    """
    if not lookahead:
        # Use previous trading day's data
        original_date = target_date
        target_date = get_trading_days_before(target_date, 1)
        logger.info(f"Lookahead=False: Analyzing for {original_date} using data from {target_date}")
        
    logger.info(f"Picking GB Line stocks for {target_date} with Vol Mult {vol_multiplier}, Lookback {lookback_days}...")
    
    # 1. Get filtered stock list (exclude ST/KC/BJ)
    stock_basic = data_provider.get_basic_information_api()
    if stock_basic.empty:
        raise ValueError("Failed to retrieve stock basic information")
    
    # Filter risky stocks
    from backtest.strategies.ts_ths_dc import no_risky_stocks
    safe_stocks = no_risky_stocks(stock_basic)
    logger.info(f"Target pool size: {len(safe_stocks)}")
    
    # 2. Get Daily Data for filtering candidates (Optimization)
    # Filter for stocks with positive gain and decent volume to reduce detailed fetch
    # Thresholds: PctChg > 2% (Breakout usually strong), Turnover > 1%
    logger.info("Batch fetching daily data...")
    try:
        # Fetch daily market data
        df_daily = data_provider.pro.daily(trade_date=target_date, fields='ts_code,close,pct_chg,amount,vol')
        # Fetch basic daily data (turnover, market cap)
        df_basic = data_provider.pro.daily_basic(trade_date=target_date, fields='ts_code,turnover_rate,circ_mv')
        
        if df_daily.empty:
             logger.warning(f"No daily data found for {target_date}")
             return pd.DataFrame()
        
        if df_basic.empty:
             logger.warning(f"No daily_basic data found for {target_date}, skipping turnover/mv filter")
             df = df_daily
        else:
             df = pd.merge(df_daily, df_basic, on='ts_code', how='inner')
             
        # Filter logic (RELAXED for more picks)
        # 1. Safe Stocks
        # 2. Modest Move (PctChg > 0.5%) - catches early breakouts
        # 3. Liquid (Amount > 3000 aka 3M RMB)
        # 4. Active (Turnover > 2.0%)
        # 5. Small/Mid Cap (CircMV < 200000 aka 20B RMB)
        
        mask = (df['pct_chg'] > 0.5) & (df['amount'] > 3000)
        mask = mask & (df['ts_code'].isin(safe_stocks))
        
        if not df_basic.empty:
            mask = mask & (df['turnover_rate'] > 2.0)
            mask = mask & (df['circ_mv'] < 200000)  # Small/Mid cap: < 20B RMB
        
        candidates = df[mask]['ts_code'].tolist()
        
    except Exception as e:
        logger.error(f"Failed to batch fetch daily data: {e}")
        return pd.DataFrame()
    
    logger.info(f"Candidates after pre-filtering: {len(candidates)}")
    
    if not candidates:
        logger.warning("No candidates found after pre-filtering.")
        return pd.DataFrame()

    picked_stocks = []
    
    # 3. Detailed Analysis for Candidates
    # Need history for MA/EMA calculation
    start_date = get_trading_days_before(target_date, lookback_days)
    
    # Batch process? data_provider.get_stock_data handles list but might be slow if list is large.
    # We loop or small batch.
    
    chunk_size = 50
    for i in range(0, len(candidates), chunk_size):
        chunk = candidates[i:i+chunk_size]
        logger.info(f"Processing chunk {i}/{len(candidates)}...")
        
        try:
            # Fetch history
            hist_data = data_provider.get_stock_data(symbols=chunk, start_date=start_date, end_date=target_date)
            if hist_data.empty:
                continue
            
            # Group by code
            for ts_code, group in hist_data.groupby('ts_code'):
                group = group.sort_values('trade_date')
                if len(group) < 60: # Not enough data
                    continue
                
                # Check target date data presence
                if group.iloc[-1]['trade_date'] != target_date:
                    continue
                
                close_series = group['close']
                vol_series = group['vol']
                
                # Calculate Indicators
                gmma = calculate_gmma(close_series)
                vol_ma20 = vol_series.rolling(20).mean()
                
                curr_idx = group.index[-1]
                prev_idx = group.index[-2]
                
                # --- Condition 1: Breakout ---
                # Close > Max(GMMA)
                curr_close = close_series.loc[curr_idx]
                curr_max_long = gmma['MAX_LONG'].loc[curr_idx]
                
                # Check if it's a "Fresh" breakout (Previous close was below or near)
                # Strict: Prev Close <= Prev Max Long OR Low <= Max Long (penetration)
                # Let's use: Close > MaxLong AND (Open < MaxLong OR PrevClose < PrevMaxLong)
                # Or simply: Close > MaxLong * 1.0 (just strict crossing)
                
                if curr_close <= curr_max_long:
                    continue
                
                # Phase 3: Breakout Freshness Check (per Section 3.1.1)
                # Only pick if previous day close was below GMMA max (fresh breakout)
                # This avoids picking stocks that have been above for days
                prev_idx = len(group) - 2  # Previous day index
                if prev_idx >= 0:
                    prev_close_val = close_series.iloc[prev_idx]
                    prev_gmma_max = gmma['MAX_LONG'].iloc[prev_idx]
                    # Fresh breakout: prev day was at or below GMMA max
                    if prev_close_val > prev_gmma_max * 1.03:  # If prev was >3% above, skip
                        continue
                
                # Removed: 3-Day Confirmation Rule (was too strict)
                
                # Condition 2: Volume
                curr_vol = vol_series.loc[curr_idx]
                curr_vol_ma20 = vol_ma20.loc[curr_idx]
                
                if curr_vol <= vol_multiplier * curr_vol_ma20:
                    continue
                
                # Condition 3: GMMA Trend Alignment
                # Favor stocks where short EMAs > long EMAs (uptrend confirmation)
                ema30 = gmma['EMA_30'].loc[curr_idx]
                ema60 = gmma['EMA_60'].loc[curr_idx]
                trend_aligned = ema30 > ema60 * 0.98  # Allow 2% tolerance for reversals
                
                # Enhanced Scoring Formula
                vol_ratio = curr_vol / curr_vol_ma20 if curr_vol_ma20 > 0 else 1.0
                pct_chg = group.iloc[-1]['pct_chg']
                
                # Breakout strength: how far above GMMA max
                breakout_margin = (curr_close - curr_max_long) / curr_max_long * 100
                trend_score = 5.0 if trend_aligned else 0.0
                
                # Combined score: volume + momentum + breakout strength + trend
                score = (vol_ratio * 8) + (pct_chg * 2) + breakout_margin + trend_score
                
                picked_stocks.append({
                    'ts_code': ts_code,
                    'name': group.iloc[-1]['name'] if 'name' in group.columns else ts_code,
                    'close': curr_close,
                    'pct_chg': pct_chg,
                    'vol_ratio': vol_ratio,
                    'gmma_max': curr_max_long,
                    'trend_aligned': trend_aligned,
                    'composite_score': score,
                    'strategy': 'GB_Line_Breakout'
                })
                
        except Exception as e:
            logger.error(f"Error filtering chunk: {e}")
            continue

    if not picked_stocks:
        return pd.DataFrame()
    
    df = pd.DataFrame(picked_stocks)
    
    # Normalize score
    if not df.empty:
        df['rank'] = df['composite_score'].rank(ascending=False).astype(int)
        df = df.sort_values('rank')
        
    return df

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pick GB Line Stocks")
    parser.add_argument("date", nargs="?", help="Target Date YYYYMMDD")
    parser.add_argument("--vol-multiplier", type=float, default=1.5, help="Volume multiplier threshold")
    parser.add_argument("--lookback", type=int, default=80, help="Lookback days for GMMA")
    parser.add_argument("--lookahead", action="store_true", help="Use current date data (Lookahead Bias)")
    
    args = parser.parse_args()
    
    if args.date:
        date_str = convert_trade_date(args.date)
        target_date = args.date
    else:
        date_str = datetime.now().strftime('%Y%m%d')
        target_date = date_str

    # Update global config based on args
    # Note: LOOKBACK_DAYS is used in pick_gb_line_stocks, we need to pass it or set it.
    # To avoid changing function signature too much, we can set global or pass kwargs.
    # Let's Refactor pick_gb_line_stocks to accept kwargs or just use the args if we move logic to main or class.
    # For now, let's just make pick_gb_line_stocks accept these params.
    
    logger.info(f"Target Date: {date_str}, Vol Mult: {args.vol_multiplier}, Lookback: {args.lookback}, Lookahead: {args.lookahead}")
    
    try:
        # We need to update pick_gb_line_stocks signature and call
        df = pick_gb_line_stocks(date_str, vol_multiplier=args.vol_multiplier, lookback_days=args.lookback, lookahead=args.lookahead)
        
        output_file = f'/tmp/pick_stocks_{target_date}.json'
        selected_stocks = []

        if df.empty:
            logger.warning("No stocks found.")
        else:
            # Top 10
            top_stocks = df.head(10)
            for _, stock in top_stocks.iterrows():
                selected_stocks.append({
                    'rank': int(stock['rank']),
                    'symbol': stock['ts_code'],
                    'score': float(f"{stock['composite_score']:.2f}"),
                    'source': 'ts_gb_line'
                })
            
            print(top_stocks[['ts_code', 'name', 'close', 'pct_chg', 'vol_ratio', 'composite_score']].to_string())

        with open(output_file, 'w') as f:
            json.dump({'selected_stocks': selected_stocks}, f)
            
        logger.info(f"Saved {len(selected_stocks)} stocks to {output_file}")
            
    except Exception as e:
        logger.error(f"Error in main: {e}")
        import traceback
        traceback.print_exc()
        exit(1)
