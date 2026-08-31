#!/usr/bin/env python3
"""Deep search for video URL in javhdporn tab via CDP."""
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
        
        # Get the full page HTML
        resp = await cdp("Runtime.evaluate", {
            "expression": "document.documentElement.outerHTML",
            "maxLength": 100000
        })
        html = resp.get("result", {}).get("result", {}).get("value", "")
        
        # Find video player related content
        import re
        # Look for video player config / source
        patterns = [
            r'(?:player|source|video|file|url|src|link|data-src)[^=]*=[\'"]([^\'"]*(?:mp4|m3u8)[^\'"]*)[\'"]',
            r'https?://[^\'"\s<>]*(?:mkmp-519|MKMP-519)[^\'"\s<>]*',
            r'(?:hls|video_url|videoUrl|videoSrc|sourceUrl)[\'"]?\s*[:=]\s*[\'"]([^\'"]*mp4[^\'"]*)[\'"]',
            r'(?:hls|video_url|videoUrl|videoSrc|sourceUrl)[\'"]?\s*[:=]\s*[\'"]([^\'"]*m3u8[^\'"]*)[\'"]',
        ]
        
        all_matches = set()
        for p in patterns:
            for m in re.finditer(p, html, re.IGNORECASE):
                all_matches.add(m.group(0)[:200])
        
        if all_matches:
            for m in sorted(all_matches):
                print(m)
        else:
            # Get just the player section
            resp2 = await cdp("Runtime.evaluate", {
                "expression": """
                (() => {
                    let els = [];
                    // Player containers
                    document.querySelectorAll('#player, .player, .video-player, .video-js, .jwplayer, [id*=jwplayer], .flowplayer').forEach(el => {
                        els.push('PLAYER: ' + (el.id || el.className));
                        els.push('  innerHTML[:200]: ' + el.innerHTML.substring(0,200));
                    });
                    // Any element with data-url, data-src, data-href
                    document.querySelectorAll('[data-url], [data-src], [data-href], [data-file]').forEach(el => {
                        let data = el.getAttribute('data-url') || el.getAttribute('data-src') || el.getAttribute('data-href') || el.getAttribute('data-file');
                        if (data && (data.includes('mp4') || data.includes('m3u8'))) {
                            els.push('DATA: ' + data.substring(0,200));
                        }
                    });
                    return els.join('\\n') || 'NOTHING_FOUND';
                })()
                """
            })
            val = resp2.get("result", {}).get("result", {}).get("value", "")
            print(val)
            
            # Also check the video element's current source
            resp3 = await cdp("Runtime.evaluate", {
                "expression": """
                (() => {
                    let v = document.querySelector('video');
                    if (!v) return 'NO_VIDEO_ELEMENT';
                    let info = 'VIDEO src: ' + (v.src || 'none');
                    info += '\\nVIDEO currentSrc: ' + (v.currentSrc || 'none');
                    info += '\\nVIDEO duration: ' + (v.duration || '0');
                    info += '\\nVIDEO networkState: ' + v.networkState;
                    info += '\\nVIDEO readyState: ' + v.readyState;
                    let ss = v.querySelectorAll('source');
                    ss.forEach(s => info += '\\n  source: ' + (s.src || 'none') + ' type=' + (s.type || 'none'));
                    return info;
                })()
                """
            })
            val2 = resp3.get("result", {}).get("result", {}).get("value", "")
            print("---")
            print(val2)
        
        # Get cookies for the domain to use with yt-dlp
        resp4 = await cdp("Network.getCookies", {"urls": ["https://www.javhdporn.net"]})
        cookies = resp4.get("result", {}).get("cookies", [])
        print("---")
        for c in cookies:
            print(f"COOKIE: {c['name']}={c['value']}")

asyncio.run(main())
