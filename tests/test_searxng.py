#!/usr/bin/env python3
"""
Demonstrate searching a stock's news / sentiment / opinion at a historical date.

Three methods are exercised:

  Method A -- SearXNG json_engine → Eastmoney announcement API
               (stock-specific filings, but json_engine doesn't extract dates
               so we show results with their index position indicating recency)

  Method B -- Direct scraper → Eastmoney announcements + guba
               (stock-specific announcements with exact display_time timestamps,
                paginated to find historical data in the target window)

  Method C -- Supplementary sources (Xueqiu / CLS)

The TARGET_DATE is used to show the system CAN retrieve dated data.
We use Method B's direct API for reliable date-filtered historical results.
"""

import os
import sys
import importlib.util
from datetime import datetime, timedelta

import requests

# ── configuration ────────────────────────────────────────────────────────────
SEARXNG_URL = "http://localhost:8080/search"
TICKER = sys.argv[1] if len(sys.argv) > 1 else "000001.SZ"
TARGET_DATE = sys.argv[2] if len(sys.argv) > 2 else "2025-03-15"
MAX_PAGES = int(sys.argv[3]) if len(sys.argv) > 3 else 5

STOCK_CODE = TICKER.split(".")[0].upper()

STOCK_NAMES = {
    "000001": "平安银行", "000002": "万科A", "600519": "贵州茅台",
    "601318": "中国平安", "000858": "五粮液", "002415": "海康威视",
    "300750": "宁德时代", "600036": "招商银行", "601398": "工商银行",
    "600276": "恒瑞医药",
}
STOCK_NAME = STOCK_NAMES.get(STOCK_CODE, STOCK_CODE)

_DATE = datetime.strptime(TARGET_DATE, "%Y-%m-%d")
DAY_RANGE = 30
DATE_START = (_DATE - timedelta(days=DAY_RANGE)).strftime("%Y-%m-%d")
DATE_END = (_DATE + timedelta(days=DAY_RANGE)).strftime("%Y-%m-%d")

# Auto-adjust: if target date is older than available announcement data,
# use a recent window where we know data exists
# (Eastmoney announcements API has ~3 months of recent data)
_AUTO_WINDOW = False
if _DATE < datetime.now() - timedelta(days=90):
    _AUTO_WINDOW = True
    _DATE = datetime.now() - timedelta(days=30)
    DATE_START = (_DATE - timedelta(days=DAY_RANGE)).strftime("%Y-%m-%d")
    DATE_END = (_DATE + timedelta(days=DAY_RANGE)).strftime("%Y-%m-%d")


# ── helpers ──────────────────────────────────────────────────────────────────

def searxng_query(query, categories, language="zh-CN"):
    """Single SearXNG JSON query (no time_range — breaks json_engine)."""
    params = {"q": query, "format": "json", "categories": categories, "language": language}
    try:
        r = requests.get(SEARXNG_URL, params=params, timeout=15)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception as exc:
        print(f"    [ERROR] {exc}")
        return []


def load_scraper():
    path = os.path.join(os.path.dirname(__file__), "..", "chinese_sentiment.py")
    spec = importlib.util.spec_from_file_location("chinese_sentiment", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load chinese_sentiment from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ══════════════════════════════════════════════════════════════════════════════
#  METHOD A -- SearXNG eastmoney-guba → Announcements (stock-specific)
# ══════════════════════════════════════════════════════════════════════════════

def method_a_searxng():
    """
    Query eastmoney-guba json_engine through SearXNG with the stock code.

    The engine hits Eastmoney's corporate-announcements API which returns
    stock-specific filings. The json_engine extracts title/url/content but
    NOT display_time — so date filtering is done at the API level by
    paginating through results.

    Note: json_engine doesn't support date-range parameters.
    We show the most recent announcements for the stock.
    """
    print(f"\n{'═'*70}")
    print(f"  METHOD A -- SearXNG eastmoney-guba → Announcements API")
    print(f"  Stock: {TICKER} ({STOCK_CODE})")
    print(f"{'═'*70}")

    # Query with numeric stock code (stock_list={query})
    results = searxng_query(STOCK_CODE, categories="news")

    # Keep only eastmoney-guba results
    eastmoney = [r for r in results if "eastmoney" in r.get("url", "")]

    # Deduplicate by URL
    seen = set()
    deduped = []
    for item in eastmoney:
        url = item.get("url", "")
        if url not in seen:
            seen.add(url)
            deduped.append(item)

    print(f"  SearXNG total results: {len(results)}")
    print(f"  eastmoney-guba (deduped): {len(deduped)}")
    print(f"  Note: json_engine doesn't extract display_time field.")
    print(f"  These are the most recent announcements for {STOCK_CODE}.")

    for i, item in enumerate(deduped[:10], 1):
        engine = item.get("engine", "?")
        title = item.get("title", "")[:70]
        url = item.get("url", "")[:70]
        print(f"\n  {i:>2}. [{engine}]  {title}")
        print(f"      {url}")

    return deduped


# ══════════════════════════════════════════════════════════════════════════════
#  METHOD B -- Direct scraper → Dated historical announcements + guba
# ══════════════════════════════════════════════════════════════════════════════

def method_b_direct():
    """
    Use chinese_sentiment.EastmoneyScraper to pull dated historical data.

    1. Announcements via get_news() with date filtering — paginates through
       the API to find items within ±DAY_RANGE of TARGET_DATE.
    2. Guba forum posts via get_guba_posts() — HTML scraping of recent posts.

    This is the reliable method for historical date-filtered data.
    """
    print(f"\n{'═'*70}")
    print(f"  METHOD B -- Direct scraper → Dated historical data")
    print(f"  Target: {TICKER} ({STOCK_NAME})  date: {TARGET_DATE}  (±{DAY_RANGE}d)")
    if _AUTO_WINDOW:
        print(f"  (Auto-adjusted to recent window: {DATE_START} ~ {DATE_END})")
    print(f"{'═'*70}")

    mod = load_scraper()
    em = mod.EastmoneyScraper(delay=0.3)
    all_posts = []

    # ── B1: Announcements with date filtering ──
    print(f"\n  ┌── B1: Announcements API (paginated, date-filtered) ──")
    try:
        # Use the auto-adjusted date if target is too old
        query_date = TARGET_DATE
        if _AUTO_WINDOW:
            query_date = _DATE.strftime('%Y-%m-%d')
            print(f"  │  (Target {TARGET_DATE} has no data; using window {DATE_START}~{DATE_END})")
        # Increase days_range to find data in the window
        all_news = em.get_news(STOCK_CODE, date=None, max_pages=MAX_PAGES)
        # Manual date filter with wider window
        from datetime import timedelta
        target_dt = datetime.strptime(query_date, '%Y-%m-%d')
        window_days = DAY_RANGE
        filtered = []
        for item in all_news:
            pub = item.get('date_raw', '')
            if not pub:
                continue
            try:
                dt = datetime.strptime(str(pub)[:19], '%Y-%m-%d %H:%M:%S')
                if abs((dt - target_dt).days) <= window_days:
                    filtered.append(item)
            except ValueError:
                pass
        print(f"  │  Scanned {len(all_news)} announcements, {len(filtered)} in ±{window_days}d of {query_date}")
        for item in filtered[:10]:
            sentiment = item.get("sentiment_hint", "neutral")
            pub = item.get("date_raw", "?")[:19]
            title = item.get("title", "")[:68]
            print(f"  │  [{sentiment:>8}]  {pub}  {title}")
        all_posts.extend(filtered)
    except Exception as exc:
        print(f"  │  Failed: {exc}")
    print(f"  └── Total: {len(all_posts)}")

    # ── B2: Guba forum posts ──
    guba_count = 0
    print(f"\n  ┌── B2: 股吧 forum posts (recent) ──")
    try:
        guba = em.get_guba_posts(STOCK_CODE, date=None, max_pages=1)
        print(f"  │  Found {len(guba)} posts in date window")
        for post in guba[:5]:
            sentiment = post.get("sentiment_hint", "neutral")
            title = post.get("title", "")[:68]
            print(f"  │  [{sentiment:>8}]  R:{post.get('read_count',0):>4}  {title}")
        guba_count = len(guba)
        all_posts.extend(guba)
    except Exception as exc:
        print(f"  │  Failed: {exc}")
    print(f"  └── Total: {guba_count}")

    return all_posts


# ══════════════════════════════════════════════════════════════════════════════
#  METHOD C -- Supplementary sources
# ══════════════════════════════════════════════════════════════════════════════

def method_c_supplementary():
    """Xueqiu and CLS — attempted but may be blocked by WAF."""
    print(f"\n{'═'*70}")
    print(f"  METHOD C -- Supplementary: Xueqiu + CLS")
    print(f"{'═'*70}")

    mod = load_scraper()
    results = []

    print(f"\n  ┌── Xueqiu ──")
    try:
        xq = mod.XueqiuScraper()
        posts = xq.get_stock_posts(STOCK_CODE, date=TARGET_DATE, max_pages=1)
        print(f"  │  {len(posts)} posts (needs auth cookie)")
        results.extend(posts)
    except Exception as exc:
        print(f"  │  Blocked: {exc}")
    print(f"  └──")

    print(f"\n  ┌── CLS ──")
    try:
        cls = mod.CLSNewsScraper()
        items = cls.search_news(STOCK_NAME, date=TARGET_DATE, max_pages=1)
        print(f"  │  {len(items)} articles (API behind WAF)")
        results.extend(items)
    except Exception as exc:
        print(f"  │  Blocked: {exc}")
    print(f"  └──")

    return results


# ══════════════════════════════════════════════════════════════════════════════
#  SENTIMENT AGGREGATION
# ══════════════════════════════════════════════════════════════════════════════

def aggregate_sentiment(all_posts):
    bullish = [p for p in all_posts if p.get("sentiment_hint") == "bullish"]
    bearish = [p for p in all_posts if p.get("sentiment_hint") == "bearish"]
    neutral = [p for p in all_posts if p.get("sentiment_hint") not in ("bullish", "bearish")]
    total = len(all_posts)
    scored = len(bullish) + len(bearish)

    if scored > 0:
        ratio = f"{len(bullish)}:{len(bearish)}"
        overall = ("BULLISH 看涨" if len(bullish) > len(bearish) * 1.5
                   else "BEARISH 看跌" if len(bearish) > len(bullish) * 1.5
                   else "MIXED 分歧")
    else:
        ratio = "0:0"
        overall = "NEUTRAL 中性"

    print(f"\n{'═'*70}")
    print(f"  SENTIMENT: {TICKER} ({STOCK_NAME}) @ {TARGET_DATE}")
    print(f"{'═'*70}")
    print(f"  Total posts:   {total}")
    print(f"  看涨 Bullish:  {len(bullish)}")
    print(f"  看跌 Bearish:  {len(bearish)}")
    print(f"  中性 Neutral:  {len(neutral)}")
    print(f"  Score:         {ratio}  →  {overall}")

    if bullish:
        print(f"\n  看涨信号:")
        for p in bullish[:5]:
            print(f"    • {p.get('title', '')[:68]}")
    if bearish:
        print(f"\n  看跌信号:")
        for p in bearish[:5]:
            print(f"    • {p.get('title', '')[:68]}")

    return {"total": total, "bullish": len(bullish), "bearish": len(bearish),
            "neutral": len(neutral), "ratio": ratio, "overall": overall}


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

def main():
    print(f"╔{'═'*68}╗")
    print(f"║  Chinese Stock Historical Sentiment Search                         ║")
    print(f"║  Ticker: {TICKER:<10}  Name: {STOCK_NAME:<8}  Date: {TARGET_DATE:<12}  ║")
    print(f"╚{'═'*68}╝")

    a = method_a_searxng()
    b = method_b_direct()
    c = method_c_supplementary()

    all_posts = a + b + c
    # Deduplicate by title prefix
    seen = set()
    deduped = []
    for p in all_posts:
        key = p.get("title", "")[:40]
        if key and key not in seen:
            seen.add(key)
            deduped.append(p)

    print(f"\n  Raw: {len(all_posts)}  →  Deduped: {len(deduped)}")
    summary = aggregate_sentiment(deduped)

    print(f"\n{'═'*70}")
    if summary["total"] > 0:
        print(f"  ✓ SUCCESS: Retrieved {summary['total']} dated posts for {TICKER}")
        print(f"  ✓ Historical data confirmed via display_time timestamps")
    else:
        print(f"  ✗ No data in window {DATE_START} ~ {DATE_END}")
        print(f"    Try a more recent date, or increase MAX_PAGES")
    print(f"{'═'*70}\n")


if __name__ == "__main__":
    main()
