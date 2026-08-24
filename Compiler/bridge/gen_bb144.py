"""C7's oracle target: BB [[144,12,12]] syndrome extraction, as QASM.

`qccd/compile/pipeline.py` compiles this code onto `ring144_24v` with a router written
for that ring and that code. This emits the *same task* as a circuit, so a general
compiler can be pointed at it and the two compared.

## Ancilla reuse is why it fits at all

144 checks with a dedicated ancilla each would need 288 qubits, and `ring144_24v` has 168
traps. The shipped pipeline does not use 288: it binds 144 check-arcs onto **24 docks**,
reusing each ancilla many times. A circuit expresses that with mid-circuit `measure` and
`reset`, which is how real ESM rounds are written anyway -- so `--ancillas 24` produces a
168-qubit circuit that fits the device exactly.

That number is the comparison's whole point. The specialised pipeline gets ancilla reuse
for free because it knows it is compiling a *code*; a general compiler is handed a circuit
in which the reuse has already been decided. `--ancillas` makes that decision explicit and
sweepable rather than hidden.

    python Compiler/bridge/gen_bb144.py -o Compiler/examples/bb144_esm.qasm --ancillas 24
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.codes.bb import gross_code  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--ancillas", type=int, default=24,
                    help="how many ancillas the round is allowed; they are reused")
    ap.add_argument("--checks", type=int, default=None,
                    help="use only the first N checks (a smaller round, for scaling runs)")
    args = ap.parse_args(argv)

    code = gross_code()
    checks = code.checks[: args.checks] if args.checks else code.checks
    n_data = code.n
    n_anc = args.ancillas
    n_q = n_data + n_anc

    L = [
        "OPENQASM 2.0;",
        'include "qelib1.inc";',
        f"// {code.name}: {n_data} data, {len(checks)} checks of weight "
        f"{sorted({len(c) for c in checks})}",
        f"// {n_anc} ancillas, reused via mid-circuit measure + reset",
        f"qreg q[{n_q}];",
        f"creg c[{len(checks)}];",
    ]

    for k, ch in enumerate(checks):
        anc = n_data + (k % n_anc)
        if k >= n_anc:
            # the ancilla is carrying a previous check's outcome: read it out and reset
            L.append(f"reset q[{anc}];")
        if ch.type == "X":
            L.append(f"h q[{anc}];")
            for d in ch.members:
                L.append(f"cx q[{anc}],q[{d}];")
            L.append(f"h q[{anc}];")
        else:
            for d in ch.members:
                L.append(f"cx q[{d}],q[{anc}];")
        L.append(f"measure q[{anc}] -> c[{k}];")

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")

    n_cx = sum(len(c) for c in checks)
    print(f"{code.name}: {len(checks)} checks, {n_cx} contacts")
    print(f"  {n_data} data + {n_anc} ancilla = {n_q} qubits "
          f"({len(checks)} checks share {n_anc} ancillas)")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
