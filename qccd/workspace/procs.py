"""Controlled subprocesses for the trusted toolchain.

Every external tool the evaluator runs (the OCaml compiler, the Lean checker, the bridge
scripts) goes through `run_limited`: an argument LIST (never a shell string built from
user text), a working directory, a wall-clock timeout, an optional memory ceiling, a
cancellation event, and a kill that takes the whole process tree -- so a timed-out or
cancelled job does not leave a 16 GB Lean process running behind it.

Windows: a Job Object with `KILL_ON_JOB_CLOSE` and a per-process memory limit.
POSIX: a new session (`killpg`) and `RLIMIT_AS` in the child.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

__all__ = ["RunResult", "run_limited"]


@dataclass
class RunResult:
    status: str            # ok | nonzero | timeout | cancelled | error
    returncode: int | None
    stdout: str
    stderr: str
    seconds: float

    @property
    def ok(self) -> bool:
        return self.status == "ok"


_TAIL = 200_000


def run_limited(args: Sequence, *, cwd: Path | None = None, timeout: float = 600.0,
                mem_mb: int | None = None, cancel: threading.Event | None = None,
                env: dict | None = None, on_start: Callable[[int], None] | None = None) -> RunResult:
    argv = [str(a) for a in args]
    t0 = time.time()
    kw: dict = dict(cwd=str(cwd) if cwd else None, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    stdin=subprocess.DEVNULL, env=env)
    job = None
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000  # CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
        if mem_mb:
            def _limit():  # pragma: no cover - runs in the child
                import resource
                b = int(mem_mb) * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_AS, (b, b))
            kw["preexec_fn"] = _limit
    try:
        proc = subprocess.Popen(argv, **kw)
    except OSError as exc:
        return RunResult("error", None, "", f"{type(exc).__name__}: {exc}", time.time() - t0)
    if os.name == "nt":
        job = _win_job(proc.pid, mem_mb)
    if on_start:
        try:
            on_start(proc.pid)
        except Exception:
            pass
    out: list = []
    err: list = []
    t_out = threading.Thread(target=_drain, args=(proc.stdout, out), daemon=True)
    t_err = threading.Thread(target=_drain, args=(proc.stderr, err), daemon=True)
    t_out.start()
    t_err.start()
    status = None
    while True:
        try:
            proc.wait(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel.is_set():
            status = "cancelled"
            _kill(proc, job)
            break
        if time.time() - t0 > timeout:
            status = "timeout"
            _kill(proc, job)
            break
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill(proc, job)
    t_out.join(timeout=5)
    t_err.join(timeout=5)
    if job is not None:
        _win_close(job)
    so = b"".join(out).decode("utf-8", "replace")[-_TAIL:]
    se = b"".join(err).decode("utf-8", "replace")[-_TAIL:]
    if status is None:
        status = "ok" if proc.returncode == 0 else "nonzero"
    return RunResult(status, proc.returncode, so, se, time.time() - t0)


def _drain(stream, sink: list) -> None:
    try:
        for chunk in iter(lambda: stream.read(65536), b""):
            sink.append(chunk)
            if sum(len(c) for c in sink) > 4 * _TAIL:
                del sink[: len(sink) // 2]
    except Exception:
        pass


def _kill(proc, job) -> None:
    try:
        if os.name == "nt":
            if job is not None:
                import ctypes
                ctypes.windll.kernel32.TerminateJobObject(job, 1)
            else:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                               capture_output=True, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


# ---------------------------------------------------------------- Windows job objects

def _win_job(pid: int, mem_mb: int | None):  # pragma: no cover - Windows only
    try:
        import ctypes
        from ctypes import wintypes

        k32 = ctypes.windll.kernel32

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in (
                "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
                "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

        class BASIC(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong),
                        ("PerJobUserTimeLimit", ctypes.c_longlong),
                        ("LimitFlags", wintypes.DWORD),
                        ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t),
                        ("ActiveProcessLimit", wintypes.DWORD),
                        ("Affinity", ctypes.c_size_t),
                        ("PriorityClass", wintypes.DWORD),
                        ("SchedulingClass", wintypes.DWORD)]

        class EXTENDED(ctypes.Structure):
            _fields_ = [("BasicLimitInformation", BASIC), ("IoInfo", IO_COUNTERS),
                        ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t),
                        ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]

        k32.CreateJobObjectW.restype = wintypes.HANDLE
        k32.OpenProcess.restype = wintypes.HANDLE
        job = k32.CreateJobObjectW(None, None)
        if not job:
            return None
        info = EXTENDED()
        flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if mem_mb:
            flags |= 0x0100 | 0x0200  # PROCESS_MEMORY | JOB_MEMORY
            info.ProcessMemoryLimit = int(mem_mb) * 1024 * 1024
            info.JobMemoryLimit = int(mem_mb) * 1024 * 1024
        info.BasicLimitInformation.LimitFlags = flags
        k32.SetInformationJobObject(job, 9, ctypes.byref(info), ctypes.sizeof(info))
        h = k32.OpenProcess(0x1F0FFF, False, pid)
        if h:
            k32.AssignProcessToJobObject(job, h)
            k32.CloseHandle(h)
        return job
    except Exception:
        return None


def _win_close(job) -> None:  # pragma: no cover - Windows only
    try:
        import ctypes
        ctypes.windll.kernel32.CloseHandle(job)
    except Exception:
        pass


def python_exe() -> str:
    return sys.executable
