"""qccd.academy/gadgets/: the full stack is the section's theme (qccd/gadget/site.py).

* The landing is the full stack: its runs in one table, its layers, its machine; the places
  are a click away, on their own page, with every place the full-stack machines use.
* A run is shown as holding only when what it computes is the .prog's and every check holds.
* With the pinned FormalRV clone present, the smallest run is built through the section's own
  builder, page and all.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from qccd.gadget import site
from qccd.gadget.floorplan import FLOORPLANS_DIR, load_file
from qccd.gadget.library import LeafLibrary
from qccd.gadget.town import town

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "out" / "gadgets" / "cache"


@pytest.fixture(scope="module")
def leaves():
    return LeafLibrary(CACHE, log=None)


@pytest.fixture(scope="module")
def machines(leaves):
    return {n: load_file(FLOORPLANS_DIR / f"{n}.floor", leaves)[0] for n in ("fullstack", "fullstack_wide")}


def _run(**over) -> dict:
    res = {"linked": 62, "qec_statements": 62,
           "concrete": {"instructions": 27, "makespan_ms": 311.7, "ions": 171, "refused": 0},
           "contract": {"checked": True, "passed": True, "contract": "CX", "branches": 64},
           "checks": {"places": {"passed": 13, "failed": []},
                      "machine_rules": {"passed": 25, "failed": [], "total": 25}},
           "abstract": {"runtime_ns": 268_500_000}}
    res.update(over)
    return {**site.FULLSTACK[0], "result": res}


def test_the_landing_is_the_full_stack(leaves, machines):
    page = site._landing({"cnot": _run()}, machines, town(leaves), {}, {})
    assert page.startswith("<h1>The full stack</h1>")
    assert 'href="fullstack/cnot/"' in page and "exactly CX in all 64 branches" in page
    assert "268.5 ms on its abstract machine" in page
    for anchor in ("layers", "machine", "checked", "below"):
        assert f'id="{anchor}"' in page
    for href in ("places/", "algorithms/", "plan/"):
        assert f'href="{href}"' in page
    # the catalogue is not on the landing any more
    assert 'id="cat-' not in page


def test_a_run_holds_only_when_it_computes_the_prog_and_every_check_holds():
    assert site.stack_ok(_run()["result"])
    assert not site.stack_ok(_run(contract={"passed": False, "why": "a frame was dropped"})["result"])
    bad = _run()["result"]
    bad["checks"]["composition_problems"] = 1
    assert not site.stack_ok(bad)
    assert "not shown: a frame was dropped" in site._landing(
        {"cnot": _run(contract={"passed": False, "why": "a frame was dropped"})}, {}, None, {}, {})


def test_the_places_page_has_every_place_the_full_stack_uses(leaves, machines, tmp_path):
    page = site._places_page(town(leaves), {}, {}, tmp_path, more=machines.values())
    assert '<a href="../">Gadgets</a>' in page
    for name in ("ppm_d3", "rot1", "mem_d3", "msf15", "se_d3"):
        assert f"<code>{name}</code>" in page, name
    # the rotation source distils nothing: the factory's T-error counts are the factory's alone
    rot = page[page.index("<code>rot1</code>"):page.index('id="cat-injection"')]
    assert "one ion, not encoded" in rot and "triples" not in rot
    fac = page[page.index("<code>msf15</code>"):page.index('id="cat-rotation"')]
    assert "35 triples" in fac


def test_search_finds_the_runs_and_the_places_where_they_now_are():
    rows = site.index_entries()
    urls = {r["u"] for r in rows}
    assert "gadgets/" in urls and "gadgets/places/" in urls
    assert {f"gadgets/fullstack/{r['key']}/" for r in site.FULLSTACK} <= urls
    assert not [u for u in urls if u.startswith("gadgets/#")], "an anchor the landing no longer has"


def test_the_smallest_run_is_built_by_the_section(leaves, tmp_path):
    from qccd.fullstack import formalrv
    try:
        formalrv.check_pin()
    except formalrv.ToolchainError as e:
        pytest.skip(f"no pinned FormalRV clone: {e}")
    written = {}

    def put(rel, page, depth, active, app=False, extra=""):
        written[rel] = (depth, app)

    old = site.FULLSTACK
    site.FULLSTACK = tuple(s for s in old if s["key"] == "cnot")
    try:
        runs = site._build_fullstack(tmp_path / "gadgets", put, leaves=leaves,
                                     studio=tmp_path / "gadgets" / "studio", cache_dir=str(CACHE),
                                     log=lambda *a, **k: None, which="small")
    finally:
        site.FULLSTACK = old
    res = runs["cnot"]["result"]
    assert site.stack_ok(res) and res["linked"] == res["qec_statements"]
    assert written == {"gadgets/fullstack/cnot/index.html": (3, True)}
    page = (tmp_path / "gadgets" / "fullstack" / "cnot" / "index.html").read_text(encoding="utf-8")
    assert "cnot.prog" in page and str(tmp_path) not in page
    # the stages' scratch files are not published beside the page
    assert not list((tmp_path / "gadgets" / "fullstack" / "cnot").glob("*.qec"))
    assert json.dumps(res)  # a run's result is plain data
    # every layer's program in a column of its own, the line running now lit in each
    # (tests/test_gadget_columns.py says what that means)
    from test_gadget_columns import CHROME, NODE, _probe, check_columns
    if NODE and CHROME:
        out = _probe(tmp_path / "gadgets" / "fullstack" / "cnot")
        # the layers and, under them, the hardware instructions -- not the gadget program (the
        # user, 2026-10-29: it "should be concrete, detailed hardware instruction")
        check_columns(out, ["prog", "qppm", "qec", "hw"])
        assert "noleft" in out["app"]["cls"], "a full-stack run gives its columns the hierarchy's room"
        assert "noright" in out["app"]["cls"], "a full-stack run opens without the right-hand panel"
        # QEC's long statements get the room the logical program's short lines do not need
        w = {c["key"]: c["w"] for c in out["cols"]}
        assert w["qec"] > w["prog"] + 60, w
        lit = {c["key"]: c["lit"] for c in out["cols"]}
        assert "CX(0, 1)" in lit["prog"]
        assert "measure" in lit["qppm"] and "merge" in lit["qec"]
