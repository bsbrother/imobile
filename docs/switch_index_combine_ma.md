## improve (baseline 58.40%, skip_holding 61.74%) to more returns:


1. Is SSE/CSI300 the best index to use for the regime filter?
No, they are actually not the best for this specific strategy.

Why they are bad for you: The SSE Composite (000001.SH) and CSI300 (000300.SH) are heavily weighted by giant state-owned enterprises (banks, oil, telecommunications). These indexes can stay artificially stable or even go up while smaller growth stocks are crashing.
The Better Alternative: The CANSLIM strategy targets high-growth, mid-cap, and tech stocks. Because of this, your regime filter should track the CSI500 (000905.SH) or the ChiNext Index (399006.SZ). These indexes represent the exact type of stocks your strategy buys. If you use CSI500 as your regime index, the system will accurately detect when the growth market is dipping and protect your capital.

2. How to adjust the logic so it drops to BEAR mode faster?
Currently, the strategy drops to BEAR mode in backtest/utils/market_regime.py using this logic:

python
# Bear market: Downtrend
        elif current_price < ma60 < ma120 and trend_60d < 0:
            regime = 'bear'
This is a very lagging (slow) indicator. It requires the 60-day moving average to cross below the 120-day moving average. By the time this happens, the market has usually already been crashing for weeks or months!

To make it drop to BEAR mode (max 5 positions, tight stop-losses) much faster when the index dips, we need to introduce a faster moving average (like the 20-day MA) to detect short-term trend breaks.

Here is what I recommend changing the logic to:

```python
# Calculate faster moving average
        ma20 = close.rolling(20, min_periods=10).mean().iloc[-1]

        # Bull market: Strong uptrend, low volatility, price above MA20
        if current_price > ma20 and current_price > ma60 > ma120 and volatility < 2.0:
            regime = 'bull'
        # Fast Bear Market / Correction: Price breaks below the 20-day MA
        elif current_price < ma20 and (current_price < ma60 or trend_60d < 0):
            regime = 'bear'
```

By adding ma20, the strategy will instantly switch to BEAR mode the moment the index dips below its 20-day average (a standard definition of a short-term correction), severely restricting it from buying 20 positions in a falling market.


## Solution
Add  SWITCH_INDEX_COMBINE_MA=false, default SSI&CSI300 and MA120(heavy, slow) set to true will switch to CS500 and MA20(growth, faster drop):
1 Switch the default index in market_regime.py to CSI500 (000905.SH)?
2 Implement the faster ma20 Bear-mode drop logic?
