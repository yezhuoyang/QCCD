"""Four small codes, every architecture, the same clickable pages as the BB study.

    repetition d=9        9 data + 8 ancillas, a path of ZZ checks
    five-qubit [[5,1,3]]  5 data + 4 ancillas, the cyclic XZZXI stabilizers (mixed Paulis)
    Steane [[7,1,3]]      7 data + 6 ancillas
    surface d=3           9 data + 8 ancillas, the rotated 17-qubit layout

Each code's one syndrome-extraction round is compiled onto every shipped device and onto
small members of the study's own families (a ring with docks, a torus, a cylinder, a
planar grid, a random 4-regular graph, the code's Tanner graph), cooled, checked against
the 20 rules, scored on both cost tables, and -- because the certificates are small --
put through the proved Lean checker for R10 on every point.  The circuit itself is checked
in stim to measure exactly the code's stabilizers.  Pages land in `SmallCode/<code>/`,
an index per code, and one index over the four.

    python Codesign/scripts/small_codes.py            # everything
    python Codesign/scripts/small_codes.py --code steane --pages-only
"""

from __future__ import annotations

import argparse
import os
import json
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Codesign" / "scripts"))

import networkx as nx  # noqa: E402

import bb_studio as B  # noqa: E402
from q06_campaign import broadcast_use, buildability, score  # noqa: E402
from qccd.arch import load  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402
from qccd.repo_paths import resolve, to_repo  # noqa: E402

BRIDGE = ROOT / "Compiler" / "bridge"
#: the compiler: QCCD_QCCDC (a published release) if set, else the one built in this tree --
#: which may carry someone's unfinished edits, so a board of record names a release
QCCDC = (Path(os.environ["QCCD_QCCDC"]) if os.environ.get("QCCD_QCCDC")
         else ROOT / "Compiler" / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe")
QCHECK = ROOT / "Compiler" / "lean" / ".lake" / "build" / "bin" / "qcheck.exe"
EXAMPLES = ROOT / "Compiler" / "examples"
ARCH = ROOT / "arch"
RING_TEMPLATE = ARCH / "ring144_24v.arch.json"
GRID_TEMPLATE = ROOT / "Compiler" / "build" / "q06j" / "torus12x12.arch.json"
OUT = ROOT / "SmallCode"


def sh(cmd, timeout=1800):
    t0 = time.time()
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr, time.time() - t0


# --------------------------------------------------------------------------- the codes

def five_qubit_qasm() -> str:
    """[[5,1,3]]: the four cyclic stabilizers XZZXI, one ancilla each, written with h and
    cx only (the compiler's gate set): an X leg is cx(anc, q), a Z leg is h(q) cx(anc, q)
    h(q), the ancilla prepared in |+> and read in the X basis."""
    stabs = ["XZZXI", "IXZZX", "XIXZZ", "ZXIXZ"]
    s = 'OPENQASM 2.0;\ninclude "qelib1.inc";\n// five-qubit [[5,1,3]]: 5 data, 4 ancilla, one ESM round\nqreg q[9];\ncreg c[4];\n'
    for k, st in enumerate(stabs):
        a = 5 + k
        s += f"h q[{a}];\n"
        for q, pch in enumerate(st):
            if pch == "X":
                s += f"cx q[{a}],q[{q}];\n"
            elif pch == "Z":
                s += f"h q[{q}];\ncx q[{a}],q[{q}];\nh q[{q}];\n"
        s += f"h q[{a}];\nmeasure q[{a}] -> c[{k}];\n"
    return s


#: name -> (title, qasm file or text, n_data, stabilizers as Pauli strings over the data)
def codes() -> dict:
    steane_x = [[3, 4, 5, 6], [1, 2, 5, 6], [0, 2, 4, 6]]
    surf_z = [[0, 1, 3, 4], [4, 5, 7, 8], [2, 5], [3, 6]]
    surf_x = [[1, 2, 4, 5], [3, 4, 6, 7], [0, 1], [7, 8]]

    def pauli(n, sup, p):
        return "".join(p if i in sup else "I" for i in range(n))

    return {
        "repetition": {"title": "Repetition code, distance 9", "short": "rep9", "n_data": 9,
                       "qasm": EXAMPLES / "rep9_esm.qasm",
                       "stabs": [pauli(9, [i, i + 1], "Z") for i in range(8)]},
        "five_qubit": {"title": "Five-qubit code [[5,1,3]]", "short": "5-qubit", "n_data": 5,
                       "qasm_text": five_qubit_qasm(),
                       "stabs": ["XZZXI", "IXZZX", "XIXZZ", "ZXIXZ"]},
        "steane": {"title": "Steane code [[7,1,3]]", "short": "Steane", "n_data": 7,
                   "qasm": EXAMPLES / "steane_esm.qasm",
                   "stabs": [pauli(7, s, "X") for s in steane_x] + [pauli(7, s, "Z") for s in steane_x]},
        "surface17": {"title": "Surface code, distance 3 (17 qubits)", "short": "surface-17", "n_data": 9,
                      "qasm": EXAMPLES / "surface17_esm.qasm",
                      "stabs": [pauli(9, s, "X") for s in surf_x] + [pauli(9, s, "Z") for s in surf_z]},
    }


def stim_check(qasm: Path, n_data: int, stabs: list[str]) -> str:
    """The circuit measures exactly these stabilizers: outcomes repeat across two rounds,
    and a single-qubit X or Z on data qubit q flips exactly the checks that anticommute
    with it.  The same test as `esm_check.py`, over Pauli strings so mixed checks work."""
    import stim
    from esm_check import parse, run_round
    n_q, ops = parse(qasm)
    n_checks = len(stabs)
    ancillas = {op[1] for op in ops if op[0] == "measure"}
    sim = stim.TableauSimulator()
    first = run_round(sim, ops, n_checks, ancillas)
    second = run_round(sim, ops, n_checks, ancillas)
    if first != second:
        return "FAILED: outcomes do not repeat"
    for q in range(n_data):
        for pauli in ("X", "Z"):
            want = {k for k, st in enumerate(stabs) if st[q] in (("Z", "Y") if pauli == "X" else ("X", "Y"))}
            (sim.x if pauli == "X" else sim.z)(q)
            got = run_round(sim, ops, n_checks, ancillas)
            flipped = {k for k in range(n_checks) if got[k] != second[k]}
            (sim.x if pauli == "X" else sim.z)(q)
            second = run_round(sim, ops, n_checks, ancillas)
            if flipped != want:
                return f"FAILED: {pauli}_{q} flips {sorted(flipped)}, expected {sorted(want)}"
    return "measures exactly the stabilizers"


# --------------------------------------------------------------------------- devices

def tanner_graph(qasm: Path) -> list[tuple[int, list[int]]]:
    """(ancilla, data it touches) from the circuit itself: a cx between an ancilla and a
    data qubit is a Tanner edge, whichever way it points."""
    from esm_check import parse
    _, ops = parse(qasm)
    anc = {op[1] for op in ops if op[0] == "measure"}
    edges: dict[int, list[int]] = {}
    for op in ops:
        if op[0] == "cx":
            a, b = op[1], op[2]
            if a in anc and b not in anc:
                edges.setdefault(a, []).append(b)
            elif b in anc and a not in anc:
                edges.setdefault(b, []).append(a)
    return [(a, sorted(set(ds))) for a, ds in sorted(edges.items())]


def tanner_doc(name: str, qasm: Path, n_data: int) -> dict:
    import math
    t = json.loads(GRID_TEMPLATE.read_text(encoding="utf-8"))
    nodes, segs = [], []
    tg = tanner_graph(qasm)
    n_ch = len(tg)
    for c, (a, _) in enumerate(tg):
        th = 2 * math.pi * c / n_ch
        nodes.append({"id": f"C{a}", "pos": [round(7 * math.cos(th), 4), round(7 * math.sin(th), 4)], "kind": "junction", "labels": ["check"]})
        nodes.append({"id": f"A{a}", "pos": [round(9 * math.cos(th), 4), round(9 * math.sin(th), 4)], "kind": "site", "capacity": 2, "zone_type": "trap", "labels": ["trap", "ancilla"]})
        segs.append({"id": f"A{a}.s", "ends": [f"C{a}", f"A{a}"], "length": 1.0, "capacity": 1})
    for q in range(n_data):
        th = 2 * math.pi * q / n_data
        nodes.append({"id": f"D{q}", "pos": [round(4 * math.cos(th), 4), round(4 * math.sin(th), 4)], "kind": "junction", "labels": ["data"]})
        nodes.append({"id": f"S{q}", "pos": [round(2.2 * math.cos(th), 4), round(2.2 * math.sin(th), 4)], "kind": "site", "capacity": 2, "zone_type": "trap", "labels": ["trap", "data"]})
        segs.append({"id": f"S{q}.s", "ends": [f"D{q}", f"S{q}"], "length": 1.0, "capacity": 1})
    for c, (a, ds) in enumerate(tg):
        thc = 2 * math.pi * c / n_ch
        for q in ds:
            thq = 2 * math.pi * q / n_data
            tid = f"K{a}_{q}"
            nodes.append({"id": tid, "pos": [round(5.5 * math.cos((thc + thq) / 2), 4), round(5.5 * math.sin((thc + thq) / 2), 4)], "kind": "site", "capacity": 2, "zone_type": "trap", "labels": ["trap", "contact"]})
            segs.append({"id": f"{tid}.a", "ends": [f"C{a}", tid], "length": 0.5, "capacity": 1})
            segs.append({"id": f"{tid}.b", "ends": [tid, f"D{q}"], "length": 0.5, "capacity": 1})
    from bb_random import _curve_extended
    doc = {k: t[k] for k in t if k not in ("geometry", "name", "description", "primitives")}
    doc.update(name=name, primitives=_curve_extended(t["primitives"]),
               description=f"the code's Tanner graph as the machine: {n_ch} check hubs, {n_data} data hubs, a contact trap per edge",
               geometry={"generator": "explicit", "params": {}, "nodes": nodes, "segments": segs})
    return doc


def random_doc(name: str, n: int, seed: int) -> dict:
    from bb_random import device_doc
    g = nx.random_regular_graph(4, n, seed=seed)
    return device_doc(name, g, nx.spring_layout(g, seed=seed, scale=4), f"a random 4-regular graph on {n} junctions")


def gen_doc(template: Path, name: str, generator: str, params: dict, description: str) -> dict:
    doc = json.loads(template.read_text(encoding="utf-8"))
    doc["name"] = name
    doc["description"] = description
    doc["geometry"] = {"generator": generator, "params": params}
    return doc


#: (key, family, title, claim, doc-maker or shipped path)
def devices(code_key: str, qasm: Path, n_data: int) -> list[dict]:
    shipped = [
        ("ring144_24v", "ring + docks", "ring144_24v — the shipped ring, 24 docks", "the study's shipped conveyor: rigid rotation with dock spurs"),
        ("cyclone_base", "junction-free loop", "cyclone_base — a loop with no junctions", "Cyclone's base: rotation on a loop without spurs"),
        ("h2_racetrack", "junction-free loop", "h2_racetrack — Quantinuum's H2 loop", "one continuous racetrack, no junctions"),
        ("cyclone_dual_loop", "two loops", "cyclone_dual_loop — data loop and ancilla loop", "two loops, the ancilla loop rotating"),
        ("ladder_2x72", "rails + highways", "ladder_2x72 — rails and highways", "two rails joined by rungs"),
        ("grid9x9", "lattice", "grid9x9 — the baseline lattice", "traps on the wires of a 9x9 junction grid"),
        ("deck_unit_cell", "lattice", "deck_unit_cell — the same lattice, broadcast-wired", "grid9x9 with the 32-channel WISE control block"),
        ("chain", "line", "chain — a line of traps", "one linear trap"),
        ("stationary_chain", "line", "stationary_chain — two traps, one rail", "two traps joined by one rail: the breakeven baseline"),
    ]
    out = [{"key": k, "family": f, "title": t, "claim": c, "arch": ARCH / f"{k}.arch.json"} for k, f, t, c in shipped]
    out += [
        {"key": "ring24_8", "family": "ring + docks", "title": "ring(12, 2, 8) — a small ring with 8 docks",
         "claim": "the study's family at the code's scale: 24 slots, 8 dock spurs",
         "doc": gen_doc(RING_TEMPLATE, "ring24_8", "ring", {"width": 12, "height": 2, "verticals": 8}, "a 24-slot ring with 8 docks")},
        {"key": "torus4x4", "family": "torus", "title": "torus 4x4 — a periodic lattice",
         "claim": "every row and column a closed loop, 32 traps",
         "doc": gen_doc(GRID_TEMPLATE, "torus4x4", "grid", {"a": 4, "b": 4, "periodic": True}, "a 4x4 torus")},
        {"key": "cylinder6x4", "family": "torus", "title": "cylinder 6x4 — four concentric rings with spokes",
         "claim": "the torus drawn as a chip: 42 traps, no crossings",
         "doc": gen_doc(GRID_TEMPLATE, "cylinder6x4", "cylinder", {"a": 6, "b": 4}, "four concentric rings joined by six spokes")},
        {"key": "grid5x5", "family": "lattice", "title": "grid5x5 — a small planar lattice",
         "claim": "40 traps on the wires of a 5x5 junction grid",
         "doc": gen_doc(GRID_TEMPLATE, "grid5x5", "grid", {"a": 5, "b": 5}, "a 5x5 planar lattice")},
        {"key": "random16", "family": "random graph", "title": "random 4-regular graph, 16 junctions",
         "claim": "32 traps on a random expander: short paths, no drawing",
         "doc": random_doc("random16", 16, 7)},
        {"key": "tanner", "family": "random graph", "title": "the code's Tanner graph as the machine",
         "claim": "a hub per check and per data qubit, a contact trap on every Tanner edge: every contact one hop",
         "doc": tanner_doc(f"tanner_{code_key}", qasm, n_data)},
    ]
    return out


# --------------------------------------------------------------------------- pipeline

def compile_point(out: Path, code_key: str, dev: dict, qasm: Path) -> dict:
    row = {"key": f"{code_key}_{dev['key']}", "device": dev["key"], "family": dev["family"], "title": dev["title"], "claim": dev["claim"]}
    if "doc" in dev:
        arch_p = out / f"{dev['key']}.arch.json"
        arch_p.write_text(json.dumps(dev["doc"], indent=1), encoding="utf-8")
    else:
        arch_p = Path(dev["arch"])
    row["arch"] = str(arch_p)
    expanded = out / f"{dev['key']}.expanded.json"
    rc, log, _ = sh([sys.executable, BRIDGE / "export_arch.py", arch_p, "-o", expanded])
    if rc != 0 or not expanded.exists():
        return {**row, "status": "refused", "reason": "export: " + log[-300:].strip()}
    prefix = out / f"{dev['key']}_{code_key}"
    try:
        rc, log, secs = sh([QCCDC, "compile", qasm, "--arch", expanded, "-o", prefix], timeout=900)
    except subprocess.TimeoutExpired:
        return {**row, "status": "refused", "reason": "timeout (15 min)"}
    row["compile_seconds"] = round(secs, 1)
    tsir = Path(str(prefix) + ".tsir.json")
    if rc != 0 or not tsir.exists():
        m = re.search(r"(unroutable[^\n]*|Unroutable[^\n]*|unrealised[^\n]*|too small[^\n]*|cannot[^\n]*|exception[^\n]*)", log)
        return {**row, "status": "refused", "reason": (m.group(1) if m else log[-300:]).strip()}
    # A PARTIAL PROGRAMME IS A REFUSAL.  The general router can return having left circuit
    # ops unrealised (the certificate lists them); the file exists, the rules pass on what
    # was emitted, and the proved checker rejects it -- so it must not be ranked.
    try:
        cert = json.loads(Path(str(prefix) + ".qcert.json").read_text(encoding="utf-8"))
        unreal = cert.get("unrealised") or []
    except Exception:  # noqa: BLE001
        unreal = []
    if unreal:
        return {**row, "status": "refused",
                "reason": f"{len(unreal)} circuit ops unrealised: the router emitted a partial programme "
                          f"(ops {unreal[:6]}{'...' if len(unreal) > 6 else ''})"}
    cooled = Path(str(prefix) + ".cooled.tsir.json")
    sh([sys.executable, BRIDGE / "insert_cooling.py", tsir, "--arch", arch_p, "-o", cooled])
    rc, rlog, _ = sh([sys.executable, BRIDGE / "check_tsir.py", cooled, "--arch", arch_p, "--model", "corrected"])
    row["rules"] = next((ln.strip() for ln in rlog.splitlines() if "rules passed" in ln), "?")
    row["rules_ok"] = "RULES FAILED" not in rlog and rc == 0
    row["rules_failed"] = re.findall(r"\[(R[0-9a-z]+)\]", rlog) if not row["rules_ok"] else []
    arch = load(str(arch_p)); prog = TSIR.load(str(cooled))
    numbers: dict = {}
    for table, tag in (("qccdsim_jones", "jones"), ("transport_excitation", "transport")):
        s = score(arch, prog, table=table)
        numbers[f"T_{tag}"] = round(s["T_round_ms"], 3)
        if tag == "jones":
            numbers["p_eff_vs_threshold"] = round(s["p_eff_vs_threshold"], 3)
            moving = {k: us for k, us in s["us_by_class"].items() if k not in ("gate", "measure", "reset", "cool")}
            transport_us = sum(moving.values())
    bc = broadcast_use(arch, prog)
    numbers.update(ions_per_instruction=round(bc["ions_per_instruction"], 2), transport_instructions=bc["transport_instructions"],
                   us_per_ion=round(transport_us / bc["ion_moves"], 2) if bc["ion_moves"] else None)
    try:
        b = buildability(arch_p)
        numbers.update(dacs=b["dacs"], area_mm2=b["area_mm2"], pads_per_trap=b["pads_per_trap"], buildable=b["buildable"], drc_clean=b["drc_clean"], drc=b["drc"])
    except Exception as exc:  # noqa: BLE001
        numbers["build_error"] = f"{type(exc).__name__}"
    # R10: the fast checkers, then the proved one -- small certificates take it seconds
    qc = Path(str(prefix) + ".qcheck.json")
    sh([sys.executable, BRIDGE / "mk_qcheck_input.py", prefix, "--arch", expanded, "-o", qc])
    rc, clog, lsecs = sh([sys.executable, BRIDGE / "check_cert.py", prefix, "--qasm", qasm, "--arch", arch_p, "--qcheck", qc], timeout=1800)
    verdict = next((ln.strip() for ln in clog.splitlines() if "-> R10" in ln), "")
    numbers["r10"] = "R10 passed" in clog
    row.update(status="ok", numbers=numbers, r10_verdict=verdict[:160], lean_seconds=round(lsecs, 1),
               instructions=len(prog), prefix=str(prefix), qasm=str(qasm), cooled=str(cooled))
    return row


def run_code(code_key: str, spec: dict, out: Path, only_dev: str | None) -> list[dict]:
    out.mkdir(parents=True, exist_ok=True)
    if "qasm" in spec:
        qasm = Path(spec["qasm"])
    else:
        qasm = out / f"{code_key}_esm.qasm"; qasm.write_text(spec["qasm_text"], encoding="utf-8")
    rows = []
    stim = stim_check(qasm, spec["n_data"], spec["stabs"])
    print(f"== {code_key}: {stim}", flush=True)
    for dev in devices(code_key, qasm, spec["n_data"]):
        if only_dev and dev["key"] != only_dev:
            continue
        r = compile_point(out, code_key, dev, qasm); r["stim"] = stim
        rows.append(r)
        if r["status"] == "ok":
            n = r["numbers"]
            print(f"   {dev['key']:18s} {n['T_jones']:8.3f} ms  {n['ions_per_instruction']:5.2f} ions/i  rules {'ok' if r['rules_ok'] else 'FAIL ' + str(r['rules_failed'])}  R10 {'passed' if n['r10'] else 'NOT'}  ({r['compile_seconds']:.0f}s + lean {r['lean_seconds']:.0f}s)", flush=True)
        else:
            print(f"   {dev['key']:18s} refused: {r.get('reason', '')[:120]}", flush=True)
    return rows


# --------------------------------------------------------------------------- pages

def pages(code_key: str, spec: dict, rows: list[dict], out: Path) -> None:
    ok = [r for r in rows if r["status"] == "ok"]
    sound = [r for r in ok if r.get("rules_ok") and r["numbers"].get("r10")]
    best = min(sound, key=lambda r: r["numbers"]["T_jones"]) if sound else None
    entries = []
    for i, r in enumerate(sorted(rows, key=lambda r: (r["status"] != "ok", r.get("numbers", {}).get("T_jones", 1e9)))):
        e = {"key": f"{i + 1:02d}_{r['device']}", "family": r["family"], "title": f"{spec['title']} on {r['title']}",
             "arch": Path(r["arch"]), "claim": r["claim"], "kicker": spec["short"].upper() + " CODESIGN",
             "short": r["device"], "answer": best is not None and r is best}
        if r["status"] == "ok":
            e.update(prefix=Path(r["prefix"]), qasm=Path(r["qasm"]), numbers=dict(r["numbers"]), r10=r["numbers"].get("r10", False))
        else:
            e["refusal"] = r.get("reason", "refused")
        entries.append(e)
    manifest = []
    for e in entries:
        m = B.build_one(e, out, max_frames=20000)
        m["short"] = e["short"]; m["answer"] = e["answer"]
        manifest.append(m)
        print(f"   page {m['key']:26s} {m['status']}", flush=True)
    (out / "manifest.json").write_text(json.dumps(to_repo(manifest), indent=1, default=str), encoding="utf-8")
    B.write_index(manifest, out, title=f"{spec['title']} on QCCD — every architecture, ranked",
                  subtitle=f"One syndrome-extraction round of the {spec['title']} compiled onto every architecture, "
                           f"shipped and from the study's families; the circuit checked in stim to measure exactly the "
                           f"code's stabilizers, every program checked against the 20 rules and, where it passed, by the "
                           f"proved Lean checker (R10).")


def top_index(all_rows: dict[str, list[dict]], specs: dict) -> None:
    cards = ""
    for k, rows in all_rows.items():
        ok = [r for r in rows if r["status"] == "ok"]
        ok.sort(key=lambda r: r["numbers"]["T_jones"])
        sound = [r for r in ok if r.get("rules_ok") and r["numbers"].get("r10")]
        best = sound[0] if sound else None
        n_r10 = sum(1 for r in ok if r["numbers"].get("r10"))
        lines = "".join(f"<tr><td>{r['device']}{'' if r.get('rules_ok') else ' <span style=color:#c62828>(rules failed)</span>'}</td><td>{r['numbers']['T_jones']:.2f} ms</td><td>{r['numbers']['ions_per_instruction']:.1f}</td>"
                        f"<td>{'✓' if r['numbers'].get('r10') else '–'}</td></tr>" for r in ok[:6])
        refused = [r["device"] for r in rows if r["status"] != "ok"]
        cards += (f"<a class='card' href='{k}/index.html'><h2>{specs[k]['title']}</h2>"
                  f"<p>{len(ok)} of {len(rows)} architectures run it{(' · ' + str(n_r10) + ' with R10 by the Lean checker') if n_r10 else ''}"
                  f"{(' · refused: ' + ', '.join(refused)) if refused else ''}</p>"
                  f"<p class='best'>fastest: <b>{best['device'] if best else '–'}</b>{(' at %.2f ms' % best['numbers']['T_jones']) if best else ''}</p>"
                  f"<table><tr><th>device</th><th>round</th><th>ions/instr</th><th>R10</th></tr>{lines}</table></a>")
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Small codes on QCCD</title><style>
body{{margin:0;background:#fcfcfb;color:#0b0b0b;font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:28px 24px 60px}} h1{{font-size:22px;font-weight:600;margin:0 0 4px}}
.sub{{color:#52514e;margin:0 0 20px;max-width:80ch}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(420px,1fr));gap:16px}}
.card{{display:block;background:#fff;border:1px solid #e6e5e1;border-radius:10px;padding:16px;text-decoration:none;color:inherit}}
.card:hover{{border-color:#2a78d6}} .card h2{{font-size:16px;margin:0 0 6px;color:#1c2a4a}} .card p{{margin:0 0 6px;color:#52514e;font-size:13px}}
.card .best{{color:#0b0b0b}} table{{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}}
th,td{{padding:3px 0;border-bottom:1px solid #efeeeb;text-align:left}} th{{color:#8a8985;font-weight:500}} td:nth-child(n+2),th:nth-child(n+2){{text-align:right;font-variant-numeric:tabular-nums}}
</style></head><body><main>
<h1>Small codes on QCCD — every architecture, ranked</h1>
<p class="sub">The same study as the BB code's, for four small codes: one syndrome-extraction round each, compiled onto every shipped device and onto small members of the ring, torus, lattice and random-graph families. Open a code for its ranking plot; click a dot there to step the program in the design tool.</p>
<div class="grid">{cards}</div>
<p class="sub" style="margin-top:22px">Built by <code>Codesign/scripts/small_codes.py</code>; rows in <code>SmallCode/&lt;code&gt;/rows.json</code>. Every compiled point passed the 20 rules unless its page says otherwise; R10 is the proved Lean checker's verdict on that point's certificate.</p>
</main></body></html>"""
    (OUT / "index.html").write_text(html, encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--code", default=None, help="one code key")
    ap.add_argument("--device", default=None, help="one device key")
    ap.add_argument("--pages-only", action="store_true", help="rebuild pages from rows.json")
    ap.add_argument("--overlay-only", action="store_true",
                    help="re-inject the notes/tools overlay into the rendered pages and rewrite the indexes")
    a = ap.parse_args(argv)
    specs = codes()
    all_rows: dict[str, list[dict]] = {}
    if a.overlay_only:
        marker = "<style>\n#bbnotes"
        for k, spec in specs.items():
            out = OUT / k
            mf = out / "manifest.json"
            if not mf.exists():
                continue
            manifest = resolve(json.loads(mf.read_text(encoding="utf-8")))
            for r in manifest:
                if r.get("status") != "ok":
                    continue
                page = out / r["page"]
                src = page.read_text(encoding="utf-8")
                if marker in src:
                    page.write_text(src[: src.index(marker)] + "</body></html>", encoding="utf-8", newline="")
                B._inject(page, r, r.get("marks") or [], r.get("numbers") or {}, "index.html")
            B.write_index(manifest, out, title=f"{spec['title']} on QCCD — every architecture, ranked",
                          subtitle=f"One syndrome-extraction round of the {spec['title']} compiled onto every architecture, "
                                   f"shipped and from the study's families; the circuit checked in stim, every program checked "
                                   f"against the 20 rules and, where it passed, by the proved Lean checker (R10).")
            print(f"   overlay -> {k}: {sum(1 for r in manifest if r.get('status') == 'ok')} pages")
        return 0
    for k, spec in specs.items():
        out = OUT / k
        if a.code and k != a.code:
            rj = out / "rows.json"
            if rj.exists():
                all_rows[k] = resolve(json.loads(rj.read_text(encoding="utf-8")))
            continue
        rj = out / "rows.json"
        if a.pages_only and rj.exists():
            rows = resolve(json.loads(rj.read_text(encoding="utf-8")))
        else:
            rows = run_code(k, spec, out, a.device)
            if a.device and rj.exists():
                rows = [r for r in resolve(json.loads(rj.read_text(encoding="utf-8"))) if r["device"] != a.device] + rows
            rj.write_text(json.dumps(to_repo(rows), indent=1, default=str), encoding="utf-8")
        all_rows[k] = rows
        pages(k, spec, rows, out)
    top_index(all_rows, specs)
    print(f"-> {OUT / 'index.html'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
