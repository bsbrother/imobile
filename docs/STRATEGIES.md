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

> **VINTAGE WARNING (2026-10-14).** These figures predate the LHB-cache dedupe and the
> fill-realism fixes, so they are **not** comparable to current numbers. The institutional cache
> carried duplicate `(代码, 上榜日期)` rows while the flow filter SUMS 机构买入净额, so the signal
> was double-counted and the returns inflated. Re-run on the deduped cache
> (20260101-20260930, `SL_ENABLED=false` + `REVIEW_COMPOUND_SIZING=true`): **+68.42%**
> (`results_backups/20260101_20260930_nostop_dedupcache/`); this project's own execution-realism
> work puts the honest range at **~24-61%** once slippage and retail commission are charged
> (see `docs/AUCTION_IMPROVE.md`, which already marks the `..._193.54` run "an impossible
> baseline, not a target"). Read the table below as a RELATIVE hold-setting comparison only.

| hold setting | result on 20260101-20260831 (pre-dedupe) |
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

### Stop-loss A/B across three periods (2024 / 2025 / 2026)

The fixed regime stop (`SL_BULL=SL_NORMAL=0.025`, `SL_VOLATILE=0.02`, `SL_BEAR=0.015`) was isolated
by toggling **only** `SL_ENABLED` in the `ts_7AZ_96MA_flow_review` `.env` section. All six runs share
one rebuilt LHB cache (2023-12-01..2026-09-30, 25,648 rows, 0 duplicates — the same inputs for each
window), one `.env` (`REVIEW_COMPOUND_SIZING=true`), and `--no-search --no-ai`; `check_lhb_coverage`
gates each window. Every other knob — fill model, sizing, RPS — is identical between the two arms.

| period | stop ON (2.5%) | stop OFF (`SL_ENABLED=false`) | benchmark (SSE) | Δ (ON − OFF) |
|---|---|---|---|---|
| 2024-01..12 | **-1.30%** | -15.86% | +12.67% | **+14.56pp** |
| 2025-01..12 | -25.19% | **-14.54%** | +18.41% | -10.65pp |
| 2026-01..09 | +38.56% | **+65.70%** | -2.03% | -27.14pp |

Readings:

- **No-stop wins 2 of 3 periods**, by wide margins, and the aggregate favours it (sum ≈ +35.3pp vs
  +12.1pp). It remains the right default — which is what the review strategy's `.env` section ships
  (`SL_ENABLED=false`).
- **2024 flips the sign.** The stop *helps* there by +14.56pp, and the win is broad, not one month
  (Aug +5.0pp, Sep +13.5pp, Mar +4.2pp). So the stop is a **period-dependent hedge**, not a uniformly
  negative lever: it pays off when intraday dips don't recover and costs when they do.
- **Caveat on "stop OFF".** `SL_ENABLED=false` zeroes only the *fixed regime* stop
  (`market_regime.py` sets it to 99%). The day-adaptive / trailing exits (`HOLD_SL_ADAPT`,
  `SL_BREAKEVEN_DAY`, `SL_TRAIL_PCT`) still fire, so the OFF arms still book ~100-150 `stop_loss`
  exits. This A/B isolates the fixed regime stop, not all stop logic.

An earlier two-period reading ("no-stop beats the stop by +27.14pp in 2026 and +10.65pp in 2025 — same
sign both periods, so the lever generalises") is superseded by the 2024 block: the sign is not stable
across periods. Quote the three-period table, not the two-period one.

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

The picker itself is deterministic: four runs of `pick_96mv_stocks('20260108')` in separate
processes — two with random `PYTHONHASHSEED`, two pinned to 0 — returned byte-identical
orderings (n=12, same order, scores `85, 83, 82, 79, 78, 78, 78, 76, 75, 74, 73, 73`). The
variation is the cached universe order, not process hash randomisation.

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

### Position sizing — POSITIVE, +36.02pp (opt-in, default OFF)

`REVIEW_COMPOUND_SIZING=true` makes position size scale with the account instead of staying
anchored to the starting capital.

**The ceiling.** `cli.py` sizes each position as `per_slot_cash = initial_cash / max_positions`,
and there are exactly `max_positions` slots — so `per_slot_cash x max_positions == initial_cash`,
always. The engine passes `--current-cash` but never `--initial-cash`, so the base stays at the
config's 600,000 for the whole run. Total invested capital is therefore capped at 600,000 no
matter how large the account grows, and deployment decays mechanically:

| month | realized P&L | deployed | equity |
|---|---|---|---|
| 202601 | 43,340 | 43.5% | 625,606 |
| 202604 | 170,885 | 39.5% | 893,417 |
| 202606 | 335,951 | 34.4% | 1,302,395 |
| 202608 | 61,796 | 15.0% | 1,527,676 |
| 202609 | 17,574 | 11.3% | 1,547,058 |

**The edge does not fade**, which is what makes this capital inefficiency rather than a decaying
signal: return on *deployed* capital was positive in all nine months (15.9 / 28.9 / 20.6 / 48.5 /
41.8 / 75.0 / 5.8 / 26.9 / 10.0%).

**Result** (20260101-20260928, same code otherwise):

| metric | baseline | compounding | delta |
|---|---|---|---|
| total return | 157.52% | **193.54%** | **+36.02pp** |
| final equity | 1,545,148 | 1,761,247 | +216,099 |
| realized P&L | 951,638 | 1,173,918 | +222,280 |
| max drawdown | -1.75% | -1.80% | -0.06pp |
| mean deployed | 29.7% | 33.8% | +4.1pp |

The gain lands exactly where the multiplier bites — 202606 +68,651, 202607 +37,683 (deployed
15.4%→25.0%), 202608 +67,652 (15.0%→28.4%), 202609 +34,251 (11.3%→21.3%) — and the extra return
cost almost nothing in drawdown. Jan/Feb are untouched by the flag; their -2,658/-964 is the
tie-order noise described above, not an effect.

**Before enabling this for live trading:** `cli.py` is the same code path used for real orders,
so the flag changes real position sizes too. It is also not a free lunch in production — the
backtest assumes unlimited liquidity, and larger size will not fill as cleanly in small caps.

Two pre-existing issues surfaced while checking this, neither caused by the flag:

- The per-day `report_orders_*.md` files overstate realized P&L by a consistent **~3.2%**
  (baseline +30,197, compound +37,247, gated +29,732). The period report's figure is the one
  that reconciles with its own daily table.
- 18-21 of ~1050 orders exceed the 25% per-position cap in *every* run, because the cap is
  applied to the per-slot budget rather than to the resulting position.

---

### Execution realism — the 193.58% is not achievable as booked

The headline return assumes stop orders always fill *at the stop price*. Measured from the run's
own 181 `report_orders_*.md` files (767 closed round-trips, matching the period report's 767 sells).

**The fill model** (`BUY_OPEN_PRICE=true` / `SELL_OPEN_PRICE=true`, both .env defaults):

| leg | assumed fill | code |
|---|---|---|
| BUY | the day's **open**, unconditionally | `engine.py:1830-1833` |
| SELL on take-profit | exactly the **TP price** | `engine.py:1427` |
| SELL on stop-loss | exactly the **SL price** | `engine.py:1427` |
| SELL when the order expires | the **open** | `engine.py:1410` |
| SELL on ER/trend exit or max-hold | the **close** | `engine.py:1523,1573,1617,1664` |

Sells are therefore **not** booked at the day's high. Checked against each day's own OHLC, the
close-based exits match that day's Close 100% of the time and only 9% coincide with its High.
The real optimism is narrower and sharper: a stop always fills at the stop price.

**Where it breaks.** The per-order TP is `+200%`, so it never traded — **0 of 767** exits hit it.
The stop is tight (median **-1.4%** vs entry: 166 are breakeven stops, 263 are -1.5%), and the
exit mix is 575 stops / 114 expired / 55 max-hold / 23 trend:

    STRICT_MAX_HOLD_CLOSE   +617,236  (55)      STOP_LOSS        -113,186  (575)
    ORDER_EXPIRED_BEFORE... +473,219  (114)     ER_TREND_EXIT    +180,001  (23)

- **143 of 575 stops (25%)** are booked at a price the day **never traded** — the booked stop sits
  *above the day's high* (300394.SZ 20260317: booked ¥320.21, high ¥289.35, +10.7%).
- **346 of 575 (60%)** opened below their stop, so a real stop order triggers at the **open**, not
  at the stop. With a median stop of -1.4%, opening 1.4% down is routine.

**Cost of being honest** — same picks, same sizes, only the fill prices corrected:

| scenario | realized P&L | return |
|---|---|---|
| as booked (`SELL_OPEN_PRICE=true`) | 1,157,271 | 192.88% |
| gap-aware stops (`SELL_OPEN_PRICE=false`) | 366,227 | **61.04%** |
| + 0.2% sell / 0.1% buy slippage | 170,049 | **28.34%** |
| + retail commission (0.025% vs the config's 0.00341%) | 145,647 | **24.27%** |
| + 0.5% sell / 0.2% buy slippage (pessimistic) | -116,018 | -19.34% |

Slippage dominates because turnover is **219x the account** — ¥131M traded on ¥600k, since
`max_hold_days: 1` turns nearly the whole book over every day. This, not the signal, is what the
upper scenarios are actually betting on.

**For auto-trading by TP/SL.** A broker-side TP/SL-only bot cannot reproduce this strategy, and
not because of fills: the TP (+200%) never triggers, and the whole +1,270,456 of profit comes from
the **192 scheduled exits** — selling at the close or the next open. A pure TP/SL bot would hold
those winners until they too hit -1.4% and would forfeit the edge. Automation must place the
*scheduled* exit as well, not just the brackets.

To measure the honest number, re-run with `SELL_OPEN_PRICE=false` plus `SELL_SLIPPAGE_PCT` and
`BUY_SLIPPAGE_PCT` (both default 0). Cross-reference:
`docs/backtest_optimization_real_trading_alignment.md`.

Every figure above is reproducible from a run's own reports, without re-running the backtest:

    python backtest/analysis/execution_realism.py backtest/results/20260101_20260930_ts_7AZ_96MA_flow_review

**What even a gap-aware run still cannot know.** Re-running with `SELL_OPEN_PRICE=false` swaps the
offline re-pricing above for the engine's own arithmetic and lets the size feedback play out (with
`REVIEW_COMPOUND_SIZING=true`, lower profits mean smaller positions), but it is a differently-specified
backtest, *not* ground truth. The engine only ever sees daily OHLC, so it cannot order intraday
events: a bar touching both the TP and the SL is still resolved by rule, and a stop that triggered
mid-session is still assumed to fill at the stop price. Slippage stays a parameter you assert
(`SELL_SLIPPAGE_PCT`), not a measurement; partial fills, queue position and the impact of buying 10+
names at the open in small caps are all outside the model; the data source is the same, so any data
error persists. Read the scenarios as "if slippage were X, the return would be Y".

30-min bars *are* cached for this window (`shared/data_cache/m1m2_min30`, 2025-09-22 → 2026-09-29,
8 bars/day over 180 days), which is enough to place each stop breach in the morning vs the afternoon
and build a better fill model than "the open or the stop". Minute-level bars are not available. Real
broker fills remain the only true ground truth.

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
- `<tag>` — OPTIONAL, lowercase experiment label (`regime_fcst`, `trendage_frcst`, `lev34`, `compound`,
  `m1m2only`, `regimegated`, …). Omit it for a plain default-config run.
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

> **This guide is contradicted by measurement.** A full 2024/2025 sweep of every runnable
> strategy (below) found the opposite: `ts_ao_er` and `ts_hma`, filed here under "BEAR + sharp
> drop", were the only picks that made money in the *bull* years 2024/2025, and the default lost
> both. The guide records intent, not measured behaviour.

---

## Strategy Selection by Tape — the 2024–2026 sweep

Every runnable strategy was re-run on 2024 and 2025 (plus the strongest reversal picks on 2026) on
one engine config — stop-loss off (`SL_ENABLED=false`), compounding on, `--no-search --no-ai` — so
the only variable is the stock-picker. Results sit in `backtest/results_backups/<range>_<src>_sweep/`.

| strategy | family | 2024 | 2025 | 2026* |
|---|---|---|---|---|
| `ts_hma` | reversal | **+10.68%** | **+2.16%** | -7.79% |
| `ts_multi_swing_defensive` | defensive | **+10.06%** | -3.55% | — |
| `ts_ao_er` | divergence | -5.51% | **+5.16%** | +3.20% |
| `ts_longup` | trend | -3.31% | -6.20% | — |
| `ts_7AZ_96MA` | regime-switch | -6.95% | -22.19% | — |
| `ts_7AZ` | fundamental | -12.38% | -16.33% | — |
| `ts_7AZ_96MA_flow_v2` | regime+flow | -22.41% | -7.78% | — |
| **default** `ts_7AZ_96MA_flow_review` | regime+flow | -15.86% | -14.56% | **+65.70%** |
| *SSE benchmark* | | +12.67% | +18.41% | -2.03% |

\* 2026 is Jan–Sep YTD. No strategy beat the index in any year.

**The tape, not the strategy, separates the winners.** Every positive 2024/2025 cell belongs to
the reversal/divergence/defensive family (`ts_hma`, `ts_ao_er`, `ts_multi_swing_defensive`); the
momentum/flow family owns every worst cell in those years — and then wins 2026 outright
(+65.70% vs -7.79% / +3.20%).

**The rule, ex post.** Reversal picks when the year's index rises; the momentum/flow default when
it falls. With hindsight that is 2024 → `ts_hma` (+10.68%), 2025 → `ts_ao_er` (+5.16%),
2026 → default (+65.70%) ⇒ **≈ +93% compounded**, vs ≈ **+19%** for the static default.

**The rule does NOT survive its causal test.** Read at each period start from the trailing signal
only (SSE vs its 200-day MA and trailing 6-month return — see `market_indices` / the index cache),
a selector would have seen:

| period | trailing signal | gate picks | realized | right? |
|---|---|---|---|---|
| 2024 | BELOW MA, 6m -7.3% | default (momentum) | -15.86% | ✗ (needed `ts_hma` +10.68%) |
| 2025 | ABOVE MA, 6m +10.1% | reversal | +2.16% / +5.16% | ✓ |
| 2026 | ABOVE MA, 6m +16.4% | reversal | -7.79% / +3.20% | ✗ (default made +65.70%) |

The trailing signal called 2024 "down" (it ended +12.67%) and 2026 "up" (it ended -2.03%): the
year's character flipped at two of three boundaries. Compounded, the causal gate lands at
**≈ -18%** — worse than doing nothing (the static default, ≈ +19%) and nowhere near the ex-post
+93%.

**Conclusion.** Selecting the strategy per period does separate the returns, but only in hindsight.
A period-start trend gate fails here because it cannot tell whether the coming year turns. Do not
wire strategy-switching into dispatch until a causal rule is validated on more boundaries; treat
the tape mapping as a hypothesis, not a rule.

### A faster signal: re-picking monthly

*(3-boundary figures. Superseded by the 2023 result below: with 2023 included the MA6 gate drops
to -35.3% and even the "monthly beats annual" edge shrinks to ~8pp.)*

The annual gate is a *timing* failure, so the natural fix is to let the selector re-pick every
month. `trading_test/gate_sim.py` does this offline — it rebuilds each run's own daily equity
curve from its `report_period_*.md` (verified: every total reproduces the report exactly), turns
it into monthly returns, and lets a selector choose a strategy per month from trailing data only.

| selector (causal) | total | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| always default (no switching) | **+19.1%** | -15.9% | -14.6% | +65.7% |
| annual gate (period-start trend) | -18.4% | -15.9% | +5.2% | -7.8% |
| monthly gate: SSE vs 6-month MA | +9.9% | +1.6% | -4.8% | +13.6% |
| monthly gate: engine's own regime | -15.9% | +3.9% | -7.8% | -12.2% |
| *ex-post monthly oracle* | *+573.6%* | | | |

- **Monthly beats annual decisively** (-18.4% → +9.9%): the annual failure was mostly timing, not
  a wrong idea — re-evaluating more often genuinely helps.
- **But no causal selector beats doing nothing** (+19.1%). Both pre-registered gates lose. The two
  variants that do beat it (+53.1% 3-month-momentum, +47.7% inverted-regime) are the best of nine
  tried on 33 months, and their mirror images are the worst of the nine — selection bias, not
  signal. The annual winner (up-tape → reversal) and the monthly winner (bear → reversal) even
  contradict each other.
- **The ceiling is enormous**: the ex-post monthly oracle is +573.6% vs +19.1%. Which strategy runs
  matters hugely; none of the tested signals can see it.

Method caveat: the gate composes real per-month returns from each year's own run — no cross-strategy
capital continuity and no switch costs. A true switching portfolio needs a month-by-month runner
that re-invokes the engine; treat these as an upper-ish bound on a causal selector, not ground truth.

### The fourth boundary (2023) — the rule is falsified

2023 is the test the three-year rule lacked: a down year (SSE -3.70%, CSI300 -11.38%) independent
of 2026. It needed the LHB institutional cache extended back to 2023-01 (done — now 2023-01-03 ..
2026-09-30), then the same pickers:

| strategy | 2023 | 2024 | 2025 | 2026 |
|---|---|---|---|---|
| `ts_ao_er` | **+28.43%** | -5.51% | +5.16% | +3.20% |
| `ts_multi_swing_defensive` | -1.05% | +10.06% | -3.55% | — |
| `ts_hma` | -2.98% | +10.68% | +2.16% | -7.79% |
| **default** `ts_7AZ_96MA_flow_review` | **-30.61%** | -15.86% | -14.56% | +65.70% |
| *SSE* | -3.70% | +12.67% | +18.41% | -2.03% |

Four-year compounded:

| rule | total |
|---|---|
| ex-post annual oracle (pick the year's best) | **+147.7%** |
| always `ts_ao_er` (no switching) | +31.7% |
| always `ts_hma` | +1.2% |
| **always default — the production config** | **-17.3%** |
| causal gate, 4 boundaries | -43.4% |
| monthly MA6 gate, 4 boundaries | -35.3% |

- **The rule is falsified.** 2023 and 2026 were *both* down years with **opposite** winners: 2023's
  best was `ts_ao_er` (+28.43%) and its worst was the default (-30.61%); 2026's best was the default
  (+65.70%). Tape direction does not pick the family.
- **The causal gate is worse than doing nothing** (-43.4% vs -17.3%), and worse with 2023 than
  without it. Every gate variant that *beats* the default has its mirror among the worst of the set
  — noise, not signal.
- **The production default lost money over 2023-2026** (-17.3%) — while the single, non-switched
  picker `ts_ao_er` made **+31.7%** (positive in 3 of 4 years, never catastrophic).
- The ex-post ceiling (+147.7% annual; +1016.8% monthly) still shows the choice matters enormously.

**Corrected 2026-10-14:** no *annual* condition selects the strategy — that part stands — but a
per-REGIME *daily* switch does. See the next section. The other open question stands: **why the
production default is the worst runnable picker over this window**, and whether `ts_ao_er`'s
four-year edge is real.

### The regime switch that DOES work — 3+ bear days → `ts_ao_er`, bull → the default (2026-10-14)

`trading_test/regime_switch_study.py`. State lagged one trading day (no look-ahead); the regime is
the engine's own `market_pattern` from `pick_stocks_*.json`; the target is each strategy's forward
21-trading-day return off its own `report_period` daily equity.

Forward-21d mean return by regime:

| regime | best | worst |
|---|---|---|
| bear | `ts_ao_er` **+3.05%** (n=195) | default **-3.65%** |
| bull | **default +2.19%** (n=352) | `ts_multi_swing_defensive` -1.10% |
| normal | `ts_longup` +1.58% (n=276) | `ts_7AZ_96MA` -1.50% |

By run-length of consecutive bear days — the user's "how many BEAR signals" question: at **0 bear
days the default is best (+1.45%)**; at **3+ consecutive bear days `ts_ao_er` is best (+3.32%,
runner-up `ts_multi_swing_defensive` +2.89%) while the default is worst (-3.85%)**. Narrowing to
`bear_run` ≥ 3 **and** a sharp index drop (5-day SSE ≤ -4%): `ts_ao_er` **+6.50%** vs default
-0.64% (n=52).

Rule backtest (daily compounding of the picked strategy's daily returns):

| rule | 2023-2026 |
|---|---|
| static default (production) | **-15.98%** |
| static `ts_ao_er` | +31.69% |
| A: `bear_run` ≥ 3 → `ts_ao_er`, else default | **+78.23%** |
| B: bear → `ts_ao_er`, bull → default | +76.69% |

Walk-forward (fit the per-regime pick on 2023-24, apply to 2025-26): the fitted map
`{bear: ts_ao_er, bull: default, normal: ts_longup}` → **+65.18%** vs the static default's
**+43.90%** — a **+21pp out-of-sample edge**.

Mechanism: `ts_ao_er` is a divergence/mean-reversion picker (it wins the oversold bear tape); the
production default chases LHB institutional flow, which pays in trending bull tape. That is why the
*annual* rule above failed: a year's tape mixes both regimes, so an annual pick averages the edge
away — the switch has to be made per day, not per year. It also means the earlier reading ("every
gate that beats the default has its mirror among the worst — noise") was too hasty: for a real
signal, the inverted rule being the worst is expected, and the independent per-day conditional means
here confirm the direction.

Caveats: overlapping 21-day windows inflate the observation count (effective n is the ~900 days,
not 4904); the daily switch is a proxy with no carry/turnover cost; 4 years, and the walk-forward's
hold-out is 2 of them. **Trust the out-of-sample +21pp, not the in-sample +94pp.** Not a live change
without explicit sign-off.

**Implemented 2026-10-14 as `ts_switch_by_regime`** (`backtest/strategies/ts_switch_by_regime.py`,
registered in `backtest/engine.py`). It is a delegating wrapper: it detects the same regime with the
state LAGGED one trading day (`SWITCH_LAG_DAYS`, matching LIVE — the 08:30 pre-market pick cannot
see today's close), then runs the chosen strategy as a subprocess with its own `.env` section forced
in (`apply_strategy_env(..., environ=child_env)`), so a delegate's picks are identical to a native
run of it. Verified: target 20230718 (state 20230717, `bear_run=3`) → `ts_ao_er`, and its picks
equal the archived native `ts_ao_er` run's picks for that date (same order; the archived file is the
raw output minus the engine's `SCORE_MIN` filter). Knobs: `SWITCH_BEAR_STRATEGY`,
`SWITCH_BULL_STRATEGY`, `SWITCH_NORMAL_STRATEGY`, `SWITCH_BEAR_RUN_MIN`, `SWITCH_BEAR_MODE`
(`bear_run` | `regime`), `SWITCH_LAG_DAYS`. Decision trail: `/tmp/switch_decisions.csv`.

It needs its own `.env` section (`# ── ts_switch_by_regime ──`, mirrors the production knobs
`SL_ENABLED=false` + `REVIEW_COMPOUND_SIZING=true`) — the section loader NEUTRALIZES keys owned only
by another strategy's section, so without it a switch run would silently drop compounding and stop
being comparable with the baselines.

**It is NOT the default** — `DEFAULT_STRATEGY` is untouched, so the live path is unchanged; it runs
only when passed as the src.

### The real run vs the proxy — the proxy is optimistic (validated 2026-10-15)

> **CORRECTION (2026-10-15): this run is INVALID as a test of the rule.** `backtest/engine.py:3041`
> applies the review overlay behind an allowlist on the **src name**, so a `ts_switch_by_regime` run
> silently ignored every review adjustment — and the review is a large lever (on the native 2026
> default run it RAISED `MAX_POSITIONS` 105 times and lowered it 58). The decomposition proves it:
> of the 36.93pp 2026 gap, **+35.15pp came from days the switch was running the *same* strategy as
> the default**, while on the 31 days it delegated to `ts_ao_er` the two earned ¥145,929 vs ¥143,956
> — a ¥2k difference. The bear stretches were never the cause; the missing review was. The allowlist
> now includes `ts_switch_by_regime`, and a 2026-only re-run is in flight. **Treat the verdict below
> as withdrawn until that lands.**
>
> **CONFIRMED 2026-10-15.** With the allowlist fixed, a 2026-only re-run — same rule, *identical*
> 31 `ts_ao_er` decisions, 181/181 sessions, and 104 review position-raises against the native
> run's 105 — reads **+66.52% vs the default's +68.42%: a -1.90pp gap, down from -50.30pp**. So the
> withheld review was the entire story, and the bear delegation is roughly break-even in 2026
> ~¥11k on ¥600k). The old 4-year dir is archived as
> `_results_backups_archive/20230101_20260930_ts_switch_by_regime_BROKEN_noreview_44.15`.
>
> **FINAL — the clean 4-year run VALIDATES the rule (2026-10-15).** 908/908 sessions, review applied
> on every one (1 per session; 0 in the broken run):
>
> | | 2023 | 2024 | 2025 | 2026 | 2023-26 | 2025-26 (hold-out) |
> |---|---|---|---|---|---|---|
> | **`ts_switch_by_regime` (clean)** | +12.66% | +2.18% | -6.58% | +61.55% | **+73.75%** | **+50.93%** |
> | static default (its own runs) | -30.61% | -15.86% | -14.56% | +68.42% | -15.98% | +43.90% |
> | study proxy (daily splicing) | — | — | — | — | +78.23% | +65.18% |
> | SSE Composite | -3.70% | +12.67% | +18.41% | -2.03% | +24.37% | — |
>
> - **Full window: +89.7pp over the production default** (+73.75% vs -15.98%), and +49pp over the SSE.
> - **It beats the default in 3 of 4 years** (+43.27pp in 2023, +18.04pp in 2024, +7.98pp in 2025) and
>   gives back only -6.87pp in 2026 — the default's one big year.
> - **It WINS the hold-out: +50.93% vs +43.90%, +7.03pp.** That is the test that matters, since the
>   rule was fitted on 2023-24 — and it is the opposite of what the broken run reported.
> - The proxy was mildly optimistic, not wildly: +78.23% predicted vs +73.75% real, a **-4.5pp
>   haircut**. So daily-splice gating is usable as a first pass; the "the proxy overstates" verdict
>   was an artifact of the broken run and is withdrawn.
> - Still a 4-year sample with a 2-year hold-out, and the as-booked fill model is optimistic on top
>   (apply the slippage/commission ladder). **Not the default; no live change without sign-off.**

**Why 2025 is the weak year (measured 2026-10-15).** Its regime mix was bull 175 / normal 49 / bear 19,
so the bear rule fired on only 17 of 243 sessions (7%) and the switch ran the default almost all year.
The default's 2025 damage is *not* the bull tape — bull days were **+¥50,523** — it is **normal
(¥-104,811 over 49 days) and bear (¥-33,863)**. And the bear branch did work: `ts_ao_er` was **+2.36%/21d**
where the default was **-7.70%**, which is exactly the +7.98pp the switch gained that year. So 2025 is a
*family*-level trap — every momentum/flow pick lost in the +18.41% up-tape year, the same trap that makes
the annual pick unfixable — not something the rule got wrong.

Rule variants, 4-year daily-splice proxy (self-checked: static default reads -15.98% ✓):

| rule | 2023 | 2024 | 2025 | 2026 | total |
|---|---|---|---|---|---|
| **A (shipped)**: 3+ bear → `ts_ao_er`, else default | +25.7% | -17.4% | -6.5% | +83.6% | **+78.23%** |
| D: A + normal → `ts_longup` | +25.7% | -9.7% | -7.6% | +83.6% | **+92.53%** |
| F: A + bull → `ts_hma` | +6.0% | -13.6% | **+0.7%** | +71.5% | +58.10% |
| G: D + bull → `ts_hma` | +14.7% | +7.7% | **+5.7%** | +11.2% | +45.29% |

- **D is the best total — it confirms the study's own fitted map — but it makes 2025 slightly worse.**
- **F and G are the only variants that turn 2025 positive, and both pay for it**: -20pp and -33pp over the
  window, with G giving back most of 2026's +83.6%. F's +0.7% is inside the splice's noise.
- No variant fixes 2025 for free, and every env knob already exists (`SWITCH_NORMAL_STRATEGY`,
  `SWITCH_BULL_STRATEGY`), so any of these is a config change, not code.

**Real 2025 verification of rule G — INCONCLUSIVE, contaminated (2026-10-16).**
`env SWITCH_BULL_STRATEGY=ts_hma SWITCH_NORMAL_STRATEGY=ts_hma` over 20250101-20251231 (243/243
sessions; wiring confirmed in the audit trail: 175 bull + 49 normal → `ts_hma`, 17 bear ≥3 → `ts_ao_er`)
returned **+5.13%**, against the proxy's +5.7% — but it is NOT a clean test: the run crossed the 09:2x
cron tick, the daily paper-sim rewrote `/tmp/review_adjustments.json`, and the engine applied that
foreign review state from session `20250407` onward (**183 of 243 sessions**). Split: the clean 60
sessions (to 2025-04-03) were **-1.51%**, the contaminated 183 contributed **+6.74%**. So the positive
sign is unproven; a re-run with a private `REVIEW_ADJUSTMENTS_PATH` is in flight, and the contaminated
dir is archived as `_results_backups_archive/20250101_20251231_ts_switch_by_regime_CONTAMINATED_cronreview_5.13`.
The 4-year clean run is unaffected — it ran 16:42→~05:4x, never crossing a 09:2x tick.

**Clean re-run: rule G turns 2025 positive — VERIFIED (2026-10-16).** 243/243 sessions, **0 review
applications** (the correct positive control for a rule whose delegates include no review strategy),
same delegate mix (175 bull + 49 normal → `ts_hma`, 17 bear ≥3 → `ts_ao_er`): **2025 = +7.97%** —
**+14.55pp on rule A's -6.58%** and **+22.53pp on the production default's -14.56%**, and above both the
proxy's +5.7% and the contaminated run's +5.13% (the cron's review state was a drag, not a help). Still
behind the SSE's +18.41%.

Cost, from the proxy: rule G's 4-year is +45.29% vs rule A's +78.23% — a **-33pp** give-back, mostly
2026 (+11.2% vs +83.6%). A real 4-year rule-G run is in flight to price that properly, since the proxy
has run *conservative* on the `ts_hma` branch (-2.3pp on 2025) while being -4.5pp optimistic on rule A.
The validated rule-A dir is archived (reversibly) as
`_results_backups_archive/20230101_20260930_ts_switch_by_regime_ruleA_validated_73.75`.

### Rule P (the improvement candidate) FAILED — and it exposed a proxy bug (2026-10-17)

Real 4-year run (`SWITCH_NORMAL_STRATEGY=ts_longup SWITCH_DROP_TRIGGER_PCT=-4`; 908/908 sessions,
0 review applications, audit `drop_pct` populated, 18 drop-trigger firings):

| | 2023 | 2024 | 2025 | 2026 | 2023-26 |
|---|---|---|---|---|---|
| rule P (REAL) | -3.07% | +32.60% | -19.83% | +5.56% | **+8.77%** |
| rule P (proxy) | +25.7% | -7.4% | -5.6% | +81.3% | +99.11% |
| rule A (REAL) | +12.66% | +2.18% | -6.58% | +61.55% | **+73.75%** |

**The proxy's +99.11% was an artifact.** `ts_longup` never ran in 2023 or 2026, and `run_rule`
silently substituted the *default* on those days — inventing the default's returns exactly where
the delegate was missing. Fixed: the tool now SKIPS any day whose delegate has no daily path and
reports the coverage plus the missing (strategy, year) pairs. After the fix D reads +48.03% on
774/904 days and P +52.98% on 777/904 — neither is a 4-year number at all, and the real run
settles it at +8.77%.

**Verdict: no improvement found.** Among the variants whose delegates DID run every year (valid
proxies): J `bear_run>=2` +79.61%, O +76.89%, A +78.23%, B +76.69% — all within ±1.4pp of A, i.e.
noise. **Rule A at +73.75% remains the best real 2023-2026 result.**

A real 4-year engine run (20230101-20260930, `--no-search --no-ai`, SL off + compounding on; 908
sessions, 698 default / 210 `ts_ao_er` days; results in
`backtest/results/20230101_20260930_ts_switch_by_regime/`):

| | 2023 | 2024 | 2025 | 2026 | 2023-2026 | 2025-26 (hold-out) |
|---|---|---|---|---|---|---|
| **`ts_switch_by_regime` (real run)** | +21.84% | -2.81% | +3.06% | +18.12% | **+44.15%** | **+21.73%** |
| static default | -30.61% | -15.86% | -14.56% | +68.42% | -15.98%* | +43.90% |
| proxy (this study, daily compounding) | — | — | — | — | +78.23% | +65.18% |

(*the default's 4-year figure is the product of its own yearly runs; no single continuous run exists.)

- **Full window the switch wins by +60pp** (+44.15% vs -15.98%), beats the SSE's +24.37%, and never has
  a catastrophic year (worst year -2.81% vs the default's -30.61%). Its own max drawdown is -29.87%
  (trough Aug 2024) — it is not a low-drawdown profile, just a less catastrophic one.
- **But out-of-sample it LOSES: +21.73% vs the default's +43.90%, -22pp** — the proxy predicted +21pp
  the *other* way. 2026 is the whole story: the switch spent 17% of that year in `ts_ao_er` and gave
  back 50pp of the default's +68.42%. Time spent in the bear delegate is time NOT earning the
  default's returns, and the default's one good year was the year that mattered.
- **So the daily-compounding proxy — the same construction as `gate_sim.py` — OVERSTATES.** It splices
  each strategy's daily returns as if the capital were in both at once; a real switch has ONE
  portfolio, each delegate reacts to the portfolio the run actually built (the review overlay is
  path-dependent), and the switch's cost is invisible to the splice. Treat every gate/switch number
  produced by daily-return splicing as an upper-ish bound that must be re-measured with a real run.
- **Verdict: built and mechanised, but NOT validated as a live improvement.** Keep it opt-in; do NOT
  make it the default. The 2023-2025 gains are in-sample (the rule was derived from all four years'
  conditional means), and the one true hold-out test we have says the default wins there.

### Exit levers are regime-dependent too — the T+1 early-cut A/B (2026-10-14)

The default's P&L decomposes by exit bucket, and the T+1 early-weakness cut (`backtest/engine.py`:
gap < -4% at the open / close < -5% vs prev close, fired only when `holding_days == 2`) is the only
bucket that LOSES money in **every** measured year (2023 -155k, 2024 -97k, 2025 -169k, 2026 -274k),
at ~-6k per firing. It is now env-gated (`EARLY_CUT_GAP_PCT` / `EARLY_CUT_CLOSE_PCT`, defaults
unchanged) and A/B'd via `trading_test/early_cut_ab.sh` + `early_cut_ab_2023.sh` — each arm isolated
with its own `REPORT_DIR` + `REVIEW_ADJUSTMENTS_PATH`, ≤2 engines in flight against the Tushare cap:

| arm | 2023 | 2026 | 2-year compounded |
|---|---|---|---|
| control (defaults, -4% / -5%) | **-30.61%** | **+68.42%** | +16.9% |
| cut OFF (-99 / -99) | -26.34% | +63.97% | +20.8% |
| cut WIDER (-8% / -10%) | -17.74% | +50.17% | **+23.5%** |

Early-cut days fired: control 27 (2023) / 40 (2026); OFF 0/0; WIDER 1/4.

- **The optimal width flips with the regime.** Bear-heavy 2023 rewards a *looser* cut (+12.9pp
  wide, +4.3pp off); bull-heavy 2026 rewards the *tight* default (+4.5pp over off, +18.3pp over
  wide). Same shape as the stop-loss finding (helps the bad years, costs 27pp in 2026) — **exit
  tightness is regime-dependent**, which is what the engine's `SL_BULL`/`SL_BEAR` + `HOLD_DAYS_MULT`
  knobs exist for.
- Do NOT read this as "remove the cut": kept tight it is a genuine safety net in a bull tape.
- Natural next step: regime-specific early-cut thresholds (`EARLY_CUT_GAP_PCT_<REGIME>`), then a
  4-year A/B.

> **Consistency note.** The 2026 figure in the four-year table above is the Oct-07 `nostop_LHBfull`
> run (+65.70%), which predates `REVIEW_COMPOUND_SIZING=true`. Re-run on today's config the same
> window reads **+68.42%** (`results_backups/20260101_20260930_nostop_dedupcache/`); the
> always-default four-year total then moves to **-16.0%** and the ex-post annual oracle to
> **+151.8%**. The table's conclusion is unchanged.
