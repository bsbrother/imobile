# trading_test — simulate real trading over a date range

Replays the live trading process day by day for a range of dates, with one account carried from the
first day to the last: cash, holdings, entry dates, T+1.

    .venv/bin/python trading_test/run_trading_test.py --start 20260901 --end 20260930 --cash 300000
    .venv/bin/python trading_test/run_trading_test.py --start 20260901 --end 20260930 --no-app     # skip the phone read

Output goes to `trading_test/results/<start>_<end>_trading/`:

    report_trading_<date>.md   one per trading day, headlines shaped like the backtest's
                               report_orders_<date>.md (Orders Executed / Total Invested /
                               Realized P&L (Today) / Cash Remaining / Total Assets), plus the
                               pre-market plan, every fill with its reason, and end-of-day holdings
    smart_orders_<date>.json   the day's plan in the backtest's order shape
    day_state_<date>.json      cash, holdings, entry dates after that day
    daily_pv.json              the equity curve, one row per day
    trades.json                every fill of the whole run
    result_report.md           the period report: headline, daily table, exit attribution,
                               and a side-by-side against the backtest for the same window
    run.json                   the run's parameters and data diagnostics

## Each day

    09:15-09:30  PRE-MARKET  prices come from the PREVIOUS TRADING DAY'S CLOSE. Builds the day's
                             buys for that day's picks and the held positions' brackets / exits.
    09:30-11:30  MARKET       the day's intraday bars, walked in time order.
    13:00-15:00  MARKET       same walk continues.
    after 15:00  POST-MARKET  mark to market at the last bar's close; write the day's artifacts.

Fill rules (`matcher.py`, pinned by `tests/test_trading_test.py`):

| event | fill |
|---|---|
| buy, limit at/above the day's open | the open — the auction clears there |
| buy, limit below the open | first bar whose low reaches the limit, at `min(limit, open)` |
| take-profit touched | `max(tp, open)` — a gap up fills better than the trigger |
| stop-loss touched | `min(sl, open)` — a gap down fills worse; the backtest's blind spot |
| scheduled exit (force-sell, hold window closed) | the open |
| shares bought today | not sellable (T+1) |

Cross-day rules (`state.py`): T+1 rolls over each night; a position is force-sold at the open once
it has been held longer than the regime's `max_hold_days`, counted EXCLUSIVELY the way the engine
counts it. With `max_hold_days: 1` that means bought on N, sold at the open of N+2.

## Data — read before quoting a number

Intraday history comes from `shared/data_cache/m1m2_min30/`. That is **30-minute bars, 8 per day**,
covering 2025-09-22 .. 2026-09-29. There is no reachable 1-minute or 09:15-09:25 auction data for
these dates on this host. Consequences, all printed by the run:

- the call auction is modelled as a limit checked against the 09:30 open;
- inside one bar the order of high and low is unknown, so a bar touching both TP and SL resolves
  TP-first and is flagged;
- **only codes present in that cache can be traded.** Anything else is carried at cost, counted in
  the `Unpriced` column and excluded from P&L — the run says so and the result is labelled partial;
- no partial fills, no queue position, no cap or liquidity model, no slippage beyond the gap rule.

Account state: the app leg needs the phone and ADB up; the fallback is `shared/db/imobile.db`, which
is only as fresh as its last sync (2026-07-14 when this was written), so a DB-sourced run is labelled
stale rather than presented as live. `--no-app` forces the DB leg.

## Configuration — `sim_config.json`

    start / end      the date range (or --start / --end)
    cash             starting cash; the per-slot base (cash / max_positions) is FIXED, matching how
                     cli.py sizes live orders — it does not compound
    max_positions    per_slot = cash / max_positions, whole 100-share lots; also caps holdings
    max_hold_days    null = use the regime's value from backtest/config.json x HOLD_DAYS_MULT,
                     floored at 1, exactly as the engine computes it
    tp_mult/sl_pct   +200% / -2.5% off the pre-market reference price
    buy_limit_mode   limit_up (prev close x board band, always in the auction, default) |
                     prev_close_buffer | order_price
    fees             commission (min ¥5) both sides, stamp duty on sells

## Did the price-limit board matter? — `check_boards.py`

`check_fills.py` asks whether each printed fill could have happened. `check_boards.py` asks the prior
question: for the stocks the strategy picked, was the day a limit-board day at all — i.e. does the
policy in `backtest/utils/limit_board.py` change any decision, or is it a guard nobody hits?

    .venv/bin/python trading_test/check_boards.py                          # live pick files
    .venv/bin/python trading_test/check_boards.py --orders-dir backtest/results/<run>/ \
        --population 120                                                   # executed book + control

Three modes: the pick files (`backtest/results/daily/pick_stocks_*.json`), the executed book
(`smart_orders_*.json` in a run directory), and `--population N`, which classifies N sampled cache
symbols over the same sessions as a control. The control is the point: if the classifier could not
see a board anywhere, a zero in the order universe would be an artefact rather than a finding.

Measured 20260101–20260930 (163 sessions, 1052 orders): **4 orders on a board** (2 `up_open`,
1 `down_sealed`, 1 `down_open`), against a control base rate of 0.3% over the whole cache. Result,
analysis and the changes it drove: `../docs/AUCTION_IMPROVE.md`.

## Verifying the fills — `check_fills.py`

    .venv/bin/python trading_test/check_fills.py --run strategy-only      # or --run with-history

Checks every BUY/SELL price in a run's `trades.json` against the real cached bars for that code and
day, and writes `fills_realizability.md` into the results directory:

    RANGE      the price lies inside the day's real low..high
    BAR        the price lies inside the bar the fill is attributed to
    AUCTION    a fill recorded as "auction fill at open" equals the day's open exactly
    TICK       the price is a valid 0.01 step
    LIMIT      inside the day's price-limit band (10%; 20% on ChiNext/STAR), BUY never above its own limit
    LIQUIDITY  the quantity fits inside the filled bar's real volume

20260901-20260930: **46/46 fills pass** (strategy-only) and **49/49** (with history), zero failures.
Every auction fill equals the open to the tick; every stop/force-sell is inside the bar it claims;
size is a rounding error against real volume (median 0.002%, max 0.02% of the filled bar).

## Traps found building this

- **The live DB stores BARE codes.** `holding_stocks.code` holds `300308` while the cache, the plans
  and the strategy all use `300308.SZ`. Unnormalised, every account holding silently "has no bars"
  and is carried at cost — the first run reported +200.94% on a day the account was down.
- **A fixed `dirname()` count breaks when files move.** All three I/O modules resolved one level too
  high after this package moved up, so the cache, the picks and the account all came back empty and
  the run reported a clean, all-zero month. They now walk up to the directory holding `backtest/`.
- **A sold-out position is not a missing position.** Carrying over anything the matcher did not
  return resurrected sold positions, which then resold every day and double-counted the proceeds
  (equity ran to ¥6.8M). `DayResult.known` now distinguishes the two; unpassed holdings are carried
  unchanged and noted as a bookkeeping warning.
- **A bracket built off a stale cost basis sits through the market.** A holding whose DB row last
  synced weeks ago had a -2.5%-from-cost stop ABOVE the open, so it fired instantly. Real, but not
  that day's trading — the fill reason says so.

## Files

    run_trading_test.py   the range loop: calendar, per-day plan, artifacts, result report
    matcher.py            pure fill engine (no I/O — this is what the tests pin)
    state.py              the carried account: T+1 roll, entry dates, equity, drawdown, warnings
    plan.py               that day's picks -> sized buys; auction limits; brackets; positions
    bars.py               BarStore (each code read once) + previous close + granularity reporting
    app_state.py          holdings/cash from the app over ADB, DB fallback with provenance
    report.py             the per-day report and result_report.md (incl. the backtest comparison)
    check_fills.py        verifies every booked fill price against that day's real bars
    ANALYSIS_vs_backtest_202609.md   why -9.89% is the account's history, not the strategy
    sim_config.json       defaults
    ../../tests/test_trading_test.py   34 tests: fill semantics, costs, sizing, cross-day state, parsing, realizability

## What this is not

It is not a return estimate for the strategy, and the headline number is not the strategy's result.
On 20260901-20260930 the same picks give:

    -9.89%   with the account's history  (one legacy position realizes -82,418 of the -89,307 total)
    -1.87%   strategy only (--no-holdings), i.e. a ~3pp realism gap vs the backtest's +1.17%

The gap is fills, not bugs: a stop that gaps overnight fills at the open rather than at its trigger
(the backtest's blind spot), plus commission and stamp duty. Read a single day as a sanity check on
the plumbing, and see `ANALYSIS_vs_backtest_202609.md` for the full decomposition.
