"""C2LR swap-or-stay devices: which legs a step pairs, read off the device.  docs/c2lr.md.

A C2LR device (R. D. Delaney et al., arXiv:2403.00756) is a lattice of junctions whose every
wire carries one **leg**: a trap site at the wire's centre holding one two-ion crystal, which
this model counts as one ion.  The `grid(a, b)` generator draws exactly that.  Transport is
not a route a programme chooses.  One shared waveform plays at every junction at once, and
each crystal's only choice is a direction bit -- shift toward its pair's junction, or away --
so which legs exchange is a fact of the GEOMETRY and the step's variation:

* a **diagonal** (demonstrated, Sec. IV; Fig. 4(f)): every junction pairs the same two of its
  legs, W-S, W-N, E-S or E-N.  Under W-S a horizontal leg pairs at its east junction (where
  it is that junction's W leg) and a vertical leg at its north junction (where it is the S
  leg), so every leg is in at most one pair;
* a **checkerboard** (the proposal's, not in the paper, ASSUMED): at the junctions of one
  colour `(r + c) mod 2`, two pairs at once -- P1 is W-S and N-E, P2 is W-N and S-E.

This module computes those matchings and nothing else: the legs, each junction's arms by
compass direction, the junction colouring, and the pairs of a variation.  What a step DOES
with them -- the exchange rule, the two-crystal exception, the price -- is
`qccd.verify.c2lr`'s.  The replay asks this module, never the programme, which is what lets
the rules exempt a swap without a programme being able to claim the exemption for anything
else.

Compass, as the studio draws it: W is the smaller x, E the larger; **N is the smaller y**,
because the page's y axis grows down the screen (`qccd/viz/render.py`, `py = oy + y*sy`).
A diagonal is symmetric under the flip, so the choice changes which name a step carries,
never which steps exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

__all__ = [
    "DIAGONALS", "PAIRINGS", "FORMS", "Variation", "NotC2LR", "C2LRGeometry",
    "is_c2lr", "geometry", "declared", "variation_of", "C2LR_PHYSICS", "c2lr_control",
    "c2lr_primitives", "c2lr_species",
]

#: The four single diagonals the paper demonstrates, as (horizontal arm, vertical arm).
DIAGONALS = ("WS", "WN", "ES", "EN")
#: The proposal's two-pair steps at one checkerboard colour.  Not in the paper.
PAIRINGS: Mapping[str, tuple[tuple[str, str], tuple[str, str]]] = {
    "P1": (("W", "S"), ("N", "E")),
    "P2": (("W", "N"), ("S", "E")),
}
#: How a step pairs legs: `diagonal` (demonstrated) or `checkerboard` (assumed).
FORMS = ("diagonal", "checkerboard")

#: Positions are declared numbers; two coordinates closer than this are the same line.
_EPS = 1e-6


class NotC2LR(ValueError):
    """The device is not a C2LR lattice, so a swap-or-stay step has no matching on it."""


@dataclass(frozen=True)
class Variation:
    """One step type: the matching a swap-or-stay step plays.

    `key` is what C1's "the same step type" compares: a crystal that entered a leg by one
    variation may leave it only by the same one."""

    form: str                          # "diagonal" | "checkerboard"
    diagonal: str | None = None        # "WS" | "WN" | "ES" | "EN"
    parity: int | None = None          # checkerboard colour, 0 | 1
    pairing: str | None = None         # "P1" | "P2"

    @property
    def key(self) -> tuple:
        return (self.form, self.diagonal, self.parity, self.pairing)

    @property
    def assumed(self) -> bool:
        """Not demonstrated in arXiv:2403.00756: the two-pair checkerboard steps."""
        return self.form == "checkerboard"

    @property
    def label(self) -> str:
        if self.form == "diagonal":
            return str(self.diagonal)
        return f"checkerboard {self.pairing} at parity {self.parity}"

    def arms(self) -> tuple[tuple[str, str], ...]:
        """The pairs of compass arms a junction in play joins."""
        if self.form == "diagonal":
            d = str(self.diagonal)
            return ((d[0], d[1]),)
        return PAIRINGS[str(self.pairing)]


def variation_of(template: Mapping) -> Variation:
    """The variation a `swap_or_stay` template names.  Shape is `qccd.ir.tsir`'s to check
    (`template_problems`); this reads a template that has passed it."""
    if template.get("diagonal") is not None:
        return Variation("diagonal", diagonal=str(template["diagonal"]))
    cb = template.get("checkerboard") or {}
    return Variation("checkerboard", parity=int(cb["parity"]), pairing=str(cb["pairing"]))


def _dir(dx: float, dy: float) -> str | None:
    """The compass arm a vector points along, or None off the two axes."""
    if abs(dy) <= _EPS and abs(dx) > _EPS:
        return "E" if dx > 0 else "W"
    if abs(dx) <= _EPS and abs(dy) > _EPS:
        return "S" if dy > 0 else "N"         # y grows down the page: N is the smaller y
    return None


_OPPOSITE = {"W": "E", "E": "W", "N": "S", "S": "N"}


@dataclass
class C2LRGeometry:
    """The legs of a C2LR lattice and how each variation pairs them.

    ``legs``     every leg (a site on a wire between two junctions), in id order
    ``arm``      junction -> {compass arm: the leg on it}
    ``ends``     leg -> {junction: the arm the leg is, seen from that junction}
    ``rail``     (leg, junction) -> the segment joining them
    ``colour``   junction -> (r + c) mod 2, or None when the lattice has no checkerboard
    """

    legs: tuple[str, ...]
    junctions: tuple[str, ...]
    arm: dict[str, dict[str, str]]
    ends: dict[str, dict[str, str]]
    rail: dict[tuple[str, str], str]
    colour: dict[str, int] | None
    _pairs: dict = field(default_factory=dict, repr=False)

    def pairs(self, var: Variation) -> dict[str, tuple[str, str]]:
        """leg -> (the junction it is paired at, its partner leg) under `var`.  A leg absent
        here is unpaired in that step (the device's edge).  A matching by construction:
        under one diagonal a leg is the named arm of exactly one of its two junctions, and
        under a checkerboard it touches exactly one junction of the step's colour."""
        hit = self._pairs.get(var.key)
        if hit is not None:
            return hit
        if var.form == "checkerboard" and self.colour is None:
            raise NotC2LR("this lattice has no checkerboard colouring: some leg joins two "
                          "junctions of the same colour")
        out: dict[str, tuple[str, str]] = {}
        for j in self.junctions:
            if var.form == "checkerboard" and self.colour[j] != var.parity:   # type: ignore[index]
                continue
            arms = self.arm.get(j, {})
            for a, b in var.arms():
                la, lb = arms.get(a), arms.get(b)
                if la is None or lb is None:
                    continue
                out[la] = (j, lb)
                out[lb] = (j, la)
        self._pairs[var.key] = out
        return out

    def junction_of(self, leg: str, var: Variation) -> str | None:
        """The junction `leg` shifts toward or away from under `var`: the end at which it is
        one of the variation's arms (of the step's colour, for a checkerboard), whether or
        not its partner exists.  Unique on a lattice: a horizontal leg is the W arm only at
        its east end, a vertical one the S arm only at its north end, and a leg touches one
        junction of each colour.  What the page draws a stayer's shift against."""
        if var.form == "checkerboard" and self.colour is None:
            return None
        names = {a for pair in var.arms() for a in pair}
        for j, arm in self.ends.get(leg, {}).items():
            if var.form == "checkerboard" and self.colour[j] != var.parity:  # type: ignore[index]
                continue
            if arm in names:
                return j
        return None


def _build(dev) -> C2LRGeometry:
    """Read the lattice off the device, or say why it is not one."""
    legs = sorted(n for n, node in dev.nodes.items() if node.kind == "site")
    if not legs:
        raise NotC2LR("the device has no legs: no site to hold a crystal")
    junctions = sorted(n for n, node in dev.nodes.items() if node.kind != "site")
    pos = {n: (float(node.pos[0]), float(node.pos[1]) if len(node.pos) > 1 else 0.0)
           for n, node in dev.nodes.items()}
    arm: dict[str, dict[str, str]] = {j: {} for j in junctions}
    ends: dict[str, dict[str, str]] = {}
    rail: dict[tuple[str, str], str] = {}
    for leg in legs:
        segs = dev.incidence[leg]
        if len(segs) != 2:
            raise NotC2LR(f"leg {leg} has {len(segs)} rails; a leg is a wire's centre, joined "
                          f"to the two junctions it lies between")
        here: dict[str, str] = {}
        for sid in segs:
            j = dev.segments[sid].other(leg)
            if dev.nodes[j].kind == "site":
                raise NotC2LR(f"leg {leg} is joined to {j}, another site; a leg's rails end at "
                              f"junctions")
            d = _dir(pos[leg][0] - pos[j][0], pos[leg][1] - pos[j][1])
            if d is None:
                raise NotC2LR(f"leg {leg} is not on an axis through junction {j}")
            if d in arm[j]:
                raise NotC2LR(f"junction {j} has two {d} legs ({arm[j][d]} and {leg})")
            arm[j][d] = leg
            here[j] = d
            rail[(leg, j)] = sid
        (j1, d1), (j2, d2) = here.items()
        if _OPPOSITE[d1] != d2:
            raise NotC2LR(f"leg {leg} is not between its junctions {j1} and {j2}: it is their "
                          f"{d1} and {d2} arm (a wrap-around wire is not a leg)")
        ends[leg] = here
    for j in junctions:
        for sid in dev.incidence[j]:
            if dev.nodes[dev.segments[sid].other(j)].kind != "site":
                raise NotC2LR(f"junction {j} is joined straight to another junction; every "
                              f"wire of a C2LR lattice carries a leg")
    # the checkerboard: (r + c) mod 2 by the junctions' ranks along each axis, and only if
    # every leg then joins two colours, which is what makes one colour's pairs a matching
    xs = sorted({round(pos[j][0], 6) for j in junctions})
    ys = sorted({round(pos[j][1], 6) for j in junctions})
    rx, ry = {x: i for i, x in enumerate(xs)}, {y: i for i, y in enumerate(ys)}
    colour: dict[str, int] | None = {
        j: (rx[round(pos[j][0], 6)] + ry[round(pos[j][1], 6)]) % 2 for j in junctions}
    for leg, js in ends.items():
        a, b = list(js)
        if colour[a] == colour[b]:           # type: ignore[index]
            colour = None
            break
    return C2LRGeometry(legs=tuple(legs), junctions=tuple(junctions), arm=arm, ends=ends,
                        rail=rail, colour=colour)


#: device identity -> (device, geometry or the reason it has none).  Keyed by `id` and
#: checked by identity, as R23 caches its facts: an Architecture is a frozen dataclass of
#: dicts and cannot be hashed.
_CACHE: dict[int, tuple] = {}


def is_c2lr(arch) -> bool:
    """Does the device declare C2LR control?  (Whether its geometry is one is `geometry`.)"""
    return str((arch.control or {}).get("model", "")) == "c2lr"


def geometry(arch) -> C2LRGeometry:
    """The device's C2LR lattice.  Raises `NotC2LR`, saying why, for a device that does not
    declare C2LR control or whose drawing is not a lattice of legs."""
    if not is_c2lr(arch):
        raise NotC2LR(f"device {arch.name!r} does not declare C2LR control "
                      f"(control.model is {str((arch.control or {}).get('model', 'simd_classes'))!r}, "
                      f"not 'c2lr')")
    dev = arch.device
    hit = _CACHE.get(id(dev))
    if hit is None or hit[0] is not dev:
        try:
            got: object = _build(dev)
        except NotC2LR as exc:
            got = exc
        if len(_CACHE) > 64:
            _CACHE.clear()
        hit = _CACHE[id(dev)] = (dev, got)
    if isinstance(hit[1], NotC2LR):
        raise NotC2LR(f"device {arch.name!r}: {hit[1]}")
    return hit[1]


def declared(arch) -> dict:
    """What the device's `control.c2lr` block offers: the diagonals, the checkerboard
    pairings, and which forms it marks assumed."""
    blk = dict((arch.control or {}).get("c2lr") or {})
    return {
        "diagonals": tuple(str(d) for d in blk.get("diagonals", DIAGONALS)),
        "checkerboard": tuple(str(p) for p in blk.get("checkerboard", ())),
        "assumed": tuple(str(a) for a in blk.get("assumed", ("checkerboard",))),
        "shared_signals": blk.get("shared_signals"),
        "inputs_per_leg": blk.get("inputs_per_leg"),
        "switches_per_leg": blk.get("switches_per_leg"),
    }


# ------------------------------------------------------------------- the device's numbers

_PAPER = "arXiv:2403.00756 v2"

#: Every number checked against arXiv:2403.00756 v2 (docs/c2lr.md section 6), by species pair.
#: A number the paper does not give is absent (Ba-Sr's reorder-or-not), never guessed.
C2LR_PHYSICS: Mapping[str, Mapping] = {
    "Ba-Sr": {
        "species": {"pair": "Ba-Sr", "qubit": "Ba+", "coolant": "Sr+", "sympathetic": True,
                    "source": _PAPER},
        "swap_or_stay": {
            "us": 312.5, "quanta_swap": 0.14, "quanta_stay": 0.035, "com_mhz": 1.85,
            "source": f"{_PAPER}: us = 1/Gamma_swap, Gamma = 3.2 kHz at 3.0 m/s (Sec. V, p6); "
                      "quanta derived (docs/c2lr.md section 6: Table II less the cooling "
                      "residue, halved; Table III rates x 1/Gamma, the larger); COM held at "
                      "1.85 MHz (p4, C8)"},
    },
    "Yb-Ba": {
        "species": {"pair": "Yb-Ba", "qubit": "Yb+", "coolant": "Ba+", "sympathetic": True,
                    "source": _PAPER},
        "swap_or_stay": {
            "us": 400.0, "quanta_swap": 0.18, "quanta_stay": 0.165, "com_mhz": 1.4,
            "source": f"{_PAPER}: us = 1/Gamma_swap, Gamma = 2.5 kHz at 3.3 m/s (Sec. V, p6); "
                      "quanta derived (docs/c2lr.md section 6: Table II less the cooling "
                      "residue, halved; Table III rates x 1/Gamma, the larger); COM held at "
                      "1.4 MHz (p4, C8)"},
        "reorder_or_not": {
            "us": 93.0, "quanta": 0.0195,
            "source": f"{_PAPER}: Fig. 5(a) and text, p7 (Yb-Ba only)"},
    },
}

#: Merge and split around a gate in one leg: not in the paper.  The platform's own slow
#: point (arXiv:2605.25118's 40 us at n~0.1), the only point the C2LR device carries, so
#: every operating-point policy reads the same one.
_MERGE_SPLIT = [{"us": 40.0, "quanta": 0.1, "table": "transport_excitation",
                 "source": "2605.25118",
                 "label": "slow point; the platform's curve, not arXiv:2403.00756"}]


#: The same census as a wiring block, which is what the DAC count (`hardware_report`, the
#: `dacs` metric, a board's budget B1) reads: the 27 shared analog signals are the broadcast
#: bank, and the two site-dependent shims of each leg its own compensation DACs, undivided.
#: Without it a C2LR device is counted as wired directly, one DAC per electrode -- 528 on a
#: 3 x 3 lattice, which no board's budget admits and which is not what the paper built.
C2LR_WIRING: Mapping = {
    "scheme": "broadcast_groups", "dacs_dynamic": 27, "compensation_electrodes_per_trap": 2,
    "shim_per_dac": 1,
    "note": f"{_PAPER}, p4: 27 shared analog signals drive every leg (the broadcast bank); two "
            "site-dependent shims per leg, each its own DAC; the per-leg digital input and "
            "switch are not DACs",
}


def c2lr_control(*, checkerboard: bool = True, wiring: bool = False) -> dict:
    """The control block of a C2LR device (docs/c2lr.md section 1).

    The census C10 reads: 27 shared analog signals, plus one digital input and one switch
    per leg (and two site-dependent shims, p4).  The checkerboard pairings are offered and
    marked assumed; `checkerboard=False` offers the demonstrated diagonals only.  `wiring`
    adds the census as the wiring block DACs are counted from (`C2LR_WIRING`)."""
    out = {
        "model": "c2lr",
        "c2lr": {
            "diagonals": list(DIAGONALS),
            "checkerboard": ["P1", "P2"] if checkerboard else [],
            "assumed": ["checkerboard"],
            "shared_signals": 27, "inputs_per_leg": 1, "switches_per_leg": 1,
            "shims_per_leg": 2,
            "source": f"{_PAPER}, p4 (27 shared analog signals; N digital inputs and N "
                      "switches; two site-dependent shims per leg)",
        },
        "classes": {"extra": [
            {"id": "swap_or_stay", "type": "c2lr",
             "note": "one swap-or-stay step: the template's direction bits, the device's "
                     "matching"},
            {"id": "reorder_or_not", "type": "c2lr",
             "note": "a 180-degree rotation of the listed crystals' wells; nothing moves"},
        ]},
        "max_simd_classes_per_cycle": 1,
        "intra_inter_exclusive": True,
    }
    if wiring:
        out["wiring"] = dict(C2LR_WIRING)
    return out


def c2lr_primitives(species: str) -> dict:
    """The primitives a C2LR device adds to (or replaces in) its template's: the step(s),
    and the merge/split a gate in one leg entails."""
    try:
        phys = C2LR_PHYSICS[species]
    except KeyError:
        raise ValueError(f"no C2LR numbers for species {species!r}; the paper gives "
                         f"{', '.join(C2LR_PHYSICS)}") from None
    out = {k: dict(v) for k, v in phys.items() if k != "species"}
    out["merge"] = {"curve": [dict(p) for p in _MERGE_SPLIT]}
    out["split"] = {"curve": [dict(p) for p in _MERGE_SPLIT]}
    return out


def c2lr_species(species: str) -> dict:
    """The species keys a C2LR device adds to its template's `species` block: the pair, its
    qubit and coolant ions.  The paper is cited as `pair_source`, so the block's own `source`
    keeps citing the numbers that are the template's (T_coh_s, coolant_fraction), not this
    paper's."""
    out = dict(C2LR_PHYSICS[species]["species"])
    out["pair_source"] = out.pop("source")
    return out
