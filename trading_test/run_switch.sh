#!/usr/bin/env bash
# One switch run, parameterised. Usage:
#   run_switch.sh holdout                  2024 only, no flatten (out-of-sample test)
#   run_switch.sh flatten                  2025-01..2026-09 with SWITCH_FLATTEN=1
#
# Both share the ledger, so they must never run concurrently with anything else.
set -uo pipefail
cd "$(dirname "$0")/.."

MODE=${1:-flatten}
STAMP=$(date +%Y%m%d_%H%M%S)
RESUME=0
REVIEW=""

case "$MODE" in
    holdout)
        YEARS=2024
        START=20240101; END=20241231
        DIRNAME=20240101_20241231_ts_switch_momentum
        FLATTEN=0
        DEFAULT_ARM=ts_7AZ_96MA_flow_review
        ;;
    flatten)
        YEARS=2025,2026
        START=20250101; END=20260930
        DIRNAME=20250101_20260930_ts_switch_momentum_flatten
        FLATTEN=1
        DEFAULT_ARM=ts_turning_point
        ;;
    resume)
        # Continue the flatten run after a kill: same schedule, same review state,
        # same report dir, --resume so the existing ledger and reports survive.
        YEARS=2025,2026
        START=20250101; END=20260930
        DIRNAME=20250101_20260930_ts_switch_momentum_flatten
        FLATTEN=1
        DEFAULT_ARM=ts_turning_point
        RESUME=1
        ;;
    *)
        echo "unknown mode $MODE"; exit 2 ;;
esac

SCHEDULE=trading_test/momentum_schedule_flatten.csv
LOG=logs/switch_${MODE}_${STAMP}.log
REVIEW=/tmp/review_adjustments_switch_${MODE}_${STAMP}.json
OUTDIR=backtest/results
DIR=${OUTDIR}/${DIRNAME}

mkdir -p logs "$OUTDIR"
exec > >(tee -a "$LOG") 2>&1

echo "== $(date '+%F %T') switch $MODE start (log $LOG)"
.venv/bin/python trading_test/engine_preflight.py || { echo "engine busy — abort"; exit 1; }
if [ "$RESUME" = 1 ]; then
    [ -s "$SCHEDULE" ] || { echo "resume: no schedule at $SCHEDULE — run 'flatten' first"; exit 1; }
    # Reuse the interrupted run's review state; a fresh path would restart the
    # strategy's sentiment from zero and bend the resumed months.
    PREV_REVIEW=$(ls -t /tmp/review_adjustments_switch_flatten_*.json 2>/dev/null | head -1)
    [ -n "$PREV_REVIEW" ] && REVIEW=$PREV_REVIEW
    echo "== resume: schedule=$SCHEDULE review=$REVIEW"
else
    .venv/bin/python trading_test/build_momentum_schedule.py --out "$SCHEDULE" \
        --years "$YEARS" --default "$DEFAULT_ARM" | head -2
    [ -d "$DIR" ] && mv "$DIR" "${DIR}_prev_${STAMP}" && echo "== previous run moved aside"
fi

START_TS=$(date +%s)
if [ "$RESUME" = 1 ]; then
    # Same panel cache and same (append-only) audit csv as the interrupted run so
    # the panel is not rebuilt and the decision log keeps one continuous history.
    PREV_TAG=$(basename "$REVIEW" .json | sed 's/^review_adjustments_switch_//')
    CACHE_KEY="switch_${PREV_TAG}"
    AUDIT="/tmp/momentum_decisions_${PREV_TAG}.csv"
    [ -f "$AUDIT" ] || AUDIT="/tmp/momentum_decisions_resume_${STAMP}.csv"
    ENGINE_FLAGS="--no-search --no-ai --resume"
else
    CACHE_KEY="switch_${MODE}_${STAMP}"
    AUDIT="/tmp/momentum_decisions_${MODE}_${STAMP}.csv"
    ENGINE_FLAGS="--no-search --no-ai"
fi
echo "== mode=$MODE cache=$CACHE_KEY audit=$AUDIT"

PYTHONPATH= \
REPORT_DIR="$OUTDIR" \
SWITCH_SCHEDULE_CSV="$SCHEDULE" \
SWITCH_FLATTEN="$FLATTEN" \
REVIEW_ADJUSTMENTS_PATH="$REVIEW" \
REVIEW_OUTPUT="$REVIEW" \
TP_PANEL_CACHE_KEY="$CACHE_KEY" \
SWITCH_AUDIT_CSV="$AUDIT" \
    .venv/bin/python backtest/engine.py "$START" "$END" ts_switch_momentum $ENGINE_FLAGS
RC=$?
echo "== $(date '+%F %T') $MODE rc=$RC elapsed=$(( $(date +%s) - START_TS ))s dir=$DIR"
mkdir -p "$DIR"
cp shared/db/test_imobile.db "$DIR/ledger.db" 2>/dev/null && echo "== ledger snapshotted"
grep -m1 'Total Return' "$DIR"/report_period_*.md 2>/dev/null
echo "== $(date '+%F %T') switch $MODE done"
