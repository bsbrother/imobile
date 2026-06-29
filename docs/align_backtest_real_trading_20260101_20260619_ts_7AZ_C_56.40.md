Now I have the actual data. Here's the full analysis:

---

## 1. Backtest Results Analysis

### Confirmed from the actual period report:

```
Initial Capital:    ¥600,000.00
Final Portfolio:    ¥938,385.65
Total Return:       56.40%  (vs SSE +3.22%, CSI300 +6.73%, CSI500 +16.17%)
Realized P&L:       ¥220,277.97 (36.71%)
Unrealized P&L:     ¥118,107.68 (19.68%) — 9 positions still open
Total Transactions: 757 (374 sells, ~383 buys)
Win/Loss visibility: heavy stop-loss churn, big wins on spike days
```

### Daily Process (verified from code + output):

| Step | What Happens | Evidence |
|------|-------------|----------|
| 1. Regime Detection | 120-day MA60/MA120 on SSE Composite → "normal" this run | `market_pattern: normal` in pick JSON |
| 2. Stock Picking | `ts_7AZ.py` → CANSLIM 7-factor → scores 4-5 → pick_stocks JSON | 12 stocks picked per day, varied quality |
| 3. Smart Orders | `cli analyze` → buy_price (ATR-based), TP=200%, SL=2.5-3% | TP=+200%, SL=-2.5% visible in reports |
| 4. Execution | Buys fill at OPEN price if open ≤ buy_price. Sells at TP/SL trigger via OHLCV | `Fill Price: ¥437.01` = open price on Jan 5 |
| 5. Force-sells | `days_held ≥ max_hold_days` OR `stagnation_cut` | `_expired`, `stagnatio` in transaction notes |
| 6. Daily Report | Portfolio value, P&L, per-order execution detail written | 115 daily reports from Jan 5 to Jun 18 |

### Key Performance Dynamics (from actual trading data):

**Phase 1 (Jan): Loss accumulation** — Portfolio dropped to ¥577K (-3.8%) by Feb 11. Heavy stop-loss exits on re-picked stocks (stop_loss is the dominant exit reason).

**Phase 2 (Feb-Mar): Recovery** — Clawed back to ¥618K (+3%) by Mar 3, then flat through Mar.

**Phase 3 (Apr-Jun): Explosion** — Six monster days account for most returns:
| Date | Day P&L | Portfolio Return |
|------|---------|-----------------|
| Apr 13 | +¥57,930 | 12.27% → 19.79% |
| Apr 27 | +¥39,913 | 19.79% → 26.16% |
| May 13 | +¥35,698 | 21.55% → 26.16% |
| May 18 | +¥35,605 | 26.16% → 28.01% |
| Jun 01 | +¥34,369 | 28.01% → 30.26% |
| Jun 12 | +¥26,145 | 36.56% → 44.11% |

Then final days accumulated ¥118K unrealized from held positions (东山精密, 中际旭创, 长飞光纤, 鼎泰高科 all running +10-25%).

### Strategy Character:
- **High churn**: 757 transactions in ~115 days = 6.6/day. Stocks held only 1-5 days.
- **Tight stops**: SL at 2.5-3%, TP at 200% (functionally "no limit" — let winners run)
- **Low correlation to indices**: Beta 0.10-0.18, correlation 0.09 — pure alpha
- **Relies on momentum spikes**: CANSLIM picks concentrated in tech/manufacturing (300xxx ChiNext stocks dominate)

---

## 2. Real Trading Alignment Analysis

### What Matches Perfectly

| Aspect | Backtest | Real Trading | Match? |
|--------|---------|-------------|--------|
| Strategy | ts_7AZ CANSLIM | Same (trading/runner.py calls pick_orders_trading with src='ts_7AZ') | ✅ Identical |
| Regime detection | detect_market_regime() | Same function called | ✅ Identical |
| Stock picking | backtest/strategies/ts_7AZ.py | Same subprocess | ✅ Identical |
| Order parameters | TP=200%, SL=2.5%, buy at ATR-based price | Same config.json + .env overrides | ✅ Identical |
| Position sizing | rank-weighted, capped at max_positions | Same | ✅ Identical |

### Where They Diverge

| Aspect | Backtest | Real Trading | Gap |
|--------|---------|-------------|-----|
| **Buy fill price** | OPEN price (instant fill) | Broker trigger: fills when `股价≤buy_price` → could be open, mid-day, or never | ~0.1-0.3% price difference typical |
| **Sell fill price** | Exact TP/SL price from OHLCV | Broker fills at market when trigger hits → could be worse during volatility | ~0.1-0.5% on volatile exits |
| **Order submission** | Instant (DB write) | ADB automation: open app → navigate → tap → type → confirm → 5-10s per order | 1-3 minute total delay for 10 orders |
| **Missed trades** | 0 missed (perfect simulation) | App login failure, network, ADB timeout, broker server reject | 1-5% of trading days potentially affected |
| **Partial fills** | Always fills 100% | A-share market can have partial fills on limit orders | Rare for these volumes (100-900 shares) |
| **Fee calculation** | Commission 0.00341% + stamp 0.05% sell + transfer 0.002% | Same (Guotai broker rates match) | ✅ Identical fees |
| **Cash management** | Simulated from realized P&L | Real broker available cash → may differ from simulation | Minor (<1%) |
| **Limit-down lock** | Modeled: skip sell if locked limit-down | Actually blocked — same behavior | ✅ Accurately modeled |
| **T+1 rule** | Enforced: buy day = cannot sell until next day | Same A-share rule | ✅ Accurately modeled |

### Quantitative Return Estimate

**Slippage impact (trading friction):**
- 374 sell trades × avg ¥35K fill × 0.15% extra slippage = ~¥19,600
- That's 3.3% of initial capital, or ~5.8% of the ¥338K total profit

**Operational risk (missed execution):**
- 115 trading days, 1-3% chance of app issue per day → expect 1-3 missed days
- If a missed day coincides with a spike day (Apr 13 +57K, etc.) → maximum hit ~10% return
- If missed days fall on flat/negative days → minimal impact ~0-2%

**Conservative scenario (85% capture):** ~48% return
**Realistic scenario (92% capture):** ~52% return
**Optimistic scenario (97% capture):** ~55% return
**Worst case (one spike day missed):** ~40-45% return

### The Critical Risk: SPIKE DAYS

The 56.40% return wasn't steady — it came from 6 explosive days. If real trading misses even ONE of those days due to an app login failure or network issue, the return drops significantly:

```
Without Apr 13 (+57K):     Return drops to ~46%
Without Apr 27 (+39K):     Return drops to ~49%
Without May 13 (+35K):     Return drops to ~50%
Without May 18 (+35K):     Return drops to ~50%
Without Jun 01 (+34K):     Return drops to ~50%
Without Jun 12 (+26K):     Return drops to ~52%
```

The strategy's performance is **highly concentrated in tail events** — a single missed trading day on a spike date is the dominant risk.

---

## Bottom Line

**Yes, the real trading pipeline is structurally aligned with the backtest** — same strategy, same functions, same parameters. The main gap is execution fidelity (fill timing, app reliability), not strategy logic.

**Realistic achievable return: 47-53%** (vs backtest 56.40%). The ~3-9% gap comes from:
- ~3%: Slippage spread on 374 trades
- ~2-5%: Missed/partial execution risk (app automation reliability)
- The spike-day concentration amplifies the operational risk

The single biggest thing you can do to close the gap: **ensure the broker app is logged in and the ADB connection is verified before every trading day** — one missed spike day costs more than all slippage combined.
