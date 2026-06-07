## backtest

Here is a comprehensive, deep-dive analysis of the **`backtest_orders.py`** and **`ts_month_src.py`** trading pipeline.

---

### 1. System Overview & Architectural Flow
The script `backtest_orders.py` drives an end-to-end backtesting engine designed for the Chinese A-share market. For each trading date within the backtest period, the system executes a modular 4-step pipeline:

```
[Start Backtest Date Loop]
  ├── Step 1: Detect Market Regime & Pick Stocks (via ts_month_src.py)
  ├── Step 1.5: Query Database (transactions & holding_stocks) for available Cash/NAV
  ├── Step 2: Create/Adjust Smart Orders (Entry, TP, SL, Sizing)
  ├── Step 3: Check execution with strict A-Share Compliance & generate Daily Report
  └── Step 3.2: Auto-update Trailing Stop-Loss values in SQLite database
[End Date Loop]
  └── Step 4: Generate Period Report (Overall P&L, Sharpe Ratio, Win Rate, Drawdown)
```

---

### 2. Strategy Selection & Dynamic Regime Detection (`ts_month_src`)
When configured with the `ts_month_src` strategy source, the system delegates stock selection to `pick_stocks_from_sector/ts_month_src.py`. This script dynamically analyzes market state using historical data from the **Shanghai Composite Index (`000001.SH`)** over a **20-trading-day (approx. 1 month) lookback window**:

#### **Regime Detection Metrics**
1. **Trend ($Trend_{10d}$)**: Distance from the 10-day Moving Average: $\frac{Price_{current} - MA_{10}}{MA_{10}} \times 100$.
2. **Volatility ($Vol$)**: Standard deviation of daily percentage returns times 100.
3. **Momentum ($Mom$)**: Total return over the 20-day period: $\frac{Price_{current}}{Price_{start}} - 1 \times 100$.

#### **Regime & Strategy Mapping Matrix**
Based on the metrics above, the regime is categorized and mapped to a specific picker script:

| Detected Regime | Condition | Momentum Condition | Selected Strategy | Delegated Script / Picker Command |
| :--- | :--- | :--- | :--- | :--- |
| **Volatile** | $Vol > 2.2\%$ | *Any* | **`ts_ai_pick`** | `python pick_stocks_from_sector/ts_ai_pick.py` |
| **Bull** | $Price_{current} > MA_{10}$ and $Trend_{10d} > 0.3\%$ | $Mom > 4.0\%$ | **`ts_longup`** | `python pick_stocks_from_sector/ts_longup.py` |
| **Bull** | $Price_{current} > MA_{10}$ and $Trend_{10d} > 0.3\%$ | $Mom \le 4.0\%$ | **`ts_dc`** | `python pick_stocks_from_sector/ts_ths_dc.py {date} ts_dc` |
| **Bear** | $Price_{current} < MA_{10}$ and $Trend_{10d} < -0.3\%$ | $Mom < -4.0\%$ | **`ts_hma`** | `python pick_stocks_from_sector/ts_hma.py` |
| **Bear** | $Price_{current} < MA_{10}$ and $Trend_{10d} < -0.3\%$ | $Mom \ge -4.0\%$ | **`ts_daily`** | `python pick_stocks_from_sector/ts_daily.py` |
| **Normal** | *Default* | *Any* | **`ts_dc`** | `python pick_stocks_from_sector/ts_ths_dc.py {date} ts_dc` |

*(Note: If needed, the system can also call the Go-based picker `ts_go` which builds and runs the high-performance binary in `utils/go-stock`).*

---

### 3. Smart Order Creation & Portfolio Sizing
Once stocks are picked, `create_smart_orders_from_picks` translates selections into precise order parameters:

1. **Indicator Analysis**: Uses RSI, Bollinger Bands, recent Support/Resistance, and ATR to identify entry zones.
2. **Regime-Dependent Pricing**:
   * **Bull Market**: Aggressive entry; orders are configured to buy directly at the **Open Price** to prevent missing vertical moves.
   * **Bear Market**: Defensive entry; orders are capped at **$\le$ Close $\times$ 0.99** of the previous day.
3. **Position Sizing**:
   * Cash allocation is calculated as: $\text{Position Value} = \frac{\text{Remaining Cash}}{\text{Remaining Slots}}$ (capped at `MAX_POSITIONS`, default 10).
   * **China A-Shares 100-Share Rule**: Order quantities are strictly rounded down to the nearest hundred: `buy_quantity = (buy_quantity // 100) * 100`.
   * Order is skipped if the remaining cash cannot afford a single 100-share lot.

---

### 4. Realistic China A-Share Compliance Rules
The engine enforces several critical regulatory and real-world market constraints to guarantee backtest integrity:

* **T+1 Compliance (Same-Day Sale Ban)**:
  * When evaluating sells, the system compares the actual `purchase_date` in the SQLite database to the current date. Selling is blocked if `purchase_date == current_date`.
* **Limit Down Lock (Liquidity Sells Block)**:
  * Retail traders cannot execute sells on a stock locked at limit down.
  * *Rule*: For ChiNext/STAR (`30...` or `688...`), `limit_down_price = round(prev_close * 0.805, 2)`. For Main board, `limit_down_price = round(prev_close * 0.905, 2)`.
  * *Constraint*: If a stock's intraday high is at or below the limit-down price, execution returns `'locked_limit_down'` and holds the position.
* **Limit Up Open Lock (Liquidity Buys Block)**:
  * Retail traders cannot realistically get filled on a stock that opens limit-up.
  * *Rule*: ChiNext/STAR `limit_up_price = round(prev_close * 1.195, 2)`, Main board `limit_up_price = round(prev_close * 1.095, 2)`.
  * *Constraint*: If `open_price >= limit_up_price`, the buy order is rejected with `'limit_up'` and marked unexecuted.
* **Open Price Fill Principle**:
  * To ensure execution realism, buy fills occur strictly at the **Open Price**, preventing idealized "buy-at-daily-low" bias.

---

### 5. Execution Strategies & Risk Controls
To protect profits and cut losses, the engine applies multiple advanced risk mitigation policies:

* **Trailing Stop-Loss**:
  * As the stock hits new highs, the engine dynamically adjusts the stop-loss upward, keeping a tight envelope of risk.
* **Early Weakness Cut (T+1 Protection)**:
  * Triggered on the first day the stock becomes sellable (T+1):
    1. **Gap Down Cut**: If the stock opens lower than **$-4.0\%$** relative to the previous close, it sells immediately at the **Open Price**.
    2. **Intraday Drop Cut**: If the stock's closing price drops below **$-5.0\%$**, it sells at the **Close Price**.
* **Strict Max-Hold Day Close**:
  * Forces a complete liquidation at **Close Price** once the maximum holding period (default 4 days) is met to eliminate long-term paper drawdowns.
* **Stagnation Cut**:
  * If a position is held for more than half its holding period and the return remains flat or losing ($< 2.0\%$), the stock is sold at **Close Price** to recycle capital into higher-velocity assets.
