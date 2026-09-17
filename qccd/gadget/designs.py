"""Composite gadgets, built from characterized leaves.  GADGETS.md §5.2.

    tile_<code>     mem + res4 + xjunc: one code block, its messengers, its tap on the bus
    pair_<code>     two tiles and the transversal-CNOT station between their data ports
    row_<code>_<P>  P pairs, their messenger buses joined end to end
    proc_<code>_<R>x<P>
                    R rows, joined at their west ends by a spine of junctions, each row
                    ending in a 15-to-1 T factory

Everything spatial is derived.  A leaf's footprint is its device's bounding box; a port
sits on its device node; a composite's port sits where the child port it binds to sits.
Channel routes are Manhattan polylines between the two port nodes, and a channel's
length (the sites a conveyor needs) is the route length less one, so moving a tile further
away makes every carry to it slower in the schedule, by replay, not by assertion.
"""

from __future__ import annotations

from .codes import CSSCode
from .library import LeafLibrary
from .model import Channel, Instance, Library, Master, Port

__all__ = ["Designer", "processor", "load_design"]


class Designer:
    """Accumulates masters into a `Library`, computing geometry as it goes."""

    def __init__(self, leaves: LeafLibrary):
        self.leaves = leaves
        self.lib = Library()

    # -- leaves ------------------------------------------------------------------

    def leaf(self, master: Master) -> Master:
        if master.name not in self.lib:
            nodes = self.leaves.data[master.name]["device"]["nodes"]
            xs = [n["pos"][0] for n in nodes]
            ys = [n["pos"][1] for n in nodes]
            master.bbox = [min(xs), min(ys), max(xs), max(ys)]
            self.lib.add(master)
        return self.lib[master.name]

    def _node_pos(self, master: Master, node: str) -> tuple[float, float]:
        for n in self.leaves.data[master.name]["device"]["nodes"]:
            if n["id"] == node:
                return float(n["pos"][0]), float(n["pos"][1])
        raise KeyError(f"{master.name} has no node {node!r}")

    def port_point(self, master: Master, port: str) -> tuple[float, float]:
        """Where `port` sits in `master`'s own frame."""
        p = master.port(port)
        if master.kind == "leaf":
            if p.node is None:
                # a classical port has no device node: nothing is shuttled through it, so it
                # sits on the footprint's edge, where a wire is soldered on
                x0, y0, x1, y1 = master.bbox or [0.0, 0.0, 0.0, 0.0]
                at = p.at
                return {"E": (x1, y0 + (y1 - y0) * at), "W": (x0, y0 + (y1 - y0) * at),
                        "S": (x0 + (x1 - x0) * at, y1),
                        "N": (x0 + (x1 - x0) * at, y0)}[p.side]
            return self._node_pos(master, p.node)
        inst_name, _, child_port = p.bind.partition(".")
        inst = master.instance(inst_name)
        x, y = self.port_point(self.lib[inst.master], child_port)
        return x + inst.x, y + inst.y

    # -- composites ----------------------------------------------------------------

    def composite(self, name: str, family: str, title: str, doc: str,
                  instances: list[Instance], links: list[tuple], ports: list[Port],
                  params: dict | None = None) -> Master:
        """`links` are `(name, "inst.port", "inst.port")` or with a 4th item: via points.

        A link may instead be a dict `{"name", "a", "b", "via", "kind", "latency_us"}`, which
        is how a classical **wire** is declared: `kind="wire"` carries bits with a latency
        and no ions, so no conveyor is ever built for it."""
        master = Master(name=name, kind="composite", family=family, title=title, doc=doc,
                        instances=instances, ports=ports, params=dict(params or {}))
        box = [float("inf"), float("inf"), float("-inf"), float("-inf")]

        def grow(x, y):
            box[0], box[1] = min(box[0], x), min(box[1], y)
            box[2], box[3] = max(box[2], x), max(box[3], y)

        for inst in instances:
            child = self.lib[inst.master]
            x0, y0, x1, y1 = child.bbox
            grow(x0 + inst.x, y0 + inst.y)
            grow(x1 + inst.x, y1 + inst.y)
        for link in links:
            if isinstance(link, dict):
                cname, a, b = link["name"], link["a"], link["b"]
                via = list(link.get("via") or [])
                kind, latency = link.get("kind", "road"), float(link.get("latency_us", 0.0))
            else:
                cname, a, b = link[:3]
                via = list(link[3]) if len(link) > 3 else []
                kind, latency = "road", 0.0
            pa = self._ref_point(master, a)
            pb = self._ref_point(master, b)
            pts = [list(pa)]
            for v in via:
                pts.append([float(v[0]), float(v[1])])
            if not via:
                if pa[0] != pb[0] and pa[1] != pb[1]:
                    pts.append([pb[0], pa[1]])
            pts.append(list(pb))
            route = sum(abs(p[0] - q[0]) + abs(p[1] - q[1]) for p, q in zip(pts, pts[1:]))
            for p in pts:
                grow(*p)
            master.channels.append(Channel(name=cname, a=a, b=b,
                                           length=max(1, int(round(route)) - 1),
                                           points=pts, kind=kind, latency_us=latency))
        master.bbox = box
        master.size = [box[2] - box[0], box[3] - box[1]]
        master.inventory = {}
        for inst in instances:
            for role, k in self.lib[inst.master].inventory.items():
                master.inventory[role] = master.inventory.get(role, 0) + k
        master.capacity = sum(self.lib[i.master].capacity for i in instances)
        self.lib.add(master)
        return master

    def _ref_point(self, master: Master, ref: str) -> tuple[float, float]:
        inst_name, _, port = ref.partition(".")
        inst = master.instance(inst_name)
        x, y = self.port_point(self.lib[inst.master], port)
        return x + inst.x, y + inst.y


# ------------------------------------------------------------------------ the design


def tile(d: Designer, code: CSSCode) -> Master:
    name = f"tile_{code.name}"
    if name in d.lib:
        return d.lib[name]
    mem = d.leaf(d.leaves.memory(code))
    d.leaf(d.leaves.reservoir(4))
    d.leaf(d.leaves.junction())
    half = code.n // 2
    return d.composite(
        name, "tile", f"{code.name} tile",
        "one code block, four messengers, and its tap on the messenger bus",
        instances=[Instance("mem", mem.name, 0.0, 0.0),
                   Instance("jx", "xjunc", float(half - 1), 7.0),
                   Instance("res", "res4", float(half + 1), 9.0)],
        links=[("net", "mem.net", "jx.n"), ("spare", "res.port", "jx.s")],
        ports=[Port("bus", "inout", "data", width=code.n, side="N", at=0.0, order="ring",
                    bind="mem.bus", code=code.name),
               Port("west", "inout", "any", side="W", at=0.8, bind="jx.w"),
               Port("east", "inout", "any", side="E", at=0.8, bind="jx.e")],
        params={"code": code.name})


def pair(d: Designer, code: CSSCode) -> Master:
    name = f"pair_{code.name}"
    if name in d.lib:
        return d.lib[name]
    t = tile(d, code)
    st = d.leaf(d.leaves.station(code))
    half = code.n // 2
    pitch = float(half + 10)
    return d.composite(
        name, "pair", f"{code.name} pair",
        "two tiles whose data bundles meet in one transversal-CNOT station",
        instances=[Instance("t0", t.name, 0.0, 10.0),
                   Instance("t1", t.name, pitch, 10.0),
                   Instance("st", st.name, 2.0, 0.0)],
        links=[("bus_a", "t0.bus", "st.a", [(0.0, 0.0)]),
               ("bus_b", "t1.bus", "st.b", [(pitch, 5.0), (1.0, 5.0), (1.0, 2.0)]),
               ("mid", "t0.east", "t1.west")],
        ports=[Port("west", "inout", "any", side="W", at=0.8, bind="t0.west"),
               Port("east", "inout", "any", side="E", at=0.8, bind="t1.east")],
        params={"code": code.name})


def row(d: Designer, code: CSSCode, pairs: int) -> Master:
    name = f"row_{code.name}_{pairs}"
    if name in d.lib:
        return d.lib[name]
    p = pair(d, code)
    pitch = float(p.bbox[2] - p.bbox[0] + 8)
    insts = [Instance(f"p{i}", p.name, i * pitch, 0.0) for i in range(pairs)]
    links = [(f"bus{i}", f"p{i}.east", f"p{i + 1}.west") for i in range(pairs - 1)]
    return d.composite(
        name, "row", f"{code.name} row of {pairs} pairs",
        "pairs side by side, their messenger buses joined end to end",
        instances=insts, links=links,
        ports=[Port("west", "inout", "any", side="W", at=0.8, bind="p0.west"),
               Port("east", "inout", "any", side="E", at=0.8, bind=f"p{pairs - 1}.east")],
        params={"code": code.name, "pairs": pairs})


def processor(leaves: LeafLibrary, code: CSSCode, rows: int, pairs: int) -> Library:
    """The top: `rows` rows of `pairs` pairs, a spine of junctions down the west side, and
    a T factory at the east end of every row."""
    d = Designer(leaves)
    r = row(d, code, pairs)
    d.leaf(leaves.junction())
    name = f"proc_{code.name}_{rows}x{pairs}"
    fac = d.leaf(leaves.factory())
    rpitch = float(r.bbox[3] - r.bbox[1] + 8)
    west_y = d.port_point(r, "west")[1]
    east = d.port_point(r, "east")
    insts, links = [], []
    for j in range(rows):
        insts.append(Instance(f"r{j}", r.name, 12.0, j * rpitch))
        insts.append(Instance(f"sp{j}", "xjunc", 0.0, j * rpitch + west_y))
        insts.append(Instance(f"fac{j}", fac.name, 12.0 + east[0] + 6.0, j * rpitch + east[1] + 3.0))
        links.append((f"tap{j}", f"sp{j}.e", f"r{j}.west"))
        links.append((f"magic{j}", f"r{j}.east", f"fac{j}.port"))
        if j:
            links.append((f"spine{j}", f"sp{j - 1}.s", f"sp{j}.n"))
    d.composite(
        name, "processor", f"{code.name} processor, {rows}x{pairs} pairs",
        f"{rows * pairs * 2} code blocks of {code.name}; transversal CNOT inside each pair, "
        f"messenger PPMs between any blocks, a 15-to-1 T factory on every row",
        instances=insts, links=links, ports=[],
        params={"code": code.name, "rows": rows, "pairs": pairs,
                "blocks": rows * pairs * 2})
    d.lib.top = name
    return d.lib


def load_design(path_or_doc, leaves: LeafLibrary, code: CSSCode) -> Library:
    """A design exported by the page's editor, re-derived here (GADGETS.md §10, "Edit").

    Nothing spatial or characterized is taken on trust from the file.  Leaf masters are
    looked up by name and characterized afresh for `code`; every composite is rebuilt
    through `Designer.composite` in dependency order, so ports, bounding boxes and channel
    lengths are recomputed from the leaf devices (a channel keeps only its via points).  A
    master the file names that is neither a known leaf nor defined in the file is refused.
    """
    import json
    from pathlib import Path

    doc = path_or_doc if isinstance(path_or_doc, dict) else \
        json.loads(Path(path_or_doc).read_text(encoding="utf-8"))
    lib = Library.from_json(doc)
    d = Designer(leaves)
    known = {
        f"mem_{code.name}": lambda: leaves.memory(code),
        f"tcx{code.n}": lambda: leaves.station(code),
        "res4": lambda: leaves.reservoir(4),
        "xjunc": leaves.junction,
        "fac_t15": leaves.factory,
    }
    for name, m in lib.masters.items():
        if m.kind == "leaf":
            if name not in known:
                raise ValueError(f"the design names leaf master {name!r}, which the library "
                                 f"cannot build for {code.name} (have: {', '.join(known)})")
            d.leaf(known[name]())
    done: set[str] = set(d.lib.masters)

    def build(name, stack=()):
        if name in done:
            return
        if name in stack:
            raise ValueError(f"master {name!r} contains itself: {' -> '.join(stack + (name,))}")
        if name not in lib.masters:
            raise ValueError(f"the design uses master {name!r} but does not define it")
        m = lib.masters[name]
        for inst in m.instances:
            build(inst.master, stack + (name,))
        links = [(c.name, c.a, c.b, [tuple(p) for p in c.points[1:-1]]) if len(c.points) > 2
                 else (c.name, c.a, c.b) for c in m.channels]
        d.composite(name, m.family, m.title, m.doc, instances=m.instances, links=links,
                    ports=m.ports, params=m.params)
        done.add(name)

    top = doc.get("top")
    if not top or top not in lib.masters:
        raise ValueError("the design names no top master")
    build(top)
    d.lib.top = top
    problems = d.lib.structure_violations()
    if problems:
        raise ValueError("the design fails G1:\n  " + "\n  ".join(problems[:20]))
    return d.lib
