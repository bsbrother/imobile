# ts_switch_momentum — does "run whichever arm has been working" generalise?

The strategy delegates each month to whichever of two arms had the better trailing month
(`ts_7AZ_96MA_flow_review` or `ts_turning_point`), using only information available at the
previous month's close (`trading_test/build_momentum_schedule.py`). It was derived on
2025-01..2026-09, so the honest test is a window the rule never saw: 2024.

Verdict: **it works in-sample and fails out-of-sample. Do not ship it as the default.**

## Results

All figures are `report_orders_*.md` NAVs from `¥600,000` start, computed with
`trading_test/compare_runs.py` (total, max drawdown) and `trading_test/decompose_switch.py`
(arm attribution per month).

### 2024 — out of sample (242 sessions, no flatten)

| run | total | max DD | return/&#124;DD&#124; |
|---|---|---|---|
| `ts_7AZ_96MA_flow_review` (always flow) | −20.18% | −27.63% | 0.73 |
| `ts_turning_point` (always turning, 2 slots) | **+18.16%** | **−8.74%** | **2.08** |
| `ts_switch_momentum` | −13.42% | −23.65% | 0.57 |

The switch loses **31.6 pp** to the arm it was supposed to pick. It is not a narrow miss:

- 2024-01 flow −6.11% vs turning +0.00% (rule had "no history", defaulted to flow)
- 2024-08 flow −14.68% vs turning −1.35% — the single worst month, taken in the wrong arm
- 2024-12 turning −7.52% vs flow +0.45%
- Sum of monthly gap to the better arm: **+40.5 pp** given away.

2024 was a year where the trailing-month winner kept mean-reverting, which is exactly the
failure mode of a momentum selector. The rule's five arm changes all pointed the wrong way
across the turn.

### 2025-01 → 2026-09 — the window the rule was derived on (424 sessions, flatten on)

| run | total | max DD | return/&#124;DD&#124; |
|---|---|---|---|
| `ts_7AZ_96MA_flow_review` (always flow) | +40.89% | −28.37% | 1.44 |
| `ts_turning_point` (always turning, 2 slots) | +47.46% | −13.27% | 3.58 |
| `ts_switch_momentum` + flatten | **+56.56%** | **−12.38%** | **4.57** |

Switch by year: 2025 **+14.83%**, 2026-01→09 **+35.66%**. It beats the better static arm by
**9.1 pp** with a slightly smaller drawdown, and it gave away 60.6 pp of monthly oracle — the
rule captures enough of the right arm, often enough, on the data it was fitted to.

Note the shape: 2025 the rule underperformed turning (+14.8% vs +37.0%) because it rotated
into flow for Jul–Sep 2025, which lost money; 2026 it made that back by holding flow through
the turning arm's dead year (+35.7% vs +6.0%).

## Why the 2024 result is not an execution artefact

The two static arms inside the switch run at their own sizing — the per-day limit follows the
delegated arm (`Orders Executed: 2/2` in turning months, `11/16` in flow months), not a fixed
switch-wide book. The month-by-month gap to the standalone arm is a few tenths of a point
(review state is shared inside a switch run), while the losses that sank 2024 are whole-arm
choices: January (wrong default) and August (flow −14.7% against turning −1.4%).

One confound remains: 2024 ran without `SWITCH_FLATTEN`, 2025-26 with it. The flatten only
ever *helps* (it closes an inherited 2-slot book so the new arm can re-slot into more
positions), so it cannot turn −13.4% into +18%; but it does mean the 2024 number is the
no-flatten variant.

## Operational notes

- `--resume` continues an interrupted switch run: existing `report_orders_*.md` are skipped
  and the ledger is preserved, so a killed run costs minutes, not a night. `run_switch.sh
  resume` reuses the schedule, the review-state file, the panel cache key and the (append-only)
  decisions csv, so a resumed month decides exactly as the original would have.
- The flatten guard reads the current month's existing reports before firing
  (`backtest/engine.py::_switch_flatten_today`). Without that, the first resumed session of a
  month re-fires the boundary flatten mid-month and liquidates a book the boundary already
  reset — that bug was found and pinned by `tests/test_switch_flatten.py` during the resume.
- Reproduce:

```
bash trading_test/ab_2024_arms.sh          # static arms, 2024
bash trading_test/run_switch.sh holdout    # switch, 2024, no flatten
bash trading_test/run_switch.sh flatten    # switch, 2025-01..2026-09, flatten
bash trading_test/run_switch.sh resume     # after an interruption
python trading_test/compare_runs.py 20240101_20241231_ts_switch_momentum \
    20240101_20241231_ts_7AZ_96MA_flow_review 20240101_20241231_ts_turning_point
python trading_test/decompose_switch.py --audit /tmp/momentum_decisions_flatten_*.csv
```

## Decision

`ts_7AZ_96MA_flow_review` stays the default. `ts_switch_momentum` stays in the tree as an
opt-in experiment, with `SWITCH_FLATTEN` default-off: the in-sample gain (+9 pp) is smaller
than the out-of-sample loss (−31 pp), and the failure is structural (a trailing-month
selector has no defence against regime mean-reversion), not a tuning miss.
