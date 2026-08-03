# Strategy Reference

Complete reference for all stock-picking strategies in iMobile.

---

## Strategy Overview

| Strategy | Type | Default | AI Required | Best In | Speed |
|---|---|---|---|---|---|---|
| `ts_7AZ` | Fundamental | ✦ Yes | No | Normal/Moderate | Fast |
| `ts_7AZ_96MA` | Regime-switch | No | No | Trend extremes → 96MA, else 7AZ | Slow |
| `ts_ao_er` | Technical | No | No | Bear/Volatile | Fast |
| `ts_ths_dc` | Technical | No | No | Bull/Normal | Medium |
| `ts_hma` | Technical | No | No | Sharp Bear | Fast |
| `ts_longup` | Technical | No | No | Strong Bull | Fast |
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
| Counter-trend plays
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
| `ts_daily` | ✅ | ✅ | Skips LLM + news API; uses technical scoring |
| All others | ❌ Ignored | ❌ Ignored | Already pure technical — zero search/AI calls |

---

## Strategy Selection Guide

```
Market is BULL + trending?     → ts_7AZ or ts_longup
Market is NORMAL?              → ts_7AZ (default)
Market is BEAR + sharp drop?   → ts_ao_er or ts_hma
Market is VOLATILE?            → ts_7AZ (conservative)
Sector rotation happening?     → ts_ths_dc
News-driven catalyst?          → ts_daily (needs AI)
Trend extreme (uptrend/crash)? → ts_7AZ_96MA
Just want it to work?          → ts_7AZ
```
