#!/usr/bin/env python3
"""Open 123av BBTU-034, click play, extract m3u8, download."""
import json, asyncio, websockets, urllib.request

URL = "https://123av.com/en/v/bbtu-034-uncensored-leaked"

async def main():
    # Find any existing page tab to open from
    tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    page_tab = None
    for t in tabs:
        if t.get("type") == "page":
            page_tab = t
            break
    
    if not page_tab:
        print("No page tab found")
        return
    
    # Open the URL in a new tab
    async with websockets.connect(page_tab["webSocketDebuggerUrl"]) as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    return data
        
        resp = await cdp("Target.createTarget", {"url": URL})
        new_id = resp.get("result", {}).get("targetId", "")
        print(f"Tab: {new_id}")
    
    await asyncio.sleep(5)
    
    # Now extract from the new tab
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{new_id}") as ws:
        msg_id = [0]
        async def cdp(method, params=None):
            msg_id[0] += 1
            msg = {"id": msg_id[0], "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
        
        video_urls = []
        
        async def collect():
            for _ in range(100):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    if video_urls:
                        break
                    continue
                data = json.loads(msg)
                method = data.get("method", "")
                params = data.get("params", {})
                if method == "Network.requestWillBeSent":
                    url = params.get("request", {}).get("url", "")
                    if any(x in url for x in [".m3u8", "video.m3u8", "master", "hls", "stream"]):
                        video_urls.append(url)
                        print(f"REQ: {url[:200]}")
        
        await cdp("Network.enable")
        await cdp("Page.enable")
        
        # Click any play button
        await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let btn = document.querySelector('[class*="play"], button, .plyr__play, .jw-play');
                if (btn) { btn.click(); return 'clicked_btn'; }
                let iframe = document.querySelector('iframe');
                if (iframe) {
                    try {
                        let doc = iframe.contentDocument;
                        if (doc) {
                            let v = doc.querySelector('video');
                            if (v) { v.play(); return 'played_video'; }
                        }
                    } catch(e) {}
                }
                // Try clicking anywhere
                document.body.click();
                return 'clicked_body';
            })()
            """
        })
        
        collector = asyncio.create_task(collect())
        
        # Wait for collector, then also check performance API
        try:
            await asyncio.wait_for(collector, timeout=15)
        except asyncio.TimeoutError:
            pass
        
        # Check performance entries
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let r = [];
                performance.getEntriesByType('resource').forEach(e => {
                    if (e.name.includes('m3u8')) r.push(e.name);
                });
                return r.join('\\n') || 'NONE';
            })()
            """
        })
        val = resp.get("result",{}).get("result",{}).get("value","")
        print(f"\n--- Performance m3u8 ---")
        print(val)
        
        unique = list(set(video_urls + val.split('\n')))
        unique = [u for u in unique if u and u != 'NONE']
        
        if unique:
            with open("/home/kasm-user/Downloads/jav/bbtu034_url.txt", "w") as f:
                f.write("\n".join(unique))
            print(f"\nSaved {len(unique)} URLs")
            for u in unique:
                print(f"  {u}")
        else:
            print("No URLs found")

asyncio.run(main())