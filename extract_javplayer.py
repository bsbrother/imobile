#!/usr/bin/env python3
"""Fetch blob URLs from javplayer.cc iframes + extract m3u8/video URLs."""
import json, asyncio, websockets, urllib.request, os

BASE_WS = "http://127.0.0.1:9222"

def get_tabs():
    return json.loads(urllib.request.urlopen(f"{BASE_WS}/json").read())

async def fetch_blob_in_tab(tab_id, label=""):
    """From a tab that's already at javplayer.cc, fetch the blob video content."""
    async with websockets.connect(f"ws://127.0.0.1:9222/devtools/page/{tab_id}") as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    return data
        
        # First try direct extraction from page - look for m3u8 in script/data
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let results = [];
                // Check video src
                let v = document.querySelector('video');
                if (v) {
                    results.push('video.src=' + (v.src||'none'));
                    results.push('video.currentSrc=' + (v.currentSrc||'none'));
                    let sources = v.querySelectorAll('source');
                    sources.forEach(s => results.push('source=' + (s.src||'none')));
                }
                // Check localStorage
                for (let key of Object.keys(localStorage)) {
                    let val = localStorage.getItem(key);
                    if (val && (val.includes('m3u8') || val.includes('.mp4'))) {
                        results.push('LS:' + key + '=' + val.substring(0,200));
                    }
                }
                // Check any inline script with m3u8
                let scripts = document.querySelectorAll('script');
                scripts.forEach(s => {
                    if (s.textContent && s.textContent.includes('m3u8')) {
                        let m = s.textContent.match(/https?:\\/\\/[^"'\\s]+m3u8[^"'\\s]*/gi);
                        if (m) m.forEach(u => results.push('script_m3u8=' + u.substring(0,200)));
                    }
                });
                // Check page HTML
                let html = document.documentElement.outerHTML;
                let m3u8 = html.match(/https?:\\/\\/[^"'\\s]+m3u8[^"'\\s]*/gi) || [];
                m3u8.forEach(u => results.push('html_m3u8=' + u.substring(0,200)));
                
                return results.join('\\n') || 'NOTHING';
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"  [{label}] direct: {val[:500]}")
        
        # Try to get blob URL and fetch it
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let v = document.querySelector('video');
                if (!v || !v.src || !v.src.startsWith('blob:')) return 'NO_BLOB';
                return v.src;
            })()
            """
        })
        blob_url = resp.get("result", {}).get("result", {}).get("value", "")
        
        if blob_url and blob_url.startswith("blob:"):
            print(f"  [{label}] Fetching blob: {blob_url[:60]}...")
            resp = await cdp("Runtime.evaluate", {
                "expression": f"""
                (() => {{
                    return fetch('{blob_url}').then(r => {{
                        if (!r.ok) return 'FETCH_FAIL:' + r.status;
                        return r.text().then(html => {{
                            let m3u8 = html.match(/https?:\\/\\/[^"'\\s]+m3u8[^"'\\s]*/gi) || [];
                            let mp4 = html.match(/https?:\\/\\/[^"'\\s]+(?:mp4|ts)[^"'\\s]*/gi) || [];
                            return JSON.stringify({{m3u8: m3u8.slice(0,3), mp4: mp4.slice(0,3), len: html.length}});
                        }});
                    }}).catch(e => 'FETCH_ERR:' + e.toString());
                }})()
                """,
                "awaitPromise": True
            })
            val2 = resp.get("result", {}).get("result", {}).get("value", "")
            print(f"  [{label}] blob result: {val2[:500]}")
            return val2
        
        return None

async def main():
    targets = get_tabs()
    tasks = []
    
    # Find all javplayer.cc tabs
    for t in targets:
        url = t.get("url", "")
        tid = t.get("id", "")
        title = t.get("title", "")[:40]
        
        if "javplayer.cc/e/" in url:
            # Extract the video code
            code = url.split("/e/")[-1].split("?")[0] if "/e/" in url else "unknown"
            tasks.append((code, tid))
            print(f"Javplayer tab: {code} -> {title}")
    
    print(f"\nProcessing {len(tasks)} javplayer.cc tabs...\n")
    
    vid_map = {}
    for code, tid in tasks:
        result = await fetch_blob_in_tab(tid, code)
        vid_map[code] = result
    
    # Save
    with open("/home/kasm-user/Downloads/jav/javplayer_urls.json", "w") as f:
        json.dump(vid_map, f, indent=2)
    print(f"\nSaved {len(vid_map)} results")

asyncio.run(main())