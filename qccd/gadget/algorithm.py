"""Logical algorithms over the verified place library: a small language and its ideal circuit.

One instruction per line; `#` starts a comment.  Blocks are named by the program; each is
one d = 3 surface-code logical qubit.

    title Bell pair by transversal CNOT
    prep  q0 X                 a fresh block in |+̄⟩ (Z for |0̄⟩)            -> a prep place
    se    q0 3                 syndrome rounds on a block (1 or 3)            -> a clinic
    cx    q0 q1                transversal CNOT, q0 controls                  -> a workshop
    zz    q0 q1 -> m           lattice surgery: measure Z̄Z̄ (xx: X̄X̄)         -> a bridge
    read  q0 Z -> a            destructive readout in Z or X                  -> a post office
    inject t                   a block holding the factory's magic state      -> factory + injection
    s     q0                   logical S̄ by consuming a |Ȳ⟩ block             -> injection + bridge + post
    x     q0                   a logical Pauli, kept in software              -> the archive
    z     q0                     (also `y`); no ion is touched
    store q0                   rest the block in a logical zone
    decode q0                  call the decoder on the syndromes q0 has left      -> the decoder
    decode                       (no block: every syndrome not yet decoded)
    if m: s q0                 any instruction may be guarded: it runs only when the
    if m ^ k: cx q0 q1           condition holds (`!m`, `m == 0`, `m ^ k` also spell one)

Any instruction may end in `@ name` to pin it to one instance of the design.

**Decoding is an instruction.**  Every place that measures leaves a *syndrome window* in its
readout buffer; nothing is decoded until the program says `decode`.  Then each window's bits
travel down that place's syndrome wire to the decoder, the decoder runs one job per window,
and the Pauli-frame update it produces goes down the frame wire to the classical memory.
`decode q0 q1` takes the windows those blocks were measured in (and those of any resource
block an S̄ gadget consumed into them); a bare `decode` takes every window still waiting.
A logical outcome is not a result until its window is decoded, so a guard may not read one
before that, and a program must decode everything it measured before it ends (G11).

**Guards.** `if <condition>: <instruction>` is a classically controlled operation: the ions
travel to the place and the place reserves the op either way (the worst case, so the
schedule stays static), but the pulses fire only when the condition holds -- a pre-compiled
branch, as trapped-ion control systems run them (AQT M-ACTION, arXiv:2101.11390). A
**condition** is exact and linear: the parity of a constant and some measured outcomes,
`c ⊕ m₁ ⊕ m₂ …`, each `m` the result of an earlier `-> m`. `!m` and `m == 0` mean `1 ⊕ m`.
A guarded instruction may not name a new outcome (`-> m`), because then later lines would
depend on whether it ran; everything else may be guarded, including `s` (whose correction is
Clifford, so a frame cannot hold it) and `cx`.

A logical Pauli is *not* an operation on ions: it is a line in the classical memory, and
the archive flips every later outcome the frame anticommutes with (GADGETS.md §10).  The S̄
gadget is a composite of places that are each verified on their own -- a |Ȳ⟩ injection, a
Z̄Z̄ lattice surgery and an X̄ readout -- plus a Pauli correction in the archive.

`ideal_circuit` is the program at the logical level -- one qubit per block, the gate or
measurement each instruction means, and `inject` as an *input* (the factory's state is
whatever it is; everything after it is Clifford).  `qccd.gadget.logic.flat` checks the
flattened hardware schedule against exactly this circuit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .logic.circuit import Circuit

__all__ = ["Instr", "Algorithm", "parse_algorithm", "AlgorithmError", "CATEGORY_OF",
           "parse_condition", "SOFTWARE", "CLASSICAL"]

#: which place category an instruction needs
CATEGORY_OF = {"prep": "prep", "se": "se", "cx": "operation", "zz": "surgery", "xx": "surgery",
               "read": "readout", "inject": "injection", "store": "zone", "s": "injection",
               "x": "archive", "y": "archive", "z": "archive", "decode": "decoder"}

#: instructions that touch no ion at all: they are writes into the classical memory
SOFTWARE = ("x", "y", "z")

#: instructions that run entirely in the classical half: no ion moves and no block is held
#: at a door for them, so a block waiting for its next quantum instruction is not parked
#: just because the decoder was called in between
CLASSICAL = SOFTWARE + ("decode",)


class AlgorithmError(ValueError):
    def __init__(self, line: int, text: str, reason: str):
        super().__init__(f"line {line}: {reason}: {text.strip()}")
        self.line, self.text, self.reason = line, text, reason


@dataclass
class Instr:
    index: int
    line: int
    text: str
    kind: str
    blocks: list[str]
    basis: str | None = None
    rounds: int = 1
    cvar: str | None = None
    at: str | None = None
    #: the guard: `(constant, (cvar, …))` -- the instruction runs when the parity is 1
    cond: tuple[int, tuple[str, ...]] | None = None
    #: the guard as written, for the page and the reports
    guard: str | None = None

    def runs(self, branch: dict[str, int]) -> bool:
        """Does this instruction run, given a value for every guard outcome?"""
        if self.cond is None:
            return True
        const, names = self.cond
        for n in names:
            const ^= branch.get(n, 0) & 1
        return bool(const)

    @property
    def op(self) -> str:
        if self.kind == "prep":
            return f"prep.{self.basis}"
        if self.kind == "read":
            return f"read.{self.basis}"
        if self.kind == "se":
            return f"se.{self.rounds}"
        if self.kind == "s":
            return "inject.Y"
        if self.kind in SOFTWARE:
            return "update"
        return self.kind          # `decode` is the decoder's own op

    def to_json(self) -> dict:
        d = {"id": self.index, "line": self.line, "text": self.text.strip(), "kind": self.kind,
             "op": self.op, "blocks": list(self.blocks)}
        if self.cvar:
            d["cvar"] = self.cvar
        if self.at:
            d["at"] = self.at
        if self.cond:
            d["guard"] = self.guard
            d["cond"] = {"const": self.cond[0], "cvars": list(self.cond[1])}
        return d


@dataclass
class Algorithm:
    name: str
    title: str
    source: str
    instructions: list[Instr] = field(default_factory=list)

    @property
    def blocks(self) -> list[str]:
        out: list[str] = []
        for ins in self.instructions:
            for b in ins.blocks:
                if b not in out:
                    out.append(b)
        return out

    @property
    def guard_cvars(self) -> list[str]:
        """The outcomes some guard reads, in program order."""
        out: list[str] = []
        for ins in self.instructions:
            for n in (ins.cond[1] if ins.cond else ()):
                if n not in out:
                    out.append(n)
        return out

    def branches(self) -> list[dict[str, int]]:
        """Every assignment of the guard outcomes: one branch to check per assignment.

        A guarded schedule is one piece of hardware whose meaning depends on bits measured
        while it runs, so it is signed off once per branch -- the way a dynamic circuit is
        stated to be correct: *for every outcome*, the logical channel is the intended one."""
        names = self.guard_cvars
        if not names:
            return [{}]
        out = []
        for mask in range(1 << len(names)):
            out.append({n: (mask >> i) & 1 for i, n in enumerate(names)})
        return out

    def cvar_instruction(self) -> dict[str, int]:
        """Which instruction measured each named outcome."""
        return {ins.cvar: ins.index for ins in self.instructions if ins.cvar}

    def next_use(self) -> dict[tuple[int, str], int | None]:
        """(instruction index, block) -> the next instruction that uses the block.

        A `decode` does not count: it reads the block's syndromes out of the places that
        measured them and never touches the block's ions."""
        out: dict[tuple[int, str], int | None] = {}
        last: dict[str, int] = {}
        for ins in reversed(self.instructions):
            if ins.kind == "decode":
                continue
            for b in ins.blocks:
                out[(ins.index, b)] = last.get(b)
                last[b] = ins.index
        return out

    def next_quantum(self) -> dict[int, int | None]:
        """instruction index -> the next instruction that is not a `decode`.

        What a block is waiting for: the decoder can be called in between without the block
        having to leave the door it is standing at."""
        out: dict[int, int | None] = {}
        nxt = None
        for ins in reversed(self.instructions):
            out[ins.index] = nxt
            if ins.kind != "decode":
                nxt = ins.index
        return out

    def ideal_circuit(self, branch: dict[str, int] | None = None
                      ) -> tuple[Circuit, list[str], list[str], list[tuple[int, int]]]:
        """(logical circuit, input blocks, output blocks, [(instruction, record)]).

        With `branch`, a guarded instruction is included exactly when its condition holds
        under that assignment: the ideal circuit of one branch of the dynamic program."""
        c = Circuit(self.blocks)
        inputs, alive = [], []
        recs: list[tuple[int, int]] = []
        for ins in self.instructions:
            q = ins.blocks
            if ins.cond is not None and not ins.runs(branch or {}):
                continue
            if ins.kind == "prep":
                c.add("R", [q[0]])
                if ins.basis == "X":
                    c.add("H", [q[0]])
                alive.append(q[0])
            elif ins.kind == "inject":
                inputs.append(q[0])
                alive.append(q[0])
            elif ins.kind == "s":
                c.add("S", [q[0]])
            elif ins.kind in SOFTWARE:
                c.add(ins.kind.upper(), [q[0]])
            elif ins.kind == "cx":
                c.add("CX", [q[0], q[1]])
            elif ins.kind in ("zz", "xx"):
                p = "Z" if ins.kind == "zz" else "X"
                recs.append((ins.index, c.mpp({q[0]: p, q[1]: p}, label={"instruction": ins.index})))
            elif ins.kind == "read":
                if ins.basis == "X":
                    c.add("H", [q[0]])
                c.add("M", [q[0]], labels=[{"instruction": ins.index}])
                recs.append((ins.index, len(c.records) - 1))
                alive.remove(q[0])
        return c, inputs, alive, recs


_LINE = re.compile(r"^\s*(\w+)\s*(.*?)\s*(?:@\s*(\w+))?\s*$")
_GUARD = re.compile(r"^\s*if\s+(.+?)\s*:\s*(.+)$", re.I)


def parse_condition(text: str) -> tuple[int, tuple[str, ...]]:
    """`m`, `!m`, `m == 0`, `m ^ k`, `1` -> `(constant, (cvar, …))`, a parity."""
    const = 0
    names: list[str] = []
    for term in re.split(r"[\^⊕+]", text):
        t = term.strip()
        if not t:
            raise ValueError(f"empty term in the condition {text!r}")
        eq = re.match(r"^(\w+)\s*(?:==|=)\s*([01])$", t)
        if eq:
            t, want = eq.group(1), int(eq.group(2))
            const ^= 1 - want
        elif t.startswith("!") or t.lower().startswith("not "):
            t = t[1:].strip() if t.startswith("!") else t[4:].strip()
            const ^= 1
        if t in ("0", "1"):
            const ^= int(t)
            continue
        if not re.match(r"^\w+$", t):
            raise ValueError(f"{t!r} is not an outcome name")
        if t in names:                      # m ⊕ m = 0
            names.remove(t)
        else:
            names.append(t)
    return const, tuple(names)


def parse_algorithm(source: str, name: str = "algorithm") -> Algorithm:
    alg = Algorithm(name=name, title=name, source=source)
    born: set[str] = set()
    gone: set[str] = set()
    cvars: set[str] = set()
    for lineno, raw in enumerate(source.splitlines(), start=1):
        text = raw.split("#", 1)[0].rstrip()
        if not text.strip():
            continue
        guard_text = None
        g = _GUARD.match(text)
        if g:
            guard_text, text = g.group(1).strip(), g.group(2)
        m = _LINE.match(text)
        if not m:
            raise AlgorithmError(lineno, raw, "not an instruction")
        word, rest, at = m.group(1).lower(), m.group(2), m.group(3)
        if guard_text and word == "title":
            raise AlgorithmError(lineno, raw, "a title cannot be guarded")
        if word == "title":
            alg.title = rest.strip() or alg.title
            continue
        cvar = None
        if "->" in rest:
            rest, cvar = (s.strip() for s in rest.split("->", 1))
        args = rest.split()

        def need(k):
            if len(args) != k:
                raise AlgorithmError(lineno, raw, f"'{word}' takes {k} argument(s)")

        def alive(b):
            if b not in born:
                raise AlgorithmError(lineno, raw, f"block {b} is used before prep or inject")
            if b in gone:
                raise AlgorithmError(lineno, raw, f"block {b} was already read out")

        ins = Instr(index=len(alg.instructions), line=lineno, text=raw, kind=word, blocks=[],
                    cvar=cvar, at=at)
        if guard_text is not None:
            try:
                ins.cond = parse_condition(guard_text)
            except ValueError as exc:
                raise AlgorithmError(lineno, raw, str(exc)) from None
            ins.guard = guard_text
            if cvar:
                raise AlgorithmError(lineno, raw, "a guarded instruction cannot name a new "
                                     "outcome: later lines would depend on whether it ran")
            unknown = [n for n in ins.cond[1] if n not in cvars]
            if unknown:
                raise AlgorithmError(lineno, raw, f"the condition reads {unknown[0]}, which no "
                                     f"earlier instruction measured")
        if word == "prep":
            need(2)
            if args[1].upper() not in ("Z", "X"):
                raise AlgorithmError(lineno, raw, "prep basis is Z or X")
            if args[0] in born:
                raise AlgorithmError(lineno, raw, f"block {args[0]} already exists")
            ins.blocks, ins.basis = [args[0]], args[1].upper()
            born.add(args[0])
        elif word == "inject":
            need(1)
            if args[0] in born:
                raise AlgorithmError(lineno, raw, f"block {args[0]} already exists")
            ins.blocks = [args[0]]
            born.add(args[0])
        elif word in ("s",) or word in SOFTWARE:
            need(1)
            alive(args[0])
            ins.blocks = [args[0]]
        elif word in ("se", "store"):
            if word == "se" and len(args) == 2:
                if args[1] not in ("1", "3"):
                    raise AlgorithmError(lineno, raw, "se takes 1 or 3 rounds")
                ins.rounds = int(args[1])
            elif len(args) != 1:
                raise AlgorithmError(lineno, raw, f"'{word}' takes a block")
            alive(args[0])
            ins.blocks = [args[0]]
        elif word in ("cx", "zz", "xx"):
            need(2)
            if args[0] == args[1]:
                raise AlgorithmError(lineno, raw, "a two-block instruction needs two blocks")
            alive(args[0])
            alive(args[1])
            ins.blocks = [args[0], args[1]]
        elif word == "decode":
            if guard_text is not None:
                raise AlgorithmError(lineno, raw, "the decoder cannot be guarded: every "
                                     "syndrome is decoded in every branch, or a later frame "
                                     "would depend on whether it was")
            if cvar:
                raise AlgorithmError(lineno, raw, "decode names no outcome: it corrects the "
                                     "ones the measurements already named")
            for b in args:
                if b not in born:
                    raise AlgorithmError(lineno, raw, f"block {b} has not been measured: it "
                                         f"was never prepared or injected")
            if len(set(args)) != len(args):
                raise AlgorithmError(lineno, raw, "a block is named twice")
            # a block that has been read out may still be decoded: that is when its last
            # window, the readout's own, becomes a result
            ins.blocks = list(args)
        elif word == "read":
            need(2)
            if args[1].upper() not in ("Z", "X"):
                raise AlgorithmError(lineno, raw, "read basis is Z or X")
            alive(args[0])
            ins.blocks, ins.basis = [args[0]], args[1].upper()
            gone.add(args[0])
        else:
            raise AlgorithmError(lineno, raw, f"unknown instruction '{word}'")
        if cvar:
            if cvar in cvars:
                raise AlgorithmError(lineno, raw, f"the outcome {cvar} is already named")
            cvars.add(cvar)
        alg.instructions.append(ins)
    if not alg.instructions:
        raise AlgorithmError(0, "", "no instructions")
    return alg


def parse_algorithm_file(path) -> Algorithm:
    p = Path(path)
    return parse_algorithm(p.read_text(encoding="utf-8"), name=p.stem)
