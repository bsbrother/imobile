# Strategy Reference

Complete reference for all stock-picking strategies in iMobile.

---

## Strategy Overview

| Strategy | Type | Default | AI Required | Best In | Speed |
|---|---|---|---|---|---|---|
| `ts_7AZ` | Fundamental | ✦ Yes | No | Normal/Moderate | Fast |
| `ts_auto` | Meta | No | No (falls back) | All conditions | Fast |
| `ts_ao_er` | Technical | No | No | Bear/Volatile | Fast |
| `ts_6Factors` | Fundamental | No | No | Value/Defensive | Fast |
| `ts_multi_factors` | Momentum | No | No | Bull/Trending | Fast |
| `ts_ths_dc` | Technical | No | No | Bull/Normal | Medium |
| `ts_hma` | Technical | No | No | Sharp Bear | Fast |
| `ts_longup` | Technical | No | No | Strong Bull | Fast |
| `ts_ai_pick` | AI | No | Yes | Any | Slow |
| `ts_daily` | AI | No | Yes | Any | Slow |

> ⚠ `ts_gb_line` and `ts_combine` exist on disk (`backtest/strategies/`) but are **not registered** in `engine.py`'s dispatch table. They cannot be called via `python backtest/engine.py`. See [Unregistered Strategies](#unregistered-strategies) below.

---

## `ts_7AZ` — CANSLIM 7-Factor Screener (Default)

**Type:** Fundamental quality  
**File:** `backtest/strategies/ts_7AZ.py`

### How It Works

1. **Stock Pool:** Gets stocks from top-performing hot sectors
2. **7-Factor Binary Scoring (C-A-N-S-L-I-M):**

| Factor | Criterion | Score |
|---|---|---|
| C (Current EPS) | Quarterly EPS growth ≥ 25% | 0/1 |
| A (Annual ROE) | Annual ROE ≥ 15% | 0/1 |
| N (New High) | Price within 15% of 52-week high | 0/1 |
| S (Small Cap) | Market cap < 20B | 0/1 |
| L (Leader) | RPS 60-day rank ≥ 70 | 0/1 |
| I (Institutional) | Turnover rate ≥ 3% | 0/1 |
| M (Market) | Price above 200-day MA | 0/1 |

3. **Ranking:** Stocks ranked by composite score (0-7). Top-N selected.

### Best Backtest Result

**70.60%** (2026-01-01 to 2026-06-19)  
Config: `HOLD_DAYS_MULT=0.5`, `SL_WITH_RE_PICK=false`, `SL_BULL=0.025`

### When To Use

- Default strategy for all market regimes
- Best in normal/moderate markets
- Avoids over-reliance on AI/LLM (pure technical + fundamental)

---

## `ts_auto` — Meta-Strategy Auto-Selector

**Type:** Meta (regime → sub-strategy)  
**File:** `backtest/strategies/ts_auto.py`

### How It Works

1. Detects 20-day market regime using MA10 crossover + momentum
2. Delegates to the best sub-strategy for current conditions:

| Regime | Sub-Strategy | Why |
|---|---|---|
| Strong Bull | `ts_longup` | ADX trend-following captures extended rallies |
| Bull/Normal | `ts_ths_dc` | Hot-sector channel breakout |
| Bear/Sharp | `ts_hma` | HMA+SuperTrend detects reversal bottoms |
| Any (AI mode) | `ts_ai_pick` | LLM analysis for uncertain regimes |

3. Falls back to `ts_7AZ` when AI is disabled (`backtest_ai=false`)

### When To Use

- When you want the system to adapt automatically
- As an A/B comparison against `ts_7AZ`
- During regime transition periods

---

## `ts_ths_dc` — Hot-Sector Channel Breakout

**Type:** Technical momentum  
**File:** `backtest/strategies/ts_ths_dc.py`

### How It Works

1. Fetches hot sectors from THS (Tonghuashun) data
2. Within each hot sector, finds stocks breaking above Donchian channel
3. Filters by volume explosion and MA alignment
4. Ranks by breakout strength and sector rank

### When To Use

- Bull and normal markets
- Sector rotation plays
- When hot money is flowing into specific themes

---

## `ts_hma` — Hull MA + SuperTrend Reversal

**Type:** Technical reversal  
**File:** `backtest/strategies/ts_hma.py`

### How It Works

1. Computes Hull Moving Average (HMA) — faster than traditional MA, less lag
2. Overlays SuperTrend indicator for trend direction
3. Buys when HMA crosses above SuperTrend (reversal signal)
4. Sells when either indicator flips bearish

### When To Use

- Sharp bear markets (catching bottom reversals)
- Volatile markets (HMA's low lag handles whipsaws better)
- Counter-trend plays

---

## `ts_longup` — ADX Trend-Following

**Type:** Technical trend  
**File:** `backtest/strategies/ts_longup.py`

### How It Works

1. Computes ADX (Average Directional Index) + slope
2. Confirms strong uptrend: ADX > 25, +DI > -DI
3. Ranks by ADX strength
4. Holds as long as trend remains intact

### When To Use

- Strong bull markets
- Extended rally phases
- When you want to let winners run (fewer exits)

---

## `ts_ao_er` — AO + ER Divergence Detection

**Type:** Technical divergence  
**File:** `backtest/strategies/ts_ao_er.py`

### How It Works

1. Computes Awesome Oscillator (AO) — measures market momentum via 5-period minus 34-period SMA of midpoints
2. Computes Efficiency Ratio (ER) — Kaufman's noise-to-signal ratio over 10 periods
3. **Entry signal:** AO falling for 3+ consecutive bars → momentum weakening, potential counter-trend entry
4. **Exit filter:** ER > 0.7 AND price rising → efficient trend detected, avoid entering (don't fight the trend)
5. Ranks candidates by AO momentum exhaustion + volume confirmation

### When To Use

- Bear/volatile markets (catches bottoms before price confirms)
- Divergence trading strategies
- Counter-trend plays

---

## `ts_6Factors` — V-G-Q-M-L-S Binary Screener

**Type:** Fundamental multi-factor  
**File:** `backtest/strategies/ts_6Factors.py`

### How It Works

1. **Stock Pool:** All A-shares, filtered ST/delisted
2. **6-Factor Binary Scoring:**

| Factor | Criterion | Score |
|---|---|---|
| V (Value) | PE < 50 AND PB < 5 | 0/1 |
| G (Growth) | Revenue YoY ≥ 15% OR Profit YoY ≥ 20% | 0/1 |
| Q (Quality) | ROE ≥ 15% AND Gross Margin ≥ 20% | 0/1 |
| M (Momentum) | 60d return > 0 AND Price > MA60 | 0/1 |
| L (Low Vol) | 20d annualized volatility < 40% | 0/1 |
| S (Size) | Market cap 20B-500B | 0/1 |

3. Uses `daily_basic` for PE/PB/market-cap pre-filter + `PRO.daily` for OHLCV momentum.
4. Final filter: score ≥ 3 (at least half of 6 factors passing).

### Best Backtest Result

**13.30%** (2026-01-01 to 2026-06-19, full period)

### When To Use

- Value/defensive rotation
- Low-volatility market environments
- When fundamentals matter more than price momentum

---

## `ts_multi_factors` — BigQuant Momentum

**Type:** Momentum-forward multi-factor  
**File:** `backtest/strategies/ts_multi_factors.py`

### How It Works

Inspired by BigQuant's volume-acceleration + slope-ranking framework:

1. **Quality Gates:** PE<100, PB<10, mcap 20-500B, price 5-200, turnover>1%
2. **Hard Filters (BigQuant steps):**
   - Volume acceleration: 5d avg vol / prior 5d avg vol > 1.07 (+7%)
   - 3-day return ≥ 0 (exclude losers)
   - 5-day slope top 20% by linear regression
3. **Composite Scoring (rank-based):**
   - Volume acceleration rank: 25%
   - Return sweet-spot quality: 20%  
   - 5-day slope rank: 25%
   - MA60 proximity: 15%
   - Volatility stability: 15%
4. Batch-fetches daily data via `PRO.daily(ts_code='...,...,...')` for speed.

### Best Backtest Result

**0.70%** (2026-06-16 to 2026-06-19, 3-day flat market — preliminary, needs full-period test)

### When To Use

- Bull/trending markets (momentum strategies need trend)
- Short-term swing trading
- Volume-confirmed breakout plays

---

## `ts_ai_pick` — Full AI Analysis

**Type:** AI-driven  
**File:** `backtest/strategies/ts_ai_pick.py`

### How It Works

1. Gets stock candidates from hot sectors
2. For each candidate: fetches news, sentiment, financials via web search
3. LLM analyzes and scores each stock
4. Returns top picks with reasoning

### When To Use

- When you want qualitative + quantitative analysis
- Uncertain markets where fundamentals matter more
- Requires web search to be enabled (`backtest_search=true`)

### Fallback

When `backtest_ai=false`: redirects to `ts_longup` (ADX trend-following)

---

## `ts_daily` — News-Driven Daily Picks

**Type:** AI-driven (daily)  
**File:** `backtest/strategies/ts_daily.py`

### How It Works

1. LLM scans current market news and hot topics
2. Identifies stocks mentioned in positive context
3. Filters by volume, price action, sector
4. Returns 3-5 picks per day

### When To Use

- Event-driven trading
- Policy/sector catalyst days
- Requires web search to be enabled

### Fallback

When `backtest_ai=false`: redirects to `ts_hma` (HMA+SuperTrend)

---

## Unregistered Strategies

These strategy files exist on disk (`backtest/strategies/`) but are **not registered** in `engine.py`'s dispatch table. They cannot be called via `python backtest/engine.py` and are not usable in the backtest pipeline.

### `ts_gb_line` — Golden Cross / Dead Cross

**Type:** Technical crossover  
**File:** `backtest/strategies/ts_gb_line.py`

**How It Works:** Monitors MA crossovers (golden cross = buy, dead cross = sell) with multi-timeframe confirmation and volume filters.

**When To Use:** Trending markets (not sideways). As a supplementary signal for other strategies.

### `ts_combine` — Multi-Strategy Combiner

**Type:** Multi-strategy  
**File:** `backtest/strategies/ts_combine.py`

**How It Works:** Runs multiple strategies in parallel, merges and deduplicates overlapping picks, allocates capital proportionally.

**When To Use:** Diversification across strategy types. Reducing single-strategy bias. Testing strategy correlation.

---

## `--no-search` / `--no-ai` Flags

These CLI flags are passed to all strategy scripts, but **only three strategies honor them:**

| Strategy | `--no-ai` | `--no-search` | Effect |
|---|---|---|---|
| `ts_ai_pick` | ✅ | ✅ | Skips LLM analysis; falls back to technical scoring |
| `ts_daily` | ✅ | ✅ | Skips LLM + news API; uses technical scoring |
| `ts_auto` | ✅ | ❌ (ignored) | Switches to ts_7AZ fallback |
| All others | ❌ Ignored | ❌ Ignored | Already pure technical — zero search/AI calls |

---

## Strategy Selection Guide

```
Market is BULL + trending?     → ts_multi_factors or ts_longup
Market is NORMAL?              → ts_7AZ (default)
Market is BEAR + sharp drop?   → ts_ao_er or ts_hma
Market is VOLATILE?            → ts_6Factors (defensive) or ts_7AZ (conservative)
Sector rotation happening?     → ts_ths_dc
News-driven catalyst?          → ts_daily (needs AI)
Want qualitative depth?        → ts_ai_pick (needs AI)
Just want it to work?          → ts_7AZ
```
