#!/usr/bin/env bash
# 2024 arms, sequential (they share the ledger — concurrent runs wipe each other's book).
#
# Feeds the out-of-sample test of the momentum switch: the rule was found on 2025-01..2026-09,
# so a 2024 replay is the first evidence about it that isn't fitted. Both arms run with their
# own config (turning: 2-slot section; flow: engine defaults), private review state, and a
# private panel cache key; the ledger is snapshotted after each arm because the next run
# wipes it.
set -uo pipefail
cd "$(dirname "$0")/.."

STAMP=$(date +%Y%m%d_%H%M%S)
LOG=logs/arms_2024_${STAMP}.log
OUTDIR=backtest/results
mkdir -p logs "$OUTDIR"
exec > >(tee -a "$LOG") 2>&1

echo "== $(date '+%F %T') 2024 arms start (log $LOG)"
.venv/bin/python trading_test/engine_preflight.py || { echo "engine busy — abort"; exit 1; }

run_arm () {   # $1 strategy  $2 short name
    local src=$1 name=$2 dir="$OUTDIR/20240101_20241231_$1"
    [ -d "$dir" ] && mv "$dir" "${dir}_prev_${STAMP}"
    local start=$(date +%s)
    echo "== $(date '+%F %T') arm $name ($src) start"
    PYTHONPATH= \
    REPORT_DIR="$OUTDIR" \
    REVIEW_ADJUSTMENTS_PATH="/tmp/review_adjustments_2024_${name}_${STAMP}.json" \
    REVIEW_OUTPUT="/tmp/review_adjustments_2024_${name}_${STAMP}.json" \
    TP_PANEL_CACHE_KEY="arm2024_${name}_${STAMP}" \
        .venv/bin/python backtest/engine.py 20240101 20241231 "$src" --no-search --no-ai
    local rc=$?
    echo "== $(date '+%F %T') arm $name rc=$rc elapsed=$(( $(date +%s) - start ))s dir=$dir"
    cp shared/db/test_imobile.db "$dir/ledger.db" 2>/dev/null && echo "== ledger snapshotted ($name)"
    grep -m1 'Total Return' "$dir"/report_period_*.md 2>/dev/null
}

run_arm ts_7AZ_96MA_flow_review flow
run_arm ts_turning_point turning

echo "== $(date '+%F %T') 2024 arms done"
