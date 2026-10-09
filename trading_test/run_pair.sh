#!/usr/bin/env bash
# Run the shipped-stop and no-stop configs back to back over one period.
#
#   usage: run_pair.sh <start YYYYMMDD> <end YYYYMMDD> <tag>
#
# Each arm is a multi-hour backtest, so this is written to survive an unattended
# run rather than to look tidy:
#
#   preflight    Refuses on bad args or a missing .env toggler BEFORE touching
#                .env, so a broken environment cannot leave SL_ENABLED toggled
#                (a pruned set_sl.py once did exactly that).
#   one engine   Every backtest wipes and shares shared/db/test_imobile.db, so two
#                concurrent engines corrupt each other. Refuse to start if any
#                engine.py is already running, and take a lock against a second
#                run_pair.
#   always back up
#                The results dir is snapshotted even when the engine exits
#                NON-ZERO. A run can die at the FINISH LINE -- after every session
#                -- and the daily reports + DB are the expensive part; only the
#                period report is rebuildable (trading_test/regen_period_report.py).
#                A failed arm's backup carries _PARTIAL_RUN.txt.
#   survive SIGKILL
#                A background snapshot loop copies the results dir AND the DB to
#                the backup every SNAP_EVERY seconds (default 300), so a hard kill
#                -- which no trap can catch -- still leaves a recent copy, marked
#                _IN_PROGRESS.txt until the arm finishes cleanly.
#   restore .env  On any exit the toggler is run back to no-stop, and the result
#                is checked (a silent restore failure is how the live path ends up
#                running the wrong stop).
#
# Test seams (defaults are the real tools; used by trading_test/test_run_pair.sh):
#   PAIR_TOGGLE=<cmd>   run instead of the .env toggler (receives true|false)
#   PAIR_ENGINE=<cmd>   run instead of the backtest engine (no args)
#   PAIR_TEST=1         skip the "an engine is already running" guard
set -u

REPO=/home/kasm-user/apps/imobile
cd "$REPO" || { echo "run_pair: no repo at $REPO" >&2; exit 2; }

PY=.venv/bin/python
TOGGLE_PY=/home/kasm-user/.hermes/bin/set_sl.py
STRAT=ts_7AZ_96MA_flow_review
BK=backtest/results_backups
DB=shared/db/test_imobile.db
LOCK=/tmp/run_pair.lock
SNAP_EVERY=${SNAP_EVERY:-300}
PAIR_TEST=${PAIR_TEST:-0}

START="${1:-}"; END="${2:-}"; TAG="${3:-}"
if [[ -z "$START" || -z "$END" || -z "$TAG" ]]; then
  echo "usage: run_pair.sh <start YYYYMMDD> <end YYYYMMDD> <tag>" >&2
  exit 2
fi
[[ -x "$PY" ]] || { echo "run_pair: no python at $PY" >&2; exit 2; }
if [[ -z "${PAIR_TOGGLE:-}" && ! -f "$TOGGLE_PY" ]]; then
  echo "run_pair: missing .env toggler $TOGGLE_PY — refusing (it would leave .env toggled)" >&2
  exit 2
fi

RES="backtest/results/${START}_${END}_${STRAT}"

toggle() {
  if [[ -n "${PAIR_TOGGLE:-}" ]]; then "$PAIR_TOGGLE" "$1"; else "$PY" "$TOGGLE_PY" "$STRAT" "$1"; fi
}
run_engine() {
  if [[ -n "${PAIR_ENGINE:-}" ]]; then "$PAIR_ENGINE"; else
    "$PY" backtest/engine.py "$START" "$END" "$STRAT" --no-search --no-ai
  fi
}
marker() { printf '%s\n' "$@" > "$1"; }

# --- guard: never run alongside another engine (shared test DB) --------------
exec 9>"$LOCK"
if ! flock -n 9; then
  echo "run_pair: another run_pair already holds $LOCK" >&2; exit 3
fi
if [[ "$PAIR_TEST" != 1 ]] && pgrep -f "backtest/engine\.py" >/dev/null 2>&1; then
  echo "run_pair: an engine.py is already running and shares $DB — refusing" >&2
  exit 3
fi

restore() {
  echo "=== restoring .env to no-stop ==="
  if ! toggle false; then
    echo "WARNING: .env restore FAILED — verify SL_ENABLED for $STRAT in $REPO/.env" >&2
  fi
}
trap restore EXIT

run_arm() {  # $1 label  $2 SL true|false  $3 backup suffix
  local label="$1" sl="$2" suffix="$3"
  local dest="${BK}/${START}_${END}_${suffix}"
  local rc=0 snap_pid=""

  echo "=== ${TAG}: ${label} (.env SL_ENABLED=${sl}) ==="
  if ! toggle "$sl"; then
    echo "run_pair: could not set SL_ENABLED=${sl}; aborting ${label}" >&2
    return 1
  fi

  mkdir -p "$dest"
  # no stale markers from a previous run with the same tag (they are excluded
  # from rsync, so nothing else would clear them)
  rm -f "$dest/_IN_PROGRESS.txt" "$dest/_PARTIAL_RUN.txt"
  marker "$dest/_IN_PROGRESS.txt" \
    "arm '${label}' started $(date '+%F %T'); a clean finish removes this file."
  # rescue snapshot: keeps the dir + DB fresh enough to recover from a SIGKILL
  ( while sleep "$SNAP_EVERY"; do
      rsync -a --delete --exclude '_IN_PROGRESS.txt' --exclude '_PARTIAL_RUN.txt' \
            --exclude 'test_imobile.db' "$RES/" "$dest/" 2>/dev/null
      cp -f "$DB" "$dest/test_imobile.db" 2>/dev/null
    done ) &
  snap_pid=$!

  run_engine; rc=$?

  kill "$snap_pid" 2>/dev/null; wait "$snap_pid" 2>/dev/null

  # final snapshot regardless of rc, then mark the outcome
  rsync -a --delete --exclude '_IN_PROGRESS.txt' --exclude '_PARTIAL_RUN.txt' \
        --exclude 'test_imobile.db' "$RES/" "$dest/" 2>/dev/null
  cp -f "$DB" "$dest/test_imobile.db" 2>/dev/null
  rm -f "$dest/_IN_PROGRESS.txt"

  if [[ $rc -eq 0 ]]; then
    rm -f "$dest/_PARTIAL_RUN.txt"    # a clean re-run must not keep a stale failure marker
    echo "=== ${label} ok -> ${dest} ==="
  else
    marker "$dest/_PARTIAL_RUN.txt" \
      "engine exited ${rc} at $(date '+%F %T'); this arm did NOT finish cleanly." \
      "The report_orders_* here and test_imobile.db are the recovered state." \
      "Rebuild the missing period report with:" \
      "  .venv/bin/python trading_test/regen_period_report.py ${START} ${END} ${REPO}/${RES}/report_period_${START}_${END}.md"
    echo "WARNING: ${label} exited ${rc}; snapshotted with _PARTIAL_RUN.txt -> ${dest}" >&2
  fi
  return $rc
}

rc1=0; rc2=0
run_arm "baseline (shipped stop)" true  "baselineSL_${TAG}" || rc1=$?
run_arm "no stop"                false "nostop_${TAG}"     || rc2=$?

if [[ $rc1 -eq 0 && $rc2 -eq 0 ]]; then
  echo "=== PAIR_DONE ${TAG} ==="
else
  echo "=== PAIR_INCOMPLETE ${TAG} (baseline rc=${rc1}, nostop rc=${rc2}) ===" >&2
  exit 1
fi
