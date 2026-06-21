#!/usr/bin/env python3
"""
Navigate to a specific order creation page in the Guotai app.

Flow:
  1. Open the app (via guotai.py open_app())
  2. Ensure homepage & login (via guotai.py login(), which calls goto_homepage())
  3. Tap '创建订单' to open the smart order page
  4. Tap the requested sub-page button (到价买入 / 到价卖出 / 止盈止损)

Usage (standalone):
  python trading/goto_page.py                     # default: order_buy
  python trading/goto_page.py --page order_buy    # 到价买入
  python trading/goto_page.py --page order_sell   # 到价卖出
  python trading/goto_page.py --page order_tp_sl  # 止盈止损

Usage (imported):
  from trading.goto_page import goto_page
  goto_page('order_sell')
"""

import os
import sys
import time
import argparse
import subprocess

from loguru import logger

# Ensure project root is on path so `trading.guotai` is importable
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from trading.guotai import open_app, login, goto_homepage


# ---------------------------------------------------------------------------
# Page definitions
# ---------------------------------------------------------------------------

PAGE_LABELS = {
    'order_buy':   '到价买入',
    'order_sell':  '到价卖出',
    'order_tp_sl': '止盈止损',
}

# ---------------------------------------------------------------------------
# Low-level helpers (self-contained, no dependency on create_order_*.py)
# ---------------------------------------------------------------------------

def _run(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    logger.debug(f"$ {cmd}")
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    if check and result.returncode != 0:
        logger.error(f"Command failed: {cmd}\nstderr: {result.stderr}")
        raise RuntimeError(f"Command failed: {cmd}")
    return result


def _tap(x: int, y: int, sleep_after: float = 1.5) -> None:
    _run(f"mobilerun device tap {x} {y}")
    time.sleep(sleep_after)


def _get_ui() -> str:
    return _run("mobilerun device ui", check=False).stdout


def _find_center(ui_text: str, label: str) -> tuple[int, int] | None:
    """Return (cx, cy) for the first element whose line contains *label*.

    Handles both UI dump formats:
      - 'NN. TextView: "label" - (x1,y1,x2,y2)'
      - 'NN. TextView: "resource_id", "label" - (x1,y1,x2,y2)'
    """
    import re
    for line in ui_text.split('\n'):
        if label in line:
            m = re.search(r'\((\d+),(\d+),(\d+),(\d+)\)', line)
            if m:
                x1, y1, x2, y2 = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
                return (x1 + x2) // 2, (y1 + y2) // 2
    return None


# ---------------------------------------------------------------------------
# Core navigation
# ---------------------------------------------------------------------------

def _goto_create_order_page() -> None:
    """From the homepage, scroll down and tap '创建订单' to open the smart order page."""
    logger.info("Looking for '创建订单' button on homepage…")

    # The button is below the fold — scroll down first
    _run("mobilerun device swipe 720 1500 720 500", check=False)
    time.sleep(2)

    ui = _get_ui()
    center = _find_center(ui, '创建订单')
    if not center:
        # Try one more scroll in case the page layout differs
        logger.info("'创建订单' not found yet, scrolling further…")
        _run("mobilerun device swipe 720 1500 720 500", check=False)
        time.sleep(2)
        ui = _get_ui()
        center = _find_center(ui, '创建订单')

    if not center:
        raise RuntimeError(
            "Cannot find '创建订单' button on homepage after scrolling. "
            "Run `mobilerun device ui` to inspect the current screen."
        )

    logger.info(f"Tapping '创建订单' at {center}")
    _tap(*center, sleep_after=3)  # Give WebView time to start loading

    # Verify we landed on the smart-order selection page.
    # The WebView can be slow; retry up to 5 times (5 extra seconds).
    for attempt in range(1, 6):
        ui = _get_ui()
        if '到价买入' in ui:
            logger.info("✅ Smart order selection page reached.")
            return
        logger.info(f"Waiting for smart order page to load (attempt {attempt}/5)…")
        time.sleep(1)

    raise RuntimeError(
        "Did not reach the smart order page after tapping '创建订单'. "
        "Run `mobilerun device ui` to inspect the current screen."
    )


def _tap_order_page(page: str) -> None:
    """Tap the button for the requested page (到价买入 / 到价卖出 / 止盈止损)."""
    label = PAGE_LABELS.get(page)
    if label is None:
        raise ValueError(f"Unknown page '{page}'. Valid: {list(PAGE_LABELS)}")

    ui = _get_ui()
    center = _find_center(ui, label)
    if not center:
        raise RuntimeError(f"Cannot find '{label}' button on smart order page.")

    logger.info(f"Tapping '{label}' at {center}")
    _tap(*center, sleep_after=2)

    # Verify
    ui = _get_ui()
    # Each sub-page has a distinctive header text
    verify_texts = {
        'order_buy':   '到价买入',
        'order_sell':  '到价卖出',
        'order_tp_sl': '止盈止损',
    }
    if verify_texts[page] not in ui:
        logger.warning(f"Could not confirm arrival on '{label}' page. Proceeding anyway.")
    else:
        logger.info(f"✅ Now on '{label}' page.")


def goto_page(page: str = 'order_buy') -> None:
    """
    Full navigation: open_app → login → homepage → 创建订单 → target page.

    Args:
        page: One of 'order_buy' (default), 'order_sell', 'order_tp_sl'.
    """
    if page not in PAGE_LABELS:
        raise ValueError(f"Unknown page '{page}'. Valid: {list(PAGE_LABELS)}")

    logger.info(f"[goto_page] Navigating to '{page}' ({PAGE_LABELS[page]})")

    # Step 1: Make sure the app is running
    open_app()

    # Step 2: Login if needed. login() calls goto_homepage() at the start,
    #         but after replaying the login trajectory the app may land on a
    #         post-login screen. We call goto_homepage() again afterwards to
    #         guarantee we are at the trading homepage before navigating.
    login()
    goto_homepage()

    # Step 3: Open smart order selection page
    _goto_create_order_page()

    # Step 4: Tap the target sub-page
    _tap_order_page(page)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Navigate to a Guotai smart order page.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Pages:
  order_buy   - 到价买入 (default)
  order_sell  - 到价卖出
  order_tp_sl - 止盈止损

Examples:
  python trading/goto_page.py
  python trading/goto_page.py --page order_sell
  python trading/goto_page.py --page order_tp_sl
        """,
    )
    parser.add_argument(
        '--page',
        type=str,
        default='order_buy',
        choices=list(PAGE_LABELS),
        help='Target page (default: order_buy)',
    )
    args = parser.parse_args()
    goto_page(args.page)


if __name__ == '__main__':
    main()
