# Trading Module — iMobile A-Shares Automated Trading

The `trading/` module is the **live-trading** subsystem of iMobile. It automates Chinese A-Share market operations by driving the 国泰海通君弘 (Guotai Junan) broker Android app via ADB + AI vision agents, and syncs real-time market data into the local SQLite database for the web dashboard.

---

## Table of Contents

- [Step-by-Step Process](#step-by-step-process)
- [Module Structure](#module-structure)
- [Architecture](#architecture)
- [Pre-Requirements](#pre-requirements)
- [Environment Variables](#environment-variables)
- [Usage](#usage)
- [Data Sync Pipeline](#data-sync-pipeline)
- [Trajectory Replay](#trajectory-replay)
- [Error Handling & Retries](#error-handling--retries)
- [Crontab Scheduling](#crontab-scheduling)

---

## Step-by-Step Process

The entire trading workflow is orchestrated by `trading/runner.py → run_daily_trading()`. It splits the daily trading cycle into three distinct phases:

### Phase Router (`main()`)

```
python trading/runner.py [date] [--phase pre-market|market|post-market|auto|all]
  │
  ├── cleanup_empty_trajectories() — purge empty trajectory dirs
  ├── login() — set GUOTAI_ACCOUNT, GUOTAI_PASSWORD env vars
  │
  ├── If --sync-only → cron_sync_app_to_db(check_trading_day_and_time=False)
  │
  └── Else: run_daily_trading(this_date, phase, user_id, dry_run, submit, package_name)
        │
        ├── Check trading calendar → if non-trading day, advance to next trading day
        ├── Auto-detect phase by current time:
        │     < 09:30 → pre-market
        │     09:30-15:00 → market
        │     > 15:00 → post-market
        ├── For each phase: check time guard, then run phase function
        └── Return status JSON summary
```

---

### Phase 1: Pre-Market (`run_pre_market`) — Before 09:30

```
Step 1.1: Sync Check (check_and_sync_app_to_db)
  │
  ├── Query DB state: cash, holdings count, running orders count
  ├── If running orders > 0 → stale orders from yesterday
  │     → cron_sync_app_to_db(check_trading_day_and_time=False)
  │     → cleans up by syncing real app state back to DB
  └── If clean (0 running orders) → skip, proceed

Step 1.2: Fetch App State (unless --dry-run)
  │
  ├── pre_requirements(package_name)
  │     ├── Get ADB device connectivity via mobilerun AndroidDriver
  │     ├── Create LLM: GoogleGenAI with gemini-3.1-flash-lite-preview (free tier)
  │     ├── Create MobileAgent config:
  │     │     max_steps=60, vision=True, reasoning=True
  │     │     after_sleep_action=1.5s between taps
  │     └── Return (tools, llm, config)
  │
  ├── Fetch POSITIONS from app:
  │     get_summary_position_from_app_position_page_structured(config, llm, tools)
  │       ├── MobileAgent navigates to 我的持仓 (My Holdings) page
  │       ├── AI reads screen → extracts CSV sections
  │       ├── Section 1: Account summary (cash, total assets, floating P&L)
  │       └── Section 2: Holdings list (name, code, holdings, price, cost)
  │
  ├── Fetch RUNNING ORDERS from app:
  │     get_order_from_app_smart_order_page_structured(config, llm, tools, tabs=["运行中"])
  │       ├── MobileAgent navigates to smart order page
  │       ├── Switch to 运行中 (Running) tab
  │       └── Extracts: name, code, trigger_condition, quantity, valid_until, order_number
  │
  └── Parse into structured lists: app_cash, app_positions[], app_running_orders[]

Step 1.3: Stock Picking + Order Generation
  │
  └── pick_orders_trading(start_date, end_date, user_id, src='ts_7AZ',
                          is_live=True, backtest_search=False, backtest_ai=False,
                          app_cash, app_positions, app_running_orders)
        │
        └── (Full 7-phase backtest pipeline from backtest/engine.py — see BACKTEST.md)
              ├── Market regime detection
              ├── ts_7AZ CANSLIM stock picking
              ├── Capital calculation (uses real app_cash)
              ├── Smart order generation (buy / TP/SL)
              └── Writes to backtest/results/daily/
                    pick_stocks_YYYYMMDD.json
                    smart_orders_YYYYMMDD.json

Step 1.4: Summary Logging
  │
  ├── Log regime, count of BUY orders, count of TP/SL orders
  └── Log each order: symbol, name, buy_price, TP, SL, quantity

Step 1.5: Submit to App (only with --submit flag)
  │
  ├── Parse smart_orders JSON
  ├── Separate: BUY orders (first N), TP/SL orders (rest)
  ├── For each BUY order:
  │     create_buy_order(code, price, quantity, submit=True)
  │       └── ADB taps through app UI to submit buy order
  ├── For each TP/SL order:
  │     create_tp_sl_order(code, tp_price, sl_price, quantity, submit=True)
  │       └── ADB taps through app UI to set TP/SL conditions
  └── Note: Without --submit, orders stay in DB only

Step 1.6: Cleanup
  └── OrderAnalyzer is NOT run — no daily report generation for live trades
      (requires historical OHLCV close prices, unavailable on day-of)
```

**Key difference from backtest mode:**
- `is_live=True` → uses real `imobile.db`, not test DB
- `backtest_search=False, backtest_ai=False` → pure technical analysis, no LLM calls during picking
- Real cash from app homepage overrides simulated capital
- No daily/period P&L reports generated (those use historical OHLCV)

---

### Phase 2: Market (`run_market`) — 09:30 to 15:00

```
Backtest model (for reference):
  └── OrderAnalyzer.check_order_execution()
        Matches OHLCV data against trigger conditions (buy_price, TP, SL)
        to simulate what would have executed. This is how the 56.40%
        backtest return was achieved.

Real trading model:
  └── Broker Auto-Execution (server-side)
        Smart orders submitted during pre-market are auto-executed by
        Guotai Junan's server-side trigger system. The app monitors
        conditions on the broker's servers — no local polling needed.

        This is a FUNDAMENTAL architectural difference:
        - Backtest: deterministic OHLCV matching → 100% execution fidelity
        - Real: broker triggers → fills depend on market liquidity, queue
          position, and exchange matching rules

Step 2.1: Mid-Day Sync (optional, for monitoring)
  │
  └── cron_sync_app_to_db(check_trading_day_and_time=False)
        ├── Open broker app via ADB + trajectory replay
        ├── Navigate to 行情 (Quotes) page → extract index + stock prices
        ├── Navigate to 我的持仓 (My Holdings) → extract positions
        ├── Navigate to 条件单 (Smart Orders) → extract order status
        └── Sync all data into DB tables

Key difference from backtest:
  - Backtest calls check_order_execution() to simulate fills from OHLCV
  - Real trading does NOT call check_order_execution() — broker handles it
  - Order execution status is only known after syncing app→DB
```

---

### Phase 3: Post-Market (`run_post_market`) — After 15:00

```
Step 3.1: Final Sync
  │
  └── cron_sync_app_to_db(check_trading_day_and_time=False)
        └── Same full sync pipeline — captures final day-end state
            (quotes, positions, order status, transactions)

Step 3.2: Backtest-Style Analysis (NEW)
  │
  └── generate_trading_report(this_date, user_id)
        ├── Read from production DB:
        │     ├── summary_account: total_assets, cash, floating_pnl
        │     ├── transactions: today's buys/sells with P&L from notes
        │     ├── holding_stocks: current positions with cost/price/P&L
        │     └── smart_orders: running order status
        │
        ├── Compute:
        │     ├── Today realized P&L (from transaction notes)
        │     ├── Total P&L (realized + unrealized)
        │     ├── Benchmark comparison (SSE Composite today return)
        │     ├── Position expiry analysis (days held vs max_hold)
        │     └── Win/loss classification
        │
        ├── Generate Markdown report:
        │     Account Summary, Transactions, Holdings
        │     Position Expiry Analysis (⚠️ force-sell warnings)
        │     Smart Orders status
        │
        └── Generate Suggestions:
              1. Force-sell expired positions count + codes
              2. Idle cash deployment recommendation
              3. Winning positions — trail stops on re-pick
              4. Losing positions — stop-loss or expiry status
              5. Unfilled BUY orders carried over
              6. Market context from SSE benchmark
              → Write to backtest/results/daily/trading_report_YYYYMMDD.md
```

---

## Module Structure

```
trading/
├── runner.py              # CLI entry — orchestrates all 3 trading phases
├── guotai.py              # Guotai broker app integration: navigation, extraction, DB sync
├── adb.py                 # ADB device connectivity & app checks
├── report.py              # Post-market trading report generator
├── sync_app_to_db.py      # Structured app→DB sync using MobileAgent + ADB
├── extractors.py          # Pydantic data models + AppDataExtractor base class
├── create_order_buy.py    # ADB-driven buy order submission
├── create_order_sell.py   # ADB-driven sell order submission
├── create_order_tp_sl.py  # ADB-driven TP/SL order submission
├── stop_order.py          # ADB-driven stop order management
└── trajectory/            # Pre-recorded tap sequences for replay
    ├── index_quote/       # Nav: app → 行情 page
    ├── order_page/        # Nav: app → 条件单 page
    └── orders_detail/     # Timestamped recordings
```

### File Responsibilities

#### `runner.py` — Main CLI Orchestrator
- Entry point: `python trading/runner.py`
- Parses CLI args, checks trading calendar
- Routes to phase-specific functions
- Handles --dry-run, --submit, --sync-only modes

#### `guotai.py` — Broker App Integration
- **App lifecycle**: `open_app()`, `close_app()`, `login()`, `goto_homepage()`, `pre_requirements()`
- **Navigation**: AI-powered page navigation with trajectory replay fallback
- **Data extraction**: CSV parsing from app screens via Gemini Vision + Levenshtein fuzzy matching
- **DB sync**: `cron_sync_app_data_to_db()` — full quote/position/order sync with time guards
- **GuotaiExtractor** class — structured extraction using Pydantic models

#### `sync_app_to_db.py` — Structured Sync
- Pure ADB UI parsers (no AI) for fast batch extraction
- Structured extraction methods: `get_summary_position_from_app_position_page_structured()`, `get_order_from_app_smart_order_page_structured()`
- Functions: `sync_index_quote_data_to_db()`, `sync_summary_position_data_to_db()`, `sync_order_data_to_db()`

#### `adb.py` — ADB Utilities
- Device discovery, connectivity checks
- App package verification

#### `report.py` — Trading Report Generator
- Reads production DB tables
- Generates Markdown report with account, transaction, and holding summaries

#### `extractors.py` — Data Models
Pydantic models returned by MobileAgent:
- `ExtractTransaction` — name, date, price, quantity, type, amount
- `ExtractOrder` — name, code, trigger, quantity, valid_until, order_number
- `ExtractQuote` — indices + stocks with price/change data
- `ExtractPosition` — floating P&L, account assets, market cap, holdings

---

## Architecture

```
Android Device / Genymotion Emulator (Google Pixel 6 Pro)
        │  USB / TCP ADB (127.0.0.1:6555)
        ▼
   ADB (adb.py) → AndroidDriver (mobilerun)
        │
        ▼
DroidRun Portal (accessibility service on device)
        │  tap / swipe / screenshot commands
        ▼
 MobileAgent + Gemini Vision (guotai.py)
        │  AI reads screen, navigates app, extracts CSV data
        │  Model: gemini-3.1-flash-lite-preview (free tier)
        │  Max steps: 60 per task, vision=True, reasoning=True
        ▼
  Data Parsing & Validation (guotai.py → parse_csv_data)
        │  Levenshtein fuzzy header matching (≤3 distance)
        │  OCR screenshot extraction fallback (ocr_screenshot2file)
        ▼
  SQLite DB (shared/db/imobile.db)
        │  Tables: market_indices, holding_stocks, summary_account,
        │          smart_orders, transactions
        ▼
  Reflex Web Dashboard (web/)
```

---

## Data Sync Pipeline

Each sync cycle (called by all 3 phases) extracts three independent datasets:

```
1. QUOTE SYNC
   get_index_stock_from_app_quote_page()
     └── Navigate to 行情 (Quotes) page
     └── AI extracts CSV: indices (name, number, ratio) + stocks (name, code, price, change%)
     └── sync_index_quote_data_to_db()
           ├── UPSERT market_indices (current_value, change_percent)
           ├── UPSERT holding_stocks (current_price, change columns)
           └── DELETE orphans not present in latest data

2. POSITION SYNC
   get_summary_position_from_app_position_page()
     └── Navigate to 我的持仓 (My Holdings)
     └── AI extracts CSV: account summary + position details
     └── sync_summary_position_data_to_db()
           ├── UPSERT summary_account (total_assets, cash, floating_pnl)
           ├── UPSERT holding_stocks (market_value, cost_basis, holdings)
           └── DELETE orphaned positions

3. ORDER SYNC
   get_order_from_app_smart_order_page()
     └── Navigate to 条件单 (Smart Orders) → 3 tabs
     └── AI extracts CSV from each tab: 运行中 / 已触发 / 已结束
     └── sync_order_data_to_db()
           ├── UPSERT smart_orders (trigger_condition, status, reason_of_ending)
           └── DELETE orders not present in app (expired/cancelled remotely)
```

**Retry policy:** Each step retries up to 3 times with 5-second delays on failure.

**DB tables written:**

| Table | Source | Key Columns |
|---|---|---|
| `market_indices` | Quote sync | index_code, current_value, change_percent |
| `holding_stocks` | Quote + Position sync | code, name, current_price, market_value, cost_basis, pnl_float |
| `summary_account` | Position sync | total_assets, total_market_value, cash, floating_pnl, position_percent |
| `smart_orders` | Order sync | trigger_condition, buy_or_sell_quantity, status, valid_until, order_number, reason_of_ending |

---

## Trajectory Replay

To avoid running the full AI agent every time (faster, saves API costs), pre-recorded navigation sequences are replayed:

```python
replay_page(description=['行情', '我的持仓'])
```

**Recording a new trajectory:**
```bash
mobilerun run "Open 国泰海通君弘, tap 行情, tap 我的持仓" \
  --provider GoogleGenAI \
  --model gemini-3.1-flash-lite-preview \
  --save-trajectory step
```

**Fallback:** If no matching trajectory is found, the MobileAgent runs live.

---

## Pre-Requirements

1. **Python environment** with `mobilerun` (DroidRun fork), `llama_index`, `google-genai`
2. **Android device/emulator** connected via ADB with DroidRun Portal installed
3. **国泰海通君弘 app** (`com.guotai.dazhihui`) installed
4. **Gemini API key** in `.env` (GOOGLE_API_KEY or GEMINI_API_KEY)
5. **Login credentials** in `.env` (GUOTAI_PACKAGE_NAME, GUOTAI_PASSWORD)

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | ✅ | Gemini API key (free tier: AI Studio) |
| `GEMINI_MODEL` | optional | Model (default: `gemini-3.1-flash-lite-preview`) |
| `GUOTAI_PACKAGE_NAME` | ✅ | Android package: `com.guotai.dazhihui` |
| `GUOTAI_PASSWORD` | ✅ | Trading account PIN (6 digits) |
| `DB_IMOBILE_FILE` | optional | Path to SQLite DB (default: `./shared/db/imobile.db`) |
| `LOG_LEVEL` | optional | `DEBUG` / `INFO` / `WARNING` (default: `DEBUG`) |
| `GEMINI_THINKING_BUDGET` | optional | Thinking tokens: `-1` dynamic, `0` off (default: `0`) |

---

## Usage

```bash
# Auto-detect phase by current time
python trading/runner.py

# Specific phase
python trading/runner.py --phase pre-market
python trading/runner.py --phase market
python trading/runner.py --phase post-market

# Specific date
python trading/runner.py 20260627 --phase pre-market

# Submit orders to broker app
python trading/runner.py --phase pre-market --submit

# Dry run (no mobile app operations)
python trading/runner.py --dry-run

# Legacy sync-only
python trading/runner.py --sync-only
```

---

## Error Handling & Retries

- Each data-extraction step retries **3 times** on failure, 5-second delays
- `parse_csv_data()` uses Levenshtein distance (≤ 3) for fuzzy CSV header matching
- All sync functions validate data before DB commit (`has_exceptions` flag)
- Orphaned DB records auto-deleted on each sync cycle
- Market-hours time guards prevent sync outside valid windows

---

## Crontab Scheduling

```cron
# Pre-market preparation
0 9 * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py --phase pre-market >> /tmp/cron_trading.log 2>&1

# Market-hours sync (every 30 min)
0,30 9-11,13-14 * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py >> /tmp/cron_trading.log 2>&1

# Post-market sync
0 16 * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py --phase post-market >> /tmp/cron_trading.log 2>&1
```

---

## Related Documentation

- [BACKTEST.md](BACKTEST.md) — Full backtest pipeline (7 phases)
- [ARCHITECTURE.md](../ARCHITECTURE.md) — Full system architecture
- [A_Share_Market_Rules.md](A_Share_Market_Rules.md) — Chinese A-Share trading rules
