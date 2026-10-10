#!/usr/bin/env bash
# Run a QUEUE of backtests, ONE AT A TIME, backing each up.
#
#   usage: run_sweep.sh <queue-file> [workers]     (workers>1 refused; see below)
#   queue lines:  "<src> <start YYYYMMDD> <end YYYYMMDD>"   (# comments allowed)
#
# Why this exists: no strategy except the default has EVER been backtested, so
# choosing one per market regime needs a sweep.
#
# SEQUENTIAL, not parallel. An engine run is NOT safe to run alongside another:
# EVERY strategy writes its picks to the SAME path (`/tmp/tmp`, with a shared
# `/tmp/ts_7AZ_tmp.json`-style fallback), and the engine renames that shared file to
# a per-run name afterwards. Two runs therefore clobber each other — either silently
# handing a run the other's picks, or raising
# `FileNotFoundError: /tmp/tmp_<src>_<date>_<pid>` (observed: ts_7AZ 2024 died at
# 20240123 while ts_7AZ 2025 ran). The engine's comment "to allow parallel backtests"
# on that rename is WRONG until the strategies accept a per-run output path — a
# 15-file change to the strategy scripts, which are also the LIVE-trading path, so
# it is left as a proposal rather than done here. SWEEP_ALLOW_PARALLEL=1 overrides.
#
# A failed run is re-tried ONCE with --resume (a transient API hiccup should not cost
# hours), and a run whose _sweep backup already has a period report is skipped, so a
# restart never redoes finished work.
set -u
REPO=/home/kasm-user/apps/imobile
cd "$REPO" || exit 2

PY=.venv/bin/python
BK=backtest/results_backups
QD="${1:?usage: run_sweep.sh <queue-file> [workers]}"
WORKERS="${2:-1}"
SUM="${SWEEP_SUMMARY:-/tmp/run_sweep_summary.tsv}"
LOCK=/tmp/run_sweep.lock

[[ -f "$QD" ]] || { echo "sweep: no queue file $QD" >&2; exit 2; }
[[ -x "$PY" ]] || { echo "sweep: no python at $PY" >&2; exit 2; }
if (( WORKERS > 1 )) && [[ "${SWEEP_ALLOW_PARALLEL:-0}" != 1 ]]; then
  echo "sweep: refusing >1 worker — concurrent engines share /tmp/tmp and corrupt each" >&2
  echo "       other (see header). Set SWEEP_ALLOW_PARALLEL=1 to override." >&2
  exit 2
fi

exec 9>"$LOCK"
flock -n 9 || { echo "sweep: another sweep holds $LOCK" >&2; exit 3; }
for pat in "backtest/engine\.py" "trading/runner\.py" "pre_market_run\.py"; do
  if pgrep -f "$pat" >/dev/null 2>&1; then
    echo "sweep: '$pat' already running — refusing (shared /tmp/tmp + shared state)" >&2
    exit 3
  fi
done

[[ -f "$SUM" ]] || printf 'src\trange\trc\ttotal_return\tbench_sse\tsecs\n' > "$SUM"

run_engine() {  # $1 src  $2 start  $3 end  $4 extra flags ("" or "--resume")
  local src="$1" start="$2" end="$3" extra="${4:-}"
  local db="/tmp/sweep_${src}_${start}.db"
  local log="/tmp/sweep_${src}_${start}.log"
  # one temp DB per (src,start), reused by a retry so --resume can continue it
  [[ -f "$db" ]] || cp -f shared/db/test_imobile.db "$db" 2>/dev/null
  # shellcheck disable=SC2086
  DBTEST_IMOBILE_FILE="$db" "$PY" backtest/engine.py "$start" "$end" "$src" \
      --no-search --no-ai $extra >"$log" 2>&1
}

run_one() {  # $1 src  $2 start  $3 end
  local src="$1" start="$2" end="$3"
  local res="backtest/results/${start}_${end}_${src}"
  local bkp="${BK}/${start}_${end}_${src}_sweep"
  if [[ -f "$bkp/report_period_${start}_${end}.md" ]]; then
    echo "[sweep] $src $start-$end already done — skipping"; return 0
  fi
  local t0 t1 rc period tot bench
  t0=$(date +%s)
  run_engine "$src" "$start" "$end" ""; rc=$?
  if (( rc != 0 )); then
    echo "[sweep] $src $start-$end failed rc=$rc — retrying once with --resume" >&2
    run_engine "$src" "$start" "$end" "--resume"; rc=$?
  fi
  t1=$(date +%s)
  period=$(ls "$res"/report_period_*.md 2>/dev/null | head -1)
  tot=$(grep -m1 "Total Return" "$period" 2>/dev/null | sed -E 's/[^0-9.+-]//g')
  bench=$(grep -m2 "Total Return" "$period" 2>/dev/null | tail -1 | cut -d'|' -f4 | tr -d ' *')
  rm -rf "$bkp"; cp -r "$res" "$bkp" 2>/dev/null
  printf '%s\t%s-%s\t%s\t%s\t%s\t%s\n' "$src" "$start" "$end" "$rc" "${tot:-NA}" "${bench:-NA}" "$((t1-t0))" >> "$SUM"
  echo "[sweep] $src $start-$end rc=$rc return=${tot:-NA} elapsed=$(( (t1-t0)/60 ))m"
}

# Never run a backtest alongside LIVE trading: they share /tmp/tmp and the
# review-state file, so an overlap corrupts both — and the live path places real
# orders. Wait for any live process to clear before each run.
wait_for_live_clear() {
  local waited=0
  while pgrep -f "trading/runner\.py|pre_market_run\.py" >/dev/null 2>&1; do
    (( waited == 0 )) && echo "[sweep] live trading process up — waiting to avoid a collision" >&2
    sleep 60; waited=$((waited+60))
  done
  (( waited > 0 )) && echo "[sweep] live clear after ${waited}s — resuming" >&2
}
while read -r src start end; do
  [[ -z "${src:-}" ]] && continue
  case "$src" in \#*) continue;; esac
  wait_for_live_clear
  run_one "$src" "$start" "$end"
done < "$QD"

echo "[sweep] done — summary $SUM"
cat "$SUM"
