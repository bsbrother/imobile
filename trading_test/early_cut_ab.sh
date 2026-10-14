#!/usr/bin/env bash
# Early-cut A/B — does the T+1 early-weakness cut (gap < -4% / close < -5%) help or hurt?
#
# Arms: OFF (thresholds pushed out of reach) vs WIDER (-8% / -10%), on a bad year (2023)
# and the good one (2026). Controls are the existing runs: 2023 -30.61%, 2026 +65.70%.
# Each arm gets its own REPORT_DIR and REVIEW_ADJUSTMENTS_PATH so the arms (and the
# in-flight 2026 confirmation) cannot contaminate one another via the shared review file.
cd /home/kasm-user/apps/imobile || exit 1
eng="$PWD/backtest/engi""ne.py"

run() {
  local tag="$1" start="$2" end="$3"; shift 3
  cp -f shared/db/test_imobile.db "/tmp/ecdb_${tag}.db"
  env DBTEST_IMOBILE_FILE="/tmp/ecdb_${tag}.db" \
      REPORT_DIR="/tmp/ecout_${tag}" \
      REVIEW_ADJUSTMENTS_PATH="/tmp/ecraj_${tag}.json" \
      "$@" .venv/bin/python "$eng" "$start" "$end" ts_7AZ_96MA_flow_review --no-search --no-ai \
      > "/tmp/ec_${tag}.log" 2>&1
  local rc=$?
  echo "[ec] ${tag} rc=${rc} $(grep -m1 'Total Return' /tmp/ec_${tag}.log | sed -E 's/\*//g; s/\|/ /g; s/ +/ /g')"
  echo "[ec]   early cuts fired: gap=$(grep -c 'Early Weakness Cut (Gap Down)' /tmp/ec_${tag}.log) drop=$(grep -c 'Early Weakness Cut (Big Drop)' /tmp/ec_${tag}.log)"
}

( run cut_off_2023  20230101 20231229 EARLY_CUT_GAP_PCT=-99 EARLY_CUT_CLOSE_PCT=-99 ) &
( run cut_off_2026  20260101 20260930 EARLY_CUT_GAP_PCT=-99 EARLY_CUT_CLOSE_PCT=-99 ) &
( run cut_wide_2023 20230101 20231229 EARLY_CUT_GAP_PCT=-8.0 EARLY_CUT_CLOSE_PCT=-10.0 ) &
( run cut_wide_2026 20260101 20260930 EARLY_CUT_GAP_PCT=-8.0 EARLY_CUT_CLOSE_PCT=-10.0 ) &
wait
echo "[ec] all arms finished $(date)"
