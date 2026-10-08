"""Quantinuum H2 (Moses et al., PRX 13, 041052 (2023), arXiv 2305.03828 v2) in our
architecture language.  Writes `h2.arch.json` beside this file and checks that it loads.

    python Reproduce/moses2023/build_h2.py

Every number is either stated by the paper (section / figure given), read off Fig. 2d
(the figure is the trap's top metal layer drawn to scale: its 750 um bar, its 6.58 mm long
axis and its 2.02 mm isthmus agree to 1%), or marked "not stated" with the assumption.
DESIGN.md tabulates them.  No primitive duration is taken from anywhere: the paper states
none, so every time below is a UNIT time and no time this document produces is compared.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "h2.arch.json"
PAPER = "2305.03828"

# ---------------------------------------------------------------- geometry (unit = 750 um)
#
# Read off the vector drawing of Fig. 2d (h2_benchmarking_figure.pdf, PDF points):
#   DG01..DG04 centres x = 168.05, 224.00, 280.15, 336.10 pt on the bottom rail (y 208.2)
#     -> pitch 56.0 pt = the 750 um the text states (Sec. II.A) and the figure's bar shows
#   top rail y = 152.2 pt -> 56.0 pt above the bottom one = 1.00 pitch
#   grey auxiliary blocks centred midway between and outside the blue zones: 5 per rail
#   gate region (outer edges of the outer grey blocks) x = 129.0 .. 375.0 pt
#   end curves: semicircles of radius 28.0 pt = 0.50 pitch, centres x = 57.65 and 446.5 pt
#   load hole: the leftmost point of the left curve (black square at x 29.6, y 180.3)
PITCH_UM = 750.0
ROW_Y = 1.0                     # top rail, in pitches (Fig. 2d: 56.0 pt / 56.0 pt)
R_END = 0.5                     # end-curve radius, pitches (Fig. 2d: 28.0 pt)
X_EDGE_R = 3.5 + 0.197          # outer edge of the right-most grey block (375.0 pt)
X_EDGE_L = -0.5 - 0.197         # and of the left-most (129.0 pt)
X_C_R = 1.5 + 3.472             # right curve centre (446.5 pt)
X_C_L = 1.5 - 3.472             # left curve centre (57.65 pt)
N_WELLS = 20                    # per conveyor region, Sec. II.A


def conveyor_point(s: float, side: str) -> tuple[float, float]:
    """The point at arc length `s` along one conveyor region, in loop order.

    Right region: from the gate region's right edge along the bottom rail, round the right
    curve, back along the top rail.  Left region: from the left edge along the top rail,
    round the left curve, back along the bottom rail."""
    if side == "R":
        straight = X_C_R - X_EDGE_R
        if s <= straight:
            return (X_EDGE_R + s, 0.0)
        s -= straight
        arc = math.pi * R_END
        if s <= arc:
            a = -math.pi / 2 + s / R_END                       # counter-clockwise
            return (X_C_R + R_END * math.cos(a), R_END + R_END * math.sin(a))
        s -= arc
        return (X_C_R - s, ROW_Y)
    straight = X_EDGE_L - X_C_L
    if s <= straight:
        return (X_EDGE_L - s, ROW_Y)
    s -= straight
    arc = math.pi * R_END
    if s <= arc:
        a = math.pi / 2 + s / R_END
        return (X_C_L + R_END * math.cos(a), R_END + R_END * math.sin(a))
    s -= arc
    return (X_C_L + s, 0.0)


CONV_LEN = 2 * (X_C_R - X_EDGE_R) + math.pi * R_END      # the same on both sides
WELL_PITCH = CONV_LEN / N_WELLS


def r4(x: float) -> float:
    return round(x + 0.0, 4)


SRC_FIG = f"{PAPER} Fig. 2d (to scale: 750 um bar)"


def build() -> dict:
    nodes: list[dict] = []
    order: list[str] = []
    s_of: dict[str, float] = {}       # arc length along the whole loop, for segment lengths

    def add(nid: str, pos, zone: str, labels: list[str], s: float) -> None:
        nodes.append({"id": nid, "pos": [r4(pos[0]), r4(pos[1])], "kind": "site",
                      "zone_type": zone, "labels": labels})
        order.append(nid)
        s_of[nid] = s

    s = 0.0
    # bottom rail, left to right: AUXD0 DG01 AUXD1 ... DG04 AUXD4 (Fig. 2d: "DG01-DG04 (from
    # left to right)"); the grey auxiliary blocks sit midway between and outside them
    for k in range(9):
        x = -0.5 + 0.5 * k
        if k % 2 == 1:
            g = (k + 1) // 2
            add(f"DG0{g}", (x, 0.0), "dg",
                [f"DG0{g}", "gate zone (blue)", f"src: {PAPER} Sec. II.A; Fig. 2d caption",
                 "pos: x = 750 um pitch (Sec. II.A), bottom rail (Fig. 2d)"], s)
        else:
            a = k // 2
            add(f"AUXD{a}", (x, 0.0), "aux",
                [f"auxiliary block {a} of the bottom rail (light grey)",
                 f"src: {PAPER} Sec. II.A, Sec. II.E; Fig. 2d",
                 "not stated: count and position read from Fig. 2d (midway between zones)"], s)
        s += 0.5
    # right conveyor region: 20 wells, evenly spaced along the green arc (Sec. II.A: "20
    # wells on each side"; well pitch not stated)
    s_edge = s - 0.5 + (X_EDGE_R - 3.5)                 # arc length at the gate-region edge
    for k in range(N_WELLS):
        sk = (k + 0.5) * WELL_PITCH
        add(f"CR{k + 1:02d}", conveyor_point(sk, "R"), "conveyor",
            [f"right conveyor well {k + 1} of {N_WELLS}", f"src: {PAPER} Sec. II.A; Fig. 2b,d,h",
             "not stated: well pitch (wells spread evenly over the green arc of Fig. 2d)"],
            s_edge + sk)
    s = s_edge + CONV_LEN + (X_EDGE_R - 3.5)
    # top rail, right to left: AUXU0 UG01 AUXU1 ... UG04 AUXU4 (Fig. 2d: "UG01-UG04 (from
    # right to left)")
    for k in range(9):
        x = 3.5 - 0.5 * k
        if k % 2 == 1:
            g = (k + 1) // 2
            add(f"UG0{g}", (x, ROW_Y), "ug",
                [f"UG0{g}", "gate zone (blue), sorting only",
                 f"src: {PAPER} Sec. II.A; Fig. 2d caption",
                 "pos: x = 750 um pitch (Sec. II.A); top rail 1.00 pitch above (Fig. 2d)"], s)
        else:
            a = k // 2
            add(f"AUXU{a}", (x, ROW_Y), "aux",
                [f"auxiliary block {a} of the top rail (light grey)",
                 f"src: {PAPER} Sec. II.A, Sec. II.E; Fig. 2d",
                 "not stated: count and position read from Fig. 2d (midway between zones)"], s)
        s += 0.5
    s_edge = s - 0.5 + (X_EDGE_R - 3.5)
    # left conveyor region: 20 wells and, in its middle, the load hole (Sec. II.A)
    for k in range(N_WELLS):
        if k == N_WELLS // 2:
            add("LOAD", conveyor_point(CONV_LEN / 2, "L"), "load",
                ["load hole", f"src: {PAPER} Sec. II.A ('in the middle of the left-side "
                 "conveyor belt region'), Sec. II.B; Fig. 2d",
                 "not stated: one site, between wells 10 and 11"], s_edge + CONV_LEN / 2)
        sk = (k + 0.5) * WELL_PITCH
        add(f"CL{k + 1:02d}", conveyor_point(sk, "L"), "conveyor",
            [f"left conveyor well {k + 1} of {N_WELLS}", f"src: {PAPER} Sec. II.A; Fig. 2b,d,h",
             "not stated: well pitch (wells spread evenly over the green arc of Fig. 2d)"],
            s_edge + sk)
    total = s_edge + CONV_LEN + (X_EDGE_R - 3.5)

    segments = []
    for i, a in enumerate(order):
        b = order[(i + 1) % len(order)]
        ds = (s_of[b] - s_of[a]) % total
        segments.append({"id": f"e{i:02d}", "ends": [a, b], "length": r4(ds), "loop": "L0",
                         "labels": ["RF null of the race track (one closed loop, no junction)"]})

    wells = [n for n in order if n.startswith(("CR", "CL"))]
    zone_sites = [n for n in order if n not in wells]
    explicit = [{"id": f"conveyor.{ph}", "role": "conveyor", "drives": wells} for ph in "abc"]
    explicit += [{"id": f"zone.{n}", "role": "independent", "drives": [n]} for n in zone_sites]

    unit = {"time_basis": "unit", "source": "none: unit time",
            "note": f"{PAPER} states no duration for this primitive (Sec. II.E gives only "
                    "whole-shot times, Table I); 1 us is a unit so the replay counts "
                    "operations. Times from this document are never compared."}

    def unit_curve(what: str) -> dict:
        return {"curve": [{"us": 1.0, "quanta": 0.0, "table": "local",
                           "source": "none: unit time",
                           "label": f"unit time for {what}; not stated by {PAPER} "
                                    "(Sec. II.E); quanta not stated either"}]}

    return {
        "name": "h2_moses2023",
        "schema_version": "0.3",
        "description": (
            "Quantinuum H2 as published in 2305.03828 (v2): one closed race-track RF null with "
            "no junctions; 4 DG gate zones that gate, prepare and measure, 4 UG gate zones that "
            "only sort, 5 auxiliary parking blocks per rail, two broadcast conveyor regions of "
            "20 wells each, and a load hole in the middle of the left one. Capacities in qubits "
            "(one qubit = one 171Yb+-138Ba+ pair). Positions in units of the 750 um gate pitch. "
            "Primitive times are UNIT times: the paper states none."),
        "provenance": {
            "paper": PAPER,
            "version_read": "v2 (16 May 2023), h2_benchmarking.tex + h2_benchmarking_figure.pdf",
            "citation": "S. A. Moses et al., A Race Track Trapped-Ion Quantum Processor, "
                        "Phys. Rev. X 13, 041052 (2023)",
            "pitch_um": PITCH_UM,
            "pitch_source": f"{PAPER} Sec. II.A: 'the spacing between gate zones remains the "
                            "same (750 um)'",
            "figure_scale_check": "Fig. 2d drawn at 56.0 pt per 750 um: DC extent 6.58 mm "
                                  "and isthmus 2.02 mm (Fig. 1 caption) agree to 1%",
            "well_pitch_um": round(WELL_PITCH * PITCH_UM, 1),
            "well_pitch_source": "not stated: 20 wells spread evenly over the conveyor arc "
                                 "of Fig. 2d (the figure's storage ions sit ~149 um apart)",
            "electrodes": "376 electrodes, 268 independent voltage sources, 1 RF drive, "
                          "280-pin CPGA (Sec. II.A); per-zone electrode counts not stated",
            "rf": "~200 V, 42 MHz; ions 70 um from the surface (Sec. II.A, Fig. 2c)",
            "operated_qubits": "32 (Abstract); four batches of 8 in the DG zones, the UG "
                               "zones and each storage region (Sec. II.E)",
            "two_qubit_parallelism": "up to four 2Q gates per round (Table I caption): four "
                                     "2Q beam pairs for four gate zones (Sec. II.C)",
            "measured": "2Q 1.84(5)e-3 (Abstract; Table II 18.3(5)e-4), 1Q 2.5(3)e-5, "
                        "SPAM 1.6(1)e-3, transport 1Q RB 2.2(3)e-4 (Table II)",
            "times": "none stated for any primitive; this document's times are unit times",
            "control": (f"Gate and auxiliary regions have independently driven electrodes "
                        f"(268 sources for 376 electrodes, {PAPER} Sec. II.A) and the paper "
                        f"states no one-waveform-per-cycle rule, so control.model is 'direct' "
                        f"(R22 off). The one broadcast constraint it does state -- the conveyor "
                        f"wells are tied to three signals {{a,b,c}} (Sec. II.A) -- is "
                        f"control.channels, which R4d judges: loaded conveyor wells move "
                        f"together, the same way."),
            "heating": "not stated; no heating block",
            "design_notes": "Reproduce/moses2023/DESIGN.md",
        },
        "zone_types": {
            "dg": {"capacity": 2, "gate": True, "spam": True, "cool": True,
                   "note": f"DG gate zone. gate/spam: 'only the DG zones are used for quantum "
                           f"operations (gating, state preparation, and measurement)' ({PAPER} "
                           f"Sec. II.A). capacity 2 qubits: a 2Q gate runs on 'two YB pairs "
                           f"combined into a single four-ion crystal YBBY' (Sec. II.E, Fig. 2e) "
                           f"and a batch of 8 fills the 4 DG zones (Sec. II.E). cool: Doppler "
                           f"sheet beams cover the trap (Sec. II.E, Fig. 2d); the resolved "
                           f"sideband cooling applied here before every 2Q round is NOT "
                           f"expressible as a zone capability."},
            "ug": {"capacity": 2, "gate": False, "spam": False, "cool": True,
                   "note": f"UG gate zone: 'we use both rows for ion rearrangement (physical "
                           f"swaps), but only the DG zones are used for quantum operations' "
                           f"({PAPER} Sec. II.A); 'used for sorting but not quantum operations' "
                           f"(Fig. 2d caption). capacity 2: a batch of 8 in the 4 UG zones "
                           f"(Sec. II.E). The PMT array could detect here (Sec. II.D) but does "
                           f"not, so spam is false."},
            "aux": {"capacity": 2, "gate": False, "spam": False, "cool": True,
                    "note": f"auxiliary region (light grey, Fig. 2d; {PAPER} Sec. II.A). NOT "
                            f"stated: how many (5 per rail read from Fig. 2d) and capacity "
                            f"(2 assumed: Fig. 2d parks one pair of qubits per block, and 'Ions "
                            f"in the UG zones are transported to the nearby auxiliary zones', "
                            f"Sec. II.E). No gating, no SPAM."},
            "conveyor": {"capacity": 1, "gate": False, "spam": False, "cool": True,
                         "note": f"conveyor-belt storage well: '{{a,b,c,...}} ... can support 20 "
                                 f"wells on each side (one for every three electrodes)' "
                                 f"({PAPER} Sec. II.A). capacity 1 qubit (one YB pair per well) "
                                 f"is read from Fig. 2h, not stated in the text."},
            "load": {"capacity": 1, "gate": False, "spam": False, "cool": True,
                     "photoionization": True,
                     "note": f"load hole 'in the middle of the left-side conveyor belt region "
                             f"... surrounded by electrodes with independent signals' ({PAPER} "
                             f"Sec. II.A); photo-ionization beams (Sec. II.B). NOT stated: its "
                             f"capacity (1 YB pair assumed)."},
        },
        "primitives": {
            "shuttle_segment": unit_curve("a linear shift (Sec. II.E)"),
            "split": unit_curve("split (Sec. II.E 'split/combine')"),
            "merge": unit_curve("combine (Sec. II.E 'split/combine')"),
            "ion_swap": unit_curve("a physical swap (Sec. II.E)"),
            "ms_gate": {"us": 1.0, **unit,
                        "fidelity_at_n0": 0.99816,
                        "fidelity_source": f"{PAPER} Abstract: 2Q infidelity 1.84(5)e-3 "
                                           "(Table II: 18.3(5)e-4; Fig. 4: 1.83(5)e-3)",
                        "gate": "U_ZZ(theta) = exp(-i theta ZZ/2): an MS gate between 1Q "
                                "wrapper pulses (Sec. II.C)",
                        "angle_law_not_expressible": "eps(theta) = (2.9(2) theta/pi + "
                                                     "0.46(6)) x 1e-3 (Fig. 3 caption)"},
            "1q_gate": {"us": 1.0, **unit, "fidelity": 0.999975,
                        "fidelity_source": f"{PAPER} Abstract: 2.5(3)e-5"},
            "measure": {"us": 1.0, **unit, "fidelity": 0.9984,
                        "fidelity_source": f"{PAPER} Abstract: SPAM 1.6(1)e-3 (preparation "
                                           "and measurement together, not measurement alone)"},
            "reset": {"us": 1.0, **unit},
            "cool": {"us": 1.0, **unit, "scope": "global", "broadcastable": True,
                     "removes_quanta": "all",
                     "physics": f"Doppler 'sheet beams' cover the whole trap during transport "
                                f"({PAPER} Sec. II.E, Fig. 2d). The resolved sideband cooling "
                                f"of the Ba+ ions in the DG zones 'before 2Q gating operations' "
                                f"(Sec. II.E) is a second, zone-scoped step the language cannot "
                                f"state."},
        },
        "species": {
            "qubit": "171Yb+", "coolant": "138Ba+", "sympathetic": True,
            "coolant_fraction": 0.5,
            "source": f"{PAPER} Sec. II.B: '32 171Yb+-138Ba+ [YB] pairs in a deterministic "
                      "orientation'; one coolant per qubit, so half the ions",
            "unit_of_capacity": "every capacity in this document counts qubits = YB pairs",
            "T1": "'finite T1 time of several minutes' (Sec. II.E): no number, so no T_coh_s",
        },
        "control": {
            "model": "direct",
            "classes": {"extra": [
                {"id": "shuttle", "type": "shift", "orbit": "any",
                 "note": "linear shift of one qubit (YB pair) between neighbouring zones or "
                         "wells: Sec. II.E 'linear shifts'. The id is the name our compiler "
                         "emits for a single-ion move."},
                {"id": "batch_shift_ccw", "type": "shift", "orbit": "L0", "delta": 1,
                 "note": "Sec. II.E: 'batch shift, which shuttles batches of ions collectively "
                         "to different regions of the trap'; one step along the loop"},
                {"id": "batch_shift_cw", "type": "shift", "orbit": "L0", "delta": -1,
                 "note": "the same, the other way round the loop"},
            ]},
            "channels": {
                "grouping": "explicit",
                "switch_per_site": False,
                "explicit": explicit,
                "note": ("conveyor.a/b/c: 'equally spaced and sized electrodes tied together in "
                         "a repeating fashion ({a,b,c,a,b,c,...}). This requires only three "
                         "total voltage signals' (Sec. II.A); read literally as three signals "
                         "for BOTH regions (the text is ambiguous). Every other site is "
                         "independently driven. The channel count this map gives is NOT H2's "
                         "268 sources: the paper does not state electrodes per zone."),
            },
            "wiring": {"note": "376 electrodes, 268 independent voltage sources, 1 RF drive; "
                               "280-pin ceramic pin grid array (Sec. II.A)"},
            "optical": {"addressing": "zone_beams",
                        "note": "four 2Q Raman beam pairs operate the four DG zones; 1Q gates "
                                "use co-propagating beams; measurement beams per DG zone; a "
                                "PMT array sees all 8 gate zones (Sec. II.C-D)"},
        },
        "geometry": {
            "generator": "explicit",
            "params": {},
            "nodes": nodes,
            "segments": segments,
            "loops": [{"id": "L0", "kind": "ring", "closed": True, "nodes": order,
                       "note": "the race track: 'a linear trap with periodic boundary "
                               "conditions' (Abstract), one RF null (Sec. II.A)"}],
        },
    }


def main() -> int:
    import sys

    sys.path.insert(0, str(HERE.parents[1]))
    from qccd.api import Machine

    doc = build()
    OUT.write_text(json.dumps(doc, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    m = Machine.load(OUT)
    arch = m.arch
    dev = arch.device
    viol = m.violations()
    print(f"{OUT.name}: {len(dev.nodes)} sites, {len(dev.segments)} segments, "
          f"{len(dev.junction_nodes)} junctions, capacity {dev.total_capacity()} qubits, "
          f"well pitch {WELL_PITCH * PITCH_UM:.1f} um")
    print("degree histogram", dev.summary().get("degree_histogram"))
    print("violations:", [str(v) for v in viol] or "none")
    return 1 if viol else 0


if __name__ == "__main__":
    raise SystemExit(main())
