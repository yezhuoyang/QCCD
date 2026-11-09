"""FormalRV's Gidney 2025 listing read at scale and its cat programs on ions (docs/FULLSTACK_PLAN.md, S10 step 3)."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from qccd.compile.tiled_cat import Target as T  # noqa: E402
from qccd.fullstack.scale import catprog as K  # noqa: E402
from qccd.fullstack.scale import gidney as G  # noqa: E402
from qccd.fullstack.scale import listing as L  # noqa: E402
from qccd.fullstack.scale.gidney_plan import ROW_TILE_US, COLUMN_TILE_US, Plan, Times, program_of  # noqa: E402

SMALL = """machine { qubits: 10 total, reset_ns: 1 ns }
params  P, M
table tab = [3, 1, 4, 1, 5]

gadget leaf[data: 4, ancilla: rv_anc + 2, c: 0, syndrome: 0](n, rv_anc) spans 10 ns:
  @0 ns  request_ancilla  count=1  ancilla[rv_anc]
  for 0 <= i < n every 1 ns:
    @0 ns  H  ancilla[rv_anc]
  @5 ns  measure  basis=X  ancilla[rv_anc]  -> c[0]
  @6 ns  release_ancilla  count=1  ancilla[rv_anc]

gadget choose[data: 4, ancilla: rv_anc + 2, c: 0, syndrome: 0](k, rv_anc) spans 10 ns:
  case k:
    0:
      skip
    1:
      @0 ns  call leaf(2, rv_anc)
    2:
      @0 ns  call leaf(5, rv_anc)

program[data: 4, ancilla: 10, c: 1, syndrome: 0]:
  for 0 <= a < tab[2] every 100 ns:
    @0 ns  call leaf(tab[a], 10*a)
  for 0 <= b < 3 step 100*(Σ s < 2 . tab[s]) ns:
    @0 ns  call leaf(1, 50)
  when c[0:1]:
    @0 ns  call choose(M, 0)
  for 0 <= g < M/2^0%2 every 1 ns:
    @0 ns  H  ancilla[1]
  for 0 <= t < tablemax(tab, 2) every 1 ns:
    @0 ns  H  ancilla[2]
"""


@pytest.fixture()
def small(tmp_path):
    p = tmp_path / "small.sysprog"
    p.write_text(SMALL, encoding="utf-8")
    return L.read(p, {"P": 7, "M": 1})


def test_a_listing_is_counted_as_written(small):
    c = L.counts(small, upper=False)
    # tab[2] = 4 trips: leaf(3), leaf(1), leaf(4), leaf(1); leaf(1) x 3; choose(1) = leaf(2); one H; then four
    assert c["H"] == (3 + 1 + 4 + 1) + 3 * 1 + 2 + 1 + 4
    assert c["request_ancilla"] == c["measure"] == c["release_ancilla"] == 4 + 3 + 1


def test_table_reads_shape_a_loop_only_through_their_values(small):
    assert small.gadgets["leaf"].relevant == frozenset({"n"})
    assert small.gadgets["choose"].relevant == frozenset({"k"})


def test_natural_arithmetic():
    e = L._Expr("(2 - 5) + 7/0 + 7%0 + 2^3^2", {})
    assert e({}) == 0 + 0 + 7 + 512


def test_top_absorbs_and_runs_its_one_trip():
    tables = {"Pm1": L._TopTable()}
    e = L._Expr("2^n - Pm1[i]*2^(n - 21)", tables)
    assert e({"n": 22}) is L.TOP and "i" not in e.names
    assert L._Expr("p/2^k%2", {})({"p": L.TOP, "k": 3}) is L.TOP


# ----------------------------------------------------------------------------- the programs

@pytest.mark.parametrize("targets", [
    (T(0, 0, False, "Z"),),
    (T(0, 0, False, "X"),),
    (T(0, 1, False, "Z"), T(0, 0, True, "Z")),
    (T(0, 1, False, "X"), T(0, 0, True, "Z")),
    (T(0, 0, True, "Y"), T(0, 1, False, "Z")),
])
def test_a_repetition_passes_every_rule_and_r10(targets, tmp_path):
    b = K.build_rep(targets, tries=20, seeds=(1,))
    r = K.check(b, tmp_path)
    assert r["failed"] == [] and r["r10_blocks"] >= 1
    assert r["us"] == K.machine_us(b)
    weight = sum(21 if t.letter == "Y" else 11 for t in targets)
    assert r["ions"] == len(targets) * 241 + (weight + 3) // 4 + 2


def test_a_y_operator_is_what_the_listing_writes():
    """CY at the crossing, then the column by CNOT, then the row by CZ: qelib1's cy in the machine's gates."""
    from qccd.compile.tiled_cat import operator_gates, rep_circuit
    gates = operator_gates("Y", 11)
    assert gates[0] == (0, "cy") and gates[1:11] == [(r * 11, "cx") for r in range(1, 11)]
    assert gates[11:] == [(c, "cz") for c in range(1, 11)]
    qasm = rep_circuit((T(0, 0, True, "Y"), T(0, 1, False, "Z")), 11)[0]
    assert "sdg q[0];\ncx q[" in qasm and "], q[0];\ns q[0];" in qasm and "cy " not in qasm


# ----------------------------------------------------------------------------- the writer's circuit is the listing's

def _rep_gates(k=6, wide=False):
    """A repetition as the listing writes it, for two X̄ (or a register's), with its cat ions numbered from 1000."""
    from qccd.compile.tiled_cat import _tree
    cats = list(range(1000, 1000 + k))
    flag, ver = 2000, 2001
    targets = (((90, "X"), (93, "X")) if not wide else tuple((3 * i, "X") for i in range(k)))
    gates = [("CNOT", a, b) for lay in _tree(cats) for a, b in lay]
    pieces = [(p, 11 * r) for p, _ in targets for r in range(11)]
    for i, (p, off) in enumerate(pieces):
        gates.append(("CNOT", cats[i // 11 if wide else i // 4], (p, off)))
    gates += [("CNOT", cats[0], flag), ("CNOT", ver, flag)]
    return gates, targets, {"H": 2, "X": k + 1}


def test_the_reader_passes_the_writers_circuits():
    assert G._as_written(*_rep_gates(), "t") is None
    assert G._as_written(*_rep_gates(k=7, wide=True), "t") is None


@pytest.mark.parametrize("spoil", ["tree", "order", "owner", "flag", "verifier", "count", "operator"])
def test_the_reader_refuses_anything_else(spoil):
    """Each is a circuit the writer would not write: compiling it anyway would prove the wrong thing."""
    gates, targets, counted = _rep_gates()
    at = next(i for i, g in enumerate(gates) if not isinstance(g[2], int))
    if spoil == "tree":
        gates[1], gates[2] = gates[2], gates[1]
        gates[2] = ("CNOT", 1000, 1005)
    elif spoil == "order":
        gates[at], gates[at + 1] = gates[at + 1], gates[at]
    elif spoil == "owner":
        gates[at] = ("CNOT", 1001, gates[at][2])
    elif spoil == "flag":
        gates[-2] = ("CNOT", 1001, 2000)
    elif spoil == "verifier":
        del gates[-1]
    elif spoil == "count":
        counted = {"H": 1, "X": 7}
    elif spoil == "operator":
        gates[at] = ("CZ", 1000, gates[at][2])
    with pytest.raises(ValueError):
        G._as_written(gates, targets, counted, "t")


def test_a_patch_with_no_operator_is_refused():
    assert G._letter([("CZ", c) for c in range(11)]) == "Z"
    assert G._letter([("CNOT", 11 * r) for r in range(11)]) == "X"
    for marks in ([("CZ", c) for c in range(10)],                     # short
                  [("CNOT", c) for c in range(11)],                   # the row by the column's gate
                  [("CZ", 11 * r) for r in range(11)]):               # the column by the row's gate
        with pytest.raises(ValueError):
            G._letter(marks)


def test_a_walk_keeps_out_of_every_tile():
    """The free ions go through seams: a program's walks never pass a trap a patch fills, named or not."""
    b = K.build_rep((T(0, 3, False, "Z"), T(0, 0, True, "Z")), tries=10, seeds=(1,))
    own = {t for q, t in b.cert["init"].items() if int(q[1:]) < 2 * 121}
    interior = b.grid.taken() - own
    assert not (b.walker.walked & interior)


def test_the_wide_cat_and_its_distance_rule(tmp_path):
    W = 4
    b = K.build_wide(W, (W // 2, -1))
    r = K.check(b, tmp_path)
    assert r["failed"] == [] and r["r10_blocks"] >= 1
    times = Times({("wide", W, None): r["us"]})
    for ctl in ((0, -1), (W - 1, -1), (W // 2, -3)):
        built = K.machine_us(K.build_wide(W, ctl))
        assert built <= times.wide(W, (ctl[0] - W // 2, ctl[1])) + 1e-6, ctl


def test_a_masked_cat_has_every_ion_and_acts_on_its_mask(tmp_path):
    """`prepMaskedP`: the whole register's cat, one tile acted on: not a small product."""
    W = 4
    b = K.build_wide(W, (W // 2, -1), mask=(W - 1,))
    r = K.check(b, tmp_path)
    assert r["failed"] == [] and r["r10_blocks"] >= 1
    assert r["ions"] == (W + 1) * 241 + (W + 1) + 2
    lines = b.qasm.splitlines()
    first = (W + 1) * 241
    acts = {int(l.split("q[")[1].split("]")[0]) - first for l in lines
            if l.startswith("cx") and int(l.split("q[")[2].split("]")[0]) < (W + 1) * 121}
    assert acts == {0, W}
    # as the listing uses it, the control beside the wire it acts on: the distance rule still bounds the built program
    times = Times({("wide", W, (W - 1,)): r["us"]})
    built = K.machine_us(K.build_wide(W, (W - 1, -1), mask=(W - 1,)))
    assert built <= times.wide(W, (W - 1 - W // 2, -1), (W - 1,)) + 1e-6


def test_seam_rates():
    assert ROW_TILE_US == 12 * 2 * 94.0 and COLUMN_TILE_US == 2 * ROW_TILE_US


# ----------------------------------------------------------------------------- the listing itself

OUT = Path(os.environ.get("QCCD_S10", ROOT / "out" / "s10"))
PIN = Path(os.environ.get("QCCD_FORMALRV", ROOT.parent / "FormalRV-pin"))
INPUTS = PIN / "FormalRV/Audit/Gidney2025/Resource/Inputs/PaperMachine_q1M/execution.inputs"
needs_listing = pytest.mark.skipif(not ((OUT / "g25.sysprog").exists() and INPUTS.exists()),
                                   reason="the listing (tools/gidney_machine.py, FormalRV's compiler) is not here")


@pytest.fixture(scope="module")
def reader():
    lst = L.read(OUT / "g25.sysprog", L.read_inputs(INPUTS))
    return lst, G.Reader(lst)


@needs_listing
def test_counts_equal_formalrvs_vm(reader):
    lst, _ = reader
    vm = json.loads((OUT / "vm.json").read_text(encoding="utf-8"))
    assert L.vm_differences(L.counts(lst), vm) == []


@needs_listing
def test_an_adder_bit_reads_as_its_products(reader):
    lst, R = reader
    g = lst.gadgets["gidneyInterior"]
    items = R.flat(R.key("gidneyInterior", {**{f: 0 for f in g.formals}, "i": 5, "rv_database": 1280}))
    reps = [i for i in items if i[0] == "rep"]
    assert all(r[1] == 11 for r in reps)
    assert [sorted(str(p) for p, _ in r[2] if isinstance(p, int)) for r in reps[:3]] == [["15"], ["16"], ["17"]]
    assert [i[1] for i in items if i[0] == "magic"] == ["|AutoCCZ>"]


@needs_listing
def test_every_patch_is_on_a_tile_of_the_floorplan(reader):
    """Nothing is nowhere: every instance's every tile a real one, and no two live patches on one."""
    lst, R = reader
    plan = Plan(R)
    seen, stack, tiles, letters, masked = set(), [None], 0, set(), 0
    while stack:
        key = stack.pop()
        if key in seen:
            continue
        seen.add(key)
        live: dict = {}
        items = R.flat(key) if key is not None else R.program()
        reps = [it for it in items if it[0] == "rep"]
        letters.update(letter for it in reps for _, letter in it[2])
        masked += sum(len(it) > 4 for it in reps)
        for it in plan._resolve(key):
            if it[0] == "call":
                stack.append(it[1])
            elif it[0] == "free":
                live.pop(it[1], None)
            elif it[0] == "work":
                for r in it[2]:
                    if r[0] == "tile":
                        x, y = r[1]
                        assert 0 <= x < plan.width and 0 <= y < plan.height, (key, r)
                        tiles += 1
    assert tiles > 1000
    # reading all of it passed `_as_written`; the rare kinds were among what it read
    assert letters == {"X", "Y", "Z"} and masked >= 1


def test_a_patch_with_no_wire_goes_to_the_port_row():
    plan = Plan.__new__(Plan)
    plan.port_row, plan.width = 40, 3
    live, spot = {}, {}
    got = [plan._place(G.Anc(0, j), {}, {}, spot, live) for j in range(3)]
    assert got == [(0, 40, False), (1, 40, False), (2, 40, False)]
    with pytest.raises(StopIteration):      # full: an error, not a patch that is nowhere
        plan._place(G.Anc(0, 3), {}, {}, spot, live)


@needs_listing
def test_the_plan_schedules_all_of_the_census(reader):
    lst, R = reader
    key = R.key("gOnePrime", {f: 0 for f in lst.gadgets["gOnePrime"].formals})
    assert sum(Plan(R).shapes(key).values()) == R.census(key)["rep"] == 766531


@needs_listing
def test_the_schedule_uses_nothing_twice_at_once(reader):
    """One whole adder, flat: every wire, tile and stretch of seam busy for disjoint intervals."""
    import gidney_concrete as GC
    lst, R = reader
    rows = json.loads((OUT / "shapes.json").read_text(encoding="utf-8"))
    times, cells = Times(), {}
    for text in rows:
        f = OUT / "programs" / f"{GC._name(text)}.json"
        if not f.exists():
            pytest.skip("the programs are not built")
        row = json.loads(f.read_text(encoding="utf-8"))
        times[GC._shape(text)] = row["us"]
        cells[GC._shape(text)] = [tuple(c) for c in row["cells"]]
    assert all(program_of(s) in times for s in Plan(R).shapes(None))
    plan = Plan(R)
    g = lst.gadgets["gidneyAddNoCarry"]
    key = R.key("gidneyAddNoCarry", {**{f: 0 for f in g.formals}, "n": 32, "rv_database": 1280})
    busy = plan.intervals(key, times, cells)
    assert sum(len(v) for v in busy.values()) > 100
    for res, spans in busy.items():
        spans.sort()
        for (a0, a1), (b0, b1) in zip(spans, spans[1:]):
            assert b0 >= a1 - 1e-6, (res, a0, a1, b0, b1)
