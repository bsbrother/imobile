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

The table above prices execution slippage while assuming the live system issues the same trades.
It did not. Three separate systems exist:

| path | what it is | can it match the backtest? |
|---|---|---|
| `trading/runner.py` | real ADB automation of the 国泰 app (`--submit`) | emits only BUY + TP/SL, never a plain SELL |
| `stock_cron_tasks.py` | paper-sim daemon (¥100,000, "NO trading is executed here") | mirrors the mechanics, in the same optimistic way |
| `pre_market_run.py` | older pre-market path (legacy force-sells) | only place a scheduled exit existed, and only for legacy holdings |

**Fixed** (engine.py, trading/runner.py, stock_cron_tasks.py):

| gap | what changed |
|---|---|
| No scheduled exit and no bracket renewal live | The held-position block in `create_smart_orders_from_picks` — the only code that produces either — was gated `if app_positions is None:`, which is dead when `runner.py` calls it. The gate is now `if app_positions is None or is_live:`, so a live run emits the daily bracket for every holding plus a scheduled exit (name ends `_expired`; expired / stagnation / ER-trend / max-hold) with the trigger priced at the auction price, which the app's conditional order then sells into at ~the open. Backtest behaviour is unchanged, so the baseline stays reproducible |
| Strategy not wired into live | `runner.py` resolves `DEFAULT_STRATEGY` and calls `apply_strategy_env(..., neutralized=...)` at import, exactly like the backtest CLI, and passes it as `src=` instead of a literal. Wrapped so a malformed `.env` cannot stop trading. Verified: `REVIEW_COMPOUND_SIZING` is `true` for the review strategy and scoped out (`None`) for any other — previously it leaked to all of them |
| 09:24 submission race | Submission starts at `TRADING_SUBMIT_BY` (default 09:15) and the wait now covers brackets as well as BUYs, so orders clear inside the 09:15-09:25 auction. BUY limits come from `_auction_buy_price()`: indicative + 0.5% buffer, floored at the suggested price, capped by the board's daily band |
| Exits indistinguishable from brackets in the log | Scheduled exits are logged as `SCHEDULED EXIT`, so a sell instruction is not read as a bracket |
| Paper sim optimistic | `simulate_trading_day` is gap-aware (a level gapped through fills at the open, annotated `跳空开盘`) and charges commission both sides plus stamp duty on sells, accumulated in `paper['fees']`. `PAPER_SLIPPAGE_PCT` defaults to 0 and is the largest remaining correction |

**Still open, and why none of this reaches 193%:**

- Fill realism is the dominant term, not the wiring: same picks and sizes, gap-aware stops alone take
  192.88% → 61.04%; +0.2%/0.1% slippage → 28.34%; + retail commission → 24.27%.
- The backtest's max-hold exits book at the **close** (engine day loop); the live held-position block
  prices its scheduled exit at the **auction/open**, so live exits that case one session earlier.
- `set_valid_until_today()` still means brackets must be re-placed every day. They now are, but a day
  where the pre-market run fails still leaves positions unprotected.
- No partial fills, no queue position, no cap impact. Turnover is 219x, and the whole thing depends on
  ADB and the broker app, where 1-5% of days are missed.
- The live account is not comparable evidence: principal ¥300,000, 6 legacy holdings whose names are
  not strategy picks, 36 transactions, `summary_account.last_updated` stuck at 2026-07-14.

Expect ~25-60% once the live path is exercised, before operational losses.

For the record, the evidence these changes came from: `pick_orders_trading` (engine.py:2730-2981),
the live entry point, contains zero sell / exit / expired / take_profit generation — so the 192
scheduled exits carrying +1,270,456 of the run's +1,157,271 profit had no live counterpart, and
`create_order_sell` is never called from `runner.py`. 32 of 34 live pre-market runs emitted no
TP/SL at all, and none after 2026-07-13, so positions were held naked.

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
