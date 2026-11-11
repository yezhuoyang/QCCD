"""A functional Helios router for the published ring/cache/logic floorplan.

This is intentionally a baseline, not Quantinuum's proprietary real-time compiler.  It
parses the input circuit, places qubits in the eight operation zones, transports one
operand to its partner for each two-qubit gate, and emits the same source certificate as
the other repository routers.  Every emitted operation is replayed and rule-checked.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Sequence

from ..arch import Architecture
from ..cost.models import corrected_model
from ..ir.tsir import Participant, TSIR
from ..verify import verify
from .c2lr_router import GATES_2Q, U3, Refused, RouteError, _parse, layers_reading_together
from .cooling import insert_cooling
from .layered import Emitter, certificate

COMPILER_NAME = "helios-functional-router (baseline)"


def _operation_sites(arch: Architecture) -> list[str]:
    return sorted(n.id for n in arch.device.labelled("operation"))


def _path(arch: Architecture, src: str, dst: str) -> list[str]:
    back: dict[str, str | None] = {src: None}
    todo = deque([src])
    while todo:
        here = todo.popleft()
        if here == dst:
            break
        for sid in arch.device.incidence[here]:
            nxt = arch.device.segments[sid].other(here)
            if nxt not in back:
                back[nxt] = here
                todo.append(nxt)
    if dst not in back:
        raise RouteError(f"no Helios transport path from {src} to {dst}")
    out = [dst]
    while back[out[-1]] is not None:
        out.append(back[out[-1]])
    return list(reversed(out))


def _move(em: Emitter, arch: Architecture, ion: str, dst: str, ops: Sequence[int]) -> None:
    """Move through functional sites; bare junctions are crossed inside one step."""
    full = _path(arch, em.pos[ion], dst)
    stops = [n for n in full if arch.device.nodes[n].kind == "site"]
    at = {n: i for i, n in enumerate(full)}
    for src, end in zip(stops, stops[1:]):
        route = full[at[src]:at[end] + 1]
        via = tuple(arch.device.segment_between(a, b).id for a, b in zip(route, route[1:]))
        em.emit(type="simd", cls="helios_shuttle", mode="inter",
                participants=(Participant(ion, src, end, via=via),),
                meta={"kind": "helios_route", "op": list(ops)})


def compile_helios(qasm_text: str, arch: Architecture, *, name: str = "circuit") -> tuple[TSIR, dict]:
    """Compile OpenQASM onto Helios's eight published two-ion operation zones."""
    if (arch.device.params or {}).get("family") != "helios":
        raise Refused(f"{arch.name} is not a Helios functional floorplan")
    n, ops, _ = _parse(qasm_text)
    sites = _operation_sites(arch)
    if n > len(sites):
        raise Refused(f"this baseline uses one initial operation zone per qubit: {n} > {len(sites)}")
    ion = {q: f"q{q}" for q in range(n)}
    placement = {ion[q]: sites[q] for q in range(n)}
    prog = TSIR(name=name, arch_spec=f"arch/{arch.name}.arch.json",
                meta={"compiler": COMPILER_NAME,
                      "qubit_map": {str(q): ion[q] for q in range(n)}})
    em = Emitter(prog, dict(placement))
    em.emit(type="init", placement=dict(placement), quanta={i: 0.0 for i in placement},
            meta={"compiler": COMPILER_NAME, "circuit": name, "arch": arch.name})
    em.emit(type="cool", broadcast=True, meta={"kind": "state_prep"})

    for layer in layers_reading_together(ops):
        one = [op for op in layer if op.name in U3]
        if one:
            em.play(one, ion)
        for kind in ("measure", "reset"):
            group = [op for op in layer if op.name == kind]
            if group:
                em.emit(type=kind, ions=tuple(ion[op.qubits[0]] for op in group),
                        meta={"kind": "readout" if kind == "measure" else "reset",
                              "op": [op.i for op in group]})
        for op in (op for op in layer if op.name in GATES_2Q):
            a, b = (ion[q] for q in op.qubits)
            home = em.pos[b]
            _move(em, arch, b, em.pos[a], [op.i])
            em.play([op], ion)
            _move(em, arch, b, home, [op.i])

    return prog, certificate(prog, ops, name, arch.name, n, ion, placement, em, COMPILER_NAME)


def compile_with_router(qasm_text: str, arch_path, workdir, *, table: str = "helios",
                        name: str = "circuit") -> tuple[Path, Path]:
    """Write, cool, replay, and check a certified Helios compilation."""
    t0 = time.time()
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    arch_path = Path(arch_path).resolve()
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))
    (work / "circuit.qasm").write_text(qasm_text, encoding="utf-8")
    bridge = Path(__file__).resolve().parents[2] / "Compiler" / "bridge"
    ex = subprocess.run([sys.executable, str(bridge / "export_arch.py"), str(arch_path), "-o",
                         str(work / "device.expanded.json")], capture_output=True, text=True)
    if ex.returncode:
        raise RouteError(f"export_arch failed: {(ex.stdout + ex.stderr)[-400:]}")
    prog, cert = compile_helios(qasm_text, arch, name=name)
    prog.save(str(work / "prog.tsir.json"), indent=None)
    (work / "prog.qcert.json").write_text(json.dumps(cert), encoding="utf-8")
    model = corrected_model(table)
    cooled = insert_cooling(prog, arch, model).program
    cooled.meta.update(prog.meta)
    final = work / "prog.cooled.tsir.json"
    cooled.save(str(final), indent=None)
    rep = verify(cooled, arch, model, check_metrics=False)
    failed = sorted(rep.rules.summary().get("failed") or [])
    if failed:
        violation = next(v for v in rep.rules.violations if v.rule in failed)
        raise RouteError(f"the Helios router fails {failed}: {violation.rule}: {violation.message}")
    (work / "compile.json").write_text(json.dumps({
        "mode": "helios-functional-router", "table": table,
        "compile_s": round(time.time() - t0, 3), "instructions": len(cooled.instructions),
    }, indent=1), encoding="utf-8")
    return final, work / "prog.qcert.json"

