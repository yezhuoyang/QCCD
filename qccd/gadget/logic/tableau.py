"""A stabilizer tableau whose signs are symbolic in the measurement record.

Aaronson-Gottesman (quant-ph/0406196) with two changes.

**Columns, not rows.**  Qubit q's X bits over all 2n rows are one Python int, and so are
its Z bits and the constant sign bits.  A Clifford gate is then two or three int
operations whatever the tableau's size; only a measurement touches every qubit.

**Signs are affine functions of random bits.**  A random measurement outcome is a fresh
variable, not a coin flip, so one run covers every branch.  Each row's sign is a constant
(the column `R`) XOR a set of variables (`V[row]`, an int).  A deterministic measurement
returns the affine function it equals: that is a detector, found rather than declared.
A reset measures into a *hidden* variable and flips the qubit back; hidden variables are
outcomes nobody recorded, so a stabilizer whose sign depends on one is not a stabilizer of
what an observer holds.  If a recorded measurement later equals a function involving a
hidden variable, that variable is solved for and the record becomes a fresh visible one.

Flows come from a Choi state: every circuit qubit starts in a Bell pair with a reference
qubit the circuit never touches.  `P -> Q xor rec[S]` holds (stim's convention: measuring
P before equals measuring Q after, XOR the records S) exactly when `Pᵀ ⊗ Q` is in the
final stabilizer group with a sign equal to the XOR of those records.
"""

from __future__ import annotations

__all__ = ["Tableau", "NotClifford", "bits", "simulate", "pauli_bits"]


class NotClifford(ValueError):
    pass


def bits(v: int):
    while v:
        low = v & -v
        yield low.bit_length() - 1
        v ^= low


def _prefix_parity(b: int, width: int) -> int:
    """Bit k of the result is the parity of the bits of `b` below k."""
    y = b << 1
    s = 1
    while s < width:
        y ^= y << s
        s <<= 1
    return y


def pauli_bits(pauli: dict, index) -> tuple[int, int, int]:
    """`{qubit: 'X'|'Y'|'Z'}` -> (x bits, z bits, number of Y) under `index`."""
    px = pz = ny = 0
    for q, p in pauli.items():
        p = p.upper()
        if p == "I":
            continue
        i = index(q)
        if p in ("X", "Y"):
            px ^= 1 << i
        if p in ("Z", "Y"):
            pz ^= 1 << i
        if p == "Y":
            ny += 1
    return px, pz, ny


class Tableau:
    def __init__(self, n: int, *, bell=()):
        self.n = n
        self.width = 2 * n
        self.X = [1 << q for q in range(n)]          # destabilizer q = X_q
        self.Z = [1 << (n + q) for q in range(n)]    # stabilizer q = Z_q
        self.R = 0
        self.V = [0] * (2 * n)
        self.stab_mask = ((1 << n) - 1) << n
        self.destab_mask = (1 << n) - 1
        for s, r in bell:
            # destab s = X_s, stab s = Z_s Z_r, destab r = Z_r, stab r = X_s X_r
            self.Z[r] |= 1 << (n + s)
            self.X[r] &= ~(1 << r)
            self.Z[r] |= 1 << r
            self.Z[r] &= ~(1 << (n + r))
            self.X[s] |= 1 << (n + r)
            self.X[r] |= 1 << (n + r)
        self.nvars = 0
        self.hidden = 0
        #: visible variable -> the record it is
        self.var_record: dict[int, int] = {}
        #: per record: (constant, variables) it equals
        self.records: list[tuple[int, int]] = []
        #: per record: True if it was a fresh random outcome
        self.random: list[bool] = []

    # -- Clifford gates ------------------------------------------------------------------

    def h(self, q):
        x, z = self.X[q], self.Z[q]
        self.R ^= x & z
        self.X[q], self.Z[q] = z, x

    def s(self, q):
        x, z = self.X[q], self.Z[q]
        self.R ^= x & z
        self.Z[q] = z ^ x

    def s_dag(self, q):
        x, z = self.X[q], self.Z[q]
        self.R ^= x & ~z
        self.Z[q] = z ^ x

    def x(self, q):
        self.R ^= self.Z[q]

    def z(self, q):
        self.R ^= self.X[q]

    def y(self, q):
        self.R ^= self.X[q] ^ self.Z[q]

    def cx(self, c, t):
        xc, zc, xt, zt = self.X[c], self.Z[c], self.X[t], self.Z[t]
        self.R ^= xc & zt & ~(xt ^ zc)
        self.X[t] = xt ^ xc
        self.Z[c] = zc ^ zt

    def cz(self, a, b):
        self.h(b)
        self.cx(a, b)
        self.h(b)

    # -- measurement -----------------------------------------------------------------------

    def _new_var(self, hidden: bool) -> int:
        v = self.nvars
        self.nvars += 1
        if hidden:
            self.hidden |= 1 << v
        return v

    def _anti(self, px: int, pz: int) -> int:
        a = 0
        for q in bits(px):
            a ^= self.Z[q]
        for q in bits(pz):
            a ^= self.X[q]
        return a

    def _rowmul(self, M: int, p: int) -> None:
        """Row h := row p · row h for every row h in the mask M (Aaronson-Gottesman rowsum,
        bit-sliced: a two-bit counter per row accumulates the power of i)."""
        bp = 1 << p
        c0 = c1 = 0
        X, Z = self.X, self.Z
        for q in range(self.n):
            xq, zq = X[q], Z[q]
            xp, zp = xq & bp, zq & bp
            if not (xp or zp):
                continue
            xm, zm = xq & M, zq & M
            if xp and zp:                 # Y · P:  g = z - x
                plus, minus = zm & ~xm, xm & ~zm
            elif xp:                      # X · P:  g = z(2x - 1)
                plus, minus = zm & xm, zm & ~xm
            else:                         # Z · P:  g = x(1 - 2z)
                plus, minus = xm & ~zm, xm & zm
            if plus:
                c1 ^= plus & c0
                c0 ^= plus
            if minus:
                c1 ^= minus & ~c0
                c0 ^= minus
            if xp:
                X[q] = xq ^ M
            if zp:
                Z[q] = zq ^ M
        # commuting rows accumulate i^0 or i^2: the sign flips where the counter reads 2
        if self.R & bp:
            self.R ^= M
        self.R ^= c1 & M
        vp = self.V[p]
        if vp:
            for h in bits(M):
                self.V[h] ^= vp

    def _copy_row(self, src: int, dst: int) -> None:
        bs, bd = 1 << src, 1 << dst
        nd = ~bd
        for q in range(self.n):
            x = self.X[q]
            self.X[q] = (x | bd) if x & bs else (x & nd)
            z = self.Z[q]
            self.Z[q] = (z | bd) if z & bs else (z & nd)
        self.R = (self.R | bd) if self.R & bs else (self.R & nd)
        self.V[dst] = self.V[src]

    def _set_row(self, p: int, px: int, pz: int) -> None:
        bp = 1 << p
        nb = ~bp
        for q in range(self.n):
            if self.X[q] & bp:
                self.X[q] &= nb
            if self.Z[q] & bp:
                self.Z[q] &= nb
        for q in bits(px):
            self.X[q] |= bp
        for q in bits(pz):
            self.Z[q] |= bp

    def _product(self, S: int) -> tuple[int, int, int, int]:
        """The product of the rows in S (in row order): (constant sign, variables, x, z)."""
        ycount = cross = resy = 0
        xres = zres = 0
        for q in range(self.n):
            xs = self.X[q] & S
            zs = self.Z[q] & S
            if not (xs or zs):
                continue
            if xs & zs:
                ycount += (xs & zs).bit_count()
            xb = xs.bit_count() & 1
            zb = zs.bit_count() & 1
            if xb:
                xres |= 1 << q
            if zb:
                zres |= 1 << q
            if xb and zb:
                resy += 1
            if xs and zs:
                cross += (xs & _prefix_parity(zs, self.width)).bit_count()
        e = (ycount + 2 * cross - resy) % 4
        if e & 1:
            raise AssertionError("product of stabilizer rows is not Hermitian")
        const = (e >> 1) ^ ((self.R & S).bit_count() & 1)
        var = 0
        for h in bits(S):
            var ^= self.V[h]
        return const, var, xres, zres

    def measure_pauli(self, px: int, pz: int, *, sign: int = 0, hidden: bool = False,
                      record: bool = True):
        """Measure (-1)^sign · P.  Returns (constant, variables, random)."""
        a = self._anti(px, pz)
        stab = a & self.stab_mask
        if stab:
            p = (stab & -stab).bit_length() - 1
            M = a & ~(1 << p)
            if M:
                self._rowmul(M, p)
            self._copy_row(p, p - self.n)
            self._set_row(p, px, pz)
            v = self._new_var(hidden)
            self.R = (self.R | (1 << p)) if sign else (self.R & ~(1 << p))
            self.V[p] = 1 << v
            const, var, rnd = 0, 1 << v, True
        else:
            S = (a & self.destab_mask) << self.n
            const, var, xr, zr = self._product(S)
            if xr != px or zr != pz:
                raise AssertionError("deterministic measurement: product mismatch")
            const ^= sign
            rnd = False
        if not record:
            return const, var, rnd
        if not rnd and var & self.hidden:
            # a recorded outcome that depends on an unrecorded one is itself random: solve
            # for the hidden variable and make the record a fresh visible variable
            h = (var & self.hidden).bit_length() - 1
            v = self._new_var(False)
            repl = var ^ (1 << v)
            for row in range(self.width):
                if (self.V[row] >> h) & 1:
                    self.V[row] ^= repl
                    if const:
                        self.R ^= 1 << row
            self.hidden &= ~(1 << h)
            const, var, rnd = 0, 1 << v, True
        k = len(self.records)
        self.records.append((const, var))
        self.random.append(rnd)
        if rnd:
            self.var_record[var.bit_length() - 1] = k
        return const, var, rnd

    def measure(self, q: int) -> int:
        self.measure_pauli(0, 1 << q)
        return len(self.records) - 1

    def reset(self, q: int) -> None:
        const, var, _ = self.measure_pauli(0, 1 << q, hidden=True, record=False)
        rows = self.Z[q]                  # rows anticommuting with X_q
        if const:
            self.R ^= rows
        if var:
            for h in bits(rows):
                self.V[h] ^= var

    # -- reading -------------------------------------------------------------------------

    def value(self, px: int, pz: int):
        """(constant, variables) that P equals on the current state, or None if P is not in
        the stabilizer group up to sign."""
        a = self._anti(px, pz)
        if a & self.stab_mask:
            return None
        const, var, xr, zr = self._product((a & self.destab_mask) << self.n)
        if xr != px or zr != pz:
            raise AssertionError("value: product mismatch")
        return const, var

    def as_records(self, var: int) -> list[int] | None:
        """The records a visible-variable set stands for; None if a hidden one is in it."""
        if var & self.hidden:
            return None
        return sorted(self.var_record[v] for v in bits(var))

    def record_expr(self, k: int) -> tuple[int, int]:
        """Record k as (constant, record bitset): a random record is itself; a deterministic
        one is the XOR of the random records its value is a function of."""
        const, var = self.records[k]
        if self.random[k]:
            return 0, 1 << k
        rec = 0
        for v in bits(var):
            rec |= 1 << self.var_record[v]
        return const, rec


def simulate(circuit, *, choi: bool = True, stop=None) -> tuple[Tableau, int]:
    """Run a Clifford circuit.  With `choi`, qubit i of the circuit is tableau qubit i and
    its reference is tableau qubit n + i.  Returns the tableau and n."""
    n = circuit.n
    tab = Tableau(2 * n if choi else n, bell=[(q, n + q) for q in range(n)] if choi else ())
    for k, op in enumerate(circuit.ops):
        if stop is not None and k >= stop:
            break
        name, t = op.name, op.targets
        if name == "CX":
            for i in range(0, len(t), 2):
                tab.cx(t[i], t[i + 1])
        elif name == "M":
            for q in t:
                tab.measure(q)
        elif name == "R":
            for q in t:
                tab.reset(q)
        elif name == "H":
            for q in t:
                tab.h(q)
        elif name == "CZ":
            for i in range(0, len(t), 2):
                tab.cz(t[i], t[i + 1])
        elif name == "S":
            for q in t:
                tab.s(q)
        elif name == "S_DAG":
            for q in t:
                tab.s_dag(q)
        elif name == "X":
            for q in t:
                tab.x(q)
        elif name == "Z":
            for q in t:
                tab.z(q)
        elif name == "Y":
            for q in t:
                tab.y(q)
        elif name == "I":
            pass
        elif name == "MPP":
            px = pz = 0
            for q, p in op.pauli:
                if p in ("X", "Y"):
                    px ^= 1 << q
                if p in ("Z", "Y"):
                    pz ^= 1 << q
            tab.measure_pauli(px, pz)
        else:
            raise NotClifford(f"operation {name} is not Clifford: the stabilizer simulation "
                              f"cannot follow it")
    return tab, n
