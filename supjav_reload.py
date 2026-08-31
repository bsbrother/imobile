#!/usr/bin/env python3
"""Reload supjav tab, capture network for video URLs."""
import json, asyncio, websockets, urllib.request, re

tab_id = 'E18CA31339E939D59DDD0CC37903CD5D'

async def main():
    async with websockets.connect(f'ws://127.0.0.1:9222/devtools/page/{tab_id}', max_size=2**24) as ws:
        msg_id = [0]
        async def cdp(method, params=None):
            msg_id[0] += 1
            msg = {'id': msg_id[0], 'method': method, 'params': params or {}}
            await ws.send(json.dumps(msg))
        
        video_urls = []
        
        async def collect():
            for _ in range(50):
                try:
                    msg = await asyncio.wait_for(ws.recv(), timeout=1)
                except asyncio.TimeoutError:
                    if video_urls:
                        break
                    continue
                data = json.loads(msg)
                method = data.get('method', '')
                params = data.get('params', {})
                if method == 'Network.requestWillBeSent':
                    url = params.get('request', {}).get('url', '')
                    if any(x in url for x in ['.m3u8', '.mp4', 'video', 'stream', 'hls', 'master', 'index.m3u8']):
                        video_urls.append(url)
                        print(f'REQ: {url[:200]}')
                elif method == 'Network.responseReceived':
                    resp_url = params.get('response', {}).get('url', '')
                    mime = params.get('response', {}).get('mimeType', '')
                    if any(x in (resp_url + mime) for x in ['video', 'mpegurl', '.m3u8', 'stream']):
                        video_urls.append(resp_url)
                        print(f'RES: {resp_url[:200]} ({mime})')
        
        await cdp('Network.enable')
        await cdp('Page.enable')
        
        collector_task = asyncio.create_task(collect())
        await cdp('Page.reload')
        
        try:
            await asyncio.wait_for(collector_task, timeout=25)
        except asyncio.TimeoutError:
            pass
        
        # Unique URLs
        unique = list(set(video_urls))
        if unique:
            with open('/home/kasm-user/Downloads/jav/supjav_url.txt', 'w') as f:
                f.write('\n'.join(unique))
            print(f'\nFound {len(unique)} URLs, saved to supjav_url.txt')
            for u in unique:
                print(f'  {u}')
        else:
            print('No video URLs found')

asyncio.run(main())