"""The Helios baseline router (qccd/compile/helios_router.py): no two ions pass each other, gates
keep the input's order, and every programme passes the `helios` rule set.

`passings` replays a programme's transport on its own and reports every move in which an ion
passes another -- through a place someone stands on, or out of a two-ion zone past the ion it
shares it with.  The replay's rules (R5, R14, R25) do not see that today, so the test does.
"""

from __future__ import annotations

import pytest

from qccd.arch.helios import architecture
from qccd.compile import helios_router
from qccd.cost.models import corrected_model
from qccd.ir.tsir import TSIR


def passings(prog: TSIR, arch) -> list[str]:
    """Every move in which an ion passes another (empty: none does)."""
    dev = arch.device
    pos: dict[str, str] = {}
    near: dict[str, tuple[str, str]] = {}      # a two-ion place -> (the side, the ion on that side)
    out: list[str] = []
    for ins in prog.instructions:
        if ins.type == "init":
            pos.update(ins.placement)
            continue
        if ins.type != "simd":
            continue
        before = dict(pos)
        movers = {p.ion for p in ins.participants}
        for p in ins.participants:
            nodes = [p.src]
            for sid in p.via or (dev.segment_between(p.src, p.dst).id,):
                nodes.append(dev.segments[sid].other(nodes[-1]))
            others = {i: n for i, n in before.items() if i not in movers}
            for n in nodes[1:-1]:
                hit = [i for i, m in others.items() if m == n]
                if hit:
                    out.append(f"instruction {ins.id}: {p.ion} passes {hit} at {n}")
            # leaving a place it shares: only out of its own side
            mates = [i for i, m in others.items() if m == p.src]
            if mates and p.src in near:
                side, ion_near = near[p.src]
                first = nodes[1]
                if (first == side) != (p.ion == ion_near):
                    out.append(f"instruction {ins.id}: {p.ion} leaves {p.src} past {mates}")
            if not mates:
                near.pop(p.src, None)
            # coming into a place someone is in: it stands on the side it came from
            if [i for i, m in others.items() if m == p.dst]:
                near[p.dst] = (nodes[-2], p.ion)
        for p in ins.participants:
            pos[p.ion] = p.dst
        for place in [k for k in near if sum(1 for m in pos.values() if m == k) < 2]:
            del near[place]
    return out


def _qasm(n: int, body: str) -> str:
    return f'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[{n}];\ncreg c[{n}];\n{body}'


#: the circuits that drove one ion through others before: a partner three zones away, and every pair
CIRCUITS = {
    "far partner": _qasm(3, "h q[0];\ncx q[0],q[2];\nmeasure q[0] -> c[0];\nmeasure q[2] -> c[2];\n"),
    "eight, far apart": _qasm(8, "h q[0];\ncx q[0],q[7];\ncx q[7],q[3];\ncz q[1],q[6];\n"
                                 + "".join(f"measure q[{i}] -> c[{i}];\n" for i in range(8))),
    "every pair of five": _qasm(5, "".join(f"cx q[{a}],q[{b}];\n" for a in range(5) for b in range(5) if a != b)
                                   + "".join(f"measure q[{i}] -> c[{i}];\n" for i in range(5))),
}


@pytest.mark.parametrize("name", sorted(CIRCUITS))
def test_no_ion_passes_another_and_every_rule_holds(name, tmp_path):
    import json
    from qccd.arch.helios import device_doc
    arch = architecture()
    # the router's own check first: it replays and verifies what it wrote (raises on a broken rule)
    (tmp_path / "helios.arch.json").write_text(json.dumps(device_doc()), encoding="utf-8")
    helios_router.compile_with_router(CIRCUITS[name], tmp_path / "helios.arch.json", tmp_path / "work", name=name)
    prog, cert = helios_router.compile_helios(CIRCUITS[name], arch, name=name)
    assert passings(prog, arch) == []
    # nothing rests on the junction: a ring step carries an ion across it inside one cycle
    pos = {}
    for ins in prog.instructions:
        pos.update(ins.placement or {})
        pos.update({p.ion: p.dst for p in ins.participants})
        assert "X0" not in pos.values(), ins.id
    # each qubit's operations run in the input's order (R25); a batch runs other qubits' side by side
    ops = {o["i"]: o["qubits"] for o in cert["circuit_ops"]}
    seen: dict[int, int] = {}
    for g in sorted(cert["gates"], key=lambda g: g["t"]):
        for q in ops[g["dag"]]:
            assert seen.get(q, -1) < g["dag"], (q, g["dag"])
            seen[q] = g["dag"]
    from qccd.compile.cooling import insert_cooling
    from qccd.site.architecture_compilation import _judge
    model = corrected_model("helios")
    cooled = insert_cooling(prog, arch, model).program
    assert passings(cooled, arch) == []
    for ruleset in ("helios", "helios-deck", "standard"):
        _, rep = _judge(arch, cooled, ruleset, model, cert)      # raises on a failing rule
        assert not rep.failed()


def test_the_checker_sees_an_ion_driven_through_another():
    """The first router walked q2 LO2 -> LO1 -> LO0, through q1: `passings` must say so."""
    from qccd.ir.tsir import Instruction, Participant
    arch = architecture()
    prog = TSIR(name="through", arch_spec="arch/helios_98.arch.json")
    prog.add(Instruction(type="init", id=prog.next_id(), placement={"q0": "LO0", "q1": "LO1", "q2": "LO2"},
                         quanta={"q0": 0.0, "q1": 0.0, "q2": 0.0}))
    for a, b in (("LO2", "LO1"), ("LO1", "LO0")):
        prog.add(Instruction(type="simd", id=prog.next_id(), cls="helios_shuttle", mode="inter",
                             participants=(Participant("q2", a, b),)))
    assert passings(prog, arch) == [f"instruction 2: q2 leaves LO1 past ['q1']"]


def test_a_full_ring_is_refused():
    """The ring needs a hole to turn: 82 qubits on its 82 sites cannot be served."""
    with pytest.raises(helios_router.Refused):
        helios_router.compile_helios(_qasm(82, "h q[0];\n"), architecture())


def test_a_batch_runs_its_gates_in_the_eight_zones_at_once():
    """Eight independent CNOTs are one batch: one MS step plays them all, in eight zones."""
    q = _qasm(16, "".join(f"cx q[{2 * k}],q[{2 * k + 1}];\n" for k in range(8)))
    prog, _ = helios_router.compile_helios(q, architecture())
    pos, zones = {}, set()
    for ins in prog.instructions:
        pos.update(ins.placement or {})
        pos.update({p.ion: p.dst for p in ins.participants})
        if ins.type == "gate" and ins.gate == "MS" and len(ins.pairs) == 8:
            zones = {pos[a] for a, b in ins.pairs if pos[a] == pos[b]}
    assert len(zones) == 8 and all(z[1] == "O" for z in zones)


def test_a_laser_step_split_by_chain_position_keeps_the_certificate_right():
    """qccd.compile.laser_positions: a pulse on two ions at different positions of their traps becomes
    two steps, and the certificate's witnesses and clock follow."""
    from qccd.api import Machine
    from qccd.compile.laser_positions import split_by_position
    from qccd.ir.tsir import Instruction
    arch = Machine.chain(2, name="two_traps").arch
    s0, s1 = sorted(n for n, v in arch.device.nodes.items() if v.kind == "site")[:2]
    prog = TSIR(name="t", arch_spec="arch/two_traps.arch.json")
    prog.add(Instruction(type="init", id=prog.next_id(), placement={"a": s0, "b": s0, "c": s1},
                         quanta={"a": 0.0, "b": 0.0, "c": 0.0}))
    prog.add(Instruction(type="gate", id=prog.next_id(), gate="R", mode="intra", arity=1,
                         params=((1.0, 0.0), (2.0, 0.0)), ions=("a", "c"), meta={"op": [0, 1]}))
    cert = {"moves": [], "gates": [{"dag": 0, "instr": 1, "t": 5, "ions": ["a"]}, {"dag": 1, "instr": 1, "t": 5, "ions": ["c"]}],
            "claims": {"cycles": 2}}
    out, c, added = split_by_position(prog, cert, arch)
    gates = [i for i in out.instructions if i.type == "gate"]
    if added == 0:            # both ions stand at position 1 of their chains: one step suffices
        assert len(gates) == 1
        return
    assert added == 1 and [g.ions for g in gates] == [("a",), ("c",)]
    assert [g.params for g in gates] == [((1.0, 0.0),), ((2.0, 0.0),)]
    assert {g["dag"]: g["instr"] for g in c["gates"]} == {0: gates[0].id, 1: gates[1].id}
    assert [g["t"] for g in c["gates"]] == [5, 6] and c["claims"]["cycles"] == 3
