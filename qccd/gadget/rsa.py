"""RSA-2048 at d = 11 as a page of the website: gadgets/rsa2048/ (docs/FULLSTACK_PLAN.md S9).

The page gives the answer -- how long Gidney 2025, as FormalRV writes it, takes to factor RSA-2048
on the stationary-tile QCCD machine at d = 11, and on how many ions -- what the number rests on,
and one prime played on the machine's floorplan step by step (`web/rsa.js`), at any level of
detail from the whole machine to every ion: each step's gadgets are fetched when the clock nears
it and dropped behind it, so a reader's browser holds a few steps, never the 282 million gadgets
of the prime, and a program's ions only once one of its gadgets is in view.

    ion_bytes(arch, prog, cycles)    one compiled program's ions, cycle by cycle, for the viewer (QIO1; QIO2
                                     with each distinct cycle once)
    pack(run, dst, logs, rest, made) the page's data from `tools/lattice_prime.py --frames` runs and
                                     `tools/lattice_programs.py`'s programs
    page(dst)                        the page's body, from the packed data
    build(base, put, data)           gadgets/rsa2048/ into a site (the data copied beside it)
"""
from __future__ import annotations

import html
import json
import re
import shutil
from pathlib import Path

__all__ = ["KEY", "ion_bytes", "title", "pack", "page", "build", "index_entry"]

KEY = "rsa2048"
WEB = Path(__file__).resolve().parent / "web"
#: the prime's parts, in program order, and the colour each is drawn in on the clock's strip
PARTS = (("gLoop1", "#8fb3e8"), ("gLoop2", "#7cc4a4"), ("gLoop3", "#b39ddb"), ("gLoop4", "#f4a7a7"),
         ("gUnloop3", "#c3a5f0"), ("gUnloop2", "#9fd8bd"))
PRIMES = 21_640
#: what a cycle of a program's ion file does (`ion_bytes`)
MOVE, PAIR, PULSE, MEASURE, RESET, COOL, VIRTUAL = 1, 2, 3, 4, 5, 6, 7


def theme_script() -> str:
    """The studio's palette and shape ratios for the player (qccd/viz/theme.py): it draws a trap, a junction, a rail
    and an ion by the same numbers as a design page and the gadget town."""
    from ..viz.theme import GEOMETRY, PALETTE
    return f"<script>window.QCCD_THEME = {json.dumps({'palette': dict(PALETTE), 'geometry': dict(GEOMETRY)})};</script>"


def ion_key() -> tuple:
    """The ions' colours, as the gadget town's legend names them (qccd/gadget/web/app.js)."""
    from ..viz.theme import PALETTE
    return (("an ion", PALETTE["data"]), ("in a gate", PALETTE["x"]), ("measured or reset", PALETTE["z"]),
            ("the site in play", PALETTE["active"]))


def ion_bytes(arch, prog, cycles, distinct: bool = False) -> bytes:
    """One program's ions for the viewer: every ion's trap at the start and, cycle by cycle as the
    replay timed it (`cycles`: its `CycleRecord`s), every move and pulse.  Little-endian:

        "QIO1", uint32 ions, uint32 cycles, float64 the program's µs
        per ion: uint8 kind (0 a data ion, 1 a measure ion), int16 x, int16 y -- its trap
        per cycle: float32 t0, float32 t1 (µs from the start), then its body: uint8 what, uint32 n, then n of
            MOVE      uint16 ion, uint8 k, k x (int16 x, int16 y): the junctions it passes, its trap
            PAIR      uint16 ion, uint16 ion: a two-qubit gate
            PULSE, VIRTUAL, MEASURE, RESET   uint16 ion
            COOL      nothing (every ion)

    `distinct` writes "QIO2" instead, for a program that repeats its cycles (a merge's rounds are one
    round cycle for cycle): the same header and ions, then uint32 bodies, each body once as uint32
    its length and its bytes, then per cycle float32 t0, float32 t1, uint32 its body.  The same
    moves and pulses, about a tenth of the bytes.

    Positions are in the grid's units (spacing 2) from data point (0, 0)'s trap: data point
    (row, col) at (2 col, 2 row), a check's measure ion at odd x and odd y, a junction at odd x and
    even y; a tile is 2 (d + 1) units a side (`qccd.fullstack.scale.programs`)."""
    import struct
    nodes, segs = arch.device.nodes, arch.device.segments
    ox, oy = nodes["T0_1h"].pos[:2]

    def at(node):
        x, y = nodes[node].pos[:2]
        return int(round(x - ox)), int(round(y - oy))

    init = next(i for i in prog.instructions if i.type == "init")
    ions = list(init.placement)
    index = {q: k for k, q in enumerate(ions)}
    when = {c.instr_id: (c.t0, c.t1) for c in cycles}
    out = bytearray()
    timed = []                                      # (t0, t1, body) a cycle
    for ins in prog.instructions:
        if ins.type in ("init", "barrier") or ins.id not in when:
            continue
        t0, t1 = when[ins.id]
        if ins.participants:
            recs = bytearray()
            for p in ins.participants:
                cur, pts = p.src, []
                for sid in p.via:
                    a, b = segs[sid].ends
                    cur = b if a == cur else a
                    pts.append(at(cur))
                if cur != p.dst:
                    pts.append(at(p.dst))
                recs += struct.pack("<HB", index[p.ion], len(pts))
                recs += struct.pack(f"<{2 * len(pts)}h", *[v for pt in pts for v in pt])
            what, count = MOVE, len(ins.participants)
        elif ins.type == "gate" and ins.pairs:
            recs = struct.pack(f"<{2 * len(ins.pairs)}H", *[index[q] for pr in ins.pairs for q in pr])
            what, count = PAIR, len(ins.pairs)
        elif ins.type in ("gate", "measure", "reset") and ins.ions:
            what = {"measure": MEASURE, "reset": RESET}.get(ins.type, VIRTUAL if ins.gate == "VZ" else PULSE)
            recs = struct.pack(f"<{len(ins.ions)}H", *[index[q] for q in ins.ions])
            count = len(ins.ions)
        elif ins.type == "cool":
            what, count, recs = COOL, 0, b""
        else:
            continue
        timed.append((t0, t1, struct.pack("<BI", what, count) + bytes(recs)))
    out += (b"QIO2" if distinct else b"QIO1") + struct.pack("<IId", len(ions), len(timed), max(c.t1 for c in cycles))
    for q in ions:
        node = init.placement[q]
        out += struct.pack("<Bhh", 0 if node.endswith("h") else 1, *at(node))
    if not distinct:
        return bytes(out + b"".join(struct.pack("<ff", t0, t1) + body for t0, t1, body in timed))
    index: dict = {}
    seq = [index.setdefault(body, len(index)) for _, _, body in timed]
    out += struct.pack("<I", len(index))
    for body in index:                              # in first use order
        out += struct.pack("<I", len(body)) + body
    out += b"".join(struct.pack("<ffI", t0, t1, k) for (t0, t1, _), k in zip(timed, seq))
    return bytes(out)


def _label(raw: str) -> tuple[str, str]:
    """(part, a reader's label) for a step label like gOnePrime/gLoop3/j2/k0/gLoop3Step."""
    parts = raw.split("/")[1:]
    part = parts[0]
    if part == "gLoop1":
        return part, f"gLoop1, iteration {int(parts[1][4:]) + 1} of 214"
    idx = [m for m in (re.fullmatch(r"(u2|k2|j|k|u)(\d+)", p) for p in parts[1:]) if m]
    where = ", ".join(f"{m[1]} = {m[2]}" for m in idx)
    what = "the work between steps" if parts[-1] == "between" else ("its last work" if parts[-1] == "end" else
                                                                     parts[-1])
    return part, f"{part}, {what}" + (f" ({where})" if where else "")


_TITLES = {("idle",): "a held patch's syndrome round", ("ht",): "H̄ (Gehér et al. 2024, on our tile)",
           ("prep", "Z"): "a tile's preparation in Z̄", ("prep", "X"): "a tile's preparation in X̄",
           ("prep", "Y"): "|Ȳ> in place (Gidney's circuit)", ("prep", "T"): "|T>: the corner injection's preparation",
           ("read", "Z"): "a tile's readout in Z̄", ("read", "X"): "a tile's readout in X̄"}


def title(shape) -> str:
    """A program's name, for a reader."""
    if shape[0] == "merge":
        return (f"a {shape[1]}̄{shape[1]}̄ merge of {len(shape[2])} patches"
                + (f" through a corridor of {len(shape[3])} tiles" if shape[3] else " face to face"))
    return _TITLES[tuple(shape)]


def _key(shape) -> tuple:
    if shape[0] != "merge":
        return tuple(shape)
    return (shape[0], shape[1], tuple((tuple(a), b) for a, b in shape[2]), tuple(tuple(c) for c in shape[3]))


def _records(buf: bytes):
    """A frame's records as arrays: (bytes, offsets, tile counts, programs) -- `lattice_prime.FRAME`."""
    import numpy as np
    offs, p, n = [], 0, len(buf)
    ap = offs.append
    while p < n:
        ap(p)
        p += 7 + 4 * buf[p + 6]
    a = np.frombuffer(buf, np.uint8)
    o = np.array(offs, dtype=np.int64)
    k = a[o + 6].astype(np.int64)
    return a, o, k, a[o + 4].astype(np.int64) | (a[o + 5].astype(np.int64) << 8)


def _tiles(a, o, k):
    """(record, x, y) of every tile of every record."""
    import numpy as np
    first = np.cumsum(k) - k
    at = np.repeat(o + 7, k) + 4 * (np.arange(int(k.sum())) - np.repeat(first, k))

    def word(i):
        return (a[i].astype(np.uint16) | (a[i + 1].astype(np.uint16) << 8)).view(np.int16).astype(np.int64)
    return np.repeat(np.arange(len(o)), k), word(at), word(at + 2)


def _places(layout) -> list:
    """Each register's and each farm's box, the biggest first: the names the map writes."""
    boxes = {}
    for r, ts in layout["registers"].items():
        boxes[r] = [min(x for x, _ in ts), min(y for _, y in ts), max(x for x, _ in ts), max(y for _, y in ts)]
    for tag, x0, y0, x1, y1 in layout["factories"]:
        b = boxes.setdefault(tag, [x0, y0, x1, y1])
        boxes[tag] = [min(b[0], x0), min(b[1], y0), max(b[2], x1), max(b[3], y1)]
    return sorted(([n, b] for n, b in boxes.items()),
                  key=lambda nb: -(nb[1][2] - nb[1][0] + 1) * (nb[1][3] - nb[1][1] + 1))


def pack(run, dst, logs, rest=None, made=None) -> dict:
    """The page's data in `dst` from a `lattice_prime.py --frames` run of one prime (`run`), the
    d = 11 logs of `tools/lattice_check.py` (`logs`), if given a `--rest` run (with `--frames`, the
    tiles it touches count as in use too) and `tools/lattice_programs.py`'s output (`made`: each
    program's exact time, its ions' file and its design page): meta.json, programs.json,
    layout.json, frames/, live/ (one bit a tile of the floorplan, row by row: does it hold a block
    as the step starts), ions/ and pages/."""
    import gzip
    import sys

    import numpy as np
    tools = str(Path(__file__).resolve().parents[2] / "tools")
    if tools not in sys.path:
        sys.path.insert(0, tools)
    from lattice_check import parse_log
    run, dst = Path(run), Path(dst)
    for sub in ("frames", "live", "ions", "pages"):
        (dst / sub).mkdir(parents=True, exist_ok=True)
    checked = {}
    for lg in logs:
        checked.update(parse_log(Path(lg).read_text(encoding="utf-8").splitlines()))
    files = {}
    if made:
        for ln in (Path(made) / "programs.jsonl").read_text(encoding="utf-8").splitlines():
            row = json.loads(ln)
            files[json.dumps(row["shape"])] = row
    layout = json.loads((run / "frames" / "layout.json").read_text(encoding="utf-8"))
    X0, Y0, X1, Y1 = layout["bounds"]
    W, H = X1 - X0 + 1, Y1 - Y0 + 1
    homes = np.zeros(W * H, bool)
    for ts in layout["registers"].values():
        for x, y in ts:
            homes[(y - Y0) * W + x - X0] = True
    used = homes.copy()
    programs, ids, steps, t, lasts = [], {}, [], 0.0, []

    def program(s, dur):
        key = json.dumps(s)
        if key not in ids:
            row, c = files.get(key, {}), checked.get(_key(s))
            ok = bool(c) and not c[1] and c[3]
            ids[key] = len(programs)
            programs.append({
                "id": len(programs), "kind": s[0], "shape": s, "dur_us": dur, "us": row.get("us", dur),
                "ions": row.get("ions", c[0] if c else 0), "instructions": row.get("instructions", 0),
                "key": row.get("key", ""), "title": title(s),
                "d11": "unchecked" if not c else ("pass" if ok else "fail"),
                "check": ("not yet checked at d = 11" if not c else "checked at d = 11: every rule and R10" if ok
                          else f"at d = 11 it FAILS {c[1]}"), "uses": 0, "steps": []})
        return ids[key]

    rows = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run / "steps").glob("[0-9][0-9][0-9].json"))]
    for i, r in enumerate(rows):
        local = [program(s, dur) for s, _, dur in r["shapes"]]
        for (_, count, _), p in zip(r["shapes"], local):
            programs[p]["uses"] += count
            programs[p]["steps"].append(i)
        part, label = _label(r["label"])
        steps.append({"label": label, "part": part, "t0": round(t, 6), "span": r["span_us"] / 1e6,
                      "gadgets": r["gadgets"], "programs": local, "check": "clean" if not r["n_bad"] else r["check"]})
        t += r["span_us"] / 1e6
        src = run / "frames" / f"{i:03d}.bin.gz"
        if not src.exists() and r.get("same_as"):     # the same schedule as an earlier step: its frame
            src = run / "frames" / r["same_as"].replace(".json", ".bin.gz")
        shutil.copyfile(src, dst / "frames" / f"{i:03d}.bin.gz")
        # the tiles it touches, and each tile's last preparation or readout: does it hold a block after
        a, o, k, prog = _records(gzip.decompress(src.read_bytes()))
        _, x, y = _tiles(a, o, k)
        used[(y - Y0) * W + (x - X0)] = True
        kinds = np.array([programs[p]["kind"] for p in local] + ["-"])[np.minimum(prog, len(local))]
        one = (k == 1) & np.isin(kinds, ("prep", "read"))
        tile = (a[o + 9].astype(np.uint16) | (a[o + 10].astype(np.uint16) << 8)).view(np.int16).astype(np.int64)
        tx = (a[o + 7].astype(np.uint16) | (a[o + 8].astype(np.uint16) << 8)).view(np.int16).astype(np.int64)
        ev_t, ev = ((tile - Y0) * W + (tx - X0))[one], (kinds[one] == "prep").astype(np.int8)
        u, at = np.unique(ev_t[::-1], return_index=True)
        lasts.append((u, ev[::-1][at]))
    # a prime begins as the prime before it ends (every prime is the same): a tile holds a block if
    # its last preparation or readout in the prime is a preparation; a register's home that neither
    # prepares nor reads in a prime holds its block throughout
    last = np.full(W * H, -1, np.int8)
    for u, ev in lasts:
        last[u] = ev
    live = np.where(last < 0, homes, last == 1)
    for i, (u, ev) in enumerate(lasts):
        (dst / "live" / f"{i:03d}.bin.gz").write_bytes(
            gzip.compress(np.packbits(live, bitorder="little").tobytes(), 9, mtime=0))
        live[u] = ev.astype(bool)
    rest_steps = []
    if rest:
        rest_steps = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((Path(rest) / "steps").glob("*.json"))]
        for f in sorted((Path(rest) / "frames").glob("*.bin.gz")):
            a, o, k, _ = _records(gzip.decompress(f.read_bytes()))
            _, x, y = _tiles(a, o, k)
            used[(y - Y0) * W + (x - X0)] = True
    if json.dumps(["idle"]) in files:                # what a held patch runs: in no frame, drawn under every held tile
        program(["idle"], files[json.dumps(["idle"])]["us"])
    for pr in programs:
        for sub, ext in (("ions", ".bin.gz"), ("pages", ".html.gz")):
            f = Path(made or ".") / sub / (pr["key"] + ext)
            if pr["key"] and f.exists():
                shutil.copyfile(f, dst / sub / f.name)
        pr["page"] = bool(pr["key"]) and (dst / "pages" / f"{pr['key']}.html.gz").exists()
    parts = []
    for name, color in PARTS:
        mine = [s for s in steps if s["part"] == name]
        if mine:
            parts.append({"name": name, "color": color, "steps": len(mine), "t0": mine[0]["t0"],
                          "span": round(sum(s["span"] for s in mine), 6)})
    fac = np.zeros(W * H, bool)
    for _, x0, y0, x1, y1 in layout["factories"]:
        for yy in range(y0, y1 + 1):
            fac[(yy - Y0) * W + x0 - X0:(yy - Y0) * W + x1 - X0 + 1] = True
    layout["places"] = _places(layout)
    n_used = int(used.sum())
    meta = {"prime_s": round(t, 6), "primes": PRIMES, "tiles": layout["tiles"], "ions_per_tile": 288,
            "factories": len(layout["factories"]), "steps": steps, "parts": parts,
            "gadgets": sum(s["gadgets"] for s in steps),
            "use": {"tiles": n_used, "ions": n_used * 288, "homes": int(homes.sum()),
                    "factories": int((used & fac & ~homes).sum()), "routing": int((used & ~fac & ~homes).sum()),
                    "floorplan": W * H, "with_rest": bool(rest)}}
    if rest:
        meta["rest"] = {"steps": len(rest_steps), "s": round(sum(r["span_us"] for r in rest_steps) / 1e6, 6),
                        "clean": all(not r["n_bad"] for r in rest_steps)}
    (dst / "meta.json").write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    (dst / "programs.json").write_text(json.dumps(programs, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    (dst / "layout.json").write_text(json.dumps(layout, separators=(",", ":")), encoding="utf-8")
    return meta


def _years(s: float) -> float:
    return s / (365.25 * 86400)


CSS = """
.rsa-answer{font-size:30px;font-weight:650;margin:6px 0 2px}
#rsa{margin:18px 0 8px;border:1px solid var(--line);border-radius:10px;background:#fff;padding:10px 12px}
#rsa-map{width:100%;height:46px;display:block;border-radius:4px;cursor:pointer;margin:0 0 8px}
.rsa-stage{position:relative}
#rsa-canvas{width:100%;height:min(62vh,560px);min-height:300px;display:block;border-radius:6px;cursor:grab;touch-action:none}
#rsa-detail{position:absolute;top:8px;right:8px;display:flex;background:#fff;border:1px solid var(--line);border-radius:7px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.12)}
#rsa-detail button{font:inherit;font-size:12.5px;border:0;background:none;padding:5px 9px;cursor:pointer;color:var(--ink2)}
#rsa-detail button+button{border-left:1px solid var(--line)}
#rsa-detail button.on{background:#1b2333;color:#fff}
.rsa-zoom{position:absolute;right:8px;bottom:8px;display:flex;flex-direction:column;background:#fff;border:1px solid var(--line);border-radius:7px;overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,.12)}
.rsa-zoom button{font:inherit;font-size:17px;width:34px;height:32px;border:0;background:none;cursor:pointer}
.rsa-zoom button+button{border-top:1px solid var(--line)}
#rsa-card{position:absolute;top:8px;left:8px;width:min(330px,calc(100% - 16px));max-height:calc(100% - 16px);overflow:auto;background:#fff;border:1px solid var(--line);border-radius:8px;box-shadow:0 2px 10px rgba(0,0,0,.18);padding:10px 12px;font-size:13px}
#rsa-card h3{font-size:15px;margin:0 22px 6px 0}
#rsa-card p{margin:4px 0;color:var(--ink2)}
#rsa-card .x{position:absolute;top:6px;right:8px;border:0;background:none;font-size:18px;cursor:pointer;color:var(--ink2)}
#rsa-card .acts{display:flex;flex-wrap:wrap;gap:6px;margin-top:8px}
#rsa-card .acts button,#rsa-card .acts a{font:inherit;font-size:12.5px;padding:4px 9px;border:1px solid var(--line);border-radius:6px;background:#f6f7f9;color:inherit;text-decoration:none;cursor:pointer}
#rsa-strip{width:100%;height:14px;display:block;margin:8px 0 2px;border-radius:3px;cursor:pointer}
.rsa-bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:8px 0 0;font-size:13px}
.rsa-bar input[type=range]{flex:1 1 220px}
.rsa-bar button,.rsa-bar select{font:inherit;font-size:13px;padding:3px 9px}
.rsa-read{font-size:12.5px;color:var(--ink2);margin:6px 0 0;min-height:1.3em}
.rsa-key{display:flex;flex-wrap:wrap;gap:4px 14px;font-size:12px;color:var(--ink2);margin:8px 0 0}
.rsa-key i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:-1px}
.rsa-progs{max-height:460px;overflow:auto;border:1px solid var(--line);border-radius:8px}
.rsa-progs table{margin:0;width:100%}
.rsa-progs th{position:sticky;top:0;background:#f6f7f9}
.rsa-progs td button,.rsa-progs td a{font:inherit;font-size:12px;padding:2px 7px;border:1px solid var(--line);border-radius:5px;background:#fff;color:inherit;text-decoration:none;cursor:pointer;white-space:nowrap}
@media (max-width:640px){#rsa-detail button{padding:5px 6px;font-size:11.5px}}
"""

HINTS = {"rsa-play": ("Play", "plays the prime on the machine, step after step on one clock"),
         "rsa-speed": ("Speed", "how much machine time passes each second; Auto slows the clock as you zoom in, "
                       "down to a millisecond a second when ions are drawn"),
         "rsa-time": ("Time in the prime", "moves the clock to any instant of the prime"),
         "rsa-part": ("Part", "moves the clock to the start of one part of the prime"),
         "rsa-strip": ("The prime's steps", "the parts of the prime along its time; a click moves the clock there"),
         "rsa-in": ("Zoom in", "shows a smaller part of the floorplan, larger, with more detail"),
         "rsa-out": ("Zoom out", "shows more of the floorplan, with less detail"),
         "rsa-fit": ("Busiest part", "shows the machine's full height at its busiest part, the crossbar"),
         "rsa-detail": ("Detail", "what the map draws: Auto follows the zoom; Machine, Patches, Gadgets or Ions "
                        "keeps one level, zooming in if that level needs it"),
         "rsa-canvas": ("The machine", "the floorplan: the wheel or a double-click zooms, a drag moves it, a click "
                        "opens a card for that tile (Ctrl-click opens its program's design page)"),
         "rsa-card": ("What is here", "the gadget at work on the tile you clicked, or what the tile holds; from "
                      "here, watch its ions, open its program's design page, or find the next one like it"),
         "rsa-level": ("The level shown", "which level of detail the map draws now, and how much of it"),
         "rsa-map": ("The whole machine", "all of the floorplan, the part shown below outlined; a click shows that part"),
         "rsa-programs": ("Every program", "each distinct compiled program the prime copies, its ions, its exact time, "
                          "how often a prime runs it and its check at d = 11"),
         "rsa-show": ("Show one", "moves the clock and the map to the next gadget of this program and draws its ions"),
         "rsa-page": ("Design page", "this program on the studio's stage: every ion, cycle by cycle, and every rule")}


def page(dst, gidney: dict | None = None) -> str:
    """The page's body from packed data in `dst`; `gidney`, if given, gadgets/rsa2048/gidney/'s {years, ions} (`rsa_gidney.summary`)."""
    meta = json.loads((Path(dst) / "meta.json").read_text(encoding="utf-8"))
    progs = json.loads((Path(dst) / "programs.json").read_text(encoding="utf-8"))
    prime = meta["prime_s"]
    rest = meta.get("rest")
    total = PRIMES * prime + (rest["s"] if rest else 0.0)
    merges = [p for p in progs if p["kind"] == "merge"]
    passed = sum(p["d11"] == "pass" for p in progs)
    use = meta["use"]
    e = html.escape
    rows = "".join(f"<tr><td>{e(p['name'])}</td><td>{p['steps']}</td><td>{p['span']:,.2f} s</td>"
                   f"<td>{p['span'] / p['steps']:,.2f} s</td></tr>" for p in meta["parts"])
    order = sorted(progs, key=lambda p: (p["kind"] != "idle", -p["uses"]))
    prow = "".join(
        f"<tr><td>{e(p['title'])}</td><td>{p['ions']:,}</td><td>{p['us'] / 1000:,.3f} ms</td>"
        f"<td>{'every held patch, every round' if p['kind'] == 'idle' else format(p['uses'], ',')}</td>"
        f"<td>{'pass' if p['d11'] == 'pass' else e(p['check'])}</td><td>"
        + ("" if p["kind"] == "idle" else f'<button data-show="{p["id"]}" data-hint="rsa-show">Show one</button> ')
        + (f'<a href="data/pages/{p["key"]}.html" target="_blank" rel="noopener" data-hint="rsa-page">Design page</a>'
           if p.get("page") else "")
        + "</td></tr>" for p in order)
    hints = json.dumps({k: {"t": a, "d": b} for k, (a, b) in HINTS.items()}, ensure_ascii=False)
    js = (WEB / "rsa.js").read_text(encoding="utf-8")
    key = "".join(f'<span><i style="background:{c}"></i>{n}</span>' for n, c in
                  (("merge", "#2e6fd8"), ("preparation", "#12a150"), ("readout", "#e08a00"), ("H̄", "#8b3fd0"),
                   ("held patch", "#8d96a8"), *ion_key(), ("factory", "#e7e9ee"), ("dlog", "#7cc4a4"), ("help", "#b39ddb"),
                   ("acc", "#f4a7a7"), ("res", "#f2cf86"), ("pp", "#8fb3e8"), ("addr", "#5b7fc7"),
                   ("exponent", "#d6dbe4")))
    idle = next((p for p in progs if p["kind"] == "idle"), None)
    theirs = ("Gidney's own compilation of the same program, as FormalRV's listing writes it (every product a lattice-surgery "
              "merge, magic states by cultivation, the exponent in yoked cold storage), has <a href='gidney/'>its own page</a>: "
              f"{gidney['years']:.2f} years on {gidney['ions'] / 1e6:.2f} million ions." if gidney else
              "Gidney's own compilation of the same program (cold yoked storage, six factories fed by magic state cultivation) is "
              "the next run; until it exists the two are not compared like for like.")
    unchecked = len(progs) - passed
    speeds = "".join(f'<option value="{v}"{" selected" if v == "auto" else ""}>{n}</option>' for v, n in
                     (("auto", "Auto"), ("0.0001", "0.1 ms a second"), ("0.001", "1 ms a second"),
                      ("0.01", "10 ms a second"), ("0.1", "0.1 s a second"), ("1", "1 s a second"),
                      ("10", "10 s a second"), ("100", "100 s a second")))
    return f"""
<p class="sub"><a href="../">Gadgets</a> &rsaquo; RSA-2048 at d = 11</p>
<h1>Factoring RSA-2048 at d = 11</h1>
<p class="rsa-answer">{_years(total):.2f} years</p>
<p>of machine time for Gidney's 2025 algorithm, as FormalRV writes it, on the stationary-tile QCCD
machine at code distance 11, with <b>{use['ions'] / 1e6:.1f} million ions</b> on {use['tiles']:,} tiles and
{meta['factories']} CCZ factories. One prime is scheduled and checked whole -- {len(meta['steps'])} steps,
{prime:,.2f} s, <b>concrete</b>; the program is that prime {PRIMES:,} times, back to back (every prime has
the same schedule, so this is <b>exact</b>, not an estimate){f", plus {rest['s'] / 3600:.2f} h of work done once ({rest['steps']:,} steps, concrete)" if rest else ""}.</p>
<p class="muted">The compilation is ours, not the paper's: our floorplan, our {meta['factories']} distilling factories, our
corridors. {theirs}</p>
<div class="gstat">
<div><b>{prime / 3600:.2f} h</b>one prime</div>
<div><b>{PRIMES:,}</b>primes</div>
<div><b>{use['ions'] / 1e6:.1f} million</b>ions</div>
<div><b>{meta['gadgets'] / 1e6:.0f} million</b>gadgets in a prime</div>
<div><b>{passed} of {len(progs)}</b>programs checked at d = 11</div>
</div>

<div id="rsa">
  <canvas id="rsa-map" data-hint="rsa-map" aria-label="the whole machine"></canvas>
  <div class="rsa-stage">
    <canvas id="rsa-canvas" data-hint="rsa-canvas" aria-label="the machine's floorplan"></canvas>
    <div id="rsa-detail" data-hint="rsa-detail" role="group" aria-label="level of detail">
      <button data-level="auto">Auto</button><button data-level="machine">Machine</button><button
      data-level="patches">Patches</button><button data-level="gadgets">Gadgets</button><button data-level="ions">Ions</button>
    </div>
    <div class="rsa-zoom"><button id="rsa-in" data-hint="rsa-in" title="zoom in">+</button><button
      id="rsa-out" data-hint="rsa-out" title="zoom out">&minus;</button></div>
    <div id="rsa-card" data-hint="rsa-card" hidden></div>
  </div>
  <canvas id="rsa-strip" data-hint="rsa-strip" aria-label="the prime's steps along its time"></canvas>
  <div class="rsa-bar">
    <button id="rsa-play" data-hint="rsa-play">Play</button>
    <select id="rsa-speed" data-hint="rsa-speed" aria-label="speed">{speeds}</select>
    <input id="rsa-time" data-hint="rsa-time" type="range" min="0" step="0.000001" value="0" aria-label="time in the prime">
    <select id="rsa-part" data-hint="rsa-part" aria-label="part of the prime"><option value="" disabled selected>Go to a part</option></select>
    <button id="rsa-fit" data-hint="rsa-fit">Busiest part</button>
  </div>
  <p class="rsa-read" id="rsa-level" data-hint="rsa-level"></p>
  <p class="rsa-read" id="rsa-step"></p>
  <p class="rsa-read" id="rsa-clock"></p>
  <div class="rsa-key">{key}</div>
</div>
<p class="muted">Zoom as on a map -- the wheel, a double-click, + and &minus; -- and the map draws more as it
grows: the machine, then its patches, then each gadget named, then every trap and ion. Click anything for
what it is; the address keeps the place and the moment, to share. The player fetches each step as the clock
nears it and lets it go once the clock has passed, so the browser holds a few steps at a time, and a
program's ions only once one of its gadgets is in view.</p>

<h2>The ions</h2>
<p>The program touches {use['tiles']:,} tiles{" (one prime and the work done once)" if use.get("with_rest") else " (one prime)"};
every trap of each holds an ion, {meta['ions_per_tile']} a tile ({meta['ions_per_tile'] // 2} data traps and
{meta['ions_per_tile'] // 2} measure traps; a patch uses 241 of them, its seams the rest):</p>
<table><tr><th>tiles</th><th>what they are</th><th>ions</th></tr>
<tr><td>{use['homes']:,}</td><td>the registers' homes: a logical qubit each</td><td>{use['homes'] * 288 / 1e6:.2f} million</td></tr>
<tr><td>{use['factories']:,}</td><td>inside the {meta['factories']} CCZ factories</td><td>{use['factories'] * 288 / 1e6:.2f} million</td></tr>
<tr><td>{use['routing']:,}</td><td>bus tiles: the corridors, relays and seats the merges cross</td><td>{use['routing'] * 288 / 1e6:.2f} million</td></tr>
<tr><td><b>{use['tiles']:,}</b></td><td><b>in all</b></td><td><b>{use['ions'] / 1e6:.2f} million</b></td></tr></table>
<p class="muted">The floorplan's rectangle is {use['floorplan']:,} tiles; the other {use['floorplan'] - use['tiles']:,}
are never touched and are not counted. Gidney's paper machine is 897,864 qubits: a logical qubit costs it
1,352 qubits hot (d = 25) or 430 in yoked cold storage, against our 241 ions; the difference is the
factories (his six are fed by magic state cultivation; ours distil, {meta['factories']} at once to keep up
with a 5.8 ms round) and the bus our long corridors cross. The floorplan was laid out for time, not for
ions.</p>

<h2>One prime</h2>
<table><tr><th>part</th><th>steps</th><th>time</th><th>a step</th></tr>{rows}
<tr><td><b>the prime</b></td><td>{len(meta['steps'])}</td><td><b>{prime:,.2f} s</b></td><td></td></tr></table>

<h2>Every program</h2>
<p>A gadget is a translated copy of one compiled program, so these {len(progs)} programs are all the machine
ever runs in a prime{f", and every one passes at d = 11" if not unchecked else f" ({unchecked} not yet checked at d = 11)"}.
"Show one" takes the map to the next copy and draws its ions; a design page is the program on the studio's
stage, cycle by cycle.</p>
<div class="rsa-progs" data-hint="rsa-programs"><table><tr><th>program</th><th>ions</th><th>its time</th>
<th>copies in a prime</th><th>d = 11</th><th></th></tr>{prow}</table></div>

<h2>What the number rests on</h2>
<ul class="steps">
<li>Every step is a schedule of compiled programs on the floorplan, checked: no two gadgets on a tile at
once, every block's instructions in program order, every guard decoded before it is read. Steps run
end to end, each from the board at rest.</li>
<li>Each distinct program -- {len(merges)} merges, and the H̄, the preparations, the readouts and the round a
held patch runs -- is compiled for the machine at d = 11 and checked against every rule and R10.</li>
<li>Every time is its compiled program's own, to the microsecond: a merge 138.340 ms (25 rounds), H̄
679.832 ms, |Ȳ> 43.862 ms, a held patch's round {idle['us'] / 1000 if idle else 5.822:.3f} ms; a preparation
is given the slowest one's time (0.345 ms) and a readout the slower basis's (0.440 ms). Decoding is taken to
take one round (assumed). No gate is reordered and FormalRV's program is not changed.</li>
<li>Drawn as checked: a merge's program ends by reading every data ion, the patches' too, so the check can
test its result; in the run the patches go on and only the corridor is read, in the same one readout cycle.
|T> is drawn and checked as its Clifford twin (the corner's T an S: the same pulses, one angle). A held
patch runs its round on its own tile, off any common clock (assumed).</li>
<li>Not built: the phase gradient's 32 rotations exp(iπZ/2<sup>k</sup>), k = 1 .. 32. The first three the
machine could do (a frame, a |Ȳ>, a T); the 29 from k = 4 need a resource state no gadget makes yet.
They run once, for seconds.</li>
</ul>
<script>window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {{}}, {hints});</script>
{theme_script()}
<script>{js}</script>
"""


def build(base, put, data, gidney: dict | None = None) -> None:
    """gadgets/rsa2048/ under the site section `base`, its data copied from `data` (`pack`)."""
    from ..site.build import PAGE, STYLE
    from .site import EXTRA_CSS, SECTION
    where = Path(base) / KEY
    if (where / "data").exists():
        shutil.rmtree(where / "data")
    shutil.copytree(data, where / "data")
    put(f"{SECTION}/{KEY}/index.html",
        PAGE.format(title="Factoring RSA-2048 at d = 11 - QCCD studio", style=STYLE,
                    extra_css=EXTRA_CSS + CSS, body=page(data, gidney)), 2, SECTION)


def standalone(out, data, index_json: str, gidney: dict | None = None) -> Path:
    """Only gadgets/rsa2048/ (and its data) into the site directory `out`, wrapped as the site
    wraps its pages: for a deploy that rebuilds nothing else."""
    from ..site.build import FOOTER, with_nav
    from .site import SECTION
    idx = index_json.replace("</", "<\\/")

    def put(rel, page_, depth, active, app=False, extra=""):
        p = Path(out) / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        page_ = with_nav(page_, depth, active, idx, app=app, extra=extra)
        p.write_text(page_.replace("__FOOTER__", FOOTER).replace("__SITEROOT__", "../" * depth),
                     encoding="utf-8", newline="")
    build(Path(out) / SECTION, put, data, gidney)
    return Path(out) / SECTION / KEY / "index.html"


def index_entry() -> dict:
    return {"t": "Factoring RSA-2048 at d = 11", "d": "how long Gidney 2025's program takes on the "
            "stationary-tile QCCD machine at d = 11 in our own compilation, and on how many ions: one prime "
            "scheduled and checked whole, played step by step from the whole machine down to every ion",
            "u": f"gadgets/{KEY}/", "k": "gadgets"}
