#!/usr/bin/env python3
"""Step 1: Open missing tabs, then extract video URLs from all open tabs via CDP."""
import json, asyncio, websockets, urllib.request, re

# === CONFIG ===
URLS = [
    ("Supjav", "https://supjav.com/414682.html"),
    ("123AV", "https://123av.com/en/v/s-cute-k33_waka_03"),
    ("123AV", "https://123av.com/en/v/atid-685"),
    ("123AV", "https://123av.com/en/v/mngs-064"),
    ("123AV", "https://123av.com/en/v/seven-003"),
    ("123AV", "https://123av.com/en/v/ckck-022"),
    ("123AV", "https://123av.com/en/v/hmn-411-uncensored-leaked"),
    ("123AV", "https://123av.com/en/v/dass-363-uncensored-leaked"),
    ("123AV", "https://123av.com/en/v/same-193-uncensored-leaked"),
]

BASE_WS = "http://127.0.0.1:9222"

def get_tabs():
    return json.loads(urllib.request.urlopen(f"{BASE_WS}/json").read())

def find_tab_by_url(url_fragment):
    for t in get_tabs():
        if url_fragment in t.get("url", ""):
            return t
    return None

async def extract_video_url(tab_id):
    """Try to extract video URL from a tab."""
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{tab_id}", max_size=2**24) as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    return data
        
        # Get page URL first
        resp = await cdp("Runtime.evaluate", {"expression": "window.location.href"})
        page_url = resp.get("result", {}).get("result", {}).get("value", "")

        # Strategy 1: Look for video element
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let videos = document.querySelectorAll('video');
                let results = [];
                videos.forEach(v => {
                    if (v.src) results.push('video.src=' + v.src);
                    if (v.currentSrc) results.push('video.currentSrc=' + v.currentSrc);
                    v.querySelectorAll('source').forEach(s => results.push('source=' + (s.src||'')));
                });
                return results.join('\\n') || 'NO_VIDEO_ELEMENTS';
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        
        # Strategy 2: Look for iframe with javplayer.cc or media
        resp2 = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe');
                let results = [];
                iframes.forEach(f => {
                    let src = f.src || '';
                    if (src.includes('javplayer') || src.includes('player') || src.includes('video') || src.startsWith('blob:')) {
                        results.push('iframe=' + src);
                    }
                });
                return results.join('\\n') || 'NO_PLAYER_IFRAME';
            })()
            """
        })
        val2 = resp2.get("result", {}).get("result", {}).get("value", "")
        
        # Strategy 3: Scan page source for mp4/m3u8 URLs
        resp3 = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let html = document.documentElement.outerHTML;
                let matches = html.match(/https?:\\/\\/[^"'\\s<>]+(?:mp4|m3u8)[^"'\\s<>]*/gi) || [];
                return 'urls=' + JSON.stringify(matches.slice(0,10));
            })()
            """
        })
        val3 = resp3.get("result", {}).get("result", {}).get("value", "")
        
        return {"page_url": page_url, "video": val, "iframe": val2, "media_urls": val3}

async def main():
    # Find existing tabs and open missing ones
    existing = get_tabs()
    existing_urls = {t.get("url", "") for t in existing}
    
    tab_map = {}  # url_fragment -> tab_id
    
    # Map existing tabs
    for label, url in URLS:
        frag = url.split("/")[-1] or url.split("/")[-2]
        tab = find_tab_by_url(frag)
        if tab:
            tab_map[url] = tab["id"]
            print(f"  EXISTS: {label}: {url}")
        else:
            print(f"  MISSING: {label}: {url}")
    
    # Open missing tabs
    for label, url in URLS:
        if url not in tab_map:
            # Use any existing page to open new tab
            page_tab = None
            for t in existing:
                if t.get("type") == "page":
                    page_tab = t
                    break
            if page_tab:
                async with websockets.connect(page_tab["webSocketDebuggerUrl"]) as ws:
                    async def cdp(method, params=None):
                        msg = {"id": 1, "method": method, "params": params or {}}
                        await ws.send(json.dumps(msg))
                        while True:
                            resp = await ws.recv()
                            data = json.loads(resp)
                            if data.get("id") == 1:
                                return data
                    resp = await cdp("Target.createTarget", {"url": url})
                    new_id = resp.get("result", {}).get("targetId", "")
                    tab_map[url] = new_id
                    print(f"  OPENED: {label}: {url} -> {new_id}")
                await asyncio.sleep(2)
    
    print(f"\nExtracting video URLs from {len(tab_map)} tabs...")
    
    # Extract from each tab
    results = {}
    for url, tab_id in tab_map.items():
        print(f"\n--- {url} ---")
        try:
            result = await extract_video_url(tab_id)
            results[url] = result
            for k, v in result.items():
                if v and v != "NO_VIDEO_ELEMENTS" and v != "NO_PLAYER_IFRAME" and v != "urls=[]":
                    print(f"  {k}: {v[:200]}")
        except Exception as e:
            print(f"  ERROR: {e}")
            results[url] = {"error": str(e)}
    
    # Save results
    with open("/home/kasm-user/Downloads/jav/video_urls.json", "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved to video_urls.json")

asyncio.run(main())