#!/usr/bin/env python3
"""Minimal local proxy that adds Oxylabs auth. Listens on 127.0.0.1:16001.
Reads credentials from ~/apps/imobile/.env by default."""
import asyncio, base64, os, sys, re

# Read credentials from .env file if not already in environment
env_file = os.path.expanduser('~/apps/imobile/.env')
if os.path.exists(env_file):
    with open(env_file) as f:
        for line in f:
            m = re.match(r'^\s*export\s+([A-Z_]+)=(.*)', line)
            if not m:
                m = re.match(r'^([A-Z_]+)=(.*)', line)
            if m:
                k, v = m.group(1), m.group(2).strip('"\'')
                if k == 'OXYLABS_USERNAME' and not os.environ.get(k):
                    os.environ[k] = v
                elif k == 'OXYLABS_PASSWORD' and not os.environ.get(k):
                    os.environ[k] = v

OXY = (os.environ.get('OXYLABS_PROXY_HOST', 'unblock.oxylabs.io'),
       int(os.environ.get('OXYLABS_PROXY_PORT', '60000')))
AUTH = base64.b64encode(f'{os.environ.get("OXYLABS_USERNAME","bsbrother8_dyGv0")}:{os.environ.get("OXYLABS_PASSWORD","")}'.encode()).decode()

async def handle(reader, writer):
    try:
        data = await reader.read(65536)
        if not data: return
        first = data.split(b'\r\n')[0].decode()
        
        # Connect to upstream proxy
        r2, w2 = await asyncio.open_connection(*OXY)
        
        if first.startswith('CONNECT '):
            hostport = first.split()[1]
            w2.write(f'CONNECT {hostport} HTTP/1.1\r\nProxy-Authorization: Basic {AUTH}\r\n\r\n'.encode())
            await w2.drain()
            resp = await r2.read(4096)
            if b'200' in resp[:64]:
                writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                await writer.drain()
                await asyncio.gather(
                    relay(reader, w2), relay(r2, writer))
            else:
                writer.write(resp)
                await writer.drain()
        else:
            w2.write(data[:data.index(b'\r\n')] + f'\r\nProxy-Authorization: Basic {AUTH}\r\n'.encode() + data[data.index(b'\r\n'):])
            await w2.drain()
            await relay(r2, writer)
        w2.close()
    except: pass
    finally: writer.close()

async def relay(r, w):
    while True:
        d = await r.read(65536)
        if not d: break
        w.write(d); await w.drain()

async def main():
    srv = await asyncio.start_server(handle, '127.0.0.1', 16001)
    print(f'Local auth proxy on 127.0.0.1:16001 -> {OXY[0]}:{OXY[1]}', file=sys.stderr)
    async with srv: await srv.serve_forever()

if __name__ == '__main__':
    asyncio.run(main())