"""The example corpus: circuits a QCCD architect would actually want compiled.

`bench/` (from C1) exists to stress the *parser* -- 500 seeded random files full of
awkward grammar. This is the other corpus: programs with structure, chosen so that every
shipped architecture has something it can host and so the matrix in `run_matrix.py`
measures something worth measuring.

Three groups:

**Small** -- `bell2` exists so `stationary_chain`, a two-trap device, is not excluded from
the matrix by default. A platform that can only be tested on its big machines is a
platform whose small ones are untested.

**Structured** -- GHZ, QFT, Bernstein-Vazirani, a ripple-carry adder. Different
interaction graphs: a path, all-to-all, a star, a chain of Toffolis. Placement and routing
behave very differently on each, which is the point of having more than one.

**Syndrome extraction** -- Steane [[7,1,3]] and the distance-3 surface code, written the
way the hardware runs them: prepare the ancilla, entangle it with the stabilizer's
support, read it out. These are the circuits this platform exists for, and they are the
ones whose compiled cost is worth comparing across architectures.

    python Compiler/bridge/gen_examples.py -o Compiler/examples
"""

from __future__ import annotations

import argparse
from pathlib import Path

HEAD = 'OPENQASM 2.0;\ninclude "qelib1.inc";\n'


def _reg(nq: int, nc: int | None = None) -> str:
    out = f"qreg q[{nq}];\n"
    if nc:
        out += f"creg c[{nc}];\n"
    return out


# ------------------------------------------------------------------ small

def bell2() -> str:
    return HEAD + _reg(2, 2) + "h q[0];\ncx q[0],q[1];\nmeasure q -> c;\n"


def ghz(n: int) -> str:
    s = HEAD + _reg(n, n) + "h q[0];\n"
    s += "".join(f"cx q[{i}],q[{i + 1}];\n" for i in range(n - 1))
    return s + "measure q -> c;\n"


# ------------------------------------------------------------------ structured

def qft(n: int) -> str:
    """All-to-all interaction: the hardest thing to place on a line."""
    s = HEAD + _reg(n, n)
    for j in range(n):
        s += f"h q[{j}];\n"
        for k in range(j + 1, n):
            s += f"cu1(pi/{2 ** (k - j)}) q[{k}],q[{j}];\n"
    for j in range(n // 2):
        s += f"swap q[{j}],q[{n - 1 - j}];\n"
    return s + "measure q -> c;\n"


def bernstein_vazirani(n: int, secret: int = 0b10110) -> str:
    """A star: every query qubit touches one target.  Placement should notice."""
    s = HEAD + _reg(n + 1, n)
    s += f"x q[{n}];\nh q[{n}];\n"
    s += "".join(f"h q[{i}];\n" for i in range(n))
    s += "".join(f"cx q[{i}],q[{n}];\n" for i in range(n) if (secret >> i) & 1)
    s += "".join(f"h q[{i}];\n" for i in range(n))
    s += "".join(f"measure q[{i}] -> c[{i}];\n" for i in range(n))
    return s


def ripple_adder(bits: int = 3) -> str:
    """A chain of Toffolis: exercises the 3-qubit decomposition end to end."""
    n = 3 * bits + 1
    s = HEAD + _reg(n, bits + 1)
    a = list(range(bits))
    b = list(range(bits, 2 * bits))
    cin = list(range(2 * bits, 3 * bits + 1))
    for i in range(bits):
        s += f"ccx q[{a[i]}],q[{b[i]}],q[{cin[i + 1]}];\n"
        s += f"cx q[{a[i]}],q[{b[i]}];\n"
        s += f"ccx q[{cin[i]}],q[{b[i]}],q[{cin[i + 1]}];\n"
        s += f"cx q[{cin[i]}],q[{b[i]}];\n"
    for i in range(bits):
        s += f"measure q[{b[i]}] -> c[{i}];\n"
    s += f"measure q[{cin[bits]}] -> c[{bits}];\n"
    return s


# ------------------------------------------------------------------ syndrome extraction

def _esm(n_data: int, x_stabs: list[list[int]], z_stabs: list[list[int]],
         name: str) -> str:
    """One round of syndrome extraction, written the way the hardware runs it.

    One ancilla per stabilizer, entangled with its support and read out.  Ancillas are
    NOT reused between stabilizers -- a QASM file has already fixed its qubit count, and
    reuse is a code-level choice made before the compiler sees anything.
    """
    n_anc = len(x_stabs) + len(z_stabs)
    s = HEAD + f"// {name}: {n_data} data, {n_anc} ancilla, one ESM round\n"
    s += _reg(n_data + n_anc, n_anc)
    anc = n_data
    for k, sup in enumerate(x_stabs):
        s += f"h q[{anc}];\n"
        for d in sup:
            s += f"cx q[{anc}],q[{d}];\n"
        s += f"h q[{anc}];\nmeasure q[{anc}] -> c[{k}];\n"
        anc += 1
    for k, sup in enumerate(z_stabs):
        for d in sup:
            s += f"cx q[{d}],q[{anc}];\n"
        s += f"measure q[{anc}] -> c[{len(x_stabs) + k}];\n"
        anc += 1
    return s


def steane_esm() -> str:
    """Steane [[7,1,3]]: 7 data, 6 ancilla."""
    x = [[3, 4, 5, 6], [1, 2, 5, 6], [0, 2, 4, 6]]
    return _esm(7, x, list(x), "steane [[7,1,3]]")


def surface17_esm() -> str:
    """Distance-3 rotated surface code: 9 data, 8 ancilla."""
    # data laid out 0..8 as a 3x3 grid, row-major
    z = [[0, 1, 3, 4], [4, 5, 7, 8], [2, 5], [3, 6]]
    x = [[1, 2, 4, 5], [3, 4, 6, 7], [0, 1], [7, 8]]
    return _esm(9, x, z, "surface d=3 (17 qubits)")


def repetition_esm(n: int = 9) -> str:
    """The distance-n repetition code: a path interaction graph, so a linear device
    should do well and a grid should show no advantage.  That contrast is the point."""
    z = [[i, i + 1] for i in range(n - 1)]
    return _esm(n, [], z, f"repetition d={n}")


EXAMPLES = {
    "bell2": bell2,
    "ghz4": lambda: ghz(4),
    "ghz16": lambda: ghz(16),
    "qft8": lambda: qft(8),
    "bv6": lambda: bernstein_vazirani(6),
    "adder3": lambda: ripple_adder(3),
    "steane_esm": steane_esm,
    "surface17_esm": surface17_esm,
    "rep9_esm": lambda: repetition_esm(9),
}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for name, fn in EXAMPLES.items():
        text = fn()
        (out / f"{name}.qasm").write_text(text, encoding="utf-8")
        nq = text.split("qreg q[")[1].split("]")[0]
        n2 = sum(text.count(g) for g in ("cx ", "cu1(", "ccx ", "swap ", "cz "))
        print(f"  {name:<16} {nq:>3} qubits, ~{n2:>3} multi-qubit gates")
    print(f"{len(EXAMPLES)} examples -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
