#!/usr/bin/env python3
"""Find video by clicking play and capturing requests."""
import json, asyncio, websockets, urllib.request

# Get tabs
tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
main_tab_id = None
for t in tabs:
    if "mkmp-519" in t.get("url", ""):
        main_tab_id = t["id"]
        print(f"Found tab: {t['title'][:60]}")
        break

if not main_tab_id:
    print("Tab not found")
    exit(1)

async def main():
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{main_tab_id}", max_size=2**24) as ws:
        msg_id = 0
        async def cdp(method, params=None):
            nonlocal msg_id
            msg_id += 1
            msg = {"id": msg_id, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
        
        # Enable network
        await cdp("Network.enable")
        
        # Find and click the video player
        # First get the position of the iframe
        await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe');
                for (let f of iframes) {
                    if (f.src && f.src.startsWith('blob:')) {
                        let rect = f.getBoundingClientRect();
                        console.log('Blob iframe bounds:', JSON.stringify({top: rect.top, left: rect.left, width: rect.width, height: rect.height}));
                        return JSON.stringify(rect);
                    }
                }
                return 'NO_BLOB_IFRAME';
            })()
            """
        })
        
        # Listen for network requests
        video_url = None
        async def listen():
            nonlocal video_url
            while True:
                try:
                    resp = await asyncio.wait_for(ws.recv(), timeout=20)
                except asyncio.TimeoutError:
                    break
                data = json.loads(resp)
                method = data.get("method", "")
                
                if method == "Network.requestWillBeSent":
                    params = data["params"]
                    req_url = params["request"]["url"]
                    if any(x in req_url.lower() for x in [".mp4", ".m3u8", "video.pornfhd"]):
                        video_url = req_url
                        print(f"\n!!! VIDEO REQUEST: {req_url}")
                
                elif method == "Network.responseReceived":
                    params = data["params"]
                    resp_url = params["response"]["url"]
                    if any(x in resp_url.lower() for x in [".mp4", ".m3u8", "mkmp-519"]):
                        video_url = resp_url
                        print(f"\n!!! VIDEO RESPONSE: {resp_url}")
        
        # Start listener
        listener = asyncio.create_task(listen())
        
        # Now click the play button - use Input.dispatchMouseEvent
        # First get the iframe rect
        await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe');
                for (let f of iframes) {
                    if (f.src && f.src.startsWith('blob:')) {
                        let rect = f.getBoundingClientRect();
                        // Click in center of iframe
                        let x = rect.left + rect.width / 2;
                        let y = rect.top + rect.height / 2;
                        return JSON.stringify({x: Math.round(x), y: Math.round(y)});
                    }
                }
                return 'NO_BLOB';
            })()
            """
        })
        
        # Actually let's use Input.dispatchMouseEvent to click
        await cdp("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": 432,  # approximate center of viewport
            "y": 280,
            "button": "left",
            "clickCount": 1
        })
        await cdp("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": 432,
            "y": 280,
            "button": "left",
            "clickCount": 1
        })
        
        # Wait for requests
        await asyncio.sleep(10)
        
        listener.cancel()
        
        if video_url:
            with open("/home/kasm-user/Downloads/jav/mkmp519_video_url.txt", "w") as f:
                f.write(video_url)
            print(f"\nSaved URL: {video_url}")
        else:
            print("\nNo video URL captured")

asyncio.run(main())