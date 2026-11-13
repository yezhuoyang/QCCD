"""Docker for an agent review: the reviewer image, a sign-in's terminal, and a review's own network,
egress proxy and container.

    image     qccd-reviewer:<digest of image/>, built on this computer the first time a sign-in or a
              review needs it (`ensure_image`): Node with the two CLIs at the versions image/Dockerfile
              pins, Python for the debugger, `script`, and the egress proxy
    sign-in   the image with `script` giving the CLI a terminal of its own (`signin_command`), so the
              same sign-in works from a page, from a terminal, and on Windows
    network   a review's own Docker network, --internal: nothing on it has a route out
    egress    a proxy container on that network and on the default one: CONNECT goes through to the
              agent's own vendor only (agents.REVIEWERS[...]["hosts"], plus QCCD_REVIEW_EGRESS_EXTRA),
              port 443 only; every request, let through or refused, is logged, and the review keeps
              the hosts (`Isolated.egress`)
    agent     a read-only root, no capabilities, no new privileges, pid, memory and CPU limits, the
              person's own uid; /job (the review's folder) and /profile (the CLI's home: a sign-in
              refreshed mid-review is kept) read-write, and qccd's own Python read-only for the
              debugger.  Claude's token goes in by name (CLAUDE_CODE_OAUTH_TOKEN), never in an argv.

Every container and network carries the label qccd.review=1 and the pid of the process that made
it: what a process that died left behind is removed (`sweep`), never another live one's.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

from . import agents

__all__ = ["IMAGE_DIR", "docker_bin", "ready", "image_tag", "image_built", "ensure_image", "remove", "sweep",
           "remove_own", "signin_command", "Isolated", "no_window"]

IMAGE_DIR = Path(__file__).resolve().with_name("image")
#: qccd's own Python, mounted read-only in a review's container for the debugger
QCCD_PACKAGE = Path(__file__).resolve().parents[1]
LABEL = "qccd.review=1"
OWNER = "qccd.review.pid"
PROXY_PORT = 3128

_READY: dict = {}
_BUILD = threading.Lock()


def no_window() -> dict:
    """Popen's keywords for a child with no console window on Windows (nothing elsewhere)."""
    return {"creationflags": 0x08000000} if os.name == "nt" else {}


def docker_bin() -> str | None:
    """The docker CLI: QCCD_DOCKER, the PATH, or Docker Desktop's own (which a service started by a
    desktop app does not have on its PATH)."""
    v = os.environ.get("QCCD_DOCKER")
    if v:
        return v if v != "none" and Path(v).is_file() else None
    found = shutil.which("docker")
    if found:
        return found
    cands = ["/usr/local/bin/docker", "/opt/homebrew/bin/docker", "/Applications/Docker.app/Contents/Resources/bin/docker"]
    if os.name == "nt":
        cands = [str(Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Docker" / "Docker" / "resources" / "bin"
                     / "docker.exe")]
    return next((c for c in cands if Path(c).is_file()), None)


def _client_env() -> dict:
    """The docker client's environment: what it needs to find the daemon, nothing of the caller's
    (no workspace token, no key)."""
    keep = ("PATH", "HOME", "USER", "USERPROFILE", "LANG", "TMPDIR", "TEMP", "TMP", "SYSTEMROOT", "APPDATA",
            "LOCALAPPDATA", "PROGRAMDATA", "ProgramFiles", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG",
            "DOCKER_CERT_PATH", "DOCKER_TLS_VERIFY")
    env = {k: v for k, v in os.environ.items() if k in keep}
    # Docker Desktop's credential helpers live beside its CLI, which a desktop app's PATH lacks
    d = docker_bin()
    if d:
        env["PATH"] = os.pathsep.join([str(Path(d).parent), env.get("PATH", "")])
    return env


def _docker(*args: str, timeout: float = 60, check: bool = True) -> subprocess.CompletedProcess:
    d = docker_bin()
    if not d:
        raise RuntimeError("Docker is not installed")
    p = subprocess.run([d, *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
                       env=_client_env(), stdin=subprocess.DEVNULL, **no_window())
    if check and p.returncode != 0:
        raise RuntimeError(f"docker {args[0]} failed: {(p.stderr or p.stdout).strip()[-600:]}")
    return p


def ready(*, fresh: bool = False) -> tuple[bool, str]:
    """(Docker can run a review now, and if not, what the person does), cached for a few seconds."""
    hit = _READY.get("v")
    if hit and not fresh and time.time() - hit[0] < 10.0:
        return hit[1]
    if not docker_bin():
        out = (False, "Docker is not installed: install Docker Desktop (or the docker engine), then try again")
    else:
        try:
            p = _docker("version", "--format", "{{.Server.Version}}", timeout=15, check=False)
            out = (True, "") if p.returncode == 0 and p.stdout.strip() else \
                (False, "Docker is not running: start Docker Desktop (or the docker service), then try again")
        except (OSError, subprocess.SubprocessError) as exc:
            out = (False, f"could not reach Docker: {exc}")
    _READY["v"] = (time.time(), out)
    return out


def _cpus() -> str:
    """At most two CPUs for a review, and no more than Docker's machine has (it refuses more)."""
    hit = _READY.get("cpus")
    if hit is None:
        try:
            n = int((_docker("info", "--format", "{{.NCPU}}", check=False, timeout=15).stdout or "").strip() or 2)
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError):
            n = 2
        hit = _READY["cpus"] = str(max(1, min(2, n)))
    return hit


def image_tag() -> str:
    """qccd-reviewer:<the digest of image/>: a change to the image's recipe is a new image."""
    h = hashlib.sha256()
    for p in sorted(IMAGE_DIR.iterdir()):
        if p.is_file():
            h.update(p.name.encode() + b"\0" + p.read_bytes() + b"\0")
    return f"qccd-reviewer:{h.hexdigest()[:12]}"


def image_built() -> bool:
    try:
        return _docker("image", "inspect", image_tag(), check=False, timeout=30).returncode == 0
    except (OSError, subprocess.SubprocessError, RuntimeError):
        return False


def ensure_image(say=None) -> str:
    """The reviewer image, built now if this computer does not have it (a few minutes, once)."""
    tag = image_tag()
    with _BUILD:
        if image_built():
            return tag
        if say:
            say([f"building the reviewer image {tag} (once, a few minutes)"])
        proc = subprocess.Popen([docker_bin(), "build", "--label", LABEL, "-t", tag, str(IMAGE_DIR)],
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                errors="replace", env=_client_env(), stdin=subprocess.DEVNULL, **no_window())
        tail: list[str] = []
        for ln in proc.stdout:
            tail.append(ln.rstrip())
            del tail[:-40]
            if say and ln.strip().startswith(("#", "Step")):
                say([f"building the reviewer image: {ln.strip()[:160]}"])
        if proc.wait() != 0:
            raise RuntimeError("building the reviewer image failed: " + " | ".join(tail[-8:])[-800:])
        return tag


def remove(name: str) -> None:
    """Remove container `name` if it is there (quietly: it may have gone by itself)."""
    try:
        _docker("rm", "-f", name, check=False, timeout=30)
    except Exception:
        pass


def _owner() -> str:
    return f"{OWNER}={os.getpid()}"


def _alive(pid: int) -> bool:
    if os.name == "nt":
        try:
            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True,
                                 timeout=10, **no_window()).stdout
        except (OSError, subprocess.SubprocessError):
            return True
        return str(pid) in out
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:                     # someone else's process: alive
        return True
    return True


def sweep() -> list[str]:
    """Remove what a review or a sign-in left behind when the process that ran it died before
    tearing it down: never another live process's.  The containers go first: a network in use
    cannot be removed."""
    gone = []
    for listing, field, rm in ((("ps", "-a"), "{{.Names}}", ("rm", "-f")),
                               (("network", "ls"), "{{.Name}}", ("network", "rm"))):
        p = _docker(*listing, "--filter", "label=" + LABEL, "--format", field + '\t{{.Label "' + OWNER + '"}}',
                    check=False, timeout=30)
        for line in (p.stdout or "").splitlines():
            name, _, pid = line.partition("\t")
            if name and pid.strip().isdigit() and not _alive(int(pid)):
                _docker(*rm, name, check=False, timeout=30)
                gone.append(name)
    return gone


def remove_own() -> list[str]:
    """Remove what this process's reviews and sign-ins are running: the service is stopping, and
    nothing of it should outlive it."""
    gone = []
    if not docker_bin():
        return gone
    for listing, field, rm in ((("ps", "-a"), "{{.Names}}", ("rm", "-f")), (("network", "ls"), "{{.Name}}", ("network", "rm"))):
        try:
            p = _docker(*listing, "--filter", "label=" + LABEL, "--filter", "label=" + _owner(), "--format", field,
                        check=False, timeout=15)
            for name in (p.stdout or "").split():
                _docker(*rm, name, check=False, timeout=30)
                gone.append(name)
        except (OSError, subprocess.SubprocessError, RuntimeError):
            pass
    return gone


def _run_as() -> list[str]:
    """The person's own uid in the container, so what it writes in their folders is theirs (Docker
    Desktop maps a bind mount's owner by itself on macOS and Windows)."""
    return ["--user", f"{os.getuid()}:{os.getgid()}"] if hasattr(os, "getuid") else []


def _mount(src: Path, dst: str, *, readonly: bool = False) -> list[str]:
    return ["--mount", f"type=bind,src={src},dst={dst}" + (",readonly" if readonly else "")]


#: a container's HOME is on its /tmp: made, then the command
_WITH_HOME = ["sh", "-c", 'mkdir -p "$HOME" && exec "$@"', "sh"]


def signin_command(rid: str, name: str, *, say=None) -> tuple[list[str], dict]:
    """The argv (and the docker client's environment) of `rid`'s own sign-in in the reviewer image:
    `script` gives the CLI a terminal, wide enough that a link is printed in one piece, and
    stdin -- the code a person pastes -- reaches it as typing.  Codex writes its sign-in into its
    profile (mounted at /profile); Claude's token is read from what it prints (agents.SigninSession)."""
    ok, why = ready(fresh=True)
    if not ok:
        raise RuntimeError(why)
    image = ensure_image(say)
    sweep()
    if rid == "claude":
        mounts, envs, cmd = [], ["-e", "HOME=/tmp/home"], "claude setup-token"
    else:
        home = agents.private_dir(agents.home_dir("codex"))
        mounts = _mount(home, "/profile")
        envs = ["-e", "HOME=/tmp/home", "-e", "CODEX_HOME=/profile"]
        cmd = "codex login --device-auth -c 'cli_auth_credentials_store=\"file\"'"
    argv = [docker_bin(), "run", "--rm", "-i", "--name", name, "--label", LABEL, "--label", _owner(),
            "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--pids-limit", "256", "--memory", "1g",
            *_run_as(), *mounts, *envs, "-e", "TERM=xterm-256color", "-e", "DISABLE_AUTOUPDATER=1",
            image, *_WITH_HOME, "script", "-qfec", f"stty cols 2000 rows 60; exec {cmd}", "/dev/null"]
    return argv, _client_env()


def _hosts(rid: str) -> list[str]:
    extra = [h.strip().lower() for h in os.environ.get("QCCD_REVIEW_EGRESS_EXTRA", "").split(",") if h.strip()]
    return list(agents.REVIEWERS[rid]["hosts"]) + extra


def cli_argv(rid: str, prompt: str, model: str | None = None) -> list[str]:
    """The CLI's own command in the container, with nothing of the person's setup (no user config,
    rules, memories, MCP servers or hooks), its own sandbox off: the container is the sandbox.
    `model` left out: QCCD_REVIEW_<X>_MODEL, else the account's own default."""
    model = model or agents.model_of(rid)
    if rid == "claude":
        # /profile is Claude Code's own home (it reads its saved tool output back from there); the token
        # is never in it: it comes by name, in the environment
        settings = {"sandbox": {"enabled": False},
                    "permissions": {"allow": ["Read", "Glob", "Grep", "Bash", "Write(./out/**)", "Edit(./out/**)"],
                                    "deny": ["WebFetch", "WebSearch"]}}
        argv = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose", "--permission-mode", "dontAsk",
                "--setting-sources", "project", "--strict-mcp-config", "--no-session-persistence",
                "--tools", "Bash,Read,Glob,Grep,Write,Edit", "--settings", json.dumps(settings)]
        return argv + (["--model", model] if model else [])
    argv = ["codex", "exec", "--json", "--skip-git-repo-check", "--ephemeral", "--ignore-user-config", "--ignore-rules",
            "--cd", "/job", "--sandbox", "danger-full-access", "-c", 'approval_policy="never"',
            "-c", "features.memories=false", "-c", "memories.use_memories=false",
            "-c", "memories.generate_memories=false", "-c", 'cli_auth_credentials_store="file"',
            "-o", "/job/out/.last-message.md"]
    return argv + (["-m", model] if model else []) + [prompt]


class Isolated:
    """`with Isolated(rid, job) as box: argv, env = box.command(prompt)` -- the network and the egress
    proxy for one review, torn down (with the agent's container) on the way out."""

    def __init__(self, rid: str, job: Path, *, say=None, model: str | None = None):
        self.rid, self.job, self.say, self.model = rid, Path(job), say, model
        self.name = "qccd-rv-" + secrets.token_hex(5)
        self.net, self.proxy = self.name + "-net", self.name + "-egress"
        self.image: str | None = None
        self.egress: dict | None = None

    def __enter__(self) -> "Isolated":
        ok, why = ready(fresh=True)
        if not ok:
            raise RuntimeError(why)
        self.image = ensure_image(self.say)
        sweep()
        try:
            _docker("network", "create", "--internal", "--label", LABEL, "--label", _owner(), self.net)
            _docker("run", "-d", "--rm", "--name", self.proxy, "--label", LABEL, "--label", _owner(),
                    "--network", "bridge", "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
                    "--pids-limit", "64", "--memory", "128m", "--user", "65534:65534", self.image,
                    "python3", "/opt/qccd-review/egress_proxy.py", "--port", str(PROXY_PORT),
                    "--allow", ",".join(_hosts(self.rid)))
            _docker("network", "connect", "--alias", "egress", self.net, self.proxy)
        except Exception:
            self._teardown()
            raise
        return self

    def command(self, prompt: str) -> tuple[list[str], dict]:
        spec = agents.REVIEWERS[self.rid]
        home = agents.private_dir(agents.home_dir(self.rid))
        proxy = f"http://egress:{PROXY_PORT}"
        envs = {"HOME": "/tmp/home", "HTTPS_PROXY": proxy, "https_proxy": proxy, "HTTP_PROXY": proxy,
                "http_proxy": proxy, "NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1",
                "PYTHONPATH": "/opt/qccd", "PYTHONDONTWRITEBYTECODE": "1", spec["home_var"]: "/profile",
                "DISABLE_AUTOUPDATER": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
        client = _client_env()
        keyflag: list[str] = []
        if self.rid == "claude":
            # by name: its value is in the docker client's environment, never in an argv
            client["CLAUDE_CODE_OAUTH_TOKEN"] = agents.claude_token() or ""
            keyflag = ["-e", "CLAUDE_CODE_OAUTH_TOKEN"]
        argv = [docker_bin(), "run", "--rm", "--name", self.name, "--label", LABEL, "--label", _owner(),
                "--network", self.net, "--read-only", "--tmpfs", "/tmp:rw,exec,size=1g", "--cap-drop", "ALL",
                "--security-opt", "no-new-privileges", "--pids-limit", "512", "--memory", "4g", "--cpus", _cpus(),
                *_run_as(), "-w", "/job", *_mount(self.job, "/job"), *_mount(home, "/profile"),
                *_mount(QCCD_PACKAGE, "/opt/qccd/qccd", readonly=True),
                *[x for k, v in envs.items() for x in ("-e", f"{k}={v}")], *keyflag,
                self.image, *_WITH_HOME, *cli_argv(self.rid, prompt, self.model)]
        return argv, client

    def __exit__(self, *exc) -> bool:
        self._teardown()
        return False

    def _teardown(self) -> None:
        try:
            p = _docker("logs", self.proxy, check=False, timeout=20)
            allowed, refused = set(), set()
            for ln in (p.stdout or "").splitlines():
                try:
                    e = json.loads(ln)
                except ValueError:
                    continue
                (allowed if e.get("allowed") else refused).add(f"{e.get('host')}:{e.get('port')}")
            self.egress = {"allowed": sorted(allowed), "refused": sorted(refused)}
        except Exception:
            pass
        for args in (("rm", "-f", self.name), ("rm", "-f", self.proxy), ("network", "rm", self.net)):
            try:
                _docker(*args, check=False, timeout=30)
            except Exception:
                pass
