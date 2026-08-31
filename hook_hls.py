#!/usr/bin/env python3
"""Hook HLS.js before it loads and capture m3u8 URLs."""
import json, asyncio, websockets, urllib.request

async def hook_and_capture(tab_id, label):
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{tab_id}", max_size=2**24) as ws:
        msg_id = [0]
        async def cdp(method, params=None):
            msg_id[0] += 1
            msg = {"id": msg_id[0], "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
        
        video_urls = []
        
        # Collect messages in background
        async def collector():
            for _ in range(200):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    data = json.loads(msg)
                    # Check for script parsed events
                    method = data.get("method", "")
                    if method in ["Network.requestWillBeSent", "Network.responseReceived"]:
                        params = data.get("params", {})
                        url = params.get("request", {}).get("url", "") or params.get("response", {}).get("url", "")
                        if any(x in url for x in [".m3u8", "/master", "/index", "/hls/", "stream", "manifest"]):
                            video_urls.append(url)
                            print(f"  [{label}] {method}: {url[:150]}")
                except asyncio.TimeoutError:
                    if video_urls:
                        break
                except Exception:
                    break
        
        # Enable debugger to pause execution before scripts run
        await cdp("Network.enable")
        await cdp("Page.enable")
        await cdp("Runtime.enable")
        
        # Inject hook BEFORE page reload
        await cdp("Page.addScriptToEvaluateOnNewDocument", {
            "source": """
            // Hook URL.createObjectURL to capture blob URLs
            window._capturedM3u8 = [];
            let origCreateObjectURL = URL.createObjectURL;
            URL.createObjectURL = function(blob) {
                let url = origCreateObjectURL.call(this, blob);
                // Try to read m3u8 blobs
                if (blob.type.includes('mpegurl') || blob.type.includes('x-mpegurl') || blob.type.includes('text')) {
                    let reader = new FileReader();
                    reader.onload = function() {
                        let text = reader.result;
                        let m3u8 = text.match(/https?:\\/\\/[^\\s]+m3u8[^\\s]*/gi) || [];
                        if (m3u8.length > 0) {
                            window._capturedM3u8.push(...m3u8);
                            console.log('[HOOK] m3u8 from blob:', m3u8);
                        }
                    };
                    reader.readAsText(blob.slice(0, 5000));
                }
                return url;
            };
            
            // Hook fetch to capture m3u8 requests
            let origFetch = window.fetch;
            window.fetch = function(url, opts) {
                let urlStr = typeof url === 'string' ? url : (url.url || url.href || '');
                if (urlStr.includes('m3u8')) {
                    window._capturedM3u8.push(urlStr);
                    console.log('[HOOK] fetch m3u8:', urlStr);
                }
                return origFetch.call(this, url, opts);
            };
            
            // Hook Hls constructor
            let origDefineProperty = Object.defineProperty;
            // We'll hook after Hls is defined
            let checkInterval = setInterval(function() {
                if (typeof Hls !== 'undefined' && !Hls._hooked) {
                    let origLoadSource = Hls.prototype.loadSource;
                    Hls.prototype.loadSource = function(url) {
                        window._capturedM3u8.push(url);
                        console.log('[HOOK] Hls.loadSource:', url);
                        return origLoadSource.call(this, url);
                    };
                    Hls._hooked = true;
                }
            }, 100);
            
            console.log('[HOOK] hooks installed');
            """
        })
        
        # Start collector
        collector_task = asyncio.create_task(collector())
        
        # Reload page
        await cdp("Page.reload")
        
        # Wait for collector
        try:
            await asyncio.wait_for(collector_task, timeout=20)
        except asyncio.TimeoutError:
            pass
        
        # Check captured m3u8
        resp = await cdp("Runtime.evaluate", {
            "expression": "JSON.stringify(window._capturedM3u8 || [])",
            "maxLength": 10000
        })
        val = resp.get("result",{}).get("result",{}).get("value","")
        if val and val != '[]':
            m3u8_list = json.loads(val)
            for u in m3u8_list:
                video_urls.append(u)
        
        return list(set(video_urls))

async def main():
    targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    
    # Map javplayer codes to video names
    code_map = {
        "KG4M1Q0K": "ckck-022",
        "K1PWDMMK": "hmn-411",
        "8PDVD5M8": "same-193",
        "K1PM9Q1K": "dass-363",
        "KXDMVZJ2": "seven-003",
        "Z8MOQEO2": "s-cute-k33_waka_03",
        "GXW9L9": "mngs-064",
        "E0NJQR": "atid-685",
    }
    
    results = {}
    for t in targets:
        url = t.get("url", "")
        if "javplayer.cc/e/" in url:
            code = url.split("/e/")[-1].split("?")[0]
            name = code_map.get(code, code)
            print(f"\n=== {name} ({code}) ===")
            try:
                urls = await hook_and_capture(t["id"], name)
                results[name] = urls
                if urls:
                    print(f"  URLS: {urls}")
                else:
                    print(f"  NO URLS FOUND")
            except Exception as e:
                print(f"  ERROR: {e}")
                results[name] = []
    
    with open("/home/kasm-user/Downloads/jav/javplayer_urls_final.json", "w") as f:
        json.dump(results, f, indent=2)
    
    print(f"\n=== RESULTS ===")
    for name, urls in results.items():
        print(f"  {name}: {' | '.join(urls) if urls else 'NO URL'}")

asyncio.run(main())