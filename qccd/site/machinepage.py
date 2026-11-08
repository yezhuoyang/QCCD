"""The Machine page: a QCCD machine as a text, formalised (2026-11-08, a reader's request).

The other Learn pages all stand on one object and none of them defined it: a programme
acts on a machine, a rule is an assertion about a machine and a run on it, the compiler's
abstractions model a machine, an evaluation reads one.  This page is that definition, in
the order a programming-language reference gives one: the abstract syntax (what a machine
document may say), the static semantics (which documents are machines at all), the derived
notions everything downstream reads (degree, junction, bend, corner, slot, hop), and the
meaning (the state space a programme steps, the alphabet its statements are written in, the
price list its replay reads).  Then the same machine four ways -- the drawing, the document,
the listing that rebuilds it, the tuple the compiler reads -- six small machines as texts, the
generators, and how to write one.

Every fact printed here is computed from the machine documents when the site is built:
the counts, the derived sets, the loader's own error messages (produced by breaking a
machine and asking the loader), the listings (checked to rebuild their machine, or the site
is not built), and the verdicts on the programmes the examples play.
"""

from __future__ import annotations

import dataclasses
import html

from . import learn
from .algorithm import df as _df_algo, eg, esc, fig, tbl
from .examples import CHAIN, DUAL, GRID, GRID3, RACE, RING, build_example, machine, rec

# --------------------------------------------------------------------------- anchors

HEADINGS = [
    ("two-readings", "One text, two readings"),
    ("syntax", "The abstract syntax"),
    ("well-formed", "Well-formed machines"),
    ("derived", "What the text derives: degree, junction, bend, slot, hop"),
    ("meaning", "What a machine means"),
    ("readers", "Who reads what"),
    ("examples", "Six machines, read as texts"),
    ("generators", "Generators: a machine from a few numbers"),
    ("writing", "Writing a machine"),
    ("conclusion", "Conclusion"),
]

#: the definition cards, by (kind, name) -> element id; the glossary and the other pages link here
IDS = {
    ("Definition M1", "machine"): "def-machine",
    ("Definition M2", "geometry"): "def-geometry",
    ("Definition M3", "node"): "def-node",
    ("Definition M4", "rail"): "def-rail",
    ("Definition M5", "loop"): "def-loop",
    ("Definition M6", "zone types and capacity"): "def-zone",
    ("Definition M7", "primitives"): "def-primitives",
    ("Definition M8", "control"): "def-control",
    ("Definition M9", "well-formed machine"): "def-well-formed",
    ("Definition M10", "degree and junction"): "def-degree",
    ("Definition M11", "bend and corner"): "def-bend",
    ("Definition M12", "slots and the shift map"): "def-slot",
    ("Definition M13", "hop"): "def-hop",
    ("Definition M14", "state space"): "def-state",
    ("Definition M15", "well-typed statement"): "def-typed",
    ("Definition M16", "generator"): "def-generator",
    ("Definition M17", "listing"): "def-listing",
}


def df(kind: str, name: str, body: str, words: str = "") -> str:
    w = f'<p class="words"><b>In words.</b> {words}</p>' if words else ""
    i = IDS.get((kind, name))
    return f'<div class="df"{f" id={chr(34)}{i}{chr(34)}" if i else ""}><b>{kind}</b> <i>{html.escape(name)}.</i> {body}{w}</div>'


# --------------------------------------------------------------------------- the machines

#: the machines this page reads as texts, in the order they are met; the dicts are the
#: examples module's, so the Language and Rules pages and this one draw the same objects
MACHINES = [
    ("chain4", CHAIN, "the register", "a line of four traps: no junction, no closed loop"),
    ("race8", RACE, "the racetrack", "one closed loop of eight traps: rotation, no junction"),
    ("grid2x3", GRID, "the lattice", "junctions of degree 3 beside plain corners, a trap on every wire"),
    ("grid3x3", GRID3, "the larger lattice", "an X-junction of degree 4 in the middle"),
    ("dual3", DUAL, "the two loops", "two closed loops that share no site, joined where two rails meet two"),
    ("ring6d", RING, "the ring with docks", "a loop that cannot gate, two spurs to traps that can"),
]

#: the programme each machine plays on its card: short, and chosen to use what the machine
#: has (a junction crossing where there is a junction, a rotation where there is a loop)
PROGRAMMES = {
    "chain4": [rec("init", {"d0": "C0", "d1": "C2"}), rec("move", "d0", "C0", "C1"),
               rec("move", "d1", "C2", "C1"), rec("cool"), rec("gate", "CX", [["d0", "d1"]])],
    "race8": [rec("fill"), rec("rotate", 2)],
    "grid2x3": [rec("init", {"d0": "T0_0v"}), rec("shuttle", "d0", ["T0_0v", "J0_1", "T0_1h"])],
    "grid3x3": [rec("init", {"d0": "T0_1h"}), rec("shuttle", "d0", ["T0_1h", "J1_1", "T1_1v"])],
    "dual3": [rec("init", {"d0": "DT0", "d1": "AT0"}), rec("rotate", 1, loop="D"), rec("rotate", -1, loop="A")],
    "ring6d": [rec("init", {"d0": "S1", "d1": "S4"}), rec("rotate", -1),
               rec("move", "d0", "S0", "A0", cls="dock"), rec("move", "d1", "S3", "A3", cls="dock")],
    "tee": [rec("init", {"d0": "A", "d1": "C"}), rec("shuttle", "d0", ["A", "J", "B"]),
            rec("shuttle", "d1", ["C", "J", "B"]), rec("cool"), rec("gate", "CX", [["d0", "d1"]])],
}


def tee_machine():
    """The one machine on this page written by hand, node by node: three traps around one
    junction.  Built with the builder the Studio's Parts mode and Sketch mode write into, and
    wrapped in the default template's zones, curves and control plane so it can be played."""
    from ..api import Machine
    from ..arch.builder import DeviceBuilder
    d = DeviceBuilder("explicit")
    d.site("A", 0.0, 0.0, zone="trap")
    d.site("B", 2.0, 0.0, zone="trap")
    d.site("C", 1.0, 1.0, zone="trap")
    d.junction("J", 1.0, 0.0)
    d.segment("A-J", "A", "J")
    d.segment("J-B", "J", "B")
    d.segment("J-C", "J", "C")
    return Machine.from_device(d.build(), name="tee")


def _machines() -> dict:
    out = {key: machine(dev) for key, dev, _, _ in MACHINES}
    out["tee"] = tee_machine()
    return out


# --------------------------------------------------------------------------- facts read off a machine

def listing_text(m, section: str | None = None, mode: str = "full", banners: bool = True) -> str:
    """The machine as the programme that rebuilds it, or one section of it (`geometry`,
    `zones`, `control`, `curves`, ...), without the trailing alignment comments.  `banners`
    False also drops the section banners, for an excerpt in a narrow card."""
    from ..arch.listing import architecture_listing
    L = architecture_listing(m.arch, mode=mode)
    # an explicit geometry's builder statements are filed under sites / segments / loops;
    # the loops section otherwise holds only the derived-loops commentary
    want = {None: None, "geometry": {"geometry", "sites", "segments", "loops"}}.get(section, {section})
    lines = [ln for ln in L.lines if want is None or (ln.section in want and (ln.section != "loops" or ln.kind == "call"))]
    out = []
    for ln in lines:
        if not banners and ln.kind == "comment":
            continue
        t = ln.text
        if ln.kind == "call" and "#" in t:
            # the emitter pads a statement and appends a note; the page quotes the statement
            t = _strip_note(t)
        out.append(t.rstrip())
    return "\n".join(out).strip("\n")


def _strip_note(t: str) -> str:
    depth = 0
    q = None
    for i, c in enumerate(t):
        if q:
            if c == "\\":
                continue
            if c == q:
                q = None
            continue
        if c in "\"'":
            q = c
        elif c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "#" and depth == 0:
            return t[:i].rstrip()
    return t


def check_round_trips(ms: dict) -> None:
    """Every listing on this page rebuilds its machine, or the site is not built: the page
    says a listing denotes the machine, and that sentence is a test."""
    from ..arch.listing import round_trip_check
    for key, m in ms.items():
        ok, diff = round_trip_check(m.arch)
        if not ok:
            raise RuntimeError(f"machine page: the listing of {key!r} does not rebuild it: {diff[:3]}")


def broken_messages() -> list[tuple[str, str, str]]:
    """The loader's own words for an ill-formed machine: each row breaks the register in one
    way and asks `Device.check_structure` (or `Architecture.from_json`) what is wrong.
    (judgment, what was written, the message)."""
    from ..arch.device import Device, ExpansionError, Loop, Node, Segment
    from ..arch import Architecture
    m = machine(CHAIN)
    dev = m.arch.device
    rows: list[tuple[str, str, str]] = []

    def msg(d: Device) -> str:
        errs = d.check_structure()
        return errs[0] if errs else "(accepted)"

    segs = dict(dev.segments)
    segs["E3"] = Segment(id="E3", ends=("C3", "C4"))
    rows.append(("every end of a rail is a node", 'd.segment("E3", "C3", "C4")  # there is no C4',
                 msg(dataclasses.replace(dev, segments=segs))))
    segs = dict(dev.segments)
    segs["E3"] = Segment(id="E3", ends=("C3", "C3"))
    rows.append(("a rail joins two different nodes", 'd.segment("E3", "C3", "C3")',
                 msg(dataclasses.replace(dev, segments=segs))))
    segs = dict(dev.segments)
    segs["E9"] = Segment(id="E9", ends=("C1", "C0"))
    rows.append(("one rail at most between two nodes", 'd.segment("E9", "C1", "C0")  # E0 already joins them',
                 msg(dataclasses.replace(dev, segments=segs))))
    loops = {**dev.loops, "P1": Loop(id="P1", nodes=("C0",), closed=False, kind="path")}
    rows.append(("a loop has at least two nodes", 'd.loop("P1", ["C0"], closed=False)',
                 msg(dataclasses.replace(dev, loops=loops))))
    loops = {**dev.loops, "P1": Loop(id="P1", nodes=("C0", "C1", "C0", "C2"), closed=False, kind="path")}
    rows.append(("a loop visits a node once", 'd.loop("P1", ["C0", "C1", "C0", "C2"], closed=False)',
                 msg(dataclasses.replace(dev, loops=loops))))
    loops = {**dev.loops, "P1": Loop(id="P1", nodes=("C0", "C2"), closed=False, kind="path")}
    rows.append(("consecutive nodes of a loop are joined by a rail", 'd.loop("P1", ["C0", "C2"], closed=False)',
                 msg(dataclasses.replace(dev, loops=loops))))
    nodes = dict(dev.nodes)
    nodes["C0"] = dataclasses.replace(nodes["C0"], capacity=0)
    rows.append(("a site holds at least one ion", 'a site C0 with capacity 0',
                 msg(dataclasses.replace(dev, nodes=nodes))))
    nodes = dict(dev.nodes)
    nodes["J"] = Node(id="J", pos=(4.0, 0.0), kind="junction", capacity=2)
    rows.append(("a bare junction holds none", 'a junction J with capacity 2',
                 msg(dataclasses.replace(dev, nodes=nodes))))
    # the two checks that need the whole document: a zone the geometry names must be declared,
    # and a cached derived field must agree with the graph
    doc = m.arch.to_json(expanded=True)
    doc["geometry"]["nodes"][0]["zone_type"] = "storage"
    try:
        Architecture.from_json(doc)
        rows.append(("every zone a site names is declared", 'd.site("C0", 0.0, 0.0, zone="storage")', "(accepted)"))
    except ExpansionError as exc:
        rows.append(("every zone a site names is declared", 'd.site("C0", 0.0, 0.0, zone="storage")',
                     str(exc).splitlines()[-1].strip()))
    doc = m.arch.to_json(expanded=True)
    doc["geometry"]["nodes"][1]["degree"] = 10
    try:
        Architecture.from_json(doc)
        rows.append(("a cached derived field agrees with the graph", '"degree": 10 written on C1', "(accepted)"))
    except ExpansionError as exc:
        rows.append(("a cached derived field agrees with the graph", '"degree": 10 written on C1',
                     str(exc).splitlines()[-1].strip()))
    return rows


def derived_table(ms: dict) -> str:
    """Definition M10-M12 read off every machine on the page."""
    rows = []
    for key, dev_dict, label, _ in MACHINES:
        d = ms[key].arch.device
        deg = {}
        for n in d.nodes:
            deg[d.degree(n)] = deg.get(d.degree(n), 0) + 1
        hist = ", ".join(f"{v} of degree {k}" for k, v in sorted(deg.items()))
        loops = "; ".join(f"{lid} ({'closed' if lp.closed else 'open'}, {len(d.loop_slots(lid))} slots)"
                          for lid, lp in d.loops.items()) or "none"
        rows.append([f"<b>{label}</b> ({key})", hist, _names(d.junction_nodes), _names(sorted(d.all_corners)),
                     _names(d.bend_nodes), loops])
    return tbl(["machine", "degrees", "junctions (degree ≥ 3)", "corners of its loops", "bends (degree-2 turns)", "loops and their slots"], rows)


def _names(xs, cap: int = 6) -> str:
    xs = list(xs)
    if not xs:
        return "∅"
    if len(xs) > cap:
        return ", ".join(xs[:cap - 1]) + f", … ({len(xs)})"
    return ", ".join(xs)


def hardware_record(m) -> list[list[str]]:
    """The record the rule language reads off a machine (`qccd.ruleset.programme.hardware_of`),
    field by field, with its value on this machine."""
    from ..ruleset.programme import hardware_of
    hw = hardware_of(m.arch)
    d = m.arch.device
    gate = sorted(s.id for s, ab in hw.abilities.items() if "gate" in ab)
    spam = sorted(s.id for s, ab in hw.abilities.items() if "spam" in ab)
    return [
        ["sites", "every place an ion may stand", _names([s.id for s in hw.sites], 8)],
        ["junctions", "nodes where three or more rails meet", _names([s.id for s in hw.junctions])],
        ["rails", "the segments, with ends(r)", f"{len(hw.rails)} rails"],
        ["capacity(x)", "ions a site or rail holds at once",
         ", ".join(sorted({f"{d.nodes[n].capacity} in zone {d.nodes[n].zone_type}" for n in d.nodes if d.nodes[n].kind == "site"}))
         + "; 1 on every rail"],
        ["degree(n)", "rails at node n", ", ".join(f"{n}: {d.degree(n)}" for n in list(d.nodes)[:6]) + (" …" if len(d.nodes) > 6 else "")],
        ["zone(s)", "the zone type of site s", ", ".join(sorted({str(d.nodes[n].zone_type) for n in d.nodes if d.nodes[n].kind == "site"}))],
        ["can(s, a)", "gate / spam / cool at site s", f"gate: {_names(gate)}; spam: {_names(spam)}"],
        ["loops", "each loop's slots in order", "; ".join(f"{k} = ⟨{', '.join(s.id for s in v)}⟩" for k, v in hw.loops.items()) or "∅"],
        ["numbers", "the declared limits", ", ".join(f"{k} = {v}" for k, v in hw.numbers.items() if k in ("max_junction_degree", "min_rail_angle_deg", "max_simd_classes", "control_model"))],
    ]


def compiler_tuple(m) -> str:
    """Definition 1 of the Compilation page, read off this machine."""
    d = m.arch.device
    traps = [n for n in d.nodes if d.nodes[n].kind == "site"]
    juncs = [n for n in d.nodes if d.nodes[n].kind != "site"]
    gate = [n for n in traps if m.arch.can(n, "gate")]
    meas = [n for n in traps if m.arch.can(n, "spam")]
    loops = [f"{k} = ⟨{', '.join(l.nodes)}⟩" for k, l in d.loops.items() if l.closed]
    caps = sorted({d.nodes[n].capacity for n in traps})
    return (f"<var>T</var> = {{{', '.join(traps)}}}; <var>J</var> = {{{', '.join(juncs) or ''}}}; "
            f"|<var>E</var>| = {len(d.segments)}; cap = {', '.join(str(c) for c in caps)}; "
            f"gate = {{{', '.join(gate)}}}; meas = {{{', '.join(meas)}}}; "
            f"<var>L</var> = {{{'; '.join(loops) or ''}}}")


def lean_facts(m) -> str:
    """What the proved checker is handed about this machine (`Compiler/bridge/mk_qcheck_input.py`):
    the gate-capable sites, the hop pairs one cycle apart, each closed loop's slots."""
    import sys
    from pathlib import Path
    here = Path(__file__).resolve().parents[2] / "Compiler" / "bridge"
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    try:
        from mk_qcheck_input import trap_hops  # type: ignore
    except Exception:  # the bridge is not part of every checkout; the page then says so
        return "the bridge that writes the checker's input is not in this build"
    arch = m.arch.to_json(expanded=True)
    hops = trap_hops(arch)
    d = m.arch.device
    gate = sorted(n for n in d.nodes if d.nodes[n].kind == "site" and m.arch.can(n, "gate"))
    loops = {k: d.loop_slots(k) for k, l in d.loops.items() if l.closed}
    return (f"gateSites = {{{', '.join(gate)}}}; hops = {len(hops)} ordered pairs, e.g. "
            + ", ".join(f"({h['from']}, {h['to']})" for h in hops[:4]) + (" …" if len(hops) > 4 else "")
            + "; loops = " + ("; ".join(f"{k} ↦ ⟨{', '.join(v)}⟩" for k, v in loops.items()) or "none"))


# --------------------------------------------------------------------------- the drawings

def dev(m, caption: str, fit=(300, 230), **kw) -> str:
    from .build import device_svg
    return fig(device_svg(m, labels=True, fit=fit, **kw), caption)


def degree_marks(m) -> dict:
    d = m.arch.device
    return {n: (f"deg {d.degree(n)}", "#1c2a4a" if d.degree(n) >= 3 else "#6b7280") for n in d.nodes}


def shift_paths(m, loop: str, delta: int, color: str = "#0b7a4b") -> list[dict]:
    d = m.arch.device
    sm = d.shift_map(loop, delta)
    out = []
    for src, dst in sm.items():
        walk = d.slot_walk(loop, src, 1 if delta > 0 else -1)
        out.append({"nodes": list(walk), "color": color, "label": ""})
    return out


def fig_two_readings() -> str:
    """The document, the machine and the listing: two texts, one object."""
    s = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 262" width="760" height="262" style="font-family:var(--sans);font-size:12.5px">
<defs><marker id="arw" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#1c2a4a"/></marker></defs>
<rect x="8" y="40" width="226" height="178" rx="10" fill="#faf9f5" stroke="#1c2a4a"/>
<text x="121" y="62" text-anchor="middle" font-weight="700" fill="#1c2a4a">the document</text>
<text x="121" y="78" text-anchor="middle" fill="#555" font-size="11.5">ring6d.arch.json</text>
<text x="22" y="102" font-family="var(--mono)" font-size="10.5">{"name": "ring6d",</text>
<text x="22" y="117" font-family="var(--mono)" font-size="10.5"> "geometry": {</text>
<text x="22" y="132" font-family="var(--mono)" font-size="10.5">    "generator": "ring",</text>
<text x="22" y="147" font-family="var(--mono)" font-size="10.5">    "params": {"width": 3, ...}},</text>
<text x="22" y="162" font-family="var(--mono)" font-size="10.5"> "zone_types": {...},</text>
<text x="22" y="177" font-family="var(--mono)" font-size="10.5"> "primitives": {...}, ...}</text>
<text x="121" y="204" text-anchor="middle" fill="#555" font-size="11.5">data: what the file stores</text>
<rect x="526" y="40" width="226" height="178" rx="10" fill="#faf9f5" stroke="#1c2a4a"/>
<text x="639" y="62" text-anchor="middle" font-weight="700" fill="#1c2a4a">the listing</text>
<text x="639" y="78" text-anchor="middle" fill="#555" font-size="11.5">the programme that rebuilds it</text>
<text x="540" y="102" font-family="var(--mono)" font-size="10.5">m = Machine.blank("ring",</text>
<text x="540" y="117" font-family="var(--mono)" font-size="10.5">      width=3, verticals=2, ...)</text>
<text x="540" y="132" font-family="var(--mono)" font-size="10.5">m.set_zone("trap", ...)</text>
<text x="540" y="147" font-family="var(--mono)" font-size="10.5">m.declare_class("rotate_cw", ...)</text>
<text x="540" y="162" font-family="var(--mono)" font-size="10.5">m.set_curve("shuttle_segment",</text>
<text x="540" y="177" font-family="var(--mono)" font-size="10.5">            [...])</text>
<text x="639" y="204" text-anchor="middle" fill="#555" font-size="11.5">code: statements, run in order</text>
<rect x="278" y="74" width="204" height="110" rx="12" fill="#eef4fb" stroke="#1c2a4a" stroke-width="1.6"/>
<text x="380" y="100" text-anchor="middle" font-weight="700" font-size="15" fill="#1c2a4a">the machine M</text>
<text x="380" y="120" text-anchor="middle" fill="#333" font-size="11.5">the expanded graph (N, E, L)</text>
<text x="380" y="136" text-anchor="middle" fill="#333" font-size="11.5">+ zones, curves, control, budget</text>
<text x="380" y="160" text-anchor="middle" fill="#555" font-size="11.5">what every reader consumes</text>
<path d="M234,108 L276,108" stroke="#1c2a4a" stroke-width="1.4" marker-end="url(#arw)"/>
<text x="255" y="100" text-anchor="middle" font-size="10.5" fill="#1c2a4a">load</text>
<path d="M276,152 L234,152" stroke="#1c2a4a" stroke-width="1.4" marker-end="url(#arw)"/>
<text x="255" y="168" text-anchor="middle" font-size="10.5" fill="#1c2a4a">to_json</text>
<path d="M526,108 L484,108" stroke="#1c2a4a" stroke-width="1.4" marker-end="url(#arw)"/>
<text x="505" y="100" text-anchor="middle" font-size="10.5" fill="#1c2a4a">exec</text>
<path d="M484,152 L526,152" stroke="#1c2a4a" stroke-width="1.4" marker-end="url(#arw)"/>
<text x="505" y="168" text-anchor="middle" font-size="10.5" fill="#1c2a4a">listing</text>
<text x="380" y="246" text-anchor="middle" font-size="12" fill="#0b7a4b">round trip: listing(M) run again gives M, checked for every machine on this page when the site is built</text>
</svg>"""
    return s


def fig_readers() -> str:
    """One machine, five readers, each taking its own projection."""
    boxes = [
        (20, 20, "Language", "a programme's names must be M's:", "sites, rails, loops, classes, abilities"),
        (560, 20, "Rules", "the Hardware record:", "sites, junctions, rails, capacity, degree, zone, can, loops, numbers"),
        (20, 170, "Compiler", "the trap graph A(M):", "(V, E, cap, gate, meas, L), and the prices for cooling"),
        (560, 170, "Evaluation", "the tables:", "curves, wiring, budget, heating, species"),
        (290, 200, "Proved checker", "read from the document, never the compiler:", "gateSites, hops, loop slots"),
    ]
    s = ['<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 760 290" width="760" height="290" style="font-family:var(--sans);font-size:12px">',
         '<defs><marker id="arw2" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#1c2a4a"/></marker></defs>',
         '<rect x="300" y="80" width="160" height="90" rx="12" fill="#eef4fb" stroke="#1c2a4a" stroke-width="1.6"/>',
         '<text x="380" y="112" text-anchor="middle" font-weight="700" font-size="15" fill="#1c2a4a">the machine M</text>',
         '<text x="380" y="132" text-anchor="middle" fill="#333" font-size="11.5">one text, loaded once</text>',
         '<text x="380" y="150" text-anchor="middle" fill="#555" font-size="11.5">every reader takes a projection</text>']
    for x, y, title, what, fields in boxes:
        w = 180 if title != "Proved checker" else 180
        s.append(f'<rect x="{x}" y="{y}" width="{w}" height="80" rx="10" fill="#faf9f5" stroke="#1c2a4a"/>')
        s.append(f'<text x="{x + w / 2}" y="{y + 20}" text-anchor="middle" font-weight="700" fill="#1c2a4a">{title}</text>')
        s.append(f'<text x="{x + w / 2}" y="{y + 38}" text-anchor="middle" fill="#555" font-size="11">{what}</text>')
        # wrap the field list onto two lines
        words = fields.split(", ")
        half = (len(words) + 1) // 2
        s.append(f'<text x="{x + w / 2}" y="{y + 54}" text-anchor="middle" fill="#333" font-size="11">{", ".join(words[:half])}{"," if len(words) > half else ""}</text>')
        if len(words) > half:
            s.append(f'<text x="{x + w / 2}" y="{y + 69}" text-anchor="middle" fill="#333" font-size="11">{", ".join(words[half:])}</text>')
    # arrows from M to each box
    arrows = [(300, 100, 200, 60), (460, 100, 560, 60), (300, 150, 200, 190), (460, 150, 560, 190), (380, 170, 380, 198)]
    for x1, y1, x2, y2 in arrows:
        s.append(f'<path d="M{x1},{y1} L{x2},{y2}" stroke="#1c2a4a" stroke-width="1.3" marker-end="url(#arw2)"/>')
    s.append("</svg>")
    return "".join(s)


def fig_state(m) -> str:
    """The state a programme steps, drawn: positions on the machine, a heat per ion, a clock."""
    from .build import device_svg
    svg = device_svg(m, labels=True, ions={"C1": ["d0"], "C2": ["d1"]}, fit=(300, 150))
    return (f'<div class="statefig"><div class="figsvg">{svg}</div>'
            f'<div class="stateparts"><div><b>pos</b><span>d0 ↦ C1, d1 ↦ C2</span><small>a partial map ions ⇀ sites(M)</small></div>'
            f'<div><b>n̄</b><span>d0: 0.10, d1: 0.00</span><small>a heat per ion, in motional quanta</small></div>'
            f'<div><b>clock</b><span>5 µs</span><small>the time elapsed</small></div></div></div>')


# --------------------------------------------------------------------------- the examples

def build_examples(out, put) -> dict:
    """Render the Studio page each machine's card plays (machine/ex/<name>.html), judge the
    programme on it, and return what the page shows.  A programme that cannot be replayed on
    its machine, or that fails a rule, stops the build: the page says these programmes are
    well-typed and legal on their machines."""
    from .build import HASH_JS, poster
    ms = _machines()
    check_round_trips(ms)
    built: dict = {}
    for key, m in ms.items():
        rel = f"machine/ex/{key}.html"
        about = next((d["about"] for k, d, _, _ in MACHINES if k == key), "three traps around one junction, written node by node")
        r = build_example({"program": PROGRAMMES[key]}, m, "corrected", out / rel, kicker="THE MACHINE",
                          headline=key, lede=about)
        if r["verdict"]["failed"] or r["rules"]["fails"]:
            raise RuntimeError(f"machine page: the programme on {key!r} is not legal: "
                               f"{r['verdict']['failed']} {r['rules']['fails']}")
        put(rel, (out / rel).read_text(encoding="utf-8"), 2, "machine", app=True, extra=HASH_JS)
        built[key] = {**r, "page": f"ex/{key}.html", "poster": poster(m, r.get("init"), fit=(640, 300))}
    return built


# --------------------------------------------------------------------------- the page

GRAMMAR = """machine    ::= (name, geometry, zone_types, primitives, control, heating, species, budget)

geometry   ::= generator(params)                         -- compact: a function that writes the explicit form
             | explicit(nodes, segments, loops)           -- expanded: every node, rail and loop stated
node       ::= site(id, pos, zone_type, capacity?)        -- a place an ion can stand
             | junction(id, pos)                          -- a bare meeting point; it holds no ion
segment    ::= rail(id, a, b, length?, capacity?, loop?)  -- a and b are node ids; capacity defaults to 1
loop       ::= loop(id, <n1, ..., nk>, closed, kind)      -- k >= 2; kind is ring or path

zone_types ::= { zone ↦ (capacity, gate?, spam?, cool?, photoionization?) }
primitives ::= { name ↦ curve | curve_by_degree | scalars }
curve      ::= [ point, ... ]                             -- at least one operating point
point      ::= (us, quanta, table?, source?, label?)
control    ::= (model, classes, max_simd_classes_per_cycle, intra_inter_exclusive, wiring, channels, optical)
class      ::= (id, type, orbit, delta?, direction?, entails?)
heating    ::= (anomalous_rate_quanta_per_ms, ...)
species    ::= (qubit, coolant, sympathetic, coolant_fraction, T_coh_s, ...)
budget     ::= { max_dacs, max_junctions, max_junction_degree, min_rail_angle_deg, ... }

pos        ::= (x, y)        id ::= a name        zone ::= a name declared in zone_types"""


def page(PAGE: str, STYLE: str, built: dict) -> str:
    from .algorithm import CSS as ALGO_CSS
    ms = _machines()
    ring, chain, grid, race, dual, grid3, tee = (ms[k] for k in ("ring6d", "chain4", "grid2x3", "race8", "dual3", "grid3x3", "tee"))
    rd = ring.arch.device
    s_ring = rd.summary()
    b: list[str] = []
    w = b.append

    # ------------------------------------------------------------------ head
    w("<h1>The machine</h1>")
    w('<p class="sub">Everything on this website happens on a machine: a programme runs on one, a rule is an '
      'assertion about one, the compiler plans on one, an evaluation prices one. This page defines that object. '
      'A machine is a <b>text</b>: a document with a fixed grammar, read by one loader into one graph with '
      'tables attached, and every other page reads that graph and nothing else. The same text is also a '
      '<b>programme</b>, a list of statements that rebuilds the machine, which is how the Design page lets you '
      'edit one.</p>')
    w(learn.strip("machine"))
    w(learn.summary([
        "<b>One text, two readings.</b> A machine is stored as a document and shown as the programme that "
        "rebuilds it; both denote one object, the expanded graph with its tables, and the site checks that they do.",
        "<b>The abstract syntax.</b> What a machine document may say, as a grammar: geometry (nodes, rails, "
        "loops), zone types, primitive curves, control, heating, species, budget. Each production is defined once "
        "and shown on a real machine.",
        "<b>Well-formed machines.</b> Which documents are machines at all: ten checks, each quoted with the "
        "words the loader prints when it fails.",
        "<b>What the text derives.</b> Degree, junction, bend, corner, slot, shift map and hop are not written "
        "in the document; they are computed from it, and they are what the rules, the compiler and the proved "
        "checker read.",
        "<b>What a machine means.</b> The state space a programme steps, the alphabet its statements must be "
        "written in, and the price list its replay reads; then who reads which part of the text: the "
        "<a href=\"../language/\">language</a>, the <a href=\"../rules/\">rules</a>, the "
        "<a href=\"../compilation/\">compiler</a>, the <a href=\"../evaluation/\">evaluation</a>, the proved checker.",
        "<b>Six machines read as texts</b>, each playable; the generators that write a machine from a few "
        "numbers; and how to write one by hand, in the Design page or in Python.",
    ], lead="The <a href=\"../physics/\">Physics</a> page said what a trapped-ion machine can do and named the "
            "five words its map is drawn in: site, segment, junction, loop, zone type. This page makes those words "
            "formal, so that the next three pages can speak about one well-defined object."))
    w(learn.ask("A programme says <code>p.move(\"d0\", \"S0\", \"A0\")</code>. What has to exist before that line means anything?",
                "A machine: a text that says S0 and A0 are sites, that one rail joins them, how many ions each "
                "holds, what crossing that rail costs in time and heat, and which movement class may carry an ion "
                "along it. A programme is written in the names a machine declares, a rule quantifies over the sets "
                "a machine defines, and the compiler searches the graph a machine draws. So the machine comes first, "
                "and it has to be defined as carefully as the programme."))
    w('<ul class="toc2">' + "".join(f'<li><a href="#{a}">{t}</a></li>' for a, t in HEADINGS) + "</ul>")
    w('<div class="algo mpage">')

    # ------------------------------------------------------------------ 1. two readings
    w(f'<h2 id="two-readings">1. {HEADINGS[0][1]}</h2>')
    w(learn.section("the one idea the page rests on: a machine is a text, and the text has two equivalent forms, "
                    "a document (data) and a listing (a programme), that denote the same object. The object is what "
                    "everything downstream reads."))
    w(learn.ask("Is a machine a file, a picture, or a program?",
                "All three name one thing. The file is the document; the picture is drawn from the graph the document "
                "loads into; and that graph can be printed back as a programme whose statements, run in order, "
                "rebuild it. The graph is the machine. The other two are its syntax."))
    w(fig(fig_two_readings(), "A machine document is loaded into the expanded graph with its tables, which is the "
                              "machine <var>M</var> every reader consumes; the same <var>M</var> is printed as a "
                              "listing, a programme in a grammar of a few method calls and no expressions (section 9), and running "
                              "that listing gives <var>M</var> back. The two texts are interchangeable; the Studio's "
                              "Design page edits the listing, the files on disk are documents."))
    w("<p>One word first. On this website <i>machine</i> and <i>device</i> name the same object: <i>device</i> is the "
      "Studio's and the file format's word (a device document, the Design page's device), <i>machine</i> the one the "
      "definitions use. Wherever another page says <i>the device</i>, it means the <var>M</var> of this page.</p>")
    w("<p>Why two forms? A document is what a file should be: data, with a schema, that a loader validates and "
      "nobody has to execute. A listing is what an editor should show: statements a person can read top to bottom, "
      "change one of, and run again, which is what the Design page's text lane does and what a Python session does. "
      "A machine written in either form is the same machine, and the page holds itself to that: for every machine "
      "shown here the listing is run again and the result compared with the original when the site is built.</p>")
    w(eg(f"The ring with two docks, <code>ring6d</code>, as a document and as a listing. The document's geometry is "
         f"compact, a generator and its parameters; loading it expands the generator into "
         f"{s_ring['n_nodes']} nodes, {s_ring['n_segments']} rails and {s_ring['n_loops']} loop. The listing's first "
         f"statement is the same generator call; the statements after it declare the zones, the movement classes and "
         f"the prices, in that order, because a zone must exist before a site is placed in it and a class must exist "
         f"before a cycle uses it."
         f"<pre class=\"ex\"><code>{esc(listing_text(ring, 'geometry'))}\n\n{esc(listing_text(ring, 'zones'))}</code></pre>"
         f"<p>The drawing beside it is read off the same graph: six loop sites, two docks, the two degree-3 nodes "
         f"where a spur meets the loop ringed dark.</p>"
         + dev(ring, "ring6d, drawn from its expanded graph: sites are discs coloured by zone, a site where three rails "
                     "meet carries a dark ring, rails are lines", fit=(420, 220))))
    w(learn.bridge("So a machine is one object with two spellings. The next section writes down what the object "
                   "may contain, production by production."))

    # ------------------------------------------------------------------ 2. syntax
    w(f'<h2 id="syntax">2. {HEADINGS[1][1]}</h2>')
    w(learn.section("the grammar of a machine document, then one definition per production: machine, geometry, "
                    "node, rail, loop, zone type, primitive, control. Each definition is followed by its instance on "
                    "a real machine, so no symbol is left abstract."))
    w(learn.ask("What is the smallest set of things a machine must declare for a programme to be checkable on it?",
                "Where ions can stand and how they are connected (the geometry), how many fit and what may happen "
                "to them there (the zones), what each move costs (the primitives), and what may move together (the "
                "control). Everything else, heating, species, budget, refines the price or bounds the hardware."))
    w(f'<pre class="grammar"><code>{esc(GRAMMAR)}</code></pre>')
    w('<p class="notecap">Read it as a type: a machine is a record of eight fields; the geometry is a sum type, '
      'compact or explicit, and the loader always produces the explicit form. A question mark marks a field with a '
      'default. The concrete syntax is JSON, with these keys spelled exactly as written (<code>zone_types</code>, '
      '<code>max_simd_classes_per_cycle</code>); the <a href="../docs/adl/">architecture description reference</a> '
      'has every key and default.</p>')
    w(df("Definition M1", "machine",
         "A <i>machine</i> is a tuple <var>M</var> = (<var>G</var>, <var>Z</var>, Π, <var>C</var>, <var>H</var>, "
         "<var>S</var>, <var>B</var>) of a geometry <var>G</var> (Definition M2), a family of zone types <var>Z</var> "
         "(M6), a table of primitives Π (M7), a control plane <var>C</var> (M8), a heating rate <var>H</var>, a "
         "species record <var>S</var> and a budget <var>B</var>, together with a name. Only <var>G</var>, <var>Z</var> "
         "and Π are required; the others default to empty, and a word that reads an absent field says so "
         "(the rules report <i>could not be checked</i>, never <i>holds</i>).",
         "A machine is the whole document, not its drawing. The drawing is <var>G</var>; the rest is what the "
         "drawing costs, what it allows, and how it is driven."))
    w(df("Definition M2", "geometry",
         "A <i>geometry</i> is a triple <var>G</var> = (<var>N</var>, <var>E</var>, <var>L</var>): a finite ordered "
         "set of nodes <var>N</var>, a finite set of rails <var>E</var> each joining two nodes, and a finite set of "
         "loops <var>L</var>, each a sequence of nodes. The order of <var>N</var> is part of the geometry: a listing "
         "prints its statements in it, and two documents with the same sets in different orders print differently.",
         "The geometry is a graph with named paths drawn on it. Nothing in it says <i>junction</i> in the sense the "
         "rules use; that is derived (M10)."))
    w(df("Definition M3", "node",
         "A <i>node</i> is either a <b>site</b> site(<var>id</var>, <var>pos</var>, <var>z</var>, <var>c</var>), a "
         "place an ion can stand, with a zone type <var>z</var> ∈ dom <var>Z</var> and an optional capacity "
         "<var>c</var> ≥ 1 of its own, or a <b>bare junction</b> junction(<var>id</var>, <var>pos</var>), a meeting "
         "point of rails that holds no ion and has no zone. <var>pos</var> ∈ ℝ² is geometry, not decoration: three "
         "rules read it (R19–R21, below).",
         "Two kinds of node, and the kind is declared. Whether a site <i>acts</i> as a junction (an ion pays to cross "
         "it) is not declared; it follows from how many rails meet there."))
    w(df("Definition M4", "rail",
         "A <i>rail</i> (segment) rail(<var>id</var>, <var>a</var>, <var>b</var>, ℓ, <var>c</var>, <var>λ</var>) joins "
         "two distinct nodes <var>a</var>, <var>b</var> ∈ <var>N</var>, has a length ℓ > 0 (default 1), a capacity "
         "<var>c</var> ≥ 1 of ions in transit at once (default 1, rule R3's bound), and optionally names the loop "
         "<var>λ</var> ∈ <var>L</var> it belongs to. Rails are undirected; at most one rail joins a given pair of nodes.",
         "A rail is the stretch of trap axis an ion is shuttled along. Its capacity is how many ions may be on it in "
         "one cycle, which is almost always one."))
    w(df("Definition M5", "loop",
         "A <i>loop</i> loop(<var>id</var>, ⟨<var>n</var>₁, …, <var>n</var><sub>k</sub>⟩, closed, kind) is a sequence "
         "of <var>k</var> ≥ 2 distinct nodes in which consecutive nodes are joined by a rail (and the last to the first "
         "when closed). A closed loop (kind <i>ring</i>) is the domain of one movement template, the rigid shift "
         "(M12); an open loop (kind <i>path</i>) is a register and has no shift.",
         "A loop is a named path. Keeping it in the geometry, instead of rediscovering cycles when compiling, is "
         "what lets <code>p.rotate</code> be one statement and one movement class."))
    w(eg("The register <code>chain4</code> written explicitly: four sites in a line, three rails, one open loop. "
         "The generator line writes exactly this when it is expanded; the fields <code>degree</code> and "
         "<code>corner</code> are cached derived values (M10, M11), re-checked on load."
         f"<pre class=\"ex\"><code>{esc(_chain_json(chain))}</code></pre>"
         + dev(chain, "chain4: N = {C0, C1, C2, C3}, E = {E0, E1, E2}, L = {P0 = ⟨C0, C1, C2, C3⟩, open}", fit=(420, 120))))
    w(df("Definition M6", "zone types and capacity",
         "The <i>zone types</i> <var>Z</var> map each zone name to a record (capacity, gate, spam, cool, "
         "photoionization) of one number and four flags. The <i>capacity</i> of a site is its own <var>c</var> when "
         "it declares one and its zone's otherwise: cap(<var>n</var>) = <var>c</var>(<var>n</var>) if set, else "
         "<var>Z</var>(<var>z</var>(<var>n</var>)).capacity; a bare junction has cap 0. The <i>abilities</i> of a site "
         "are its zone's flags: can(<var>n</var>, <var>a</var>) ⇔ <var>Z</var>(<var>z</var>(<var>n</var>)).<var>a</var> "
         "for <var>a</var> ∈ {gate, spam, cool}.",
         "A zone type is a reusable label: declare <i>trap</i> once as “two ions, gates, measurement and cooling "
         "allowed”, then stamp it on a hundred sites. Capacity is rule R1's bound, the flags are rule R6's "
         "permissions, and motion is independent of both: an ion may cross a site it cannot be gated in."))
    w(eg("On <code>ring6d</code> the loop sites are zone <i>data</i> and the two docks zone <i>trap</i>; the third "
         "zone, <i>load</i>, is declared and unused. So cap(S1) = 2, can(S1, gate) is false, can(A0, gate) is true."
         f"<pre class=\"ex\"><code>{esc(listing_text(ring, 'zones'))}</code></pre>"))
    w(df("Definition M7", "primitives",
         "The <i>primitives</i> Π map a primitive's name to its price. A transport primitive (shuttle_segment, split, "
         "merge, ion_swap) has a <i>curve</i>: a non-empty finite set of operating points (us, quanta, table, source), "
         "each a measured pair of a duration in microseconds and a heat in motional quanta, tagged with the table it "
         "was taken from. junction_cross has one curve per degree. A gate or readout primitive (ms_gate, 1q_gate, "
         "measure, reset, cool) has scalars: a duration and a fidelity or an error, and for the two-ion gate the heat "
         "budget max_quanta it tolerates (rule R7). An <i>operating-point policy</i> π picks one point of each curve; "
         "the price of a cycle under π is the sum over the primitives it uses.",
         "A machine does not have <i>a</i> cost per move; it has a menu per move, because slowing a transport buys "
         "coldness, and the compiler is allowed to make that trade. A policy is the choice of one row from each menu."))
    w(eg("The shuttle curve of the default template, which every machine on this page carries: four operating "
         "points from three published tables. A policy such as (qccdsim_jones, fastest) resolves it to 5 µs and 0.1 "
         "quanta; (transport_excitation, coolest) to 14 µs and 0.1."
         f"<pre class=\"ex\"><code>{esc(_curve_lines(ring, 'shuttle_segment'))}</code></pre>"))
    w(df("Definition M8", "control",
         "The <i>control plane</i> <var>C</var> says what may move together. Its model is one of simd_classes, direct "
         "or c2lr. Under simd_classes it declares a set of <i>movement classes</i>, each (id, type, orbit, delta, "
         "entails): a motion template over the nodes of an orbit (a loop, the spurs, or any), with the primitives it "
         "entails (a dock entails a split and a merge; a rigid rotation entails neither), and a bound "
         "max_simd_classes_per_cycle on how many classes one cycle may drive. The wiring block (scheme, electrodes "
         "per trap and junction, DACs) and the channel map say what the evaluation's hardware metrics count.",
         "This is where broadcast lives. One cycle, one waveform (rule R22): every ion that moves in a cycle moves "
         "the same way, and a site may only opt out. The classes are the ways there are to move; the cycle bound "
         "says how many at once."))
    w(eg("The classes <code>ring6d</code> declares: the two rotations of L0, the dock and undock along the spurs, two "
         "sorting shifts, and the plain shuttle; one class per cycle."
         f"<pre class=\"ex\"><code>{esc(_classes_lines(ring))}</code></pre>"))
    w(learn.bridge("That is the whole syntax: a graph with named paths, a zone table, a price table, a control plane, "
                   "three small records. Not every text in this grammar is a machine. The next section says which are."))

    # ------------------------------------------------------------------ 3. well-formed
    w(f'<h2 id="well-formed">3. {HEADINGS[2][1]}</h2>')
    w(learn.section("the static semantics: the checks a document must pass to be a machine, each with the sentence "
                    "the loader prints when it fails. The sentences below were produced by breaking the register in "
                    "one place and asking the loader."))
    w(learn.ask("A document can name a rail to a node that does not exist, or a loop of one node. Who refuses it, and when?",
                "The loader, before anything else runs. A machine is loaded once; if any check below fails, there is no "
                "machine, so no programme can be written on it and no rule can be asked about it. The checks are "
                "structural: they read the document and nothing else."))
    w(df("Definition M9", "well-formed machine",
         "A document is a <i>well-formed machine</i>, written ⊢ <var>M</var> ok, when it has the shape of the grammar "
         "and: (1) every end of a rail is a node of <var>N</var>; (2) a rail joins two different nodes; (3) at most one "
         "rail joins a pair of nodes; (4) a loop has at least two nodes, (5) visits no node twice and (6) has a rail "
         "between each consecutive pair; (7) a site has capacity ≥ 1 and (8) a bare junction capacity 0; (9) every "
         "zone a site names is declared in <var>Z</var>; (10) a derived field cached in the file (degree, corner) "
         "agrees with the graph. The loader runs all ten on every load, and a programme or a rule only ever sees a "
         "machine that passed.",
         "Ten sentences, and a hand-edited file cannot quietly disagree with its own graph. The last check is what "
         "makes the cached fields safe to read."))
    rows = [[f"<b>{j}</b>", f"<code>{esc(what)}</code>", f"<i>{esc(msg)}</i>"] for j, what, msg in broken_messages()]
    w(eg("The register, broken ten ways, and what the loader says each time. Every message names the offending "
         "object, so the line to fix is the line it names." + tbl(["the check", "what was written", "the loader says"], rows)))
    w("<p>Three more checks read the positions rather than the graph, and they are rules of the rule language, so "
      "that a machine model may set their numbers: R19 bounds the degree of a node (six unless the document says "
      "fewer; a planar chip says four), R20 requires two rails meeting at a node to subtend at least a minimum "
      "angle (60 degrees by default), and R21 requires rails to be planar, meeting only the nodes they end at and "
      "crossing only at a shared node. A Tanner graph laid out as rails, or a torus drawn flat with its wrap wires "
      "across the die, is a well-formed document and not a surface trap; these three say so, from the drawing alone. "
      "The <a href=\"../rules/#R19\">Rules</a> page shows each with a machine that keeps it and one that breaks it.</p>")
    w(learn.bridge("A well-formed machine is a graph with tables. Most of what the other pages talk about, junctions, "
                   "corners, slots, hops, is in none of those tables. It is derived, as the next section defines."))

    # ------------------------------------------------------------------ 4. derived
    w(f'<h2 id="derived">4. {HEADINGS[3][1]}</h2>')
    w(learn.section("the notions computed from the geometry that every downstream reader uses: degree and junction, "
                    "bend and corner, slot and shift map, hop. Each is a definition, a picture, and the values on "
                    "the six machines."))
    w(learn.ask("The document never says “this node is a junction”. How does the rule that prices a junction crossing know where one is?",
                "By counting. A junction is a node where three or more rails meet, and the price is read at that "
                "count. Attach a spur to a loop site and it becomes a junction with no word changed; the cost model "
                "finds out by counting, not by being told."))
    w(df("Definition M10", "degree and junction",
         "The <i>degree</i> of a node is the number of rails incident to it, deg(<var>n</var>) = |{<var>e</var> ∈ "
         "<var>E</var> : <var>n</var> ∈ ends(<var>e</var>)}|. A node is a <i>junction</i> when deg(<var>n</var>) ≥ 3, "
         "whatever its kind (rule R18). A site of degree ≥ 3 is a junction an ion may also stand on; a bare "
         "junction of any degree is a node no ion stands on.",
         "Junctions are found, not declared. That is why a dock spur is expensive: it makes the loop site it meets a "
         "degree-3 node, and every rotation through that site now pays a junction crossing."))
    w('<div class="strip">'
      + dev(grid, "grid2x3: the two T-nodes J0_1 and J1_1 have degree 3 and are junctions; the four corners have degree 2 and are not", marks=degree_marks(grid))
      + dev(ring, "ring6d: S0 and S3 have degree 3 once the spurs are attached; the other loop sites have degree 2", marks=degree_marks(ring))
      + dev(grid3, "grid3x3: the centre J1_1 has degree 4, an X-junction; the edge midpoints degree 3", marks=degree_marks(grid3))
      + "</div>")
    w(df("Definition M11", "bend and corner",
         "A <i>corner</i> of a loop is a node at which the loop changes direction: for consecutive nodes "
         "<var>p</var>, <var>n</var>, <var>q</var> of the loop, the unit vector from <var>p</var> to <var>n</var> "
         "differs from that from <var>n</var> to <var>q</var>. A <i>bend</i> is a node of degree exactly 2 at which "
         "the rail turns: a corner of a loop, or a degree-2 node whose two rails are not collinear. A bend is not a "
         "junction (rule R18): an ion is carried round it at the price of ordinary rail. A bend that is a <b>site</b> "
         "(the corners of ring6d) is a slot like any other; a bend that is a <b>bare junction</b> (the corners of a "
         "lattice, or of a ring built with corners = \"junction\") is no slot: no ion stops there.",
         "A rail that turns is still one rail to the ion. On a ring built with junction corners, one slot of rotation "
         "carries an ion from the last trap before the corner, round it, to the first trap after; nothing is "
         "charged for the turn, because there is no barrier in a continuous RF null."))
    w(df("Definition M12", "slots and the shift map",
         "The <i>slots</i> of a loop are its nodes in order, less the bends that are bare junctions: the places an "
         "ion stands on the loop. For "
         "a closed loop with slots ⟨<var>s</var>₀, …, <var>s</var><sub>k−1</sub>⟩ the <i>shift map</i> by δ is "
         "shift<sub>δ</sub>(<var>s</var><sub>i</sub>) = <var>s</var><sub>(i+δ) mod k</sub>. One rotation statement "
         "applies this map to every ion on the loop at once; it is the single movement template rigid rotation needs.",
         "The shift map is the conveyor belt. Its domain is the slots, so a bend is passed and never landed on, and "
         "its size is the loop's length, so turning a loop of 144 ions is one instruction."))
    w('<div class="strip">'
      + dev(race, "race8: the eight slots of L0 and shift₊₁, every ion one slot clockwise in one cycle", paths=shift_paths(race, "L0", 1))
      + dev(ring, "ring6d: L0 has six slots; S2 and S5 are its degree-2 corners, S0 and S3 its corners that are also junctions", hi={"S2", "S5"})
      + "</div>")
    w(df("Definition M13", "hop",
         "Two sites <var>a</var>, <var>b</var> are <i>one hop apart</i> when there is a path of rails from <var>a</var> "
         "to <var>b</var> whose interior nodes are all junctions (bare, or sites of degree ≥ 3), of at most three "
         "interior nodes. hops(<var>M</var>) is the set of such ordered pairs. A transport cycle of a programme is one "
         "hop per moving ion; a rotation is one shift.",
         "An ion leaves a trap, crosses junctions, lands on a trap, all in one cycle: that is a hop. The compiler "
         "plans in hops, the proved checker is handed the hop set read off the document, and a programme that asks "
         "for a pair not in it is refused before any rule is consulted (next section)."))
    w(dev(grid, "grid2x3: the hops out of T0_1h, each through one T-junction; the lattice corners are degree 2 and are passed as ordinary rail",
          fit=(420, 230), paths=[{"nodes": ["T0_1h", "J0_1", "T0_0v"], "color": "#1c6fd1", "label": ""},
                                 {"nodes": ["T0_1h", "J0_1", "T0_1v"], "color": "#0b7a4b", "label": ""},
                                 {"nodes": ["T0_1h", "J1_1", "T1_0v"], "color": "#d97706", "label": ""},
                                 {"nodes": ["T0_1h", "J1_1", "T1_1v"], "color": "#7c3aed", "label": ""}]))
    w(eg("The derived notions on the six machines of this page, read off their documents when the site was built."
         + derived_table(ms)))
    w(learn.bridge("With the derived notions in hand, the question of meaning can be asked precisely: what does a "
                   "machine determine about the programmes written on it?"))

    # ------------------------------------------------------------------ 5. meaning
    w(f'<h2 id="meaning">5. {HEADINGS[4][1]}</h2>')
    w(learn.section("the semantics: a machine fixes the state space a programme steps, the alphabet a programme's "
                    "statements are written in (so that a statement can be ill-typed on one machine and fine on "
                    "another), and the price list the replay reads. The Language page's semantics then takes over."))
    w(learn.ask("The Language page says a programme steps a state of three parts. Where does that state live?",
                "In the machine. The positions are a map into <var>M</var>'s sites, the moves are along <var>M</var>'s "
                "rails and loops, the prices are <var>M</var>'s curves. Change the machine and the same programme "
                "text means a different run, or none."))
    w(df("Definition M14", "state space",
         "The <i>state space</i> of a machine is Σ<sub><var>M</var></sub> = (Ion ⇀ sites(<var>M</var>)) × "
         "(Ion → ℝ<sub>≥0</sub>) × ℝ<sub>≥0</sub>: a partial map from ions to sites (where every ion is), a heat "
         "n̄ per ion in motional quanta, and the clock. The initial state is empty; <code>p.init</code> fills the map. "
         "A state is <i>admissible</i> when it respects <var>M</var>: |pos⁻¹(<var>n</var>)| ≤ cap(<var>n</var>) for "
         "every site, and no ion stands on a bare junction.",
         "Three things: where, how hot, what time. The machine supplies the set of places and the limits; the "
         "programme supplies the moves."))
    w(fig_state(chain))
    w('<p class="notecap">The state after <code>p.init({"d0": "C1", "d1": "C2"})</code> and one hop on the register: '
      'a position for each ion, a heat for each, a clock. Every statement of the hardware language is a step '
      'Σ<sub><var>M</var></sub> → Σ<sub><var>M</var></sub>; the <a href="../language/#semantics">Language</a> page gives '
      'the step of each.</p>')
    w(df("Definition M15", "well-typed statement",
         "A statement is <i>well-typed on <var>M</var></i> when every name in it is one <var>M</var> declares and every "
         "relation it asserts holds in <var>M</var>: an <code>init</code> names sites; a <code>move</code> from "
         "<var>a</var> to <var>b</var> requires (<var>a</var>, <var>b</var>) ∈ hops(<var>M</var>) and a declared class; "
         "a <code>rotate</code> names a closed loop of <var>L</var>; a <code>gate</code> on a site requires "
         "can(site, gate). A programme is well-typed when every statement is. Well-typed is not legal: a well-typed "
         "programme is then replayed and every cycle judged by the rules; an ill-typed one is refused by the replay "
         "itself, with the name it could not find.",
         "Typing is the machine's part of the bargain, rules are the physics' part. “No rail between S0 and S3” is "
         "a type error, “two ions on a rail of capacity one” is a rule failing (R3)."))
    w(eg("On <code>ring6d</code>, <code>p.move(\"d0\", \"S0\", \"S1\")</code> is well-typed: S0 and S1 are sites and "
         "one rail joins them. <code>p.move(\"d0\", \"S0\", \"S3\")</code> is not: no rail joins them, and the replay "
         "stops with <i>no segment between 'S0' and 'S3'</i> before any rule is asked. "
         "<code>p.gate(\"CX\", [[\"d0\", \"d1\"]], sites=[\"S1\"])</code> is well-typed (S1 is a site) and illegal "
         "(can(S1, gate) is false), which rule R6 reports with the site's name."))
    w("<p>What the replay reads from <var>M</var>, step by step: for a transport cycle, the rails crossed give the "
      "primitives used (a shuttle per rail, a junction_cross at the degree of every junction transited, a split and a "
      "merge when the class entails them), and the policy picks a point of each curve, so the cycle's duration and "
      "heat are sums of operating points; for a gate, the gate primitive's duration, and the error read against each "
      "ion's n̄; for cooling, the cooling primitive. The <a href=\"../evaluation/\">Evaluation</a> page is the "
      "catalogue of the numbers so obtained.</p>")

    # ------------------------------------------------------------------ 6. readers
    w(f'<h2 id="readers">6. {HEADINGS[5][1]}</h2>')
    w(learn.section("one table: which part of the machine each of the five readers takes, and the name that reader "
                    "gives its projection. The point is that there is one text and five projections, never five "
                    "descriptions of the hardware."))
    w(fig(fig_readers(), "One machine, five readers. Each reads a projection of <var>M</var>: the Language its names "
                         "and relations, the Rules the Hardware record, the compiler the trap graph, the Evaluation "
                         "the tables, the proved checker three facts read from the document and never from the compiler."))
    w(tbl(["reader", "what it reads of M", "what it calls it", "where"], [
        ["<b>Language</b>", "sites, hops, closed loops, declared classes, abilities", "the names a statement may use (M15)", '<a href="../language/#semantics">Language · semantics</a>'],
        ["<b>Rules</b>", "sites, junctions, rails, capacity, degree, zone, can, ends, rails_at, loops, the declared numbers", "the device vocabulary: the Hardware record", '<a href="../rules/#words">Rules · the words</a>'],
        ["<b>Compiler</b>", "traps T, junctions J, rails E, cap, gate, meas, closed loops L; the curves, for cooling insertion", "the trap graph A(M), Definition 1 there", '<a href="../compilation/#def-device">Compilation · 5.1</a>'],
        ["<b>Evaluation</b>", "the curves under a policy, the wiring and budget, the heating rate, the species", "the device's tables, the device's wiring, the device's laws", '<a href="../evaluation/#words">Evaluation · the words</a>'],
        ["<b>Proved checker</b>", "gate-capable sites, hops(M), each closed loop's slots", "gateSites, hops, loops: facts the compiler cannot widen", '<a href="../compilation/#correctness">Compilation · 5.9</a>'],
    ]))
    w(eg("The three projections of <code>ring6d</code>, read when the site was built."
         f"<p><b>The Hardware record the rules read</b> (field, meaning, value here):</p>"
         + tbl(["field", "meaning", "on ring6d"], hardware_record(ring))
         + f"<p><b>The trap graph the compiler reads:</b> {compiler_tuple(ring)}.</p>"
         + f"<p><b>What the proved checker is handed:</b> {esc(lean_facts(ring))}.</p>"))
    w(learn.bridge("Five readers, one text. The next section reads six machines as texts, each in the four forms of "
                   "section 1, and each playable."))

    # ------------------------------------------------------------------ 7. examples
    w(f'<h2 id="examples">7. {HEADINGS[6][1]}</h2>')
    w(learn.section("the six machines the examples of this website run on, each shown as its drawing, its generator "
                    "line and what that line expands to, with a short programme on it you can play. Pressing Play opens "
                    "the machine's Studio page, whose Hardware column is this machine as a programme, line by line."))
    for key, dev_dict, label, role in MACHINES:
        m = ms[key]
        d = m.arch.device
        sm = d.summary()
        card = built.get(key, {})
        geo = listing_text(m, "geometry", banners=False)
        w(f'<h3 id="m-{key}">{html.escape(label)}: <code>{key}</code></h3>')
        w('<div class="mcard">')
        w(f'<div class="mleft">{dev(m, html.escape(dev_dict["about"]), fit=(360, 220))}</div>')
        w('<div class="mright">')
        w(f'<p class="role">{html.escape(role)}.</p>')
        w(f'<pre class="ex"><code>{esc(geo)}</code></pre>')
        w(f'<p class="facts">Expands to {sm["n_nodes"]} nodes ({sm["n_sites"]} sites, total capacity {sm["total_capacity"]}), '
          f'{sm["n_segments"]} rails, {sm["n_loops"]} loop{"s" if sm["n_loops"] != 1 else ""}; '
          f'junctions {_names(d.junction_nodes)}; corners {_names(sorted(d.all_corners))}.</p>')
        if card:
            w(f'<pre class="ex prog"><code>{esc(card["text"])}</code></pre>')
            v = card["verdict"]
            w(f'<div class="verdict">well-typed and legal on {key} · cost {v["cost"]:g} · steps {v["steps"]} · {v["us"]:g} µs · peak n̄ {v["peak_quanta"]:.2f}</div>')
        w("</div></div>")
        if card:
            w(learn.runbox(card["page"], card["poster"], f"{key}: the programme, played on its machine",
                           what="the programme on this machine; its Studio page shows the machine as a programme in the Hardware column"))
            w(f'<a class="open" href="{card["page"]}#step=1">open the page, with its Report pane</a>')
    w(learn.bridge("Five of the six were written by a generator from two or three numbers. The next section says what "
                   "a generator is, and why the compact form is only kept when it is honest."))

    # ------------------------------------------------------------------ 8. generators
    w(f'<h2 id="generators">8. {HEADINGS[7][1]}</h2>')
    w(learn.section("what a generator is (a function from parameters to an explicit geometry), the seven shipped "
                    "ones, and the rule that decides whether a file may store the parameters instead of the graph."))
    w(learn.ask("If a ring of 144 slots and 24 docks is stored as two numbers, what happens when someone drags one site?",
                "The two numbers stop describing it. So a document stores its generator and parameters only while "
                "re-running them reproduces the graph exactly; the moment it does not, the file is written in the "
                "explicit form. Nothing downstream ever reads the parameters: every reader takes the expanded graph."))
    w(df("Definition M16", "generator",
         "A <i>generator</i> is a function gen : Params → Geometry from a record of parameters to an explicit "
         "geometry (<var>N</var>, <var>E</var>, <var>L</var>). A document whose geometry is compact, "
         "generator(params), denotes gen(params). A geometry <var>G</var> is <i>reproducible</i> from (gen, params) "
         "when gen(params) = <var>G</var> exactly; the writer stores the compact form only then, and the explicit form "
         "otherwise, so that no file ever loads as a different machine from the one that was saved.",
         "A generator is a recipe; the graph is the dish. The file keeps the recipe only while it still makes this dish."))
    w(tbl(["generator", "parameters", "what it writes"], [
        ["<code>ring</code>", "width, height, verticals, dock_offset, corners", "one closed loop of 2W + 2H − 4 slots (W, H ≥ 2), V spurs to dock sites, the first at slot dock_offset; with corners = \"junction\", four bends at the corners and traps only on the sides"],
        ["<code>racetrack</code>", "straight", "one closed loop of 2·straight trap sites and no junction: every bend is ordinary rail"],
        ["<code>grid</code>", "a, b", "an a × b lattice of nodes with a trap in the middle of every wire: interior nodes degree 4, edge nodes 3, corners 2"],
        ["<code>chain</code>", "n", "n sites in a line, one open loop, two ends of degree 1"],
        ["<code>ladder</code>", "width, rungs, highways", "two rails joined by rungs"],
        ["<code>dual_loop</code>", "width, couplings, corners", "an inner loop and an outer loop, joined by a rail at each coupling, where two sites become junctions"],
        ["<code>cylinder</code>", "a, b", "the planar drawing of a periodic grid: the wrap in one direction, so that R21 holds"],
    ]))
    rings = [("ring(3, 2, 2)", ring), ("ring(4, 2, 2)", _ring(4, 2, 2)), ("ring(5, 3, 4)", _ring(5, 3, 4))]
    w(eg("One generator, three parameter choices. The slot count, the dock positions and therefore the junctions all "
         "follow from the numbers; the document stores the numbers, and the loader writes the graph."
         + '<div class="strip">' + "".join(dev(m_, f"{t}: {m_.arch.device.summary()['n_sites']} sites, "
                                                   f"{len(m_.arch.device.junction_nodes)} junctions", fit=(240, 180)) for t, m_ in rings) + "</div>"))
    w(learn.bridge("Generators write most machines. The last section is for the ones they cannot: how a machine is "
                   "written by hand, as the programme the Design page edits."))

    # ------------------------------------------------------------------ 9. writing
    w(f'<h2 id="writing">9. {HEADINGS[8][1]}</h2>')
    w(learn.section("the listing as a language: its statements (twelve declarations, three edits, five builder verbs, "
                    "four seeds) and the part of M each writes, the grammar's "
                    "one deliberate absence (no expressions), a machine written by hand from three traps and a "
                    "junction, and where to type one."))
    w(learn.ask("Why is the editing language a list of method calls rather than a form or a JSON editor?",
                "Because a list of statements can be read top to bottom, run, and diffed. What the Design page shows is "
                "exactly what a Python session prints for the same machine and exactly what it executes; copy the "
                "panel into a file and it runs. A form cannot say that, and a JSON editor shows data, not the "
                "order in which a machine is made."))
    w(df("Definition M17", "listing",
         "A <i>listing</i> of a machine <var>M</var> is a programme in the grammar below whose statements, run in order "
         "from an empty machine, construct <var>M</var>: ⟦listing(<var>M</var>)⟧ = <var>M</var>. The grammar has no "
         "expression production: a value is a literal, a list, a dictionary or <code>dict(…)</code>, never an "
         "operator, an attribute or a call, and the method names are checked against the table below when the text "
         "is parsed. A listing is <i>self-contained</i> when it names no template: every zone, curve and class is stated.",
         "A listing is a programme whose output is a machine. The site treats the equation as a test: every listing "
         "shown on this page was run again and compared with its machine."))
    w(f'<pre class="grammar"><code>{esc(LISTING_GRAMMAR)}</code></pre>')
    w(tbl(["statement", "writes", "which part of M"], [
        ['<code>m = Machine.blank(gen, …, zones=[…])</code>', "the geometry from a generator, and the zone names it may use", "G (compact), dom Z"],
        ['<code>d = DeviceBuilder(); d.site(id, x, y, zone=…, capacity=…)</code>', "one site", "N"],
        ['<code>d.junction(id, x, y)</code>', "one bare junction", "N"],
        ['<code>d.segment(id, a, b, loop=…, length=…, capacity=…)</code>', "one rail", "E"],
        ['<code>d.loop(id, [nodes], closed=…, kind=…)</code>', "one loop", "L"],
        ['<code>m = Machine.blank_device(d.build(), …)</code>', "seals an explicit geometry into a machine", "G (explicit)"],
        ['<code>m.set_zone(zone, capacity=…, gate=…, spam=…, cool=…)</code>', "one zone type", "Z"],
        ['<code>m.set_site_capacity(sites, n)</code>', "a capacity a site keeps when its zone is retuned", "cap"],
        ['<code>m.set_curve(primitive, [dict(us=…, quanta=…, table=…), …])</code>', "a transport primitive's operating points", "Π"],
        ['<code>m.set_degree_curve(primitive, degree, [...])</code>', "a junction crossing's curve at one degree", "Π"],
        ['<code>m.set_primitive(primitive, us=…, fidelity=…, …)</code>', "a gate or readout primitive's scalars", "Π"],
        ['<code>m.declare_class(id, type=…, orbit=…, delta=…, entails=[…])</code>', "one movement class", "C"],
        ['<code>m.set_control(model=…, max_simd_classes_per_cycle=…, channels=…)</code>', "the control model and its bounds", "C"],
        ['<code>m.set_wiring(scheme=…, dacs_dynamic=…, electrodes_per_trap=…)</code>', "the wiring block", "C"],
        ['<code>m.set_heating(…)</code>, <code>m.set_species(…)</code>, <code>m.set_budget(…)</code>, <code>m.describe(…)</code>', "the three records and the description", "H, S, B, name"],
        ['<code>m.move_site(site, x, y)</code>, <code>m.set_segment_length(…)</code>', "edits to a geometry already sealed", "N, E"],
    ]))
    tee_card = built.get("tee", {})
    w(eg("A machine written by hand: three traps A, B, C around one junction J, a T. Four builder statements place "
         "the nodes, three the rails, one seals the geometry; the zones, curves and control plane are then stated as "
         "on any other machine (here borrowed from the default template, so the machine can be played). The loader "
         "derives deg(J) = 3, so J is a junction and a hop from A to B crosses it at the degree-3 price."
         f"<pre class=\"ex\"><code>{esc(listing_text(tee, 'geometry', banners=False))}</code></pre>"
         + '<div class="strip">' + dev(tee, "tee: N = {A, B, C, J}, J a bare junction of degree 3; no loop", fit=(300, 200), marks=degree_marks(tee))
         + "</div>"
         + (f"<pre class=\"ex prog\"><code>{esc(tee_card['text'])}</code></pre>"
            f"<div class=\"verdict\">well-typed and legal on tee · cost {tee_card['verdict']['cost']:g} · steps {tee_card['verdict']['steps']} · "
            f"{tee_card['verdict']['us']:g} µs · peak n̄ {tee_card['verdict']['peak_quanta']:.2f}</div>" if tee_card else "")))
    if tee_card:
        w(learn.runbox(tee_card["page"], tee_card["poster"], "tee: two ions brought through the junction to one trap, then gated",
                       what="the programme on the hand-written machine; its page's Hardware column is the listing above"))
        w(f'<a class="open" href="{tee_card["page"]}#step=1">open the page, with its Report pane</a>')
    w("<p><b>Where to write one.</b> On the <a href=\"../design/\">Design</a> page: its Parts mode places sites, "
      "junctions and rails with the pointer, its Sketch mode turns a drawn shape into sites and rails, and its text "
      "lane shows the listing and accepts edits to it, all three writing the same statements. In Python, the same "
      "statements on a <code>Machine</code>, then <code>m.save(path)</code> writes the document and "
      "<code>m.listing()</code> prints the programme. In a workspace, the agent edits machines through the same "
      "statements. Whichever way, the result is a document that the loader checks (section 3) before any "
      "programme runs on it.</p>")

    # ------------------------------------------------------------------ conclusion
    w(f'<h2 id="conclusion">{HEADINGS[9][1]}</h2>')
    w("<p>The machine is the object the rest of the Learn pages had been using without defining. It is a text with "
      "a grammar (section 2) and a static semantics (section 3); it determines a graph and a set of derived facts "
      "(section 4); it fixes the state space, the alphabet and the price list of every programme written on it "
      "(section 5); and it is read by five readers as five projections of one thing (section 6). The same text can "
      "be stored as data or shown as a programme, and the two are checked to agree (sections 1 and 9).</p>")
    w("<p>Read the next pages with this in hand. On the <a href=\"../language/\">Language</a> page every statement "
      "is a step of Σ<sub><var>M</var></sub>, well-typed only in <var>M</var>'s names. On the "
      "<a href=\"../rules/\">Rules</a> page every rule quantifies over sets <var>M</var> defines (sites, rails, "
      "junctions) and reads numbers <var>M</var> declares (capacity, the geometry limits), beside the sets a run "
      "defines (operations, instants). On the <a href=\"../compilation/\">Compilation</a> page the compiler's first "
      "definition is the trap graph A(<var>M</var>), a projection of <var>M</var>, and its proved checker is handed "
      "three facts read off <var>M</var>'s document. On the <a href=\"../evaluation/\">Evaluation</a> page the three "
      "texts a number is a function of are <var>M</var>, a programme and an evaluation.</p>")
    w("</div>")  # .algo
    w(learn.recap([
        "A machine is a text: a document with the grammar of section 2, loaded once into an expanded graph with "
        "tables. The same text prints as a listing, a programme of method calls that rebuilds it, and the "
        "two are checked to agree.",
        "A document is a machine only if it passes ten structural checks (section 3); three more rules read the "
        "positions. A programme or a rule never sees a document that failed.",
        "Junctions, bends, corners, slots, the shift map and hops are derived from the geometry, never declared: a "
        "junction is a node of degree three or more, a hop is a trap-to-trap path through junctions only.",
        "A machine fixes the state space a programme steps (positions, heat, clock), the names a statement may use "
        "(well-typed), and the prices a replay reads (curves under a policy). Ill-typed is refused by the replay; "
        "illegal is a rule failing.",
        "Five readers, one text: the language, the rules, the compiler, the evaluation and the proved checker each "
        "take a projection of the one machine.",
    ], "machine", more="The Language page is next: the statements a programme is made of, each a step of the state "
                       "space this page defined, on the machines this page drew."))
    body = "".join(b)
    css = ALGO_CSS + CSS + learn.LAYOUT_CSS
    return PAGE.format(title="Machine - QCCD studio", style=STYLE, extra_css=css, body=learn.apply(body, "machine/", 1))


LISTING_GRAMMAR = """listing   ::= (statement | comment | blank)*
statement ::= "m" "=" "Machine" "." seed "(" args ")"            -- seed: blank | blank_device | from_device | from_template
            | "m" "." method "(" args ")"                        -- one of the twelve setters, or an edit
            | "d" "=" "DeviceBuilder" "(" args ")"               -- an explicit geometry begins
            | "d" "." builder "(" args ")"                       -- site | junction | segment | loop
args      ::= [ arg ("," arg)* [","] ]
arg       ::= name "=" value | value
value     ::= string | number | "True" | "False" | "None" | "[" values "]" | "{" pairs "}" | "dict" "(" args ")"
-- no operator, no attribute, no arithmetic, no control flow; a logical line continues while a bracket is open"""


def _ring(width: int, height: int, verticals: int):
    from ..api import Machine
    return Machine.ring(width, height, verticals, name=f"ring{width}x{height}v{verticals}")


def _chain_json(m) -> str:
    import json
    j = m.arch.to_json(expanded=True)["geometry"]
    nodes = [{k: v for k, v in n.items() if k in ("id", "pos", "kind", "capacity", "zone_type", "degree", "corner")} for n in j["nodes"]]
    segs = [{k: v for k, v in s.items() if k in ("id", "ends", "length", "capacity", "loop")} for s in j["segments"]]
    text = json.dumps({"nodes": nodes[:2], "segments": segs[:1], "loops": j["loops"]}, indent=1)
    # shorten: the first two nodes and the first rail stand for the rest
    text = text.replace('"segments": [', '"segments": [   // E1, E2 alike').replace('"nodes": [', '"nodes": [   // C2, C3 alike')
    return '"geometry": ' + text


def _curve_lines(m, primitive: str) -> str:
    from ..arch.listing import architecture_listing
    L = architecture_listing(m.arch, mode="full")
    for ln in L.lines:
        if ln.kind == "call" and ln.text.startswith(f'm.set_curve("{primitive}"'):
            return "\n".join(_strip_note(t).rstrip() for t in ln.text.split("\n"))
    return f'm.set_curve("{primitive}", [...])'


def _classes_lines(m) -> str:
    from ..arch.listing import architecture_listing
    L = architecture_listing(m.arch, mode="full")
    out = []
    for ln in L.lines:
        if ln.kind == "call" and (ln.text.startswith("m.declare_class(") or "max_simd_classes_per_cycle" in ln.text):
            t = _strip_note(ln.text)
            # the long notes inside a class declaration are documentation; keep the fields
            import re
            t = re.sub(r', note="[^"]*"', "", t)
            out.append(t.rstrip())
    return "\n".join(out)


def index_entries() -> list[dict]:
    out = [{"t": "Machine", "d": "a QCCD machine as a text: its grammar, well-formedness, derived notions and meaning",
            "u": "machine/", "k": "page"}]
    out += [{"t": t, "d": "Machine", "u": f"machine/#{a}", "k": "page"} for a, t in HEADINGS]
    out += [{"t": f"{k} · {n}", "d": "Machine · a definition", "u": f"machine/#{i}", "k": "page"} for (k, n), i in IDS.items()]
    out += [{"t": f"machine {key}", "d": role[:90], "u": f"machine/#m-{key}", "k": "page"} for key, _, _, role in MACHINES]
    return out


CSS = """
.grammar{font-size:12.5px;line-height:1.6;white-space:pre;overflow-x:auto;background:#faf9f5;border:1px solid var(--line);border-radius:10px;padding:12px 16px}
.algo pre.ex{white-space:pre;overflow-x:auto;font-size:11.5px;line-height:1.55}
.algo pre.ex.prog{background:#eef4fb}
.mpage table.sm{width:100%} .mpage table.sm td,.mpage table.sm th{white-space:normal;vertical-align:top;padding:4px 12px 4px 0}
.mpage table.sm td:first-child{color:var(--ink)}
.mcard pre.ex{white-space:pre-wrap;word-break:break-word}
.mcard{display:grid;grid-template-columns:minmax(0,380px) minmax(0,1fr);gap:16px;align-items:start;margin:8px 0 6px}
.mcard .mleft figure.dg{margin:0} .mcard .role{margin:0 0 8px;font-weight:600;color:#1c2a4a}
.mcard .facts{font-size:13px;color:var(--ink2);margin:4px 0 8px}
.mcard .verdict{font-family:var(--sans);font-size:12.5px;color:#0b7a4b;margin:4px 0 0}
@media (max-width:760px){.mcard{grid-template-columns:minmax(0,1fr)}}
.statefig{display:grid;grid-template-columns:minmax(0,320px) minmax(0,1fr);gap:14px;align-items:center;margin:10px 0 4px;padding:10px 12px;background:var(--card);border-left:3px solid #0b7a4b}
.statefig .stateparts div{display:grid;grid-template-columns:56px 1fr;gap:2px 10px;margin:0 0 8px;font-size:13.5px}
.statefig .stateparts b{font-family:var(--serif);font-size:15px;color:#1c2a4a} .statefig .stateparts small{grid-column:2;color:var(--ink3);font-size:12px}
@media (max-width:640px){.statefig{grid-template-columns:minmax(0,1fr)}}
.algo a.open{display:inline-block;font-family:var(--sans);font-size:12.5px;margin:4px 0 14px}
"""
