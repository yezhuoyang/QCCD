"""The logic verifier: stabilizer flows, spec completeness, fault distance.

Pure circuits, no hardware: these tests pin down that the checker accepts what is right and
rejects what is wrong, and (when stim is installed) that it agrees with stim.
"""

from __future__ import annotations

import random

import pytest

from qccd.gadget.logic.circuit import Circuit
from qccd.gadget.logic.experiment import (Block, LogicalFlow, build_experiment, distance_report,
                                          logical_pauli, to_stim_experiment)
from qccd.gadget.logic.faults import propagate
from qccd.gadget.logic.spec import Flow, Spec, verify_flows
from qccd.gadget.logic.statevec import qubit_state, run_postselected
from qccd.gadget.logic.tableau import bits, pauli_bits, simulate
from qccd.gadget.surface import Check, merged_patch, patch

try:
    import stim
except ImportError:          # the checker itself never needs stim
    stim = None


# ----------------------------------------------------------------------------- helpers


def se_round(p, data, anc, *, flip=None, drop_reset=False):
    """The textbook layered round; `flip` turns one (check, data) CNOT around."""
    c = Circuit(list(data) + [anc[ch.name] for ch in p.checks])
    if not drop_reset:
        c.add("R", [anc[ch.name] for ch in p.checks])
    xs = [anc[ch.name] for ch in p.checks if ch.basis == "X"]
    c.add("H", xs)
    for t in range(4):
        pairs = []
        for ch in p.checks:
            for (_, q), layer in zip(ch.corners, ch.layers):
                if layer != t:
                    continue
                pair = [anc[ch.name], data[q]] if ch.basis == "X" else [data[q], anc[ch.name]]
                if flip == (ch.name, q):
                    pair.reverse()
                pairs += pair
        if pairs:
            c.add("CX", pairs)
    c.add("H", xs)
    c.add("M", [anc[ch.name] for ch in p.checks])
    return c


def se_spec(p, data, code):
    blk = Block(code, data, "A")
    flows = []
    for ch in p.checks:
        P = {data[q]: ch.basis for q in ch.support}
        flows += [Flow(f"{ch.name} measured", P, {}, "measure"),
                  Flow(f"{ch.name} holds", {}, dict(P), "frame")]
    for L in ("X", "Z"):
        P = logical_pauli(blk, 0, L)
        flows.append(Flow(f"{L} kept", P, dict(P), "exact"))
    return Spec("se", list(data), list(data), flows)


@pytest.fixture(scope="module")
def surface():
    p = patch(3, 3, name="surface_d3")
    data = [f"d{q}" for q in range(p.n)]
    anc = {ch.name: f"a{ch.name}" for ch in p.checks}
    return p, p.code, data, anc


# ----------------------------------------------------------------------------- the tableau


def test_single_gate_flows():
    c = Circuit(["q"])
    c.add("H", ["q"])
    tab, n = simulate(c)
    for inp, out in (("X", "Z"), ("Z", "X")):
        rx, rz, _ = pauli_bits({"q": inp}, lambda q: n)
        sx, sz, _ = pauli_bits({"q": out}, lambda q: 0)
        assert tab.value(rx ^ sx, rz ^ sz) == (0, 0)
    # Y -> -Y under H: the reference carries Yᵀ = -Y, so the product's sign is the flow's
    rx, rz, ny = pauli_bits({"q": "Y"}, lambda q: n)
    sx, sz, _ = pauli_bits({"q": "Y"}, lambda q: 0)
    const, var = tab.value(rx ^ sx, rz ^ sz)
    assert var == 0 and (const ^ (ny & 1)) == 1


def test_measurement_and_reset():
    c = Circuit(["q"])
    c.add("M", ["q"])
    tab, n = simulate(c)
    rx, rz, _ = pauli_bits({"q": "Z"}, lambda q: n)
    const, var = tab.value(rx, rz)                      # Z before -> the record
    assert tab.as_records(var) == [0] and const == 0
    c = Circuit(["q"])
    c.add("R", ["q"])
    tab, n = simulate(c)
    rx, rz, _ = pauli_bits({"q": "Z"}, lambda q: n)
    assert tab.value(rx, rz) is None or tab.as_records(tab.value(rx, rz)[1]) is None
    sx, sz, _ = pauli_bits({"q": "Z"}, lambda q: 0)
    assert tab.value(sx, sz) == (0, 0)                  # after a reset, Z = +1 exactly


@pytest.mark.skipif(stim is None, reason="stim not installed")
def test_tableau_agrees_with_stim_on_random_circuits():
    rng = random.Random(7)
    g1 = ["H", "S", "S_DAG", "X", "Y", "Z"]
    checked = 0
    for _ in range(120):
        nq = rng.randint(2, 5)
        c = Circuit([f"q{i}" for i in range(nq)])
        for _ in range(rng.randint(1, 25)):
            r = rng.random()
            if r < 0.35:
                a, b = rng.sample(range(nq), 2)
                c.add(rng.choice(["CX", "CZ"]), [a, b])
            elif r < 0.7:
                c.add(rng.choice(g1), [rng.randrange(nq)])
            elif r < 0.82:
                c.add("M", [rng.randrange(nq)])
            elif r < 0.92:
                c.add("R", [rng.randrange(nq)])
            else:
                qs = rng.sample(range(nq), rng.randint(1, min(3, nq)))
                c.mpp({q: rng.choice("XYZ") for q in qs})
        sc = stim.Circuit(c.to_stim())
        tab, n = simulate(c)
        for _ in range(6):
            S = 0
            for i in range(2 * n):
                if rng.random() < 0.4:
                    S |= 1 << (2 * n + i)
            if not S:
                continue
            const, var, x, z = tab._product(S)
            P, Q, ny = {}, {}, 0
            for q in range(n):
                xb, zb = (x >> q) & 1, (z >> q) & 1
                if xb or zb:
                    Q[q] = "Y" if xb and zb else ("X" if xb else "Z")
                xb, zb = (x >> (n + q)) & 1, (z >> (n + q)) & 1
                if xb or zb:
                    P[q] = "Y" if xb and zb else ("X" if xb else "Z")
                    ny += xb and zb
            recs = tab.as_records(var)
            lhs = "*".join(f"{p}{q}" for q, p in sorted(P.items())) or "1"
            rhs = "*".join(f"{p}{q}" for q, p in sorted(Q.items())) or "1"
            if recs is None:
                assert not sc.has_flow(stim.Flow(f"{lhs} -> {rhs}"), unsigned=True)
                continue
            sign = "-" if (const ^ (ny & 1)) else ""
            text = f"{sign}{lhs} -> {rhs}" + "".join(f" xor rec[{r}]" for r in recs)
            assert sc.has_flow(stim.Flow(text)), text
            checked += 1
    assert checked > 200


# ----------------------------------------------------------------------------- specs


def test_textbook_round_meets_a_complete_spec(surface):
    p, code, data, anc = surface
    rep = verify_flows(se_round(p, data, anc), se_spec(p, data, code))
    assert rep["passed"] and rep["complete"] and rep["rank"] == 18


def test_an_incomplete_spec_does_not_pass(surface):
    p, code, data, anc = surface
    spec = se_spec(p, data, code)
    spec.flows = [f for f in spec.flows if "kept" not in f.name]     # drop the logicals
    rep = verify_flows(se_round(p, data, anc), spec)
    assert not rep["complete"] and not rep["passed"]


def test_a_cnot_turned_around_breaks_the_flows(surface):
    p, code, data, anc = surface
    rep = verify_flows(se_round(p, data, anc, flip=("X1", 4)), se_spec(p, data, code))
    assert not rep["passed"]
    assert any(not f["ok"] for f in rep["flows"])


def test_a_missing_reset_breaks_the_flows(surface):
    p, code, data, anc = surface
    rep = verify_flows(se_round(p, data, anc, drop_reset=True), se_spec(p, data, code))
    assert not rep["passed"]


# ----------------------------------------------------------------------------- distance


def _hook_unsafe(p):
    q = patch(p.rows, p.cols, name=p.name)
    checks = []
    for ch in q.checks:
        if ch.basis == "X" and ch.weight == 4:
            cm = dict(ch.corners)
            order = ("NW", "SW", "NE", "SE")
            checks.append(Check(ch.name, ch.basis, ch.i, ch.j, tuple((k, cm[k]) for k in order),
                                (0, 1, 2, 3)))
        else:
            checks.append(ch)
    q.checks = checks
    return q


def _memory_flows():
    return [LogicalFlow("X", [("A", 0, "X")], [("A", 0, "X")]),
            LogicalFlow("Z", [("A", 0, "Z")], [("A", 0, "Z")])]


def test_textbook_round_has_distance_three(surface):
    p, code, data, anc = surface
    blk = Block(code, data, "A")
    rep = distance_report(se_round(p, data, anc), blocks_in=[blk], blocks_out=[blk],
                          flows=_memory_flows())
    assert rep["passed"] and rep["at_least"] == 3


def test_hook_unsafe_order_is_caught_with_a_witness(surface):
    p, code, data, anc = surface
    bad = _hook_unsafe(p)
    blk = Block(code, data, "A")
    circ = se_round(bad, data, anc)
    assert verify_flows(circ, se_spec(p, data, code))["passed"]      # still computes the right thing
    rep = distance_report(circ, blocks_in=[blk], blocks_out=[blk], flows=_memory_flows())
    assert not rep["passed"] and rep["distance"] == 2 and len(rep["witness"]) == 2


@pytest.mark.skipif(stim is None, reason="stim not installed")
def test_distance_agrees_with_stim(surface):
    p, code, data, anc = surface
    blk = Block(code, data, "A")
    for circ, want in ((se_round(p, data, anc), 3), (se_round(_hook_unsafe(p), data, anc), 2)):
        e = build_experiment(circ, blocks_in=[blk], blocks_out=[blk], flows=_memory_flows())
        sc = stim.Circuit(to_stim_experiment(e))
        found = sc.search_for_undetectable_logical_errors(
            dont_explore_detection_event_sets_with_size_above=6,
            dont_explore_edges_with_degree_above=6,
            dont_explore_edges_increasing_symptom_degree=False, canonicalize_circuit_errors=True)
        assert len(found) == want


def test_merged_patches_measure_the_logical_product():
    for kind in ("ZZ", "XX"):
        mp, where = merged_patch(3, kind)
        assert mp.code.valid() and mp.code.k == 1
        seam = set(where["seam"])
        letter = kind[0]
        product = 0
        for ch in mp.checks:
            if ch.name in where["measured"]:
                for q in ch.support:
                    product ^= 1 << q
        small = patch(3, 3)
        want = 0
        for q in small.logical(letter):
            want ^= 1 << where["A"][q]
            want ^= 1 << where["B"][q]
        # equal to L̄_A L̄_B up to the separate patches' own stabilizers of that type
        stabs = []
        for side in ("A", "B"):
            for ch in small.checks:
                if ch.basis == letter:
                    stabs.append(sum(1 << where[side][q] for q in ch.support))
        from qccd.gadget import gf2
        assert gf2.in_span(stabs, product ^ want)
        assert not (product & sum(1 << q for q in seam))


def test_state_vector_and_z_faults_on_the_15_to_1_circuit():
    from qccd.gadget.leaves.factory import rm15_circuit
    gates, out = rm15_circuit()
    c = Circuit([f"f{i}" for i in range(15)])
    for g in gates:
        if g[0] == "CX":
            c.add("CX", [g[1], g[2]])
        elif g[0] == "reset":
            c.add("R", [g[1]])
        elif g[0] == "measure":
            c.add("M", [g[1]])
        else:
            c.add(g[0], [g[1]])
    st, p = run_postselected(c)
    a0, a1 = qubit_state(st, out)
    import cmath
    import math
    assert abs(p - 1) < 1e-9
    target = (1 / math.sqrt(2), cmath.exp(-1j * math.pi / 4) / math.sqrt(2))
    assert abs(target[0].conjugate() * a0 + target[1].conjugate() * a1) ** 2 > 1 - 1e-9
    tq = [(k, t) for k, op in enumerate(c.ops) if op.name == "T" for t in op.targets]
    prop = propagate(c, custom=lambda k, op: [((t,), ("Z",)) for kk, t in tq if kk == k])
    syn = []
    for f in range(len(prop.faults)):
        s = sum(((fl >> f) & 1) << r for r, fl in enumerate(prop.flips))
        syn.append((s, ((prop.final_x[out] | prop.final_z[out]) >> f) & 1))
    assert all(s for s, _ in syn)
    assert all(syn[i][0] ^ syn[j][0] for i in range(15) for j in range(i + 1, 15))
    undetected = [(i, j, k) for i in range(15) for j in range(i + 1, 15) for k in range(j + 1, 15)
                  if not (syn[i][0] ^ syn[j][0] ^ syn[k][0])]
    assert len(undetected) == 35
    assert all(syn[i][1] ^ syn[j][1] ^ syn[k][1] for i, j, k in undetected)
