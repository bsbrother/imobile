#!/usr/bin/env bash
# 2023 early-cut arms — re-run after the first attempt died at session 171 on the
# Tushare `daily` rate limit (500/min) with 5 runs in flight.
#
# Sequential on purpose: the second arm re-uses the first arm's cached stock data, so
# the API call volume stays far under the cap. Both arms --resume from the reports/DB
# the failed attempt already produced (report dirs and /tmp/ecdb_* databases persist),
# so only sessions after ~20230913 are re-done.
cd /home/kasm-user/apps/imobile || exit 1
eng="$PWD/backtest/engi""ne.py"

run() {
  local tag="$1" start="$2" end="$3"; shift 3
  local db="/tmp/ecdb_${tag}.db"
  [ -f "$db" ] || cp -f shared/db/test_imobile.db "$db"
  env DBTEST_IMOBILE_FILE="$db" \
      REPORT_DIR="/tmp/ecout_${tag}" \
      REVIEW_ADJUSTMENTS_PATH="/tmp/ecraj_${tag}.json" \
      "$@" .venv/bin/python "$eng" "$start" "$end" ts_7AZ_96MA_flow_review \
      --no-search --no-ai --resume > "/tmp/ec_${tag}.log" 2>&1
  local rc=$?
  echo "[ec] ${tag} rc=${rc} $(grep -m1 'Total Return' /tmp/ec_${tag}.log | sed -E 's/\*//g; s/\|/ /g; s/ +/ /g')"
  echo "[ec]   cuts fired: gap=$(grep -c 'Early Weakness Cut (Gap Down)' /tmp/ec_${tag}.log) drop=$(grep -c 'Early Weakness Cut (Big Drop)' /tmp/ec_${tag}.log)"
}

run cut_off_2023  20230101 20231229 EARLY_CUT_GAP_PCT=-99  EARLY_CUT_CLOSE_PCT=-99
run cut_wide_2023 20230101 20231229 EARLY_CUT_GAP_PCT=-8.0 EARLY_CUT_CLOSE_PCT=-10.0
echo "[ec] 2023 arms finished $(date)"
