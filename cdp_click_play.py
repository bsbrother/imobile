#!/usr/bin/env python3
"""Click play button then capture video URL."""
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
        
        # Enable Runtime
        await cdp("Runtime.enable")
        
        # Click the play button via JS
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                // Click the play button
                let playBtn = document.querySelector('.play-button');
                if (playBtn) {
                    playBtn.click();
                    return 'CLICKED_PLAY';
                }
                // Try responsive-player click
                let player = document.querySelector('.responsive-player');
                if (player) {
                    player.click();
                    return 'CLICKED_PLAYER';
                }
                // Try video-player div
                let vp = document.getElementById('video-player');
                if (vp) {
                    vp.click();
                    return 'CLICKED_VP';
                }
                return 'NOTHING_TO_CLICK';
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"Click result: {val}")
        
        # Wait for video player to load
        print("Waiting for player to load...")
        await asyncio.sleep(5)
        
        # Check if blob iframe appeared
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe[src*="blob"]');
                if (iframes.length > 0) return iframes[0].src;
                return 'NO_BLOB_IFRAME';
            })()
            """
        })
        blob_url = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"Blob URL after click: {blob_url}")
        
        if blob_url and blob_url.startswith('blob:'):
            # Fetch and extract
            resp = await cdp("Runtime.evaluate", {
                "expression": f"""
                (() => {{
                    return fetch('{blob_url}').then(r => r.text()).then(html => {{
                        let mp4 = html.match(/https?:\\/\\/[^"'\\s<>]+(?:mp4|m3u8)[^"'\\s<>]*/gi) || [];
                        return JSON.stringify({{urls: mp4, html: html.substring(0, 1000)}});
                    }}).catch(e => 'FETCH_ERROR: ' + e);
                }})()
                """,
                "awaitPromise": True
            })
            val2 = resp.get("result", {}).get("result", {}).get("value", "")
            print(f"Blob content: {val2[:2000]}")
            
            # Save URL if found
            try:
                data = json.loads(val2) if isinstance(val2, str) else val2
                urls = data.get('urls', [])
                if urls:
                    url = urls[0]
                    with open("/home/kasm-user/Downloads/jav/mkmp519_video_url.txt", "w") as f:
                        f.write(url)
                    print(f"\nSAVED: {url}")
            except:
                pass

asyncio.run(main())