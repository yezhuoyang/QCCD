"""Which line at every layer above a `.sysprog` event is it executing?

FormalRV does not yet emit provenance (the request is with its owners; see
docs/FULLSTACK_PLAN.md D1), so the links between layers are RECONSTRUCTED here from what the
lowering preserves exactly, and every link says how it was made:

  .qppm -> .qec      'pauli'    a `merge` names the same Pauli product as the `measure` it
                                implements (q[i] -> LogicData[i][0], anc[k] -> the k-th |Y+> block)
                     'band'     a `frame on c[lo,hi)` lies inside that measurement's record band
                     'order'    the k-th magic preparation / unconditional frame, in order
                     'group'    alloc / prepare / extract / decode / split around an anchored merge
  .qec -> .sysprog   'band'     the statement writes or reads exactly the same record band
                                (`-> c[lo,hi)`, `decode ... syndrome[lo,hi)`, `frame on c[..)`)
                     'operands' the split's Z measurement of the merge ancilla
                     'kind'     the magic request / patch request / |+> preparation of the group
                     'count'    `alloc ancilla[a,b)` <-> `request_ancilla count=b-a`
                     'inserted' a release the system compiler inserted after the last use
  .prog -> .qppm     'only'     the program has one statement
                     'name'     a gadget call keeps its name across the layers

A `.sysprog` event inside a call inherits the link of its call statement (the gadget body is
shared by many calls; the call site says which `.qec` statement it serves).

Nothing is guessed: if a program-body line of the listing cannot be anchored, `link` raises
with the lines left over.  FormalRV's sidecars, when they exist, replace this module's output
link for link, and the page shows which kind each link is.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import layers, sysprog as sp


class Unlinked(ValueError):
    pass


@dataclass
class Link:
    stmt: object                  # layers.Stmt (or sysprog.Node) at the upper layer
    how: str


@dataclass
class Chain:
    prog: layers.Stage
    qppm: layers.Stage
    qec: layers.Stage
    listing: sp.Listing
    sys_to_qec: dict = field(default_factory=dict)     # sysprog joined line -> Link(qec stmt)
    qec_to_qppm: dict = field(default_factory=dict)    # qec line -> Link(qppm stmt)
    qppm_to_prog: dict = field(default_factory=dict)   # qppm line -> Link(prog stmt)

    def resolve(self, event: sp.Event) -> dict:
        """The four captions' sources for one event: {layer: (stmt, how, context)}."""
        out = {"sysprog": (event.node, "event", " / ".join(f.label for f in event.frames))}
        node = None
        for cand in [event.node] + [f.node for f in reversed(event.frames)]:
            if cand.line in self.sys_to_qec:
                node = cand
                break
        if node is None:
            return out
        q = self.sys_to_qec[node.line]
        out["qec"] = (q.stmt, q.how, "")
        p = self.qec_to_qppm.get(q.stmt.line)
        if p:
            out["qppm"] = (p.stmt, p.how, "")
            g = self.qppm_to_prog.get(p.stmt.line)
            if g:
                out["prog"] = (g.stmt, g.how, "")
        return out


# ----------------------------------------------------------------------------- .qec statements

_BAND = r"(\w+)\[(\d+),(\d+)\)"


def _band(text: str, reg: str) -> tuple | None:
    m = re.search(rf"\b{reg}\[(\d+),(\d+)\)", text)
    return (int(m[1]), int(m[2])) if m else None


def _qec_kind(s: layers.Stmt) -> str:
    t = s.text
    if t.startswith("alloc LogicalAncilla"):
        return "alloc_block"
    if t.startswith("alloc ancilla"):
        return "alloc"
    if t.startswith("prepare |Y+>") or t.startswith("prepare |T>") or t.startswith("prepare |CCZ>"):
        return "magic"
    if t.startswith("prepare "):
        return "prepare"
    if " = merge " in t:
        return "merge"
    if t.startswith("extract "):
        return "extract"
    if t.startswith("decode "):
        return "decode"
    if " = split " in t:
        return "split"
    if t.startswith("frame on "):
        return "frame_on"
    if t.startswith("frame "):
        return "frame"
    return "other"


def _norm_pauli_qec(text: str) -> tuple:
    """`Z[LogicData[0][0]] X[LogicData[1][0]] Z[LogicalAncilla[0][0]]` -> sorted factors."""
    out = []
    for p, fam, blk in re.findall(r"([XYZ])\[(LogicData|LogicalAncilla)\[(\d+)\]\[0\]\]", text):
        out.append((p, "q" if fam == "LogicData" else "anc", int(blk)))
    return tuple(sorted(out))


def _norm_pauli_qppm(text: str, anc_block: dict) -> tuple:
    out = []
    for p, reg, idx in re.findall(r"([XYZ])\s+(q|anc)\[([^\]]+)\]", text):
        i = int(eval_simple(idx))
        out.append((p, "q", i) if reg == "q" else (p, "anc", anc_block.get(i, -1)))
    return tuple(sorted(out))


def eval_simple(text: str) -> int:
    return sp.evaluate(text.replace(" ", ""), {})


# ----------------------------------------------------------------------------- .sysprog program body

def _events_by_top(expansion: sp.Expansion, body: list) -> dict:
    """joined line of each program-body node -> the events under it (a loop, a call, or itself)."""
    tops = {n.line for n in body}
    out = {ln: [] for ln in tops}
    for e in expansion.events:
        top = e.frames[0].node.line if e.frames else e.node.line
        out.setdefault(top, []).append(e)
    return out


def _span(bits: list) -> tuple | None:
    if not bits:
        return None
    lo, hi = min(bits), max(bits) + 1
    return (lo, hi) if hi - lo == len(set(bits)) else None


# ----------------------------------------------------------------------------- linking

def link(prog: layers.Stage, qppm: layers.Stage, qec: layers.Stage, expansion: sp.Expansion) -> Chain:
    listing = expansion.listing
    chain = Chain(prog, qppm, qec, listing)
    _link_qec_to_sys(chain, expansion)
    _link_qppm_to_qec(chain)
    _link_prog_to_qppm(chain)
    return chain


def link_upper(prog: layers.Stage, qppm: layers.Stage, qec: layers.Stage) -> Chain:
    """The `.qec -> .qppm -> .prog` links alone: what QCCD's own lowering (`qec2alg`) needs,
    with no `.sysprog` involved."""
    chain = Chain(prog, qppm, qec, None)
    _link_qppm_to_qec(chain)
    _link_prog_to_qppm(chain)
    return chain


def _link_qec_to_sys(chain: Chain, expansion: sp.Expansion) -> None:
    qprog = chain.qec.program
    if qprog is None:
        raise Unlinked("the .qec has no program")
    body = chain.listing.body
    under = _events_by_top(expansion, body)
    free = [n for n in body]                    # program-body nodes not yet claimed, in order
    claimed: dict = {}

    def take(node: sp.Node, stmt: layers.Stmt, how: str) -> None:
        if node.line in claimed:
            raise Unlinked(f".sysprog line {node.line} claimed twice "
                           f"({claimed[node.line].stmt.text!r} and {stmt.text!r})")
        claimed[node.line] = Link(stmt, how)
        free.remove(node)

    def written(node, reg):
        return _span([i for e in under.get(node.line, []) for (r, i) in e.writes if r == reg])

    def read_band(node, reg):
        return _span([i for e in under.get(node.line, []) for (r, i) in e.reads if r == reg])

    def kind_of(node):
        evs = under.get(node.line, [])
        if node.text.startswith("for ") and any(e.frames and e.frames[-1].kind == "call" for e in evs):
            return "call_loop"
        if node.text.startswith("for "):
            return "loop"
        return evs[0].kind if evs else "empty"

    stmts = list(qprog.body)
    merge_anc: dict = {}                         # merge stmt line -> the sysprog ancilla it uses
    last_anchor = 0                              # index into `body` of the last anchored node
    pending_alloc = []                           # qec allocs/prepares waiting for their group

    def pos(node):
        return body.index(node)

    for s in stmts:
        k = _qec_kind(s)
        if k == "merge":
            band = _band(s.text.split("->")[-1], "c")
            hits = [n for n in free if kind_of(n) == "loop" and written(n, "c") == band]
            if len(hits) != 1:
                raise Unlinked(f"no single .sysprog loop writes c{list(band)} for {s.text!r}")
            take(hits[0], s, "band")
            # the merge ancilla: the qubit every trip's route names last-but-one is not stable;
            # use the 1-qubit request just before the loop instead (the group's `alloc ancilla[a,a+1)`)
            i = pos(hits[0])
            # group members before the loop (allocs, |+> preparation) -> the pending qec allocs
            before = [n for n in body[last_anchor:i] if n in free]
            for q in pending_alloc:
                qk = _qec_kind(q)
                want = {"alloc_block": "request_ancilla", "magic": "request_magic",
                        "alloc": "request_ancilla", "prepare": "loop"}[qk]
                cands = [n for n in before if n in free and kind_of(n) == want and
                         (qk != "alloc_block" or " patch " in n.text) and
                         (qk != "alloc" or (" patch " not in n.text and
                                            _count(n.text) == _alloc_size(q.text)))]
                if not cands:
                    raise Unlinked(f"no .sysprog statement for {q.text!r} before line {hits[0].line}")
                take(cands[0], q, {"alloc_block": "kind", "magic": "kind", "alloc": "count",
                                   "prepare": "kind"}[qk])
                if qk == "alloc" and _alloc_size(q.text) == 1:
                    merge_anc[s.line] = _first_qubit(cands[0])
                if qk == "magic":            # the factory workspace release right after it
                    rel = [n for n in body[pos(cands[0]):i] if n in free and kind_of(n) == "release_ancilla"
                           and " patch " not in n.text]
                    if rel:
                        take(rel[0], q, "kind")
            pending_alloc = []
            # the check ancillas' release after the loop
            rel = [n for n in body[i + 1:] if n in free and kind_of(n) == "release_ancilla" and
                   " patch " not in n.text]
            if rel:
                take(rel[0], s, "order")
            last_anchor = i
        elif k in ("alloc_block", "magic", "alloc", "prepare"):
            pending_alloc.append(s)
        elif k == "extract":
            band = _band(s.text, "syndrome")
            got = []
            for n in [n for n in free if kind_of(n) == "call_loop"]:
                w = [i for e in under[n.line] for (r, i) in e.writes if r == "syndrome"]
                if w and band[0] <= min(w) and max(w) < band[1]:
                    got.append((n, w))
            covered = sorted(i for _, w in got for i in w)
            if covered != list(range(band[0], band[1])):
                raise Unlinked(f"the extraction calls do not write exactly syndrome{list(band)} for {s.text!r}")
            for n, _ in got:
                take(n, s, "band")
        elif k == "decode":
            reg = "syndrome" if "syndrome[" in s.text else "c"
            band = _band(s.text, reg)
            hits = [n for n in free if kind_of(n) == "decode_syndrome" and read_band(n, reg) == band]
            if len(hits) != 1:
                raise Unlinked(f"no single decode reads {reg}{list(band)} for {s.text!r}")
            take(hits[0], s, "band")
        elif k == "split":
            name = s.text.split("= split", 1)[1].strip()
            merge = next((m for m in stmts if _qec_kind(m) == "merge" and f" {name} = merge" in f" {m.text}"), None)
            anc = merge_anc.get(merge.line) if merge else None
            hits = [n for n in free if kind_of(n) == "measure" and "basis=Z" in n.text and anc is not None
                    and _first_qubit(n) == anc]
            if len(hits) != 1:
                raise Unlinked(f"no Z measurement of the merge ancilla for {s.text!r}")
            take(hits[0], s, "operands")
            rel = [n for n in free if kind_of(n) == "release_ancilla" and _first_qubit(n) == anc]
            if rel:
                take(rel[0], s, "operands")
        elif k == "frame_on":
            band = _band(s.text, "c")
            hits = [n for n in free if kind_of(n) == "pauli_frame_update" and read_band(n, "c") == band]
            if not hits:
                raise Unlinked(f"no frame update reads c{list(band)} for {s.text!r}")
            for n in hits:
                take(n, s, "band")
        elif k == "frame":
            hits = [n for n in free if kind_of(n) == "pauli_frame_update" and not read_band(n, "c")]
            if not hits:
                raise Unlinked(f"no unconditional frame update for {s.text!r}")
            take(hits[0], s, "order")
        else:
            raise Unlinked(f"a .qec statement this linker does not know: {s.text!r}")
    # releases of whole blocks: inserted by the system compiler after the block's last use
    for n in list(free):
        if kind_of(n) == "release_ancilla" and " patch " in n.text:
            q = _last_user_of_patch(n, claimed, body)
            if q is not None:
                take(n, q, "inserted")
    if free:
        raise Unlinked("unlinked .sysprog program lines: " +
                       ", ".join(f"{n.line}: {n.text[:60]}" for n in free[:8]))
    chain.sys_to_qec = claimed


def _count(text: str) -> int:
    m = re.search(r"count=(\d+)", text)
    return int(m[1]) if m else -1


def _alloc_size(text: str) -> int:
    m = re.search(r"\[(\d+),(\d+)\)", text)
    return int(m[2]) - int(m[1]) if m else -1


def _first_qubit(node) -> tuple | None:
    m = re.search(r"\b(data|ancilla)\[(\d+)\]", node.text)
    return (m[1], int(m[2])) if m else None


def _last_user_of_patch(node, claimed, body):
    """The `.qec` statement of the last program line before `node` that names the patch's cells."""
    m = re.search(r"ancilla\[(\d+):(\d+)\]", node.text)
    if not m:
        return None
    cells = set(range(int(m[1]), int(m[2]) + 1))
    i = body.index(node)
    for n in reversed(body[:i]):
        nums = {int(x) for x in re.findall(r"ancilla\[(\d+)\]", n.text)}
        for a, b in re.findall(r"ancilla\[(\d+):(\d+)\]", n.text):
            nums |= set(range(int(a), int(b) + 1))
        mm = re.search(r"\((\d+),", n.text)          # a call's first argument is the block base
        if n.text.startswith("for ") and n.children and "call " in n.children[0].text:
            c = re.search(r"call \w+\((\d+)", n.children[0].text)
            if c:
                nums.add(int(c[1]))
        if nums & cells and n.line in claimed:
            return claimed[n.line].stmt
    return None


def _link_qppm_to_qec(chain: Chain) -> None:
    qp = chain.qppm.program
    qe = chain.qec.program
    if qp is None or qe is None:
        raise Unlinked("missing program unit")
    qppm_stmts = list(qp.statements())
    # anc[k] -> the k-th |Y+> (or other magic) block, by order of preparation
    anc_block, nb = {}, 0
    for s in qppm_stmts:
        m = re.match(r"prepare \|[^>]+>\s*->\s*anc\[(.+)\]", s.text)
        if m:
            anc_block[eval_simple(m[1])] = nb
            nb += 1
    magic_qppm = [s for s in qppm_stmts if re.match(r"prepare \|[^>]+>\s*->\s*anc\[", s.text)]
    measures = [s for s in qppm_stmts if s.text.startswith("measure ")]
    uncond = [s for s in qp.body if s.text.startswith("frame ")]
    guarded = [(w, c) for w in qp.statements() if w.text.startswith("when ") for c in w.children]
    out = {}
    stmts = list(qe.body)
    group: list = []
    magic_i = 0
    used_meas: set = set()
    merge_band: dict = {}
    uncond_i = 0
    for s in stmts:
        k = _qec_kind(s)
        if k in ("alloc_block", "magic"):
            if k == "magic":
                out[s.line] = Link(magic_qppm[magic_i], "order")
                group.append(s)
                for g in group:
                    out[g.line] = Link(magic_qppm[magic_i], "order")
                group = []
                magic_i += 1
            else:
                group.append(s)
        elif k in ("alloc", "prepare"):
            group.append(s)
        elif k == "merge":
            pa = _norm_pauli_qec(s.text.split("= merge", 1)[1].split(" with ")[0])
            hits = [m for m in measures if m.line not in used_meas and
                    _norm_pauli_qppm(m.text.split("->")[0], anc_block) == pa]
            if not hits:
                raise Unlinked(f"no .qppm measurement of {pa} for {s.text!r}")
            target = hits[0]
            used_meas.add(target.line)
            out[s.line] = Link(target, "pauli")
            for g in group:
                out[g.line] = Link(target, "group")
            group = []
            merge_band[target.line] = _band(s.text.split("->")[-1], "c")
            current = target
        elif k in ("extract", "decode", "split"):
            out[s.line] = Link(current, "group")
        elif k == "frame_on":
            band = _band(s.text, "c")
            pa = _norm_pauli_qec(s.text)
            cands = []
            for w, c in guarded:
                mj = re.match(r"when m\[(\d+)\]", w.text)
                meas = next((m for m in measures if re.search(rf"->\s*m\[{mj[1]}\]", m.text)), None) if mj else None
                mb = merge_band.get(meas.line) if meas else None
                if mb and mb[0] <= band[0] and band[1] <= mb[1] and \
                        _norm_pauli_qppm(c.text, anc_block) == pa and c.line not in {l.stmt.line for l in out.values()}:
                    cands.append(c)
            if not cands:
                raise Unlinked(f"no guarded .qppm frame for {s.text!r}")
            out[s.line] = Link(cands[0], "band")
        elif k == "frame":
            while uncond_i < len(uncond) and _norm_pauli_qppm(uncond[uncond_i].text, anc_block) != _norm_pauli_qec(s.text):
                uncond_i += 1
            if uncond_i >= len(uncond):
                raise Unlinked(f"no unconditional .qppm frame for {s.text!r}")
            out[s.line] = Link(uncond[uncond_i], "order")
            uncond_i += 1
    chain.qec_to_qppm = out


def _link_prog_to_qppm(chain: Chain) -> None:
    pp = chain.prog.program
    qp = chain.qppm.program
    if pp is None or qp is None:
        return
    top = [s for s in pp.body]
    out = {}
    if len(top) == 1:
        for s in qp.statements():
            out[s.line] = Link(top[0], "only")
    else:
        names = {re.match(r"(\w+)\(", s.text)[1]: s for s in top if re.match(r"\w+\(", s.text)}
        for s in qp.statements():
            m = re.match(r"call (\w+)\(|(\w+)\(", s.text)
            n = m and (m[1] or m[2])
            if n in names and n not in ("H", "X", "Z", "S", "CX", "CZ", "T"):
                out[s.line] = Link(names[n], "name")
    chain.qppm_to_prog = out
