"""The component tables, proved against the factories they were derived from.

`variants.py` exists so the design tool can offer a component's parameters in a browser
that cannot run Python.  The whole argument for it is that the table is DERIVED from the
factory and then CHECKED against the factory, so it cannot drift the way a hand-written
JavaScript mirror would.  This file is that check, and it runs before any JavaScript
exists -- if these fail, nothing about the browser is worth discussing.

Two failures this file is built around, both of which are easy to ship and hard to see:

**A signed zero.**  `linear_register`'s first site sits at `0 * pitch`.  Probed only at
positive values it looks like the constant `0.0`, and then at `pitch = -3` the factory
produces `-0.0` where the table produces `+0.0`.  `[0.0] == [-0.0]` is `True` in Python,
so an ordinary assertion is blind to it; every comparison here goes through `_same_bits`.

**A shared pool record with two meanings.**  `loading_zone` has two sites, and only one of
them takes the `capacity` parameter -- `m` is hard-wired to 2.  A table that interned
records without their local ids, or a hand-written rule saying "set every site's
capacity", would silently change a trap the factory never touches.

The classification snapshot at the end is the tripwire for the case nobody is watching
for: someone edits a factory so a parameter starts doing real arithmetic, and the palette
quietly loses a control or gains a wrong one.  It fails by parameter name instead.
"""

from __future__ import annotations

import inspect
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import variants as V  # noqa: E402
from qccd.arch.library import CATALOG, VARIANT_DOMAINS, build  # noqa: E402

NAMES = sorted(CATALOG)


@pytest.fixture(scope="module")
def blocks():
    return V.all_blocks()


def _defaults(name):
    return {p: par.default
            for p, par in inspect.signature(CATALOG[name]).parameters.items()
            if par.default is not inspect.Parameter.empty}


# -- the tables reproduce the factory -------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_the_table_reproduces_the_factory_over_the_whole_grid(name, blocks):
    """The claim the design rests on, at every offered variant crossed with a slot sweep
    that includes a negative float and a value below 1."""
    n = V.check_variants(name, "full", block=blocks[name])
    assert n > 0
    # and the sweep has to actually be a sweep
    slots = [p for p, m in blocks[name]["params"].items() if m["kind"] == "slot"]
    variants = len(blocks[name]["grid"])
    assert n >= variants, (n, variants)
    if slots:
        assert n > variants, "no slot values were swept"


@pytest.mark.parametrize("name", NAMES)
def test_resolving_at_the_defaults_gives_the_records_the_page_already_ships(name, blocks):
    """The free invariant: the default records still ship as the witness, so the table
    can be checked against them with no new data."""
    dims = VARIANT_DOMAINS.get(name, {})
    dflt = _defaults(name)
    sel = {k: dflt[k] for k in dims}
    assert V._same_bits(V.resolve(name, blocks[name], sel), V._spec(name))


def test_a_signed_zero_is_not_lost(blocks):
    """`0 * pitch` is `-0.0` at negative pitch. The classifier must call it a multiply,
    not a constant -- and `==` cannot tell the difference, which is why it nearly got
    shipped that way."""
    b = blocks["linear_register"]
    got = V.resolve("linear_register", b, {"n": 8}, {"pitch": -3.0})
    want = V._spec("linear_register", n=8, pitch=-3.0)
    assert V._same_bits(got, want)

    x = got["records"][0]["args"][1]
    assert V._bits(x) == V._bits(-0.0), (x, V._bits(x).hex())
    assert V._bits(x) != V._bits(0.0), "the sign of the zero was lost"
    # and the naive check really is blind to it, which is the point of _same_bits
    assert [0.0] == [-0.0]


def test_the_two_sites_of_a_loading_zone_do_not_share_a_slotset(blocks):
    """`l` takes `capacity`; `m` is hard-wired to 2. A blanket 'set every site's
    capacity' rule -- the obvious hand-written one -- corrupts `m`."""
    got = V.resolve("loading_zone", blocks["loading_zone"], {}, {"capacity": 5})
    caps = {r["args"][0]: r["kwargs"].get("capacity")
            for r in got["records"] if r["method"] == "d.site"}
    assert caps == {"l": 5, "m": 2}, caps
    assert caps == {r["args"][0]: r["kwargs"].get("capacity")
                    for r in build("loading_zone", capacity=5).records
                    if r["method"] == "d.site"}


def test_a_text_slot_rewrites_the_blurb_the_factory_writes(blocks):
    got = V.resolve("loading_zone", blocks["loading_zone"], {}, {"capacity": 5})
    assert got["blurb"] == build("loading_zone", capacity=5).blurb
    assert "5 ions" in got["blurb"]


# -- the classification is what we think it is ----------------------------------------

def test_the_classification_snapshot(blocks):
    """A factory edit that changes what a parameter DOES must fail here, by name, rather
    than silently removing a palette control or offering one that lies."""
    kinds: dict[str, str] = {}
    for name in NAMES:
        for p, meta in blocks[name]["params"].items():
            kinds[f"{name}.{p}"] = meta["kind"]

    dim = sorted(k for k, v in kinds.items() if v == "dim")
    slot = sorted(k for k, v in kinds.items() if v == "slot")
    inert = sorted(k for k, v in kinds.items() if v == "inert")

    assert dim == ["grid_tile.a", "grid_tile.b", "linear_register.n",
                   "transport_loop.width", "trap_junction.arms"], dim
    assert inert == ["trap_junction.arm"], inert
    assert len(slot) == 19, slot
    # every parameter of every factory is accounted for, none silently dropped
    for name in NAMES:
        declared = set(_defaults(name))
        assert set(blocks[name]["params"]) == declared, (name, declared)


def test_the_inert_parameter_is_named_and_explained(blocks):
    """`trap_junction.arm` reaches `params` and no record, pin or blurb. A control that
    moves nothing is worse than no control: the user turns it and believes something
    happened."""
    meta = blocks["trap_junction"]["params"]["arm"]
    assert meta["kind"] == "inert"
    assert "changes nothing" in meta["why"]
    a = build("trap_junction", arm=1.0)
    b = build("trap_junction", arm=99.0)
    assert a.records == b.records and a.pins == b.pins and a.blurb == b.blurb


def test_only_three_operations_are_ever_asked_of_the_browser(blocks):
    """The complete inventory of arithmetic the JS half will do. If a fourth op appears
    here, the drift argument has to be made again from scratch."""
    ops = set()
    for b in blocks.values():
        for ss in b["slotsets"]:
            ops.update(s[2] for s in ss)
        ops.update(s[2] for s in b["topslots"])
    assert ops <= {"mul", "set", "text"}, ops


def test_the_multiplier_set_is_finite_and_small(blocks):
    """What makes the arithmetic sweepable rather than merely sampled."""
    coefs = V.coef_set(blocks)
    assert coefs, "no multipliers at all means `mul` was never classified"
    assert len(coefs) < 100, len(coefs)
    assert all(isinstance(c, float) for c in coefs)
    assert 0.0 in coefs, "the 0*pitch coefficient must be present, not folded to a const"


# -- the label -------------------------------------------------------------------------

@pytest.mark.parametrize("name", NAMES)
def test_the_variant_label_carries_integers_only(name, blocks):
    """Pin ids depend on the enumerated dims and nothing else, so the label never has to
    carry a float -- which is how float formatting is kept out of the round trip."""
    b = blocks[name]
    for d in b["dims"]:
        for v in d["values"]:
            lab = V.variant_label(name, b, {d["param"]: v})
            assert lab.startswith("cmpvar:" + name)
            for part in lab.split(":")[2:]:
                for kv in part.split(","):
                    assert kv.split("=")[1].lstrip("-").isdigit(), lab


def test_the_label_distinguishes_the_two_components_that_share_a_pin_node(blocks):
    """`ancilla_dock` and `trap_junction` both have `pins[0].node == 'j'`. Recovering a
    placed instance's identity by probing for that node cannot tell them apart -- which
    is a bug the browser has today."""
    a = V.variant_label("ancilla_dock", blocks["ancilla_dock"], {})
    t = V.variant_label("trap_junction", blocks["trap_junction"], {"arms": 4})
    assert a != t
    assert build("ancilla_dock").pins[0]["node"] == build("trap_junction").pins[0]["node"]


# -- the shape of what gets shipped ----------------------------------------------------

def test_interning_keeps_the_payload_affordable(blocks):
    """The tables only work because variants share records. If interning ever stops
    working the page grows without anyone noticing until it is huge."""
    import json

    total = sum(len(json.dumps(b, separators=(",", ":"))) for b in blocks.values())
    assert total < 120_000, f"{total:,} bytes is more than the page can justify"
    naive = 0
    for name in NAMES:
        dims = VARIANT_DOMAINS.get(name, {})
        for sel in V._grid_points(dims):
            naive += len(json.dumps(V._spec(name, **V._unit_kwargs(name, sel)),
                                    separators=(",", ":")))
    assert total < naive, (total, naive)


@pytest.mark.parametrize("name", NAMES)
def test_every_grid_row_carries_the_bound_the_browser_needs(name, blocks):
    """`computeLayout` throws above COORD_MAX and the palette is not inside `paint()`'s
    try/catch, so the browser has to refuse before it draws. The bound travels as data --
    the largest coefficient this variant multiplies -- so no limit is retyped in JS."""
    b = blocks[name]
    muls = {s[0] for ss in b["slotsets"] for s in ss if s[2] == "mul"}
    for row in b["grid"]:
        bounds, constmax = row[4], row[5]
        assert constmax >= 0.0
        for p in muls:
            if p in bounds:
                assert bounds[p]["cmax"] >= bounds[p]["cmin"] >= 0.0, (p, bounds[p])
