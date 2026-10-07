# Call auction & price-limit boards — analysis and the changes it drove

Source question: [zhuanlan.zhihu.com/p/15574858619](https://zhuanlan.zhihu.com/p/15574858619) — the A-share
opening call auction, and what to do about limit-up / limit-down boards.

Three questions, answered against this repo's own data, then the code and live-path changes that
follow from the answers. Everything below is measured, not asserted: reproduce commands are inline.

---

## 0. The mechanism (what the exchange actually does)

| window | what is allowed | what the displayed price means |
|---|---|---|
| 09:15–09:20 | place **and cancel** | **can be spoofed** — large players place and pull to probe demand |
| 09:20–09:25 | place only, **cancel locked** | committed demand; this book converges to the open |
| 09:25:00 | the book matches once, at ONE price | **that price is the opening price** |
| 09:25–09:30 | orders accepted, **not submitted** to the exchange | queue for the 09:30 continuous session |

Two consequences drive everything below:

1. A quote *read* before 09:20 is evidence of nothing. A quote read at 09:20–09:25 is real, and so is
   any order placed then — which is why an order submitted in 09:20–09:25 fills at the open.
2. 09:25–09:30 orders **do not join the auction**. They buy a place in the 09:30 queue, not the
   auction's single price.

---

## 1. Is 09:20–09:25 better than 09:15–09:20?

Better, for two independent reasons, and the repo was on the wrong side of the first one.

- **Signal quality.** Only after 09:20 is the indicative price real. Before that, an order sized off
  the quote is sized off a number that can be withdrawn (`backtest/utils/limit_board.py` documents the
  same for the board gate).
- **Participation** is identical: both windows submit before the 09:25 match, so both fill at the open.

The live path read a quote and *waited until 09:15* (`TRADING_SUBMIT_BY` default `0915`) — i.e. it read
the price at the one moment it was least trustworthy, while gaining nothing on participation.

**Change:** `TRADING_SUBMIT_BY` default is now **`0920`**, and a new `TRADING_QUOTE_TRUST_FROM`
(default `0920`) makes the price helpers refuse a pre-lock quote outright: the order is skipped, not
submitted on a spoofable number (`trading/runner.py`, `_quote_is_trustworthy`). `limit_up` mode is
exempt — it reads no quote at all, so there is nothing to spoof.

## 2. Why not just submit at 09:25–09:30, since it also sits at the open?

Because it does **not** sit at the open. That window's orders miss the auction; they enter the 09:30
continuous book and fill at whatever the market is then — the first prints of an untraded session,
which is a strictly worse and unbounded comparison against the auction's single price.

But the question has a correct reading, and it is worth having as an explicit mode: waiting for the
09:25 print gives you the real open *before* committing, at the cost of your auction slot. That is a
genuine trade — information for participation — so it is now a first-class option rather than an
accident:

**Change:** `TRADING_SUBMIT_MODE=confirmed_open` waits for the 09:25 print, then per symbol reads
`get_confirmed_open(code)` and gives up any BUY that (a) sits on a limit board, or (b) gaps beyond the
regime's `max_open_gap_pct`; the survivors are submitted in 09:25–09:30 with a bid just over the open.
Default stays `auction` (09:20–09:25, fills at the open — the fill the backtest models).

## 3. Limit-up / limit-down boards: keep going or avoid?

**Avoid the BUY, keep the exit — and the exit may be impossible.**

The board states, and what each means for execution:

| state | definition | BUY | SELL |
|---|---|---|---|
| `normal` | trades inside the band | allowed | allowed |
| `up_sealed` | `low >= limit-up` | **refused** — no sellers | allowed, fills at the band top (good for us) |
| `up_open` | `open >= limit-up` | **refused** — entry at the top of the band | allowed |
| `down_sealed` | `high <= limit-down` | **refused** — crash open, premise void | **impossible** — no buyers; position is trapped |
| `down_open` | `open <= limit-down` | **refused** — crash open | possible if it unseals |

Bands: 10% main board, 20% 创业板 300/301 & 科创板 688/689, 30% 北交所, 5% ST (not derivable from the
code alone — ST is the documented blind spot, and it errs toward allowing a trade the gap cap then
catches).

The backtest already had **half** of this, both halves hardcoded and inline: a limit-UP open refused on
the buy side (`engine.py:1810`) and a sealed limit-DOWN refused on the sell side (`engine.py:1406`).
Missing: the crash-open BUY (a knife-catch the momentum premise never contemplated), the 30%/689 bands,
and **the entire live path**, which had no board awareness at all and would happily bid into a sealed
board.

### What it changed, measured

```
.venv/bin/python trading_test/check_boards.py --range 20260101 20260930 \
    --orders-dir backtest/results/20260101_20260930_ts_7AZ_96MA_flow_review --population 120
```

Over 163 sessions / 1052 planned orders:

| state | count |
|---|---|
| normal | 1034 |
| up_open | 2 |
| down_sealed | 1 |
| down_open | 1 |
| up_sealed | 0 |
| no cached bars | 14 |

- `601872.SH` 20260224 and `002384.SZ` 20260408 — opened +10.00%: already refused by the legacy guard.
- `002837.SZ` 20260421 and `603256.SH` 20260601 — opened −10.00%: **the new BUY guard refuses these
  two.** They were previously bought into a crash open.
- Control (the classifier can see boards): the same sessions across a sample of the whole cache give
  **105 / 38794 (0.3%)** symbol-days on a board, including 28 `up_sealed` and 22 `down_sealed`. So the
  near-zero rate in the order universe is a real finding, not a broken classifier.

**Verdict:** on this sample the board policy changes 2 orders out of 1052 (0.2%). It is a correctness
guard for the tail — a sealed board or a crash open — not a performance lever. It costs nothing to
carry and removes a class of fictional fill.

---

## 4. Where the rule has to live: the fill PRICE

Rule as stated: **at the limit-up you cannot buy; at the limit-down you cannot sell.** That is a
statement about the *price a fill prints at*, not about the session, and the difference decides the
implementation:

- a stop that gaps to a limit-down **open** cannot fill — its price IS the limit-down;
- a take-profit the stock reaches **later the same session** CAN fill, even though the session
  opened at the limit-down, because the price lifted off the floor first.

So a session-level "down_open → no exit" rule is wrong in the second case and a session-level
"sealed → no exit" rule misses the first. The check therefore has to run against the price each
branch would actually book, which is why it is a parameter of the fill:

    fill_block_reason('buy',  buy_price,  prev_close, symbol)   -> refuse when >= limit-up (no sellers)
    fill_block_reason('sell', sell_price, prev_close, symbol)   -> refuse when <= limit-down (no buyers)

`execute_buy_order` and `execute_sell_order` call it at their top, so no call site can book an
impossible fill by forgetting an earlier check. `prev_close` is passed by the caller (it is in scope
in `check_order_execution`, which owns all 7 call sites — 6 sell branches + the buy).

Bands are tick-rounded as the exchange publishes them, so the comparison is exact:
`12.34 × 1.10 → 13.57`, and 13.57 is a limit-up while 13.56 is not.

### Was the backtest doing it right? An audit of every booked fill

```
.venv/bin/python trading_test/check_executed_sells.py --range 20260101 20260930
```

1534 executed trades from `transactions` (767 buys, 767 sells):

| check | count |
|---|---|
| SELLs booked at/below the limit-down (no buyers — impossible) | **1** |
| BUYs booked at/above the limit-up (no sellers — impossible) | 0 |

The one:

| date | code | name | booked | prev close | limit-down |
|---|---|---|---|---|---|
| 20260422 | 002837.SZ | 英维克_expired | 98.07 | 108.97 | 98.07 |

The scheduled exit (`_expired`) sells at the open; that open **was** the limit-down, so the engine
booked a sale to a buyer who did not exist. The position was locked. With the rule in place that
branch is refused and the position carries to the next session. Note the same name appears on
20260421 in the board measurement above (`down_sealed`) — the buy on that day is refused by the
policy guard and the exit on the next day by the price rule, which is the same board being handled
consistently at both ends.

Buys audit clean: the existing open-vs-limit-up pre-check already covered them, which is why the
buy-side guard inside `execute_buy_order` is defence in depth rather than a behaviour change.

**So the honest answer to "is the backtest doing it right": no, not quite — one impossible exit in
767 sells (0.13%). It is now refused, and the count is reproducible.**

---

## 5. Coverage: the rule on all three paths

| path | BUY at the limit-up (no sellers) | SELL at the limit-down (no buyers) |
|---|---|---|
| **backtest fills** (`backtest/engine.py`) | refused — session guard before the order exists, then `fill_block_reason('buy', ...)` inside `execute_buy_order` | refused — sealed-session guard, then `fill_block_reason('sell', ...)` inside `execute_sell_order` |
| **live** (`trading/runner.py`) | `_auction_buy_price` / `_confirmed_open_buy_price` return `None`, so the order is **skipped** (`⏭️ SKIP BUY ...`) | cannot fill (the exchange has no counterparty); the live quote **and** the order's own triggers are checked, warned, and the position carries |
| **live** (`trading/pre_market_run.py`) | skipped on the board verdict, gated on the confirmed 09:25 open **or the post-09:20 indicative quote** | same floor check before the force-sell bracket |
| **replay** (`trading_test/`) | `check_fills.py` BAND check | `check_fills.py` BAND check |

The asymmetry is the point. The backtest **books** trades, so it must actively refuse an impossible
fill. The live path cannot make one — the exchange won't match a buy with no sellers or a sell with no
buyers — so its job is to not *submit* a buy it cannot get and to tell the operator when an exit is
trapped. Both halves of the same rule, and each is now pinned by a test.

### The band logic was copied, and the copies disagreed

Closing this properly meant sweeping for hardcoded band literals, and there were more than expected —
**six sites across four files**, each written as `startswith('3') or startswith('688')` and so each
missing 689 STAR and 30% 北交所:

| where | what it did |
|---|---|
| `backtest/utils/limit_board.py` | now the only definition |
| `backtest/engine.py` (×2) | `widen_pct` in the live order-emission block |
| `backtest/engine.py` (×2) | the legacy literals inside `LIMIT_BOARD_GUARD=0` — **kept on purpose** |
| `backtest/cli.py` (×2) | the bull/bullish gap caps that size the planned buy price |
| `trading/pre_market_run.py`, `trading_test/check_fills.py` | limit-up price / band check |

Every one now reads `board_band()`. The only literals left in the repo are the two legacy fallbacks,
which is what makes `LIMIT_BOARD_GUARD=0` a faithful reproduction of the old behaviour rather than an
approximation.

### Two live gaps found while verifying this

Both were in `pre_market_run.py`, the second live entry point:

1. **The 09:20 window was unchecked.** `get_confirmed_open` returns `None` before 09:25 by design, and
   the board gate sat inside `if confirmed_open is not None` — so during the *normal* submission window
   it never ran, and orders went to the band top with no board check. It now falls back to the live
   indicative quote, and only from `TRADING_QUOTE_TRUST_FROM` (09:20), after the cancel lock. With no
   trustworthy price at all it says so and submits at the band top, which cannot fill while a board is
   locked.
2. **Its force-sell bracket had no floor check.** A stop at/below the day's limit-down was logged as a
   liquidation when it is in fact a trapped position — now warned, mirroring `runner.py`.

### Verification per path

- **backtest** — `trading_test/check_executed_sells.py`: 0 impossible buys, 1 impossible sell out of
  767 (found, now refused).
- **live** — `test_both_rules_are_enforced_on_both_paths` pins all four cells above, plus the skip
  ordering and the trust window.
- **replay** — `trading_test/check_fills.py --run strategy-only|with-history`: 0 failures, including
  the new BAND check (unit-pinned by `test_realizability_refuses_a_fill_printed_exactly_on_the_band`).

### The bigger finding this measurement exposed

461 of 1052 orders (**43.8%**) had a planned limit BELOW the previous close — the engine buys the dip,
mean 7.54% under. The backtest fills a BUY only when `open <= planned limit`, so **every day that opens
above the previous close cannot fill in the backtest.**

In a call auction you pay the clearing price, not your limit. So a live bid at the indicative quote
(`indicative` mode) *or* the whole band (`limit_up`, what `.env` currently sets) **fills all of those
gap-up days anyway, at the open.** The live book is therefore buying the 43.8% of days the backtest
refused, by construction. That is a structural live-vs-backtest divergence, larger than the board
question that led to it — and it is in the direction that hurts.

**Change:** a third mode, `TRADING_BUY_LIMIT_MODE=plan_capped`, which never bids above the engine's own
planned limit, so live trades the list the backtest traded. The price is matching misses instead of
living with unexplained extra fills. Default is unchanged (`indicative`); `.env` currently sets
`limit_up` and is left as-is — **switching it changes real orders and needs a decision.**

---

## What changed, in code

| file | change |
|---|---|
| `backtest/utils/limit_board.py` | **new** — tick-rounded bands, `board_state()`, `buy_block_reason()`, `sell_block_reason()`, and the fill-price rule `fill_block_reason()`; one shared implementation for backtest and live, with `board_guard_enabled()` as the single switch |
| `backtest/engine.py` | session guards use the shared policy via `board_state` inside `check_order_execution` (adds the crash-open BUY refusal and the 30%/689 bands), **and** `execute_buy_order`/`execute_sell_order` enforce the fill-price rule so no call site can book a fill at the band. All 7 call sites pass `prev_close`. `LIMIT_BOARD_GUARD=0` restores the legacy inline behaviour for an A/B |
| `trading/runner.py` | `TRADING_SUBMIT_BY` 0915→**0920**; new `TRADING_QUOTE_TRUST_FROM`, `TRADING_SUBMIT_MODE` (`auction`\|`confirmed_open`); `_auction_buy_price` now returns `(price, reason)` and returns `None` to **skip** a board/gap-refused order; new `plan_capped` bid mode; sealed-board warning before an exit that cannot fill |
| `trading/pre_market_run.py` | replaced its own copy of the band table with `board_band()`; added the board gate on the confirmed open **and on the post-09:20 indicative quote** (it is the 09:20 window that matters, and the confirmed open does not exist then), plus the limit-down floor check before its force-sell bracket |
| `tests/test_limit_board.py` | **new** — 19 tests for bands, states, verdicts, the fill-price rule, the guard switch, and the degenerate-input paths |
| `tests/test_live_path_wiring.py` | +14 tests: the trust window, board refusal, `plan_capped`, submit-loop skip ordering, the fill-level rule and its call-site count, and source guards that fail loudly if any of this is undone; one existing test re-pinned from the 09:15 default to 09:20 |
| `trading_test/check_boards.py` | **new** — the exposure measurement above (pick files, executed book, population control) |
| `trading_test/check_executed_sells.py` | **new** — the audit of every booked fill in `transactions` against its day's band |
| `trading_test/check_fills.py` | its own band copy replaced with `board_band()`; new BAND check — a fill printed exactly ON the band is refused, not just one outside it |
| `backtest/utils/tp_sl.py` | **new** — the held-position bracket policy (`adaptive_tp_sl`), moved out of `engine.py` so the live path recomputes the same levels |
| `backtest/utils/limit_board.py` | + `gap_exit_reason()` / `gap_exit_enabled()` / `gap_exit_near_pct()` — the pre-open force-exit rule (at/below the stop, within 0.5% of it, or at the floor) |
| `trading/runner.py`, `trading/pre_market_run.py` | the same rule at submit time: the bracket is replaced by a sell at the confirmed 09:25 print when the print has already reached the stop |

Verification: `make test` → **258 passed**, 21 skipped, 2 xfailed, plus the one pre-existing failure
(`test_rps_threshold_is_80`: `.env` sets `TS7AZ_RPS_MIN=60` against the test's 80). The new coverage
includes the submission target (`_submit_target_secs()` = 09:25:05 by default), the dormant window, the
scheduled-exit re-price, and the backtest's gapped-through exit rule. `trading_test/check_boards.py` output:
`trading_test/results/limit_board_exposure.md`.

---

## 6. The 09:25 auction price is the execution reference — now the default

Directive: **09:15-09:25 does nothing; every order is created and submitted in 09:25-09:30, priced off
the 09:25 auction print** — which is the 09:30 open, so both paths transact at the same number.

| | backtest | live |
|---|---|---|
| BUY price | `BUY_OPEN_PRICE=true` (default, and set in `.env`): fills at the open = the auction price, unconditionally | bid = confirmed open + `TRADING_AUCTION_BUFFER_PCT`, capped at the real limit-up |
| SELL when the market opened **through** the trigger | **new default**: fills at the open (`SELL_AT_OPEN_WHEN_GAPPED`) | a scheduled exit is re-priced at the confirmed open (the plan had it at the previous close) |
| SELL at the trigger (no gap through) | fills at the trigger — unchanged | bracket levels are strategy levels relative to cost — untouched, still limit-down checked |
| submission time | n/a — daily bars, the open *is* the auction result | 09:25:05 (`TRADING_AUCTION_CONFIRM_AT` + 5s); was 09:20 |

**Why the sell change matters.** Under the old rule a stop at 97.00 filled at 97.00 even when the
stock opened at 90 — booking a price the market had already left. `SELL_AT_OPEN_WHEN_GAPPED=0`
restores it for an A/B. This was not a marginal effect: comparing the 20260101-20260930 run against
the pre-change `..._compound_193.54` backup, **306 of the 633 sells both runs made were re-priced,
298 of them stop-losses, essentially all downward (worst −12.16%), for −¥517,303 on those trades
alone** — and the difference compounds from there. Reported return went 193.54% → 34.61%. The new
fills equal the day's real opening price to the tick (20260702 301308.SZ: 613.16 = the open), while
several old fills sat **above the entire day's high** (698.05 vs a high of 636.99; 609.74 vs 566.66;
380.37 vs 370.89) — i.e. sells at prices the stock never traded.

**Do not benchmark against `..._193.54` again.** It is an impossible baseline, not a target: it
assumed every stop filled at its trigger, so the strategy never lost more than its stop even on a
gap-down open. Re-baseline on a post-change run. Recovering that edge is a real research question
(overnight gap risk, position sizing, gap-aware stops) — not a reason to restore the old fill model.

**Why the live change costs nothing in fill quality.** An order created at 09:25:05 sits at the front
of the 09:30 continuous-session queue, and the 09:25 auction print *is* the 09:30 open to within a
tick. So it fills at the number the auction produced without having to guess at it during 09:15-09:25 —
the window in which the indicative quote can still be placed and pulled.

**Flags.** `TRADING_SUBMIT_MODE` (default `confirmed_open`; `auction` = legacy),
`TRADING_AUCTION_CONFIRM_AT` (0925), `TRADING_AUCTION_BUFFER_PCT` (0.005), `SELL_AT_OPEN_WHEN_GAPPED`
(default on; `SELL_OPEN_PRICE=false` also restores the legacy sell). `TRADING_SUBMIT_BY`,
`TRADING_QUOTE_TRUST_FROM` and `TRADING_BUY_LIMIT_MODE` now apply to the legacy `auction` mode only.

## Live-path settings to decide (each changes real orders)

| env | now | options |
|---|---|---|
| `TRADING_SUBMIT_MODE` | `confirmed_open` — **new default** (09:25:05 → 09:25-09:30, priced off the auction print) | `auction` = legacy: submit by `TRADING_SUBMIT_BY` and bid into the match itself |
| `TRADING_SUBMIT_BY` | `0920` | legacy `auction` mode only — post-cancel-lock is strictly better than 09:15 |
| `TRADING_BUY_LIMIT_MODE` | `limit_up` in `.env` | legacy `auction` mode only: `indicative` for near-quote fills, `plan_capped` to trade the backtest's list |
| `SELL_AT_OPEN_WHEN_GAPPED` | on — **new default**; a trigger the market opened through fills at the open | `0` (or `SELL_OPEN_PRICE=false`) reproduces the old optimistic trigger-price fill |
| `LIMIT_BOARD_GUARD` | on (default) | `0` to reproduce pre-change backtest results |

`.env` still pins nothing for `TRADING_SUBMIT_MODE` / `SELL_AT_OPEN_WHEN_GAPPED`, so both new defaults
are live as soon as the code runs. The one thing `.env` does pin is `SELL_OPEN_PRICE=true`, which under
the new reading means the modern behaviour rather than the legacy one; `SELL_OPEN_PRICE=false` is what
restores the legacy fill.

## 7. Re-calibrating a holding's bracket before the open, and force-exiting what the print already reached

Directive: each trading date, recompute the held positions' TP/SL **before 09:25**, and if the 09:25-09:30
print (the open) is at, nearly at, or below the stop — or at the limit-down — force the SELL.

**The re-calibration already existed; it now has one home.** `_adaptive_tp_sl` (breakeven shield once the
position is in profit, trailing stop, take-profit ratchet by days held, all clamped) runs for every holding
before each session. It moved from `backtest/engine.py` to `backtest/utils/tp_sl.py` so the live path
computes the same levels rather than drifting from them — one bracket implementation, both paths. Every
level it produces still passes the limit-down floor check, because a stop below the floor is not a stop.

**The force-exit is new**, and it is a statement about *where the day's first price sits*, not about the
session's range:

| open (the 09:25 print) | action |
|---|---|
| at/below the stop | exit at the open — the stop's own price is fiction once the market opened past it |
| within `GAP_EXIT_NEAR_PCT` (0.5%) above the stop | exit at the open — one tick from stopped, and the day has not started |
| at/below the limit-down | exit **requested**, and `fill_block_reason` refuses it: no buyers, so the position carries |
| above the take-profit | the existing gap-up take-profit, unchanged |

Previously the exit branch only ran when the session's range touched a trigger, so a position that opened
just above its stop and then recovered was simply held — the day's later path decided, not the print.

Measured, 20260101-20260930 (`logs/rerun_gap_exit_20261003.log`):

| forced-exit decisions | 392 |
|---|---|
| open at/below the stop | 327 |
| open **within 0.5%** above it | **65** |
| open at/below the limit-down | 0 |

| | baseline (no rule) | with the rule |
|---|---|---|
| total return | 34.61% | **38.27%** |
| final value | ¥807,668.98 | ¥829,642.50 |
| transactions / sells | 1396 / 698 | 1410 / 705 |

+3.66 points, +¥21,973.52 over 181 sessions: 82 days better, 53 worse, 46 flat; best day +¥14,727.04
(20260528), worst −¥13,070.93 (20260608). Both runs: strategy `bear`, max hold 1 day, ¥600,000 initial
capital.

**Caveat, and it is not cosmetic.** The engine's per-day AI/search stage is not deterministic, so part of
that delta may be run-to-run variance rather than the rule. The control that separates them is a same-code
re-run, and the two runs' pick files have not been compared. Treat 38.27% as "the rule plus whatever the
models did differently", not as a clean isolated effect.

Flags: `GAP_EXIT=0` disables the rule (the plain fill rules stay); `GAP_EXIT_NEAR_PCT` (default `0.005`)
sets the "nearly" band.

---

## What this does not establish

- No 09:15–09:25 auction prints exist in the cache (finest data is 30-minute bars), so the auction
  phase is modelled from the exchange rules and the day's OHLC, not observed tick by tick.
- `up_sealed` is inferred from `low >= limit-up`; a board that seals mid-session and opens and closes
  off the limit may classify as `normal`.
- ST names' ±5% band is not derivable from the symbol; they classify on their board's nominal band.
- 14 of 1052 order-days have no cached bars and are counted separately, never as `normal`.
