#!/usr/bin/env bash
# Same arm as the validated 2025 run (MAX_POS_*=2) on 2026-01->09, so the two windows
# are measured the same way in the same vintage. Reference points on disk:
#   backtest/results/20260101_20260930_ts_turning_point_prev1025  shipped config  +1.78% / -3.90% DD
#   backtest/results/20250101_20251231_ts_turning_point_conc2    MAX_POS_*=2 2025 +37.98% / -5.33% DD
set -u
cd /home/kasm-user/apps/imobile || exit 1

START=20260101
END=20260930
STAMP=$(date +%Y%m%d_%H%M%S)
LOG=logs/ab_turning_2026_conc_${STAMP}.log
PY=".venv/bin/python"
unset PYTHONPATH

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "2026 concentration arm starting; log=$LOG"
$PY trading_test/engine_preflight.py >>"$LOG" 2>&1 || { say "!! refused: another engine is running"; exit 9; }

env -u PYTHONPATH \
    REPORT_DIR=backtest/results \
    REVIEW_ADJUSTMENTS_PATH="/tmp/review_adjustments_t2026conc_${STAMP}.json" \
    REVIEW_OUTPUT="/tmp/review_adjustments_t2026conc_${STAMP}.json" \
    TP_PANEL_CACHE_KEY="turning_2026_conc_${STAMP}" \
    MAX_POS_BULL=2 MAX_POS_NORMAL=2 MAX_POS_VOLATILE=2 MAX_POS_BEAR=2 \
    $PY backtest/engine.py "$START" "$END" ts_turning_point --no-search --no-ai >>"$LOG" 2>&1
rc=$?
dir="backtest/results/${START}_${END}_ts_turning_point"
say "2026 conc arm rc=$rc dir=$dir"
if [ -d "$dir" ]; then
    cp shared/db/test_imobile.db "$dir/ledger_conc2.db" 2>/dev/null && say "ledger -> $dir/ledger_conc2.db"
    mkdir -p "backtest/results/${START}_${END}_ts_turning_point_conc2"
    cp -a "$dir"/. "backtest/results/${START}_${END}_ts_turning_point_conc2/" 2>/dev/null
    say "artifacts copied to ${START}_${END}_ts_turning_point_conc2"
    grep -E 'Total Return' "$dir/report_period_${START}_${END}.md" | head -2 | tee -a "$LOG"
fi
say "2026 conc arm done"
