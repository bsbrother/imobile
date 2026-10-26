#!/usr/bin/env bash
# 2025 A/B for ts_turning_point, run as ONE batch so both arms share the same data
# vintage (the project's own rule: quote a delta only against a control measured in
# the same batch).
#
#   arm A  conc2   MAX_POS_*=2  — the +6.86% / -12.44% DD arm from the 2026 sweep,
#                                 never validated out of sample
#   arm B  shipped current .env section (TAKE_PROFIT_PCT=0.04, TP_SKIP_REGIMES=bear)
#                                 as the same-vintage control against
#                                 backtest/results/20250101_20251231_ts_turning_point_prev1025
#                                 (+11.43%, measured 2026-10-25)
#
# Each arm gets its own TP_PANEL_CACHE_KEY (concurrent arms corrupt each other via the
# shared panel; sequential ones would be fine, but keeping them separate also keeps each
# arm's panel from silently serving the other's backfill) and its own ledger snapshot.
set -u
cd /home/kasm-user/apps/imobile || exit 1

START=20250101
END=20251231
STAMP=$(date +%Y%m%d_%H%M%S)
LOG=logs/ab_turning_2025_${STAMP}.log
PY=".venv/bin/python"
unset PYTHONPATH

say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

run_arm() {            # name  extra_env...
    local name=$1; shift
    say "=== ARM $name ${*:-} ==="
    $PY trading_test/engine_preflight.py >>"$LOG" 2>&1 || { say "!! arm $name refused: engine running"; return 9; }
    env -u PYTHONPATH \
        REPORT_DIR=backtest/results \
        REVIEW_ADJUSTMENTS_PATH="/tmp/review_adjustments_t2025_${name}_${STAMP}.json" \
        REVIEW_OUTPUT="/tmp/review_adjustments_t2025_${name}_${STAMP}.json" \
        TP_PANEL_CACHE_KEY="turning_2025_${name}_${STAMP}" \
        "$@" \
        $PY backtest/engine.py "$START" "$END" ts_turning_point --no-search --no-ai >>"$LOG" 2>&1
    local rc=$?
    local dir="backtest/results/${START}_${END}_ts_turning_point"
    say "arm $name rc=$rc dir=$dir"
    if [ -d "$dir" ]; then
        cp shared/db/test_imobile.db "$dir/ledger_${name}.db" 2>/dev/null && say "ledger -> $dir/ledger_${name}.db"
        mkdir -p "backtest/results/${START}_${END}_ts_turning_point_${name}"
        cp -a "$dir"/. "backtest/results/${START}_${END}_ts_turning_point_${name}/" 2>/dev/null
        say "arm artifacts copied to ${START}_${END}_ts_turning_point_${name}"
    fi
    return $rc
}

say "2025 pair starting; log=$LOG"
run_arm conc2 MAX_POS_BULL=2 MAX_POS_NORMAL=2 MAX_POS_VOLATILE=2 MAX_POS_BEAR=2
run_arm shipped
say "2025 pair done"
