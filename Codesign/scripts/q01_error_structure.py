"""CD4 · 0.1 — does anything the ARCHITECTURE controls actually move the score?

The falsification test the plan puts first.  If the error budget of a compiled program is
the same on every device, then geometry is irrelevant under this model and the codesign
study is void before it starts.

Method: no new compilation.  `Compiler/bridge/run_matrix.py` has already compiled nine
example circuits onto nine architectures and left the cooled programs in
`Compiler/build/out/`.  This replays each one, decomposes `-ln F` into its four terms, and
asks which of them a device can change.

    python Codesign/scripts/q01_error_structure.py [--json Codesign/data/q01.json]

`p_eff` here is `-ln F` divided by the number of OPERATIONS (two-qubit gates, one-qubit
gates, measures, resets).  That denominator is a convention and it matters: quoted against
a circuit-level threshold, a definition that also counts idle locations would give a smaller
number.  It is stated here rather than buried so that any comparison to Bravyi's 0.7 %
threshold can be read with the convention in view.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.analysis.budget import error_budget  # noqa: E402
from qccd.arch import load  # noqa: E402
from qccd.cost import corrected_model, t2_metrics  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402
from qccd.verify import replay  # noqa: E402

OUT = ROOT / "Compiler" / "build" / "out"


def devices() -> list[str]:
    return sorted(p.name[: -len(".arch.json")] for p in (ROOT / "arch").glob("*.arch.json"))


def measure(path: Path, device: str) -> dict | None:
    arch = load(str(ROOT / "arch" / f"{device}.arch.json"))
    model = corrected_model()
    prog = TSIR.load(str(path))
    res = replay(prog, arch, model, check_rules=False, keep_cycles=False)
    m = t2_metrics(arch, res, model)
    b = error_budget(prog, arch, model)

    ops = m.n_gate_pairs + res.n_1q_gates + res.n_measure + res.n_reset
    if ops == 0:
        return None
    # what the DEVICE is responsible for: everything above the floor the gate set would
    # cost on a machine with no transport at all
    excess = max(m.neg_log_fidelity - b.floor_error, 0.0)
    return {
        "device": device,
        "runtime_ms": m.runtime_us / 1000.0,
        "neg_log_fidelity": m.neg_log_fidelity,
        "gate_error": m.gate_error_sum,
        "idle_error": m.idle_error,
        "spam_error": m.spam_error,
        "floor_error": b.floor_error,
        "excess_error": excess,
        "excess_share": excess / m.neg_log_fidelity if m.neg_log_fidelity else 0.0,
        "n_ops": ops,
        "n_gate_pairs": m.n_gate_pairs,
        "p_eff": m.neg_log_fidelity / ops,
        "mean_gate_quanta": m.mean_gate_quanta,
        "peak_quanta": m.peak_quanta,
        "quanta_per_data_ion": dict(m.quanta_per_data_ion),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=None)
    ap.add_argument("--threshold", type=float, default=0.007,
                    help="the code's published circuit-level threshold")
    a = ap.parse_args(argv)

    devs = devices()
    rows: list[dict] = []
    for p in sorted(OUT.glob("*.cooled.tsir.json")):
        stem = p.name[: -len(".cooled.tsir.json")]
        dev = next((d for d in devs if stem.endswith("_" + d)), None)
        if dev is None:
            continue
        try:
            got = measure(p, dev)
        except Exception as exc:                          # noqa: BLE001
            print(f"  {stem}: skipped ({type(exc).__name__})")
            continue
        if got:
            got["circuit"] = stem[: -len("_" + dev)]
            rows.append(got)

    if not rows:
        print("no compiled programs found; run Compiler/bridge/run_matrix.py first")
        return 2

    hdr = (f"{'circuit':<14}{'device':<17}{'ms':>8}{'-lnF':>9}{'excess':>9}"
           f"{'exc%':>7}{'gate':>9}{'idle':>9}{'spam':>9}{'p_eff':>9}")
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(rows, key=lambda r: (r["circuit"], r["device"])):
        print(f"{r['circuit']:<14}{r['device']:<17}{r['runtime_ms']:>8.2f}"
              f"{r['neg_log_fidelity']:>9.4f}{r['excess_error']:>9.4f}"
              f"{r['excess_share'] * 100:>6.1f}%{r['gate_error']:>9.4f}"
              f"{r['idle_error']:>9.5f}{r['spam_error']:>9.4f}{r['p_eff']:>9.5f}")

    # the question, answered
    print()
    by_circuit: dict[str, list[dict]] = {}
    for r in rows:
        by_circuit.setdefault(r["circuit"], []).append(r)
    spreads = [(c, max(v, key=lambda r: r["neg_log_fidelity"]),
                min(v, key=lambda r: r["neg_log_fidelity"]))
               for c, v in by_circuit.items() if len(v) > 1]
    print("SAME CIRCUIT, DIFFERENT DEVICE -- how much does the device move the score?")
    for c, hi, lo in sorted(spreads, key=lambda t: t[0]):
        ratio = hi["neg_log_fidelity"] / lo["neg_log_fidelity"]
        print(f"  {c:<14} {ratio:>5.2f}x   best {lo['device']:<17} "
              f"worst {hi['device']:<17} (excess share {lo['excess_share']:.0%}"
              f" .. {hi['excess_share']:.0%})")

    p_effs = [r["p_eff"] for r in rows]
    print()
    print(f"p_eff over all {len(rows)} programs: {min(p_effs):.5f} .. {max(p_effs):.5f} "
          f"({max(p_effs) / min(p_effs):.2f}x spread), threshold {a.threshold}")
    print(f"  above threshold: {sum(1 for p in p_effs if p > a.threshold)} of {len(rows)}")
    shares = [r["excess_share"] for r in rows]
    print(f"device-attributable share of -ln F: {min(shares):.0%} .. {max(shares):.0%}, "
          f"median {sorted(shares)[len(shares) // 2]:.0%}")

    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"\n-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
