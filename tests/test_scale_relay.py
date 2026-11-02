"""Relay chains (qccd/fullstack/scale/relay.py, FULLSTACK_PLAN S9 step 5).

Held here:
* a chain of k relays is the product it replaces, its new outcomes forgotten (k = 0 .. 6, Z̄
  and X̄), and without its frames it is not;
* a CNOT's ancilla goes where its control's column meets its target's row, and both of its
  products then become chains on the tiles between;
* scheduled, a crossbar of such CNOTs passes the schedule's check with two merge programs only
  (a Z̄ pair down a column, an X̄ pair along a row), each a corridor merge at full distance, and
  a pinned relay waits for its tile.
"""
from __future__ import annotations

from collections import Counter

import pytest

from qccd.fullstack.scale import relay as R
from qccd.fullstack.scale.codesign import equal_drop
from qccd.fullstack.scale.lattice import LatticeError, check, schedule
from qccd.fullstack.scale.program import Template


@pytest.mark.parametrize("letter", "ZX")
def test_a_chain_is_the_product_it_replaces(letter):
    assert all(R.proved(letter, k) for k in range(7))


def test_without_its_frames_a_chain_is_not():
    lines = ("prep l0 X", "prep l1 X", "ppm Z p0 Z l0 -> o1", "ppm Z l1 Z p1 -> o2", "ppm Z l0 Z l1 -> o3",
             "read l0 X -> o4", "read l1 X -> o5", "bit o1 ^ o2 ^ o3 -> o0")
    a = Template("bad", lines, (0,) * len(lines), 2, 2, 6, 0)
    b = Template("ppm", ("ppm Z p0 Z p1 -> o0",), (0,), 2, 0, 1, 0)
    assert not equal_drop(a, b)
    assert equal_drop(Template("good", lines + ("if o4: z p0", "if o5: z p0"), (0,) * 10, 2, 2, 6, 0), b)


@pytest.mark.parametrize("letter", "ZX")
def test_a_move_is_a_swap_into_a_fresh_patch(letter):
    assert R.move_proved(letter)
    lines = R.move(letter, "a", "n", ["r0", "r1"], "mv")
    assert lines[0] == ("prep", ("n", R.OTHER[letter]), None, ())
    assert ("read", ("a", R.OTHER[letter]), "mv.s", ()) in lines
    # a move down a column (Z̄) schedules clean on the one pair program
    homes = {"a": (0, 0)}
    pins = {"n": (0, 3), "r0": (0, 1), "r1": (0, 2)} if letter == "Z" else {"n": (3, 0), "r0": (1, 0), "r1": (2, 0)}
    stream = [("prep", ("a", "Z"), None, ())] + lines + [("read", ("n", "Z"), "out", ())]
    gadgets = schedule(stream, homes, (-1, -1, 4, 4), pinned=pins)
    assert check(stream, gadgets) == []
    assert len({g.shape for g in gadgets if g.kind == "merge"}) == 1


def _cnot(c, t, n):
    return [("prep", (f"L{n}", "X"), None, ()), ("ppm", ("Z", c, "Z", f"L{n}"), f"a{n}", ()),
            ("ppm", ("X", f"L{n}", "X", t), f"b{n}", ()), ("read", (f"L{n}", "Z"), f"c{n}", ()),
            ("z", (c,), None, (f"b{n}",)), ("x", (t,), None, (f"a{n}",)), ("x", (t,), None, (f"c{n}",))]


def _crossbar(n_c=4, n_t=3):
    """Controls c0.. along the bottom row, targets t0.. in a column on the left; every pair."""
    homes = {f"c{s}": (s, n_t) for s in range(n_c)} | {f"t{j}": (-1, j) for j in range(n_t)}
    stream = [ln for s in range(n_c) for j in range(n_t) for ln in _cnot(f"c{s}", f"t{j}", s * n_t + j)]
    return stream, homes, (-2, -1, n_c, n_t + 1)


def test_a_cnot_ancilla_sits_at_the_crossing_and_its_products_become_chains():
    stream, homes, _ = _crossbar()
    pins = R.crossings(stream, homes)
    assert pins["L5"] == (1, 2)                  # c1's column, t2's row
    relayed, rpins, src = R.relay(stream, {**homes, **pins})
    # L5: Z̄ to c1 at (1, 3) -- adjacent, no relay; X̄ to t2 at (-1, 2) -- one relay on (0, 2)
    kz = next(i for i, ln in enumerate(stream) if ln[0] == "ppm" and ln[1][3] == "L5")
    kx = next(i for i, ln in enumerate(stream) if ln[0] == "ppm" and ln[1][1] == "L5")
    assert [b for b in rpins if b.startswith(f"r{kx}.")] == [f"r{kx}.0"] and rpins[f"r{kx}.0"] == (0, 2)
    assert not any(b.startswith(f"r{kz}.") for b in rpins)
    assert R.line((0, 0), (0, 3)) == [(0, 1), (0, 2)] and R.line((3, 1), (0, 1)) == [(2, 1), (1, 1)]
    assert set(src) == set(range(len(stream)))


def test_a_crossbar_schedules_clean_on_two_merge_programs():
    from qccd.qec.experiments import corridor_merge, ideal_stim
    stream, homes, bounds = _crossbar()
    pins = R.crossings(stream, homes)
    relayed, rpins, _ = R.relay(stream, {**homes, **pins})
    gadgets = schedule(relayed, homes, bounds, pinned={**pins, **rpins})
    assert check(relayed, gadgets) == []
    merges = Counter(g.shape for g in gadgets if g.kind == "merge")
    assert set(merges) == {("merge", "Z", (((0, 0), "bottom"), ((0, 1), "top")), ()),
                           ("merge", "X", (((0, 0), "right"), ((1, 0), "left")), ())}
    for _, letter, patches, corridor in merges:
        c = ideal_stim(corridor_merge(3, letter, list(patches), set(corridor)))
        assert not c.compile_detector_sampler().sample(16, append_observables=True).any()


def test_a_pinned_relay_waits_for_its_tile_and_a_held_tile_is_refused():
    homes = {"a": (0, 0), "b": (0, 3), "c": (1, 1)}
    stream = [("ppm", ("X", "c", "X", "u"), "o0", ()), ("prep", ("u", "Z"), None, ())]
    with pytest.raises(KeyError):                 # (u is made after it is used: a malformed stream)
        schedule(stream, homes, (-1, -1, 2, 4), pinned={"u": (0, 1)})
    stream, pins, _ = R.relay([("ppm", ("Z", "a", "Z", "b"), "o", ())], homes)
    gadgets = schedule(stream, homes, (-1, -1, 2, 4), pinned=pins)
    assert check(stream, gadgets) == [] and sorted(pins.values()) == [(0, 1), (0, 2)]
    held = [("prep", ("v", "X"), None, ()), ("prep", ("w", "X"), None, ())]
    with pytest.raises(LatticeError, match="not free"):
        schedule(held, homes, (-1, -1, 2, 4), pinned={"v": (0, 1), "w": (0, 1)})
