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

## Two things the schedule decides, made explicit

`--steady-state` resets an ancilla before EVERY check, not only when it is reused.  In a
running memory experiment every ancilla arrives carrying the previous round's outcome, so
all 144 checks need a reset regardless of how many ancillas there are.  Without the flag,
`resets = 144 - ancillas`, and since a reset costs 3x a measurement, three quarters of a
naive ancilla sweep's fidelity trend is "we did fewer resets" (`Codesign/findings/q05`).

`--schedule <json>` takes the binding and the contact order from a code-aware scheduler
(`Codesign/scripts/bb_schedule.py`) instead of the round-robin default: which ancilla
serves which check, and in what order the contacts happen.  The statements are emitted in
schedule order so the compiler's dependency DAG carries exactly the order the scheduler
chose -- and `Codesign/scripts/esm_check.py` then verifies the circuit still measures
exactly the stabilizers, which an X/Z interleaving can silently break.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.codes.bb import gross_code  # noqa: E402


def emit_default(checks, n_data: int, n_anc: int, steady: bool) -> list[str]:
    """Round-robin binding, checks in index order, contacts in member order."""
    L: list[str] = []
    for k, ch in enumerate(checks):
        anc = n_data + (k % n_anc)
        if steady or k >= n_anc:
            # the ancilla is carrying a previous check's outcome: it has been read out
            # already (or will be, in steady state, by the previous round) -- reset it
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
    return L


def emit_scheduled(checks, n_data: int, sched: dict, steady: bool) -> list[str]:
    """The scheduler's binding and contact order, statement by statement.

    Each ancilla's check is opened (reset, and H for an X check) at its first contact and
    closed (H, measure) after its last, so per ancilla the program is the same
    reset/H/cx.../H/measure chain as the default -- only the interleaving across ancillas
    and the order within a check follow the schedule.
    """
    contacts = sched["contacts"]  # [offset, check index, data qubit, ancilla qubit]
    n_anc = sched["n_ancillas"]
    first_use = {n_data + j: True for j in range(n_anc)}
    remaining = {ci: len(checks[ci].members) for ci in range(len(checks))}
    opened: set[int] = set()
    L: list[str] = []
    for _off, ci, d, anc in contacts:
        ch = checks[ci]
        if ci not in opened:
            opened.add(ci)
            if steady or not first_use[anc]:
                L.append(f"reset q[{anc}];")
            first_use[anc] = False
            if ch.type == "X":
                L.append(f"h q[{anc}];")
        if ch.type == "X":
            L.append(f"cx q[{anc}],q[{d}];")
        else:
            L.append(f"cx q[{d}],q[{anc}];")
        remaining[ci] -= 1
        if remaining[ci] == 0:
            if ch.type == "X":
                L.append(f"h q[{anc}];")
            L.append(f"measure q[{anc}] -> c[{ci}];")
    missing = [ci for ci, r in remaining.items() if r]
    if missing:
        raise SystemExit(f"schedule leaves {len(missing)} checks incomplete: {missing[:8]}")
    return L


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", required=True)
    ap.add_argument("--ancillas", type=int, default=24,
                    help="how many ancillas the round is allowed; they are reused")
    ap.add_argument("--checks", type=int, default=None,
                    help="use only the first N checks (a smaller round, for scaling runs)")
    ap.add_argument("--steady-state", action="store_true",
                    help="reset before every check, as a running memory experiment must")
    ap.add_argument("--schedule", default=None,
                    help="a .schedule.json from Codesign/scripts/bb_schedule.py: binding "
                         "and contact order from the code-aware scheduler")
    args = ap.parse_args(argv)

    code = gross_code()
    checks = code.checks[: args.checks] if args.checks else code.checks
    n_data = code.n
    sched = None
    if args.schedule:
        sched = json.loads(Path(args.schedule).read_text(encoding="utf-8"))
        if args.checks:
            raise SystemExit("--schedule and --checks do not combine")
        if sched["n_data"] != n_data:
            raise SystemExit(f"schedule is for {sched['n_data']} data qubits, code has {n_data}")
        n_anc = sched["n_ancillas"]
    else:
        n_anc = args.ancillas
    n_q = n_data + n_anc

    L = [
        "OPENQASM 2.0;",
        'include "qelib1.inc";',
        f"// {code.name}: {n_data} data, {len(checks)} checks of weight "
        f"{sorted({len(c) for c in checks})}",
        f"// {n_anc} ancillas, reused via mid-circuit measure + reset"
        + (" (steady state: a reset before every check)" if args.steady_state else ""),
    ]
    if sched:
        L.append(f"// schedule: {sched['placement']} placement, {sched['binding']} binding "
                 f"on {sched['arch']} -- {sched['stats']['batches']} batches predicted")
    L += [f"qreg q[{n_q}];", f"creg c[{len(checks)}];"]
    if sched:
        L += emit_scheduled(checks, n_data, sched, args.steady_state)
    else:
        L += emit_default(checks, n_data, n_anc, args.steady_state)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")

    n_cx = sum(len(c) for c in checks)
    n_reset = sum(1 for l in L if l.startswith("reset"))
    print(f"{code.name}: {len(checks)} checks, {n_cx} contacts, {n_reset} resets")
    print(f"  {n_data} data + {n_anc} ancilla = {n_q} qubits "
          f"({len(checks)} checks share {n_anc} ancillas)")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
