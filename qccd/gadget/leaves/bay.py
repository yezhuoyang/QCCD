"""The bay: a racetrack ring with dock traps and corner doors, and its conveyor compiler.

Every verified surface-code place (syndrome extraction, preparation, readout, lattice
surgery, magic-state injection, storage) is a bay: `2W` storage slots around a racetrack,
dock traps on spurs off chosen slots (outward, so the two rows never share a trap
position), and up to four doors, one on each corner slot, each a spur to a port trap
and a stub.  Doors are directional by convention: blocks come in through `TL`/`BL` and
leave through `TR`/`BR`, so with the ring turning clockwise a block leaves in the order it
arrived (a place is FIFO, like a corridor).

**The conveyor round.**  Ancillas live in the docks; data ions ride the ring.  At every
step the compiler docks each data ion that is (a) standing beside the dock of the check it
is due at next and (b) has every contact the schedule orders before it done; all docked
pairs take one batched CX; the docked ions return to their slots; the ring turns one
slot.  The schedule is `Patch.precedence()`, a partial order, and any execution that
respects it is the textbook circuit up to commuting gates -- which is what lets a moving
machine realise a layered circuit without ever standing in layers.  A contact that is
enabled is met within one revolution, so the loop always finishes.

Where the ions sit decides how long a round takes, so `place()` searches data slots and
ancilla docks for the fewest rotation steps, from a fixed seed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from ...api import Machine
from ...arch.builder import DeviceBuilder
from ...ir.tsir import Instruction, loop_shift
from .common import TrackedProgram, add_port_stub, declare_port_classes, declare_port_zone

__all__ = ["Bay", "bay", "BayProgram", "dry_round", "place", "CORNERS"]

CORNERS = ("TL", "TR", "BR", "BL")


@dataclass
class Bay:
    name: str
    W: int
    docks: list[int]
    ports: dict[str, str]              # port name -> corner
    machine: Machine
    widths: dict[str, int] = field(default_factory=dict)

    @property
    def N(self) -> int:
        return 2 * self.W

    def corner_slot(self, corner: str) -> int:
        return {"TL": 0, "TR": self.W - 1, "BR": self.W, "BL": 2 * self.W - 1}[corner]

    def slot_node(self, port: str) -> str:
        return f"S{self.corner_slot(self.ports[port])}"

    def trap(self, port: str) -> str:
        return f"P_{port}"

    def stub(self, port: str) -> str:
        return f"X_{port}"

    def size(self) -> list[float]:
        xs = [nd.pos[0] for nd in self.machine.arch.device.nodes.values()]
        ys = [nd.pos[1] for nd in self.machine.arch.device.nodes.values()]
        return [max(xs) - min(xs), max(ys) - min(ys)]


def bay(name: str, W: int, docks, ports: dict[str, str], widths: dict[str, int]) -> Bay:
    corners = {0: "TL", W - 1: "TR", W: "BR", 2 * W - 1: "BL"}
    docks = sorted(int(s) for s in docks)
    for s in docks:
        if s in corners or not 0 <= s < 2 * W:
            raise ValueError(f"{name}: dock slot {s} is a corner or off the ring")
    d = DeviceBuilder("explicit", shape="bay", W=W)
    pos = {}
    for i in range(W):
        pos[i] = (float(i), 0.0)
        pos[W + i] = (float(W - 1 - i), 1.0)
    for s in range(2 * W):
        labels = ["top" if s < W else "bottom"]
        if s in docks:
            labels.append("dock")
        d.site(f"S{s}", *pos[s], zone="data", labels=labels)
    for s in range(2 * W):
        d.segment(f"E{s}", f"S{s}", f"S{(s + 1) % (2 * W)}", loop="L0", labels=["rail"])
    d.loop("L0", [f"S{s}" for s in range(2 * W)], closed=True, kind="ring",
           note="the rigid-rotation orbit")
    for s in docks:
        x, y = pos[s]
        ay = -1.0 if s < W else 2.0
        d.site(f"A{s}", x, ay, zone="trap", labels=["spur_trap"])
        d.segment(f"V{s}", f"S{s}", f"A{s}", length=1.0, labels=["spur"])
    m = Machine.from_device(d.build(), name=name)
    declare_port_zone(m)
    declare_port_classes(m)
    for port, corner in ports.items():
        s = {"TL": 0, "TR": W - 1, "BR": W, "BL": 2 * W - 1}[corner]
        x, y = pos[s]
        dy = -1.0 if corner in ("TL", "TR") else 1.0
        m.add_site(f"P_{port}", x, y + dy, zone="trap", labels=["port_trap"], to=[f"S{s}"],
                   segment_ids=[f"V_{port}"])
        add_port_stub(m, f"X_{port}", (x, y + 2 * dy), f"P_{port}",
                      width=widths.get(port, 1), segment=f"E_{port}")
    return Bay(name, W, docks, dict(ports), m, dict(widths))


class BayProgram(TrackedProgram):
    """`TrackedProgram` on a bay: rotation with an offset from the home frame, doors, and
    the conveyor round."""

    def __init__(self, b: Bay, name: str, placement: dict[str, str]):
        super().__init__(b.machine, name, placement)
        self.bay = b
        self.N = b.N
        self.offset = 0                  # net rotation since the home frame

    def slot(self, ion: str) -> int | None:
        node = self.pos[ion]
        return int(node[1:]) if node.startswith("S") else None

    def occupant(self, slot: int) -> str | None:
        here = self.at(f"S{slot}")
        return here[0] if here else None

    def shortest(self, delta: int) -> int:
        d = delta % self.N
        return d if d <= self.N // 2 else d - self.N

    def rotate(self, delta: int, **meta) -> None:
        if not delta:
            return
        self.prog.add(Instruction(
            type="simd", id=self.prog.next_id(),
            cls="rotate_cw" if delta > 0 else "rotate_ccw", mode="inter",
            template=loop_shift("L0", int(delta)),
            meta=dict(meta, kind="rotate", hops=abs(int(delta)))))
        for ion, where in list(self.pos.items()):
            if where.startswith("S"):
                self.pos[ion] = f"S{(int(where[1:]) + delta) % self.N}"
        self.offset = (self.offset + delta) % self.N

    def restore(self, **meta) -> None:
        self.rotate(self.shortest(-self.offset), restore=True, **meta)

    # -- doors ----------------------------------------------------------------------------

    def absorb(self, port: str, ions: list[str], homes: list[int], *,
               cool_every: int | None = None, **meta) -> None:
        """Take `ions` in through `port`, one at a time, each onto its home slot.  With
        `cool_every`, the ions already inside are cooled every that-many arrivals."""
        corner = self.bay.corner_slot(self.bay.ports[port])
        for j, (ion, h) in enumerate(zip(ions, homes)):
            if cool_every and j and j % cool_every == 0:
                self.cool_inside(door=port, **meta)
            self.rotate(self.shortest(corner - h - self.offset), door=port, **meta)
            if self.occupant(corner) is not None:
                raise RuntimeError(f"{self.bay.name}: door slot S{corner} is occupied")
            self.move("port_in", [(ion, self.bay.trap(port))], door=port, **meta)
            self.move("undock", [(ion, f"S{corner}")], door=port, **meta)

    def cool_inside(self, **meta) -> None:
        """Cool every ion still inside the bay, by name.  A broadcast cool is illegal while an
        ion waits on a stub (a stub cannot cool: R6); naming the ions inside is not."""
        inside = [ion for ion, where in self.pos.items()
                  if self.dev.nodes[where].zone_type != "port"]
        if inside:
            self.prog.add(Instruction(type="cool", id=self.prog.next_id(),
                                      ions=tuple(sorted(inside)), meta=dict(meta)))

    def emit(self, port: str, ions: list[str], *, cool_every: int | None = None, **meta) -> None:
        """Send `ions` out through `port`, in the order given.  With `cool_every`, the ions
        still inside are cooled before every that-many departures: bringing each ion to the
        door turns the ring, and the rotations heat everyone left on it."""
        corner = self.bay.corner_slot(self.bay.ports[port])
        for j, ion in enumerate(ions):
            if cool_every and j % cool_every == 0:
                self.cool_inside(door=port, **meta)
            self.rotate(self.shortest(corner - self.slot(ion)), door=port, **meta)
            self.move("dock", [(ion, self.bay.trap(port))], door=port, **meta)
            self.move("port_out", [(ion, self.bay.stub(port))], door=port, **meta)

    # -- gates at docks ---------------------------------------------------------------------

    def delta_to(self, ion: str, node: str) -> int:
        """The shortest rotation that brings `ion` (on the ring) to slot `node`."""
        return self.shortest(int(node[1:]) - self.slot(ion))

    def at_docks(self, ions: list[str], fn, **meta) -> None:
        """Bring each ion to a dock with room (a dock trap holds two: its resident ancilla
        and one visitor) and apply `fn(batch)` there, all docks in parallel."""
        free = [s for s in self.bay.docks if len(self.at(f"A{s}")) < 2]
        if not free:
            raise RuntimeError(f"{self.bay.name}: no dock has room")
        pending = list(ions)
        guard = 0
        while pending:
            here = [(ion, s) for s in free for ion in [self.occupant(s)] if ion in pending]
            if here:
                self.move("dock", [(ion, f"A{s}") for ion, s in here], **meta)
                fn([ion for ion, _ in here])
                self.move("undock", [(ion, f"S{s}") for ion, s in here], **meta)
                for ion, _ in here:
                    pending.remove(ion)
            if pending:
                self.rotate(1, **meta)
                guard += 1
                if guard > 2 * self.N:
                    raise RuntimeError(f"{self.bay.name}: ions never reach a free dock")

    def conveyor_round(self, patch, data_ion: dict[int, str], anc_ion: dict[str, str],
                       anc_dock: dict[str, int], *, round_index: int = 0,
                       tag: str = "round") -> dict:
        """One syndrome-extraction round of `patch`: ancillas prepared in their docks, the
        contacts in any order `patch.precedence()` allows, ancillas read.  Returns counts."""
        checks = {c.name: c for c in patch.checks}
        before = patch.precedence()
        ancillas = [anc_ion[c.name] for c in patch.checks]
        xs = [anc_ion[c.name] for c in patch.checks if c.basis == "X"]
        self.reset(ancillas, op=tag, round=round_index)
        if xs:
            self.gate1("H", xs, op=tag, round=round_index)
        pending = {name: [q for _, q in c.corners] for name, c in checks.items()}
        done: set[tuple[str, int]] = set()
        steps = batches = 0
        idle = 0
        while any(pending.values()):
            enabled = []
            for name, queue in pending.items():
                if not queue:
                    continue
                q = queue[0]
                if self.slot(data_ion[q]) == anc_dock[name] and \
                        all(b in done for b in before[(name, q)]):
                    enabled.append((name, q))
            if enabled:
                self.move("dock", [(data_ion[q], f"A{anc_dock[n]}") for n, q in enabled],
                          op=tag, round=round_index)
                pairs = [(anc_ion[n], data_ion[q]) if checks[n].basis == "X"
                         else (data_ion[q], anc_ion[n]) for n, q in enabled]
                self.gate("CX", pairs, op=tag, round=round_index)
                self.move("undock", [(data_ion[q], f"S{anc_dock[n]}") for n, q in enabled],
                          op=tag, round=round_index)
                for n, q in enabled:
                    done.add((n, q))
                    pending[n].pop(0)
                batches += 1
                idle = 0
            if any(pending.values()):
                self.rotate(1, op=tag, round=round_index)
                steps += 1
                idle += 1
                if idle > self.N:
                    raise RuntimeError(f"{self.bay.name}: the conveyor round is stuck")
        if xs:
            self.gate1("H", xs, op=tag, round=round_index)
        self.measure(ancillas, op=tag, round=round_index,
                     checks=[c.name for c in patch.checks])
        return {"steps": steps, "batches": batches}


def dry_round(patch, data_slot: dict[int, int], anc_dock: dict[str, int], N: int) -> tuple[int, int]:
    """(rotation steps, contact batches) of one conveyor round, without emitting anything."""
    before = patch.precedence()
    pending = {c.name: [q for _, q in c.corners] for c in patch.checks}
    done: set = set()
    steps = batches = idle = 0
    while any(pending.values()):
        enabled = [(n, qs[0]) for n, qs in pending.items() if qs
                   and (data_slot[qs[0]] + steps) % N == anc_dock[n]
                   and all(b in done for b in before[(n, qs[0])])]
        if enabled:
            batches += 1
            idle = 0
            for n, q in enabled:
                done.add((n, q))
                pending[n].pop(0)
        if any(pending.values()):
            steps += 1
            idle += 1
            if idle > N:
                return 10 ** 9, 0
    return steps, batches


def place(patch, N: int, docks: list[int], *, fixed: dict[int, int] | None = None,
          forbidden: set[int] = frozenset(), seed: int = 7, iterations: int = 4000
          ) -> tuple[dict[int, int], dict[str, int], int]:
    """Data slots and ancilla docks for a short conveyor round: random restarts, then
    single swaps accepted when they do not lengthen the round."""
    rng = random.Random(seed)
    names = [c.name for c in patch.checks]
    data = [q for q in range(patch.n) if q not in (fixed or {})]
    slots = [s for s in range(N) if s not in forbidden and s not in (fixed or {}).values()]
    if len(slots) < len(data) or len(docks) < len(names):
        raise ValueError("a bay too small for the patch")

    def cost(ds, ad):
        steps, batches = dry_round(patch, ds, ad, N)
        restore = min(steps % N, N - steps % N)
        return steps + restore + 0.25 * batches

    best = None
    for restart in range(8):
        chosen = rng.sample(slots, len(data))
        ds = {**(fixed or {}), **dict(zip(data, chosen))}
        ad = dict(zip(names, rng.sample(docks, len(names))))
        c = cost(ds, ad)
        for _ in range(iterations // 8):
            if rng.random() < 0.5:
                a, b = rng.sample(names, 2)
                ad[a], ad[b] = ad[b], ad[a]
                c2 = cost(ds, ad)
                if c2 <= c:
                    c = c2
                else:
                    ad[a], ad[b] = ad[b], ad[a]
            else:
                q = rng.choice(data)
                used = set(ds.values())
                options = [s for s in slots if s not in used] + [ds[x] for x in data if x != q]
                s = rng.choice(options)
                other = next((x for x in data if ds[x] == s), None)
                old = ds[q]
                ds[q] = s
                if other is not None:
                    ds[other] = old
                c2 = cost(ds, ad)
                if c2 <= c:
                    c = c2
                else:
                    ds[q] = old
                    if other is not None:
                        ds[other] = s
        if best is None or c < best[2]:
            best = (dict(ds), dict(ad), c)
    return best[0], best[1], dry_round(patch, best[0], best[1], N)[0]
