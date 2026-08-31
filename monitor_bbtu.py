#!/usr/bin/env python3
"""Monitor BBTU-034 recording progress and trigger download when done."""
import json, asyncio, websockets, urllib.request, time

async def main():
    # Find the tab
    tab_id = None
    for t in json.loads(urllib.request.urlopen('http://127.0.0.1:9222/json').read()):
        if '8J54GX5K' in t.get('url',''):
            tab_id = t['id']
            break
    if not tab_id:
        print("Tab not found")
        return
    
    async with websockets.connect(f'ws://127.0.0.1:9222/devtools/page/{tab_id}', max_size=2**24) as ws:
        async def cdp(method, params=None):
            msg = {'id': 1, 'method': method, 'params': params or {}}
            await ws.send(json.dumps(msg))
            while True:
                resp = await ws.recv()
                data = json.loads(resp)
                if data.get('id') == 1:
                    return data
        
        # Set download dir
        await cdp('Browser.setDownloadBehavior', {
            'behavior': 'allow',
            'downloadPath': '/home/kasm-user/Downloads/jav'
        })
        
        total_dur = 6541
        last_pct = 0
        
        while True:
            await asyncio.sleep(60)
            resp = await cdp('Runtime.evaluate', {
                'expression': '''
                (() => {
                    let v = window._bbtuVideo;
                    let r = window._bbtuRecorder;
                    if (!v || !r) return JSON.stringify({error: 'lost', state: r?r.state:'null'});
                    return JSON.stringify({
                        pct: (v.currentTime/v.duration*100).toFixed(1),
                        state: r.state,
                        cur: Math.round(v.currentTime)
                    });
                })()
                '''
            })
            val = resp.get('result',{}).get('result',{}).get('value','')
            try:
                d = json.loads(val)
                pct = float(d.get('pct', 0))
                print(f"{time.strftime('%H:%M:%S')} | {d['pct']}% ({d['state']}) current={d['cur']}s")
                
                if last_pct > 0 and pct == last_pct:
                    print("Stalled! Checking...")
                    # Try resuming playback
                    await cdp('Runtime.evaluate', {
                        'expression': '''
                        (() => {
                            let v = window._bbtuVideo;
                            if (v && v.paused) { v.play(); return 'resumed'; }
                            return 'not paused';
                        })()
                        '''
                    })
                
                last_pct = pct
                
                # Check if done or near done
                if pct >= 99.9:
                    print("Recording complete! Stopping and downloading...")
                    resp = await cdp('Runtime.evaluate', {
                        'expression': '''
                        (() => {
                            let r = window._bbtuRecorder;
                            if (r && r.state === 'recording') {
                                r.stop();
                                return 'stopped, download triggered';
                            }
                            return 'not recording: ' + (r?r.state:'null');
                        })()
                        '''
                    })
                    print(resp.get('result',{}).get('result',{}).get('value',''))
                    break
            except Exception as e:
                print(f"Parse error: {e} | raw: {val[:200]}")

if __name__ == '__main__':
    asyncio.run(main())