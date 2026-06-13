# iMobile — A-Shares Automated Trading System

Backtest engine, live trading orchestrator, mobile app data sync, and portfolio web UI for the Chinese A-Shares market.

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Initialize database
sqlite3 imobile.db < db/imobile.sql

# Run a backtest (3 months, regime-aware strategy selection)
python backtest_orders.py 20250101 20250331 ts_auto

# View monthly performance
python utils/result_ts_auto.py

# Live trading (requires Android emulator + DroidRun)
python app_trading.py 20260214 --phase pre-market
```

## Three Subsystems

### 1. Backtest Engine (`backtest_orders.py`)

Historical simulation with T+1 compliance, realistic execution, and multi-strategy support.

```bash
# Full backtest with resume support
python backtest_orders.py 20260101 20260605 ts_auto 1 false true resume
#                           start    end      strategy     user search ai  resume
```

**Key strategies:**

| Strategy | Type | Description |
|----------|------|-------------|
| `ts_auto` | ✦ Meta | Auto-selects best sub-strategy based on 20-day regime |
| `ts_7AZ` | Fundamental | CANSLIM 7-factor (C-A-N-S-L-I-M) quality screener |
| `ts_daily` | AI | News-driven daily picks (LLM + web search) |
| `ts_ai_pick` | AI | Full AI analysis with news/sentiment |
| `ts_dc` | Tech | Hot-sector channel breakout |
| `ts_hma` | Tech | Hull Moving Average + SuperTrend |
| `ts_longup` | Tech | ADX trend-following |
| `ts_go` | Tech | Go-based bulk technical screener |

See [ARCHITECTURE.md](ARCHITECTURE.md) for detailed strategy comparison and regime detection logic.

### 2. Live Trading (`app_trading.py`)

Daily trading workflow orchestrator:

```bash
python app_trading.py [date] --phase pre-market   # Pick stocks, create orders
python app_trading.py [date] --phase market        # Execute orders during session
python app_trading.py [date] --phase post-market   # Reports + mobile data sync
python app_trading.py [date] --phase all           # Full day pipeline
python app_trading.py --sync-only                  # Just sync mobile app data
```

### 3. Web Portfolio (`imobile/`)

Reflex-based web UI for portfolio tracking:

```bash
reflex run          # Start dev server at http://localhost:3000
```

## Project Structure

```
imobile/
├── backtest_orders.py      Main backtest entry point
├── app_trading.py           Live trading orchestrator
├── app_guotai.py            Mobile app automation (DroidRun + Gemini)
├── ARCHITECTURE.md          Detailed architecture and workflow docs
├── backtest/                Backtest framework (data, strategies, utils)
├── pick_stocks_from_sector/ Stock selection strategies
├── app/                     Live trading application modules
├── imobile/                 Reflex web application
├── utils/                   Shared utilities and submodules
├── db/                      Database schema and migrations
└── docs/                    Additional documentation
```

## Configuration

Edit `backtest/config.json` for risk parameters, position sizing, and regime-specific rules.

## Requirements

- Python 3.12+
- SQLite
- Tushare API (for A-Shares data)
- Google Gemini API (for AI strategies and mobile automation)
- Android emulator + ADB (for mobile automation)
- DroidRun (for mobile app interaction)

## Documentation

- [ARCHITECTURE.md](ARCHITECTURE.md) — Full system architecture and workflow
- [CHANGELOG.md](CHANGELOG.md) — Development history and changes
- [docs/](docs/) — Strategy design notes, API docs, design documents
