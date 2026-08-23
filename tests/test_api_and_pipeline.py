"""The Python surface and the compilation pipeline.

Two claims are under test here. First, that a machine can be described and programmed
entirely in Python and the result is the *same kind of object* the importer and the
compiler produce -- not a parallel path with its own semantics. Second, that a code goes
in one end of the pipeline and a verified program comes out the other.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.codes import gross_code  # noqa: E402
from qccd.compile import CompilePolicy, build, compile_code  # noqa: E402
from qccd.cost import deck_model  # noqa: E402
from qccd.ir import TSIR, extract_inline_data  # noqa: E402
from qccd.verify import ReplayError  # noqa: E402

HTML = ROOT / "visualizer_24_ancillas_24_junctions_standalone.html"


# --------------------------------------------------------------- machine setup


def test_a_machine_is_built_and_retuned_from_python():
    m = Machine.ring(width=12, height=2, verticals=4, name="tiny")
    assert m.summary()["n_junction_nodes"] == 4
    assert len(m.sites("dock")) == 4

    m.set_zone("data", capacity=5)
    assert m.arch.zone_types["data"]["capacity"] == 5
    assert m.device.nodes["S0"].capacity == 5, "retuning a zone reaches the sites"

    m.set_primitive("ms_gate", max_quanta=7.0)
    assert m.arch.primitives.scalar("ms_gate")["max_quanta"] == 7.0

    m.set_curve("shuttle_segment", [(9.0, 0.25)], table="local")
    pt = m.arch.primitives.curve("shuttle_segment").points[0]
    assert (pt.us, pt.quanta, pt.table) == (9.0, 0.25, "local")

    m.set_wiring(scheme="direct")
    assert m.resources().scheme == "direct"


def test_every_generator_is_reachable_from_python():
    made = [
        Machine.ring(10, 2, 2), Machine.grid(4, 4), Machine.chain(6),
        Machine.ladder(8, rungs=2, highways=2), Machine.racetrack(6),
        Machine.dual_loop(6),
    ]
    for m in made:
        assert m.summary()["n_nodes"] > 1
        assert not m.device.check_structure()


def test_a_machine_round_trips_through_a_file(tmp_path):
    m = Machine.ring(12, 2, 4, name="rt")
    path = m.save(tmp_path / "rt.arch.json")
    again = Machine.load(path)
    assert again.summary() == m.summary()


# --------------------------------------------------------------- programming


def ring_machine() -> Machine:
    return Machine.load(ROOT / "arch" / "ring144_24v.arch.json")


def test_a_hand_written_program_verifies():
    m = ring_machine()
    p = m.program("hand")
    p.init({**{f"d{i}": f"S{i}" for i in range(144)},
            **{f"a{s}": f"A{s}" for s in range(0, 144, 6)}})
    p.rotate(+6)
    with p.cycle("dock") as c:
        c.move("d114", "S120", "A120", via=["V120"])
    p.cool()                      # R7: the junction crossing put the ion over budget
    p.gate("CX", [("d114", "a120")], sites=["A120"])
    with p.cycle("undock") as c:
        c.move("d114", "A120", "S120", via=["V120"])
    r = m.run(p)
    assert r.ok, r.rules_failed
    assert r.steps > 0 and r.runtime_ms > 0


def test_the_api_refuses_programs_the_hardware_cannot_run():
    m = ring_machine()
    with pytest.raises(ValueError, match="intra"):
        m.program("x").cycle("dock", mode="sideways")
    with pytest.raises(ValueError, match="no such node"):
        m.program("x").init({"d0": "S99999"})
    with pytest.raises(ValueError, match="open"):
        Machine.chain(8).program("x").fill().rotate(1)
    with pytest.raises(ValueError, match="moves nothing"):
        with m.program("x").init({"d0": "S0"}).cycle("dock"):
            pass


def test_a_move_that_contradicts_the_state_is_caught():
    m = ring_machine()
    p = m.program("x").init({"d0": "S0"}).rotate(+1)
    p.move("d0", "S0", "A0", via=["V0"])
    with pytest.raises(ReplayError, match="declared at"):
        m.run(p)


def test_a_python_program_is_the_same_object_as_an_imported_one():
    """No parallel path: a hand-built program and the artifact's are one type, judged by
    one verifier, and both round-trip through the same JSON."""
    m = ring_machine()
    hand = m.program("hand").init({"d0": "S0"}).rotate(+1).build()
    imported = build(m.arch, "deck", html_path=HTML)
    assert isinstance(hand, TSIR) and isinstance(imported, TSIR)
    for prog in (hand, imported):
        assert TSIR.from_json(prog.to_json()).to_json() == prog.to_json()


def test_run_result_exposes_the_verifier_not_a_summary_of_it():
    m = ring_machine()
    r = m.run(build(m.arch, "deck", html_path=HTML), model="deck")
    assert r.cost == 397184 and r.steps == 8808
    assert r.ok and "R9" in r.rules_passed
    assert r.t1().contact_batch_utilization == pytest.approx(2.18, abs=0.005)


def test_render_from_the_api(tmp_path):
    m = ring_machine()
    out = m.render(m.program("x").init({"d0": "S0"}).rotate(+3), tmp_path / "x.html")
    assert out.exists() and out.stat().st_size > 4000


# --------------------------------------------------------------- the code layer


def test_our_bb_code_is_the_artifacts_code():
    """External oracle: the gross code we generate has to be the one the shipped
    schedule measures, check for check."""
    code = gross_code()
    theirs = extract_inline_data(HTML)["geometries"][0]["checks"]
    ours = Counter(frozenset(c.members) for c in code.checks)
    them = Counter(frozenset(int(x) - 1 for x in c["members"]) for c in theirs)
    assert ours == them


def test_the_code_satisfies_css_commutation():
    code = gross_code()
    xs = [set(c.members) for c in code.checks if c.type == "X"]
    zs = [set(c.members) for c in code.checks if c.type == "Z"]
    assert xs and zs
    assert not [1 for x in xs for z in zs if len(x & z) % 2]


def test_the_code_shape_matches_the_gross_code():
    s = gross_code().summary()
    assert s["n"] == 144 and s["checks"] == 144
    assert s["check_weight"] == [6] and s["contacts"] == 864
    assert s["contacts_per_data_qubit"] == [6]


# --------------------------------------------------------------- the pipeline


@pytest.fixture(scope="module")
def compiled():
    m = ring_machine()
    return m, compile_code(m.arch, gross_code(),
                           policy=CompilePolicy(insert_cooling=False))


def test_the_pipeline_runs_every_plan_pass(compiled):
    _, r = compiled
    names = [p.name.split(maxsplit=1)[1] for p in r.passes]
    assert names == ["place", "order", "route", "simd", "opoint", "cooling", "schedule"]


def test_the_compiled_program_realizes_every_contact(compiled):
    m, r = compiled
    assert r.contacts == 864
    res = m.run(r.program, check_metrics=False).report.result
    assert res.n_gate_pairs == 864
    per_ion = Counter()
    for instr in r.program.instructions:
        for a, b in instr.pairs:
            per_ion[a] += 1
    assert set(per_ion.values()) == {6}, "every data ion takes part in six contacts"


def test_the_compiled_program_obeys_the_ancilla_reuse_rule(compiled):
    """A dock serves one check at a time, and a wave completes before the next starts.

    Pooling the waves cuts the hop count by more than 4x -- which is why the discipline
    has to be tested, not assumed.
    """
    _, r = compiled
    binding = r.binding
    wave_of_batch: dict[int, set] = {}
    for instr in r.program.instructions:
        if instr.type != "gate":
            continue
        checks = instr.meta.get("checks", [])
        docks = [binding[c] for c in checks]
        assert len(docks) == len(set(docks)), "one dock cannot serve two checks at once"
        wave_of_batch.setdefault(instr.meta["batch"], set()).update(checks)


def test_the_compiled_program_is_rule_legal_once_cooling_is_inserted():
    m = ring_machine()
    r = compile_code(m.arch, gross_code(), policy=CompilePolicy(insert_cooling=True))
    out = m.run(r.program, check_metrics=False)
    assert out.ok, out.rules_failed


def test_the_compiler_is_measured_against_the_shipped_schedule(compiled):
    """The shipped hand-made schedule is the oracle a compiler has to be scored against.

    Not an equality: the compiler is free to be better. But it must realize the same 864
    contacts, and its hop count has to be reported next to 2672 rather than on its own.
    """
    _, r = compiled
    assert r.contacts == 864
    assert 0 < r.hops < 2672, f"{r.hops} hops against the shipped 2672"
    assert r.batches < 396


def test_policy_changes_the_schedule_and_nothing_else(compiled):
    """Swapping one pass must move the schedule and leave the semantics alone.

    That is the whole reason the pipeline is a list of named passes: PLAN §7.1 records
    that a mismatched policy makes any architecture look bad, so a comparison has to be
    able to hold six passes fixed and vary the seventh.
    """
    m, base = compiled
    other = compile_code(m.arch, gross_code(),
                         policy=CompilePolicy(insert_cooling=False,
                                              ancilla_binding="fixed"))
    assert other.contacts == base.contacts == 864, "same semantics"
    assert other.hops > base.hops, "the binding policy has to matter"


def test_compile_from_the_machine_facade():
    m = ring_machine()
    r = m.compile(gross_code(), policy=CompilePolicy(insert_cooling=False))
    assert r.contacts == 864


def test_a_loop_too_small_for_the_code_is_refused():
    m = Machine.load(ROOT / "arch" / "cyclone_base.arch.json")
    with pytest.raises(ValueError, match="loop slots but the code needs"):
        compile_code(m.arch, gross_code())


def test_a_device_without_docks_cannot_bind_ancillas():
    """Enough slots, but nowhere to put an ancilla: the order pass has to say so."""
    m = Machine.ring(72, 2, 0, name="no_docks")
    with pytest.raises(ValueError, match="dock"):
        compile_code(m.arch, gross_code())
