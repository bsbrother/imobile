"""
Using Mobilerun's AndroidDriver and UIState to find and tap a date in a date picker:

Key points:

- AndroidStateProvider.get_state() returns a UIState with parsed elements
- ui.get_element_coords(index) returns center (x, y) coordinates
- driver.tap(x, y) taps at those coordinates

If the date picker is in a WebView and elements aren't exposed, you may need to use click_at(x, y) with known coordinates or vision-based detection.
"""
import asyncio
from mobilerun.tools import AndroidDriver
from mobilerun.tools.ui.provider import AndroidStateProvider
from mobilerun.tools.filters.concise_filter import ConciseFilter
from mobilerun.tools.formatters.indexed_formatter import IndexedFormatter

async def export_ui_state():
    """
    # Dump UI hierarchy to XML on device, then pull it
    adb shell uiautomator dump /sdcard/ui_dump.xml
    adb pull /sdcard/ui_dump.xml /tmp/ui_dump.xml
    """

    driver = AndroidDriver()
    await driver.connect()

    state_provider = AndroidStateProvider(driver, ConciseFilter(), IndexedFormatter())
    ui = await state_provider.get_state()

    # Option A: Save formatted text representation
    with open("/tmp/ui_state.txt", "w") as f:
        f.write(ui.formatted_text)

    # Option B: Save raw elements as JSON
    import json
    with open("/tmp/ui_elements.json", "w") as f:
        json.dump(ui.elements, f, indent=2)

    # Option C: Get raw UI tree directly
    tree = await driver.get_ui_tree()
    with open("/tmp/ui_tree.json", "w") as f:
        json.dump(tree, f, indent=2)


async def find_and_tap_date(target_day: str = "01"):
    # Initialize driver
    driver = AndroidDriver()
    await driver.connect()

    # Create state provider to get UIState
    state_provider = AndroidStateProvider(driver, ConciseFilter(), IndexedFormatter())
    ui = await state_provider.get_state()

    # Search through elements for the target day
    for i, element in enumerate(ui.elements):
        text = element.get("text", "")
        content_desc = element.get("contentDescription", "")

        # Match the day number (e.g., "01" or "1")
        if text == target_day or text == target_day.lstrip("0"):
            try:
                x, y = ui.get_element_coords(i)
                print(f"Found day '{target_day}' at index {i}, coords ({x}, {y})")
                await driver.tap(x, y)
                print("Tapped successfully!")
                return
            except ValueError:
                continue

    print(f"Day '{target_day}' not found in UI tree")


if __name__ == '__main__':
    # Saved UI state tree to /tmp/xx for find the date button.
    asyncio.run(export_ui_state())

    # e.g. Today is 2026-07-01, now find and tap  01 button on the date picker.
    asyncio.run(find_and_tap_date("01"))
