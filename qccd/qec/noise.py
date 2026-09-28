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

__all__ = ["NoiseModel", "CHANNELS", "CHANNEL_NAMES", "MODELS", "get", "describe", "MsError",
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
        nbar_eff = max(float(nbar), 0.0) if self.heating else 0.0
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


#: The published models.  `qccd-noise@1` is `NoiseModel()` with its defaults.
MODELS: dict[str, NoiseModel] = {"qccd-noise@1": NoiseModel()}


def get(model_id: str) -> NoiseModel:
    try:
        return MODELS[model_id]
    except KeyError:
        raise KeyError(f"no noise model {model_id!r} (known: {', '.join(MODELS)})") from None


def describe(model_id: str = "qccd-noise@1") -> dict:
    """A model's settings and its channel table, JSON-safe: the `noise` reference section."""
    m = get(model_id)
    return {"id": m.id, "settings": m.to_json(), "channels": [dict(c) for c in CHANNELS],
            "caps": dict(CAPS),
            "notes": ["probabilities are per operand: per pair for DEPOLARIZE2, per ion "
                      "otherwise",
                      "the initial |0⟩ of every ion is noiseless; an explicit reset is not",
                      "the logical error rate per round is 1 − (1 − LER)^(1/rounds)"]}
