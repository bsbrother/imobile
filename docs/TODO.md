# TODO — Current Tasks

## Completed — 2026-07 Refactor (refactor/optimize-and-clean branch)

- [x] **Test infrastructure**: pytest.ini, conftest.py, integration marker auto-skip, Makefile
- [x] **Characterization test**: ts_7AZ 70.59% baseline pinned (±0.2% band, 65% floor)
- [x] **Ghost test cleanup**: deleted 2 broken, moved 3 to scripts/, renamed 2 dot-filenames
- [x] **Performance: regime memoization** — eliminates ~120 redundant API calls per backtest
- [x] **Error handling: subprocess.run()** — replaced 11 os.system() calls, captures stderr
- [x] **Portability: sys.executable** — replaced hard-coded venv path
- [x] **Strategy dispatch**: table-driven _STRATEGY_SCRIPTS dict (was 10-branch if/elif)
- [x] **Unit test coverage**: +34 tests (engine helpers + CANSLIM scoring), 0.32s total
- [x] **Documentation**: REFACTORING.md, CHANGELOG, TODO, README updates

## Active

### Backtest Optimization
- [ ] Continue ts_7AZ parameter optimization: test HOLD_DAYS_MULT 0.3 vs 0.5, frozen SL vs widening
- [ ] Explore dynamic TP (trailing lock-in at 100% profit) instead of flat 200%
- [ ] Test higher frequency (daily re-pick) vs current ~weekly

### Search & News Integration
- [ ] Union news/sentiment/opinion for all strategies (currently only ts_ai + ts_daily)
- [ ] ts_ths_dc, ts_hma, ts_longup are pure technical — explore adding sentiment layer

### Strategy Improvements
- [x] ts_auto removed — ts_auto superior (more granular, 20d window, momentum sub-conditions)
- [x] ts_7AZ CANSLIM optimized to 70.60% (HOLD_DAYS_MULT=0.5, SL frozen, SL_BULL=2.5%)
- [x] Backtest resume support: skip already-processed dates, preserve DB state
- [ ] Proper period report when resuming with extended end_date

### Trading
- [ ] Legacy stock sell-only pipeline for pre-START_REAL_TRADING_DATE positions
- [ ] Test ts_daily as ts_ths_dc replacement for trend + hot sector picks
- [ ] Mid-day re-pick: regenerate picks at 13:00 based on morning session data

### Mobile Automation
- [ ] Fix "System UI not responding" Genymotion crash (RAM, graphics mode)
- [ ] Trajectory replay for common workflows (quote page, position page)
- [ ] Macro data fetching (Tushare GDP/CPI APIs) for regime detection

## Backlog

- [ ] Multi-strategy combination: allocate capital across strategies by regime
- [ ] Slack/Telegram notifications for trade signals
- [ ] Cron-based fully automated daily trading pipeline
- [ ] Benchmark comparison dashboard (vs SSE, CSI300, CSI500)
- [ ] Walk-forward optimization: auto-adjust params every 4 weeks

## Notes

### Best ts_7AZ Config (2026-07)
```
HOLD_DAYS_MULT=0.5
SL_WITH_RE_PICK=false     # frozen SL
SL_BULL=0.025
SL_NORMAL=0.025
SL_VOLATILE=0.02
SL_BEAR=0.015
ER_EXIT_ENABLED=true
SCORE_MIN=0
Result: 70.60% (20260101-20260619)
Saved: backtest/results_backups/20260101_20260619_ts_7AZ_70.60_baseline
```
