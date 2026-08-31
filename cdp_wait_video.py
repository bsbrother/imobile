#!/usr/bin/env python3
"""Wait for blob iframe to finish loading, then extract video URL."""
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
        async def cdp(method, params=None):
            nonlocal msg_id
            msg_id += 1
            msg = {"id": msg_id, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == msg_id:
                    return data
        
        await cdp("Runtime.enable")
        
        # Check current state
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe[src*="blob"]');
                if (iframes.length === 0) return 'NO_BLOB';
                let blobUrl = iframes[0].src;
                
                // Check if iframe's document is ready
                try {
                    let doc = iframes[0].contentDocument;
                    if (!doc) return 'NO_DOC';
                    let videos = doc.querySelectorAll('video');
                    let results = [];
                    videos.forEach(v => {
                        results.push('video.src=' + (v.src||'').substring(0,300));
                        results.push('video.currentSrc=' + (v.currentSrc||'').substring(0,300));
                        results.push('video.duration=' + v.duration);
                    });
                    if (videos.length === 0) {
                        results.push('no video yet, doc has ' + doc.body.innerHTML.length + ' chars');
                        results.push('doc.body[:500]: ' + doc.body.innerHTML.substring(0, 500));
                    }
                    return JSON.stringify({blob: blobUrl, results: results});
                } catch(e) {
                    return JSON.stringify({blob: blobUrl, error: e.toString()});
                }
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print(val[:2000])
        
        # If no doc yet, wait and retry
        for attempt in range(10):
            data = json.loads(val) if isinstance(val, str) else {}
            if data.get('results') and any('video.src=' in r for r in data['results']):
                break
            await asyncio.sleep(2)
            resp = await cdp("Runtime.evaluate", {
                "expression": """
                (() => {
                    let iframes = document.querySelectorAll('iframe[src*="blob"]');
                    if (iframes.length === 0) return JSON.stringify({error: 'NO_BLOB'});
                    try {
                        let doc = iframes[0].contentDocument;
                        if (!doc) return JSON.stringify({error: 'NO_DOC'});
                        let videos = doc.querySelectorAll('video');
                        let results = [];
                        videos.forEach(v => {
                            results.push('video.src=' + (v.src||'').substring(0,300));
                            results.push('video.currentSrc=' + (v.currentSrc||'').substring(0,300));
                            results.push('video.duration=' + v.duration);
                            // Also check sources
                            let sources = v.querySelectorAll('source');
                            sources.forEach(s => results.push('source.src=' + (s.src||'')));
                        });
                        if (videos.length === 0) {
                            results.push('doc body length: ' + doc.body.innerHTML.length);
                        }
                        return JSON.stringify({attempt: """ + str(attempt+1) + """, results: results});
                    } catch(e) {
                        return JSON.stringify({error: e.toString()});
                    }
                })()
                """
            })
            val = resp.get("result", {}).get("result", {}).get("value", "")
            print(f"Attempt {attempt+1}: {val[:500]}")
        
        # Final check - also try from different angle
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                // Check all iframes thoroughly
                let iframes = document.querySelectorAll('iframe');
                let results = [];
                iframes.forEach((f, i) => {
                    results.push('iframe[' + i + '] src=' + (f.src||'').substring(0, 100));
                    if (f.src && f.src.startsWith('blob:')) {
                        try {
                            let doc = f.contentDocument || f.contentWindow.document;
                            if (doc) {
                                let vids = doc.querySelectorAll('video');
                                results.push('  videos: ' + vids.length);
                                vids.forEach(v => results.push('    src=' + (v.src||'')));
                            } else {
                                results.push('  no document');
                            }
                        } catch(e) {
                            results.push('  error: ' + e.toString().substring(0, 80));
                        }
                    }
                });
                return results.join('\\n');
            })()
            """
        })
        val2 = resp.get("result", {}).get("result", {}).get("value", "")
        print("\n=== FINAL STATE ===")
        print(val2 or "NONE")

asyncio.run(main())