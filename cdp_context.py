#!/usr/bin/env python3
"""Get video URL by executing JS in the blob iframe's execution context."""
import json, asyncio, websockets, urllib.request

tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
tab_id = None
for t in tabs:
    if "mkmp-519" in t.get("url", ""):
        tab_id = t["id"]
        break

async def main():
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{tab_id}", max_size=2**24) as ws:
        msg_id = 0
        async def cdp(method, params=None):
            nonlocal msg_id
            msg_id += 1
            msg = {"id": msg_id, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == msg_id:
                    return data
        
        # Step 1: Enable Runtime to get execution contexts
        await cdp("Runtime.enable")
        
        # Step 2: Get all execution contexts
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe[src*=\"blob\"]');
                if (iframes.length === 0) return 'NO_BLOB_IFRAME';
                let iframe = iframes[0];
                return JSON.stringify({
                    src: iframe.src,
                    id: iframe.id || '',
                    className: iframe.className || ''
                });
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"Main page: {val}")
        
        # Step 3: Use Page.createIsolatedWorld to create an execution context in the iframe
        # First get the frameId of the iframe
        resp = await cdp("Page.getFrameTree")
        tree = resp.get("result", {}).get("frameTree", {})
        
        def find_frame(node, url_pattern):
            """Recursively search frame tree for a blob frame."""
            frame = node.get("frame", {})
            furl = frame.get("url", "")
            fid = frame.get("id", "")
            if url_pattern in furl:
                return fid
            for child in node.get("childFrames", []):
                result = find_frame(child, url_pattern)
                if result:
                    return result
            return None
        
        blob_frame_id = find_frame(tree, "blob")
        print(f"Blob frame ID: {blob_frame_id}")
        
        if blob_frame_id:
            # Try to evaluate JS in the blob frame's context
            resp = await cdp("Runtime.evaluate", {
                "expression": """
                (() => {
                    let video = document.querySelector('video');
                    if (!video) return 'NO_VIDEO_IN_BLOB';
                    return JSON.stringify({
                        src: video.src || '',
                        currentSrc: video.currentSrc || '',
                        duration: video.duration,
                        width: video.videoWidth,
                        height: video.videoHeight
                    });
                })()
                """,
                "contextId": None  # don't specify - let CDP figure it out
            })
            
            # The above won't work. Let me try with the frame's execution context
            # Get execution contexts
            resp = await cdp("Runtime.evaluate", {
                "expression": "1"  # dummy - just to get contexts list
            })
            
            # Wait for execution contexts to be reported
            contexts = []
            async def collect_contexts():
                nonlocal contexts
                while len(contexts) < 5:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=3)
                    except asyncio.TimeoutError:
                        break
                    data = json.loads(msg)
                    if data.get("method") == "Runtime.executionContextCreated":
                        ctx = data["params"]["context"]
                        contexts.append(ctx)
                        aux = ctx.get("auxData", {})
                        frame = aux.get("frameId", "")
                        origin = ctx.get("origin", "")
                        if "blob" in origin or "blob" in frame:
                            print(f"  BLOB CTX: id={ctx['id']} origin={origin} frame={frame}")
            
            collect_task = asyncio.create_task(collect_contexts())
            await asyncio.sleep(2)
            collect_task.cancel()
            
            # Try to find old contexts
            for ctx in contexts:
                origin = ctx.get("origin", "")
                if "blob" in origin:
                    print(f"Found blob context: {ctx['id']}")
                    # Now evaluate in this context
                    resp = await cdp("Runtime.evaluate", {
                        "expression": """
                        (() => {
                            let video = document.querySelector('video');
                            if (!video) return JSON.stringify({error: 'no video element'});
                            return JSON.stringify({
                                src: video.src || '',
                                currentSrc: video.currentSrc || '',
                                duration: video.duration
                            });
                        })()
                        """,
                        "contextId": ctx["id"]
                    })
                    val = resp.get("result", {}).get("result", {}).get("value", "")
                    print(f"Blob frame video: {val}")
                    
                    # If video has no src, check all elements with src
                    resp = await cdp("Runtime.evaluate", {
                        "expression": """
                        (() => {
                            let results = [];
                            document.querySelectorAll('[src]').forEach(el => {
                                let s = el.src || '';
                                if (s && s.length > 5) {
                                    results.push(el.tagName + ':' + s.substring(0,200));
                                }
                            });
                            return JSON.stringify(results);
                        })()
                        """,
                        "contextId": ctx["id"]
                    })
                    val2 = resp.get("result", {}).get("result", {}).get("value", "")
                    print(f"Blob frame elements with src: {val2[:500]}")

asyncio.run(main())