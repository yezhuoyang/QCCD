"""G1: the gate error's chain-length term, and the calibration that keeps it a refinement.

`Codesign/EVALUATION.md` G1. Before this term existed, `gate_error` was `eps0 + k*nbar`
with no dependence on how many ions share the trap -- so a search over trap capacity would
have driven it to R13's hard cap of 15 and reported the cap as an optimum, because longer
chains cost exactly nothing.

The whole change rests on one property, and it is the first thing tested here: **at the
reference chain length the new model is the old model, to the last bit, for every n-bar.**
That is what makes it a refinement of a validated model rather than a replacement of one,
and it is why no already-verified number on a capacity-2 device moved when it landed.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.cost.models import _chain_factor  # noqa: E402

ARCH = ROOT / "arch"
DEVICES = sorted(p.name[: -len(".arch.json")] for p in ARCH.glob("*.arch.json"))


@pytest.fixture(scope="module")
def model():
    return corrected_model()


def _legacy(arch, nbar: float) -> float:
    """The pre-G1 formula, written out rather than imported, so this test still fails if
    the implementation is changed to agree with itself."""
    spec = arch.primitives.scalar("ms_gate")
    eps0 = 1.0 - float(spec["fidelity_at_n0"])
    slope = float(str(spec["error_vs_quanta"]).partition(":")[2])
    return eps0 + slope * max(nbar, 0.0)


@pytest.mark.parametrize("device", DEVICES)
@pytest.mark.parametrize("nbar", [0.0, 0.1, 0.25, 0.5, 0.75, 1.0])
def test_reference_chain_reproduces_the_legacy_model_exactly(device, nbar, model):
    """The calibration constraint, on every shipped architecture.

    Not `approx`: the constants are chosen so the two expressions are algebraically the
    same polynomial in n-bar, so they must agree bit for bit. A tolerance here would let
    a mis-derived kappa through.
    """
    arch = load(str(ARCH / f"{device}.arch.json"))
    n_ref = int(str(arch.primitives.scalar("ms_gate")["error_vs_chain"]).partition(":")[2])
    assert model.gate_error(arch, nbar, n_ref) == _legacy(arch, nbar)


@pytest.mark.parametrize("device", DEVICES)
def test_omitting_the_chain_length_gives_the_legacy_model(device, model):
    """A caller that does not know the chain length must never be silently given a
    different number than it got before the argument existed."""
    arch = load(str(ARCH / f"{device}.arch.json"))
    for nbar in (0.0, 0.5, 1.0):
        assert model.gate_error(arch, nbar) == _legacy(arch, nbar)
        assert model.gate_error(arch, nbar, None) == _legacy(arch, nbar)


def test_longer_chains_are_never_cheaper(model):
    """The free lunch G1 exists to remove -- including the one the raw formula creates.

    `N / ln N` has a minimum at `N = e`, so taken literally it makes a 3-ion chain 5.4 %
    *better* than a 2-ion one. `_chain_factor`'s monotone envelope removes that, because
    an optimiser handed a 5 % discount at N=3 would take it.
    """
    arch = load(str(ARCH / "ring144_24v.arch.json"))
    errs = [model.gate_error(arch, 0.3, N) for N in range(2, 33)]
    assert errs == sorted(errs), "gate error must be non-decreasing in chain length"
    assert _chain_factor(3, 2) == _chain_factor(2, 2), "the N=e dip must be clamped away"
    assert 3 / math.log(3) < 2 / math.log(2), "...and it is a real dip in the raw form"


def test_r13s_cap_now_costs_something(model):
    """The point of the exercise: a chain at R13's limit must be measurably worse.

    If this ratio is 1.0 the term is not doing its job and a capacity sweep is unsafe.
    """
    arch = load(str(ARCH / "ring144_24v.arch.json"))
    at_ref = model.gate_error(arch, 0.0, 2)
    at_cap = model.gate_error(arch, 0.0, 15)
    assert at_cap / at_ref > 1.4, f"R13's cap of 15 ions costs only {at_cap / at_ref:.3f}x"


def test_a_chain_shorter_than_two_cannot_ask_for_a_discount(model):
    """R6b co-locates both ions of a pair, so N < 2 cannot arise from a legal program.
    The clamp is a guard against a caller, and it must not read as a one-ion gate being
    cheaper than a two-ion one."""
    arch = load(str(ARCH / "ring144_24v.arch.json"))
    two = model.gate_error(arch, 0.4, 2)
    assert model.gate_error(arch, 0.4, 1) == two
    assert model.gate_error(arch, 0.4, 0) == two


def test_gate_duration_is_unscaled_unless_an_architecture_says_otherwise(model):
    """`us_vs_chain` is deliberately undeclared everywhere: `ms_gate` does not say whether
    the gate is AM, FM or PM, and Murali's linear scaling is FM-specific. The mechanism
    must exist and must stay off until an architecture states a gate type."""
    for device in DEVICES:
        arch = load(str(ARCH / f"{device}.arch.json"))
        spec = arch.primitives.scalar("ms_gate")
        assert "us_vs_chain" not in spec, f"{device} declares us_vs_chain"
        base = float(spec["us"])
        for N in (2, 8, 15):
            assert model.gate(arch, "MS", 1, N).us == base


def test_the_mechanism_works_when_it_is_declared(model):
    """...and when a gate type *is* stated, the duration scales and is normalised so the
    declared `us` is the duration at the reference length."""
    arch = load(str(ARCH / "ring144_24v.arch.json"))
    spec = dict(arch.primitives.scalar("ms_gate"))
    spec["us_vs_chain"] = "linear:2"
    arch.primitives.scalars["ms_gate"] = spec
    assert model.gate(arch, "MS", 1, 2).us == float(spec["us"])
    assert model.gate(arch, "MS", 1, 4).us == pytest.approx(2 * float(spec["us"]))
    assert model.gate(arch, "MS", 1, None).us == float(spec["us"])


def test_a_cost_model_written_before_G1_still_replays():
    """The compatibility guarantee, and it is not hypothetical -- G1 broke this once.

    `gate` and `gate_error` gained an `n_chain` argument. The replay first passed it
    positionally, which raised `TypeError` on every `CostModel` subclass overriding the
    old three-argument `gate` -- `test_review_regressions`'s `HotGate` caught it, and a
    user's own cost model would have broken identically with no test to notice. The replay
    now asks each method whether it accepts the argument. This pins that.
    """
    from qccd.cost.models import Charge
    from qccd.ir.tsir import TSIR, Instruction
    from qccd.verify import replay
    from qccd.verify.replay import _accepts_chain

    arch = load(str(ARCH / "ring144_24v.arch.json"))

    class PreG1(type(corrected_model())):
        """Exactly the signatures that shipped before G1 -- no `n_chain` anywhere."""

        def gate(self, arch, gate, n_pairs):
            return Charge(cost=0.0, depth=1, us=25.0, quanta={"gate": 0.5})

        def gate_error(self, arch, nbar):
            return 0.001

    m = PreG1()
    assert not _accepts_chain(m.gate), "the probe must see the old signature"
    assert not _accepts_chain(m.gate_error)

    placement = {"d0": "A0", "a0": "A0"}
    prog = TSIR(name="t", arch_spec="inline", instructions=[
        Instruction(type="init", id=0, placement=placement,
                    quanta={k: 0.0 for k in placement}),
        Instruction(type="gate", id=1, gate="CX", mode="intra",
                    pairs=(("d0", "a0"),), sites=("A0",)),
    ])
    res = replay(prog, arch, m, check_rules=False, keep_cycles=False)
    assert res.n_gate_pairs == 1
    assert res.gate_error_sum == 0.001, "the old two-argument gate_error must be used"
    assert res.per_ion_quanta["d0"]["gate"] == pytest.approx(0.5)
    # and the chain length is still recorded, because the replay knows it either way
    assert dict(res.chain_len_at_gate) == {2: 1}


def test_the_probe_is_conservative_about_signatures_it_cannot_read():
    """An unreadable signature must fall back to the argument the method definitely has."""
    from qccd.verify.replay import _accepts_chain

    assert not _accepts_chain(None)
    assert not _accepts_chain(lambda arch, nbar: 0.0)
    assert _accepts_chain(lambda arch, nbar, n_chain=None: 0.0)
    assert _accepts_chain(lambda *a, **kw: 0.0), "a catch-all can take it"


def test_a_bad_calibration_is_refused_rather_than_silently_floored(model):
    """`eps0' = eps0 - slope/2` goes negative if an architecture declares a heating slope
    more than twice its zero-quanta infidelity. Clamping that at zero would quietly change
    the model; it must raise."""
    arch = load(str(ARCH / "ring144_24v.arch.json"))
    spec = dict(arch.primitives.scalar("ms_gate"))
    spec["error_vs_quanta"] = "linear:1.0"          # slope >> 2 * eps0
    arch.primitives.scalars["ms_gate"] = spec
    with pytest.raises(ValueError, match="slope <= 2"):
        model.gate_error(arch, 0.0, 2)
