#!/usr/bin/env bash
# The exact plain command, both windows, default REPORT_DIR (backtest/results).
# No env overrides at all: the 2-slot book comes from the .env section.
set -u
cd /home/kasm-user/apps/imobile || exit 1
LOG=logs/plain_turning_point_$(date +%Y%m%d_%H%M%S).log
PY=".venv/bin/python"

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "plain runs starting; log=$LOG"
for win in "20250101 20251231" "20260101 20260930"; do
    set -- $win
    say "=== $PY backtest/engine.py $1 $2 ts_turning_point --no-search --no-ai ==="
    $PY trading_test/engine_preflight.py >>"$LOG" 2>&1 || { say "!! refused: engine running"; exit 9; }
    $PY backtest/engine.py "$1" "$2" ts_turning_point --no-search --no-ai >>"$LOG" 2>&1
    rc=$?
    dir="backtest/results/${1}_${2}_ts_turning_point"
    say "rc=$rc dir=$dir"
    [ -d "$dir" ] && cp shared/db/test_imobile.db "$dir/ledger.db" 2>/dev/null
    [ -f "$dir/report_period_${1}_${2}.md" ] && grep -m1 'Total Return' "$dir/report_period_${1}_${2}.md" | tee -a "$LOG"
done
say "plain runs done"
