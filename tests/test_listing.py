"""The two listings, the control record, and provenance.

The user can now read three things the toolchain previously only computed: the
architecture as the program that rebuilds it, the hardware program as a listing with the
executing instruction marked, and what the control plane is doing on the cycle being
animated.  All three are structured records with stable ids, because the point of
building them this way is that a design tool can later map a click back to an object --
so these tests are about the *structure* holding, not about the text reading nicely.

The load-bearing properties, in the order they would hurt if they broke:

**Template compression survives.**  A rigid rotation of 144 ions is ONE listing line.
If anyone "fixes" the IR by pre-expanding rotations into explicit participants, the
listing becomes 144 lines, `templates()` collapses, and PLAN §1's headline result stops
being visible.

**Ids agree, everywhere.**  `Line.id == Instruction.id == frame.id == ControlRecord.instr_id`,
and the architecture listing's `class:dock` is the same `dock` the program's instruction
names.  Every click-through in the page and every edit in a future editor rides on that.

**Provenance changes no number.**  It is annotation in the strictest sense; the same
program replayed with and without it must produce identical cost, steps, quanta and rule
verdicts, or the four external oracles are no longer meaningful.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.arch import load  # noqa: E402
from qccd.arch.listing import (  # noqa: E402
    architecture_listing,
    class_participants,
    round_trip_check,
)
from qccd.compile import build  # noqa: E402
from qccd.cost import corrected_model, deck_model  # noqa: E402
from qccd.ir import provenance as prov  # noqa: E402
from qccd.ir.listing import (  # noqa: E402
    disassemble,
    render,
    render_line,
    to_page_model,
)
from qccd.ir.tsir import TSIR  # noqa: E402
from qccd.verify import replay, verify  # noqa: E402
from qccd.verify.control import control_record, control_trace  # noqa: E402
from qccd.viz import build_view_model, render_html  # noqa: E402

ARCH_DIR = ROOT / "arch"
DEVICES = sorted(p.stem.replace(".arch", "") for p in ARCH_DIR.glob("*.arch.json"))


@pytest.fixture(scope="module")
def ring():
    return load(ARCH_DIR / "ring144_24v.arch.json")


@pytest.fixture(scope="module")
def deck(ring):
    """The shipped schedule, disassembled against a real replay -- built once."""
    prog = build(ring, "deck")
    model = corrected_model()
    res = verify(prog, ring, model, check_metrics=False).result
    return prog, ring, model, res, disassemble(prog, ring, res=res, model=model)


# ===================================================================== program


def test_a_rigid_rotation_of_144_ions_is_one_line_not_144(ring):
    """PLAN §1's thesis, in the listing: one template is one line.

    The replay expands a `loop_shift` of |delta| into |delta| unit sub-cycles, but that
    is a REPLAY detail.  The listing shows what the hardware is *issued*: one class, one
    direction, variadic participation.
    """
    prog = build(ring, "rotate", 13)
    model = corrected_model()
    res = replay(prog, ring, model, check_rules=False)
    listing = disassemble(prog, ring, res=res, model=model)
    rot = [l for l in listing.lines if l.type == "simd"]
    assert len(rot) == 1, [render_line(l) for l in rot]
    line = rot[0]
    assert line.op == "ROT.CW"
    assert line.width == 144, "the line must still say how many ions moved"
    assert line.movement.kind == "template" and line.movement.delta == 13
    assert line.cost.cycles == 13, "13 machine cycles, still one instruction"
    text = render_line(line)
    assert text.count("\n") == 0
    # and the whole program is two lines: place, then rotate
    assert len(listing.lines) == 2


def test_the_odd_even_sort_needs_a_column_where_rotation_needs_a_line(ring):
    """The same realignment, two schemes, on the same device -- printed."""
    rot = disassemble(build(ring, "rotate", 36), ring)
    oe = disassemble(build(ring, "oddeven", 36), ring)
    assert len(rot.lines) == 2
    assert len(oe.lines) > 100, len(oe.lines)
    assert len(rot.summary.templates) < len(oe.summary.templates)


def test_every_listing_row_carries_the_instruction_id_it_describes(deck):
    prog, arch, model, res, listing = deck
    assert len(listing.lines) == len(prog.instructions)
    for line, instr in zip(listing.lines, prog.instructions):
        assert line.id == instr.id
        assert line.type == instr.type
        assert listing.by_id(instr.id) is line
    # ids are unique, which is what makes `by_id` and the page's index well defined
    assert len({l.id for l in listing.lines}) == len(listing.lines)


def test_the_rule_column_lands_on_the_exact_instruction_that_broke_the_rule(deck):
    """R7 gates a too-hot ion; the `!` column must point at that CX, not at the program."""
    prog, arch, model, res, listing = deck
    flagged = [l for l in listing.lines if l.rules]
    assert flagged, "the deck program does fail R7 under the corrected model"
    assert all(l.type == "gate" for l in flagged if "R7" in l.rules)
    by_id = {v.instr_id for v in res.rules.violations if v.rule == "R7"}
    assert {l.id for l in flagged if "R7" in l.rules} == by_id


def test_the_listing_replays_the_costs_it_was_given(deck):
    prog, arch, model, res, listing = deck
    total = sum(l.cost.cost or 0.0 for l in listing.lines)
    assert total == pytest.approx(res.total_cost)
    assert sum(l.cost.steps or 0 for l in listing.lines) == res.total_steps
    assert sum(l.cost.cycles for l in listing.lines) == len(res.cycles)
    assert listing.summary.n_instructions == len(prog)


def test_without_a_replay_the_listing_shows_claims_and_says_nothing_it_cannot_know(ring):
    """Graceful degradation is a property, not an accident: `.` means unknown, never 0."""
    prog = build(ring, "rotate", 3)
    listing = disassemble(prog)                      # no arch, no replay, no model
    line = listing.lines[1]
    assert line.cost.us is None and line.cost.dnbar is None
    assert " . " in render_line(line) or render_line(line).rstrip().endswith(".")


def test_the_page_model_is_addressable_by_instruction_id(deck):
    prog, arch, model, res, listing = deck
    pm = to_page_model(listing)
    assert pm["ids"] == [i.id for i in prog.instructions]
    # interning actually interns: six mnemonics across 1,579 rows
    assert len(pm["ops"]) < 12 and len(pm["pts"]) < 12
    assert len(pm["detail"]) == len(prog.instructions)
    blob = json.dumps(pm, separators=(",", ":"))
    assert len(blob) < 400_000, f"the page model must stay small: {len(blob)}"


def test_folding_a_program_with_inserted_cooling_does_not_shatter_it(ring):
    """An inserted `cool` carries `batch` but no `group`; a `barrier` carries neither.

    Left dangling, those blanks make one section per inserted instruction -- ~400
    spurious sections on the deck program.  A blank key is filled forward from the
    instruction it precedes, on the theory that an inserted instruction serves what
    comes after it.
    """
    from qccd.compile import CoolingPolicy, insert_cooling

    model = corrected_model()
    prog = insert_cooling(build(ring, "deck"), ring, model,
                          policy=CoolingPolicy()).program
    listing = disassemble(prog, ring)
    assert len(listing.sections) < 20, [s.label for s in listing.sections[:12]]
    assert listing.sections[0].kind in ("group", "round", "batch", "flat")


def test_render_produces_one_row_per_instruction_at_depth_one(ring):
    prog = build(ring, "walk", 3)
    listing = disassemble(prog, ring)
    text = render(listing, depth=1, header=False, cursor_id=listing.lines[1].id)
    rows = [r for r in text.splitlines() if r and not r.startswith("==")]
    assert len(rows) == len(prog.instructions)
    assert sum(1 for r in rows if r.startswith(">")) == 1, "one cursor, on one row"


# ================================================================ architecture


@pytest.mark.parametrize("device", DEVICES)
def test_every_shipped_architecture_round_trips_through_its_own_listing(device):
    """Emit the Python, exec it, diff the result.  This is what makes the architecture
    panel a *program* rather than a description of one."""
    arch = load(ARCH_DIR / f"{device}.arch.json")
    ok, diff = round_trip_check(arch)
    assert ok, f"{device}: {diff[:4]}"


def test_the_architecture_listing_names_the_object_every_statement_declares(ring):
    listing = architecture_listing(ring, mode="full")
    targets = {l.target for l in listing.lines if l.target}
    assert "class:dock" in targets and "class:rotate_cw" in targets
    assert "zone:ancilla" in targets and "curve:shuttle_segment" in targets
    assert "control.channels" in targets and "loop:L0" in targets
    # every declared SIMD class has a record, and it is a `call` record an editor can
    # rewrite rather than a comment it would have to parse
    for cid in ring.simd_classes:
        rows = listing.lines_for(f"class:{cid}")
        assert rows, cid
        rec = listing.by_n(rows[0])
        assert rec.kind == "call" and rec.call["method"] == "declare_class"
        assert rec.call["args"] == [cid]


def test_clicking_any_node_or_segment_lands_somewhere_in_the_listing(ring):
    """In generator mode no record declares an individual node, so the index falls back
    to the generator call.  A click that resolves to nothing is a dead end in the UI."""
    listing = architecture_listing(ring, mode="full")
    for nid in list(ring.device.nodes)[:20]:
        assert listing.lines_for(f"site:{nid}"), nid
    for sid in list(ring.device.segments)[:20]:
        assert listing.lines_for(f"segment:{sid}"), sid


def test_dragging_a_site_moves_the_listing_into_patch_mode_and_still_round_trips():
    """The drag operation, and what it does to the description of the machine."""
    m = Machine.load(ARCH_DIR / "ring144_24v.arch.json")
    assert m.arch.device.reproducible_from_generator()
    before = len(architecture_listing(m.arch, mode="full").lines)
    m.move_site("A0", 36.0, 0.72).set_site_capacity("A0", 4)
    assert not m.arch.device.reproducible_from_generator()
    listing = architecture_listing(m.arch, mode="full")
    assert listing.mode == "patch"
    # the patch does NOT restate 168 nodes; it restates the one that moved
    assert len(listing.lines) < before + 8
    assert listing.lines_for("site:A0")
    ok, diff = round_trip_check(m.arch, listing=listing)
    assert ok, diff[:4]


def test_an_added_node_forces_the_explicit_form_and_that_round_trips_too():
    from dataclasses import replace

    from qccd.arch import Device, Node, Segment

    m = Machine.load(ARCH_DIR / "h2_racetrack.arch.json")
    d = m.arch.device
    anchor = next(iter(d.nodes))
    nodes = dict(d.nodes)
    nodes["X9"] = Node(id="X9", pos=(99.0, 9.0), kind="site", capacity=2,
                       zone_type="trap", labels=("extra",))
    segs = dict(d.segments)
    segs["XSEG"] = Segment(id="XSEG", ends=(anchor, "X9"), labels=("spur",))
    m.arch = replace(m.arch, device=Device(nodes=nodes, segments=segs, loops=d.loops,
                                           generator=d.generator, params=d.params))
    listing = architecture_listing(m.arch, mode="full")
    assert listing.mode == "explicit"
    assert listing.lines_for("site:X9") and listing.lines_for("segment:XSEG")
    ok, diff = round_trip_check(m.arch, listing=listing)
    assert ok, diff[:4]


def test_class_participants_resolves_an_orbit_to_the_sites_that_may_take_part(ring):
    assert len(class_participants(ring, "rotate_cw")) == 144       # the loop
    assert len(class_participants(ring, "shuttle")) == 168         # "any"
    assert len(class_participants(ring, "dock")) == 48             # the spur endpoints
    assert class_participants(ring, "no_such_class") == ()


def test_a_direct_wired_plane_summarizes_its_channels_instead_of_listing_4608():
    """grid9x9 expands to 4608 channel groups.  Enumerating them would make the listing
    unusable; the scheme is one fact, not 4608 facts."""
    arch = load(ARCH_DIR / "grid9x9.arch.json")
    listing = architecture_listing(arch, mode="full")
    control = [l for l in listing.lines if l.section == "control"]
    assert len(control) < 30, len(control)
    assert any("channel(s) over" in l.text for l in control)


# ===================================================================== control


def test_a_dock_is_never_reported_as_drivable_because_nothing_judged_it(ring):
    """Every dock in the deck program runs on a `spur` segment that belongs to no loop,
    so R4d is silent about it.  Silence is not a pass, and the record must say so with
    `None` rather than `True`."""
    prog = build(ring, "deck")
    trace = control_trace(prog, ring, corrected_model())
    by_cls = {}
    for r in trace.records:
        by_cls.setdefault(r.cls, []).append(r)
    assert all(r.feasible is None for r in by_cls["dock"])
    assert all(r.feasible is None for r in by_cls["undock"])
    assert all(r.feasible is True for r in by_cls["rotate_cw"])


def test_a_whole_program_collapses_to_a_handful_of_control_states(ring):
    """1,579 instructions, 3,861 machine cycles, ~12 distinct control states.  That is
    what makes precomputing them into the page cheap enough to be the right answer."""
    prog = build(ring, "deck")
    trace = control_trace(prog, ring, corrected_model())
    assert len(trace.records) == len(prog.instructions)
    assert len(trace.index) == len(prog.instructions)
    assert len(trace.table) <= 20, len(trace.table)
    assert trace.varies == [], "a folded instruction whose hops disagreed"
    blob = json.dumps(trace.to_json(), separators=(",", ":"))
    assert len(blob) < 40_000, len(blob)
    # and the record's id is the instruction's id, which is the listing row's id
    assert [r.instr_id for r in trace.records] == [i.id for i in prog.instructions]


def test_the_control_record_counts_the_serialization_broadcast_wiring_buys(ring):
    """Moving one ion engages every channel and holds 167 of 168 sites out by switch.
    Both halves of the WISE trade, on one cycle."""
    prog = build(ring, "deck")
    trace = control_trace(prog, ring, corrected_model())
    dock = next(r for r in trace.records if r.cls == "dock")
    rot = next(r for r in trace.records if r.cls == "rotate_cw")
    assert dock.channels_engaged == dock.channels_total == 32
    assert dock.sites_acting == 1 and dock.sites_held == 167
    assert rot.sites_acting == 144 and rot.sites_held == 24
    assert rot.duty > 0.85 and dock.duty < 0.01
    assert dock.switch_elements == 1 * 24 * 2


def test_an_undeclared_control_plane_claims_nothing():
    arch = load(ARCH_DIR / "chain.arch.json")
    prog = build(arch, "walk", 2)
    trace = control_trace(prog, arch, corrected_model())
    assert all(r.driver == "undeclared" for r in trace.records)
    assert all(r.feasible is None for r in trace.records)
    assert any("declares no control.channels" in n for n in trace.notes)


def test_the_control_record_and_the_verifier_cannot_disagree(ring):
    """Both read the same CycleView through the same `ControlPlane.drivable` call, so
    the panel's failure text is byte-identical to the verifier's."""
    from qccd.verify.rules import CycleView, r4_drivable

    seen = []
    prog = build(ring, "rotate", 2)
    replay(prog, ring, corrected_model(), check_rules=False, keep_cycles=False,
           on_cycle=lambda v: seen.append((control_record(v), r4_drivable(v))))
    assert seen
    for rec, viols in seen:
        assert rec.feasible is (not viols)
        assert list(rec.problems) == [v.message for v in viols]


def test_drivability_still_reads_the_way_the_rule_documents_it(ring):
    """Two ions counter-rotating on one broadcast loop is the canonical R4 failure."""
    plane = ring.control_plane
    ok, why = plane.drivable({"S0": "L0:+1", "S6": "L0:+1"})
    assert ok and not why
    ok, why = plane.drivable({"S0": "L0:+1", "S6": "L0:-1"})
    assert not ok
    assert "different things" in why[0] and "L0:+1" in why[0] and "L0:-1" in why[0]


def test_the_engagement_index_agrees_with_the_walk_it_replaced(ring):
    """`engagement` picks whichever walk is cheaper; both must give the same answer."""
    plane = ring.control_plane
    for actions in ({"S0": "a"}, {"S0": "a", "S1": "a"},
                    {n: "a" for n in list(ring.device.nodes)[:80]}):
        eng = plane.engagement(actions)
        assert len(eng) == len(plane.groups)          # broadcast: everything is engaged
        assert all(e.acting == len(actions) for e in eng)
        assert plane.covered_sites([e.group.id for e in eng]) == plane.n_sites
    assert plane.channels_of("S0") == tuple(
        g.id for g in plane.groups if "S0" in g.sites)


# ================================================================== provenance


def test_provenance_records_the_python_call_that_emitted_each_instruction():
    m = Machine.load(ARCH_DIR / "ring144_24v.arch.json")
    p = m.program("demo")
    p.fill()
    p.rotate(+13, batch=0)
    with p.cycle("dock") as c:
        c.move("d143", "S0", "A0", via=["V0"])
    prog = p.build()
    rows = prov.listing_rows(prog)
    assert [r["src"]["op"] for r in rows] == ["fill", "rotate", "cycle"]
    here = __file__.replace("\\", "/")
    for r in rows:
        assert r["src"]["file"].endswith(Path(here).name)
    # the reported line for a `with p.cycle(...)` is the user's `with`, not __exit__
    assert "p.cycle(" in rows[2]["src"]["text"]


def test_a_shuttle_is_one_call_however_many_instructions_it_emits():
    """The re-entrancy guard: `p.shuttle` calls `p.move` four times, and all four
    instructions must point at the shuttle line the user actually wrote."""
    m = Machine.chain(8, name="prov_chain")
    p = m.program("walk").init({"d0": "C0"})
    p.shuttle("d0", [f"C{i}" for i in range(5)])
    prog = p.build()
    calls = prov.calls_to_instructions(prog)
    assert len(calls) == 2, calls                   # init, shuttle
    shuttle_call = max(calls)
    assert len(calls[shuttle_call]) == 4
    assert prov.call_of(prog, prog.instructions[-1])["op"] == "shuttle"


def test_provenance_survives_a_json_round_trip():
    m = Machine.load(ARCH_DIR / "ring144_24v.arch.json")
    p = m.program("rt").fill().rotate(+5)
    prog = p.build()
    back = TSIR.from_json(json.loads(json.dumps(prog.to_json())))
    assert prov.log_of(back) == prov.log_of(prog)
    assert prov.listing_rows(back) == prov.listing_rows(prog)
    assert prov.index_by_line(back) == prov.index_by_line(prog)


def _assert_identities_survive(before: TSIR, after: TSIR) -> None:
    """`Instruction.id` is a HANDLE.  A pass may insert; it may not re-label.

    Every join in the system keys on it -- `CycleRecord.instr_id`, `Violation.instr_id`,
    `ControlRecord.instr_id`, `Listing.by_id`, the page's `LROW`,
    `prov.calls_to_instructions` -- so a pass that renumbers silently re-targets every
    one of them, with no exception and no diagnostic.  This is the property that stops
    that, factored out so the mutation guard below can feed it a counterexample.
    """
    kept = {i.id: i for i in after.instructions}
    assert len(kept) == len(after.instructions), "instruction ids are not unique"
    assert after.id_seq > max(kept), "the allocator would hand out a live id"
    for i in before.instructions:
        assert i.id in kept, f"instruction {i.id} lost its identity across the pass"
        assert kept[i.id].type == i.type, f"id {i.id} now names a {kept[i.id].type}"
        assert kept[i.id].meta.get("call") == i.meta.get("call"), \
            f"id {i.id} changed provenance"
    old = {i.id for i in before.instructions}
    # the relative order of the survivors is unchanged
    assert ([i.id for i in after.instructions if i.id in old]
            == [i.id for i in before.instructions])
    # what the pass created collides with nothing that existed
    fresh = [i.id for i in after.instructions if i.id not in old]
    assert set(fresh).isdisjoint(old) and len(set(fresh)) == len(fresh)


def test_an_instruction_id_survives_the_cooling_pass(ring):
    """The precondition for a program editor: a selection, an undo entry, a breakpoint
    or a comment anchor must survive a compiler pass.

    Before this change `insert_cooling` re-labelled wholesale, so of the 1,578 ids
    `prov.index_by_line` reported for `import_deck.py:162` on the deck program, 2
    survived.  The `unfixable` / `before_instruction` clauses are not decoration: they
    are the assertions that failed, because `CoolingResult.unfixable` reported ids of the
    program you passed IN beside the program you got BACK (measured on `walk 3`: reported
    id 6 was a `simd` in the input and a `cool` in the output).
    """
    from qccd.compile import CoolingPolicy, insert_cooling

    model = corrected_model()
    before = build(ring, "walk", 3)
    r = insert_cooling(before, ring, model,
                       policy=CoolingPolicy(max_quanta_between_cools=1.0))
    after = r.program
    assert r.n_cools > 0 and len(after) > len(before), "the pass must have inserted"
    _assert_identities_survive(before, after)

    # every id the RESULT reports indexes the program the result carries
    by_id = {i.id: i for i in after.instructions}
    for iid in r.unfixable:
        assert iid in by_id, f"unfixable {iid} names no instruction of the result"
    n_pointers = 0
    for instr in after.instructions:
        if instr.type == "cool" and "before_instruction" in instr.meta:
            tgt = instr.meta["before_instruction"]
            assert tgt in by_id, f"cool {instr.id} points at no instruction of the result"
            n_pointers += 1
    assert n_pointers > 0, "no cool carried a `before_instruction`; nothing was checked"


def test_the_identity_check_actually_catches_a_pass_that_renumbers(ring):
    """MUTATION GUARD.  A test that cannot fail is not a test.

    The renumbered program planted here is internally *consistent* -- unique ids, a valid
    `id_seq`, the right order -- which is exactly why the check has to take `before` as
    well as `after`.  A one-sided check would pass it.
    """
    from dataclasses import replace as dc_replace

    from qccd.compile import CoolingPolicy, insert_cooling

    before = build(ring, "walk", 3)
    r = insert_cooling(before, ring, corrected_model(),
                       policy=CoolingPolicy(max_quanta_between_cools=1.0))
    _assert_identities_survive(before, r.program)      # the real one passes

    renumbered = TSIR(
        name=r.program.name, arch_spec=r.program.arch_spec,
        instructions=[dc_replace(i, id=n)
                      for n, i in enumerate(r.program.instructions)],
        metrics=dict(r.program.metrics), meta=dict(r.program.meta),
        id_seq=len(r.program.instructions))
    with pytest.raises(AssertionError, match="lost its identity|now names a"):
        _assert_identities_survive(before, renumbered)


def test_the_reverse_index_a_program_editor_needs_is_durable(ring):
    """`prov.index_by_line` is the reverse index the editor phase is meant to be built
    on.  Measured before this change on the deck program: 2 of 1,578 ids survived the
    cooling pass for `import_deck.py:162`.  Now every one does."""
    from qccd.compile import CoolingPolicy, insert_cooling

    before = build(ring, "walk", 3)
    after = insert_cooling(before, ring, corrected_model(),
                           policy=CoolingPolicy(max_quanta_between_cools=1.0)).program
    ix_before, ix_after = prov.index_by_line(before), prov.index_by_line(after)
    assert ix_before, "no provenance index at all; nothing was checked"
    for key, ids in ix_before.items():
        assert key in ix_after, f"{key} vanished from the index"
        kept = [i for i in ids if i in set(ix_after[key])]
        assert kept == ids, (
            f"{key}: {len(ids) - len(kept)} of {len(ids)} ids no longer name the "
            f"instruction they named before the pass")


def test_the_addr_column_counts_even_though_the_ids_do_not(ring):
    """The one user-visible consequence, and the reason two display sites had to move in
    the same change.  Ids are identities and so interleave after an insertion
    (`0,1,2,1579,3,...`); the leftmost column of the listing a human scrolls is a
    position and must be strictly increasing."""
    from qccd.compile import CoolingPolicy, insert_cooling

    before = build(ring, "walk", 3)
    after = insert_cooling(before, ring, corrected_model(),
                           policy=CoolingPolicy(max_quanta_between_cools=1.0)).program
    lst = disassemble(after, ring)
    addr = [int(render_line(ln).split()[0]) for ln in lst.lines]
    assert addr == sorted(addr) and len(set(addr)) == len(addr), addr[:12]
    ids = to_page_model(lst)["ids"]
    assert ids != sorted(ids), (
        "the ids are still dense and ordered, so this test proves nothing -- the pass "
        "must have stopped inserting")
    # and the two are deliberately different things
    assert addr != ids


def test_id_seq_round_trips_and_a_legacy_program_migrates(ring):
    """The allocator's high-water mark serializes; a document written before it existed
    resumes above the highest id it carries."""
    prog = build(ring, "walk", 3)
    doc = prog.to_json()
    assert doc["id_seq"] == prog.id_seq
    assert TSIR.from_json(doc).id_seq == prog.id_seq
    legacy = {k: v for k, v in doc.items() if k != "id_seq"}
    assert TSIR.from_json(legacy).id_seq == max(i.id for i in prog.instructions) + 1
    assert TSIR.from_json({"name": "e", "arch_spec": "x"}).id_seq == 0


def test_provenance_survives_the_cooling_pass(ring):
    """What survives is instruction -> call, and the derived indices rebuild from it."""
    from qccd.compile import CoolingPolicy, insert_cooling

    model = corrected_model()
    before = build(ring, "walk", 3)
    r = insert_cooling(before, ring, model,
                       policy=CoolingPolicy(max_quanta_between_cools=1.0))
    after = r.program
    assert r.n_cools > 0 and len(after) > len(before), "the pass must have inserted"
    # EVERY instruction knows where it came from -- not just the ones the builder made.
    # The cools this pass inserts have a source too: the pass, and the policy it ran
    # under. They used to carry none at all, so a fifth of the flagship page's listing
    # rows read "no source line recorded".
    untagged = [i for i in after.instructions if "call" not in (i.meta or {})]
    assert not untagged, f"{len(untagged)} instructions lost their provenance"
    for instr in after.instructions:
        r = prov.resolve(after, instr)
        assert r is not None, f"instr {instr.id} has a call index that resolves to nothing"
        if instr.type == "cool":
            assert r["file"].endswith("cooling.py"), r
            assert r["op"] == "compile.insert_cooling", r
        else:
            assert r["file"].endswith("programs.py"), r


def test_adding_provenance_changes_no_number(ring):
    """The whole basis for having it on by default: it is annotation, and annotation
    may not move a metric.  Same program, same model, with and without."""
    model = corrected_model()

    def numbers(provenance):
        m = Machine.load(ARCH_DIR / "ring144_24v.arch.json")
        p = m.program("cmp", provenance=provenance).fill().rotate(+7)
        with p.cycle("dock") as c:
            c.move("d137", "S0", "A0", via=["V0"])
        p.gate("CX", [("d137", "a0")], sites=["A0"])
        p.cool()
        res = replay(p.build(), ring, model, check_rules=True)
        return {
            "cost": res.total_cost, "steps": res.total_steps, "us": res.total_us,
            "peak": res.peak_quanta, "pairs": res.n_gate_pairs,
            "per_ion": res.per_ion_quanta, "final": res.final_quanta,
            "by_class": res.cost_by_class, "hops": res.hops_by_class,
            "cycles": [(c.instr_id, c.cost, c.depth) for c in res.cycles],
            "failed": sorted(res.rules.failed()), "passed": res.rules.passed(),
        }

    assert numbers("calls") == numbers("off")


def test_the_shipped_oracle_is_untouched_by_provenance(ring):
    """cost 397184 / steps 8808 under the deck's own model, with every instruction of
    the imported schedule now carrying a `call` index."""
    prog = build(ring, "deck")
    assert all("call" in (i.meta or {}) for i in prog.instructions)
    res = replay(prog, ring, deck_model(), check_rules=False)
    assert res.total_cost == 397184
    assert res.total_steps == 8808


def test_exporting_provenance_drops_the_absolute_path_of_the_build_machine():
    m = Machine.load(ARCH_DIR / "ring144_24v.arch.json")
    prog = m.program("x").fill().rotate(+1).build()
    log = prov.log_of(prog)
    assert log["root"], "recorded in memory"
    thin = prov.thin(log, "sites")
    assert "root" not in thin
    assert all(not c.get("args") for c in thin["calls"])
    assert prov.thin(log, "off") is None


# ======================================================================== page


def test_the_emitted_page_has_a_listing_row_for_every_animated_frame(tmp_path, ring):
    prog = build(ring, "deck")
    model = corrected_model()
    res = verify(prog, ring, model, check_metrics=False).result
    view = build_view_model(ring, prog, res, model)
    frames = view["program"]["frames"]
    listing = view["listing"]
    assert listing is not None
    row_of = {i: n for n, i in enumerate(listing["ids"])}
    for f in frames:
        assert f["id"] in row_of, f["id"]
        assert "ctl" in f
        assert 0 <= f["ctl"] < len(view["control"]["records"])
    assert len(view["control"]["index"]) == len(prog.instructions)


def test_the_page_carries_the_architecture_as_a_program(ring):
    prog = build(ring, "rotate", 3)
    model = corrected_model()
    res = verify(prog, ring, model, check_metrics=False).result
    view = build_view_model(ring, prog, res, model)
    al = view["arch"]["listing"]
    assert al["mode"] == "generator"
    assert any(l["kind"] == "call" for l in al["lines"])
    # the join the two panels share: the instruction's class is an architecture id
    cls = view["program"]["frames"][1]["cls"]
    assert al["index"]["class:" + cls]
    # and the existing arch keys the page's draw() reads are untouched
    for k in ("name", "nodes", "segments", "loops", "summary", "hardware",
              "zone_types", "generator", "params"):
        assert k in view["arch"]


def test_the_page_stays_self_contained_with_prose_and_source_text_in_the_blob(tmp_path):
    """Provenance and the architecture listing put user-authored text into the JSON.
    `json.dumps` does not escape `<`, so a description containing `</script>` would end
    the data block early -- and the seven forbidden tokens apply to the blob too."""
    m = Machine.load(ARCH_DIR / "ring144_24v.arch.json")
    m.describe("a </script> <script src=x> @import fetch( XMLHttpRequest test",
               note='href="http://example.com"')
    p = m.program("nasty").fill().rotate(+1)
    out = m.render(p, tmp_path / "nasty.html")
    txt = out.read_text(encoding="utf-8")
    for bad in ("<script src=", "<link ", "@import", "fetch(", "XMLHttpRequest",
                "<img src=", 'href="http'):
        assert bad not in txt, bad
    import re

    data = re.search(r'<script id="data" type="application/json">(.*?)</script>',
                     txt, re.S)
    assert data
    doc = json.loads(data.group(1))
    assert "</script>" in doc["arch"]["description"], "the text survives, escaped"


def test_render_html_refuses_to_write_a_page_that_would_reach_the_network(tmp_path,
                                                                         monkeypatch):
    """The guard is what turns a mystifying test failure into a located one."""
    import qccd.viz.render as R

    arch = load(ARCH_DIR / "chain.arch.json")
    prog = build(arch, "walk", 2)
    model = corrected_model()
    res = replay(prog, arch, model, check_rules=False)
    monkeypatch.setattr(R, "_TEMPLATE", R._TEMPLATE.replace(
        "<title>", '<link rel="x"><title>'))
    with pytest.raises(ValueError, match="self-contained"):
        R.render_html(arch, prog, res, model, tmp_path / "bad.html")
