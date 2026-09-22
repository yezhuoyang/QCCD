"""The evaluator: `grade(trusted_task_release, bundle, profile) -> report`.

One contract, independent of transport: the local service, the CLI and the official
worker all call `grade`.  It reuses the toolchain rather than restating it -- the loader
(`Architecture.from_json`), the replay and the 27 rules (`qccd.verify.verify`), the
compiler's parser (`qccdc_cli parse`), the trusted checker input builder
(`Compiler/bridge/mk_qcheck_input.py`), the proved Lean checker (`qcheck`) and the
semantic checker (`Compiler/bridge/check_cert.py`).

WHAT A PASSING REFERENCE REPORT ESTABLISHES, stage by stage (and nothing more):

  bundle               every file hashes to its manifest digest; the manifest names THIS task
                       release (id and digest); no stray or unknown members.
  device               the device loads under the architecture schema and structure checks.
  physics_lock         primitives/heating/species/budget equal the release's physics.json:
                       the score is under the task's physics, not the participant's.
  program              the final program is a well-formed TSIR within the release limits.
  rules                replaying the final program on the device under the task model,
                       every rule the replay can check passes; rules it cannot check are
                       listed as skipped with the reason, and only the release's
                       `allowed_skips` may be skipped.
  correspondence       the final program is the certified program with only the release's
                       allowed transformations applied (inserted `cool` instructions),
                       instruction by instruction, ids included.
  certificate_binding  the certificate's circuit is the trusted task circuit (op for op,
                       as the compiler's own parser reads the release's QASM); its gate
                       witnesses name instructions of the certified program that carry the
                       same operation stamps (`qccd.ir.source_map`).
  lean_certificate     the proved Lean checker accepts the certificate against facts it
                       re-derives from the SUBMITTED device (`check_sound`).
  semantics            O1 (transport replay of the certificate) and O2 (the stabilizer
                       tableau composed from the certified program's emitted pulses equals
                       the tableau of the TRUSTED circuit) both hold, and no op is
                       unrealised.  Outside the Clifford fragment O2 is `partial`.
  metrics              the leaderboard numbers, from `metrics.compute_metrics`.

It does NOT establish physical realisability beyond the task's model: the rules and the
physics tables are the model.  It does not compare the certificate's transport moves
with the final program's transport instructions one by one; co-location of the program's
own gates is R6b of the replay, and the semantic link is O2 through the certificate's
qubit->ion map.

Eligibility is decided by the release's policy: every required check `passed`, under the
`reference` profile.  A skipped, unsupported, partial, timed-out or cancelled required
check is never eligible -- absence of a failure is not a pass.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from . import EVALUATOR_NAME, EVALUATOR_VERSION, evaluator_identity
from .bundle import Bundle, BundleError, read_bundle
from .jsonsafe import canonical_text, digest, normalize_numbers
from .procs import run_limited
from .tasks import FIXED_BLOCKS, TaskRelease, physics_mismatch

__all__ = ["grade", "Toolchain", "PROFILES", "REPORT_KIND", "comparable", "STAGE_STATUSES"]

REPORT_KIND = "qccd.evaluation_report"
PROFILES = ("draft", "reference")
STAGE_STATUSES = ("passed", "failed", "partial", "skipped", "unsupported", "timeout",
                  "cancelled", "internal_error")
REPO = Path(__file__).resolve().parents[2]
BRIDGE = REPO / "Compiler" / "bridge"

#: rules the replay reports as skipped for reasons that are properties of the checker,
#: not of the entry: R7b is uncheckable by design, R9 checks claims an entry need not
#: make, R10 is decided by the certificate/semantics stages below.
ALLOWED_SKIPS = {"R7b", "R9", "R10"}
#: rules the replay only partially checks by construction
ALLOWED_PARTIAL = {"R15"}

_EXE_CACHE: dict = {}


@dataclass
class Toolchain:
    qccdc: Path | None
    qcheck: Path | None
    python: str
    bridge: Path

    @classmethod
    def discover(cls) -> "Toolchain":
        def pick(env, *cands):
            v = os.environ.get(env)
            if v:
                p = Path(v)
                return p if p.is_file() else None
            for c in cands:
                if c.is_file():
                    return c
            return None
        ob = REPO / "Compiler" / "ocaml" / "_build" / "default" / "bin"
        lb = REPO / "Compiler" / "lean" / ".lake" / "build" / "bin"
        return cls(qccdc=pick("QCCD_QCCDC", ob / "qccdc_cli.exe", ob / "qccdc_cli"),
                   qcheck=pick("QCCD_QCHECK", lb / "qcheck.exe", lb / "qcheck"),
                   python=sys.executable, bridge=BRIDGE)

    def identity(self) -> dict:
        return {"qccdc": _exe_digest(self.qccdc), "qcheck": _exe_digest(self.qcheck),
                "python": platform.python_version(),
                "bridge": _dir_digest(self.bridge, ("check_cert.py", "mk_qcheck_input.py",
                                                    "export_arch.py", "unitary.py", "insert_cooling.py"))}


def _exe_digest(p: Path | None) -> str | None:
    if p is None or not p.is_file():
        return None
    st = p.stat()
    key = (str(p), st.st_size, st.st_mtime)
    if key not in _EXE_CACHE:
        h = hashlib.sha256()
        with open(p, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        _EXE_CACHE[key] = "sha256:" + h.hexdigest()
    return _EXE_CACHE[key]


def _dir_digest(root: Path, names) -> str:
    h = hashlib.sha256()
    for n in names:
        p = root / n
        if p.is_file():
            h.update(n.encode())
            h.update(p.read_bytes().replace(b"\r\n", b"\n"))
    return "sha256:" + h.hexdigest()


class _Stage:
    def __init__(self, sid: str, required: bool):
        self.id = sid
        self.required = required
        self.status = "skipped"
        self.coverage = ""
        self.detail: dict = {}
        self.diagnostics: list = []
        self.t0 = time.time()
        self.seconds = 0.0

    def done(self, status: str, coverage: str = "", **detail) -> "_Stage":
        assert status in STAGE_STATUSES
        self.status = status
        self.coverage = coverage or self.coverage
        self.detail.update(detail)
        self.seconds = round(time.time() - self.t0, 3)
        return self

    def diag(self, rid: str, message: str, *, severity: str = "error", **where) -> None:
        d = {"id": rid, "severity": severity, "stage": self.id, "message": message[:2000]}
        for k in ("entities", "instructions", "pointer", "rule", "reference"):
            if k in where and where[k] is not None:
                d[k] = where[k]
        self.diagnostics.append(d)

    def to_json(self) -> dict:
        return {"id": self.id, "required": self.required, "status": self.status,
                "coverage": self.coverage, "detail": self.detail,
                "diagnostics": self.diagnostics[:200]}


def grade(release: TaskRelease, bundle_dir: Path, profile: str = "reference", *,
          workdir: Path, toolchain: Toolchain | None = None,
          cancel: threading.Event | None = None,
          progress: Callable[[str, str], None] | None = None,
          run: Mapping | None = None) -> dict:
    """Grade one immutable bundle against one trusted release.  Never raises for a bad
    submission -- a bad submission is a report; only a broken evaluator raises."""
    if profile not in PROFILES:
        raise ValueError(f"profile must be one of {PROFILES}")
    tc = toolchain or Toolchain.discover()
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    started = time.time()
    required = set(release.required_checks) if profile == "reference" else set()
    stages: dict = {}
    order = ["bundle", "device", "physics_lock", "program", "rules", "correspondence",
             "certificate_binding", "lean_certificate", "semantics", "metrics"]
    for sid in order:
        stages[sid] = _Stage(sid, sid in required)
    limits = release.manifest.get("limits") or {}
    say = progress or (lambda s, m: None)
    ctx: dict = {}

    def cancelled() -> bool:
        return cancel is not None and cancel.is_set()

    def finish() -> dict:
        return _report(release, profile, stages, order, ctx, tc, started, run)

    # ---- bundle
    st = stages["bundle"]
    say("bundle", "reading the bundle")
    try:
        b = read_bundle(Path(bundle_dir))
    except BundleError as exc:
        st.diag(f"BUNDLE.{exc.code.upper()}", str(exc))
        st.done("failed", "manifest and file digests")
        return finish()
    ctx["bundle"] = b
    if b.manifest["task"]["id"] != release.id or b.manifest["task"]["digest"] != release.digest:
        st.diag("BUNDLE.WRONG_TASK", f"the bundle is for {b.manifest['task']['id']} "
                f"({b.manifest['task']['digest'][:19]}...), not {release.id} ({release.digest[:19]}...)")
        st.done("failed", "task binding")
        return finish()
    st.done("passed", "manifest schema, member set, sha256 of every file, task id and digest")

    # ---- device
    st = stages["device"]
    from ..arch.device import Architecture
    try:
        arch = Architecture.from_json(b.docs["device"])
        n_nodes = len(arch.device.nodes)
        if n_nodes > int(limits.get("max_nodes", 10 ** 6)):
            st.diag("DEVICE.TOO_LARGE", f"{n_nodes} nodes exceeds the release limit {limits.get('max_nodes')}")
            st.done("failed", "size limit")
            return finish()
        ctx["arch"] = arch
        st.done("passed", "architecture schema, structure, declared zones", nodes=n_nodes,
                segments=len(arch.device.segments), loops=len(arch.device.loops))
    except (ValueError, KeyError, TypeError) as exc:
        st.diag("DEVICE.INVALID", f"the device does not load: {exc}", pointer="/device")
        st.done("failed", "architecture schema and structure")
        return finish()

    # ---- physics lock
    st = stages["physics_lock"]
    mism = physics_mismatch(b.docs["device"], release)
    if mism:
        for blk in mism:
            st.diag("TASK.PHYSICS_MODIFIED", f"the device's {blk!r} differs from the task release's "
                    f"physics: this is an exploratory design, not an entry for {release.id}",
                    pointer=f"/device/{blk}")
        st.done("failed", "fixed blocks " + ", ".join(FIXED_BLOCKS), changed=mism)
    else:
        st.done("passed", "fixed blocks " + ", ".join(FIXED_BLOCKS) + " equal the release")

    # ---- program
    st = stages["program"]
    from ..ir.tsir import TSIR, validate_program
    try:
        prog = TSIR.from_json(b.docs["program"])
        errs = validate_program(prog)
        if errs:
            for e in list(errs)[:50]:
                st.diag("PROGRAM.INVALID", str(e), pointer="/program")
            st.done("failed", "TSIR shape")
            return finish()
        if len(prog) > int(limits.get("max_instructions", 10 ** 9)):
            st.diag("PROGRAM.TOO_LARGE", f"{len(prog)} instructions exceeds {limits.get('max_instructions')}")
            st.done("failed", "size limit")
            return finish()
        ctx["prog"] = prog
        st.done("passed", "TSIR shape and instruction types", instructions=len(prog))
    except (ValueError, KeyError, TypeError) as exc:
        st.diag("PROGRAM.INVALID", f"the program does not parse: {exc}", pointer="/program")
        st.done("failed", "TSIR shape")
        return finish()

    if cancelled():
        return _cancel_rest(stages, order, finish)

    # ---- rules
    st = stages["rules"]
    say("rules", "replaying the program and checking the rules")
    try:
        _rules_stage(st, release, arch, prog)
    except Exception as exc:  # the checker failing is an evaluator problem, not a verdict
        st.diag("EVALUATOR.INTERNAL", f"{type(exc).__name__}: {exc}")
        st.done("internal_error", "replay + rules")

    # ---- correspondence
    st = stages["correspondence"]
    certified = None
    if "certified_program" in b.docs:
        try:
            certified = TSIR.from_json(b.docs["certified_program"])
            ctx["certified"] = certified
            _correspondence_stage(st, release, prog, certified)
        except (ValueError, KeyError, TypeError) as exc:
            st.diag("CERT.CERTIFIED_PROGRAM_INVALID", f"the certified program does not parse: {exc}")
            st.done("failed", "certified program shape")
    else:
        st.diag("BUNDLE.NO_CERTIFIED_PROGRAM", "no certified program: the final program cannot be tied "
                "to a certificate", severity="warning")
        st.done("skipped", "needs certified.tsir.json")

    if profile == "draft":
        for sid in ("certificate_binding", "lean_certificate", "semantics"):
            stages[sid].done("skipped", "not run under the draft profile")
    else:
        # ---- the certificate chain, in a scratch directory the tools write into
        cert = b.docs.get("certificate")
        cw = work / "check"
        if cw.exists():
            shutil.rmtree(cw, ignore_errors=True)
        cw.mkdir(parents=True)
        ready = cert is not None and certified is not None
        if ready:
            (cw / "device.arch.json").write_text(json.dumps(b.docs["device"]), encoding="utf-8")
            (cw / "prog.tsir.json").write_text(json.dumps(b.docs["certified_program"]), encoding="utf-8")
            (cw / "prog.qcert.json").write_text(json.dumps(cert), encoding="utf-8")
            (cw / "circuit.qasm").write_bytes(release.circuit_path.read_bytes())
        # binding
        st = stages["certificate_binding"]
        say("certificate_binding", "binding the certificate to the task circuit and the program")
        if not ready:
            st.diag("CERT.MISSING", "the bundle has no certificate or no certified program")
            st.done("failed", "requires certificate + certified program")
        else:
            _binding_stage(st, release, tc, cw, cert, certified, limits, cancel)
        if cancelled():
            return _cancel_rest(stages, order, finish)
        # lean
        st = stages["lean_certificate"]
        say("lean_certificate", "running the proved Lean checker")
        if not ready:
            st.done("skipped", "no certificate")
        elif stages["certificate_binding"].status != "passed":
            st.done("skipped", "the certificate is not bound to this task; checking it proves nothing here")
        else:
            _lean_stage(st, tc, cw, limits, cancel)
        if cancelled():
            return _cancel_rest(stages, order, finish)
        # semantics
        st = stages["semantics"]
        say("semantics", "checking the program implements the circuit")
        if not ready:
            st.done("skipped", "no certificate")
        else:
            _semantics_stage(st, tc, cw, limits, cancel)

    # ---- metrics
    st = stages["metrics"]
    say("metrics", "computing the leaderboard metrics")
    try:
        from ..arch.device import Architecture as _A
        phys_arch = arch
        if mism:
            # scored under the TASK's physics even when the device carries its own; the
            # entry is already ineligible, the numbers are for comparison only
            doc = dict(b.docs["device"])
            doc.update(release.physics())
            phys_arch = _A.from_json(doc)
        from .metrics import METRIC_UNITS, compute_metrics
        values, breakdown = compute_metrics(phys_arch, prog, release.manifest["physics"])
        spec = {m["name"]: m for m in release.manifest.get("metrics") or []}
        ctx["metrics"] = {k: {"value": v, "unit": (spec.get(k) or {}).get("unit", METRIC_UNITS.get(k, "")),
                              "better": (spec.get(k) or {}).get("better"),
                              "ranked": k == release.manifest.get("rank_by")}
                          for k, v in values.items()}
        ctx["breakdown"] = breakdown
        st.done("passed", "replay under each task table; hardware report; broadcast use"
                + (" (under the task physics, substituted)" if mism else ""))
    except Exception as exc:
        st.diag("METRICS.FAILED", f"{type(exc).__name__}: {exc}")
        st.done("failed" if isinstance(exc, (ValueError, KeyError)) else "internal_error", "metrics")
    return finish()


# ---------------------------------------------------------------------- stages

def _rules_stage(st: _Stage, release: TaskRelease, arch, prog) -> None:
    from ..cost.models import corrected_model
    from ..verify import verify
    table = release.manifest["physics"].get("rank_table", "qccdsim_jones")
    rep = verify(prog, arch, corrected_model(table), check_metrics=False)
    s = rep.rules.summary()
    failed = sorted(s.get("failed") or [])
    skipped = dict(s.get("skipped") or {})
    partial = dict(s.get("partial") or {})
    passed = sorted(set(s.get("passed") or []) - set(partial))
    for v in rep.rules.violations[:500]:
        st.diag(f"RULE.{v.rule}", v.message, rule=v.rule,
                instructions=[v.instr_id] if getattr(v, "instr_id", None) is not None else None,
                severity=getattr(v, "severity", "error") or "error",
                reference={"doc": f"docs/rules.md#{v.rule.lower()}", "release": release.id})
    bad_skips = sorted(set(skipped) - ALLOWED_SKIPS)
    bad_partial = sorted(set(partial) - ALLOWED_PARTIAL)
    for r in bad_skips:
        st.diag(f"RULE.{r}.SKIPPED", f"{r} was not checked: {skipped[r]}", severity="warning", rule=r)
    cov = f"{len(passed)} rules passed, {len(failed)} failed, {len(skipped)} skipped, {len(partial)} partial"
    detail = {"passed": passed, "failed": failed, "skipped": skipped, "partial": partial,
              "model": f"corrected/{table}", "total_us": rep.result.total_us,
              "total_cost": rep.result.total_cost, "total_steps": rep.result.total_steps}
    if failed:
        st.done("failed", cov, **detail)
    elif bad_skips or bad_partial:
        st.done("partial", cov + "; required rules were not all checked", **detail)
    else:
        st.done("passed", cov, **detail)


def _correspondence_stage(st: _Stage, release: TaskRelease, final, certified) -> None:
    """final = certified + allowed insertions, by instruction identity.

    An instruction the final program shares with the certified one (same id) must be the
    same instruction -- same type, operands, template, claims -- in the same order.  The
    one field allowed to differ is the provenance index `meta.call`
    (`qccd.ir.provenance.CALL_KEY`), which passes stamp onto untagged instructions and
    which points into the program's own provenance table rather than describing the
    operation.  Every instruction the final program ADDS must be of an allowed type
    (`insert:cool`) and must carry a fresh id.
    """
    from ..ir.provenance import CALL_KEY
    allowed = {t.split(":", 1)[1] for t in release.manifest.get("allowed_transformations") or []
               if t.startswith("insert:")}
    cids = [i.id for i in certified.instructions]
    cset = set(cids)
    if len(cset) != len(cids):
        st.diag("CORR.DUPLICATE_IDS", "the certified program repeats instruction ids")
        st.done("failed", "instruction identity")
        return
    inserted = [i for i in final.instructions if i.id not in cset]
    bad = [i for i in inserted if i.type not in allowed]
    if bad:
        st.diag("CORR.FOREIGN_INSTRUCTION", f"the final program adds {len(bad)} instruction(s) of types "
                f"{sorted({i.type for i in bad})} that are not allowed transformations {sorted(allowed)}",
                instructions=[i.id for i in bad[:20]])
    kept = [i for i in final.instructions if i.id in cset]
    if [i.id for i in kept] != cids:
        missing = sorted(cset - {i.id for i in kept})
        st.diag("CORR.ORDER", f"the final program does not keep every certified instruction in order "
                f"(missing {missing[:10]})", instructions=missing[:10] or None)
    by_id = {i.id: i for i in certified.instructions}

    def norm(ins):
        j = normalize_numbers(ins.to_json())
        meta = dict(j.get("meta") or {})
        meta.pop(CALL_KEY, None)
        j["meta"] = meta
        return canonical_text(j)
    changed = [i.id for i in kept if norm(i) != norm(by_id[i.id])]
    if changed:
        st.diag("CORR.CHANGED", f"{len(changed)} certified instruction(s) were altered in the final program",
                instructions=changed[:20])
    if bad or changed or [i.id for i in kept] != cids:
        st.done("failed", "identity, order and content of every certified instruction; types of insertions")
        return
    st.done("passed", f"every certified instruction kept, in order, unchanged (ignoring the provenance index "
            f"{CALL_KEY!r}); {len(inserted)} inserted {sorted(allowed)} instruction(s) with fresh ids",
            inserted=len(inserted), certified_instructions=len(cids))


def _binding_stage(st: _Stage, release: TaskRelease, tc: Toolchain, cw: Path, cert: Mapping,
                   certified, limits: Mapping, cancel) -> None:
    if tc.qccdc is None:
        st.diag("TOOLCHAIN.QCCDC_MISSING", "the compiler (qccdc_cli) is not installed, so the task "
                "circuit cannot be parsed to bind the certificate")
        st.done("unsupported", "needs qccdc_cli parse")
        return
    r = run_limited([tc.qccdc, "parse", cw / "circuit.qasm", "-o", cw / "circuit.parsed.json"],
                    cwd=cw, timeout=float(limits.get("compile_timeout_s", 600)), mem_mb=4096, cancel=cancel)
    if r.status in ("timeout", "cancelled"):
        st.done(r.status, "qccdc_cli parse")
        return
    if not r.ok:
        st.diag("TOOLCHAIN.PARSE_FAILED", f"parsing the trusted circuit failed: {r.stderr[-500:] or r.stdout[-500:]}")
        st.done("internal_error", "qccdc_cli parse")
        return
    parsed = json.loads((cw / "circuit.parsed.json").read_text(encoding="utf-8"))
    want = [(o["i"], o["name"], list(o["qubits"]), [float(x) for x in o.get("params") or []])
            for o in parsed["ops"]]
    got = [(o.get("i"), o.get("name"), list(o.get("qubits") or []), [float(x) for x in o.get("params") or []])
           for o in cert.get("circuit_ops") or []]
    problems = []
    if cert.get("n_qubits") != parsed.get("n_qubits"):
        problems.append(f"the certificate is for {cert.get('n_qubits')} qubits, the task circuit has "
                        f"{parsed.get('n_qubits')}")
    if got != want:
        k = next((i for i in range(min(len(got), len(want))) if got[i] != want[i]), min(len(got), len(want)))
        problems.append(f"the certificate's circuit differs from the task circuit at op {k} "
                        f"({got[k] if k < len(got) else 'missing'} vs {want[k] if k < len(want) else 'missing'})")
    if cert.get("unrealised"):
        problems.append(f"{len(cert['unrealised'])} circuit ops are unrealised: a partial program")
    # the gate witnesses must name certified-program instructions with matching stamps
    try:
        from ..ir import source_map
        source_map.build(certified, dict(cert), cw / "circuit.qasm", check=True)
    except Exception as exc:
        problems.append(f"gate witnesses do not match the certified program: {type(exc).__name__}: {exc}")
    for p in problems:
        st.diag("CERT.BINDING", p)
    if problems:
        st.done("failed", "circuit ops (compiler parser), qubit count, coverage, witness stamps")
    else:
        st.done("passed", "circuit ops equal the release circuit as parsed by qccdc_cli; "
                "every gate witness names a certified instruction with the same op stamp",
                ops=len(want))


def _lean_stage(st: _Stage, tc: Toolchain, cw: Path, limits: Mapping, cancel) -> None:
    if tc.qcheck is None:
        st.diag("TOOLCHAIN.QCHECK_MISSING", "the proved Lean checker (qcheck) is not installed; the "
                "certificate cannot be decided", severity="error")
        st.done("unsupported", "needs qcheck")
        return
    exp = run_limited([tc.python, tc.bridge / "export_arch.py", cw / "device.arch.json",
                       "-o", cw / "device.expanded.json"], cwd=cw, timeout=300, mem_mb=4096, cancel=cancel)
    if not exp.ok:
        st.diag("TOOLCHAIN.EXPORT_FAILED", (exp.stderr or exp.stdout)[-800:])
        st.done("cancelled" if exp.status == "cancelled" else "internal_error", "export_arch")
        return
    mk = run_limited([tc.python, tc.bridge / "mk_qcheck_input.py", cw / "prog", "--arch",
                      cw / "device.expanded.json", "-o", cw / "prog.qcheck.json"],
                     cwd=cw, timeout=300, mem_mb=4096, cancel=cancel)
    if not mk.ok:
        st.diag("TOOLCHAIN.QCHECK_INPUT_FAILED", (mk.stderr or mk.stdout)[-800:])
        st.done("cancelled" if mk.status == "cancelled" else "internal_error", "mk_qcheck_input")
        return
    r = run_limited([tc.qcheck, cw / "prog.qcheck.json"], cwd=cw,
                    timeout=float(limits.get("lean_timeout_s", 1800)), mem_mb=int(limits.get("lean_mem_mb", 12288)),
                    cancel=cancel)
    if r.status in ("timeout", "cancelled"):
        st.diag(f"CERT.LEAN_{r.status.upper()}", f"the Lean checker did not finish ({r.status} after {r.seconds:.0f} s)")
        st.done(r.status, "qcheck")
        return
    out = r.stdout
    if "ACCEPTED" in out:
        st.done("passed", "QCCDC.Cert.check (proved sound: check_sound) accepted the certificate against "
                "hop/gate-site facts re-derived from the submitted device")
        return
    why = [ln.strip()[2:] for ln in out.splitlines() if ln.strip().startswith("- ")]
    if "REJECTED" in out:
        for w in why[:20] or ["(no reason printed)"]:
            st.diag("CERT.LEAN_REJECTED", w)
        st.done("failed", "qcheck")
        return
    # neither verdict: a crash (qcheck OOMs on large certificates and prints nothing useful)
    st.diag("CERT.LEAN_NO_VERDICT", f"qcheck exited {r.returncode} without a verdict: "
            f"{(r.stderr or out)[-400:]}")
    st.done("internal_error", "qcheck produced no verdict")


def _semantics_stage(st: _Stage, tc: Toolchain, cw: Path, limits: Mapping, cancel) -> None:
    r = run_limited([tc.python, tc.bridge / "check_cert.py", cw / "prog", "--qasm", cw / "circuit.qasm",
                     "--arch", cw / "device.arch.json", "--json"], cwd=cw,
                    timeout=float(limits.get("lean_timeout_s", 1800)), mem_mb=8192, cancel=cancel)
    if r.status in ("timeout", "cancelled"):
        st.done(r.status, "check_cert O1/O2")
        return
    try:
        verdict = json.loads(r.stdout[r.stdout.index("{"):])
    except (ValueError, IndexError):
        st.diag("TOOLCHAIN.CHECK_CERT_FAILED", (r.stderr or r.stdout)[-800:])
        st.done("internal_error", "check_cert produced no verdict")
        return
    o1 = verdict.get("o1_transport")
    o2 = str(verdict.get("o2_semantics", ""))
    unreal = verdict.get("unrealised_ops", 0)
    detail = {"o1_transport": o1, "o2_semantics": o2, "method": verdict.get("method"),
              "unrealised_ops": unreal, "circuit_sha256": verdict.get("circuit_sha256")}
    if o1 != "ok":
        for p in (o1 if isinstance(o1, list) else [str(o1)]):
            st.diag("SEM.O1", f"transport/co-location: {p}")
    if unreal:
        st.diag("SEM.UNREALISED", f"{unreal} circuit ops are not realised by the program")
    if "MISMATCH" in o2:
        st.diag("SEM.O2_MISMATCH", f"the program does not implement the task circuit: {o2}")
    if o1 != "ok" or unreal or "MISMATCH" in o2:
        st.done("failed", "O1 transport replay; O2 pulses vs trusted circuit", **detail)
    elif o2.startswith("ok"):
        st.done("passed", "O1 transport replay of the certificate; O2 " + str(verdict.get("method")), **detail)
    else:
        st.diag("SEM.O2_NOT_ESTABLISHED", f"semantics not established: {o2}", severity="warning")
        st.done("partial", "O1 only: the circuit is outside the checked fragment", **detail)


def _cancel_rest(stages: dict, order: list, finish: Callable) -> dict:
    for sid in order:
        if stages[sid].status == "skipped" and not stages[sid].coverage:
            stages[sid].done("cancelled", "the job was cancelled before this stage ran")
    return finish()


# ---------------------------------------------------------------------- report

def _report(release: TaskRelease, profile: str, stages: dict, order: list, ctx: dict,
            tc: Toolchain, started: float, run: Mapping | None) -> dict:
    b: Bundle | None = ctx.get("bundle")
    reasons = []
    ran_out = next((sid for sid in order if stages[sid].status in ("failed", "internal_error", "cancelled",
                                                                       "timeout")), None)
    for sid in order:
        s = stages[sid]
        if s.status == "skipped" and not s.coverage:
            s.coverage = f"not run: stage {ran_out!r} ended the grading" if ran_out else "not run"
        if s.required and s.status != "passed":
            reasons.append(f"{sid}: {s.status}")
    eligible = profile == "reference" and not reasons and all(
        stages[s].status == "passed" for s in release.required_checks)
    if profile != "reference":
        reasons.insert(0, f"profile {profile!r} is not the reference profile")
    diags = [d for sid in order for d in stages[sid].diagnostics]
    ident = evaluator_identity()
    ident["toolchain"] = tc.identity()
    rep = {
        "kind": REPORT_KIND, "version": 1,
        "task": {"id": release.id, "digest": release.digest},
        "bundle": {"digest": b.digest if b else None, "files": b.file_digests() if b else {}},
        "input": (b.manifest.get("provenance") if b else None) or {},
        "evaluator": ident,
        "profile": profile,
        "stages": [stages[s].to_json() for s in order],
        "eligibility": {"eligible": bool(eligible), "policy": "every required check passed under the "
                        "reference profile", "required": release.required_checks if profile == "reference" else [],
                        "reasons": reasons},
        "metrics": ctx.get("metrics") or {},
        "rank_by": release.manifest.get("rank_by"),
        "cost_breakdown": ctx.get("breakdown") or {},
        "diagnostics": diags[:1000],
        "numerical_policy": release.manifest.get("numerical_policy"),
        "run": dict(run or {}),
        "timing": {"started_at": started, "finished_at": time.time(),
                   "wall_seconds": round(time.time() - started, 3),
                   "stage_seconds": {s: stages[s].seconds for s in order}},
    }
    return rep


#: fields that legitimately differ between two gradings of the same inputs
NONDETERMINISTIC = ("timing", "run")


def comparable(report: Mapping) -> dict:
    """The part of a report two graders must agree on (local vs official parity)."""
    out = {k: v for k, v in report.items() if k not in NONDETERMINISTIC}
    ev = dict(out.get("evaluator") or {})
    out["evaluator"] = {k: ev.get(k) for k in ("name", "version")}
    return out


def metrics_agree(a: Mapping, b: Mapping, policy: Mapping | None) -> list:
    """Metric names whose values differ beyond the numerical policy."""
    pol = dict(policy or {})
    rel, abs_ = float(pol.get("float_rel_tol", 0.0)), float(pol.get("float_abs_tol", 0.0))
    bad = []
    for k in sorted(set(a) | set(b)):
        va, vb = (a.get(k) or {}).get("value"), (b.get(k) or {}).get("value")
        if isinstance(va, (int, float)) and isinstance(vb, (int, float)):
            if abs(va - vb) > max(abs_, rel * max(abs(va), abs(vb))):
                bad.append(k)
        elif va != vb:
            bad.append(k)
    return bad
