"""Codesign rewrites of compiled-once templates, each proved by the exact simulator.

The user, 2026-10-29: "this is a codesign system, you don't necessarily need to follow how we
design gadget ... find the bottleneck first (Amdahl)", and "we should keep 25 [rounds] to stay
paper faithful".  So nothing here drops a round a merge needs: it replaces merges by operations
that need none, and every replacement is a theorem about the template, not a guess.

* **A FormalRV lattice-surgery CNOT, whole.**  `prep L X; ppm Z c Z L; ppm X L X t..; read L Z`
  and its frames is `cx c t..` (to one target or several) -- a transversal CNOT, no merge.
* **FormalRV's H̄** -- three |Ȳ⟩ merges S̄, √X̄, S̄ on one block -- is `ht`: one visit to a
  Hadamard bay (qccd/gadget/leaves/hadamard.py).
* **A Pauli product** is measured by one fresh ancilla and transversal CNOTs: Z̄ factors CNOT
  into it (|0̄⟩), X̄ factors take a CNOT from it (|+̄⟩), and a mixed product turns the ancilla
  with `ht` in between: |0̄⟩, CX z -> anc, H̄, CX anc -> x, read X̄.  (Backwards from X̄_anc:
  X̄_anc ∏X̄_x, then Z̄_anc ∏X̄_x, then Z̄_anc ∏Z̄_z ∏X̄_x, and Z̄_anc = +1 on |0̄⟩.)
* **A table lookup's row is one call** (`fuse_rows`): the row's CNOTs share their control, so
  as one fan-out call the control stays out of memory while the targets come and go.  The same
  instructions in the same order; the table's bit is a slot reserved either way.

A rewrite may drop outcomes only when no other line reads them and no call site hands them on
(`exports`).  `equal_drop` is the proof: every branch of the original lands, outcomes forgotten,
on the rewrite's branch with the same state and the same total probability.  Templates with an
|AutoCCZ⟩ state rest on the per-pattern proofs; the others are proved whole.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from itertools import product

from .expr import Expr
from .program import Body, Let, Loop, Pred, Program, Seg, Template, When, _Rec

__all__ = ["rewrite", "equal_drop", "exports", "products_proved", "fan_template", "fuse_rows",
           "records_read"]

PPM = re.compile(r"ppm ((?:[XZ] [pl]\d+ ?)+)-> (o\d+)")


def _body(ln: str) -> str:
    return ln.split(": ", 1)[1] if ln.startswith("if ") else ln


def _guard(ln: str) -> str:
    return ln.split(": ", 1)[0] + ": " if ln.startswith("if ") else ""


# ---- the proof -----------------------------------------------------------------------------

def run(t: Template, e: tuple):
    """Every branch of a template's body, its outside outcomes `e` given (rounds and decodes
    leave the state alone), its parameters each half of a Bell pair."""
    from qccd.gadget.logic.contract import _bell_start, run_lines
    params = [f"p{i}" for i in range(t.n_params)]
    pre = [(0, f"bit {'1' if v else '1 ^ 1'} -> e{j}") for j, v in enumerate(e)]
    body = [(i + 1, ln) for i, ln in enumerate(t.lines) if not _body(ln).startswith(("se ", "decode"))]
    return run_lines(pre + body, _bell_start(params), {})


def equal_drop(a: Template, b: Template) -> bool:
    """`b` is `a` with some outcomes gone: the same channel once those are forgotten (every
    branch of `a` lands on the branch of `b` with the outcomes `b` keeps, same state, and the
    probabilities add up), for every value of the outside outcomes."""
    for e in product((0, 1), repeat=a.n_ext):
        rb = list(run(b, e))
        keep = set().union(*(br.outcomes for br in rb)) if rb else set()
        by = {tuple(sorted(br.outcomes.items())): br for br in rb}
        acc: dict = defaultdict(float)
        for x in run(a, e):
            key = tuple(sorted((o, v) for o, v in x.outcomes.items() if o in keep))
            y = by.get(key)
            if y is None or set(x.state.names) != set(y.state.names):
                return False
            if abs(x.state.overlap(y.state) - 1) > 1e-7:
                return False
            acc[key] += x.prob
        if any(abs(acc[k] - by[k].prob) > 1e-9 for k in by):
            return False
    return True


def _canon(seg, first: dict):
    """A segment with its blocks and outcomes renamed in order of appearance (`first` fixed)."""
    names = dict(first)

    def ren(m):
        w = m[0]
        if w not in names:
            names[w] = f"{w[0]}{sum(1 for v in names.values() if v[0] == w[0])}"
        return names[w]
    return tuple(re.sub(r"\b[plo]\d+\b", ren, ln) for ln in seg)


# ---- the patterns --------------------------------------------------------------------------

def _se_run(lines, i, g, blks):
    """Past the `se` lines on `blks` under guard `g`, from line i."""
    while i < len(lines) and _guard(lines[i]) == g and _body(lines[i]).startswith("se ") \
            and _body(lines[i]).split()[1] in blks:
        i += 1
    return i


def _ymerge(lines, i):
    """A |Ȳ⟩ merge at line i -- (block, letter, end, outcomes) or None:
       prep L Y; ppm A P Z L -> oa; se..; decode P L; read L X -> ob; decode L; if oa: a P; if ob: a P"""
    if i + 1 >= len(lines) or _guard(lines[i]):
        return None
    m = re.fullmatch(r"prep (l\d+) Y", lines[i])
    m2 = m and re.fullmatch(rf"ppm ([XZ]) ([pl]\d+) Z {m[1]} -> (o\d+)", lines[i + 1])
    if not m2:
        return None
    L, (A, blk, oa) = m[1], (m2[1], m2[2], m2[3])
    j = _se_run(lines, i + 2, "", (blk, L))
    got = []
    for w in (rf"decode {blk} {L}", rf"read {L} X -> (o\d+)", rf"decode {L}"):
        mm = re.fullmatch(w, lines[j] if j < len(lines) else "")
        if not mm:
            return None
        got.append(mm)
        j += 1
    ob, a = got[1][1], A.lower()
    if lines[j:j + 2] != [f"if {oa}: {a} {blk}", f"if {ob}: {a} {blk}"]:
        return None
    return blk, A, j + 2, [oa, ob]


def _cx_idiom(lines, i):
    """FormalRV's lattice-surgery CNOT at line i -- (end, control, targets, outcomes, guard):
       prep L X; ppm Z c Z L -> oa; se..; decode c L; ppm X L X t.. -> ob; se..; decode L t..;
       read L Z -> oc; decode L; then its frames, steered by oa/ob/oc alone
    every line under one guard `g` (a frame's guard then reads `E & o`)."""
    if i >= len(lines):
        return None
    g = _guard(lines[i])
    m = re.fullmatch(r"prep (l\d+) X", _body(lines[i]))
    if not m:
        return None
    L = m[1]

    def at(j):
        return _body(lines[j]) if j < len(lines) and _guard(lines[j]) == g else ""
    m1 = re.fullmatch(rf"ppm Z ([pl]\d+) Z {L} -> (o\d+)", at(i + 1))
    if not m1:
        return None
    c, oa = m1[1], m1[2]
    j = _se_run(lines, i + 2, g, (c, L))
    if at(j) == f"decode {c} {L}":
        j += 1
    m2 = re.fullmatch(rf"ppm X {L}((?: X [pl]\d+)+) -> (o\d+)", at(j))
    if not m2:
        return None
    ts, ob = m2[1].split()[1::2], m2[2]
    if c in ts or L in ts:
        return None
    j = _se_run(lines, j + 1, g, tuple(ts) + (L,))
    if at(j) == "decode " + " ".join([L] + ts):
        j += 1
    m3 = re.fullmatch(rf"read {L} Z -> (o\d+)", at(j))
    if not m3:
        return None
    oc = m3[1]
    j += 1
    if at(j) == f"decode {L}":
        j += 1
    outs, E = {oa, ob, oc}, (g[3:-2] if g else None)
    while j < len(lines):
        mm = re.fullmatch(r"if (.+): ([xz]) ([pl]\d+)", lines[j])
        if not mm or mm[3] not in [c] + ts:
            break
        parts = [x.strip() for x in mm[1].split("&")]
        if not ((E is None and len(parts) == 1 and parts[0] in outs)
                or (E is not None and len(parts) == 2 and parts[0] == E and parts[1] in outs)):
            break
        j += 1
    return j, c, ts, [oa, ob, oc], g


_PROVED: dict = {}


def _cx_segment(seg, g, ctl, tgts):
    """`cx` lines for a lattice-surgery CNOT window, proved the same channel (cached by shape)."""
    E = g[3:-2] if g else None
    plain = [ln if E is None else
             (ln[len(g):] if ln.startswith(g) else re.sub(rf"^if {re.escape(E)} & ", "if ", ln)) for ln in seg]
    canon = _canon(plain, {ctl: "p0", **{t: f"p{k + 1}" for k, t in enumerate(tgts)}})
    if ("cx", canon) not in _PROVED:
        n = 1 + len(tgts)
        want = tuple(f"cx p0 p{k + 1}" for k in range(len(tgts)))
        _PROVED[("cx", canon)] = equal_drop(Template("a", canon, (0,) * len(canon), n, 1, 3, 0),
                                            Template("b", want, (0,) * len(want), n, 0, 0, 0))
    return [f"{g}cx {ctl} {t}" for t in tgts] if _PROVED[("cx", canon)] else None


def _h_segment(seg, block):
    """`ht` (and the Pauli frame that makes it the same channel) for a |Ȳ⟩-merge triple on
    `block`, proved here and cached by the triple's shape; None if no such line is."""
    canon = _canon(seg, {block: "p0"})
    if ("h", canon) not in _PROVED:
        a = Template("a", canon, (0,) * len(canon), 1, 3, 6, 0)
        _PROVED[("h", canon)] = None
        for tail in ((), ("x p0",), ("z p0",), ("y p0",)):
            if equal_drop(a, Template("b", ("ht p0",) + tail, (0,) * (1 + len(tail)), 1, 0, 0, 0)):
                _PROVED[("h", canon)] = ("ht p0",) + tail
                break
    fix = _PROVED[("h", canon)]
    return None if fix is None else [ln.replace("p0", block) for ln in fix]


def _product(ln: str, m, c: str) -> list:
    """A Pauli product's line measured by the fresh ancilla `c` and transversal CNOTs."""
    g = _guard(ln)
    fs = m[1].split()
    zs = [x for L, x in zip(fs[0::2], fs[1::2]) if L == "Z"]
    xs = [x for L, x in zip(fs[0::2], fs[1::2]) if L == "X"]
    if not xs:
        body = [f"prep {c} Z"] + [f"cx {x} {c}" for x in zs] + [f"read {c} Z -> {m[2]}"]
    elif not zs:
        body = [f"prep {c} X"] + [f"cx {c} {x}" for x in xs] + [f"read {c} X -> {m[2]}"]
    else:
        body = [f"prep {c} Z"] + [f"cx {x} {c}" for x in zs] + [f"ht {c}"] + \
               [f"cx {c} {x}" for x in xs] + [f"read {c} X -> {m[2]}"]
    return [g + b for b in body + [f"decode {c}"]], zs + xs


def rewrite(t: Template, exported=frozenset(), *, cx_idiom: bool = True):
    """(the template rewritten, Counter of what was rewritten), or (None, empty) if nothing.
    `exported`: the outcomes some call site of `t` hands on (`exports`)."""
    lines, out, k, n = list(t.lines), [], t.n_locals, Counter()
    exported = set(exported)

    def free(i, end, drop):
        rest = lines[:i] + lines[end:]
        return not exported & set(drop) and not any(re.search(rf"\b{o}\b", ln) for ln in rest for o in drop)
    i = 0
    while i < len(lines):
        cxi = _cx_idiom(lines, i) if cx_idiom else None
        if cxi and free(i, cxi[0], cxi[3]):
            fix = _cx_segment(lines[i:cxi[0]], cxi[4], cxi[1], cxi[2])
            if fix is not None:
                out += fix
                n["cx"] += 1
                i = cxi[0]
                continue
        u = [_ymerge(lines, i)]
        while u[-1] and len(u) < 3:
            u.append(_ymerge(lines, u[-1][2]))
        if all(u) and len(u) == 3 and len({x[0] for x in u}) == 1 and "".join(x[1] for x in u) in ("ZXZ", "XZX") \
                and free(i, u[2][2], [o for x in u for o in x[3]]):
            fix = _h_segment(lines[i:u[2][2]], u[0][0])
            if fix is not None:
                out += fix
                n["ht"] += 1
                i = u[2][2]
                continue
        m = PPM.fullmatch(_body(lines[i]))
        if m:
            fix, blks = _product(lines[i], m, f"l{k}")
            k += 1
            out += fix
            n["product"] += 1
            i = _se_run(lines, i + 1, _guard(lines[i]), blks)
            continue
        out.append(lines[i])
        i += 1
    if not n:
        return None, n
    return Template(t.tid + "_cd", tuple(out), (0,) * len(out), t.n_params, k, t.n_out, t.n_ext), n


def products_proved(sizes=(1, 2, 3, 4)) -> bool:
    """Every letter pattern of a product over `sizes` blocks: the ancilla measurement is the merge."""
    for size in sizes:
        for letters in product("XZ", repeat=size):
            ps = [f"p{i}" for i in range(size)]
            lines = ("ppm " + " ".join(f"{L} {q}" for L, q in zip(letters, ps)) + " -> o0",) + \
                tuple(f"se {q} 3" for q in ps)
            t = Template("seg", lines, (0,) * len(lines), size, 0, 1, 0)
            if not equal_drop(t, rewrite(t)[0]):
                return False
    return True


# ---- what a call site hands on ------------------------------------------------------------

def _segs(body):
    for n in body:
        if isinstance(n, Seg):
            yield n
        elif isinstance(n, (Pred, When, Let, Loop)):
            yield from _segs(n.body)


def records_read(prog: Program) -> bool:
    """Does any template read a record by its address?  (Gidney 2025 never does: it reads
    source slots, which the archive writes from outcomes -- `Seg.writes`.)"""
    return any(isinstance(r, _Rec) and r.table == "rec"
               for b in [prog.root, *prog.bodies.values()] for x in _segs(b) for r in x.ext)


def exports(prog: Program) -> dict:
    """Per template, the outcomes some call site hands on: its record bands, when any template
    reads a record by address, and every outcome a record or source slot is written from."""
    out: dict = defaultdict(set)
    bands = records_read(prog)
    for b in [prog.root, *prog.bodies.values()]:
        for s in _segs(b):
            if bands:
                out[s.tid].update(f"o{k}" for k, band in enumerate(s.bands) if band is not None)
            for w in s.writes:
                out[s.tid].update(re.findall(r"\bo\d+\b", w[3]))
    return out


# ---- a lookup's row as one call -------------------------------------------------------------

def fan_template(k: int) -> Template:
    """One control into k targets: `cx p0 p1; ...; cx p0 pk`."""
    lines = tuple(f"cx p0 p{j + 1}" for j in range(k))
    return Template(f"fan{k}", lines, (0,) * k, k + 1, 0, 0, 0)


def _sub(e: Expr, var: str, text: str) -> Expr:
    return Expr(re.sub(rf"\b{re.escape(var)}\b", f"({text})", e.text))


def fuse_rows(prog: Program, cx_tid: str, K: int) -> dict:
    """Every lookup row of `cx_tid` calls -- a loop whose body is the table bit's slot (and its
    empty `else`) around one call with a fixed control and a target the loop indexes -- becomes
    fan-out calls of at most K targets, in place.  A call needs a home for each block, so a row
    is cut into chunks.  `cx_tid` is a CNOT proved in every branch (`rewrite` makes it `cx`), so
    its merge ancilla is not made and the records its merges write are dropped -- only when no
    template reads a record by address.  Returns {"rows": loops rewritten, "fans": {k: tid}}."""
    drop_bands = not records_read(prog)
    fans: dict = {}
    rows = 0

    def fan(k):
        if k not in fans:
            t = fan_template(k)
            prog.templates[t.tid] = t
            fans[k] = t.tid
        return fans[k]

    def row(L: Loop):
        full = [n for n in L.body if not (isinstance(n, Pred) and not n.body)]
        if len(full) != 1 or not isinstance(full[0], Pred) or full[0].structural or len(full[0].body) != 1 \
                or any(not isinstance(n, Pred) for n in L.body):
            return None
        s = full[0].body[0]
        if not isinstance(s, Seg) or s.tid != cx_tid or s.ext or s.writes or len(s.params) != 2:
            return None
        if not drop_bands and any(b is not None for b in s.bands):
            return None
        (fc, ec), (ft, et) = s.params
        if L.var in ec.vars or L.var not in et.vars:
            return None
        try:
            lo, hi = L.lo(prog.params), L.hi(prog.params)
        except Exception:  # noqa: BLE001  (a row as long as a runtime value: left alone)
            return None
        n = hi - lo
        if n < 2:
            return None
        out = []
        c = f"{L.var}__chunk"
        if n // K:
            first = f"{lo} + {c}*{K}"
            out.append(Loop(c, Expr("0"), Expr(str(n // K)),
                            Body([Seg(fan(K), ((fc, ec),) + tuple((ft, _sub(et, L.var, f"{first} + {j}"))
                                                                  for j in range(K)), (), ())]),
                            qline=L.qline))
        if n % K:
            base = lo + (n // K) * K
            out.append(Seg(fan(n % K), ((fc, ec),) + tuple((ft, _sub(et, L.var, f"{base + j}"))
                                                            for j in range(n % K)), (), ()))
        return out

    def visit(body):
        nonlocal rows
        i = 0
        while i < len(body):
            node = body[i]
            if isinstance(node, Loop):
                rep = row(node)
                if rep is not None:
                    body[i:i + 1] = rep
                    rows += 1
                    i += len(rep)
                    continue
                visit(node.body)
            elif isinstance(node, (Pred, When, Let)):
                visit(node.body)
            i += 1
    visit(prog.root)
    for b in list(prog.bodies.values()):
        visit(b)
    return {"rows": rows, "fans": fans}
