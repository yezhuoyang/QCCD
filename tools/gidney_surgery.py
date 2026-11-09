"""FormalRV's Gidney 2025 by lattice surgery on the ion machine (docs/FULLSTACK_PLAN.md, S10; the user's call of 2026-11-06).

Reads the listing `tools/gidney_concrete.py` read, from OUT (`out/s10`), and works in OUT/surgery.

    gidney_surgery.py labels OUT                      which way round each wire holds its state: labels.json
    gidney_surgery.py shapes OUT                      every program the plan needs, with uses: shapes.json
    gidney_surgery.py compile OUT [WORKERS] [GLOB]    each built once, every rule, R10, its waveforms, its logic: programs/
    gidney_surgery.py gadgets OUT                     the programs that are not merges: the cultivated |T>, Ȳ read and made in place
    gidney_surgery.py run OUT                         one prime and the program on those programs: concrete.json
    gidney_surgery.py ions OUT [WORKERS] [--full]     every program the schedule places, its ions for the page: page/ions/
    gidney_surgery.py page OUT [WINDOW_S]             one prime's schedule as the page plays it, window by window: page/data/

Each program is written when it is checked, and a rerun builds only what is missing.
"""
from __future__ import annotations

import ast
import fnmatch
import hashlib
import json
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))     # R10's helper lives with its tests
sys.setrecursionlimit(100000)

#: the floorplan: `SurgeryPlan`'s knobs.  Bands of four rows, the road and a cultivated factory's slot under it, slots eleven tiles apart, a
#: frame's factories taking four of them in turn (docs/FULLSTACK_PLAN.md, S10 step 4)
FLOOR = {"pitch": 3, "band": 4, "inner": 1, "gap": 0, "spread": 4, "period": 11}
#: the exponent in yoked cold storage: 1,280 logical qubits at 73 ions each, workspace counted (`qccd.fullstack.scale.yoked`)
COLD_IONS = 1280 * 73


def _dir(out: Path) -> Path:
    d = out / "surgery"
    d.mkdir(exist_ok=True)
    (d / "programs").mkdir(exist_ok=True)
    return d


def _name(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _reader(out: Path):
    import gidney_concrete as GC
    return GC._reader(out)


def _plan(out: Path, R):
    from qccd.fullstack.scale.gidney_surgery import SurgeryPlan
    rows = json.loads((_dir(out) / "labels.json").read_text(encoding="utf-8"))
    return SurgeryPlan(R, {int(k): v for k, v in rows["labels"].items()}, **FLOOR)


def labels(out: Path) -> None:
    from qccd.fullstack.scale.gidney_surgery import labels as solve
    lst, R = _reader(out)
    t0 = time.time()
    got, info = solve(R)
    (_dir(out) / "labels.json").write_text(json.dumps({"labels": {str(k): v for k, v in got.items()}, "info": info}), encoding="utf-8")
    print(f"{sum(got.values())} of {len(got)} wires turned; {len(info['hard'])} products no labelling makes one way round; "
          f"{info['broken']:,} of {info['edges']:,} wire pairs through a request's patch ask for H̄; {time.time() - t0:.0f} s")


def shapes(out: Path) -> None:
    lst, R = _reader(out)
    plan = _plan(out, R)
    t0 = time.time()
    got = plan.merges(None)
    rows = {repr(s): n for s, n in got.items()}
    (_dir(out) / "shapes.json").write_text(json.dumps(rows, indent=0), encoding="utf-8")
    print(f"{plan.width} x {plan.height} = {plan.width * plan.height} tiles; {len(rows)} programs, {sum(rows.values()):,} uses; "
          f"longest corridor {max(len(s[3]) for s in got)} tiles; {time.time() - t0:.0f} s")
    for s, n in got.most_common(16):
        print(f"{n:>14,}  {s}")


def _check_one(args) -> str:
    text, out = args
    from qccd.fullstack.scale import layer as L
    from qccd.fullstack.scale import programs as PR
    from qccd.qec.experiments import corridor_merge, ideal_stim
    f = Path(out) / "programs" / f"{_name(text)}.json"
    t0 = time.time()
    try:
        _, letter, pats, cor = ast.literal_eval(text)
        group = (letter, [(tuple(t), s) for t, s in pats], {tuple(c) for c in cor})
        # its logic, at d = 3: the product and every patch's own logical deterministic, the circuit at full distance
        e = corridor_merge(3, *group)
        c = ideal_stim(e)
        c.detector_error_model()
        if c.compile_detector_sampler().sample(16, append_observables=True).any():
            raise ValueError("a detector or an observable is not deterministic at d = 3")
        distance = len(ideal_stim(e, 1e-3).shortest_graphlike_error(ignore_ungraphlike_errors=True))
        b = L.build([group])
        same = L.waveforms(b) == L.waveforms(L.build([L.facing(0)]))
        with tempfile.TemporaryDirectory() as tmp:
            got = PR.check(b, tmp, L.round_ops(b))
        row = {"shape": text, "us": got["us"], "failed": got["failed"], "r10_blocks": got["r10_blocks"], "ions": got["ions"],
               "distance_at_3": distance, "same_waveforms": same, "s": round(time.time() - t0, 1)}
        f.write_text(json.dumps(row), encoding="utf-8")
        return f"{row['us']:>9.0f} us  failed {row['failed']}  r10 {row['r10_blocks']}  d3 {distance}  same {same}  {row['ions']:>6} ions  {row['s']:>6.0f} s  {text}"
    except Exception as exc:        # a shape stays unbuilt, listed, and the run refuses
        return f"FAILED {text}: {exc!r}"


def gadgets(out: Path) -> None:
    """The programs the schedule uses that are not merges, each compiled and checked as the merges are (every rule, R10), its time the one the plan
    charges: the cultivated |T> (`cultivation`: noiseless, every detector deterministic), Ȳ read in place, |Ȳ> made in place."""
    import stim

    from qccd.fullstack.scale import cultivation as C
    from qccd.fullstack.scale import gidney_surgery as GS
    from qccd.fullstack.scale import programs as PR
    d = _dir(out)
    text = (Path(__file__).resolve().parents[1] / "qccd/qec/cultivation/end2end_d1=3_d2=11_r1=1_r2=1.stim").read_text(encoding="utf-8")
    t0 = time.time()
    r = C.read(C.to_surface(text, 11, 2), name="cultivated_T")
    prog, cert, arch = C.build(r)
    e = r.experiment
    b = PR.Built(("T",), e.qasm, r.rows, r.cols, arch, prog, cert, e.n_data + e.n_ancilla)
    circuit, _ = C.noisy(r, PR.cooled(b), arch)
    ideal = C.without_noise(str(circuit))
    deterministic = not ideal.compile_detector_sampler().sample(256, append_observables=True).any()
    with tempfile.TemporaryDirectory() as tmp:
        got = PR.check(b, tmp)
    rows = {"T": {**got, "deterministic": deterministic, "plan_us": GS.T_US, "s": round(time.time() - t0, 1)}}
    for shape, want in ((("read", "Y"), GS.READ_Y_US), (("prep", "Y"), GS.PREP_Y_US)):
        t0 = time.time()
        b = PR.build(shape)
        with tempfile.TemporaryDirectory() as tmp:
            got = PR.check(b, tmp)
        rows[" ".join(shape)] = {**got, "plan_us": want, "s": round(time.time() - t0, 1)}
    (d / "gadgets.json").write_text(json.dumps(rows, indent=1), encoding="utf-8")
    for k, v in rows.items():
        print(f"{k:>7}: {v['us']:>9.0f} us (the plan charges {v['plan_us']:.0f})  failed {v['failed']}  r10 {v['r10_blocks']}  {v['ions']} ions"
              + (f"  deterministic {v['deterministic']}" if "deterministic" in v else ""))


#: the programs the schedule places that are not merges, as `programs.build` names them; ("T",) is the cultivated |T>
OTHERS = (("prep", "Z"), ("prep", "X"), ("read", "Z"), ("read", "X"), ("read", "Y"), ("prep", "Y"), ("idle",), ("T",))


def program_key(shape) -> str:
    """A program's file name on the page: the first 12 hex digits of its shape's JSON's SHA-1 (`tools/lattice_programs.py`'s)."""
    return hashlib.sha1(json.dumps(shape).encode()).hexdigest()[:12]


def _built(shape):
    """The program a shape names, built at d = 11: a merge on the layer (`layer.build`), the cultivated |T>, or `programs.build`'s."""
    from qccd.fullstack.scale import layer as L
    from qccd.fullstack.scale import programs as PR
    if shape[0] == "merge":
        _, letter, pats, cor = shape
        return L.build([(letter, [(tuple(t), side) for t, side in pats], {tuple(c) for c in cor})])
    if shape == ("T",):
        from qccd.fullstack.scale import cultivation as C
        text = (Path(__file__).resolve().parents[1] / "qccd/qec/cultivation/end2end_d1=3_d2=11_r1=1_r2=1.stim").read_text(encoding="utf-8")
        r = C.read(C.to_surface(text, 11, 2), name="cultivated_T")
        prog, cert, arch = C.build(r)
        e = r.experiment
        return PR.Built(("T",), e.qasm, r.rows, r.cols, arch, prog, cert, e.n_data + e.n_ancilla)
    return PR.build(shape)


#: the merge whose cooling and cycle times every merge shares (`_like_reference`)
REFERENCE = ("merge", "X", (((0, 0), "right"), ((1, 0), "left")), ())
_REF: dict = {}


def _signature(prog) -> list:
    return [(i.id, i.type, getattr(i, "cls", None)) for i in prog.instructions]


def _like_reference(b):
    """A merge's ions without cooling or replaying it: every merge program has the same cycles on one clock (`compile` checks its waveforms
    against one merge's), so where the cooling pass puts its cooling and when each cycle runs are the reference merge's.  Its own instructions
    in the reference's cooled order, at the reference's times; None unless its instructions are the reference's one for one (id, type, class).
    `ions` checks it byte for byte against the full path before it relies on it."""
    from qccd.fullstack.scale import programs as PR
    from qccd.gadget.rsa import ion_bytes
    from qccd.verify.replay import replay
    if not _REF:
        rb = _built(REFERENCE)
        cool = PR.cooled(rb)
        _REF.update(sig=_signature(rb.prog), cool=cool, cycles=replay(cool, rb.arch, PR._model(), check_rules=False).cycles)
    if _signature(b.prog) != _REF["sig"]:
        return None
    mine = {i.id: i for i in b.prog.instructions}

    class Like:
        instructions = [mine.get(i.id, i) for i in _REF["cool"].instructions]
    return ion_bytes(b.arch, Like, _REF["cycles"], distinct=True)


def _full(b) -> bytes:
    """A program's ions the long way: cooled (a replay with R7) and replayed; the smaller of QIO1 and QIO2."""
    from qccd.fullstack.scale import programs as PR
    from qccd.gadget.rsa import ion_bytes
    from qccd.verify.replay import replay
    prog = PR.cooled(b)
    res = replay(prog, b.arch, PR._model(), check_rules=False)
    return min((ion_bytes(b.arch, prog, res.cycles, distinct=q) for q in (False, True)), key=len)


def _ions_one(args) -> str:
    """One program's ions written for the page (no rules: `compile` checked it): a merge like the reference's (`_like_reference`) if `fast`,
    else cooled and replayed."""
    import gzip
    text, page, fast = args
    shape = ast.literal_eval(text)
    key = program_key(shape)
    t0 = time.time()
    try:
        b = _built(shape)
        raw = _like_reference(b) if fast and shape[0] == "merge" else None
        how = "like the reference" if raw is not None else "cooled and replayed"
        raw = raw if raw is not None else _full(b)
        us = struct_us(raw)
        gz = gzip.compress(raw, 9, mtime=0)
        (Path(page) / "ions" / f"{key}.bin.gz").write_bytes(gz)
        row = {"key": key, "shape": shape, "us": us, "ions": b.ions, "instructions": len(b.prog.instructions), "format": raw[:4].decode(),
               "bytes": len(gz), "how": how, "s": round(time.time() - t0, 1)}
        (Path(page) / "programs" / f"{key}.json").write_text(json.dumps(row), encoding="utf-8")
        return f"{row['us']:>9.0f} us  {row['ions']:>6} ions  {row['format']} {row['bytes'] / 1e6:6.2f} MB  {row['s']:>5.0f} s  {how:>19}  {text}"
    except Exception as exc:
        return f"FAILED {text}: {exc!r}"


def struct_us(raw: bytes) -> float:
    """An ion file's program time, µs (its header)."""
    import struct
    return struct.unpack_from("<d", raw, 12)[0]


def ions(out: Path, workers: int, fast: bool = True) -> None:
    """Every program the schedule places -- each merge of shapes.json and `OTHERS` -- its ions in OUT/surgery/page/ions/KEY.bin.gz and its row in
    page/programs/KEY.json; the smallest first, and a rerun makes only what is missing.  `fast`: a merge like the reference's
    (`_like_reference`), once the smallest, the median and the largest merge have come out byte for byte as the full path makes them."""
    import multiprocessing as mp
    d = _dir(out)
    page = d / "page"
    for sub in ("ions", "programs"):
        (page / sub).mkdir(parents=True, exist_ok=True)
    rows = json.loads((d / "shapes.json").read_text(encoding="utf-8"))
    size = {}
    for k in rows:
        f = d / "programs" / f"{_name(k)}.json"
        size[k] = json.loads(f.read_text(encoding="utf-8"))["ions"] if f.exists() else 10 ** 9
    merges = sorted(rows, key=lambda k: size[k])
    if fast:
        for k in (merges[0], merges[len(merges) // 2], merges[-1]):
            b = _built(ast.literal_eval(k))
            if _like_reference(b) != _full(b):
                sys.exit(f"a merge's ions are not its reference's cooling and times with its own instructions: {k}; run with --full")
        print("the smallest, the median and the largest merge: the reference's cooling and times give their ions byte for byte", flush=True)
    todo = [repr(s) for s in OTHERS] + merges
    todo = [k for k in todo if not (page / "programs" / f"{program_key(ast.literal_eval(k))}.json").exists()]
    print(f"{len(todo)} programs to write, {workers} workers", flush=True)
    with mp.Pool(workers) as pool:
        for line in pool.imap_unordered(_ions_one, [(k, str(page), fast) for k in todo]):
            print(line, flush=True)


#: the prime's parts, in program order, and the colour each is drawn in on the clock's strip (`qccd.gadget.rsa.PARTS`)
_KIND_PROGRAM = {"|T>": ("T",), "|Y+>": ("prep", "Y"), "h": ("h",), "cold": ("cold",)}
_HOLDS = {"prep", "|T>", "|Y+>", "h", "cold"}


def page_data(out: Path, window_s: float = 120.0) -> None:
    """The page's data (`qccd.gadget.rsa_gidney`) in OUT/surgery/page/data: one prime's flat schedule, traced item by item as `Plan.flat`
    places it (the same schedule `run` times), cut into windows of `window_s`: per window its gadgets (frames/NNN.bin.gz, the records of
    `qccd.gadget.rsa`: uint32 start µs, uint16 program, uint8 tiles, the tiles) with those still at work as it starts (its `lead`), and the
    tiles holding a block at its frame's first moment (live/NNN.bin.gz); meta.json, programs.json (every program, its exact time, its check, its
    ion file from `ions`) and layout.json.  A patch's syndrome rounds between gadgets are not drawn as gadgets: the page runs the held patch's
    round under every held tile, as it does for S9."""
    import array
    import gzip
    import math
    import shutil

    import numpy as np

    import gidney_concrete as GC
    from qccd.fullstack.scale import programs as PR
    from qccd.fullstack.scale.gidney_plan import strips
    from qccd.fullstack.scale.gidney_surgery import SLOT_ROWS, SLOT_WIDTH
    from qccd.gadget import rsa
    d = _dir(out)
    page = d / "page"
    data = page / "data"
    for sub in ("frames", "live", "ions"):
        if (data / sub).exists():
            shutil.rmtree(data / sub)
        (data / sub).mkdir(parents=True)
    lst, R = _reader(out)
    plan = _plan(out, R)
    key = GC._keys(lst, R)
    t_start = time.time()

    held: dict = {}

    def patch_tiles(k) -> dict:
        """Where each of an instance's patches sits: the tile its preparation, its factory or its release names (one per key: `_resolve` is
        memoised)."""
        got = held.get(k)
        if got is None:
            got = held[k] = {}
            for it in plan._resolve(k):
                if it[0] == "work" and it[4] in ("prep", "|AutoCCZ>"):
                    pats = [r for r in it[2] if r[0] == "patch"]
                    tiles = [r[1] for r in it[2] if r[0] == "tile"]
                    got.update(zip(pats, tiles))
                elif it[0] == "free":
                    got.setdefault(it[2], it[1][1])
        return got

    ids: dict = {}
    order: list = []                    # (program, µs, kind)
    starts, ends = array.array("d"), array.array("d")
    pid, part = array.array("i"), array.array("b")
    toff, tiles, holds = array.array("q", [0]), array.array("h"), array.array("b")
    parts: dict = {}

    def trace(path, it, t, e):
        kind = it[4]
        if kind in ("rounds", "|AutoCCZ>") or e <= t:
            return
        if kind == "merge":
            shape, (cx, cy) = it[5], it[6]
            ts = [(x + cx, y + cy) for (x, y), _ in shape[2]] + [(x + cx, y + cy) for x, y in shape[3]]
            after = [1] * len(shape[2]) + [0] * len(shape[3])
            prog = shape
        else:
            ts = []
            pt = None
            for r in it[2]:
                if r[0] == "tile":
                    ts.append(r[1])
                elif r[0] == "wire":
                    ts.append(plan.tile[r[1]])
                elif r[0] == "patch":
                    pt = patch_tiles(path[-1]) if pt is None else pt
                    if r in pt:
                        ts.append(pt[r])
            ts = list(dict.fromkeys(ts))
            after = [0 if kind == "read" else 1] * len(ts)
            extra = it[5] if len(it) > 5 else None
            prog = _KIND_PROGRAM.get(kind) or (tuple(extra) if extra else (kind, "?"))
            if prog == ("prep", "T"):
                prog = ("T",)
        if not ts:
            return
        k = (prog, round(e - t, 3))
        i = ids.get(k)
        if i is None:
            i = ids[k] = len(order)
            order.append((prog, e - t, kind))
        starts.append(t)
        ends.append(e)
        pid.append(i)
        part.append(parts.setdefault(path[1][0] if len(path) > 1 else path[0][0], len(parts)))
        for (x, y), a in zip(ts, after):
            tiles.append(x)
            tiles.append(y)
            holds.append(a)
        toff.append(len(holds))

    end = plan.flat(key, {}, {}, trace=trace)[0]
    n = len(starts)
    print(f"one prime {end / 3.6e9:.2f} h, {n:,} gadgets, {len(order)} programs, traced in {time.time() - t_start:.0f} s", flush=True)
    st, en = np.frombuffer(starts, np.float64), np.frombuffer(ends, np.float64)
    pi, pa = np.frombuffer(pid, np.int32), np.frombuffer(part, np.int8)
    off = np.frombuffer(toff, np.int64)
    tl = np.frombuffer(tiles, np.int16).reshape(-1, 2)
    ho = np.frombuffer(holds, np.int8)
    srt = np.argsort(st, kind="stable")
    W, H = plan.width, plan.height
    homes = np.zeros(W * H, bool)
    for t_ in plan.wire_tiles:
        homes[t_[1] * W + t_[0]] = True
    live = homes.copy()
    longest = float((en - st).max())
    n_win = max(1, math.ceil(end / 1e6 / window_s))
    ss = st[srt]
    steps, applied = [], 0
    uses = np.bincount(pi, minlength=len(order))
    seen: list = [[] for _ in order]
    for w in range(n_win):
        t0, t1 = w * window_s * 1e6, min((w + 1) * window_s * 1e6, end)
        # those still at work as the window starts: its lead goes back to the first of them
        a = int(np.searchsorted(ss, t0 - longest, "left"))
        b = int(np.searchsorted(ss, t0, "left"))
        running = srt[a:b][en[srt[a:b]] > t0]
        o = float(st[running].min()) if len(running) else t0
        lo = int(np.searchsorted(ss, o, "left"))
        hi = int(np.searchsorted(ss, t1, "left"))
        # the tiles holding a block at the frame's first moment: every gadget begun before it is done by the window's start
        js = srt[applied:lo]
        if len(js):
            ln = off[js + 1] - off[js]
            at = np.repeat(off[js], ln) + (np.arange(int(ln.sum())) - np.repeat(np.cumsum(ln) - ln, ln))
            tid = (tl[at, 1].astype(np.int64) * W + tl[at, 0])[::-1]
            u, first = np.unique(tid, return_index=True)        # the last gadget on each tile decides it
            live[u] = ho[at][::-1][first].astype(bool)
        applied = lo
        (data / "live" / f"{w:03d}.bin.gz").write_bytes(gzip.compress(np.packbits(live, bitorder="little").tobytes(), 9, mtime=0))
        local: dict = {}
        buf = bytearray()
        for j in srt[lo:hi]:
            g = int(pi[j])
            if g not in local:
                local[g] = len(local)
            k = int(off[j + 1] - off[j])
            assert k < 256, k
            buf += int(round(st[j] - o)).to_bytes(4, "little") + local[g].to_bytes(2, "little") + bytes([k])
            buf += tl[off[j]:off[j + 1]].astype("<i2").tobytes()
        (data / "frames" / f"{w:03d}.bin.gz").write_bytes(gzip.compress(bytes(buf), 9, mtime=0))
        mine = srt[b:hi] if t0 > 0 else srt[lo:hi]
        counts = np.bincount(pa[mine], minlength=len(parts)) if len(mine) else np.zeros(len(parts), int)
        name = list(parts)[int(counts.argmax())] if len(mine) else (steps[-1]["part"] if steps else "")
        for g in set(int(v) for v in pi[mine]):
            seen[g].append(w)
        steps.append({"label": name, "part": name, "t0": round(t0 / 1e6, 6), "span": round((t1 - t0) / 1e6, 6), "lead": int(round(t0 - o)),
                      "gadgets": int(len(mine)), "programs": list(local), "check": "clean"})
    # each part's windows, numbered: "gLoop1, window 3 of 120"
    by_part = Counter(s_["part"] for s_ in steps)
    k_part: Counter = Counter()
    for s_ in steps:
        k_part[s_["part"]] += 1
        s_["label"] = f"{s_['part']}, window {k_part[s_['part']]} of {by_part[s_['part']]}"
    colors = dict(rsa.PARTS)
    meta_parts = []
    for name in parts:
        mine = [s_ for s_ in steps if s_["part"] == name]
        if mine:
            meta_parts.append({"name": name, "color": colors.get(name, "#c8ccd6"), "steps": len(mine), "t0": mine[0]["t0"],
                               "span": round(sum(s_["span"] for s_ in mine), 6)})
    # the programs: each one's exact time, ions and check, its ion file if `ions` made it
    checked = json.loads((d / "gadgets.json").read_text(encoding="utf-8"))
    small = {}
    for shape in (("prep", "Z"), ("prep", "X"), ("read", "Z"), ("read", "X"), ("idle",)):
        with tempfile.TemporaryDirectory() as tmp:
            small[shape] = PR.check(PR.build(shape), tmp)
    progs = []
    for i, (prog, dur, kind) in enumerate(order + [(("idle",), 5822.0, "idle")]):
        k = program_key(prog) if prog[0] not in ("h", "cold") and "?" not in prog else ""
        row = json.loads((page / "programs" / f"{k}.json").read_text(encoding="utf-8")) if k and (page / "programs" / f"{k}.json").exists() else {}
        if k and (page / "ions" / f"{k}.bin.gz").exists():
            shutil.copyfile(page / "ions" / f"{k}.bin.gz", data / "ions" / f"{k}.bin.gz")
        elif k:
            k = ""
        if prog[0] == "merge":
            c = json.loads((d / "programs" / f"{_name(repr(prog))}.json").read_text(encoding="utf-8"))
            ok = not c["failed"] and c["same_waveforms"] and c["distance_at_3"] == 3
            check = ("checked at d = 11: every rule and R10, its logic at d = 3 and the one clock's waveforms" if ok
                     else f"at d = 11 it FAILS {c['failed']}")
            d11, ions_n, us = ("pass" if ok else "fail"), c["ions"], c["us"]
        elif prog in small:
            c = small[prog]
            d11, ions_n, us = ("pass" if not c["failed"] else "fail"), c["ions"], c["us"]
            check = "checked at d = 11: every rule and R10" if not c["failed"] else f"at d = 11 it FAILS {c['failed']}"
        elif prog in (("T",), ("read", "Y"), ("prep", "Y")):
            c = checked[{"T": "T", "read": "read Y", "prep": "prep Y"}[prog[0]]]
            d11, ions_n, us = ("pass" if not c["failed"] else "fail"), c["ions"], c["us"]
            check = ("checked at d = 11: every rule and R10" + (", every detector deterministic" if prog == ("T",) else "")
                     if not c["failed"] else f"at d = 11 it FAILS {c['failed']}")
        elif prog == ("h",):
            d11, ions_n, us = "pulse", 121, dur
            check = "one H pulse on each of the patch's 121 data ions, which turns the patch's labels: not a program of its own"
        elif prog == ("cold",):
            d11, ions_n, us = "composed", 0, dur
            check = ("composed: a cold access, counted as a merge at a hot round with the stored block held; not compiled, its corridor "
                     "not routed")
        else:
            d11, ions_n, us = "unchecked", 0, dur
            check = "not checked"
        progs.append({"id": i, "kind": kind, "shape": prog, "dur_us": int(round(dur)), "us": us, "ions": ions_n,
                      "instructions": row.get("instructions", 0), "key": k, "title": rsa_title(prog), "d11": d11, "check": check,
                      "uses": int(uses[i]) if i < len(order) else 0, "steps": seen[i] if i < len(order) else [], "page": False})
    # the floorplan
    regs, names, colors_ = {}, {}, {}
    palette = ["#8fb3e8", "#7cc4a4", "#f4a7a7", "#b39ddb", "#f2cf86", "#5b7fc7", "#9fd8bd", "#c3a5f0", "#e6a57e"]
    for r, (base, bits, stride) in enumerate(strips()):
        ws = [base + stride * b_ + k_ for b_ in range(bits) for k_ in range(stride)]
        regs[f"r{r}"] = [list(plan.tile[w_]) for w_ in ws]
        names[f"r{r}"] = f"wires {base}-{base + bits * stride - 1}"
        colors_[f"r{r}"] = palette[r % len(palette)]
    factories = []
    for band, row in sorted(plan.slots.items()):
        tag = f"F{band[0]}"
        names[tag] = "a factory slot"
        for x0, y0 in row:
            factories.append([tag, x0, y0, x0 + SLOT_WIDTH - 1, y0 + SLOT_ROWS - 1])
    layout = {"bounds": [0, 0, W - 1, H - 1], "tiles": W * H, "registers": regs, "factories": factories, "sites": [], "seats": [],
              "names": names, "colors": colors_}
    layout["places"] = rsa._places(layout)
    conc = json.loads((d / "concrete.json").read_text(encoding="utf-8"))
    meta = {"prime_s": round(end / 1e6, 6), "program_s": conc["program_s"], "primes": rsa.PRIMES, "tiles": W * H, "ions_per_tile": 288,
            "factories": len(factories), "steps": steps, "parts": meta_parts, "gadgets": n, "window_s": window_s,
            "hot": conc["hot"], "cold_ions": conc["cold_ions"], "work_s": conc["prime_work_s"], "merge_us": conc["merge_us"]}
    (data / "meta.json").write_text(json.dumps(meta, separators=(",", ":")), encoding="utf-8")
    (data / "programs.json").write_text(json.dumps(progs, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")
    (data / "layout.json").write_text(json.dumps(layout, separators=(",", ":")), encoding="utf-8")
    missing = sum(1 for p_ in progs if not p_["key"] and p_["d11"] in ("pass", "fail"))
    size = sum(f.stat().st_size for f in data.rglob("*") if f.is_file())
    print(f"{n_win} windows of {window_s:g} s; {len(progs)} programs, {missing} without an ion file yet; {size / 1e6:.1f} MB in {data}; "
          f"{time.time() - t_start:.0f} s", flush=True)


_TITLES = {("T",): "|T> by cultivation (Gidney, Shutty and Jones), on a d = 11 patch",
           ("read", "Y"): "Ȳ read in place (Gidney's |Ȳ> preparation run backwards)",
           ("h",): "a transversal H: one pulse on the patch's data ions", ("cold",): "a cold access (composed)"}


def rsa_title(shape) -> str:
    from qccd.gadget import rsa
    shape = tuple(shape)
    if shape in _TITLES:
        return _TITLES[shape]
    return rsa.title(shape) if shape[0] == "merge" or shape in rsa._TITLES else " ".join(map(str, shape))


def compile_all(out: Path, workers: int, glob: str) -> None:
    import multiprocessing as mp
    d = _dir(out)
    rows = json.loads((d / "shapes.json").read_text(encoding="utf-8"))
    todo = [k for k in sorted(rows, key=lambda k: len(ast.literal_eval(k)[3])) if fnmatch.fnmatch(k, glob)
            and not (d / "programs" / f"{_name(k)}.json").exists()]
    print(f"{len(todo)} programs to build, {workers} workers", flush=True)
    with mp.Pool(workers) as pool:
        for line in pool.imap_unordered(_check_one, [(k, str(d)) for k in todo]):
            print(line, flush=True)


def run(out: Path) -> None:
    import gidney_concrete as GC
    from qccd.fullstack.scale.gidney_surgery import hot_ions
    d = _dir(out)
    rows = json.loads((d / "shapes.json").read_text(encoding="utf-8"))
    missing, failed, us = [], [], Counter()
    for text in rows:
        f = d / "programs" / f"{_name(text)}.json"
        if not f.exists():
            missing.append(text)
            continue
        row = json.loads(f.read_text(encoding="utf-8"))
        us[row["us"]] += 1
        if row["failed"] or not row["same_waveforms"] or row["distance_at_3"] != 3:
            failed.append(text)
    if missing:
        sys.exit(f"{len(missing)} programs not built, e.g. {missing[:3]}")
    if failed:
        sys.exit(f"{len(failed)} programs not clean, e.g. {failed[:3]}")
    if len(us) != 1:
        sys.exit(f"merges of more than one time: {us}")
    g = d / "gadgets.json"
    if not g.exists():
        sys.exit("the programs that are not merges are not built: run `gadgets` first")
    for k, v in json.loads(g.read_text(encoding="utf-8")).items():
        if v["failed"] or not v.get("deterministic", True) or abs(v["us"] - v["plan_us"]) > 0.5:
            sys.exit(f"{k}: not clean, or not the time the plan charges: {v}")
    lst, R = _reader(out)
    plan = _plan(out, R)
    t0 = time.time()
    # the programs checked are the ones this plan uses, not those of an older plan
    now = {repr(s): n for s, n in plan.merges(None).items()}
    if now != rows:
        sys.exit(f"shapes.json is not this plan's: {len(set(now) ^ set(rows))} programs differ; run `shapes` again")
    key = GC._keys(lst, R)
    prime = plan.schedule(key, {}, {})[0]
    whole = plan.schedule(None, {}, {})[0]
    work = plan.serial(key, {})
    placed, tiles = plan.placed(None)
    hot = hot_ions(plan, placed, tiles)
    result = {"prime_s": prime / 1e6, "program_s": whole / 1e6, "years": whole / 1e6 / (365.25 * 86400),
              "tiles": plan.width * plan.height, "wires": len(plan.wire_tiles), "floor": FLOOR, "programs": len(rows),
              "merge_us": next(iter(us)), "uses": sum(rows.values()), "hot": hot, "cold_ions": COLD_IONS,
              "clashes": dict(plan.clashes), "prime_work_s": {k: v / 1e6 for k, v in work.most_common()}}
    (d / "concrete.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"one prime {prime / 3.6e9:.2f} h; the program {result['years']:.2f} years; {hot['tiles']} of {result['tiles']} tiles used, "
          f"{hot['ions']:,} hot ions + {COLD_IONS:,} cold = {hot['ions'] + COLD_IONS:,}; {sum(work.values()) / prime:.2f} at once; "
          f"{time.time() - t0:.0f} s; clashes {dict(plan.clashes)}")
    total = sum(work.values())
    for k, v in work.most_common():
        print(f"{v / 1e6:>12,.0f} s  {100 * v / total:5.1f} %  {k}")


def main(argv) -> None:
    if len(argv) < 2:
        sys.exit(__doc__)
    what, out = argv[0], Path(argv[1])
    if what == "labels":
        labels(out)
    elif what == "shapes":
        shapes(out)
    elif what == "gadgets":
        gadgets(out)
    elif what == "compile":
        compile_all(out, int(argv[2]) if len(argv) > 2 else 2, argv[3] if len(argv) > 3 else "*")
    elif what == "run":
        run(out)
    elif what == "ions":
        ions(out, int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 2, "--full" not in argv)
    elif what == "page":
        page_data(out, float(argv[2]) if len(argv) > 2 else 120.0)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
