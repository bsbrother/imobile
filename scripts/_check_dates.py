#!/usr/bin/env python3
"""Check if eastmoney-guba announcements have dates and can be filtered."""
import json
import requests

SEARXNG = "http://localhost:8080/search"

# Query eastmoney-guba with stock code
r = requests.get(SEARXNG, params={
    "q": "000001", "format": "json", "categories": "news", "language": "zh-CN"
}, timeout=15)
d = r.json()

print(f"Total results: {len(d.get('results',[]))}")
print("\n--- Eastmoney-guba results (with dates in display_time) ---")
for res in d.get("results", []):
    if res.get("engine") == "eastmoney-guba":
        print(f"  title: {res.get('title','')[:60]}")
        print(f"  url:   {res.get('url','')[:60]}")
        print(f"  content/snippet: {res.get('content','')[:80]}")
        print()

# The json_engine doesn't extract dates. Check the raw API response.
print("\n--- Raw API response for 000001 announcements ---")
r2 = requests.get("https://np-anotice-stock.eastmoney.com/api/security/ann",
    params={"sr": -1, "page_size": 10, "page_index": 1, "ann_type": "A", "client_source": "web", "stock_list": "000001"},
    headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
d2 = r2.json()
items = d2.get("data", {}).get("list", [])
print(f"Total items: {len(items)}")
for item in items[:5]:
    print(f"  {item.get('display_time','')[:19]}  {item.get('title','')[:60]}")

# Check if any items fall in 2025-03 window
from datetime import datetime
target = datetime(2025, 3, 15)
window = 30
in_window = []
for item in items:
    try:
        dt = datetime.strptime(item.get('display_time','')[:19], '%Y-%m-%d %H:%M:%S')
        if abs((dt - target).days) <= window:
            in_window.append(item)
    except:
        pass
print(f"\nItems in 2025-02-13 ~ 2025-04-14 window: {len(in_window)}")
for item in in_window[:3]:
    print(f"  {item.get('display_time','')[:19]}  {item.get('title','')[:60]}")
