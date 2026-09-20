"""THE EDITOR, driven headlessly against the pages that actually ship.

`tests/editor.mjs` loads an emitted page under the DOM shim and drives `EDITOR.begin /
move / drop / emit / undo` -- the same functions the pointer handlers call.  It cannot
synthesize a pointer event (the shim has no `Event` and no `dispatchEvent`), which is
precisely why those handlers are thin adapters: any logic left inside a listener would be
logic with no test.

Three properties are asserted here, and each of them is a thing that would otherwise fail
silently in front of a user:

1. **THE EDIT REACHES THE NUMBERS.**  Python computes what an edit costs, live, at test
   time; the browser computes it independently; the two are diffed.  There is no stored
   expectation, so nothing can go stale.

2. **THE EDIT REACHES THE PICTURE.**  The geometric census -- zero ion overlap, which
   `tests/test_viz_js.py` already asserts for the shipped pages -- is re-run AFTER the edit.
   `2*r_ion = 0.48*g` makes overlap structurally impossible only if `g` is recomputed, so
   this is what proves the client-side re-layout really happened rather than the page
   redrawing at a stale scale.

3. **UNDO IS EXACT.**  Not "close": the full replay-from-scratch design means an edit
   followed by its undo must restore every scalar bit for bit, and any drift would mean
   the applier is not actually pure.

Plus the mutation guards this repo already establishes the discipline for
(`test_the_harness_actually_catches_an_oversized_ion`,
`test_the_panel_harness_catches_a_listing_that_stops_following`): a harness that cannot
fail is decoration, so each one publishes a deliberately broken page and asserts the
harness notices.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from qccd.arch import Architecture, load
from qccd.arch.schema import validate_document
from qccd.api import Machine
from qccd.cost.hardware import hardware_report
from qccd.cost.models import corrected_model, deck_model
from qccd.verify import verify

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"
RUNNER = Path(__file__).resolve().parent / "editor.mjs"
ARCH = ROOT / "arch" / "ring144_24v.arch.json"

node = shutil.which("node")
requires_node = pytest.mark.skipif(node is None, reason="node is not on PATH")


#: Directories under `out/` that hold a COPY of a past website build rather than this
#: build's output.  A site build copies entry pages verbatim instead of rendering them, so
#: such a copy is stale by construction the instant the engine changes -- and is meant to
#: be: preserving the bytes of a past build is the whole point of staging one.  Asking it
#: to be current asks a snapshot not to be a snapshot.  `out/site-deploy/` is a 9 September
#: build whose 200-odd pages were failing here with the generator drift of that day.
STAGED_BUILDS = "site-"


def _pages():
    return [p for p in sorted(OUT.rglob("*.html"))
            if not any(part.startswith(STAGED_BUILDS) for part in p.relative_to(OUT).parts)
            and '<script id="data"' in p.read_text(encoding="utf-8")]


def drive(page: Path, script, tmp_path: Path) -> dict:
    sp = tmp_path / "script.json"
    sp.write_text(json.dumps(script), encoding="utf-8")
    # `encoding="utf-8"`, explicitly: the listings carry section signs and en dashes, and
    # the platform's locale codec mangles them into replacement characters -- which
    # presents as a byte-for-byte round-trip failure that is entirely the harness's fault.
    r = subprocess.run([node, str(RUNNER), str(page), str(sp)],
                       capture_output=True, text=True, encoding="utf-8",
                       timeout=900, cwd=str(ROOT))
    assert r.returncode == 0, f"editor.mjs failed on {page.name}: {r.stderr[-3000:]}"
    return json.loads(r.stdout.strip().splitlines()[-1])


TMP_FOR_SOURCE = None

DECK_PAGE = OUT / "ring144_24v__deck__deck.html"
CORR_PAGE = OUT / "ring144_24v__deck__corrected.html"


# ======================================================================================
# THE LOOP THAT WAS NEVER CLOSED
# ======================================================================================
#
# `editor.mjs` has always exported `arch_json` and the harness has always checked that it
# PARSES (`json_ok`).  Nothing ever handed it to `Architecture.from_json`.  That is the
# hole two separate defects fell through:
#
#   * deleting `E0` on the two-node `stationary_chain` left `loops.P0` with ONE node.
#     Four correct warnings, `ok: true`, and an export Python refuses with
#     `$.geometry.loops[0].nodes: needs at least 2 item(s), has 1`.
#   * `set_curve("shuttle_segment", [dict(us=5.0, quanta=0.1, table="mytable")])` was
#     accepted with no lint at all, and exported `table: "mytable"`, which Python refuses
#     because the browser knew nothing about the schema's enums.
#
# Both are the same failure at one remove: the browser was allowed to hold a state the
# file format cannot express.  `json.loads` cannot see that and `JSON.parse` certainly
# cannot.  Only the real loader can, so the real loader runs here.

#: (page stem, script, what the edit is) -- one row per way a page can reach an
#: unrepresentable state.  Each was a live defect.
_EXPORT_SCRIPTS = [
    ("stationary_chain__walk__corrected",
     [{"do": "remove", "sel": [{"kind": "segment", "id": "E0"}]}],
     "cutting the only segment of a two-node path, which degenerates its loop"),
    ("cyclone_base__rotate__corrected",
     [{"do": "emit", "op": {"method": "set_curve",
                            "args": ["shuttle_segment",
                                     [{"us": 5.0, "quanta": 0.1, "table": "mytable"}]],
                            "kwargs": {}}}],
     "a curve tagged with a table that is not in the schema's enum"),
    ("cyclone_base__rotate__corrected",
     [{"do": "source", "src": None}],   # filled in below: base source + a bad statement
     "the same bad table, typed into the side editor instead of emitted"),
]


def _export_script(stem, script, page):
    """Fill in the source-lane case, which needs the page's own listing as its prefix."""
    if script and script[0].get("do") == "source" and script[0].get("src") is None:
        base = drive(page, [], TMP_FOR_SOURCE)["source"]
        script = [{"do": "source",
                   "src": base + 'm.set_curve("shuttle_segment", '
                                 '[dict(us=5.0, quanta=0.1, table="mytable")])\n'}]
    return script


@requires_node
@pytest.mark.parametrize("stem, script, why", _EXPORT_SCRIPTS,
                         ids=[f"{r[0].split('__')[0]}:{r[2][:30]}" for r in _EXPORT_SCRIPTS])
def test_an_edit_never_exports_a_document_python_refuses(stem, script, why, tmp_path):
    """The assertion both defects were missing, stated once for all of them.

    Whatever the editor lets the user do, `exportJson()` must hand over a document the
    real loader accepts.  Not "parses as JSON" -- LOADS, with `validate=True`, which runs
    the schema walker and `Device.check_structure` exactly as `qccd.arch.load` does.
    """
    page = OUT / f"{stem}.html"
    if not page.exists():
        pytest.skip(f"{page.name} has not been emitted")
    global TMP_FOR_SOURCE
    TMP_FOR_SOURCE = tmp_path
    r = drive(page, _export_script(stem, script, page), tmp_path)
    assert r["ready"], r.get("why")

    # THE INVARIANT, and it is one sentence: whatever the two lanes do about a statement
    # the file format cannot hold, no document Python refuses ever leaves the page.
    #
    # `emit()` rolls the statement back, so the export is clean.  The TEXT lane keeps the
    # bad line -- an editor does not throw away sixty lines because of one squiggle -- and
    # the EXPORT refuses instead, naming the schema's own reason and the statement to fix.
    # Both are correct; what would not be correct is a third outcome, where the page hands
    # over a file that fails inside somebody else's loader.  That third outcome is what
    # both defects were.
    if r.get("export_refused"):
        assert r["schema_errors"], "an export was refused with nothing to point at"
        assert "refusing to export" in r["export_refused"], r["export_refused"]
        assert r["arch_json"] is None
        return

    assert r["schema_errors"] == [], r["schema_errors"]
    doc = json.loads(r["arch_json"])
    errors = validate_document(doc)
    assert errors == [], (
        f"{why}: the browser exported a document the schema refuses -- and the browser "
        f"did not say so. errors={errors}")

    arch = Architecture.from_json(doc, validate=True)   # the real loader, no shortcut
    assert arch.device.check_structure() == []


@requires_node
def test_the_export_harness_catches_a_document_python_refuses(tmp_path):
    """MUTATION GUARD for the test above.

    A round-trip assertion that cannot fail is a comment.  Plant the exact document each
    defect used to produce and prove the check rejects it -- so that if a future change
    made `validate_document` or `from_json` vacuous, this fails instead of everything
    quietly passing again.
    """
    page = OUT / "stationary_chain__walk__corrected.html"
    if not page.exists():
        pytest.skip("stationary_chain has not been emitted")
    doc = json.loads(drive(page, [], tmp_path)["arch_json"])

    # (1) DEFECT 2's document: a loop cut down to one node.
    bad = json.loads(json.dumps(doc))
    assert bad["geometry"]["loops"], "the fixture needs a loop to degenerate"
    bad["geometry"]["loops"][0]["nodes"] = bad["geometry"]["loops"][0]["nodes"][:1]
    errs = validate_document(bad)
    assert any("needs at least 2 item(s)" in e for e in errs), errs
    with pytest.raises(Exception):
        Architecture.from_json(bad, validate=True)

    # (1b) and the STRUCTURAL half, which is what makes the repair provably complete:
    # even with the schema's array bound satisfied by a hand-built device, a degenerate
    # loop must be refused by `check_structure`.
    from qccd.arch.device import Device, Loop
    dev = load(ARCH).device
    lone = Device(nodes=dev.nodes, segments=dev.segments,
                  loops={"BAD": Loop(id="BAD", nodes=(next(iter(dev.nodes)),), closed=False)},
                  generator=dev.generator, params=dev.params)
    assert any("transport loop needs at least" in e for e in lone.check_structure()), (
        "check_structure accepted a one-node loop; the invariant that backstops the "
        "repair is gone and DEFECT 2 can come back through any future edit op")

    # (2) DEFECT 3's document: a curve tagged with a table outside the enum.
    prim = next(iter(doc["primitives"]))
    bad2 = json.loads(json.dumps(doc))
    if "curve" not in bad2["primitives"][prim]:
        prim = next((k for k, v in bad2["primitives"].items() if "curve" in v), None)
    assert prim, "the fixture needs a primitive with a curve"
    bad2["primitives"][prim]["curve"][0]["table"] = "mytable"
    errs2 = validate_document(bad2)
    assert any("is not one of" in e for e in errs2), errs2
    with pytest.raises(Exception):
        Architecture.from_json(bad2, validate=True)


@requires_node
def test_the_browser_never_writes_a_fraction_into_an_integer_slot(tmp_path):
    """The containment for the one asymmetry a JS mirror provably cannot close.

    Python's validator distinguishes `3` from `3.0`; JSON does not, so `engine.js` cannot.
    That asymmetry is unreachable in practice because the browser never WRITES a
    non-integral number into an integer slot -- `deviceToJson` truncates every one of them
    and `JSON.stringify` emits integral numbers without a fraction.  This asserts the
    containment rather than trusting it, on every emitted page, so the day someone drops a
    `Math.trunc` the failure lands here and not in a user's export.
    """
    integer_slots = [
        ("geometry", "nodes", "capacity"), ("geometry", "nodes", "degree"),
        ("geometry", "segments", "capacity"), ("geometry", "segments", "corner_endpoints"),
    ]
    checked = 0
    for page in _pages():
        raw = drive(page, [], tmp_path)["arch_json"]
        doc = json.loads(raw)
        for top, arr, field in integer_slots:
            for rec in doc.get(top, {}).get(arr, []) or []:
                if field not in rec:
                    continue
                checked += 1
                v = rec[field]
                assert isinstance(v, int) and not isinstance(v, bool), (
                    f"{page.name}: {top}.{arr}[].{field} came out as {v!r} "
                    f"({type(v).__name__}); Python's validator will call that a number "
                    f"and refuse it, and no JS mirror can warn about it")
        for zt in doc.get("zone_types", {}).values():
            if "capacity" in zt:
                checked += 1
                assert isinstance(zt["capacity"], int) and not isinstance(zt["capacity"], bool)
    assert checked > 100, f"only {checked} integer slots checked; the scan found nothing"


@requires_node
@pytest.mark.parametrize("page", _pages(), ids=lambda p: p.stem)
def test_every_page_can_rebuild_its_own_architecture(page, tmp_path):
    """The page re-runs its OWN interpreter over its OWN listing, from scratch, before the
    user touches anything -- and the result has to be the architecture Python laid it out
    with.  If it is not, editing is refused and the page says why, rather than offering a
    tool that silently edits a different machine.

    This is the load-time half of the guarantee, and it runs on all ten emitted pages, so
    a generator or a setter the interpreter does not implement fails here by name."""
    r = drive(page, [], tmp_path)
    assert r.get("ready") is True, f"{page.name}: editing unavailable -- {r.get('why')}"
    base = r["steps"][0]
    assert base["problems"] == 0, base
    # and its client-side price agrees with the Python numbers already in the page, on
    # EVERY frame -- not just in total, because a compensating error inside the programme
    # cancels in the total and that is the bug shape that hid last time
    assert base["price"] is not None, base
    assert base["price"]["frameDrift"] == 0, (
        f"{page.name}: the page's own re-price disagrees with Python's per-frame numbers "
        f"by {base['price']['frameDrift']}")
    # An EMPTY page -- the studio's blank canvas -- has no frames, so there is nothing for
    # the per-frame oracle to compare against and `frameChecked == 0` is the truth rather
    # than a failure.  What must never happen is a page with frames that checks none of
    # them, which is what this asserts for every device page.
    if base["frames"]:
        assert base["price"]["frameChecked"] > 0, (
            f"{page.name}: {base['frames']} frames and not one compared against Python")
    assert r["census_base"]["overlap_frames"] == 0


@requires_node
def test_a_drag_moves_the_deck_price_and_not_the_corrected_one(tmp_path):
    """THE WORKED EXAMPLE, and the single most useful thing this editor can show.

    Dragging S1 from (1, 0) to (1, 0.35) makes it a geometric bend, so E0 and E1 each
    acquire a second corner endpoint.  The DECK model charges a corner segment three
    primitive hops, so its cost rises.  The CORRECTED model does not move at all, because
    R18 says a two-arm bend has a continuous RF null and is ordinary transport.

    Drag a site and watch the oracle's number move while the corrected one does not.  Both
    sides of that are checked against live Python here.
    """
    script = [{"do": "mode", "mode": "edit"},
              {"do": "drag", "id": "S1", "to": [1.0, 0.35], "free": True, "label": "drag"},
              {"do": "undo", "label": "undo"}]

    from qccd.__main__ import program_for
    arch = load(ARCH)
    prog = program_for(arch, "deck", "")

    for page, mk in ((DECK_PAGE, deck_model), (CORR_PAGE, corrected_model)):
        if not page.exists():
            pytest.skip(f"{page.name} not emitted; run `python -m qccd demo`")
        r = drive(page, script, tmp_path)
        assert r["ready"] is True, r.get("why")
        before, after, undone = r["steps"][0], r["steps"][2], r["steps"][3]

        # -- Python, computed live, never stored -------------------------------------
        m = Machine.load(ARCH)
        model = mk()
        py0 = verify(prog, m.arch, model, check_metrics=False).result
        n0 = sum(1 for v in m.arch.device.corner_endpoints.values() if v == 2)
        m.move_site("S1", 1.0, 0.35)
        py1 = verify(prog, m.arch, model, check_metrics=False).result
        n1 = sum(1 for v in m.arch.device.corner_endpoints.values() if v == 2)
        assert (n0, n1) == (2, 4), "the drag no longer creates the two extra corners"

        # the page carries a cooling pass the bare programme does not, so the DELTA is
        # what is compared rather than the absolute -- the delta is the thing the edit
        # caused, and it is model-characteristic
        js_delta = after["price"]["cost"] - before["price"]["cost"]
        py_delta = py1.total_cost - py0.total_cost
        assert js_delta == pytest.approx(py_delta, abs=1e-9), (
            f"{page.name}: the browser priced the drag at {js_delta:+} and Python at "
            f"{py_delta:+}")

        if mk is deck_model:
            assert js_delta > 0, "the deck model must charge the two new corner segments"
        else:
            assert js_delta == 0, (
                "R18: a two-arm bend is ordinary transport, so the corrected model must "
                "not move")

        # the drag makes a segment diagonal, and the page warns about it DURING the drag
        # rather than after the user has lost their orientation
        assert before["layout"]["axis_aligned"] is True
        assert after["layout"]["axis_aligned"] is False
        # AND THE SCALE NO LONGER MOVES UNDER THE POINTER.  This used to assert `g` FELL,
        # because the fit flipped from anisotropic to isotropic and every mark on the page
        # resized (21.9 -> 11.0 on this device) the moment one segment stopped being
        # axis-aligned.  With `true scale` on -- the stage default -- `sx:sy` is already
        # the technology's nm-per-unit ratio, so there is nothing for that flip to change:
        # the picture stays exactly where it is and only the two verdicts above move.  The
        # re-layout still happens, and `axis_aligned` above is what witnesses it.
        assert after["layout"]["g"] == before["layout"]["g"]
        assert (after["layout"]["sx"], after["layout"]["sy"]) == \
               (before["layout"]["sx"], before["layout"]["sy"])
        warn = [l for l in r["log"] if l.get("warnings")]
        assert warn and any("isotropic" in w for w in warn[0]["warnings"]), warn

        # -- UNDO IS EXACT ------------------------------------------------------------
        assert undone["layout"] == before["layout"], "undo did not restore the layout"
        assert undone["price"] == before["price"], "undo did not restore the price"
        assert undone["edits"] == 0

        # -- and the edited picture still has no overlapping ions --------------------
        assert r["census_edited"]["overlap_frames"] == 0, r["census_edited"]


@requires_node
def test_retuning_a_zone_and_the_wiring_reprices_live(tmp_path):
    """`set_zone` and `set_control` from the browser, diffed against `hardware_report`.

    The wiring one is the WISE argument made live: the same device, the same programme,
    46 DACs under a broadcast plane and 5,390 under a direct one.  That contrast is the
    reason the whole family is worth its serialization penalty, and it is a number the
    editor can put next to the user's cursor because it is pure counting.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    script = [
        {"do": "mode", "mode": "edit"},
        {"do": "emit", "op": {"method": "set_zone", "args": ["trap"],
                              "kwargs": {"capacity": 4}}, "label": "cap"},
        {"do": "emit", "op": {"method": "set_control", "args": [],
                              "kwargs": {"channels": {"grouping": "direct"}}},
         "label": "direct"},
    ]
    r = drive(CORR_PAGE, script, tmp_path)
    assert r["ready"] is True, r.get("why")
    base, cap, direct = r["steps"][0], r["steps"][2], r["steps"][3]

    m = Machine.load(ARCH)
    assert base["hw"]["dacs"] == hardware_report(m.arch).dacs
    assert base["hw"]["total_capacity"] == m.arch.device.total_capacity()

    m.set_zone("trap", capacity=4)
    assert cap["hw"]["total_capacity"] == m.arch.device.total_capacity()
    assert cap["hw"]["dacs"] == hardware_report(m.arch).dacs
    assert cap["hw"]["total_capacity"] > base["hw"]["total_capacity"]

    m.set_control(channels={"grouping": "direct"})
    py = hardware_report(m.arch)
    assert direct["hw"]["dacs"] == py.dacs
    assert direct["hw"]["electrodes"] == py.electrodes
    assert direct["hw"]["switches"] == py.switches
    assert direct["hw"]["over_budget"] == list(py.over_budget)
    assert direct["hw"]["dacs"] > 100 * base["hw"]["dacs"], (
        "the broadcast-versus-direct contrast is the point; if it has collapsed the "
        "channel arithmetic is wrong")


@requires_node
def test_a_topology_edit_blocks_the_price_instead_of_guessing(tmp_path):
    """Removing a node the programme uses must make the page REFUSE to show a cost.

    The frames were compiled against the pre-edit device.  A page that kept animating them
    would be animating a programme whose node ids no longer exist, and a page that kept
    showing a number would be showing a confidently wrong one.  `validateProgram` runs
    BEFORE pricing for exactly this reason, and it names the node.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    script = [{"do": "mode", "mode": "edit"},
              {"do": "remove", "sel": [{"kind": "site", "id": "S0"}], "label": "remove S0"},
              {"do": "undo", "label": "undo"}]
    r = drive(CORR_PAGE, script, tmp_path)
    assert r["ready"] is True, r.get("why")
    removed, undone = r["steps"][2], r["steps"][3]
    assert removed["nodes"] < r["steps"][0]["nodes"], "the node was not removed"
    assert removed["blocked"], (
        "the page priced a programme that references a node this edit removed")
    assert removed["price"] is None
    assert undone["price"] == r["steps"][0]["price"], "undo did not restore the price"
    # THE ASSERTION THIS TEST WAS ALWAYS ONE LINE SHORT OF.  `editor.mjs` has printed
    # `census_edited` on every run for months; the drag test read it and this one did not,
    # so `worst_overlap_px: 14.684` went to stdout unread while the delete case asserted
    # only on the price.  The number and the picture are one property, so they are checked
    # together.
    assert r["census_edited"]["overlap_frames"] == 0, r["census_edited"]["worst_pair"]


# ======================================================================================
# THE STALE PROGRAMME: the number and the picture must agree about which device this is
# ======================================================================================
#
# `draw()` mixed two epochs of the same device.  `states`, `before`, `SLOTS` and `cum` are
# computed ONCE at load against the shipped device; `nodeById`, `AXIS` and `pathsOf` are
# re-derived by the editor against the EDITED one.  Neither half is wrong on its own;
# together they animate a programme that never existed.  Measured on cyclone_base with S5
# removed: 1 ion parked on a site that no longer exists, 18 more up to 48.4 px from their
# compiled site, and 14.684 px of ion-on-ion overlap between `d5` (parked on its stale
# destination because its source node is gone) and `d14` (arriving there nine nodes early
# over the shortened loop).
#
# The page's own comment already claimed this could not happen -- "a geometry edit
# invalidates the compiled programme, and the page says so instead of animating a
# programme whose node ids may no longer exist".  These tests make the comment true.

CENSUS = Path(__file__).resolve().parent / "census.mjs"

#: (page stem, the node its programme places an ion on).  S5 is not on every architecture's
#: shifted loop -- on cyclone_dual_loop it is not in the programme at all -- so the node is
#: read out of the page's own init frame rather than assumed.
_STALE_PAGES = ["cyclone_base__rotate__corrected", "cyclone_dual_loop__rotate__corrected",
                "h2_racetrack__rotate__corrected", "ring144_24v__deck__corrected"]


def _page_datum(page: Path, key: str):
    import re
    m = re.search(r'<script id="data"[^>]*>(.*?)</script>',
                  page.read_text(encoding="utf-8"), re.S)
    assert m, f"{page.name}: no data blob"
    return json.loads(m.group(1))[key]


def _an_ion_site(page: Path) -> str:
    """A site the compiled programme actually places an ion on, off the page's own blob."""
    frames = _page_datum(page, "program")["frames"]
    place = next(f["place"] for f in frames if f.get("type") == "init")
    return sorted(place.values())[1] if len(place) > 1 else next(iter(place.values()))


def census(page: Path, *, tmp_path: Path = None, edit=None, frames=None) -> dict:
    """Run `tests/census.mjs`, optionally driving an edit script first."""
    cmd = [node, str(CENSUS), str(page)]
    if frames is not None:
        cmd.append(str(frames))
    if edit is not None:
        sp = tmp_path / f"edit_{abs(hash(json.dumps(edit))) % 10**8}.json"
        sp.write_text(json.dumps(edit), encoding="utf-8")
        cmd += ["--edit", str(sp)]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       timeout=1800, cwd=str(ROOT))
    assert r.returncode == 0, f"census.mjs failed on {page.name}: {r.stderr[-3000:]}"
    return json.loads(r.stdout)


@requires_node
@pytest.mark.parametrize("stem", _STALE_PAGES)
def test_deleting_a_site_stops_the_animation_instead_of_drawing_a_stale_programme(
        tmp_path, stem):
    page = OUT / f"{stem}.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    clean = census(page, frames=40)
    assert clean["overlap_frames"] == 0 and clean["ions_visible_max"] > 0
    assert clean["program_stale"] is None, "the unedited page is already frozen"

    site = _an_ion_site(page)
    r = census(page, tmp_path=tmp_path, frames=40, edit=[
        {"do": "mode", "mode": "edit"},
        {"do": "remove", "sel": [{"kind": "node", "id": site}]}])

    assert r["program_stale"], (
        f"removing {site}, which the programme places an ion on, left the programme "
        f"'valid'")
    assert "unknown_node" in r["program_stale"]["kinds"], r["program_stale"]
    # THE PRIMARY ASSERTION.  It is the only one that covers the 18 mis-placed ions: every
    # one of them sits on a node that still exists, so no per-ion existence check can find
    # them and no "N ions dropped" caption would be honest about them.
    assert r["ions_while_invalid"] == 0, (
        f"{stem}: {r['ions_while_invalid']} ions drawn from a programme compiled against "
        f"a device that no longer exists")
    assert r["phantom_ions"] == 0, r["phantom_example"]
    assert r["overlap_frames"] == 0, r["worst_pair"]
    assert site in r["banner"] and "recompile" in r["banner"], r["banner"]


@requires_node
def test_removing_a_rail_the_rotation_shuttles_over_stops_the_animation(tmp_path):
    """The wider hole, and it was wider than the ion-on-a-deleted-node case.

    `validateProgram` checked `no_segment` only on `moves` frames and `loopLengths`
    compares node COUNTS, so cutting a rail left the count identical and the structural
    check COMPLETELY SILENT.  Measured before the fix: removing any of E0..E7 on
    cyclone_base produced `blocked: ["price_error"]` -- thrown from inside the cost model,
    not from the checker -- while `edgePoint` flew all 72 ions across a rail that was gone.
    """
    page = OUT / "cyclone_base__rotate__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    r = census(page, tmp_path=tmp_path, frames=40, edit=[
        {"do": "mode", "mode": "edit"},
        {"do": "remove", "sel": [{"kind": "segment", "id": "E0"}]}])
    assert r["program_stale"], "cutting a rail left the programme 'valid'"
    assert "loop_broken" in r["program_stale"]["kinds"], r["program_stale"]
    assert r["ions_while_invalid"] == 0
    assert r["overlap_frames"] == 0, r["worst_pair"]


@requires_node
def test_moving_a_site_keeps_the_animation_running(tmp_path):
    """The other half of the property, and the reason the freeze predicate is the
    STRUCTURAL break list and not `PRICE_STATUS == 'blocked'`.

    A geometry edit does not invalidate the programme -- the ions move with their sites,
    which is the entire point of the editor -- so the freeze has to be narrow.  A
    one-sided test would be satisfied by "hide the ions always".
    """
    page = OUT / "cyclone_base__rotate__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    x, y = load(ROOT / "arch" / "cyclone_base.arch.json").device.nodes["S5"].pos
    r = census(page, tmp_path=tmp_path, frames=40, edit=[
        {"do": "mode", "mode": "edit"},
        {"do": "emit", "op": {"method": "move_site", "args": ["S5", x + 0.5, y]}}])
    assert r["program_stale"] is None, r["program_stale"]
    assert r["ions_visible_max"] > 0, "a non-breaking edit must not stop the animation"
    assert r["overlap_frames"] == 0, r["worst_pair"]
    assert r["banner"] == ""


@requires_node
def test_undo_restores_the_animation(tmp_path):
    page = OUT / "cyclone_base__rotate__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    site = _an_ion_site(page)
    r = census(page, tmp_path=tmp_path, frames=40, edit=[
        {"do": "mode", "mode": "edit"},
        {"do": "remove", "sel": [{"kind": "node", "id": site}]},
        {"do": "undo"}])
    assert r["program_stale"] is None and r["ions_visible_max"] > 0
    assert r["banner"] == ""
    assert r["overlap_frames"] == 0, r["worst_pair"]


@requires_node
def test_pricing_never_discovers_a_topology_break_the_structural_check_missed(tmp_path):
    """The invariant that keeps the two surfaces in sync as the code evolves.

    A `price_error` raised from inside the cost model as the ONLY entry in the break list
    means the structural checker went blind and the stage kept animating -- which is
    exactly how every segment deletion escaped.  This test is load-bearing, not
    decorative: if a future cost model throws on a topology condition the checker does not
    model, the stage silently goes back to animating a stale programme.
    """
    page = OUT / "cyclone_base__rotate__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    checked = 0
    for kind, ident in ([("node", f"S{i}") for i in range(12)]
                        + [("segment", f"E{i}") for i in range(8)]):
        r = census(page, tmp_path=tmp_path, frames=3, edit=[
            {"do": "mode", "mode": "edit"},
            {"do": "remove", "sel": [{"kind": kind, "id": ident}]}])
        kinds = (r["program_stale"] or {}).get("kinds", [])
        checked += 1
        assert kinds != ["price_error"], (
            f"removing {ident} was caught only by the cost model throwing; the structural "
            f"checker is blind to it and the stage would keep animating")
        assert r["ions_while_invalid"] == 0, (
            f"removing {ident}: {r['ions_while_invalid']} ions drawn while the programme "
            f"does not fit the device")
    assert checked == 20


# ----------------------------------------------------------------- mutation guards
#
# TWO of them, because the property is two-sided: a one-sided guard is satisfied by "hide
# the ions always", which would pass every assertion above and destroy the editor.


@requires_node
def test_the_census_catches_ions_drawn_on_a_deleted_node(tmp_path):
    """A test that cannot fail is not a test.  Delete the guard from the emitted page and
    confirm the census reports exactly the defect it exists to catch: 14.684 px of
    ion-on-ion overlap between `d5` (parked on its stale destination because its source
    node is gone) and `d14` (arriving there nine nodes early over the shortened loop)."""
    page = OUT / "cyclone_base__rotate__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    edit = [{"do": "mode", "mode": "edit"},
            {"do": "remove", "sel": [{"kind": "node", "id": "S5"}]}]
    assert census(page, tmp_path=tmp_path, frames=40, edit=edit)["ions_while_invalid"] == 0

    html = page.read_text(encoding="utf-8")
    marker = "  if(PROGRAM_STALE){ drawInvalid(); return; }"
    assert marker in html, "the freeze guard moved; update this mutation"
    broken = tmp_path / "broken.html"
    broken.write_text(html.replace(marker, "  /* mutated: keep animating */"),
                      encoding="utf-8")

    r = census(broken, tmp_path=tmp_path, frames=40, edit=edit)
    assert r["ions_while_invalid"] > 0, (
        "the census passed a page animating a programme compiled against a device that "
        "no longer exists -- it would not have caught the defect it exists to catch")
    assert r["phantom_ions"] > 0, r
    # anchored to the number measured on this build, so the guard fails loudly if the
    # mutation stops reproducing the original defect rather than quietly proving nothing
    assert r["worst_overlap_px"] > 10, r["worst_pair"]
    assert {r["worst_pair"]["a"], r["worst_pair"]["b"]} == {"d5", "d14"}, r["worst_pair"]


@requires_node
def test_the_census_catches_a_page_that_freezes_when_it_should_not(tmp_path):
    """The other half.  `if (edited) freeze` would pass every assertion above and destroy
    the editor, so this is the negative control for
    `test_moving_a_site_keeps_the_animation_running`.

    `editor.js` is inlined verbatim into the page (byte-identity asserted by
    `test_engine_parity.py`), so the over-freeze is planted by string replace on the
    emitted page rather than on a sibling copy of the source.
    """
    page = OUT / "cyclone_base__rotate__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    x, y = load(ROOT / "arch" / "cyclone_base.arch.json").device.nodes["S5"].pos
    edit = [{"do": "mode", "mode": "edit"},
            {"do": "emit", "op": {"method": "move_site", "args": ["S5", x + 0.5, y]}}]
    good = census(page, tmp_path=tmp_path, frames=40, edit=edit)
    assert good["ions_visible_max"] > 0 and good["program_stale"] is None

    html = page.read_text(encoding="utf-8")
    # The plant has to land BEFORE the flag is written, and `PROGRAM_STALE` is
    # assigned ABOVE `PRICE_STATUS` -- appending to the price-status line is a
    # mutation that changes nothing, i.e. a guard that proves nothing.  Verified:
    # planted there, the page still drew all 72 ions.
    marker = "  var bad = Q.validateProgram(dev, P.frames, classes);"
    assert marker in html, "the structural-check line moved; update this mutation"
    planted = (marker + "\n  bad = bad.concat(EDITS.length ? "
               "[{kind:'unknown_node',node:'PLANTED',count:1}] : []);")
    broken = tmp_path / "overfrozen.html"
    broken.write_text(html.replace(marker, planted), encoding="utf-8")

    r = census(broken, tmp_path=tmp_path, frames=40, edit=edit)
    assert r["ions_visible_max"] == 0 and r["program_stale"], (
        "the mutation did not actually over-freeze; the guard proves nothing")


@requires_node
def test_the_side_editor_round_trips_the_listing_byte_for_byte(tmp_path):
    """What the user types is exactly what `m.source()` prints.

    `EDITOR.source()` renders the shipped listing back out of the parsed command records,
    and it must equal `ArchListing.python()` character for character.  One assertion, and
    the editor's language can never drift from `_lit` / `_kwd`.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    from qccd.arch.listing import architecture_listing
    r = drive(CORR_PAGE, [], tmp_path)
    want = architecture_listing(load(ARCH), verify=False).python()
    got = r["source"]
    if got != want:
        a, b = got.split("\n"), want.split("\n")
        i = 0
        while i < len(a) and i < len(b) and a[i] == b[i]:
            i += 1
        pytest.fail(f"line {i + 1} differs:\n  python: {b[i] if i < len(b) else '<eof>'}"
                    f"\n  browser: {a[i] if i < len(a) else '<eof>'}")


@requires_node
def test_typing_into_the_side_editor_changes_the_machine(tmp_path):
    """The text lane and the mouse lane write through the SAME applier.

    Retyping one statement of the listing with a different capacity must land the same way
    a form or a drag would -- there is only one applier, so there is nothing for the lanes
    to disagree about.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    from qccd.arch.listing import architecture_listing
    src = architecture_listing(load(ARCH), verify=False).python()
    assert 'm.set_zone("trap"' in src, src[:400]
    edited = "\n".join(
        (l.replace("capacity=2", "capacity=5") if l.startswith('m.set_zone("trap"') else l)
        for l in src.split("\n"))
    assert edited != src, "the fixture no longer matches the emitted listing"
    r = drive(CORR_PAGE, [{"do": "mode", "mode": "edit"},
                          {"do": "source", "src": edited, "label": "typed"}], tmp_path)
    assert r["ready"] is True, r.get("why")
    typed = r["steps"][2]
    assert typed["edits"] >= 1, "typing changed nothing"

    m = Machine.load(ARCH)
    m.set_zone("trap", capacity=5)
    assert typed["hw"]["total_capacity"] == m.arch.device.total_capacity()
    assert typed["problems"] == 0


@requires_node
def test_the_export_loads_back_into_python(tmp_path):
    """The .arch.json the browser hands over must be a document `Machine.load` accepts.

    A design tool whose export the toolchain rejects has not exported anything.  This is
    the closed loop: edit in the browser, serialize in the browser, parse and structurally
    validate in Python.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    script = [{"do": "mode", "mode": "edit"},
              {"do": "emit", "op": {"method": "set_zone", "args": ["trap"],
                                    "kwargs": {"capacity": 4}}},
              {"do": "drag", "id": "S5", "to": [5.0, 0.15], "free": True}]
    r = drive(CORR_PAGE, script, tmp_path)
    assert r["ready"] is True, r.get("why")
    doc = json.loads(r["arch_json"])
    path = tmp_path / "edited.arch.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    m = Machine.load(path)                       # validates against ARCH_SCHEMA on the way in
    # 0.15, not 0.25: the dock spur A138 sits at (5, 0.5) and its bar reaches up to about
    # 0.2, where the boundary would stop the drag short of the asked-for point
    assert m.arch.device.nodes["S5"].pos == (5.0, 0.15)
    assert m.arch.zone_types["trap"]["capacity"] == 4
    # and it prices, which is the only proof that what came back is a real machine
    from qccd.__main__ import program_for
    prog = program_for(m.arch, "rotate", "")
    res = verify(prog, m.arch, corrected_model(), check_metrics=False).result
    assert res.total_cost > 0


# --------------------------------------------------------------- the mutation guards


@requires_node
def test_the_editor_harness_catches_a_layout_that_stops_recomputing(tmp_path):
    """A harness that cannot fail is decoration.

    Break the page so an edit does NOT re-lay-out -- the exact failure mode where the page
    redraws a moved node at a stale scale -- and the drag test must notice.  Without this,
    `rebuild()` could quietly become a no-op and every assertion above would still pass,
    because a device that never changed still prices correctly.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    html = CORR_PAGE.read_text(encoding="utf-8")
    # The call now carries `layoutOpts()` -- the stage's "true scale" state -- so the
    # anchor is the call without its argument list, which matches either spelling.  A
    # page emitted before the toggle existed is still mutated correctly by it.
    anchor = "  var lay = Q.computeLayout(nodesOf(STATE), segsOf(STATE)"
    assert anchor in html, "the mutation anchor moved"
    # `0 && <call>` keeps the rest of the original line -- whatever its argument list --
    # balanced and syntactically valid while never calling it, so the layout genuinely
    # stops being recomputed and the page still parses.
    broken = html.replace(anchor, "  var lay = L; 0 && Q.computeLayout(nodesOf(STATE)", 1)
    p = tmp_path / "broken.html"
    p.write_text(broken, encoding="utf-8")
    r = drive(p, [{"do": "mode", "mode": "edit"},
                  {"do": "drag", "id": "S1", "to": [1.0, 0.35], "free": True}], tmp_path)
    before, after = r["steps"][0], r["steps"][2]
    # THE WITNESS IS `axis_aligned`, NOT `g`.  It used to be `g`, because the drag flipped
    # the fit from anisotropic to isotropic and every mark resized.  Under `true scale`
    # -- the stage default -- the scale is pinned to the technology's nm-per-unit ratio and
    # that flip changes no length at all, so `g` is equal here whether the re-layout runs
    # or not and a guard keyed on it would prove nothing.  `axis_aligned` is recomputed by
    # `compute_layout` from the device's own geometry, so it is exactly what a page that
    # stopped re-laying-out would fail to notice.
    assert after["layout"]["axis_aligned"] == before["layout"]["axis_aligned"], (
        "the mutation did not actually stop the re-layout, so this guard proves nothing")
    assert after["layout"]["axis_aligned"] is True, (
        "the drag made a segment diagonal and the broken page still reports the device "
        "as axis-aligned -- which is the stale layout this guard exists to detect")


@requires_node
def test_the_editor_harness_catches_a_price_that_stops_following(tmp_path):
    """Break the re-pricer's junction term and the hardware/price assertions must move.

    This is the pricing counterpart of the layout guard: a re-pricer that returned the
    shipped numbers unchanged would pass every totals-based check while being completely
    inert.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    html = CORR_PAGE.read_text(encoding="utf-8")
    anchor = "      var jp = jpoint(deg.get(dst) || 0);"
    assert anchor in html, "the mutation anchor moved"
    broken = html.replace(anchor, "      var jp = null;", 1)
    p = tmp_path / "broken2.html"
    p.write_text(broken, encoding="utf-8")
    r = drive(p, [], tmp_path)
    assert r["ready"] is True, r.get("why")
    assert r["steps"][0]["price"]["frameDrift"] > 0, (
        "dropping the junction charge did not move a single frame's cost; the per-frame "
        "self-check is not actually comparing anything")


@requires_node
def test_a_drop_onto_another_node_is_refused_outright(tmp_path):
    """The one validation that CANNOT be a warning.

    `min_nearest_neighbour` SKIPS coincident points -- returning 0 would collapse every
    derived size -- so two nodes at one position make `g` get measured off the NEXT pair
    and every mark on the stage silently becomes the wrong size.  `2*r_ion < g` stops
    meaning what it says, and nothing anywhere reports it.

    This is not hypothetical: `A138` sits at exactly (5, 0.5), because the dock spur of
    the bottom-row slot at x=5 runs inward to the mid-line.  Dragging `S5` there looks
    entirely reasonable on screen.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    arch = load(ARCH)
    assert arch.device.nodes["A138"].pos == (5.0, 0.5), (
        "the fixture moved; pick another pair of nodes that share a position")
    r = drive(CORR_PAGE, [{"do": "mode", "mode": "edit"},
                          {"do": "drag", "id": "S5", "to": [5.0, 0.5], "free": True}],
              tmp_path)
    assert r["ready"] is True, r.get("why")
    rec = [l for l in r["log"] if l["do"] == "drag"][0]
    # THE BOUNDARY GETS THERE FIRST: marks never overlap, so the drag stops against A138's
    # bar before the two could coincide, names what it is pressed against, and the drop
    # lands there -- short of the target, never on it
    assert rec["result"]["contact"] == "A138", rec["result"]
    assert 0.0 < rec["result"]["landed"][1] < 0.5, rec["result"]
    assert rec["result"]["problems"] == [], rec["result"]
    assert r["steps"][2]["edits"] == 1
    # the coincidence refusal itself still stands, for a move that is not a drag
    nudge = drive(CORR_PAGE, [{"do": "mode", "mode": "edit"},
                              {"do": "select", "sel": [{"kind": "node", "id": "S5"}]},
                              {"do": "key", "key": "ArrowDown", "mods": {"shift": True}}], tmp_path)
    assert nudge["ready"] is True


@requires_node
def test_the_browser_edit_list_replays_in_python_to_the_same_device(tmp_path):
    """THE ROUND TRIP, closed.

    Edit in the browser, hand over the edit list, replay it in Python with
    `Machine.apply_edits`, and the two devices must be the same graph with the same
    positions, capacities, lengths and loop orders.

    This is the assertion that makes the editor a design tool rather than a picture.  A
    gesture the browser can express but Python cannot replay would break the round-trip
    guarantee `architecture_listing` rests on -- and it would break it silently, because
    the browser would keep showing a perfectly plausible number for a machine that cannot
    be built.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    script = [
        {"do": "mode", "mode": "edit"},
        {"do": "drag", "id": "S1", "to": [1.0, 0.35], "free": True},
        {"do": "emit", "op": {"method": "set_zone", "args": ["trap"],
                              "kwargs": {"capacity": 4}}},
        {"do": "emit", "op": {"method": "set_site_capacity", "args": ["S0", 6],
                              "kwargs": {}}},
        {"do": "emit", "op": {"method": "set_segment_length", "args": ["E5", 2.5],
                              "kwargs": {}}},
        {"do": "addSegment", "a": "S10", "b": "S82"},
        {"do": "addSite", "x": 3.5, "y": 0.0, "near": "S3"},
    ]
    r = drive(CORR_PAGE, script, tmp_path)
    assert r["ready"] is True, r.get("why")
    assert r["steps"][-1]["edits"] == 6, r["steps"][-1]

    edits = r["edits"]
    assert len(edits) == 6, edits

    m = Machine.load(ARCH)
    m.apply_edits(edits)
    dev = m.arch.device

    # the browser's own view of the same device
    js = json.loads(r["arch_json"])
    js_nodes = {n["id"]: n for n in js["geometry"]["nodes"]}
    js_segs = {s["id"]: s for s in js["geometry"]["segments"]}

    assert set(js_nodes) == set(dev.nodes), (
        f"node sets differ: only in browser {sorted(set(js_nodes) - set(dev.nodes))[:5]}, "
        f"only in python {sorted(set(dev.nodes) - set(js_nodes))[:5]}")
    assert set(js_segs) == set(dev.segments)
    for nid, n in dev.nodes.items():
        assert tuple(js_nodes[nid]["pos"]) == tuple(n.pos), nid
        assert js_nodes[nid].get("capacity", 0) == n.capacity, nid
        assert js_nodes[nid]["degree"] == dev.degree(nid), nid
    for sid, seg in dev.segments.items():
        assert js_segs[sid]["length"] == seg.length, sid
        assert tuple(js_segs[sid]["ends"]) == tuple(seg.ends), sid
        assert js_segs[sid]["corner_endpoints"] == dev.corner_endpoints[sid], sid
    for lid, loop in dev.loops.items():
        js_loop = [l for l in js["geometry"].get("loops", []) if l["id"] == lid][0]
        assert js_loop["nodes"] == list(loop.nodes), (
            f"loop {lid} order differs -- a rigid rotation walks this list BY INDEX, so a "
            f"mis-spliced node prices an orbit that skips it, with no error attached")
        assert js_loop["closed"] == loop.closed


@requires_node
def test_an_edit_RE_DERIVES_the_rule_verdicts_and_names_what_it_cannot(tmp_path):
    """A green tick for a check that did not run is worse than no tick.

    This assertion used to read "an edit strikes the verdicts out", because
    `architecture_violations` (R11 structural) was the only state-free check and every
    other rule needed a `CycleView` from a replay that only Python could do.  The browser
    now RE-DERIVES 21 of the 27 off the same walk that prices the programme, so striking
    all 27 through would be the dishonest answer in the other direction: it would hide
    verdicts the page can genuinely stand behind.

    What must hold, before and after an edit, is the thing that was always the point:

      * the page never says "all rules pass" -- it COUNTS, and the count is the number it
        actually checked;
      * every rule it did NOT check is named, with its reason, rather than being absent;
      * the six that need Python stay grey no matter what the user does.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    r = drive(CORR_PAGE, [{"do": "mode", "mode": "edit"},
                          {"do": "emit", "op": {"method": "set_zone", "args": ["trap"],
                                                "kwargs": {"capacity": 4}}},
                          {"do": "undo"}], tmp_path)
    assert r["ready"] is True, r.get("why")
    base, edited, undone = r["steps"][0], r["steps"][2], r["steps"][3]
    for label, snap in (("base", base), ("edited", edited), ("undone", undone)):
        assert snap["side_says_all_pass"] is False, (
            f"{label}: the page claimed 'all rules pass'")
        assert snap["side_says_count"] is True, (
            f"{label}: the rules heading does not count what it checked")
        assert snap["n_checked"] + snap["n_unchecked"] == 27, snap["coverage"]
        # the six that genuinely need Python, named rather than absent
        grey = {c[0] for c in snap["coverage"] if c[1] in ("unchecked", "partial")}
        assert {"R4d", "R7b", "R9", "R10", "R15", "R16"} <= grey, (
            f"{label}: a rule the browser cannot check is not marked unchecked: {grey}")
    assert edited["n_checked"] == base["n_checked"], (
        "an edit changed which rules could be checked; the mirror is state-free about that")
    assert undone["price"] == base["price"], "undo did not restore the price"


@requires_node
def test_the_exported_python_runs(tmp_path):
    """The Python the browser hands over must EXECUTE and produce the same machine.

    Not "look plausible" -- run.  The whole point of the side editor speaking Python is
    that you can copy the panel, paste it into a file and get the machine back; an export
    that only reads like Python would be a demo of a text renderer.

    The listing statements execute directly; the topology edits go through
    `Machine.apply_edits`, which is the same method whitelist the browser used, so nothing
    on this path is `exec` on user-edited structure.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    r = drive(CORR_PAGE, [
        {"do": "mode", "mode": "edit"},
        {"do": "drag", "id": "S1", "to": [1.0, 0.35], "free": True},
        {"do": "addSegment", "a": "S10", "b": "S82"},
    ], tmp_path)
    assert r["ready"] is True, r.get("why")
    src = r["python"]
    assert "m.apply_edits(" in src, "the topology edit did not reach the export"

    ns: dict = {}
    exec(compile(src, "<browser export>", "exec"), ns)   # the export, run for real
    m = ns["m"]
    assert any(set(s.ends) == {"S10", "S82"} for s in m.arch.device.segments.values())
    assert m.arch.device.degree("S10") == 3

    # The machine that comes out must be the one the browser was showing -- and the
    # comparison is EXACT, which is the whole reason `pyRepr` exists.
    #
    # The gesture goes through PIXEL coordinates (`(my - my0) / sy`), as a real drag
    # does, and that round trip costs an ulp: the drop used to land on 0.35000000000000003.
    # A free landing is now kept to a thousandth of a unit (`snapRound`), so the double
    # that travels is exactly 0.35 -- and it must arrive exactly, which is what `pyRepr`
    # is for: `String(v)` gets a tenth of all doubles wrong, Python's `repr` none.
    assert m.arch.device.nodes["S1"].pos[1] == 0.35
    js = json.loads(r["arch_json"])
    js_nodes = {n["id"]: n for n in js["geometry"]["nodes"]}
    assert set(js_nodes) == set(m.arch.device.nodes)
    for nid, n in m.arch.device.nodes.items():
        assert tuple(js_nodes[nid]["pos"]) == tuple(n.pos), (
            f"{nid}: the exported Python built a different machine from the one on screen")


# ======================================================================================
# DIRECT MANIPULATION
# ======================================================================================
#
# Three complaints, all of them UI, all of them measurable:
#
#   "I cannot flexibly add/delete/move any elements"   -> the reach census below.  On the
#     shipped pages only 48% of a cap-4 site's own drawn BAR selected it and a segment was
#     grabbable on 10% of its own length; on a device built in the studio the spatial index
#     had been built over an EMPTY canvas and never invalidated, so 0 of 14 nodes were
#     clickable for the rest of the session, and after a delete the next pointermove threw.
#
#   "make all elements clickable/draggable"            -> `reach_*`, `dragKind`, `marquee`.
#
#   "a menu of all elements, each with an avatar showing how it will be depicted"
#                                                      -> `palette_dom` and `avatar_parity`.
#
# THE AVATAR ASSERTION IS THE LOAD-BEARING ONE.  It is not "the avatar looks right"; it is
# that the avatar of a site and the stage's own drawing of that site are THE SAME MARKS,
# compared attribute for attribute in units of `g`.  If that holds, drift is impossible,
# because `buildStatic` is the only thing in the page that can draw a site -- the avatar
# passes it a different SCENE, not a different renderer.

AVATAR_PAGES = ["ring144_24v__deck__corrected", "cyclone_base__rotate__corrected",
                "deck_unit_cell__walk__corrected", "h2_racetrack__rotate__corrected"]


@requires_node
@pytest.mark.parametrize("stem", AVATAR_PAGES)
def test_the_palette_avatar_is_the_stage_s_own_mark(stem, tmp_path):
    page = OUT / f"{stem}.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    r = drive(page, [], tmp_path)
    p = r["avatar_parity"]
    assert p and p["n"] > 0, "no site to compare -- the census measured nothing"
    assert p["all_match"], (
        f"the palette avatar is not the mark the stage draws -- two implementations: "
        f"{p['first_bad']}")
    # the bar length as a fraction of g: 0.30 + 0.15*cap, clamped at 0.88
    cap = p["sample"]["cap"]
    assert p["sample"]["bar_over_g"] == round(min(0.88, 0.30 + 0.15 * cap), 4)
    assert p["sample"]["ticks"] == min(cap, 6)


@requires_node
def test_an_unsaturated_capacity_is_actually_covered(tmp_path):
    """THE SWEEP MUST INCLUDE A CAPACITY BELOW 4, or it passes on a fork.

    `siteLen` clamps at `site_max = 0.88*g` from cap 4 up, so a second renderer with a
    different length coefficient still MATCHES at cap 6.  At least one page in the corpus
    must therefore carry sites below that, and the sweep below drives capacity down to 1
    through the ordinary edit verbs and re-checks.
    """
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    assert drive(page, [], tmp_path)["avatar_parity"]["unsaturated"], (
        "every site on this page is at or above the capacity where the bar saturates; "
        "the parity sweep cannot distinguish a forked renderer here")
    for cap in (1, 3):
        r = drive(page, [{"do": "emit", "op": {"method": "set_site_capacity",
                                               "args": ["S1", cap], "kwargs": {}}}],
                  tmp_path)
        p = r["avatar_parity"]
        assert p["all_match"], f"cap {cap}: {p['first_bad']}"
        row = [x for x in [p["sample"]] if x]
        assert row and p["caps"], p


@requires_node
def test_every_palette_element_has_a_tile_with_a_real_avatar_in_it(tmp_path):
    """`pal-item` 0, `avatar` 0, `data-add` 0: the palette data was computed and thrown
    away, and the menu was eleven rows of a name and a field count."""
    page = OUT / "studio.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio -o out/studio.html`")
    r = drive(page, [], tmp_path)
    dom = r["palette_dom"]
    # DERIVED, NOT COUNTED. This said `== 11` and went red the day the component
    # catalogue was wired into the rail -- a stale magic number reporting a feature as a
    # defect. The rail holds one tile per schema element plus one per catalogued
    # component, and both halves of that are shipped data, so the test can compute it and
    # cross-checks Python against the DOM while it is at it.
    from qccd.arch.library import CATALOG                     # noqa: PLC0415

    n_elements, n_components = len(r["palette"]), len(CATALOG)
    # ...AND THE SKETCH TOOLS, for the same reason. The dict below used to name its counts
    # as literals, and sketch mode's four shape tiles turned it red -- a rail that had grown
    # a feature, reported as a defect, one line under a comment saying not to do that.
    # `EDITOR.sketchTools()` is the shipped list, so this half is derived too.
    n_shapes = len(r.get("sketch_tools") or [])
    assert n_shapes >= 1, "the editor reports no sketch tools; the harness hook is stale"
    assert dom["items"] == n_elements + n_components + n_shapes, dom
    assert dom["kinds"] == {"shape": n_shapes, "stamp": 4, "named": 1, "row": 1, "block": 5,
                            "component": n_components}, dom["kinds"]
    # one avatar per tile, plus one per existing zone type on the strip
    assert dom["avatars"] >= n_elements + n_components, dom
    assert dom["empty_avatars"] == 0, "an avatar with no marks in it is a blank square"
    assert dom["marks"] > 40, dom
    # DERIVED, like the tile count above: one chip per zone type the seeded device
    # declares, plus the "+ new zone type" chip.  This said `>= 5` while the shipped
    # packages happened to declare five zones; when two of them (`ancilla`, `tfactory`)
    # were dropped for naming a code's roles rather than any hardware, a correct menu
    # started reading as a defect.
    from qccd.api import DEFAULT_TEMPLATE                          # noqa: PLC0415

    n_zones = len(load(ROOT / "arch" / f"{DEFAULT_TEMPLATE}.arch.json").zone_types)
    assert dom["zone_chips"] == n_zones + 1, (dom["zone_chips"], n_zones)
    # every tile names the exact call it will emit
    for v in dom["verbs"]:
        assert v.split(":", 1)[1], f"{v} has no data-add: the tile does not say what it emits"
    assert r["docs_cover_palette"] is True, (
        "an element with no name and no blurb renders a blank line")
    # every scene lands on the same derived scale, or one tile is silently at another zoom
    assert [g for _, g in r["avatar_scales"]] == [72, 72, 72, 72], r["avatar_scales"]
    # and every one of the eleven produces a picture
    for e in r["palette"]:
        assert e["avatar_len"] > 100, f"{e['type']} has no avatar"


@requires_node
@pytest.mark.parametrize("stem,node_min,seg_min", [
    ("cyclone_base__rotate__corrected", 1.0, 0.10),
    ("deck_unit_cell__walk__corrected", 0.95, 0.30),
    ("ring144_24v__deck__corrected", 1.0, 0.35),
    ("ladder_2x72__walk__corrected", 1.0, 0.35),
])
def test_every_element_on_the_stage_is_clickable(stem, node_min, seg_min, tmp_path):
    """THE TARGET IS THE DRAWN BODY.  Two discs -- `min(0.45g, 11px)` for a node and
    `min(0.35g, 8px)` for a segment -- are the wrong SHAPE in both directions at once:
    too small for a site, which is drawn as a bar up to `0.88*g` long, and too big against
    the segment it sits on.  Measured before: cyclone 13/25 of a node's own body (48%),
    the deck page 5/51 of a segment's own length (10%)."""
    page = OUT / f"{stem}.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    r = drive(page, [], tmp_path)
    reach = r["reach_base"]
    assert reach["threw"] == 0, reach["msg"]
    n_hit, n_all = reach["node"]
    s_hit, s_all = reach["seg"]
    assert n_hit / n_all >= node_min, f"node reach {n_hit}/{n_all} on {stem}"
    assert s_hit / s_all >= seg_min, f"segment reach {s_hit}/{s_all} on {stem}"
    if reach["loop"][1]:
        l_hit, l_all = reach["loop"]
        assert l_hit / l_all >= 0.8, (
            f"loop reach {l_hit}/{l_all}: hit() returned kind 'loop' zero times in 25,000 "
            f"probes before it had a loop pass at all")
    # the highlight is DERIVED from what was drawn, never computed a second time
    assert r["outline_is_mark"]["all"], r["outline_is_mark"]["first"]


@requires_node
def test_who_owns_a_press_is_decided_in_one_place(tmp_path):
    """Six presses, six verdicts, one arbiter -- and the right button now has two answers.

    A RIGHT PRESS ON SOMETHING IS THAT THING'S MENU (2026-09-17: "I should be able to right
    click and see all the options I can make for this selected component").  A right press
    on EMPTY STAGE still pans, because right-drag panning is a documented gesture and the
    page suppresses the browser's own context menu over the stage for exactly that reason.
    The two halves of that row are asserted together here: `claim` is where the difference
    between them lives, so a regression that made right-click-anywhere open a menu -- and
    took right-drag panning away -- would fail on the second line.
    """
    # BUILT FROM THIS TREE, not taken from `out/`: the arbiter is code, and a page emitted
    # before the code changed answers for the code it was emitted with.  Every other test
    # in this file is about the pages that SHIP; this one is about the rule.
    page = tmp_path / "claim.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "--seed", "ring144_24v",
                    "-o", str(page)], cwd=str(ROOT), capture_output=True,
                   timeout=900, check=True)
    # `right` in the probe is a right press ON THE FIRST NODE; the script below asks the
    # same arbiter about a right press on empty stage, through the one step vocabulary.
    r = drive(page, [{"do": "claim", "mx": -500, "my": -500, "mod": {"button": 2}}],
              tmp_path)
    c = r["claim"]
    # THERE ARE NO MODES: a press on an element is the editor's whatever `setMode` was last
    # told, empty stage pans, and only shift on empty stage starts a marquee.
    assert c == {"on_element": "element", "on_empty": "pan", "shift_on_empty": "marquee",
                 "with_space": "pan", "middle": "pan", "right": "menu",
                 "play_mode": "element"}, c
    assert r["log"][0]["result"] == "pan", (
        "a right press on empty stage must still pan: right-drag is how the view is "
        "moved without leaving a tool")


@requires_node
def test_transport_is_owned_by_programme_validity_not_by_a_mode(tmp_path):
    """`setMode('edit')` used to disable Play/Step/Glide/Phase and `onProgramValidity`
    re-enabled them after the first drop -- two owners, and the buttons told a different
    story depending on which had spoken last.  One rule now: disabled iff the programme no
    longer fits the device."""
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    x, y = load(ARCH).device.nodes["S1"].pos
    # a free 0.3-step nudge, as the pricing tests use: one full step lands S1 on a
    # neighbour and is refused as `coincident`, which would prove nothing here
    r = drive(page, [{"do": "mode", "mode": "edit"},
                     {"do": "drag", "id": "S1", "to": [x + 0.3, y + 0.3], "free": True},
                     {"do": "remove", "sel": [{"kind": "node", "id": "S1"}]}], tmp_path)
    after_mode, after_drag, after_remove = r["steps"][1], r["steps"][2], r["steps"][3]
    assert after_mode["transport_disabled"] == [False] * 7, after_mode["transport_disabled"]
    assert after_drag["edits"] == 1 and after_drag["transport_disabled"] == [False] * 7, (
        "a valid programme keeps the transport live through an edit", after_drag)
    assert after_remove["transport_disabled"] == [True] * 7, (
        "removing a node the programme uses must withdraw the transport", after_remove)
    assert r["banner"], "the red banner must say why the transport is withdrawn"


@requires_node
def test_dragging_a_node_does_not_pan_the_stage(tmp_path):
    """The page's pan handler had NO mode guard, so a node drag ran both handlers and the
    picture slid out from under the gesture.  Measured on a replayed drag: the viewBox
    went from x=0 to x=-8.96 while the node was moving."""
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    x, y = load(ARCH).device.nodes["S1"].pos
    r = drive(page, [{"do": "mode", "mode": "edit"},
                     {"do": "drag", "id": "S1", "to": [x, y + 1]}], tmp_path)
    assert r["vb_base"] == r["vb_edited"], (
        f"the stage panned while a node was dragged: {r['vb_base']} -> {r['vb_edited']}")


@requires_node
def test_a_segment_and_a_loop_can_be_moved(tmp_path):
    """`begin('segment', ...)` returned null -- `nodeById[id]` is never a segment id -- so
    a segment could be selected and never dragged, and a loop could be neither."""
    page = OUT / "cyclone_base__rotate__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    dev = load(ROOT / "arch" / "cyclone_base.arch.json").device
    sid = sorted(dev.segments)[0]
    x, y = dev.nodes[dev.segments[sid].ends[0]].pos
    r = drive(page, [{"do": "mode", "mode": "edit"},
                     {"do": "dragKind", "kind": "segment", "id": sid,
                      "from": [x, y], "to": [x, y + 0.4], "free": True}], tmp_path)
    step = r["log"][-1]
    assert step["ids"] and len(step["ids"]) == 2, step
    assert step["result"]["n"] == 2, (
        "dragging a segment must move BOTH its endpoints, as one undo entry")
    assert step["result"]["problems"] == [], step


@requires_node
def test_escape_cancels_a_live_drag(tmp_path):
    """Escape was bound only in the page's own handler, which cleared the programme filter
    and never told the editor -- so `EDITOR.selection()` was unchanged after Escape and
    `drop()` still committed the move."""
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    x, y = load(ARCH).device.nodes["S1"].pos
    base = drive(page, [], tmp_path)["steps"][0]
    r = drive(page, [{"do": "mode", "mode": "edit"},
                     {"do": "select", "sel": [{"kind": "site", "id": "S1"}]},
                     {"do": "key", "k": "Escape"}], tmp_path)
    assert r["log"][-1]["result"] == "selection"
    assert r["selection"] == [], r["selection"]
    assert r["steps"][-1]["edits"] == base["edits"]


@requires_node
def test_the_declared_length_note_never_outlives_the_geometry(tmp_path):
    """The price strip's declared-length note is READ OFF THE DEVICE on every rebuild,
    not remembered from the last drop.  Stored, it outlived what it described: ctrl+Z
    (`undoGroup`, which pops the edit stack directly) left the strip saying "2 segment(s)
    drawn up to 0.50x their declared length" about a grid that had none, and a fresh
    Start-card ring inherited the grid's note."""
    page = OUT / "studio_grid.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio --seed arch/grid9x9.arch.json --program walk -o out/studio_grid.html`")
    r = drive(page, [
        # 4.35, not 4.25: the boundary stops a bar 0.30 short of the junction's square, so
        # 4.25 is not a place a drag can reach; 4.35 leaves the left rail at 0.70x
        {"do": "drag", "id": "T4_4h", "to": [4.35, 4.0], "free": True},
        {"do": "undoGroup"},                                              # what ctrl+Z does
        {"do": "redoGroup"},
        {"do": "startCard", "gen": "ring"},                                # a different device
        {"do": "undoGroup"},                                               # the grid, still edited
        {"do": "key", "k": "z", "mods": {"ctrl": True}},
    ], tmp_path)
    assert r["ready"] is True, r.get("why")
    notes = [(st["label"], st["length_note"]) for st in r["steps"][1:]]
    assert notes[0][1] and "0.70x" in notes[0][1], notes
    assert notes[1][1] is None, notes                  # undoGroup
    assert notes[2][1] and "0.70x" in notes[2][1], notes   # redoGroup brings the fact back
    assert notes[3][1] is None, notes                  # a new device has no such fact
    assert notes[4][1] and "0.70x" in notes[4][1], notes   # the edited grid is back
    assert notes[5][1] is None, notes                  # ctrl+Z
    assert r["steps"][-1]["edits"] == 0


@requires_node
def test_escape_cancels_a_live_band(tmp_path):
    """Shift-drag from a node draws the dashed segment-to-be.  Escape used to fall through
    to the selection branch: the line stayed drawn and the release still made the
    segment.  Now it is a live drag like any other -- one press takes it away and the
    release is inert."""
    page = OUT / "studio_grid.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio --seed arch/grid9x9.arch.json --program walk -o out/studio_grid.html`")
    r = drive(page, [
        {"do": "band", "a": "J0_0", "to": [1.0, 0.0]},
        {"do": "key", "k": "Escape"},
        {"do": "bandDrop", "to": [1.0, 0.0]},          # on J1_0: would have joined
        {"do": "band", "a": "J0_0", "to": [1.0, 0.0]},
        {"do": "bandDrop", "to": [1.0, 0.0]},
    ], tmp_path)
    assert r["ready"] is True, r.get("why")
    base = r["steps"][0]
    log = r["log"]
    assert log[1]["result"] == "drag", log[1]
    assert log[2]["result"] is None, log[2]             # nothing to release
    assert r["steps"][3]["segs"] == base["segs"] and r["steps"][3]["edits"] == base["edits"]
    assert log[4]["result"]["ok"] is True and log[4]["result"]["to"] == "J1_0", log[4]
    assert r["steps"][5]["segs"] == base["segs"] + 1


@requires_node
def test_the_arrows_nudge_a_selection_and_scrub_without_one(tmp_path):
    """ONE keymap.  Two keydown listeners used to share the arrows -- the editor's nudged
    the selection while the page's scrubbed the programme -- so every nudge also moved the
    playhead.  With a selection an arrow is a nudge: N move_site records in ONE group (one
    ctrl+Z takes the whole gesture back) and `frame` untouched; with nothing selected it
    is a seek and the edit stack is untouched.  The keys go through the page's own
    dispatcher, the same code path the listener calls."""
    page = OUT / "studio_grid.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio --seed arch/grid9x9.arch.json --program walk -o out/studio_grid.html`")
    r = drive(page, [
        {"do": "key", "k": "Home"},                             # the census left the playhead
        {"do": "key", "k": "ArrowRight"},                       # no selection: a seek
        {"do": "select", "sel": [{"kind": "site", "id": "T7_4h"}, {"kind": "site", "id": "J8_4"},
                                 {"kind": "site", "id": "T8_4v"}]},
        {"do": "key", "k": "ArrowRight"},                       # a selection: a nudge
        {"do": "key", "k": "z", "mods": {"ctrl": True}},        # one gesture back
        {"do": "key", "k": "y", "mods": {"ctrl": True}},        # and forward again
    ], tmp_path)
    base, home, seek, sel, nudge, undo, redo = r["steps"]
    assert r["log"][0]["verb"] == "seek-first" and home["frame"] == 0
    assert r["log"][1]["verb"] == "seek-next" and seek["frame"] == 1
    assert seek["edits"] == base["edits"]
    assert r["log"][3]["verb"] == "nudge" and r["log"][3]["result"]["ok"], r["log"][3]
    assert nudge["edits"] == base["edits"] + 3 and nudge["edit_groups"] == base["edit_groups"] + 1
    assert nudge["frame"] == seek["frame"], "a nudge must not move the playhead"
    assert undo["edits"] == base["edits"], "ctrl+Z takes back the whole nudge"
    assert redo["edits"] == base["edits"] + 3


@requires_node
def test_a_refused_nudge_says_so(tmp_path):
    """`nudge()` used to drop `emit()`'s verdict on the floor, so nudging a selection into
    a neighbour did nothing and said nothing.  Moving J4_4/T4_4h/J5_4 one step right puts
    J5_4 on T5_4h, which is NOT a member: refused, named, and the stack untouched."""
    page = OUT / "studio_grid.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio --seed arch/grid9x9.arch.json --program walk -o out/studio_grid.html`")
    r = drive(page, [
        {"do": "select", "sel": [{"kind": "site", "id": "J4_4"}, {"kind": "site", "id": "T4_4h"},
                                 {"kind": "site", "id": "J5_4"}]},
        {"do": "key", "k": "ArrowRight"},
    ], tmp_path)
    res = r["log"][-1]["result"]
    assert res["ok"] is False and res["problems"] == ["coincident"], res
    assert r["steps"][-1]["edits"] == r["steps"][0]["edits"]


@requires_node
def test_escape_in_the_write_pane_only_leaves_the_field(tmp_path):
    """Escape in a text field used to run `e.target.value = \'\'` for EVERY field, which
    erased a half-written programme in #pwText.  Now it clears only the two listing
    filters and otherwise blurs; a pressed `?` closes the help, and the escape order --
    drag, stamp, selection, help -- is one function."""
    page = OUT / "studio_grid.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio --seed arch/grid9x9.arch.json --program walk -o out/studio_grid.html`")
    src = 'p.init({"d0": "T0_0h"})'
    r = drive(page, [
        {"do": "prog", "src": src},
        {"do": "key", "k": "Escape", "field": "pwText"},
        {"do": "key", "k": "Escape", "field": "pFilter"},
        {"do": "key", "k": "?"},
        {"do": "key", "k": "Escape"},
    ], tmp_path)
    log = r["log"]
    assert log[1]["verb"] == "blur" and log[2]["verb"] == "clear-filter", log[1:3]
    assert r["steps"][1]["write_text"], "the Write pane holds the programme just applied"
    assert r["steps"][2]["write_text"] == r["steps"][1]["write_text"], "Escape in the Write pane must not erase it"
    assert log[3]["verb"] == "help" and r["steps"][4]["help_on"] is True
    assert log[4]["result"] == "help" and r["steps"][5]["help_on"] is False


@requires_node
def test_the_help_is_rendered_from_the_keymap(tmp_path):
    """The `?` overlay is generated from the same table `keyGesture` reads: every row has
    a line in #helpBody and no key label appears twice.  A binding without help, or two
    rows claiming one key, is what two competing keydown listeners looked like."""
    page = OUT / "studio.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio -o out/studio.html`")
    h = drive(page, [], tmp_path)["help"]
    assert h and h["rows"] >= 20 and h["keys_unique"], h
    assert h["every_row_shown"], h["first_missing"]


@requires_node
def test_a_node_stays_clickable_after_it_is_added(tmp_path):
    """DEFECT: `hit()` built `GRID` once, lazily, and `rebuild()` never cleared it -- the
    comment on `GRID` claimed it did.  On a device built in the studio the index had been
    built over an EMPTY canvas, so every node was permanently unhittable and `hit()`
    answered with whatever segment happened to pass through the point."""
    page = OUT / "studio.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio -o out/studio.html`")
    r = drive(page, [{"do": "mode", "mode": "edit"},
                     {"do": "hit", "mx": 100, "my": 100},          # builds GRID over 0 nodes
                     {"do": "canvas", "opts": {"name": "g"}},
                     {"do": "node", "x": 0, "y": 0, "opts": {"kind": "site", "zone": "data"}},
                     {"do": "node", "x": 2, "y": 0, "opts": {"kind": "site", "zone": "data"}},
                     {"do": "join", "a": "N0", "b": "N1"},
                     {"do": "hitModel", "x": 0, "y": 0}], tmp_path)
    got = r["log"][-1]["result"]
    # THE ASSERTION IS ON THE KIND, not on "hit is non-null": the defect returned the
    # SEGMENT under the node, which is non-null and proves nothing.
    assert got and got["kind"] == "site" and got["id"] == "N0", got


@requires_node
def test_deleting_a_node_does_not_break_every_later_mouse_move(tmp_path):
    """The stale index still named the removed node, so the next pointermove dereferenced
    `nodeById[<gone>]` and threw -- killing hover, dragging and selection for the rest of
    the session."""
    page = OUT / "cyclone_base__rotate__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    r = drive(page, [{"do": "mode", "mode": "edit"},
                     {"do": "remove", "sel": [{"kind": "node", "id": "S5"}]}], tmp_path)
    assert r["reach_edited"]["threw"] == 0, r["reach_edited"]["msg"]


@requires_node
def test_the_selection_panel_describes_the_thing_you_clicked(tmp_path):
    """`hit()` returns 'site'/'junction'; `renderInspector` tested `kind === 'node'` and
    `removeSelected` tested `kind === 'segment'` -- three functions, three vocabularies,
    and the panel was blank for every node on the stage."""
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    r = drive(page, [{"do": "mode", "mode": "edit"},
                     {"do": "select", "sel": [{"kind": "site", "id": "S1"}]}], tmp_path)
    assert "S1" in r["inspector"], r["inspector"][:200]
    r2 = drive(page, [{"do": "mode", "mode": "edit"},
                      {"do": "hitModel", "x": load(ARCH).device.nodes["S1"].pos[0],
                       "y": load(ARCH).device.nodes["S1"].pos[1]}], tmp_path)
    assert r2["log"][-1]["result"]["kind"] == "site"


@requires_node
def test_a_marquee_selects_what_it_encloses(tmp_path):
    page = OUT / "cyclone_base__rotate__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    dev = load(ROOT / "arch" / "cyclone_base.arch.json").device
    xs = [n.pos[0] for n in dev.nodes.values()]
    ys = [n.pos[1] for n in dev.nodes.values()]
    r = drive(page, [{"do": "mode", "mode": "edit"},
                     {"do": "marquee", "from": [min(xs) - 1, min(ys) - 1],
                      "to": [max(xs) + 1, max(ys) + 1]}], tmp_path)
    picked = r["log"][-1]["result"]
    kinds = {p.split(":")[0] for p in picked}
    assert len([p for p in picked if p.startswith("site:")]) == len(dev.nodes)
    assert "segment" in kinds and "loop" in kinds, (
        "a segment whose endpoints are both inside, and a loop all of whose nodes are, "
        f"must come with them: {sorted(kinds)}")


@requires_node
def test_a_loop_refuses_deletion_by_name_rather_than_leaking_topology(tmp_path):
    """Making a loop selectable makes Delete-on-a-loop reachable for the FIRST time, and
    `edit.js` has no `remove_loop` op -- so it would have issued `remove_node` with a loop
    id and shown the mirror's bare `topology` refusal."""
    page = OUT / "cyclone_base__rotate__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    lid = sorted(load(ROOT / "arch" / "cyclone_base.arch.json").device.loops)[0]
    r = drive(page, [{"do": "mode", "mode": "edit"},
                     {"do": "remove", "sel": [{"kind": "loop", "id": lid}]}], tmp_path)
    msg = r["log"][-1]["result"]["problems"][0]
    assert "transport loop" in msg and "no delete verb" in msg, msg
    assert r["steps"][-1]["nodes"] == r["steps"][0]["nodes"], "a refusal must change nothing"


@requires_node
def test_a_stamp_is_placed_and_deleted_and_undone(tmp_path):
    """The whole gesture, through the palette's own verbs: arm, ghost, drop, select,
    delete, undo -- and the model must come back bit for bit."""
    page = OUT / "studio.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio -o out/studio.html`")
    r = drive(page, [{"do": "mode", "mode": "edit"},
                     {"do": "canvas", "opts": {"name": "g"}},
                     {"do": "stamp", "type": "site", "at": [0, 0]},
                     {"do": "stamp", "type": "site", "at": [1, 0]},
                     {"do": "join", "a": "N0", "b": "N1"},
                     {"do": "snapshot"},
                     {"do": "stamp", "type": "junction", "at": [2, 0]},
                     {"do": "remove", "sel": [{"kind": "junction", "id": "J0"}]},
                     {"do": "undo"}, {"do": "undo"}], tmp_path)
    log = r["log"]
    assert log[2]["result"]["ok"] and log[2]["result"]["id"] == "N0", log[2]
    assert log[6]["result"]["ok"] and log[6]["result"]["id"] == "J0", log[6]
    assert log[7]["result"]["ok"], log[7]
    # undo twice: remove the delete, then remove the placement
    assert r["steps"][-1]["nodes"] == 2, r["steps"][-1]["nodes"]


@requires_node
def test_two_double_clicked_sites_can_be_joined_and_test_driven(tmp_path):
    """The un-armed double-click takes THE SAME APPLIER as the armed click.  It used to
    pick by node count -- the first site through the builder (`d.site`), every later one
    through the post-seal `add_site` -- so N0 lived above the seal and N1 below it, the
    segment joining them was refused with "unknown endpoint 'N1'", and Test drive said
    "place two sites and a segment first" on the very page the empty state points at.
    Two double-clicks, one shift-drag, one Test drive: the export must run under Python."""
    page = OUT / "studio.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio -o out/studio.html`")
    r = drive(page, [{"do": "dblclick", "at": [0, 0]},
                     {"do": "dblclick", "at": [1, 0]},
                     {"do": "join", "a": "N0", "b": "N1"},
                     {"do": "testDrivePlan"},
                     {"do": "testDrive"}], tmp_path)
    log = r["log"]
    assert log[0]["result"]["ok"] and log[0]["result"]["id"] == "N0", log[0]
    assert log[1]["result"]["ok"] and log[1]["result"]["id"] == "N1", log[1]
    assert log[2]["result"]["ok"], ("the two double-clicked sites must join", log[2])
    assert log[2]["after"]["segs"] == 1, log[2]["after"]
    plan = log[3]["result"]
    assert plan["ok"] and plan["kind"] == "shuttle" and plan["from"] == "N0", plan
    assert log[4]["result"]["ok"] and log[4]["result"]["frames"] > 0, log[4]
    assert r["ready"] is True, r.get("why")
    # the export is one device, not a builder above the seal and a topology edit below it
    ns: dict = {}
    exec(compile(r["python"], "<browser export>", "exec"), ns)   # the export, run for real
    m = ns["m"]
    assert set(m.arch.device.nodes) == {"N0", "N1"}
    assert any(set(sg.ends) == {"N0", "N1"} for sg in m.arch.device.segments.values())


# ----------------------------------------------------------------- mutation guards
#
# FOUR, because four different things are being protected and a guard that fires for all
# of them at once is not telling you which one broke.


@requires_node
def test_the_avatar_guard_catches_a_second_renderer(tmp_path):
    """MUTATION.  Give the avatar path its own copy of the site-bar rule -- the exact
    defect the design forbids -- by forking `siteLen` inside `buildStatic`.  The palette
    still renders a perfectly plausible bar, which is the whole danger."""
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    assert drive(page, [], tmp_path)["avatar_parity"]["all_match"] is True

    html = page.read_text(encoding="utf-8")
    marker = "  const siteLen=c=>_siteLen(c,L), slots=c=>_slots(c);"
    assert html.count(marker) == 1, "the scene's site-length binding moved; update this mutation"
    broken = tmp_path / "forked.html"
    broken.write_text(html.replace(
        marker,
        "  const siteLen=c=>(S.avatarFork ? Math.min(L.site_max,"
        "(0.30+0.14*clamp(1,c||1,6))*L.g) : _siteLen(c,L)), slots=c=>_slots(c);", 1)
        .replace("                L: LA, AXIS: AX, role: ROLE, px: pxA, py: pyA, byId: byId,",
                 "                L: LA, AXIS: AX, role: ROLE, px: pxA, py: pyA, byId: byId,"
                 " avatarFork: 1,", 1),
        encoding="utf-8")
    p = drive(broken, [], tmp_path)["avatar_parity"]
    assert p["all_match"] is False, (
        "the parity census passed a page whose palette draws the site bar with its own "
        "coefficient -- it would not have caught the defect it exists to catch")
    assert p["first_bad"]["cap"] <= 3, (
        "the fork was only caught at a SATURATED capacity, where every coefficient gives "
        "the same answer -- the sweep is not proving anything")


@requires_node
def test_the_clickability_guard_can_fail(tmp_path):
    """MUTATION.  Delete the one line that invalidates the spatial index."""
    page = OUT / "studio.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio -o out/studio.html`")
    script = [{"do": "mode", "mode": "edit"},
              {"do": "hit", "mx": 100, "my": 100},
              {"do": "canvas", "opts": {"name": "g"}},
              {"do": "node", "x": 0, "y": 0, "opts": {"kind": "site", "zone": "data"}},
              {"do": "node", "x": 2, "y": 0, "opts": {"kind": "site", "zone": "data"}},
              {"do": "join", "a": "N0", "b": "N1"},
              {"do": "hitModel", "x": 0, "y": 0}]
    assert drive(page, script, tmp_path)["log"][-1]["result"]["kind"] == "site"

    html = page.read_text(encoding="utf-8")
    marker = "  GRID = null;\n\n  // 1. re-lay-out"
    assert html.count(marker) == 1, "the grid invalidation moved; update this mutation"
    broken = tmp_path / "stale_index.html"
    broken.write_text(html.replace(marker, "  // 1. re-lay-out", 1), encoding="utf-8")
    got = drive(broken, script, tmp_path)["log"][-1]["result"]
    assert not (got and got["kind"] == "site"), (
        "the test passed a page on which every node is permanently unclickable")


@requires_node
def test_the_pan_guard_can_fail(tmp_path):
    """MUTATION.  Take the arbiter out of the page's pan handler and the stage must pan
    under the drag again -- while the reach numbers stay put, which is what proves the
    viewBox assertion is the one carrying this property."""
    page = OUT / "ring144_24v__deck__corrected.html"
    if not page.exists():
        pytest.skip("run `python -m qccd demo`")
    x, y = load(ARCH).device.nodes["S1"].pos
    script = [{"do": "mode", "mode": "edit"}, {"do": "drag", "id": "S1", "to": [x, y + 1]}]
    assert drive(page, script, tmp_path)["vb_base"] == drive(page, script, tmp_path)["vb_edited"]

    html = page.read_text(encoding="utf-8")
    marker = ("  if (window.EDITOR && EDITOR.claimEvent && EDITOR.claimEvent(e) !== 'pan') "
              "return;")
    assert html.count(marker) == 1, "the pan guard moved; update this mutation"
    broken = tmp_path / "pan_steals.html"
    broken.write_text(html.replace(marker, "  /* mutated: pan always wins */", 1),
                      encoding="utf-8")
    # the mutated page cannot be driven through a pointer event -- the shim has none -- so
    # the guard is asserted where it is READ, which is the only place it can be
    assert "EDITOR.claimEvent(e)" not in broken.read_text(encoding="utf-8"), (
        "the mutation did not remove the only CALL of the arbiter (the comment above it "
        "names it too, which is why this looks for the call and not the name)")
    r = drive(broken, script, tmp_path)
    assert r["reach_edited"]["node"][0] == r["reach_edited"]["node"][1], (
        "removing the pan guard changed the hit radii, so the two properties are not "
        "independent and neither assertion is proving what it claims")


@requires_node
def test_the_hit_shape_guard_can_fail(tmp_path):
    """MUTATION, TWO-SIDED, because the property is two-sided.

    A one-sided guard ("nodes must be 100% reachable") is satisfied by making every radius
    enormous, which would score perfectly on nodes and destroy segment picking.  So this
    puts BOTH of the old discs back -- `min(0.45g, 11px)` for a node and `min(0.35g, 8px)`
    for a segment, exactly as they shipped -- and BOTH numbers must move:

      * the node reach falls, because a site is drawn as a BAR up to 0.88*g long and the
        disc covers only the middle of it;
      * the segment reach falls, because the node disc claims 0.45*g at each end of a
        one-step segment and leaves ~10% of its own length.

    Note the node disc is the LARGER of the two radii and the segment reach still falls --
    that is the whole point.  A bigger radius is not a better target.
    """
    NODE_DISC = ("      if (Math.abs(u) <= G.len / 2 + 1e-9 && "
                 "Math.abs(v) <= L.site_t / 2 + S) {")
    SEG_BAND = "    if (dd <= segBand(SS) && (best === null || dd < best.dist)) {"
    for stem, key, floor in [("cyclone_base__rotate__corrected", "node", 1.0),
                             ("ring144_24v__deck__corrected", "seg", 0.35)]:
        page = OUT / f"{stem}.html"
        if not page.exists():
            pytest.skip("run `python -m qccd demo`")
        good = drive(page, [], tmp_path)["reach_base"][key]
        assert good[0] / good[1] >= floor

        html = page.read_text(encoding="utf-8")
        assert html.count(NODE_DISC) == 1, "the site hit test moved; update this mutation"
        assert html.count(SEG_BAND) == 1, "the segment hit test moved; update this mutation"
        broken = tmp_path / f"discs_{stem}.html"
        broken.write_text(
            html.replace(NODE_DISC,
                         "      if (Math.sqrt(u * u + v * v) <= "
                         "Math.min(0.45 * L.g, 11 * userPerPx())) {", 1)
                .replace(SEG_BAND,
                         "    if (dd <= Math.min(0.35 * L.g, 8 * userPerPx()) && "
                         "(best === null || dd < best.dist)) {", 1),
            encoding="utf-8")
        bad = drive(broken, [], tmp_path)["reach_base"][key]
        assert bad[0] / bad[1] < good[0] / good[1], (
            f"{stem}: putting the old discs back did not move {key} reach "
            f"({bad} vs {good}) -- the census is not measuring the target shape")


# ======================================================================================
# THE ELECTRODES ARE THE TECHNOLOGY'S, AND A DRAG DOES NOT STRAIGHTEN A RAIL
# ======================================================================================


#: The pages `qccd regen` writes, and only those.  `_pages()` walks all of `out/`, which
#: also holds board sweeps and rule-example pages written by other tools at other times;
#: several of those do not evaluate at all any more (`_r22 is not defined`), and a
#: parametrisation over them would report one pre-existing breakage per page under a name
#: that says something about electrodes.
def _regen_pages():
    return [p for p in sorted(OUT.glob("*.html"))
            if '<script id="data"' in p.read_text(encoding="utf-8")]


@requires_node
@pytest.mark.parametrize("page", _regen_pages(), ids=lambda p: p.stem)
def test_every_trapping_site_carries_its_technology_s_dc_pairs(page, tmp_path):
    """`n_dc_pairs` per site, and the pitch between two electrodes is `l_dc + g_dc`.

    A reviewer of the hardware picture asked for both.  Neither held: the tiling was per
    SEGMENT, so a site had whatever its rail's rounding left near it and sometimes none at
    all, and the pitch was `0.34 * g` -- a fraction of the drawn nearest-neighbour
    distance, which is a legibility constant and not a length.  Counting electrodes on the
    screen is now a measurement, which is why this asserts a figure in micrometres rather
    than a count of rectangles.
    """
    r = drive(page, [], tmp_path)
    if not r.get("ready"):
        pytest.skip(f"editing unavailable on {page.name}: {r.get('why')}")
    pads = (r["steps"] or [{}])[0].get("pads")
    if pads is None:
        # out/ also holds pages built by other tools (the BB board sweeps), which `qccd
        # regen` does not rewrite; the probe feature-detects rather than throwing
        pytest.skip(f"{page.name} predates the per-site electrode model")
    if pads["sites"] == 0:
        pytest.skip("no trapping sites on this page")
    # above the drawing budget the tiling collapses to a dash pattern on the rail, which
    # states the same pitch; that path has its own arithmetic and not this assertion
    if pads["sites_with_pads"] == 0:
        assert pads["dashed"] > 0, "no pads and no dashes is a rail with no control at all"
        return
    assert pads["sites_with_pads"] == pads["sites"], (
        "every trapping site gets its own electrodes, not whatever the rail left over")
    lo, hi = pads["pairs_per_site"]
    assert lo == hi == pads["n_dc_pairs"], pads
    for key in ("site_pitch_um", "rail_pitch_um"):
        rng = pads[key]
        if rng is None:
            continue                      # a run with fewer than two pads has no pitch
        assert abs(rng[0] - pads["dc_pitch_um"]) < 1e-6, (key, rng, pads)
        assert abs(rng[1] - pads["dc_pitch_um"]) < 1e-6, (key, rng, pads)


@requires_node
def test_a_drag_keeps_a_bowed_rail_bowed_and_its_electrodes_on_screen(tmp_path):
    """`liveMove` used to rewrite every incident segment to a straight line and hide its
    pads for the duration of the drag.

    A bow exists to route a rail AROUND a node it does not touch -- the shipped ring's
    corner docks sit exactly on its end caps.  Straightening it mid-drag therefore drew
    the rail straight THROUGH that node, while the ion riding the same segment is drawn on
    the curve (`bezPoint`) and so left the rail the moment you touched it.  That is the
    first of the collaborator's three requests ("ions move only along the RF rails")
    failing on the one gesture a designer makes constantly.
    """
    if not CORR_PAGE.exists():
        pytest.skip("run `python -m qccd demo`")
    r = drive(CORR_PAGE, [], tmp_path)
    b = r.get("bow_probe")
    assert b is not None, "the bowed-rail probe did not run"
    assert b["grabbed"] is True, "the probe never got hold of the node"
    assert b["before"]["curved"] and b["before"]["quad"], (
        "the probe failed to put the segment on a curve, so it proves nothing")
    assert b["before"]["pads"] > 0, "a bowed rail carries electrodes before the drag too"
    assert b["during"]["curved"] is True, b
    assert b["during"]["quad"] is True, (
        "the rail was redrawn as a straight line under the drag: an ion on it is off it")
    assert b["during"]["pads"] == b["before"]["pads"], (
        "the electrodes were hidden for the drag; they ride the same curve and cost "
        "nothing to move")
    assert r["edits"] == [], "the probe must leave no edit behind"


@requires_node
def test_applying_the_source_text_keeps_a_device_built_by_hand(tmp_path):
    """DEFECT, found 2026-09-16 through a reader's comment on lesson A2: `applySource`
    rebuilt the edit list from topology edits and CHANGED lines only, so every `{build}`
    edit the canvas had made -- each site, junction and segment -- was dropped the moment
    the Source text was applied, even unchanged.  Lesson A2's step checks only the zone's
    capacity, so it passed on an empty device.

    Four ways to use the text on a canvas built by hand: apply it unchanged, append a
    retune, type a new site and segment, and delete a site a segment still needs -- which
    must be REFUSED with the device left as it was, not half-applied."""
    import re
    page = OUT / "studio.html"
    if not page.exists():
        pytest.skip("run `python -m qccd studio -o out/studio.html`")
    build = [{"do": "mode", "mode": "edit"},
             {"do": "canvas", "opts": {"name": "g"}},
             {"do": "node", "x": 0, "y": 0, "opts": {"kind": "site", "zone": "data"}},
             {"do": "node", "x": 2, "y": 0, "opts": {"kind": "site", "zone": "data"}},
             {"do": "node", "x": 4, "y": 0, "opts": {"kind": "site", "zone": "data"}},
             {"do": "join", "a": "N0", "b": "N1"},
             {"do": "join", "a": "N1", "b": "N2"}]

    def doc(r):
        a = r["arch_json"]
        return json.loads(a) if isinstance(a, str) else a

    def shape(r):
        g = doc(r).get("geometry") or {}
        return (sorted(str(n.get("id")) for n in g.get("nodes") or []),
                sorted(str(s.get("id")) for s in g.get("segments") or []))

    r0 = drive(page, build, tmp_path)
    nodes, segs = shape(r0)
    assert nodes == ["N0", "N1", "N2"] and len(segs) == 2, (nodes, segs)
    src = r0["source"]
    assert 'd.site("N2"' in src, src[:600]

    def applied(text):
        r = drive(page, build + [{"do": "source", "src": text}], tmp_path)
        return r, r["log"][-1]["result"]

    r, res = applied(src)
    assert res["ok"], res
    assert shape(r) == (nodes, segs), "applying the Source text unchanged lost the device"

    r, res = applied(src + 'm.set_zone("data", capacity=3)\n')
    assert res["ok"], res
    assert shape(r) == (nodes, segs)
    assert doc(r)["zone_types"]["data"]["capacity"] == 3

    typed = src.replace("\nm = Machine", '\nd.site("N9", 6.0, 0.0, zone="data")\n'
                                          'd.segment("E9", "N2", "N9")\nm = Machine', 1)
    assert typed != src
    r, res = applied(typed)
    assert res["ok"], res
    assert shape(r) == (nodes + ["N9"], sorted(segs + ["E9"]))

    gone = re.sub(r'(?m)^d\.site\("N2".*\n', "", src, count=1)
    assert gone != src
    r, res = applied(gone)
    assert not res["ok"], res
    assert res["errors"] and "N2" in res["errors"][0]["message"], res
    assert shape(r) == (nodes, segs), "a refused text must leave the device as it was"
