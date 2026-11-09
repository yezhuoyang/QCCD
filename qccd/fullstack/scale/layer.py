"""One layer of the machine as one program (docs/FULLSTACK_PLAN.md, S10's design study): disjoint lattice surgeries and
patches that only hold, on one lattice, every plaquette playing the same moves at once.

A cat product's program walks its ions over the seams, and a different product walks them differently.  A layer's ions
step one junction and back, the same steps whatever the layer holds: `waveforms` is the same list, cycle for cycle, for
one merge, for eight, for four merges among patches that only hold, and for one merge across a register.  So its time does
not depend on what is in it, nothing travels, and no two regions need drivers of their own.

    build(groups, idle)     its program: `experiments.layer_merge`, compiled by `compile_tiled`
    waveforms(built)        each cycle: ("move", the motions in it) or the kind of instruction
    rest_traps(...)         what the rest of the machine leaves around it: every other ion of its tiles' lattice, at rest
"""
from __future__ import annotations

from . import machine as M
from . import programs as PR

__all__ = ["build", "waveforms", "facing", "rest_traps", "round_ops"]


def facing(x: int, y: int = 0) -> tuple:
    """Z̄Z̄ of tiles (x, y) and (x, y + 1) across the seam between them: no corridor."""
    return ("Z", [((x, y + 1), "top"), ((x, y), "bottom")], set())


def rest_traps(e, rows: int, cols: int) -> list:
    """Every data and measure trap of a rows x cols lattice that the experiment `e` does not use: seams and tiles in full, nothing
    travels, so an ion the machine holds is on its trap unless a program names it."""
    from ...compile.tiled_surface import _data_trap, _face_trap
    used = {_data_trap(*q) for q in e.coords} | {_face_trap(*f) for f in e.faces}
    every = {_data_trap(r, c) for r in range(rows) for c in range(cols)}
    every |= {_face_trap(i, j) for i in range(rows - 1) for j in range(cols - 1)}
    return sorted(every - used)


def build(groups, idle=(), rounds: int | None = None, full: bool = False) -> PR.Built:
    """`full`: with the rest of the lattice's ions on their traps, at rest, the machine as it stands."""
    from ...compile.tiled_surface import compile_tiled
    from ...qec.experiments import layer_merge
    e = layer_merge(M.DISTANCE, groups, idle, rounds)
    rows, cols = (max(x[i] for x in e.coords) + 1 for i in (0, 1))
    arch = M.device(rows, cols)
    prog, cert, _ = compile_tiled(e.qasm, rows, cols, arch=arch, coords=e.coords, faces=e.faces,
                                  resting=rest_traps(e, rows, cols) if full else ())
    return PR.Built(("layer", len(groups), len(idle)), e.qasm, rows, cols, arch, prog, cert, e.n_data + e.n_ancilla)


def round_ops(b: PR.Built) -> int:
    """How many source operations one round of a layer is: from one round's readout to the next's."""
    from ...compile.c2lr_router import _parse
    _, ops, _ = _parse(b.qasm)
    at = [o.i for o in ops if o.name == "measure"]
    starts = [i for i, j in zip(at[1:], at) if i != j + 1]
    return starts[1] - starts[0] if len(starts) > 1 else 2000


def waveforms(b: PR.Built) -> list:
    dev = b.arch.device
    out = []
    for ins in b.prog.instructions:
        if ins.type == "simd" and ins.cls == "shuttle":
            seen = set()
            for p in ins.participants:
                node, steps = p.src, []
                for sid in p.via:
                    s, e = dev.segments[sid].ends
                    nxt = e if s == node else s
                    a, c = dev.nodes[node].pos, dev.nodes[nxt].pos
                    steps.append((round(c[0] - a[0], 3), round(c[1] - a[1], 3)))
                    node = nxt
                seen.add(tuple(steps))
            out.append(("move", tuple(sorted(seen))))
        else:
            out.append((ins.type, getattr(ins, "gate", None)))
    return out
