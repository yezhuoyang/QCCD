"""Synthesis: a logical program on a design, as a timed schedule of gadget ops.

`synthesize(program, lib, leaves)` returns a GIR (GADGETS.md §4, §7): events (one op on one
leaf instance), carries (ions moving along one channel), and the program they discharge.
Only characterized abstracts are read -- op durations, per-ion port crossing times, channel
transits replayed from the conveyor reference -- so the whole machine is scheduled without
replaying a single ion-level program.

The algorithm is deliberately plain and deterministic (§7):

* **flatten** the hierarchy into leaf instances and nets (channels with both ends resolved
  through composite port bindings to leaf ports), and give every resting ion a global id;
* take instructions **in program order**.  A memory's ops start only at its own cycle
  boundaries and never before its previous op ends, which keeps program order per block;
  shared resources (stations, reservoirs, junctions, channels) keep interval calendars and
  are used at the earliest time that fits, so instructions on disjoint blocks run
  concurrently;
* **handshakes**: when op U hands a bundle to op D through a channel of transit `τ`, D
  starts at `max_j(u + out_j + τ − in_j)` -- the earliest start at which every ion is there
  when D takes it in, from both ops' measured per-ion crossing times;
* **routes** are shortest paths over nets and junctions, reserved hop by hop and shifted
  later as a whole when any hop is busy (circuit switching: it cannot deadlock);
* finally every gap in every block's timeline is **refreshed** with `cycle × N`.
"""

from __future__ import annotations

import heapq
import math
from bisect import bisect_left
from dataclasses import dataclass, field

from .library import LeafLibrary
from .logicq import Instruction, LogicalProgram
from .model import Library, Master

__all__ = ["synthesize", "SynthError", "Calendar", "flatten", "Net", "Wire"]

GIR_SCHEMA = "qccd.gir/0.1"


class SynthError(ValueError):
    pass


# --------------------------------------------------------------------------- calendars


class Calendar:
    """Busy intervals of one resource, kept sorted; `earliest` finds the first gap."""

    def __init__(self):
        self.iv: list[tuple[float, float]] = []

    def earliest(self, t: float, dur: float) -> float:
        s = t
        i = max(0, bisect_left(self.iv, (s, s)) - 1)
        while i < len(self.iv):
            a, b = self.iv[i]
            if b <= s:
                i += 1
                continue
            if a >= s + dur:
                break
            s = b
            i += 1
        return s

    def free(self, t0: float, t1: float) -> bool:
        return self.earliest(t0, t1 - t0) == t0

    def reserve(self, t0: float, t1: float) -> None:
        if t1 < t0:
            raise SynthError(f"reserving a negative interval [{t0}, {t1}]")
        i = bisect_left(self.iv, (t0, t1))
        self.iv.insert(i, (t0, t1))

    @property
    def end(self) -> float:
        return max((b for _, b in self.iv), default=0.0)


# --------------------------------------------------------------------------- flattening


def _natural(name: str):
    head = name.rstrip("0123456789")
    tail = name[len(head):]
    return (head, int(tail) if tail else -1)


_ROLE_ORDER = {"data": 0, "ancilla": 1, "messenger": 2, "magic": 3, "any": 4}


def ion_order(leaves: LeafLibrary, master: str) -> list[str]:
    """A master's resting ions in the order global ids are handed out."""
    data = leaves.data[master]
    home, roles = data["home"], data["roles"]
    return sorted(home, key=lambda i: (_ROLE_ORDER.get(roles.get(i, "any"), 9), _natural(i)))


@dataclass
class LeafInst:
    path: str
    master: Master
    x: float
    y: float
    first_id: int
    ids: dict[str, int] = field(default_factory=dict)


@dataclass
class Net:
    path: str
    a: tuple[str, str]
    b: tuple[str, str]
    length: int
    role: str = "any"


@dataclass
class Wire:
    """A classical link: bits from `a` to `b`, `latency_us` later.  No ions, no conveyor."""

    path: str
    a: tuple[str, str]
    b: tuple[str, str]
    latency_us: float = 0.0


def _resolve(lib: Library, master: Master, path: str, ref: str) -> tuple[str, str]:
    inst_name, _, port = ref.partition(".")
    child = lib[master.instance(inst_name).master]
    cpath = f"{path}.{inst_name}" if path else inst_name
    if child.kind == "leaf":
        child.port(port)
        return cpath, port
    return _resolve(lib, child, cpath, child.port(port).bind)


def flatten(lib: Library, leaves: LeafLibrary, *, wires: dict | None = None):
    leaf_insts: dict[str, LeafInst] = {}
    nets: dict[str, Net] = {}
    next_id = 0

    def walk(master: Master, path: str, ox: float, oy: float):
        nonlocal next_id
        for inst in master.instances:
            child = lib[inst.master]
            p = f"{path}.{inst.name}" if path else inst.name
            if child.kind == "leaf":
                order = ion_order(leaves, child.name)
                li = LeafInst(p, child, ox + inst.x, oy + inst.y, next_id,
                              {n: next_id + i for i, n in enumerate(order)})
                next_id += len(order)
                leaf_insts[p] = li
            else:
                walk(child, p, ox + inst.x, oy + inst.y)
        for ch in master.channels:
            a = _resolve(lib, master, path, ch.a)
            b = _resolve(lib, master, path, ch.b)
            if ch.kind == "wire":
                # a classical wire is not a road: no ion ever travels on it, and its cost is
                # one number, its latency (GADGETS.md §10)
                if wires is not None:
                    wires[f"{path}/{ch.name}"] = Wire(f"{path}/{ch.name}", a, b,
                                                      float(ch.latency_us))
                continue
            nets[f"{path}/{ch.name}"] = Net(f"{path}/{ch.name}", a, b, ch.length, ch.role)

    walk(lib[lib.top], "", 0.0, 0.0)
    return leaf_insts, nets, next_id


# --------------------------------------------------------------------------- synthesis


@dataclass
class _Mem:
    free: float = 0.0          # when its last logical op ended
    cycle_us: float = 0.0
    ops: list[tuple[float, float]] = field(default_factory=list)

    def boundary_at_or_after(self, t: float) -> float:
        if t <= self.free:
            return self.free
        k = math.ceil((t - self.free) / self.cycle_us - 1e-9)
        return self.free + k * self.cycle_us

    def boundary_at_or_before(self, t: float) -> float:
        if t <= self.free:
            return self.free
        k = math.floor((t - self.free) / self.cycle_us + 1e-9)
        return self.free + k * self.cycle_us


class _Synth:
    def __init__(self, prog: LogicalProgram, lib: Library, leaves: LeafLibrary,
                 ppm_rounds: int = 1, cycles_between: int = 1, log=None):
        self.prog, self.lib, self.leaves = prog, lib, leaves
        self.ppm_rounds = max(1, int(ppm_rounds))
        #: syndrome-extraction cycles a block runs after every logical op before the next
        #: one may start on it -- the errors an op introduces are caught before another
        #: op spreads them
        self.cycles_between = max(0, int(cycles_between))
        self.log = log or (lambda *a, **k: None)
        self.leaf, self.nets, self.n_ions = flatten(lib, leaves)
        self.cal: dict[str, Calendar] = {p: Calendar() for p in self.leaf}
        self.net_cal: dict[str, Calendar] = {p: Calendar() for p in self.nets}
        self.net_at: dict[tuple[str, str], str] = {}
        for n in self.nets.values():
            self.net_at[n.a] = n.path
            self.net_at[n.b] = n.path
        self.events: list[list] = []
        self.carries: list[list] = []
        self.refused: list[list] = []
        self.block_leaf: dict[str, str] = {}
        self.mem: dict[str, _Mem] = {}
        self.tokens: dict[str, list[list]] = {}     # reservoir path -> [[free_at, local, gid]]
        self._bind_blocks()
        self._graph()

    # -- binding -----------------------------------------------------------------------

    def _bind_blocks(self):
        by_code: dict[str, list[str]] = {}
        for path, li in self.leaf.items():
            if li.master.family == "memory":
                code = li.master.params["code"]["name"]
                by_code.setdefault(code, []).append(path)
                self.mem[path] = _Mem(cycle_us=li.master.ops["cycle"].duration_us)
            if li.master.family == "reservoir":
                self.tokens[path] = [[0.0, n, gid] for n, gid in li.ids.items()]
        taken: dict[str, int] = {}
        for block, decl in self.prog.declarations.items():
            code = decl.code
            family = f"mem_{code.name}"
            pool = [p for paths in by_code.values() for p in paths
                    if self.leaf[p].master.name == family]
            # a block binds to a memory of the same matrices, not merely the same name
            if not pool:
                pool = [p for paths in by_code.values() for p in paths
                        if self.leaf[p].master.params["code"]["n"] == code.n
                        and self.leaf[p].master.params["code"]["k"] == code.k]
            i = taken.get(family, 0)
            if i >= len(pool):
                raise SynthError(f"block {block!r} ([[{code.n},{code.k}]]) has no free memory "
                                 f"in {self.lib.top}: {len(pool)} of that code, all taken")
            self.block_leaf[block] = pool[i]
            taken[family] = i + 1

    def _graph(self):
        self.adj: dict[tuple[str, str], list[tuple[tuple[str, str], float, str, tuple]]] = {}

        def edge(u, v, w, kind, info):
            self.adj.setdefault(u, []).append((v, w, kind, info))

        for n in self.nets.values():
            w = self.leaves.channel_timing(n.length, 1)["transit_us"]
            edge(n.a, n.b, w, "net", (n.path,))
            edge(n.b, n.a, w, "net", (n.path,))
        for path, li in self.leaf.items():
            if li.master.family == "junction":
                d = li.master.ops["pass"].duration_us
                ports = [p.name for p in li.master.ports]
                for a in ports:
                    for b in ports:
                        if a != b:
                            edge((path, a), (path, b), d, "junction", (path, a, b))
        self.nearest_res: dict[str, tuple[str, float]] = {}
        for mem in self.mem:
            dist = self._dijkstra((mem, "net"))
            best = min(((dist[(r, "port")][0], r) for r in self.tokens if (r, "port") in dist),
                       default=None)
            if best is not None:
                self.nearest_res[mem] = (best[1], best[0])

    def _dijkstra(self, src):
        dist = {src: (0.0, None)}
        pq = [(0.0, src)]
        while pq:
            d, u = heapq.heappop(pq)
            if d > dist[u][0]:
                continue
            if u != src and self.leaf.get(u[0]) and self.leaf[u[0]].master.family != "junction":
                continue           # memories and reservoirs are endpoints, not thoroughfares
            for v, w, kind, info in self.adj.get(u, ()):
                nd = d + w
                if v not in dist or nd < dist[v][0]:
                    dist[v] = (nd, (u, kind, info))
                    heapq.heappush(pq, (nd, v))
        return dist

    def _route(self, src, dst) -> list[tuple[str, tuple]]:
        dist = self._dijkstra(src)
        if dst not in dist:
            raise SynthError(f"no transport path from {src[0]}.{src[1]} to {dst[0]}.{dst[1]}")
        hops, v = [], dst
        while dist[v][1] is not None:
            u, kind, info = dist[v][1]
            hops.append((kind, info, u, v))
            v = u
        return list(reversed(hops))

    # -- emitting --------------------------------------------------------------------------

    def event(self, path, op, t0, t1, ins, group="", bind=None, repeat=1, warp=None):
        """Row: `[id, t0, t1, leaf, op, repeat, instruction, group, bind, warp]`.

        `bind` lists the global ids of the op's visiting ions in the leaf's visitor order.
        `warp` maps the op's local clock to the global one where a handshake stretched the
        op: `[[local us, global us], ...]`, piecewise linear; empty means unstretched.
        """
        self.cal[path].reserve(t0, t1)
        self.events.append([len(self.events), round(t0, 3), round(t1, 3), path, op, repeat,
                            ins, group, bind or [],
                            [[round(a, 3), round(b, 3)] for a, b in (warp or [])]])

    def carry(self, net, src, deps, transit, ions, role, ins, group="", takes=None):
        """Ions entering `net` from leaf port `src` at times `deps`, each taking `transit`.

        Row: `[id, net, from leaf, from port, t0, departures - t0, transit, ions, role,
        instruction, group, last taken, taken - t0]`.  `taken` is when the sink takes each
        ion off the far end; between arrival and then the ion waits in the channel, which is
        what G4 counts against the channel's length.
        """
        t0 = min(deps)
        takes = list(takes) if takes else [d + transit for d in deps]
        self.carries.append([len(self.carries), net, src[0], src[1], round(t0, 3),
                             [round(d - t0, 3) for d in deps], round(transit, 3),
                             list(ions), role, ins, group, round(max(takes), 3),
                             [round(k - t0, 3) for k in takes]])

    def refuse(self, ins: Instruction, reason: str):
        self.refused.append([ins.index, reason])

    # -- the route of one messenger ---------------------------------------------------------

    def _fly(self, route, t_depart, gid, ins, group, wait_until=None):
        """Reserve every hop of `route` starting no earlier than `t_depart`; returns arrival.

        The whole route slides later when any hop is busy.  `wait_until(arrival)` may ask
        the messenger to linger in the last channel (a memory not yet at a cycle boundary);
        that channel stays reserved until it leaves.
        """
        t_try = t_depart
        for _ in range(10000):
            t, plan, shift = t_try, [], 0.0
            for kind, info, u, _v in route:
                if kind == "net":
                    d = self.leaves.channel_timing(self.nets[info[0]].length, 1)["transit_us"]
                    cal = self.net_cal[info[0]]
                else:
                    d = self.leaf[info[0]].master.ops["pass"].duration_us
                    cal = self.cal[info[0]]
                s = cal.earliest(t, d)
                if s > t:
                    shift = s - t
                    break
                plan.append((kind, info, u, t, t + d))
                t += d
            if shift:
                t_try += shift
                continue
            leave = wait_until(t) if wait_until else t
            hold = t
            if plan and leave > t:
                kind, info, u, a, b = plan[-1]
                if kind == "net" and not self.net_cal[info[0]].free(a, leave):
                    t_try += 1.0
                    continue
                plan[-1] = (kind, info, u, a, leave)
                hold = leave
            for k, (kind, info, u, a, b) in enumerate(plan):
                if kind == "net":
                    self.net_cal[info[0]].reserve(a, b)
                    last = k == len(plan) - 1
                    transit = self.leaves.channel_timing(self.nets[info[0]].length, 1)["transit_us"]
                    taken = hold if last else a + transit
                    self.carries.append([len(self.carries), info[0], u[0], u[1], round(a, 3),
                                         [0.0], round(transit, 3), [gid], "messenger", ins,
                                         group, round(taken, 3), [round(taken - a, 3)]])
                else:
                    self.event(info[0], "pass", a, b, ins, group, bind=[["i0", gid]])
            return t, leave
        raise SynthError("a messenger route never found a free window")

    # -- instructions ------------------------------------------------------------------------

    def run(self):
        for ins in self.prog.instructions:
            try:
                getattr(self, f"_do_{ins.kind}", self._do_unsupported)(ins)
            except SynthError as exc:
                self.refuse(ins, str(exc))
        self._refresh()
        return self._gir()

    def _do_unsupported(self, ins):
        reasons = {
            "gate1": "a single-logical H/S on a block with k > 1 is LogicQ's PPM gadget with an "
                     "ancilla logical (P5)",
            "gate2": "a single-logical CNOT/CZ between blocks with k > 1 is LogicQ's PPM gadget "
                     "(P5); use transversalCNOTBatch for the block-level CNOT",
            "tcnot": "a single-incidence transversalCNOT acts on one pair only when k = 1; "
                     "not realised in the beta",
        }
        self.refuse(ins, reasons.get(ins.kind, f"no realisation for {ins.kind!r}"))

    def _do_magic(self, ins):
        """`magic T q[i]`: one messenger measures Z̄ᵢ ⊗ Z_T; the T state never travels.

        A reservoir releases a messenger in |0⟩; it collects Z̄ᵢ at the block (`visit.Zi`),
        then Z_T at a factory holding a distilled state (`consume`, which also measures the T
        ion in X), and returns to be read (`accept.Z`).  The factory with the soonest state is
        chosen and `produce` is scheduled on it if none is waiting; the messenger is released
        so that it reaches the factory about when the state is ready.  The X outcome and the
        S̄ correction are classical (GADGETS.md §6.2), drawn as a frame update on the block.
        """
        block, idx = ins.qubits[0]
        code = self.prog.code(block)
        key = self.leaves.ensure_visit(code, idx, "Z")
        path = self.block_leaf[block]
        if path not in self.nearest_res:
            raise SynthError(f"{block}'s memory has no reservoir on its bus")
        facs = [p for p, li in self.leaf.items() if li.master.family == "factory"]
        if not facs:
            raise SynthError(f"{self.lib.top} has no magic-state factory")
        res = self.nearest_res[path][0]
        R = self.leaf[res].master
        rel, acc = R.ops["release.Z"], R.ops["accept.Z"]
        M = self.mem[path]
        V = self.leaf[path].master.ops[key]
        grp = f"i{ins.index}"
        if not hasattr(self, "fac_state"):
            self.fac_state = {f: {"free": 0.0, "ready": None} for f in facs}

        # which factory: the one whose state is ready soonest, counting the trip there
        to_block = self._dijkstra((res, "port")).get((path, "net"), (float("inf"),))[0]
        from_block = self._dijkstra((path, "net"))
        start_est = M.boundary_at_or_after(M.free)
        best = None
        for f in facs:
            if (f, "port") not in from_block:
                continue
            st = self.fac_state[f]
            P = self.leaf[f].master.ops["produce"]
            ready = st["ready"] if st["ready"] is not None else \
                self.cal[f].earliest(st["free"], P.duration_us) + P.duration_us
            arrive = start_est + V.duration_us + from_block[(f, "port")][0]
            cand = max(ready, arrive)
            if best is None or cand < best[0]:
                best = (cand, f, ready)
        if best is None:
            raise SynthError(f"no factory is reachable from {block}'s memory")
        _, fac, _ = best
        F = self.leaf[fac].master
        P, Cn = F.ops["produce"], F.ops["consume"]
        st = self.fac_state[fac]
        if st["ready"] is None:
            ps = self.cal[fac].earliest(st["free"], P.duration_us)
            self.event(fac, "produce", ps, ps + P.duration_us, ins.index, grp)
            st["ready"] = ps + P.duration_us

        token = min(self.tokens[res], key=lambda tk: tk[0])
        lead = to_block + from_block[(fac, "port")][0] + V.duration_us
        hint = max(M.boundary_at_or_after(M.free) - to_block, st["ready"] - lead) \
            - rel.transfer("port", "out").times_us[0]
        t_rel = self.cal[res].earliest(max(token[0], hint, 0.0), rel.duration_us)
        self.event(res, "release.Z", t_rel, t_rel + rel.duration_us, ins.index, grp,
                   bind=[["m0", token[2]]])
        t = t_rel + rel.transfer("port", "out").times_us[0]

        vin = V.transfer("net", "in").times_us[0]
        route = self._route((res, "port"), (path, "net"))
        _, leave = self._fly(route, t, token[2], ins.index, grp,
                             wait_until=lambda a: M.boundary_at_or_after(a - vin) + vin)
        v = leave - vin
        self.event(path, key, v, v + V.duration_us, ins.index, grp, bind=[["m0", token[2]]])
        M.ops.append((v, v + V.duration_us))
        self._settle(M, v + V.duration_us)
        t = v + V.transfer("net", "out").times_us[0]

        cin = Cn.transfer("port", "in").times_us[0]
        route = self._route((path, "net"), (fac, "port"))
        ready = st["ready"]
        _, leave = self._fly(route, t, token[2], ins.index, grp,
                             wait_until=lambda a: self.cal[fac].earliest(
                                 max(a - cin, ready), Cn.duration_us) + cin)
        tc = leave - cin
        self.event(fac, "consume", tc, tc + Cn.duration_us, ins.index, grp,
                   bind=[["m0", token[2]]])
        st["free"], st["ready"] = tc + Cn.duration_us, None
        t = tc + Cn.transfer("port", "out").times_us[0]

        ain = acc.transfer("port", "in").times_us[0]
        route = self._route((fac, "port"), (res, "port"))
        _, leave = self._fly(
            route, t, token[2], ins.index, grp,
            wait_until=lambda a: self.cal[res].earliest(a - ain, acc.duration_us) + ain)
        ta = leave - ain
        self.event(res, "accept.Z", ta, ta + acc.duration_us, ins.index, grp,
                   bind=[["m0", token[2]]])
        token[0] = ta + acc.duration_us
        done = ta + acc.duration_us
        self.events.append([len(self.events), round(done, 3), round(done, 3), path, "frame", 1,
                            ins.index, grp, [["correction", f"S on {block}[{idx}] if X = -1"]], []])

    def _do_pauli(self, ins):
        for block in ins.blocks:
            path = self.block_leaf[block]
            t = self.mem[path].free
            self.events.append([len(self.events), round(t, 3), round(t, 3), path, "frame", 1,
                                ins.index, "", [], []])

    _do_discard = _do_pauli

    def _do_transversal(self, ins):
        block = ins.blocks[0]
        code = self.prog.code(block)
        if not code.transversal_preserves(ins.op):
            raise SynthError(f"transversal {ins.op} does not preserve the stabilizer group of "
                             f"{block} ({code.name}); LogicQ's checkTransversal refuses it too")
        path = self.block_leaf[block]
        m = self.mem[path]
        op = self.leaf[path].master.ops["layer1q"]
        s = m.boundary_at_or_after(m.free)
        self.event(path, "layer1q", s, s + op.duration_us, ins.index, bind=[["gate", ins.op]])
        m.ops.append((s, s + op.duration_us))
        self._settle(m, s + op.duration_us)

    def _do_tcnot_batch(self, ins):
        """Two bundles out, one station, two bundles back -- with elastic handshakes.

        Each handoff is paced (GADGETS.md §7): a source may stall between ions but never runs
        ahead of its verified program, a channel of L sites never holds more than L ions (an
        ion enters only once the one L places ahead has been taken in), and every ion is at
        the far end before the sink takes it.  The station's intake and the memories' absorb
        run at their characterized pace; the memories' emit and the station's exit stretch.
        """
        qc, qt = ins.blocks
        pc, pt = self.block_leaf[qc], self.block_leaf[qt]
        nc, nt = self.net_at.get((pc, "bus")), self.net_at.get((pt, "bus"))
        if not nc or not nt:
            raise SynthError(f"{qc} or {qt} has no data-bundle channel")
        endc = self.nets[nc].b if self.nets[nc].a == (pc, "bus") else self.nets[nc].a
        endt = self.nets[nt].b if self.nets[nt].a == (pt, "bus") else self.nets[nt].a
        if endc[0] != endt[0] or self.leaf[endc[0]].master.family != "station":
            raise SynthError(f"{qc} and {qt} share no transversal-CNOT station in "
                             f"{self.lib.top}; bind them to the two tiles of one pair")
        st = endc[0]
        opname = "cx" if endc[1] == "a" else "xc"
        X = self.leaf[st].master.ops[opname]
        side = {}
        for key, path, net, port in (("c", pc, nc, endc[1]), ("t", pt, nt, endt[1])):
            M = self.leaf[path].master
            side[key] = dict(
                path=path, net=net, port=port, mem=self.mem[path],
                E=M.ops["emit"], A=M.ops["absorb"], L=self.nets[net].length,
                tau=self.leaves.channel_timing(self.nets[net].length, 1)["transit_us"],
                out=M.ops["emit"].transfer("bus", "out").times_us,
                back_in=M.ops["absorb"].transfer("bus", "in").times_us,
                st_in=X.transfer(port, "in").times_us,
                st_out=X.transfer(port, "out").times_us)

        def pace(start, out, tau, sink, inn, L):
            """Departures of a stalling source feeding a fixed-pace sink, and how late."""
            deps, stall = [], 0.0
            for j, o in enumerate(out):
                d = start + o + stall
                if j >= L:
                    d = max(d, sink + inn[j - L])
                stall = d - (start + o)
                deps.append(d)
            late = max(d + tau - (sink + inn[j]) for j, d in enumerate(deps))
            return deps, late

        # forward: the station start, and each emit on a cycle boundary of its own block
        s = max(sd["mem"].boundary_at_or_after(sd["mem"].free)
                + max(o + sd["tau"] - i for o, i in zip(sd["out"], sd["st_in"]))
                for sd in side.values())
        for _ in range(2000):
            s = self.cal[st].earliest(s, X.duration_us)
            late = 0.0
            for sd in side.values():
                M = sd["mem"]
                ideal = s + sd["st_in"][0] - sd["tau"] - sd["out"][0]
                sd["u"] = max(M.boundary_at_or_after(M.free), M.boundary_at_or_before(ideal))
                sd["deps"], lt = pace(sd["u"], sd["out"], sd["tau"], s, sd["st_in"], sd["L"])
                late = max(late, lt)
            if late <= 1e-6:
                break
            s += late
        else:
            raise SynthError("the station never lined up with both blocks' cycle boundaries")

        # back: both bundles leave the station in the same cycles, so one stall serves both
        for sd in side.values():
            emit_end = sd["deps"][-1] + (sd["E"].duration_us - sd["out"][-1])
            sd["v"] = max(emit_end, max(s + o + sd["tau"] - i
                                        for o, i in zip(sd["st_out"], sd["back_in"])))
        n = len(side["c"]["st_out"])
        for _ in range(2000):
            deps, stall = [], 0.0
            for j in range(n):
                d = s + side["c"]["st_out"][j] + stall
                for sd in side.values():
                    if j >= sd["L"]:
                        d = max(d, sd["v"] + sd["back_in"][j - sd["L"]])
                stall = d - (s + side["c"]["st_out"][j])
                deps.append(d)
            worst = 0.0
            for sd in side.values():
                lt = max(d + sd["tau"] - (sd["v"] + sd["back_in"][j])
                         for j, d in enumerate(deps))
                if lt > 1e-6:
                    sd["v"] += lt
                    worst = max(worst, lt)
            if worst <= 1e-6:
                break
        else:
            raise SynthError("the returning bundles never fit their channels")
        exit_stall = deps[-1] - (s + side["c"]["st_out"][-1])
        s_end = s + X.duration_us + exit_stall
        if not self.cal[st].free(s, s_end):
            raise SynthError(f"station {st} is busy while the bundles return")

        grp = f"i{ins.index}"
        ids = {}
        for key, sd in side.items():
            leaf = self.leaf[sd["path"]]
            ids[key] = [leaf.ids[nm] for nm in sd["E"].transfer("bus", "out").ions]
            emit_end = sd["deps"][-1] + (sd["E"].duration_us - sd["out"][-1])
            warp = ([[0.0, sd["u"]]] + [[o, d] for o, d in zip(sd["out"], sd["deps"])]
                    + [[sd["E"].duration_us, emit_end]])
            a0 = sd["deps"][0]
            b0 = sd["v"] + sd["back_in"][-1]
            if not self.net_cal[sd["net"]].free(a0, b0):
                raise SynthError(f"channel {sd['net']} is busy when the bundle needs it")
            self.net_cal[sd["net"]].reserve(a0, b0)
            self.event(sd["path"], "emit", sd["u"], emit_end, ins.index, grp, warp=warp)
            self.carry(sd["net"], (sd["path"], "bus"), sd["deps"], sd["tau"], ids[key], "data",
                       ins.index, grp, takes=[s + i for i in sd["st_in"]])
        ids_a, ids_b = (ids["c"], ids["t"]) if side["c"]["port"] == "a" else (ids["t"], ids["c"])
        st_warp = ([[0.0, s]] + [[o, d] for o, d in zip(side["c"]["st_out"], deps)]
                   + [[X.duration_us, s_end]])
        bind = ([[f"c{j}", g] for j, g in enumerate(ids_a)]
                + [[f"t{j}", g] for j, g in enumerate(ids_b)])
        self.event(st, opname, s, s_end, ins.index, grp, bind=bind, warp=st_warp)
        for key, sd in side.items():
            back = list(reversed(ids[key]))
            self.carry(sd["net"], (st, sd["port"]), deps, sd["tau"], back, "data", ins.index,
                       grp, takes=[sd["v"] + i for i in sd["back_in"]])
            self.event(sd["path"], "absorb", sd["v"], sd["v"] + sd["A"].duration_us, ins.index,
                       grp)
            sd["mem"].ops.append((sd["u"], sd["v"] + sd["A"].duration_us))
            self._settle(sd["mem"], sd["v"] + sd["A"].duration_us)

    def _do_ppm(self, ins):
        bases = {p for _, _, p in ins.target}
        if len(bases) != 1 or bases & {"Y"}:
            raise SynthError("the beta's messenger collects one basis per measurement (all X "
                             "or all Z); mixed and Y products need a basis change on the "
                             "messenger")
        basis = bases.pop()
        per_block: dict[str, list[int]] = {}
        for b, i, _ in ins.target:
            per_block.setdefault(b, []).append(i)
        visits = []
        for b, idx in per_block.items():
            code = self.prog.code(b)
            if len(idx) != 1:
                raise SynthError(f"{len(idx)} logicals of {b} in one product: the beta visits one "
                                 f"logical per block")
            visits.append((b, self.leaves.ensure_visit(code, idx[0], basis)))
        first = self.block_leaf[visits[0][0]]
        if first not in self.nearest_res:
            raise SynthError(f"{visits[0][0]}'s memory has no reservoir on its bus")
        res = self.nearest_res[first][0]
        R = self.leaf[res].master
        rel, acc = R.ops[f"release.{basis}"], R.ops[f"accept.{basis}"]
        for r in range(self.ppm_rounds):
            grp = f"i{ins.index}.r{r}"
            token = min(self.tokens[res], key=lambda tk: tk[0])
            M0 = self.mem[first]
            hint = M0.boundary_at_or_after(M0.free) - self.nearest_res[first][1] \
                - rel.transfer("port", "out").times_us[0]
            t_rel = self.cal[res].earliest(max(token[0], hint, 0.0), rel.duration_us)
            self.event(res, f"release.{basis}", t_rel, t_rel + rel.duration_us, ins.index, grp,
                       bind=[["m0", token[2]]])
            here, t = (res, "port"), t_rel + rel.transfer("port", "out").times_us[0]
            for block, opkey in visits:
                path = self.block_leaf[block]
                M = self.mem[path]
                V = self.leaf[path].master.ops[opkey]
                vin = V.transfer("net", "in").times_us[0]
                route = self._route(here, (path, "net"))
                arrive, leave = self._fly(route, t, token[2], ins.index, grp,
                                          wait_until=lambda a, M=M, vin=vin:
                                          M.boundary_at_or_after(a - vin) + vin)
                v = leave - vin
                self.event(path, opkey, v, v + V.duration_us, ins.index, grp,
                           bind=[["m0", token[2]]])
                M.ops.append((v, v + V.duration_us))
                self._settle(M, v + V.duration_us)
                here, t = (path, "net"), v + V.transfer("net", "out").times_us[0]
            route = self._route(here, (res, "port"))
            ain = acc.transfer("port", "in").times_us[0]
            arrive, leave = self._fly(
                route, t, token[2], ins.index, grp,
                wait_until=lambda a: self.cal[res].earliest(a - ain, acc.duration_us) + ain)
            ta = leave - ain
            self.event(res, f"accept.{basis}", ta, ta + acc.duration_us, ins.index, grp,
                       bind=[["m0", token[2]]])
            token[0] = ta + acc.duration_us

    def _settle(self, m: _Mem, end: float) -> None:
        """A logical op on a block ended at `end`: it may take the next one only after
        `cycles_between` extraction cycles, which `_refresh` then draws in."""
        m.free = end + self.cycles_between * m.cycle_us

    # -- refresh ------------------------------------------------------------------------------

    def _refresh(self):
        horizon = max([e[2] for e in self.events] + [c[11] for c in self.carries] + [0.0])
        self.idle: dict[str, float] = {}
        for path, m in self.mem.items():
            spans = sorted(m.ops)
            t, idle = 0.0, 0.0
            C = m.cycle_us
            for a, b in spans + [(horizon, horizon)]:
                gap = a - t
                n = int(math.floor(gap / C + 1e-9)) if gap > 0 else 0
                if n:
                    self.events.append([len(self.events), round(t, 3), round(t + n * C, 3), path,
                                        "cycle", n, -1, "", [], []])
                idle += max(0.0, gap - n * C)
                t = max(t, b)
            self.idle[path] = idle
        self.horizon = horizon

    # -- output ----------------------------------------------------------------------------------

    def _gir(self) -> dict:
        self.events.sort(key=lambda e: (e[1], e[2], e[3]))
        for i, e in enumerate(self.events):
            e[0] = i
        self.carries.sort(key=lambda c: c[4])
        for i, c in enumerate(self.carries):
            c[0] = i
        return {
            "schema": GIR_SCHEMA,
            "top": self.lib.top,
            "units": "us",
            "makespan_us": round(self.horizon, 3),
            "n_ions": self.n_ions,
            "ion_order": {m: ion_order(self.leaves, m) for m in
                          sorted({li.master.name for li in self.leaf.values()})},
            "leaves": {p: [li.master.name, li.x, li.y, li.first_id] for p, li in self.leaf.items()},
            "nets": {p: [n.a[0], n.a[1], n.b[0], n.b[1], n.length] for p, n in self.nets.items()},
            "blocks": {b: {"leaf": p, "code": self.prog.code(b).name, "n": self.prog.code(b).n,
                           "k": self.prog.code(b).k} for b, p in self.block_leaf.items()},
            "program": {"name": self.prog.name, "source": self.prog.source,
                        "instructions": [i.to_json() for i in self.prog.instructions]},
            "ppm_rounds": self.ppm_rounds,
            "cycles_between": self.cycles_between,
            "events": self.events,
            "carries": self.carries,
            "refused": self.refused,
            "idle_us": {p: round(v, 3) for p, v in self.idle.items()},
            "channels": self.leaves.channel_references(),
        }


def synthesize(prog: LogicalProgram, lib: Library, leaves: LeafLibrary, *,
               ppm_rounds: int = 1, cycles_between: int = 1, log=None) -> dict:
    return _Synth(prog, lib, leaves, ppm_rounds=ppm_rounds, cycles_between=cycles_between,
                  log=log).run()
