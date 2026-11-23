"""Download and reproduce: one zip per leaderboard data point.

Every point on every leaderboard -- a design's logical error rate on a memory board, a size of
Goal 3's sweep, a round time on a syndrome-round board -- can be downloaded as one `.zip` that
holds what the number was made from and the scripts that make it again:

    README.md                    what the point is, the commands, and what must match how closely
    circuit.qasm                 the input QEC program
    machine.arch.json            the machine it runs on, with its physics
    hardware_program.tsir.json   the hardware program exactly as replayed (the cooled program)
    hardware_program.txt         the same program in the text syntax, for reading
    certificate.qcert.json       the compiler's certificate, when the entry has one
    detectors.json               the experiment's detectors and observables on the circuit's bits
    circuit.stim                 the noisy stim circuit: the board's noise model applied to the
                                 hardware program, with the detectors and observables
    experiment.json              machine-readable: board, noise model, decoder and every parameter,
                                 seed, budget, stopping rule, the published result, tool versions
    run.py                       self-contained (stim, numpy, the decoder): samples `circuit.stim`,
                                 decodes, and says whether the published rate is reproduced
    regenerate.py                with the qccd package: replays the program on the machine (time,
                                 rule verdicts) and rebuilds `circuit.stim` from the program
    requirements.txt             the tested versions

A board ranked by time has no noise experiment and no decoder: its zip has no `circuit.stim`,
`detectors.json` or `run.py`, and `regenerate.py`'s replay is the reproduction.

This module is the one builder, used by every board:

    Point                 one data point: its files, its board's release, its published numbers
    build(point)          the zip's bytes (deterministic) and what was recorded
    board_point / write_board    a seed board's rows (BBResults, SmallCode, MemoryBoard)
    replay / noisy_stim   what `regenerate.py` calls: the same code that built the zip
    decoder_label         the decoder of a logical error rate, in words, with its parameters
    link(key)             the button's target on an entry page (`render_html(reproduce=)`)

The zip's bytes are a function of the point's files, the release and the source commit: entries
in a fixed order with a fixed timestamp, text with LF line ends.  The files of the machine that
compiled a program name its folders in the program's provenance; those paths are removed (only
they), so a zip never says whose disk it was built on.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import io
import json
import os
import re
import subprocess
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = ["Point", "build", "write", "board_point", "write_board", "replay", "noisy_stim",
           "decoder_config", "decoder_label", "decoder_labels", "link", "zip_name", "LABEL", "HINT",
           "REPO_URL", "ReproduceError"]

REPO = Path(__file__).resolve().parents[2]
REPO_URL = "https://github.com/yezhuoyang/QCCD"
SITE_URL = "https://qccd.academy"

#: the button, word for word, wherever it is drawn
LABEL = "Download and reproduce"
#: what the button gives, in words (a page never names a source file)
HINT = ("Downloads one zip file with everything this number was made from: the input circuit, the "
        "machine, the hardware program, the noisy stim circuit, and the Python scripts that run the "
        "experiment again, with instructions.")
#: the folder, beside a board's pages, its zips are served from
FOLDER = "reproduce"

#: the versions the scripts were tested with (2026-11-23, Python 3.14, Windows); a point's own
#: record of the tools its published number was made with takes precedence
TESTED = {"stim": "1.15.0", "numpy": "2.4.4", "pymatching": "2.4.0", "ldpc": "2.4.1", "scipy": "1.17.1"}

#: a release's operating-point table -> the tag its time is published under (`T_<tag>`)
TABLE_TAGS = {"qccdsim_jones": "jones", "transport_excitation": "transport"}

_ZIP_TIME = (2026, 1, 1, 0, 0, 0)
_HERE = Path(__file__).resolve().parent


class ReproduceError(ValueError):
    """A point whose zip cannot be made: a file is missing, or its program does not replay to
    the published number (the folder holds another compile of the point)."""


# ------------------------------------------------------------------ the decoder, in words

_BPOSD = {"bp_method": "product_sum", "osd_method": "osd_cs", "osd_order": 7}


def decoder_config(ler: Mapping | None = None, budget: Mapping | None = None) -> dict:
    """The decoder of a logical error rate with every parameter: `{name, label, package,
    parameters}`.

    `ler` is the stored result (its `decoder_config` when a newer report carries one, else its
    `decoder` name: `pymatching` or `bposd`); `budget` the release's sampling budget, which
    holds BP-OSD's iteration cap (`bp_max_iter`) and, when no result is at hand, the decoder
    the release asks for."""
    ler, budget = dict(ler or {}), dict(budget or {})
    given = ler.get("decoder_config") if isinstance(ler.get("decoder_config"), Mapping) else None
    name = str(ler.get("decoder") or (given or {}).get("name") or budget.get("decoder") or "auto")
    if name == "pymatching":
        out = {"name": "pymatching", "label": "PyMatching 2 (minimum-weight perfect matching)",
               "package": "pymatching",
               "parameters": {"method": "minimum-weight perfect matching",
                              "error_model": "stim detector error model, decompose_errors=True"}}
    elif name == "bposd":
        it = budget.get("bp_max_iter")
        its = f"{int(it)} iterations" if it else "as many iterations as detectors, at least 100"
        out = {"name": "bposd",
               "label": f"BP-OSD (ldpc: product-sum BP, {its}; OSD-CS, order {_BPOSD['osd_order']})",
               "package": "ldpc",
               "parameters": {**_BPOSD, "bp_max_iter": int(it) if it else None,
                              "bp_max_iter_rule": "bp_max_iter, or max(number of detectors, 100) when none is set",
                              "error_model": "stim detector error model, decompose_errors=False",
                              "syndrome_cache": True}}
    else:
        out = {"name": "auto", "package": None, "parameters": {},
               "label": "PyMatching 2 where every error is graph-like, else BP-OSD (ldpc: product-sum BP; "
                        f"OSD-CS, order {_BPOSD['osd_order']})"}
    if given and given.get("label"):
        # a result that says its own decoder in words is taken at its word
        out["label"] = str(given["label"])
        out["recorded"] = dict(given)
    return out


def decoder_label(ler: Mapping | None = None, budget: Mapping | None = None) -> str:
    """`decoder_config(...)["label"]`: "PyMatching 2 (minimum-weight perfect matching)",
    "BP-OSD (ldpc: product-sum BP, 30 iterations; OSD-CS, order 7)"."""
    return decoder_config(ler, budget)["label"]


def decoder_labels(budget: Mapping | None) -> dict:
    """A release's budget -> the label for each decoder name a row of it can carry.  A page
    that draws live rows (the official entries) picks by the row's `ler.decoder`."""
    return {name: decoder_label({"decoder": name}, budget) for name in ("pymatching", "bposd")}


# ------------------------------------------------------------------ one data point


@dataclass
class Point:
    """One data point of a leaderboard: the files it was made from and what was published."""

    board: str                              # the board on the site: "surface3_mem", "goal3-surface3"
    board_title: str
    entry: str                              # the row: "07_grid9x9", "c2lr_4x4"
    title: str                              # the row's title, as the board shows it
    qasm: Path
    machine: Path
    program: Path
    certificate: Path | None = None
    table: str = "qccdsim_jones"            # the clock the board's time is replayed under
    release: str | None = None              # the task release: circuit, detectors, noise, budget
    noise: str | None = None                # the noise model, when it is not the release's
    budget: Mapping | None = None           # the sampling budget with its seed, when not the release's
    ler: Mapping | None = None              # the published sample; None on a board ranked by time
    tools: Mapping | None = None            # the versions the published number was made with
    time_ms: float | None = None            # the published total time on `table`
    time_tol_ms: float = 6e-4               # a board prints its time to 3 decimals of a millisecond
    rule_set: str | None = None             # the rule set the point is judged by, when it has its own
    replay_rules: bool = True               # also the replay's own rule verdicts (a board's page shows them)
    rules_failed: Sequence[str] | None = None   # the published verdict: the rules it breaks
    machine_name: str = ""
    family: str = ""
    compiler: str = ""
    physics: str = ""                       # the physics the board is graded under, by name
    url: str = ""                           # the board's page on the site
    numbers: Mapping = field(default_factory=dict)   # other published numbers, as the board shows them
    note: str = ""                          # one more sentence for the README
    no_ler_reason: str = ""                 # why a point of a board with rates has none (it broke a rule)

    @property
    def name(self) -> str:
        return zip_name(self.board, self.entry)


def zip_name(board: str, entry: str) -> str:
    """The file a reader saves: `<board>-<entry>-reproduce.zip`."""
    safe = lambda s: re.sub(r"[^A-Za-z0-9._-]+", "_", str(s)).strip("_")  # noqa: E731
    return f"{safe(board)}-{safe(entry)}-reproduce.zip"


def link(board: str, entry: str, *, folder: str = FOLDER) -> dict:
    """What `render_html(reproduce=)` takes for an entry page: where the zip is, relative to the
    page, and the name it is saved under."""
    return {"href": f"{folder}/{entry}.zip", "name": zip_name(board, entry)}


# ------------------------------------------------------------------ files as the zip carries them

#: an absolute path, as the machine that compiled a program wrote it into the program
_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|/(?:home|Users|root|tmp|var|mnt|opt|srv|c|d)/|\\\\)")
#: what must never be in a zip: a user folder, or an IPv4 address
_LEAK = re.compile(r"[A-Za-z]:\\{1,4}Users|[A-Za-z]:/Users|/home/[A-Za-z]|/Users/[A-Za-z]|"
                   r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")


def _scrub(doc: Any, key: str | None = None) -> tuple[Any, list]:
    """`doc` without the absolute paths of the machine that wrote it: a provenance root is
    blanked, any other path keeps its file name.  Returns the document and what was changed."""
    changed: list = []

    def walk(o, k):
        if isinstance(o, dict):
            return {kk: walk(v, kk) for kk, v in o.items()}
        if isinstance(o, list):
            return [walk(v, k) for v in o]
        if isinstance(o, str) and _ABS.match(o):
            new = "" if k == "root" else re.split(r"[\\/]+", o.rstrip("\\/"))[-1]
            changed.append((o, new))
            return new
        return o

    return walk(doc, key), changed


def _json_member(path: Path, what: str) -> tuple[str, Any, bool]:
    """A JSON file as the zip carries it: its text (LF, paths of the building machine removed),
    the parsed document, and whether anything was removed."""
    if not Path(path).is_file():
        raise ReproduceError(f"{what}: {Path(path).name} is missing")
    text = Path(path).read_text(encoding="utf-8").replace("\r\n", "\n")
    doc = json.loads(text)
    clean, changed = _scrub(doc)
    if not changed:
        return text, doc, False
    out = text
    for old, new in dict(changed).items():
        for enc in (json.dumps(old), json.dumps(old, ensure_ascii=False)):
            out = out.replace(enc, json.dumps(new))
    try:
        if json.loads(out) != clean:
            raise ValueError
    except ValueError:
        # the file spelled its strings another way: write the cleaned document afresh
        out = json.dumps(clean, separators=(",", ":"))
    return out, clean, True


def _sha(text: str | bytes) -> str:
    return "sha256:" + hashlib.sha256(text if isinstance(text, bytes) else text.encode("utf-8")).hexdigest()


@functools.lru_cache(maxsize=1)
def source_commit() -> str | None:
    """The commit of the code that builds the zips: `QCCD_COMMIT`, else this checkout's HEAD."""
    env = (os.environ.get("QCCD_COMMIT") or "").strip()
    if re.fullmatch(r"[0-9a-f]{7,40}", env):
        return env
    try:
        r = subprocess.run(["git", "-C", str(REPO), "rev-parse", "HEAD"], capture_output=True, text=True,
                           timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    out = r.stdout.strip()
    return out if r.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", out) else None


@functools.lru_cache(maxsize=1)
def source_dirty() -> bool:
    """Whether the package the zips are built by differs from `source_commit()`: uncommitted
    changes under `qccd/`.  A zip then says so, since the commit it names is not quite its code."""
    if (os.environ.get("QCCD_COMMIT") or "").strip():
        return False
    try:
        r = subprocess.run(["git", "-C", str(REPO), "status", "--porcelain", "--untracked-files=no", "--", "qccd"],
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0 and bool(r.stdout.strip())


# ------------------------------------------------------------------ what regenerate.py calls


def replay(program, machine, table: str, *, rule_set: str | None = None, certificate: Mapping | None = None,
           replay_rules: bool = True) -> dict:
    """Replay a hardware program on its machine under the clock `table`: its time and its rule
    verdicts.  `{table, total_us, total_ms, rules: {failed, passed} | None, rule_set: {name,
    failed, holds, not_applicable} | None}`.

    `rules` are the replay's own verdicts (what a board's entry page shows); `rule_set` judges
    the program by a named rule set of the rule language (a Goal 3 family's, a C2LR board's)."""
    from ..cost import corrected_model
    from .extract import load_arch, load_program

    arch, prog, model = load_arch(machine), load_program(program), corrected_model(table)
    out: dict = {"table": table, "total_us": None, "total_ms": None, "rules": None, "rule_set": None}
    if replay_rules:
        from ..verify import verify
        rep = verify(prog, arch, model, check_metrics=False)
        s = rep.rules.summary()
        out["total_us"] = float(rep.result.total_us)
        out["rules"] = {"failed": sorted(s["failed"]), "passed": sorted(s["passed"])}
    if rule_set:
        from ..ruleset import FAILS, HOLDS, check, legacy_config
        from ..ruleset import load as load_rules
        from ..ruleset.compile import circuit_of_certificate
        from ..ruleset.programme import programme_execution
        rs = load_rules(rule_set)
        exe = programme_execution(prog, arch, model,
                                  circuit=circuit_of_certificate(dict(certificate)) if certificate else None,
                                  rule_config=legacy_config(rs) or None, check_metrics=False)
        judged = check(rs, exe)
        if out["total_us"] is None:
            out["total_us"] = float(max(exe.instant_time))
        out["rule_set"] = {"name": rs.name, "title": rs.title,
                           "failed": sorted(x.id for x in judged.results if x.verdict == FAILS),
                           "holds": sorted(x.id for x in judged.results if x.verdict == HOLDS),
                           "not_applicable": sorted(x.id for x in judged.results if x.verdict not in (HOLDS, FAILS))}
    out["total_ms"] = None if out["total_us"] is None else out["total_us"] / 1000.0
    return out


def noisy_stim(program, machine, qasm: str, detectors: Mapping, noise: str, certificate: Mapping | None = None):
    """The noisy stim circuit of a hardware program: `(text, extraction)`.

    The program is replayed on the machine and the noise model's text (`qccd/evalset/library/
    <noise>.eval`) writes the circuit: every gate, measurement and reset as its stabilizer
    operation on the ions it names, every channel's Pauli noise where its clause says, then the
    experiment's detectors and observables (`detectors`, a `qccd.detectors` document over the
    bits of `qasm`).  `text` is exactly what `circuit.stim` holds."""
    from .experiments import MemoryExperiment
    from .extract import extract

    exp = MemoryExperiment.from_spec(detectors, qasm)
    ex = extract(program, machine, exp, noise=noise, cert=certificate)
    return "\n".join(ex.lines) + "\n", ex


def _rounds_run(exp) -> int:
    """The syndrome rounds the program runs: a Z-then-X memory on one block runs its rounds twice."""
    return exp.rounds * (2 if str(exp.code).endswith("_seq") else 1)


def _sampling(budget: Mapping) -> dict:
    """The budget with every default of `estimate_ler` written out, so a script needs no default."""
    from .decode import estimate_ler

    d = {k: p.default for k, p in inspect.signature(estimate_ler).parameters.items()
         if k in ("max_shots", "max_errors", "batch", "time_limit_s")}
    out = {"seed": budget.get("seed"), **{k: budget.get(k, d[k]) for k in ("max_shots", "max_errors", "batch")},
           "time_limit_s": budget.get("time_limit_s", d["time_limit_s"])}
    out["stop"] = (f"batches of {out['batch']} shots are drawn from one seeded sampler until, after a whole "
                   f"batch, {out['max_errors']} logical errors have been counted, or {out['max_shots']} shots "
                   "have been drawn" + ("" if not out["time_limit_s"] else
                                        f", or {out['time_limit_s']} s have passed (checked after each batch)"))
    out["counted"] = "a shot is a logical error when any observable is predicted wrong"
    return out


# ------------------------------------------------------------------ the zip


def _text(name: str) -> str:
    return (_HERE / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def build(point: Point) -> tuple[bytes, dict]:
    """The point's zip, and what was recorded (`experiment.json`, with `seconds` and `bytes`).

    Raises `ReproduceError` when a file is missing, when the program does not replay to the
    published time (the folder holds another compile of the point), or when the circuit made
    from it is not the published experiment's."""
    t0 = time.time()
    for what, p in (("the circuit", point.qasm), ("the machine", point.machine), ("the program", point.program)):
        if not Path(p).is_file():
            raise ReproduceError(f"{point.board} {point.entry}: {what} ({Path(p).name}) is missing")
    qasm = Path(point.qasm).read_text(encoding="utf-8").replace("\r\n", "\n")
    machine_text, machine_doc, _ = _json_member(point.machine, "the machine")
    program_text, program_doc, scrubbed = _json_member(point.program, "the program")
    cert_text = cert_doc = None
    if point.certificate is not None and Path(point.certificate).is_file():
        cert_text, cert_doc, _ = _json_member(point.certificate, "the certificate")

    rel = None
    if point.release:
        from ..workspace.tasks import find_release
        rel = find_release(point.release)
    is_ler = point.ler is not None
    if is_ler and (rel is None or not rel.manifest.get("qec")):
        raise ReproduceError(f"{point.board} {point.entry}: a logical error rate needs the release of its "
                             "memory experiment")

    # -- the replay: the time and the rule verdicts, by the code regenerate.py runs
    from .extract import load_arch, load_program
    arch, prog = load_arch(machine_doc), load_program(program_doc)
    rp = replay(prog, arch, point.table, rule_set=point.rule_set, certificate=cert_doc,
                replay_rules=point.replay_rules or not point.rule_set)
    if point.time_ms is not None and abs(rp["total_ms"] - point.time_ms) > max(point.time_tol_ms,
                                                                                1e-9 * abs(point.time_ms)):
        raise ReproduceError(f"{point.board} {point.entry}: the program replays to {rp['total_ms']:.6f} ms on the "
                             f"{point.table} table, the published row says {point.time_ms} ms")

    members: dict[str, str] = {"circuit.qasm": qasm, "machine.arch.json": machine_text,
                               "hardware_program.tsir.json": program_text}
    from ..ir.target_text import instruction_text
    members["hardware_program.txt"] = "\n".join(instruction_text(i, arch.device) for i in prog.instructions) + "\n"
    if cert_text is not None:
        members["certificate.qcert.json"] = cert_text

    record: dict[str, Any] = {
        "kind": "qccd.reproduce", "version": 1,
        "reproduces": "ler" if is_ler else "time",
        "board": {"id": point.board, "title": point.board_title, "url": point.url or None,
                  "release": rel.id if rel else None, "release_digest": rel.digest if rel else None,
                  "ranked_by": (rel.manifest.get("rank_by") if rel else None)},
        "entry": {"key": point.entry, "title": point.title, "machine": point.machine_name or arch.name,
                  "family": point.family or None, "compiler": point.compiler or None},
        "circuit": {"qubits": None, "matches_release": None},
        "physics": {"name": point.physics or None, "clock_table": point.table,
                    "release_physics_digest": (rel.manifest["physics"].get("digest") if rel else None),
                    "note": "the machine file carries the physics blocks the replay and the noise model read"},
    }
    if rel is not None:
        record["circuit"]["matches_release"] = _sha(qasm) == rel.manifest["circuit"]["digest"]

    exp = None
    if is_ler:
        from .experiments import MemoryExperiment
        q = rel.manifest["qec"]
        spec = rel.detectors()
        noise = point.noise or q["noise"]
        budget = dict(point.budget if point.budget is not None else (q.get("ler") or {}))
        exp = MemoryExperiment.from_spec(spec, qasm)
        stim_text, ex = noisy_stim(prog, arch, qasm, spec, noise, cert_doc)
        ler = dict(point.ler)
        if int(ler.get("rounds", exp.rounds)) != exp.rounds:
            raise ReproduceError(f"{point.board} {point.entry}: the published rate is over {ler.get('rounds')} "
                                 f"rounds, the release's experiment has {exp.rounds}")
        members["detectors.json"] = json.dumps(spec, indent=1, sort_keys=True) + "\n"
        members["circuit.stim"] = stim_text
        dec = decoder_config(ler, budget)
        if dec["name"] not in ("pymatching", "bposd"):
            raise ReproduceError(f"{point.board} {point.entry}: the published result names no decoder")
        record.update({
            "experiment": {**exp.summary(), "rounds_run": _rounds_run(exp),
                           "detectors_in_circuit": ex.stats.get("detectors"),
                           "observables_in_circuit": ex.stats.get("observables"),
                           "qubits_in_circuit": ex.stats.get("ions")},
            "noise": {"id": noise, "text": ex.evaluation.get("name"), "digest": ex.evaluation.get("digest"),
                      "parameters": ex.params,
                      "expected_errors_by_channel": {k: float(v) for k, v in ex.budget.items()}},
            "decoder": dec,
            "sampling": _sampling(budget),
        })
    else:
        record["experiment"] = None
        record["why_no_noise_experiment"] = point.no_ler_reason or (
            "this board ranks by the running time of one compiled program on its machine; no noise model is "
            "applied and nothing is decoded, so there is no stim circuit: replaying the hardware program is "
            "the whole reproduction")

    rounds_run = _rounds_run(exp) if exp is not None else None
    time_pub = {"table": point.table, "total_ms": point.time_ms,
                "per_round_ms": (None if point.time_ms is None or not rounds_run else point.time_ms / rounds_run),
                "rounds_run": rounds_run}
    record["published"] = {
        "ler": dict(point.ler) if is_ler else None,
        "time": time_pub,
        "rules_failed": None if point.rules_failed is None else sorted(point.rules_failed),
        "numbers": dict(point.numbers) or None,
        "tools": dict(point.tools) if point.tools else None,
    }
    record["replay"] = {**rp, "note": "the hardware program replayed on the machine when this zip was built; "
                                      "regenerate.py must print the same time and the same verdicts"}
    record["circuit"]["qubits"] = (exp.n_data + exp.n_ancilla) if exp is not None else None
    record["program"] = {"instructions": len(prog.instructions),
                         "provenance_paths_removed": bool(scrubbed)}
    record["built"] = {"repository": REPO_URL, "commit": source_commit(), "builder": "qccd/qec/reproduce.py",
                       **({"uncommitted_changes": True} if source_dirty() else {})}
    tools = {**TESTED, **{k: v for k, v in (point.tools or {}).items() if v and k in TESTED}}
    record["requirements"] = tools

    if is_ler:
        members["run.py"] = _text("reproduce_run.py")
    members["regenerate.py"] = _text("reproduce_regenerate.py")
    members["requirements.txt"] = _requirements(record)
    record["files"] = {n: {"sha256": _sha(t), "bytes": len(t.encode("utf-8"))} for n, t in sorted(members.items())}
    members["experiment.json"] = json.dumps(record, indent=1) + "\n"
    members["README.md"] = _readme(point, record)

    order = ["README.md", "circuit.qasm", "machine.arch.json", "hardware_program.tsir.json",
             "hardware_program.txt", "certificate.qcert.json", "detectors.json", "circuit.stim",
             "experiment.json", "run.py", "regenerate.py", "requirements.txt"]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for n in order:
            if n not in members:
                continue
            leak = _LEAK.search(members[n])
            if leak:
                raise ReproduceError(f"{point.board} {point.entry}: {n} would carry {leak.group(0)!r}, a path or "
                                     "an address of the machine that built it")
            info = zipfile.ZipInfo(n, date_time=_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3                      # the same bytes whichever system builds it
            info.external_attr = 0o644 << 16
            z.writestr(info, members[n].encode("utf-8"), compresslevel=9)
    data = buf.getvalue()
    return data, {**record, "name": point.name, "bytes": len(data), "seconds": round(time.time() - t0, 2)}


def write(point: Point, path: Path) -> dict:
    """Build the point's zip and write it to `path`; `{name, bytes, seconds, sha256}`."""
    data, info = build(point)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {"name": info["name"], "bytes": len(data), "seconds": info["seconds"], "sha256": _sha(data),
            "reproduces": info["reproduces"]}


def _requirements(record: dict) -> str:
    t = record["requirements"]
    pip = f'pip install "qccd @ git+{REPO_URL}@{record["built"]["commit"] or "main"}"'
    if record["reproduces"] != "ler":
        return ("# This point is a replay of the hardware program: regenerate.py needs only the QCCD package\n"
                "# (standard-library Python, 3.10 or newer), from the public repository at the commit this zip\n"
                f"# was built from:   {pip}\n"
                f"qccd @ git+{REPO_URL}@{record['built']['commit'] or 'main'}\n")
    dec = record["decoder"]["name"]
    lines = ["# What run.py needs: the versions the published number was made and tested with.",
             "# (Python 3.11 or newer for these exact versions; tested on Python 3.14.)",
             f"stim=={t['stim']}", f"numpy=={t['numpy']}"]
    if dec == "pymatching":
        lines.append(f"pymatching=={t['pymatching']}")
    else:
        lines += [f"ldpc=={t['ldpc']}", f"scipy=={t['scipy']}"]
    lines += ["# regenerate.py also needs the QCCD package, at the commit this zip was built from:",
              f"#   {pip}"]
    return "\n".join(lines) + "\n"


def _fmt(x) -> str:
    return "-" if x is None else (f"{x:.7g}" if isinstance(x, float) else str(x))


def _readme(point: Point, r: dict) -> str:
    ler, t, b = r["published"]["ler"], r["published"]["time"], r["built"]
    commit = b["commit"] or "main"
    pip_qccd = f'pip install "qccd @ git+{REPO_URL}@{commit}"'
    L: list[str] = [f"# Download and reproduce: {point.board_title}", "",
                    f"**{point.title}**", "",
                    "This folder holds one data point of the QCCD leaderboard"
                    + (f" ({r['board']['url']})" if r["board"]["url"] else "")
                    + ": everything the number was made from, and the scripts that make it again.", ""]
    rows = [("board", f"{point.board_title}" + (f" (release `{r['board']['release']}`)" if r["board"]["release"] else "")),
            ("entry", f"`{point.entry}`"),
            ("machine", f"`{r['entry']['machine']}`" + (f", {point.family}" if point.family else "")
             + " (`machine.arch.json`)"),
            ("compiled by", point.compiler or "-"),
            ("hardware program", f"{r['program']['instructions']} instructions (`hardware_program.tsir.json`; "
                                 "`hardware_program.txt` is the same program as text)")]
    if ler is not None:
        e, s, d, n = r["experiment"], r["sampling"], r["decoder"], r["noise"]
        par = ", ".join(f"{k}={v}" for k, v in d["parameters"].items() if k not in ("bp_max_iter_rule",))
        rows += [
            ("experiment", f"`{e['experiment']}`: {e['ions']} qubits ({e['n_data']} data, {e['n_ancilla']} ancilla), "
                           f"{e['detectors']} detectors, {e['observables']} observable(s); the rate per round is over "
                           f"{e['rounds']} rounds" + (f" (the program runs {e['rounds_run']}: a Z memory, then an X memory)"
                                                      if e["rounds_run"] != e["rounds"] else "")),
            ("noise model", f"`{n['id']}` (text digest `{(n['digest'] or '')[:23]}`)"
                            + (f", on {point.physics}" if point.physics else "")),
            ("decoder", f"{d['label']}; parameters: {par}"),
            ("seed", f"`{s['seed']}`"),
            ("budget", s["stop"] + "; " + s["counted"]),
            ("published", f"**{ler['errors']} logical errors in {int(ler['shots']):,} shots**: LER per shot "
                          f"{ler['ler']:.6g} (95% interval {ler['lo']:.6g} to {ler['hi']:.6g}); **per round "
                          f"{ler['per_round']:.6g}** ({ler['per_round_lo']:.6g} to {ler['per_round_hi']:.6g}); "
                          f"stopped by `{ler.get('stopped') or '-'}`"),
        ]
    if t["total_ms"] is not None:
        rows.append(("published time", f"**{_fmt(float(t['total_ms']))} ms** in all on the `{t['table']}` table"
                     + (f", {_fmt(float(t['per_round_ms']))} ms per round over {t['rounds_run']} rounds"
                        if t["per_round_ms"] is not None else "")))
    else:
        rows.append(("time at build", f"{r['replay']['total_ms']:.6f} ms on the `{t['table']}` table"))
    if r["published"]["rules_failed"] is not None:
        rows.append(("published rules", "every rule holds" if not r["published"]["rules_failed"]
                     else "breaks " + ", ".join(r["published"]["rules_failed"])))
    tools = r["published"]["tools"]
    if tools:
        rows.append(("made with", ", ".join(f"{k} {v}" for k, v in tools.items() if v)))
    L += ["| | |", "|---|---|"] + [f"| {k} | {v} |" for k, v in rows] + [""]
    if point.note:
        L += [point.note, ""]

    L += ["## Set up", "",
          "Unzip into an empty folder and open a terminal there. Python 3.11 or newer.", "",
          "```", "python -m venv .venv",
          ".venv\\Scripts\\activate          # Windows",
          "source .venv/bin/activate       # Linux, macOS", "```", ""]
    if ler is not None:
        L += ["## 1. Reproduce the logical error rate", "",
              "`run.py` needs no QCCD code: it samples `circuit.stim` with stim and decodes with "
              f"{r['decoder']['label'].split(' (')[0]}.", "",
              "```", "pip install -r requirements.txt", "python run.py", "```", "",
              "It draws the published budget with the published seed, prints the errors, the shots, the rate per "
              "shot and per round with Wilson's 95% interval, and a verdict. `python run.py --shots 20000` is a "
              "quick run; `--seed N` draws an independent sample.", "",
              "## 2. Rebuild the stim circuit from the hardware program", "",
              "`regenerate.py` uses the QCCD package, from the public repository at the commit this zip was "
              "built from. It replays `hardware_program.tsir.json` on `machine.arch.json` (the time, the rule "
              "verdicts), then lets the noise model write the stim circuit again from the program, the machine, "
              "`circuit.qasm` and `detectors.json`, and checks that it is `circuit.stim`, character for "
              "character.", "",
              "```", pip_qccd, "python regenerate.py", "```", "",
              "## What must match", "",
              "- **Exactly:** the replayed time, the rule verdicts, and the stim circuit (`regenerate.py` says "
              "`identical`). These are computed, not sampled.",
              "- **Within the 95% interval:** the logical error rate. stim documents a seeded sample as the same "
              "for one stim version on one machine architecture. With the versions in `requirements.txt` on the "
              "same kind of machine `run.py` prints the published counts exactly (`REPRODUCED EXACTLY`). On another "
              "machine, operating system or version the same seed draws another sample of the same distribution; "
              "the point is then reproduced when **the published rate lies inside the 95% interval of your run, or "
              "the two intervals overlap** (`REPRODUCED within the 95% interval`). `run.py` exits with status 1 "
              "only when neither holds.", ""]
    else:
        why = (point.no_ler_reason[:1].upper() + point.no_ler_reason[1:] + "." if point.no_ler_reason else
               "This board ranks by the running time of one compiled program on its machine. No noise model is "
               "applied and nothing is decoded, so there is no stim circuit and no sampling script: replaying the "
               "hardware program on the machine is the whole reproduction.")
        L += ["## Reproduce the time", "",
              why + " `regenerate.py` uses the QCCD package "
              "(standard-library Python), from the public repository at the commit this zip was built from.", "",
              "```", "pip install -r requirements.txt", "python regenerate.py", "```", "",
              "## What must match", "",
              "- **Exactly:** the replayed time and the rule verdicts. They are computed from the program and the "
              "machine's physics, not sampled; `regenerate.py` exits with status 1 when either differs.", ""]
    L += ["## The files", "",
          "| file | what it is |", "|---|---|",
          "| `circuit.qasm` | the input QEC program (OpenQASM 2)" + (
              ", the release's circuit byte for byte" if r["circuit"]["matches_release"] else "") + " |",
          "| `machine.arch.json` | the machine: its traps, junctions and rails, its control, and its physics |",
          "| `hardware_program.tsir.json` | the hardware program exactly as replayed: every transport, gate, "
          "measurement, reset and cooling step" + (
              " (the folder names of the computer that compiled it were removed from its provenance; nothing else)"
              if r["program"]["provenance_paths_removed"] else "") + " |",
          "| `hardware_program.txt` | the same program, one instruction per line, for reading |"]
    if "certificate.qcert.json" in r["files"]:
        L.append("| `certificate.qcert.json` | the compiler's certificate: which instruction realises which circuit "
                 "operation |")
    if ler is not None:
        L += ["| `detectors.json` | the experiment's detectors and observables, over the circuit's classical bits |",
              "| `circuit.stim` | the noisy stim circuit that is sampled: the hardware program under the noise model, "
              "with the detectors and observables |"]
    L += ["| `experiment.json` | everything above, machine-readable, with each file's SHA-256 |"]
    if ler is not None:
        L.append("| `run.py` | samples and decodes `circuit.stim` (stim, numpy, the decoder; no QCCD code) |")
    L += ["| `regenerate.py` | replays the program" + (" and rebuilds `circuit.stim`" if ler is not None else "")
          + " with the QCCD package |",
          "| `requirements.txt` | the tested versions |", "",
          f"Source: {REPO_URL} at commit `{commit}`"
          + (" (built from a working tree with changes not yet committed)" if b.get("uncommitted_changes") else "")
          + ". The zip is built by `qccd/qec/reproduce.py`"
          + ("; the sampling procedure `run.py` implements is `qccd/qec/decode.py::estimate_ler`." if ler is not None
             else "; the replay is `qccd/verify`'s."), ""]
    return "\n".join(L)


# ------------------------------------------------------------------ a seed board's rows


def _release_of(task: Mapping):
    from ..workspace.tasks import ReleaseError, find_release
    rid = task.get("release") or f"{task['id']}@{task.get('version', 1)}"
    try:
        return find_release(rid)
    except ReleaseError:
        return None


def board_point(task: Mapping, row: Mapping, *, root: Path = REPO) -> Point | None:
    """A seed board's row (a `manifest.json` entry of BBResults, SmallCode or MemoryBoard) as a
    point, or None when the row has no compiled program.  `task` is the board's `task.json`."""
    if row.get("status") != "ok" or not row.get("prefix"):
        return None

    def at(v) -> Path:
        p = Path(str(v))
        return p if p.is_absolute() else Path(root) / p

    prefix = at(row["prefix"])
    rel = _release_of(task)
    table = (rel.manifest["physics"].get("rank_table") if rel else None) or row.get("table") or "qccdsim_jones"
    n = row.get("numbers") or {}
    key = "T_" + TABLE_TAGS.get(table, table)
    ler = row.get("ler") if (rel is not None and rel.manifest.get("qec")) else None
    tools = None
    rep = prefix.parent / "ler_report.json"
    if ler is not None and rep.is_file():
        tools = (json.loads(rep.read_text(encoding="utf-8")).get("tools") or None)
    track = (rel.manifest.get("track") if rel else None)
    compiler = ("the baseline C2LR router" if track == "c2lr" else
                str(n.get("compiled") or "") or "the reference compiler, qccdc")
    cert = Path(str(prefix) + ".qcert.json")
    shown = {k: n[k] for k in ("T_jones", "T_transport", key, "ler_per_round", "ler", "shots", "errors",
                               "two_qubit_gates", "ions_per_instruction", "dacs", "r10") if k in n}
    return Point(
        board=str(task["id"]), board_title=str(task.get("title") or task["id"]), entry=str(row["key"]),
        title=str(row.get("title") or row["key"]),
        qasm=at(row.get("qasm") or (str(row["prefix"]) + ".qasm")), machine=at(row["arch"]),
        program=Path(str(prefix) + ".cooled.tsir.json"), certificate=cert if cert.is_file() else None,
        table=table, release=rel.id if rel else None, ler=ler, tools=tools,
        time_ms=n.get(key), rule_set=(rel.manifest.get("rules") if rel else None),
        rules_failed=row.get("rules_failed"), machine_name=str(row.get("short") or ""),
        family=str(row.get("family") or ""), compiler=compiler,
        url=f"{SITE_URL}/board/{task['id']}/", numbers=shown)


def write_board(task: Mapping, rows: Sequence[Mapping], out_dir: Path, *, prebuilt: Path | None = None,
                root: Path = REPO, log=None) -> dict:
    """Every compiled row's zip into `<out_dir>/reproduce/<key>.zip`.

    A zip is built from the row's files; where those are not in this tree (a board whose
    artifacts are not committed) the zip a generator left in `prebuilt` is copied instead.
    Returns `{built, copied, missing: [key...], failed: {key: why}, bytes}`; a row that has
    neither is `missing`, and its page's button then leads nowhere, so a caller says so."""
    out = Path(out_dir) / FOLDER
    done = {"built": 0, "copied": 0, "missing": [], "failed": {}, "bytes": 0, "seconds": 0.0}
    for row in rows:
        if row.get("status") != "ok" or not row.get("prefix"):
            continue
        key = str(row["key"])
        dst = out / f"{key}.zip"
        ready = Path(prebuilt) / f"{key}.zip" if prebuilt else None
        try:
            p = board_point(task, row, root=root)
            if p is not None and all(Path(x).is_file() for x in (p.qasm, p.machine, p.program)):
                info = write(p, dst)
                done["built"] += 1
                done["bytes"] += info["bytes"]
                done["seconds"] += info["seconds"]
                continue
        except Exception as exc:  # noqa: BLE001 -- one row's failure is reported, the board goes on
            done["failed"][key] = f"{type(exc).__name__}: {exc}"[:300]
            if log:
                log(f"   reproduce {task['id']} {key}: NOT BUILT: {done['failed'][key]}")
        if ready is not None and ready.is_file():
            out.mkdir(parents=True, exist_ok=True)
            dst.write_bytes(ready.read_bytes())
            done["copied"] += 1
            done["bytes"] += dst.stat().st_size
            done["failed"].pop(key, None)
        elif key not in done["failed"]:
            done["missing"].append(key)
    return done
