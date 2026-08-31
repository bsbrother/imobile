#!/usr/bin/env python3
"""Get video URL from the page's running JavaScript."""
import json, asyncio, websockets, urllib.request

tabs = json.loads(urllib.request.urlopen("http://127.0.0.1:9222/json").read())
tab_id = None
for t in tabs:
    if "mkmp-519" in t.get("url", ""):
        tab_id = t["id"]
        print(f"Tab: {t['title'][:60]}")
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

        # Check for jwplayer or any player variable
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let results = [];
                
                // Check for video element even if inside blob
                let videos = document.querySelectorAll('video');
                results.push('videos: ' + videos.length);
                videos.forEach((v, i) => {
                    results.push('  ' + i + ': src=' + (v.src||'').substring(0,100));
                    results.push('    currentSrc=' + (v.currentSrc||'').substring(0,100));
                });
                
                // Check the blob iframe more directly
                let iframes = document.querySelectorAll('iframe');
                iframes.forEach((f, i) => {
                    if (f.src && f.src.startsWith('blob:')) {
                        results.push('blob iframe: ' + f.src);
                        // Check srcdoc
                        if (f.srcdoc) results.push('  srcdoc: ' + f.srcdoc.substring(0,200));
                    }
                });
                
                // Look in the video-player div for any data attributes with URL
                let player = document.getElementById('video-player');
                if (player) {
                    let attrs = player.attributes;
                    for (let a of attrs) {
                        results.push('player attr: ' + a.name + '=' + (a.value||'').substring(0,100));
                    }
                    // Check children
                    let resp = player.querySelector('.responsive-player');
                    if (resp) {
                        // Check any iframe already loaded
                        let vifs = resp.querySelectorAll('iframe');
                        vifs.forEach(vif => results.push('player iframe: ' + (vif.src||'none').substring(0,150)));
                    }
                }
                
                // Check all iframes on the page for their current src/content
                let allFrames = document.querySelectorAll('iframe');
                allFrames.forEach((f, i) => {
                    results.push('iframe[' + i + ']: ' + (f.src||'[no src]').substring(0,120));
                    if (f.src && f.src.startsWith('blob:')) {
                        // Try to access its contentWindow
                        try {
                            let w = f.contentWindow;
                            if (w) {
                                try {
                                    let d = w.document;
                                    let vids = d.querySelectorAll('video');
                                    vids.forEach(v => {
                                        results.push('  BLOB VIDEO: src=' + (v.src||'').substring(0,150));
                                        results.push('    currentSrc=' + (v.currentSrc||'').substring(0,150));
                                    });
                                    if (vids.length === 0) {
                                        // Check for source elements
                                        let srcEls = d.querySelectorAll('[src]');
                                        srcEls.forEach(el => {
                                            let s = el.src || '';
                                            if (s.includes('mp4') || s.includes('m3u8') || s.includes('blob') || s.includes('http')) {
                                                results.push('  BLOB el: ' + el.tagName + ' src=' + s.substring(0,150));
                                            }
                                        });
                                    }
                                } catch(e) {
                                    results.push('  cannot access doc: ' + e.message);
                                }
                            }
                        } catch(e) {
                            results.push('  cannot access window: ' + e.message);
                        }
                    }
                });
                
                return results.join('\\n');
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print(val or "NO RESULT")
        
        # Also dump all iframe src attributes directly
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let results = [];
                let iframes = document.querySelectorAll('iframe');
                iframes.forEach((f, i) => {
                    let src = f.getAttribute('src') || '';
                    results.push(i + ': ' + src.substring(0,200));
                });
                return results.join('\\n');
            })()
            """
        })
        val2 = resp.get("result", {}).get("result", {}).get("value", "")
        print("\n--- ALL IFRAME SRC ATTRS ---")
        print(val2 or "NONE")

asyncio.run(main())