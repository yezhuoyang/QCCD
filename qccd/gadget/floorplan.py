"""Floorplans: where every place of a machine stands, and how roads and wires join them.

A floorplan is a text file (`*.floor`), one item per line, `#` to the end of a line is a
comment (docs/ARCHITECTURE.md §2):

    floorplan town_d3 "A town of verified places"
    code surface_d3

    region supply                        # every place below belongs to this region
    place depot depot9 at 0 11           # name, master, and the footprint's top-left corner
    region compute
    place prep prep_d3 at 14 10
    ...
    junction j0_0 at 0 0                 # a junction (junc9): four arms n, e, s, w
    road j0_0.s -- depot.out as door_depot_out
    road j0_0 -- j0_1                    # a bare junction: the arm facing the road is used
    road prep.out -- j1_2 via 25 20      # a route: through these corners, in order
    wire prep.syn -> decoder.syn latency 5 via 17.85 62, 2 62
    wires auto bus 62                    # every classical port to the control places

* A **place** is any verified place master (`prep_d3`, `se_d3`, `tcx_d3`, `ls_d3`,
  `read_d3`, `zone_d3`, `mem_d3`, `msf15`, `inj_d3`, `depot9`, `dec_d3`, `arch1`) or its
  family word (`prep`, `se`, `operation`, `surgery`, `readout`, `zone`, `memory`,
  `factory`, `injection`, `depot`, `decoder`, `archive`).
* A **region** names what the places after it are for: `memory`, `compute`, `factory`,
  `supply`, `control`; junctions are in `traffic`.  Which families may stand in which
  region is a rule (F9), not a convention.
* A **road** is a conveyor: its length in sites is its route length less one, the same
  measure every composite uses (`designs.Designer`).  Without `via`, a road between two
  points not on one line takes one corner, horizontal first.
* A **wire** carries bits, never ions, with one cost: its latency in microseconds.

`load()` turns the text into the `Library` composite the scheduler runs on (the same object
`town.town()` builds), with its regions; `write()` turns a composite back into text.  The
town of GADGETS.md §9 is `examples/gadgets/floorplans/town.floor`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .designs import Designer
from .library import LeafLibrary
from .model import Instance, Library

__all__ = ["Floorplan", "FloorplanError", "parse_floorplan", "load", "load_file", "write",
           "REGIONS", "HOME_REGION", "MASTER_OF", "FLOORPLANS_DIR"]

FLOORPLANS_DIR = Path(__file__).resolve().parents[2] / "examples" / "gadgets" / "floorplans"

#: the region kinds, and what each is for
REGIONS = {
    "memory": "logical zones and memories: where blocks rest between operations (a memory "
              "corrects what it holds)",
    "compute": "the places that act on blocks: preparation, syndrome extraction, transversal "
               "gates, lattice surgery, readout",
    "factory": "magic-state factories and injection",
    "supply": "ion depots: fresh ions in, used ions out",
    "control": "the decoder and the classical memory",
    "traffic": "junctions: where roads meet",
}

#: the region each place family belongs in (rule F9)
HOME_REGION = {
    "zone": "memory", "memory": "memory", "prep": "compute", "se": "compute", "operation": "compute",
    "surgery": "compute", "readout": "compute", "factory": "factory", "injection": "factory",
    "depot": "supply", "decoder": "control", "archive": "control", "road": "traffic",
}

#: a family word, or a master name, -> the master
MASTER_OF = {
    "prep": "prep_d3", "se": "se_d3", "zone": "zone_d3", "memory": "mem_d3", "operation": "tcx_d3",
    "surgery": "ls_d3", "readout": "read_d3", "factory": "msf15", "injection": "inj_d3",
    "depot": "depot9", "decoder": "dec_d3", "archive": "arch1", "junction": "junc9",
}
for _m in list(MASTER_OF.values()):
    MASTER_OF[_m] = _m

JUNCTION = "junc9"


class FloorplanError(ValueError):
    def __init__(self, line: int, text: str, reason: str):
        super().__init__(f"line {line}: {reason}" + (f": {text.strip()}" if text.strip() else ""))
        self.line, self.text, self.reason = line, text, reason


@dataclass
class _Road:
    a: str
    b: str
    via: list = field(default_factory=list)
    name: str | None = None
    line: int = 0


@dataclass
class _Wire:
    a: str
    b: str
    latency: float | None = None
    via: list = field(default_factory=list)
    name: str | None = None
    line: int = 0


@dataclass
class Floorplan:
    """A floorplan as written: nothing derived yet."""

    name: str
    title: str = ""
    code: str = "surface_d3"
    #: (name, master, x, y, region, line): x, y is the footprint's top-left corner
    places: list = field(default_factory=list)
    #: (name, x, y, region, line): x, y is the junction's centre
    junctions: list = field(default_factory=list)
    roads: list = field(default_factory=list)
    wires: list = field(default_factory=list)
    auto_wires: bool = False
    bus_y: float | None = None
    source: str = ""

    @property
    def regions(self) -> dict[str, str]:
        out = {p[0]: p[4] for p in self.places}
        out.update({j[0]: j[3] for j in self.junctions})
        return out


_NUM = r"-?\d+(?:\.\d+)?"


def _points(text: str, lineno: int, raw: str) -> list[list[float]]:
    out = []
    for part in [p.strip() for p in text.split(",") if p.strip()]:
        xy = part.split()
        if len(xy) != 2 or not all(re.fullmatch(_NUM, v) for v in xy):
            raise FloorplanError(lineno, raw, f"a point is two numbers, not {part!r}")
        out.append([float(xy[0]), float(xy[1])])
    return out


def parse_floorplan(source: str, name: str = "floorplan") -> Floorplan:
    fp = Floorplan(name=name, source=source)
    region = None
    seen: set[str] = set()
    for lineno, raw in enumerate(source.splitlines(), start=1):
        text = raw.split("#", 1)[0].strip()
        if not text:
            continue
        word, _, rest = text.partition(" ")
        rest = rest.strip()
        if word == "floorplan":
            m = re.fullmatch(r'(\w+)(?:\s+"([^"]*)")?', rest)
            if not m:
                raise FloorplanError(lineno, raw, 'write: floorplan NAME "title"')
            fp.name, fp.title = m.group(1), m.group(2) or ""
        elif word == "code":
            fp.code = rest
        elif word == "region":
            m = re.fullmatch(r'(\w+)(?:\s+(\w+))?(?:\s+"[^"]*")?', rest)
            if not m or m.group(1) not in REGIONS:
                raise FloorplanError(lineno, raw, f"a region is one of {', '.join(REGIONS)}")
            region = m.group(1)
        elif word == "place":
            m = re.fullmatch(rf"(\w+)\s+(\w+)\s+at\s+({_NUM})\s+({_NUM})", rest)
            if not m:
                raise FloorplanError(lineno, raw, "write: place NAME MASTER at X Y")
            nm, master = m.group(1), m.group(2)
            if master not in MASTER_OF or MASTER_OF[master] == JUNCTION:
                raise FloorplanError(lineno, raw, f"{master!r} is not a place of the verified "
                                     f"library")
            if region is None:
                raise FloorplanError(lineno, raw, "a place stands in a region: write "
                                     "`region KIND` above it")
            if nm in seen:
                raise FloorplanError(lineno, raw, f"{nm} is already placed")
            seen.add(nm)
            fp.places.append((nm, MASTER_OF[master], float(m.group(3)), float(m.group(4)),
                              region, lineno))
        elif word == "junction":
            m = re.fullmatch(rf"(\w+)\s+at\s+({_NUM})\s+({_NUM})", rest)
            if not m:
                raise FloorplanError(lineno, raw, "write: junction NAME at X Y")
            if m.group(1) in seen:
                raise FloorplanError(lineno, raw, f"{m.group(1)} is already placed")
            seen.add(m.group(1))
            fp.junctions.append((m.group(1), float(m.group(2)), float(m.group(3)), "traffic",
                                 lineno))
        elif word == "road":
            m = re.fullmatch(r"([\w.]+)\s*--\s*([\w.]+)(?:\s+via\s+(.+?))?(?:\s+as\s+(\w+))?",
                             rest)
            if not m:
                raise FloorplanError(lineno, raw, "write: road A -- B [via X Y, X Y] [as NAME]")
            fp.roads.append(_Road(m.group(1), m.group(2),
                                  _points(m.group(3), lineno, raw) if m.group(3) else [],
                                  m.group(4), lineno))
        elif word == "wire":
            m = re.fullmatch(rf"([\w.]+)\s*->\s*([\w.]+)(?:\s+latency\s+({_NUM}))?"
                             rf"(?:\s+via\s+(.+?))?(?:\s+as\s+(\w+))?", rest)
            if not m:
                raise FloorplanError(lineno, raw, "write: wire A.port -> B.port [latency US] "
                                     "[via X Y, X Y] [as NAME]")
            fp.wires.append(_Wire(m.group(1), m.group(2),
                                  float(m.group(3)) if m.group(3) else None,
                                  _points(m.group(4), lineno, raw) if m.group(4) else [],
                                  m.group(5), lineno))
        elif word == "wires":
            m = re.fullmatch(rf"auto(?:\s+bus\s+({_NUM}))?", rest)
            if not m:
                raise FloorplanError(lineno, raw, "write: wires auto [bus Y]")
            fp.auto_wires = True
            fp.bus_y = float(m.group(1)) if m.group(1) else None
        else:
            raise FloorplanError(lineno, raw, f"unknown item {word!r}: floorplan, code, region, "
                                 f"place, junction, road, wire, wires")
    if not fp.places:
        raise FloorplanError(0, "", "a floorplan places at least one place")
    return fp


def _arm(dx: float, dy: float) -> str:
    """The junction arm that faces a direction (+y is south, as drawn)."""
    if abs(dy) >= abs(dx):
        return "s" if dy > 0 else "n"
    return "e" if dx > 0 else "w"


def load(fp: Floorplan | str, leaves: LeafLibrary, *, name: str | None = None) -> Library:
    """The floorplan as the `Library` composite the scheduler runs on."""
    if isinstance(fp, str):
        fp = parse_floorplan(fp, name or "floorplan")
    d = Designer(leaves)
    masters = {}
    for nm, master, *_ in fp.places:
        masters[nm] = d.leaf(leaves.place(master))
    jm = d.leaf(leaves.place(JUNCTION))
    instances = []
    for nm, master, x, y, _region, _line in fp.places:
        m = d.lib[master]
        x0, y0, _, _ = m.bbox
        instances.append(Instance(nm, master, x - x0, y - y0))
    for nm, x, y, _region, _line in fp.junctions:
        instances.append(Instance(nm, JUNCTION, x, y))
    where = {i.name: i for i in instances}

    def point(ref: str) -> tuple[float, float]:
        inst, _, port = ref.partition(".")
        i = where[inst]
        px, py = d.port_point(d.lib[i.master], port)
        return i.x + px, i.y + py

    def resolve(ref: str, toward: tuple[float, float] | None, line: int) -> str:
        inst, dot, port = ref.partition(".")
        if inst not in where:
            raise FloorplanError(line, ref, f"nothing called {inst} is placed")
        i = where[inst]
        m = d.lib[i.master]
        if not dot:
            if i.master != JUNCTION:
                raise FloorplanError(line, ref, f"{inst} is a place: name its door "
                                     f"({', '.join(p.name for p in m.ports if p.kind == 'ion')})")
            if toward is None:
                raise FloorplanError(line, ref, "between two bare junctions the route must say "
                                     "which way: name an arm (n, e, s, w)")
            return f"{inst}.{_arm(toward[0] - i.x, toward[1] - i.y)}"
        try:
            m.port(port)
        except KeyError as exc:
            raise FloorplanError(line, ref, exc.args[0]) from None
        return ref

    links: list = []
    used_names: set[str] = set()

    def fresh(base: str) -> str:
        nm, k = base, 1
        while nm in used_names:
            k += 1
            nm = f"{base}_{k}"
        used_names.add(nm)
        return nm

    def end_point(ref: str, line: int) -> tuple[float, float]:
        inst = ref.partition(".")[0]
        if inst not in where:
            raise FloorplanError(line, ref, f"nothing called {inst} is placed")
        return point(resolve(ref, (0.0, 0.0), line)) if "." in ref else (where[inst].x,
                                                                         where[inst].y)

    for r in fp.roads:
        # a bare junction's arm faces the next point of the road: its first corner, or else
        # the other end (a door, or the other junction's centre)
        pa, pb = end_point(r.a, r.line), end_point(r.b, r.line)
        a = resolve(r.a, tuple(r.via[0]) if r.via else pb, r.line)
        b = resolve(r.b, tuple(r.via[-1]) if r.via else pa, r.line)
        nm = fresh(r.name or f"road_{a.replace('.', '_')}_{b.replace('.', '_')}")
        links.append((nm, a, b, r.via) if r.via else (nm, a, b))

    wires = list(fp.wires)
    if fp.auto_wires:
        wires += _auto_wires(fp, d, where)
    from .logic.decode import LATENCY
    default_latency = {"syndrome": LATENCY["link_us"], "outcome": LATENCY["link_us"],
                       "decision": LATENCY["decision_us"], "frame": 1.0}
    for w in wires:
        for ref in (w.a, w.b):
            inst, dot, port = ref.partition(".")
            if inst not in where or not dot:
                raise FloorplanError(w.line, ref, "a wire joins two doors, PLACE.port")
            p = d.lib[where[inst].master].port(port)
            if p.kind != "classical":
                raise FloorplanError(w.line, ref, f"{ref} carries ions, not bits")
        sig = d.lib[where[w.a.partition('.')[0]].master].port(w.a.partition('.')[2]).signal
        lat = w.latency if w.latency is not None else default_latency.get(sig, 1.0)
        nm = fresh(w.name or f"wire_{w.a.replace('.', '_')}_{w.b.replace('.', '_')}")
        links.append({"name": nm, "a": w.a, "b": w.b, "kind": "wire", "latency_us": lat,
                      "via": w.via})

    d.composite(fp.name, "town", fp.title or fp.name, f"a floorplan: {fp.name}",
                instances=instances, links=links, ports=[],
                params={"places": [p[0] for p in fp.places if p[4] != "control"],
                        "control": [p[0] for p in fp.places if p[4] == "control"],
                        "code": fp.code, "regions": fp.regions, "floorplan": fp.source})
    d.lib.top = fp.name
    return d.lib


def _auto_wires(fp: Floorplan, d: Designer, where: dict) -> list[_Wire]:
    """Every classical port of every place wired to the control places: syndromes to the
    decoder, outcomes to the classical memory, decisions from it, and the decoder's frame
    into it.  Drawn down to a bus line and along it, as the town draws them."""
    fam = {nm: d.lib[m].family for nm, m, *_ in fp.places}
    dec = [nm for nm, f in fam.items() if f == "decoder"]
    arc = [nm for nm, f in fam.items() if f == "archive"]
    out: list[_Wire] = []
    if not dec or not arc:
        return out
    decoder, archive = dec[0], arc[0]
    to = {"syndrome": f"{decoder}.syn", "outcome": f"{archive}.in", "decision": f"{archive}.ctl"}
    if fp.bus_y is not None:
        bus = fp.bus_y
    else:
        ys = [where[nm].y + d.lib[where[nm].master].bbox[3] for nm, f in fam.items()
              if f not in ("decoder", "archive")] + [j[2] for j in fp.junctions]
        bus = max(ys) + 10.0

    def at(ref):
        inst, _, port = ref.partition(".")
        i = where[inst]
        px, py = d.port_point(d.lib[i.master], port)
        return i.x + px, i.y + py

    for nm, master, *_ in fp.places:
        if fam[nm] in ("decoder", "archive"):
            continue
        for p in d.lib[master].ports:
            if p.kind != "classical" or p.signal not in to:
                continue
            other = to[p.signal]
            a, b = (f"{nm}.{p.name}", other) if p.dir == "out" else (other, f"{nm}.{p.name}")
            out.append(_Wire(a, b, None, [[at(a)[0], bus], [at(b)[0], bus]],
                             f"wire_{nm}_{p.name}", 0))
    out.append(_Wire(f"{decoder}.frame", f"{archive}.frame", 1.0, [], "wire_decoder_frame", 0))
    return out


def load_file(path, leaves: LeafLibrary) -> tuple[Library, Floorplan]:
    p = Path(path)
    fp = parse_floorplan(p.read_text(encoding="utf-8"), name=p.stem)
    return load(fp, leaves), fp


def _num(v: float) -> str:
    return f"{v:g}"


def write(lib: Library, *, regions: dict[str, str] | None = None, title: str | None = None,
          code: str = "surface_d3", comment: str = "") -> str:
    """A flat composite (places, junctions, roads, wires) as floorplan text."""
    top = lib[lib.top]
    regions = dict(regions or top.params.get("regions") or {})
    lines = []
    if comment:
        lines += [f"# {ln}" if ln else "#" for ln in comment.splitlines()] + [""]
    lines += [f'floorplan {top.name} "{title or top.title}"', f"code {code}", ""]
    order = ["supply", "compute", "memory", "factory", "control"]
    insts = [i for i in top.instances if i.master != JUNCTION]
    for reg in order + sorted({regions.get(i.name, "compute") for i in insts} - set(order)):
        mine = [i for i in insts if (regions.get(i.name) or HOME_REGION.get(lib[i.master].family,
                                                                             "compute")) == reg]
        if not mine:
            continue
        lines.append(f"region {reg}")
        for i in mine:
            x0, y0, _, _ = lib[i.master].bbox
            lines.append(f"place {i.name:<9} {i.master:<7} at {_num(i.x + x0)} {_num(i.y + y0)}")
        lines.append("")
    js = [i for i in top.instances if i.master == JUNCTION]
    if js:
        lines.append("# junctions: where roads meet")
        for i in js:
            lines.append(f"junction {i.name} at {_num(i.x)} {_num(i.y)}")
        lines.append("")
    roads = [c for c in top.channels if c.kind != "wire"]
    if roads:
        lines.append("# roads: conveyors between doors and junction arms")
        for c in roads:
            via = c.points[1:-1]
            # the default route takes one corner, horizontal first: only say `via` otherwise
            pa, pb = c.points[0], c.points[-1]
            default = [] if (pa[0] == pb[0] or pa[1] == pb[1]) else [[pb[0], pa[1]]]
            v = "" if via == default else " via " + ", ".join(f"{_num(x)} {_num(y)}" for x, y in via)
            lines.append(f"road {c.a} -- {c.b}{v} as {c.name}")
        lines.append("")
    ws = [c for c in top.channels if c.kind == "wire"]
    if ws:
        lines.append("# wires: bits, never ions")
        for c in ws:
            via = c.points[1:-1]
            pa, pb = c.points[0], c.points[-1]
            default = [] if (pa[0] == pb[0] or pa[1] == pb[1]) else [[pb[0], pa[1]]]
            v = "" if via == default else " via " + ", ".join(f"{_num(x)} {_num(y)}" for x, y in via)
            lines.append(f"wire {c.a} -> {c.b} latency {_num(c.latency_us)}{v} as {c.name}")
    return "\n".join(lines).rstrip() + "\n"
