"""Characterization: replay a leaf's op programs and write down only what the replay says.

This is the gadget layer's SPICE-to-Liberty step (GADGETS.md §1).  For every op it runs
the repository's verifier -- the 27 rules, under the corrected cost model -- and records:

* **status**: `verified` when no rule fails AND the op ends in the placement it promised;
  `failed` otherwise, with the reason.  R10 is always *skipped* on these programs (there
  is no circuit certificate), and the rule report says so in the verifier's own words.
* **duration** and the per-instruction times, from the replay's cycle records;
* **transfers**: every ion that reaches or leaves a port stub, in order, with the window
  and the n̄ the ions carry at the moment they cross (read with the replay's probe);
* **metrics**: gate, measurement and cooling counts, hops, junction transits, peak n̄.

The characterized master goes into the library; the device and programs go into the
master's leaf data, which the page loads when someone opens an instance.
"""

from __future__ import annotations

from collections import Counter

from ..cost.models import corrected_model
from ..verify import verify
from ..verify.replay import replay
from .leaves.common import LeafBuild
from .model import Master, Op, Transfer

__all__ = ["characterize", "leaf_data"]


def characterize(build: LeafBuild, *, table: str = "qccdsim_jones") -> Master:
    model = corrected_model(table)
    master = build.master
    arch = build.machine.arch
    stub_port = {p.stub: p for p in master.ports if p.stub}
    build.times = {}

    for key, prog in build.programs.items():
        report = verify(prog, arch, model, check_metrics=False)
        res = report.result
        summary = report.rules.summary()

        span: dict[int, list[float]] = {}
        for c in res.cycles:
            s = span.setdefault(c.instr_id, [c.t0, c.t1])
            s[0], s[1] = min(s[0], c.t0), max(s[1], c.t1)
        build.times[key] = [[i.id, *span.get(i.id, [0.0, 0.0])] for i in prog.instructions]

        crossings: list[tuple[int, str, str, str]] = []   # (position, port, dir, ion)
        for k, ins in enumerate(prog.instructions):
            if ins.type == "init":
                for ion, node in ins.placement.items():
                    if node in stub_port:
                        crossings.append((k, stub_port[node].name, "in-wait", ion))
            for p in ins.participants:
                if p.dst in stub_port:
                    crossings.append((k, stub_port[p.dst].name, "out", p.ion))
                if p.src in stub_port:
                    crossings.append((k, stub_port[p.src].name, "in", p.ion))

        # n̄ an ion carries as it crosses: read before the instruction AFTER an arrival at
        # a stub, and before the instruction that takes an ion off one
        want = {}
        for k, _, d, ion in crossings:
            if d == "out":
                want.setdefault(k + 1, set()).add(ion)
            elif d == "in":
                want.setdefault(k, set()).add(ion)
        heat: dict[tuple[int, str], float] = {}
        if want:
            order = {ins.id: k for k, ins in enumerate(prog.instructions)}

            def probe(ins, current):
                k = order[ins.id]
                for ion in want.get(k, ()):
                    heat[(k, ion)] = float(current.get(ion, 0.0))

            final = replay(prog, arch, model, check_rules=False, keep_cycles=False,
                           probe=probe).final_quanta
            last = len(prog.instructions)
            for ion in want.get(last, ()):
                heat[(last, ion)] = float(final.get(ion, 0.0))

        transfers: dict[tuple[str, str], Transfer] = {}
        for k, port, d, ion in crossings:
            if d == "in-wait":
                continue
            ins = prog.instructions[k]
            t0, t1 = span.get(ins.id, [0.0, 0.0])
            when = t1 if d == "out" else t0
            q = heat.get((k + 1, ion) if d == "out" else (k, ion), 0.0)
            tr = transfers.get((port, d))
            if tr is None:
                tr = transfers[(port, d)] = Transfer(port, d, 0, when, when, 0.0, [], [])
            tr.count += 1
            tr.t_first_us = min(tr.t_first_us, when)
            tr.t_last_us = max(tr.t_last_us, when)
            tr.quanta = max(tr.quanta, q)
            tr.ions.append(ion)
            tr.times_us.append(when)

        problems = []
        expect = build.expect_end.get(key)
        if expect is not None:
            wrong = sorted(ion for ion, node in expect.items()
                           if res.final_positions.get(ion) != node)
            if wrong:
                problems.append(f"placement contract broken: {len(wrong)} ion(s) do not end "
                                f"where the op promised (e.g. {wrong[:4]})")
        failed = list(summary["failed"])
        logic = logic_report(build, key, prog)
        if logic.get("status") == "failed":
            problems.append("logic check failed: " + "; ".join(logic.get("why", [])[:3]))
        status = "failed" if failed or problems else "verified"

        kinds = Counter(i.type for i in prog.instructions)
        master.ops[key] = Op(
            name=key, title=build.titles.get(key, key), status=status,
            duration_us=float(res.total_us),
            transfers=sorted(transfers.values(), key=lambda t: (t.t_first_us, t.port)),
            metrics={
                "instructions": len(prog.instructions), "cycles": len(res.cycles),
                "gates_2q": res.n_gate_pairs, "gates_1q": res.n_1q_gates,
                "measures": res.n_measure, "resets": res.n_reset,
                "cools": kinds.get("cool", 0),
                "hops": int(sum(res.hops_by_class.values())),
                "junction_transits": int(sum(res.junction_transits.values())),
                "peak_quanta": round(float(res.peak_quanta), 4),
                "gate_error_sum": float(res.gate_error_sum),
                "us_by_type": {k: round(v, 3) for k, v in res.us_by_type.items()},
                "cost": float(res.total_cost), "steps": int(res.total_steps),
            },
            rules={"passed": sorted(summary["passed"]), "failed": failed,
                   "skipped": dict(summary["skipped"]), "partial": dict(summary["partial"]),
                   "violations": [f"[{v.rule}] instruction {v.instr_id}: {v.message}"
                                  for v in report.rules.violations[:20]]},
            notes=list(build.notes.get(key, [])) + problems,
            params=dict(build.params.get(key, {})),
            program=key,
            logic=logic,
        )
    return master


def logic_report(build: LeafBuild, key: str, prog) -> dict:
    """Check what the op's circuit computes: its flows, its fault distance, any custom check.

    `status` is `verified` when every check the op has passes (a distance the op declares
    it does not have -- injection -- is reported, not failed), `failed` otherwise, and
    `none` for an op with no specification (the beta leaves)."""
    from .logic.circuit import NotACircuit, from_tsir
    from .logic.experiment import distance_report
    from .logic.spec import verify_flows

    spec = build.specs.get(key)
    exp = build.experiments.get(key)
    custom = build.logic_checks.get(key)
    if spec is None and exp is None and custom is None:
        return {"status": "none"}
    out: dict = {"why": []}
    try:
        circ = from_tsir(prog)
    except NotACircuit as e:
        return {"status": "failed", "why": [str(e)]}
    counts = circ.counts()
    out["circuit"] = {"qubits": circ.n, "ops": counts, "records": len(circ.records)}

    def where(rec: int) -> str:
        k, q = circ.records[rec]
        lab = circ.labels[rec]
        op = circ.ops[k]
        return f"rec {rec}: {circ.qubits[q]} at instruction {op.source}" + (
            f" (round {lab['round']})" if "round" in lab else "")

    ok = True
    if spec is not None:
        rep = verify_flows(circ, spec, describe_record=where)
        rep["spec"] = spec.to_json()
        out["flows"] = rep
        if not rep["passed"]:
            ok = False
            bad = [f["name"] for f in rep["flows"] if not f["ok"]]
            if bad:
                out["why"].append(f"flows that do not hold: {', '.join(bad[:4])}")
            if not rep["complete"]:
                out["why"].append(f"spec rank {rep['rank']} < {rep['needed']}: incomplete")
            if rep.get("error"):
                out["why"].append(rep["error"])
    if exp is not None:
        kw = {k: v for k, v in exp.items() if k != "expect"}
        rep = distance_report(circ, **kw)
        rep["expect"] = exp.get("expect", "ft")
        out["distance"] = rep
        if rep["expect"] == "ft" and not rep.get("passed"):
            ok = False
            out["why"].append(
                f"fault distance {rep.get('distance')}: " + " + ".join(rep.get("witness", [])[:2])
                if rep.get("distance") else rep.get("error", "distance experiment failed"))
    if custom is not None:
        rep = custom(circ)
        out["check"] = rep
        if not rep.get("passed"):
            ok = False
            out["why"].append(rep.get("claim", "custom check failed"))
    out["status"] = "verified" if ok else "failed"
    return out


def leaf_data(build: LeafBuild) -> dict:
    """Everything the page needs to replay one leaf ion by ion."""
    arch = build.machine.arch
    dev = arch.device.to_json()
    return {
        "master": build.master.name,
        "device": {"nodes": dev["nodes"], "segments": dev["segments"],
                   "loops": dev.get("loops", [])},
        # the whole architecture document, so a cached leaf can still be replayed and
        # rendered as a studio page without rebuilding it
        "arch": arch.to_json(expanded=True),
        "zones": {k: dict(v) for k, v in arch.zone_types.items()},
        "home": dict(build.home),
        "roles": dict(build.roles),
        "programs": {k: p.to_json() for k, p in build.programs.items()},
        "times": dict(getattr(build, "times", {})),
    }
