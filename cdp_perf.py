#!/usr/bin/env python3
"""Get video URL from Performance API which tracks all resources."""
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
        
        # Get ALL performance entries
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let entries = performance.getEntriesByType('resource');
                let results = [];
                entries.forEach(e => {
                    let url = e.name || '';
                    if (url.includes('.mp4') || url.includes('.m3u8') || url.includes('.ts') || 
                        url.includes('mkmp') || url.includes('video.pornfhd') || url.includes('blob')) {
                        results.push({
                            url: url.substring(0, 200),
                            type: e.initiatorType,
                            duration: Math.round(e.duration),
                            transferSize: e.transferSize
                        });
                    }
                });
                
                // Also check navigation entries
                let navEntries = performance.getEntriesByType('navigation');
                navEntries.forEach(e => {
                    // These won't have mp4 but log them anyway
                });
                
                return JSON.stringify(results, null, 2);
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print("Performance entries with media/video:")
        print(val or "NONE")
        
        # Also try: check the network directly via CDP Network domain
        await cdp("Network.enable")
        
        # Get the HAR-like data
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                // Also check for any object URLs
                let results = [];
                // If the page has used createObjectURL, those blob URLs should be here
                // Check all iframes for their current document
                let iframes = document.querySelectorAll('iframe');
                iframes.forEach((f, i) => {
                    let src = f.src || '';
                    results.push('iframe[' + i + ']: ' + src.substring(0, 120));
                    
                    // Check sandbox attribute
                    let sandbox = f.getAttribute('sandbox') || 'none';
                    results.push('  sandbox: ' + sandbox);
                    let allow = f.getAttribute('allow') || 'none';
                    results.push('  allow: ' + allow);
                    
                    // Try to access document
                    try {
                        let doc = f.contentDocument;
                        if (doc) {
                            let videos = doc.querySelectorAll('video');
                            results.push('  videos: ' + videos.length);
                            videos.forEach(v => {
                                results.push('    src: ' + (v.src || '').substring(0,150));
                                results.push('    currentSrc: ' + (v.currentSrc || '').substring(0,150));
                                let sources = v.querySelectorAll('source');
                                sources.forEach(s => results.push('    source: ' + (s.src || '')));
                            });
                            if (videos.length === 0) {
                                let allSrc = doc.querySelectorAll('[src]');
                                results.push('  elements with src: ' + allSrc.length);
                                allSrc.forEach(el => {
                                    let s = (el.src || '').substring(0, 150);
                                    if (s) results.push('    ' + el.tagName + ': ' + s);
                                });
                            }
                        } else {
                            results.push('  NO contentDocument access');
                        }
                    } catch(e) {
                        results.push('  access error: ' + e.message.substring(0, 100));
                    }
                });
                return results.join('\\n');
            })()
            """
        })
        val2 = resp.get("result", {}).get("result", {}).get("value", "")
        print("\n--- Iframe analysis ---")
        print(val2 or "NONE")
        
        # Check if there's a #document inside the iframe
        # Even if cross-origin, we might be able to access it
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe');
                let results = [];
                iframes.forEach((f, i) => {
                    if (!f.src || !f.src.startsWith('blob:')) return;
                    
                    // Check if we can see inside via contentWindow.postMessage
                    // Or check the allow attribute
                    results.push('BLOB iframe[' + i + '] attributes:');
                    for (let attr of f.attributes) {
                        results.push('  ' + attr.name + '=' + (attr.value||'').substring(0,100));
                    }
                });
                return results.join('\\n') || 'NO_BLOB_IFRAMES';
            })()
            """
        })
        val3 = resp.get("result", {}).get("result", {}).get("value", "")
        print("\n--- Blob iframe attributes ---")
        print(val3 or "NONE")

asyncio.run(main())