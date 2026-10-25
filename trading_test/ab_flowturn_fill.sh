#!/usr/bin/env bash
# Second arm of the ts_flow_turning A/B: TURN_MODE=fill (append only when the base
# picker emitted FEWER names than the regime limit, i.e. a genuinely free slot).
#
# The control arm (shipped ts_7AZ_96MA_flow_review, same window, same floor) was run
# 20261025 18:45 -> 21:47 and is kept at
#   backtest/results/20260101_20260930_ts_7AZ_96MA_flow_review
# No market data updates over the weekend, so the LHB/OHLCV vintage is unchanged and
# this single arm can be compared against it.
#
# Census that motivates this arm (from the insert-at-7 run's own log): 49 of 183
# sessions had base picks < regime limit (bear 29, normal 19, bull 1), so this is the
# ONLY merge mode that can add exposure without displacing a flow pick.
set -u
cd /home/kasm-user/apps/imobile || exit 1

START=20260101
END=20260930
STAMP=$(date +%Y%m%d_%H%M%S)
LOG=logs/ab_flowturn_fill_${STAMP}.log
PY=".venv/bin/python"
unset PYTHONPATH

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "fill arm starting; log=$LOG"
$PY trading_test/engine_preflight.py >>"$LOG" 2>&1 || { say "!! refused: another engine is running"; exit 9; }

env -u PYTHONPATH \
    REPORT_DIR=backtest/results \
    REVIEW_ADJUSTMENTS_PATH="/tmp/review_adjustments_fill_${STAMP}.json" \
    REVIEW_OUTPUT="/tmp/review_adjustments_fill_${STAMP}.json" \
    TP_PANEL_CACHE_KEY=ab_flowturn_${START}_${END} \
    TURN_MODE=fill TURN_EXTRA_MAX=2 \
    $PY backtest/engine.py "$START" "$END" ts_flow_turning --no-search --no-ai >>"$LOG" 2>&1
rc=$?
dir="backtest/results/${START}_${END}_ts_flow_turning"
say "fill arm rc=$rc dir=$dir"
[ -d "$dir" ] && cp shared/db/test_imobile.db "$dir/ledger_fill.db" 2>/dev/null && say "ledger snapshot -> $dir/ledger_fill.db"
[ -f "$dir/report_period_${START}_${END}.md" ] && grep -E 'Total Return' "$dir/report_period_${START}_${END}.md" | head -2 | tee -a "$LOG"
say "fill arm done"
