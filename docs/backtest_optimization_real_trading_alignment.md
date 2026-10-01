# Walkthrough - Backtest Optimization and Real Trading Alignment

Documents the evolution of ts_7AZ / ts_7AZ_96MA_flow backtest optimization and real trading alignment.

---

## Current Best Configuration

**Period:** 2026-01-01 to 2026-08-07  
**Strategy:** ts_7AZ_96MA_flow (CANSLIM + regime switch)  
**Result:** **107.31% total return** (saved at `backtest/results/20260101_20260807_ts_7AZ_96MA_flow_OPENFILL`)

### Parameters

| Variable | Value | Notes |
|---|---|---|
| `SL_BULL` | 0.025 | 2.5% stop-loss in bull |
| `SL_NORMAL` | 0.025 | 2.5% in normal |
| `SL_VOLATILE` | 0.02 | 2% in volatile |
| `SL_BEAR` | 0.015 | 1.5% in bear |
| `SL_WITH_RE_PICK` | false | Frozen SL (no widening on re-pick) |
| `HOLD_DAYS_MULT` | 1.0 | Config values directly: Bull 7d, Normal 5d, Volatile 4d, Bear 2d |
| `BUY_OPEN_PRICE` | true | Buy at open price (open-fill model) |
| `SELL_OPEN_PRICE` | true | Simple TP/SL sell |
| `ER_EXIT_ENABLED` | true | Kaufman ER trend exit |
| `SCORE_MIN` | 0 | No score filter |
| `BACKTEST_BUY_OPEN_PRICE` | true | Legacy fallback (same as BUY_OPEN_PRICE) |
| `SKIP_GAPS_DOWN_OPEN_PRICE` | false | Don't skip gap-downs |

### Monthly Performance

| Month | Return | Notes |
|---|---|---|
| Jan 2026 | +4.0% | Slow start |
| Feb 2026 | +3.3% | Consolidation |
| Mar 2026 | -4.8% | Drawdown |
| Apr 2026 | +22.1% | Breakout |
| May 2026 | +15.1% | Continued |
| Jun 2026 | +28.8% | Strong finish |

**Key characteristic:** Returns are concentrated in tail-event spike days. ~6 days account for >60% of total returns.

---

## Historical Configurations Tested

| Config | Return | Key Difference |
|---|---|---|
| Baseline (original) | 68.98% | Original params |
| Baseline (post-bugfix) | 81.49% | Zero-share sell bug fixed |
| Tighter SL (SL_BULL=0.028) | 80.36% | Slightly tighter stop |
| ER exit (close simulation) | 87.44% | Ideal close-exit (not real-world) |
| ER exit (next-open exit) | 85.23% | Real-world aligned exit |
| **Frozen SL + HOLD_DAYS_MULT=1.0** | **107.31%** | **Current best (open-fill, ts_7AZ_96MA_flow)** |
| Baseline (ts_7AZ, 6mo) | 70.60% | Earlier shorter-period run |
| Frozen SL + HOLD_DAYS_MULT=0.5 | 70.60% | 50% shorter holds (superseded) |

> Note: The 85-87% results used cached picks and close-exit simulation that overstated real-world returns.
> The 70.60% is the most recent full re-run with conservative, real-world-aligned parameters.

---

## Real Trading Alignment

### What Matches Perfectly

| Aspect | Backtest | Real Trading |
|---|---|---|
| Strategy | ts_7AZ CANSLIM | Same |
| Regime detection | `detect_market_regime()` | Same function |
| Stock picking | `backtest/strategies/ts_7AZ.py` | Same subprocess |
| Order parameters | TP=200%, SL=2.5%, ATR-based buy | Same config + .env |
| Position sizing | Rank-weighted, capped | Same |
| T+1 rule | Enforced | Same A-share rule |
| Fees | 0.00341% + 0.05% stamp | Same broker rates |

### Where They Diverge

| Aspect | Backtest | Real Trading | Gap |
|---|---|---|---|
| Buy fill | OPEN price (instant) | Broker trigger at 09:30 fills ≈ the open | small (~0.1%) |
| Sell fill | exactly the TP/SL price | stop fills at the **open** when it gaps through | **measured: -132pp** (see below) |
| Slippage | 0 by default (`SELL_SLIPPAGE_PCT`/`BUY_SLIPPAGE_PCT`) | 0.2-0.8% per side in A-shares | **measured: -33pp** at 0.2%/0.1% |
| Order submission | Instant (DB write) | ADB automation: 5-10s/order | 1-3 min total |
| Missed trades | 0 | App login/network/ADB failures | 1-5% of days |
| Partial fills | Always 100% | A-share can have partial fills | Rare for small sizes |

### Realistic Return Estimate

Measured from the run's own per-day reports (767 closed round-trips), not assumed as a capture
rate. Same picks and same sizes — only fill prices, slippage and commission were changed:

| scenario | return |
|---|---|
| as booked by the backtest | 192.88% |
| gap-aware stop fills (`SELL_OPEN_PRICE=false`) | **61.04%** |
| + 0.2% sell / 0.1% buy slippage | **28.34%** |
| + retail commission (0.025% vs 0.00341%) | **24.27%** |
| + 0.5% sell / 0.2% buy slippage (pessimistic) | -19.34% |

The dominant correction is not slippage but **gap-through**: 143 of 575 stops (25%) are booked at a
price the day never traded, and 346 (60%) opened below their stop, where a real order fills at the
open — with a median stop of only -1.4%, opening that low is routine. Slippage then bites hard
because turnover is 219x the account (¥131M on ¥600k). The earlier "~60-68%" capture estimate is
consistent with the gap-aware row but folded slippage and fees into an unmeasured haircut.

`max_hold_days: 1` means the profit comes from the **192 scheduled exits** (+1,270,456), not from the
+200% take-profit, which never triggers in 767 trades. Any automation must place that scheduled
exit, not only the TP/SL brackets.

### Structural gaps in the live path

The table above understates the problem: it prices execution slippage while assuming the live
system issues the same trades. It does not. Three separate systems exist and none reproduces the
strategy's exits:

| path | what it is | can it match the backtest? |
|---|---|---|
| `trading/runner.py` | real ADB automation of the 国泰 app (`--submit`) | **No** — emits only BUY + TP/SL, never a SELL |
| `stock_cron_tasks.py` | paper-sim daemon (¥100,000, "NO trading is executed here") | mirrors the mechanics, but shares the optimistic stop fill and charges no fees |
| `pre_market_run.py` | older pre-market path (legacy force-sells) | the only place a scheduled exit exists, and only for legacy holdings |

- `pick_orders_trading` (engine.py:2730-2981), the live entry point, contains **zero** sell /
  exit / expired / take_profit generation. The 192 scheduled exits that carry +1,270,456 of the
  run's +1,157,271 profit have no live counterpart, and `create_order_sell` is never called from
  `runner.py`.
- Bracket renewal for held positions is gated off in live mode: engine.py:499 `if app_positions is
  None:` runs the "TP/SL for ALL DB holdings" block only in backtest mode, while runner.py:168
  passes `app_positions` (an empty list is coerced to `None`, which is why the only 2 of 34 live
  runs that emitted a bracket were runs where the app read came back empty). Observed: 32 of 34
  live pre-market runs emitted no TP/SL, and none since 2026-07-13 — so positions were held naked.
- App-side orders are set `set_valid_until_today()`, so any bracket expires the same day.
- Submission starts at 09:24 (runner.py:95-104) at 5-10s per ADB order, so with 15-20 orders the
  later ones miss the 09:25 auction close.
- `apply_strategy_env` is wired into the backtest CLI only (engine.py:3038); the live path never
  calls it and hardcodes `src='ts_7AZ_96MA_flow_review'` (runner.py:164). The `.env` strategy
  section therefore governs backtests, not live trading — except that python-dotenv's flat read
  still leaks `REVIEW_COMPOUND_SIZING=true` into the live process, so live sizing compounds as a
  side effect.
- The paper sim fills stops at the trigger price exactly like the backtest
  (stock_cron_tasks.py:850-853) and applies no commission or stamp duty, so its reported returns
  track the same inflated number.

The live account is not comparable evidence either: principal ¥300,000, 6 legacy holdings whose
names are not strategy picks, 36 transactions, and `summary_account.last_updated` stuck at
2026-07-14.

Closing the gap means: a live sell path for scheduled exits; daily bracket renewal in live mode
(iterate `app_positions`); `apply_strategy_env` in `runner.py`; submitting before 09:24; and a
gap-aware, fee-charging paper sim if it is used as a gauge. Even then, expect ~25-60%.

### Critical Risk: Spike Days

The strategy's returns are concentrated in ~6 explosive days. Missing even one due to app/ADB failure is the dominant risk. Ensure broker app is logged in and ADB verified before every trading day.

---

## Verification

```bash
# Run backtest
backtest-trading run python backtest/engine.py 20260101 20260619 --no-ai --no-search

# Analyze results
backtest-trading run python backtest/result_backtest.py backtest/results/20260101_20260619_ts_7AZ

# Dry-run real trading
backtest-trading run python trading/runner.py --phase pre-market --dry-run
```
