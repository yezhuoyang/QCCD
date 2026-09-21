"""The classical half of the machine: the decoder, the classical memory, wires and frames.

Three claims are tested here, each the way the layer itself states it:

* the **decoder** is a finite gadget wired to every place that measures, and its lookup
  table corrects every single fault of the syndrome-extraction circuit it reads;
* a **wire** is not a road: no ion can be sent down one, a message takes the wire's
  latency, and nothing may read a bit before it arrives (G10, G11);
* a correction kept **in software** is kept in the archive, and the flat sign-off is only
  allowed exactly the frames the archive holds -- change one and the sign-off fails;
* a **classically controlled** op reserves its place either way and fires only when its
  guard says so, the guard has to be there first (G11), and every branch of the dynamic
  program is signed off on its own -- invert a guard and it fails.
"""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from qccd.gadget.algorithm import (AlgorithmError, parse_algorithm, parse_algorithm_file,
                                   parse_condition)
from qccd.gadget.city import schedule
from qccd.gadget.leaves.classical import CLASSICAL
from qccd.gadget.library import LeafLibrary
from qccd.gadget.logic.flat import sign_off
from qccd.gadget.model import Port
from qccd.gadget.place_checks import check_places
from qccd.gadget.synth import flatten
from qccd.gadget.town import town

ALGS = Path(__file__).resolve().parents[1] / "examples" / "gadgets" / "algorithms"


@pytest.fixture(scope="module")
def leaves(tmp_path_factory):
    return LeafLibrary(tmp_path_factory.getbasetemp() / "place_cache", log=None)


@pytest.fixture(scope="module")
def lib(leaves):
    return town(leaves)


@pytest.fixture(scope="module")
def runs(leaves, lib):
    out = {}
    for stem in ("frames", "s_gate", "bell_surgery", "cond_cx", "t_gate"):
        alg = parse_algorithm_file(ALGS / f"{stem}.alg")
        out[stem] = (alg, schedule(alg, lib, leaves))
    return out


# --------------------------------------------------------------------------- the places


def test_the_classical_places_are_built_and_modeled(leaves):
    for name in CLASSICAL:
        m = leaves.place(name)
        assert m.kind == "leaf" and m.family in ("decoder", "archive")
        assert m.ions_at_rest == 0 and m.capacity == 0
        assert leaves.data[name]["programs"] == {}         # nothing to replay: no ions
        assert leaves.data[name]["device"]["nodes"]        # but something to draw
        for op in m.ops.values():
            assert op.status == "modeled", op.name
            assert op.duration_us > 0
            assert op.notes


def test_the_decoder_table_corrects_every_single_fault(leaves):
    chk = leaves.place("dec_d3").ops["decode"].logic
    assert chk["status"] == "verified"
    t = chk["check"]
    assert t["passed"] and not t["clashes"]
    assert t["single_faults"] > 300 and t["entries"] > 1
    assert t["syndrome_bits"] >= 8 and t["corrections"] == 2
    table = dict((s, c) for s, c in t["table"])
    assert table[0] == 0, "a fault with no syndrome at all must have no logical effect"


def test_a_wire_is_not_a_road(lib, leaves):
    top = lib[lib.top]
    wires = [c for c in top.channels if c.kind == "wire"]
    roads = [c for c in top.channels if c.kind != "wire"]
    assert wires and roads
    assert all(c.latency_us > 0 for c in wires)
    assert not lib.structure_violations()
    # the road network the scheduler routes ions on must not contain a single wire
    collected: dict = {}
    _, nets, _ = flatten(lib, leaves, wires=collected)
    assert len(collected) == len(wires)
    assert not any("wire" in name for name in nets)
    # every place that measures is wired to the decoder, and every outcome to the archive
    ends = {(c.a, c.b) for c in wires} | {(c.b, c.a) for c in wires}
    for inst in top.instances:
        if lib[inst.master].family in ("decoder", "archive"):
            continue                      # the control places are the other end of the wires
        for p in lib[inst.master].ports:
            if p.kind != "classical":
                continue
            other = {"syndrome": "decoder.syn", "outcome": "archive.in",
                     "decision": "archive.ctl", "frame": "archive.frame"}[p.signal]
            assert (f"{inst.name}.{p.name}", other) in ends or \
                   (other, f"{inst.name}.{p.name}") in ends, (inst.name, p.name)


def test_no_ion_can_be_sent_down_a_wire():
    ion = Port("out", "out", "data", 9)
    bits = Port("syn", "in", "any", 0, kind="classical", signal="syndrome")
    assert any("classical" in why for why in ion.accepts(bits))
    other = Port("ctl", "in", "any", 0, kind="classical", signal="decision")
    assert any("signals differ" in why for why in bits.accepts(other))
    ok = Port("syn", "in", "any", 0, kind="classical", signal="syndrome")
    assert Port("syn", "out", "any", 0, kind="classical", signal="syndrome").accepts(ok) == []


# --------------------------------------------------------------------------- the schedule


def test_every_syndrome_reaches_the_decoder_and_every_outcome_the_archive(runs, lib, leaves):
    for stem, (alg, gir) in runs.items():
        msgs = gir["messages"]
        syn = [m for m in msgs if m[9] == "syndrome"]
        frame = [m for m in msgs if m[9] == "frame"]
        res = [m for m in msgs if m[9] == "outcome"]
        decodes = [e for e in gir["events"] if e[4] == "decode"]
        assert syn and len(decodes) == len(syn) == len(frame), stem
        assert len(res) == len(gir["outcomes"]), stem
        assert all(m[4] == gir["control"]["decoder"] for m in syn), stem
        # a message is carried by a wire with that latency, and arrives before it is used
        for m in msgs:
            assert m[7] - m[6] == pytest.approx(gir["wires"][m[1]][4]), (stem, m[0])
        ck = check_places(gir, lib, leaves)
        assert not ck["failed"], (stem, ck["violations"])
        assert ck["metrics"]["messages"] == len(msgs)
        assert ck["metrics"]["reaction_us"] > 0


def test_a_bit_used_before_it_arrives_fails_g10(runs, lib, leaves):
    _, gir = runs["frames"]
    bad = copy.deepcopy(gir)
    msg = next(m for m in bad["messages"] if m[9] == "syndrome")
    msg[7] = msg[6] + 10 * (msg[7] - msg[6])        # the wire is slower than it says
    ck = check_places(bad, lib, leaves)
    assert "G10" in ck["failed"]
    _, sg = runs["s_gate"]
    bad = copy.deepcopy(sg)
    f = next(f for f in bad["frames"] if f["az"]["sources"])
    f["t"] = 0.0                                    # written before anything was measured
    assert "G10" in check_places(bad, lib, leaves)["failed"]


def test_a_decoder_that_falls_behind_fails_g11(runs, lib, leaves):
    _, gir = runs["bell_surgery"]
    bad = copy.deepcopy(gir)
    jobs = [e for e in bad["events"] if e[4] == "decode"]
    assert len(jobs) > 1
    last = max(e[2] for e in bad["events"])
    jobs[0][1], jobs[0][2] = last, last + (jobs[0][2] - jobs[0][1])
    assert "G11" in check_places(bad, lib, leaves)["failed"]


# --------------------------------------------------------------------------- the frames


def test_a_logical_pauli_is_a_line_in_the_archive_and_flips_the_outcome(runs, lib, leaves):
    alg, gir = runs["frames"]
    # no ion is touched: the software Paulis add no event outside the archive
    soft = [f for f in gir["frames"] if f["kind"] == "pauli"]
    assert len(soft) == 2
    for f in soft:
        e = gir["events"][f["event"]]
        assert gir["leaves"][e[3]][0] == "arch1" and e[4] == "update"
    rep = sign_off(alg, gir, lib, leaves)
    assert rep["passed"], rep["why"]
    # both readouts come out flipped, because the frame anticommutes with what was measured
    assert [r["relation"] for r in rep["relations"]] == ["m=Z̄[q0] = 1", "n=X̄[q1] = 1"]
    assert all(r["ok"] for r in rep["relations"])
    assert rep["archive"]["software_paulis"] == 2
    # and it is the archive that makes it match: take its corrections away and the
    # schedule stops computing the algorithm
    bad = copy.deepcopy(gir)
    bad["frames"] = [f for f in bad["frames"] if f["kind"] != "pauli"]
    assert not sign_off(alg, bad, lib, leaves)["passed"]


def test_the_s_gadget_makes_an_s_and_keeps_its_correction_in_software(runs, lib, leaves):
    alg, gir = runs["s_gate"]
    # the gadget is a composition of places that are verified on their own
    ops = {e[4] for e in gir["events"]}
    assert {"inject.Y", "zz", "read.X"} <= ops
    rep = sign_off(alg, gir, lib, leaves)
    assert rep["passed"], rep["why"]
    assert [r["relation"] for r in rep["relations"]] == ["m=X̄[q0] = 1"]
    assert rep["relations"][0]["ok"]
    # the correction is a Z frame conditioned on the parity of the two outcomes per gadget
    frames = [f for f in gir["frames"] if f["kind"] == "pauli"]
    assert len(frames) == 2
    assert frames[-1]["ax"] == {"const": 0, "sources": []}
    assert len(frames[-1]["az"]["sources"]) == 4      # two S̄ gadgets, two outcomes each
    # injection is not fault-tolerant, and the report says so instead of claiming distance 3
    assert "inject.Y" in rep["distance"]["skipped"]


def test_the_frame_is_not_a_licence_to_ignore_the_sign_off(runs, lib, leaves):
    """A frame the archive does not hold cannot rescue a schedule: drop one outcome from
    the S̄ correction and the sign-off must fail."""
    alg, gir = runs["s_gate"]
    bad = copy.deepcopy(gir)
    f = next(f for f in bad["frames"] if f.get("delta", {}).get("az", {}).get("sources"))
    f["delta"]["az"]["sources"] = f["delta"]["az"]["sources"][:-1]
    assert not sign_off(alg, bad, lib, leaves)["passed"]


def test_the_y_injection_prepares_a_logical_y(leaves):
    op = leaves.place("inj_d3").ops["inject.Y"]
    lg = op.logic
    assert op.status == "verified" and lg["status"] == "verified"
    fl = lg["flows"]
    assert fl["complete"] and fl["rank"] == fl["needed"] == 9
    assert fl["spec"]["title"] == "prepare |Ȳ⟩"
    assert any(f["name"] == "Ȳ = +1" and f["ok"] for f in fl["flows"])
    assert lg["distance"]["distance"] == 1 and lg["distance"]["expect"] != "ft"


# ----------------------------------------------------------- classically controlled execution


def test_a_guard_is_a_parity_of_outcomes():
    assert parse_condition("m") == (0, ("m",))
    assert parse_condition("!m") == parse_condition("m == 0") == (1, ("m",))
    assert parse_condition("m ^ k") == parse_condition("m ⊕ k") == (0, ("m", "k"))
    assert parse_condition("m ^ m") == (0, ())              # a parity, so it cancels
    for src, why in (
        ("prep q0 Z\nif m: x q0", "no earlier instruction measured"),
        ("prep q0 Z\nread q0 Z -> m\nif m: read q0 Z -> r", "cannot name"),
        ("prep q0 Z\nread q0 Z -> m\nif m+: x q0", "condition"),
    ):
        with pytest.raises(AlgorithmError) as exc:
            parse_algorithm(src)
        assert why in str(exc.value)


def test_a_guarded_op_reserves_its_place_and_waits_for_the_bit(runs, lib, leaves):
    alg, gir = runs["cond_cx"]
    guarded = [e for e in gir["events"] if len(e) > 10 and e[10]]
    assert guarded, "the conditional CNOT should be marked on its event"
    for e in guarded:
        g = e[10]
        assert g["cond"]["cvars"] == ["m"] and g["branches"] == ["run", "skip"]
        # the guard is at the door before the op starts ...
        assert e[1] >= g["ready_us"] - 1e-6
        # ... on a decision wire from the archive to that place ...
        msgs = [m for m in gir["messages"] if m[9] == "decision" and m[4] == e[3]]
        assert msgs and msgs[0][2] == gir["control"]["archive"]
        # ... and the place is reserved for the whole op either way (the worst case)
        op = lib[gir["leaves"][e[3]][0]].ops[e[4]]
        assert e[2] - e[1] == pytest.approx(op.duration_us)
    assert check_places(gir, lib, leaves)["metrics"]["guarded_ops"] == len(guarded)


def test_a_guard_that_arrives_too_late_fails_g11(runs, lib, leaves):
    _, gir = runs["cond_cx"]
    bad = copy.deepcopy(gir)
    e = next(e for e in bad["events"] if len(e) > 10 and e[10])
    e[10]["ready_us"] = e[1] + 1000.0                  # decided after the op began
    assert "G11" in check_places(bad, lib, leaves)["failed"]
    bad = copy.deepcopy(gir)
    e = next(e for e in bad["events"] if len(e) > 10 and e[10])
    bad["messages"] = [m for m in bad["messages"]
                       if not (m[9] == "decision" and m[4] == e[3])]
    assert "G11" in check_places(bad, lib, leaves)["failed"]


def test_every_branch_of_a_dynamic_program_is_signed_off(runs, lib, leaves):
    alg, gir = runs["cond_cx"]
    assert alg.guard_cvars == ["m"] and len(alg.branches()) == 2
    rep = sign_off(alg, gir, lib, leaves)
    assert rep["passed"], rep["why"]
    assert rep["branch_count"] == 2 and len(rep["branches"]) == 2
    # what the two branches promise is different, and both are checked
    said = {b["label"]: [r["relation"] for r in b["relations"]] for b in rep["branches"]}
    assert said["m=0"] == ["u=X̄[q0] = 0"]
    assert said["m=1"] == ["u=X̄[q0] ⊕ v=X̄[q1] = 0"]
    assert all(r["ok"] for b in rep["branches"] for r in b["relations"])
    for b in rep["branches"]:
        assert b["random_bits"]["hardware"] == b["random_bits"]["ideal"]
        assert b["conditioned_bits"] == 1


def test_the_t_gate_teleports_through_every_branch(runs, lib, leaves):
    alg, gir = runs["t_gate"]
    assert len(alg.branches()) == 4
    rep = sign_off(alg, gir, lib, leaves)
    assert rep["passed"], rep["why"]
    # the S̄ gadget runs in the two branches where the surgery outcome asks for it
    ran = [b for b in rep["branches"] if b["branch"]["m"] == 1]
    assert len(ran) == 2
    for b in rep["branches"]:
        flows = {f["flow"].split(" →")[0]: f for f in b["flows"]}
        assert set(flows) == {"X̄[t]", "Z̄[t]"}, b["label"]
        assert all(f["ok"] for f in b["flows"]), (b["label"], b["flows"])
        # with the S̄ correction the input's X̄ lands as Ȳ, without it as X̄: the branch's
        # own ideal circuit says which, and the hardware has to agree
        # with the S̄ correction the input's X̄ lands as Ȳ and without it as X̄, and in
        # every branch the flow is EXACT: no leftover outcome terms, because the
        # conditional corrections resolved them
        import unicodedata
        got = unicodedata.normalize("NFC", flows["X̄[t]"]["flow"])
        want = unicodedata.normalize("NFC", "Ȳ[q0]" if b["branch"]["m"] else "X̄[q0]")
        assert want in got, (b["label"], got)
        assert "⊕" not in got, (b["label"], got)


def test_the_conditional_correction_is_checked_not_assumed(runs, lib, leaves):
    """Mutate the correction and the sign-off must fail: this is what makes the branch
    check worth running."""
    alg, gir = runs["t_gate"]
    for src in (alg.source.replace("if m:  s q0", "if !m: s q0"),     # inverted guard
                alg.source.replace("if m:  s q0", "store q0"),        # no S correction
                alg.source.replace("if m:  s q0", "s      q0"),       # never conditional
                alg.source.replace("if k:  z q0", "if k:  x q0")):    # the wrong Pauli
        rep = sign_off(parse_algorithm(src, "wrong"), gir, lib, leaves, distance=False)
        assert not rep["passed"], src


def test_a_conditional_correction_may_be_read_out_afterwards(lib, leaves):
    """The pattern that makes conditional corrections worth having: correct, then measure.

    The archive's frame for the block differs between branches, so the readout it corrects
    differs too -- and each branch's claim is checked against that branch's ideal circuit."""
    alg = parse_algorithm("""title correct then read
prep q0 X
prep a X
read a Z -> m
decode a
if m: z q0
read q0 X -> u
decode
""", "cond_read")
    gir = schedule(alg, lib, leaves)
    assert not gir["refused"]
    rep = sign_off(alg, gir, lib, leaves, distance=False)
    assert rep["passed"], rep["why"]
    said = {b["label"]: [r["relation"] for r in b["relations"]] for b in rep["branches"]}
    assert said == {"m=0": ["u=X̄[q0] = 0"], "m=1": ["u=X̄[q0] = 1"]}
    # the promise really depends on the correction being that one: the wrong Pauli, or the
    # opposite condition, and the branches no longer say what the hardware does.  (Dropping
    # a software correction altogether is not a difference the ions can show -- both sides
    # of it are classical -- so what is checked is that it is the RIGHT one.)
    for src in (alg.source.replace("if m: z q0", "if m: x q0"),
                alg.source.replace("if m: z q0", "if !m: z q0")):
        assert not sign_off(parse_algorithm(src, "wrong"), gir, lib, leaves,
                            distance=False)["passed"], src


def test_the_corrected_teleportation_is_deterministic(lib, leaves):
    """`teleport_fix.alg`: the receiving block ends in a known state, in every branch."""
    alg = parse_algorithm_file(ALGS / "teleport_fix.alg")
    gir = schedule(alg, lib, leaves)
    rep = sign_off(alg, gir, lib, leaves, distance=False)
    assert rep["passed"], rep["why"]
    said = {b["label"]: [r["relation"] for r in b["relations"]] for b in rep["branches"]}
    assert said == {"m2=0": ["r=X̄[b] = 0"], "m2=1": ["r=X̄[b] = 0"]}, said
    # and it has to be that correction: the wrong Pauli or the wrong bit fails
    for src in (alg.source.replace("if m2: z b", "if m2: x b"),
                alg.source.replace("if m2: z b", "if m1: z b")):
        assert not sign_off(parse_algorithm(src, "wrong"), gir, lib, leaves,
                            distance=False)["passed"], src


def test_a_branch_that_cannot_happen_says_so(lib, leaves):
    alg = parse_algorithm("""title impossible
prep q0 X
prep a Z
read a Z -> m
decode a
if m: x q0
store q0
decode
""", "impossible")
    gir = schedule(alg, lib, leaves)
    assert not gir["refused"]
    rep = sign_off(alg, gir, lib, leaves, distance=False)
    assert rep["passed"], rep["why"]
    dead = [b for b in rep["branches"] if b.get("unreachable")]
    assert len(dead) == 1 and dead[0]["branch"] == {"m": 1}
    assert "always measures m = 0" in dead[0]["why"][0]


def test_a_place_that_cannot_be_told_refuses_the_guard(lib, leaves):
    alg = parse_algorithm("""title a clinic cannot be told
prep q0 X
prep a X
read a Z -> m
decode a
if m: se q0 1
decode
""", "nope")
    gir = schedule(alg, lib, leaves)
    assert gir["refused"], "syndrome extraction cannot be told whether to run"
    assert "cannot be guarded" in gir["refused"][0][1]


def test_the_t_gadget_correction_table_is_right():
    """The corrections `t_gate.alg` applies, checked on the state itself.

    The per-branch sign-off shows the hardware runs exactly the conditional *Clifford*
    program the algorithm states; that the program is the right one for a T gate is a
    statement about a non-Clifford state, so it is checked here on the two-qubit logical
    circuit: |+> and the magic state in, a ZZ measurement, an X readout of the magic block,
    then S if the ZZ outcome was 1 and Z if the X outcome was 1.  Every branch must leave
    T|+> on the data block."""
    import cmath
    import math

    r2 = 1 / math.sqrt(2)
    t = cmath.exp(1j * math.pi / 4)
    target = (r2, r2 * t)                       # T|+>
    for m in (0, 1):
        for k in (0, 1):
            # |+> (data, index 0) x |T> (magic, index 1), as amplitudes a[data][magic]
            a = {(d, g): (r2 if d == 0 else r2) * (r2 if g == 0 else r2 * t)
                 for d in (0, 1) for g in (0, 1)}
            a = {key: v for key, v in a.items() if ((key[0] ^ key[1]) == m)}   # ZZ = (-1)^m
            # read the magic block in X: project on (|0> + (-1)^k |1>)/sqrt(2)
            out = [0j, 0j]
            for (d, g), v in a.items():
                out[d] += v * (1 if (g == 0 or k == 0) else -1) * r2
            if m:                               # the conditional S̄ correction
                out[1] *= 1j
            if k:                               # the conditional Z̄ correction
                out[1] *= -1
            norm = math.sqrt(sum(abs(x) ** 2 for x in out))
            assert norm > 1e-9, (m, k)
            out = [x / norm for x in out]
            overlap = abs(target[0].conjugate() * out[0] + target[1].conjugate() * out[1])
            assert overlap > 1 - 1e-9, (m, k, out, overlap)
