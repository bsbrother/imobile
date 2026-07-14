# Setup Guide

Developer quick-start for the iMobile project.

---

## Prerequisites

- **Python 3.12** (managed via `uv`)
- **Tushare Pro account** (8000+ points recommended)
- **Google Gemini API key** (free tier: https://aistudio.google.com/app/apikey)
- **Bash shell** with the project's `.bashrc` auto-setup

---

## 1. Clone & Environment

```bash
cd /home/kasm-user/apps/imobile

# Virtual environment is pre-created via uv:
#   uv venv --python 3.12
#   source .venv/bin/activate

# When you cd into the project directory, ~/.bashrc auto-activates .venv
# and auto-sources .env for you. Verify:
echo $VIRTUAL_ENV    # → /home/kasm-user/apps/imobile/.venv
echo $TUSHARE_TOKEN  # → your token
```

If auto-activation doesn't work (non-interactive shells), use the helper:

```bash
# One-time: the backtest-trading helper sets up venv + env for any command
backtest-trading run python --version
```

---

## 2. Configure `.env`

```bash
cp .env.example .env
# Edit .env with your values:
#   TUSHARE_TOKEN=your_token
#   GOOGLE_API_KEY=your_gemini_key
#   GUOTAI_PASSWORD=your_trading_pin  (for live trading only)
```

Full variable reference: [docs/ENV_VARS.md](docs/ENV_VARS.md)

---

## 3. Run a Backtest

```bash
# Quick backtest with default ts_7AZ strategy
backtest-trading run python backtest/engine.py 20260101 20260619

# Fast mode (no AI, no search — ~30x faster; note: flags only affect ts_ai_pick/ts_daily/ts_auto)
backtest-trading run python backtest/engine.py 20260101 20260619 ts_7AZ --no-search --no-ai

# Analyze results
backtest-trading run python backtest/result_backtest.py backtest/results/20260101_20260619_ts_7AZ
```

---

## 4. Live Trading (Guotai Junan App)

```bash
# Pre-market (before 09:30): pick stocks, generate orders
backtest-trading run python trading/runner.py --phase pre-market --dry-run

# With order submission to broker app
backtest-trading run python trading/runner.py --phase pre-market --submit

# Post-market sync
backtest-trading run python trading/runner.py --phase post-market
```

---

## 5. Web Dashboard

```bash
cd web
reflex run
# → http://localhost:3000
```

---

## Project Layout

```
imobile/
├── backtest/          # Quantitative engine, strategies, order generation
│   ├── engine.py      #   Main orchestrator
│   ├── strategies/    #   Stock picking strategies
│   └── results/       #   Backtest output
├── trading/           # Live trading + ADB mobile automation
│   └── runner.py      #   Trading phase orchestrator
├── web/               # Reflex web dashboard
├── shared/db/         # SQLite databases
├── docs/              # Documentation
│   ├── BACKTEST.md    #   Full backtest pipeline
│   ├── TRADING.md     #   Live trading pipeline
│   ├── STRATEGIES.md  #   Strategy comparison
│   └── ENV_VARS.md    #   Environment variables reference
└── .env.example       #   Template for .env
```

---

## Common Commands

```bash
# Activate environment manually (if auto-setup fails):
source .venv/bin/activate
set -a; source .env; set +a

# Strategy backtest with specific strategy:
backtest-trading run python backtest/engine.py 20260101 20260619 ts_6Factors --no-search --no-ai

# Resume interrupted backtest:
backtest-trading run python backtest/engine.py 20260101 20260619 --resume

# Pre-market dry run (no app interaction):
backtest-trading run python trading/runner.py 20260712 --phase pre-market --dry-run

# Sync app data to DB (any time):
backtest-trading run python trading/runner.py --sync-only
```
