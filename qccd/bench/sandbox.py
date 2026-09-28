"""Where the official grader runs a submitted compiler: a separate container, reached by a spool.

    python -m qccd.bench.sandbox --spool /sandbox          (the sandbox container's command)

Why a second container.  The grader already runs with no network, no credentials and a
read-only root, but a submitted compiler is somebody else's code: in the grader's own container
it could read the grader's files, leave a process behind to rewrite a report after it is
written, or look at other jobs.  So the grader never executes it.  It writes each request into
a spool directory it shares with the sandbox; the sandbox (same image, no network, no secrets,
nothing mounted but that spool) runs the compiler under `contract.invoke`'s limits, writes the
outputs back, and after every request kills every process it can signal (`kill(-1)`: in its
own PID namespace that is everything the compiler started).  The grader then reads the outputs
as untrusted data and grades them.  Hidden pairs come from a seed only the worker holds.

SPOOL PROTOCOL (per grading job <j>):

    jobs/<j>/compiler/                  the compiler directory       (grader writes, once)
    jobs/<j>/req/<n>/request.json       {"kind": "build"|"invoke", "timeout", "mem_mb"}
    jobs/<j>/req/<n>/in/                circuit.qasm, device.arch.json, device.expanded.json
    jobs/<j>/req/<n>/READY              written last
    jobs/<j>/req/<n>/out/               what the compiler wrote       (sandbox)
    jobs/<j>/req/<n>/result.json, DONE  the invocation record          (sandbox)

The same protocol run in one process (`QCCD_SANDBOX_SPOOL` unset) is `harness.LocalRunner`.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import sys
import threading
import time
from pathlib import Path

from .contract import CompilerSpec, Invocation, build, invoke, load_compiler

__all__ = ["SpoolRunner", "serve"]

MARGIN_S = 60.0


class SpoolRunner:
    """The grader's side: hand each build/invoke to the sandbox and wait for its answer."""

    def __init__(self, spool: Path, job: str):
        self.base = Path(spool) / "jobs" / job
        self.n = 0
        self.lock = threading.Lock()
        self.copied = False

    def _submit(self, spec: CompilerSpec, kind: str, timeout: float, mem_mb: int, inputs: dict[str, Path],
                cancel) -> tuple[Path, dict]:
        with self.lock:
            if not self.copied:
                if self.base.exists():
                    shutil.rmtree(self.base)
                shutil.copytree(spec.root, self.base / "compiler")
                self.copied = True
            self.n += 1
            rq = self.base / "req" / f"{self.n:05d}"
        (rq / "in").mkdir(parents=True)
        for name, src in inputs.items():
            shutil.copyfile(src, rq / "in" / name)
        (rq / "request.json").write_text(json.dumps({"kind": kind, "timeout": timeout, "mem_mb": mem_mb}),
                                         encoding="utf-8")
        (rq / "READY").write_text("", encoding="utf-8")
        deadline = time.time() + timeout + MARGIN_S
        while not (rq / "DONE").exists():
            if (cancel is not None and cancel.is_set()) or time.time() > deadline:
                return rq, {"status": "cancelled" if cancel is not None and cancel.is_set() else "timeout",
                            "seconds": timeout, "log": "the sandbox did not answer in time"}
            time.sleep(0.2)
        try:
            return rq, json.loads((rq / "result.json").read_text(encoding="utf-8")[:200000])
        except (OSError, ValueError):
            return rq, {"status": "crash", "seconds": 0.0, "log": "the sandbox's result was unreadable"}

    def build(self, spec: CompilerSpec, *, workdir: Path, timeout: float, cancel=None) -> tuple[bool, str]:
        if not spec.build:
            return True, ""
        _, r = self._submit(spec, "build", timeout, 8192, {}, cancel)
        return r.get("status") == "ok", str(r.get("log", ""))[-4000:]

    def invoke(self, spec: CompilerSpec, circuit: Path, device: Path, expanded: Path, out: Path, *,
               timeout: float, mem_mb: int, cancel=None) -> Invocation:
        rq, r = self._submit(spec, "invoke", timeout, mem_mb,
                             {"circuit.qasm": circuit, "device.arch.json": device,
                              "device.expanded.json": expanded}, cancel)
        out = Path(out)
        if out.exists():
            shutil.rmtree(out)
        out.mkdir(parents=True)
        prog = cert = None
        for name in ("program.tsir.json", "certificate.qcert.json"):
            src = rq / "out" / name
            # outputs are untrusted: a regular file of bounded size, or nothing
            if src.is_file() and not src.is_symlink() and src.stat().st_size <= 64 * 1024 * 1024:
                shutil.copyfile(src, out / name)
                if name.startswith("program"):
                    prog = out / name
                else:
                    cert = out / name
        status = str(r.get("status", "crash"))
        if status == "ok" and prog is None:
            status = "no_output"
        return Invocation(status, float(r.get("seconds", 0.0)), prog if status == "ok" else None, cert,
                          str(r.get("log", ""))[-4000:])

    def close(self) -> None:
        shutil.rmtree(self.base, ignore_errors=True)


# ---------------------------------------------------------------------- the sandbox's side

def _handle(rq: Path) -> None:
    req = json.loads((rq / "request.json").read_text(encoding="utf-8"))
    spec = load_compiler(rq.parent.parent / "compiler")
    t0 = time.time()
    if req["kind"] == "build":
        ok, log = build(spec, workdir=rq, timeout=float(req["timeout"]))
        res = {"status": "ok" if ok else "crash", "seconds": time.time() - t0, "log": log}
    else:
        i = rq / "in"
        inv = invoke(spec, i / "circuit.qasm", i / "device.arch.json", i / "device.expanded.json", rq / "out",
                     timeout=float(req["timeout"]), mem_mb=int(req["mem_mb"]))
        res = {"status": inv.status, "seconds": inv.seconds, "log": inv.log[-4000:]}
    (rq / "result.json").write_text(json.dumps(res), encoding="utf-8")
    (rq / "DONE").write_text("", encoding="utf-8")


def _reap() -> None:
    """Kill everything this sandbox can signal but itself: what a compiler left running."""
    if os.environ.get("QCCD_SANDBOX_KILL_ALL") != "1" or os.name == "nt":
        return                              # only inside the sandbox container (its own PID namespace)
    old = signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        os.kill(-1, signal.SIGKILL)
    except OSError:
        pass
    finally:
        signal.signal(signal.SIGTERM, old)


def serve(spool: Path, *, once: bool = False) -> None:
    spool = Path(spool)
    (spool / "jobs").mkdir(parents=True, exist_ok=True)
    while True:
        did = False
        for rq in sorted(spool.glob("jobs/*/req/*")):
            if (rq / "READY").exists() and not (rq / "DONE").exists() and not (rq / "TAKEN").exists():
                (rq / "TAKEN").write_text("", encoding="utf-8")
                try:
                    _handle(rq)
                except Exception as exc:           # the request, not the sandbox, failed
                    (rq / "result.json").write_text(json.dumps({"status": "crash", "seconds": 0.0,
                                                                "log": f"{type(exc).__name__}: {exc}"}),
                                                    encoding="utf-8")
                    (rq / "DONE").write_text("", encoding="utf-8")
                _reap()
                did = True
        if once:
            return
        if not did:
            time.sleep(0.2)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m qccd.bench.sandbox", description=__doc__.splitlines()[0])
    ap.add_argument("--spool", default=os.environ.get("QCCD_SANDBOX_SPOOL", "/sandbox"))
    ap.add_argument("--once", action="store_true")
    a = ap.parse_args(argv)
    serve(Path(a.spool), once=a.once)
    return 0


if __name__ == "__main__":
    sys.exit(main())
