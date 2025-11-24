from imobile.pages.sector_history import SectorHistoryState, Sector
import reflex as rx

# Mock State
class MockState(SectorHistoryState):
    def __init__(self):
        super().__init__()
        # Mock cache
        self._ths_concepts_cache = [
            {'ts_code': '885772', 'name': '锂电池'},
            {'ts_code': '885928', 'name': '新能源汽车'},
            {'ts_code': '881234', 'name': '人工智能'},
        ]

def test_matching():
    state = MockState()
    
    # Test 1: Exact match (ignoring suffix)
    print("Test 1: '锂电池概念' (DC) -> '锂电池' (THS)")
    state.find_related_ths_sectors("锂电池概念")
    print(f"Found: {[s.name for s in state.related_ths_sectors]}")
    assert any(s.name == '锂电池' for s in state.related_ths_sectors)
    
    # Test 2: Partial match
    print("\nTest 2: '新能源' (DC) -> '新能源汽车' (THS)")
    state.find_related_ths_sectors("新能源")
    print(f"Found: {[s.name for s in state.related_ths_sectors]}")
    assert any(s.name == '新能源汽车' for s in state.related_ths_sectors)
    
    # Test 3: No match
    print("\nTest 3: 'Unknown' -> []")
    state.find_related_ths_sectors("Unknown")
    print(f"Found: {[s.name for s in state.related_ths_sectors]}")
    assert len(state.related_ths_sectors) == 0

if __name__ == "__main__":
    try:
        test_matching()
        print("\nAll tests passed!")
    except Exception as e:
        print(f"\nTest failed: {e}")
