#!/usr/bin/env python3
"""Get video source from live playing video element and save cookies."""
import json, asyncio, websockets

TAB_ID = "6C77CC1FD2C46020A86A1CE3AE006C5A"
CDP_WS = "ws://127.0.0.1:9222/devtools/page/" + TAB_ID

async def main():
    async with websockets.connect(CDP_WS, max_size=2**24) as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            resp = await ws.recv()
            return json.loads(resp)
        
        # Get video element source and cookies
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let v = document.querySelector('video');
                if (!v) return 'NO_VIDEO_ELEMENT';
                let info = {
                    src: v.src || '',
                    currentSrc: v.currentSrc || '',
                    duration: v.duration || 0,
                    networkState: v.networkState,
                    readyState: v.readyState,
                    error: v.error ? v.error.message : null,
                    videoWidth: v.videoWidth,
                    videoHeight: v.videoHeight
                };
                return JSON.stringify(info);
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print("VIDEO_INFO:", val)
        
        # Also check for any blob: URLs or other media elements
        resp2 = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let urls = [];
                // All elements with src pointing to media
                document.querySelectorAll('[src*=\"mp4\"], [src*=\"m3u8\"], [src*=\"blob:\"]').forEach(el => {
                    urls.push(el.tagName + ' src=' + (el.src || '').substring(0,150));
                });
                // Check for object/embed
                document.querySelectorAll('object, embed').forEach(el => {
                    urls.push(el.tagName + ' data=' + (el.data || el.src || '').substring(0,150));
                });
                return urls.join('\\n') || 'NONE';
            })()
            """
        })
        val2 = resp2.get("result", {}).get("result", {}).get("value", "")
        print("MEDIA_ELEMENTS:", val2)
        
        # Save cookies to netscape format for yt-dlp
        resp3 = await cdp("Network.getCookies", {"urls": ["https://www.javhdporn.net", "https://video.pornfhd.com"]})
        cookies = resp3.get("result", {}).get("cookies", [])
        print("---")
        print("COOKIES:")
        for c in cookies:
            print(f"  {c['name']}={c['value'][:50]}...")

asyncio.run(main())
