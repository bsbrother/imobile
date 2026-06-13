# iMobile Architecture

> A-Shares automated trading system: backtesting, live trading, mobile app data sync, and portfolio web UI.

## Project Overview

iMobile is a comprehensive A-Shares (Chinese stock market) trading automation system with three major subsystems:

1. **Backtest Engine** — historical simulation with regime-aware strategy selection
2. **Live Trading** — daily pre-market stock picking, smart order generation, execution
3. **Web & Mobile** — Reflex web portfolio UI, DroidRun mobile app automation

## Directory Map

```
imobile/
├── backtest_orders.py         ★ Main backtest entry point (2,100+ lines)
├── app_trading.py             ★ Live trading orchestrator (CLI)
├── app_guotai.py              ★ Mobile app automation (DroidRun + Gemini)
│
├── backtest/                  # Backtest framework
│   ├── core/                  #   Order generator, position sizing
│   ├── strategies/            #   Strategy classes
│   ├── data/                  #   Data providers (Tushare, Akshare, SQLite cache)
│   ├── utils/                 #   Trading calendar, market regime detection
│   ├── analysis/              #   Post-backtest analysis tools
│   ├── config.json            #   Risk/reward ratios, position sizing per regime
│   └── cli.py                 #   CLI for pick/analyze/run commands
│
├── pick_stocks_from_sector/   # Stock selection strategies
│   ├── ts_auto.py        #   ★ Meta-strategy: regime → delegates to best sub-strategy
│   ├── ts_daily.py            #   News-driven daily picks (LLM + search)
│   ├── ts_ai_pick.py           #   AI-driven stock selection
│   ├── ts_dc.py / ts_ths_dc.py #   Hot-sector + channel breakout
│   ├── ts_longup.py           #   Trend-following (ADX, slope analysis)
│   ├── ts_hma.py              #   Hull Moving Average + SuperTrend
│   └── ts_gb_line.py          #   Golden-cross / dead-cross line strategy
│
├── app/                       # Live trading application
│   ├── core/                  #   Device auth, ADB replay, screenshots
│   ├── trading/               #   Daily trading workflow
│   ├── data/                  #   Guotai app data extraction
│   └── core/sync.py           #   App → DB sync pipeline
│
├── imobile/                   # Reflex web application
│   ├── pages/                 #   Portfolio, stock analysis pages
│   ├── components/            #   Reusable UI components
│   ├── states/                #   Reflex state management
│   └── utils/                 #   Web helpers
│
├── utils/                     # Shared utilities
│   ├── daily_stock_analysis/  #   [submodule] LLM-powered stock analysis pipeline
│   ├── droidrun/              #   [submodule] Android automation SDK
│   ├── go-stock/              #   Go-based technical screener (ts_go strategy)
│   ├── FreeRide/              #   News/sentiment search integration
│   ├── result_ts_auto.py #   Monthly performance reporter
│   └── stock_news_public_opinion.py  # Search provider bridge
│
├── db/                        # Database
│   ├── imobile.sql            #   Schema (tables: holding_stocks, smart_orders, transactions, ...)
│   └── migrations/            #   Alembic migrations
│
├── docs/                      # Documentation
│   ├── commit.md              #   Development changelog
│   ├── design/                #   Design documents
│   └── api/                   #   API docs
│
└── tests/                     # Test suite
```

## Core Workflow: Backtest Engine

```
python backtest_orders.py <start_date> <end_date> <strategy> [user_id] [search] [ai] [resume]

     ┌─────────────────────────────────────────────┐
     │  pick_orders_trading(start, end, src, ...)  │
     └──────────────────┬──────────────────────────┘
                        │
    ┌───────────────────▼──────────────────────────┐
    │  For each trading day:                       │
    │                                              │
    │  1. pick_stocks_to_file(date, src)           │
    │     ├── detect_market_regime(date)           │  120-day MA60/MA120 crossover
    │     ├── ts_auto → determine_strategy()  │  20-day MA10 + momentum split
    │     ├── Delegate to sub-strategy script      │
    │     └── Write pick_stocks_YYYYMMDD.json      │
    │                                              │
    │  2. create_smart_orders_from_picks()         │
    │     ├── Technical analysis per stock         │
    │     ├── Risk-adjusted position sizing        │
    │     └── Write smart_orders_YYYYMMDD.json     │
    │                                              │
    │  3. OrderAnalyzer.generate_daily_report()    │
    │     ├── execute_buy_order()  → DB insert     │
    │     ├── execute_sell_order() → DB insert     │
    │     └── Write report_orders_YYYYMMDD.md      │
    │                                              │
    │  4. (period end) generate_period_report()    │
    │     └── Write report_period_<range>.md       │
    └──────────────────────────────────────────────┘
```

## Strategy Selection: ts_auto (Meta-Strategy)

`ts_auto` is the primary strategy. It detects short-term market regime (20 trading days) and delegates to the optimal sub-strategy:

| Regime | Momentum | Strategy | Rationale |
|--------|----------|----------|-----------|
| Bull (>MA10, trend>0.3%) | Momentum > 4% | **ts_longup** | Strong uptrend — ride momentum leaders |
| Bull | Momentum ≤ 4% | **ts_dc** | Moderate uptrend — channel breakout |
| Bear (<MA10, trend<-0.3%) | Momentum < -4% | **ts_hma** | Freefall — HMA oversold bounces |
| Bear | Momentum ≥ -4% | **ts_daily** | Drifting down — isolated news plays |
| Volatile (vol>2.2%) | — | **ts_ai_pick** | Choppy — AI-driven fundamental picks |
| Normal | — | **ts_dc** | Sideways — channel breakout value |

The meta-strategy `ts_auto` decides which to use based on a 20-day MA10/volatility/trend view.

## Strategy Types

### AI-Dependent (require LLM + search)
- **ts_ai_pick** — Full AI analysis with news/sentiment
- **ts_daily** — Daily news-driven picks using `daily_stock_analysis` submodule
- **ts_auto** — Meta-strategy that may delegate to AI strategies

### Pure Technical (no LLM/search needed)
- **ts_dc** — Hot sectors + money flow + limit-up analysis
- **ts_hma** — Hull Moving Average + SuperTrend indicators
- **ts_longup** — Slope analysis, MA, ADX trend detection
- **ts_go** — Go backend: bulk technical indicators with late-trend filters

### Backtest Flags
- `backtest_search=False` — Skip all web search calls
- `backtest_ai=False` — Force AI-dependent strategies → pure technical fallbacks
- `resume` keyword — Skip dates with existing reports, preserve DB state

## Live Trading: app_trading.py

```
python app_trading.py [date] --phase [pre-market|market|post-market|auto|all]

Phases:
  pre-market  → Pick stocks → create/adjust smart orders
  market      → Monitor and execute orders
  post-market → Generate daily report, sync mobile app data
```

## Mobile Automation: app_guotai.py

Uses DroidRun + Gemini to automate the Guotai stock trading app on Android:

```
Device (Genymotion) → ADB → DroidRun Portal → Gemini Vision → data extraction
                                                                    │
                                        ┌───────────────────────────┘
                                        ▼
                              DB sync (indices, quotes, positions, P&L)
```

## Database Schema

Key tables in `db/imobile.sql`:

| Table | Purpose |
|-------|---------|
| `holding_stocks` | Current positions (symbol, cost_basis, shares, user_id) |
| `smart_orders` | Pending buy/sell orders with trigger prices |
| `transactions` | Completed trades with P&L tracking |
| `user_table` | User accounts |
| `market_indices` | Cached market index data |
| `stocks_table` | Stock metadata and real-time quotes |

## Configuration

`backtest/config.json` controls risk parameters per regime:

```json
{
  "init_info": { "initial_cash": 600000, "max_positions": 6 },
  "trading_rules": {
    "risk_reward_ratios": {
      "bull_market":    { "take_profit_pct": 0.55, "stop_loss_pct": 0.03 },
      "bear_market":    { "take_profit_pct": 0.12, "stop_loss_pct": 0.05 },
      "volatile_market": { "take_profit_pct": 0.22, "stop_loss_pct": 0.04 },
      "normal_market":  { "take_profit_pct": 0.22, "stop_loss_pct": 0.06 }
    }
  }
}
```

## Key Design Principles

1. **T+1 Compliance** — All backtests enforce T+1 settlement (buy today, sell tomorrow earliest)
2. **No Lookahead** — Strategies only use data available at market open
3. **Realistic Execution** — Orders fill at open_price, limit-up/down locks are modeled
4. **20%/10% Limit Rules** — ChiNext (30xxxx) and STAR (688xxx) use 20% limits; main board uses 10%
5. **Fees Modeled** — Commission 0.00341% (min ¥5), stamp duty 0.05% on sells
