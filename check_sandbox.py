#!/usr/bin/env python3
"""Check iframe sandbox + retry contentDocument after longer wait."""
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
        
        # Check all iframe attributes
        resp = await cdp('Runtime.evaluate', {
            'expression': """
            (() => {
                let results = [];
                let iframes = document.querySelectorAll('iframe');
                iframes.forEach((f, i) => {
                    if (!f.src || !f.src.startsWith('blob:')) return;
                    let attrs = {};
                    for (let a of f.attributes) {
                        attrs[a.name] = a.value || '(empty)';
                    }
                    results.push(JSON.stringify({index: i, src: f.src.substring(0,60), attrs: attrs}));
                });
                return results.join('\\n') || 'NO_BLOB_IFRAMES';
            })()
            """
        })
        val = resp.get('result', {}).get('result', {}).get('value', '')
        print(f'Iframe attrs: {val}')
        
        # Try waiting longer and rechecking contentDocument
        for attempt in range(5):
            await asyncio.sleep(3)
            resp = await cdp('Runtime.evaluate', {
                'expression': """
                (() => {
                    let iframes = document.querySelectorAll('iframe[src*="blob"]');
                    if (iframes.length === 0) return 'NO_BLOB';
                    try {
                        let doc = iframes[0].contentDocument;
                        if (!doc) return 'NO_DOC';
                        let body = doc.body;
                        if (!body) return 'NO_BODY';
                        let html = body.innerHTML;
                        let videos = doc.querySelectorAll('video');
                        let info = [];
                        videos.forEach(v => {
                            info.push('src=' + (v.src||''));
                            info.push('currentSrc=' + (v.currentSrc||''));
                            info.push('duration=' + v.duration);
                        });
                        if (videos.length === 0) {
                            info.push('no video, body len=' + html.length);
                            if (html.length > 0) info.push('body[:300]: ' + html.substring(0,300));
                        }
                        return JSON.stringify(info);
                    } catch(e) {
                        return 'ERROR: ' + e.toString().substring(0,100);
                    }
                })()
                """
            })
            val = resp.get('result', {}).get('result', {}).get('value', '')
            print(f'  attempt {attempt+1}: {val[:500]}')
            if val and 'src=' in val and val != 'NO_DOC' and val != 'NO_BODY':
                break

asyncio.run(main())