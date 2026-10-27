"""The full stack on QCCD (qccd/fullstack): FormalRV's layers above, ions below, every link checked.

Most tests read the slice-1 fixture (one logical CX on surface d=3, compiled by FormalRV at its
pinned commit; tests/fixtures/fullstack/cnot) and need neither FormalRV nor the QCCD compiler.
The end-to-end test runs the whole pipeline and is skipped where the pinned FormalRV clone or
qccdc is not built.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from qccd.fullstack import devirt, layers, provenance, sysprog as sp

REPO = Path(__file__).resolve().parents[1]
FIX = REPO / "tests" / "fixtures" / "fullstack" / "cnot"
PROG = REPO / "examples" / "fullstack" / "cnot.prog"


@pytest.fixture(scope="module")
def stages():
    expansion = sp.expand(sp.read(FIX / "cnot.sysprog"))
    return (layers.read(PROG, "prog"), layers.read(FIX / "cnot.qppm", "qppm"),
            layers.read(FIX / "cnot.qec", "qec"), expansion)


# ----------------------------------------------------------------------------- the reader

def test_natural_arithmetic_is_the_listing_language():
    ev = sp.evaluate
    assert ev("5 - 7", {}) == 0                                   # truncated subtraction
    assert ev("7 / 0", {}) == 0 and ev("7 % 0", {}) == 7          # natural division
    assert ev("2 ^ 3 ^ 2", {}) == 512                             # right-associative power
    delta = "24*(1 - (1 - (a - w + (w - a))))"                    # the compiler's a != w test
    assert ev(delta, {"a": 3, "w": 3}) == 0 and ev(delta, {"a": 3, "w": 5}) == 24


def test_expansion_agrees_with_formalrv_vm(stages):
    *_, x = stages
    vm = json.loads((FIX / "vm.json").read_text(encoding="utf-8"))
    assert sp.vm_counts_agree(x, vm) == []
    assert len(x.events) == vm["dynamic_instruction_count"] == 2086
    assert len(x.calls) == vm["dynamic_call_count"]
    assert x.end_ns == vm["runtime_upper_bound_ns"]


def test_every_event_keeps_its_line_trip_and_call(stages):
    *_, x = stages
    inside = [e for e in x.events if e.frames and e.frames[-1].kind == "call"]
    assert inside, "the extraction rounds run inside surfaceRound calls"
    e = inside[0]
    assert e.frames[-1].label in ("surfaceRound", "surfaceRoundLogicalAncilla")
    assert e.frames[0].kind == "trip" and "=" in e.frames[0].label
    assert x.listing.lines[e.node.line - 1].strip().startswith("@")


def test_unsupported_constructs_are_refused_not_miscounted(tmp_path):
    text = (FIX / "cnot.sysprog").read_text(encoding="utf-8").replace(
        "  for 0 <= rv_r26 < 3 every 5115000 ns:", "  when c[0]:", 1)
    p = tmp_path / "guarded.sysprog"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(sp.ListingError, match="guards"):
        sp.expand(sp.read(p))


# ----------------------------------------------------------------------------- the links

def test_every_program_line_is_linked_up_to_the_prog(stages):
    prog, qppm, qec, x = stages
    ch = provenance.link(prog, qppm, qec, x)
    assert set(ch.sys_to_qec) == {n.line for n in x.listing.body}
    assert set(ch.qec_to_qppm) == {s.line for s in qec.program.body}
    assert set(ch.qppm_to_prog) == {s.line for s in qppm.program.statements()}
    # each merge is anchored by its Pauli product, each frame by its record band
    merges = [s for s in qec.program.body if " = merge " in s.text]
    assert all(ch.qec_to_qppm[s.line].how == "pauli" for s in merges)
    for e in x.events:
        r = ch.resolve(e)
        assert {"sysprog", "qec", "qppm", "prog"} <= set(r), (e.uid, e.kind, e.node.line)


def test_a_record_band_that_does_not_match_is_refused(stages, tmp_path):
    prog, qppm, qec, x = stages
    text = (FIX / "cnot.qec").read_text(encoding="utf-8").replace("-> c[0,6) commuting", "-> c[0,7) commuting", 1)
    p = tmp_path / "bad.qec"
    p.write_text(text, encoding="utf-8")
    with pytest.raises(provenance.Unlinked, match="c\\[0, 7\\]"):
        provenance.link(prog, qppm, layers.read(p, "qec"), x)


# ----------------------------------------------------------------------------- devirtualization

def test_devirtualize_allocates_ions_by_lifetime(stages):
    *_, qec, x = stages
    d = devirt.devirtualize(x, devirt.code_from_qec(qec))
    assert d.n_qubits == 36                               # FormalRV's VM peak is 70 (it counts workspace)
    assert d.factory["workspace_granted"] == 17 and d.factory["workspace_used"] == 0
    resets = sum(1 for o in d.ops if o.role == "reset")
    requested = sum(e.arg for e in x.events if e.kind == "request_ancilla")
    assert resets == requested                            # every requested cell becomes one reset
    from qccd.qec.qasm import parse_qasm
    q = parse_qasm(d.qasm.replace("creg syn[", "creg s[").replace("-> syn[", "-> s["))
    assert q.n_qubits == 36
    # every circuit op names the event it implements, and the event exists
    assert all(0 <= o.event < len(x.events) for o in d.ops)


def test_the_factory_makes_the_promised_state():
    code = {"name": "Surface", "n": 9, "stabs": ["X[1]X[2]", "X[0]X[1]X[3]X[4]", "X[4]X[5]X[7]X[8]", "X[6]X[7]",
                                                 "Z[0]Z[3]", "Z[1]Z[2]Z[4]Z[5]", "Z[3]Z[4]Z[6]Z[7]", "Z[5]Z[8]"],
            "logX": "X[0]X[3]X[6]", "logZ": "Z[0]Z[1]Z[2]"}
    gates, info = devirt.y_factory(code)
    assert gates and "Y-bar" in info["check"]
    bad = dict(code, logX="X[0]")                          # not a logical: anticommutes with Z0Z3
    with pytest.raises(Exception):
        devirt.y_factory(bad)


# ----------------------------------------------------------------------------- the gadget town

CACHE = REPO / "out" / "gadgets" / "cache"


def test_the_bench_measures_any_product_of_logicals():
    from qccd.gadget.leaves.bench import PATTERNS
    from qccd.gadget.library import LeafLibrary
    m = LeafLibrary(CACHE, log=None).place("ppm_d3")
    assert m.family == "ppm" and set(m.ops) == {f"ppm.{p}" for p in PATTERNS}
    for name, op in m.ops.items():
        fl = op.logic["flows"]
        assert op.status == "verified", (name, op.notes)
        assert fl["passed"] and fl["rank"] == fl["needed"] == 18 * len(name.split(".")[1]), name


def test_ppm_and_prep_y_are_instructions():
    from qccd.gadget.algorithm import AlgorithmError, parse_algorithm
    a = parse_algorithm("prep q0 Z\nprep q1 Z\nprep a Y\nppm Z q0 X q1 Z a -> m\n")
    ins = a.instructions[-1]
    assert ins.kind == "ppm" and ins.blocks == ["q0", "q1", "a"] and ins.op == "ppm.ZXZ"
    assert a.instructions[2].op == "inject.Y"
    c, _, _, recs = a.ideal_circuit()
    assert len(recs) == 1
    for bad in ("ppm Y q0 -> m", "ppm Z q0 Z q0 -> m", "ppm Z q0 X q1 Z a X b Z c -> m", "ppm Z q0 X"):
        with pytest.raises(AlgorithmError):
            parse_algorithm("prep q0 Z\nprep q1 Z\nprep a Y\nprep b Z\nprep c Z\n" + bad + "\n")


@pytest.fixture(scope="module")
def lowered(stages):
    from qccd.fullstack import qec2alg
    prog, qppm, qec, _ = stages
    return qec2alg.lower(qec, title="CX(0, 1) from FormalRV"), provenance.link_upper(prog, qppm, qec)


def test_the_lowering_links_every_qec_statement(stages, lowered):
    low, chain = lowered
    _, _, qec, _ = stages
    assert set(low.linked) == {s.line for s in qec.program.body}
    assert set(low.qec_of.values()) | set(low.absorbed) == set(low.linked)
    words = [t.split()[0] for t, _, role in low.lines if role != "input"]
    assert words.count("ppm") == 3 and words.count("read") == 3 and words.count("prep") == 3
    # every lowered instruction reaches the .prog line through the .qec and .qppm links
    for al, ql in low.qec_of.items():
        p = chain.qec_to_qppm[ql].stmt.line
        assert chain.qppm_to_prog[p].stmt.text.strip() == "CX(0, 1)"


def test_the_lowered_program_is_exactly_the_progs_gate(stages, lowered):
    from qccd.fullstack.town import contract_check
    low, _ = lowered
    prog = stages[0]
    rep = contract_check(prog, low)
    assert rep["passed"] and rep["contract"] == "CX" and rep["branches"] == 64
    assert rep["worst_fidelity"] == pytest.approx(1.0) and rep["control"]["passed"] is False
    # drop one Pauli frame: no longer CX in every branch
    broken = copy.deepcopy(low)
    assert "\nif m0: z q0\n" in broken.text
    broken.text = broken.text.replace("\nif m0: z q0\n", "\n", 1)
    assert not contract_check(prog, broken)["passed"]


def test_a_formalrv_program_runs_on_the_gadget_town(stages, lowered, tmp_path):
    import json as _json
    from qccd.fullstack.town import stack_payload
    from qccd.gadget.verified import build_algorithm
    low, chain = lowered
    prog, qppm, qec, _ = stages
    alg = tmp_path / "cnot.alg"
    alg.write_text(low.text, encoding="utf-8")
    payload = stack_payload(prog, qppm, qec, chain, low, foot="test")
    r = build_algorithm(alg, out_dir=tmp_path / "site", cache_dir=CACHE, stack=payload, log=None)
    s = r["stats"]
    assert s["refused"] == 0 and not r["checks"].get("failed")
    assert s["rules"]["failed"] == [] and s["signed_off"]
    used = {r["gir"]["leaves"][e[3]][0] for e in r["gir"]["events"]}
    assert {"ppm_d3", "inj_d3", "read_d3"} <= used
    html = (tmp_path / "site" / "index.html").read_text(encoding="utf-8")
    data = _json.loads(html.split('<script id="gdata" type="application/json">', 1)[1].split("</script>", 1)[0])
    assert set(data["stack"]["layers"]) == {"prog", "qppm", "qec"}
    assert all(int(k) in low.qec_of for k in data["stack"]["alg"])
    assert "fullstack-line" in html                     # the stack's control is declared


# ----------------------------------------------------------------------------- gadgets, loops, branches: DJ

DJ = REPO / "tests" / "fixtures" / "fullstack" / "dj"


@pytest.fixture(scope="module")
def dj():
    import json as _json
    from qccd.fullstack import qec2alg, structure
    prog = layers.read(DJ / "Examples" / "DJ" / "DJ.prog", "prog")
    qppm, qec = layers.read(DJ / "dj.qppm", "qppm"), layers.read(DJ / "dj.qec", "qec")
    templates = next(iter(_json.loads((DJ / "templates.json").read_text(encoding="utf-8")).values()))
    chain = structure.link(prog, qppm, qec, templates)
    return prog, qppm, qec, chain, qec2alg.lower(qec, title="DJ")


def test_a_gadget_becomes_one_gate_and_every_line_is_linked(dj):
    from qccd.fullstack import structure
    from qccd.gadget.algorithm import parse_algorithm
    prog, qppm, qec, chain, low = dj
    assert list(low.gates) == ["djoracle"]                  # FormalRV's djOracle, one definition
    alg = parse_algorithm(low.text)
    assert [c["gate"] for c in alg.calls] == ["djoracle"]
    assert all(i.src_line for i in alg.instructions if i.call)   # expanded lines keep their body line
    reach = structure.reachable(qec)
    qlines = [s.line for u in qec.units if u.kind == "program" or (u.kind == "gadget" and u.name in reach)
              for s in u.statements() if s.text.strip() != "skip"]
    assert all(q in low.linked for q in qlines)
    assert all(q in chain.qec_to_qppm for q in qlines)
    preach = structure.reachable(qppm)
    plines = [s.line for u in qppm.units if u.kind == "program" or (u.kind == "gadget" and u.name in preach)
              for s in u.statements()]
    assert all(p in chain.qppm_to_prog for p in plines)
    # through the call: a merge inside the oracle reaches CX(i, n) inside djOracle.prog
    inner = [l for l, lk in chain.qppm_to_prog.items() if lk.stmt.text.strip() == "CX(i, n)"]
    assert inner and prog.lines[chain.qppm_to_prog[inner[0]].stmt.line - 1].strip() == "CX(i, n)"


def test_deutsch_jozsa_gives_a_in_every_branch(dj):
    from qccd.fullstack import semantics
    from qccd.gadget.algorithm import parse_algorithm
    *_, low = dj
    alg = parse_algorithm(low.text)
    ok = semantics.check_outcome_spec(alg, ["q0", "q1"], 1, 1)
    assert ok["passed"] and ok["random_outcomes"] >= 20 and ok["bits"][0]["expression"] == "1"
    assert not semantics.check_outcome_spec(alg, ["q0", "q1"], 0, 1)["passed"]
    # a correction that matters, removed: the outcome is no longer a constant
    broken = parse_algorithm(low.text.replace("  if o1: z p0\n", "", 1))
    assert not semantics.check_outcome_spec(broken, ["q0", "q1"], 1, 1)["passed"]


def test_deutsch_jozsa_runs_on_the_gadget_town(dj, tmp_path):
    from qccd.fullstack.town import stack_payload
    from qccd.gadget.verified import build_algorithm
    prog, qppm, qec, chain, low = dj
    alg = tmp_path / "dj.alg"
    alg.write_text(low.text, encoding="utf-8")
    r = build_algorithm(alg, out_dir=tmp_path / "site", cache_dir=CACHE, log=None,
                        stack=stack_payload(prog, qppm, qec, chain, low, foot="test"))
    s, comp = r["stats"], r["compositional"]
    assert s["refused"] == 0 and s["rules"]["failed"] == [] and comp["passed"]
    sem = comp["parts"]["semantics"]
    assert sem["method"].startswith("symbolic") and sem["outcomes"]["m24"] == "1"
    assert any(g["gate"] == "djoracle" and g["contract"] is None for g in comp["parts"]["gates"]["rows"])


# ----------------------------------------------------------------------------- T states: Shor for 15

SHOR = REPO / "tests" / "fixtures" / "fullstack" / "shor15"


def test_shor15_lowered_from_formalrv_has_the_right_distribution():
    """FormalRV's own lowering of examples/fullstack/shor15.prog (15 T states, two Toffolis, a
    semiclassical inverse QFT), lowered again by QCCD: the three control readouts give
    c0 = 0 always and (c1, c2) uniform -- exactly, branches merged as their outcomes stop
    being read, never more than a few live."""
    from qccd.fullstack import qec2alg
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.logic.merged import distribution
    low = qec2alg.lower(layers.read(SHOR / "shor15.qec", "qec"))
    alg = parse_algorithm(low.text)
    assert sum(1 for i in alg.instructions if i.kind == "inject") == 15
    assert any(i.kind == "ppm" and len(i.blocks) == 4 for i in alg.instructions)   # the Toffoli's ZZZ
    outs = [next(i.cvar for i in alg.instructions if i.cvar and i.blocks == [q] and i.kind in ("read", "ppm")
                 and (i.basis == "Z" or i.paulis == ["Z"])) for q in ("q4", "q5", "q6")]
    got = distribution(alg, outs)
    assert got["distribution"] == {(0, 0, 0): 0.25, (0, 0, 1): 0.25, (0, 1, 0): 0.25, (0, 1, 1): 0.25}
    assert got["peak_branches"] <= 64
    # every T inverted is still exactly a Toffoli (it is a real matrix): nothing may change
    import re as _re
    conj = "\n".join(ln for ln in low.text.split("\n") if not _re.fullmatch(r"x a\d+(_\d+)?", ln.strip()))
    assert distribution(parse_algorithm(conj), outs)["distribution"] == got["distribution"]


@pytest.mark.skipif(not (REPO / "examples" / "fullstack" / "shor15.prog").exists(), reason="no source")
def test_a_wrong_gate_in_shor15_is_caught_by_the_state_check(tmp_path):
    """The distribution of a period-4 Shor is uniform, which many wrong programs also give; the
    state left on the work register, result by result, is what pins it down."""
    import json as _json
    from qccd.fullstack import qec2alg, structure, town
    prog = layers.read(REPO / "examples" / "fullstack" / "shor15.prog", "prog")
    qppm, qec = layers.read(SHOR / "shor15.qppm", "qppm"), layers.read(SHOR / "shor15.qec", "qec")
    if not _pinned():
        pytest.skip("linking .prog -> .qppm by prefix differencing needs FormalRV")
    chain = structure.link(prog, qppm, qec, structure.learn_templates(),
                           prog_path=REPO / "examples" / "fullstack" / "shor15.prog", specialize={"n": 7})
    low = qec2alg.lower(qec)
    good = town.distribution_check(prog, chain, low, {"n": 7})
    assert good["passed"] and good["states_compared"] >= 4 and good["worst_state_fidelity"] > 0.999999
    # the first CX(5, 0) rotation measured as Z-bar instead of X-bar: a CZ, not a CX
    bad = _re_sub_first(low.text, r"ppm Z q5 X q0 ", "ppm Z q5 Z q0 ")
    low.text = bad
    assert not town.distribution_check(prog, chain, low, {"n": 7})["passed"]


def _re_sub_first(text, a, b):
    assert a in text
    return text.replace(a, b, 1)


def _pinned() -> bool:
    try:
        from qccd.fullstack import formalrv
        formalrv.check_pin()
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _pinned(), reason="needs the pinned FormalRV clone")
def test_shor15_from_formalrv_is_signed_off_on_the_town(tmp_path):
    from qccd.fullstack import town
    r = town.run(REPO / "examples" / "fullstack" / "shor15.prog", out=tmp_path / "shor15", specialize={"n": 7})
    assert r["linked"] == r["qec_statements"] == r["linked_up"]
    assert r["concrete"]["refused"] == 0 and r["checks"]["signed_off"] == "compositional"
    c = r["contract"]
    assert c["kind"] == "distribution" and c["passed"] and c["prog"] == {"000": 0.25, "001": 0.25,
                                                                       "010": 0.25, "011": 0.25}


# ----------------------------------------------------------------------------- folded: a gate scheduled once


def _folded_checks(alg_text: str):
    """Schedule `alg_text` folded, expand it, and run every place check and machine rule on
    the expansion."""
    from qccd.gadget.floorplan import FLOORPLANS_DIR, load_file
    from qccd.gadget.fold import expand, parse_folded, schedule_folded
    from qccd.gadget.judge import judge_schedule
    from qccd.gadget.library import LeafLibrary
    from qccd.gadget.place_checks import check_places
    leaves = LeafLibrary(CACHE, log=None)
    alg = parse_folded(alg_text)
    lib, fp = load_file(FLOORPLANS_DIR / f"{alg.floorplan or 'fullstack'}.floor", leaves)
    g = schedule_folded(alg, lib, leaves)
    x = expand(g)
    return g, x, check_places(x, lib, leaves), judge_schedule(lib, leaves, x, regions=fp.regions)


REUSE = """title a gate called twice on the same homes, once on others
floorplan "fullstack"
gate zzy p0 p1:
  prep l0 Y
  ppm Z p0 Z p1 Z l0 -> o1
  decode p0 p1 l0
  read l0 X -> o2
  decode l0
  if o1: z p0
  if o1: z p1
  if o2: z p0
  if o2: z p1
  x p0
prep q0 Z
prep q1 Z
prep q2 Z
zzy q0 q1
zzy q0 q1
zzy q1 q2
read q0 Z -> r0
read q1 Z -> r1
read q2 Z -> r2
decode q0 q1 q2
"""


def test_a_gate_is_scheduled_once_per_context_and_its_calls_never_overlap():
    g, x, checks, rules = _folded_checks(REUSE)
    assert g["schema"] == "qccd.gir/0.3" and not g["refused"]
    # (q0, q1) twice: one macro, reused; (q1, q2): homes differ, a macro of its own
    assert g["folded"]["macros"] == 2 and g["folded"]["reused"] == 1
    a, b, c = g["calls"]
    assert a["m"] == b["m"] != c["m"]
    # the second call on q0, q1 starts once the first has given them back
    assert b["t0"] >= a["t0"] + max(g["macros"][a["m"]]["free"]) - 1e-6
    assert checks["failed"] == [] and rules["failed"] == []
    # every instruction of every call is in the expansion, once
    kinds = [i["kind"] for i in x["program"]["instructions"] if not i.get("refresh")]
    assert kinds.count("ppm") == 3 and kinds.count("read") == 6
    # the frame a call leaves is its caller's: X-bar lands on its first block each time, so
    # q0 (first twice) reads unflipped, q1 (first once) flipped, q2 (never first) unflipped
    flips = {o["cvar"]: o["flip"]["const"] for o in x["outcomes"] if o.get("cvar") in ("r0", "r1", "r2")}
    assert flips == {"r0": 0, "r1": 1, "r2": 0}


def test_each_gate_is_checked_once_and_the_composition_by_construction():
    """A run too large to expand is checked per view -- each body's own rows, the blocks it
    was passed resting at their homes, its calls as boxes -- and by composition.  On a run
    small enough to expand too, the two verdicts agree; and each of the three is shown to
    catch what it is for."""
    import copy as _copy
    from qccd.gadget.floorplan import FLOORPLANS_DIR, load_file
    from qccd.gadget.fold import check_composition, check_folded, parse_folded, schedule_folded
    from qccd.gadget.library import LeafLibrary
    from qccd.fullstack import qec2alg
    leaves = LeafLibrary(CACHE, log=None)
    low = qec2alg.lower(layers.read(SHOR / "shor15.qec", "qec"))
    alg = parse_folded(low.text)
    lib, _ = load_file(FLOORPLANS_DIR / "fullstack.floor", leaves)
    keep = {}
    g = schedule_folded(alg, lib, leaves, keep=keep)
    per = check_folded(g, lib, leaves)
    assert per["failed"] == [] and len(per["passed"]) == 11 and per["metrics"]["views"] == 19
    assert check_composition(g, keep["folder"]) == []
    # two calls moved onto one another: only the composition can see it
    bad = _copy.deepcopy(g)
    bad["calls"][1]["t0"] = bad["calls"][0]["t0"]
    assert check_composition(bad, keep["folder"])
    # a gate's block that does not come home: its own view's G2
    bad = _copy.deepcopy(g)
    mid = bad["calls"][0]["m"]
    bad["macros"][mid]["homes"] = ["mem8"] * len(g["macros"][mid]["homes"])
    assert "G2" in check_folded(bad, lib, leaves)["failed"]
    # the holds that keep resting blocks refreshed, dropped from the program: G8
    bad = _copy.deepcopy(g)
    assert bad["holds"]
    bad["holds"] = []
    assert "G8" in check_folded(bad, lib, leaves)["failed"]


def test_a_box_is_fitted_past_every_conflict_not_just_the_first_resource():
    """A call's footprint is fitted interval by interval.  A rounding difference on one
    resource (1e-11 us late) once ended the scan and was then read as 'no shift', so a real
    conflict on a later resource went unseen -- found by check_composition in FormalRV's Shor."""
    from types import SimpleNamespace
    from qccd.gadget.fold import FastCalendar, MacroCity
    a, b = FastCalendar(), FastCalendar()
    a.reserve(0.0, 100.0 + 1e-11)          # free from 100 us on, to a rounding
    b.reserve(1500.0, 2000.0)              # busy where the box needs it
    me = SimpleNamespace(cal={"A": a, "B": b}, net_cal={})
    m = SimpleNamespace(gate="g", footprint={("leaf", "A"): [(0.0, 10.0)], ("leaf", "B"): [(1310.0, 3510.0)]})
    t0 = MacroCity.fit(me, m, 100.0)
    assert t0 + 1310.0 >= 2000.0 - 1e-9      # B is free from 2000 us: the box starts after that


def test_folded_deutsch_jozsa_passes_every_check(dj):
    *_, low = dj
    g, x, checks, rules = _folded_checks(low.text)
    assert g["folded"]["macros"] == 1 and not g["refused"]
    assert checks["failed"] == [] and rules["failed"] == []


def test_folded_shor15_passes_every_check():
    from qccd.fullstack import qec2alg
    low = qec2alg.lower(layers.read(SHOR / "shor15.qec", "qec"))
    g, x, checks, rules = _folded_checks(low.text)
    assert not g["refused"] and g["folded"]["macros"] == len(low.gates)
    assert sum(1 for i in x["program"]["instructions"] if i["kind"] == "inject") == 15
    assert checks["failed"] == [] and rules["failed"] == []


def test_a_folded_run_plays_on_the_page_a_window_at_a_time(tmp_path):
    """Shor-15 folded, on the Design page: wherever the playhead goes, the page has the
    macros it needs, draws the calls that run there, lights the program line, and says
    which gate (and which line of it) an instruction is in."""
    import shutil
    import subprocess
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is needed to look at the page")
    from qccd.fullstack import qec2alg
    from qccd.gadget.fold import build_folded
    from qccd.gadget.library import LeafLibrary
    low = qec2alg.lower(layers.read(SHOR / "shor15.qec", "qec"))
    alg = tmp_path / "shor15.alg"
    alg.write_text(low.text, encoding="utf-8")
    r = build_folded(alg, out_dir=tmp_path / "site", leaves=LeafLibrary(CACHE, log=None), log=None)
    assert r["checks"]["failed"] == [] and r["checks"]["metrics"]["per_view_agrees"]
    assert len(list((tmp_path / "site" / "macro").glob("m*.js"))) == r["stats"]["macros"]
    p = subprocess.run([node, str(REPO / "tests" / "gadget_fold_browser.mjs"), str(tmp_path / "site")],
                       capture_output=True, text=True, encoding="utf-8", timeout=300)
    if p.returncode == 2:
        pytest.skip("Chrome is needed to look at the page")
    assert p.returncode == 0, p.stderr
    seen = json.loads(p.stdout.strip().splitlines()[-1])
    assert seen["errors"] == [] and "fatal" not in seen, seen.get("fatal") or seen["errors"]
    for s in seen["seeks"]:
        assert s["a"] <= s["t"] <= s["b"] and not s["missing"], s
        assert s["events"] > 0 and s["lit"] >= 1, s
        # every row on screen is a line of the run's TSIR modules (hw/, beside the page)
        assert s["unplaced"] == 0, s
    assert any(s["inGadgets"] for s in seen["seeks"])
    hw = tmp_path / "site" / "hw"
    assert (hw / "program.tsir").is_file() and (hw / "program.js").is_file()
    assert {f"gadget/{m}.tsir" for m in r["gir"]["macros"]} <= set(r["modules"])
    # somewhere a gate runs: its instruction names the gate and the line of the gate it is on
    inside = [s for s in seen["seeks"] if s["focus"] and s["focus"]["chain"]]
    assert inside, seen["seeks"]
    assert all(s["focus"]["src"] for s in inside)
    assert seen["played"]["t"] > 0.2 * seen["boot"]["makespan"] and seen["played"]["a"] > 0


# ----------------------------------------------------------------------------- nothing virtual: the factory


def test_the_factory_compiles_its_protocol_line_by_line():
    """The 15-to-1 factory's protocol is a program (protocols/rm15.circ), the derivation it
    came from still gives it, and the factory's TSIR carries it out operation by operation,
    every gate, reset and measurement naming its line -- beside the state-vector check that
    it distills T†|+⟩.  A protocol the TSIR does not follow is caught."""
    from qccd.gadget.leaves.factory import PROTOCOLS, read_circuit, rm15_circuit, source_order
    from qccd.gadget.library import LeafLibrary
    from qccd.gadget.logic.circuit import from_tsir
    from qccd.ir.tsir import TSIR
    gates, lines, out = read_circuit(PROTOCOLS / "rm15.circ")
    assert (gates, out) == rm15_circuit()
    leaves = LeafLibrary(CACHE, log=None)
    op = leaves.place("msf15").ops["produce"]
    ck = op.logic["check"]
    assert op.status == "verified" and ck["passed"] and ck["output"] == "T†|+⟩"
    assert ck["source_order"]["passed"] and ck["source_order"]["operations"] == 119
    d = leaves.data["msf15"]
    assert d["sources"]["produce"]["name"] == "rm15.circ"
    ops = [i for i in d["programs"]["produce"]["instructions"] if i["type"] in ("gate", "reset", "measure")]
    assert len(ops) == 119 and all((i.get("meta") or {}).get("src", "").startswith("rm15.circ:") for i in ops)
    # the protocol with two operations swapped is not what the hardware does
    circ = from_tsir(TSIR.from_json(d["programs"]["produce"]))
    names = [f"s{i}" if i != out else "m0" for i in range(15)]
    swapped = list(gates)
    k = next(i for i, g in enumerate(gates) if g[0] == "CX")
    swapped[k], swapped[k + 1] = swapped[k + 1], swapped[k]
    assert not source_order(circ, names, swapped, lines, "rm15.circ")["passed"]
    # a rotation source names its three lines too
    leaves.place("rot1")
    rot = leaves.data["rot1"]
    assert rot["sources"]["rz4"]["text"].splitlines()[-1] == "RZ4 0"
    assert all((i.get("meta") or {}).get("src", "").startswith("rz4.circ:")
               for i in rot["programs"]["rz4"]["instructions"] if i["type"] in ("gate", "reset"))


# ----------------------------------------------------------------------------- rotations finer than T


def test_a_rotation_source_makes_its_state_and_is_verified():
    from qccd.gadget.library import LeafLibrary
    m = LeafLibrary(CACHE, log=None).place("rot1")
    assert m.family == "rotation" and len(m.ops) == 16
    for name, op in m.ops.items():
        assert op.status == "verified" and op.logic["status"] == "verified", name
        assert op.logic["check"]["fidelity"] == 1.0


@pytest.mark.parametrize("letters", [("Z",), ("X",), ("Z", "Z"), ("Z", "X")])
def test_a_rotation_is_exactly_its_angle_in_every_branch(letters):
    """FormalRV's `ppmrot(±π/2^k) P`, as QCCD lowers it (repeat until success: a Pauli-product
    measurement against RZ(k-1)|+⟩, the doubled angle on the other outcome): the blocks end in
    exp(∓iπ/2^k P)|ψ⟩, exactly, whatever the outcomes -- for each k and sign, a product of one
    or two blocks, Z̄ or X̄."""
    import math
    import numpy as np
    from qccd.fullstack import qec2alg
    from qccd.gadget.algorithm import parse_algorithm
    from qccd.gadget.logic.contract import State, _primitive
    from qccd.gadget.logic.merged import _factor_out, _words

    def every_branch(alg):
        """Each branch's final state (on the blocks still alive), all outcomes enumerated."""
        brs = [(State(), {})]
        for ins in alg.instructions:
            word, args = _words(ins)
            out = []
            for st, oc in brs:
                if ins.cond is not None and not ins.cond.holds(oc):
                    out.append((st, oc))
                elif ins.kind in ("ppm", "read"):
                    for o in (0, 1):
                        s2 = st.copy()
                        if (_primitive(s2, word, args, o) or 0) > 1e-12:
                            out.append((s2, {**oc, ins.cvar: o}))
                elif ins.kind != "decode":
                    s2 = st.copy()
                    _primitive(s2, word, args)
                    out.append((s2, oc))
                else:
                    out.append((st, oc))
            brs = out
        return [st for st, _ in brs]

    class _Stage:
        def gadgets(self):
            return {}
    blocks = [f"q{i}" for i in range(len(letters))]
    # a state that is no eigenstate of anything measured: |+⟩, then |Ȳ⟩ on the second
    prep = ["prep q0 X"] + (["prep q1 Y"] if len(blocks) > 1 else [])
    psi = np.array([1.0 + 0j])
    for i in range(len(blocks)):
        psi = np.kron(np.array([1, 1j if i else 1]) / math.sqrt(2), psi)   # q0 the low bit
    P = np.array([[1.0 + 0j]])
    mats = {"X": np.array([[0, 1], [1, 0]]), "Z": np.diag([1, -1])}
    for p in letters:
        P = np.kron(mats[p], P)
    for k in range(2, 7):
        for sign in (1, -1):
            it = qec2alg._Interp(_Stage())
            items = it.rotation(list(zip(letters, blocks)), k, sign, 1)
            w = qec2alg._Writer(it.out, fresh=10 ** 8)
            body = w.lines(items)
            gates = []
            for name, params, gbody in w.defs:
                gates.append(f"gate {name} {' '.join(params)}:")
                gates += ["  " + t for t, _ in gbody]
            alg = parse_algorithm("\n".join(gates + prep + [t for t, _ in body]) + "\n")
            phi = sign * math.pi / 2 ** k
            want = (math.cos(phi) * np.eye(len(psi)) - 1j * math.sin(phi) * P) @ psi
            finals = every_branch(alg)
            assert len(finals) >= 2 * (k - 1)           # every outcome of every level, enumerated
            for st in finals:
                for q in [q for q in list(st.names) if q not in blocks]:
                    assert _factor_out(st, q)            # the resource blocks are gone, unentangled
                v = np.zeros(len(psi), dtype=complex)
                for b, a in st.amp.items():
                    v[sum(((b >> st.names.index(q)) & 1) << i for i, q in enumerate(blocks))] += a
                assert abs(abs(np.vdot(want, v)) - 1) < 1e-9, (letters, k, sign)
            # and the resource it asks for is the one its angle needs
            text = alg.source
            if k >= 4:
                assert f"R{k - 1}{'' if sign > 0 else 'dg'}" in text
            elif k == 3:
                assert "inject" in text


@pytest.mark.skipif(not _pinned(), reason="needs the pinned FormalRV clone")
def test_formalrvs_shor_simulates_from_the_input_its_spec_assumes():
    """FormalRV's own Examples/Shor at its witness (a = 2, N = 3: order 2), simulated from its
    .prog -- `ctrl` blocks, `@ shift(m)` maps, descending loops -- from the basis state its spec
    assumes (|rev(bits, 1)⟩ on the work register): the phase register reads 0 or 8 (bit-reversed),
    half each.  From |0…0⟩ instead (a·0 = 0: nothing kicks back) it always reads 0."""
    from qccd.fullstack import formalrv, progsim
    prog = layers.read(formalrv.root() / "FormalRV" / "Examples" / "Shor" / "Shor.prog", "prog")
    sp = dict(m=4, bits=3, N=3, a=2, ainv=2, rec=19)
    assert progsim.distribution(prog, sp, 4, inputs={6: 1}) == {(0, 0, 0, 0): 0.5, (1, 0, 0, 0): 0.5}
    assert progsim.distribution(prog, sp, 4) == {(0, 0, 0, 0): 1.0}


@pytest.mark.skipif(not _pinned(), reason="needs the pinned FormalRV clone")
def test_formalrvs_shor_lowers_once_per_call_shape(tmp_path):
    """FormalRV's own Examples/Shor at its witness, unmodified, inverse QFT included: a few
    hundred gates, each FormalRV gadget body lowered once per shape, its controlled copies
    (`!ctl!g`) and its rotations (`ppmrot`, as rotation-source states and repeat until
    success) among them."""
    from qccd.fullstack import formalrv, qec2alg
    src = formalrv.root() / "FormalRV" / "Examples" / "Shor" / "Shor.prog"
    formalrv.prog_to_qppm(src, tmp_path / "shor.qppm")
    formalrv.qppm_to_qec(tmp_path / "shor.qppm", tmp_path / "shor.qec", "surface", 3,
                         dict(m=4, bits=3, N=3, a=2, ainv=2, rec=19))
    qec = layers.read(tmp_path / "shor.qec", "qec")
    low = qec2alg.lower(qec)
    assert 500 < len(low.gates) < 1000 and len(low.text.splitlines()) < 25000
    assert any(name.startswith("ctl_modmulmcp") for name in low.gates)
    assert " R3dg" in low.text and " R4" in low.text          # pi/16 and pi/32 rotations
    # every .qec statement the program reaches links to its .qppm statement -- through the
    # controlled copies, the one-trip loops that bind a descending loop, and `if not c:`, whose
    # body FormalRV writes as the `rv_e` branch
    from qccd.fullstack import structure
    links = structure.link_qppm_qec(layers.read(tmp_path / "shor.qppm", "qppm"), qec)
    reach = structure.reachable(qec)
    stmts = [s for u in qec.units if u.kind == "program" or (u.kind == "gadget" and u.name in reach)
             for s in u.statements() if s.text.strip() != "skip"]
    assert len(stmts) > 9000 and all(s.line in links for s in stmts)


# ----------------------------------------------------------------------------- end to end

def _toolchains_built() -> bool:
    try:
        from qccd.fullstack import formalrv
        formalrv.check_pin()
        from qccd.qec import _toolchain
        return _toolchain()[0] is not None
    except Exception:
        return False


@pytest.mark.skipif(not _toolchains_built(), reason="needs the pinned FormalRV clone and qccdc")
def test_one_logical_cnot_on_ions_every_layer_checked(tmp_path):
    from qccd.fullstack import consistency, pipeline
    ex = REPO / "examples" / "fullstack"
    t = pipeline.run(PROG, machine=ex / "s1.machine", sysspec=ex / "cnot.sysspec",
                     arch=REPO / "arch" / "grid9x9.arch.json", out=tmp_path / "run", specialize={"n": 2})
    c = t["checks"]
    assert t["abstract"]["vm_status"] == "RESOURCE_VALIDATED"
    assert c["rules"]["failed"] == []
    assert c["correspondence"]["ok"]
    assert c["r10_semantic"]["o2_semantics"] == "ok" and c["r10_semantic"]["order"] == "ok"
    assert c["r10_lean"]["status"] in ("accepted", "not run")
    assert c["stack"]["ok"] and c["stack"]["instructions_checked"] > 5000
    # move one .sysprog line's link to another PPM's extraction: the stack check must notice
    tsir = json.loads((tmp_path / "run" / "hw" / "prog.tsir.json").read_text(encoding="utf-8"))
    m = copy.deepcopy(t)
    lines = m["layers"]["qec"]["lines"]
    ext = sorted(int(k) for k in m["links"]["qec"] if lines[int(k) - 1].strip().startswith("extract"))
    under_first = sorted(int(k) for k, v in m["links"]["sys"].items() if v[0] == ext[0])
    m["links"]["sys"][str(under_first[1])] = [ext[2], "band"]
    assert not consistency.check(m, tsir)["ok"]
