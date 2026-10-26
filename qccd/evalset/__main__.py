"""`python -m qccd.evalset`: read, run and check evaluations.

    python -m qccd.evalset list                          the evaluations the website ships
    python -m qccd.evalset show goal1                    one, as its own text
    python -m qccd.evalset bundle <id>-bundle.zip        an entry's metrics, requirements, circuit
          [--eval goal1] [--sample] [--stim out.stim]
    python -m qccd.evalset agree <bundle>...             the circuit against the grader's, qubit by qubit
"""

from __future__ import annotations

import argparse
import json
import sys


def _num(v) -> str:
    if isinstance(v, float):
        return f"{v:.6g}"
    return str(v)


def main(argv=None) -> int:
    from . import load, names

    ap = argparse.ArgumentParser(prog="python -m qccd.evalset", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    s = sub.add_parser("show")
    s.add_argument("name")
    s = sub.add_parser("bundle")
    s.add_argument("bundle")
    s.add_argument("--eval", default="goal1")
    s.add_argument("--sample", action="store_true", help="estimate the logical error rate too")
    s.add_argument("--stim", help="write the noisy circuit here")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("agree")
    s.add_argument("bundles", nargs="+")
    a = ap.parse_args(argv)

    if a.cmd == "list":
        for n in names():
            ev = load(n)
            print(f"{n:16s} {ev.title}")
        return 0
    if a.cmd == "show":
        print(load(a.name).text(), end="")
        return 0
    if a.cmd == "bundle":
        from .bundle import evaluate_bundle
        r = evaluate_bundle(a.bundle, a.eval, sample=a.sample)
        if a.stim:
            with open(a.stim, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(r.circuit.lines) + "\n")
        if a.json:
            out = {k: v for k, v in r.metrics.items()}
            out["requirements"] = r.requirements
            out["budget"] = r.circuit.budget
            print(json.dumps(out, indent=1, default=str))
            return 0
        print(f"{r.bundle.task}: {r.evaluation.title}")
        for m in r.evaluation.metrics_:
            if m.name in r.metrics:
                print(f"  {m.name:28s} {_num(r.metrics[m.name]):>14s} {m.unit}")
        for name, why in (r.metrics.get("__missing__") or {}).items():
            print(f"  {name:28s} {'-':>14s} ({why})")
        for q in r.requirements:
            print(f"  {q['id']} {q['verdict']:22s} {q['title']}" + (f" ({q['reason']})" if q.get("reason") else ""))
        print(f"  circuit: {len(r.circuit.lines)} lines, {r.circuit.measurements} measurements; "
              f"noiseless check: {r.noiseless.reason}")
        return 0 if all(q["verdict"] == "holds" for q in r.requirements) else 1
    if a.cmd == "agree":
        from ..qec import MemoryExperiment
        from ..qec.extract import extract, load_arch, load_program
        from ..workspace.tasks import find_release
        from .bundle import open_bundle
        from .compare import same_circuit
        ev = load("goal1")
        bad = 0
        for path in a.bundles:
            b = open_bundle(path)
            rel = find_release(b.task)
            exp = MemoryExperiment.from_spec(rel.detectors(), rel.circuit_text())
            prog = load_program(b.doc("program.tsir.json"))
            arch = load_arch(b.doc("device.arch.json"))
            cert = b.files.get("certificate.qcert.json")
            noise = (rel.manifest.get("qec") or {}).get("noise", "qccd-noise@2")
            ref = extract(prog, arch, exp, noise=noise, cert=cert)
            mine = load(noise).circuit(load(noise).run(prog, arch, experiment=exp, cert=cert))
            ok, why = same_circuit(ref.circuit, mine.stim())
            bad += not ok
            print(f"{'same' if ok else 'DIFFERENT'}  {b.task}  {path}  {why}")
        return 1 if bad else 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
