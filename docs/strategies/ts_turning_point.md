# ts_turning_point — 转折K线战法 (turning candle + one-day confirmation)

Source: `docs/strategies/turning_point.md` (今日头条 2026-05-05, 阳生说股).
Code: `backtest/strategies/ts_turning_point.py`. Registered in `backtest/engine.py`
(dispatch table + `_valid_sources`), `.env` section `ts_turning_point`.

## The idea, in one line

A single big-volume breakout candle out of a low-level consolidation is not a buy
signal — it can be a bull trap. Buy only after the **next** bar holds the breakout
level on continuing volume, with MACD freshly crossed up and Bollinger bands going
from squeeze to expansion.

## Bar semantics (T+1, no look-ahead)

```
T    = turning candle        (second-to-last closed bar of the reference window)
T+1  = confirmation candle  (LAST closed bar = the reference date)
buy  = the session after the reference date, at the open (the engine's job)
```

The engine calls the picker for session `D`; the picker resolves the reference
date `R = D-1` trading day and evaluates every condition on bars closed at `R` or
earlier. No bar from `D` is ever read.

**Deliberate deviation from the article.** The article's chart puts the buy arrow
on the confirmation candle itself (day 2). That entry is only available if you buy
at day-2's close *after* the confirmation is known, which the engine cannot model
(it buys at the next session's open, `BUY_OPEN_PRICE=true`). So this strategy buys
at the first tradeable price after the confirmation bar closes — day-3 open. The
cost is the day-2 appreciation (in the article's own example, +2.7%). Buying day-2
intraday instead would be a different, look-ahead-free-but-looser rule: "T popped
yesterday, buy on strength today", with no close-confirmation at all.

## Rule → code

| Article (定性) | Code (定量, env-gated) | Default |
|---|---|---|
| 前期低位横盘、均线黏合 | median of `(max(MA5,10,20)-min)/close` over 20 bars before T | `TP_MA_SPREAD_MAX=0.03` |
| 横盘箱体窄 | 20-bar `(high-low)/close` before T | `TP_RANGE_MAX=0.18` |
| 实体饱满 | `(close_T-open_T)/(high_T-low_T)` | `TP_BODY_MIN=0.60` |
| 放量突破 | `vol_T / mean(vol[T-5:T])` | `TP_VOL_MULT=1.5` |
| 突破短期均线压制 | `close_T > MA5, MA10, MA20` | — |
| MACD 金叉 | cross within the last N bars ending at T | `TP_MACD_LOOKBACK=2` |
| 布林带收口转开口 | `width_T > width_{T-1}` and `width_{T-1}` ≤ Pth percentile of prior 20 | `TP_SQUEEZE_PCT=0.35` |
| 第二天站稳突破位 | `close_T+1 > close_T` | — |
| 未跌破均线 | `low_T+1 >= MA5_T+1 × (1-tol)` | `TP_MA_HOLD_TOL=0.005` |
| 量能延续 | `vol_T+1 >= x × vol_T` | `TP_CONFIRM_VOL_MIN=0.80` |
| 不追高 | `close_T+1 / MA20_T+1` | `TP_MAX_EXTENDED=1.15` |
| (交易可行性) | skip if T+1 is already limit-up | `TP_LIMIT_UP_PCT=0.095` |

Universe: `no_risky_stocks()` (mainboard + ChiNext 300/301 + STAR 688, no ST/N/C),
price ≥ `TP_MIN_PRICE=3`, turnover ≥ `TP_MIN_AMOUNT=50000` 千元 (¥50M).
Output: top `TP_MAX_PICKS=12` by composite score, cut at `TP_MIN_SCORE=45`.

Composite score (0–100+) mixes body ratio, T-bar gain, volume expansion, squeeze
depth, breakout over the box high, and the confirmation gap, with regime-dependent
weights — in `bear`/`volatile` the squeeze and confirmation terms dominate, in
`bull` breakout strength does.

## Why the funnel is narrow

Per-session gate rejections are logged (`Gate rejections: ...`). A typical session
of ~5,000 stocks looks like:

```
consolidation=2,100-3,700   body=250-1,900   gain=10-550   ma_break<=10
volume<=35   macd=2-150   boll<=15   confirm_*=1-4        picks = 0-2
```

The conjunctive requirement "T is a fat bullish candle AND the 20 bars before it
were a glued-MA box" is rare by construction — that is the article's pattern, not
a bug. Consequence: with the engine's 20 position slots this strategy deploys only
a small fraction of capital, so compare it on risk-adjusted terms, not headline
return alone.

## Data access (and the bug worth remembering)

The engine spawns one subprocess per session. Fetching 60 trading days of per-date
bulk OHLCV costs ~60s per session (one cached tushare call per date) versus ~30s of
analysis, i.e. ~3h of re-reading the same overlapping window over a 9-month run. So
the picker keeps a rolling long-format panel in the cross-process pickle cache
(`tp_ohlcv_panel_v1`) and extends it by the one new date per session (~0.7s).

The first version **silently zeroed a month of a run**: a session whose reference
date moved backwards (engine preprocessing a session, standalone smoke test, `--resume`)
sliced the cached panel to that date and *stored the slice*. Every following session
then had < 50 bars per stock, every stock died on the history gate, and the run
reported "Picked 0 stocks" for 22 consecutive sessions with no error. The panel is
now a union that only grows — head gap and tail gap are both backfilled — plus a
coverage assertion that logs `ERROR` when the window is short. Regression tests:
`tests/test_turning_point.py::test_panel_*`.

`TP_PANEL_CACHE=false` disables the cache (slower, identical picks).

## Exit model is NOT the article's

The article sells a multi-week 主升浪 entry. The engine's exits come from
`config.json` `risk_reward_ratios`, where `max_hold_days` is 1 in every regime and
`take_profit_pct: 2.0` is read as a fraction — the nominal TP lands at ×3 the entry
price (a ¥5.82 entry gets a ¥17.46 TP), i.e. the bracket is decoration and trades
close on the 1-day max hold. `SL_ENABLED=false` and `REVIEW_COMPOUND_SIZING=true`
in the `.env` section keep the picker the only difference versus the production
default. Net: this measures "is the confirmed turning candle a good next-day
entry", not "does it start a 主升浪". The article's thesis needs a multi-day exit
variant to be tested at all.

## Results — 20260101 → 20260930

Run: `python backtest/engine.py 20260101 20260930 ts_turning_point --no-search --no-ai`
(181 sessions, ~73 min wall clock). Baseline = production default `ts_7AZ_96MA_flow_review`
over the *same* window with the *same* `.env` exit/sizing knobs (deduped-LHB vintage,
`SL_ENABLED=false`, `REVIEW_COMPOUND_SIZING=true`): `results/20260101_20260930_ts_7AZ_96MA_flow_review`.

| Metric | ts_turning_point | ts_7AZ_96MA_flow_review |
|---|---|---|
| Total return | **+1.24%** | **+68.42%** |
| Final equity | ¥607,412 | ¥1,010,536 |
| Max drawdown | -5.59% | -12.00% |
| Transactions | 296 (148 round trips) | 1,294 |
| Return / DD | 0.22 | 5.70 |
| vs SSE Composite (-3.19%) | +4.43pp | +71.61pp |

Pick census: 154 picks over 181 sessions — **95 sessions (52%) picked nothing**,
mean 0.85 picks/day, max 7. Average position ~¥63k against ¥600k cash, i.e. ~10%
deployment.

Trade-level (FIFO, from the run's own `transactions`):

| Metric | Value |
|---|---|
| Round trips | 148 |
| Win rate | 41.9% |
| Mean trade return | +0.07% |
| Median trade return | -0.07% |
| Total P&L | ¥7,412 |

### What this says

1. **At a 1-day horizon the confirmed turning candle has no edge** — 41.9% win
   rate, +0.07% mean per trade, i.e. indistinguishable from a coin flip once
   commission and 0.5% stamp duty are charged.
2. **The headline +1.24% is mostly the deployment ceiling, not the signal.** With
   52% of sessions picking nothing and a ¥600k account, the strategy is idle most
   of the time; its return cannot be compared to a full-slot strategy as if the
   difference were edge.
3. **The article's own claim is untested by this run.** It is a multi-day 主升浪
   thesis; the engine closes on the 1-day max hold. Monthly decomposition shows
   the same churn: +0.5% Jan, -1.4% Mar, +2.9% Apr, -1.7% Sep — the two bad months
   are what erase the four good ones.

### A/B: multi-day hold (2026-10-24)

One lever, same picker, same window, same `.env`: `HOLD_DAYS_MULT=5` → the regime's
`max_hold_days` goes 1 → 5. **The pick files are byte-identical across the two arms
(181/181)**, so the exit setting is the only difference.

| Metric | hold=1 | hold=5 |
|---|---|---|
| Total return | **+1.24%** | **-2.07%** |
| Final equity | ¥607,412 | ¥587,585 |
| Max drawdown | -5.59% | -7.56% |
| Transactions | 296 | 292 |
| Round trips | 148 | 146 |
| Win rate | 41.9% | **32.2%** |
| Mean / median trade | +0.07% / -0.07% | -0.09% / -0.07% |
| Total trade P&L | ¥7,412 | ¥-12,415 |

Monthly (hold=5): Jan -3,054 · Feb -6,736 · Mar -19,278 · Apr **+13,286** ·
May **+36,495** · Jun +2,423 · Jul -201 · Aug +12,694 · Sep **-48,044**.

Backups: `results_backups/20260101_20260930_ts_turning_point_hold1_1.24` and
`..._hold5_-2.07` (the latter carries its `ledger.db`).

**Why it got worse — and why this is NOT yet a test of "let it run".** Actual holding
periods are median 2 days / mean 4.2 days even with a 5-day cap, because the engine
cuts trades before the cap binds:

```
stagnation_days = max(3, holding_days // 2 + 1)          # engine.py:590
days_held >= stagnation_days and return < STAGNATION_CUT_LIMIT (1.0%)  -> force sell
```

So on day 3 any position up less than 1% is cut. Exit mix in the hold=5 arm
(live run DB): `stop_loss 57 (+¥31,667)`, `stagnation_cut 46 (**-¥96,180**, avg -¥2,091)`,
`gap_exit 24 (-¥5,340)`, `order_expired_before_sell 12 (+¥60,048)`, `early_cut_* 5
(-¥25,186)`, `er_trend_exit 2 (+¥22,577)`. The stagnation rule is the largest single
drain and it is what converts "hold longer" into "hold long enough to be mediocre".

(The hold=1 arm's exit mix is not recoverable: those reasons live only in the run's
DB, which the next run overwrote. Snapshot `shared/db/test_imobile.db` with each arm.)

## Improvement sweep — 20260101→20260930

Nine arms, one lever each (or one coherent group), every arm run with an isolated
`REPORT_DIR`, its own `DBTEST_IMOBILE_FILE` and a private `REVIEW_ADJUSTMENTS_PATH`.
Compare with `python trading_test/ab_compare.py <arm dirs...>`.

| # | arm | knobs | ret% | maxDD% | trades | win% | avg trade% |
|---|---|---|---|---|---|---|---|
| 0 | baseline | strict, hold 1, TP inert | +1.24 | -5.59 | 148 | 41.9 | +0.07 |
| 1 | hold5+nostag | `HOLD_DAYS_MULT=5 STAGNATION_CUT_LIMIT=-100` | -6.09 | -9.87 | 137 | 35.0 | +0.07 |
| 2 | loose | body .5, gain 2%, vol 1.3, squeeze .5, spread .04, range .22, score 40 | -2.10 | -8.58 | 330 | 38.5 | -0.22 |
| 3 | loose+hold5+nostag | 1+2 | -1.18 | -12.24 | 292 | 33.6 | -0.29 |
| 4 | bear-skip | `TP_SKIP_REGIMES=bear` | +1.05 | -5.05 | 125 | 40.0 | +0.12 |
| 5 | TP 4% | `CONFIG_FILE` with `take_profit_pct=0.04`, trailing off | **+1.54** | -5.19 | 135 | 45.2 | +0.14 |
| 6 | TP 4% + bear-skip | 4+5 | +1.52 | **-3.17** | 114 | 43.0 | **+0.18** |
| 7 | tight | gain 4%, vol 1.8, extended 1.08, score 50 | +0.19 | -3.41 | 43 | 41.9 | +0.23 |
| 8 | top-1 only | `TP_MAX_PICKS=1` | -0.59 | -4.00 | 73 | 47.9 | -0.17 |

What each lever taught:

- **Longer hold is worse, in every pairing** (-6.09 strict, -1.18 loose). With the
  stagnation cut disabled, `order_expired_before_sell` (the cap binding) lost
  -¥25,669 over 44 exits: these entries do not trend, they mean-revert.
- **Looser gates are worse**, not just diluted: 330 trades at -0.22% mean vs 148 at
  +0.07%. The strict body/volume/squeeze conjunction is what little signal exists.
- **Tighter gates are worse too** (+0.19% on 43 trades): past a point the filter
  removes the winners, not the losers.
- **Ranking has no information, possibly negative**: taking only the top-scored pick
  each day produced the worst per-trade mean of the sweep (-0.17%). Do not size up
  the composite score without a separate test.
- **A real take-profit is the one lever that paid**: `take_profit_pct=0.04` (the
  shipped value is read as a fraction, so it lands at ×3 price and never fires)
  lifted return +0.30pp and turned 45% of trades into wins, with `take_profit` firing
  24 times.
- **Skipping the bear regime mostly buys drawdown**: return -0.19pp, DD +0.54pp on
  its own; combined with the 4% TP it gives the same return as TP alone with DD
  -3.17% vs -5.19%.

Three refinements of the winner (all worse, 2026 window):

| arm | knobs on top of TP4+bear | ret% | maxDD% | trades | avg trade% |
|---|---|---|---|---|---|
| b2_tp4_bear (winner) | — | **+1.52** | **-3.17** | 114 | +0.18 |
| b3_h3 | `HOLD_DAYS_MULT=3` | -0.60 | -5.01 | 125 | +0.04 |
| b3_vol | `TP_VOL_MULT=1.8` | +0.22 | -4.06 | 105 | +0.08 |
| b3_squeeze | `TP_SQUEEZE_PCT=0.50` | -1.45 | -6.01 | 155 | -0.17 |

### Out-of-sample: 2025 hold-out

Same winning knobs (strict gates + TP 4% + skip bear), run on 20250101–20251231
(243 sessions, its own panel-cache key so it could not thrash the 2026 arms):

| Metric | Value |
|---|---|
| Total return | **+11.43%** |
| Max drawdown | -3.92% |
| Transactions | 820 (408 round trips) |
| Win rate | 42.2% |
| Mean trade | +0.35% |
| Total trade P&L | ¥69,227 |
| SSE Composite / CSI 300 / CSI 500 | +18.41% / +17.66% / +30.39% |

So the configuration is positive in both windows (+11.43% in a strong bull year,
+1.52% in a flat-to-down partial year) — mild evidence the per-trade edge is not
pure overfit. But it did **not** beat the index in 2025 (-6.98pp vs SSE, -18.96pp vs
CSI 500) and it only deploys ~8–10% of capital, so the returns are small in
absolute terms. Per-trade means (+0.18% / +0.35%) are thin enough that slippage and
retail commission could erase them — the same realism caveat as the production
strategy.

### Shipped configuration = the winner

The `.env` section `ts_turning_point` now ships the configuration that won both
windows, so a plain run gets it:

```bash
# TAKE_PROFIT_PCT=0.04   (real +4% bracket; shared config.json's 2.0 never fires)
# TP_SKIP_REGIMES=bear   (no entries in a bear regime)
python backtest/engine.py 20260101 20260930 ts_turning_point --no-search --no-ai
```

| config | 2026-01→09 | 2025 |
|---|---|---|
| baseline (inert TP, no skip) | +1.24% / -5.59% DD | -0.30% / -6.24% DD |
| **shipped (TP 4% + bear-skip)** | **+1.78% / -3.90% DD** | **+11.43% / -3.92% DD** |

Both shipped runs are solo runs of the `.env` section (no `CONFIG_FILE`), with the
per-phase ledger and the results dir as the artifacts: 2026 = 125 round trips, 44.8%
wins, +0.27% mean trade; 2025 = 408 round trips, 43.4% wins, +0.43% mean.

> **Correction to the sweep table above.** The winner row there reads +1.52%
> because those nine arms ran four-at-a-time sharing one panel-cache key; the
> concurrent writers could drop each other's backfill, and 9 sessions of the 2026
> arm picked **nothing** where a solo run picks 1–2 names (today's picks are a
> strict superset in all 181 sessions — verified pick-file by pick-file). The arm
> was starved, not the config weaker: re-run solo, the same knobs give **+1.78%**
> with the same drawdown profile. Treat the other eight rows as directionally
> right but possibly a few sessions short; the ranking (strict conjunction > loose,
> 1-day > multi-day, real TP > inert TP) is unaffected, and the winner was
> re-validated solo in both windows.

Why it is not baked into `config.json`: `take_profit_pct` is shared by every
strategy, and the shipped 2.0 is deliberately inert for the ones that exit on
max_hold. `TAKE_PROFIT_PCT` (and `TP_{REGIME}`) are the strategy-scoped override
read by `utils/market_regime.py`, next to the existing `SL_{REGIME}`. Sections are
forced into the environment for their own strategy and neutralised for the others
(`utils/strategy_env.py`), so this strategy's 4% bracket cannot leak into another.

For a one-off variant without touching `.env`:

```bash
env -u PYTHONPATH .venv/bin/python trading_test/make_tp_config.py --tp 0.06 --out /tmp/tp6.json
CONFIG_FILE=/tmp/tp6.json python backtest/engine.py ... ts_turning_point --no-search --no-ai
```

Caveat on promoting it: the 4%-TP + bear-skip pair was chosen on the 2026 window
(9 arms) and confirmed on exactly one out-of-sample window (2025). Two windows is
better than one but is not a track record — if 2027 behaves differently, revisit.

### Honest next options (not run)

- **Cap that actually binds**: `HOLD_DAYS_MULT=5` **with** `STAGNATION_CUT_LIMIT`
  pushed out (e.g. `-100` disables the condition) so the 5-day cap is the exit, not
  the day-3 stagnation cut. This is the arm the A/B above still needs; the current
  result is "longer cap + unchanged stagnation rule", not "let a 主升浪 run".
- **Looser gates** (`TP_BODY_MIN=0.5`, `TP_VOL_MULT=1.3`, `TP_SQUEEZE_PCT=0.5`):
  raises frequency/deployment, but confounds "the pattern works" with "more shots".
- **As a confirmation filter** on the production CANSLIM/7AZ picks (the code's
  gates are already exposed as pure functions) rather than as a standalone picker.

## Usage

```bash
# single session smoke test (writes picks JSON to /tmp)
env -u PYTHONPATH PICK_OUTPUT=/tmp/tp.json .venv/bin/python \
    backtest/strategies/ts_turning_point.py 20260615 --no-search --no-ai

# full backtest
env -u PYTHONPATH .venv/bin/python backtest/engine.py 20260101 20260930 \
    ts_turning_point --no-search --no-ai

# tests
env -u PYTHONPATH .venv/bin/python -m pytest tests/test_turning_point.py -q
```

## Can it match `ts_7AZ_96MA_flow_review`? (gap decomposition, measured)

20260101→20260930, same window, same account size:

| | `ts_7AZ_96MA_flow_review` | `ts_turning_point` (shipped) |
|---|---|---|
| Total return | **+68.42%** | **+1.78%** |
| Max drawdown | -12.00% | -3.90% |
| Average equity | ¥821,828 | ¥613,665 |
| Average deployed | ¥354,948 (**43.2%**) | ¥93,163 (**15.2%**) |
| Capital turnover | 52.0× | 10.6× |
| Round trips | 647 | 125 |
| Edge per round trip | **+0.96%** | **+0.27%** |
| Notional per trip ÷ equity | 8.0% | 8.5% |

`return ≈ trips × edge × (notional per trip ÷ equity)`

The two strategies put the **same fraction of equity into each trade** (8.0% vs
8.5%). The 38× return gap is 5.2× more round trips × 3.6× better edge; the
deployment gap (43% vs 15%) is a *consequence* of holding ~5 concurrent names
instead of ~1.8, not a cause. Sizing was never the missing multiplier.

### Arms (all solo runs, own panel-cache key)

| arm | change | ret% | maxDD% | trips | edge% | dep% |
|---|---|---|---|---|---|---|
| shipped | — | +1.78 | -3.90 | 125 | 0.27 | 15.2 |
| hold 5 + TP 6% | `HOLD_DAYS_MULT=5 STAGNATION_CUT_LIMIT=-100 TP_*=0.06` | -4.87 | -7.98 | 116 | 0.34 | 24.0 |
| hold 5 + 4 slots | both | -4.15 | -7.43 | 111 | 0.29 | 22.7 |
| limit entry | `BUY_OPEN_PRICE=false` | +1.78 | -3.90 | 125 | 0.27 | 15.2 (inert) |
| 4 slots (knob fixed) | `MAX_POS_*=4`, 23%/pick | +3.99 | -13.73 | 101 | 0.22 | 34.9 |
| **2 slots (knob fixed)** | `MAX_POS_*=2`, 23%/pick | **+6.86** | -12.44 | 98 | **0.47** | 32.6 |

Two of those arms (`MAX_POS_*`) originally measured 1.38 / 2.62% because the knob
was inert — see the defect below. With the knob wired, concentration is worth
~4× the return at ~3× the drawdown: **+6.86% at -12.4% DD**, i.e. return/DD 0.55
against the flow strategy's 5.7. The 2-slot arm also picks up edge (0.27 → 0.47%):
with only two slots only the top-ranked names get filled, so rank does carry some
information — just not enough.

### The ceiling, in arithmetic

- The engine's hard cap is 25% of the sizing base per position. At that cap,
  ~100 trips × ~0.5% edge ≈ **7%** — what the 2-slot arm measured.
- Matching 68% at 125 trips × 0.27% would need **~160% of equity per trade**.
- Matching it at 8.5% per trade needs **~8× (trips × edge)**: e.g. 1,000 trades at
  0.5%, or 125 trades at 2.1%.

Nothing in the picker's knob space produces 8× edge, and the two levers that could
have raised frequency or edge were measured negative: looser gates -2.10% (330
trades at -0.22% mean), longer holds -4.87%. **Parity with the flow strategy is a
signal gap, not a tuning gap.**

### Recommendation

1. Use it as what it is: a low-frequency module. Shipped default = +1.78% / -3.90%
   DD; aggressive concentration (`MAX_POS_*=2`) = +6.86% / -12.44% DD. The
   aggressive form has the production strategy's drawdown for a tenth of its
   return, so it only makes sense as a small satellite allocation.
2. To actually bank these entries, **merge them into the flow pipeline** (flow
   picks first, turning-point picks filling free slots). That is the only path
   where they add to the 68% base instead of competing with it. Not built here —
   it is a new strategy wrapper, not a knob.

### Defect found and fixed on the way

`backtest/cli.py` computed `per_slot_cash = sizing_base / slots` from a hardcoded
regime map (12/10/8/5) while `engine.py` logged its limit from
`MAX_POS_BULL/NORMAL/VOLATILE/BEAR`. A run with `MAX_POS_BULL=4` therefore logged
"4 positions" and still bought 600k/12 ≈ ¥50k per name. The CLI map now reads the
same env knobs with the same defaults (no behaviour change when unset), verified
by order size (¥50k → ¥150k inside the 25% cap) and guarded by
`tests/test_position_limit_knob.py`.
