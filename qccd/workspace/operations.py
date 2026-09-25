"""The semantic operations a change set may carry.

Every operation COMPILES to the records the studio already speaks -- `{topology:{op,args}}`
for graph edits (`qccd.arch.edit.OPS`), `{method,args,kwargs}` for retunes
(`qccd.arch.edit.MUTATE`), a replaced base for a new device, and `{method,...}` program
records (`qccd.api.PROGRAM_METHODS`) -- and those records then go through the one
applier in `design.replay`.  Nothing here moves a node or prices a design; an operation
is a small, typed front end over the toolchain's own whitelists.

The registry `OPERATIONS` is data: each entry names its parameters, and the machine-
readable signatures an agent reads (`describe_operations()`) are derived from it rather
than written a second time in the skill.

Operations are checked for SHAPE here (types, required fields, id grammar).  Whether the
result is a legal device is `design.replay`'s job, and whether it is a GOOD device is the
evaluator's; a well-formed edit that produces a physically invalid draft is accepted with
diagnostics.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from ..arch.edit import MUTATE
from .design import DesignRefused, DesignState, Replayed, base_from_arch, clean_edit

__all__ = ["OPERATIONS", "OperationError", "compile_operations", "describe_operations",
           "GENERATORS", "PROGRAM_VERBS"]

#: `qccd/arch/schema.py` ID_PATTERN, restated as a check on input, not a second schema:
#: the replay re-validates every id against the real schema anyway.
_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_.:-]*$")

#: generators a `construct` may name -- the ones `Machine` has a constructor for
GENERATORS = ("ring", "grid", "chain", "ladder", "racetrack", "dual_loop")


def _program_verbs() -> tuple:
    from ..api import PROGRAM_METHODS
    return tuple(PROGRAM_METHODS)


PROGRAM_VERBS = _program_verbs()


class OperationError(ValueError):
    """A malformed or structurally impossible operation (not a draft-quality problem)."""

    def __init__(self, code: str, message: str, index: int | None = None, op: str = ""):
        super().__init__(message)
        self.code = code
        self.index = index
        self.op = op

    def to_json(self) -> dict:
        return {"code": self.code, "message": str(self), "index": self.index, "op": self.op}


@dataclass(frozen=True)
class OpSpec:
    name: str
    summary: str
    params: Mapping[str, str]            # name -> "type[?]" (a trailing ? = optional)
    compile: Callable
    category: str = "device"             # device | program | constraint | sync


def _need(op: Mapping, key: str, kind, where: str):
    if key not in op:
        raise OperationError("missing_field", f"{where}: missing {key!r}")
    v = op[key]
    if kind is float:
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise OperationError("bad_type", f"{where}: {key!r} must be a number")
        return float(v)
    if kind is int:
        if isinstance(v, bool) or not isinstance(v, int):
            raise OperationError("bad_type", f"{where}: {key!r} must be an integer")
        return v
    if not isinstance(v, kind):
        raise OperationError("bad_type", f"{where}: {key!r} must be {kind.__name__}")
    return v


def _id(v: Any, where: str) -> str:
    if not isinstance(v, str) or not _ID.match(v):
        raise OperationError("bad_id", f"{where}: {v!r} is not a usable id (letters, digits, _ . : -)")
    return v


def _pos(v: Any, where: str) -> list:
    if (not isinstance(v, (list, tuple)) or len(v) != 2
            or any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in v)):
        raise OperationError("bad_type", f"{where}: pos must be [x, y]")
    return [float(v[0]), float(v[1])]


def _ids(v: Any, where: str) -> list:
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)) or not v:
        raise OperationError("bad_type", f"{where}: expected one id or a non-empty list of ids")
    return [_id(x, where) for x in v]


class _Ctx:
    """What an operation compiles against: the state being built and its last replay."""

    def __init__(self, state: DesignState, replayed: Replayed | None, actor: Mapping):
        self.state = state
        self.replayed = replayed
        self.actor = actor
        self.fresh_taken: set = set()

    def arch_doc(self) -> dict:
        if self.replayed is None or self.replayed.arch_doc is None:
            from .design import replay
            self.replayed = replay(self.state)
        return self.replayed.arch_doc

    def node_ids(self) -> set:
        geo = self.arch_doc().get("geometry") or {}
        return {n["id"] for n in geo.get("nodes") or []}

    def seg_ids(self) -> set:
        geo = self.arch_doc().get("geometry") or {}
        return {s["id"] for s in geo.get("segments") or []}

    def zone_types(self) -> dict:
        return copy.deepcopy(self.arch_doc().get("zone_types") or {})

    def fresh(self, prefix: str) -> str:
        taken = self.node_ids() | self.seg_ids() | self.fresh_taken
        k = 0
        while f"{prefix}{k}" in taken:
            k += 1
        self.fresh_taken.add(f"{prefix}{k}")
        return f"{prefix}{k}"

    def push(self, rec: dict) -> None:
        self.state.edits.append(rec)
        self.replayed = None      # the next lookup replays


def _meta(ctx: _Ctx, label: str) -> dict:
    return {"label": label, "src": str(ctx.actor.get("kind", "agent"))}


# ------------------------------------------------------------------ device operations

def _op_add_site(ctx: _Ctx, op: Mapping, where: str) -> None:
    kind = op.get("kind", "site")
    nid = _id(op["id"], where) if op.get("id") is not None else ctx.fresh("J" if kind == "junction" else "N")
    args: dict = {"id": nid, "pos": _pos(op.get("pos"), where), "labels": list(op.get("labels") or [])}
    if op.get("on") is not None:
        args["on"] = _id(op["on"], where)
    if op.get("to"):
        args["to"] = _ids(op["to"], where)
    if kind == "junction":
        ctx.push({"topology": {"op": "add_junction", "args": args}, "meta": _meta(ctx, "add junction")})
        return
    if kind != "site":
        raise OperationError("bad_value", f"{where}: kind must be 'site' or 'junction'")
    zone = op.get("zone")
    args["zone"] = zone
    cap = op.get("capacity", 0 if zone else 1)
    if isinstance(cap, bool) or not isinstance(cap, int) or cap < 0:
        raise OperationError("bad_value", f"{where}: capacity must be a non-negative integer")
    args["capacity"] = cap
    args["zone_types"] = ctx.zone_types()
    ctx.push({"topology": {"op": "add_site", "args": args}, "meta": _meta(ctx, "add site")})


def _op_add_segment(ctx: _Ctx, op: Mapping, where: str) -> None:
    sid = _id(op["id"], where) if op.get("id") is not None else ctx.fresh("X")
    args = {"id": sid, "a": _id(op.get("a"), where), "b": _id(op.get("b"), where)}
    if op.get("length") is not None:
        args["length"] = _need(op, "length", float, where)
    if op.get("capacity") is not None:
        args["capacity"] = _need(op, "capacity", int, where)
    if op.get("loop") is not None:
        args["loop"] = _id(op["loop"], where)
    ctx.push({"topology": {"op": "add_segment", "args": args}, "meta": _meta(ctx, "connect")})


def _op_remove_node(ctx: _Ctx, op: Mapping, where: str) -> None:
    mend = op.get("mend", "splice")
    if mend not in ("splice", "open", "delete"):
        raise OperationError("bad_value", f"{where}: mend must be splice|open|delete")
    ctx.push({"topology": {"op": "remove_node", "args": {
        "id": _id(op.get("id"), where), "mend": mend, "cascade": bool(op.get("cascade", True))}},
        "meta": _meta(ctx, "remove node")})


def _op_remove_segment(ctx: _Ctx, op: Mapping, where: str) -> None:
    on_loop = op.get("on_loop", "open")
    if on_loop not in ("refuse", "open", "delete"):
        raise OperationError("bad_value", f"{where}: on_loop must be refuse|open|delete")
    ctx.push({"topology": {"op": "remove_segment", "args": {
        "id": _id(op.get("id"), where), "on_loop": on_loop}}, "meta": _meta(ctx, "remove segment")})


def _op_move_site(ctx: _Ctx, op: Mapping, where: str) -> None:
    x, y = _pos(op.get("pos"), where)
    ctx.push({"method": "move_site", "args": [_id(op.get("id"), where), x, y], "kwargs": {},
              "meta": _meta(ctx, "move")})


def _op_set_site_capacity(ctx: _Ctx, op: Mapping, where: str) -> None:
    ids = _ids(op.get("ids", op.get("id")), where)
    cap = _need(op, "capacity", int, where)
    ctx.push({"method": "set_site_capacity", "args": [ids, cap], "kwargs": {},
              "meta": _meta(ctx, "capacity")})


def _op_set_segment_length(ctx: _Ctx, op: Mapping, where: str) -> None:
    ids = _ids(op.get("ids", op.get("id")), where)
    ctx.push({"method": "set_segment_length", "args": [ids, _need(op, "length", float, where)],
              "kwargs": {}, "meta": _meta(ctx, "length")})


def _op_set_zone(ctx: _Ctx, op: Mapping, where: str) -> None:
    zone = _id(op.get("zone"), where)
    fields = op.get("fields") or {}
    if not isinstance(fields, Mapping) or not fields:
        raise OperationError("bad_type", f"{where}: fields must be a non-empty object")
    ctx.push({"method": "set_zone", "args": [zone], "kwargs": copy.deepcopy(dict(fields)),
              "meta": _meta(ctx, "zone")})


def _op_call(ctx: _Ctx, op: Mapping, where: str) -> None:
    """Any retune the toolchain whitelists (`qccd.arch.edit.MUTATE`), verbatim."""
    method = op.get("method")
    if method not in MUTATE:
        raise OperationError("unknown_method", f"{where}: {method!r} is not one of {sorted(MUTATE)}")
    args = op.get("args", [])
    kwargs = op.get("kwargs", {})
    if not isinstance(args, list) or not isinstance(kwargs, Mapping):
        raise OperationError("bad_type", f"{where}: args must be a list, kwargs an object")
    ctx.push({"method": method, "args": copy.deepcopy(args), "kwargs": copy.deepcopy(dict(kwargs)),
              "meta": _meta(ctx, method)})


def _op_construct(ctx: _Ctx, op: Mapping, where: str) -> None:
    """Replace the device with a generated one (the studio's gallery pick)."""
    from ..api import Machine

    gen = op.get("generator")
    if gen not in GENERATORS:
        raise OperationError("bad_value", f"{where}: generator must be one of {list(GENERATORS)}")
    params = op.get("params") or {}
    if not isinstance(params, Mapping):
        raise OperationError("bad_type", f"{where}: params must be an object")
    name = op.get("name") or "design"
    try:
        m = getattr(Machine, gen)(**dict(params), name=_id(name, where))
    except (TypeError, ValueError, KeyError) as exc:
        raise OperationError("bad_params", f"{where}: {gen}({dict(params)}) refused: {exc}") from None
    base = base_from_arch(m.arch)
    ctx.state.geom, ctx.state.seed, ctx.state.post = base.geom, base.seed, base.post
    ctx.state.edits = []
    ctx.replayed = None


def _op_add_chain(ctx: _Ctx, op: Mapping, where: str) -> None:
    """`count` sites from `start` in steps of `step`, joined in a line.

    `attach_to` joins the first site to an existing node; `close` joins the last back to
    the first.  Deterministic ids `<prefix><k>` and segments `<prefix><k>_<k+1>`.
    """
    prefix = _id(op.get("prefix", "C"), where)
    count = _need(op, "count", int, where)
    if not 1 <= count <= 2000:
        raise OperationError("bad_value", f"{where}: count must be 1..2000")
    x0, y0 = _pos(op.get("start"), where)
    dx, dy = _pos(op.get("step", [1, 0]), where)
    zone = op.get("zone")
    ids = [f"{prefix}{k}" for k in range(count)]
    taken = ctx.node_ids() | ctx.seg_ids()
    clash = [i for i in ids if i in taken]
    if clash:
        raise OperationError("id_taken", f"{where}: ids already in use: {clash[:5]}")
    for k, nid in enumerate(ids):
        sub = {"id": nid, "pos": [x0 + k * dx, y0 + k * dy], "zone": zone,
               "labels": list(op.get("labels") or [])}
        if op.get("capacity") is not None:
            sub["capacity"] = op["capacity"]
        _op_add_site(ctx, sub, where)
    for k in range(count - 1):
        _op_add_segment(ctx, {"id": f"{prefix}{k}_{k + 1}", "a": ids[k], "b": ids[k + 1]}, where)
    if op.get("attach_to"):
        _op_add_segment(ctx, {"id": f"{prefix}_attach", "a": _id(op["attach_to"], where), "b": ids[0]}, where)
    if op.get("close") and count > 2:
        _op_add_segment(ctx, {"id": f"{prefix}{count - 1}_0", "a": ids[-1], "b": ids[0]}, where)


def _op_add_grid(ctx: _Ctx, op: Mapping, where: str) -> None:
    """A rows x cols lattice of sites with nearest-neighbour segments."""
    prefix = _id(op.get("prefix", "G"), where)
    rows, cols = _need(op, "rows", int, where), _need(op, "cols", int, where)
    if not (1 <= rows <= 100 and 1 <= cols <= 100):
        raise OperationError("bad_value", f"{where}: rows and cols must be 1..100")
    x0, y0 = _pos(op.get("origin", [0, 0]), where)
    pitch = float(op.get("pitch", 2.0))
    zone = op.get("zone")
    name = lambda r, c: f"{prefix}{r}_{c}"
    taken = ctx.node_ids() | ctx.seg_ids()
    if any(name(r, c) in taken for r in range(rows) for c in range(cols)):
        raise OperationError("id_taken", f"{where}: grid ids with prefix {prefix!r} already in use")
    for r in range(rows):
        for c in range(cols):
            _op_add_site(ctx, {"id": name(r, c), "pos": [x0 + c * pitch, y0 + r * pitch],
                               "zone": zone}, where)
    for r in range(rows):
        for c in range(cols):
            if c + 1 < cols:
                _op_add_segment(ctx, {"id": f"{name(r, c)}h", "a": name(r, c), "b": name(r, c + 1)}, where)
            if r + 1 < rows:
                _op_add_segment(ctx, {"id": f"{name(r, c)}v", "a": name(r, c), "b": name(r + 1, c)}, where)


def _op_replicate(ctx: _Ctx, op: Mapping, where: str) -> None:
    """Copy a set of nodes, and the segments among them, by an offset.

    The generalisation primitive for "apply the same arrangement to that module": the
    copied nodes keep zone, capacity, kind and labels; ids get `suffix`; `attach` maps a
    copied node id to an existing node it should be joined to.
    """
    src = _ids(op.get("nodes"), where)
    dx, dy = _pos(op.get("offset"), where)
    suffix = str(op.get("suffix", "_copy"))
    if not re.match(r"^[A-Za-z0-9_.:-]+$", suffix):
        raise OperationError("bad_value", f"{where}: suffix may hold only letters, digits, _ . : -")
    doc = ctx.arch_doc()
    geo = doc.get("geometry") or {}
    nodes = {n["id"]: n for n in geo.get("nodes") or []}
    missing = [n for n in src if n not in nodes]
    if missing:
        raise OperationError("unknown_entity", f"{where}: no such nodes {missing[:5]}")
    new = {n: f"{n}{suffix}" for n in src}
    taken = ctx.node_ids() | ctx.seg_ids()
    if any(v in taken for v in new.values()):
        raise OperationError("id_taken", f"{where}: suffix {suffix!r} collides with existing ids")
    for nid in src:
        n = nodes[nid]
        pos = [float(n["pos"][0]) + dx, float(n["pos"][1]) + dy]
        if n.get("kind") == "junction":
            _op_add_site(ctx, {"id": new[nid], "pos": pos, "kind": "junction"}, where)
        else:
            _op_add_site(ctx, {"id": new[nid], "pos": pos, "zone": n.get("zone_type"),
                               "capacity": int(n.get("capacity") or 0) if n.get("zone_type") is None
                               else 0, "labels": [l for l in (n.get("labels") or [])
                                                  if not str(l).startswith("cmp:")]}, where)
    for s in geo.get("segments") or []:
        a, b = s["ends"]
        if a in new and b in new:
            _op_add_segment(ctx, {"id": f"{s['id']}{suffix}", "a": new[a], "b": new[b],
                                  "length": s.get("length"), "capacity": s.get("capacity")}, where)
    for copied, target in (op.get("attach") or {}).items():
        if copied not in new:
            raise OperationError("unknown_entity", f"{where}: attach names {copied!r}, which is not copied")
        _op_add_segment(ctx, {"a": new[copied], "b": _id(target, where)}, where)


# ------------------------------------------------------------------ program operations

def _check_calls(calls: Any, where: str) -> list:
    if not isinstance(calls, list):
        raise OperationError("bad_type", f"{where}: calls must be a list")
    out = []
    for i, c in enumerate(calls):
        if not isinstance(c, Mapping) or c.get("method") not in PROGRAM_VERBS:
            raise OperationError("unknown_method",
                                 f"{where}: calls[{i}] must name one of {list(PROGRAM_VERBS)}")
        args, kwargs = c.get("args", []), c.get("kwargs", {})
        if not isinstance(args, list) or not isinstance(kwargs, Mapping):
            raise OperationError("bad_type", f"{where}: calls[{i}] args/kwargs shape")
        out.append({"method": c["method"], "args": copy.deepcopy(args),
                    "kwargs": copy.deepcopy(dict(kwargs))})
    return out


def _op_set_program(ctx: _Ctx, op: Mapping, where: str) -> None:
    ctx.state.program = _check_calls(op.get("calls"), where)


def _op_append_program(ctx: _Ctx, op: Mapping, where: str) -> None:
    ctx.state.program = ctx.state.program + _check_calls(op.get("calls"), where)


_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


def _op_set_final_program(ctx: _Ctx, op: Mapping, where: str) -> None:
    """Point the design at a compiled TSIR artifact (by digest) or clear it (null).

    `certified` and `certificate` name the compiler's own output and its certificate, so
    a submission can tie the graded program back to what was checked; `compiled_for` is
    the architecture digest it was compiled against, so a later device edit shows the
    program as stale instead of silently pairing it with a different device."""
    ref = op.get("artifact")
    if ref is None:
        ctx.state.final_program = None
        return
    out = {}
    for k in ("artifact", "certified", "certificate", "compiled_for", "circuit_digest"):
        v = op.get(k)
        if v is None and k != "artifact":
            continue
        if not isinstance(v, str) or not _DIGEST.match(v):
            raise OperationError("bad_value", f"{where}: {k} must be a sha256:<hex> digest")
        out[k] = v
    if op.get("compiler") is not None:
        if op["compiler"] not in ("compile", "rotate", "external"):
            raise OperationError("bad_value", f"{where}: compiler must be compile, rotate or external")
        out["compiler"] = op["compiler"]
    if op.get("board") is not None:
        # the board whose circuit this program realises (a release id); submissions to another board
        # compile again rather than grade a program for a different circuit
        import re as _re
        if not isinstance(op["board"], str) or not _re.match(r"^[a-z0-9_]{1,40}@[0-9]{1,6}$", op["board"]):
            raise OperationError("bad_value", f"{where}: board must be a release id like bb144@1")
        out["board"] = op["board"]
    out["label"] = str(op.get("label", ""))[:200]
    ctx.state.final_program = out


# ------------------------------------------------------------------ browser sync

def _op_studio_sync(ctx: _Ctx, op: Mapping, where: str) -> None:
    """The browser's own edits, as the records `EDITOR` holds.

    `remove` lists records (by value) that were taken back -- the page's undo --, `append`
    the records added since the base revision, `base` a new device (the gallery, a
    canvas op), `program` the Write lane's records.  Records are normalised to the three
    studio shapes by `design.clean_edit`, so a page cannot carry anything else in.
    """
    st = ctx.state
    if op.get("base") is not None:
        b = op["base"]
        if not isinstance(b, Mapping):
            raise OperationError("bad_type", f"{where}: base must be an object")
        fresh = DesignState.from_json({"geom": b.get("geom"), "seed": b.get("seed"),
                                       "post": b.get("post"), "edits": []})
        st.geom, st.seed, st.post, st.edits = fresh.geom, fresh.seed, fresh.post, []
    for i, rec in enumerate(op.get("remove") or []):
        target = clean_edit(rec, f"{where}.remove[{i}]")
        from .jsonsafe import canonical_text
        key = canonical_text(_strip_meta(target))
        for j in range(len(st.edits) - 1, -1, -1):
            if canonical_text(_strip_meta(st.edits[j])) == key:
                del st.edits[j]
                break
        else:
            raise OperationError("stale_record",
                                 f"{where}.remove[{i}]: that record is not in the design any more")
    for i, rec in enumerate(op.get("append") or []):
        st.edits.append(clean_edit(rec, f"{where}.append[{i}]"))
    if op.get("program") is not None:
        st.program = _check_calls(op["program"], f"{where}.program")
    ctx.replayed = None


def _strip_meta(rec: Mapping) -> dict:
    return {k: v for k, v in rec.items() if k not in ("meta", "_report")}


OPERATIONS: dict = {s.name: s for s in [
    OpSpec("add_site", "Add a trap site (or a junction with kind='junction').",
           {"id": "string?", "pos": "[x, y]", "kind": "'site'|'junction'?", "zone": "string?",
            "capacity": "int?", "on": "segment id?", "to": "node id[]?", "labels": "string[]?"},
           _op_add_site),
    OpSpec("add_segment", "Connect two nodes with a transport segment.",
           {"id": "string?", "a": "node id", "b": "node id", "length": "number?",
            "capacity": "int?", "loop": "loop id?"}, _op_add_segment),
    OpSpec("remove_node", "Remove a node (splice the rail through it by default).",
           {"id": "node id", "mend": "'splice'|'open'|'delete'?", "cascade": "bool?"},
           _op_remove_node),
    OpSpec("remove_segment", "Remove a segment.",
           {"id": "segment id", "on_loop": "'refuse'|'open'|'delete'?"}, _op_remove_segment),
    OpSpec("move_site", "Move a node to a new diagram position (lattice units).",
           {"id": "node id", "pos": "[x, y]"}, _op_move_site),
    OpSpec("set_site_capacity", "Set how many ions a site (or sites) may hold.",
           {"ids": "node id[]", "capacity": "int"}, _op_set_site_capacity),
    OpSpec("set_segment_length", "Set the physical length of segments (pitch units).",
           {"ids": "segment id[]", "length": "number"}, _op_set_segment_length),
    OpSpec("set_zone", "Change a zone type (capacity, gate, spam, cool...).",
           {"zone": "zone name", "fields": "object"}, _op_set_zone),
    OpSpec("call", "Any whitelisted retune verbatim (qccd.arch.edit.MUTATE).",
           {"method": "|".join(sorted(MUTATE)), "args": "list", "kwargs": "object"}, _op_call),
    OpSpec("construct", "Replace the device with a generated one.",
           {"generator": "|".join(GENERATORS), "params": "object", "name": "string?"},
           _op_construct),
    OpSpec("add_chain", "Batch: a line of sites joined by segments.",
           {"prefix": "string", "count": "int", "start": "[x, y]", "step": "[dx, dy]?",
            "zone": "string?", "capacity": "int?", "attach_to": "node id?", "close": "bool?"},
           _op_add_chain),
    OpSpec("add_grid", "Batch: a rows x cols lattice of sites.",
           {"prefix": "string", "rows": "int", "cols": "int", "origin": "[x, y]?",
            "pitch": "number?", "zone": "string?"}, _op_add_grid),
    OpSpec("replicate", "Batch: copy nodes and their internal segments by an offset.",
           {"nodes": "node id[]", "offset": "[dx, dy]", "suffix": "string?",
            "attach": "{copied id: existing node id}?"}, _op_replicate),
    OpSpec("set_program", "Replace the authored program (qccd Program verbs).",
           {"calls": "[{method, args, kwargs}]"}, _op_set_program, "program"),
    OpSpec("append_program", "Append statements to the authored program.",
           {"calls": "[{method, args, kwargs}]"}, _op_append_program, "program"),
    OpSpec("set_final_program", "Adopt a compiled program (a compile job's adopt_with), or clear it.",
           {"artifact": "sha256:<hex>|null", "certified": "sha256:<hex>?", "certificate": "sha256:<hex>?",
            "compiled_for": "sha256:<hex>?", "compiler": "'compile'|'rotate'|'external'?", "label": "string?"},
           _op_set_final_program, "program"),
    OpSpec("studio_sync", "The browser's own records (used by Studio, not by agents).",
           {"remove": "record[]?", "append": "record[]?", "base": "{geom,seed,post}?",
            "program": "call[]?"}, _op_studio_sync, "sync"),
]}

#: operations only the browser may send
BROWSER_ONLY = frozenset({"studio_sync"})


def compile_operations(base: DesignState, base_replayed: Replayed | None,
                       ops: list, actor: Mapping) -> DesignState:
    """Apply `ops` to a COPY of `base` and return the new state (not yet replayed-checked).

    Raises `OperationError` (with the index of the failing op) on malformed input.
    """
    if not isinstance(ops, list) or not ops:
        raise OperationError("empty", "a change set needs at least one operation")
    if len(ops) > 5000:
        raise OperationError("too_many", "a change set may carry at most 5000 operations")
    state = base.copy()
    ctx = _Ctx(state, base_replayed, actor)
    for i, op in enumerate(ops):
        if not isinstance(op, Mapping):
            raise OperationError("bad_type", f"operations[{i}] is not an object", i)
        name = op.get("type")
        spec = OPERATIONS.get(name)
        if spec is None:
            raise OperationError("unknown_operation",
                                 f"operations[{i}]: unknown type {name!r} (have: {sorted(OPERATIONS)})", i, str(name))
        # Studio's records come from the person's page, from an import on the file's behalf, or from an
        # agent drawing on the person's page through a page action (service.change_set: via "page")
        if name in BROWSER_ONLY and actor.get("kind") != "human" and actor.get("via") not in ("import", "page"):
            raise OperationError("forbidden", f"operations[{i}]: {name} is reserved for Studio", i, name)
        try:
            spec.compile(ctx, op, f"operations[{i}] ({name})")
        except OperationError as exc:
            exc.index = i
            exc.op = name
            raise
        except DesignRefused as exc:
            raise OperationError("bad_record", str(exc), i, name) from None
        except KeyError as exc:
            raise OperationError("missing_field", f"operations[{i}] ({name}): missing {exc}", i, name) from None
    return state


def describe_operations() -> list:
    """Machine-readable signatures, derived from the registry."""
    return [{"type": s.name, "category": s.category, "summary": s.summary,
             "params": dict(s.params), "browser_only": s.name in BROWSER_ONLY}
            for s in OPERATIONS.values()]
