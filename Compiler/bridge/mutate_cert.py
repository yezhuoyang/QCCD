"""C6's mutation suite: seed defects into a valid certificate and require rejection.

`Compiler/PLAN.md` C6 states the criterion plainly -- *a checker that accepts a mutant is
a checker that proves nothing*.  So each mutation below is a way a compiler can actually
be wrong, applied to a certificate the checker has already accepted, and the only
acceptable outcome is a rejection naming the right conjunct.

## One mutation is expected NOT to be caught, and that is the point

`swap_operands` exchanges the two ions of a gate's witness.  The Lean checker verifies that
a witness names its op's own ions, in any order (`OwnIons`), and that they are
**co-located** in one gate-capable trap; swapping them changes neither, so it passes,
correctly, because O1 does not claim to see operand *order*.  The mutated certificate
passes the whole of R10, also correctly: a witness names ions, not which is the control, so
the certificate alone says nothing wrong.  The compiler bug the mutation stands for -- a
programme that gates a CX's two ions the wrong way round -- is real (a CX is not symmetric),
and it is caught in the *programme*: the **stabilizer tableau** in `check_cert.py`, the
other half of R10, composes the emitted pulses and reports TABLEAU MISMATCH.

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
sys.path.insert(0, str(HERE))
from check_cert import find_qcheck  # noqa: E402

QCHECK = find_qcheck(HERE.parent / "lean") or HERE.parent / "lean" / ".lake" / "build" / "bin" / "qcheck"


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

    This is the mutation that aims squarely at `RespectsOrder`. Nothing about co-location
    is disturbed -- each witness keeps its ions, its trap and its cycle -- so `GatesLegal`
    still passes, and two ops which do not commute are realised back to front.

    Each witness now also names the other op's ions, which `OwnIons` refuses as well, so a
    rejection alone no longer shows the order check fired: the suite requires its reason.
    """
    return _swap_pair(d, commuting=False)


def swap_commuting_gates(d):
    """The same exchange, for two CNOTs that COMMUTE (a shared control or a shared target).

    The order clause used to admit exactly these, and the rotation compiler relied on it
    to batch a check's CNOTs by offset.  Since 2026-10-19 the compiler may not reorder a
    qubit's ops even where they commute -- the order is the circuit author's -- so this
    must be rejected for order too, not only for the ions it borrows.
    """
    return _swap_pair(d, commuting=True)


def _commutes(x, y):
    if x["name"] != "cx" or y["name"] != "cx":
        return False
    (c1, t1), (c2, t2) = x["qubits"], y["qubits"]
    return (c1 == c2 and t1 != t2) or (t1 == t2 and c1 != c2)


def _swap_pair(d, *, commuting: bool):
    ops = {o["i"]: o for o in d["circuit_ops"]}
    for i, ga in enumerate(d["gates"]):
        for gb in d["gates"][i + 1:]:
            oa, ob = ops.get(ga["dag"]), ops.get(gb["dag"])
            if oa is None or ob is None or ga["t"] == gb["t"]:
                continue
            if not set(oa["qubits"]) & set(ob["qubits"]) or _commutes(oa, ob) != commuting:
                continue
            ga["dag"], gb["dag"] = gb["dag"], ga["dag"]
            return d
    return None


def _positions_at(d, t):
    """Where every ion is at the start of cycle `t`, replayed as qcheck replays it: every
    move and rotation of an earlier cycle, a cycle's moves before its rotations."""
    pos = dict(d["init"])
    rots = d.get("rotations", [])
    events = sorted([(m["t"], 0, i) for i, m in enumerate(d["moves"])]
                    + [(r["t"], 1, i) for i, r in enumerate(rots)])
    for cyc, kind, i in events:
        if cyc >= t:
            break
        if kind == 0:
            pos[d["moves"][i]["ion"]] = d["moves"][i]["to"]
            continue
        nodes = d.get("loops", {}).get(rots[i]["loop"])
        if not nodes:
            continue
        slot = {s: k for k, s in enumerate(nodes)}
        for ion, site in list(pos.items()):
            if site in slot:
                pos[ion] = nodes[(slot[site] + rots[i]["delta"]) % len(nodes)]
    return pos


def foreign_ions(d):
    """A witness names other ions than its op's: as many as it named, all of them standing
    in one gate-capable trap at its cycle, none of them its op's.  Positions, trap, cycle
    and order all still check, so only `OwnIons` can see it -- before it, qcheck accepted
    this."""
    ops = {o["i"]: o for o in d["circuit_ops"]}
    gate_sites = set(d["gate_sites"])
    for g in d["gates"]:
        o = ops.get(g["dag"])
        if o is None or not g["ions"]:
            continue
        own = {d["map"].get(str(q)) for q in o["qubits"]}
        there: dict[str, list[str]] = {}
        for ion, site in _positions_at(d, g["t"]).items():
            if site in gate_sites and ion not in own:
                there.setdefault(site, []).append(ion)
        for site, ions in sorted(there.items()):
            if len(ions) >= len(g["ions"]):
                g["ions"], g["site"] = sorted(ions)[:len(g["ions"])], site
                return d
    return None


def swap_operands(d):
    """Exchange a gate's two operands in its witness.  O1 cannot see this, and need not:
    the bug it stands for (a programme with control and target exchanged) is in the pulses,
    where the tableau sees it."""
    for g in d["gates"]:
        if len(g["ions"]) == 2:
            g["ions"] = [g["ions"][1], g["ions"][0]]
            return d
    return None


#: (name, mutation, the layer that must catch it[, words its rejection must include])
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
    ("swap_dependent_gates", swap_dependent_gates, "lean", "out of order"),
    ("swap_commuting_gates", swap_commuting_gates, "lean", "out of order"),
    ("foreign_ions", foreign_ions, "lean", "other ions than"),
    ("swap_operands", swap_operands, "tableau"),
]


def run_qcheck(doc: dict, tmp: Path) -> tuple[bool, list[str]]:
    """The verdict, and every reason a rejection prints."""
    tmp.write_text(json.dumps(doc) + "\n", encoding="utf-8")
    r = subprocess.run([str(QCHECK), str(tmp)], capture_output=True, text=True)
    accepted = "ACCEPTED" in r.stdout
    return accepted, [ln.strip()[2:] for ln in r.stdout.splitlines() if ln.strip().startswith("- ")]


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
    for name, fn, layer, *must in MUTATIONS:
        mutant = fn(copy.deepcopy(base))
        if mutant is None:
            print(f"{name:<18} {layer:<9} {'n/a':<9} nothing to mutate in this circuit")
            skipped += 1
            continue
        accepted, reasons = run_qcheck(mutant, tmp)
        named = [w for w in reasons if must and must[0] in w]
        why = (named or reasons or [""])[0]
        if layer == "lean":
            if accepted:
                print(f"{name:<18} {layer:<9} {'ACCEPTED':<9} <-- MISSED")
                missed += 1
            elif must and not named:
                print(f"{name:<18} {layer:<9} {'rejected':<9} <-- MISSED: not for `{must[0]}` "
                      f"but {reasons}")
                missed += 1
            else:
                print(f"{name:<18} {layer:<9} {'rejected':<9} {why}")
                caught += 1
        else:
            # the tableau's job; the Lean checker is expected to pass it
            mark = "as expected" if accepted else "also caught"
            print(f"{name:<18} {layer:<9} {'accepted' if accepted else 'rejected':<9} "
                  f"{mark} -- O1 does not see operand order; the programme bug this "
                  f"stands for is in the pulses, where check_cert.py's tableau sees it")

    tmp.unlink(missing_ok=True)
    print()
    print(f"the proved checker caught {caught} of the {caught + missed} mutations it "
          f"claims, missed {missed}, {skipped} not applicable")
    print("MUTATION PASS" if missed == 0 else "MUTATION FAIL")
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())
