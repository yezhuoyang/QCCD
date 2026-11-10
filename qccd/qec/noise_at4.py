"""PR A configuration; source: Helios reference bca3de9 and docs/REPORT.md."""
from dataclasses import dataclass, field
from .noise import NoiseModel3

@dataclass(frozen=True, slots=True)
class NoiseAt4Config:
    """Only PR A mechanisms; excluded leakage/cooling/transport policies remain @3."""
    #: N:engine.py:164-170; gate_error_budget.py:58-105; profiles.py:244-264 (bca3de9).
    thermal_gate_error: str = "reference"
    #: N:engine.py:108-121,379-430; DECISIONS.md:D1, plus approved source-gate policy.
    idle_policy: str = "since_reset"
    #: N:engine.py:195-203,375; trap=site is the approved PR A mapping.
    meas_crosstalk_scope: str = "same_trap"

    def __post_init__(self):
        for name, allowed in (("thermal_gate_error", ("linear", "reference")),
                              ("idle_policy", ("per_interval", "since_reset", "whole_round")),
                              ("meas_crosstalk_scope", ("chip", "same_trap"))):
            if getattr(self, name) not in allowed:
                raise ValueError(f"{name} must be one of {allowed}")

@dataclass(frozen=True)
class NoiseModel4(NoiseModel3):
    id: str = "qccd-noise@4"
    config: NoiseAt4Config = field(default_factory=NoiseAt4Config)

    def eval_parameters(self):
        return {"thermal_gate_error": self.config.thermal_gate_error,
                "idle_policy": self.config.idle_policy,
                "meas_crosstalk_scope": self.config.meas_crosstalk_scope}

    def to_json(self):
        out = super().to_json()
        if self.config.idle_policy != "per_interval":
            from .noise_at4_idle import T_LIN, T_G
            out["t_lin"], out["t_g"] = T_LIN, T_G
        return out

    def resolve(self, arch):
        out = super().resolve(arch)
        out.update(self.to_json())
        out["r16_cost_estimate"] = "legacy linear replay estimate; use emitted channel budget for reference thermal error"
        out["trap_mapping"] = "trap = site; requires confirmation from John" if self.config.meas_crosstalk_scope == "same_trap" else "chip-wide spectators"
        return out
