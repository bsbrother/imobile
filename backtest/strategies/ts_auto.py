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
    and select a strategy.

    Default: ts_7AZ (CANSLIM) for ALL regimes — proven 185.58% return over 343 days,
    beating every other strategy in 19 of 21 months.

    Fallback strategies kept for specific edge cases:
      - ts_longup: strong bull with momentum > 4% (proven trend-following)
      - ts_hma: sharp bear with momentum < -8% (Hull MA reversal detection)
      - ts_7AZ: everything else (normal, moderate bull/bear, volatile)
    """
    # 20 trading days before current date (approx 1 month)
    start_date = get_trading_days_before(date_str, 20)
    end_date = get_trading_days_before(date_str, 1) # up to previous trading day
    
    df = data_provider.get_index_data('000001.SH', start_date, end_date)
    if df is None or df.empty or len(df) < 5:
        logger.warning(f"[ts_auto] Not enough data, fallback to ts_7AZ")
        return "ts_7AZ"
    
    df = df.sort_values(by='trade_date')
    close = df['close'].astype(float)
    
    ma10 = close.rolling(10).mean().iloc[-1] if len(close) >= 10 else close.mean()
    current_price = close.iloc[-1]
    
    returns = close.pct_change()
    volatility = returns.std() * 100
    trend_10d = (current_price - ma10) / ma10 * 100
    momentum = (current_price / close.iloc[0] - 1) * 100

    logger.info(f"[ts_auto] Moment: {momentum:.2f}% Vol: {volatility:.2f}% Trend10d: {trend_10d:.2f}%")
    
    # Default: ts_7AZ CANSLIM for everything (proven 185% return, 19/21 winning months)
    strategy = "ts_7AZ"
    
    # Exceptions: proven edge cases where specialized strategies excel
    if momentum > 4.0 and volatility < 1.5 and current_price > ma10:
        # Strong bull with low volatility — ts_longup trend-following
        strategy = "ts_longup"
    elif momentum < -8.0 and volatility > 2.5:
        # Sharp bear with high volatility — ts_hma Hull MA reversal
        strategy = "ts_hma"
    
    logger.info(f"[ts_auto] Selected: {strategy} (momentum={momentum:.1f}%, vol={volatility:.1f}%)")
    return strategy

def main():
    if len(sys.argv) < 2:
        print("Usage: python ts_auto.py YYYYMMDD [--no-search] [--no-ai]")
        sys.exit(1)
    
    date_str = sys.argv[1]
    # Collect optional flags (everything after the date)
    extra_flags = sys.argv[2:] if len(sys.argv) > 2 else []
    flags_str = " ".join(extra_flags)

    # If --no-ai is set, force ts_7AZ (pure CANSLIM, no LLM/search needed)
    if "--no-ai" in extra_flags:
        logger.info("[ts_auto] --no-ai flag detected, forcing ts_7AZ (pure technical)")
        strategy = "ts_7AZ"
    else:
        strategy = determine_strategy(date_str)
    
    logger.info(f"[ts_auto] Delegating pick for {date_str} to {strategy} flags={flags_str}")
    
    # Execute the selected strategy, passing through the flags
    if strategy == "ts_dc":
        os.system(f"{sys.executable} backtest/strategies/ts_ths_dc.py {date_str} ts_dc {flags_str}")
    elif strategy == "ts_longup":
        os.system(f"{sys.executable} backtest/strategies/ts_longup.py {date_str} {flags_str}")
    elif strategy == "ts_hma":
        os.system(f"{sys.executable} backtest/strategies/ts_hma.py {date_str} {flags_str}")
    elif strategy == "ts_ai_pick":
        os.system(f"{sys.executable} backtest/strategies/ts_ai_pick.py {date_str} {flags_str}")
    elif strategy == "ts_daily":
        os.system(f"{sys.executable} backtest/strategies/ts_daily.py {date_str} {flags_str}")
    elif strategy == "ts_go":
        cmd = f'cd utils/go-stock && go build -o pick_stocks cmd/pick_stocks/main.go && ./pick_stocks -date {date_str} -output /tmp/tmp {flags_str}'
        os.system(cmd)
    elif strategy == "ts_7AZ":
        os.system(f"{sys.executable} backtest/strategies/ts_7AZ.py {date_str} ts_7AZ {flags_str}")

if __name__ == "__main__":
    main()