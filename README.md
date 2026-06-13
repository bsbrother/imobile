# iMobile — A-Shares Automated Trading System

Three subsystems for Chinese A-Share market trading:

| # | System | Entry | Description |
|---|--------|-------|-------------|
| 1 | **Backtest** | `backtest/engine.py` | Historical backtest with regime-aware strategy selection |
| 2 | **Trading** | `trading/runner.py` | Live trading with smart orders + mobile ADB automation |
| 3 | **Web** | `web/config.py` | Reflex portfolio dashboard |

## Quick Start

```bash
# Install dependencies
pip install -r requirements.txt

# Copy and configure environment
cp .env.example .env
# Edit .env with your Tushare token, OpenRouter keys, etc.

# Run backtest (default: ts_auto meta-strategy)
python backtest/engine.py 20250101 20250612

# Run specific strategy
python backtest/engine.py 20250101 20250612 ts_7AZ

# Live trading
python trading/runner.py --phase pre-market

# Analyze backtest results
python utils/result_backtest.py backtest/results/20250101_20250612_ts_auto

# Reflex web dashboard
cd web && reflex run
```

## Strategies

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

## Project Structure

```
imobile/
├── backtest/          # Backtest engine + strategies
├── trading/           # Live trading + mobile ADB
├── web/               # Reflex web dashboard
├── shared/            # Databases, data, caches
├── utils/             # Shared utilities
└── docs/              # Documentation
```

See [ARCHITECTURE.md](ARCHITECTURE.md) for full documentation.
