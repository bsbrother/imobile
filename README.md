# iMobile — A-Shares Automated Trading System

iMobile is a comprehensive, automated quantitative trading system designed specifically for the Chinese A-Share market. It enforces T+1 settlement rules, models realistic transaction costs, and prevents short-selling.

The project is divided into three core subsystems:

| # | System | Entry | Description |
|---|--------|-------|-------------|
| 1 | **Backtest** | `backtest/engine.py` | Historical backtest with regime-aware strategy selection |
| 2 | **Trading** | `trading/runner.py` | Live trading with smart orders + mobile ADB automation |
| 3 | **Web** | `web/app.py` | Reflex-based portfolio and market analysis dashboard |

---

## 🚀 Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment variables (Tushare token, LLM keys)
cp .env.example .env

# 3. Run a backtest (Default: ts_7AZ CANSLIM strategy)
python backtest/engine.py 20250101 20250612

# 4. Analyze backtest results
python backtest/result_backtest.py backtest/results/20250101_20250612_ts_7AZ

# 5. Live trading (pre-market phase)
python trading/runner.py --phase pre-market

# 6. Launch the Reflex Web Dashboard
cd web && reflex run
```

---

## 🌟 Subsystem Features

### 1. Backtest Engine (`backtest/`)
- **Regime-Aware CANSLIM Strategy:** `ts_7AZ` screens stocks across 7 dimensions (C-A-N-S-L-I-M) with regime-based risk management.
- **Dynamic Risk Management:** Take-profit and stop-loss adjust per regime (Bull 25%/5%, Normal 15%/4%, Volatile 10%/3%, Bear 8%/2%) with trailing SL. ChiNext/STAR (3/688) stocks get boosted TP/SL (35%/15% Bull, 25%/14% Normal) for their 20% daily limit.
- **Smart Order Generation:** Computes position size, buy price, take-profit, and stop-loss using technical indicators (ATR, Bollinger Bands). Bull regime uses ATR-based gap pricing (`close × (1 + 0.5×ATR/close)`, capped at 7%/13%) to ensure the `股价 ≤ buy_price` trigger fires on gap-up opens.
- **A-Shares Compliance:** Strictly enforces T+1 settlement, limit-up/limit-down blocking, and realistic fees (Commission + Stamp Duty). *Note: The minimum buy lot size of 200 shares for STAR/ChiNext stocks is currently not enforced in the simulation engine (it uses a 100-shares limit for all A-shares).*
- **Rich Reporting:** Generates per-day Markdown reports with P&L, transactions, and final period vs benchmark (SSE/CSI300) comparisons. Reports are automatically skipped for future dates (pre-market mode) where no historical OHLCV data exists yet.

### 2. Live Trading & Automation (`trading/`)
- **Android UI Automation:** Uses `DroidRun` + ADB + Google Gemini Vision to navigate the Guotai Junan stock app on an Android emulator.
- **Phased Execution Pipeline:** Runs via crontab in `pre-market` (generate smart orders), `market` (execute & monitor), and `post-market` (sync & report) phases.
- **Pre-Market Smart Orders:** Picks stocks and sets regime-aware buy triggers before market open. Execution/benchmark reports are skipped for future dates (no OHLCV yet).
- **Resilient Mobile Agent:** Recovers from popups, updates, and navigation failures using intelligent OCR-based UI parsing.
- **Database Synchronization:** Automatically syncs real-time holdings, cash balances, and P&L from the brokerage app into a local SQLite database (`imobile.db`).

### 3. Web Dashboard (`web/`)
- **Real-Time Portfolio Overview:** Tracks total market value, cash, daily P&L, and cumulative P&L synced directly from the mobile app.
- **Sector History Analysis:** Interactive visualization tool to explore historical hot sectors, view top stocks, and plot candlestick + MACD charts.
- **Trading Operations Integration:** Links directly to AI-generated stock analysis reports and operation command logs for every held position.
- **Responsive Dark-Mode UI:** Built purely in Python using [Reflex](https://reflex.dev/) and Radix UI.

---

## 📈 Stock Picking Strategies

| Strategy | Type | Description | Best In |
|----------|------|-------------|---------|
| `ts_7AZ` | ✦ Default | CANSLIM 7-factor (C-A-N-S-L-I-M) quality screener with regime-based TP/SL | Normal/Moderate |
| `ts_ao_er` | Technical | AO + ER (Elliott Wave Oscillator divergence detection) | Bear/Volatile |
| `ts_6Factors` | Fundamental | 6-factor V-G-Q-M-L-S screener (value, growth, quality, momentum, low-vol, size) | All |
| `ts_auto` | Meta | Auto-selects best sub-strategy based on 20-day regime | All conditions |
| `ts_dc` | Technical | Hot-sector channel breakout | Bull/Normal |
| `ts_hma` | Technical | Hull Moving Average + SuperTrend reversal detection | Sharp Bear |
| `ts_longup` | Technical | ADX trend-following | Strong Bull |
| `ts_multi_factors` | Momentum | BigQuant-inspired volume-acceleration + slope-ranking strategy | Bull/Trending |
| `ts_ai_pick`| AI | Full AI analysis with news/sentiment | Any |
| `ts_daily` | AI | News-driven daily picks (LLM + web search) | Any |

---

## 📁 Project Structure

```
imobile/
├── backtest/          # Quantitative engine, strategies, order generation
├── trading/           # Live trading runner, ADB mobile automation agent
├── web/               # Reflex web dashboard (frontend + backend)
├── shared/            # Shared SQLite databases (imobile.db, cache)
├── utils/             # Cross-module utilities (LLM analysis, search)
├── tests/             # Pytest test suite
└── docs/              # Detailed subsystem documentation
```

For a deep dive into how these systems interact, please read [ARCHITECTURE.md](ARCHITECTURE.md) or explore the individual module documentation in `docs/`.

---

## 📚 Documentation

| Doc | Description |
|---|---|
| [docs/SETUP.md](docs/SETUP.md) | Developer quick-start guide |
| [ARCHITECTURE.md](ARCHITECTURE.md) | System architecture and data flow |
| [docs/BACKTEST.md](docs/BACKTEST.md) | Full backtest pipeline (7 phases) |
| [docs/TRADING.md](docs/TRADING.md) | Live trading pipeline (3 phases) |
| [docs/WEB.md](docs/WEB.md) | Web dashboard |
| [docs/STRATEGIES.md](docs/STRATEGIES.md) | Strategy comparison and selection guide |
| [docs/ENV_VARS.md](docs/ENV_VARS.md) | Complete environment variables reference |
| [docs/TODO.md](docs/TODO.md) | Current tasks and backlog |
| [CHANGELOG.md](CHANGELOG.md) | Project changelog |
| [.env.example](.env.example) | Environment variables template |
