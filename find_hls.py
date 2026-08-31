#!/usr/bin/env python3
"""Simple: find HLS m3u8 from javplayer.cc pages by inspecting Hls instance."""
import json, asyncio, websockets, urllib.request

async def find_m3u8(tab_id, label):
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{tab_id}", max_size=2**24) as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    return data
        
        # Strategy: Intercept XMLHttpRequest and check performance entries
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let results = [];
                
                // Check all performance entries
                let entries = performance.getEntriesByType('resource');
                entries.forEach(e => {
                    if (e.name && (e.name.includes('m3u8') || e.name.includes('hls') || e.name.includes('master'))) {
                        results.push('perf:' + e.name);
                    }
                });
                
                // Check if HLS.js is loaded and find its instance
                if (typeof Hls !== 'undefined') {
                    results.push('HLS.js loaded');
                    // Check all video elements for attached Hls instances
                    let videos = document.querySelectorAll('video');
                    videos.forEach((v, i) => {
                        results.push('video[' + i + '] src=' + (v.src||'').substring(0,100));
                        // Try to access Hls attached via __hls or data-hls
                        if (v.__hls && v.__hls.levels) {
                            v.__hls.levels.forEach((l, j) => {
                                results.push('  level[' + j + '] url=' + (l.url||''));
                            });
                        }
                    });
                    
                    // Check window for any Hls instances
                    for (let key of Object.keys(window)) {
                        try {
                            let val = window[key];
                            if (val && typeof val === 'object' && val.levels && Array.isArray(val.levels)) {
                                results.push('HLS on window.' + key);
                                if (val.url) results.push('  url=' + val.url);
                                val.levels.forEach((l, j) => {
                                    results.push('  level[' + j + '] url=' + (l.url||''));
                                });
                            }
                        } catch(e) {}
                    }
                }
                
                // Check MediaSource
                let videos = document.querySelectorAll('video');
                videos.forEach((v, i) => {
                    if (v.srcObject) {
                        results.push('video[' + i + '] has srcObject');
                    }
                });
                
                return results.join('\\n') || 'NOTHING';
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        
        # Also try clicking play if video not loaded yet
        resp2 = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let v = document.querySelector('video');
                if (v && v.paused) {
                    v.play().then(() => 'playing').catch(e => 'play error: ' + e);
                    return 'clicked play';
                }
                if (v) return 'already playing (' + v.readyState + ')';
                // Try clicking play button
                let btn = document.querySelector('[class*=\"play\"], button, .plyr__play');
                if (btn) { btn.click(); return 'clicked btn'; }
                return 'no video element';
            })()
            """
        })
        
        # Wait a moment and re-check
        await asyncio.sleep(3)
        
        resp3 = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let results = [];
                let entries = performance.getEntriesByType('resource');
                entries.forEach(e => {
                    if (e.name && e.name.includes('m3u8')) {
                        results.push('LATE_PERF:' + e.name);
                    }
                });
                if (typeof Hls !== 'undefined') {
                    for (let key of Object.keys(window)) {
                        try {
                            let val = window[key];
                            if (val && typeof val === 'object' && val.levels && Array.isArray(val.levels) && val.levels.length > 0) {
                                results.push('HLS:' + key);
                                val.levels.forEach((l, j) => {
                                    results.push('  level[' + j + ']: ' + (l.url||'(no url)') + ' br=' + (l.bitrate||0));
                                });
                                // Also check the top-level url
                                if (val.url) results.push('  top_url=' + val.url);
                            }
                        } catch(e) {}
                    }
                }
                return results.join('\\n') || 'STILL_NOTHING';
            })()
            """
        })
        val2 = resp3.get("result", {}).get("result", {}).get("value", "")
        
        return f"FIRST: {val[:500]}\nAFTER PLAY: {val2[:500]}"

async def main():
    targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    
    name_map = {
        "KG4M1Q0K": "ckck-022",
        "K1PWDMMK": "hmn-411",
        "8PDVD5M8": "same-193",
        "K1PM9Q1K": "dass-363",
        "KXDMVZJ2": "seven-003",
        "Z8MOQEO2": "s-cute-k33",
        "GXW9L9": "mngs-064",
        "E0NJQR": "atid-685",
    }
    
    for t in targets:
        url = t.get("url", "")
        if "javplayer.cc/e/" in url:
            code = url.split("/e/")[-1].split("?")[0] if "/e/" in url else url.split("/")[-1]
            name = name_map.get(code, code)
            print(f"\n=== {name} ({code}) ===")
            result = await find_m3u8(t["id"], name)
            print(result)

asyncio.run(main())