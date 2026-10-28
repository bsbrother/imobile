#!/usr/bin/env bash
# `python backtest/engine.py ... ts_7AZ_96MA_flow_review` on both windows, default
# REPORT_DIR (backtest/results), no env overrides — the shipped flow config straight
# from .env (DEFAULT_STRATEGY section + global knobs).
#
# Waits for whatever is currently in the engine before starting (the two plain
# ts_turning_point runs), because concurrent runs share the ledger and the panel cache.
set -u
cd /home/kasm-user/apps/imobile || exit 1
LOG=logs/flow_review_2025_2026_$(date +%Y%m%d_%H%M%S).log
PY=".venv/bin/python"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

# $1 = PID of the batch that must finish first (the plain ts_turning_point runs).
# Waiting on the PID, not on "no engine process", because that batch runs two windows
# back to back and there is an idle gap between them.
WAIT_PID=${1:-}
say "queued; waiting for pid ${WAIT_PID:-<none>} to finish; log=$LOG"
for _ in $(seq 1 960); do
    [ -n "$WAIT_PID" ] && ! kill -0 "$WAIT_PID" 2>/dev/null && break
    [ -z "$WAIT_PID" ] && ! pgrep -f 'backtest/engine\.py' >/dev/null 2>&1 && break
    sleep 30
done
pgrep -f 'backtest/engine\.py' >/dev/null 2>&1 && { say "!! engine still busy after 4h, giving up"; exit 9; }

for win in "20250101 20251231" "20260101 20260930"; do
    set -- $win
    say "=== $PY backtest/engine.py $1 $2 ts_7AZ_96MA_flow_review --no-search --no-ai ==="
    $PY backtest/engine.py "$1" "$2" ts_7AZ_96MA_flow_review --no-search --no-ai >>"$LOG" 2>&1
    rc=$?
    dir="backtest/results/${1}_${2}_ts_7AZ_96MA_flow_review"
    say "rc=$rc dir=$dir"
    [ -d "$dir" ] && cp shared/db/test_imobile.db "$dir/ledger.db" 2>/dev/null
    [ -f "$dir/report_period_${1}_${2}.md" ] && grep -m1 'Total Return' "$dir/report_period_${1}_${2}.md" | tee -a "$LOG"
done
say "flow_review runs done"
