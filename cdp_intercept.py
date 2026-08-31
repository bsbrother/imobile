#!/usr/bin/env python3
"""Intercept blob URL creation to capture video URL."""
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
        video_urls = []
        
        async def cdp(method, params=None):
            nonlocal msg_id
            msg_id += 1
            msg = {"id": msg_id, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
        
        # Enable network monitoring
        await cdp("Network.enable")
        
        # Inject a script BEFORE page load to intercept blob creation
        # Actually page is already loaded, so let's:
        # 1. Hook URL.createObjectURL to capture any new blobs
        # 2. Hook HTMLIFrameElement.prototype.src setter
        # 3. Then reload the page
        
        await cdp("Runtime.evaluate", {
            "expression": """
            // Monkey-patch to capture blob URLs
            window._capturedBlobs = [];
            window._capturedVideoUrls = [];
            
            // Hook URL.createObjectURL
            let origCreateObjectURL = URL.createObjectURL.bind(URL);
            URL.createObjectURL = function(blob) {
                let url = origCreateObjectURL(blob);
                window._capturedBlobs.push(url);
                console.log('[INTERCEPT] createObjectURL:', url);
                
                // Try to read the blob content
                let reader = new FileReader();
                reader.onload = function() {
                    let text = reader.result;
                    // Look for video URLs
                    let matches = text.match(/https?:\\/\\/[^\"'\\s<]+(?:mp4|m3u8)[^\"'\\s<]*/gi);
                    if (matches) {
                        window._capturedVideoUrls.push(...matches);
                        console.log('[INTERCEPT] Found video URLs in blob:', matches);
                    }
                };
                reader.readAsText(blob);
                
                return url;
            };
            
            // Hook iframe.src setter
            let origDescriptor = Object.getOwnPropertyDescriptor(HTMLIFrameElement.prototype, 'src');
            Object.defineProperty(HTMLIFrameElement.prototype, 'src', {
                get: origDescriptor.get,
                set: function(val) {
                    if (val.startsWith('blob:')) {
                        window._capturedBlobs.push(val);
                        console.log('[INTERCEPT] iframe.src =', val.substring(0,80));
                    }
                    origDescriptor.set.call(this, val);
                }
            });
            
            // Also hook the src attribute
            let origSetAttr = Element.prototype.setAttribute;
            Element.prototype.setAttribute = function(name, value) {
                if (name === 'src' && this.tagName === 'IFRAME' && value && value.startsWith('blob:')) {
                    window._capturedBlobs.push(value);
                    console.log('[INTERCEPT] iframe.setAttribute src =', value.substring(0,80));
                }
                return origSetAttr.call(this, name, value);
            };
            
            'hooks installed';
            """
        })
        
        # Now reload the page with hooks active
        print("Reloading page with hooks...")
        await cdp("Page.reload")
        await asyncio.sleep(8)
        
        # Check what was captured
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            JSON.stringify({
                blobs: window._capturedBlobs || [],
                videoUrls: window._capturedVideoUrls || []
            })
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        data = json.loads(val) if val else {}
        print(f"Captured {len(data.get('blobs',[]))} blobs, {len(data.get('videoUrls',[]))} video URLs")
        for u in data.get('videoUrls', []):
            print(f"  VIDEO: {u}")
        
        # Also check network requests that happened during reload
        # We need to collect these
        print("\nCollecting network events...")
        network_events = []
        
        async def collect():
            while True:
                try:
                    resp = await asyncio.wait_for(ws.recv(), timeout=3)
                except asyncio.TimeoutError:
                    break
                data = json.loads(resp)
                method = data.get("method", "")
                if method == "Network.requestWillBeSent":
                    req_url = data["params"]["request"]["url"]
                    if any(x in req_url.lower() for x in [".mp4", ".m3u8", "video.pornfhd"]):
                        network_events.append(("REQ", req_url))
                        print(f"  REQ: {req_url[:150]}")
                elif method == "Network.responseReceived":
                    resp_url = data["params"]["response"]["url"]
                    if any(x in resp_url.lower() for x in [".mp4", ".m3u8", "mkmp-519"]):
                        network_events.append(("RESP", resp_url))
                        print(f"  RESP: {resp_url[:150]}")
        
        collect_task = asyncio.create_task(collect())
        await asyncio.sleep(10)
        collect_task.cancel()

asyncio.run(main())