"""Compile the H2-T1 circuits on h2.arch.json with the published compiler, verify every
program the compiler leaves, count two-qubit rounds, and write results.json.

    set QCCD_QCCDC=<the published qccdc_cli>
    python Reproduce/moses2023/run_h2.py <circuits dir from circuits.py> <work dir>
           [--only ghz,mb,...] [--limit N] [--checks <check_rewrites log>]

A ROUND is one `gate` instruction carrying two-qubit pairs (an MS instruction): the
compiler emits every pair of one round as one instruction, so the count is exact.  It is
a scheduling proxy, not a time; the document's primitive times are unit times.

Besides the compiler, each circuit gets the bounds its own structure implies on a machine
with four gate zones (no compiler involved):
    depth      the longest chain of two-qubit gates through shared qubits (ASAP layers)
    floor      max(depth, ceil(gates / 4)): no schedule with <= 4 gates a round does better
    layered    sum over ASAP layers of ceil(|layer| / 4): the paper's own procedure
               (Sec. II.E: largest layer of gates, executed in batches of four DG zones)
               with every batch full and transport free
"""

from __future__ import annotations

import collections
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

from qccd.cost import corrected_model  # noqa: E402
from qccd.qec import CompileFailed, compile_with_qccdc, load_arch, load_program  # noqa: E402
from qccd.verify import verify  # noqa: E402

ARCH = HERE / "h2.arch.json"
ZONES = 4

#: H2-T1 (Table I of 2305.03828): # 2Q gates, # 2Q gate rounds
PAPER = {
    "ghz": {"row": "GHZ", "qubits": 32, "gates": 31, "rounds": 14},
    "mb": {"row": "MB, l=10", "qubits": 32, "gates": 320, "rounds": 80},
    "qv": {"row": "QV", "qubits": 16, "gates": 310, "rounds": 128,
           "note": "Table I lists one example circuit; the text gives an average of 296 "
                   "two-qubit gates over the 200 circuits (Sec. IV.B)"},
    "rcs": {"row": "RCS", "qubits": 32, "gates": 172, "rounds": 56},
    "qaoa": {"row": "QAOA, p=2", "qubits": 32, "gates": 96, "rounds": 66},
}

STMT = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\(([^)]*)\))?\s+([^;]*);\s*$")


def sha256(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ------------------------------------------------------------------ circuit bounds

def bounds(text: str) -> dict:
    last: dict[str, int] = collections.defaultdict(int)
    layers: dict[int, int] = collections.defaultdict(int)
    n2q = 0
    for line in text.splitlines():
        m = STMT.match(line.strip())
        if not m or m.group(1) in ("OPENQASM", "include", "qreg", "creg", "measure"):
            continue
        name, args = m.group(1), [a.strip() for a in m.group(3).split(",")]
        if name == "barrier":                      # a requested time-ordering (Sec. II.E)
            floor = max(last[a] for a in args)
            for a in args:
                last[a] = floor
            continue
        if name in ("rxx", "cx") and len(args) == 2:
            n2q += 1
            lay = max(last[args[0]], last[args[1]]) + 1
            last[args[0]] = last[args[1]] = lay
            layers[lay] += 1
    depth = max(layers, default=0)
    return {"gates": n2q, "depth": depth, "floor": max(depth, math.ceil(n2q / ZONES)),
            "layered": sum(math.ceil(k / ZONES) for k in layers.values()),
            "widest_layer": max(layers.values(), default=0)}


# ------------------------------------------------------------------ one program

def program_stats(tsir: Path, cert: Path | None) -> dict:
    doc = json.loads(tsir.read_text(encoding="utf-8"))
    ins = doc["instructions"]
    ms = [i for i in ins if i.get("type") == "gate" and i.get("pairs")]
    st = {
        "instructions": len(ins),
        "rounds": len(ms),
        "pairs": sum(len(i["pairs"]) for i in ms),
        "pairs_per_round": dict(sorted(collections.Counter(len(i["pairs"]) for i in ms).items())),
        "transport_cycles": sum(1 for i in ins if i.get("type") == "simd"),
        "cool": sum(1 for i in ins if i.get("type") == "cool"),
        "measure_instructions": sum(1 for i in ins if i.get("type") == "measure"),
    }
    if cert is not None and cert.exists():
        c = json.loads(cert.read_text(encoding="utf-8"))
        names = {o["i"]: o["name"] for o in c["circuit_ops"]}
        un = c.get("unrealised") or []
        st["circuit_ops"] = len(names)
        st["unrealised"] = len(un)
        st["unrealised_by_gate"] = dict(collections.Counter(names[i] for i in un))
    return st


def rule_verdicts(tsir: Path, arch) -> dict:
    rep = verify(load_program(tsir), arch, corrected_model(table="local"))
    s = rep.rules.summary()
    by = collections.defaultdict(list)
    for v in rep.rules.violations:
        by[v.rule].append(str(v))
    return {"failed": sorted(s["failed"]), "passed": sorted(s["passed"]),
            "skipped": sorted(s["skipped"]), "partial": sorted(s["partial"]),
            "violations": {r: {"count": len(v), "first": v[:2]} for r, v in sorted(by.items())}}


def compile_one(text: str, work: Path, arch) -> dict:
    work.mkdir(parents=True, exist_ok=True)
    for stale in work.glob("prog*"):
        stale.unlink()
    t0 = time.time()
    rec: dict = {}
    try:
        prog, cert = compile_with_qccdc(text, ARCH, work, mode="compile", timeout=900)
        rec["verdict"] = "compiled"
        rec["program"] = program_stats(prog, cert)
        rec["rules"] = rule_verdicts(prog, arch)
    except CompileFailed as exc:
        rec["verdict"] = f"refused ({exc.kind})"
        rec["reason"] = str(exc)
        raw, cert = work / "prog.tsir.json", work / "prog.qcert.json"
        if raw.exists():
            # what the compiler DID emit before declining: a partial program, verified as is
            rec["partial_program"] = program_stats(raw, cert)
            rec["partial_rules"] = rule_verdicts(raw, arch)
    rec["seconds"] = round(time.time() - t0, 1)
    return rec


# ------------------------------------------------------------------ minimal examples

MINIMAL = [
    {"id": "M1_barrier",
     "claim": "a barrier on more than two qubits crashes the compiler before placement",
     "qasm": 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[3];\ncreg c[3];\nh q[0];\n'
             'barrier q[0],q[1],q[2];\nmeasure q[0] -> c[0];\n',
     "init": None},
    {"id": "M2_five_parallel_gates",
     "claim": "five independent two-qubit gates on a machine with four gate zones: the "
              "fifth is left unrealised instead of running in a second round",
     "qasm": 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[10];\ncx q[0],q[1];\n'
             'cx q[2],q[3];\ncx q[4],q[5];\ncx q[6],q[7];\ncx q[8],q[9];\n',
     "init": {f"q{i}": f"AUX{'D' if i < 5 else 'U'}{i % 5}" for i in range(10)}},
    {"id": "M3_bystanders_in_gate_zones",
     "claim": "one two-qubit gate, every gate zone holding one idle qubit: the gate is "
              "left unrealised; nothing is moved out of a gate zone to make room",
     "qasm": 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[6];\ncx q[0],q[1];\n',
     "init": {"q0": "AUXD0", "q1": "AUXD1", "q2": "DG01", "q3": "DG02", "q4": "DG03",
              "q5": "DG04"}},
    {"id": "M4_conveyor_moved_one_ion",
     "claim": "one gate whose first operand waits in a conveyor well beside an idle qubit "
              "in another well: the compiler pulls the one ion out of the broadcast "
              "conveyor while the other, on the same three tied signals, stays -- the "
              "compile succeeds and the program fails R4 (drivability)",
     "qasm": 'OPENQASM 2.0;\ninclude "qelib1.inc";\nqreg q[3];\ncx q[0],q[1];\n',
     "init": {"q0": "CR01", "q1": "AUXD3", "q2": "CR03"}},
]


def release_of(exe: Path, *, version_only: bool = False) -> str | None:
    """Which published release a compiler binary is: its sha256 looked up in the toolchain
    manifest (qccd/workspace/toolchain.json); a binary not there is an unreleased build."""
    man = json.loads((ROOT / "qccd" / "workspace" / "toolchain.json").read_text(encoding="utf-8"))
    q = man["qccdc_cli"]
    digest = sha256(Path(exe))
    for plat, a in q["assets"].items():
        if a.get("sha256") == digest or a.get("unpacked_sha256") == digest:
            return q["version"] if version_only else f"qccdc_cli, release {q['version']} ({plat})"
    return None if version_only else "qccdc_cli, an unreleased build"


def run_minimal(work: Path, arch) -> list[dict]:
    qccdc = os.environ["QCCD_QCCDC"]
    work.mkdir(parents=True, exist_ok=True)
    exp = work / "h2.expanded.json"
    subprocess.run([sys.executable, str(ROOT / "Compiler/bridge/export_arch.py"), str(ARCH),
                    "-o", str(exp)], check=True, capture_output=True)
    out = []
    for m in MINIMAL:
        q = work / f"{m['id']}.qasm"
        q.write_text(m["qasm"], encoding="utf-8")
        cmd = [qccdc, "compile", str(q), "--arch", str(exp), "-o", str(work / m["id"]),
               "--no-rotate"]
        if m["init"]:
            ip = work / f"{m['id']}.init.json"
            ip.write_text(json.dumps(m["init"]), encoding="utf-8")
            cmd += ["--init-placement", str(ip)]
        cp = subprocess.run(cmd, capture_output=True, text=True)
        rec = {**m, "command": "qccdc compile <qasm> --arch <h2 expanded> -o <out> --no-rotate"
                              + (" --init-placement <init>" if m["init"] else ""),
               "exit": cp.returncode,
               "said": [ln for ln in (cp.stdout + cp.stderr).splitlines()
                        if "UNREALISED" in ln or "error" in ln.lower() or "deferred" in ln][:4]}
        tsir, cert = work / f"{m['id']}.tsir.json", work / f"{m['id']}.qcert.json"
        if tsir.exists():
            rec["program"] = program_stats(tsir, cert)
            rec["rules"] = rule_verdicts(tsir, arch)
        out.append(rec)
        print(f"  {m['id']}: exit {cp.returncode}; " + "; ".join(rec["said"])
              + (f"; rules failed {rec['rules']['failed']}" if "rules" in rec else ""))
    return out


# ------------------------------------------------------------------ main

def summarise(vals: list[int | float]) -> dict:
    v = sorted(vals)
    if not v:
        return {}
    return {"n": len(v), "min": v[0], "median": v[len(v) // 2], "mean": round(sum(v) / len(v), 2),
            "max": v[-1]}


def main(argv: list[str]) -> int:
    # absolute: compile_with_qccdc runs its tools with cwd = workdir and hands them
    # workdir-relative paths, so a relative workdir fails as "export_arch failed"
    circ, work = Path(argv[0]).resolve(), Path(argv[1]).resolve()
    opts = dict(zip(argv[2::2], argv[3::2]))
    only = set(opts.get("--only", "ghz,mb,qv,rcs,qaoa").split(","))
    limit = int(opts.get("--limit", "100000"))
    arch = load_arch(ARCH)
    manifest = json.loads((circ / "manifest.json").read_text(encoding="utf-8"))
    qccdc = Path(os.environ["QCCD_QCCDC"])

    per: list[dict] = []
    counts: dict[str, int] = collections.Counter()
    for row in manifest:
        fam = row["family"]
        if fam not in only or counts[fam] >= limit:
            continue
        counts[fam] += 1
        text = (circ / "rewritten" / f"{row['id']}.qasm").read_text(encoding="utf-8")
        rec = {"family": fam, "id": row["id"], "source_sha256": row["source_sha256"],
               "rewritten_sha256": row["rewritten_sha256"],
               "zero_angle_components_dropped": row["zero_angle_dropped"],
               "bounds": bounds(text)}
        rec["compile"] = compile_one(text, work / row["id"], arch)
        if row["barriers"]:
            nb = (circ / "nobarrier" / f"{row['id']}.qasm").read_text(encoding="utf-8")
            rec["compile_without_barriers"] = compile_one(nb, work / (row["id"] + "_nb"), arch)
        per.append(rec)
        c = rec["compile"]
        extra = ""
        if "compile_without_barriers" in rec:
            nbc = rec["compile_without_barriers"]
            pp = nbc.get("partial_program") or nbc.get("program") or {}
            extra = (f" | no barriers: {nbc['verdict']}, unrealised "
                     f"{pp.get('unrealised')} {pp.get('unrealised_by_gate')}")
        pp = c.get("partial_program") or c.get("program") or {}
        print(f"{row['id']:18s} gates {rec['bounds']['gates']:4d} layered "
              f"{rec['bounds']['layered']:4d} | {c['verdict']} {c.get('reason', '')[:70]!r} "
              f"unrealised {pp.get('unrealised')}{extra}", flush=True)

    table = []
    for fam in ("ghz", "mb", "qv", "rcs", "qaoa"):
        rs = [r for r in per if r["family"] == fam]
        if not rs:
            continue
        verdicts = collections.Counter(r["compile"]["verdict"] for r in rs)
        ent = {"family": fam, **PAPER[fam], "circuits": len(rs),
               "ours_gates": summarise([r["bounds"]["gates"] for r in rs]),
               "depth": summarise([r["bounds"]["depth"] for r in rs]),
               "floor": summarise([r["bounds"]["floor"] for r in rs]),
               "layered": summarise([r["bounds"]["layered"] for r in rs]),
               "compiler_verdicts": dict(verdicts),
               "compiler_rounds": summarise([r["compile"]["program"]["rounds"] for r in rs
                                             if r["compile"]["verdict"] == "compiled"])}
        if fam == "qv":
            ex = [r for r in rs if r["bounds"]["gates"] == PAPER["qv"]["gates"]]
            ent["circuits_with_310_gates"] = [r["id"] for r in ex]
            ent["layered_of_those"] = sorted(r["bounds"]["layered"] for r in ex)
            ent["floor_of_those"] = sorted(r["bounds"]["floor"] for r in ex)
        table.append(ent)

    out = {
        "paper": {
            "key": "moses2023", "arxiv": "2305.03828", "version_read": "v2",
            "title": "A Race Track Trapped-Ion Quantum Processor",
            "venue": "Phys. Rev. X 13, 041052 (2023)",
            "artifact": {"url": "https://github.com/Quantinuum/quantinuum-hardware-h2-benchmark "
                                "(was CQCL/quantinuum-hardware-h2-benchmark)",
                         "commit": "d422a716977899d7d6603c9efadb44e89e495125",
                         "license": "none; README: (c) 2023 by Quantinuum. All Rights Reserved.",
                         "use": "read and run locally; no file of it is in this repository. "
                                "Circuits are identified by the sha256 of their QASM text."},
        },
        "device": {"arch": "Reproduce/moses2023/h2.arch.json", "sha256": sha256(ARCH),
                   "violations_R11_R18_R19_R20_R21": [str(v) for v in
                                                     __import__("qccd.api", fromlist=["Machine"])
                                                     .Machine.load(ARCH).violations()],
                   "gate_zones": [n for n in arch.device.nodes if arch.can(n, "gate")],
                   "sites": len(arch.device.nodes), "capacity_qubits": arch.device.total_capacity(),
                   "design": "Reproduce/moses2023/DESIGN.md"},
        "compiler": {"binary": release_of(qccdc), "release": release_of(qccdc, version_only=True),
                     "sha256": sha256(qccdc), "call": "qccd.qec.compile_with_qccdc(qasm, "
                     "h2.arch.json, workdir, mode='compile')",
                     "verifier_model": "corrected_model(table='local') -- unit times"},
        "rewrite": {"rules": "Reproduce/moses2023/circuits.py docstring",
                    "checks": opts.get("--checks")},
        "rounds_definition": "one gate instruction carrying two-qubit pairs (an MS round); a "
                             "scheduling proxy, not a time",
        "table": table,
        "circuits": per,
        "minimal_examples": run_minimal(work / "minimal", arch),
    }
    if out["rewrite"]["checks"] and Path(out["rewrite"]["checks"]).exists():
        out["rewrite"]["checks"] = Path(out["rewrite"]["checks"]).read_text(encoding="utf-8")
    (HERE / "results.json").write_text(json.dumps(out, indent=1) + "\n", encoding="utf-8")
    for t in table:
        print(json.dumps({k: t[k] for k in ("family", "gates", "rounds", "ours_gates", "floor",
                                            "layered", "compiler_verdicts")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
