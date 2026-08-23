"""Layer 2 -- TSIR, the control IR.  PLAN §4.

A TSIR program is a list of instructions over an architecture.  Two things about the
shape are deliberate:

**One instruction is one concurrent cycle.**  A `simd` instruction carries *many*
participants and a `gate` instruction carries *many* pairs, because a broadcast-wired
machine really does drive them together -- that is the whole content of R4's "a class
fixes (type, direction), participation is variadic".  Cost aggregates as a sum across
participants and depth/duration as a max, which is what makes "9.1% batch utilization"
a number the IR can even express.

**Cost, duration and quanta are annotations, not content.**  An instruction says what
moves where; `qccd.verify.replay` computes what it costs under a given
`qccd.cost` model.  Any annotations already present are *claims*, and R9 is exactly the
check that the claims equal the replay.  That separation is what lets M1 and M2 run the
identical schedule under the deck's model and under the corrected one and attribute
every difference to the model rather than to the program.

The `template` form of a `simd` instruction (`{"kind": "loop_shift", "loop": ..., "delta":
...}`) is not a compression trick.  PLAN §1's thesis is that rigid rotation needs exactly
one movement template where an odd-even sort needs many, so the IR has to be able to say
"one template" in one object; counting templates is how M4 will measure it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Sequence

__all__ = [
    "Participant",
    "Instruction",
    "TSIR",
    "INSTRUCTION_TYPES",
    "loop_shift",
]

#: Every instruction type the verifier knows how to replay.
INSTRUCTION_TYPES = (
    "init",  # place ions, set initial quanta
    "simd",  # a transport cycle: one class, variadic participants
    "gate",  # one or more co-located pairs, driven together
    "measure",
    "reset",
    "cool",  # broadcast or per-ion sympathetic/Doppler cooling
    "barrier",  # explicit synchronization; costs nothing
)


@dataclass(frozen=True, slots=True)
class Participant:
    """One ion's movement inside a `simd` cycle."""

    ion: str
    src: str
    dst: str
    via: tuple[str, ...] = ()  # segment ids, in traversal order

    def to_json(self) -> dict:
        d = {"ion": self.ion, "from": self.src, "to": self.dst}
        if self.via:
            d["via"] = list(self.via)
        return d

    @staticmethod
    def from_json(d: Mapping) -> "Participant":
        return Participant(
            ion=str(d["ion"]),
            src=str(d["from"]),
            dst=str(d["to"]),
            via=tuple(str(v) for v in d.get("via", ())),
        )


def loop_shift(loop: str, delta: int) -> dict:
    """The rigid-rotation movement template."""
    return {"kind": "loop_shift", "loop": loop, "delta": int(delta)}


@dataclass(frozen=True, slots=True)
class Instruction:
    """One concurrent cycle of the machine."""

    type: str
    id: int

    # --- transport ---------------------------------------------------------
    cls: str | None = None  # SIMD class id; serialized as "class"
    mode: str | None = None  # "intra" | "inter"  (R4b)
    template: Mapping | None = None  # e.g. loop_shift("L0", +1)
    participants: tuple[Participant, ...] = ()
    holds: tuple[str, ...] = ()  # resources occupied over [t0, t1)

    # --- gates / SPAM ------------------------------------------------------
    gate: str | None = None  # "MS" | "CX" | "SWAP" | ...
    pairs: tuple[tuple[str, str], ...] = ()
    ions: tuple[str, ...] = ()
    sites: tuple[str, ...] = ()

    # --- cooling -----------------------------------------------------------
    broadcast: bool = False

    # --- init --------------------------------------------------------------
    placement: Mapping[str, str] = field(default_factory=dict)
    quanta: Mapping[str, float] = field(default_factory=dict)

    # --- annotations (claims; R9 checks them against the replay) -----------
    t0: float | None = None
    t1: float | None = None
    cost: float | None = None
    steps: int | None = None
    quanta_delta: Mapping[str, float] | None = None
    operating_point: Mapping | None = None

    # --- provenance --------------------------------------------------------
    meta: Mapping = field(default_factory=dict)

    def with_annotations(
        self,
        *,
        t0: float,
        t1: float,
        cost: float,
        steps: int,
        quanta_delta: Mapping[str, float] | None = None,
        operating_point: Mapping | None = None,
    ) -> "Instruction":
        return replace(
            self,
            t0=t0,
            t1=t1,
            cost=cost,
            steps=steps,
            quanta_delta=dict(quanta_delta) if quanta_delta else None,
            operating_point=operating_point,
        )

    def moved_ions(self) -> tuple[str, ...]:
        return tuple(p.ion for p in self.participants)

    def to_json(self) -> dict:
        d: dict = {"type": self.type, "id": self.id}
        if self.cls is not None:
            d["class"] = self.cls
        if self.mode is not None:
            d["mode"] = self.mode
        if self.template is not None:
            d["template"] = dict(self.template)
        if self.participants:
            d["participants"] = [p.to_json() for p in self.participants]
        if self.holds:
            d["holds"] = list(self.holds)
        if self.gate is not None:
            d["gate"] = self.gate
        if self.pairs:
            d["pairs"] = [list(p) for p in self.pairs]
        if self.ions:
            d["ions"] = list(self.ions)
        if self.sites:
            d["sites"] = list(self.sites)
        if self.broadcast:
            d["broadcast"] = True
        if self.placement:
            d["placement"] = dict(self.placement)
        if self.quanta:
            d["quanta"] = dict(self.quanta)
        for key in ("t0", "t1", "cost", "steps"):
            v = getattr(self, key)
            if v is not None:
                d[key] = v
        if self.quanta_delta:
            d["quanta_delta"] = dict(self.quanta_delta)
        if self.operating_point:
            d["operating_point"] = dict(self.operating_point)
        if self.meta:
            d["meta"] = dict(self.meta)
        return d

    @staticmethod
    def from_json(d: Mapping) -> "Instruction":
        pairs = tuple((str(a), str(b)) for a, b in d.get("pairs", ()))
        return Instruction(
            type=str(d["type"]),
            id=int(d["id"]),
            cls=d.get("class"),
            mode=d.get("mode"),
            template=d.get("template"),
            participants=tuple(Participant.from_json(p) for p in d.get("participants", ())),
            holds=tuple(str(h) for h in d.get("holds", ())),
            gate=d.get("gate"),
            pairs=pairs,
            ions=tuple(str(i) for i in d.get("ions", ())),
            sites=tuple(str(s) for s in d.get("sites", ())),
            broadcast=bool(d.get("broadcast", False)),
            placement=dict(d.get("placement", {})),
            quanta=dict(d.get("quanta", {})),
            t0=d.get("t0"),
            t1=d.get("t1"),
            cost=d.get("cost"),
            steps=d.get("steps"),
            quanta_delta=d.get("quanta_delta"),
            operating_point=d.get("operating_point"),
            meta=dict(d.get("meta", {})),
        )


@dataclass
class TSIR:
    """A timed, rule-checkable hardware program."""

    name: str
    arch_spec: str
    instructions: list[Instruction] = field(default_factory=list)
    metrics: dict = field(default_factory=dict)
    meta: dict = field(default_factory=dict)
    #: high-water mark of the identity allocator.
    #:
    #: `Instruction.id` is an IDENTITY, not a position.  It is allocated once, by
    #: `next_id()`, and no pass may reassign it: every join in the system keys on it
    #: (`CycleRecord.instr_id`, `Violation.instr_id`, `ControlRecord.instr_id`,
    #: `Listing.by_id`, the page's `LROW`, `prov.calls_to_instructions`), so an id that
    #: silently re-targets is a handle that lies.  Ids are therefore unique but neither
    #: dense nor monotonic in program order -- position is carried by `Line.index` and
    #: by the frame's own array index, which is where a human-readable address belongs.
    id_seq: int = 0

    # ---------------------------------------------------------------- basics

    def __len__(self) -> int:
        return len(self.instructions)

    def __iter__(self) -> Iterator[Instruction]:
        return iter(self.instructions)

    def add(self, instr: Instruction) -> Instruction:
        self.instructions.append(instr)
        return instr

    def next_id(self) -> int:
        """Allocate a fresh identity.  SIDE-EFFECTING: never call it to peek.

        It used to be `len(self.instructions)`, i.e. a position dressed as a handle.
        That is what let `cooling._rebuild` re-label every instruction and silently
        re-target 1,576 of the 1,578 ids `prov.index_by_line` had handed out.
        """
        nid = self.id_seq
        self.id_seq = nid + 1
        return nid

    def of_type(self, *types: str) -> tuple[Instruction, ...]:
        want = set(types)
        return tuple(i for i in self.instructions if i.type in want)

    def templates(self) -> dict[str, int]:
        """Distinct movement templates -> how many machine cycles each is issued for.

        This is PLAN §1's quantity: rigid rotation should need one template where an
        odd-even sort needs many.  A shift by k is *not* k templates -- it is the same
        unit-hop template driven k times, which is exactly the property that makes one
        broadcast waveform enough -- so the key normalizes to (loop, direction) and the
        value counts unit cycles.  An instruction with explicit participants is keyed by
        its class: variadic participation within one class is still one template (R4).
        """
        out: dict[str, int] = {}
        for i in self.instructions:
            if i.type != "simd":
                continue
            if i.template and i.template.get("kind") == "loop_shift":
                delta = int(i.template["delta"])
                if delta == 0:
                    continue
                sign = "+1" if delta > 0 else "-1"
                key = f"loop_shift:{i.template['loop']}:{sign}"
                out[key] = out.get(key, 0) + abs(delta)
            else:
                key = f"class:{i.cls}"
                out[key] = out.get(key, 0) + 1
        return out

    def ion_names(self) -> tuple[str, ...]:
        for i in self.instructions:
            if i.type == "init":
                return tuple(i.placement)
        return ()

    # ---------------------------------------------------------------- json

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "arch_spec": self.arch_spec,
            "instructions": [i.to_json() for i in self.instructions],
            "metrics": dict(self.metrics),
            "meta": dict(self.meta),
            "id_seq": self.id_seq,
        }

    @staticmethod
    def from_json(d: Mapping) -> "TSIR":
        instrs = [Instruction.from_json(i) for i in d.get("instructions", ())]
        # the migration hook: a document written before `id_seq` existed carries ids
        # that were positions, and positions are already unique, so reinterpreting them
        # as identities is safe -- the allocator just has to resume above the highest.
        seq = (int(d["id_seq"]) if "id_seq" in d
               else max((i.id for i in instrs), default=-1) + 1)
        return TSIR(
            name=str(d["name"]),
            arch_spec=str(d["arch_spec"]),
            instructions=instrs,
            metrics=dict(d.get("metrics", {})),
            meta=dict(d.get("meta", {})),
            id_seq=seq,
        )

    def save(self, path: str | Path, *, indent: int | None = None) -> Path:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_json(), indent=indent) + "\n", encoding="utf-8")
        return p

    @staticmethod
    def load(path: str | Path) -> "TSIR":
        return TSIR.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def validate_program(prog: TSIR) -> list[str]:
    """Shape checks that need no architecture: ids, types, required fields."""
    errors: list[str] = []
    seen: set[int] = set()
    for n, instr in enumerate(prog.instructions):
        where = f"instructions[{n}] (id={instr.id})"
        if instr.type not in INSTRUCTION_TYPES:
            errors.append(f"{where}: unknown type {instr.type!r}")
        if instr.id in seen:
            errors.append(f"{where}: duplicate instruction id")
        seen.add(instr.id)
        if instr.type == "init" and not instr.placement:
            errors.append(f"{where}: init carries no placement")
        if instr.type == "simd":
            if instr.cls is None:
                errors.append(f"{where}: simd carries no class (R4 needs one)")
            if not instr.participants and not instr.template:
                errors.append(f"{where}: simd has neither participants nor a template")
            if instr.mode not in ("intra", "inter"):
                errors.append(f"{where}: simd mode must be 'intra' or 'inter' (R4b)")
        if instr.type == "gate":
            if not instr.pairs and len(instr.ions) != 2:
                errors.append(f"{where}: gate needs `pairs` or exactly two `ions`")
            if instr.gate is None:
                errors.append(f"{where}: gate carries no gate name")
    if seen and prog.id_seq <= max(seen):
        # the allocator may never hand out an id that is already alive: `next_id()` is
        # what makes `Instruction.id` an identity, and an allocator that collides turns
        # it back into a position with extra steps.
        errors.append(
            f"id_seq={prog.id_seq} would re-issue a live id (highest in use is "
            f"{max(seen)})")
    if prog.instructions and prog.instructions[0].type != "init":
        errors.append("instructions[0]: a program must open with `init`")
    return errors


def iter_pairs(instr: Instruction) -> Iterable[tuple[str, str]]:
    """The gate's operand pairs, whether written as `pairs` or as two `ions`."""
    if instr.pairs:
        yield from instr.pairs
    elif len(instr.ions) == 2:
        yield (instr.ions[0], instr.ions[1])


def total_participants(instrs: Sequence[Instruction]) -> int:
    return sum(len(i.participants) for i in instrs)
