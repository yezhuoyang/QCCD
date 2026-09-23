"""Install, inspect and remove the QCCD skill + MCP configuration for an agent client.

ONE canonical skill (`qccd/workspace/skill/`) is packaged for each client, with references
generated from the live registries (operations, rules, the workspace's task release, the
evaluator's stages) so the instructions and the code ship as one release:

  Claude Code   .claude/skills/qccd/        the skill
                .mcp.json  "qccd"           stdio MCP server (project scope; Claude asks to approve it)
                CLAUDE.md  managed block    a short pointer to the skill
  Codex         .agents/skills/qccd/        the skill (Codex scans .agents/skills up to the repo root)
                .codex/config.toml block    [mcp_servers.qccd] (Codex loads project config only for
                                            TRUSTED projects -- `status` says so)
                AGENTS.md  managed block    a short pointer to the skill

Existing agent instructions and other MCP servers are preserved: edits live inside
explicit managed blocks (`qccd:begin` / `qccd:end`), a JSON server entry is marked with
`QCCD_MANAGED=1`, and a configuration the user wrote themselves is never overwritten --
install refuses and says why.  Uninstall removes exactly what install wrote; a skill file the
user changed since is kept and reported.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path

from .jsonsafe import strict_loads

__all__ = ["install", "uninstall", "status", "build_skill", "SKILL_VERSION"]

SKILL_SRC = Path(__file__).resolve().parent / "skill"
SKILL_VERSION = "1.1.0"
MD_BEGIN = "<!-- qccd:begin (managed by `qccd agent install`; edit outside this block) -->"
MD_END = "<!-- qccd:end -->"
TOML_BEGIN = "# qccd:begin (managed by `qccd agent install`; edit outside this block)"
TOML_END = "# qccd:end"
MANIFEST = ".qccd-managed.json"

POINTER = """## QCCD workspace

This project is a QCCD co-design workspace. Use the `qccd` skill and the `qccd_*` MCP tools:
start each turn with `qccd_get_context`, change the design only with `qccd_apply_change_set`
(pass the `expected_revision` you read), and answer Studio prompts in their threads. Prompts the
user sends from QCCD Studio arrive as `<channel source="qccd">` messages or as `unread_prompts`.
Never edit `.qccd/`, never publish, never treat a skipped check as passed."""


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _python_env() -> tuple:
    """The interpreter the MCP server runs under, and the env it needs to import qccd."""
    exe = sys.executable
    repo = Path(__file__).resolve().parents[2]
    env = {"QCCD_MANAGED": "1"}
    import importlib.util
    spec = importlib.util.find_spec("qccd")
    installed = spec is not None and spec.origin and "site-packages" in str(spec.origin)
    if not installed:
        env["PYTHONPATH"] = str(repo)
    return exe, env


def mcp_server_config(root: Path, client: str, *, session_id: str | None = None,
                      channel: bool = False) -> dict:
    """The stdio server entry an agent client runs (the same one `install` writes)."""
    exe, env = _python_env()
    if session_id:
        env["QCCD_SESSION_ID"] = session_id
    if os.environ.get("QCCD_RUNTIME_DIR"):
        env["QCCD_RUNTIME_DIR"] = os.environ["QCCD_RUNTIME_DIR"]
    args = ["-m", "qccd.workspace", "mcp", "--client", client, "--root", str(root)] + (["--channel"] if channel else [])
    return {"command": exe, "args": args, "env": env}


def build_skill(dest: Path, root: Path | None = None) -> dict:
    """Write the skill for one client: static files + generated references + manifest."""
    from ..verify.rules import RULE_STATEMENTS
    from .operations import describe_operations
    dest = Path(dest)
    if dest.exists():
        shutil.rmtree(dest)
    (dest / "references").mkdir(parents=True)
    files = {}

    def put(rel: str, data: bytes):
        p = dest / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        files[rel] = _sha(data)

    put("SKILL.md", (SKILL_SRC / "SKILL.md").read_bytes())
    put("references/workflow.md", (SKILL_SRC / "references" / "workflow.md").read_bytes())
    put("references/operations.json", json.dumps(describe_operations(), indent=1).encode())
    put("references/rules.json", json.dumps([{"id": k, "statement": v} for k, v in RULE_STATEMENTS.items()],
                                            indent=1).encode())
    from . import evaluator as ev
    put("references/evaluator.json", json.dumps({"stages_doc": ev.__doc__, "statuses": list(ev.STAGE_STATUSES),
                                                 "allowed_skips": sorted(ev.ALLOWED_SKIPS)}, indent=1).encode())
    if root is not None:
        try:
            from .tasks import find_release, read_lock
            rel = find_release(read_lock(root)["task"]["id"])
            put("references/task.json", json.dumps(rel.summary(), indent=1).encode())
        except Exception:
            pass
    put("examples/change_set.json", json.dumps({
        "expected_revision": 3, "request_id": "example-1", "mode": "preview", "summary": "a two-site spur",
        "operations": [{"type": "add_chain", "prefix": "S", "count": 2, "start": [0, 2], "step": [2, 0],
                        "zone": "trap", "attach_to": "C0"}]}, indent=1).encode())
    man = {"skill": "qccd", "version": SKILL_VERSION, "files": files}
    (dest / MANIFEST).write_text(json.dumps(man, indent=1), encoding="utf-8")
    return man


def _modified(dest: Path) -> list:
    m = dest / MANIFEST
    if not m.exists():
        return ["(no manifest: not installed by qccd)"]
    man = json.loads(m.read_text(encoding="utf-8"))
    out = []
    for rel, d in man.get("files", {}).items():
        p = dest / rel
        if not p.exists() or _sha(p.read_bytes()) != d:
            out.append(rel)
    return out


# ---------------------------------------------------------------------- managed text blocks

def _set_block(path: Path, text: str, begin: str, end: str) -> str:
    old = path.read_text(encoding="utf-8") if path.exists() else ""
    block = f"{begin}\n{text.rstrip()}\n{end}\n"
    if begin.split(" ")[0] + " qccd:begin" in old or "qccd:begin" in old:
        pat = re.compile(re.escape(begin.split("(")[0].strip()) + r".*?" + re.escape(end) + r"\n?", re.S)
        new = pat.sub(lambda m: block, old, count=1)
        verb = "updated"
    else:
        new = (old.rstrip() + "\n\n" if old.strip() else "") + block
        verb = "added"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    return verb


def _drop_block(path: Path, begin: str, end: str) -> bool:
    if not path.exists():
        return False
    old = path.read_text(encoding="utf-8")
    pat = re.compile(r"\n*" + re.escape(begin.split("(")[0].strip()) + r".*?" + re.escape(end) + r"\n?", re.S)
    new, n = pat.subn("\n", old, count=1)
    if n:
        new = new.strip("\n")
        if new:
            path.write_text(new + "\n", encoding="utf-8")
        else:
            path.unlink()
    return bool(n)


def _toml_str(s: str) -> str:
    return "'" + s + "'" if "'" not in s else json.dumps(s)


# ---------------------------------------------------------------------- per client

def install(root: Path, client: str, *, scope: str = "project", channel: bool = False) -> list:
    root = Path(root)
    if scope != "project":
        raise ValueError("only project scope is supported")
    exe, env = _python_env()
    lines = []
    if client == "claude":
        man = build_skill(root / ".claude" / "skills" / "qccd", root)
        lines.append(f"skill      .claude/skills/qccd  (v{man['version']}, {len(man['files'])} files)")
        mcp = root / ".mcp.json"
        doc = strict_loads(mcp.read_bytes()) if mcp.exists() else {}
        servers = doc.setdefault("mcpServers", {})
        cur = servers.get("qccd")
        if cur and (cur.get("env") or {}).get("QCCD_MANAGED") != "1":
            raise RuntimeError(".mcp.json already has a 'qccd' server that qccd did not write; remove or rename it")
        args = ["-m", "qccd.workspace", "mcp", "--client", "claude"] + (["--channel"] if channel else [])
        servers["qccd"] = {"command": exe, "args": args, "env": env}
        mcp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        lines.append(f"mcp        .mcp.json 'qccd' ({'channel' if channel else 'tools; pull delivery'}) "
                     f"- Claude Code asks you to approve project servers on first use")
        verb = _set_block(root / "CLAUDE.md", POINTER, MD_BEGIN, MD_END)
        lines.append(f"pointer    CLAUDE.md managed block {verb}")
        if channel:
            lines.append("channel    start Claude Code here with: claude --dangerously-load-development-channels server:qccd")
            lines.append("           (Claude Code channels are a research preview and need a claude.ai login)")
    elif client == "codex":
        man = build_skill(root / ".agents" / "skills" / "qccd", root)
        lines.append(f"skill      .agents/skills/qccd  (v{man['version']}, {len(man['files'])} files)")
        cfg = root / ".codex" / "config.toml"
        text = cfg.read_text(encoding="utf-8") if cfg.exists() else ""
        outside = re.sub(re.escape(TOML_BEGIN.split("(")[0].strip()) + r".*?" + re.escape(TOML_END), "", text, flags=re.S)
        if re.search(r"^\s*\[mcp_servers\.qccd\]", outside, re.M):
            raise RuntimeError(".codex/config.toml already defines [mcp_servers.qccd] outside the managed block")
        body = "\n".join([
            "[mcp_servers.qccd]",
            f"command = {_toml_str(exe)}",
            'args = ["-m", "qccd.workspace", "mcp", "--client", "codex"]',
            "startup_timeout_sec = 60",
            "# the workspace service authorises every call; set \"prompt\" to approve each one in Codex",
            'default_tools_approval_mode = "approve"',
            "tool_timeout_sec = 300",
            "",
            "[mcp_servers.qccd.env]",
        ] + [f"{k} = {_toml_str(v)}" for k, v in env.items()])
        verb = _set_block(cfg, body, TOML_BEGIN, TOML_END)
        lines.append(f"mcp        .codex/config.toml [mcp_servers.qccd] {verb} (loaded only if you trust this project in Codex)")
        verb = _set_block(root / "AGENTS.md", POINTER, MD_BEGIN, MD_END)
        lines.append(f"pointer    AGENTS.md managed block {verb}")
        lines.append("delivery   run `qccd agent connect --client codex` for automatic Studio -> Codex delivery")
    else:
        raise ValueError("client must be codex or claude")
    return lines


def uninstall(root: Path, client: str, *, scope: str = "project") -> list:
    root = Path(root)
    lines = []
    sk = root / (".claude" if client == "claude" else ".agents") / "skills" / "qccd"
    if sk.exists():
        mod = _modified(sk)
        if mod:
            lines.append(f"kept       {sk.relative_to(root)} (changed since install: {', '.join(mod)})")
        else:
            shutil.rmtree(sk)
            lines.append(f"removed    {sk.relative_to(root)}")
    if client == "claude":
        mcp = root / ".mcp.json"
        if mcp.exists():
            doc = strict_loads(mcp.read_bytes())
            cur = (doc.get("mcpServers") or {}).get("qccd")
            if cur and (cur.get("env") or {}).get("QCCD_MANAGED") == "1":
                del doc["mcpServers"]["qccd"]
                if not doc["mcpServers"] and set(doc) == {"mcpServers"}:
                    mcp.unlink()
                else:
                    mcp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
                lines.append("removed    .mcp.json 'qccd'")
        if _drop_block(root / "CLAUDE.md", MD_BEGIN, MD_END):
            lines.append("removed    CLAUDE.md managed block")
    else:
        if _drop_block(root / ".codex" / "config.toml", TOML_BEGIN, TOML_END):
            lines.append("removed    .codex/config.toml managed block")
        if _drop_block(root / "AGENTS.md", MD_BEGIN, MD_END):
            lines.append("removed    AGENTS.md managed block")
    return lines or ["nothing to remove"]


def status(root: Path, client: str) -> dict:
    root = Path(root)
    sk = root / (".claude" if client == "claude" else ".agents") / "skills" / "qccd"
    out: dict = {"client": client, "skill": None, "mcp": None, "pointer": False, "notes": []}
    if (sk / MANIFEST).exists():
        man = json.loads((sk / MANIFEST).read_text(encoding="utf-8"))
        out["skill"] = {"path": str(sk.relative_to(root)), "version": man["version"],
                        "current": man["version"] == SKILL_VERSION, "modified": _modified(sk)}
    if client == "claude":
        mcp = root / ".mcp.json"
        if mcp.exists():
            cur = (strict_loads(mcp.read_bytes()).get("mcpServers") or {}).get("qccd")
            if cur:
                out["mcp"] = {"command": cur.get("command"), "args": cur.get("args"),
                              "channel": "--channel" in (cur.get("args") or []),
                              "managed": (cur.get("env") or {}).get("QCCD_MANAGED") == "1"}
        out["pointer"] = MD_BEGIN.split("(")[0].strip() in ((root / "CLAUDE.md").read_text(encoding="utf-8")
                                                          if (root / "CLAUDE.md").exists() else "")
        out["capabilities"] = ({"delivery": "automatic via Claude Code channel (unacknowledged; research preview)"}
                               if out["mcp"] and out["mcp"]["channel"] else
                               {"delivery": "pull only (reduced): prompts are seen on the next qccd_get_context"})
    else:
        cfg = root / ".codex" / "config.toml"
        out["mcp"] = {"present": cfg.exists() and "[mcp_servers.qccd]" in cfg.read_text(encoding="utf-8"),
                      "note": "Codex loads .codex/config.toml only for trusted projects"}
        out["pointer"] = MD_BEGIN.split("(")[0].strip() in ((root / "AGENTS.md").read_text(encoding="utf-8")
                                                          if (root / "AGENTS.md").exists() else "")
        out["capabilities"] = {"delivery": "automatic via the Codex app-server bridge after "
                                           "`qccd agent connect --client codex`; otherwise pull"}
    return out
