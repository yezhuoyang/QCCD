"""Benchmark suites: every (circuit, device) pair a submitted compiler is graded on.

A SUITE is an immutable directory, like a board's task release:

    suites/<suite>@<release>/suite.json       the manifest: circuits, devices, pairs, limits, metrics
                             physics.json     the physics every pair is scored under
                             circuits/<c>.qasm, <c>.detectors.json (memory experiments)
                             devices/<d>.arch.json
                             baseline.json    the reference compiler's result on every public pair

Its identity is the digest of `suite.json`'s canonical form, and every file it names is pinned
by digest.  A published suite never changes; a new one is `<suite>@2`.

Two splits.  The PUBLIC pairs are in the directory, so anyone can run them locally.  The
HIDDEN pairs are generated from a seed only the official server holds (`QCCD_HIDDEN_SEED`):
fresh random Clifford circuits and generated devices of fresh sizes, so a compiler tuned to
the public pairs is still measured on ones it has never seen.  Without the seed there are none.

Every pair is scored under the suite's physics (the devices' fixed blocks are replaced by
`physics.json`), so a speedup is the compiler's and never a difference in the physics tables.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

__all__ = ["Suite", "Pair", "SUITES_DIR", "find_suite", "list_suites", "SuiteError", "hidden_pairs",
           "circuit_kind"]

SUITES_DIR = Path(__file__).resolve().parent / "suites"
FIXED_BLOCKS = ("primitives", "heating", "species", "budget")


class SuiteError(ValueError):
    pass


def _sha(b: bytes) -> str:
    return "sha256:" + hashlib.sha256(b).hexdigest()


@dataclass(frozen=True)
class Pair:
    id: str                    # "<circuit>@<device>"
    circuit: str
    device: str
    split: str                 # public | hidden
    qasm: str
    device_doc: Mapping
    kind: str                  # memory | clifford | general
    detectors: Mapping | None  # the memory experiment's detector spec (qccd.detectors)
    n_qubits: int

    def to_json(self) -> dict:
        return {"id": self.id, "circuit": self.circuit, "device": self.device, "split": self.split,
                "kind": self.kind, "qubits": self.n_qubits}


def circuit_kind(qasm: str, detectors: Mapping | None) -> str:
    """memory: a QEC memory experiment (graded by its detectors); clifford: every gate is
    Clifford (graded by the stabilizer tableau); general: anything else (exact unitary, small)."""
    if detectors:
        return "memory"
    clifford = {"h", "s", "sdg", "x", "y", "z", "cx", "cz", "cy", "swap", "id", "sx", "sxdg",
                "measure", "reset", "barrier"}
    for line in qasm.splitlines():
        w = line.strip().split("(")[0].split(" ")[0]
        if not w or w.startswith("//") or w in ("OPENQASM", "include", "qreg", "creg"):
            continue
        if w not in clifford:
            return "general"
    return "clifford"


def n_qubits(qasm: str) -> int:
    import re
    return sum(int(m.group(1)) for m in re.finditer(r"qreg\s+\w+\[(\d+)\]", qasm))


@dataclass(frozen=True)
class Suite:
    root: Path
    manifest: dict

    @property
    def id(self) -> str:
        return f"{self.manifest['suite']}@{self.manifest['release']}"

    @property
    def digest(self) -> str:
        from ..workspace.jsonsafe import digest
        return digest(self.manifest)

    @property
    def title(self) -> str:
        return str(self.manifest.get("title") or self.id)

    def physics(self) -> dict:
        return json.loads((self.root / self.manifest["physics"]["file"]).read_text(encoding="utf-8"))

    def baseline(self) -> dict:
        ref = self.manifest.get("baseline")
        if not ref:
            return {}
        return json.loads((self.root / ref["file"]).read_text(encoding="utf-8")).get("pairs", {})

    def device_doc(self, name: str) -> dict:
        d = next(x for x in self.manifest["devices"] if x["name"] == name)
        doc = json.loads((self.root / d["file"]).read_text(encoding="utf-8"))
        return with_physics(doc, self.physics())

    def pairs(self, split: str = "public", *, hidden_seed: str | None = None,
              only: list[str] | None = None) -> list[Pair]:
        circ = {c["name"]: c for c in self.manifest["circuits"]}
        out = []
        if split in ("public", "all"):
            for p in self.manifest["pairs"]:
                c = circ[p["circuit"]]
                qasm = (self.root / c["file"]).read_text(encoding="utf-8")
                det = (json.loads((self.root / c["detectors"]["file"]).read_text(encoding="utf-8"))
                       if c.get("detectors") else None)
                out.append(Pair(f"{p['circuit']}@{p['device']}", p["circuit"], p["device"], "public", qasm,
                                self.device_doc(p["device"]), c["kind"], det, int(c["qubits"])))
        if split in ("hidden", "all") and hidden_seed:
            out += hidden_pairs(hidden_seed, self.physics(), count=int((self.manifest.get("hidden") or {})
                                                                      .get("count", 0)))
        if only:
            want = set(only)
            out = [p for p in out if p.id in want or p.circuit in want or p.device in want]
        return out

    def summary(self) -> dict:
        """What the official service lists for this suite (the shape of a task summary)."""
        m = self.manifest
        return {"id": self.id, "digest": self.digest, "title": m.get("title"), "track": m.get("track"),
                "kind": "compiler_suite", "about": m.get("description"), "rank_by": m.get("rank_by"),
                "metrics": m.get("metrics"), "pairs": len(m["pairs"]),
                "circuits": [c["name"] for c in m["circuits"]], "devices": [d["name"] for d in m["devices"]],
                "hidden_pairs": int((m.get("hidden") or {}).get("count", 0)), "limits": m.get("limits"),
                "contract": m.get("contract"), "ler": m.get("ler")}

    def board(self) -> dict:
        m = self.manifest
        return {"title": self.title, "about": m.get("description"), "rank_by": m.get("rank_by"), "id": self.id,
                "track": "compiler", "pairs": len(m["pairs"])}

    def verify_files(self) -> None:
        refs = [self.manifest["physics"]] + ([self.manifest["baseline"]] if self.manifest.get("baseline") else [])
        for c in self.manifest["circuits"]:
            refs.append(c)
            if c.get("detectors"):
                refs.append(c["detectors"])
        refs += list(self.manifest["devices"])
        for ref in refs:
            p = self.root / ref["file"]
            if not p.is_file():
                raise SuiteError(f"{self.id}: {ref['file']} is missing")
            if _sha(p.read_bytes()) != ref["digest"]:
                raise SuiteError(f"{self.id}: {ref['file']} does not match its pinned digest")

    @classmethod
    def load(cls, root: Path) -> "Suite":
        root = Path(root)
        man = json.loads((root / "suite.json").read_text(encoding="utf-8"))
        if man.get("kind") != "qccd.compiler_suite" or man.get("version") != 1:
            raise SuiteError(f"{root}: not a qccd.compiler_suite v1")
        s = cls(root=root, manifest=man)
        s.verify_files()
        return s


def with_physics(doc: Mapping, physics: Mapping) -> dict:
    out = dict(doc)
    for b in FIXED_BLOCKS:
        if b in physics:
            out[b] = physics[b]
    return out


def list_suites(extra: Path | None = None) -> list[Suite]:
    out = []
    for base in [SUITES_DIR] + ([Path(extra)] if extra else []):
        if base.is_dir():
            for d in sorted(base.iterdir()):
                if (d / "suite.json").is_file():
                    out.append(Suite.load(d))
    return out


def find_suite(ref: str | None = None, extra: Path | None = None) -> Suite:
    """A suite by id (`suite@1`), by name (newest release), or the newest of all when unnamed."""
    suites = list_suites(extra)
    if not suites:
        raise SuiteError("no benchmark suite is installed")
    if not ref:
        return max(suites, key=lambda s: int(s.manifest["release"]))
    hits = [s for s in suites if s.id == ref or s.manifest["suite"] == ref or s.title.lower() == str(ref).lower()]
    if not hits:
        raise SuiteError(f"no suite {ref!r}; there is: " + ", ".join(s.id for s in suites))
    return max(hits, key=lambda s: int(s.manifest["release"]))


# ---------------------------------------------------------------------- the hidden split

def random_clifford(n: int, depth: int, rng: random.Random) -> str:
    """A random Clifford circuit: layers of H/S on random qubits and CX on random disjoint pairs."""
    s = f'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[{n}];\ncreg c[{n}];\n'
    for _ in range(depth):
        qs = list(range(n))
        rng.shuffle(qs)
        for q in qs[: max(1, n // 3)]:
            s += f"{rng.choice(['h', 's', 'sdg', 'x'])} q[{q}];\n"
        rng.shuffle(qs)
        for k in range(0, n - 1, 2):
            if rng.random() < 0.6:
                s += f"cx q[{qs[k]}],q[{qs[k + 1]}];\n"
    for q in range(n):
        s += f"measure q[{q}] -> c[{q}];\n"
    return s


def hidden_pairs(seed: str, physics: Mapping, *, count: int) -> list[Pair]:
    """`count` pairs no one has seen, determined by `seed`: fresh random Clifford circuits on
    generated devices of fresh sizes.  The same seed always gives the same pairs (so a regrade is
    comparable); a different seed gives a different split."""
    from ..api import Machine
    rng = random.Random(hashlib.sha256(f"qccd-hidden:{seed}".encode()).hexdigest())
    out = []
    for i in range(count):
        n = rng.randint(6, 14)
        qasm = random_clifford(n, rng.randint(4, 10), rng)
        kind = rng.choice(["grid", "ring"])
        if kind == "grid":
            a, b = rng.randint(3, 6), rng.randint(3, 6)
            m, dname = Machine.grid(a, b, name=f"hgrid{a}x{b}"), f"hidden_grid{a}x{b}_{i}"
        else:
            w = rng.choice([8, 12, 16, 24])
            v = rng.choice([v for v in (4, 6, 8, 12) if w % v == 0] or [4])
            m, dname = Machine.ring(w, 2, v, name=f"hring{w}"), f"hidden_ring{w}v{v}_{i}"
        doc = with_physics(m.arch.to_json(), physics)
        cname = f"hidden_clifford{n}_{i}"
        out.append(Pair(f"{cname}@{dname}", cname, dname, "hidden", qasm, doc, "clifford", None, n))
    return out


# ---------------------------------------------------------------------- building a suite (maintainers)

def _put(p: Path, text: str) -> None:
    """Write `text` as UTF-8 with LF line ends on every platform.  The files are pinned by
    digest, and `write_text` writes CRLF on Windows, so a suite built there would not match."""
    p.write_bytes(text.replace("\r\n", "\n").encode("utf-8"))


def build_suite(name: str, release: str, *, out_dir: Path, circuits: list[dict], devices: list[dict],
                physics: Mapping, title: str, description: str, limits: Mapping, ler: Mapping,
                hidden_count: int, baseline: Mapping | None = None) -> Path:
    """Write a suite directory.  `circuits`: [{name, qasm, detectors (or None), source}];
    `devices`: [{name, doc, source}].  Every pair is kept: a pair the reference compiler cannot
    compile is exactly where another compiler can do better."""
    from ..workspace.jsonsafe import normalize_numbers
    d = Path(out_dir) / f"{name}@{release}"
    d.mkdir(parents=True, exist_ok=False)
    (d / "circuits").mkdir()
    (d / "devices").mkdir()
    _put(d / "physics.json", json.dumps(normalize_numbers(dict(physics)), indent=1, sort_keys=True) + "\n")
    cs = []
    for c in circuits:
        p = d / "circuits" / f"{c['name']}.qasm"
        _put(p, c["qasm"])
        entry: dict[str, Any] = {"name": c["name"], "file": f"circuits/{c['name']}.qasm", "digest": _sha(p.read_bytes()),
                                 "qubits": n_qubits(c["qasm"]), "kind": circuit_kind(c["qasm"], c.get("detectors")),
                                 "source": c.get("source", "")}
        if c.get("detectors"):
            dp = d / "circuits" / f"{c['name']}.detectors.json"
            _put(dp, json.dumps(c["detectors"], indent=1, sort_keys=True) + "\n")
            entry["detectors"] = {"file": f"circuits/{c['name']}.detectors.json", "digest": _sha(dp.read_bytes())}
        cs.append(entry)
    ds = []
    for dv in devices:
        p = d / "devices" / f"{dv['name']}.arch.json"
        _put(p, json.dumps(dv["doc"], indent=1, sort_keys=True) + "\n")
        ds.append({"name": dv["name"], "file": f"devices/{dv['name']}.arch.json", "digest": _sha(p.read_bytes()),
                   "source": dv.get("source", "")})
    man = {
        "kind": "qccd.compiler_suite", "version": 1, "suite": name, "release": release,
        "title": title, "description": description, "track": "compiler", "contract": "qccd.compiler@1",
        "physics": {"file": "physics.json", "digest": _sha((d / "physics.json").read_bytes()),
                    "model": "corrected", "rank_table": "qccdsim_jones"},
        "circuits": cs, "devices": ds,
        "pairs": [{"circuit": c["name"], "device": dv["name"]} for c in cs for dv in ds],
        "hidden": {"count": int(hidden_count), "seed": "held by the official server (QCCD_HIDDEN_SEED)"},
        "limits": dict(limits), "ler": dict(ler),
        "metrics": [
            {"name": "speedup", "unit": "x", "better": "high",
             "about": "geometric mean over pairs both compilers compiled validly of T_jones(reference) / T_jones(yours)"},
            {"name": "coverage", "unit": "fraction", "better": "high",
             "about": "pairs compiled validly / pairs in the suite"},
            {"name": "ler_ratio", "unit": "x", "better": "low",
             "about": "geometric mean over memory pairs both compiled of LER(yours) / LER(reference)"},
            {"name": "wrong", "unit": "count", "better": "low",
             "about": "pairs where the program failed a rule or does not implement the circuit (any makes the entry ineligible)"},
            {"name": "compile_seconds", "unit": "s", "better": "low",
             "about": "total wall time of the compiler over the suite (reported, not ranked)"},
        ],
        "rank_by": "speedup",
    }
    if baseline is not None:
        _put(d / "baseline.json", json.dumps(baseline, indent=1, sort_keys=True) + "\n")
        man["baseline"] = {"file": "baseline.json", "digest": _sha((d / "baseline.json").read_bytes())}
    _put(d / "suite.json", json.dumps(man, indent=1, sort_keys=True) + "\n")
    return d
