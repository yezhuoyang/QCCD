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

**The collaborator's technology rules are fields with minimums.**  A reviewer of the
hardware model asked for six named variables -- `n_dc_pairs`, `w_rf`, `w_dc`, `l_dc`,
`g_dc`, `g_rf` -- each with a minimum and a default.  They are `TECH_RULES`, they are
checked when a file loads, and a breach is a `TechnologyError` that names the field, the
value and the minimum.  Three of them are the dimensions this file already had under
other names (`w_dc` is `dc_width`, `g_dc` is `gap`, `l_dc` is `dc_pitch - gap`), so the
rules are read through `Rule.read` rather than duplicated as second copies that could
drift.

**A minimum can be waived, by name, in writing.**  `waivers` maps a rule's field to the
reason it does not apply to THIS technology, and only a waived rule may be under its
minimum.  The one shipped waiver is `eth_junction_2201.12579`'s `g_rf`: reproducing a
published trap at the 5 um gap its authors fabricated is not a new design, and inflating
it to the 8 um minimum would draw metal that paper never had and misreport its geometry.
A waiver for a rule the technology already meets is refused too -- a stale waiver is a
claim nobody checked.

**Purposes are closed.**  `layer(purpose)` raises on a purpose it does not know and on a
purpose no layer claims.  The alternative -- returning nothing -- would silently drop an
electrode out of the RF sum and report a confident ion height for a trap missing metal.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

__all__ = ["PURPOSES", "Dim", "Count", "Layer", "Rule", "TECH_RULES", "TechnologyError",
           "Technology", "load_technology", "preset_names", "PRESET_DIR"]

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
class Count:
    """One dimensionless count, and where it came from.

    `Dim` cannot carry it: `n_dc_pairs` is three electrode PAIRS, not three nanometres,
    and putting it in `dims` would make `nm("n_dc_pairs")` return 3 nm to the first caller
    who did not read this line.
    """

    n: int
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "n", _as_int(self.n, "Count.n"))
        object.__setattr__(self, "source", _as_source(self.source, "Count.source"))

    @property
    def is_declared(self) -> bool:
        return self.source.startswith(DECLARED)

    def to_json(self) -> dict:
        return {"n": self.n, "source": self.source}

    @classmethod
    def from_json(cls, d: Mapping) -> "Count":
        unknown = set(d) - {"n", "source"}
        if unknown:
            raise KeyError(f"unknown key(s) in a count: {sorted(unknown)}")
        return cls(d["n"], d["source"])


class TechnologyError(ValueError):
    """A technology that breaks one of the collaborator's minimums, named."""


@dataclass(frozen=True)
class Rule:
    """One of the collaborator's technology variables: a minimum, a default, a source.

    `read` is a function of a `Technology` rather than a field name because three of the
    six already existed here under other names.  Storing a second copy under the
    collaborator's name would let the two drift and make the file's own arithmetic
    ambiguous -- so the rule reads the dimension that is already there, and the mapping is
    written down once, here.
    """

    field: str
    minimum: int
    default: int
    unit: str
    reads: str
    what: str
    read: Callable[["Technology"], int | None]


def _dim_nm(name: str):
    def read(tech: "Technology") -> int | None:
        d = tech.dims.get(name)
        return None if d is None else d.nm
    return read


def _pad_length(tech: "Technology") -> int | None:
    """`l_dc`: how long one control electrode is, which is the pitch less the gap."""
    pitch, gap = tech.dims.get("dc_pitch"), tech.dims.get("gap")
    if pitch is None or gap is None:
        return None
    return pitch.nm - gap.nm


#: The collaborator's technology rules.  Each is a named variable with a minimum and a
#: default; `load_technology` refuses a file that breaks one unless the file waives it by
#: name.  The defaults are the reviewer's own numbers and are what
#: `presets/surface_default.tech.json` is built from.
TECH_RULES: tuple[Rule, ...] = (
    Rule("n_dc_pairs", 3, 3, "pairs", "n_dc_pairs",
         "pairs of DC electrodes beneath every trapping site",
         lambda t: t.n_dc_pairs_count.n),
    Rule("w_rf", 30_000, 60_000, "nm", "dims['w_rf']",
         "RF rail width; the collaborator's default is 1.2 * w_dc", _dim_nm("w_rf")),
    Rule("w_dc", 30_000, 50_000, "nm", "dims['dc_width']",
         "width of one DC control electrode", _dim_nm("dc_width")),
    Rule("l_dc", 30_000, 50_000, "nm", "dims['dc_pitch'] - dims['gap']",
         "length of one DC control electrode along the rail", _pad_length),
    Rule("g_dc", 5_000, 8_000, "nm", "dims['gap']",
         "gap between two DC electrodes", _dim_nm("gap")),
    Rule("g_rf", 8_000, 10_000, "nm", "dims['g_rf']",
         "clearance from RF metal to anything else", _dim_nm("g_rf")),
)

RULES_BY_FIELD = {r.field: r for r in TECH_RULES}

#: What `n_dc_pairs` is when a technology file does not say.  Every other rule reads a
#: dimension the file must already carry; this one is the only new number, so it is the
#: only one that can be absent, and the collaborator's own minimum is its default.
DEFAULT_N_DC_PAIRS = Count(
    3, DECLARED + " no n_dc_pairs in this technology file, so the collaborator's own "
    "minimum of three DC electrode pairs per trapping site is assumed")


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
    #: how many pairs of DC electrodes every trapping site gets.  Optional in the file;
    #: absent means `DEFAULT_N_DC_PAIRS`, which is the collaborator's own minimum.
    n_dc_pairs_count: Count | None = None
    #: rule field -> why that minimum does not apply to THIS technology.  The only way a
    #: number below a minimum loads at all, and it has to be written down to be used.
    waivers: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dims", dict(self.dims))
        object.__setattr__(self, "layers", tuple(self.layers))
        object.__setattr__(self, "source", _as_source(self.source, "Technology.source"))
        object.__setattr__(self, "waivers", dict(self.waivers))
        if self.n_dc_pairs_count is None:
            object.__setattr__(self, "n_dc_pairs_count", DEFAULT_N_DC_PAIRS)
        if not isinstance(self.n_dc_pairs_count, Count):
            raise TypeError("Technology.n_dc_pairs_count must be a Count")
        # `g_rf` is the one rule whose dimension this file did not used to have.  A
        # technology written before it is read as clearing RF by the DC gap -- which is
        # what the builder did do -- rather than being refused for a field that did not
        # exist when it was written.
        if "g_rf" not in self.dims and "gap" in self.dims:
            self.dims["g_rf"] = Dim(
                self.dims["gap"].nm,
                "derived: this technology file declares no g_rf, so RF metal is kept "
                "clear of other metal by the same gap as two DC electrodes")
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
        self._check_rules()

    # ----------------------------------------------- the collaborator's minimums

    def _check_rules(self) -> None:
        """Every rule, against its minimum, at load time.  Waived only in writing."""
        unknown = set(self.waivers) - set(RULES_BY_FIELD)
        if unknown:
            raise TechnologyError(
                f"technology {self.name!r} waives {sorted(unknown)}, which is not a "
                f"technology rule; the rules are {sorted(RULES_BY_FIELD)}")
        for f_name, why in self.waivers.items():
            if not isinstance(why, str) or not why.strip():
                raise TechnologyError(
                    f"technology {self.name!r} waives {f_name!r} with no reason. A "
                    f"minimum is waived in writing or not at all.")
        for rule in TECH_RULES:
            value = rule.read(self)
            if value is None:
                continue          # the dimension it reads is not in this file at all
            waiver = self.waivers.get(rule.field)
            if value < rule.minimum:
                if waiver:
                    continue
                raise TechnologyError(
                    f"technology {self.name!r}: {rule.field} = {value} {rule.unit} is "
                    f"below the minimum of {rule.minimum} {rule.unit} "
                    f"({rule.what}; read from {rule.reads}, default "
                    f"{rule.default} {rule.unit}). Raise it, or waive {rule.field!r} in "
                    f"the file's \"waivers\" with the reason it does not apply here.")
            if waiver:
                raise TechnologyError(
                    f"technology {self.name!r} waives {rule.field!r}, but {rule.field} = "
                    f"{value} {rule.unit} already meets its {rule.minimum} {rule.unit} "
                    f"minimum. A waiver nobody needs is a claim nobody checked; delete it.")

    @property
    def n_dc_pairs(self) -> int:
        """Pairs of DC electrodes every trapping site must have beneath it."""
        return self.n_dc_pairs_count.n

    @property
    def g_rf(self) -> int:
        """Clearance from RF metal to anything else, in nm."""
        return self.nm("g_rf")

    @property
    def g_dc(self) -> int:
        """Gap between two DC electrodes, in nm."""
        return self.nm("gap")

    @property
    def w_dc(self) -> int:
        return self.nm("dc_width")

    @property
    def l_dc(self) -> int:
        return self.nm("dc_pitch") - self.nm("gap")

    @property
    def w_rf(self) -> int:
        return self.nm("w_rf")

    def rule_table(self) -> tuple[tuple[str, int, int, str, int | None], ...]:
        """(field, minimum, default, unit, this technology's value), in rule order."""
        return tuple((r.field, r.minimum, r.default, r.unit, r.read(self))
                     for r in TECH_RULES)

    def waived(self) -> tuple[str, ...]:
        """The rules this technology is under, by name, in order."""
        return tuple(sorted(self.waivers))

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
                        ("nm_per_unit_y", self.nm_per_unit_y),
                        ("n_dc_pairs", self.n_dc_pairs_count)):
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
            "n_dc_pairs": self.n_dc_pairs_count.to_json(),
            "dims": {k: v.to_json() for k, v in sorted(self.dims.items())},
            "layers": [lay.to_json() for lay in self.layers],
            **({"waivers": dict(sorted(self.waivers.items()))} if self.waivers else {}),
        }

    @classmethod
    def from_json(cls, doc: Mapping) -> "Technology":
        known = {"name", "description", "source", "nm_per_unit_x", "nm_per_unit_y",
                 "dims", "layers"}
        #: written after `known` was closed, so absent is legal and means the documented
        #: default -- an older .tech.json still loads, and says what it said before
        optional = {"n_dc_pairs", "waivers"}
        unknown = set(doc) - known - optional
        if unknown:
            raise KeyError(
                f"unknown key(s) in technology {doc.get('name')!r}: {sorted(unknown)}; "
                f"known keys are {sorted(known | optional)}")
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
            n_dc_pairs_count=(Count.from_json(doc["n_dc_pairs"])
                              if "n_dc_pairs" in doc else None),
            waivers=dict(doc.get("waivers") or {}),
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
