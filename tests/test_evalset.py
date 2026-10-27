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
    assert {"metrics", "board", "qccd-noise@1", "qccd-noise@2", "goal1"} <= set(names())
    for n in names():
        ev = load(n)
        assert ev.name == n and ev.title


def test_there_are_examples_to_check():
    assert len(ENTRIES) >= 2, "the published entries the page and these tests recompute are missing"


@pytest.mark.parametrize("sid", ENTRIES)
def test_the_noisy_circuit_is_the_graders(sid):
    """The text writes the circuit the hand-written extraction wrote (the one every published
    entry was graded with), and books its expected faults under the report's own budget lines."""
    from qccd.qec.legacy_extract import extract_legacy

    prog, arch, exp, cert, report = _entry(sid)
    ref = extract_legacy(prog, arch, exp, noise="qccd-noise@2", cert=cert)
    ev = load("goal1")
    mine = ev.circuit(ev.run(prog, arch, experiment=exp, cert=cert))
    ok, why = same_circuit(ref.circuit, mine.stim())
    assert ok, why
    budget = [s for s in report["stages"] if s["id"] == "ler"][0]["detail"]["budget"]
    for key, t in budget.items():
        if key == "total":
            continue
        assert _close(mine.budget.get(key, 0.0), t, 1e-9), (key, mine.budget.get(key), t)


@pytest.mark.parametrize("sid", ENTRIES)
def test_extract_is_the_text(sid):
    """The extraction every consumer calls (the grader, the workspace, the boards) is the text:
    its circuit is the evaluation's line for line, and its trace still tiles the circuit."""
    from qccd.qec.extract import extract
    from qccd.qec.legacy_extract import extract_legacy

    prog, arch, exp, cert, _ = _entry(sid)
    new = extract(prog, arch, exp, noise="qccd-noise@2", cert=cert)
    ev = load("qccd-noise@2")
    mine = ev.circuit(ev.run(prog, arch, experiment=exp, cert=cert))
    assert new.lines == mine.lines
    assert new.evaluation["digest"] == ev.digest()
    old = extract_legacy(prog, arch, exp, noise="qccd-noise@2", cert=cert)
    assert {k: round(v, 15) for k, v in new.stats.items() if isinstance(v, float)} ==         {k: round(v, 15) for k, v in old.stats.items() if isinstance(v, float)}
    assert {k: v for k, v in new.stats.items() if not isinstance(v, float)} ==         {k: v for k, v in old.stats.items() if not isinstance(v, float)}


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
    assert got["depth"] == res.total_steps
    comp = res.quanta_components
    assert _close(got["transport_quanta"], comp["shuttle"] + comp["junction"] + comp["split_merge"], 1e-9)
    assert _close(got["total_quanta"], sum(comp.values()), 1e-9)


@pytest.mark.parametrize("sid", ENTRIES[:1])
def test_noise_at_one_is_the_graders_too(sid):
    from qccd.qec.legacy_extract import extract_legacy

    prog, arch, exp, cert, _ = _entry(sid)
    ref = extract_legacy(prog, arch, exp, noise="qccd-noise@1", cert=cert)
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
    assert rows["B1"]["verdict"] == "fails"                     # the DAC budget, from board.eval
    assert rows["B2"]["verdict"] == "holds"
    assert rows["G2"]["verdict"] == "holds"                     # at most 200 ions
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


def test_the_board_metrics_are_the_text():
    """compute_metrics (the grader's metrics stage, the workspace, the bench) is the text."""
    from qccd.workspace.metrics import compute_metrics, replay_time

    prog, arch, exp, cert, report = _entry(ENTRIES[0])
    got, br = compute_metrics(arch, prog, {"rank_table": "quantinuum", "tables": ["quantinuum", "qccdsim_jones"]})
    assert _close(got["T_quantinuum"], replay_time(arch, prog, "quantinuum")[0])
    assert _close(got["T_jones"], replay_time(arch, prog, "qccdsim_jones")[0])
    assert br["evaluation"]["name"] == "metrics"
    for k in ("instructions", "dacs", "ions_per_instruction", "transport_instructions"):
        assert _close(got[k], report["metrics"][k]["value"]), k


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


def test_a_clock_is_an_operating_point_table():
    """A misspelt clock was priced on another table, silently: seven task files asked for
    `qccdsim_transport`, and their `T_transport` was never computed (`T_qccdsim_transport`, as
    long as `T_jones`, was reported in its place).  The language refuses it wherever it is named."""
    from qccd.workspace.metrics import compute_metrics

    with pytest.raises(EvalSyntaxError, match="line 2: clock 'qccdsim_transport'"):
        load('evaluation "x"\nclock "qccdsim_transport"\n')
    with pytest.raises(EvalSyntaxError, match="line 2: clock"):
        load('evaluation "x"\nmetric t "T" clock "fast"\n  = 1\n')
    with pytest.raises(ValueError, match="not an operating-point table"):
        load("metrics").with_clock("qccdsim_transport")
    prog, arch, *_ = _entry(ENTRIES[0])
    with pytest.raises(EvalError, match="not an operating-point table"):
        compute_metrics(arch, prog, {"rank_table": "quantinuum", "tables": ["quantinuum", "qccdsim_transport"]})
    for p in sorted((ROOT / "tasks").glob("*/task.json")):
        tables = (json.loads(p.read_text(encoding="utf-8")).get("physics") or {}).get("tables") or []
        from qccd.arch.curves import TABLES
        assert set(tables) <= set(TABLES), f"{p.parent.name} names a table the loader refuses: {tables}"


def test_the_shortcuts_are_the_walk():
    """The interpreter's two shortcuts change no value: a one-variable aggregate rebinds one
    environment and evaluates a word's other arguments once; a word is not kept per call.  Each
    is held to the plain walk, which a `where true` forces."""
    prog, arch, *_ = _entry(ENTRIES[1])
    ev = load('evaluation "x"\ninclude "metrics"\nclock "quantinuum"\n'
              'metric flat "F" = max(nbar(i, end(k)) for k in steps, i in ions)\n'
              'metric walked "W" = max(max(nbar(i, end(k)) for i in ions where true) for k in steps where true)\n'
              'metric heat "H" = sum(sum(nbar(i, start(k)) for i in ions) for k in steps)\n'
              'metric heat_walked "HW" = sum(sum(nbar(i, start(k)) for i in ions where true) for k in steps where true)\n')
    e = ev.evaluator(ev.run(prog, arch))
    assert e.value("peak_nbar") == e.value("flat") == e.value("walked") > 0
    assert e.value("heat") == e.value("heat_walked") > 0
