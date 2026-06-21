# Pre-Market Smart Order Creation (09:15–09:25)

This document explains how smart orders are generated before the A-Share market opens,
covering data flow, buy-price logic per regime, and the smart order trigger mechanic.

---

## Overview

Smart orders are Guotai Junan conditional orders placed via the **国泰海通君弘** app's
"智能交易" feature. Each order has:

| Field | Description |
|---|---|
| `触发条件` | Price condition that fires the order, e.g. `股价<=13.43元(触发买入)` |
| `买入/卖出价格` | Execution price method: `即时买一价` (best ask at trigger time) |
| `买入/卖出数量` | Number of shares |
| `有效期至` | Expiry date (trading day); order is auto-cancelled after this date |

The trigger for a **buy** order is always `股价 <= buy_price` — the order fires when
price drops **to or below** the trigger. This is important for bull markets where stocks
gap up at open (see below).

---

## Workflow

```
~09:00  python trading/runner.py 20260622 --phase pre-market
          │
          ▼
     pick_orders_trading()  [backtest/engine.py]
          │
          ├─ Step 1: pick_stocks_to_file(date, src='ts_7AZ')
          │    ├─ detect_market_regime(yesterday)   → bull / bear / normal / volatile
          │    ├─ ts_7AZ CANSLIM 7-factor screener
          │    └─ Writes: backtest/results/pick_stocks_20260622.json
          │
          ├─ Step 2: create_smart_orders_from_picks(pick_file)
          │    ├─ python -m backtest.cli analyze --stocks-file ...
          │    │    └─ analyze_stocks_and_generate_orders()  [backtest/cli.py]
          │    │         ├─ Historical data: close, ATR, RSI, Bollinger Bands
          │    │         ├─ buy_price  ← regime-dependent (see below)
          │    │         ├─ sell_take_profit  ← buy_price × (1 + take_profit_ratio)
          │    │         ├─ sell_stop_loss    ← buy_price × (1 - stop_loss_ratio)
          │    │         └─ position_size     ← rank-weighted allocation
          │    ├─ Upsert new orders into smart_orders table (DB)
          │    ├─ Adjust buy_price of existing orders (take lower price)
          │    └─ Writes: backtest/results/smart_orders_20260622.json
          │
          ├─ Step 3: generate_daily_report()       ← SKIPPED (future date, no OHLCV)
          └─ Step 4: generate_period_report()      ← SKIPPED (future date, no OHLCV)
```

---

## Buy Price Logic by Regime

### Why Regime Matters

The Guotai smart order fires when `股价 <= buy_price`. In a **bear** or **normal** market,
stocks typically open near or below the previous close, so setting `buy_price` slightly
below close acts as a conservative dip-buy trigger.

In a **bull** market stocks frequently **gap up** 3–8% at open. If `buy_price = close_price`,
the stock opens above the trigger and the order **never fires**.

### Formula

```python
if regime == 'bull':
    # Gap-adjusted trigger: set buy_price above close to catch typical gap-up opens.
    is_wide_limit = symbol.startswith('3') or symbol.startswith('688')
    max_gap_pct   = 0.13 if is_wide_limit else 0.07   # ChiNext/STAR vs main board
    min_gap_pct   = 0.02                               # always at least 2% above close

    atr_gap  = latest_atr * 0.5                        # expected single-session move
    gap_pct  = atr_gap / close_price
    gap_pct  = max(min_gap_pct, min(gap_pct, max_gap_pct))

    buy_price = round(close_price * (1 + gap_pct), 2)

elif regime == 'bear':
    # Conservative: trigger only if stock dips, no higher than yesterday's close.
    buy_price = min(rsi_bb_support_price, close_price * 0.99)

else:  # normal / volatile
    # RSI/Bollinger Band/support-based entry.
    buy_price = rsi_bb_support_price
```

### Expected Gap Coverage (Main Board, ±10% limit)

| Stock ATR | gap_pct | buy_price vs close | Fires if open ≤ |
|---|---|---|---|
| Low (ATR ~1%) | 2% (min) | close × 1.02 | +2% above close |
| Normal (ATR ~5%) | 2.5% | close × 1.025 | +2.5% above close |
| High (ATR ~10%) | 5% | close × 1.05 | +5% above close |
| Very high (ATR ~20%) | 7% (max) | close × 1.07 | +7% above close |

> Stocks gapping more than the cap (e.g. >7% on main board) are left unfilled.
> This is intentional: near-limit-up opens usually indicate overheating; better to miss
> than to chase.

### ChiNext (300) / STAR (688) — 20% Daily Limit

The wider daily limit allows larger gap-ups, so `max_gap_pct = 0.13` (13%).
This provides a meaningful buffer below the 20% limit ceiling.

---

## Take-Profit and Stop-Loss

Ratios come from `detect_market_regime()` which reads `config.json`:

| Regime | TP% | SL% | Max Hold |
|---|---|---|---|
| bull | 200% | 0.5% | 15d |
| normal | 200% | 0.5% | 10d |
| volatile | 200% | 0.5% | 8d |
| bear | 200% | 0.5% | 5d |

ChiNext/STAR stocks additionally receive a +10pp TP bonus (wider limit = higher ceiling).

---

## Position Sizing

Rank-weighted allocation across up to `max_positions = 20` slots:

| Rank | Allocation |
|---|---|
| Rank 1 (≥4 slots remaining) | 25% of remaining cash |
| Rank 2 (≥3 slots remaining) | 22% of remaining cash |
| Rank 3 (≥2 slots remaining) | 18% of remaining cash |
| Rank 4+ | Equal split of remaining cash |

Additionally capped by a 5%-of-portfolio risk-per-position limit:
`buy_quantity = min(risk_adjusted_shares, value_based_shares)`, rounded down to 100-share lots.

---

## Output Files

| File | Path |
|---|---|
| Picked stocks | `backtest/results/pick_stocks_20260622.json` |
| Smart orders | `backtest/results/smart_orders_20260622.json` |
| DB table | `smart_orders` in `shared/db/imobile.db` |

The `smart_orders` table is synced to the Guotai app manually (or via the market-phase
automation). Each order row has `status='running'` until filled, expired, or cancelled.

---

## Notes

- Steps 3 (daily execution report) and 4 (period benchmark report) are **always skipped**
  for future trading dates. They require actual OHLCV close prices which don't exist until
  after market close.
- `valid_until` is set to `get_trading_days_after(this_date, holding_days)` — the order
  auto-expires after the max holding period if not filled.
- Existing running orders for the same stock are **adjusted** (buy_price lowered, valid_until
  extended), not duplicated.

---

## Related

- [TRADING.md](TRADING.md) — Full live trading module documentation
- [BACKTEST.md](BACKTEST.md) — Backtest engine, strategies, and reporting
- [A_Share_Market_Rules.md](A_Share_Market_Rules.md) — T+1, daily limits, fee rules
