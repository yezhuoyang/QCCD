"""q07: does a machine built in layers beat a planar one?

A layered machine (docs/PLAN-3d.md) is planar traps one above another, joined by risers.  A
riser is an ordinary shuttle, so a layered machine differs from a planar one in two ways
only:

* its rail graph need not be planar, and a junction may join a rail up and a rail down;
* each layer has a broadcast of its own, so one cycle may carry one waveform A LAYER where
  a planar broadcast machine carries one.

Three experiments, each on a yardstick that already exists in this repository:

    python Codesign/scripts/q07_layers.py run qccdc    # E1: the site's compiler, unchanged, on stacked lattices
    python Codesign/scripts/q07_layers.py run boards   # E2: the conveyor sandwich on the Architecture boards,
                                                       #     in 1, 2 and 3 layers, by each board's own grader
    python Codesign/scripts/q07_layers.py run goal3    # E3: the same machine on Goal 3's codes, scored as its sweep scores
                                                       #     (--ler surface3 surface5 bb72 also samples those LERs)
    python Codesign/scripts/q07_layers.py table
    python Codesign/scripts/q07_layers.py page --only bb144_three_layers   # a design page, with the 3-D stage

A design that breaks a rule is reported as broken, never ranked.  Work files go under
`out/q07/` (git-ignored); the rows are `Codesign/data/q07.json`; what they say is
`Codesign/findings/q07-layers.md`.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from qccd import Machine                                    # noqa: E402
from qccd.cost.models import corrected_model                # noqa: E402
from qccd.ir.tsir import TSIR                               # noqa: E402
from qccd.ruleset.compile import compile_under              # noqa: E402

QCCDC = ROOT / "Compiler" / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"
WORK = ROOT / "out" / "q07"
DATA = ROOT / "Codesign" / "data" / "q07.json"

#: the boards' circuits (tasks/<board>/task.json names the same files)
CIRCUITS = {
    "steane": ROOT / "Compiler" / "examples" / "steane_esm.qasm",
    "rep9": ROOT / "Compiler" / "examples" / "rep9_esm.qasm",
    "five_qubit": ROOT / "SmallCode" / "five_qubit" / "five_qubit_esm.qasm",
    "surface17": ROOT / "Compiler" / "examples" / "surface17_esm.qasm",
    "bb144": ROOT / "tasks" / "bb144" / "circuit.qasm",
}


def score(machine: Machine, circuit: str, tag: str, *, timeout: float = 3600,
          post=None) -> dict:
    """Compile `circuit` onto `machine`, cool, judge, replay.  One row of the table."""
    qasm = CIRCUITS[circuit]
    work = WORK / circuit / tag
    work.mkdir(parents=True, exist_ok=True)
    arch = work / "machine.arch.json"
    machine.save(arch, expanded=True)
    d = machine.arch.device
    row = {"circuit": circuit, "design": tag, "layers": len(d.layers), "risers": len(d.risers),
           "nodes": len(d.nodes), "traps": len(d.sites()), "capacity": d.total_capacity(),
           "junctions": len(d.junction_nodes), "dacs": machine.resources().dacs,
           "geometry": sorted({v.rule for v in machine.violations()})}
    t0 = time.time()
    try:
        out = compile_under(qasm, arch, "standard", work, qccdc=QCCDC, semantics=False,
                            timeout=timeout)
    except Exception as exc:                       # a timeout, or a compiler that died
        row.update(status="error", why=str(exc)[-200:], compile_s=round(time.time() - t0, 1))
        return row
    row["compile_s"] = round(time.time() - t0, 1)
    if not out["compiled"]:
        row.update(status="refused", why=" ".join(out["log"].split())[-220:])
        return row
    prog = TSIR.load(out["programme"])
    if post is not None:
        prog = post(prog, machine)
    r = machine.run(prog, model=corrected_model("qccdsim_jones"))
    rt = machine.run(prog, model=corrected_model("transport_excitation"))
    simd = [i for i in prog.instructions if i.type == "simd"]
    us = r.report.result.us_by_class
    transport_us = sum(v for k, v in us.items() if k not in ("gate", "cool", "measure", "reset"))
    failing = sorted({x.id for x in out["report"].results if x.verdict == "fails"}
                     | set(r.rules_failed))
    moved = sum(len({p.ion for p in (i.participants or ())}) for i in simd)
    used = sorted({d.layer_of(x) for i in simd for p in (i.participants or ())
                   for x in (p.src, p.dst)})
    riser_hops = sum(1 for i in simd for p in (i.participants or ()) for s in (p.via or ())
                     if s in d.segments and d.is_riser(s))
    row.update(status="ok" if not failing else "breaks " + ",".join(failing),
               T_jones=round(r.runtime_ms, 3), T_transport=round(rt.runtime_ms, 3),
               transport_cycles=len(simd), transport_ms=round(transport_us / 1000, 3),
               cools=sum(1 for i in prog.instructions if i.type == "cool"),
               instructions=len(prog.instructions),
               ions_per_cycle=round(moved / len(simd), 2) if simd else 0.0,
               layers_used=used, riser_hops=riser_hops, peak_quanta=round(r.peak_quanta, 2))
    return row


# ------------------------------------------------------------------------------ designs

def grid(a: int, b: int, **kw) -> Machine:
    return Machine.grid(a, b, name=f"grid{a}x{b}", template="grid9x9", **kw)


def stack_of(layers: list[Machine], risers: list[tuple[str, str]], name: str) -> Machine:
    return Machine.layered(layers, risers=risers, name=name)


def stacked_grids(a: int, b: int, n: int, every: int = 1) -> Machine:
    """`n` copies of grid(a, b), a riser at every `every`-th junction between neighbours."""
    names = [chr(ord("A") + k) for k in range(n)]
    risers = []
    for k in range(n - 1):
        for i in range(0, a, every):
            for j in range(0, b, every):
                risers.append((f"{names[k]}.J{i}_{j}", f"{names[k + 1]}.J{i}_{j}"))
    return stack_of([grid(a, b) for _ in range(n)], risers, f"grid{a}x{b}x{n}")


def save(rows: list[dict]) -> None:
    old = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
    key = lambda r: (r.get("study", ""), r["circuit"], r["design"])
    merged = {key(r): r for r in old}
    merged.update({key(r): r for r in rows})
    DATA.parent.mkdir(parents=True, exist_ok=True)
    DATA.write_text(json.dumps(sorted(merged.values(), key=key), indent=1) + "\n",
                    encoding="utf-8", newline="\n")


# ------------------------------------------------------------------------------ the board's grader

def grade_on_board(board: str, machine: Machine, n_data: int, tag: str, *,
                   per_layer: bool | None = None, profile: str = "reference",
                   data_at: dict | None = None) -> dict:
    """The conveyor router's programme for `board`'s circuit on `machine`, graded by the
    board's own grader (`qccd.workspace.evaluator.grade`): the release's physics, its budget,
    its rule set, the certificate checks and its metrics.  Returns the grader's report.

    The bundle is what any entry's is -- device, programme, certified programme,
    certificate -- so every stage reads this entry as it reads one compiled by `qccdc`.
    """
    import shutil
    from qccd.arch.device import Architecture
    from qccd.compile.conveyor_router import COMPILER_NAME, compile_conveyor
    from qccd.compile.cooling import insert_cooling
    from qccd.workspace.bundle import write_bundle
    from qccd.workspace.evaluator import grade
    from qccd.workspace.tasks import board_device, find_release

    rel = find_release(board)
    doc = board_device(machine.arch.to_json(expanded=True), rel)
    arch = Architecture.from_json(doc)
    t0 = time.time()
    prog, cert = compile_conveyor(rel.circuit_text(), arch, n_data, name=rel.id.split("@")[0],
                                  per_layer=per_layer, data_at=data_at)
    table = rel.manifest.get("rank_table", "qccdsim_jones")
    cooled = insert_cooling(prog, arch, corrected_model(table)).program
    cooled.meta.update(prog.meta)
    seconds = round(time.time() - t0, 2)
    work = WORK / "graded" / board.replace("@", "_") / tag
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    write_bundle(work / "bundle", task_id=rel.id, task_digest=rel.digest, device=doc,
                 program=cooled.to_json(), certified_program=prog.to_json(), certificate=cert,
                 provenance={"compiler": COMPILER_NAME, "study": "Codesign q07"})
    report = grade(rel, work / "bundle", profile, workdir=work / "grade")
    report["_compile_seconds"] = seconds
    simd = [i for i in cooled.instructions if i.type == "simd"]
    report["_programme"] = {"placement": prog.meta.get("placement"), "wait_steps": prog.meta.get("wait_steps"),
                            "transport_cycles": len(simd),
                            "rotate_cycles": sum((i.meta or {}).get("kind") == "rotate" for i in simd),
                            "cools": sum(i.type == "cool" for i in cooled.instructions),
                            "instructions": len(cooled.instructions)}
    (work / "report.json").write_text(json.dumps(report, indent=1, default=str), encoding="utf-8")
    return report


def brief(report: dict) -> dict:
    """The lines of a grader's report this study quotes."""
    stages = {s["id"]: s["status"] for s in report.get("stages", [])} if isinstance(
        report.get("stages"), list) else {k: v.get("status") for k, v in (report.get("stages") or {}).items()}
    m = report.get("metrics") or {}
    return {"eligible": report.get("eligible"), "status": report.get("status"), "stages": stages,
            "T_jones": m.get("T_jones"), "T_transport": m.get("T_transport"), "dacs": m.get("dacs"),
            "ions_per_instruction": m.get("ions_per_instruction"),
            "conformance": report.get("conformance"), "compile_s": report.get("_compile_seconds"),
            **(report.get("_programme") or {})}


# ------------------------------------------------------------------------------ Goal 3's yardstick

GOAL3 = ROOT / "goals" / "goal3"
GOAL3_CODES = ("surface3", "surface5", "surface7", "color3", "color5", "color7", "hgp58", "bb72")


def goal3_best(code: str) -> dict:
    """Goal 3's fastest rule-clean point of each family for `code` (`results/<code>.jsonl`)."""
    best: dict[str, dict] = {}
    f = GOAL3 / "results" / f"{code}.jsonl"
    for ln in f.read_text(encoding="utf-8").splitlines() if f.exists() else []:
        if not ln.strip():
            continue
        r = json.loads(ln)
        if r.get("status") == "ok" and r.get("T_round_ms") is not None:
            if r["family"] not in best or r["T_round_ms"] < best[r["family"]]["T_round_ms"]:
                best[r["family"]] = {"point": r["point"], "T_round_ms": round(r["T_round_ms"], 3),
                                     "ler_per_round": (r.get("ler") or {}).get("per_round")}
    return best


def goal3_point(code: str, *, layers: int = 3, per_trap: int = 1, cooling: str = "helios",
                ler: bool = False, align: str = "best", wait: float | None = None,
                lattice: bool = True, visitors: int = 1) -> dict:
    """One sandwich on one of Goal 3's codes, scored as `goals/goal3/sweep.py` scores a point:
    the code's experiment from its release (Z memory then X memory), the Helios nominal
    physics (qccd-physics@3), the cooling pass on the `helios` table, a rule set judged as the
    grader judges an entry's, and the replayed time over the rounds run.

    `per_trap` is how many data qubits stand in one trap, and `visitors` how many ancillas
    it has room for besides.  `lattice`: a surface code's data are laid row by row with a
    spare trap after each row (`conveyor_router.lattice_rows`), which its numbering alone
    does not say.  `cooling="helios"` keeps the
    model's 3 ms ground-state cooling, what C2LR and WISE pay; `"free"` is the Cyclone
    family's reading (deck S10: sympathetic cooling in no time), and nothing else of
    Cyclone's prices.  The rule set is the site's `standard`.
    """
    import math
    for q in (str(GOAL3),):
        if q not in sys.path:
            sys.path.insert(0, q)
    import sweep as G                                       # goals/goal3/sweep.py
    from qccd.arch import load
    from qccd.arch.sandwich import conveyor_sandwich
    from qccd.compile import conveyor_router
    from qccd.compile.c2lr_router import Refused
    from qccd.ruleset import FAILS, HOLDS, check, legacy_config
    from qccd.ruleset import load as load_rules
    from qccd.ruleset.compile import circuit_of_certificate
    from qccd.ruleset.programme import programme_execution

    rel = G.release(code)
    exp = G.load_exp(rel)
    positions, data_at = math.ceil(exp.n_data / per_trap), None
    side = math.isqrt(exp.n_data)
    if lattice and code.startswith("surface") and per_trap == 1 and side * side == exp.n_data:
        positions, data_at = conveyor_router.lattice_rows(side, side)
    machine = conveyor_sandwich(positions, layers=layers, capacity=per_trap + visitors)
    tag = f"{machine.arch.name}_p{per_trap}_{cooling}" + (f"_v{visitors}" if visitors != 1 else "")
    row = {"code": code, "release": rel.id, "physics": "qccd-physics@3", "family": "Sandwich",
           "point": tag, "layers": layers, "per_trap": per_trap, "visitors": visitors,
           "cooling": cooling, "lattice": data_at is not None,
           "compiler": conveyor_router.COMPILER_NAME, "rule_set": "standard",
           "ions": exp.n_data + exp.n_ancilla}
    work = WORK / "goal3" / code / tag
    work.mkdir(parents=True, exist_ok=True)
    phys = G.physics()
    prims = {k: dict(v) for k, v in phys["primitives"].items()}
    if cooling == "free":
        prims["cool"] = {**prims["cool"], "us": 0,
                         "note": "sympathetic cooling in no time, as Goal 3's Cyclone family reads deck S10"}
    elif cooling != "helios":
        raise ValueError(cooling)
    doc = machine.arch.to_json(expanded=True)
    doc.update(primitives=prims, heating=phys["heating"], species=phys["species"], budget=phys["budget"])
    arch_p = work / "device.arch.json"
    arch_p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    t0 = time.time()
    try:
        cooled, cert_p = conveyor_router.compile_with_router(exp.qasm, arch_p, work, n_data=exp.n_data,
                                                             table=G.TABLE, name=code, align=align,
                                                             wait=wait, data_at=data_at)
    except Refused as exc:
        return {**row, "status": "refused", "why": str(exc)[:300]}
    row["compile_s"] = round(time.time() - t0, 2)
    arch = load(str(arch_p))
    d = arch.device
    prog = TSIR.load(str(cooled))
    cert = json.loads(cert_p.read_text(encoding="utf-8"))
    rs = load_rules("standard")
    exe = programme_execution(prog, arch, corrected_model(G.TABLE), circuit=circuit_of_certificate(cert),
                              rule_config=legacy_config(rs) or None, check_metrics=False)
    rep = check(rs, exe)
    T = max(exe.instant_time) / 1000.0
    failed = sorted(x.id for x in rep.results if x.verdict == FAILS)
    simd = [i for i in prog.instructions if i.type == "simd"]
    row.update(status="ok" if not failed else "breaks " + ",".join(failed),
               rules_skipped=sorted(x.id for x in rep.results if x.verdict not in (HOLDS, FAILS)),
               placement=prog.meta.get("placement"), wait_steps=prog.meta.get("wait_steps"),
               T_ms=round(T, 3), rounds=G.rounds_run(exp), T_round_ms=round(T / G.rounds_run(exp), 3),
               transport_cycles=len(simd),
               rotate_cycles=sum((i.meta or {}).get("kind") == "rotate" for i in simd),
               cools=sum(i.type == "cool" for i in prog.instructions),
               instructions=len(prog.instructions), nodes=len(d.nodes),
               junctions=len(d.junction_nodes), dacs=machine.resources().dacs)
    del exe, rep
    if ler and not failed:
        from qccd.qec import evaluate_memory
        budget = dict((rel.manifest["qec"].get("ler")) or {})
        lr = evaluate_memory(str(cooled), str(arch_p), exp, noise_id=G.NOISE, cert=cert,
                             ler_budget={k: v for k, v in budget.items() if k != "seed"},
                             seed=budget.get("seed"))
        chk = lr.get("check") or {}
        row["ler_check"] = bool(chk.get("ok"))
        if chk.get("ok"):
            row["ler_per_round"] = (lr.get("ler") or {}).get("per_round")
            row["ler"] = lr["ler"]
        else:
            row["ler_why"] = str(chk.get("reason") or chk)[:300]
    return row


# ------------------------------------------------------------------------------ the three experiments

def leader(board: str) -> dict | None:
    """The board's fastest rule-clean row in this repository (`SmallCode/*/rows.json`,
    `BBResults/studio/manifest.json`): what a new design has to beat."""
    where = {"bb144": ROOT / "BBResults" / "studio" / "manifest.json",
             "steane": ROOT / "SmallCode" / "steane" / "rows.json",
             "rep9": ROOT / "SmallCode" / "repetition" / "rows.json",
             "five_qubit": ROOT / "SmallCode" / "five_qubit" / "rows.json",
             "surface17": ROOT / "SmallCode" / "surface17" / "rows.json"}.get(board)
    if where is None or not where.exists():
        return None
    ok = [r for r in json.loads(where.read_text(encoding="utf-8"))
          if r.get("status") == "ok" and (r.get("numbers") or {}).get("T_jones") is not None]
    if not ok:
        return None
    best = min(ok, key=lambda r: r["numbers"]["T_jones"])
    return {"design": best["key"], "T_jones": best["numbers"]["T_jones"],
            "source": str(where.relative_to(ROOT)).replace("\\", "/")}


def run_qccdc() -> list[dict]:
    """E1.  The site's compiler, unchanged, on planar lattices and on lattices stacked."""
    designs = [("grid5x5", grid(5, 5)), ("grid4x3", grid(4, 3)), ("grid3x3", grid(3, 3)),
               ("grid2x2x4", stacked_grids(2, 2, 4)), ("grid3x2x2", stacked_grids(3, 2, 2)),
               ("grid3x3x2", stacked_grids(3, 3, 2))]
    rows = []
    for tag, machine in designs:
        row = {"study": "qccdc", **score(machine, "steane", tag)}
        rows.append(row)
        save([row])                               # a row is kept as soon as it exists
        print(f"  {tag:12s} {row['status']:10s} T_jones {row.get('T_jones')} cycles {row.get('transport_cycles')}",
              flush=True)
    return rows


#: the Architecture boards: release, data qubits, (rows, cols) of a surface code's lattice
BOARDS = {"bb144": ("bb144@2", 144, None), "steane": ("steane@1", 7, None), "rep9": ("rep9@1", 9, None),
          "five_qubit": ("five_qubit@1", 5, None), "surface17": ("surface17@1", 9, (3, 3))}


def run_boards(only: list[str] | None = None) -> list[dict]:
    """E2.  The conveyor sandwich in 1, 2 and 3 layers, by each board's own grader."""
    from qccd.arch.sandwich import conveyor_sandwich
    from qccd.compile.conveyor_router import lattice_rows
    rows = []
    for board, (release, n_data, lattice) in BOARDS.items():
        if only and board not in only:
            continue
        per_trap = 2 if board == "bb144" else 1
        positions, data_at = -(-n_data // per_trap), None
        if lattice:
            positions, data_at = lattice_rows(*lattice)
        top = leader(board)
        for visitors in ((1, 2) if board == "bb144" else (1,)):
            for layers in (3, 2, 1):
                machine = conveyor_sandwich(positions, layers=layers, capacity=per_trap + visitors)
                tag = machine.arch.name + (f"_v{visitors}" if visitors != 1 else "")
                b = brief(grade_on_board(release, machine, n_data, tag, data_at=data_at))
                d = machine.arch.device
                not_passed = {k: v for k, v in b["stages"].items() if v != "passed"}
                value = lambda m: (b.get(m) or {}).get("value")
                row = {"study": "board", "circuit": release, "design": tag, "layers": layers,
                       "per_trap": per_trap, "visitors": visitors, "nodes": len(d.nodes),
                       "junctions": len(d.junction_nodes), "dacs": value("dacs"),
                       "status": "ok" if set(not_passed) <= {"lean_certificate"} else "not passed: "
                       + ", ".join(sorted(not_passed)),
                       "stages_not_passed": not_passed, "T_jones": value("T_jones"),
                       "T_transport": value("T_transport"), "transport_cycles": b.get("transport_cycles"),
                       "rotate_cycles": b.get("rotate_cycles"), "cools": b.get("cools"),
                       "placement": b.get("placement"), "compile_s": b.get("compile_s"),
                       "compiler": "conveyor-router", "leader": top}
                rows.append(row)
                save([row])
                print(f"  {release:14s} {tag:20s} {row['status']:4s} T_jones {row['T_jones']} "
                      f"(leader {top and top['T_jones']}) cycles {row['transport_cycles']} dacs {row['dacs']}",
                      flush=True)
    return rows


def run_goal3(only: list[str] | None = None, *, ler: tuple[str, ...] = ()) -> list[dict]:
    """E3.  The sandwich on Goal 3's codes, in 1, 2 and 3 layers, under both readings of
    cooling.  `ler` names the codes whose three-layer point (3 ms cooling, one data ion a
    trap) is also sampled for its LER."""
    rows = []
    for code in GOAL3_CODES:
        if only and code not in only:
            continue
        best = goal3_best(code)
        for per_trap in ((1, 2) if code == "bb72" else (1,)):
            for cooling in ("helios", "free"):
                for layers in (3, 2, 1):
                    row = goal3_point(code, layers=layers, per_trap=per_trap, cooling=cooling,
                                      ler=(code in ler and layers == 3 and cooling == "helios"
                                           and per_trap == 1))
                    row.update(study="goal3", circuit=code, design=row["point"], goal3_best=best)
                    rows.append(row)
                    save([row])
                    print(f"  {code:9s} {row['point']:28s} {row['status']:4s} ms/round {row.get('T_round_ms')} "
                          f"cools {row.get('cools')} ler/round {row.get('ler_per_round')}", flush=True)
    return rows


def page(key: str = "bb144_three_layers", out: Path | None = None) -> Path:
    """One of the website's layered pages (`qccd.site.layers.SHOWS`), written under `out/q07/pages/`:
    a board's circuit on the sandwich as a design page, with the 3-D stage."""
    from qccd.site import layers as site_layers
    out = out or WORK / "pages" / f"{key}.html"
    site_layers.render(key, out)
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("what", choices=["run", "table", "page"])
    ap.add_argument("which", nargs="?", choices=["qccdc", "boards", "goal3"])
    ap.add_argument("--only", nargs="*", default=None, help="boards or codes to run")
    ap.add_argument("--ler", nargs="*", default=[], help="goal3: codes whose three-layer point is sampled")
    a = ap.parse_args()
    if a.what == "page":
        for key in (a.only or ["bb144_three_layers"]):
            print(page(key))
    elif a.what == "run":
        if a.which is None:
            ap.error("run what: qccdc, boards or goal3")
        rows = (run_qccdc() if a.which == "qccdc" else run_boards(a.only) if a.which == "boards"
                else run_goal3(a.only, ler=tuple(a.ler)))
        save(rows)
        print(f"{len(rows)} rows -> {DATA.relative_to(ROOT)}")
    else:
        rows = json.loads(DATA.read_text(encoding="utf-8")) if DATA.exists() else []
        for study in ("qccdc", "board", "goal3"):
            for c in sorted({r["circuit"] for r in rows if r.get("study") == study}):
                mine = [r for r in rows if r.get("study") == study and r["circuit"] == c]
                print(f"\n== {study}: {c}")
                key = "T_round_ms" if study == "goal3" else "T_jones"
                for r in sorted(mine, key=lambda r: (r.get(key) is None, r.get(key) or 0)):
                    print(f"  {r['design']:30s} layers {r.get('layers')}  {r['status']:12s} {key} {r.get(key)}"
                          f"  cycles {r.get('transport_cycles')}  cools {r.get('cools')}  dacs {r.get('dacs')}")
