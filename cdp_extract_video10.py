#!/usr/bin/env python3
"""Get blob iframe content via Page.navigate to blob URL in new tab."""
import json, asyncio, websockets, urllib.request

# First get the browser WebSocket URL
version = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json/version").read())
browser_ws = version.get("webSocketDebuggerUrl", "")
print(f"Browser WS: {browser_ws}")

# Get the tab that has javhdporn
tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
main_tab_id = None
blob_url = ""
for t in tabs:
    url = t.get("url", "")
    if "mkmp-519" in url:
        main_tab_id = t["id"]
        # Get the blob URL from the main tab
        with urllib.request.urlopen(f"http://127.0.0.1:9222/json/{main_tab_id}") as f:
            tab_info = json.loads(f.read())
        break

async def main():
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{main_tab_id}", max_size=2**24) as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    return data
        
        # Get blob URL
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe[src*=\"blob\"]');
                return iframes.length > 0 ? iframes[0].src : '';
            })()
            """
        })
        blob_url = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"Blob URL: {blob_url}")
        
        # Now navigate to the blob URL in a new tab
        # Actually, let's just fetch it via Page.navigate
        # But navigate won't work on blob URLs. Let's use fetch and evaluate
        # Let's use Network.loadNetworkResource or just fetch via JS
        # Actually the script 8 already tried fetch and it didn't work
        # Let me try a different tactic: intercept the XHR/fetch that loads the video
        
        # Enable network interception
        await cdp("Network.enable")
        
        # Set up request interception for the blob iframe
        await cdp("Network.setRequestInterception", {
            "patterns": [{"urlPattern": "*", "interceptionStage": "HeadersReceived"}]
        })
        
        # Set up the request handler
        async def handle_events():
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                method = data.get("method", "")
                if method == "Network.requestWillBeSent":
                    req = data["params"]["request"]
                    url = req.get("url", "")
                    if any(x in url.lower() for x in [".mp4", ".m3u8"]):
                        print(f"FOUND VIDEO URL: {url}")
                        # Save it
                        with open("/home/kasm-user/Downloads/jav/mkmp519_video_url.txt", "w") as f:
                            f.write(url)
                    
                elif method == "Network.responseReceived":
                    resp_info = data["params"]["response"]
                    url = resp_info.get("url", "")
                    if any(x in url.lower() for x in [".mp4", ".m3u8"]):
                        print(f"FOUND VIDEO RESPONSE: {url}")
                
                elif method == "Network.requestIntercepted":
                    params = data["params"]
                    await cdp("Network.continueInterceptedRequest", {
                        "interceptionId": params["interceptionId"]
                    })
        
        # Start handler in background
        handler_task = asyncio.create_task(handle_events())
        
        # Reload the page to capture requests
        await cdp("Page.reload")
        
        # Wait for requests to come in
        await asyncio.sleep(15)
        
        # Cancel handler
        handler_task.cancel()
        
        # Check if we saved the URL
        try:
            with open("/home/kasm-user/Downloads/jav/mkmp519_video_url.txt") as f:
                print(f"\nSAVED URL: {f.read().strip()}")
        except FileNotFoundError:
            print("\nNo URL saved")

asyncio.run(main())