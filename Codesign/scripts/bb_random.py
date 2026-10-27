"""q06l -- weird graphs: random, rewired, and the code's own Tanner graph as a device.

Every device the study measured so far is a lattice or a loop.  This asks what the round
costs on graphs with no order at all, and on the one graph with the most order there is:

    rewired    the 12x12 torus with a fraction p of its wires rewired at random, degree
               sequence kept (Watts-Strogatz by double-edge swaps): p = 0.1, 0.3, 1.0
    regular    a random 4-regular graph on 144 junctions -- the torus's degrees, none
               of its geometry
    geometric  144 points in a square joined within a radius chosen for mean degree 6:
               random, but local, the way a die is
    tanner     the code's Tanner graph as the machine: a hub per check and per data
               qubit, a contact trap on every Tanner edge, a spur trap on every hub --
               every contact is one hop from both operands

A device is a graph with one trap on every edge, like `grid`.  The cost curve prices
junctions of degree 2-4; random graphs have higher degrees, so the curve is extended to
degrees 5-16 AT THE DEGREE-4 PRICE (120 us, 3 quanta).  That is optimistic and stated.
None of these is drawable on a plane; the point is what the routing costs, not the die.

    python Codesign/scripts/bb_random.py [--only KEY]      # rows -> Codesign/data/q06l_random.json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from pathlib import Path

import networkx as nx

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Codesign" / "scripts"))

from q06_campaign import broadcast_use, score  # noqa: E402
from qccd.arch import load  # noqa: E402
from qccd.codes.bb import gross_code  # noqa: E402
from qccd.ir.tsir import TSIR  # noqa: E402

BRIDGE = ROOT / "Compiler" / "bridge"
QCCDC = ROOT / "Compiler" / "ocaml" / "_build" / "default" / "bin" / "qccdc_cli.exe"
TEMPLATE = ROOT / "Compiler" / "build" / "q06j" / "torus12x12.arch.json"   # the SOURCE doc: the expanded one carries derived keys


def sh(cmd, timeout=1800):
    t0 = time.time()
    p = subprocess.run([str(x) for x in cmd], capture_output=True, text=True, timeout=timeout)
    return p.returncode, p.stdout + p.stderr, time.time() - t0


# --------------------------------------------------------------------------- graphs

def torus_graph() -> nx.Graph:
    g = nx.Graph()
    for i in range(12):
        for j in range(12):
            g.add_edge((i, j), ((i + 1) % 12, j))
            g.add_edge((i, j), (i, (j + 1) % 12))
    return nx.convert_node_labels_to_integers(g, ordering="sorted")


def rewired(p: float, seed: int) -> nx.Graph:
    g = torus_graph()
    m = g.number_of_edges()
    nx.double_edge_swap(g, nswap=int(p * m), max_tries=50 * m + 1000, seed=seed)
    return g


def regular(seed: int) -> nx.Graph:
    return nx.random_regular_graph(4, 144, seed=seed)


def geometric(seed: int) -> nx.Graph:
    # radius for mean degree ~6 among 144 points on the unit square (pi r^2 * 143 = 6):
    # at mean degree 4 the graph fragments and the giant component holds 40 points
    g = nx.random_geometric_graph(144, radius=(6 / (3.14159 * 143)) ** 0.5, seed=seed)
    giant = max(nx.connected_components(g), key=len)
    return nx.convert_node_labels_to_integers(g.subgraph(giant).copy())


def graph_stats(g: nx.Graph) -> dict:
    deg = [d for _, d in g.degree()]
    return {"nodes": g.number_of_nodes(), "edges": g.number_of_edges(),
            "degree_hist": {str(k): deg.count(k) for k in sorted(set(deg))},
            "diameter": nx.diameter(g), "mean_path": round(nx.average_shortest_path_length(g), 2)}


# --------------------------------------------------------------------------- devices

def _curve_extended(prims: dict) -> dict:
    prims = json.loads(json.dumps(prims))
    cbd = prims["junction_cross"]["curve_by_degree"]
    for d in range(5, 17):
        cbd.setdefault(str(d), json.loads(json.dumps(cbd["4"])))
    return prims


def device_doc(name: str, g: nx.Graph, pos: dict, description: str) -> dict:
    """One trap per edge, junctions at the graph's nodes; the template's physics."""
    t = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    nodes, segs = [], []
    for n in g.nodes:
        x, y = pos[n]
        nodes.append({"id": f"J{n}", "pos": [round(float(x), 4), round(float(y), 4)],
                      "kind": "junction", "labels": ["lattice"]})
    for k, (u, v) in enumerate(sorted(g.edges)):
        (x1, y1), (x2, y2) = pos[u], pos[v]
        tid = f"T{k}"
        nodes.append({"id": tid, "pos": [round(float(x1 + x2) / 2, 4), round(float(y1 + y2) / 2, 4)],
                      "kind": "site", "capacity": 2, "zone_type": "trap", "labels": ["trap"]})
        segs.append({"id": f"{tid}.a", "ends": [f"J{u}", tid], "length": 0.5, "capacity": 1})
        segs.append({"id": f"{tid}.b", "ends": [tid, f"J{v}"], "length": 0.5, "capacity": 1})
    doc = {k: t[k] for k in t if k not in ("geometry", "name", "description", "primitives")}
    doc.update(name=name, description=description, primitives=_curve_extended(t["primitives"]),
               geometry={"generator": "explicit", "params": {}, "nodes": nodes, "segments": segs})
    return doc


def tanner_doc(name: str) -> tuple[dict, dict[str, str]]:
    """The Tanner graph as a machine, and the natural placement on it.

    Hub `C{v}` per check (X_v: v < 72, Z_v: 72 + v), hub `D{q}` per data qubit; a contact
    trap `K{c}_{q}` on every Tanner edge; a spur trap `A{c}` on every check hub and `S{q}`
    on every data hub.  Data ion q starts at `S{q}`; ancilla 144+v (which serves X_v then
    Z_v) starts at `A{v}`.  Hub degree is 7: six Tanner edges and the spur.
    """
    code = gross_code()
    t = json.loads(TEMPLATE.read_text(encoding="utf-8"))
    import math
    nodes, segs = [], []
    n_ch, n_d = len(code.checks), code.n
    # a ring of check hubs outside a ring of data hubs, spurs pointing away
    for c in range(n_ch):
        th = 2 * math.pi * c / n_ch
        nodes.append({"id": f"C{c}", "pos": [round(14 * math.cos(th), 4), round(14 * math.sin(th), 4)],
                      "kind": "junction", "labels": ["check"]})
        nodes.append({"id": f"A{c}", "pos": [round(16 * math.cos(th), 4), round(16 * math.sin(th), 4)],
                      "kind": "site", "capacity": 2, "zone_type": "trap", "labels": ["trap", "ancilla"]})
        segs.append({"id": f"A{c}.s", "ends": [f"C{c}", f"A{c}"], "length": 1.0, "capacity": 1})
    for q in range(n_d):
        th = 2 * math.pi * q / n_d
        nodes.append({"id": f"D{q}", "pos": [round(8 * math.cos(th), 4), round(8 * math.sin(th), 4)],
                      "kind": "junction", "labels": ["data"]})
        nodes.append({"id": f"S{q}", "pos": [round(6 * math.cos(th), 4), round(6 * math.sin(th), 4)],
                      "kind": "site", "capacity": 2, "zone_type": "trap", "labels": ["trap", "data"]})
        segs.append({"id": f"S{q}.s", "ends": [f"D{q}", f"S{q}"], "length": 1.0, "capacity": 1})
    for c, ch in enumerate(code.checks):
        thc = 2 * math.pi * c / n_ch
        for q in ch.members:
            thq = 2 * math.pi * q / n_d
            tid = f"K{c}_{q}"
            nodes.append({"id": tid, "pos": [round(11 * math.cos((thc + thq) / 2), 4),
                                             round(11 * math.sin((thc + thq) / 2), 4)],
                          "kind": "site", "capacity": 2, "zone_type": "trap", "labels": ["trap", "contact"]})
            segs.append({"id": f"{tid}.a", "ends": [f"C{c}", tid], "length": 0.5, "capacity": 1})
            segs.append({"id": f"{tid}.b", "ends": [tid, f"D{q}"], "length": 0.5, "capacity": 1})
    doc = {k: t[k] for k in t if k not in ("geometry", "name", "description", "primitives")}
    doc.update(name=name, primitives=_curve_extended(t["primitives"]),
               description="q06l: the BB code's Tanner graph as the machine -- one hop per contact",
               geometry={"generator": "explicit", "params": {}, "nodes": nodes, "segments": segs})
    place = {f"q{q}": f"S{q}" for q in range(n_d)}
    place.update({f"q{n_d + v}": f"A{v}" for v in range(72)})
    return doc, place


# --------------------------------------------------------------------------- pipeline

def compile_and_score(out: Path, key: str, arch_p: Path, qasm: Path, init: dict | None) -> dict:
    row: dict = {"key": key}
    prefix = out / f"{key}_bb"
    # the compiler reads the EXPANDED document (per-node degrees computed by the exporter);
    # the Python side reads the source document
    expanded = arch_p.with_name(arch_p.name.replace(".arch.json", ".expanded.json"))
    sh([sys.executable, BRIDGE / "export_arch.py", arch_p, "-o", expanded])
    cmd = [QCCDC, "compile", qasm, "--arch", expanded, "-o", prefix, "--no-rotate"]
    if init:
        pl = out / f"{key}.placement.json"
        pl.write_text(json.dumps(init, indent=0), encoding="utf-8")
        cmd += ["--init-placement", pl]
    try:
        rc, log, secs = sh(cmd, timeout=2400)
    except subprocess.TimeoutExpired:
        return {**row, "status": "timeout (40 min)"}
    row["compile_seconds"] = round(secs, 1)
    tsir = Path(str(prefix) + ".tsir.json")
    if rc != 0 or not tsir.exists():
        m = re.search(r"(unroutable[^\n]*|Unroutable[^\n]*|unrealised[^\n]*|exception[^\n]*)", log)
        return {**row, "status": "refused", "reason": (m.group(1) if m else log[-400:]).strip()}
    cooled = Path(str(prefix) + ".cooled.tsir.json")
    sh([sys.executable, BRIDGE / "insert_cooling.py", tsir, "--arch", arch_p, "-o", cooled])
    rc, rlog, _ = sh([sys.executable, BRIDGE / "check_tsir.py", cooled, "--arch", arch_p, "--model", "corrected"])
    row["rules"] = next((ln.strip() for ln in rlog.splitlines() if "rules passed" in ln), "?")
    row["rules_ok"] = "RULES FAILED" not in rlog and rc == 0
    row["rules_failed"] = re.findall(r"RULES FAILED: \[([^\]]*)\]", rlog)
    arch = load(str(arch_p)); prog = TSIR.load(str(cooled))
    for table, tag in (("qccdsim_jones", "jones"), ("transport_excitation", "transport")):
        s = score(arch, prog, table=table)
        row[f"T_{tag}"] = round(s["T_round_ms"], 2)
        if tag == "jones":
            row["p_eff_vs_threshold"] = round(s["p_eff_vs_threshold"], 3)
            moving = {k: us for k, us in s["us_by_class"].items() if k not in ("gate", "measure", "reset", "cool")}
            row["transport_ms"] = round(sum(moving.values()) / 1000, 2)
            row["clock_ms"] = {k: round(v, 2) for k, v in s["clock_ms"].items()}
    bc = broadcast_use(arch, prog)
    row.update(transport_instructions=bc["transport_instructions"], ion_moves=bc["ion_moves"],
               ions_per_instruction=round(bc["ions_per_instruction"], 2),
               us_per_ion=round(1000 * row["transport_ms"] / bc["ion_moves"], 2) if bc["ion_moves"] else None,
               instructions=len(prog), status="ok",
               files={"arch": str(arch_p), "qasm": str(qasm), "cooled": str(cooled),
                      "cert": str(prefix) + ".qcert.json"})
    return row


CASES = ["rewired_p0.1", "rewired_p0.3", "rewired_p1.0", "regular4", "geometric", "tanner_own", "tanner_placed"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=str(ROOT / "Compiler" / "build" / "q06l"))
    ap.add_argument("--only", default=None)
    ap.add_argument("--json", default=str(ROOT / "Codesign" / "data" / "q06l_random.json"))
    a = ap.parse_args(argv)
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    qasm = ROOT / "Compiler" / "build" / "q06j" / "bb144_a72.qasm"
    rows = []
    if Path(a.json).exists() and a.only:
        rows = [r for r in json.loads(Path(a.json).read_text(encoding="utf-8")) if r["key"] != a.only]
    for key in CASES:
        if a.only and key != a.only:
            continue
        print(f"== {key}", flush=True)
        init = None
        if key.startswith("rewired"):
            p = float(key.split("_p")[1]); g = rewired(p, seed=1)
            pos = nx.spring_layout(g, seed=1, scale=8) if p > 0.05 else {n: (n // 12, n % 12) for n in g}
            doc = device_doc(key, g, pos, f"q06l: the 12x12 torus with {int(p * 100)}% of its wires rewired at random")
        elif key == "regular4":
            g = regular(seed=3); pos = nx.spring_layout(g, seed=3, scale=8)
            doc = device_doc(key, g, pos, "q06l: a random 4-regular graph on 144 junctions")
        elif key == "geometric":
            g = geometric(seed=4); pos = {n: (12 * x, 12 * y) for n, (x, y) in nx.get_node_attributes(g, "pos").items()}
            doc = device_doc(key, g, pos, "q06l: a random geometric graph, mean degree 4")
        else:
            doc, place = tanner_doc(key); g = None
            init = place if key == "tanner_placed" else None
        arch_p = out / f"{key}.arch.json"
        arch_p.write_text(json.dumps(doc, indent=1), encoding="utf-8")
        load(str(arch_p))   # refuse early if the document is not a device
        r = compile_and_score(out, key, arch_p, qasm, init)
        r["graph"] = graph_stats(g) if g is not None else {"nodes": 288, "edges": 864 + 288, "degree_hist": {"7": 288}, "note": "Tanner graph: every contact one hop"}
        r["traps"] = sum(1 for n in doc["geometry"]["nodes"] if n["kind"] == "site")
        rows.append(r)
        if r["status"] == "ok":
            print(f"   {r['T_jones']:8.2f} ms jones  {r['T_transport']:8.2f} ms transport  {r['transport_instructions']:5d} instr  "
                  f"{r['ions_per_instruction']:5.2f} ions/i  {r['us_per_ion']:6.2f} us/ion  rules {'ok' if r['rules_ok'] else 'FAIL ' + str(r['rules_failed'])}  "
                  f"diam {r['graph'].get('diameter', '-')}  ({r['compile_seconds']:.0f}s)", flush=True)
        else:
            print(f"   {r['status']}: {r.get('reason', '')[:220]}", flush=True)
    Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
    print(f"-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
