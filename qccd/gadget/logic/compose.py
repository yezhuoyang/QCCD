"""The compositional sign-off: what makes a whole program correct when it is too big to flatten.

The flat sign-off (`flat.py`) simulates a whole schedule, branch by branch.  A program with
dozens of magic states has more branches than anyone can enumerate, and its corrections make
frames that are products of bits, not parities; it is checked in parts instead
(docs/ARCHITECTURE.md §1, §6), and every part is a check that ran:

1. **places** -- every op the schedule runs is verified as hardware and as logic (G9);
2. **gadgets** -- the S̄ and √X̄ gadgets are S and √X in every branch, with the corrections
   the scheduler writes (`contract.check_pi4`);
3. **gates** -- every gate the program calls is its contract in every branch, its own calls
   counted as theirs (`contract.check_gates`), and every call is its gate's body with fresh
   names (`algorithm.py` expands nothing else);
4. **composition** -- the floorplan and the schedule obey every machine rule (F1-F9, G1-G14):
   ions go where the gates send them, nothing runs into anything, every bit is used after it
   exists, every syndrome is decoded;
5. **semantics** -- the program, every call counted as its contract, computes what it should:
   the probability of every assignment of its top-level outcomes, exactly.  A program whose
   calls name no contract (a gadget lowered from FormalRV is not one named gate) or that has
   too many outcomes to enumerate is decided symbolically instead, when it is Clifford with
   Pauli feedback (`symbolic.py`): every outcome as an affine expression of the random ones,
   on the expanded program -- every branch at once, exactly.

Each part is reported with what it found; the whole is signed off only if all ran and held.
"""

from __future__ import annotations

from .contract import check_gates, check_pi4, program_outcomes

__all__ = ["compositional_sign_off"]


def _symbolic(alg) -> dict | None:
    from .symbolic import NotClifford, expr_text, run
    try:
        out = run(alg)
    except NotClifford as exc:
        return {"passed": False, "method": "symbolic", "why": str(exc)}
    free = sorted({v for e in out.values() for v in e if v != "1"})
    return {"passed": True, "method": "symbolic stabilizer simulation of the expanded program: "
                                      "every branch at once",
            "outcomes": {k: expr_text(v) for k, v in out.items()}, "random": len(free),
            "deterministic": sorted(k for k, v in out.items() if not (set(v) - {"1"}))}


def compositional_sign_off(alg, gir: dict, rules: dict, *, flat: dict | None = None,
                           outcomes: list[str] | None = None, semantics: dict | None = None) -> dict:
    """`semantics`, when given, is a verdict the caller decided against the program's own
    source (a program lowered from FormalRV is checked against its `.prog`: qccd/fullstack); it
    replaces the semantics part, labelled as such."""
    used = {i.kind for i in alg.instructions}
    called = {c["gate"] for c in alg.calls}
    gadgets = [check_pi4(k) for k in ("s", "sx") if k in used]
    uncontracted = sorted(g for g in called if alg.gates[g].contract is None)
    # only a call that names no contract needs the expansion decided: every other program is
    # checked exactly as before, its calls as their contracts, its top-level outcomes enumerated
    symbolic = bool(uncontracted)
    gates = [r for r in check_gates(alg) if r["gate"] in called and r["gate"] not in uncontracted]
    for g in uncontracted:
        gates.append({"gate": g, "contract": None, "passed": True,
                      "why": "names no contract (a gadget lowered from FormalRV): its calls are "
                             "expanded, and the program's semantics is decided on the expansion"})
    if semantics is not None:
        sem_part = {**semantics, "passed": bool(semantics.get("passed")),
                    "method": "decided against the program's source: " + str(semantics.get("method", ""))}
    elif symbolic:
        sem_part = _symbolic(alg)
    else:
        sem = program_outcomes(alg, outcomes=outcomes)
        sem_part = {"passed": bool(sem["distribution"]), **{k: v for k, v in sem.items()
                                                           if k != "distribution"},
                    "distribution": [{"outcomes": dict(zip(sem["outcomes"], k)), "p": p}
                                     for k, p in sem["distribution"].items()]}
    g9 = "G9" in rules.get("passed", [])
    composition = not rules.get("failed") and not rules.get("unchecked")
    parts = {
        "places": {"passed": g9, "what": "every op the schedule runs is verified (G9)"},
        "gadgets": {"passed": all(g["passed"] for g in gadgets), "rows": gadgets},
        "gates": {"passed": all(g["passed"] for g in gates), "rows": gates,
                  "calls": {g: sum(1 for c in alg.calls if c["gate"] == g) for g in sorted(called)}},
        "composition": {"passed": composition, "held": rules.get("passed", []),
                        "failed": rules.get("failed", [])},
        "semantics": sem_part,
    }
    out = {"kind": "compositional", "passed": all(p["passed"] for p in parts.values()),
           "parts": parts, "refused": len(gir.get("refused") or [])}
    if flat is not None:
        out["flat"] = {"passed": flat.get("passed"), "skipped": flat.get("skipped")}
    if gir.get("refused"):
        out["passed"] = False
    return out
