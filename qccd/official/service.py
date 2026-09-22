"""The official submission service: application layer + HTTP API.

Trust boundaries, stated as code:

* TASKS come from the server's own release directory (`releases_dir`), verified against
  their pinned digests at start-up.  A bundle naming a task or digest the server does not
  hold is refused; a client cannot supply a task.
* UPLOADS need a bearer token whose SHA-256 is in `uploaders` (and not disabled), within
  the uploader's daily quota.  With no uploader configured the upload path FAILS CLOSED.
  A development token exists only when `QCCD_OFFICIAL_DEV=1`, it must be given explicitly
  (`QCCD_OFFICIAL_DEV_TOKEN`), and it is accepted only from loopback.
* A BUNDLE is data: the archive is size-limited and safely extracted (no absolute paths,
  `..`, links, duplicates, zip bombs), re-hashed file by file, and stored content-addressed.
  Nothing in it is executed.  The client's local report, verdicts or scores are not
  accepted at all; the profile is always `reference`.
* An upload returns an id immediately; grading happens in the worker/grader, never in the
  request.  The same uploader re-submitting the same bundle for the same task gets the same
  submission back (idempotent).
* REPORTS are append-only: a regrade under a new evaluator policy adds a report and marks
  the old one superseded; the old one is kept.  A leaderboard is always for one
  (task release, evaluator policy) pair.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import tempfile
import time
from pathlib import Path

from ..workspace.bundle import BundleError, extract_archive
from ..workspace.jsonsafe import digest
from ..workspace.tasks import TaskRelease
from .db import Database

try:  # the HTTP layer is optional; the service class works without it (worker, tests)
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
except ImportError:  # pragma: no cover
    FastAPI = Request = JSONResponse = None

__all__ = ["OfficialService", "OfficialError", "create_app"]

MAX_ARCHIVE = 64 * 1024 * 1024


class OfficialError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _now() -> float:
    return time.time()


class OfficialService:
    def __init__(self, db_url: str, releases_dir: Path, artifacts_dir: Path, *,
                 evaluator_policy: str | None = None):
        from ..workspace import EVALUATOR_VERSION
        self.db = Database(db_url)
        self.releases_dir = Path(releases_dir)
        self.artifacts = Path(artifacts_dir)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.evaluator_policy = evaluator_policy or f"reference-{EVALUATOR_VERSION}"
        self.releases: dict = {}
        for d in sorted(self.releases_dir.iterdir()):
            if (d / "release.json").is_file():
                rel = TaskRelease.load(d)           # verifies pinned file digests
                self.releases[rel.id] = rel
                self.db.execute("INSERT INTO releases(id, digest, path, active, evaluator_policy, created_at) "
                                "VALUES(?,?,?,1,?,?) ON CONFLICT(id) DO UPDATE SET digest=excluded.digest, "
                                "path=excluded.path, evaluator_policy=excluded.evaluator_policy",
                                (rel.id, rel.digest, str(d), self.evaluator_policy, _now()))

    # ------------------------------------------------------------------ uploaders

    def create_uploader(self, name: str, quota_per_day: int = 50) -> str:
        token = "qccd_" + secrets.token_urlsafe(32)
        self.db.execute("INSERT INTO uploaders(id, name, token_sha256, quota_per_day, created_at) VALUES(?,?,?,?,?)",
                        ("u_" + secrets.token_hex(6), name[:80], hashlib.sha256(token.encode()).hexdigest(),
                         int(quota_per_day), _now()))
        return token

    def authenticate(self, token: str | None, client_host: str | None) -> dict:
        if not token:
            raise OfficialError("unauthorized", "an upload token is required", 401)
        dev = os.environ.get("QCCD_OFFICIAL_DEV") == "1"
        dev_token = os.environ.get("QCCD_OFFICIAL_DEV_TOKEN")
        if dev and dev_token and secrets.compare_digest(token, dev_token):
            if client_host not in ("127.0.0.1", "::1", "localhost"):
                raise OfficialError("unauthorized", "the development token is accepted only from loopback", 401)
            row = self.db.one("SELECT * FROM uploaders WHERE name='development'")
            if row is None:
                self.db.execute("INSERT INTO uploaders(id, name, token_sha256, quota_per_day, created_at) "
                                "VALUES(?,?,?,?,?)", ("u_dev", "development",
                                                      hashlib.sha256(dev_token.encode()).hexdigest(), 1000, _now()))
                row = self.db.one("SELECT * FROM uploaders WHERE name='development'")
            return row
        row = self.db.one("SELECT * FROM uploaders WHERE token_sha256=? AND disabled=0",
                          (hashlib.sha256(token.encode()).hexdigest(),))
        if row is None:
            raise OfficialError("unauthorized", "unknown or disabled upload token", 401)
        return row

    # ------------------------------------------------------------------ submissions

    def submit(self, uploader: dict, archive: bytes, *, task: str, visibility: str = "public",
               display_name: str = "") -> dict:
        if visibility not in ("public", "unlisted", "private"):
            raise OfficialError("bad_request", "visibility must be public, unlisted or private", 422)
        if len(archive) > MAX_ARCHIVE:
            raise OfficialError("too_large", "the archive is larger than 64 MB", 413)
        rel = self.releases.get(task)
        if rel is None:
            raise OfficialError("unknown_task", f"this server grades {sorted(self.releases)}, not {task!r}", 404)
        day = self.db.one("SELECT COUNT(*) AS n FROM submissions WHERE uploader=? AND created_at>?",
                          (uploader["id"], _now() - 86400))
        if day and day["n"] >= uploader["quota_per_day"]:
            raise OfficialError("quota", "daily submission quota reached", 429)
        with tempfile.TemporaryDirectory() as td:
            try:
                b = extract_archive(archive, Path(td) / "bundle")
            except BundleError as exc:
                raise OfficialError(f"bundle_{exc.code}", str(exc), 422) from None
            if b.manifest["task"]["id"] != rel.id or b.manifest["task"]["digest"] != rel.digest:
                raise OfficialError("wrong_task", f"the bundle is for {b.manifest['task']['id']} "
                                    f"({b.manifest['task']['digest'][:19]}...), not this server's {rel.id}", 422)
            bundle_digest = b.digest
        prior = self.db.one("SELECT * FROM submissions WHERE uploader=? AND task=? AND bundle_digest=?",
                            (uploader["id"], rel.id, bundle_digest))
        if prior:
            return {**self._public(prior), "duplicate": True}
        arch_sha = hashlib.sha256(archive).hexdigest()
        path = self.artifacts / f"{arch_sha}.zip"
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(archive)
            os.replace(tmp, path)
        sid = "os_" + secrets.token_hex(8)
        with self.db.tx() as db:
            db.execute("INSERT INTO submissions(id, uploader, task, task_digest, bundle_digest, archive_sha256, "
                       "visibility, display_name, status, created_at) VALUES(?,?,?,?,?,?,?,?, 'queued', ?)",
                       (sid, uploader["id"], rel.id, rel.digest, bundle_digest, arch_sha, visibility,
                        display_name[:80], _now()))
            db.execute("INSERT INTO jobs(id, submission, status, attempts, created_at) VALUES(?,?, 'queued', 0, ?)",
                       ("oj_" + secrets.token_hex(8), sid, _now()))
        return self._public(self.db.one("SELECT * FROM submissions WHERE id=?", (sid,)))

    def _public(self, row: dict) -> dict:
        return {"submission_id": row["id"], "task": row["task"], "bundle_digest": row["bundle_digest"],
                "status": row["status"], "visibility": row["visibility"], "display_name": row["display_name"],
                "created_at": row["created_at"]}

    def submission(self, sid: str, uploader: dict | None) -> dict:
        row = self.db.one("SELECT * FROM submissions WHERE id=?", (sid,))
        if row is None or (row["visibility"] == "private" and (uploader is None or uploader["id"] != row["uploader"])):
            raise OfficialError("not_found", "no such submission", 404)
        out = self._public(row)
        rep = self.db.one("SELECT * FROM reports WHERE submission=? AND superseded_by IS NULL "
                          "ORDER BY created_at DESC LIMIT 1", (sid,))
        if rep:
            r = json.loads(rep["report"])
            out["report"] = {"report_digest": rep["report_digest"], "evaluator_policy": rep["evaluator_policy"],
                             "eligible": bool(rep["eligible"]), "eligibility": r.get("eligibility"),
                             "metrics": {k: v.get("value") for k, v in (r.get("metrics") or {}).items()},
                             "stages": {s["id"]: s["status"] for s in r.get("stages", [])}}
        job = self.db.one("SELECT status, attempts, error FROM jobs WHERE submission=? ORDER BY created_at DESC LIMIT 1",
                          (sid,))
        out["job"] = job
        return out

    def report(self, sid: str, uploader: dict | None) -> dict:
        self.submission(sid, uploader)
        rep = self.db.one("SELECT report FROM reports WHERE submission=? AND superseded_by IS NULL "
                          "ORDER BY created_at DESC LIMIT 1", (sid,))
        if rep is None:
            raise OfficialError("not_ready", "the submission has not been graded yet", 409)
        return json.loads(rep["report"])

    def record_report(self, sid: str, report: dict) -> None:
        rel = self.releases[self.db.one("SELECT task FROM submissions WHERE id=?", (sid,))["task"]]
        key = rel.manifest.get("rank_by")
        val = ((report.get("metrics") or {}).get(key) or {}).get("value")
        rid = "or_" + secrets.token_hex(8)
        eligible = bool((report.get("eligibility") or {}).get("eligible"))
        with self.db.tx() as db:
            db.execute("UPDATE reports SET superseded_by=? WHERE submission=? AND superseded_by IS NULL", (rid, sid))
            db.execute("INSERT INTO reports(id, submission, evaluator_version, evaluator_policy, report_digest, report, "
                       "eligible, rank_value, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                       (rid, sid, (report.get("evaluator") or {}).get("version", "?"), self.evaluator_policy,
                        digest(report), json.dumps(report, sort_keys=True), 1 if eligible else 0,
                        float(val) if isinstance(val, (int, float)) else None, _now()))
            db.execute("UPDATE submissions SET status=? WHERE id=?", ("eligible" if eligible else "ineligible", sid))

    def leaderboard(self, task: str) -> dict:
        rel = self.releases.get(task)
        if rel is None:
            raise OfficialError("unknown_task", f"no task {task!r}", 404)
        key = rel.manifest.get("rank_by")
        better = next((m.get("better") for m in rel.manifest.get("metrics", []) if m["name"] == key), "low")
        rows = self.db.all("SELECT s.id, s.display_name, s.bundle_digest, s.created_at, r.rank_value, r.report_digest "
                           "FROM submissions s JOIN reports r ON r.submission=s.id "
                           "WHERE s.task=? AND s.visibility='public' AND r.superseded_by IS NULL AND r.eligible=1 "
                           "AND r.evaluator_policy=?", (task, self.evaluator_policy))
        rows.sort(key=lambda r: (r["rank_value"] if better == "low" else -(r["rank_value"] or 0)))
        return {"task": task, "task_digest": rel.digest, "evaluator_policy": self.evaluator_policy,
                "rank_by": key, "better": better, "rows": rows}

    def tasks(self) -> list:
        return [r.summary() for r in self.releases.values()]

    def regrade(self, task: str) -> int:
        """Queue every submission of `task` again (e.g. after an evaluator policy change).
        Old reports stay; they are superseded only when the new report is recorded."""
        n = 0
        for s in self.db.all("SELECT id FROM submissions WHERE task=?", (task,)):
            self.db.execute("INSERT INTO jobs(id, submission, status, attempts, created_at) VALUES(?,?, 'queued', 0, ?)",
                            ("oj_" + secrets.token_hex(8), s["id"], _now()))
            n += 1
        return n


def create_app(svc: OfficialService):
    # FastAPI resolves the `Request` annotations below against THIS module's globals
    # (annotations are strings here), so the import above must stay at module level.
    if FastAPI is None:
        raise RuntimeError("the official HTTP API needs fastapi (pip install .[official])")

    app = FastAPI(title="QCCD official submissions", docs_url=None, redoc_url=None, openapi_url=None)

    def err(e: OfficialError):
        return JSONResponse({"error": {"code": e.code, "message": str(e)}}, status_code=e.status)

    def who(request: Request, required: bool) -> dict | None:
        auth = request.headers.get("authorization", "")
        token = auth[7:] if auth.startswith("Bearer ") else None
        if not token and not required:
            return None
        return svc.authenticate(token, request.client.host if request.client else None)

    @app.get("/v1/health")
    async def health():
        return {"ok": True, "tasks": sorted(svc.releases), "evaluator_policy": svc.evaluator_policy}

    @app.get("/v1/tasks")
    async def tasks():
        return {"tasks": svc.tasks()}

    @app.post("/v1/submissions")
    async def submit(request: Request):
        try:
            up = who(request, True)
            cl = request.headers.get("content-length")
            if cl and cl.isdigit() and int(cl) > MAX_ARCHIVE:
                raise OfficialError("too_large", "the archive is larger than 64 MB", 413)
            data = await request.body()
            out = svc.submit(up, data, task=request.headers.get("x-qccd-task", ""),
                             visibility=request.headers.get("x-qccd-visibility", "public"),
                             display_name=request.headers.get("x-qccd-display-name", ""))
            return JSONResponse(out, status_code=200 if out.get("duplicate") else 202)
        except OfficialError as e:
            return err(e)

    @app.get("/v1/submissions/{sid}")
    async def status(sid: str, request: Request):
        try:
            return svc.submission(sid, who(request, False))
        except OfficialError as e:
            return err(e)

    @app.get("/v1/submissions/{sid}/report")
    async def report(sid: str, request: Request):
        try:
            return svc.report(sid, who(request, False))
        except OfficialError as e:
            return err(e)

    @app.get("/v1/leaderboard/{task}")
    async def board(task: str):
        try:
            return svc.leaderboard(task)
        except OfficialError as e:
            return err(e)

    return app


def main(argv=None) -> int:
    import argparse
    import uvicorn
    ap = argparse.ArgumentParser(prog="python -m qccd.official.service")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("serve", "add-uploader", "regrade"):
        p = sub.add_parser(n)
        p.add_argument("--db", default=os.environ.get("QCCD_OFFICIAL_DB", "sqlite:///official/official.db"))
        p.add_argument("--releases", default=os.environ.get("QCCD_OFFICIAL_RELEASES",
                                                              str(Path(__file__).resolve().parents[1] / "workspace" / "releases")))
        p.add_argument("--artifacts", default=os.environ.get("QCCD_OFFICIAL_ARTIFACTS", "official/artifacts"))
        if n == "serve":
            p.add_argument("--host", default="127.0.0.1")
            p.add_argument("--port", type=int, default=8300)
        if n == "add-uploader":
            p.add_argument("name")
            p.add_argument("--quota", type=int, default=50)
        if n == "regrade":
            p.add_argument("task")
    a = ap.parse_args(argv)
    svc = OfficialService(a.db, Path(a.releases), Path(a.artifacts))
    if a.cmd == "add-uploader":
        print(svc.create_uploader(a.name, a.quota))
        return 0
    if a.cmd == "regrade":
        print(f"queued {svc.regrade(a.task)} regrade job(s)")
        return 0
    if os.environ.get("QCCD_OFFICIAL_DEV") == "1" and a.host not in ("127.0.0.1", "localhost"):
        print("refusing: development mode binds to loopback only", flush=True)
        return 2
    uvicorn.run(create_app(svc), host=a.host, port=a.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
