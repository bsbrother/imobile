import os
import subprocess
import re

configs = {
    'A': {'SL_WITH_RE_PICK': 'false', 'SL_ENABLED': 'true', 'SCORE_MIN': '0', 'HOLD_DAYS_MULT': '1.0'},
    'B': {'SL_WITH_RE_PICK': 'false', 'SL_ENABLED': 'true', 'SCORE_MIN': '0', 'HOLD_DAYS_MULT': '0.7'},
    'C': {'SL_WITH_RE_PICK': 'false', 'SL_ENABLED': 'true', 'SCORE_MIN': '0', 'HOLD_DAYS_MULT': '0.5'},
    'D': {'SL_WITH_RE_PICK': 'false', 'SL_ENABLED': 'true', 'SCORE_MIN': '5', 'HOLD_DAYS_MULT': '0.5'},
}

results = {}
for name, env in configs.items():
    print(f"\n========================================\nRunning combination {name}...\n========================================")
    # Merge env into current process env
    run_env = os.environ.copy()
    run_env.update(env)
    
    # Run backtest
    cmd = ["python", "backtest/engine.py", "20260101", "20260619", "ts_7AZ", "--no-search", "--no-ai"]
    subprocess.run(cmd, env=run_env, check=True)
    
    # Run result_backtest.py to parse the return
    res = subprocess.run(["python", "backtest/result_backtest.py"], capture_output=True, text=True)
    out = res.stdout
    
    # Extract overall return
    m = re.search(r'Return:\s*([\d\.]+)%', out)
    if m:
        ret = m.group(1) + "%"
    else:
        ret = "Error"
    print(f"Combination {name} Return: {ret}")
    results[name] = ret

print("\n========================================")
print("FINAL RESULTS:")
print("========================================")
for name, ret in results.items():
    print(f"Test {name}: {ret}")
