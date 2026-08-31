#!/usr/bin/env python3
"""Reload javplayer.cc tabs, capture network requests for m3u8."""
import json, asyncio, websockets, urllib.request

async def reload_and_capture(tab_id, label):
    video_urls = []
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{tab_id}", max_size=2**24) as ws:
        msg_id = [0]
        async def cdp(method, params=None):
            msg_id[0] += 1
            msg = {"id": msg_id[0], "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            # Don't wait for response while collecting events
        
        await cdp("Network.enable")
        await cdp("Page.enable")
        
        # Now reload
        await cdp("Page.reload")
        
        # Collect events
        async def collect():
            for _ in range(100):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=0.5)
                    data = json.loads(msg)
                    method = data.get("method", "")
                    params = data.get("params", {})
                    if method == "Network.requestWillBeSent":
                        url = params.get("request", {}).get("url", "")
                        if any(x in url for x in [".m3u8", ".mp4", ".ts", "/hls/", "/video/"]):
                            video_urls.append(url)
                            print(f"  REQ: {url[:150]}")
                    elif method == "Network.responseReceived":
                        url = params.get("response", {}).get("url", "")
                        mime = params.get("response", {}).get("mimeType", "")
                        if "video" in mime or "mpegurl" in mime or ".m3u8" in url:
                            video_urls.append(url)
                            print(f"  RESP: {url[:150]} mime={mime}")
                except asyncio.TimeoutError:
                    if video_urls:
                        break
                except Exception:
                    break
        
        await asyncio.wait_for(collect(), timeout=15)
        
    return video_urls

async def main():
    targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    tasks = []
    for t in targets:
        url = t.get("url", "")
        if "javplayer.cc/e/" in url:
            code = url.split("/e/")[-1].split("?")[0]
            tasks.append((code, t["id"]))
    
    all_urls = {}
    for code, tid in tasks:
        print(f"\n=== {code} ===")
        urls = await reload_and_capture(tid, code)
        all_urls[code] = urls
        if urls:
            print(f"  GOT: {urls[0]}")
    
    with open("/home/kasm-user/Downloads/jav/javplayer_final.json", "w") as f:
        json.dump(all_urls, f, indent=2)
    print(f"\nSaved")

asyncio.run(main())