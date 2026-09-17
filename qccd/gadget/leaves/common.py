"""What every leaf gadget is built from: a testbench device, op programs, a home placement.

A leaf generator returns a `LeafBuild`; `qccd.gadget.characterize` turns it into a
characterized `Master`.  The generator's job is only to say what the op DOES, in TSIR --
everything the parent level is later told about the op (its duration, when ions cross
which port, how warm they are) is measured from the replay, never written down beside it.

**Stubs.**  A port is a device node on the gadget's boundary.  One step beyond it the
testbench adds a stub: a site in zone `port` that holds the whole bundle and can do
nothing else (no gate, no SPAM, no cooling).  An ion that leaves ends its op parked there;
an ion that arrives starts there.  The stub stands for the channel the parent will attach,
the way an ideal source or load stands for the rest of a circuit in a cell testbench, and
it is the only idealised thing in a leaf.  Handoffs between a stub and its port node use
the classes `port_in` / `port_out`, which entail no split or merge: the channel side of a
handoff is a single ion moving between neighbouring traps, like any conveyor step.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ...api import Machine
from ...ir.tsir import TSIR, Instruction, Participant

__all__ = ["LeafBuild", "PORT_ZONE", "declare_port_zone", "declare_port_classes",
           "add_port_stub", "gate1q", "TrackedProgram", "tidy"]

PORT_ZONE = "port"


@dataclass
class LeafBuild:
    """A leaf gadget before characterization."""

    master: "object"                     # qccd.gadget.model.Master, ops still empty
    machine: Machine                     # the testbench device, stubs included
    programs: dict[str, TSIR] = field(default_factory=dict)
    titles: dict[str, str] = field(default_factory=dict)
    notes: dict[str, list[str]] = field(default_factory=dict)
    params: dict[str, dict] = field(default_factory=dict)
    #: where every resting ion lives between ops
    home: dict[str, str] = field(default_factory=dict)
    #: each resting or visiting ion's role: data, ancilla, messenger, magic
    roles: dict[str, str] = field(default_factory=dict)
    #: per op, where every ion must be when the op ends (the placement contract)
    expect_end: dict[str, dict[str, str]] = field(default_factory=dict)
    #: per op, `[instruction id, t0, t1]` from the replay (filled by characterize)
    times: dict[str, list] = field(default_factory=dict)
    #: builder state an on-demand op needs later (a memory's ring order, its code)
    extra: dict = field(default_factory=dict)
    master_name: str = ""
    #: per op, the stabilizer-flow specification its circuit must meet (`logic.spec.Spec`)
    specs: dict = field(default_factory=dict)
    #: per op, the reference experiment of its fault distance (`distance_report` kwargs,
    #: plus `expect`: "ft" or a sentence saying why the op is not fault-tolerant)
    experiments: dict = field(default_factory=dict)
    #: per op, any other logic check: a function of the op's circuit returning a report
    logic_checks: dict = field(default_factory=dict)


def declare_port_zone(m: Machine) -> Machine:
    return m.set_zone(PORT_ZONE, capacity=1, gate=False, spam=False, cool=False,
                      note="testbench stub: an ideal ion source/sink standing for the "
                           "channel a parent gadget attaches here")


def declare_port_classes(m: Machine) -> Machine:
    m.declare_class("port_in", type="shift", orbit="port", direction="inward",
                    note="one ion from the channel stub onto the port node")
    m.declare_class("port_out", type="shift", orbit="port", direction="outward",
                    note="one ion from the port node onto the channel stub")
    return m


def add_port_stub(m: Machine, stub: str, pos: tuple[float, float], port_node: str,
                  width: int, segment: str) -> Machine:
    """A stub site holding up to `width` ions, one segment beyond `port_node`."""
    m.add_site(stub, *pos, zone=PORT_ZONE, capacity=max(1, int(width)),
               labels=["port", "port_stub"], to=[port_node], segment_ids=[segment])
    return m


def tidy(m: Machine, programs, keep_classes=()) -> Machine:
    """Drop the zone types and movement classes the gadget borrowed but never uses.

    Every leaf starts from the template architecture's declarations, and the studio lints
    the leftovers (`zone_unused`, `class_no_participants`) -- rightly: a gadget's document
    should declare what its device is, not what another device was.  Classes an op compiled
    later may still need are named in `keep_classes`.
    """
    zones_used = {n.zone_type for n in m.arch.device.nodes.values() if n.zone_type}
    m.only_zones(*[z for z in m.arch.zone_types if z in zones_used])
    used = set(keep_classes)
    for prog in programs:
        for ins in prog.instructions:
            if ins.type == "simd" and ins.cls:
                used.add(ins.cls)
    declared = [e["id"] for e in (m.arch.control.get("classes", {}) or {}).get("extra", []) or []]
    m.only_classes(*[c for c in declared if c in used])
    return m


def gate1q(prog: TSIR, name: str, ions, sites=(), **meta) -> TSIR:
    """A batch of single-qubit gates driven together: one instruction, `arity=1`.

    `Program.gate` only spells pairs; the IR has always carried batched single-qubit
    layers (`Instruction.arity`), and this is the one place a leaf emits one.
    """
    prog.add(Instruction(type="gate", id=prog.next_id(), gate=name, mode="intra",
                         arity=1, ions=tuple(ions), sites=tuple(sites), meta=dict(meta)))
    return prog


class TrackedProgram:
    """A TSIR under construction that knows where every ion is.

    A leaf op is "move these ions there, then do something where they are".  Tracking
    positions while emitting makes each op a short loop over what should happen, instead
    of index arithmetic that a later move would silently invalidate.  A cycle's moves are
    given as `(ion, destination)`; the route is the one segment joining them unless `via`
    names the segments for that ion (a crossing through a junction, say).
    """

    def __init__(self, machine: Machine, name: str, placement: dict[str, str]):
        self.m = machine
        self.dev = machine.arch.device
        self.prog = TSIR(name=name, arch_spec=machine.spec_path)
        self.pos = dict(placement)
        self.prog.add(Instruction(type="init", id=self.prog.next_id(),
                                  placement=dict(placement),
                                  quanta={k: 0.0 for k in placement}))

    def at(self, node: str) -> list[str]:
        return [ion for ion, where in self.pos.items() if where == node]

    def move(self, cls: str, moves, *, via: dict[str, list[str]] | None = None,
             **meta) -> None:
        parts = []
        for ion, dst in moves:
            src = self.pos[ion]
            path = (via or {}).get(ion)
            if path is None:
                path = [self.dev.segment_between(src, dst).id]
            parts.append(Participant(ion, src, dst, tuple(path)))
            self.pos[ion] = dst
        if parts:
            self.prog.add(Instruction(type="simd", id=self.prog.next_id(), cls=cls,
                                      mode="inter", participants=tuple(parts),
                                      meta=dict(meta)))

    def gate(self, name: str, pairs, **meta) -> None:
        sites = tuple(self.pos[a] for a, _ in pairs)
        self.prog.add(Instruction(type="gate", id=self.prog.next_id(), gate=name,
                                  mode="intra", pairs=tuple(tuple(p) for p in pairs),
                                  sites=sites, meta=dict(meta)))

    def gate1(self, name: str, ions, **meta) -> None:
        gate1q(self.prog, name, ions, sites=[self.pos[i] for i in ions], **meta)

    def measure(self, ions, **meta) -> None:
        self.prog.add(Instruction(type="measure", id=self.prog.next_id(),
                                  ions=tuple(ions), meta=dict(meta)))

    def reset(self, ions, **meta) -> None:
        self.prog.add(Instruction(type="reset", id=self.prog.next_id(),
                                  ions=tuple(ions), meta=dict(meta)))

    def cool(self, **meta) -> None:
        self.prog.add(Instruction(type="cool", id=self.prog.next_id(), broadcast=True,
                                  meta=dict(meta)))
