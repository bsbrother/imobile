#!/usr/bin/env python3
import os
import sys
import asyncio
import dotenv

# Add the parent directory to Python path so we can import from utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from droidrun import DroidAgent, AdbTools
from droidrun.tools import Tools
from utils.gemini_thinking import create_gemini_with_thinking

# Load environment variables
dotenv.load_dotenv(os.path.expanduser('.env'), verbose=True)
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_THINKING_BUDGET = os.getenv("GEMINI_THINKING_BUDGET", "-1")
if not GOOGLE_API_KEY:
    raise ValueError("❌ GOOGLE_API_KEY not set. Skipping DroidAgent test.")
    

def test_device_connectivity(tools: Tools):
    """Test if the device is connected and the DroidRun Portal is working."""
    print("🔧 Testing device connectivity...")
    try:
        # Test get_state to check accessibility service
        state = tools.get_state()
        if 'phone_state' in state and 'a11y_tree' in state:
            print("✅ Device state retrieved successfully - accessibility service is working")
            return True
        else:
            print(f"⚠️ Incomplete state retrieved: {list(state.keys())}")
            return False
    except Exception as e:
        print(f"❌ Failed to get device state: {e}")
        print("💡 This indicates the accessibility service is not enabled.")
        return False


async def droid_run(tools:Tools, goal:str | None):
    if not goal:
        raise ValueError("❌ Goal not set. Please provide a goal.")

    llm = create_gemini_with_thinking(
        model=GEMINI_MODEL,
        api_key=GOOGLE_API_KEY,
        thinking_budget=int(GEMINI_THINKING_BUDGET),
        temperature=0.1
    )

    agent = DroidAgent(
        # goal="Open Chrome and search for weather",  # Google network or check bot.
        goal=goal,
        llm=llm,
        tools=tools,
        vision=True,         # Set to True for vision models, False for text-only
        reasoning=True,      # Optional: enable planning/reasoning
        timeout=10000,
        enable_tracing=False,  # Requires running 'phoenix serve' in a separate terminal first
    )

    result = await agent.run()
    print(f"Success: {result['success']}")
    if result.get('output'):
        print(f"Output: {result['output']}")
      

def get_first_device_serial():
    """Get the serial number of the first connected Android device."""
    import subprocess
    result = subprocess.run(['adb', 'devices'], capture_output=True, text=True)
    lines = result.stdout.strip().split('\n')
    devices = [line.split()[0] for line in lines[1:] if 'device' in line]
    if not devices:
        raise RuntimeError("No connected Android devices found.")
    return devices[0]


if __name__ == "__main__":
    serial = get_first_device_serial()
    if not serial:
        raise ValueError("No connected Android device found. Please connect a device via ADB.")
    # load adb tools for the first connected device
    tools = AdbTools(serial=serial)
    # Test connectivity first
    if not test_device_connectivity(tools):
        print("\n" + "="*60)
        print("SETUP REQUIRED:")
        print("1. Install DroidRun Portal on your Android device: droidrun setup")
        print("2. Enable the DroidRun Portal accessibility service:")
        print("   Settings > Accessibility > DroidRun Portal > Enable")
        print("3. Verify Setup: droidrun ping")
        print("4. Ensure ADB is working: adb devices")
        print("="*60)
        exit(1)

    goal="Open Settings and check battery level, then close app and go back to home screen"
    print(f'Using device with serial: {serial}, 'f'Goal: {goal}')
    asyncio.run(droid_run(tools=tools, goal=goal))