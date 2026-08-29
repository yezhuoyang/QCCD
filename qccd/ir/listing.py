"""Layer 2.5 -- the disassembler.  TSIR -> a structured, foldable listing.

`qccd.ir.tsir` is the hardware program.  Nothing until now could *show* it: the CLI
printed nine aggregate numbers and the viewer drew the ions, but the object a hardware
engineer has to read -- what operation, on which participants, holding which resources,
at what point on which primitive curve -- had no rendering at all.

Two rules shape this module.

**Structure first, text second.**  `disassemble()` returns `Line` records with the
instruction's `id` on every one of them; `render_line()` is a pure function of a `Line`.
A future editor that lets a user click a listing row maps the click back to
`prog.instructions` through `Line.id` -- never by parsing a string it printed.

**Mirror the IR's own compression.**  A rigid rotation of 144 ions is ONE instruction
carrying one `loop_shift` template, so it is ONE line.  `Line.width` says 144 and
`Line.cost.cycles` says 13; the listing never expands what the IR deliberately folded.
That is PLAN §1's thesis made readable: rigid rotation shows one `ROT.CW` line where an
odd-even sort shows a column of `SORT.S` / `SORT.M`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Iterable, Iterator, Mapping, Sequence

from .tsir import TSIR, Instruction, broadcast_kind, iter_pairs

__all__ = [
    "OpPoint", "Movement", "Operand", "Cost", "GroupKey", "Line", "Section",
    "Summary", "Listing",
    "disassemble", "render_line", "render_header", "render", "quanta_trace",
    "to_view_model", "to_page_model",
    "MNEMONIC_BY_CLASS", "COLUMNS",
]

# --------------------------------------------------------------------- mnemonics

MNEMONIC_BY_TYPE = {
    "init": "INIT",
    "gate": "GATE",
    "measure": "MEAS",
    "reset": "RESET",
    "cool": "COOL",
    "barrier": "BARRIER",
    "simd": "MOVE",
}

#: SIMD class id -> mnemonic.  An unknown class falls back to its own id, upper-cased,
#: so a user-declared class shows up as itself rather than as a generic MOVE.
MNEMONIC_BY_CLASS = {
    "rotate_cw": "ROT.CW",
    "rotate_ccw": "ROT.CCW",
    "dock": "DOCK",
    "undock": "UNDOCK",
    "shuttle": "SHUT",
    "sort_split": "SORT.S",
    "sort_merge": "SORT.M",
    "swap": "SWAP",
}

#: The fixed one-line column layout.  (name, width, align)
COLUMNS = (
    ("addr", 5, ">"),
    ("op", 8, "<"),
    ("mode", 5, "<"),
    ("operands", 46, "<"),
    ("xN", 5, ">"),
    ("hold", 5, ">"),
    ("cost", 8, ">"),
    ("stp", 4, ">"),
    ("us", 9, ">"),
    ("d-nbar", 8, ">"),
    ("op-point", 20, "<"),
    ("!", 6, "<"),
)

#: primitive name -> the short form the one-line op-point column uses
SHORT_PRIMITIVE = {
    "shuttle_segment": "shuttle",
    "junction_cross": "junc",
    "ms_gate": "MS",
    "gate_swap": "swap",
}


# ---------------------------------------------------------------------- records


@dataclass(frozen=True, slots=True)
class OpPoint:
    """Which point on which primitive curve this instruction was priced at."""

    primitive: str                  # "shuttle_segment" | "junction_cross[3]" | "split"
    us: float | None
    quanta: float | None
    table: str | None = None        # "qccdsim_jones" | "cyclone" | "scalar" | ...
    label: str | None = None
    source: str | None = None       # arXiv id the point came from
    n: int | None = None            # times charged inside this instruction

    def text(self, short: bool = False) -> str:
        if self.us is None and self.quanta is None:
            return f"{self.primitive} x{self.n or 1} ({self.table})"
        us = "-" if self.us is None else f"{_g(self.us)}us"
        q = "" if self.quanta is None else f"/{_g(self.quanta)}n"
        name = self.primitive
        if short:
            base, _, deg = name.partition("[")
            name = SHORT_PRIMITIVE.get(base, base) + (deg.rstrip("]") if deg else "")
        return f"{name}@{us}{q}"


@dataclass(frozen=True, slots=True)
class Movement:
    """What class of motion this is, and which way it goes."""

    kind: str                       # "template" | "explicit" | "none"
    loop: str | None = None
    delta: int | None = None
    direction: str | None = None    # cw | ccw | inward | outward
    orbit: str | None = None        # the declared class's orbit
    hops: int = 0                   # unit transport hops charged, total
    entails: tuple[str, ...] = ()   # split / merge implied by the class


@dataclass(frozen=True, slots=True)
class Operand:
    """One participant of a variadic instruction, in display form."""

    ion: str
    src: str | None = None
    dst: str | None = None
    via: tuple[str, ...] = ()
    partner: str | None = None      # the other half of a gate pair
    site: str | None = None         # where a gate / SPAM happens


@dataclass(frozen=True, slots=True)
class Cost:
    """What it cost.  `claimed_*` are the program's own annotations (R9's subject)."""

    cost: float | None = None
    steps: int | None = None
    us: float | None = None
    dnbar: float | None = None      # net change in summed n-bar over all ions
    nbar_max: float | None = None   # hottest ion at instruction start
    cycles: int = 1                 # machine cycles this one instruction expands to
    claimed_cost: float | None = None
    claimed_steps: int | None = None

    @property
    def disagrees(self) -> bool:
        return (self.claimed_cost is not None and self.cost is not None
                and abs(self.claimed_cost - self.cost) > 1e-6)


@dataclass(frozen=True, slots=True)
class GroupKey:
    """The fold coordinates read out of `meta`.  Stable ids, not rendered strings."""

    round: object = None
    group: object = None
    batch: object = None
    check: object = None
    phase: object = None

    def path(self) -> tuple[tuple[str, object], ...]:
        return tuple((k, getattr(self, k)) for k in
                     ("round", "group", "batch", "check", "phase")
                     if getattr(self, k) is not None)


@dataclass(frozen=True, slots=True)
class Line:
    """One listing row.  `id` is the handle a future editor clicks back through."""

    index: int                      # position in prog.instructions
    id: int                         # Instruction.id  -- the stable handle
    type: str
    op: str                         # mnemonic
    cls: str | None
    mode: str | None                # intra | inter
    detail: str                     # the operand column, already abbreviated
    width: int                      # variadic participation: ions / pairs / moves
    operands: tuple[Operand, ...]
    movement: Movement
    holds: tuple[str, ...]
    cost: Cost
    opoints: tuple[OpPoint, ...]
    group: GroupKey
    channels: tuple[str, ...] = ()  # control channels driven (opt-in)
    rules: tuple[str, ...] = ()     # rule ids this instruction violated
    note: str | None = None
    provenance: Mapping = field(default_factory=dict)
    #: the ControlRecord for this instruction, when one was supplied.  Same `instr_id`,
    #: so the listing row, the animation frame and the control panel are one object
    #: seen three ways.
    control: object | None = None
    meta: Mapping = field(default_factory=dict)

    @property
    def n_holds(self) -> int:
        return len(self.holds)

    def dominant(self) -> OpPoint | None:
        """The operating point that dominates this instruction's duration."""
        if not self.opoints:
            return None
        return max(self.opoints, key=lambda p: ((p.us or 0.0) * (p.n or 1), p.primitive))

    def to_json(self) -> dict:
        return _json(self)


@dataclass
class Section:
    """A fold: a contiguous run of lines sharing one `meta` key."""

    kind: str                       # "round" | "group" | "batch" | "phase" | "flat"
    key: object
    label: str
    #: POSITION of the first / last line in the run (`Line.index`), not the instruction
    #: id.  Ids are identities and so are neither dense nor ordered: a section spanning
    #: ids 3..1579 of a cooled program holds four instructions, not 1,577, and a span
    #: that lies about its extent is worse than no span at all.
    first: int
    last: int
    lines: list[Line] = field(default_factory=list)
    children: list["Section"] = field(default_factory=list)

    def walk(self) -> Iterator[Line]:
        yield from self.lines
        for c in self.children:
            yield from c.walk()

    def totals(self) -> dict:
        ls = list(self.walk())
        return {
            "instructions": len(ls),
            "cycles": sum(l.cost.cycles for l in ls),
            "cost": sum(l.cost.cost or 0.0 for l in ls),
            "steps": sum(l.cost.steps or 0 for l in ls),
            "us": sum(l.cost.us or 0.0 for l in ls),
            "hops": sum(l.movement.hops for l in ls),
            "contacts": sum(l.width for l in ls if l.type == "gate"),
        }


@dataclass
class Summary:
    """The listing header."""

    program: str
    arch: str
    model: str | None
    n_instructions: int
    n_cycles: int
    n_ions: int
    by_type: dict                   # type -> count
    by_class: dict                  # class -> {n, cost, steps, us, hops, width}
    templates: dict                 # TSIR.templates()
    total_cost: float
    total_steps: int
    total_us: float | None
    peak_quanta: float | None
    n_gate_pairs: int
    fold: str
    rules_passed: tuple[str, ...] = ()
    rules_failed: tuple[str, ...] = ()


@dataclass
class Listing:
    summary: Summary
    lines: list[Line]
    sections: list[Section]

    def by_id(self, instr_id: int) -> Line | None:
        for l in self.lines:
            if l.id == instr_id:
                return l
        return None

    def to_json(self) -> dict:
        return {
            "summary": _json(self.summary),
            "lines": [l.to_json() for l in self.lines],
            "sections": [_section_json(s) for s in self.sections],
        }


# --------------------------------------------------------------------- helpers


def _g(v: float) -> str:
    return f"{v:g}"


def _json(obj):
    if is_dataclass(obj):
        return {k: _json(v) for k, v in asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_json(v) for v in obj]
    if isinstance(obj, dict):
        return {str(k): _json(v) for k, v in obj.items()}
    return obj


def _section_json(s: Section, *, with_ids: bool = True) -> dict:
    d = {"kind": s.kind, "key": s.key, "label": s.label,
         "first": s.first, "last": s.last, "totals": s.totals(),
         "children": [_section_json(c, with_ids=with_ids) for c in s.children]}
    if with_ids:
        d["ids"] = [l.id for l in s.lines]
    return d


def _natkey(s: str):
    head = s.rstrip("0123456789")
    tail = s[len(head):]
    return (head, int(tail) if tail else -1)


def _run(names: Sequence[str], limit: int = 2) -> str:
    if not names:
        return ""
    if len(names) <= limit:
        return ",".join(names)
    ordered = sorted(names, key=_natkey)
    return f"{ordered[0]}..{ordered[-1]}"


# --------------------------------------------------------- replay annotations


def _from_replay(res) -> dict:
    """instr_id -> (cost, steps, us, n_cycles, max_participants)."""
    if res is None:
        return {}
    out: dict[int, list] = {}
    for c in res.cycles:
        slot = out.setdefault(c.instr_id, [0.0, 0, 0.0, 0, 0])
        slot[0] += c.cost
        slot[1] += c.depth
        slot[2] += c.t1 - c.t0
        slot[3] += 1
        slot[4] = max(slot[4], c.n_participants)
    return {k: tuple(v) for k, v in out.items()}


def quanta_trace(prog: TSIR, arch, model) -> dict[int, tuple[float, float]]:
    """instr_id -> (n-bar of the hottest ion at start, net change in summed n-bar).

    Uses the replay's existing `probe` hook, so the numbers are the verifier's own --
    a cool shows a *negative* delta because it really does remove n-bar.
    """
    from ..verify.replay import replay

    marks: list[tuple[int, float, float]] = []

    def probe(instr, current):
        marks.append((instr.id, max(current.values(), default=0.0),
                      sum(current.values())))

    res = replay(prog, arch, model, check_rules=False, probe=probe)
    out: dict[int, tuple[float, float]] = {}
    for i, (iid, peak, total) in enumerate(marks):
        nxt = marks[i + 1][2] if i + 1 < len(marks) else _final_total(res)
        out[iid] = (peak, nxt - total)
    return out


def _final_total(res) -> float:
    """Summed n-bar as the program ends.

    `per_ion_quanta` is the LIFETIME deposit and never comes down, so using it here
    would report the last instruction of a cooled program as depositing a quarter of a
    million quanta.  `ReplayResult.final_quanta` is the running value.
    """
    if getattr(res, "final_quanta", None):
        return sum(res.final_quanta.values())
    return sum(sum(v.values()) for v in res.per_ion_quanta.values())


# ----------------------------------------------------------------- per-instruction


def _op_for(instr: Instruction) -> str:
    if instr.type == "simd":
        cls = instr.cls or ""
        return MNEMONIC_BY_CLASS.get(cls, (cls or "MOVE").upper().replace("_", ".")[:8])
    if instr.type == "gate":
        return (instr.gate or "GATE").upper()
    return MNEMONIC_BY_TYPE.get(instr.type, instr.type.upper())


def _movement(instr: Instruction, arch) -> Movement:
    if instr.type != "simd":
        return Movement(kind="none")
    decl: Mapping = {}
    if arch is not None and instr.cls:
        try:
            decl = arch.simd_class(instr.cls)
        except KeyError:
            decl = {}
    entails = tuple(arch.entails(instr.cls)) if (arch is not None and instr.cls) else ()
    if instr.template and instr.template.get("kind") == "loop_shift":
        delta = int(instr.template["delta"])
        loop = str(instr.template["loop"])
        n_loop = len(arch.device.loops[loop].nodes) if arch is not None else 0
        return Movement(kind="template", loop=loop, delta=delta,
                        direction="cw" if delta >= 0 else "ccw",
                        orbit=decl.get("orbit"), hops=abs(delta) * n_loop,
                        entails=entails)
    hops = sum(len(p.via) or 1 for p in instr.participants)
    return Movement(kind="explicit", direction=decl.get("direction"),
                    orbit=decl.get("orbit"), hops=hops, entails=entails)


def _detail(instr: Instruction, arch, mv: Movement, n_from_replay: int,
            n_ions: int = 0):
    """-> (detail text, width, operands, note), with the R4c claim in front.

    The word is deliberately in the DETAIL column and not in `op`: the mnemonic column
    is eight characters and is the class, which is what a reader scans for.  A broadcast
    is a property of how that class is driven, so it reads as a qualifier --
    `broadcast(one) L0 cw 1 hop  all of L0` -- and every existing line is byte-identical
    because an instruction that makes no claim gets no prefix.  `cool` is left alone: it
    already prints "broadcast, all ions".
    """
    txt, width, ops, note = _detail_body(instr, arch, mv, n_from_replay, n_ions)
    kind = broadcast_kind(instr)
    if kind and instr.type != "cool":
        txt = f"broadcast({kind}) {txt}"
    return txt, width, ops, note


def _detail_body(instr: Instruction, arch, mv: Movement, n_from_replay: int,
                 n_ions: int = 0):
    """-> (detail text, width, operands, note)"""
    meta = instr.meta or {}
    note = meta.get("note")

    if instr.type == "init":
        by_zone: dict[str, list[str]] = {}
        for ion, node in instr.placement.items():
            z = "site"
            if arch is not None:
                n = arch.device.nodes.get(node)
                if n is not None:
                    z = n.zone_type or n.kind
            by_zone.setdefault(str(z), []).append(node)
        txt = "place " + " ".join(
            f"{len(v)}x{k}[{_run(v)}]"
            for k, v in sorted(by_zone.items(), key=lambda kv: -len(kv[1])))
        ops = tuple(Operand(ion=i, dst=n) for i, n in instr.placement.items())
        return txt, len(instr.placement), ops, note

    if instr.type == "simd":
        if mv.kind == "template":
            n = n_from_replay
            who = f"x{n} ions" if n else f"all of {mv.loop}"
            return (f"{mv.loop} {mv.direction} {abs(mv.delta or 0)} hop"
                    f"{'s' if abs(mv.delta or 0) != 1 else ''}  {who}",
                    n, (), note)
        ops = tuple(Operand(ion=p.ion, src=p.src, dst=p.dst, via=tuple(p.via))
                    for p in instr.participants)
        shown = ", ".join(f"{o.ion} {o.src}>{o.dst}" for o in ops[:2])
        if len(ops) > 2:
            shown += f", +{len(ops) - 2}"
        if mv.entails:
            shown += f"  [{'+'.join(mv.entails)}]"
        return shown, len(ops), ops, note

    if instr.type == "gate":
        pairs = list(iter_pairs(instr))
        ops = tuple(Operand(ion=a, partner=b,
                            site=instr.sites[i] if i < len(instr.sites) else None)
                    for i, (a, b) in enumerate(pairs))
        shown = ", ".join(f"({o.ion},{o.partner})@{o.site or '?'}" for o in ops[:2])
        if len(ops) > 2:
            shown += f", +{len(ops) - 2}"
        return shown, len(pairs), ops, note

    if instr.type == "cool":
        who = "broadcast, all ions" if instr.broadcast else f"x{len(instr.ions)} " \
                                                            f"{_run(list(instr.ions))}"
        trig = meta.get("trigger")
        # a broadcast cool names no ions precisely because it reaches all of them;
        # reporting len(()) as the width printed `.` against a cycle that cools 168
        width = n_ions if instr.broadcast else len(instr.ions)
        return (who + (f"  trigger={trig}" if trig else ""),
                width, tuple(Operand(ion=i) for i in instr.ions), note)

    if instr.type in ("measure", "reset"):
        return (f"x{len(instr.ions)} {_run(list(instr.ions))}", len(instr.ions),
                tuple(Operand(ion=i) for i in instr.ions), note)

    return (note or "sync", 0, (), None)


def _opoints(instr: Instruction, arch, model, mv: Movement, width: int):
    if arch is None or model is None:
        return ()
    pol = getattr(model, "policy", None)
    out: list[OpPoint] = []

    def from_curve(name, n=None, primitive=None):
        try:
            p = arch.primitives.curve(name).pick(pol)
        except Exception:
            return None
        return OpPoint(primitive or name, p.us, p.quanta, p.table, p.label, p.source, n)

    def from_scalar(name, n=None):
        try:
            s = arch.primitives.scalar(name)
        except Exception:
            return None
        us = s.get("us")
        return OpPoint(name, float(us) if us is not None else None, None,
                       "scalar", s.get("label"), s.get("source"), n)

    if instr.type == "simd":
        if not getattr(model, "models_time", False):
            return (OpPoint("hop", None, None, getattr(model, "name", None),
                            "unit hop, deck model", None, mv.hops or None),)
        p = from_curve("shuttle_segment", mv.hops or None)
        if p:
            out.append(p)
        njunc: dict[int, int] = {}
        min_deg = int(getattr(model, "junction_min_degree", 3))
        if instr.participants:
            for part in instr.participants:
                node = part.src
                for sid in part.via or ():
                    node = arch.device.segments[sid].other(node)
                    d = arch.device.degree(node)
                    if d >= min_deg:
                        njunc[d] = njunc.get(d, 0) + 1
        elif instr.template and instr.template.get("kind") == "loop_shift":
            # A rigid rotation carries NO participants -- that is the whole point of the
            # template. Walking `participants` therefore found no junctions and every
            # rotation row reported `shuttle@5us/0.1n` while the replay was charging 312
            # junction crossings on that very instruction. Count the junctions the shift
            # actually crosses: every occupied slot advances |delta| steps around the
            # loop, so the crossings are the degree>=3 nodes it lands on.
            loop = arch.device.loops.get(str(instr.template.get("loop")))
            if loop is not None:
                seq = list(loop.nodes)
                k = len(seq)
                delta = int(instr.template.get("delta", 0))
                step = 1 if delta >= 0 else -1
                for start in range(k):        # one orbit = every slot, occupied or not
                    node = start
                    for _ in range(abs(delta)):
                        node = (node + step) % k
                        d = arch.device.degree(seq[node])
                        if d >= min_deg:
                            njunc[d] = njunc.get(d, 0) + 1
        for d, n in sorted(njunc.items()):
            c = arch.primitives.degree_curve("junction_cross").get(d)
            if c is not None:
                pt = c.pick(pol)
                out.append(OpPoint(f"junction_cross[{d}]", pt.us, pt.quanta,
                                   pt.table, pt.label, pt.source, n))
        for e in mv.entails:
            p = from_curve(e, width)
            if p:
                out.append(p)
    elif instr.type == "gate":
        key = {"MS": "ms_gate", "CX": "ms_gate", "SWAP": "gate_swap"}.get(
            instr.gate or "MS", "ms_gate")
        p = from_scalar(key, width or 1)
        if p:
            out.append(p)
    elif instr.type in ("cool", "measure", "reset"):
        p = from_scalar(instr.type, max(1, width))
        if p:
            out.append(p)
    return tuple(out)


def _channels(instr: Instruction, arch) -> tuple[str, ...]:
    if arch is None:
        return ()
    cp = arch.control_plane
    if not cp.groups:
        return ()
    sites: set[str] = set()
    for p in instr.participants:
        sites.add(p.src)
        sites.add(p.dst)
    sites.update(instr.sites)
    if instr.template and instr.template.get("kind") == "loop_shift":
        sites.update(arch.device.loops[str(instr.template["loop"])].nodes)
    out: set[str] = set()
    for s in sites:
        out.update(cp.channels_of(s))
    return tuple(sorted(out))


def _from_record(rec) -> tuple[str, ...]:
    """The channel ids a `ControlRecord` says this cycle engaged.

    Empty unless the record was built with `with_ids=True` -- the ids are deliberately
    left out of the default record so that a whole program's records deduplicate.  The
    caller then falls back to the static walk over every site the instruction names.
    """
    out: list[str] = []
    for b in getattr(rec, "banks", ()) if rec is not None else ():
        out.extend(b.ids)
    return tuple(sorted(out))


def _group_key(meta: Mapping) -> GroupKey:
    return GroupKey(round=meta.get("round"), group=meta.get("group"),
                    batch=meta.get("batch"), check=meta.get("check"),
                    phase=meta.get("phase"))


def _provenance(meta: Mapping) -> dict:
    """Everything that says where this instruction CAME FROM.

    `meta["call"]` is what `qccd.api.Program` fills in: an index into the program's
    `meta["prov"]` table, which holds the file, line and source text of the Python call
    that emitted this instruction.  It is what makes click-an-instruction ->
    highlight-your-source-line possible.  `resolve_provenance` joins the two.
    """
    out: dict = {}
    for k in ("call", "src", "src_id", "before_instruction", "kind", "scheme", "code",
              "trigger"):
        if k in meta:
            out[k] = meta[k]
    return out


# ------------------------------------------------------------------- disassemble


def disassemble(
    prog: TSIR,
    arch=None,
    *,
    res=None,
    model=None,
    quanta: Mapping[int, tuple[float, float]] | None = None,
    fold: str | None = None,
    with_control: bool = False,
    control: Mapping[int, object] | None = None,
) -> Listing:
    """Turn a TSIR program into structured listing lines plus folds and a header.

    `arch` unlocks zone names, SIMD-class declarations and operating points; `res` (a
    `ReplayResult`) supplies the *replayed* cost/steps/us rather than the program's own
    claims; `quanta` is the optional output of `quanta_trace`; `control` is a
    `{instr_id: ControlRecord}` map from `qccd.verify.control`, which fills `Line.control`
    and resolves `Line.channels` from what the cycle actually engaged rather than from a
    static walk over every site the instruction names.  With none of them the listing
    still renders -- from the instruction's own annotations.
    """
    ann = _from_replay(res)
    viol: dict[int, set[str]] = {}
    if res is not None:
        for v in res.rules.violations:
            viol.setdefault(v.instr_id, set()).add(v.rule)
    # every ion the program ever places -- a broadcast cool reaches all of them, and
    # a program may init more than once
    all_ions: set[str] = set()
    for i in prog.instructions:
        if i.type == "init":
            all_ions |= set(i.placement)

    lines: list[Line] = []
    for index, instr in enumerate(prog.instructions):
        meta = dict(instr.meta or {})
        mv = _movement(instr, arch)
        rec = ann.get(instr.id)
        n_replay = rec[4] if rec else 0
        detail, width, operands, note = _detail(instr, arch, mv, n_replay,
                                                n_ions=len(all_ions))
        if mv.kind == "template" and rec:
            # the replay expanded |delta| unit cycles; hops = participants x cycles
            mv = Movement(**{**_asdict(mv), "hops": n_replay * rec[3]})
        q = (quanta or {}).get(instr.id)
        crec = (control or {}).get(instr.id)
        cost = Cost(
            cost=rec[0] if rec else instr.cost,
            steps=rec[1] if rec else instr.steps,
            us=rec[2] if rec else None,
            cycles=rec[3] if rec else 1,
            nbar_max=q[0] if q else None,
            dnbar=q[1] if q else None,
            claimed_cost=instr.cost,
            claimed_steps=instr.steps,
        )
        lines.append(Line(
            index=index, id=instr.id, type=instr.type, op=_op_for(instr),
            cls=instr.cls, mode=instr.mode, detail=detail, width=width,
            operands=operands, movement=mv, holds=tuple(instr.holds), cost=cost,
            opoints=_opoints(instr, arch, model, mv, width),
            group=_group_key(meta),
            channels=(_from_record(crec) or (_channels(instr, arch)
                                             if with_control else ())),
            rules=tuple(sorted(viol.get(instr.id, ()))),
            note=note, provenance=_provenance(meta), control=crec, meta=meta,
        ))

    fold = fold or _auto_fold(lines)
    sections = _fold(lines, fold)
    return Listing(summary=_summary(prog, arch, model, res, lines, fold),
                   lines=lines, sections=sections)


def _asdict(mv: Movement) -> dict:
    return {f: getattr(mv, f) for f in Movement.__slots__}


def _auto_fold(lines: Sequence[Line]) -> str:
    """Pick the coarsest fold key the program actually populates."""
    for k in ("round", "group", "batch", "phase", "check"):
        if any(getattr(l.group, k) is not None for l in lines):
            return k
    return "flat"


def _resolve(lines: Sequence[Line], key: str) -> list[object]:
    """Fill in fold keys the builders left blank.

    A cooling pass inserts a `cool` carrying `batch` but no `group`, and a barrier
    carries nothing at all.  An inserted instruction serves the instruction it precedes,
    so a blank key is filled forward from the next line that has one, and only then
    backward -- otherwise every inserted cool would split its group in two.
    """
    vals: list[object] = [getattr(l.group, key) for l in lines]
    # `init` is the prologue; it belongs to no round and must not be swept into the
    # first one by the forward fill
    frozen = {i for i, l in enumerate(lines) if l.type == "init"}
    nxt: object = None
    for i in range(len(vals) - 1, -1, -1):
        if i in frozen:
            continue
        if vals[i] is None:
            vals[i] = nxt
        else:
            nxt = vals[i]
    prev: object = None
    for i, v in enumerate(vals):
        if i in frozen:
            continue
        if v is None:
            vals[i] = prev
        else:
            prev = v
    return vals


def _fold(lines: Sequence[Line], fold: str) -> list[Section]:
    if fold == "flat" or not lines:
        return [Section("flat", None, "program", lines[0].index if lines else 0,
                        lines[-1].index if lines else 0, list(lines))]
    inner_key = {"round": "batch", "group": "batch"}.get(fold)
    outer_vals = _resolve(lines, fold)
    inner_vals = _resolve(lines, inner_key) if inner_key else [None] * len(lines)
    out: list[Section] = []
    for l, okey, ikey in zip(lines, outer_vals, inner_vals):
        if not out or out[-1].key != okey:
            out.append(Section(fold, okey,
                               f"{fold} {okey}" if okey is not None else "prologue",
                               l.index, l.index))
        sec = out[-1]
        sec.last = l.index
        if ikey is None:
            sec.lines.append(l)
            continue
        if not sec.children or sec.children[-1].key != ikey:
            sec.children.append(Section(inner_key, ikey, f"{inner_key} {ikey}",
                                        l.index, l.index))
        sec.children[-1].last = l.index
        sec.children[-1].lines.append(l)
    for sec in out:
        _label(sec)
        for c in sec.children:
            _label(c)
    return out


def _label(sec: Section) -> None:
    checks = []
    for l in sec.walk():
        c = l.group.check
        if c is not None and c not in checks:
            checks.append(c)
    if checks:
        shown = ", ".join(str(c) for c in checks[:3])
        if len(checks) > 3:
            shown += f", +{len(checks) - 3}"
        sec.label = f"{sec.kind} {sec.key}   checks {shown}"


def _summary(prog: TSIR, arch, model, res, lines: Sequence[Line], fold: str) -> Summary:
    by_type: dict[str, int] = {}
    by_class: dict[str, dict] = {}
    for l in lines:
        by_type[l.type] = by_type.get(l.type, 0) + 1
        key = l.cls or l.type
        d = by_class.setdefault(key, {"n": 0, "cycles": 0, "cost": 0.0, "steps": 0,
                                      "us": 0.0, "hops": 0, "width": 0})
        d["n"] += 1
        d["cycles"] += l.cost.cycles
        d["cost"] += l.cost.cost or 0.0
        d["steps"] += l.cost.steps or 0
        d["us"] += l.cost.us or 0.0
        d["hops"] += l.movement.hops
        d["width"] += l.width
    return Summary(
        program=prog.name,
        arch=getattr(arch, "name", None) or prog.arch_spec,
        model=getattr(model, "name", None),
        n_instructions=len(lines),
        n_cycles=sum(l.cost.cycles for l in lines),
        n_ions=len(prog.ion_names()),
        by_type=by_type,
        by_class=by_class,
        templates=prog.templates(),
        total_cost=getattr(res, "total_cost", None) or sum(
            l.cost.cost or 0.0 for l in lines),
        total_steps=getattr(res, "total_steps", None) or sum(
            l.cost.steps or 0 for l in lines),
        total_us=getattr(res, "total_us", None),
        peak_quanta=getattr(res, "peak_quanta", None),
        n_gate_pairs=sum(l.width for l in lines if l.type == "gate"),
        fold=fold,
        rules_passed=tuple(res.rules.summary()["passed"]) if res is not None else (),
        rules_failed=tuple(res.rules.summary()["failed"]) if res is not None else (),
    )


# ------------------------------------------------------------------- rendering


def _cell(v, width, align) -> str:
    s = "." if v is None or v == "" else str(v)
    if len(s) > width:
        s = s[: width - 1] + "~"
    return f"{s:{align}{width}}"


def render_line(line: Line, *, cursor: bool = False) -> str:
    """One instruction, one line.  A pure function of the record."""
    c = line.cost
    dom = line.dominant()
    cells = [
        # POSITION, not identity.  `Instruction.id` is a durable handle allocated once
        # and never reassigned, so after a pass that inserts (cooling) the ids run
        # 0,1,2,1579,3,4,... -- correct as a handle, unreadable as an address.  A column
        # a human scrolls has to count, so it counts.  The identity stays the join key
        # everywhere else (`Line.id`, `by_id`, the page's `LROW`, the NOW strip's `#id`).
        f"{line.index:d}",
        line.op,
        line.mode or "",
        line.detail,
        f"x{line.width}" if line.width else "",
        f"{len(line.holds)}" if line.holds else "",
        f"{c.cost:,.0f}" if c.cost is not None else None,
        c.steps,
        f"{c.us / 1000:,.2f}ms" if (c.us or 0) >= 1000 else (
            f"{c.us:,.1f}us" if c.us is not None else None),
        (f"{c.dnbar:+,.1f}" if c.dnbar is not None else None),
        dom.text(short=True) if dom else None,
        " ".join(line.rules),
    ]
    body = " ".join(_cell(v, w, a) for v, (n, w, a) in zip(cells, COLUMNS))
    return ("> " if cursor else "  ") + body


def render_header(s: Summary) -> list[str]:
    out = [
        f"{s.program}  on  {s.arch}" + (f"   [{s.model} model]" if s.model else ""),
        f"  {s.n_instructions:,} instructions -> {s.n_cycles:,} machine cycles"
        f"   {s.n_ions} ions   {s.n_gate_pairs:,} contacts",
        f"  cost {s.total_cost:,.0f}   steps {s.total_steps:,}"
        + (f"   runtime {s.total_us / 1000:,.2f} ms" if s.total_us else "")
        + (f"   peak n-bar {s.peak_quanta:,.1f}" if s.peak_quanta else ""),
        "",
        f"  {'class':12s} {'instrs':>7s} {'cycles':>8s} {'width':>8s} {'hops':>9s} "
        f"{'cost':>11s} {'steps':>8s} {'us':>12s}",
    ]
    for k, d in sorted(s.by_class.items(), key=lambda kv: -kv[1]["cost"]):
        out.append(f"  {k:12s} {d['n']:7,d} {d['cycles']:8,d} {d['width']:8,d} "
                   f"{d['hops']:9,d} {d['cost']:11,.0f} {d['steps']:8,d} "
                   f"{d['us']:12,.0f}")
    out += ["",
            f"  movement templates ({len(s.templates)}): " + ", ".join(
                f"{k} x{v}" for k, v in sorted(s.templates.items()))]
    if s.rules_passed or s.rules_failed:
        out.append(f"  rules passed {' '.join(s.rules_passed) or '(none)'}")
        out.append(f"  rules failed {' '.join(s.rules_failed) or '(none)'}")
    out += ["", "  " + " ".join(_cell(n, w, "<") for n, w, a in COLUMNS),
            "  " + "-" * (sum(w for _, w, _ in COLUMNS) + len(COLUMNS) - 1)]
    return out


def render(
    listing: Listing,
    *,
    depth: int = 1,
    cursor_id: int | None = None,
    header: bool = True,
    limit: int | None = None,
) -> str:
    """The whole listing as text.  `depth=0` folds every section to one summary line."""
    out: list[str] = []
    if header:
        out += render_header(listing.summary)
    n = 0
    for sec in listing.sections:
        out += _render_section(sec, depth, cursor_id)
        n += 1
        if limit and n >= limit:
            out.append(f"  ... {len(listing.sections) - n} more sections")
            break
    return "\n".join(out)


def _render_section(sec: Section, depth: int, cursor_id, level: int = 0) -> list[str]:
    t = sec.totals()
    pad = "  " * level
    head = (f"{pad}== {sec.label:<38s} {t['instructions']:5,d} instr  "
            f"{t['cycles']:6,d} cyc  cost {t['cost']:11,.0f}  "
            f"{t['us'] / 1000:9,.2f} ms  {t['contacts']:3d} contacts")
    if depth <= level:
        return [head]
    out = [head]
    for l in sec.lines:
        out.append(render_line(l, cursor=(cursor_id == l.id)))
    for c in sec.children:
        out += _render_section(c, depth, cursor_id, level + 1)
    return out


# ------------------------------------------------------------- page view model


def to_view_model(listing: Listing) -> dict:
    """What the HTML page embeds: rendered rows keyed by instruction id.

    `Listing.to_json()` is full fidelity and costs ~2.4 MB on the deck program -- far
    too much to sit next to the frame blob in a self-contained page.  This is the same
    listing at ~290 KB (26 KB gzipped): one pre-rendered string per instruction, plus
    the fold tree and enough per-row structure for the page to colour and filter.  The
    page highlights the executing instruction by looking `frame.id` up in `index`;
    nothing on the page ever parses a rendered line.
    """
    return {
        "columns": [{"name": n, "width": w, "align": a} for n, w, a in COLUMNS],
        "header": render_header(listing.summary),
        "summary": _json(listing.summary),
        "index": {str(l.id): i for i, l in enumerate(listing.lines)},
        "rows": [
            {
                "id": l.id,
                "text": render_line(l).rstrip(),
                "op": l.op,
                "type": l.type,
                "cls": l.cls,
                "width": l.width,
                "rules": list(l.rules),
            }
            for l in listing.lines
        ],
        "sections": [_section_json(s, with_ids=False) for s in listing.sections],
    }


#: mode -> the small int the page model stores it as
_MODES = (None, "intra", "inter")


def to_page_model(listing: Listing) -> dict:
    """The listing compressed for a self-contained page: columnar and interned.

    `to_view_model` pre-renders one 133-character row per instruction, which is 450 KB
    for the deck program -- as much again as the whole existing frame blob.  This is the
    same information at ~70 KB: parallel arrays, with the mnemonics and operating-point
    labels interned (6 and 5 distinct values across 1,579 rows) and the operand column
    kept as the string Python already abbreviated, so the page never re-derives an
    operand rendering and cannot disagree with `python -m qccd listing`.

    Every row is addressable by `Instruction.id` through `ids` / `index`, which is the
    handle the animation frame, the rule violations and a future editor click all share.
    """
    ops: list[str] = []
    pts: list[str] = []
    types: list[str] = []
    clss: list[str | None] = []

    seen: dict[tuple[int, object], int] = {}

    def intern(table: list, v):
        key = (id(table), v)
        ix = seen.get(key)
        if ix is None:
            ix = seen[key] = len(table)
            table.append(v)
        return ix

    def num(x):
        """An integral float prints as `1300`, not `1300.0` -- 1,579 rows x 2 bytes."""
        if x is None:
            return None
        x = round(float(x), 1)
        return int(x) if x == int(x) else x

    row_id, row_op, row_md, row_dt, row_wd, row_hd = [], [], [], [], [], []
    row_ct, row_st, row_us, row_dq, row_pt, row_rl = [], [], [], [], [], []
    row_ty, row_cl, row_cn = [], [], []
    rules: list[str] = []
    for l in listing.lines:
        dom = l.dominant()
        row_id.append(l.id)
        row_op.append(intern(ops, l.op))
        row_md.append(_MODES.index(l.mode) if l.mode in _MODES else 0)
        row_dt.append(l.detail)
        row_wd.append(l.width)
        row_hd.append(len(l.holds))
        row_ct.append(num(l.cost.cost))
        row_st.append(l.cost.steps)
        row_us.append(num(l.cost.us))
        row_dq.append(num(l.cost.dnbar))
        row_pt.append(intern(pts, dom.text(short=True) if dom else ""))
        row_rl.append(intern(rules, " ".join(l.rules)))
        row_ty.append(intern(types, l.type))
        row_cl.append(intern(clss, l.cls))
        row_cn.append(l.provenance.get("call") if isinstance(
            l.provenance.get("call"), int) else None)
    return {
        "columns": [{"name": n, "width": w, "align": a} for n, w, a in COLUMNS],
        "header": render_header(listing.summary),
        "summary": _json(listing.summary),
        "ops": ops, "pts": pts, "types": types, "classes": clss, "rule_sets": rules,
        # no id->row index: the page builds it in one pass over `ids`, and shipping it
        # costs 16 KB of "0":0,"1":1,... on a program this size
        "ids": row_id,
        "op": row_op, "mode": row_md, "detail": row_dt, "width": row_wd,
        "holds": row_hd, "cost": row_ct, "steps": row_st, "us": row_us,
        "dnbar": row_dq, "point": row_pt, "rules": row_rl,
        "type": row_ty, "cls": row_cl, "call": row_cn,
        # top-level folds only, and only the fields a navigator needs.  The full tree
        # (396 batch children on the deck program) is 79 KB of totals nobody reads on a
        # page whose batch boundaries are already drawn from `frame.batch`.
        "sections": [{"kind": s.kind, "label": s.label, "first": s.first,
                      "last": s.last, "n": s.totals()["instructions"],
                      "cost": num(s.totals()["cost"]),
                      "us": num(s.totals()["us"]),
                      "contacts": s.totals()["contacts"],
                      "children": len(s.children)}
                     for s in listing.sections],
        "fold": listing.summary.fold,
    }


def _path_str(g: GroupKey) -> str:
    return "/".join(f"{k}={v}" for k, v in g.path())
