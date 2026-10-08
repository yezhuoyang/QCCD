"""MQT IonShuttler (Schoenberger et al.: the SAT tool of arXiv 2311.03454, the cycle heuristic
of arXiv 2402.14065) in the timed checker.

Every dumped schedule must replay, pass the core checker (strict durations) and the paper's
own rules, and measure exactly the number the tool printed.  Every rule the module adds must
catch a fault planted for it, in a small schedule written in the tools' own dump format.
"""

from __future__ import annotations

import copy
import dataclasses
import gzip
import json
import shutil
from pathlib import Path

import pytest

from qccd.repro import ionshuttler as ish
from qccd.repro.timed import check

ROOT = Path(__file__).resolve().parents[1] / "Reproduce" / "schoenberger2024"
ART = ROOT / "artifact"
FILES = sorted(ART.glob("*.schedule.json.gz"))


def failed(rep) -> set[str]:
    return {k for k, v in rep.checks.items() if v == "failed"}


# ----------------------------------------------------------------- the artifact's schedules


def test_every_dump_is_here():
    names = [f.name.split(".")[0] for f in FILES]
    paper_sat = [n for n in names if n.startswith(("sat_L", "sat_R2215"))]
    paper_heur = [n for n in names if n.startswith("heur_")]
    diag = [n for n in names if n.startswith(("heurNoGS_", "sat_R2251T"))]
    assert (len(paper_sat), len(paper_heur), len(diag)) == (40, 15, 20)
    assert (ART / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name.split(".")[0])
def test_each_schedule_replays_to_the_printed_number_and_passes_every_rule(path):
    s = ish.import_ionshuttler(path, circuits_dir=ROOT / "circuits")
    rep = ish.check_ionshuttler(s)
    assert rep.ok, rep.summary()
    tool = s.source["tool"]
    must = {"structure", "positions", "capacity", "ion_serial", "durations", "layout", "junctions",
            "path", "nodes", "pz", "gates", "sequence"}
    if tool == ish.SAT:
        must |= {"junction_mutex", "segment_mutex", "sequence_code"}
    assert all(rep.checks[c] == "passed" for c in must), rep.checks
    assert rep.metrics["makespan_steps"] == s.claims["printed"]


def test_the_printed_numbers_come_from_the_console():
    bundle = json.loads(gzip.decompress((ART / "runs.json.gz").read_bytes()))["runs"]
    for f in FILES:
        rid = f.name.split(".")[0]
        d = json.loads(gzip.decompress(f.read_bytes()))
        tool = ish._tool_of(d)
        assert ish._stdout_number(bundle[rid]["stdout"], tool) == d["metrics"][ish.PRINTED[tool]], rid


def test_results_json_regenerates_from_the_artifact_folder(tmp_path):
    shutil.copytree(ART, tmp_path / "artifact")
    shutil.copytree(ROOT / "circuits", tmp_path / "circuits")
    res = ish.reproduce(tmp_path, log=lambda *_: None)
    assert res["summary"]["replayed"] == res["summary"]["ok"] == res["summary"]["equal_to_printed"] == 75
    rows = res["summary"]["paper_rows"]
    # P1 Table I: three lattice rows exactly, the racetrack row not
    assert {k: (r["ours"], r["verdict"]) for k, r in rows.items() if k.startswith("sat")} == {
        "sat_L4411_fra12": (18.5, "exact"), "sat_L3311_fra6": (10.9, "exact"),
        "sat_L4411_fra6": (12.5, "exact"), "sat_R2215_fra6": (13.2, "differs")}
    # P2 Table I is not reproduced; the authors' own log is
    assert rows["heur_L4411_fra12"]["ours"] == 20.8 and rows["heur_L4411_fra12"]["verdict"] == "differs"
    h = next(c for c in res["configs"] if c["config"] == "heur_H6211_fra8")
    assert h["authors_log"]["verdict"] == "exact" and h["paper"]["verdict"] == "differs"
    nr = res["not_replayed"][0]
    assert (nr["proved_unsat_up_to"], nr["undecided_at"]) == (19, 20)
    shipped = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))
    assert shipped["summary"] == res["summary"]
    assert shipped["ours"]["summary"] == res["ours"]["summary"]
    for f in (ROOT / "ours").glob("*.gz"):                 # our schedules regenerate byte for byte
        assert (tmp_path / "ours" / f.name).read_bytes() == f.read_bytes(), f.name
    assert [c["printed"] for c in shipped["configs"]] == [json.loads(json.dumps(c["printed"])) for c in res["configs"]]


def test_a_fault_planted_in_a_real_schedule_is_caught():
    d = json.loads(gzip.decompress((ART / "sat_L4411_fra12_seed0.schedule.json.gz").read_bytes()))
    d["sequence"] = d["sequence"][::-1]
    rep = ish.check_ionshuttler(ish.import_ionshuttler(d, name="reversed"))
    assert failed(rep) == {"sequence"}


# ------------------------------------------------- small schedules in the tools' dump format

#: a racetrack 2 2 1 2: four junctions, two minor nodes (0,1 and 1,1), the PZ as P1 draws it
#: (e6 = e_out from the exit junction 1,2; e7 = e_in, capacity 2, back to the entry junction 1,0)
SAT_EDGES = {"e0": ("0,0", "0,1"), "e1": ("0,1", "0,2"), "e2": ("0,0", "1,0"), "e3": ("0,2", "1,2"),
             "e4": ("1,0", "1,1"), "e5": ("1,1", "1,2"), "e6": ("1,2", "2,3"), "e7": ("1,0", "2,3")}


def sat_dump(initial: dict[int, str], steps: list[list[tuple]], gates: dict[int, list[int]],
             sequence: list[list[int]]) -> dict:
    """``steps[t]`` = the moves into state t+1 as (chain, from, to, [nodes]); ``gates`` =
    state -> the chains of the element executed there."""
    types = {"0,1": "trap_node", "1,1": "trap_node", "2,3": "processing_zone_node"}
    nodes = sorted({n for e in SAT_EDGES.values() for n in e})
    et = {"e6": ("exit", 1), "e7": ("entry", 2)}
    out_steps = [{"t": 0, "moves": [], "gates": [], "gate_seq_index": []}]
    for t, ms in enumerate(steps, start=1):
        out_steps.append({"t": t, "moves": [{"ion": c, "from": a, "to": b, "through_nodes": list(ns)}
                                            for c, a, b, ns in ms], "gates": [], "gate_seq_index": []})
    seq_i = 0
    for t in sorted(gates):
        out_steps[t]["gates"].append(gates[t])
        out_steps[t]["gate_seq_index"].append(seq_i)
        seq_i += 1
    return {
        "metrics": {ish.PRINTED[ish.SAT]: len(out_steps)},
        "graph": {"nodes": [{"id": n, "node_type": types.get(n, "junction_node")} for n in nodes],
                  "edges": [{"id": e, "nodes": list(ns), "edge_type": et.get(e, ("trap",))[0],
                             "capacity": et.get(e, ("trap", 1))[1]} for e, ns in SAT_EDGES.items()],
                  "junction_nodes": ["0,0", "0,2", "1,0", "1,2"],
                  "exit_edge(e_out)": "e6", "entry_edge(e_in, gate site)": "e7"},
        "initial": {str(c): e for c, e in initial.items()},
        "qubit_to_ion": {str(c): str(c) for c in initial},
        "sequence": sequence, "steps": out_steps,
    }


#: two chains: c1 slides along the top (through the minor node 0,1) and round the corner;
#: each chain goes out through e_out, is processed alone on e_in and comes back
SAT_OK = dict(
    initial={0: "e5", 1: "e0"},
    steps=[[(0, "e5", "e6", ["1,2"]), (1, "e0", "e3", ["0,1", "0,2"])],       # -> state 1
           [(0, "e6", "e7", ["2,3"]), (1, "e3", "e5", ["1,2"])],              # -> state 2
           [(0, "e7", "e4", ["1,0"]), (1, "e5", "e6", ["1,2"])],              # -> state 3
           [(1, "e6", "e7", ["2,3"])],                                       # -> state 4
           [(1, "e7", "e2", ["1,0"])]],                                      # -> state 5
    gates={2: [0], 4: [1]}, sequence=[[0], [1]])


def sat(**change) -> "ish.Report":
    spec = {**copy.deepcopy(SAT_OK), **change}
    return ish.check_ionshuttler(ish.import_ionshuttler(sat_dump(**spec), name="tiny"))


def test_the_small_sat_schedule_passes_and_counts_states():
    s = ish.import_ionshuttler(sat_dump(**SAT_OK), name="tiny")
    rep = ish.check_ionshuttler(s)
    assert rep.ok, rep.summary()
    # six states, five transitions: state t is the unit [t, t+1), so the makespan is 6
    assert rep.metrics["makespan_steps"] == 6 == s.claims["printed"]
    assert min(e.t0 for e in s.events) == 1.0          # state 0 has no incoming transition
    assert rep.metrics["ionshuttler"]["multi_node_moves"] == 1


def test_a_train_into_a_vacated_edge_is_legal():
    # c1 enters e5 in the step c0 leaves it, through a different node
    rep = sat(initial={0: "e5", 1: "e4"},
              steps=[[(0, "e5", "e6", ["1,2"]), (1, "e4", "e5", ["1,1"])],
                     [(0, "e6", "e7", ["2,3"])],
                     [(0, "e7", "e2", ["1,0"]), (1, "e5", "e6", ["1,2"])],
                     [(1, "e6", "e7", ["2,3"])],
                     [(1, "e7", "e4", ["1,0"])]],
              gates={2: [0], 4: [1]})
    assert rep.ok, rep.summary()


def test_path_an_edge_passed_must_be_empty_at_the_previous_state():
    spec = copy.deepcopy(SAT_OK)
    spec["initial"][2] = "e1"                          # c2 sits on the edge c1 slides through
    rep = sat(**spec)
    assert "path" in failed(rep), rep.checks
    assert rep.failed("path")[0].message.startswith("c1 passes e1 in step 1")


def test_junctions_a_move_crosses_at_most_one():
    spec = copy.deepcopy(SAT_OK)
    spec["initial"][1] = "e2"
    spec["steps"][0][1] = (1, "e2", "e3", ["0,0", "0,1", "0,2"])   # round two corners at once
    assert failed(sat(**spec)) == {"junctions"}


def test_nodes_one_chain_per_node_per_transition_in_the_sat():
    # c1 follows c0 through the junction 1,2 in the same step: legal capacity, illegal node
    spec = copy.deepcopy(SAT_OK)
    spec["initial"][1] = "e3"
    spec["steps"][0][1] = (1, "e3", "e5", ["1,2"])
    spec["steps"][1] = [(0, "e6", "e7", ["2,3"])]
    rep = sat(**spec)
    assert failed(rep) == {"nodes", "junction_mutex", "segment_mutex"}, rep.checks


def test_pz_a_chain_on_e_out_must_be_on_e_in_next():
    # c0 waits a step on e_out
    rep = sat(steps=[[(0, "e5", "e6", ["1,2"]), (1, "e0", "e3", ["0,1", "0,2"])],
                     [(1, "e3", "e5", ["1,2"])],
                     [(0, "e6", "e7", ["2,3"]), (1, "e5", "e4", ["1,1"])],
                     [(0, "e7", "e2", ["1,0"])],
                     [(1, "e4", "e3", ["1,1", "1,2"])]],
              gates={3: [0]}, sequence=[[0]])
    assert failed(rep) == {"pz"}, rep.checks
    assert rep.failed("pz")[0].message == "c0 is on e_out e6 at state 1 and not on e_in at state 2"


def test_pz_e_in_is_entered_only_from_e_out_and_the_pz_is_empty_at_the_end():
    rep = sat(initial={0: "e4"}, steps=[[(0, "e4", "e7", ["1,0"])]], gates={1: [0]}, sequence=[[0]])
    msgs = [v.message for v in rep.failed("pz")]
    assert any("not a direction of the one-way processing zone" in m for m in msgs)
    assert any("must be empty in the last state" in m for m in msgs)


def test_gates_only_at_the_gate_site():
    s = ish.import_ionshuttler(sat_dump(**SAT_OK), name="tiny")
    # an element executed where c0 actually stands at state 1 (e_out), not on e_in
    g = next(e for e in s.events if e.kind == "gate1")
    s.events[s.events.index(g)] = dataclasses.replace(g, t0=2.0, t1=2.0, at="e6", meta={**g.meta, "state": 1})
    s.events.sort(key=lambda e: (e.t0, e.kind not in ("gate", "gate1"), e.id))
    s.events[:] = [dataclasses.replace(e, id=i) for i, e in enumerate(s.events)]
    rep = ish.check_ionshuttler(s)
    assert "gates" in failed(rep) and rep.checks["positions"] == "passed"


def test_sequence_the_sat_order_is_fixed():
    assert failed(sat(sequence=[[1], [0]])) == {"sequence"}


def test_sequence_code_a_one_chain_element_is_alone_on_e_in():
    # c0 waits on e_in (capacity 2) while c1 joins it and is processed: P1 allows it, the code not
    rep = sat(steps=[[(0, "e5", "e6", ["1,2"]), (1, "e0", "e3", ["0,1", "0,2"])],
                     [(0, "e6", "e7", ["2,3"]), (1, "e3", "e5", ["1,2"])],
                     [(1, "e5", "e6", ["1,2"])],
                     [(1, "e6", "e7", ["2,3"])],
                     [(0, "e7", "e4", ["1,0"])],
                     [(1, "e7", "e2", ["1,0"])]])
    assert failed(rep) == {"sequence_code"}, rep.checks


def test_layout_a_chain_moves_once_per_step():
    spec = copy.deepcopy(SAT_OK)
    spec["steps"][0][1] = (1, "e0", "e1", ["0,1"])
    spec["steps"][0].append((1, "e1", "e3", ["0,2"]))              # the slide as two moves
    assert "layout" in failed(sat(**spec))


def test_capacity_is_the_core_checkers():
    # c1 moves into e5 while c0 stays there
    rep = sat(initial={0: "e5", 1: "e3"}, steps=[[(1, "e3", "e5", ["1,2"])]], gates={}, sequence=[])
    assert failed(rep) == {"capacity"}, rep.checks


# -------------------------------------------------------------------- the heuristic's format

#: a 2 x 2 grid of junctions, a one-edge exit path to the PZ node, the parking edge (the gate
#: site, capacity 3) and a one-edge entry path back
HEUR_EDGES = {"e0": ("0,0", "0,1"), "e1": ("0,0", "1,0"), "e2": ("0,1", "1,1"), "e3": ("1,0", "1,1"),
              "e4": ("1,1", "2,2.0"), "e5": ("1,0", "2,2.0"), "e6": ("2,2.0", "3,2")}


def heur_dump(initial: dict[int, str], iters: list[list[tuple]], gates: dict[int, list]) -> dict:
    """``iters[t]`` = iteration t's moves; ``gates[t]`` = (qubits, step_of_gate, duration,
    completed)."""
    types = {"2,2.0": "processing_zone_node", "3,2": "parking_node"}
    et = {"e4": ("exit", 1), "e5": ("first_entry_connection", 1), "e6": ("parking_edge", 3)}
    nodes = sorted({n for e in HEUR_EDGES.values() for n in e})
    steps = []
    for t, ms in enumerate(iters):
        st = {"t": t, "moves": [{"ion": c, "from": a, "to": b, "through_nodes": list(ns), "phase": "rotate"}
                                for c, a, b, ns in ms], "gates": [], "gate_detail": None}
        if t in gates:
            qs, k, d, done = gates[t]
            st["gates"] = [qs]
            st["gate_detail"] = {"qubits": qs, "op": ["h", []], "step_of_gate": k, "duration": d,
                                 "completed_this_step": done}
        steps.append(st)
    return {
        "metrics": {ish.PRINTED[ish.HEURISTIC]: len(steps) - 1},
        "config": {"time_1qubit_gate": 1, "time_2qubit_gate": 3},
        "graph": {"nodes": [{"id": n, "node_type": types.get(n, "junction_node")} for n in nodes],
                  "edges": [{"id": e, "nodes": list(ns), "edge_type": et.get(e, ("trap",))[0],
                             "capacity": et.get(e, ("trap", 1))[1]} for e, ns in HEUR_EDGES.items()],
                  "junction_nodes(MemoryZone.junction_nodes)": ["0,0", "0,1", "1,0", "1,1"],
                  "path_to_pz(exit)": ["e4"], "path_from_pz(entry)": ["e5"],
                  "parking_edge(gate site)": "e6"},
        "initial": {str(c): e for c, e in initial.items()},
        "qubit_to_ion": {str(c): str(c) for c in initial},
        "steps": steps,
    }


#: iteration 2 is the code's PZ exchange: c1 enters the parking edge while c0 leaves it, both
#: through the PZ node
HEUR_OK = dict(
    initial={0: "e2", 1: "e3"},
    iters=[[(0, "e2", "e4", ["1,1"])],
           [(0, "e4", "e6", ["2,2.0"]), (1, "e3", "e4", ["1,1"])],
           [(1, "e4", "e6", ["2,2.0"]), (0, "e6", "e5", ["2,2.0"])]],
    gates={1: ([0], 1, 1, True), 2: ([1], 1, 1, True)})
CIRCUIT = [("h", (0,)), ("h", (1,))]


def heur(sched_change=None, circuit=CIRCUIT, **change) -> "ish.Report":
    spec = {**copy.deepcopy(HEUR_OK), **change}
    return ish.check_ionshuttler(ish.import_ionshuttler(heur_dump(**spec), name="tiny-h", circuit=circuit))


def test_the_small_heuristic_schedule_passes_and_counts_from_zero():
    s = ish.import_ionshuttler(heur_dump(**HEUR_OK), name="tiny-h", circuit=CIRCUIT)
    rep = ish.check_ionshuttler(s)
    assert rep.ok, rep.summary()
    # three iterations, printed 2: iteration t is the unit [t-1, t)
    assert rep.metrics["makespan_steps"] == 2 == s.claims["printed"]
    assert min(e.t0 for e in s.events) == -1.0
    assert rep.metrics["ionshuttler"]["pz_node_shared"]["steps"] == 1


def test_the_pz_exchange_is_why_the_heuristic_profile_shares_nodes():
    s = ish.import_ionshuttler(heur_dump(**HEUR_OK), name="tiny-h", circuit=CIRCUIT)
    strict_nodes = dataclasses.replace(ish.IONSHUTTLER_HEURISTIC, junction_mutex=True, segment_mutex=True)
    rep = check(s, strict_nodes, duration=ish.ionshuttler_duration, strict=True)
    assert {"junction_mutex", "segment_mutex"} <= failed(rep)
    # our rule lets exactly this through, and counts it
    checks, _, info = ish.ionshuttler_rules(s, ish.HEURISTIC)
    assert checks["nodes"] == "passed" and info["pz_node_shared"] == {**info["pz_node_shared"], "steps": 1, "max_chains": 2}


def test_nodes_the_heuristic_exempts_only_its_pz_node():
    # c1 follows c0 through the junction 1,1 in one step: the core's mutexes are off for the
    # heuristic, so only our rule sees it
    rep = heur(initial={0: "e2", 1: "e3"},
               iters=[[(0, "e2", "e4", ["1,1"]), (1, "e3", "e2", ["1,1"])],
                      [(0, "e4", "e6", ["2,2.0"])],
                      [(1, "e2", "e4", ["1,1"])],
                      [(1, "e4", "e6", ["2,2.0"])]],
               gates={1: ([0], 1, 1, True), 3: ([1], 1, 1, True)})
    assert failed(rep) == {"nodes"}, rep.checks


def test_pz_the_heuristic_pz_is_one_way():
    back = heur(iters=[[(0, "e2", "e4", ["1,1"])],
                       [(0, "e4", "e2", ["1,1"])],
                       [(0, "e2", "e4", ["1,1"])],
                       [(0, "e4", "e6", ["2,2.0"]), (1, "e3", "e4", ["1,1"])],
                       [(1, "e4", "e6", ["2,2.0"]), (0, "e6", "e5", ["2,2.0"])]],
                gates={3: ([0], 1, 1, True), 4: ([1], 1, 1, True)})
    assert failed(back) == {"pz"}
    side = heur(iters=[[(0, "e2", "e4", ["1,1"]), (1, "e3", "e5", ["1,0"])]], gates={})
    assert "pz" in failed(side)
    start = heur(initial={0: "e6", 1: "e3"}, iters=[[(1, "e3", "e4", ["1,1"])], [(1, "e4", "e6", ["2,2.0"])]],
                 gates={0: ([0], 1, 1, True), 1: ([1], 1, 1, True)})
    assert failed(start) == {"pz"}


def test_gates_a_one_qubit_gate_takes_one_iteration():
    rep = heur(gates={1: ([0], 1, 1, False), 2: ([0], 2, 1, True)},
               iters=[[(0, "e2", "e4", ["1,1"])], [(0, "e4", "e6", ["2,2.0"])], []])
    assert "gates" in failed(rep)
    assert "its time is 1 consecutive step(s)" in rep.failed("gates")[0].message


def test_sequence_every_heuristic_gate_runs_exactly_once():
    rep = heur(iters=HEUR_OK["iters"][:2], gates={1: ([0], 1, 1, True)})
    assert failed(rep) == {"sequence"}, rep.checks
    rep = heur(circuit=[("h", (0,)), ("h", (0,))])
    assert failed(rep) == {"sequence"}


def test_layout_a_retimed_move_is_caught():
    s = ish.import_ionshuttler(heur_dump(**HEUR_OK), name="tiny-h", circuit=CIRCUIT)
    m = next(e for e in s.events if e.kind == "merge" and e.meta.get("role") == "arrive")
    s.events[s.events.index(m)] = dataclasses.replace(m, t0=m.t0 - 0.1, t1=m.t1 - 0.1)
    assert "layout" in failed(ish.check_ionshuttler(s))


def test_a_saved_schedule_reloads_and_rechecks_from_the_file_alone(tmp_path):
    from qccd.repro.timed import TimedSchedule

    s = ish.import_ionshuttler(FILES[0], circuits_dir=ROOT / "circuits")
    again = TimedSchedule.load(s.save(tmp_path / "x.schedule.json"))
    rep = ish.check_ionshuttler(again)
    assert rep.ok and rep.metrics["makespan_steps"] == s.claims["printed"]


# -------------------------------------------- ours, under the heuristic's model, same placements

OURS = sorted((ROOT / "ours").glob("*_ours.schedule.json.gz"))


@pytest.mark.parametrize("path", OURS, ids=lambda p: p.name.split(".")[0])
def test_our_schedules_pass_the_same_checks_on_the_same_placements_and_are_optimal(path):
    rid = path.name.split(".")[0][: -len("_ours")]
    theirs = json.loads(gzip.decompress((ART / f"{rid}.schedule.json.gz").read_bytes()))
    mine = json.loads(gzip.decompress(path.read_bytes()))
    assert mine["initial"] == theirs["initial"] and mine["graph"] == theirs["graph"]
    rep = ish.check_ionshuttler(ish.import_ionshuttler(path, circuits_dir=ROOT / "circuits"))
    assert rep.ok, rep.summary()
    n = rep.metrics["makespan_steps"]
    assert n == mine["metrics"][ish.PRINTED[ish.HEURISTIC]] == ish.lower_bound(theirs)
    assert n < theirs["metrics"][ish.PRINTED[ish.HEURISTIC]]


def test_our_counts():
    assert len(OURS) == 15
    res = json.loads((ROOT / "results.json").read_text(encoding="utf-8"))["ours"]["summary"]
    assert res["heur_L4411_fra12"]["ours_mean"] == 13.3 and res["heur_L4411_fra12"]["heuristic_mean"] == 21.6
    assert res["heur_H6211_fra8"]["ours_mean"] == 8.0 and res["heur_H6211_fra8"]["heuristic_mean"] == 14.6


def test_our_scheduler_is_seeded_and_reproduces_the_shipped_schedule():
    d = json.loads(gzip.decompress((ART / "heur_L4411_fra12_seed3.schedule.json.gz").read_bytes()))
    dump, st = ish.schedule_ours(d)
    assert st["optimal"] and st["best"] == 13
    shipped = json.loads(gzip.decompress((ROOT / "ours" / "heur_L4411_fra12_seed3_ours.schedule.json.gz").read_bytes()))
    assert dump == shipped


def test_a_fault_planted_in_our_schedule_is_caught():
    mine = json.loads(gzip.decompress(OURS[-1].read_bytes()))
    last = max(i for i, s in enumerate(mine["steps"]) if s["gates"])
    mine["steps"][last]["gates"], mine["steps"][last]["gate_detail"] = [], None
    rep = ish.check_ionshuttler(ish.import_ionshuttler(mine, name="planted", circuits_dir=ROOT / "circuits"))
    assert failed(rep) == {"sequence"}
