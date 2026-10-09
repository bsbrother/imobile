#!/usr/bin/env bash
# Run a QUEUE of backtests with bounded parallelism, each in its own DB.
#
#   usage: run_sweep.sh <queue-file> [workers]      (default 3 workers)
#   queue lines:  "<src> <start YYYYMMDD> <end YYYYMMDD>"   (# comments allowed)
#
# Why this exists: no strategy except the default has EVER been backtested, so
# picking one per market regime needs a sweep. Each run is ~1-3h, so:
#
#   * every run gets its own DBTEST_IMOBILE_FILE. A backtest WIPES its DB on
#     start, so a shared DB corrupts concurrent runs; with separate DBs they only
#     share the read-only OHLCV cache and the per-(range,src) results dir, both safe.
#   * bounded parallelism (default 3) to stay clear of Tushare rate limits.
#   * a global lock + guards for a running engine.py / live trading process, so a
#     sweep never collides with a live run (each non-resume engine also deletes
#     /tmp/review_adjustments.json, which would corrupt a live *_review run).
#   * every run is copied to results_backups/<start>_<end>_<src>_sweep, and a TSV
#     row (src, range, rc, total return, benchmark SSE, secs) is appended as it lands.
set -u
REPO=/home/kasm-user/apps/imobile
cd "$REPO" || exit 2

PY=.venv/bin/python
BK=backtest/results_backups
QD="${1:?usage: run_sweep.sh <queue-file> [workers]}"
WORKERS="${2:-3}"
SUM="${SWEEP_SUMMARY:-/tmp/run_sweep_summary.tsv}"
LOCK=/tmp/run_sweep.lock

[[ -f "$QD" ]] || { echo "sweep: no queue file $QD" >&2; exit 2; }
[[ -x "$PY" ]] || { echo "sweep: no python at $PY" >&2; exit 2; }

exec 9>"$LOCK"
flock -n 9 || { echo "sweep: another sweep holds $LOCK" >&2; exit 3; }
for pat in "backtest/engine\.py" "trading/runner\.py" "pre_market_run\.py"; do
  if pgrep -f "$pat" >/dev/null 2>&1; then
    echo "sweep: '$pat' already running — refusing (shared state/live path)" >&2
    exit 3
  fi
done

[[ -f "$SUM" ]] || printf 'src\trange\trc\ttotal_return\tbench_sse\tsecs\n' > "$SUM"

run_one() {  # $1 src  $2 start  $3 end
  local src="$1" start="$2" end="$3"
  local db="/tmp/sweep_${src}_${start}.db"
  local res="backtest/results/${start}_${end}_${src}"
  local log="/tmp/sweep_${src}_${start}.log"
  local t0 t1 rc tot bench
  cp -f shared/db/test_imobile.db "$db" 2>/dev/null
  t0=$(date +%s)
  DBTEST_IMOBILE_FILE="$db" "$PY" backtest/engine.py "$start" "$end" "$src" --no-search --no-ai >"$log" 2>&1
  rc=$?
  t1=$(date +%s)
  local period
  period=$(ls "$res"/report_period_*.md 2>/dev/null | head -1)
  tot=$(grep -m1 "Total Return" "$period" 2>/dev/null | sed -E 's/[^0-9.+-]//g')
  bench=$(grep -m2 "Total Return" "$period" 2>/dev/null | tail -1 | cut -d'|' -f3 | tr -d ' *')
  rm -rf "${BK}/${start}_${end}_${src}_sweep"
  cp -r "$res" "${BK}/${start}_${end}_${src}_sweep" 2>/dev/null
  printf '%s\t%s-%s\t%s\t%s\t%s\t%s\n' "$src" "$start" "$end" "$rc" "${tot:-NA}" "${bench:-NA}" "$((t1-t0))" >> "$SUM"
  echo "[sweep] $src $start-$end rc=$rc return=${tot:-NA} elapsed=$(( (t1-t0)/60 ))m"
}

n=0
while read -r src start end; do
  [[ -z "${src:-}" ]] && continue
  case "$src" in \#*) continue;; esac
  run_one "$src" "$start" "$end" &
  n=$((n+1))
  (( n % WORKERS == 0 )) && wait
done < "$QD"
wait
echo "[sweep] done — summary $SUM"
cat "$SUM"
