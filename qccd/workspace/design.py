"""The design, as the studio holds it, replayed by the Python toolchain.

The browser studio's source of truth is not a device -- it is a LIST OF RECORDS
(`qccd/viz/js/editor.js`, "THE KEYSTONE"): the shipped listing split into

    geom   the builder statements (`DeviceBuilder`, `d.site`, ...) before the seal
    seed   exactly one seal (`blank`, `blank_device`, `from_template`, `from_device`)
    post   the retunes that follow the seal
    edits  what was done since: `{method,...}`, `{topology:{op,args}}`, `{build:{...}}`

and `replay()` rebuilds the architecture from scratch every time.  The authored program
is a second list, `program.calls`.  A saved studio file (`kind: "qccd.studio"`) is these
lists plus the architecture the browser serialized from them.

This module is the SAME replay in Python, through the whitelists the toolchain already
owns -- `qccd.arch.edit.apply_program` / `apply_call` for statements and
`qccd.arch.edit.apply_edit` for topology, whose JS mirror `tests/test_edit_parity.py`
holds equal.  It never trusts the `arch` a client serialized: the workspace recomputes
the architecture from the records, so the design a human edited in the browser and the
design an agent edited through MCP are one object built by one applier.

Nothing here knows about HTTP, MCP, SQLite or any agent.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from ..arch import validate_document
from ..arch.device import Architecture
from ..arch.edit import (BUILD, MUTATE, SEED, EditError, TopologyError, apply_call,
                         apply_edit, apply_program)
from .jsonsafe import canonical_text, digest

__all__ = [
    "STUDIO_KIND",
    "DesignRefused",
    "DesignState",
    "Replayed",
    "base_from_arch",
    "empty_design",
    "replay",
    "entities",
    "diff_designs",
    "touched_entities",
    "record_kind",
]

STUDIO_KIND = "qccd.studio"

#: meta keys a record may carry.  Anything else a client puts in `meta` is dropped when the
#: record enters the workspace, so a record cannot smuggle state past the applier.
_META_KEYS = ("group", "label", "src", "change_set", "author")


class DesignRefused(ValueError):
    """A design the toolchain cannot build at all (a structural refusal, not a draft).

    `problems` is a list of `{code, message, index?, path?}`; `index` points into the
    records that were being applied when the refusal happened.
    """

    def __init__(self, message: str, problems: Sequence[Mapping] = ()):
        super().__init__(message)
        self.problems = [dict(p) for p in problems]


def record_kind(rec: Mapping) -> str:
    """`build` | `topology` | `mutate` | `seed` -- which applier a record goes through."""
    if "build" in rec:
        return "build"
    if "topology" in rec:
        return "topology"
    method = str(rec.get("method", ""))
    if method in BUILD:
        return "build"
    if method in SEED:
        return "seed"
    return "mutate"


def _clean_call(call: Mapping, where: str) -> dict:
    if not isinstance(call, Mapping):
        raise DesignRefused(f"{where}: a call record must be an object",
                            [{"code": "bad_record", "message": f"{where} is not an object"}])
    method = call.get("method")
    if not isinstance(method, str) or not method:
        raise DesignRefused(f"{where}: missing method",
                            [{"code": "bad_record", "message": f"{where} has no method"}])
    args = call.get("args", [])
    kwargs = call.get("kwargs", {})
    if not isinstance(args, list) or not isinstance(kwargs, dict):
        raise DesignRefused(f"{where}: args must be a list and kwargs an object",
                            [{"code": "bad_record", "message": f"{where}: args/kwargs shape"}])
    return {"method": method, "args": copy.deepcopy(args), "kwargs": copy.deepcopy(kwargs)}


def clean_edit(rec: Mapping, where: str = "edit") -> dict:
    """Normalise one edit record to exactly the three shapes the studio emits."""
    if not isinstance(rec, Mapping):
        raise DesignRefused(f"{where}: not an object",
                            [{"code": "bad_record", "message": f"{where} is not an object"}])
    meta = rec.get("meta") or {}
    meta = {k: meta[k] for k in _META_KEYS if isinstance(meta, Mapping) and k in meta}
    if "build" in rec:
        out: dict = {"build": _clean_call(rec["build"], where + ".build")}
        if out["build"]["method"] not in BUILD:
            raise DesignRefused(f"{where}: {out['build']['method']!r} is not a builder verb",
                                [{"code": "unknown_method", "message": out["build"]["method"]}])
    elif "topology" in rec:
        t = rec["topology"]
        if not isinstance(t, Mapping) or not isinstance(t.get("op"), str) \
                or not isinstance(t.get("args", {}), Mapping):
            raise DesignRefused(f"{where}: topology must be {{op, args}}",
                                [{"code": "bad_record", "message": f"{where}.topology shape"}])
        out = {"topology": {"op": t["op"], "args": copy.deepcopy(dict(t.get("args", {})))}}
    else:
        out = _clean_call(rec, where)
        if out["method"] not in MUTATE:
            raise DesignRefused(
                f"{where}: {out['method']!r} is not an edit the studio can apply",
                [{"code": "unknown_method",
                  "message": f"{out['method']!r} is not one of {sorted(MUTATE)}"}])
    if meta:
        out["meta"] = meta
    return out


@dataclass
class DesignState:
    """The authoritative, revisioned design inputs.

    `geom/seed/post/edits` describe the device exactly as the studio does; `program` is
    the authored program as `{method,args,kwargs}` records (the studio's Write lane);
    `final_program` optionally names a compiled TSIR artifact by digest -- the hardware
    program a submission is graded on when it was produced by a compiler rather than
    authored.
    """

    geom: list = field(default_factory=list)
    seed: dict | None = None
    post: list = field(default_factory=list)
    edits: list = field(default_factory=list)
    program: list = field(default_factory=list)
    final_program: dict | None = None

    # -- (de)serialisation --------------------------------------------------------

    @classmethod
    def from_studio(cls, doc: Mapping) -> "DesignState":
        """From a `qccd.studio` document (the browser's `EDITOR.snapshot()` / saved file)."""
        if not isinstance(doc, Mapping) or doc.get("kind") != STUDIO_KIND:
            raise DesignRefused("not a qccd.studio document",
                                [{"code": "bad_document", "message": "kind != qccd.studio"}])
        version = doc.get("version", 1)
        if version != 1:
            raise DesignRefused(f"qccd.studio version {version!r} is not supported",
                                [{"code": "bad_version", "message": str(version)}])
        geom = [_clean_call(c, f"geom[{i}]") for i, c in enumerate(doc.get("geom") or [])]
        seed = _clean_call(doc["seed"], "seed") if doc.get("seed") else None
        post = [_clean_call(c, f"post[{i}]") for i, c in enumerate(doc.get("post") or [])]
        edits = [clean_edit(e, f"edits[{i}]") for i, e in enumerate(doc.get("edits") or [])]
        prog = [_clean_call(c, f"program.calls[{i}]")
                for i, c in enumerate(((doc.get("program") or {}).get("calls")) or [])]
        final = doc.get("final_program")
        return cls(geom=geom, seed=seed, post=post, edits=edits, program=prog,
                   final_program=dict(final) if isinstance(final, Mapping) else None)

    def to_studio(self, arch_doc: Mapping | None = None) -> dict:
        """The `qccd.studio` document `EDITOR.restore()` accepts."""
        out = {
            "kind": STUDIO_KIND, "version": 1,
            "arch": dict(arch_doc) if arch_doc is not None else None,
            "program": {"calls": copy.deepcopy(self.program)},
            "geom": copy.deepcopy(self.geom), "seed": copy.deepcopy(self.seed),
            "post": copy.deepcopy(self.post), "edits": copy.deepcopy(self.edits),
            "saved_at_frame": 0,
        }
        if self.final_program:
            out["final_program"] = dict(self.final_program)
        return out

    def to_json(self) -> dict:
        return {"geom": self.geom, "seed": self.seed, "post": self.post,
                "edits": self.edits, "program": self.program,
                "final_program": self.final_program}

    @classmethod
    def from_json(cls, d: Mapping) -> "DesignState":
        return cls.from_studio({"kind": STUDIO_KIND, "version": 1, "geom": d.get("geom"),
                                "seed": d.get("seed"), "post": d.get("post"),
                                "edits": d.get("edits"),
                                "program": {"calls": d.get("program") or []},
                                "final_program": d.get("final_program")})

    def copy(self) -> "DesignState":
        return DesignState.from_json(copy.deepcopy(self.to_json()))

    def base_calls(self) -> list:
        """The studio's `baseCallsFrom`: geom, then hoisted build edits, seed, post."""
        out = list(self.geom)
        out.extend(e["build"] for e in self.edits if "build" in e)
        if self.seed:
            out.append(self.seed)
        return out + list(self.post)

    def input_digest(self) -> str:
        """Digest of the records with `meta` stripped: two designs built by the same
        statements are the same input however they were grouped or attributed."""
        def strip(rec):
            return {k: v for k, v in rec.items() if k != "meta"}
        return digest({"geom": self.geom, "seed": self.seed, "post": self.post,
                       "edits": [strip(e) for e in self.edits], "program": self.program,
                       "final_program": self.final_program})


@dataclass
class Replayed:
    """The outcome of replaying a `DesignState`."""

    state: DesignState
    arch: Architecture | None
    arch_doc: dict | None
    problems: list          # [{code, message, index?}] -- per-edit refusals
    reports: dict           # edit index -> EditReport json, for topology edits

    @property
    def ok(self) -> bool:
        return self.arch is not None and not self.problems

    def arch_digest(self) -> str | None:
        return digest(self.arch_doc) if self.arch_doc is not None else None


def base_from_arch(arch: Architecture, policy=None) -> DesignState:
    """The records the studio page starts from for `arch` (its `splitListing()`).

    `render_html` ships `architecture_listing(arch, mode="full", ...)` and the page splits
    its call lines into geom/seed/post; this is that split, so a workspace seeded from an
    architecture holds exactly what a page rendered from it holds.
    """
    from ..arch.listing import architecture_listing

    lines = architecture_listing(arch, mode="full", policy=policy,
                                 verify=False).to_json(refs=False)["lines"]
    st = DesignState()
    for ln in lines:
        if ln.get("kind") != "call" or not ln.get("call"):
            continue
        c = _clean_call(ln["call"], "listing")
        kind = record_kind(c)
        if st.seed is None and kind == "build":
            st.geom.append(c)
        elif st.seed is None and kind == "seed":
            st.seed = c
        else:
            st.post.append(c)
    return st


def empty_design(name: str = "design", template: str | None = None) -> DesignState:
    """The blank canvas `python -m qccd studio` opens on, as records."""
    from ..api import Machine
    from ..arch.builder import DeviceBuilder

    m = Machine.from_device(DeviceBuilder("explicit").build(), name=name, template=template)
    return base_from_arch(m.arch)


def replay(state: DesignState, *, strict: bool = False) -> Replayed:
    """Rebuild the architecture from the records, exactly as `editor.js::replay` does.

    A base program that does not replay is a structural refusal (`DesignRefused`).  An
    individual edit that is refused is recorded in `problems` and skipped -- the same
    policy as the page -- unless `strict`, in which case the first refusal raises.
    """
    try:
        machine = apply_program(state.base_calls())
    except EditError as exc:
        raise DesignRefused(f"the base statements do not replay: {exc}",
                            [{"code": exc.code, "message": str(exc), "index": exc.index,
                              "where": "base"}]) from None
    problems: list = []
    reports: dict = {}
    for i, rec in enumerate(state.edits):
        if "build" in rec:
            continue
        try:
            if "topology" in rec:
                dev, rep = apply_edit(machine.arch.device, rec["topology"])
                machine._rebuild(device=dev)
                reports[i] = rep.to_json()
            else:
                machine, _ = apply_call((machine, None), rec)
        except (EditError, TopologyError, KeyError, ValueError, TypeError) as exc:
            p = {"code": getattr(exc, "code", None) or type(exc).__name__,
                 "message": str(exc), "index": i}
            if strict:
                raise DesignRefused(f"edit {i} was refused: {exc}", [p]) from None
            problems.append(p)
    arch = machine.arch
    try:
        doc = arch.to_json(expanded=True)
    except Exception as exc:  # pragma: no cover - serialisation of a replayed machine
        raise DesignRefused(f"the replayed architecture does not serialise: {exc}",
                            [{"code": "serialise", "message": str(exc)}]) from None
    errs = validate_document(doc)
    for e in errs:
        p = {"code": "schema", "message": e, "path": e.split(":")[0]}
        if strict:
            raise DesignRefused(f"the design is not a loadable document: {e}", [p])
        problems.append(p)
    if not errs:
        # the loader is stricter than the schema walk (undeclared zones, structure)
        try:
            Architecture.from_json(doc)
        except (ValueError, KeyError) as exc:
            p = {"code": "structure", "message": str(exc)}
            if strict:
                raise DesignRefused(f"the design does not load: {exc}", [p]) from None
            problems.append(p)
    return Replayed(state=state, arch=arch, arch_doc=doc, problems=problems,
                    reports=reports)


# ------------------------------------------------------------------ entities and diffs

def entities(arch_doc: Mapping) -> dict:
    """Every addressable entity of an expanded architecture document, by stable key.

    Keys are `node:<id>`, `segment:<id>`, `loop:<id>`, `zone:<name>` and `block:<name>`
    (primitives, control, heating, species, budget, description).  Ids are the device's
    own ids: the studio has no rename verb, so an id IS the identity, and a label change
    (a zone assignment, a capacity) never changes it.
    """
    out: dict = {}
    geo = arch_doc.get("geometry") or {}
    for n in geo.get("nodes") or []:
        rec = {k: n.get(k) for k in ("id", "kind", "pos", "zone_type", "capacity", "labels")}
        out[f"node:{n['id']}"] = rec
    for s in geo.get("segments") or []:
        rec = {k: s.get(k) for k in ("id", "ends", "length", "capacity", "loop", "labels")}
        out[f"segment:{s['id']}"] = rec
    for lp in geo.get("loops") or []:
        out[f"loop:{lp['id']}"] = {k: lp.get(k) for k in ("id", "nodes", "closed", "kind")}
    for z, zt in (arch_doc.get("zone_types") or {}).items():
        out[f"zone:{z}"] = dict(zt)
    for b in ("primitives", "control", "heating", "species", "budget", "description"):
        if b in arch_doc:
            out[f"block:{b}"] = arch_doc[b]
    return out


def _endpoints(ent: Mapping) -> tuple:
    ends = ent.get("ends") or ()
    return tuple(ends) if isinstance(ends, (list, tuple)) else ()


def diff_designs(before: Replayed | None, after: Replayed) -> dict:
    """A structured diff: which entities were added, removed or changed, and how.

    `changed[key]` lists the fields whose values differ.  Program records are compared
    as a list.  The diff is what a change set reports back and what conflict detection
    and protection read -- it is computed from the REPLAYED documents, so it reports
    what an edit did rather than what its record claimed to do.
    """
    a = entities(before.arch_doc) if before and before.arch_doc else {}
    b = entities(after.arch_doc) if after.arch_doc else {}
    added = sorted(set(b) - set(a))
    removed = sorted(set(a) - set(b))
    changed = {}
    for k in sorted(set(a) & set(b)):
        if canonical_text(a[k]) != canonical_text(b[k]):
            if isinstance(a[k], Mapping) and isinstance(b[k], Mapping):
                fields = sorted(f for f in set(a[k]) | set(b[k])
                                if canonical_text(a[k].get(f)) != canonical_text(b[k].get(f)))
            else:
                fields = ["value"]
            changed[k] = fields
    prog_a = before.state.program if before else []
    prog_b = after.state.program
    fin_a = before.state.final_program if before else None
    fin_b = after.state.final_program
    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "program_changed": canonical_text(prog_a) != canonical_text(prog_b),
        "final_program_changed": canonical_text(fin_a) != canonical_text(fin_b),
        "summary": _diff_summary(added, removed, changed,
                                 canonical_text(prog_a) != canonical_text(prog_b)),
    }


def _diff_summary(added, removed, changed, prog) -> str:
    def count(keys, kind):
        return sum(1 for k in keys if k.startswith(kind + ":"))
    parts = []
    for verb, keys in (("added", added), ("removed", removed), ("changed", list(changed))):
        bits = [f"{count(keys, k)} {k}{'s' if count(keys, k) != 1 else ''}"
                for k in ("node", "segment", "loop", "zone", "block") if count(keys, k)]
        if bits:
            parts.append(f"{verb} " + ", ".join(bits))
    if prog:
        parts.append("program changed")
    return "; ".join(parts) or "no change"


def touched_entities(diff: Mapping, before: Replayed | None = None,
                     after: Replayed | None = None) -> set:
    """The entity keys a change touched: added, removed, changed -- plus the endpoints of
    every added or removed segment, because connecting to a node changes that node's
    neighbourhood even though its own record is unchanged."""
    keys = set(diff.get("added", ())) | set(diff.get("removed", ())) | set(diff.get("changed", {}))
    ents = {}
    if before and before.arch_doc:
        ents.update(entities(before.arch_doc))
    if after and after.arch_doc:
        ents.update(entities(after.arch_doc))
    for k in list(keys):
        if k.startswith("segment:") and k in ents:
            for n in _endpoints(ents[k]):
                keys.add(f"node:{n}#incident")
    if diff.get("program_changed"):
        keys.add("program")
    if diff.get("final_program_changed"):
        keys.add("final_program")
    return keys


def entity_summary(arch_doc: Mapping, keys: Iterable[str] | None = None,
                   limit: int = 200) -> list:
    """A bounded list of entity records for context payloads."""
    ents = entities(arch_doc)
    chosen = [k for k in (keys if keys is not None else ents) if k in ents]
    return [{"key": k, **(ents[k] if isinstance(ents[k], Mapping) else {"value": ents[k]})}
            for k in chosen[:limit]]
