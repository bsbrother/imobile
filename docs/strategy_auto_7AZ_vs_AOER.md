# Strategy Analysis: ts_ao_er vs ts_7AZ

## Strategy Comparison

| Dimension | ts_7AZ (CANSLIM) | ts_ao_er (AO+ER) |
|-----------|------------------|-------------------|
| **Philosophy** | Fundamental + Technical (William O'Neil) | Pure Technical Momentum |
| **Entry Signal** | 7-letter score (C-A-N-S-L-I-M) ≥ 4 | AO falling 3+ bars + ER < 0.65 |
| **What it measures** | EPS growth, ROE, 52-week high proximity, market cap, RPS, turnover, MA200 | Momentum oscillator reversal + trend efficiency |
| **Data needed** | Financial statements + 280 days OHLCV | 60 days OHLCV only |
| **Pool size** | Top 50 by market cap (pre-filtered) | Random 300 (sampled) |
| **Exit logic** | Delegated to engine (TP/SL/trailing/stagnation) | Same engine (no ER-based exit implemented) |
| **Regime adaptation** | Score-based (no regime weighting) | Regime-weighted scoring multipliers |
| **Speed** | Slow (~0.2s/stock API calls for fundamentals) | Faster (pure OHLCV computation) |

## Key Differences

### 1. Entry Philosophy — Complementary, Not Competing

**ts_7AZ** asks: *"Is this a fundamentally strong company near its high, with growth?"*
- Looks for EPS growth ≥ 25%, ROE ≥ 17%, price near 52-week high, reasonable market cap
- **Strength**: Filters for quality companies — avoids junk stocks
- **Weakness**: Slow to react to short-term momentum shifts; may miss technical setups

**ts_ao_er** asks: *"Is momentum weakening in a choppy environment — is this a dip-buy?"*
- Looks for AO falling 3+ bars (momentum decay) + ER < 0.65 (not yet trending efficiently)
- **Strength**: Catches short-term pullbacks in otherwise healthy stocks
- **Weakness**: No quality filter — could pick fundamentally weak stocks on a dip

### 2. Stock Pool — Different Universes

| | ts_7AZ | ts_ao_er |
|-|--------|----------|
| Pre-filter | Market cap ≤ 500B, turnover 2-15% | No market cap filter (just price > ¥3) |
| Size limit | Top 50 by market cap | Random 300 sample |
| Data depth | 280 days (needs 250 for RPS) | 60 days (needs 34 for AO long period) |

### 3. Exit Logic — Both Use the Same Engine

> [!IMPORTANT]
> Neither strategy controls exit logic. Both feed stock picks into the **same engine** ([engine.py](file:///home/kasm-user/apps/imobile/backtest/engine.py)), which handles TP/SL, trailing stops, stagnation cuts, and max-hold exits. The AO_ER strategy's original ER-based exit (sell when ER > 0.7 + price rising) is **NOT implemented** in the engine.

## Can They Be Combined?

**Yes — they are naturally complementary.** Here's why:

1. **ts_7AZ finds quality stocks** (fundamentals + relative strength)
2. **ts_ao_er finds timing opportunities** (momentum dip + low trend efficiency)
3. A stock appearing in **both** lists would be a high-quality company currently pulling back — an ideal setup

### Proposed Combination: Intersection Boost + Union with Weighting

```
Combined Score = CANSLIM_score × α + AO_ER_score × β + Bonus(if in both)
```

Where:
- Stocks in **both** lists get a bonus → prioritized for capital allocation
- Stocks in **only ts_7AZ** → still valid (fundamentals-driven entry)
- Stocks in **only ts_ao_er** → allowed but lower priority (needs quality verification)

## Risks of Combining

> [!WARNING]
> **Overfitting risk**: The current 88.13% was achieved with specific env settings on a specific period (Jan-Jun 2026). Adding more signals could overfit to this period.

> [!CAUTION]
> **The AO_ER exit signal (ER > 0.7 + rising) is NOT wired into the engine.** If we combine strategies, the AO_ER-specific exit logic would need to be implemented in `check_order_execution()` or the benefit of the ER exit is lost.

## Recommendation

Before building a combined strategy, I recommend a **simpler test first**:

### Option A: Run ts_ao_er standalone backtest (quick validation)
Run the same 20260101-20260619 period with `ts_ao_er` instead of `ts_7AZ` to see its standalone performance. If it's significantly worse, combining likely won't help.


### 7AZ vs AO_ER
$ python backtest/engine.py 20260101 20260619 ts_xx
total returns: 7AZ(88 %) vs AO_ER()


## AO_ER Improvement plan (analysis only):

1. Universe size → biggest impact on total return Current 300/4990 sample means ~94% of stocks never get screened. At 300 stocks/day with 20 picks, the strategy exhausts its candidate pool. Raising to 800+ would surface more small-cap names that have stronger AO‑fall signals (small caps oscillate more, producing deeper AO drops).

2. AO‑fall threshold tuning AO_FALL_BARS=3 is the minimum. From the dry‑run, top picks had 6‑10 falling bars. Raising to 5 would filter out weak signals (only 3‑4 bars fall → often noise), likely improving win rate. Trade‑off: fewer picks per day, possibly missing some winners.

3. ER exit buffer width ER_MAX_FOR_ENTRY=0.65 vs ER_THRESHOLD=0.70 — a 0.05 gap. Widening this to 0.55 would exclude more stocks already in efficient trends (stronger exit signal avoidance). Tightening to 0.70 would let in more candidates but increase risk of entering near exit.

4. Regime weight tuning Current multipliers favor volatile regimes (AO weight 1.3x, ER 0.6x). In bear markets, the ER weight is 1.3x — stocks with low ER get priority. For a bull market backtest (2026 has been bull-dominant), consider testing inverted weights: lower ER weight in bull (trending stocks are fine to enter) and higher ER weight in bear (only enter chop, not trending down).

5. Add position‑size gating Currently no scoring floor — even low‑score stocks (below 150) can enter if the pool is thin. A SCORE_MIN env‑var filter (like ts_7AZ uses via .env) would force minimum quality: e.g., only picks with composite_score ≥ 150 get submitted.

6. Check hold‑days interaction The log shows HOLD_DAYS_MULT=0.5 → max_hold 1→1d. That's extremely short for an AO‑mean‑reversion strategy (the article's AMT strategy averaged 2 trades/year). If the backtest is churning daily, consider testing HOLD_DAYS_MULT=1.0 or higher to let mean‑reversion plays actually revert.


## After improvement AO_ER
$ python backtest/engine.py 20260101 20260619 ts_xx
total returns: 7AZ(88 %) vs AO_ER()


### backup curent branch & rename to ts_7AZ and create new branch: ts_7AZ_ao_er



### Build combined strategy(ts_7AZ_ao_er.py):
Option 3: Use AO_ER as an exit signal only (not entry)
- Keep ts_7AZ for stock picking (the superior entry signal)
- Use ER > 0.7 as an additional exit trigger in the engine (sell when trend becomes "efficient" — the original paper's exit logic)
- This adds the useful part of AO_ER (exit timing) without diluting entry quality

Option 3 is the most promising — it uses the best of both strategies: ts_7AZ picks quality stocks, ER detects when to exit. This avoids diluting the entry pool while potentially improving exit timing.

