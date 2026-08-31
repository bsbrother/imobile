#!/usr/bin/env python3
"""Reload javhdporn page, click play, extract video."""
import json, asyncio, websockets, urllib.request

tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json').read())
tab_id = None
for t in tabs:
    if 'mkmp-519' in t.get('url', ''):
        tab_id = t['id']
        break

async def main():
    async with websockets.connect(f'ws://127.0.0.1:9222/devtools/page/{tab_id}') as ws:
        async def cdp(method, params=None):
            msg = {'id': 1, 'method': method, 'params': params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get('id') == 1:
                    return data
        
        # Reload page
        await cdp('Page.reload')
        await asyncio.sleep(6)
        
        # Check state
        resp = await cdp('Runtime.evaluate', {
            'expression': """
            (() => {
                let iframes = document.querySelectorAll('iframe[src*="blob"]');
                if (iframes.length > 0) return 'BLOB:' + iframes[0].src;
                let playBtn = document.querySelector('.play-button');
                if (playBtn) {
                    playBtn.click();
                    return 'CLICKED_PLAY';
                }
                let watchBtn = document.querySelector('[class*="watch"], [id*="watch"]');
                if (watchBtn) { watchBtn.click(); return 'CLICKED_WATCH'; }
                let resp = document.querySelector('.responsive-player');
                if (resp) { resp.click(); return 'CLICKED_RESPONSIVE'; }
                return 'NOTHING_CLICKABLE';
            })()
            """
        })
        val = resp.get('result', {}).get('result', {}).get('value', '')
        print(f'State: {val}')
        
        # Wait for blob iframe
        blob_url = ''
        for i in range(8):
            await asyncio.sleep(2)
            resp = await cdp('Runtime.evaluate', {
                'expression': """
                (() => {
                    let iframes = document.querySelectorAll('iframe[src*="blob"]');
                    return iframes.length > 0 ? iframes[0].src : '';
                })()
                """
            })
            blob_url = resp.get('result', {}).get('result', {}).get('value', '')
            if blob_url:
                print(f'Blob appeared: {blob_url}')
                break
            print(f'  wait {i+1}...')
        
        if not blob_url:
            print('No blob iframe appeared')
            return
        
        # Wait more for content to load
        await asyncio.sleep(5)
        
        # Try to get video URL
        for method in ['contentDocument', 'fetch']:
            if method == 'contentDocument':
                resp = await cdp('Runtime.evaluate', {
                    'expression': """
                    (() => {
                        let iframes = document.querySelectorAll('iframe[src*="blob"]');
                        if (iframes.length === 0) return 'NO_IFRAME';
                        try {
                            let doc = iframes[0].contentDocument;
                            if (!doc) return 'NO_DOCUMENT';
                            let videos = doc.querySelectorAll('video');
                            if (videos.length > 0) {
                                let v = videos[0];
                                return JSON.stringify({
                                    src: v.src || '',
                                    currentSrc: v.currentSrc || '',
                                    duration: v.duration,
                                    ready: v.readyState
                                });
                            }
                            return 'NO_VIDEO in doc, body=' + (doc.body ? doc.body.innerHTML.length : 0);
                        } catch(e) {
                            return 'CROSS_ORIGIN: ' + e.toString().substring(0, 100);
                        }
                    })()
                    """
                })
            else:
                resp = await cdp('Runtime.evaluate', {
                    'expression': f"""
                    (() => {{
                        return fetch('{blob_url}').then(r => r.text()).then(html => {{
                            let mp4 = [];
                            let re = /https?:\\/\\/[^"'\\s<>]+(?:mp4|m3u8)[^"'\\s<>]*/gi;
                            let m;
                            while ((m = re.exec(html)) !== null) mp4.push(m[0]);
                            return JSON.stringify({{urls: mp4.slice(0,5), htmllen: html.length}});
                        }}).catch(e => 'FETCH_FAIL: ' + e.toString());
                    }})()
                    """,
                    'awaitPromise': True
                })
            
            val = resp.get('result', {}).get('result', {}).get('value', '')
            print(f'{method}: {val[:800]}')
            
            if val and 'src' in val and 'mp4' in val:
                print(f'FOUND VIDEO URL in {method}!')

asyncio.run(main())