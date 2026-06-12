# TODO — Current Tasks

## Active

### ts_month_src Optimization
- [ ] Achieve >15% total return over 3-month backtest (20250101–20250331)
- [ ] Incorporate news/sentiment analysis via FreeRide for better stock selection
- [ ] Iterative optimization: run backtest → check return → adjust thresholds → re-run

### Search & News Integration
- [ ] Union news/sentiment/opinion for all strategies (currently only ts_ai + ts_daily)
- [ ] ts_dc, ts_go, ts_hma, ts_longup are pure technical — explore adding sentiment layer

### Strategy Improvements
- [x] ts_auto removed — ts_month_src superior (more granular, 20d window, momentum sub-conditions)
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
