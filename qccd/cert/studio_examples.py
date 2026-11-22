"""Circuits compiled by QCCDCert for a studio machine, for the Compilation page.

    python -m qccd.cert.studio_examples

For each circuit: QCCDCert compiles it for the machine's hardware program
(`qccd.cert.driver.compile_for_machine`), and this project's extension then inserts cooling
(`qccd.compile.insert_cooling`), which the studio's replay needs for its heat rules (R7,
R7c).  Both programmes are written: `prog.tsir.json` is QCCDCert's target code, the one
Theorem `verified_compilation` covers; `prog.cooled.tsir.json` is the same code with cooling
added, which the page plays and the studio's replay judges.  The site build reads these files
and needs no toolchain.
"""

from __future__ import annotations

import json
from pathlib import Path

from .driver import SPREAD, compile_for_machine
from .toolchain import ROOT

OUT = ROOT / "qccd" / "site" / "compiled" / "cert" / "studio"
SRC = ROOT / "qccd" / "site" / "compiled"
DEVICE = "grid2x3"

#: (id, statement, what it shows).  The circuits are the ones in qccd/site/compiled/<id>/.
CIRCUITS = [
    ("bell", "h q[0]; cx q[0],q[1]; measure q -> c;", "a Hadamard, a CNOT that needs transport, two readouts"),
    ("h", "h q[0];", "a Hadamard"),
    ("x", "x q[0];", "a bit flip"),
    ("y", "y q[0];", "a bit flip with a phase"),
    ("z", "z q[0];", "a phase flip"),
    ("s", "s q[0];", "a quarter turn about z"),
    ("t", "t q[0];", "an eighth of a turn about z"),
    ("rx", "rx(0.5) q[0];", "a rotation about x by 0.5"),
    ("ry", "ry(0.5) q[0];", "a rotation about y by 0.5"),
    ("rz", "rz(0.7854) q[0];", "a rotation about z by 0.7854"),
    ("cx", "cx q[0],q[1];", "the entangler"),
    ("cz", "cz q[0],q[1];", "a controlled phase"),
    ("swap", "swap q[0],q[1];", "three CNOTs"),
    ("measure", "measure q[0] -> c[0];", "a readout"),
    ("reset", "reset q[0];", "optical pumping to 0"),
]


def publishable(prog: dict) -> dict:
    """The cooling pass records the repository root it ran in (`meta.prov.root`): blank it,
    as the course's shipped copies do, so no local path is published."""
    prov = (prog.get("meta") or {}).get("prov")
    if isinstance(prov, dict) and "root" in prov:
        prov["root"] = ""
    return prog


def build(out: Path = OUT) -> list[dict]:
    from ..arch import Architecture
    from ..compile import insert_cooling
    from ..cost import corrected_model
    from ..ir.tsir import TSIR
    from ..verify import verify
    arch = Architecture.from_json(json.loads((SRC / f"{DEVICE}.arch.json").read_text(encoding="utf-8")))
    model = corrected_model()
    rows = []
    for gid, stmt, what in CIRCUITS:
        folder = Path(out) / gid
        r = compile_for_machine(arch, model, SRC / gid / "circuit.qasm", folder, extra_rules=[SPREAD])
        prog = TSIR.from_json(r.tsir)
        cooled = insert_cooling(prog, arch, model)
        cp = cooled.program
        (folder / "prog.cooled.tsir.json").write_text(json.dumps(publishable(cp.to_json()), indent=1) + "\n", encoding="utf-8")
        rep = verify(cp, arch, model, check_metrics=False).summary()
        row = {"id": gid, "statement": stmt, "what": what, "device": DEVICE,
               "finish_us": r.compilation.finish, "parallel_us": r.compilation.parallel.get("finish"),
               "instructions": len(r.compilation.execution["instructions"]),
               "pulses": [ln for ln in r.compilation.lines["pulses"]],
               "cools": cooled.n_cools, "cooled_us": rep["metrics"]["runtime_us"],
               "studio_failed": rep["rules"]["failed"], "studio_passed": len(rep["rules"]["passed"]),
               "notes": r.translation.notes}
        (folder / "row.json").write_text(json.dumps(row, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        rows.append(row)
        print(f"  {gid:8s} {row['finish_us']:>5} us  {row['instructions']:>3} instr  cools {row['cools']}  "
              f"studio failed {row['studio_failed']}")
    (Path(out) / "rows.json").write_text(json.dumps(rows, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return rows


#: Part D of the course compares micro.qasm on the shipped machines; QCCDCert compiles the
#: ones without a closed loop (the others keep the project's rotation extension).
MICRO_ARCHES = ("grid9x9", "deck_unit_cell")


def build_micro() -> list[str]:
    """micro.qasm compiled by QCCDCert (plus the cooling extension) for each loop-free shipped
    machine, as Compiler/examples/micro/qccdcert/micro_<arch>.{tsir,cooled.tsir,qcert}.json --
    the files the course's companion page (lesson D1) and its D2 table read."""
    from ..arch import Architecture
    from ..compile import insert_cooling
    from ..cost import corrected_model
    from ..ir.tsir import TSIR
    from ..viz.course import MICRO_QASM, QCCDCERT
    import subprocess
    QCCDCERT.mkdir(parents=True, exist_ok=True)
    model = corrected_model()
    done = []
    for stem in MICRO_ARCHES:
        # the committed machine, not a working-tree edit another session may be making
        text = subprocess.run(["git", "show", f"HEAD:arch/{stem}.arch.json"], cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8", check=True).stdout
        arch = Architecture.from_json(json.loads(text))
        work = ROOT / "out" / "cert" / f"micro_{stem}"
        r = compile_for_machine(arch, model, MICRO_QASM, work)
        cooled = insert_cooling(TSIR.from_json(r.tsir), arch, model).program
        (QCCDCERT / f"micro_{stem}.tsir.json").write_text(json.dumps(r.tsir, indent=1) + "\n", encoding="utf-8")
        (QCCDCERT / f"micro_{stem}.cooled.tsir.json").write_text(json.dumps(publishable(cooled.to_json()), indent=1) + "\n",
                                                                encoding="utf-8")
        (QCCDCERT / f"micro_{stem}.qcert.json").write_text(json.dumps(r.certificate, indent=1) + "\n",
                                                          encoding="utf-8")
        print(f"  micro on {stem}: {len(r.compilation.execution['instructions'])} events, {r.compilation.finish} us")
        done.append(stem)
    return done


if __name__ == "__main__":
    import sys
    if "--micro" in sys.argv:
        build_micro()
    else:
        build()
