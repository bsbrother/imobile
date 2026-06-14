# TODO — Current Tasks

## Active

### ts_auto Optimization
- [ ] Achieve >15% total return over 3-month backtest (20250101–20250331)
- [ ] Incorporate news/sentiment analysis via FreeRide for better stock selection
- [ ] Iterative optimization: run backtest → check return → adjust thresholds → re-run

### Search & News Integration
- [ ] Union news/sentiment/opinion for all strategies (currently only ts_ai + ts_daily)
- [ ] ts_dc, ts_go, ts_hma, ts_longup are pure technical — explore adding sentiment layer

### Strategy Improvements
- [x] ts_auto removed — ts_auto superior (more granular, 20d window, momentum sub-conditions)
- [ ] Backtest resume: fix REPORT_PATH changing when end_date changes (symlink or copy old reports)
- [ ] Proper period report when resuming with extended end_date

### Trading
- [ ] Legacy stock sell-only pipeline (ts_history.py) for pre-2026-02-24 positions
- [ ] Test ts_daily as ts_dc replacement for trend + hot sector picks

### Mobile Automation
- [ ] Fix "System UI not responding" Genymotion crash (RAM, graphics mode)
- [ ] Trajectory replay for common workflows (quote page, position page)
- [ ] Macro data fetching (Tushare GDP/CPI APIs) for regime detection

## Backlog

- [ ] Multi-strategy combination: allocate capital across strategies by regime
- [ ] Slack/Telegram notifications for trade signals
- [ ] Cron-based fully automated daily trading pipeline
- [ ] Benchmark comparison dashboard (vs SSE, CSI300, CSI500)

## Date
### 20260601
Recommended Next Steps (if you wish to exceed 15% in the 3‑month window)
    1. Fine‑tune sub‑strategy selection in ts_auto.py:
       - In bull regimes, favor ts_longup or ts_go with even looser momentum thresholds (e.g., momentum > -5.0).
       - In bear/volatile regimes, consider using ts_go or ts_dc with relaxed entry criteria to increase trade frequency.
    2. Dynamic position sizing: Allocate more capital to higher‑conviction picks (e.g., top‑ranked stocks get 1.2× base size).
    3. Trailing‑profit enhancements: Instead of fixed take‑profit, use a trailing lock‑in (e.g., lock in 50% of gains after 100% profit is reached) to let extreme winners run further.
    4. Weekly re‑optimization: Run a short walk‑forward optimization (e.g., every 4 weeks) to adjust take‑profit/stop‑loss based on recent volatility.
    5. Extend the backtest window: If the 6‑month result is acceptable, consider using a rolling 6‑month window for live trading, which consistently exceeds 15%.
