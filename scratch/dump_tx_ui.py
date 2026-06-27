import sys
import os
import asyncio

sys.path.insert(0, '/home/kasm-user/apps/imobile')
from trading.sync_app_to_db import pre_requirements, goto_homepage
from utils.tools import get_ui_tree, device_tap, find_element_center, device_swipe

async def main():
    tools, llm, config = await pre_requirements()
    print("Navigating to history transactions page...")
    goto_homepage()
    ui = get_ui_tree()
    trading_tab_center = find_element_center(ui, "btm_text3") or find_element_center(ui, "bottom_menu_button3") or (720, 2880)
    device_tap(*trading_tab_center, sleep_after=3)
    
    ui = get_ui_tree()
    p = find_element_center(ui, "我知道了")
    if p:
        device_tap(*p, sleep_after=1.5)
        ui = get_ui_tree()
        
    center_history = find_element_center(ui, "历史成交")
    if not center_history:
        device_swipe(720, 2000, 720, 500, sleep_after=2)
        ui = get_ui_tree()
        center_history = find_element_center(ui, "历史成交") or (180, 1317)
        
    device_tap(*center_history, sleep_after=3)
    
    # Now dump the UI tree of the transaction history page
    ui = get_ui_tree()
    out_path = "/home/kasm-user/apps/imobile/scratch/tx_ui_tree.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(ui)
    print(f"UI tree successfully saved to {out_path}")

if __name__ == '__main__':
    asyncio.run(main())
