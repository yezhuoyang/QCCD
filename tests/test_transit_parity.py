"""The occupancy law has two implementations.  They must not drift apart.

`qccd/viz/js/transit.js` runs in the browser -- the studio stage and the gadget Design
canvas both call it -- and `qccd/viz/transit.py` is its twin for `tools/make_gif.py`, which
has no JavaScript runtime.  This is the same arrangement `engine.js` is under, and it is
here for the same reason: three implementations of "where is this ion right now" is how the
project ended up with a page that drew a docking ion correctly and a GIF of the same
programme that drew it on top of its neighbour.

Every scenario below is run through both and compared to 1e-9 -- positions, the detour
flag, the slot pitches, which pairs were found to conflict, and which side each one was
sent round.  A change to one that is not made to the other fails here.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.viz.transit import Geometry, Step, Transit  # noqa: E402

HARNESS = Path(__file__).parent / "transit.mjs"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(
    NODE is None, reason="node is not on PATH; the JavaScript twin cannot be run"
)

PITCH = 0.3
BOW = 0.5


def _line(n, spacing=1.0):
    return {f"N{i}": [i * spacing, 0.0] for i in range(n)}


#: One entry per shape of conflict the law has an opinion about.  They are deliberately
#: small: a scenario that needs a device to state it is a scenario that will be read as a
#: fact about that device rather than about the rule.
SCENARIOS: dict[str, dict] = {
    # an ion joining a resident in a two-slot trap
    "arrive": {
        "nodes": _line(2),
        "steps": [{"before": {"a": "N0", "b": "N1"}, "pos": {"a": "N1", "b": "N1"},
                   "paths": {"a": ["N0", "N1"]}}],
    },
    # the exchange: both move, both must get past the other
    "exchange": {
        "nodes": _line(2),
        "steps": [{"before": {"a": "N0", "b": "N1"}, "pos": {"a": "N1", "b": "N0"},
                   "paths": {"a": ["N0", "N1"], "b": ["N1", "N0"]}}],
    },
    # four of them in a row, so the carried-forward order is exercised
    "repeated_exchange": {
        "nodes": _line(2),
        "steps": [
            {"before": {"a": "N0", "b": "N1"}, "pos": {"a": "N1", "b": "N0"},
             "paths": {"a": ["N0", "N1"], "b": ["N1", "N0"]}},
            {"before": {"a": "N1", "b": "N0"}, "pos": {"a": "N0", "b": "N1"},
             "paths": {"a": ["N1", "N0"], "b": ["N0", "N1"]}},
            {"before": {"a": "N0", "b": "N1"}, "pos": {"a": "N1", "b": "N0"},
             "paths": {"a": ["N0", "N1"], "b": ["N1", "N0"]}},
            {"before": {"a": "N1", "b": "N0"}, "pos": {"a": "N0", "b": "N1"},
             "paths": {"a": ["N1", "N0"], "b": ["N0", "N1"]}},
        ],
    },
    # a route that runs over a trap somebody is sitting in
    "over": {
        "nodes": _line(3),
        "steps": [{"before": {"a": "N0", "b": "N1"}, "pos": {"a": "N2", "b": "N1"},
                   "paths": {"a": ["N0", "N1", "N2"]}}],
    },
    # one ion following another: not a conflict
    "convoy": {
        "nodes": _line(3),
        "steps": [{"before": {"a": "N0", "b": "N1"}, "pos": {"a": "N1", "b": "N2"},
                   "paths": {"a": ["N0", "N1"], "b": ["N1", "N2"]}}],
    },
    # unequal hops: the arc-length walk
    "unequal_hops": {
        "nodes": {"A": [0.0, 0.0], "B": [3.0, 0.0], "C": [4.0, 0.0]},
        "steps": [{"before": {"a": "A"}, "pos": {"a": "C"},
                   "paths": {"a": ["A", "B", "C"]}}],
    },
    # natural order, and a crowded trap
    "crowded": {
        "nodes": _line(2),
        "steps": [{"before": {"d2": "N0", "d10": "N0", "d1": "N1"},
                   "pos": {"d2": "N1", "d10": "N0", "d1": "N1"},
                   "paths": {"d2": ["N0", "N1"]}}],
    },
    # two nodes at one coordinate: one place
    "colocated": {
        "nodes": {"A": [0.0, 0.0], "B": [0.0, 0.0], "C": [1.0, 0.0]},
        "site": {"A": "A", "B": "A", "C": "C"},
        "steps": [{"before": {"a": "A", "b": "B", "c": "C"},
                   "pos": {"a": "A", "b": "B", "c": "A"},
                   "paths": {"c": ["C", "A"]}}],
    },
    # a whole row moving one step along, which is what a rigid rotation looks like
    "row_shift": {
        "nodes": _line(6),
        "steps": [{"before": {f"d{i}": f"N{i}" for i in range(5)},
                   "pos": {f"d{i}": f"N{i + 1}" for i in range(5)},
                   "paths": {f"d{i}": [f"N{i}", f"N{i + 1}"] for i in range(5)}}],
    },
    # a device that has lost a node the programme names: nothing may be drawn at NaN
    "missing_node": {
        "nodes": _line(2),
        "steps": [{"before": {"a": "N0", "ghost": "GONE"},
                   "pos": {"a": "N1", "ghost": "GONE"},
                   "paths": {"a": ["N0", "N1"]}}],
    },
    # A MOVER BETWEEN TWO CO-LOCATED NODES, which is the one place the two implementations
    # could still have disagreed: the JS asked "did this ion's path start and end on the
    # same NODE?" to decide whether it counts as moving for the side assignment, while the
    # Python asked about the same PLACE.  An ion shuffling between two traps at one
    # coordinate answers differently, and it can be the partner in somebody else's
    # conflict, so the side alternation could have come out mirrored between the twins.
    # `b` shuffles between two traps at one coordinate while `a` is routed straight over
    # that coordinate, so `b` is the partner in an "over" conflict -- which is what makes
    # the question reachable at all.
    "colocated_mover": {
        "nodes": {"W": [0.0, 0.0], "X": [1.0, 0.0], "Y": [1.0, 0.0], "Q": [2.0, 0.0]},
        "site": {"W": "W", "X": "X", "Y": "X", "Q": "Q"},
        "steps": [{"before": {"a": "W", "b": "X"}, "pos": {"a": "Q", "b": "Y"},
                   "paths": {"a": ["W", "X", "Q"], "b": ["X", "Y"]}}],
    },
    # TWO TRAP-MATES GOING ROUND EACH OTHER INSIDE A BAR OF REAL THICKNESS: `a` leaves past
    # `b`, and both step off the centre line, on opposite sides, capped by the bar -- the
    # only scenarios here where the confinement and the mark sizes (`room`) have anything
    # to say, so they are what pins those two halves of the law between the twins
    "trap_mates_swap": {
        "nodes": _line(2), "span": 0.44, "across": 0.1, "rail": 0.02,
        "steps": [{"before": {}, "pos": {"a": "N0", "b": "N0"}},
                  {"before": {"a": "N0", "b": "N0"}, "pos": {"a": "N1", "b": "N0"},
                   "paths": {"a": ["N0", "N1"]}}],
    },
    "trap_mates_swap_vertical": {
        "nodes": {"N0": [0.0, 0.0], "N1": [0.0, 1.0]},
        "axis": {"N0": [0.0, 1.0], "N1": [0.0, 1.0]},
        "span": 0.44, "across": 0.1, "rail": 0.02,
        "steps": [{"before": {}, "pos": {"a": "N0", "b": "N0", "c": "N0"}},
                  {"before": {"a": "N0", "b": "N0", "c": "N0"},
                   "pos": {"a": "N1", "b": "N0", "c": "N0"},
                   "paths": {"a": ["N0", "N1"]}}],
    },
    # a bar TILTED against the rail its ion leaves by (`24_cylinder12_own_a72`: 22 degrees
    # against 9), where the side a pair goes round is read off the geometry, not handed out
    "tilted_bar_leave": {
        "nodes": {"N0": [0.0, 0.0], "N1": [1.0, 0.15]},
        "axis": {"N0": [0.9271838546, 0.3746065934], "N1": [0.9890, 0.1478]},
        "span": 0.44, "across": 0.1, "rail": 0.02,
        "steps": [{"before": {}, "pos": {"a": "N0", "b": "N0"}},
                  {"before": {"a": "N0", "b": "N0"}, "pos": {"a": "N1", "b": "N0"},
                   "paths": {"a": ["N0", "N1"]}}],
    },
    # a crowded trap, where one swap is drawn at the same size as in a roomy one
    "crowded_swap": {
        "nodes": _line(2), "span": 0.44, "across": 0.1, "rail": 0.02, "pitch": 0.05,
        "steps": [{"before": {}, "pos": {"a": "N0", "b": "N0", "c": "N0", "d": "N0"}},
                  {"before": {"a": "N0", "b": "N0", "c": "N0", "d": "N0"},
                   "pos": {"a": "N1", "b": "N0", "c": "N0", "d": "N0"},
                   "paths": {"a": ["N0", "N1"]}}],
    },
    # two ions leaving one trap by opposite ends, each past the other: nobody stays, and
    # they still pass (`cross`, found on `torus4x4`)
    "cross_leave": {
        "nodes": {"L": [-1.0, 0.0], "N0": [0.0, 0.0], "R": [1.0, 0.0]},
        "span": 0.44, "across": 0.1, "rail": 0.02,
        "steps": [{"before": {}, "pos": {"a": "N0", "b": "N0"}},
                  {"before": {"a": "N0", "b": "N0"}, "pos": {"a": "R", "b": "L"},
                   "paths": {"a": ["N0", "R"], "b": ["N0", "L"]}}],
    },
    # in through the MIDDLE of a bar, from a rail across it, past a resident on the way
    # to its slot (`cyclone_base`'s vertical rails)
    "middle_entry": {
        "nodes": {"N0": [0.0, 0.0], "U": [0.0, -1.0]},
        "axis": {"N0": [1.0, 0.0], "U": [0.0, 1.0]},
        "span": 0.44, "across": 0.1, "rail": 0.02, "pitch": 0.2,
        "steps": [{"before": {}, "pos": {"b": "N0", "c": "N0"}},
                  {"before": {}, "pos": {"a": "U", "b": "N0", "c": "N0"}},
                  {"before": {"a": "U", "b": "N0", "c": "N0"},
                   "pos": {"a": "N0", "b": "N0", "c": "N0"}, "paths": {"a": ["U", "N0"]}}],
    },
    # round a corner into a bar, past its resident, in an order the programme sets: the
    # pair steps aside across the bar it MEETS in, not across the arriving ion's own
    "corner_arrive": {
        "nodes": {"V": [-1.0, 0.0], "N0": [0.0, 0.0]},
        "axis": {"V": [0.0, 1.0], "N0": [1.0, 0.0]},
        "span": 0.44, "across": 0.1, "rail": 0.02, "pitch": 0.22,
        "steps": [{"before": {}, "pos": {"a": "N0", "b": "V"}},
                  {"before": {"a": "N0", "b": "V"}, "pos": {"a": "N0", "b": "N0"},
                   "paths": {"b": ["V", "N0"]}}],
        "orders": [{"N0": ["a"], "V": ["b"]}, {"N0": ["a", "b"]}],
    },
    "exchange_in_a_bar": {
        "nodes": _line(2), "span": 0.44, "across": 0.1, "rail": 0.02,
        "steps": [{"before": {"a": "N0", "b": "N1"}, "pos": {"a": "N1", "b": "N0"},
                   "paths": {"a": ["N0", "N1"], "b": ["N1", "N0"]}}],
    },
    # a vertical rail, so the axis is not the default
    "vertical": {
        "nodes": {"N0": [0.0, 0.0], "N1": [0.0, 1.0]},
        "axis": {"N0": [0.0, 1.0], "N1": [0.0, 1.0]},
        "steps": [{"before": {"a": "N0", "b": "N1"}, "pos": {"a": "N1", "b": "N0"},
                   "paths": {"a": ["N0", "N1"], "b": ["N1", "N0"]}}],
    },
}

SAMPLES = 6


def _python(scn: dict) -> dict:
    nodes = {k: tuple(v) for k, v in scn["nodes"].items()}
    axis = {k: tuple(v) for k, v in (scn.get("axis") or {}).items()}
    site = scn.get("site") or {}

    pitch = scn.get("pitch", PITCH)

    def slots(_node, k):
        return [(j - (k - 1) / 2) * pitch for j in range(k)], pitch

    T = Transit(Geometry(
        pos=lambda nid: nodes.get(nid),
        axis=lambda nid: axis.get(nid, (1.0, 0.0)),
        slot_offsets=slots,
        site=lambda nid: site.get(nid, nid),
        bow=scn.get("bow", BOW),
        span=lambda _nid: scn.get("span", 0.0),
        across=lambda _nid: scn.get("across", 0.0),
        rail=scn.get("rail", 0.0),
    ))
    steps = [Step(before=s["before"], pos=s["pos"], paths=s.get("paths") or {})
             for s in scn["steps"]]
    order = scn.get("orders") or T.slot_order(steps)
    frames = []
    for k, st in enumerate(steps):
        for s in range(SAMPLES + 1):
            t = s / SAMPLES
            placed = T.place(st, t, order[k - 1] if k > 0 else {}, order[k])
            pas = T.passes(st, order[k - 1] if k > 0 else {}, order[k])
            frames.append({
                "step": k, "t": t,
                "at": {i: [round(p.x, 9), round(p.y, 9), 1 if p.fly else 0,
                           1 if p.swap else 0, round(p.pitch, 9),
                           round(p.pitch_a, 9), round(p.pitch_b, 9), round(p.room, 9)]
                       for i, p in placed.items()},
                "passes": [list(x) for x in pas["pairs"]],
                "side": pas["side"],
            })
    return {"order": order, "frames": frames}


def _javascript(scn: dict, tmp_path: Path) -> dict:
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps({**scn, "samples": SAMPLES,
                                "pitch": scn.get("pitch", PITCH), "bow": scn.get("bow", BOW)}),
                    encoding="utf-8")
    out = subprocess.run([NODE, str(HARNESS), str(path)],
                         capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, f"harness failed:\n{out.stdout}\n{out.stderr}"
    return json.loads(out.stdout)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_two_implementations_place_every_ion_identically(name, tmp_path):
    scn = SCENARIOS[name]
    py, js = _python(scn), _javascript(scn, tmp_path)

    assert len(py["frames"]) == len(js["frames"])
    for a, b in zip(py["frames"], js["frames"]):
        assert (a["step"], a["t"]) == (b["step"], b["t"])
        assert set(a["at"]) == set(b["at"]), (
            f"{name} step {a['step']} t={a['t']}: python placed {sorted(a['at'])}, "
            f"javascript placed {sorted(b['at'])}")
        for ion in a["at"]:
            pa, pb = a["at"][ion], b["at"][ion]
            for j, what in enumerate(("x", "y", "fly", "swap", "pitch",
                                      "pitchA", "pitchB", "room")):
                assert abs(pa[j] - pb[j]) < 1e-9, (
                    f"{name} step {a['step']} t={a['t']} {ion}.{what}: "
                    f"python {pa[j]}, javascript {pb[j]}")


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_the_two_implementations_agree_about_who_must_pass_whom(name, tmp_path):
    scn = SCENARIOS[name]
    py, js = _python(scn), _javascript(scn, tmp_path)
    assert py["order"] == js["order"], f"{name}: the slot order differs"
    for a, b in zip(py["frames"], js["frames"]):
        assert sorted(map(tuple, a["passes"])) == sorted(map(tuple, b["passes"])), (
            f"{name} step {a['step']}: python found {a['passes']}, "
            f"javascript found {b['passes']}")
        assert a["side"] == b["side"], f"{name} step {a['step']}: sides differ"
