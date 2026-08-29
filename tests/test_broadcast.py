"""R4c -- broadcast as a language primitive, and the negative test for every clause.

The design question this file pins: **a TSIR `simd` instruction has never said whether
its participants are driven by ONE waveform or by one waveform each.**  The same four-hop
cycle is legal on `grid9x9` (direct wiring, 5,760 DACs) and on `deck_unit_cell` (broadcast
wiring, 44 DACs) -- the same 225-node graph -- and until `broadcast` existed the file was
byte-identical and every rule agreed.  `test_the_two_wirings_were_indistinguishable_before`
holds that fact still, so the argument for the field cannot rot.

Every claim is an INTENT: it names no channel (a ring rotation engages `linear_h`,
`linear_v` and `junction` at once) and states no count (the count is
`len(Device.corners(loop))`, a device property).  The verifier computes what the device
would need and reports the disagreement.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for p in (ROOT, ROOT / "tools"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from qccd.arch import load  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.ir import TSIR, Instruction, Participant  # noqa: E402
from qccd.ir.tsir import BROADCASTS, broadcast_kind, validate_program  # noqa: E402
from qccd.verify import verify  # noqa: E402

pytest.importorskip("PIL", reason="the tiny figure devices live in the figure tool")

from make_rule_figs import init, lab_ring, prog, rotate, wired_ring  # noqa: E402

MODEL = corrected_model()
PLACE8 = {f"i{i}": f"S{i}" for i in range(8)}


def fired(program, arch):
    rep = verify(program, arch, MODEL, check_metrics=False)
    return {v.rule for v in rep.rules.violations}, rep


# ------------------------------------------------------------------ transport


def test_a_path_frame_rotation_really_is_one_waveform():
    """H2's reading: the tiling follows the trap axis, bends included (2305.03828)."""
    arch = wired_ring()
    assert arch.control_plane.frame == "path"
    rules, _ = fired(prog(init(PLACE8), rotate(broadcast="one")), arch)
    assert rules == set(), rules


def test_a_lab_frame_rotation_is_not_one_waveform_and_r4c_is_the_only_rule_that_says_so():
    """THE ISOLATING WITNESS.

    R4d passes -- every channel of the four-group map is uniform, the wiring CAN produce
    this cycle.  R19 passes -- four groups for four directions.  R11 passes -- one
    direction along the loop.  R4 passes -- the class is declared.  The only thing wrong
    is what the PROGRAM said about itself.
    """
    arch = lab_ring()
    assert arch.control_plane.frame == "lab"
    rules, rep = fired(prog(init(PLACE8), rotate(broadcast="one")), arch)
    assert rules == {"R4c"}, rules
    msg = str(rep.rules.violations[0])
    assert "needs 4 distinct drives" in msg and "len(corners) = 4" in msg, msg


def test_per_direction_is_the_lab_frame_spelling_and_is_legal():
    rules, _ = fired(prog(init(PLACE8), rotate(broadcast="per_direction")), lab_ring())
    assert rules == set(), rules


def test_the_same_instruction_reads_both_ways_and_the_device_decides():
    """The point of the whole design: one rotation, two machines, two verdicts."""
    one = prog(init(PLACE8), rotate(broadcast="one"))
    assert fired(one, wired_ring())[0] == set()
    assert fired(one, lab_ring())[0] == {"R4c"}


# ------------------------------------------------- one broadcast vs many drives


def _four_hops(arch):
    """Four traps of a grid, each hopping to the next trap along."""
    dev = arch.device

    def hop(t):
        seen, frontier = {t: []}, [t]
        while frontier:
            cur = frontier.pop(0)
            for sid in dev.incidence[cur]:
                nxt = dev.segments[sid].other(cur)
                if nxt in seen:
                    continue
                seen[nxt] = seen[cur] + [sid]
                if dev.nodes[nxt].kind == "site":
                    return nxt, tuple(seen[nxt])
                frontier.append(nxt)
        return None, ()

    movers, used = [], set()
    for n in dev.nodes.values():
        if n.kind != "site":
            continue
        dst, via = hop(n.id)
        if dst and dst not in used and n.id not in used and dst != n.id:
            movers.append((n.id, dst, via))
            used |= {n.id, dst}
        if len(movers) == 4:
            break
    parts = tuple(Participant(f"i{k}", s, d, via)
                  for k, (s, d, via) in enumerate(movers))
    place = {p.ion: p.src for p in parts}
    return parts, place


def _grid_prog(parts, place, broadcast=False):
    return TSIR(name="four_hops", arch_spec="inline", instructions=[
        Instruction(type="init", id=0, placement=place,
                    quanta={k: 0.0 for k in place}),
        Instruction(type="simd", id=1, cls="shuttle", mode="inter",
                    participants=parts, broadcast=broadcast)])


def test_the_two_wirings_were_indistinguishable_before():
    """The defect, pinned.  Same graph, same file, 5,760 DACs against 44, one verdict.

    If this ever starts failing because the two devices diverge for some other reason,
    the argument for `broadcast` needs re-making, not this test deleting.
    """
    g = load(ROOT / "arch" / "grid9x9.arch.json")
    d = load(ROOT / "arch" / "deck_unit_cell.arch.json")
    assert list(g.device.nodes) == list(d.device.nodes)
    assert (g.control_plane.grouping, d.control_plane.grouping) == ("direct", "broadcast")
    parts, place = _four_hops(g)
    p = _grid_prog(parts, place)
    assert json.dumps(p.to_json(), sort_keys=True) == json.dumps(
        _grid_prog(parts, place).to_json(), sort_keys=True)
    assert fired(p, g)[0] == set()
    assert fired(p, d)[0] == set()


def test_per_site_is_refuted_by_broadcast_wiring():
    d = load(ROOT / "arch" / "deck_unit_cell.arch.json")
    parts, place = _four_hops(load(ROOT / "arch" / "grid9x9.arch.json"))
    rules, rep = fired(_grid_prog(parts, place, "per_site"), d)
    assert rules == {"R4c"}, rules
    assert "with one waveform" in str(rep.rules.violations[0])


def test_per_site_is_satisfied_by_direct_wiring():
    g = load(ROOT / "arch" / "grid9x9.arch.json")
    parts, place = _four_hops(g)
    assert fired(_grid_prog(parts, place, "per_site"), g)[0] == set()


# -------------------------------------------------------------------- optics

STATIONARY = ROOT / "arch" / "stationary_chain.arch.json"
TWO_ZONES = {"a": "C0", "b": "C0", "c": "C1", "d": "C1"}


def _optical(gate_kw, cool_broadcast=True):
    return TSIR(name="opt", arch_spec="inline", instructions=[
        Instruction(type="init", id=0, placement=dict(TWO_ZONES),
                    quanta={k: 0.0 for k in TWO_ZONES}),
        Instruction(type="gate", id=1, gate="MS", **gate_kw),
        Instruction(type="cool", id=2, broadcast=cool_broadcast)])


def test_a_steered_beam_cannot_light_two_zones_at_once():
    arch = load(STATIONARY)
    assert arch.control["optical"]["addressing"] == "steerable_raman"
    rules, rep = fired(_optical(
        {"pairs": (("a", "b"), ("c", "d")), "broadcast": "one"}), arch)
    assert rules == {"R4c"}, rules
    assert "steerable_raman" in str(rep.rules.violations[0])


def test_a_zone_that_cannot_opt_out_refutes_a_partial_broadcast():
    arch = load(STATIONARY)
    assert arch.control["optical"]["per_zone_switch"] is False
    rules, rep = fired(_optical({"pairs": (("a", "b"),), "broadcast": "one"}), arch)
    assert rules == {"R4c"}, rules
    assert "per_zone_switch=false" in str(rep.rules.violations[0])


def test_the_same_two_zone_broadcast_is_legal_on_a_global_beam_device():
    arch = load(ROOT / "arch" / "cyclone_base.arch.json")
    sites = [n.id for n in arch.device.nodes.values() if n.kind == "site"][:2]
    place = {"a": sites[0], "b": sites[0], "c": sites[1], "d": sites[1]}
    p = TSIR(name="opt", arch_spec="inline", instructions=[
        Instruction(type="init", id=0, placement=place,
                    quanta={k: 0.0 for k in place}),
        Instruction(type="gate", id=1, gate="MS",
                    pairs=(("a", "b"), ("c", "d")), broadcast="one"),
        Instruction(type="cool", id=2, broadcast=True)])
    assert fired(p, arch)[0] == set()


def test_a_broadcast_cool_is_judged_by_the_cool_primitive_not_by_the_raman_path():
    """Cooling is not steered.  `stationary_chain`'s own `primitives.cool` says
    `broadcastable: true, scope: "global"` -- "Doppler sheet beams cover the whole trap"
    -- which is R7c's premise, so `optical.addressing` must not judge it."""
    arch = load(STATIONARY)
    assert arch.primitives.scalar("cool")["broadcastable"] is True
    rules, _ = fired(_optical({"pairs": (("a", "b"), ("c", "d"))}), arch)
    assert rules == set(), rules


# ------------------------------------------------------------------ the field


def test_the_legacy_boolean_round_trips_byte_for_byte():
    """Every shipped `cool` carries `broadcast: true`; the golden oracle must not move."""
    i = Instruction(type="cool", id=1, broadcast=True)
    assert i.to_json()["broadcast"] is True
    assert Instruction.from_json(i.to_json()).broadcast is True
    assert broadcast_kind(i) == "one"
    assert "broadcast" not in Instruction(type="cool", id=1).to_json()


def test_a_string_claim_round_trips():
    for word in BROADCASTS:
        i = Instruction(type="simd", id=1, cls="shuttle", mode="inter",
                        participants=(Participant("a", "S0", "S1"),), broadcast=word)
        assert Instruction.from_json(i.to_json()).broadcast == word


def test_an_unknown_broadcast_word_is_refused():
    p = TSIR(name="x", arch_spec="inline", id_seq=2, instructions=[
        Instruction(type="init", id=0, placement={"a": "S0"}),
        Instruction(type="simd", id=1, cls="shuttle", mode="inter",
                    participants=(Participant("a", "S0", "S1"),),
                    broadcast="everywhere")])
    errs = validate_program(p)
    assert any("everywhere" in e for e in errs), errs


def test_init_and_barrier_may_not_claim_a_broadcast():
    """`init` and `barrier` build no CycleView (docs/notes.md 5.5), so a claim on them
    could never be checked -- and an unfalsifiable claim is the false green the repo's
    own contract forbids."""
    p = TSIR(name="x", arch_spec="inline", id_seq=2, instructions=[
        Instruction(type="init", id=0, placement={"a": "S0"}, broadcast="one"),
        Instruction(type="barrier", id=1, broadcast="one")])
    errs = validate_program(p)
    assert sum("cannot claim a broadcast" in e for e in errs) == 2, errs


def test_silence_is_not_a_pass():
    """A programme that claims nothing leaves R4c SKIPPED, never `passed`."""
    rep = verify(prog(init(PLACE8), rotate()), wired_ring(), MODEL,
                 check_metrics=False)
    summary = rep.summary()["rules"]
    assert "R4c" not in summary["passed"]
    assert "no instruction" in summary["skipped"]["R4c"]


def test_a_claim_that_was_checked_says_how_many():
    rep = verify(prog(init(PLACE8), rotate(broadcast="one")), wired_ring(), MODEL,
                 check_metrics=False)
    assert "R4c" in rep.summary()["rules"]["passed"]
    assert rep.rules.notes["R4c"].startswith("1 of 2 instruction(s)")


def test_the_builder_carries_the_claim_without_a_new_verb():
    """`PROGRAM_METHODS` is derived from `Program`, mirrored in engine.js as `PCALLS`,
    and `test_every_program_verb_is_exercised` asserts the corpus reaches every one.  A
    new VERB would break that on the spot; a keyword argument does not."""
    from qccd.api import PROGRAM_METHODS, Machine

    assert len(PROGRAM_METHODS) == 12, PROGRAM_METHODS
    m = Machine(load(ROOT / "arch" / "cyclone_base.arch.json"))
    p = m.program("b", provenance="off")
    p.fill("L0")
    p.rotate(1, broadcast="one")
    p.gate("MS", [("d0", "d1")], broadcast="one")
    p.cool()
    built = p.build().instructions
    assert [broadcast_kind(i) for i in built] == [None, "one", "one", "one"]


def test_the_listing_says_the_word():
    from qccd.ir.listing import disassemble, render

    arch = lab_ring()
    text = render(disassemble(prog(init(PLACE8), rotate(broadcast="per_direction")),
                              arch, model=MODEL))
    assert "broadcast(per_direction)" in text
    plain = render(disassemble(prog(init(PLACE8), rotate()), arch, model=MODEL))
    assert "broadcast(" not in plain
