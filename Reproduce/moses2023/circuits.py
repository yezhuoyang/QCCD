"""The H2 benchmark circuits, taken from the paper's artifact and rewritten EXACTLY into
gates our compiler knows.  Nothing from the artifact is stored in this repository: the
artifact (github.com/CQCL/quantinuum-hardware-h2-benchmark, "All Rights Reserved") is
read where it was cloned, and the circuits are written to a scratch directory.

    python Reproduce/moses2023/circuits.py <artifact clone> <out dir>

Which circuits (H2-T1 rows of 2305.03828 Table I):
    ghz    ghz/data/GHZ_N32.json, the Z-basis circuit (all 32 Z keys hold one text)
    mb     mirror_benchmarking/data/MB_N32.json, the ten l = 10 circuits
    qv     quantum_volume/data/n16_H2-1_raw_results.json, all 200 `qv_circs`
    rcs    random_circuit_sampling/data/RCS_N32_Experiment_10Circuits.json, `qasm_compiled`
    qaoa   qaoa/data/QAOA_N32_p2_Experiment.json, all 27 (one per optimiser step)

The rewrite (every identity is checked numerically by check_rewrites.py, and against the
artifact's own ideal outputs where a simulation is affordable):
    U1q(t, f) q          -> u3(t, f - pi/2, pi/2 - f) q           exp(-i t/2 (cos f X + sin f Y))
    RZZ(t) a,b           -> h a; h b; rxx(t) a,b; h a; h b         exp(-i t/2 ZZ)
    ZZ a,b               -> the same with t = pi/2                 U_ZZ(pi/2), Fig. 5 caption
    Rxxyyzz(x, y, z) a,b -> rxx(x); sdg sdg rxx(y) s s; h h rxx(z) h h, a zero angle dropped
                                                                   exp(-i/2 (x XX + y YY + z ZZ))
    barrier (diagnostic copies only) -> removed
`rxx(t)` is the one gate our compiler emits as a single MS pulse (Compiler/ocaml/lib/
gateset_composites.ml), so one H2 two-qubit gate stays one MS: the H2 gate is "a
phase-sensitive MS gate sandwiched between 1Q wrapper pulses" (Sec. II.C).  A rewrite to
cx would also be exact, but costs two MS per non-Clifford U_ZZ(t) and so doubles the
two-qubit count the comparison is about.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import operator
import re
import sys
from pathlib import Path

PI = math.pi
STMT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?\s+([^;]*);\s*$")
TWO_Q_NATIVE = ("ZZ", "RZZ", "Rxxyyzz", "cx")


def ev(expr: str) -> float:
    """A QASM angle expression: numbers, pi, + - * / and parentheses only."""
    ops = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
           ast.Div: operator.truediv, ast.USub: operator.neg, ast.UAdd: operator.pos}

    def go(n):
        if isinstance(n, ast.Expression):
            return go(n.body)
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return float(n.value)
        if isinstance(n, ast.Name) and n.id == "pi":
            return PI
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            return ops[type(n.op)](go(n.left), go(n.right))
        if isinstance(n, ast.UnaryOp) and type(n.op) in ops:
            return ops[type(n.op)](go(n.operand))
        raise ValueError(f"unsupported angle expression {expr!r}")

    return go(ast.parse(expr.strip(), mode="eval"))


def is_zero_rotation(t: float) -> bool:
    """exp(-i t/2 PP) is the identity up to a global phase iff t is a multiple of 2 pi."""
    r = math.remainder(t, 2 * PI)
    return abs(r) < 1e-9


def num(x: float) -> str:
    return repr(float(x))


def rewrite(text: str, *, strip_barriers: bool = False) -> tuple[str, dict]:
    """(rewritten QASM, census).  Raises on any statement it does not understand."""
    out: list[str] = []
    census = {"two_qubit_gates_h2": 0, "zero_angle_dropped": 0, "by_source_gate": {},
              "barriers": 0, "measure": 0}

    def bump(k):
        census["by_source_gate"][k] = census["by_source_gate"].get(k, 0) + 1

    def zz_like(basis: str, t: float, a: str, b: str) -> None:
        pre, post = {"X": ([], []), "Y": (["sdg"], ["s"]), "Z": (["h"], ["h"])}[basis]
        for g in pre:
            out.append(f"{g} {a};")
            out.append(f"{g} {b};")
        out.append(f"rxx({num(t)}) {a},{b};")
        for g in post:
            out.append(f"{g} {a};")
            out.append(f"{g} {b};")
        census["two_qubit_gates_h2"] += 1

    saw_qelib = False
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("//"):
            continue
        if s.startswith("OPENQASM"):
            out.append(s)
            continue
        if s.startswith("include"):
            if "qelib1.inc" in s:
                saw_qelib = True
                out.append(s)
            continue                      # hqslib1(.inc/_dev.inc): every use is rewritten below
        if s.startswith(("qreg", "creg")):
            if not saw_qelib:
                out.append('include "qelib1.inc";')
                saw_qelib = True
            out.append(s)
            continue
        if s.startswith("measure"):
            census["measure"] += 1
            out.append(s)
            continue
        m = STMT.match(s)
        if not m:
            raise ValueError(f"cannot read statement {s!r}")
        name, params, args = m.group(1), m.group(2), m.group(3)
        ps = [ev(p) for p in params.split(",")] if params else []
        qs = [a.strip() for a in args.split(",")]
        bump(name)
        if name == "barrier":
            census["barriers"] += 1
            if not strip_barriers:
                out.append(s)
            continue
        if name == "U1q":
            t, f = ps
            out.append(f"u3({num(t)},{num(f - PI / 2)},{num(PI / 2 - f)}) {qs[0]};")
        elif name == "RZZ":
            zz_like("Z", ps[0], *qs)
        elif name == "ZZ":
            zz_like("Z", PI / 2, *qs)
        elif name == "Rxxyyzz":
            for basis, t in zip("XYZ", ps):
                if is_zero_rotation(t):
                    census["zero_angle_dropped"] += 1
                else:
                    zz_like(basis, t, *qs)
        elif name == "cx":
            census["two_qubit_gates_h2"] += 1
            out.append(s)
        elif len(qs) == 1 and name in {"h", "x", "y", "z", "s", "sdg", "t", "tdg", "rx", "ry",
                                       "rz", "u1", "u2", "u3", "u", "p", "id"}:
            out.append(s)
        else:
            raise ValueError(f"no exact rewrite for {s!r}")
    return "\n".join(out) + "\n", census


def select(art: Path) -> list[tuple[str, str, str]]:
    """[(family, circuit id, source QASM)] for the H2-T1 rows."""
    rows: list[tuple[str, str, str]] = []
    d = json.loads((art / "ghz/data/GHZ_N32.json").read_text(encoding="utf-8"))
    ztexts = sorted({v for k, v in d["qasm"].items() if "'Z'" in k})
    assert len(ztexts) == 1, "the GHZ Z-basis keys should all hold one circuit"
    rows.append(("ghz", "ghz_n32", ztexts[0]))
    d = json.loads((art / "mirror_benchmarking/data/MB_N32.json").read_text(encoding="utf-8"))
    for k in sorted(d["qasm"], key=lambda k: ast.literal_eval(k)):
        ell, i = ast.literal_eval(k)
        if ell == 10:
            rows.append(("mb", f"mb_n32_l10_{i}", d["qasm"][k]))
    d = json.loads((art / "quantum_volume/data/n16_H2-1_raw_results.json").read_text(encoding="utf-8"))
    for i, t in enumerate(d["qv_circs"]):
        rows.append(("qv", f"qv_n16_{i:03d}", t))
    d = json.loads((art / "random_circuit_sampling/data/RCS_N32_Experiment_10Circuits.json")
                   .read_text(encoding="utf-8"))
    for k in sorted(d["qasm_compiled"], key=int):
        rows.append(("rcs", f"rcs_n32_{int(k)}", d["qasm_compiled"][k]))
    d = json.loads((art / "qaoa/data/QAOA_N32_p2_Experiment.json").read_text(encoding="utf-8"))
    for k in sorted(d["qasm"], key=int):
        rows.append(("qaoa", f"qaoa_n32_p2_{int(k):02d}", d["qasm"][k]))
    return rows


def main(argv: list[str]) -> int:
    art, out = Path(argv[0]), Path(argv[1])
    for sub in ("source", "rewritten", "nobarrier"):
        (out / sub).mkdir(parents=True, exist_ok=True)
    manifest = []
    for fam, cid, text in select(art):
        rw, census = rewrite(text)
        nb, _ = rewrite(text, strip_barriers=True)
        (out / "source" / f"{cid}.qasm").write_text(text, encoding="utf-8")
        (out / "rewritten" / f"{cid}.qasm").write_text(rw, encoding="utf-8")
        (out / "nobarrier" / f"{cid}.qasm").write_text(nb, encoding="utf-8")
        manifest.append({"family": fam, "id": cid,
                         "source_sha256": hashlib.sha256(text.encode()).hexdigest(),
                         "rewritten_sha256": hashlib.sha256(rw.encode()).hexdigest(),
                         **census})
    (out / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    by = {}
    for r in manifest:
        by.setdefault(r["family"], []).append(r["two_qubit_gates_h2"])
    for fam, v in by.items():
        print(f"{fam:5s} {len(v):4d} circuits  2Q gates min {min(v)} mean {sum(v) / len(v):.1f} "
              f"max {max(v)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
