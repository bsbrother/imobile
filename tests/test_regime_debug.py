
import sys
import os
# Mock config if needed, or rely on actual config
# We need to make sure we are in the right directory or path is set
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.utils.market_regime import detect_market_regime

def test_regime():
    dates_to_test = [
        ('2025-10-09', 'bull'),  # We know this should be Bull now
        ('2025-11-11', None)     # Check what this returns
    ]

    print("=== Testing Valid Dates ===")
    for date, expected in dates_to_test:
        print(f"\nTesting regime for {date}...")
        try:
            regime_data = detect_market_regime(date)
            print(f"Regime: {regime_data['regime']}")
            print(f"Config: StopLoss={regime_data.get('stop_loss_pct')}, TakeProfit={regime_data.get('take_profit_pct')}")
            
            if expected and regime_data['regime'] != expected:
                print(f"WARNING: Expected {expected}, got {regime_data['regime']}")
            else:
                print("Check: OK")
                
        except Exception as e:
            print(f"ERROR: Failed to detect regime for {date}: {e}")

    print("\n=== Testing Insufficient Data (Should Raise Error) ===")
    bad_date = '1990-12-19' # Early SSE date, might not have 60 days prior
    print(f"Testing regime for {bad_date}...")
    try:
        detect_market_regime(bad_date)
        print("ERROR: Should have raised ValueError but didn't")
    except ValueError as e:
        print(f"SUCCESS: Caught expected error: {e}")
    except Exception as e:
        print(f"WARNING: Caught unexpected error type: {type(e).__name__}: {e}")

if __name__ == "__main__":
    test_regime()
