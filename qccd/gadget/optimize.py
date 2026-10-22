"""Designing a floorplan for a program: where the places go decides how far the ions travel.

Shuttling is most of what a QCCD machine does -- every lattice surgery, every magic state,
every rest in a memory is a trip -- so the floorplan is an optimisation problem with the
program as its input.  This searches the family of floorplans `town.town(rows=...)` builds
(places in rows between streets, doors on the streets): which places stand in which row, in
which order.  Every candidate is *scheduled*, not estimated: its traffic and makespan are the
schedule's own, and a candidate that refuses an instruction or breaks a machine rule is not a
candidate.  The result is an ordinary floorplan file, to be read, judged (F1-F9) and edited
by hand like any other.

    python -m qccd.gadget optimize examples/gadgets/algorithms/shor15.alg \\
        --floorplan examples/gadgets/floorplans/shor.floor --budget 40 -o better.floor

The objective is `traffic` (ion-sites shuttled), `makespan`, or `both` (their product, so a
change must pay in one what it costs in the other).  The search is a seeded hill climb over
two moves: swap two places in one row, or move a place to another row.  It is a design aid,
not a proof of optimality, and says so.
"""

from __future__ import annotations

import random
import time

from .algorithm import Algorithm
from .city import schedule
from .floorplan import write
from .judge import judge_schedule, traffic
from .town import STREET, town

__all__ = ["rows_of", "evaluate", "optimise"]


def rows_of(lib) -> list[list[tuple[str, str]]]:
    """The rows of a floorplan in the town family: places grouped by the street band they
    stand in, left to right; the control floor (decoder, classical memory) left out."""
    top = lib[lib.top]
    rows: dict[int, list] = {}
    for i in top.instances:
        m = lib[i.master]
        if i.master == "junc9" or m.family in ("decoder", "archive"):
            continue
        y0 = i.y + (m.bbox[1] if m.bbox else 0.0)
        rows.setdefault(int(y0 // STREET), []).append((i.x + (m.bbox[0] if m.bbox else 0.0), i.name, i.master))
    return [[(n, mm) for _, n, mm in sorted(r)] for _, r in sorted(rows.items())]


def evaluate(alg: Algorithm, rows, leaves, *, name: str = "candidate", check: bool = False) -> dict:
    """Build the floorplan for `rows`, schedule `alg` on it, and measure."""
    try:
        lib = town(leaves, name=name, rows=rows)
    except ValueError as exc:
        return {"ok": False, "why": str(exc)}
    gir = schedule(alg, lib, leaves)
    if gir["refused"]:
        return {"ok": False, "why": f"{len(gir['refused'])} instruction(s) refused: "
                                    f"{gir['refused'][0][1]}", "lib": lib}
    t = traffic(gir, lib)
    out = {"ok": True, "lib": lib, "gir": gir, "traffic": t["ion_sites"],
           "makespan_us": gir["makespan_us"], "metrics": t}
    if check:
        rep = judge_schedule(lib, leaves, gir)
        failed = [r for r in rep["failed"] if r != "G8"]      # refresh is the next step's job
        out["rules_failed"] = failed
        out["ok"] = not failed
    return out


def _score(r: dict, objective: str) -> float:
    if not r.get("ok"):
        return float("inf")
    if objective == "traffic":
        return float(r["traffic"])
    if objective == "makespan":
        return float(r["makespan_us"])
    return float(r["traffic"]) * float(r["makespan_us"])


def optimise(alg: Algorithm, rows, leaves, *, objective: str = "traffic", budget: int = 40,
             seed: int = 1, log=None) -> dict:
    """A seeded hill climb from `rows`.  Returns the start, the best, and every step taken."""
    log = log or (lambda *a, **k: None)
    rng = random.Random(seed)
    t0 = time.time()
    cur = [list(r) for r in rows]
    start = evaluate(alg, cur, leaves, name="start", check=True)
    if not start["ok"]:
        raise ValueError(f"the starting floorplan does not run the program: {start.get('why') or start.get('rules_failed')}")
    best, best_rows = start, [list(r) for r in cur]
    history = [{"step": 0, "move": "start", "traffic": start["traffic"],
                "makespan_us": start["makespan_us"], "accepted": True}]
    log(f"  start: traffic {start['traffic']:,} ion-sites, makespan {start['makespan_us'] / 1e6:.2f} s")
    for step in range(1, budget + 1):
        cand = [list(r) for r in best_rows]
        if rng.random() < 0.6 or len(cand) < 2:
            r = rng.randrange(len(cand))
            if len(cand[r]) < 2:
                continue
            i, j = rng.sample(range(len(cand[r])), 2)
            cand[r][i], cand[r][j] = cand[r][j], cand[r][i]
            move = f"swap {cand[r][j][0]} and {cand[r][i][0]} in row {r}"
        else:
            a, b = rng.sample(range(len(cand)), 2)
            if len(cand[a]) < 2:
                continue
            k = rng.randrange(len(cand[a]))
            p = cand[a].pop(k)
            cand[b].insert(rng.randrange(len(cand[b]) + 1), p)
            move = f"move {p[0]} from row {a} to row {b}"
        res = evaluate(alg, cand, leaves, name=f"c{step}")
        ok = _score(res, objective) < _score(best, objective)
        if ok:
            res = evaluate(alg, cand, leaves, name=f"c{step}", check=True)
            ok = res["ok"] and _score(res, objective) < _score(best, objective)
        history.append({"step": step, "move": move, "accepted": ok,
                        "traffic": res.get("traffic"), "makespan_us": res.get("makespan_us"),
                        "why": res.get("why") or res.get("rules_failed")})
        if ok:
            best, best_rows = res, cand
            log(f"  step {step}: {move} -> traffic {res['traffic']:,}, makespan "
                f"{res['makespan_us'] / 1e6:.2f} s")
    return {"objective": objective, "budget": budget, "seed": seed,
            "seconds": round(time.time() - t0, 1),
            "start": {k: start[k] for k in ("traffic", "makespan_us", "metrics")},
            "best": {k: best[k] for k in ("traffic", "makespan_us", "metrics")},
            "rows": best_rows, "lib": best["lib"], "history": history}


def floorplan_text(result: dict, *, title: str, comment: str = "") -> str:
    return write(result["lib"], title=title, comment=comment)
