"""The leaf library: build each leaf master once, characterize it, cache it on disk.

Characterizing `mem_bb72` means compiling a syndrome round and replaying six programs
through the verifier -- about ten seconds, against microseconds for everything a parent
does with the result.  So a leaf is built at most once per content hash: the builder's
name, its parameters, the code's check matrices, and `LEAF_VERSION`, which is bumped
whenever a builder changes what it emits.  The cache holds the characterized master and
the leaf data (device and programs), exactly what the page loads.

Ops a program needs that the default build lacks -- a messenger visit to logical 7 in
the X basis, say -- are compiled into the cached build on demand and the cache entry is
rewritten, so they too are paid for once.
"""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from .characterize import characterize, leaf_data
from .codes import CSSCode
from .leaves.common import LeafBuild
from .model import Master, Transfer

__all__ = ["LeafLibrary", "LEAF_VERSION"]

#: bump when any leaf builder or `characterize` changes what it produces
LEAF_VERSION = 13


#: the modules every place builder shares: a change in one of them changes every place
_SHARED = ("leaves/bay.py", "leaves/common.py", "leaves/places.py", "surface.py",
           "characterize.py")


def _source_key(*parts: str) -> str:
    """A content hash of the sources a leaf is built from.

    A leaf is cached under this, so editing a builder invalidates exactly the leaves that
    builder makes -- no more, and never less.  `LEAF_VERSION` stays as the manual override
    for changes the hash cannot see (a dependency further away)."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", "replace"))
    root = Path(__file__).resolve().parent
    for rel in _SHARED:
        try:
            h.update((root / rel).read_bytes())
        except OSError:                  # installed without sources: fall back to the version
            h.update(rel.encode())
    return h.hexdigest()[:12]


def _code_key(code: CSSCode) -> str:
    h = hashlib.sha256()
    h.update(json.dumps([code.name, code.n, list(code.hx), list(code.hz)]).encode())
    return h.hexdigest()[:12]


class LeafLibrary:
    """Characterized leaf masters by name, with their leaf data, built lazily."""

    def __init__(self, cache_dir: str | Path | None = None, *, rebuild: bool = False,
                 log=print):
        self.cache = Path(cache_dir) if cache_dir else None
        self.rebuild = rebuild
        self.log = log or (lambda *a, **k: None)
        self.masters: dict[str, Master] = {}
        self.data: dict[str, dict] = {}
        self.builds: dict[str, LeafBuild] = {}
        self._channel: dict[tuple[int, int], dict] = {}

    # -- cache -----------------------------------------------------------------

    def _path(self, name: str, key: str) -> Path | None:
        return self.cache / f"{name}-{key}.json" if self.cache else None

    def _load(self, name: str, key: str) -> bool:
        p = self._path(name, key)
        if self.rebuild or p is None or not p.exists():
            return False
        doc = json.loads(p.read_text(encoding="utf-8"))
        self.masters[name] = Master.from_json(doc["master"])
        self.data[name] = doc["data"]
        return True

    def _save(self, name: str, key: str) -> None:
        p = self._path(name, key)
        if p is None:
            return
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps({"version": LEAF_VERSION, "master":
                                 self.masters[name].to_json(), "data": self.data[name]}),
                     encoding="utf-8")

    def _get(self, name: str, key: str, build_fn) -> Master:
        key = f"v{LEAF_VERSION}-{key}"
        if name in self.masters:
            return self.masters[name]
        if not self._load(name, key):
            self.log(f"  characterizing {name} ...")
            build = build_fn()
            build.master_name = build.master.name
            self.builds[name] = build
            self.masters[name] = characterize(build)
            self.data[name] = leaf_data(build)
            self._save(name, key)
        self._keys = getattr(self, "_keys", {})
        self._keys[name] = key
        return self.masters[name]

    # -- the verified places (qccd.gadget.leaves.places) --------------------------

    def place(self, name: str) -> Master:
        """A place of the verified surface-code library, by master name."""
        from .leaves.classical import CLASSICAL
        from .leaves.places import PLACES
        if name in CLASSICAL:
            return self.classical(name)
        if name not in PLACES:
            raise KeyError(f"no place {name!r} "
                           f"(have: {', '.join(list(PLACES) + list(CLASSICAL))})")
        import inspect
        try:
            src = inspect.getsource(PLACES[name])
        except OSError:
            src = name
        return self._get(name, f"place-{_source_key(name, src)}", PLACES[name])

    def classical(self, name: str) -> Master:
        """A place that holds no ions (decoder, classical memory): built, not replayed.

        A classical place has no TSIR, so `characterize` has nothing to measure; the
        builder returns the master and its leaf data directly.  The decoder is handed the
        syndrome-extraction gadget's own circuit, so its lookup table is derived from the
        very circuit whose syndromes it reads."""
        from .leaves.classical import CLASSICAL
        if name in self.masters:
            return self.masters[name]
        import inspect
        from .leaves import classical as classical_mod
        key = (f"v{LEAF_VERSION}-classical-"
               f"{_source_key(name, inspect.getsource(classical_mod))}")
        if not self._load(name, key):
            self.log(f"  building {name} (classical) ...")
            kwargs = {}
            if name == "dec_d3":
                from ..ir.tsir import TSIR
                from .logic.circuit import from_tsir
                self.place("se_d3")
                kwargs["se_circuit"] = from_tsir(
                    TSIR.from_json(self.data["se_d3"]["programs"]["se.1"]))
            master, data = CLASSICAL[name](**kwargs)
            self.masters[name], self.data[name] = master, data
            self._save(name, key)
        self._keys = getattr(self, "_keys", {})
        self._keys[name] = key
        return self.masters[name]

    def places(self) -> dict[str, Master]:
        from .leaves.classical import CLASSICAL
        from .leaves.places import PLACES
        return {name: self.place(name) for name in list(PLACES) + list(CLASSICAL)}

    # -- the beta leaves ---------------------------------------------------------

    def memory(self, code: CSSCode) -> Master:
        from .leaves.memory import memory
        return self._get(f"mem_{code.name}", _code_key(code), lambda: memory(code))

    def station(self, code: CSSCode) -> Master:
        from .leaves.transport import station
        return self._get(f"tcx{code.n}", f"{code.n}-{code.name}",
                         lambda: station(code.n, code=code.name))

    def reservoir(self, k: int = 4) -> Master:
        from .leaves.transport import reservoir
        return self._get(f"res{k}", str(k), lambda: reservoir(k))

    def junction(self) -> Master:
        from .leaves.transport import junction
        return self._get("xjunc", "1", lambda: junction())

    def factory(self) -> Master:
        from .leaves.factory import factory_t15
        return self._get("fac_t15", "1", factory_t15)

    def ensure_visit(self, code: CSSCode, logical: int, basis: str) -> str:
        """The memory op `visit.<basis><logical>`, compiled and characterized if missing."""
        name = f"mem_{code.name}"
        master = self.memory(code)
        key = f"visit.{basis}{logical}"
        if key in master.ops:
            return key
        from .leaves.memory import add_visit, memory
        build = self.builds.get(name)
        if build is None:
            self.log(f"  rebuilding {name} to add {key} ...")
            build = memory(code, visits=[])
            build.master_name = build.master.name
            if build.home != self.data[name]["home"]:
                raise RuntimeError(
                    f"{name}: rebuilding it placed the ions differently from the cached "
                    f"build, so a new visit would not match the cached cycle; rerun with "
                    f"--rebuild")
            self.builds[name] = build
        add_visit(build, logical, basis)
        scratch = copy.copy(build.master)
        scratch.ops = {}
        sub = LeafBuild(master=scratch, machine=build.machine,
                        programs={key: build.programs[key]}, titles=build.titles,
                        notes=build.notes, params=build.params, home=build.home,
                        roles=build.roles, expect_end=build.expect_end)
        characterize(sub)
        op = scratch.ops[key]
        master.ops[key] = op
        self.data[name]["programs"][key] = build.programs[key].to_json()
        self.data[name]["times"][key] = sub.times[key]
        self.data[name]["roles"]["m0"] = "messenger"
        self._save(name, self._keys[name])
        return key

    # -- channels --------------------------------------------------------------------

    def channel_timing(self, length: int, count: int) -> dict:
        """What carrying `count` ions along `length` sites takes, from a replayed conveyor.

        Memoized per `(length, count)`: a design has few distinct channel lengths, so the
        reference program is built and verified a handful of times, and every carry in the
        schedule is priced from a replay rather than from a formula written beside one.
        """
        key = (max(1, int(length)), max(1, int(count)))
        if key not in self._channel:
            from .leaves.transport import channel
            build = channel(*key)
            master = characterize(build)
            op = master.ops["carry"]
            din = op.transfer("w", "in")
            dout = op.transfer("e", "out")
            transit = max(o - i for i, o in zip(din.times_us, dout.times_us))
            self._channel[key] = {
                "status": op.status, "duration_us": op.duration_us, "transit_us": transit,
                "quanta": dout.quanta, "rules_failed": op.rules["failed"],
            }
        return self._channel[key]

    def channel_references(self) -> dict:
        return {f"{L}x{k}": v for (L, k), v in sorted(self._channel.items())}


def transfer_times(t: Transfer) -> list[float]:
    return list(t.times_us)
