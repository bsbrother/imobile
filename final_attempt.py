#!/usr/bin/env python3
"""Click play and monitor network for video URL."""
import json, asyncio, websockets, urllib.request, re

tabs = json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json').read())
tab_id = None
for t in tabs:
    if 'mkmp-519' in t.get('url',''):
        tab_id = t['id']
        break

async def main():
    async with websockets.connect(f'ws://127.0.0.1:9222/devtools/page/{tab_id}', max_size=2**24) as ws:
        msg_id = [0]
        async def cdp(method, params=None):
            msg_id[0] += 1
            msg = {'id': msg_id[0], 'method': method, 'params': params or {}}
            await ws.send(json.dumps(msg))
        
        await cdp('Network.enable')
        await cdp('Page.enable')
        
        video_urls = []
        
        # Click play button
        await cdp('Runtime.evaluate', {
            'expression': """
            (() => {
                let btn = document.querySelector('.play-button');
                if (btn) { btn.click(); return 'clicked'; }
                let resp = document.querySelector('.responsive-player');
                if (resp) { resp.click(); return 'clicked_resp'; }
                let vp = document.getElementById('video-player');
                if (vp) { vp.click(); return 'clicked_vp'; }
                return 'no_target';
            })()
            """
        })
        
        # Monitor network for 15 seconds
        print('Monitoring network for video requests...')
        for _ in range(20):
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=1)
                data = json.loads(msg)
                method = data.get('method', '')
                params = data.get('params', {})
                
                if method == 'Network.requestWillBeSent':
                    url = params.get('request', {}).get('url', '')
                    if any(x in url.lower() for x in ['.mp4', '.m3u8', '.ts', 'video']):
                        video_urls.append(url)
                        print(f'  REQ: {url[:150]}')
                
                elif method == 'Network.responseReceived':
                    url = params.get('response', {}).get('url', '')
                    mime = params.get('response', {}).get('mimeType', '')
                    if any(x in (url+mime).lower() for x in ['video/', 'mp4', 'm3u8']):
                        video_urls.append(url)
                        print(f'  RESP: {url[:150]} mime={mime}')
            except asyncio.TimeoutError:
                pass
        
        # Also check the page state
        resp = await cdp('Runtime.evaluate', {
            'expression': """
            (() => {
                let videos = document.querySelectorAll('video');
                let results = [];
                videos.forEach(v => results.push(v.src||v.currentSrc||''));
                let sources = document.querySelectorAll('source');
                sources.forEach(s => results.push(s.src||''));
                return JSON.stringify(results);
            })()
            """
        })
        # Collect final response
        while True:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2)
                data = json.loads(msg)
                if data.get('id') == msg_id[0]:
                    val = data.get('result', {}).get('result', {}).get('value', '')
                    print(f'Page videos: {val}')
                    break
            except asyncio.TimeoutError:
                break
        
        # Deduplicate and save
        unique_urls = list(set(video_urls))
        if unique_urls:
            print(f'\nFound {len(unique_urls)} unique video URLs:')
            for u in unique_urls:
                print(f'  {u}')
            # Save first one
            with open('/home/kasm-user/Downloads/jav/mkmp519_video_url.txt', 'w') as f:
                f.write(unique_urls[0])
        else:
            print('\nNo video URLs captured')
            print('Trying fallback: scan pornfhd CDN for MKMP-519...')

asyncio.run(main())