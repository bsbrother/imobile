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

### Honest next options (not run)

- **Multi-day exit variant** (`HOLD_DAYS_MULT` / a hold-days override): the only
  way to actually test the article's thesis.
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
