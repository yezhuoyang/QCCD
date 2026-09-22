"""Drive the QCCD MCP adapter over REAL stdio with the official MCP SDK client.

    python tests/workspace_mcp_client.py <workspace-root> <script.json> [--client codex|claude|generic] [--channel]

The script is a list of `{"tool": name, "args": {...}}` or `{"sleep": s}` steps (a step's
args may say "$prev.<index>.<dotted.path>" to reuse an earlier result).  Prints one JSON
line: the server's capabilities, the tool list, every result, and every server->client
NOTIFICATION seen on the raw stream (the SDK's typed handler drops methods it does not
know, and a Claude channel push is one, so the stream is tapped below the session).
Runs under the project venv, where `mcp` is installed; the adapter is spawned with the
same interpreter.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _resolve(v, results):
    if isinstance(v, str) and v.startswith("$prev"):
        _, idx, *path = v.split(".")
        cur = results[int(idx)]
        for p in path:
            cur = cur[int(p)] if isinstance(cur, list) else cur[p]
        return cur
    if isinstance(v, dict):
        return {k: _resolve(x, results) for k, x in v.items()}
    if isinstance(v, list):
        return [_resolve(x, results) for x in v]
    return v


def _plain(x):
    return json.loads(json.dumps(x, default=lambda o: o.model_dump() if hasattr(o, "model_dump") else str(o)))


async def main(root: str, script_path: str, client: str, channel: bool) -> dict:
    import anyio
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    script = json.loads(Path(script_path).read_text(encoding="utf-8"))
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    args = ["-m", "qccd.workspace", "mcp", "--client", client, "--root", root] + (["--channel"] if channel else [])
    params = StdioServerParameters(command=sys.executable, args=args, env=env, cwd=root)
    notes: list = []
    out: dict = {"results": [], "notifications": notes}

    async with stdio_client(params) as streams:
        raw_read, write = streams[0], streams[1]
        tap_send, read = anyio.create_memory_object_stream(1000)

        async def tap():
            async with tap_send:
                async for item in raw_read:
                    msg = getattr(item, "message", None)
                    m = getattr(msg, "root", msg)          # mcp-types 2.x: the concrete message, no RootModel
                    if m is not None and getattr(m, "method", None) and getattr(m, "id", None) is None:
                        notes.append({"method": m.method, "params": _plain(getattr(m, "params", None))})
                    await tap_send.send(item)

        async with anyio.create_task_group() as tg:
            tg.start_soon(tap)
            async with ClientSession(read, write) as s:
                init = await s.initialize()
                out["capabilities"] = json.loads(init.capabilities.model_dump_json(by_alias=True, exclude_none=True))
                out["instructions"] = (init.instructions or "")[:200]
                tl = await s.list_tools()
                out["tools"] = [t.name for t in tl.tools]
                out["annotations"] = {t.name: _plain(t.annotations) for t in tl.tools}
                results: list = []
                for step in script:
                    if "sleep" in step:
                        await asyncio.sleep(step["sleep"])
                        results.append(None)
                        continue
                    a = _resolve(step.get("args") or {}, results)
                    r = await s.call_tool(step["tool"], a)
                    sc = r.structured_content if hasattr(r, "structured_content") else None
                    results.append(sc)
                    out["results"].append({"tool": step["tool"], "is_error": bool(r.is_error), "structured": sc,
                                           "text": (r.content[0].text if r.content else "")[:300]})
            tg.cancel_scope.cancel()
    return out


if __name__ == "__main__":
    root, script = sys.argv[1], sys.argv[2]
    client = sys.argv[sys.argv.index("--client") + 1] if "--client" in sys.argv else "generic"
    print(json.dumps(asyncio.run(main(root, script, client, "--channel" in sys.argv)), default=str))
