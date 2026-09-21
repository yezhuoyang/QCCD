"""`decode` is a TSIR instruction: calling the decoder is part of the instruction set.

Before this the studio's instruction set had no way to say where a measurement's outcome
goes.  A compiled error-correction programme measured its ancillas and stopped; the
leaderboard's QEC-cycle layer drew a decoder and dashed wires under the device, and nothing
in the programme ever used them, so nothing on the page ever lit.

`decode` names the ions whose outcomes it sends.  It is CLASSICAL: it moves no ion and
costs no machine time, because the decoder works alongside the ions -- only something that
reads its answer would wait.  That is the property most worth pinning, because the board
ranks designs by replayed time: `qccd.compile.decode.insert_decodes` puts a decode into
every leaderboard programme, and if a decode cost anything every ranking would move.

The browser twin (`engine.js` `_pDecode`) is pinned by `tests/test_engine_parity.py`'s
programme lane, whose corpus now carries every form of the verb; the lit wires are
measured on the page by `tests/test_board_decode.py`.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from qccd import Machine
from qccd.arch import load
from qccd.compile.decode import insert_decodes, windows
from qccd.cost import corrected_model
from qccd.ir.listing import disassemble
from qccd.ir.tsir import TSIR, INSTRUCTION_TYPES, Instruction, validate_program
from qccd.verify import verify

ROOT = Path(__file__).resolve().parents[1]
ARCH = ROOT / "arch"
BOARD = ROOT / "Compiler" / "build" / "q06" / "ring144_24v_a24_stride.cooled.tsir.json"


def small_program():
    """Two ancillas measured twice each around some motion: two windows."""
    m = Machine.load(ARCH / "grid9x9.arch.json")
    sites = [n.id for n in m.arch.device.nodes.values() if n.kind == "site"]
    p = m.program("t")
    p.init({"a0": sites[0], "a1": sites[1]})
    p.measure(["a0"]).measure(["a1"]).reset(["a0", "a1"])
    p.measure(["a0", "a1"])
    return m, p


# --------------------------------------------------------------------------- the IR


def test_decode_is_in_the_instruction_set_and_round_trips():
    assert "decode" in INSTRUCTION_TYPES
    ins = Instruction(type="decode", id=7, ions=("a0", "a1"), meta={"window": 0})
    back = Instruction.from_json(json.loads(json.dumps(ins.to_json())))
    assert back.type == "decode" and back.ions == ("a0", "a1") and back.meta["window"] == 0


def test_a_decode_must_name_what_it_sends():
    prog = TSIR(name="t", arch_spec="x",
                instructions=[Instruction(type="init", id=0, placement={"a": "S0"}),
                              Instruction(type="decode", id=1)], id_seq=2)
    assert any("decode names no ions" in e for e in validate_program(prog))


# --------------------------------------------------------------------------- the verb


def test_the_verb_defaults_to_everything_measured_since_the_last_decode():
    m, p = small_program()
    p.decode()
    got = [i for i in p.build().instructions if i.type == "decode"]
    # a1 was measured last, a0 just before it: the order of their LATEST measurements
    assert [i.ions for i in got] == [("a0", "a1")]
    p.measure(["a1"]).decode()
    got = [i for i in p.build().instructions if i.type == "decode"]
    assert got[-1].ions == ("a1",), "the default stops at the previous decode"
    p.decode(["a0"])
    assert [i for i in p.build().instructions if i.type == "decode"][-1].ions == ("a0",)


def test_the_verb_refuses_when_there_is_nothing_to_send():
    m = Machine.load(ARCH / "grid9x9.arch.json")
    site = next(n.id for n in m.arch.device.nodes.values() if n.kind == "site")
    p = m.program("t").init({"a0": site})
    with pytest.raises(ValueError, match="nothing to decode"):
        p.decode()


# --------------------------------------------------------------------------- the replay


def test_a_decode_costs_no_machine_time_and_no_rule_sees_it():
    """What `qccd-c2` asked for: every rule that switches on the type must do the right
    thing with a decode, and the right thing is NOTHING -- it touches no ion."""
    m, p = small_program()
    plain = p.build()
    p.decode()
    with_ = p.build()
    arch, model = m.arch, corrected_model()
    a = verify(plain, arch, model, check_metrics=False)
    b = verify(with_, arch, model, check_metrics=False)
    assert b.result.total_us == a.result.total_us
    assert b.result.total_cost == a.result.total_cost
    assert b.rules.summary() == a.rules.summary()
    cyc = next(c for c in b.result.cycles if c.type == "decode")
    assert cyc.t0 == cyc.t1 and cyc.cost == 0.0
    assert not [v for v in b.rules.violations if v.instr_id == cyc.instr_id]


def test_the_listing_says_decode():
    m, p = small_program()
    p.decode()
    prog = p.build()
    lst = disassemble(prog, m.arch)
    row = next(ln for ln in lst.lines if ln.op == "DECODE")
    assert "decoder" in row.detail and "a0" in row.detail and row.width == 2


# --------------------------------------------------------------------------- the pass


def test_the_pass_calls_the_decoder_once_per_window():
    m, p = small_program()
    prog = insert_decodes(p.build())
    decs = [(k, i) for k, i in enumerate(prog.instructions) if i.type == "decode"]
    # a0 and a1 measured, then measured again: two windows, each decoded straight after
    # its last measurement
    assert [i.ions for _, i in decs] == [("a0", "a1"), ("a0", "a1")]
    for k, i in decs:
        assert prog.instructions[k - 1].type == "measure"
    assert not validate_program(prog)
    assert insert_decodes(prog) is prog, "the pass is idempotent"


@pytest.mark.skipif(not BOARD.exists(), reason="the compiled leaderboard programme is not built")
def test_the_pass_moves_no_leaderboard_number():
    """The board ranks by replayed time, under two cost models: neither may move, no rule
    verdict may change, and every compiled instruction keeps its id (the certificate and
    the circuit join key on it).  `ring144_24v a24` re-uses 24 ancillas across 144 checks,
    so its round is six windows and the decoder is called six times."""
    arch = load(ARCH / "ring144_24v.arch.json")
    prog = TSIR.load(BOARD)
    dec = insert_decodes(prog)
    assert not validate_program(dec)
    assert [len(w[1]) for w in windows(prog)] == [24] * 6
    assert [i.id for i in dec.instructions if i.type != "decode"] == [i.id for i in prog.instructions]
    assert min(i.id for i in dec.instructions if i.type == "decode") > max(i.id for i in prog.instructions)
    for table in ("qccdsim_jones", "transport_excitation"):
        model = corrected_model(table)
        a = verify(prog, arch, model, check_metrics=False)
        b = verify(dec, arch, model, check_metrics=False)
        assert b.result.total_us == a.result.total_us, table
        assert b.rules.summary() == a.rules.summary(), table
