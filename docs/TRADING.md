# Trading Module — iMobile A-Shares Automated Trading

The `trading/` module is the **live-trading** subsystem of iMobile. It automates Chinese A-Share market operations by driving the [国泰海通君弘 (Guotai Junan)](https://www.gtja.com/) broker Android app via ADB + AI vision agents, and syncs real-time market data into the local SQLite database for the web dashboard.

---

## Table of Contents

- [Main Features](#main-features)
- [Module Structure](#module-structure)
- [Architecture](#architecture)
- [Pre-Requirements](#pre-requirements)
- [Environment Variables](#environment-variables)
- [Usage](#usage)
- [Trading Phases](#trading-phases)
- [Crontab Scheduling](#crontab-scheduling)
- [Data Sync Pipeline](#data-sync-pipeline)
- [Trajectory Replay](#trajectory-replay)
- [Key Classes & Functions](#key-classes--functions)
- [Error Handling & Retries](#error-handling--retries)
- [Logging](#logging)

---

## Main Features

| Feature | Description |
|---|---|
| **Mobile ADB automation** | Controls the Guotai Android app on a real device or emulator via ADB + DroidRun Portal |
| **AI vision navigation** | Uses Gemini LLM (vision-enabled) to navigate UI, read on-screen data, and fill forms |
| **Trajectory replay** | Pre-recorded tap sequences replay for fast, deterministic navigation; falls back to live agent on miss |
| **Real-time data extraction** | Extracts market indices, portfolio positions, and smart orders from the broker app |
| **DB sync** | Syncs extracted data (quotes, positions, orders) into SQLite (`imobile.db`) for the Reflex dashboard |
| **Daily trading phases** | Structured pre-market / market / post-market workflow with cron scheduling |
| **Stock picking integration** | Calls `backtest/engine.py` (`pick_orders_trading`) in pre-market to generate smart orders |
| **Structured data models** | Pydantic models for all extracted data (transactions, positions, quotes, orders) |

---

## Module Structure

```
trading/
├── runner.py        # CLI entry point — orchestrates all trading phases
├── guotai.py        # Guotai broker app integration: navigation, extraction, DB sync
├── adb.py           # ADB device connectivity & app-existence checks
├── extractors.py    # Pydantic data models + AppDataExtractor base class
└── trajectory/      # Saved DroidRun tap-sequence recordings for replay
    ├── 20260615_154834_2a088f21/
    └── 20260615_162511_e92450d5/
```

### File Responsibilities

#### `runner.py` — Main CLI Orchestrator

- Entry point: `python trading/runner.py`
- Parses CLI arguments (`date`, `--phase`, `--user-id`, `--dry-run`, `--sync-only`)
- Checks trading calendar; skips non-trading days
- Calls `run_daily_trading()` for the requested phase
- Cleans up empty trajectory directories on startup

#### `guotai.py` — Broker App Integration

Core module (~1 073 lines). Contains:

- **App lifecycle**: `open_app()`, `close_app()`, `pre_requirements()`
- **Navigation agents**: AI agents that tap through the app UI
- **Data extraction**: `get_index_stock_from_app_quote_page()`, `get_summary_position_from_app_position_page()`, `get_order_from_app_smart_order_page()`
- **DB sync**: `sync_index_quote_data_to_db()`, `sync_summary_position_data_to_db()`, `sync_order_data_to_db()`
- **Cron job**: `cron_sync_app_data_to_db()` — full sync pipeline with market-hours guard
- **`GuotaiExtractor`** class — structured data extractor using Pydantic output models

#### `adb.py` — ADB Utilities

- `get_device_serials()` — list connected ADB devices
- `get_device_connectivity()` — verify device + DroidRun Portal reachability
- `check_app_exist()` — assert the broker app package is installed

#### `extractors.py` — Data Models

Pydantic models returned by the MobileAgent:

| Model | Fields |
|---|---|
| `ExtractTransaction` | name, transaction_date, price, quantity, transaction_type, amount |
| `ExtractOrder` | name, code, trigger_condition, commission_method, buy_or_sell_quantity, valid_until, order_number, reason_of_ending |
| `ExtractQuote` | indices `[{name, number, ratio}]`, stocks `[{name, code, latest_price, …}]` |
| `ExtractPosition` | floating_profit_loss, account_assets, market_cap, positions_pct, available, desirable, holdings list |

---

## Architecture

```
Android Device / Genymotion Emulator
        │  USB / TCP ADB
        ▼
   ADB (adb.py)
        │  AndroidDriver (mobilerun)
        ▼
DroidRun Portal (accessibility service on device)
        │  tap / swipe / screenshot commands
        ▼
 MobileAgent + Gemini Vision (guotai.py)
        │  AI reads screen, navigates, extracts CSV data
        ▼
  Data Parsing & Validation (guotai.py)
        │  parse_csv_data / normalize / validate
        ▼
  SQLite DB (shared/db/imobile.db)
        │  market_indices, holding_stocks, summary_account, smart_orders
        ▼
  Reflex Web Dashboard (web/)
```

The agent follows a **Goal → Planning → Execution → Reflection** loop (up to 60 steps). Vision is enabled (`ExecutorConfig(vision=True)`) so the model can read Chinese text and UI elements from screenshots.

---

## Pre-Requirements

### 1. Python Environment

```bash
cd ~/apps/imobile
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Key packages used by the trading module:

| Package | Purpose |
|---|---|
| `mobilerun` (DroidRun fork) | Android driver + MobileAgent orchestration |
| `llama_index.llms.google_genai` | Gemini LLM interface for MobileAgent |
| `python-dotenv` | `.env` loading |
| `loguru` | Structured logging |
| `Levenshtein` | Fuzzy CSV header matching |
| `pydantic` | Structured output models |

> **Note:** `mobilerun` / DroidRun with Google provider must be installed separately:
> ```bash
> pip install droidrun[google] -e utils/droidrun/.
> ```

### 2. Android Device or Emulator

The trading module requires an Android device (physical or emulator, e.g. Genymotion) connected via ADB.

```bash
# Verify ADB sees the device
adb devices

# Verify DroidRun Portal connectivity
droidrun ping
```

**DroidRun Portal setup on the device:**

1. Install DroidRun Portal APK: `droidrun setup`
2. Enable accessibility service: **Settings → Accessibility → DroidRun Portal → Enable**
3. Confirm: `droidrun ping`

### 3. Broker App Installed

The 国泰海通君弘 app (`com.guotai.dazhihui`) must be installed on the device.

```bash
# Verify app is present
adb shell pm list packages | grep guotai
```

### 4. `.env` Configuration

Copy and populate the environment file (see [Environment Variables](#environment-variables) below).

```bash
cp .env.example .env
# Edit .env
```

---

## Environment Variables

The following variables are **required** for the trading module:

| Variable | Required | Description |
|---|---|---|
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | ✅ | Gemini API key from [Google AI Studio](https://aistudio.google.com/app/apikey) |
| `GEMINI_MODEL` | ✅ | Model to use (default: `gemini-3.1-flash-lite-preview`) |
| `GUOTAI_PACKAGE_NAME` | ✅ | Android package: `com.guotai.dazhihui` |
| `GUOTAI_PASSWORD` | ✅ | Trading account PIN (6 digits) |
| `DB_IMOBILE_FILE` | ✅ | Path to SQLite database (default: `./shared/db/imobile.db`) |
| `LOG_LEVEL` | optional | `DEBUG` / `INFO` / `WARNING` / `ERROR` (default: `DEBUG`) |
| `LOG_PATH` | optional | Log output directory (default: `/tmp/ibacktest_logs`) |
| `GEMINI_THINKING_BUDGET` | optional | Thinking tokens: `-1` dynamic, `0` off (default: `0`) |

Additional variables used indirectly (Tushare, search APIs, etc.) are documented in `.env`.

---

## Usage

### Run with Auto Phase Detection

```bash
python trading/runner.py
```

Detects the current time and automatically picks the appropriate phase.

### Run a Specific Phase

```bash
# Pre-market: stock picking + smart order generation
python trading/runner.py --phase pre-market

# Market: monitor and execute pending smart orders
python trading/runner.py --phase market

# Post-market: sync mobile app data to DB
python trading/runner.py --phase post-market

# Run all phases sequentially
python trading/runner.py --phase all
```

### Specify a Trading Date

```bash
python trading/runner.py 20260214 --phase pre-market
```

### Dry Run (no mobile app operations)

```bash
python trading/runner.py --dry-run
```

### Legacy Sync-Only Mode

```bash
# Only sync data from mobile app, no trading phases
python trading/runner.py --sync-only
```

### Run the Cron Sync Directly

```bash
# Sync all data (quote, position, orders) from app to DB; no trading-time check
python trading/guotai.py
```

This calls `cron_sync_app_data_to_db(check_trading_day_and_time=False)` directly.

---

## Trading Phases

### `pre-market`

Runs before the market opens (typically ~09:00):

1. Calls `pick_orders_trading()` from `backtest/engine.py` with `src='ts_7AZ'` (default CANSLIM strategy)
2. Screens stocks across 7 CANSLIM factors with regime-based TP/SL from `config.json`
3. Generates smart orders via `cli analyze`:
   - **Bull regime:** `buy_price = close × (1 + clamp(0.5×ATR/close, min=2%, max=7%/13%))` — set above yesterday's close so the `股价 ≤ buy_price(触发买入)` trigger fires on a typical gap-up open without chasing near-limit-up prices. ChiNext (300) / STAR (688) use 13% cap; main board uses 7%.
   - **Other regimes (bear/normal/volatile):** RSI / Bollinger Band / recent-support-based entry (conservative)
4. Writes orders to DB (`smart_orders` table) and daily output to `backtest/results/daily/`
5. Steps 3 (daily execution report) and 4 (period benchmark report) are **skipped automatically** when the target date is today or in the future — those steps require historical OHLCV data that doesn't exist yet for a future trading date.

### `market`

During trading session (09:30–11:30, 13:00–15:00):

- Monitors and executes pending smart orders. Note that the live market session execution via ADB in `trading/runner.py` is currently a **`TODO` stub**. Instead, the system relies on the Guotai Junan broker app's native server-side trigger system: smart orders generated during `pre-market` are uploaded/synced to the app, and the broker's servers handle the live execution.
- *A-Share Rule Difference*: The backtesting system does not enforce the real-market requirement that ChiNext (`30xxx`) and STAR Market (`688xxx`) stocks have a minimum buy quantity of **200 shares** (it rounds down to 100 shares unconditionally). Ensure your real-world sizing rules enforce the 200-shares floor to prevent order submission failures on the broker app.

### `post-market`

After market close:

1. Runs `cron_sync_app_data_to_db(check_trading_day_and_time=False)`
2. Opens the broker app via ADB
3. Navigates to 我的持仓 (My Holdings)
4. Extracts and syncs: market quotes, portfolio positions, smart orders
5. Updates `market_indices`, `holding_stocks`, `summary_account`, `smart_orders` tables

---

## Crontab Scheduling

Add to `crontab -e` to automate trading on weekdays during market hours:

```cron
# Pre-market preparation (before open)
0 9 * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py --phase pre-market >> /tmp/cron_trading.log 2>&1 &

# Market-hours data sync (every 30 min during session)
30 9              * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py >> /tmp/cron_trading.log 2>&1 &
0,30 10-11        * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py >> /tmp/cron_trading.log 2>&1 &
0,30 13-14        * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py >> /tmp/cron_trading.log 2>&1 &
0,30 15           * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py >> /tmp/cron_trading.log 2>&1 &

# Post-market sync (1 hour after close)
0 16              * * 1-5 cd $HOME/apps/imobile && source .venv/bin/activate && python trading/runner.py --phase post-market >> /tmp/cron_trading.log 2>&1 &
```

The `cron_sync_app_data_to_db()` function includes built-in time guards:
- Skips if today is not a trading day
- Skips before 09:30 or during lunch break (11:30–13:00)
- Skips more than 1 hour after market close (after 16:00)

---

## Data Sync Pipeline

Each sync cycle extracts three independent data sets from the app:

```
App Screen → MobileAgent (Gemini Vision)
                │
                ▼  CSV text output
         get_index_stock_from_app_quote_page()
                │  "index_name,index_number,index_ratio\n...\n\nname,code,price,..."
                ▼
         sync_index_quote_data_to_db()
                │  Upserts market_indices + holding_stocks; removes orphans
                ▼
         SQLite: market_indices, holding_stocks (price/change columns)

         get_summary_position_from_app_position_page()
                │  "floating_profit_loss,account_assets,...\n\nname,market_cap,..."
                ▼
         sync_summary_position_data_to_db()
                │  Upserts summary_account + holding_stocks (position columns)
                ▼
         SQLite: summary_account, holding_stocks (position/PnL columns)

         get_order_from_app_smart_order_page()
                │  "name,code,trigger_condition,...\n..."
                ▼
         sync_order_data_to_db()
                │  Upserts smart_orders; removes orphans
                ▼
         SQLite: smart_orders
```

Each step retries up to **3 times** on failure before aborting.

### DB Tables Written

| Table | Written by | Key data |
|---|---|---|
| `market_indices` | `sync_index_quote_data_to_db` | index_code, current_value, change_percent |
| `holding_stocks` | both quote & position sync | code, name, current_price, market_value, pnl_float, cost_basis |
| `summary_account` | `sync_summary_position_data_to_db` | total_assets, total_market_value, cash, floating_pnl |
| `smart_orders` | `sync_order_data_to_db` | trigger_condition, quantity, valid_until, reason_of_ending |

---

## Trajectory Replay

To avoid running the full AI agent every time (slower, costs API calls), the system can replay pre-recorded navigation sequences:

```python
# Replays a trajectory matching keywords in its description
replay_page(description=['行情', '我的持仓'])
```

### Record a New Trajectory

```bash
mobilerun run "Open '国泰海通君弘', then tap '行情', then tap '我的持仓'" \
  --provider GoogleGenAI \
  --model gemini-3.1-flash-lite-preview \
  --save-trajectory step
```

Trajectories are stored in `trading/trajectory/`. The replay function searches available trajectories by keyword and falls back to the live agent if no match is found.

---

## Key Classes & Functions

### `GuotaiExtractor` (in `guotai.py`)

Extends `AppDataExtractor`. Provides structured async methods:

```python
extractor = GuotaiExtractor(config=config, llm=llm, driver=driver)

await extractor.open_app_login()          # Open app + login via trajectory replay
await extractor.get_quotes()              # → ExtractQuote
await extractor.get_positions()           # → ExtractPosition
await extractor.get_transactions()        # → ExtractTransaction
extractor.goto_homepage()                 # ADB force-stop + re-open app
```

### `cron_sync_app_data_to_db()` (in `guotai.py`)

Full sync pipeline:

```python
result = await cron_sync_app_data_to_db(check_trading_day_and_time=True)
# Returns: {quote_sync_result, position_sync_result, order_sync_result}
```

### `pre_requirements()` (in `guotai.py`)

Initialises ADB connection + LLM + MobileConfig:

```python
tools, llm, config = await pre_requirements()
```

MobileAgent is configured with:
- `max_steps=60` (reflects up to 60 UI actions per task)
- `reasoning=True` (planning mode)
- `vision=True` (screenshot-based navigation)
- `after_sleep_action=1.5` seconds between actions

---

## Error Handling & Retries

- Each data-extraction step retries up to **3 times** on failure, with 5-second delays
- `get_format_output()` uses Levenshtein distance (≤ 3) for fuzzy CSV header matching, tolerating minor LLM formatting variations
- All sync functions use `has_exceptions` flag with explicit validation before committing to DB
- Orphaned DB records (present in DB but absent from latest app data) are **automatically deleted** on each sync cycle

---

## Logging

Logging is configured via `backtest/utils/logging_config.py`:

```bash
LOG_LEVEL=INFO    # .env
LOG_PATH=./logs   # .env
```

Cron output redirects to `/tmp/cron_trading.log`. Use `loguru` structured logs for production monitoring.

---

## Related Documentation

- [README.md](../README.md) — Project overview & quick start
- [ARCHITECTURE.md](../ARCHITECTURE.md) — Full system architecture
- [A_Share_Market_Rules.md](A_Share_Market_Rules.md) — Chinese A-Share trading rules (T+1, circuit breakers, etc.)
- [DATA_FLOW_DIAGRAM.txt](DATA_FLOW_DIAGRAM.txt) — Full data flow between subsystems
- [TODO.md](TODO.md) — Planned features and known gaps
