"""The evaluation language, held to the grader.

Every number the Evaluation page attributes to the language must be the number the
official grader computes.  The two published Goal 1 entries kept in
`qccd/evalset/examples/` are recomputed here from their bundles: the noisy circuit, qubit
by qubit, against `qccd.qec.extract`; the error budget, channel by channel; and the
deterministic metrics and the logical-error-rate arithmetic against their official reports.
The hardware formulas are held to `qccd.cost.hardware.hardware_report` on every shipped
device.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

stim = pytest.importorskip("stim")

from qccd.evalset import EvalSyntaxError, load, names  # noqa: E402
from qccd.evalset.bundle import EXAMPLES, open_bundle  # noqa: E402
from qccd.evalset.compare import same_circuit  # noqa: E402
from qccd.evalset.interp import EvalError  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
ENTRIES = sorted(p.stem for p in EXAMPLES.glob("*.zip"))
DETERMINISTIC = ("T_quantinuum", "instructions", "dacs", "ions_per_instruction", "transport_instructions")
OURS = {"T_quantinuum": "T"}


def _entry(sid: str):
    from qccd.qec import MemoryExperiment
    from qccd.qec.extract import load_arch, load_program
    from qccd.workspace.tasks import find_release

    b = open_bundle(EXAMPLES / f"{sid}.zip")
    rel = find_release(b.task)
    exp = MemoryExperiment.from_spec(rel.detectors(), rel.circuit_text())
    prog = load_program(b.doc("program.tsir.json"))
    arch = load_arch(b.doc("device.arch.json"))
    cert = b.files.get("certificate.qcert.json")
    report = json.loads((EXAMPLES / f"{sid}.report.json").read_text(encoding="utf-8"))
    return prog, arch, exp, cert, report


def _close(a: float, b: float, rel: float = 1e-9) -> bool:
    return a == b or abs(a - b) <= rel * max(abs(a), abs(b))


def test_every_shipped_evaluation_loads():
    assert {"metrics", "qccd-noise@1", "qccd-noise@2", "goal1"} <= set(names())
    for n in names():
        ev = load(n)
        assert ev.name == n and ev.title


def test_there_are_examples_to_check():
    assert len(ENTRIES) >= 2, "the published entries the page and these tests recompute are missing"


@pytest.mark.parametrize("sid", ENTRIES)
def test_the_noisy_circuit_is_the_graders(sid):
    from qccd.qec.extract import extract

    prog, arch, exp, cert, report = _entry(sid)
    ref = extract(prog, arch, exp, noise="qccd-noise@2", cert=cert)
    ev = load("goal1")
    mine = ev.circuit(ev.run(prog, arch, experiment=exp, cert=cert))
    ok, why = same_circuit(ref.circuit, mine.stim())
    assert ok, why
    # and the budget, channel by channel, against the official report
    budget = [s for s in report["stages"] if s["id"] == "ler"][0]["detail"]["budget"]
    groups = {"ms_base+ms_heating+ms_chain": ("ms",), "gate_1q": ("gate_1q",),
              "measure": ("readout_z", "readout_x"), "reset": ("prepare", "reset"),
              "memory": ("memory",), "memory_coherent": ("memory_coherent",),
              "crosstalk": ("crosstalk",), "crosstalk_global": ("crosstalk_global", "crosstalk_reset")}
    for theirs, ours in groups.items():
        t = sum(budget[k] for k in theirs.split("+"))
        o = sum(mine.budget[k] for k in ours)
        assert _close(o, t, 1e-9), (theirs, o, t)


@pytest.mark.parametrize("sid", ENTRIES)
def test_the_metrics_are_the_official_reports(sid):
    prog, arch, exp, cert, report = _entry(sid)
    ev = load("goal1")
    run = ev.run(prog, arch, experiment=exp, cert=cert)

    class Sample:      # the official sample's counts, read through the language's own formulas
        ler = [s for s in report["stages"] if s["id"] == "ler"][0]["detail"]["ler"]
        shots, errors = ler["shots"], ler["errors"]

    got = ev.metrics(run, sample=Sample)
    for k in DETERMINISTIC:
        assert _close(got[OURS.get(k, k)], report["metrics"][k]["value"]), k
    for k in ("ler", "ler_per_round", "ler_per_round_per_logical"):
        assert _close(got[k], report["metrics"][k]["value"], 1e-12), k
    assert _close(got["ler_per_round_lo"], Sample.ler["per_round_lo"], 1e-12)
    assert _close(got["ler_per_round_hi"], Sample.ler["per_round_hi"], 1e-12)


@pytest.mark.parametrize("sid", ENTRIES[:1])
def test_the_replay_totals_are_the_metrics(sid):
    prog, arch, exp, cert, report = _entry(sid)
    ev = load("metrics")
    run = ev.run(prog, arch)
    got = ev.metrics(run)
    res = run.result
    assert _close(got["T"], res.total_us / 1000)
    assert _close(got["cost"], res.total_cost)
    assert _close(got["peak_nbar"], res.peak_quanta)
    assert _close(got["gate_error_sum"], res.gate_error_sum)
    assert got["junction_crossings"] == sum(res.junction_transits.values())


@pytest.mark.parametrize("sid", ENTRIES[:1])
def test_noise_at_one_is_the_graders_too(sid):
    from qccd.qec.extract import extract

    prog, arch, exp, cert, _ = _entry(sid)
    ref = extract(prog, arch, exp, noise="qccd-noise@1", cert=cert)
    ev = load("qccd-noise@1")
    mine = ev.circuit(ev.run(prog, arch, experiment=exp, cert=cert))
    ok, why = same_circuit(ref.circuit, mine.stim())
    assert ok, why


def test_a_flipped_pulse_is_caught_by_the_noiseless_check():
    from qccd.ir.tsir import TSIR
    from qccd.qec.check import noiseless_check

    prog, arch, exp, cert, _ = _entry(ENTRIES[0])
    doc = prog.to_json()
    for ins in doc["instructions"]:
        if ins.get("type") == "gate" and ins.get("gate") == "MS" and ins.get("params"):
            ins["params"] = [[-p[0]] + list(p[1:]) for p in ins["params"]]
            break
    else:
        pytest.skip("no MS pulse to flip")
    ev = load("goal1")
    good = noiseless_check(ev.circuit(ev.run(prog, arch, experiment=exp, cert=cert)).stim())
    bad = noiseless_check(ev.circuit(ev.run(TSIR.from_json(doc), arch, experiment=exp, cert=cert)).stim())
    assert good.ok and not bad.ok


def test_a_requirement_fails_with_its_budget():
    prog, arch, exp, cert, _ = _entry(ENTRIES[0])
    text = (ROOT / "qccd" / "evalset" / "library" / "goal1.eval").read_text(encoding="utf-8")
    ev = load(text.replace("param max_dacs = 128", "param max_dacs = 10"))
    rows = {r["id"]: r for r in ev.requirements_check(ev.run(prog, arch, experiment=exp, cert=cert))}
    assert rows["G2"]["verdict"] == "fails"
    assert rows["G4"]["verdict"] == "holds"
    assert rows["G1"]["verdict"] == "could not be checked"     # no rule verdict was given


def test_the_hardware_formulas_are_the_hardware_report():
    from qccd.arch import Architecture
    from qccd.cost.hardware import WIRING_FLOORS, hardware_report
    from qccd.ir.tsir import TSIR

    ev = load("metrics")
    devices = sorted((ROOT / "arch").glob("*.arch.json"))
    assert devices
    for p in devices:
        arch = Architecture.from_json(json.loads(p.read_text(encoding="utf-8")))
        site = next(n for n, nd in arch.device.nodes.items() if nd.kind == "site")
        prog = TSIR.from_json({"name": "empty", "arch_spec": arch.name, "instructions": [
            {"type": "init", "id": 0, "placement": {"q0": site}}]})
        got = ev.metrics(ev.run(prog, arch), only=["electrodes", "switches", "dacs", "n_sites", "n_junctions"])
        rep = hardware_report(arch, floors=WIRING_FLOORS)
        assert (got["electrodes"], got["switches"], got["dacs"]) == (rep.electrodes, rep.switches, rep.dacs), p.name
        assert (got["n_sites"], got["n_junctions"]) == (rep.n_traps, rep.n_junctions), p.name


def test_syntax_errors_name_the_line():
    with pytest.raises(EvalSyntaxError, match="line 3"):
        load('evaluation "x"\nmetric a "A" = 1\nmetric a "A again" = 2\n')
    with pytest.raises(EvalSyntaxError, match="on"):
        load('evaluation "x"\nchannel c "C"\n  after g in two_qubit_gates:\n    X_ERROR(0.1) at ions(g)\n')
    with pytest.raises(EvalSyntaxError, match="not a metric"):
        load('evaluation "x"\nrank nothing\n')


def test_a_channel_is_bound_to_an_operation_or_a_step():
    prog, arch, exp, cert, _ = _entry(ENTRIES[0])
    ev = load('evaluation "x"\ninclude "metrics"\nclock "quantinuum"\n'
              'channel c "C"\n  after i in ions:\n    Z_ERROR(0.1) on i\n')
    with pytest.raises(EvalError, match="bound to"):
        ev.circuit(ev.run(prog, arch, experiment=exp, cert=cert))
