"""Generated results: jobs, immutable snapshots, local submissions, publication approval.

A LOCAL SUBMISSION freezes the exact inputs before any job starts: the device the
workspace replays at that revision, the final program, the certified program and the
certificate are written as a canonical bundle into `.qccd/snapshots/<id>/bundle/`,
made read-only, and named by the bundle digest.  Grading reads only that directory, so
editing the workspace afterwards cannot change the run's artifacts or its report -- the
report is written next to the bundle, once, and its digest recorded.

Jobs return an id at once and run in the service's worker threads; every external tool
they run is a controlled subprocess (`procs.run_limited`) with a timeout and a kill that
takes the whole tree.  A job whose process is gone after a restart is marked
`internal_error` and not rerun -- rerunning uncertain work could duplicate effects.

PUBLICATION needs a person.  `prepare_publish` (any actor) shows the exact bundle;
`approve_publish` (a person only) binds an approval to the bundle digest AND the
publication parameters; `publish` re-hashes the bundle, checks both, and uploads -- a
changed bundle or changed visibility needs a new approval.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Mapping

from .bundle import BundleError, archive_bundle, read_bundle, write_bundle
from .jsonsafe import canonical_bytes, digest, strict_loads
from .store import dumps, loads

__all__ = ["ResultsMixin", "JOB_KINDS"]

JOB_KINDS = ("compile", "evaluate", "run")
TERMINAL = ("succeeded", "failed", "cancelled", "timeout", "internal_error")


def _now() -> float:
    return time.time()


class ResultsMixin:

    def _results_init(self) -> None:
        self._pool = ThreadPoolExecutor(max_workers=int(os.environ.get("QCCD_JOB_WORKERS", "2")),
                                        thread_name_prefix="qccd-job")
        self._cancel_events: dict = {}
        self.snapshots_dir = self.root / ".qccd" / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ jobs

    def job(self, job_id: str) -> dict:
        from .core import WorkspaceError
        r = self.store.one("SELECT * FROM jobs WHERE id=?", (job_id,))
        if r is None:
            raise WorkspaceError("unknown_job", f"no job {job_id!r}", status=404)
        d = dict(r)
        for k in ("progress", "result", "actor"):
            d[k] = loads(d[k])
        d["cancel_requested"] = bool(d["cancel_requested"])
        return d

    def active_jobs(self) -> list:
        return [_job_brief(self.job(r["id"])) for r in
                self.store.all("SELECT id FROM jobs WHERE status IN ('queued','running') ORDER BY created_at")]

    def jobs(self, limit: int = 50) -> list:
        return [_job_brief(self.job(r["id"])) for r in
                self.store.all("SELECT id FROM jobs ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),))]

    def start_job(self, actor: Mapping, kind: str, params: Mapping, *, request_id: str | None = None,
                  origin_prompt_id: str | None = None) -> dict:
        """Create a job and return at once.  `request_id` makes a retry return the same job."""
        from .core import WorkspaceError, new_id
        if kind not in JOB_KINDS:
            raise WorkspaceError("bad_request", f"kind must be one of {JOB_KINDS}", status=422)
        self._check_actor_may_write(actor, origin_prompt_id)
        key = f"{actor.get('session') or actor.get('id')}:{request_id}" if request_id else None
        if key:
            prior = self.store.one("SELECT id FROM jobs WHERE idempotency_key=?", (key,))
            if prior:
                out = _job_brief(self.job(prior["id"]))
                out["duplicate"] = True
                return out
        params = dict(params or {})
        branch = self.resolve_draft(params.get("branch") or params.get("draft") or "main")
        params.pop("draft", None)
        b = self.branch(branch)
        rev = params.get("revision", b["head"])
        if not isinstance(rev, int) or not 0 <= rev <= b["head"]:
            raise WorkspaceError("bad_request", f"revision {rev!r} is not a revision of {branch}", status=422)
        params["branch"], params["revision"] = branch, rev
        if kind == "evaluate":
            prof = params.get("profile", "draft")
            if prof not in ("draft", "reference"):
                raise WorkspaceError("bad_request", "profile must be draft or reference", status=422)
            params["profile"] = prof
        if kind == "run":
            self._check_run_params(params)
        jid = new_id("job")
        limit = float(params.get("timeout_s") or (self.release.manifest.get("limits") or {}).get("lean_timeout_s", 1800))
        with self.store.tx() as db:
            db.execute("INSERT INTO jobs(id, kind, snapshot_id, profile, status, progress, idempotency_key, "
                       "origin_prompt_id, actor, created_at, deadline, result) VALUES(?,?,?,?, 'queued', ?, ?, ?, ?, ?, ?, ?)",
                       (jid, kind, params.get("snapshot_id"), params.get("profile"),
                        dumps({"stage": "queued", "message": "waiting for a worker"}), key, origin_prompt_id,
                        dumps(dict(actor)), _now(), _now() + limit + 600, dumps({"params": params})))
            self._emit(db, "job.updated", {"job_id": jid, "kind": kind, "status": "queued",
                                           "origin_prompt_id": origin_prompt_id}, branch, rev)
            if origin_prompt_id:
                self._link_prompt(db, origin_prompt_id, "job", jid, actor)
            else:
                working = self._working_prompt(db, actor)
                if working:
                    self._link_prompt(db, working, "job", jid, actor)
        ev = threading.Event()
        self._cancel_events[jid] = ev
        self._pool.submit(self._run_job, jid, kind, params, dict(actor), origin_prompt_id, ev)
        return _job_brief(self.job(jid))

    def cancel_job(self, actor: Mapping, job_id: str) -> dict:
        """Request cancellation and report what actually happened: a queued job is
        cancelled at once, a running one when its current subprocess is killed, and a
        finished one cannot be."""
        j = self.job(job_id)
        if j["status"] in TERMINAL:
            return {"job_id": job_id, "outcome": "already_finished", "status": j["status"]}
        with self.store.tx() as db:
            db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
            if j["status"] == "queued":
                db.execute("UPDATE jobs SET status='cancelled', finished_at=? WHERE id=? AND status='queued'",
                           (_now(), job_id))
            self._emit(db, "job.updated", {"job_id": job_id, "status": "cancelling" if j["status"] == "running"
                                           else "cancelled", "by": dict(actor)})
        ev = self._cancel_events.get(job_id)
        if ev:
            ev.set()
        return {"job_id": job_id, "outcome": "cancel_requested" if j["status"] == "running" else "cancelled",
                "status": self.job(job_id)["status"]}

    def _progress(self, job_id: str, stage: str, message: str) -> None:
        with self.store.tx() as db:
            db.execute("UPDATE jobs SET progress=? WHERE id=?",
                       (dumps({"stage": stage, "message": message[:300], "at": _now()}), job_id))
            self._emit(db, "job.updated", {"job_id": job_id, "status": "running", "stage": stage,
                                           "message": message[:300]})

    def _run_job(self, jid, kind, params, actor, origin_prompt_id, cancel: threading.Event) -> None:
        with self.store.tx() as db:
            cur = db.execute("UPDATE jobs SET status='running', started_at=?, pid=?, owner='service' "
                             "WHERE id=? AND status='queued'", (_now(), os.getpid(), jid))
            if cur.rowcount != 1:
                return
            self._emit(db, "job.updated", {"job_id": jid, "status": "running", "kind": kind})
        watcher = threading.Thread(target=self._watch_cancel, args=(jid, cancel), daemon=True)
        watcher.start()
        status, result, error = "internal_error", None, None
        try:
            if kind == "compile":
                result = self._job_compile(jid, params, actor, origin_prompt_id, cancel)
            elif kind == "run":
                result = self._job_run(jid, params, actor, origin_prompt_id, cancel)
            else:
                result = self._job_evaluate(jid, params, actor, origin_prompt_id, cancel)
            status = result.pop("_status", "succeeded")
        except Exception as exc:  # recorded, never swallowed
            error = f"{type(exc).__name__}: {exc}"
            result = {"traceback": traceback.format_exc()[-4000:]}
            code = getattr(exc, "code", None)
            status = "failed" if code else "internal_error"
        if cancel.is_set() and status not in ("succeeded",):
            status = "cancelled"
        with self.store.tx() as db:
            db.execute("UPDATE jobs SET status=?, result=?, error=?, finished_at=? WHERE id=?",
                       (status, dumps(result), error, _now(), jid))
            self._emit(db, "job.updated", {"job_id": jid, "status": status, "kind": kind,
                                           "error": (error or "")[:300],
                                           "summary": (result or {}).get("summary"),
                                           "origin_prompt_id": origin_prompt_id})
        self._cancel_events.pop(jid, None)

    def _watch_cancel(self, jid: str, ev: threading.Event) -> None:
        while not ev.is_set() and not getattr(self, "_closed", False):
            try:
                r = self.store.one("SELECT status, cancel_requested FROM jobs WHERE id=?", (jid,))
            except Exception:          # the workspace was closed under us: nothing left to watch
                return
            if r is None or r["status"] in TERMINAL:
                return
            if r["cancel_requested"]:
                ev.set()
                return
            time.sleep(0.5)

    # ------------------------------------------------------------------ compile

    def _job_compile(self, jid, params, actor, origin_prompt_id, cancel) -> dict:
        """Compile the task circuit for the design at one revision, with the REAL
        toolchain: export -> qccdc_cli compile -> insert_cooling.  The artifacts are
        stored by digest; adopting them into the design is a separate change set
        (`set_final_program`), so compiling never edits the design behind anyone's back."""
        from .core import WorkspaceError
        from .evaluator import Toolchain
        from .procs import run_limited
        tc = Toolchain.discover()
        if tc.qccdc is None:
            raise WorkspaceError("toolchain_missing", "the compiler (qccdc_cli) is not installed: run "
                                 "`qccd toolchain install`, or build Compiler/ocaml", status=424)
        branch, rev = params["branch"], params["revision"]
        r = self.replayed(branch, rev)
        if not r.ok:
            raise WorkspaceError("design_invalid", "the design at that revision does not build", status=422)
        work = self.root / ".qccd" / "runs" / jid
        work.mkdir(parents=True, exist_ok=True)
        (work / "device.arch.json").write_text(json.dumps(r.arch_doc), encoding="utf-8")
        (work / "circuit.qasm").write_bytes(self.release.circuit_path.read_bytes())
        self._progress(jid, "export", "expanding the device for the compiler")
        ex = run_limited([tc.python, tc.bridge / "export_arch.py", work / "device.arch.json", "-o",
                          work / "device.expanded.json"], cwd=work, timeout=300, cancel=cancel)
        if not ex.ok:
            return {"_status": "cancelled" if ex.status == "cancelled" else "failed",
                    "summary": "export failed", "log": (ex.stderr or ex.stdout)[-2000:]}
        mode = params.get("compiler", "compile")
        if mode not in ("compile", "rotate"):
            raise WorkspaceError("bad_request", "compiler must be compile or rotate", status=422)
        self._progress(jid, "compile", f"qccdc_cli {mode}")
        cargs = [tc.qccdc, mode, work / "circuit.qasm", "--arch", work / "device.expanded.json", "-o", work / "prog"]
        cp = run_limited(cargs, cwd=work, timeout=float((self.release.manifest.get("limits") or {})
                                                        .get("compile_timeout_s", 600)), mem_mb=8192, cancel=cancel)
        log = (cp.stdout + "\n" + cp.stderr)[-6000:]
        if cp.status in ("cancelled", "timeout"):
            return {"_status": cp.status, "summary": f"compile {cp.status}", "log": log}
        if not cp.ok or not (work / "prog.tsir.json").exists():
            return {"_status": "failed", "summary": f"the compiler refused (exit {cp.returncode})", "log": log,
                    "refusal": _compiler_reason(log)}
        cert = json.loads((work / "prog.qcert.json").read_text(encoding="utf-8"))
        if cert.get("unrealised"):
            return {"_status": "failed", "summary": f"{len(cert['unrealised'])} circuit ops unrealised: "
                    "a partial program is a refusal", "log": log}
        self._progress(jid, "cooling", "inserting cooling (R7)")
        co = run_limited([tc.python, tc.bridge / "insert_cooling.py", work / "prog.tsir.json", "--arch",
                          work / "device.arch.json", "-o", work / "prog.cooled.tsir.json"],
                         cwd=work, timeout=600, cancel=cancel)
        if not (work / "prog.cooled.tsir.json").exists():
            return {"_status": "cancelled" if co.status == "cancelled" else "failed",
                    "summary": "cooling insertion failed", "log": (co.stdout + co.stderr)[-2000:]}
        raw = (work / "prog.tsir.json").read_bytes()
        cooled = (work / "prog.cooled.tsir.json").read_bytes()
        certb = (work / "prog.qcert.json").read_bytes()
        d_raw = self.put_artifact(canonical_bytes(strict_loads(raw)), "application/json", "certified program")
        d_cool = self.put_artifact(canonical_bytes(strict_loads(cooled)), "application/json", "final program (cooled)")
        d_cert = self.put_artifact(canonical_bytes(strict_loads(certb)), "application/json", "certificate")
        n_instr = len(strict_loads(cooled).get("instructions", []))
        final_ref = {"artifact": d_cool, "certified": d_raw, "certificate": d_cert,
                     "compiled_for": r.arch_digest(), "compiler": mode,
                     "label": f"qccdc {mode}, r{rev}, {n_instr} instructions"}
        return {"summary": f"compiled at r{rev}: {n_instr} instructions (cooled); certificate stored",
                "final_program": final_ref, "log": log[-2000:], "cooling_log": (co.stdout + co.stderr)[-1000:],
                "adopt_with": {"type": "set_final_program", **final_ref}}

    # ------------------------------------------------------------------ snapshots

    def freeze_snapshot(self, branch: str, revision: int, actor: Mapping, *, title: str = "") -> dict:
        """Write the immutable bundle for one revision.  Refuses a design with no
        compiled final program: the official track grades artifacts, not intentions."""
        from .core import WorkspaceError, new_id
        rev = self.revision(branch, revision)
        r = self.replayed(branch, revision)
        if not r.ok:
            raise WorkspaceError("design_invalid", f"{branch}@r{revision} does not build: "
                                 f"{(r.problems or [{}])[0].get('message')}", status=422)
        fp = rev.state.final_program
        if not fp or not fp.get("artifact"):
            raise WorkspaceError("no_final_program", "the design has no final hardware program: run a compile "
                                 "job and adopt its result (set_final_program) first", status=409)
        prog = strict_loads(self.get_artifact(fp["artifact"]))
        certified = strict_loads(self.get_artifact(fp["certified"])) if fp.get("certified") else None
        cert = strict_loads(self.get_artifact(fp["certificate"])) if fp.get("certificate") else None
        sid = new_id("snap")
        d = self.snapshots_dir / sid
        d.mkdir(parents=True)
        prov = {"workspace_id": self.id, "branch": branch, "revision": revision,
                "input_digest": rev.input_digest, "arch_digest": r.arch_digest(),
                "compiled_for": fp.get("compiled_for"), "compiler": fp.get("compiler")}
        pres = {"title": title[:200], "studio": rev.state.to_studio(None)}
        b = write_bundle(d / "bundle", task_id=self.release.id, task_digest=self.release.digest,
                         device=r.arch_doc, program=prog, certified_program=certified, certificate=cert,
                         presentation=pres, provenance=prov)
        _freeze_dir(d / "bundle")
        with self.store.tx() as db:
            db.execute("INSERT INTO snapshots(id, branch, revision, input_digest, bundle_digest, dir, created_at) "
                       "VALUES(?,?,?,?,?,?,?)", (sid, branch, revision, rev.input_digest, b.digest,
                                                 str(d.relative_to(self.root)), _now()))
            self._emit(db, "snapshot.created", {"snapshot_id": sid, "bundle_digest": b.digest,
                                                "revision": revision, "actor": dict(actor)}, branch, revision)
        return {"snapshot_id": sid, "bundle_digest": b.digest, "branch": branch, "revision": revision,
                "files": b.manifest["files"], "stale_program": fp.get("compiled_for") not in (None, r.arch_digest())}

    def snapshot(self, sid: str) -> dict:
        from .core import WorkspaceError
        r = self.store.one("SELECT * FROM snapshots WHERE id=?", (sid,))
        if r is None:
            raise WorkspaceError("unknown_snapshot", f"no snapshot {sid!r}", status=404)
        return dict(r)

    # ------------------------------------------------------------------ evaluate / submit

    def submit_local(self, actor: Mapping, *, branch: str = "main", revision: int | None = None,
                     profile: str = "reference", origin_prompt_id: str | None = None,
                     request_id: str | None = None, title: str = "") -> dict:
        """Freeze, then grade with the shared reference evaluator.  Returns at once with
        the submission and job ids."""
        from .core import WorkspaceError, new_id
        self._check_actor_may_write(actor, origin_prompt_id)
        if request_id:
            prior = self.store.one("SELECT response FROM idempotency WHERE key=?", (f"submit:{request_id}",))
            if prior:
                out = loads(prior["response"])
                out["duplicate"] = True
                return out
        rev = self.branch(branch)["head"] if revision is None else revision
        snap = self.freeze_snapshot(branch, rev, actor, title=title)
        sub_id = new_id("sub")
        with self.store.tx() as db:
            db.execute("INSERT INTO submissions(id, snapshot_id, job_id, profile, task_release, status, "
                       "origin_prompt_id, actor, created_at) VALUES(?,?,NULL,?,?, 'grading', ?, ?, ?)",
                       (sub_id, snap["snapshot_id"], profile, self.release.id, origin_prompt_id,
                        dumps(dict(actor)), _now()))
        job = self.start_job(actor, "evaluate", {"branch": branch, "revision": rev, "snapshot_id": snap["snapshot_id"],
                                                 "profile": profile, "submission_id": sub_id},
                             origin_prompt_id=origin_prompt_id)
        with self.store.tx() as db:
            db.execute("UPDATE submissions SET job_id=? WHERE id=?", (job["job_id"], sub_id))
            out = {"submission_id": sub_id, "job_id": job["job_id"], "snapshot_id": snap["snapshot_id"],
                   "bundle_digest": snap["bundle_digest"], "revision": rev, "branch": branch,
                   "profile": profile, "status": "grading",
                   "label": "Local result - not published"}
            if request_id:
                db.execute("INSERT INTO idempotency(key, request_digest, response, created_at) VALUES(?,?,?,?)",
                           (f"submit:{request_id}", digest([branch, rev, profile]), dumps(out), _now()))
            self._emit(db, "submission.updated", {"submission_id": sub_id, "status": "grading",
                                                  "revision": rev, "bundle_digest": snap["bundle_digest"],
                                                  "origin_prompt_id": origin_prompt_id}, branch, rev)
            if origin_prompt_id:
                self._link_prompt(db, origin_prompt_id, "submission", sub_id, actor)
        return out

    def _job_evaluate(self, jid, params, actor, origin_prompt_id, cancel) -> dict:
        from .evaluator import grade
        profile = params.get("profile", "draft")
        sid = params.get("snapshot_id")
        tmp_snap = None
        if not sid:
            snap = self.freeze_snapshot(params["branch"], params["revision"], actor, title="validation")
            sid = snap["snapshot_id"]
            tmp_snap = sid
        s = self.snapshot(sid)
        d = self.root / s["dir"]
        run = {"job_id": jid, "snapshot_id": sid, "submission_id": params.get("submission_id")}
        report = grade(self.release, d / "bundle", profile, workdir=self.root / ".qccd" / "runs" / jid,
                       cancel=cancel, progress=lambda st, m: self._progress(jid, st, m), run=run)
        name = f"report.{profile}.{jid}.json"
        data = json.dumps(report, indent=1, sort_keys=True, ensure_ascii=False).encode("utf-8")
        path = d / name
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o444)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        rdig = digest(report)
        status = "cancelled" if any(st["status"] == "cancelled" for st in report["stages"]) else "succeeded"
        elig = report["eligibility"]["eligible"]
        if params.get("submission_id"):
            with self.store.tx() as db:
                db.execute("UPDATE submissions SET status=?, report_digest=? WHERE id=?",
                           ("cancelled" if status == "cancelled" else ("eligible" if elig else "ineligible"),
                            rdig, params["submission_id"]))
                self._emit(db, "submission.updated", {"submission_id": params["submission_id"],
                                                      "status": "eligible" if elig else "ineligible",
                                                      "report_digest": rdig,
                                                      "metrics": {k: v["value"] for k, v in report["metrics"].items()},
                                                      "origin_prompt_id": origin_prompt_id})
        summary = (f"{profile} grade of r{params['revision']}: "
                   + ("ELIGIBLE" if elig else "not eligible (" + "; ".join(report["eligibility"]["reasons"][:3]) + ")"))
        return {"_status": status, "summary": summary, "report_path": str(path.relative_to(self.root)),
                "report_digest": rdig, "eligible": elig, "snapshot_id": sid, "temporary_snapshot": bool(tmp_snap),
                "stages": {st["id"]: st["status"] for st in report["stages"]},
                "metrics": {k: v["value"] for k, v in report["metrics"].items()}}

    def submission(self, sub_id: str) -> dict:
        from .core import WorkspaceError
        r = self.store.one("SELECT * FROM submissions WHERE id=?", (sub_id,))
        if r is None:
            raise WorkspaceError("unknown_submission", f"no submission {sub_id!r}", status=404)
        d = dict(r)
        d["actor"] = loads(d["actor"])
        snap = self.snapshot(d["snapshot_id"])
        d["snapshot"] = snap
        d["report"] = self.report_for(sub_id)
        head = self.head(snap["branch"])
        d["stale"] = head.input_digest != snap["input_digest"]
        d["label"] = "Local result - not published"
        return d

    def report_for(self, sub_id: str) -> dict | None:
        r = self.store.one("SELECT * FROM submissions WHERE id=?", (sub_id,))
        if r is None or not r["job_id"]:
            return None
        j = self.job(r["job_id"])
        path = ((j.get("result") or {}) or {}).get("report_path")
        if not path:
            return None
        data = (self.root / path).read_bytes()
        rep = json.loads(data)
        if r["report_digest"] and digest(rep) != r["report_digest"]:
            from .core import WorkspaceError
            raise WorkspaceError("corrupt_report", f"{path} does not match its recorded digest", status=500)
        return rep

    def submissions(self, limit: int = 50) -> list:
        out = []
        for r in self.store.all("SELECT id FROM submissions ORDER BY created_at DESC LIMIT ?", (max(1, min(limit, 500)),)):
            s = self.submission(r["id"])
            rep = s["report"] or {}
            out.append({"submission_id": s["id"], "snapshot_id": s["snapshot_id"], "status": s["status"],
                        "profile": s["profile"],
                        "task_release": s["task_release"], "revision": s["snapshot"]["revision"],
                        "branch": s["snapshot"]["branch"], "bundle_digest": s["snapshot"]["bundle_digest"],
                        "stale": s["stale"], "eligible": (rep.get("eligibility") or {}).get("eligible"),
                        "metrics": {k: v.get("value") for k, v in (rep.get("metrics") or {}).items()},
                        "rank_by": rep.get("rank_by"), "evaluator": (rep.get("evaluator") or {}).get("version"),
                        "created_at": s["created_at"], "label": s["label"]})
        return out

    def leaderboard(self) -> dict:
        """The local leaderboard: this workspace's graded submissions, ranked by the
        release's metric, eligible first -- never mixed across task releases or
        evaluator versions."""
        rows = [s for s in self.submissions(500) if s["task_release"] == self.release.id]
        key = self.release.manifest.get("rank_by")
        better = next((m.get("better") for m in self.release.manifest.get("metrics", []) if m["name"] == key), "low")
        def sortkey(s):
            v = s["metrics"].get(key)
            v = float("inf") if v is None else (v if better == "low" else -v)
            return (0 if s["eligible"] else 1, v)
        rows.sort(key=sortkey)
        return {"task": self.release.id, "rank_by": key, "better": better, "rows": rows,
                "label": "Local results - not published"}

    def latest_result(self, branch: str) -> dict | None:
        r = self.store.one("SELECT s.id FROM submissions s JOIN snapshots n ON n.id=s.snapshot_id "
                           "WHERE n.branch=? ORDER BY s.created_at DESC LIMIT 1", (branch,))
        if r is None:
            return None
        s = self.submission(r["id"])
        rep = s["report"] or {}
        return {"submission_id": s["id"], "status": s["status"], "revision": s["snapshot"]["revision"],
                "stale": s["stale"], "eligible": (rep.get("eligibility") or {}).get("eligible"),
                "metrics": {k: v.get("value") for k, v in (rep.get("metrics") or {}).items()},
                "label": s["label"]}

    # ------------------------------------------------------------------ publication

    def prepare_publish(self, actor: Mapping, submission_id: str, *, visibility: str = "public",
                        display_name: str = "") -> dict:
        """The review of exactly what would be uploaded.  Creates nothing."""
        from .core import WorkspaceError
        if visibility not in ("public", "unlisted", "private"):
            raise WorkspaceError("bad_request", "visibility must be public, unlisted or private", status=422)
        s = self.submission(submission_id)
        b = read_bundle(self.root / s["snapshot"]["dir"] / "bundle")
        if b.digest != s["snapshot"]["bundle_digest"]:
            raise WorkspaceError("bundle_changed", "the snapshot's bundle no longer matches its digest", status=409)
        params = {"visibility": visibility, "display_name": display_name[:80], "task": self.release.id}
        rep = s["report"] or {}
        return {"submission_id": submission_id, "task": {"id": self.release.id, "digest": self.release.digest},
                "bundle_digest": b.digest, "params": params, "params_digest": digest(params),
                "files": [{"role": k, "path": v["path"], "digest": v["digest"], "bytes": v["bytes"]}
                          for k, v in b.manifest["files"].items()],
                "local_report": {"eligible": (rep.get("eligibility") or {}).get("eligible"),
                                 "reasons": (rep.get("eligibility") or {}).get("reasons"),
                                 "metrics": {k: v.get("value") for k, v in (rep.get("metrics") or {}).items()},
                                 "profile": rep.get("profile")},
                "excluded": ["optimizer source", "agent conversations", "the workspace database",
                             "credentials", "unrelated files"],
                "requires": "a person approves this exact digest and these parameters in Studio or with "
                            "`qccd publish --approve` at a terminal; an agent cannot approve"}

    def approve_publish(self, actor: Mapping, submission_id: str, bundle_digest: str, params: Mapping) -> dict:
        from .core import WorkspaceError, new_id
        if actor.get("kind") != "human":
            raise WorkspaceError("forbidden", "only a person can approve a publication", status=403)
        review = self.prepare_publish(actor, submission_id, visibility=params.get("visibility", "public"),
                                      display_name=params.get("display_name", ""))
        if review["bundle_digest"] != bundle_digest:
            raise WorkspaceError("digest_mismatch", "the approved digest is not this submission's bundle", status=409)
        extra = set(params) - {"visibility", "display_name"}
        if extra:
            raise WorkspaceError("bad_request", f"unknown publication parameters {sorted(extra)}", status=422)
        aid = new_id("ap")
        with self.store.tx() as db:
            db.execute("INSERT INTO approvals(id, submission_id, bundle_digest, params_digest, params, approved_by, "
                       "created_at) VALUES(?,?,?,?,?,?,?)", (aid, submission_id, bundle_digest,
                                                             review["params_digest"], dumps(review["params"]),
                                                             dumps(dict(actor)), _now()))
            self._emit(db, "publication.approved", {"approval_id": aid, "submission_id": submission_id,
                                                    "bundle_digest": bundle_digest, "params": review["params"]})
        return {"approval_id": aid, "bundle_digest": bundle_digest, "params": review["params"],
                "params_digest": review["params_digest"]}

    def publish(self, approval_id: str, uploader) -> dict:
        """Upload an approved bundle.  `uploader(archive_bytes, meta) -> dict` is the
        credentialed transport (never available to graders or to agents)."""
        from .core import WorkspaceError
        r = self.store.one("SELECT * FROM approvals WHERE id=?", (approval_id,))
        if r is None:
            raise WorkspaceError("unknown_approval", f"no approval {approval_id!r}", status=404)
        if r["used_at"]:
            raise WorkspaceError("approval_used", "this approval was already used; approve again to resubmit",
                                 status=409)
        s = self.submission(r["submission_id"])
        b = read_bundle(self.root / s["snapshot"]["dir"] / "bundle")
        if b.digest != r["bundle_digest"]:
            raise WorkspaceError("digest_mismatch", "the bundle on disk is not the one that was approved", status=409)
        params = loads(r["params"])
        if digest(params) != r["params_digest"]:
            raise WorkspaceError("params_mismatch", "approval parameters were altered", status=409)
        archive = archive_bundle(b)
        resp = uploader(archive, {"bundle_digest": b.digest, "task": s["task_release"], **params})
        with self.store.tx() as db:
            db.execute("UPDATE approvals SET used_at=?, publication=? WHERE id=? AND used_at IS NULL",
                       (_now(), dumps(resp), approval_id))
            self._emit(db, "publication.submitted", {"approval_id": approval_id, "submission_id": s["id"],
                                                     "bundle_digest": b.digest, "response": resp})
        return {"approval_id": approval_id, "bundle_digest": b.digest, "server": resp}


def _job_brief(j: Mapping) -> dict:
    res = j.get("result") or {}
    return {"job_id": j["id"], "kind": j["kind"], "status": j["status"], "progress": j.get("progress"),
            "summary": res.get("summary"), "origin_prompt_id": j.get("origin_prompt_id"),
            "created_at": j["created_at"], "started_at": j.get("started_at"), "finished_at": j.get("finished_at"),
            "error": j.get("error"), "cancel_requested": j.get("cancel_requested")}


def _compiler_reason(log: str) -> str:
    import re
    m = re.search(r"(unroutable[^\n]*|Unroutable[^\n]*|unrealised[^\n]*|too small[^\n]*|cannot[^\n]*|exception[^\n]*)", log)
    return (m.group(1) if m else log[-300:]).strip()


def _freeze_dir(d: Path) -> None:
    for p in d.iterdir():
        try:
            os.chmod(p, stat.S_IREAD | stat.S_IRGRP | stat.S_IROTH)
        except OSError:
            pass
