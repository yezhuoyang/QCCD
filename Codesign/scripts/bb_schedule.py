"""Decide the BB round's schedule with the code-aware placer, and hand it to the compiler.

The OCaml rotation pipeline compiles whatever QASM it is given and certifies the result,
but it lays the riders out by stride and takes the check->ancilla binding the QASM fixed.
Both are decisions a code-aware pass makes far better: `qccd/compile` places the six
members of every check close together (annealing on check windows) and packs 144
check-arcs onto the docks with an event-driven sweep -- 61 batches on `ring144_24v`
against the stride layout's 546.  It emits no certificate, though, so nothing it produces
can be quoted at the study's top tier.

This script is the bridge.  It runs the placer and the binder, then writes

    <prefix>.schedule.json    which ancilla serves which check, and every contact in
                              sweep order -- `gen_bb144.py --schedule` turns it into QASM
    <prefix>.placement.json   rider -> loop slot, ancilla -> dock, for `qccdc rotate
                              --placement` (and `compile`'s rotation fallback)

so the OCaml pipeline reproduces the code-aware schedule AND emits the certificate the
proved checker discharges.  The schedule is decided upstream; the compiler certifies it.

    python Codesign/scripts/bb_schedule.py --arch arch/ring144_24v.arch.json \
        --ancillas 24 -o Compiler/build/q06/ring24_a24

`--ancillas` may be fewer than the docks: the ancillas then use an evenly spaced subset.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.codes.bb import gross_code  # noqa: E402
from qccd.compile.order import bind_dynamic, bind_fixed_waves  # noqa: E402
from qccd.compile.place import (  # noqa: E402
    anneal,
    identity_seed,
    interleaved_seed,
    lower_bound_revolutions,
    refine,
    structured_seeds,
    window,
)


def choose_docks(all_docks: list[int], n: int) -> list[int]:
    """`n` docks, evenly spaced around the loop, from the `all_docks` slot list."""
    if n > len(all_docks):
        raise SystemExit(f"{n} ancillas but the device has {len(all_docks)} docks")
    if n == len(all_docks):
        return list(all_docks)
    return [all_docks[(i * len(all_docks)) // n] for i in range(n)]


def conveyor(arch, loop_id: str | None = None) -> tuple[str, list[str], dict[int, str]]:
    """`(loop id, its node order, {rail index: dock site})`, derived the way the compiler
    derives it: the first closed loop of four or more nodes, and every gate-capable site
    that hangs off it by one segment.

    Structural, not by name, so this serves a ring whose docks are `A{n}` on spurs and a
    dual loop whose "docks" are slots on a second conveyor alike -- the two shapes q06f
    compares.  A rail slot with more than one dock keeps the first, since a rider can
    only be lifted one way.
    """
    loops = [lid for lid, lp in arch.device.loops.items() if lp.closed and len(lp.nodes) >= 4]
    if not loops:
        raise SystemExit(f"{arch.name} has no closed loop; the conveyor router needs one")
    loop_id = loop_id or loops[0]
    nodes = list(arch.device.loops[loop_id].nodes)
    index = {n: i for i, n in enumerate(nodes)}
    docks: dict[int, str] = {}
    for sg in arch.device.segments.values():
        a, b = sg.ends
        for rail, site in ((a, b), (b, a)):
            if rail in index and site not in index \
                    and arch.device.nodes[site].kind == "site" and arch.can(site, "gate"):
                docks.setdefault(index[rail], site)
    return loop_id, nodes, docks


def bind_phased(checks, slot_of, docks, cap, binder=bind_dynamic) -> "Binding":
    """All X checks, then all Z checks, on one monotone sweep -- Cyclone's rule.

    An X check and a Z check that share two data qubits must touch both in the same
    relative order, or the Z outcome picks up a factor of Z on the X ancilla and is
    random.  A binder that packs the two types together satisfies that only by luck --
    three of four packings tried on `ring144_24v` did not (`Codesign/findings/q06`).
    Finishing every X check before the first Z contact satisfies it by construction:
    on every data qubit the X contacts precede the Z contacts.  The price is the idle
    at the phase boundary, and the Z phase continues the sweep from where X ended
    (`t_start`) so alignment, which is a property of the absolute offset, is kept.
    """
    from qccd.compile.order import Binding

    xs = [c for c in checks if c.type == "X"]
    zs = [c for c in checks if c.type == "Z"]
    # the shipped six-wave rule spreads 144 checks over 24 docks in waves of 24; per
    # phase that is 72 checks, so three waves keep every dock in use
    kw = {"waves": 3} if binder is bind_fixed_waves else {}
    bx = binder(xs, slot_of, docks, cap, **kw)
    bz = binder(zs, slot_of, docks, cap, t_start=bx.sweep, **kw)
    return Binding(assignments=bx.assignments + bz.assignments, sweep=bz.sweep,
                   capacity=cap, n_docks=len(docks), strategy=bx.strategy + "-xz",
                   notes=[f"X phase: {bx.sweep} offsets; Z phase: {bz.sweep - bx.sweep} "
                          f"offsets, from offset {bx.sweep}"] + bx.notes[1:3])


def schedule(arch_path: Path, n_anc: int, *, placement: str = "anneal",
             binding: str = "dynamic", phases: str = "xz", iterations: int = 60_000,
             refine_steps: int = 300, rng_seed: int = 20260820,
             loop_id: str | None = None, swap_roles: bool = False) -> dict:
    arch = load(str(arch_path))
    code = gross_code()
    loop_id, nodes, all_dock_sites = conveyor(arch, loop_id)
    cap = len(nodes)
    all_docks = sorted(all_dock_sites)

    # Who rides and who parks.  This study's assignment is data riding, ancillas parked at
    # the docks.  Cyclone's is the mirror image -- the ancillas move in lockstep past
    # stationary data -- and it is expressible here whenever the device has a parking site
    # per data qubit.  The arithmetic is the same in both, because a contact depends only
    # on the RELATIVE displacement: with the parked side at position `p` and the riding
    # side starting at `r`, they meet after the loop turns by `(p - r) mod cap`.
    #
    # `bind_dynamic` computes `(dock - slot_of[member]) mod cap`, which is that expression
    # with the parked side in `dock` and the riding side in `slot_of`.  In the swapped
    # world the members ARE the parked side, so both coordinate systems are negated and
    # the same call comes out with the same meaning.
    if swap_roles:
        if sorted(all_docks) != list(range(cap)):
            raise SystemExit(
                f"{arch.name}: the swapped assignment parks a data qubit at every loop "
                f"slot, so it needs a dock on all {cap} of them; this device has "
                f"{len(all_docks)}")
        stride = max(1, cap // n_anc)
        riders = [(i * stride) % cap for i in range(n_anc)]   # ancilla j starts here
        if len(set(riders)) != n_anc:
            raise SystemExit(f"{n_anc} ancillas do not fit distinctly on {cap} slots")
        docks = [(-r) % cap for r in riders]              # the binder's "dock" is a rider
        dock_site: dict[int, str] = {}
    else:
        docks = choose_docks(all_docks, n_anc)
        dock_site = {s: all_dock_sites[s] for s in docks}

    def bind(slot_of):
        # in the swapped assignment the members are the PARKED side, so both coordinate
        # systems are negated and the binder's `(dock - slot)` is the same displacement
        sl = {q: (-s) % cap for q, s in slot_of.items()} if swap_roles else slot_of
        binder = bind_dynamic if binding == "dynamic" else bind_fixed_waves
        if phases == "xz":
            return bind_phased(code.checks, sl, docks, cap, binder)
        return binder(code.checks, sl, docks, cap)

    # The clock, not the hop count.  A batch is dock + cool + gate + undock, 725 us under
    # the Jones table; a rigid hop is one junction crossing, 100 us.  Ranking placements by
    # revolutions -- what the shipped pipeline does -- prefers fewer hops at any cost in
    # batches, and a batch is worth seven hops.  (T_round under the corrected model is
    # exactly this sum plus the gates and SPAM, which placement does not move.)
    def clock_us(b) -> float:
        return 725.0 * len(b.contacts_by_offset()) + 100.0 * b.sweep

    t0 = time.time()
    notes: list[str] = []
    seeds = {"identity": identity_seed(code), "interleaved": interleaved_seed(code)}
    if placement == "anneal":
        pl = anneal(code, cap, n_docks=len(docks), seed=seeds["interleaved"],
                    iterations=iterations, rng_seed=rng_seed)
        candidates = {"interleaved": seeds["interleaved"], "annealed": pl.slot_of}
        notes += pl.notes
    elif placement == "structured":
        # the homomorphic family, ranked by the true sweep AGAINST THESE DOCKS; the
        # interleaved seed is the member (2m, 2) and wins only at dock spacing 6
        fam = structured_seeds(code, cap)
        # Two thousand members on a 192-slot loop, and a bind on a badly aligned member
        # runs to its guard, so the true clock is evaluated only for the members worth
        # it.  What makes a member worth it is computable: with docks one spacing `s`
        # apart, the checks that can share a batch are the translates whose slot shift
        # is a multiple of `s`, i.e. the subgroup {(i, j) : a*i + b*j = 0 (mod s)}, and
        # its size is the batch size the member can reach.  Rank by that, break ties
        # by the window sum, and evaluate the clock for the top few dozen.
        s = docks[1] - docks[0] if len(docks) > 1 else cap
        def lockstep(k: str) -> int:
            a, b = (int(x[1:]) for x in k.split("_"))
            return sum(1 for i in range(code.l) for j in range(code.m) if (a * i + b * j) % s == 0)
        def wsum(sl) -> int:
            return sum(window([sl[q] for q in c.members], cap) for c in code.checks)
        short = sorted(fam, key=lambda k: (-lockstep(k), wsum(fam[k])))[:32]
        ranked = sorted(((clock_us(bind(fam[k])) / 1000.0, k) for k in short))
        best_k = ranked[0][1]
        notes.append(f"structured family: {len(fam)} injective (a, b) maps, {len(short)} "
                     f"evaluated (lockstep {lockstep(short[0])}..{lockstep(short[-1])} of "
                     f"{len(docks)} docks); best {best_k} clock {ranked[0][0]:.1f} ms, "
                     f"interleaved (a{2 * code.m}_b2) "
                     f"{clock_us(bind(seeds['interleaved'])) / 1000:.1f} ms; runners-up "
                     + ", ".join(f"{k} {s:.1f}" for s, k in ranked[1:4]))
        candidates = {best_k: fam[best_k], "interleaved": seeds["interleaved"]}
    else:
        candidates = {placement: seeds[placement]}
    scored = {tag: (clock_us(bind(sl)), sl) for tag, sl in candidates.items()}
    best_tag = min(scored, key=lambda k: scored[k][0])
    _, slot_of = scored[best_tag]
    if len(scored) > 1:
        notes.append("selected on the clock estimate: "
                     + ", ".join(f"{k} {v[0] / 1000:.1f} ms" for k, v in sorted(scored.items()))
                     + f" -> {best_tag}")
    if refine_steps:
        slot_of, _, more = refine(slot_of, lambda sl: clock_us(bind(sl)),
                                  steps=refine_steps, rng_seed=rng_seed)
        notes += more
    b = bind(slot_of)
    ws = [window([slot_of[q] for q in c.members], cap) for c in code.checks]
    bound = lower_bound_revolutions(ws, cap, len(docks))

    # every contact in sweep order; ties within an offset are one batch
    contacts = []
    for a in b.assignments:
        for off, q in a.contacts:
            contacts.append((off, code.checks.index(a.check), q, a.dock))
    contacts.sort()
    offsets = sorted({c[0] for c in contacts})
    hops = offsets[-1] - 0 if offsets else 0  # monotone cw from offset 0
    sizes: dict[int, int] = {}
    for off in offsets:
        k = sum(1 for c in contacts if c[0] == off)
        sizes[k] = sizes.get(k, 0) + 1

    # ancilla j <-> the binder's dock docks[j] <-> QASM qubit n_data + j
    anc_of_dock = {d: j for j, d in enumerate(docks)}
    order: dict[int, list[int]] = {j: [] for j in range(len(docks))}
    for a in sorted(b.assignments, key=lambda a: (a.dock, a.start)):
        order[anc_of_dock[a.dock]].append(code.checks.index(a.check))

    if swap_roles:
        # the parked side is the DATA, one per dock site; the riders are the ancillas
        parked = [{"data": q, "qubit": q, "slot": s, "site": all_dock_sites[s]}
                  for q, s in sorted(slot_of.items())]
        loop_slot = {code.n + j: (-d) % cap for j, d in enumerate(docks)}
    else:
        parked = [{"ancilla": j, "qubit": code.n + j, "slot": d, "site": dock_site[d]}
                  for j, d in enumerate(docks)]
        loop_slot = {int(q): int(s) for q, s in slot_of.items()}

    return {
        "arch": arch.name, "arch_path": str(arch_path), "loop": loop_id,
        "capacity": cap, "n_data": code.n, "n_ancillas": len(docks),
        "roles": "ancillas ride, data parked" if swap_roles else "data ride, ancillas parked",
        "docks": parked,
        "placement": placement, "binding": binding, "phases": phases, "selected": best_tag,
        "loop_slot": {int(k): int(v) for k, v in loop_slot.items()},
        "order": {int(j): v for j, v in order.items()},
        # (offset, check index, data qubit, ancilla qubit)
        "contacts": [[off, ci, q, code.n + anc_of_dock[d]] for off, ci, q, d in contacts],
        "stats": {
            "batches": len(offsets), "hops": hops, "contacts": len(contacts),
            "revolutions": b.revolutions, "bound_revolutions": bound,
            "mean_window": sum(ws) / len(ws), "max_window": max(ws),
            "batch_sizes": dict(sorted(sizes.items())),
            "seconds": round(time.time() - t0, 1),
        },
        "notes": notes + b.notes,
    }


def write(sched: dict, prefix: Path) -> tuple[Path, Path]:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    sp = Path(str(prefix) + ".schedule.json")
    pp = Path(str(prefix) + ".placement.json")
    sp.write_text(json.dumps(sched, indent=1), encoding="utf-8")
    pp.write_text(json.dumps({
        "loop": {str(q): s for q, s in sched["loop_slot"].items()},
        "docks": {str(d["qubit"]): d["site"] for d in sched["docks"]},
    }, indent=1), encoding="utf-8")
    return sp, pp


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--arch", required=True)
    ap.add_argument("--ancillas", type=int, default=None,
                    help="how many docks to use (default: all)")
    ap.add_argument("--placement", default="structured",
                    choices=["structured", "anneal", "interleaved", "identity"])
    ap.add_argument("--binding", default="dynamic", choices=["dynamic", "fixed"])
    ap.add_argument("--swap-roles", action="store_true",
                    help="park the data and ride the ancillas (Cyclone's assignment); "
                         "needs a dock at every loop slot")
    ap.add_argument("--phases", default="xz", choices=["xz", "mixed"],
                    help="xz: every X check before the first Z contact (correct by "
                         "construction); mixed: pack both types together (must pass "
                         "esm_check)")
    ap.add_argument("--iterations", type=int, default=60_000)
    ap.add_argument("--refine", type=int, default=300)
    ap.add_argument("--seed", type=int, default=20260820)
    ap.add_argument("-o", "--out", required=True, help="output prefix")
    a = ap.parse_args(argv)

    arch = load(a.arch)
    n_docks = len(list(arch.device.labelled("dock")))
    n_anc = a.ancillas or n_docks
    s = schedule(Path(a.arch), n_anc, placement=a.placement, binding=a.binding,
                 phases=a.phases, iterations=a.iterations, refine_steps=a.refine,
                 rng_seed=a.seed, swap_roles=a.swap_roles)
    sp, pp = write(s, Path(a.out))
    st = s["stats"]
    print(f"{s['arch']}: {s['capacity']} slots, {n_docks} docks, {s['n_ancillas']} "
          f"ancillas -- {s['roles']}")
    for n in s["notes"]:
        print(f"  - {n}")
    print(f"  {st['contacts']} contacts in {st['batches']} batches, {st['hops']} hops "
          f"({st['revolutions']:.2f} revolutions, bound {st['bound_revolutions']:.2f}); "
          f"batch sizes {st['batch_sizes']}   [{st['seconds']}s]")
    print(f"  -> {sp}\n  -> {pp}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
