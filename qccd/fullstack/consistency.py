"""The four-line caption, checked (docs/FULLSTACK_PLAN.md §2.6a).

At every hardware instruction the page names a logical operation, a PPM, a QEC operation, a
Clifford statement and the instruction itself.  This module checks, for EVERY instruction that
serves a circuit operation, that each of those resolves and that they agree with one another:

  hardware -> Clifford   every ion the instruction touches is a physical qubit the .sysprog event
                         names (an ion moving for a gate is one of that gate's operands)
  Clifford -> PPM        every data qubit the event touches belongs to a logical qubit the PPM
                         (or frame) names: data[i] is logical q[i // n] of an n-qubit code
  every layer resolves   the event has a .qec statement, that a .qppm statement, that a .prog line

A link moved to the wrong statement breaks the second check (the test suite moves one and
expects a failure).  The ion check holds by the devirtualizer's own map; it fails if the circuit
and the program drift apart.
"""
from __future__ import annotations

import re


def _named_qubits(text: str) -> set:
    """Logical qubits a .qppm statement names: `q[3]` -> 3 (`q[0+1]` evaluates)."""
    out = set()
    for idx in re.findall(r"\bq\[([^\]]+)\]", text):
        try:
            out.add(int(eval_nat(idx)))
        except ValueError:
            pass
    return out


def eval_nat(text: str) -> int:
    from .sysprog import evaluate
    return evaluate(text.replace(" ", ""), {})


def check(trace: dict, tsir: dict, limit: int = 50) -> dict:
    n = trace["code"]["n"]
    links = trace["links"]
    sys_q = {int(k): v for k, v in links["sys"].items()}
    qec_p = {int(k): v for k, v in links["qec"].items()}
    qppm_g = {int(k): v for k, v in links["qppm"].items()}
    qppm_lines = trace["layers"]["qppm"]["lines"]
    events = {e[0]: e for e in trace["events"]}
    op_event = [c[2] for c in trace["circuit"]]
    problems: list[str] = []
    resolved = 0
    checked_instrs = 0

    def chain(uid):
        e = events[uid]
        line, frames = e[4], e[5]
        for cand in [line] + [f[1] for f in reversed(frames)]:
            if cand in sys_q:
                q = sys_q[cand][0]
                p = qec_p.get(q, [None])[0]
                g = qppm_g.get(p, [None])[0] if p else None
                return q, p, g
        return None, None, None

    for ins in tsir["instructions"]:
        ops = (ins.get("meta") or {}).get("op") or []
        if not ops:
            continue
        checked_instrs += 1
        uids = {op_event[o] for o in ops if o < len(op_event)}
        phys = set()
        for u in uids:
            phys |= {f"q{p}" for p in events[u][7] if p is not None}
        ions = set(ins.get("ions") or []) | {p["ion"] for p in ins.get("participants") or []}
        stray = ions - phys
        if ins.get("type") != "cool" and stray and len(problems) < limit:
            problems.append(f"instruction #{ins['id']} ({ins.get('type')}) touches {sorted(stray)[:4]}, "
                            f"which no .sysprog event it serves names")
        for u in uids:
            q, p, g = chain(u)
            if not (q and p and g):
                if len(problems) < limit:
                    problems.append(f"instruction #{ins['id']}: event {u} ({events[u][1]}, .sysprog line "
                                    f"{events[u][4]}) does not resolve to every layer (qec={q}, qppm={p}, prog={g})")
                continue
            resolved += 1
            data = [int(m[1]) for m in (re.fullmatch(r"data\[(\d+)\]", v) for v in events[u][6]) if m]
            if data:
                named = _named_qubits(qppm_lines[p - 1])
                bad = sorted({d // n for d in data} - named)
                if bad and len(problems) < limit:
                    problems.append(f"instruction #{ins['id']}: event {u} ({events[u][1]}, .sysprog line {events[u][4]}) "
                                    f"touches logical qubit(s) {bad}, which its PPM `{qppm_lines[p - 1].strip()}` "
                                    f"does not name")
    return {"ok": not problems, "instructions_checked": checked_instrs, "event_chains_resolved": resolved,
            "problems": problems}
