#!/usr/bin/env python3
"""Capture video URL from network requests in real-time via CDP."""
import json, asyncio, websockets

TAB_ID = "6C77CC1FD2C46020A86A1CE3AE006C5A"
CDP_WS = "ws://127.0.0.1:9222/devtools/page/" + TAB_ID

async def main():
    async with websockets.connect(CDP_WS, max_size=2**24) as ws:
        msg_id = 0
        async def cdp(method, params=None):
            nonlocal msg_id
            msg_id += 1
            msg = {"id": msg_id, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
        
        # Collect events
        events = []
        async def collect():
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                method = data.get("method", "")
                if method in ["Network.requestWillBeSent", "Network.responseReceived"]:
                    events.append(data)
                    if method == "Network.requestWillBeSent":
                        req = data["params"]["request"]
                        url = req.get("url", "")
                        if any(x in url.lower() for x in [".mp4", ".m3u8", "mkmp", "video.pornfhd"]):
                            print(f"REQUEST: {url[:200]}")
                            print(f"  HEADERS: {json.dumps(dict(req.get('headers',{})), indent=2)[:300]}")
                    elif method == "Network.responseReceived":
                        resp_info = data["params"]["response"]
                        url = resp_info.get("url", "")
                        if any(x in url.lower() for x in [".mp4", ".m3u8", "mkmp"]):
                            print(f"RESPONSE: {url[:200]}")
                            print(f"  STATUS: {resp_info.get('status')}")
                            print(f"  TYPE: {resp_info.get('type')}")
                            print(f"  MIME: {resp_info.get('mimeType')}")
        
        # Enable network
        await cdp("Network.enable")
        
        # Trigger a seek in video to force a new request
        await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe');
                iframes.forEach(f => {
                    if (f.src.startsWith('blob:')) {
                        try {
                            let doc = f.contentDocument;
                            if (doc) {
                                let v = doc.querySelector('video');
                                if (v && v.duration > 10) {
                                    v.currentTime = v.duration / 2;
                                    console.log('Video seeked to:', v.currentTime);
                                }
                            }
                        } catch(e) {}
                    }
                });
                return 'seek triggered';
            })()
            """
        })
        
        # Wait for network events
        await asyncio.sleep(5)
        
        # Print all collected events
        print("---")
        for ev in events:
            method = ev.get("method")
            if method == "Network.requestWillBeSent":
                req = ev["params"]["request"]
                url = req.get("url", "")
                if any(x in url.lower() for x in [".mp4", ".m3u8", "mkmp", "video.pornfhd", ".ts"]):
                    print(f"REQ: {url[:200]}")
            elif method == "Network.responseReceived":
                resp_info = ev["params"]["response"]
                url = resp_info.get("url", "")
                if any(x in url.lower() for x in [".mp4", ".m3u8", "mkmp", "video.pornfhd"]):
                    print(f"RES: {url[:200]} status={resp_info.get('status')} mime={resp_info.get('mimeType')}")
        
        await cdp("Network.disable")

asyncio.run(main())