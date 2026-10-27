"""TSIR as text: one instruction a line.  docs/tsir.md, "Text".

The JSON form is what passes read; the text form is what a person reads, and what the
debugger lights line by line.  The two are the same program -- `parse_program(program_text(p))`
gives back `p` exactly (`tests/test_tsir_text.py` holds every leaf program to it).

    tsir ppm_d3.ppm.ZX+cooling                    the program's name
    arch ppm_d3                                   its architecture
    id_seq 225                                    the identity allocator's high-water mark
    meta {"derived_from": "ppm_d3.ppm.ZX", ...}   its own metadata (JSON), if any
    metrics {...}                                 the metrics it claims (JSON), if any

    0 init p0@A45 d0@X_in_a d1@X_in_a ... | p0=0.1 d0=0.0      placement | n̄ (quanta)
    1 simd rotate_cw ~L0 +30                ; door=in_a op=enter kind=rotate hops=30
    2 simd port_in d0:X_in_a->P_in_a[E_in_a] ; door=in_a op=enter
    65 gate CX p0:e6 @A45                   ; op=ppm round=0 factor=X
    63 gate H p0 @A45                       ; op=ppm round=0
    88 measure p0                           ; op=ppm round=0 checks=["P0"]
    10 cool d0 d1 d2 p0                     (`cool` alone is the broadcast cool)
    reset / barrier / decode <ions>

A line starts with the instruction's id: it is an identity, not a position (`TSIR.id_seq`),
and the page cites it (`#65`).  After `;`, `key=value` is the instruction's metadata; a key
with a leading dot is one of its own fields where it differs from what the line implies
(`.mode=inter`, `.arity=-` for none, `.t0=12.5`, `.holds=[...]`).  A value is a bare word
or JSON.  Simd moves: `~LOOP ±k` is the rigid rotation template, otherwise one
`ion:from->to[via,...]` per participant.  Gates: `a:b` is a pair, a bare ion one of a
batch of single-qubit gates (`arity=1`).
"""
from __future__ import annotations

import json
import re

from .tsir import TSIR, Instruction, Participant

__all__ = ["instruction_text", "parse_instruction", "program_text", "parse_program", "TextError"]


class TextError(ValueError):
    pass


_BARE = re.compile(r"[A-Za-z_][\w.:/\-+†|]*")
_DEFAULT_MODE = {"simd": "inter", "gate": "intra"}


def _val(v) -> str:
    if isinstance(v, str) and _BARE.fullmatch(v) and v not in ("true", "false", "null"):
        return v
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


def _unval(s: str):
    try:
        return json.loads(s)
    except ValueError:
        return s


def _tokens(s: str) -> list[str]:
    """Split on spaces outside quotes and brackets (a JSON value is one token)."""
    out, cur, depth, quote = [], [], 0, False
    i = 0
    while i < len(s):
        c = s[i]
        if quote:
            cur.append(c)
            if c == "\\" and i + 1 < len(s):
                cur.append(s[i + 1])
                i += 1
            elif c == '"':
                quote = False
        elif c == '"':
            quote = True
            cur.append(c)
        elif c in "[{":
            depth += 1
            cur.append(c)
        elif c in "]}":
            depth -= 1
            cur.append(c)
        elif c.isspace() and depth == 0:
            if cur:
                out.append("".join(cur))
                cur = []
        else:
            cur.append(c)
        i += 1
    if cur:
        out.append("".join(cur))
    return out


def instruction_text(ins: Instruction) -> str:
    t = ins.type
    head: list[str] = [str(ins.id), t]
    notes: list[str] = []
    if t == "init":
        head += [f"{ion}@{site}" for ion, site in ins.placement.items()]
        if ins.quanta:
            head += ["|"] + [f"{ion}={_val(q)}" for ion, q in ins.quanta.items()]
    elif t == "simd":
        head.append(ins.cls or "-")
        tpl = ins.template
        if tpl is not None and tpl.get("kind") == "loop_shift" and set(tpl) == {"kind", "loop", "delta"} \
                and not ins.participants:
            head += [f"~{tpl['loop']}", f"{int(tpl['delta']):+d}"]
        else:
            if tpl is not None:
                notes.append(f".template={_val(dict(tpl))}")
            for p in ins.participants:
                head.append(f"{p.ion}:{p.src}->{p.dst}" + (f"[{','.join(p.via)}]" if p.via else ""))
    elif t == "gate":
        head.append(ins.gate or "-")
        if ins.pairs:
            head += [f"{a}:{b}" for a, b in ins.pairs]
            if ins.ions:
                notes.append(f".ions={_val(list(ins.ions))}")
            if ins.arity is not None:
                notes.append(f".arity={ins.arity}")
        else:
            head += list(ins.ions)
            if ins.arity != 1:
                notes.append(f".arity={'-' if ins.arity is None else ins.arity}")
        head += [f"@{s}" for s in ins.sites]
        if ins.params:
            notes.append(f".params={_val([list(p) for p in ins.params])}")
    elif t == "cool":
        if not ins.broadcast:
            head += list(ins.ions)
            if not ins.ions:
                notes.append(".broadcast=false")
        elif ins.ions:
            head += list(ins.ions)
            notes.append(".broadcast=true")
    else:                                                # measure, reset, barrier, decode
        head += list(ins.ions)
    # the fields a line does not imply
    if t in _DEFAULT_MODE:
        if ins.mode != _DEFAULT_MODE[t]:
            notes.append(f".mode={'-' if ins.mode is None else _val(ins.mode)}")
    elif ins.mode is not None:
        notes.append(f".mode={_val(ins.mode)}")
    if ins.holds:
        notes.append(f".holds={_val(list(ins.holds))}")
    for key in ("t0", "t1", "cost", "steps"):
        v = getattr(ins, key)
        if v is not None:
            notes.append(f".{key}={_val(v)}")
    if ins.quanta_delta:
        notes.append(f".quanta_delta={_val(dict(ins.quanta_delta))}")
    if ins.operating_point:
        notes.append(f".operating_point={_val(dict(ins.operating_point))}")
    if t != "init" and ins.quanta:
        notes.append(f".quanta={_val(dict(ins.quanta))}")
    if t != "init" and ins.placement:
        notes.append(f".placement={_val(dict(ins.placement))}")
    if t not in ("gate",) and ins.sites:
        notes.append(f".sites={_val(list(ins.sites))}")
    for k, v in (ins.meta or {}).items():
        if not _BARE.fullmatch(k) or k.startswith("."):
            raise TextError(f"instruction {ins.id}: a metadata key {k!r} cannot be written")
        notes.append(f"{k}={_val(v)}")
    return " ".join(head) + (" ; " + " ".join(notes) if notes else "")


def parse_instruction(line: str) -> Instruction:
    body, _, ann = line.partition(" ; ")
    toks = body.split()
    if len(toks) < 2:
        raise TextError(f"not an instruction: {line!r}")
    iid, t = int(toks[0]), toks[1]
    rest = toks[2:]
    kw: dict = {"type": t, "id": iid}
    meta: dict = {}
    fields: dict = {}
    for tok in _tokens(ann):
        k, eq, v = tok.partition("=")
        if not eq:
            raise TextError(f"instruction {iid}: annotation {tok!r} is not key=value")
        if k.startswith("."):
            fields[k[1:]] = None if v == "-" else _unval(v)
        else:
            meta[k] = _unval(v)
    if t == "init":
        if "|" in rest:
            k = rest.index("|")
            place, quanta = rest[:k], rest[k + 1:]
        else:
            place, quanta = rest, []
        kw["placement"] = dict(p.split("@", 1) for p in place)
        kw["quanta"] = {q.split("=", 1)[0]: _unval(q.split("=", 1)[1]) for q in quanta}
    elif t == "simd":
        kw["cls"] = None if rest[0] == "-" else rest[0]
        kw["mode"] = _DEFAULT_MODE["simd"]
        if len(rest) == 3 and rest[1].startswith("~"):
            kw["template"] = {"kind": "loop_shift", "loop": rest[1][1:], "delta": int(rest[2])}
        else:
            parts = []
            for p in rest[1:]:
                m = re.fullmatch(r"([^:]+):(.+?)->([^\[]+)(?:\[(.*)\])?", p)
                if not m:
                    raise TextError(f"instruction {iid}: participant {p!r}")
                parts.append(Participant(m[1], m[2], m[3], tuple(x for x in (m[4] or "").split(",") if x)))
            kw["participants"] = tuple(parts)
    elif t == "gate":
        kw["gate"] = None if rest[0] == "-" else rest[0]
        kw["mode"] = _DEFAULT_MODE["gate"]
        ops = [x for x in rest[1:] if not x.startswith("@")]
        kw["sites"] = tuple(x[1:] for x in rest[1:] if x.startswith("@"))
        if any(":" in x for x in ops):
            kw["pairs"] = tuple(tuple(x.split(":", 1)) for x in ops)
        else:
            kw["ions"] = tuple(ops)
            kw["arity"] = 1
    elif t == "cool":
        kw["ions"] = tuple(rest)
        kw["broadcast"] = not rest
    else:
        kw["ions"] = tuple(rest)
    for k, v in fields.items():
        if k == "holds" or k == "sites" or k == "ions":
            kw[k] = tuple(v or ())
        elif k == "params":
            kw[k] = tuple(tuple(float(x) for x in p) for p in (v or ()))
        else:
            kw[k] = v
    kw["meta"] = meta
    return Instruction(**kw)


def program_text(prog: TSIR) -> str:
    lines = [f"tsir {prog.name}", f"arch {prog.arch_spec}", f"id_seq {prog.id_seq}"]
    if prog.meta:
        lines.append("meta " + json.dumps(prog.meta, separators=(",", ":"), ensure_ascii=False))
    if prog.metrics:
        lines.append("metrics " + json.dumps(prog.metrics, separators=(",", ":"), ensure_ascii=False))
    lines += [instruction_text(i) for i in prog.instructions]
    return "\n".join(lines) + "\n"


def parse_program(text: str) -> TSIR:
    head: dict = {}
    instrs: list = []
    for n, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        word = line.split(None, 1)[0]
        if word in ("tsir", "arch", "id_seq", "meta", "metrics"):
            head[word] = line.split(None, 1)[1] if len(line.split(None, 1)) > 1 else ""
            continue
        try:
            instrs.append(parse_instruction(line.strip()))
        except (TextError, ValueError, IndexError) as e:
            raise TextError(f"line {n}: {e}") from None
    return TSIR(name=head.get("tsir", ""), arch_spec=head.get("arch", ""), instructions=instrs,
                metrics=json.loads(head["metrics"]) if "metrics" in head else {},
                meta=json.loads(head["meta"]) if "meta" in head else {},
                id_seq=int(head["id_seq"]) if "id_seq" in head else
                max((i.id for i in instrs), default=-1) + 1)
