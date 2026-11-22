"""QCCDCert, the verified compiler at the core of compilation (qccd/cert/).

Without the toolchain these tests read the committed compiler output in
qccd/site/compiled/cert/ and check that it is the paper's (the numbers and listings of the
tutorial appendix of "QCCDCert: Formally Verified Compilation for QCCD Co-Design"), that the
page's displays agree with the compiler's acceptance, and that the circuit map joins every
instruction to its source statement.  With the toolchain (QCCD_CERT or ../ISCA27ArtifactQCCD)
they also recompile and compare, and run the workspace engine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qccd.cert import frames as F
from qccd.cert.examples import OUT, examples, read
from qccd.cert.toolchain import available

PAPER_EX1_TIMED = """init q0@S,q1@G;
transport q0:S->G[p0];                 // t=0, d=2
idle 1;                                // t=2, d=1
idle 1;                                // t=3, d=1
idle 1;                                // t=4, d=1
pulse R(pi/2,pi/2) q0 @G via c3;       // t=5, d=2
pulse MS(pi/2) q0,q1 @G via c4;        // t=7, d=4
broadcast R(-pi/2,0) q0,q1 @G via b0;  // t=11, d=2
pulse R(-pi/2,pi/2) q0 @G via c6;      // t=13, d=2
readout q1 -> c0 @G via a0;            // t=15, d=3"""

VARIANTS = [("primer", ""), ("ex1", ""), ("ex2", ""), ("ex2", "-one-optical"), ("ex3", "")]


def test_the_numbers_are_the_papers():
    s = json.loads((OUT / "summary.json").read_text(encoding="utf-8"))
    assert (s["ex1"]["finish"], s["ex1"]["instructions"]) == (18, 9)
    assert (s["ex2"]["finish"], s["ex2-one-optical"]["finish"]) == (34, 38)
    assert (s["ex2"]["instructions"], s["ex2-one-optical"]["instructions"]) == (14, 16)
    assert (s["ex3"]["finish"], s["ex3"]["parallel"], s["ex3"]["chain_finish"]) == (44, 32, 44)
    assert s["ex3"]["exchanges"] == [[1, 0, 1], [3, 3, 2]]
    assert s["primer"]["finish"] == 51


def test_example_1_prints_as_the_paper_prints_it():
    c = read("ex1")
    assert "\n".join(c.lines["timed"]) == PAPER_EX1_TIMED
    assert c.lines["rules"] == ["assert init: (t = 0 and delta = 0) => N-(S) = 1;",
                                "assert cap: forall s:Sites. Ntr(s) <= cap(s);",
                                "assert rel: n_optical > 0 => t >= 5;"]
    assert c.lines["hardware"][0] == "hardware Primer; site S[1]; site J[1]; site G[2];"
    assert c.lines["hardware"][1] == "path p0:S->J->G channel 0 waveform 0 duration 2;"


@pytest.mark.parametrize("key,suffix", VARIANTS)
def test_every_displayed_frame_agrees_with_the_compilers_acceptance(key, suffix):
    c = read(key, suffix)
    for timeline in (False, True):
        F.check_all(c.hardware, c.rules, c.frames(timeline=timeline))


def test_the_transport_frame_of_example_1_has_the_papers_transit_counts():
    fs = read("ex1").frames()
    assert fs[0].obs["Ntr"] == [1, 0, 1] and fs[1].obs["Ntr"] == [1, 1, 2]
    assert [f.start for f in fs[1:6]] == [0, 2, 3, 4, 5]
    assert fs[7].obs["n_optical"] == 2            # the broadcast frame


@pytest.mark.parametrize("key,suffix", VARIANTS)
def test_meaning_proj_of_the_target_code_is_the_native_program(key, suffix):
    from qccd.site.certpage import projection
    proj, native = projection(read(key, suffix))
    assert proj == native


def test_rule_one_forbids_the_broadcast_of_example_2():
    a, b = read("ex2"), read("ex2", "-one-optical")
    assert any(x.startswith("broadcast") for x in a.lines["timed"])
    assert not any(x.startswith("broadcast") for x in b.lines["timed"])


def test_a_studio_compilation_joins_every_instruction_to_its_statement():
    from qccd.ir.source_map import build
    from qccd.ir.tsir import TSIR
    d = OUT / "studio" / "bell"
    if not d.exists():
        pytest.skip("studio examples not built")
    prog = TSIR.load(d / "prog.cooled.tsir.json")
    src = build(prog, json.loads((d / "prog.qcert.json").read_text(encoding="utf-8")), d / "main.qasm")
    assert [o["name"] for o in src["ops"]] == ["h", "cx", "measure"]
    acting = [i for i in prog.instructions if i.type in ("gate", "measure")]
    assert all(str(i.id) in src["realises"] for i in acting)


def test_every_studio_example_passes_the_studio_rules_after_cooling():
    rows = json.loads((OUT / "studio" / "rows.json").read_text(encoding="utf-8"))
    assert {r["id"] for r in rows} >= {"bell", "h", "cx", "measure", "reset"}
    assert all(r["studio_failed"] == [] for r in rows), [(r["id"], r["studio_failed"]) for r in rows]


# ------------------------------------------------------------------ with the toolchain
needs_cert = pytest.mark.skipif(not available(), reason="QCCDCert toolchain not available")


@needs_cert
def test_recompiling_gives_the_committed_output(tmp_path):
    from qccd.cert.compile import compile_dir
    for ex in examples():
        for suffix, rules in ex["variants"].items():
            c = compile_dir(tmp_path / ex["key"], ex["hardware"], rules, ex["qasm"], flags=ex.get("flags", ()),
                            chain_ports=ex.get("ports"), suffix=suffix)
            assert c.lines == read(ex["key"], suffix).lines, ex["key"] + suffix


@needs_cert
def test_the_workspace_engine_compiles_a_studio_design(tmp_path):
    from qccd.workspace.runs import _qccdcert_compile
    src = Path(__file__).resolve().parents[1] / "qccd" / "site" / "compiled"
    (tmp_path / "device.arch.json").write_text((src / "grid2x3.arch.json").read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "circuit.qasm").write_text((src / "bell" / "circuit.qasm").read_text(encoding="utf-8"), encoding="utf-8")
    out = _qccdcert_compile(tmp_path, "qccdsim_jones")
    assert out["compilation"]["engine"] == "QCCDCert" and out["compilation"]["status"] == "passed"
    for f in ("prog.tsir.json", "prog.cooled.tsir.json", "prog.qcert.json"):
        assert (tmp_path / f).exists()


def _arch(name):
    from qccd.arch import Architecture
    src = Path(__file__).resolve().parents[1] / "qccd" / "site" / "compiled" / f"{name}.arch.json"
    return Architecture.from_json(json.loads(src.read_text(encoding="utf-8")))


def test_a_closed_loop_is_a_mandatory_broadcast_channel():
    """A conveyor loop is QCCDCert's own broadcast transport: one channel, declared mandatory,
    one path per slot each way, one waveform per direction, one duration."""
    from qccd.cert.machine import translate
    from qccd.cost import corrected_model
    tr = translate(_arch("ring6d"), corrected_model(), [])
    geo = tr.hardware["device"]["base"]["geometry"]
    ch = tr.loop_channels["L0"]
    assert {"channel": ch, "mandatory": True} in geo["broadcasts"]
    loop = [i for i, p in enumerate(geo["paths"]) if p["channel"] == ch]
    assert len(loop) == 12 and {geo["paths"][i]["waveform"] for i in loop} == {0, 1}
    assert len({tr.hardware["device"]["base"]["pathDurations"][i] for i in loop}) == 1
    assert {tr.rotations[i][1] for i in loop} == {1, -1}


def test_studio_junction_rules_are_in_the_hardware_program():
    """R2 (one ion on a node where three rails meet) is that site's capacity; R23 (never rest
    there) puts it in `rest` and in the `via` of the routes through it, never at their ends."""
    from qccd.cert.machine import translate
    from qccd.cost import corrected_model
    tr = translate(_arch("ring6d"), corrected_model(), [])
    geo = tr.hardware["device"]["base"]["geometry"]
    junction_traps = [i for i, s in enumerate(geo["sites"]) if s["name"] in ("S0", "S3")]
    assert all(geo["sites"][i]["capacity"] == 1 for i in junction_traps)
    assert sorted(tr.rest) == sorted(junction_traps)
    ordinary = [p for p in geo["paths"] if p["channel"] == 0]
    assert not any(p["src"] in tr.rest or p["dst"] in tr.rest for p in ordinary)
    assert any(set(p["via"]) & set(tr.rest) for p in ordinary)


@needs_cert
def test_a_full_gate_zone_is_routed_by_a_proposal_qccdcert_checks(tmp_path):
    """ring6d has two gate zones of two ions: QCCDCert's search cannot make room, so this
    site's router proposes routes and QCCDCert's routing-proposal stage admits them."""
    from qccd.cert.driver import compile_for_machine
    from qccd.cost import corrected_model
    ghz = Path(__file__).resolve().parents[1] / "Compiler" / "examples" / "ghz4.qasm"
    r = compile_for_machine(_arch("ring6d"), corrected_model(), ghz, tmp_path)
    assert (tmp_path / "route-plan.json").exists()
    assert any("routing-proposal stage" in n for n in r.translation.notes)
    assert r.compilation.finish > 0
