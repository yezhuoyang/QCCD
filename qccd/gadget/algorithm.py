"""Logical algorithms over the verified place library: a small language and its ideal circuit.

One instruction per line; `#` starts a comment.  Blocks are named by the program; each is
one d = 3 surface-code logical qubit.

    title Bell pair by transversal CNOT
    prep  q0 X                 a fresh block in |+̄⟩ (Z for |0̄⟩)            -> a prep place
    se    q0 3                 syndrome rounds on a block (1 or 3)            -> a clinic
    cx    q0 q1                transversal CNOT, q0 controls                  -> a workshop
    zz    q0 q1 -> m           lattice surgery: measure Z̄Z̄ (xx: X̄X̄)         -> a bridge
    ppm   Z q0 X q1 Z q2 -> m  any product of one X̄ or Z̄ from each of 1-3 blocks,
                                 measured by a probe ion (non-destructive)     -> a Pauli-product bench
    prep  q0 Y                 a fresh block in |Ȳ⟩ (S|+⟩ injected; distance 1) -> an injection place
    read  q0 Z -> a            destructive readout in Z or X                  -> a post office
    inject t                   a block holding the factory's magic state      -> factory + injection
                                 (T†|+̄⟩: the factory's own verified output)
    s     q0                   logical S̄ by consuming a |Ȳ⟩ block             -> injection + bridge + post
    sx    q0                   logical √X̄ the same way, by X̄X̄ surgery        -> injection + bridge + post
    x     q0                   a logical Pauli, kept in software              -> the archive
    z     q0                     (also `y`); no ion is touched
    store q0                   rest the block in a logical zone
    decode q0                  call the decoder on the syndromes q0 has left      -> the decoder
    decode                       (no block: every syndrome not yet decoded)
    if m: s q0                 any instruction may be guarded: it runs only when the
    if m ^ k: cx q0 q1           condition holds (`!m`, `m == 0`, `m ^ k` also spell one)
    if m & k: z q0             a conjunction of parities: both must hold

Any instruction may end in `@ name` to pin it to one instance of the design, and

    floorplan "shor"            names the machine the program runs on (default: the town)
                                (examples/gadgets/floorplans/shor.floor)

**Gates.**  A program may define gates from instructions and call them like instructions
(docs/ARCHITECTURE.md §6):

    include "standard"          the standard gates (qccd/gadget/gates/standard.alg)

    gate t q implements T:      # parameters are blocks; the body is indented
      inject m                  # a block the body creates is the body's own
      x m                       # T†|+̄⟩ -> T|+̄⟩, in software
      zz q m -> a               # an outcome the body names is the body's own
      read m X -> b
      decode q m
      if a: s q
      if b: z q

    t q3                        # a call: the body, with q -> q3 and fresh local names

A call expands to its body.  Every block the body creates and every outcome it names is
renamed fresh for that call (`t3_m`, `t3_a`), so two calls never share them, and the body
sees nothing of the program but its parameters.  A guarded call (`if m1: tdg c`) guards
every instruction of the body that can be guarded, conjoined with the body's own guard;
what cannot be guarded (`prep`, `inject`, `se`, `store`, `decode`) runs either way, which is
harmless because it only creates, rests, checks or decodes.  Inside a guarded call an
instruction may name an outcome: everything that reads it is inside the same call, under
the same guard.  `implements U` names the gate the body is meant to be; the contract
checker (`qccd.gadget.logic.contract`) proves it, branch by branch, on the states the
places are verified to produce.

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
**condition** is a conjunction of parities, each the parity of a constant and some measured
outcomes, `c ⊕ m₁ ⊕ m₂ …`, each `m` the result of an earlier `-> m`. `!m` and `m == 0` mean
`1 ⊕ m`; `a & b` (or `a and b`) holds when both do.  A guarded instruction may not name a new
outcome (`-> m`), because then later lines would depend on whether it ran; everything else
may be guarded, including `s` (whose correction is Clifford, so a frame cannot hold it) and
`cx`.

A logical Pauli is *not* an operation on ions: it is a line in the classical memory, and
the archive flips every later outcome the frame anticommutes with (GADGETS.md §10).  The S̄
gadget is a composite of places that are each verified on their own -- a |Ȳ⟩ injection, a
Z̄Z̄ lattice surgery and an X̄ readout -- plus a Pauli correction in the archive; the √X̄
gadget is the same with X̄X̄ surgery and a Z̄ readout.

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
           "parse_condition", "parse_guard", "Guard", "Gate", "SOFTWARE", "CLASSICAL",
           "UNGUARDABLE", "GATES_DIR", "CONTRACT_ARITY"]

#: which place category an instruction needs
CATEGORY_OF = {"prep": "prep", "se": "se", "cx": "operation", "zz": "surgery", "xx": "surgery",
               "ppm": "ppm",
               "read": "readout", "inject": "injection", "store": "zone", "s": "injection",
               "sx": "injection", "x": "archive", "y": "archive", "z": "archive",
               "decode": "decoder"}

#: instructions that touch no ion at all: they are writes into the classical memory
SOFTWARE = ("x", "y", "z")

#: instructions that run entirely in the classical half: no ion moves and no block is held
#: at a door for them, so a block waiting for its next quantum instruction is not parked
#: just because the decoder was called in between
CLASSICAL = SOFTWARE + ("decode",)

#: what no guard can gate: it creates, rests, checks or decodes, so in a guarded call it runs
#: either way
UNGUARDABLE = ("prep", "inject", "se", "store", "decode")

#: where `include "NAME"` finds NAME.alg
GATES_DIR = Path(__file__).resolve().parent / "gates"

#: the gates a definition may say it implements, and how many blocks each acts on
CONTRACT_ARITY = {"I": 1, "X": 1, "Y": 1, "Z": 1, "H": 1, "S": 1, "Sdg": 1, "SX": 1, "SXdg": 1,
                  "T": 1, "Tdg": 1, "CX": 2, "CZ": 2, "SWAP": 2, "CCX": 3, "CCZ": 3,
                  "CSWAP": 3}

PRIMITIVES = ("prep", "inject", "s", "sx", "x", "y", "z", "se", "store", "cx", "zz", "xx",
              "ppm", "decode", "read")

#: the most blocks one Pauli-product measurement may name (the bench has three doors)
PPM_MAX = 3


class AlgorithmError(ValueError):
    def __init__(self, line: int, text: str, reason: str):
        super().__init__(f"line {line}: {reason}: {text.strip()}")
        self.line, self.text, self.reason = line, text, reason


# ------------------------------------------------------------------------------- guards

@dataclass(frozen=True)
class Guard:
    """A condition: a conjunction of parities, each `(constant, (outcome, ...))`, holding
    when every parity is 1."""

    parities: tuple[tuple[int, tuple[str, ...]], ...]
    text: str = ""

    @property
    def names(self) -> tuple[str, ...]:
        out: list[str] = []
        for _, ns in self.parities:
            for n in ns:
                if n not in out:
                    out.append(n)
        return tuple(out)

    def holds(self, branch: dict[str, int]) -> bool:
        for const, names in self.parities:
            c = const
            for n in names:
                c ^= branch.get(n, 0) & 1
            if not c:
                return False
        return True

    def __and__(self, other: "Guard | None") -> "Guard":
        if other is None:
            return self
        text = f"{self.text} & {other.text}" if self.text and other.text else (self.text or other.text)
        return Guard(self.parities + other.parities, text)

    def rename(self, m: dict[str, str]) -> "Guard":
        return Guard(tuple((c, tuple(m.get(n, n) for n in ns)) for c, ns in self.parities),
                     _rename_text(self.text, m))

    def json(self) -> dict:
        """One parity is `{const, cvars}` (as it always was); a conjunction also lists its
        parities under `all`, and `cvars` is every outcome any of them reads."""
        d = {"const": self.parities[0][0] if len(self.parities) == 1 else None,
             "cvars": list(self.names), "text": self.text}
        if len(self.parities) > 1:
            d["all"] = [{"const": c, "cvars": list(ns)} for c, ns in self.parities]
        return d


def _rename_text(text: str, m: dict[str, str]) -> str:
    return re.sub(r"\w+", lambda k: m.get(k.group(0), k.group(0)), text)


# ------------------------------------------------------------------------------- instructions

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
    #: the guard: the instruction runs when it holds
    cond: Guard | None = None
    #: the guard as written, for the page and the reports
    guard: str | None = None
    #: the calls this instruction was expanded from, outermost first: `[{gate, call, line}]`
    call: list = field(default_factory=list)
    #: a syndrome round the refresh pass added (`qccd.gadget.refresh`): run no earlier than
    #: this, in microseconds
    not_before: float | None = None
    refresh: bool = False
    #: `ppm`: the Pauli letter on each block, in the order of `blocks`
    paulis: list = field(default_factory=list)
    #: for an instruction a gate call expanded: the line of the gate BODY it came from
    #: (`line` stays the call site's), and the file that line is in ("" for the program)
    src_line: int | None = None
    src_origin: str = ""

    def runs(self, branch: dict[str, int]) -> bool:
        """Does this instruction run, given a value for every guard outcome?"""
        return self.cond is None or self.cond.holds(branch)

    @property
    def op(self) -> str:
        if self.kind == "prep" and self.basis == "Y":
            return "inject.Y"
        if self.kind == "prep":
            return f"prep.{self.basis}"
        if self.kind == "ppm":
            return "ppm." + "".join(self.paulis)
        if self.kind == "read":
            return f"read.{self.basis}"
        if self.kind == "se":
            return f"se.{self.rounds}"
        if self.kind in ("s", "sx"):
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
            d["cond"] = self.cond.json()
        if self.call:
            d["call"] = [dict(c) for c in self.call]
        if self.refresh:
            d["refresh"] = True
            d["not_before"] = self.not_before
        if self.paulis:
            d["paulis"] = list(self.paulis)
        if self.src_line is not None:
            d["src"] = self.src_line
            if self.src_origin:
                d["src_origin"] = self.src_origin
        return d


@dataclass
class Gate:
    """A gate definition: parameters, a body of instruction lines, the gate it implements."""

    name: str
    params: list[str]
    body: list[tuple[int, str]]            # (line number, text), dedented
    contract: str | None = None
    line: int = 0
    source: str = ""                       # the file it came from, or "" for the program

    def to_json(self) -> dict:
        return {"name": self.name, "params": list(self.params), "contract": self.contract,
                "line": self.line, "source": self.source,
                "body": [t for _, t in self.body]}


@dataclass
class Algorithm:
    name: str
    title: str
    source: str
    instructions: list[Instr] = field(default_factory=list)
    gates: dict[str, Gate] = field(default_factory=dict)
    includes: list[str] = field(default_factory=list)
    #: every call made, in program order: `{gate, call, line, args, first, last}` (instruction
    #: indices of the expansion)
    calls: list[dict] = field(default_factory=list)
    #: the floorplan the program names (`floorplan "NAME"`), or None for the town
    floorplan: str | None = None

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
            for n in (ins.cond.names if ins.cond else ()):
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
        """(instruction index, block) -> the next instruction that uses the block's ions.

        Neither a `decode` nor a Pauli kept in software counts: one reads the block's
        syndromes out of the places that measured them, the other is a line in the classical
        memory, and neither touches the block's ions -- so a block whose next instruction is
        `x q` and whose next *move* is far away goes to rest, rather than wait at a door."""
        out: dict[tuple[int, str], int | None] = {}
        last: dict[str, int] = {}
        for ins in reversed(self.instructions):
            if ins.kind in CLASSICAL:
                continue
            for b in ins.blocks:
                out[(ins.index, b)] = last.get(b)
                last[b] = ins.index
        return out

    def next_quantum(self) -> dict[int, int | None]:
        """instruction index -> the next instruction that moves ions (not a `decode`, not a
        Pauli kept in software).

        What a block is waiting for: the decoder can be called, and the archive written, in
        between without the block having to leave the door it is standing at."""
        out: dict[int, int | None] = {}
        nxt = None
        for ins in reversed(self.instructions):
            out[ins.index] = nxt
            if ins.kind not in CLASSICAL:
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
                if ins.basis in ("X", "Y"):
                    c.add("H", [q[0]])
                if ins.basis == "Y":
                    c.add("S", [q[0]])
                alive.append(q[0])
            elif ins.kind == "inject":
                inputs.append(q[0])
                alive.append(q[0])
            elif ins.kind == "s":
                c.add("S", [q[0]])
            elif ins.kind == "sx":
                # √X = H S H exactly
                c.add("H", [q[0]])
                c.add("S", [q[0]])
                c.add("H", [q[0]])
            elif ins.kind in SOFTWARE:
                c.add(ins.kind.upper(), [q[0]])
            elif ins.kind == "cx":
                c.add("CX", [q[0], q[1]])
            elif ins.kind in ("zz", "xx"):
                p = "Z" if ins.kind == "zz" else "X"
                recs.append((ins.index, c.mpp({q[0]: p, q[1]: p}, label={"instruction": ins.index})))
            elif ins.kind == "ppm":
                recs.append((ins.index, c.mpp(dict(zip(q, ins.paulis)), label={"instruction": ins.index})))
            elif ins.kind == "read":
                if ins.basis == "X":
                    c.add("H", [q[0]])
                c.add("M", [q[0]], labels=[{"instruction": ins.index}])
                recs.append((ins.index, len(c.records) - 1))
                alive.remove(q[0])
        return c, inputs, alive, recs


_LINE = re.compile(r"^\s*(\w+)\s*(.*?)\s*(?:@\s*(\w+))?\s*$")
_GUARD = re.compile(r"^\s*if\s+(.+?)\s*:\s*(.+)$", re.I)
_GATE = re.compile(r"^gate\s+(\w+)((?:\s+\w+)*?)(?:\s+implements\s+(\w+))?\s*:\s*$")
_INCLUDE = re.compile(r'^include\s+"([\w.-]+)"\s*$')
_FLOORPLAN = re.compile(r'^floorplan\s+"([\w.-]+)"\s*$')


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


def parse_guard(text: str) -> Guard:
    """`m`, `m ^ k`, `a & b`, `a and !b` -> a Guard: a conjunction of parities."""
    parts = [p for p in re.split(r"&|\band\b", text) if p.strip()]
    if not parts:
        raise ValueError(f"empty condition {text!r}")
    return Guard(tuple(parse_condition(p) for p in parts), text.strip())


# ------------------------------------------------------------------------------- parsing

def _split(raw: str) -> tuple[str | None, str, list[str], str | None, str | None] | None:
    """(guard text, word, args, cvar, @at) of one instruction line, or None if empty."""
    text = raw.split("#", 1)[0].rstrip()
    if not text.strip():
        return None
    guard_text = None
    g = _GUARD.match(text)
    if g:
        guard_text, text = g.group(1).strip(), g.group(2)
    m = _LINE.match(text)
    if not m:
        raise ValueError("not an instruction")
    word, rest, at = m.group(1).lower(), m.group(2), m.group(3)
    cvar = None
    if "->" in rest:
        rest, cvar = (s.strip() for s in rest.split("->", 1))
    return guard_text, word, rest.split(), cvar, at


class _Parser:
    def __init__(self, alg: Algorithm):
        self.alg = alg
        self.born: set[str] = set()
        self.gone: set[str] = set()
        self.cvars: set[str] = set()
        self.calls: dict[str, int] = {}

    # -- definitions --------------------------------------------------------------------
    def read(self, source: str, origin: str = "") -> None:
        """Read a file: definitions and includes are kept, instructions emitted."""
        lines = source.splitlines()
        i = 0
        while i < len(lines):
            lineno, raw = i + 1, lines[i]
            i += 1
            text = raw.split("#", 1)[0].rstrip()
            if not text.strip():
                continue
            stripped = text.strip()
            inc = _INCLUDE.match(stripped)
            if inc:
                self.include(inc.group(1), lineno, raw)
                continue
            fl = _FLOORPLAN.match(stripped)
            if fl and not origin:
                self.alg.floorplan = fl.group(1)
                continue
            if stripped.startswith("gate ") or stripped == "gate":
                g = _GATE.match(stripped)
                if not g:
                    raise AlgorithmError(lineno, raw, "write: gate NAME PARAM ... [implements U]:")
                name, params = g.group(1).lower(), g.group(2).split()
                if name in PRIMITIVES or name in ("title", "gate", "include", "if"):
                    raise AlgorithmError(lineno, raw, f"'{name}' is an instruction, not a gate name")
                if name in self.alg.gates:
                    raise AlgorithmError(lineno, raw, f"gate {name} is already defined")
                if len(set(params)) != len(params) or not params:
                    raise AlgorithmError(lineno, raw, "a gate takes one or more distinct blocks")
                contract = g.group(3)
                if contract is not None:
                    if contract not in CONTRACT_ARITY:
                        raise AlgorithmError(lineno, raw, f"{contract} is not a gate a contract "
                                             f"can name ({', '.join(CONTRACT_ARITY)})")
                    if CONTRACT_ARITY[contract] != len(params):
                        raise AlgorithmError(lineno, raw, f"{contract} acts on "
                                             f"{CONTRACT_ARITY[contract]} block(s), the gate on "
                                             f"{len(params)}")
                body: list[tuple[int, str]] = []
                while i < len(lines):
                    nxt = lines[i]
                    content = nxt.split("#", 1)[0]
                    if content.strip() and not nxt[:1].isspace():
                        break
                    if content.strip():
                        body.append((i + 1, nxt.strip()))
                    i += 1
                if not body:
                    raise AlgorithmError(lineno, raw, f"gate {name} has no body")
                self.alg.gates[name] = Gate(name, params, body, contract, lineno, origin)
                continue
            if raw[:1].isspace() and not origin:
                # an indented line outside a gate: the program is flat
                pass
            if origin:
                raise AlgorithmError(lineno, raw, f"{origin} may only define gates and include")
            if stripped.startswith("title"):
                parts = stripped.split(None, 1)
                if parts[0] == "title":
                    self.alg.title = parts[1].strip() if len(parts) > 1 else self.alg.title
                    continue
            try:
                sp = _split(raw)
            except ValueError as exc:
                raise AlgorithmError(lineno, raw, str(exc)) from None
            if sp is None:
                continue
            guard_text, word, args, cvar, at = sp
            if guard_text is not None and word == "title":
                raise AlgorithmError(lineno, raw, "a title cannot be guarded")
            guard = None
            if guard_text is not None:
                try:
                    guard = parse_guard(guard_text)
                except ValueError as exc:
                    raise AlgorithmError(lineno, raw, str(exc)) from None
            self.emit(word, args, cvar, at, guard, True, lineno, raw, [])

    def include(self, name: str, lineno: int, raw: str) -> None:
        if name in self.alg.includes:
            return
        p = GATES_DIR / f"{name}.alg"
        if not p.exists():
            raise AlgorithmError(lineno, raw, f"no gate file {name!r} (looked in {GATES_DIR.name}/)")
        self.alg.includes.append(name)
        self.read(p.read_text(encoding="utf-8"), origin=f"{name}.alg")

    # -- expansion ----------------------------------------------------------------------
    def emit(self, word, args, cvar, at, guard: Guard | None, written: bool, lineno, raw, call):
        if word in self.alg.gates:
            return self.expand(self.alg.gates[word], args, cvar, at, guard, lineno, raw, call)
        return self.primitive(word, args, cvar, at, guard, written, lineno, raw, call)

    def expand(self, gate: Gate, args, cvar, at, guard, lineno, raw, call):
        if cvar:
            raise AlgorithmError(lineno, raw, f"gate {gate.name} names no outcome")
        if len(args) != len(gate.params):
            raise AlgorithmError(lineno, raw, f"gate {gate.name} takes {len(gate.params)} "
                                 f"block(s): {' '.join(gate.params)}")
        if len(set(args)) != len(args):
            raise AlgorithmError(lineno, raw, f"gate {gate.name} needs distinct blocks")
        if len(call) > 32:
            raise AlgorithmError(lineno, raw, f"gate {gate.name} calls itself")
        k = self.calls.get(gate.name, 0) + 1
        self.calls[gate.name] = k
        tag = f"{gate.name}{k}"
        frame = {"gate": gate.name, "call": tag, "line": lineno, "contract": gate.contract}
        rename = dict(zip(gate.params, args))
        first = len(self.alg.instructions)
        for bl, btext in gate.body:
            try:
                sp = _split(btext)
            except ValueError as exc:
                raise AlgorithmError(lineno, raw, f"in gate {gate.name} (line {bl}): {exc}") from None
            if sp is None:
                continue
            g_text, word, bargs, bcvar, bat = sp

            def ren(n: str) -> str:
                if n in rename:
                    return rename[n]
                return f"{tag}_{n}"
            inner = None
            if g_text is not None:
                try:
                    inner = parse_guard(g_text)
                except ValueError as exc:
                    raise AlgorithmError(lineno, raw, f"in gate {gate.name} (line {bl}): {exc}") from None
                inner = inner.rename({n: ren(n) for n in inner.names})
            nargs = list(bargs)
            if word in ("se",) and len(nargs) == 2:
                nargs = [ren(nargs[0]), nargs[1]]
            elif word in ("prep", "read"):
                nargs = [ren(nargs[0])] + nargs[1:] if nargs else nargs
            elif word == "ppm":
                # letters stay letters: rename only the blocks (every second argument)
                nargs = [a if i % 2 == 0 else ren(a) for i, a in enumerate(nargs)]
            else:
                nargs = [ren(a) for a in nargs]
            combined = (guard & inner) if guard is not None else inner
            text = f"{('if ' + combined.text + ': ') if combined else ''}{word} " + \
                   " ".join(nargs) + (f" -> {ren(bcvar)}" if bcvar else "")
            self.emit(word, nargs, ren(bcvar) if bcvar else None, bat or at, combined,
                      g_text is not None, lineno, text, call + [dict(frame, body=bl)])
        self.alg.calls.append({"gate": gate.name, "call": tag, "line": lineno, "args": list(args),
                               "contract": gate.contract, "depth": len(call),
                               "first": first, "last": len(self.alg.instructions) - 1,
                               "guard": guard.text if guard else None})

    def primitive(self, word, args, cvar, at, guard, written, lineno, raw, call):
        def need(k):
            if len(args) != k:
                raise AlgorithmError(lineno, raw, f"'{word}' takes {k} argument(s)")

        def alive(b):
            if b not in self.born:
                raise AlgorithmError(lineno, raw, f"block {b} is used before prep or inject")
            if b in self.gone:
                raise AlgorithmError(lineno, raw, f"block {b} was already read out")

        if word not in PRIMITIVES:
            raise AlgorithmError(lineno, raw, f"unknown instruction '{word}'")
        if guard is not None and word in UNGUARDABLE:
            if written and word == "decode":
                raise AlgorithmError(lineno, raw, "the decoder cannot be guarded: every "
                                     "syndrome is decoded in every branch, or a later frame "
                                     "would depend on whether it was")
            if not written:
                guard = None             # a guarded call: what cannot be gated runs either way
        ins = Instr(index=len(self.alg.instructions), line=lineno, text=raw, kind=word, blocks=[],
                    cvar=cvar, at=at, call=list(call))
        if call and call[-1].get("body") is not None:
            # the body line this instruction came from: the innermost call's
            ins.src_line = call[-1]["body"]
            g = self.alg.gates.get(call[-1]["gate"])
            ins.src_origin = g.source if g else ""
        if guard is not None:
            ins.cond, ins.guard = guard, guard.text
            if cvar and written:
                raise AlgorithmError(lineno, raw, "a guarded instruction cannot name a new "
                                     "outcome: later lines would depend on whether it ran")
            unknown = [n for n in guard.names if n not in self.cvars]
            if unknown:
                raise AlgorithmError(lineno, raw, f"the condition reads {unknown[0]}, which no "
                                     f"earlier instruction measured")
        if word == "prep":
            need(2)
            if args[1].upper() not in ("Z", "X", "Y"):
                raise AlgorithmError(lineno, raw, "prep basis is Z, X or Y")
            if args[0] in self.born:
                raise AlgorithmError(lineno, raw, f"block {args[0]} already exists")
            ins.blocks, ins.basis = [args[0]], args[1].upper()
            self.born.add(args[0])
        elif word == "inject":
            need(1)
            if args[0] in self.born:
                raise AlgorithmError(lineno, raw, f"block {args[0]} already exists")
            ins.blocks = [args[0]]
            self.born.add(args[0])
        elif word in ("s", "sx") or word in SOFTWARE:
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
        elif word == "ppm":
            if not args or len(args) % 2 or not 1 <= len(args) // 2 <= PPM_MAX:
                raise AlgorithmError(lineno, raw, f"write: ppm P q [P q ...] with 1 to {PPM_MAX} "
                                     f"blocks, each P one of X, Z")
            letters, blks = [a.upper() for a in args[0::2]], args[1::2]
            bad = [p for p in letters if p not in ("X", "Z")]
            if bad:
                raise AlgorithmError(lineno, raw, f"{bad[0]} is not X or Z (the bench measures "
                                     f"products of X̄ and Z̄)")
            if len(set(blks)) != len(blks):
                raise AlgorithmError(lineno, raw, "a block is named twice")
            for b in blks:
                alive(b)
            ins.blocks, ins.paulis = list(blks), letters
        elif word in ("cx", "zz", "xx"):
            need(2)
            if args[0] == args[1]:
                raise AlgorithmError(lineno, raw, "a two-block instruction needs two blocks")
            alive(args[0])
            alive(args[1])
            ins.blocks = [args[0], args[1]]
        elif word == "decode":
            if cvar:
                raise AlgorithmError(lineno, raw, "decode names no outcome: it corrects the "
                                     "ones the measurements already named")
            for b in args:
                if b not in self.born:
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
            self.gone.add(args[0])
        if cvar:
            if cvar in self.cvars:
                raise AlgorithmError(lineno, raw, f"the outcome {cvar} is already named")
            self.cvars.add(cvar)
        self.alg.instructions.append(ins)


def parse_algorithm(source: str, name: str = "algorithm") -> Algorithm:
    alg = Algorithm(name=name, title=name, source=source)
    _Parser(alg).read(source)
    if not alg.instructions:
        raise AlgorithmError(0, "", "no instructions")
    return alg


def parse_algorithm_file(path) -> Algorithm:
    p = Path(path)
    return parse_algorithm(p.read_text(encoding="utf-8"), name=p.stem)
