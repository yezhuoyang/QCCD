"""Named, versioned noise models: how a device's own physics becomes stim noise.

A board names its noise model by id, and an id means one fixed set of rules forever:
changing the physics is a NEW model (`qccd-noise@2`), never an edit to `@1`, because a
logical error rate is only comparable with another one computed under the same rules.

Every number a model uses comes from the architecture document the program was compiled
for -- the same `primitives`, `heating` and `species` blocks the replay and the rules
read -- plus the replay's own per-cycle view of each gate (the n̄ of its ions, how many ions
share its trap, how long each cycle took).  Nothing is fitted and nothing is assumed per
device.  `CHANNELS` states each rule once, in words; it is rendered verbatim for agents
and on the website, so it is the documentation, not a summary of it.

`qccd-noise@1` is deliberately simple.  What it leaves out is listed in
docs/PLAN-boards.md ("a more realistic noise model"): idle time per ion from the
schedule instead of the serial replay, transport-induced dephasing, crosstalk, ion loss,
imperfect cooling and a calibrated T2.  The shipped devices declare `T_coh_s = 600`, which
makes idling negligible; heating, through the MS error, dominates.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Mapping

__all__ = ["NoiseModel", "NoiseModel2", "CHANNELS", "CHANNEL_NAMES", "CHANNEL_NAMES_2", "MODELS", "get",
           "describe", "MsError",
           "CAPS"]

#: The error-budget channels, in the order a report lists them.
CHANNEL_NAMES = ("ms_base", "ms_heating", "ms_chain", "gate_1q", "measure", "reset", "idle")

#: The largest probability each stim channel is given.  DEPOLARIZE2 at 15/16 and
#: DEPOLARIZE1 at 3/4 are the fully mixing channels; a flip channel above 1/2 is no longer
#: an error but a gate, so it stops at 1/2.  Only a device far outside any physical regime
#: (n̄ in the hundreds) reaches a cap.
CAPS = {"DEPOLARIZE2": 15 / 16, "DEPOLARIZE1": 0.75, "X_ERROR": 0.5, "Z_ERROR": 0.5}

#: Where each channel is applied, what it is, and which field of the architecture document
#: it reads.  `s` is the model's `scale` (1 for the official model).
CHANNELS: tuple[dict[str, str], ...] = (
    {
        "channel": "ms_base",
        "where": "after every two-qubit gate (each MS pair; each abstract CX, CZ or SWAP, "
                 "which stands for one MS)",
        "stim": "DEPOLARIZE2(p_MS) on the pair; p_MS = s · ε(n̄, N) is the sum of this "
                "channel and the next two",
        "formula": "ε₀ = 1 − F₀: the gate's error with both ions in the motional ground "
                   "state, in a two-ion trap",
        "source": "primitives.ms_gate.fidelity_at_n0",
    },
    {
        "channel": "ms_heating",
        "where": "the same two-qubit gates",
        "stim": "part of the pair's DEPOLARIZE2",
        "formula": "k · n̄, where n̄ is the motional occupation of the hotter of the two ions "
                   "when the gate starts, as the replay computes it from every shuttle, "
                   "junction crossing, split, merge and elapsed microsecond since that ion "
                   "was last cooled (the n̄ rule R7 caps and R16 prices)",
        "source": "primitives.ms_gate.error_vs_quanta (\"linear:k\"); n̄ from "
                  "primitives.shuttle_segment / junction_cross / split / merge and "
                  "heating.anomalous_rate_quanta_per_ms, at the model's operating-point table",
    },
    {
        "channel": "ms_chain",
        "where": "the same two-qubit gates",
        "stim": "part of the pair's DEPOLARIZE2",
        "formula": "ε(n̄, N) − ε(n̄, 2), where N is how many ions share the gate's trap: the "
                   "Murali et al. chain-length term, A ∝ N/ln N, calibrated so a two-ion "
                   "trap adds nothing",
        "source": "primitives.ms_gate.error_vs_chain (\"murali:N_ref\"); N from the "
                  "replayed occupancy (the quantity R13 caps)",
    },
    {
        "channel": "gate_1q",
        "where": "after every single-qubit pulse R(θ, φ), on its ion (and after each "
                 "abstract H, S, S_DAG, X, Y or Z)",
        "stim": "DEPOLARIZE1(s · (1 − F₁))",
        "formula": "1 − F₁. The frame change VZ is noiseless: it is a phase update, not a "
                   "pulse, and its duration still reaches the idle channel",
        "source": "primitives.1q_gate.fidelity",
    },
    {
        "channel": "measure",
        "where": "immediately before every measurement, on the measured ion",
        "stim": "X_ERROR(s · (1 − F_m)) then M",
        "formula": "1 − F_m: the probability the readout reports the wrong outcome",
        "source": "primitives.measure.fidelity",
    },
    {
        "channel": "reset",
        "where": "immediately after every reset, on the reset ion",
        "stim": "R then X_ERROR(s · e_r)",
        "formula": "e_r: the probability the ion is left in |1⟩ instead of |0⟩",
        "source": "primitives.reset.error",
    },
    {
        "channel": "idle",
        "where": "after every instruction, on every ion in the program (transport, "
                 "cooling, gates and readout all take time, and every ion dephases while "
                 "they do)",
        "stim": "Z_ERROR(s · (1 − e^(−Δt/T₂)) / 2)",
        "formula": "pure dephasing over the instruction's replayed duration Δt; the "
                   "instructions run one after another, as the replay times them",
        "source": "species.T_coh_s (T₂), unless the model fixes t2_s; Δt from the "
                  "primitives' durations at the model's operating-point table",
    },
)


@dataclass(frozen=True)
class MsError:
    """One two-qubit gate's error, and how it splits across the budget's MS channels."""

    total: float
    base: float
    heating: float
    chain: float


@dataclass(frozen=True)
class NoiseModel:
    """The rules that turn a replayed program into noise.  See `CHANNELS`.

    ``t2_s``     override the device's T₂ (seconds); `None` reads `species.T_coh_s`
    ``scale``    multiply every probability (1 for an official model; 0 is noiseless)
    ``heating``  price n̄ into the MS error (off: every gate as if freshly cooled)
    ``chain``    price chain length into the MS error (off: every gate in a two-ion trap)
    ``idle``     apply the idle dephasing channel
    ``table``    the operating-point table the replay's durations and heating come from
    """

    #: the stim channel `@2`'s crosstalk emits with (a class attribute, not a field, so a
    #: model's settings document is unchanged by it)
    crosstalk_op = "DEPOLARIZE1"

    id: str = "qccd-noise@1"
    t2_s: float | None = None
    scale: float = 1.0
    heating: bool = True
    chain: bool = True
    idle: bool = True
    table: str = "qccdsim_jones"

    # ------------------------------------------------------------------ parameters

    def resolve(self, arch) -> dict[str, Any]:
        """Every number this model reads from `arch`, JSON-safe.  What a report records."""
        prim = arch.primitives
        ms = prim.scalar("ms_gate")
        t2 = self.t2_s if self.t2_s is not None else arch.species.get("T_coh_s")
        t2 = float(t2) if t2 else None
        return {
            "id": self.id,
            "table": self.table,
            "scale": self.scale,
            "heating": self.heating,
            "chain": self.chain,
            "idle": self.idle,
            "p1": 1.0 - float(prim.scalar("1q_gate").get("fidelity", 1.0)),
            "pm": 1.0 - float(prim.scalar("measure").get("fidelity", 1.0)),
            "pr": float(prim.scalar("reset").get("error", 0.0)),
            "t2_s": t2,
            "t2_source": "model" if self.t2_s is not None else "species.T_coh_s",
            "ms": {
                "eps0": 1.0 - float(ms.get("fidelity_at_n0", 1.0)),
                "error_vs_quanta": str(ms.get("error_vs_quanta", "linear:0")),
                "error_vs_chain": ms.get("error_vs_chain"),
                "max_quanta": ms.get("max_quanta"),
            },
        }

    # ------------------------------------------------------------------ channels

    def ms_error(self, cost_model, arch, nbar: float, n_chain: int) -> MsError:
        """ε(n̄, N) split into base (n̄ = 0, N = 2), heating and chain, then scaled and capped.

        The split evaluates the cost model's own `gate_error` at three settings, so the
        three parts always add up to exactly the number the replay prices (R16).  A capped
        total shrinks the parts in proportion, so the budget still sums to the circuit.
        """
        nbar_eff = self.nbar_at_gate(max(float(nbar), 0.0)) if self.heating else 0.0
        n_eff = int(n_chain) if (self.chain and n_chain) else 2
        e_base = cost_model.gate_error(arch, 0.0, 2)
        e_heat = cost_model.gate_error(arch, nbar_eff, 2)
        e_full = cost_model.gate_error(arch, nbar_eff, n_eff)
        raw = self.scale * e_full
        total = min(CAPS["DEPOLARIZE2"], max(raw, 0.0))
        f = (total / raw) if raw > 0 else 0.0
        return MsError(total=total,
                       base=f * self.scale * e_base,
                       heating=f * self.scale * (e_heat - e_base),
                       chain=f * self.scale * (e_full - e_heat))

    def p_1q(self, params: Mapping) -> float:
        return min(CAPS["DEPOLARIZE1"], self.scale * params["p1"])

    def p_measure(self, params: Mapping) -> float:
        return min(CAPS["X_ERROR"], self.scale * params["pm"])

    def p_reset(self, params: Mapping) -> float:
        return min(CAPS["X_ERROR"], self.scale * params["pr"])

    def p_idle(self, params: Mapping, dt_us: float) -> float:
        t2 = params.get("t2_s")
        if not self.idle or not t2 or dt_us <= 0:
            return 0.0
        pz = 0.5 * (1.0 - math.exp(-dt_us * 1e-6 / float(t2)))
        return min(CAPS["Z_ERROR"], self.scale * pz)

    def to_json(self) -> dict:
        return asdict(self)

    # ------------------------------------------------------------------ what @2 adds
    #
    # `qccd-noise@1` prices only the gates, so two machines running one circuit differ only
    # where their gate counts or chain lengths do.  These hooks are where a model that also
    # prices the SCHEDULE puts the rest; `@1` answers none of them, so its circuits are
    # unchanged byte for byte.

    def channel_names(self) -> tuple[str, ...]:
        return CHANNEL_NAMES

    def channels(self) -> tuple[dict[str, str], ...]:
        return CHANNELS

    def nbar_at_gate(self, replay_nbar: float) -> float:
        """The n̄ an MS gate sees, from the replay's (which counts from the last cool)."""
        return replay_nbar

    def crosstalk_error(self, params: Mapping, kind: str) -> float:
        """The error a measurement or reset puts on each OTHER ion of its trap; 0 under `@1`."""
        return 0.0

    def crosstalk_global(self, params: Mapping, kind: str) -> float:
        """The error a measurement or reset puts on each ion in ANOTHER trap; 0 under `@1`."""
        return 0.0

    def p_initial(self, params: Mapping) -> float:
        """The error of each ion's first preparation in |0⟩; `@1` takes it as noiseless."""
        return 0.0

    #: `@2` replaces the per-instruction T₂ channel with memory charged per ion: linearly in
    #: the time it is not being operated on, and coherently over each idle stretch.
    per_ion_memory = False

    def memory_linear(self, params: Mapping, dt_us: float) -> float:
        return 0.0

    def memory_coherent(self, params: Mapping, stretch_us: float) -> float:
        return 0.0


# ---------------------------------------------------------------------- qccd-noise@2
#
# What a SCHEDULE costs, so that two machines running one circuit get different logical error
# rates when they spend different time idle, in transport or beside a readout.  Every number
# is a published measurement on trapped-ion QCCD hardware (or arithmetic on two of them, said
# where it is), and every channel is a Pauli channel stim samples directly: a coherent rotation
# enters as its Pauli twirl, and leakage as the Pauli error the vendor's own stabilizer emulator
# converts it to.  What the literature does not support is left out and said so, rather than
# given an invented number.
#
# `@2` reads the device's gate, readout and transport numbers exactly as `@1` does, from the
# fixed blocks of its physics; the boards that use it pin a physics whose every number is cited
# (qccd/workspace/physics/qccd-physics@2.json), and its operating-point table is "quantinuum":
# Quantinuum's published transport measurements.

#: Helios (Quantinuum, 98 ¹³⁷Ba⁺ qubits) measures memory error growing linearly and
#: quadratically with idle time (arXiv:2511.05465, Table II p.7, §III B4: a + b·l + c·l² per
#: 55 ms depth-1 layer).  The vendor's emulator fixes the two rates it uses for exactly this, on
#: idle AND transport time: Z with probability r·t, and a coherent Z rotation at f (in units of
#: 2π rad/s) whose stabilizer form is Z with probability sin²(π·f·t) (Quantinuum Helios-1E
#: emulator parameters, docs.quantinuum.com, 2025-11-18).  The two sources agree:
#: sin²(π·f·55 ms) = 1.0e-4, i.e. 6.7e-5 average infidelity, the paper's c = 7(2)e-5.  The
#: paper finds most of the linear term is leakage (4.0e-4 of 5e-4 per layer); the emulator
#: charges it as Z, and so does this model.  The emulator's stabilizer default also multiplies
#: f by 1.5 to stand in for coherent build-up; this model keeps the measured f.
HELIOS_MEMORY_RATE_PER_S = 7.9e-3
HELIOS_COHERENT_DEPHASING_HZ = 0.058
#: Ground-state cooling leaves n̄ < 0.05 on the axial modes (EIT on Ba⁺ plus sideband on Yb⁺,
#: Burton et al., arXiv:2206.11888, the Ba-Yb experiment of Fig. 3), not 0: every gate starts
#: from at least this.
COOLING_NBAR_FLOOR = 0.05
#: A mid-circuit measurement and reset disturbs the other ions (Helios, arXiv:2511.05465
#: §III B5 p.10, Fig. 10): 2.1(1)e-4 on each of the 3 laser-adjacent ions, 4.8(1)e-5 averaged
#: over all 97 spectators.  The ions that do not share the trap therefore see
#: (97·4.8e-5 − 3·2.1e-4)/94 = 4.3e-5 each.  The channel is bit-flip plus leakage; twirled here.
#: It is charged once per measure-and-reset, at the measurement.
CROSSTALK_LOCAL = 2.1e-4
CROSSTALK_GLOBAL_AVERAGE, HELIOS_SPECTATORS, HELIOS_ADJACENT = 4.8e-5, 97, 3
CROSSTALK_GLOBAL = ((HELIOS_SPECTATORS * CROSSTALK_GLOBAL_AVERAGE - HELIOS_ADJACENT * CROSSTALK_LOCAL)
                    / (HELIOS_SPECTATORS - HELIOS_ADJACENT))
#: A reset alone disturbs the other ions far less: 3.5e-6 (Helios-1E `p_crosstalk_init`).
CROSSTALK_RESET = 3.5e-6

CHANNEL_NAMES_2 = ("ms_base", "ms_heating", "ms_chain", "gate_1q", "measure", "reset",
                   "memory", "memory_coherent", "crosstalk", "crosstalk_global")


def _channels_2() -> tuple[dict[str, str], ...]:
    keep = {c["channel"]: dict(c) for c in CHANNELS}
    heat = keep["ms_heating"]
    heat["formula"] = (heat["formula"] + ", plus the n̄ ground-state cooling leaves behind "
                       f"({COOLING_NBAR_FLOOR}, so no gate is ever at n̄ = 0)")
    heat["source"] = (heat["source"] + ", at the \"quantinuum\" table; the cooling floor from "
                      "arXiv:2206.11888 (Fig. 3's Ba-Yb experiment)")
    return (
        keep["ms_base"], heat, keep["ms_chain"], keep["gate_1q"], keep["measure"], keep["reset"],
        {
            "channel": "memory",
            "where": "after every instruction, on every ion it does not operate on (idling, "
                     "riding a rotation or being shuttled: Quantinuum charges memory on idle "
                     "and transport time alike)",
            "stim": "Z_ERROR(s · r · Δt)",
            "formula": f"r = {HELIOS_MEMORY_RATE_PER_S} /s, the Helios linear memory rate; Δt the "
                       "instruction's replayed duration. Most of the measured linear term is "
                       "leakage, which the vendor's stabilizer emulator charges as Z, as here",
            "source": "arXiv:2511.05465 Table II and §III B4 (linear memory term); Quantinuum "
                      "Helios-1E emulator parameters (linear_dephasing_rate)",
        },
        {
            "channel": "memory_coherent",
            "where": "on an ion at the end of each of its idle stretches: before the next gate, "
                     "measurement or reset that operates on it, and before the final readout",
            "stim": "Z_ERROR(s · sin²(π · f · T))",
            "formula": f"f = {HELIOS_COHERENT_DEPHASING_HZ} Hz, a coherent Z rotation of angle "
                       "2πfT over the stretch's length T, entered as its Pauli twirl; it grows "
                       "as T², so a long wait costs far more than many short ones",
            "source": "arXiv:2511.05465 Table II (quadratic memory term, 7(2)e-5·l² per 55 ms "
                      "layer); Quantinuum Helios-1E emulator parameters (quadratic_dephasing_rate)",
        },
        {
            "channel": "crosstalk",
            "where": "after every measurement, on each OTHER ion in the same trap",
            "stim": f"DEPOLARIZE1(s · {CROSSTALK_LOCAL})",
            "formula": "the error a measurement and reset puts on the laser-adjacent ions",
            "source": "arXiv:2511.05465 §III B5 p.10, Fig. 10 (local MCMR crosstalk 2.1(1)e-4)",
        },
        {
            "channel": "crosstalk_global",
            "where": "after every measurement, and every reset, on each ion in ANOTHER trap",
            "stim": f"DEPOLARIZE1(s · {CROSSTALK_GLOBAL:.2g}) after a measurement, "
                    f"DEPOLARIZE1(s · {CROSSTALK_RESET:g}) after a reset",
            "formula": f"(97 · 4.8e-5 − 3 · 2.1e-4) / 94 = {CROSSTALK_GLOBAL:.2g}: Helios's average "
                       "over all 97 spectators with the 3 laser-adjacent ones taken out",
            "source": "arXiv:2511.05465 §III B5 p.10 (global MCMR crosstalk 4.8(1)e-5); "
                      "Quantinuum Helios-1E emulator parameters (p_crosstalk_init)",
        },
    )


@dataclass(frozen=True)
class NoiseModel2(NoiseModel):
    """`qccd-noise@1` plus what the schedule does (see `CHANNEL_NAMES_2` and `_channels_2`).

    Left out, each for want of a published number: a per-primitive qubit error for a shuttle,
    junction, split or swap (no QCCD vendor or experiment reports one: transport costs its time,
    which `memory` charges, and its heating, which the MS error charges); ion loss (no vendor
    number, and not a Pauli channel); MS-gate crosstalk (not detected on Helios, below 4.3e-4).
    Leakage is inside the measured gate and memory errors and is charged as their Pauli error.
    """

    id: str = "qccd-noise@2"
    table: str = "quantinuum"
    memory_rate_per_s: float = HELIOS_MEMORY_RATE_PER_S
    coherent_dephasing_hz: float = HELIOS_COHERENT_DEPHASING_HZ
    nbar_floor: float = COOLING_NBAR_FLOOR
    crosstalk_local: float = CROSSTALK_LOCAL
    crosstalk_far: float = CROSSTALK_GLOBAL
    crosstalk_reset: float = CROSSTALK_RESET

    per_ion_memory = True

    def resolve(self, arch) -> dict[str, Any]:
        # the operating-point policy falls back to another table's points when its own has none,
        # which would price this model's schedule from numbers it does not cite: refuse instead
        prim = arch.primitives
        missing = [name for name, curve in prim.curves.items()
                   if not any(p.table == self.table for p in curve.points)]
        missing += [f"{name}[degree {d}]" for name, dc in prim.degree_curves.items()
                    for d, curve in dc.by_degree.items()
                    if d >= 3 and not any(p.table == self.table for p in curve.points)]
        if missing:
            raise ValueError(f"{self.id} prices transport from the {self.table!r} operating-point "
                             f"table, which this device's physics does not declare for "
                             f"{', '.join(sorted(missing))}")
        out = super().resolve(arch)
        out.update({"memory_rate_per_s": self.memory_rate_per_s,
                    "coherent_dephasing_hz": self.coherent_dephasing_hz,
                    "nbar_floor": self.nbar_floor, "crosstalk_local": self.crosstalk_local,
                    "crosstalk_far": self.crosstalk_far, "crosstalk_reset": self.crosstalk_reset})
        # the T₂ channel of @1 is replaced by `memory`: say so rather than report a T₂ it never uses
        out["t2_s"], out["t2_source"] = None, "replaced by the memory channels"
        return out

    def channel_names(self) -> tuple[str, ...]:
        return CHANNEL_NAMES_2

    def channels(self) -> tuple[dict[str, str], ...]:
        return _channels_2()

    def nbar_at_gate(self, replay_nbar: float) -> float:
        return replay_nbar + self.nbar_floor

    def p_idle(self, params: Mapping, dt_us: float) -> float:
        return 0.0                      # replaced by memory_linear, per ion

    def memory_linear(self, params: Mapping, dt_us: float) -> float:
        if dt_us <= 0:
            return 0.0
        return min(CAPS["Z_ERROR"], self.scale * self.memory_rate_per_s * dt_us * 1e-6)

    def memory_coherent(self, params: Mapping, stretch_us: float) -> float:
        if stretch_us <= 0:
            return 0.0
        p = math.sin(math.pi * self.coherent_dephasing_hz * stretch_us * 1e-6) ** 2
        return min(CAPS["Z_ERROR"], self.scale * p)

    def crosstalk_error(self, params: Mapping, kind: str) -> float:
        p = self.crosstalk_local if kind == "measure" else 0.0
        return min(CAPS["DEPOLARIZE1"], self.scale * p)

    def crosstalk_global(self, params: Mapping, kind: str) -> float:
        p = self.crosstalk_far if kind == "measure" else self.crosstalk_reset
        return min(CAPS["DEPOLARIZE1"], self.scale * p)

    def p_initial(self, params: Mapping) -> float:
        return self.p_reset(params)     # the first preparation is a reset like any other


#: The published models.  `qccd-noise@1` is `NoiseModel()` with its defaults.
MODELS: dict[str, NoiseModel] = {"qccd-noise@1": NoiseModel(), "qccd-noise@2": NoiseModel2()}

#: Which text a model's knobs set, knob by knob: (NoiseModel field, the text's parameter).
_KNOBS = (("scale", "scale"), ("heating", "heating"), ("chain", "chain"), ("idle", "idle"),
          ("t2_s", "t2"), ("memory_rate_per_s", "memory_rate"),
          ("coherent_dephasing_hz", "coherent_rate"), ("nbar_floor", "nbar_floor"),
          ("crosstalk_local", "crosstalk_near"), ("crosstalk_far", "crosstalk_far"),
          ("crosstalk_reset", "crosstalk_reset"))


def evaluation(model: "NoiseModel | str"):
    """The evaluation text that IS this model (`qccd/evalset/library/<id>.eval`), with every
    knob the model sets differently from the published one written onto the text's parameters,
    and the model's operating-point table as its clock.  This is what writes the circuit: the
    probability functions on the classes above are the hand-written model the text replaced,
    kept for the tests that hold the two together."""
    from ..evalset import load

    nm = get(model) if isinstance(model, str) else model
    base = "qccd-noise@2" if isinstance(nm, NoiseModel2) else "qccd-noise@1"
    ev = load(base)
    default = MODELS[base]
    knobs = {}
    for field_, param in _KNOBS:
        if not hasattr(nm, field_) or param not in ev.params:
            continue
        v = getattr(nm, field_)
        if v != getattr(default, field_):
            knobs[param] = v
    if knobs:
        ev = ev.with_params(knobs)
    if nm.table != (ev.clock or default.table):
        ev = ev.with_clock(nm.table)
    return ev


def get(model_id: str) -> NoiseModel:
    try:
        return MODELS[model_id]
    except KeyError:
        raise KeyError(f"no noise model {model_id!r} (known: {', '.join(MODELS)})") from None


def describe(model_id: str = "qccd-noise@1") -> dict:
    """A model's settings and its channel table, JSON-safe: the `noise` reference section."""
    m = get(model_id)
    ev = evaluation(m)
    return {"id": m.id, "settings": m.to_json(), "channels": [dict(c) for c in m.channels()],
            # what writes the circuit: the model's evaluation text, and each channel as written
            "text": ev.text(), "digest": ev.digest(), "clock": ev.clock,
            "clauses": [{"channel": c.name, "title": c.title, "clause": c.text,
                         "budget": [k for k, _ in c.counted] or [c.name], "about": c.about,
                         "source": c.source} for c in ev.channels],
            "caps": dict(CAPS),
            "notes": ["probabilities are per operand: per pair for DEPOLARIZE2, per ion "
                      "otherwise",
                      "the initial |0⟩ of every ion is noiseless; an explicit reset is not"
                      if type(m).p_initial is NoiseModel.p_initial else
                      "the initial |0⟩ of every ion carries the reset's error, as every reset does",
                      "the logical error rate per round is 1 − (1 − LER)^(1/rounds)"]}
