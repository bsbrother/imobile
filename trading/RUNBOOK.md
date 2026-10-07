# Real-market runbook — one trading day, end to end

Worked example: **2026-10-08** (Thursday). Verified against the project calendar: 2026-10-08 IS a
trading day, and it is the **first trading day after the National Day break** (20261001–20261007
closed), so the previous trading day is **20260930**.

Two tracks run on a real day and they are NOT the same thing:

| Track | Who runs it | Places real orders? | Output |
|---|---|---|---|
| `stock_cron_tasks.py` daemon | automatic, started at boot | **No** | pick + plan pushed to WeChat, ¥100k paper portfolio |
| `trading/runner.py` / `trading/pre_market_run.py` | **manual / agent-invoked** | **Yes**, via ADB on the phone | orders live in the broker app |

There is no crontab entry, systemd timer, or shell hook that invokes `runner.py` — checked. The
daemon has zero references to `create_ordinary_order` / `create_tp_sl_order`. So the automated track
gives you a pushed plan; **placing the real orders is the step a human or the agent must invoke.**

---

## Preconditions (T-1 evening or before 09:00)

1. **Phone reachable over ADB.** `adb devices` lists the serial; DroidRun Portal accessibility
   service enabled on the device (`droidrun ping`).
2. **Broker app installed and logged in.** `python -m trading.adb` checks connectivity + package.
3. **`.env` carries** `GUOTAI_PACKAGE_NAME`, `GUOTAI_PASSWORD`, `GOOGLE_API_KEY`/`GEMINI_API_KEY`,
   and `DEFAULT_STRATEGY` (= the live strategy; also applied by `runner.py` at import via
   `apply_strategy_env`). Never print these values.
4. **Trading day check.** Not a trading day → `runner.py` rolls forward to the next one.

Post-holiday specifics for 2026-10-08: any conditional order left **running** since 09-30 must be
stopped first (see Phase 5), and the pre-market reference price is 2026-09-30's close. That is
correct, not stale — the exchange sets the ±10%/±20% band for 10-08 off the 09-30 close.

---

## Timeline

    08:30-09:14   plan + dry run          no app writes
    09:15-09:25   DORMANT                 nothing is submitted, nothing is priced off the indicative
                                          quote (placeable-and-pullable before the 09:20 cancel lock)
    09:25:00      auction matches at ONE price  = the open  <- the price every order is priced off
    09:25:05      CREATE + SUBMIT         all orders, at that auction price
    09:30-11:30   session I               broker executes server-side, workstation only syncs
    13:00-15:00   session II              same
    15:05+        post-market             sync, report, suggestions

---

## Phase 1 — read the account (phone read, ~2-4 min)

Reads live state off the phone by UI parsing, then checks the DB against it.

1. Launch + login the app:
   `open_app()` → `adb shell am start -W -n <pkg>/com.gtja.home.InitScreen`, `login()` if the
   session dropped.
2. **我的持仓**: home → `行情` bottom tab → `我的持仓` tab → tap **`同步`** to force a refresh.
3. **智能订单**: page through the `今日已触发` / `运行中` / `已结束` tabs (the scraper is told to
   skip nothing here, and to stop at the sync cutoff date).
4. **成交history**: `历史成交` page, stops at the cutoff date.
5. DB vs app match check → on mismatch, sync app→DB (`cron_sync_app_to_db`).

Command:

    .venv/bin/python trading/runner.py 20261008 --phase pre-market            # dry run, no writes
    .venv/bin/python trading/pre_market_run.py 20261008                       # equivalent entry point

---

## Phase 2 — pick, size, plan (no app interaction)

1. **Invalidate stale OHLCV.** `SQLiteDataCache.invalidate_recent(data_type='ohlcv_data', days=3)`
   — drops ~25 rows, so the pick does not use a stale close.
2. **Regime** via `detect_market_regime(20261008)` → bear normal bull volatile → per-regime TP/SL,
   `max_hold_days`, `max_open_gap_pct`, position count.
3. **Pick** with the live strategy (`src = .env DEFAULT_STRATEGY`) → `pick_stocks_20261008.json`.
4. **Orders** — `python -m backtest.cli analyze` → `smart_orders_20261008.json`: BUY quantity per
   slot (`cash / max_positions`, whole 100-share lots), per-stock cap, and a skip reason when the
   minimum lot does not fit the slot.
5. **Held positions** get a fresh TP/SL bracket each day; a position whose hold window has closed
   gets a **scheduled exit** (name ends `_expired`) whose trigger is priced at the auction/open
   price — that is a sell instruction, not a bracket.
6. **Dry run first, always.** Verify BUY count, cash-total ≤ available, TP/SL count = holdings,
   and read `pre_market_20261008.md`.

---

## Phase 3 — CREATE + SUBMIT on the auction price (09:25:05-09:30) — the mobile-app phase

09:15-09:25 is deliberately idle. It is the window in which the indicative quote can be placed and
pulled, so no order is created from it. The auction prints ONE price at 09:25:00 — the open — and
`runner.py` sleeps until that print, `TRADING_SUBMIT_MODE`'s target (09:25:05 by default:
`TRADING_AUCTION_CONFIRM_AT` plus 5s for the print to settle).

Everything is then priced off that print:

- **BUYs** bid the confirmed open plus `TRADING_AUCTION_BUFFER_PCT` (0.5%), capped at the real
  limit-up. Two gates run first, both on certain numbers rather than indicative ones: the regime
  `max_open_gap_pct` cap and the price-limit board policy (below). A refused order is skipped rather
  than sent at the band top.
- **Scheduled exits** (name ending `_expired`) are re-priced at the confirmed open — the pre-market
  plan had them at the previous close. A scheduled exit means "sell at the auction price" and now it is.
- **Brackets** (TP/SL on held positions) are strategy levels relative to cost and are left alone; each
  is still checked against the limit-down, because a stop at/below the floor cannot fill.

The cost of this mode is the auction slot: orders created at 09:25:05 queue at the front of the 09:30
continuous session instead of joining the 09:25 match. That is the intended trade — the 09:25 print is
the 09:30 open to within a tick, so the front of the queue gets that price without having to guess it.

`TRADING_SUBMIT_MODE=auction` is the legacy path: submit by `TRADING_SUBMIT_BY` (default 09:20) to
join the auction itself, bidding from the indicative quote — and that quote is read only from
`TRADING_QUOTE_TRUST_FROM` (09:20), after the cancel lock. (`TRADING_BUY_LIMIT_MODE=limit_up` is
exempt from the quote-trust rule: it reads no quote.)

**Limit boards.** Before submitting, each BUY is checked against the price-limit board
(`backtest/utils/limit_board.py`). A board that is sealed at limit-up has no sellers; an open at
limit-up is an entry at the top of the band; a crash open at limit-down voids the momentum premise
the pick was made on. All three skip the order (logged as `⏭️ SKIP BUY <code>: <reason>`) instead of
putting in an order that cannot fill or should not fill.

**Exits are checked at the price they would print at.** At the limit-down there are no buyers, so a
SELL cannot fill — no matter what triggered it. That is a property of the price, not of the day:
a scheduled exit priced at a limit-down open cannot fill, while a take-profit the stock reaches
later in the same session can. At submit time each held position is therefore checked twice and warns
if either applies:

- the live quote sits at/below the limit-down (`fill_block_reason('sell', ...)`), or
- its own TP/SL trigger sits at/below the limit-down, in which case it can never be reached either.

Either way the position **carries to the next session** — the exit is not cancelled, the daily plan
re-emits it. The backtest refuses exactly the same fills (`execute_sell_order`), which is what keeps
the two paths comparable.

    .venv/bin/python trading/runner.py 20261008 --phase pre-market --submit
    # or, with the explicit plan/cash-limit/limit-up logic:
    .venv/bin/python trading/pre_market_run.py 20261008 --submit

**Per-order app actions — TP/SL form (`create_tp_sl_order`)**

    open_app → login → 今日触发 → tap 止盈止损 (1150, 2240)
    tap stock-code field (820, 873) → overlay search (806, 369) → type code → tap first result (719, 717)
    tap TP field (939, 1953)  → clear → type TP price
    swipe 720,2000 → 720,500 ; tap SL field (939, 1187) → clear → type SL price
    tap quantity (939, 1959)  → clear → type quantity
    swipe to bottom ; tap order-method (217, 2319) → confirm (1021, 2244)
    tap order type / 自动 (721, 2899)
    tap 创建订单 (902, 2825) → confirm popups (1021, 2244) x2

**Per-order app actions — BUY (`create_ordinary_order`)**

    goto 普通交易/买入 page
    tap code field (483, 552) → overlay search (483, 150) → type code → tap first result (719, 717)
    tap price (483, 856) → type price ; swipe 720,2000→720,500 to drop the keyboard
    tap quantity (483, 1070) → type quantity ; swipe again
    tap 买入 (483, 1366) → confirm popups x2

**BUY price.** `runner.py` computes it in `_auction_buy_price()`. In an auction you pay the clearing
price, not your bid, so the limit only decides whether you are IN the match — bidding higher buys
participation, not a worse price. Three modes (`TRADING_BUY_LIMIT_MODE`):

| mode | bid | what it costs |
|---|---|---|
| `indicative` (default) | live quote + `TRADING_AUCTION_BUFFER_PCT` (0.5%), floored at the plan | fills gap-up days the backtest refused |
| `limit_up` (in `.env`) | full band off the previous close: 10% main, 20% 688/689/300/301, 30% 北交所 | guaranteed participation, same gap-up cost |
| `plan_capped` | never above the engine's own planned limit | trades the backtest's list; matching misses |

Either way the bid is capped at the real limit-up, and a board-refused order is skipped before this
point. The gap-up divergence is measured: 461/1052 (43.8%) of historical orders had a planned limit
BELOW the previous close (mean 7.54% below — a dip-buy limit), and the backtest fills only when
`open <= planned limit`. Any live bid at or above the open therefore fills days the backtest skipped.
See `docs/AUCTION_IMPROVE.md` §3 and `trading_test/check_boards.py`.

**Then sync:** `cron_sync_app_to_db()` so the DB reflects what was actually submitted.

---

## Phase 4 — market hours (09:30-11:30, 13:00-15:00)

The broker's server-side conditional orders do the work; the workstation only accounts for it.

    .venv/bin/python trading/runner.py --phase market        # one sync; the docstring suggests hourly

- The scheduled exit and TP/SL fire at 09:30 whichever side the market opens through, because both
  triggers were priced at the auction price.
- A position bought today is **T+1**: not sellable until the next session.

## Phase 5 — post-market (>15:00, e.g. 15:10)

    .venv/bin/python trading/runner.py --phase post-market

1. **Final sync** app→DB (end-of-day position, orders, transactions).
2. **Report** `backtest/results/daily/post_market_20261008.md`: account summary and holdings
   mark-to-market, **order execution analysis** (which BUYs filled vs unfilled, which TP/SL
   triggered, legacy force-sells), realized and floating P&L, benchmark vs indices, expiry
   analysis, and 6-8 suggestions for the next day.
3. **Stop stale running orders.** `python trading/stop_order.py --submit`. Any conditional order
   still `运行中` at the close — including everything left over from 09-30 — must be cancelled or it
   will fire into tomorrow's session.

---

## What runs by itself on 2026-10-08 (no human)

`stock_cron_tasks.py`, started at boot by `startup_sidecars.sh`, trading-day aware
(Asia/Shanghai):

    09:25-09:30   PRE_START/PRE_END   pick + plan (BOTH new picks and held names) -> push to WeChat
    09:31, 09:51  OPEN_SCAN_TIMES     open scans, top-5 advisory -> push
    15:05+        POST_START          as-backtest OHLC settlement -> push
    intraday      random 30-35 min    equity snapshots (no lunch pushes 11:30-13:00)

It keeps its own ¥100,000 paper portfolio in `stock_cron_state.json`, independent of the live
account, and **never calls the app**. Treat its numbers as analysis, not as fills.

---

## Two gaps found while writing this

1. **Conditional orders do not get a 1-day validity.** There are two order forms and they differ:
   `create_ordinary_order` drives **普通买入/卖出** — an A-share ordinary limit order is day-valid by
   exchange rule, so it has no date field and needs none. `create_tp_sl_order` drives **止盈止损**,
   which DOES carry a validity period, and it never calls `set_valid_until_today()`. That helper has
   exactly one functional call site in the whole repo — `create_order_sell.py:166` — a standalone
   script that nothing imports or invokes; `create_order_buy.py` imports it but never calls it (dead
   import). So the daily brackets and the `_expired` scheduled exits keep the app's own default
   validity instead of one trading day. That is exactly why Phase 5's `stop_order.py` step is not
   optional, and why orders left from 09-30 would still be live on 10-08.
2. **Nothing schedules the real-order path.** No crontab, no systemd timer, no reference to
   `runner.py` in the boot scripts. Only the paper daemon is scheduled.

Both are worth deciding on deliberately: fixing (1) means calling `set_valid_until_today()` inside
`create_tp_sl_order` (the TP/SL date field is at y≈2900, not the BUY form's y≈1550 — see
`utils/tools.py`); fixing (2) means scheduling `runner.py --phase pre-market --submit` (real money,
so it wants a dry-run gate and a kill switch first). Neither is changed here — this is a description
of the live path, and both touch real orders.
