"""Leaf gadgets, synthesis and the hierarchy checks (docs/GADGETS.md §5, §7, §8).

The leaves are built and characterized once for the module (about fifteen seconds, most of
it compiling the bb72 syndrome round), into a cache shared with `test_gadget_page.py`.

Every G-rule is also broken on purpose: each mutation below corrupts one synthesized
schedule in the one way its rule exists to catch, and the rule must fail on it.
"""

from __future__ import annotations

import copy

import pytest

from qccd.gadget.checks import RULES, check
from qccd.gadget.designs import processor
from qccd.gadget.library import LeafLibrary
from qccd.gadget.logicq import parse
from qccd.gadget.programs import declarations, showcase
from qccd.gadget.synth import synthesize


@pytest.fixture(scope="module")
def leaves(tmp_path_factory):
    return LeafLibrary(tmp_path_factory.getbasetemp() / "gadget_cache", log=None)


@pytest.fixture(scope="module")
def small(leaves):
    prog = parse(showcase(4), name="showcase4")
    lib = processor(leaves, prog.code("q0"), 1, 2)
    gir = synthesize(prog, lib, leaves)
    return prog, lib, gir


# ------------------------------------------------------------------------ leaves


def test_every_beta_leaf_op_is_verified(small, leaves):
    _, lib, _ = small
    ops = [(m.name, op) for m in lib.masters.values() if m.kind == "leaf" for op in m.ops.values()]
    assert {name for name, _ in ops} == {"mem_bb72", "tcx72", "res4", "xjunc", "fac_t15"}
    bad = [(name, op.name, op.rules["failed"], op.notes) for name, op in ops
           if op.status != "verified"]
    assert not bad
    for _, op in ops:
        assert "R10" in op.rules["skipped"]          # no certificate: said, not hidden


def test_memory_cycle_is_a_full_syndrome_round_that_returns_home(small):
    _, lib, _ = small
    cycle = lib["mem_bb72"].ops["cycle"]
    assert cycle.metrics["gates_2q"] == 432            # 72 checks x 6 contacts
    assert cycle.transfers == []
    assert cycle.duration_us > 0


def test_bundles_come_back_in_reverse_order(small):
    _, lib, _ = small
    mem = lib["mem_bb72"]
    out = mem.ops["emit"].transfer("bus", "out").ions
    back = mem.ops["absorb"].transfer("bus", "in").ions
    assert sorted(out) == sorted(f"d{i}" for i in range(1, 73))
    assert back == list(reversed(out))
    st = lib["tcx72"].ops["cx"]
    for port, prefix in (("a", "c"), ("b", "t")):
        assert st.transfer(port, "in").ions == [f"{prefix}{j}" for j in range(72)]
        assert st.transfer(port, "out").ions == [f"{prefix}{j}" for j in reversed(range(72))]


def test_a_visit_touches_exactly_the_logical_support(small, leaves):
    prog, lib, _ = small
    code = prog.code("q0")
    key = leaves.ensure_visit(code, 3, "Z")
    op = lib["mem_bb72"].ops[key]
    assert op.status == "verified"
    assert op.metrics["gates_2q"] == len(code.logical_support(3, "Z"))


@pytest.mark.parametrize("length", [1, 4, 10])
def test_channel_transit_is_replayed_not_assumed(leaves, length):
    ref = leaves.channel_timing(length, 1)
    assert ref["status"] == "verified"
    assert ref["transit_us"] == pytest.approx((length + 1) * 5.0)   # one shuttle per hop


def test_the_factory_distills_with_the_rm15_circuit(small):
    from qccd.gadget.leaves.factory import rm15_circuit
    _, lib, _ = small
    gates, out = rm15_circuit()
    produce = lib["fac_t15"].ops["produce"]
    assert produce.metrics["gates_2q"] == sum(1 for g in gates if g[0] == "CX") == 66
    assert produce.metrics["measures"] == 14 and produce.metrics["resets"] == 15
    assert produce.transfers == []                   # the state waits inside the factory
    consume = lib["fac_t15"].ops["consume"]
    assert [(t.port, t.dir) for t in consume.transfers] == [("port", "in"), ("port", "out")]


def test_the_rm15_circuit_is_the_encoder_twice_around_a_t_layer():
    from qccd.gadget.leaves.factory import rm15_circuit
    gates, out = rm15_circuit()
    cx = [g for g in gates if g[0] == "CX"]
    t_at = next(i for i, g in enumerate(gates) if g[0] == "T")
    before = [g for g in gates[:t_at] if g[0] == "CX"]
    after = [g for g in gates[t_at:] if g[0] == "CX"]
    assert sorted(after[:len(before)]) == sorted(before)          # the encoder, undone
    # every encoder CX runs pivot -> non-pivot, so the fan-out commutes and is self-inverse
    pivots = {g[1] for g in gates if g[0] == "H" and gates.index(g) < t_at}
    assert all(c in pivots and t not in pivots for _, c, t in before)
    assert sum(1 for g in gates if g[0] == "measure") == 14 and out not in {
        g[1] for g in gates if g[0] == "measure"}


# ------------------------------------------------------------------------ synthesis


def test_a_t_gate_is_one_messenger_through_block_and_factory(leaves):
    src = "\n".join(declarations(2)) + "\nmagic T q0[3]\n"
    prog = parse(src)
    lib = processor(leaves, prog.code("q0"), 1, 1)
    gir = synthesize(prog, lib, leaves)
    assert gir["refused"] == []
    ops = [e[4] for e in sorted(gir["events"], key=lambda e: e[1]) if e[6] == 0 and e[2] > e[1]]
    core = [op for op in ops if op != "pass"]
    assert core == ["produce", "release.Z", "visit.Z3", "consume", "accept.Z"]
    messenger = {g for e in gir["events"] if e[6] == 0 for _, g in e[8] if isinstance(g, int)}
    assert len(messenger) == 1
    res = check(gir, lib, leaves)
    assert res["failed"] == []





def test_the_small_showcase_passes_every_hierarchy_check(small, leaves):
    _, lib, gir = small
    res = check(gir, lib, leaves)
    assert res["failed"] == [] and res["skipped"] == {}
    assert sorted(res["passed"]) == sorted(RULES)
    assert gir["refused"] == []


def test_blocks_run_a_cycle_between_logical_ops(small):
    _, _, gir = small
    mem = gir["blocks"]["q0"]["leaf"]
    ops = sorted((e for e in gir["events"] if e[3] == mem and e[2] > e[1]), key=lambda e: e[1])
    logical = [e for e in ops if e[4] != "cycle"]
    groups = {}
    for e in logical:
        groups.setdefault(e[7] or e[6], []).append(e)
    spans = sorted((min(e[1] for e in g), max(e[2] for e in g)) for g in groups.values())
    for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
        between = [e for e in ops if e[4] == "cycle" and a1 - 1e-6 <= e[1] and e[2] <= b0 + 1e-6]
        assert between, f"no extraction cycle between {a1} and {b0}"


def test_illegal_transversal_is_refused_with_logicqs_reason(leaves):
    src = "\n".join(declarations(2)) + "\nblockTransversal q0 H\nLogical X q1[0]\n"
    prog = parse(src)
    lib = processor(leaves, prog.code("q0"), 1, 1)
    gir = synthesize(prog, lib, leaves)
    assert [r[0] for r in gir["refused"]] == [0]
    assert "checkTransversal" in gir["refused"][0][1]
    res = check(gir, lib, leaves)
    assert res["failed"] == ["G7"]


def test_cnot_between_blocks_without_a_station_is_refused(leaves):
    src = "\n".join(declarations(4)) + "\ntransversalCNOTBatch q0 q2\n"
    prog = parse(src)
    lib = processor(leaves, prog.code("q0"), 1, 2)
    gir = synthesize(prog, lib, leaves)
    assert gir["refused"] and "share no transversal-CNOT station" in gir["refused"][0][1]


def test_disjoint_instructions_run_concurrently(leaves):
    src = "\n".join(declarations(4)) + "\ntransversalCNOTBatch q0 q1\ntransversalCNOTBatch q2 q3\n"
    prog = parse(src)
    lib = processor(leaves, prog.code("q0"), 1, 2)
    gir = synthesize(prog, lib, leaves)
    spans = {}
    for e in gir["events"]:
        if e[6] >= 0:
            s = spans.get(e[6], [e[1], e[2]])
            spans[e[6]] = [min(s[0], e[1]), max(s[1], e[2])]
    assert spans[0][0] < spans[1][1] and spans[1][0] < spans[0][1]


# ------------------------------------------------------------------------ the checks can fail


def _failing(gir, lib, leaves):
    return set(check(gir, lib, leaves)["failed"])


def test_g1_catches_a_bundle_in_the_wrong_order(small, leaves):
    _, lib, gir = small
    bad = copy.deepcopy(gir)
    ev = next(e for e in bad["events"] if e[4] in ("cx", "xc"))
    ev[8][0], ev[8][1] = ev[8][1], ev[8][0]
    assert "G1" in _failing(bad, lib, leaves)


def test_g2_catches_an_ion_that_never_comes_home(small, leaves):
    _, lib, gir = small
    bad = copy.deepcopy(gir)
    last = max((c for c in bad["carries"] if c[8] == "messenger"), key=lambda c: c[4])
    bad["carries"].remove(last)
    assert "G2" in _failing(bad, lib, leaves)


def test_g2_catches_an_ion_leaving_before_it_arrived(small, leaves):
    _, lib, gir = small
    bad = copy.deepcopy(gir)
    # the station hands the first returning ion back before the memory's emit even started
    back = next(c for c in bad["carries"] if c[8] == "data" and c[3] in ("a", "b"))
    back[4] = 0.0
    assert "G2" in _failing(bad, lib, leaves)


def test_g3_catches_an_overfull_instance(small, leaves):
    _, lib, gir = small
    tight = copy.deepcopy(lib)
    tight.masters["res4"].capacity = 2
    assert "G3" in _failing(gir, tight, leaves)


def test_g4_catches_a_channel_holding_more_ions_than_sites(small, leaves):
    _, lib, gir = small
    bad = copy.deepcopy(gir)
    busy = max(bad["nets"], key=lambda n: sum(len(c[7]) for c in bad["carries"] if c[1] == n))
    bad["nets"][busy][4] = 1
    assert "G4" in _failing(bad, lib, leaves)


def test_g5_catches_two_ops_at_once(small, leaves):
    _, lib, gir = small
    bad = copy.deepcopy(gir)
    ev = next(e for e in bad["events"] if e[4] == "emit")
    dup = list(ev)
    dup[1], dup[2] = ev[1] + 1.0, ev[2] + 1.0
    bad["events"].append(dup)
    assert "G5" in _failing(bad, lib, leaves)


def test_g6_catches_an_ion_past_the_loss_limit(small, leaves):
    _, lib, gir = small
    hot = copy.deepcopy(lib)
    hot.masters["tcx72"].ops["cx"].transfers[-1].quanta = 1e4
    assert "G6" in _failing(gir, hot, leaves)


def test_g7_catches_an_instruction_with_no_event(small, leaves):
    _, lib, gir = small
    bad = copy.deepcopy(gir)
    bad["events"] = [e for e in bad["events"] if e[6] != 3]
    bad["carries"] = [c for c in bad["carries"] if c[9] != 3]
    assert "G7" in _failing(bad, lib, leaves)


def test_g8_catches_a_block_left_without_extraction(small, leaves):
    _, lib, gir = small
    bad = copy.deepcopy(gir)
    bad["events"] = [e for e in bad["events"] if e[4] != "cycle"]
    assert "G8" in _failing(bad, lib, leaves)


def test_g9_catches_a_failed_leaf_op_in_use(small, leaves):
    _, lib, gir = small
    broken = copy.deepcopy(lib)
    broken.masters["xjunc"].ops["pass"].status = "failed"
    assert "G9" in _failing(gir, broken, leaves)
