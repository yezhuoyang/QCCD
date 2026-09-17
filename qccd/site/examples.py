"""The examples behind the site's Language and Rules pages.

Every example is a small programme written as the records the studio's Write pane
produces (`{"method", "args", "kwargs"}`), so the text the page shows is exactly what runs:
`Program.apply_calls` turns the records into instructions, the verifier judges them, and
`Machine.render` writes the same page the studio opens on them.  Nothing here is a
second implementation of a rule or a price; the verdicts are quoted from `qccd.verify`.

The examples stand on several small devices, each chosen for what it shows best: a
four-site linear register (no junctions, no loop), an eight-site racetrack (one loop, no
junctions), a 2x3 lattice (real T-junctions beside plain corners), two concentric loops
joined at one coupling (a junction between a data loop and a trap loop), the six-site
ring with dock spurs the course stands on, and wide-trap variants for the crowding rules.
"""

from __future__ import annotations

import copy
import dataclasses
import json
from typing import Any

from ..api import Machine, _template_doc
from ..cost import corrected_model, deck_model
from ..ir import TSIR, Instruction, Participant
from ..verify import verify
from ..verify.rules import RULE_SOURCES, RULE_STATEMENTS


def rec(method: str, *args, **kwargs) -> dict:
    return {"method": method, "args": list(args), "kwargs": dict(kwargs)}


# ------------------------------------------------------------------- the devices

CHAIN = {"generator": "chain", "params": {"n": 4}, "name": "chain4",
         "about": "a four-site linear register C0–C3: every site a trap (gates, SPAM, cooling; capacity 2), "
                  "two open ends, no junctions and no loop"}
CHAIN_BIG = {"generator": "chain", "params": {"n": 2, "site_zone": "big"}, "name": "chain2big", "template": "big",
             "about": "two wide traps C0 and C1 in a line, zone `big`: capacity 32, gates allowed"}
RACE = {"generator": "racetrack", "params": {"straight": 4}, "name": "race8",
        "about": "an eight-site racetrack S0–S7: one closed loop L0 of trap sites (gates, SPAM, cooling; "
                 "capacity 2) and no junctions, so every bend is ordinary transport"}
GRID = {"generator": "grid", "params": {"a": 2, "b": 3}, "name": "grid2x3",
        "about": "a 2×3 lattice: six lattice nodes, of which J0_1 and J1_1 are degree-3 T-junctions and the "
                 "other four degree-2 corners, with one trap site (T…) on every wire between them"}
DUAL = {"generator": "dual_loop", "params": {"width": 3, "couplings": [1]}, "name": "dual3",
        "about": "two concentric loops: an inner data loop D (DT0–DT2, DB0–DB2; capacity 2, no gates) and an "
                 "outer trap loop A (AT0–AT2, AB0–AB2), joined at x = 1 where DT1 and AT1 are degree-3 junctions"}
RING = {"generator": "ring", "params": {"width": 3, "height": 2, "verticals": 2}, "name": "ring6d",
        "about": "a six-site loop with dock spurs at S0 and S3; the spur ends A0 and A3 are trap "
                 "sites (gates, measurement, cooling; capacity 2), the loop sites hold 2 and cannot gate"}
RING_LOAD = {"generator": "ring", "params": {"width": 3, "height": 2, "verticals": 2, "ancilla_zone": "load"},
             "name": "ring6load", "about": "the six-site ring with load-zone spur ends (capacity 8, no gates)"}
RING_BIG = {"generator": "ring", "params": {"width": 3, "height": 2, "verticals": 2, "ancilla_zone": "big"},
            "name": "ring6big", "template": "big",
            "about": "the six-site ring with a `big` spur zone: capacity 32, gates allowed"}

# -------------------------------------------------------- explicit devices (geometry)

def _explicit(name: str, about: str, nodes: dict, segments: list[tuple[str, str, str]]) -> dict:
    """A device drawn node by node: `nodes` maps id -> (x, y), every node a trap site;
    `segments` lists (id, a, b).  The geometry rules judge the drawing itself, so these
    examples are devices, not programmes."""
    return {"generator": "explicit", "name": name, "about": about,
            "nodes": dict(nodes), "segments": list(segments)}


STAR5 = _explicit("star5", "five rails meeting at one node J: a degree-5 junction, which no 2D surface trap has",
                  {"J": (0, 0), "N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0), "NE": (1, 1)},
                  [("rN", "J", "N"), ("rS", "J", "S"), ("rE", "J", "E"), ("rW", "J", "W"), ("rNE", "J", "NE")])
CROSS4 = _explicit("cross4", "four rails meeting at one node J at right angles: an X-junction, degree 4",
                   {"J": (0, 0), "N": (0, 1), "S": (0, -1), "E": (1, 0), "W": (-1, 0)},
                   [("rN", "J", "N"), ("rS", "J", "S"), ("rE", "J", "E"), ("rW", "J", "W")])
FORK30 = _explicit("fork30", "a fork whose two branches leave J only 30 degrees apart",
                   {"J": (0, 0), "W": (-1, 0), "A": (2, 0), "B": (1.732, 1)},
                   [("rW", "J", "W"), ("rA", "J", "A"), ("rB", "J", "B")])
FORK90 = _explicit("fork90", "the same fork drawn as a T-junction: the branches leave J 90 degrees apart",
                   {"J": (0, 0), "W": (-1, 0), "A": (1, 0), "B": (0, 1)},
                   [("rW", "J", "W"), ("rA", "J", "A"), ("rB", "J", "B")])
CROSSING = _explicit("crossing", "two rails drawn across each other with no node where they meet",
                     {"A": (-1, 0), "B": (1, 0), "C": (0, -1), "D": (0, 1)},
                     [("rAB", "A", "B"), ("rCD", "C", "D")])
PLANAR = _explicit("planar", "the same four sites joined through a junction J where the rails meet",
                   {"A": (-1, 0), "B": (1, 0), "C": (0, -1), "D": (0, 1), "J": (0, 0)},
                   [("rA", "A", "J"), ("rB", "J", "B"), ("rC", "C", "J"), ("rD", "J", "D")])

DEVICES = (CHAIN, RACE, GRID, DUAL, RING, CHAIN_BIG, RING_LOAD, RING_BIG,
           STAR5, CROSS4, FORK30, FORK90, CROSSING, PLANAR)

_MACHINES: dict[str, Machine] = {}


def machine(dev: dict) -> Machine:
    """The device, built the way the studio builds one: the named generator with its
    parameters, borrowing the default template's physics and control plane."""
    if dev["name"] not in _MACHINES:
        template = None
        if dev.get("template") == "big":
            template = copy.deepcopy(_template_doc(None))
            template["zone_types"]["big"] = {"capacity": 32, "gate": True, "spam": True, "cool": True,
                                             "note": "a wide trap for the crowding rules"}
        if dev["generator"] == "explicit":
            from ..arch.device import Device, Node, Segment
            nodes = {nid: Node(id=nid, pos=(float(x), float(y)), kind="site", zone_type="trap", labels=("trap",))
                     for nid, (x, y) in dev["nodes"].items()}
            segs = {}
            for sid, a, b in dev["segments"]:
                ax, ay = nodes[a].pos
                bx, by = nodes[b].pos
                segs[sid] = Segment(id=sid, ends=(a, b), length=((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5,
                                    capacity=1, loop=None, labels=("rail",))
            device = Device(nodes=nodes, segments=segs, loops={}, generator="explicit", params={})
            _MACHINES[dev["name"]] = Machine.from_device(device, name=dev["name"], template=template)
            return _MACHINES[dev["name"]]
        ctor = getattr(Machine, dev["generator"])
        _MACHINES[dev["name"]] = ctor(**dev["params"], name=dev["name"], template=template)
    return _MACHINES[dev["name"]]


#: The walk the lattice examples share: from the trap on the left column's lower wire,
#: through the T-junction J0_1, to the trap on the middle horizontal wire.
GRID_WALK = ["T0_0v", "J0_1", "T0_1h"]
#: Its bend twin: through the corner J0_0 instead, which R18 prices as plain transport.
GRID_BEND = ["T0_0v", "J0_0", "T0_0h"]


# ------------------------------------------------------------------- the language

GRAMMAR = """programme  ::=  { statement NEWLINE }
statement  ::=  "p." verb "(" [ arguments ] ")"
verb       ::=  "init" | "fill" | "move" | "shuttle" | "simd" | "rotate"
             |  "gate" | "cool" | "measure" | "reset" | "barrier" | "claim"
arguments  ::=  argument { "," argument }
argument   ::=  literal | name "=" literal            (keywords after positionals)
literal    ::=  string | number | "True" | "False" | "None"
             |  "[" [ literal { "," literal } ] "]"
             |  "{" [ string ":" literal { "," string ":" literal } ] "}"
"""

#: One entry per statement: the signature, what it means as a transition of the machine
#: state, what it costs, the rules that judge it, the IR it becomes, and one example.
VERBS: list[dict] = [
    {"verb": "init", "sig": 'p.init({ion: site, ...}, quanta=None)',
     "what": "Places the named ions on the named sites. The first statement of every programme: a device starts empty.",
     "state": "position := placement; n̄(ion) := 0, or the quanta given. Nothing moves.",
     "cost": "0 hops, 0 µs.", "rules": ["R1", "R2"], "ir": "init",
     "device": CHAIN, "example": [rec("init", {"d0": "C1", "d1": "C0"})]},
    {"verb": "fill", "sig": 'p.fill(loop=None, prefix="d")',
     "what": "Places one ion on every slot of a loop, d0 on its first site and so on: the packed ring in one line.",
     "state": "position := every site of the loop, in order. Nothing moves.",
     "cost": "0 hops, 0 µs.", "rules": ["R1"], "ir": "init",
     "device": RACE, "example": [rec("fill")]},
    {"verb": "move", "sig": 'p.move(ion, src, dst, via=None, cls="shuttle")',
     "what": "One ion, one hop, in its own cycle: the shortest form of a transport instruction.",
     "state": "position(ion) := dst; the segment between src and dst, or the segments in via, is crossed once; n̄ grows by the primitives crossed.",
     "cost": "the segment's hop, plus a junction_cross priced by the degree of any junction transited; duration from the device's curves.",
     "rules": ["R1", "R2", "R3", "R4", "R8"], "ir": "simd with one participant",
     "device": CHAIN, "example": [rec("init", {"d0": "C1"}), rec("move", "d0", "C1", "C2")]},
    {"verb": "shuttle", "sig": 'p.shuttle(ion, [site, site, ...], cls="shuttle")',
     "what": "Walks one ion along a path of site ids, one trap-to-trap step per cycle. Junctions on the way are transited, never rested on.",
     "state": "one cycle per step of the path; after each, position(ion) is the next site.",
     "cost": "the sum of the hops; here the walk from T0_0v to T0_1h transits the T-junction J0_1, and the crossing is charged at degree 3.",
     "rules": ["R1", "R2", "R3", "R4", "R8", "R18"], "ir": "one simd per step",
     "device": GRID, "example": [rec("init", {"d0": "T0_0v"}), rec("shuttle", "d0", GRID_WALK)]},
    {"verb": "simd", "sig": 'p.simd(cls, [[ion, from, to], [ion, from, to, via], ...], mode="inter")',
     "what": "Several ions in one cycle, driven together under one movement class. The rules judge the cycle as a whole: one waveform, one direction per loop, no exchanges.",
     "state": "every listed ion moves at once; position := the listed destinations.",
     "cost": "cost sums over the participants; depth and duration take the maximum, because the cycle waits for its slowest ion.",
     "rules": ["R1", "R2", "R3", "R4", "R4d", "R5", "R8", "R11"], "ir": "simd",
     "device": RACE, "example": [rec("init", {"d0": "S1", "d1": "S4"}), rec("simd", "shuttle", [["d0", "S1", "S2"], ["d1", "S4", "S5"]])]},
    {"verb": "rotate", "sig": 'p.rotate(delta, loop=None)',
     "what": "Turns a closed loop: every ion on it moves delta slots forward (negative: back). One instruction, one movement template driven |delta| times.",
     "state": "position(ion) := slot + delta (mod the loop) for every ion on the loop.",
     "cost": "|delta| unit cycles, each the full loop's hops summed and the deepest edge as its depth.",
     "rules": ["R4", "R11"], "ir": "simd with a loop_shift template",
     "device": DUAL, "example": [rec("fill", loop="A", prefix="a"), rec("rotate", 1, loop="A")]},
    {"verb": "gate", "sig": 'p.gate(name, [[control, target], ...], sites=None)',
     "what": "A gate cycle. Two-qubit: every pair sits in one site whose zone allows gates, control first. Single-qubit: an empty pair list and the site named.",
     "state": "positions unchanged; the gate's error is evaluated from each ion's n̄ at gate time.",
     "cost": "0 hops; the gate's duration from the primitive table.",
     "rules": ["R6", "R6b", "R7", "R7c", "R12", "R13", "R16"], "ir": "gate",
     "device": CHAIN, "example": [rec("init", {"d0": "C0", "d1": "C0"}), rec("cool"), rec("gate", "CX", [["d0", "d1"]])]},
    {"verb": "cool", "sig": 'p.cool(ions=None)',
     "what": "Cools ions back toward the motional ground state: all of them when no list is given (a broadcast), else the listed ones.",
     "state": "n̄(ion) := what the cooling primitive leaves, for the cooled ions.",
     "cost": "the cooling primitive's duration; one op for every ion under broadcast.",
     "rules": ["R6", "R7", "R7c"], "ir": "cool",
     "device": GRID, "example": [rec("init", {"d0": "T0_0v"}), rec("shuttle", "d0", GRID_WALK), rec("cool")]},
    {"verb": "measure", "sig": 'p.measure([ion, ...])',
     "what": "Reads the listed ions out. Each must sit in a zone with SPAM (state preparation and measurement).",
     "state": "positions unchanged.", "cost": "the measure primitive's duration.", "rules": ["R6"], "ir": "measure",
     "device": RACE, "example": [rec("init", {"d0": "S3"}), rec("measure", ["d0"])]},
    {"verb": "reset", "sig": 'p.reset([ion, ...])',
     "what": "Puts the listed ions back to |0>. The same zone requirement as measuring.",
     "state": "positions unchanged.", "cost": "the reset primitive's duration.", "rules": ["R6"], "ir": "reset",
     "device": CHAIN, "example": [rec("init", {"d0": "C2"}), rec("reset", ["d0"])]},
    {"verb": "barrier", "sig": 'p.barrier()',
     "what": "A cycle in which nothing happens: an explicit synchronisation point, a marker between phases of a programme.",
     "state": "unchanged.", "cost": "0 hops, 0 µs.", "rules": [], "ir": "barrier",
     "device": RACE, "example": [rec("init", {"d0": "S0"}), rec("move", "d0", "S0", "S1"), rec("barrier"), rec("move", "d0", "S1", "S2")]},
    {"verb": "claim", "sig": 'p.claim(total_cost=..., total_steps=..., ...)',
     "what": "Annotates the programme with the totals its author claims. Claims are not content: the replay recomputes everything and R9 rejects any claim it cannot reproduce.",
     "state": "unchanged; the claim is recorded on the programme.", "cost": "none.", "rules": ["R9"], "ir": "the programme's metrics",
     "device": CHAIN, "check_metrics": True,
     "example": [rec("init", {"d0": "C0"}), rec("move", "d0", "C0", "C1"), rec("claim", total_cost=1, total_steps=1)]},
]

#: The IR beneath the text.
IR_TABLE = [("init", "init", "placement, quanta"), ("fill", "init", "placement over a loop"),
            ("move / shuttle / simd", "simd", "class, mode, participants (ion, from, to, via)"),
            ("rotate", "simd", "class, a loop_shift template, holds"),
            ("gate", "gate", "gate, pairs, sites"), ("cool", "cool", "broadcast or ions"),
            ("measure / reset", "measure / reset", "ions"), ("barrier", "barrier", "nothing"),
            ("claim", "metrics", "claimed totals, checked by R9")]


# ------------------------------------------------------------------- the rules

def _hot_gate():
    """On the lattice: d0 is cooled, then walks through the T-junction and meets d1 hot."""
    return [rec("init", {"d0": "T0_0v", "d1": "T0_1h"}), rec("cool"), rec("shuttle", "d0", GRID_WALK), rec("gate", "CX", [["d0", "d1"]])]


def _cold_gate():
    return [rec("init", {"d0": "T0_0v", "d1": "T0_1h"}), rec("shuttle", "d0", GRID_WALK), rec("cool"), rec("gate", "CX", [["d0", "d1"]])]


def _chain_gate():
    """On the register: d0 walks one site over, is cooled, and gates with d1."""
    return [rec("init", {"d0": "C1", "d1": "C0"}), rec("shuttle", "d0", ["C1", "C0"]), rec("cool"), rec("gate", "CX", [["d0", "d1"]])]


def _r4b_ir(m: Machine) -> TSIR:
    """R4b's violation cannot be written in the language: a cycle that both moves and
    gates has no statement.  Only the IR can say it, so the example is the IR."""
    return TSIR(name="mixed_cycle", arch_spec=m.name, instructions=[
        Instruction(type="init", id=0, placement={"d0": "C1", "d1": "C2"}, quanta={"d0": 0.0, "d1": 0.0}),
        Instruction(type="simd", id=1, cls="shuttle", mode="inter", gate="CX", pairs=(("d0", "d1"),),
                    participants=(Participant("d0", "C1", "C0"),))])


#: One entry per rule.  `checks` is what the verifier does, in one sentence; `pass` and
#: `fail` are the two programmes; a rule the verifier cannot judge in the page says so
#: in `note` and shows what it reports instead.
RULES: list[dict] = [
    {"id": "R1", "checks": "after every cycle, no site holds more ions than its capacity.",
     "device": CHAIN,
     "fail": {"program": [rec("init", {"d0": "C2", "d1": "C2", "d2": "C1"}), rec("move", "d2", "C1", "C2")],
              "why": "C2 already holds two ions, its capacity; a third arrives."},
     "pass": {"program": [rec("init", {"d0": "C2", "d2": "C1"}), rec("move", "d2", "C1", "C2")],
              "why": "the same hop into a site with room for it."}},
    {"id": "R2", "checks": "a junction (a node of degree 3 or more) holds at most one ion, and at most one ion crosses it in a cycle.",
     "device": DUAL,
     "fail": {"program": [rec("init", {"d0": "DT1", "d1": "DT0"}), rec("move", "d1", "DT0", "DT1")],
              "why": "DT1 is where the coupling meets the inner loop, a degree-3 node; d0 is resting there when d1 arrives."},
     "pass": {"program": [rec("init", {"d1": "DT0"}), rec("move", "d1", "DT0", "DT1")],
              "why": "one ion at the junction."}},
    {"id": "R3", "checks": "no segment carries more ions in one cycle than its capacity (1 on every shipped device).",
     "device": RACE,
     "fail": {"program": [rec("init", {"d0": "S1", "d1": "S1"}), rec("simd", "shuttle", [["d0", "S1", "S2"], ["d1", "S1", "S2"]])],
              "why": "two ions on the S1–S2 segment in the same cycle."},
     "pass": {"program": [rec("init", {"d0": "S1", "d1": "S1"}), rec("move", "d0", "S1", "S2"), rec("move", "d1", "S1", "S2")],
              "why": "the same two hops, one cycle each."}},
    {"id": "R4", "checks": "every movement class a cycle uses is declared by the device, and a cycle uses no more classes than the control plane allows.",
     "device": CHAIN,
     "fail": {"program": [rec("init", {"d0": "C1"}), rec("move", "d0", "C1", "C2", cls="teleport")],
              "why": "no class named teleport exists on this device."},
     "pass": {"program": [rec("init", {"d0": "C1"}), rec("move", "d0", "C1", "C2", cls="shuttle")],
              "why": "shuttle is declared."}},
    {"id": "R4d", "checks": "a cycle is drivable by the declared channels: along a loop every ion on a shared channel moves the same signed hop, and the docks on a channel act the same way.",
     "device": RACE,
     "fail": {"program": [rec("init", {"d0": "S0", "d1": "S4"}), rec("simd", "shuttle", [["d0", "S0", "S1"], ["d1", "S4", "S3"]])],
              "why": "one waveform drives the racetrack, and the two ions ask it to move opposite ways at once."},
     "pass": {"program": [rec("init", {"d0": "S0", "d1": "S4"}), rec("simd", "shuttle", [["d0", "S0", "S1"], ["d1", "S4", "S5"]])],
              "why": "both hop one slot the same way round: one waveform does it."},
     "note": "R4d is a clause of R4 (the channel rule), so the verifier files its sentences under R4; the page asks for R4d alone to show them."},
    {"id": "R4b", "checks": "a cycle has one mode, and never mixes transport with a gate.",
     "device": CHAIN,
     "fail": {"ir": _r4b_ir, "why": "one cycle that both shuttles d0 and fires a CX. The language has no statement for this: only the IR can write it, and R4b is what refuses it."},
     "pass": {"program": [rec("init", {"d0": "C0", "d1": "C0"}), rec("cool"), rec("move", "d0", "C0", "C1"), rec("move", "d0", "C1", "C0"), rec("gate", "CX", [["d0", "d1"]])],
              "why": "transport and the gate as separate cycles, which is all the language can say."}},
    {"id": "R5", "checks": "no two ions exchange positions along one segment in one cycle.",
     "device": CHAIN,
     "fail": {"program": [rec("init", {"d0": "C1", "d1": "C2"}), rec("simd", "shuttle", [["d0", "C1", "C2"], ["d1", "C2", "C1"]])],
              "why": "d0 and d1 swap across the C1–C2 segment. An exchange also breaks R3 and the channel clause of R4: one segment, two ions, two directions, one waveform."},
     "pass": {"program": [rec("init", {"d0": "C1", "d1": "C2"}), rec("move", "d0", "C1", "C2"), rec("move", "d1", "C2", "C1")],
              "why": "the same swap in two cycles, resting together at C2 in between."}},
    {"id": "R6", "checks": "gates, measurement and cooling happen only in a site whose zone type has that capability, at the site the replay says the ion is in.",
     "device": DUAL,
     "fail": {"program": [rec("init", {"d0": "DT0", "d1": "DT0"}), rec("cool"), rec("gate", "CX", [["d0", "d1"]])],
              "why": "DT0 is a data site on the inner loop: capacity 2, gate=false."},
     "pass": {"program": [rec("init", {"d0": "AT0", "d1": "AT0"}), rec("cool"), rec("gate", "CX", [["d0", "d1"]])],
              "why": "AT0 on the outer loop is a trap site: gates, SPAM and cooling allowed."}},
    {"id": "R6b", "checks": "both ions of a two-qubit gate are in the same site; a named site is cross-checked against the replay.",
     "device": CHAIN,
     "fail": {"program": [rec("init", {"d0": "C0", "d1": "C1"}), rec("cool"), rec("gate", "CX", [["d0", "d1"]], ["C0"])],
              "why": "d1 is still at C1, one site over, when the gate at C0 fires."},
     "pass": {"program": [rec("init", {"d0": "C0", "d1": "C0"}), rec("cool"), rec("gate", "CX", [["d0", "d1"]])],
              "why": "both ions at C0."}},
    {"id": "R7", "checks": "at gate time both ions carry no more motional quanta than the gate's budget (ms_gate.max_quanta).",
     "device": GRID, "model": "corrected",
     "fail": {"program": _hot_gate(), "why": "d0 is cooled, then walks through the T-junction J0_1 (3 quanta) and meets the gate hot."},
     "pass": {"program": _cold_gate(), "why": "the same walk, cooled after it."}},
    {"id": "R7b", "checks": "a per-gate-zone thermal duty-cycle budget, not just the instantaneous occupancy.",
     "device": RACE, "model": "corrected",
     "note": "No architecture declares a per-zone duty-cycle budget yet, so the verifier reports R7b as skipped, with that reason, rather than as passed. A busy trap is shown so the skip is visible.",
     "pass": {"program": [rec("init", {"d0": "S0", "d1": "S0"}), rec("cool"), rec("gate", "CX", [["d0", "d1"]]), rec("gate", "CX", [["d1", "d0"]]), rec("gate", "CX", [["d0", "d1"]])],
              "why": "three gates in a row in one site: the verdict is skipped, not passed.", "expect": "skipped"}},
    {"id": "R7c", "checks": "under a model that tracks heating, a programme with two-qubit gates schedules cooling somewhere; under a model without heating the rule is skipped.",
     "device": CHAIN, "model": "corrected",
     "fail": {"program": [rec("init", {"d0": "C0", "d1": "C0"}), rec("gate", "CX", [["d0", "d1"]])],
              "why": "a gate and no cooling operation anywhere in the programme."},
     "pass": {"program": [rec("init", {"d0": "C0", "d1": "C0"}), rec("cool"), rec("gate", "CX", [["d0", "d1"]])],
              "why": "one broadcast cool."}},
    {"id": "R8", "checks": "the ion→site map stays a bijection over time: the ion set is invariant, no ion participates twice in a cycle, no position changes without a participant.",
     "device": RACE,
     "fail": {"program": [rec("init", {"d0": "S1"}), rec("simd", "shuttle", [["d0", "S1", "S2"], ["d0", "S1", "S2"]])],
              "why": "d0 is listed twice in one cycle (R3 fires too: the segment carries it twice)."},
     "pass": {"program": [rec("init", {"d0": "S1", "d1": "S4"}), rec("simd", "shuttle", [["d0", "S1", "S2"], ["d1", "S4", "S5"]])],
              "why": "two different ions."}},
    {"id": "R9", "checks": "every claimed total, per-batch figure and per-instruction annotation equals what the replay computes.",
     "device": GRID, "model": "corrected", "check_metrics": True,
     "fail": {"program": _cold_gate() + [rec("claim", total_steps=3, total_cost=2)],
              "why": "the programme claims 3 steps and cost 2; the replay finds 4 and 3."},
     "pass": {"program": _cold_gate() + [rec("claim", total_steps=4, total_cost=3)],
              "why": "the true totals."}},
    {"id": "R10", "checks": "the compiled programme implements the input circuit: every circuit operation realised once, in order per qubit, with both ions of each two-qubit gate together.",
     "device": CHAIN, "model": "corrected",
     "note": "The page's verifier skips R10: it needs the circuit and a symbolic permutation with Pauli-frame tracking. On the leaderboard R10 is judged by the proved Lean checker on the compiler's certificate, which is what the entries' badges report.",
     "pass": {"program": _chain_gate(), "why": "a gate with no circuit to check it against: skipped, with the reason.", "expect": "skipped"}},
    {"id": "R11", "checks": "shuttling along a loop is unidirectional within a cycle, and every junction degree the device has can be priced.",
     "device": RACE,
     "fail": {"program": [rec("init", {"d0": "S1", "d1": "S5"}), rec("simd", "shuttle", [["d0", "S1", "S2"], ["d1", "S5", "S4"]])],
              "why": "d0 goes one way round the racetrack, d1 the other, in one cycle (R4's channel clause fires too)."},
     "pass": {"program": [rec("init", {"d0": "S1", "d1": "S5"}), rec("simd", "shuttle", [["d0", "S1", "S2"], ["d1", "S5", "S6"]])],
              "why": "both the same way."}},
    {"id": "R12", "checks": "at most one gate per trap per cycle.",
     "device": CHAIN_BIG,
     "fail": {"program": [rec("init", {"d0": "C0", "d1": "C0", "d2": "C0", "d3": "C0"}), rec("cool"), rec("gate", "CX", [["d0", "d1"], ["d2", "d3"]])],
              "why": "two pairs gated in C0 at once."},
     "pass": {"program": [rec("init", {"d0": "C0", "d1": "C0", "d2": "C1", "d3": "C1"}), rec("cool"), rec("gate", "CX", [["d0", "d1"], ["d2", "d3"]])],
              "why": "the two pairs in two traps: inter-trap parallelism is free."}},
    {"id": "R13", "checks": "no more than 15 ions in a trap at gate time.",
     "device": CHAIN_BIG,
     "fail": {"program": [rec("init", {f"d{i}": "C0" for i in range(17)}), rec("cool"), rec("gate", "CX", [["d0", "d1"]])],
              "why": "a chain of 17 in C0 when the gate fires."},
     "pass": {"program": [rec("init", {f"d{i}": "C0" for i in range(15)}), rec("cool"), rec("gate", "CX", [["d0", "d1"]])],
              "why": "fifteen."}},
    {"id": "R14", "checks": "an ion splits out of a trap only from its edge; leaving a chain of more than two needs an accounted gate_swap.",
     "device": RING_LOAD,
     "fail": {"program": [rec("init", {"d0": "A0", "d1": "A0", "d2": "A0"}), rec("move", "d0", "A0", "S0", cls="dock")],
              "why": "d0 splits from a chain of three with no swap accounted."},
     "pass": {"program": [rec("init", {"d0": "A0", "d1": "A0"}), rec("move", "d0", "A0", "S0", cls="dock")],
              "why": "from a chain of two every ion is at an edge."}},
    {"id": "R15", "checks": "motional quanta compose with an interference term; the replay adds them, which is an upper bound.",
     "device": DUAL, "model": "corrected",
     "note": "The corpus gives no secular phase model for these primitives, so every quanta figure is additive and the verifier reports R15 as partial, with that reason, on every programme that heats.",
     "pass": {"program": [rec("init", {"d0": "DT0"}), rec("shuttle", "d0", ["DT0", "DT1", "AT1"]), rec("cool")],
              "why": "a walk from the data loop through the coupling to the trap loop: the reported n̄ is an upper bound, and R15 says so.", "expect": "partial"}},
    {"id": "R16", "checks": "a two-qubit gate's error is evaluated from the n̄ its ions carry at gate time, not taken as a constant.",
     "device": GRID, "model": "corrected",
     "note": "R16 is a formula the replay applies, not a verdict it can fail: the two examples show it applied. Compare the gate error the replay reports for the hot gate and the cooled one.",
     "fail": {"program": _hot_gate(), "why": "the gate on a hot ion: the error read off n̄ ≈ 3.2, what the T-junction cost it (R7 fires for the same reason).", "expect": "any"},
     "pass": {"program": _cold_gate(), "why": "cooled first: the error read off n̄ ≈ 0."}},
    {"id": "R17", "checks": "anomalous heating accrues with elapsed time whether or not an ion moves; skipped under a model that does not model time.",
     "device": CHAIN,
     "fail": {"program": [rec("init", {"d0": "C0", "d1": "C3"}), rec("shuttle", "d0", ["C0", "C1", "C2"]), rec("cool")], "model": "deck",
              "why": "under the deck's cost model there is no clock, so R17 is skipped with that reason.", "expect": "skipped"},
     "pass": {"program": [rec("init", {"d0": "C0", "d1": "C3"}), rec("shuttle", "d0", ["C0", "C1", "C2"]), rec("cool")], "model": "corrected",
              "why": "under the corrected model d1 never moves and still heats while d0 walks: its anomalous quanta are listed below."}},
    {"id": "R18", "checks": "a node is a junction only where three or more trap axes meet; junction cost is charged by that degree, read off the expanded graph, never declared.",
     "device": GRID,
     "note": "R18 holds by construction: the price of a hop is looked up by the degree the graph reports. The two examples walk the same ion out of the same trap, once through a T-junction and once round a corner.",
     "fail": {"program": [rec("init", {"d0": "T0_0v"}), rec("shuttle", "d0", GRID_WALK)],
              "why": "T0_0v → J0_1 → T0_1h transits J0_1, where three wires meet: a junction_cross charged at degree 3.", "expect": "any", "tag": "junction"},
     "pass": {"program": [rec("init", {"d0": "T0_0v"}), rec("shuttle", "d0", GRID_BEND)],
              "why": "T0_0v → J0_0 → T0_0h transits the corner J0_0, where only two wires meet: a bend, priced as plain transport.", "tag": "bend"}},
    {"id": "R19", "checks": "no node joins more rails than budget.max_junction_degree (4 unless the document says otherwise): a 2D surface trap has T- and X-junctions and nothing denser.",
     "device": CROSS4,
     "note": "R19 is judged from the drawing, before any programme runs; the two examples are two devices, each shown with one resting ion.",
     "fail": {"device": STAR5, "program": [rec("init", {"d0": "N"})],
              "why": "J joins five rails. Where a fifth rail would go there is no room for its RF electrodes on a plane."},
     "pass": {"device": CROSS4, "program": [rec("init", {"d0": "N"})],
              "why": "J joins four rails at right angles: an X-junction."}},
    {"id": "R20", "checks": "at every node with two or more rails, any two of them subtend at least budget.min_rail_angle_deg (60 degrees unless the document says otherwise), measured from the node positions.",
     "device": FORK90,
     "note": "60 degrees is the default the reviewers proposed as an example; a real junction is usually symmetric, so 90 degrees for an X. A document may declare its own minimum.",
     "fail": {"device": FORK30, "program": [rec("init", {"d0": "W"})],
              "why": "the branches to A and B leave J 30 degrees apart: their RF rails would overlap before the ion could tell them apart."},
     "pass": {"device": FORK90, "program": [rec("init", {"d0": "W"})],
              "why": "the same fork as a T: 90 degrees between the branches."}},
    {"id": "R21", "checks": "rails are planar: a rail passes through no node it does not end at, and two rails cross only at a node they share.",
     "device": PLANAR,
     "note": "This is the rule that refuses a torus drawn on a plane: its wrap wires run back across the die through every node on the row. The cylinder generator is the planar drawing of the same torus.",
     "fail": {"device": CROSSING, "program": [rec("init", {"d0": "A"})],
              "why": "the rail A-B and the rail C-D cross at the origin, where there is no node: two RF nulls through the same point, no junction."},
     "pass": {"device": PLANAR, "program": [rec("init", {"d0": "A"})],
              "why": "the same four sites joined through a junction J, so the rails meet only where the graph says they do."}},
    {"id": "R22", "checks": "one transport cycle is one waveform: every ion that moves in a simd cycle has the same motion signature (class, and hop by hop the same path direction, spur direction or lab-frame axis); a site may opt out, never do something else.",
     "device": GRID,
     "fail": {"program": [rec("init", {"d0": "T0_0v", "d1": "T0_1h"}), rec("simd", "shuttle", [["d0", "T0_0v", "T0_1v", ["T0_0v.b", "T0_1v.a"]], ["d1", "T0_1h", "T1_1v", ["T0_1h.b", "T1_1v.a"]]])],
              "why": "d0 walks north through J0_1 while d1 walks east then north through J1_1: two motions, so two waveforms, in one cycle."},
     "pass": {"program": [rec("init", {"d0": "T0_0v", "d1": "T1_0v"}), rec("simd", "shuttle", [["d0", "T0_0v", "T0_1v", ["T0_0v.b", "T0_1v.a"]], ["d1", "T1_0v", "T1_1v", ["T1_0v.b", "T1_1v.a"]]])],
              "why": "both walk north through their own T-junction: one motion, one waveform."}},
]


# ------------------------------------------------------------------- building

def text_of(records: list[dict]) -> str:
    """The programme as the Write pane would show it: `p.method(args, key=value)`."""
    def lit(v: Any) -> str:
        if v is True:
            return "True"
        if v is False:
            return "False"
        if v is None:
            return "None"
        return json.dumps(v)
    lines = []
    for r in records:
        parts = [lit(a) for a in r["args"]] + [f"{k}={lit(v)}" for k, v in r["kwargs"].items()]
        lines.append(f'p.{r["method"]}({", ".join(parts)})')
    return "\n".join(lines)


def ir_text(prog: TSIR) -> str:
    out = []
    for ins in prog.instructions:
        d = {k: v for k, v in dataclasses.asdict(ins).items() if v not in (None, (), [], {}, "", 0.0) or k in ("id", "type")}
        d.pop("meta", None)
        out.append(json.dumps(d, default=list))
    return "\n".join(out)


def _state(summary: dict, rule: str) -> str:
    if rule in summary.get("failed", ()):
        return "failed"
    if rule in (summary.get("partial") or {}):
        return "partial"
    if rule in (summary.get("skipped") or {}):
        return "skipped"
    if rule in summary.get("passed", ()):
        return "passed"
    return "unknown"


def judge(m: Machine, prog: TSIR, model_name: str, focus: str | None = None,
          check_metrics: bool = False) -> dict:
    """The verifier's word on one programme, plus the focus rule's own sentences."""
    model = deck_model() if model_name == "deck" else corrected_model()
    rep = verify(prog, m.arch, model, check_metrics=check_metrics)
    s = rep.rules.summary()
    res = rep.result
    anomalous = {ion: round(sum(v for k, v in comps.items() if k == "anomalous"), 3)
                 for ion, comps in res.per_ion_quanta.items()}
    out = {
        "model": model.name, "failed": list(s["failed"]),
        "skipped": dict(s.get("skipped") or {}), "partial": {k: str(v) for k, v in (s.get("partial") or {}).items()},
        "messages": [{"rule": v.rule, "message": v.message} for v in rep.rules.violations[:8]],
        "cost": res.total_cost, "steps": res.total_steps, "us": res.total_us,
        "peak_quanta": res.peak_quanta, "gate_error": res.gate_error_sum, "anomalous": anomalous,
    }
    if focus:
        only = verify(prog, m.arch, model, only_rules=[focus], check_metrics=(focus == "R9"))
        msgs = [v.message for v in only.rules.violations[:4]]
        state = "failed" if msgs else _state(s, focus)
        why = (s.get("skipped") or {}).get(focus) or (s.get("partial") or {}).get(focus) or ""
        out["focus"] = {"rule": focus, "state": state, "why": str(why), "messages": msgs}
    return out


def build_example(ex: dict, m: Machine, model_name: str, page_path, *, kicker: str,
                  headline: str, lede: str, focus: str | None = None,
                  check_metrics: bool = False) -> dict:
    """Render one example's page and judge it.  Returns what the site page shows."""
    if ex.get("ir"):
        prog = ex["ir"](m)
        text, ir = ir_text(prog), True
    else:
        prog = m.program("example", provenance="off").apply_calls(ex["program"]).build()
        text, ir = text_of(ex["program"]), False
    model = deck_model() if model_name == "deck" else corrected_model()
    m.render(prog, page_path, model=model, kicker=kicker, headline=headline, lede=lede, open_pane="R")
    return {"text": text, "ir": ir, "why": ex.get("why", ""), "expect": ex.get("expect"),
            "tag": ex.get("tag"), "verdict": judge(m, prog, model_name, focus, check_metrics)}


def rule_meta(rule: str) -> dict:
    return {"statement": RULE_STATEMENTS.get(rule, ""), "sources": RULE_SOURCES.get(rule, "")}
