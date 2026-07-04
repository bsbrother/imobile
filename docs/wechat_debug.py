#!/usr/bin/env python3
"""Compare curl vs browser HTML for WeChat article."""
import asyncio
from playwright.async_api import async_playwright

URL = 'https://mp.weixin.qq.com/s/YnYSOTH8ezySPoGnlQKcLw'

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        await page.goto(URL, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(2)
        html = await page.content()
        await browser.close()
        
        import subprocess
        curl = subprocess.run(['curl', '-sL', URL, '-H', 'User-Agent: Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36'],
                            capture_output=True, text=True, timeout=15)
        
        print(f"Browser HTML: {len(html)} chars")
        print(f"Curl HTML:    {len(curl.stdout)} chars")
        
        # Key indicators
        for label, h in [("Browser", html), ("Curl", curl.stdout)]:
            has_js = 'js_content' in h
            has_appmsg = 'appmsg_token' in h
            has_error = '参数错误' in h
            has_ct = 'var ct' in h
            print(f"{label}: js_content={has_js}, appmsg_token={has_appmsg}, error={has_error}, ct={has_ct}")
        
        # Print first 300 chars of browser HTML
        print(f"\nBrowser HTML head:\n{html[:500]}")

asyncio.run(main())
