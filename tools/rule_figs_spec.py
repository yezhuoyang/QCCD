"""The figure table: one entry per term and per rule.

Kept apart from `make_rule_figs.py` because it is data, not machinery -- and because it is
the thing a reader who disagrees with a figure should be able to edit without touching a
renderer.

Every `Case` declares the rule set it expects to fire, and `Case.run` **refuses to emit
the figure** if the verifier disagrees. So this file cannot drift away from
`qccd/verify/rules.py`: if a rule's behaviour changes, the build breaks rather than
shipping a picture that lies.
"""

from __future__ import annotations

from qccd.arch import load
from qccd.cost import corrected_model
from qccd.ir import TSIR, Instruction

from make_rule_figs import (ROOT, Case, Fig, chain, chart, grid, init, lab_ring,
                            lab_ring_both_ways, lab_ring_broadcast, move, prog,
                            ring, rotate, two_traps, wired_ring)

# --------------------------------------------------------------- shared devices

R = ring()                       # 8 rail slots + 2 dock spurs; S0/S4 are degree 3
R0 = ring(verticals=0)           # the same ring with no spurs: every node degree 2
RQ = ring(zone="quiet")          # rail slots that cannot gate; ancillas that can
RW = wired_ring()                # ...with its control channels declared
RL = lab_ring()                  # ...tiled on the CHIP AXES, one group per direction
RLB = lab_ring_broadcast()       # lab frame + the shipped all-sites broadcast map
RL2 = lab_ring_both_ways()       # lab frame, a +1 map asked to turn -1 as well
SC = load(ROOT / 'arch' / 'stationary_chain.arch.json')  # steerable_raman, no switch
W1 = two_traps(1)                # one segment, capacity 1
W2 = two_traps(2)                # one segment, capacity 2
WW = two_traps(1, "wide")        # capacity-4 traps, no loop, no junction
C = chain(5)
CR = chain(5, zone="register")   # capacity 32: a stationary register
G = grid()                       # 3x3 lattice: degree-4 junctions, NO loops

GATE = dict(type="gate", gate="MS")


def _gate(pairs, id=1, **kw):
    return Instruction(id=id, pairs=tuple(pairs), **GATE, **kw)


def _hot(place, n, id=0):
    return Instruction(type="init", id=id, placement=dict(place),
                       quanta={k: n for k in place})


def _degrees(arch) -> str:
    """The degree histogram and junction set, READ OFF the expanded graph.

    Typed out by hand this would be the one hand-written claim in a tool whose whole
    point is that it makes none.
    """
    import collections
    d = arch.device
    hist = collections.Counter(d.degree(n) for n in d.nodes)
    return (f"degrees {dict(sorted(hist.items()))}   "
            f"junction_nodes = ({', '.join(d.junction_nodes) or ''})")


def gate_nbar(rep) -> str:
    """What the gates in this programme actually ran at.

    R7's subject is a temperature, and a stage cannot draw one: two panels differing only
    in n-bar are the same picture. So the figure prints the replayed number instead of
    asserting it.
    """
    r = rep.result
    return (f"replayed: hottest ion entering a 2Q gate = "
            f"{r.max_gate_quanta_seen:.3f} quanta, budget 1.0; "
            f"summed gate error {r.gate_error_sum:.5f}")


def cycles_then_q(rep) -> str:
    """How many machine cycles one instruction actually became, plus the heating.

    The stage draws one instruction, so a caption claiming "three steps" would be a claim
    the picture does not support. The replay's own cycle count does support it.
    """
    n = sum(1 for c in rep.result.cycles if c.type == "simd")
    return f"the replay charged this ONE instruction as {n} machine cycles.  " + q(rep)


def q(rep, ion="d0"):
    """The replayed quanta of one ion, itemized -- a number, not an assertion."""
    per = rep.result.per_ion_quanta.get(ion, {})
    parts = [f"{k} {v:.2f}" for k, v in per.items() if v]
    return f"replayed n-bar for {ion}: " + (", ".join(parts) or "0") + \
           f"   total {sum(per.values()):.2f} quanta"


# ==================================================================== TERMINOLOGY

def terminology() -> list[Fig]:
    return [
        Fig(key="t01_node_and_segment", static=True,
            head="Term - node, segment, ion",
            sub="Ions REST on nodes and TRAVEL along segments. A node is a trap slot with "
                "a capacity and a zone type; a segment is the shuttling path between two "
                "of them, with a capacity of its own. Every id below is the id the "
                "verifier's messages use.",
            cases=[Case(R, prog(init({"d0": "S1", "d1": "S5", "a0": "A0"}),
                                move(("d0", "S1", "S2"))),
                        "one ion moves S1 -> S2", (),
                        note="nodes S0..S7 on the rail, A0/A4 on the spurs; "
                             "segments E0..E7 on the rail, V0/V4 the spurs")]),

        Fig(key="t02_degree_and_junction", static=True,
            head="Term - degree, and what makes a junction (R18)",
            sub="Nothing declares 'this is a junction'. Degree is counted from the "
                "expanded graph, and R18 reads the price off it: a node is a junction iff "
                "three or more trap axes meet there. Attaching a spur to a rail node is "
                "what MAKES it one - gold squares are degree >= 3.",
            cases=[Case(R0, prog(init({"d0": "S1"}), move(("d0", "S1", "S2"))),
                        "0 spurs: every node degree 2", (),
                        note=lambda rep: _degrees(R0) + " -- four corners, but a bend is "
                                         "not a junction: continuous RF rails, no barrier"),
                   Case(R, prog(init({"d0": "S1"}), move(("d0", "S1", "S2"))),
                        "2 spurs: S0 and S4 become degree 3", (),
                        highlight=("S0", "S4"),
                        note=lambda rep: _degrees(R) + " -- charged junction_cross at "
                                         "degree 3: 100 us and 3.0 quanta, against 5 us "
                                         "/ 0.1 quanta for a plain shuttle")]),

        Fig(key="t03_corner", static=True,
            head="Term - corner (a property of the LOOP, not of a node)",
            sub="A corner is where the loop's in- and out-direction differ, found by "
                "walking the loop's cyclic node order. corner_endpoints[seg] == 2 marks a "
                "segment that contains a whole turn - in a height-2 ring exactly the two "
                "end-caps, which is what the deck charged 3 primitive hops for and R18 "
                "reduces to 1.",
            cases=[Case(R0, prog(init({"d0": "S1"}), move(("d0", "S1", "S2"))),
                        "the 4 corners of an 8-slot ring", (),
                        highlight=tuple(R0.device.corners("L0")),
                        note="corner segments (both endpoints are corners): " +
                             ", ".join(s for s, n in R0.device.corner_endpoints.items()
                                       if n == 2))]),

        Fig(key="t04_cycle_and_subcycle",
            head="Term - one cycle is one machine step",
            sub="A loop_shift template with |delta| > 1 is ONE instruction and MORE THAN "
                "ONE cycle: the replay decomposes it into |delta| unit sub-cycles, each "
                "with its own depth, duration and full rule check. The stage animates the "
                "one instruction; the band below counts what the replay actually charged.",
            cases=[Case(R, prog(init({"d0": "S1", "d1": "S3", "d2": "S6"}),
                                Instruction(type="simd", id=1, cls="rotate_cw",
                                            mode="inter",
                                            template={"kind": "loop_shift", "loop": "L0",
                                                      "delta": 3})),
                        "rotate_cw L0 +3", (), model="corrected", note=cycles_then_q)],
            ms=260),

        Fig(key="t05_class_and_broadcast",
            head="Term - a SIMD class, and variadic participation",
            sub="A class fixes (type, direction) and is broadcast: one waveform on one "
                "channel drives every site wired to it. Participation is VARIADIC - each "
                "site may join or be held out by its switch. So 'every ion moves' and "
                "'one ion moves' are the same single class, and both are one cycle.",
            cases=[Case(R, prog(init({f"d{i}": f"S{i}" for i in range(8)}),
                                Instruction(type="simd", id=1, cls="rotate_cw",
                                            mode="inter",
                                            template={"kind": "loop_shift", "loop": "L0",
                                                      "delta": 1})),
                        "one class, every site joins", (),
                        note="rigid rotation: 8 ions, 1 instruction, 1 class"),
                   Case(R, prog(init({f"d{i}": f"S{i}" for i in range(8)}),
                                move(("d1", "S1", "S2"), cls="rotate_cw")),
                        "the same class, one site joins", (),
                        note="the other 7 switches stay open; the waveform is identical")]),

        Fig(key="t06_entails_split_merge",
            head="Term - `entails`: what a movement class costs beyond the hop",
            sub="A rigid rotation is a conveyor: the whole chain rides a moving "
                "potential and nothing splits. A dock LIFTS one ion out of the rail's "
                "potential and inserts it into a separate trap, so it costs a split at "
                "the source and a merge at the destination. That is a property of the "
                "CLASS, not of occupancy, so the architecture declares it.",
            cases=[Case(R, prog(init({"d0": "S1"}), move(("d0", "S1", "S2"),
                                                         cls="rotate_cw")),
                        "rotate_cw  entails ()", (), model="corrected", note=q),
                   Case(R, prog(init({"d0": "S0"}), move(("d0", "S0", "A0"), cls="dock")),
                        "dock  entails (split, merge)", (), model="corrected", note=q)]),

        Fig(key="t07_loops", static=True,
            head="Term - loop: the domain of exactly one movement template",
            sub="A closed loop is what lets rigid rotation exist: 'shift every ion on L0 "
                "by k' is ONE class where an odd-even sort needs many. An open loop is a "
                "linear register and refuses a rigid shift. A grid has no loops at all - "
                "which is also why R4d and R11 have nothing to judge there.",
            cases=[Case(R0, prog(init({"d0": "S1"}), move(("d0", "S1", "S2"))),
                        "ring: L0, closed", (), labels=False,
                        note="Device.shift_map('L0', +1) IS the rotation template"),
                   Case(C, prog(init({"d0": "C1"}), move(("d0", "C1", "C2"))),
                        "chain: P0, open", (), labels=False,
                        note="a linear register; asking it for a rigid shift raises"),
                   Case(G, prog(init({"d0": "T0_0h"}),
                                move(("d0", "T0_0h", "T0_0v",
                                      ("T0_0h.a", "T0_0v.a")))),
                        "grid: no loops", (), labels=False,
                        note="288 segments belong to no named path on grid9x9; R4d is "
                             "silent and says 'not judged', which is not a pass")],
            width=250),
    ]


# ========================================================================= RULES

def rules() -> list[Fig]:
    return [
        # ------------------------------------------------------------------ R1
        Fig(key="R1_capacity",
            head="R1 - occupancy(site) <= capacity, where ions come to REST",
            sub="A trap well holds a bounded ion chain. Capacity is declared per zone "
                "type and is a sweep axis, not a constant: 2510.23519 finds 2 optimal "
                "for surface codes, 2511.15910 finds 8 for qLDPC on a ring.",
            cases=[Case(R, prog(init({"d0": "S1", "d1": "S2"}), move(("d0", "S1", "S2"))),
                        "LEGAL  2 ions into a capacity-2 trap", (), highlight=("S2",)),
                   Case(R, prog(init({"d0": "S1", "d1": "S2", "d2": "S2"}),
                                move(("d0", "S1", "S2"))),
                        "VIOLATION  a third arrives", ("R1",), highlight=("S2",))]),

        Fig(key="R1b_roadblock",
            head="R1 - ...and where a route passes THROUGH",
            sub="The clause the whole field is organised around: 'a filled trap can block "
                "the movement of another ion'. A multi-segment hop has to FIT inside every "
                "trap on its path, and if it does not, that trap is a roadblock and the "
                "route must go around or wait (2511.15910).",
            cases=[Case(R, prog(init({"x": "S1", "r": "S2"}),
                                move(("x", "S1", "S3", ("E1", "E2")))),
                        "LEGAL  S2 has one slot spare", (), highlight=("S2",)),
                   Case(R, prog(init({"x": "S1", "r1": "S2", "r2": "S2"}),
                                move(("x", "S1", "S3", ("E1", "E2")))),
                        "VIOLATION  S2 is full", ("R1",), highlight=("S2",),
                        note="no other rule in R1..R18 inspects transit occupancy at all")],
            ms=240),

        # ------------------------------------------------------------------ R2
        Fig(key="R2_junction_exclusive",
            head="R2 - a junction is a router: one ion through it per cycle",
            sub="Shuttling through a junction fights an RF barrier from the unbalanced "
                "fields of the electrodes across it (quant-ph/0702175). It is a "
                "single-ion manoeuvre: the electrode configuration realises ONE "
                "in-direction-to-out-direction map per cycle, so two ions have no common "
                "controlled trajectory.",
            cases=[Case(R, prog(init({"d0": "S1"}), move(("d0", "S1", "S0"))),
                        "LEGAL  one ion crosses S0", (), highlight=("S0",)),
                   Case(R, prog(init({"d0": "S1", "d1": "A0"}),
                                move(("d0", "S1", "S0"), ("d1", "A0", "S0"))),
                        "VIOLATION  two arrive from two segments", ("R2",),
                        highlight=("S0",),
                        note="R3 is silent: E0 and V0 each carry exactly 1 of their 1")]),

        # ------------------------------------------------------------------ R3
        Fig(key="R3_segment_capacity",
            head="R3 - a segment is a link: <= segment.capacity ions on it per cycle",
            sub="Not the same statement as R2. R2 bounds a NODE, R3 bounds an EDGE - and "
                "the case only R3 reaches is the co-located, co-directional pile-up: two "
                "ions legally sharing a capacity-2 trap, both asked down the same segment "
                "in the same direction.",
            cases=[Case(R, prog(init({"d0": "S1", "d1": "S1"}), move(("d0", "S1", "S2"))),
                        "LEGAL  one of the two leaves", (), highlight=("E1",)),
                   Case(R, prog(init({"d0": "S1", "d1": "S1"}),
                                move(("d0", "S1", "S2"), ("d1", "S1", "S2"))),
                        "VIOLATION  both take E1", ("R3",), highlight=("E1",),
                        note="no swap, no junction, no overflow: R1, R2, R5 and R11 all "
                             "pass")]),

        # ------------------------------------------------------------------ R4
        Fig(key="R4_declared_class",
            head="R4 - a cycle carries a DECLARED movement class",
            sub="A class is not a label the program picks; it is whatever one setting of "
                "the control channels can produce, and the architecture enumerates them. "
                "A class fixes (type, direction) - so 'left' and 'right' are two classes, "
                "and WISE drives one per cycle.",
            cases=[Case(R, prog(init({"d0": "S1"}), move(("d0", "S1", "S2"),
                                                         cls="rotate_cw")),
                        "LEGAL  rotate_cw is declared", (),
                        note="declared: " + ", ".join(R.simd_classes)),
                   Case(R, prog(init({"d0": "S1"}), move(("d0", "S1", "S2"),
                                                         cls="teleport")),
                        "VIOLATION  teleport is not", ("R4",))]),

        Fig(key="R4t_classes_over_time", static=True,
            head="R4 - ...and at most max_simd_classes_per_cycle active at any INSTANT",
            sub="One instruction is one class, so the per-cycle count is 1 by "
                "construction and says nothing. The real constraint lives between "
                "instructions that OVERLAP IN TIME, which is why the check sweeps the "
                "interval endpoints of a scheduled program. Without t0/t1 a program is a "
                "strict sequence, nothing overlaps, and the check is a no-op - the honest "
                "answer for a program nobody has scheduled.",
            cases=[Case(R, TSIR(name="seq", arch_spec="i", instructions=[
                            init({"a": "S1", "b": "S5"}),
                            move(("a", "S1", "S2"), cls="rotate_cw", id=1,
                                 t0=0.0, t1=10.0),
                            move(("b", "S5", "S6"), cls="rotate_cw", id=2,
                                 t0=5.0, t1=15.0)]),
                        "LEGAL  overlapping, but ONE class", ()),
                   Case(R, TSIR(name="par", arch_spec="i", instructions=[
                            init({"a": "S1", "b": "S5"}),
                            move(("a", "S1", "S2"), cls="rotate_cw", id=1,
                                 t0=0.0, t1=10.0),
                            move(("b", "S5", "S6"), cls="nudge", id=2,
                                 t0=5.0, t1=15.0)]),
                        "VIOLATION  two classes at t=7.5 us", ("R4",),
                        note="max_simd_classes_per_cycle = 1 (WISE). The deck gives WISE "
                             "2 and C2LR 4 - q_simd_class_budget is still open")]),

        # ----------------------------------------------------------------- R4b
        Fig(key="R4b_intra_inter",
            head="R4b - transport and gates never share a cycle",
            sub="Two distinct control pathways: DC transport electrodes move ions, lasers "
                "gate them. The rule also keeps intra-trap and inter-trap transport apart "
                "in time, because those are driven differently too.",
            cases=[Case(R, prog(init({"d0": "S1", "a": "A0", "b": "A0"}),
                                move(("d0", "S1", "S2")), _gate([("a", "b")], id=2)),
                        "LEGAL  move, then gate", ()),
                   Case(R, prog(init({"d0": "S1", "a": "A0", "b": "A0"}),
                                move(("d0", "S1", "S2"), pairs=(("a", "b"),))),
                        "VIOLATION  one cycle carries both", ("R4b",))]),

        # ----------------------------------------------------------------- R4d
        Fig(key="R4d_drivable",
            head="R4d - can the declared wiring actually PRODUCE this cycle?",
            sub="Derived from the electrode map rather than declared. Within one named "
                "path the verdict is layout-independent and absolute: a path is one "
                "conveyor, so two ions on it that share a channel cannot be driven in "
                "opposite directions, however the electrodes are shaped. Here 3 linear_h "
                "channels drive all 10 sites - H2's {a,b,c} tiling at eight slots.",
            cases=[Case(RW, prog(init({"a": "S1", "b": "S5"}),
                                 move(("a", "S1", "S2"), ("b", "S5", "S6"))),
                        "LEGAL  both follow the same waveform", ()),
                   Case(RW, prog(init({"a": "S1", "b": "S5"}),
                                 move(("a", "S1", "S2"), ("b", "S5", "S4"))),
                        "VIOLATION  one waveform, two demands", ("R4", "R11"),
                        note="Two things to notice. R11 fires too - on a WIRED device the "
                             "two overlap, and R11 is the only check when no channels are "
                             "declared. And the label reads [R4], not [R4d]: the "
                             "drivability check emits under R4's name while being "
                             "registered as R4d, so R4d lands in `passed` on this very "
                             "cycle (docs/notes.md 5.1)")]),

        Fig(key="R4d2_switch_per_site",
            head="R4d - ...and the clause that is R4d's alone: the per-site switch",
            sub="The SAME one-ion move, on the same eight slots, with the same three "
                "broadcast channels. The only difference is whether a site can be held "
                "out of the waveform its channel carries. Without that switch a channel "
                "is all-or-nothing, so moving one ion means moving ten - and the WISE "
                "trade (deck p.19: two switch elements per electrode) is exactly what "
                "buys the difference.",
            cases=[Case(wired_ring(switch_per_site=True),
                        prog(init({"a": "S1"}), move(("a", "S1", "S2"))),
                        "LEGAL  switch_per_site: true", ()),
                   Case(wired_ring(switch_per_site=False),
                        prog(init({"a": "S1"}), move(("a", "S1", "S2"))),
                        "VIOLATION  switch_per_site: false", ("R4",),
                        note="R11 is silent - one ion, one direction. This is the one "
                             "cycle in the whole set that R4d rejects and every other "
                             "rule accepts")]),

        # ----------------------------------------------------------------- R4c
        Fig(key="R4c_broadcast_claim",
            head="R4c - a broadcast is a CLAIM the instruction makes, and the device "
                 "answers it",
            sub="The identical rigid rotation, on two eight-slot rings that differ in "
                "one declared field: control.channels.frame. On a path-frame tiling the "
                "conveyor follows the trap axis, so one waveform advances the whole loop "
                "and broadcast='one' is true - that is H2 (2305.03828). On a lab-frame "
                "tiling the electrodes are fixed to the chip axes, so the same rotation "
                "is four waveforms, one per direction the path turns into, and "
                "broadcast='one' is refuted by the device. The instruction never states "
                "the number four; the device geometry does.",
            cases=[Case(RW, prog(init({f"i{i}": f"S{i}" for i in range(8)}),
                                 rotate(broadcast="one")),
                        "LEGAL  frame='path': one conveyor, one waveform", ()),
                   Case(RL, prog(init({f"i{i}": f"S{i}" for i in range(8)}),
                                 rotate(broadcast="per_direction")),
                        "LEGAL  frame='lab': one waveform per direction", ()),
                   Case(RL, prog(init({f"i{i}": f"S{i}" for i in range(8)}),
                                 rotate(broadcast="one")),
                        "VIOLATION  frame='lab', but the program claims one drive",
                        ("R4c",),
                        note="R4d is silent - every channel is uniform, the wiring CAN "
                             "produce this cycle. R19 is silent - four groups for four "
                             "directions. The only thing wrong is what the program said "
                             "about itself")]),

        Fig(key="R4c2_optical_broadcast",
            head="R4c - the same claim on the optical side: 'Broadcast laser'",
            sub="A gate instruction carries many pairs because a broadcast machine "
                "drives them together - but nothing in a TSIR file has ever said whether "
                "one beam or N steered beams lit them. stationary_chain is the one "
                "shipped device that declares optical.addressing='steerable_raman' and "
                "per_zone_switch=false, and it is where the claim fails: a steered beam "
                "reaches one zone at a time, and a zone with no switch cannot be left "
                "dark.",
            cases=[Case(SC, prog(Instruction(type="init", id=0,
                                             placement={"a": "C0", "b": "C0",
                                                        "c": "C1", "d": "C1"},
                                             quanta={k: 0.0 for k in "abcd"}),
                                 _gate([("a", "b")], id=1),
                                 Instruction(type="cool", id=2, broadcast=True)),
                        "LEGAL  no broadcast claim: not judged, and silence is not a pass",
                        ()),
                   Case(SC, prog(Instruction(type="init", id=0,
                                             placement={"a": "C0", "b": "C0",
                                                        "c": "C1", "d": "C1"},
                                             quanta={k: 0.0 for k in "abcd"}),
                                 _gate([("a", "b")], id=1, broadcast="one"),
                                 Instruction(type="cool", id=2, broadcast=True)),
                        "VIOLATION  one beam, but C1 cannot opt out", ("R4c",),
                        note="the broadcast COOL in the same programme is legal: cooling "
                             "is not steered, and this device's own primitives.cool "
                             "declares broadcastable=true, scope='global' (R7c)")]),


        # ----------------------------------------------------------------- R19
        Fig(key="R19_electrode_frame", static=True,
            head="R19 - a lab-frame tiling needs one channel group per direction it turns",
            sub="An ARCHITECTURE rule, like R11(b): it has no program variable, and it "
                "fires with no program at all. Both panels are the same eight slots "
                "declaring frame='lab'. The left one cuts its channel map from "
                "Device.shift_directions - one explicit group per axis direction, sizes "
                "3/3/1/1 here and 71/71/1/1 on ring144_24v, constant in array size. The "
                "right one uses the map every shipped device actually has, "
                "grouping='broadcast', which puts all sites on every channel: one "
                "independent group against four directions.",
            cases=[Case(RL, prog(init({f"i{i}": f"S{i}" for i in range(8)}),
                                 rotate(broadcast="per_direction")),
                        "LEGAL  4 groups, one per direction", (),
                        note="4 channels for 8 slots, and 4 for 144 on the shipped ring - "
                             "the number is how many axis directions the path turns in, "
                             "not the array size, so "
                             "WISE's O(1) DAC claim survives the lab frame intact"),
                   Case(RLB, prog(init({f"i{i}": f"S{i}" for i in range(8)}),
                                  rotate(broadcast="per_direction")),
                        "VIOLATION  grouping='broadcast'", ("R19", "R4"),
                        note="R4d fires too, and the pair is the point: R19 is an "
                             "ARCHITECTURE verdict - this device cannot rotate, no "
                             "program needed - while R4d is a CYCLE verdict exhibiting a "
                             "rotation that proves it. (The label reads R4, not R4d: "
                             "docs/notes.md 5.1.) Every shipped device is wired this way, "
                             "which is why R19 defaults to frame='path' and reports a "
                             "SKIP REASON rather than a pass")]),

        Fig(key="R19b_both_directions", static=True,
            head="R19 - and turning the loop BOTH ways needs the common refinement",
            sub="The consequence that is not obvious. A +1 shift and a -1 shift do not "
                "partition the sites the same way: they are offset by one at the corners, "
                "so the site that goes +y under +1 is not the site that goes -y under -1. "
                "A four-group map cut for +1 therefore asks one of its channels for two "
                "waveforms under -1. The fix is the common refinement of the two "
                "partitions - 6 groups here and on ring144_24v (70/70/1/1/1/1), still "
                "constant in array size.",
            cases=[Case(RL, prog(init({f"i{i}": f"S{i}" for i in range(8)}),
                                 rotate(broadcast="per_direction")),
                        "LEGAL  the device declares +1 only", (),
                        note="lab_ring declares rotate_cw and nudge, both +1. Four groups "
                             "are enough for one direction of travel"),
                   Case(RL2, prog(init({f"i{i}": f"S{i}" for i in range(8)}),
                                  rotate(broadcast="per_direction")),
                        "VIOLATION  the same map, rotate_ccw restored", ("R19",),
                        note="R19 judges the DECLARED classes, not the program: this "
                             "fires with no -1 instruction anywhere in the program")]),

        # ------------------------------------------------------------------ R5
        Fig(key="R5_no_exchange",
            head="R5 - two ions may not pass through each other in a 1D channel",
            sub="On every shipped device segment.capacity is 1, so an exchange is two "
                "ions on one segment and R3 rejects it first - which is why R5 looks "
                "redundant. Widen the segment to 2 and R3 is satisfied: R5 is then the "
                "only thing left standing between the schedule and two ions swapping "
                "along a channel that is order-preserving.",
            cases=[Case(W2, prog(init({"x": "T0", "y": "T1"}), move(("x", "T0", "T1"))),
                        "LEGAL  one ion crosses (E0 capacity 2)", ()),
                   Case(W2, prog(init({"x": "T0", "y": "T1"}),
                                 move(("x", "T0", "T1"), ("y", "T1", "T0"))),
                        "VIOLATION  they exchange", ("R5",),
                        note="at capacity 1 the same cycle fires R3 as well - R5's whole "
                             "unique territory is capacity >= 2")]),

        # ------------------------------------------------------------------ R6
        Fig(key="R6_zone_capability",
            head="R6 - gate / measure / cool only where the zone type allows",
            sub="Gate zones are DISCRETE POINTS, set by where laser power can be "
                "delivered. Motion and gate capability are decoupled: an ion may traverse "
                "a site it cannot be gated in. The check reads the REPLAYED site, not the "
                "instruction's own `sites` annotation - a check driven by what a program "
                "claims about itself can be switched off by omitting the claim.",
            cases=[Case(RQ, prog(init({"a": "A0", "b": "A0"}), _gate([("a", "b")])),
                        "LEGAL  gate at A0 (gate: true)", (), highlight=("A0",)),
                   Case(RQ, prog(init({"a": "S1", "b": "S1"}), _gate([("a", "b")])),
                        "VIOLATION  gate at S1 (zone 'quiet')", ("R6",),
                        highlight=("S1",),
                        note="the pair IS co-located, so R6b passes: the two rules are "
                             "independent")]),

        Fig(key="R6b_colocation",
            head="R6b - a 2Q gate acts only on ions in the SAME trap",
            sub="An entangling gate needs a shared motional mode; two ions in two wells "
                "have none. Independent of R6 in both directions - on four of the nine "
                "shipped devices every node is gate-capable, so R6 cannot fire at all "
                "while R6b fires freely.",
            cases=[Case(R, prog(init({"a": "A0", "b": "A0"}), _gate([("a", "b")])),
                        "LEGAL  both at A0", (), highlight=("A0",)),
                   Case(R, prog(init({"a": "A0", "b": "A4"}), _gate([("a", "b")])),
                        "VIOLATION  A0 and A4", ("R6b",), highlight=("A0", "A4"),
                        note="both sites CAN gate, so R6 passes")]),

        # ------------------------------------------------------------------ R7
        Fig(key="R7_thermal_budget", static=True,
            head="R7 - a 2Q gate needs both ions at n-bar <= ms_gate.max_quanta",
            sub="The budget is 1.0 quanta, and that number is not arbitrary: the heating "
                "term of eps = eps0 + 2.0e-3*n-bar equals the intrinsic floor 1.84e-3 at "
                "n-bar = 0.92. R7 refuses a gate as soon as heating becomes the majority "
                "of its error - which is also the edge of the domain the linear fit is "
                "valid on.",
            cases=[Case(R, prog(_hot({"a": "A0", "b": "A0"}, 5.0),
                                Instruction(type="cool", id=1, broadcast=True),
                                _gate([("a", "b")], id=2)),
                        "LEGAL  cooled to 0 first", (), model="corrected",
                        highlight=("A0",),
                        note=lambda rep: "one global Doppler cool costs 300 us and "
                                         "zeroes every ion in the trap.  " + gate_nbar(rep)),
                   Case(R, prog(_hot({"a": "A0", "b": "A0"}, 5.0),
                                Instruction(type="cool", id=1, ions=("a",)),
                                _gate([("a", "b")], id=2)),
                        "VIOLATION  b is still at 5 quanta", ("R7",), model="corrected",
                        highlight=("A0",),
                        note=lambda rep: "a cool DOES exist in this program, so R7c "
                                         "passes: R7 and R7c are not the same check.  "
                                         + gate_nbar(rep))]),

        Fig(key="R7c_cooling_mandatory", static=True,
            head="R7c - cooling is mandatory, not a refinement",
            sub="The shipped 24-ancilla schedule contains ZERO cooling operations against "
                "~1747 quanta per data ion per round. Without cooling a broadcast-wired "
                "machine cannot pass a logical error rate of 1e-4 at all (2510.23519, "
                "2606.06455). R7c fires on a program with gates and no cooling ANYWHERE - "
                "even when every individual gate is cold enough for R7.",
            cases=[Case(R, prog(_hot({"a": "A0", "b": "A0"}, 0.0),
                                Instruction(type="cool", id=1, broadcast=True),
                                _gate([("a", "b")], id=2)),
                        "LEGAL  a cool is scheduled", (), model="corrected"),
                   Case(R, prog(_hot({"a": "A0", "b": "A0"}, 0.0),
                                _gate([("a", "b")], id=1)),
                        "VIOLATION  no cool anywhere", ("R7c",), model="corrected",
                        note=lambda rep: "the gate is COLD and R7 passes - R7c is a "
                                         "property of the PROGRAM, not of any one gate.  "
                                         + gate_nbar(rep))]),

        # ------------------------------------------------------------------ R8
        Fig(key="R8_bijection",
            head="R8 - the ion -> site map stays a function",
            sub="Bookkeeping integrity rather than hardware: an ion is in one place. "
                "The check also catches an ion that moved with no participant declaring "
                "it, and a participant whose declared origin is not where the replay says "
                "the ion was.",
            cases=[Case(W2, prog(init({"d": "T0"}), move(("d", "T0", "T1"))),
                        "LEGAL  one participant per ion", ()),
                   Case(W2, prog(init({"d": "T0"}),
                                 move(("d", "T0", "T1"), ("d", "T0", "T1"))),
                        "VIOLATION  d listed twice in one cycle", ("R8",),
                        note="drawn on a stage with no loop and no junction so that R11 "
                             "and R2 cannot mask the finding")]),

        # ------------------------------------------------------------------ R9
        Fig(key="R9_claims_vs_replay", static=True,
            head="R9 - what the program CLAIMS equals what the replay COMPUTES",
            sub="A TSIR program carries total_cost, total_steps, t0/t1 and per-instruction "
                "quanta. Those are claims. The replay recomputes all of them from the "
                "architecture and the cost model, and R9 falsifies the ones that "
                "disagree - checked at three granularities, because a total can agree by "
                "cancellation. This is the difference between a verifier and a "
                "pretty-printer.",
            cases=[Case(R, TSIR(name="honest", arch_spec="i",
                                instructions=[init({"d0": "S1"}),
                                              move(("d0", "S1", "S2"))],
                                metrics={"total_cost": 1.0, "total_steps": 1}),
                        "LEGAL  claims total_cost 1.0", (), metrics=True),
                   Case(R, TSIR(name="wrong", arch_spec="i",
                                instructions=[init({"d0": "S1"}),
                                              move(("d0", "S1", "S2"))],
                                metrics={"total_cost": 999.0, "total_steps": 1}),
                        "VIOLATION  claims 999.0", ("R9",), metrics=True)]),

        # ----------------------------------------------------------------- R10
        Fig(key="R10_implements_the_circuit", static=True,
            head="R10 - the program implements the INPUT CIRCUIT",
            sub="Both panels are perfectly legal hardware: same device, same trap, one "
                "MS gate, every one of R1..R18 passing. They entangle different pairs. "
                "No hardware rule can tell them apart - that is exactly the gap R10 "
                "fills, and why it needs symbolic permutation and Pauli-frame tracking "
                "against the QASM DAG rather than a replay. It is discharged out of tree "
                "by a checker proved in Lean 4 (Compiler/).",
            cases=[Case(CR, prog(init({"q0": "C0", "q1": "C0", "q2": "C0"}),
                                 _gate([("q0", "q1")])),
                        "MS(q0, q1)  - all rules pass", ()),
                   Case(CR, prog(init({"q0": "C0", "q1": "C0", "q2": "C0"}),
                                 _gate([("q0", "q2")])),
                        "MS(q0, q2)  - all rules pass", (),
                        note="if the circuit asked for MS(q0,q1), this one is WRONG and "
                             "the verifier cannot see it")]),

        # ----------------------------------------------------------------- R11
        Fig(key="R11_unidirectional",
            head="R11 - shuttling is unidirectional per path per cycle",
            sub="A ring turns one way at a time. This is the only per-cycle check that "
                "compares ions' displacements TO EACH OTHER - R4d compares sites to the "
                "channel map, and is silent on a device that declares no channels "
                "(`chain`, `stationary_chain`), under per-site direct wiring, and when "
                "two ions leave the SAME site in opposite directions.",
            cases=[Case(R, prog(init({"a": "S1", "b": "S5"}),
                                move(("a", "S1", "S2"), ("b", "S5", "S6"))),
                        "LEGAL  both +1 along L0", (),
                        note="the arrows point OPPOSITE WAYS in the lab frame and both "
                             "are +1 along the path - a loop is one conveyor, and the "
                             "bottom row runs right-to-left in slot order"),
                   Case(R, prog(init({"a": "S1", "b": "S5"}),
                                move(("a", "S1", "S2"), ("b", "S5", "S4"))),
                        "VIOLATION  +1 and -1 in one cycle", ("R11",),
                        note="both arrows point the SAME way in the lab frame; along L0 "
                             "they are +1 and -1. This ring declares no control.channels, "
                             "so R4d reports 'not judged' - which is not a pass")]),

        # ----------------------------------------------------------------- R12
        Fig(key="R12_intra_trap_parallelism", static=True,
            head="R12 - one gate per trap per cycle (the OPTICAL constraint)",
            sub="The mirror of R4 on the other control pathway. Light reaches a trap "
                "through fibre and a waveguide beneath it, and the 1-to-2^n splitter "
                "geometry sets the maximum fan-out; one trap gets one beam. Inter-trap "
                "parallelism is unconstrained - two traps may gate at once.",
            cases=[Case(CR, prog(init({"a": "C0", "b": "C0"}), _gate([("a", "b")])),
                        "LEGAL  one pair in C0", (), highlight=("C0",)),
                   Case(CR, prog(init({"a": "C0", "b": "C0", "c": "C0", "e": "C0"}),
                                 _gate([("a", "b"), ("c", "e")])),
                        "VIOLATION  two pairs in C0", ("R12",), highlight=("C0",),
                        note="capacity 32 here, so R1 is silent and R12 is isolated")]),

        # ----------------------------------------------------------------- R13
        Fig(key="R13_chain_length", static=True,
            head="R13 - 2Q gate time degrades sharply above ~15 ions in a trap",
            sub="More ions means a denser normal-mode spectrum, which forces a smaller "
                "detuning to keep the gate mode spectrally isolated, which forces a "
                "longer gate. Distinct from R1: capacity is how many ions the POTENTIAL "
                "holds, R13 is how many the GATE can address. On a capacity-32 register "
                "R1 permits 32 and R13 refuses at 16.",
            cases=[Case(CR, prog(init({f"i{k}": "C0" for k in range(15)}),
                                 _gate([("i0", "i1")])),
                        "LEGAL  15 ions in the chain", (), highlight=("C0",)),
                   Case(CR, prog(init({f"i{k}": "C0" for k in range(16)}),
                                 _gate([("i0", "i1")])),
                        "VIOLATION  16 ions", ("R13",), highlight=("C0",),
                        note="on the other eight shipped devices no gate-capable site "
                             "exceeds capacity 4, so R13 can never fire there")]),

        # ----------------------------------------------------------------- R14
        Fig(key="R14_split_at_edge",
            head="R14 - an ion must be at a chain EDGE to split out of it",
            sub="Ions sit in one harmonic well; splitting reshapes the DC electrodes into "
                "a double well and separates the chain. Only the ions at the ends come "
                "off - reaching the edge from the middle costs an intra-trap swap, three "
                "CX. At capacity <= 2 every ion is already at an edge and the swap is "
                "free, so the rule exists to stop a capacity sweep silently ceasing to "
                "pay it.",
            cases=[Case(WW, prog(init({"d0": "T0", "d1": "T0"}),
                                 move(("d0", "T0", "T1"), cls="dock")),
                        "LEGAL  a chain of 2: both at an edge", (), model="corrected"),
                   Case(WW, prog(init({"d0": "T0", "d1": "T0", "d2": "T0"}),
                                 move(("d0", "T0", "T1"), cls="dock")),
                        "VIOLATION  a chain of 3, no gate_swap accounted", ("R14",),
                        model="corrected",
                        note="capacity 4 here; on a capacity-2 device this rule is "
                             "unreachable by construction")]),
    ]


# ================================================================ MODEL CONTRACTS
#
# R15, R16 and R17 do not constrain a program -- no schedule can violate them, so there
# is no illegal stage to draw.  They constrain the COST MODEL, and what a cost model has
# is a curve.  Every point below is computed by calling the shipped model.

def model_contracts() -> list[Fig]:
    arch = load(__import__("pathlib").Path(__file__).resolve().parents[1]
                / "arch" / "ring144_24v.arch.json")
    m = corrected_model()
    spec = arch.primitives.scalar("ms_gate")
    eps0 = 1.0 - float(spec["fidelity_at_n0"])
    budget = float(spec["max_quanta"])
    rate = m.anomalous_per_us(arch)          # quanta per microsecond

    def r16(w, h):
        xs = [i * 3.0 / 240 for i in range(241)]
        return chart(
            w, h,
            [("eps(n-bar) = eps0 + 2.0e-3 n-bar", "accent",
              [(x, m.gate_error(arch, x)) for x in xs]),
             ("eps0 = 1.84e-3  (H2 measured, 2305.03828)", "navy",
              [(x, eps0) for x in xs], True)],
            xlabel="n-bar at gate time  (motional quanta)",
            ylabel="2Q gate error",
            xlim=(0, 3), ylim=(0, m.gate_error(arch, 3.0)),
            marks=[(eps0 / 2.0e-3, "teal", " n-bar* = 0.92: heating = floor"),
                   (budget, "anc", " R7 budget = 1.0")],
            shade=(budget, 3.0, "R7 refuses here - and so the fit is never extrapolated"))

    def r17(w, h):
        ts = [i * 300.0 / 240 for i in range(241)]      # milliseconds
        return chart(
            w, h,
            [("anomalous n-bar = ndot * t,  ndot = 0.05 quanta/ms", "accent",
              [(t, rate * t * 1000.0) for t in ts]),
             ("R7 budget = 1.0 quanta", "anc", [(t, budget) for t in ts], True)],
            xlabel="elapsed time (ms) - the ion has not moved",
            ylabel="n-bar",
            xlim=(0, 300), ylim=(0, rate * 300_000),
            marks=[(budget / (rate * 1000.0), "teal", " budget spent at 20 ms"),
                   (267.0, "navy", " 267 ms: the shipped rotation")])

    def r15(w, h):
        import math
        n1, n2 = 1.0, 1.0
        th = [i * math.pi / 120 for i in range(121)]
        return chart(
            w, h,
            [("true: n1 + n2 + 2*sqrt(n1 n2) cos(theta)", "accent",
              [(t, n1 + n2 + 2 * math.sqrt(n1 * n2) * math.cos(t)) for t in th]),
             ("what Charge.then computes: n1 + n2", "navy",
              [(t, n1 + n2) for t in th], True)],
            xlabel="secular phase theta between the two transports (radians)",
            ylabel="n-bar after both",
            xlim=(0, math.pi), ylim=(0, 4.2),
            marks=[(math.pi, "teal", " theta = pi: the second transport CANCELS the "
                                        "first")])

    return [
        Fig(key="R16_gate_error_vs_nbar", head="R16 - gate error is a FUNCTION of n-bar",
            sub="Not a constant. A Molmer-Sorensen gate is nominally insensitive to n "
                "because its phase is geometric - a rigid translation of phase space "
                "encloses the same area wherever it starts. n-bar re-enters through loop "
                "non-closure, whose infidelity |eps|^2(2 n-bar + 1) is EXACTLY affine, "
                "and through Debye-Waller, which is linear only while n-bar << 1.",
            chart=r16, chart_note=[
                ("muted", "This is a contract on the COST MODEL, not on a program: no "
                          "schedule can violate it, and a model whose gate_error ignores "
                          "n-bar entirely is still reported as passing R16 today."),
                ("accent", "gate_error() has no clamp. It crosses 1.0 at n-bar = 499 and "
                           "returns 3.496 for the shipped schedule's 1747 quanta - a "
                           "number 3.5x larger than certain, being summed into an "
                           "objective.")]),

        Fig(key="R17_anomalous_heating", head="R17 - heating accrues with ELAPSED TIME",
            sub="ndot = S_E(omega) e^2 / 4 m hbar omega, whether or not the ion moves. "
                "The per-gate contribution is negligible (1.25e-3 quanta over a 25 us "
                "gate) which is why the replay charges it to the NEXT gate. Over a "
                "schedule it is not negligible at all.",
            chart=r17, chart_note=[
                ("accent", "A perfectly STATIONARY ion in the shipped 267 ms rotation "
                           "accrues 13.35 quanta - 13x R7's budget - before a single "
                           "shuttle, junction or split is charged."),
                ("muted", "Also a model contract: `corrected_model(include_anomalous="
                          "False)` sets the rate to zero and R17 is still reported as "
                          "passing.")]),

        Fig(key="R15_composition", head="R15 - quanta do NOT compose additively",
            sub="Transport is a coherent displacement, not thermal heating: the waveform "
                "is deterministic, so the residue has a definite amplitude AND a definite "
                "phase. Displacements compose as amplitudes, D(a1)D(a2) ~ D(a1+a2), which "
                "is where the interference term comes from. The presence of a cosine is "
                "itself the proof that the channel is coherent - thermal contributions "
                "are phase-random and would simply add.",
            chart=r15, chart_note=[
                ("loop", "Additive composition is an UPPER BOUND, attained only at "
                            "theta = 0. At theta = pi with equal amplitudes the second "
                            "transport exactly undoes the first - the mechanism behind "
                            "the measured 0.36 quanta round trip (2201.07358)."),
                ("muted", "It is not uniformly loose: additive is EXACT for the "
                          "`anomalous` component, which is incoherent diffusion. The "
                          "components are already tracked separately, so a correct R15 is "
                          "confined to Charge.then.")]),
    ]


def SPECS() -> list[Fig]:
    return terminology() + rules() + model_contracts()
