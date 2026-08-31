#!/usr/bin/env python3
"""Read blob content via Network.getResponseBody for blob URL."""
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
        
        # Step 1: Get the blob iframe src
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe');
                for (let f of iframes) {
                    if (f.src.startsWith('blob:')) return f.src;
                }
                return '';
            })()
            """
        })
        blob_url = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"Blob URL: {blob_url}")
        
        # Step 2: Enable Fetch domain and intercept the blob fetch
        await cdp("Fetch.enable", {"patterns": [
            {"urlPattern": "blob:*", "requestStage": "Response"}
        ]})
        
        # Step 3: Trigger the iframe content by doing a fetch from the page context
        resp = await cdp("Runtime.evaluate", {
            "awaitPromise": True,
            "expression": f"""
            (() => {{
                if (!'{blob_url}') return 'no blob';
                return fetch('{blob_url}').then(r => r.text().then(t => {{
                    // Find video URLs
                    let matches = t.match(/https?:\\/\\/[^\"'\\\\s<]+(?:mp4|m3u8)[^\"'\\\\s<]*/gi);
                    return JSON.stringify({{
                        length: t.length,
                        urls: matches || [],
                        snippet: t.substring(0, 500)
                    }});
                }})).catch(e => 'fetch error: ' + e.toString());
            }})()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"Fetch result: {val[:1000]}")

asyncio.run(main())