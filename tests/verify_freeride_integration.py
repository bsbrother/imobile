# -*- coding: utf-8 -*-
"""
Simple verification test for FreeRide integration in ts_daily.py
"""

import os
import sys

# Ensure sys.path includes workspace root
workspace_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if workspace_root not in sys.path:
    sys.path.insert(0, workspace_root)

def test_imports():
    """Test that the modified ts_daily.py can be imported without errors."""
    try:
        from pick_stocks_from_sector.ts_daily import GeminiDailyAnalyzer, DailyAnalysisCache
        print("✅ SUCCESS: ts_daily.py imports correctly")
        print("   - GeminiDailyAnalyzer class available")
        print("   - DailyAnalysisCache class available")
        return True
    except Exception as e:
        print(f"❌ FAILED: Could not import ts_daily.py: {e}")
        return False

def test_freeride_configuration():
    """Test that the analyzer is configured for FreeRide."""
    try:
        from pick_stocks_from_sector.ts_daily import GeminiDailyAnalyzer
        analyzer = GeminiDailyAnalyzer()
        
        # Check model name
        expected_model = "openrouter/nvidia/nemotron-3-super-120b-a12b:free"
        if analyzer._model_name == expected_model:
            print(f"✅ SUCCESS: Model correctly set to {expected_model}")
        else:
            print(f"❌ FAILED: Model is {analyzer._model_name}, expected {expected_model}")
            return False
            
        # Check that client is initialized
        if analyzer._client is not None:
            print("✅ SUCCESS: OpenAI client initialized for FreeRide gateway")
        else:
            print("❌ FAILED: OpenAI client not initialized")
            return False
            
        # Check availability
        if analyzer.is_available():
            print("✅ SUCCESS: FreeRide model is available")
        else:
            print("❌ FAILED: FreeRide model is not available")
            return False
            
        return True
    except Exception as e:
        print(f"❌ FAILED: Error testing FreeRide configuration: {e}")
        return False

def test_no_gemini_code():
    """Verify that Gemini-specific code has been removed."""
    try:
        with open('./pick_stocks_from_sector/ts_daily.py', 'r') as f:
            content = f.read()
        
        # Check for Gemini imports
        if 'google.genai' in content:
            print("❌ FAILED: Google GenAI imports still present")
            return False
        else:
            print("✅ SUCCESS: Google GenAI imports removed")
            
        # Check for Gemini model usage
        if 'models.generate_content' in content:
            print("❌ FAILED: Gemini API still being used")
            return False
        else:
            print("✅ SUCCESS: Gemini API removed")
            
        # Check for OpenAI usage
        if 'from openai import OpenAI' in content and 'chat.completions.create' in content:
            print("✅ SUCCESS: OpenAI-compatible API being used")
        else:
            print("❌ FAILED: OpenAI-compatible API not properly configured")
            return False
            
        return True
    except Exception as e:
        print(f"❌ FAILED: Error checking code: {e}")
        return False

def main():
    """Run all verification tests."""
    print("=" * 60)
    print("FREERIDE INTEGRATION VERIFICATION FOR TS_DAILY")
    print("=" * 60)
    
    tests = [
        test_imports,
        test_freeride_configuration,
        test_no_gemini_code
    ]
    
    passed = 0
    total = len(tests)
    
    for test in tests:
        print()
        if test():
            passed += 1
        print("-" * 40)
    
    print()
    print("=" * 60)
    print(f"RESULTS: {passed}/{total} tests passed")
    
    if passed == total:
        print("🎉 ALL TESTS PASSED!")
        print("✅ The ts_daily strategy has been successfully modified to use FreeRide")
        print("   instead of Google Gemini for stock news/sentiment analysis.")
        print()
        print("Next steps:")
        print("1. Ensure FreeRide service is running on 127.0.0.1:11343")
        print("2. Run: /usr/bin/python3 pick_stocks_from_sector/ts_daily.py <target_date>")
        return True
    else:
        print("❌ SOME TESTS FAILED")
        print("Please review the errors above and fix the issues.")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)