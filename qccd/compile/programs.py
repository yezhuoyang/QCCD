"""Program builders -- the one place a TSIR program is constructed.

Before this existed, "rotate the loop" lived in an example file and, separately, in the
CLI; "walk a path" lived only in the CLI where no example could reach it. Two
implementations of the same movement is exactly how a platform starts reporting two
different numbers for the same thing.

Every builder here takes an `Architecture` and returns a `TSIR`, so every entry point --
examples, CLI, tests, the compiler -- goes through the same code and the same verifier.

    rotate(arch, k)        rigid lockstep rotation: one template, every ion on the loop
    walk(arch, n)          point-to-point shuttling: the lowest common denominator
    odd_even(arch, k)      transposition sort: the scheme rotation is measured against
    from_deck(arch)        the shipped 24-ancilla schedule, imported from the artifact
"""

from __future__ import annotations

from typing import Callable, Mapping, Sequence

from ..arch import Architecture
from ..ir import provenance as prov
from ..ir.import_deck import DEFAULT_HTML, import_schedule
from ..ir.tsir import TSIR, Instruction, Participant, loop_shift
from .oddeven import cyclic_shift_target, odd_even_sort_program

__all__ = ["rotate", "walk", "odd_even", "from_deck", "build", "BUILDERS", "closed_loops"]


def closed_loops(arch: Architecture) -> list[str]:
    return [lid for lid, lp in arch.device.loops.items() if lp.closed]


def _loop_ions(arch: Architecture, loop_id: str) -> tuple[list[str], list[str]]:
    nodes = list(arch.device.loops[loop_id].nodes)
    return [f"d{i}" for i in range(len(nodes))], nodes


def _spec(arch: Architecture) -> str:
    return f"arch/{arch.name}.arch.json"


# --------------------------------------------------------------------------- rotate


def rotate(
    arch: Architecture,
    k: int | None = None,
    *,
    loop_id: str | None = None,
    name: str | None = None,
) -> TSIR:
    """A rigid rotation of the whole loop by `k`.

    One instruction, one template, every ion on the loop: the movement PLAN §1's thesis
    rests on. `k` defaults to a quarter turn.
    """
    loops = closed_loops(arch)
    if not loops:
        raise ValueError(f"{arch.name} has no closed loop, so it cannot rotate")
    loop_id = loop_id or loops[0]
    ions, nodes = _loop_ions(arch, loop_id)
    if k is None:
        k = max(1, len(nodes) // 4)
    prog = TSIR(name=name or f"rotate_{k}", arch_spec=_spec(arch))
    prog.add(Instruction(type="init", id=prog.next_id(),
                         placement={ion: nodes[i] for i, ion in enumerate(ions)},
                         quanta={ion: 0.0 for ion in ions}))
    if k:
        prog.add(Instruction(
            type="simd", id=prog.next_id(), mode="inter",
            cls="rotate_cw" if k >= 0 else "rotate_ccw",
            template=loop_shift(loop_id, k),
            meta={"kind": "rotate", "hops": abs(k)}))
    # a builder has no user frame to point at -- what emitted these instructions is the
    # builder, so the record names it and the site is this line
    prov.stamp(prog, "programs.rotate", arch=arch.name, k=k, loop_id=loop_id)
    return prog


# ----------------------------------------------------------------------------- walk


def longest_path(arch: Architecture) -> list[str]:
    """A long simple path through the device, by double BFS.

    Every device is connected transport hardware, so something can be shuttled from
    somewhere to somewhere; this finds a route that exercises whatever the geometry has,
    which on a grid means crossing its degree-4 X-junctions.
    """
    dev = arch.device

    def bfs(src: str) -> tuple[str, dict[str, str]]:
        parent = {src: src}
        order = [src]
        head = 0
        while head < len(order):
            cur = order[head]
            head += 1
            for nb in dev.neighbours(cur):
                if nb not in parent:
                    parent[nb] = cur
                    order.append(nb)
        return order[-1], parent

    far, _ = bfs(next(iter(dev.nodes)))
    end, parent = bfs(far)
    path = [end]
    while path[-1] != far:
        path.append(parent[path[-1]])
    return list(reversed(path))


def walk(arch: Architecture, n_ions: int = 4, *, name: str | None = None) -> TSIR:
    """Shuttle a few ions along a long path, one *trap-to-trap* move per cycle.

    A move goes trap to trap and lists the segments it crosses in `via`; it never stops
    on a junction. That is not a detail: junctions hold no ions and R2 allows at most one
    on one at any instant, so an ion transits a junction and rests only in a trap.
    """
    dev = arch.device
    path = longest_path(arch)
    stops = [n for n in path if dev.nodes[n].kind == "site"]
    if len(stops) < 2:
        raise ValueError(f"{arch.name}: no two trap sites are connected")
    idx = {n: i for i, n in enumerate(path)}
    vias = []
    for a, b in zip(stops, stops[1:]):
        hop = path[idx[a]:idx[b] + 1]
        vias.append(tuple(dev.segment_between(u, v).id for u, v in zip(hop, hop[1:])))

    n_ions = max(1, min(n_ions, len(stops) // 2))
    prog = TSIR(name=name or f"walk_{n_ions}", arch_spec=_spec(arch))
    prog.add(Instruction(
        type="init", id=prog.next_id(),
        placement={f"d{i}": stops[i] for i in range(n_ions)},
        quanta={f"d{i}": 0.0 for i in range(n_ions)},
        meta={"note": f"{len(stops)} trap stops along a {len(path)}-node path"}))

    at = {f"d{i}": i for i in range(n_ions)}
    while max(at.values()) < len(stops) - 1:
        taken = set(at.values())
        movers = []
        for ion in sorted(at, key=lambda k: -at[k]):
            nxt = at[ion] + 1
            if nxt < len(stops) and nxt not in taken:
                movers.append((ion, stops[at[ion]], stops[nxt], vias[at[ion]]))
                taken.discard(at[ion])
                taken.add(nxt)
        if not movers:
            break
        for ion, _, _, _ in movers:
            at[ion] += 1
        prog.add(Instruction(
            type="simd", id=prog.next_id(), cls="shuttle", mode="inter",
            participants=tuple(Participant(i, a, b, via=v) for i, a, b, v in movers)))
    prov.stamp(prog, "programs.walk", arch=arch.name, n_ions=n_ions)
    return prog


# ------------------------------------------------------------------------- odd-even


def odd_even(
    arch: Architecture,
    k: int | None = None,
    *,
    loop_id: str | None = None,
    name: str | None = None,
) -> TSIR:
    """Odd-even transposition sort realizing a cyclic shift by `k` -- rotation's rival."""
    loops = closed_loops(arch)
    if not loops:
        raise ValueError(f"{arch.name} has no closed loop to sort on")
    loop_id = loop_id or loops[0]
    ions, nodes = _loop_ions(arch, loop_id)
    if k is None:
        k = max(1, len(nodes) // 4)
    prog = odd_even_sort_program(
        arch, ions, cyclic_shift_target(ions, k), loop_id=loop_id,
        arch_spec=_spec(arch), name=name or f"oddeven_shift_{k}",
    ).program
    prov.stamp(prog, "programs.odd_even", arch=arch.name, k=k, loop_id=loop_id)
    return prog


# ----------------------------------------------------------------------------- deck


def from_deck(arch: Architecture, *, html_path=DEFAULT_HTML, **kw) -> TSIR:
    """The shipped 24-ancilla schedule, imported from the standalone artifact."""
    prog = import_schedule(arch, html_path=html_path, **kw)
    # the honest answer for an imported schedule: these instructions came from the
    # artifact, through this importer, not from anybody's Python
    prov.stamp(prog, "programs.from_deck", arch=arch.name,
               html=str(getattr(html_path, "name", html_path)))
    return prog


BUILDERS: Mapping[str, Callable[..., TSIR]] = {
    "rotate": rotate,
    "walk": walk,
    "oddeven": odd_even,
    "deck": from_deck,
}


def build(arch: Architecture, kind: str, *args, **kw) -> TSIR:
    """Build a program by name, falling back to `walk` where the shape does not fit.

    A device with no closed loop cannot rotate or run a packed sort; rather than refuse,
    the fallback runs the movement that device *can* do, so every architecture in the
    catalogue has something to execute and to look at.
    """
    try:
        fn = BUILDERS[kind]
    except KeyError:
        raise ValueError(
            f"unknown program {kind!r}; have: {', '.join(sorted(BUILDERS))}") from None
    try:
        return fn(arch, *args, **kw)
    except ValueError as exc:
        if kind in ("rotate", "oddeven") and "closed loop" in str(exc):
            return walk(arch)
        raise
