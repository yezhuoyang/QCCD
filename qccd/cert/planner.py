"""This project's router, as an extension of QCCDCert: it proposes, QCCDCert checks.

QCCDCert's transport search moves the operands of the next pulse; on a machine whose gate
zones hold two ions (a ring with two docks, say) an operand can reach its zone only after a
bystander has made room, and the search gives up.  QCCDCert's answer is its routing-proposal
stage (paper, Section 4; Lean `RoutePlan`): an external router proposes an initial placement
and, for every pulse/control token, the transports to perform before it -- and **nothing in a
proposal is trusted**: every proposed transport is admitted only with a proof of its core
legality at the placement reached so far, every rule is checked on every frame as before, and
the result is the same routed program the search would produce, so Theorem
`verified_compilation` covers it unchanged.

That is the shape every extension of the compiler takes here: this module is a heuristic, and
a wrong proposal is a refused compilation, never wrong target code.

The proposal is greedy, token by token: for a token whose operands are not where a capable
calibration is, pick the capable site nearest to them, make room there by moving bystanders
to the nearest site with space, then bring each operand along a shortest route that is free
now (its transit sites empty, its destination with room).  Single-ion transports only; the
parallel schedule of QCCDCert overlaps them later where they share no resource.
"""

from __future__ import annotations

import json
from collections import deque
from pathlib import Path

from .toolchain import Toolchain, find


class NoPlan(RuntimeError):
    """The greedy proposal found no way to serve a token."""


def _wave_key(w: dict) -> str:
    return json.dumps(w, sort_keys=True, separators=(",", ":"))


class Planner:
    def __init__(self, hardware: dict, rest: list[int]):
        dev = hardware["device"]
        base = dev["base"]
        geo = base["geometry"]
        self.cap = [s["capacity"] for s in geo["sites"]]
        self.paths = geo["paths"]
        self.mandatory = {b["channel"] for b in geo.get("broadcasts", []) if b.get("mandatory")}
        self.rest = set(rest)
        # single-ion moves only: paths on a channel without a mandatory broadcast
        self.out: dict[int, list[int]] = {}
        for i, p in enumerate(self.paths):
            if p["channel"] not in self.mandatory:
                self.out.setdefault(p["src"], []).append(i)
        self.cal_sites: dict[str, set[int]] = {}
        for c in base["calibrations"]:
            self.cal_sites.setdefault(_wave_key(c["waveform"]), set()).add(c["site"])
        self.read_sites = {r["site"] for r in dev.get("readouts", [])}
        self.pump_sites = {r["site"] for r in dev.get("pumps", [])}
        self.n_sites = len(self.cap)

    # ---------------------------------------------------------------- what a token needs
    def need(self, tok: dict) -> tuple[list[int], set[int]] | None:
        kind, b = next(iter(tok.items()))
        if kind == "pulse":
            pk, pb = next(iter(b["operation"]["pulse"].items()))
            wave = {pk: {k: v for k, v in pb.items() if k in ("theta", "phi")}}
            ions = [pb["ion"]] if pk == "r" else [pb["control"], pb["target"]]
            return ions, set(self.cal_sites.get(_wave_key(wave), ()))
        if kind == "measure":
            return [b["ion"]], set(self.read_sites)
        if kind == "reset":
            return [b["ion"]], set(self.pump_sites)
        return None  # latch, barrier: no placement requirement

    # ---------------------------------------------------------------- moves
    def legal(self, pos: list[int], ion: int, path: int) -> bool:
        p = self.paths[path]
        if pos[ion] != p["src"] or p["dst"] in self.rest:
            return False
        if any(v in pos for v in p["via"]):              # a transit site must be empty
            return False
        return pos.count(p["dst"]) + 1 <= self.cap[p["dst"]]

    def route(self, pos: list[int], ion: int, goal: set[int], avoid: set[int] = frozenset()) -> list[int] | None:
        """Shortest sequence of legal single moves of `ion` (others fixed) ending in `goal`."""
        start = pos[ion]
        if start in goal:
            return []
        prev = {start: None}
        q = deque([start])
        while q:
            s = q.popleft()
            for i in self.out.get(s, []):
                p = self.paths[i]
                d = p["dst"]
                if d in prev or d in self.rest or d in avoid and d not in goal:
                    continue
                trial = list(pos)
                trial[ion] = s
                if not self.legal(trial, ion, i):
                    continue
                prev[d] = (s, i)
                if d in goal:
                    out = []
                    while prev[d] is not None:
                        s0, i0 = prev[d]
                        out.append(i0)
                        d = s0
                    return out[::-1]
                q.append(d)
        return None

    def distance(self, src: int) -> dict[int, int]:
        dist = {src: 0}
        q = deque([src])
        while q:
            s = q.popleft()
            for i in self.out.get(s, []):
                d = self.paths[i]["dst"]
                if d not in dist and d not in self.rest:
                    dist[d] = dist[s] + 1
                    q.append(d)
        return dist

    # ---------------------------------------------------------------- the proposal
    def initial(self, qubits: int, tokens: list[dict]) -> list[int]:
        """Spread the ions over the sites with room, gate-capable sites first, in the order
        the ions are first used, never on a site no ion may stand on."""
        order = []
        for t in tokens:
            nd = self.need(t)
            for q in (nd[0] if nd else []):
                if q not in order:
                    order.append(q)
        order += [q for q in range(qubits) if q not in order]
        capable = set().union(*self.cal_sites.values()) if self.cal_sites else set()
        sites = sorted((s for s in range(self.n_sites) if s not in self.rest and self.cap[s] > 0),
                       key=lambda s: (s not in capable, s))
        pos = [-1] * qubits
        used = {s: 0 for s in sites}
        for q in order:
            # one ion per site while there is room, then fill sites up to their capacity
            s = next((x for x in sites if used[x] == 0), None)
            if s is None:
                s = next((x for x in sites if used[x] < self.cap[x]), None)
            if s is None:
                raise NoPlan(f"{qubits} ions do not fit on this machine")
            pos[q] = s
            used[s] += 1
        return pos

    def plan(self, qubits: int, tokens: list[dict]) -> dict:
        pos = self.initial(qubits, tokens)
        init = list(pos)
        steps: list[list[list[dict]]] = []
        for n, tok in enumerate(tokens):
            events: list[list[dict]] = []

            def move(ion: int, seq: list[int]) -> None:
                for i in seq:
                    events.append([{"ion": ion, "path": i}])
                    pos[ion] = self.paths[i]["dst"]

            nd = self.need(tok)
            if nd:
                ions, sites = nd
                if not sites:
                    raise NoPlan(f"token {n}: no calibration serves it on this machine")
                if not (len({pos[q] for q in ions}) == 1 and pos[ions[0]] in sites):
                    dists = {q: self.distance(pos[q]) for q in ions}
                    ranked = sorted(sites, key=lambda s: (sum(dists[q].get(s, 10 ** 6) for q in ions), s))
                    done = False
                    for t in ranked:
                        if any(t not in dists[q] for q in ions) or self.cap[t] < len(ions):
                            continue
                        trial = list(pos)
                        saved = len(events)
                        ok = True
                        # make room: bystanders leave t for the nearest site with space
                        while sum(1 for q in range(qubits) if pos[q] == t and q not in ions) > self.cap[t] - len(ions):
                            by = next(q for q in range(qubits) if pos[q] == t and q not in ions)
                            free = {s for s in range(self.n_sites) if s != t and s not in self.rest
                                    and pos.count(s) < self.cap[s]}
                            r = self.route(pos, by, free, avoid={t})
                            if r is None:
                                ok = False
                                break
                            move(by, r)
                        for q in ions:
                            if not ok:
                                break
                            r = self.route(pos, q, {t})
                            if r is None:
                                # one blocker on the way: move it aside and try again
                                ok = False
                                break
                            move(q, r)
                        if ok:
                            done = True
                            break
                        del events[saved:]
                        pos[:] = trial
                    if not done:
                        raise NoPlan(f"token {n}: no route brings ions {ions} together at a capable site")
            steps.append(events)
        return {"initial": init, "transports": steps}


def propose(hardware: dict, rest: list[int], qasm_path: str | Path, tc: Toolchain | None = None,
            profile: str = "qiskit") -> dict:
    tc = tc or find()
    out = tc.run("qccd-dynamic", "pulses", Path(qasm_path).resolve(), "--qasm-profile", profile)
    prog = json.loads(out)["pulses"]
    return Planner(hardware, rest).plan(prog["qubits"], prog["code"])
