"""PR A configuration; source: Helios reference bca3de9 and docs/REPORT.md."""
from dataclasses import dataclass, field
from .noise import NoiseModel3

@dataclass(frozen=True, slots=True)
class NoiseAt4Config:
    """Only PR A mechanisms. Defaults advance with their owning implementation commit."""
    thermal_gate_error: str = "linear"
    idle_policy: str = "per_interval"
    meas_crosstalk_scope: str = "chip"

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
