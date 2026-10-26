"""One program from FormalRV's `.prog` to checked ions, with every layer kept and linked.

    .prog --prepare_qppm--> .qppm --prepare_qec--> .qec --qec_sys_staged--> .sysprog   (FormalRV, pinned)
          FormalRV's VM admits the .sysprog on the machine profile           (the ABSTRACT estimate)
    .sysprog --sysprog.expand--> events   (checked: the VM's opcode counts and instruction count)
    events --provenance.link--> each .sysprog line's .qec statement, its .qppm PPM, its .prog line
    events --devirt--> a circuit on physical qubits --qccdc--> TSIR + certificate  (the CONCRETE run)
    TSIR: the hardware rules by replay under the pinned physics; R10's two halves (the proved Lean
          checker on the certificate; check_cert.py against the very circuit emitted here)

The result is `trace.json` beside the compiled program: everything a page needs to say, at any
instant, which logical operation, PPM, Clifford circuit and hardware instruction is running.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import time
from pathlib import Path

from . import devirt, formalrv, layers, provenance, sysprog as sp

REPO = Path(__file__).resolve().parents[2]
PHYSICS = {"physics@2": (REPO / "qccd" / "workspace" / "physics" / "qccd-physics@2.json", "quantinuum")}
FIXED_BLOCKS = ("primitives", "heating", "species", "budget")
SCHEMA = "qccd.fullstack/0.1"


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    if p.is_dir():
        for f in sorted(p.rglob("*")):
            if f.is_file():
                h.update(f.relative_to(p).as_posix().encode())
                h.update(f.read_bytes())
    else:
        h.update(p.read_bytes())
    return h.hexdigest()


def device_with_physics(arch: Path, physics: str) -> dict:
    """The device document with the pinned physics' fixed blocks written in (as a board does)."""
    doc = json.loads(Path(arch).read_text(encoding="utf-8"))
    path, _ = PHYSICS[physics]
    ref = json.loads(path.read_text(encoding="utf-8"))
    doc.update({b: ref[b] for b in FIXED_BLOCKS if b in ref})
    return doc


def _stage_text(stage: layers.Stage) -> dict:
    return {"path": stage.path, "members": [[m, f, n] for m, f, n in stage.members], "lines": stage.lines}


def run(prog: Path, *, machine: Path, sysspec: Path, arch: Path, out: Path, code: str = "surface",
        distance: int = 3, specialize: dict | None = None, physics: str = "physics@2",
        staged: bool = True) -> dict:
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    log: dict = {"schema": SCHEMA, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
                 "formalrv": formalrv.provenance(), "physics": physics, "steps": []}
    t0 = time.time()

    def step(name, fn):
        t = time.time()
        r = fn()
        log["steps"].append({"step": name, "s": round(time.time() - t, 3)})
        return r

    stem = Path(prog).name.split(".")[0]
    qppm_p, qec_p, sys_p = out / f"{stem}.qppm", out / f"{stem}.qec", out / f"{stem}.sysprog"
    for p in (qppm_p, qec_p, sys_p):
        if p.is_dir():
            shutil.rmtree(p)
        elif p.exists():
            p.unlink()
    step("prog -> qppm (FormalRV prepare_qppm)", lambda: formalrv.prog_to_qppm(prog, qppm_p))
    step("qppm -> qec (FormalRV prepare_qec)", lambda: formalrv.qppm_to_qec(qppm_p, qec_p, code, distance, specialize))
    step("qec -> sysprog (FormalRV qec_sys_staged)", lambda: formalrv.qec_to_sys(qec_p, machine, sysspec, sys_p, staged))
    vm = step("FormalRV VM", lambda: formalrv.vm(sys_p))
    (out / "vm.json").write_text(json.dumps(vm, indent=1), encoding="utf-8")

    listing = sp.read(sys_p)
    expansion = step("expand .sysprog", lambda: sp.expand(listing))
    diffs = sp.vm_counts_agree(expansion, vm)
    if diffs:
        raise RuntimeError("the expansion disagrees with FormalRV's VM: " + "; ".join(diffs))
    st_prog, st_qppm, st_qec = (layers.read(prog, "prog"), layers.read(qppm_p, "qppm"), layers.read(qec_p, "qec"))
    chain = step("link the layers", lambda: provenance.link(st_prog, st_qppm, st_qec, expansion))
    codeinfo = devirt.code_from_qec(st_qec)
    dv = step("devirtualize", lambda: devirt.devirtualize(expansion, codeinfo))

    cw = out / "hw"
    if cw.exists():
        shutil.rmtree(cw)
    cw.mkdir()
    dev = device_with_physics(arch, physics)
    (cw / "device.arch.json").write_text(json.dumps(dev, indent=1), encoding="utf-8")
    from ..qec import compile_with_qccdc
    tsir_p, cert_p = step("compile (qccdc)", lambda: compile_with_qccdc(dv.qasm, cw / "device.arch.json", cw,
                                                                    mode="compile", timeout=3000))
    checks = step("check the hardware program", lambda: check_hardware(cw, physics))

    # ------------------------------------------------------------------ the trace
    tsir = json.loads(Path(tsir_p).read_text(encoding="utf-8"))
    instr_ops = {}
    for ins in tsir["instructions"]:
        ops = (ins.get("meta") or {}).get("op")
        if ops:
            instr_ops[ins["id"]] = ops
    events = []
    for e in expansion.events:
        events.append([e.uid, e.kind, e.start, e.duration, e.node.line,
                       [[f.kind, f.node.line, f.label, f.start] for f in e.frames],
                       [f"{r}[{i}]" for r, i in e.qubits],
                       [dv.phys.get((r, i)) for r, i in e.qubits]])
    trace = {
        **log,
        "program": stem,
        "code": codeinfo, "distance": distance,
        "layers": {"prog": _stage_text(st_prog), "qppm": _stage_text(st_qppm), "qec": _stage_text(st_qec),
                   "sysprog": {"path": str(sys_p), "members": [[m, f, n] for m, f, n in listing.members],
                               "lines": listing.lines}},
        "links": {"sys": {ln: [l.stmt.line, l.how] for ln, l in chain.sys_to_qec.items()},
                  "qec": {ln: [l.stmt.line, l.how] for ln, l in chain.qec_to_qppm.items()},
                  "qppm": {ln: [l.stmt.line, l.how] for ln, l in chain.qppm_to_prog.items()}},
        "events": events,
        "calls": [[c.uid, c.name, c.start, c.end, c.node.line, c.parent] for c in expansion.calls],
        "circuit": [[o.index, o.text, o.event, o.role] for o in dv.ops],
        "physical_qubits": dv.n_qubits,
        "factory": dv.factory,
        "hardware": {"tsir": Path(tsir_p).name, "certified": "prog.tsir.json", "certificate": Path(cert_p).name,
                     "device": "device.arch.json", "instructions": len(tsir["instructions"])},
        "instr_ops": instr_ops,
        "abstract": {"vm_status": vm.get("status"), "runtime_ns": vm.get("runtime_upper_bound_ns"),
                     "peak_qubits": vm.get("peak_qubits_upper_bound"),
                     "dynamic_instructions": vm.get("dynamic_instruction_count")},
        "concrete": {"runtime_us": checks.get("rules", {}).get("total_us"), "ions": dv.n_qubits,
                     "instructions": len(tsir["instructions"])},
        "checks": checks,
        "inputs": {"prog": _sha(Path(prog)), "machine": _sha(Path(machine)), "sysspec": _sha(Path(sysspec)),
                   "arch": _sha(Path(arch))},
        "total_s": round(time.time() - t0, 1),
    }
    from .consistency import check as check_stack
    certified = json.loads((cw / "prog.tsir.json").read_text(encoding="utf-8"))
    trace["checks"]["stack"] = step("check the stack at every instruction", lambda: check_stack(trace, certified))
    trace["steps"] = log["steps"]
    (out / "trace.json").write_text(json.dumps(trace, separators=(",", ":")), encoding="utf-8")
    return trace


# ----------------------------------------------------------------------------- the hardware checks

def check_hardware(cw: Path, physics: str) -> dict:
    """Rules by replay; certified -> final correspondence (cooling insertions only); R10's two
    halves.  Each verdict is recorded with what it covers; nothing here is summarised as proved
    that is not."""
    from ..arch import load
    from ..cost.models import corrected_model
    from ..ir import TSIR
    from ..verify import verify
    from ..workspace.evaluator import Toolchain
    out: dict = {}
    _, table = PHYSICS[physics]
    final = TSIR.load(cw / "prog.cooled.tsir.json")
    certified = TSIR.load(cw / "prog.tsir.json")
    arch = load(cw / "device.arch.json")
    rep = verify(final, arch, corrected_model(table), check_metrics=False)
    s = rep.rules.summary()
    out["rules"] = {"table": table, "passed": sorted(set(s.get("passed") or []) - set(s.get("partial") or {})),
                    "failed": sorted(s.get("failed") or []), "skipped": dict(s.get("skipped") or {}),
                    "partial": dict(s.get("partial") or {}), "total_us": rep.result.total_us,
                    "violations": [[v.rule, v.message[:300]] for v in rep.rules.violations[:50]]}
    cids = [i.id for i in certified.instructions]
    cset = set(cids)
    inserted = [i for i in final.instructions if i.id not in cset]
    kept = [i.id for i in final.instructions if i.id in cset]
    out["correspondence"] = {"ok": kept == cids and all(i.type == "cool" for i in inserted),
                             "inserted": len(inserted), "inserted_types": sorted({i.type for i in inserted})}
    tc = Toolchain.discover()
    py = tc.python
    bridge = Path(tc.bridge)

    def run(args, timeout=3600):
        r = subprocess.run([str(a) for a in args], capture_output=True, text=True, cwd=str(cw), timeout=timeout,
                           encoding="utf-8", errors="replace")
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    rc, txt = run([py, bridge / "check_cert.py", cw / "prog", "--qasm", cw / "circuit.qasm",
                   "--arch", cw / "device.arch.json", "--json"])
    try:
        v = json.loads(txt[txt.index("{"):txt.rindex("}") + 1])
    except ValueError:
        v = {"error": txt[-800:]}
    out["r10_semantic"] = {"checker": "Compiler/bridge/check_cert.py", "o1_transport": v.get("o1_transport"),
                           "o2_semantics": v.get("o2_semantics"), "order": v.get("order"),
                           "unrealised_ops": v.get("unrealised_ops"), "method": v.get("method"),
                           "error": v.get("error")}
    if tc.qcheck is None:
        out["r10_lean"] = {"status": "not run", "why": "qcheck is not installed"}
    else:
        rc, txt = run([py, bridge / "mk_qcheck_input.py", cw / "prog", "--arch", cw / "device.expanded.json",
                       "-o", cw / "prog.qcheck.json"])
        if rc:
            out["r10_lean"] = {"status": "error", "why": txt[-600:]}
        else:
            rc, txt = run([tc.qcheck, cw / "prog.qcheck.json"])
            status = "accepted" if "ACCEPTED" in txt else "rejected" if "REJECTED" in txt else "no verdict"
            out["r10_lean"] = {"status": status, "checker": "qcheck (QCCDC.Cert.check, proved sound: check_sound)",
                               "detail": [ln.strip() for ln in txt.splitlines() if ln.strip().startswith("- ")][:20]}
    return out
