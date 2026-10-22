#!/usr/bin/env python3
"""Compile the derived checker with the browser's own wasm Lean and pack the library.

usage: python build_packs.py --repo <repo> --derived <port_checker output> --engine <dir> \
                             --json-lib <dir> --out <dir> [--runtime <dir>] [--engine-info <json>] [--no-brotli]

  --engine    the unpacked runtime fixture: bin/lean.js, bin/lean.wasm, lib/lean (Init closure);
              its lean.js/lean.wasm compile the library (the command-line path)
  --runtime   a directory with the lean.js/lean.wasm to SHIP, when they differ from the
              fixture's (the book ships a relink with the parse-error patch; see
              tools/engine/ENGINE.md); default: the fixture's own pair
  --engine-info  a JSON object merged into manifest.engine (patch, link kit, toolchain)
  --json-lib  the .olean/.ir/.ir.sig closure of Lean.Data.Json (Std + Lean parts), same commit
  --out       receives build/ (sources, oleans, logs) and site/ (what a web server hosts):
                site/lean-<sha12>.js, site/lean-<sha12>.wasm      the engine
                site/<pack>-<sha12>.pack (+ .gz, .br)             init, json, qccdc
                site/<pack>-<sha12>.index.json                    [path, offset, length] per file
                site/manifest.json                                everything above, with sha256 + sizes

Compiling: `lean -j0` under Node (tools/lean-node.cjs) with LEAN_NODE_LATE_TASKMGR=4.  With a
task manager the CLI reads every imported .olean on a pthread, and each file read is proxied
to the blocked main thread: 170-250 s per module.  With -j0 the reads happen on the main
thread (about 5 s for the whole closure); the runner then starts the task manager when
finalizeImport begins, before anything calls IO.Promise.new (which panics without one).  The
outputs are byte-identical to the slow route's.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import zlib
import gzip
from pathlib import Path

HERE = Path(__file__).resolve().parent
LEAN_NODE = HERE / "lean-node.cjs"
BROTLI_JS = HERE / "brotli.cjs"


def sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def git(repo: Path, *args: str, input: bytes | None = None) -> str:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, input=input, check=True).stdout.decode().strip()


def worktree_object_id(repo: Path, rel: str) -> str:
    """git object id of a working-tree path (blob or tree), computed without writing to .git."""
    p = repo / rel
    if p.is_file():
        return git(repo, "hash-object", "--", rel)
    entries = []
    for child in sorted(p.iterdir(), key=lambda c: c.name + ("/" if c.is_dir() else "")):
        oid = worktree_object_id(repo, f"{rel}/{child.name}")
        mode = b"40000" if child.is_dir() else b"100644"
        entries.append(mode + b" " + child.name.encode() + b"\0" + bytes.fromhex(oid))
    body = b"".join(entries)
    return hashlib.sha1(b"tree %d\0" % len(body) + body).hexdigest()


def qcheck_provenance(repo: Path) -> dict:
    sys.path.insert(0, str(repo))
    from qccd.workspace import toolchain  # the repo's own definition of the checker's version
    paths = toolchain.QCHECK_SOURCES
    work = {p: worktree_object_id(repo, p) for p in paths}
    head = {}
    for p in paths:
        r = subprocess.run(["git", "rev-parse", "--verify", "-q", f"HEAD:{p}"], cwd=repo, capture_output=True, text=True)
        head[p] = r.stdout.strip()
    pinned = toolchain.manifest()["qcheck"]["version"]
    return {
        "qcheck_version": toolchain.qcheck_version(work),          # of the sources actually read
        "qcheck_version_head": toolchain.qcheck_version(head) if all(head.values()) else None,
        "worktree_matches_head": work == head,
        "pinned_in_toolchain_json": pinned,
        "repo_head": git(repo, "rev-parse", "HEAD"),
        "object_ids": work,
    }


def imports_of(text: str) -> list[str]:
    return re.findall(r"^(?:public\s+)?(?:meta\s+)?import\s+(\S+)", text, flags=re.M)


def module_order(src: Path) -> list[str]:
    mods = {}
    for p in sorted(src.rglob("*.lean")):
        rel = p.relative_to(src).as_posix()
        if rel == "QCCDC/Cert/Axioms.lean":
            continue                      # a check, not part of the library
        mods[rel[:-5].replace("/", ".")] = imports_of(p.read_text(encoding="utf-8"))
    order, seen = [], set()

    def visit(m: str, stack: tuple = ()):
        if m in seen or m not in mods:
            return
        if m in stack:
            raise SystemExit(f"import cycle through {m}")
        for d in mods[m]:
            visit(d, stack + (m,))
        seen.add(m)
        order.append(m)
    for m in sorted(mods):
        visit(m)
    return order


def run_lean(engine: Path, json_lib: Path, work: Path, args: list[str], timeout: int = 900) -> tuple[int, list[dict], str, float]:
    env = dict(os.environ, LEAN_NODE_LATE_TASKMGR="4", LEAN_NODE_EXTRA=f"{json_lib};{work / 'lib'}")
    t = time.time()
    r = subprocess.run(["node", "--stack-size=8192", str(LEAN_NODE), str(engine / "bin" / "lean.js"), str(engine / "lib" / "lean"),
                        str(work), "-j0", *args], env=env, capture_output=True, timeout=timeout)
    dt = time.time() - t
    out = r.stdout.decode("utf-8", "replace")
    msgs = []
    for line in out.splitlines():
        if line.startswith("{"):
            try:
                msgs.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return r.returncode, msgs, out + r.stderr.decode("utf-8", "replace"), dt


def pack(name: str, roots: list[tuple[Path, str]], commit: str, site: Path, brotli: bool) -> dict:
    files = []
    for root, prefix in roots:
        for p in sorted(root.rglob("*")):
            if p.is_file() and re.search(r"\.(olean|ir|ir\.sig)$", p.name):
                files.append((p, (prefix + p.relative_to(root).as_posix())))
    files.sort(key=lambda f: f[1])
    chunks, index, off = [], [], 0
    for p, rel in files:
        b = p.read_bytes()
        if rel.endswith(".olean") and (b[:5] != b"olean" or b[40:80].decode("latin1") != commit):
            raise SystemExit(f"{rel}: not an .olean of engine commit {commit}")
        chunks.append(b)
        index.append([rel, off, len(b)])
        off += len(b)
    raw = b"".join(chunks)
    h = sha256(raw)
    base = f"{name}-{h[:12]}"
    (site / f"{base}.pack").write_bytes(raw)
    gz = gzip.compress(raw, compresslevel=9, mtime=0)
    (site / f"{base}.pack.gz").write_bytes(gz)
    idx = json.dumps({"leanCommit": commit, "pack": f"{base}.pack", "files": index}, separators=(",", ":")).encode()
    (site / f"{base}.index.json").write_bytes(idx)
    entry = {"name": name, "file": f"{base}.pack", "sha256": h, "bytes": len(raw), "files": len(index),
             "gzip_bytes": len(gz), "index": f"{base}.index.json", "index_sha256": sha256(idx), "index_bytes": len(idx)}
    if brotli:
        entry["br_bytes"] = compress_br(site / f"{base}.pack")
    return entry


def compress_br(path: Path) -> int:
    subprocess.run(["node", str(BROTLI_JS), str(path)], check=True)
    return (path.parent / (path.name + ".br")).stat().st_size


def put(site: Path, src: Path, stem: str, ext: str, brotli: bool) -> dict:
    b = src.read_bytes()
    h = sha256(b)
    name = f"{stem}-{h[:12]}{ext}"
    (site / name).write_bytes(b)
    gz = gzip.compress(b, compresslevel=9, mtime=0)
    (site / (name + ".gz")).write_bytes(gz)
    e = {"file": name, "sha256": h, "bytes": len(b), "gzip_bytes": len(gz)}
    if brotli:
        e["br_bytes"] = compress_br(site / name)
    return e


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, type=Path)
    ap.add_argument("--derived", required=True, type=Path)
    ap.add_argument("--engine", required=True, type=Path)
    ap.add_argument("--json-lib", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--runtime", type=Path, default=None)
    ap.add_argument("--engine-info", type=Path, default=None)
    ap.add_argument("--no-brotli", action="store_true")
    a = ap.parse_args()
    out = a.out.resolve()
    work, site = out / "build", out / "site"
    if work.exists():
        shutil.rmtree(work)
    if site.exists():
        shutil.rmtree(site)
    (work / "lib").mkdir(parents=True)
    site.mkdir(parents=True)
    shutil.copytree(a.derived, work / "src", ignore=shutil.ignore_patterns("*.diff", "*.json"))
    commit = (a.engine / "lib" / "lean" / "Init.olean").read_bytes()[40:80].decode("latin1")
    prov = qcheck_provenance(a.repo.resolve())
    print(f"engine commit {commit}; qcheck_version {prov['qcheck_version']} (HEAD {prov['qcheck_version_head']}, pinned {prov['pinned_in_toolchain_json']})")

    builds, problems = [], []
    for mod in module_order(work / "src"):
        rel = mod.replace(".", "/")
        (work / "lib" / rel).parent.mkdir(parents=True, exist_ok=True)
        code, msgs, log, dt = run_lean(a.engine, a.json_lib, work, ["-R", "/work/src", "-o", f"/work/lib/{rel}.olean",
                                                                   "-i", f"/work/lib/{rel}.ilean", "--json", f"/work/src/{rel}.lean"])
        (work / f"{mod}.log").write_text(log, encoding="utf-8")
        errs = [m for m in msgs if m.get("severity") == "error"]
        warns = [m for m in msgs if m.get("severity") == "warning"]
        print(f"  {mod}: exit {code}, {dt:.1f} s, {len(errs)} errors, {len(warns)} warnings")
        builds.append({"module": mod, "seconds": round(dt, 1), "exit": code,
                       "warnings": [f"{m['pos']['line']}:{m['pos']['column']} {m['data'].splitlines()[0]}" for m in warns]})
        if code != 0 or errs:
            for m in errs:
                print(f"    error {m['pos']['line']}:{m['pos']['column']} {m['data']}")
            problems.append(mod)
            break
    if problems:
        raise SystemExit(f"build failed in {problems}; logs in {work}")
    # the trusted base, printed by the same engine
    code, msgs, log, dt = run_lean(a.engine, a.json_lib, work, ["--json", "/work/src/QCCDC/Cert/Axioms.lean"])
    axioms = {}
    for m in msgs:
        mm = re.match(r"'([^']+)' depends on axioms: \[(.*)\]", m.get("data", ""))
        if mm:
            axioms[mm.group(1)] = [x.strip() for x in mm.group(2).split(",") if x.strip()]
    print(f"  axioms: {len(axioms)} reported in {dt:.1f} s")

    brotli = not a.no_brotli
    rt = a.runtime if a.runtime else a.engine / "bin"
    engine_files = {"lean.js": put(site, rt / "lean.js", "lean", ".js", brotli),
                    "lean.wasm": put(site, rt / "lean.wasm", "lean", ".wasm", brotli)}
    compiler = {"lean.js": sha256((a.engine / "bin" / "lean.js").read_bytes()),
                "lean.wasm": sha256((a.engine / "bin" / "lean.wasm").read_bytes())}
    engine_info = json.loads(a.engine_info.read_text(encoding="utf-8")) if a.engine_info else {}
    packs = [pack("init", [(a.engine / "lib" / "lean", "")], commit, site, brotli),
             pack("json", [(a.json_lib, "")], commit, site, brotli),
             pack("qccdc", [(work / "lib", "")], commit, site, brotli)]
    port_sources = json.loads((a.derived / "port-sources.json").read_text(encoding="utf-8")) if (a.derived / "port-sources.json").exists() else None
    manifest = {
        "schema": 1,
        "built": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "engine": {"lean": "4.33.0-pre", "commit": commit, "source": "github.com/cauli/lean4 (release browser-62b6a22-compact1; runtime fixture runtime-62b6a22-compact1)",
                   "files": engine_files, "needs": "SharedArrayBuffer: serve pages and workers with COOP same-origin + COEP require-corp",
                   "library_compiled_by": {"note": "the unmodified fixture lean.js/lean.wasm, command-line path", "sha256": compiler},
                   **engine_info},
        "packs": packs,
        "derived_from": {**prov, "port_sources": port_sources},
        "modules": builds,
        "axioms": axioms,
        "first_load": {"raw_bytes": sum(f["bytes"] for f in engine_files.values()) + sum(p["bytes"] for p in packs),
                       "gzip_bytes": sum(f["gzip_bytes"] for f in engine_files.values()) + sum(p["gzip_bytes"] for p in packs),
                       "br_bytes": (sum(f["br_bytes"] for f in engine_files.values()) + sum(p["br_bytes"] for p in packs)) if brotli else None},
    }
    (site / "manifest.json").write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    fl = manifest["first_load"]
    print(f"site: {site}\n  first load raw {fl['raw_bytes']/2**20:.1f} MiB, gzip {fl['gzip_bytes']/2**20:.1f} MiB"
          + (f", brotli {fl['br_bytes']/2**20:.1f} MiB" if brotli else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
