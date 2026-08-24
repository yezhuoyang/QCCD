"""Layer 3 -- the rules R1..R18 as machine-checkable invariants.  PLAN §5.

Every rule traces to a source; the statements below are quoted from
`Knowledge/notes/constraints.yaml` (`python Knowledge/kg/query.py rules`).  Each check
runs against one replay cycle, so a violation names the instruction that caused it.

What each rule can and cannot see
---------------------------------
R1-R8, R11-R14      per-cycle state checks; complete
R9                  program-level: claimed metrics vs replayed ones
R10                 needs symbolic circuit tracking; reported as `skipped`, not `passed`
R15                 the interference term needs a phase model the corpus does not supply
                    for these primitives; additive composition is used and reported as an
                    upper bound, so R15 is `partial`
R16, R17            model-level assertions: does the model in use make gate error a
                    function of n-bar, and does it accrue heating with elapsed time?

A rule that cannot be checked reports `skipped` and never `passed`.  A verifier that
prints a green tick for a check it did not run is worse than no verifier.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Callable, Mapping, Sequence

from ..arch import Architecture, Segment
from ..ir.tsir import Instruction, iter_operands, iter_pairs

__all__ = [
    "Violation",
    "ResolvedMove",
    "CycleView",
    "action_label",
    "path_actions",
    "architecture_violations",
    "concurrency_violations",
    "RuleReport",
    "CYCLE_RULES",
    "check_cycle",
    "rule_statements",
]


RULE_SOURCES: Mapping[str, str] = {
    "R1": "deck_v3, 2004.04706, 2511.15910",
    "R2": "deck_v3, 2510.23519",
    "R3": "deck_v3, 2510.23519",
    "R4": "deck_v3, 2504.17886",
    "R4d": "deck_v3, 2403.00756, 2305.03828",
    "R4b": "2504.17886",
    "R5": "deck_v3",
    "R6": "deck_v3, 2504.17886",
    "R6b": "2504.17886",
    "R7": "deck_v3, 2511.15910",
    "R7b": "2504.17886",
    "R7c": "2510.23519, 2606.06455",
    "R8": "deck_v3",
    "R9": "local",
    "R10": "local, zac",
    "R11": "2511.15910",
    "R12": "2511.15910, 2004.04706",
    "R13": "2511.15910, 2004.04706",
    "R14": "2510.23519",
    "R15": "2605.25118",
    "R16": "2510.23519, 2605.25118",
    "R17": "2605.25118",
    "R18": "quant-ph/0702175, 2305.03828, 1210.3655",
}

RULE_STATEMENTS: Mapping[str, str] = {
    "R1": "occupancy(site) <= site.capacity at every instant",
    "R2": "at most one ion occupies any junction at any instant",
    "R3": "at most segment.capacity ions occupy any shuttling segment",
    "R4": "at most max_simd_classes_per_cycle classes active; a class fixes "
          "(type, direction); participation is variadic",
    "R4d": "a cycle must be drivable by the declared control channels: one channel "
           "carries one waveform, and opting out needs a per-site switch",
    "R4b": "intra-trap and inter-trap transport never overlap in time",
    "R5": "no two ions exchange positions along one segment in a single step",
    "R6": "gate / measure / cool only where the zone type has the capability",
    "R6b": "a 2Q gate acts only on ions co-located in the same gate zone",
    "R7": "a 2Q gate requires both ions' n-bar <= ms_gate.max_quanta",
    "R7b": "per-gate-zone thermal duty-cycle budget, not just instantaneous occupancy",
    "R7c": "cooling is mandatory under broadcast wiring",
    "R8": "the ion->site map is a bijection over time outside explicit load/unload",
    "R9": "claimed steps/cost/duration/quanta equal the replayed values",
    "R10": "the compiled program implements the input circuit",
    "R11": "shuttling is unidirectional; a trap connects to <= 2 shuttling paths",
    "R12": "intra-trap parallelism = 1; inter-trap parallelism unconstrained",
    "R13": "2Q gate time degrades sharply above ~15 ions per trap",
    "R14": "an ion must be at a trap edge to split; getting there costs a 3-CX swap",
    "R15": "quanta compose with an interference term, not additively",
    "R16": "2Q gate error is a function of accumulated n-bar at gate time",
    "R17": "anomalous heating accrues with elapsed time whether or not an ion moves",
    "R18": "a node is a junction only if three or more trap axes meet at it",
}


@dataclass(frozen=True, slots=True)
class Violation:
    rule: str
    instr_id: int
    message: str
    severity: str = "error"

    def __str__(self) -> str:
        return f"[{self.rule}] instruction {self.instr_id}: {self.message}"


@dataclass(frozen=True, slots=True)
class ResolvedMove:
    """One participant's movement, with the segment it uses resolved."""

    ion: str
    src: str
    dst: str
    seg: Segment
    entails: tuple[str, ...] = ()


@dataclass
class CycleView:
    """Everything a per-cycle rule needs about one instruction."""

    arch: Architecture
    instr: Instruction
    moves: tuple[ResolvedMove, ...]
    pos_before: Mapping[str, str]
    pos_after: Mapping[str, str]
    occ_before: Mapping[str, int]
    occ_after: Mapping[str, int]
    quanta: Mapping[str, float]
    duration_us: float = 0.0
    config: Mapping[str, object] = field(default_factory=dict)
    #: node id -> how many ions pass THROUGH it this cycle without stopping there.
    #: A multi-segment move enters every node on its path; only the last is a resting
    #: place, and the others are transits.
    transits: Mapping[str, int] = field(default_factory=dict)

    def gate_sites(self) -> tuple[str, ...]:
        """Where the gates of this instruction actually happen.

        Derived from the *replayed* positions of the operands, not from the optional
        `sites` annotation.  A check driven by what a program claims about itself can be
        switched off by omitting the claim; the replay is the thing that establishes
        where the ions are, so R6, R6b and R13 read it.  `sites`, when present, is a
        claim that R6b cross-checks.
        """
        out: list[str] = []
        for operands in iter_operands(self.instr):
            for ion in operands:
                site = self.pos_before.get(ion)
                if site is not None and site not in out:
                    out.append(site)
        for site in self.instr.sites:
            if site not in out:
                out.append(site)
        return tuple(out)

    def gate_budget(self) -> float:
        """R7's n-bar budget: the policy override if one was supplied, else the
        architecture's `ms_gate.max_quanta`, else unbounded."""
        override = self.config.get("max_gate_quanta")
        if override is not None:
            return float(override)  # type: ignore[arg-type]
        try:
            spec = self.arch.primitives.scalar("ms_gate")
        except KeyError:
            return math.inf
        return float(spec.get("max_quanta", math.inf))


@dataclass
class RuleReport:
    """Outcome per rule across a whole replay."""

    violations: list[Violation] = field(default_factory=list)
    checked: set[str] = field(default_factory=set)
    skipped: dict[str, str] = field(default_factory=dict)
    partial: dict[str, str] = field(default_factory=dict)
    notes: dict[str, object] = field(default_factory=dict)

    def add(self, v: Violation | None) -> None:
        if v is not None:
            self.violations.append(v)

    def extend(self, vs: Sequence[Violation]) -> None:
        self.violations.extend(vs)

    def by_rule(self) -> dict[str, int]:
        c: Counter[str] = Counter(v.rule for v in self.violations)
        return dict(sorted(c.items()))

    def failed(self) -> set[str]:
        return {v.rule for v in self.violations if v.severity == "error"}

    def passed(self) -> list[str]:
        bad = self.failed()
        return sorted(
            r for r in self.checked if r not in bad and r not in self.skipped
        )

    def ok(self) -> bool:
        return not self.failed()

    def summary(self) -> dict:
        return {
            "passed": self.passed(),
            "failed": sorted(self.failed()),
            "partial": dict(self.partial),
            "skipped": dict(self.skipped),
            "violations": len(self.violations),
            "by_rule": self.by_rule(),
        }


# --------------------------------------------------------------------------- rules


def r1_capacity(v: CycleView) -> list[Violation]:
    """R1 -- `occupancy(site) <= site.capacity` **at every instant**.

    Two instants matter, and only one of them used to be checked:

    * where an ion **comes to rest**, which is the post-cycle occupancy; and
    * where an ion **passes through**. A move whose path crosses an occupied trap has to
      fit inside it on the way past, and if it does not, that trap is a *roadblock* --
      the central problem the whole field is organised around ("a filled trap can block
      the movement of another ion"; arXiv:2511.15910 calls the resulting serialization
      the reason grids lose on non-local codes).

    The transit test is deliberately conservative. The IR does not order events inside a
    cycle, so a resident that leaves during the cycle cannot be assumed to leave *first*;
    the check therefore uses `max(before, after)` occupancy and admits no ordering it was
    not told about. Junctions are excluded because they hold no ions by construction and
    are governed by R2, which is what a junction is for.
    """
    out = []
    dev = v.arch.device
    for node_id, occ in v.occ_after.items():
        cap = dev.nodes[node_id].capacity
        if occ > cap:
            out.append(
                Violation(
                    "R1",
                    v.instr.id,
                    f"site {node_id} holds {occ} ions but capacity is {cap}; "
                    f"a rebalance must be scheduled explicitly",
                )
            )
    for node_id, n in (v.transits or {}).items():
        node = dev.nodes.get(node_id)
        if node is None or node.kind != "site":
            continue  # a junction holds no ions; R2 governs it
        resident = max(v.occ_before.get(node_id, 0), v.occ_after.get(node_id, 0))
        if resident + n > node.capacity:
            out.append(
                Violation(
                    "R1",
                    v.instr.id,
                    f"ROADBLOCK: {n} ion(s) transit site {node_id}, which already holds "
                    f"{resident} of its {node.capacity}; there is no room to pass "
                    f"through and the route must go around or wait",
                )
            )
    return out


def r2_junction_exclusive(v: CycleView) -> list[Violation]:
    out = []
    dev = v.arch.device
    for node_id, occ in v.occ_after.items():
        if occ > 1 and dev.degree(node_id) >= 3:
            out.append(
                Violation(
                    "R2",
                    v.instr.id,
                    f"junction {node_id} (degree {dev.degree(node_id)}) holds {occ} ions",
                )
            )
    # two ions entering the same junction within one cycle is equally illegal
    entering: Counter[str] = Counter(
        m.dst for m in v.moves if dev.degree(m.dst) >= 3
    )
    for node_id, n in entering.items():
        if n > 1:
            out.append(
                Violation(
                    "R2", v.instr.id, f"{n} ions cross junction {node_id} in one cycle"
                )
            )
    return out


def r3_segment_capacity(v: CycleView) -> list[Violation]:
    use: Counter[str] = Counter(m.seg.id for m in v.moves)
    return [
        Violation(
            "R3",
            v.instr.id,
            f"segment {sid} carries {n} ions but capacity is "
            f"{v.arch.device.segments[sid].capacity}",
        )
        for sid, n in use.items()
        if n > v.arch.device.segments[sid].capacity
    ]


def r4_simd_classes(v: CycleView) -> list[Violation]:
    if v.instr.type != "simd":
        return []
    out = []
    limit = v.arch.max_simd_classes()
    # one instruction is one cycle and carries one class, so the count is 1 by
    # construction; what is worth checking is that the class was actually declared
    if v.instr.cls is not None and v.arch.simd_classes:
        if v.instr.cls not in v.arch.simd_classes:
            out.append(
                Violation(
                    "R4",
                    v.instr.id,
                    f"movement class {v.instr.cls!r} is not declared by the architecture",
                )
            )
    if 1 > limit:
        out.append(
            Violation("R4", v.instr.id, f"cycle uses 1 class but the limit is {limit}")
        )
    return out


def action_label(loop: str, delta: int) -> str:
    """The action signature a channel has to produce: `"L0:+1"`.

    Naming the path as well as the direction is what makes the verifier's message and
    the control panel's message byte-identical -- and it turns "asked to do 2 different
    things (-1 and 1)" into "(L0:+1 and L0:-1)", which says which conveyor.
    """
    return f"{loop}:{delta:+d}"


def path_actions(v: CycleView) -> dict[str, dict[str, int]]:
    """`{path: {source site: signed hops ALONG that path}}` for this cycle.

    The only frame in which the action is well defined: a named path is one conveyor, so
    "forward one slot" is the same instruction to every site on it, however the path
    bends in the lab frame.  Moves off a named path are ABSENT -- no conveyor, no
    direction, no verdict -- which is why `r4_drivable` is silent about a dock, and why
    silence there is not a pass.
    """
    dev = v.arch.device
    by_path: dict[str, dict[str, int]] = {}
    index: dict[str, dict[str, int]] = {}      # hoisted: was rebuilt per move
    for m in v.moves:
        if dev.nodes[m.src].kind != "site":
            continue
        loop = m.seg.loop
        if loop is None or loop not in dev.loops:
            continue                      # off a named path: no conveyor, no verdict
        seq = dev.loops[loop].nodes
        idx = index.get(loop)
        if idx is None:
            idx = index[loop] = {n: i for i, n in enumerate(seq)}
        if m.src not in idx or m.dst not in idx:
            continue
        k = len(seq)
        # how far and which way ALONG THE PATH, not in the lab frame: a conveyor drives
        # a whole loop forward with one waveform even where the loop bends
        d = (idx[m.dst] - idx[m.src]) % k
        by_path.setdefault(loop, {})[m.src] = d if d <= k // 2 else d - k
    return by_path


def r4_drivable(v: CycleView) -> list[Violation]:
    """R4, derived from the wiring rather than declared.

    A movement class is not a label the program picks; it is whatever one setting of the
    control channels can produce. So the real check is: given which electrodes share a
    channel, can these sites be asked to do these things at the same instant?

    Scoped to one path at a time, deliberately. Within a single named path the verdict is
    layout-independent and absolute: a path is one conveyor, so two ions on it that share
    a channel cannot be driven in opposite directions, however the electrodes are shaped.
    ACROSS paths there is no verdict, because whether one waveform advances a top-rail
    and a bottom-rail ion alike depends on whether their electrode layouts are mirrored
    or translated -- a field solve over one device's geometry, which is exactly what
    PLAN §2 puts out of scope. Silence there is the honest answer, not a pass.

    An architecture that has not declared its channel map is not judged at all.
    """
    if v.instr.type != "simd" or not v.moves:
        return []
    plane = v.arch.control_plane
    if not plane.declared or not plane.groups:
        return []
    out: list[Violation] = []
    for loop, deltas in sorted(path_actions(v).items()):
        labels = {s: action_label(loop, d) for s, d in deltas.items()}
        ok, problems = plane.drivable(labels)
        if not ok:
            out += [Violation("R4", v.instr.id, f"on path {loop!r}: {p}")
                    for p in problems]
    return out


def r4b_intra_inter(v: CycleView) -> list[Violation]:
    out = []
    if v.instr.type == "simd":
        if v.instr.mode not in ("intra", "inter"):
            out.append(
                Violation("R4b", v.instr.id, f"transport cycle has mode {v.instr.mode!r}")
            )
        if v.instr.pairs or v.instr.gate:
            out.append(
                Violation(
                    "R4b",
                    v.instr.id,
                    "a cycle carries both transport and gates: intra- and inter-trap "
                    "control pathways are distinct and cannot overlap",
                )
            )
    return out


def r5_no_exchange(v: CycleView) -> list[Violation]:
    seen: dict[str, set[tuple[str, str]]] = defaultdict(set)
    out = []
    for m in v.moves:
        seen[m.seg.id].add((m.src, m.dst))
    for sid, pairs in seen.items():
        for src, dst in pairs:
            if (dst, src) in pairs:
                out.append(
                    Violation(
                        "R5",
                        v.instr.id,
                        f"ions exchange positions across segment {sid} in one step",
                    )
                )
                break
    return out


def r6_capability(v: CycleView) -> list[Violation]:
    out = []
    arch = v.arch
    if v.instr.type == "gate":
        for site in v.gate_sites():
            if not arch.can(site, "gate"):
                zt = arch.device.nodes[site].zone_type
                out.append(
                    Violation(
                        "R6", v.instr.id, f"gate at {site} whose zone type {zt!r} has gate=false"
                    )
                )
    elif v.instr.type in ("measure", "reset"):
        for ion in v.instr.ions:
            site = v.pos_before.get(ion)
            if site is None:
                out.append(Violation("R6", v.instr.id, f"{v.instr.type} on unplaced ion {ion}"))
            elif not arch.can(site, "spam"):
                out.append(
                    Violation("R6", v.instr.id, f"{v.instr.type} at {site}: no spam capability")
                )
    elif v.instr.type == "cool":
        ions = v.instr.ions or tuple(v.pos_before)
        for ion in ions:
            site = v.pos_before.get(ion)
            if site is None:
                out.append(Violation("R6", v.instr.id, f"cool on unplaced ion {ion}"))
            elif not arch.can(site, "cool"):
                out.append(Violation("R6", v.instr.id, f"cool at {site}: no cool capability"))
    return out


def r6b_colocation(v: CycleView) -> list[Violation]:
    if v.instr.type != "gate":
        return []
    out = []
    declared = set(v.instr.sites)
    for a, b in iter_pairs(v.instr):
        sa, sb = v.pos_before.get(a), v.pos_before.get(b)
        if sa is None or sb is None:
            out.append(Violation("R6b", v.instr.id, f"gate on unplaced ion(s) {a}, {b}"))
            continue
        if sa != sb:
            out.append(
                Violation("R6b", v.instr.id, f"2Q gate on {a}@{sa} and {b}@{sb}: not co-located")
            )
        elif declared and sa not in declared:
            out.append(
                Violation(
                    "R6b",
                    v.instr.id,
                    f"gate on {a},{b} happens at {sa} but the instruction declares {sorted(declared)}",
                )
            )
    return out


def r7_thermal(v: CycleView) -> list[Violation]:
    if v.instr.type != "gate":
        return []
    budget = v.gate_budget()
    out = []
    for a, b in iter_pairs(v.instr):
        for ion in (a, b):
            n = v.quanta.get(ion, 0.0)
            if n > budget:
                out.append(
                    Violation(
                        "R7",
                        v.instr.id,
                        f"ion {ion} enters a 2Q gate at n-bar={n:.3f} > {budget}; "
                        f"a cooling operation must precede it",
                    )
                )
    return out


def r8_bijection(v: CycleView) -> list[Violation]:
    out = []
    if set(v.pos_before) != set(v.pos_after):
        lost = sorted(set(v.pos_before) - set(v.pos_after))[:5]
        made = sorted(set(v.pos_after) - set(v.pos_before))[:5]
        out.append(
            Violation("R8", v.instr.id, f"ion set changed outside load/unload (lost {lost}, new {made})")
        )
    once: Counter[str] = Counter(p.ion for p in v.instr.participants)
    for ion, n in once.items():
        if n > 1:
            out.append(
                Violation(
                    "R8",
                    v.instr.id,
                    f"ion {ion} is a participant {n} times in one cycle; the ion->site "
                    f"map would not be a function",
                )
            )
    # the ion's declared origin is the FIRST resolved hop's source, not the last:
    # a participant whose `via` crosses several segments produces several ResolvedMoves,
    # and the intermediate nodes are not where the ion started
    origin: dict[str, str] = {}
    for m in v.moves:
        origin.setdefault(m.ion, m.src)
    for ion, before in v.pos_before.items():
        after = v.pos_after.get(ion)
        if after == before:
            continue
        if ion not in origin:
            out.append(
                Violation("R8", v.instr.id, f"ion {ion} moved {before} -> {after} with no participant")
            )
        elif origin[ion] != before:
            out.append(
                Violation("R8", v.instr.id, f"ion {ion} declared from {origin[ion]} but was at {before}")
            )
    return out


def r11_unidirectional(v: CycleView) -> list[Violation]:
    """Shuttling is unidirectional per path.

    Within one cycle every ion moving along a given loop must move the same way.  The
    complementary half of R11 -- that a trap connecting to more than two shuttling paths
    needs a junction -- is a property of the architecture rather than of a cycle, and is
    checked once per program by `architecture_violations`.
    """
    out = []
    per_loop: dict[str, set[int]] = defaultdict(set)
    for m in v.moves:
        loop = m.seg.loop
        if loop is None:
            continue
        seq = v.arch.device.loops[loop].nodes
        idx = {n: i for i, n in enumerate(seq)}
        k = len(seq)
        per_loop[loop].add((idx[m.dst] - idx[m.src]) % k)
    for loop, deltas in per_loop.items():
        if len(deltas) > 1:
            out.append(
                Violation(
                    "R11",
                    v.instr.id,
                    f"ions move along loop {loop} by {sorted(deltas)} in one cycle; "
                    f"shuttling is unidirectional per path",
                )
            )
    return out


def concurrency_violations(arch: Architecture, prog) -> list[Violation]:
    """R4 and R4b across *time*, once a program carries explicit `t0`/`t1`.

    One instruction is one class, so the per-cycle check is trivially satisfied and says
    nothing about a machine that can drive several classes at once. The real constraint
    lives between instructions that OVERLAP IN TIME: at any instant, at most
    `max_simd_classes_per_cycle` distinct movement classes may be active (R4), and
    intra- and inter-trap transport may never be active together (R4b).

    Without explicit times a program is a strict sequence and nothing overlaps, so this
    check is a no-op -- which is the honest answer for a program that has not been
    scheduled, not a pass.
    """
    out: list[Violation] = []
    timed = [i for i in prog.instructions
             if i.t0 is not None and i.t1 is not None and i.t1 > i.t0]
    if not timed:
        return out
    limit = arch.max_simd_classes()

    # sweep the interval endpoints; between two consecutive endpoints the active set is
    # constant, so checking there checks every instant
    points = sorted({i.t0 for i in timed} | {i.t1 for i in timed})
    for a, b in zip(points, points[1:]):
        mid = (a + b) / 2
        live = [i for i in timed if i.t0 <= mid < i.t1]
        transport = [i for i in live if i.type == "simd"]
        classes = {i.cls for i in transport if i.cls}
        if len(classes) > limit:
            out.append(Violation(
                "R4", transport[0].id,
                f"{len(classes)} movement classes active together at t={mid:.3f} us "
                f"({sorted(classes)}) but the machine drives {limit}"))
        modes = {i.mode for i in transport if i.mode}
        if len(modes) > 1:
            out.append(Violation(
                "R4b", transport[0].id,
                f"intra- and inter-trap transport overlap at t={mid:.3f} us; they use "
                f"distinct control pathways"))
    return out


def architecture_violations(arch: Architecture) -> list[Violation]:
    """Program-level checks that need the architecture but no state.

    R11's structural half: every node the graph makes a junction has to be *priceable*
    at that degree, or the architecture cannot charge for what it built.  R18 then reads
    the price off the degree.
    """
    out: list[Violation] = []
    dev = arch.device
    try:
        curve = arch.primitives.degree_curve("junction_cross")
    except KeyError:
        curve = None
    for nid in dev.junction_nodes:
        deg = dev.degree(nid)
        if curve is None or curve.get(deg) is None:
            out.append(
                Violation(
                    "R11",
                    -1,
                    f"node {nid} has degree {deg} but the architecture prices no "
                    f"junction_cross at degree {deg}",
                )
            )
    return out


def r12_intra_parallelism(v: CycleView) -> list[Violation]:
    if v.instr.type != "gate":
        return []
    per_site: Counter[str] = Counter()
    for operands in iter_operands(v.instr):
        # intra-trap parallelism is 1 for a gate of ANY arity: one trap, one beam
        site = v.pos_before.get(operands[0])
        if site is not None:
            per_site[site] += 1
    return [
        Violation("R12", v.instr.id, f"{n} gates in trap {site} in one cycle; intra-trap parallelism is 1")
        for site, n in per_site.items()
        if n > 1
    ]


def r13_chain_length(v: CycleView, limit: int = 15) -> list[Violation]:
    if v.instr.type != "gate":
        return []
    out = []
    for site in set(v.gate_sites()):
        occ = v.occ_before.get(site, 0)
        if occ > limit:
            out.append(
                Violation(
                    "R13",
                    v.instr.id,
                    f"2Q gate in a chain of {occ} ions at {site}; gate time degrades "
                    f"sharply above ~{limit}",
                )
            )
    return out


def r14_split_at_edge(v: CycleView) -> list[Violation]:
    """An ion must be at the end of its chain to split out of it.

    With capacity <= 2 every ion is at an edge, so the hidden 3-CX swap is free; the
    check exists so that raising capacity in a sweep does not silently stop paying it.
    """
    out = []
    for m in v.moves:
        if "split" not in m.entails:
            continue
        occ = v.occ_before.get(m.src, 0)
        if occ > 2 and not v.instr.meta.get("gate_swaps"):
            out.append(
                Violation(
                    "R14",
                    v.instr.id,
                    f"ion {m.ion} splits from a chain of {occ} at {m.src} with no "
                    f"gate_swap accounted; R14 charges 3 CX to reach the edge",
                )
            )
    return out


#: Rules evaluated once per replayed cycle.
CYCLE_RULES: Mapping[str, Callable[[CycleView], list[Violation]]] = {
    "R1": r1_capacity,
    "R2": r2_junction_exclusive,
    "R3": r3_segment_capacity,
    "R4": r4_simd_classes,
    "R4d": r4_drivable,
    "R4b": r4b_intra_inter,
    "R5": r5_no_exchange,
    "R6": r6_capability,
    "R6b": r6b_colocation,
    "R7": r7_thermal,
    "R8": r8_bijection,
    "R11": r11_unidirectional,
    "R12": r12_intra_parallelism,
    "R13": r13_chain_length,
    "R14": r14_split_at_edge,
}


def check_cycle(view: CycleView, only: Sequence[str] | None = None) -> list[Violation]:
    out: list[Violation] = []
    for name, fn in CYCLE_RULES.items():
        if only is not None and name not in only:
            continue
        out.extend(fn(view))
    return out


def _rule_order(rule: str) -> tuple[int, str]:
    """R1 < R4 < R4b < R4d < R7 < R7b ... -- numeric first, then the letter suffix."""
    digits = "".join(c for c in rule[1:] if c.isdigit())
    return (int(digits or 0), rule)


def rule_statements() -> dict[str, dict[str, str]]:
    return {
        r: {"statement": RULE_STATEMENTS[r], "sources": RULE_SOURCES.get(r, "")}
        for r in sorted(RULE_STATEMENTS, key=_rule_order)
    }
