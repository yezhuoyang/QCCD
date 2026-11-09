"""Magic state cultivation on the ion machine (`qccd.fullstack.scale.cultivation`, docs/FULLSTACK_PLAN.md S10 step 4), and the factory that
turns cultivated T states into FormalRV's |AutoCCZ> (`factory.cultivated_autoccz`).  What must hold: Gidney, Shutty and Jones's circuits play
on the tile lattice gate for gate, every rule and R10, every detector deterministic without noise; the cultivated T leaves a plain patch a
merge can take; the factory makes FormalRV's state exactly and catches every single T fault; its schedule on a slot is sound for every way
round its outputs must leave."""
from __future__ import annotations

import itertools
import random
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tests"))

from qccd.fullstack.scale import cultivation as C
from qccd.fullstack.scale import factory as F
from qccd.fullstack.scale import programs as PR

CIRCUITS = REPO / "qccd" / "qec" / "cultivation"


def _program(text, name):
    r = C.read(text, name=name)
    prog, cert, arch = C.build(r)
    e = r.experiment
    b = PR.Built((name,), e.qasm, r.rows, r.cols, arch, prog, cert, e.n_data + e.n_ancilla)
    circuit, ext = C.noisy(r, PR.cooled(b), arch)
    return r, b, circuit, ext


def test_injection_and_cultivation_at_d1_3_play_on_the_tiles(tmp_path):
    """Their 15 qubits on 7 data traps and 8 face traps, every CX a face ion stepping to its data ion; at the machine's noise nearly every attempt
    is kept, and its leading error, counted exactly, is far below the record's at p = 5e-4 (4.0e-8)."""
    text = (CIRCUITS / "inject_cultivate_d1=3.stim").read_text(encoding="utf-8")
    r, b, circuit, ext = _program(text, "cultivate_d3")
    assert (r.experiment.n_data, r.experiment.n_ancilla) == (7, 8)
    assert not C.without_noise(str(circuit)).compile_detector_sampler().sample(64, append_observables=True).any()
    got = PR.check(b, tmp_path)
    assert got["failed"] == [] and got["ions"] == 15
    kept, errors = C.accepted(circuit, 400_000)
    assert kept / 400_000 > 0.99
    leading = C.undetected(circuit)
    assert leading[1][1] == 0 and leading[2][1] == 0           # distance 3: no one or two faults pass unseen
    assert 0 < leading[3][1] < 1e-10


def test_a_cultivated_t_on_a_d11_patch_is_one_program_and_leaves_a_plain_patch(tmp_path):
    """The end-to-end construction, dropped to a plain patch (`to_surface`) and two of our rounds: 246 ions, every rule and R10, every detector
    deterministic, and the time the floorplan charges for a T."""
    from qccd.fullstack.scale.gidney_surgery import T_US
    text = (CIRCUITS / "end2end_d1=3_d2=11_r1=1_r2=1.stim").read_text(encoding="utf-8")
    plain = C.to_surface(text, 11, 2)
    r, b, circuit, ext = _program(plain, "cultivated_T")
    assert not C.without_noise(str(circuit)).compile_detector_sampler().sample(64, append_observables=True).any()
    got = PR.check(b, tmp_path)
    assert got["failed"] == [] and got["ions"] == 246
    assert got["us"] == T_US


def test_a_shuttle_cycle_moves_every_ion_the_same_way():
    """One cycle is one waveform (R22): a colour code's layer has up to four ways out, each its own cycle; a surface round's are unchanged."""
    from qccd.qec.experiments import rotated_surface
    from qccd.compile.tiled_surface import compile_tiled
    text = (CIRCUITS / "inject_cultivate_d1=3.stim").read_text(encoding="utf-8")
    r = C.read(text)
    prog, _, arch = C.build(r)
    dev = arch.device
    for ins in prog.instructions:
        if ins.type == "simd" and ins.cls == "shuttle":
            ways = set()
            for p in ins.participants:
                a, c = dev.nodes[p.src].pos, dev.nodes[p.dst].pos
                ways.add((round(c[0] - a[0], 3), round(c[1] - a[1], 3)))
            assert len(ways) == 1, ins.id
    e = rotated_surface(3, 3)
    assert len(compile_tiled(e.qasm, 3, 3)[0].instructions) == 115      # as before the split: a round's layers have one way out a leg


def test_y_read_in_place_holds_every_rule_and_r10(tmp_path):
    from qccd.fullstack.scale.gidney_surgery import READ_Y_US
    got = PR.check(PR.build(("read", "Y")), tmp_path)
    assert got["failed"] == [] and got["us"] == READ_Y_US


def test_hbar_by_three_y_merges_is_h():
    rng = random.Random(3)
    for trial in range(12):
        v = np.random.default_rng(trial).normal(size=2) + 1j * np.random.default_rng(trial + 99).normal(size=2)
        v /= np.linalg.norm(v)
        sv = F._SV()
        sv.add("b", list(v))
        sv, _ = F.run(F.hbar("b", "t"), rng, sv=sv)
        assert abs(np.vdot(F._H @ v, sv.psi)) ** 2 == pytest.approx(1.0, abs=1e-12)


def test_the_cultivated_factory_makes_formalrvs_state_and_catches_every_single_t_fault():
    """The correction on the T state (read in Ȳ instead of X̄) and H̄ by |Ȳ> merges change nothing in what the factory makes."""
    got = F.cultivated_census(30)
    assert got["accepted"] == 30
    assert got["fidelity"] == pytest.approx(1.0, abs=1e-9)
    assert got["caught"] == got["mags"] == 8


def test_the_factory_schedules_soundly_on_its_slot_for_every_way_its_outputs_leave():
    """`SurgeryPlan._factory_unit` for each of the eight ways x, y, z can be asked to leave: `lattice.check` finds nothing, the outputs leave
    the way asked, and an output that turns costs the three merges of H̄."""
    from qccd.fullstack.scale import gidney_surgery as S
    plan = S.SurgeryPlan.__new__(S.SurgeryPlan)     # the unit needs no floorplan: it is scheduled on a slot of its own
    plan._units = {}
    base = None
    for want in itertools.product((0, 1), repeat=3):
        u = S.SurgeryPlan._factory_unit(plan, want)
        assert [label for _, label in u["sites"][:3]] == list(want)
        merges = sum(1 for k, *_ in u["items"] if k == "merge")
        turned = sum(w != l for w, l in zip(want, (0, 0, 1)))
        if base is None and turned == 0:
            base = merges
        assert sum(1 for k, *_ in u["items"] if k == "|T>") == 8
    for want in itertools.product((0, 1), repeat=3):
        u = S.SurgeryPlan._factory_unit(plan, want)
        turned = sum(w != l for w, l in zip(want, (0, 0, 1)))
        assert sum(1 for k, *_ in u["items"] if k == "merge") == base + 3 * turned
