"""Calling the decoder is an instruction (docs/GADGETS.md §10, §11).

Before this, the decoder ran by itself: every op that measured pushed its syndrome down its
wire the moment the bits existed, and the program never said so.  That made the classical
half invisible twice over -- it was not in the instruction set, and on the Design canvas a
syndrome's trip down its wire lasted five microseconds of a program hundreds of
milliseconds long, so no playback rate that showed ions moving ever drew it.

Now a place that measures keeps its syndrome in its readout buffer, and `decode` sends it:
down the wire from the place that measured it to the decoder, one decoding job per window,
and the frame update down its own wire to the classical memory.  Two rules make that more
than a label, and both are tested here:

* an outcome is not a result until its window is decoded -- a guard that reads one before
  that is refused, with the line the program is missing;
* every window is decoded before the program ends, or G11 fails and names it.

And on the page, the wires a `decode` uses are LIT while it runs, which is measured on the
canvas's own pixels -- with a mutation that switches the highlight off, because a highlight
that never comes on passes every count the page keeps about itself.
"""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from qccd.gadget.algorithm import AlgorithmError, parse_algorithm, parse_algorithm_file
from qccd.gadget.city import schedule
from qccd.gadget.library import LeafLibrary
from qccd.gadget.logic.flat import sign_off
from qccd.gadget.place_checks import CLASSICAL_FAMILIES, check_places
from qccd.gadget.town import town

ROOT = Path(__file__).resolve().parent.parent
ALGS = ROOT / "examples" / "gadgets" / "algorithms"
PROBE = ROOT / "tests" / "gadget_decode_browser.mjs"
CHROME = os.environ.get("CHROME") or next((p for p in (
    "C:/Program Files/Google/Chrome/Application/chrome.exe", "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser", "/usr/bin/chromium") if Path(p).exists()), None)
NODE = shutil.which("node")

BELL = """title Bell pair
prep q0 X
prep q1 Z
cx q0 q1
se q0 1
se q1 1
decode q0 q1
read q0 Z -> a
read q1 Z -> b
decode
"""


@pytest.fixture(scope="module")
def leaves(tmp_path_factory):
    return LeafLibrary(tmp_path_factory.getbasetemp() / "place_cache", log=None)


@pytest.fixture(scope="module")
def lib(leaves):
    return town(leaves)


def run(src, lib, leaves, name="t"):
    alg = parse_algorithm(src, name)
    return alg, schedule(alg, lib, leaves)


# --------------------------------------------------------------------------- the language


def test_decode_is_an_instruction_with_or_without_blocks():
    alg = parse_algorithm(BELL)
    dec = [i for i in alg.instructions if i.kind == "decode"]
    assert [(i.blocks, i.op) for i in dec] == [(["q0", "q1"], "decode"), ([], "decode")]
    # it touches no ion, so it is nothing in the ideal circuit: the same records either way
    bare = parse_algorithm(re.sub(r"(?m)^decode.*\n", "", BELL))
    assert (len(alg.ideal_circuit()[0].records) == len(bare.ideal_circuit()[0].records))
    # a block that has been read out may still be decoded: that is when its last window,
    # the readout's own, becomes a result
    parse_algorithm("prep q0 Z\nread q0 Z -> m\ndecode q0\n")


def test_what_decode_cannot_be_is_refused_with_the_reason():
    for src, why in (
        ("prep q0 Z\nread q0 Z -> m\nif m: decode q0", "cannot be guarded"),
        ("prep q0 Z\ndecode q0 -> m", "names no outcome"),
        ("prep q0 Z\ndecode q1", "has not been measured"),
        ("prep q0 Z\ndecode q0 q0", "named twice"),
    ):
        with pytest.raises(AlgorithmError) as exc:
            parse_algorithm(src)
        assert why in str(exc.value), (src, str(exc.value))


# --------------------------------------------------------------------------- the schedule


def test_a_program_that_never_decodes_fails_g11_and_names_every_window(lib, leaves):
    alg, gir = run(re.sub(r"(?m)^decode.*\n", "", BELL), lib, leaves)
    assert not gir["refused"]
    windows = gir["windows"]
    assert len(windows) == 6 and all(w["decoded_by"] is None for w in windows)
    assert not [m for m in gir["messages"] if m[9] == "syndrome"], "nothing was sent"
    ck = check_places(gir, lib, leaves)
    assert ck["failed"] == ["G11"]
    said = ck["violations"]["G11"]
    assert len(said) == 6 and all("is never decoded" in v for v in said)
    assert ck["metrics"]["undecoded_windows"] == 6


def test_decode_sends_each_window_from_the_place_that_measured_it(lib, leaves):
    alg, gir = run(BELL, lib, leaves)
    ck = check_places(gir, lib, leaves)
    assert not gir["refused"] and not ck["failed"], ck["violations"]
    assert sign_off(alg, gir, lib, leaves, distance=False)["passed"]
    events = {e[0]: e for e in gir["events"]}
    decoder = gir["control"]["decoder"]
    for w in gir["windows"]:
        ins = w["decoded_by"]
        assert ins is not None
        assert alg.instructions[ins].kind == "decode"
        syn = [m for m in gir["messages"] if m[9] == "syndrome" and m[13] == w["event"]]
        assert len(syn) == 1, w
        m = syn[0]
        # from the place that measured it, to the decoder, sent by the decode
        assert (m[2], m[3], m[4], m[10]) == (w["place"], "syn", decoder, ins)
        assert events[w["event"]][3] == w["place"]
        # not before the bits existed, and not before the program asked
        assert m[6] >= w["measured_us"] - 1e-6
        # and the decoder worked on it after it arrived, then the frame went to the memory
        assert w["decoded_us"] >= m[7] - 1e-6 and w["written_us"] > w["decoded_us"]
    # the first decode takes q0 and q1's rounds so far; the last takes the two readouts
    by = {}
    for w in gir["windows"]:
        by.setdefault(w["decoded_by"], []).append(w["op"])
    assert [sorted(v) for _, v in sorted(by.items())] == [
        ["prep.X", "prep.Z", "se.1", "se.1"], ["read.Z", "read.Z"]]


def test_calling_the_decoder_moves_no_ion(lib, leaves):
    """Decoding is classical: putting `decode` lines into a program changes nothing any ion
    does -- not a route, not a place, not a microsecond -- or the instruction would be
    costing the quantum half something it does not."""
    _, with_ = run(BELL, lib, leaves)
    _, without = run(re.sub(r"(?m)^decode.*\n", "", BELL), lib, leaves)
    masters = {p: v[0] for p, v in with_["leaves"].items()}
    fams = {m: lib[m].family for m in set(masters.values())}

    def quantum(gir):
        return sorted((round(e[1], 3), round(e[2], 3), e[3], e[4]) for e in gir["events"]
                      if fams[masters[e[3]]] not in CLASSICAL_FAMILIES)

    assert quantum(with_) == quantum(without)
    assert ([(round(c[4], 3), round(c[11], 3)) for c in with_["carries"]]
            == [(round(c[4], 3), round(c[11], 3)) for c in without["carries"]])


def test_a_guard_may_not_read_an_outcome_the_decoder_has_not_corrected(lib, leaves):
    src = """title guard before decode
prep q0 X
prep q1 Z
prep a X
read a Z -> m
if m: cx q0 q1
read q0 X -> u
read q1 X -> v
decode
"""
    _, gir = run(src, lib, leaves)
    assert [r[0] for r in gir["refused"]] == [4]
    why = gir["refused"][0][1]
    assert "has not been decoded" in why and "add `decode a` before this line" in why
    # the same program with the line it asked for goes through, and the guard waits for it
    fixed = src.replace("if m:", "decode a\nif m:")
    alg, gir = run(fixed, lib, leaves)
    assert not gir["refused"]
    assert not check_places(gir, lib, leaves)["failed"]
    written = max(w["written_us"] for w in gir["windows"] if w["decoded_by"] == 4)
    guarded = [e for e in gir["events"] if len(e) > 10 and e[10]]
    assert guarded and all(e[10]["ready_us"] >= written for e in guarded)


def test_decoding_a_block_takes_the_windows_of_what_it_consumed(lib, leaves):
    """An S̄ gadget consumes a |Ȳ⟩ block into q0; `decode q0` has to take that block's
    windows too, or they could never be named and would wait for ever."""
    _, gir = run("prep q0 X\ns q0\ndecode q0\nread q0 X -> m\ndecode q0\n", lib, leaves)
    assert not gir["refused"]
    assert all(w["decoded_by"] is not None for w in gir["windows"])
    assert any(any(".Y" in b for b in w["blocks"]) for w in gir["windows"])


def test_a_decode_with_nothing_to_decode_is_refused(lib, leaves):
    _, gir = run("prep q0 Z\ndecode q0\ndecode q0\nread q0 Z -> m\ndecode\n", lib, leaves)
    assert [r[0] for r in gir["refused"]] == [2]
    assert "nothing to decode" in gir["refused"][0][1]


def test_every_shipped_program_decodes_everything_it_measures(lib, leaves):
    for p in sorted(ALGS.glob("*.alg")):
        alg = parse_algorithm_file(p)
        assert any(i.kind == "decode" for i in alg.instructions), p.stem
        gir = schedule(alg, lib, leaves)
        assert gir["windows"] and all(w["decoded_by"] is not None for w in gir["windows"]), p.stem


# --------------------------------------------------------------------------- the checks


def test_the_checks_catch_a_decode_that_did_not_happen(lib, leaves):
    _, gir = run(BELL, lib, leaves)
    # a window claimed decoded with no syndrome message carrying it
    bad = copy.deepcopy(gir)
    w = bad["windows"][0]
    bad["messages"] = [m for m in bad["messages"] if not (m[9] == "syndrome" and m[13] == w["event"])]
    assert "G11" in check_places(bad, lib, leaves)["failed"]
    # a syndrome sent after its op ended by something that is not a decode
    bad = copy.deepcopy(gir)
    m = next(m for m in bad["messages"] if m[9] == "syndrome")
    m[10] = next(i["id"] for i in bad["program"]["instructions"] if i["kind"] == "read")
    assert "G10" in check_places(bad, lib, leaves)["failed"]


def test_the_checks_catch_a_guard_that_read_an_undecoded_outcome(lib, leaves):
    src = BELL.replace("read q1 Z -> b\ndecode\n", "read q1 Z -> b\ndecode\nprep q2 X\n"
                       "prep q3 Z\nif a: cx q2 q3\nread q2 Z -> c\nread q3 Z -> d\ndecode\n")
    _, gir = run(src, lib, leaves)
    assert not gir["refused"] and not check_places(gir, lib, leaves)["failed"]
    bad = copy.deepcopy(gir)
    ev = next(o["event"] for o in bad["outcomes"] if o["cvar"] == "a")
    w = next(w for w in bad["windows"] if w["event"] == ev)
    w["written_us"] = bad["makespan_us"] + 1.0          # decoded after the guard read it
    assert "G11" in check_places(bad, lib, leaves)["failed"]


# --------------------------------------------------------------------------- the page


@pytest.fixture(scope="module")
def page(tmp_path_factory, leaves):
    if NODE is None or CHROME is None:
        pytest.skip("node and Chrome are needed to look at the page")
    from qccd.gadget.verified import build_algorithm
    out = tmp_path_factory.mktemp("decode_page")
    build_algorithm(ALGS / "bell_cnot.alg", out_dir=out, leaves=leaves, log=None)
    return out


def probe(d: Path, shot: Path | None = None) -> dict:
    args = [NODE, str(PROBE), str(d)] + ([str(shot)] if shot else [])
    r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", timeout=300)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


@pytest.fixture(scope="module")
def seen(page):
    return probe(page, page / "decode.png")


def test_the_page_knows_each_decode_and_what_it_sends(seen):
    assert seen["errors"] == [] and "fatal" not in seen, seen.get("fatal")
    decs = seen["decodes"]
    assert len(decs) == 2
    assert decs[0]["senders"] == ["prep", "clinic"] and decs[0]["windows"] == 4
    assert decs[1]["senders"] == ["post"] and decs[1]["windows"] == 2
    for d in decs:
        # the syndrome wires of the senders, and the decoder's frame wire to the memory
        assert any("frame" in w for w in d["wires"]) and len(d["wires"]) == len(d["senders"]) + 1
        assert d["t0"] <= d["show"] <= d["t1"]


def test_the_wires_are_lit_while_a_decode_runs_and_only_then(seen):
    """Measured in pixels of the lit colours, against the same picture half a microsecond
    before the decode starts -- nothing on the stage has moved in between, so everything
    the count gains is the highlight."""
    for px in seen["pixels"]:
        before, during = px["before"], px["during"]
        assert during[0] >= 4 * before[0] and during[0] - before[0] > 500, px
        assert during[1] >= 4 * before[1] and during[1] - before[1] > 500, px
    # far from any decode the lit colours are only what else on the stage is near them
    assert seen["quiet"]["pixels"][0] < 0.25 * min(p["during"][0] for p in seen["pixels"])
    assert seen["quiet"]["hud"] == ""
    for p in seen["packets"]:
        assert p["packets"] >= 1, p
    for h, d in zip(seen["hud"], seen["decodes"]):
        assert h["text"].startswith(d["text"].split("#")[0].strip()), h
        assert f"{d['windows']} syndrome window" in h["text"]
    for p in seen["program"]:
        assert any("live" in c for c in p["live"]), p


def test_playing_stops_at_a_decode_and_slows_through_it(seen):
    """At 10 ms of machine time a second, a 10 µs decode is a thousandth of one frame: the
    playhead would step over it and nobody would ever see the bits move."""
    pb = seen["playback"]
    assert pb["samples_inside"] >= 3, pb          # it stopped IN the decode
    assert pb["slowed_max"] > 100, pb             # and was slowed there, and said so
    assert pb["reached_after"], pb                # and then went on


def test_the_decode_inspector_lists_its_windows(seen):
    text = seen["inspector"]
    assert "a call to the decoder" in text
    assert text.count("prep.") == 2 and text.count("se.1") == 2


def test_the_probe_would_see_a_highlight_that_never_comes_on(page, tmp_path):
    """A test that cannot fail is not a test: switch the highlight off and the pixel
    assertion must go red."""
    broken = tmp_path / "broken"
    shutil.copytree(page, broken)
    html = (broken / "index.html").read_text(encoding="utf-8")
    needle = "const lit = !!(S.lit && S.lit.nets.has(net));"
    assert needle in html, "the wire highlight moved; update this mutation"
    html = html.replace(needle, "const lit = false;")
    html = html.replace("const lit = S.lit && S.lit.places.has(cpath);", "const lit = false;")
    (broken / "index.html").write_text(html, encoding="utf-8")
    off = probe(broken)
    assert off["errors"] == []
    px = off["pixels"][0]
    assert not (px["during"][0] >= 4 * px["before"][0] and px["during"][0] - px["before"][0] > 500), (
        "the pixel test passed a page whose wires never light up")
