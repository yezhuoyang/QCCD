"""Cost models: what a primitive costs, in the two units the objective is made of.

PLAN §0.2 -- `cost` and `steps` are not rival objectives.  `steps` is a proxy for
runtime, hence for idling noise; `cost` is a proxy for routing-operation count, hence for
heating.  Once heating is tracked directly (M2) the proxy is only a cheap T1 surrogate,
but M1's oracle is stated in it, so both are carried.

Two models, differing in exactly the parameters PLAN §0.5 corrects:

===================  ==========================  ================================
                     DeckModel                   CorrectedModel
===================  ==========================  ================================
corner segment       `corner_hops = 3`           `corner_hops = 1` (R18: a bend is
                                                 ordinary transport)
degree >= 3 node     uniform, +1 hop / +1 step   `junction_cross` curve at that
                                                 degree: 100 us, 3.0 quanta
split / merge        not modelled                80 us, 6 quanta each, on the
                                                 movement classes that entail them
heating              not modelled                per-ion n-bar, by component
wall clock           not modelled                microseconds from the curves
===================  ==========================  ================================

`DeckModel` is not a strawman kept for contrast; it is the *oracle*.  M1 exists to prove
the replay engine reproduces a schedule someone else computed, and that is only a proof
if the model is theirs, not ours.

Junction charging convention
----------------------------
A junction is charged **on entry** to a degree->=3 node, once per move.  Charging both
entry and exit would double-count every transit; charging only exit would miss the last
one.  On entry, an ion completing one revolution of the shipped ring crosses each of the
24 docks exactly once, which is what makes "445 transits per data ion" come out of the
replay rather than out of a formula.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Mapping, Sequence

from ..arch import Architecture, CurvePoint, OperatingPointPolicy, Segment

__all__ = [
    "Charge",
    "CostModel",
    "DeckModel",
    "CorrectedModel",
    "QUANTA_COMPONENTS",
    "deck_model",
    "corrected_model",
]

#: The named halves of the heating budget.  Keeping them separate is what lets M2 report
#: "267 shuttling + 1336 junction + 144 dock/undock" instead of one opaque total.
QUANTA_COMPONENTS = ("shuttle", "junction", "split_merge", "gate", "anomalous")


def _chain_reference(spec: Mapping[str, object], key: str) -> int | None:
    """Read a `"<shape>:<N_ref>"` chain rule off a primitive, or `None` if undeclared.

    Undeclared means the primitive's numbers carry no statement about chain length, and
    every model must then behave exactly as it did before chain length was modelled at
    all.  Silence is not an invitation to assume a default.
    """
    rule = spec.get(key)
    if rule is None:
        return None
    shape, _, value = str(rule).partition(":")
    if shape not in ("murali", "linear"):
        raise ValueError(f"unsupported {key} rule {rule!r}")
    n_ref = int(value)
    if n_ref < 2:
        raise ValueError(f"{key} reference chain length must be >= 2, got {n_ref}")
    return n_ref


def _chain_factor(n_chain: int, n_ref: int) -> float:
    """Murali's `A ~ N / ln N`, as a **monotone** envelope from the reference length.

    Two deliberate departures from the bare formula, both because a search is going to
    be pointed at this and neither is a claim the source makes:

    **Clamped below at `n_ref`.**  `N / ln N` has a minimum at `N = e ~ 2.718`, so taken
    literally it says a 3-ion chain is **5.4 % better** than a 2-ion one.  That is an
    artifact of evaluating an asymptotic large-`N` scaling at `N = 3`, not a measured
    discount -- and handing an optimiser a 5 % free lunch at `N = 3` is precisely the
    failure mode G1 exists to prevent.  The envelope says instead that a chain longer
    than the reference is never *better* than the reference, which is the conservative
    reading and the one that cannot be exploited.

    **Clamped below at 2 ions.**  A two-qubit gate needs two ions in the trap, and R6b
    already requires the pair to be co-located, so `n_chain < 2` cannot reach here from a
    legal program.  The clamp is a guard against a caller, not a model of a one-ion gate.
    """
    n = max(int(n_chain), 2)
    raw = n / math.log(n)
    floor = n_ref / math.log(n_ref)
    return max(raw, floor)


@dataclass(frozen=True, slots=True)
class Charge:
    """What one ion's one primitive (or composite) costs.

    ``cost``   dimensionless routing cost (the deck's unit; Jones' routing-op count)
    ``depth``  dimensionless primitive steps -- a batch takes the max over participants
    ``us``     wall clock
    ``quanta`` motional quanta, split by component
    """

    cost: float = 0.0
    depth: int = 0
    us: float = 0.0
    quanta: Mapping[str, float] = field(default_factory=dict)

    @property
    def total_quanta(self) -> float:
        return sum(self.quanta.values())

    def then(self, other: "Charge") -> "Charge":
        """Sequential composition -- both durations elapse, both heatings accrue.

        R15 says quanta actually compose as
        `n_hom + n_inhom + 2 sqrt(n_hom n_inhom) cos(theta)`, whose interference term can
        be negative.  Additive composition is therefore an *upper bound*, which is what
        is implemented; the phase model needed for the exact form is not in the corpus
        for these primitives (`Knowledge: q_heating_rate_measurement`).
        """
        q = dict(self.quanta)
        for k, v in other.quanta.items():
            q[k] = q.get(k, 0.0) + v
        return Charge(
            cost=self.cost + other.cost,
            depth=self.depth + other.depth,
            us=self.us + other.us,
            quanta=q,
        )

    def overlapping(self, other: "Charge") -> "Charge":
        """Two primitives driven together: one duration covers both.

        Used where a segment traversal *is* the junction crossing rather than something
        that happens after it -- the ion does not shuttle for 5 us and then spend another
        100 us at the junction, it spends 100 us crossing.  Heating still adds.
        """
        q = dict(self.quanta)
        for k, v in other.quanta.items():
            q[k] = q.get(k, 0.0) + v
        return Charge(
            cost=self.cost + other.cost,
            depth=max(self.depth, other.depth),
            us=max(self.us, other.us),
            quanta=q,
        )

    def scaled(self, k: float) -> "Charge":
        return Charge(
            cost=self.cost * k,
            depth=int(round(self.depth * k)),
            us=self.us * k,
            quanta={a: b * k for a, b in self.quanta.items()},
        )


class CostModel:
    """Base class.  Subclasses answer "what does this primitive cost?"."""

    name: str = "abstract"
    models_time: bool = False
    models_heating: bool = False

    # -- transport ---------------------------------------------------------

    def move(
        self,
        arch: Architecture,
        seg: Segment,
        src: str,
        dst: str,
        *,
        entails: Sequence[str] = (),
    ) -> Charge:
        raise NotImplementedError

    # -- everything else ---------------------------------------------------

    def gate(
        self, arch: Architecture, gate: str, n_pairs: int, n_chain: int | None = None
    ) -> Charge:
        return Charge()

    def gate_1q(self, arch: Architecture, gate: str, n: int) -> Charge:
        """A single-qubit gate.  Priced from the `1q_gate` primitive, not `ms_gate`.

        Every architecture declares `1q_gate` and, before the compiler needed to emit
        one, nothing read it -- so a single-qubit rotation would have been charged as a
        two-qubit entangling gate, roughly two orders of magnitude too slow.
        """
        return Charge()

    def cool(self, arch: Architecture) -> Charge:
        return Charge()

    def measure(self, arch: Architecture) -> Charge:
        return Charge()

    def reset(self, arch: Architecture) -> Charge:
        return Charge()

    def anomalous_per_us(self, arch: Architecture) -> float:
        """R17: quanta accrued per microsecond of elapsed time, moving or not."""
        return 0.0

    def gate_error(
        self, arch: Architecture, nbar: float, n_chain: int | None = None
    ) -> float:
        """R16: two-qubit gate error as a function of accumulated quanta.

        ``n_chain`` is how many ions share the trap the gate happens in -- the same
        quantity R13 caps at 15, read from the replayed occupancy.  ``None`` means "not
        supplied", and every model must then answer exactly as it did before the argument
        existed, so that a caller which does not know the chain length is never silently
        given a different number.
        """
        return 0.0

    def describe(self) -> dict:
        return {"name": self.name, "models_time": self.models_time,
                "models_heating": self.models_heating}


# --------------------------------------------------------------------------- deck


@dataclass(frozen=True)
class DeckModel(CostModel):
    """The shipped deck's model, exactly as the 24-ancilla artifact computes it.

    ``rotation_cost_rule``  one rigid one-slot rotation moves every occupied data ion;
                            corner edges cost 3 primitive hops, straight edges cost 1
    ``rotation_step_rule``  one rigid hop takes the maximum primitive edge depth used by
                            any moving ion in that hop
    ``dock_rule``           parallel dock + undock costs 2 one-ion hops per contacted
                            member and 2 batched steps per contact batch

    -- `INLINE_DATA.model`, `visualizer_24_ancillas_24_junctions_standalone.html:344`

    A degree->=3 node is charged nothing extra: the deck's dock and undock are one
    uniform one-ion hop each, which is what "+1 step at a junction" means here.  PLAN
    §0.5 is the case that this is wrong; `CorrectedModel` is that correction.
    """

    corner_hops: int = 3
    name: str = "deck"
    models_time: bool = False
    models_heating: bool = False

    def move(
        self,
        arch: Architecture,
        seg: Segment,
        src: str,
        dst: str,
        *,
        entails: Sequence[str] = (),
    ) -> Charge:
        # A corner segment is one whose two endpoints are both corners of its loop --
        # i.e. it contains a whole turn.  In the shipped 2x72 ring exactly the two
        # end-caps qualify, giving 142 x 1 + 2 x 3 = 148 per rigid hop and depth 3.
        hops = (
            self.corner_hops
            if arch.device.corner_endpoints.get(seg.id, 0) == 2
            else 1
        )
        return Charge(cost=hops, depth=hops)

    def describe(self) -> dict:
        d = super().describe()
        d["corner_hops"] = self.corner_hops
        return d


# ----------------------------------------------------------------------- corrected


@dataclass(frozen=True)
class CorrectedModel(CostModel):
    """PLAN §0.3-§0.5: a bend is one shuttle, a degree->=3 node is a junction.

    ``corner_hops = 1``      a two-arm bend has a continuous RF null and therefore no
                             barrier (quant-ph/0702175; H2 is one continuous RF null,
                             2305.03828).  Rule R18.
    junction by degree       the `junction_cross` curve is indexed by the degree the
                             expanded graph reports, so the 24 spurs of the shipped ring
                             pay for themselves 445 times per data ion per round.
    split / merge            charged on the movement classes whose `entails` says so --
                             a dock lifts an ion out of one potential and inserts it into
                             another; a conveyor-belt rotation does neither.
    """

    policy: OperatingPointPolicy = OperatingPointPolicy("qccdsim_jones", "fastest")
    corner_hops: int = 1
    # Opt-in, and off by default so the shipped oracles are untouched. `Segment.length`
    # is in trap-pitch units and the `shuttle_segment` curve point is calibrated for one
    # pitch, so a segment of length L is L pitches of transport. The corpus says
    # excitation is time-dominated rather than distance-dominated (2605.25118: the
    # near-adiabatic regime is reached within 20 us "regardless of the transport
    # distance"), which is consistent with scaling both us and quanta by the same hop
    # count -- a longer segment costs more because it takes longer, not because distance
    # heats per se.
    length_scaling: bool = False
    pitch: float = 1.0
    junction_min_degree: int = 3
    include_anomalous: bool = True
    name: str = "corrected"
    models_time: bool = True
    models_heating: bool = True

    # -- curve lookups ------------------------------------------------------

    def _point(self, arch: Architecture, primitive: str) -> CurvePoint:
        return arch.primitives.curve(primitive).pick(self.policy)

    def junction_point(self, arch: Architecture, degree: int) -> CurvePoint | None:
        if degree < self.junction_min_degree:
            return None  # R18: a bend is ordinary transport, not a junction
        curve = arch.primitives.degree_curve("junction_cross").get(degree)
        if curve is None:
            raise KeyError(
                f"architecture {arch.name!r} has a degree-{degree} node on a transport "
                f"path but no junction_cross curve for degree {degree}"
            )
        return curve.pick(self.policy)

    # -- primitives ---------------------------------------------------------

    def move(
        self,
        arch: Architecture,
        seg: Segment,
        src: str,
        dst: str,
        *,
        entails: Sequence[str] = (),
    ) -> Charge:
        shuttle = self._point(arch, "shuttle_segment")
        if arch.device.corner_endpoints.get(seg.id, 0) == 2:
            hops = self.corner_hops          # charged by the turn, not by its length
        elif self.length_scaling and self.pitch > 0:
            hops = max(1, round(seg.length / self.pitch))
        else:
            hops = 1
        charge = Charge(
            cost=float(hops),
            depth=1,
            us=shuttle.us * hops,
            quanta={"shuttle": shuttle.quanta * hops},
        )
        jp = self.junction_point(arch, arch.device.degree(dst))
        if jp is not None:
            charge = charge.overlapping(
                Charge(cost=1.0, depth=1, us=jp.us, quanta={"junction": jp.quanta})
            )
        # `entails` is charged once per MOVE, not once per segment: the replay passes
        # it only on the first segment of a multi-segment route, because one dock is one
        # split plus one merge however long the spur is
        for which in entails:
            if which not in ("split", "merge"):
                raise ValueError(f"movement class entails unknown primitive {which!r}")
            p = self._point(arch, which)
            charge = charge.then(
                Charge(cost=1.0, depth=1, us=p.us, quanta={"split_merge": p.quanta})
            )
        return charge

    def gate(
        self, arch: Architecture, gate: str, n_pairs: int, n_chain: int | None = None
    ) -> Charge:
        key = {"MS": "ms_gate", "CX": "ms_gate", "SWAP": "gate_swap"}.get(gate, "ms_gate")
        spec = arch.primitives.scalar(key)
        if key == "gate_swap":
            ms = arch.primitives.scalar("ms_gate")
            return Charge(cost=0.0, depth=1,
                          us=float(spec["gates"]) * self._gate_us(ms, n_chain))
        return Charge(cost=0.0, depth=1, us=self._gate_us(spec, n_chain))

    @staticmethod
    def _gate_us(spec: Mapping[str, object], n_chain: int | None) -> float:
        """Gate duration, optionally scaled by chain length.

        The other half of G1.  Murali *et al.* report that gate duration scales with the
        gate type -- **FM ∝ chain size N**, AM ∝ ion separation, PM weakly -- so a longer
        chain can cost time as well as fidelity.  Declaring `us_vs_chain: "linear:N_ref"`
        turns that on, normalised so the declared `us` is the duration **at `N_ref`** and
        nothing changes at the reference length.

        **No shipped architecture declares it, deliberately.** `ms_gate` cites
        2305.03828 for its duration and does not say which of AM/FM/PM the gate is, and
        Murali's linear scaling is FM-specific. Asserting it for an unstated gate type
        would be inventing a number rather than refining one. The mechanism is here so a
        capacity sweep can turn it on for an architecture that *does* say -- and when it
        is on, the extra elapsed time reaches the error through R17's anomalous accrual
        by itself, so there is no second `Γ` to calibrate and nothing double-counted.
        """
        us = float(spec["us"])
        n_ref = _chain_reference(spec, "us_vs_chain")
        if n_ref is None or n_chain is None:
            return us
        return us * max(int(n_chain), 2) / n_ref

    def gate_1q(self, arch: Architecture, gate: str, n: int) -> Charge:
        # A virtual-Z is a frame update: the controller advances the phase of every later
        # pulse on that ion and no laser fires, so it takes no time.  It is still a real
        # operation on the state, which is why it appears in the program at all.
        if gate == "VZ":
            return Charge(cost=0.0, depth=1, us=0.0)
        spec = arch.primitives.scalar("1q_gate")
        return Charge(cost=0.0, depth=1, us=float(spec["us"]))

    def cool(self, arch: Architecture) -> Charge:
        spec = arch.primitives.scalar("cool")
        return Charge(cost=0.0, depth=1, us=float(spec["us"]))

    def measure(self, arch: Architecture) -> Charge:
        return Charge(cost=0.0, depth=1, us=float(arch.primitives.scalar("measure")["us"]))

    def reset(self, arch: Architecture) -> Charge:
        return Charge(cost=0.0, depth=1, us=float(arch.primitives.scalar("reset")["us"]))

    def anomalous_per_us(self, arch: Architecture) -> float:
        if not self.include_anomalous:
            return 0.0
        return arch.anomalous_rate() / 1000.0

    def gate_error(
        self, arch: Architecture, nbar: float, n_chain: int | None = None
    ) -> float:
        """R16.  `error_vs_quanta: "linear:2.0e-3"` means eps = eps0 + 2.0e-3 * n-bar.

        With `error_vs_chain` also declared, the chain-length term of Murali *et al.*
        (ISCA 2020) is added -- see `_chain_factor` and G1 in `Codesign/EVALUATION.md`.
        """
        spec = arch.primitives.scalar("ms_gate")
        eps0 = 1.0 - float(spec.get("fidelity_at_n0", 1.0))
        rule = str(spec.get("error_vs_quanta", "linear:0"))
        kind, _, value = rule.partition(":")
        if kind != "linear":
            raise ValueError(f"unsupported error_vs_quanta rule {rule!r}")
        slope = float(value)
        nbar = max(nbar, 0.0)

        n_ref = _chain_reference(spec, "error_vs_chain")
        if n_ref is None or n_chain is None:
            return eps0 + slope * nbar

        # The calibration must be checkable BEFORE the short-circuit below, or an
        # architecture with an impossible slope would quietly return legacy numbers at
        # the reference length and only blow up once a sweep reached a longer chain.
        half = slope / 2.0
        if eps0 - half < 0.0:
            raise ValueError(
                f"ms_gate declares error_vs_quanta slope {slope:g} against "
                f"fidelity_at_n0 infidelity {eps0:g}: the chain-length calibration needs "
                f"slope <= 2*eps0, or the zero-quanta floor goes negative"
            )

        # A chain no longer than the reference IS the reference case, so return the
        # reference expression itself rather than an algebraically-equal regrouping of
        # it.  The two agree to about 1 ULP, which is close enough for physics and not
        # close enough for the claim being made: that this change leaves every
        # already-validated number on a capacity-2 device EXACTLY as it was.  A
        # 1-ULP-per-gate drift over 864 gates is a different claim, and a weaker one.
        ratio = _chain_factor(n_chain, n_ref) / _chain_factor(n_ref, n_ref)
        if ratio == 1.0:
            return eps0 + slope * nbar

        # G1.  Murali et al. give the laser-instability coefficient as A ~ N / ln N and
        # the error as A (2 n-bar + 1).  Writing that as
        #
        #     eps(n-bar, N)  =  eps0' + kappa f(N) (2 n-bar + 1)
        #
        # and requiring it to equal the legacy eps0 + slope*n-bar AT THE REFERENCE CHAIN
        # LENGTH, for every n-bar, pins both constants with nothing left to choose:
        #
        #     kappa  =  slope / (2 f(N_ref))          matching the n-bar coefficient
        #     eps0'  =  eps0 - kappa f(N_ref)         matching the constant
        #            =  eps0 - slope / 2
        #
        # That is what makes this a REFINEMENT rather than a replacement (D1): at
        # N = N_ref the two models are the same function of n-bar, to the last bit, so
        # every already-validated number on a capacity-2 device is unchanged.  N_ref is
        # 2 because `fidelity_at_n0` and `error_vs_quanta` are both quoted from a
        # two-ion gate zone (2305.03828); an architecture whose gate numbers came from a
        # longer chain must say so in its own `error_vs_chain`.
        # Written in terms of the RATIO f(N)/f(N_ref), which is all the shape contributes
        # once kappa is pinned: kappa*f(N) = (slope/2) * ratio.  So neither kappa nor
        # f itself appears, and the expression below is manifestly `eps0 + slope*nbar`
        # at ratio = 1.
        return (eps0 - half) + half * ratio * (2.0 * nbar + 1.0)

    def max_gate_quanta(self, arch: Architecture) -> float:
        """R7's budget."""
        return float(arch.primitives.scalar("ms_gate").get("max_quanta", float("inf")))

    def with_table(self, table: str) -> "CorrectedModel":
        return replace(self, policy=replace(self.policy, table=table))

    def describe(self) -> dict:
        d = super().describe()
        d.update(
            {
                "corner_hops": self.corner_hops,
                "junction_min_degree": self.junction_min_degree,
                "table": self.policy.table,
                "objective": self.policy.objective,
                "include_anomalous": self.include_anomalous,
                "length_scaling": self.length_scaling,
                "pitch": self.pitch,
            }
        )
        return d


def deck_model(**kw) -> DeckModel:
    return DeckModel(**kw)


def corrected_model(table: str = "qccdsim_jones", **kw) -> CorrectedModel:
    return CorrectedModel(policy=OperatingPointPolicy(table, "fastest"), **kw)
