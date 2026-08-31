#!/usr/bin/env python3
"""Deep dive into video source - check MSE, fetch blob URL, etc."""
import json, asyncio, websockets

TAB_ID = "6C77CC1FD2C46020A86A1CE3AE006C5A"
CDP_WS = "ws://127.0.0.1:9222/devtools/page/" + TAB_ID

async def main():
    async with websockets.connect(CDP_WS, max_size=2**24) as ws:
        async def cdp(method, params=None):
            msg = {"id": 1, "method": method, "params": params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get("id") == 1:
                    return data
        
        # Check if video uses MSE
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let info = [];
                
                // Get all blob: URLs from the page
                let iframes = document.querySelectorAll('iframe[src*=\"blob\"]');
                iframes.forEach(f => info.push('BLOB IFRAME: ' + f.src));
                
                // Check video elements anywhere
                let allVideos = document.querySelectorAll('video');
                info.push('Total video elements: ' + allVideos.length);
                allVideos.forEach((v, i) => {
                    info.push('  video[' + i + ']:');
                    info.push('    src: ' + (v.src || 'none').substring(0,150));
                    info.push('    currentSrc: ' + (v.currentSrc || 'none').substring(0,150));
                    info.push('    networkState: ' + v.networkState);
                    info.push('    readyState: ' + v.readyState);
                    
                    // Check if using MSE
                    if (v.src && v.src.startsWith('blob:')) {
                        // Check MediaSource
                        try {
                            let ms = new MediaSource();
                            info.push('    MSE available');
                        } catch(e) {}
                    }
                    
                    // Check for source elements
                    let sources = v.querySelectorAll('source');
                    sources.forEach(s => info.push('    source: ' + (s.src || 'none') + ' type=' + (s.type || 'none')));
                });
                
                return info.join('\\n');
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print("PAGE_VIDEO_CHECK:")
        print(val or "NONE")
        
        # Try to fetch the blob URL content
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe[src*=\"blob\"]');
                if (iframes.length === 0) return 'NO_BLOB_IFRAMES';
                let blobUrl = iframes[0].src;
                
                // Try to fetch the blob URL directly
                return fetch(blobUrl, {mode: 'same-origin'}).then(r => {
                    if (!r.ok) return 'FETCH_STATUS: ' + r.status;
                    return r.text().then(text => {
                        let lines = text.substring(0, 2000);
                        // Find video URLs in the blob content
                        let urls = [];
                        let re = /https?:\\/\\/[^\"'\\s<]+(?:mp4|m3u8)[^\"'\\s<]*/gi;
                        let m;
                        while ((m = re.exec(text)) !== null) {
                            urls.push(m[0]);
                        }
                        if (urls.length > 0) return 'URLS: ' + urls.join(' | ');
                        return 'BLOB_CONTENT[:2000]:\\n' + lines;
                    });
                }).catch(e => 'FETCH_ERR: ' + e.toString());
            })()
            """
        })
        val2 = resp.get("result", {}).get("result", {}).get("value", "")
        print("---")
        print("BLOB_FETCH:")
        print(val2 or "NONE")
        
        # Try alternative: look for localStorage/sessionStorage config
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let keys = Object.keys(localStorage);
                let results = [];
                keys.forEach(k => {
                    let v = localStorage.getItem(k);
                    if (v && (v.includes('mp4') || v.includes('m3u8') || v.includes('video'))) {
                        results.push('LS: ' + k + ' = ' + v.substring(0, 200));
                    }
                });
                
                keys = Object.keys(sessionStorage);
                keys.forEach(k => {
                    let v = sessionStorage.getItem(k);
                    if (v && (v.includes('mp4') || v.includes('m3u8') || v.includes('video'))) {
                        results.push('SS: ' + k + ' = ' + v.substring(0, 200));
                    }
                });
                
                return results.join('\\n') || 'NO_STORAGE_MATCHES';
            })()
            """
        })
        val3 = resp.get("result", {}).get("result", {}).get("value", "")
        print("---")
        print("STORAGE:")
        print(val3 or "NONE")

asyncio.run(main())