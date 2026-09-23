"""Finding, starting and stopping the one service a workspace has.

There is ONE service process per active workspace.  It records itself in a runtime file
OUTSIDE the project -- `~/.qccd/runtime/<workspace-id>.json` (override the directory with
`QCCD_RUNTIME_DIR`) -- holding its port, pid and two bearer tokens:

    agent_token   what MCP adapters present; may edit, comment, run jobs, submit locally,
                  prepare (never approve) publication
    owner_token   what the CLI presents on the user's behalf

The tokens never enter the workspace directory, a URL, a log line or an export.  A
browser does not use either: it pairs with a one-time code and gets an HttpOnly cookie.

A runtime file whose pid is gone, or whose port answers for a different workspace, is
STALE and is removed rather than trusted.

A second, STICKY file (`<workspace-id>.sticky.json`, same directory) outlives any one
process: the port the service last listened on and the browser pairings it issued (the
sha256 of each cookie, with its CSRF token).  A service that restarts -- after a crash, a
kill, a reboot -- listens on the same port when it is free and still honours those
pairings, so an open Studio page's event stream reconnects by itself instead of pointing at
a dead port with a cookie nobody knows.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from .tasks import LOCK_NAME, read_lock

__all__ = ["runtime_dir", "runtime_path", "find_workspace_root", "read_runtime", "write_runtime",
           "remove_runtime", "ensure_service", "service_request", "free_port", "health",
           "read_sticky", "write_sticky", "bind_listener", "keep_alive", "code_identity"]


def runtime_dir() -> Path:
    d = Path(os.environ.get("QCCD_RUNTIME_DIR") or (Path.home() / ".qccd" / "runtime"))
    d.mkdir(parents=True, exist_ok=True)
    return d


def runtime_path(ws_id: str) -> Path:
    if not ws_id.replace("_", "").isalnum():
        raise ValueError("bad workspace id")
    return runtime_dir() / f"{ws_id}.json"


def find_workspace_root(start: Path | None = None) -> Path:
    p = Path(start or os.getcwd()).resolve()
    for d in [p, *p.parents]:
        if (d / LOCK_NAME).is_file():
            return d
    raise FileNotFoundError(f"no {LOCK_NAME} in {p} or any parent: run `qccd init` first")


def free_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind((host, 0))
        return s.getsockname()[1]


def _write_private(p: Path, data: dict) -> None:
    tmp = p.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def sticky_path(ws_id: str) -> Path:
    return runtime_path(ws_id).with_name(f"{ws_id}.sticky.json")


def read_sticky(ws_id: str) -> dict:
    try:
        d = json.loads(sticky_path(ws_id).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_sticky(ws_id: str, **fields) -> dict:
    d = read_sticky(ws_id)
    d.update(fields)
    _write_private(sticky_path(ws_id), d)
    return d


def bind_listener(preferred: int | None = None, host: str = "127.0.0.1", *, fallback: bool = True) -> socket.socket:
    """A listening socket on `preferred` when that port is free, else on any free port.
    Bound here, not by the web server, so there is no window between choosing the port and
    taking it.  No SO_REUSEADDR: on Windows it would let this process take a port another
    one is listening on."""
    for port in ([preferred] if preferred else []) + ([0] if fallback or not preferred else []):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            if os.name == "nt":
                s.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            s.bind((host, int(port)))
            s.listen(128)
            return s
        except OSError:
            s.close()
    raise OSError(f"cannot listen on {host}:{preferred}" if preferred else "no port to listen on")


def code_identity() -> str:
    """Which code a process runs: a digest of the qccd package's source files (path, size,
    modification time; about 150 files, ~10 ms).  A service keeps the code it started with,
    so after a `git pull` it runs the OLD code until it restarts -- this is how `qccd studio`
    tells.  `qccd/site` (the website builder) is left out: the service never loads it."""
    pkg = Path(__file__).resolve().parents[1]
    h = hashlib.sha256()
    for dirpath, dirnames, filenames in os.walk(pkg):
        dirnames[:] = sorted(d for d in dirnames if d not in ("__pycache__", "site") and not d.startswith("."))
        for name in sorted(filenames):
            if name.endswith((".py", ".js", ".css", ".html")):
                p = Path(dirpath) / name
                try:
                    st = p.stat()
                except OSError:
                    continue
                h.update(f"{p.relative_to(pkg).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()[:16]


def write_runtime(ws_id: str, root: Path, port: int, pid: int, *, code: str | None = None) -> dict:
    info = {"workspace_id": ws_id, "root": str(root), "host": "127.0.0.1", "port": port, "pid": pid,
            "agent_token": secrets.token_urlsafe(32), "owner_token": secrets.token_urlsafe(32),
            "started_at": time.time(), "code": code, "version": 1}
    _write_private(runtime_path(ws_id), info)
    return info


def remove_runtime(ws_id: str, pid: int | None = None) -> None:
    p = runtime_path(ws_id)
    try:
        if pid is not None:
            cur = json.loads(p.read_text(encoding="utf-8"))
            if cur.get("pid") != pid:
                return
        p.unlink()
    except (FileNotFoundError, ValueError):
        pass


def health(info: dict, timeout: float = 2.0) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/api/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError):
        return None


def read_runtime(ws_id: str, *, check: bool = True) -> dict | None:
    """The running service's record, or None.  A record is REMOVED only when its pid is
    provably gone; a live pid that does not answer yet (starting, busy) is left alone."""
    p = runtime_path(ws_id)
    if not p.is_file():
        return None
    try:
        info = json.loads(p.read_text(encoding="utf-8"))
    except ValueError:
        return None                        # being written; the writer replaces it atomically
    if not check:
        return info
    h = health(info)
    if h and h.get("workspace_id") == ws_id:
        return info
    from .core import _pid_alive
    if not _pid_alive(int(info.get("pid", 0))):
        remove_runtime(ws_id, int(info.get("pid", 0)))
    return None


def _holder_alive(ws_id: str) -> bool:
    info = read_runtime(ws_id, check=False)
    if not info:
        return False
    from .core import _pid_alive
    return _pid_alive(int(info.get("pid", 0)))


def ensure_service(root: Path, *, wait: float = 60.0, python: str | None = None,
                   restart_stale: bool = False) -> dict:
    """Return the running service for `root`, starting one (detached) if needed.

    `restart_stale`: a running service started from OTHER code than this process's (QCCD was
    updated since, or it predates this check) is stopped and started again, and the result
    carries `restarted_stale: True`.  Only `qccd studio` asks for it: an agent adapter must
    not stop the service the person is using."""
    root = Path(root).resolve()
    ws_id = read_lock(root)["workspace_id"]
    info = read_runtime(ws_id)
    restarted = False
    if info and restart_stale and info.get("code") != code_identity():
        _stop_stale(info)
        info, restarted = None, True
    if info:
        return info
    return dict(_start_service(root, ws_id, wait=wait, python=python), **({"restarted_stale": True} if restarted else {}))


def _stop_stale(info: dict, wait: float = 20.0) -> None:
    """Stop a service that runs older code, the way `qccd stop` does, and wait for it to go."""
    from .core import _pid_alive
    try:
        service_request(info, "POST", "/api/shutdown", {}, token="owner", timeout=10)
    except Exception:
        pass
    t0 = time.time()
    while _pid_alive(int(info.get("pid", 0))) and time.time() - t0 < wait:
        time.sleep(0.25)
    if _pid_alive(int(info.get("pid", 0))):
        raise RuntimeError(f"the running service (pid {info.get('pid')}) was started from older code and "
                           f"did not stop; run `qccd stop`, then `qccd studio` again")


def _start_service(root: Path, ws_id: str, *, wait: float, python: str | None) -> dict:
    # a live process holds the record but did not answer: give it time, never start a twin
    t0 = time.time()
    while _holder_alive(ws_id) and time.time() - t0 < 20:
        time.sleep(0.5)
        info = read_runtime(ws_id)
        if info:
            return info
    log = root / ".qccd" / "service.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    args = [python or sys.executable, "-m", "qccd.workspace", "serve", "--root", str(root)]
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": open(log, "ab"), "stderr": subprocess.STDOUT,
                "cwd": str(root)}
    env = dict(os.environ)
    repo = str(Path(__file__).resolve().parents[2])
    env["PYTHONPATH"] = repo + os.pathsep + env.get("PYTHONPATH", "")
    kw["env"] = env
    if os.name == "nt":
        # DETACHED | NEW_GROUP | NO_WINDOW, and BREAKAWAY_FROM_JOB: an adapter started by an
        # agent client may live in the client's job object, and a service started inside it
        # would be killed with the agent.  Breakaway is refused when the job forbids it;
        # then the service starts inside the job and the adapter says so.
        base = 0x00000008 | 0x00000200 | 0x08000000
        try:
            subprocess.Popen(args, creationflags=base | 0x01000000, **kw)
        except OSError:
            subprocess.Popen(args, creationflags=base, **kw)
    else:
        kw["start_new_session"] = True
        subprocess.Popen(args, **kw)
    t0 = time.time()
    while time.time() - t0 < wait:
        info = read_runtime(ws_id)
        if info:
            return info
        time.sleep(0.25)
    raise RuntimeError(f"the workspace service did not start within {wait:.0f} s; see {log}")


def keep_alive(root: Path, stop, *, interval: float = 3.0, on_event=None) -> str:
    """Watch the workspace's service until `stop` (a threading.Event) is set, restarting it
    when it was KILLED.  A service that was STOPPED on purpose removed its runtime file on
    the way out; one that was killed left the file behind with a dead pid.  Only the second
    is restarted, so `qccd stop` still stops.  Returns why it ended: "stopped" (the service
    was shut down on purpose) or "cancelled" (`stop` was set)."""
    from .core import _pid_alive
    root = Path(root).resolve()
    ws_id = read_lock(root)["workspace_id"]
    note = on_event or (lambda kind, info: None)
    while not stop.wait(interval):
        info = read_runtime(ws_id, check=False)
        if info is None:
            note("stopped", None)
            return "stopped"
        if health(info) or _pid_alive(int(info.get("pid", 0))):
            continue                        # answering, or alive and busy: leave it alone
        new = ensure_service(root)
        note("restarted", {"old_pid": info.get("pid"), "pid": new["pid"], "port": new["port"],
                           "same_port": new["port"] == info.get("port")})
    return "cancelled"


def service_request(info: dict, method: str, path: str, body=None, *, token: str = "agent",
                    session: str | None = None, timeout: float = 60.0):
    """One authenticated JSON request to the local service.  Raises `ServiceError`."""
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(f"http://127.0.0.1:{info['port']}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {info[token + '_token']}")
    req.add_header("Content-Type", "application/json")
    if session:
        req.add_header("X-QCCD-Session", session)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw.decode("utf-8")) if raw else None
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            detail = json.loads(raw.decode("utf-8"))
        except ValueError:
            detail = {"code": "http_error", "message": raw.decode("utf-8", "replace")[:500]}
        raise ServiceError(exc.code, detail) from None


class ServiceError(Exception):
    def __init__(self, status: int, detail: dict):
        err = detail.get("error", detail) if isinstance(detail, dict) else {"message": str(detail)}
        super().__init__(f"{status} {err.get('code')}: {err.get('message')}")
        self.status = status
        self.detail = err
