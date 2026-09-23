"""Design rules over the drawn metal -- and one number that is deliberately not a rule.

**This is not a verifier rule.**  Nothing here is added to `RULE_STATEMENTS`,
`architecture_violations` or `BROWSER_SET`.  Those are mirrored in `engine.js` and diffed
at tolerance zero over every architecture file, so a Python-only rule firing there is an
automatic red harness.  A design-rule check is also a different kind of claim: it is about
a technology's fabrication limits, which the document does not declare and the browser
cannot know.

**Union by net, then compare across nets.**  Two perpendicular RF rails necessarily
overlap at every degree-4 node.  On one net that is a merge, not a spacing violation --
and unioning first also stops one long rail, drawn as many rectangles, from reporting the
same neighbour a hundred times.  So each net becomes a set of disjoint rectangles and only
different nets are compared.

**Counted versus declared is a disclosure, not a verdict.**  `qccd/cost/hardware.py`
prices `control.wiring.electrodes_per_trap = 24`; this package draws nine control
electrodes per trap pitch.  Those numbers disagree, and the disagreement proves nothing:
the 24 is an authored integer that may well include shim and compensation electrodes this
package does not draw, and the nine follows from a `dc_pitch` and a lattice scale that are
themselves a technology's claims.  **Neither side is measured.**  So the report prints both,
prints the axial trap pitch each implies, and stops.  Turning that into a pass/fail would
be inventing a fact out of two conventions.

What *is* a verdict: metal narrower than the process allows, two nets closer than it
allows, RF welded to a control electrode, two electrodes drawn on top of each other, and a
trapping site with fewer control electrodes under it than the technology says every site
gets.  Those are failures of the drawn geometry against a declared limit, and they are
reported as violations.

**The last two are the collaborator's rules, checked where they can be measured.**
`qccd/phys/tech.py` enforces the minimums of `n_dc_pairs`, `w_rf`, `w_dc`, `l_dc`, `g_dc`
and `g_rf` when a technology file loads -- that is a statement about the technology.  Here
the same rules are checked against the metal a device actually got: `overlap` is the hard
one (two electrodes sharing area are one electrode, whatever the netlist says), and
`dc_pairs_per_site` names each site that came up short rather than reporting an average.
`rf_dc_clearance` now measures against `g_rf` rather than the DC gap, which is the whole
reason those were split into two numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Mapping, Sequence

from .build import dc_pairs_by_site, unconnected_crossings
from .shapes import Layout, Violation, min_width_violations
from .tech import Technology

__all__ = ["Disclosure", "DRCReport", "check", "checked", "RULES"]

#: Every rule this module can report.  Closed, so a report can be read without the code.
RULES: tuple[str, ...] = ("min_width", "min_gap", "rf_dc_clearance", "overlap",
                          "dc_pairs_per_site")

#: Rules whose numbers are counts rather than nanometres, so a report does not print
#: "1 nm against 3 nm" for three electrode pairs.
COUNT_RULES: frozenset[str] = frozenset({"dc_pairs_per_site"})


@dataclass(frozen=True)
class Disclosure:
    """Two numbers that disagree, where neither is measured.  Printed, never judged."""

    topic: str
    statement: str
    counted: object = None
    declared: object = None

    def as_dict(self) -> dict:
        return {"topic": self.topic, "statement": self.statement,
                "counted": self.counted, "declared": self.declared}


@dataclass(frozen=True)
class DRCReport:
    technology: str
    device: str
    n_polys: int
    violations: tuple[Violation, ...] = ()
    disclosures: tuple[Disclosure, ...] = ()
    #: how many net pairs were actually compared, so a vacuous pass is visible
    compared: int = 0

    def by_rule(self) -> dict[str, int]:
        out = {r: 0 for r in RULES}
        for v in self.violations:
            out[v.rule] = out.get(v.rule, 0) + 1
        return out

    @property
    def clean(self) -> bool:
        return not self.violations

    def as_dict(self) -> dict:
        return {"technology": self.technology, "device": self.device,
                "n_polys": self.n_polys, "compared": self.compared,
                "by_rule": self.by_rule(), "clean": self.clean,
                "violations": [v.as_dict() for v in self.violations],
                "disclosures": [d.as_dict() for d in self.disclosures]}

    def text(self, limit: int = 8) -> str:
        lines = [f"DRC {self.device} under {self.technology}: "
                 f"{self.n_polys} polygons, {self.compared} net pairs compared"]
        counts = self.by_rule()
        for rule in RULES:
            lines.append(f"  {rule:18s} {counts.get(rule, 0)}")
        shown = 0
        for v in self.violations:
            if shown >= limit:
                lines.append(f"  ... and {len(self.violations) - shown} more")
                break
            lines.append("    " + _violation_line(v))
            shown += 1
        for d in self.disclosures:
            lines.append(f"  [disclosure] {d.topic}")
            for line in d.statement.splitlines():
                lines.append(f"      {line}")
        return "\n".join(lines)


def _violation_line(v: Violation) -> str:
    """One finding, in the units it is actually in.

    A count rule reported in nanometres reads "1 nm against 3 nm" for one electrode pair
    against three, which is the kind of line that makes a reader distrust the whole report.
    """
    where = f"near ({v.where[0]}, {v.where[1]})"
    if v.rule in COUNT_RULES:
        pair = "pair" if v.measured_nm == 1 else "pairs"
        return (f"{v.rule} at {' and '.join(v.owners)}: {v.measured_nm} DC {pair} "
                f"against {v.required_nm} required, {where}")
    if v.rule == "overlap":
        return (f"{v.rule} on {v.layer}: {v.measured_nm} nm of metal SHARED by "
                f"{' and '.join(v.owners)} {where}")
    return (f"{v.rule} on {v.layer}: {v.measured_nm} nm against {v.required_nm} nm, "
            f"between {' and '.join(v.owners)} {where}")


# ------------------------------------------------------------------- the geometry

def _gap_pairs(groups: Mapping[str, Sequence[tuple[int, int, int, int]]], limit: int,
               rule: str, layer: str) -> tuple[list[Violation], int]:
    """Every pair of rectangles from *different* groups closer than `limit`.

    Compared as `dx*dx + dy*dy < limit*limit`, in integers, so a corner separation is
    measured properly and no square root is taken.  Boxes are swept in x order and the
    scan for a given box stops at the first partner too far away in x -- which is sound
    because `dx` alone already exceeds the limit there, and every later partner is
    further right still.
    """
    flat: list[tuple[int, int, int, int, str]] = []
    for net, boxes in groups.items():
        for x0, y0, x1, y1 in boxes:
            flat.append((x0, y0, x1, y1, net))
    flat.sort()
    limit2 = limit * limit
    out: list[Violation] = []
    compared = 0
    for i, (ax0, ay0, ax1, ay1, an) in enumerate(flat):
        for j in range(i + 1, len(flat)):
            bx0, by0, bx1, by1, bn = flat[j]
            if bx0 - ax1 >= limit:
                break
            if an == bn:
                continue
            compared += 1
            dx = max(bx0 - ax1, ax0 - bx1, 0)
            dy = max(by0 - ay1, ay0 - by1, 0)
            d2 = dx * dx + dy * dy
            if d2 < limit2:
                measured = dx + dy if (dx == 0 or dy == 0) else max(dx, dy)
                out.append(Violation(rule, layer, (an, bn), measured, limit,
                                     (max(ax0, bx0), max(ay0, by0))))
    return _one_per_net_pair(out), compared


def _one_per_net_pair(found: Sequence[Violation], *,
                      worst=min) -> list[Violation]:
    """Collapse to one finding per pair of nets, keeping the worst approach.

    Unioning by net stops a rail drawn as many rectangles from reporting its neighbour
    many times -- but the union's own output is a slab decomposition, so a long rail comes
    back as several rectangles and a pad near the seam is close to two of them.  "Net RF is
    too close to net DC:north:3" is one fact about the layout however the union chose to
    cut it, so that is the unit reported.
    """
    best: dict[tuple[str, str, str], Violation] = {}
    for v in found:
        key = (v.rule, v.layer, "\x00".join(sorted(v.owners)))
        prev = best.get(key)
        if prev is None or worst(v.measured_nm, prev.measured_nm) == v.measured_nm:
            best[key] = v
    return list(best.values())


def _same_layer_gaps(layout: Layout, tech: Technology) -> tuple[list[Violation], int]:
    out: list[Violation] = []
    compared = 0
    for lay in tech.layers:
        groups = layout.union_by_net(lay.name)
        if len(groups) < 2:
            continue
        found, n = _gap_pairs(groups, lay.min_gap_nm, "min_gap", lay.name)
        out.extend(found)
        compared += n
    return out, compared


def _rf_to_dc(layout: Layout, tech: Technology) -> tuple[list[Violation], int]:
    """RF against control metal, which the same-layer check cannot see.

    They are different masks, so `min_gap` never compares them -- and this is the failure
    that matters most, because RF touching a control electrode is not a marginal spacing
    complaint, it is a short.

    The limit is `g_rf`, the technology's own clearance from driven metal to anything else,
    floored by the two layers' process rules: both have to hold, and which one binds is a
    property of the technology rather than something to choose here.  Before `g_rf` existed
    this was the layer rules alone, which measured the mask and not the design -- a
    technology could declare a 10 um RF clearance, draw 5, and pass.
    """
    if not (tech.has_purpose("rf") and tech.has_purpose("dc")):
        return [], 0
    rf, dc = tech.layer("rf"), tech.layer("dc")
    limit = max(tech.g_rf, rf.min_gap_nm, dc.min_gap_nm)
    left = layout.union_by_net(rf.name)
    right = layout.union_by_net(dc.name)
    return _cross_pairs(left, right, limit, "rf_dc_clearance", f"{rf.name}/{dc.name}")


def _cross_pairs(left: Mapping[str, Sequence[tuple[int, int, int, int]]],
                 right: Mapping[str, Sequence[tuple[int, int, int, int]]],
                 limit: int, rule: str, layer: str) -> tuple[list[Violation], int]:
    """Every rectangle of `left` against every rectangle of `right`, closer than `limit`.

    A separate sweep from `_gap_pairs` rather than one tagged pool: tagging would put the
    discriminator into the net name and the report would read `RF:RF` and `DC:DC:north:1`.
    Comparing two pools also halves the work, since neither side is compared with itself.
    """
    a = sorted((x0, y0, x1, y1, net) for net, boxes in left.items() for x0, y0, x1, y1
               in boxes)
    b = sorted((x0, y0, x1, y1, net) for net, boxes in right.items() for x0, y0, x1, y1
               in boxes)
    limit2 = limit * limit
    out: list[Violation] = []
    compared = 0
    start = 0
    for ax0, ay0, ax1, ay1, an in a:
        while start < len(b) and b[start][2] <= ax0 - limit:
            start += 1
        for j in range(start, len(b)):
            bx0, by0, bx1, by1, bn = b[j]
            if bx0 - ax1 >= limit:
                break
            compared += 1
            dx = max(bx0 - ax1, ax0 - bx1, 0)
            dy = max(by0 - ay1, ay0 - by1, 0)
            d2 = dx * dx + dy * dy
            if d2 < limit2:
                measured = dx + dy if (dx == 0 or dy == 0) else max(dx, dy)
                out.append(Violation(rule, layer, (an, bn), measured, limit,
                                     (max(ax0, bx0), max(ay0, by0))))
    return _one_per_net_pair(out), compared


def _overlaps(layout: Layout, tech: Technology) -> tuple[list[Violation], int]:
    """Any two electrodes, on any layers, that share area.  One finding per pair.

    The hard one.  A spacing violation is a fabrication risk; an overlap is two electrodes
    that are one piece of metal, and no netlist, voltage plan or field solve downstream
    means anything after it.  So the predicate is positive area -- `dx > 0 and dy > 0` --
    and it deliberately does NOT fire on metal that merely touches, which `min_gap` and
    `rf_dc_clearance` already report at 0 nm.

    Same net is skipped on purpose and for the usual reason: two perpendicular RF rails
    necessarily overlap at every degree-4 node, and on one net that is a merge.  Shapes are
    unioned by net first so that one rail drawn in fifty pieces reports its neighbour once.
    Layers are crossed as well as walked, because an RF rail over a control pad is the same
    short whether or not the two are on one mask.
    """
    #: layer order comes from the technology, so that one pair of nets gets one label --
    #: keying a finding on "RF/DC" and "DC/RF" separately reported every cross-layer
    #: overlap twice, once for each order the sweep happened to meet the two shapes in
    rank = {lay.name: i for i, lay in enumerate(tech.layers)}
    flat: list[tuple[int, int, int, int, str, str]] = []
    for lay in tech.layers:
        for net, boxes in layout.union_by_net(lay.name).items():
            for x0, y0, x1, y1 in boxes:
                flat.append((x0, y0, x1, y1, net, lay.name))
    flat.sort()
    out: list[Violation] = []
    compared = 0
    for i, (ax0, ay0, ax1, ay1, an, al) in enumerate(flat):
        for j in range(i + 1, len(flat)):
            bx0, by0, bx1, by1, bn, bl = flat[j]
            if bx0 >= ax1:
                break                       # sorted by x0: nothing later can overlap
            if an == bn:
                continue                    # one electrode drawn in several pieces
            compared += 1
            ix = min(ax1, bx1) - max(ax0, bx0)
            iy = min(ay1, by1) - max(ay0, by0)
            if ix > 0 and iy > 0:
                lo_l, hi_l = sorted((al, bl), key=lambda n: rank[n])
                layer = al if al == bl else f"{lo_l}/{hi_l}"
                out.append(Violation("overlap", layer, (an, bn), min(ix, iy), 0,
                                     (max(ax0, bx0), max(ay0, by0))))
    return _one_per_net_pair(out, worst=max), compared


def _dc_pairs_per_site(layout: Layout, arch) -> list[Violation]:
    """Every trapping site with fewer than `n_dc_pairs` control electrode pairs under it.

    One finding per site, naming the site and its count, because an average would hide
    exactly the case this is for: a device where most traps are fully controlled and the
    ones beside a junction have nothing.  `build.dc_pairs_by_site` defines the span and
    what counts as a pair; the number required is the technology's, not this module's.
    """
    device = getattr(arch, "device", arch)
    tech = layout.tech
    want = tech.n_dc_pairs
    sx, sy = tech.nm_per_unit_x.nm, tech.nm_per_unit_y.nm
    layer = tech.layer("dc").name if tech.has_purpose("dc") else ""
    out: list[Violation] = []
    for site, n in sorted(dc_pairs_by_site(layout, arch).items()):
        if n >= want:
            continue
        pos = device.nodes[site].pos
        where = (int(round(pos[0] * sx)), int(round(pos[1] * sy)))
        out.append(Violation("dc_pairs_per_site", layer, (site,), n, want, where))
    return out


# ---------------------------------------------------------------- the disclosures

def _electrode_disclosure(layout: Layout, arch) -> Disclosure | None:
    """Counted against declared, with the pitch each implies, and no verdict."""
    device = getattr(arch, "device", arch)
    wiring = dict(getattr(arch, "control", {}).get("wiring", {}) or {})
    if "electrodes_per_trap" not in wiring:
        return None
    declared = int(wiring["electrodes_per_trap"])
    sites = [n for n in device.nodes.values() if n.kind == "site"]
    pads = [p for p in layout.flatten() if p.role == "dc_pad"]
    if not sites:
        return None
    counted = len(pads) / len(sites)
    pitch = layout.tech.nm("dc_pitch")
    if not pads:
        return Disclosure(
            "electrodes per trap",
            (f"drawn: 0 control electrodes over {len(sites)} sites\n"
             f"declared: {declared} in control.wiring.electrodes_per_trap\n"
             f"no comparison is possible, and the absence is the finding -- see the "
             f"'no control electrodes survived' disclosure."),
            counted=0, declared=declared)
    columns = len({p.net.rsplit(":", 2)[-2] for p in pads if p.net.count(":") >= 2}) or 1
    implied = (declared / columns) * pitch
    return Disclosure(
        "electrodes per trap",
        (f"drawn: {counted:.2f} control electrodes per site "
         f"({len(pads)} pads over {len(sites)} sites, in {columns} columns)\n"
         f"declared: {declared} in control.wiring.electrodes_per_trap\n"
         f"the declared count in {columns} columns at a {pitch} nm pitch implies an axial "
         f"trap pitch of {implied:.0f} nm; the technology says "
         f"{layout.tech.nm_per_unit_x.nm} nm\n"
         f"NEITHER NUMBER IS MEASURED. The declared one is an authored integer that may "
         f"count shim and compensation electrodes this package does not draw "
         f"(control.wiring also declares "
         f"{wiring.get('compensation_electrodes_per_trap', '?')} compensation electrodes "
         f"per trap); the drawn one follows from a dc_pitch and a lattice scale that are "
         f"the technology's claims. This is for the architect to judge."),
        counted=round(counted, 3), declared=declared)


def _crossing_disclosure(arch) -> Disclosure | None:
    device = getattr(arch, "device", arch)
    pairs = unconnected_crossings(device)
    if not pairs:
        return None
    by_degree: dict[int, list[str]] = {}
    for seg, node in pairs:
        by_degree.setdefault(device.degree(node), []).append(f"{seg} through {node}")
    lines = [f"{len(pairs)} segment(s) run through a node they are not incident to; "
             f"a planar trap has no overpass, so the graph and the plane disagree."]
    for degree in sorted(by_degree):
        what = ("a dock drawn ON a rail rather than beside it"
                if degree == 1 else
                "two transport paths sharing metal -- if the crossing is real this node "
                "is a junction, its degree is understated, and it is not being charged "
                "as one")
        lines.append(f"crossed node degree {degree}: {', '.join(by_degree[degree])}")
        lines.append(f"    {what}")
    return Disclosure("segments crossing nodes", "\n".join(lines),
                      counted=len(pairs), declared=None)


# --------------------------------------------------------------------- the entry point

def check(layout: Layout, arch=None) -> DRCReport:
    """Run every rule over the flattened layout and collect the disclosures."""
    tech = layout.tech
    polys = layout.flatten()
    violations: list[Violation] = list(min_width_violations(polys, tech))
    gaps, compared = _same_layer_gaps(layout, tech)
    violations.extend(gaps)
    shorts, n = _rf_to_dc(layout, tech)
    violations.extend(shorts)
    compared += n
    welded, n = _overlaps(layout, tech)
    violations.extend(welded)
    compared += n
    if arch is not None:
        violations.extend(_dc_pairs_per_site(layout, arch))

    disclosures: list[Disclosure] = []
    if arch is not None:
        for maker in (_electrode_disclosure, _crossing_disclosure):
            got = maker(layout, arch) if maker is _electrode_disclosure else maker(arch)
            if got is not None:
                disclosures.append(got)
    if tech.has_purpose("dc") and not any(
            p.layer == tech.layer("dc").name for p in polys):
        disclosures.append(Disclosure(
            "no control electrodes survived",
            "not one control electrode is left in this layout, so every spacing rule "
            "involving the control layer passed by vacuum. Every pad was dropped where a "
            "perpendicular rail crosses its column -- which at this lattice scale is "
            "everywhere. The device is not merely hard to control; as drawn it has "
            "nothing to control it with.",
            counted=0))
    if tech.waived():
        disclosures.append(Disclosure(
            "waived technology minimums",
            "this technology is under one or more of the project's own minimums, with the "
            "reason written in its file:\n"
            + "\n".join(f"{name}: {tech.waivers[name]}" for name in tech.waived())
            + "\nThe rule was still checked -- it was waived, not absent -- so a reader "
              "who does not accept the reason can read the number off the file and the "
              "minimum off qccd/phys/tech.py:TECH_RULES.",
            counted=len(tech.waived())))
    if tech.declared():
        disclosures.append(Disclosure(
            "authored dimensions",
            "technology dimensions this project chose rather than read: "
            + ", ".join(tech.declared()),
            counted=len(tech.declared())))

    name = getattr(arch, "name", None) or "(device)"
    order = {r: i for i, r in enumerate(RULES)}
    violations.sort(key=lambda v: (order.get(v.rule, 99), v.where, v.owners))
    return DRCReport(tech.name, name, len(polys), tuple(violations),
                     tuple(disclosures), compared)


def checked(layout: Layout, arch=None) -> Layout:
    """The same layout with `violations` filled in, so a report carries its own findings."""
    return replace(layout, violations=check(layout, arch).violations)
