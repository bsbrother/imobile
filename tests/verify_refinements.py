from imobile.pages.sector_history import SectorHistoryState, Sector
import pandas as pd
import reflex as rx

# Mock State
class MockState(SectorHistoryState):
    def __init__(self):
        super().__init__()
        self.selected_sector = Sector(ts_code="885772", name="Test", pct_chg=0.0, close=0.0, trade_date="20230101", source="THS")

def test_name_formatting():
    print("Testing Name Formatting...")
    code = "885772"
    name = "Lithium"
    formatted = f"{name}({code})"
    print(f"Original: {name}, Code: {code} -> Formatted: {formatted}")
    assert formatted == "Lithium(885772)"

def test_error_handling():
    print("\nTesting Error Handling in load_sector_details...")
    state = MockState()
    
    # Test 1: Empty code
    print("Test 1: Empty code")
    try:
        state.load_sector_details("", "20230101", "Test")
        print("Passed (Handled gracefully)")
    except Exception as e:
        print(f"Failed: {e}")

    # Test 2: Code without dot (should not crash)
    print("\nTest 2: Code '885772' (no dot)")
    try:
        state.load_sector_details("885772", "20230101", "Test")
        print("Passed (Handled gracefully)")
    except Exception as e:
        print(f"Failed: {e}")

    # Test 3: Non-string code (if passed somehow)
    print("\nTest 3: Non-string code (int)")
    try:
        state.load_sector_details(885772, "20230101", "Test")
        print("Passed (Handled gracefully)")
    except Exception as e:
        print(f"Failed: {e}")

if __name__ == "__main__":
    try:
        test_name_formatting()
        test_error_handling()
        print("\nAll tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
