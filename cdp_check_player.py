#!/usr/bin/env python3
"""Extract video URL from page scripts via CDP."""
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

        # Check for JW Player / video.js / other player objects on window
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let info = [];
                // Check global player objects
                if (window.jwplayer) info.push('jwplayer exists');
                if (window.videojs) info.push('videojs exists');
                if (window.flowplayer) info.push('flowplayer exists');
                if (window.MediaElementPlayer) info.push('MediaElementPlayer exists');
                if (window.player) info.push('window.player exists: ' + typeof window.player);
                
                // Check for any global variable with video URL
                let keys = Object.getOwnPropertyNames(window);
                keys.forEach(k => {
                    try {
                        let v = window[k];
                        if (typeof v === 'string' && (v.includes('.mp4') || v.includes('.m3u8'))) {
                            info.push('window.' + k + ' = ' + v.substring(0,200));
                        }
                    } catch(e) {}
                });
                
                return info.join('\\n') || 'NOTHING';
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print(val)
        
        # Check the blob URL content by examining performance entries more thoroughly
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let entries = performance.getEntries();
                let results = [];
                entries.forEach(e => {
                    if (e.name && (e.name.includes('mkmp') || e.name.includes('pornfhd') || e.name.includes('.mp4'))) {
                        results.push(e.name.substring(0,200));
                    }
                });
                return results.join('\\n') || 'NO_PERF_ENTRIES';
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print("---PERF---")
        print(val)

asyncio.run(main())