"""QCCDCert target code as a TSIR programme, for the studio's player.

The one design-page template (`qccd.viz.render.render_html`) plays TSIR.  This module turns a
QCCDCert execution into TSIR without deciding anything: each QCCDCert instruction becomes one
TSIR instruction (a transport -> one `simd` shuttle with its moves as participants and the
studio segments of each path as `via`; a pulse -> one `gate`; a broadcast -> one `gate` on all
its ions; readout -> `measure`; pumping -> `reset`; a barrier -> `barrier`).  Waits and the
controller latch move nothing and change no qubit; they become `barrier` markers so the step
numbers of the page and of the target code agree.

`certificate(...)` gives `qccd.ir.source_map.build` the circuit side of the join, so the
Program column of every QCCDCert page is drawn by the same code as every other page's, from
each instruction's QCCDCert origin (`origin.statement`, the source statement it discharges).
"""

from __future__ import annotations

import math
import re
from pathlib import Path

from .machine import Translation


def angle(e) -> float:
    """An exact QCCDCert angle as a float, for the player only (the compiler keeps it exact)."""
    if e == "pi":
        return math.pi
    if isinstance(e, (int, float)):
        return float(e)
    kind, b = next(iter(e.items()))
    if kind == "rational":
        return b["numerator"] / b["denominator"]
    if kind == "neg":
        return -angle(b["argument"])
    l, r = angle(b["left"]), angle(b["right"])
    if kind == "add":
        return l + r
    if kind == "sub":
        return l - r
    if kind == "mul":
        return l * r
    return l / r


def _origin(ins: dict) -> int | None:
    kind, b = next(iter(ins.items()))
    if kind == "pulse":
        return b["operation"].get("origin", {}).get("statement")
    if kind == "broadcast":
        return b["pulses"][0].get("origin", {}).get("statement")
    if kind in ("readout", "pump", "latch"):
        return b.get("origin", {}).get("statement")
    if kind == "barrier":
        return b.get("boundary", {}).get("origin", {}).get("statement")
    return None


def operations(qasm_text: str) -> list[tuple[int, int, str]]:
    """(op index, root statement index, statement text) of each executable statement: the
    circuit's operations, numbered 0, 1, ... as every circuit map of the platform numbers
    them (a lesson's `realises:1` is the second operation)."""
    out = []
    for i, (line, t) in enumerate(statement_lines(qasm_text)):
        name = t.split("(")[0].split()[0] if t else "?"
        if name in ("qreg", "creg", "gate", "opaque", "include"):
            continue
        out.append((len(out), i, t))
    return out


def to_tsir(tr: Translation, execution: dict, *, name: str, arch_spec: str = "",
            stages: dict | None = None, qasm: str | None = None) -> dict:
    """`stages` (the compiler's stage file) maps `origin.statement` to the root statement, and
    `qasm` maps the root statement to its operation index, which `meta.op` then holds."""
    root_of = resolved_index(stages) if stages else {}
    op_of = {root: k for k, root, _ in operations(qasm)} if qasm is not None else None
    hw = tr.hardware["device"]
    base = hw["base"]
    cals = base["calibrations"]
    site = tr.site_ids
    q = lambda i: f"q{i}"  # noqa: E731
    out = [{"type": "init", "id": 0, "placement": {q(i): site[s] for i, s in enumerate(execution["initial"])},
            "quanta": {q(i): 0.0 for i in range(len(execution["initial"]))},
            "meta": {"compiler": "QCCDCert", "kind": "init"}}]
    stmts: list[int | None] = []
    for k, ins in enumerate(execution["instructions"], start=1):
        kind, b = next(iter(ins.items()))
        st = _origin(ins)
        if st is not None and stages:
            st = root_of.get(st, st)
        if st is not None and op_of is not None:
            st = op_of.get(st)
        meta = {"compiler": "QCCDCert", "kind": kind, "target_index": k - 1}
        if st is not None:
            meta["op"] = [st]
        if kind == "transport":
            parts = []
            for m in b["moves"]:
                p = base["geometry"]["paths"][m["path"]]
                parts.append({"ion": q(m["ion"]), "from": site[p["src"]], "to": site[p["dst"]],
                              "via": list(tr.path_segments[m["path"]])})
            out.append({"type": "simd", "id": k, "class": "shuttle", "mode": "inter",
                        "participants": parts, "meta": meta})
        elif kind == "pulse":
            pk, pb = next(iter(b["operation"]["pulse"].items()))
            where = site[cals[b["calibration"]]["site"]]
            if pk == "r":
                out.append({"type": "gate", "id": k, "mode": "intra", "gate": "R", "arity": 1,
                            "params": [[angle(pb["theta"]), angle(pb["phi"])]], "ions": [q(pb["ion"])],
                            "sites": [where], "meta": meta})
            else:
                out.append({"type": "gate", "id": k, "mode": "intra", "gate": "MS",
                            "params": [[angle(pb["theta"])]], "pairs": [[q(pb["control"]), q(pb["target"])]],
                            "sites": [where], "meta": meta})
        elif kind == "broadcast":
            ions, params, where = [], [], []
            for p, c in zip(b["pulses"], b.get("calibrations", [])):
                pb = p["pulse"]["r"]
                ions.append(q(pb["ion"]))
                params.append([angle(pb["theta"]), angle(pb["phi"])])
                where.append(site[cals[c]["site"]])
            out.append({"type": "gate", "id": k, "mode": "intra", "gate": "R", "arity": 1,
                        "params": params, "ions": ions, "sites": where, "meta": meta})
        elif kind == "readout":
            out.append({"type": "measure", "id": k, "ions": [q(b["ion"])], "meta": meta})
        elif kind == "pump":
            out.append({"type": "reset", "id": k, "ions": [q(b["ion"])], "meta": meta})
        else:  # idle, latch, barrier: no ion moves and no qubit changes
            meta["note"] = {"idle": f"idle {b.get('duration', '')}", "latch": "latch saved",
                            "barrier": "barrier"}[kind]
            out.append({"type": "barrier", "id": k, "meta": meta})
        stmts.append(st)
    _stamp_transports(out)
    return {"name": name, "arch_spec": arch_spec, "instructions": out, "metrics": {},
            "meta": {"compiler": "QCCDCert"}}


def _stamp_transports(instructions: list[dict]) -> None:
    """A QCCDCert transport carries no source origin (it serves the pulses after it).  For the
    Program column, stamp it with the statement of the next instruction that uses one of its
    ions, marked `op_from: next use` -- a display read off the target code, not a claim of
    the compiler."""
    nxt: dict[str, list[int]] = {}
    for ins in reversed(instructions):
        if ins["type"] == "simd":
            ops = sorted({o for p in ins["participants"] for o in nxt.get(p["ion"], [])})
            if ops:
                ins["meta"]["op"] = ops
                ins["meta"]["op_from"] = "next use"
            continue
        op = (ins.get("meta") or {}).get("op")
        if op:
            ions = list(ins.get("ions") or []) + [q for pr in ins.get("pairs") or [] for q in pr]
            for q in ions:
                nxt[q] = op


def statement_lines(qasm: str) -> list[tuple[int, str]]:
    """(line, text) of each top-level statement after the `OPENQASM` header, in the order of
    the compiler's source bundle (an `include` is a statement there)."""
    out = []
    body = re.sub(r"//[^\n]*", "", qasm)
    for m in re.finditer(r"[^;{}]*(?:\{[^}]*\})?[^;{}]*;", body):
        text = m.group(0)
        line = body.count("\n", 0, m.start() + len(text) - len(text.lstrip())) + 1
        t = text.strip()
        if t.startswith("OPENQASM"):
            continue
        out.append((line, t))
    return out


def resolved_index(stages: dict) -> dict[int, int]:
    """QCCDCert's `origin.statement` counts the statements of the program with its includes
    inlined; map each such index back to the root statement it belongs to."""
    bundle = stages["source"]["bundle"]
    units = bundle["units"]
    root_id = stages["source"]["root"]
    root = next((u for u in units if u["id"] == root_id), units[0])
    # the user's file: the unit that is neither a builtin library nor a profile wrapper
    original = next((u for u in units if not str(u["id"]).startswith(("builtin:", "profile:"))), root)

    def unit_for(path: str):
        if path == "<original-source>":
            return original
        name = path[1:-1] + ".inc" if path.startswith("<") and path.endswith(">") else path
        return next((u for u in units if u is not root and str(u["id"]).endswith(name)), None)

    flat: list[tuple[object, int]] = []

    def walk(unit, depth=0):
        for i, st in enumerate(unit["statements"]):
            if "include" in st:
                inc = unit_for(st["include"]["path"])
                if inc is not None and depth < 16:
                    walk(inc, depth + 1)
            else:
                flat.append((unit, i))

    walk(root)
    return {k: i for k, (u, i) in enumerate(flat) if u is original}


def certificate(prog_json: dict, qasm_text: str) -> dict:
    """The circuit side of the join `qccd.ir.source_map.build(prog, cert, qasm)` draws the
    Program column from: the source's operations (`circuit_ops`, numbered 0, 1, ... with their
    lines), against which every instruction's `meta.op` -- the operation QCCDCert says it
    discharges (`origin.statement`) -- is matched.  `gates` is empty: QCCDCert issues
    no per-gate witnesses, because its theorem covers the whole target code (Meaning)."""
    lines = dict((i, ln) for i, (ln, _) in enumerate(statement_lines(qasm_text)))
    ops = []
    for k, root, t in operations(qasm_text):
        name = t.split("(")[0].split()[0] if t else "?"
        qubits = [int(x) for x in re.findall(r"\[(\d+)\]", t)]
        if name == "if":
            body = t.split(")", 1)[1].strip() if ")" in t else t
            name = "if " + (body.split("(")[0].split()[0] if body else "?")
        ops.append({"i": k, "name": name, "qubits": qubits[:3], "params": [], "line": lines[root]})
    return {"compiler": "QCCDCert", "circuit_ops": ops, "gates": [],
            "note": "QCCDCert target code: Meaning is a clause of the compiler's theorem, no per-gate witness"}
