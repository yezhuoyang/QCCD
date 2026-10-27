"""The machine level (docs/ARCHITECTURE.md): rules as text, floorplans, parallel execution.

* G1-G11, written in the rule language (`qccd/ruleset/library/machine.rules`), give the
  place model's own checks' verdicts (`place_checks.py`) on every example, sequential and
  parallel, and on every mutation; G12-G14 catch what those checks never looked at.
* The town is a floorplan (`examples/gadgets/floorplans/town.floor`) that loads into exactly
  the design `town()` builds, passes F1-F9, and every way of breaking it fails the rule it
  breaks.
* Parallel execution: every example passes every rule and is signed off end to end, the
  factory works while the computation runs, and the sequential policy is the old one.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from qccd.gadget.algorithm import parse_algorithm_file
from qccd.gadget.city import schedule
from qccd.gadget.floorplan import FloorplanError, load, parse_floorplan
from qccd.gadget.judge import judge_floorplan, judge_schedule, traffic
from qccd.gadget.library import LeafLibrary
from qccd.gadget.logic.flat import sign_off
from qccd.gadget.place_checks import check_places
from qccd.gadget.town import town
from qccd.ruleset import check, load as load_rules, names
from qccd.ruleset.machine import machine_execution

ROOT = Path(__file__).resolve().parents[1]
ALGS = ROOT / "examples" / "gadgets" / "algorithms"
TOWN = ROOT / "examples" / "gadgets" / "floorplans" / "town.floor"
#: the examples that run on the town (Shor names its own floorplan: test_gadget_shor.py)
EXAMPLES = sorted(f for f in ALGS.glob("*.alg")
                  if "\nfloorplan " not in "\n" + f.read_text(encoding="utf-8"))

#: GADGETS.md §9.3's makespans, from the sequential policy that measured them
#: re-measured 2026-10-20, when split and merge became charged for what a move does (#16/#17),
#: and 2026-10-26, when heat was followed across the town (GADGETS.md §4.1): junctions pass a
#: bundle one ion at a time, places with lasers cool what comes in, readouts recool, and every
#: road is timed by its own compiled program (GADGETS.md §4.2)
SEQUENTIAL_MS = {"bell_cnot": 194.4, "bell_surgery": 309.3, "ghz3": 215.3, "teleport": 592.6,
                 "magic_state": 295.1, "magic_teleport": 816.8, "frames": 121.8,
                 "s_gate": 545.1, "cond_cx": 159.4, "teleport_fix": 568.2, "t_gate": 725.5}


@pytest.fixture(scope="module")
def leaves():
    return LeafLibrary(ROOT / "out" / "gadgets" / "cache", log=lambda *a, **k: None)


@pytest.fixture(scope="module")
def lib(leaves):
    return town(leaves)


@pytest.fixture(scope="module")
def runs(lib, leaves):
    out = {}
    for f in EXAMPLES:
        alg = parse_algorithm_file(f)
        out[f.stem] = (alg, schedule(alg, lib, leaves, parallel=True),
                       schedule(alg, lib, leaves, parallel=False))
    return out


def legacy(gir, lib, leaves) -> dict:
    c = check_places(gir, lib, leaves)
    out = {r: "holds" for r in c["passed"]}
    out.update({r: "fails" for r in c["failed"]})
    out.update({r: "does not apply" for r in c["skipped"]})
    return out


def ours(gir, lib, leaves) -> dict:
    rep = check(load_rules("machine"), machine_execution(lib, leaves, gir))
    return {r.id: r.verdict for r in rep.results}


# ------------------------------------------------------------------------- the rules are text

def test_the_machine_rules_are_text_in_the_rule_language():
    rs = load_rules("machine")
    assert rs.level == "machine"
    # G1-G14 on the schedule, H1 on the hardware between the gadgets (GADGETS.md §4.2)
    assert [r.id for r in rs.rules] == [f"G{i}" for i in range(1, 15)] + ["H1"]
    assert "machine" in names("machine") and "floorplan" in names("machine")
    assert "machine" not in names()           # the device level's list is unchanged
    fp = load_rules("floorplan")
    assert [r.id for r in fp.rules] == [f"F{i}" for i in range(1, 10)]


@pytest.mark.parametrize("policy", ["parallel", "sequential"])
def test_g1_to_g11_as_text_give_the_place_checks_verdicts(runs, lib, leaves, policy):
    for stem, (alg, par, seq) in runs.items():
        gir = par if policy == "parallel" else seq
        a, b = legacy(gir, lib, leaves), ours(gir, lib, leaves)
        assert {k: b[k] for k in a} == a, stem


def _mutants(gir):
    out = {}
    g = copy.deepcopy(gir)
    c = g["carries"][3]
    c[7][0], c[7][1] = c[7][1], c[7][0]
    out["two ions swap places in a bundle"] = (g, {"G1"})
    g = copy.deepcopy(gir)
    e = [x for x in g["events"] if x[3] in ("workshop", "bridge")]
    e[1][1], e[1][2] = e[0][1], e[0][2]
    out["two ops at once at one place"] = (g, {"G5"})
    g = copy.deepcopy(gir)
    g["messages"][0][7] += 3.0
    out["a message slower than its wire"] = (g, {"G10"})
    g = copy.deepcopy(gir)
    g["windows"][0]["decoded_by"] = None
    out["a syndrome never decoded"] = (g, {"G11"})
    g = copy.deepcopy(gir)
    g["refused"].append([1, "test"])
    out["a refused instruction"] = (g, {"G7"})
    g = copy.deepcopy(gir)
    gu = [x for x in g["events"] if len(x) > 10 and x[10]]
    gu[0][10]["ready_us"] = gu[0][1] + 50
    out["a guard that is not there yet"] = (g, {"G11"})
    return out


def test_every_mutation_fails_the_same_rules_both_ways(runs, lib, leaves):
    alg, par, _ = runs["t_gate"]
    for what, (g, must) in _mutants(par).items():
        a, b = legacy(g, lib, leaves), ours(g, lib, leaves)
        assert {k: b[k] for k in a} == a, what
        assert must <= {k for k, v in b.items() if v == "fails"}, what


def test_a_bundle_nobody_takes_in_fails_g12_which_the_place_checks_never_saw(runs, lib, leaves):
    """Dropping a junction's pass leaves a bundle on a road: G1-G11 all still hold."""
    _, par, _ = runs["t_gate"]
    g = copy.deepcopy(par)
    passes = [e for e in g["events"] if e[3].startswith(("j", "w", "e")) and e[4].startswith("pass")]
    g["events"].remove(passes[3])
    assert legacy(g, lib, leaves).get("G12") is None
    assert not [k for k, v in legacy(g, lib, leaves).items() if v == "fails"]
    assert ours(g, lib, leaves)["G12"] == "fails"


def test_two_bundles_on_one_road_at_once_fail_g13(runs, lib, leaves):
    _, par, _ = runs["bell_cnot"]
    g = copy.deepcopy(par)
    by_net: dict = {}
    for c in g["carries"]:
        by_net.setdefault(c[1], []).append(c)
    a, b = next(v for v in by_net.values() if len(v) >= 2)[:2]
    shift = (b[4] - a[4]) - 1.0                 # b now starts 1 us after a does
    b[4] -= shift
    b[11] -= shift
    assert ours(g, lib, leaves)["G13"] == "fails"


def test_g14_counts_the_ions_on_a_road_not_whether_one_stopped(runs, lib, leaves):
    """A whole bundle held at the far end of a street breaks G14: nine ions on a few sites.
    One ion stopping there briefly, while the rest are still on their way, does not -- the
    street never holds more than a couple of the bundle at once (the old G14 refused that
    too, on the theory that any stop is the whole bundle stopping)."""
    _, par, _ = runs["bell_cnot"]
    i = next(k for k, c in enumerate(par["carries"]) if "street" in str(c[1]) and len(c[7]) == 9)
    held = copy.deepcopy(par)
    c = held["carries"][i]
    last = max(c[5]) + c[6] + 1000.0             # every ion taken only after the last has arrived
    c[12] = [last + k for k in range(len(c[12]))]
    c[11] = c[4] + max(c[12])
    assert ours(held, lib, leaves)["G14"] == "fails"
    brief = copy.deepcopy(par)
    c = brief["carries"][i]
    c[12] = [c[12][0] + 50.0] + list(c[12][1:])   # the first ion waits 50 us
    assert ours(brief, lib, leaves)["G14"] == "holds"


# ------------------------------------------------------------------------- floorplans

def test_the_town_is_a_floorplan(lib, leaves):
    fp = parse_floorplan(TOWN.read_text(encoding="utf-8"), "town_d3")
    lib2 = load(fp, leaves)
    A, B = lib[lib.top], lib2[lib2.top]

    def inst(m):
        return {i.name: (i.master, round(i.x, 6), round(i.y, 6)) for i in m.instances}

    def chans(m):
        return {c.name: (c.a, c.b, c.length, [[round(x, 6), round(y, 6)] for x, y in c.points],
                         c.kind, c.latency_us) for c in m.channels}
    assert inst(A) == inst(B)
    assert chans(A) == chans(B)
    assert set(fp.regions.values()) == {"supply", "compute", "memory", "factory", "control",
                                        "traffic"}


def test_the_town_passes_every_floorplan_rule(leaves):
    fp = parse_floorplan(TOWN.read_text(encoding="utf-8"), "town_d3")
    rep = judge_floorplan(load(fp, leaves), leaves, regions=fp.regions)
    assert rep["ok"] and rep["passed"] == [f"F{i}" for i in range(1, 10)], rep


def _edit(text: str, old: str, new: str) -> str:
    assert old in text, old
    return text.replace(old, new)


BROKEN = {
    "F3": lambda t: _edit(t, "place zone1     zone_d3 at 73 10", "place zone1     zone_d3 at 64 10"),
    "F2": lambda t: "\n".join(ln for ln in t.splitlines() if "as door_zone1_in" not in ln),
    "F4": lambda t: t + "road j0_1.n -- j1_2.n via 8 -5, 52 -5, 52 20, 56 20\n",
    "F5": lambda t: _edit(t, "junction j0_0 at 0 0", "junction j0_0 at 0 6"),
    "F6": lambda t: "\n".join(ln for ln in t.splitlines()
                              if not any(f"as {w}_{k}" in ln for w in ("west", "east")
                                         for k in (0, 1))),
    "F7": lambda t: _edit(t, "wires auto bus 62", ""),
    "F9": lambda t: _edit(t, "region memory\nplace zone1", "region compute\nplace zone1"),
}


@pytest.mark.parametrize("rule", sorted(BROKEN))
def test_a_broken_floorplan_fails_the_rule_it_breaks(leaves, rule):
    text = BROKEN[rule](TOWN.read_text(encoding="utf-8"))
    fp = parse_floorplan(text, "broken")
    rep = judge_floorplan(load(fp, leaves), leaves, regions=fp.regions)
    assert rule in rep["failed"], rep["failed"]
    row = next(r for r in rep["rules"] if r["id"] == rule)
    assert row.get("count", 0) >= 1 and row.get("examples")


@pytest.mark.parametrize("text, why", [
    ("floorplan x\nplace a prep_d3 at 0 0\n", "region"),
    ("floorplan x\nregion compute\nplace a nothing at 0 0\n", "not a place"),
    ("floorplan x\nregion moon\n", "region is one of"),
    ("floorplan x\nregion compute\nplace a prep_d3 at 0 0\nroad a.in -- b.out\n", "nothing called b"),
    ("floorplan x\nregion compute\nplace a prep_d3 at 0 0\nroad a -- a.out\n", "name its door"),
    ("floorplan x\nregion compute\nplace a prep_d3 at 0 0\nwire a.in -> a.syn\n", "carries ions"),
])
def test_a_floorplan_that_cannot_be_read_says_why(leaves, text, why):
    with pytest.raises(FloorplanError, match=why):
        load(parse_floorplan(text, "x"), leaves)


# ------------------------------------------------------------------------- parallel execution

def test_every_example_passes_every_rule_in_parallel_and_signs_off(runs, lib, leaves):
    for stem, (alg, par, seq) in runs.items():
        assert par["policy"] == "parallel" and not par["refused"], stem
        rep = judge_schedule(lib, leaves, par)
        assert not rep["failed"] and not rep["unchecked"], (stem, rep["failed"])
        assert sign_off(alg, par, lib, leaves)["passed"], stem
        assert par["makespan_us"] <= seq["makespan_us"] + 1e-6, stem


def test_the_sequential_policy_is_the_one_the_place_model_measured(runs):
    for stem, (_, _, seq) in runs.items():
        assert seq["policy"] == "sequential"
        assert round(seq["makespan_us"] / 1000, 1) == SEQUENTIAL_MS[stem], stem


def test_the_factory_works_while_the_computation_runs(runs, lib):
    """magic_teleport: the factory's production overlaps the other blocks' operations."""
    alg, par, seq = runs["magic_teleport"]
    fam = {p: lib[v[0]].family for p, v in par["leaves"].items()}
    produce = [e for e in par["events"] if fam[e[3]] == "factory" and e[4] == "produce"]
    others = [e for e in par["events"] if fam[e[3]] in ("prep", "se", "operation", "surgery",
                                                        "readout") and e[6] != produce[0][6]]
    assert produce and any(o[1] < p[2] and p[1] < o[2] for p in produce for o in others)
    assert par["makespan_us"] < 0.7 * seq["makespan_us"]
    t = traffic(par, lib)
    assert t["peak_parallel_ops"] >= 2
    assert t["ion_sites"] == traffic(seq, lib)["ion_sites"]      # the same roads, sooner
