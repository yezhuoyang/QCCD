"""Junction corners on loops (reader report T30, Ke Sun): a bend is not a stop.

"The trapping sites only stay on the edge, not in the corners.  The corners should be
junctions only."  `ring(..., corners="junction")` and `dual_loop(..., corners="junction")`
put a bare junction where only the loop's two rails meet in each corner (`Device.is_bend`).
A loop's SLOTS are its nodes less its bends; one slot of rotation carries the ion beside a
corner round it in one cycle (`Device.slot_walk`), and no ion ever rests on a corner.

What is held here, on the three devices the spec names -- `ring(6, 3, 2)`, `ring(4, 3)`
(lesson A4's six traps) and `dual_loop(6)`, all with junction corners:

* hand-written rotations (both ways, several slots, ions beside a corner, a packed loop)
  replay with every ion on a trap after every cycle, along the walk round the corner;
* the corner move is priced as the two (or three) rails it crosses, the existing
  multi-segment pricing, and a template prices exactly what the same move written out
  with its `via` does;
* every rule of the legacy verifier and of the standard rule set holds -- R22 and R11 read
  the corner move as one step along the loop, like a straight one -- and the rules still
  refuse an unlike motion;
* an ion left on a corner, or placed on one, is refused by both verifiers;
* the Python builders (rotate, odd-even, the BB pipeline, the language's core form) and the
  listings count slots, and the workspace's `construct` writes junction corners for a new
  ring unless told otherwise.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.api import Machine  # noqa: E402
from qccd.cost.models import corrected_model, deck_model  # noqa: E402
from qccd.ir.tsir import TSIR, Instruction, Participant, loop_shift  # noqa: E402
from qccd.ruleset import HOLDS, check_programme  # noqa: E402
from qccd.verify.replay import ReplayError, bend_vias, conveyed_paths, replay  # noqa: E402
from qccd.verify.rules import (enters_bend, hop_label, loop_node_set, motion_signature,  # noqa: E402
                               motion_signatures)

_CACHE: dict = {}


def dev(key: str) -> Machine:
    """The three devices of the spec, each on the default template's physics and control."""
    if key not in _CACHE:
        _CACHE[key] = {
            "ring6x3v2": lambda: Machine.ring(6, 3, 2, name="ring6x3v2_j", corners="junction"),
            "ring4x3": lambda: Machine.ring(4, 3, name="ring4x3_j", corners="junction"),
            "dual6": lambda: Machine.dual_loop(6, name="dual6_j", corners="junction"),
        }[key]()
    return _CACHE[key]


#: the loop each device rotates
LOOP = {"ring6x3v2": "L0", "ring4x3": "L0", "dual6": "A"}


def rotation(m: Machine, loop: str, deltas, fill=None, extra=None) -> TSIR:
    """Ions on the given slot indices (all of them when `fill` is None), then the shifts."""
    slots = m.arch.device.loop_slots(loop)
    place = {f"d{i}": s for i, s in enumerate(slots) if fill is None or i in fill}
    place.update(extra or {})
    p = TSIR(name="rot", arch_spec="test")
    p.add(Instruction(type="init", id=p.next_id(), placement=place, quanta={k: 0.0 for k in place}))
    for d in deltas:
        p.add(Instruction(type="simd", id=p.next_id(), cls="rotate_cw" if d > 0 else "rotate_ccw",
                          mode="inter", template=loop_shift(loop, d)))
    return p


def views_of(prog: TSIR, m: Machine, model=None):
    seen: list = []
    res = replay(prog, m.arch, model or corrected_model(), on_cycle=seen.append)
    return res, seen


def beside_corners(m: Machine, loop: str) -> list[int]:
    """Slot indices of the slots on either side of a bend."""
    d = m.arch.device
    slots = d.loop_slots(loop)
    near = set(bend_vias(d, loop, 1)) | set(bend_vias(d, loop, -1))
    return [i for i, s in enumerate(slots) if s in near]


# ------------------------------------------------------------------ the devices


def test_the_corners_are_bends_and_the_slots_are_the_traps():
    for key, n_slots, n_bends in (("ring6x3v2", 10, 4), ("ring4x3", 6, 4), ("dual6", 8, 4)):
        d = dev(key).arch.device
        loop = LOOP[key]
        bends = [n for n in d.loops[loop].nodes if d.is_bend(n)]
        assert len(d.loop_slots(loop)) == n_slots and len(bends) == n_bends, key
        assert all(d.nodes[n].kind == "junction" and d.nodes[n].capacity == 0 for n in bends)
        assert all(d.nodes[s].kind == "site" for s in d.loop_slots(loop))
        assert not set(bends) & set(d.junction_nodes)          # two rails: no junction by R18
    # the inner loop of the dual loop has its own four corners
    d = dev("dual6").arch.device
    assert [n for n in d.loops["D"].nodes if d.is_bend(n)] == ["DJ1", "DJ2", "DJ3", "DJ0"]


# ------------------------------------------------------------------ rotations land on traps

PROGRAMMES = {
    "plus1": ([1], None),
    "plus3": ([3], None),
    "minus1": ([-1], None),
    "minus2": ([-2], None),
    "both_ways": ([1, -1, 5, -4], None),
    "beside_corners_plus": ([2], "corners"),
    "beside_corners_minus": ([-3], "corners"),
}


@pytest.mark.parametrize("key", ["ring6x3v2", "ring4x3", "dual6"])
@pytest.mark.parametrize("name", sorted(PROGRAMMES))
def test_a_rotation_lands_every_ion_on_a_trap_along_the_walk_round_the_corner(key, name):
    m = dev(key)
    loop = LOOP[key]
    d = m.arch.device
    deltas, fill = PROGRAMMES[name]
    if fill == "corners":
        fill = beside_corners(m, loop)
    prog = rotation(m, loop, deltas, fill)
    res, views = views_of(prog, m)
    assert res.rules.ok(), [str(v) for v in res.rules.violations]
    slots = d.loop_slots(loop)
    n_cycles = sum(abs(x) for x in deltas)
    assert len(views) == n_cycles
    turned = 0
    for v in views:
        assert all(d.nodes[n].kind == "site" for n in v.pos_after.values()), v.pos_after
        step = int(v.instr.template["delta"] > 0) * 2 - 1
        for ion, src in v.pos_before.items():
            path = [src] + [mv.dst for mv in v.moves if mv.ion == ion]
            # one slot along the loop, through whatever bends lie between
            assert tuple(path) == d.slot_walk(loop, src, step), (ion, path)
            assert v.pos_after[ion] == slots[(slots.index(src) + step) % len(slots)]
            turned += len(path) > 2
    assert turned > 0, "the programme never took an ion round a corner"
    # and where every ion ends is the rigid rotation of where it started
    start = prog.instructions[0].placement
    net = sum(deltas)
    assert res.final_positions == {i: slots[(slots.index(s) + net) % len(slots)] for i, s in start.items()}


def test_the_inner_and_outer_loops_of_the_dual_loop_both_turn():
    m = dev("dual6")
    d = m.arch.device
    place = {f"a{i}": s for i, s in enumerate(d.loop_slots("A"))}
    place.update({f"d{i}": s for i, s in enumerate(d.loop_slots("D"))})
    p = TSIR(name="both", arch_spec="test")
    p.add(Instruction(type="init", id=p.next_id(), placement=place, quanta={k: 0.0 for k in place}))
    for loop, delta in (("D", 2), ("A", -3), ("D", -1)):
        p.add(Instruction(type="simd", id=p.next_id(), cls="rotate_cw" if delta > 0 else "rotate_ccw",
                          mode="inter", template=loop_shift(loop, delta)))
    res, views = views_of(p, m)
    assert res.rules.ok(), [str(v) for v in res.rules.violations]
    assert all(d.nodes[n].kind == "site" for v in views for n in v.pos_after.values())
    rep = check_programme("standard", p, m.arch, corrected_model())
    assert rep.ok, rep.failed()


# ------------------------------------------------------------------ the price of a corner


def test_a_corner_move_is_priced_as_the_rails_it_crosses():
    """The existing multi-segment pricing: each rail charged as it is crossed, the corner a
    bend (degree 2, no junction crossing), composed in sequence."""
    m = dev("ring4x3")
    d = m.arch.device
    # ring(4, 3): S0 S1 J1 S2 J2 S3 S4 J3 S5 J0 -- S1's next slot is S2, round J1
    assert d.loops["L0"].nodes == ("S0", "S1", "J1", "S2", "J2", "S3", "S4", "J3", "S5", "J0")
    for model in (corrected_model(), deck_model()):
        one = rotation(m, "L0", [1], fill=[1])                 # d1 on S1 only
        res = replay(one, m.arch, model)
        e1, e2 = d.segment_between("S1", "J1"), d.segment_between("J1", "S2")
        want = model.move(m.arch, e1, "S1", "J1").then(model.move(m.arch, e2, "J1", "S2"))
        assert (res.total_cost, res.total_steps, res.total_us) == (want.cost, want.depth, want.us)
        straight = replay(rotation(m, "L0", [1], fill=[0]), m.arch, model)   # S0 -> S1
        s = model.move(m.arch, d.segment_between("S0", "S1"), "S0", "S1")
        assert (straight.total_cost, straight.total_steps, straight.total_us) == (s.cost, s.depth, s.us)
        assert res.total_cost == 2 * straight.total_cost         # two rails, no junction
    q = corrected_model()
    shuttle = q._point(m.arch, "shuttle_segment")
    res = replay(rotation(m, "L0", [1], fill=[1]), m.arch, q)
    assert res.total_us == pytest.approx(2 * shuttle.us)
    assert res.quanta_components["shuttle"] == pytest.approx(2 * shuttle.quanta)
    assert res.quanta_components["junction"] == 0.0 and not res.junction_transits


def test_the_dual_loop_cap_is_three_rails_with_the_turn_on_the_middle_one():
    m = dev("dual6")
    d = m.arch.device
    walk = d.slot_walk("A", "AT4", 1)
    assert walk == ("AT4", "AJ1", "AJ2", "AB4")
    segs = [d.segment_between(a, b) for a, b in zip(walk, walk[1:])]
    assert [d.corner_endpoints[s.id] for s in segs] == [1, 2, 1]
    prog = rotation(m, "A", [1], fill=[d.loop_slots("A").index("AT4")])
    assert replay(prog, m.arch, deck_model()).total_cost == 1 + 3 + 1     # the deck's corner_hops
    assert replay(prog, m.arch, corrected_model()).total_cost == 3       # a bend is one shuttle


def test_a_template_prices_what_the_same_moves_written_out_price():
    """The template's participants are the walk round the corner, nothing else: written out
    as an explicit cycle with each `via`, the replay gives the same figures and verdicts."""
    for key in ("ring6x3v2", "ring4x3", "dual6"):
        m = dev(key)
        loop = LOOP[key]
        d = m.arch.device
        tpl = rotation(m, loop, [1, -1, -1], fill=beside_corners(m, loop) + [0])
        pos = dict(tpl.instructions[0].placement)
        out = TSIR(name="written", arch_spec="test")
        out.add(tpl.instructions[0])
        for ins in tpl.instructions[1:]:
            step = 1 if ins.template["delta"] > 0 else -1
            vias = bend_vias(d, loop, step)
            sh = d.shift_map(loop, step)
            parts = tuple(Participant(i, s, sh[s], vias.get(s, ())) for i, s in sorted(pos.items()))
            out.add(Instruction(type="simd", id=out.next_id(), cls=ins.cls, mode="inter", participants=parts))
            pos = {i: sh[s] for i, s in pos.items()}
        for model in (corrected_model(), deck_model()):
            a, b = replay(tpl, m.arch, model), replay(out, m.arch, model)
            assert (a.total_cost, a.total_steps, a.total_us) == (b.total_cost, b.total_steps, b.total_us), key
            assert a.quanta_components == pytest.approx(b.quanta_components)
            assert a.final_positions == b.final_positions
            assert a.rules.summary() == b.rules.summary()
        ra = check_programme("standard", tpl, m.arch, corrected_model())
        rb = check_programme("standard", out, m.arch, corrected_model())
        assert ra.ok and rb.ok, (key, ra.failed(), rb.failed())


# ------------------------------------------------------------------ the rules


@pytest.mark.parametrize("key", ["ring6x3v2", "ring4x3", "dual6"])
def test_every_rule_of_the_standard_set_holds_on_a_rotation(key):
    m = dev(key)
    for deltas, fill in (([1, 3, -2], None), ([2, -5], beside_corners(m, LOOP[key]))):
        prog = rotation(m, LOOP[key], deltas, fill)
        rep = check_programme("standard", prog, m.arch, corrected_model())
        assert rep.ok, [(r.id, r.examples[:2]) for r in rep.results if r.id in rep.failed()]
        held = {r.id for r in rep.results if r.verdict == HOLDS}
        assert {"R0", "R1", "R2", "R3", "R4", "R4d", "R5", "R8", "R11", "R22", "R23"} <= held
        res = replay(prog, m.arch, corrected_model())
        assert res.rules.ok(), [str(v) for v in res.rules.violations]


def test_a_corner_hop_is_the_same_motion_as_a_straight_one():
    m = dev("ring4x3")
    d = m.arch.device
    _, views = views_of(rotation(m, "L0", [1]), m)
    v = views[0]
    sigs = {m_.ion: motion_signature(v, m_.ion) for m_ in v.moves}
    assert set(sigs.values()) == {"rotate_cw L0:+1"}, sigs
    assert motion_signatures(v) == sigs
    memo: dict = {}
    assert motion_signatures(v, cache=memo) == sigs and motion_signatures(v, cache=memo) == sigs
    # the positive control: read rail by rail, the ion on S1 (two rails) differs from the one
    # on S0 (one rail) -- which is the difference the signature must not make
    on = loop_node_set(d)
    rails = lambda ion: ",".join(hop_label(d, on, x.src, x.dst, x.seg) for x in v.moves if x.ion == ion)
    assert rails("d0") == "L0:+1" and rails("d1") == "L0:+1,L0:+1"
    assert enters_bend(d, "S1", "J1", d.segment_between("S1", "J1"))
    assert not enters_bend(d, "J1", "S2", d.segment_between("J1", "S2"))
    # and back again
    _, back = views_of(rotation(m, "L0", [-1]), m)
    assert set(motion_signatures(back[0]).values()) == {"rotate_ccw L0:-1"}


def test_a_lattice_turning_at_a_two_rail_junction_keeps_its_lab_frame_labels():
    """`enters_bend` is a closed-loop matter: a grid's degree-2 corner junction is on no loop,
    and a walk round it keeps both lab-frame labels, as before."""
    g = Machine.grid(3, 3, name="g33")
    d = g.arch.device
    corner = next(n for n in d.nodes if d.nodes[n].kind == "junction" and d.degree(n) == 2)
    assert d.is_bend(corner)
    a, b = d.neighbours(corner)
    sa, sb = d.segment_between(a, corner), d.segment_between(corner, b)
    assert not enters_bend(d, a, corner, sa)
    on = loop_node_set(d)
    assert hop_label(d, on, a, corner, sa) != hop_label(d, on, corner, b, sb)


def test_a_hand_written_cycle_round_a_corner_is_one_waveform_and_an_unlike_one_is_not():
    m = dev("ring4x3")
    d = m.arch.device

    def prog(parts):
        place = {p.ion: p.src for p in parts}
        p = TSIR(name="hand", arch_spec="test")
        p.add(Instruction(type="init", id=p.next_id(), placement=place, quanta={k: 0.0 for k in place}))
        p.add(Instruction(type="simd", id=p.next_id(), cls="rotate_cw", mode="inter", participants=tuple(parts)))
        return p

    vias = bend_vias(d, "L0", 1)
    like = prog([Participant("a", "S0", "S1"), Participant("b", "S1", "S2", vias["S1"]),
                 Participant("c", "S4", "S5", vias["S4"])])
    res = replay(like, m.arch, corrected_model())
    assert res.rules.ok(), [str(v) for v in res.rules.violations]
    assert check_programme("standard", like, m.arch, corrected_model()).ok
    # one ion forward round a corner, one backward along a side: R11 and R22 both refuse it
    back = bend_vias(d, "L0", -1)
    unlike = prog([Participant("b", "S1", "S2", vias["S1"]), Participant("c", "S4", "S3")])
    res = replay(unlike, m.arch, corrected_model())
    assert {"R11", "R22"} <= res.rules.failed()
    rep = check_programme("standard", unlike, m.arch, corrected_model())
    assert {"R11", "R22"} <= set(rep.failed())
    assert back["S2"] == (d.segment_between("S2", "J1").id, d.segment_between("J1", "S1").id)


def test_the_dock_slots_a_rotation_carries_are_not_parked_junction_stops():
    """ring(6, 3, 2) has docks on S0 and S5, degree 3 and so junctions by R18; a packed
    rotation leaves ions on them, and R23 exempts the slots of a conveyed loop -- its SLOTS,
    which the corners are not."""
    m = dev("ring6x3v2")
    d = m.arch.device
    assert {"S0", "S5"} <= set(d.junction_nodes)
    prog = rotation(m, "L0", [1, 1, -3])
    assert conveyed_paths(prog, m.arch) == frozenset({"L0"})
    res = replay(prog, m.arch, corrected_model())
    assert "R23" not in res.rules.failed() and res.rules.ok()
    from qccd.ruleset.programme import programme_execution
    exe = programme_execution(prog, m.arch, corrected_model())
    rotated = {s.label() for s in exe.facts["rotated_nodes"]}
    assert rotated == set(d.loop_slots("L0"))
    assert check_programme("standard", prog, m.arch, corrected_model()).ok


# ------------------------------------------------------------------ the negative controls


def test_an_ion_left_on_a_corner_is_refused():
    m = dev("ring4x3")
    d = m.arch.device
    p = TSIR(name="parked", arch_spec="test")
    p.add(Instruction(type="init", id=p.next_id(), placement={"x": "S1", "y": "S3"}, quanta={"x": 0.0, "y": 0.0}))
    p.add(Instruction(type="simd", id=p.next_id(), cls="rotate_cw", mode="inter",
                      participants=(Participant("x", "S1", "J1", (d.segment_between("S1", "J1").id,)),)))
    res = replay(p, m.arch, corrected_model())
    assert res.rules.failed() == {"R1"}
    assert any("J1" in v.message for v in res.rules.violations if v.rule == "R1")
    # the rule language: a corner is neither a site (R1) nor, at two rails, a junction (R2,
    # R23), so it is R0 -- the move names a stop the device does not have -- that refuses it
    rep = check_programme("standard", p, m.arch, corrected_model())
    assert rep.failed() == ["R0"], [(r.id, r.verdict) for r in rep.results]
    # the same move carried on to the trap beyond the corner is fine
    ok = TSIR(name="carried", arch_spec="test")
    ok.add(p.instructions[0])
    ok.add(Instruction(type="simd", id=ok.next_id(), cls="rotate_cw", mode="inter",
                       participants=(Participant("x", "S1", "S2", bend_vias(d, "L0", 1)["S1"]),)))
    assert replay(ok, m.arch, corrected_model()).rules.ok()
    assert check_programme("standard", ok, m.arch, corrected_model()).ok


def test_an_ion_placed_on_a_corner_is_refused():
    m = dev("ring4x3")
    p = TSIR(name="placed", arch_spec="test")
    p.add(Instruction(type="init", id=p.next_id(), placement={"x": "J2", "y": "S0"}, quanta={"x": 0.0, "y": 0.0}))
    p.add(Instruction(type="simd", id=p.next_id(), cls="rotate_cw", mode="inter",
                      participants=(Participant("y", "S0", "S1"),)))
    rep = check_programme("standard", p, m.arch, corrected_model())
    assert rep.failed() == ["R0"], [(r.id, r.verdict) for r in rep.results]
    res = replay(p, m.arch, corrected_model())
    assert res.rules.failed() == {"R1"}
    assert any("J2" in v.message for v in res.rules.violations if v.rule == "R1")


def test_a_move_across_a_corner_must_say_which_rails_it_takes():
    """S1 and S2 are neighbouring slots but not neighbouring nodes: the producer writes the
    walk round the corner, and the replay does not guess it."""
    m = dev("ring4x3")
    p = TSIR(name="unrouted", arch_spec="test")
    p.add(Instruction(type="init", id=p.next_id(), placement={"x": "S1"}, quanta={"x": 0.0}))
    p.add(Instruction(type="simd", id=p.next_id(), cls="rotate_cw", mode="inter",
                      participants=(Participant("x", "S1", "S2"),)))
    with pytest.raises(ReplayError, match="no segment between"):
        replay(p, m.arch, corrected_model())


# ------------------------------------------------------------------ builders and listings


def test_the_builders_fill_and_turn_slots_only():
    from qccd.compile.programs import odd_even, rotate
    for key in ("ring6x3v2", "ring4x3"):
        m = dev(key)
        d = m.arch.device
        prog = rotate(m.arch, 3)
        assert set(prog.instructions[0].placement.values()) == set(d.loop_slots("L0"))
        res = replay(prog, m.arch, corrected_model())
        assert res.rules.ok(), [str(v) for v in res.rules.violations]
        oe = odd_even(m.arch, 2)
        res, views = views_of(oe, m)
        assert all(d.nodes[n].kind == "site" for v in views for n in v.pos_after.values())
        turned = [p for ins in oe.instructions for p in ins.participants if p.via]
        assert turned and all(any(d.is_bend(d.segments[s].ends[0]) or d.is_bend(d.segments[s].ends[1])
                                  for s in p.via) for p in turned)
        # (on ring6x3v2 a transposition merges two ions on a dock slot, a junction by R18,
        # which R2 refuses with trap corners as well; the corner-free ring is judged whole)
        if key == "ring4x3":
            assert not res.rules.failed() & {"R1", "R2", "R3", "R5", "R8", "R11"}, res.rules.failed()
        assert m.program("f", provenance="off").fill().build().instructions[0].placement == \
            {f"d{i}": s for i, s in enumerate(d.loop_slots("L0"))}


def test_the_bb_pipeline_compiles_on_a_junction_cornered_ring():
    """The Python ring router (`qccd.compile.pipeline`) on a junction-cornered ring: the
    [[72,12,6]] bivariate-bicycle code (the gross code's polynomials at l = m = 6) on
    38 x 2 with junction corners, 72 slots, 12 docks six apart."""
    from qccd.codes.bb import BBCode, build_checks
    from qccd.compile.pipeline import CompilePolicy, compile_code
    code = BBCode(l=6, m=6, a_terms=(("x", 3), ("y", 1), ("y", 2)), b_terms=(("y", 3), ("x", 1), ("x", 2)),
                  name="bb72_12_6")
    code.checks = build_checks(code)
    m = Machine.ring(38, 2, 12, name="ring72_j", corners="junction")
    d = m.arch.device
    assert len(d.loop_slots("L0")) == 72 == code.n
    # the identity placement: what is under test is the slots, not the annealer
    r = compile_code(m.arch, code, policy=CompilePolicy(insert_cooling=True, placement="identity"))
    assert r.contacts == 6 * 72
    assert any((ins.template or {}).get("kind") == "loop_shift" for ins in r.program.instructions)
    assert set(r.placement.values()) <= set(d.loop_slots("L0"))
    out = m.run(r.program, check_metrics=False)
    assert out.ok, out.rules_failed
    assert set(out.report.result.final_positions.values()) <= {n for n in d.nodes if d.nodes[n].kind == "site"}


def test_the_language_core_form_walks_round_the_corner():
    from qccd.site.examples import core_form, judge, machine, rec
    ring = {"generator": "ring", "params": {"width": 4, "height": 3, "verticals": 0, "corners": "junction"},
            "name": "ring4x3_core"}
    m = machine(ring)
    for calls in ([rec("fill"), rec("rotate", 1)], [rec("init", {"a": "S1", "b": "S4"}), rec("rotate", -2)]):
        core = core_form(m, calls)
        assert {c["method"] for c in core} <= {"init", "simd"}
        assert any(len(part) == 4 for c in core if c["method"] == "simd" for part in c["args"][1])
        a = m.program("a", provenance="off").apply_calls(calls).build()
        b = m.program("b", provenance="off").apply_calls(core).build()
        ja, jb = judge(m, a, "corrected"), judge(m, b, "corrected")
        assert (ja["failed"], ja["cost"], ja["us"]) == (jb["failed"], jb["cost"], jb["us"]) and not ja["failed"]


def test_the_listings_count_slots():
    from qccd.arch.listing import class_participants
    from qccd.ir.listing import disassemble
    m = dev("ring4x3")
    d = m.arch.device
    assert class_participants(m.arch, "rotate_cw") == d.loop_slots("L0")
    prog = rotation(m, "L0", [3])
    lst = disassemble(prog, m.arch, res=replay(prog, m.arch, corrected_model()), model=corrected_model())
    line = next(ln for ln in lst.lines if ln.type == "simd")
    assert line.movement.hops == 3 * len(d.loop_slots("L0"))


# ------------------------------------------------------------------ new designs


def test_a_new_ring_from_the_workspace_has_junction_corners_unless_it_says_otherwise():
    from qccd.workspace.design import empty_design, replay as replay_design
    from qccd.workspace.operations import OperationError, compile_operations
    base = empty_design("t")
    me = {"kind": "agent"}
    st = compile_operations(base, None, [{"type": "construct", "generator": "ring",
                                          "params": {"width": 4, "height": 3}}], me)
    d = replay_design(st).arch.device
    assert d.params.get("corners") == "junction" and "J0" in d.nodes and len(d.loop_slots("L0")) == 6
    st = compile_operations(base, None, [{"type": "construct", "generator": "ring",
                                          "params": {"width": 4, "height": 3, "corners": "site"}}], me)
    d = replay_design(st).arch.device
    assert "corners" not in d.params and len(d.loop_slots("L0")) == 10   # the older drawing, byte for byte
    st = compile_operations(base, None, [{"type": "construct", "generator": "dual_loop",
                                          "params": {"width": 6}}], me)
    assert replay_design(st).arch.device.params.get("corners") == "junction"
    # a ring whose docks do not divide the junction ring's slots says how to get the old one
    with pytest.raises(OperationError, match='"corners": "site"'):
        compile_operations(base, None, [{"type": "construct", "generator": "ring",
                                         "params": {"width": 8, "height": 2, "verticals": 8}}], me)
