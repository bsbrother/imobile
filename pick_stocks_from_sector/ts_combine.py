"""
Pick stocks by combining Tushare THS and DC strategies.
Prioritizes stocks selected by BOTH strategies (Intersection).
"""
import os
import sys
import json
import pandas as pd
from datetime import datetime
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pick_stocks_from_sector.ts_ths_dc import pick_strong_stocks
from backtest.utils.trading_calendar import get_trading_days_before, get_trading_days_between
from backtest.utils.util import convert_trade_date

# Configuration
RECENT_DAYS = 5

def pick_combined_stocks(start_date: str, end_date: str) -> pd.DataFrame:
    """
    Run both THS and DC strategies and combine the results.
    """
    logger.info(f"Running Combined Strategy from {start_date} to {end_date}...")

    # 1. Run THS Strategy
    logger.info(">>> Executing THS Strategy...")
    try:
        df_ths = pick_strong_stocks(start_date, end_date, src='ts_ths')
        if not df_ths.empty:
            df_ths['src_ths'] = True
            # Normalize scores if needed, or just use raw composite_score
    except Exception as e:
        logger.error(f"THS Strategy failed: {e}")
        df_ths = pd.DataFrame()

    # 2. Run DC Strategy
    logger.info(">>> Executing DC Strategy...")
    try:
        df_dc = pick_strong_stocks(start_date, end_date, src='ts_dc')
        if not df_dc.empty:
            df_dc['src_dc'] = True
    except Exception as e:
        logger.error(f"DC Strategy failed: {e}")
        df_dc = pd.DataFrame()

    # 3. Merge Results
    if df_ths.empty and df_dc.empty:
        logger.warning("Both strategies returned no stocks.")
        return pd.DataFrame()

    # Prepare for merge
    # We want to keep all columns, but handle duplicates.
    # Key columns: ts_code, name, composite_score, strategy
    
    # Rename score columns to avoid collision/confusion before merge
    if not df_ths.empty:
        df_ths = df_ths.rename(columns={'composite_score': 'score_ths', 'strategy': 'strategy_ths'})
    if not df_dc.empty:
        df_dc = df_dc.rename(columns={'composite_score': 'score_dc', 'strategy': 'strategy_dc'})

    # Outer Merge on ts_code
    if df_ths.empty:
        merged_df = df_dc
        merged_df['score_ths'] = 0
        merged_df['strategy_ths'] = ''
        merged_df['src_ths'] = False
    elif df_dc.empty:
        merged_df = df_ths
        merged_df['score_dc'] = 0
        merged_df['strategy_dc'] = ''
        merged_df['src_dc'] = False
    else:
        # Merge
        # We need to preserve 'name' from either side
        merged_df = pd.merge(
            df_ths, 
            df_dc[['ts_code', 'score_dc', 'strategy_dc', 'src_dc']], 
            on='ts_code', 
            how='outer'
        )
        
        # Fill NaNs
        merged_df['src_ths'] = merged_df['src_ths'].fillna(False)
        merged_df['src_dc'] = merged_df['src_dc'].fillna(False)
        merged_df['score_ths'] = merged_df['score_ths'].fillna(0)
        merged_df['score_dc'] = merged_df['score_dc'].fillna(0)
        
        # If name is missing (from DC-only rows), we might need to fetch it or it might be missing if we didn't include it in the subset above.
        # Actually, df_dc has 'name'. Let's include it in merge or fill it.
        # Better approach: concat and group? No, merge is safer for intersection logic.
        # Let's fix the name issue for DC-only rows
        if 'name' in df_dc.columns:
            # Create a mapping
            name_map = df_dc.set_index('ts_code')['name'].to_dict()
            merged_df['name'] = merged_df.apply(
                lambda row: row['name'] if pd.notna(row['name']) else name_map.get(row['ts_code'], ''), axis=1
            )

    # 4. Calculate Combined Score
    # Logic:
    # - Normalize scores from both sources to 0-100 scale if they aren't already
    # - Sum the scores: final = score_ths + score_dc
    # - This naturally rewards intersection (sum of two scores) but allows high-scoring single source to compete
    
    def normalize_score(df, col):
        if df.empty or col not in df.columns:
            return
        max_val = df[col].max()
        min_val = df[col].min()
        if max_val > min_val:
            df[col] = (df[col] - min_val) / (max_val - min_val) * 100
        elif max_val > 0:
            df[col] = 100
        else:
            df[col] = 0

    # Create copies to avoid setting on slice warnings if any
    # We already have merged_df. 
    # But we need to normalize based on the ORIGINAL distribution to preserve relative strength?
    # Actually, merged_df has the raw scores.
    # Let's normalize within the merged set? No, that mixes distributions.
    # Best to use the raw scores if they are already on similar scales (0-100).
    # ts_ths_dc.py produces scores roughly 0-100.
    
    # Max Score + Bonus Strategy
    # Logic: Take the higher score of the two sources.    # Create final score: Weighted Average + Bonus
    # Logic: Use weighted average to smooth out noise.
    # Weight THS (0.3) lower than DC (0.7) as DC signals appear more reliable recently.
    merged_df['weighted_score'] = merged_df['score_ths'] * 0.3 + merged_df['score_dc'] * 0.7
    
    # Add bonus for consensus (present in both)
    # Increased bonus to 20 to strongly reward high-conviction picks.
    merged_df['bonus'] = merged_df.apply(lambda row: 20 if row['src_ths'] and row['src_dc'] else 0, axis=1)
    
    merged_df['final_score'] = merged_df['weighted_score'] + merged_df['bonus']
    
    # Combine Strategy Strings
    def combine_strategies(row):
        s1 = str(row.get('strategy_ths', '')) if pd.notna(row.get('strategy_ths')) else ''
        s2 = str(row.get('strategy_dc', '')) if pd.notna(row.get('strategy_dc')) else ''
        parts = [p for p in [s1, s2] if p and p != 'nan']
        return ' + '.join(parts)

    merged_df['combined_strategy'] = merged_df.apply(combine_strategies, axis=1)
    
    # Add Source Label
    def get_source_label(row):
        if row.get('src_ths') and row.get('src_dc'):
            return 'BOTH'
        elif row.get('src_ths'):
            return 'THS'
        else:
            return 'DC'
    merged_df['source'] = merged_df.apply(get_source_label, axis=1)

    # Sort
    merged_df = merged_df.sort_values('final_score', ascending=False).reset_index(drop=True)
    merged_df['rank'] = merged_df.index + 1
    
    return merged_df

if __name__ == "__main__":
    argv = sys.argv[1:]
    if len(argv) >= 1:
        date_str = convert_trade_date(argv[0])
    else:
        date_str = datetime.now().strftime('%Y%m%d')

    # Setup dates
    # Logic from ts_ths_dc.py
    date = get_trading_days_before(date_str, 1)
    start_date = get_trading_days_before(date, RECENT_DAYS-1)
    end_date = date
    
    logger.info(f"Target Date: {date_str} (Analysis Period: {start_date} - {end_date})")

    df = pick_combined_stocks(start_date, end_date)
    
    if df.empty:
        logger.warning("No stocks found.")
        exit(0)

    # Output to /tmp/tmp for backtest system
    output_file = '/tmp/tmp'
    selected_stocks = []
    for _, stock in df.iterrows():
        selected_stocks.append({
            'rank': int(stock['rank']),
            'symbol': stock['ts_code'],
            'score': float(f"{stock['final_score']:.2f}"),
            'source': stock['source'] # Extra info, might be ignored by standard parser but useful
        })
    
    with open(output_file, 'w') as f:
        json.dump({'selected_stocks': selected_stocks}, f)
    logger.info(f"Saved {len(selected_stocks)} picked stocks to {output_file}")

    # Console Output
    print("\nTOP 10 Combined Strong Stocks --------------------------")
    print(f"{'Rank':<5} {'Code':<10} {'Name':<10} {'Source':<6} {'Score':<6} {'Strategy'}")
    print("-" * 80)
    for i, (_, stock) in enumerate(df.head(20).iterrows(), 1):
        print(f"{stock['rank']:<5} {stock['ts_code']:<10} {stock['name']:<10} {stock['source']:<6} {stock['final_score']:.2f}   {stock['combined_strategy'][:40]}...")
