"""`python -m qccd.site.compile_examples` -- the basic gates, compiled and verified.

The Compilation page shows every basic QASM gate going through the real pipeline: the
OCaml compiler (`qccdc_cli.exe compile`), the cooling pass, the 22 replayed rules, the
checker input, and R10 by the proved Lean checker and the stabilizer tableau (or the exact
unitary outside the Clifford fragment).  Those tools live on the machine that builds the
repository, not on the machine that builds the site, so this script runs them once and
writes what they produced under `qccd/site/compiled/<id>/`; `python -m qccd site` reads
that directory and never needs a toolchain.  Every artefact on the page is one of these
files, unedited.

    qccd/site/compiled/<device>.arch.json     the devices the gates are compiled onto
    qccd/site/compiled/<id>/circuit.qasm      the input
    qccd/site/compiled/<id>/prog.tsir.json    what the compiler emitted
    qccd/site/compiled/<id>/prog.qcert.json   its certificate
    qccd/site/compiled/<id>/prog.cooled.tsir.json   after the cooling pass
    qccd/site/compiled/<id>/prog.qcheck.json  the checker's input (certificate + device facts)
    qccd/site/compiled/<id>/rules.json        check_tsir --json: the 22 rules
    qccd/site/compiled/<id>/verdict.json      check_cert --json: R10, both halves
    qccd/site/compiled/<id>/meta.json         title, note, the compiler's own summary line
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
COMPILER = ROOT / "Compiler"
QCCDC = COMPILER / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"
QCHECK = COMPILER / "lean" / ".lake" / "build" / "bin" / "qcheck.exe"
BRIDGE = COMPILER / "bridge"
OUT = HERE / "compiled"

def header(n: int = 2) -> str:
    return f'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[{n}];\ncreg c[{n}];\n'


HEADER = header(2)

#: The small devices the gates are compiled onto, by name: the generator call that builds
#: each, and one line about it.  The same devices carry the Language and Rules pages.
DEVICES: dict[str, tuple[str, dict, str]] = {
    "chain4": ("chain", {"n": 4}, "a four-site linear register C0–C3, every site a trap, no junctions"),
    "race8": ("racetrack", {"straight": 4}, "an eight-site racetrack S0–S7: one loop of trap sites, no junctions"),
    "grid2x3": ("grid", {"a": 2, "b": 3}, "a 2×3 lattice: T-junctions and corners, one trap on every wire"),
    "ring6d": ("ring", {"width": 3, "height": 2, "verticals": 2}, "the six-site ring with two dock spurs"),
}

#: (id, the circuit body, title, what to notice, device).  The first is the worked example
#: the page walks through stage by stage; the rest are the basic gates, one each, spread
#: over the devices so that transport is seen on a line, a loop, a lattice and a dock.
GATES: list[tuple[str, str, str, str, str]] = [
    ("bell", "h q[0];\ncx q[0],q[1];\nmeasure q -> c;", "A Bell pair",
     "the worked example: a Hadamard, a CNOT that needs transport, and two measurements", "chain4"),
    ("h", "h q[0];", "h", "a half turn about the diagonal axis: one turn about z, then one pulse", "chain4"),
    ("x", "x q[0];", "x", "a bit flip: 0 and 1 swap over. One pulse", "race8"),
    ("y", "y q[0];", "y", "a bit flip with a phase: one pulse, about a different axis", "grid2x3"),
    ("z", "z q[0];", "z", "a phase flip: a half turn about z. Free on some machines, three pulses on this one", "ring6d"),
    ("s", "s q[0];", "s", "a quarter turn about z: three pulses here, for the same reason as z", "chain4"),
    ("t", "t q[0];", "t", "an eighth of a turn about z. The check for this one compares the exact rotation, not a shortcut", "race8"),
    ("rx", "rx(0.5) q[0];", "rx(θ)", "any angle at all, about x: one pulse", "grid2x3"),
    ("ry", "ry(0.5) q[0];", "ry(θ)", "any angle at all, about y: one pulse", "ring6d"),
    ("rz", "rz(0.7854) q[0];", "rz(θ)", "any angle about z, which this machine pays for in pulses", "chain4"),
    ("cx", "cx q[0],q[1];", "cx", "the entangler: carry one ion to the other, then one two-ion pulse with four single-ion pulses around it", "grid2x3"),
    ("cz", "cz q[0],q[1];", "cz", "a CNOT with two more pulses around it, on the same pair of ions", "race8"),
    ("swap", "swap q[0],q[1];", "swap", "three CNOTs in a row, so three two-ion pulses at one trap", "ring6d"),
    ("measure", "measure q[0] -> c[0];", "measure", "reading the qubit out, in a trap that can do it: a laser asks the ion whether it is 0 or 1", "chain4"),
    ("reset", "reset q[0];", "reset", "putting the qubit back to 0, with a laser, in the same kind of trap", "race8"),
]


#: The Algorithm section's two circuits: four qubits each, chosen so that the decisions the
#: section explains are visible in the programme -- two gates in one layer sharing an
#: instruction, a meeting trap chosen for room rather than distance, a hop that cannot share
#: a cycle with another because it is a different waveform, and the placer's candidates
#: scored and hill-climbed.  Same pipeline, same files, same checks as the gates.
ALGO: list[tuple[str, str, str, str, str]] = [
    ("algo_par", "h q[0];\nh q[2];\ncx q[0],q[1];\ncx q[2],q[3];\ncx q[1],q[2];\nmeasure q -> c;",
     "Two Bell pairs, then joined",
     "four layers: two Hadamards fire in one instruction, two CNOTs run side by side in two traps, then one CNOT joins the pairs",
     "grid2x3"),
    ("algo_ghz", "h q[0];\ncx q[0],q[1];\ncx q[1],q[2];\ncx q[2],q[3];\nmeasure q -> c;",
     "A GHZ chain",
     "every CNOT shares a qubit with the next: the placer lays the chain along the register, and no ion is sent home while the next gate still needs it",
     "chain4"),
    # compiled by the ROTATION pass (see ALGO_CMD): one ancilla parked at a dock, three data
    # qubits riding the loop, three contacts -- the shape of a stabilizer measurement
    ("algo_rot", "h q[0];\ncx q[0],q[1];\ncx q[0],q[2];\ncx q[0],q[3];\nh q[0];\nmeasure q[0] -> c[0];",
     "A three-qubit parity check, by rotation",
     "the ancilla q0 sits at a dock, the data ride the loop; the loop turns twice and every CNOT is a contact at the dock",
     "ring6d"),
]

#: The Algorithm section's fourth circuit: a Toffoli, so that the lowering of §2 can be drawn
#: from the certificate's (lowered) op list and checked against the exact unitary.
ALGO.append(("algo_ccx", "ccx q[0],q[1],q[2];", "A Toffoli, lowered",
             "one three-qubit gate becomes six CNOTs and nine single-qubit gates before placement; R10 is decided against the exact unitary",
             "grid2x3"))

#: Which compiler command an example uses; `compile` unless listed here.
ALGO_CMD = {"algo_rot": "rotate"}

#: A circuit the Algorithm section quotes only through `qccdc decompose`: how a Toffoli
#: lowers.  The tool's output is written beside the examples, unedited.
LOWERING_QASM = 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[3];\ncreg c[3];\nccx q[0],q[1],q[2];\n'


def sh(cmd: list, timeout: int = 900) -> tuple[int, str, float]:
    t0 = time.time()
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout,
                       cwd=str(COMPILER))
    return r.returncode, (r.stdout or "") + (r.stderr or ""), time.time() - t0


def device(name: str = "ring6d") -> tuple[Path, Path]:
    """One of DEVICES, written as its architecture document and its expanded form."""
    from ..api import Machine
    OUT.mkdir(parents=True, exist_ok=True)
    arch = OUT / f"{name}.arch.json"
    exp = OUT / f"{name}.expanded.json"
    gen, params, _ = DEVICES[name]
    if not arch.exists():
        m = getattr(Machine, gen)(**params, name=name)
        arch.write_text(json.dumps(m.arch.to_json(), indent=1) + "\n", encoding="utf-8", newline="\n")
    rc, log, _ = sh([sys.executable, BRIDGE / "export_arch.py", arch, "-o", exp])
    if rc != 0:
        raise SystemExit("export_arch failed:\n" + log[-800:])
    return arch, exp


def compile_one(gid: str, body: str, title: str, note: str, arch: Path, exp: Path, dev: str = "ring6d",
                n_qubits: int = 2) -> dict:
    d = OUT / gid
    d.mkdir(parents=True, exist_ok=True)
    qasm = d / "circuit.qasm"
    qasm.write_text(header(n_qubits) + body + "\n", encoding="utf-8", newline="\n")
    prefix = d / "prog"
    rc, log, secs = sh([QCCDC, ALGO_CMD.get(gid, "compile"), qasm, "--arch", exp, "-o", prefix])
    if rc != 0 or not (d / "prog.tsir.json").exists():
        raise SystemExit(f"{gid}: the compiler refused:\n{log[-800:]}")
    summary = next((ln.strip() for ln in log.splitlines() if "instructions" in ln and "layers" in ln), "")
    # the compiler's own account of its decisions (the placement candidates and their
    # scores, the hill-climb, a tight device): shown on the page as it was printed
    notes = [ln.strip()[2:] for ln in log.splitlines() if ln.startswith("  - ")]
    regime = re.search(r"\((\S+) regime\)", log)
    cooled = d / "prog.cooled.tsir.json"
    rc, clog, _ = sh([sys.executable, BRIDGE / "insert_cooling.py", d / "prog.tsir.json", "--arch", arch, "-o", cooled])
    if rc != 0:
        raise SystemExit(f"{gid}: cooling failed:\n{clog[-800:]}")
    # the repository ships this file: its provenance must not name this machine's checkout
    from qccd.ir.provenance import blank_root_file
    blank_root_file(cooled)
    rc, rlog, _ = sh([sys.executable, BRIDGE / "check_tsir.py", cooled, "--arch", arch, "--model", "corrected", "--json"])
    rules = json.loads(rlog[rlog.index("{"):]) if "{" in rlog else {"error": rlog[-400:]}
    (d / "rules.json").write_text(json.dumps(rules, indent=1) + "\n", encoding="utf-8", newline="\n")
    sh([sys.executable, BRIDGE / "mk_qcheck_input.py", prefix, "--arch", exp, "-o", d / "prog.qcheck.json"])
    rc, vlog, lsecs = sh([sys.executable, BRIDGE / "check_cert.py", prefix, "--qasm", qasm, "--arch", arch,
                          "--qcheck", d / "prog.qcheck.json", "--json"], timeout=1800)
    verdict = json.loads(vlog[vlog.index("{"):]) if "{" in vlog else {"error": vlog[-400:]}
    (d / "verdict.json").write_text(json.dumps(verdict, indent=1) + "\n", encoding="utf-8", newline="\n")
    meta = {"id": gid, "title": title, "note": note, "compiler": summary, "device": dev, "device_about": DEVICES[dev][2],
            "notes": notes, "regime": regime.group(1) if regime else "", "n_qubits": n_qubits,
            "compile_seconds": round(secs, 2), "check_seconds": round(lsecs, 1)}
    (d / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(f"  {gid:8s} {summary[:70]:70s} rules {len(rules.get('passed', []))} passed, "
          f"{len(rules.get('failed', []))} failed | R10 {verdict.get('R10')} ({lsecs:.0f} s)", flush=True)
    return meta


def load_compiled(which: list[tuple[str, str, str, str, str]] = GATES) -> list[dict]:
    """What the page reads: every example's files, in GATES order; empty if none exist."""
    out = []
    for gid, body, title, note, dev in which:
        d = OUT / gid
        if not (d / "verdict.json").exists():
            continue
        rd = lambda name: json.loads((d / name).read_text(encoding="utf-8"))
        out.append({"id": gid, "dir": d, "qasm": (d / "circuit.qasm").read_text(encoding="utf-8"),
                    "meta": rd("meta.json"), "rules": rd("rules.json"), "verdict": rd("verdict.json"),
                    "cert": rd("prog.qcert.json"), "tsir": rd("prog.cooled.tsir.json"),
                    "raw": rd("prog.tsir.json")})
    return out


def load_algo() -> list[dict]:
    return load_compiled(ALGO)


def main(argv=None) -> int:
    """`--only id [id ...]` compiles a subset; everything else is left as it is."""
    args = list(sys.argv[1:] if argv is None else argv)
    only = set(args[args.index("--only") + 1:]) if "--only" in args else None
    for exe in (QCCDC, QCHECK):
        if not exe.exists():
            raise SystemExit(f"{exe} is not built; see Compiler/README.md")
    paths = {name: device(name) for name in DEVICES}
    todo = [(g, 2) for g in GATES] + [(g, 1 + max(int(x) for x in re.findall(r"q\[(\d+)\]", g[1]))) for g in ALGO]
    if only is not None:
        todo = [(g, n) for g, n in todo if g[0] in only]
    print(f"{len(DEVICES)} devices; {len(todo)} circuits")
    for (gid, body, title, note, dev), n in todo:
        compile_one(gid, body, title, note, *paths[dev], dev=dev, n_qubits=n)
    if only is None or "lowering" in only:
        q = OUT / "ccx.qasm"
        q.write_text(LOWERING_QASM, encoding="utf-8", newline="\n")
        rc, log, _ = sh([QCCDC, "decompose", q])
        (OUT / "lowering.txt").write_text(log, encoding="utf-8", newline="\n")
        print("  lowering ", log.strip().splitlines()[-2:])
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
