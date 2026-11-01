#!/usr/bin/env bash
# Wait for the 2024 arms, then run the two switch experiments back to back.
#
# Sequential on purpose: every engine run wipes shared/db/test_imobile.db, so two concurrent
# runs would corrupt each other's book. Order is information-first: the 2024 hold-out asks
# whether the momentum rule generalises at all, the flatten run asks whether the switching
# cost was implementation rather than signal.
set -uo pipefail
cd "$(dirname "$0")/.."

ARMS_LOG=${1:-$(ls -t logs/arms_2024_*.log 2>/dev/null | head -1)}
[ -z "${ARMS_LOG:-}" ] && { echo "no 2024 arms log found"; exit 1; }

echo "== $(date '+%F %T') queue waiting on $ARMS_LOG"
for _ in $(seq 1 480); do      # up to 8h of 60s polls
    grep -q '2024 arms done' "$ARMS_LOG" && break
    sleep 60
done
if ! grep -q '2024 arms done' "$ARMS_LOG"; then
    echo "== $(date '+%F %T') arms never finished — aborting queue"
    exit 1
fi
echo "== $(date '+%F %T') arms finished; ledger will be reused, starting hold-out"

bash trading_test/run_switch.sh holdout
bash trading_test/run_switch.sh flatten

echo "== $(date '+%F %T') queue done"
