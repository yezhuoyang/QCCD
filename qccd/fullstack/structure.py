"""The `.prog -> .qppm -> .qec` links for whole programs: units, loops, branches, calls.

`provenance.py` anchors one flat program body by concrete Pauli products.  Real programs have
gadgets, loops with symbolic indices, compile-time branches and calls, so here the links are
made structurally -- unit by unit, statement by statement, in the order FormalRV's lowering
keeps -- and every link is checked by what both sides must share:

  .qppm  ->  .qec
    for ↔ for                     'loop'     (children matched recursively)
    if ↔ for rv_gK (then) / for rv_eK (else)   'branch'  (FormalRV writes a compile-time
                                               branch as a loop of 0 or 1 trips)
    g(...) ↔ g(...)               'name'
    prepare |S> -> anc ↔ alloc + prepare |S> / N magic |S>   'order' (the state must agree)
    measure P ↔ merge P'          'shape'    P and P' have the same Pauli letters on the same
                                             block families (q ↔ LogicData, anc ↔ LogicalAncilla)
      alloc / prepare |+> / extract / decode / split around it   'group'
    when m: frame P ↔ frame on c[..) P'      'shape'
    frame P ↔ frame P'            'shape'
  .prog  ->  .qppm
    for ↔ for, if ↔ if, g(...) @ map ↔ g(...) @ map, measure ↔ measure, when ↔ when
    a gate (H, X, CX, ...) ↔ the next run of .qppm statements as long as FormalRV's own
    lowering of that gate alone ('template': learned once by compiling a one-gate program
    with FormalRV's prepare_qppm, cached per FormalRV commit)

A statement that matches nothing is refused (`Unlinked`) with both lines named; nothing is guessed.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import layers
from .provenance import Chain, Link, Unlinked, _qec_kind

TEMPLATES = Path(__file__).resolve().parents[2] / "out" / "fullstack" / "templates.json"


# ----------------------------------------------------------------------------- shapes

def _qppm_letters(text: str) -> tuple:
    return tuple((p, "q" if r == "q" else "a") for p, r, _ in re.findall(r"([XYZ])\s+(q|anc)\[([^\]]+)\]", text))


def _qec_letters(text: str) -> tuple:
    return tuple((p, "q" if fam == "LogicData" else "a")
                 for p, fam, _ in re.findall(r"([XYZ])\[(\w+)\[([^\]]+)\]\[0\]\]", text))


def reachable(stage: layers.Stage) -> set:
    """The gadgets the program calls, directly or through other gadgets."""
    gad = stage.gadgets()
    seen: set = set()
    todo = [stage.program] if stage.program else []
    while todo:
        u = todo.pop()
        for s in u.statements():
            m = re.match(r"\s*([\w!]+)\(", s.text)        # (FormalRV's `!ctl!g` included)
            if m and m[1] in gad and m[1] not in seen:
                seen.add(m[1])
                todo.append(gad[m[1]])
    return seen


def _unit_pairs(a: layers.Stage, b: layers.Stage) -> list:
    """(unit of a, unit of b) by name, for the units the program reaches (a library may hold
    gadgets this program never calls); the programs pair whatever they are called."""
    out = []
    ga, gb = a.gadgets(), b.gadgets()
    used = reachable(b) | reachable(a)
    for name, u in ga.items():
        if name in gb and name in used:
            out.append((u, gb[name]))
    if a.program and b.program:
        out.append((a.program, b.program))
    return out


# ----------------------------------------------------------------------------- .qppm -> .qec

def link_qppm_qec(qppm: layers.Stage, qec: layers.Stage) -> dict:
    out: dict = {}
    for up, uq in _unit_pairs(qppm, qec):
        _match_qec(uq.body, up.body, out, f"{uq.kind} {uq.name}")
    return out


#: a loop of exactly one trip, `for A <= v < A + 1:` -- how FormalRV's `.qec` binds the
#: variable of a descending `.qppm` loop (`for n > k >= 0` becomes `for 0 <= d < n` around one
#: of these); it has no `.qppm` statement of its own
_ONE_TRIP = re.compile(r"for\s+(.+?)\s*(?:<=|≤)\s*\w+\s*<\s*\1\s*\+\s*1\s*:")


def _match_qec(qbody: list, pbody: list, out: dict, where: str) -> None:
    j = 0
    pending: list = []                     # group statements waiting for their merge
    last_measure = None
    swapped = None                         # an `if` whose body is the next `rv_e` branch
    # a one-trip binding loop is read through: its lines are matched where it stands, and it
    # is linked with the first of them
    binders: list = []
    flat: list = []
    for s in qbody:
        if _ONE_TRIP.fullmatch(s.text.strip()):
            binders.append(s)
            flat += s.children
        else:
            flat.append(s)
    qbody = flat

    def nxt(pred, what, s):
        nonlocal j
        while j < len(pbody):
            p = pbody[j]
            j += 1
            if pred(p):
                return p
            if p.text.strip() != "skip":
                raise Unlinked(f"{where}: .qec line {s.line} ({s.text.strip()[:70]!r}) expected {what}, "
                               f"but the .qppm has line {p.line} ({p.text.strip()[:70]!r})")
        raise Unlinked(f"{where}: .qec line {s.line} ({s.text.strip()[:70]!r}): no {what} left in the .qppm")

    for s in qbody:
        t = s.text.strip()
        if t == "skip":
            continue
        loop = re.fullmatch(r"for\s+.+?\s*(?:<=|≤)\s*(\w+)\s*<\s*.+?:", t)
        if loop and loop[1].startswith(("rv_g", "rv_e")):
            empty = all(c.text.strip() == "skip" for c in s.children)
            if loop[1].startswith("rv_g"):
                p = nxt(lambda p: p.text.strip().startswith("if "), "a compile-time branch (if)", s)
                out[s.line] = Link(p, "branch")
                # under a negated condition (`if not c:`) FormalRV's `rv_g` branch is the EMPTY
                # one, and the `if`'s body is the `rv_e` branch that follows
                if not (empty and p.children):
                    _match_qec(s.children, p.children, out, where)
                swapped = p if (empty and p.children) else None
            else:
                # the else branch: FormalRV's `if` had none, or its `else:` follows
                if j < len(pbody) and pbody[j].text.strip() == "else:":
                    p = pbody[j]
                    j += 1
                    out[s.line] = Link(p, "branch")
                    _match_qec(s.children, p.children, out, where)
                elif swapped is not None and not empty:
                    out[s.line] = Link(swapped, "branch")
                    _match_qec(s.children, swapped.children, out, where)
                else:
                    prev = pbody[j - 1] if j else None
                    if prev is not None:
                        out[s.line] = Link(prev, "branch")
                swapped = None
            continue
        if loop:
            p = nxt(lambda p: p.text.strip().startswith("for "), "a loop", s)
            out[s.line] = Link(p, "loop")
            _match_qec(s.children, p.children, out, where)
            continue
        if t.startswith("when ") and t.endswith(":"):
            p = nxt(lambda p: p.text.strip().startswith("when "), "a guard", s)
            out[s.line] = Link(p, "loop")
            _match_qec(s.children, p.children, out, where)
            continue
        call = re.fullmatch(r"([\w!]+)\(.*\)", t)
        kd = _qec_kind(s)
        if call and kd == "other":
            p = nxt(lambda p: re.match(rf"{re.escape(call[1])}\(", p.text.strip()) is not None,
                    f"a call of {call[1]}", s)
            out[s.line] = Link(p, "name")
            continue
        magic = re.search(r"(?:prepare|magic)\s+(\|[^>]+>)\s*->\s*LogicalAncilla", t)
        if kd == "alloc_block":
            pending.append(s)
        elif magic:
            state = magic[1]
            p = nxt(lambda p: re.match(rf"prepare\s+{re.escape(state)}\s*->\s*anc", p.text.strip()) is not None,
                    f"a {state} preparation", s)
            out[s.line] = Link(p, "order")
            for g in pending:
                out[g.line] = Link(p, "order")
            pending = []
        elif kd in ("alloc", "prepare"):
            pending.append(s)
        elif kd == "merge":
            shape = _qec_letters(t.split("= merge", 1)[1].split(" with ")[0])
            p = nxt(lambda p: p.text.strip().startswith("measure ")
                    and _qppm_letters(p.text.split("->")[0]) == shape, f"a measurement of shape {shape}", s)
            out[s.line] = Link(p, "shape")
            for g in pending:
                out[g.line] = Link(p, "group")
            pending = []
            last_measure = p
        elif kd in ("extract", "decode", "split"):
            if last_measure is None:
                raise Unlinked(f"{where}: .qec line {s.line}: a {kd} before any merge")
            out[s.line] = Link(last_measure, "group")
        elif kd == "frame_on":
            shape = _qec_letters(t)
            p = nxt(lambda p: p.text.strip().startswith("when ") and len(p.children) == 1
                    and p.children[0].text.strip().startswith("frame ")
                    and _qppm_letters(p.children[0].text) == shape, f"a guarded frame of shape {shape}", s)
            out[s.line] = Link(p.children[0], "shape")
        elif kd == "frame":
            shape = _qec_letters(t)
            p = nxt(lambda p: p.text.strip().startswith("frame ") and _qppm_letters(p.text) == shape,
                    f"a frame of shape {shape}", s)
            out[s.line] = Link(p, "shape")
        elif t.startswith("ppmrot"):
            p = nxt(lambda p: p.text.strip().startswith("ppmrot"), "a rotation", s)
            out[s.line] = Link(p, "order")
        else:
            raise Unlinked(f"{where}: .qec line {s.line}: no rule links {t[:80]!r}")
    for b in binders:
        first = next((c.line for c in b.children if c.line in out), None)
        if first is not None:
            out[b.line] = Link(out[first].stmt, "loop")


# ----------------------------------------------------------------------------- .prog -> .qppm

_STRUCT = re.compile(r"^(for |if |else:|when |measure )")


def _is_call(text: str, gadgets: set) -> str | None:
    m = re.match(r"(\w+)\(", text.strip())
    return m[1] if m and m[1] in gadgets else None


def link_prog_qppm(prog: layers.Stage, qppm: layers.Stage, templates: dict) -> dict:
    out: dict = {}
    gadgets = set(prog.gadgets()) | set(qppm.gadgets())
    for ug, up in _unit_pairs(prog, qppm):
        _match_prog(ug.body, up.body, out, templates, gadgets, f"{ug.kind} {ug.name}")
    return out


def _gate_of(text: str) -> str | None:
    m = re.match(r"([A-Za-z]+)\(", text.strip())
    return m[1] if m else None


def _match_prog(gbody: list, pbody: list, out: dict, templates: dict, gadgets: set, where: str) -> None:
    j = 0
    for s in gbody:
        t = s.text.strip()
        call = _is_call(t, gadgets)
        if _STRUCT.match(t) or call:
            key = ("for " if t.startswith("for ") else "if " if t.startswith("if ") else
                   "else:" if t.startswith("else:") else "when " if t.startswith("when ") else
                   "measure " if t.startswith("measure ") else None)
            while j < len(pbody):
                p = pbody[j]
                j += 1
                pt = p.text.strip()
                if (key and pt.startswith(key)) or (call and re.match(rf"{call}\(", pt)):
                    out_how = "name" if call else "loop" if key in ("for ", "if ", "else:", "when ") else "order"
                    _link_all(p, s, out, out_how) if not s.children else None
                    out[p.line] = Link(s, out_how)
                    if s.children:
                        _match_prog(s.children, p.children, out, templates, gadgets, where)
                    break
                raise Unlinked(f"{where}: .prog line {s.line} ({t[:60]!r}) expected its .qppm form, found "
                               f".qppm line {p.line} ({pt[:60]!r})")
            else:
                raise Unlinked(f"{where}: .prog line {s.line} ({t[:60]!r}): nothing left in the .qppm")
            continue
        gate = _gate_of(t)
        if gate is None:
            raise Unlinked(f"{where}: .prog line {s.line}: no rule links {t[:80]!r}")
        if gate not in templates:
            raise Unlinked(f"{where}: .prog line {s.line}: no template for gate {gate} "
                           f"(learn it: structure.learn_templates)")
        n = templates[gate]
        run = pbody[j:j + n]
        if len(run) < n:
            raise Unlinked(f"{where}: .prog line {s.line} ({t!r}) needs {n} .qppm statements, {len(run)} left")
        for p in run:
            _link_all(p, s, out, "template")
        j += n


def _link_all(p, s, out, how):
    out[p.line] = Link(s, how)
    for c in p.children:
        _link_all(c, s, out, how)


# ----------------------------------------------------------------------------- templates

GATES = {"X": "X(0)", "Y": "Y(0)", "Z": "Z(0)", "H": "H(0)", "S": "S(0)", "Sdg": "Sdg(0)", "T": "T(0)",
         "Tdg": "Tdg(0)", "CX": "CX(0, 1)", "CZ": "CZ(0, 1)", "CCX": "CCX(0, 1, 2)", "SWAP": "SWAP(0, 1)"}


def learn_templates(gates=None, cache: Path = TEMPLATES, scratch: Path | None = None) -> dict:
    """How many top-level .qppm statements FormalRV's prepare_qppm writes for each gate alone."""
    from . import formalrv
    commit = formalrv.commit()
    have = {}
    if cache.exists():
        have = json.loads(cache.read_text(encoding="utf-8"))
    mine = have.get(commit, {})
    scratch = Path(scratch or cache.parent / "templates")
    for g in (gates or GATES):
        if g in mine:
            continue
        call = GATES.get(g)
        if call is None:
            continue
        d = scratch / f"{g}.prog"
        d.mkdir(parents=True, exist_ok=True)
        (d / "sources").write_text("head.prog\nmain.prog\n", encoding="utf-8")
        (d / "head.prog").write_text("\ncode surface d=3\n\nparams  n\n\n", encoding="utf-8")
        (d / "main.prog").write_text(f"program[q: n]:\n  {call}\n\n", encoding="utf-8")
        out = scratch / f"{g}.qppm"
        try:
            formalrv.prog_to_qppm(d, out)
        except formalrv.ToolchainError:
            continue
        st = layers.read(out, "qppm")
        mine[g] = len(st.program.body) if st.program else 0
    have[commit] = mine
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(have, indent=1), encoding="utf-8")
    return mine


# ----------------------------------------------------------------------------- prefix differencing

PREFIX_CACHE = Path(__file__).resolve().parents[2] / "out" / "fullstack" / "prefix"


def _preorder(body: list, depth: int = 0) -> list:
    out = []
    for s in body:
        out.append((s, depth))
        out += _preorder(s.children, depth + 1)
    return out


def _flat(stmts: list, depth: int = 0, *, exact: bool = False) -> list:
    """A .qppm body, pre-order, as text (indentation is the nesting).  Unless `exact`, the
    ancilla slots and measurement-record numbers are blanked: adding a gate renumbers every
    later one, and that is bookkeeping, not a change in what the statement does."""
    out = []
    for s in stmts:
        t = re.sub(r"\s+", " ", s.text.strip())
        if not exact:
            t = re.sub(r"\b(anc|m)\[[^\]]*\]", r"\1[_]", t)
        out.append(("  " * depth) + t)
        out += _flat(s.children, depth + 1, exact=exact)
    return out


def link_prog_qppm_by_prefix(prog_path: Path, prog: layers.Stage, qppm: layers.Stage,
                             specialize: dict | None = None, unit=None, targets=None) -> dict:
    """`.qppm` line -> Link(.prog statement) for the PROGRAM unit (or the gadget `unit`, whose
    `.qppm` units `targets` -- the gadget's own and FormalRV's controlled copy `!ctl!g`), by
    compiling every prefix of it with FormalRV's own prepare_qppm and aligning consecutive
    outputs.

    FormalRV's `.prog` compiler optimises across gates (adjacent rotations merge; two π/4 make a
    Pauli frame), so a gate's `.qppm` is not a fixed template.  A statement that appears when
    gate k is added is gate k's ('prefix'); one that CHANGES when gate k is added is shared by
    the gate it came from and gate k ('merged', attributed to k, the later).  The last prefix
    must reproduce the real `.qppm` program exactly, or nothing is linked (`Unlinked`)."""
    import difflib
    import hashlib
    import shutil
    from . import formalrv
    unit = unit or prog.program
    if unit is None:
        return {}
    # the .qppm units this one becomes: the program's, or a gadget's own and its controlled copy
    names = [None] if unit.kind == "program" else list(targets or [unit.name])

    def unit_of(st, name):
        return st.program if name is None else st.gadgets().get(name)
    pre = _preorder(unit.body)
    # where the block lives: its member file, from its header to the next top-level line (a
    # program stands last in its member)
    member = unit.header.member
    src_dir = Path(prog_path)
    if not src_dir.is_dir():
        raise Unlinked("prefix differencing needs a .prog stage folder")
    key = hashlib.sha256((formalrv.PIN_COMMIT + "\n".join(prog.lines) + repr(sorted((specialize or {}).items()))
                          + (f"\0{unit.kind} {unit.name}" if unit.kind != "program" else "")
                          ).encode()).hexdigest()[:16]
    work = PREFIX_CACHE / key
    work.mkdir(parents=True, exist_ok=True)
    header = unit.header.text.rstrip()
    member_text = (src_dir / member).read_text(encoding="utf-8-sig").split("\n")
    start = unit.header.mline - 1
    end = len(member_text)
    if unit.kind != "program":
        end = next((i for i in range(start + 1, len(member_text))
                    if member_text[i].strip() and not member_text[i][:1].isspace()), len(member_text))
    flats: dict = {n: [] for n in names}
    last_k = None
    for k in range(1, len(pre) + 1):
        keep = {id(s) for s, _ in pre[:k]}
        # a block header whose children are all cut has no body: that prefix is the previous one
        if pre[k - 1][0].children:
            for n in names:
                flats[n].append(None)
            continue

        def emit(stmts, depth):
            lines = []
            for s in stmts:
                if id(s) not in keep:
                    continue
                lines.append("  " * (depth + 1) + s.text.strip())
                lines += emit(s.children, depth + 1)
            return lines
        body = emit(unit.body, 0)
        out_q = work / f"p{k}.qppm"
        if not out_q.exists():
            d = work / f"p{k}.prog"
            if d.exists():
                shutil.rmtree(d)
            shutil.copytree(src_dir, d)
            (d / member).write_text("\n".join(member_text[:start] + [header] + body + [""] + member_text[end:])
                                    + "\n\n", encoding="utf-8")
            try:
                formalrv.prog_to_qppm(d, out_q)
            except formalrv.ToolchainError as exc:
                raise Unlinked(f"FormalRV refused the .prog prefix ending at line {pre[k - 1][0].line}: {exc}")
        st = layers.read(out_q, "qppm")
        for n in names:
            u = unit_of(st, n)
            flats[n].append(_flat(u.body) if u else [])
        last_k = k
    if last_k is None:
        return {}
    last_st = layers.read(work / f"p{last_k}.qppm", "qppm")
    # a block header (`when c[..]:`, `for ...:`) belongs to the .prog block it came from, not
    # to the first statement inside that block (the prefix that first showed it)
    parent = {}
    for s, _ in pre:
        for c in s.children:
            parent[id(c)] = s
    out: dict = {}
    for n in names:
        real = unit_of(qppm, n)
        mine = unit_of(last_st, n)
        if real is None or mine is None:
            continue
        if (next(f for f in reversed(flats[n]) if f is not None) != _flat(real.body)
                or _flat(mine.body, exact=True) != _flat(real.body, exact=True)):
            raise Unlinked(f"the last prefix of {unit.kind} {unit.name or ''} does not reproduce FormalRV's "
                           f".qppm{' ' + n if n else ''}: prefix differencing refused")
        # attribute, prefix by prefix
        owner: list = []                     # per position of the current flat list: (prog stmt, how)
        prev: list = []
        for k, f in enumerate(flats[n], start=1):
            if f is None:
                continue
            s = pre[k - 1][0]
            sm = difflib.SequenceMatcher(a=prev, b=f, autojunk=False)
            new_owner: list = [None] * len(f)
            for tag, i1, i2, j1, j2 in sm.get_opcodes():
                if tag == "equal":
                    for d in range(i2 - i1):
                        new_owner[j1 + d] = owner[i1 + d]
                else:
                    how = "merged" if tag == "replace" else "prefix"
                    for j in range(j1, j2):
                        new_owner[j] = (s, how)
            owner, prev = new_owner, f
        lines_pre = [p for p, _ in _preorder(real.body)]
        for p, o in zip(lines_pre, owner):
            if o is None:
                continue
            s, how = o
            pt = p.text.strip()
            h = parent.get(id(s))
            if p.children and h is not None and pt.split()[0] == h.text.strip().split()[0]:
                s, how = h, "prefix"
            out[p.line] = Link(s, how)
    return out


def link(prog: layers.Stage, qppm: layers.Stage, qec: layers.Stage, templates: dict | None = None,
         prog_path: Path | None = None, specialize: dict | None = None) -> Chain:
    """The whole chain.  `.prog -> .qppm` uses FormalRV's per-gate templates; where its compiler
    merged across gates (the templates do not fit), the program unit is linked by prefix
    differencing instead (`prog_path` needed)."""
    if templates is None:
        templates = learn_templates()
    chain = Chain(prog, qppm, qec, None)
    chain.qec_to_qppm = link_qppm_qec(qppm, qec)
    qg = qppm.gadgets()
    controlled = [g for g in prog.gadgets() if f"!ctl!{g}" in qg]
    try:
        if controlled:
            raise Unlinked("FormalRV made controlled copies of gadgets: they are linked by prefix")
        chain.qppm_to_prog = link_prog_qppm(prog, qppm, templates)
    except Unlinked:
        if prog_path is None:
            raise
        out = {}
        gadgets = set(prog.gadgets()) | set(qg)
        # a gadget by its templates where they fit (FormalRV compiles a gadget on its own),
        # otherwise -- its compiler merged across gates, or it made a controlled copy
        # (`!ctl!g`, under `ctrl`), which no template describes -- by prefix differencing
        for ug, up in _unit_pairs(prog, qppm):
            if ug.kind != "gadget":
                continue
            part: dict = {}
            try:
                _match_prog(ug.body, up.body, part, templates, gadgets, f"gadget {ug.name}")
                if ug.name in controlled:
                    part.update(link_prog_qppm_by_prefix(prog_path, prog, qppm, specialize, unit=ug,
                                                         targets=[f"!ctl!{ug.name}"]))
            except Unlinked:
                part = link_prog_qppm_by_prefix(prog_path, prog, qppm, specialize, unit=ug,
                                                targets=[ug.name] + ([f"!ctl!{ug.name}"] if ug.name in controlled else []))
            out.update(part)
        # a gadget only its controlled copy is reached through (no plain call of it anywhere)
        reached = {ug.name for ug, _ in _unit_pairs(prog, qppm) if ug.kind == "gadget"}
        for g in controlled:
            if g not in reached:
                out.update(link_prog_qppm_by_prefix(prog_path, prog, qppm, specialize,
                                                    unit=prog.gadgets()[g], targets=[f"!ctl!{g}"]))
        out.update(link_prog_qppm_by_prefix(prog_path, prog, qppm, specialize))
        chain.qppm_to_prog = out
    return chain
