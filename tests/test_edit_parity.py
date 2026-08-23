"""THE PARITY GUARANTEE for the topology editor.

`qccd/arch/edit.py` and `qccd/viz/js/edit.js` are two implementations of one truth: add
and remove nodes and segments, maintain the loop orders, recompute degree / corners /
corner_endpoints, detect disconnection.  The Python one is what `Machine` and the
verifier use; the JS one is what the emitted page uses to re-render and re-price a
dragged edit without a server.

This codebase has already been bitten hard by exactly this shape of bug -- a JS operand
renderer and a Python one that disagreed, and a filter that searched text the user could
not see, giving 3,830 of 3,830 rows wrong -- so the mirror does not ship without a
mechanical guarantee that fails loudly when the two drift.

The guarantee is a DIFFERENTIAL RUN, not a golden file:

  * a corpus is built here, in this process, from the nine shipped architectures plus
    hand-written adversarial cases (chord across a loop, disconnecting removal, splice
    that would need a parallel edge, cutting an already-open path, a device with no
    loops at all);
  * every case is a `(device, edit script)` pair, and the script is executed step by
    step by BOTH implementations;
  * after every single edit both sides emit the whole device, the three derived maps,
    and the report -- or, for a refused edit, the error message;
  * the two traces are diffed field by field, floats to an exact-equality bar for ids
    and structure and 1e-12 for coordinates and lengths, and the FIRST divergence is
    reported with the case, the edit index, the JSON path and both values.

A golden vector would go stale silently the moment someone edited one side.  A
differential run cannot: there is nothing to refresh, and the only way to make it pass
is to make the two implementations agree.

The randomised half is seeded, so a failure reproduces exactly; the seed is printed in
the failure message.
"""

from __future__ import annotations

import json
import math
import os
import random
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from qccd.arch import GENERATORS, load
from qccd.arch.edit import device_to_wire, trace
from qccd.arch.schema import export_schema

ROOT = Path(__file__).resolve().parent.parent
ARCH_DIR = ROOT / "arch"
RUNNER = Path(__file__).resolve().parent / "edit_parity.mjs"
SEED = int(os.environ.get("QCCD_PARITY_SEED", "20260819"))
N_RANDOM = int(os.environ.get("QCCD_PARITY_EDITS", "24"))

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="node is not on PATH")


# --------------------------------------------------------------------- the corpus


def _shipped():
    for p in sorted(ARCH_DIR.glob("*.arch.json")):
        yield p.stem, load(p)


def _random_script(arch, rng: random.Random, n: int) -> list[dict]:
    """A seeded random walk over the five verbs.

    It intentionally emits edits that will be REFUSED as well as ones that succeed: half
    the parity surface is the validation, and an error message that differs between the
    two implementations is a real divergence -- the user is told two different stories
    about the same click.

    The script is generated against the ORIGINAL device only (ids are chosen up front),
    so it is pure data: neither implementation gets to influence what it is asked to do.
    """
    dev = arch.device
    zt = {k: dict(v) for k, v in arch.zone_types.items()}
    nodes = sorted(dev.nodes)
    segs = sorted(dev.segments)
    zones = sorted(zt) or [None]
    out: list[dict] = []
    for i in range(n):
        verb = rng.choice(["add_site", "add_site", "add_junction", "add_segment",
                           "remove_segment", "remove_node"])
        if verb in ("add_site", "add_junction"):
            nid = f"X{i}"
            anchor = dev.nodes[rng.choice(nodes)]
            pos = [round(float(anchor.pos[0]) + rng.uniform(-2, 2), 6),
                   round(float(anchor.pos[1] if len(anchor.pos) > 1 else 0.0)
                         + rng.uniform(-2, 2), 6)]
            args: dict = {"id": nid, "pos": pos}
            gesture = rng.random()
            if gesture < 0.45 and segs:
                args["on"] = rng.choice(segs)
            elif gesture < 0.85:
                args["to"] = rng.sample(nodes, min(len(nodes), rng.choice([1, 1, 2, 3])))
            if verb == "add_site":
                args["zone"] = rng.choice(zones)
                args["zone_types"] = zt
                if rng.random() < 0.25:
                    args["capacity"] = rng.choice([1, 2, 4, 8])
            out.append({"op": verb, "args": args})
        elif verb == "add_segment":
            a, b = (rng.sample(nodes, 2) if len(nodes) > 1 else (nodes[0], nodes[0]))
            args = {"id": f"Y{i}", "a": a, "b": b}
            if rng.random() < 0.3:
                args["loop"] = rng.choice(sorted(dev.loops)) if dev.loops else "L0"
            if rng.random() < 0.3:
                args["length"] = round(rng.uniform(0.25, 4.0), 6)
            out.append({"op": verb, "args": args})
        elif verb == "remove_segment":
            out.append({"op": verb, "args": {
                "id": rng.choice(segs),
                "on_loop": rng.choice(["refuse", "open", "delete"])}})
        else:
            out.append({"op": verb, "args": {
                "id": rng.choice(nodes),
                "mend": rng.choice(["splice", "splice", "open", "delete"]),
                "cascade": rng.random() < 0.3}})
    return out


def _adversarial(name: str, arch) -> list[dict] | None:
    """The cases a random walk would take a long time to find, stated on purpose."""
    dev = arch.device
    zt = {k: dict(v) for k, v in arch.zone_types.items()}
    zone = next(iter(zt), None)
    loops = sorted(dev.loops)
    nodes = sorted(dev.nodes)
    segs = sorted(dev.segments)
    if not nodes or not segs:
        return None
    script: list[dict] = []
    # subdivide a loop edge, then splice the new node straight back out -- the round trip
    # must return the loop to its original length
    on_loop_seg = next((s.id for s in dev.segments.values() if s.loop), segs[0])
    script.append({"op": "add_site", "args": {
        "id": "ADV0", "pos": [0.5, 0.5], "on": on_loop_seg, "zone": zone,
        "zone_types": zt}})
    script.append({"op": "remove_node", "args": {"id": "ADV0", "mend": "splice"}})
    # a chord: legal, changes degree, must be refused with loop=
    if loops and len(dev.loops[loops[0]].nodes) > 6:
        seq = dev.loops[loops[0]].nodes
        script.append({"op": "add_segment", "args": {
            "id": "ADVCHORD", "a": seq[0], "b": seq[len(seq) // 2]}})
        script.append({"op": "add_segment", "args": {
            "id": "ADVCHORD2", "a": seq[1], "b": seq[len(seq) // 2 + 1],
            "loop": loops[0]}})
    # a parallel edge, which check_structure forbids
    s0 = dev.segments[segs[0]]
    script.append({"op": "add_segment", "args": {
        "id": "ADVDUP", "a": s0.ends[0], "b": s0.ends[1]}})
    # a self-loop
    script.append({"op": "add_segment", "args": {
        "id": "ADVSELF", "a": nodes[0], "b": nodes[0]}})
    # cut a loop open, then cut the open path again in the middle
    if loops:
        lp = dev.loops[loops[0]]
        if len(lp.nodes) > 4:
            e0 = dev.segment_between(lp.nodes[0], lp.nodes[1])
            e1 = dev.segment_between(lp.nodes[2], lp.nodes[3])
            script.append({"op": "remove_segment", "args": {"id": e0.id, "on_loop": "open"}})
            script.append({"op": "remove_segment", "args": {"id": e1.id, "on_loop": "open"}})
    # an isolated node, then wire it, then disconnect it again
    script.append({"op": "add_site", "args": {
        "id": "ADVISO", "pos": [99.0, 99.0], "zone": zone, "zone_types": zt}})
    script.append({"op": "add_segment", "args": {"id": "ADVISOE", "a": "ADVISO", "b": nodes[0]}})
    script.append({"op": "remove_segment", "args": {"id": "ADVISOE"}})
    script.append({"op": "remove_node", "args": {"id": "ADVISO"}})
    # a junction declared but under-degree, and one that is a real R18 junction
    script.append({"op": "add_junction", "args": {
        "id": "ADVJ", "pos": [1.5, 1.5], "to": nodes[:1]}})
    script.append({"op": "add_junction", "args": {
        "id": "ADVJ3", "pos": [2.5, 2.5], "to": nodes[1:4]}})
    # unknown ids, unknown ops
    script.append({"op": "remove_segment", "args": {"id": "NOPE"}})
    script.append({"op": "remove_node", "args": {"id": "NOPE"}})
    script.append({"op": "add_site", "args": {"id": nodes[0], "pos": [0.0, 0.0], "zone": zone,
                                              "zone_types": zt}})
    script.append({"op": "add_site", "args": {"id": "ADVNOZONE", "pos": [3.0, 3.0]}})
    script.append({"op": "add_site", "args": {"id": "ADVBADZONE", "pos": [3.0, 3.0],
                                              "zone": "no_such_zone", "zone_types": zt}})
    return script


def build_corpus() -> dict:
    rng = random.Random(SEED)
    cases = []
    for name, arch in _shipped():
        cases.append({"name": f"{name}::random",
                      "device": device_to_wire(arch.device),
                      "script": _random_script(arch, random.Random(rng.randrange(1 << 30)),
                                               N_RANDOM)})
        adv = _adversarial(name, arch)
        if adv:
            cases.append({"name": f"{name}::adversarial",
                          "device": device_to_wire(arch.device),
                          "script": adv})
    # The schema bounds travel WITH the corpus, straight out of `schema.py`.  `edit.js`
    # keeps no schema constant of its own, so the harness must hand them over exactly the
    # way the emitted page does -- and a corpus that carried a different bound than the
    # page ships would be a third source of truth.
    return {"cases": cases, "bounds": export_schema()["bounds"]}


# ----------------------------------------------------------------------- the diff


def _num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def diff_json(a, b, path: str = "", tol: float = 1e-12, out: list | None = None) -> list:
    """Structural diff with a float tolerance.  Order-sensitive on lists and dicts.

    Dict ORDER is compared because it is load-bearing: the listing emits nodes and
    segments in insertion order, and the page draws them in it, so two devices with the
    same contents in a different order are not the same page.
    """
    out = [] if out is None else out
    if len(out) >= 12:
        return out
    if isinstance(a, dict) and isinstance(b, dict):
        ka, kb = list(a), list(b)
        if ka != kb:
            only_a = [k for k in ka if k not in b][:5]
            only_b = [k for k in kb if k not in a][:5]
            if only_a or only_b:
                out.append(f"{path}: keys differ (py only {only_a}, js only {only_b})")
            else:
                out.append(f"{path}: key ORDER differs (py {ka[:6]}... js {kb[:6]}...)")
            return out
        for k in ka:
            diff_json(a[k], b[k], f"{path}.{k}", tol, out)
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            out.append(f"{path}: length {len(a)} != {len(b)} (py {a[:4]} js {b[:4]})")
            return out
        for i, (x, y) in enumerate(zip(a, b)):
            diff_json(x, y, f"{path}[{i}]", tol, out)
        return out
    if _num(a) and _num(b):
        if not math.isclose(float(a), float(b), rel_tol=0.0, abs_tol=tol):
            out.append(f"{path}: {a!r} != {b!r} (delta {float(b) - float(a):.3e})")
        return out
    if a != b:
        sa, sb = repr(a), repr(b)
        out.append(f"{path}: {sa[:160]} != {sb[:160]}")
    return out


def run_js(corpus: dict, tmp_path: Path) -> dict:
    src = tmp_path / "corpus.json"
    src.write_text(json.dumps(corpus), encoding="utf-8")
    proc = subprocess.run([node, str(RUNNER), str(src)], capture_output=True, text=True,
                          cwd=str(ROOT))
    if proc.returncode != 0:
        raise AssertionError(f"the JS mirror failed to run:\n{proc.stderr[:4000]}")
    return json.loads(proc.stdout)


# ----------------------------------------------------------------------- the tests


@requires_node
def test_python_and_js_topology_edits_agree(tmp_path):
    """Every edit, on every shipped architecture, produces the same device on both sides."""
    corpus = build_corpus()
    js = run_js(corpus, tmp_path)["cases"]
    failures: list[str] = []
    n_edits = n_ok = n_refused = 0
    for case in corpus["cases"]:
        name = case["name"]
        from qccd.arch.edit import device_from_wire
        py = trace(device_from_wire(case["device"]), case["script"])
        their = js.get(name)
        assert their is not None, f"the JS runner produced no trace for {name}"
        assert len(py) == len(their), f"{name}: {len(py)} python steps vs {len(their)} js"
        for mine, theirs in zip(py, their):
            n_edits += 1
            if mine.get("ok"):
                n_ok += 1
            else:
                n_refused += 1
            d = diff_json(mine, theirs, path=f"{name}[{mine['i']}] {mine['op']}")
            if d:
                failures.append("\n    ".join(d[:6]))
                break
    assert not failures, (
        f"PYTHON AND JS TOPOLOGY EDITS HAVE DRIFTED (seed {SEED}).\n"
        f"  reproduce: QCCD_PARITY_SEED={SEED} pytest tests/test_edit_parity.py\n"
        + "\n\n".join(failures[:4]))
    # a corpus that refuses everything, or accepts everything, would pass vacuously
    assert n_ok > 100, f"only {n_ok} edits succeeded; the corpus is not exercising much"
    assert n_refused > 10, f"only {n_refused} edits were refused; validation is untested"


@requires_node
def test_js_derive_matches_python_on_every_shipped_device(tmp_path):
    """The three derived maps the page ships precomputed, recomputed in JS, must match.

    This is the cheapest and strongest half of the mirror: `degree`, `corner` and
    `corner_endpoints` are pure functions of (nodes, segments, loops), Python already
    computes them for every emitted page, and the same check runs IN the page at load
    time -- so a drift is caught in CI here and, failing that, announced on the page
    itself rather than quietly mispricing a rotation.
    """
    cases = [{"name": name, "device": device_to_wire(arch.device), "script": []}
             for name, arch in _shipped()]
    src = tmp_path / "derive.json"
    src.write_text(json.dumps({"cases": cases, "bounds": export_schema()["bounds"]}), encoding="utf-8")
    probe = tmp_path / "derive.mjs"
    probe.write_text(
        "import fs from 'fs';\n"
        f"const E=(await import({json.dumps((ROOT / 'qccd/viz/js/edit.js').as_uri())})).default;\n"
        f"const c=JSON.parse(fs.readFileSync({json.dumps(str(src))},'utf8'));\n"
        "const out={};for(const x of c.cases) out[x.name]=E.derived(x.device);\n"
        "process.stdout.write(JSON.stringify(out));\n", encoding="utf-8")
    proc = subprocess.run([node, str(probe)], capture_output=True, text=True, cwd=str(ROOT))
    assert proc.returncode == 0, proc.stderr[:4000]
    theirs = json.loads(proc.stdout)
    from qccd.arch.edit import derived
    for name, arch in _shipped():
        mine = derived(arch.device)
        d = diff_json(mine, theirs[name], path=f"derive::{name}")
        assert not d, f"{name}: JS derive() disagrees with Python\n  " + "\n  ".join(d[:8])


@requires_node
def test_generated_devices_too(tmp_path):
    """The generators, not just the shipped files -- more shapes, including no loops."""
    from qccd.arch.edit import device_from_wire
    specs = [
        ("ring", "ring", dict(width=12, height=2, verticals=4)),
        ("ring_tall", "ring", dict(width=8, height=4, verticals=4)),
        ("ring_bare", "ring", dict(width=10, height=2, verticals=0)),
        ("grid", "grid", dict(a=4, b=4)),                 # NO loops at all
        ("chain", "chain", dict(n=8)),                    # one OPEN loop
        ("ladder", "ladder", dict(width=12, rungs=3, highways=2)),
        ("racetrack", "racetrack", dict(straight=8)),
        ("dual_loop", "dual_loop", dict(width=8, couplings=[0, 4])),
    ]
    rng = random.Random(SEED ^ 0xBEEF)
    cases = []
    for name, gen_name, kw in specs:
        dev = GENERATORS[gen_name](**kw)

        class _Fake:  # _random_script only reads .device and .zone_types
            device = dev
            zone_types = {"data": {"capacity": 2}, "ancilla": {"capacity": 1}}

        cases.append({"name": name, "device": device_to_wire(dev),
                      "script": _random_script(_Fake, random.Random(rng.randrange(1 << 30)),
                                               N_RANDOM)})
    js = run_js({"cases": cases, "bounds": export_schema()["bounds"]}, tmp_path)["cases"]
    for case in cases:
        py = trace(device_from_wire(case["device"]), case["script"])
        d: list[str] = []
        for mine, theirs in zip(py, js[case["name"]]):
            d = diff_json(mine, theirs, path=f"{case['name']}[{mine['i']}] {mine['op']}")
            if d:
                break
        assert not d, f"{case['name']}: drift\n  " + "\n  ".join(d[:8])


def test_python_edits_keep_every_loop_walkable():
    """The invariant the whole module exists to hold, checked independently of the mirror.

    `loop_segments` raising is the failure mode a splice bug produces, and it is silent
    until something tries to rotate -- so it is asserted after every edit of every script
    rather than only at the end.
    """
    from qccd.arch.edit import device_from_wire
    rng = random.Random(SEED)
    for name, arch in _shipped():
        script = _random_script(arch, random.Random(rng.randrange(1 << 30)), N_RANDOM)
        dev = arch.device
        for rec in trace(dev, script):
            if not rec.get("ok"):
                continue
            dev = device_from_wire(rec["device"])
            assert not dev.check_structure(), f"{name}[{rec['i']}]: {dev.check_structure()[:3]}"
            for lid in dev.loops:
                dev.loop_segments(lid)          # raises KeyError on a broken walk
                if dev.loops[lid].closed:
                    dev.shift_map(lid, 1)       # raises on an open loop
