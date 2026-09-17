"""Flat sign-off: the whole schedule, ion by ion, against the algorithm's ideal circuit.

This is the gadget layer's LVS.  Every gadget op was verified against its own spec; this
checks that the *composition* computes the logical algorithm, from nothing but the
scheduled hardware:

1. **Flatten.**  Every event's circuit (read off its verified TSIR) is renamed onto global
   ions -- residents by their instance's ids, visitors by the event's binding -- and every
   gate, measurement and reset is placed at its absolute time.  Sorting by time is exact:
   two instructions on one ion never overlap, and instructions on different ions commute.
   The magic-state factory is the one exception: its T gates are verified separately (state
   vector), so its output ion enters the flat circuit as a free input.

2. **Decode.**  A logical outcome is the parity of the records a gadget's own flow report
   names for it (a readout's `Z̄ measured`, a bridge's `Z̄_AZ̄_B measured`), corrected by the
   Pauli frame the classical memory held for that block at that moment -- exactly the
   expression the archive shows (`gir.outcomes[...].value`).  A logical Pauli frame is a
   gadget's `logical` frame flow, and the frame a surviving block ends with is applied to
   every flow it appears in: for a frame `X^ax Z^az` and an operator `X^qx Z^qz` the sign
   flips by `ax·qz ⊕ az·qx`.  Nothing is taken from the flat simulation.

3. **Compare**, on one symbolic run of the flat circuit (Choi state on every ion):
   * every deterministic relation among the ideal circuit's outcomes holds among the decoded
     physical outcomes, up to a combination of declared frames;
   * the decoded outcomes carry exactly as many independent random bits as the ideal ones
     (no outcome the ideal leaves random came out fixed, and none the other way);
   * every flow of the ideal circuit from its inputs to its surviving blocks holds on the
     hardware, again up to declared frames.

5. **Branches.**  A guarded schedule (`if m: s q0`) means something different for each
   value of the bits its guards read, so it is signed off once per branch: the events of a
   guarded instruction that does not run are left out of the flat circuit (its place ran
   the skip branch: ions in, wait, ions out), the ideal circuit is built for that branch,
   the archive's guarded frames are applied only where they hold, and the branch's guard
   value is added to the known-value span -- so a relation that holds *given* `m = 1` is
   accepted in that branch and nowhere else.  Every branch must pass.

4. **Fault distance.**  Detectors are the deterministic record parities that no logical
   Pauli, inserted on any block between two gadgets, can flip; the observables are the
   relations of step 3.  All single faults and pairs of faults of the whole schedule are
   checked for an undetected logical error.
"""

from __future__ import annotations

import time
from itertools import product

from ...ir.tsir import TSIR
from .. import gf2
from ..algorithm import Algorithm
from .circuit import Circuit, from_tsir
from .faults import low_weight_logical, propagate
from .tableau import bits, pauli_bits, simulate

__all__ = ["flatten_schedule", "sign_off"]


class _Span:
    """A GF(2) span of record expressions, each row remembering the constant it carries and
    the record set it came from, so a reduction also says which records it used."""

    def __init__(self):
        self.rows: list[tuple[int, int, int, int]] = []     # (pivot, vector, const, origin)

    def reduce(self, v: int, c: int = 0, origin: int = 0) -> tuple[int, int, int]:
        for p, row, rc, ro in self.rows:
            if (v >> p) & 1:
                v ^= row
                c ^= rc
                origin ^= ro
        return v, c, origin

    def add(self, v: int, c: int = 0, origin: int = 0) -> bool:
        v, c, origin = self.reduce(v, c, origin)
        if not v:
            return False
        p = v.bit_length() - 1
        self.rows = [(q, row ^ v, rc ^ c, ro ^ origin) if (row >> p) & 1 else (q, row, rc, ro)
                     for q, row, rc, ro in self.rows]
        self.rows.append((p, v, c, origin))
        return True


def flatten_schedule(gir: dict, lib, leaves, *, skip=("factory",), skip_events=()):
    """The schedule as one circuit over global ions `g<id>`.

    Returns `(circuit, record_of, event_ops)`: `record_of[(event id, local record)]` is the
    global record index, `event_ops[event id]` the global op indices of that event.
    `skip_events` are event ids to leave out -- a guarded op that does not run in the
    branch being checked, whose place held its ions and fired nothing."""
    cache: dict[tuple[str, str], tuple[Circuit, dict, list]] = {}
    items = []
    skip_events = set(skip_events)
    for e in gir["events"]:
        if e[0] in skip_events:
            continue
        mname = gir["leaves"][e[3]][0]
        m = lib[mname]
        if m.family in skip:
            continue
        data = leaves.data[mname]
        if e[4] not in data["programs"]:
            continue
        key = (mname, e[4])
        if key not in cache:
            circ = from_tsir(TSIR.from_json(data["programs"][e[4]]))
            t_of = {row[0]: row[1] for row in data["times"][e[4]]}
            local_rec = []
            r = 0
            for op in circ.ops:
                if op.name == "M":
                    local_rec.append(list(range(r, r + len(op.targets))))
                    r += len(op.targets)
                else:
                    local_rec.append([])
            cache[key] = (circ, t_of, local_rec)
        circ, t_of, local_rec = cache[key]
        if not circ.ops:
            continue
        first = gir["leaves"][e[3]][3]
        names = {nm: first + k for k, nm in enumerate(gir["ion_order"][mname])}
        for nm, gid in e[8]:
            if isinstance(gid, int):
                names[nm] = gid
        for k, op in enumerate(circ.ops):
            items.append((e[1] + t_of.get(op.source, 0.0), e[0], k, e, circ, names, local_rec))
    items.sort(key=lambda it: (it[0], it[1], it[2]))
    flat = Circuit()
    record_of: dict[tuple[int, int], int] = {}
    event_ops: dict[int, list[int]] = {}
    for t, eid, k, e, circ, names, local_rec in items:
        op = circ.ops[k]
        targets = []
        for q in op.targets:
            nm = circ.qubits[q]
            if nm not in names:
                raise KeyError(f"event {eid} ({e[3]} {e[4]}): ion {nm} has no global id")
            targets.append(f"g{names[nm]}")
        base = len(flat.records)
        flat.add(op.name, targets, source=op.source, meta={"event": eid, "t": t, "leaf": e[3]})
        event_ops.setdefault(eid, []).append(len(flat.ops) - 1)
        for j, lr in enumerate(local_rec[k]):
            record_of[(eid, lr)] = base + j
    return flat, record_of, event_ops


def _flow(lib, gir, eid, name):
    e = gir["events"][eid]
    op = lib[gir["leaves"][e[3]][0]].ops[e[4]]
    for f in op.logic.get("flows", {}).get("flows", []):
        if f["name"] == name:
            return f
    raise KeyError(f"{e[3]} {e[4]} reports no flow {name!r}")


def sign_off(alg: Algorithm, gir: dict, lib, leaves, *, distance: bool = True) -> dict:
    """Sign the schedule off against the algorithm -- once per branch if it is guarded."""
    branches = alg.branches()
    if len(branches) == 1:
        return _sign_off(alg, gir, lib, leaves, {}, distance=distance)
    t0 = time.time()
    out: dict = {"kind": "flat", "passed": True, "relations": [], "flows": [], "why": [],
                 "branch_count": len(branches), "branches": [],
                 "guards": list(alg.guard_cvars), "circuit": {"qubits": 0, "ops": {},
                                                              "records": 0}}
    for br in branches:
        label = ", ".join(f"{k}={v}" for k, v in br.items())
        r = _sign_off(alg, gir, lib, leaves, br, distance=distance)
        r["branch"] = br
        r["label"] = label
        out["branches"].append(r)
        out["passed"] &= bool(r.get("passed"))
        # the page shows one list of checks: every branch's, labelled with its condition
        for row in r.get("relations", []):
            out["relations"].append(dict(row, relation=f"[{label}] {row['relation']}"))
        for row in r.get("flows", []):
            out["flows"].append(dict(row, flow=f"[{label}] {row['flow']}"))
        for why in r.get("why", []):
            out["why"].append(f"[{label}] {why}")
        out["circuit"] = r.get("circuit", out["circuit"])
        if r.get("archive"):
            out["archive"] = r["archive"]
        if r.get("random_bits"):
            out["random_bits"] = r["random_bits"]
        if r.get("distance"):
            out.setdefault("distance", r["distance"])
    out["seconds"] = round(time.time() - t0, 3)
    return out


def _sign_off(alg: Algorithm, gir: dict, lib, leaves, branch: dict, *,
              distance: bool = True) -> dict:
    try:
        return _sign_off_branch(alg, gir, lib, leaves, branch, distance=distance)
    except KeyError as exc:
        return {"kind": "flat", "passed": False, "relations": [], "flows": [],
                "branch": dict(branch), "why": [str(exc.args[0] if exc.args else exc)],
                "circuit": {"qubits": 0, "ops": {}, "records": 0}}


def _sign_off_branch(alg: Algorithm, gir: dict, lib, leaves, branch: dict, *,
                     distance: bool = True) -> dict:
    t_start = time.time()
    report: dict = {"kind": "flat", "passed": False, "relations": [], "flows": [], "why": []}
    # the guarded instructions that do not run in this branch: their places held the ions
    # and fired nothing, so their events are not part of this branch's circuit
    off = {ins.index for ins in alg.instructions
           if ins.cond is not None and not ins.runs(branch)}
    skip_events = [e[0] for e in gir["events"] if e[6] in off]
    flat, record_of, event_ops = flatten_schedule(gir, lib, leaves, skip_events=skip_events)
    report["circuit"] = {"qubits": flat.n, "ops": flat.counts(), "records": len(flat.records)}
    tab, n = simulate(flat, choi=True)
    expr = [tab.record_expr(r) for r in range(len(flat.records))]

    def value(recs):
        c, v = 0, 0
        for r in recs:
            rc, rv = expr[r]
            c ^= rc
            v ^= rv
        return c, v

    # -- decode: outcomes and declared frames, from the gadgets' own flow reports ----------
    def refs_records(refs) -> list[int]:
        """The global records a parity expression of the classical memory names."""
        out: list[int] = []
        for eid, flow in refs or ():
            f = _flow(lib, gir, int(eid), flow)
            for r in f["records"]:
                if (int(eid), r) not in record_of:
                    raise KeyError(
                        f"event {eid} does not measure in this branch ({branch}), but the "
                        f"archive reads its outcome: the schedule and the algorithm "
                        f"disagree about which guarded instruction runs")
                out.append(record_of[(int(eid), r)])
        return out

    outcomes = [o for o in gir["outcomes"] if o["instruction"] not in off]
    # the Pauli frame every surviving block ends with, as the archive holds it
    def _holds(cond) -> bool:
        """Does a guard recorded in the GIR hold in this branch?"""
        if not cond:
            return True
        c = int(cond.get("const", 0))
        for n in cond.get("cvars", ()):
            c ^= int(branch.get(n, 0)) & 1
        return bool(c)

    # The frame every block ends with, folded from the archive's own lines in the order they
    # were written: each line says what it changed (`delta`), so a conditional S̄ and a
    # conditional Pauli on one block compose, and a line whose guard does not hold in this
    # branch is simply not there.
    holds = _holds
    block_frame: dict[str, dict] = {}
    guarded_frames = 0

    def as_expr(d) -> tuple[int, list[int]]:
        return int(d.get("const", 0)), refs_records(d.get("sources"))

    def xor_expr(a, b):
        return a[0] ^ b[0], list(a[1]) + list(b[1])

    history: list[tuple[float, str, dict]] = []      # (t, block, frame after that line)
    for f in sorted((gir.get("frames") or []), key=lambda r: (r["t"], r["id"])):
        if f.get("kind") != "pauli":
            continue
        # a line the archive wrote is there only if the instruction that wrote it ran, and
        # only in the branches where its own condition holds
        if f.get("instruction") in off or not holds(f.get("guard")):
            continue
        guarded_frames += 1 if f.get("guard") else 0
        cur = block_frame.setdefault(f["block"], {"ax": (0, []), "az": (0, [])})
        delta = f.get("delta")
        if delta is None:                  # a schedule written before deltas existed
            cur["ax"], cur["az"] = as_expr(f["ax"]), as_expr(f["az"])
        else:
            if delta.get("conjugate") == "S":      # S̄ takes X̄ to Ȳ: az takes ax
                cur["az"] = xor_expr(cur["az"], cur["ax"])
            for part, other in (delta.get("copy") or {}).items():
                src = block_frame.get(other) or {"ax": (0, []), "az": (0, [])}
                cur[part] = xor_expr(cur[part], src[part])
            cur["ax"] = xor_expr(cur["ax"], as_expr(delta["ax"]))
            cur["az"] = xor_expr(cur["az"], as_expr(delta["az"]))
        history.append((float(f["t"]), f["block"], {k: v for k, v in cur.items()}))

    def frame_at(block: str, t: float) -> dict:
        """The frame the archive held for `block` at time `t`, in this branch."""
        out = {"ax": (0, []), "az": (0, [])}
        for ft, fb, state in history:
            if fb == block and ft <= t + 1e-6:
                out = state
        return out

    # An outcome as the archive reports it: the records the measuring gadget's flow report
    # names, corrected by the frame that block carried when it was measured.  In a branch
    # where a conditional correction ran, that frame is different -- and so is the outcome.
    outcome_recs: dict[int, list[int]] = {}
    outcome_const: dict[int, int] = {}
    for o in outcomes:
        if o.get("correct") is None and o.get("value") is not None:
            outcome_recs[o["instruction"]] = refs_records(o["value"].get("sources"))
            outcome_const[o["instruction"]] = int(o["value"].get("const", 0))
            continue
        f = _flow(lib, gir, o["event"], o["flow"])
        recs = [record_of[(o["event"], r)] for r in f["records"]]
        const = 0
        for block, qx, qz in (o.get("correct") or ()):
            fr = frame_at(block, float(o.get("at", 0.0)))
            for key, used in (("ax", qz), ("az", qx)):
                if not used:
                    continue
                c, rr = fr[key]
                const ^= c
                recs += rr
        outcome_recs[o["instruction"]] = recs
        outcome_const[o["instruction"]] = const
    report["archive"] = {
        "frames": len(gir.get("frames") or []),
        "software_paulis": sum(1 for f in (gir.get("frames") or []) if f.get("kind") == "pauli"),
        "guarded_frames": guarded_frames,
        "blocks": {b: {k: v[0] for k, v in fr.items()} for b, fr in block_frame.items()},
    }
    # `frames` is a span of KNOWN record identities: a declared frame ("this operator comes
    # out multiplied by (-1)^(that parity)") and, in a branch, the guard's own value.
    frames = _Span()
    frame_count = 0
    frame_sets: list[list[int]] = []
    for e in gir["events"]:
        if e[0] not in event_ops:
            continue
        op = lib[gir["leaves"][e[3]][0]].ops.get(e[4])
        for f in (op.logic.get("flows", {}).get("flows", []) if op else []):
            if f.get("role") == "logical" and f["kind"] == "frame" and f.get("records"):
                recs = [record_of[(e[0], r)] for r in f["records"]]
                c, v = value(recs)
                frames.add(v, c, sum(1 << r for r in set(recs)))
                frame_sets.append(recs)
                frame_count += 1
    report["frames"] = frame_count
    # in this branch the guards' outcomes are not free: they have the branch's values
    conditioned = 0
    if branch:
        cvar_ins = alg.cvar_instruction()
        # a branch the algorithm can never take is reported, not checked: conditioning on an
        # outcome the ideal circuit fixes the other way would make every claim provable
        itab0, _ = simulate(alg.ideal_circuit(branch)[0], choi=True)
        for name, val in branch.items():
            k = cvar_ins.get(name)
            rec = next((r for i, r in alg.ideal_circuit(branch)[3] if i == k), None)
            if rec is None or itab0.random[rec]:
                continue
            const, rest = itab0.record_expr(rec)
            if not rest and const != int(val):
                report.update(passed=True, unreachable=True, branch=dict(branch),
                              why=[f"this branch cannot occur: the algorithm always "
                                   f"measures {name} = {const}"],
                              seconds=round(time.time() - t_start, 3))
                return report
        for name, val in branch.items():
            k = cvar_ins.get(name)
            if k is None or k not in outcome_recs:
                continue
            c, v = value(outcome_recs[k])
            if frames.add(v, c ^ outcome_const[k] ^ int(val),
                          sum(1 << r for r in set(outcome_recs[k]))):
                conditioned += 1
        report["branch"] = dict(branch)
        report["conditioned_bits"] = conditioned

    # -- the ideal circuit --------------------------------------------------------------------
    ideal, inputs, alive, lrecs = alg.ideal_circuit(branch)
    itab, inn = simulate(ideal, choi=True)
    rec_ins = {k: ins for ins, k in lrecs}
    names = {ins.index: (ins.cvar or f"{ins.kind}@{ins.index}") for ins in alg.instructions}

    def label(ins_index):
        ins = alg.instructions[ins_index]
        if ins.kind == "read":
            what = f"{ins.basis}̄[{ins.blocks[0]}]"
        elif ins.kind in ("zz", "xx"):
            what = f"{ins.kind.upper()[0]}̄{ins.kind.upper()[1]}̄[{ins.blocks[0]},{ins.blocks[1]}]"
        else:
            what = ins.kind
        return f"{names[ins_index]}={what}" if ins.cvar else what

    def decoded(instrs):
        """The decoded value of a group of logical outcomes: (constant, records)."""
        c, v = 0, 0
        for j in instrs:
            cc, vv = value(outcome_recs[j])
            c ^= cc ^ outcome_const[j]
            v ^= vv
        return c, v

    def frame_sign(Q: dict) -> tuple[int, int]:
        """What the archive's frames do to the sign of the operator `Q` on those blocks."""
        c, v = 0, 0
        for q, letter in Q.items():
            fr = block_frame.get(q)
            if not fr:
                continue
            for key, used in (("ax", letter in "ZY"), ("az", letter in "XY")):
                if not used:
                    continue
                fc, frecs = fr[key]
                cc, vv = value(frecs)
                c ^= fc ^ cc
                v ^= vv
        return c, v

    cvar_of = {ins.index: ins.cvar for ins in alg.instructions if ins.cvar}
    ok = True
    missing = [ins for _, ins in rec_ins.items() if ins not in outcome_recs]
    if missing:
        report["why"].append(f"no decoded outcome for instructions {missing}")
        report["error"] = report["why"][-1]
        return report

    # relations
    for k, rnd in enumerate(itab.random):
        if rnd:
            continue
        c_ideal, rset = itab.record_expr(k)
        group = [k] + [j for j in range(len(itab.records)) if (rset >> j) & 1]
        # a bit this branch fixes is substituted into the constant rather than compared: in
        # the branch where the correction ran, "m ⊕ r = 1" is the statement "r = 0"
        fixed = [j for j in group if cvar_of.get(rec_ins[j]) in branch]
        group = [j for j in group if j not in fixed]
        for j in fixed:
            c_ideal ^= int(branch[cvar_of[rec_ins[j]]]) & 1
        if not group:
            if c_ideal:
                ok = False
                report["relations"].append(
                    {"relation": "this branch contradicts the algorithm", "ok": False,
                     "why": "the outcomes the guards read cannot take these values"})
            continue
        c_phys, v = decoded([rec_ins[j] for j in group])
        rv, rc, _ = frames.reduce(v, c_phys)
        text = " ⊕ ".join(label(rec_ins[j]) for j in sorted(group)) + f" = {c_ideal}"
        row = {"relation": text, "ok": not rv and rc == c_ideal}
        if rv:
            row["why"] = "not deterministic on the hardware, even allowing every declared frame"
        elif rc != c_ideal:
            row["why"] = f"holds with the opposite value ({rc})"
        ok &= row["ok"]
        report["relations"].append(row)

    # randomness
    r_ideal = sum(1 for rnd in itab.random if rnd)
    span = _Span()
    for k in range(len(itab.records)):
        v = decoded([rec_ins[k]])[1]
        rv, _, _ = frames.reduce(v)
        span.add(rv)
    r_phys = len(span.rows)
    # every bit a guard read is fixed in this branch, on both sides
    report["random_bits"] = {"ideal": r_ideal, "hardware": r_phys + conditioned,
                             "conditioned": conditioned}
    if r_phys + conditioned != r_ideal:
        ok = False
        report["why"].append(f"the hardware's outcomes carry {r_phys + conditioned} random "
                             f"bits, the ideal circuit's {r_ideal}")

    # flows from inputs to surviving blocks
    block_ions = {b: [f"g{g}" for g in info["ions"]] for b, info in gir["blocks"].items()}
    magic_ion = {b: f"g{info['magic_ion']}" for b, info in gir["blocks"].items() if "magic_ion" in info}
    lq = inputs + alive
    basis_vecs = []
    out_flows: list = []
    if lq:
        from ..surface import surface_code
        code = surface_code(3)
        sup = {s: code.logical_support(0, s) for s in ("X", "Z")}
        for letters in product("IXZY", repeat=len(lq)):
            if all(x == "I" for x in letters):
                continue
            P = {q: s for q, s in zip(inputs, letters[:len(inputs)]) if s != "I"}
            Q = {q: s for q, s in zip(alive, letters[len(inputs):]) if s != "I"}
            rx, rz, ny = pauli_bits(P, lambda q: inn + ideal.index[q])
            sx, sz, _ = pauli_bits(Q, lambda q: ideal.index[q])
            val = itab.value(rx ^ sx, rz ^ sz)
            if val is None or (val[1] & itab.hidden):
                continue
            vec = 0
            for i, s in enumerate(letters):
                vec |= ((s in "XY") << (2 * i)) | ((s in "ZY") << (2 * i + 1))
            if gf2.rank(basis_vecs + [vec]) == len(basis_vecs):
                continue
            basis_vecs.append(vec)
            c_ideal = val[0] ^ (ny & 1)
            lset = [itab.var_record[vv] for vv in bits(val[1])]
            # the same substitution as for the relations: a bit this branch fixes is part of
            # the statement, not of the comparison -- which is how a conditional correction
            # turns "X̄ → Ȳ ⊕ k" into the exact channel "X̄ → Ȳ"
            fixed_l = [j for j in lset if cvar_of.get(rec_ins[j]) in branch]
            lset = [j for j in lset if j not in fixed_l]
            for j in fixed_l:
                c_ideal ^= int(branch[cvar_of[rec_ins[j]]]) & 1
            # the same flow on the hardware
            hP = {magic_ion[q]: s for q, s in P.items()}
            hQ: dict[str, str] = {}
            for q, s in Q.items():
                ions = block_ions[q]
                for letter in ("X", "Z"):
                    if s in (letter, "Y"):
                        for idx in sup[letter]:
                            cur = hQ.get(ions[idx], "I")
                            hQ[ions[idx]] = {("I", "X"): "X", ("I", "Z"): "Z", ("X", "Z"): "Y",
                                             ("Z", "X"): "Y"}[(cur, letter)]
            out_flows.append((hQ, lset, c_ideal, P))
            hx, hz, hny = pauli_bits(hP, lambda q: n + flat.index[q])
            fx, fz, _ = pauli_bits(hQ, lambda q: flat.index[q])
            hval = tab.value(hx ^ fx, hz ^ fz)
            text = (" ".join(f"{s}̄[{q}]" for q, s in P.items()) or "1") + " → " + \
                   (" ".join(f"{s}̄[{q}]" for q, s in Q.items()) or "1") + \
                   "".join(f" ⊕ {label(rec_ins[j])}" for j in lset)
            row = {"flow": text}
            if hval is None or hval[1] & tab.hidden:
                row.update(ok=False, why="the hardware does not carry this operator there")
            else:
                c = hval[0] ^ (hny & 1)
                v = 0                       # the value's variables, as the records they are
                for var in bits(hval[1]):
                    v |= 1 << tab.var_record[var]
                for j in lset:
                    cj, vj = decoded([rec_ins[j]])
                    c ^= cj
                    v ^= vj
                fc, fv = frame_sign(Q)          # the correction the archive is holding
                c ^= fc
                v ^= fv
                rv, rc, _ = frames.reduce(v, c)
                row["ok"] = not rv and rc == c_ideal
                if rv:
                    row["why"] = "holds only up to records that are neither outcomes nor frames"
                elif rc != c_ideal:
                    row["why"] = "holds with the opposite sign"
            ok &= row["ok"]
            report["flows"].append(row)
        need = len(lq)
        if len(basis_vecs) != need:
            report["why"].append(f"the ideal circuit has {len(basis_vecs)} independent flows, "
                                 f"expected {need}")
    report["checked_in_s"] = round(time.time() - t_start, 3)

    # -- fault distance of the whole schedule -------------------------------------------------
    injections = sorted({str(e[4]) for e in gir["events"] if str(e[4]).startswith("inject")})
    if distance and not inputs and not injections:
        report["distance"] = _schedule_distance(flat, gir, outcome_recs, frame_sets, event_ops,
                                                itab, rec_ins, out_flows, alive)
        if not report["distance"].get("passed"):
            ok = False
            report["why"].append("schedule fault distance below 3: "
                                 + " + ".join(report["distance"].get("witness", [])[:2]))
    elif distance:
        report["distance"] = {"skipped": "the algorithm injects a state (" +
                              ", ".join(injections or ["inject"]) + "), and injection is not "
                              "fault-tolerant by design (distance 1)"}
    report["passed"] = bool(ok and not report["why"])
    report["seconds"] = round(time.time() - t_start, 3)
    return report


def _schedule_distance(flat, gir, outcome_recs, frame_sets, event_ops, itab, rec_ins,
                       out_flows, alive):
    """Distance of the whole schedule.  Blocks still alive at the end are read by ideal
    decoders (their stabilizers, then each output flow's logical operator), so a fault that
    corrupts a surviving block counts like one that corrupts an outcome."""
    t0 = time.time()
    from ..surface import surface_code
    code = surface_code(3)
    sup = {s: code.logical_support(0, s) for s in ("X", "Z")}
    exp = Circuit()
    exp.extend(flat, noisy=True)
    decoded = []
    for b in alive:
        ions = [f"g{g}" for g in gir["blocks"][b]["ions"]]
        for row in code.hx:
            exp.mpp({ions[q]: "X" for q in gf2.support(row)})
        for row in code.hz:
            exp.mpp({ions[q]: "Z" for q in gf2.support(row)})
    for hQ, lset, c_ideal, P in out_flows:
        if P:
            continue                       # only flows from nothing to a surviving block
        decoded.append((exp.mpp(hQ, label={"decode": True}), lset))
    tab, _ = simulate(exp, choi=True)
    expr = [tab.record_expr(r) for r in range(len(exp.records))]

    def value(recs):
        c, v = 0, 0
        for r in recs:
            rc, rv = expr[r]
            c ^= rc
            v ^= rv
        return c, v

    frames = _Span()
    for recs in frame_sets:
        c, v = value(recs)
        frames.add(v, c, sum(1 << r for r in set(recs)))
    W = []
    for k, rnd in enumerate(tab.random):
        if not rnd:
            _, rec = expr[k]
            W.append(rec | (1 << k))
    # logical insertions: X̄ and Z̄ on a block after the last op of every event it takes part in
    ion_block = {}
    for b, info in gir["blocks"].items():
        for g in info["ions"]:
            ion_block[f"g{g}"] = b
    last_op: dict[tuple[int, str], int] = {}
    for eid, ops in event_ops.items():
        for k in ops:
            for q in exp.ops[k].targets:
                b = ion_block.get(exp.qubits[q])
                if b is not None:
                    last_op[(eid, b)] = max(last_op.get((eid, b), -1), k)
    inserts: dict[int, list] = {}
    for (eid, b), k in last_op.items():
        ions = [f"g{g}" for g in gir["blocks"][b]["ions"]]
        for letter in ("X", "Z"):
            qs = tuple(exp.index[ions[i]] for i in sup[letter])
            inserts.setdefault(k, []).append((qs, tuple(letter for _ in qs)))
    prop_l = propagate(exp, custom=lambda k, op: inserts.get(k, ()))
    n_ins = len(prop_l.faults)
    flips_by_ins = [0] * n_ins
    for r, fl in enumerate(prop_l.flips):
        for f in bits(fl):
            flips_by_ins[f] |= 1 << r
    basis: list[tuple[int, int]] = []
    detectors = []
    for w in W:
        sig = 0
        for f in range(n_ins):
            if (w & flips_by_ins[f]).bit_count() & 1:
                sig |= 1 << f
        for bs, bw in basis:
            piv = bs.bit_length() - 1
            if (sig >> piv) & 1:
                sig ^= bs
                w ^= bw
        if sig:
            basis.append((sig, w))
        elif w:
            detectors.append(w)
    # observables: the ideal relations and the decoded output flows, with the frames they need
    observables = []
    unresolved = 0

    def observable(recs):
        bitset, c, v = 0, 0, 0
        for r in recs:
            bitset ^= 1 << r
            rc, rv = expr[r]
            c ^= rc
            v ^= rv
        rv, _, used = frames.reduce(v, c)
        return bitset ^ used if not rv else None

    for k, rnd in enumerate(itab.random):
        if rnd:
            continue
        _, rset = itab.record_expr(k)
        group = [k] + [j for j in range(len(itab.records)) if (rset >> j) & 1]
        o = observable([r for j in group for r in outcome_recs[rec_ins[j]]])
        if o is None:
            unresolved += 1
        else:
            observables.append(o)
    for mpp_rec, lset in decoded:
        o = observable([mpp_rec] + [r for j in lset for r in outcome_recs[rec_ins[j]]])
        if o is None:
            unresolved += 1
        else:
            observables.append(o)
    if not observables:
        return {"skipped": "this branch has no deterministic result to protect: every "
                           "outcome of it is random", "detectors": len(detectors),
                "seconds": round(time.time() - t0, 2)}
    prop = propagate(exp)
    witness = low_weight_logical(prop, detectors, observables)
    return {"faults": len(prop.faults), "detectors": len(detectors),
            "observables": len(observables), "logical_insertions": n_ins,
            "decoded_outputs": len(decoded), "unresolved": unresolved,
            "at_least": 3 if witness is None else None,
            "distance": None if witness is None else len(witness),
            "witness": [prop.faults[f].describe(exp) for f in (witness or [])],
            "passed": witness is None and not unresolved and bool(observables),
            "seconds": round(time.time() - t0, 2)}
