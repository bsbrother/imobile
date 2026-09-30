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

> ⚠ `ts_7AZ_96MA_flow_longterm` exists on disk (`backtest/strategies/`) but is **not registered** in `engine.py`'s dispatch table. It cannot be called via `python backtest/engine.py`. See [Unregistered Strategies](#unregistered-strategies) below.

---

## The `ts_7AZ_96MA_flow` Family (Production Default)

`ts_7AZ_96MA_flow_review` is the production strategy and the default `src` for both
`python backtest/engine.py` (engine.py:3003) and `pick_orders_trading()` (engine.py:2728).

| `src` | File | Role |
|---|---|---|
| `ts_7AZ_96MA_flow` | `ts_7AZ_96MA_flow.py` | v1 picking (base) |
| `ts_7AZ_96MA_flow_v2` | `ts_7AZ_96MA_flow_v2.py` | v2 regime-adaptive LHB + volume boost. **Holds the shipped 157.86% knobs.** |
| `ts_7AZ_96MA_flow_review` | `ts_7AZ_96MA_flow_review.py` | **DEFAULT.** v2 picking + post-market review overlay. Imports `_regime_96ma`, `_in_crash`, `_apply_flow_filter_v2` from v2. |
| `ts_7AZ_96MA_flow_review_longterm` | `ts_7AZ_96MA_flow_review_longterm.py` | review overlay + long-term variant |
| `ts_7AZ_96MA_flow_longterm` | `ts_7AZ_96MA_flow_longterm.py` | On disk but **not registered** — unreachable via the CLI |

**Best measured result: 157.86%** (2026-01-01 → 2026-08-31), with `max_hold_days: 1` for every
regime in `backtest/config.json`.

| hold setting | result on 20260101-20260831 |
|---|---|
| `max_hold_days: 1` (current config) | **157.86%** |
| intended 7/5/4/2 with `HOLD_DAYS_MULT=0.5` | 138.19% |
| the old 139.04% run | 139.04% — an artifact, see below |

The 139.04% figure was **not reproducible from the committed config**. `get_regime_config()`
used to mutate the dict returned by `ConfigManager.get()`, which hands back the live cached
object rather than a copy, so the multiplicative `HOLD_DAYS_MULT` compounded on every call for
the same regime — bull `max_hold` went 7 → 3 → 1 across a run. That run was really holding
about 1 day while the config claimed 7/5/4/2. Fixed on `fix/regime-config-copy`; the effective
1-day hold it had been applying accidentally is now explicit and beats it by 18.82pp.

### Strategies measured under this config

| strategy | range | return |
|---|---|---|
| `ts_7AZ_96MA_flow_review` (default) | 20260101-20260831 | **157.86%** |
| `ts_7AZ_96MA_flow_v2` (picking *without* the review overlay) | 20260101-20260831 | 126.02% |
| `ts_7AZ` (CANSLIM, 6 months) | 20260101-20260619 | 94.84% |

Only the default clears 139%. Two things fall out of this:

- The review overlay is worth **~31.8pp** over the bare v2 picking under this config, which is
  a reversal of its earlier reputation as net-negative — it had been measured under holds it
  did not suit.
- The 1-day cap helped every strategy measured so far (default +19.67pp, `ts_7AZ` +24.25pp), so
  it reads as a **general lever in this codebase** rather than something specific to the overlay.

All three are fresh full runs with no skips and no `--resume`.

### Out-of-sample check: 2025

The 1-day cap was validated on a second, never-before-run range (2025 full year), with
`config@03abc86` (holds 7/5/4/2) as the control. Those two configs differ **only** in the four
`max_hold_days` values, so the holds are the sole variable in the comparison.

| | 1-day cap | 7/5/4/2 |
|---|---|---|
| 2025 total return | **102.66%** | 83.67% |
| final NAV | ¥1,215,987.76 | ¥1,102,008.65 |
| transactions | 2849 | 2582 |
| deepest NAV drawdown | -5.35% | -4.76% |

**+18.99pp** on 2025 against **+19.67pp** on 2026 — the same lever, two independent years, near
identical magnitude. It won 9 of 12 months and its three losing months were small (-2.06pp,
-0.46pp, -1.85pp).

Both legs: 243 trading days each, fresh runs, no skips, no `--resume`, zero errors, and
`config.json` restored afterwards.

It is not free: the drawdown is ~0.6pp deeper (-5.35% vs -4.76%). Faster rotation buys a lot
more return for a little more risk — worth remembering before sizing real capital on it.

### Alternative-data experiments — NEGATIVE, do not re-run

Three signal families were built to see whether auction/intraday data or a demand-side leader
ranking could beat 157.52% on 20260101-20260928. All four runs lost. Every switch defaults
**OFF**, so 157.52% stays reproducible from `main`.

| run | return | vs baseline | maxDD |
|---|---|---|---|
| baseline (all flags off) | 157.52% | — | -1.75% |
| M3 leader re-rank (`REVIEW_M3_LEADER`) | 117.08% | **-40.44pp** | -1.69% |
| M1/M2 auction + late-session (`REVIEW_M1M2_INTRADAY`) | 129.92% | **-27.60pp** | -1.56% |
| M1/M2 + M3 | 129.92% | **-27.60pp** | -1.56% |
| M1/M2 gated to bear/volatile (`REVIEW_M1M2_REGIMES`) | 150.07% | **-7.45pp** | -1.77% |

Key readings:

- **M3 is inert when combined with M1/M2.** The two re-ranks both sort the whole frame and
  M1/M2 is applied second, so M3's ordering is overwritten; with continuous scores, ties are
  measure-zero. 178 of 179 dates produced identical order sets. Enable one or the other.
- **The gate only shrinks the loss.** Gating to bear/volatile fired on 45/179 dates (Jun 5,
  Jul 5, Aug 19, Sep 16) and recovered 73% of M1/M2's damage, but June still surrendered
  65,527 and the total stayed negative. `volatile` never occurs in this range, so it is
  purely a bear gate.
- **Why a strong offline signal still lost.** The auction gap separated returns well *among
  trades the strategy had already chosen* (corr +0.2933; quartile means 0.655 / 0.913 / 2.223
  / 3.589%), but a re-rank also *imports* names sitting lower in the pool, whose returns were
  never observed. The test validated the signal on a set the intervention then changes. The
  late-session feature was weak to begin with (corr +0.0787).
- **The monthly pattern is not regime-aligned.** Jan/Feb/May were bull and lost, Mar was
  normal and gained, and the worst month (June, -137,266) sits *inside* the gate. Hypothesis
  forming and testing on the same data is how that trap gets set — a 2025 out-of-sample run
  would be required before believing any of it.

Data-availability constraints measured on this host, which bound any future attempt: EastMoney
refuses every endpoint (`RemoteDisconnected`, and `stock_zh_a_hist_pre_min_em` goes to
`push2his.eastmoney.com`); Tushare `stk_auction_o` is not permitted and `stk_mins` is capped at
1 call/hour; TDX accepts connections but every bar call errors. Sina's fixed 1970-bar window is
the only reachable history, so granularity sets depth: 1-min reaches 9 sessions, 5-min 42,
15-min 124, 30-min 247 (2025-09-22 onward), 60-min 493. 30-min is the finest that covers a
multi-month backtest.

### Reproducibility caveat: ties are ordered non-deterministically

`pick_96mv_stocks` (`backtest/strategies/ts_96MA.py:548`) sorts with
`df.sort_values('composite_score', ascending=False)` — pandas' default quicksort, which is not
stable, and with no tie-break column. Candidate pools carry many equal scores (9 of 12 at
`0.0`, and often long runs at `5.0`), so the order *within* a tie group falls back to whatever
row order the frame arrived in.

That row order is **not fixed**, and the chain is:

1. The universe comes from `data_provider.get_basic_information_api()` (`ts_96MA.py:471`), which
   reads `shared/data_cache/cache_stock_basic_all.pkl`. Its `ts_code` column is **not sorted** —
   the first six rows are `920202.BJ, 920025.BJ, 920229.BJ, 301716.SZ, 920201.BJ, 301686.SZ`.
2. `ts_96MA.py:514` iterates that frame to build candidates, so tie order inherits it.
3. The cache is refreshed on TTL expiry during any picking run, and the refreshed order differs.
   Observed: the cache was rewritten 2026-09-29 16:48, between the 157.52% baseline (09-28
   19:41) and the alternative-data runs (09-30).
4. The engine buys `selected_stocks[:MAX_POSITIONS]` in file order, so a tie-order flip changes
   which name is bought.

The picker itself is deterministic: three runs of `pick_96mv_stocks('20260108')` in separate
processes — two with random `PYTHONHASHSEED`, one pinned to 0 — returned byte-identical
orderings. The variation is the cached universe order, not process hash randomisation.

Consequence: two runs of the same config on different days produce different picks. Measured on
20260101-20260928, 47 of 134 non-gated dates differed, diverging from the 5th session (20260109
picks `301138.SZ` in one run and `301446.SZ` in the other; same 12 candidates, same scores,
different tie order).

The monetary impact here was small — the 95 non-gated Jan-May dates totalled +395 realized
against the earlier baseline, ~0.07% — so the comparisons above hold. But three habits follow:

- Re-run the baseline **on the same day** for any A/B claiming single-digit-pp precision.
- A deterministic tie-break (sort by `composite_score` desc **then `ts_code` asc**) removes the
  dependency on universe order entirely, at the cost of re-baselining every strategy that calls
  `pick_96mv_stocks` (7 call sites, including `ts_96MA`'s own CLI). Sorting the universe after
  load would only be defensive — the cache order cannot be guaranteed.
- Watch for this masking effect: the M1/M2 runs hid the instability by re-sorting on a
  continuous score, and matched 179/179 across days. A deterministic-looking run is not
  evidence the pipeline is deterministic.

---

## Results / Backup Directory Naming

Generated runs live under `backtest/results/` and `backtest/results_backups/`. Both are gitignored —
they are artifacts, never committed.

A tag in a directory name is **an experiment label, not a strategy.** There is no
`ts_7AZ_96MA_flow_regime_fcst` strategy; `regime_fcst` records the knob-set that produced that run
(per-regime trend-age + fixed forecast dates, commit `29eba71`).

Observed layout:

    results/         <start>_<end>_<src>                      e.g. 20260101_20260831_ts_7AZ_96MA_flow_review
    results_backups/ <start>_<end>_<src>_<tag>_<return>       e.g. 20260101_20260831_ts_7AZ_96MA_flow_review_139.04

Rules:

- `<src>` — the actual `src` value used for the run, so the name always resolves to real code.
- `<tag>` — OPTIONAL, lowercase experiment label (`regime_fcst`, `trendage_frcst`, `lev34`, …). Omit it
  for a plain default-config run.
- `<return>` — total return in percent (`139.04`), matching the `**Total Return**` row of
  `report_period_*.md`. Keep these two in sync — a dirname that disagrees with its own report is a bug.

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

**107.31%** (2026-01-01 to 2026-08-07, `ts_7AZ_96MA_flow`, open-fill) — **historical**.
Superseded by the default strategy's 157.86% (see "Best measured result" above). Kept as the
record of the open-fill validation experiment, which is what selected `BUY_OPEN_PRICE=true`.
Config: `HOLD_DAYS_MULT=1.0`, `BUY_OPEN_PRICE=true`, `SL_WITH_RE_PICK=false`

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

### `ts_7AZ_96MA_flow_longterm` — Flow + Long-Term Variant

**Type:** Regime-switch + LHB flow  
**File:** `backtest/strategies/ts_7AZ_96MA_flow_longterm.py`

**How It Works:** The long-term sibling of the `ts_7AZ_96MA_flow` family. Sibling module
`ts_7AZ_96MA_flow_review_longterm.py` IS registered; this base module is imported by it, which is why
it stays on disk despite having no dispatch-table entry of its own.

**When To Use:** Extended-hold backtests. Reach it through `ts_7AZ_96MA_flow_review_longterm` rather
than directly — it has no CLI entry point.

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
