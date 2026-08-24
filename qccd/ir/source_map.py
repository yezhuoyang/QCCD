"""The join between a compiled program and the circuit it came from.

The animation shows which hardware instruction is executing. For a compiled program there
is a second thing worth showing, and it is the one somebody debugging a compiler actually
wants: **which statement of their QASM that instruction is discharging** -- and, while the
machine is only shuttling, which statement it is travelling towards.

Two sources are joined here, and the reason there are two is worth stating.

* The **certificate** is authoritative but sparse. Its gate witnesses carry
  `instr -> dag`, and the Lean checker decides them -- but a witness names one instruction
  per circuit operation, and a `cx` is seven pulses. On `steane_esm/grid9x9` that is 10 of
  the 50 gate instructions; on the BB[[144,12,12]] rotation schedule, 690 of 3,018. Four
  fifths of the program would have no answer.
* The **program** is dense but is the compiler's own account: every instruction the
  compiler emits carries `meta.op`, the circuit operations it serves, stamped as it was
  emitted.

So the dense map is used, and the sparse one is used to **check** it: every gate witness
must find its `dag` in the `meta.op` of the instruction it names, or `build()` raises. A
compiler whose stamps drifted from the claims the checker verified cannot quietly draw a
plausible page.

    from qccd.ir.source_map import build
    src = build(prog, cert, qasm_path)

It lives here rather than in `Compiler/bridge/` because both callers are on this side of
that boundary: the compiler's renderer and `qccd studio`. A design tool that had to import
a script out of `Compiler/` to open a compiled program would have the dependency exactly
backwards.
"""

from __future__ import annotations

from pathlib import Path


class Mismatch(Exception):
    """The compiler's per-instruction stamps disagree with its own certificate."""


def _ops_of(instr) -> list[int]:
    raw = (instr.meta or {}).get("op")
    if not isinstance(raw, list):
        return []
    return [int(x) for x in raw]


def build(prog, cert: dict, qasm_path: str | Path, *, check: bool = True) -> dict:
    """The `source` payload `qccd.viz.render_html` draws the circuit pane from."""
    qasm = Path(qasm_path)
    text = qasm.read_text(encoding="utf-8")

    ops = [
        {
            "i": o["i"],
            "name": o["name"],
            "q": list(o.get("qubits", ())),
            "p": [round(float(x), 6) for x in o.get("params", ())],
            # a certificate written before source lines were carried has none; the pane
            # then simply never highlights, rather than highlighting the wrong line
            "line": int(o.get("line", 0)),
        }
        for o in cert["circuit_ops"]
    ]
    known = {o["i"] for o in ops}

    # An instruction that PERFORMS a circuit operation, against one that is moving ions so
    # that a later one can. The distinction is the instruction's own type, not a guess:
    # `simd` is transport and everything else that carries a stamp is the operation.
    realises: dict[str, list[int]] = {}
    toward: dict[str, list[int]] = {}
    for instr in prog.instructions:
        got = [i for i in _ops_of(instr) if i in known]
        if not got:
            continue
        (toward if instr.type == "simd" else realises)[str(instr.id)] = sorted(got)

    if check:
        _cross_check(cert, realises)

    return {
        "name": qasm.name,
        "lines": text.splitlines(),
        "ops": ops,
        "realises": realises,
        "toward": toward,
    }


def _cross_check(cert: dict, realises: dict[str, list[int]]) -> None:
    """Every verified witness must agree with the stamp on the instruction it names."""
    bad: list[str] = []
    for g in cert.get("gates", ()):
        stamped = realises.get(str(g["instr"]))
        if stamped is None:
            bad.append(f"op {g['dag']}: witness names instruction {g['instr']}, "
                       f"which claims no circuit operation at all")
        elif g["dag"] not in stamped:
            bad.append(f"op {g['dag']}: witness names instruction {g['instr']}, "
                       f"which claims ops {stamped}")
        if len(bad) >= 5:
            break
    if bad:
        raise Mismatch(
            "the compiler's per-instruction attribution disagrees with its certificate; "
            "the page would show a join nothing has verified:\n  " + "\n  ".join(bad))
