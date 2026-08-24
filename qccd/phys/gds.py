"""GDSII, written by hand, so a fab tool can open what this project derives.

Around 200 lines of `struct` and no new dependency.  `gdstk` and `gdspy` both do this
better, and neither is worth a wheel: the subset a rectilinear electrode layout needs is
one library, one structure and a stream of BOUNDARY records, and writing it here means the
byte layout is something this project can test rather than something it trusts.

**Integer nanometres, all the way to the file.**  The GDSII database unit is 1e-9 m and
the user unit is 1e-6 m, so a coordinate in the file *is* the nanometre integer that
`qccd/phys/shapes.py` computed -- no scaling, no rounding, nothing to disagree about.  The
XY field is a 4-byte signed integer, which reaches +/- 2.147 m; a coordinate past that is
refused **by name**, because silently wrapping one would move a polygon 4.3 metres and
still produce a file that opens.

**The output is a pure function of the layout.**  GDSII carries a modification and an
access timestamp in `BGNLIB` and `BGNSTR`, and writing the wall clock there would make two
runs of the same input differ.  They are fixed at 1970-01-01, and two writes of one layout
are byte-identical -- which is what lets the round-trip test compare files rather than
just shapes.

**Flattened, deliberately.**  GDSII has cells and `SREF`s and this package has `Cell` and
`Inst`, so writing the hierarchy would be natural.  It is not written, because verifying it
would need the *reader* to resolve placements -- a second implementation of
`Poly.placed`, which is the one thing this project does not allow itself.  A flat
structure round-trips through a reader that only parses records and knows nothing about
geometry, and that reader is genuinely independent of the writer.

**What does not survive.**  `role`, `net` and `owner` are this package's metadata, not
GDSII concepts.  The file carries layer number, datatype and coordinates; the round-trip
test compares exactly those, exactly.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

from .shapes import Layout, Poly
from .tech import Technology

__all__ = ["write_gds", "read_gds", "GdsBoundary", "GdsLibrary",
           "real8_to_bytes", "bytes_to_real8", "COORD_MIN", "COORD_MAX"]

#: The GDSII XY field is a 4-byte signed integer, in database units (nanometres here).
COORD_MIN = -(1 << 31)
COORD_MAX = (1 << 31) - 1

#: user unit / database unit, and database unit in metres.  1 um and 1 nm.
USER_UNIT_M = 1e-6
DB_UNIT_M = 1e-9

# record types
_HEADER, _BGNLIB, _LIBNAME, _UNITS, _ENDLIB = 0x00, 0x01, 0x02, 0x03, 0x04
_BGNSTR, _STRNAME, _ENDSTR = 0x05, 0x06, 0x07
_BOUNDARY, _LAYER, _DATATYPE, _XY, _ENDEL = 0x08, 0x0D, 0x0E, 0x10, 0x11

# data types
_NO_DATA, _INT2, _INT4, _REAL8, _ASCII = 0x00, 0x02, 0x03, 0x05, 0x06

#: A fixed timestamp, so the file is a function of the layout and nothing else.
_EPOCH = (1970, 1, 1, 0, 0, 0)


# ------------------------------------------------------------------ the 8-byte real

def real8_to_bytes(value: float) -> bytes:
    """GDSII's excess-64 base-16 float: sign, 7-bit exponent, 56-bit mantissa.

    Not IEEE 754.  The exponent is a power of *sixteen* offset by 64 and the mantissa is a
    fraction in `[1/16, 1)`, which is why this cannot be a `struct` format character.  Both
    numbers this file actually writes -- 1e-3 and 1e-9 -- come back bit-identical, and
    `tests/test_gds.py` says so rather than assuming it.
    """
    if value == 0.0:
        return b"\x00" * 8
    sign = 0x80 if value < 0 else 0x00
    v = abs(value)
    exponent = 0
    while v >= 1.0:
        v /= 16.0
        exponent += 1
    while v < 1.0 / 16.0:
        v *= 16.0
        exponent -= 1
    mantissa = int(round(v * (1 << 56)))
    if mantissa >> 56:  # rounded up out of the fraction
        mantissa >>= 4
        exponent += 1
    if not (-64 <= exponent <= 63):
        raise ValueError(f"{value!r} is outside the range a GDSII real can carry")
    return bytes([sign | (exponent + 64)]) + mantissa.to_bytes(7, "big")


def bytes_to_real8(raw: bytes) -> float:
    if len(raw) != 8:
        raise ValueError(f"a GDSII real is 8 bytes, got {len(raw)}")
    sign = -1.0 if raw[0] & 0x80 else 1.0
    exponent = (raw[0] & 0x7F) - 64
    mantissa = int.from_bytes(raw[1:8], "big")
    return sign * mantissa / (1 << 56) * (16.0 ** exponent)


# ------------------------------------------------------------------------ records

def _record(rtype: int, dtype: int, payload: bytes = b"") -> bytes:
    if len(payload) % 2:
        payload += b"\x00"
    length = len(payload) + 4
    if length > 0xFFFF:
        raise ValueError(
            f"GDSII record 0x{rtype:02x} would be {length} bytes; the length field is "
            f"16 bits, so this has to be split and this writer does not split")
    return struct.pack(">HBB", length, rtype, dtype) + payload


def _ascii(rtype: int, text: str) -> bytes:
    raw = text.encode("ascii", errors="strict")
    return _record(rtype, _ASCII, raw)


def _stamp(rtype: int) -> bytes:
    return _record(rtype, _INT2, struct.pack(">12h", *(_EPOCH * 2)))


# ------------------------------------------------------------------------- writing

def _check(value: int, poly: Poly, axis: str) -> int:
    if not (COORD_MIN <= value <= COORD_MAX):
        raise ValueError(
            f"{axis} = {value} nm on layer {poly.layer!r} owned by {poly.owner!r} does not "
            f"fit the 4-byte signed GDSII coordinate field "
            f"([{COORD_MIN}, {COORD_MAX}] nm, i.e. +/- 2.147 m). Writing it would wrap it "
            f"to a plausible-looking coordinate metres away, so it is refused instead.")
    return value


def library_bytes(layout: Layout, *, name: str = "QCCD") -> bytes:
    """The whole library as bytes: one flat structure holding every placed polygon."""
    tech: Technology = layout.tech
    numbers = {lay.name: (lay.gds_layer, lay.gds_datatype) for lay in tech.layers}
    out = [
        _record(_HEADER, _INT2, struct.pack(">h", 600)),
        _stamp(_BGNLIB),
        _ascii(_LIBNAME, name),
        _record(_UNITS, _REAL8,
                real8_to_bytes(DB_UNIT_M / USER_UNIT_M) + real8_to_bytes(DB_UNIT_M)),
        _stamp(_BGNSTR),
        _ascii(_STRNAME, name),
    ]
    for poly in layout.flatten():
        try:
            layer, datatype = numbers[poly.layer]
        except KeyError:
            raise KeyError(
                f"polygon owned by {poly.owner!r} is on layer {poly.layer!r}, which "
                f"technology {tech.name!r} does not declare; it has no GDS number and "
                f"guessing one would put metal on a mask nobody asked for") from None
        pts = poly.points
        closed = list(pts) + [pts[0]]
        if len(closed) < 4:
            raise ValueError(f"a GDSII boundary needs at least 3 distinct points, "
                             f"{poly.owner!r} has {len(pts)}")
        xy = b"".join(struct.pack(">ii", _check(x, poly, "x"), _check(y, poly, "y"))
                      for x, y in closed)
        out.append(_record(_BOUNDARY, _NO_DATA))
        out.append(_record(_LAYER, _INT2, struct.pack(">h", layer)))
        out.append(_record(_DATATYPE, _INT2, struct.pack(">h", datatype)))
        out.append(_record(_XY, _INT4, xy))
        out.append(_record(_ENDEL, _NO_DATA))
    out.append(_record(_ENDSTR, _NO_DATA))
    out.append(_record(_ENDLIB, _NO_DATA))
    return b"".join(out)


def write_gds(layout: Layout, path: str | Path, *, name: str = "QCCD") -> Path:
    p = Path(path)
    p.write_bytes(library_bytes(layout, name=name))
    return p


# ------------------------------------------------------------------------- reading

@dataclass(frozen=True)
class GdsBoundary:
    """One BOUNDARY, as it came out of the file: numbers, not names."""

    layer: int
    datatype: int
    #: flat and CLOSED, exactly as stored -- the first point is repeated at the end
    xy: tuple[int, ...]

    @property
    def points(self) -> tuple[tuple[int, int], ...]:
        it = iter(self.xy)
        return tuple(zip(it, it))

    @property
    def open_xy(self) -> tuple[int, ...]:
        """The coordinates with the closing repeat dropped, which is `Poly.xy`'s form."""
        return self.xy[:-2]


@dataclass(frozen=True)
class GdsLibrary:
    name: str
    user_unit_per_db: float
    db_unit_m: float
    structures: tuple[str, ...]
    boundaries: tuple[GdsBoundary, ...]


def read_gds(source: bytes | str | Path) -> GdsLibrary:
    """A GDSII reader that knows records and nothing else.

    Deliberately ignorant: it does not know what a layout is, does not import `shapes`,
    and does no geometry.  That is what makes it usable as evidence about the writer --
    the two halves share no code beyond the 8-byte real, which has its own test.
    """
    raw = source if isinstance(source, bytes) else Path(source).read_bytes()
    pos = 0
    name = ""
    unit_ratio = db_unit = 0.0
    structures: list[str] = []
    boundaries: list[GdsBoundary] = []
    pending: dict = {}
    while pos < len(raw):
        if pos + 4 > len(raw):
            raise ValueError(f"truncated record header at byte {pos}")
        length, rtype, dtype = struct.unpack_from(">HBB", raw, pos)
        if length < 4 or length % 2:
            raise ValueError(f"record at byte {pos} declares a length of {length}, which "
                             f"is not an even number of at least 4")
        if pos + length > len(raw):
            raise ValueError(f"record at byte {pos} runs {length} bytes past the end")
        body = raw[pos + 4: pos + length]
        pos += length
        if rtype == _LIBNAME:
            name = body.rstrip(b"\x00").decode("ascii")
        elif rtype == _UNITS:
            unit_ratio = bytes_to_real8(body[0:8])
            db_unit = bytes_to_real8(body[8:16])
        elif rtype == _STRNAME:
            structures.append(body.rstrip(b"\x00").decode("ascii"))
        elif rtype == _BOUNDARY:
            pending = {}
        elif rtype == _LAYER:
            pending["layer"] = struct.unpack(">h", body)[0]
        elif rtype == _DATATYPE:
            pending["datatype"] = struct.unpack(">h", body)[0]
        elif rtype == _XY:
            if len(body) % 8:
                raise ValueError("an XY payload must be a whole number of (x, y) pairs")
            pending["xy"] = struct.unpack(f">{len(body) // 4}i", body)
        elif rtype == _ENDEL:
            if pending:
                missing = {"layer", "datatype", "xy"} - set(pending)
                if missing:
                    raise ValueError(f"a boundary ended without {sorted(missing)}")
                boundaries.append(GdsBoundary(pending["layer"], pending["datatype"],
                                              tuple(pending["xy"])))
                pending = {}
        elif rtype == _ENDLIB:
            break
    if not structures:
        raise ValueError("no structure in this library")
    return GdsLibrary(name, unit_ratio, db_unit, tuple(structures), tuple(boundaries))
