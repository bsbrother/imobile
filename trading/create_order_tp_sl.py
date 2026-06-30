#!/usr/bin/env python3
"""
Fill all fields on the "止盈止损" (take profit and stop loss) order page.

Prerequisites:
  - The app must be connected via ADB and logged in (handled automatically via goto_page).
  - Run `mobilerun device ui` first to verify element positions.

Usage:
  python trading/order_tp_sl.py --code 600279 --tp 4.10 --sl 3.80 --quantity 900
  python trading/order_tp_sl.py --code 600279 --json path_to_smart_orders.json
  python trading/order_tp_sl.py --batch  # Run steps 1-3 for all holding stocks

Interaction notes:
  - Stock code: tapping opens a separate "选择股票" overlay with a search EditText.
    Type the code, select the matching stock, app auto-returns to the TP/SL page.
  - TP/SL prices: WebView EditText fields for take-profit and stop-loss triggers.
    Must use `adb shell input keyevent` (DEL to clear) + `adb shell input text`.
  - Quantity: WebView EditText for number of shares to sell.
  - valid_until: Taps the date field (WebView, bounds=[0,0]) to open calendar picker,
    then taps the target day number (e.g. "30") and confirms "确定".
    Target is 1 trading day: today if <15:00, next trading day if >=15:00.
    TP/SL form date value at ~(500,2900) — different from BUY form's ~(400,1550).
    See utils/tools.py set_valid_until_today() for coordinate fallback logic.
"""

import os
import sys
import time
import argparse
import subprocess
import re
