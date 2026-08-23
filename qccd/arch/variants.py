"""Component parameters, made live in a browser that cannot run Python.

`library.py`'s factories are the only description of what a component is, and the design
tool is a single self-contained page with no server and no Pyodide -- so the browser
cannot call them.  Shipping each component at its defaults (what we did until now) leaves
the palette advertising parameters nobody can move, which is the same "the menu looks
broken" complaint that got the avatars built.  Mirroring the factories in JavaScript is
the one thing this project has decided it will not do: a second implementation drifts, and
it already cost us once, when a JS operand renderer disagreed with its Python counterpart
on 3,830 of 3,830 rows.

This module is the third option.  It DERIVES, from the factories themselves, a table small
enough to ship and dumb enough to replay -- and the replay is three operations, which is
the complete list of arithmetic the browser is trusted with:

    mul   x = coefficient * value          one IEEE multiply
    set   x = value                        substitution, no arithmetic
    text  s = prefix + str(value) + suffix  one interpolation

Everything else -- which records exist, which node ids they carry, what the walk of a loop
is -- is precomputed by Python across an enumerated grid and interned.  Nothing about the
shape of a component is ever recomputed in JavaScript.

Why the derivation cannot drift
-------------------------------
The recipe is not written down anywhere.  It is recovered by BUILDING the factory at
probe values and differencing the results, every time the page is emitted, and
`check_variants` then replays the recipe and compares against the factory again.  A
factory edit that changes what a parameter does either reclassifies automatically or
raises `VariantError` naming the parameter -- it cannot silently produce a table that
disagrees with the code it came from.  `render.py` runs that gate before it writes the
blob, so a disagreeing page cannot be built at all.

Two things here are load-bearing and look like details
------------------------------------------------------
**The probe set contains a negative number.**  With only positive probes, a coordinate
like `linear_register`'s `s0.x = 0 * pitch` looks CONSTANT -- `0*1` and `0*3` are both
`0.0` -- and would ship as the constant `0.0`.  Then at `pitch = -3` Python produces
`-0.0` and the table produces `+0.0`.  That is a real disagreement in the bits, and

    [0.0] == [-0.0]   ->   True

so an equality check is blind to it.  Hence `struct.pack` comparisons throughout, and
`-1.0` in `PROBES`.

**`mul` is tried before `set`.**  A leaf that happens to equal its probe value at every
probe (a coefficient of exactly 1.0) is a multiply, not a substitution.  Classifying it as
`set` would be right for floats and wrong the moment the coefficient is not 1.
"""

from __future__ import annotations

import copy
import inspect
import json
import struct
from typing import Any, Iterable, Mapping, Sequence

from . import library

__all__ = ["VariantError", "classify", "variant_block", "resolve", "variant_label",
           "check_variants", "coef_set", "PROBES", "UNIT_FLOATS"]


class VariantError(RuntimeError):
    """A parameter whose effect on the factory this module cannot express.

    Raised at page-emit time, by name, rather than shipping a table that disagrees with
    the factory it was derived from.
    """


#: Probe values per parameter type.  THE NEGATIVE FLOAT IS LOAD-BEARING -- see the module
#: docstring: without it, `0 * pitch` classifies as a constant and disagrees with Python
#: by a signed zero.  0.5 is there so a coefficient of 2 cannot masquerade as `set`.
PROBES: Mapping[str, tuple] = {
    "number": (1.0, 3.0, -1.0, 0.5),
    "integer": (1, 5, 7),
    "string": ("trap", "zzq", "q"),
}

#: Float parameters are pooled at 1.0 so that every coordinate literal in the pool IS its
#: own multiplier coefficient, and `coefficient * value` needs no division to recover.
UNIT_FLOATS = 1.0

_SPEC_KEYS = ("records", "pins", "requires", "blurb")


# ------------------------------------------------------------------ leaves and paths

def _norm(obj):
    """Tuples to lists, so a spec compares and hashes the way JSON will ship it."""
    return json.loads(json.dumps(obj, default=list))


def _spec(name: str, **kw) -> dict:
    c = library.build(name, **kw)
    return _norm({"records": [dict(r) for r in c.records],
                  "pins": [dict(p) for p in c.pins],
                  "requires": dict(c.requires),
                  "blurb": c.blurb})


def _leaves(obj, path=(), out=None) -> dict[tuple, Any]:
    """Every scalar in the spec, by the path that reaches it."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            _leaves(v, path + (k,), out)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            _leaves(v, path + (i,), out)
    else:
        out[path] = obj
    return out


def _pathget(obj, path):
    for p in path:
        obj = obj[p]
    return obj


def _pathset(obj, path, value):
    for p in path[:-1]:
        obj = obj[p]
    obj[path[-1]] = value


def _bits(x) -> bytes:
    return struct.pack("<d", float(x))


def _same_float(a, b) -> bool:
    """Bit equality. `0.0 == -0.0` is True and they are not the same number here."""
    if not isinstance(a, (int, float)) or not isinstance(b, (int, float)):
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    return _bits(a) == _bits(b)


def _ptype(default) -> str:
    if isinstance(default, bool):
        return "string"
    if isinstance(default, float):
        return "number"
    if isinstance(default, int):
        return "integer"
    return "string"


# ------------------------------------------------------------------------- classify

def _dims_of(name: str) -> dict[str, list]:
    return dict(library.VARIANT_DOMAINS.get(name, {}))


def _unit_kwargs(name: str, sel: Mapping[str, Any]) -> dict:
    """The build the pool is made from: the grid point, floats forced to 1.0."""
    kw = dict(sel)
    for p, par in inspect.signature(library.CATALOG[name]).parameters.items():
        if p in kw:
            continue
        if isinstance(par.default, float):
            kw[p] = UNIT_FLOATS
    return kw


def _template(strings: Sequence[str], values: Sequence[Any]) -> list[str] | None:
    """Longest common prefix/suffix over the probe renderings, or None."""
    pre = strings[0]
    for s in strings[1:]:
        i = 0
        while i < min(len(pre), len(s)) and pre[i] == s[i]:
            i += 1
        pre = pre[:i]
    suf = strings[0]
    for s in strings[1:]:
        i = 0
        while i < min(len(suf), len(s)) and suf[-1 - i] == s[-1 - i]:
            i += 1
        suf = suf[len(suf) - i:] if i else ""
    for s, v in zip(strings, values):
        if s != pre + str(v) + suf:
            return None
    return [pre, suf]


def classify(name: str, sel: Mapping[str, Any]) -> dict[tuple, list]:
    """At one grid point: every leaf a non-dim parameter moves, and how.

    Returns ``{path: [param, op, extra]}``.  Raises `VariantError` for a leaf that moves
    in a way none of the three operations expresses -- which is the point: an
    unexpressible parameter is refused loudly instead of being frozen silently.
    """
    fn = library.CATALOG[name]
    dims = _dims_of(name)
    base = _spec(name, **_unit_kwargs(name, sel))
    base_leaves = _leaves(base)
    out: dict[tuple, list] = {}

    for pname, par in inspect.signature(fn).parameters.items():
        if pname in dims:
            continue
        default = par.default
        if default is inspect.Parameter.empty:
            continue
        kind = _ptype(default)
        probes = PROBES[kind]
        seen: dict[tuple, list] = {}
        for v in probes:
            kw = _unit_kwargs(name, sel)
            kw[pname] = v
            try:
                got = _spec(name, **kw)
            except Exception as exc:                          # noqa: BLE001
                raise VariantError(
                    f"{name}.{pname}: the factory refuses the probe value {v!r} "
                    f"({exc}); classification needs it to see what the parameter does"
                ) from exc
            gl = _leaves(got)
            if set(gl) != set(base_leaves):
                raise VariantError(
                    f"{name}.{pname} changes the SHAPE of the component (a leaf appears "
                    f"or disappears at {pname}={v!r}), so it is a dimension, not a slot; "
                    f"add it to VARIANT_DOMAINS in library.py")
            for path, val in gl.items():
                if val == base_leaves[path] and _bits_equal_or_nonfloat(val, base_leaves[path]):
                    continue
                seen.setdefault(path, []).append((v, val))

        for path, obs in seen.items():
            b = base_leaves[path]
            # 1. MUL -- tried FIRST, so a coefficient of 1.0 is not mistaken for `set`
            if isinstance(b, (int, float)) and not isinstance(b, bool) and kind == "number":
                if all(_same_float(b * v, got) for v, got in obs):
                    out[path] = [pname, "mul"]
                    continue
            # 2. SET -- the leaf simply is the parameter
            if all(got == v and type(got) is type(v) for v, got in obs):
                out[path] = [pname, "set"]
                continue
            # 3. TEXT -- one interpolation into a fixed template
            if all(isinstance(got, str) for _, got in obs) and kind in ("integer", "string"):
                tpl = _template([g for _, g in obs], [v for v, _ in obs])
                if tpl is not None and tpl[0] + str(default) + tpl[1] == _pathget(base, path):
                    out[path] = [pname, "text", tpl]
                    continue
            raise VariantError(
                f"{name}.{pname} moves {'.'.join(map(str, path))} in a way this module "
                f"cannot express with mul/set/text: base={b!r}, observed={obs!r}. "
                f"Making the browser reproduce it would mean re-implementing the factory "
                f"in JavaScript, which is exactly what this design refuses.")
    return out


def _bits_equal_or_nonfloat(a, b) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return _bits(a) == _bits(b)
    return True


def inert_params(name: str) -> dict[str, str]:
    """Parameters that move nothing at all -- reported, never silently offered.

    `trap_junction.arm` is the whole population today: it reaches `params` and no record,
    pin or blurb.  A control that cannot move anything is worse than no control, because
    the user turns it and believes something happened.
    """
    fn = library.CATALOG[name]
    dims = _dims_of(name)
    out: dict[str, str] = {}
    for pname, par in inspect.signature(fn).parameters.items():
        if pname in dims or par.default is inspect.Parameter.empty:
            continue
        base = _spec(name, **_unit_kwargs(name, {k: v[0] for k, v in dims.items()}))
        moved = False
        for v in PROBES[_ptype(par.default)]:
            kw = _unit_kwargs(name, {k: vs[0] for k, vs in dims.items()})
            kw[pname] = v
            try:
                if _spec(name, **kw) != base:
                    moved = True
                    break
            except Exception:                                  # noqa: BLE001
                moved = True
                break
        if not moved:
            out[pname] = (f"{pname} appears in this component's params and in no record, "
                          f"pin or blurb -- setting it changes nothing")
    return out


# --------------------------------------------------------------------- the table

def _grid_points(dims: Mapping[str, list]) -> list[dict]:
    """Every combination, in `dims` order -- which is also the row order of `grid`."""
    keys = list(dims)
    out = [{}]
    for k in keys:
        out = [dict(p, **{k: v}) for p in out for v in dims[k]]
    return out


def _defaults(name: str) -> dict:
    return {p: par.default
            for p, par in inspect.signature(library.CATALOG[name]).parameters.items()
            if par.default is not inspect.Parameter.empty}


def variant_block(name: str) -> dict:
    """The shippable table for one component.

    Records are interned BY FULL CONTENT INCLUDING THEIR LOCAL ID, which is what keeps
    `loading_zone`'s two sites apart: `l` carries `capacity` and `m` is hard-wired to 2,
    so they must never share a slotset.  The invariant that makes the table sound --
    *the same pooled record always carries the same slotset* -- is asserted here rather
    than assumed, at every grid point.
    """
    dims = _dims_of(name)
    dflt = _defaults(name)
    pool: list[dict] = []
    pool_ix: dict[str, int] = {}
    poolslots: list[int] = []
    slotsets: list[list] = []
    slotset_ix: dict[str, int] = {}
    pins_pool: list[list] = []
    pins_ix: dict[str, int] = {}
    req_pool: list[dict] = []
    req_ix: dict[str, int] = {}
    blurbs: list[str] = []
    blurb_ix: dict[str, int] = {}
    grid: list[list] = []
    top: list = []

    for sel in _grid_points(dims):
        spec = _spec(name, **_unit_kwargs(name, sel))
        moves = classify(name, sel)

        # split the leaf map into per-record slots and top-level slots
        per_rec: dict[int, list] = {}
        this_top: list = []
        for path, rec in moves.items():
            if path[0] == "records":
                per_rec.setdefault(path[1], []).append([rec[0], list(path[2:])] + rec[1:])
            else:
                this_top.append([rec[0], list(path)] + rec[1:])
        this_top.sort(key=json.dumps)
        if not top:
            top = this_top
        elif top != this_top:
            raise VariantError(
                f"{name}: the top-level slots differ between grid points "
                f"({top!r} vs {this_top!r}); the table cannot carry one list")

        plan = []
        for i, record in enumerate(spec["records"]):
            key = json.dumps(record, sort_keys=True)
            if key not in pool_ix:
                pool_ix[key] = len(pool)
                pool.append(record)
                poolslots.append(-1)
            idx = pool_ix[key]
            ss = sorted(per_rec.get(i, []), key=json.dumps)
            sk = json.dumps(ss, sort_keys=True)
            if sk not in slotset_ix:
                slotset_ix[sk] = len(slotsets)
                slotsets.append(ss)
            sidx = slotset_ix[sk]
            if poolslots[idx] == -1:
                poolslots[idx] = sidx
            elif poolslots[idx] != sidx:
                # THE INVARIANT. If it ever fires, two variants disagree about what a
                # parameter does to an identical record, and the table cannot be keyed by
                # pool index. Better a build failure than a page that is quietly wrong.
                raise VariantError(
                    f"{name}: record {record!r} carries slotset "
                    f"{slotsets[poolslots[idx]]!r} at one grid point and {ss!r} at "
                    f"{sel!r}; the table cannot intern it")
            plan.append(idx)

        pk = json.dumps(spec["pins"], sort_keys=True)
        if pk not in pins_ix:
            pins_ix[pk] = len(pins_pool)
            pins_pool.append(spec["pins"])
        rk = json.dumps(spec["requires"], sort_keys=True)
        if rk not in req_ix:
            req_ix[rk] = len(req_pool)
            req_pool.append(spec["requires"])
        bk = spec["blurb"]
        if bk not in blurb_ix:
            blurb_ix[bk] = len(blurbs)
            blurbs.append(bk)

        # WHAT THE BROWSER MAY DRAW. `computeLayout` throws above COORD_MAX and the
        # palette is not inside `paint()`'s try/catch, so a tile can brick the editor.
        # The bound travels as DATA -- the largest and smallest coefficient this variant
        # multiplies -- so no limit is ever re-typed in JavaScript.
        bounds: dict[str, dict] = {}
        for i, record in enumerate(spec["records"]):
            for slot in per_rec.get(i, []):
                pname, path, op = slot[0], slot[1], slot[2]
                if op != "mul" or path[0] != "args":
                    continue
                c = abs(float(_pathget(record, path)))
                b = bounds.setdefault(pname, {"cmax": 0.0, "cmin": float("inf")})
                b["cmax"] = max(b["cmax"], c)
                if c > 0:
                    b["cmin"] = min(b["cmin"], c)
        for b in bounds.values():
            if b["cmin"] == float("inf"):
                b["cmin"] = 0.0
        constmax = 0.0
        for record in spec["records"]:
            if record["method"] in ("d.site", "d.junction"):
                constmax = max(constmax, abs(float(record["args"][1])),
                               abs(float(record["args"][2])))

        grid.append([plan, pins_ix[pk], req_ix[rk], blurb_ix[bk], bounds, constmax])

    inert = inert_params(name)
    params: dict[str, dict] = {}
    for i, (d, values) in enumerate(dims.items()):
        params[d] = {"kind": "dim", "dim": i, "values": values, "default": dflt.get(d)}
    slot_params = {s[0] for ss in slotsets for s in ss} | {t[0] for t in top}
    for p in sorted(slot_params):
        params[p] = {"kind": "slot", "type": _ptype(dflt[p]), "default": dflt[p]}
    for p, why in inert.items():
        params[p] = {"kind": "inert", "default": dflt[p], "why": why}

    return {"dims": [{"param": k, "values": v} for k, v in dims.items()],
            "pool": pool, "poolslots": poolslots, "slotsets": slotsets,
            "pins_pool": pins_pool, "req_pool": req_pool, "blurbs": blurbs,
            "grid": grid, "topslots": top, "params": params}


def _row(block: dict, sel: Mapping[str, Any]) -> list:
    """The grid row for a selection -- mixed-radix over `dims`, in declared order."""
    ix = 0
    for d in block["dims"]:
        vals = d["values"]
        v = sel.get(d["param"], vals[0])
        if v not in vals:
            raise VariantError(f"{d['param']}={v!r} is not offered; have {vals!r}")
        ix = ix * len(vals) + vals.index(v)
    return block["grid"][ix]


def _apply(obj, slots: Iterable[Sequence], values: Mapping[str, Any]) -> None:
    for slot in slots:
        pname, path, op = slot[0], slot[1], slot[2]
        if pname not in values:
            continue
        v = values[pname]
        if op == "mul":
            _pathset(obj, path, _pathget(obj, path) * v)
        elif op == "set":
            _pathset(obj, path, v)
        elif op == "text":
            _pathset(obj, path, slot[3][0] + str(v) + slot[3][1])
        else:                                                # pragma: no cover - guarded
            raise VariantError(f"unknown slot op {op!r}")


def resolve(name: str, block: dict, sel: Mapping[str, Any],
            slots: Mapping[str, Any] | None = None) -> dict:
    """Replay the table.  The Python half of what the browser will do, exactly."""
    slots = dict(slots or {})
    row = _row(block, sel)
    plan, pins_i, req_i, blurb_i = row[0], row[1], row[2], row[3]
    spec = {"records": [copy.deepcopy(block["pool"][i]) for i in plan],
            "pins": copy.deepcopy(block["pins_pool"][pins_i]),
            "requires": copy.deepcopy(block["req_pool"][req_i]),
            "blurb": block["blurbs"][blurb_i]}
    for i, rec_i in enumerate(plan):
        _apply(spec["records"][i], block["slotsets"][block["poolslots"][rec_i]], slots)
    _apply(spec, block["topslots"], slots)
    return spec


def variant_label(name: str, block: dict, sel: Mapping[str, Any]) -> str:
    """The label a stamp writes so its variant survives export and reload.

    INTEGERS ONLY.  Pin node ids depend on the enumerated dims and on nothing else, so
    the label never has to carry a float and float formatting can never drift across the
    two languages.
    """
    parts = []
    for d in block["dims"]:
        v = sel.get(d["param"], d["values"][0])
        parts.append(f"{d['param']}={int(v)}")
    return "cmpvar:" + name + (":" + ",".join(parts) if parts else "")


def coef_set(blocks: Mapping[str, dict]) -> set:
    """Every distinct multiplier that actually ships -- a finite set, so the arithmetic
    can be swept exhaustively rather than sampled."""
    out = set()
    for block in blocks.values():
        for rec_i, ss_i in enumerate(block["poolslots"]):
            if ss_i < 0:
                continue
            for slot in block["slotsets"][ss_i]:
                if slot[2] == "mul":
                    out.add(float(_pathget(block["pool"][rec_i], slot[1])))
    return out


def _same_bits(a, b) -> bool:
    """Equality that can see a signed zero, which `==` cannot."""
    la, lb = _leaves(a), _leaves(b)
    if set(la) != set(lb):
        return False
    for k, v in la.items():
        w = lb[k]
        if isinstance(v, float) or isinstance(w, float):
            if not isinstance(v, (int, float)) or not isinstance(w, (int, float)):
                return False
            if _bits(v) != _bits(w):
                return False
        elif v != w or type(v) is not type(w):
            return False
    return True


#: slot values swept when replaying the table against the factory
SWEEPS: Mapping[str, list] = {
    "number": [1.0, 0.5, 3.0, -1.0, 0.001],
    "integer": [1, 3, 8],
    "string": ["trap", "data"],
}


def check_variants(name: str, mode: str = "spine", block: dict | None = None) -> int:
    """Replay the table against the factory.  Returns the number of specs compared.

    `render.py` calls this before it writes the blob, so a page carrying a table that
    disagrees with the factory it came from cannot be built.
    """
    block = block if block is not None else variant_block(name)
    dims = _dims_of(name)
    points = _grid_points(dims)
    if mode == "spine" and len(points) > 3:
        keep = {0, len(points) // 2, len(points) - 1}
        points = [p for i, p in enumerate(points) if i in keep]

    slot_params = [(p, m) for p, m in sorted(block["params"].items())
                   if m["kind"] == "slot"]
    combos: list[dict] = [{}]
    for p, meta in slot_params:
        vals = SWEEPS[meta["type"]]
        if mode == "spine":
            vals = vals[:2]
        combos = [dict(c, **{p: v}) for c in combos for v in vals]

    n = 0
    for sel in points:
        for slots in combos:
            want = _spec(name, **dict(sel, **slots))
            got = resolve(name, block, sel, slots)
            n += 1
            if not _same_bits(got, want):
                raise VariantError(
                    f"{name} at {sel!r} {slots!r}: the table does not reproduce the "
                    f"factory.\n  factory {json.dumps(want)[:400]}\n"
                    f"  table   {json.dumps(got)[:400]}")
    return n


def all_blocks() -> dict[str, dict]:
    """Every component's table, built and self-checked."""
    return {name: variant_block(name) for name in sorted(library.CATALOG)}
