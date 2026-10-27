"""Every result of the BB codesign study, as clickable studio pages.

The findings under `Codesign/findings/q06*` are prose over numbers.  This puts each
number where a person can click on it: one `qccd studio` page per design, opened on the
verified programme with its circuit beside it, so every instruction the study counted can
be stepped, its cost read, its rule verdict seen, and the QASM statement it discharges
found.  An index page carries the study's claims, and every claim links to the page and
the STEP where it is visible.

    python Codesign/scripts/bb_studio.py -o BBResults/studio

Nothing here is a second renderer.  The pages are `qccd.viz.render.render_html` over the
same `(arch, programme, replay)` the campaign scored, the circuit join is
`qccd.ir.source_map` (which refuses if the certificate disagrees), and every number on the
index is measured on the programme the page shows (`_numbers`; until 2026-10-23 they were read
from the study's data files, which mixed two compiles on some rows).  What this adds is
a small overlay on each emitted page -- a deep link `#step=N`, and the study's notes on
the steps it drew conclusions from -- injected AFTER rendering, so the studio's own
sources are untouched.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime
import hashlib
import html
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.compile.decode import insert_decodes  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.ir.source_map import build as build_source  # noqa: E402
from qccd.ir.tsir import TSIR, iter_pairs  # noqa: E402
from qccd.verify import verify  # noqa: E402
from qccd.viz.render import render_html  # noqa: E402
from qccd.repo_paths import resolve, to_repo  # noqa: E402

BUILD = ROOT / "Compiler" / "build"
DATA = ROOT / "Codesign" / "data"
BRIDGE = ROOT / "Compiler" / "bridge"
#: every compiled entry's R10 verdict, keyed by a digest of the bytes the check read
R10_CACHE = DATA / "bb_r10.json"
STRIDE_QASM = BUILD / "q06" / "ring144_24v_a24_stride.qasm"

#: what every q06g ring entry now says about its control plane (2026-10-23)
SWITCHED = ("It has per-site two-way switches, the deck's: each site follows the motion bank or "
            "the hold bank of the 32 broadcast channels, so it pays 1 select bit per site per "
            "transport cycle. The switchless declaration it was compiled for was dropped on "
            "2026-10-23: R4d's hold clause refuses it, because a switchless channel cannot hold a "
            "docked ion while it moves the loop.")
#: what every direct-wired entry compiled under control.model simd_classes says (2026-10-23)
ONE_WAVEFORM = ("It runs under the one-waveform model (control.model simd_classes: one motion a "
                "cycle, the R22 split) on direct wiring, so its time is an upper bound for a direct "
                "machine.")

# --------------------------------------------------------------------------- catalogue

#: One entry per design the study drew a conclusion from.  `prefix` names a compiled
#: programme (`<prefix>.cooled.tsir.json`, `.qcert.json`, and `.qasm` beside it unless
#: `qasm` overrides); an entry with no prefix is a device the compiler could not serve,
#: and gets a page of the device alone with the refusal as its lede.
#: `"draft": True` keeps an entry off the board (pages, manifest, index) unless `--drafts`.
#: Publishing one is a decision, not a side effect of a rebuild: the eight entries 13 and
#: 20-29 were drafts until the user published them on 2026-10-20.
#: `"recompiled"`: the programme on disk is not the study's but a later compile (the study's
#: is beside it as `<prefix>.pre_order.*`); its facts are the compile's, and every number
#: that depends on the programme is computed from it rather than read from the study's data.
CATALOG: tuple[dict, ...] = (
    {"key": "01_baseline_stride", "family": "ring + docks",
     "title": "ring144_24v, stride layout", "gif": "01_baseline_stride.gif",
     "arch": ROOT / "arch" / "ring144_24v.arch.json",
     "prefix": BUILD / "q06" / "ring144_24v_a24_stride",
     "recompiled": {"compiler": "f88754a2fa71", "date": "2026-10-20", "batches": 824,
                    "hops": 7586, "rotations": 824},
     "claim": "the study's starting point: the rotation pass laying riders out by stride, "
              "recompiled on 2026-10-20 with a compiler that keeps every qubit's gates in "
              "order: 824 batches of 1.05 contacts each, which is why it takes 1.42 s. The "
              "study's 546 batches and 520 ms came from a compiler that reordered a qubit's "
              "gates, which the checker now refuses."},
    {"key": "02_shipped_ring_code_aware", "family": "ring + docks",
     "title": "ring144_24v, code-aware schedule", "gif": "02_shipped_ring_code_aware.gif",
     "arch": ROOT / "arch" / "ring144_24v.arch.json",
     "prefix": BUILD / "q06" / "ring144_24v_a24_bridge_structured_xz",
     "claim": "the same device and circuit with the code-aware placer and binder: "
              "55 batches instead of the stride layout's 824, 70 ms instead of 1.42 s. "
              "The compilation axis alone is 20x."},
    # 03-08: the q06g rings, each on the SWITCHED twin of the device it was compiled for
    # (`<name>.arch.json` beside `<name>_noswitch.arch.json`: the same file with
    # control.channels.switch_per_site true, byte-identical to the generator's own
    # `rings/<name>.arch.json`).  The programmes are the ones compiled for the switchless
    # declaration, unchanged; since 2026-10-23 R4d's hold clause refuses them there.
    {"key": "03_h3_v24_192", "family": "ring + docks",
     "title": "ring(95, 3, 24), 192 slots, 24 ancillas, per-site switches", "gif": "03_h3_v24_192.gif",
     "arch": BUILD / "q06g" / "ring192_h3_v24_o2.arch.json",
     "prefix": BUILD / "q06g" / "ring192_h3_v24_o2_noswitch_a24_bridge_structured_xz_dockall_transport-fastest",
     "claim": "24 docks on a loop of height 3: 66 batches, 87.5 ms. " + SWITCHED + " Not buildable "
              "by today's DRC: 102 of 216 sites get fewer than the 3 control-electrode pairs the "
              "technology requires (the 24 docks, the 24 rail sites they hang off, and 54 more); "
              "it was the study's first buildable answer before that rule."},
    {"key": "04_h4_v24_216", "family": "ring + docks",
     "title": "ring(106, 4, 24), 216 slots, 24 ancillas, per-site switches", "gif": "04_h4_v24_216.gif",
     "arch": BUILD / "q06g" / "ring216_h4_v24_o2.arch.json",
     "prefix": BUILD / "q06g" / "ring216_h4_v24_o2_noswitch_a24_bridge_structured_xz_dockall_transport-fastest",
     "claim": "height 4: 55 batches, 70.8 ms. " + SWITCHED + " The docks and the rail sites they "
              "hang off get their 3 control-electrode pairs here; 54 plain rail sites do not, so "
              "today's DRC still fails it -- the fewest short sites of any ring on the board."},
    {"key": "05_h3_v36_288_ANSWER", "family": "ring + docks", "answer": True,
     "title": "ring(143, 3, 36), 288 slots, 36 ancillas, per-site switches -- the recommended design",
     "gif": "05_h3_v36_288_ANSWER.gif",
     "arch": BUILD / "q06g" / "ring288_h3_v36_o2.arch.json",
     "prefix": BUILD / "q06g" / "ring288_h3_v36_o2_noswitch_a36_bridge_structured_xz_dockall_transport-fastest",
     "claim": "46 batches, 341 hops, 55.6 ms, R10 passed: the proved Lean checker and the tableau. "
              "Its rotation schedule is within 10.7% of its lower bound; its batches are 52% full. "
              + SWITCHED + " That is 324 select bits a cycle, 64.8 Mbit/s at the 5 us rotation "
              "step; the 32 broadcast DACs are exactly the motion bank and the hold bank (58 with "
              "the shims). The shims cost time: 17.1 ms a round through the 1:100 demultiplexer "
              "(WISE, once per reconfiguration), 2.7 ms at 1:16 with 162 shim DACs. Not buildable "
              "by today's DRC: 150 of 324 sites are short of 3 control-electrode pairs (the 36 "
              "docks, the 36 rail sites they hang off, and 78 more)."},
    {"key": "06_h5_v36_360", "family": "ring + docks",
     "title": "ring(177, 5, 36), 360 slots, 36 ancillas, per-site switches", "gif": "06_h5_v36_360.gif",
     "arch": BUILD / "q06g" / "ring360_h5_v36_o2.arch.json",
     "prefix": BUILD / "q06g" / "ring360_h5_v36_o2_noswitch_a36_bridge_structured_xz_dockall_transport-fastest",
     "claim": "the same 36 ancillas on a taller, longer loop: the same clock (56.0 ms) for twice "
              "the die (70.7 mm2). " + SWITCHED + " Height 5 gives the docks their electrode "
              "pairs; 78 rail sites still fall short, so today's DRC fails it."},
    {"key": "07_h3_v48_384", "family": "ring + docks",
     "title": "ring(191, 3, 48), 384 slots, 48 ancillas, per-site switches", "gif": "07_h3_v48_384.gif",
     "arch": BUILD / "q06g" / "ring384_h3_v48_o2.arch.json",
     "prefix": BUILD / "q06g" / "ring384_h3_v48_o2_noswitch_a48_bridge_structured_xz_dockall_transport-fastest",
     "claim": "48 docks lose to 36: 48 does not divide the placement's period, and the "
              "schedule sits 43% above its rotation bound where 36 sits at 11%. " + SWITCHED
              + " Not buildable by today's DRC: 198 of 432 sites are short of control-electrode pairs."},
    {"key": "08_h4_v72_360_unbuildable", "family": "ring + docks",
     "title": "ring(178, 4, 72), 360 slots, 72 ancillas, per-site switches -- NOT buildable",
     "gif": "08_h4_v72_360_unbuildable.gif",
     "arch": BUILD / "q06g" / "ring360_h4_v72_o0.arch.json",
     "prefix": BUILD / "q06g" / "ring360_h4_v72_o0_noswitch_a72_bridge_structured_xz_dockall_transport-fastest",
     "claim": "the model's optimum: 19 batches, 24.9 ms, 2.2x faster than 05. " + SWITCHED
              + " Not buildable: today's DRC finds 146 of 432 sites short of 3 control-electrode "
              "pairs (the study's builder found 36 minimum-gap violations instead; today's finds "
              "none of those)."},
    # 09-11: real direct machines -- the q06h lattices with control.model "direct", compiled
    # by the released qccdc with the q06h recipe (a bare `compile`, insert_cooling, check_tsir
    # --model corrected).  Until 2026-10-23 these rows showed the one-waveform programme
    # (control.model simd_classes) on a direct-wired device, which is not a direct comparator;
    # that programme is entry 12's.
    {"key": "09_grid13x13", "family": "lattice",
     "title": "grid13x13, 312 traps, direct wiring and direct control",
     "arch": BUILD / "q06h" / "grid13x13_direct.arch.json",
     "prefix": BUILD / "q06h" / "grid13x13_direct_bb", "qasm": STRIDE_QASM,
     "compiled": {"compiler": "b4a003dbb4ae", "date": "2026-10-23", "note": "control.model direct"},
     "claim": "a real direct machine: grid13x13 with control.model 'direct', so a cycle may play "
              "several waveforms. It does in most: 930 of 1,319 transport cycles move ions in two "
              "to eight directions at once (up to 10 distinct motions, 11 waveforms with the "
              "hold), and the round takes 405.6 ms at 4.8 ions per instruction -- against 866.1 ms "
              "for the same lattice told to play one waveform a cycle (12). Every site has channels "
              "of its own: no select bits, and 12,480 DACs, over the task's 128."},
    {"key": "10_grid11x11", "family": "lattice",
     "title": "grid11x11, 220 traps, direct control, as drawn -- no control electrodes survive",
     "arch": BUILD / "q06h" / "grid11x11_direct.arch.json",
     "prefix": BUILD / "q06h" / "grid11x11_direct_bb", "qasm": STRIDE_QASM,
     "compiled": {"compiler": "b4a003dbb4ae", "date": "2026-10-23", "note": "control.model direct"},
     "claim": "the fastest lattice, as a direct machine: 376.7 ms at 4.95 ions per instruction, "
              "883 of 1,208 cycles moving ions in two to eight directions at once. And a die with "
              "zero control electrodes: every trap is 112.5 um from two junctions whose keep-outs "
              "reach 180 um. 8,800 DACs."},
    {"key": "11_grid11x11_s2_buildable", "family": "lattice",
     "title": "grid11x11 at junction pitch 2, direct control -- the same programme",
     "arch": BUILD / "q06h" / "grid11x11_s2_direct.arch.json",
     "prefix": BUILD / "q06h" / "grid11x11_direct_bb", "qasm": STRIDE_QASM,
     "compiled": {"compiler": "b4a003dbb4ae", "date": "2026-10-23",
                  "note": "control.model direct, on grid11x11_direct; compiled on this device it is the same instruction list"},
     "claim": "node-for-node the same graph as 10, pulled apart so its traps clear the keep-outs: "
              "the same programme (compiling it here gives the identical instruction list), the "
              "same 376.7 ms and 25 rules, 35.6 mm2 instead of 9.4. Still not buildable by today's "
              "DRC: 110 traps get no control-electrode pair and the other 110 get 2 of the 3 required."},
    {"key": "12_grid13x13_broadcast", "family": "lattice",
     "title": "grid13x13 with the 32-channel broadcast block", "gif": "09_grid13x13.gif",
     "arch": BUILD / "q06h" / "grid13x13_bcast.arch.json",
     "prefix": BUILD / "q06h" / "grid13x13_bcast_bb", "qasm": STRIDE_QASM,
     "claim": "the one-waveform programme (one motion a cycle, the R22 split: 3,007 transport "
              "cycles at 2.68 ions each) on the deck's broadcast block with per-site switches: 57 "
              "DACs and 866.1 ms, against the direct machine's 405.6 ms at 12,480 DACs (09). The 32 "
              "channels are two banks of 16, a motion and the hold, chosen by 1 select bit per site "
              "per cycle, so the direct programme does not run here: it fails R4 (up to 10 motions "
              "against 2 channel sets) and R22. Its shims cost 47.4 ms a round at 1:100, 7.6 ms at 1:16."},
    {"key": "13_dual144_c24_R2", "family": "two loops",
     "title": "dual144, two concentric loops joined at 24 couplings -- fails R2",
     "arch": BUILD / "q06f" / "dual144_c24.arch.json",
     "prefix": BUILD / "q06f" / "dual144_c24_a24_bridge_structured_xz",
     "recompiled": {"compiler": "f88754a2fa71", "date": "2026-10-20", "batches": 183,
                    "hops": 335, "rotations": 182},
     "claim": "compiles, then fails R2: a coupled ancilla site is degree 3, and a junction "
              "may hold one ion. It cannot both house an ancilla and receive a rider."},
    # q06j: the code's own torus, and the planar grid at the same ancilla count
    {"key": "20_torus12_own_a72", "family": "torus",
     "title": "torus 12x12, 288 traps, 72 ancillas -- the code's own shape",
     "arch": BUILD / "q06j" / "torus12x12.arch.json",
     "prefix": BUILD / "q06j" / "torus12_own_a72_bb", "qasm": BUILD / "q06j" / "bb144_a72.qasm",
     "q06j": "torus12_own_a72",
     "claim": "a periodic lattice, every row and column a closed loop, with one ancilla per "
              "check pair: 516.4 ms at 3.79 ions per instruction, against the planar grid's 648.1 "
              "at 72 ancillas (22) and 869.9 at 24 (23). The wrap is worth 1.25x, the ancillas "
              "1.34x. " + ONE_WAVEFORM + " The study's 97 ms was this programme before the R22 "
              "split, several motions a cycle."},
    {"key": "21_torus12_code_a72", "family": "torus",
     "title": "torus 12x12 with the code-aware placement, 72 ancillas",
     "arch": BUILD / "q06j" / "torus12x12.arch.json",
     "prefix": BUILD / "q06j" / "torus12_code_a72_bb", "qasm": BUILD / "q06j" / "bb144_a72.qasm",
     "q06j": "torus12_code_a72",
     "claim": "the code's cell (i, j) laid at columns i, rows 2j and 2j+1 via --init-placement: "
              "462.8 ms at 4.54 ions per instruction, 1.12x the compiler's own placement (20, "
              "516.4 ms). " + ONE_WAVEFORM + " Before the R22 split the two placements tied "
              "(the study's 99 and 97 ms)."},
    {"key": "22_planar12_own_a72", "family": "lattice",
     "title": "grid12x12, 264 traps, 72 ancillas",
     "arch": BUILD / "q06j" / "grid12x12.arch.json",
     "prefix": BUILD / "q06j" / "planar12_own_a72_bb", "qasm": BUILD / "q06j" / "bb144_a72.qasm",
     "q06j": "planar12_own_a72",
     "claim": "the same planar grid as q06h's lattices with 72 ancillas instead of 24: 648.1 ms "
              "instead of 869.9 (23), 1.34x. The lattice family's 24-ancilla numbers were "
              "ancilla-starved. " + ONE_WAVEFORM},
    {"key": "23_planar12_own_a24", "family": "lattice",
     "title": "grid12x12, 264 traps, 24 ancillas",
     "arch": BUILD / "q06j" / "grid12x12.arch.json",
     "prefix": BUILD / "q06j" / "planar12_own_a24_bb", "qasm": BUILD / "q06j" / "bb144_a24.qasm",
     "q06j": "planar12_own_a24",
     "claim": "the control for the ancilla count: the q06h tier, 869.9 ms at 2.62 ions per "
              "instruction -- level with grid13x13 under the same model (12, 866.1 ms). " + ONE_WAVEFORM},
    # q06k: the torus drawn as a chip -- concentric rings with spokes
    {"key": "24_cylinder12_own_a72", "family": "torus",
     "title": "cylinder 12x12 -- twelve concentric rings joined by spokes, 72 ancillas",
     "arch": BUILD / "q06j" / "cylinder12x12.arch.json",
     "prefix": BUILD / "q06j" / "cylinder12_own_a72_bb", "qasm": BUILD / "q06j" / "bb144_a72.qasm",
     "q06j": "cylinder12_own_a72",
     "claim": "the torus drawn as a chip with no crossings: each row a closed ring, the columns "
              "radial spokes. Under one waveform a cycle it is the slowest of the lattices that "
              "pass every rule: 1,254.2 ms at 1.08 ions per instruction, against the torus's "
              "516.4 and the planar grid's 648.1. " + ONE_WAVEFORM + " The study's 106 ms, which "
              "kept most of the torus's speed, was the programme before the R22 split."},
    {"key": "25_annulus12_own_a72", "family": "torus",
     "title": "annulus 12x12 -- the cylinder plus the y-wrap through 120 crossings, 72 ancillas",
     "arch": BUILD / "q06j" / "annulus12x12.arch.json",
     "prefix": BUILD / "q06j" / "annulus12_own_a72_bb", "qasm": BUILD / "q06j" / "bb144_a72.qasm",
     "q06j": "annulus12_own_a72",
     "claim": "a true torus on a plane needs 120 crossings, and every one lands on a ring "
              "wire, so the x-hops pay for the y-wrap: 1,489.5 ms, the slowest compiled lattice "
              "on the board. The wrap is not worth its crossings. " + ONE_WAVEFORM},
    # q06l: weird graphs -- random, rewired, and the Tanner graph as a device
    {"key": "26_rewired_p0.1", "family": "random graph",
     "title": "torus 12x12 with 10% of its wires rewired at random, 72 ancillas",
     "arch": BUILD / "q06l" / "rewired_p0.1.arch.json",
     "prefix": BUILD / "q06l" / "rewired_p0.1_bb", "qasm": BUILD / "q06j" / "bb144_a72.qasm",
     "q06l": "rewired_p0.1",
     "claim": "28 random swaps take the torus from diameter 12 to 8, and under one waveform a "
              "cycle the shorter distances do not pay: the round goes from 516.4 to 1,125.5 ms, "
              "one ion per transport instruction. Not drawable: every shortcut would be "
              "crossings on a chip. " + ONE_WAVEFORM + " Before the R22 split it was 82 ms."},
    {"key": "27_regular4", "family": "random graph",
     "title": "a random 4-regular graph on 144 junctions, 72 ancillas",
     "arch": BUILD / "q06l" / "regular4.arch.json",
     "prefix": BUILD / "q06l" / "regular4_bb", "qasm": BUILD / "q06j" / "bb144_a72.qasm",
     "q06l": "regular4",
     "claim": "the torus's degrees with none of its geometry: diameter 6, 1,036.1 ms at one ion "
              "per transport instruction. An expander beats every lattice on distance and has "
              "hundreds of crossings in practice. " + ONE_WAVEFORM},
    {"key": "28_tanner_own", "family": "random graph",
     "title": "the code's Tanner graph as the machine -- every contact one hop, 72 ancillas",
     "arch": BUILD / "q06l" / "tanner_own.arch.json",
     "prefix": BUILD / "q06l" / "tanner_own_bb", "qasm": BUILD / "q06j" / "bb144_a72.qasm",
     "q06l": "tanner_own",
     "claim": "a hub per check and per data qubit, a contact trap on each of the 864 Tanner "
              "edges: no device makes the contacts shorter. 856.5 ms at one ion per transport "
              "instruction -- under the random graphs (1,036 and 1,126 ms), over the torus "
              "(516.4). " + ONE_WAVEFORM + " Before the R22 split it was 79.5 ms."},
    {"key": "29_geometric", "family": "random graph",
     "title": "a random geometric graph: 139 points joined within a radius, mean degree 6",
     "arch": BUILD / "q06l" / "geometric.arch.json",
     "refusal": "unroutable: q145 cannot reach T150 from T316 within 40 cycles. Dead ends "
                "(four degree-1 vertices), diameter 25.",
     "claim": "random AND local, the way a die is: the worst of both. Locality without "
              "structure does not route."},
    # devices the compiler could not serve: the page shows the shape and carries the
    # refusal, because the shape IS the finding
    {"key": "14_racetrack240", "family": "junction-free loop",
     "title": "racetrack240: a closed loop, no junctions, no docks",
     "arch": BUILD / "q06i" / "racetrack240.arch.json",
     "refusal": "rotation does not apply: the device has no closed loop with docks; then "
                "unroutable: q150 cannot reach S76 from S176 within 248 cycles. 70% occupancy.",
     "claim": "the cheapest transport in the study, unusable: rigid rotation preserves "
              "cyclic order so two riders never meet, and a loop has one route."},
    {"key": "15_racetrack480", "family": "junction-free loop",
     "title": "racetrack480: the same loop at 35% occupancy",
     "arch": BUILD / "q06i" / "racetrack480.arch.json",
     "refusal": "did not finish in 30 minutes. 312 of 480 slots empty.",
     "claim": "free space is not the constraint: a lattice routes at 76% occupancy, this "
              "loop cannot at 35%."},
    {"key": "16_chain400", "family": "line",
     "title": "chain400: a line of 400 traps",
     "arch": BUILD / "q06i" / "chain400.arch.json",
     "refusal": "unroutable: q156 cannot reach C202 from C233 within 806 cycles. 42% occupancy.",
     "claim": "a line has no closed loop for rotation and one route for the router."},
    {"key": "17_dual240_c0", "family": "two loops",
     "title": "dual240 with no couplings",
     "arch": BUILD / "q06i" / "dual240_c0.arch.json",
     "refusal": "unroutable: q155 cannot reach AT16 from AT45 within 248 cycles.",
     "claim": "uncoupled, the data loop and the ancilla loop can never interact."},
    {"key": "18_ladder144_r12", "family": "rails + highways",
     "title": "ladder144: 12 rails and highways, 576 sites",
     "arch": BUILD / "q06h" / "ladder144_r12.arch.json",
     "refusal": "did not finish in 20 minutes. 29% occupancy.",
     "claim": "open rails are not a closed loop, so rotation does not apply; the router "
              "did not finish."},
    {"key": "19_grid9x9", "family": "lattice",
     "title": "grid9x9: 144 traps for 168 qubits",
     "arch": BUILD / "q06h" / "grid9x9.arch.json",
     "refusal": "unroutable: 117% occupancy, no free destination. The device q01b's "
                "'8 of 9 cannot compile' was true of.",
     "claim": "genuinely too small -- the one lattice verdict that was right."},
)

#: the name a dot wears on the plot and in the table: short, and distinct between the
#: designs that share a device (grid13x13 direct against broadcast, grid11x11 as drawn
#: against pitch 2, the shipped ring's two schedules)
SHORT = {
    "01_baseline_stride": "ring144_24v stride", "02_shipped_ring_code_aware": "ring144_24v code-aware",
    "03_h3_v24_192": "ring(95,3,24)", "04_h4_v24_216": "ring(106,4,24)",
    "05_h3_v36_288_ANSWER": "ring(143,3,36)", "06_h5_v36_360": "ring(177,5,36)",
    "07_h3_v48_384": "ring(191,3,48)", "08_h4_v72_360_unbuildable": "ring(178,4,72)",
    "09_grid13x13": "grid13x13 direct", "10_grid11x11": "grid11x11 direct, as drawn",
    "11_grid11x11_s2_buildable": "grid11x11 direct, pitch 2", "12_grid13x13_broadcast": "grid13x13 broadcast",
    "13_dual144_c24_R2": "dual144 c24", "14_racetrack240": "racetrack240",
    "15_racetrack480": "racetrack480", "16_chain400": "chain400", "17_dual240_c0": "dual240",
    "18_ladder144_r12": "ladder144", "19_grid9x9": "grid9x9",
    "20_torus12_own_a72": "torus12x12 a72", "21_torus12_code_a72": "torus12x12 code-placed a72",
    "22_planar12_own_a72": "grid12x12 a72", "23_planar12_own_a24": "grid12x12 a24",
    "24_cylinder12_own_a72": "cylinder12x12 a72", "25_annulus12_own_a72": "annulus12x12 a72",
    "26_rewired_p0.1": "torus 10% rewired", "27_regular4": "random 4-regular",
    "28_tanner_own": "Tanner graph", "29_geometric": "random geometric",
}

FAMILY_NOTE = {
    "ring + docks": ("rigid rotation", "a closed loop AND gate-capable docks off it"),
    "lattice": ("individual routing", "route diversity: a blocked path has an alternative"),
    "junction-free loop": ("neither", "a loop without docks: rotation cannot bring two ions together, and the router has one route"),
    "line": ("neither", "no closed loop, one route"),
    "two loops": ("neither", "uncoupled they never meet; coupled, the coupling is a junction and R2 refuses it"),
    "rails + highways": ("neither", "the rails are open, and the router did not finish"),
    "torus": ("individual routing", "route diversity plus the wrap: every row and column is a closed loop, so the code's offsets are short in both directions"),
    "random graph": ("individual routing", "route diversity and a short diameter -- and no drawing on a plane"),
}


# --------------------------------------------------------------------------- numbers

def _numbers(entry: dict) -> dict:
    """Every figure of an entry, MEASURED on the programme its page shows and the device it
    names -- nothing read from the study's data files.

    Until 2026-10-23 this read the study's data files (`q06i_profiles.json`, `q06g_E3.json`,
    `q06j_torus.json`, `q06l_random.json`), which were written when each programme was first
    compiled.  Several programmes were rewritten since (the R22 split, the pricing pass, the
    recompiles), so a row could carry instructions and ions per instruction from September's
    compile beside a time replayed from today's: entries 09-12 did.  Now one function
    measures one (device, programme) pair, the way `arch_profile.profile` and
    `small_codes.py` do, plus what the programme asks of the control hardware
    (`qccd.cost.waveforms` and `hardware_report(census=...)`).  `build_one` then replaces
    the two times with the replay of the page's own programme (decodes inserted), which is
    the same number."""
    out: dict = {}
    if entry.get("prefix") and Path(str(entry["prefix"]) + ".cooled.tsir.json").is_file():
        out.update(_measure(entry))
    if entry.get("recompiled"):
        rc = entry["recompiled"]
        out["recompiled"] = f"recompiled {rc['date']} with qccdc {rc['compiler']}"
    if entry.get("compiled"):
        c = entry["compiled"]
        out["compiled"] = f"compiled {c['date']} with qccdc {c['compiler']}" + (f" ({c['note']})" if c.get("note") else "")
    if entry.get("q06l"):
        # the graph's diameter is a property of the device the study generated, not of any
        # compile, so it is still read from the study's record of that graph
        g = {r["key"]: r for r in json.loads((DATA / "q06l_random.json").read_text(encoding="utf-8"))}
        d = ((g.get(entry["q06l"]) or {}).get("graph") or {}).get("diameter")
        if d is not None:
            out["diameter"] = d
    v = r10_verdict(entry)
    out["r10"] = v["r10"] if v else None
    if v:
        out["r10_verdict"] = v["verdict"]
    return out


#: the demultiplexer order every board row is also priced at, besides the one it declares:
#: WISE's trade (Sec. V B 3) at an order a 1:16 analog switch delivers
DEMUX_ALT = 16


def _measure(entry: dict) -> dict:
    """The numbers of one compiled entry, from `<prefix>.cooled.tsir.json` on `entry["arch"]`.

    Time, error and parallelism as `arch_profile.profile` computes them (`q06_campaign.score`
    on both cost tables, `broadcast_use`, the gate parallelism); the device's DACs, area,
    electrodes per trap and today's design-rule verdict as `q06_campaign.buildability`
    computes them; batches and rigid hops from the replay's own per-class ledger, so they are
    this programme's and not a compile log's; the rotation's distance from its lower bound
    only where the schedule that fed this compile is beside it AND agrees with the programme
    on batches and hops.  Then the control figures: the waveform census of the programme
    and `hardware_report(arch, census=...)` -- the waveforms played at once, the DACs that
    takes, the select bits a switched broadcast plane loads per cycle and at what rate, and
    the shim demultiplexer's charging time at the declared order and at `DEMUX_ALT`."""
    from arch_profile import gate_parallelism
    from q06_campaign import broadcast_use, buildability, score
    from qccd.cost.hardware import hardware_report
    from qccd.cost.waveforms import waveform_census

    arch_p = Path(entry["arch"])
    arch = load(str(arch_p))
    prog = TSIR.load(str(entry["prefix"]) + ".cooled.tsir.json")
    s = score(arch, prog, table="qccdsim_jones")
    bc = broadcast_use(arch, prog)
    moving = sum(us for k, us in s["us_by_class"].items() if k not in ("gate", "measure", "reset", "cool"))
    pairs = gate_parallelism(prog)
    out: dict = {
        "T_jones": round(s["T_round_ms"], 3),
        "p_eff_vs_threshold": round(s["p_eff_vs_threshold"], 3),
        "transport_instructions": bc["transport_instructions"],
        "ions_per_instruction": round(bc["ions_per_instruction"], 2),
        "us_per_ion": round(moving / bc["ion_moves"], 2) if bc["ion_moves"] else None,
        "pairs_per_gate": pairs["pairs_per_gate"] or None,
        "cool_share_pct": round(100.0 * s["clock_ms"]["cooling"] / s["T_round_ms"], 1) if s["T_round_ms"] else None,
    }
    try:
        b = buildability(arch_p)
    except Exception as exc:   # a graph with no drawing (the q06l random graphs): no metal to judge
        out["dacs"] = hardware_report(arch).dacs
        out["drc_error"] = f"{type(exc).__name__}: {exc}"[:160]
    else:
        out.update(dacs=b["dacs"], area_mm2=b["area_mm2"], pads_per_trap=b["pads_per_trap"],
                   buildable=b["buildable"], drc_clean=b["drc_clean"], drc=b["drc"])
    try:
        out["T_transport"] = round(score(arch, prog, table="transport_excitation")["T_round_ms"], 3)
    except Exception:      # a device whose curves lack the second table
        pass
    if s["batches"]:
        out["batches"], out["hops"] = s["batches"], s["rotate_hops"]
        sched = Path(str(entry["prefix"]) + ".sched.schedule.json")
        st = json.loads(sched.read_text(encoding="utf-8")).get("stats", {}) if sched.is_file() else {}
        if st.get("batches") == s["batches"] and st.get("hops") == s["rotate_hops"]:
            if st.get("revolutions") and st.get("bound_revolutions"):
                out["rotation_gap_pct"] = round(100 * (st["revolutions"] / st["bound_revolutions"] - 1), 1)
            if st.get("batch_sizes"):
                out["batch_sizes"] = {int(k): v for k, v in st["batch_sizes"].items()}
    # ---- what the programme asks of the control hardware
    c = waveform_census(prog, arch)
    hw = hardware_report(arch, census=c)
    alt = next(r for r in hw.demux_tradeoff if r["M"] == DEMUX_ALT)    # its default orders hold 16
    out.update({
        "dacs_census": hw.dacs,
        "dacs_dynamic_required": hw.dacs_dynamic_required,
        "waveforms_max": c.max_concurrent,
        "motions_max": c.max_signatures,
        "transport_cycles": c.n_transport_cycles,
        "cycles_multi_motion": sum(v for k, v in c.signature_histogram.items() if k >= 2),
        "cycles_with_holds": c.n_cycles_with_holds,
        "switch_per_site": bool(arch.control_plane.switch_per_site),
        "select_bits_per_site": hw.select_bits_per_site,
        "select_bits_per_cycle": hw.select_bits_per_cycle,
        "select_rate_mbit_s": round(hw.select_rate_mbit_s or 0.0, 1),
        "reconfigurations": c.n_reconfigurations,
        "gate_layers": c.n_gate_layers,
        "demux_order": hw.demux_order,
        "shim_charge_us_per_layer": hw.shim_charge_us_per_layer,
        "shim_charge_ms": round(hw.shim_charge_total_us / 1000.0, 3),
        "shim_charge_every_layer_ms": round(hw.shim_charge_total_us_every_layer / 1000.0, 3),
        "demux_alt": DEMUX_ALT,
        "shim_charge_m16_ms": round(alt["charge_us_reconfigurations"] / 1000.0, 3),
        "shim_charge_every_layer_m16_ms": round(alt["charge_us_every_layer"] / 1000.0, 3),
        "shim_dacs_m16": alt["shim_dacs"],
        "shim_dacs_m16_wise": alt["shim_dacs_wise"],
    })
    return out


_RELEASE_OF: dict = {}


def release_of(out_dir: Path) -> str | None:
    """The task release whose board is written to `out_dir` (its `seed`, tasks/<id>/task.json)."""
    key = str(Path(out_dir).resolve())
    if not _RELEASE_OF:
        for f in sorted((ROOT / "tasks").glob("*/task.json")):
            t = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(t.get("seed"), str):
                _RELEASE_OF[str((ROOT / t["seed"]).resolve())] = t.get("release") or f"{t['id']}@{t.get('version', 1)}"
    return _RELEASE_OF.get(key)


def over_budget(arch, release_id: str | None) -> list[str]:
    """What the official grader refuses this device for (DEVICE.OVER_BUDGET, #15): the
    release's budget against `hardware_report` with every number the device declares about
    its wiring counted at no less than the physics -- the grader's own call
    (qccd/workspace/evaluator.py), so a board ranks nothing the grader would refuse."""
    if not release_id or not arch or not Path(arch).exists():
        return []
    from qccd.cost.hardware import WIRING_FLOORS, hardware_report
    from qccd.workspace.tasks import find_release
    budget = find_release(release_id).physics().get("budget") or {}
    return list(hardware_report(load(str(arch)), floors=WIRING_FLOORS, budget=budget).over_budget)


def _digest(paths) -> str:
    h = hashlib.sha256()
    for p in paths:
        p = Path(p)
        h.update(p.name.encode() + b"\0")
        h.update(p.read_bytes() if p.is_file() else b"-")
    return h.hexdigest()[:16]


def r10_verdict(entry: dict) -> dict | None:
    """R10 for a compiled entry, DECIDED rather than typed -- as `small_codes.py` decides it:
    the proved checker on the certificate's checker input, then the semantic check against the
    source circuit (`check_cert.py --qcheck`).  The catalogue used to carry `"r10": True` by
    hand, and `01_baseline_stride` kept saying "passed" after the checker learnt the strict
    order its certificate breaks.  Cached in `R10_CACHE` under a digest of everything the check
    reads -- programme, certificate, circuit, device, the two bridge scripts and the checker
    binary -- so a new checker or a recompiled entry is checked again, and nothing else is.
    None for an entry with no compiled programme."""
    prefix = entry.get("prefix")
    if not prefix or not Path(str(prefix) + ".qcert.json").is_file():
        return None
    from qccd.workspace.evaluator import Toolchain
    qcheck = Toolchain.discover().qcheck
    qasm = Path(entry.get("qasm") or str(prefix) + ".qasm")
    arch = Path(entry["arch"])
    key = _digest([Path(str(prefix) + ".tsir.json"), Path(str(prefix) + ".qcert.json"), qasm, arch,
                   BRIDGE / "check_cert.py", BRIDGE / "mk_qcheck_input.py", BRIDGE / "export_arch.py"]
                  + ([qcheck] if qcheck else []))
    try:
        cache = json.loads(R10_CACHE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    hit = cache.get(entry["key"])
    if hit and hit.get("key") == key:
        return hit
    if qcheck is None:
        return {"key": key, "r10": None, "verdict": "not run: no checker built here"}
    with tempfile.TemporaryDirectory() as td:
        expanded, qc = Path(td) / "arch.expanded.json", Path(td) / "prog.qcheck.json"

        def run(*cmd, timeout=1800):
            p = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, timeout=timeout)
            return p.returncode, p.stdout + p.stderr
        rc, log = run(sys.executable, BRIDGE / "export_arch.py", arch, "-o", expanded)
        if rc == 0:
            rc, log = run(sys.executable, BRIDGE / "mk_qcheck_input.py", prefix, "--arch", expanded, "-o", qc)
        why = ""
        if rc == 0:
            rc, log = run(sys.executable, BRIDGE / "check_cert.py", prefix, "--qasm", qasm, "--arch", arch,
                          "--qcheck", qc)
            if "R10 passed" not in log and qc.is_file():
                # check_cert says only that the proved checker refused; its first reason is the news
                _, qlog = run(qcheck, qc)
                why = next((ln.strip()[2:] for ln in qlog.splitlines() if ln.strip().startswith("- ")), "")
    verdict = next((ln.strip() for ln in log.splitlines() if "-> R10" in ln), "") or log.strip()[-200:]
    if why:
        verdict += ": " + why
    res = {"key": key, "r10": "R10 passed" in log, "verdict": verdict[:240],
           "checked": datetime.date.today().isoformat()}
    cache[entry["key"]] = res
    R10_CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    return res


# --------------------------------------------------------------------------- notes

def _marks(entry: dict, prog: TSIR, arch, report) -> list[dict]:
    """The steps the study's conclusions were read off, each as (frame, label, note).

    A frame index is the instruction's position, because `build_view_model` emits one
    frame per instruction in order (the pages here are well under `max_frames`).
    """
    dev = arch.device
    ins = prog.instructions
    marks: list[dict] = []

    def add(i, label, note, kind="note"):
        # one note per step: a second claim at the same instruction joins the first
        for m in marks:
            if m["frame"] == i:
                m["label"] += f" / {label}"
                m["note"] += f"  ALSO: {note}"
                if kind == "fail" or (kind == "warn" and m["kind"] == "note"):
                    m["kind"] = kind
                return
        marks.append({"frame": i, "step": i + 1, "id": ins[i].id, "label": label,
                      "note": note, "kind": kind})

    kinds = [(i, x.type, (x.meta or {}).get("kind")) for i, x in enumerate(ins)]
    # the round's split, from the page's own replay: what share cooling and transport take
    res = getattr(report, "result", None)
    by_type = dict(getattr(res, "us_by_type", {}) or {})
    total_us = float(getattr(res, "total_us", 0.0) or 0.0)

    def share(kind: str) -> str:
        return f"{100.0 * by_type.get(kind, 0.0) / total_us:.0f}%" if total_us else "a share"
    plane = arch.control_plane
    n_contacts = sum(1 for x in ins if x.type == "gate" for _ in iter_pairs(x)) or 1
    # a batch's size is its CONTACT count -- the pairs of the contact gate that follows
    # the dock -- not the dock's rider count: under `--dock-all` every rider beside a
    # dock pulls in with the batch whether or not it has a contact this turn
    docks = []
    for i, t, k in kinds:
        if t == "simd" and k == "dock":
            gate = next((j for j in range(i + 1, min(i + 8, len(ins)))
                         if ins[j].type == "gate" and (ins[j].meta or {}).get("kind") == "contact"), None)
            n = sum(1 for _ in iter_pairs(ins[gate])) if gate is not None else len(ins[i].participants or ())
            docks.append((i, n))
    n_batches = len(docks)

    # violations first: they are what a debugger opens the page for
    seen_rules = set()
    for v in report.rules.violations:
        if v.severity != "error" or v.rule in seen_rules:
            continue
        seen_rules.add(v.rule)
        pos = next((i for i, x in enumerate(ins) if x.id == v.instr_id), None)
        if pos is not None:
            add(pos, f"{v.rule} fails here", v.message, kind="fail")
        elif v.instr_id < 0 and ins:
            # an architecture-level rule (R11, R19-R21) names no instruction: the
            # device itself is what fails, so the note stands on the first frame
            add(0, f"{v.rule} fails on this device", v.message, kind="fail")

    if n_batches:
        rot = next((i for i, t, k in kinds if t == "simd" and k == "rotate"), None)
        if rot is not None:
            init = next((x for x in ins if x.type == "init"), None)
            on_loop = {n for lp in dev.loops.values() if lp.closed for n in lp.nodes}
            riders = sum(1 for s in ((init.placement or {}).values() if init else ()) if s in on_loop)
            hops = (ins[rot].meta or {}).get("hops")
            hold = ""
            if plane.declared and plane.grouping != "direct" and plane.switch_per_site:
                # the hold is a waveform too (R4d's hold clause, 2026-10-23)
                hold = (" Every loaded site the rotation does not move -- a docked ancilla -- "
                        "HOLDS meanwhile: its two-way switch selects the hold bank instead of the "
                        "motion bank, 1 select bit per site per cycle. A switchless channel cannot "
                        "hold a docked ion while it moves the loop (R4d).")
            add(rot, "rigid rotation",
                f"one loop_shift template turns the whole loop{f' by {hops} hops' if hops else ''}: "
                f"every rider moves with one waveform, {riders} ions here. This is the broadcast "
                f"primitive the ring is built around.{hold}")
        sizes = Counter(n for _, n in docks)
        first = docks[0][0]
        add(first, f"batch 1 of {n_batches}",
            f"a dock: {docks[0][1]} riders pull into their spurs at once. Each batch pays dock "
            f"+ undock + one forced cool (about 725 us) whatever its size; mean fill here is "
            f"{n_contacts / n_batches:.1f} contacts of {max(sizes) if sizes else '?'} possible.")
        small = [(i, n) for i, n in docks if n == 1]
        if small:
            add(small[0][0], f"a 1-contact batch ({len(small)} of {n_batches})",
                f"one rider docks alone, and the batch still pays its full overhead: "
                f"{len(small)} such batches cost about {len(small) * 0.73:.1f} ms for "
                f"{100 * len(small) / n_contacts:.1f}% of the contacts.", kind="warn")
        big = max(docks, key=lambda t: t[1])
        if big[1] > 1 and big[0] != first:
            add(big[0], f"the fullest batch ({big[1]} contacts)",
                "every ancilla in use at once: this is what the lockstep placement is "
                "engineered to make happen, and the per-batch overhead is amortised best here.")
        cool = next((i for i, t, k in kinds if t == "cool" and k == "cool"), None)
        if cool is not None:
            add(cool, "forced cool",
                "300 us, once per batch: an ion that has crossed a junction carries 3 quanta "
                "against a 1-quantum gate budget, so the cool is structural, not a policy. "
                f"Cooling is {share('cool')} of the round.")
        undock = next((i for i, t, k in kinds if t == "simd" and k == "undock"), None)
        if undock is not None:
            add(undock, "undock", "the riders leave their spurs. Entering the degree-3 rail node "
                "is charged as a junction crossing -- the price of the only mechanism a loop has.")
        contact = next((i for i, t, k in kinds if t == "gate" and k == "contact"), None)
        if contact is not None:
            n_pairs = sum(1 for _ in iter_pairs(ins[contact]))
            add(contact, f"contact gates ({n_pairs} pairs at once)",
                "the two-qubit gates of one batch, all in one instruction; each pair sits in "
                "its own dock. The Circuit pane shows which QASM statements this discharges.")
    else:
        # the individual router: no batches, no rotation
        shuttles = [(i, len(x.participants or ())) for i, x in enumerate(ins)
                    if x.type == "simd" and x.participants]
        if shuttles:
            big = max(shuttles, key=lambda t: t[1])
            add(big[0], f"the busiest shuttle ({big[1]} ions)",
                f"the most ions this router ever moves at once, against a ring rotation's 144. "
                f"The mean is {sum(n for _, n in shuttles) / len(shuttles):.1f}. Every hop is "
                f"the `shuttle` class, whatever its direction.")
            typical = next((i for i, n in shuttles if n <= 5), None)
            if typical is not None:
                add(typical, "a typical shuttle", "a handful of ions, each on its own route: the "
                    f"{share('simd')} of this round that is transport is made of instructions "
                    "like this one.")
            # the instruction wanting the most distinct directions
            def ndirs(x):
                d = set()
                for p in x.participants:
                    a, b = dev.nodes[p.src].pos, dev.nodes[p.dst].pos
                    d.add((round(b[0] - a[0], 6), round(b[1] - a[1], 6)))
                return len(d)
            most = max(shuttles, key=lambda t: ndirs(ins[t[0]]))
            k = ndirs(ins[most[0]])
            if k > 1 and (arch.control or {}).get("model") == "direct":
                # Only a direct-control device plays several motions in one cycle: under
                # simd_classes R22 allows one, and a second lab-frame "direction" there is a
                # periodic wrap (the torus) of the same motion.  One motion takes every
                # electrode role of a cell, so k motions need k channel SETS (R4d's count since
                # 2026-10-23), not k channels.
                add(most[0], f"{k} directions at once",
                    f"ions moving {k} different ways in one instruction. A ring rotation asks for "
                    f"one. Every site of a direct machine has channels of its own, so this is "
                    f"legal here. The 32-channel broadcast block is two banks of 16, a motion and "
                    f"the hold, so it plays one of these per cycle: R4d counts the banks, R22 the "
                    f"cycles.")
        cool = next((i for i, t, k in kinds if t == "cool"), None)
        if cool is not None:
            add(cool, "cool", f"the lattice cools too; {share('cool')} of its round.")
    marks.sort(key=lambda m: m["frame"])
    return marks


# --------------------------------------------------------------------------- overlay

OVERLAY_CSS = """
#bbnotes{position:fixed;right:12px;bottom:12px;z-index:60;max-width:380px;
 background:#141a29;color:#e6ecf7;border:1px solid #3a4a72;border-radius:10px;
 box-shadow:0 8px 28px rgba(0,0,0,.45);font:12.5px/1.45 ui-sans-serif,system-ui,sans-serif}
#bbnotes[data-open="0"]{display:none}
#bbnotes .bbhead{display:flex;align-items:center;gap:8px;padding:8px 10px;border-bottom:1px solid #263353}
#bbnotes .bbhead b{flex:1}
#bbnotes .bbx{background:none;border:0;color:#8b9ab5;font-size:16px;cursor:pointer;padding:0 4px;line-height:1}
#bbnotes .bbx:hover{color:#fff}
#bbnotes .bbbody{padding:8px 10px;max-height:44vh;overflow:auto}
#bbnotes .bbnow{padding:8px 10px;border-radius:8px;background:#0e1320;margin-bottom:8px;border-left:3px solid #5b7cfa}
#bbnotes .bbnow[data-kind="fail"]{border-left-color:#ff6b6e}
#bbnotes .bbnow[data-kind="warn"]{border-left-color:#f2b84b}
#bbnotes .bbnow .t{color:#8b9ab5;font-size:11px}
#bbnotes ul{list-style:none;margin:0;padding:0}
#bbnotes li{padding:4px 6px;border-radius:6px;cursor:pointer;display:flex;gap:8px}
#bbnotes li:hover{background:#1b2436}
#bbnotes li[data-here="1"]{background:#22304f}
#bbnotes li .n{color:#8b9ab5;font-variant-numeric:tabular-nums;min-width:52px}
#bbnotes li[data-kind="fail"] .n{color:#ff6b6e}
#bbnotes li[data-kind="warn"] .n{color:#f2b84b}
#bbnotes .bbnav{display:flex;gap:6px;margin-top:8px}
#bbnotes button.bbn{background:#1b2436;color:#e6ecf7;border:1px solid #3a4a72;border-radius:6px;padding:3px 9px;cursor:pointer;font:inherit}
#bbnotes a{color:#9db4ff}
#bbpill{position:fixed;right:12px;bottom:12px;z-index:59;background:#fff;color:#1c2a4a;border:1px solid #cfceca;
 border-radius:999px;padding:5px 12px;font:12.5px ui-sans-serif,system-ui,sans-serif;cursor:pointer;box-shadow:0 2px 8px rgba(0,0,0,.12)}
#bbpill[data-open="1"]{display:none}
/* the executing instruction, top-left of the stage, always */
#canvas{position:relative}
#bbnow{position:absolute;left:8px;top:8px;z-index:4;max-width:min(52%,560px);pointer-events:none;
 background:rgba(255,255,255,.94);border:1px solid #cfceca;border-radius:8px;padding:6px 10px;
 font:12px/1.45 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;color:#0b0b0b;
 box-shadow:0 2px 8px rgba(0,0,0,.10);max-height:7.6em;overflow:hidden}
#bbnow .k{color:#52514e;font:11px ui-sans-serif,system-ui,sans-serif;letter-spacing:.02em;text-transform:uppercase;margin-bottom:2px}
#bbnow .mut{color:#52514e}
/* hardware program and source circuit SIDE BY SIDE, each at full width.  The two
   blocks sit in a ROW inside the column-flex pane, so every listing keeps the bounded
   height its virtual scroller needs (a wrapped flex line lets the list grow to its
   content -- 28,000 px -- and nothing scrolls) */
#bbrow{display:flex;flex-direction:row;gap:10px;flex:1 1 0;min-height:0;min-width:0}
#bbrow .pblock{flex:1 1 0;min-width:0;min-height:0;display:flex;flex-direction:column}
#bbrow .pblock .wrapl{flex:1 1 auto;min-height:0;display:flex;flex-direction:column}
#bbrow .pblock .lst{flex:1 1 auto;min-height:120px;overflow:auto}
.progcol[data-bbview="both"]{width:min(800px,52vw)!important}
/* the executing row, loud; the grey "now" boxes gone (the stage card carries that) */
#paneP .now{display:none!important}
#paneP .ln.cur,#paneP .ql.cur{background:#fff1c2!important;box-shadow:inset 4px 0 0 #2a78d6!important;font-weight:600}
.progcol[data-bbview="hw"],.progcol[data-bbview="gates"]{width:min(470px,34vw)!important}
.bbclose{background:none;border:1px solid #cfceca;border-radius:6px;color:#52514e;cursor:pointer;font:12px ui-sans-serif,system-ui,sans-serif;padding:1px 7px;margin-left:6px}
.bbclose:hover{color:#0b0b0b;border-color:#8a8985}
#bbreopen{display:flex;gap:6px;flex:0 0 auto;flex-wrap:wrap;align-items:flex-start;align-self:flex-start}
/* draggable panel boundaries: thin strips laid over the row grid at each panel's edge */
#row{position:relative}
.bbgrip{position:absolute;z-index:30;background:transparent;touch-action:none}
.bbgrip[data-axis="x"]{width:8px;cursor:col-resize;top:0;bottom:0;margin-left:-4px}
.bbgrip[data-axis="y"]{height:8px;cursor:row-resize;left:0;right:0;margin-top:-4px}
.bbgrip:hover,.bbgrip[data-drag="1"]{background:rgba(42,120,214,.25)}
.bbgrip[data-axis="x"]:after{content:"";position:absolute;left:3px;top:50%;width:2px;height:28px;margin-top:-14px;border-radius:2px;background:#b9c9ee}
.bbgrip[data-axis="y"]:after{content:"";position:absolute;top:3px;left:50%;height:2px;width:28px;margin-left:-14px;border-radius:2px;background:#b9c9ee}
/* the qubit-label toggle lives in the transport toolbar */
#bblab{display:inline-flex;align-items:center;gap:4px;font:12px ui-sans-serif,system-ui,sans-serif;color:#0b0b0b;margin-left:6px;white-space:nowrap}
#bbqlabels text{font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:600;fill:#0b0b0b;paint-order:stroke;stroke:#fff;stroke-width:0.35em;stroke-linejoin:round;pointer-events:none}
/* the tools bar between the head and the row: everything that used to crowd the rail,
   plus a search box over every control, hint and lesson on the page */
#bbtools{position:relative;display:flex;align-items:center;gap:6px;flex-wrap:wrap;padding:5px 10px;border-bottom:1px solid #e6e5e1;background:#fbfbfa;font:12.5px ui-sans-serif,system-ui,sans-serif;z-index:70}
#bbtools .bbtb{background:#fff;border:1px solid #cfceca;border-radius:6px;padding:4px 10px;cursor:pointer;font:inherit;color:#0b0b0b}
#bbtools .bbtb[aria-expanded="true"]{background:#eef3fb;border-color:#2a78d6;color:#1c2a4a}
#bbtools .bbsw{position:relative;margin-left:auto;display:flex;align-items:center;gap:6px}
#bbsearch{width:min(360px,40vw);padding:5px 9px;border:1px solid #cfceca;border-radius:6px;font:inherit}
#bbsearch:focus{outline:2px solid #2a78d6;outline-offset:0}
#bbsres{position:absolute;right:0;top:100%;margin-top:4px;width:min(520px,80vw);max-height:60vh;overflow:auto;background:#fff;border:1px solid #cfceca;border-radius:8px;box-shadow:0 8px 28px rgba(0,0,0,.14);z-index:90;display:none}
#bbsres[data-open="1"]{display:block}
#bbsres .r{padding:7px 10px;border-bottom:1px solid #efeeeb;cursor:pointer}
#bbsres .r:hover,#bbsres .r[data-sel="1"]{background:#eef3fb}
#bbsres .r b{font-size:13px}
#bbsres .r .w{color:#52514e;font-size:11.5px;margin-left:6px}
#bbsres .r .d{color:#52514e;font-size:12px;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#bbsres .none{padding:8px 10px;color:#8a8985}
.bbpop{position:absolute;top:100%;margin-top:4px;z-index:85;width:400px;max-width:90vw;max-height:72vh;overflow:auto;background:#fff;border:1px solid #cfceca;border-radius:10px;box-shadow:0 10px 32px rgba(0,0,0,.16);padding:10px 12px;display:none}
.bbpop[data-open="1"]{display:block}
.bbpop .bbph{display:flex;align-items:center;margin-bottom:6px;font-weight:600}
.bbpop .bbph .bbx{margin-left:auto;background:none;border:0;font-size:16px;cursor:pointer;color:#52514e}
.bbpop details.palfold>summary{display:none}
.bbpop details.palfold{display:block}
.bbpop .mut.bbempty{color:#8a8985;font-size:12px}
.bbflash{outline:3px solid #2a78d6!important;outline-offset:2px;transition:outline-color .8s}
/* instruction hints and the cheat sheet */
#bbtip{position:fixed;z-index:95;max-width:460px;background:#fff;border:1px solid #cfceca;border-radius:8px;
 box-shadow:0 8px 28px rgba(0,0,0,.16);padding:8px 11px;font:12.5px/1.45 ui-sans-serif,system-ui,sans-serif;color:#0b0b0b;pointer-events:none;display:none}
#bbtip[data-open="1"]{display:block}
#bbtip .op{display:inline-block;font:11px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:700;color:#fff;border-radius:4px;padding:1px 6px;margin-right:6px;vertical-align:1px}
#bbtip b.n{font-size:13px}
#bbtip .what{margin:4px 0 2px}
#bbtip code{display:block;background:#f3f4f7;border-radius:5px;padding:4px 7px;margin:4px 0;font:11.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap}
#bbtip .this{color:#52514e;font-size:12px;border-top:1px solid #efeeeb;margin-top:5px;padding-top:4px}
#bbtip .this .mut{color:#8a8985}
.bbpop[data-pop="cheat"]{width:620px}
.bbcheat h4{margin:12px 0 4px;font-size:13px;color:#1c2a4a}
.bbcheat .row{display:grid;grid-template-columns:78px 1fr;gap:8px;padding:7px 0;border-bottom:1px solid #efeeeb;align-items:start}
.bbcheat .row .op{font:11px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-weight:700;color:#fff;border-radius:4px;padding:2px 6px;text-align:center}
.bbcheat .row b{display:block;font-size:13px}
.bbcheat .row .what{color:#0b0b0b;font-size:12.5px}
.bbcheat .row code{display:block;background:#f3f4f7;border-radius:5px;padding:3px 7px;margin:3px 0;font:11.5px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;white-space:pre-wrap}
.bbcheat .row .cost{color:#52514e;font-size:12px}
.bbcheat p{font-size:12.5px;color:#52514e;margin:4px 0}
.bbcheat table.cols{width:100%;border-collapse:collapse;font-size:12px}
.bbcheat table.cols td{padding:3px 4px;border-bottom:1px solid #efeeeb;vertical-align:top}
.bbcheat table.cols td:first-child{font-family:ui-monospace,Menlo,Consolas,monospace;white-space:nowrap;color:#1c2a4a}
/* the rail keeps only the standard components: no scrolling needed */
#rail{overflow:visible}
#palElements h4{margin-top:0}
#bbreopen:empty{display:none}
#bbreopen button{background:#eef3fb;border:1px solid #b9c9ee;border-radius:999px;color:#1c2a4a;cursor:pointer;font:12px ui-sans-serif,system-ui,sans-serif;padding:2px 10px}
"""

OVERLAY_JS = r"""
(function(){
  // The study's notes on this programme, a deep link, the executing instruction on the
  // stage, and the two programme listings side by side.  Everything goes through the
  // page's own functions (`seek`, `nowHTML`, `setProgView`, `progPinToggle`), so nothing
  // here shows a state the page itself would not show.
  var M = window.BB_MARKS || [];
  var box = document.getElementById('bbnotes'), pill = document.getElementById('bbpill');
  function $(id){ return document.getElementById(id); }
  function esc(s){ return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;'); }
  function cur(){ try { return (typeof frame === 'number') ? frame : 0; } catch(e){ return 0; } }
  function go(i){ try { if(typeof seek === 'function') seek(i); } catch(e){} }
  function byId(k){ for(var i=0;i<M.length;i++) if(String(M[i].id)===String(k)) return M[i].frame; return null; }
  function store(k, v){ try { localStorage.setItem(k, v); } catch(e){} }
  function read(k){ try { return localStorage.getItem(k); } catch(e){ return null; } }

  // ---- (1) the notes: closed unless asked for -----------------------------------
  function notesOpen(on){
    if(box) box.setAttribute('data-open', on ? '1' : '0');
    if(pill) pill.setAttribute('data-open', on ? '1' : '0');
    store('bb.notes', on ? '1' : '0');
  }
  if(box){
    var x = $('bbx'); if(x) x.onclick = function(){ notesOpen(false); };
    if(pill) pill.onclick = function(){ notesOpen(true); };
    notesOpen(read('bb.notes') === '1');
  }
  function renderNotes(){
    if(!box) return;
    var f = cur(), here = null, next = null, prev = null;
    for(var i=0;i<M.length;i++){
      if(M[i].frame===f) here = M[i];
      if(M[i].frame<f) prev = M[i];
      if(M[i].frame>f && !next) next = M[i];
    }
    var now = $('bbnow2');
    if(now){
      if(here){ now.setAttribute('data-kind', here.kind||'note');
        now.innerHTML = '<div class="t">step '+(f+1)+' &middot; instruction '+esc(here.id)+'</div><b>'+esc(here.label)+'</b><div>'+esc(here.note)+'</div>'; }
      else { now.setAttribute('data-kind','note');
        now.innerHTML = '<div class="t">step '+(f+1)+'</div><div>'+(next?'next note at step '+(next.frame+1)+' &middot; '+esc(next.label):'no further notes')+'</div>'; }
    }
    var ul = $('bblist');
    if(ul){ ul.innerHTML = M.map(function(m){
      return '<li data-kind="'+(m.kind||'note')+'" data-here="'+(m.frame===f?1:0)+'" data-f="'+m.frame+'"><span class="n">step '+(m.frame+1)+'</span><span>'+esc(m.label)+'</span></li>'; }).join('');
      var lis = ul.children || [];
      for(var j=0;j<lis.length;j++)(function(li){ if(li.addEventListener) li.addEventListener('click', function(){ go(parseInt(li.getAttribute('data-f'),10)); }); })(lis[j]);
    }
    var p = $('bbprev'), n = $('bbnext');
    if(p) p.onclick = function(){ if(prev) go(prev.frame); };
    if(n) n.onclick = function(){ if(next) go(next.frame); };
  }

  // ---- (3) the executing instruction, top-left of the stage ------------------------
  var canvas = $('canvas'), nowEl = null;
  if(canvas && canvas.appendChild){ nowEl = document.createElement('div'); nowEl.id = 'bbnow'; canvas.appendChild(nowEl); }
  function renderNow(){
    if(!nowEl) return;
    try {
      var f = cur(), fr = (typeof P !== 'undefined' && P.frames) ? P.frames[f] : null, N = (typeof P !== 'undefined' && P.frames) ? P.frames.length : 0;
      var body = (fr && typeof nowHTML === 'function') ? nowHTML(fr) : '';
      nowEl.innerHTML = '<div class="k">executing &middot; step '+(f+1)+' / '+N+'</div>' + body;
    } catch(e){ nowEl.innerHTML = ''; }
  }

  // ---- (2) hardware programme and source circuit side by side ---------------------
  var pc = $('progcol'), pp = $('paneP'), hw = $('pHw'), pq = $('paneQ');
  function bbView(){ try { return (typeof PROG_VIEW !== 'undefined') ? PROG_VIEW : 'hw'; } catch(e){ return 'hw'; } }
  function paintView(){
    var v = bbView();
    if(pc){ pc.setAttribute('data-bbview', v); var wp0 = parseInt(read('bb.w.prog') || '', 10); if(wp0) pc.style.setProperty('width', wp0 + 'px', 'important'); }
    var ro = $('bbreopen');
    if(ro){ ro.innerHTML = '';
      if(v !== 'both' && (typeof SRC === 'undefined' || SRC)){
        var b = document.createElement('button');
        b.textContent = v === 'hw' ? '\u25B8 show the source circuit beside it' : '\u25B8 show the hardware program beside it';
        b.onclick = function(){ if(typeof setProgView === 'function') setProgView('both'); };
        ro.appendChild(b);
      }
    }
  }
  function closeBtn(el, view, title){
    if(!el) return;
    var h = el.querySelector ? el.querySelector('.ph') : null; if(!h) return;
    var b = document.createElement('button'); b.className = 'bbclose'; b.textContent = '\u00D7'; b.title = title;
    b.onclick = function(){ if(typeof setProgView === 'function') setProgView(view); };
    h.appendChild(b);
  }
  if(pp && hw && pq && typeof setProgView === 'function'){
    // the row that holds both listings: moving the blocks keeps their ids and handlers
    var row = document.createElement('div'); row.id = 'bbrow';
    pp.insertBefore(row, hw); row.appendChild(hw); row.appendChild(pq);
    closeBtn(hw, 'gates', 'close the hardware program (the circuit stays)');
    closeBtn(pq, 'hw', 'close the source circuit (the hardware program stays)');
    var ro = document.createElement('div'); ro.id = 'bbreopen'; pp.appendChild(ro);
    // wrap the page's own view switch so the column width follows it
    var _spv = setProgView; setProgView = function(v){ var r = _spv.apply(this, arguments); try{ paintView(); }catch(e){} return r; };
    // first visit: beside the animation, both listings, side by side
    if(read('qccd.studio.progview') === null || read('bb.layout') !== '1'){
      try { if(typeof progPinToggle === 'function' && typeof PROG_COL !== 'undefined' && !PROG_COL) progPinToggle(true); } catch(e){}
      try { setProgView('both'); } catch(e){}
      store('bb.layout', '1');
    }
    try { var qv = /(?:\?|&)view=(hw|gates|both)/.exec(location.search||''); if(qv) setProgView(qv[1]); } catch(e){}
    if(false){
    } else {
      try { if(typeof progPinToggle === 'function' && typeof PROG_COL !== 'undefined' && !PROG_COL && read('qccd.studio.progpin') !== '0') progPinToggle(true); } catch(e){}
      paintView();
    }
    try { if(typeof sizeStage === 'function') sizeStage(); } catch(e){}
    var settle = function(){
      try { if(typeof sizeLists === 'function') sizeLists(); } catch(e){}
      try { if(typeof PLIST !== 'undefined' && PLIST && PLIST.follow && typeof VIEWPOS !== 'undefined' && VIEWPOS[cur()] >= 0) PLIST.scrollToRow(VIEWPOS[cur()]); } catch(e){}
      try { if(typeof syncCursor === 'function') syncCursor(true); } catch(e){}
    };
    try { setTimeout(settle, 50); setTimeout(settle, 400); } catch(e){}
  }

  // ---- (4) any speed factor: whole numbers, and every half step to 4 --------------
  // The page's transport reads its speed with parseInt, so 1.5 read as 1.  This replaces
  // the option list and the ticker with ones that take a float: below 16x every frame
  // is played and its duration is the 1x duration over the factor; from 16x up the
  // page's own frame-skipping strides are kept.
  try {
    var sel = $('speed');
    if(sel && typeof tick === 'function' && typeof hopsIn === 'function'){
      var keep = sel.value;
      var facs = [1, 1.5, 2, 2.5, 3, 3.5, 4, 6, 8, 16, 64, 256];
      sel.innerHTML = facs.map(function(k){ return '<option value="'+k+'">'+k+'x</option>'; }).join('');
      sel.value = facs.indexOf(parseFloat(keep)) >= 0 ? keep : '4';
      var base = (typeof MS !== 'undefined' && MS && MS[1]) ? MS[1] : 900;
      var strideOf = function(k){ return (typeof STRIDE !== 'undefined' && STRIDE[k]) ? STRIDE[k] : 1; };
      var durAt = function(i, k){ return Math.max(40, (base / k) * Math.min(6, Math.sqrt(hopsIn(i)))); };
      tick = function(now){
        var k = parseFloat(sel.value) || 4, stride = strideOf(k);
        if(stride > 1){
          phase = 1; frame = Math.min(lastFrame(), frame + stride); draw();
          if(frame >= lastFrame()){ stop(); return; }
          raf = requestAnimationFrame(tick); return;
        }
        var dur = durAt(frame, k);
        phase = (now - t0) / dur;
        while(phase >= 1){
          if(frame >= lastFrame()){ frame = lastFrame(); phase = 1; draw(); stop(); return; }
          frame++; t0 += dur; dur = durAt(frame, k); phase = (now - t0) / dur;
        }
        draw();
        if(raf) raf = requestAnimationFrame(tick);
      };
    }
  } catch(e){}

  // ---- (5) qubit labels over the ions ----------------------------------------------
  // The ion IS the virtual qubit in these programmes (`q45` carries q[45]), so the label
  // is the ion's name in the circuit's spelling, drawn just above its mark at a size
  // that stays readable whatever the zoom.  Off by default; the toggle is remembered.
  var svgEl = $('svg'), labG = null, LAB = {};
  function labelsOn(){ return read('bb.qlabels') === '1'; }
  function renderLabels(){
    if(!svgEl) return;
    var on = labelsOn();
    if(!on){ if(labG) labG.setAttribute('display', 'none'); return; }
    if(!labG){ labG = document.createElementNS('http://www.w3.org/2000/svg', 'g'); labG.id = 'bbqlabels'; svgEl.appendChild(labG); }
    labG.removeAttribute('display');
    var scale = (svgEl.clientWidth || 1000) / ((typeof VB !== 'undefined' && VB.w) || 1000);   // css px per user unit
    var ions = (typeof IONP !== 'undefined') ? IONP : {};
    for(var ion in ions){
      var c = ions[ion].c; if(!c) continue;
      var t = LAB[ion];
      if(!t){ t = document.createElementNS('http://www.w3.org/2000/svg', 'text'); t.setAttribute('text-anchor', 'middle');
        t.textContent = /^q\d+$/.test(ion) ? ion.replace(/^q(\d+)$/, 'q[$1]') : ion; labG.appendChild(t); LAB[ion] = t; }
      if(c.getAttribute('display') === 'none'){ t.setAttribute('display', 'none'); continue; }
      var cx = parseFloat(c.getAttribute('cx')), cy = parseFloat(c.getAttribute('cy')), r = parseFloat(c.getAttribute('r')) || 4;
      var fs = Math.max(0.95 * r, 10 / scale);
      t.setAttribute('x', cx); t.setAttribute('y', cy - 1.15 * r - 0.25 * fs); t.setAttribute('font-size', fs); t.removeAttribute('display');
    }
  }
  try {
    var bar = document.querySelector('#stagebar .ctrl') || $('stagebar');
    if(bar){
      var lab = document.createElement('label'); lab.id = 'bblab'; lab.title = 'write each ion\'s virtual qubit above it';
      var cb = document.createElement('input'); cb.type = 'checkbox'; cb.checked = labelsOn();
      cb.onchange = function(){ store('bb.qlabels', cb.checked ? '1' : '0'); try { draw(); } catch(e){} };
      lab.appendChild(cb); lab.appendChild(document.createTextNode('q labels'));
      bar.appendChild(lab);
    }
  } catch(e){}

  // ---- (6) draggable panel boundaries ---------------------------------------------
  // Each strip sits on the row grid over a panel's edge; dragging writes the panel's
  // width (or the dock's height, in the wide regime where it is a bottom row) inline,
  // which the grid's `auto` tracks follow.  Remembered per panel.
  var rowEl = $('row'), rail = $('rail'), dock = $('dock');
  var GRIPS = [];
  function mkGrip(axis, name){
    var g = document.createElement('div'); g.className = 'bbgrip'; g.setAttribute('data-axis', axis); g.setAttribute('data-grip', name);
    rowEl.appendChild(g); GRIPS.push(g); return g;
  }
  function px(v){ return Math.round(v) + 'px'; }
  function setW(el, w, important){ if(important) el.style.setProperty('width', px(w), 'important'); else el.style.width = px(w); }
  function placeGrips(){
    if(!rowEl) return;
    var R = rowEl.getBoundingClientRect(), mode = rowEl.getAttribute('data-layout');
    GRIPS.forEach(function(g){
      var name = g.getAttribute('data-grip'), el = name === 'rail' ? rail : name === 'prog' ? pc : dock;
      var vis = el && el.offsetWidth > 0 && el.getAttribute('data-collapsed') !== '1' && mode !== 'narrow';
      if(name === 'dock' && mode === 'wide'){ g.setAttribute('data-axis', 'y'); } else if(name === 'dock') g.setAttribute('data-axis', 'x');
      if(!vis){ g.style.display = 'none'; return; }
      g.style.display = '';
      var B = el.getBoundingClientRect();
      if(g.getAttribute('data-axis') === 'y'){ g.style.top = px(B.top - R.top); g.style.left = px(B.left - R.left); g.style.right = 'auto'; g.style.width = px(B.width); }
      else { var x = name === 'rail' ? B.right : B.left; g.style.left = px(x - R.left); g.style.top = px(B.top - R.top); g.style.height = px(B.height); }
    });
  }
  function afterResize(){ try { if(typeof sizeStage === 'function') sizeStage(); } catch(e){} try { if(typeof sizeLists === 'function') sizeLists(); } catch(e){} placeGrips(); }
  function wire(g){
    var name = g.getAttribute('data-grip'), el = name === 'rail' ? rail : name === 'prog' ? pc : dock;
    var startX = 0, startY = 0, w0 = 0, h0 = 0;
    g.addEventListener('pointerdown', function(ev){
      startX = ev.clientX; startY = ev.clientY; w0 = el.getBoundingClientRect().width; h0 = el.getBoundingClientRect().height;
      g.setAttribute('data-drag', '1'); try { g.setPointerCapture(ev.pointerId); } catch(e){}
      ev.preventDefault();
    });
    g.addEventListener('pointermove', function(ev){
      if(g.getAttribute('data-drag') !== '1') return;
      if(g.getAttribute('data-axis') === 'y'){
        var h = Math.max(90, Math.min(window.innerHeight * 0.8, h0 - (ev.clientY - startY)));
        rowEl.style.gridTemplateRows = 'minmax(0,1fr) ' + px(h); store('bb.w.dockh', String(Math.round(h)));
      } else {
        var d = ev.clientX - startX, w = name === 'rail' ? w0 + d : w0 - d;
        w = Math.max(120, Math.min(window.innerWidth * 0.85, w));
        setW(el, w, name === 'prog'); store('bb.w.' + name, String(Math.round(w)));
      }
      placeGrips();
    });
    var end = function(ev){ if(g.getAttribute('data-drag') !== '1') return; g.removeAttribute('data-drag'); try { g.releasePointerCapture(ev.pointerId); } catch(e){} afterResize(); };
    g.addEventListener('pointerup', end); g.addEventListener('pointercancel', end);
  }
  if(rowEl && rail && dock && pc){
    wire(mkGrip('x', 'rail')); wire(mkGrip('x', 'prog')); wire(mkGrip('x', 'dock'));
    // remembered widths
    var wr = parseInt(read('bb.w.rail') || '', 10); if(wr) setW(rail, wr, false);
    var wp = parseInt(read('bb.w.prog') || '', 10); if(wp) setW(pc, wp, true);
    var wd = parseInt(read('bb.w.dock') || '', 10); if(wd) setW(dock, wd, false);
    var hd = parseInt(read('bb.w.dockh') || '', 10); if(hd && rowEl.getAttribute('data-layout') === 'wide') rowEl.style.gridTemplateRows = 'minmax(0,1fr) ' + px(hd);
    try { window.addEventListener('resize', function(){ setTimeout(placeGrips, 60); }); } catch(e){}
    setTimeout(placeGrips, 80); setTimeout(placeGrips, 500);
    if(typeof setProgView === 'function'){ var _spv2 = setProgView; setProgView = function(v){ var r = _spv2.apply(this, arguments); setTimeout(placeGrips, 30); return r; }; }
  }

  // ---- (8) what every hardware instruction means: hover hints and a cheat sheet ------
  // The definitions are the compiler's own: the instruction types and fields are
  // `docs/tsir.md`, the native pulses R / VZ / MS and the decompositions are
  // `Compiler/ocaml/lib/gateset.ml` (proved in Lean), the SIMD classes are what this
  // device declares (`D.classes`, with the designers' notes), and the costs are the
  // physics package this page was priced with (`PH`).
  function costOf(kind){
    try {
      if(kind === 'shuttle' && PH.shuttle) return 'one hop: ' + PH.shuttle.us + ' \u00b5s, +' + PH.shuttle.quanta + ' quanta; entering a junction (degree \u2265 ' + (PH.junction_min_degree || 3) + '): ' + Object.keys(PH.junction_by_degree || {}).map(function(d){ var j = PH.junction_by_degree[d]; return 'degree ' + d + ' ' + j.us + ' \u00b5s / +' + j.quanta; }).join(', ');
      if(kind === 'splitmerge' && PH.split && PH.merge) return 'split ' + PH.split.us + ' \u00b5s / +' + PH.split.quanta + ' quanta, merge ' + PH.merge.us + ' \u00b5s / +' + PH.merge.quanta + ' quanta, plus the junction crossing of the rail node';
      if(kind === 'gate' && PH.scalars) return Object.keys(PH.scalars).filter(function(k){ return /gate|ms|beam|cool|measure|readout|reset|spam/i.test(k); }).map(function(k){ var v = PH.scalars[k]; var t = (v && typeof v === 'object') ? [(v.us != null ? v.us + ' µs' : ''), (v.quanta != null ? '+' + v.quanta + ' quanta' : '')].filter(Boolean).join(' / ') : String(v); return k.replace(/_/g, ' ') + ' = ' + t; }).join(', ');
    } catch(e){}
    return '';
  }
  var OPS = {
    INIT:  { name: 'init \u2014 place every ion', what: 'The first instruction of every programme: it declares which trap each ion starts in, with zero motional quanta. Nothing moves and nothing costs.', formal: '{ "type": "init", "id": 0, "placement": { "<ion>": "<site>", ... }, "quanta": { "<ion>": 0.0, ... } }', costk: '' },
    SHUT:  { name: 'shuttle \u2014 move ions one trap to the next', what: 'A transport cycle in which each listed ion travels its own path: from its trap, through the named segments (and any junction on the way), to the next trap. Every participant moves in the same machine cycle; the instruction is charged at its slowest rider.', formal: '{ "type": "simd", "class": "shuttle", "mode": "inter",\n  "participants": [ { "ion": "q45", "from": "S242", "to": "A242", "via": ["S242.a", "A242.b"] }, ... ] }', costk: 'shuttle' },
    'ROT.CW': { name: 'rotate_cw \u2014 rigid rotation of the whole loop', what: 'One movement template turns EVERY ion on the loop by the same number of slots with one broadcast waveform (\u00b1 hops in the listing). The riders keep their cyclic order, so a rotation never brings two of them together \u2014 that is what the docks are for. A shift by k is the unit hop driven k times.', formal: '{ "type": "simd", "class": "rotate_cw", "mode": "inter",\n  "template": { "kind": "loop_shift", "loop": "L0", "delta": 13 },\n  "holds": ["L0", ...], "meta": { "batch": 7, "kind": "rotate", "hops": 13 } }', costk: 'shuttle' },
    'ROT.CCW': { name: 'rotate_ccw \u2014 rigid rotation, the other way', what: 'The same template with a negative delta.', formal: '{ "type": "simd", "class": "rotate_ccw", "template": { "kind": "loop_shift", "loop": "L0", "delta": -1 } }', costk: 'shuttle' },
    DOCK:  { name: 'dock \u2014 riders pull into their spurs', what: 'Each listed ion leaves the rail into the dock trap beside it, where a gate can happen. It entails a split at the source and a merge at the destination, and the rail node it leaves through is degree 3, so it is charged as a junction crossing too. This is the price of the only mechanism a loop has for bringing two ions together.', formal: '{ "type": "simd", "class": "dock", "mode": "inter",\n  "participants": [ { "ion": "q18", "from": "S74", "to": "A74", "via": ["V74"] }, ... ] }', costk: 'splitmerge' },
    UNDOCK:{ name: 'undock \u2014 riders return to the rail', what: 'The reverse of dock: the ions leave their dock traps and rejoin the loop, split + merge again, through the same degree-3 node.', formal: '{ "type": "simd", "class": "undock", "mode": "inter", "participants": [ ... ] }', costk: 'splitmerge' },
    'SORT.M': { name: 'sort_merge \u2014 one step of an odd\u2013even sort', what: 'One ion of a neighbouring pair joins the other\u2019s chain (a merge); with sort_split it realises a transposition on a loop with no docks.', formal: '{ "type": "simd", "class": "sort_merge", ... }', costk: 'splitmerge' },
    'SORT.S': { name: 'sort_split \u2014 the other half of the transposition', what: 'The partner splits back out.', formal: '{ "type": "simd", "class": "sort_split", ... }', costk: 'splitmerge' },
    R:     { name: 'R(\u03b8, \u03c6) \u2014 one single-qubit laser pulse', what: 'A rotation by \u03b8 about an axis at angle \u03c6 in the equatorial plane of the Bloch sphere: the one physical single-qubit pulse this hardware has. Every single-qubit gate of the circuit (h, x, s, t, u3\u2026) compiles to exactly one R plus one VZ \u2014 the identity u3(\u03b8,\u03c6,\u03bb) = VZ(\u03c6+\u03bb)\u00b7R(\u03b8, \u03c0/2\u2212\u03bb), proved in Lean. Many R pulses on different ions are driven together in one instruction.', formal: '{ "type": "gate", "gate": "R", "mode": "intra", "ions": ["q45", ...], "params": [[\u03b8, \u03c6], ...], "sites": ["A10", ...] }', costk: 'gate' },
    VZ:    { name: 'VZ(\u03bb) \u2014 a virtual Z: a frame update, not a pulse', what: 'Advances the phase reference of every later pulse on that ion by \u03bb. No laser fires, so it takes no time and adds no heating; it is what makes a single-qubit gate cost one pulse rather than three.', formal: '{ "type": "gate", "gate": "VZ", "mode": "intra", "ions": ["q45", ...], "params": [[\u03bb], ...] }', costk: '' },
    MS:    { name: 'MS(\u03b8) \u2014 the M\u00f8lmer\u2013S\u00f8rensen entangling pulse', what: 'The only two-qubit primitive: a shared motional mode couples two ions that sit in the SAME trap, so the pair must already be co-located (R6b checks this). A circuit CX compiles to four R pulses around one MS(\u03c0/2), proved in Lean (cx_decomp). Every pair listed is driven in the same instruction, one pair per trap.', formal: '{ "type": "gate", "gate": "MS", "mode": "intra",\n  "pairs": [["q153", "q18"], ["q152", "q20"], ...], "sites": ["A10", ...] }', costk: 'gate' },
    COOL:  { name: 'cool \u2014 remove motional quanta', what: 'Sympathetic cooling of the listed ions (or every ion, when broadcast). Transport heats an ion \u2014 a junction crossing adds 3 quanta against a 1-quantum gate budget \u2014 so the cooling pass inserts one before the gates that need it (trigger = R7_gate). The first cool is the state preparation.', formal: '{ "type": "cool", "broadcast": true }   or   { "type": "cool", "ions": ["q18", ...] }', costk: 'gate' },
    RESET: { name: 'reset \u2014 prepare ancillas in |0\u27e9', what: 'Re-initialises the listed ions, mid-circuit; an ancilla is reset before every check it serves.', formal: '{ "type": "reset", "ions": ["q153", ...] }', costk: 'gate' },
    MEAS:  { name: 'measure \u2014 read the ancillas out', what: 'Projective readout of the listed ions into classical bits; the syndrome.', formal: '{ "type": "measure", "ions": ["q153", ...] }', costk: 'gate' },
    BARRIER: { name: 'barrier \u2014 an explicit synchronisation', what: 'Costs nothing; marks a point everything before must finish by.', formal: '{ "type": "barrier" }', costk: '' }
  };
  function opOf(f){
    try { if(typeof opText === 'function'){ var t = opText(f); if(OPS[t]) return t; } } catch(e){}
    if(!f) return null;
    if(f.type === 'init') return 'INIT'; if(f.type === 'cool') return 'COOL'; if(f.type === 'reset') return 'RESET'; if(f.type === 'measure') return 'MEAS'; if(f.type === 'barrier') return 'BARRIER';
    if(f.type === 'gate') return OPS[f.gate] ? f.gate : 'MS';
    if(f.type === 'simd'){ var c = String(f.cls || ''); if(c === 'rotate_cw') return 'ROT.CW'; if(c === 'rotate_ccw') return 'ROT.CCW'; if(c === 'dock') return 'DOCK'; if(c === 'undock') return 'UNDOCK'; if(c === 'sort_merge') return 'SORT.M'; if(c === 'sort_split') return 'SORT.S'; return 'SHUT'; }
    return null;
  }
  function classNote(f){
    try { var c = f && f.cls && D.classes && D.classes[f.cls]; if(!c) return '';
      var bits = []; if(c.orbit) bits.push('orbit ' + c.orbit); if(c.delta != null) bits.push('delta ' + c.delta); if(c.direction) bits.push(c.direction); if(c.entails) bits.push('entails ' + c.entails.join('+'));
      return 'declared class ' + c.id + (bits.length ? ' (' + bits.join(', ') + ')' : '') + (c.note ? ': ' + c.note : ''); } catch(e){ return ''; }
  }
  var SRC_OPS = { h: 'Hadamard on one qubit \u2014 compiles to one R pulse and one VZ.', cx: 'controlled-NOT, control then target \u2014 compiles to four R pulses around one MS(\u03c0/2) on the co-located pair.', cz: 'controlled-Z.', measure: 'read a qubit into a classical bit.', reset: 'prepare a qubit in |0\u27e9.', qreg: 'declares the quantum register: q[i] is virtual qubit i.', creg: 'declares the classical register the measurements write to.', OPENQASM: 'the circuit language version.', include: 'the standard gate library.', barrier: 'a synchronisation point.' };
  var tip = document.createElement('div'); tip.id = 'bbtip'; document.body.appendChild(tip);
  function opColourOf(f){ try { return opColour(f); } catch(e){ return '#52514e'; } }
  function showTip(html, x, y){ tip.innerHTML = html; tip.setAttribute('data-open', '1');
    var W = window.innerWidth || 1600, H = window.innerHeight || 900, w = Math.min(460, W - 24);
    tip.style.left = px(Math.min(x + 14, W - w - 8)); tip.style.top = px(y + 16 > H - 220 ? Math.max(8, y - 220) : y + 16); }
  function hideTip(){ tip.setAttribute('data-open', '0'); }
  function frameTip(f, withThis){
    var k = opOf(f); if(!k) return '';
    var o = OPS[k], cost = costOf(o.costk), cn = classNote(f);
    var h = '<span class="op" style="background:' + opColourOf(f) + '">' + esc(k) + '</span><b class="n">' + esc(o.name) + '</b>'
      + '<div class="what">' + esc(o.what) + '</div>'
      + (cn ? '<div class="what" style="color:#52514e">' + esc(cn) + '</div>' : '')
      + '<code>' + esc(o.formal) + '</code>'
      + (cost ? '<div class="what" style="color:#52514e">on this device: ' + esc(cost) + '</div>' : '');
    if(withThis && f){ try { h += '<div class="this">this instruction: ' + nowHTML(f) + '</div>'; } catch(e){} }
    return h;
  }
  function rowFrame(el){
    var r = el; while(r && r !== document.body){ if(r.classList && r.classList.contains('ln') && typeof r._i === 'number'){ return (r._i >= 0 && typeof P !== 'undefined') ? P.frames[r._i] : null; } r = r.parentElement; } return null;
  }
  function srcRow(el){ var r = el; while(r && r !== document.body){ if(r.classList && r.classList.contains('ql')) return r; r = r.parentElement; } return null; }
  try {
    var pwin = $('pWin');
    if(pwin){ pwin.addEventListener('mousemove', function(ev){ var f = rowFrame(ev.target); if(!f){ hideTip(); return; } showTip(frameTip(f, true), ev.clientX, ev.clientY); }); pwin.addEventListener('mouseleave', hideTip); }
    var qwin = $('qWin');
    if(qwin){ qwin.addEventListener('mousemove', function(ev){ var r = srcRow(ev.target); if(!r){ hideTip(); return; }
      var t = (r.textContent || '').trim(); var m = /^\d+\s*(\/\/.*|[A-Za-z_]+)/.exec(t); var w = m ? (m[1].indexOf('//') === 0 ? '//' : m[1]) : '';
      var d = w === '//' ? 'a comment.' : (SRC_OPS[w] || (w ? 'a circuit statement.' : ''));
      showTip('<b class="n">' + esc(t.slice(0, 80)) + '</b><div class="what">' + esc(d) + '</div>' + (SRC_OPS[w] ? '<div class="what" style="color:#52514e">click the statement to jump to the hardware instruction that discharges it</div>' : ''), ev.clientX, ev.clientY); });
      qwin.addEventListener('mouseleave', hideTip); }
    if(nowEl){ nowEl.style.pointerEvents = 'auto'; nowEl.addEventListener('mousemove', function(ev){ var f = (typeof P !== 'undefined') ? P.frames[cur()] : null; if(f) showTip(frameTip(f, false), ev.clientX, ev.clientY); }); nowEl.addEventListener('mouseleave', hideTip); }
  } catch(e){}
  // the cheat sheet: every instruction, the grammar, the device's classes, the pulses
  function cheatHTML(){
    var order = ['INIT', 'SHUT', 'ROT.CW', 'ROT.CCW', 'DOCK', 'UNDOCK', 'SORT.M', 'SORT.S', 'R', 'VZ', 'MS', 'COOL', 'RESET', 'MEAS', 'BARRIER'];
    var declared = {}; try { declared = D.classes || {}; } catch(e){}
    var fake = { INIT: {type:'init'}, SHUT: {type:'simd', cls:'shuttle'}, 'ROT.CW': {type:'simd', cls:'rotate_cw'}, 'ROT.CCW': {type:'simd', cls:'rotate_ccw'}, DOCK: {type:'simd', cls:'dock'}, UNDOCK: {type:'simd', cls:'undock'}, 'SORT.M': {type:'simd', cls:'sort_merge'}, 'SORT.S': {type:'simd', cls:'sort_split'}, R: {type:'gate', gate:'R'}, VZ: {type:'gate', gate:'VZ'}, MS: {type:'gate', gate:'MS'}, COOL: {type:'cool'}, RESET: {type:'reset'}, MEAS: {type:'measure'}, BARRIER: {type:'barrier'} };
    var h = '<div class="bbcheat"><p>An instruction is one concurrent machine cycle. A <i>simd</i> instruction moves many ions at once; a <i>gate</i> instruction drives many co-located pairs (or many single ions) at once. Costs, durations and heating are annotations the replay computes, not content. Hover any row of the hardware program for this card with the row\u2019s own numbers.</p>';
    h += '<h4>Instructions</h4>';
    order.forEach(function(k){ var o = OPS[k], f = fake[k], cost = costOf(o.costk), cn = classNote(f);
      h += '<div class="row" data-op="' + k + '"><span class="op" style="background:' + opColourOf(f) + '">' + esc(k) + '</span><div><b>' + esc(o.name) + '</b><div class="what">' + esc(o.what) + '</div>' + (cn ? '<div class="cost">' + esc(cn) + '</div>' : '') + '<code>' + esc(o.formal) + '</code>' + (cost ? '<div class="cost">on this device: ' + esc(cost) + '</div>' : '') + '</div></div>'; });
    h += '<h4>Fields</h4><table class="cols">'
      + '<tr><td>type</td><td>init \u00b7 simd \u00b7 gate \u00b7 measure \u00b7 reset \u00b7 cool \u00b7 barrier</td></tr>'
      + '<tr><td>id</td><td>a durable handle allocated once; the listing shows POSITION (row number), the NOW strip shows #id</td></tr>'
      + '<tr><td>mode</td><td><b>inter</b> = between traps (transport) \u00b7 <b>intra</b> = inside a trap (gates). Rule R4b: one cycle is one or the other.</td></tr>'
      + '<tr><td>class</td><td>the SIMD movement class a transport cycle uses; a device declares its classes (below) and how many may run per cycle (here ' + (function(){ try { return P.max_simd_classes || 1; } catch(e){ return 1; } })() + ')</td></tr>'
      + '<tr><td>template</td><td>loop_shift(loop, delta): one waveform moves every ion on the loop \u2014 the rigid-rotation primitive</td></tr>'
      + '<tr><td>participants</td><td>ion, from, to, via (the segments crossed): one entry per moving ion</td></tr>'
      + '<tr><td>pairs / ions / sites</td><td>what a gate acts on, and where</td></tr>'
      + '<tr><td>holds</td><td>resources reserved for the cycle (a loop, spurs)</td></tr>'
      + '<tr><td>meta</td><td>the compiler\u2019s annotations: batch, kind (rotate, dock, contact, route, undock, beam, virtual_z, cool, readout), hops, trigger</td></tr>'
      + '<tr><td>cost \u00b7 steps \u00b7 \u00b5s \u00b7 \u0394n\u0304</td><td>the replay\u2019s annotations per row: the cost model\u2019s units, machine cycles occupied, wall time, and the change in motional quanta of the hottest ion</td></tr>'
      + '</table>';
    h += '<h4>Pulses</h4><p>Any single-qubit gate u3(\u03b8, \u03c6, \u03bb) = VZ(\u03c6+\u03bb) \u00b7 R(\u03b8, \u03c0/2 \u2212 \u03bb): one frame update, one pulse. CX = (R \u2297 I)\u00b7(R \u2297 R)\u00b7MS(\u03c0/2)\u00b7(R \u2297 I) up to a global phase: four pulses and one entangler, on a co-located pair. Both identities are theorems in Compiler/lean (QCCDC.Pulse.Decompose).</p>';
    var dk = Object.keys(declared);
    if(dk.length){ h += '<h4>Movement classes this device declares</h4><table class="cols">' + dk.map(function(k){ var c = declared[k]; return '<tr><td>' + esc(k) + '</td><td>' + esc([c.type, c.orbit ? 'orbit ' + c.orbit : '', c.delta != null ? 'delta ' + c.delta : '', c.direction || '', c.entails ? 'entails ' + c.entails.join('+') : ''].filter(Boolean).join(' \u00b7 ')) + (c.note ? '<br><span style="color:#52514e">' + esc(c.note) + '</span>' : '') + '</td></tr>'; }).join('') + '</table>'; }
    try { if(P.templates){ h += '<h4>Templates this programme uses</h4><table class="cols">' + Object.keys(P.templates).map(function(k){ return '<tr><td>' + esc(k) + '</td><td>' + P.templates[k] + ' cycles</td></tr>'; }).join('') + '</table>'; } } catch(e){}
    h += '<h4>Circuit statements</h4><table class="cols">' + Object.keys(SRC_OPS).map(function(k){ return '<tr><td>' + esc(k) + '</td><td>' + esc(SRC_OPS[k]) + '</td></tr>'; }).join('') + '</table></div>';
    return h;
  }
  var CHEAT = null;

  // ---- (7) the rail keeps the standard components; everything else moves to the top ---
  // The editor re-renders the palette on every paint (`renderPalette` replaces the
  // children of #palBody), so the three folds are re-captured after each render; the
  // Start and Selection sections are static containers and move once.
  var head = document.querySelector('main > .card > .head'), card = head && head.parentElement;
  var POPS = {}, TOOLS = null;
  function mkPop(key, title){
    var b = document.createElement('button'); b.className = 'bbtb'; b.textContent = title; b.setAttribute('aria-expanded', 'false');
    var pop = document.createElement('div'); pop.className = 'bbpop'; pop.setAttribute('data-pop', key);
    var ph = document.createElement('div'); ph.className = 'bbph'; ph.textContent = title;
    var x = document.createElement('button'); x.className = 'bbx'; x.textContent = '\u00D7'; x.onclick = function(){ openPop(null); }; ph.appendChild(x);
    var host = document.createElement('div'); host.className = 'bbhost';
    pop.appendChild(ph); pop.appendChild(host);
    b.onclick = function(){ openPop(b.getAttribute('aria-expanded') === 'true' ? null : key); };
    POPS[key] = { btn: b, pop: pop, host: host, title: title };
    return POPS[key];
  }
  function openPop(key){
    for(var k in POPS){ var on = k === key; POPS[k].btn.setAttribute('aria-expanded', on ? 'true' : 'false'); POPS[k].pop.setAttribute('data-open', on ? '1' : '0');
      if(on){ POPS[k].pop.style.left = px(Math.max(0, POPS[k].btn.offsetLeft)); } }
  }
  function emptyNote(host, what){
    if(host.childElementCount) return;
    var m = document.createElement('div'); m.className = 'mut bbempty';
    var why = null; try { why = (typeof EDITOR !== 'undefined' && EDITOR.why) ? EDITOR.why() : null; } catch(e){}
    m.textContent = what + (why ? ' is not available for this device: ' + why : ' has nothing to show yet.');
    host.appendChild(m);
  }
  function capturePalette(){
    var body = $('palBody'); if(!body) return;
    var map = { row: 'rows', block: 'machine', component: 'components' };
    for(var fold in map){
      var det = body.querySelector('details.palfold[data-fold="' + fold + '"]'), P = POPS[map[fold]]; if(!P) continue;
      if(det){ var grp = det.querySelector('.palgrp'); P.host.replaceChildren(); if(grp) P.host.appendChild(grp); det.remove(); }
      emptyNote(P.host, P.title);
    }
  }
  if(head && card && rowEl && rail && !$('bbtools')){
    TOOLS = document.createElement('div'); TOOLS.id = 'bbtools';
    var order = [['start', 'Start \u2014 new or template device'], ['rows', 'Append a row'], ['machine', 'Machine settings'], ['components', 'Components \u2014 whole parts'], ['selection', 'Selection'], ['cheat', 'Instruction cheat sheet']];
    order.forEach(function(o){ var P = mkPop(o[0], o[1]); TOOLS.appendChild(P.btn); });
    CHEAT = POPS.cheat; CHEAT.host.innerHTML = cheatHTML();
    // the search box, right-aligned
    var sw = document.createElement('div'); sw.className = 'bbsw';
    var inp = document.createElement('input'); inp.id = 'bbsearch'; inp.type = 'search'; inp.placeholder = 'find any feature\u2026  (Ctrl+K)'; inp.setAttribute('autocomplete', 'off');
    var res = document.createElement('div'); res.id = 'bbsres';
    sw.appendChild(inp); sw.appendChild(res); TOOLS.appendChild(sw);
    order.forEach(function(o){ TOOLS.appendChild(POPS[o[0]].pop); });
    card.insertBefore(TOOLS, rowEl);
    // Start and Selection move once; the folds are captured on every palette render
    var st = $('palStart'); if(st){ POPS.start.host.appendChild(st); st.open = true; }
    var ins = $('palInspect'); if(ins){ POPS.selection.host.appendChild(ins); }
    capturePalette(); setTimeout(capturePalette, 300); setTimeout(capturePalette, 1500); setTimeout(capturePalette, 4000);
    try { var body = $('palBody'); if(body && typeof MutationObserver !== 'undefined') new MutationObserver(function(){ capturePalette(); }).observe(body, { childList: true }); } catch(e){}
    // a selection opens its panel, so clicking a part still shows its details
    try { var insp = $('palInsp'); if(insp && typeof MutationObserver !== 'undefined') new MutationObserver(function(){
      var t = (insp.textContent || '').trim();
      if(t && t.indexOf('Nothing selected') !== 0 && t.indexOf('armed:') !== 0) openPop('selection');
    }).observe(insp, { childList: true, subtree: true }); } catch(e){}
    // click elsewhere, or Escape, closes
    try { document.addEventListener('pointerdown', function(ev){ var t = ev.target; if(TOOLS.contains(t)) return; openPop(null); res.setAttribute('data-open', '0'); }); } catch(e){}
    try { document.addEventListener('keydown', function(ev){
      if(ev.key === 'Escape'){ openPop(null); res.setAttribute('data-open', '0'); }
      if((ev.ctrlKey || ev.metaKey) && (ev.key === 'k' || ev.key === 'K')){ ev.preventDefault(); inp.focus(); inp.select(); }
    }); } catch(e){}

    // ---- the search: every control on the page, its hint, and the course -------------
    function textOf(el){
      // a palette tile's name, not its avatar's alt text; block children joined by spaces
      if(el.classList && el.classList.contains('pal-item')){ var nb = el.querySelector('.pal-text b'); if(nb) return nb.textContent.trim(); var de = el.getAttribute('data-el'); if(de) return de.replace(/^cmp:/, '').replace(/_/g, ' '); }
      var t = el.getAttribute('aria-label') || el.getAttribute('placeholder') || '';
      if(!t){ var av = el.querySelector && el.querySelector('.avatar'); var txt = (typeof el.innerText === 'string' && el.innerText) ? el.innerText : el.textContent || '';
        if(av && av.textContent) txt = txt.replace(av.textContent, ' '); t = txt; }
      return t.trim().replace(/\s+/g, ' ').slice(0, 70);
    }
    function whereOf(el){
      var e = el;
      while(e && e !== document.body){
        if(e.classList && e.classList.contains('bbpop')) return { kind: 'pop', key: e.getAttribute('data-pop'), name: (POPS[e.getAttribute('data-pop')] || {}).title || 'panel' };
        if(e.id && /^pane[A-Z]$/.test(e.id)) return { kind: 'pane', key: e.id.slice(4), name: { L: 'Learn', P: 'Program', Q: 'Circuit', A: 'Device', M: 'Machine', W: 'Write', R: 'Report' }[e.id.slice(4)] || 'panel' };
        if(e.id === 'rail') return { kind: 'rail', name: 'Elements (left rail)' };
        if(e.id === 'stagebar') return { kind: 'bar', name: 'transport bar' };
        if(e.id === 'bbnotes') return { kind: 'notes', name: 'Study notes' };
        if(e.id === 'bbtools') return { kind: 'tools', name: 'tools bar' };
        if(e.classList && e.classList.contains('head')) return { kind: 'head', name: 'header' };
        e = e.parentElement;
      }
      return { kind: 'page', name: 'page' };
    }
    function index(){
      var out = [], seen = new Set();
      var els = document.querySelectorAll('button, [data-hint], .pal-item, summary, select, input[placeholder], label, a.btn');
      for(var i = 0; i < els.length; i++){
        var el = els[i];
        if(res.contains(el) || (el.closest && el.closest('#bbsres'))) continue;
        if(el.closest && el.closest('#bbqlabels')) continue;
        var label = textOf(el); var key = el.getAttribute('data-hint');
        var h = null; try { h = (key && typeof EDITOR !== 'undefined' && EDITOR.hintFor) ? EDITOR.hintFor(key) : null; } catch(e){}
        if(!label && h) label = h.t || '';
        if(!label) continue;
        var w = whereOf(el);
        var sig = label + '|' + (h ? h.t : '') + '|' + w.name; if(seen.has(sig)) continue; seen.add(sig);
        out.push({ label: label, title: h ? h.t : (el.getAttribute('title') || ''), desc: h ? (h.d || '') + (h.k ? '  [' + h.k + ']' : '') : (el.getAttribute('title') || ''), where: w, el: el });
      }
      Object.keys(OPS).forEach(function(k){ var el = CHEAT ? CHEAT.host.querySelector('.row[data-op="' + k + '"]') : null;
        out.push({ label: 'Instruction ' + k + ' \u2014 ' + OPS[k].name, title: '', desc: OPS[k].what.slice(0, 140), where: { kind: 'pop', key: 'cheat', name: 'Instruction cheat sheet' }, el: el }); });
      try { (EDITOR.lessonList() || []).forEach(function(l){ out.push({ label: 'Lesson ' + l.id + ' \u00b7 ' + l.title, title: '', desc: 'the course, part ' + l.part, where: { kind: 'lesson', id: l.id, name: 'Learn' }, el: null }); }); } catch(e){}
      M.forEach(function(m){ out.push({ label: 'Note: ' + m.label, title: '', desc: m.note.slice(0, 120), where: { kind: 'note', frame: m.frame, name: 'Study notes, step ' + (m.frame + 1) }, el: null }); });
      return out;
    }
    function reveal(item){
      var w = item.where, el = item.el;
      openPop(null); res.setAttribute('data-open', '0');
      if(w.kind === 'lesson'){ try { EDITOR.lessonLoad(w.id); } catch(e){} try { setPane('L'); } catch(e){} return; }
      if(w.kind === 'note'){ notesOpen(true); go(w.frame); return; }
      if(w.kind === 'pop') openPop(w.key);
      if(w.kind === 'pane'){ try { setPane(w.key); } catch(e){} }
      if(w.kind === 'notes') notesOpen(true);
      if(w.kind === 'rail'){ try { if(rail.getAttribute('data-collapsed') === '1' && window.PANELS) PANELS.fold(rail, false); } catch(e){} }
      if(!el) return;
      try { el.scrollIntoView({ block: 'center', inline: 'nearest' }); } catch(e){}
      try { el.focus({ preventScroll: true }); } catch(e){}
      el.classList.add('bbflash'); setTimeout(function(){ el.classList.remove('bbflash'); }, 1800);
    }
    var HITS = [], SEL = 0;
    function search(q){
      q = (q || '').trim().toLowerCase();
      res.replaceChildren();
      if(!q){ res.setAttribute('data-open', '0'); return; }
      var words = q.split(/\s+/), all = index();
      HITS = all.map(function(it){
        var hay = (it.label + ' ' + it.title + ' ' + it.desc + ' ' + it.where.name).toLowerCase();
        var ok = words.every(function(w){ return hay.indexOf(w) >= 0; }); if(!ok) return null;
        var score = words.reduce(function(a, w){ return a + (it.label.toLowerCase().indexOf(w) >= 0 ? 2 : 0) + (it.title.toLowerCase().indexOf(w) >= 0 ? 1 : 0); }, 0);
        return { it: it, score: score };
      }).filter(Boolean).sort(function(a, b){ return b.score - a.score; }).slice(0, 14).map(function(x){ return x.it; });
      SEL = 0;
      if(!HITS.length){ var n = document.createElement('div'); n.className = 'none'; n.textContent = 'nothing matches \u201c' + q + '\u201d'; res.appendChild(n); }
      HITS.forEach(function(it, i){
        var r = document.createElement('div'); r.className = 'r'; r.setAttribute('data-sel', i === 0 ? '1' : '0');
        var b = document.createElement('b'); b.textContent = it.label; r.appendChild(b);
        var w = document.createElement('span'); w.className = 'w'; w.textContent = 'in ' + it.where.name; r.appendChild(w);
        if(it.desc || it.title){ var d = document.createElement('div'); d.className = 'd'; d.textContent = (it.title && it.title !== it.label ? it.title + ' \u2014 ' : '') + it.desc; r.appendChild(d); }
        r.onclick = function(){ reveal(it); };
        res.appendChild(r);
      });
      res.setAttribute('data-open', '1');
    }
    inp.addEventListener('input', function(){ search(inp.value); });
    inp.addEventListener('focus', function(){ if(inp.value) search(inp.value); });
    inp.addEventListener('keydown', function(ev){
      if(ev.key === 'ArrowDown' || ev.key === 'ArrowUp'){ ev.preventDefault(); if(!HITS.length) return; SEL = (SEL + (ev.key === 'ArrowDown' ? 1 : HITS.length - 1)) % HITS.length;
        Array.from(res.children).forEach(function(r, i){ r.setAttribute('data-sel', i === SEL ? '1' : '0'); }); }
      if(ev.key === 'Enter'){ ev.preventDefault(); if(HITS[SEL]) reveal(HITS[SEL]); }
    });
  }

  // ---- wire the redraw hook and the deep link ------------------------------------
  function render(){ renderNotes(); renderNow(); renderLabels(); }
  try {
    if(typeof draw === 'function'){ var _d = draw; draw = function(){ var r = _d.apply(this, arguments); try{ render(); }catch(e){} return r; }; }
  } catch(e){}
  function fromHash(){
    try {
      var h = (location.hash||'').replace(/^#/,''), m;
      if((m = /(?:^|&)step=(\d+)/.exec(h))) go(parseInt(m[1],10)-1);
      else if((m = /(?:^|&)id=([^&]+)/.exec(h))){ var fr = byId(decodeURIComponent(m[1])); if(fr!=null) go(fr); }
    } catch(e){}
  }
  try { window.addEventListener('hashchange', fromHash); } catch(e){}
  fromHash();
  render();
})();
"""


def _inject(page: Path, entry: dict, marks: list[dict], numbers: dict, index_href: str) -> None:
    """Add the overlay AFTER rendering: the studio's own sources are not touched."""
    src = page.read_text(encoding="utf-8")
    facts = []
    if numbers.get("T_jones") is not None:
        facts.append(f"{numbers['T_jones']:.1f} ms (jones) / {numbers['T_transport']:.1f} ms (transport)")
    if numbers.get("batches"):
        facts.append(f"{numbers['batches']} batches, {numbers['hops']} hops")
    if numbers.get("ions_per_instruction"):
        facts.append(f"{numbers['ions_per_instruction']:.1f} ions per transport instruction")
    if "buildable" in numbers:
        facts.append("buildable" if numbers["buildable"] else
                     ("DRC clean, no wells" if numbers.get("drc_clean") else "not buildable"))
    if numbers.get("waveforms_max") is not None and numbers.get("shim_charge_ms") is not None:
        # what the programme asks of the control hardware (`_measure`, 2026-10-23)
        bits = numbers.get("select_bits_per_cycle") or 0
        facts.append(f"up to {numbers['waveforms_max']} waveforms a cycle, "
                     + (f"{bits} select bits a cycle" if bits else "no select bits")
                     + f", shims {numbers['shim_charge_ms']:.1f} ms at 1:{numbers.get('demux_order')} "
                       f"/ {numbers.get('shim_charge_m16_ms', 0):.1f} ms at 1:{numbers.get('demux_alt', DEMUX_ALT)}")
    if numbers.get("recompiled"):
        facts.append(numbers["recompiled"])
    if numbers.get("compiled"):
        facts.append(numbers["compiled"])
    if numbers.get("r10"):
        facts.append("R10: Lean checker + tableau")
    elif numbers.get("r10") is False:
        facts.append((numbers.get("r10_verdict") or "R10 FAILED: the checker rejects the certificate")
                     .lstrip("-> ").strip())
    body = (
        f'<div id="bbnotes" data-open="0"><div class="bbhead" id="bbhead">'
        f'<b>Study notes &middot; {html.escape(entry["key"])}</b>'
        f'<a href="{html.escape(index_href)}">index</a>'
        f'<button class="bbx" id="bbx" title="close the notes">&times;</button></div>'
        f'<div class="bbbody"><div class="t" style="color:#8b9ab5;margin-bottom:6px">'
        f'{html.escape(" · ".join(facts))}</div>'
        f'<div style="margin-bottom:8px">{html.escape(entry.get("claim") or entry.get("refusal") or "")}</div>'
        f'<div class="bbnow" id="bbnow2"></div><ul id="bblist"></ul>'
        f'<div class="bbnav"><button class="bbn" id="bbprev">&#9664; prev note</button>'
        f'<button class="bbn" id="bbnext">next note &#9654;</button>'
        f'<span style="color:#8b9ab5;align-self:center">or open <code>#step=N</code></span></div>'
        f'</div></div>'
        f'<button id="bbpill" data-open="0" title="the study\'s notes on this programme">Study notes</button>'
    )
    blob = json.dumps(marks, separators=(",", ":")).replace("</", "<\\/")
    tail = (f"<style>{OVERLAY_CSS}</style>{body}"
            f"<script>window.BB_MARKS={blob};</script><script>{OVERLAY_JS}</script>")
    if "</body>" in src:
        src = src.replace("</body>", tail + "</body>", 1)
    else:
        src += tail
    page.write_text(src, encoding="utf-8", newline="")


# --------------------------------------------------------------------------- pages

def _publishable(arch):
    """The device as a page may carry it.  `render_html` refuses a page whose data names a
    file by its path in this repository (a reader cannot open it), and a derived device's
    provenance does exactly that (`derived_from: Compiler/build/q06h/grid13x13.arch.json`,
    the direct-control twins of 2026-10-23).  Such a value keeps only the file's name; every
    other field, and the file on disk, are untouched."""
    from qccd.viz.render import _PAYLOAD_PATH
    prov = dict(arch.provenance or {})
    bare = {k: Path(v).name for k, v in prov.items()
            if isinstance(v, str) and _PAYLOAD_PATH.search(json.dumps(v))}
    return dataclasses.replace(arch, provenance={**prov, **bare}) if bare else arch


def build_one(entry: dict, out_dir: Path, *, max_frames: int) -> dict:
    arch_p = Path(entry["arch"])
    if not arch_p.exists():
        return {**{k: str(v) for k, v in entry.items()}, "status": f"missing {arch_p.name}"}
    arch = load(str(arch_p))
    # the page is priced on its board's clock (`table`; a Goal 1 board's is `quantinuum`), so the
    # evaluation it carries is the one the board grades by -- the clock's noise model with it
    table = entry.get("table") or "qccdsim_jones"
    model = corrected_model(table)
    numbers = entry["numbers"] if isinstance(entry.get("numbers"), dict) else _numbers(entry)
    page = out_dir / f"{entry['key']}.html"
    source = None
    marks: list[dict] = []
    if entry.get("prefix"):
        prefix = Path(entry["prefix"])
        tsir = Path(str(prefix) + ".cooled.tsir.json")
        cert = Path(str(prefix) + ".qcert.json")
        qasm = Path(entry.get("qasm") or (str(prefix) + ".qasm"))
        for p in (tsir, cert, qasm):
            if not p.exists():
                return {**{k: str(v) for k, v in entry.items()}, "status": f"missing {p.name}"}
        prog = TSIR.load(str(tsir))
        # THE DECODER IS CALLED IN THE PROGRAMME, once per syndrome window
        # (`qccd.compile.decode`): the compiler's instructions untouched, each keeping its
        # id, plus a `decode` where a window of outcomes is complete.  It moves no ion and
        # takes no machine time, so every replayed number below is the one the compiler's
        # programme alone would give -- and the page lights the wires to the decoder there.
        prog = insert_decodes(prog)
        source = build_source(prog, json.loads(cert.read_text(encoding="utf-8")), qasm)
        report = verify(prog, arch, model, check_metrics=False)
        marks = _marks(entry, prog, arch, report)
        rules = report.rules.summary()
        # The running times the board ranks by are the REPLAYED ones.  The study's data
        # files were written when the programmes were compiled, and a programme can be
        # rewritten afterwards (the R22 uniformising pass, a recompile); a page that
        # quoted the old figure beside the new programme would be lying about itself.
        numbers = dict(numbers)
        jones = report if table == "qccdsim_jones" else verify(prog, arch, corrected_model("qccdsim_jones"), check_metrics=False)
        numbers["T_jones"] = round(jones.result.total_us / 1000.0, 3)
        try:
            alt = verify(prog, arch, corrected_model("transport_excitation"), check_metrics=False)
            numbers["T_transport"] = round(alt.result.total_us / 1000.0, 3)
        except Exception:      # a device whose curves lack the second table keeps the study's figure
            pass
        numbers["T_source"] = "replay"
        if not isinstance(entry.get("numbers"), dict):
            # the BB board's own rows: "every rule passes" from the page's own verdict
            numbers["rules_ok"] = not rules["failed"]
        lede =(f"{entry['claim']}  |  {len(prog)} instructions against "
                f"{len(source['ops'])} circuit statements; rules "
                f"{'all pass' if not rules['failed'] else 'FAILED ' + ' '.join(rules['failed'])}")
        headline = f"{entry['title']}"
    else:
        prog = TSIR(name="not compiled", arch_spec=str(arch_p))
        report = verify(prog, arch, model, check_metrics=False)
        rules = report.rules.summary()
        lede = f"REFUSED: {entry['refusal']}  |  {entry['claim']}"
        headline = entry["title"]
    # `template_stems="*"` is what `qccd studio` ships by default, and the page's editor
    # state is only initialised with a template registry present -- a page rendered
    # without one loads, but `EDITOR.state()` is null and the harness cannot digest it.
    render_html(_publishable(arch), prog, report.result, model, page, kicker=entry.get("kicker") or "BB CODESIGN",
                headline=headline, lede=lede, source=source,
                template_stems="*", max_frames=max_frames)
    _inject(page, entry, marks, numbers, "index.html")
    return {**{k: (str(v) if isinstance(v, Path) else v) for k, v in entry.items()},
            "status": "ok", "page": page.name, "instructions": len(prog),
            "rules_failed": rules["failed"], "rules_passed": len(rules["passed"]),
            "disqualified": bool(rules["failed"]),
            "marks": marks, "numbers": numbers, "bytes": page.stat().st_size,
            # the website reads the top-level flag too (qccd/site/build.py::_r10)
            "r10": numbers.get("r10"),
            "over_budget": over_budget(arch_p, release_of(out_dir)),
            "sites": sum(1 for n in arch.device.nodes.values() if n.kind == "site"),
            "junctions": len(arch.device.junction_nodes),
            "loops": sum(1 for lp in arch.device.loops.values() if lp.closed)}


# --------------------------------------------------------------------------- index

def _fmt(v, nd=1):
    if v is None:
        return "–"
    if isinstance(v, bool):
        return "yes" if v else "no"
    if isinstance(v, float):
        return f"{v:,.{nd}f}"
    return f"{v:,}" if isinstance(v, int) else str(v)


def write_index(rows: list[dict], out_dir: Path, *, title: str | None = None,
                subtitle: str | None = None) -> Path:
    """One white page, one plot: every design is a dot, ranked by whatever the reader picks.

    The chart is the page.  A `Rank by` control chooses the measure (running time on
    either cost table, error, DACs, die area, electrodes per trap, parallelism, batches,
    hops, the schedule's distance from its bound); the dots are laid out best-to-worst
    left to right, or against a second measure as a scatter.  Colour is the FAMILY only
    (three, the slots that validate all-pairs), filled marks are buildable and hollow
    ones are not, and the designs the compiler refused stand as crosses in their own
    column, because "no result" is a result.  A click fills the detail panel with the
    numbers, the claim, and the step links into the studio page.  A table view carries
    every value, which the aqua slot's contrast requires.
    """
    records = []
    for r in rows:
        if r.get("status") != "ok":
            continue
        # A design that fails any rule is DISQUALIFIED, not ranked: the ranking holds only
        # candidates that obey every rule (2026-09-15).  Since 2026-10-20 the page lists
        # it apart, with the rules it breaks (`_disqualified_html`), instead of omitting it.
        if r.get("rules_failed") or r.get("over_budget"):
            continue
        n = r.get("numbers") or {}
        records.append({
            "key": r["key"], "title": r["title"], "family": r["family"],
            "short": r.get("short") or SHORT.get(r["key"], r["title"]),
            "answer": bool(r.get("answer")), "page": r.get("page"), "gif": r.get("gif"),
            "claim": r.get("claim", ""), "refusal": r.get("refusal"),
            "compiled": bool(r.get("prefix")),
            "rules_failed": r.get("rules_failed") or [],
            "rules_passed": r.get("rules_passed"),
            "instructions": r.get("instructions"),
            "sites": r.get("sites"), "junctions": r.get("junctions"), "loops": r.get("loops"),
            "numbers": {k: v for k, v in n.items() if k not in ("batch_sizes", "drc")},
            "drc": sum((n.get("drc") or {}).values()) if n.get("drc") else None,
            "marks": [{"step": m["step"], "label": m["label"], "kind": m["kind"]}
                      for m in (r.get("marks") or [])],
        })
    data = json.dumps(records, separators=(",", ":")).replace("</", "<\\/")
    page = _INDEX_TEMPLATE.replace("__DATA__", data)
    dq = [r for r in rows if r.get("status") == "ok" and (r.get("rules_failed") or r.get("over_budget"))]
    page = page.replace("\n</main>", _disqualified_html(dq) + "\n</main>", 1)
    if title:
        page = page.replace("BB [[144,12,12]] on QCCD — every design, ranked", title)
    if subtitle:
        page = page.replace("One syndrome round of the gross code compiled onto every architecture the study reached.", subtitle)
    p = out_dir / "index.html"
    p.write_text(page, encoding="utf-8")
    return p


def _disqualified_html(rows: list[dict]) -> str:
    """The designs that break a rule, listed apart and never ranked: each with the rules it
    breaks (linked to the Rules page, which states them) and a way into its programme, where
    every violation is shown on its step.  Empty when every design obeys every rule."""
    if not rows:
        return ""

    def broken(r):
        rules = " ".join(f'<a href="../../rules/#{html.escape(x)}">{html.escape(x)}</a>'
                         for x in r.get("rules_failed") or [])
        # the task's hardware budget is not a rule of the programme: it is the grader's
        # refusal of the device (DEVICE.OVER_BUDGET), said as the grader says it
        budget = "; ".join(f"over budget: {html.escape(x)}" for x in r.get("over_budget") or [])
        return " ".join(x for x in (rules, budget) if x)

    def r10(r):
        v = (r.get("numbers") or {}).get("r10")
        return "passed" if v is True else "FAILED" if v is False else "not run"

    trs = "".join(
        f'<tr><td><a href="{html.escape(r["page"])}">{html.escape(r["title"])}</a>'
        f'<div class="dqc">{html.escape(r.get("claim") or "")}</div></td>'
        f'<td>{html.escape(r.get("family") or "")}</td><td class="dqr">{broken(r)}</td>'
        f'<td>{r10(r)}</td></tr>'
        for r in sorted(rows, key=lambda r: r["key"]))
    n = len(rows)
    return ('<section class="dq" id="disqualified"><h2>Disqualified &mdash; not ranked</h2>'
            f'<p class="sub">{n} design{"s" if n != 1 else ""} ran the same circuit but '
            f'break{"s" if n == 1 else ""} at least one rule or need more hardware than the task '
            f'allows, so {"it is" if n == 1 else "they are"} not ranked above. Each is listed with '
            'what it breaks; open one to see every violation on its step in the design '
            'tool.</p><div class="dqw"><table class="tbl dqt"><tr><th>design</th><th>family</th>'
            '<th>breaks</th><th>R10</th></tr>' + trs + '</table></div></section>')


_INDEX_TEMPLATE = r"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>BB [[144,12,12]] on QCCD — every design, ranked</title>
<style>
:root{color-scheme:light;
 --surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;--ink3:#8a8985;--line:#e6e5e1;--grid:#efeeeb;
 --ring:#2a78d6;--lattice:#eb6834;--loops:#1baf7a;--refused:#8a8985;--accent:#2a78d6}
*{box-sizing:border-box}
body{margin:0;background:var(--surface);color:var(--ink);
 font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1280px;margin:0 auto;padding:26px 28px 60px}
h1{font-size:22px;font-weight:600;margin:0 0 2px;letter-spacing:-.01em}
.sub{color:var(--ink2);margin:0 0 18px;max-width:80ch}
.controls{display:flex;flex-wrap:wrap;gap:18px;align-items:center;padding:10px 0 6px;border-top:1px solid var(--line);border-bottom:1px solid var(--line);margin-bottom:8px}
.controls label{display:flex;gap:8px;align-items:center;color:var(--ink2);font-size:13px}
select{font:inherit;font-size:13px;padding:5px 8px;border:1px solid #cfceca;border-radius:6px;background:#fff;color:var(--ink)}
.seg{display:inline-flex;border:1px solid #cfceca;border-radius:6px;overflow:hidden}
.seg button{font:inherit;font-size:13px;padding:5px 10px;border:0;background:#fff;color:var(--ink2);cursor:pointer}
.seg button[aria-pressed="true"]{background:#eef3fb;color:var(--ink);font-weight:600}
.chartwrap{position:relative}
svg{display:block;width:100%;height:auto}
.axis text{font-size:12px;fill:var(--ink2)} .axis line{stroke:var(--line)}
.grid line{stroke:var(--grid)}
.axtitle{font-size:12.5px;fill:var(--ink2)}
.dot{cursor:pointer} .dot circle.hit{fill:transparent}
.dot circle.mark{fill:currentColor;stroke:var(--surface);stroke-width:2}
.dot.hollow circle.mark{fill:var(--surface);stroke:currentColor;stroke-width:2.5}
.dot circle.halo{fill:none;stroke:none} .dot.sel circle.halo{stroke:var(--ink);stroke-width:1.5}
.dot text{font-size:12px;fill:var(--ink);pointer-events:none}
.dot text.mut{fill:var(--ink2)}
.cross{cursor:pointer} .cross line{stroke:var(--refused);stroke-width:2}
.cross.sel line{stroke:var(--ink)}
.legend{display:flex;flex-wrap:wrap;gap:18px;color:var(--ink2);font-size:12.5px;margin:4px 0 14px;align-items:center}
.legend .sw{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px;vertical-align:-1px}
.legend .sw.hollow{background:#fff!important;border:2px solid var(--ink2)}
.legend .x{display:inline-block;width:10px;height:10px;margin-right:6px;position:relative;vertical-align:-1px}
.legend .x:before,.legend .x:after{content:"";position:absolute;left:4px;top:-1px;width:2px;height:12px;background:var(--refused);transform:rotate(45deg)}
.legend .x:after{transform:rotate(-45deg)}
.tip{position:absolute;pointer-events:none;background:#fff;border:1px solid #cfceca;border-radius:6px;padding:6px 9px;font-size:12.5px;box-shadow:0 2px 10px rgba(0,0,0,.08);display:none;max-width:320px}
.tip b{font-size:13px}
.detail{border-top:1px solid var(--line);padding-top:16px;display:grid;grid-template-columns:minmax(300px,1fr) minmax(340px,1.3fr);gap:28px}
.detail.empty{color:var(--ink3);display:block}
.detail h2{font-size:17px;margin:0 0 4px;font-weight:600}
.detail .fam{color:var(--ink2);font-size:12.5px;margin-bottom:8px}
.detail p{margin:0 0 10px;color:var(--ink2)}
.detail table{width:100%;border-collapse:collapse;font-size:13px}
.detail td{padding:4px 0;border-bottom:1px solid var(--grid)}
.detail td:last-child{text-align:right;font-variant-numeric:tabular-nums}
.ok{color:#008300} .bad{color:#c62828}
.btns{display:flex;gap:10px;margin:8px 0 14px;flex-wrap:wrap}
.btn{display:inline-block;padding:7px 12px;border-radius:6px;border:1px solid #cfceca;background:#fff;color:var(--ink);text-decoration:none;font-size:13px}
.btn.primary{background:var(--accent);color:#fff;border-color:var(--accent)}
.steps{list-style:none;margin:0;padding:0;font-size:13px}
.steps li{padding:3px 0;border-bottom:1px solid var(--grid)}
.steps a{color:var(--accent);text-decoration:none;font-variant-numeric:tabular-nums;margin-right:8px}
.steps li[data-kind=fail] a{color:#c62828} .steps li[data-kind=warn] a{color:#9a6b00}
.tbl{width:100%;border-collapse:collapse;font-size:13px;margin-top:6px}
#tablewrap{overflow-x:auto}
.tbl th,.tbl td{padding:6px 8px;border-bottom:1px solid var(--line);text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tbl th:first-child,.tbl td:first-child{text-align:left} .tbl th{color:var(--ink2);font-weight:500;cursor:pointer}
.tbl tr{cursor:pointer} .tbl tr:hover td{background:#f4f6fa} .tbl tr.sel td{background:#eef3fb}
[hidden]{display:none!important}
.dq{margin-top:30px;border-top:1px solid var(--line);padding-top:14px}
.dq h2{font-size:17px;margin:0 0 4px;font-weight:600}
.dqw{overflow-x:auto}.dqt td{vertical-align:top;white-space:normal;overflow-wrap:anywhere;text-align:left}.dqt th{text-align:left;cursor:default}.dqt tr{cursor:default}.dqt td a{color:inherit}.dq .dqc{color:var(--ink3);font-size:12px;margin-top:2px;max-width:640px}
.dq .dqr a{margin-right:6px}
</style></head><body><main>
<h1>BB [[144,12,12]] on QCCD — every design, ranked</h1>
<p class="sub">One syndrome round of the gross code compiled onto every architecture the study reached. Each dot is a design; pick what to rank by, click a dot for its numbers and the steps in the design tool where each claim can be checked.</p>
<div class="controls">
 <label>Rank by <select id="ysel"></select></label>
 <label>against <select id="xsel"><option value="rank">rank (best → worst)</option></select></label>
 <label><input type="checkbox" id="log"> log scale</label>
 <span class="seg" role="group" aria-label="view"><button id="vchart" aria-pressed="true">chart</button><button id="vtable" aria-pressed="false">table</button></span>
 <span id="count" style="color:var(--ink3);font-size:12.5px;margin-left:auto"></span>
</div>
<div class="chartwrap" id="chartwrap"><svg id="svg" viewBox="0 0 1224 560" aria-label="ranking plot"></svg><div class="tip" id="tip"></div></div>
<div class="legend" id="legend"></div>
<div id="tablewrap" hidden></div>
<div class="detail empty" id="detail">Click a dot to see the design's numbers, its claim, and the steps to check it on.</div>
</main>
<script>
var DATA = __DATA__;
var METRICS = [
 {k:'T_jones', name:'Total running time — jones table', unit:'ms', better:'low', log:true},
 {k:'T_transport', name:'Total running time — transport table', unit:'ms', better:'low', log:true},
 {k:'p_eff_vs_threshold', name:'Error — p_eff / threshold', unit:'', better:'low', nd:3},
 {k:'dacs', name:'Resource — DACs', unit:'', better:'low', log:true, nd:0},
 {k:'select_bits_per_cycle', name:'Control — select bits per cycle', unit:'', better:'low', nd:0, opt:true},
 {k:'shim_charge_ms', name:'Control — shim charging, declared demux', unit:'ms', better:'low', log:true, opt:true},
 {k:'shim_charge_m16_ms', name:'Control — shim charging at 1:16', unit:'ms', better:'low', log:true, opt:true},
 {k:'area_mm2', name:'Resource — die area', unit:'mm²', better:'low'},
 {k:'pads_per_trap', name:'Resource — control electrodes per trap', unit:'', better:'high', nd:2},
 {k:'ions_per_instruction', name:'Parallelism — ions per transport instruction', unit:'', better:'high'},
 {k:'pairs_per_gate', name:'Parallelism — pairs per gate instruction', unit:'', better:'high'},
 {k:'batches', name:'Schedule — batches', unit:'', better:'low', nd:0},
 {k:'hops', name:'Schedule — rigid hops', unit:'', better:'low', nd:0},
 {k:'rotation_gap_pct', name:'Schedule — rotation above its lower bound', unit:'%', better:'low'},
 {k:'us_per_ion', name:'Transport — µs per ion moved', unit:'µs', better:'low', nd:2, log:true},
 {k:'diameter', name:'Graph — diameter (hops)', unit:'', better:'low', nd:0}
];
// the control measures (opt) are offered only on a board whose rows carry them
METRICS = METRICS.filter(function(m){ return !m.opt || DATA.some(function(d){ return d.numbers && typeof d.numbers[m.k] === 'number'; }); });
var FAMHEX = {'ring + docks':'#2a78d6','lattice':'#eb6834','torus':'#1baf7a','random graph':'#4a3aa7','two loops':'#8a8985'};
var $ = function(id){ return document.getElementById(id); };
var ysel = $('ysel'), xsel = $('xsel'), logbox = $('log');
METRICS.forEach(function(m){ var o = document.createElement('option'); o.value = m.k; o.textContent = m.name; ysel.appendChild(o);
  var o2 = document.createElement('option'); o2.value = m.k; o2.textContent = m.name; xsel.appendChild(o2); });
var SEL = null, VIEW = 'chart';
function metric(k){ for (var i=0;i<METRICS.length;i++) if (METRICS[i].k===k) return METRICS[i]; return null; }
function val(d, k){ var v = d.numbers ? d.numbers[k] : null; return (typeof v === 'number' && isFinite(v)) ? v : null; }
function fmt(v, m){ if (v==null) return '–'; var nd = (m && m.nd!=null) ? m.nd : (Math.abs(v)>=100?0:1); return v.toLocaleString(undefined,{maximumFractionDigits:nd, minimumFractionDigits:nd}) + (m && m.unit ? ' '+m.unit : ''); }
function ranked(k){
  var m = metric(k), xs = DATA.filter(function(d){ return d.compiled && val(d,k)!=null; });
  xs.sort(function(a,b){ return m.better==='low' ? val(a,k)-val(b,k) : val(b,k)-val(a,k); });
  return xs;
}
function svgEl(n, attrs){ var e = document.createElementNS('http://www.w3.org/2000/svg', n); for (var a in attrs) e.setAttribute(a, attrs[a]); return e; }
function ticks(lo, hi, log){
  if (log){ var t=[], p=Math.pow(10, Math.floor(Math.log10(lo))); while (p<=hi*1.0001){ [1,2,5].forEach(function(f){ var v=p*f; if (v>=lo*0.999 && v<=hi*1.001) t.push(v); }); p*=10; } return t; }
  var span = hi-lo, step = Math.pow(10, Math.floor(Math.log10(span/5))); var err = span/5/step; step *= err>=5?5:err>=2?2:1;
  var t=[], v=Math.ceil(lo/step)*step; while (v<=hi+1e-9){ t.push(+v.toFixed(10)); v+=step; } return t;
}
function draw(){
  var yk = ysel.value, xk = xsel.value, ym = metric(yk), xm = xk==='rank' ? null : metric(xk);
  var rows = ranked(yk); if (xm) rows = rows.filter(function(d){ return val(d,xk)!=null; });
  var refused = DATA.filter(function(d){ return !d.compiled; });
  var missing = DATA.filter(function(d){ return d.compiled && rows.indexOf(d)<0; });
  var svg = $('svg'); while (svg.firstChild) svg.removeChild(svg.firstChild);
  var W=1224, H=560, L=78, R=refused.length?150:28, T=26, B=52, pw=W-L-R, ph=H-T-B;
  var useLog = logbox.checked;
  var yv = rows.map(function(d){ return val(d,yk); }), ylo=Math.min.apply(null,yv), yhi=Math.max.apply(null,yv);
  if (ylo===yhi){ ylo*=0.9; yhi*=1.1; }
  if (useLog && ylo<=0) useLog=false;
  var ypad = useLog ? 1 : (yhi-ylo)*0.1;
  var y0 = useLog ? ylo/1.4 : (ylo-ypad < 0 && ylo>=0 ? 0 : ylo-ypad), y1 = useLog ? yhi*1.4 : yhi+ypad;
  var Y = function(v){ return useLog ? T + ph - (Math.log(v)-Math.log(y0))/(Math.log(y1)-Math.log(y0))*ph : T + ph - (v-y0)/(y1-y0)*ph; };
  var X;
  if (!xm){ X = function(d,i){ return L + 24 + (rows.length>1 ? i/(rows.length-1) : 0.5)*(pw-48); }; }
  else { var xv = rows.map(function(d){ return val(d,xk); }), xlo=Math.min.apply(null,xv), xhi=Math.max.apply(null,xv); if (xlo===xhi){ xlo*=0.9; xhi*=1.1; }
    var xlog = useLog && xlo>0; var x0 = xlog? xlo/1.4 : xlo-(xhi-xlo)*0.1, x1 = xlog? xhi*1.4 : xhi+(xhi-xlo)*0.1;
    X = function(d){ var v=val(d,xk); return xlog ? L + (Math.log(v)-Math.log(x0))/(Math.log(x1)-Math.log(x0))*pw : L + (v-x0)/(x1-x0)*pw; };
    var xt = ticks(xlo, xhi, xlog), gx = svgEl('g',{class:'axis'});
    xt.forEach(function(v){ var o={}; o[xk]=v; var x = X({numbers:o}); if (x<L||x>L+pw) return;
      gx.appendChild(svgEl('line',{x1:x,x2:x,y1:T+ph,y2:T+ph+5})); var t=svgEl('text',{x:x,y:T+ph+18,'text-anchor':'middle'}); t.textContent=fmt(v,xm); gx.appendChild(t); });
    svg.appendChild(gx);
  }
  var g = svgEl('g',{class:'grid'}), ga = svgEl('g',{class:'axis'});
  ticks(ylo, yhi, useLog).forEach(function(v){ var y=Y(v); if (y<T||y>T+ph) return; g.appendChild(svgEl('line',{x1:L,x2:L+pw,y1:y,y2:y}));
    var t=svgEl('text',{x:L-8,y:y+4,'text-anchor':'end'}); t.textContent=fmt(v,ym); ga.appendChild(t); });
  svg.appendChild(g); svg.appendChild(ga);
  svg.appendChild(svgEl('line',{x1:L,x2:L+pw,y1:T+ph,y2:T+ph,stroke:'#cfceca'}));
  var yt = svgEl('text',{x:L,y:14,class:'axtitle'}); yt.textContent = ym.name + (ym.unit?' ('+ym.unit+')':'') + ' — ' + (ym.better==='low'?'lower is better':'higher is better'); svg.appendChild(yt);
  var xt2 = svgEl('text',{x:L+pw/2,y:H-8,class:'axtitle','text-anchor':'middle'}); xt2.textContent = xm ? xm.name + (xm.unit?' ('+xm.unit+')':'') : 'rank (best on the left)'; svg.appendChild(xt2);
  var best = rows[0];
  rows.forEach(function(d,i){
    var x = X(d,i), y = Y(val(d,yk)), col = FAMHEX[d.family]||'#8a8985';
    var gd = svgEl('g',{class:'dot'+(d.numbers.buildable?'':' hollow')+(SEL===d.key?' sel':''),'data-key':d.key,style:'color:'+col});
    gd.appendChild(svgEl('circle',{class:'halo',cx:x,cy:y,r:11}));
    gd.appendChild(svgEl('circle',{class:'mark',cx:x,cy:y,r:d.answer?7.5:5.5}));
    gd.appendChild(svgEl('circle',{class:'hit',cx:x,cy:y,r:16}));
    if (d.answer || d===best){ var lab = svgEl('text',{x:x, y:y-15, 'text-anchor':'middle', class:d.answer?'':'mut'}); lab.textContent = (d.answer?'recommended · ':'') + shortName(d); gd.appendChild(lab); }
    gd.addEventListener('mousemove', function(ev){ tip(ev, d, yk, xk); });
    gd.addEventListener('mouseleave', hideTip);
    gd.addEventListener('click', function(){ select(d.key); });
    svg.appendChild(gd);
  });
  if (refused.length){
    var cx = L+pw+34, t=svgEl('text',{x:cx-6,y:T+8,class:'axtitle'}); t.textContent='compiler refused'; svg.appendChild(t);
    svg.appendChild(svgEl('line',{x1:L+pw+16,x2:L+pw+16,y1:T,y2:T+ph,stroke:'#e6e5e1','stroke-dasharray':'3 4'}));
    refused.forEach(function(d,i){ var y = T+44+i*((ph-70)/Math.max(1,refused.length-1));
      var gc = svgEl('g',{class:'cross'+(SEL===d.key?' sel':''),'data-key':d.key});
      gc.appendChild(svgEl('line',{x1:cx-5,y1:y-5,x2:cx+5,y2:y+5})); gc.appendChild(svgEl('line',{x1:cx-5,y1:y+5,x2:cx+5,y2:y-5}));
      gc.appendChild(svgEl('circle',{cx:cx,cy:y,r:14,fill:'transparent'}));
      var lab = svgEl('text',{x:cx+12,y:y+4,style:'font-size:11.5px;fill:#52514e'}); lab.textContent = shortName(d); gc.appendChild(lab);
      gc.addEventListener('mousemove', function(ev){ tip(ev, d, yk, xk); }); gc.addEventListener('mouseleave', hideTip);
      gc.addEventListener('click', function(){ select(d.key); }); svg.appendChild(gc); });
  }
  $('count').textContent = rows.length + ' designs plotted' + (missing.length ? ' · ' + missing.length + (missing.length===1?' has':' have') + ' no value for this measure (see the table)' : '') + (refused.length ? ' · ' + refused.length + ' refused' : '');
  legend(); if (VIEW==='table') table();
}
function shortName(d){ return d.short || d.title; }
function legend(){ var el=$('legend'); el.innerHTML='';
  Object.keys(FAMHEX).forEach(function(f){ var s=document.createElement('span'); var sw=document.createElement('i'); sw.className='sw'; sw.style.background=FAMHEX[f]; s.appendChild(sw); s.appendChild(document.createTextNode(f)); el.appendChild(s); });
  var h=document.createElement('span'); var hs=document.createElement('i'); hs.className='sw hollow'; h.appendChild(hs); h.appendChild(document.createTextNode('hollow = not buildable in this technology')); el.appendChild(h);
  var x=document.createElement('span'); var xs=document.createElement('i'); xs.className='x'; x.appendChild(xs); x.appendChild(document.createTextNode('the compiler could not serve the device')); el.appendChild(x);
  var g=document.createElement('span'); var gs=document.createElement('i'); gs.className='sw'; gs.style.background='#8a8985'; g.appendChild(gs); g.appendChild(document.createTextNode('grey = a loop without docks, a line, rails, or two loops')); el.appendChild(g);
  var a=document.createElement('span'); a.textContent='larger dot = the recommended design'; el.appendChild(a); }
function tip(ev, d, yk, xk){ var t=$('tip'), ym=metric(yk), xm=xk==='rank'?null:metric(xk); t.innerHTML='';
  var b=document.createElement('b'); b.textContent=shortName(d); t.appendChild(b); t.appendChild(document.createElement('br'));
  if (d.compiled){ var v=document.createElement('b'); v.textContent=fmt(val(d,yk),ym); t.appendChild(v); t.appendChild(document.createTextNode(' '+(ym.name.split(' — ')[1]||ym.name)));
    if (xm){ t.appendChild(document.createElement('br')); t.appendChild(document.createTextNode(fmt(val(d,xk),xm)+' '+(xm.name.split(' — ')[1]||xm.name))); } }
  else t.appendChild(document.createTextNode(d.refusal||'refused'));
  t.appendChild(document.createElement('br')); var s=document.createElement('span'); s.style.color='#8a8985'; s.textContent=d.family+' · click for details'; t.appendChild(s);
  var w=$('chartwrap').getBoundingClientRect(); t.style.display='block'; t.style.left=(ev.clientX-w.left+14)+'px'; t.style.top=(ev.clientY-w.top+14)+'px'; }
function hideTip(){ $('tip').style.display='none'; }
function select(key){ SEL=key; draw(); detail(DATA.filter(function(d){return d.key===key;})[0]); pushState(); }
function row(k,v,cls){ var tr=document.createElement('tr'), a=document.createElement('td'), b=document.createElement('td'); a.textContent=k; if (cls) b.className=cls; b.textContent=v; tr.appendChild(a); tr.appendChild(b); return tr; }
function detail(d){ var el=$('detail'); el.className='detail'; el.innerHTML='';
  var left=document.createElement('div'), right=document.createElement('div');
  var h=document.createElement('h2'); h.textContent=d.title; left.appendChild(h);
  var f=document.createElement('div'); f.className='fam'; f.textContent=d.family + (d.compiled ? ' · '+d.instructions+' instructions' : ' · '+d.sites+' sites, '+d.junctions+' junctions, '+d.loops+' closed loops'); left.appendChild(f);
  var c=document.createElement('p'); c.textContent=d.claim; left.appendChild(c);
  if (d.refusal){ var rf=document.createElement('p'); rf.className='bad'; rf.textContent='Compiler: '+d.refusal; left.appendChild(rf); }
  var btns=document.createElement('div'); btns.className='btns';
  var a=document.createElement('a'); a.className='btn primary'; a.href=d.page; a.textContent=d.compiled?'Open in studio — step every instruction':'Open in studio — inspect the device'; btns.appendChild(a);
  if (d.gif){ var g=document.createElement('a'); g.className='btn'; g.href='../'+d.gif; g.textContent='animated clip'; btns.appendChild(g); }
  left.appendChild(btns);
  if (d.marks && d.marks.length){ var sh=document.createElement('div'); sh.style.cssText='font-size:12.5px;color:#52514e;margin-bottom:4px'; sh.textContent='Check each claim on its step:'; left.appendChild(sh);
    var ul=document.createElement('ul'); ul.className='steps'; d.marks.forEach(function(m){ var li=document.createElement('li'); li.setAttribute('data-kind',m.kind); var la=document.createElement('a'); la.href=d.page+'#step='+m.step; la.textContent='step '+m.step; li.appendChild(la); li.appendChild(document.createTextNode(m.label)); ul.appendChild(li); }); left.appendChild(ul); }
  if (d.compiled){ var tb=document.createElement('table'); var n=d.numbers;
    METRICS.forEach(function(m){ if (val(d,m.k)!=null) tb.appendChild(row(m.name.replace(' — ',': '), fmt(val(d,m.k),m))); });
    // what the programme asks of the control hardware: the waveform census (qccd/cost/waveforms.py)
    if (n.waveforms_max!=null) tb.appendChild(row('Control: waveforms played at once', n.waveforms_max + ' (' + n.motions_max + ' motion' + (n.motions_max===1?'':'s') + (n.waveforms_max>n.motions_max?' + the hold':'') + ')'));
    if (n.dacs_census!=null) tb.appendChild(row('Control: DACs with the census', fmt(n.dacs_census,{nd:0})));
    if (n.select_rate_mbit_s) tb.appendChild(row('Control: select rate at the shortest cycle', fmt(n.select_rate_mbit_s,{nd:1,unit:'Mbit/s'})));
    if (n.demux_order!=null) tb.appendChild(row('Control: declared shim demultiplexer', '1:' + n.demux_order + ', ' + fmt(n.shim_charge_us_per_layer,{nd:0,unit:'µs'}) + ' a charge'));
    if (n.shim_charge_every_layer_ms!=null) tb.appendChild(row('Control: shim charging every gate layer (WISER), declared / 1:16', fmt(n.shim_charge_every_layer_ms,{nd:1,unit:'ms'}) + ' / ' + fmt(n.shim_charge_every_layer_m16_ms,{nd:1,unit:'ms'})));
    if (n.shim_dacs_m16!=null) tb.appendChild(row('Control: shim DACs at 1:16 (deck / WISE)', n.shim_dacs_m16 + ' / ' + n.shim_dacs_m16_wise));
    tb.appendChild(row('buildable', n.buildable ? 'yes' : (n.drc_clean ? 'DRC clean, but no control electrodes survive' : 'no'+(d.drc?' ('+d.drc+' design-rule violations)':'')), n.buildable?'ok':'bad'));
    tb.appendChild(row('rules', d.rules_failed.length ? d.rules_failed.join(' ')+' FAILED' : d.rules_passed+' passed', d.rules_failed.length?'bad':'ok'));
    tb.appendChild(row('R10: Lean checker + tableau', n.r10 === true ? 'passed' : n.r10 === false ? (n.r10_verdict ? n.r10_verdict.replace(/^-> R10 /, '') : 'FAILED') : 'not run', n.r10 === true ? 'ok' : n.r10 === false ? 'bad' : ''));
    right.appendChild(tb); }
  el.appendChild(left); el.appendChild(right); }
function table(){ var w=$('tablewrap'); w.innerHTML=''; var yk=ysel.value;
  var cols=['T_jones','T_transport','p_eff_vs_threshold','dacs','select_bits_per_cycle','shim_charge_ms','shim_charge_m16_ms','area_mm2','pads_per_trap','ions_per_instruction','batches','hops'].map(metric).filter(Boolean);
  var t=document.createElement('table'); t.className='tbl'; var tr=document.createElement('tr');
  ['design','family','buildable'].concat(cols.map(function(m){ return m.name.split(' — ')[1]||m.name; })).forEach(function(h,i){ var th=document.createElement('th'); th.textContent=h; if (i>=3){ th.onclick=function(){ ysel.value=cols[i-3].k; draw(); }; th.title='rank by this'; if (cols[i-3].k===yk) th.style.color='#0b0b0b'; } tr.appendChild(th); }); t.appendChild(tr);
  var rows = ranked(yk).concat(DATA.filter(function(d){ return d.compiled && val(d,yk)==null; }), DATA.filter(function(d){ return !d.compiled; }));
  rows.forEach(function(d){ var r=document.createElement('tr'); if (SEL===d.key) r.className='sel';
    [shortName(d), d.family, d.compiled ? (d.numbers.buildable?'yes':(d.numbers.drc_clean?'no wells':'no')) : 'refused'].concat(cols.map(function(m){ return fmt(val(d,m.k),m); })).forEach(function(v){ var td=document.createElement('td'); td.textContent=v; r.appendChild(td); });
    r.onclick=function(){ select(d.key); }; t.appendChild(r); });
  w.appendChild(t); }
function setView(v){ VIEW=v; if (typeof pushState==='function') setTimeout(pushState,0); $('vchart').setAttribute('aria-pressed', v==='chart'); $('vtable').setAttribute('aria-pressed', v==='table'); $('chartwrap').hidden = v!=='chart'; $('legend').hidden = v!=='chart'; $('tablewrap').hidden = v!=='table'; if (v==='table') table(); }
ysel.onchange = function(){ var m=metric(ysel.value); logbox.checked=!!m.log; draw(); }; xsel.onchange=draw; logbox.onchange=draw;
$('vchart').onclick=function(){ setView('chart'); }; $('vtable').onclick=function(){ setView('table'); };
// a view is shareable: ?y=<measure>&x=<measure|rank>&log=0|1&sel=<key>
var Q = {}; try { location.search.replace(/^\?/,'').split('&').forEach(function(kv){ if(!kv) return; var i=kv.indexOf('='); Q[decodeURIComponent(kv.slice(0,i))]=decodeURIComponent(kv.slice(i+1)); }); } catch(e){}
ysel.value = metric(Q.y||'') ? Q.y : 'T_jones'; xsel.value = (Q.x==='rank' || metric(Q.x||'')) ? Q.x : 'rank';
logbox.checked = Q.log!=null ? Q.log==='1' : !!metric(ysel.value).log; draw(); if (Q.view==='table') setView('table');
function pushState(){ try { var q='?y='+ysel.value+'&x='+xsel.value+'&log='+(logbox.checked?1:0)+(SEL?'&sel='+SEL:'')+(VIEW==='table'?'&view=table':''); history.replaceState(null,'',q); } catch(e){} }
[ysel,xsel,logbox].forEach(function(el){ el.addEventListener('change', pushState); });
var ans = DATA.filter(function(d){ return d.key===Q.sel; })[0] || DATA.filter(function(d){ return d.answer; })[0]; if (ans) select(ans.key);
</script></body></html>
"""


# --------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=str(ROOT / "BBResults" / "studio"))
    ap.add_argument("--only", default=None, help="one catalogue key")
    ap.add_argument("--drafts", action="store_true", help="also build the entries marked draft")
    ap.add_argument("--max-frames", type=int, default=20000)
    ap.add_argument("--index-only", action="store_true",
                    help="rewrite index.html from manifest.json without re-rendering pages")
    ap.add_argument("--overlay-only", action="store_true",
                    help="re-inject the notes overlay into the rendered pages, then the index")
    a = ap.parse_args(argv)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    drafts = set() if a.drafts else {e["key"] for e in CATALOG if e.get("draft")}
    if a.index_only or a.overlay_only:
        rows = [r for r in resolve(json.loads((out_dir / "manifest.json").read_text(encoding="utf-8")))
                if r["key"] not in drafts]
        if a.overlay_only:
            # re-inject the overlay into pages already rendered: the notes and numbers
            # are in the manifest, and the studio render is the slow part
            marker = "<style>\n#bbnotes"
            for r in rows:
                if r.get("status") != "ok":
                    continue
                entry = next((e for e in CATALOG if e["key"] == r["key"]), None)
                if entry is not None:
                    r["numbers"] = _numbers(entry)
                    r["r10"] = r["numbers"].get("r10")
                page = out_dir / r["page"]
                src = page.read_text(encoding="utf-8")
                if marker in src:
                    src = src[: src.index(marker)] + "</body></html>"
                    page.write_text(src, encoding="utf-8", newline="")
                _inject(page, r, r["marks"], r["numbers"], "index.html")
                print(f"  overlay -> {page.name}")
            (out_dir / "manifest.json").write_text(json.dumps(to_repo(rows), indent=1, default=str), encoding="utf-8")
        print(f"-> {write_index(rows, out_dir)}")
        return 0
    rows = []
    if a.only and (out_dir / "manifest.json").exists():
        # one page rebuilt, the others kept: the manifest is the index's record
        rows = [r for r in resolve(json.loads((out_dir / "manifest.json").read_text(encoding="utf-8")))
                if r["key"] != a.only and r["key"] not in drafts]
    for entry in CATALOG:
        if a.only and entry["key"] != a.only:
            continue
        if entry["key"] in drafts:
            continue
        r = build_one(entry, out_dir, max_frames=a.max_frames)
        rows.append(r)
        print(f"  {entry['key']:30s} {r['status']:10s}"
              + (f" {r['instructions']:5d} instr  {len(r['marks']):2d} notes  "
                 f"{r['bytes'] / 1e6:4.1f} MB"
                 + (f"  RULES FAILED {r['rules_failed']}" if r.get('rules_failed') else "")
                 if r["status"] == "ok" else ""), flush=True)
    # the manifest first: it is the record the index is rebuilt from (`--index-only`),
    # so a fault in the index must not lose the pages' evidence
    rows.sort(key=lambda r: r["key"])
    manifest = out_dir / "manifest.json"
    manifest.write_text(json.dumps(to_repo(rows), indent=1, default=str), encoding="utf-8")
    print(f"-> {manifest}")
    print(f"-> {write_index(rows, out_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
