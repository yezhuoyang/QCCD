"""The worker (queue -> spool) and the grader (spool -> report), and the one-shot grade.

    python -m qccd.official.worker worker --db ... --spool DIR [--inline]
    python -m qccd.official.worker grader --spool DIR --releases DIR
    python -m qccd.official.worker one JOBDIR --releases DIR           (what the grader runs per job)

SPOOL PROTOCOL (a directory both sides share; the grader needs nothing else):

    inbox/<job>/bundle/...        the extracted, re-hashed bundle      (worker writes)
    inbox/<job>/job.json          {"job", "release"} -- release is an ID the grader resolves
                                  in ITS OWN read-only release directory
    inbox/<job>/READY             written last; the grader ignores a job without it
    work/<job>/                   the grader claimed it (atomic rename)
    outbox/<job>/report.json      the result, then outbox/<job>/DONE

In the Compose deployment the grader is a separate container with `network_mode: none`,
no credentials, a read-only root file system and the spool as its only writable mount; the
worker holds the database connection and never runs the toolchain.  `--inline` (for a
local development server without containers) runs the same one-shot grade as a
resource-limited subprocess of the worker instead -- weaker isolation, stated.

A job whose worker died (no heartbeat for `STALE_S`) is put back in the queue.  Grading is
a pure function of (bundle, release, evaluator), so a repeated grade cannot duplicate any
effect: the report of the last successful attempt is the one recorded.
"""

from __future__ import annotations

import json
import os
import secrets
import shutil
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

__all__ = ["run_worker", "run_grader", "grade_one", "STALE_S"]

STALE_S = 900
MAX_ATTEMPTS = 3


def _now() -> float:
    return time.time()


# ---------------------------------------------------------------------- the grader side

def grade_one(job_dir: Path, releases: Path) -> int:
    """Grade one job directory in THIS process and write report.json next to it."""
    from ..workspace.evaluator import grade
    from ..workspace.tasks import TaskRelease
    job = json.loads((job_dir / "job.json").read_text(encoding="utf-8"))
    rid = str(job["release"])
    if "/" in rid or "\\" in rid or ".." in rid:
        raise SystemExit("bad release id")
    rel = TaskRelease.load(Path(releases) / rid)
    with tempfile.TemporaryDirectory() as td:
        rep = grade(rel, job_dir / "bundle", "reference", workdir=Path(td),
                    run={"job_id": job["job"], "grader": socket.gethostname()})
    (job_dir / "report.json").write_text(json.dumps(rep, sort_keys=True), encoding="utf-8")
    return 0


def _grade_subprocess(job_dir: Path, releases: Path, timeout: float) -> str:
    """Run `grade_one` in a limited child with a minimal environment (no credentials)."""
    from ..workspace.procs import run_limited
    env = {k: v for k, v in os.environ.items()
           if k in ("PATH", "SYSTEMROOT", "PYTHONPATH", "QCCD_QCCDC", "QCCD_QCHECK", "TEMP", "TMP", "HOME",
                    "USERPROFILE", "LOCALAPPDATA", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS")}
    r = run_limited([sys.executable, "-m", "qccd.official.worker", "one", str(job_dir), "--releases", str(releases)],
                    timeout=timeout, mem_mb=int(os.environ.get("QCCD_GRADER_MEM_MB", "16384")), env=env)
    if r.status == "ok" and (job_dir / "report.json").exists():
        return "ok"
    (job_dir / "grader.log").write_text((r.stdout + "\n" + r.stderr)[-20000:], encoding="utf-8")
    return r.status


def run_grader(spool: Path, releases: Path, *, once: bool = False, timeout: float = 3600.0) -> None:
    spool = Path(spool)
    for d in ("inbox", "work", "outbox"):
        (spool / d).mkdir(parents=True, exist_ok=True)
    while True:
        did = False
        for job in sorted((spool / "inbox").iterdir()):
            if not (job / "READY").exists():
                continue
            work = spool / "work" / job.name
            try:
                os.rename(job, work)                       # atomic claim
            except OSError:
                continue
            status = _grade_subprocess(work, releases, timeout)
            out = spool / "outbox" / job.name
            os.rename(work, out)
            (out / "DONE").write_text(status, encoding="utf-8")
            did = True
        if once:
            return
        if not did:
            time.sleep(1.0)


# ---------------------------------------------------------------------- the worker side

def _claim(svc) -> dict | None:
    db = svc.db
    with db.tx() as t:
        row = t.one(db.claim_sql())
        if row is None:
            return None
        t.execute("UPDATE jobs SET status='running', attempts=attempts+1, locked_by=?, locked_at=?, heartbeat=? "
                  "WHERE id=? AND status='queued'", (f"{socket.gethostname()}:{os.getpid()}", _now(), _now(), row["id"]))
    return row


def _requeue_stale(svc) -> int:
    n = 0
    for j in svc.db.all("SELECT id, attempts FROM jobs WHERE status='running' AND heartbeat<?", (_now() - STALE_S,)):
        svc.db.execute("UPDATE jobs SET status=?, locked_by=NULL, error=? WHERE id=?",
                       ("queued" if j["attempts"] < MAX_ATTEMPTS else "failed",
                        "the worker holding this job stopped", j["id"]))
        n += 1
    return n


def process_one(svc, spool: Path, *, inline: bool, releases: Path, timeout: float = 3600.0) -> bool:
    import zipfile
    from ..workspace.bundle import extract_archive
    _requeue_stale(svc)
    job = _claim(svc)
    if job is None:
        return False
    sub = svc.db.one("SELECT * FROM submissions WHERE id=?", (job["submission"],))
    jdir = Path(spool) / "inbox" / job["id"]
    if jdir.exists():
        shutil.rmtree(jdir)
    jdir.mkdir(parents=True)
    try:
        extract_archive((svc.artifacts / f"{sub['archive_sha256']}.zip").read_bytes(), jdir / "bundle")
    except Exception as exc:
        svc.db.execute("UPDATE jobs SET status='failed', error=?, finished_at=? WHERE id=?",
                       (f"bundle could not be staged: {exc}", _now(), job["id"]))
        return True
    (jdir / "job.json").write_text(json.dumps({"job": job["id"], "release": sub["task"]}), encoding="utf-8")
    (jdir / "READY").write_text("", encoding="utf-8")
    if inline:
        run_grader(spool, releases, once=True, timeout=timeout)
    out = Path(spool) / "outbox" / job["id"]
    t0 = _now()
    while not (out / "DONE").exists():
        if _now() - t0 > timeout + 120:
            svc.db.execute("UPDATE jobs SET status='queued', error='grader timed out' WHERE id=?", (job["id"],))
            return True
        svc.db.execute("UPDATE jobs SET heartbeat=? WHERE id=?", (_now(), job["id"]))
        time.sleep(1.0)
    status = (out / "DONE").read_text(encoding="utf-8").strip()
    if status == "ok":
        report = json.loads((out / "report.json").read_text(encoding="utf-8"))
        svc.record_report(sub["id"], report)
        svc.db.execute("UPDATE jobs SET status='succeeded', finished_at=?, error=NULL WHERE id=?", (_now(), job["id"]))
    else:
        log = (out / "grader.log").read_text(encoding="utf-8")[-2000:] if (out / "grader.log").exists() else ""
        final = job["attempts"] + 1 >= MAX_ATTEMPTS
        svc.db.execute("UPDATE jobs SET status=?, error=?, finished_at=? WHERE id=?",
                       ("failed" if final else "queued", f"grader {status}: {log}", _now(), job["id"]))
        if final:
            svc.db.execute("UPDATE submissions SET status='grading_failed' WHERE id=?", (sub["id"],))
    shutil.rmtree(out, ignore_errors=True)
    return True


def run_worker(svc, spool: Path, *, inline: bool, releases: Path, once: bool = False) -> None:
    while True:
        did = process_one(svc, spool, inline=inline, releases=releases)
        if once:
            return
        if not did:
            time.sleep(1.0)


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="python -m qccd.official.worker")
    sub = ap.add_subparsers(dest="cmd", required=True)
    rel_default = os.environ.get("QCCD_OFFICIAL_RELEASES",
                                 str(Path(__file__).resolve().parents[1] / "workspace" / "releases"))
    p = sub.add_parser("worker")
    p.add_argument("--db", default=os.environ.get("QCCD_OFFICIAL_DB", "sqlite:///official/official.db"))
    p.add_argument("--artifacts", default=os.environ.get("QCCD_OFFICIAL_ARTIFACTS", "official/artifacts"))
    p.add_argument("--spool", default=os.environ.get("QCCD_OFFICIAL_SPOOL", "official/spool"))
    p.add_argument("--releases", default=rel_default)
    p.add_argument("--inline", action="store_true")
    p.add_argument("--once", action="store_true")
    p = sub.add_parser("grader")
    p.add_argument("--spool", default=os.environ.get("QCCD_OFFICIAL_SPOOL", "/spool"))
    p.add_argument("--releases", default=rel_default)
    p = sub.add_parser("one")
    p.add_argument("job_dir")
    p.add_argument("--releases", default=rel_default)
    a = ap.parse_args(argv)
    if a.cmd == "one":
        return grade_one(Path(a.job_dir), Path(a.releases))
    if a.cmd == "grader":
        run_grader(Path(a.spool), Path(a.releases))
        return 0
    from .service import OfficialService
    svc = OfficialService(a.db, Path(a.releases), Path(a.artifacts))
    run_worker(svc, Path(a.spool), inline=a.inline, releases=Path(a.releases), once=a.once)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
