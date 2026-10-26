"""The distribution of a FormalRV `.prog`'s classical results, computed from the `.prog` itself.

The reference the lowered gadget program is compared with: a state vector over the `.prog`'s
qubits from |0…0⟩, every `measure q ↦ c[i]` a branch, `when c[i]:` bodies run in the branches
where the bit is 1, loops and compile-time `if`s evaluated, gadget calls placed `@ id()`
expanded with their formals.  Gates: X, Z, H, CX, CZ, CCX and Rz(q, k) = diag(1, e^{-iπ/2^k}),
FormalRV's own convention (GateLang.lean: `Rz q k` puts exp(-i·π/2^k) on |1⟩).
It is for SMALL programs (it simulates every qubit); anything else is refused by name.
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


def _ev(text: str, env: dict) -> int:
    t = text.replace("·", "*").replace("≤", "<=").replace("≥", ">=").strip()
    t = re.sub(r"(?<![<>=!])=(?!=)", "==", t)
    for tok in re.findall(r"[A-Za-z_]\w*", t):
        if tok not in env:
            raise NotSimulated(f"unbound name {tok!r} in {text!r}")
    if not re.fullmatch(r"[\w\s+\-*/%()<>=!]+", t):
        raise NotSimulated(f"unsupported expression {text!r}")
    return int(eval(t.replace("/", "//"), {"__builtins__": {}}, dict(env)))  # noqa: S307 -- checked above


def distribution(prog: layers.Stage, params: dict, records: int, order: list | None = None,
                 states: dict | None = None) -> dict:
    """{result bits: probability}; `order`, if given, receives the record index of every
    measurement in the order the program runs them."""
    gad = prog.gadgets()
    unit = prog.program
    m = re.search(r"\[q:\s*([^,\]]+)", unit.header.text)
    n = _ev(m[1], params) if m else 0
    if n > 20:
        raise NotSimulated(f"{n} qubits is too many to simulate")
    branches = [({0: 1 + 0j}, [0] * records, 1.0)]

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

    def run(body, env, brs):
        for s in body:
            t = s.text.strip()
            loop = re.fullmatch(r"for\s+(.+?)\s*(?:<=|≤)\s*(\w+)\s*<\s*(.+?):", t)
            if loop:
                for v in range(_ev(loop[1], env), _ev(loop[3], env)):
                    brs = run(s.children, {**env, loop[2]: v}, brs)
                continue
            if t.startswith("if ") and t.endswith(":"):
                if _ev(t[3:-1], env):
                    brs = run(s.children, env, brs)
                continue
            w = re.fullmatch(r"when\s+c\[(.+)\]:", t)
            if w:
                i = _ev(w[1], env)
                yes = [b for b in brs if b[1][i]]
                no = [b for b in brs if not b[1][i]]
                brs = no + run(s.children, env, yes)
                continue
            me = re.fullmatch(r"measure\s+(.+?)\s*↦\s*c\[(.+)\]", t)
            if me:
                q, i = _ev(me[1], env), _ev(me[2], env)
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
            call = re.fullmatch(r"(\w+)\((.*)\)\s*@\s*id\(\)", t)
            if call and call[1] in gad:
                g = gad[call[1]]
                fm = re.match(r"gadget\s+\w+\[.*?\]\((.*)\):", g.header.text)
                formals = [f.strip() for f in split_top(fm[1], ",")] if fm else []
                args = [_ev(a, env) for a in split_top(call[2], ",")]
                brs = run(g.body, {**env, **dict(zip(formals, args))}, brs)
                continue
            gm = re.fullmatch(r"(X|Z|H|CX|CZ|CCX|Rz)\((.*)\)", t)
            if not gm:
                raise NotSimulated(f".prog line {s.line}: {t!r}")
            a = [_ev(x, env) for x in split_top(gm[2], ",")]
            nm = gm[1]
            out = []
            for amp, c, p in brs:
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
