"""Two renderers over one table, and a round trip at literal tolerance zero.

**The round trip.**  Writer to an independent reader and back, for all nine shipped
devices, returning the identical list of (layer, datatype, coordinates) -- not close, not
to a tolerance, *identical*, because the coordinates never stopped being integers.  The
reader shares no code with the writer beyond the 8-byte real, which has its own test, and
it knows nothing about layouts, polygons or geometry: it parses records.  That is what
makes it evidence rather than a mirror.

**Two renderers.**  `gds.py` and `svg.py` both consume `Layout.flatten()` and nothing
else.  Here they are both read *back* -- the GDSII through the record parser, the SVG
through an XML parser -- and asserted to agree on polygon count, per-layer polygon count,
per-layer integer area and bounding box, over all nine devices.  The areas are computed by
a shoelace written locally in this file, so neither renderer's own arithmetic is judging
it.  That comparison is qiskit-metal's thesis turned into a test: if the two disagree,
the shape table was not the source of truth.

It works because the SVG keeps its coordinates in the same integer nanometres and puts the
scale in one `transform`.  Had the writer baked the scale into the points, this would be a
comparison to some number of decimal places and would prove much less.
"""

from __future__ import annotations

import re
import struct
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.phys.build import build_layout  # noqa: E402
from qccd.phys.gds import (  # noqa: E402
    COORD_MAX,
    COORD_MIN,
    GdsBoundary,
    bytes_to_real8,
    library_bytes,
    read_gds,
    real8_to_bytes,
    write_gds,
)
from qccd.phys.shapes import Cell, Inst, Layout, Poly  # noqa: E402
from qccd.phys.svg import svg_text, write_svg  # noqa: E402
from qccd.phys.tech import load_technology  # noqa: E402

ARCH = ROOT / "arch"
DEVICES = sorted(p.stem.replace(".arch", "") for p in ARCH.glob("*.arch.json"))
PRESET = "eth_junction_2201.12579"
SVG_NS = "{http://www.w3.org/2000/svg}"


@pytest.fixture(scope="module")
def tech():
    return load_technology(PRESET)


@pytest.fixture(scope="module")
def layouts(tech):
    return {s: build_layout(load(ARCH / f"{s}.arch.json"), tech) for s in DEVICES}


def _shoelace(points) -> int:
    """Twice the signed area, in ints.  Local, so neither renderer marks its own work."""
    return sum(a[0] * b[1] - b[0] * a[1]
               for a, b in zip(points, points[1:] + points[:1]))


# --------------------------------------------------------------- the 8-byte real

@pytest.mark.parametrize("value", [1e-3, 1e-9, 1.0, 0.5, 0.0, -1e-3, 1e-6, 2.5e-4,
                                   1e-10, 1234.5, -0.0625])
def test_the_excess_64_real_round_trips_exactly(value):
    """GDSII's float is base **sixteen**, excess 64 -- not IEEE 754, so `struct` cannot.

    Both numbers this writer ever emits are here: 1e-3 (user units per database unit) and
    1e-9 (the database unit in metres).  If either did not come back bit-identical, every
    coordinate in every file would be scaled by something nobody chose.
    """
    raw = real8_to_bytes(value)
    assert len(raw) == 8
    assert bytes_to_real8(raw) == value
    assert real8_to_bytes(bytes_to_real8(raw)) == raw


def test_the_real_has_the_byte_pattern_the_format_specifies():
    """A golden vector, so "round-trips" cannot be satisfied by a self-consistent bug."""
    assert real8_to_bytes(0.0) == b"\x00" * 8
    assert real8_to_bytes(1.0).hex() == "4110000000000000"
    assert real8_to_bytes(0.5).hex() == "4080000000000000"
    assert real8_to_bytes(1e-3).hex() == "3e4189374bc6a7f0"
    assert real8_to_bytes(1e-9).hex() == "3944b82fa09b5a54"
    # the sign is the top bit and nothing else changes
    assert real8_to_bytes(-1e-3).hex() == "be4189374bc6a7f0"


def test_a_real_out_of_range_is_refused():
    with pytest.raises(ValueError, match="outside the range"):
        real8_to_bytes(1e300)


def test_a_real_of_the_wrong_length_is_refused():
    with pytest.raises(ValueError, match="8 bytes"):
        bytes_to_real8(b"\x00" * 7)


# ------------------------------------------------------------------ the round trip

@pytest.mark.parametrize("stem", DEVICES)
def test_the_gds_round_trip_is_exact_for_every_shipped_device(layouts, tech, stem):
    """**The headline.**  Write, read with something that knows only records, compare.

    Nothing here is a tolerance.  The coordinates in the file are the nanometre integers
    the builder computed, because the database unit is 1e-9 m -- so the comparison is
    between two lists of ints and it is `==`.
    """
    lay = layouts[stem]
    numbers = {l.name: (l.gds_layer, l.gds_datatype) for l in tech.layers}
    want = [(numbers[p.layer][0], numbers[p.layer][1], p.xy) for p in lay.flatten()]
    lib = read_gds(library_bytes(lay))
    got = [(b.layer, b.datatype, b.open_xy) for b in lib.boundaries]
    assert len(want) > 0, "an empty comparison would pass"
    assert got == want


@pytest.mark.parametrize("stem", DEVICES)
def test_the_file_is_a_function_of_the_layout_and_nothing_else(layouts, stem):
    """No wall clock in `BGNLIB`, so two writes of one layout are byte-identical."""
    first = library_bytes(layouts[stem])
    assert first == library_bytes(layouts[stem])


def test_the_timestamps_are_a_fixed_epoch_and_not_the_clock(layouts):
    """Comparing two writes cannot see a clock: they happen in the same second.

    So the timestamp fields are read out of the file and checked against the constant.
    `BGNLIB` and `BGNSTR` each carry twelve INT2 -- a modification date and an access date
    -- and all twenty-four have to be 1970-01-01T00:00:00, or the "same input, same bytes"
    claim only holds for writes close together in time.
    """
    raw = library_bytes(layouts["stationary_chain"])
    want = struct.pack(">12h", 1970, 1, 1, 0, 0, 0, 1970, 1, 1, 0, 0, 0)
    found = []
    pos = 0
    while pos < len(raw):
        length, rtype, _dtype = struct.unpack_from(">HBB", raw, pos)
        if rtype in (0x01, 0x05):  # BGNLIB, BGNSTR
            found.append(raw[pos + 4: pos + length])
        pos += length
    assert len(found) == 2, "one BGNLIB and one BGNSTR"
    assert all(f == want for f in found), [f.hex() for f in found]


def test_an_odd_length_payload_is_padded_to_an_even_record(layouts):
    """Every GDSII record length is even, and only an odd-length string can prove it.

    Nothing the writer emits for the shipped devices is odd: the numeric payloads are all
    even by construction and the default library name has four characters.  A three
    character name is the one input that exercises the padding, and without it the branch
    is dead code that no device would notice losing.
    """
    raw = library_bytes(layouts["stationary_chain"], name="ODD")
    pos = 0
    while pos < len(raw):
        length, rtype, _dtype = struct.unpack_from(">HBB", raw, pos)
        assert length % 2 == 0, (pos, rtype, length)
        pos += length
    assert pos == len(raw)
    lib = read_gds(raw)
    assert lib.name == "ODD" and lib.structures == ("ODD",)


def test_the_units_record_says_nanometres(layouts):
    lib = read_gds(library_bytes(layouts["ring144_24v"]))
    assert lib.db_unit_m == 1e-9
    assert lib.user_unit_per_db == 1e-3
    assert lib.structures == ("QCCD",)
    assert lib.name == "QCCD"


def test_the_boundary_is_closed_in_the_file_and_open_in_the_layout(layouts):
    """GDSII repeats the first point; `Poly.xy` does not.  Both conventions, once each."""
    b = read_gds(library_bytes(layouts["stationary_chain"])).boundaries[0]
    pts = b.points
    assert pts[0] == pts[-1], "a GDSII boundary is closed"
    assert len(b.open_xy) == len(b.xy) - 2
    assert b.open_xy == layouts["stationary_chain"].flatten()[0].xy


def test_every_record_is_well_formed(layouts):
    """Walk the byte stream: even lengths, no overrun, HEADER first and ENDLIB last."""
    raw = library_bytes(layouts["h2_racetrack"])
    pos, types = 0, []
    while pos < len(raw):
        length, rtype, _dtype = struct.unpack_from(">HBB", raw, pos)
        assert length >= 4 and length % 2 == 0, (pos, length)
        assert pos + length <= len(raw)
        types.append(rtype)
        pos += length
    assert pos == len(raw), "the records must tile the file exactly"
    assert types[0] == 0x00 and types[-1] == 0x04, "HEADER first, ENDLIB last"
    assert types.count(0x08) == layouts["h2_racetrack"].n_polys()


# -------------------------------------------------------------------- the refusals

def _one(tech, x1, y1=90000):
    poly = Poly.rect("RF", 0, 0, x1, y1, role="rail", net="RF", owner="huge")
    return Layout(tech, {"c": Cell("c", (poly,))}, (Inst("c"),))


def test_a_coordinate_past_the_four_byte_field_is_refused_and_named(tech):
    """Wrapping would move a polygon 4.3 metres and still produce a file that opens."""
    with pytest.raises(ValueError) as e:
        library_bytes(_one(tech, COORD_MAX + 1))
    msg = str(e.value)
    assert "huge" in msg and "RF" in msg and str(COORD_MAX) in msg
    assert "wrap" in msg
    # and one nanometre under it is fine
    assert library_bytes(_one(tech, COORD_MAX))


def test_the_negative_end_of_the_field_is_checked_too(tech):
    poly = Poly.rect("RF", COORD_MIN - 1, 0, 0, 90000, role="rail", net="RF",
                     owner="deep")
    lay = Layout(tech, {"c": Cell("c", (poly,))}, (Inst("c"),))
    with pytest.raises(ValueError, match="deep"):
        library_bytes(lay)


def test_a_polygon_on_a_layer_the_technology_does_not_declare_is_refused(tech):
    poly = Poly.rect("MYSTERY", 0, 0, 1000, 1000, role="rail", net="RF", owner="x")
    lay = Layout(tech, {"c": Cell("c", (poly,))}, (Inst("c"),))
    with pytest.raises(KeyError, match="MYSTERY"):
        library_bytes(lay)


#: Each entry targets exactly one refusal branch of the reader.  Crafted rather than
#: carved out of a real file, because truncating a real file lands on whichever branch the
#: last record happens to hit and says nothing about the others.
_HEAD = struct.pack(">HBB", 6, 0x00, 0x02) + struct.pack(">h", 600)


@pytest.mark.parametrize("raw,match", [
    (_HEAD + struct.pack(">HBB", 100, 0x10, 0x03) + b"\x00" * 4, "past the end"),
    (_HEAD + b"\x00\x02", "truncated record header"),
    (_HEAD + struct.pack(">HBB", 5, 0x06, 0x06) + b"\x00\x00", "not an even number"),
    (_HEAD + struct.pack(">HBB", 2, 0x06, 0x06), "not an even number"),
    (_HEAD + struct.pack(">HBB", 10, 0x10, 0x03) + b"\x00" * 6, "whole number of"),
])
def test_a_damaged_file_is_refused_rather_than_half_read(raw, match):
    with pytest.raises(ValueError, match=match):
        read_gds(raw)


def test_a_truncated_real_file_is_also_refused(layouts):
    """The crafted cases above are surgical; this one is what actually happens."""
    raw = library_bytes(layouts["stationary_chain"])
    for cut in (2, 6, 10, 37, len(raw) // 2):
        with pytest.raises(ValueError):
            read_gds(raw[:-cut])


def test_a_boundary_missing_its_datatype_is_refused(tech):
    raw = bytearray(library_bytes(_one(tech, 100000)))
    # blank the DATATYPE record (type 0x0e) by turning it into a no-op ENDEL-less filler
    idx = raw.find(bytes([0x00, 0x06, 0x0E, 0x02]))
    assert idx > 0, "the DATATYPE record moved"
    del raw[idx:idx + 6]
    with pytest.raises(ValueError, match="without"):
        read_gds(bytes(raw))


def test_an_empty_library_is_refused_rather_than_returned(tech):
    from qccd.phys import gds
    only_header = (gds._record(0x00, 0x02, struct.pack(">h", 600))
                   + gds._record(0x04, 0x00))
    with pytest.raises(ValueError, match="no structure"):
        read_gds(only_header)


def test_write_gds_puts_the_same_bytes_on_disk(layouts, tmp_path):
    p = write_gds(layouts["chain"], tmp_path / "chain.gds")
    assert p.read_bytes() == library_bytes(layouts["chain"])


# ------------------------------------------------- two renderers, one shape table

def _svg_polys(text: str):
    """Parse the SVG back into {layer name: [(gds number, [(x, y), ...]), ...]}."""
    root = ET.fromstring(text)
    out: dict[str, list] = {}
    for g in root.iter(f"{SVG_NS}g"):
        name = g.get("data-layer")
        if name is None:
            continue
        entries = out.setdefault(name, [])
        for poly in g.findall(f"{SVG_NS}polygon"):
            pts = []
            for pair in poly.get("points", "").split():
                x, y = pair.split(",")
                pts.append((int(x), int(y)))
            entries.append((g.get("data-gds"), pts))
    return out


@pytest.mark.parametrize("stem", DEVICES)
def test_gds_and_svg_agree_on_the_metal_they_were_both_given(layouts, tech, stem):
    """Count, per-layer count, per-layer integer area and bounding box.  Exactly.

    Both files are read *back* and compared with each other -- neither is compared with
    the `Layout` that produced it, so a shared misreading of the layout would still show
    up as agreement, but a renderer dropping, duplicating, reordering or mistransforming a
    polygon would not.  The areas are shoelaced locally, so `Poly.area_nm2` is not marking
    its own homework either.
    """
    lay = layouts[stem]
    by_number = {(l.gds_layer, l.gds_datatype): l.name for l in tech.layers}

    gds_by_layer: dict[str, list] = {}
    for b in read_gds(library_bytes(lay)).boundaries:
        gds_by_layer.setdefault(by_number[(b.layer, b.datatype)], []).append(
            list(b.points[:-1]))
    svg_by_layer = {name: [pts for _num, pts in entries]
                    for name, entries in _svg_polys(svg_text(lay)).items()}

    assert set(gds_by_layer) == set(svg_by_layer)
    assert sum(map(len, gds_by_layer.values())) == lay.n_polys() > 0

    for name in sorted(gds_by_layer):
        g, s = gds_by_layer[name], svg_by_layer[name]
        assert len(g) == len(s), name
        assert sum(abs(_shoelace(p)) for p in g) == sum(abs(_shoelace(p)) for p in s), name
        gxy = [c for p in g for c in p]
        sxy = [c for p in s for c in p]
        assert (min(gxy), max(gxy)) == (min(sxy), max(sxy)), name
        assert g == s, f"{name}: same polygons, same order"


@pytest.mark.parametrize("stem", DEVICES)
def test_the_svg_carries_the_same_integers_the_gds_does(layouts, stem):
    """No decimal point in any coordinate: the scale is in the transform, not the points."""
    text = svg_text(layouts[stem])
    for pts in re.findall(r'<polygon points="([^"]+)"', text):
        assert "." not in pts and "e" not in pts.lower(), pts[:80]


def test_the_svg_transform_is_isotropic_and_flips_y(layouts):
    """`scale(s, -s)`, one `s`.  The browser's px()/py() is anisotropic up to 12x and
    would shear a true-to-scale rectangle; nothing here goes near it."""
    root = ET.fromstring(svg_text(layouts["ring144_24v"]))
    metal = next(g for g in root.iter(f"{SVG_NS}g") if g.get("id") == "metal")
    sx, sy = re.search(r"scale\(([^,]+),([^)]+)\)", metal.get("transform")).groups()
    assert float(sx) > 0 and float(sy) < 0
    assert float(sx) == -float(sy), "one scale, both axes"
    assert len(re.findall(r"scale\(", svg_text(layouts["ring144_24v"]))) == 1


@pytest.mark.parametrize("stem", DEVICES)
def test_the_svg_is_well_formed_and_standalone(layouts, stem):
    text = svg_text(layouts[stem])
    root = ET.fromstring(text)            # raises if it is not well formed
    assert root.tag == f"{SVG_NS}svg"
    assert "<script" not in text and "javascript:" not in text
    urls = set(re.findall(r"https?://[^\s\"'<>]+", text))
    assert urls <= {"http://www.w3.org/2000/svg"}, urls


def test_an_empty_layout_renders_something_that_says_so(tech):
    text = svg_text(Layout(tech))
    ET.fromstring(text)
    assert "empty layout" in text


def test_write_svg_puts_the_same_text_on_disk(layouts, tmp_path):
    p = write_svg(layouts["chain"], tmp_path / "chain.svg")
    assert p.read_text(encoding="utf-8") == svg_text(layouts["chain"])


def test_the_two_writers_read_the_same_table_and_import_nothing_of_each_other():
    """One table, two consumers -- and neither may quietly become the other's source."""
    for rel in ("qccd/phys/gds.py", "qccd/phys/svg.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        imports = re.findall(r"^\s*(?:from|import)\s+([\w.]+)", src, flags=re.M)
        assert not any("viz" in m for m in imports), (rel, imports)
        other = "svg" if rel.endswith("gds.py") else "gds"
        assert not any(m.endswith("." + other) or m == other for m in imports), rel
        assert "flatten()" in src, f"{rel} must render the shape table, not rebuild it"
