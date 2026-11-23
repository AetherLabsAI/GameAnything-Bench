#!/usr/bin/env python3
"""Inside a trial container: expose the host's model proxy socket (/run/gab/llm.sock, gab/host/keyproxy.py) as
http://127.0.0.1:PORT, so a harness that only speaks HTTP reaches its model while the container has no network. No key here:
the host proxy adds it.   usage: llm_forward.py PORT SOCKET"""
import asyncio, sys

PORT, SOCK = int(sys.argv[1]), sys.argv[2]


async def pipe(r, w):
    try:
        while True:
            b = await r.read(65536)
            if not b: break
            w.write(b); await w.drain()
    except Exception: pass
    finally:
        try: w.close()
        except Exception: pass


async def handle(cr, cw):
    try: ur, uw = await asyncio.open_unix_connection(SOCK)
    except Exception:
        cw.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"); await cw.drain(); cw.close(); return
    await asyncio.gather(pipe(cr, uw), pipe(ur, cw))


async def main():
    srv = await asyncio.start_server(handle, "127.0.0.1", PORT)
    async with srv: await srv.serve_forever()

asyncio.run(main())
