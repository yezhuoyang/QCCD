"""LogicQ programs, as text, into the instructions the gadget layer realises.

LogicQ has no single file format holding both code declarations and a program: its
declarations are Lean command macros (`ChainQ/SurfaceSyntax.lean`) and its statement
parsers read one layer each (`Compiler/Mixed/Parse.lean` for `Logical …` and the Mixed IR
keywords, `PPM/Parse.lean` for measurements).  A `.lq` file is simply both, in order, each
statement written exactly as the LogicQ parser for its layer reads it.  The rule this
module keeps is **a statement that parses here parses there**: where LogicQ is strict
(spaces, not commas, between CNOT operands; `↦` in a measurement target) this is strict
too, and says what LogicQ would accept instead.

Lexing follows LogicQ's statement lexer: `//` starts a comment, statements split on
newlines and `;`.  A `code … { … }` declaration runs to its closing brace, because its
fields are `;`-separated inside it.

Accepted (docs/GADGETS.md §6.1):

    code q as BivariateBicycle { l = 6; m = 6; A = x^3 + y + y^2; B = y^3 + x + x^2;
                                 params = (72, 12, 6); }
    code b as Bare
    Logical H q[0]            Logical CNOT q[0] r[0]        Logical measure q[0]↦Z -> c0
    transversal 0 H           transversalCNOT q[0] r[0] [[1]]
    pauli X q[0]              magic T q[0]                  ppm c1 := M q[0]↦Z, r[1]↦Z
    blockTransversal q H      transversalCNOTBatch q r      (the paper's block-level forms)
    Logical blockTransversal q H          Logical transversalBatch q -> r   (the Tutorial's)

Everything else is refused with its line number and the reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .codes import CSSCode, PolyError, bare, bivariate_bicycle, parse_poly

__all__ = ["LogicalProgram", "Instruction", "Declaration", "ParseError", "parse",
           "parse_file"]


class ParseError(ValueError):
    def __init__(self, line: int, text: str, reason: str):
        super().__init__(f"line {line}: {reason}\n    {text.strip()}")
        self.line = line
        self.text = text
        self.reason = reason


@dataclass(frozen=True)
class Declaration:
    block: str
    family: str
    line: int
    code: CSSCode
    declared: tuple[int, int, int] | None = None


@dataclass(frozen=True)
class Instruction:
    """One logical instruction, normalised.

    `kind` is what the realisation table in GADGETS.md §6.2 dispatches on:

    | kind          | from                                                      |
    |---------------|-----------------------------------------------------------|
    | `pauli`       | `pauli P q[i]`, `Logical X/Z q[i]`, `ppm frame P(q[i])`    |
    | `ppm`         | `ppm c := M …`, `Logical measure … -> c`, `c := M …`       |
    | `transversal` | `transversal b g`, `blockTransversal`, `Logical H/S` on k=1 |
    | `gate1`       | `Logical H/S q[i]` on a block with k > 1 (a PPM gadget)    |
    | `gate2`       | `Logical CNOT/CZ q[i] r[j]` (a PPM gadget)                 |
    | `tcnot`       | `transversalCNOT q[i] r[j] M`                              |
    | `tcnot_batch` | `transversalCNOTBatch`, `Logical transversalBatch`         |
    | `magic`       | `magic T q[i]`, `Logical T q[i]`                           |
    | `discard`     | `ppm discard q[i]`                                         |
    """

    index: int
    line: int
    text: str
    kind: str
    op: str = ""
    qubits: tuple[tuple[str, int], ...] = ()
    target: tuple[tuple[str, int, str], ...] = ()
    cvar: str | None = None
    incidence: tuple[tuple[int, ...], ...] | None = None
    layer: str = ""

    @property
    def blocks(self) -> tuple[str, ...]:
        seen: list[str] = []
        for b, *_ in (*self.qubits, *self.target):
            if b not in seen:
                seen.append(b)
        return tuple(seen)

    def to_json(self) -> dict:
        d = {"id": self.index, "line": self.line, "text": self.text.strip(),
             "kind": self.kind, "op": self.op, "blocks": list(self.blocks)}
        if self.qubits:
            d["qubits"] = [list(q) for q in self.qubits]
        if self.target:
            d["target"] = [list(t) for t in self.target]
        if self.cvar is not None:
            d["cvar"] = self.cvar
        if self.layer:
            d["layer"] = self.layer
        return d


@dataclass
class LogicalProgram:
    name: str
    source: str
    declarations: dict[str, Declaration] = field(default_factory=dict)
    instructions: list[Instruction] = field(default_factory=list)

    @property
    def blocks(self) -> list[str]:
        """Declaration order: what `transversal <nat>` numbers."""
        return list(self.declarations)

    def code(self, block: str) -> CSSCode:
        return self.declarations[block].code

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for ins in self.instructions:
            kinds[ins.kind] = kinds.get(ins.kind, 0) + 1
        return {"name": self.name, "blocks": len(self.declarations),
                "logical_qubits": sum(d.code.k for d in self.declarations.values()),
                "data_qubits": sum(d.code.n for d in self.declarations.values()),
                "instructions": len(self.instructions), "by_kind": kinds}


# --------------------------------------------------------------------------- lexing

_IDENT = r"[A-Za-z0-9_]+"
_QUBIT = re.compile(rf"^\s*({_IDENT})\s*\[\s*(\d+)\s*\]\s*$")
_CVAR = re.compile(r"^c?(\d+)$")


def _statements(source: str) -> list[tuple[int, str]]:
    """`(line, text)` pieces: comments dropped, split on newlines and `;` outside braces."""
    out: list[tuple[int, str]] = []
    buf: list[str] = []
    start: int | None = None
    depth = 0

    def flush():
        nonlocal buf, start
        text = "".join(buf)
        if text.strip():
            out.append((start, text))
        buf, start = [], None

    for n, raw in enumerate(source.splitlines(), start=1):
        for ch in raw.split("//", 1)[0]:
            if ch == ";" and depth == 0:
                flush()
                continue
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth < 0:
                    raise ParseError(n, raw, "'}' without a matching '{'")
            if start is None and not ch.isspace():
                start = n
            buf.append(ch)
        if depth == 0:
            flush()
        else:
            buf.append("\n")
    if depth:
        raise ParseError(start or 0, "".join(buf), "unclosed '{' in a code declaration")
    return out


# --------------------------------------------------------------------------- parsing


def parse(source: str, name: str = "program") -> LogicalProgram:
    prog = LogicalProgram(name=name, source=source)
    for line, text in _statements(source):
        stmt = text.strip()
        head = stmt.split(None, 1)[0] if stmt else ""
        if head in ("code", "indexed_code", "indexed_css"):
            _declaration(prog, line, stmt)
            continue
        ins = _instruction(prog, line, stmt, len(prog.instructions))
        if ins is not None:
            prog.instructions.append(ins)
    return prog


def parse_file(path) -> LogicalProgram:
    p = Path(path)
    return parse(p.read_text(encoding="utf-8"), name=p.stem)


def _declaration(prog: LogicalProgram, line: int, stmt: str) -> None:
    if stmt.startswith(("indexed_code", "indexed_css")):
        raise ParseError(line, stmt, "indexed declarations carry Lean-term logical bases; "
                         "declare the code with `code … as BivariateBicycle {…}` and the "
                         "basis is derived as LogicQ's deriveLogicalBasis? derives it")
    m = re.match(rf"^code\s+({_IDENT})\s+as\s+({_IDENT})\s*(\{{.*\}})?\s*$", stmt, re.S)
    if not m:
        raise ParseError(line, stmt, "expected `code <name> as <Family> { … }`")
    block, family, body = m.group(1), m.group(2), m.group(3)
    if block in prog.declarations:
        raise ParseError(line, stmt, f"block {block!r} is already declared on line "
                         f"{prog.declarations[block].line}")
    if family == "Bare":
        prog.declarations[block] = Declaration(block, "Bare", line, bare("bare"))
        return
    if family != "BivariateBicycle":
        raise ParseError(line, stmt, f"code family {family!r} is not realised by the gadget "
                         f"layer yet (have: BivariateBicycle, Bare)")
    if not body:
        raise ParseError(line, stmt, "a BivariateBicycle declaration needs a { … } body")
    fields = {}
    for part in body.strip()[1:-1].split(";"):
        if not part.strip():
            continue
        if "=" not in part:
            raise ParseError(line, stmt, f"expected `field = value`, found {part.strip()!r}")
        key, value = part.split("=", 1)
        fields[key.strip()] = value.strip()
    missing = [f for f in ("l", "m", "A", "B") if f not in fields]
    if missing:
        raise ParseError(line, stmt, f"missing field(s): {', '.join(missing)}")
    try:
        l, m_ = int(fields["l"]), int(fields["m"])
        a = parse_poly(fields["A"], l, m_)
        b = parse_poly(fields["B"], l, m_)
    except (ValueError, PolyError) as exc:
        raise ParseError(line, stmt, str(exc)) from None
    declared = None
    if "params" in fields:
        pm = re.match(r"^\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\)$", fields["params"])
        if not pm:
            raise ParseError(line, stmt, "params must be `(n, k, d)`")
        declared = tuple(int(g) for g in pm.groups())
    try:
        code = bivariate_bicycle(None, l, m_, a, b, declared)
    except ValueError as exc:
        raise ParseError(line, stmt, str(exc)) from None
    if declared and (declared[0] != code.n or declared[1] != code.k):
        raise ParseError(line, stmt, f"params say [[{declared[0]},{declared[1]},"
                         f"{declared[2]}]] but the matrices give n = {code.n}, k = {code.k}")
    prog.declarations[block] = Declaration(block, "BivariateBicycle", line, code, declared)


def _qubit(prog: LogicalProgram, line: int, stmt: str, tok: str) -> tuple[str, int]:
    m = _QUBIT.match(tok)
    if not m:
        raise ParseError(line, stmt, f"expected a logical qubit `block[i]`, found {tok!r}")
    block, idx = m.group(1), int(m.group(2))
    if block not in prog.declarations:
        raise ParseError(line, stmt, f"no code block named {block!r} is declared")
    k = prog.declarations[block].code.k
    if idx >= k:
        raise ParseError(line, stmt, f"{block} has k = {k}; there is no logical {idx}")
    return block, idx


def _block(prog: LogicalProgram, line: int, stmt: str, tok: str) -> str:
    if tok.isdigit():
        blocks = prog.blocks
        if int(tok) >= len(blocks):
            raise ParseError(line, stmt, f"block number {tok} names no declaration "
                             f"({len(blocks)} declared)")
        return blocks[int(tok)]
    if tok not in prog.declarations:
        raise ParseError(line, stmt, f"no code block named {tok!r} is declared")
    return tok


def _mtarget(prog, line, stmt, text: str) -> tuple[tuple[str, int, str], ...]:
    factors = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(.*?)\s*(?:↦|\|->)\s*([XYZ])\S*$", part)
        if not m:
            raise ParseError(line, stmt, f"expected `q[i]↦P` in a measurement target, found "
                             f"{part!r}")
        block, idx = _qubit(prog, line, stmt, m.group(1))
        factors.append((block, idx, m.group(2)))
    if not factors:
        raise ParseError(line, stmt, "an empty measurement target measures nothing")
    seen = set()
    for b, i, _ in factors:
        if (b, i) in seen:
            raise ParseError(line, stmt, f"{b}[{i}] appears twice in one measurement")
        seen.add((b, i))
    return tuple(factors)


def _boolmat(line, stmt, text: str) -> tuple[tuple[int, ...], ...]:
    t = text.strip()
    if not (t.startswith("[") and t.endswith("]")):
        raise ParseError(line, stmt, f"expected a 0/1 matrix `[[…],…]`, found {t!r}")
    rows = re.findall(r"\[([^\[\]]*)\]", t[1:-1])
    out = []
    for r in rows:
        bits = [b.strip() for b in r.split(",") if b.strip()]
        if any(b not in ("0", "1") for b in bits):
            raise ParseError(line, stmt, f"matrix entries are 0 or 1, found [{r}]")
        out.append(tuple(int(b) for b in bits))
    return tuple(out)


def _instruction(prog: LogicalProgram, line: int, stmt: str, index: int):
    toks = stmt.split()
    head = toks[0]

    def mk(kind, **kw):
        return Instruction(index=index, line=line, text=stmt, kind=kind, **kw)

    if head == "Logical":
        return _logical(prog, line, stmt, toks[1:], mk)

    if head == "transversal":
        if len(toks) != 3 or toks[2] not in ("H", "S"):
            raise ParseError(line, stmt, "expected `transversal <block number> H|S`")
        block = _block(prog, line, stmt, toks[1])
        return mk("transversal", op=toks[2], target=tuple(
            (block, i, "") for i in range(prog.code(block).k)), layer="mixed")

    if head == "blockTransversal":
        if len(toks) != 3 or toks[2] not in ("H", "S"):
            raise ParseError(line, stmt, "expected `blockTransversal <block> H|S`")
        block = _block(prog, line, stmt, toks[1])
        return mk("transversal", op=toks[2], target=tuple(
            (block, i, "") for i in range(prog.code(block).k)), layer="paper")

    if head == "transversalCNOT":
        rest = stmt[len(head):].strip()
        m = re.match(r"^(\S+\[\s*\d+\s*\])\s+(\S+\[\s*\d+\s*\])\s+(\[.*\])$", rest)
        if not m:
            raise ParseError(line, stmt, "expected `transversalCNOT q[i] r[j] [[…]]` "
                             "(three space-separated operands)")
        c = _qubit(prog, line, stmt, m.group(1))
        t = _qubit(prog, line, stmt, m.group(2))
        inc = _boolmat(line, stmt, m.group(3))
        nc, nt = prog.code(c[0]).n, prog.code(t[0]).n
        if len(inc) != nc or any(len(r) != nt for r in inc):
            raise ParseError(line, stmt, f"the incidence must be {nc} x {nt} "
                             f"(control block's n by target block's n)")
        return mk("tcnot", op="CNOT", qubits=(c, t), incidence=inc, layer="mixed")

    if head == "transversalCNOTBatch":
        if len(toks) < 3:
            raise ParseError(line, stmt, "expected `transversalCNOTBatch <block> <block>`")
        cb, tb = _block(prog, line, stmt, toks[1]), _block(prog, line, stmt, toks[2])
        return _batch(prog, line, stmt, cb, tb, mk, "paper")

    if head in ("pauli",):
        if len(toks) != 3 or toks[1][:1] not in ("X", "Y", "Z"):
            raise ParseError(line, stmt, "expected `pauli X|Y|Z q[i]`")
        q = _qubit(prog, line, stmt, toks[2])
        return mk("pauli", op=toks[1][0], qubits=(q,), layer="mixed")

    if head == "magic":
        if len(toks) != 3 or toks[1] != "T":
            raise ParseError(line, stmt, "expected `magic T q[i]`")
        q = _qubit(prog, line, stmt, toks[2])
        return mk("magic", op="T", qubits=(q,), layer="mixed")

    if head == "ppm":
        return _ppm(prog, line, stmt, stmt[len(head):].strip(), mk)

    if re.match(r"^c?\d+\s*:=", stmt):
        return _ppm(prog, line, stmt, stmt, mk)

    if head in ("automorphism", "switch", "parallelPPM"):
        raise ParseError(line, stmt, f"`{head}` carries a Lean-term payload with no text "
                         f"form in LogicQ; the gadget layer cannot realise it yet")
    raise ParseError(line, stmt, f"not a LogicQ statement: {head!r}")


def _logical(prog, line, stmt, toks, mk):
    if not toks:
        raise ParseError(line, stmt, "`Logical` needs an instruction")
    op, args = toks[0], toks[1:]
    if op in ("H", "S", "T", "X", "Z"):
        if len(args) != 1:
            raise ParseError(line, stmt, f"expected `Logical {op} q[i]`")
        q = _qubit(prog, line, stmt, args[0])
        if op in ("X", "Z"):
            return mk("pauli", op=op, qubits=(q,), layer="logical")
        if op == "T":
            return mk("magic", op="T", qubits=(q,), layer="logical")
        if prog.code(q[0]).k == 1:
            return mk("transversal", op=op, target=((q[0], 0, ""),), layer="logical")
        return mk("gate1", op=op, qubits=(q,), layer="logical")
    if op in ("CNOT", "CZ"):
        joined = " ".join(args)
        if "," in joined:
            raise ParseError(line, stmt, f"LogicQ separates `Logical {op}` operands with "
                             f"spaces, not a comma: `Logical {op} q[0] r[0]`")
        if len(args) != 2:
            raise ParseError(line, stmt, f"expected `Logical {op} q[i] r[j]`")
        c = _qubit(prog, line, stmt, args[0])
        t = _qubit(prog, line, stmt, args[1])
        if c == t:
            raise ParseError(line, stmt, "control and target are the same logical qubit")
        return mk("gate2", op=op, qubits=(c, t), layer="logical")
    if op == "measure":
        rest = stmt.split("measure", 1)[1]
        if "->" not in rest:
            raise ParseError(line, stmt, "expected `Logical measure q[i]↦P -> c<n>`")
        target, cvar = rest.rsplit("->", 1)
        cv = _cvar(line, stmt, cvar.strip())
        return mk("ppm", op="M", target=_mtarget(prog, line, stmt, target), cvar=cv,
                  layer="logical")
    if op == "blockTransversal":
        if len(args) != 2 or args[1] not in ("H", "S"):
            raise ParseError(line, stmt, "expected `Logical blockTransversal <block> H|S`")
        block = _block(prog, line, stmt, args[0])
        return mk("transversal", op=args[1], target=tuple(
            (block, i, "") for i in range(prog.code(block).k)), layer="tutorial")
    if op == "transversalBatch":
        m = re.match(rf"^({_IDENT})\s*->\s*({_IDENT})$", " ".join(args))
        if not m:
            raise ParseError(line, stmt, "expected `Logical transversalBatch <block> -> <block>`")
        cb, tb = _block(prog, line, stmt, m.group(1)), _block(prog, line, stmt, m.group(2))
        return _batch(prog, line, stmt, cb, tb, mk, "tutorial")
    raise ParseError(line, stmt, f"`Logical {op}` is not a LogicQ logical instruction "
                     f"(have: H S T X Z CNOT CZ measure)")


def _batch(prog, line, stmt, cb, tb, mk, layer):
    if cb == tb:
        raise ParseError(line, stmt, "a transversal CNOT needs two different blocks")
    cc, tc = prog.code(cb), prog.code(tb)
    if cc.n != tc.n or cc.k != tc.k:
        raise ParseError(line, stmt, f"{cb} is [[{cc.n},{cc.k}]] and {tb} is "
                         f"[[{tc.n},{tc.k}]]; the identity incidence needs equal codes")
    pairs = tuple((cb, i, "") for i in range(cc.k)) + tuple((tb, i, "") for i in range(tc.k))
    return mk("tcnot_batch", op="CNOT", target=pairs, layer=layer)


def _ppm(prog, line, stmt, body, mk):
    body = body.strip()
    m = re.match(r"^(c?\d+)\s*:=\s*M\s+(.*)$", body)
    if m:
        cv = _cvar(line, stmt, m.group(1))
        return mk("ppm", op="M", target=_mtarget(prog, line, stmt, m.group(2)), cvar=cv,
                  layer="mixed")
    m = re.match(r"^frame\s+([XYZ])\s*\(\s*(.*?)\s*\)$", body)
    if m:
        q = _qubit(prog, line, stmt, m.group(2))
        return mk("pauli", op=m.group(1), qubits=(q,), layer="ppm")
    m = re.match(r"^discard\s+(.*)$", body)
    if m:
        q = _qubit(prog, line, stmt, m.group(1))
        return mk("discard", op="discard", qubits=(q,), layer="ppm")
    if body == "skip":
        return None
    if body == "abort":
        raise ParseError(line, stmt, "`abort` ends every run; there is nothing to realise")
    raise ParseError(line, stmt, "expected `ppm c<n> := M q[i]↦P, …`, `ppm frame P(q[i])`, "
                     "`ppm discard q[i]` or `ppm skip`")


def _cvar(line, stmt, tok: str) -> str:
    m = _CVAR.match(tok)
    if not m:
        raise ParseError(line, stmt, f"expected a classical variable `c<n>`, found {tok!r}")
    return f"c{m.group(1)}"
