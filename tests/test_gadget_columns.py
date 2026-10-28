"""Every program of a run in a column of its own (qccd/gadget/web/app.js, "the columns").

The user, 2026-10-28: "All programs should take an entire column! Don't squeeze them in one
column!! ... the user should see four program in four entire column (or only some of them).
Also, please use different color to highlight keyword, add annotation to keyword. Just as how
we do for hardware language."

So a gadget page stands each program beside the animation in a full-height column -- FormalRV's
.prog, .qppm and .qec on a full-stack run, the gadget program, the TSIR module and the hardware
program -- with the line running now lit, and every language's words coloured by what they do
and explained on hover (qccd/gadget/web/langs.js).  What is held here:

* the colouring only ever adds markup: every line of every language reads exactly as written,
  and every word the page marks as explained has its note (tests/gadget_langs.mjs);
* each statement word is drawn as a tag, so a language whose words were never tagged fails;
* a page that is not folded carries its leaves' TSIR as text beside it (`hw/leaf/<master>.js`),
  the same leaf module a folded run writes, so its Hardware column reads real TSIR;
* every control the columns add is declared for agents (docs/agent-interface.md);
* in Chrome, at 1600 x 900: the columns are full height, side by side, each with its lit line
  in view; a hovered tag shows its note; the Hardware column lights the very instruction the
  page says is running; Wrap and the folding panel work; the page never scrolls sideways.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "qccd" / "gadget" / "web"
FIX = ROOT / "tests" / "fixtures" / "fullstack"
NODE = shutil.which("node")
from chrome_path import CHROME  # noqa: E402

requires_node = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _langs(*pairs) -> dict:
    r = subprocess.run([NODE, str(ROOT / "tests" / "gadget_langs.mjs"), *[f"{k}={v}" for k, v in pairs]],
                       capture_output=True, text=True, encoding="utf-8", timeout=120)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def page(tmp_path_factory):
    """A small run as the site builds a place's page: not folded, so its TSIR text is the
    one `write_leaf_files` writes."""
    from qccd.gadget.library import LeafLibrary
    from qccd.gadget.verified import build_algorithm
    out = tmp_path_factory.mktemp("cols") / "bell"
    leaves = LeafLibrary(tmp_path_factory.getbasetemp() / "place_cache", log=None)
    build_algorithm(ROOT / "examples" / "gadgets" / "algorithms" / "bell_cnot.alg", out_dir=out, leaves=leaves, log=None)
    return out


# --------------------------------------------------------------------------- the languages


@requires_node
def test_every_line_of_every_language_reads_as_written_and_every_marked_word_is_explained(page):
    files = [("prog", p) for p in sorted((ROOT / "examples" / "fullstack").rglob("*.prog")) if p.is_file()]
    files += [("prog", p) for p in sorted((FIX / "dj" / "Examples").rglob("*.prog")) if p.is_file()]
    files += [("qppm", p) for p in sorted(FIX.glob("*/*.qppm"))]
    files += [("qec", p) for p in sorted(FIX.glob("*/*.qec"))]
    files += [("sys", p) for p in sorted(FIX.glob("*/*.sysprog"))]
    files += [("alg", p) for p in sorted((ROOT / "examples" / "gadgets" / "algorithms").glob("*.alg"))]
    files += [("tsir", p) for p in sorted((page / "hw" / "leaf").glob("*.js"))]
    assert {k for k, _ in files} == {"prog", "qppm", "qec", "sys", "alg", "tsir"}
    r = _langs(*files)
    assert r["changed"] == [], r["changed"][:5]
    assert r["unnoted"] == [], r["unnoted"][:5]
    assert r["tables"] == []
    # the statement words are TAGS, in every language -- the check above would pass on a
    # tokenizer that tagged nothing
    tagged = {}
    for key, f in r["files"].items():
        for w, n in f["tags"].items():
            tagged.setdefault(key.split(":")[0], set()).add(w)
    assert {"CX", "H", "measure"} <= tagged["prog"]
    assert {"prepare", "measure", "frame"} <= tagged["qppm"]
    assert {"alloc", "merge", "split", "extract", "decode", "frame"} <= tagged["qec"]
    assert {"request_ancilla", "route", "CNOT", "measure", "decode_syndrome", "pauli_frame_update"} <= tagged["sys"]
    assert {"prep", "cx", "read", "decode"} <= tagged["alg"]
    assert {"simd", "gate_", "cool", "init"} <= tagged["tsir"]


@requires_node
def test_a_word_is_a_statement_only_where_a_statement_starts(tmp_path):
    """`chan9.carry` names a road's gadget; `carry 12 …` is a carry row.  `x q0` is a logical
    Pauli; the `x` of `if x:` is not."""
    f = tmp_path / "t.tsir"
    f.write_text("  run 4 1.0 2.0 chan9.carry @road1 a:1\n  carry 12 87105.0 chan9.carry /r j0.s -> b.in chan9\n",
                 encoding="utf-8")
    g = tmp_path / "t.alg"
    g.write_text("prep q0 Z\nx q0\nif m: x q0\nppm X q0 -> x\n", encoding="utf-8")
    r = _langs(("tsir", f), ("alg", g))
    assert r["files"]["tsir:t.tsir"]["tags"] == {"run": 1, "carry": 1}
    assert r["files"]["alg:t.alg"]["tags"] == {"prep": 1, "x": 2, "ppm": 1}


# --------------------------------------------------------------------------- the page


def test_a_page_that_is_not_folded_carries_its_hardware_programs_as_tsir_text(page):
    """Every op the run schedules at a place that has ions is there, as TSIR text that reads
    back to exactly the program the page replays."""
    from qccd.gadget.place_checks import CLASSICAL_FAMILIES
    from qccd.ir.tsir import TSIR
    from qccd.ir.tsir_text import parse_program
    html = (page / "index.html").read_text(encoding="utf-8")
    blob = json.loads(html.split('<script id="gdata" type="application/json">', 1)[1].split("</script>", 1)[0])
    assert blob.get("leafText") is True
    gir = blob["gir"]
    masters = blob["library"]["masters"]
    ran = {(gir["leaves"][e[3]][0], e[4]) for e in gir["events"]}
    texts = {}
    for f in (page / "hw" / "leaf").glob("*.js"):
        m = re.fullmatch(r'window\.__gadgetModule&&window\.__gadgetModule\(("[^"]+"),(".*")\);\n', f.read_text(encoding="utf-8"), re.S)
        assert m, f.name
        assert json.loads(m.group(1)) == f"leaf/{f.stem}.tsir"
        texts[f.stem] = json.loads(m.group(2))
    leak = re.compile(r"[A-Za-z]:(?:\\\\|\\|/)+Users|/home/|\"root\":|\"file\":\s*\"qccd/|\.py\"")
    checked = 0
    for master, op in sorted(ran):
        if masters[master].get("family") in CLASSICAL_FAMILIES:
            continue                                  # bits, not ions: no hardware program
        text = texts.get(master)
        assert text is not None, f"{master} runs but has no TSIR text"
        assert not leak.search(text)
        sec = text.split(f"\nleaf {master}.{op}\n", 1)[1].split("\nleaf ", 1)[0]
        lines = [s[2:] for s in sec.split("\n") if s.strip() and not s.strip().startswith("source ")]
        raw = (page / "leaf" / f"{master}.js").read_text(encoding="utf-8")
        data = json.loads(raw[raw.index("(") + 1: raw.rindex(")")])
        assert parse_program("\n".join(lines)).to_json() == TSIR.from_json(data["programs"][op]).to_json()
        checked += 1
    assert checked >= 5


def test_every_control_the_columns_add_is_declared_for_agents():
    from qccd.gadget.page import COLUMN_HINTS
    used = set()
    for f in ("page.html", "app.js"):
        used |= set(re.findall(r'data-hint="([^"$]+)"', (WEB / f).read_text(encoding="utf-8")))
    assert used, "no data-hint found: the pattern is wrong, not the page"
    assert used - {"fullstack-line"} <= set(COLUMN_HINTS), used - set(COLUMN_HINTS)
    for k, h in COLUMN_HINTS.items():
        assert h["t"] and h["d"], k


# --------------------------------------------------------------------------- in Chrome


def _probe(d: Path, w=1600, h=900, shot=None) -> dict:
    args = [NODE, str(ROOT / "tests" / "gadget_columns_browser.mjs"), str(d), str(w), str(h)] + ([str(shot)] if shot else [])
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=240)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)
    assert "fatal" not in out, out.get("fatal")
    return out


def check_columns(out: dict, expect: list[str]) -> None:
    """What every page's columns must do, whatever they hold (also run on a full-stack run,
    tests/test_gadget_site.py)."""
    assert out["errors"] == []
    app = out["app"]
    assert app["scrollW"] <= app["W"], "the page scrolls sideways"
    assert [c["key"] for c in out["cols"]] == expect
    # the columns' region runs from under the header to the bottom of the page, and every
    # column the whole of it under the bar that picks them
    assert app["colsH"] >= app["appH"] - 48, app
    for c in out["cols"]:
        assert c["h"] >= app["colsH"] - app["barH"] - 2, c
        assert c["w"] >= 180, c
        assert c["lit"] and c["litInView"], c
        assert c["noted"] and c["tags"], c
    xs = sorted(c["w"] for c in out["cols"])
    assert xs[-1] - xs[0] <= 2, "the columns share the width"
    for n in out["notes"]:
        assert n["word"] and n["tip"], n
        assert n["tip"].split("\n")[1], n
    hw = out.get("hardware")
    assert hw, "the Hardware column lit nothing"
    f = hw["focus"]
    assert f and f["ins"] is not None
    assert hw["state"]["rel"] == f"leaf/{f['master']}.tsir"
    assert re.match(rf"^\d+ {f['ins']} ", hw["lit"]), (hw["lit"], f)
    assert out["wrap"] == {"off": True, "on": True}
    # folding the right-hand panel gives its room to the stage and the columns (the columns
    # first, while they are narrower than they would like), and opening it takes it back
    fold = out["fold"]
    assert sum(fold["folded"]) > sum(fold["before"]) + 250, fold
    assert fold["folded"][0] >= fold["before"][0] - 2 and fold["folded"][1] >= fold["before"][1] - 2, fold
    assert all(abs(a - b) <= 2 for a, b in zip(fold["after"], fold["before"])), fold


@pytest.mark.skipif(NODE is None or CHROME is None, reason="node and Chrome are needed")
def test_the_program_stands_in_a_column_of_its_own(page):
    out = _probe(page)
    check_columns(out, ["alg"])
    assert out["app"]["stage"] >= 500
