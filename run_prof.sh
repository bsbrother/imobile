#!/usr/bin/env bash
# Profile: where does a full-history day's time go?
cd /home/kasm-user/apps/imobile || exit 1
export PYTHONHOME=/home/kasm-user/.local/share/cpython-3.12.11-linux-x86_64-gnu
export PYTHONPATH="$PWD/.venv/lib/python3.12/site-packages"
PY=$PWD/.venv/bin/python3
echo "== LHB CSV size =="
ls -la --block-size=M shared/data/lhb/lhb_institutional_2026.csv
wc -l shared/data/lhb/lhb_institutional_2026.csv
echo "== time one strategy pick 20260105 =="
/usr/bin/time -v $PY backtest/strategies/ts_7AZ_96MA_flow.py 20260105 > /tmp/pick_test.log 2>/tmp/pick_time.log || true
grep -E "Elapsed|Maximum resident" /tmp/pick_time.log
echo "== tail pick log =="
tail -4 /tmp/pick_test.log
echo "== index data path (get_index_data impl) =="
grep -n "def get_index_data" backtest/data_provider.py
sed -n "$(grep -n 'def get_index_data' backtest/data_provider.py | head -1 | cut -d: -f1),+25p" backtest/data_provider.py
echo PROF_DONE