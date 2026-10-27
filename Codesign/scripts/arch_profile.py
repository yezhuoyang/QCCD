"""What kind of machine is this, measured from a compiled BB round?

A ranking says which device is fastest.  It does not say *why*, and for a codesign study
the why is the deliverable: a ring is fast because one waveform turns 144 ions, a lattice
is slow because it moves five at a time, and both statements are numbers you can take off
a compiled programme rather than adjectives.

This reports, for each (device, programme) pair, the handful of quantities that separate
the families:

    parallelism   ions moved per transport instruction, and pairs gated per gate
                  instruction -- the two ways a machine does many things at once
    the clock     where the round's microseconds actually go, by instruction class
    the hop       what one unit of transport costs here: 5 us on a junction-free loop,
                  100 us where a rider enters a degree-3 node
    the wiring    channels, switches, and whether the programme is drivable by them
    the metal     DACs, junctions, die area, design-rule verdict, electrodes per trap

    python Codesign/scripts/arch_profile.py --json Codesign/data/q06i_profiles.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Codesign" / "scripts"))

from q06_campaign import broadcast_use, buildability, score  # noqa: E402
from qccd.arch import load  # noqa: E402
from qccd.ir.tsir import TSIR, iter_pairs  # noqa: E402

BUILD = ROOT / "Compiler" / "build"

#: family, device document, compiled programme.  One representative per family per size,
#: chosen as the best that family reached.
CASES: tuple[dict, ...] = (
    {"family": "ring + docks", "name": "ring(143,3,36) a36",
     "arch": BUILD / "q06g" / "ring288_h3_v36_o2_noswitch.arch.json",
     "prog": BUILD / "q06g" / "ring288_h3_v36_o2_noswitch_a36_bridge_structured_xz_dockall_transport-fastest.cooled.tsir.json"},
    {"family": "ring + docks", "name": "ring144_24v a24 (code-aware)",
     "arch": ROOT / "arch" / "ring144_24v.arch.json",
     "prog": BUILD / "q06" / "ring144_24v_a24_bridge_structured_xz.cooled.tsir.json"},
    {"family": "ring + docks", "name": "ring144_24v a24 (stride)",
     "arch": ROOT / "arch" / "ring144_24v.arch.json",
     "prog": BUILD / "q06" / "ring144_24v_a24_stride.cooled.tsir.json"},
    {"family": "lattice", "name": "grid11x11 (as drawn)",
     "arch": BUILD / "q06h" / "grid11x11.arch.json",
     "prog": BUILD / "q06h" / "grid11x11_bb.cooled.tsir.json"},
    # the SAME programme on the SAME graph, with the junctions pulled to two lattice
    # units so the traps clear their keep-outs.  `grid(a, b, spacing=2)` is node- and
    # segment-identical to `grid(a, b)`, so this is the honest price of a lattice that
    # has control electrodes at all -- the as-drawn row above has zero (q06i).
    {"family": "lattice", "name": "grid11x11_s2 (buildable)",
     "arch": BUILD / "q06i" / "grid11x11_s2.arch.json",
     "prog": BUILD / "q06h" / "grid11x11_bb.cooled.tsir.json"},
    {"family": "lattice", "name": "grid13x13 (direct)",
     "arch": BUILD / "q06h" / "grid13x13.arch.json",
     "prog": BUILD / "q06h" / "grid13x13_bb.cooled.tsir.json"},
    {"family": "lattice", "name": "grid13x13 (broadcast)",
     "arch": BUILD / "q06h" / "grid13x13_bcast.arch.json",
     "prog": BUILD / "q06h" / "grid13x13_bcast_bb.cooled.tsir.json"},
    {"family": "junction-free loop", "name": "racetrack180",
     "arch": BUILD / "q06i" / "racetrack180.arch.json",
     "prog": BUILD / "q06i" / "racetrack180_bb.cooled.tsir.json"},
    {"family": "junction-free loop", "name": "racetrack240",
     "arch": BUILD / "q06i" / "racetrack240.arch.json",
     "prog": BUILD / "q06i" / "racetrack240_bb.cooled.tsir.json"},
    {"family": "line", "name": "chain200",
     "arch": BUILD / "q06i" / "chain200.arch.json",
     "prog": BUILD / "q06i" / "chain200_bb.cooled.tsir.json"},
    {"family": "line", "name": "chain300",
     "arch": BUILD / "q06i" / "chain300.arch.json",
     "prog": BUILD / "q06i" / "chain300_bb.cooled.tsir.json"},
    {"family": "two loops", "name": "dual240 (no coupling)",
     "arch": BUILD / "q06i" / "dual240_c0.arch.json",
     "prog": BUILD / "q06i" / "dual240_c0_bb.cooled.tsir.json"},
    {"family": "rails + highways", "name": "ladder144",
     "arch": BUILD / "q06h" / "ladder144_r12.arch.json",
     "prog": BUILD / "q06h" / "ladder144_r12_bb.cooled.tsir.json"},
)


def hop_cost(arch, prog: TSIR) -> dict:
    """What one unit of transport costs on this device, and how often each cost is paid.

    The single number that separates a junction-free loop from a ring with dock spurs:
    entering a node of degree three or more is charged the junction curve (100 us on the
    Jones table) where a plain shuttle is 5.  A rigid rotation pays it if ANY of its riders
    lands on such a node, so the histogram is over moves, not over instructions.
    """
    dev = arch.device
    deg = Counter()
    for ins in prog.instructions:
        if ins.type != "simd":
            continue
        for p in ins.participants:
            # every node the ion ENTERS, not just where it stops.  A lattice trap is
            # degree 2 and the junction between two traps is degree 4, so counting
            # destinations alone reports a grid as junction-free -- which is how this
            # metric first read, and it is wrong by 100 us a hop.
            seen = [p.dst]
            for sid in p.via or ():
                seg = dev.segments.get(sid)
                if seg:
                    seen.extend(n for n in seg.ends if n != p.src)
            for n in set(seen):
                deg[dev.degree(n)] += 1
    return {"nodes_entered_by_degree": {str(k): v for k, v in sorted(deg.items())},
            "junction_entries": sum(v for k, v in deg.items() if k >= 3),
            "plain_entries": sum(v for k, v in deg.items() if k < 3)}


def gate_parallelism(prog: TSIR) -> dict:
    """Pairs per two-qubit gate instruction: the other way a machine does many at once."""
    sizes = [sum(1 for _ in iter_pairs(i)) for i in prog.instructions
             if i.type == "gate" and i.pairs]
    if not sizes:
        return {"gate_instructions": 0, "pairs_per_gate": 0.0, "max_pairs": 0}
    return {"gate_instructions": len(sizes),
            "pairs_per_gate": round(sum(sizes) / len(sizes), 2),
            "max_pairs": max(sizes)}


def profile(case: dict) -> dict:
    arch_p, prog_p = Path(case["arch"]), Path(case["prog"])
    if not (arch_p.exists() and prog_p.exists()):
        return {**{k: str(v) for k, v in case.items()}, "status": "not compiled"}
    arch, prog = load(str(arch_p)), TSIR.load(str(prog_p))
    # BOTH cost tables, always.  The ring's headline 47.8 ms and the lattice's 192 ms were
    # measured on different tables -- `transport_excitation` offers split/merge at 30 us
    # where `qccdsim_jones` charges 80 -- and quoting one against the other would flatter
    # whichever family was measured on the cheaper one.  A family comparison has to name
    # its table, so this reports each row on both and compares within a column.
    s = score(arch, prog, table="qccdsim_jones")
    s_fast = score(arch, prog, table="transport_excitation")
    b = buildability(arch_p)
    bc = broadcast_use(arch, prog)
    dev = arch.device
    # The number that separates the families, and the one a ranking hides: transport time
    # divided by the ion-moves it delivered.  A machine that moves 72 ions with one
    # waveform spends a fraction of a microsecond per ion; one that moves five spends
    # tens.  Both may be "fast" or "slow" for reasons of size; this is the mechanism.
    # from the replay's own per-class ledger, not from the clock buckets: `clock_ms`
    # folds the forced cool into "batches", which on a device with no docks makes the
    # cooling time masquerade as transport and understates the cost per ion six-fold.
    moving = {k: us for k, us in s["us_by_class"].items()
              if k not in ("gate", "measure", "reset", "cool")}
    transport_us = sum(moving.values())
    per_ion = transport_us / bc["ion_moves"] if bc["ion_moves"] else float("nan")
    return {
        "family": case["family"], "name": case["name"],
        "status": "ok",
        "sites": sum(1 for n in dev.nodes.values() if n.kind == "site"),
        "gate_sites": sum(1 for n in dev.nodes.values() if arch.can(n.id, "gate")),
        "junction_nodes": len(dev.junction_nodes),
        "closed_loops": sum(1 for lp in dev.loops.values() if lp.closed),
        "T_round_ms": round(s["T_round_ms"], 2),
        "T_round_ms_jones": round(s["T_round_ms"], 2),
        "T_round_ms_transport": round(s_fast["T_round_ms"], 2),
        "clock_ms": {k: round(v, 2) for k, v in s["clock_ms"].items()},
        "transport_instructions": bc["transport_instructions"],
        "ions_per_instruction": round(bc["ions_per_instruction"], 2),
        "ion_moves": bc["ion_moves"],
        "transport_ms": round(transport_us / 1000.0, 2),
        "transport_by_class_ms": {k: round(v / 1000.0, 2) for k, v in
                                  sorted(moving.items(), key=lambda kv: -kv[1])},
        "us_per_ion_moved": round(per_ion, 2),
        "gate": gate_parallelism(prog),
        "hops": hop_cost(arch, prog),
        "channels": bc["shared_channels"], "switches": bc["switches"],
        "dacs": b["dacs"], "area_mm2": b["area_mm2"],
        "drc": b["drc"], "drc_clean": b["drc_clean"],
        "pads_per_trap": b["pads_per_trap"], "buildable": b["buildable"],
        "neg_log_fidelity": round(s["neg_log_fidelity"], 4),
        "p_eff_vs_threshold": round(s["p_eff_vs_threshold"], 3),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", default=None)
    a = ap.parse_args(argv)
    rows = [profile(c) for c in CASES]
    hdr = (f"{'family':20s}{'device':30s}{'T jones':>9}{'T tran':>9}{'instr':>7}"
           f"{'ions/i':>8}{'pairs/gate':>11}{'us/ion':>8}{'DACs':>8}{'mm2':>7}  build")
    print(hdr)
    for r in rows:
        if r["status"] != "ok":
            print(f"{r['family']:20s}{r['name']:30s}   {r['status']}")
            continue
        v = ("yes" if r["buildable"] else
             ("no wells" if r["drc_clean"] else f"{sum(r['drc'].values())} drc"))
        print(f"{r['family']:20s}{r['name']:30s}{r['T_round_ms_jones']:>9.2f}"
              f"{r['T_round_ms_transport']:>9.2f}"
              f"{r['transport_instructions']:>7}{r['ions_per_instruction']:>8.2f}"
              f"{r['gate']['pairs_per_gate']:>11.2f}{r['us_per_ion_moved']:>8.2f}"
              f"{r['dacs']:>8}{r['area_mm2']:>7.1f}  {v}")
    if a.json:
        Path(a.json).parent.mkdir(parents=True, exist_ok=True)
        Path(a.json).write_text(json.dumps(rows, indent=1), encoding="utf-8")
        print(f"\n-> {a.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
