"""Distance costs time -- opt-in, because the shipped oracles were calibrated without it.

An audit of our own model against the corpus found that `Segment.length` was computed by
every generator, serialized into every arch file, and read by no cost model: a spur of
length 10 cost exactly what a spur of length 0.5 cost. That is not a defensible default
for a tool meant to benchmark routing, where the whole question is which ion goes how far.

The fix is opt-in rather than automatic. `Segment.length` is in trap-pitch units and the
`shuttle_segment` curve point is calibrated for one pitch, so a straight segment of length
L is L pitches of transport. Corner segments keep their turn-based charge, because what
makes a turn expensive is the turn.

Direction of the correction, from the corpus: arXiv:2605.25118 finds phonon number far
more sensitive to duration than to distance -- the near-adiabatic regime is reached within
20 us "regardless of the transport distance". So length must not add heat on its own; it
adds *time*, and heat follows time. Scaling both by one hop count is exactly that.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.cost import corrected_model, deck_model  # noqa: E402
from qccd.verify import replay  # noqa: E402

ARCH = ROOT / "arch"


def _ring():
    return Machine.load(ARCH / "ring144_24v.arch.json")


def test_off_by_default_so_every_shipped_oracle_is_untouched():
    m = _ring()
    p = m.program("hop").fill().rotate(+1).build()
    assert corrected_model().length_scaling is False
    base = replay(p, m.arch, corrected_model())
    scaled = replay(p, m.arch, corrected_model(length_scaling=True))
    # the shipped ring is built on a unit pitch (144 rail segments of length 1.0 and 24
    # half-pitch spurs, which floor to one transport primitive), so even turning it ON
    # changes nothing there -- the oracle is safe either way
    assert base.total_cost == scaled.total_cost
    assert base.total_us == scaled.total_us
    lengths = {round(sg.length, 3) for sg in m.device.segments.values()}
    assert lengths == {1.0, 0.5}


def test_the_deck_oracle_is_unaffected_because_it_never_had_a_length_term():
    m = _ring()
    r = replay(m.program("hop").fill().rotate(+1).build(), m.arch, deck_model())
    assert (r.total_cost, r.total_steps) == (148, 3)


def test_a_longer_segment_takes_longer_when_scaling_is_on():
    """The defect the audit found, stated as a test: two identical hops over segments of
    different length must not cost the same."""
    m = Machine.chain(3, name="stretched")
    dev = m.device
    segs = sorted(dev.segments.values(), key=lambda s: s.id)
    short, long = segs[0], segs[1]
    m.set_segment_length(long.id, 4.0)

    def hop(a, b):
        p = m.program(f"{a}{b}").init({"d": a})
        with p.cycle("shuttle") as c:
            c.move("d", a, b)
        return p

    model = corrected_model(length_scaling=True)
    r_short = m.run(hop("C0", "C1"), model=model, check_metrics=False)
    r_long = m.run(hop("C1", "C2"), model=model, check_metrics=False)
    assert r_long.runtime_ms == 4 * r_short.runtime_ms
    assert r_long.cost == 4 * r_short.cost
    # heat follows time, it is not an extra distance penalty on top
    assert r_long.peak_quanta == 4 * r_short.peak_quanta

    flat = corrected_model()
    assert m.run(hop("C0", "C1"), model=flat, check_metrics=False).runtime_ms == \
        m.run(hop("C1", "C2"), model=flat, check_metrics=False).runtime_ms


def test_a_corner_is_charged_for_its_turn_not_its_length():
    """A corner segment contains a whole 180-degree turn; making it geometrically long
    must not double-charge it on top of the turn."""
    m = _ring()
    dev = m.device
    corner = next(s for s in dev.segments.values()
                  if dev.corner_endpoints.get(s.id, 0) == 2)
    p = m.program("hop").fill().rotate(+1).build()
    before = replay(p, m.arch, corrected_model(length_scaling=True)).total_cost
    m.set_segment_length(corner.id, 9.0)
    after = replay(p, m.arch, corrected_model(length_scaling=True)).total_cost
    assert after == before


def test_the_model_says_whether_it_is_scaling():
    assert corrected_model(length_scaling=True).describe()["length_scaling"] is True
    assert corrected_model().describe()["length_scaling"] is False
