"""Every program the lattice schedule copies, built at the machine's distance (docs/FULLSTACK_PLAN.md,
slice S9).  A scheduled gadget is a translated copy of the program of its shape
(`lattice.Gadget.shape`); this module turns a shape into that program -- the circuit, the tiled grid
it is played on, the compiled program and its certificate -- and gives its machine time to the
microsecond, from the program itself (replayed, cooled, as the verifier plays it).

    ("merge", letter, patches, corridor)  `corridor_merge`, MERGE_ROUNDS rounds
    ("ht",)                               `hadamard_bar`, MERGE_ROUNDS rounds a phase: the tile, its seam
                                          and its right neighbour
    ("prep", "Z" | "X")                   every data ion reset, then (X) an H on each
    ("prep", "Y")                         |Ȳ> in place, Gidney's (`y_prep_qasm`)
    ("read", "Y")                         Ȳ read in place, his preparation run backwards (`y_read_qasm`)
    ("prep", "T")                         the corner injection's preparation (Li 2015): the corner
                                          in T|+>, the rest of the patch in |0> or |+>; compiled as
                                          its Clifford twin (the corner's T an S: the same pulses,
                                          one angle), since R10 compares Clifford tableaux
    ("read", "Z" | "X")                   (X) an H on each data ion, then every one measured
    ("idle",)                             a held patch's syndrome round: its measure ions reset,
                                          then one round -- what a patch runs while no gadget holds it

Where a program sits: its lattice point (row, col) is on tile (col // (d + 1), row // (d + 1)) of
the gadget, counted from the gadget's least x and least y -- `corridor_merge`'s own convention, the
seam row and column belonging to the tile before them.  On its grid (`machine.device`, spacing 2)
data point (row, col) is the trap T{col}_{row + 1}h at (2 col + 1, 2 row + 2).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from . import machine as M

__all__ = ["Built", "build", "machine_us", "check", "KINDS", "IDLE", "kind"]

IDLE = ("idle",)
KINDS = ("merge", "ht", "prep", "read", "idle")


@dataclass
class Built:
    shape: tuple
    qasm: str
    rows: int
    cols: int
    arch: object
    prog: object
    cert: object
    ions: int


def kind(shape) -> str:
    return shape[0]


def _header(n: int) -> list:
    return ['OPENQASM 2.0;', 'include "qelib1.inc";', f"qreg q[{n}];", f"creg c[{n}];"]


def _circuit(shape) -> tuple:
    """(qasm, rows, cols, coords, faces, ions) of a shape's program."""
    from ...qec import experiments as E
    d = M.DISTANCE
    n = d * d
    k = shape[0]
    if k == "merge":
        _, letter, pats, cor = shape
        e = E.corridor_merge(d, letter, [(tuple(t), s) for t, s in pats], {tuple(c) for c in cor}, M.MERGE_ROUNDS)
        rows, cols = (max(x[i] for x in e.coords) + 1 for i in (0, 1))
        return e.qasm, rows, cols, e.coords, e.faces, e.n_data + e.n_ancilla
    if k == "ht":
        e = E.hadamard_bar(d, "Z", M.MERGE_ROUNDS)
        return e.qasm, d, 2 * d + 1, e.coords, e.faces, e.n_data + e.n_ancilla
    if shape == ("prep", "Y"):
        e = E.y_memory(d)
        return E.y_prep_qasm(d), d, d, e.coords, e.faces, e.n_data + e.n_ancilla
    if shape == ("read", "Y"):
        e = E.y_memory(d)
        return E.y_read_qasm(d), d, d, e.coords, e.faces, e.n_data + e.n_ancilla
    if shape == IDLE:
        e = E.rotated_surface(d, 1)
        lines = e.qasm.splitlines()
        at = next(i for i, ln in enumerate(lines) if ln.startswith("creg")) + 1
        resets = [f"reset q[{q}];" for q in range(e.n_data, e.n_data + e.n_ancilla)]
        return "\n".join(lines[:at] + resets + lines[at:]) + "\n", d, d, None, None, e.n_data + e.n_ancilla
    lines = _header(n)
    if shape in (("prep", "Z"), ("prep", "X")):
        lines += [f"reset q[{q}];" for q in range(n)]
        lines += [f"h q[{q}];" for q in range(n)] if shape[1] == "X" else []
    elif shape == ("prep", "T"):
        # `injection`'s preparation: c >= r in |0>, r > c in |+>, the corner (0, 0) in T|+> (twin: S|+>)
        lines += [f"reset q[{q}];" for q in range(n)]
        lines += [f"h q[{r * d + c}];" for r in range(d) for c in range(d) if r > c]
        lines += ["h q[0];", "s q[0];"]
    elif shape in (("read", "Z"), ("read", "X")):
        lines += [f"h q[{q}];" for q in range(n)] if shape[1] == "X" else []
        lines += [f"measure q[{q}] -> c[{q}];" for q in range(n)]
    else:
        raise ValueError(f"no program for the shape {shape!r}")
    return "\n".join(lines) + "\n", d, d, None, None, n


@lru_cache(maxsize=4)
def _model():
    from ...cost.models import corrected_model
    return corrected_model("quantinuum")


def build(shape) -> Built:
    """The shape's program, compiled for the machine (uncooled, as `compile_tiled` gives it)."""
    from ...compile.tiled_surface import compile_tiled
    qasm, rows, cols, coords, faces, ions = _circuit(shape)
    arch = M.device(rows, cols)
    prog, cert, _ = compile_tiled(qasm, rows, cols, arch=arch, coords=coords, faces=faces)
    return Built(shape, qasm, rows, cols, arch, prog, cert, ions)


def cooled(b: Built):
    """The program as the machine runs it: cooling inserted, as every check plays it."""
    from ...compile.cooling import insert_cooling
    return insert_cooling(b.prog, b.arch, _model()).program


def machine_us(b: Built, prog=None) -> float:
    """The program's machine time, µs, from its own replay (cooled)."""
    from ...verify.replay import replay
    return replay(prog or cooled(b), b.arch, _model(), check_rules=False).total_us


def check(b: Built, tmp, block_ops: int = 2000) -> dict:
    """Every rule (`qccd.verify`) and R10 (the certificate replayed against the source circuit,
    block by block) on the cooled program: {"ions", "failed", "us", "r10_blocks"}.  R10's
    helper lives with its tests (`tests/test_c2lr_router.py`), so the repo's tests directory
    must be importable.  `block_ops`: a block's least size; a program's round, so that equal
    rounds are equal blocks and are compared once."""
    from pathlib import Path

    from test_c2lr_router import _r10_blocks

    from ...verify import verify
    rep = verify(cooled(b), b.arch, _model(), check_metrics=False)
    failed, us = rep.rules.summary().get("failed"), rep.result.total_us
    del rep
    blocks = _r10_blocks(b.prog, b.cert, b.arch, b.qasm, Path(tmp), block_ops)
    return {"ions": b.ions, "failed": failed, "us": us, "r10_blocks": blocks}
