# Backtest Module — iMobile A-Shares Quantitative Engine

The `backtest/` module is the **quantitative research and simulation** subsystem of iMobile. It implements a full China A-Share market backtesting framework: regime-aware strategy selection, hot-sector stock picking, smart order generation, T+1-compliant order execution, P&L reporting, and benchmark comparison.

---

## Table of Contents

- [Main Features](#main-features)
- [Module Structure](#module-structure)
- [Architecture & Workflow](#architecture--workflow)
- [Strategies](#strategies)
- [Market Regime Detection](#market-regime-detection)
- [Pre-Requirements](#pre-requirements)
- [Environment Variables](#environment-variables)
- [Configuration (`config.json`)](#configuration-configjson)
- [Usage](#usage)
- [Output Files](#output-files)
- [Analysis & Reporting](#analysis--reporting)
- [A-Shares Compliance](#a-shares-compliance)
- [Data Providers](#data-providers)
- [Key Classes & Functions](#key-classes--functions)
- [Logging](#logging)

---

## Main Features

| Feature | Description |
|---|---|
| **Regime-aware meta-strategy** | `ts_auto` detects the 20-day market regime and delegates to the best sub-strategy automatically |
| **Multiple stock-picking strategies** | 8 strategies covering AI-driven, technical, fundamental, and hot-sector approaches |
| **Smart order generation** | Computes buy price (ATR-based for bull, RSI/BB/support for others), take-profit, stop-loss, and position size per stock per day |
| **T+1 rule enforcement** | Buys today are not sellable until next trading day — fully modeled in backtest and live trading |
| **No short-selling** | Hard constraint: only long positions allowed |
| **Limit-up / limit-down modeling** | 10% main board, 20% ChiNext/STAR, 30% BSE; locked limit-down prevents selling |
| **Realistic fee simulation** | Commission 0.00341% (min ¥5) + stamp duty 0.05% on sells |
| **Portfolio daily reporting** | Per-day Markdown report with P&L, positions, transactions, benchmarks |
| **Period P&L report** | Summary report for the full backtest period vs SSE/CSI300/CSI500 (skipped for future/single-day pre-market runs) |
| **Result analyzer** | `result_backtest.py` produces monthly breakdown + index comparison from any results folder |
| **Multi-data-provider support** | Tushare (primary), AkShare (fallback), TDX; SQLite caching layer |
| **Trailing stop** | Optional trailing-stop calculation per order |
| **Pattern detection** | `ChinaMarketPatternDetector` classifies market into bull/normal/volatile/bear |
| **Benchmark comparison** | Strategy return vs SSE Composite, CSI300, CSI500, CSI1000 (historical dates only) |

---

## Module Structure

```
backtest/
├── engine.py               # Main entry point: pick → orders → execute → report
├── cli.py                  # CLI: run / pick / analyze / config commands
├── result_backtest.py      # Standalone result analyzer (monthly P&L + index table)
├── config.json             # Risk parameters, position sizing, strategy configs
├── config.json.example     # Template config
├── __init__.py             # Package init: data provider, calendar, config singletons
│
├── strategies/             # Stock-picking strategy scripts
│   ├── ts_auto.py          #   ★ Meta-strategy: regime → delegates to sub-strategy
│   ├── ts_7AZ.py           #   CANSLIM 7-factor fundamental screener
│   ├── ts_ths_dc.py        #   Hot-sector + channel breakout (ts_dc)
│   ├── ts_hma.py           #   Hull Moving Average + SuperTrend
│   ├── ts_longup.py        #   ADX trend-following
│   ├── ts_ai_pick.py       #   Full AI analysis (LLM + news/sentiment)
│   ├── ts_daily.py         #   News-driven daily picks (LLM + web search)
│   ├── ts_gb_line.py       #   Golden-cross / dead-cross line strategy
│   ├── ts_combine.py       #   Multi-strategy combiner
│   ├── picker.py           #   ASharesStockPicker base class
│   ├── manager.py          #   StrategyManager: register + dispatch strategies
│   ├── bull.py             #   Bull-market strategy config
│   ├── bear.py             #   Bear-market strategy config
│   ├── normal.py           #   Normal-market strategy config
│   └── volatile.py         #   Volatile-market strategy config
│
├── core/                   # Backtest engine internals
│   ├── backtest.py         #   ChinaASharesBacktest + ASharesBacktestWrapper
│   ├── strategy.py         #   ASharesStrategy base class
│   ├── interfaces.py       #   DataProvider / MarketPatternDetector / StockPicker ABCs
│   └── validator.py        #   TradeValidator: T+1 and short-selling rule enforcement
│
├── data/                   # Data provider layer
│   ├── provider.py         #   TushareDataProvider, AkshareDataProvider, TdxDataProvider
│   ├── provider_akshare.py #   AkShare-specific data fetching
│   ├── cache.py            #   In-memory + file cache
│   ├── sqlite_cache.py     #   SQLiteDataCache: persistent OHLCV + index caching
│   └── validator.py        #   Data integrity checks
│
├── analysis/               # Post-backtest analysis tools
│   ├── performance.py      #   PerformanceAnalyzer: Sharpe, drawdown, alpha, beta
│   ├── benchmark.py        #   Benchmark comparison (SSE, CSI300, CSI500, CSI1000)
│   ├── reporting.py        #   Report generation (Markdown, HTML, CSV)
│   ├── plots.py            #   Equity curve, drawdown, sector charts (Matplotlib/Plotly)
│   ├── pattern_analyzer.py #   Market pattern analysis tools
│   ├── pattern_detector.py #   ChinaMarketPatternDetector
│   └── indicators.py       #   TechnicalIndicators: RSI, Bollinger Bands, ATR, etc.
│
├── utils/                  # Shared utilities
│   ├── trading_calendar.py #   A-Share trading calendar (holiday-aware, pickle-cached)
│   ├── market_regime.py    #   detect_market_regime() using MA60/MA120 + volatility
│   ├── config.py           #   ConfigManager: JSON config loader
│   ├── basic_information.py#   Stock basic info cache (sector, industry, market)
│   ├── trailing_stop.py    #   Trailing stop calculation
│   ├── util.py             #   Date conversion, formatting helpers
│   ├── logging_config.py   #   Loguru setup
│   ├── exceptions.py       #   IBacktestError, TradeValidationError
│   └── proxy_config.py     #   Proxy rotation (for web scrapers)
│
├── cbs_ewo/                # CBS/EWO wave analysis (supplemental)
└── results/                # Backtest output directory (auto-created)
    └── <start>_<end>_<strategy>/
        ├── pick_stocks_YYYYMMDD.json
        ├── smart_orders_YYYYMMDD.json
        ├── report_orders_YYYYMMDD.md
        └── report_period_<start>_<end>.md
```

---

## Architecture & Workflow

### Core Pipeline

```
python backtest/engine.py <start_date> <end_date> [strategy]
         │
         ▼  For each trading day in range:
┌──────────────────────────────────────────────────┐
│  1. pick_stocks_to_file(date, src)               │
│     ├── detect_market_regime(date)               │  120-day MA60/MA120 + volatility
│     ├── ts_auto → determine_strategy()           │  20-day MA10 + momentum
│     ├── Delegate to sub-strategy script          │  writes /tmp/tmp
│     └── Write pick_stocks_YYYYMMDD.json          │
│                                                  │
│  2. create_smart_orders_from_picks()             │
│     ├── cli analyze --stocks-file ...            │  RSI + BB + ATR + regime TP/SL
│     │   └── Bull regime: buy_price = close ×     │  (1 + clamp(0.5×ATR/close, 2%, 7%/13%))
│     │       Other regimes: RSI/BB/support-based  │
│     ├── Add new buy orders to DB                 │
│     ├── Adjust existing orders (TP↑, SL stable) │
│     └── Force-sell expired orders                │
│                                                  │
│  3. OrderAnalyzer.generate_daily_report()        │  ← SKIPPED if date >= today
│     └── check_order_execution()                  │    (no OHLCV for future dates)
│         ├── execute_buy_order()  → DB insert     │  T+1: available_shares = 0 today
│         └── execute_sell_order() → DB insert     │  T+1: checks purchase_date < today
│                                                  │
│  4. (period end) generate_period_report()        │  ← SKIPPED if end_date >= today
│     ├── Read transactions table                  │    (no benchmark OHLCV for future)
│     ├── Read holding_stocks table                │
│     └── vs SSE / CSI300 / CSI500 benchmarks     │
└──────────────────────────────────────────────────┘
```

### Live Trading Integration

`engine.py` also exports `pick_orders_trading()` which is called by `trading/runner.py` during the pre-market phase for live trading.

---

## Strategies

### `ts_7AZ` — CANSLIM Strategy ✦ (Default)

Screens stocks across 7 dimensions (C-A-N-S-L-I-M) using Tushare API fundamentals + technicals. Pairs with regime-based risk management from `config.json`:

| Regime | TP | SL | Max Hold | 3/688 TP | 3/688 SL |
|--------|-----|-----|----------|-----------|----------|
| Bull | 25% | 5% | 15d | 35% | 15% |
| Normal | 15% | 4% | 10d | 25% | 14% |
| Volatile | 10% | 3% | 8d | 20% | 13% |
| Bear | 8% | 2% | 5d | 18% | 12% |

**Trailing SL:** SL = `current_price × (1 - buffer%)`, resets on each re-pick. Narrow enough to cut losers fast (fail-fast dynamic), wide enough to avoid noise on normal intraday volatility. 3/688 stocks get +10pp boost for their 20% daily limit.

> **Proven performance**: 185.58% total return over 343 days (2025-01 to 2026-06) with 19/21 winning months.

### `ts_auto` — Meta-Strategy

Analyzes 20 trading days of SSE Composite (000001.SH) data to classify the regime, then delegates to the best sub-strategy:

| Condition | Strategy Selected |
|---|--|
| `momentum > 4%` AND `volatility < 1.5%` AND `price > MA10` | `ts_longup` (trend-following) |
| `momentum < -8%` AND `volatility > 2.5%` | `ts_hma` (reversal) |
| Everything else (default) | `ts_7AZ` (CANSLIM) |

### Strategy Comparison

| Strategy | Type | Description | Best In |
|---|---|---|---|
| `ts_7AZ` | ✦ Default | CANSLIM 7-factor screener with regime-based TP/SL step-ladder | Normal/moderate |
| `ts_auto` | Meta | Regime-aware auto-selector | All conditions |
| `ts_ths_dc` / `ts_dc` | Technical | Hot-sector channel breakout (THS sector heat + Donchian) | Bull/normal markets |
| `ts_hma` | Technical | Hull Moving Average + SuperTrend reversal detection | Sharp bear markets |
| `ts_longup` | Technical | ADX + slope-based trend-following | Strong bull markets |
| `ts_ai_pick` | AI | Full AI analysis: LLM + web news + sentiment | Any (requires API) |
| `ts_daily` | AI | Daily LLM + web search news-driven picks | Any (requires API) |
| `ts_gb_line` | Technical | Golden-cross / dead-cross signals | Trending markets |
| `ts_go` | Technical | Go-language bulk screener | Speed-optimized runs |

---

## Market Regime Detection

`backtest/utils/market_regime.py` → `detect_market_regime(date)`

Uses 120 trading days (~6 months) of SSE Composite data:

| Regime | Condition | TP% | SL% | Max Hold Days |
|---|---|---|---|---|
| `bull` | price > MA60 > MA120, volatility < 2% | 200% | 0.5% | 15 |
| `bear` | price < MA60 < MA120 | 200% | 0.5% | 5 |
| `volatile` | daily volatility > 3% | 200% | 0.5% | 8 |
| `normal` | default | 200% | 0.5% | 10 |

The regime config is loaded from `config.json` under `trading_rules.risk_reward_ratios`.

---

## Pre-Requirements

### Python Environment

```bash
cd ~/apps/imobile
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Key packages for the backtest module:

| Package | Purpose |
|---|---|
| `tushare` | Primary stock/index data provider |
| `akshare` | Alternative/fallback data provider |
| `backtesting` | Core backtesting.py framework |
| `pandas` / `numpy` | Data manipulation |
| `ta` | Technical indicators library |
| `loguru` | Structured logging |
| `python-dotenv` | `.env` loading |
| `matplotlib` / `seaborn` / `plotly` | Chart generation |
| `google-genai` / `evoagentx` | AI-based strategies (optional) |

### Tushare Account

The default data provider is **Tushare**. A Tushare token with sufficient points is required for most market data APIs.

1. Register at [tushare.pro](https://tushare.pro)
2. Get your token from the user center
3. Set `TUSHARE_TOKEN` in `.env`

> Minimum recommended: 2000 Tushare points (covers daily OHLCV, index, basic info).

### AkShare (Alternative)

To use AkShare instead of Tushare (no token required), set in `config.json`:

```json
{ "init_info": { "data_provider": "akshare" } }
```

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TUSHARE_TOKEN` | ✅ (Tushare) | Tushare API token |
| `GOOGLE_API_KEY` / `GEMINI_API_KEY` | AI strategies only | For ts_ai_pick, ts_daily |
| `CONFIG_FILE` | optional | Path to config JSON (default: `./backtest/config.json`) |
| `BACKTEST_PATH` | optional | Backtest root path (default: `./backtest`) |
| `REPORT_PATH` | optional | Results output dir (default: `./backtest/results`) | Daily output → `results/daily/` |
| `LOG_LEVEL` | optional | `DEBUG` / `INFO` / `WARNING` (default: `INFO`) |
| `LOG_PATH` | optional | Log directory (default: `./logs`) |
| `CACHE_PATH` | optional | Cache directory (default: `./data_cache`) |
| `CAL_PICKLE_FILE` | optional | Trading calendar pickle path |
| `BASIC_INFO_PICKLE_FILE` | optional | Stock basic info pickle path |
| `DB_CACHE_FILE` | optional | SQLite data cache path |
| `DB_IMOBILE_FILE` | optional | Main imobile SQLite DB path |

---

## Configuration (`config.json`)

The file at `backtest/config.json` controls all runtime parameters. Key sections:

### `init_info`

```json
{
  "data_provider": "tushare",
  "initial_cash": 600000,
  "commission": 0.00341,
  "tax": 0.005
}
```

- `initial_cash`: Starting capital in CNY (default ¥600,000)
- `commission`: 0.00341% (typical broker rate, min ¥5 applied in code)
- `tax`: 0.5% stamp duty on sells only

### `trading_rules.risk_reward_ratios`

Per-regime take-profit, stop-loss, and holding period:

```json
{
  "bull_market":    { "take_profit_pct": 2.0, "stop_loss_pct": 0.005, "max_hold_days": 15 },
  "normal_market":  { "take_profit_pct": 2.0, "stop_loss_pct": 0.005, "max_hold_days": 10 },
  "volatile_market":{ "take_profit_pct": 2.0, "stop_loss_pct": 0.005, "max_hold_days": 8  },
  "bear_market":    { "take_profit_pct": 2.0, "stop_loss_pct": 0.005, "max_hold_days": 5  }
}
```

### `trading_rules.position_sizing`

```json
{
  "max_positions": 20,
  "rank_weighted": true,
  "top_3_weight": 0.15,
  "mid_4_weight": 0.10,
  "bottom_weight": 0.0875
}
```

### `benchmarks` (in `reporting`)

Defaults: SSE Composite, CSI300, CSI500, CSI1000.

---

## Usage

### Run a Full Backtest

```bash
# Default: ts_7AZ CANSLIM strategy, today's date range
python backtest/engine.py 20250101 20250612

# Specify strategy
python backtest/engine.py 20250101 20250612 ts_7AZ
python backtest/engine.py 20250101 20250612 ts_auto
python backtest/engine.py 20250101 20250612 ts_dc

# Disable AI/search (pure technical, faster)
python backtest/engine.py 20250101 20250612 ts_7AZ 1 false false

# Specify user_id and flags
# python backtest/engine.py <start> <end> <strategy> <user_id> <search> <ai>
python backtest/engine.py 20250101 20250612 ts_daily 1 false true
```

### CLI Interface

```bash
# Run backtest (YYYY-MM-DD format)
python -m backtest.cli run --start-date 2025-01-01 --end-date 2025-06-12

# Run with specific strategy
python -m backtest.cli run --start-date 2025-01-01 --end-date 2025-06-12 --strategy bull_market

# Pick stocks for the next trading day
python -m backtest.cli pick
python -m backtest.cli pick --date 2025-06-15

# Analyze picked stocks and generate smart orders
python -m backtest.cli analyze --stocks-file backtest/results/pick_stocks_20250615.json \
  -o backtest/results/smart_orders_20250615.json

# Create default config
python -m backtest.cli config create -o my_config.json

# Validate config
python -m backtest.cli config validate backtest/config.json

# Show version
python -m backtest.cli version
```

### Analyze Results

```bash
# All result directories under backtest/results/
python backtest/result_backtest.py

# Specific result directory
python backtest/result_backtest.py backtest/results/20250101_20250612_ts_7AZ
```

### Resume an Interrupted Backtest

```bash
# Use --resume flag (picks up from last completed date)
python backtest/engine.py 20250101 20250612 ts_7AZ 1 true true --resume
```

---

## Output Files

All outputs go to `REPORT_PATH` (default `backtest/results/<start>_<end>_<strategy>/`):

| File | Description |
|---|---|
| `pick_stocks_YYYYMMDD.json` | Picked stock list with regime data |
| `smart_orders_YYYYMMDD.json` | Smart orders with buy/TP/SL prices and quantities |
| `report_orders_YYYYMMDD.md` | Daily P&L report: positions, transactions, portfolio value |
| `report_period_<start>_<end>.md` | Full-period report: total return vs benchmarks |

### `pick_stocks_YYYYMMDD.json` Schema

```json
{
  "pick_date": "2025-06-15 09:00:00",
  "base_date": "20250614",
  "target_trading_date": "20250615",
  "market_pattern": "normal",
  "regime_data": { "regime": "normal", "max_hold_days": 10, ... },
  "selected_stocks": [
    { "symbol": "000970", "name": "中科三环", "score": 0.82 },
    ...
  ]
}
```

### `smart_orders_YYYYMMDD.json` Schema

```json
{
  "target_trading_date": "20250615",
  "market_pattern": "normal",
  "regime_data": { ... },
  "smart_orders": [
    {
      "symbol": "000970",
      "name": "中科三环",
      "current_price": 14.17,
      "buy_price": 13.90,
      "sell_take_profit_price": 41.70,
      "sell_stop_loss_price": 13.83,
      "buy_quantity": 4000
    }
  ]
}
```

---

## Analysis & Reporting

### `result_backtest.py` — Monthly Analyzer

Reads `report_orders_*.md` files from a results directory and prints:
- Month-by-month: Start Value, End Value, Return%, Realized P&L, Unrealized P&L, Total P&L
- Overall: Initial → Final → Return% vs SSE / CSI300 / CSI500

```
======================================================================
Backtest: 20250101 → 20260612  |  Strategy: ts_7AZ (default)
Index:  SSE 21.58%  |  CSI300 24.33%  |  CSI500 -0.31%  |  Strategy 185.58%
======================================================================
Month    Start Value     End Value  Return%     Realized   Unrealized  Total P&L
----------------------------------------------------------------------
202501   ¥600,000    ¥643,210     7.20%   ¥28,000    ¥15,210   ¥43,210
...
```

### `analysis/performance.py` — PerformanceAnalyzer

- Total return, annualized return
- Sharpe ratio, Sortino ratio
- Maximum drawdown, drawdown duration
- Win rate, average win/loss
- Alpha, Beta vs benchmark

### `analysis/benchmark.py` — Benchmark Comparison

Compares strategy equity curve against:
- SSE Composite (000001.SH)
- CSI 300 (000300.SH)
- CSI 500 (000905.SH)
- CSI 1000 (000852.SH)
- CSIA 500 (000510.SH)

### `analysis/plots.py` — Chart Generation

- Equity curve (strategy vs benchmarks)
- Drawdown chart
- Monthly returns heatmap
- Sector exposure chart

---

## A-Shares Compliance

The backtesting engine strictly enforces Chinese A-Share market rules:

### T+1 Settlement

- Shares bought today have `available_shares = 0` until next trading day
- `execute_buy_order()` inserts holding with `available_shares = 0`
- `update_available_shares_for_new_day()` runs at the start of each day to release yesterday's purchases
- `check_order_execution()` queries `MIN(transaction_date)` from transactions to get the true purchase date before allowing a sell

### Price Limit Rules

| Board | Daily Limit | Code Prefix |
|---|---|---|
| Main Board (SSE/SZSE) | ±10% | 600x, 601x, 603x, 000x, 001x, 002x |
| ChiNext (创业板) | ±20% | 300x, 301x |
| STAR Market (科创板) | ±20% | 688x |
| Beijing SE (北交所) | ±30% | 4x, 8x |
| ST stocks | ±5% | ST prefix |

- **Limit-down lock**: If `high_price <= limit_down_price` all day, sell is blocked
- **Limit-up filter**: Stocks that open limit-up are not bought (can't fill)

### No Short Selling

- `TradeValidator` rejects any sell orders for stocks not in holdings
- `ASharesStrategy` base class enforces this in `validate_trade()`

### Transaction Costs

```python
# Buy
commission = max(price * quantity * 0.0000341, 5.0)  # min ¥5
net_cost   = price * quantity + commission

# Sell
commission = max(price * quantity * 0.0000341, 5.0)  # min ¥5
tax        = price * quantity * 0.0005                # stamp duty
net_proceeds = price * quantity - commission - tax
```

### Backtest vs. Real-World Trading Gaps

While the backtesting engine is designed to mirror real market conditions closely, there are a few important gaps:

1. **STAR / ChiNext Minimum Quantity Rule**:
   - *Real Market*: In Chinese A-shares, stocks starting with `30` (ChiNext) or `688` (STAR Market) have a minimum purchase quantity of **200 shares** (and increments of 1 share after that).
   - *Backtest*: Unconditionally rounds buy quantities down to the nearest **100 shares** and allows a minimum purchase of 100 shares for STAR/ChiNext stocks. This would be rejected by a real broker.
2. **Order Execution & Slippage**:
   - *Real Market*: Intraday prices fluctuate continuously. Orders are executed using brokerage APIs or app interfaces (e.g. `即时买一价`) subject to queue delays and slippage.
   - *Backtest*: Fills buy orders exactly at the **open price** (assuming the stock didn't open at limit-up) and sells exactly at the **trigger price** (TP/SL) if hit, or at the open/close for cuts/expiry.
3. **Double Trigger Scenario**:
   - *Real Market*: If a stock's intraday high hits the TP and low hits the SL on the same day, the one that occurs first chronologically triggers.
   - *Backtest*: If both are hit, the backtest defaults to assuming the TP was hit first, which can slightly overestimate performance on highly volatile days.
4. **Market Session Automation**:
   - *Real Market*: Execution in live trading is delegated to the Guotai broker app (smart orders trigger natively on their servers).
   - *Backtest*: The market session runner in `trading/runner.py` is currently a `TODO` stub, meaning the simulation does not execute real-time market actions via ADB during the session. Instead, the pre-market phase creates/uploads smart orders, and the post-market phase syncs results.

---

## Data Providers

### Tushare (Primary / Default)

```python
# Configured in config.json: "data_provider": "tushare"
# Requires: TUSHARE_TOKEN in .env
```

Provides:
- `get_stock_data(symbol, start, end)` — daily OHLCV + pre_close
- `get_index_data(index_code, start, end)` — index OHLCV; **Tushare is tried first**, AkShare is the fallback
- `get_trading_calendar(start, end)` — A-Share trading days

### AkShare (Fallback)

```python
# Used automatically when Tushare index fetch fails or returns < 20 rows.
# Also configurable as primary: "data_provider": "akshare" in config.json
# No API key required
```

### SQLite Data Cache

All API responses are cached in `DB_CACHE_FILE` (`./shared/db/db_cache.db`) via `SQLiteDataCache` with configurable TTL (default: 1 year for historical data).

### Trading Calendar

`backtest/utils/trading_calendar.py` — `TradingCalendar`:
- Fetches A-Share trading days from Tushare
- Persisted to pickle (`CAL_PICKLE_FILE`) and refreshed daily
- Key functions: `is_trading_day()`, `get_trading_days_before()`, `get_trading_days_after()`, `count_trading_days_between()`

---

## Key Classes & Functions

### `engine.py`

```python
# Top-level orchestrator
pick_orders_trading(
    start_date='20250101',
    end_date='20250612',
    user_id=1,
    src='ts_7AZ',             # Default CANSLIM strategy
    backtest_search=True,
    backtest_ai=True,
    resume=False
)

# Step 1: pick stocks for one day
pick_stocks_to_file(this_date, src='ts_7AZ') -> str  # path to pick JSON (default CANSLIM)

# Step 2: generate smart orders
create_smart_orders_from_picks(pick_input_file, user_id=1) -> str  # path to orders JSON

# Step 3a: execute buy
execute_buy_order(user_id, symbol, name, buy_price, quantity, take_profit, stop_loss,
                  transaction_date, order_number, holding_days=4) -> bool

# Step 3b: execute sell
execute_sell_order(user_id, symbol, name, sell_price, quantity,
                   transaction_date, order_number, reason='take_profit') -> bool

# T+1: release yesterday's shares at start of day
update_available_shares_for_new_day(date, user_id=1) -> int
```

### `OrderAnalyzer` (in `engine.py`)

```python
analyzer = OrderAnalyzer(smart_orders_file, user_id=1)
analyzer.generate_daily_report(date)   # Check + execute orders for one day
analyzer.generate_period_report()       # Full-period summary vs benchmarks
```

### `ts_auto.determine_strategy(date_str)` (in `strategies/ts_auto.py`)

```python
strategy = determine_strategy('20250615')  # Returns 'ts_7AZ', 'ts_longup', or 'ts_hma'
```

### `detect_market_regime(date)` (in `utils/market_regime.py`)

```python
regime_data = detect_market_regime('20250615')
# Returns: {'regime': 'normal', 'take_profit_pct': 2.0, 'stop_loss_pct': 0.005, 'max_hold_days': 10, ...}
```

### `ASharesBacktestWrapper` (in `core/backtest.py`)

```python
wrapper = ASharesBacktestWrapper(data_provider, strategy_manager, pattern_detector, stock_picker)
results = wrapper.run_portfolio_backtest(
    start_date='2025-01-01',
    end_date='2025-06-12',
    initial_cash=600000,
    commission=0.0000341,
    max_positions=20
)
```

### `TechnicalIndicators` (in `analysis/indicators.py`)

```python
rsi    = TechnicalIndicators.rsi(close_series)
upper, middle, lower = TechnicalIndicators.bollinger_bands(close_series)
atr    = TechnicalIndicators.average_true_range(high, low, close)
```

---

## Logging

Logging uses `loguru` configured in `utils/logging_config.py`:

```bash
LOG_LEVEL=INFO   # .env
LOG_PATH=./logs  # .env
```

Each backtest run writes timestamped logs to `LOG_PATH/`. Cron output redirects to `/tmp/cron_trading.log`.

---

## Related Documentation

- [README.md](../README.md) — Project overview & quick start
- [ARCHITECTURE.md](../ARCHITECTURE.md) — Full system architecture
- [TRADING.md](TRADING.md) — Live trading module (uses backtest engine for pre-market orders)
- [A_Share_Market_Rules.md](A_Share_Market_Rules.md) — Chinese A-Share rules: T+1, circuit breakers, board classifications
- [DATA_FLOW_DIAGRAM.txt](DATA_FLOW_DIAGRAM.txt) — Full data flow between subsystems
- [analyze_backtest.md](analyze_backtest.md) — Notes on reading backtest result reports
- [TODO.md](TODO.md) — Planned improvements
