"""FormalRV's Gidney 2025 by lattice surgery (`qccd.fullstack.scale.gidney_surgery`, docs/FULLSTACK_PLAN.md S10).  What must hold: every
product the listing measures is accounted for, in its order, one letter on its floorplan, on tiles nobody else holds at that time; a
patch's merge rounds are the listing's rounds; and no ion is virtual, a merge runs among the machine's other ions at rest."""
from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
sys.path.insert(0, str(REPO / "tests"))
sys.setrecursionlimit(100000)

from qccd.fullstack.scale import gidney_surgery as S
from qccd.fullstack.scale import layer as L
from qccd.fullstack.scale import programs as PR
from qccd.fullstack.scale.gidney import Anc
from qccd.fullstack.scale.gidney_plan import ROUND_US
from qccd.qec.experiments import corridor_merge, ideal_stim

OUT = REPO / "out" / "s10"
needs_listing = pytest.mark.skipif(not (OUT / "g25.sysprog").exists(), reason="the listing is built by tools/gidney_machine.py")


@pytest.fixture(scope="module")
def reader():
    import gidney_concrete as GC
    return GC._reader(OUT)


@pytest.fixture(scope="module")
def solved(reader):
    return S.labels(reader[1])


@pytest.fixture(scope="module")
def plan(reader, solved):
    return S.SurgeryPlan(reader[1], solved[0], pitch=3, band=2, inner=1, gap=0)


@needs_listing
def test_no_product_is_refused_by_the_labelling(solved):
    """Wires of one product can always be held the same way round; only wire pairs through a request's patch ever ask otherwise."""
    assert solved[1]["hard"] == []
    assert 0 < solved[1]["broken"] < solved[1]["edges"]


def _count(plan, key, memo) -> Counter:
    if key in memo:
        return memo[key]
    out: Counter = Counter()
    items = plan.reader.flat(key) if key is not None else plan.reader.program()
    for it in items:
        if it[0] == "call":
            out.update(_count(plan, it[1], memo))
        elif it[0] == "rep":
            out["listing products"] += 1
        elif it[0] == "rounds":
            out["listing rounds"] += it[1]
    for it in plan._resolve(key):
        if it[0] == "work":
            out["plan " + it[4]] += 1
            if it[4] == "rounds":
                out["plan rounds counted"] += round(it[1] / ROUND_US)
    memo[key] = out
    return out


@needs_listing
def test_every_product_is_a_merge_or_a_readout(plan):
    """16,589,428,495 products: each one merge, or at the end of a patch its readout; H̄ and Ȳ only add |Ȳ>s, each one merge and one
    readout."""
    c = _count(plan, None, {})
    assert c["listing products"] == 16_589_428_495
    read_for_product = c["plan read"] - _reader_reads(plan)
    y = c["plan |Y+>"] - _reader_y(plan)
    assert read_for_product > y > 0
    assert c["plan merge"] == c["listing products"] - (read_for_product - y) + y


@needs_listing
def test_a_merges_rounds_are_exactly_the_listings_and_the_others_stay(plan):
    """After a product the listing writes its repetitions' count of rounds on each patch, and nothing else: those are the merge's own.
    Every other round the listing writes is still in the plan."""
    seen, stack, taken = set(), [None], 0
    per_frame = {}
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        items = plan.reader.flat(key) if key is not None else plan.reader.program()
        absorbed = 0
        for i, it in enumerate(items):
            if it[0] == "call":
                stack.append(it[1])
            elif it[0] == "rep":
                inside = {p for p, _ in it[2]}
                per = Counter()
                j = i + 1
                while j < len(items) and items[j][0] == "rounds" and items[j][2] in inside:
                    per[items[j][2]] += items[j][1]
                    j += 1
                assert per == {p: it[1] for p in inside}, (key, i, it[:2])
                absorbed += sum(per.values())
        per_frame[key] = absorbed
    c = _count(plan, None, {})

    def total(key, memo={}):
        if key in memo:
            return memo[key]
        items = plan.reader.flat(key) if key is not None else plan.reader.program()
        memo[key] = per_frame[key] + sum(total(it[1]) for it in items if it[0] == "call")
        return memo[key]
    assert c["plan rounds counted"] == c["listing rounds"] - total(None)


def _walk_reader(plan, kind, key=None, memo=None, test=lambda it: True) -> int:
    memo = {} if memo is None else memo
    if key in memo:
        return memo[key]
    n = 0
    items = plan.reader.flat(key) if key is not None else plan.reader.program()
    i = 0
    while i < len(items):
        it = items[i]
        if it[0] == "call":
            n += _walk_reader(plan, kind, it[1], memo, test)
        elif it[0] == kind and test(it):
            n += 1
            if kind == "read":      # the plan runs a patch's consecutive reads as one, as `gidney_plan.Plan`'s
                while i + 1 < len(items) and items[i + 1][0] == "read" and items[i + 1][1] == it[1]:
                    i += 1
        i += 1
    memo[key] = n
    return n


def _reader_reads(plan) -> int:
    return _walk_reader(plan, "read")


def _reader_y(plan) -> int:
    return _walk_reader(plan, "magic", test=lambda it: it[1] == "|Y+>")


@needs_listing
def test_a_merge_is_one_letter_and_holds_its_logic(plan, reader):
    """Every program one prime needs is a corridor merge that measures its product and keeps distance, checked at small distance."""
    import gidney_concrete as GC
    shapes = plan.merges(GC._keys(*reader))
    assert len(shapes) < 1000
    for shape in sorted(shapes, key=lambda s: len(s[3]))[:60]:
        _, letter, pats, cor = shape
        assert letter in "XZ"
        e = corridor_merge(3, letter, [(tuple(t), s) for t, s in pats], {tuple(c) for c in cor})
        c = ideal_stim(e)
        c.detector_error_model()
        assert not c.compile_detector_sampler().sample(8, append_observables=True).any()
        assert len(ideal_stim(e, 1e-3).shortest_graphlike_error(ignore_ungraphlike_errors=True)) == 3


@needs_listing
def test_no_tile_or_patch_is_used_twice_at_once(plan, reader):
    """A whole adder, written out flat: each resource's intervals are disjoint, and all work on a wire starts on the machine's clock."""
    from qccd.fullstack.scale.gidney_plan import Times
    lst, R = reader
    key = R.key("gidneyAddNoCarry", {"n": 32, "rv_database": 1280})
    used = plan.intervals(key, Times(), {})
    assert sum(1 for r in used if r[0] == "tile") > 10
    for r, spans in used.items():
        if r[0] == "patch":         # a request's name is its instance's own: its tile is what is physical
            continue
        spans = sorted(spans)
        for (a, b), (c, d) in zip(spans, spans[1:]):
            assert c >= b - 1e-6, (r, (a, b), (c, d))
        if r[0] == "wire":
            for a, _ in spans:
                assert abs(a / plan.clock - round(a / plan.clock)) < 1e-6, (r, a)


@pytest.fixture(scope="module")
def slotted(reader, solved):
    from gidney_surgery import FLOOR
    return S.SurgeryPlan(reader[1], solved[0], **FLOOR)


@needs_listing
def test_written_out_flat_on_the_slot_floorplan_nothing_is_used_twice_at_once(slotted, reader):
    """`Plan.flat`, the scheduler below a prime: an adder with its cultivated factories, every instance written out, each item as soon as it can run.
    Each resource's intervals are disjoint and all work on a wire starts on the clock; and it is never later than placing instances whole."""
    from qccd.fullstack.scale.gidney_plan import Times
    lst, R = reader
    key = R.key("gidneyAddNoCarry", {"n": 32, "rv_database": 1280})
    used: dict = {}
    end = slotted.flat(key, Times(), {}, out=used)[0]
    assert sum(1 for r in used if r[0] == "tile") > 10
    for r, spans in used.items():
        spans = sorted(spans)
        for (a, b), (c, d) in zip(spans, spans[1:]):
            assert c >= b - 1e-6, (r, (a, b), (c, d))
        if r[0] == "wire":
            for a, _ in spans:
                assert abs(a / slotted.clock - round(a / slotted.clock)) < 1e-6, (r, a)
    assert end <= slotted.schedule(key, {}, {})[0] + 1e-6


@needs_listing
def test_the_trace_is_the_schedule_flat_places(slotted, reader):
    """`Plan.flat(trace=)`, what the page draws: every work item with the start and end the schedule gives it -- the same intervals on every
    tile as `out` records -- and every preparation and readout naming the compiled program it runs (its basis)."""
    from qccd.fullstack.scale.gidney_plan import Times
    lst, R = reader
    key = R.key("gidneyAddNoCarry", {"n": 32, "rv_database": 1280})
    used: dict = {}
    seen: dict = {}
    kinds: set = set()

    def trace(path, it, t, e):
        assert path[0] == key and e >= t
        for r in it[2]:
            if r[0] == "tile":
                seen.setdefault(r, []).append((t, e))
        if it[4] in ("prep", "read"):
            assert it[5][0] == it[4] and it[5][1] in "ZXYT", it
            kinds.add(it[5])
    end = slotted.flat(key, Times(), {}, out=used, trace=trace)[0]
    assert {r: sorted(v) for r, v in seen.items()} == {r: sorted(v) for r, v in used.items() if r[0] == "tile"}
    assert max(e for v in seen.values() for _, e in v) <= end + 1e-6
    assert ("read", "Y") in kinds and ("prep", "Z") in kinds


@needs_listing
def test_the_clock_holds_for_a_merge(plan, reader):
    lst, R = reader
    key = R.key("gidneyAddNoCarry", {"n": 32, "rv_database": 1280})
    assert plan._on_clock(S.ROUND_IN_MERGE_US * 3) == pytest.approx(S.ROUND_IN_MERGE_US * 3)
    assert plan._on_clock(S.ROUND_IN_MERGE_US * 3 + 1) == pytest.approx(S.ROUND_IN_MERGE_US * 4)
    assert plan.schedule(key, {}, {})[0] > 0


def test_a_patch_at_the_end_of_its_life_is_read_not_merged():
    """`M(Z̄)` then release: FormalRV writes a one-patch merge, and it is the patch's readout."""
    class Stub:
        def flat(self, key):
            return [("prep", 0, 1, "Z"), ("rep", 11, ((Anc(0, 0), "Z"),), 3), ("rounds", 11, Anc(0, 0)), ("decode", 11), ("free", 0)]

        def program(self):
            return self.flat(None)
    plan = S.SurgeryPlan(Stub(), {})
    got = [it for it in plan._resolve(None) if it[0] == "work"]
    assert [it[4] for it in got] == ["prep", "read"]


def test_a_patch_met_both_ways_round_takes_hbar_as_three_merges():
    """A request's patch: Z̄ with a wire's row, X̄ with another wire's row: FormalRV's H̄ in between, S̄, √X̄, S̄, each a |Ȳ> and a merge."""
    class Stub:
        def flat(self, key):
            a = Anc(0, 0)
            return [("prep", 0, 1, "Z"), ("rep", 11, ((a, "Z"), (1, "Z")), 3), ("rounds", 11, a), ("rounds", 11, 1),
                    ("rep", 11, ((a, "X"), (4, "Z")), 3), ("rounds", 11, a), ("rounds", 11, 4), ("read", a), ("free", 0)]

        def program(self):
            return self.flat(None)
    plan = S.SurgeryPlan(Stub(), {})
    kinds = Counter(it[4] for it in plan._resolve(None) if it[0] == "work")
    assert plan.hbars[2] == 1
    assert kinds["|Y+>"] == 3
    assert kinds["merge"] == 2 + 3


def test_a_merge_among_the_machines_other_ions_at_rest_holds_every_rule_and_r10(tmp_path):
    """No ion is virtual: seams and neighbouring tiles full of ions at rest change neither the program's time nor its waveforms."""
    group = L.facing(1)
    idle = [(0, 0), (0, 1), (2, 0), (2, 1)]
    plain = L.build([group], idle)
    full = L.build([group], idle, full=True)
    assert len(full.prog.instructions[0].placement) > len(plain.prog.instructions[0].placement)
    assert L.waveforms(full) == L.waveforms(plain)
    got = PR.check(full, tmp_path)
    assert got["failed"] == []
    assert got["us"] == PR.check(plain, tmp_path)["us"]
