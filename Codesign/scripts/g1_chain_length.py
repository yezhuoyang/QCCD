"""G1 -- the chain-length term in the gate error, and what closing it did.

`Codesign/EVALUATION.md` G1.  `gate_error` was `eps0 + k*nbar` with no dependence on how
many ions share the trap, so a search over trap capacity would have driven it to R13's
hard cap of 15 and reported the cap as an optimum.  This reports the closed form, the
calibration that keeps it a refinement, and the measured effect on every verified program.

    python Codesign/scripts/g1_chain_length.py --json Codesign/data/g1.json

The three things it has to establish, in order:

1.  **At the reference chain length the new model IS the old one, exactly.**  Otherwise
    this is a replacement of a validated model, not a refinement of one, and every number
    downstream drifts from the 397,184 / 8,808 oracle.
2.  **A longer chain is never cheaper.**  The raw `N/ln N` has a minimum at `N = e`, so
    taken literally it discounts a 3-ion chain by 5.4 % -- a free lunch of exactly the
    kind G1 exists to remove.
3.  **R13's cap now costs something**, and by how much, because that is the whole point.
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.cost import corrected_model, t2_metrics  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402
from qccd.verify import replay  # noqa: E402

MATRIX = ROOT / "Compiler" / "build" / "matrix"
VERDICTS = [ROOT / "Compiler" / "build" / "matrix.json",
            ROOT / "Codesign" / "data" / "q01b_matrix.json"]


def _without_chain_term(arch):
    """The same architecture with `error_vs_chain` undeclared -- i.e. the pre-G1 model.

    Mutating a copy of the primitive rather than reimplementing the old formula, so the
    "before" number comes from the same code path the repository actually shipped. A
    reconstruction accumulates in a different order and differs in the last bits, which
    would report a dozen programs as "changed by G1" when nothing changed at all.
    """
    import copy

    before = copy.deepcopy(arch)
    spec = dict(before.primitives.scalar("ms_gate"))
    spec.pop("error_vs_chain", None)
    before.primitives.scalars["ms_gate"] = spec
    return before


def verified_pairs() -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for v in VERDICTS:
        if v.exists():
            out += [(r["circuit"], r["device"])
                    for r in json.loads(v.read_text(encoding="utf-8"))
                    if r.get("status") == "ok"]
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)

    model = corrected_model()
    arch = load(str(ROOT / "arch" / "ring144_24v.arch.json"))
    spec = arch.primitives.scalar("ms_gate")
    eps0 = 1.0 - float(spec["fidelity_at_n0"])
    slope = float(str(spec["error_vs_quanta"]).partition(":")[2])
    n_ref = int(str(spec["error_vs_chain"]).partition(":")[2])
    cap = float(spec["max_quanta"])

    print("THE CLOSED FORM")
    print("  eps(nbar, N)  =  eps0' + kappa f(N) (2 nbar + 1),   f(N) = N / ln N")
    print(f"  declared: eps0 = {eps0:.3e}   slope = {slope:.1e}/quantum   "
          f"N_ref = {n_ref}   R7 cap = {cap:g} quanta")
    kappa = slope / (2.0 * (n_ref / math.log(n_ref)))
    print(f"  derived : kappa = slope / (2 f(N_ref)) = {kappa:.4e}")
    print(f"            eps0' = eps0 - kappa f(N_ref) = eps0 - slope/2 = "
          f"{eps0 - slope / 2:.3e}")
    print("  NOTHING IS FITTED.  Requiring the new model to equal the old one at N_ref,")
    print("  for every nbar, pins both constants -- so D1's 'largest single assumption'")
    print("  is the SHAPE (Murali et al., ISCA 2020) and not a free scale.")

    print()
    print("1 - AT THE REFERENCE LENGTH IT IS THE OLD MODEL, EXACTLY")
    print(f"    {'nbar':>6}{'legacy eps0+k*nbar':>21}{'new, N=N_ref':>16}{'identical?':>12}")
    ok = True
    for nb in (0.0, 0.1, 0.25, 0.5, 0.75, 1.0):
        legacy = eps0 + slope * nb
        new = model.gate_error(arch, nb, n_ref)
        ok &= new == legacy
        print(f"    {nb:>6}{legacy:>21.12e}{new:>16.6e}{str(new == legacy):>12}")
    print(f"    -> {'bit-exact at every point' if ok else 'DRIFT: this is not a refinement'}")

    print()
    print("2 - A LONGER CHAIN IS NEVER CHEAPER")
    print(f"    {'N':>4}{'raw f(N)/f(2)':>15}{'used':>8}{'eps at nbar=0':>15}"
          f"{'eps at R7 cap':>15}")
    curve = []
    for N in (2, 3, 4, 5, 6, 8, 10, 12, 15):
        raw = (N / math.log(N)) / (n_ref / math.log(n_ref))
        e0 = model.gate_error(arch, 0.0, N)
        e1 = model.gate_error(arch, cap, N)
        curve.append({"N": N, "raw_ratio": raw, "eps_cold": e0, "eps_hot": e1})
        flag = "  <- clamped" if raw < 1.0 else ""
        print(f"    {N:>4}{raw:>15.3f}{max(raw, 1.0):>8.3f}{e0:>15.4e}{e1:>15.4e}{flag}")
    print("    the raw form dips below 1 at N=3 because N/lnN has a minimum at N=e;")
    print("    the monotone envelope refuses that discount rather than handing it to a search")

    print()
    print("3 - WHAT R13'S CAP NOW COSTS, AND HOW MUCH ROOM GEOMETRY HAS")
    cold_ref, cold_cap = model.gate_error(arch, 0.0, n_ref), model.gate_error(arch, 0.0, 15)
    hot_ref, hot_cap = model.gate_error(arch, cap, n_ref), model.gate_error(arch, cap, 15)
    print(f"    a 15-ion chain costs {cold_cap / cold_ref:.2f}x the reference at nbar=0, "
          f"{hot_cap / hot_ref:.2f}x at R7's cap")
    print(f"    before G1, a FEASIBLE gate's error lay in "
          f"[{eps0:.2e}, {eps0 + slope * cap:.2e}] -> {(eps0 + slope * cap) / eps0:.2f}x of room")
    print(f"    after  G1, jointly over nbar<={cap:g} and N<=15:  "
          f"[{cold_ref:.2e}, {hot_cap:.2e}] -> {hot_cap / cold_ref:.2f}x")
    print(f"    -> G1 widens what geometry can move the gate term by "
          f"{(hot_cap / cold_ref) / ((eps0 + slope * cap) / eps0):.2f}x")

    print()
    print("4 - WHAT IT DID TO THE VERIFIED PROGRAMS")
    rows = []
    hist_all: Counter = Counter()
    for circuit, device in verified_pairs():
        p = MATRIX / f"{circuit}_{device}.cooled.tsir.json"
        if not p.exists():
            continue
        prog = TSIR.load(str(p))
        ar = load(str(ROOT / "arch" / f"{device}.arch.json"))
        res = replay(prog, ar, model, check_rules=False, keep_cycles=False)
        m = t2_metrics(ar, res, model)
        hist = Counter(res.chain_len_at_gate)
        hist_all.update(hist)
        # The pre-G1 number, by REPLAYING AGAIN against an architecture with the term
        # undeclared -- see `_without_chain_term`.
        before = _without_chain_term(ar)
        m0 = t2_metrics(before,
                        replay(prog, before, model, check_rules=False, keep_cycles=False),
                        model)
        rows.append({
            "circuit": circuit, "device": device,
            "n_gate_pairs": m.n_gate_pairs,
            "chain_hist": {str(k): v for k, v in sorted(hist.items())},
            "max_chain": max(hist) if hist else 0,
            "gate_error": m.gate_error_sum,
            "gate_error_legacy": m0.gate_error_sum,
            "neg_log_fidelity": m.neg_log_fidelity,
            "neg_log_fidelity_legacy": m0.neg_log_fidelity,
        })

    moved = [r for r in rows if r["gate_error"] != r["gate_error_legacy"]]
    print(f"    chain length at a 2Q gate, over all {len(rows)} verified programs: "
          f"{dict(sorted(hist_all.items()))}")
    print(f"    programs whose score changed: {len(moved)} of {len(rows)}")
    if moved:
        print(f"      {'circuit':<14}{'device':<19}{'max N':>6}{'-lnF was':>11}"
              f"{'-lnF now':>11}{'ratio':>8}")
        for r in sorted(moved, key=lambda r: -r["neg_log_fidelity"] / r["neg_log_fidelity_legacy"]):
            print(f"      {r['circuit']:<14}{r['device']:<19}{r['max_chain']:>6}"
                  f"{r['neg_log_fidelity_legacy']:>11.5f}{r['neg_log_fidelity']:>11.5f}"
                  f"{r['neg_log_fidelity'] / r['neg_log_fidelity_legacy']:>8.3f}x")
    unchanged = [r for r in rows if r["gate_error"] == r["gate_error_legacy"]]
    short = sum(1 for r in unchanged if r["max_chain"] <= n_ref)
    print(f"    unchanged, BIT FOR BIT: {len(unchanged)} programs -- {short} never gate a")
    print(f"    chain longer than {n_ref}, and the rest never longer than 4, which the")
    print("    monotone envelope prices at the reference")
    print("    G1 IS A GUARD ON AN AXIS NOT YET SWEPT, not a repair to today's comparison:")
    print("    seven of the nine shipped devices declare trap capacity 2, so the new term")
    print("    is a constant on them and folds back into eps0 by construction.")

    print()
    print("5 - THE RANKING IT MOVED")
    per_circuit: dict[str, list[dict]] = {}
    for r in rows:
        per_circuit.setdefault(r["circuit"], []).append(r)
    moves = 0
    for c, v in sorted(per_circuit.items()):
        if len(v) < 2:
            continue
        was = [x["device"] for x in sorted(v, key=lambda r: r["neg_log_fidelity_legacy"])]
        now = [x["device"] for x in sorted(v, key=lambda r: r["neg_log_fidelity"])]
        for d in was:
            i, j = was.index(d), now.index(d)
            if i == j:
                continue
            moves += 1
            # Keyed off whether this device's SCORE changed, not off its chain length:
            # `cyclone_base` gates 4-ion chains, which the monotone envelope still prices
            # at the reference, so it scores the same and only moved because something
            # else fell past it.
            own = any(x["device"] == d and x["gate_error"] != x["gate_error_legacy"]
                      for x in v)
            why = "its own chains" if own else "displaced by one that did"
            print(f"    {c:<14}{d:<19} rank {i + 1} -> {j + 1} of {len(was)}"
                  f"   ({why})")
    if not moves:
        print("    none -- no verified program gates a chain longer than the reference")

    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(
            {"calibration": {"eps0": eps0, "slope": slope, "n_ref": n_ref,
                             "kappa": kappa, "eps0_prime": eps0 - slope / 2,
                             "r7_max_quanta": cap},
             "curve": curve, "rows": rows,
             "chain_hist_all": {str(k): v for k, v in sorted(hist_all.items())}},
            indent=1), encoding="utf-8")
        print(f"\n-> {a.json}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
