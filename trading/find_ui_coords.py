import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.tools import get_ui_tree, find_element_center, find_edittext_near_label, device_tap, device_swipe, adb_type
from trading.guotai import open_app, login, goto_homepage, replay_page

def extract_smart_buy_coords():
    print("\n--- Extracting Smart BUY Order Form Coordinates ---")
    open_app()
    login()
    goto_homepage()
    replay_page(['今日触发'])
    
    time.sleep(2)
    ui_text = get_ui_tree()
    
    btn_buy = find_element_center(ui_text, '到价买入')
    if not btn_buy:
        for _ in range(5):
            device_swipe(720, 2000, 720, 500, sleep_after=1.5)
            ui_text = get_ui_tree()
            btn_buy = find_element_center(ui_text, '到价买入')
            if btn_buy: break
    print(f"到价买入 (Button): {btn_buy}")
    if btn_buy:
        device_tap(*btn_buy, sleep_after=2)
    
    # Scroll to top of page
    device_swipe(720, 500, 720, 1500, sleep_after=1.5)
    ui_text = get_ui_tree()
    
    # 1. Stock Code
    stock_code_field = find_element_center(ui_text, '输入股票代码') or find_element_center(ui_text, '股票名称/代码')
    print(f"Stock Code Field: {stock_code_field}")
    
    # 2. Trigger Condition
    cond_field = find_element_center(ui_text, '当股价 ≥') or find_element_center(ui_text, '当股价 ≤')
    print(f"Trigger Condition Field: {cond_field}")
    
    # 3. Trigger Price
    trigger_price_field = find_edittext_near_label(ui_text, '触发价')
    print(f"Trigger Price Field: {trigger_price_field}")
    
    # Now tap stock code to open overlay
    if stock_code_field:
        device_tap(*stock_code_field, sleep_after=2)
        ui_overlay = get_ui_tree()
        
        search_bar = find_edittext_near_label(ui_overlay, '') or find_element_center(ui_overlay, '输入股票代码')
        print(f"Overlay Search Bar: {search_bar}")
        
        # Type to get results
        adb_type('000001')
        time.sleep(2)
        ui_overlay_results = get_ui_tree()
        first_result = find_element_center(ui_overlay_results, '000001')
        print(f"Overlay First Result: {first_result}")
        
        if first_result:
            device_tap(*first_result, sleep_after=2)
    
    # Swipe down to see remaining fields
    device_swipe(720, 2000, 720, 500, sleep_after=1.5)
    ui_text2 = get_ui_tree()
    
    # 4. Order Method
    order_method = find_element_center(ui_text2, '最新价') or find_element_center(ui_text2, '委托方式')
    print(f"Order Method Field: {order_method}")
    
    # 5. Quantity
    quantity_field = find_edittext_near_label(ui_text2, '买入数量')
    print(f"Quantity Field: {quantity_field}")
    
    # 6. Order Type
    order_type = find_element_center(ui_text2, '自动下单') or find_element_center(ui_text2, '确认下单')
    print(f"Order Type (Auto): {order_type}")
    
    # 7. Valid Until
    valid_until = find_element_center(ui_text2, '有效期至')
    print(f"Valid Until Label: {valid_until}")
    
    # 8. Submit button
    submit_btn = find_element_center(ui_text2, '创建订单')
    print(f"Submit Button: {submit_btn}")

def extract_ordinary_buy_coords():
    print("\n--- Extracting Ordinary BUY Order Form Coordinates ---")
    goto_homepage()
    replay_page(['买入'])
    time.sleep(2)
    ui_text = get_ui_tree()
    
    stock_code_field = find_element_center(ui_text, '输入股票代码') or find_element_center(ui_text, '证券代码')
    print(f"Stock Code Field (Ordinary): {stock_code_field}")
    
    price_field = find_edittext_near_label(ui_text, '买入价格') or find_edittext_near_label(ui_text, '价格')
    print(f"Price Field (Ordinary): {price_field}")
    
    quantity_field = find_edittext_near_label(ui_text, '买入数量') or find_edittext_near_label(ui_text, '数量')
    print(f"Quantity Field (Ordinary): {quantity_field}")
    
    submit_btn = find_element_center(ui_text, '买入')
    print(f"Submit Button (Ordinary): {submit_btn}")

def extract_tp_sl_coords():
    print("\n--- Extracting Smart TP/SL Order Form Coordinates ---")
    open_app()
    login()
    goto_homepage()
    replay_page(['今日触发'])
    
    time.sleep(2)
    ui_text = get_ui_tree()

    btn_tpsl = find_element_center(ui_text, '止盈止损')
    if not btn_tpsl:
        for _ in range(5):
            device_swipe(720, 2000, 720, 500, sleep_after=1.5)
            ui_text = get_ui_tree()
            btn_tpsl = find_element_center(ui_text, '止盈止损')
            if btn_tpsl: break
    print(f"止盈止损 (Button): {btn_tpsl}")
    if btn_tpsl:
        device_tap(*btn_tpsl, sleep_after=2)

    device_swipe(720, 500, 720, 1500, sleep_after=1.5)
    ui_text = get_ui_tree()

    # 1. Stock Code
    stock_code_field = find_element_center(ui_text, '输入股票代码') or find_element_center(ui_text, '股票名称/代码')
    print(f"Stock Code Field: {stock_code_field}")
    
    # 2. TP Price
    tp_price_field = find_edittext_near_label(ui_text, '止盈触发')
    print(f"TP Price Field: {tp_price_field}")
    
    device_swipe(500, 1000, 500, 300, sleep_after=1.5)
    ui_text2 = get_ui_tree()
    
    # 3. SL Price
    sl_price_field = find_edittext_near_label(ui_text2, '止损触发')
    print(f"SL Price Field: {sl_price_field}")

    # 4. Quantity
    quantity_field = find_edittext_near_label(ui_text2, '卖出数量') or find_edittext_near_label(ui_text2, '委托数量')
    print(f"Quantity Field: {quantity_field}")

if __name__ == '__main__':
    extract_tp_sl_coords()
