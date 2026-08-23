"""Regressions for the defects an adversarial review of M0-M2 confirmed.

Each test is named for what was wrong, not for what is right, so a failure says which
defect came back. None of these changed any M0/M1/M2 acceptance number -- they are holes
in checks that were being *reported as passing*, plus latent bugs on paths the shipped
schedule does not exercise but the next milestone will.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import SCHEMA_VERSION, Architecture, load  # noqa: E402
from qccd.compile import CoolingPolicy, insert_cooling  # noqa: E402
from qccd.cost import Charge, corrected_model, deck_model, t2_metrics  # noqa: E402
from qccd.ir import TSIR, Instruction, Participant, loop_shift  # noqa: E402
from qccd.verify import ReplayError, replay, verify  # noqa: E402

from test_rules import PRIMS, ZONES, fired, init, prog_of, run, tiny_ring  # noqa: E402


# --------------------------------------------------------------- replay engine


def test_non_transport_heating_lands_on_real_ions_not_a_phantom():
    """`Charge.quanta` is keyed by COMPONENT everywhere; the non-transport branch used
    to read the keys as ion names, fabricating an ion called 'gate'."""
    arch = tiny_ring()

    class HotGate(type(corrected_model())):
        def gate(self, arch, gate, n_pairs):
            return Charge(cost=0.0, depth=1, us=25.0, quanta={"gate": 0.5})

    model = HotGate()
    prog = prog_of(
        init(d0="A0", a0="A0"),
        Instruction(type="gate", id=1, gate="CX", mode="intra",
                    pairs=(("d0", "a0"),), sites=("A0",)),
    )
    res = replay(prog, arch, model, check_rules=False)
    assert set(res.per_ion_quanta) == {"d0", "a0"}, "no fabricated ion"
    assert res.per_ion_quanta["d0"]["gate"] == pytest.approx(0.5)
    assert res.per_ion_quanta["a0"]["gate"] == pytest.approx(0.5)
    assert res.quanta_components["gate"] == pytest.approx(1.0)


def test_gate_heating_is_visible_to_r7():
    """The phantom-ion bug also made R7 and R16 blind to gate heating."""
    arch = tiny_ring()

    class HotGate(type(corrected_model())):
        def gate(self, arch, gate, n_pairs):
            return Charge(cost=0.0, depth=1, us=25.0, quanta={"gate": 0.6})

    model = HotGate()
    gates = [
        Instruction(type="gate", id=i, gate="CX", mode="intra",
                    pairs=(("d0", "a0"),), sites=("A0",))
        for i in range(1, 5)
    ]
    prog = prog_of(init(d0="A0", a0="A0"), *gates)
    report = verify(prog, arch, model, check_metrics=False)
    assert fired(report, "R7"), "gate heating must reach R7's budget"
    assert report.result.max_gate_quanta_seen > 1.0


def test_a_second_init_does_not_leak_occupancy():
    arch = tiny_ring()
    moved = prog_of(
        init(d0="S1", d1="S2"),
        Instruction(type="init", id=1, placement={"d0": "S5"}, quanta={"d0": 0.0}),
        Instruction(type="simd", id=2, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 1)),
    )
    equivalent = prog_of(
        init(d0="S5", d1="S2"),
        Instruction(type="simd", id=2, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 1)),
    )
    a = run(moved, arch)
    b = run(equivalent, arch)
    assert a.result.final_positions == b.result.final_positions
    assert a.rules.by_rule() == b.rules.by_rule() == {}


def test_entails_is_charged_once_per_move_not_once_per_segment():
    """A dock is one split plus one merge however long the spur is."""
    doc = {
        "name": "two_segment_spur",
        "schema_version": SCHEMA_VERSION,
        "geometry": {
            "generator": "explicit",
            "sites": [
                {"id": "S0", "pos": [0, 0], "kind": "site", "capacity": 2, "zone_type": "gatezone"},
                {"id": "M0", "pos": [0, 0.5], "kind": "site", "capacity": 2, "zone_type": "gatezone"},
                {"id": "A0", "pos": [0, 1], "kind": "site", "capacity": 2, "zone_type": "gatezone"},
            ],
            "segments": [
                {"id": "V0a", "ends": ["S0", "M0"], "capacity": 1},
                {"id": "V0b", "ends": ["M0", "A0"], "capacity": 1},
            ],
        },
        "zone_types": ZONES,
        "primitives": PRIMS,
        "control": {
            "model": "simd_classes",
            "max_simd_classes_per_cycle": 1,
            "classes": {"generator": "x_junction_grid", "count": 18, "extra": [
                {"id": "dock", "type": "shift", "orbit": "spurs", "entails": ["split", "merge"]},
            ]},
        },
        "heating": {"anomalous_rate_quanta_per_ms": 0.0},
    }
    arch = Architecture.from_json(doc)
    prog = prog_of(
        init(d0="S0"),
        Instruction(type="simd", id=1, cls="dock", mode="inter",
                    participants=(Participant("d0", "S0", "A0", via=("V0a", "V0b")),)),
    )
    res = replay(prog, arch, corrected_model(), check_rules=False)
    # one split (6.0) + one merge (6.0), not two of each
    assert res.quanta_components["split_merge"] == pytest.approx(12.0)
    assert res.quanta_components["shuttle"] == pytest.approx(0.2)  # two segments
    assert res.total_us == pytest.approx(80.0 + 80.0 + 5.0 + 5.0)


def test_r8_does_not_false_fire_on_a_multi_segment_via():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="dock", mode="inter",
                    participants=(Participant("d0", "S1", "A0", via=("E0", "V0")),)),
    )
    report = run(prog, arch)
    assert not fired(report, "R8"), report.rules.by_rule()
    assert report.result.final_positions["d0"] == "A0"


def test_a_zero_hop_rotation_still_has_its_claims_checked():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 0), cost=99999.0, steps=12345),
    )
    report = verify(prog, arch, deck_model())
    assert fired(report, "R9"), "a degenerate cycle must not wave its annotations through"


def test_measure_or_cool_naming_an_unplaced_ion_raises_replay_error():
    arch = tiny_ring()
    for instr in (
        Instruction(type="measure", id=1, ions=("d1",)),
        Instruction(type="cool", id=1, ions=("d1",)),
        Instruction(type="reset", id=1, ions=("d1",)),
    ):
        prog = prog_of(init(d0="S1"), instr)
        with pytest.raises(ReplayError, match="unplaced ion"):
            replay(prog, arch, corrected_model())


def test_r7_and_r16_read_the_same_nbar():
    """R7 used to test n-bar AFTER the gate's own anomalous heating while R16 evaluated
    the error from the value before it."""
    doc = {
        "name": "hot_background",
        "schema_version": SCHEMA_VERSION,
        "geometry": {"generator": "chain", "params": {"n": 3, "site_zone": "gatezone"}},
        "zone_types": ZONES,
        "primitives": PRIMS,
        "heating": {"anomalous_rate_quanta_per_ms": 40.0},
    }
    arch = Architecture.from_json(doc)
    prog = prog_of(
        init(d0="C0", a0="C0"),
        Instruction(type="measure", id=1, ions=("d0",)),
        Instruction(type="gate", id=2, gate="CX", mode="intra",
                    pairs=(("d0", "a0"),), sites=("C0",)),
    )
    report = verify(prog, arch, corrected_model(), check_metrics=False)
    msgs = fired(report, "R7")
    assert msgs, "the gate is over budget either way"
    seen = report.result.max_gate_quanta_seen
    assert f"{seen:.3f}" in msgs[0], f"R7 says {msgs[0]}, R16 used {seen}"


# --------------------------------------------------------------- reporting


def test_only_rules_cannot_license_a_rule_with_no_implementation():
    arch = tiny_ring()
    prog = prog_of(init(d0="S1"))
    report = verify(prog, arch, corrected_model(), only_rules=["R1", "R99"],
                    check_metrics=False)
    assert "R99" not in report.rules.passed()
    assert "R99" in report.rules.skipped
    assert "R1" in report.rules.passed()


def test_keep_cycles_false_does_not_produce_a_green_r9():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 1), cost=99999.0, steps=12345),
    )
    strict = verify(prog, arch, deck_model())
    assert fired(strict, "R9")
    lean = verify(prog, arch, deck_model(), keep_cycles=False)
    assert "R9" in lean.rules.partial, "a check that did not run must not read as passed"
    assert lean.metrics["instructions_annotated"] == 1
    assert lean.metrics["instructions_checked"] == 0


def test_r9_checks_the_time_and_quanta_claims_it_says_it_does():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 1), t0=0.0, t1=1e9),
    )
    prog.metrics = {"runtime_us": 1e9, "total_quanta": -5.0, "cooling_us": 7.7e7}
    report = verify(prog, arch, corrected_model())
    msgs = " ".join(fired(report, "R9"))
    for label in ("runtime_us", "total_quanta", "cooling_us", "t1"):
        assert label in msgs, f"R9 never checked {label}"


def test_r9_names_the_annotations_it_cannot_check():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 1),
                    quanta_delta={"d0": -12345.0},
                    operating_point={"us": 999, "quanta": 999}),
    )
    report = verify(prog, arch, corrected_model())
    assert set(report.metrics["not_checked"]) == {"quanta_delta", "operating_point"}
    assert "R9" in report.rules.partial


def test_r7c_fires_on_gates_with_no_cooling_under_a_heating_model():
    arch = tiny_ring()
    prog = prog_of(
        init(d0="A0", a0="A0"),
        Instruction(type="gate", id=1, gate="CX", mode="intra",
                    pairs=(("d0", "a0"),), sites=("A0",)),
    )
    report = verify(prog, arch, corrected_model(), check_metrics=False)
    assert fired(report, "R7c"), "R7c must be in a bucket, and must be able to fire"
    cooled = prog_of(
        init(d0="A0", a0="A0"),
        Instruction(type="cool", id=1, broadcast=True),
        Instruction(type="gate", id=2, gate="CX", mode="intra",
                    pairs=(("d0", "a0"),), sites=("A0",)),
    )
    assert not fired(verify(cooled, arch, corrected_model(), check_metrics=False), "R7c")


def test_r6_and_r13_read_replayed_positions_not_the_sites_annotation():
    quiet = tiny_ring(zone="quiet")
    prog = prog_of(
        init(d0="S1", d1="S1"),
        Instruction(type="gate", id=1, gate="CX", mode="intra", pairs=(("d0", "d1"),)),
    )
    assert fired(run(prog, quiet), "R6"), "omitting `sites` must not disable R6"

    big = tiny_ring(zone="big")
    ions = {f"d{i}": "S1" for i in range(17)}
    crowded = prog_of(
        init(**ions),
        Instruction(type="gate", id=1, gate="CX", mode="intra", pairs=(("d0", "d1"),)),
    )
    assert fired(run(crowded, big), "R13"), "omitting `sites` must not disable R13"


def test_r7_survives_an_architecture_with_no_ms_gate():
    prims = {k: v for k, v in PRIMS.items() if k != "ms_gate"}
    arch = Architecture.from_json({
        "name": "no_ms_gate",
        "schema_version": SCHEMA_VERSION,
        "geometry": {"generator": "chain", "params": {"n": 3, "site_zone": "gatezone"}},
        "zone_types": ZONES,
        "primitives": prims,
    })
    prog = prog_of(
        init(d0="C0", a0="C0"),
        Instruction(type="gate", id=1, gate="CX", mode="intra",
                    pairs=(("d0", "a0"),), sites=("C0",)),
    )
    report = verify(prog, arch, deck_model(), check_metrics=False)
    assert not fired(report, "R7")  # an undeclared budget is unbounded, not a crash


# --------------------------------------------------------------- cost / T2


def test_t2_reports_wall_clock_by_class():
    arch = load(ROOT / "arch" / "ring144_24v.arch.json")
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 2)),
    )
    res = replay(prog, arch, corrected_model(), check_rules=False)
    t2 = t2_metrics(arch, res, corrected_model())
    assert t2.us_by_class, "the per-class wall clock is the T2 tier's whole promise"
    assert t2.as_dict()["us_by_class"]["rotate_cw"] == pytest.approx(res.total_us)


def test_counterfactual_survives_keep_cycles_false_and_honours_corner_hops():
    arch = load(ROOT / "arch" / "ring144_24v.arch.json")
    prog = prog_of(
        init(d0="S1"),
        Instruction(type="simd", id=1, cls="rotate_cw", mode="inter",
                    template=loop_shift("L0", 4)),
    )
    lean = replay(prog, arch, corrected_model(), check_rules=False, keep_cycles=False)
    t2 = t2_metrics(arch, lean, corrected_model())
    assert t2.counterfactual_rotation_us == pytest.approx(4 * 5.0)
    # a model that does not model time has no counterfactual, rather than a bogus one
    deck_res = replay(prog, arch, deck_model(), check_rules=False)
    assert t2_metrics(arch, deck_res, deck_model()).counterfactual_rotation_us == 0.0


def test_neg_log_fidelity_carries_the_spam_term():
    arch = load(ROOT / "arch" / "ring144_24v.arch.json")
    base = prog_of(init(d0="A0", a0="A0"))
    with_spam = prog_of(
        init(d0="A0", a0="A0"),
        Instruction(type="measure", id=1, ions=("a0",)),
        Instruction(type="reset", id=2, ions=("a0",)),
    )
    model = corrected_model()
    t_base = t2_metrics(arch, replay(base, arch, model, check_rules=False), model)
    t_spam = t2_metrics(arch, replay(with_spam, arch, model, check_rules=False), model)
    expected = (1 - 0.9984) + 5e-3
    assert t_spam.spam_error == pytest.approx(expected)
    assert t_spam.neg_log_fidelity - t_base.neg_log_fidelity == pytest.approx(
        expected, rel=1e-3
    )


# --------------------------------------------------------------- cooling


def test_the_r7_budget_knob_actually_changes_the_schedule():
    arch = tiny_ring()
    model = corrected_model()
    prog = prog_of(
        init(d0="S1", a0="A0"),
        Instruction(type="simd", id=1, cls="nudge", mode="inter",
                    participants=(Participant("d0", "S1", "S0"),)),
        Instruction(type="simd", id=2, cls="dock", mode="inter",
                    participants=(Participant("d0", "S0", "A0", via=("V0",)),)),
        Instruction(type="gate", id=3, gate="CX", mode="intra",
                    pairs=(("d0", "a0"),), sites=("A0",)),
    )
    strict = insert_cooling(prog, arch, model, policy=CoolingPolicy(max_gate_quanta=1.0))
    loose = insert_cooling(prog, arch, model, policy=CoolingPolicy(max_gate_quanta=1e9))
    assert strict.n_cools == 1
    assert loose.n_cools == 0, "an unbounded budget must need no cooling"
    assert loose.r7_violations_before == 0
    assert strict.budget == 1.0 and loose.budget == 1e9


def test_peak_between_cools_is_not_reported_as_zero_when_nothing_was_cooled():
    arch = tiny_ring()
    model = corrected_model()
    prog = prog_of(
        init(d0="S1", a0="A0"),
        Instruction(type="simd", id=1, cls="nudge", mode="inter",
                    participants=(Participant("d0", "S1", "S0"),)),
    )
    result = insert_cooling(prog, arch, model, policy=CoolingPolicy(max_gate_quanta=1e9))
    assert result.n_cools == 0
    assert result.peak_quanta_between_cools > 0.0, (
        "the diagnostic must not read 'safe' precisely in the uncooled case"
    )


def test_ion_loss_cap_is_enforced_or_reported_unfixable():
    arch = tiny_ring()
    model = corrected_model()
    prog = prog_of(
        init(d0="S1", a0="A0"),
        *[
            Instruction(type="simd", id=i, cls="nudge", mode="inter",
                        participants=(
                            Participant("d0", "S1", "S0") if i % 2 else
                            Participant("d0", "S0", "S1"),
                        ))
            for i in range(1, 9)
        ],
    )
    ok = insert_cooling(prog, arch, model,
                        policy=CoolingPolicy(max_quanta_between_cools=6.0))
    assert ok.peak_quanta_between_cools <= 6.0 or ok.unfixable

    # a cap below a single junction crossing cannot be met by any cooling schedule
    impossible = insert_cooling(prog, arch, model,
                                policy=CoolingPolicy(max_quanta_between_cools=0.001))
    assert impossible.unfixable, "an unattainable cap must be named, not silently 'met'"
    assert any("no cooling schedule can satisfy" in n for n in impossible.notes)


def test_non_broadcast_cooling_produces_a_replayable_program():
    arch = tiny_ring()
    model = corrected_model()
    prog = prog_of(
        init(d0="S1", a0="A0"),
        Instruction(type="simd", id=1, cls="nudge", mode="inter",
                    participants=(Participant("d0", "S1", "S0"),)),
        Instruction(type="simd", id=2, cls="dock", mode="inter",
                    participants=(Participant("d0", "S0", "A0", via=("V0",)),)),
        Instruction(type="gate", id=3, gate="CX", mode="intra",
                    pairs=(("d0", "a0"),), sites=("A0",)),
    )
    result = insert_cooling(prog, arch, model, policy=CoolingPolicy(broadcast=False))
    assert result.n_cools >= 1
    assert result.r7_violations_after == 0
    cool = next(i for i in result.program.instructions if i.type == "cool")
    assert cool.ions and not cool.broadcast


def test_cooling_a_zero_duration_program_does_not_crash():
    arch = tiny_ring()
    prog = prog_of(init(d0="S1"), Instruction(type="barrier", id=1))
    result = insert_cooling(prog, arch, corrected_model())
    assert result.n_cools == 0
    assert result.cooling_share == 0.0


# --------------------------------------------------------------- arch


def test_compact_serialization_refuses_to_discard_a_modified_graph():
    arch = load(ROOT / "arch" / "ring144_24v.arch.json")
    assert arch.device.reproducible_from_generator()
    doc = arch.to_json(expanded=True)
    doc["geometry"]["nodes"] = [s for s in doc["geometry"]["nodes"] if s["id"] != "A6"]
    doc["geometry"]["segments"] = [
        s for s in doc["geometry"]["segments"] if s["id"] != "V6"
    ]
    for site in doc["geometry"]["nodes"]:
        if site["id"] == "S6":
            site["degree"] = 2
    modified = Architecture.from_json(doc)
    assert not modified.device.reproducible_from_generator()
    # asking for the compact form must not hand back the pristine generator output
    round_tripped = Architecture.from_json(modified.to_json(expanded=False))
    assert len(round_tripped.device.nodes) == len(modified.device.nodes) == 167
    assert round_tripped.device.summary()["n_junction_nodes"] == 23


# --------------------------------------------------------------- importer


def test_completeness_checks_the_member_set_not_just_the_count():
    """Six distinct member indices per check is satisfied by six contacts on the wrong
    ions; the contacted member set has to equal the check's declared members."""
    from qccd.ir import completeness_report, extract_inline_data, import_schedule

    html = ROOT / "visualizer_24_ancillas_24_junctions_standalone.html"
    if not html.exists():
        pytest.skip(f"{html.name} is not present; it is third-party material and untracked")
    arch = load(ROOT / "arch" / "ring144_24v.arch.json")
    prog = import_schedule(arch, extract_inline_data(html))
    assert completeness_report(prog)["complete"]

    # relabel two contacts between checks: every count, and every replayed position,
    # is untouched
    exps = [dict(e) for e in prog.meta["contact_expectations"]]
    i, j = 0, next(k for k, e in enumerate(exps) if e["check"] != exps[0]["check"])
    exps[i]["check"], exps[j]["check"] = exps[j]["check"], exps[i]["check"]
    swapped = TSIR(name=prog.name, arch_spec=prog.arch_spec,
                   instructions=prog.instructions, metrics=prog.metrics,
                   meta=dict(prog.meta, contact_expectations=exps))
    report = completeness_report(swapped)
    assert not report["complete"]
    assert len(report["checks_with_wrong_members"]) == 2
    assert report["checks_complete_6_of_6"] == 144  # the count alone is still fooled


# ------------------------------------------------- `qccd regen` emitted a DIFFERENT page

def test_regen_emits_the_same_studio_that_the_studio_command_does(tmp_path):
    """`qccd regen` exists so that no emitted page is ever left stale or divergent -- and
    it was itself the divergence. It built the studio's argument namespace by restating
    that command's defaults in a literal list, and two of them were wrong: `all_templates`
    became None where the parser says True, and `table` became None where the parser says
    'qccdsim_jones'. The page it wrote was 49 KB smaller, with a different start gallery
    and a different cost model -- and `out/studio.html` is the page other tests read, so
    the divergence was being inherited rather than caught.

    The fix is to ask the parser instead of restating it, which this asserts by building
    both pages and comparing bytes.
    """
    import subprocess

    a = tmp_path / "via_studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(a)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)

    from qccd.__main__ import build_parser
    ns = build_parser().parse_args(["studio", "-o", str(tmp_path / "via_regen.html"),
                                    "--max-frames", "20000"])
    direct = build_parser().parse_args(["studio", "-o", str(a), "--max-frames", "20000"])
    for k, v in vars(direct).items():
        if k == "out":
            continue
        assert getattr(ns, k) == v, (
            f"regen would build the studio with {k}={getattr(ns, k)!r} where "
            f"`qccd studio` uses {v!r}")

    b = tmp_path / "via_regen.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(b),
                    "--max-frames", "20000"], cwd=ROOT, capture_output=True,
                   timeout=900, check=True)
    assert a.read_bytes() == b.read_bytes(), (
        f"the two paths emit different pages: {a.stat().st_size} vs {b.stat().st_size}")
