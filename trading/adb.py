#!/usr/bin/env python3
import os
import sys
import subprocess
import asyncio
import dotenv

from mobilerun import AndroidDriver

# Add the parent directory to Python path so we can import from utils
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load environment variables
dotenv.load_dotenv(os.path.expanduser('.env'), verbose=True)
GUOTAI_PACKAGE_NAME = os.getenv('GUOTAI_PACKAGE_NAME')
if not GUOTAI_PACKAGE_NAME:
    raise ValueError("❌ GUOTAI_PACKAGE_NAME not set. Please set it in the .env file.")


def get_device_serials():
    """
    Get a list of connected device serial numbers using adb. same as `understand` command:
    droidrun devices    # List all connected devices and their serials
    adb devices         # Or using ADB directly
    """
    result = subprocess.run(['adb', 'devices'], capture_output=True, text=True)
    lines = result.stdout.strip().split('\n')[1:]  # Skip header
    devices = []
    for line in lines:
        if '\tdevice' in line:
            serial = line.split('\t')[0]
            devices.append(serial)
    return devices

async def get_device_connectivity() -> AndroidDriver:
    """if the device is connected and the DroidRun Portal is working, return the AndroidDriver instance.
    Raises an error if no device is connected or if the accessibility service is not enabled.
    """
    devices = get_device_serials()
    if not devices:
        raise RuntimeError("No connected Android devices found. See README.md for setup instructions.")
    try:
        tools = AndroidDriver(serial=devices[0])
        # Test screenshot to check device connectivity (get_state removed in mobilerun 0.6.x)
        screenshot = await tools.screenshot()
        if screenshot:
            print("✅ Device screenshot retrieved successfully - connection is working")
            return tools
        else:
            raise RuntimeError("Device screenshot returned empty")
    except Exception as e:
        print("\n" + "="*60)
        print("SETUP REQUIRED:")
        print("1. Install DroidRun Portal on your Android device: droidrun setup")
        print("2. Enable the DroidRun Portal accessibility service:")
        print("   Settings > Accessibility > DroidRun Portal > Enable")
        print("3. Verify Setup: droidrun ping")
        print("4. Ensure ADB is working: adb devices")
        print("="*60)
        raise RuntimeError(f"❌ Failed to get device state: {e}")

async def check_app_exist(tools: AndroidDriver, app_name: str | None = None):
    """
    Check if the app is installed on the device.

    Args:
        app_name (str): Package name of the app to check (e.g., 'com.guotai.dazhihui')

    Returns:
        bool: True if the app is installed, False otherwise
    """

    try:
        packages = await tools.list_packages(include_system_apps=False)
        if app_name not in packages:
            raise
        print(f"✅ App '{app_name}' is installed on mobile.")
    except Exception as e:
        print(f"✗ App '{app_name}' is not installed on mobile, See README.md for setup instructions.")
        raise RuntimeError(f"❌ Failed to check app exist: {e}")


async def main():
    tools = await get_device_connectivity()
    await check_app_exist(tools, GUOTAI_PACKAGE_NAME)

if __name__ == "__main__":
    asyncio.run(main())

