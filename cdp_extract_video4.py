#!/usr/bin/env python3
"""Search inside the blob iframe for video source."""
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
        
        # First, get all frames
        resp = await cdp("Page.getFrameTree")
        tree = resp.get("result", {}).get("frameTree", {})
        
        # Find all child frames
        frames = []
        def walk(node):
            f = node.get("frame", {})
            frames.append((f.get("id"), f.get("url", "")))
            for child in node.get("childFrames", []):
                walk(child)
        walk(tree)
        
        for fid, furl in frames:
            if 'blob' in furl or 'player' in furl or 'mkmp' in furl.lower():
                print(f"FRAME: {fid} -> {furl[:150]}")
        
        # For each blob iframe, try to evaluate JS inside it
        for fid, furl in frames:
            if 'blob' in furl:
                print(f"\n--- Inside frame {fid} ---")
                try:
                    resp = await cdp("Runtime.evaluate", {
                        "expression": """
                        (() => {
                            let v = document.querySelector('video');
                            if (!v) return 'NO_VIDEO';
                            return JSON.stringify({
                                src: v.src || '',
                                currentSrc: v.currentSrc || '',
                                duration: v.duration,
                                videoWidth: v.videoWidth,
                                videoHeight: v.videoHeight
                            });
                        })()
                        """,
                        "contextId": None,
                        "frameId": fid
                    })
                    val = resp.get("result", {}).get("result", {}).get("value", "")
                    print("VIDEO:", val)
                except Exception as e:
                    print(f"Error: {e}")

asyncio.run(main())
