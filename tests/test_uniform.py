"""The uniformising pass: one transport cycle, one waveform (R22).

The fixtures are deliberately tiny and hand-written.  A pass that splits a cycle is only
worth anything if the programme still *means* the same thing afterwards, so every test
here checks the finishing positions as well as the rules -- a pass that dropped a
participant would pass a rule check and fail this.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.api import Machine  # noqa: E402
from qccd.compile.uniform import make_uniform, signatures  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.ir.tsir import validate_program  # noqa: E402
from qccd.verify import verify  # noqa: E402

MODEL = corrected_model("qccdsim_jones")


def _verify(prog, arch):
    return verify(prog, arch, MODEL, check_metrics=False)


def mixed_program():
    """A 2x3 grid and one cycle that moves two ions in opposite directions.

    `T0_0v` and `T0_1v` are the two vertical traps of column 0, `T1_0v` and `T1_1v` of
    column 1, so `a` walks north up one column while `b` walks south down the other:
    one time slot, two waveforms.
    """
    m = Machine.grid(2, 3)
    p = m.program("mixed", provenance="off")
    p.init({"a": "T0_0v", "b": "T1_1v"})
    p.simd("shuttle", [
        ("a", "T0_0v", "T0_1v", ["T0_0v.b", "T0_1v.a"]),
        ("b", "T1_1v", "T1_0v", ["T1_1v.a", "T1_0v.b"]),
    ])
    return m, p.build()


# --------------------------------------------------------------- the basic case


def test_r22_fires_before_the_pass_and_not_after():
    m, prog = mixed_program()
    before = _verify(prog, m.arch)
    assert "R22" in before.rules.failed()

    r = make_uniform(prog, m.arch)
    assert r.changed
    after = _verify(r.program, m.arch)
    assert "R22" not in after.rules.failed()
    assert after.rules.failed() == set(), sorted(after.rules.failed())


def test_the_split_buys_exactly_one_cycle_per_waveform():
    m, prog = mixed_program()
    assert signatures(m.arch, prog.instructions[1]) == {
        "a": "shuttle +y,+y", "b": "shuttle -y,-y"}

    r = make_uniform(prog, m.arch)
    assert (r.cycles_before, r.cycles_after) == (1, 2)
    assert len(r.splits) == 1
    assert sorted(r.splits[0].signatures) == ["shuttle +y,+y", "shuttle -y,-y"]
    # every cycle of the result is one waveform, by the verifier's own function
    for instr in r.program.instructions:
        if instr.type == "simd" and instr.participants:
            assert len(set(signatures(m.arch, instr).values())) == 1


def test_the_programme_still_means_the_same_thing():
    m, prog = mixed_program()
    before = _verify(prog, m.arch)
    after = _verify(make_uniform(prog, m.arch).program, m.arch)
    assert before.result.final_positions == after.result.final_positions
    assert before.result.final_positions == {"a": "T0_1v", "b": "T1_0v"}
    # the split costs time -- that is the point of R22, not a regression
    assert after.result.total_us > before.result.total_us


def test_the_result_is_a_structurally_valid_programme():
    m, prog = mixed_program()
    r = make_uniform(prog, m.arch)
    assert validate_program(r.program) == []
    ids = [i.id for i in r.program.instructions]
    assert ids == list(range(len(ids)))          # renumbered contiguously
    assert r.program.id_seq == len(ids)


def test_the_id_map_names_the_instructions_each_old_one_became():
    m, prog = mixed_program()
    r = make_uniform(prog, m.arch)
    assert set(r.id_map) == {i.id for i in prog.instructions}
    assert r.id_map[0] == [0]                     # the init is untouched
    assert r.id_map[1] == [1, 2]                  # the cycle became two
    by_id = {i.id: i for i in r.program.instructions}
    for old, new in r.id_map.items():
        for nid in new:
            assert by_id[nid].type == prog.instructions[old].type


def test_the_pass_is_idempotent():
    m, prog = mixed_program()
    once = make_uniform(prog, m.arch)
    twice = make_uniform(once.program, m.arch)
    assert not twice.changed
    assert twice.program.to_json() == once.program.to_json()
    assert twice.id_map == {i.id: [i.id] for i in once.program.instructions}


# --------------------------------------------------------------- already uniform


def test_a_rigid_rotation_is_returned_unchanged():
    """A `loop_shift` template is one waveform by construction, so there is nothing to
    buy and the pass must not touch the programme -- ids included."""
    m = Machine.ring(6, 2, verticals=0)
    p = m.program("spin", provenance="off")
    p.fill()
    p.rotate(1)
    p.rotate(-1)
    prog = p.build()
    assert "R22" not in _verify(prog, m.arch).rules.failed()

    before = prog.to_json()
    r = make_uniform(prog, m.arch)
    assert not r.changed
    assert r.program is prog
    assert r.program.to_json() == before
    assert (r.cycles_before, r.cycles_after) == (2, 2)
    assert r.id_map == {i.id: [i.id] for i in prog.instructions}


def test_a_cycle_whose_ions_all_do_the_same_thing_is_left_alone():
    m = Machine.grid(2, 3)
    p = m.program("lockstep", provenance="off")
    p.init({"a": "T0_0v", "b": "T1_0v"})
    p.simd("shuttle", [
        ("a", "T0_0v", "T0_1v", ["T0_0v.b", "T0_1v.a"]),
        ("b", "T1_0v", "T1_1v", ["T1_0v.b", "T1_1v.a"]),
    ])
    prog = p.build()
    assert "R22" not in _verify(prog, m.arch).rules.failed()
    r = make_uniform(prog, m.arch)
    assert not r.changed and r.program is prog


# --------------------------------------------------------------- ordering


def test_a_group_waits_for_the_group_that_vacates_its_destination():
    """`b` leaves `T0_1v`; `a` is moving into it.  Issue them the other way round and a
    programme that was legal stops being legal, so the dependency -- not the signature
    order -- has to decide."""
    m = Machine.grid(2, 3)
    p = m.program("convoy", provenance="off")
    p.init({"a": "T0_0v", "b": "T0_1v"})
    p.simd("shuttle", [
        ("a", "T0_0v", "T0_1v", ["T0_0v.b", "T0_1v.a"]),        # +y,+y
        ("b", "T0_1v", "T0_1h", ["T0_1v.a", "T0_1h.a"]),        # -y,+x
    ])
    prog = p.build()
    r = make_uniform(prog, m.arch)
    assert r.changed and not r.dependency_cycles
    # sorted signature order would put "+y,+y" first ('+' sorts before '-'), which is
    # exactly the wrong way round: the vacating group has to go first
    assert r.splits[0].signatures == ("shuttle -y,+x", "shuttle +y,+y")
    moved = [i for i in r.program.instructions if i.type == "simd"]
    assert [pp.ion for i in moved for pp in i.participants] == ["b", "a"]
    assert _verify(r.program, m.arch).result.final_positions == {
        "a": "T0_1v", "b": "T0_1h"}


def test_two_ions_exchanging_sites_have_no_order_and_the_report_says_so():
    m = Machine.grid(2, 3)
    p = m.program("swap", provenance="off")
    p.init({"a": "T0_0v", "b": "T0_1v"})
    p.simd("shuttle", [
        ("a", "T0_0v", "T0_1v", ["T0_0v.b", "T0_1v.a"]),
        ("b", "T0_1v", "T0_0v", ["T0_1v.a", "T0_0v.b"]),
    ])
    prog = p.build()
    r = make_uniform(prog, m.arch)
    assert r.dependency_cycles == [1]
    assert r.splits[0].dependency_cycle
    assert r.splits[0].signatures == ("shuttle +y,+y", "shuttle -y,-y")  # sorted
    assert any("vacate-before-arrive" in n for n in r.notes)


# --------------------------------------------------------------- claims


def test_claims_the_split_invalidates_are_dropped_rather_than_left_to_lie():
    m, prog = mixed_program()
    replayed = _verify(prog, m.arch).result
    prog.metrics.update(total_cost=replayed.total_cost, runtime_us=replayed.total_us,
                        contacts=0)
    r = make_uniform(prog, m.arch)
    assert "total_cost" not in r.program.metrics
    assert "runtime_us" not in r.program.metrics
    assert r.program.metrics["contacts"] == 0     # a split changes no contact
    # and what survives is true: R9 has nothing to complain about
    rep = verify(r.program, m.arch, MODEL, check_metrics=True)
    assert "R9" not in rep.rules.failed()


def test_the_split_pieces_keep_the_provenance_of_the_cycle_they_came_from():
    m = Machine.grid(2, 3)
    p = m.program("stamped", provenance="off")
    p.init({"a": "T0_0v", "b": "T1_1v"})
    p.simd("shuttle", [
        ("a", "T0_0v", "T0_1v", ["T0_0v.b", "T0_1v.a"]),
        ("b", "T1_1v", "T1_0v", ["T1_1v.a", "T1_0v.b"]),
    ], op=[3, 7], kind="route")
    prog = p.build()
    r = make_uniform(prog, m.arch)
    pieces = [i for i in r.program.instructions if i.type == "simd"]
    assert len(pieces) == 2
    for piece in pieces:
        assert piece.meta["op"] == [3, 7]
        assert piece.meta["kind"] == "route"
        assert piece.meta["split_of"] == 1
        assert piece.cls == "shuttle" and piece.mode == "inter"
    assert {piece.meta["waveform"] for piece in pieces} == {
        "shuttle +y,+y", "shuttle -y,-y"}


# --------------------------------------------------------------- the bridge


def test_the_bridge_rewrites_the_certificate_through_the_id_map(tmp_path):
    m = Machine.grid(2, 3)
    arch_file = m.save(tmp_path / "grid.arch.json")

    # a gate AFTER the cycle that splits, so its handle really does move
    p = m.program("certified", provenance="off")
    p.init({"a": "T0_0v", "b": "T1_1v"})
    p.simd("shuttle", [
        ("a", "T0_0v", "T0_1v", ["T0_0v.b", "T0_1v.a"]),
        ("b", "T1_1v", "T1_0v", ["T1_1v.a", "T1_0v.b"]),
    ])
    p.gate("MS", [("a", "b")], sites=["T0_1v"])
    prog = p.build()
    tsir = tmp_path / "prog.tsir.json"
    prog.save(tsir, indent=None)

    cert = {"circuit": "x", "arch": m.arch.name, "n_qubits": 2,
            "map": {"0": "a", "1": "b"},
            "init": {"a": "T0_0v", "b": "T1_1v"}, "circuit_ops": [], "moves": [],
            "rotations": [], "unrealised": [],
            "gates": [{"dag": 0, "instr": 2, "t": 2, "site": "T0_1v",
                       "ions": ["a", "b"], "pulses": []}]}
    cert_in = tmp_path / "prog.qcert.json"
    cert_in.write_text(json.dumps(cert), encoding="utf-8")

    out = tmp_path / "u.tsir.json"
    cert_out = tmp_path / "u.qcert.json"
    r = subprocess.run(
        [sys.executable, str(ROOT / "Compiler" / "bridge" / "make_uniform.py"),
         str(tsir), "--arch", str(arch_file),
         "-o", str(out), "--cert", str(cert_in), "--cert-out", str(cert_out)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 -> 2 transport cycles" in r.stdout

    got = json.loads(cert_out.read_text(encoding="utf-8"))
    # instruction 1 became 1 and 2, so the gate that was 2 is now 3
    assert got["gates"][0]["instr"] == 3
    assert got["gates"][0]["t"] == 2              # a cycle ordinal, not a handle
    assert got["moves"] == [] and got["circuit_ops"] == []


if __name__ == "__main__":       # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
