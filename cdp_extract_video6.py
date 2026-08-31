#!/usr/bin/env python3
"""Get all network requests from tab and save cookies for yt-dlp."""
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
        
        # Enable network to get all requests
        await cdp("Network.enable")
        
        # Now get all cookies
        resp = await cdp("Network.getAllCookies")
        cookies = resp.get("result", {}).get("cookies", [])
        
        # Save cookies in Netscape format for curl/yt-dlp
        lines = ["# Netscape HTTP Cookie File"]
        for c in cookies:
            domain = c.get("domain", "")
            if domain.startswith("."):
                domain = domain[1:]
            flag = "TRUE" if domain.startswith(".") else "FALSE"
            path = c.get("path", "/")
            secure = "TRUE" if c.get("secure") else "FALSE"
            expires = str(int(c.get("expires", 0)))
            name = c.get("name", "")
            value = c.get("value", "")
            lines.append(f"{domain}\t{flag}\t{path}\t{secure}\t{expires}\t{name}\t{value}")
        
        with open("/home/kasm-user/Downloads/jav/javhdporn_cookies.txt", "w") as f:
            f.write("\n".join(lines))
        
        print(f"Saved {len(cookies)} cookies")
        
        # Now get network requests that contain mp4/m3u8
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                // Get performance resources
                let entries = performance.getEntriesByType('resource');
                let urls = [];
                entries.forEach(e => {
                    let url = e.name;
                    if (url.includes('.mp4') || url.includes('.m3u8')) {
                        urls.push(url);
                    }
                });
                return urls.join('\\n') || 'NO_MP4_RESOURCES';
            })()
            """
        })
        val = resp.get("result", {}).get("result", {}).get("value", "")
        print("PERF_RESOURCES:")
        print(val)
        
        # Also try to get the blob iframe's content by navigating to it via XHR
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe');
                let blobUrl = '';
                iframes.forEach(f => {
                    if (f.src.startsWith('blob:')) {
                        blobUrl = f.src;
                    }
                });
                if (!blobUrl) return 'NO_BLOB';
                
                // Try XMLHttpRequest to fetch the blob
                return new Promise((resolve) => {
                    let xhr = new XMLHttpRequest();
                    xhr.open('GET', blobUrl, true);
                    xhr.onload = function() {
                        let text = xhr.responseText.substring(0, 3000);
                        // Look for mp4/m3u8 in the response
                        let m = text.match(/https?:\\/\\/[^\"'\\s<]+(?:mp4|m3u8)[^\"'\\s<]*/gi);
                        if (m) resolve('XHR_BLOB_VIDEO: ' + m.slice(0,5).join(' | '));
                        resolve('XHR_BLOB_HTML: ' + text);
                    };
                    xhr.onerror = function() {
                        resolve('XHR_ERROR');
                    };
                    xhr.send();
                });
            })()
            """
        })
        val2 = resp.get("result", {}).get("result", {}).get("value", "")
        print("---")
        print(val2)

asyncio.run(main())
