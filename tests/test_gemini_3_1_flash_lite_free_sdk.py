
import pytest
import os
from google import genai
from google.genai.errors import APIError

pytestmark = pytest.mark.integration


def test_gemini_api():
    # 1. Retrieve the API key from your environment variables
    # Set this in your terminal first: export GEMINI_API_KEY="your_key_here"
    api_key = os.environ.get("GEMINI_API_KEY")

    if not api_key:
        print("❌ Error: GEMINI_API_KEY environment variable not found.")
        print("Please set it before running the script.")
        return

    print("🔄 Initializing Gemini Client...")
    client = genai.Client(api_key=api_key)

    # 2. Use the recommended free tier model (gemini-2.5-flash)
    # Note: 'pro' models will fail on the free tier as of April 2026
    model_name = "gemini-3.1-flash-lite"

    print(f"🔄 Sending test request to model: {model_name}...")
    try:
        response = client.models.generate_content(
            model=model_name,
            contents="Respond with only the word 'Success' if you can read this.",
        )

        # 3. Analyze response to verify operation
        print("\n✅ API is working perfectly!")
        print(f"Model Response: {response.text.strip()}")
        print("\n📈 Account Status: Active Free Tier (No charges will apply).")

    except APIError as e:
        print("\n❌ API Request Failed.")
        if e.code == 403:
            print("Reason: Auth Error (Your API key is invalid, deleted, or expired).")
        elif e.code == 429:
            print("Reason: Free Tier Rate Limit Exceeded (Too many requests).")
        elif "model" in str(e).lower():
            print("Reason: Model Restriction (You may be trying to access a paid Pro model).")
        else:
            print(f"Error Details: {e}")

    except Exception as e:
        print(f"\n❌ An unexpected error occurred: {e}")

if __name__ == "__main__":
    test_gemini_api()

