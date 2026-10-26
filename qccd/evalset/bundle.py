"""An evaluation of a submitted design, from its bundle: the files the leaderboard serves.

A bundle (`<id>-bundle.zip`, or the folder it unpacks to) carries the device, the programme
and the compiler's certificate, and names the task release it was graded against.  The
release supplies the circuit, the experiment's detectors and the sampling budget; the
evaluation supplies everything else.  Nothing here reads the grader's own report: its
numbers are recomputed, and a page may then set them beside the report's.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["Bundle", "open_bundle", "evaluate_bundle", "EXAMPLES"]

#: Two published Goal 1 entries, kept with their official reports, that the tests and the
#: Evaluation page recompute.
EXAMPLES = Path(__file__).parent / "examples"


@dataclass
class Bundle:
    task: str
    files: dict[str, Any]
    name: str = ""

    def doc(self, name: str):
        return self.files[name]


def open_bundle(path) -> Bundle:
    """A bundle from its zip or its folder."""
    p = Path(path)
    files: dict[str, Any] = {}
    if p.is_dir():
        for f in p.glob("*.json"):
            files[f.name] = json.loads(f.read_text(encoding="utf-8"))
    else:
        with zipfile.ZipFile(p) as z:
            for n in z.namelist():
                if n.endswith(".json") and "/" not in n.strip("/"):
                    files[n] = json.loads(z.read(n).decode("utf-8"))
    man = files.get("manifest.json") or {}
    task = (man.get("task") or {}).get("id") or ""
    return Bundle(task=task, files=files, name=p.stem)


@dataclass
class Evaluated:
    bundle: Bundle
    evaluation: Any
    run: Any
    experiment: Any
    release: Any
    metrics: dict = field(default_factory=dict)
    requirements: list = field(default_factory=list)
    circuit: Any = None
    noiseless: Any = None
    sample: Any = None
    rules: Any = None


def evaluate_bundle(path, evaluation: str = "goal1", *, sample: bool = False,
                    rules: bool = True, seed: int | None = None) -> Evaluated:
    """Run the evaluation on a bundle: metrics, requirements, the noisy circuit and its
    noiseless check, and -- with `sample` -- the logical error rate under the release's own
    sampling budget and seed."""
    from ..cost.models import corrected_model
    from ..qec import MemoryExperiment
    from ..qec.check import noiseless_check
    from ..qec.decode import estimate_ler
    from ..qec.extract import load_arch, load_program
    from ..workspace.tasks import find_release
    from . import load

    b = open_bundle(path)
    rel = find_release(b.task)
    ev = load(evaluation)
    exp = MemoryExperiment.from_spec(rel.detectors(), rel.circuit_text())
    prog = load_program(b.doc("program.tsir.json"))
    arch = load_arch(b.doc("device.arch.json"))
    cert = b.files.get("certificate.qcert.json")
    run = ev.run(prog, arch, experiment=exp, cert=cert)
    out = Evaluated(bundle=b, evaluation=ev, run=run, experiment=exp, release=rel)
    checks: dict[str, bool] = {}
    if rules:
        from ..ruleset import check_programme, load as load_rules
        out.rules = check_programme(load_rules(ev.rules or "standard"), prog, arch,
                                    corrected_model(ev.clock or "qccdsim_jones"))
        checks["rules"] = bool(out.rules.ok)
    out.circuit = ev.circuit(run)
    out.noiseless = noiseless_check(out.circuit.stim())
    checks["noiseless"] = out.noiseless.ok
    if sample and out.noiseless.ok:
        q = rel.manifest.get("qec") or {}
        budget = {k: v for k, v in (q.get("ler") or {}).items() if k != "seed"}
        if budget.get("decoder", "auto") == "auto" and not exp.matchable:
            budget["decoder"] = "bposd"
        out.sample = estimate_ler(out.circuit.stim(), rounds=exp.rounds,
                                  seed=(q.get("ler") or {}).get("seed") if seed is None else seed,
                                  **budget)
    out.metrics = ev.metrics(run, sample=out.sample, checks=checks)
    out.requirements = ev.requirements_check(run, sample=out.sample, checks=checks)
    return out
