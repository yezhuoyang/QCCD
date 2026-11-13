#!/usr/bin/env python3
"""The egress proxy of an agent review (qccd/review/container.py): HTTP CONNECT to the allowed
hosts only, nothing else.

    python3 egress_proxy.py --port 3128 --allow api.anthropic.com,claude.ai [--ports 443]

A host is allowed when it is one of --allow or a subdomain of one, and only on --ports.  Plain
HTTP is refused (the model APIs are HTTPS).  Every request, let through or refused, is one JSON
line on stdout: {"host", "port", "allowed"} -- the review keeps the list.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys


def allowed(host: str, port: str, allow: list[str], ports: set[str]) -> bool:
    h = host.lower().rstrip(".").strip("[]")
    return port in ports and any(h == a or h.endswith("." + a) for a in allow)


def log(**kw) -> None:
    print(json.dumps(kw), flush=True)


async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError, asyncio.IncompleteReadError):
        pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, allow: list[str], ports: set[str]) -> None:
    try:
        line = await asyncio.wait_for(reader.readline(), 30)
        parts = line.decode("latin-1").split()
        while True:                                  # the request's headers, up to the blank line
            h = await asyncio.wait_for(reader.readline(), 30)
            if h in (b"\r\n", b"\n", b""):
                break
        if len(parts) < 2 or parts[0].upper() != "CONNECT":
            target = parts[1] if len(parts) > 1 else ""
            log(host=target[:200], port="", allowed=False, method=(parts[0] if parts else "")[:16])
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        host, _, port = parts[1].rpartition(":")
        ok = bool(host) and allowed(host, port, allow, ports)
        log(host=host[:200], port=port[:8], allowed=ok)
        if not ok:
            writer.write(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            return
        up_r, up_w = await asyncio.wait_for(asyncio.open_connection(host, int(port)), 20)
        writer.write(b"HTTP/1.1 200 Connection established\r\n\r\n")
        await writer.drain()
        await asyncio.gather(pipe(reader, up_w), pipe(up_r, writer))
    except (asyncio.TimeoutError, ConnectionError, OSError, ValueError):
        try:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\n\r\n")
        except Exception:
            pass
    finally:
        try:
            writer.close()
        except Exception:
            pass


async def main(argv=None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=3128)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--allow", required=True, help="comma-separated hosts (and their subdomains)")
    ap.add_argument("--ports", default="443", help="comma-separated destination ports")
    a = ap.parse_args(argv)
    allow = [h.strip().lower() for h in a.allow.split(",") if h.strip()]
    ports = {p.strip() for p in a.ports.split(",") if p.strip()}
    server = await asyncio.start_server(lambda r, w: handle(r, w, allow, ports), a.host, a.port)
    print(json.dumps({"listening": a.port, "allow": allow}), file=sys.stderr, flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
