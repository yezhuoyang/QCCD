"""The workspace: one transactional design API for the human and the agent.

`Workspace` is the application layer.  It knows nothing about HTTP, MCP, browsers or any
agent provider: the local service (`http.py`) and the MCP adapter (`mcp_server.py`) are
thin translations into these methods, so a drag in Studio and an agent's tool call are
the same `apply_change_set` with a different `actor`.

A CHANGE SET is `{branch, expected_revision, request_id, origin_prompt_id?, mode, summary,
operations, rebase?}`.  It is validated whole -- compiled by `operations`, replayed by
`design.replay`, checked against protected entities -- before anything is written, and
committed in one SQLite transaction together with its event, so a crash leaves either the
old revision or the new one.  `mode: preview` runs everything except the write.

Concurrency is optimistic.  A change set names the revision it was made against; if the
branch has moved on, it is a CONFLICT that reports the intervening change sets and the
entities they touched -- unless the caller asked for `rebase: "if_disjoint"` and nothing
overlaps, in which case the operations are recompiled against the current head and
validated again.  Nothing is last-writer-wins.
"""

from __future__ import annotations

import copy
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from .design import (DesignRefused, DesignState, Replayed, base_from_arch, diff_designs,
                     empty_design, entities, entity_summary, replay, touched_entities)
from .jsonsafe import canonical_text, digest
from .operations import OperationError, compile_operations, describe_operations
from .store import Store, dumps, loads
from .tasks import DEFAULT_BOARD, LOCK_NAME, TaskRelease, find_release, physics_mismatch, read_lock, write_lock

__all__ = ["WorkspaceCore", "WorkspaceError", "new_id", "CONTRACT_VERSION"]

#: the version of the change-set / context / event contracts (schemas/*.json)
CONTRACT_VERSION = "1"

#: a small cache of replays keyed by (branch, revision)
_REPLAY_CACHE_SIZE = 64


class WorkspaceError(Exception):
    """A structured refusal: `code` is stable, `status` is an HTTP-ish class."""

    def __init__(self, code: str, message: str, *, status: int = 400, detail: Any = None):
        super().__init__(message)
        self.code = code
        self.status = status
        self.detail = detail

    def to_json(self) -> dict:
        out = {"code": self.code, "message": str(self)}
        if self.detail is not None:
            out["detail"] = self.detail
        return out


def new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"


def _now() -> float:
    return time.time()


def _norm_touch(key: str) -> str:
    return key.split("#", 1)[0]


def _overlap(a: Iterable[str], b: Iterable[str]) -> list:
    """Keys that collide.  `node:X#incident` collides with `node:X` and vice versa."""
    na = {_norm_touch(k) for k in a}
    nb = {_norm_touch(k) for k in b}
    return sorted(na & nb)


def _actor(actor: Mapping | None) -> dict:
    a = dict(actor or {})
    a.setdefault("kind", "system")
    a.setdefault("id", a["kind"])
    return {k: a[k] for k in ("kind", "id", "session", "label", "view", "via") if k in a}


@dataclass
class Revision:
    branch: str
    revision: int
    state: DesignState
    constraints: dict
    change_set_id: str | None
    input_digest: str
    arch_digest: str | None
    created_at: float


class WorkspaceCore:
    """One workspace directory: `qccd.lock.json`, `design/`, and `.qccd/`.

    The design half of the application layer; `app.Workspace` adds collaboration
    (prompts, sessions, delivery) and results (jobs, snapshots, submissions)."""

    DB = ".qccd/workspace.db"

    def __init__(self, root: Path, *, release_dirs: Path | None = None, recover: bool = False):
        """Open a workspace.  `recover=True` is for THE service process only (it holds
        `.qccd/service.lock`): recovery rewrites live state -- sessions, sending deliveries,
        running jobs -- and must never be run by a second opener (the CLI, a publish, a
        test) while a service is serving the same database."""
        self.root = Path(root).resolve()
        self.lock_doc = read_lock(self.root)
        self.id = self.lock_doc["workspace_id"]
        self.release_dirs = release_dirs
        # `release` is the workspace's DEFAULT board, used where a board is needed without being named
        # (its first device, the shared physics and cost tables); every submission names its own
        pinned = self.lock_doc.get("task")
        self.release = find_release(pinned["id"] if pinned else DEFAULT_BOARD, release_dirs)
        if pinned and self.release.digest != pinned["digest"]:
            raise WorkspaceError(
                "release_mismatch",
                f"{LOCK_NAME} pins {pinned['id']} at {pinned['digest']}, "
                f"but the installed release has digest {self.release.digest}", status=409)
        self.store = Store(self.root / self.DB)
        self._replays: dict = {}
        self._replay_order: list = []
        self._cond = threading.Condition()
        self._listeners: list = []
        self.artifacts_dir = self.root / ".qccd" / "artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)
        self._after_init()
        if recover:
            self.recover()

    # ------------------------------------------------------------------ lifecycle

    @classmethod
    def init(cls, root: Path, release_ref: str | None = None, *, name: str = "design",
             starter: bool = True, release_dirs: Path | None = None) -> "WorkspaceCore":
        """Create a workspace: lockfile, `.qccd/`, the first revision, the design mirror.

        A workspace is general -- any design, any board -- and needs no board to be made.
        `release_ref` is the old pinned form (a lockfile v1): it only sets the default board."""
        from ..api import Machine

        root = Path(root).resolve()
        root.mkdir(parents=True, exist_ok=True)
        if (root / LOCK_NAME).exists():
            raise WorkspaceError("exists", f"{root} already holds a workspace", status=409)
        release = find_release(release_ref or DEFAULT_BOARD, release_dirs)
        ws_id = new_id("ws")
        from . import evaluator_identity, schema_versions
        write_lock(root, release if release_ref else None, ws_id, evaluator=evaluator_identity(),
                   schemas=schema_versions())
        (root / ".qccd").mkdir(exist_ok=True)
        gi = root / ".gitignore"
        lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
        if ".qccd/" not in lines:
            gi.write_text("\n".join(lines + ["# QCCD local state: database, snapshots, runs, caches",
                                             ".qccd/"]) + "\n", encoding="utf-8")
        st = None
        start = release.manifest.get("starter")
        if starter and start:
            m = getattr(Machine, start["generator"])(**start.get("params", {}), name=start.get("name", name))
            st = base_from_arch(m.arch)
        if st is None:
            st = empty_design(name)
        ws = cls.__new__(cls)
        ws.root = root
        ws.lock_doc = read_lock(root)
        ws.id = ws_id
        ws.release = release
        ws.release_dirs = release_dirs
        ws.store = Store(root / cls.DB)
        ws._replays, ws._replay_order = {}, []
        ws._cond = threading.Condition()
        ws._listeners = []
        ws.artifacts_dir = root / ".qccd" / "artifacts"
        ws.artifacts_dir.mkdir(parents=True, exist_ok=True)
        ws._after_init()
        r = replay(st, strict=True)
        with ws.store.tx() as db:
            db.execute("INSERT INTO meta(key, value) VALUES('workspace_id', ?)", (ws_id,))
            db.execute("INSERT INTO branches(name, kind, head, status, created_at, created_by) "
                       "VALUES('main', 'main', 0, 'open', ?, 'init')", (_now(),))
            db.execute("INSERT INTO revisions(branch, revision, change_set_id, state, input_digest, "
                       "arch_digest, created_at) VALUES('main', 0, NULL, ?, ?, ?, ?)",
                       (dumps({"design": st.to_json(), "constraints": {}}), st.input_digest(),
                        r.arch_digest(), _now()))
            ws._emit(db, "workspace.created", {"default_board": release.id}, "main", 0)
        ws.write_mirror("main")
        return ws

    def close(self) -> None:
        self.store.close()

    def recover(self) -> None:
        """After a crash or restart: the mirror is rewritten from the database (the
        authoritative copy), deliveries caught mid-send become `uncertain`, and jobs that
        were running under a process that is gone are marked failed rather than left
        running forever."""
        with self.store.tx() as db:
            rows = db.execute("SELECT id FROM deliveries WHERE state='sending'").fetchall()
            for r in rows:
                db.execute("UPDATE deliveries SET state='uncertain', updated_at=?, "
                           "last_error='service restarted while sending' WHERE id=?", (_now(), r["id"]))
            jobs = db.execute("SELECT id, pid FROM jobs WHERE status IN ('running','queued')").fetchall()
            for j in jobs:
                if j["pid"] and _pid_alive(j["pid"]):
                    continue
                db.execute("UPDATE jobs SET status='internal_error', finished_at=?, "
                           "error='the job process ended while the service was down; it was not rerun' "
                           "WHERE id=?", (_now(), j["id"]))
            # nothing is connected to a service that just started; adapters re-announce
            # themselves on their next request (pull/channel) or reconnect (app server)
            db.execute("UPDATE sessions SET status='disconnected' WHERE status='connected'")
        try:
            self.write_mirror("main")
        except Exception:
            pass

    # ------------------------------------------------------------------ revisions

    def branch(self, name: str) -> dict:
        r = self.store.one("SELECT * FROM branches WHERE name=?", (name,))
        if r is None:
            raise WorkspaceError("unknown_branch", f"no branch {name!r}", status=404)
        return dict(r)

    def resolve_draft(self, name: str | None) -> str:
        """A design by what a person calls it: its title (any text they chose), `main`, or an
        internal name (`cand/A`, `A`).  A title two designs share is refused, naming them."""
        name = (name or "main").strip()
        for cand in (name, f"cand/{name}"):
            if self.store.one("SELECT 1 FROM branches WHERE name=?", (cand,)):
                return cand
        want = " ".join(name.split()).casefold()
        hits = [b["name"] for b in self.branches() if b.get("status", "open") == "open"
                and " ".join(self.design_title(b).split()).casefold() == want]
        if len(hits) == 1:
            return hits[0]
        have = [self.design_title(b) for b in self.branches() if b.get("status", "open") == "open"]
        if len(hits) > 1:
            raise WorkspaceError("ambiguous_design", f"{len(hits)} designs are called {name!r}; rename one", status=409)
        raise WorkspaceError("unknown_branch", f"no design {name!r}; there are: {', '.join(have)}", status=404)

    @staticmethod
    def design_title(b: Mapping) -> str:
        """What a person calls a design: the title they gave it, else the main design / its name."""
        if b.get("title"):
            return str(b["title"])
        return "Main design" if b["name"] == "main" else str(b["name"]).removeprefix("cand/")

    def rename_design(self, actor: Mapping, branch: str, title: str) -> dict:
        title = " ".join(str(title or "").split())
        if not title or len(title) > 120:
            raise WorkspaceError("bad_name", "a design's name is 1 to 120 characters", status=422)
        b = self.branch(branch)
        with self.store.tx() as db:
            db.execute("UPDATE branches SET title=? WHERE name=?", (title, b["name"]))
            self._emit(db, "branch.renamed", {"branch": b["name"], "title": title, "actor": _actor(actor)},
                       b["name"], b["head"])
        return self.branch(b["name"])

    def branches(self) -> list:
        return [dict(r) for r in self.store.all("SELECT * FROM branches ORDER BY created_at")]

    def head(self, branch: str = "main") -> Revision:
        b = self.branch(branch)
        return self.revision(branch, b["head"])

    def revision(self, branch: str, rev: int) -> Revision:
        r = self.store.one("SELECT * FROM revisions WHERE branch=? AND revision=?", (branch, rev))
        if r is None:
            raise WorkspaceError("unknown_revision", f"{branch} has no revision {rev}", status=404)
        body = loads(r["state"])
        return Revision(branch=branch, revision=rev, state=DesignState.from_json(body["design"]),
                        constraints=body.get("constraints") or {},
                        change_set_id=r["change_set_id"], input_digest=r["input_digest"],
                        arch_digest=r["arch_digest"], created_at=r["created_at"])

    def replayed(self, branch: str = "main", rev: int | None = None) -> Replayed:
        if rev is None:
            rev = self.branch(branch)["head"]
        key = (branch, rev)
        if key in self._replays:
            return self._replays[key]
        r = replay(self.revision(branch, rev).state)
        self._replays[key] = r
        self._replay_order.append(key)
        while len(self._replay_order) > _REPLAY_CACHE_SIZE:
            self._replays.pop(self._replay_order.pop(0), None)
        return r

    # ------------------------------------------------------------------ change sets

    def apply_change_set(self, req: Mapping, actor: Mapping) -> dict:
        actor = _actor(actor)
        if isinstance(req, Mapping) and isinstance(req.get("branch"), str) and not _branch_name_ok(req["branch"]):
            # a design by the name its person gave it, as every other door takes it (a live agent's
            # branch="Two triangles" was refused here, 2026-09-26, and cost it two model calls)
            req = {**req, "branch": self.resolve_draft(req["branch"])}
        req = _check_request(req, self.id)
        branch = req["branch"]
        b = self.branch(branch)
        if b["status"] != "open":
            raise WorkspaceError("branch_closed", f"branch {branch!r} is {b['status']}", status=409)
        self._check_actor_may_write(actor, req.get("origin_prompt_id"))
        self._check_mode(actor, req.get("origin_prompt_id"), branch, req["mode"])
        idem_key = None
        if req["mode"] == "apply":
            idem_key = f"cs:{actor.get('session') or actor['id']}:{req['request_id']}"
            req_digest = digest({k: v for k, v in req.items() if k not in ("mode",)})
            prior = self.store.one("SELECT * FROM idempotency WHERE key=?", (idem_key,))
            if prior is not None:
                if prior["request_digest"] != req_digest:
                    raise WorkspaceError(
                        "idempotency_conflict",
                        f"request_id {req['request_id']!r} was already used for a different change set",
                        status=409)
                out = loads(prior["response"])
                out["duplicate"] = True
                return out
        head_rev = b["head"]
        expected = req["expected_revision"]
        if expected > head_rev or expected < 0:
            raise WorkspaceError("bad_revision",
                                 f"expected_revision {expected} but {branch} is at {head_rev}", status=409)
        if expected < head_rev and req["rebase"] != "if_disjoint":
            # STALE FIRST: an operation may be invalid only against the old base (an id that
            # exists now, not then), and "refused" would send the agent down the wrong path.
            # The answer to a stale request is always "refresh and reconsider".
            intervening = self._change_sets_between(branch, expected, head_rev)
            raise WorkspaceError(
                "conflict",
                f"{branch} moved from revision {expected} to {head_rev}; refresh and reconsider "
                f"the change against the current design", status=409,
                detail={"head_revision": head_rev, "expected_revision": expected, "overlapping": [],
                        "intervening": [_cs_brief(cs) for cs in intervening]})
        base = self.revision(branch, expected)
        base_r = self.replayed(branch, expected)
        protect_ops, design_ops = _split_constraint_ops(req["operations"])
        constraints = copy.deepcopy(base.constraints)
        if design_ops:
            try:
                new_state = compile_operations(base.state, base_r, design_ops, actor)
            except OperationError as exc:
                raise WorkspaceError(exc.code, str(exc), status=422, detail=exc.to_json()) from None
            new_r = self._validated_replay(new_state, base_r)
        else:
            new_state, new_r = base.state.copy(), base_r
        replaces_base = any(isinstance(op, Mapping) and (
            op.get("type") == "construct" or (op.get("type") == "studio_sync" and op.get("base") is not None))
            for op in design_ops)
        diff = diff_designs(base_r, new_r)
        touched = touched_entities(diff, base_r, new_r)
        constraints, c_touched = _apply_constraint_ops(constraints, protect_ops, actor, new_r)
        touched |= c_touched
        if replaces_base:
            touched.add("device:base")
        rebased_from = None
        if expected < head_rev:
            intervening = self._change_sets_between(branch, expected, head_rev)
            other = set()
            for cs in intervening:
                other |= set(cs["touched"])
            clash = _overlap(touched, other)
            replaced = [cs["id"] for cs in intervening if "device:base" in cs["touched"]]
            if req["rebase"] != "if_disjoint" or clash or replaced or "device:base" in touched:
                raise WorkspaceError(
                    "conflict",
                    f"{branch} moved from revision {expected} to {head_rev}; refresh and reconsider "
                    f"the change against the current design",
                    status=409,
                    detail={"head_revision": head_rev, "expected_revision": expected,
                            "overlapping": clash or replaced,
                            "intervening": [_cs_brief(cs) for cs in intervening]})
            # disjoint: recompile the same operations against the current head
            head = self.revision(branch, head_rev)
            head_r = self.replayed(branch, head_rev)
            if design_ops:
                try:
                    new_state = compile_operations(head.state, head_r, design_ops, actor)
                except OperationError as exc:
                    raise WorkspaceError("conflict", f"the change no longer applies on revision "
                                         f"{head_rev}: {exc}", status=409,
                                         detail={"head_revision": head_rev,
                                                 "intervening": [_cs_brief(cs) for cs in intervening]}) from None
                new_r = self._validated_replay(new_state, head_r)
            else:
                new_state, new_r = head.state.copy(), head_r
            constraints, c_touched = _apply_constraint_ops(copy.deepcopy(head.constraints),
                                                           protect_ops, actor, new_r)
            diff = diff_designs(head_r, new_r)
            touched = touched_entities(diff, head_r, new_r) | c_touched
            if replaces_base:
                touched.add("device:base")
            rebased_from = expected
            base, base_r = head, head_r
        # PROTECTION: enforced here, for every actor and every door (UI, MCP, import, adoption)
        violated = _protection_violations(base.constraints, touched, diff)
        if violated:
            raise WorkspaceError(
                "protected",
                "the change touches protected entities: " + ", ".join(v["key"] for v in violated[:8]),
                status=403, detail={"violations": violated})
        if not diff["added"] and not diff["removed"] and not diff["changed"] \
                and not diff["program_changed"] and not diff["final_program_changed"] \
                and canonical_text(constraints) == canonical_text(base.constraints) \
                and canonical_text(new_state.edits) == canonical_text(base.state.edits):
            status_noop = True
        else:
            status_noop = False
        diagnostics = self.diagnose(new_r)
        created = [k for k in diff["added"]]
        result = {
            "contract": CONTRACT_VERSION,
            "status": "previewed" if req["mode"] == "preview" else "committed",
            "workspace_id": self.id, "branch": branch,
            "base_revision": base.revision if rebased_from is None else rebased_from,
            "rebased_onto": base.revision if rebased_from is not None else None,
            "revision": None, "change_set_id": None,
            "summary": req.get("summary") or diff["summary"],
            "diff": diff, "touched": sorted(touched), "created_entities": created,
            "diagnostics": diagnostics, "noop": status_noop,
            "attribution": {"actor": actor, "origin_prompt_id": req.get("origin_prompt_id"),
                            "request_id": req["request_id"]},
            "input_digest": new_state.input_digest(),
        }
        if req["mode"] == "preview":
            return result
        if status_noop:
            result["status"] = "noop"
            result["revision"] = base.revision
            return result
        cs_id = new_id("cs")
        for rec in new_state.edits:
            meta = rec.setdefault("meta", {})
            if "change_set" not in meta:
                meta["change_set"] = cs_id
                meta["author"] = actor["kind"]
        with self.store.tx() as db:
            cur = db.execute("SELECT head FROM branches WHERE name=?", (branch,)).fetchone()["head"]
            if cur != base.revision:
                raise WorkspaceError("conflict", f"{branch} moved while this change was validated; retry",
                                     status=409, detail={"head_revision": cur})
            rev = cur + 1
            db.execute("INSERT INTO revisions(branch, revision, change_set_id, state, input_digest, "
                       "arch_digest, created_at) VALUES(?,?,?,?,?,?,?)",
                       (branch, rev, cs_id, dumps({"design": new_state.to_json(), "constraints": constraints}),
                        new_state.input_digest(), new_r.arch_digest(), _now()))
            db.execute("UPDATE branches SET head=? WHERE name=?", (rev, branch))
            db.execute("INSERT INTO change_sets(id, branch, base_revision, revision, request_id, actor, "
                       "origin_prompt_id, summary, operations, diff, touched, diagnostics, status, "
                       "created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'committed', ?)",
                       (cs_id, branch, base.revision, rev, req["request_id"], dumps(actor),
                        req.get("origin_prompt_id"), result["summary"], dumps(req["operations"]),
                        dumps(diff), dumps(sorted(touched)), dumps(diagnostics), _now()))
            result["status"] = "committed"
            result["revision"] = rev
            result["change_set_id"] = cs_id
            if idem_key:
                db.execute("INSERT INTO idempotency(key, request_digest, response, created_at) "
                           "VALUES(?,?,?,?)", (idem_key, req_digest, dumps(result), _now()))
            self._emit(db, "design.committed", {
                "change_set_id": cs_id, "actor": actor, "summary": result["summary"],
                "diff_summary": diff["summary"], "touched": sorted(touched)[:200],
                "origin_prompt_id": req.get("origin_prompt_id"),
                "request_id": req["request_id"], "input_digest": result["input_digest"]},
                branch, rev)
            if req.get("origin_prompt_id"):
                self._link_prompt(db, req["origin_prompt_id"], "change_set", cs_id, actor)
            else:
                working = self._working_prompt(db, actor)
                if working:
                    self._link_prompt(db, working, "change_set", cs_id, actor)
        self._replays[(branch, rev)] = new_r
        if branch == "main":
            self.write_mirror("main")
        return result

    def _validated_replay(self, new_state: DesignState, base_r: Replayed) -> Replayed:
        try:
            r = replay(new_state)
        except DesignRefused as exc:
            raise WorkspaceError("refused", str(exc), status=422, detail={"problems": exc.problems}) from None
        old = {canonical_text(p) for p in base_r.problems}
        fresh = [p for p in r.problems if canonical_text(p) not in old]
        if fresh:
            raise WorkspaceError("refused",
                                 "the toolchain refused the change: " + fresh[0]["message"],
                                 status=422, detail={"problems": fresh})
        return r

    def diagnose(self, r: Replayed, limit: int = 50) -> dict:
        """Draft-quality diagnostics: never a reason to refuse an edit."""
        out: dict = {"problems": list(r.problems)[:limit], "violations": [], "warnings": [],
                     "program": None, "physics": None}
        if r.arch is None:
            return out
        try:
            from ..api import Machine
            m = Machine(r.arch)
            for v in m.violations()[:limit]:
                out["violations"].append({"rule": v.rule, "message": v.message,
                                          "severity": getattr(v, "severity", "error"),
                                          "instr_id": getattr(v, "instr_id", None)})
        except Exception as exc:  # the diagnostic must never take the edit down
            out["warnings"].append(f"architecture checks did not run: {type(exc).__name__}: {exc}")
        for i, rep in sorted(r.reports.items()):
            for w in rep.get("warnings") or []:
                out["warnings"].append(f"edit {i}: {w}")
        if r.state.program:
            try:
                from ..api import Machine
                prog = Machine(r.arch).program("authored", provenance="off").apply_calls(r.state.program).build()
                out["program"] = {"ok": True, "instructions": len(prog)}
            except Exception as exc:
                out["program"] = {"ok": False, "message": str(exc)}
        mism = physics_mismatch(r.arch_doc or {}, self.release)
        out["physics"] = {"matches_release": not mism, "changed_blocks": mism,
                          "mode": "task" if not mism else "exploratory"}
        out["warnings"] = out["warnings"][:limit]
        return out

    def _change_sets_between(self, branch: str, lo: int, hi: int) -> list:
        rows = self.store.all("SELECT * FROM change_sets WHERE branch=? AND revision>? AND revision<=? "
                              "ORDER BY revision", (branch, lo, hi))
        return [_cs_row(r) for r in rows]

    def history(self, branch: str = "main", *, limit: int = 50, before: int | None = None) -> list:
        limit = max(1, min(int(limit), 500))
        if before is None:
            rows = self.store.all("SELECT * FROM change_sets WHERE branch=? ORDER BY revision DESC LIMIT ?",
                                  (branch, limit))
        else:
            rows = self.store.all("SELECT * FROM change_sets WHERE branch=? AND revision<? "
                                  "ORDER BY revision DESC LIMIT ?", (branch, before, limit))
        return [_cs_brief(_cs_row(r)) for r in rows]

    def change_set(self, cs_id: str) -> dict:
        r = self.store.one("SELECT * FROM change_sets WHERE id=?", (cs_id,))
        if r is None:
            raise WorkspaceError("unknown_change_set", f"no change set {cs_id!r}", status=404)
        return _cs_row(r)

    # ------------------------------------------------------------------ undo

    def undo_change_set(self, cs_id: str, actor: Mapping, *, request_id: str | None = None,
                        expected_revision: int | None = None, mode: str = "apply") -> dict:
        """Take one change set back as a NEW, validated change set.

        Never a rollback: later, unrelated edits (a human's, typically) stay.  A later
        change that depends on what is being undone is reported as a conflict rather than
        silently broken.
        """
        actor = _actor(actor)
        cs = self.change_set(cs_id)
        if cs["status"] != "committed":
            raise WorkspaceError("already_undone", f"{cs_id} is {cs['status']}", status=409)
        branch = cs["branch"]
        head = self.head(branch)
        if expected_revision is not None and expected_revision != head.revision:
            raise WorkspaceError("conflict", f"{branch} is at {head.revision}, not {expected_revision}",
                                 status=409, detail={"head_revision": head.revision})
        later = self._change_sets_between(branch, cs["revision"], head.revision)
        before = self.revision(branch, cs["base_revision"])
        after = self.revision(branch, cs["revision"])
        new_state = head.state.copy()
        constraints = copy.deepcopy(head.constraints)
        if "device:base" in cs["touched"]:
            if later:
                raise WorkspaceError("conflict", "a new device was built later changes rest on; "
                                     "undo those first", status=409,
                                     detail={"dependent": [_cs_brief(c) for c in later]})
            new_state = before.state.copy()
            constraints = copy.deepcopy(before.constraints)
        else:
            mine = [e for e in new_state.edits if (e.get("meta") or {}).get("change_set") == cs_id]
            removed_by_cs = _removed_records(before.state, after.state)
            new_state.edits = [e for e in new_state.edits
                               if (e.get("meta") or {}).get("change_set") != cs_id]
            if removed_by_cs:
                if later:
                    raise WorkspaceError("conflict", "this change took records back and later changes "
                                         "followed it; undo those first", status=409,
                                         detail={"dependent": [_cs_brief(c) for c in later]})
                new_state.edits = copy.deepcopy(before.state.edits)
            if cs["diff"].get("program_changed"):
                if canonical_text(head.state.program) != canonical_text(after.state.program):
                    raise WorkspaceError("conflict", "the program changed after this change set",
                                         status=409, detail={"dependent": [_cs_brief(c) for c in later
                                                                           if c["diff"].get("program_changed")]})
                new_state.program = copy.deepcopy(before.state.program)
            if cs["diff"].get("final_program_changed"):
                new_state.final_program = copy.deepcopy(before.state.final_program)
            for key in list(constraints.get("protected", {})):
                if constraints["protected"][key].get("change_set") == cs_id:
                    del constraints["protected"][key]
            _ = mine
        try:
            r = replay(new_state)
        except DesignRefused as exc:
            raise WorkspaceError("conflict", f"undoing {cs_id} leaves a design that does not build: {exc}",
                                 status=409, detail={"problems": exc.problems,
                                                     "dependent": [_cs_brief(c) for c in later]}) from None
        if r.problems:
            blamed = sorted({(new_state.edits[p["index"]].get("meta") or {}).get("change_set")
                             for p in r.problems if isinstance(p.get("index"), int)
                             and p["index"] < len(new_state.edits)} - {None})
            raise WorkspaceError("conflict", f"later changes depend on {cs_id}: {', '.join(blamed) or 'see problems'}",
                                 status=409, detail={"problems": r.problems, "dependent_change_sets": blamed})
        head_r = self.replayed(branch, head.revision)
        diff = diff_designs(head_r, r)
        touched = touched_entities(diff, head_r, r)
        violated = _protection_violations(head.constraints, touched, diff)
        if violated:
            raise WorkspaceError("protected", "undo would change protected entities", status=403,
                                 detail={"violations": violated})
        result = {"contract": CONTRACT_VERSION, "status": "previewed" if mode == "preview" else "committed",
                  "workspace_id": self.id, "branch": branch, "base_revision": head.revision,
                  "undo_of": cs_id, "diff": diff, "touched": sorted(touched),
                  "diagnostics": self.diagnose(r), "summary": f"undo {cs_id}: {cs['summary']}"}
        if mode == "preview":
            return result
        new_cs = new_id("cs")
        with self.store.tx() as db:
            cur = db.execute("SELECT head FROM branches WHERE name=?", (branch,)).fetchone()["head"]
            if cur != head.revision:
                raise WorkspaceError("conflict", "the branch moved during undo; retry", status=409)
            rev = cur + 1
            db.execute("INSERT INTO revisions(branch, revision, change_set_id, state, input_digest, "
                       "arch_digest, created_at) VALUES(?,?,?,?,?,?,?)",
                       (branch, rev, new_cs, dumps({"design": new_state.to_json(), "constraints": constraints}),
                        new_state.input_digest(), r.arch_digest(), _now()))
            db.execute("UPDATE branches SET head=? WHERE name=?", (rev, branch))
            db.execute("INSERT INTO change_sets(id, branch, base_revision, revision, request_id, actor, "
                       "origin_prompt_id, summary, operations, diff, touched, diagnostics, status, undo_of, "
                       "created_at) VALUES(?,?,?,?,?,?,NULL,?,?,?,?,?, 'committed', ?, ?)",
                       (new_cs, branch, head.revision, rev, request_id or new_cs, dumps(actor),
                        result["summary"], dumps([{"type": "undo", "change_set": cs_id}]), dumps(diff),
                        dumps(sorted(touched)), dumps(result["diagnostics"]), cs_id, _now()))
            db.execute("UPDATE change_sets SET status='undone', undone_by=? WHERE id=?", (new_cs, cs_id))
            self._emit(db, "design.committed", {"change_set_id": new_cs, "actor": actor,
                                                "summary": result["summary"], "undo_of": cs_id,
                                                "diff_summary": diff["summary"], "touched": sorted(touched)[:200],
                                                "input_digest": new_state.input_digest()}, branch, rev)
        self._replays[(branch, rev)] = r
        result.update(revision=rev, change_set_id=new_cs)
        if branch == "main":
            self.write_mirror("main")
        return result

    # ------------------------------------------------------------------ candidates

    def create_candidate(self, actor: Mapping, *, name: str | None = None, source: str = "main",
                         note: str = "", origin_prompt_id: str | None = None, title: str | None = None) -> dict:
        """A new design (a draft), copied from `source`.  `title` is what the person calls it, any
        text; its internal name is made up (`name` is the old way, kept for tools that pass one)."""
        actor = _actor(actor)
        src = self.head(source)
        if title is not None:
            title = " ".join(str(title).split())
            if not title or len(title) > 120:
                raise WorkspaceError("bad_name", "a design's name is 1 to 120 characters", status=422)
        bname = f"cand/{name or ('d' + secrets.token_hex(3))}"
        if not _branch_name_ok(bname):
            raise WorkspaceError("bad_name", f"{bname!r} is not a usable branch name")
        with self.store.tx() as db:
            if db.execute("SELECT 1 FROM branches WHERE name=?", (bname,)).fetchone():
                raise WorkspaceError("exists", f"branch {bname!r} exists", status=409)
            db.execute("INSERT INTO branches(name, kind, head, parent, parent_revision, status, created_by, "
                       "created_at, note, title) VALUES(?, 'candidate', 0, ?, ?, 'open', ?, ?, ?, ?)",
                       (bname, source, src.revision, dumps(actor), _now(), note[:500], title))
            db.execute("INSERT INTO revisions(branch, revision, change_set_id, state, input_digest, "
                       "arch_digest, created_at) VALUES(?, 0, NULL, ?, ?, ?, ?)",
                       (bname, dumps({"design": src.state.to_json(), "constraints": src.constraints}),
                        src.input_digest, src.arch_digest, _now()))
            self._emit(db, "branch.created", {"branch": bname, "parent": source, "title": title,
                                              "parent_revision": src.revision, "actor": actor,
                                              "origin_prompt_id": origin_prompt_id}, bname, 0)
        return self.branch(bname)

    def compare(self, a: str, b: str = "main") -> dict:
        ra, rb = self.replayed(a), self.replayed(b)
        diff = diff_designs(rb, ra)
        return {"from": b, "to": a, "from_revision": self.branch(b)["head"],
                "to_revision": self.branch(a)["head"], "diff": diff,
                "diagnostics": {"from": self.diagnose(rb), "to": self.diagnose(ra)}}

    def adopt_candidate(self, candidate: str, actor: Mapping, *, expected_revision: int,
                        request_id: str, mode: str = "apply") -> dict:
        """Bring a candidate into main -- through the same change-set path, so protection
        and conflict detection apply exactly as they do to a direct edit."""
        actor = _actor(actor)
        cand = self.branch(candidate)
        if cand["kind"] != "candidate" or cand["status"] != "open":
            raise WorkspaceError("bad_branch", f"{candidate} is not an open candidate", status=409)
        parent = cand["parent"]
        css = self._change_sets_between(candidate, 0, cand["head"])
        if not css:
            raise WorkspaceError("empty", f"{candidate} has no changes to adopt", status=409)
        ops = []
        for cs in css:
            if cs["status"] != "committed":
                continue
            for op in cs["operations"]:
                if op.get("type") in ("undo",):
                    raise WorkspaceError("unsupported", "candidates containing undo change sets must be "
                                         "adopted by hand", status=409)
                ops.append(op)
        req = {"workspace_id": self.id, "branch": parent, "expected_revision": cand["parent_revision"],
               "request_id": request_id, "mode": mode, "rebase": "if_disjoint",
               "summary": f"adopt {candidate} ({len(css)} change sets)", "operations": ops}
        head = self.branch(parent)["head"]
        if expected_revision != head:
            raise WorkspaceError("conflict", f"{parent} is at {head}, not {expected_revision}", status=409,
                                 detail={"head_revision": head})
        out = self.apply_change_set(req, actor)
        if mode == "apply" and out.get("status") == "committed":
            with self.store.tx() as db:
                db.execute("UPDATE branches SET status='adopted' WHERE name=?", (candidate,))
                self._emit(db, "branch.adopted", {"branch": candidate, "into": parent,
                                                  "change_set_id": out["change_set_id"]}, parent, out["revision"])
        return out

    def discard_candidate(self, candidate: str, actor: Mapping) -> dict:
        cand = self.branch(candidate)
        if cand["kind"] != "candidate":
            raise WorkspaceError("bad_branch", f"{candidate} is not a candidate", status=409)
        with self.store.tx() as db:
            db.execute("UPDATE branches SET status='discarded' WHERE name=?", (candidate,))
            self._emit(db, "branch.discarded", {"branch": candidate, "actor": _actor(actor)}, candidate, cand["head"])
        return self.branch(candidate)

    # ------------------------------------------------------------------ events

    def _emit(self, db, type_: str, payload: Mapping, branch: str | None = None,
              revision: int | None = None) -> int:
        cur = db.execute("INSERT INTO events(ts, type, branch, revision, payload) VALUES(?,?,?,?,?)",
                         (_now(), type_, branch, revision, dumps(payload)))
        seq = cur.lastrowid
        # wake listeners after COMMIT; the condition is notified by `notify()`
        threading.Timer(0, self.notify).start()
        return seq

    def emit(self, type_: str, payload: Mapping, branch: str | None = None,
             revision: int | None = None) -> int:
        with self.store.tx() as db:
            return self._emit(db, type_, payload, branch, revision)

    def notify(self) -> None:
        with self._cond:
            self._cond.notify_all()
        for fn in list(self._listeners):
            try:
                fn()
            except Exception:
                pass

    def wait_for_events(self, cursor: int, timeout: float) -> None:
        with self._cond:
            if self.last_event_seq() > cursor:
                return
            self._cond.wait(timeout)

    def last_event_seq(self) -> int:
        r = self.store.one("SELECT MAX(seq) AS s FROM events")
        return int(r["s"] or 0)

    def events_since(self, cursor: int, *, limit: int = 200, types: Iterable[str] | None = None) -> dict:
        """Events after `cursor`.  `gap: true` means the cursor predates retention and the
        client must fetch a fresh snapshot instead of replaying."""
        limit = max(1, min(int(limit), 1000))
        low = self.store.one("SELECT MIN(seq) AS s FROM events")
        low_seq = int(low["s"] or 0)
        gap = cursor > 0 and low_seq > cursor + 1
        rows = self.store.all("SELECT * FROM events WHERE seq>? ORDER BY seq LIMIT ?", (cursor, limit))
        evs = []
        for r in rows:
            if types and r["type"] not in types:
                continue
            evs.append({"seq": r["seq"], "ts": r["ts"], "type": r["type"], "branch": r["branch"],
                        "revision": r["revision"], "payload": loads(r["payload"])})
        return {"events": evs, "gap": gap, "cursor": rows[-1]["seq"] if rows else cursor,
                "head_seq": self.last_event_seq()}

    def prune_events(self, keep: int = 20000) -> int:
        with self.store.tx() as db:
            top = db.execute("SELECT MAX(seq) AS s FROM events").fetchone()["s"] or 0
            cur = db.execute("DELETE FROM events WHERE seq <= ?", (top - keep,))
            return cur.rowcount

    # ------------------------------------------------------------------ mirror / import

    @property
    def mirror_path(self) -> Path:
        return self.root / "design" / "studio.json"

    def write_mirror(self, branch: str = "main") -> Path:
        """Write `design/studio.json` atomically: the design as a `qccd.studio` document,
        stamped with the revision it is, so a raw file edit can be told apart from a
        stale copy.  Derived from the database; never the other way round."""
        h = self.head(branch)
        r = self.replayed(branch, h.revision)
        doc = h.state.to_studio(r.arch_doc)
        doc["qccd_workspace"] = {"id": self.id, "branch": branch, "revision": h.revision,
                                 "input_digest": h.input_digest}
        p = self.mirror_path
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        os.replace(tmp, p)
        return p

    def import_file(self, path: Path, actor: Mapping, *, branch: str = "main",
                    request_id: str | None = None, mode: str = "apply") -> dict:
        """Bring a design file written outside Studio (an optimizer's output, a hand edit
        of the mirror) into the live workspace -- as a change set, so the same validation,
        protection and conflict rules apply.  A file made from an older revision than the
        current head is a conflict: raw file edits never overwrite newer UI edits."""
        from .jsonsafe import strict_loads
        p = Path(path).resolve()
        if not p.is_file():
            raise WorkspaceError("not_found", f"{p} does not exist", status=404)
        if p.stat().st_size > 16 * 1024 * 1024:
            raise WorkspaceError("too_large", f"{p} is larger than 16 MB", status=413)
        try:
            doc = strict_loads(p.read_bytes())
        except ValueError as exc:
            raise WorkspaceError("bad_file", f"{p.name}: {exc}", status=422) from None
        try:
            incoming = DesignState.from_studio(doc)
        except DesignRefused as exc:
            raise WorkspaceError("bad_file", str(exc), status=422, detail={"problems": exc.problems}) from None
        head = self.head(branch)
        stamp = doc.get("qccd_workspace") or {}
        if stamp.get("id") not in (None, self.id):
            raise WorkspaceError("foreign_file", "the file belongs to another workspace", status=409)
        base_rev = stamp.get("revision", head.revision) if stamp.get("branch", branch) == branch else head.revision
        if not isinstance(base_rev, int) or base_rev > head.revision:
            raise WorkspaceError("bad_file", "the file's revision stamp is not a revision of this branch",
                                 status=422)
        base = self.revision(branch, base_rev)
        ops = [{"type": "studio_sync", "base": {"geom": incoming.geom, "seed": incoming.seed,
                                                "post": incoming.post},
                "append": incoming.edits, "program": incoming.program}]
        same_base = canonical_text([incoming.geom, incoming.seed, incoming.post]) == \
            canonical_text([base.state.geom, base.state.seed, base.state.post])
        if same_base:
            removed = [e for e in base.state.edits
                       if canonical_text(_sm(e)) not in {canonical_text(_sm(x)) for x in incoming.edits}]
            added = [e for e in incoming.edits
                     if canonical_text(_sm(e)) not in {canonical_text(_sm(x)) for x in base.state.edits}]
            ops = [{"type": "studio_sync", "remove": removed, "append": added,
                    "program": incoming.program}]
        req = {"workspace_id": self.id, "branch": branch, "expected_revision": base_rev,
               "request_id": request_id or f"import:{digest(doc)[7:23]}", "mode": mode,
               "rebase": "never", "summary": f"import {p.name}", "operations": ops}
        # `studio_sync` is Studio's operation; an import uses it on the file's behalf and
        # says so (`via`), keeping the real actor for attribution and permissions
        return self.apply_change_set(req, {**_actor(actor), "via": "import"})

    # ------------------------------------------------------------------ artifacts

    def put_artifact(self, data: bytes, media_type: str, label: str = "") -> str:
        import hashlib
        d = "sha256:" + hashlib.sha256(data).hexdigest()
        path = self.artifacts_dir / d.split(":", 1)[1]
        if not path.exists():
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        with self.store.tx() as db:
            db.execute("INSERT OR IGNORE INTO artifacts(digest, media_type, size, path, label, created_at) "
                       "VALUES(?,?,?,?,?,?)", (d, media_type, len(data), str(path.relative_to(self.root)),
                                              label[:200], _now()))
        return d

    def get_artifact(self, d: str) -> bytes:
        r = self.store.one("SELECT * FROM artifacts WHERE digest=?", (d,))
        if r is None:
            raise WorkspaceError("unknown_artifact", f"no artifact {d}", status=404)
        data = (self.root / r["path"]).read_bytes()
        import hashlib
        if "sha256:" + hashlib.sha256(data).hexdigest() != d:
            raise WorkspaceError("corrupt_artifact", f"artifact {d} does not match its digest", status=500)
        return data

    # ------------------------------------------------------------------ query

    def query_design(self, *, branch: str = "main", keys: list | None = None, kind: str | None = None,
                     limit: int = 200, include_operations: bool = False) -> dict:
        h = self.head(branch)
        r = self.replayed(branch, h.revision)
        ents = entities(r.arch_doc or {})
        chosen = list(ents)
        if keys:
            chosen = [k for k in keys if k in ents]
        if kind:
            chosen = [k for k in chosen if k.startswith(kind + ":")]
        missing = [k for k in (keys or []) if k not in ents]
        out = {"branch": branch, "revision": h.revision, "input_digest": h.input_digest,
               "total": len(chosen), "entities": entity_summary(r.arch_doc or {}, chosen, limit),
               "missing": missing, "truncated": len(chosen) > limit,
               "protected": h.constraints.get("protected", {}),
               "program": {"statements": len(h.state.program), "calls": h.state.program[:limit]},
               "final_program": h.state.final_program}
        if include_operations:
            out["operations"] = describe_operations()
        return out

    def design_document(self, branch: str = "main", revision: int | None = None) -> dict:
        """The `qccd.studio` document the browser restores, for one revision."""
        rev = self.revision(branch, revision if revision is not None else self.branch(branch)["head"])
        r = self.replayed(branch, rev.revision)
        doc = rev.state.to_studio(r.arch_doc)
        doc["qccd_workspace"] = {"id": self.id, "branch": branch, "revision": rev.revision,
                                 "input_digest": rev.input_digest, "constraints": rev.constraints}
        return doc

    # ------------------------------------------------------------------ hooks for mixins

    def _after_init(self) -> None:
        pass

    def _check_actor_may_write(self, actor: Mapping, origin_prompt_id: str | None) -> None:
        pass

    def _check_mode(self, actor: Mapping, origin_prompt_id: str | None, branch: str, mode: str) -> None:
        pass

    def _link_prompt(self, db, prompt_id: str, what: str, ref: str, actor: Mapping) -> None:
        pass

    def _working_prompt(self, db, actor: Mapping) -> str | None:
        return None


# ---------------------------------------------------------------------- helpers

def _pid_alive(pid: int) -> bool:
    """Is `pid` a running process?  ACCESS DENIED means yes: a sandboxed agent process
    (Codex runs MCP servers under a restricted token) cannot open the service's process,
    and reading that as "dead" once made an adapter delete a live service's runtime file
    and start a second service for the same workspace."""
    if pid <= 0:
        return False
    if os.name == "nt":
        import ctypes
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return ctypes.get_last_error() == 5          # ERROR_ACCESS_DENIED: it exists
        code = ctypes.c_ulong()
        ok = ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(h)
        return bool(ok) and code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _sm(rec: Mapping) -> dict:
    return {k: v for k, v in rec.items() if k not in ("meta", "_report")}


def _removed_records(before: DesignState, after: DesignState) -> list:
    keys = {canonical_text(_sm(e)) for e in after.edits}
    return [e for e in before.edits if canonical_text(_sm(e)) not in keys]


def _branch_name_ok(name: str) -> bool:
    import re
    return bool(re.match(r"^(main|cand/[A-Za-z0-9_.-]{1,64})$", name))


_REQ_KEYS = {"workspace_id", "branch", "expected_revision", "request_id", "origin_prompt_id",
             "mode", "summary", "operations", "rebase"}


def _check_request(req: Mapping, ws_id: str) -> dict:
    if not isinstance(req, Mapping):
        raise WorkspaceError("bad_request", "a change set must be an object", status=422)
    extra = set(req) - _REQ_KEYS
    if extra:
        raise WorkspaceError("bad_request", f"unknown fields {sorted(extra)}", status=422)
    out = dict(req)
    if out.get("workspace_id") not in (None, ws_id):
        raise WorkspaceError("wrong_workspace", "the change set names another workspace", status=409)
    out["branch"] = out.get("branch") or "main"
    if not isinstance(out["branch"], str) or not _branch_name_ok(out["branch"]):
        raise WorkspaceError("bad_request", "branch must be 'main' or 'cand/<name>'", status=422)
    er = out.get("expected_revision")
    if isinstance(er, bool) or not isinstance(er, int):
        raise WorkspaceError("bad_request", "expected_revision (an integer) is required -- read it from "
                             "qccd_get_context; do not guess it", status=422)
    rid = out.get("request_id")
    if not isinstance(rid, str) or not 1 <= len(rid) <= 128:
        raise WorkspaceError("bad_request", "request_id (1-128 characters) is required", status=422)
    out["mode"] = out.get("mode") or "preview"
    if out["mode"] not in ("preview", "apply"):
        raise WorkspaceError("bad_request", "mode must be 'preview' or 'apply'", status=422)
    out["rebase"] = out.get("rebase") or "never"
    if out["rebase"] not in ("never", "if_disjoint"):
        raise WorkspaceError("bad_request", "rebase must be 'never' or 'if_disjoint'", status=422)
    if not isinstance(out.get("operations"), list) or not out["operations"]:
        raise WorkspaceError("bad_request", "operations must be a non-empty list", status=422)
    if out.get("summary") is not None and not isinstance(out["summary"], str):
        raise WorkspaceError("bad_request", "summary must be a string", status=422)
    out["summary"] = (out.get("summary") or "")[:500]
    op = out.get("origin_prompt_id")
    if op is not None and (not isinstance(op, str) or len(op) > 64):
        raise WorkspaceError("bad_request", "origin_prompt_id must be a prompt id", status=422)
    return out


def _cs_row(r) -> dict:
    return {"id": r["id"], "branch": r["branch"], "base_revision": r["base_revision"],
            "revision": r["revision"], "request_id": r["request_id"], "actor": loads(r["actor"]),
            "origin_prompt_id": r["origin_prompt_id"], "summary": r["summary"],
            "operations": loads(r["operations"]), "diff": loads(r["diff"]),
            "touched": loads(r["touched"]), "diagnostics": loads(r["diagnostics"]),
            "status": r["status"], "undo_of": r["undo_of"], "undone_by": r["undone_by"],
            "created_at": r["created_at"]}


def _cs_brief(cs: Mapping) -> dict:
    return {"change_set_id": cs["id"], "revision": cs["revision"], "actor": cs["actor"],
            "summary": cs["summary"], "diff_summary": cs["diff"].get("summary"),
            "touched": cs["touched"][:50], "status": cs["status"], "undo_of": cs.get("undo_of"),
            "origin_prompt_id": cs.get("origin_prompt_id"), "created_at": cs["created_at"]}


# ---------------------------------------------------------------------- constraints

_CONSTRAINT_OPS = {"protect", "unprotect"}


def _split_constraint_ops(ops: list) -> tuple:
    c, d = [], []
    for op in ops:
        (c if isinstance(op, Mapping) and op.get("type") in _CONSTRAINT_OPS else d).append(op)
    return c, d


def _apply_constraint_ops(constraints: dict, ops: list, actor: Mapping, r: Replayed) -> tuple:
    """`protect` adds an enforceable constraint; `unprotect` removes one.

    An agent may protect but may not unprotect what a human protected: a lock the thing it
    locks out can lift is not a lock.
    """
    prot = constraints.setdefault("protected", {})
    touched = set()
    ents = entities(r.arch_doc or {}) if ops else {}
    for i, op in enumerate(ops):
        keys = op.get("keys")
        if isinstance(keys, str):
            keys = [keys]
        if not isinstance(keys, list) or not keys or not all(isinstance(k, str) for k in keys):
            raise WorkspaceError("bad_request", f"operations: {op.get('type')} needs keys: [entity key]",
                                 status=422)
        if op["type"] == "protect":
            level = op.get("level", "entity")
            if level not in ("entity", "neighbourhood"):
                raise WorkspaceError("bad_request", "level must be 'entity' or 'neighbourhood'", status=422)
            missing = [k for k in keys if k not in ents]
            if missing:
                raise WorkspaceError("unknown_entity", f"cannot protect entities that do not exist: {missing[:5]}",
                                     status=422)
            for k in keys:
                prot[k] = {"level": level, "by": dict(actor), "note": str(op.get("note", ""))[:300],
                           "prompt_id": op.get("prompt_id"), "at": _now()}
                touched.add(f"constraint:{k}")
        else:
            for k in keys:
                cur = prot.get(k)
                if cur is None:
                    continue
                if (cur.get("by") or {}).get("kind") == "human" and actor.get("kind") != "human":
                    raise WorkspaceError("forbidden", f"{k} was protected by a person; only a person may "
                                         f"unprotect it", status=403)
                del prot[k]
                touched.add(f"constraint:{k}")
    return constraints, touched


def _protection_violations(constraints: Mapping, touched: Iterable[str], diff: Mapping) -> list:
    prot = (constraints or {}).get("protected") or {}
    out = []
    touched = set(touched)
    for key, spec in prot.items():
        hit = key in touched
        if not hit and spec.get("level") == "neighbourhood":
            hit = f"{key}#incident" in touched
        if hit:
            how = ("removed" if key in diff.get("removed", ()) else
                   "changed: " + ",".join(diff.get("changed", {}).get(key, [])) if key in diff.get("changed", {})
                   else "its connections changed")
            out.append({"key": key, "level": spec.get("level"), "how": how,
                        "protected_by": spec.get("by"), "note": spec.get("note")})
    return out
