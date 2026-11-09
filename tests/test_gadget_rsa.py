"""RSA-2048 at d = 11 as a page (qccd/gadget/rsa.py, tools/lattice_prime.py --frames).

Held here, on a run of two made-up steps:
* a frame file holds each gadget's start, program and tiles, in order of start, and reads back
  as `web/rsa.js` reads it;
* `pack` adds the steps' times into the prime's, numbers every distinct program once, maps each
  step's own program numbers onto those, and says of each program what its d = 11 check said;
* the tiles holding a block as each step starts: a prime begins as the prime before it ends, a
  preparation makes a tile hold one and a readout frees it, and a register's home that neither
  prepares nor reads holds its block throughout;
* the ions counted are the tiles the program touches (and the registers' homes), 288 a tile --
  not the floorplan's rectangle;
* a program's ion file reads back: every ion starts in a trap of its kind, every move passes
  junctions and ends in a trap, cycles in time order, its time the program's own;
* the page states the answer and the ions from those numbers, and every control on it is declared
  for an agent (each `data-hint` has its `QCCD_HINTS` entry).
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
    return [Gadget("merge", 500.0, 138_840.0, frozenset([(3, 4), (3, 5)]), PAIR, 2),
            Gadget("prep", 0.0, 345.0, frozenset([(3, 4)]), ("prep", "Z"), 1),
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
    """Two steps: the same three gadgets, and a bus tile (0, 5) prepared in the first, read in the second."""
    run = tmp_path / "run"
    (run / "steps").mkdir(parents=True)
    (run / "frames").mkdir()
    extra = [Gadget("prep", 1_000.0, 1_345.0, frozenset([(0, 5)]), ("prep", "X"), 4),
             Gadget("read", 1_000.0, 1_440.0, frozenset([(0, 5)]), ("read", "Z"), 4)]
    for i, (label, span) in enumerate([("gOnePrime/gLoop1/iter0", 65e6), ("gOnePrime/gLoop2/u0/gLoop2Step", 53e6)]):
        gs = _gadgets() + [extra[i]]
        shapes = list(dict.fromkeys(g.shape for g in gs))
        order = shapes if i == 0 else shapes[::-1]          # each step numbers its programs its own way
        LP.write_frame(run / "frames" / f"{i:03d}.bin.gz", gs, {s: k for k, s in enumerate(order)})
        dur = {"merge": 138_340, "prep": 345, "read": 440}
        (run / "steps" / f"{i:03d}.json").write_text(json.dumps({
            "label": label, "span_us": span, "gadgets": 4, "n_bad": 0, "check": [],
            "shapes": [[list(s), 1, dur[s[0]]] for s in order]}))
    (run / "frames" / "layout.json").write_text(json.dumps({
        "bounds": [-3, 0, 5, 8], "tiles": 81, "factories": [["D", 0, 0, 1, 1]], "sites": [],
        "registers": {"pp": [[3, 4]], "exp": [[4, 8]]}, "seats": [[-2, 7]], "lanes": []}))
    log = tmp_path / "d11.log"
    log.write_text(f"{PAIR!r} | x1 | 505 ions, failed [], 138.340 ms, R10 ok (20 blocks) | 30 s\n"
                   "('prep', 'Z') | x1 | 121 ions, failed [], 0.310 ms, R10 ok (1 blocks) | 1 s\n")
    return run, log


def test_pack_adds_the_steps_and_numbers_each_program_once(tmp_path):
    run, log = _run(tmp_path)
    meta = rsa.pack(run, tmp_path / "data", [log])
    assert meta["prime_s"] == 118.0 and [s["t0"] for s in meta["steps"]] == [0.0, 65.0]
    assert [p["name"] for p in meta["parts"]] == ["gLoop1", "gLoop2"]
    progs = json.loads((tmp_path / "data" / "programs.json").read_text(encoding="utf-8"))
    assert len(progs) == 5
    a, b = (meta["steps"][k]["programs"] for k in (0, 1))
    assert sorted(set(a) & set(b)) == [0, 1, 2]            # one numbering, whatever the step's own
    merge = next(p for p in progs if p["kind"] == "merge")
    assert merge["d11"] == "pass" and merge["check"] == "checked at d = 11: every rule and R10"
    assert merge["uses"] == 2 and merge["steps"] == [0, 1] and merge["dur_us"] == 138_340
    assert next(p for p in progs if p["shape"] == ["read", "X"])["d11"] == "unchecked"
    assert (tmp_path / "data" / "frames" / "001.bin.gz").exists()


def _live(path, layout):
    x0, y0, x1, y1 = layout["bounds"]
    w = x1 - x0 + 1
    bits = gzip.decompress(Path(path).read_bytes())
    return {(i % w + x0, i // w + y0) for i in range(w * (y1 - y0 + 1)) if bits[i >> 3] >> (i & 7) & 1}


def test_the_tiles_holding_a_block_as_each_step_starts(tmp_path):
    run, log = _run(tmp_path)
    rsa.pack(run, tmp_path / "data", [log])
    layout = json.loads((tmp_path / "data" / "layout.json").read_text(encoding="utf-8"))
    # (3, 4): prepared last in the prime, so held as it starts; (4, 8): a home never touched, held
    # throughout; (0, 5): read last in the prime, so free as it starts, held once step 0 prepares it
    assert _live(tmp_path / "data" / "live" / "000.bin.gz", layout) == {(3, 4), (4, 8)}
    assert _live(tmp_path / "data" / "live" / "001.bin.gz", layout) == {(3, 4), (4, 8), (0, 5)}


def test_the_ions_are_the_tiles_the_program_touches(tmp_path):
    run, log = _run(tmp_path)
    use = rsa.pack(run, tmp_path / "data", [log])["use"]
    assert use == {"tiles": 5, "ions": 5 * 288, "homes": 2, "factories": 0, "routing": 3, "floorplan": 81,
                   "with_rest": False}


def test_a_programs_ion_file_reads_back(tmp_path):
    from qccd.fullstack.scale import programs as P
    from qccd.verify.replay import replay
    b = P.build(("idle",))
    prog = P.cooled(b)
    res = replay(prog, b.arch, P._model(), check_rules=False)
    buf = rsa.ion_bytes(b.arch, prog, res.cycles)
    assert buf[:4] == b"QIO1"
    n, nc, us = struct.unpack_from("<IId", buf, 4)
    assert n == b.ions == 241 and us == res.total_us == P.machine_us(b)
    p, kinds = 20, []
    for _ in range(n):
        kind, x, y = struct.unpack_from("<Bhh", buf, p)
        p += 5
        assert (x % 2, y % 2) == ((0, 0) if kind == 0 else (1, 1))     # a data trap, or a check's
        kinds.append(kind)
    assert kinds.count(0) == 121
    last, moves = 0.0, 0
    for _ in range(nc):
        t0, t1, what, cnt = struct.unpack_from("<ffBI", buf, p)
        p += 13
        assert last <= t0 <= t1 <= us
        last = t0
        if what == rsa.MOVE:
            for _ in range(cnt):
                _, k = struct.unpack_from("<HB", buf, p)
                pts = struct.unpack_from(f"<{2 * k}h", buf, p + 3)
                p += 3 + 4 * k
                assert all((x % 2, y % 2) == (1, 0) for x, y in zip(pts[:-2:2], pts[1:-2:2]))   # junctions
                assert (pts[-2] % 2) == (pts[-1] % 2)                                            # then a trap
                moves += 1
        elif what == rsa.PAIR:
            p += 4 * cnt
        elif what != rsa.COOL:
            p += 2 * cnt
    assert p == len(buf) and moves > 0


def test_qio2_holds_the_same_cycles_as_qio1():
    """A merge repeats its round cycle for cycle, so QIO2 keeps each distinct cycle once: written back out cycle by cycle it is QIO1, byte for
    byte, at a tenth of the size."""
    from qccd.fullstack.scale import layer as L
    from qccd.fullstack.scale import programs as P
    from qccd.verify.replay import replay
    b = L.build([("X", [((0, 0), "right"), ((1, 0), "left")], set())])
    prog = P.cooled(b)
    res = replay(prog, b.arch, P._model(), check_rules=False)
    one, two = (rsa.ion_bytes(b.arch, prog, res.cycles, distinct=q) for q in (False, True))
    assert one[:4] == b"QIO1" and two[:4] == b"QIO2" and one[4:20] == two[4:20]
    n, nc, _ = struct.unpack_from("<IId", two, 4)
    p = 20 + 5 * n
    (nb,) = struct.unpack_from("<I", two, p)
    p += 4
    bodies = []
    for _ in range(nb):
        (k,) = struct.unpack_from("<I", two, p)
        bodies.append(two[p + 4:p + 4 + k])
        p += 4 + k
    back = bytearray(two[:20 + 5 * n].replace(b"QIO2", b"QIO1", 1))
    for _ in range(nc):
        t0, t1, k = struct.unpack_from("<ffI", two, p)
        back += struct.pack("<ff", t0, t1) + bodies[k]
        p += 12
    assert p == len(two) and bytes(back) == one
    assert nb < nc / 10 and len(two) < len(one) / 5


def test_the_page_states_the_answer_and_declares_its_controls(tmp_path):
    run, log = _run(tmp_path)
    rsa.pack(run, tmp_path / "data", [log])
    body = rsa.page(tmp_path / "data")
    years = 21_640 * 118.0 / (365.25 * 86400)
    assert f"{years:.2f} years" in body and "concrete" in body and "exact" in body
    assert "0.0 million ions" in body and "<b>5</b>" in body       # 5 tiles touched, of the 81 of the rectangle
    hints = json.loads(re.search(r"Object\.assign\(window\.QCCD_HINTS \|\| \{\}, (\{.*?\})\);", body).group(1))
    declared = set(re.findall(r'data-hint="([^"]+)"', body))
    assert declared and declared <= set(hints)
    assert {"rsa-detail", "rsa-card", "rsa-programs", "rsa-show"} <= declared


def _gidney_data(tmp_path):
    """gadgets/rsa2048/gidney/'s data, made up: two windows, a merge, a cultivated |T>, a cold access."""
    d = tmp_path / "gdata"
    d.mkdir()
    steps = [{"label": "gLoop1, window 1 of 2", "part": "gLoop1", "t0": 0.0, "span": 120.0, "lead": 0, "gadgets": 3, "programs": [0, 1],
              "check": "clean"},
             {"label": "gLoop1, window 2 of 2", "part": "gLoop1", "t0": 120.0, "span": 80.0, "lead": 61032, "gadgets": 2,
              "programs": [0, 2], "check": "clean"}]
    meta = {"prime_s": 200.0, "program_s": 21_640 * 200.0 + 5000.0, "primes": 21_640, "tiles": 81, "ions_per_tile": 288, "factories": 2,
            "steps": steps, "parts": [{"name": "gLoop1", "color": "#8fb3e8", "steps": 2, "t0": 0.0, "span": 200.0}], "gadgets": 5,
            "window_s": 120.0, "hot": {"tiles": 9, "data": 1089, "faces": 1080, "ions": 2169}, "cold_ions": 730,
            "work_s": {"merge": 150.0, "|T>": 60.0, "cold": 10.0}, "merge_us": 61032.0}
    merge = ["merge", "X", [[[0, 0], "right"], [[1, 0], "left"]], []]
    progs = [{"id": 0, "kind": "merge", "shape": merge, "dur_us": 61032, "us": 61032.0, "ions": 505, "instructions": 900, "key": "aa",
              "title": rsa.title(merge), "d11": "pass", "check": "checked at d = 11", "uses": 3, "steps": [0, 1], "page": False},
             {"id": 1, "kind": "|T>", "shape": ["T"], "dur_us": 123191, "us": 117369.0, "ions": 246, "instructions": 40, "key": "bb",
              "title": "|T> by cultivation", "d11": "pass", "check": "checked at d = 11", "uses": 1, "steps": [0], "page": False},
             {"id": 2, "kind": "cold", "shape": ["cold"], "dur_us": 66580, "us": 66580.0, "ions": 0, "instructions": 0, "key": "",
              "title": "a cold access (composed)", "d11": "composed", "check": "composed: not compiled", "uses": 1, "steps": [1],
              "page": False}]
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / "programs.json").write_text(json.dumps(progs), encoding="utf-8")
    return d


def test_gidneys_compilation_page_states_its_answer_and_what_is_composed(tmp_path):
    """gadgets/rsa2048/gidney/: the program's own time (not primes times a prime), the ions hot and cold, what is composed, the comparison
    with our own compilation, and every control declared for an agent."""
    from qccd.gadget import rsa_gidney
    body = rsa_gidney.page(_gidney_data(tmp_path), ours={"years": 21.56, "ions": 18_982_944})
    years = (21_640 * 200.0 + 5000.0) / (365.25 * 86400)
    assert f"{years:.2f} years" in body and "2,169 ions" in body and "730" in body
    assert "2 of 2</b>programs checked" in body                     # the cold access is not a program
    assert "composed" in body and "drive lines" in body and 'href="../"' in body
    hints = json.loads(re.search(r"Object\.assign\(window\.QCCD_HINTS \|\| \{\}, (\{.*?\})\);", body).group(1))
    declared = set(re.findall(r'data-hint="([^"]+)"', body))
    assert declared and declared <= set(hints)
    assert rsa_gidney.summary(tmp_path / "gdata")["ions"] == 2169 + 730


def test_our_page_links_gidneys_compilation_once_it_exists(tmp_path):
    run, log = _run(tmp_path)
    rsa.pack(run, tmp_path / "data", [log])
    assert "next run" in rsa.page(tmp_path / "data") and 'gidney/' not in rsa.page(tmp_path / "data")
    body = rsa.page(tmp_path / "data", {"years": 29.48, "ions": 679_898})
    assert "href='gidney/'" in body and "29.48 years on 0.68 million ions" in body
