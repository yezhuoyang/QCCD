"""C6's mutation suite: seed defects into a valid certificate and require rejection.

`Compiler/PLAN.md` C6 states the criterion plainly -- *a checker that accepts a mutant is
a checker that proves nothing*.  So each mutation below is a way a compiler can actually
be wrong, applied to a certificate the checker has already accepted, and the only
acceptable outcome is a rejection naming the right conjunct.

## One mutation is expected NOT to be caught, and that is the point

`swap_operands` exchanges the two ions of a gate.  The Lean checker verifies
**co-location** -- both operands in one gate-capable trap -- and swapping them changes
neither ion's position, so it passes, correctly, because O1 does not claim to see operand
*order*.  A CX is not symmetric, so the swap is a real bug; it is caught by the
**stabilizer tableau** in `check_cert.py`, which is the other half of R10.

Reporting that split honestly is more useful than tuning the suite until everything is
caught by one layer.  The suite therefore records, per mutation, which layer is supposed
to catch it, and fails if a layer misses one it claimed.

    python Compiler/bridge/mutate_cert.py build/qcheck_ghz8_grid9x9.json
"""

from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
QCHECK = HERE.parent / "lean" / ".lake" / "build" / "bin" / "qcheck.exe"


# ------------------------------------------------------------------ mutations
#
# Each returns a mutated document, or None when the input has nothing to mutate.

def drop_gate(d):
    if len(d["gates"]) < 1:
        return None
    d["gates"] = d["gates"][1:]
    return d


def duplicate_gate(d):
    if not d["gates"]:
        return None
    d["gates"] = [d["gates"][0]] + d["gates"]
    return d


def shift_gate_time(d):
    if not d["gates"]:
        return None
    d["gates"][-1]["t"] = 0
    return d


def teleport_move(d):
    if not d["moves"]:
        return None
    traps = sorted({h["to"] for h in d["hops"]})
    cur = d["moves"][0]["to"]
    far = next((t for t in traps
                if {"from": d["moves"][0]["from"], "to": t} not in d["hops"] and t != cur),
               None)
    if far is None:
        return None
    d["moves"][0]["to"] = far
    return d


def wrong_departure(d):
    if not d["moves"]:
        return None
    traps = sorted({h["to"] for h in d["hops"]})
    other = next((t for t in traps if t != d["moves"][0]["from"]), None)
    if other is None:
        return None
    d["moves"][0]["from"] = other
    return d


def ungateable_site(d):
    if not d["gates"]:
        return None
    traps = sorted({h["to"] for h in d["hops"]})
    bad = next((t for t in traps if t not in d["gate_sites"]), None)
    if bad is None:
        return None  # every trap can gate on this device
    d["gates"][0]["site"] = bad
    return d


def alias_qubits(d):
    if len(d["map"]) < 2:
        return None
    ks = sorted(d["map"], key=lambda k: int(k))
    d["map"][ks[1]] = d["map"][ks[0]]
    return d


def mark_unrealised(d):
    d["unrealised"] = [0]
    return d


def unsort_moves(d):
    if len(d["moves"]) < 2:
        return None
    d["moves"] = list(reversed(d["moves"]))
    if d["moves"][0]["t"] <= d["moves"][1]["t"]:
        return None  # already non-decreasing reversed; nothing was broken
    return d


def reorder_gates(d):
    """Give a later gate an earlier cycle than one it depends on."""
    if len(d["gates"]) < 2:
        return None
    a, b = d["gates"][0], d["gates"][1]
    a["t"], b["t"] = max(a["t"], b["t"]), min(a["t"], b["t"])
    if a["t"] == b["t"]:
        return None
    return d


def swap_dependent_gates(d):
    """Exchange which circuit op two witnesses realise, leaving everything else alone.

    This is the mutation that aims squarely at `RespectsOrder`, and it is the one worth
    having now that the rule admits commuting reorderings. Nothing about co-location is
    disturbed -- each witness keeps its ions, its trap and its cycle -- so `GatesLegal`
    still passes and the ONLY thing wrong is that two ops which do not commute are
    realised back to front. If the commutation exemption were too generous, this is what
    would slip through.
    """
    ops = {o["i"]: o for o in d["circuit_ops"]}

    def commutes(x, y):
        if x["name"] != "cx" or y["name"] != "cx":
            return False
        (c1, t1), (c2, t2) = x["qubits"], y["qubits"]
        return (c1 == c2 and t1 != t2) or (t1 == t2 and c1 != c2)

    for i, ga in enumerate(d["gates"]):
        for gb in d["gates"][i + 1:]:
            oa, ob = ops.get(ga["dag"]), ops.get(gb["dag"])
            if oa is None or ob is None or ga["t"] == gb["t"]:
                continue
            if not set(oa["qubits"]) & set(ob["qubits"]) or commutes(oa, ob):
                continue
            ga["dag"], gb["dag"] = gb["dag"], ga["dag"]
            return d
    return None


def swap_operands(d):
    """Exchange a gate's two operands.  O1 cannot see this -- the tableau can."""
    for g in d["gates"]:
        if len(g["ions"]) == 2:
            g["ions"] = [g["ions"][1], g["ions"][0]]
            return d
    return None


MUTATIONS = [
    ("drop_gate", drop_gate, "lean"),
    ("duplicate_gate", duplicate_gate, "lean"),
    ("shift_gate_time", shift_gate_time, "lean"),
    ("teleport_move", teleport_move, "lean"),
    ("wrong_departure", wrong_departure, "lean"),
    ("ungateable_site", ungateable_site, "lean"),
    ("alias_qubits", alias_qubits, "lean"),
    ("mark_unrealised", mark_unrealised, "lean"),
    ("unsort_moves", unsort_moves, "lean"),
    ("reorder_gates", reorder_gates, "lean"),
    ("swap_dependent_gates", swap_dependent_gates, "lean"),
    ("swap_operands", swap_operands, "tableau"),
]


def run_qcheck(doc: dict, tmp: Path) -> tuple[bool, str]:
    tmp.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    r = subprocess.run([str(QCHECK), str(tmp)], capture_output=True, text=True)
    accepted = "ACCEPTED" in r.stdout
    why = ""
    for line in r.stdout.splitlines():
        if line.strip().startswith("- "):
            why = line.strip()[2:]
            break
    return accepted, why


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("input", help="a qcheck input the checker already ACCEPTS")
    args = ap.parse_args(argv)

    if not QCHECK.exists():
        print(f"missing {QCHECK}; run `cd Compiler/lean && lake build qcheck`")
        return 2

    base = json.loads(Path(args.input).read_text(encoding="utf-8"))
    tmp = Path(args.input).with_suffix(".mutant.json")

    ok, _ = run_qcheck(base, tmp)
    if not ok:
        print("the UNMUTATED certificate is rejected; the suite would prove nothing")
        return 2
    print(f"baseline accepted: {base['circuit']} on {base['arch']}")
    print(f"{'mutation':<18} {'expected':<9} {'verdict':<9} why")
    print("-" * 74)

    caught = missed = skipped = 0
    for name, fn, layer in MUTATIONS:
        mutant = fn(copy.deepcopy(base))
        if mutant is None:
            print(f"{name:<18} {layer:<9} {'n/a':<9} nothing to mutate in this circuit")
            skipped += 1
            continue
        accepted, why = run_qcheck(mutant, tmp)
        if layer == "lean":
            if accepted:
                print(f"{name:<18} {layer:<9} {'ACCEPTED':<9} <-- MISSED")
                missed += 1
            else:
                print(f"{name:<18} {layer:<9} {'rejected':<9} {why}")
                caught += 1
        else:
            # the tableau's job; the Lean checker is expected to pass it
            mark = "as expected" if accepted else "also caught"
            print(f"{name:<18} {layer:<9} {'accepted' if accepted else 'rejected':<9} "
                  f"{mark} -- O1 does not see operand order; "
                  f"check_cert.py's tableau does")

    tmp.unlink(missing_ok=True)
    print()
    print(f"the proved checker caught {caught} of the {caught + missed} mutations it "
          f"claims, missed {missed}, {skipped} not applicable")
    print("MUTATION PASS" if missed == 0 else "MUTATION FAIL")
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())
