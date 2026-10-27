"""A programme, as the rules see it: replayed once, step by step.

The replay is the verifier's own (`qccd.verify.replay`), read through its per-step view, so
where an ion is at every instant is exactly what the rest of the platform computes.  Each
step becomes a few instants and a list of operations:

    before step k     the state the step starts from (= after step k-1)
    during step k     only for a step that moves ions: a site counts the ions resting there
                      before or after the step, whichever is more, plus every ion passing
                      through it; a rail counts the ions crossing it
    after the last    the final state

A two-qubit gate is one operation per pair; a batch of single-ion pulses is one operation
per ion; a transport step is one operation per moving ion, from where it stood to where it
stops, with every rail it crosses as a hop.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

from ..ir.tsir import iter_operands, iter_pairs
from .model import (
    CircuitOpData, Execution, Hardware, Hop, Ion, Op, Rail, Site, Timeline,
)

__all__ = ["hardware_of", "programme_execution", "Claim"]


class Claim:
    """One figure a programme states about itself (R9)."""

    __slots__ = ("label_", "claimed", "replayed")
    kind = "claim"

    def __init__(self, label: str, claimed: Any, replayed: Any):
        self.label_ = label
        self.claimed = claimed
        self.replayed = replayed

    def label(self) -> str:
        return f"the claimed {self.label_}"

    def __hash__(self) -> int:
        return hash(("claim", self.label_))

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Claim) and other.label_ == self.label_

    def __lt__(self, other: "Claim") -> bool:
        return self.label_ < other.label_


def hardware_of(arch) -> Hardware:
    """The device of an architecture document, in the rules' terms."""
    dev = arch.device
    nodes = [Site(n) for n in dev.nodes]
    sites = [Site(n) for n, node in dev.nodes.items() if node.kind == "site"]
    junctions = [Site(n) for n in dev.junction_nodes]
    rails = [Rail(s) for s in dev.segments]
    cap: dict[Any, int] = {Site(n): int(node.capacity) for n, node in dev.nodes.items()}
    cap.update({Rail(s): int(seg.capacity) for s, seg in dev.segments.items()})
    abilities: dict[Site, frozenset[str]] = {}
    for n, node in dev.nodes.items():
        zt = node.zone_type
        if zt is None:
            abilities[Site(n)] = frozenset()
            continue
        try:
            z = arch.zone(zt)
        except KeyError:
            abilities[Site(n)] = frozenset()
            continue
        abilities[Site(n)] = frozenset(k for k in ("gate", "spam", "cool", "measure", "reset")
                                       if z.get(k, False))
    try:
        curve = arch.primitives.degree_curve("junction_cross")
        priced = frozenset(d for d in range(0, 16) if curve.get(d) is not None)
    except KeyError:
        priced = frozenset()
    from ..verify.rules import geometry_limits
    max_deg, min_angle = geometry_limits(arch)
    return Hardware(
        name=arch.name, sites=sites, junctions=junctions, nodes=nodes, rails=rails,
        capacity=cap, degree={Site(n): dev.degree(n) for n in dev.nodes},
        zone={Site(n): node.zone_type for n, node in dev.nodes.items()},
        abilities=abilities,
        ends={Rail(s): (Site(seg.ends[0]), Site(seg.ends[1])) for s, seg in dev.segments.items()},
        rails_at={Site(n): tuple(Rail(s) for s in dev.incidence[n]) for n in dev.nodes},
        loops={lid: tuple(Site(n) for n in lp.nodes) for lid, lp in dev.loops.items()},
        numbers={"max_junction_degree": max_deg, "min_rail_angle_deg": min_angle,
                 "max_simd_classes": arch.max_simd_classes(),
                 "control_model": str(arch.control.get("model", "simd_classes")),
                 "priced_degrees": priced,
                 "duty_budget": arch.budget.get("duty_cycle") if isinstance(arch.budget, Mapping) else None},
        arch=arch,
    )


def programme_execution(prog, arch, model, *, circuit: Sequence[CircuitOpData] | None = None,
                        computes_circuit: bool | None = None,
                        rule_config: Mapping[str, object] | None = None,
                        check_metrics: bool = True) -> Execution:
    """Replay `prog` on `arch` under the clock `model` and build its execution."""
    from ..verify import verify_metrics
    from ..verify.replay import replay
    from ..verify.rules import motion_signature, r4_drivable, r4_simd_classes, hop_label, loop_node_set

    hw = hardware_of(arch)
    dev = arch.device
    on_loop = loop_node_set(dev)
    views: list = []
    res = replay(prog, arch, model, check_rules=False, keep_cycles=True,
                 rule_config=rule_config, on_cycle=views.append)

    occ = Timeline(0)
    pos = Timeline(None)
    instants: list[str] = ["the start"]
    times: list[float] = [0.0]
    ops: list[Op] = []
    hops: list[Hop] = []
    steps: list[dict] = []
    heat: dict[Ion, dict[int, float]] = {}
    ions_seen: dict[str, Ion] = {}

    def ion(i: str) -> Ion:
        x = ions_seen.get(i)
        if x is None:
            x = ions_seen[i] = Ion(i)
        return x

    if views:
        for n, c in views[0].occ_before.items():
            occ.set(Site(n), 0, c)
        for i, s in views[0].pos_before.items():
            pos.set(ion(i), 0, Site(s))
    cur_occ: dict[str, int] = dict(views[0].occ_before) if views else {}
    cur_pos: dict[str, str] = dict(views[0].pos_before) if views else {}
    t = 0.0
    budget_known = None

    for k, v in enumerate(views):
        ins = v.instr
        before = len(instants) - 1
        # an `init` between steps re-places ions: the step's own view says where they are
        if v.occ_before != cur_occ:
            for n in set(cur_occ) | set(v.occ_before):
                occ.set(Site(n), before, v.occ_before.get(n, 0))
        if v.pos_before != cur_pos:
            for i in set(cur_pos) | set(v.pos_before):
                s = v.pos_before.get(i)
                pos.set(ion(i), before, Site(s) if s is not None else None)
        t0, t1 = t, t + float(v.duration_us or 0.0)
        moving = ins.type == "simd" and bool(v.moves)
        if moving:
            during = len(instants)
            instants.append(f"during step {k}")
            times.append(t0)
            transit = v.transits or {}
            for n in set(v.occ_before) | set(v.occ_after) | set(transit):
                val = max(v.occ_before.get(n, 0), v.occ_after.get(n, 0)) + transit.get(n, 0)
                occ.set(Site(n), during, val)
            for sid, c in Counter(m.seg.id for m in v.moves).items():
                occ.set(Rail(sid), during, c)
            for m in v.moves:
                pos.set(ion(m.ion), during, None)
        after = len(instants)
        instants.append(f"after step {k}")
        times.append(t1)
        for n in set(v.occ_before) | set(v.occ_after) | set(v.transits or {}):
            occ.set(Site(n), after, v.occ_after.get(n, 0))
        if moving:
            for sid in {m.seg.id for m in v.moves}:
                occ.set(Rail(sid), after, 0)
        for i in set(v.pos_before) | set(v.pos_after):
            if v.pos_before.get(i) != v.pos_after.get(i) or moving:
                s = v.pos_after.get(i)
                pos.set(ion(i), after, Site(s) if s is not None else None)
        cur_occ, cur_pos = dict(v.occ_after), dict(v.pos_after)

        serves = tuple(int(x) for x in ((ins.meta or {}).get("op") or ())
                       if isinstance(x, (int, float)) or str(x).isdigit()) \
            if isinstance((ins.meta or {}).get("op"), (list, tuple)) else \
            ((int(ins.meta["op"]),) if isinstance((ins.meta or {}).get("op"), int) else ())

        def mk(kind: str, ions_: Sequence[str], site: str | None = None, **facts) -> Op:
            o = Op(index=len(ops), kind=kind, ions=tuple(ion(i) for i in ions_), start=before,
                   end=after, t0=t0, t1=t1, site=Site(site) if site is not None else None,
                   step=k, serves=serves, facts=facts)
            ops.append(o)
            return o

        st: dict[str, Any] = {"index": k, "instr": ins.id, "type": ins.type, "cls": ins.cls,
                              "mode": ins.mode, "carries_gates": bool(ins.pairs or ins.gate)
                              if ins.type == "simd" else False}
        if ins.type == "simd":
            st["class_declared"] = not r4_simd_classes(v)
            st["drivable"] = not r4_drivable(v)
            # one operation per participant: a participant's route is contiguous, so an ion's
            # next hop that does not start where its last one ended is a second listing of
            # the same ion in this step -- which R8 must see as two operations, not one walk
            routes: list[tuple[str, list]] = []
            last: dict[str, tuple[int, str]] = {}
            for m in v.moves:
                prev = last.get(m.ion)
                if prev is not None and prev[1] == m.src:
                    routes[prev[0]][1].append(m)
                    last[m.ion] = (prev[0], m.dst)
                else:
                    routes.append((m.ion, [m]))
                    last[m.ion] = (len(routes) - 1, m.dst)
            paid = bool((ins.meta or {}).get("gate_swaps"))
            for i, ms in routes:
                path = [Site(ms[0].src)] + [Site(m.dst) for m in ms]
                entails = ms[0].entails
                src_occ = v.occ_before.get(ms[0].src, 0)
                o = mk("move", [i], None, motion=motion_signature(v, i),
                       split="split" in entails, merge="merge" in entails,
                       swap_paid=paid, at_chain_end=src_occ <= 2)
                o.path = tuple(path)
                o.rails = tuple(Rail(m.seg.id) for m in ms)
                o.text = f"move {i} {ms[0].src}→{ms[-1].dst}"
                for m in ms:
                    loop = m.seg.loop
                    shift = None
                    if loop is not None and loop in dev.loops:
                        lp = dev.loops[loop]
                        if m.src in lp.nodes and m.dst in lp.nodes:
                            n = len(lp.nodes)
                            d = (lp.nodes.index(m.dst) - lp.nodes.index(m.src)) % n
                            shift = d if d <= n // 2 else d - n
                    hops.append(Hop(len(hops), o, ion(i), Site(m.src), Site(m.dst), Rail(m.seg.id),
                                    loop=loop, shift=shift,
                                    label_=hop_label(dev, on_loop, m.src, m.dst, m.seg)))
        elif ins.type == "gate":
            declared = tuple(ins.sites)
            for operands in iter_operands(ins):
                site = v.pos_before.get(operands[0])
                if len(operands) == 2:
                    o = mk("gate2", operands, site, gate=ins.gate, declared_sites=declared)
                    o.text = f"{ins.gate or 'gate'} {operands[0]},{operands[1]}"
                    hk = before
                    for i in operands:
                        heat.setdefault(ion(i), {})[hk] = float(v.quanta.get(i, 0.0))
                else:
                    o = mk("gate1", operands, site, gate=ins.gate, declared_sites=declared)
                    o.text = f"{ins.gate or 'gate'} {operands[0]}"
        elif ins.type in ("measure", "reset"):
            for i in ins.ions:
                o = mk(ins.type, [i], v.pos_before.get(i))
                o.text = f"{ins.type} {i}"
        elif ins.type == "cool":
            cooled = ins.ions or (tuple(v.pos_before) if ins.broadcast else ())
            o = mk("cool", cooled, None)
            o.text = "cool" + (" all" if not ins.ions else " " + ",".join(ins.ions[:4]))
        steps.append(st)
        t = t1
        if budget_known is None:
            budget_known = v.gate_budget()

    exe = Execution(kind="programme", hardware=hw, ions=sorted(ions_seen.values()), ops=ops,
                    instants=instants, instant_time=times, occupancy=occ, position=pos,
                    hops=hops, steps=steps, heat=heat,
                    circuit=list(circuit) if circuit is not None else None,
                    name=getattr(prog, "name", ""))
    from ..verify.replay import conveyed_paths
    exe.facts["rotated_nodes"] = frozenset(
        Site(n) for lid in conveyed_paths(prog, arch) if lid in dev.loops for n in dev.loops[lid].nodes)
    exe.facts["heat_budget"] = budget_known if budget_known is not None else float("inf")
    exe.facts["models_heating"] = bool(getattr(model, "models_heating", False))
    exe.facts["computes_circuit"] = computes_circuit
    if check_metrics:
        rep, _ = verify_metrics(prog, res, model_name=getattr(model, "name", None))
        if rep.get("skipped"):
            exe.facts["claims_skipped"] = rep["skipped"]
        else:
            exe.facts["claims"] = [Claim(lbl, rep["claims"][lbl], rep["replayed"][lbl])
                                   for lbl in rep.get("checked", ())
                                   if rep["claims"].get(lbl) is not None]
            # the per-batch and per-instruction annotations, through the same comparison
            for c in prog.meta.get("batch_claims", ()):
                got = res.per_batch.get(c["batch"])
                for f in ("cost", "steps", "contacts"):
                    exe.facts["claims"].append(Claim(f"{f} of batch {c['batch']}", c[f],
                                                     None if got is None else got[f]))
    else:
        exe.facts["claims_skipped"] = "the programme's own figures were not checked"
    exe.facts["replay"] = res
    return exe
