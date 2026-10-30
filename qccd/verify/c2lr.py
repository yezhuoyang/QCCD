"""What a C2LR step does: the exchange rule, the two-crystal exception, and who is heated.

docs/c2lr.md section 2.  A `swap_or_stay` template supplies one bit per crystal (shift toward
the pair's junction, or away); the DEVICE supplies the pairs (`qccd.arch.c2lr`).  Then one
shared waveform exchanges, at every junction in play, the two wells nearest it -- one in each
paired leg -- and C2LR⁻¹ brings every crystal back to a leg centre.  So for a pair of legs
(A, B) the crystal toward the junction in A goes to B and the one toward it in B goes to A,
which is every case at once:

    A toward, B toward              swap
    A away, B away                  stay (both)
    A toward, B away, B occupied    enter: B now holds two (C1's exception)
    A toward, B empty               move
    B holds two, the entered one    exit: it returns the way it came
      toward

A leg holding two crystals has one near each of its ends.  Only the crystal nearer the
step's junction can be shifted toward it, and C1 allows the two only between an enter and the
next exit OF THE SAME STEP TYPE: the crystal that entered is the one that leaves, by the
variation it entered by.  This module tracks which crystal entered which leg, at which
junction and by which variation, through every instruction of a programme -- template steps,
plain moves, re-placements -- because C1 is a fact about the whole history, not one step.

The replay (`qccd.verify.replay`) owns one `C2LRTracker` per run on a C2LR device and hands
each cycle's `C2LRStep` to the rules on the `CycleView`; `walk` replays positions alone, for
the certificate check and the page, so all three expand a template the same way.

Refusals come in two strengths.  A step that cannot be executed at all -- the device is not a
C2LR lattice, a named crystal is not placed or not in a leg -- raises `ReplayError`.  A step
that executes but breaks a C rule -- a toward bit on a leg with no partner, an exit by another
variation, a third crystal in a leg -- is executed as the exchange rule says and reported, so
both verifiers judge it and say which rule.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator, Mapping

from ..arch.c2lr import NotC2LR, Variation, declared, geometry, is_c2lr, variation_of
from ..ir.tsir import C2LR_TEMPLATES, TSIR, Instruction, Participant, template_problems

__all__ = ["C2LRStep", "C2LRTracker", "walk", "is_c2lr_class"]


@dataclass(frozen=True)
class C2LRStep:
    """One cycle on a C2LR device, as the rules see it.

    ``kind``      "swap_or_stay" | "reorder_or_not" for a template step, else "other"
    ``exchange``  True only for a swap-or-stay step: its routes are the device's matching,
                  derived here and never read from the programme (the R2/R3/R5/R22 exemption)
    ``movers``    the crystals that changed leg; ``stayers`` every other crystal placed
    ``heated``    every crystal the step's waveform plays on (C9: all of them)
    ``c1``/``c2``/``c6``  what the step broke of those rules, in words
    ``excepted_before``/``excepted_after``  the legs holding two crystals under C1's
                  exception (one entered by a swap-or-stay enter and not yet left)
    ``check_before``  the state before this cycle was not judged after an earlier one (the
                  first cycle, or one after a re-placing `init`)
    """

    kind: str = "other"
    variation: Variation | None = None
    participants: tuple[Participant, ...] = ()
    movers: tuple[str, ...] = ()
    stayers: tuple[str, ...] = ()
    reversed: tuple[str, ...] = ()
    heated: frozenset[str] = frozenset()
    c1: tuple[str, ...] = ()
    c2: tuple[str, ...] = ()
    c6: tuple[str, ...] = ()
    excepted_before: frozenset[str] = frozenset()
    excepted_after: frozenset[str] = frozenset()
    check_before: bool = False

    @property
    def exchange(self) -> bool:
        return self.kind == "swap_or_stay"

    @property
    def template(self) -> bool:
        return self.kind in C2LR_TEMPLATES

    @property
    def assumed(self) -> bool:
        return self.variation is not None and self.variation.assumed


def is_c2lr_class(arch, cls: str | None) -> bool:
    """Is `cls` one of the device's C2LR step classes (a step it plays from a template)?"""
    if not cls:
        return False
    if cls in C2LR_TEMPLATES:
        return True
    try:
        return str(arch.simd_class(cls).get("type")) == "c2lr"
    except KeyError:
        return False


class _Err(Exception):
    """A step that cannot be executed; the caller re-raises it as its own error type."""


@dataclass
class C2LRTracker:
    """The two-crystal state of every leg, followed through a programme.

    ``near``     crystal -> the junction it is nearer, for a crystal sharing its leg
    ``entered``  crystal -> (variation key, junction, leg, variation label) of the enter that
                 put it into a leg holding another crystal, while it is still there
    """

    arch: object
    near: dict[str, str] = field(default_factory=dict)
    entered: dict[str, tuple] = field(default_factory=dict)
    fresh: bool = True

    def __post_init__(self) -> None:
        self.geo = geometry(self.arch)       # NotC2LR: the caller decides what that means
        self.legs = frozenset(self.geo.legs)

    # ------------------------------------------------------------------ state

    def _contents(self, pos: Mapping[str, str]) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for ion, s in pos.items():
            if s in self.legs:
                out.setdefault(s, []).append(ion)
        for v in out.values():
            v.sort()
        return out

    def excepted(self, pos: Mapping[str, str]) -> frozenset[str]:
        """The legs C1's exception covers now: two crystals, exactly one of which entered
        this leg by a swap-or-stay enter."""
        out = set()
        for leg, ions in self._contents(pos).items():
            if len(ions) == 2:
                rec = [i for i in ions if self.entered.get(i, (None, None, None))[2] == leg]
                if len(rec) == 1:
                    out.add(leg)
        return frozenset(out)

    def _settle(self, pos: Mapping[str, str]) -> None:
        """Forget what no longer holds: a crystal alone in its leg is centred and carries no
        enter; a crystal that left its leg carries nothing from it."""
        contents = self._contents(pos)
        for ion in list(self.near):
            leg = pos.get(ion)
            if leg not in contents or len(contents[leg]) < 2:
                del self.near[ion]
        for ion in list(self.entered):
            leg = pos.get(ion)
            if self.entered[ion][2] != leg or len(contents.get(leg, ())) < 2:
                del self.entered[ion]

    def _exit_problems(self, leaving: Mapping[str, str], before: Mapping[str, str],
                       var: Variation | None, how: str) -> list[str]:
        """C1: who may leave a leg the exception covers -- the crystal that entered it, by
        the variation it entered by.  A leg holding two WITHOUT the exception already fails
        C1's occupancy half, so leaving it is not judged twice."""
        out = []
        excepted = self.excepted(before)
        for ion, leg in sorted(leaving.items()):
            if leg not in excepted:
                continue
            rec = self.entered.get(ion)
            if rec is None or rec[2] != leg:
                out.append(f"{ion} leaves leg {leg} by {how}, but {leg} holds two crystals and "
                           f"{ion} is not the one that entered it: only the crystal that entered "
                           f"may leave (C1)")
            elif var is None or rec[0] != var.key:
                out.append(f"{ion} entered leg {leg} by {rec[3]} and leaves it by {how}; it must "
                           f"leave by the step type it entered by (C1)")
        return out

    # ------------------------------------------------------------------ steps

    def observe(self, before: Mapping[str, str], after: Mapping[str, str],
                instr: Instruction | None = None) -> C2LRStep:
        """A cycle that is not a C2LR template: an `init`, a plain move, a gate.  Ions that
        moved carry no record from where they were; a leg that now holds two without an enter
        is C1's to report, from the occupancies."""
        exc_before = self.excepted(before)
        moved = {i: before[i] for i in before if after.get(i) != before.get(i)}
        what = f"a plain {instr.cls or 'move'} step" if instr is not None else "re-placement"
        c1 = self._exit_problems({i: s for i, s in moved.items() if s in self.legs},
                                 before, None, what) if instr is not None else []
        c2 = []
        if instr is not None and instr.type == "simd" and not instr.template \
                and is_c2lr_class(self.arch, instr.cls):
            c2.append(f"a {instr.cls} step lists its own routes; a C2LR step's only per-leg "
                      f"choice is the direction bit, and its routes are the device's (C2)")
        for i in moved:
            self.near.pop(i, None)
            self.entered.pop(i, None)
        self._settle(after)
        check = self.fresh
        if instr is None:
            self.fresh = True
        else:
            self.fresh = False
        return C2LRStep(c1=tuple(c1), c2=tuple(c2), excepted_before=exc_before,
                        excepted_after=self.excepted(after), check_before=check,
                        stayers=tuple(sorted(after)), heated=frozenset())

    def init(self, before: Mapping[str, str], after: Mapping[str, str]) -> None:
        self.observe(before, after, None)

    def expand(self, instr: Instruction, pos: Mapping[str, str]) -> tuple[C2LRStep, dict[str, str]]:
        """Expand a C2LR template at positions `pos`: the step, and the positions after it.
        Raises `_Err` for a step that cannot be executed."""
        tpl = instr.template or {}
        bad = template_problems(tpl, instr.participants)
        if bad:
            raise _Err(f"instruction {instr.id}: {bad[0]}")
        kind = str(tpl["kind"])
        check, self.fresh = self.fresh, False
        exc_before = self.excepted(pos)
        everyone = frozenset(pos)
        if kind == "reorder_or_not":
            rev = [str(i) for i in tpl.get("reverse", ())]
            for ion in rev:
                self._in_leg(instr, ion, pos, "reorder_or_not")
            return C2LRStep(kind=kind, reversed=tuple(rev), stayers=tuple(sorted(pos)),
                            heated=everyone, excepted_before=exc_before,
                            excepted_after=exc_before, check_before=check), dict(pos)

        var = variation_of(tpl)
        try:
            pairs = self.geo.pairs(var)
        except NotC2LR as exc:
            raise _Err(f"instruction {instr.id}: {exc}") from None
        offer = declared(self.arch)
        c6: list[str] = []
        if var.form == "diagonal" and var.diagonal not in offer["diagonals"]:
            c6.append(f"the device does not offer diagonal {var.diagonal} (it offers "
                      f"{', '.join(offer['diagonals']) or 'none'}) (C6)")
        if var.form == "checkerboard" and var.pairing not in offer["checkerboard"]:
            c6.append(f"the device does not offer the checkerboard pairing {var.pairing} (it "
                      f"offers {', '.join(offer['checkerboard']) or 'none'}) (C6)")
        c1: list[str] = []
        contents = self._contents(pos)
        toward: dict[str, str] = {}           # leg -> the crystal shifted toward its junction
        for ion in [str(i) for i in tpl.get("toward", ())]:
            leg = self._in_leg(instr, ion, pos, "swap_or_stay")
            pair = pairs.get(leg)
            if pair is None:
                c6.append(f"{ion} is shifted toward a junction from leg {leg}, which has no "
                          f"partner under {var.label} (the device's edge); a crystal there can "
                          f"only stay (C6)")
                continue
            j = pair[0]
            if len(contents.get(leg, ())) > 1 and self.near.get(ion) != j:
                c1.append(f"{ion} cannot shift toward {j}: leg {leg} holds two crystals and the "
                          f"one nearer {j} is in the way (C1)")
                continue
            toward[leg] = ion
        # the exchange: at every pair, the crystal toward the junction in one leg crosses to
        # the other, leg -> junction -> partner leg, along the device's own two rails
        moves: list[Participant] = []
        where: dict[str, tuple[str, str]] = {}            # mover -> (junction, leg it enters)
        for leg in sorted(toward):
            ion = toward[leg]
            j, partner = pairs[leg]
            via = (self.geo.rail[(leg, j)], self.geo.rail[(partner, j)])
            moves.append(Participant(ion=ion, src=leg, dst=partner, via=via))
            where[ion] = (j, partner)
        after = dict(pos)
        for p in moves:
            after[p.ion] = p.dst
        c1 += self._exit_problems({p.ion: p.src for p in moves}, pos, var,
                                  f"swap-or-stay {var.label}")
        # who is near which end now, and who entered where
        new_contents = self._contents(after)
        for p in moves:
            self.near.pop(p.ion, None)
            self.entered.pop(p.ion, None)
        for p in moves:
            j, leg = where[p.ion]
            here = new_contents.get(leg, [])
            if len(here) > 2:
                c1.append(f"leg {leg} would hold {len(here)} crystals after {p.ion} enters it "
                          f"(C1: at most two, and two only between an enter and its exit)")
            elif len(here) == 2:
                other = next(i for i in here if i != p.ion)
                self.near[p.ion] = j
                if other not in where:
                    # an ENTER: the crystal joins one that stayed
                    self.entered[p.ion] = (var.key, j, leg, f"swap-or-stay {var.label}")
                    far = next(x for x in self.geo.ends[leg] if x != j)
                    self.near.setdefault(other, far)
        self._settle(after)
        movers = tuple(sorted(p.ion for p in moves))
        step = C2LRStep(kind=kind, variation=var, participants=tuple(moves), movers=movers,
                        stayers=tuple(sorted(set(pos) - set(movers))), heated=everyone,
                        c1=tuple(c1), c6=tuple(c6), excepted_before=exc_before,
                        excepted_after=self.excepted(after), check_before=check)
        return step, after

    def _in_leg(self, instr: Instruction, ion: str, pos: Mapping[str, str], what: str) -> str:
        site = pos.get(ion)
        if site is None:
            raise _Err(f"instruction {instr.id}: {what} names {ion}, which is not placed")
        if site not in self.legs:
            raise _Err(f"instruction {instr.id}: {what} names {ion}, which is at {site}, not "
                       f"in a leg; a C2LR step shifts crystals in legs")
        return site


def tracker_for(arch) -> C2LRTracker | None:
    """A tracker for a C2LR device, None for any other.  A device that declares C2LR control
    but is not a lattice gets None too: its template steps are refused when they are met, and
    C10 says why."""
    if not is_c2lr(arch):
        return None
    try:
        return C2LRTracker(arch)
    except NotC2LR:
        return None


def walk(prog: TSIR, arch) -> Iterator[tuple[Instruction, C2LRStep | None, dict[str, str]]]:
    """Replay positions alone: `(instruction, its C2LR step or None, positions after it)`.

    What the certificate check and the page need of a programme on a C2LR device -- where
    each template sends each crystal -- without a cost model.  A template is expanded by the
    same `C2LRTracker.expand` the replay uses, so the three cannot disagree.  Raises
    ValueError for a template that cannot be executed."""
    trk = tracker_for(arch)
    pos: dict[str, str] = {}
    dev = arch.device
    for ins in prog.instructions:
        if ins.type == "init":
            before = dict(pos)
            pos.update({str(k): str(v) for k, v in ins.placement.items()})
            if trk is not None:
                trk.init(before, pos)
            yield ins, None, dict(pos)
            continue
        if ins.type != "simd":
            yield ins, None, dict(pos)
            continue
        tpl = ins.template or {}
        if tpl.get("kind") in C2LR_TEMPLATES:
            if trk is None:
                raise ValueError(f"instruction {ins.id}: a {tpl.get('kind')} step needs a C2LR "
                                 f"device; {_why_not(arch)}")
            try:
                step, pos = trk.expand(ins, pos)
            except _Err as exc:
                raise ValueError(str(exc)) from None
            yield ins, step, dict(pos)
            continue
        before = dict(pos)
        if tpl.get("kind") == "loop_shift":
            loop, delta = str(tpl.get("loop")), int(tpl.get("delta", 0) or 0)
            if loop in dev.loops and delta:
                for _ in range(abs(delta)):
                    shift = dev.shift_map(loop, 1 if delta > 0 else -1)
                    pos = {i: shift.get(n, n) for i, n in pos.items()}
        for p in ins.participants:
            pos[p.ion] = p.dst
        step = trk.observe(before, pos, ins) if trk is not None else None
        yield ins, step, dict(pos)


def _why_not(arch) -> str:
    try:
        geometry(arch)
    except NotC2LR as exc:
        return str(exc)
    return "it is one"
