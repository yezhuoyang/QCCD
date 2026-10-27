"""The distribution of a FormalRV `.prog`'s classical results, computed from the `.prog` itself.

The reference the lowered gadget program is compared with: a state vector over the `.prog`'s
qubits from |0…0⟩, every `measure q ↦ c[i]` a branch, `when c[i]:` bodies run in the branches
where the bit is 1, loops and compile-time `if`s evaluated, gadget calls placed `@ id()`
expanded with their formals.  Gates: X, Z, H, CX, CZ, CCX and Rz(q, k) = diag(1, e^{-iπ/2^k}),
FormalRV's own convention (GateLang.lean: `Rz q k` puts exp(-i·π/2^k) on |1⟩).

`ctrl q:` controls everything below it on qubit q (gates only: a measurement under a control
is refused); a call placed `@ m(args)` runs its body through FormalRV's qubit map `m` (`map
shift(b)(i) = b + i`); loops count up (`for 0 ≤ k < n`, with or without `headfirst`) or down
(`for n > k ≥ 0`).  The state is sparse -- a dict of the nonzero amplitudes -- so a program is
as large as its superposition, not its qubit count: Shor's modular exponentiation keeps one
term per exponent.  A state past `MAX_TERMS` terms is refused by name.
"""
from __future__ import annotations

import cmath
import math
import re

from . import layers
from .sysprog import split_top

_R2 = 1 / math.sqrt(2)


class NotSimulated(ValueError):
    pass


#: the most nonzero amplitudes one branch may hold
MAX_TERMS = 1 << 20

_MAP = re.compile(r"^\s*map\s+(\w+)\(([^)]*)\)\((\w+)\)\s*=\s*(.+?)\s*$")


def _maps(prog: layers.Stage) -> dict:
    """FormalRV's qubit maps, `map shift(b)(i) = b + i`: name -> (params, index, expression)."""
    out = {}
    for line in getattr(prog, "lines", []) or []:
        m = _MAP.match(line)
        if m:
            out[m[1]] = ([x.strip() for x in m[2].split(",") if x.strip()], m[3], m[4])
    return out


def _ev(text: str, env: dict) -> int:
    t = text.replace("·", "*").replace("≤", "<=").replace("≥", ">=").strip()
    t = re.sub(r"(?<![<>=!])=(?!=)", "==", t)
    t = t.replace("^", "**")                         # FormalRV's ^ is a power (⊕ is xor)
    t = re.sub(r"(?<!/)/(?!/)", "//", t)              # and its / divides naturals
    for tok in re.findall(r"[A-Za-z_]\w*", t):
        if tok not in env and tok not in ("not", "and", "or"):
            raise NotSimulated(f"unbound name {tok!r} in {text!r}")
    if not re.fullmatch(r"[\w\s+\-*/%()<>=!]+", t):
        raise NotSimulated(f"unsupported expression {text!r}")
    return int(eval(t, {"__builtins__": {}}, dict(env)))  # noqa: S307 -- checked above


def distribution(prog: layers.Stage, params: dict, records: int, order: list | None = None,
                 states: dict | None = None, inputs: dict | None = None) -> dict:
    """{result bits: probability}; `order`, if given, receives the record index of every
    measurement in the order the program runs them.  `inputs` {qubit: 1}: the basis state the
    program is given (its spec's `assumes`; |0…0⟩ by default)."""
    gad = prog.gadgets()
    maps = _maps(prog)
    unit = prog.program
    m = re.search(r"\[q:\s*([^,\]]+)", unit.header.text)
    n = _ev(m[1], params) if m else 0
    start = sum(1 << int(q) for q, v in (inputs or {}).items() if v)
    branches = [({start: 1 + 0j}, [0] * records, 1.0)]

    def gate1(amp, q, mtx):
        k = 1 << q
        new = {}
        for b, a in amp.items():
            hi = 1 if b & k else 0
            b0 = b & ~k
            for o in (0, 1):
                c = mtx[o][hi]
                if c:
                    key = b0 | (k if o else 0)
                    new[key] = new.get(key, 0) + c * a
        return {b: a for b, a in new.items() if abs(a) > 1e-12}

    def perm(amp, f):
        return {f(b): a for b, a in amp.items()}

    def run(body, env, brs, qmap=lambda i: i, ctl=()):
        """`qmap`: this body's qubit indices -> the program's; `ctl`: the program's qubits that
        control everything here."""
        def on(b):
            return all(b >> c & 1 for c in ctl)

        for s in body:
            t = s.text.strip()
            loop = re.fullmatch(r"for\s+(.+?)\s*(?:<=|≤)\s*(\w+)\s*<\s*(.+?)(?:\s+headfirst)?:", t)
            if loop:
                for v in range(_ev(loop[1], env), _ev(loop[3], env)):
                    brs = run(s.children, {**env, loop[2]: v}, brs, qmap, ctl)
                continue
            down = re.fullmatch(r"for\s+(.+?)\s*>\s*(\w+)\s*(?:>=|≥)\s*(.+?):", t)
            if down:
                for v in range(_ev(down[1], env) - 1, _ev(down[3], env) - 1, -1):
                    brs = run(s.children, {**env, down[2]: v}, brs, qmap, ctl)
                continue
            c = re.fullmatch(r"ctrl\s+(.+):", t)
            if c:
                brs = run(s.children, env, brs, qmap, ctl + (qmap(_ev(c[1], env)),))
                continue
            if t.startswith("if ") and t.endswith(":"):
                if _ev(t[3:-1], env):
                    brs = run(s.children, env, brs, qmap, ctl)
                continue
            w = re.fullmatch(r"when\s+c\[(.+)\]:", t)
            if w:
                i = _ev(w[1], env)
                yes = [b for b in brs if b[1][i]]
                no = [b for b in brs if not b[1][i]]
                brs = no + run(s.children, env, yes, qmap, ctl)
                continue
            me = re.fullmatch(r"measure\s+(.+?)\s*↦\s*c\[(.+)\]", t)
            if me:
                if ctl:
                    raise NotSimulated(f".prog line {s.line}: a measurement under a control")
                q, i = qmap(_ev(me[1], env)), _ev(me[2], env)
                if order is not None:
                    order.append(i)
                out = []
                for amp, c, p in brs:
                    for o in (0, 1):
                        keep = {b: a for b, a in amp.items() if ((b >> q) & 1) == o}
                        pr = sum(abs(a) ** 2 for a in keep.values())
                        if pr > 1e-12:
                            c2 = list(c)
                            c2[i] = o
                            out.append(({b: a / math.sqrt(pr) for b, a in keep.items()}, c2, p * pr))
                brs = out
                continue
            call = re.fullmatch(r"(\w+)\((.*)\)\s*@\s*(\w+)\((.*)\)", t)
            if call and call[1] in gad:
                g = gad[call[1]]
                fm = re.match(r"gadget\s+\w+\[.*?\]\((.*)\):", g.header.text)
                formals = [f.strip() for f in split_top(fm[1], ",")] if fm else []
                args = [_ev(a, env) for a in split_top(call[2], ",")] if call[2].strip() else []
                if call[3] not in maps:
                    raise NotSimulated(f".prog line {s.line}: no map {call[3]!r}")
                mp, idx, expr = maps[call[3]]
                margs = [_ev(a, env) for a in split_top(call[4], ",")] if call[4].strip() else []
                bound = dict(zip(mp, margs))

                def inner(i, outer=qmap, bound=bound, idx=idx, expr=expr):
                    return outer(_ev(expr, {**bound, idx: i}))
                brs = run(g.body, {**env, **dict(zip(formals, args))}, brs, inner, ctl)
                continue
            gm = re.fullmatch(r"(X|Z|H|CX|CZ|CCX|Rz)\((.*)\)", t)
            if not gm:
                raise NotSimulated(f".prog line {s.line}: {t!r}")
            raw = [_ev(x, env) for x in split_top(gm[2], ",")]
            nm = gm[1]
            # qubits through the map; Rz's second argument is its angle, not a qubit
            a = [qmap(raw[0]), raw[1]] if nm == "Rz" else [qmap(x) for x in raw]
            out = []
            for amp, c, p in brs:
                if ctl:
                    # a controlled gate: it acts on the terms whose controls are all 1
                    rest = {b: v for b, v in amp.items() if not on(b)}
                    amp = {b: v for b, v in amp.items() if on(b)}
                if nm == "X":
                    amp = perm(amp, lambda b, q=a[0]: b ^ (1 << q))
                elif nm == "Z":
                    amp = gate1(amp, a[0], ((1, 0), (0, -1)))
                elif nm == "H":
                    amp = gate1(amp, a[0], ((_R2, _R2), (_R2, -_R2)))
                elif nm == "Rz":
                    # FormalRV's Rz(q, k): phase exp(-i·π/2^k) on |1⟩ (FormalRV/QParam/GateLang.lean)
                    amp = gate1(amp, a[0], ((1, 0), (0, cmath.exp(-1j * math.pi / 2 ** a[1]))))
                elif nm == "CX":
                    amp = perm(amp, lambda b, x=a[0], y=a[1]: b ^ (1 << y) if b >> x & 1 else b)
                elif nm == "CZ":
                    amp = {b: (-v if (b >> a[0] & 1) and (b >> a[1] & 1) else v) for b, v in amp.items()}
                elif nm == "CCX":
                    amp = perm(amp, lambda b, x=a[0], y=a[1], z=a[2]:
                               b ^ (1 << z) if (b >> x & 1) and (b >> y & 1) else b)
                if ctl:
                    for b, v in rest.items():
                        amp[b] = amp.get(b, 0) + v
                if len(amp) > MAX_TERMS:
                    raise NotSimulated(f".prog line {s.line}: the state has {len(amp)} terms")
                out.append((amp, c, p))
            brs = out
        return brs

    brs = run(unit.body, dict(params), branches)
    dist: dict = {}
    for amp, c, p in brs:
        dist[tuple(c)] = dist.get(tuple(c), 0.0) + p
        if states is not None:
            # the post-measurement state of every qubit, for this assignment of the results
            states.setdefault(tuple(c), (n, amp))
    return {k: round(v, 9) for k, v in sorted(dist.items())}
