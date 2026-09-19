"""CD4 . 0.5 -- sweep the ring's dock count at BB scale, and separate it from ancilla count.

The study had one architecture that could compile the BB round, so there was nothing to
compare (blocker B2).  Every device the `ring` generator emits is conveyor-shaped -- a
closed loop with gate-capable dock spurs -- so rigid rotation serves all of them, and the
dock count becomes a sweepable architecture axis with real candidates at both ends.

    python Codesign/scripts/q05_ring_dock_sweep.py --json Codesign/data/q05.json

Two axes, because the obvious sweep confounds them.  `verticals` sets the dock count, and
the natural circuit for V docks has V ancillas -- so a one-parameter sweep changes the
machine and the program together.  `gen_bb144.py` emits a reset only for checks beyond the
first `n_anc`, so resets = 144 - V, and reset error (5e-3) is 3x measure error (1.6e-3):
three quarters of a naive sweep's fidelity trend is "we did fewer resets", which would be
true on any device.  So this also runs the geometry axis at FIXED ancilla count, where
SPAM and the operation count are constant and every difference is the machine.

`p_eff`'s denominator counts OPERATIONS (2q, 1q, measure, reset), not idle locations.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.cost import corrected_model, t2_metrics  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402
from qccd.verify import replay  # noqa: E402

EXE = ROOT / "Compiler" / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"
RAIL_SLOTS = 144  # ring(width=72, height=2) gives 2W + 2H - 4 slots


def sh(cmd: list[str]) -> tuple[int, str]:
    env = {**os.environ,
           "PATH": os.path.expanduser(r"~\AppData\Local\opam\default\bin")
           + os.pathsep + os.environ.get("PATH", "")}
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(ROOT))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def build_arch(out: Path, verticals: int) -> Path:
    """`ring144_24v` with ONE parameter changed, so every other declaration is identical.

    That is what makes the comparison mean something: the primitives, the wiring, the
    coherence time and the heating rate all come from the shipped file, and at
    `verticals=24` the result is the shipped baseline itself.
    """
    if RAIL_SLOTS % verticals:
        raise SystemExit(f"{verticals} docks do not divide {RAIL_SLOTS} rail slots evenly")
    doc = json.loads((ROOT / "arch" / "ring144_24v.arch.json").read_text(encoding="utf-8"))
    doc["name"] = f"ring144_{verticals}v"
    doc["description"] = f"dock sweep: {RAIL_SLOTS} rail slots, {verticals} dock spurs"
    doc["geometry"]["params"]["verticals"] = verticals
    p = out / f"ring144_{verticals}v.arch.json"
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return p


def one(out: Path, verticals: int, ancillas: int) -> dict:
    tag = f"v{verticals}_a{ancillas}"
    arch_path = build_arch(out, verticals)
    exp = out / f"{tag}.expanded.json"
    sh([sys.executable, "Compiler/bridge/export_arch.py", str(arch_path), "-o", str(exp)])
    qasm = out / f"bb_a{ancillas}.qasm"
    if not qasm.exists():
        sh([sys.executable, "Compiler/bridge/gen_bb144.py", "-o", str(qasm),
            "--ancillas", str(ancillas)])

    prefix = out / tag
    rc, log = sh([str(EXE), "compile", str(qasm), "--arch", str(exp), "-o", str(prefix)])
    row = {"verticals": verticals, "ancillas": ancillas}
    if rc != 0 or not Path(str(prefix) + ".tsir.json").exists():
        row["status"] = "did not compile"
        row["detail"] = log.strip().splitlines()[-1][:100] if log.strip() else ""
        return row
    # "864 contacts in 546 batches, 546 rotations, 776 hops" -- matched by name, because
    # splitting on "in" also catches the "in" inside "routing" on a neighbouring line
    for line in log.splitlines():
        for key, pat in (("batches", r"(\d+)\s+batches"), ("hops", r"(\d+)\s+hops"),
                         ("rotations", r"(\d+)\s+rotations")):
            if "contacts in" in line:
                mm = re.search(pat, line)
                if mm:
                    row[key] = int(mm.group(1))

    cooled = out / f"{tag}.cooled.tsir.json"
    sh([sys.executable, "Compiler/bridge/insert_cooling.py", str(prefix) + ".tsir.json",
        "--arch", str(arch_path), "-o", str(cooled)])
    rc, rlog = sh([sys.executable, "Compiler/bridge/check_tsir.py", str(cooled),
                   "--arch", str(arch_path), "--model", "corrected"])
    row["rules"] = next((l.strip() for l in rlog.splitlines() if "rules passed" in l), "?")
    row["rules_ok"] = "RULES FAILED" not in rlog

    arch = load(str(arch_path))
    model = corrected_model()
    res = replay(TSIR.load(str(cooled)), arch, model, check_rules=False, keep_cycles=False)
    m = t2_metrics(arch, res, model)
    ops = m.n_gate_pairs + res.n_1q_gates + res.n_measure + res.n_reset
    eps0 = 1.0 - float(arch.primitives.scalar("ms_gate")["fidelity_at_n0"])
    gate_floor = m.n_gate_pairs * eps0
    row.update({
        "status": "ok",
        "runtime_ms": m.runtime_us / 1000.0,
        "neg_log_fidelity": m.neg_log_fidelity,
        "gate_error": m.gate_error_sum,
        "gate_floor": gate_floor,
        "idle_error": m.idle_error,
        "spam_error": m.spam_error,
        # what the architecture is responsible for: heating that survived cooling, plus
        # the idle dephasing of however many ions for however long the round took
        "device_error": (m.gate_error_sum - gate_floor) + m.idle_error,
        "n_ops": ops,
        "n_reset": res.n_reset,
        "n_ions": len(res.per_ion_quanta),
        "p_eff": m.neg_log_fidelity / ops,
        "mean_gate_quanta": m.mean_gate_quanta,
        "chain_len_at_gate": {str(k): v for k, v in sorted(res.chain_len_at_gate.items())},
    })
    return row


def table(title: str, note: str, rows: list[dict]) -> None:
    print()
    print(title)
    print(f"  {note}")
    print(f"  {'docks':>6}{'anc':>5}{'T_round':>11}{'p_eff':>12}{'-lnF':>10}"
          f"{'floor':>9}{'device':>9}{'dev%':>7}{'resets':>8}{'batches':>9}  rules")
    for r in rows:
        if r.get("status") != "ok":
            print(f"  {r['verticals']:>6}{r['ancillas']:>5}   "
                  f"{r.get('detail', r['status'])}")
            continue
        print(f"  {r['verticals']:>6}{r['ancillas']:>5}{r['runtime_ms']:>9.2f}ms"
              f"{r['p_eff']:>12.4e}{r['neg_log_fidelity']:>10.5f}"
              f"{r['gate_floor'] + r['spam_error']:>9.4f}{r['device_error']:>9.5f}"
              f"{r['device_error'] / r['neg_log_fidelity'] * 100:>6.1f}%"
              f"{r['n_reset']:>8}{r.get('batches', 0):>9}  "
              f"{'ok' if r['rules_ok'] else 'FAILED'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=None)
    ap.add_argument("--out", default=None, help="scratch dir for architectures and programs")
    ap.add_argument("--docks", default="12,16,18,24,36,48,72",
                    help="the confounded sweep, where docks == ancillas")
    ap.add_argument("--separate", default="24,36,48,72",
                    help="the geometry axis: these dock counts at FIXED 24 ancillas")
    a = ap.parse_args(argv)

    out = Path(a.out) if a.out else ROOT / "Compiler" / "build" / "q05"
    out.mkdir(parents=True, exist_ok=True)
    if not EXE.exists():
        print(f"missing {EXE}; run `cd Compiler/ocaml && dune build`")
        return 2

    confounded = [one(out, v, v) for v in (int(x) for x in a.docks.split(",")) if v]
    # ancillas <= docks is a hard constraint of rigid rotation: the ancillas park at docks
    separated = [one(out, v, 24) for v in (int(x) for x in a.separate.split(",")) if v >= 24]

    table("CONFOUNDED SWEEP -- docks and ancillas move together",
          "resets = 144 - docks, and reset error is 3x measure error, so most of the "
          "fidelity trend here is SPAM",
          confounded)
    table("GEOMETRY AXIS -- ancillas FIXED at 24, so SPAM and the op count are constant",
          "every difference in this table is the machine, not the program",
          separated)

    ok = [r for r in separated if r.get("status") == "ok"]
    if len(ok) > 1:
        best_p = min(ok, key=lambda r: r["p_eff"])
        worst_p = max(ok, key=lambda r: r["p_eff"])
        fast = min(ok, key=lambda r: r["runtime_ms"])
        slow = max(ok, key=lambda r: r["runtime_ms"])
        print()
        print("WHAT THE GEOMETRY ALONE IS WORTH")
        print(f"  T_round: {slow['runtime_ms']:.2f} ms ({slow['verticals']} docks) -> "
              f"{fast['runtime_ms']:.2f} ms ({fast['verticals']} docks) = "
              f"{slow['runtime_ms'] / fast['runtime_ms']:.3f}x")
        print(f"  p_eff  : {worst_p['p_eff']:.4e} -> {best_p['p_eff']:.4e} = "
              f"{worst_p['p_eff'] / best_p['p_eff']:.4f}x")
        same_gate = len({round(r["gate_error"], 12) for r in ok}) == 1
        print(f"  the gate term is {'identical' if same_gate else 'NOT identical'} at every "
              f"point ({ok[0]['gate_error']:.5f} = n_pairs x eps0), because the cooling "
              f"pass drives the mean gate n-bar to {ok[0]['mean_gate_quanta']:g}")
        print("  -> the architecture moves the ROUND TIME and barely moves the SCORE")

    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(
            {"confounded": confounded, "geometry_axis": separated}, indent=1),
            encoding="utf-8")
        print(f"\n-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
