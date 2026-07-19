"""
Test: gemini-3.1-flash-lite-preview via llama_index GoogleGenAI.
Uses exact same imports as gemini_free_api.py and DroidRun MobileAgent.

Expected: 405 Method Not Allowed on client.models.get(model=...)
On google ai studio ->create new project ->create new API (iset norestrict)
"""
import pytest
import os
import asyncio
import dotenv
dotenv.load_dotenv('.env')

from llama_index.llms.google_genai import GoogleGenAI
from mobilerun import MobileAgent

pytestmark = pytest.mark.integration


GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-lite-preview")

print(f"Key: {GOOGLE_API_KEY[:10]}...")
print(f"Model: {GEMINI_MODEL}")


def test_google_genai_init():
    """Test 1: GoogleGenAI.__init__ — this is where 405 happens."""
    print("\n--- Test 1: GoogleGenAI.__init__ ---")
    try:
        llm = GoogleGenAI(model=GEMINI_MODEL, api_key=GOOGLE_API_KEY, temperature=0.2)
        print(f"✅ GoogleGenAI created: model={llm.model}")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")


async def test_mobile_agent():
    """Test 2: MobileAgent — needs working GoogleGenAI."""
    print("\n--- Test 2: MobileAgent ---")
    try:
        llm = GoogleGenAI(model=GEMINI_MODEL, api_key=GOOGLE_API_KEY, temperature=0.2)
        MobileAgent(goal="Tap OK button", llms=llm)
        print("✅ MobileAgent created")
    except Exception as e:
        print(f"❌ {type(e).__name__}: {e}")


if __name__ == "__main__":
    test_google_genai_init()
    # MobileAgent test skipped unless device connected
    asyncio.run(test_mobile_agent())


    """
    # CLI
    export GOOGLE_API_KEY=your-api-key-here
    mobilerun run "Your task here" \
      --provider GoogleGenAI \
      --model gemini-3.1-flash-lite-preview
    #
    """
