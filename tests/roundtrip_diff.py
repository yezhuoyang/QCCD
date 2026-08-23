#!/usr/bin/env python
"""The Python half of the round-trip harness.

    node tests/roundtrip_census.mjs <page.html> <edit> <dump.json>
    python tests/roundtrip_diff.py <dump.json>

Reads the dump `roundtrip_census.mjs` wrote, rebuilds the exported architecture through
the REAL toolchain (`Architecture.from_json`, plus a second pass through the exported
Python listing + `Machine.apply_edits`), and diffs the rebuilt architecture field by
field against what the JS editor believes it has.

Nothing here is "close enough": every scalar is compared with `==`, every float position
with `==` first and only then reported as a tolerance-sized drift, and every ordered list
(loops, and each loop's node sequence) is compared as a SEQUENCE.
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from qccd.arch import Architecture  # noqa: E402
from qccd.arch.control import build_control_plane  # noqa: E402
from qccd.cost.hardware import hardware_report  # noqa: E402


# --------------------------------------------------------------------------- belief
def belief_of(arch: Architecture) -> dict:
    """The same shape `__belief()` builds in the browser, from a Python Architecture."""
    dev = arch.device
    node_order = list(dev.nodes)
    seg_order = list(dev.segments)
    nodes, caps, explicit, zone_of = {}, {}, [], {}
    nsite = njunc = 0
    for nid in node_order:
        n = dev.nodes[nid]
        nodes[nid] = {
            "pos": [float(n.pos[0]), float(n.pos[1])],
            "kind": n.kind,
            "zone": n.zone_type,
            "cap": int(n.capacity),
            "explicit": bool(n.capacity_explicit),
            "labels": list(n.labels),
        }
        zone_of[nid] = n.zone_type
        if n.kind == "site":
            nsite += 1
            caps[nid] = int(n.capacity)
        else:
            njunc += 1
        if n.capacity_explicit:
            explicit.append(nid)

    segs = {}
    for sid in seg_order:
        s = dev.segments[sid]
        segs[sid] = {"ends": list(s.ends), "length": float(s.length),
                     "cap": int(s.capacity), "loop": s.loop, "labels": list(s.labels)}

    loops = [{"id": lid, "kind": lp.kind, "closed": bool(lp.closed),
              "note": lp.note, "nodes": list(lp.nodes)}
             for lid, lp in dev.loops.items()]

    deg_hist: dict[str, int] = {}
    for nid in dev.nodes:
        d = dev.degree(nid)
        deg_hist[str(d)] = deg_hist.get(str(d), 0) + 1
    deg_hist = {k: deg_hist[k] for k in sorted(deg_hist, key=int)}

    classes = dict(arch.control.get("classes", {}) or {})
    declared = [dict(c) for c in classes.get("extra", []) or []]

    hw = hardware_report(arch)
    plane = build_control_plane(dev, arch.control)

    return {
        "name": arch.name,
        "description": arch.description,
        "generator": dev.generator,
        "params": dict(dev.params),
        "n_nodes": len(node_order),
        "n_sites": nsite,
        "n_junction_kind": njunc,
        "n_junction_degree": len(dev.junction_nodes),
        "n_segments": len(seg_order),
        "node_order": node_order,
        "seg_order": seg_order,
        "nodes": nodes,
        "segments": segs,
        "loops": loops,
        "loop_order": [l["id"] for l in loops],
        "capacities": caps,
        "capacity_explicit": sorted(explicit),
        "total_capacity": dev.total_capacity(),
        "degree_histogram": deg_hist,
        "zone_types": {k: dict(v) for k, v in arch.zone_types.items()},
        "zone_of_site": zone_of,
        "zones_in_use": sorted({z for z in zone_of.values() if z is not None}),
        "classes_block": classes,
        "declared_classes": declared,
        "declared_class_ids": [c.get("id") for c in declared],
        "control": dict(arch.control),
        "channels_spec": dict(arch.control.get("channels")) if arch.control.get("channels") else None,
        "control_plane": {"n_shared": hw.dacs_broadcast,
                          "n_compensation": hw.dacs_compensation,
                          "n_channels": hw.dacs,
                          "electrodes": hw.electrodes,
                          "switches": hw.switches},
        "budget": dict(arch.budget),
        "heating": dict(arch.heating),
        "species": dict(arch.species),
        "provenance": dict(arch.provenance),
        "over_budget": list(hw.over_budget),
        # not part of the JS belief: the structural facts the plane itself reports
        "_plane_declared": plane.declared,
        "_plane_shared": plane.n_shared_channels,
        "_plane_comp": plane.n_compensation_channels,
        "_plane_total": plane.n_channels,
    }


def js_primitives_doc(raw: dict) -> dict:
    """Mirror `primitivesToJson` in engine.js, so the JS raw primitives can be diffed
    against `PrimitiveSet.to_json()` in the SAME shape."""
    out: dict = {}
    for name, pts in (raw.get("curves") or {}).items():
        out[name] = {"curve": [_pt(p) for p in pts]}
    for name, by in (raw.get("degree_curves") or {}).items():
        out.setdefault(name, {})
        out[name]["curve_by_degree"] = {
            str(d): [_pt(p) for p in by[d]] for d in sorted(by, key=int)}
    for name, sc in (raw.get("scalars") or {}).items():
        out.setdefault(name, {})
        out[name].update(sc)
    return out


def _pt(p: dict) -> dict:
    d = {"us": p.get("us"), "quanta": p.get("quanta"),
         "table": p.get("table", "qccdsim_jones")}
    if p.get("source") is not None:
        d["source"] = p["source"]
    if p.get("label") is not None:
        d["label"] = p["label"]
    return d


# --------------------------------------------------------------------------- diffing
def diff(js, py, path="", out=None, floats=None):
    out = [] if out is None else out
    floats = [] if floats is None else floats
    if isinstance(js, float) or isinstance(py, float):
        if isinstance(js, (int, float)) and isinstance(py, (int, float)):
            if js == py:
                return out, floats
            if math.isclose(js, py, rel_tol=1e-9, abs_tol=1e-9):
                floats.append((path, js, py))
                return out, floats
            out.append((path, js, py))
            return out, floats
    if type(js) is not type(py) and not (
            isinstance(js, (int, float)) and isinstance(py, (int, float))):
        out.append((path, f"<{type(js).__name__}>{js!r}", f"<{type(py).__name__}>{py!r}"))
        return out, floats
    if isinstance(js, dict):
        for k in js:
            if k not in py:
                out.append((f"{path}.{k}", js[k], "<MISSING>"))
            else:
                diff(js[k], py[k], f"{path}.{k}", out, floats)
        for k in py:
            if k not in js:
                out.append((f"{path}.{k}", "<MISSING>", py[k]))
    elif isinstance(js, list):
        if len(js) != len(py):
            out.append((f"{path}[len]", len(js), len(py)))
        for i in range(min(len(js), len(py))):
            diff(js[i], py[i], f"{path}[{i}]", out, floats)
    elif js != py:
        out.append((path, js, py))
    return out, floats


FIELDS = [
    ("node count", ["n_nodes", "n_sites", "n_junction_kind", "n_junction_degree"]),
    ("node ids + order", ["node_order"]),
    ("nodes (pos/kind/zone/cap/explicit/labels)", ["nodes"]),
    ("segment count", ["n_segments"]),
    ("segment ids + order", ["seg_order"]),
    ("segments (ends/length/cap/loop/labels)", ["segments"]),
    ("loops (ORDER matters)", ["loops", "loop_order"]),
    ("capacities", ["capacities", "total_capacity"]),
    ("explicit per-site overrides", ["capacity_explicit"]),
    ("zones", ["zone_types", "zone_of_site", "zones_in_use"]),
    ("declared classes", ["declared_classes", "declared_class_ids", "classes_block"]),
    ("control block", ["control", "channels_spec"]),
    ("control-plane channel count", ["control_plane"]),
    ("budget", ["budget", "over_budget"]),
    ("degree histogram", ["degree_histogram"]),
    ("name/description/provenance", ["name", "description", "provenance"]),
    ("generator provenance", ["generator", "params"]),
    ("heating/species", ["heating", "species"]),
]


VERDICT: dict = {}


def report(js_belief, py_belief, label):
    rows = []
    VERDICT[label] = {}
    for title, keys in FIELDS:
        bad, drift = [], []
        for k in keys:
            d, f = diff(js_belief.get(k), py_belief.get(k), k)
            bad += d
            drift += f
        rows.append((title, bad, drift))
    ok = all(not b for _, b, _ in rows)
    print(f"\n---- {label}: {'ROUND-TRIPS' if ok else 'LOSES INFORMATION'} ----")
    for title, bad, drift in rows:
        mark = "ok " if not bad else "BAD"
        extra = f"  (+{len(drift)} float drift)" if drift else ""
        print(f"  [{mark}] {title}{extra}")
        for p, a, b in bad[:8]:
            print(f"        {p}: js={_s(a)}  py={_s(b)}")
        if len(bad) > 8:
            print(f"        ... {len(bad) - 8} more")
        for p, a, b in drift[:3]:
            print(f"        ~ {p}: js={a!r} py={b!r} (within 1e-9)")
        VERDICT[label][title] = {
            "ok": not bad, "n_diff": len(bad), "n_drift": len(drift),
            "sample": [[p, _s(a), _s(b)] for p, a, b in bad[:4]]}
    return ok, rows


def _s(v):
    s = repr(v)
    return s if len(s) <= 90 else s[:87] + "..."


# --------------------------------------------------------------------------- main
def main(dump_path: str) -> int:
    dump = json.loads(Path(dump_path).read_text(encoding="utf-8"))
    if dump.get("fatal"):
        print("FATAL from the JS harness:", dump["fatal"])
        return 2
    js = dump["after"]
    doc = json.loads(dump["arch_json"])

    print("=" * 78)
    print(f"page   : {dump['file']}")
    print(f"edit   : {dump['edit']}   ({dump.get('n_edits')} edit record(s))")
    print(f"result : {json.dumps(dump.get('edit_result'))[:400]}")
    if dump.get("edit_error"):
        print(f"EDIT ERROR: {dump['edit_error']}")
    print(f"census : base {dump['census_base']['overlap_frames']} overlap frame(s), "
          f"after {dump['census_after']['overlap_frames']}; worst overlap "
          f"{dump['census_after']['worst_overlap_px']}px, worst seam snap "
          f"{dump['census_after']['worst_boundary_snap_px']}px")
    if dump.get("problems") or dump.get("lints"):
        print(f"editor : problems={dump['problems']} lints={dump['lints']}")

    # ---- lane A: the exported .arch.json, through Architecture.from_json ------------
    try:
        arch = Architecture.from_json(doc, validate=True)
    except Exception as exc:  # noqa: BLE001
        print(f"\n---- lane A (exportJson -> Architecture): REFUSED BY PYTHON ----")
        print(f"  {type(exc).__name__}: {exc}")
        return 1
    okA, _ = report(js, belief_of(arch), "lane A: exportJson -> Architecture.from_json")

    # ---- the document identity check: does Python re-serialize what it was given? ---
    re_doc = arch.to_json(expanded=True)
    dbad, ddrift = diff(doc, re_doc, "doc")
    print(f"\n  [{'ok ' if not dbad else 'BAD'}] document identity "
          f"(exported json == Architecture.from_json(...).to_json())")
    for p, a, b in dbad[:12]:
        print(f"        {p}: exported={_s(a)}  reparsed={_s(b)}")
    if len(dbad) > 12:
        print(f"        ... {len(dbad) - 12} more")

    # ---- primitives, in the document shape ------------------------------------------
    pbad, _ = diff(js_primitives_doc(js["curves"]), arch.primitives.to_json(), "primitives")
    print(f"  [{'ok ' if not pbad else 'BAD'}] curves / primitives "
          f"(JS editor state == PrimitiveSet.to_json())")
    for p, a, b in pbad[:8]:
        print(f"        {p}: js={_s(a)}  py={_s(b)}")

    # ---- lane B: the exported Python listing, replayed -------------------------------
    okB = None
    try:
        ns: dict = {}
        exec(compile(dump["python"], "<exported listing>", "exec"), ns)  # noqa: S102
        m = ns.get("m")
        if m is None:
            raise RuntimeError("the exported listing bound no `m`")
        okB, _ = report(js, belief_of(m.arch), "lane B: exportPython -> replayed listing")
    except Exception as exc:  # noqa: BLE001
        print("\n---- lane B (exportPython -> replayed listing): REFUSED BY PYTHON ----")
        print(f"  {type(exc).__name__}: {exc}")
        okB = False

    # ---- lane C: the edit record, replayed through Machine.apply_edits ---------------
    okC = None
    base = Path(__file__).resolve().parent.parent / "arch" / f"{dump['before']['name']}.arch.json"
    if base.exists():
        try:
            from qccd import Machine

            mm = Machine.load(base)
            mm.apply_edits(json.loads(dump["edits_record"]))
            okC, _ = report(js, belief_of(mm.arch), "lane C: edits -> Machine.apply_edits")
        except Exception as exc:  # noqa: BLE001
            print("\n---- lane C (edits -> Machine.apply_edits): REFUSED BY PYTHON ----")
            print(f"  {type(exc).__name__}: {exc}")
            okC = False

    # ---- lane D: `qccd.arch.save(arch, path)` -- the DEFAULT, compact serialization ----
    # `save` defaults to expanded=False, so this is the path a user takes when they write
    # the edited architecture back to a file with the library's own one-liner.
    okD = None
    try:
        import tempfile

        from qccd.arch import load as arch_load
        from qccd.arch import save as arch_save

        with tempfile.TemporaryDirectory() as td:
            p = arch_save(arch, Path(td) / "compact.arch.json")   # DEFAULT expanded=False
            compact = json.loads(Path(p).read_text(encoding="utf-8"))
            back = arch_load(p)
        form = "expanded" if compact["geometry"].get("nodes") else "generator+params only"
        print(f"\n(lane D writes the {form} form)")
        okD, _ = report(js, belief_of(back), "lane D: qccd.arch.save(...) default -> load")
    except Exception as exc:  # noqa: BLE001
        print("\n---- lane D (save default -> load): REFUSED BY PYTHON ----")
        print(f"  {type(exc).__name__}: {exc}")
        okD = False

    print(f"\nSUMMARY {dump['file']} / {dump['edit']}: "
          f"laneA={'ok' if okA else 'LOSS'} "
          f"docIdentity={'ok' if not dbad else 'LOSS'} "
          f"primitives={'ok' if not pbad else 'LOSS'} "
          f"laneB={'ok' if okB else 'LOSS'} "
          f"laneC={'n/a' if okC is None else ('ok' if okC else 'LOSS')} "
          f"laneD={'ok' if okD else 'LOSS'}")
    VERDICT["_meta"] = {"file": dump["file"], "edit": dump["edit"],
                        "n_edits": dump.get("n_edits"),
                        "edit_result": dump.get("edit_result"),
                        "edit_error": dump.get("edit_error"),
                        "doc_identity": {"ok": not dbad, "n_diff": len(dbad),
                                         "sample": [[p, _s(a), _s(b)] for p, a, b in dbad[:4]]},
                        "primitives": {"ok": not pbad, "n_diff": len(pbad),
                                       "sample": [[p, _s(a), _s(b)] for p, a, b in pbad[:4]]},
                        "census_after": dump["census_after"],
                        "problems": dump.get("problems"), "lints": dump.get("lints")}
    Path(dump_path + ".verdict.json").write_text(json.dumps(VERDICT, indent=1), encoding="utf-8")
    return 0 if (okA and not dbad and not pbad and okB and okD) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
