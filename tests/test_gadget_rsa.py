"""RSA-2048 at d = 11 as a page (qccd/gadget/rsa.py, tools/lattice_prime.py --frames).

Held here, on a run of two made-up steps:
* a frame file holds each gadget's start, program and tiles, in order of start, and reads back
  as `web/rsa.js` reads it;
* `pack` adds the steps' times into the prime's, numbers every distinct program once, maps each
  step's own program numbers onto those, and says of a merge what its d = 11 check said;
* the page states the answer from those numbers, and every control on it is declared for an
  agent (each `data-hint` has its `QCCD_HINTS` entry).
"""
from __future__ import annotations

import gzip
import json
import re
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import lattice_prime as LP  # noqa: E402

from qccd.fullstack.scale.lattice import Gadget  # noqa: E402
from qccd.gadget import rsa  # noqa: E402

PAIR = ("merge", "Z", (((0, 0), "bottom"), ((0, 1), "top")), ())


def _gadgets():
    return [Gadget("merge", 500.0, 138_800.0, frozenset([(3, 4), (3, 5)]), PAIR, 2),
            Gadget("prep", 0.0, 330.0, frozenset([(3, 4)]), ("prep", "Z"), 1),
            Gadget("read", 140_000.0, 140_440.0, frozenset([(-2, 7)]), ("read", "X"), 3)]


def _read(path):
    """A frame file as `web/rsa.js` reads it: (start, program, tiles) per gadget."""
    buf, out, p = gzip.decompress(Path(path).read_bytes()), [], 0
    while p < len(buf):
        start, prog, n = struct.unpack_from(LP.FRAME, buf, p)
        p += 7
        tiles = struct.unpack_from(f"<{2 * n}h", buf, p)
        p += 4 * n
        out.append((start, prog, [tuple(tiles[i:i + 2]) for i in range(0, len(tiles), 2)]))
    return out


def test_a_frame_reads_back_in_order_of_start(tmp_path):
    gs = _gadgets()
    index = {g.shape: i for i, g in enumerate(gs)}
    LP.write_frame(tmp_path / "f.bin.gz", gs, index)
    assert _read(tmp_path / "f.bin.gz") == [(0, 1, [(3, 4)]), (500, 0, [(3, 4), (3, 5)]), (140000, 2, [(-2, 7)])]


def _run(tmp_path):
    run = tmp_path / "run"
    (run / "steps").mkdir(parents=True)
    (run / "frames").mkdir()
    gs = _gadgets()
    shapes = list(dict.fromkeys(g.shape for g in gs))
    for i, (label, span) in enumerate([("gOnePrime/gLoop1/iter0", 65e6), ("gOnePrime/gLoop2/u0/gLoop2Step", 53e6)]):
        order = shapes if i == 0 else shapes[::-1]          # each step numbers its programs its own way
        LP.write_frame(run / "frames" / f"{i:03d}.bin.gz", gs, {s: k for k, s in enumerate(order)})
        (run / "steps" / f"{i:03d}.json").write_text(json.dumps({
            "label": label, "span_us": span, "gadgets": 3, "n_bad": 0, "check": [],
            "shapes": [[list(s), 1, 138300 if s[0] == "merge" else 330] for s in order]}))
    (run / "frames" / "layout.json").write_text(json.dumps({
        "bounds": [-3, 0, 5, 8], "tiles": 81, "factories": [["D", 0, 0, 1, 1]], "sites": [],
        "registers": {"pp": [[3, 4]]}, "seats": [[-2, 7]], "lanes": []}))
    log = tmp_path / "d11.log"
    log.write_text(f"{PAIR!r} | x1 | 505 ions, failed [], 138.3 ms, R10 ok (20 blocks) | 30 s\n")
    return run, log


def test_pack_adds_the_steps_and_numbers_each_program_once(tmp_path):
    run, log = _run(tmp_path)
    meta = rsa.pack(run, tmp_path / "data", [log])
    assert meta["prime_s"] == 118.0 and [s["t0"] for s in meta["steps"]] == [0.0, 65.0]
    assert [p["name"] for p in meta["parts"]] == ["gLoop1", "gLoop2"]
    progs = json.loads((tmp_path / "data" / "programs.json").read_text(encoding="utf-8"))
    assert len(progs) == 3
    a, b = (meta["steps"][k]["programs"] for k in (0, 1))
    assert a == b[::-1] and sorted(a) == [0, 1, 2]          # one numbering, whatever the step's own
    merge = next(p for p in progs if p["kind"] == "merge")
    assert merge["d11"] == "pass" and "505 ions, every rule, R10" in merge["text"]
    assert (tmp_path / "data" / "frames" / "001.bin.gz").exists()


def test_the_page_states_the_answer_and_declares_its_controls(tmp_path):
    run, log = _run(tmp_path)
    rsa.pack(run, tmp_path / "data", [log])
    body = rsa.page(tmp_path / "data")
    years = 21_640 * 118.0 / (365.25 * 86400)
    assert f"{years:.2f} years" in body and "concrete" in body and "exact" in body
    hints = json.loads(re.search(r"Object\.assign\(window\.QCCD_HINTS \|\| \{\}, (\{.*?\})\);", body).group(1))
    declared = set(re.findall(r'data-hint="([^"]+)"', body))
    assert declared and declared <= set(hints)
