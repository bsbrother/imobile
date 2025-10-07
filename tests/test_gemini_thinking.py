#!/usr/bin/env python3
"""
Simple test for Google Gemini thinking mode functionality
"""
import os
import dotenv

# Load environment variables
dotenv.load_dotenv(os.path.expanduser('.env'), verbose=True)

def test_thinking_mode():
    """Test the thinking mode implementation"""
    try:
        from droidrun.agent.utils.gemini_thinking import create_gemini_with_thinking
        
        # Test creating Gemini with thinking mode
        llm = create_gemini_with_thinking(
            model="gemini-2.5-flash",  
            thinking_budget=-1,  # Unlimited thinking
            temperature=0.1
        )
        
        print("✅ Successfully created GoogleGenAI with thinking mode")
        
        # Test a simple completion
        response = llm.complete("What is 2 + 2? Please think through this step by step.")
        print(f"Response: {response}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing thinking mode: {e}")
        import traceback
        traceback.print_exc()
        return False

def test_llm_picker():
    """Test the LLM picker with thinking mode"""
    try:
        # Set thinking budget in environment
        os.environ['GEMINI_THINKING_BUDGET'] = '-1'
        
        from droidrun.agent.utils.llm_picker import load_llm
        
        # Load GoogleGenAI with thinking mode
        llm = load_llm("GoogleGenAI", model="gemini-2.5-flash")
        
        print("✅ Successfully loaded GoogleGenAI with thinking mode via LLM picker")
        
        # Test a simple completion
        response = llm.complete("Explain briefly what thinking mode does for Gemini.")
        print(f"Response: {response}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error testing LLM picker: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    print("=" * 60)
    print("Testing Google Gemini Thinking Mode Implementation")
    print("=" * 60)
    
    print("\n1. Testing direct thinking mode creation...")
    test_thinking_mode()
    
    print("\n2. Testing thinking mode via LLM picker...")
    test_llm_picker()
    
    print("\n✅ Test completed!")