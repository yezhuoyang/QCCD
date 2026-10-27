"""q06 -- the campaign: which (architecture, compilation) runs the BB round best, verified.

One function, `point`, takes a design and a compilation policy through the whole ladder
the study requires of a quoted number, and writes one JSON row:

    ring(W, H, V, dock_offset)  -->  export  -->  schedule (code-aware, or the stride
    default)  -->  QASM (steady-state resets)  -->  esm_check (the circuit measures
    exactly the stabilizers)  -->  qccdc rotate [--placement]  -->  insert cooling  -->
    the 23 rules  -->  score  -->  DRC / DACs / junctions / die area  -->  [R10 by Lean]

Two compilers, same device, same circuit:

    stride   the study's baseline: round-robin binding, stride placement, monotone sweep
    bridge   the code-aware schedule (`bb_schedule.py`) handed to the same OCaml pass via
             `--schedule` and `--placement`, so it is certified the same way

The score is what `Codesign/EVALUATION.md` asks for, with two corrections q05 forced:
resets are steady-state (144 regardless of ancilla count), and `p_eff`'s denominator is
the CIRCUIT's fault locations (2q gates + measures + resets), not the compiled pulse
count -- the OCaml pipeline emits 3,744 zero-error 1q pulses where the Python one emits
none, and a denominator that counts them makes the two incomparable.

    python Codesign/scripts/q06_campaign.py --exp E1 --json Codesign/data/q06_E1.json
    python Codesign/scripts/q06_campaign.py --exp E2 --json Codesign/data/q06_E2.json
    python Codesign/scripts/q06_campaign.py --robust Codesign/data/q06_E2.json \
        --json Codesign/data/q06_E3.json
    python Codesign/scripts/q06_campaign.py --point "H=3,V=24,A=24,compiler=bridge" --lean
"""

from __future__ import annotations

import argparse
import dataclasses as dc
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.arch.generators import _ring_slots  # noqa: E402
from qccd.cost import corrected_model, t2_metrics  # noqa: E402
from qccd.ir.tsir import TSIR, iter_pairs  # noqa: E402
from qccd.verify import replay  # noqa: E402

HERE = Path(__file__).resolve().parent
EXE = ROOT / "Compiler" / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"
BRIDGE = ROOT / "Compiler" / "bridge"
TEMPLATE = ROOT / "arch" / "ring144_24v.arch.json"
N_DATA, N_CHECKS, N_CONTACTS = 144, 144, 864

# EVALUATION.md section 3: the published sub-threshold curve, evaluated at our p_eff.
# An EXTRAPOLATION for ranking, never a claimed logical error rate.
P_REF, EPS_REF, D = 1e-3, 2e-7, 12
THRESHOLD = 7e-3


def sh(cmd: list[str], cwd: Path = ROOT) -> tuple[int, str]:
    env = {**os.environ,
           "PATH": os.path.expanduser(r"~\AppData\Local\opam\default\bin")
           + os.pathsep + os.environ.get("PATH", "")}
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, env=env,
                       cwd=str(cwd))
    return r.returncode, (r.stdout or "") + (r.stderr or "")


# --------------------------------------------------------------------------- devices


def ring_geometry(V: int, H: int, *, min_slots: int = N_DATA,
                  W: int | None = None) -> dict | None:
    """The smallest ring of height `H` with >= `min_slots` slots that `V` docks divide,
    and a dock offset that keeps every dock off the corners and the side slots.

    A corner dock's spur lies along the side rail and fails DRC; a side-slot dock's spur
    crosses the whole interior.  Neither costs anything in the transport model, so
    avoiding them is free -- when it is possible.  Returns None when no offset works.
    """
    widths = [W] if W else range(max(2, (min_slots - 2 * H + 4) // 2), 200)
    for w in widths:
        slots = _ring_slots(w, H)
        n = len(slots)
        if n < min_slots or n % V:
            continue
        sides = [s[0] for s in slots]
        bad = {i for i in range(n)
               if sides[i] != sides[i - 1] or sides[i] != sides[(i + 1) % n]
               or sides[i] in ("left", "right")}
        spacing = n // V
        for off in range(spacing):
            docks = {(off + i * spacing) % n for i in range(V)}
            if docks & bad:
                continue
            # a top and a bottom dock at the same x put two ancilla sites on one point
            xs = [slots[d][1] for d in docks]
            if len(set(xs)) != len(xs):
                continue
            return {"width": w, "height": H, "verticals": V, "dock_offset": off,
                    "slots": n}
    return None


def find_clean_ring(V: int, H: int, *, min_slots: int = N_DATA, max_slots: int = 216,
                    out: Path | None = None) -> dict:
    """The ring of height `H` with `V` docks that the DRC likes best.

    Scans every width whose slot count `V` divides, and every dock offset, and keeps the
    layout with the fewest violations (ties: fewer slots, then smaller offset).  A spur
    can land on a control pad or clear it depending on where it sits against the pad
    tiling, which is why the offset is searched rather than reasoned about.  Returns the
    geometry dict plus its DRC result; 'clean' is the flag the outer loop constrains on.
    """
    out = out or ROOT / "Compiler" / "build" / "q06" / "rings"
    out.mkdir(parents=True, exist_ok=True)
    best = None
    for w in range(max(2, (min_slots - 2 * H + 4) // 2), 200):
        n = 2 * w + 2 * H - 4
        if n < min_slots:
            continue
        if n > max_slots:
            break
        if n % V:
            continue
        spacing = n // V
        for off in range(spacing):
            g = {"width": w, "height": H, "verticals": V, "dock_offset": off, "slots": n}
            try:
                p = build_arch(out, g)
                if not positions_distinct(p):
                    # a top spur and a bottom spur at the same x end on the same point:
                    # two ancilla sites on one spot.  The DRC cannot see it -- coincident
                    # metal is one polygon -- so it is refused here, before the DRC.
                    continue
                b = buildability(p)
            except Exception:  # a degenerate geometry is a non-candidate
                continue
            key = (sum(b["drc"].values()), n, off)
            if best is None or key < best[0]:
                best = (key, g, b)
            if key[0] == 0:
                return {**g, "drc": b["drc"], "clean": True, "area_mm2": b["area_mm2"]}
    if best is None:
        return {}
    _, g, b = best
    return {**g, "drc": b["drc"], "clean": False, "area_mm2": b["area_mm2"]}


def positions_distinct(arch_path: Path) -> bool:
    """No two nodes on one point.  `device.check_structure` does not test positions."""
    arch = load(str(arch_path))
    seen = set()
    for nd in arch.device.nodes.values():
        key = (round(nd.pos[0], 6), round(nd.pos[1], 6))
        if key in seen:
            return False
        seen.add(key)
    return True


def build_arch(out: Path, geom: dict, name: str | None = None, *, switches: bool = True,
               template: Path = TEMPLATE) -> Path:
    """`ring144_24v` with only its geometry changed, as q05 did: primitives, wiring,
    T_coh and heating are the shipped ones, so every difference is the geometry.

    `switches=False` removes the per-site switch from the declared channel map: the
    spurs then share one waveform all-or-nothing, which R4d judges (q06d)."""
    doc = json.loads(template.read_text(encoding="utf-8"))
    if geom:
        g = {k: geom[k] for k in ("width", "height", "verticals", "dock_offset")}
        name = name or f"ring{geom['slots']}_h{g['height']}_v{g['verticals']}_o{g['dock_offset']}"
        doc["description"] = (f"q06: {geom['slots']}-slot loop, height {g['height']}, "
                              f"{g['verticals']} dock spurs offset {g['dock_offset']}")
        doc["geometry"]["params"].update(g)
    else:
        name = name or doc["name"]
    if not switches:
        name += "_noswitch"
        doc["control"]["channels"]["switch_per_site"] = False
        doc["description"] += " -- no per-site switch: the spurs are all-or-nothing"
    doc["name"] = name
    p = out / f"{name}.arch.json"
    p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    return p


# --------------------------------------------------------------------------- scoring


def patched(arch, *, T_coh_s=None, anomalous_rate=None, reset_error=None,
            measure_fidelity=None):
    sp, ht, sc = dict(arch.species), dict(arch.heating), dict(arch.primitives.scalars)
    if T_coh_s is not None:
        sp["T_coh_s"] = T_coh_s
    if anomalous_rate is not None:
        ht["anomalous_rate_quanta_per_ms"] = anomalous_rate
    if reset_error is not None:
        sc["reset"] = {**sc["reset"], "error": reset_error}
    if measure_fidelity is not None:
        sc["measure"] = {**sc["measure"], "fidelity": measure_fidelity}
    return dc.replace(arch, species=sp, heating=ht,
                      primitives=dc.replace(arch.primitives, scalars=sc))


def lambda_rate(p_eff: float, T_round_us: float) -> float:
    """Logical failures per second, from Bravyi's published point -- an extrapolation."""
    eps_l = EPS_REF * (p_eff / P_REF) ** ((D + 1) // 2)
    return eps_l / (T_round_us * 1e-6)


def broadcast_use(arch, prog: TSIR) -> dict:
    """How much work each broadcast instruction does: ions moved per transport
    instruction.  A rigid rotation moves every rider on the loop with one waveform; a
    dock moves every acting spur.  The schedules that win are the ones that put the
    most ions into each instruction -- which is the whole point of broadcast wiring."""
    loop_nodes: set[str] = set()
    for lp in arch.device.loops.values():
        if lp.closed:
            loop_nodes.update(lp.nodes)
    init = next((i for i in prog.instructions if i.type == "init"), None)
    riders = sum(1 for s in (init.placement.values() if init else ()) if s in loop_nodes)
    n_instr = 0
    ion_moves = 0          # ion-hops: riders x |delta| per rotation, participants per dock
    ions_per: list[int] = []
    for ins in prog.instructions:
        if ins.type != "simd":
            continue
        if ins.cls and ins.cls.startswith("rotate"):
            delta = abs(int((ins.template or {}).get("delta", 1)))
            n_instr += 1
            ion_moves += riders * delta
            ions_per.append(riders)
        elif ins.participants:
            n_instr += 1
            ion_moves += len(ins.participants)
            ions_per.append(len(ins.participants))
    plane = arch.control_plane
    return {
        "transport_instructions": n_instr,
        "ion_moves": ion_moves,
        "ions_per_instruction": (sum(ions_per) / len(ions_per)) if ions_per else 0.0,
        "max_ions_per_instruction": max(ions_per) if ions_per else 0,
        "shared_channels": plane.n_shared_channels,
        "switch_per_site": plane.switch_per_site,
        "switches": plane.n_switches,
    }


def score(arch, prog: TSIR, overrides: dict | None = None, table: str = "qccdsim_jones",
          objective: str = "fastest") -> dict:
    """(T_round, -ln F, p_eff, p_eff_90, the split, the clock's split, Lambda)."""
    o = dict(overrides or {})
    law = o.pop("idle_law", "linear")
    table = o.pop("table", table)
    objective = o.pop("objective", objective)
    arch = patched(arch, **o)
    model = corrected_model(table, objective=objective)
    eps: list[float] = []

    def hook(v):
        if getattr(v.instr, "type", "") != "gate":
            return
        for a, b in iter_pairs(v.instr):
            nbar = max(v.quanta.get(a, 0.0), v.quanta.get(b, 0.0))
            try:
                n_chain = v.occ_before.get(v.pos_before[a], 2)
                eps.append(model.gate_error(arch, nbar, n_chain))
            except Exception:
                eps.append(model.gate_error(arch, nbar))

    try:
        res = replay(prog, arch, model, check_rules=False, keep_cycles=False, on_cycle=hook)
    except TypeError:
        res = replay(prog, arch, model, check_rules=False, keep_cycles=False)
    prefix = "q" if any(i.startswith("q") for i in res.per_ion_quanta) else "d"
    m = t2_metrics(arch, res, model, data_ion_prefix=prefix)
    T = res.total_us * 1e-6
    n_ions = len(res.per_ion_quanta)
    tc = float(arch.species.get("T_coh_s") or 0) or float("inf")
    idle = n_ions * T / tc if law == "linear" else n_ions * (T / tc) ** 2
    nlf = m.gate_error_sum + idle + m.spam_error
    eps0 = 1.0 - float(arch.primitives.scalar("ms_gate")["fidelity_at_n0"])
    floor = m.n_gate_pairs * eps0 + m.spam_error
    locations = m.n_gate_pairs + res.n_measure + res.n_reset  # the circuit's fault locations
    s = sorted(eps)
    p90 = s[int(0.9 * (len(s) - 1))] if s else eps0
    p_eff = nlf / locations
    by_class = dict(res.us_by_class)
    by_type = dict(res.us_by_type)
    batches = res.hops_by_class.get("dock", 0)
    rot_hops = sum(v for k, v in res.hops_by_class.items() if k.startswith("rotate"))
    return {
        "broadcast": broadcast_use(arch, prog),
        "T_round_ms": res.total_us / 1000.0,
        "neg_log_fidelity": nlf, "gate_error": m.gate_error_sum, "idle_error": idle,
        "spam_error": m.spam_error, "floor": floor, "device_error": nlf - floor,
        "device_share": (nlf - floor) / nlf,
        "p_eff": p_eff, "p_eff_90": p90, "p_eff_vs_threshold": p_eff / THRESHOLD,
        "lambda_per_s": lambda_rate(p_eff, res.total_us),
        "locations": locations, "n_pairs": m.n_gate_pairs, "n_measure": res.n_measure,
        "n_reset": res.n_reset, "n_1q": res.n_1q_gates, "n_ions": n_ions,
        "batches": batches, "rotate_hops": rot_hops,
        "mean_gate_quanta": m.mean_gate_quanta,
        "chain_len_at_gate": {str(k): v for k, v in sorted(res.chain_len_at_gate.items())},
        "us_by_class": dict(by_class),
        "clock_ms": {
            "batches": (by_class.get("dock", 0) + by_class.get("undock", 0)
                        + by_type.get("cool", 0)) / 1000.0,
            "rotation": sum(v for k, v in by_class.items() if k.startswith("rotate")) / 1000.0,
            "gates": by_type.get("gate", 0) / 1000.0,
            "spam": (by_type.get("measure", 0) + by_type.get("reset", 0)) / 1000.0,
            "cooling": by_type.get("cool", 0) / 1000.0,
        },
        "overrides": {**o, "idle_law": law, "table": table, "objective": objective},
    }


# --------------------------------------------------------------------------- buildability


#: A well is formed by the control electrodes on both sides of the axis acting together,
#: and one lattice unit IS three of them -- `nm_per_unit_x` is derived as 3 x `dc_pitch`.
#: So a trap with fewer than three surviving pads has no well, however clean its metal is.
#: This is not a design rule; it is what the technology's own axial scale means.
PADS_PER_TRAP_FLOOR = 3.0


def buildability(arch_path: Path) -> dict:
    from qccd.cost.hardware import hardware_report
    from qccd.phys.build import build_layout
    from qccd.phys.drc import check
    from qccd.phys.tech import load_technology

    arch = load(str(arch_path))
    hw = hardware_report(arch)
    tech = load_technology("eth_junction_2201.12579")
    layout = build_layout(arch, tech)
    rep = check(layout, arch)
    x0, y0, x1, y1 = layout.bbox()
    # what the crossings left behind.  A DRC-clean device whose pads were ALL deleted by
    # keep-outs reports clean because there is no metal left to collide -- which is what a
    # 72-dock ring at height 2 does (0.00 pads per trap, `Codesign/findings/q06g`).
    d = next((x for x in rep.disclosures if x.topic == "electrodes per trap"), None)
    pads = float(d.counted) if d else float("nan")
    return {
        "dacs": hw.dacs, "junctions": hw.n_junctions, "over_budget": list(hw.over_budget),
        "drc_clean": bool(rep.clean), "drc": dict(rep.by_rule()),
        "pads_per_trap": round(pads, 2), "declared_per_trap": (d.declared if d else None),
        "has_wells": bool(pads >= PADS_PER_TRAP_FLOOR),
        "buildable": bool(rep.clean and pads >= PADS_PER_TRAP_FLOOR),
        "die_mm": [round((x1 - x0) * 1e-6, 3), round((y1 - y0) * 1e-6, 3)],
        "area_mm2": round((x1 - x0) * (y1 - y0) * 1e-12, 3),
    }


# --------------------------------------------------------------------------- one point


def point(out: Path, geom: dict, A: int, compiler: str = "bridge", *, steady: bool = True,
          placement: str = "structured", binding: str = "dynamic", phases: str = "xz",
          sweep: str = "monotone", lean: bool = False, arch_path: Path | None = None,
          tag: str | None = None, switches: bool = True, dock_all: bool = False,
          table: str = "qccdsim_jones", objective: str = "fastest",
          swap_roles: bool = False) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if not switches:
        # the same device with the per-site switch removed from its channel map
        arch_path = build_arch(out, geom if arch_path is None else {}, switches=False,
                               template=arch_path or TEMPLATE)
    arch_path = arch_path or build_arch(out, geom)
    name = arch_path.stem.replace(".arch", "")
    bridge = compiler == "bridge"
    tag = tag or f"{name}_a{A}_{compiler}" \
        + ("" if (placement == "anneal" or not bridge) else f"_{placement}") \
        + ("" if (binding == "dynamic" or not bridge) else f"_{binding}") \
        + ("" if (phases == "mixed" or not bridge) else f"_{phases}") \
        + ("" if sweep == "monotone" else f"_{sweep}") \
        + ("_dockall" if dock_all else "") + ("_swap" if swap_roles else "") \
        + ("" if (table == "qccdsim_jones" and objective == "fastest")
           else f"_{table.split('_')[0]}-{objective}") \
        + ("" if steady else "_firstround")
    row: dict = {"tag": tag, "arch": name, "geometry": geom, "ancillas": A, "compiler": compiler,
                 "placement": placement if compiler == "bridge" else "stride",
                 "binding": binding if compiler == "bridge" else "round-robin",
                 "phases": phases if compiler == "bridge" else "xz",
                 "sweep": sweep, "steady_state": steady,
                 "switch_per_site": switches, "dock_all": dock_all,
                 "table": table, "objective": objective, "swap_roles": swap_roles}
    if not positions_distinct(arch_path):
        # Two sites on one point.  The DRC cannot see it -- coincident metal is one
        # polygon, so a degenerate device reports CLEAN -- and the rules cannot either,
        # because they judge the graph.  A ring with a dock at every slot is exactly
        # this: every spur ends on the mid-line, so the dock of slot s and the dock of
        # slot n-1-s are the same point (q06f).
        row.update(status="degenerate", detail="two sites share a position")
        return row
    exp = out / f"{name}.expanded.json"
    if not exp.exists():
        sh([sys.executable, BRIDGE / "export_arch.py", arch_path, "-o", exp])

    qasm = out / f"{tag}.qasm"
    gen = [sys.executable, BRIDGE / "gen_bb144.py", "-o", qasm]
    if steady:
        gen.append("--steady-state")
    placement_json = None
    if compiler == "bridge":
        prefix = out / f"{tag}.sched"
        rc, log = sh([sys.executable, HERE / "bb_schedule.py", "--arch", arch_path,
                      "--ancillas", str(A), "--placement", placement, "--binding", binding,
                      "--phases", phases, "-o", prefix]
                     + (["--swap-roles"] if swap_roles else []))
        if rc:
            row.update(status="schedule failed", detail=log.strip().splitlines()[-1][:160])
            return row
        sched = json.loads(Path(str(prefix) + ".schedule.json").read_text(encoding="utf-8"))
        row["predicted"] = sched["stats"]
        placement_json = Path(str(prefix) + ".placement.json")
        gen += ["--schedule", str(prefix) + ".schedule.json"]
    else:
        gen += ["--ancillas", str(A)]
    rc, log = sh(gen)
    if rc:
        row.update(status="qasm failed", detail=log.strip().splitlines()[-1][:160])
        return row

    rc, log = sh([sys.executable, HERE / "esm_check.py", qasm])
    row["esm_check"] = "measures exactly the stabilizers" if rc == 0 else "FAILED"
    if rc:
        row.update(status="not a valid ESM round", detail=log.strip()[-300:])
        return row

    prefix = out / tag
    cmd = [EXE, "rotate", qasm, "--arch", exp, "-o", prefix]
    if placement_json:
        cmd += ["--placement", placement_json]
    # always explicit: qccdc's default became greedy on 2026-10-19, and a point labelled
    # monotone must not silently run the other rule
    cmd += ["--sweep", sweep]
    if dock_all:
        cmd += ["--dock-all"]
    rc, log = sh(cmd)
    if rc != 0 or not Path(str(prefix) + ".tsir.json").exists():
        row.update(status="did not compile", detail=log.strip().splitlines()[-1][:200])
        return row
    for line in log.splitlines():
        if "dock-all:" in line:
            mm = re.search(r"dock-all: (\d+) riders", line)
            if mm:
                row["extra_docks"] = int(mm.group(1))
        if "contacts in" in line:
            for key, pat in (("batches", r"(\d+)\s+batches"), ("hops", r"(\d+)\s+hops"),
                             ("rotations", r"(\d+)\s+rotations")):
                mm = re.search(pat, line)
                if mm:
                    row["compiled_" + key] = int(mm.group(1))
    row["cert_selfcheck"] = "OK" if "self-check OK" in log else "FAILED"

    cooled = out / f"{tag}.cooled.tsir.json"
    op = ["--table", table, "--objective", objective]
    _, clog = sh([sys.executable, BRIDGE / "insert_cooling.py", str(prefix) + ".tsir.json",
                  "--arch", arch_path, "-o", cooled] + op)
    mm = re.search(r"(\d+) global cooling operations", clog)
    if mm:
        row["cools"] = int(mm.group(1))
    rc, rlog = sh([sys.executable, BRIDGE / "check_tsir.py", cooled, "--arch", arch_path,
                   "--model", "corrected"] + op)
    row["rules"] = next((l.strip() for l in rlog.splitlines() if "rules passed" in l), "?")
    row["rules_ok"] = "RULES FAILED" not in rlog and rc == 0

    arch = load(str(arch_path))
    row.update(score(arch, TSIR.load(str(cooled)), table=table, objective=objective))
    row["build"] = buildability(arch_path)
    row["files"] = {"arch": str(arch_path), "qasm": str(qasm), "cooled": str(cooled),
                    "prefix": str(prefix), "expanded": str(exp)}
    row["status"] = "ok" if row["rules_ok"] else "RULES FAILED"

    if lean and row["status"] == "ok":
        row["r10"] = r10(row)
    row["seconds"] = round(time.time() - t0, 1)
    return row


def r10(row: dict) -> dict:
    """R10 by the proved Lean checker (O1) and the stabilizer tableau (O2)."""
    f = row["files"]
    qc = Path(f["prefix"] + ".qcheck.json")
    t0 = time.time()
    sh([sys.executable, BRIDGE / "mk_qcheck_input.py", f["prefix"], "--arch", f["expanded"],
        "-o", qc])
    rc, log = sh([sys.executable, BRIDGE / "check_cert.py", f["prefix"], "--qasm", f["qasm"],
                  "--arch", f["arch"], "--qcheck", qc])
    verdict = next((l.strip() for l in log.splitlines() if "R10" in l and "->" in l), "")
    return {"passed": rc == 0 and "R10 passed" in log, "verdict": verdict[:200],
            "seconds": round(time.time() - t0, 1)}


# --------------------------------------------------------------------------- experiments


def exp_E1(out: Path, lean: bool) -> list[dict]:
    """The compilation axis on the FIXED shipped device, first pass: the shipped Python
    pipeline's policy (annealed placement, X and Z packed together) against stride."""
    g = {"width": 72, "height": 2, "verticals": 24, "dock_offset": 0, "slots": 144}
    ship = TEMPLATE
    rows = [
        point(out, g, 24, "stride", steady=False, arch_path=ship, lean=lean),  # q05's baseline
        point(out, g, 24, "stride", arch_path=ship, lean=lean),
        point(out, g, 24, "stride", sweep="greedy", arch_path=ship),
        point(out, g, 24, "bridge", placement="identity", binding="fixed", phases="mixed", arch_path=ship),
        point(out, g, 24, "bridge", placement="identity", phases="mixed", arch_path=ship),
        point(out, g, 24, "bridge", placement="interleaved", binding="fixed", phases="mixed", arch_path=ship),
        point(out, g, 24, "bridge", placement="interleaved", phases="mixed", arch_path=ship),
        point(out, g, 24, "bridge", placement="anneal", phases="mixed", arch_path=ship, lean=lean),
        point(out, g, 24, "bridge", placement="anneal", phases="mixed", sweep="greedy", arch_path=ship),
    ]
    for A in (6, 8, 12, 16, 18):
        rows.append(point(out, g, A, "bridge", placement="anneal", phases="mixed", arch_path=ship))
        rows.append(point(out, g, A, "stride", arch_path=ship))
    return rows


RINGS_JSON = ROOT / "Codesign" / "data" / "q06_rings.json"
#: the wide search of `q06g_ringsearch.py`, taken in preference when it is present: it
#: covers heights 2-6 and up to 448 slots against `rings_table`'s 216, and it was taken
#: with the corrected pad keep-out
WIDE_RINGS_JSON = ROOT / "Codesign" / "data" / "q06g_rings.json"


def rings_table(heights=(2, 3, 4), docks=(12, 16, 18, 24, 36, 48, 72, 144)) -> dict:
    """Every (height, docks) -> the DRC-preferred ring, cached to q06_rings.json."""
    table = json.loads(RINGS_JSON.read_text(encoding="utf-8")) if RINGS_JSON.exists() else {}
    for H in heights:
        for V in docks:
            key = f"h{H}_v{V}"
            if key in table:
                continue
            g = find_clean_ring(V, H)
            if g:
                table[key] = g
                print(f"  {key}: W={g['width']} slots={g['slots']} offset={g['dock_offset']} "
                      f"DRC {g['drc']} {'CLEAN' if g['clean'] else ''} area {g['area_mm2']} mm2")
            RINGS_JSON.parent.mkdir(parents=True, exist_ok=True)
            RINGS_JSON.write_text(json.dumps(table, indent=1), encoding="utf-8")
    return table


def exp_E1b(out: Path, lean: bool) -> list[dict]:
    """The compilation axis, final policy: structured placement, X-then-Z phases."""
    g = {"width": 72, "height": 2, "verticals": 24, "dock_offset": 0, "slots": 144}
    ship = TEMPLATE
    rows = [point(out, g, 24, "bridge", arch_path=ship, lean=lean),
            point(out, g, 24, "bridge", placement="interleaved", arch_path=ship),
            point(out, g, 24, "bridge", placement="identity", arch_path=ship),
            point(out, g, 24, "bridge", placement="interleaved", binding="fixed", arch_path=ship),
            point(out, g, 24, "bridge", sweep="greedy", arch_path=ship)]
    for A in (6, 8, 12, 16, 18):
        rows.append(point(out, g, A, "bridge", arch_path=ship))
    return rows


def exp_E3(out: Path, lean: bool) -> list[dict]:
    """The architecture axis over the WIDE, corrected ring search (`q06g`).

    Every buildable (height, docks) the wide search found, at its own dock count as the
    ancilla count and at 24, on the final compilation policy: switchless with dock-all,
    and both operating points so the technology assumption is visible in every row.
    """
    table = json.loads(WIDE_RINGS_JSON.read_text(encoding="utf-8"))
    rows = []
    for key, g in sorted(table.items()):
        if not g:
            continue
        geom = {k: g[k] for k in ("width", "height", "verticals", "dock_offset", "slots")}
        for A in sorted({min(24, g["verticals"]), g["verticals"]}):
            for table_name, obj in (("qccdsim_jones", "fastest"),
                                    ("transport_excitation", "fastest")):
                rows.append(point(out, geom, A, "bridge", switches=False, dock_all=True,
                                  table=table_name, objective=obj,
                                  lean=lean and A == g["verticals"] and g.get("clean")))
    return rows


def buildable_only(rows: list[dict]) -> list[dict]:
    """Rows on a device that is DRC-clean AND still has wells to trap in."""
    return [r for r in rows
            if r.get("status") == "ok" and r.get("build", {}).get("buildable")]


def exp_E2(out: Path, lean: bool, heights=(2, 3), docks=(12, 16, 18, 24, 36, 48, 72, 144)) -> list[dict]:
    """The architecture axis with the good compiler: docks x ancillas x height, every
    ring the DRC-preferred one for its (height, docks)."""
    table = rings_table(heights, docks)
    rows = []
    for H in heights:
        for V in docks:
            g = table.get(f"h{H}_v{V}")
            if not g:
                continue
            g = {k: g[k] for k in ("width", "height", "verticals", "dock_offset", "slots")}
            for A in sorted({min(24, V), V}):
                rows.append(point(out, g, A, "bridge", lean=lean and A == V))
            if V >= 24:
                rows.append(point(out, g, min(24, V), "stride"))
    return rows


def exp_robust(rows: list[dict]) -> list[dict]:
    """Re-score every ok row under the model's uncertain parameters.  One replay each."""
    grid = [
        {"T_coh_s": 600}, {"T_coh_s": 100}, {"T_coh_s": 30}, {"T_coh_s": 10},
        {"T_coh_s": 3}, {"T_coh_s": 1},
        {"idle_law": "quadratic"}, {"reset_error": 1e-3}, {"reset_error": 2e-2},
        {"anomalous_rate": 0.5}, {"anomalous_rate": 5.0},
    ]
    out = []
    for r in rows:
        if r.get("status") != "ok":
            continue
        arch = load(r["files"]["arch"])
        prog = TSIR.load(r["files"]["cooled"])
        for ov in grid:
            s = score(arch, prog, ov)
            out.append({"tag": r["tag"], "arch": r["arch"], "ancillas": r["ancillas"],
                        "compiler": r["compiler"], "override": ov,
                        **{k: s[k] for k in ("T_round_ms", "neg_log_fidelity", "idle_error",
                                              "spam_error", "gate_error", "p_eff",
                                              "lambda_per_s", "device_share")}})
    return out


# --------------------------------------------------------------------------- reporting


def table(rows: list[dict]) -> str:
    L = [f"  {'tag':44s}{'A':>4}{'T_round':>10}{'batches':>8}{'hops':>6}{'-lnF':>9}"
         f"{'p_eff':>11}{'dev%':>6}{'build':>8}{'mm2':>7}  rules  R10"]
    for r in rows:
        if r.get("status") != "ok":
            L.append(f"  {r['tag']:44s}     {r.get('status')}: {r.get('detail', '')[:80]}")
            continue
        b = r["build"]
        r10s = r.get("r10", {}).get("passed")
        verdict = ("build" if b.get("buildable") else
                   "no well" if b.get("drc_clean") else sum(b["drc"].values()))
        L.append(f"  {r['tag']:44s}{r['ancillas']:>4}{r['T_round_ms']:>8.2f}ms"
                 f"{r['batches']:>8}{r['rotate_hops']:>6}{r['neg_log_fidelity']:>9.4f}"
                 f"{r['p_eff']:>11.4e}{100 * r['device_share']:>5.1f}%"
                 f"{str(verdict):>8}{b['area_mm2']:>7.1f}"
                 f"  {'ok' if r['rules_ok'] else 'FAIL'}   "
                 f"{'passed' if r10s else ('-' if r10s is None else 'FAILED')}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--exp", choices=["E1", "E1b", "E2", "E3"], default=None)
    ap.add_argument("--point", default=None,
                    help='e.g. "H=3,V=24,A=24,compiler=bridge,placement=anneal,sweep=monotone"')
    ap.add_argument("--robust", default=None, help="a q06 JSON to re-score under the grid")
    ap.add_argument("--rings", action="store_true",
                    help="find the DRC-preferred ring for every (height, docks) and stop")
    ap.add_argument("--lean", action="store_true", help="R10 by the Lean checker (~1-5 min/point)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--json", default=None)
    ap.add_argument("--ledger", default=str(ROOT / "Codesign" / "ledger.jsonl"))
    a = ap.parse_args(argv)
    out = Path(a.out) if a.out else ROOT / "Compiler" / "build" / "q06"
    out.mkdir(parents=True, exist_ok=True)
    if not EXE.exists():
        print(f"missing {EXE}; run `cd Compiler/ocaml && dune build`")
        return 2

    rows: list[dict] = []
    if a.rings:
        rings_table()
        return 0
    if a.robust:
        base = json.loads(Path(a.robust).read_text(encoding="utf-8"))
        rows = exp_robust(base["rows"] if isinstance(base, dict) else base)
        for r in rows:
            print(f"  {r['tag']:44s} {json.dumps(r['override']):28s} T {r['T_round_ms']:8.2f} ms"
                  f"  -lnF {r['neg_log_fidelity']:.4f}  p_eff {r['p_eff']:.4e}"
                  f"  lambda {r['lambda_per_s']:.3e}/s  dev {100 * r['device_share']:.1f}%")
    elif a.exp == "E1":
        rows = exp_E1(out, a.lean)
    elif a.exp == "E1b":
        rows = exp_E1b(out, a.lean)
    elif a.exp == "E3":
        rows = exp_E3(out, a.lean)
    elif a.exp == "E2":
        rows = exp_E2(out, a.lean)
    elif a.point:
        kv = dict(x.split("=") for x in a.point.split(","))
        H, V, A = int(kv.get("H", 2)), int(kv.get("V", 24)), int(kv.get("A", 24))
        W = int(kv["W"]) if "W" in kv else None
        g = ring_geometry(V, H, W=W)
        if g is None:
            g = {"width": W or (72 if H == 2 else 71), "height": H, "verticals": V,
                 "dock_offset": int(kv.get("offset", 0)), "slots": 2 * (W or 72) + 2 * H - 4}
        if "offset" in kv:
            g["dock_offset"] = int(kv["offset"])
        arch_path = (TEMPLATE if kv.get("arch") == "shipped"
                     else Path(kv["arch"]) if kv.get("arch") else None)
        rows = [point(out, g, A, kv.get("compiler", "bridge"), placement=kv.get("placement", "structured"),
                      binding=kv.get("binding", "dynamic"), phases=kv.get("phases", "xz"),
                      sweep=kv.get("sweep", "monotone"),
                      steady=kv.get("steady", "1") != "0", lean=a.lean, arch_path=arch_path,
                      switches=kv.get("switches", "1") != "0", dock_all=kv.get("dock_all", "0") == "1",
                      table=kv.get("table", "qccdsim_jones"), objective=kv.get("objective", "fastest"),
                      swap_roles=kv.get("swap_roles", "0") == "1")]
    else:
        ap.print_help()
        return 2

    if not a.robust:
        print(table(rows))
        with open(a.ledger, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r) + "\n")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps({"rows": rows}, indent=1), encoding="utf-8")
        print(f"-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
