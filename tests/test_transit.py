"""The occupancy law: what the picture must do when one ion is in another's way.

`qccd/viz/js/transit.js` decides where every ion is drawn while the machine is moving it,
for the studio stage and the gadget Design canvas alike, and `qccd/viz/transit.py` is its
twin for `tools/make_gif.py`.  Before it existed there were three implementations and they
disagreed: the studio's was careful, the Design canvas placed each ion without reference to
any other (41% of sampled instants drew two ions through each other, the worst pair at a
separation of exactly zero), and the GIF renderer walked paths hop-uniformly and stacked
ions with a plain `sorted()`.

The properties asserted here are the ones a reader of the picture is entitled to:

  * an ion never passes THROUGH another -- no two marks are ever drawn closer than the
    slot pitch that separates them at rest;
  * an exchange is an exchange -- the two ions end in each other's slots, and they are
    drawn going round each other on OPPOSITE sides rather than meeting in the middle;
  * nothing teleports -- the end of one step is exactly the start of the next, for every
    ion, and the motion within a step is continuous;
  * nothing is duplicated or lost -- the same ion names come out that went in.

`tests/test_transit_parity.py` is the other half: it pins the two implementations to each
other.  This file tests the Python one, which is the readable one.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.viz.transit import Geometry, Step, Transit, natural_key  # noqa: E402

PITCH = 0.3


def line(n: int, *, spacing: float = 1.0) -> dict[str, tuple[float, float]]:
    """`n` nodes in a row: N0 .. N{n-1}."""
    return {f"N{i}": (i * spacing, 0.0) for i in range(n)}


def transit(nodes, *, bow: float = 0.5, site=None, axis=None) -> Transit:
    def slots(_node, k):
        return [(j - (k - 1) / 2) * PITCH for j in range(k)], PITCH

    return Transit(Geometry(
        pos=lambda nid: nodes.get(nid),
        axis=(axis or (lambda nid: (1.0, 0.0))),
        slot_offsets=slots,
        site=(site or (lambda nid: nid)),
        bow=bow,
    ))


def walk(T: Transit, steps: list[Step], samples: int = 8):
    """Every ion's drawn position over the whole programme, sample by sample.

    Returns `[(step, t, {ion: (x, y, placed)})]`, which is what every property below is
    measured on -- the picture, not the intent behind it.
    """
    order = T.slot_order(steps)
    out = []
    for k, st in enumerate(steps):
        for s in range(samples + 1):
            t = s / samples
            placed = T.place(st, t, order[k - 1] if k > 0 else {}, order[k])
            out.append((k, t, {i: (p.x, p.y, p) for i, p in placed.items()}))
    return out


def closest(row) -> tuple[float, tuple[str, str] | None]:
    best, who = math.inf, None
    ions = sorted(row)
    for i, a in enumerate(ions):
        for b in ions[i + 1:]:
            d = math.dist(row[a][:2], row[b][:2])
            if d < best:
                best, who = d, (a, b)
    return best, who


# ------------------------------------------------------- moving into an occupied cell


def test_two_ions_moving_together_keep_their_spacing():
    """A chain in a moving well keeps its spacing along the direction it moves.

    Two ions share N0 and walk N0 -> N1 -> N2 together, the way every ion of a trap does in
    a rigid rotation.  The slot offset used to be SHED on the rail -- the fix for single
    ions cutting corners -- which put both of them on the rail's centre line at the same
    point: `centres_apart: 0`, measured by `tests/test_studio.py`'s census on a ring144
    rotation with two ions in one trap.  The along-rail part of the offset is carried as an
    arc shift now, so they are a pitch apart the whole way, on the rail.
    """
    nodes = line(3)
    T = transit(nodes)
    steps = [Step(before={}, pos={"a": "N0", "b": "N0"}, paths={}),       # the init
             Step(before={"a": "N0", "b": "N0"}, pos={"a": "N2", "b": "N2"},
                  paths={"a": ["N0", "N1", "N2"], "b": ["N0", "N1", "N2"]})]
    rows = walk(T, steps, samples=40)
    worst = min(closest(r)[0] for _, _, r in rows)
    assert worst >= PITCH - 1e-9, f"two ions moving together drawn {worst:.4f} apart, pitch {PITCH}"
    # b led the chain, so b is still in front when it arrives: the order is kept
    assert T.slot_order(steps)[-1] == {"N2": ["a", "b"]}
    # and on the rail: the line is y = 0, so nothing strays from it
    assert max(abs(v[1]) for _, _, r in rows for v in r.values()) < 1e-12


def test_the_detour_is_zero_at_the_frame_boundaries():
    """Nothing moves at a frame boundary, even when the pair's pitches differ.

    `a` leaves a TIGHT trap (pitch 0.1) for a WIDE one (pitch 0.5), past `b`.  The detour's
    clearance is the wider pitch's, which is more than `a` and `b` stand apart -- so it used
    to lift `a` at t = 0, where it was still resting, and the picture jumped at the seam:
    1.08 px on `tcx72.cx`, whose 72-ion traps sit beside two-slot ones.  The clearance is
    now capped by where the pair stand at both ends of the step.
    """
    nodes = line(2)
    pitch = {"N0": 0.1, "N1": 0.5}

    def slots(node, k):
        return [(j - (k - 1) / 2) * pitch[node] for j in range(k)], pitch[node]

    T = Transit(Geometry(pos=lambda nid: nodes.get(nid), axis=lambda nid: (1.0, 0.0),
                         slot_offsets=slots, site=lambda nid: nid, bow=0.5))
    steps = [Step(before={}, pos={"a": "N0", "b": "N0", "d": "N1"}, paths={}),
             Step(before={"a": "N0", "b": "N0", "d": "N1"}, pos={"a": "N1", "b": "N0", "d": "N1"},
                  paths={"a": ["N0", "N1"]})]
    order = T.slot_order(steps)
    end = T.place(steps[0], 1.0, {}, order[0])
    start = T.place(steps[1], 0.0, order[0], order[1])
    for ion in ("a", "b", "d"):
        jump = math.dist((end[ion].x, end[ion].y), (start[ion].x, start[ion].y))
        assert jump < 1e-12, f"{ion} jumps {jump:.4f} at the frame boundary"
    # and the detour is still there in the middle, where `a` has to get past `b`
    mid = T.place(steps[1], 0.25, order[0], order[1])
    assert abs(mid["a"].y) > 0 or math.dist((mid["a"].x, mid["a"].y), (mid["b"].x, mid["b"].y)) >= 0.1 - 1e-9


def test_an_ion_entering_an_occupied_trap_is_never_drawn_on_top_of_the_resident():
    """The commonest case on any lattice, and the one the Design canvas got wrong.

    `b` sits in N1 and does not move; `a` shuttles N0 -> N1 and joins it.  At no instant
    may the two marks be closer than the slot pitch they will sit at when `a` arrives.
    """
    nodes = line(2)
    T = transit(nodes)
    steps = [Step(before={"a": "N0", "b": "N1"}, pos={"a": "N1", "b": "N1"},
                  paths={"a": ["N0", "N1"]})]
    rows = walk(T, steps, samples=40)
    worst = min(closest(r)[0] for _, _, r in rows)
    assert worst >= PITCH - 1e-9, f"two ions drawn {worst:.4f} apart, pitch is {PITCH}"


def test_the_resident_makes_room_rather_than_being_shoved_at_the_last_moment():
    """A trap's population changes DURING the step, so the ion staying behind moves too.

    Pinning a rester to its end-state slot makes it jump the instant the step begins --
    straight into the ion still departing.  Its motion must be continuous.
    """
    nodes = line(2)
    T = transit(nodes)
    steps = [Step(before={"a": "N0", "b": "N1"}, pos={"a": "N1", "b": "N1"},
                  paths={"a": ["N0", "N1"]})]
    rows = walk(T, steps, samples=40)
    ys = [r["b"][0] for _, _, r in rows]
    jumps = [abs(ys[i + 1] - ys[i]) for i in range(len(ys) - 1)]
    assert max(jumps) < PITCH / 2, "the resting ion jumps rather than sliding"


# --------------------------------------------------------------- the physical swap


def swap_steps(n: int) -> list[Step]:
    """`n` consecutive exchanges of two ions across one rail."""
    steps, a, b = [], "N0", "N1"
    for _ in range(n):
        steps.append(Step(before={"a": a, "b": b}, pos={"a": b, "b": a},
                          paths={"a": [a, b], "b": [b, a]}))
        a, b = b, a
    return steps


def test_two_ions_exchanging_along_one_rail_go_round_each_other_not_through():
    """The case a single fixed detour side could not fix.

    Both ions have to get past each other, so both were bowed the same way and met in the
    middle anyway.  They must take OPPOSITE sides, and the marks must stay apart.
    """
    nodes = line(2)
    T = transit(nodes, bow=0.5)
    rows = walk(T, swap_steps(1), samples=40)
    # the two are recognised as an exchange, and given opposite sides
    mid = [r for _, t, r in rows if abs(t - 0.5) < 1e-9][0]
    assert mid["a"][2].swap and mid["b"][2].swap, "neither ion was drawn going round"
    assert mid["a"][1] * mid["b"][1] < 0, (
        f"both ions bowed to the same side: a at y={mid['a'][1]}, b at y={mid['b'][1]}")
    worst = min(closest(r)[0] for _, _, r in rows)
    assert worst > 0.2, f"the two marks come within {worst:.4f} of each other"


def test_two_trap_mates_getting_past_each_other_both_step_aside_inside_the_bar():
    """"When a pair of ions swap, show they are swapped: not directly through each other,
    but slightly off the middle, to show the ions swap in the site" (a collaborator,
    2026-09-21).

    `a` leaves N0 past its trap-mate `b`, who stays.  Only the ion in flight used to step
    aside, so the whole of the room came out of one side of the bar, and both marks were
    shrunk to specks passing on the centre line: 0.058 g apart, radii 0.026 and 0.013 g on
    `cyclone_base`'s odd-even sort.  Now BOTH step off the line, on opposite sides, by half
    of what the pair needs each -- and neither leaves the bar it is confined to.
    """
    nodes, half = line(2), 0.1                      # the bar is 0.2 thick

    def slots(_node, k):
        return [(j - (k - 1) / 2) * PITCH for j in range(k)], PITCH

    T = Transit(Geometry(pos=lambda nid: nodes.get(nid), axis=lambda nid: (1.0, 0.0),
                         slot_offsets=slots, site=lambda nid: nid, span=lambda nid: 0.44,
                         across=lambda nid: half, rail=0.02, bow=0.5))
    steps = [Step(before={}, pos={"a": "N0", "b": "N0"}, paths={}),
             Step(before={"a": "N0", "b": "N0"}, pos={"a": "N1", "b": "N0"},
                  paths={"a": ["N0", "N1"]})]
    order = T.slot_order(steps)
    rows = [(t, T.place(steps[1], t, order[0], order[1])) for t in (k / 80 for k in range(81))]
    # the frame's two ends are on the centre line, exactly
    for t, r in (rows[0], rows[-1]):
        assert r["a"].y == 0.0 and r["b"].y == 0.0, (t, r["a"].y, r["b"].y)
    # at the closest approach, both are off the middle, on opposite sides, by as much
    t, r = min(rows, key=lambda tr: math.dist((tr[1]["a"].x, tr[1]["a"].y),
                                              (tr[1]["b"].x, tr[1]["b"].y)))
    a, b = r["a"], r["b"]
    assert a.y * b.y < 0, f"at t={t} a is at y={a.y} and b at y={b.y}: not on opposite sides"
    assert math.isclose(abs(a.y), abs(b.y), rel_tol=1e-9), (a.y, b.y)
    assert abs(a.y) >= 0.4 * half, f"a pass drawn only {abs(a.y):.4f} off the middle"
    # the marks fit: neither covers the other, and each keeps a real size -- not a speck
    d = math.dist((a.x, a.y), (b.x, b.y))
    assert a.room + b.room <= d + 1e-12, (a.room, b.room, d)
    assert min(a.room, b.room) >= 0.4 * half, (a.room, b.room)
    # and nobody, at any instant, is drawn outside the bar -- centre or mark
    for t, r in rows:
        for p in r.values():
            assert abs(p.y) + (p.room if p.y else 0.0) <= half + 1e-12, (t, p)


def test_a_swap_ends_with_the_two_ions_in_each_other_s_places():
    """Identities, not just positions: after the exchange `a` is where `b` was."""
    nodes = line(2)
    T = transit(nodes)
    steps = swap_steps(1)
    order = T.slot_order(steps)
    end = T.place(steps[0], 1.0, {}, order[0])
    assert end["a"].node == "N1" and end["b"].node == "N0"
    assert math.isclose(end["a"].x, nodes["N1"][0], abs_tol=PITCH)
    assert math.isclose(end["b"].x, nodes["N0"][0], abs_tol=PITCH)


def test_repeated_swaps_stay_continuous_and_keep_both_ions():
    """Six exchanges in a row: nothing teleports, nothing is duplicated, nothing is lost."""
    nodes = line(2)
    T = transit(nodes)
    steps = swap_steps(6)
    rows = walk(T, steps, samples=12)
    for _, _, r in rows:
        assert set(r) == {"a", "b"}, f"the stage holds {sorted(r)}"
    worst = min(closest(r)[0] for _, _, r in rows)
    assert worst > 0.2, f"two marks come within {worst:.4f} during repeated swaps"
    # and the step boundaries are seamless
    for i in range(len(rows) - 1):
        (k0, t0, a), (k1, t1, b) = rows[i], rows[i + 1]
        if k1 == k0 or t0 != 1.0:
            continue
        for ion in a:
            assert math.dist(a[ion][:2], b[ion][:2]) < 1e-9, (
                f"{ion} jumps between step {k0} and {k1}")


# ----------------------------------------------------- skipping, and being skipped


def test_an_ion_whose_route_crosses_an_occupied_trap_does_not_glide_over_it():
    """A lattice route runs THROUGH the traps between its ends.

    The studio's own detour test looked only at the two ends of the walk, so an ion
    crossing a trap that another ion sits in for the whole step was drawn straight over
    it.  It must go round.
    """
    nodes = line(3)
    T = transit(nodes)
    steps = [Step(before={"a": "N0", "b": "N1"}, pos={"a": "N2", "b": "N1"},
                  paths={"a": ["N0", "N1", "N2"]})]
    order = T.slot_order(steps)
    mid = T.place(steps[0], 0.5, {}, order[0])
    assert mid["a"].swap, "the crossing ion was not drawn going round the resident"
    rows = walk(T, steps, samples=40)
    worst = min(closest(r)[0] for _, _, r in rows)
    assert worst > 0.1, f"the crossing ion comes within {worst:.4f} of the resident"


def test_a_convoy_is_not_a_conflict():
    """An ion leaving the node another is arriving at is getting out of the way.

    Treating that as a collision would put a detour on half the moves of a rotation, which
    is a picture of congestion that is not there.
    """
    nodes = line(3)
    T = transit(nodes)
    steps = [Step(before={"a": "N0", "b": "N1"}, pos={"a": "N1", "b": "N2"},
                  paths={"a": ["N0", "N1"], "b": ["N1", "N2"]})]
    order = T.slot_order(steps)
    mid = T.place(steps[0], 0.5, {}, order[0])
    assert not mid["a"].swap and not mid["b"].swap


def test_no_ion_is_duplicated_or_lost_when_a_whole_row_shifts():
    nodes = line(8)
    T = transit(nodes)
    ions = {f"d{i}": f"N{i}" for i in range(7)}
    steps = [Step(before=dict(ions),
                  pos={k: f"N{int(v[1:]) + 1}" for k, v in ions.items()},
                  paths={k: [v, f"N{int(v[1:]) + 1}"] for k, v in ions.items()})]
    rows = walk(T, steps, samples=10)
    for _, _, r in rows:
        assert set(r) == set(ions), "an ion appeared or vanished mid-shift"
        assert closest(r)[0] > 0.5, "the row bunches up"


# -------------------------------------------------------------- order and identity


def test_slot_order_is_natural_so_d10_does_not_take_d2_s_place():
    """Plain lexicographic order puts `d10` before `d2`, and the slot an ion holds then
    changes as the population around it changes -- a jump of one pitch, per frame."""
    assert natural_key("d2") < natural_key("d10")
    nodes = line(1)
    T = transit(nodes)
    steps = [Step(before={"d2": "N0", "d10": "N0"}, pos={"d2": "N0", "d10": "N0"}, paths={})]
    order = T.slot_order(steps)
    assert order[0]["N0"] == ["d2", "d10"]


def test_two_traps_at_one_coordinate_share_one_slot_stack():
    """A device may put two nodes at one point -- the BB72 memory leaf does it twelve
    times.  Ions on them are drawn at the same spot unless occupancy is grouped by PLACE.
    """
    nodes = {"A": (0.0, 0.0), "B": (0.0, 0.0)}
    T = transit(nodes, site=lambda nid: "A")
    steps = [Step(before={"a": "A", "b": "B"}, pos={"a": "A", "b": "B"}, paths={})]
    order = T.slot_order(steps)
    assert sorted(order[0]["A"]) == ["a", "b"]
    placed = T.place(steps[0], 1.0, {}, order[0])
    assert math.dist((placed["a"].x, placed["a"].y),
                     (placed["b"].x, placed["b"].y)) >= PITCH - 1e-9


# ------------------------------------------------------------------- the path walk


def test_an_ion_advances_at_one_speed_along_a_path_of_unequal_hops():
    """By arc length, not by hop count.

    Spreading `t` over the HOPS makes an ion crossing a 3-unit segment and then a 1-unit
    one spend half the step on each: one shuttle drawn at two speeds, visibly lurching at
    the join.  Both twins did this before; the page had already been fixed.
    """
    nodes = {"A": (0.0, 0.0), "B": (3.0, 0.0), "C": (4.0, 0.0)}
    T = transit(nodes)
    path = ["A", "B", "C"]
    xs = [T.point_on_path(path, i / 200)[0] for i in range(201)]
    steps = [xs[i + 1] - xs[i] for i in range(len(xs) - 1)]
    assert max(steps) - min(steps) < 1e-6, "the ion changes speed at the join"
    assert math.isclose(xs[-1], 4.0, abs_tol=1e-9)
    assert math.isclose(xs[len(xs) // 2], 2.0, abs_tol=0.02)


@pytest.mark.parametrize("bad", [float("nan"), -1.0, 2.0])
def test_a_phase_outside_zero_to_one_is_clamped_rather_than_drawn(bad):
    nodes = line(2)
    T = transit(nodes)
    steps = [Step(before={"a": "N0"}, pos={"a": "N1"}, paths={"a": ["N0", "N1"]})]
    order = T.slot_order(steps)
    p = T.place(steps[0], bad, {}, order[0])["a"]
    assert math.isfinite(p.x) and math.isfinite(p.y)
    assert 0.0 - PITCH <= p.x <= 1.0 + PITCH
