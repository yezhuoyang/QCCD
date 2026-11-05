"""One prime on one floorplan (tools/lattice_prime.py, FULLSTACK_PLAN S9 step 5).

Held here, on small streams (the whole prime is the tool's own run):
* a measurement and reset on one patch (`ppm Z q -> m; if m: x q`) is a readout and a reset,
  the outcome kept -- without the flip it is not -- and a lone one, the patch kept, is a merge
  with a fresh patch made in its basis;
* a step's signature does not see names (outcomes, ancillas) but does see a program block;
* the scheduler takes outcomes measured before its stream as decoded (`known`) and, without
  them, refuses the guard that reads one;
* `bring(only=)` brings only the blocks it is told to, and leaves the rest to the scheduler;
* a capped corridor search finds the corridor the uncapped one finds, when it is short enough.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import lattice_prime as LP  # noqa: E402

from qccd.fullstack.scale import relay as R  # noqa: E402
from qccd.fullstack.scale.codesign import equal_drop  # noqa: E402
from qccd.fullstack.scale.lattice import LatticeError, _Board, _route, check, schedule  # noqa: E402
from qccd.fullstack.scale.program import Template  # noqa: E402


@pytest.mark.parametrize("letter", "ZX")
def test_a_measurement_and_reset_on_one_patch_is_a_readout_and_a_reset(letter):
    assert LP.one_patch_proved(letter)
    lone = Template("m", (f"ppm {letter} p0 -> o0",), (0,), 1, 0, 1, 0)
    rp = Template("rp", (f"read p0 {letter} -> o0", f"prep p0 {letter}"), (0, 0), 1, 0, 1, 0)
    assert not equal_drop(lone, rp)               # without the flip the state is not reset


@pytest.mark.parametrize("letter", "ZX")
def test_a_lone_measurement_is_a_merge_with_a_fresh_patch(letter):
    assert LP.one_patch_kept_proved(letter)


def test_the_rewrite_takes_the_pair_and_a_lone_product_takes_a_fresh_patch():
    lines = [("ppm", ("Z", "q1"), "m", ()), ("x", ("q2",), None, ("k",)), ("x", ("q1",), None, ("m",))]
    assert LP.one_patch(lines) == [("read", ("q1", "Z"), "m", ()), ("prep", ("q1", "Z"), None, ()),
                                   ("x", ("q2",), None, ("k",))]
    lone = [("ppm", ("Z", "q1"), "m", ("g",)), ("ht", ("q1",), None, ("g",))]
    assert LP.one_patch(lone) == [("prep", ("m.a", "Z"), None, ()), ("ppm", ("Z", "q1", "Z", "m.a"), "m", ("g",)),
                                  ("read", ("m.a", "Z"), "m.a", ("g",)), ("ht", ("q1",), None, ("g",))]


def test_a_signature_sees_program_blocks_not_names():
    def step(anc, out, blk):
        return [("prep", (anc, "X"), None, ()), ("ppm", ("Z", blk, "Z", anc), out, ()),
                ("read", (anc, "X"), out + "r", ()), ("z", (blk,), None, (out,))]
    a = LP.signature(step("a1", "m1", "q5"), {"a1": (0, 1)}, [])
    assert a == LP.signature(step("b7", "m9", "q5"), {"b7": (0, 1)}, [])
    assert a != LP.signature(step("a1", "m1", "q6"), {"a1": (0, 1)}, [])
    assert a != LP.signature(step("a1", "m1", "q5"), {"a1": (1, 0)}, [])


def test_outcomes_measured_before_the_stream_are_known():
    homes = {"q0": (0, 0)}
    stream = [("x", ("q0",), None, ("earlier",)), ("read", ("q0", "Z"), "m", ()), ("prep", ("q0", "Z"), None, ("m",))]
    with pytest.raises(LatticeError, match="nothing before it measures"):
        schedule(stream, homes, (-1, -1, 1, 1))
    gadgets = schedule(stream, homes, (-1, -1, 1, 1), known=["earlier"])
    assert check(stream, gadgets, known=["earlier"]) == [] and gadgets[0].t0 == 0.0
    assert check(stream, gadgets)                  # without them the check objects


def test_bring_only_brings_the_blocks_it_is_told_to():
    homes = {"c": (0, 0), "t": (4, 3)}
    stream = [("prep", ("L", "X"), None, ()), ("ppm", ("Z", "c", "Z", "L"), "a", ()),
              ("ppm", ("X", "L", "X", "t"), "b", ()), ("read", ("L", "Z"), "r", ()),
              ("prep", ("M", "X"), None, ()), ("ppm", ("Z", "c", "Z", "M"), "d", ()),
              ("ppm", ("X", "M", "X", "t"), "e", ()), ("read", ("M", "Z"), "s", ())]
    out, pins = R.bring(stream, homes, {}, set(), only=lambda k, blk: blk == "L")
    assert "L" in pins and "M" not in pins
    assert [ln for ln in out if "M" in ln[1]] == [ln for ln in stream if "M" in ln[1]]


def test_a_capped_search_finds_the_corridor_an_uncapped_one_does():
    board = _Board({"a": (0, 0), "b": (4, 0)}, (-1, -3, 5, 3))
    for t in [(1, 0), (2, 0), (3, 0), (2, -1)]:   # a wall across the straight way
        board.take(t, 0.0, float("inf"), "w")
    tiles = [(0, 0), (4, 0)]
    free = _route(board, tiles, "X", 0.0, 1.0, {"a", "b"})
    assert free is not None and _route(board, tiles, "X", 0.0, 1.0, {"a", "b"}, len(free[1])) == free
    assert _route(board, tiles, "X", 0.0, 1.0, {"a", "b"}, len(free[1]) - 1) is None
