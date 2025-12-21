# Notes for Using [backtest_orders.py](file:///home/kasm-user/apps/imobile/backtest_orders.py) in Real Market

> [!CAUTION]
> **CRITICAL WARNING: DATA LOSS RISK**
> The current script **WIPES the database** every time it runs.
> Lines 1750-1753:
> ```python
> os.system(f"""
>     rm -f db/test_imobile.db; sqlite3 db/test_imobile.db < db/imobile.sql;
>     rm -rf {REPORT_PATH}/*; rm -rf /tmp/tmp
> """)
> ```
> If you modify the script to use your real database (`imobile.db`) without removing these lines, **YOU WILL LOSE ALL YOUR TRADING HISTORY**.

## 1. Database Configuration
- **Current State**: The script imports `DBTEST` from `db.db`:
  `from db.db import DBTEST as DB`
- **Required Change**: For real trading, you must use the production database:
  `from db.db import DB as DB`
- **Action**: You must manually change the import or modify [db.py](file:///home/kasm-user/apps/imobile/db/db.py) to route `DBTEST` to the real DB (not recommended without removing the wipe logic).

## 2. Simulated Execution vs. Real Execution
- **Current State**: The functions [execute_buy_order](file:///home/kasm-user/apps/imobile/backtest_orders.py#344-464) and [execute_sell_order](file:///home/kasm-user/apps/imobile/backtest_orders.py#466-577) **DO NOT** send orders to a broker. They only record a transaction in the local SQLite database.
- **Implication**: This script is currently a **Signal Generator** and **Shadow Ledger**.
  - It will tell you what *should* be bought/sold.
  - It will track P&L *assuming* you filled at those prices.
  - You must **manually place orders** in your broker terminal (e.g., QMT, XtQuant, TDX).

## 3. Data Source (`ts_go`) Limitations
- **Current State**: The Go picker (`utils/go-stock`) fetches data using Tushare.
- **Fallback Logic**: If run during the day (e.g., 10:00 AM) when today's data is not yet available, it falls back to **Yesterday's Data**.
- **Implication**: You cannot use this for "Intraday" picking based on live data unless Tushare provides real-time snapshot data (which `GetTopList` usually handles, but fallback logic might mask it).
- **Recommendation**: Ensure you are running it at a time when data is available (usually after market close 15:00+ for next day), OR ensure the Go tool is updated to handle real-time snapshots if you want intraday signals.

## 4. T+1 Enforcement
- **Current State**: The script strictly enforces T+1 rules (cannot sell stocks bought today).
- **Note**: This is correct for China A-shares. However, if you are trading other instruments (e.g., ETF intraday, bonds) or have special privileges, this script will block you from recording those trades.

## 5. Argument Handling & Defaults
- **Current State**:
  ```python
  start_date = '2025-12-01'
  end_date = '2025-12-31'
  ```
  It defaults to a specific hardcoded date range if arguments are not provided.
- **Action**: When running for "today", you must explicitly pass today's date:
  `python backtest_orders.py YYYYMMDD YYYYMMDD ts_go`

## 6. Recommendations for "Real Market" Adaptation
1.  **Remove the DB Wipe**: Delete lines 1750-1753 immediately.
2.  **Switch DB**: Change line 47 to `from db.db import DB`.
3.  **Automate Execution (Optional)**: If you want automatic trading, you need to integrate an Order Execution System (like `mini-qmt` or `xtquant`) inside `execute_buy_order` and `execute_sell_order`.
4.  **Operational Check**: Ensure `go build` works in your environment, as it rebuilds the picker every time.

