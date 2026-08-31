#!/usr/bin/env python3
"""Extract live cf_clearance + user-agent, try curl download."""
import json, asyncio, websockets, urllib.request

tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
tab_id = None
for t in tabs:
    if "mkmp-519" in t.get("url", ""):
        tab_id = t["id"]
        break

async def main():
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{tab_id}", max_size=2**24) as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    return data
        
        # Get cookies specifically for javhdporn.net
        resp = await cdp("Network.getCookies", {
            "urls": [
                "https://www.javhdporn.net",
                "https://javhdporn.net",
                "https://video.pornfhd.com",
                "https://video.javhdporn.net",
            ]
        })
        cookies = resp.get("result", {}).get("cookies", [])
        
        # Build Netscape cookie file
        lines = ["# Netscape HTTP Cookie File"]
        for c in cookies:
            domain = c.get("domain", "")
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = str(int(c.get("expires", 0))) if c.get("expires", -1) >= 0 else "0"
            name = c.get("name", "")
            value = c.get("value", "")
            if name and value:
                lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
        
        cookie_file = "/home/kasm-user/Downloads/jav/javhdporn_live_cookies.txt"
        with open(cookie_file, "w") as f:
            f.write("\n".join(lines))
        
        # Find cf_clearance
        for c in cookies:
            if c['name'] == 'cf_clearance':
                print(f"LIVE cf_clearance: {c['value'][:80]}...")
                break
        
        # Get user agent
        resp = await cdp("Runtime.evaluate", {
            "expression": "navigator.userAgent"
        })
        ua = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"UA: {ua}")
        
        # Get video URL from blob iframe
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe[src*="blob"]');
                if (iframes.length === 0) return JSON.stringify({error: 'no blob iframe'});
                try {
                    let doc = iframes[0].contentDocument;
                    if (!doc) return JSON.stringify({error: 'no doc'});
                    let videos = doc.querySelectorAll('video');
                    if (videos.length === 0) return JSON.stringify({error: 'no video'});
                    return JSON.stringify({
                        src: videos[0].src || '',
                        currentSrc: videos[0].currentSrc || '',
                    });
                } catch(e) {
                    return JSON.stringify({error: e.toString()});
                }
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"Video info: {val}")
        
        print(f"\nCookies saved to {cookie_file}")

asyncio.run(main())