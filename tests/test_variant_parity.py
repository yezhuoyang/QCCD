"""Python and JavaScript resolve a component variant to the same bits, or this fails.

`tests/test_variant_tables.py` proves the tables reproduce the factories.  This proves the
BROWSER reproduces the tables -- which is the other half, and the half that has burned this
project before: a JS operand renderer beside a Python one disagreed on 3,830 of 3,830 rows,
and nothing noticed until someone read the screen.

Shape is deliberately the same as `test_edit_parity.py`: Python writes a corpus of
*parameter points*, node runs the SHIPPED `engine.js` resolver over it, Python compares.
Neither side ever reads the other's output, so there is nothing to refresh and no way for
the two to converge on a shared mistake.

**Tolerance is zero, and it is compared in bits.**  Every number crosses the boundary as
its raw 64-bit pattern, so `0.0` and `-0.0` are different values here -- which they must
be, because `linear_register`'s first site is `0 * pitch` and at negative pitch the sign of
that zero is the whole answer.  A `==` comparison cannot see it.

The arithmetic sweep is EXHAUSTIVE over what ships rather than sampled: `coef_set` walks
the shipped pools and returns every distinct multiplier (37 today), and each is crossed
with hostile values -- signed zeros, subnormals, the largest finite double, the ulp
neighbours of 1, and the values that straddle Python's repr format switch.
"""

from __future__ import annotations

import json
import math
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import variants as V  # noqa: E402
from qccd.arch.library import VARIANT_DOMAINS  # noqa: E402

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

#: hostile doubles the multiply has to survive
HOSTILE = [
    0.0, -0.0, 1.0, -1.0, 0.5, 3.0, 0.1, 1 / 3, 2 / 3, 7.7, 1e-3, 1e3,
    5e-324, -5e-324, 2.2250738585072014e-308, 1.7976931348623157e308,
    -1.7976931348623157e308, 1.0000000000000002, 0.9999999999999999,
    1e16, 1e17, 1e-4, 1e-5, 2.0 ** -1074, 2.0 ** 1023, 123.456,
]

SWEEP = {"number": [1.0, 0.5, 3.0, -1.0, 0.001, 7.5],
         "integer": [1, 2, 3, 8, 64],
         "string": ["trap", "data", "gate", "zzq"]}


def _bits(x) -> str:
    return "f:" + struct.pack("<d", float(x)).hex()


def _bitify(x):
    if isinstance(x, list):
        return [_bitify(i) for i in x]
    if isinstance(x, dict):
        return {k: _bitify(v) for k, v in x.items()}
    if isinstance(x, bool):
        return x
    if isinstance(x, (int, float)):
        return _bits(x)
    return x


@pytest.fixture(scope="module")
def blocks():
    return V.all_blocks()


@pytest.fixture(scope="module")
def corpus(blocks):
    """Every offered variant of every component, crossed with a slot sweep."""
    points = []
    for name, b in blocks.items():
        slot_params = [(p, m) for p, m in sorted(b["params"].items())
                       if m["kind"] == "slot"]
        combos: list[dict] = [{}]
        for p, meta in slot_params:
            combos = [dict(c, **{p: v}) for c in combos for v in SWEEP[meta["type"]]]
        # cap the cross per component so the corpus stays a few thousand points, but keep
        # every VARIANT -- the enumerated axis is the one that must be complete
        if len(combos) > 24:
            combos = combos[:: max(1, len(combos) // 24)][:24]
        for sel in V._grid_points(VARIANT_DOMAINS.get(name, {})):
            for slots in combos:
                points.append({"name": name, "sel": sel, "slots": slots})
    return {"blocks": blocks, "points": points,
            "coefficients": sorted(V.coef_set(blocks)), "values": HOSTILE}


@pytest.fixture(scope="module")
def js(corpus, tmp_path_factory):
    d = tmp_path_factory.mktemp("vparity")
    cin, cout = d / "corpus.json", d / "out.json"
    cin.write_text(json.dumps(corpus), encoding="utf-8")
    r = subprocess.run([NODE, str(ROOT / "tests" / "variant_parity.mjs"),
                        str(cin), str(cout)],
                       capture_output=True, text=True, timeout=900, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-3000:]}"
    return json.loads(cout.read_text(encoding="utf-8"))


def test_the_corpus_is_not_empty_and_covers_every_offered_variant(corpus, blocks):
    """Anti-vacuity: a harness that silently sweeps nothing passes forever."""
    assert len(corpus["points"]) > 500, len(corpus["points"])
    seen = {(p["name"], json.dumps(p["sel"], sort_keys=True)) for p in corpus["points"]}
    want = {(n, json.dumps(s, sort_keys=True))
            for n in blocks for s in V._grid_points(VARIANT_DOMAINS.get(n, {}))}
    assert seen == want, "not every offered variant is in the corpus"
    assert len(corpus["coefficients"]) > 20, corpus["coefficients"]


def test_the_two_languages_resolve_every_point_to_the_same_bits(corpus, js, blocks):
    """The differential. Tolerance zero, compared as raw float64 patterns."""
    bad = []
    for point, got in zip(corpus["points"], js["results"]):
        assert "error" not in got, (point, got.get("error"))
        want = _bitify(V.resolve(point["name"], blocks[point["name"]],
                                 point["sel"], point["slots"]))
        if got["spec"] != want:
            bad.append((point, want, got["spec"]))
    assert not bad, (f"{len(bad)} of {len(corpus['points'])} points differ; first: "
                     f"{json.dumps(bad[0], default=str)[:1200]}")


def test_kwarg_key_order_survives_the_crossing(corpus, js, blocks):
    """Order is user-visible text in the listing, so it is part of the answer."""
    for point, got in zip(corpus["points"], js["results"]):
        want = V.resolve(point["name"], blocks[point["name"]],
                         point["sel"], point["slots"])
        for w, g in zip(want["records"], got["spec"]["records"]):
            assert list(w["kwargs"]) == list(g["kwargs"]), (point, w, g)


def test_the_label_is_identical_in_both_languages(corpus, js, blocks):
    """A stamp writes this into the document; if the two halves disagree, an exported
    design resolves its pins differently depending on who placed it."""
    for point, got in zip(corpus["points"], js["results"]):
        want = V.variant_label(point["name"], blocks[point["name"]], point["sel"])
        assert got["label"] == want, (point, want, got["label"])


def test_the_multiply_agrees_on_every_coefficient_that_ships(corpus, js):
    """Exhaustive over the reachable arithmetic, because the coefficient set is finite:
    every multiplier in every shipped pool, crossed with hostile doubles."""
    n = 0
    for i, c in enumerate(corpus["coefficients"]):
        for j, v in enumerate(corpus["values"]):
            n += 1
            assert js["arith"][i][j] == _bits(c * v), (
                f"{c!r} * {v!r}: python {_bits(c * v)} js {js['arith'][i][j]}")
    assert n == len(corpus["coefficients"]) * len(HOSTILE)
    assert n > 700, n


def test_a_signed_zero_crosses_the_boundary_intact(corpus, js, blocks):
    """The failure the bit comparison exists for. `0 * pitch` at negative pitch is -0.0,
    and `[0.0] == [-0.0]` is True, so an ordinary assertion would pass on a wrong page."""
    ix = next(i for i, p in enumerate(corpus["points"])
              if p["name"] == "linear_register" and p["slots"].get("pitch") == -1.0)
    got = js["results"][ix]["spec"]["records"][0]["args"][1]
    assert got == _bits(-0.0), got
    assert got != _bits(0.0)


def test_the_guard_refuses_what_the_layout_cannot_measure(blocks):
    """`computeLayout` throws above COORD_MAX and `renderPalette` is outside `paint()`'s
    try/catch, so an unrefused value does not draw badly -- it aborts the bar and keeps
    doing so, because the value persists."""
    b = blocks["linear_register"]
    src = ROOT / "tests" / "_guard.mjs"
    # The probes arrive as STRINGS: `json.dumps(inf)` emits a bare `Infinity`, which is
    # not JSON -- and a non-finite value is one of the cases under test here.
    src.write_text(
        "import { createRequire } from 'module';\n"
        "import fs from 'fs';\n"
        "const require = createRequire(import.meta.url);\n"
        "const Q = require('../qccd/viz/engine.js');\n"
        "const vb = JSON.parse(fs.readFileSync(process.argv[2],'utf8'));\n"
        "const out = [];\n"
        "for (const p of JSON.parse(process.argv[3]).map(Number)) {\n"
        "  const g = Q.variantGuard(vb, {n: 32}, {pitch: p});\n"
        "  let threw = false;\n"
        "  try {\n"
        "    const s = Q.resolveVariant(vb, {n: 32}, {pitch: p});\n"
        "    const nodes = s.records.filter(r => r.method === 'd.site')\n"
        "      .map(r => ({id: r.args[0], x: r.args[1], y: r.args[2]}));\n"
        "    Q.computeLayout(nodes, []);\n"
        "  } catch (e) { threw = true; }\n"
        "  out.push({p, refused: !!g, code: g && g.code, threw});\n"
        "}\n"
        "console.log(JSON.stringify(out));\n", encoding="utf-8")
    try:
        vb = ROOT / "tests" / "_guard_vb.json"
        vb.write_text(json.dumps(b), encoding="utf-8")
        probes = ["1.0", "1e3", "1e4", "3.3e4", "1e5", "1e6", "1e9", "0.0", "1e-9",
                  "Infinity"]
        r = subprocess.run([NODE, str(src), str(vb), json.dumps(probes)],
                           capture_output=True, text=True, timeout=600, cwd=ROOT)
        assert r.returncode == 0, r.stderr[-2000:]
        rows = json.loads(r.stdout)
    finally:
        for f in (ROOT / "tests" / "_guard.mjs", ROOT / "tests" / "_guard_vb.json"):
            if f.exists():
                f.unlink()

    assert len(rows) == len(probes)
    assert any(x["refused"] for x in rows), "the guard never refuses anything"
    assert any(not x["refused"] for x in rows), "the guard refuses everything"
    for x in rows:
        if x["threw"]:
            assert x["refused"], (
                f"pitch={x['p']} made computeLayout throw and the guard allowed it")
        if x["refused"]:
            assert x["code"] in ("out_of_range", "no_variant"), x


def test_a_deep_copy_keeps_two_resolves_from_corrupting_each_other(blocks, tmp_path):
    """The pool is shared by every variant and every instance. A shallow copy makes one
    component's pitch silently change another's, which no single-resolve test can see."""
    src = tmp_path / "alias.mjs"
    src.write_text(
        "import { createRequire } from 'module';\n"
        "import fs from 'fs';\n"
        "const require = createRequire(import.meta.url);\n"
        "const Q = require('" + (ROOT / "qccd" / "viz" / "engine.js").as_posix() + "');\n"
        "const vb = JSON.parse(fs.readFileSync(process.argv[2],'utf8'));\n"
        "const a = Q.resolveVariant(vb, {n: 8}, {pitch: 3.0});\n"
        "const b = Q.resolveVariant(vb, {n: 8}, {pitch: 1.0});\n"
        "const c = Q.resolveVariant(vb, {n: 8}, {pitch: 3.0});\n"
        "console.log(JSON.stringify({a: a.records.map(r=>r.args[1]),\n"
        "  b: b.records.map(r=>r.args[1]), c: c.records.map(r=>r.args[1]),\n"
        "  pool: vb.pool.map(r=>r.args && r.args[1])}));\n", encoding="utf-8")
    vb = tmp_path / "vb.json"
    vb.write_text(json.dumps(blocks["linear_register"]), encoding="utf-8")
    r = subprocess.run([NODE, str(src), str(vb)], capture_output=True, text=True,
                       timeout=600, cwd=ROOT)
    assert r.returncode == 0, r.stderr[-2000:]
    d = json.loads(r.stdout)
    assert d["a"] == d["c"], "a second resolve at the same value gave a different answer"
    assert d["a"] != d["b"], "the two pitches produced identical geometry"
    before = [x for x in blocks["linear_register"]["pool"]
              if x.get("args") and len(x["args"]) > 1]
    assert d["pool"] == [x["args"][1] if x.get("args") and len(x["args"]) > 1 else None
                         for x in blocks["linear_register"]["pool"]], \
        "resolving mutated the shipped pool in place"
    assert before  # anti-vacuity


def test_the_harness_catches_a_planted_drift(corpus, blocks, tmp_path):
    """A differential that cannot fail is decoration. Plant `base + v` where the resolver
    does `base * v` and confirm the comparison reports it."""
    engine = (ROOT / "qccd" / "viz" / "engine.js").read_text(encoding="utf-8")
    old = "if (op === 'mul') _vPathSet(obj, path, _vPathGet(obj, path) * v);"
    assert old in engine, "the resolver moved; this mutation no longer plants anything"
    mutant = tmp_path / "engine.js"
    mutant.write_text(engine.replace(
        old, "if (op === 'mul') _vPathSet(obj, path, _vPathGet(obj, path) + v);", 1),
        encoding="utf-8")

    src = tmp_path / "m.mjs"
    src.write_text(
        "import { createRequire } from 'module';\n"
        "import fs from 'fs';\n"
        "const require = createRequire(import.meta.url);\n"
        "const Q = require('./engine.js');\n"
        "const vb = JSON.parse(fs.readFileSync(process.argv[2],'utf8'));\n"
        "console.log(JSON.stringify(Q.resolveVariant(vb, {n: 8}, {pitch: 3.0})));\n",
        encoding="utf-8")
    vb = tmp_path / "vb.json"
    vb.write_text(json.dumps(blocks["linear_register"]), encoding="utf-8")
    r = subprocess.run([NODE, str(src), str(vb)], capture_output=True, text=True,
                       timeout=600, cwd=tmp_path)
    assert r.returncode == 0, r.stderr[-2000:]
    got = _bitify(json.loads(r.stdout))
    want = _bitify(V.resolve("linear_register", blocks["linear_register"],
                             {"n": 8}, {"pitch": 3.0}))
    assert got != want, "the planted `base + v` was not caught: the harness is blind"


def test_no_leaf_is_a_nan_that_would_compare_equal_to_nothing(corpus, js):
    """NaN != NaN, so a NaN leaf would make the differential vacuously pass forever."""
    for got in js["results"]:
        for rec in got["spec"]["records"]:
            for a in rec["args"][1:]:
                if isinstance(a, str) and a.startswith("f:"):
                    v = struct.unpack("<d", bytes.fromhex(a[2:]))[0]
                    assert not math.isnan(v), rec
