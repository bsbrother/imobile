# Changelog

Key changes and milestones in iMobile development.

## 2026-06

### Pre-Market Smart Order Fixes
- **Tushare-first data provider** — `TushareDataProvider.get_index_data()` now tries Tushare first and falls back to Akshare; previously the order was reversed, causing noisy "Akshare index fetch failed" warnings on every run.
- **Skip backtest reports for future dates** — `pick_orders_trading()` (Steps 3 & 4) now guards `generate_daily_report()` and `generate_period_report()` behind `date < today`. Pre-market runs targeting a future trading date no longer attempt to simulate order execution or benchmark comparison (which require OHLCV data that doesn't exist yet), eliminating "No OHLCV data found" and "Failed to calculate metrics for CSI 500" noise.
- **Remove dead target-date OHLCV fetch** — `analyze_stocks_and_generate_orders()` in `cli.py` previously fetched `get_stock_data(symbol, target_trading_date, ...)` unconditionally for every symbol, then discarded the result in all non-bull regimes. Moved the fetch inside `if regime == 'bull':` where it is actually used.
- **ATR-based bull market buy trigger** — In bull regime, smart order buy price is now set to `close × (1 + gap_pct)` where `gap_pct = clamp(0.5×ATR/close, min=2%, max=7%/13%)`. This ensures the `股价 <= buy_price(触发买入)` smart order fires on a typical gap-up open without chasing parabolic/near-limit-up moves. ChiNext (300) and STAR (688) stocks use the wider 13% cap (20% daily limit), main board stocks use 7% (10% daily limit).

### Documentation & Architecture
- **Comprehensive Subsystem Docs** — Created detailed documentation for all three core subsystems (`docs/BACKTEST.md`, `docs/TRADING.md`, `docs/WEB.md`).
- **Architecture Revamp** — Rewrote `README.md` and `ARCHITECTURE.md` to reflect the unified structure and data flow between the Backtest engine, Live Trading mobile agent, and Reflex Web dashboard.

### Strategy & Backtesting
- **ts_auto optimization** — aggressive parameter tuning for >15% 3-month returns
  - Increased max_positions to 6, bull TP to 55%, loosened stagnation cuts
  - 20-day lookback with MA10 crossover (replaced 40-day lag)
- **ts_auto strategy** — removed (ts_auto superior: 20d window, momentum sub-conditions, more granular)
- **Backtest resume support** — skip already-processed dates, preserve DB state
- **Monthly reporter** — `utils/result_ts_auto.py` for per-month P&L analysis

### Data & Search
- **Search provider cache** — pre-backtest search API validation, working provider whitelist
- **SearXNG integration** — local search backend at localhost:8080
- **FreeRide/OpenRouter** — alternative LLM provider for news analysis

### Infrastructure
- **Data cache fixes** — resolved `data_cache.db` corruption, bulk OHLCV fetching
- **Performance** — bulk API calls in ts_ths_dc.py (30min → seconds per day)

## 2026-05

### Strategy Selection
- **ts_auto**: regime → delegates to ts_ai_pick, ts_daily, ts_dc, ts_go, ts_hma, ts_longup
- **Dual-layer regime detection**: macro (120-day) + micro (20-day) for risk + strategy
- **Star Board & ChiNext support**: 20% limit rules for 30xxxx and 688xxx stocks
- **ts_daily**: leading breakout stocks from hot sectors with volume explosion filter

### Realism Fixes
- **No lookahead**: AI strategies now use `get_trading_days_before(target_date, 1)`
- **Limit-down sell blocking**: can't sell if stock locked at limit-down all day
- **Gap-down buy fix**: removed artificial `open_price >= prev_close` buy block
- **Fees modeled**: commission 0.00341% (min ¥5), stamp duty 0.05% on sells

### Architecture
- **Runtime monkey patching**: custom backtest logic without modifying daily_stock_analysis submodule
- **Macro data gap**: identified no programmatic CPI/GDP/interest rate fetching

## 2026-02

- **Live trading begins**: ts_auto auto-select strategy for new positions
- **Legacy stock handling**: ts_history.py for pre-2026-02-24 positions (sell-only)
- **Mobile → DB sync**: app_guotai.py extracts real-time data from Guotai app

## 2025-12

- **Backtest orders script** operational with T+1 compliance
- **Strategy comparison**: ts_ths (15.60%) vs ts_dc (2.49%) vs ts_combine (-0.11%)
- **Monthly results**: October -2.63% (ts_dc), November +6.95% (ts_dc)
- **result_ts_vs_index.py**: strategy vs SSE/CSI300 benchmark comparison

## 2025-11

- **EWO strategy exploration**: Elliott Wave Oscillator for divergence detection
- **5/21 moving average line strategy**: buy/sell signals from moving average crossovers
- **Hot sector stock picking**: multi-factor scoring (strategy count, vol/return, limit-up)
- **Smart order sell logic**: trailing stops, volume-based, market condition-based exits

## 2025-10

- **Initial backtest framework**: cli.py pick/analyze/run commands
- **Data provider integration**: Tushare, Akshare, Baostock
- **Database schema v1**: holding_stocks, smart_orders, transactions tables
- **Project bootstrap**: Reflex web app, DroidRun mobile automation setup
