"""Controlled subprocesses for the trusted toolchain.

Every external tool the evaluator runs (the OCaml compiler, the Lean checker, the bridge
scripts) goes through `run_limited`: an argument LIST (never a shell string built from
user text), a working directory, a wall-clock timeout, an optional memory ceiling, a
cancellation event, and a kill that takes the whole process tree -- so a timed-out or
cancelled job does not leave a 16 GB Lean process running behind it.

Windows: a Job Object with `KILL_ON_JOB_CLOSE` and a per-process memory limit.
Linux: a new session (`killpg`) and `RLIMIT_AS` in the child.
macOS: a new session, and the memory ceiling as a watchdog on the process group's
physical footprint.  Darwin does not enforce `RLIMIT_AS`, and refuses to set it below the
address space a process has already reserved (every limit under ~1 TB), so a `preexec_fn`
that tried would stop every tool from starting.
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
    watch_mb = None
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | 0x08000000  # CREATE_NO_WINDOW
    else:
        kw["start_new_session"] = True
        if mem_mb and sys.platform == "darwin":
            watch_mb = int(mem_mb)
        elif mem_mb:
            limit = _posix_as_limit(int(mem_mb))

            def _limit():  # pragma: no cover - runs in the child
                import resource
                resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
            kw["preexec_fn"] = _limit
    try:
        proc = subprocess.Popen(argv, **kw)
    except (OSError, subprocess.SubprocessError) as exc:
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
        if watch_mb is not None:
            used = _darwin_footprint_mb(proc.pid)
            if used is not None and used > watch_mb:
                _kill(proc, job)
                err.append(f"\nstopped: the process used {used} MB, over its {watch_mb} MB "
                           f"memory limit\n".encode())
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


def _posix_as_limit(mem_mb: int) -> int:
    """The address-space limit, in bytes, for a child asked to stay under `mem_mb`.  A limit
    inherited from an outer sandbox (the official grader runs each job under one) can only
    be lowered, never raised, so a larger request is clamped to it: asking for more would
    make the child fail to start at all."""
    import resource
    want = int(mem_mb) * 1024 * 1024
    _soft, hard = resource.getrlimit(resource.RLIMIT_AS)
    return want if hard == resource.RLIM_INFINITY else min(want, hard)


_LIBPROC = None


def _darwin_footprint_mb(pgid: int) -> int | None:
    """The physical footprint (what Activity Monitor calls Memory) of every process in the
    group, in MB; None when it cannot be read, which never kills anything."""
    global _LIBPROC
    try:
        import ctypes
        if _LIBPROC is None:
            lib = ctypes.CDLL("/usr/lib/libproc.dylib")
            lib.proc_listpgrppids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
            lib.proc_pid_rusage.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p]
            _LIBPROC = lib
        pids = (ctypes.c_int * 256)()
        n = _LIBPROC.proc_listpgrppids(pgid, pids, ctypes.sizeof(pids))
        total = 0
        info = (ctypes.c_uint64 * 32)()     # rusage_info_v0; ri_phys_footprint is word 9
        for pid in pids[:max(0, min(n, 256))] or [pgid]:
            if pid > 0 and _LIBPROC.proc_pid_rusage(pid, 0, info) == 0:
                total += info[9]
        return total >> 20
    except Exception:
        return None


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
