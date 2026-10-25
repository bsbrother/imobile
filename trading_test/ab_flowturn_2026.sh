#!/usr/bin/env bash
# A/B: shipped flow strategy (control) vs the same strategy with the turning-point
# picker's picks inserted at the funding frontier (ts_flow_turning, TURN_MODE=insert).
#
# Both arms: same window, same deterministic floor (--no-search --no-ai), same panel
# cache key (arms run SEQUENTIALLY, so the second arm reads the first's warm cache
# instead of fetching a different vintage), each arm gets a PRIVATE review-adjustments
# path so a 09:2x cron tick cannot leak its paper-sim review state into a long run.
#
# The ledger (shared/db/test_imobile.db) is wiped by every run, so it is snapshotted
# into each arm's result dir immediately after that arm finishes — exit reasons
# (stop_loss / stagnation_cut / gap_exit) live only there.
set -u
cd /home/kasm-user/apps/imobile || exit 1

START=20260101
END=20260930
STAMP=$(date +%Y%m%d_%H%M%S)
LOG=logs/ab_flowturn_${STAMP}.log
CACHE_KEY=ab_flowturn_${START}_${END}
PY=".venv/bin/python"
export -n PYTHONPATH 2>/dev/null || true
unset PYTHONPATH

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

run_arm() {  # name src extra_env...
    local name=$1 src=$2; shift 2
    say "=== ARM $name (src=$src) ${*:-} ==="
    $PY trading_test/engine_preflight.py >>"$LOG" 2>&1 || { say "!! arm $name refused: another engine is running"; return 9; }
    env -u PYTHONPATH \
        REPORT_DIR=backtest/results \
        REVIEW_ADJUSTMENTS_PATH="/tmp/review_adjustments_${name}_${STAMP}.json" \
        REVIEW_OUTPUT="/tmp/review_adjustments_${name}_${STAMP}.json" \
        TP_PANEL_CACHE_KEY="$CACHE_KEY" \
        "$@" \
        $PY backtest/engine.py "$START" "$END" "$src" --no-search --no-ai >>"$LOG" 2>&1
    local rc=$?
    local dir="backtest/results/${START}_${END}_${src}"
    say "arm $name rc=$rc dir=$dir"
    if [ -d "$dir" ]; then
        cp shared/db/test_imobile.db "$dir/ledger_${name}.db" 2>/dev/null && say "ledger snapshot -> $dir/ledger_${name}.db"
    fi
    return $rc
}

say "A/B flow vs flow+turning starting; log=$LOG cache=$CACHE_KEY"

# Control: the shipped configuration, nothing else set (env -u PYTHONPATH only).
run_arm control ts_7AZ_96MA_flow_review

# Merged: extras at the funding frontier.
run_arm insert7 ts_flow_turning TURN_MODE=insert TURN_INSERT_AT=7 TURN_EXTRA_MAX=2

say "A/B done"
for d in backtest/results/${START}_${END}_ts_7AZ_96MA_flow_review \
         backtest/results/${START}_${END}_ts_flow_turning; do
    [ -f "$d/report_period_${START}_${END}.md" ] || continue
    say "--- $d"
    grep -E 'Total Return|Max Drawdown|Sharpe' "$d/report_period_${START}_${END}.md" | head -5 | tee -a "$LOG"
done
