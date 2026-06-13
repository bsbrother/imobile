# iMobile Architecture

> A-Shares automated trading system: backtesting, live trading, and reflex web dashboard.

## Project Overview

iMobile is organized into 3 subsystems:

1. **Backtest** — `backtest/` — quantitative research engine
2. **Trading** — `trading/` — live trading & mobile ADB automation
3. **Web** — `web/` — Reflex portfolio dashboard

Shared resources live in `shared/`.

## Directory Map

```
imobile/
├── backtest/                     # [1] Backtest Engine
│   ├── engine.py                 #   Main entry point (was: backtest_orders.py)
│   ├── strategies/               #   Stock selection strategies (was: pick_stocks_from_sector/)
│   │   ├── ts_auto.py            #     ★ Meta-strategy: regime → delegates
│   │   ├── ts_7AZ.py             #     CANSLIM 7-factor fundamental screener
│   │   ├── ts_ths_dc.py          #     Hot-sector + channel breakout (ts_dc)
│   │   ├── ts_daily.py           #     News-driven daily picks (LLM + search)
│   │   ├── ts_ai_pick.py         #     AI-driven stock selection
│   │   ├── ts_longup.py          #     Trend-following (ADX, slope analysis)
│   │   ├── ts_hma.py             #     Hull Moving Average + SuperTrend
│   │   └── ts_gb_line.py         #     Golden-cross / dead-cross line strategy
│   ├── core/                     #   Order generator, position sizing
│   ├── data/                     #   Data providers (Tushare, Akshare, SQLite cache)
│   ├── utils/                    #   Trading calendar, market regime detection
│   ├── analysis/                 #   Post-backtest analysis tools
│   ├── cbs_ewo/                  #   CBS/EWO analysis
│   ├── cli.py                    #   CLI for pick/analyze/run commands
│   ├── config.json               #   Risk/reward ratios, position sizing per regime
│   ├── results/                   Backtest output
│
├── trading/                      # [2] Live Trading System
│   ├── runner.py                 #   Live trading orchestrator (was: app_trading.py)
│   ├── guotai.py                 #   国泰君安 broker integration (was: app_guotai.py)
│   ├── adb.py                    #   ADB phone emulation (was: gm_emulate_adb.py)
│   └── trajectory/               #   DroidRun tap record replay
│
├── web/                          # [3] Reflex Web Dashboard
│   ├── config.py                 #   Reflex config (was: rxconfig.py)
│   ├── app/                      #   Reflex app (was: imobile/)
│   │   ├── imobile/              #     Main app module
│   │   │   ├── pages/            #     Portfolio, stock analysis pages
│   │   │   ├── components/       #     Reusable UI components
│   │   │   ├── states/           #     Reflex state management
│   │   │   └── utils/            #     Web helpers
│   │   ├── api.py                #     API endpoints
│   │   └── db.py                 #     Database models
│   ├── assets/                   #   Web assets (charts, icons)
│   ├── .web/                     #   Reflex build output
│   └── migrations/               #   Alembic DB migrations (was: alembic/)
│
├── shared/                       # Shared Resources
│   ├── db/                       #   Databases (imobile.db, caches)
│   ├── data/                     #   Stock analysis data
│   ├── data_cache/               #   Cached data files
│   └── utils/                    #   Shared utilities (future)
│
├── utils/                        # Shared Utilities (kept at root for import compat)
│   ├── daily_stock_analysis/     #   LLM-powered stock analysis pipeline
│   ├── searxng/                  #   Search engine integration
│   ├── FreeRide/                 #   OpenRouter proxy
│   ├── TradingAgents-CN/         #   Trading agents
│   ├── result_backtest.py        #   Post-backtest monthly + index analyzer
│   └── stock_news_public_opinion.py  # Search provider bridge
│
├── .env                          # Environment variables
├── requirements.txt              # Python dependencies
├── pytest.ini                    # Test config
└── docs/                         # Documentation
```

## Core Workflow: Backtest Engine

```
python backtest/engine.py <start> <end> <strategy> [--search] [--ai] [--resume]

     ┌─────────────────────────────────────────────┐
     │  For each trading day:                       │
     │                                              │
     │  1. pick_stocks_to_file(date, src)           │
     │     ├── detect_market_regime(date)           │  120-day MA60/MA120
     │     ├── ts_auto → determine_strategy()       │  20-day MA10 + momentum
     │     ├── Delegate to sub-strategy script      │
     │     └── Write pick_stocks_YYYYMMDD.json      │
     │                                              │
     │  2. create_smart_orders_from_picks()         │
     │     ├── Technical analysis per stock         │
     │     ├── Risk-adjusted position sizing        │
     │     └── Write smart_orders_YYYYMMDD.json     │
     │                                              │
     │  3. execute → OrderAnalyzer                  │
     │     ├── execute_buy_order()  → DB insert     │
     │     ├── execute_sell_order() → DB insert     │
     │     └── Write report_orders_YYYYMMDD.md      │
     │                                              │
     │  4. (period end) generate_period_report()    │
     └──────────────────────────────────────────────┘
```

## Strategy Selection: ts_auto (Meta-Strategy)

Default: **ts_7AZ** CANSLIM (proven 185% return, 19/21 winning months).

Edge cases:
- Strong bull (mom > 4%, vol < 1.5%) → **ts_longup** trend-following
- Sharp bear (mom < -8%, vol > 2.5%) → **ts_hma** Hull MA reversal

## Live Trading: trading/runner.py

```bash
python trading/runner.py [date] --phase [pre-market|market|post-market|auto|all]
```

Phases:
- pre-market  → Pick stocks → create/adjust smart orders
- market      → Monitor and execute orders
- post-market → Generate daily report, sync mobile app data

## Mobile Automation: trading/guotai.py

Uses DroidRun + Gemini to automate the Guotai stock trading app on Android:

```
Device (Genymotion) → ADB → DroidRun Portal → Gemini Vision → data extraction
                                                                   │
                                       ┌───────────────────────────┘
                                       ▼
                             DB sync (indices, quotes, positions, P&L)
```

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

1. **T+1 Compliance** — All backtests enforce T+1 settlement
2. **No Lookahead** — Strategies only use data available at market open
3. **Realistic Execution** — Orders fill at open_price, limit-up/down locks modeled
4. **20%/10% Limit Rules** — ChiNext/STAR use 20% limits; main board uses 10%
5. **Fees Modeled** — Commission 0.00341% (min ¥5), stamp duty 0.05% on sells
