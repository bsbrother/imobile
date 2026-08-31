#!/usr/bin/env python3
"""Extract video URL from a Chrome tab via CDP and print it."""
import json, sys, asyncio, websockets

TAB_ID = "6C77CC1FD2C46020A86A1CE3AE006C5A"
CDP_WS = "ws://127.0.0.1:9222/devtools/page/" + TAB_ID

async def main():
    async with websockets.connect(CDP_WS, max_size=2**24) as ws:
        # Evaluate JS to find video URL
        msg = {
            "id": 1,
            "method": "Runtime.evaluate",
            "params": {
                "expression": """
                (() => {
                    let results = [];
                    
                    // video element
                    let v = document.querySelector('video');
                    if (v) {
                        results.push('video_src: ' + (v.src || 'none'));
                        let ss = v.querySelectorAll('source');
                        ss.forEach(s => results.push('source: ' + (s.src || 'none')));
                    }
                    
                    // iframe player
                    let ifs = document.querySelectorAll('iframe');
                    ifs.forEach(f => results.push('iframe: ' + (f.src || 'none')));
                    
                    // page source for mp4/m3u8 URLs
                    let html = document.documentElement.innerHTML;
                    let re = /https?:\\/\\/[^"'\\s]+?(?:\\.mp4|\\.m3u8)[^"'\\s]*/gi;
                    let matches = html.match(re) || [];
                    matches.slice(0,10).forEach(u => results.push('media_url: ' + u));
                    
                    // Check script tags for player config
                    let scripts = document.querySelectorAll('script');
                    scripts.forEach(s => {
                        if (s.textContent && (s.textContent.includes('mp4') || s.textContent.includes('m3u8') || s.textContent.includes('player'))) {
                            let m = s.textContent.match(/https?:\\/\\/[^"'\\s]+?(?:\\.mp4|\\.m3u8)[^"'\\s]*/gi);
                            if (m) m.slice(0,5).forEach(u => results.push('script_media: ' + u));
                        }
                    });
                    
                    return results.join('\\n') || 'NO_RESULTS';
                })()
                """
            }
        }
        await ws.send(json.dumps(msg))
        resp = await ws.recv()
        data = json.loads(resp)
        if 'result' in data and 'result' in data['result']:
            val = data['result']['result'].get('value', '')
            print(val)
        else:
            print('ERROR:', json.dumps(data, indent=2)[:2000])

asyncio.run(main())
