"""The reviewers' transport variables: a shuttle's time from a speed and the rail's
physical length, opt-in, with every shipped number untouched by default."""

from qccd.api import Machine
from qccd.cost import corrected_model
from qccd.verify import replay


def _walk(m):
    p = m.program("t", provenance="off")
    p.init({"d0": "C0"})
    p.move("d0", "C0", "C1")
    return p.build()


def test_speed_derives_the_hop_time_from_the_rail_length():
    m = Machine.chain(4)
    base = replay(_walk(m), m.arch, corrected_model(), check_rules=False)
    fast = replay(_walk(m), m.arch, corrected_model(shuttle_speed_m_s=1.0, pitch_um=225.0), check_rules=False)
    # a unit rail is 225 um; at 1 m/s (= 1 um/us) that is 225 us
    assert abs(fast.total_us - 225.0) < 1e-9
    assert base.total_us != fast.total_us
    slow = replay(_walk(m), m.arch, corrected_model(shuttle_speed_m_s=0.5, pitch_um=225.0), check_rules=False)
    assert abs(slow.total_us - 450.0) < 1e-9


def test_default_is_the_curve_point():
    m = Machine.chain(4)
    model = corrected_model()
    assert model.shuttle_speed_m_s is None
    res = replay(_walk(m), m.arch, model, check_rules=False)
    assert abs(res.total_us - model._point(m.arch, "shuttle_segment").us) < 1e-9
