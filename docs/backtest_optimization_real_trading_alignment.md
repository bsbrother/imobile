# Walkthrough - Backtest Optimization and Real Trading Alignment

Documents the evolution of ts_7AZ backtest optimization, from baseline to current best, and real trading alignment analysis.

---

## Current Best Configuration

**Period:** 2026-01-01 to 2026-06-19  
**Strategy:** ts_7AZ (CANSLIM)  
**Result:** **70.60% total return** (saved at `backtest/results_backups/20260101_20260619_ts_7AZ_70.60_baseline`)

### Parameters

| Variable | Value | Notes |
|---|---|---|
| `SL_BULL` | 0.025 | 2.5% stop-loss in bull |
| `SL_NORMAL` | 0.025 | 2.5% in normal |
| `SL_VOLATILE` | 0.02 | 2% in volatile |
| `SL_BEAR` | 0.015 | 1.5% in bear |
| `SL_WITH_RE_PICK` | false | Frozen SL (no widening on re-pick) |
| `HOLD_DAYS_MULT` | 0.5 | 50% shorter hold: Bull 7d, Normal 5d, Vol 4d, Bear 2d |
| `ER_EXIT_ENABLED` | true | Kaufman ER trend exit |
| `SCORE_MIN` | 0 | No score filter |
| `BACKTEST_BUY_OPEN_PRICE` | true | Buy at open price |
| `SL_ENABLED` | true | Stop-loss enabled |
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
| **Frozen SL + HOLD_DAYS_MULT=0.5** | **70.60%** | **Current best (most stable)** |

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
| Buy fill | OPEN price (instant) | Broker trigger when price ≤ buy_price | ~0.1-0.3% |
| Sell fill | Exact TP/SL from OHLCV | Market fill at trigger | ~0.1-0.5% |
| Order submission | Instant (DB write) | ADB automation: 5-10s/order | 1-3 min total |
| Missed trades | 0 | App login/network/ADB failures | 1-5% of days |
| Partial fills | Always 100% | A-share can have partial fills | Rare for small sizes |

### Realistic Return Estimate

- **Conservative (85% capture):** ~60% return
- **Realistic (92% capture):** ~65% return
- **Optimistic (97% capture):** ~68% return
- **Worst case (one spike missed):** ~55-58% return

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
