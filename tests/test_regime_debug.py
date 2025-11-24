
import sys
import os
# Mock config if needed, or rely on actual config
# We need to make sure we are in the right directory or path is set
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.utils.market_regime import detect_market_regime

def test_regime():
    date = '2025-10-09'
    print(f"Testing regime for {date}...")
    regime_data = detect_market_regime(date)
    print(f"Regime Data: {regime_data}")

    stop_loss = regime_data.get('stop_loss_pct')
    print(f"Stop Loss Pct: {stop_loss}")

    if stop_loss is None:
        print("ERROR: stop_loss_pct is None")
    elif stop_loss == 0.10:
        print("WARNING: stop_loss_pct is 0.10 (default?)")
    else:
        print(f"OK: stop_loss_pct is {stop_loss}")

if __name__ == "__main__":
    test_regime()
