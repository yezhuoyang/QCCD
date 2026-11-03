"""A CZ by a pair made ahead (qccd/fullstack/scale/czpair.py): proved a CZ, every frame needed,
the surgery machine's CZ found and replaced, the pair placed facing its partners, and the H̄ off
the guard's path on the lattice."""
from qccd.fullstack.scale import czpair as C
from qccd.fullstack.scale.lattice import check, schedule


def _surgery_cz(a, b, L, outs, g):
    """The surgery machine's CZ as `program._Exp` writes it: the preparation under no guard."""
    m, n, s = outs
    fr = lambda o: tuple(sorted(set(g) | {o}))
    return [("prep", (L, "X"), None, ()), ("ppm", ("Z", a, "Z", L), m, g), ("ht", (L,), None, g),
            ("ppm", ("Z", L, "Z", b), n, g), ("read", (L, "X"), s, g),
            ("z", (a,), None, fr(n)), ("z", (b,), None, fr(m)), ("z", (b,), None, fr(s))]


def test_a_pair_is_a_cz_and_every_frame_counts():
    for g in ((), ("g",), ("g", "!h")):
        new = C.pair_lines("a", "b", "A", "B", ("m", "n", "s"), g)
        assert C.proved(new, "a", "b", ["A", "B"], g)
        for k in range(8, 13):
            assert not C.proved(new[:k] + new[k + 1:], "a", "b", ["A", "B"], g)


def test_the_surgery_cz_is_replaced_by_a_pair():
    seg = _surgery_cz("a", "b", "L", ("m", "n", "s"), ("g",))
    out, found = C.pairs(seg + [("x", ("b",), None, ("g",))])
    assert found == [("L.A", "L.B", "a", "b")]
    assert out[:-1] == C.pair_lines("a", "b", "L.A", "L.B", ("m", "n", "s"), ("g",))
    assert out[-1] == ("x", ("b",), None, ("g",))
    # an outcome some other line reads: left alone
    kept = seg + [("z", ("c",), None, ("m",))]
    assert C.pairs(kept) == (kept, [])


def test_the_pair_faces_its_partners():
    homes = {"a": (0, 0), "b": (0, 2)}
    pins = C.place([("A", "B", "a", "b")], homes)
    assert pins["A"] in ((0, 1), (0, -1)) and pins["B"] in ((0, 1), (0, 3)) and pins["A"] != pins["B"]
    assert (pins["B"][0] + 1, pins["B"][1]) not in homes.values()


def test_a_ported_stream_is_found_too():
    # `Farm.ports` writes argument lists, not tuples
    seg = [(w, list(x), o, list(g)) for w, x, o, g in _surgery_cz("a", "b", "L", ("m", "n", "s"), ("g",))]
    assert C.pairs(seg)[1] == [("L.A", "L.B", "a", "b")]


def test_the_pair_takes_the_hbar_off_the_guard():
    # the guard comes late: six merges on c, then c read; then CZ a b under it
    homes = {"a": (0, 0), "b": (0, 2), "c": (4, 0), "d": (4, 2)}
    lead = [("ppm", ("Z", "c", "Z", "d"), f"w{i}", ()) for i in range(6)] + [("read", ("c", "X"), "g", ())]
    cz = _surgery_cz("a", "b", "L", ("m", "n", "s"), ("g",))
    bounds = (-1, -1, 6, 5)
    ends = {}
    for name, (stream, pins) in {"surgery": (lead + cz, {}), "pair": (None, None)}.items():
        if name == "pair":
            stream, found = C.pairs(lead + cz)
            pins = C.place(found, homes)
        gadgets = schedule(stream, homes, bounds, pinned=pins)
        assert check(stream, gadgets) == []
        guard = max(g.t1 for g in gadgets if g.line == len(lead) - 1)
        ends[name] = max(g.t1 for g in gadgets) - guard
    # after the guard: merge, H̄, merge -- or one layer of two merges
    assert ends["surgery"] > 679_800.0
    assert ends["pair"] < ends["surgery"] - 600_000.0
