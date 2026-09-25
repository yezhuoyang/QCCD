"""Task releases and the workspace lockfile.

A TASK RELEASE fixes everything a score depends on that the participant does not choose:
the circuit, the physics tables, the fixed architecture blocks, the checks an entry must
pass, the metrics and their units, the ranking metric, the numerical policy and the
evaluator identity.  It is an immutable directory

    releases/<task>@<release>/release.json     the manifest (digest-pinned files)
                              circuit.qasm     the circuit, byte for byte
                              physics.json     the fixed blocks' trusted values

and its identity is the digest of `release.json`'s canonical form.  Each release is one
LEADERBOARD ("board") of the website, known to people by its title ("BB [[144,12,12]]").

A workspace is general: it holds any number of designs and submits any of them to any board
(`find_board` by title or short name; a submission records the exact release and digest it
was graded against, so a result never silently changes meaning).  A workspace made before
that (lockfile v1) pinned one release; it still opens, and that release is its default.

What a participant MAY change is the design (geometry, zones, capacities, wiring,
control) and the hardware program.  What they may NOT change for a leaderboard entry is
the physics: `primitives`, `heating`, `species` and `budget` must equal the release's
`physics.json`.  A design that changes them is still a legal local experiment -- it is
labelled exploratory and is never eligible for the task.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .jsonsafe import canonical_text, digest, normalize_numbers, strict_loads

__all__ = ["TaskRelease", "ReleaseError", "RELEASES_DIR", "find_release", "list_releases", "find_board",
           "list_boards", "DEFAULT_BOARD", "write_lock", "read_lock", "LOCK_NAME", "physics_mismatch",
           "FIXED_BLOCKS"]

RELEASES_DIR = Path(__file__).resolve().parent / "releases"
LOCK_NAME = "qccd.lock.json"
FIXED_BLOCKS = ("primitives", "heating", "species", "budget")
#: the board a general workspace uses where it needs one without being told (its first device, the
#: physics and cost tables -- the same on every board); never shown to people
DEFAULT_BOARD = "ghz4@1"


class ReleaseError(ValueError):
    pass


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


@dataclass(frozen=True)
class TaskRelease:
    root: Path
    manifest: dict

    @property
    def id(self) -> str:
        return f"{self.manifest['task']}@{self.manifest['release']}"

    @property
    def digest(self) -> str:
        return digest(self.manifest)

    @property
    def circuit_path(self) -> Path:
        return self.root / self.manifest["circuit"]["file"]

    def circuit_text(self) -> str:
        return self.circuit_path.read_text(encoding="utf-8")

    def physics(self) -> dict:
        return json.loads((self.root / self.manifest["physics"]["file"]).read_text(encoding="utf-8"))

    @property
    def required_checks(self) -> list:
        return list(self.manifest["required_checks"])

    @property
    def title(self) -> str:
        return str(self.manifest.get("title") or self.manifest["task"])

    def board(self) -> dict:
        """What a person (or an agent speaking to one) is shown about this board: its title."""
        m = self.manifest
        return {"title": self.title, "about": m.get("description"), "rank_by": m.get("rank_by"),
                "suggested_start": m.get("starter"), "id": self.id}

    def summary(self) -> dict:
        m = self.manifest
        return {"id": self.id, "digest": self.digest, "title": m.get("title"),
                "track": m.get("track"), "rank_by": m.get("rank_by"),
                "metrics": m.get("metrics"), "required_checks": m.get("required_checks"),
                "physics": {k: v for k, v in m["physics"].items() if k != "file"},
                "evaluator": m.get("evaluator"), "numerical_policy": m.get("numerical_policy"),
                "design_variables": m.get("design_variables"),
                "allowed_transformations": m.get("allowed_transformations"),
                "starter": m.get("starter")}

    def verify_files(self) -> None:
        """Every file the manifest pins must hash to its pinned digest."""
        for key in ("circuit", "physics"):
            ref = self.manifest[key]
            p = self.root / ref["file"]
            if not p.is_file():
                raise ReleaseError(f"{self.id}: {ref['file']} is missing")
            got = _sha256_file(p)
            if got != ref["digest"]:
                raise ReleaseError(f"{self.id}: {ref['file']} digest {got} != pinned {ref['digest']}")

    @classmethod
    def load(cls, root: Path) -> "TaskRelease":
        root = Path(root)
        man = strict_loads((root / "release.json").read_bytes())
        if man.get("kind") != "qccd.task_release" or man.get("version") != 1:
            raise ReleaseError(f"{root}: not a qccd.task_release v1")
        rel = cls(root=root, manifest=man)
        rel.verify_files()
        return rel


def list_releases(extra: Path | None = None) -> list:
    out = []
    for base in [RELEASES_DIR] + ([Path(extra)] if extra else []):
        if base.is_dir():
            for d in sorted(base.iterdir()):
                if (d / "release.json").is_file():
                    out.append(TaskRelease.load(d))
    return out


def find_release(ref: str, extra: Path | None = None) -> TaskRelease:
    """`<task>@<release>` -> the release, verified."""
    if "@" not in ref:
        raise ReleaseError(f"{ref!r}: name a release as <task>@<release>")
    for base in [RELEASES_DIR] + ([Path(extra)] if extra else []):
        d = base / ref
        if (d / "release.json").is_file():
            return TaskRelease.load(d)
    have = [r.id for r in list_releases(extra)]
    raise ReleaseError(f"no task release {ref!r} (have: {have})")


def _norm(s: str) -> str:
    return "".join(c for c in str(s).lower() if c.isalnum())


def list_boards(extra: Path | None = None) -> list:
    """Every board, the newest release of each task, in the website's order."""
    newest: dict = {}
    for r in list_releases(extra):
        t = r.manifest["task"]
        if t not in newest or int(r.manifest["release"]) > int(newest[t].manifest["release"]):
            newest[t] = r
    return sorted(newest.values(), key=lambda r: (int(r.manifest.get("order", 99)), r.title))


def find_board(ref: str | None, extra: Path | None = None) -> TaskRelease:
    """A board by what a person calls it: its title ("BB [[144,12,12]]", "surface code"), its
    short name ("bb144"), or an exact release id.  Unique or refused, with the boards there are."""
    boards = list_boards(extra)
    if not ref or not str(ref).strip():
        raise ReleaseError("name a board: " + "; ".join(b.title for b in boards))
    ref = str(ref).strip()
    if "@" in ref:
        return find_release(ref, extra)
    want = _norm(ref)
    for test in (lambda b: _norm(b.title) == want or _norm(b.manifest["task"]) == want,
                 lambda b: want in _norm(b.title) or want in _norm(b.manifest["task"])):
        hits = [b for b in boards if test(b)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            raise ReleaseError(f"{ref!r} could be: " + "; ".join(b.title for b in hits))
    raise ReleaseError(f"no board {ref!r}; the boards are: " + "; ".join(b.title for b in boards))


def physics_mismatch(arch_doc: Mapping, release: TaskRelease) -> list:
    """The fixed blocks whose values differ from the release's trusted physics."""
    ref = release.physics()
    out = []
    for b in FIXED_BLOCKS:
        if canonical_text(normalize_numbers(arch_doc.get(b))) != canonical_text(normalize_numbers(ref.get(b))):
            out.append(b)
    return out


# ------------------------------------------------------------------ the lockfile

def write_lock(root: Path, release: TaskRelease | None, workspace_id: str, *,
               evaluator: Mapping, schemas: Mapping, mode: str = "task") -> Path:
    """Version 2 (a general workspace, no board) unless a release is named: then version 1, the
    old pinned form (kept for tools that still make one)."""
    if release is None:
        lock = {"kind": "qccd.lock", "version": 2, "workspace_id": workspace_id,
                "evaluator": dict(evaluator), "schemas": dict(schemas)}
        p = Path(root) / LOCK_NAME
        p.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return p
    lock = {
        "kind": "qccd.lock", "version": 1,
        "workspace_id": workspace_id,
        "mode": mode,                       # task | exploratory
        "task": {"id": release.id, "digest": release.digest,
                 "circuit_digest": release.manifest["circuit"]["digest"],
                 "physics_digest": release.manifest["physics"]["digest"]},
        "evaluator": dict(evaluator),
        "schemas": dict(schemas),
        "numerical_policy": release.manifest.get("numerical_policy"),
    }
    p = Path(root) / LOCK_NAME
    p.write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return p


def read_lock(root: Path) -> dict:
    p = Path(root) / LOCK_NAME
    lock = strict_loads(p.read_bytes())
    if lock.get("kind") != "qccd.lock" or lock.get("version") not in (1, 2):
        raise ReleaseError(f"{p}: not a qccd.lock (v1 or v2)")
    return lock


def build_release(task: str, release: str, circuit_text: str, physics: Mapping, *,
                  title: str, description: str, out_dir: Path, metrics: list,
                  rank_by: str, starter: Mapping | None = None, stabilizers=None,
                  limits: Mapping | None = None, order: int | None = None) -> Path:
    """Write a release directory (a maintainer tool; releases are immutable once published)."""
    d = Path(out_dir) / f"{task}@{release}"
    d.mkdir(parents=True, exist_ok=False)
    (d / "circuit.qasm").write_bytes(circuit_text.encode("utf-8"))
    (d / "physics.json").write_text(
        json.dumps(normalize_numbers(dict(physics)), indent=1, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    man: dict[str, Any] = {
        "kind": "qccd.task_release", "version": 1,
        "task": task, "release": release, "title": title, "description": description,
        "track": "codesign-artifact",
        "circuit": {"file": "circuit.qasm", "digest": _sha256_file(d / "circuit.qasm")},
        "physics": {"file": "physics.json", "digest": _sha256_file(d / "physics.json"),
                    "model": "corrected", "rank_table": "qccdsim_jones",
                    "tables": ["qccdsim_jones", "transport_excitation"],
                    "fixed_blocks": list(FIXED_BLOCKS)},
        "design_variables": ["geometry", "zone_types", "control", "name", "description"],
        "allowed_transformations": ["insert:cool"],
        "required_checks": ["bundle", "device", "physics_lock", "program", "rules",
                            "certificate_binding", "lean_certificate", "semantics",
                            "correspondence", "metrics"],
        "metrics": metrics,
        "rank_by": rank_by,
        "numerical_policy": {"float_rel_tol": 1e-9, "float_abs_tol": 1e-12,
                             "time_unit": "ms", "compare": "rel_or_abs"},
        "limits": {"max_bundle_bytes": 64 * 1024 * 1024, "max_nodes": 2000,
                   "max_instructions": 500000, "lean_timeout_s": 1800,
                   "compile_timeout_s": 600},
        "evaluator": {"name": "qccd-reference-evaluator", "contract": "1"},
    }
    if limits:
        man["limits"].update(dict(limits))
    if order is not None:
        man["order"] = int(order)
    if starter:
        man["starter"] = dict(starter)
    if stabilizers:
        man["stabilizers"] = list(stabilizers)
    (d / "release.json").write_text(json.dumps(man, indent=1, sort_keys=True, ensure_ascii=False) + "\n",
                                    encoding="utf-8")
    return d
