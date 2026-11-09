"""FormalRV's Gidney 2025 listing read as logical work on patches (docs/FULLSTACK_PLAN.md, slice S10 step 3).

The listing (`listing.read`) is physical gates on virtual qubits, but every gate belongs to one of a few
pieces, and this module recognises them, gadget by gadget:

    ("prep", p, patches, basis)    `request_ancilla ... patch`, with `H` on all its ions: |0̄> or |+̄>
    ("magic", state, p)            `request_magic` into patch(es) p
    ("rep", reps, targets, cat)    a loop holding `route`s: `reps` repetitions of a cat state over the
                                   targets [(patch, letter)], letter Z (row 0, CZ), X (column 0, CNOT) or Y (their
                                   crossing, CY); checked gate by gate against what the writer writes (`_as_written`);
                                   `cat` ions, not counting the flag and the verifiers
    ("cold", reps, block, sel, letter, partner)  the same with a `fanLadder` on stored qubit sel of a block
    ("rounds", n, patch)           n syndrome rounds of a patch
    ("read", patch, basis)         its logical operator's data ions measured, in the listing's basis (Z or X)
    ("h", patch)                   a guarded H on data ions of a patch
    ("free", p)                    its release
    ("decode", rounds)             a decode_syndrome
    ("guard", source) ... ("endguard",)   quantum work under a `when`: it waits for the local outcomes before
                                   it, unless its guard reads a `source` record, an outcome of long ago

A patch is a hot wire `w` (an int >= 0), a cold block `-1 - b`, or (`Anc`) one patch of a request.
Guards are charged taken, as FormalRV's VM charges them; a `when` over quantum work reads as its body.

The flat list is memoised on the formals that shape it, with request addresses kept relative to the gadget's
`rv_anc`, so walking one prime takes seconds.
"""
from __future__ import annotations

import bisect
import re
from dataclasses import dataclass

from ...compile.tiled_cat import _tree, operator_gates
from ..sysprog import split_top
from . import listing as L

__all__ = ["Anc", "Reader", "HOT0", "HOT_STRIDE", "COLD_STRIDE"]

HOT0, HOT_STRIDE, COLD_STRIDE = 50575, 121, 7225
D = 11
INF = float("inf")


@dataclass(frozen=True)
class Anc:
    base: int       # the request's first address, relative to the frame it is read in
    index: int = 0  # which patch of the request

    def shifted(self, by: int) -> "Anc":
        return Anc(self.base + by, self.index)


_OPERAND = re.compile(r"(?:[XYZ]\s+)?([a-z]+)\[(.*)\]")


class Reader:
    """One listing, read as logical work."""

    def __init__(self, lst: L.Listing):
        self.lst = lst
        self._ops: dict = {}
        self._shape = self._shaping()
        self._memo: dict = {}

    # ---------------------------------------------------------------- operands, compiled once
    def operands(self, s: L.Stmt, text: str | None = None):
        got = self._ops.get((s.line, text))
        if got is None:
            got = []
            for item in split_top(text if text is not None else s.text.split(None, 1)[1]):
                m = _OPERAND.fullmatch(item.strip())
                if m and m[1] in ("data", "ancilla"):
                    parts = split_top(m[2], ":")
                    lo = L._Expr(parts[0], self.lst.tables)
                    got.append((m[1], lo, L._Expr(parts[1], self.lst.tables) if len(parts) == 2 else lo))
            self._ops[(s.line, text)] = got
        return got

    def _shaping(self) -> dict:
        """For each gadget, the formals its flat list depends on, addresses of requests aside: what
        shapes its loops, and every formal reaching a data operand (a wire)."""
        lst = self.lst
        need = {g.name: set(g.relevant) for g in lst.gadgets.values()}
        changed = True
        while changed:
            changed = False
            for g in lst.gadgets.values():
                have = need[g.name]
                before = len(have)

                def scan(nodes, bound):
                    for n in nodes:
                        if isinstance(n, L.For):
                            scan(n.body, bound | {n.v})
                        elif isinstance(n, L.When):
                            scan(n.body, bound)
                        elif isinstance(n, L.Case):
                            for a in n.arms.values():
                                scan(a, bound)
                        elif isinstance(n, L.Stmt):
                            if n.kind in ("CNOT", "CZ", "CY", "H", "measure"):
                                head = n.text.split(None, 1)[1].split("->")[0]
                                head = head.split("basis=", 1)[-1][1:] if n.kind == "measure" else head
                                for reg, lo, hi in self.operands(n, head):
                                    if reg == "data":
                                        have.update(lo.names - bound)
                        elif isinstance(n, L.CallS):
                            c = lst.gadgets[n.name]
                            for f, a in zip(c.formals, n.args):
                                if f in need[c.name] or f in ("dbase", "sel"):
                                    have.update(a.names - bound)
                scan(g.body, frozenset())
                have &= set(g.formals)
                have.discard("rv_anc")      # requests are read relative to it
                if len(have) != before:
                    changed = True
        return need

    # ---------------------------------------------------------------- the flat list
    def key(self, name: str, args: dict) -> tuple:
        """The identity of one call's flat list: the gadget and the formals that shape it."""
        g = self.lst.gadgets[name]
        return (name, tuple(sorted(((f, args[f]) for f in g.formals if f in self._shape[g.name]),
                                   key=lambda kv: kv[0])))

    def flat(self, key: tuple) -> list:
        """The items of one gadget instance, in stream order, its requests relative to its frame; a
        gadget it calls is the item ("call", key, base), base the callee's `rv_anc` in this frame."""
        got = self._memo.get(key)
        if got is None:
            g = self.lst.gadgets[key[0]]
            env = {**self.lst.params, **{f: 0 for f in g.formals}, **dict(key[1])}
            env["rv_anc"] = 0
            got = self._memo[key] = self._walk(g.body, env, {})
        return got

    def census(self, key: tuple | None = None, memo: dict | None = None):
        """{item kind: dynamic count} under one instance (or the program), rounds counted singly."""
        from collections import Counter
        memo = {} if memo is None else memo
        if key in memo:
            return memo[key]
        items = self.program() if key is None else self.flat(key)
        out = Counter()
        for it in items:
            if it[0] == "call":
                for k, v in self.census(it[1], memo).items():
                    out[k] += v
            elif it[0] == "rounds":
                out["rounds"] += it[1]
            elif it[0] in ("rep", "cold"):
                out[it[0]] += 1
                out["reps"] += it[1]
            elif it[0] == "magic":
                out[it[1]] += 1
            elif it[0] not in ("guard", "endguard"):
                out[it[0]] += 1
        memo[key] = out
        return out

    def program(self) -> list:
        return self._walk(self.lst.body, dict(self.lst.params), {})

    def _patch_of(self, reg, x, reqs):
        if reg == "data":
            if x >= HOT0:
                return (x - HOT0) // HOT_STRIDE, (x - HOT0) % HOT_STRIDE
            return -1 - x // COLD_STRIDE, x % COLD_STRIDE
        live = reqs.get("list", ())
        i = bisect.bisect_right(live, (x, INF)) - 1
        if i >= 0 and x < live[i][0] + live[i][1]:
            return Anc(live[i][0], (x - live[i][0]) // HOT_STRIDE), (x - live[i][0]) % HOT_STRIDE
        return None, None

    def _walk(self, nodes, env: dict, reqs: dict) -> list:
        out: list = []
        reqs.setdefault("list", [])
        for n in nodes:
            if isinstance(n, L.Stmt):
                self._stmt(n, env, reqs, out)
            elif isinstance(n, L.CallS):
                self._call(n, env, reqs, out)
            elif isinstance(n, L.When):
                body = self._walk(n.body, env, reqs)
                if body:            # quantum work waits for its guard
                    out += [("guard", n.guard.startswith("source")), *body, ("endguard",)]
            elif isinstance(n, L.Case):
                v = n.e(env)
                out += self._walk(n.arms.get(1 if v is L.TOP else v, []), env, reqs)
            elif isinstance(n, L.For):
                self._for(n, env, reqs, out)
        return out

    def _stmt(self, s: L.Stmt, env, reqs, out) -> None:
        k = s.kind
        if k == "request_ancilla" and " patch " in s.text:
            _, lo, hi = self.operands(s, s.text[s.text.index("patch") + 5:])[0]
            base, top = lo(env), hi(env)
            bisect.insort(reqs["list"], (base, top - base + 1))
            out.append(("prep", base, (top - base + 1) // HOT_STRIDE, "Z"))
        elif k == "release_ancilla" and " patch " in s.text:
            out.append(("free", self.operands(s, s.text[s.text.index("patch") + 5:])[0][1](env)))
        elif k == "request_magic":
            state = re.search(r"state=(\S+)", s.text)[1]
            out.append(("magic", state, self.operands(s, s.text.split("request_magic")[0])[0][1](env)))
        elif k == "decode_syndrome":
            out.append(("decode", int(re.search(r"rounds=(\d+)", s.text)[1])))
        elif k == "measure":
            head = s.text.split("basis=", 1)[1][1:].split("->")[0]
            reg, lo, _ = self.operands(s, head)[0]
            p, _off = self._patch_of(reg, lo(env), reqs)
            if p is not None:
                out.append(("read", p, s.text.split("basis=", 1)[1][0]))
        elif k == "H":
            reg, lo, _ = self.operands(s, s.text[1:].strip())[0]
            p, _off = self._patch_of(reg, lo(env), reqs)
            if p is not None:
                out.append(("h", p))

    def _call(self, n: L.CallS, env, reqs, out) -> None:
        g = self.lst.gadgets[n.name]
        if n.name.startswith("surfaceRound"):
            d = n.args[0](env)
            reg = "ancilla" if "LogicalAncilla" in n.name else "data"
            out.append(("rounds", 1, self._patch_of(reg, d, reqs)[0]))
            return
        if n.name.startswith(("logicalFrame",)):
            return
        args = {f: a(env) for f, a in zip(g.formals, n.args)}
        out.append(("call", self.key(n.name, args), args.get("rv_anc", 0)))

    def _for(self, n: L.For, env, reqs, out) -> None:
        lo, hi = n.lo(env), n.hi(env)
        hi = lo + 1 if hi is L.TOP else hi
        if hi <= lo:
            return
        direct = [s for s in n.body if isinstance(s, L.Stmt) and s.kind == "route"]
        fan = [s for s in n.body if isinstance(s, L.CallS) and s.name.startswith("fanLadder")]
        nested = n.v.startswith("rv_s") and any(isinstance(s, L.Stmt) and s.kind == "route"
                                                for s in L._walk_nodes(n.body))
        if direct or fan or nested:
            out.append(self._rep(n, hi - lo, {**env, n.v: lo}, reqs))
            return
        calls = [s for s in n.body if isinstance(s, L.CallS)]
        if calls and len(calls) == len(n.body) and all(s.name.startswith("surfaceRound") for s in calls):
            for s in calls:                         # rounds, each patch's own
                reg = "ancilla" if "LogicalAncilla" in s.name else "data"
                out.append(("rounds", hi - lo, self._patch_of(reg, s.args[0]({**env, n.v: lo}), reqs)[0]))
            return
        if n.v.startswith("rv_h"):                  # an H on every ion of a fresh patch
            for i in range(len(out) - 1, -1, -1):
                if out[i][0] == "prep":
                    out[i] = (*out[i][:3], "X")
                    break
            return
        for v in range(lo, hi):
            out += self._walk(n.body, {**env, n.v: v}, reqs)

    def _rep(self, n: L.For, reps: int, env, reqs) -> tuple:
        """One repetition's targets, read from its first trip."""
        hits: dict = {}
        cat: set = set()
        cold = []
        gates: list = []
        counted = {"H": 0, "X": 0}

        def touch(s, e):
            qs = []
            for reg, lo, hi in self.operands(s):
                for x in range(lo(e), hi(e) + 1):
                    p, off = self._patch_of(reg, x, reqs)
                    if p is None:
                        cat.add(x)
                        qs.append(x)
                    else:
                        hits.setdefault(p, []).append((s.kind, off))
                        qs.append((p, off))
            gates.extend((s.kind, qs[i], qs[i + 1]) for i in range(0, len(qs) - 1, 2))

        def scan(nodes, e):
            for s in nodes:
                if isinstance(s, L.Stmt):
                    if s.kind in ("CNOT", "CZ", "CY"):
                        touch(s, e)
                    elif s.kind == "H":
                        counted["H"] += 1
                    elif s.kind == "measure":
                        counted["X"] += "basis=X" in s.text
                elif isinstance(s, L.For):
                    a, b = s.lo(e), s.hi(e)
                    for v in range(a, a + 1 if b is L.TOP else b):
                        scan(s.body, {**e, s.v: v})
                elif isinstance(s, L.When):
                    scan(s.body, e)
                elif isinstance(s, L.Case):
                    v = s.e(e)
                    scan(s.arms.get(1 if v is L.TOP else v, []), e)
                elif isinstance(s, L.CallS) and s.name.startswith("fanLadder"):
                    g = self.lst.gadgets[s.name]
                    a = {f: x(e) for f, x in zip(g.formals, s.args)}
                    cold.append((-1 - a["dbase"] // COLD_STRIDE, a["sel"], s.name[9]))

        scan(n.body, env)
        targets = tuple((p, _letter(offs)) for p, offs in hits.items() if not (isinstance(p, int) and p < 0))
        if cold:
            block, sel, letter = cold[0]
            return ("cold", reps, block, sel, letter, targets, len(cat))
        where = f"line {n.line}"
        acting = _as_written(gates, targets, counted, where)
        return ("rep", reps, targets, len(cat)) if acting is None else ("rep", reps, targets, len(cat), acting)


_GATE = {"cz": "CZ", "cx": "CNOT", "cy": "CY"}
_OPERATORS = {letter: [(_GATE[g], off) for off, g in operator_gates(letter, D)] for letter in "ZXY"}


def _letter(marks) -> str:
    """A patch's operator, read from its gates; anything else is no product the writer can play."""
    for letter, gates in _OPERATORS.items():
        if marks == gates:
            return letter
    raise ValueError(f"a patch's gates are no logical operator: {marks}")


def _as_written(gates, targets, counted, where) -> tuple | None:
    """The check that makes R10 about the listing: R10 proves a program implements the writer's circuit, and this that
    the writer's circuit is the listing's repetition, gate by gate, in order, each by the same cat ion: the tree, the
    operators, the flag, the verifier.  Refuses anything else.  Returns None, or for a masked cat (only some of a
    register's ions act) which cat ions act on the targets, in order."""
    free = [(a, b) for k, a, b in gates if isinstance(a, int) and isinstance(b, int)]
    on = [(k, a, *b) for k, a, b in gates if isinstance(a, int) and not isinstance(b, int)]
    if len(free) + len(on) != len(gates) or len(free) < 2:
        raise ValueError(f"{where}: a patch is the control of a gate, or there is no flag")
    flag, ver = free[-1][1], free[-1][0]
    cats = sorted({q for g in free for q in g} - {flag, ver})
    k = len(cats)
    if free != [g for lay in _tree(cats) for g in lay] + [(cats[0], flag), (ver, flag)]:
        raise ValueError(f"{where}: the cat is not the writer's tree, flag and verifier: {free}")
    if counted != {"H": 2, "X": k + 1}:
        raise ValueError(f"{where}: {counted} for a cat of {k}")
    want = [(g, p, off) for p, letter in targets for g, off in _OPERATORS[letter]]
    if [(g, p, off) for g, _a, p, off in on] != want:
        raise ValueError(f"{where}: the operators are not in the writer's order")
    owners = [cats.index(a) for _g, a, _p, _off in on]
    if k == (len(on) + 3) // 4 and owners == [i // 4 for i in range(len(on))]:
        return None
    own = owners[::D]
    if owners == [o for o in own for _ in range(D)] and own[0] == 0 and own == sorted(set(own)):
        if own == list(range(k)) and len(targets) > 4:
            return None
        if k > len(targets):
            return tuple(own)
    raise ValueError(f"{where}: its cat ions are not given out as the writer gives them: {owners}")
