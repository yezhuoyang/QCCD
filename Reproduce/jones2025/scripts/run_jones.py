"""Driver for scottjones03/PartIIProject (Jones & Murali, arXiv 2510.23519).

Runs ONE configuration with the artifact's own code and dumps the artifact's own
internal schedule. Nothing in the artifact's algorithms is changed: this file only
(a) calls the artifact's entry points the way its authors did, and (b) wraps a few
functions from the outside to *observe* their inputs/outputs:

  * src.simulator.qccd_circuit.ionRouting               -> device + initial layout (at entry),
                                                           allOps + barriers (at exit)
  * src.simulator.qccd_circuit.paralleliseOperationsWithBarriers -> the {start time: ParallelOperation} map
  * Operation.run / QubitOperation.run                  -> chain order of the touched traps,
                                                           junction/segment occupancy after every op
                                                           (ops run during routing in allOps order)

Two commits are run.  d235a22 is the code behind the paper's Table 2.  afbd1b4 (2026-10-22) is
the authors' fix after our gate-order note: process_circuit now hands the parser's layer
barriers to ionRouting, and gained `pass_barriers=False`, which schedules by the components'
happens-before relation alone.  `--pass-barriers false` asks for that; the default call is the
d235a22 call byte for byte, and at d235a22 `layerBarriers` is never passed.

Recipes
  process_circuit : src.simulator.qccd_circuit.process_circuit(d, c, gate_improvements=[], num_shots=1)
                    (the driver used by experiments/trap_capacity_experiments.py; gate_improvements=[]
                    skips the stim sampling / decoding loop entirely; it does not touch the schedule)
  notebook        : the surface_code_sims.ipynb cell recipe:
                    processCircuitAugmentedGrid(rows, cols, trapCapacity, padding) -> refreshGraph
                    -> ionRouting -> paralleliseOperationsWithBarriers -> print(...)
"""
import argparse
import json
import logging
import os
import sys
import time


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--name", required=True)
    ap.add_argument("--recipe", choices=["process_circuit", "notebook"], required=True)
    ap.add_argument("--code", choices=["rotated", "repetition"], default="rotated")
    ap.add_argument("--distance", type=int, required=True)
    ap.add_argument("--capacity", type=int, required=True)
    ap.add_argument("--rows", type=int, default=None)
    ap.add_argument("--cols", type=int, default=None)
    ap.add_argument("--padding", type=int, default=None)
    ap.add_argument("--traps", type=int, default=None, help="notebook recipe: processCircuitNetworkedGrid(traps=N) (switch)")
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--topology-note", default="")
    ap.add_argument("--command-line", default="")
    ap.add_argument("--commit", default="")
    # ADAPTATION (afbd1b4): "default" = call process_circuit without the keyword
    ap.add_argument("--pass-barriers", choices=["default", "false"], default="default")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    out = os.path.abspath(args.out)
    os.makedirs(out, exist_ok=True)
    sys.path.insert(0, repo)
    os.chdir(out)  # process_circuit writes process_log.txt into the cwd

    import numpy as np
    np.random.seed(args.seed)  # the compile path draws no random numbers; set anyway (recorded)

    import src.simulator.qccd_circuit as qc
    from src.utils.qccd_nodes import Trap, Junction, Crossing, Ion
    from src.utils import qccd_operations as qo
    from src.utils import qccd_operations_on_qubits as qq

    mplog = qc.get_logger()
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(logging.Formatter('%(processName)s - %(levelname)s - %(message)s'))
    mplog.addHandler(sh)

    cap = {}

    # ---------- observers ----------
    def comp_ref(c):
        if isinstance(c, Trap):
            return {"type": "trap", "id": c.idx}
        if isinstance(c, Junction):
            return {"type": "junction", "id": c.idx}
        if isinstance(c, Crossing):
            return {"type": "segment", "id": c.idx}
        if isinstance(c, Ion):
            return {"type": "ion", "id": c.idx}
        return {"type": type(c).__name__, "id": getattr(c, "idx", None)}

    def state_of(comps):
        traps, juncs, segs = {}, {}, {}
        for c in comps:
            if isinstance(c, Trap):
                traps[str(c.idx)] = [i.idx for i in c.ions]
            elif isinstance(c, Junction):
                juncs[str(c.idx)] = [i.idx for i in c.ions]
            elif isinstance(c, Crossing):
                segs[str(c.idx)] = c.ion.idx if c.ion is not None else None
        return {"traps": traps, "junctions": juncs, "segments": segs}

    run_log = {}       # id(op) -> dict(pre, post, seq)
    depth = [0]
    seq = [0]

    def observed(orig):
        def run(self, *a, **k):
            top = depth[0] == 0
            pre = state_of(self.involvedComponents) if top else None
            if top and isinstance(self, qo.Merge):
                pre_ion = self._crossing.ion.idx if self._crossing.ion is not None else None
            else:
                pre_ion = None
            depth[0] += 1
            try:
                return orig(self, *a, **k)
            finally:
                depth[0] -= 1
                if top:
                    run_log[id(self)] = {"pre": pre, "post": state_of(self.involvedComponents),
                                         "seq": seq[0], "merge_ion": pre_ion}
                    seq[0] += 1
        return run

    qo.Operation.run = observed(qo.Operation.run)
    qq.QubitOperation.run = observed(qq.QubitOperation.run)

    def snapshot_device(arch):
        traps = []
        for t in arch._manipulationTraps:
            traps.append({
                "id": t.idx, "label": t.label, "class": type(t).__name__,
                "pos": list(map(float, t.pos)),
                "capacity": t.capacity, "desired_capacity": t.desiredCapacity,
                "isHorizontal": bool(t._isHorizontal), "ion_spacing": t._spacing,
                "chain_axis": "x" if t._isHorizontal else "y",
                "chain_order_note": "ions listed in increasing coordinate along chain_axis (index 0 = low end)",
            })
        juncs = []
        deg = {}
        for c in arch._crossings:
            for n in c.connection:
                deg[n.idx] = deg.get(n.idx, 0) + 1
        for j in arch._junctions:
            juncs.append({"id": j.idx, "label": j.label, "pos": list(map(float, j.pos)),
                          "capacity": j.capacity, "degree": deg.get(j.idx, 0)})
        segs = []
        for c in arch._crossings:
            s, t = c.connection
            ends = []
            for me, other in ((s, t), (t, s)):
                e = {"node_type": "trap" if isinstance(me, Trap) else "junction", "id": me.idx}
                if isinstance(me, Trap):
                    ax = 0 if me._isHorizontal else 1
                    if other.pos[ax] < me.pos[ax]:
                        e["trap_side"] = "low"
                    elif other.pos[ax] > me.pos[ax]:
                        e["trap_side"] = "high"
                    else:
                        e["trap_side"] = "tie"
                ends.append(e)
            segs.append({"id": c.idx, "label": c.label, "source": ends[0], "target": ends[1]})
        return {"traps": traps, "junctions": juncs, "segments": segs}

    real_ionRouting = qc.ionRouting

    def ionRouting(arch, instructions, trapCapacity, layerBarriers=None):
        # ADAPTATION (afbd1b4): process_circuit now passes layerBarriers=...; pass it through
        # unchanged. At d235a22 it is never passed, and the real call stays 3-argument.
        cap["device"] = snapshot_device(arch)
        cap["initial_layout"] = {str(t.idx): [i.idx for i in t.ions] for t in arch._manipulationTraps if t.ions}
        cap["ion_labels"] = {str(i.idx): i.label for i in arch.ions.values()}
        cap["instructions"] = list(instructions)
        cap["trapCapacity_arg"] = trapCapacity
        cap["layerBarriers_arg"] = list(layerBarriers) if layerBarriers is not None else None
        if layerBarriers is None:
            allOps, barriers = real_ionRouting(arch, instructions, trapCapacity)
        else:
            allOps, barriers = real_ionRouting(arch, instructions, trapCapacity, layerBarriers=layerBarriers)
        cap["allOps"] = list(allOps)
        cap["barriers"] = list(barriers)
        return allOps, barriers

    qc.ionRouting = ionRouting

    real_par = qc.paralleliseOperationsWithBarriers

    def paralleliseOperationsWithBarriers(ops, barriers):
        m = real_par(ops, barriers)
        cap["parallelOpsMap"] = m
        return m

    qc.paralleliseOperationsWithBarriers = paralleliseOperationsWithBarriers

    # ADAPTATION (afbd1b4): with pass_barriers=False process_circuit calls
    # paralleliseOperations(allOps) directly (the name in qccd_circuit's namespace; the barrier
    # version calls its own module's copy, which this does not touch).
    real_par_plain = getattr(qc, "paralleliseOperations", None)

    def paralleliseOperations(ops):
        m = real_par_plain(ops)
        cap["parallelOpsMap"] = m
        cap["scheduler"] = "paralleliseOperations (no barriers)"
        return m

    if real_par_plain is not None:
        qc.paralleliseOperations = paralleliseOperations

    # ---------- run ----------
    d, c = args.distance, args.capacity
    t_start = time.time()
    results = None
    printed = []

    def p(s):
        print(s)
        printed.append(s)

    if args.recipe == "process_circuit":
        if args.code != "rotated":
            raise SystemExit("process_circuit only builds the rotated surface code")
        circuit_holder = {}
        real_generated = qc.QCCDCircuit.generated.__func__

        @classmethod
        def generated(cls, *a, **k):
            circ = real_generated(cls, *a, **k)
            circuit_holder["c"] = circ
            circuit_holder["args"] = (a, k)
            return circ
        qc.QCCDCircuit.generated = generated
        # ADAPTATION (afbd1b4): pass_barriers is a keyword of process_circuit; pass it only when
        # asked for False, so the default call is byte-for-byte the call made at d235a22.
        if args.pass_barriers == "false":
            results = qc.process_circuit(d, c, [], 1, pass_barriers=False)
        else:
            results = qc.process_circuit(d, c, [], 1)
        circuit = circuit_holder["c"]
        gen_args = circuit_holder["args"]
        print(results)
        printed.append(repr(results))
    else:
        noise = 0.01
        if args.code == "repetition":
            circuit = qc.QCCDCircuit()
            for i in range(2 * d - 1):
                circuit += qc.QCCDCircuit(f"QUBIT_COORDS({i},0) {i}")
            circuit += qc.QCCDCircuit.generated(
                "repetition_code:memory", rounds=1, distance=d,
                after_clifford_depolarization=noise, after_reset_flip_probability=noise,
                before_measure_flip_probability=noise, before_round_data_depolarization=noise)
            gen_args = (("repetition_code:memory",), dict(rounds=1, distance=d, noise_args_all=noise,
                                                          prepended_QUBIT_COORDS=f"(i,0) for i<{2*d-1}"))
        else:
            circuit = qc.QCCDCircuit.generated(
                "surface_code:rotated_memory_z", rounds=1, distance=d,
                after_clifford_depolarization=noise, after_reset_flip_probability=noise,
                before_measure_flip_probability=noise, before_round_data_depolarization=noise)
            gen_args = (("surface_code:rotated_memory_z",), dict(rounds=1, distance=d, noise_args_all=noise))
        if args.traps:
            arch, (instructions, _) = circuit.processCircuitNetworkedGrid(traps=args.traps, trapCapacity=c)
        else:
            arch, (instructions, _) = circuit.processCircuitAugmentedGrid(
                rows=args.rows, cols=args.cols, trapCapacity=c, padding=args.padding)
        arch.refreshGraph()
        allOps, barriers = qc.ionRouting(arch, instructions, trapCapacity=c)
        parallelOpsMap = qc.paralleliseOperationsWithBarriers(allOps, barriers)
    wall = time.time() - t_start

    allOps = cap["allOps"]
    instructions = cap["instructions"]
    pmap = cap["parallelOpsMap"]
    numSplits = sum(1 for x in allOps if isinstance(x, qo.Split))
    numGateSwaps = sum(1 for x in allOps if isinstance(x, qq.GateSwap))
    # the notebook cells' own print lines (surface_code_sims.ipynb cells 7/8/15)
    p(f"total number of routing operations: {len(allOps)-len(instructions)}")
    p(f"total number of qubit operations: {len(instructions)}")
    p(f"total number of operations: {len(allOps)}")
    p(f"time for operations: {max(pmap.keys())*1e6}")
    p(f"num gate swaps: {numGateSwaps}")
    p(f"num splits: {numSplits}")

    # ---------- events in the tool's order ----------
    op_index = {id(o): k for k, o in enumerate(allOps)}
    # barrier block of each op
    bl = [0] + list(cap["barriers"]) + [len(allOps)]
    block_of = {}
    for b, (s, e) in enumerate(zip(bl[:-1], bl[1:])):
        for k in range(s, e):
            block_of.setdefault(k, b)
    seen = set()
    events = []
    step_keys = list(pmap.keys())
    t0_of = {}
    for t, par in pmap.items():
        for o in par.operations:
            if id(o) in seen:
                continue
            seen.add(id(o))
            t0_of[id(o)] = t
            events.append((t, o))
    assert len(events) == len(allOps), (len(events), len(allOps))
    # consistency: every op listed in a step is running at that step. The tool compares float
    # times with ==, so an op whose end is t + 3e-19 s is still listed at step t ("float jitter").
    jitter = []
    for t, par in pmap.items():
        for o in par.operations:
            s0 = t0_of[id(o)]
            end = s0 + o.operationTime()
            assert s0 <= t + 1e-12 and t <= end + 1e-12, (t, s0, o.label)
            if t > end - 1e-12:
                jitter.append({"step_t": t, "op_t0": s0, "op_end": end, "label": o.label})
    near_dup_keys = sum(1 for a, b in zip(sorted(step_keys), sorted(step_keys)[1:]) if b - a < 1e-12)

    def ion_list(o):
        return [i.idx for i in o.ions] if hasattr(o, "_ions") and isinstance(o, qq.QubitOperation) else None

    ev_out = []
    for eid, (t, o) in enumerate(events):
        rl = run_log.get(id(o))
        e = {
            "id": eid,
            "kind": type(o).__name__,
            "KEY": o.KEY.name,
            "label": o.label,
            "t0": t,
            "t1": t + o.operationTime(),
            "t0_us": round(t * 1e6, 6),
            "t1_us": round((t + o.operationTime()) * 1e6, 6),
            "operationTime": o.operationTime(),
            "allOps_index": op_index[id(o)],
            "routing_pass": block_of[op_index[id(o)]],
            "run_seq": rl["seq"] if rl else None,
            "involvedComponents": [comp_ref(x) for x in o.involvedComponents],
            "fidelity": float(o.fidelity()),
        }
        if isinstance(o, qq.QubitOperation):
            e["ions"] = [i.idx for i in o.ions]
            e["trap"] = o._trap.idx if o._trap is not None else None
            if isinstance(o, qq.GateSwap):
                e["ion1"] = o._ion1.idx
                e["ion2"] = o._ion2.idx
                e["n_ms"] = len(o._operations)
        else:
            cr = getattr(o, "_crossing", None)
            if cr is not None:
                e["segment"] = cr.idx
            if isinstance(o, (qo.Split, qo.Merge)):
                e["trap"] = o._trap.idx
                other = [n for n in cr.connection if n is not o._trap]
                e["other_end"] = comp_ref(other[0]) if other else None
            if isinstance(o, qo.JunctionCrossing):
                e["junction"] = o._junction.idx
            kw = getattr(o, "_ion", None)
            e["ion_kwarg"] = kw.idx if kw is not None else None
            # the ion actually moved, observed from the state change
            mv = None
            if rl:
                pre, post = rl["pre"], rl["post"]
                if isinstance(o, (qo.Split, qo.Move)):
                    mv = post["segments"].get(str(cr.idx))
                elif isinstance(o, qo.Merge):
                    mv = rl["merge_ion"]
                elif isinstance(o, qo.JunctionCrossing):
                    seg_pre = pre["segments"].get(str(cr.idx))
                    mv = seg_pre if seg_pre is not None else (pre["junctions"].get(str(o._junction.idx)) or [None])[0]
                    e["direction"] = "segment->junction" if seg_pre is not None else "junction->segment"
            e["ion"] = mv
        if rl:
            e["chains_after"] = rl["post"]["traps"]
            if rl["post"]["junctions"]:
                e["junctions_after"] = rl["post"]["junctions"]
            if rl["post"]["segments"]:
                e["segments_after"] = rl["post"]["segments"]
        ev_out.append(e)

    # ---------- circuit file ----------
    circ_name = "circuit.stim"
    with open(os.path.join(out, circ_name), "w", newline="\n") as f:
        f.write(str(circuit) + "\n")
    with open(os.path.join(out, "tool_instruction_stream.txt"), "w", newline="\n") as f:
        f.write("\n".join(circuit.circuitString()) + "\n")

    qubit_to_ion = {str(k): v[0].idx for k, v in circuit.ionMapping.items()}
    qubit_info = {str(k): {"ion": v[0].idx, "label": v[0].label, "coords": list(v[1]),
                           "role": "data" if v[0].label.startswith("D") else "ancilla"}
                  for k, v in circuit.ionMapping.items()}

    device = cap["device"]

    def K(mod, cls, attr, missing="absent at this commit"):
        c_ = getattr(mod, cls, None)
        return getattr(c_, attr, missing) if c_ is not None else missing
    device["timing_constants_s"] = {
        "Split.SPLITTING_TIME": qo.Split.SPLITTING_TIME,
        "Merge.MERGING_TIME": qo.Merge.MERGING_TIME,
        "Move.MOVING_TIME": qo.Move.MOVING_TIME,
        "JunctionCrossing.CROSSING_TIME (per entry or per exit op)": qo.JunctionCrossing.CROSSING_TIME,
        "TwoQubitMSGate.OP_TIME": K(qq, "TwoQubitMSGate", "OP_TIME", "absent at this commit (calculateOperationTime hard-codes 40e-6)"),
        "OneQubitGate.GATE_DURATION (XRotation/YRotation)": qq.OneQubitGate.GATE_DURATION,
        "Measurement.MEASUREMENT_TIME": qq.Measurement.MEASUREMENT_TIME,
        "QubitReset.RESET_TIME": qq.QubitReset.RESET_TIME,
        "GateSwap": "3 x TwoQubitMSGate.OP_TIME",
        "CrystalRotation.ROTATION_TIME (unused)": K(qo, "CrystalRotation", "ROTATION_TIME"),
        "PhysicalCrossingSwap.CROSSING_TIME (WISE only)": K(qo, "PhysicalCrossingSwap", "CROSSING_TIME"),
    }
    device["other_constants"] = {
        "Junction.DEFAULT_CAPACITY": Junction.DEFAULT_CAPACITY,
        "Trap.CAPACITY_SCALING": Trap.CAPACITY_SCALING,
        "Trap.BACKGROUND_HEATING_RATE": Trap.BACKGROUND_HEATING_RATE,
        "TwoQubitMSGate.A": K(qq, "TwoQubitMSGate", "A"),
        "heating_rates": {"Split": qo.Split.HEATING_RATE, "Merge": qo.Merge.HEATING_RATE,
                          "Move": qo.Move.HEATING_RATE, "JunctionCrossing": qo.JunctionCrossing.HEATING_RATE},
        "T2_s": K(qo, "CrystalOperation", "T2"),
    }
    device["component_locking_rule"] = (
        "an op occupies every component in its involvedComponents for its whole duration; "
        "ops sharing any component keep allOps (program) order; "
        "QubitOperations lock their ions + trap; Split/Merge lock trap + segment + both segment endpoints; "
        "Move locks the segment; JunctionCrossing locks junction + segment")

    t_end = max(e["t1"] for e in ev_out)
    sched = {
        "tool": "PartIIProject QEC compiler (Jones & Murali, arXiv 2510.23519)",
        "repo": "https://github.com/scottjones03/PartIIProject",
        "commit": args.commit,
        "command": args.command_line,
        "config": {
            "name": args.name, "recipe": args.recipe, "code": args.code, "distance": d,
            "capacity": c, "rows": args.rows, "cols": args.cols, "padding": args.padding, "traps": args.traps,
            "rounds": 1, "seed_numpy": args.seed, "topology_note": args.topology_note,
            "generated_args": [list(gen_args[0]), {k: (v if not isinstance(v, (list, tuple)) else list(v)) for k, v in gen_args[1].items()}],
            "gate_improvements": [] if args.recipe == "process_circuit" else None,
            "trapCapacity_passed_to_ionRouting": cap["trapCapacity_arg"],
            "layerBarriers_passed_to_ionRouting": cap.get("layerBarriers_arg"),
            "pass_barriers_arg": args.pass_barriers,
            "scheduler": cap.get("scheduler", "paralleliseOperationsWithBarriers"),
        },
        "units": "seconds (t0, t1, operationTime); *_us fields are the same in microseconds",
        "circuit_file": circ_name,
        "device": device,
        "initial_layout": cap["initial_layout"],
        "qubit_to_ion": qubit_to_ion,
        "qubits": qubit_info,
        "ion_labels": cap["ion_labels"],
        "barriers": cap["barriers"],
        "barriers_note": "indices into allOps where each routing pass ends; paralleliseOperationsWithBarriers schedules each block after the previous block's latest end",
        "steps": [{"t": t, "t_us": round(t * 1e6, 6), "n_running": len(par.operations)} for t, par in pmap.items()],
        "events": ev_out,
        "metrics": {
            "process_circuit_results": results,
            "notebook_print_lines": printed[-6:],
            "time for operations (us) = max(parallelOpsMap.keys())*1e6 [start of last step]": max(pmap.keys()) * 1e6,
            "total number of routing operations = len(allOps)-len(instructions)": len(allOps) - len(instructions),
            "total number of operations = len(allOps)": len(allOps),
            "total number of qubit operations = len(instructions)": len(instructions),
            "num gate swaps": numGateSwaps,
            "num splits": numSplits,
            "counts_by_kind": {k: sum(1 for e in ev_out if e["kind"] == k) for k in sorted({e["kind"] for e in ev_out})},
            "derived_not_printed_by_tool": {"makespan_end_us = max(t1)": round(t_end * 1e6, 6),
                                            "routing_passes": len(cap["barriers"]),
                                            "float_jitter_listings (op listed in a step at its own end time)": len(jitter),
                                            "float_jitter_examples": jitter[:5],
                                            "step_keys_closer_than_1e-12s": near_dup_keys},
            "wall_seconds": round(wall, 2),
        },
    }
    if results is not None:
        sched["metrics"]["process_circuit_results"] = json.loads(json.dumps(results, default=float))
    with open(os.path.join(out, "schedule.json"), "w", newline="\n") as f:
        json.dump(sched, f, indent=1, default=float)
    print(f"[driver] wrote {os.path.join(out, 'schedule.json')} events={len(ev_out)} wall={wall:.1f}s")


if __name__ == "__main__":
    main()
