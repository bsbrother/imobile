#!/usr/bin/env python3
"""Extract video URLs from javplayer.cc by reading localStorage + page scripts."""
import json, asyncio, websockets, urllib.request

async def get_video_url(tab_id, label):
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{tab_id}") as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    return data
        
        # 1. Check localStorage
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let result = {};
                for (let i = 0; i < localStorage.length; i++) {
                    let key = localStorage.key(i);
                    let val = localStorage.getItem(key);
                    if (val && (val.includes('m3u8') || val.includes('.mp4') || val.includes('http') || val.includes('cdn'))) {
                        result[key] = val.substring(0, 300);
                    }
                }
                return JSON.stringify(result);
            })()
            """
        })
        ls = resp.get("result", {}).get("result", {}).get("value", "")
        
        # 2. Check sessionStorage
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let result = {};
                for (let i = 0; i < sessionStorage.length; i++) {
                    let key = sessionStorage.key(i);
                    let val = sessionStorage.getItem(key);
                    if (val) result[key] = val.substring(0, 200);
                }
                return JSON.stringify(result);
            })()
            """
        })
        ss = resp.get("result", {}).get("result", {}).get("value", "")
        
        # 3. Check for m3u8 in the full page HTML
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let html = document.documentElement.outerHTML;
                let m3u8 = [];
                let re = /https?:\\/\\/[^"'\\s<>]+m3u8[^"'\\s<>]*/gi;
                let m;
                while ((m = re.exec(html)) !== null) m3u8.push(m[0]);
                return JSON.stringify(m3u8.slice(0, 10));
            })()
            """
        })
        html_m3u8 = resp.get("result", {}).get("result", {}).get("value", "")
        
        # 4. Try to read the video's current source via MSE interface
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let v = document.querySelector('video');
                if (!v) return 'NO_VIDEO';
                // If using MSE, the source might be accessible via the media source
                let info = {
                    src: (v.src||'').substring(0,80),
                    currentSrc: (v.currentSrc||'').substring(0,80),
                    duration: v.duration,
                    readyState: v.readyState,
                    networkState: v.networkState,
                    // Check for hls.js or other player globals
                    hlsExists: typeof Hls !== 'undefined',
                    // Check window for any source config
                };
                return JSON.stringify(info);
            })()
            """
        })
        video_info = resp.get("result", {}).get("result", {}).get("value", "")
        
        print(f"[{label}] LS: {ls[:200]}")
        print(f"[{label}] SS: {ss[:200]}")
        print(f"[{label}] HTML m3u8: {html_m3u8[:200]}")
        print(f"[{label}] Video: {video_info[:200]}")
        
        # Try to access the HLS.js instance if available
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                if (typeof Hls === 'undefined') return 'NO_HLS';
                let video = document.querySelector('video');
                // Try to find Hls instance attached to the video
                let players = document.querySelectorAll('video');
                let result = [];
                // Check window for player instance
                for (let key of Object.keys(window)) {
                    try {
                        let val = window[key];
                        if (val && val.levels && Array.isArray(val.levels)) {
                            result.push('HLS instance: window.' + key);
                            val.levels.forEach((l, i) => {
                                result.push('  level[' + i + ']: bitrate=' + l.bitrate + ' url=' + (l.url||'none'));
                            });
                        }
                    } catch(e) {}
                }
                return result.join('\\n') || 'NO_HLS_INSTANCE';
            })()
            """
        })
        hls = resp.get("result", {}).get("result", {}).get("value", "")
        if hls != 'NO_HLS' and hls != 'NO_HLS_INSTANCE':
            print(f"[{label}] HLS: {hls[:500]}")

async def main():
    targets = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
    for t in targets:
        url = t.get("url", "")
        if "javplayer.cc/e/" in url:
            code = url.split("/e/")[-1].split("?")[0]
            print(f"\n=== {code} ===")
            await get_video_url(t["id"], code)

asyncio.run(main())