"""Gates built from places, and the contracts that say what they compute (ARCHITECTURE.md §6).

* The S̄ and √X̄ gadgets are S and √X exactly, in every branch, with the corrections the
  scheduler writes into the frame.
* Every standard gate (qccd/gadget/gates/standard.alg) is its contract in every branch, on
  the states the places are verified to make -- and the T gadget as first shipped, which
  forgot that the factory makes T†|+⟩, is not, in any branch.
* Calls expand with fresh names, a guarded call guards its body (conjoined with the body's
  own guards), and a gate that is wrong is found wrong.
* Shor's algorithm for 15, every call counted as its contract, gives the textbook outcomes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from qccd.gadget.algorithm import AlgorithmError, parse_algorithm, parse_algorithm_file
from qccd.gadget.logic.contract import (check_gate, check_gates, check_pi4, program_outcomes)

ROOT = Path(__file__).resolve().parents[1]
SHOR = ROOT / "examples" / "gadgets" / "algorithms" / "shor15.alg"


@pytest.mark.parametrize("kind, U, corr", [("s", "S", "Z̄ when 0 ⊕"), ("sx", "SX", "X̄ when 1 ⊕")])
def test_the_pi4_gadgets_are_their_rotations_in_every_branch(kind, U, corr):
    r = check_pi4(kind)
    assert r["passed"] and r["contract"] == U and r["correction"].startswith(corr), r
    assert len(r["branches"]) == 4 and all(b["ok"] for b in r["branches"])


def test_every_standard_gate_is_its_contract():
    alg = parse_algorithm('include "standard"\nprep q X\n', "lib")
    rows = {r["gate"]: r for r in check_gates(alg)}
    assert set(rows) == {"sdg", "sxdg", "h", "t", "tdg", "cz", "ccx", "cswap"}
    for name, r in rows.items():
        assert r["passed"], (name, r)
        assert all(b["ok"] and abs(b["fidelity"] - 1) < 1e-7 for b in r["branches"]), name
    # T and T† branch on two measurements; a gate made of calls is checked as one branch,
    # its calls counted as the contracts already proved
    assert len(rows["t"]["branches"]) == 4 and len(rows["tdg"]["branches"]) == 4
    assert len(rows["ccx"]["branches"]) == 1


T_AS_FIRST_SHIPPED = """
gate t q implements T:
  inject m
  zz q m -> a
  read m X -> b
  decode q m
  if a: s q
  if b: z q
prep q X
"""


def test_the_t_gadget_as_first_shipped_is_not_t_in_any_branch():
    """It assumed the magic block holds T|+⟩; the factory is verified to make T†|+⟩."""
    alg = parse_algorithm(T_AS_FIRST_SHIPPED, "old")
    r = check_gate(alg.gates["t"], alg.gates)
    assert r["passed"] is False
    assert len(r["branches"]) == 4 and not any(b["ok"] for b in r["branches"])
    # and the example that ships now is the fixed one
    src = (ROOT / "examples" / "gadgets" / "algorithms" / "t_gate.alg").read_text(encoding="utf-8")
    assert "\nx      t" in src.replace("\r\n", "\n")


@pytest.mark.parametrize("body, contract", [
    ("  s q\n  sx q\n  sx q\n", "H"),              # one √X too many
    ("  s q\n  s q\n", "Sdg"),                     # S² = Z, not S†
    ("  cx a b\n", "CZ"),
])
def test_a_wrong_gate_is_found_wrong(body, contract):
    params = "a b" if contract == "CZ" else "q"
    alg = parse_algorithm(f"gate g {params} implements {contract}:\n{body}prep q X\n", "w")
    assert check_gate(alg.gates["g"], alg.gates)["passed"] is False


def test_a_call_expands_with_fresh_names_and_a_guard_reaches_its_body():
    alg = parse_algorithm('include "standard"\nprep a X\nprep b X\nread a X -> m\ndecode a\n'
                          'if m: tdg b\nt b\n', "x")
    t_calls = [c for c in alg.calls if c["gate"] in ("t", "tdg")]
    assert [c["call"] for c in t_calls] == ["tdg1", "t1"]
    inj = [i for i in alg.instructions if i.kind == "inject"]
    assert [i.blocks[0] for i in inj] == ["tdg1_m", "t1_m"]            # fresh, never shared
    guarded = [i for i in alg.instructions if i.call and i.call[0]["call"] == "tdg1"]
    # what can be gated is gated by m -- conjoined with the body's own guard -- and what
    # cannot (inject, decode) runs either way
    for i in guarded:
        if i.kind in ("inject", "decode"):
            assert i.cond is None, i.text
        else:
            assert i.cond is not None and "m" in i.cond.names, i.text
    s_corr = [i for i in guarded if i.kind == "s"]
    assert s_corr and s_corr[0].cond.names == ("m", "tdg1_a")
    assert s_corr[0].runs({"m": 1, "tdg1_a": 1}) and not s_corr[0].runs({"m": 0, "tdg1_a": 1})
    # inside a guarded call the body may name an outcome: only the body reads it
    assert any(i.cvar == "tdg1_b" and i.cond is not None for i in guarded)


@pytest.mark.parametrize("src, why", [
    ("gate g q implements Q:\n  s q\nprep q X\n", "not a gate a contract can name"),
    ("gate g q implements CX:\n  s q\nprep q X\n", "acts on 2"),
    ("gate g q:\n  s q\nprep a X\ng a a\n", "takes 1 block"),
    ("gate g q:\n  g q\nprep a X\ng a\n", "calls itself"),
    ("gate s q:\n  x q\nprep a X\n", "is an instruction"),
    ("gate g q:\n  s r\nprep a X\ng a\n", "used before prep"),
    ("prep a X\nif a: decode\n", "cannot be guarded"),
    ('include "nothing"\nprep a X\n', "no gate file"),
])
def test_a_gate_that_cannot_be_read_says_why(src, why):
    with pytest.raises(AlgorithmError, match=why):
        parse_algorithm(src, "bad")


def test_shor_finds_the_order_of_7_modulo_15():
    alg = parse_algorithm_file(SHOR)
    r = program_outcomes(alg, outcomes=["m1", "m2", "m3"])
    # the phase is 0.m3 m2 m1 = s/4: m1 is always 0, and s = 2 m3 + m2 is uniform
    assert r["distribution"] == {(0, a, b): 0.25 for a in (0, 1) for b in (0, 1)}
    # an odd s gives the order 4, and the factors
    from math import gcd
    assert {gcd(7 ** 2 - 1, 15), gcd(7 ** 2 + 1, 15)} == {3, 5}
    # every gate it calls is proved
    assert all(r["passed"] for r in check_gates(alg))
    kinds = [i.kind for i in alg.instructions]
    assert kinds.count("inject") == 15 and kinds.count("read") == 18
