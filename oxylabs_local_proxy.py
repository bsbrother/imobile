#!/usr/bin/env python3
"""Simple local proxy that forwards to Oxylabs with auth.
Runs on localhost:60001, forwards all traffic through unblock.oxylabs.io:60000 with auth.
"""
import asyncio
import os
import sys

OXYLABS_HOST = os.environ.get('OXYLABS_PROXY_HOST', 'unblock.oxylabs.io')
OXYLABS_PORT = int(os.environ.get('OXYLABS_PROXY_PORT', '60000'))
OXYLABS_USER = os.environ.get('OXYLABS_USERNAME', 'bsbrother8_dyGv0')
OXYLABS_PASS = os.environ.get('OXYLABS_PASSWORD', '')
LISTEN_HOST = '127.0.0.1'
LISTEN_PORT = 60001

BUFFER_SIZE = 65536

async def forward(reader, writer):
    """Forward one connection through Oxylabs."""
    try:
        # Read the CONNECT/HTTP request
        data = await reader.read(BUFFER_SIZE)
        if not data:
            return
        
        # Parse the first line to determine target
        first_line = data.split(b'\r\n')[0].decode('utf-8', errors='replace')
        
        if first_line.startswith('CONNECT'):
            # HTTPS CONNECT: extract host:port
            parts = first_line.split()
            if len(parts) >= 2:
                target_host, target_port_str = parts[1].split(':')
                target_port = int(target_port_str)
                
                # Connect to Oxylabs with auth
                oxy_reader, oxy_writer = await asyncio.open_connection(
                    OXYLABS_HOST, OXYLABS_PORT
                )
                
                # Send CONNECT to Oxylabs with auth
                auth = base64.b64encode(f'{OXYLABS_USER}:{OXYLABS_PASS}'.encode()).decode()
                connect_req = f'CONNECT {target_host}:{target_port} HTTP/1.1\r\n'
                connect_req += f'Host: {target_host}:{target_port}\r\n'
                connect_req += f'Proxy-Authorization: Basic {auth}\r\n'
                connect_req += '\r\n'
                oxy_writer.write(connect_req.encode())
                await oxy_writer.drain()
                
                # Read response
                response = await oxy_reader.read(BUFFER_SIZE)
                if b'200' in response[:64]:
                    # Send 200 to client
                    writer.write(b'HTTP/1.1 200 Connection Established\r\n\r\n')
                    await writer.drain()
                    
                    # Bidirectional relay
                    await asyncio.gather(
                        relay(reader, oxy_writer),
                        relay(oxy_reader, writer),
                    )
                else:
                    writer.write(response[:512])
                    await writer.drain()
                
                oxy_writer.close()
        else:
            # Plain HTTP: forward through Oxylabs
            oxy_reader, oxy_writer = await asyncio.open_connection(
                OXYLABS_HOST, OXYLABS_PORT
            )
            
            # Add Proxy-Authorization header
            headers = data.split(b'\r\n')
            auth = base64.b64encode(f'{OXYLABS_USER}:{OXYLABS_PASS}'.encode()).decode()
            auth_header = f'Proxy-Authorization: Basic {auth}'.encode()
            
            # Find where to insert
            insert_pos = data.find(b'\r\n')
            if insert_pos > 0:
                modified = data[:insert_pos] + b'\r\n' + auth_header + data[insert_pos:]
            else:
                modified = data
            
            oxy_writer.write(modified)
            await oxy_writer.drain()
            
            # Relay response
            await relay(oxy_reader, writer)
            oxy_writer.close()
    except Exception as e:
        print(f'Proxy error: {e}', file=sys.stderr)
    finally:
        writer.close()

async def relay(reader, writer):
    """Relay data bidirectionally."""
    try:
        while True:
            data = await reader.read(BUFFER_SIZE)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except:
        pass

async def main():
    server = await asyncio.start_server(forward, LISTEN_HOST, LISTEN_PORT)
    addr = server.sockets[0].getsockname()
    print(f'Local proxy listening on {addr[0]}:{addr[1]}', file=sys.stderr)
    
    async with server:
        await server.serve_forever()

if __name__ == '__main__':
    import base64
    asyncio.run(main())