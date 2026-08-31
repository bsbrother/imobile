#!/usr/bin/env python3
"""Find all targets and attach to blob iframe to get video URL."""
import json, asyncio, websockets

TAB_ID = "6C77CC1FD2C46020A86A1CE3AE006C5A"
CDP_WS = "ws://127.0.0.1:9222/devtools/page/" + TAB_ID

async def main():
    async with websockets.connect(CDP_WS, max_size=2**24) as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    return data
        
        # Get all targets from browser
        # First disconnect from page and connect to browser
        await ws.close()
        
    # Connect to the browser-level WebSocket instead
    async with websockets.connect("ws://127.0.0.1:9222/devtools/browser/8ded4330-3ae9-4614-b231-32e4ae21768b", max_size=2**24) as ws:
        # Actually the browser WS endpoint might be different. Let me get it from /json/version
        pass
    
    # Alternative: Get all targets from the page itself
    async with websockets.connect(CDP_WS, max_size=2**24) as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    return data
        
        # Try to inject a script that navigates across frames
        # First, try to use Runtime.evaluate with a frame-specific context
        # List all execution contexts
        resp = await cdp("Runtime.executionContexts")
        contexts = resp.get("result", [])
        print(f"Found {len(contexts)} execution contexts")
        for ctx in contexts:
            cid = ctx.get("id")
            name = ctx.get("name", "")
            origin = ctx.get("origin", "")
            frame_id = ctx.get("frameId", "")
            if 'blob' in origin or 'javhdporn' in origin or name:
                print(f"  ctx {cid}: origin={origin}, name={name}, frame={frame_id}")
        
        # Try to find video in each blob context
        # Actually, for blob: URLs, the execution context might not be listed separately
        # Let's try another approach - look at the window's frames collection
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let results = [];
                // Access iframe via contentWindow
                let iframes = document.querySelectorAll('iframe');
                iframes.forEach((f, i) => {
                    results.push('iframe[' + i + '] src=' + f.src.substring(0,100));
                    try {
                        let w = f.contentWindow;
                        if (w) {
                            results.push('  contentWindow OK');
                            try {
                                let v = w.document.querySelector('video');
                                if (v) {
                                    results.push('  video src: ' + (v.src || '').substring(0,200));
                                    results.push('  video currentSrc: ' + (v.currentSrc || '').substring(0,200));
                                } else {
                                    results.push('  no video in iframe document');
                                    // Check all elements with src
                                    let mediaEls = w.document.querySelectorAll('[src]');
                                    mediaEls.forEach(el => {
                                        let s = el.src || '';
                                        if (s.includes('mp4') || s.includes('m3u8') || s.includes('blob')) {
                                            results.push('  ' + el.tagName + ' src=' + s.substring(0,200));
                                        }
                                    });
                                }
                            } catch(e) {
                                results.push('  doc access error: ' + e.message);
                            }
                        }
                    } catch(e) {
                        results.push('  contentWindow error: ' + e.message);
                    }
                });
                return results.join('\\n');
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print("---")
        print(val)

asyncio.run(main())