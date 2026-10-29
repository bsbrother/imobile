#!/usr/bin/env bash
# ts_switch_momentum over the two measured windows, in one continuous run.
#
# The schedule (month -> arm) is rebuilt first from the standalone arms' monthly equity, so
# the switch can only ever use months that are already closed. Review state is private to
# this run (the live cron writes /tmp/review_adjustments.json at ~09:2x and a long run that
# shares it would silently pick up another book's sizing), and the panel cache has its own
# key because concurrent arms corrupt a shared one.
set -uo pipefail
cd "$(dirname "$0")/.."

STAMP=$(date +%Y%m%d_%H%M%S)
SCHEDULE=trading_test/momentum_schedule.csv
LOG=logs/switch_momentum_${STAMP}.log
REVIEW=/tmp/review_adjustments_switch_${STAMP}.json
OUTDIR=backtest/results
DIR=${OUTDIR}/20250101_20260930_ts_switch_momentum

mkdir -p logs "$OUTDIR"
exec > >(tee -a "$LOG") 2>&1

echo "== $(date '+%F %T') switch_momentum start (log $LOG)"
.venv/bin/python trading_test/engine_preflight.py || { echo "engine busy — abort"; exit 1; }

.venv/bin/python trading_test/build_momentum_schedule.py --out "$SCHEDULE" | head -3

# Keep any previous reading of this window instead of overwriting it.
if [ -d "$DIR" ]; then
    PREV=${DIR}_prev_${STAMP}
    mv "$DIR" "$PREV"
    echo "== previous run moved to $PREV"
fi

START=$(date +%s)
PYTHONPATH= \
REPORT_DIR="$OUTDIR" \
SWITCH_SCHEDULE_CSV="$SCHEDULE" \
REVIEW_ADJUSTMENTS_PATH="$REVIEW" \
REVIEW_OUTPUT="$REVIEW" \
TP_PANEL_CACHE_KEY="switch_momentum_${STAMP}" \
SWITCH_AUDIT_CSV="/tmp/momentum_decisions_${STAMP}.csv" \
    .venv/bin/python backtest/engine.py 20250101 20260930 ts_switch_momentum --no-search --no-ai
RC=$?
echo "== $(date '+%F %T') rc=$RC elapsed=$(( $(date +%s) - START ))s dir=$DIR"

# The ledger is wiped by every engine run; snapshot it now or the exits are lost.
cp shared/db/test_imobile.db "$DIR/ledger.db" 2>/dev/null && echo "== ledger snapshotted"
grep -m1 'Total Return' "$DIR"/report_period_*.md 2>/dev/null
echo "== $(date '+%F %T') switch_momentum done"
