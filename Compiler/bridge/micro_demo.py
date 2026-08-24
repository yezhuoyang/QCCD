"""The small worked example: one circuit, two architectures, side by side.

Everything else in `Compiler/` is measured at a scale where the numbers matter and the
individual instructions cannot be read.  This is the opposite, and it is what the README
shows: four qubits, seven statements, and two twelve-trap devices, small enough that every
hardware instruction the compiler emits fits on one screen beside the statement it came
from.

The two devices are the same size and differ in the one thing this project exists to
study -- **the wiring**:

  micro_ring   a closed loop of 8 rail slots with 4 dock spurs.  Only the docks can gate,
               so every pair has to be brought to one; the loop is what carries them.
  micro_grid   a 3x3 lattice, 12 traps, every one of them gate-capable.  Nothing has to
               travel far, and the junctions are what it costs.

Both are generated here rather than checked into `arch/`, which holds nine reference
devices from published designs and should not gain teaching ones.

    python Compiler/bridge/micro_demo.py            # build, compile, verify, compare
    python Compiler/bridge/micro_demo.py --gif      # and render the two clips
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPILER = ROOT / "Compiler"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.arch import Architecture  # noqa: E402
from qccd.cost.models import corrected_model  # noqa: E402
from qccd.ir.source_map import build as build_source  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402
from qccd.verify import verify  # noqa: E402

BUILD = COMPILER / "build"
OUT = BUILD / "out"
EXE = COMPILER / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"
QASM = COMPILER / "examples" / "micro.qasm"

#: The clip window for each device: `(start, n)`.  Both open on the first pulse rather than
#: on `init`, and both close just after the SECOND `cx` is cleared -- the same two circuit
#: statements on each device.  The clips are therefore different lengths, and that is the
#: comparison: the grid discharges in 17 instructions what the ring needs 26 for.
DEVICES = {
    "micro_ring": (lambda: Machine.ring(4, 2, verticals=4, name="micro_ring"), (2, 26)),
    "micro_grid": (lambda: Machine.grid(3, 3, name="micro_grid"), (2, 17)),
}


def build_devices() -> None:
    for name, (gen, _) in DEVICES.items():
        gen().save(str(BUILD / f"{name}.arch.json"))
        run([sys.executable, str(COMPILER / "bridge" / "export_arch.py"),
             str(BUILD / f"{name}.arch.json"), "-o", str(BUILD / f"{name}.expanded.json")])


def run(cmd, **kw):
    got = subprocess.run([str(c) for c in cmd], capture_output=True, text=True,
                         cwd=COMPILER, **kw)
    if got.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed:\n{got.stdout}\n{got.stderr}")
    return got.stdout


def compile_one(name: str) -> dict:
    """Compile, cool, verify, and check R10.  Returns what the comparison needs."""
    pre = OUT / f"micro_{name}"
    run([EXE, "compile", QASM, "--arch", BUILD / f"{name}.expanded.json", "-o", pre])
    run([sys.executable, "bridge/insert_cooling.py", f"{pre}.tsir.json",
         "--arch", BUILD / f"{name}.arch.json", "-o", f"{pre}.cooled.tsir.json"])

    arch = Architecture.from_json(
        json.loads((BUILD / f"{name}.arch.json").read_text(encoding="utf-8")))
    prog = TSIR.load(f"{pre}.cooled.tsir.json")
    model = corrected_model()
    rep = verify(prog, arch, model, check_metrics=False)
    res, rules = rep.result, rep.rules.summary()

    cert = json.loads(Path(f"{pre}.qcert.json").read_text(encoding="utf-8"))
    src = build_source(prog, cert, QASM)

    # small enough that the PROVED checker decides it in under a second, so this demo
    # reports R10 `passed` rather than the `partial` a big program has to settle for
    qc = BUILD / f"qc_micro_{name}.json"
    run([sys.executable, "bridge/mk_qcheck_input.py", pre,
         "--arch", BUILD / f"{name}.expanded.json", "-o", qc])
    r10 = run([sys.executable, "bridge/check_cert.py", pre, "--qasm", QASM,
               "--arch", BUILD / f"{name}.arch.json", "--qcheck", qc])
    transport = sum(1 for i in prog.instructions if i.type == "simd")
    hops = sum(len(i.participants) for i in prog.instructions if i.type == "simd")
    return {
        "device": name, "arch": arch, "prog": prog, "source": src,
        "traps": len(list(arch.device.sites())),
        "junctions": sum(1 for n in arch.device.nodes if arch.device.degree(n) >= 3),
        "instructions": len(prog), "transport": transport, "hops": hops,
        "cost": res.total_cost, "steps": res.total_steps, "us": res.total_us,
        "passed": len(rules["passed"]), "failed": rules["failed"],
        "unrealised": cert["unrealised"],
        "r10": "passed" if "R10 passed" in r10 else
               "partial" if "R10 partial" in r10 else "FAILED",
    }


def table(rows: list[dict]) -> str:
    head = ("metric", *(r["device"] for r in rows))
    def line(label, key, fmt="{}"):
        return (label, *(fmt.format(r[key]) for r in rows))
    body = [
        line("traps", "traps"), line("junctions", "junctions"),
        line("hardware instructions", "instructions"),
        line("transport instructions", "transport"),
        line("ion-hops", "hops"),
        line("cost", "cost", "{:,.0f}"), line("machine steps", "steps", "{:,}"),
        line("runtime (ms)", "us", "{:,.3f}"),
        line("rules passed", "passed"), line("R10", "r10"),
    ]
    rows_ = [head, *body]
    w = [max(len(r[c]) for r in rows_) for c in range(len(head))]
    sep = "  ".join("-" * x for x in w)
    out = ["  ".join(c.ljust(x) for c, x in zip(head, w)), sep]
    out += ["  ".join(c.ljust(x) for c, x in zip(r, w)) for r in body]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--gif", action="store_true", help="also render the two clips")
    ap.add_argument("--width", type=int, default=760)
    ap.add_argument("--stage", type=int, default=250,
                    help="cap the stage at this many pixels tall")
    a = ap.parse_args(argv)

    build_devices()
    rows = [compile_one(n) for n in DEVICES]

    src = rows[0]["source"]
    print(f"{QASM.name}: {len(src['ops'])} statements on "
          f"{max(max(o['q']) for o in src['ops']) + 1} qubits\n")
    print(table(rows))
    for r in rows:
        if r["failed"] or r["unrealised"]:
            print(f"\n  {r['device']}: FAILED {r['failed']} unrealised {r['unrealised']}")
            return 1

    us = {r["device"]: r["us"] for r in rows}
    a_, b_ = rows[0]["device"], rows[1]["device"]
    faster, slower = (a_, b_) if us[a_] < us[b_] else (b_, a_)
    print(f"\n  {faster} runs the same circuit {us[slower] / us[faster]:.2f}x faster "
          f"than {slower}, on the same number of traps.")

    if a.gif:
        sys.path.insert(0, str(ROOT / "tools"))
        from make_gif import render_compiled  # noqa: PLC0415
        for r in rows:
            start, n = DEVICES[r["device"]][1]
            render_compiled(
                OUT / f"micro_{r['device']}.cooled.tsir.json", QASM,
                ROOT / "docs" / "img" / f"{r['device']}.gif",
                arch_path=BUILD / f"{r['device']}.arch.json",
                start=start, n=n, width=a.width, stage_h=a.stage)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
