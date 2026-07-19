import os
import subprocess
import re
import json

def run_backtest(env_updates):
    run_env = os.environ.copy()
    run_env.update(env_updates)
    
    cmd = ["python", "backtest/engine.py", "20260101", "20260619", "ts_7AZ", "--no-search", "--no-ai"]
    subprocess.run(cmd, env=run_env, capture_output=True)
    
    res = subprocess.run(["python", "backtest/result_backtest.py"], capture_output=True, text=True)
    out = res.stdout
    
    m = re.search(r'Return:\s*([\d\.]+)%', out)
    if m:
        return float(m.group(1))
    return 0.0

# Define a smart parameter sweep
sweep_configs = [
    # Widen stop loss in normal/volatile/bear
    {'SL_BULL': '0.03', 'SL_NORMAL': '0.03', 'SL_VOLATILE': '0.025', 'SL_BEAR': '0.02', 'HOLD_DAYS_MULT': '0.5', 'SL_WITH_RE_PICK': 'false'},
    {'SL_BULL': '0.04', 'SL_NORMAL': '0.03', 'SL_VOLATILE': '0.025', 'SL_BEAR': '0.02', 'HOLD_DAYS_MULT': '0.5', 'SL_WITH_RE_PICK': 'false'},
    {'SL_BULL': '0.04', 'SL_NORMAL': '0.035', 'SL_VOLATILE': '0.03', 'SL_BEAR': '0.02', 'HOLD_DAYS_MULT': '0.5', 'SL_WITH_RE_PICK': 'false'},
    
    # Test HOLD_DAYS_MULT
    {'SL_BULL': '0.03', 'SL_NORMAL': '0.025', 'SL_VOLATILE': '0.02', 'SL_BEAR': '0.015', 'HOLD_DAYS_MULT': '0.6', 'SL_WITH_RE_PICK': 'false'},
    {'SL_BULL': '0.03', 'SL_NORMAL': '0.025', 'SL_VOLATILE': '0.02', 'SL_BEAR': '0.015', 'HOLD_DAYS_MULT': '0.7', 'SL_WITH_RE_PICK': 'false'},
    
    # Test combination of wider SL + HOLD_DAYS_MULT = 0.6
    {'SL_BULL': '0.03', 'SL_NORMAL': '0.03', 'SL_VOLATILE': '0.025', 'SL_BEAR': '0.02', 'HOLD_DAYS_MULT': '0.6', 'SL_WITH_RE_PICK': 'false'},
    {'SL_BULL': '0.04', 'SL_NORMAL': '0.035', 'SL_VOLATILE': '0.03', 'SL_BEAR': '0.02', 'HOLD_DAYS_MULT': '0.6', 'SL_WITH_RE_PICK': 'false'},
    
    # Test SL_WITH_RE_PICK = true with wider SL
    {'SL_BULL': '0.03', 'SL_NORMAL': '0.03', 'SL_VOLATILE': '0.025', 'SL_BEAR': '0.02', 'HOLD_DAYS_MULT': '0.5', 'SL_WITH_RE_PICK': 'true'},
]

print("Starting parameter search...")
best_score = 56.40
best_config = None

for i, cfg in enumerate(sweep_configs):
    ret = run_backtest(cfg)
    print(f"Run {i+1}/{len(sweep_configs)}: {cfg} => Return: {ret}%")
    if ret > best_score:
        best_score = ret
        best_config = cfg

print(f"\nBest Config found: {best_config} with Return: {best_score}%")
