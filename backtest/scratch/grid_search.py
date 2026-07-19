import os
import subprocess
import re
import json

def run_backtest(env_updates):
    # Merge env into current process env
    run_env = os.environ.copy()
    run_env.update(env_updates)
    
    # Run backtest
    cmd = ["python", "backtest/engine.py", "20260101", "20260619", "ts_7AZ", "--no-search", "--no-ai"]
    subprocess.run(cmd, env=run_env, capture_output=True)
    
    # Run result_backtest.py to parse the return
    res = subprocess.run(["python", "backtest/result_backtest.py"], capture_output=True, text=True)
    out = res.stdout
    
    # Extract overall return
    m = re.search(r'Return:\s*([\d\.]+)%', out)
    if m:
        return float(m.group(1))
    return 0.0

# Phase 1: Optimize exit parameters
phase1_configs = [
    # SL Enabled = True, SL_WITH_RE_PICK = False
    {'SL_ENABLED': 'true', 'SL_WITH_RE_PICK': 'false', 'HOLD_DAYS_MULT': '1.0', 'POS_SCORE_WEIGHT': 'false', 'SCORE_MIN': '0'},
    {'SL_ENABLED': 'true', 'SL_WITH_RE_PICK': 'false', 'HOLD_DAYS_MULT': '0.7', 'POS_SCORE_WEIGHT': 'false', 'SCORE_MIN': '0'},
    {'SL_ENABLED': 'true', 'SL_WITH_RE_PICK': 'false', 'HOLD_DAYS_MULT': '0.5', 'POS_SCORE_WEIGHT': 'false', 'SCORE_MIN': '0'},
    # SL Enabled = True, SL_WITH_RE_PICK = True
    {'SL_ENABLED': 'true', 'SL_WITH_RE_PICK': 'true', 'HOLD_DAYS_MULT': '1.0', 'POS_SCORE_WEIGHT': 'false', 'SCORE_MIN': '0'},
    {'SL_ENABLED': 'true', 'SL_WITH_RE_PICK': 'true', 'HOLD_DAYS_MULT': '0.7', 'POS_SCORE_WEIGHT': 'false', 'SCORE_MIN': '0'},
    {'SL_ENABLED': 'true', 'SL_WITH_RE_PICK': 'true', 'HOLD_DAYS_MULT': '0.5', 'POS_SCORE_WEIGHT': 'false', 'SCORE_MIN': '0'},
    # SL Enabled = False
    {'SL_ENABLED': 'false', 'SL_WITH_RE_PICK': 'false', 'HOLD_DAYS_MULT': '1.0', 'POS_SCORE_WEIGHT': 'false', 'SCORE_MIN': '0'},
    {'SL_ENABLED': 'false', 'SL_WITH_RE_PICK': 'false', 'HOLD_DAYS_MULT': '0.7', 'POS_SCORE_WEIGHT': 'false', 'SCORE_MIN': '0'},
    {'SL_ENABLED': 'false', 'SL_WITH_RE_PICK': 'false', 'HOLD_DAYS_MULT': '0.5', 'POS_SCORE_WEIGHT': 'false', 'SCORE_MIN': '0'},
]

print("Starting Phase 1: Optimizing Exit Parameters...")
best_p1_score = -999.0
best_p1_config = None
p1_results = []

for i, cfg in enumerate(phase1_configs):
    ret = run_backtest(cfg)
    print(f"Run {i+1}/9: SL_ENABLED={cfg['SL_ENABLED']}, SL_WITH_RE_PICK={cfg['SL_WITH_RE_PICK']}, HOLD_DAYS_MULT={cfg['HOLD_DAYS_MULT']} => Return: {ret}%")
    p1_results.append((cfg, ret))
    if ret > best_p1_score:
        best_p1_score = ret
        best_p1_config = cfg

print(f"\nBest Phase 1 Config: {best_p1_config} with Return: {best_p1_score}%\n")

# Phase 2: Optimize Sizing and Score Filters using the best P1 config
print("Starting Phase 2: Optimizing Sizing & Filters...")
phase2_configs = [
    # Test POS_SCORE_WEIGHT = True
    {**best_p1_config, 'POS_SCORE_WEIGHT': 'true'},
    # Test SCORE_MIN = 3
    {**best_p1_config, 'SCORE_MIN': '3'},
    # Test SCORE_MIN = 5
    {**best_p1_config, 'SCORE_MIN': '5'},
    # Test POS_SCORE_WEIGHT = True and SCORE_MIN = 3
    {**best_p1_config, 'POS_SCORE_WEIGHT': 'true', 'SCORE_MIN': '3'},
]

best_overall_score = best_p1_score
best_overall_config = best_p1_config
p2_results = []

for i, cfg in enumerate(phase2_configs):
    ret = run_backtest(cfg)
    print(f"Run {i+1}/4: POS_SCORE_WEIGHT={cfg['POS_SCORE_WEIGHT']}, SCORE_MIN={cfg['SCORE_MIN']} => Return: {ret}%")
    p2_results.append((cfg, ret))
    if ret > best_overall_score:
        best_overall_score = ret
        best_overall_config = cfg

print(f"\nBest Overall Config: {best_overall_config} with Return: {best_overall_score}%")

# Save results
output = {
    'p1_results': p1_results,
    'p2_results': p2_results,
    'best_overall_config': best_overall_config,
    'best_overall_score': best_overall_score
}
with open('backtest/scratch/grid_search_results.json', 'w') as f:
    json.dump(output, f, indent=2)
print("Saved results to backtest/scratch/grid_search_results.json")
