#!/usr/bin/env python3
"""Access iframe contentDocument for blob iframe."""
import json, asyncio, websockets

TAB_ID = "6C77CC1FD2C46020A86A1CE3AE006C5A"
CDP_WS = "ws://127.0.0.1:9222/devtools/page/" + TAB_ID

async def main():
    async with websockets.connect(CDP_WS, max_size=2**24) as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            resp = await ws.recv()
            return json.loads(resp)
        
        # Access iframe contentDocument
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe');
                let results = [];
                iframes.forEach((f, i) => {
                    results.push('iframe[' + i + ']: ' + f.src.substring(0,100));
                    try {
                        let cd = f.contentDocument;
                        if (cd) {
                            results.push('  contentDoc OK');
                            let v = cd.querySelector('video');
                            if (v) {
                                results.push('  video src: ' + (v.src || '').substring(0,200));
                                results.push('  video currentSrc: ' + (v.currentSrc || '').substring(0,200));
                            } else {
                                results.push('  no video in iframe');
                            }
                            // Also check blob urls
                            let els = cd.querySelectorAll('[src]');
                            els.forEach(el => {
                                let s = el.src || '';
                                if (s.includes('mp4') || s.includes('m3u8') || s.includes('blob')) {
                                    results.push('  ' + el.tagName + ' src=' + s.substring(0,200));
                                }
                            });
                        } else {
                            results.push('  no contentDocument (cross-origin?)');
                        }
                    } catch(e) {
                        results.push('  error: ' + e.message);
                    }
                });
                return results.join('\\n') || 'NO_IFRAMES';
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print(val)
        
        if 'no contentDocument' in val or 'NO_VIDEO' in val or 'cross-origin' in val:
            # Cross-origin blob URL - can't access directly
            # Try to get the blob URL content via fetch
            resp2 = await cdp("Runtime.evaluate", {
                "expression": """
                (() => {
                    let iframes = document.querySelectorAll('iframe');
                    let blobUrl = '';
                    iframes.forEach(f => {
                        if (f.src.startsWith('blob:')) {
                            blobUrl = f.src;
                        }
                    });
                    if (!blobUrl) return 'NO_BLOB_IFRAME';
                    
                    // Try to fetch the blob URL and parse it
                    return fetch(blobUrl).then(r => r.text()).then(html => {
                        // Find video URL in the blob HTML
                        let m = html.match(/https?:\\/\\/[^\"'\\s]+(?:mp4|m3u8)[^\"'\\s]*/gi);
                        if (m && m.length > 0) return 'BLOB_VIDEO: ' + m.slice(0,3).join(' | ');
                        // Look for src or file
                        let m2 = html.match(/["'](?:src|file|url|video)["']\\s*:\\s*["']([^"']+)["']/gi);
                        if (m2) return 'BLOB_CONFIG: ' + m2.slice(0,5).join('\\n');
                        return 'BLOB_HTML[:500]: ' + html.substring(0,500);
                    }).catch(e => 'FETCH_ERROR: ' + e.toString());
                })()
                """
            })
            val2 = resp2.get("result", {}).get("result", {}).get("value", "")
            print("---")
            print(val2)
            
            # If still no luck, try Network monitoring approach
            if 'FETCH_ERROR' in val2 or 'NO_BLOB' in val2:
                print("---")
                print("Attempting Network approach...")
                # Enable network tracking
                await cdp("Network.enable")
                
                # Get all network requests for the tab
                resp3 = await cdp("Network.getRequestPostData", {})
                # Actually, let's use the performance log to find ongoing requests
                
                # List all captured requests
                resp3 = await cdp("Runtime.evaluate", {
                    "expression": """
                    (() => {
                        // Try performance API
                        let entries = performance.getEntriesByType('resource');
                        let mediaUrls = [];
                        entries.forEach(e => {
                            if (e.name.includes('mp4') || e.name.includes('m3u8')) {
                                mediaUrls.push(e.name);
                            }
                        });
                        return 'PERF_RESOURCES:\\n' + (mediaUrls.join('\\n') || 'none');
                    })()
                    """
                })
                val3 = resp3.get("result", {}).get("result", {}).get("value", "")
                print(val3)

asyncio.run(main())
