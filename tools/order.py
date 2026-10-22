"""Check that a QCCD schedule runs a correct surface-code round.

Usage:  python order.py SCHEDULE.json.gz SOURCE.stim

SCHEDULE is either an artifact schedule (Jones & Murali, keys qubit_to_ion / TwoQubitMSGate)
or a QCCD-studio schedule (keys qubits / kind "gate").  SOURCE is the circuit it compiles.

It rebuilds the circuit in the order the schedule actually runs its two-qubit gates, then checks
  1. with no noise, every detector is deterministic (the round measures the code), and
  2. the circuit-level distance equals the source circuit's (no harmful hook errors).
Assumes resets/Hadamards before the CNOTs and Hadamards/measurements after them stay in place.
"""
import gzip, json, sys
import stim

def load(sched_path):
    d = json.load(gzip.open(sched_path) if sched_path.endswith(".gz") else open(sched_path))
    if "qubit_to_ion" in d:                     # Jones artifact format
        q2i = {int(k): str(v) for k, v in d["qubit_to_ion"].items()}
        gates = [e for e in d["events"] if e["kind"] == "TwoQubitMSGate"]
        key = "t0"
    else:                                       # QCCD-studio format
        q2i = {int(k): str(v) for k, v in d["qubits"].items()}
        gates = [e for e in d["events"] if e["kind"] == "gate" and len(e["ions"]) == 2]
        key = "t0"
    i2q = {v: k for k, v in q2i.items()}
    gates.sort(key=lambda e: (e[key], e.get("id", 0)))
    return q2i, [frozenset(i2q[str(i)] for i in e["ions"]) for e in gates]

def split_source(src):
    head, tail, pairs, seen = [], [], [], False
    for ins in src:
        if ins.name == "CX":
            seen = True
            t = [x.value for x in ins.targets_copy()]
            pairs += [(t[i], t[i + 1]) for i in range(0, len(t), 2)]
        elif ins.name != "TICK":
            (tail if seen else head).append(ins)
    return head, tail, pairs

def build(head, tail, seq, noisy):
    c = stim.Circuit()
    for ins in head:
        c.append(ins)
        if noisy and ins.name in ("R", "RX", "H"):
            c.append("DEPOLARIZE1", [t.value for t in ins.targets_copy()], 1e-3)
    for a, b in seq:
        c.append("CX", [a, b])
        if noisy:
            c.append("DEPOLARIZE2", [a, b], 1e-3)
    for ins in tail:
        if noisy and ins.name in ("M", "MR"):
            c.append("X_ERROR", [t.value for t in ins.targets_copy()], 1e-3)
        elif noisy and ins.name in ("MX", "MRX"):
            c.append("Z_ERROR", [t.value for t in ins.targets_copy()], 1e-3)
        c.append(ins)
        if noisy and ins.name == "H":
            c.append("DEPOLARIZE1", [t.value for t in ins.targets_copy()], 1e-3)
    return c

def check(name, head, tail, seq):
    c = build(head, tail, seq, False)
    s = c.compile_detector_sampler().sample(500, append_observables=True)
    nd = c.num_detectors
    rand_det = [int(i) for i in s[:, :nd].any(axis=0).nonzero()[0]]
    obs_flip = int(s[:, nd:].any(axis=1).sum())
    if rand_det or obs_flip:
        print(f"{name}: FAIL - with no noise, detectors {rand_det} fire and the observable flips in {obs_flip}/500 shots")
        return None
    err = build(head, tail, seq, True).search_for_undetectable_logical_errors(
        dont_explore_detection_event_sets_with_size_above=4,
        dont_explore_edges_with_degree_above=4,
        dont_explore_edges_increasing_symptom_degree=False)
    print(f"{name}: detectors deterministic; circuit distance <= {len(err)}")
    return len(err)

def main(sched_path, src_path):
    head, tail, pairs = split_source(stim.Circuit(open(src_path).read()))
    q2i, executed = load(sched_path)
    lookup = {frozenset(p): p for p in pairs}
    missing = [g for g in executed if g not in lookup]
    if missing or len(executed) != len(pairs):
        print(f"FAIL - schedule has {len(executed)} two-qubit gates, source has {len(pairs)}; unmatched: {missing[:5]}")
        return 1
    seq = [lookup[g] for g in executed]
    def per_qubit(s):
        o = {}
        for p in s:
            for q in p:
                o.setdefault(q, []).append(p)
        return o
    so, eo = per_qubit(pairs), per_qubit(seq)
    diffs = [q for q in sorted(so) if so[q] != eo[q]]
    for q in diffs:
        print(f"qubit {q} (ion {q2i.get(q)}): source {so[q]}\n{'':>{len(str(q)) + len(str(q2i.get(q))) + 10}}executed {eo[q]}")
    if not diffs:
        print("executed order matches the source on every qubit")
    d_src = check("source  ", head, tail, pairs)
    d_exe = check("executed", head, tail, seq)
    ok = d_exe is not None and d_exe == d_src
    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1

if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
