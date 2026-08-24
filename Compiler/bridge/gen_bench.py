"""Generate the C1 benchmark corpus: named circuits plus seeded random QASM.

The random half writes QASM *text* directly rather than exporting from qiskit, and that
is the whole point.  qiskit's exporter emits a narrow, fully-indexed dialect: one qreg,
no whole-register broadcasts, no `gate` declarations, no comments, no expressions beyond
a bare float.  Testing against only what it emits would leave most of the grammar
unexercised and would have found none of the bugs this corpus is meant to find.

So the generator deliberately produces the awkward forms:

  * several `qreg`s and `creg`s, so bit numbering depends on declaration order;
  * whole-register broadcast -- `h q;`, `cx q, r;`, `measure q -> c;`, `barrier q;` --
    which is where the flattening rules actually live;
  * expressions: `pi/2`, `-pi/4`, `2*pi/3`, `sqrt(2)`, `2^3`, nested parens;
  * user-declared `gate`s, which must stay one DAG node rather than being inlined;
  * line comments in inconvenient places.

`/* ... */` is deliberately absent: it is not in the OpenQASM 2.0 grammar and qiskit
rejects it.  Our lexer accepts it as an extension (real hand-written files contain it),
but a shared corpus has to be readable by both sides, so the generator does not emit it.

Both front ends then read the same text, and `diff_dag.py` compares what they built.

    python Compiler/bridge/gen_bench.py -o Compiler/bench --random 500 --seed 7
"""

from __future__ import annotations

import argparse
import math
import random
from pathlib import Path

HEADER = 'OPENQASM 2.0;\ninclude "qelib1.inc";\n'

# (name, nparams, nqubits) -- kept to gates qiskit's LEGACY_CUSTOM_INSTRUCTIONS keeps
# under their qelib1 spelling, so a mismatch means a parse bug and not a rename.
GATES_1Q_0P = ["x", "y", "z", "h", "s", "sdg", "t", "tdg", "id"]
GATES_1Q_1P = ["rx", "ry", "rz", "u1"]
GATES_1Q_2P = ["u2"]
GATES_1Q_3P = ["u3"]
GATES_2Q_0P = ["cx", "cy", "cz", "ch", "swap"]
GATES_2Q_1P = ["crz", "cu1", "rzz"]
GATES_2Q_3P = ["cu3"]
GATES_3Q_0P = ["ccx", "cswap"]

EXPRS = [
    "pi", "pi/2", "-pi/4", "2*pi/3", "0.5", "-0.25", "1e-3", "3", "-2",
    "sqrt(2)", "sin(0.5)", "cos(pi/3)", "ln(2)", "exp(0.1)", "2^3",
    "(pi/2 + 0.1)", "pi*2 - 1", "-(pi/8)", "((1+2)*3)/4",
]


# ------------------------------------------------------------------ named circuits


def ghz(n: int = 8) -> str:
    s = HEADER + f"qreg q[{n}];\ncreg c[{n}];\nh q[0];\n"
    s += "".join(f"cx q[{i}],q[{i + 1}];\n" for i in range(n - 1))
    return s + "measure q -> c;\n"


def qft(n: int = 6) -> str:
    s = HEADER + f"qreg q[{n}];\ncreg c[{n}];\n"
    for j in range(n):
        s += f"h q[{j}];\n"
        for k in range(j + 1, n):
            s += f"cu1({math.pi}/{2 ** (k - j)}) q[{k}],q[{j}];\n"
    for j in range(n // 2):
        s += f"swap q[{j}],q[{n - 1 - j}];\n"
    return s + "measure q -> c;\n"


def clifford_chain(n: int = 12, depth: int = 20, seed: int = 0) -> str:
    """Pure Clifford: the fragment D1 makes the compiler's verified core."""
    rng = random.Random(seed)
    s = HEADER + f"qreg q[{n}];\ncreg c[{n}];\n"
    for _ in range(depth):
        for _ in range(n // 2):
            if rng.random() < 0.5:
                s += f"{rng.choice(['h', 's', 'sdg', 'x', 'y', 'z'])} q[{rng.randrange(n)}];\n"
            else:
                a, b = rng.sample(range(n), 2)
                s += f"{rng.choice(['cx', 'cz', 'swap'])} q[{a}],q[{b}];\n"
    return s + "measure q -> c;\n"


def broadcast_zoo() -> str:
    """Every broadcast form in one file, which is where flattening goes wrong."""
    return (
        HEADER
        + "// several registers: bit numbering follows declaration order\n"
        "qreg a[3];\nqreg b[3];\nqreg solo[1];\ncreg ca[3];\ncreg cb[3];\n"
        "h a;                  // whole-register broadcast\n"
        "cx a,b;               // pairwise, index by index\n"
        "cx solo[0],a;         // one fixed bit against a register\n"
        "rz(pi/2) b;\n"
        "barrier a,b;          // ONE node over six qubits\n"
        "reset a;\n"
        "ccx a[0],b[1],solo[0];\n"
        "measure a -> ca;\n"
        "measure b[2] -> cb[0];\n"
    )


def custom_gates() -> str:
    """User-declared gates must survive as single DAG nodes, not be inlined."""
    return (
        HEADER
        + "gate mycx a,b { cx a,b; }\n"
        "gate rot(theta) a { rz(theta) a; ry(theta/2) a; }\n"
        "gate bell a,b { h a; cx a,b; }\n"
        "qreg q[4];\ncreg c[4];\n"
        "bell q[0],q[1];\n"
        "mycx q[1],q[2];\n"
        "rot(pi/3) q[3];\n"
        "rot(-pi/6) q[0];\n"
        "bell q[2],q[3];\n"
        "measure q -> c;\n"
    )


def conditional() -> str:
    return (
        HEADER
        + "qreg q[3];\ncreg c[3];\n"
        "h q[0];\nmeasure q[0] -> c[0];\n"
        "if (c==1) x q[1];\n"
        "if (c==3) cx q[1],q[2];\n"
        "measure q -> c;\n"
    )


NAMED = {
    "ghz8": lambda: ghz(8),
    "ghz32": lambda: ghz(32),
    "qft6": lambda: qft(6),
    "clifford12": lambda: clifford_chain(12, 20, seed=1),
    "clifford40": lambda: clifford_chain(40, 30, seed=2),
    "broadcast_zoo": broadcast_zoo,
    "custom_gates": custom_gates,
}

# `conditional` is NOT in NAMED.  qiskit 2.x lowers `if (c==k) op;` into an `if_else`
# instruction carrying a QuantumCircuit body, so its canonical form has no counterpart in
# ours and a comparison would be meaningless rather than failing.  Our parser still reads
# the construct -- feed-forward is needed for real QEC rounds -- and it is written out
# here as a parse-only fixture so the support is exercised and visible.
PARSE_ONLY = {"conditional": conditional}


# ------------------------------------------------------------------ random circuits


def random_qasm(rng: random.Random) -> str:
    n_qregs = rng.randint(1, 3)
    widths = [rng.randint(1, 5) for _ in range(n_qregs)]
    # a uniform width makes whole-register two-qubit broadcasts legal
    if rng.random() < 0.5:
        widths = [widths[0]] * n_qregs
    qnames = [f"q{i}" for i in range(n_qregs)]
    cw = rng.randint(1, 4)

    s = HEADER
    if rng.random() < 0.3:
        s += "// a leading comment\n// and a second one\n"
    for name, w in zip(qnames, widths):
        s += f"qreg {name}[{w}];\n"
    s += f"creg c[{cw}];\n"

    # a user gate, sometimes
    have_custom = rng.random() < 0.4
    if have_custom:
        s += "gate mygate(t) a,b { rz(t) a; cx a,b; }\n"

    total = sum(widths)
    bits = [(nm, i) for nm, w in zip(qnames, widths) for i in range(w)]

    def bit() -> str:
        nm, i = rng.choice(bits)
        return f"{nm}[{i}]"

    def two_distinct():
        a = rng.randrange(total)
        b = rng.randrange(total)
        while b == a:
            b = rng.randrange(total)
        return f"{bits[a][0]}[{bits[a][1]}]", f"{bits[b][0]}[{bits[b][1]}]"

    for _ in range(rng.randint(3, 40)):
        r = rng.random()
        if r < 0.30:
            s += f"{rng.choice(GATES_1Q_0P)} {bit()};\n"
        elif r < 0.42:
            g = rng.choice(GATES_1Q_1P)
            s += f"{g}({rng.choice(EXPRS)}) {bit()};\n"
        elif r < 0.47:
            s += f"u2({rng.choice(EXPRS)},{rng.choice(EXPRS)}) {bit()};\n"
        elif r < 0.52:
            s += (f"u3({rng.choice(EXPRS)},{rng.choice(EXPRS)},"
                  f"{rng.choice(EXPRS)}) {bit()};\n")
        elif r < 0.68 and total >= 2:
            a, b = two_distinct()
            s += f"{rng.choice(GATES_2Q_0P)} {a},{b};\n"
        elif r < 0.74 and total >= 2:
            a, b = two_distinct()
            s += f"{rng.choice(GATES_2Q_1P)}({rng.choice(EXPRS)}) {a},{b};\n"
        elif r < 0.78 and total >= 3:
            idx = rng.sample(range(total), 3)
            args = ",".join(f"{bits[i][0]}[{bits[i][1]}]" for i in idx)
            s += f"{rng.choice(GATES_3Q_0P)} {args};\n"
        elif r < 0.83:
            # whole-register broadcast of a 1Q gate
            s += f"{rng.choice(GATES_1Q_0P)} {rng.choice(qnames)};\n"
        elif r < 0.87 and n_qregs >= 2 and len(set(widths)) == 1:
            a, b = rng.sample(qnames, 2)
            s += f"cx {a},{b};\n"
        elif r < 0.90:
            s += f"barrier {rng.choice(qnames)};\n"
        elif r < 0.93:
            s += f"reset {bit()};\n"
        elif r < 0.96 and have_custom and total >= 2:
            a, b = two_distinct()
            s += f"mygate({rng.choice(EXPRS)}) {a},{b};\n"
        else:
            s += f"measure {bit()} -> c[{rng.randrange(cw)}];\n"
        if rng.random() < 0.05:
            s += "// noise\n"
    return s


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--random", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args(argv)

    out = Path(args.out)
    (out / "random").mkdir(parents=True, exist_ok=True)

    for name, fn in NAMED.items():
        (out / f"{name}.qasm").write_text(fn(), encoding="utf-8")
    print(f"{len(NAMED)} named circuits -> {out}")

    (out / "parse_only").mkdir(parents=True, exist_ok=True)
    for name, fn in PARSE_ONLY.items():
        (out / "parse_only" / f"{name}.qasm").write_text(fn(), encoding="utf-8")
    print(f"{len(PARSE_ONLY)} parse-only circuit(s) -> {out / 'parse_only'}")

    rng = random.Random(args.seed)
    for i in range(args.random):
        (out / "random" / f"r{i:04d}.qasm").write_text(random_qasm(rng), encoding="utf-8")
    print(f"{args.random} random circuits (seed {args.seed}) -> {out / 'random'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
