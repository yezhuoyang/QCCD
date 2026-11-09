"""Magic state cultivation on the ion machine (docs/FULLSTACK_PLAN.md, S10 step 4): Gidney, Shutty and Jones's circuits
(arXiv:2409.17595; zenodo.org/records/13777072, code Apache 2.0, circuits and statistics CC BY 4.0), gate for gate.

Their qubits stand on a square grid and every CX joins grid neighbours, so the grid's two checkerboards are our tile lattice turned by
45 degrees: a point of even x + y a data trap, a point of odd x + y a face trap, and each CX a face ion stepping to its data ion.  Nothing is
searched and nothing is reordered.  A colour code's data ions stand on both checkerboards; `compile_tiled` does not care.

    to_surface(text, d)     an end-to-end circuit, its patch dropped to a plain one at the end, so that merges can take it
    read(text)              their circuit, noise dropped: the experiment, its coordinates, the part that is not on ions
    build(text)             the program: `compile_tiled` on `machine.device`
    noisy(reading, built)   its circuit with the noise its own timeline gives (`qccd.qec.extract`), detector coordinates kept

Their circuits are Clifford twins: their T is an S (`S_DAG` at the injection, `S` and `S_DAG` transversally), which is what R10 and stim
can follow.  On ions T and S are the same instructions: a frame change (`VZ`) of a different angle.

A circuit ends in `MPP`: their noiseless read of the logical Y and of the code's stabilizers, how a memory is judged, not a thing the machine
does.  It stays out of the program and `noisy` adds it after the last instruction, noiselessly, as they do.
"""
from __future__ import annotations

from dataclasses import dataclass

from . import machine as M

__all__ = ["Reading", "read", "build", "noisy", "without_noise", "accepted", "undetected", "to_surface"]

_NOISE = {"DEPOLARIZE1", "DEPOLARIZE2", "X_ERROR", "Y_ERROR", "Z_ERROR", "PAULI_CHANNEL_1", "PAULI_CHANNEL_2"}
_GATES = {"S": "s", "S_DAG": "sdg", "H": "h"}


@dataclass
class Reading:
    """A circuit of theirs as our compiler reads it.  Source qubit q is `order[q]`: data first."""

    experiment: object      # `qccd.qec.experiments.MemoryExperiment`, without the noiseless tail
    rows: int
    cols: int
    tail: list              # stim lines after the last instruction: the `MPP` and what it decides, in `rec[...]` from the end of the whole record
    annotations: list       # each detector of the experiment, then each of the tail: its stim coordinates


def without_noise(text: str):
    import stim
    out = stim.Circuit()
    for ins in stim.Circuit(text).flattened():
        if ins.name not in _NOISE:
            out.append(ins)
    return out


def _grid(r, c) -> tuple:
    """A data point (r, c) of the patch (or (r + 1/2, c + 1/2) for a face) on their grid."""
    return r + c, r - c


def _gf2_null_parity(samples, want: int, among: list):
    """The fewest of the columns `among` that with column `want` XOR to a constant over every row of `samples`, or None: Gaussian elimination, smallest set
    found by eliminating in order of `among`.  Columns are numpy bool arrays of shots."""
    import numpy as np
    target = samples[:, want].astype(np.uint8)
    target = target ^ target[0]
    cols = [(samples[:, k].astype(np.uint8) ^ samples[0, k]) for k in among]
    # singles and pairs first: most checks are unchanged
    for k, col in zip(among, cols):
        if np.array_equal(col, target):
            return [k]
    basis: list = []        # (pivot row, vector, combination)
    for k, col in zip(among, cols):
        vec = col.copy()
        combo = {k}
        for piv, b, cb in basis:
            if vec[piv]:
                vec = vec ^ b
                combo = combo ^ cb
        rows = np.nonzero(vec)[0]
        if len(rows):
            basis.append((int(rows[0]), vec, combo))
    vec = target.copy()
    combo: set = set()
    for piv, b, cb in basis:
        if vec[piv]:
            vec = vec ^ b
            combo = combo ^ cb
    return sorted(combo) if not vec.any() else None


def to_surface(text: str, d: int, rounds: int = 2, read_odd: str = "ZZXZZ", prep_even: str = "XZZZ", products: bool = False) -> str:
    """An end-to-end circuit of theirs, its patch dropped to a plain surface patch, then `rounds` of our patch's rounds, then their noiseless read.

    Their post-escape code is a plain patch but for a corner: 110 of its 120 checks are a plain patch's, its logicals run along the far sides, and
    five data qubits stand on face points (`read_odd`: each measured out, in the basis named) while four data points of the plain patch hold nothing
    (`prep_even`: each prepared).  So one round of plain checks takes it there.  A plain check's detector is whatever earlier results fix it (found by sampling
    the noiseless circuit); a check no earlier result fixes starts at random, the next round's detector covers it, and the logical is far away.
    Of the 512 ways to choose the bases, the defaults keep the most detectors (1,290 at d = 11, one round).  `products`: also the detectors that are
    products of several earlier results, postselected.  Without them a measurement fault in the last round before the drop is a boundary edge of the matcher, as it should be.
    The text must end in their `MPP`."""
    import numpy as np
    import stim
    c = without_noise(text)
    grid = {tuple(int(x) for x in v[:2]): q for q, v in c.get_final_qubit_coordinates().items()}
    body = stim.Circuit()
    tail_first = None
    for ins in c:
        if ins.name == "MPP":
            tail_first = ins
            break
        body.append(ins)
    if tail_first is None:
        raise ValueError("the circuit has no noiseless read")
    last_round = _last_round_bits(body)
    from ...gadget.surface import patch
    checks = patch(d, d).checks
    point = {}
    for ch in checks:
        point[ch.name] = grid[(ch.i + ch.j + 1, ch.i - ch.j)]
    data = {(r, k): grid[_grid(r, k)] for r in range(d) for k in range(d)}
    old_data = {t.value for t in tail_first.targets_copy() if not t.is_combiner}
    odd = sorted(q for q in old_data if q not in data.values())
    even = sorted(q for q in data.values() if q not in old_data)
    if len(odd) != len(read_odd) or len(even) != len(prep_even):
        raise ValueError(f"{len(odd)} data on face points, {len(even)} plain data points empty")

    new = body.copy()
    n0 = new.num_measurements
    # the data on face points read out, the empty data points prepared
    for q, b in zip(odd, read_odd):
        new.append("MX" if b == "X" else "M", [q])
    for q, b in zip(even, prep_even):
        new.append("RX" if b == "X" else "R", [q])
    new.append("TICK")
    n1 = new.num_measurements
    per_round = []
    for t in range(rounds):
        new.append("RX", [point[ch.name] for ch in checks if ch.basis == "X"])
        new.append("R", [point[ch.name] for ch in checks if ch.basis == "Z"])
        new.append("TICK")
        by_layer: dict = {}
        for ch in checks:
            m = point[ch.name]
            for q, layer in zip(ch.support, ch.layers):
                dq = data[divmod(q, d)]
                by_layer.setdefault(layer, []).extend((m, dq) if ch.basis == "X" else (dq, m))
        for layer in sorted(by_layer):
            new.append("CX", by_layer[layer])
            new.append("TICK")
        here = {}
        for ch in checks:
            if ch.basis == "X":
                here[ch.name] = new.num_measurements
                new.append("MX", [point[ch.name]])
        for ch in checks:
            if ch.basis == "Z":
                here[ch.name] = new.num_measurements
                new.append("M", [point[ch.name]])
        new.append("TICK")
        per_round.append(here)

    # detectors, against the noiseless circuit
    shots = new.compile_sampler(seed=1).sample(1024)
    among = last_round + list(range(n0, n1))
    final_note = []
    for ch in checks:
        b = per_round[0][ch.name]
        got = _gf2_null_parity(shots, b, among)
        note = (ch.i + ch.j + 1, ch.i - ch.j, 0, 0, 6) if ch.basis == "X" else (ch.i + ch.j + 1, ch.i - ch.j, 0, 3, 7)
        if got is not None and (products or len(got) == 1):
            # a product of several results is the drop's own: postselected, kept from the matcher, as their injection and cultivation detectors are
            final_note.append((note if len(got) == 1 else (note[0], note[1], 0, -1, -9), [b] + got))
    for t in range(1, rounds):
        for ch in checks:
            note = (ch.i + ch.j + 1, ch.i - ch.j, t, 0, 6) if ch.basis == "X" else (ch.i + ch.j + 1, ch.i - ch.j, t, 3, 7)
            final_note.append((note, [per_round[t][ch.name], per_round[t - 1][ch.name]]))
    total = new.num_measurements
    for note, bits in final_note:
        new.append("DETECTOR", [stim.target_rec(x - total) for x in bits], note)
    # their noiseless read: the same logical Y, the plain checks
    logical = _first_product(tail_first)
    products = [logical]
    for ch in checks:
        products.append("*".join(f"{ch.basis}{data[divmod(q, d)]}" for q in ch.support))
    new.append(stim.Circuit("MPP " + " ".join(products)))
    total = new.num_measurements
    new.append("OBSERVABLE_INCLUDE", [stim.target_rec(-len(products))], 0)
    for k, ch in enumerate(checks):
        note = (ch.i + ch.j + 1, ch.i - ch.j, rounds, 0, 6) if ch.basis == "X" else (ch.i + ch.j + 1, ch.i - ch.j, rounds, 3, 7)
        at = total - len(products) + 1 + k
        new.append("DETECTOR", [stim.target_rec(at - total), stim.target_rec(per_round[-1][ch.name] - total)], note)
    return str(new)


def _first_product(mpp) -> str:
    """An `MPP`'s first product, as text."""
    ts = mpp.targets_copy()
    out, k = [], 0
    while k < len(ts):
        t = ts[k]
        out.append(("X" if t.is_x_target else "Y" if t.is_y_target else "Z") + str(t.value))
        if k + 1 >= len(ts) or not ts[k + 1].is_combiner:
            break
        k += 2
    return "*".join(out)


def _last_round_bits(body) -> list:
    """The record bits since the circuit's last wide reset: its last round's results."""
    start = bit = 0
    for ins in body:
        if ins.name in ("R", "RX", "RZ") and len(ins.targets_copy()) > 8:
            start = bit
        elif ins.name in ("M", "MZ", "MX"):
            bit += len(ins.targets_copy())
    return list(range(start, bit))


def _lattice(coords: dict) -> tuple:
    """(x, y) -> a point of the lattice: (even x + y) data (r, c), (odd) face (i, j).  (data, faces, rows, cols), each keyed by source qubit."""
    def turned(x, y):
        return (x + y) / 2, (x - y) / 2
    pts = {q: turned(x, y) for q, (x, y) in coords.items()}
    data = {q: p for q, p in pts.items() if p[0] == int(p[0])}
    # shift the data points to start at (0, 0): a face at i = -1 or j = -1 stands on the machine's outer wires
    r0 = min(p[0] for p in data.values())
    c0 = min(p[1] for p in data.values())
    dout = {q: (int(p[0] - r0), int(p[1] - c0)) for q, p in data.items()}
    fout = {q: (int(p[0] - r0 - 0.5), int(p[1] - c0 - 0.5)) for q, p in pts.items() if q not in data}
    rows = max(max(p[0] for p in dout.values()), max(p[0] + 1 for p in fout.values())) + 1
    cols = max(max(p[1] for p in dout.values()), max(p[1] + 1 for p in fout.values())) + 1
    return dout, fout, rows, cols


def read(text: str, *, name: str = "cultivation") -> Reading:
    """Their circuit, noise dropped, gate for gate.  Every reset is played, the first too: an attempt starts from ions that were just used."""
    from ...qec.experiments import MemoryExperiment
    c = without_noise(text)
    coords = {q: (int(v[0]), int(v[1])) for q, v in c.get_final_qubit_coordinates().items()}
    data, faces, rows, cols = _lattice(coords)
    data_q, face_q = sorted(data), sorted(faces)
    to = {q: i for i, q in enumerate(data_q + face_q)}
    det_coords = c.get_detector_coordinates()

    lines, detectors, observables = [], [], {}
    tail, tail_marks = [], []
    bit = 0                 # the measurements on ions so far
    k_det = 0
    annotations, tail_notes = [], []
    mpp_at = None           # where the noiseless record starts
    for ins in c:
        n = ins.name
        args = ins.gate_args_copy()
        targets = ins.targets_copy()
        if n in ("TICK", "QUBIT_COORDS", "SHIFT_COORDS"):
            continue
        if n == "MPP":
            if mpp_at is None:
                mpp_at = bit
            tail.append(_renumbered(targets, to))
            bit += _products(targets)
            continue
        if n in ("DETECTOR", "OBSERVABLE_INCLUDE"):
            at = [bit + t.value for t in targets]    # absolute record indices
            if mpp_at is not None and any(x >= mpp_at for x in at):
                tail_marks.append((n, args, at))
                if n == "DETECTOR":
                    tail_notes.append(tuple(det_coords[k_det]))
            elif n == "DETECTOR":
                detectors.append(tuple(sorted(at)))
                annotations.append(tuple(det_coords[k_det]))
            else:
                observables.setdefault(int(args[0]) if args else 0, []).extend(at)
            k_det += n == "DETECTOR"
            continue
        if mpp_at is not None:
            raise ValueError(f"{n} after the noiseless read: not a circuit of the form read() understands")
        qs = [t.value for t in targets]
        if n in ("R", "RZ", "RX"):
            for q in qs:
                lines.append(f"reset q[{to[q]}];")
            if n == "RX":
                lines.extend(f"h q[{to[q]}];" for q in qs)
        elif n in ("M", "MZ", "MX"):
            if n == "MX":
                lines.extend(f"h q[{to[q]}];" for q in qs)
            for q in qs:
                lines.append(f"measure q[{to[q]}] -> c[{bit}];")
                bit += 1
        elif n == "CX":
            lines.extend(f"cx q[{to[a]}],q[{to[b]}];" for a, b in zip(qs[::2], qs[1::2]))
        elif n in _GATES:
            lines.extend(f"{_GATES[n]} q[{to[q]}];" for q in qs)
        else:
            raise ValueError(f"stim instruction {n} is not one read() knows")
    n_bits = mpp_at if mpp_at is not None else bit
    # the tail, in `rec[...]` from the end of the whole record, the tail's own measurements counted
    total = bit
    out_tail = list(tail)
    for n, args, at in tail_marks:
        arg = "(" + ", ".join(str(a) for a in args) + ")" if args and n == "OBSERVABLE_INCLUDE" else ""
        out_tail.append(f"{n}{arg} " + " ".join(f"rec[{x - total}]" for x in at))
    n_q = len(to)
    qasm = ["OPENQASM 2.0;", 'include "qelib1.inc";', f"qreg q[{n_q}];", f"creg c[{n_bits}];"] + lines
    exp = MemoryExperiment(
        name=name, code="cultivation", distance=0, rounds=0, basis="Y",
        n_data=len(data_q), n_ancilla=len(face_q), qasm="\n".join(qasm) + "\n",
        detectors=tuple(detectors),
        observables=tuple(tuple(v) for _, v in sorted(observables.items()) if v) or ((),),
        coords=tuple(data[q] for q in data_q), faces=tuple(faces[q] for q in face_q))
    return Reading(exp, rows, cols, out_tail, annotations + tail_notes)


def _renumbered(targets, to: dict) -> str:
    """An `MPP` on the compiled circuit's qubit numbers."""
    out = []
    for t in targets:
        if t.is_combiner:
            out.append("*")
        else:
            out.append(" " if out else "")
            out.append(("X" if t.is_x_target else "Y" if t.is_y_target else "Z") + str(to[t.value]))
    return "MPP " + "".join(out)


def _products(targets) -> int:
    """How many results an `MPP`'s targets make."""
    n, combining = 0, False
    for t in targets:
        if t.is_combiner:
            combining = True
        else:
            n += not combining
            combining = False
    return n


def build(reading: Reading):
    """The program, on the machine's device.  (TSIR, certificate, architecture)."""
    from ...compile.tiled_surface import compile_tiled
    e = reading.experiment
    arch = M.device(reading.rows, reading.cols)
    return compile_tiled(e.qasm, reading.rows, reading.cols, arch=arch, coords=e.coords, faces=e.faces, name=e.name)


def noisy(reading: Reading, prog, arch, noise=None):
    """(noisy stim circuit with the tail and detector coordinates, the `Extraction`).  `prog` as the machine runs it: cooled."""
    import stim

    from ...qec.extract import extract
    ext = extract(prog, arch, reading.experiment, noise=noise or M.noise())
    lines = [ln for ln in ext.lines]
    # stim's own coordinates on each detector, in their order: the gap decoder reads them
    k = 0
    out = []
    for ln in lines:
        if ln.startswith("DETECTOR"):
            head, _, rest = ln.partition(" ")
            note = reading.annotations[k] if k < len(reading.annotations) else ()
            out.append(f"DETECTOR({', '.join(str(x) for x in note)}) {rest}" if note else f"DETECTOR {rest}")
            k += 1
        else:
            out.append(ln)
    tail = list(reading.tail)
    for ln in tail:
        if ln.startswith("DETECTOR"):
            _, _, rest = ln.partition(" ")
            note = reading.annotations[k]
            out.append(f"DETECTOR({', '.join(str(x) for x in note)}) {rest}")
            k += 1
        else:
            out.append(ln)
    return stim.Circuit("\n".join(out)), ext


def accepted(circuit, shots: int, chunk: int = 2_000_000) -> tuple:
    """(kept, errors among the kept) with every detector postselected: their `perfectionist`."""
    import numpy as np
    sampler = circuit.compile_detector_sampler()
    kept = errors = 0
    left = shots
    while left > 0:
        n = min(left, chunk)
        dets, obs = sampler.sample(n, bit_packed=True, separate_observables=True)
        ok = ~np.any(dets, axis=1)
        kept += int(np.count_nonzero(ok))
        errors += int(np.count_nonzero(np.any(obs, axis=1) & ok))
        left -= n
    return kept, errors


def undetected(circuit, weight: int = 3) -> dict:
    """The exact leading term of a postselected error: every set of up to `weight` of the circuit's error mechanisms that fires no detector and
    flips the observable, with the sum of the products of their probabilities.  Exact to that order, for the noise as it stands, where sampling
    would need 10^12 shots.  {w: (sets, sum of probabilities)}."""
    from collections import defaultdict
    dem = circuit.detector_error_model(decompose_errors=False, approximate_disjoint_errors=True, ignore_decomposition_failures=True,
                                       block_decomposition_from_introducing_remnant_edges=False).flattened()
    errs = defaultdict(float)
    for ins in dem:
        if ins.type != "error":
            continue
        dets, obs = set(), 0
        for t in ins.targets_copy():
            if t.is_relative_detector_id():
                dets ^= {t.val}
            elif t.is_logical_observable_id():
                obs ^= 1
        key = (frozenset(dets), obs)
        p = ins.args_copy()[0]
        errs[key] = errs[key] * (1 - p) + p * (1 - errs[key])
    items = list(errs.items())
    out = {}
    if weight >= 1:
        hits = [p for (d, o), p in items if not d and o]
        out[1] = (len(hits), sum(hits))
    if weight >= 2:
        by = defaultdict(lambda: [0.0, 0.0])    # detectors -> the sum of p with obs 0, with obs 1
        for (d, o), p in items:
            by[d][o] += p
        n2, s2 = 0, 0.0
        for d, (p0, p1) in by.items():
            if d:
                s2 += p0 * p1
                n2 += 1
        out[2] = (n2, s2)
    if weight >= 3:
        # a + b + c = 0 on detectors and odd on the observable: for each pair, the third is looked up
        by = defaultdict(lambda: [0.0, 0.0])
        for (d, o), p in items:
            by[d][o] += p
        n3, s3 = 0, 0.0
        for i, ((d1, o1), p1) in enumerate(items):
            if not d1:
                continue
            for (d2, o2), p2 in items[i + 1:]:
                if not d2:
                    continue
                d3 = d1 ^ d2
                if not d3 or d3 not in by:
                    continue
                pz = by[d3][1 ^ o1 ^ o2]
                if pz:
                    n3 += 1
                    s3 += p1 * p2 * pz
        out[3] = (n3 // 3, s3 / 3)
    return out
