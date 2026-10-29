"""FormalRV's index arithmetic, parsed once and evaluated many times, at any size.

The `.qec` FormalRV writes for a program keeps its loops and its gadgets' parameters
symbolic, so every number QCCD needs -- a block's index, a loop's trip count, the constant a
modular multiplier adds -- is an expression over loop variables and parameters.  This is the
arithmetic of `qccd.fullstack.sysprog.evaluate` (natural numbers: `-` stops at 0, `/` is
floor division, `x / 0 = 0`, `x % 0 = x`), with two differences that matter at scale:

* an expression is parsed once (`parse`, cached by text) into a small tree, and evaluated
  from the tree;
* a power under a remainder is computed modulo, never in full: Shor's constant
  `a^(2^(m - 1 - j)) % N` at m = 4096 has an exponent of four thousand bits, and the power
  itself would not fit in any memory.  `x % N` evaluates `x` modulo `N` through `+`, `*` and
  `^` (the only operations that commute with the remainder); anything else under it is
  evaluated exactly, as `evaluate` does.
"""
from __future__ import annotations

import re
from functools import lru_cache

__all__ = ["parse", "ev", "free", "Expr", "ExprError"]

_TOKEN = re.compile(r"\d+|[A-Za-z_][A-Za-z_0-9']*|[()+*/%^\-,]|\S")

#: an exact power larger than this many bits is refused: it is a remainder that was not
#: written as one, and computing it would not end
MAX_BITS = 1 << 22


class ExprError(ValueError):
    pass


# a node: ("n", value) | ("v", name) | (op, a, b) for op in + - * / % ^ | ("min"|"max", [args])
@lru_cache(maxsize=None)
def parse(text: str):
    tokens = _TOKEN.findall(text.replace("·", "*"))
    pos = 0

    def atom():
        nonlocal pos
        if pos >= len(tokens):
            raise ExprError(f"incomplete expression: {text}")
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            v = expr(0)
            if pos >= len(tokens) or tokens[pos] != ")":
                raise ExprError(f"missing ')' in {text}")
            pos += 1
            return v
        if tok.isdecimal():
            return ("n", int(tok))
        if tok in ("min", "max") and pos < len(tokens) and tokens[pos] == "(":
            pos += 1
            args = [expr(0)]
            while pos < len(tokens) and tokens[pos] == ",":
                pos += 1
                args.append(expr(0))
            if pos >= len(tokens) or tokens[pos] != ")":
                raise ExprError(f"missing ')' in {text}")
            pos += 1
            return (tok, tuple(args))
        if re.fullmatch(r"[A-Za-z_][A-Za-z_0-9']*", tok):
            return ("v", tok)
        raise ExprError(f"unsupported token {tok!r} in {text!r}")

    def expr(minimum: int):
        nonlocal pos
        value = atom()
        while pos < len(tokens) and tokens[pos] in "+-*/%^":
            op = tokens[pos]
            prec = {"+": 1, "-": 1, "*": 2, "/": 2, "%": 2, "^": 3}[op]
            if prec < minimum:
                break
            pos += 1
            rhs = expr(prec if op == "^" else prec + 1)
            value = (op, value, rhs)
        return value

    node = expr(0)
    if pos != len(tokens):
        raise ExprError(f"unparsed suffix in {text!r}")
    return node


def ev(node, env: dict) -> int:
    """The value of `node` (a tree from `parse`, or a text) with `env`'s variables."""
    if isinstance(node, str):
        node = parse(node)
    k = node[0]
    if k == "n":
        return node[1]
    if k == "v":
        try:
            return env[node[1]]
        except KeyError:
            raise ExprError(f"unbound {node[1]!r}") from None
    if k in ("min", "max"):
        vals = [ev(a, env) for a in node[1]]
        return min(vals) if k == "min" else max(vals)
    if k == "%":
        m = ev(node[2], env)
        return _mod(node[1], env, m) if m else ev(node[1], env)
    a, b = ev(node[1], env), ev(node[2], env)
    if k == "+":
        return a + b
    if k == "-":
        return a - b if a > b else 0
    if k == "*":
        return a * b
    if k == "/":
        return a // b if b else 0
    if k == "^":
        if a > 1 and b * max(1, a.bit_length()) > MAX_BITS:
            raise ExprError(f"{a}^{b} is too large to compute exactly; write it under a remainder")
        return a ** b
    raise ExprError(f"no operation {k!r}")


def _mod(node, env: dict, m: int) -> int:
    """`node` modulo m (> 0), computed modulo where that is exact."""
    k = node[0]
    if k == "+":
        return (_mod(node[1], env, m) + _mod(node[2], env, m)) % m
    if k == "*":
        return (_mod(node[1], env, m) * _mod(node[2], env, m)) % m
    if k == "^":
        return pow(_mod(node[1], env, m), ev(node[2], env), m)
    return ev(node, env) % m


def free(node) -> frozenset:
    """The variables `node` reads."""
    if isinstance(node, str):
        node = parse(node)
    k = node[0]
    if k == "n":
        return frozenset()
    if k == "v":
        return frozenset([node[1]])
    if k in ("min", "max"):
        return frozenset().union(*(free(a) for a in node[1]))
    return free(node[1]) | free(node[2])


class Expr:
    """An expression and its text, compared and hashed by text."""
    __slots__ = ("text", "node")

    def __init__(self, text: str):
        self.text = " ".join(text.split())
        self.node = parse(self.text)

    def __call__(self, env: dict) -> int:
        return ev(self.node, env)

    @property
    def vars(self) -> frozenset:
        return free(self.node)

    def __eq__(self, other):
        return isinstance(other, Expr) and other.text == self.text

    def __hash__(self):
        return hash(self.text)

    def __repr__(self):
        return f"Expr({self.text!r})"
