#!/usr/bin/env bash
# Exercise the hardening in run_pair.sh using its test seams: no engine is
# launched, no DB or .env is touched. Verifies the failure paths that a real
# multi-hour run cannot be asked to reproduce on demand.
#
#   bash trading_test/test_run_pair.sh
set -u
cd "$(dirname "$0")/.." || exit 2

RP=trading_test/run_pair.sh
TAG="selftest_$$"
RANGE_A=19990104
RANGE_B=19990105
TMP=$(mktemp -d)
fail=0
ok()  { echo "PASS: $1"; }
bad() { echo "FAIL: $1"; fail=1; }
cleanup() {
  rm -rf backtest/results_backups/${RANGE_A}_${RANGE_B}_*_${TAG} "$TMP"
}
trap cleanup EXIT

# 1. missing args -> exit 2, before anything is touched
bash $RP >/dev/null 2>&1; rc=$?
[[ $rc -eq 2 ]] && ok "missing args -> exit 2" || bad "missing args rc=$rc (want 2)"

# 2. engine exits non-zero -> the arm is STILL backed up, tagged _PARTIAL_RUN.txt
PAIR_TEST=1 PAIR_TOGGLE=true PAIR_ENGINE=false \
  bash $RP "$RANGE_A" "$RANGE_B" "$TAG" >"$TMP/fail.log" 2>&1; rc=$?
[[ $rc -eq 1 ]] && ok "failed pair -> exit 1" || bad "failed pair rc=$rc (want 1)"
d="${PWD}/backtest/results_backups/${RANGE_A}_${RANGE_B}_baselineSL_${TAG}"
[[ -f "$d/_PARTIAL_RUN.txt" ]] && ok "failed arm backed up with _PARTIAL_RUN.txt" \
                              || bad "failed arm has no _PARTIAL_RUN.txt"
[[ -f "$d/_IN_PROGRESS.txt" ]] && bad "_IN_PROGRESS.txt left behind on failure" \
                              || ok "no stale _IN_PROGRESS.txt"
grep -q "PAIR_INCOMPLETE ${TAG}" "$TMP/fail.log" && ok "log says PAIR_INCOMPLETE" \
                                                 || bad "no PAIR_INCOMPLETE in log"
# the _PARTIAL_RUN.txt must name the recovery command
grep -q "regen_period_report.py" "$d/_PARTIAL_RUN.txt" && ok "_PARTIAL_RUN.txt names the recovery cmd" \
                                                       || bad "_PARTIAL_RUN.txt lacks recovery cmd"

# 3. clean run -> both arms backed up, no markers, PAIR_DONE, exit 0
PAIR_TEST=1 PAIR_TOGGLE=true PAIR_ENGINE=true \
  bash $RP "$RANGE_A" "$RANGE_B" "$TAG" >"$TMP/ok.log" 2>&1; rc=$?
[[ $rc -eq 0 ]] && ok "clean pair -> exit 0" || bad "clean pair rc=$rc (want 0)"
for arm in baselineSL nostop; do
  dd="${PWD}/backtest/results_backups/${RANGE_A}_${RANGE_B}_${arm}_${TAG}"
  [[ -d "$dd" ]] && ok "clean run produced $arm backup" || bad "missing $arm backup"
  [[ -f "$dd/_PARTIAL_RUN.txt" || -f "$dd/_IN_PROGRESS.txt" ]] && bad "$arm left a marker" \
                                                              || ok "$arm leaves no marker"
done
grep -q "PAIR_DONE ${TAG}" "$TMP/ok.log" && ok "log says PAIR_DONE" || bad "no PAIR_DONE in log"

# 4. concurrency guard -> refuse (exit 3) while an engine.py is running
bash -c 'exec -a "backtest/engine.py" sleep 30' & decoy=$!
sleep 0.3
PAIR_TOGGLE=true PAIR_ENGINE=true \
  bash $RP "$RANGE_A" "$RANGE_B" "$TAG" >"$TMP/guard.log" 2>&1; rc=$?
kill "$decoy" 2>/dev/null; wait "$decoy" 2>/dev/null
[[ $rc -eq 3 ]] && ok "refuses while an engine.py runs -> exit 3" || bad "guard rc=$rc (want 3)"
grep -q "already running" "$TMP/guard.log" && ok "guard explains itself" || bad "guard message missing"

echo
[[ $fail -eq 0 ]] && echo "ALL PASS" || echo "SOME FAILED"
exit $fail
