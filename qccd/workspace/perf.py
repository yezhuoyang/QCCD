"""Where the time goes when a compiled program runs on a design, and how two runs differ.

`performance(arch, prog, res)` reads one replay (`qccd.verify.replay`, with its per-cycle
trace) and returns a JSON-ready report:

    total         the round's modelled time, and the same program under the transport table
    breakdown     time by category -- transport, cooling, gates, measurement, reset -- with
                  shares, and the transport time split by move class
    counts        instructions, cycles, gates, measurements, transport and cooling steps
    longest       the single slowest steps
    hotspots      the busiest junctions (with how uneven they are), the busiest ions, how
                  many ions shared a trap at each gate
    heating       the peak motional quanta and the ion that carried it
    rules         which of the replayed rules failed (a failing run is not a valid design)
    bottleneck    plain sentences naming the dominant cost, computed from the numbers above

Nothing here is estimated: every number is read off the replay the evaluator itself uses.
`compare(a, b)` puts two such reports side by side, with differences.
"""

from __future__ import annotations

from bisect import bisect_right
from statistics import median
from typing import Mapping

__all__ = ["performance", "compare", "frame_at"]

#: replay instruction types -> the category a person reads
_CATEGORY = {"gate": "gates", "cool": "cooling", "measure": "measurement", "reset": "reset",
             "simd": "transport", "move": "transport", "shuttle": "transport", "split": "transport",
             "merge": "transport", "swap": "transport", "junction": "transport"}
_NOT_TRANSPORT_CLASSES = {"cool", "gate", "measure", "reset", None}


def _ms(us: float) -> float:
    return round(us / 1000.0, 3)


def performance(arch, prog, res, *, table: str = "qccdsim_jones", t_transport_ms: float | None = None) -> dict:
    total_us = float(res.total_us)
    cats: dict[str, float] = {}
    for typ, us in (res.us_by_type or {}).items():
        cats[_CATEGORY.get(typ, typ)] = cats.get(_CATEGORY.get(typ, typ), 0.0) + float(us)
    rest = total_us - sum(cats.values())
    if rest > 1e-6:
        cats["other"] = rest
    breakdown = [{"category": k, "ms": _ms(v), "share": round(v / total_us, 4) if total_us else 0.0}
                 for k, v in sorted(cats.items(), key=lambda kv: -kv[1]) if v > 0]
    transport_by_class = {str(c): _ms(us) for c, us in sorted((res.us_by_class or {}).items(), key=lambda kv: -kv[1])
                          if c not in _NOT_TRANSPORT_CLASSES}

    cycles = list(res.cycles or [])
    steps = [c for c in cycles if c.type != "init"]
    n_transport = sum(1 for c in steps if _CATEGORY.get(c.type) == "transport")
    n_cool = sum(1 for c in steps if c.type == "cool")
    moved = [c.n_participants for c in steps if _CATEGORY.get(c.type) == "transport" and c.n_participants]
    counts = {"instructions": len(prog.instructions), "steps": len(steps),
              "two_qubit_gates": int(res.n_gate_pairs), "one_qubit_gates": int(getattr(res, "n_1q_gates", 0)),
              "measurements": int(res.n_measure), "resets": int(res.n_reset),
              "transport_steps": n_transport, "cooling_steps": n_cool,
              "ions_moved_per_transport_step": round(sum(moved) / len(moved), 2) if moved else 0.0}

    longest = [{"instruction": c.instr_id, "type": c.type, "class": c.cls, "us": round(c.t1 - c.t0, 2),
                "ions": c.n_participants}
               for c in sorted(steps, key=lambda c: -(c.t1 - c.t0))[:5]]

    jt = res.junction_transits_by_node or {}
    top_j = jt.most_common(5) if hasattr(jt, "most_common") else sorted(jt.items(), key=lambda kv: -kv[1])[:5]
    j_med = median(jt.values()) if jt else 0
    mv = res.moves_per_ion or {}
    top_ions = mv.most_common(3) if hasattr(mv, "most_common") else sorted(mv.items(), key=lambda kv: -kv[1])[:3]
    hotspots = {"junctions": [{"node": n, "transits": int(k)} for n, k in top_j],
                "junctions_used": len(jt), "median_junction_transits": j_med,
                "junction_imbalance": round(top_j[0][1] / j_med, 2) if top_j and j_med else None,
                "busiest_ions": [{"ion": i, "moves": int(k)} for i, k in top_ions],
                "ions_in_trap_at_gates": {str(k): int(v) for k, v in sorted((res.chain_len_at_gate or {}).items())}}

    failed = sorted(res.rules.failed()) if getattr(res, "rules", None) is not None else []
    report = {
        "table": table,
        "total": {"ms": _ms(total_us), **({"transport_table_ms": round(t_transport_ms, 3)}
                                           if t_transport_ms is not None else {})},
        "breakdown": breakdown,
        "transport_by_class_ms": transport_by_class,
        "counts": counts,
        "longest": longest,
        "hotspots": hotspots,
        "heating": {"peak_quanta": round(float(res.peak_quanta), 3), "peak_ion": res.peak_quanta_ion},
        "rules": {"failed": failed, "violations": res.rules.by_rule() if failed else {}},
    }
    report["bottleneck"] = _bottleneck(report)
    return report


def _bottleneck(r: Mapping) -> list[str]:
    """Sentences a person can act on, each computed from the report (no free text)."""
    out: list[str] = []
    total = r["total"]["ms"]
    bd = r["breakdown"]
    if not bd or not total:
        return ["The program is empty: nothing ran."]
    top = bd[0]
    c = r["counts"]
    if top["category"] == "transport":
        cls = r["transport_by_class_ms"]
        parts = ", ".join(f"{k} {v:g} ms" for k, v in list(cls.items())[:3])
        out.append(f"Transport dominates: {top['ms']:g} ms of {total:g} ms ({top['share']:.0%}), "
                   f"in {c['transport_steps']} steps ({parts}). Fewer or more parallel moves shorten the round most.")
    elif top["category"] == "cooling":
        out.append(f"Cooling dominates: {top['ms']:g} ms of {total:g} ms ({top['share']:.0%}) in "
                   f"{c['cooling_steps']} cooling steps. Moves that deposit less heat, or cooling in parallel "
                   "with transport, would shorten the round most.")
    elif top["category"] == "gates":
        out.append(f"Gates dominate: {top['ms']:g} ms of {total:g} ms ({top['share']:.0%}) for "
                   f"{c['two_qubit_gates']} two-qubit gates. The schedule is already gate-bound; only "
                   "more gates in parallel helps.")
    else:
        out.append(f"{top['category'].capitalize()} takes the largest share: {top['ms']:g} ms of {total:g} ms "
                   f"({top['share']:.0%}).")
    if len(bd) > 1 and bd[1]["share"] >= 0.15:
        out.append(f"Next is {bd[1]['category']}: {bd[1]['ms']:g} ms ({bd[1]['share']:.0%}).")
    h = r["hotspots"]
    if h["junctions"]:
        j = h["junctions"][0]
        if h["junction_imbalance"] and h["junction_imbalance"] >= 1.5:
            out.append(f"Junction {j['node']} is a hotspot: {j['transits']} transits, "
                       f"{h['junction_imbalance']}x the median of {h['median_junction_transits']:g}.")
        else:
            out.append(f"Junction traffic is even ({h['junctions_used']} junctions, about "
                       f"{h['median_junction_transits']:g} transits each), so no single junction is the limit.")
    if c["transport_steps"] and c["ions_moved_per_transport_step"] <= 1.5:
        out.append(f"Transport steps move {c['ions_moved_per_transport_step']:g} ions on average: "
                   "moves are nearly serial.")
    if r["rules"]["failed"]:
        out.append("The run breaks rule(s) " + ", ".join(r["rules"]["failed"]) +
                   ": its time is not a valid result until they pass.")
    return out


def frame_at(times_us: list[float], t_us: float) -> int:
    """The last step that has started by `t_us` (frame index into the program)."""
    return max(0, bisect_right(times_us, t_us) - 1)


def compare(a: Mapping, b: Mapping, labels: tuple[str, str] = ("A", "B")) -> dict:
    """Two run summaries (each with `performance`) side by side, with B minus A."""
    pa, pb = a["performance"], b["performance"]
    rows = []

    def row(name, va, vb, unit=""):
        d = None if va is None or vb is None else round(vb - va, 3)
        rows.append({"metric": name, labels[0]: va, labels[1]: vb, "difference": d, "unit": unit})
    row("round time", pa["total"]["ms"], pb["total"]["ms"], "ms")
    cats = sorted({x["category"] for x in pa["breakdown"]} | {x["category"] for x in pb["breakdown"]})
    ga = {x["category"]: x["ms"] for x in pa["breakdown"]}
    gb = {x["category"]: x["ms"] for x in pb["breakdown"]}
    for cat in cats:
        row(cat, ga.get(cat, 0.0), gb.get(cat, 0.0), "ms")
    for k in ("instructions", "transport_steps", "cooling_steps", "two_qubit_gates"):
        row(k.replace("_", " "), pa["counts"][k], pb["counts"][k])
    row("peak heating", pa["heating"]["peak_quanta"], pb["heating"]["peak_quanta"], "quanta")
    ta, tb = pa["total"]["ms"], pb["total"]["ms"]
    if ta and tb:
        faster, slower = (labels[0], labels[1]) if ta <= tb else (labels[1], labels[0])
        ratio = max(ta, tb) / min(ta, tb)
        verdict = (f"{faster} is faster: {min(ta, tb):g} ms against {max(ta, tb):g} ms ({ratio:.2f}x)."
                   if abs(ta - tb) > 1e-9 else "Both take the same time.")
    else:
        verdict = "One of the runs has no time."
    diffs = sorted(((r["metric"], r["difference"]) for r in rows
                    if r["unit"] == "ms" and r["metric"] != "round time" and r["difference"]),
                   key=lambda kv: -abs(kv[1]))
    if diffs:
        m, d = diffs[0]
        verdict += f" The biggest difference is {m}: {labels[1]} {'+' if d > 0 else ''}{d:g} ms."
    return {"rows": rows, "verdict": verdict,
            "bottlenecks": {labels[0]: pa["bottleneck"], labels[1]: pb["bottleneck"]}}
