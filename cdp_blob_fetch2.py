#!/usr/bin/env python3
"""Fetch blob URL content from main page context (same origin)."""
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
        
        # Get the blob URL
        resp = await cdp("Runtime.evaluate", {
            "expression": """
            (() => {
                let iframes = document.querySelectorAll('iframe[src*="blob"]');
                return iframes.length > 0 ? iframes[0].src : 'NO_BLOB';
            })()
            """
        })
        blob_url = resp.get("result", {}).get("result", {}).get("value", "")
        print(f"Blob URL: {blob_url}")
        
        if blob_url and blob_url.startswith('blob:'):
            # Fetch the blob content from main page context
            resp = await cdp("Runtime.evaluate", {
                "expression": f"""
                (() => {{
                    return fetch('{blob_url}').then(r => {{
                        if (!r.ok) return 'FETCH_FAILED: ' + r.status;
                        return r.text().then(html => {{
                            // Extract video URLs
                            let mp4_matches = html.match(/https?:\\/\\/[^"'\\s<>]+(?:mp4|m3u8)[^"'\\s<>]*/gi) || [];
                            let script_matches = html.match(/(?:source|src|file|url|video)["']?\s*[:=]\s*["']([^"']+)["']/gi) || [];
                            return JSON.stringify({{
                                html_len: html.length,
                                mp4_urls: mp4_matches.slice(0, 20),
                                script_hints: script_matches.slice(0, 10),
                                html_snippet: html.substring(0, 2000)
                            }}, null, 2);
                        }});
                    }}).catch(e => 'FETCH_ERROR: ' + e.toString());
                }})()
                """,
                "awaitPromise": True
            })
            val = resp.get("result", {}).get("result", {}).get("value", "")
            print(val[:3000])
        else:
            # No blob iframe - maybe the page state changed
            # Try to force the player to load by clicking the play button
            resp = await cdp("Runtime.evaluate", {
                "expression": """
                (() => {
                    // Check what's inside the video-player div
                    let player = document.getElementById('video-player');
                    if (!player) return 'NO_PLAYER_DIV';
                    return JSON.stringify({
                        innerHTML_len: player.innerHTML.length,
                        innerHTML_snippet: player.innerHTML.substring(0, 2000),
                        iframes: player.querySelectorAll('iframe').length
                    });
                })()
                """
            })
            val2 = resp.get("result", {}).get("result", {}).get("value", "")
            print("Player div state:", val2[:2000])

asyncio.run(main())