import os
import sys
import pandas as pd
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from backtest.utils.trading_calendar import get_trading_days_before
from backtest import data_provider

def determine_strategy(date_str: str) -> str:
    """
    Detect market regime based on 20 trading days before the current trading date,
    and select a strategy: ts_ai, ts_daily, ts_dc, ts_go, ts_hma, ts_longup.
    """
    # 20 trading days before current date (approx 1 month)
    start_date = get_trading_days_before(date_str, 20)
    end_date = get_trading_days_before(date_str, 1) # up to previous trading day
    
    df = data_provider.get_index_data('000001.SH', start_date, end_date)
    if df is None or df.empty or len(df) < 5:
        logger.warning(f"[ts_month_src] Not enough index data from {start_date} to {end_date}, fallback to ts_daily")
        return "ts_daily"
    
    df = df.sort_values(by='trade_date')
    close = df['close'].astype(float)
    
    ma10 = close.rolling(10).mean().iloc[-1] if len(close) >= 10 else close.mean()
    current_price = close.iloc[-1]
    
    returns = close.pct_change()
    volatility = returns.std() * 100
    trend_10d = (current_price - ma10) / ma10 * 100
    momentum = (current_price / close.iloc[0] - 1) * 100

    logger.info(f"[ts_month_src] Analyzing historical data from {start_date} to {end_date} for date {date_str}")
    logger.info(f"[ts_month_src] Momentum: {momentum:.2f}%, Volatility: {volatility:.2f}%, Trend10d: {trend_10d:.2f}%")
    
    regime = "normal"
    if volatility > 2.2:
        regime = "volatile"
    elif current_price > ma10 and trend_10d > 0.3:
        regime = "bull"
    elif current_price < ma10 and trend_10d < -0.3:
        regime = "bear"
        
    logger.info(f"[ts_month_src] Detected regime: {regime}")
    
    # Map regime to available strategies
    if regime == "bull":
        if momentum > 4.0:
            strategy = "ts_longup"
        else:
            strategy = "ts_dc"
    elif regime == "bear":
        if momentum < -4.0:
            strategy = "ts_hma"
        else:
            strategy = "ts_daily"
    elif regime == "volatile":
        strategy = "ts_ai_pick"
    else:
        strategy = "ts_go"

    logger.info(f"[ts_month_src] Recommended Strategy: {strategy}")
    return strategy

def main():
    if len(sys.argv) < 2:
        print("Usage: python ts_month_src.py YYYYMMDD")
        sys.exit(1)
    
    date_str = sys.argv[1]
    strategy = determine_strategy(date_str)
    
    logger.info(f"[ts_month_src] Delegating pick for {date_str} to {strategy}")
    
    # Execute the selected strategy
    if strategy == "ts_dc":
        os.system(f"{sys.executable} pick_stocks_from_sector/ts_ths_dc.py {date_str} ts_dc")
    elif strategy == "ts_longup":
        os.system(f"{sys.executable} pick_stocks_from_sector/ts_longup.py {date_str}")
    elif strategy == "ts_hma":
        os.system(f"{sys.executable} pick_stocks_from_sector/ts_hma.py {date_str}")
    elif strategy == "ts_ai_pick":
        os.system(f"{sys.executable} pick_stocks_from_sector/ts_ai_pick.py {date_str}")
    elif strategy == "ts_daily":
        os.system(f"{sys.executable} pick_stocks_from_sector/ts_daily.py {date_str}")
    elif strategy == "ts_go":
        cmd = f'cd utils/go-stock && go build -o pick_stocks cmd/pick_stocks/main.go && ./pick_stocks -date {date_str} -output /tmp/tmp'
        os.system(cmd)

if __name__ == "__main__":
    main()