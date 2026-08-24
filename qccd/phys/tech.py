"""The technology file: every physical dimension, and where each one came from.

This is the sidecar. It is **not** part of `.arch.json` and never will be -- the metal is a
pure function of `(Device, Technology)`, so putting it in the document would add something
to edit, lose on undo, and diff, for no information the pair does not already determine.
`Knowledge/notes/accumulated.yaml:d_technology_is_a_sidecar` records that decision and why.

**Every number carries a source, and the type system will not let you skip it.**  `Dim`
has two fields and both are required; there is no `Dim(41500)`.  That is the whole design.
A width without a page reference is the failure mode this package exists to end, because
the thing it replaces -- `control.wiring.electrodes_per_trap = 24` -- is exactly that.

**Authored numbers say so.**  Not every dimension is published.  A source beginning with
`declared:` marks a number this project chose rather than read, and `Technology.declared()`
lists them, so "how much of this preset is actually cited" is a query rather than an
argument.  The one shipped preset has one.

**Two scales, not one.**  `nm_per_unit_x` and `nm_per_unit_y` are separate `Dim`s with
separate sources.  A single global scale would quietly assert that the transverse rail
spacing of `ring144_24v` -- whose y-extent is exactly 1.0 -- is one axial trap pitch, and
that is a drawing convention, not a physical claim.  The shipped preset sets them equal and
says so; an architect who means something else has to write down what, and cite it.

**Purposes are closed.**  `layer(purpose)` raises on a purpose it does not know and on a
purpose no layer claims.  The alternative -- returning nothing -- would silently drop an
electrode out of the RF sum and report a confident ion height for a trap missing metal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = ["PURPOSES", "Dim", "Layer", "Technology", "load_technology", "preset_names",
           "PRESET_DIR"]

#: What a layer is *for*.  Closed, because `layer(purpose)` has to be able to refuse.
#:
#:   rf       driven at the trap drive frequency; the only purpose the field kernel sums
#:   dc       segmented control electrodes; shape the axial well, invisible to the RF solve
#:   ground   RF ground return / field plate
#:   shim     individually driven micromotion-compensation electrodes outside the RF rails
#:   outline  drawn for the fab tool and for the eye; never metal, never in a field sum
PURPOSES: tuple[str, ...] = ("rf", "dc", "ground", "shim", "outline")

PRESET_DIR = Path(__file__).resolve().parent / "presets"

#: A source string starting with this marks a number this project chose, not one it read.
DECLARED = "declared:"


def _as_int(value: Any, what: str) -> int:
    """An integer, and not a bool or a float that happens to be whole."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{what} must be an int in nanometres, got {type(value).__name__} {value!r}; "
            f"a float here is how a layout stops being reproducible")
    return value


def _as_source(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(
            f"{what} needs a non-empty source. Cite a paper and a line, or write "
            f"{DECLARED!r} followed by why this project chose the number.")
    return value


@dataclass(frozen=True)
class Dim:
    """One length in integer nanometres, and where it came from."""

    nm: int
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "nm", _as_int(self.nm, "Dim.nm"))
        object.__setattr__(self, "source", _as_source(self.source, "Dim.source"))

    @property
    def is_declared(self) -> bool:
        """True when this number was chosen by us rather than read out of a paper."""
        return self.source.startswith(DECLARED)

    def to_json(self) -> dict:
        return {"nm": self.nm, "source": self.source}

    @classmethod
    def from_json(cls, d: Mapping) -> "Dim":
        unknown = set(d) - {"nm", "source"}
        if unknown:
            raise KeyError(f"unknown key(s) in a dim: {sorted(unknown)}")
        return cls(d["nm"], d["source"])


@dataclass(frozen=True)
class Layer:
    """One drawn layer: what it is for, where it sits, and what the fab will accept."""

    name: str
    gds_layer: int
    gds_datatype: int
    purpose: str
    z_nm: int
    thickness_nm: int
    material: str
    min_width_nm: int
    min_gap_nm: int
    source: str

    _INTS = ("gds_layer", "gds_datatype", "z_nm", "thickness_nm", "min_width_nm",
             "min_gap_nm")

    def __post_init__(self) -> None:
        if self.purpose not in PURPOSES:
            raise ValueError(
                f"layer {self.name!r} claims purpose {self.purpose!r}, which is not one of "
                f"{list(PURPOSES)}. Purposes are closed so that layer(purpose) can refuse "
                f"instead of silently returning nothing.")
        for f_name in self._INTS:
            object.__setattr__(self, f_name,
                               _as_int(getattr(self, f_name), f"Layer.{f_name}"))
        object.__setattr__(self, "source", _as_source(self.source, "Layer.source"))
        if not (0 <= self.gds_layer <= 65535 and 0 <= self.gds_datatype <= 65535):
            raise ValueError(
                f"layer {self.name!r}: GDSII layer and datatype are 16-bit, got "
                f"{self.gds_layer}/{self.gds_datatype}")
        if self.min_width_nm <= 0 or self.min_gap_nm <= 0:
            raise ValueError(f"layer {self.name!r}: min_width_nm and min_gap_nm must be "
                             f"positive design rules, got {self.min_width_nm}/"
                             f"{self.min_gap_nm}")

    def to_json(self) -> dict:
        return {"name": self.name, "gds_layer": self.gds_layer,
                "gds_datatype": self.gds_datatype, "purpose": self.purpose,
                "z_nm": self.z_nm, "thickness_nm": self.thickness_nm,
                "material": self.material, "min_width_nm": self.min_width_nm,
                "min_gap_nm": self.min_gap_nm, "source": self.source}

    @classmethod
    def from_json(cls, d: Mapping) -> "Layer":
        known = {"name", "gds_layer", "gds_datatype", "purpose", "z_nm", "thickness_nm",
                 "material", "min_width_nm", "min_gap_nm", "source"}
        unknown = set(d) - known
        if unknown:
            raise KeyError(
                f"unknown key(s) in layer {d.get('name')!r}: {sorted(unknown)}; known "
                f"keys are {sorted(known)}")
        missing = known - set(d)
        if missing:
            raise KeyError(f"layer {d.get('name')!r} is missing {sorted(missing)}")
        return cls(**dict(d))


@dataclass(frozen=True)
class Technology:
    """A process: its dimensions, its layers, and the lattice-to-metal scale."""

    name: str
    description: str
    dims: Mapping[str, Dim]
    layers: tuple[Layer, ...]
    nm_per_unit_x: Dim
    nm_per_unit_y: Dim
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "dims", dict(self.dims))
        object.__setattr__(self, "layers", tuple(self.layers))
        object.__setattr__(self, "source", _as_source(self.source, "Technology.source"))
        for k, v in self.dims.items():
            if not isinstance(v, Dim):
                raise TypeError(f"dim {k!r} is {type(v).__name__}, not a Dim")
        seen_names: set[str] = set()
        seen_gds: set[tuple[int, int]] = set()
        for lay in self.layers:
            if lay.name in seen_names:
                raise ValueError(f"two layers named {lay.name!r}")
            key = (lay.gds_layer, lay.gds_datatype)
            if key in seen_gds:
                raise ValueError(
                    f"layer {lay.name!r} reuses GDS {key}; two layers on one GDS number "
                    f"cannot be told apart by a fab tool")
            seen_names.add(lay.name)
            seen_gds.add(key)
        for axis, d in (("x", self.nm_per_unit_x), ("y", self.nm_per_unit_y)):
            if not isinstance(d, Dim):
                raise TypeError(f"nm_per_unit_{axis} must be a Dim")
            if d.nm <= 0:
                raise ValueError(f"nm_per_unit_{axis} must be positive, got {d.nm}")

    # ------------------------------------------------------------------ lookup

    def dim(self, name: str) -> Dim:
        """One dimension, or a refusal that names the ones that exist."""
        try:
            return self.dims[name]
        except KeyError:
            known = ", ".join(sorted(self.dims)) or "(none)"
            raise KeyError(
                f"{name!r} is not a dimension of technology {self.name!r}. Known "
                f"dimensions: {known}") from None

    def nm(self, name: str) -> int:
        """The value of one dimension, in nanometres."""
        return self.dim(name).nm

    def layer(self, purpose: str) -> Layer:
        """The single layer with this purpose.  Raises on unknown, absent or ambiguous.

        Ambiguity is an error rather than a first-match, because the caller that most
        wants this is the RF sum: two layers claiming `rf` and one of them being picked by
        declaration order is a wrong ion height that looks like a right one.
        """
        if purpose not in PURPOSES:
            raise KeyError(
                f"{purpose!r} is not a layer purpose; known purposes are "
                f"{list(PURPOSES)}")
        hits = [lay for lay in self.layers if lay.purpose == purpose]
        if not hits:
            have = sorted({lay.purpose for lay in self.layers})
            raise KeyError(
                f"technology {self.name!r} has no {purpose!r} layer; it declares "
                f"{have}. Returning nothing here would drop the electrode out of every "
                f"sum that uses it and report a number for a trap missing metal.")
        if len(hits) > 1:
            raise KeyError(
                f"technology {self.name!r} has {len(hits)} layers with purpose "
                f"{purpose!r}: {[h.name for h in hits]}. Which one is meant is not "
                f"something this can guess.")
        return hits[0]

    def layer_by_name(self, name: str) -> Layer:
        for lay in self.layers:
            if lay.name == name:
                return lay
        known = ", ".join(lay.name for lay in self.layers) or "(none)"
        raise KeyError(f"{name!r} is not a layer of {self.name!r}. Known layers: {known}")

    def has_purpose(self, purpose: str) -> bool:
        return any(lay.purpose == purpose for lay in self.layers)

    # ------------------------------------------------------------- provenance

    def declared(self) -> tuple[str, ...]:
        """The dimensions this project chose rather than read, in name order.

        Keeping this queryable is the point: a preset's honesty is a number, not a claim.
        """
        names = [k for k, v in self.dims.items() if v.is_declared]
        for axis, d in (("nm_per_unit_x", self.nm_per_unit_x),
                        ("nm_per_unit_y", self.nm_per_unit_y)):
            if d.is_declared:
                names.append(axis)
        return tuple(sorted(names))

    @property
    def is_isotropic(self) -> bool:
        return self.nm_per_unit_x.nm == self.nm_per_unit_y.nm

    def require_coplanar(self, *purposes: str) -> int:
        """The common `z_nm` of the named layers, or a refusal.

        The field kernel solves one plane.  A stack with metal at two heights is a
        different boundary-value problem, and approximating it by pretending the layers
        are coplanar would be wrong by an amount nothing here can bound -- so it refuses.
        """
        wanted = purposes or ("rf",)
        zs = {p: self.layer(p).z_nm for p in wanted}
        distinct = set(zs.values())
        if len(distinct) > 1:
            raise ValueError(
                f"technology {self.name!r} puts {zs} at different heights; the gapless-"
                f"plane solution is for ONE plane and there is no approximation here that "
                f"can be bounded, so this refuses rather than guessing.")
        z = distinct.pop()
        if z != 0:
            raise ValueError(
                f"technology {self.name!r} places {list(wanted)} at z = {z} nm; the field "
                f"kernel measures the ion height from the electrode plane, so the "
                f"electrode plane must be z = 0.")
        return z

    # ---------------------------------------------------------- serialisation

    def to_json(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "source": self.source,
            "nm_per_unit_x": self.nm_per_unit_x.to_json(),
            "nm_per_unit_y": self.nm_per_unit_y.to_json(),
            "dims": {k: v.to_json() for k, v in sorted(self.dims.items())},
            "layers": [lay.to_json() for lay in self.layers],
        }

    @classmethod
    def from_json(cls, doc: Mapping) -> "Technology":
        known = {"name", "description", "source", "nm_per_unit_x", "nm_per_unit_y",
                 "dims", "layers"}
        unknown = set(doc) - known
        if unknown:
            raise KeyError(
                f"unknown key(s) in technology {doc.get('name')!r}: {sorted(unknown)}; "
                f"known keys are {sorted(known)}")
        missing = known - set(doc)
        if missing:
            raise KeyError(f"technology {doc.get('name')!r} is missing {sorted(missing)}")
        return cls(
            name=doc["name"],
            description=doc["description"],
            dims={k: Dim.from_json(v) for k, v in doc["dims"].items()},
            layers=tuple(Layer.from_json(l) for l in doc["layers"]),
            nm_per_unit_x=Dim.from_json(doc["nm_per_unit_x"]),
            nm_per_unit_y=Dim.from_json(doc["nm_per_unit_y"]),
            source=doc["source"],
        )


def preset_names() -> tuple[str, ...]:
    return tuple(sorted(p.name[: -len(".tech.json")]
                        for p in PRESET_DIR.glob("*.tech.json")))


def load_technology(name_or_path: str | Path) -> Technology:
    """A shipped preset by name, or any `.tech.json` by path."""
    p = Path(name_or_path)
    if p.suffix == ".json" and p.exists():
        return Technology.from_json(json.loads(p.read_text(encoding="utf-8")))
    candidate = PRESET_DIR / f"{name_or_path}.tech.json"
    if candidate.exists():
        return Technology.from_json(json.loads(candidate.read_text(encoding="utf-8")))
    known = ", ".join(preset_names()) or "(none)"
    raise FileNotFoundError(
        f"no technology {str(name_or_path)!r}: it is not a readable .tech.json path and "
        f"not a shipped preset. Presets: {known}")
