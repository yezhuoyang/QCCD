"""QCCD's own lowering: a FormalRV `.qec` program -> a gadget program (`.alg`) on the town.

FormalRV's `.qec` says what happens to code blocks: allocate, prepare a magic state, merge and
split (a Pauli-product measurement), extract, decode, apply a Pauli frame.  The gadget
library says how a trapped-ion machine does each of those, at verified places.  This module
is the bridge between the two, and it is QCCD's (FormalRV is input only; it is not changed).

    .qec statement                                  .alg instruction                  place
    alloc LogicalAncilla[j] + prepare |Y+> -> it    prep aJ Y                         pharmacy (inject.Y)
    alloc / prepare |+> / checks of a merge         (the bench's own probe ion)
    mergedK M = merge P with ... -> c[lo,hi)        ppm P -> mK                       Pauli-product bench
      ... single factor, block never used again     read aJ X -> mK                   post office
    extract M rounds=r                              (the bench's own rounds)
    decode ... / decode ...                         decode <the merge's blocks>       decoder
    ... = split M                                   (the bench lets the blocks go)
    frame on c[lo,hi) P                             if mK: <p> <block>  per factor    archive
    frame P                                         <p> <block>        per factor    archive
    LogicData[i] (an input of the program)          prep qI Z  (marked input)         school

Every emitted line carries the `.qec` statement it implements (`Lowered.qec_of`), and through
`provenance.link_upper` the `.qppm` PPM and the `.prog` line.  A `.qec` statement this lowering
does not know is refused by name, never dropped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import layers, provenance


class NotLowered(ValueError):
    pass


@dataclass
class Lowered:
    text: str                              # the .alg program
    lines: list                            # [(alg text, qec line or 0, role)]
    qec_of: dict = field(default_factory=dict)       # alg line (1-based) -> qec line
    role_of: dict = field(default_factory=dict)      # alg line -> 'input' | 'qec' | 'header'
    linked: dict = field(default_factory=dict)       # qec line -> [alg lines] (incl. absorbed)
    absorbed: dict = field(default_factory=dict)     # qec line -> why no instruction of its own
    blocks: dict = field(default_factory=dict)       # .alg block -> .qec block
    outcomes: dict = field(default_factory=dict)     # .alg outcome -> qec record band


_FACTOR = re.compile(r"([XYZ])\[(LogicData|LogicalAncilla)\[(\d+)\]\[0\]\]")


def _factors(text: str) -> list:
    return [(p, ("q" if fam == "LogicData" else "a") + idx) for p, fam, idx in _FACTOR.findall(text)]


def _band(text: str, reg: str):
    m = re.search(rf"\b{reg}\[(\d+),(\d+)\)", text)
    return (int(m[1]), int(m[2])) if m else None


def lower(qec: layers.Stage, *, title: str = "", floorplan: str = "fullstack",
          inputs: str = "Z") -> Lowered:
    prog = qec.program
    if prog is None:
        raise NotLowered("the .qec has no program")
    if qec.gadgets():
        raise NotLowered("this lowering handles a .qec program body; gadget calls come next "
                         f"({', '.join(qec.gadgets())})")
    # the data blocks: Surface[k] LogicData in the program header
    m = re.search(r"\w+\[(\d+)\]\s+LogicData", prog.header.text)
    n_data = int(m[1]) if m else 0
    stmts = list(prog.body)
    kinds = [provenance._qec_kind(s) for s in stmts]

    # liveness: the last statement that names each block
    last_use: dict = {}
    for i, s in enumerate(stmts):
        for _, b in _factors(s.text):
            last_use[b] = i
        mm = re.search(r"LogicalAncilla\[(\d+),\d+\)", s.text)
        if mm:
            last_use.setdefault(f"a{mm[1]}", i)

    out = Lowered(text="", lines=[])

    def emit(text: str, qline: int, role: str = "qec"):
        out.lines.append((text, qline, role))

    def absorb(s: layers.Stmt, why: str, into: str):
        out.absorbed[s.line] = f"{why} ({into})"

    for i in range(n_data):
        emit(f"prep q{i} {inputs}", 0, "input")
        out.blocks[f"q{i}"] = f"LogicData[{i}]"
    merges: list = []                                # [(band, outcome, blocks, qec line)]
    current = None                                   # the merge whose group we are in
    k = 0
    for i, (s, kd) in enumerate(zip(stmts, kinds)):
        t = s.text
        if kd == "alloc_block":
            absorb(s, "a block is allocated where it is prepared", "the next prepare")
        elif kd == "magic":
            mm = re.search(r"prepare (\|[^>]+>)\s*->\s*LogicalAncilla\[(\d+),\d+\)", t)
            if not mm:
                raise NotLowered(f".qec line {s.line}: {t!r}")
            state, j = mm[1], mm[2]
            if state != "|Y+>":
                raise NotLowered(f".qec line {s.line}: no gadget prepares {state} yet")
            emit(f"prep a{j} Y", s.line)
            out.blocks[f"a{j}"] = f"LogicalAncilla[{j}]"
        elif kd in ("alloc", "prepare"):
            absorb(s, "the merge's ancillas and their |+> are the bench's own probe ion", "the merge")
        elif kd == "merge":
            fs = _factors(t.split("= merge", 1)[1].split(" with ")[0])
            band = _band(t.split("->")[-1], "c")
            name = f"m{k}"
            k += 1
            blocks = [b for _, b in fs]
            if len(fs) == 1 and last_use.get(blocks[0], -1) <= _group_end(stmts, kinds, i):
                p, b = fs[0]
                emit(f"read {b} {p} -> {name}", s.line)
            else:
                emit("ppm " + " ".join(f"{p} {b}" for p, b in fs) + f" -> {name}", s.line)
            merges.append((band, name, blocks, s.line))
            out.outcomes[name] = band
            current = merges[-1]
        elif kd == "extract":
            absorb(s, "the merged patch's rounds are the bench's own rounds", "the merge")
        elif kd == "decode":
            if current is None:
                raise NotLowered(f".qec line {s.line}: a decode outside a merge group")
            if s.line == _first_decode(stmts, kinds, i, current[3]):
                emit("decode " + " ".join(current[2]), s.line)
            else:
                absorb(s, "both decodes of a merge are one call of the decoder", "the decode above")
        elif kd == "split":
            absorb(s, "the bench lets the blocks go when it has measured", "the merge")
        elif kd == "frame_on":
            band = _band(t, "c")
            owner = next((mg for mg in merges if mg[0][0] <= band[0] and band[1] <= mg[0][1]), None)
            if owner is None:
                raise NotLowered(f".qec line {s.line}: no merge wrote c{list(band)}")
            for p, b in _factors(t):
                emit(f"if {owner[1]}: {p.lower()} {b}", s.line)
        elif kd == "frame":
            for p, b in _factors(t):
                emit(f"{p.lower()} {b}", s.line)
        else:
            raise NotLowered(f".qec line {s.line}: no gadget lowering for {t!r}")

    header = [f"title {title or 'a FormalRV program on the gadget town'}", f'floorplan "{floorplan}"']
    body = [ln for ln, _, _ in out.lines]
    out.text = "\n".join(header + body) + "\n"
    for n, (ln, ql, role) in enumerate(out.lines, start=len(header) + 1):
        out.role_of[n] = role
        if ql:
            out.qec_of[n] = ql
            out.linked.setdefault(ql, []).append(n)
    # an absorbed statement is linked to the instruction it was absorbed into: the next one
    # emitted for its group (the merge, or the decode above it)
    for ql in out.absorbed:
        nxt = [n for n, q in sorted(out.qec_of.items()) if q >= ql]
        prv = [n for n, q in sorted(out.qec_of.items()) if q <= ql]
        target = (nxt[0] if "merge" in out.absorbed[ql] and nxt else prv[-1] if prv else nxt[0])
        out.linked.setdefault(ql, []).append(target)
    return out


def _group_end(stmts, kinds, i) -> int:
    """The index of the merge's split (the end of its group)."""
    for j in range(i + 1, len(stmts)):
        if kinds[j] == "split":
            return j
    return i


def _first_decode(stmts, kinds, i, merge_line) -> int:
    start = next(j for j, s in enumerate(stmts) if s.line == merge_line)
    for j in range(start, len(stmts)):
        if kinds[j] == "decode":
            return stmts[j].line
    return -1
