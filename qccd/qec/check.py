"""The noiseless check: does the extracted circuit do what the source circuit does?

Run the extracted circuit with every noise channel removed.  Two things must then hold for
every declared detector and observable:

1. **it is deterministic** -- the circuit fixes its value; and
2. **its value is 0** -- the value the SOURCE circuit gives it, because an experiment's
   detectors are defined as parities that come out 0 when nothing went wrong.

Determinism alone is not enough, and the case that shows it is a flipped MS sign:
MS(-π/2) for MS(+π/2) is a Pauli-frame slip, which keeps every detector deterministic and
only flips some of their VALUES.  Checking the parity against the noiseless reference
sample catches it.  A dropped pulse or a dropped gate usually breaks determinism instead.

Unlike the certificate's tableau check, this one sees measurements and resets, and needs
no certificate: it grades the program on what it does to the detectors.  It never raises
for a wrong program -- a wrong program is a result, reported in `CheckResult`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

__all__ = ["CheckResult", "noiseless_check"]

#: gates whose every target writes one measurement record
_MEASURING_1 = {"M", "MZ", "MX", "MY", "MR", "MRZ", "MRX", "MRY"}
#: pair-measurements: one record per pair
_MEASURING_2 = {"MXX", "MYY", "MZZ"}


@dataclass
class CheckResult:
    ok: bool
    reason: str
    nondeterministic: bool = False
    wrong_detectors: list[int] = field(default_factory=list)
    wrong_observables: list[int] = field(default_factory=list)
    random_detectors: list[int] = field(default_factory=list)
    random_observables: list[int] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def _records(inst) -> int:
    """How many measurement records one flattened instruction appends."""
    import stim

    name = inst.name
    if name in _MEASURING_1:
        return len(inst.targets_copy())
    if name in _MEASURING_2:
        return len(inst.targets_copy()) // 2
    if name.startswith("M") or name.startswith("HERALDED"):
        one = stim.Circuit()
        one.append(inst)
        return one.num_measurements
    return 0


def noiseless_check(circuit, *, shots: int = 256) -> CheckResult:
    """Determinism and reference parity of every detector and observable (see module)."""
    try:
        clean = circuit.without_noise()
    except Exception as exc:  # noqa: BLE001 -- a broken circuit is a failed check, not a crash
        return CheckResult(False, f"the circuit could not be read: {exc}")

    # determinism: stim refuses to build a model around a non-deterministic detector
    nondet_reason = ""
    try:
        clean.detector_error_model()
    except ValueError as exc:
        nondet_reason = str(exc).strip().splitlines()[0] if str(exc).strip() else repr(exc)

    random_dets: list[int] = []
    random_obs: list[int] = []
    if nondet_reason:
        # which ones: sampled detection events relative to the reference sample are all 0
        # for a deterministic detector and fair coins for a random one (2^-shots to miss)
        try:
            import numpy as np

            det, obs = clean.compile_detector_sampler(seed=0).sample(
                shots, separate_observables=True)
            random_dets = [int(i) for i in np.flatnonzero(det.any(axis=0))]
            random_obs = [int(i) for i in np.flatnonzero(obs.any(axis=0))]
        except Exception:  # noqa: BLE001 -- the reason string still says what is wrong
            pass

    # parity: the reference sample is one noiseless run; deterministic parities are its
    try:
        ref = clean.reference_sample()
    except Exception as exc:  # noqa: BLE001
        return CheckResult(False, f"no reference sample: {exc}", nondeterministic=bool(nondet_reason))
    n_meas = 0
    det_index = 0
    wrong_dets: list[int] = []
    obs_parity: dict[int, int] = {}
    for inst in clean.flattened():
        name = inst.name
        if name == "DETECTOR":
            par = 0
            for t in inst.targets_copy():
                par ^= int(ref[n_meas + t.value])
            if par and det_index not in random_dets:
                wrong_dets.append(det_index)
            det_index += 1
        elif name == "OBSERVABLE_INCLUDE":
            k = int(inst.gate_args_copy()[0])
            par = obs_parity.get(k, 0)
            for t in inst.targets_copy():
                if t.is_measurement_record_target:
                    par ^= int(ref[n_meas + t.value])
            obs_parity[k] = par
        else:
            n_meas += _records(inst)
    wrong_obs = sorted(k for k, v in obs_parity.items() if v and k not in random_obs)

    problems = []
    if nondet_reason:
        what = []
        if random_dets:
            what.append(f"{len(random_dets)} detector(s) (first {random_dets[:8]})")
        if random_obs:
            what.append(f"observable(s) {random_obs[:8]}")
        problems.append("non-deterministic " + (" and ".join(what) if what else "detectors")
                        + f": {nondet_reason}")
    if wrong_dets:
        problems.append(f"{len(wrong_dets)} detector(s) have parity 1 where the source "
                        f"circuit gives 0 (first {wrong_dets[:8]})")
    if wrong_obs:
        problems.append(f"observable(s) {wrong_obs[:8]} have parity 1 where the source "
                        f"circuit gives 0")
    if clean.num_observables == 0:
        problems.append("the circuit declares no observable")
    ok = not problems
    return CheckResult(
        ok=ok,
        reason="every detector and observable is deterministic and has the source's parity"
        if ok else "; ".join(problems),
        nondeterministic=bool(nondet_reason),
        wrong_detectors=wrong_dets,
        wrong_observables=wrong_obs,
        random_detectors=random_dets,
        random_observables=random_obs,
    )
