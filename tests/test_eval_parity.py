"""The two interpreters of the evaluation language agree: Python's (qccd/evalset) and the
browser's (qccd/viz/js/evaluate.js, over the engine's own replay).

Every studio page carries the evaluation texts' parse trees and evaluates them in the browser;
the grader, the workspace and the site evaluate the same texts in Python.  Each case here is a
device and a programme: Python evaluates it with `qccd.evalset.for_page(clock)` (the board's
metrics, the hardware budget and the clock's noise model), `tests/eval_parity.mjs` evaluates
the same trees on the page's own frames, and every metric, every requirement's verdict and
every budget line must agree.

Tolerance: 1e-9 relative.  The two sides add the same terms, but the engine and the replay
accumulate the clock and the heat in different orders (see tests/parity.mjs, "pricing"), and
ln / exp / sin / pow come from two different maths libraries; agreement to the last bit is not
on offer, agreement to nine digits is, and every defect this test exists for is far outside it.
"""

from __future__ import annotations

import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "tests" / "eval_parity.mjs"
node = shutil.which("node")
pytestmark = pytest.mark.skipif(node is None, reason="node is not installed")


def _model_opts(model, vm) -> dict:
    return {"kind": model.name,
            "corner_hops": int(getattr(model, "corner_hops", 1)),
            "junction_min_degree": int(getattr(model, "junction_min_degree", 3)),
            "length_scaling": bool(getattr(model, "length_scaling", False)),
            "pitch": float(getattr(model, "pitch", 1.0)),
            "include_anomalous": bool(getattr(model, "include_anomalous", True)),
            "anomalous_per_ms": float(vm["physics"].get("anomalous_per_ms", 0.0))}


def _case(name: str, arch, prog, clock: str, model=None) -> dict:
    from qccd.cost import corrected_model
    from qccd.evalset import for_page
    from qccd.evalset.ast_json import payload
    from qccd.evalset.interp import noise_budget
    from qccd.verify import replay
    from qccd.viz.render import build_view_model
    from test_engine_parity import _calls_of

    model = model or corrected_model(clock)
    res = replay(prog, arch, model, check_rules=False)
    vm = build_view_model(arch, prog, res, model, evaluation=False)
    ev = for_page(clock)
    # a page priced by another model (`deck`) is evaluated on the replay it shows
    run = ev.run(prog, arch, cost_model=model)
    e = ev.evaluator(run)
    metrics = {}
    for m in ev.metrics_:
        try:
            metrics[m.name] = e.value(m.name)
        except Exception as exc:  # noqa: BLE001 -- what the browser must also refuse
            metrics[m.name] = {"missing": str(exc)}
    budget = noise_budget(e, ev.channels)
    return {"name": name, "clock": clock, "calls": _calls_of(arch),
            "frames": vm["program"]["frames"], "loops": vm["arch"]["loops"],
            "classes": vm.get("classes") or {}, "model": _model_opts(model, vm),
            "closed_loops": {lid: list(lp.nodes) for lid, lp in arch.device.loops.items() if lp.closed},
            "payload": payload(ev),
            "py": {"metrics": metrics,
                   "requirements": [{"id": r["id"], "verdict": r["verdict"]} for r in ev.requirements_check(run)],
                   "budget": {k: {"faults": v["faults"], "places": v["places"]} for k, v in budget.items()}}}


def _cases() -> list[dict]:
    from qccd.arch import Architecture
    from qccd.compile.programs import build as build_prog
    from qccd.evalset.bundle import EXAMPLES, open_bundle
    from qccd.qec.extract import load_arch, load_program

    out = []
    for sid in ("os_30293bca0826939b", "os_4c75a8b07249aea2"):
        z = EXAMPLES / f"{sid}.zip"
        if z.exists():
            b = open_bundle(z)
            out.append(_case(f"entry:{sid}", load_arch(b.doc("device.arch.json")),
                             load_program(b.doc("program.tsir.json")), "quantinuum"))
    for device, kind in (("h2_racetrack", "rotate"), ("cyclone_base", "oddeven"), ("grid9x9", "walk"),
                         ("deck_unit_cell", "walk"), ("ring144_24v", "rotate")):
        p = ROOT / "arch" / f"{device}.arch.json"
        if not p.exists():
            continue
        arch = Architecture.from_json(json.loads(p.read_text(encoding="utf-8")))
        prog = build_prog(arch, kind, 4) if kind == "walk" else build_prog(arch, kind)
        out.append(_case(f"{device}:{kind}", arch, prog, "qccdsim_jones"))
        if device in ("ring144_24v", "cyclone_base"):
            # the deck model prices a corner by its hops: a step count that is not the text's
            # depth, which the browser once read in its place (8,808 against 5,444)
            from qccd.cost import deck_model
            out.append(_case(f"{device}:{kind}:deck", arch, prog, "qccdsim_jones", deck_model()))
    try:
        from qccd.site.evaluation import _small_programmes
    except ImportError:
        return out
    for x in _small_programmes():
        out.append(_case(f"small:{x['key']}", x["machine"].arch, x["run"].prog, "qccdsim_jones"))
    return out


def _close(a, b) -> bool:
    if isinstance(a, dict) or isinstance(b, dict):
        return isinstance(a, dict) and isinstance(b, dict) and ("missing" in a) == ("missing" in b)
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    if a is None or b is None:
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if math.isinf(a) or math.isinf(b):
            return a == b
        # plus an absolute 1e-14: `(1 - exp(-x)) / 2` for x ~ 1e-8 cancels, and the last bit of
        # two libraries' exp becomes 1e-8 of the result (2e-16 absolute, on qccd-noise@1's idle)
        return a == b or abs(a - b) <= 1e-9 * max(abs(a), abs(b)) + 1e-14
    return a == b


def test_the_browser_reads_the_text_as_python_does(tmp_path):
    from qccd.arch.schema import export_schema
    from test_engine_parity import DEFAULT_TEMPLATE, _template_cases

    cases = _cases()
    assert len(cases) >= 6, "too few cases to mean anything"
    doc = {"schema_blob": export_schema(), "templates": _template_cases(),
           "template_default": DEFAULT_TEMPLATE, "cases": cases}
    path = tmp_path / "eval_cases.json"
    path.write_text(json.dumps(doc, default=str), encoding="utf-8")
    run = subprocess.run([node, str(RUNNER), str(path)], capture_output=True, text=True, timeout=900, cwd=str(ROOT))
    assert run.returncode == 0, run.stdout[-2000:] + run.stderr[-3000:]
    js = json.loads(run.stdout.strip().splitlines()[-1])
    assert "fatal" not in js, js
    by = {r["name"]: r for r in js["results"]}
    for r in js["results"]:
        print(f"{r['name']:32s} {r.get('frames', 0):6d} frames {r.get('ms', 0):6d} ms in the browser's interpreter")
    problems = []
    for c in cases:
        r = by[c["name"]]
        if "error" in r:
            problems.append(f"{c['name']}: the browser failed: {r['error']} {r.get('stack', '')}")
            continue
        for k, v in c["py"]["metrics"].items():
            if not _close(v, r["metrics"].get(k)):
                problems.append(f"{c['name']}: metric {k}: python {v!r}, browser {r['metrics'].get(k)!r}")
        jr = {x["id"]: x["verdict"] for x in r["requirements"]}
        for x in c["py"]["requirements"]:
            if jr.get(x["id"]) != x["verdict"]:
                problems.append(f"{c['name']}: requirement {x['id']}: python {x['verdict']}, browser {jr.get(x['id'])}")
        for k, v in c["py"]["budget"].items():
            got = r["budget"].get(k)
            if got is None or got["places"] != v["places"] or not _close(v["faults"], got["faults"]):
                problems.append(f"{c['name']}: budget {k}: python {v}, browser {got}")
    assert not problems, "\n".join(problems[:40])
