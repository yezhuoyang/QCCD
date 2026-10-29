"""Shor at scale, layer three: every template compiled once, and the run's time composed.

FormalRV's Shor is nine templates and the rotation chains (tests/test_scale_symbolic.py);
each is compiled ONCE by the town's own scheduler on a district of the machine and checked
there by the place and machine rules every gadget page runs (`scale.compile`).  The run's
time is then a sum over the symbolic program (`program.duration_ns`), exact, at any size.
What is held here:

* every template is a `.alg` program the parser reads, its guarded groups (a T gate's S
  correction) written as gates called under their guard, as qec2alg writes them;
* a template compiled once holds every place rule (a CNOT, and a Toffoli with its guarded
  corrections);
* the composed time is the sum over every instance the run executes: with every template
  taking 1 ns and a rotation level 1 ns, it is the number of template instances plus the
  number of rotation levels, counted here by walking the run instance by instance.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from qccd.fullstack import layers
from qccd.fullstack.scale import program as P
from qccd.fullstack.scale.compile import compile_template, template_alg

REPO = Path(__file__).resolve().parents[1]
WITNESS = dict(m=4, bits=3, N=3, a=2, ainv=2, rec=19)


def _prog(w=WITNESS):
    from qccd.fullstack import formalrv
    try:
        formalrv.check_pin()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"the pinned FormalRV clone is needed: {e}")
    st = layers.read(formalrv.root() / "FormalRV" / "Examples" / "Shor" / "Shor.qec", "qec")
    return P.build(st, params=w)


def test_every_template_is_a_program_the_parser_reads():
    from qccd.gadget.fold import parse_folded
    prog = _prog()
    assert len(prog.templates) == 9
    for t in prog.templates.values():
        alg = parse_folded(template_alg(t), name=t.tid)
        assert any(i.kind == "call" for i in alg.instructions)
    # the Toffoli's guarded corrections are gates called under their guard
    toff = next(t for t in prog.templates.values() if len(t.lines) == 230)
    src = template_alg(toff)
    assert "gate tpl_w1 " in src and "if o4: tpl_w1 " in src.replace("  ", " ")


@pytest.mark.parametrize("lines", [32, 230])
def test_a_template_compiled_once_holds_every_place_rule(lines):
    from qccd.gadget.library import LeafLibrary
    prog = _prog()
    t = next(t for t in prog.templates.values() if len(t.lines) == lines)
    leaves = LeafLibrary(str(REPO / "out" / "gadgets" / "cache"), log=None)
    c = compile_template(t, REPO / "examples" / "gadgets" / "floorplans" / "fullstack.floor", leaves)
    assert c["checks"]["failed"] == [], c["checks"]
    assert {"G1", "G5", "G9", "G11", "H1"} <= set(c["checks"]["passed"])
    assert c["span_us"] > 0 and c["instructions"] == len(t.lines)


def _instances(prog) -> int:
    """Template instances plus rotation levels, walked one instance at a time."""
    n = 0

    def body(b, env):
        nonlocal n
        for node in b:
            if isinstance(node, P.Seg):
                n += 1
            elif isinstance(node, P.Loop):
                for v in range(node.lo(env), node.hi(env)):
                    body(node.body, {**env, node.var: v})
            elif isinstance(node, P.Let):
                body(node.body, {**env, node.var: node.value(env)})
            elif isinstance(node, P.Pred):
                body(node.body, env)                      # a slot: reserved
            elif isinstance(node, P.Call):
                body(prog.body_of(node, env), {f: e(env) for f, e in node.skeys + node.args})
            elif isinstance(node, P.Rot):
                n += max(0, node.k(env) - 1)
    body(prog.root, dict(prog.params))
    return n


def test_the_runs_time_is_composed_exactly_without_walking_the_run():
    for w in (WITNESS, dict(m=6, bits=4, N=7, a=2, ainv=4, rec=25)):
        prog = _prog(w)
        ns = P.duration_ns(prog, {tid: 1 for tid in prog.templates}, lambda k, sign, nf: max(0, k - 1))
        assert ns == _instances(prog)
