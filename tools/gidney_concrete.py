"""FormalRV's Gidney 2025 listing timed on ions at d = 11 (docs/FULLSTACK_PLAN.md, slice S10 step 3).

    python tools/gidney_concrete.py counts OUT                 the listing read; its counts against FormalRV's VM
    python tools/gidney_concrete.py shapes OUT                 every program the listing needs, by uses (OUT/shapes.json)
    python tools/gidney_concrete.py compile OUT [WORKERS] [GLOB] [again]  build and check each (OUT/programs/<hash>.json);
                                                               again: search on from each one's homes, keep it if bettered
    python tools/gidney_concrete.py wide OUT WIDTH...          the register-wide cats
    python tools/gidney_concrete.py run OUT                    the schedule: one prime, the program, the parts

OUT holds g25.sysprog and vm.json (`tools/gidney_machine.py`, FormalRV's compiler and VM); the inputs are the pin's.
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.setrecursionlimit(100000)

PIN = Path(os.environ.get("QCCD_FORMALRV", Path(__file__).resolve().parents[2] / "FormalRV-pin"))
INPUTS = PIN / "FormalRV/Audit/Gidney2025/Resource/Inputs/PaperMachine_q1M/execution.inputs"


def _reader(out: Path):
    from qccd.fullstack.scale import gidney as G
    from qccd.fullstack.scale import listing as L
    lst = L.read(out / "g25.sysprog", L.read_inputs(INPUTS))
    return lst, G.Reader(lst)


def _text(shape) -> str:
    if shape[0] == "wide":
        mask = shape[3] if len(shape) > 3 else shape[2]
        return f"wide {shape[1]}" + (f" mask {','.join(map(str, mask))}" if mask else "")
    return "rep " + " ".join(f"{t.x},{t.y},{'t' if t.turned else 'u'},{t.letter}" for t in shape[1])


def _shape(text: str):
    """A program, from its name: ("rep", targets) or ("wide", width, mask)."""
    from qccd.compile.tiled_cat import Target
    w = text.split()
    if w[0] == "wide":
        return ("wide", int(w[1]), tuple(int(v) for v in w[3].split(",")) if len(w) > 3 else None)
    out = []
    for item in w[1:]:
        x, y, t, l = item.split(",")
        out.append(Target(int(x), int(y), t == "t", l))
    return ("rep", tuple(out))


def _name(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()[:12]


def _keys(lst, R):
    g = lst.gadgets["gOnePrime"]
    return R.key("gOnePrime", {f: 0 for f in g.formals})


def counts(out: Path) -> None:
    from qccd.fullstack.scale import listing as L
    lst, _ = _reader(out)
    t0 = time.time()
    c = L.counts(lst)
    vm = json.loads((out / "vm.json").read_text(encoding="utf-8"))
    d = L.vm_differences(c, vm)
    print(f"{c['instructions']:,} instructions in {time.time() - t0:.1f} s; differences from the VM: {d or 'none'}")


def shapes(out: Path) -> None:
    from qccd.fullstack.scale.gidney_plan import Plan
    lst, R = _reader(out)
    plan = Plan(R)
    t0 = time.time()
    got = plan.shapes(None)
    rows = {}
    for shape, n in got.items():
        key = _text(shape)
        rows[key] = rows.get(key, 0) + n
    (out / "shapes.json").write_text(json.dumps(rows, indent=0), encoding="utf-8")
    print(f"{plan.tiles} tiles ({plan.width} x {plan.height}); {len(rows)} programs, {sum(rows.values()):,} uses, {time.time() - t0:.0f} s")
    for k, v in sorted(rows.items(), key=lambda kv: -kv[1])[:24]:
        print(f"{v:>12,}  {k}")


def _build(text: str, tries: int, out: Path) -> dict:
    from qccd.fullstack.scale import catprog as K
    t0 = time.time()
    shape = _shape(text)
    f = out / "programs" / f"{_name(text)}.json"
    old = json.loads(f.read_text(encoding="utf-8")) if f.exists() else None
    if shape[0] == "wide":
        built = K.build_wide(shape[1], (shape[1] // 2, -1), mask=shape[2])
    else:
        built = K.build_rep(shape[1], tries=tries, seeds=(1,), start=old["homes"] if old else None)
    if old and K.machine_us(built) >= old["us"]:    # searched on and found nothing better: already checked
        return {**old, "s": round(time.time() - t0, 1)}
    with tempfile.TemporaryDirectory() as tmp:
        checked = K.check(built, tmp)
    row = {"shape": text, "us": checked["us"], "failed": checked["failed"], "r10_blocks": checked["r10_blocks"],
           "ions": checked["ions"], "cells": K.cells(built), "homes": list(built.homes), "s": round(time.time() - t0, 1)}
    f.write_text(json.dumps(row), encoding="utf-8")
    return row


def _worker(args):
    text, tries, out = args
    try:
        row = _build(text, tries, Path(out))
        return f"{row['us']:>9.0f} us  failed {row['failed']}  r10 {row['r10_blocks']}  {row['s']:>6.0f} s  {text}"
    except Exception as exc:        # a shape no walk was found for stays listed, and the run says so
        return f"FAILED {text}: {exc!r}"


def _tries(uses: int) -> int:
    """A longer search where the program spends its time."""
    return 2000 if uses >= 10**8 else 1000 if uses >= 10**7 else 300 if uses >= 10**4 else 40


def compile_all(out: Path, workers: int, glob: str, again: bool = False) -> None:
    import multiprocessing as mp
    (out / "programs").mkdir(exist_ok=True)
    rows = json.loads((out / "shapes.json").read_text(encoding="utf-8"))
    todo = [k for k in sorted(rows, key=lambda k: -rows[k]) if fnmatch.fnmatch(k, glob)
            and ((again and k.startswith("rep")) or not (out / "programs" / f"{_name(k)}.json").exists())]
    print(f"{len(todo)} programs to build, {workers} workers", flush=True)
    with mp.Pool(workers) as pool:
        for line in pool.imap_unordered(_worker, [(k, _tries(rows[k]), str(out)) for k in todo]):
            print(line, flush=True)


def wide(out: Path, widths) -> None:
    (out / "programs").mkdir(exist_ok=True)
    for w in widths:
        text = f"wide {w}"
        if (out / "programs" / f"{_name(text)}.json").exists():
            continue
        row = _build(text, 0, out)
        print(f"{row['us']:>9.0f} us  failed {row['failed']}  r10 {row['r10_blocks']}  {row['s']:>6.0f} s  {text}", flush=True)


def run(out: Path) -> None:
    from qccd.fullstack.scale.gidney_plan import Plan, Times
    lst, R = _reader(out)
    rows = json.loads((out / "shapes.json").read_text(encoding="utf-8"))
    times, cells, failed = Times(), {}, []
    missing = []
    for text in rows:
        f = out / "programs" / f"{_name(text)}.json"
        if not f.exists():
            missing.append(text)
            continue
        row = json.loads(f.read_text(encoding="utf-8"))
        shape = _shape(text)
        times[shape] = row["us"]
        cells[shape] = [tuple(c) for c in row["cells"]]
        if row["failed"]:
            failed.append(text)
    if missing:
        sys.exit(f"{len(missing)} programs not built, e.g. {missing[:5]}")
    plan = Plan(R)
    t0 = time.time()
    prime = plan.schedule(_keys(lst, R), times, cells)[0]
    whole = plan.schedule(None, times, cells)[0]
    result = {"prime_s": prime / 1e6, "program_s": whole / 1e6, "years": whole / 1e6 / (365.25 * 86400),
              "tiles": plan.tiles, "programs": len(rows), "failed": failed, "clashes": dict(plan.clashes)}
    named: dict = {}
    for k, v in plan._memo.items():
        if k is not None:
            named.setdefault(k[0], []).append(v[0] / 1e6)
    result["gadgets_s"] = {k: [min(v), max(v), len(v)] for k, v in named.items()}
    work = plan.serial(_keys(lst, R), times)
    result["prime_work_s"] = {k: v / 1e6 for k, v in sorted(work.items(), key=lambda kv: -kv[1])}
    uses = plan.shapes(_keys(lst, R))
    by = {}
    for shape, n in uses.items():
        reps = 11       # the listing's: a wide cat's and a product's alike
        us = times.wide(shape[1], shape[2], shape[3]) if shape[0] == "wide" else times.rep(shape)
        name = _text(shape)
        by[name] = by.get(name, 0.0) + n * reps * us / 1e6
    result["prime_top_shapes_s"] = dict(sorted(by.items(), key=lambda kv: -kv[1])[:24])
    (out / "concrete.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(f"one prime {result['prime_s']:,.0f} s = {result['prime_s'] / 3600:.2f} h; program {result['program_s']:,.0f} s = "
          f"{result['years']:.2f} years; {plan.tiles} hot tiles; {time.time() - t0:.0f} s to schedule")
    print("failed programs", failed, "clashes", dict(plan.clashes))
    total = sum(work.values())
    print(f"one prime's work, as if nothing ran at once: {total / 1e6:,.0f} s, {total / prime:.2f} x its time")
    for k, v in sorted(work.items(), key=lambda kv: -kv[1]):
        print(f"{v / 1e6:>12,.0f} s  {100 * v / total:5.1f} %  {k}")
    for k, v in list(result["prime_top_shapes_s"].items())[:16]:
        print(f"{v:>12,.0f} s  {100 * v * 1e6 / total:5.1f} %  {k}")


def main(argv) -> None:
    if len(argv) < 2:
        sys.exit(__doc__)
    what, out = argv[0], Path(argv[1])
    if what == "counts":
        counts(out)
    elif what == "shapes":
        shapes(out)
    elif what == "compile":
        compile_all(out, int(argv[2]) if len(argv) > 2 else 2, argv[3] if len(argv) > 3 else "*", argv[4:] == ["again"])
    elif what == "wide":
        wide(out, [int(w) for w in argv[2:]])
    elif what == "run":
        run(out)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])
