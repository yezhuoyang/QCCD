"""The BB board: every design that runs the board's round, as clickable studio pages.

Since 2026-11-02 the board is release bb144@2: Bravyi et al.'s syndrome round (one ancilla
per check, 288 ions, the depth-8 schedule of Fig. 5 of arXiv:2308.07915), compiled from the
release's circuit by the released qccdc, which keeps every qubit's gates in the circuit's
order.  Until then it held the q06 codesign study's 29 designs, all compiled from 24-ancilla
variants of the round; those programmes no longer run the board's circuit, so they left it
(the git history has them).  Shapes the compiler does not serve yet are listed as refusals.

One `qccd studio` page per design, opened on the verified programme with its circuit
beside it, so every instruction can be stepped, its cost read, its rule verdict seen, and
the QASM statement it discharges found.  An index page carries each design's claim, and
every claim links to the page and the STEP where it is visible.

    python Codesign/scripts/bb_studio.py -o BBResults/studio

Nothing here is a second renderer.  The pages are `qccd.viz.render.render_html` over the
same `(arch, programme, replay)` the campaign scored, the circuit join is
`qccd.ir.source_map` (which refuses if the certificate disagrees), and every number on the
index is measured on the programme the page shows (`_numbers`; until 2026-10-23 they were read
from the study's data files, which mixed two compiles on some rows).  What this adds is
the study's notes on the steps it drew conclusions from, passed to `render_html(notes=...)`:
the page template draws them, and no page is patched after it is written.
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
#: the board's programmes since 2026-11-02 (bb144@2): `<key>.arch.json` is the device as a
#: design is entered on the release (`qccd.workspace.tasks.board_device`), `<key>.qasm` the
#: release's circuit, `<key>.tsir.json`/`.qcert.json`/`.stages.json` what qccdc wrote, and
#: `<key>.cooled.tsir.json` that programme with cooling inserted (`insert_cooling.py --table
#: qccdsim_jones`, the release's one allowed transformation); `<key>.compile.json` records the
#: modes tried, exit codes, seconds and the compiler's sha256, `<key>.<mode>.log` its output
BB2 = BUILD / "bb2"
#: the released compiler every entry of the board was compiled with
QCCDC = "cd3efedbbc10"
#: what a refused device's compile was given: the release's own limit
TIME_LIMIT = "30 minutes (the release's compile_timeout_s, 1800 s)"
#: the words for qccdc's engines, as `<prefix>.stages.json` names them
ENGINE_WORDS = {"rotate": "rigid rotation (qccdc rotate)",
                "general": "the general router (qccdc compile)",
                "general-split": "the general router with split rounds (qccdc compile's fallback: "
                                 "an unroutable round runs in halves, the rest deferred)",
                "racetrack": "the race track (qccdc compile's fallback)",
                "conveyor": "the conveyor router (the data stand still; the ancillas of a loop "
                            "turn and hop together)"}
#: the colours of the families that are not on every board (`write_index`)
FAMHEX_MORE = {"conveyor sandwich": "#b5357f"}
#: the release every entry of this board is compiled from
BB_RELEASE = "bb144@2"

# --------------------------------------------------------------------------- catalogue

#: One entry per design on the board.  `prefix` names a compiled programme
#: (`<prefix>.cooled.tsir.json`, `.qcert.json`, and `.qasm` beside it unless `qasm` overrides);
#: an entry with no prefix is a device the compiler could not serve, and gets a page of the
#: device alone with the refusal as its lede: the compiler's own words and the time it had.
#: `"draft": True` keeps an entry off the board (pages, manifest, index) unless `--drafts`.
#:
#: SINCE 2026-11-02 THE BOARD IS RELEASE bb144@2 (the user: "for BBCode, we should use the order
#: designed by Bravyi", then "Ship with what compiles").  Every compiled entry was compiled from
#: the release's circuit (`<prefix>.qasm`, a copy of qccd/workspace/releases/bb144@2/circuit.qasm)
#: by the released qccdc cd3efedbbc10 as the workspace's compile job runs it -- `rotate` first on a
#: device with a closed loop, else `compile`, with no options -- and cooled by insert_cooling.py at
#: the rank table.  On the release's starter that gives the job's own cooled programme instruction
#: for instruction (the same cools in the same places, 1,000.77 ms / 1,043.67 ms replayed both
#: ways).  The devices are `qccd.workspace.tasks.board_device` of a template plus a generator: the
#: release's physics, the template's zones and control.  Which engine wrote each programme and
#: whether it keeps the circuit's order are read off its files (`_engine_and_order`), not typed.
#: The 29 entries before (the q06 study, compiled from 24-ancilla variants of bb144@1's round)
#: are in the git history with their claims.
CATALOG: tuple[dict, ...] = (
    # one dock per check: every check ion parks in a dock, the data ions ride the loop and
    # rigid rotation brings each to its checks in the schedule's order (qccdc rotate)
    {"key": "ring146_h2_d144", "family": "ring + docks",
     "title": "ring(146, 2, 144) with junction corners: a dock for every check -- the release's starter",
     "arch": BB2 / "ring146_h2_d144.arch.json", "prefix": BB2 / "ring146_h2_d144",
     "compiled": {"compiler": QCCDC, "date": "2026-11-02", "note": "qccdc rotate, then insert_cooling"},
     "claim": ("The release's starter. Each of the 144 check ions waits in a dock of its own, and the "
               "144 data ions ride a loop of 288 slots, one in every second slot. Rigid rotation turns "
               "the whole loop to bring each data ion past its checks' docks in the schedule's order: "
               "411 rotations, 11,640 hops, and 864 contacts in 411 batches -- about two a batch, "
               "since each check takes its CNOTs one at a time, in the schedule's order. The heating model "
               "asks for 411 cools. It is the programme the workspace's compile job writes for the "
               "starter, instruction for instruction.")},
    {"key": "ring145_h3_d144", "family": "ring + docks",
     "title": "ring(145, 3, 144) with junction corners: the same 288-slot loop and 144 docks, height 3",
     "arch": BB2 / "ring145_h3_d144.arch.json", "prefix": BB2 / "ring145_h3_d144",
     "compiled": {"compiler": QCCDC, "date": "2026-11-02", "note": "qccdc rotate, then insert_cooling"},
     "claim": ("The same loop of 288 slots and the same 144 docks, drawn on a rectangle of height 3. The "
               "compiler writes the same programme as for ring(146, 2, 144), instruction for instruction "
               "and cool for cool (411 rotations, 11,640 hops, 411 batches); what differs is the machine: "
               "where its corners turn, and its die.")},
    {"key": "ring144_h4_d144", "family": "ring + docks",
     "title": "ring(144, 4, 144) with junction corners: the same 288-slot loop and 144 docks, height 4",
     "arch": BB2 / "ring144_h4_d144.arch.json", "prefix": BB2 / "ring144_h4_d144",
     "compiled": {"compiler": QCCDC, "date": "2026-11-02", "note": "qccdc rotate, then insert_cooling"},
     "claim": ("The same loop and docks again, on a rectangle of height 4: once more the same programme, "
               "instruction for instruction and cool for cool, on a different machine. Drawn at height 4, "
               "some dock rails cross without a junction (V100 and V142 among them), so "
               "R21 refuses the machine and it is listed under Disqualified, never ranked.")},
    {"key": "ring218_h2_d144", "family": "ring + docks",
     "title": "ring(218, 2, 144) with junction corners: a roomier loop of 432 slots, 144 docks",
     "arch": BB2 / "ring218_h2_d144.arch.json", "prefix": BB2 / "ring218_h2_d144",
     "compiled": {"compiler": QCCDC, "date": "2026-11-02", "note": "qccdc rotate, then insert_cooling"},
     "claim": ("A roomier loop: 432 slots for the same 144 docks, so the data ions ride every third slot. "
               "The schedule keeps its 411 batches, but each rotation has farther to go: 18,885 hops "
               "instead of 11,640.")},
    # dock-free loops that gate on the loop itself, on the Cyclone template (4-ion traps):
    # rotation needs docks and refuses, so `compile` runs
    {"key": "cyc148_h2", "family": "loop without docks",
     "title": "ring(148, 2, 0) with junction corners: 292 traps of up to 4 ions, no docks",
     "arch": BB2 / "cyc148_h2.arch.json", "prefix": BB2 / "cyc148_h2",
     "compiled": {"compiler": QCCDC, "date": "2026-11-02", "note": "qccdc compile, then insert_cooling"},
     "claim": ("No docks: one loop of 292 traps that hold up to four ions each and can all gate, so a data "
               "ion and its check meet in a trap on the loop itself. Rotation needs docks and refuses; the "
               "general router serves it in 2,289 transport cycles, and the heating model asks for only "
               "7 cools.")},
    {"key": "cyc148_h3", "family": "loop without docks",
     "title": "ring(148, 3, 0) with junction corners: 294 traps of up to 4 ions, height 3, no docks",
     "arch": BB2 / "cyc148_h3.arch.json", "prefix": BB2 / "cyc148_h3",
     "compiled": {"compiler": QCCDC, "date": "2026-11-02", "note": "qccdc compile, then insert_cooling"},
     "claim": ("The 292-trap loop drawn on a rectangle of height 3, which adds a trap on each short side: "
               "294 traps. The general router serves it in 2,215 transport cycles, with 7 cools.")},
    {"key": "cyc120_h2", "family": "loop without docks",
     "title": "ring(120, 2, 0) with junction corners: 236 traps of up to 4 ions, no docks",
     "arch": BB2 / "cyc120_h2.arch.json", "prefix": BB2 / "cyc120_h2",
     "compiled": {"compiler": QCCDC, "date": "2026-11-02", "note": "qccdc compile, then insert_cooling"},
     "claim": ("The same kind of loop with 236 traps, so the ions start up to four to a trap. The general "
               "router still serves it directly, in 1,850 transport cycles, with 7 cools.")},
    {"key": "cyc100_h2", "family": "loop without docks",
     "title": "ring(100, 2, 0) with junction corners: 196 traps of up to 4 ions, no docks",
     "arch": BB2 / "cyc100_h2.arch.json", "prefix": BB2 / "cyc100_h2",
     "compiled": {"compiler": QCCDC, "date": "2026-11-02",
                  "note": "qccdc compile (split rounds), then insert_cooling"},
     "claim": ("Shorter still: 196 traps. The "
               "general router alone does not route it; its fallback does, by running each round it "
               "cannot route in halves and deferring the rest, still in the circuit's order: 1,694 "
               "transport cycles and 7 cools, after 1,462 s of compiling.")},
    # THE CONVEYOR SANDWICH (docs/PLAN-3d.md, Codesign/findings/q07-layers.md): data that never
    # move, between two conveyor loops of ancillas.  One graph built on three layers, on two,
    # and in a plane (`qccd.arch.sandwich`), each compiled by the machine's own router
    # (`qccd.compile.conveyor_router`), NOT by qccdc.  Their files are made here, from the
    # release (`make`, `_make_sandwich`), so these three rebuild on any checkout.
    # UNPUBLISHED (the user, 2026-11-10): `"restricted": "layers"` keeps an entry off the board
    # every visitor reads.  The folder holds two boards: `index.html` and `manifest.json` with
    # every entry, which the website serves only to readers granted `layers` (under /layers/),
    # and `index.public.html` and `manifest.public.json` without the restricted ones.
    {"key": "sandwich72_L3", "family": "conveyor sandwich", "restricted": "layers",
     "title": "conveyor sandwich on three layers: 72 data traps between a loop below and a loop above",
     "arch": BB2 / "sandwich72_L3.arch.json", "prefix": BB2 / "sandwich72_L3",
     "make": {"positions": 72, "layers": 3},
     "compiled": {"by": "the conveyor router", "date": "2026-11-10", "note": "then insert_cooling"},
     "claim": ("The data ions never move: L and R ion k share trap k. The X checks ride a conveyor loop "
               "on the layer below and the Z checks one on the layer above, with a riser from every slot "
               "to the trap it faces. A layer of CNOTs is a turn of a loop and a hop, whatever the number "
               "of checks, and each layer has a broadcast of its own, so the two loops turn in the same "
               "cycle: 118 transport cycles and 19 cools. Compiled by a router written for this machine, "
               "in under a second. Press 3D on its page to turn it.")},
    {"key": "sandwich72_L2", "family": "conveyor sandwich", "restricted": "layers",
     "title": "conveyor sandwich on two layers: one loop in the data traps' plane, one above",
     "arch": BB2 / "sandwich72_L2.arch.json", "prefix": BB2 / "sandwich72_L2",
     "make": {"positions": 72, "layers": 2},
     "compiled": {"by": "the conveyor router", "date": "2026-11-10", "note": "then insert_cooling"},
     "claim": ("The same machine with the X checks' loop in the data traps' own plane, inside their "
               "ring, and the Z checks' loop a layer above. The loops still turn together; the hops of "
               "the loop in the plane are a planar machine's: 151 transport cycles and 19 cools.")},
    {"key": "sandwich72_L1", "family": "conveyor sandwich", "restricted": "layers",
     "title": "conveyor sandwich in one plane: a loop inside the ring of 72 data traps and one outside",
     "arch": BB2 / "sandwich72_L1.arch.json", "prefix": BB2 / "sandwich72_L1",
     "make": {"positions": 72, "layers": 1},
     "compiled": {"by": "the conveyor router", "date": "2026-11-10", "note": "then insert_cooling"},
     "claim": ("The same machine as a planar one: nested racetracks. One broadcast, so the two loops "
               "take turns, and a hop from the top row and one from the bottom row are two waveforms: "
               "213 transport cycles and 19 cools. What the layers of the two entries above add is the "
               "difference from this one.")},
    # the shapes the compiler does not serve yet: listed, never ranked; the router work for
    # them is a separate follow-up (the user, 2026-11-02).  ring146_h2_d72 is the device the probe
    # of 2026-11-02 compiled (export_arch gives the same expanded device) with the same compiler
    # and circuit; its logs are `<key>.compile.log` / `<key>.rotate.log`.  The others were compiled
    # here, `<key>.compile.json` recording each try.
    # grid13x13 takes ring144_24v's control.channels and control.wiring (broadcast channels, WISE
    # shims: 57 DACs) in place of grid9x9's direct control (12,480 DACs against the task's 128).
    # The probe compiled the direct-wired one (`grid13x13_direct.probe.*`): out of time at 30 min,
    # with the same account up to split rounds.
    {"key": "grid13x13", "family": "lattice",
     "title": "grid13x13 with broadcast control, 312 traps -- not yet compiled by our compiler",
     "arch": BB2 / "grid13x13.arch.json",
     "refusal": ("not yet compiled by our compiler: qccdc compile gave up after 1,781 s, inside the "
                 f"release's {TIME_LIMIT}. Its own account: the general router's transport candidate "
                 "exceeds site capacity at T8_8v; rigid rotation does not apply (the device has no "
                 "closed loop with docks); the race track does not apply (no closed loop); split rounds "
                 "do not complete either."),
     "claim": ("A lattice of 312 traps joined by 165 junctions, with the default template's broadcast "
               "control (57 DACs; grid9x9's own direct control would need 12,480, against the task's "
               "128). It has room for the 288 ions, but the router's next move would overfill a trap, "
               "the fallbacks that need no free trap, rotation and the race track, need a closed loop, "
               "and splitting the rounds does not get through either.")},
    {"key": "ring146_h2_d72", "family": "ring + docks",
     "title": "ring(146, 2, 72) with junction corners: a dock for every second check -- not yet compiled",
     "arch": BB2 / "ring146_h2_d72.arch.json",
     "refusal": ("not yet compiled by our compiler: qccdc compile did not finish in "
                 f"{TIME_LIMIT}. Its own account: the general router left 881 operations "
                 "unrealised; rigid rotation does not apply (144 qubits need a dock but the device "
                 "has 72); the race track does not apply (a dock is off the loop, so the device is "
                 "not a single loop); it was running split rounds when the time ran out. qccdc "
                 "rotate refuses it at once: 144 qubits need a dock but the device has 72."),
     "claim": ("The starter's loop with half its docks: 72 for 144 checks. Rotation needs a dock for "
               "every check, and the general router leaves 881 of the round's 1,152 operations "
               "unplaced.")},
    {"key": "cyc76_h2", "family": "loop without docks",
     "title": "ring(76, 2, 0) with junction corners: 148 traps of up to 4 ions -- not yet compiled",
     "arch": BB2 / "cyc76_h2.arch.json",
     "refusal": ("not yet compiled by our compiler: qccdc compile gave up after 1,709 s, inside the "
                 f"release's {TIME_LIMIT}. Its own account: q237 cannot reach S80 from S12 within 156 "
                 "cycles; rigid rotation does not apply (the device has no closed loop with docks); the "
                 "race track does not apply (288 qubits but the loop has 148 sites, and a rigid shift "
                 "needs every ion alone in its site); split rounds do not complete either."),
     "claim": ("The shortest loop of the kind: 148 traps for 288 ions, two to a trap on average. With "
               "that little room the router cannot bring every ion to where its next gate is.")},
    # racetrack150 takes ring144_24v's control.wiring (WISE: demultiplexed shims) in place of
    # H2's per-trap shim drivers, which would need 332 DACs against the task's 128.  The device
    # compiled for 30 minutes was H2's own (`racetrack150.compiled_as.arch.json`); export_arch of the
    # two differs in control.wiring alone, whose scheme qccdc's compile never reads.
    {"key": "racetrack150", "family": "loop without docks",
     "title": "racetrack(150): an H2-style race track of 300 two-ion traps, no junctions -- not yet compiled",
     "arch": BB2 / "racetrack150.arch.json",
     "refusal": ("not yet compiled by our compiler: qccdc compile did not finish in "
                 f"{TIME_LIMIT}. Its own account: the general router: q143 cannot reach S45 from S144 "
                 "within 308 cycles; rigid rotation does not apply (the device has no closed loop "
                 "with docks); it was trying the race track (rigid shifts of the whole loop, swaps in "
                 "its zones) when the time ran out."),
     "claim": ("Quantinuum H2's shape, made long enough for the round: one loop of 300 traps of up to "
               "two ions, with no junctions at all, on H2's template but with demultiplexed shims (H2's "
               "own shim drivers would need 332 DACs here, against the task's 128). The 288 ions nearly "
               "fill it, and neither the router nor the race track finishes.")},
)

#: the name a dot wears on the plot and in the table: short, and distinct between designs
SHORT = {
    "ring146_h2_d144": "ring(146,2,144) junction",
    "ring145_h3_d144": "ring(145,3,144) junction",
    "ring144_h4_d144": "ring(144,4,144) junction",
    "ring218_h2_d144": "ring(218,2,144) junction",
    "cyc148_h2": "ring(148,2,0) 4-ion traps",
    "cyc148_h3": "ring(148,3,0) 4-ion traps",
    "cyc120_h2": "ring(120,2,0) 4-ion traps",
    "cyc100_h2": "ring(100,2,0) 4-ion traps",
    "grid13x13": "grid13x13",
    "ring146_h2_d72": "ring(146,2,72) junction",
    "cyc76_h2": "ring(76,2,0) 4-ion traps",
    "racetrack150": "racetrack(150)",
    "sandwich72_L3": "sandwich(72) on 3 layers",
    "sandwich72_L2": "sandwich(72) on 2 layers",
    "sandwich72_L1": "sandwich(72) in a plane",
}

#: each family's transport mechanism, and what the shape must have for it
FAMILY_NOTE = {
    "ring + docks": ("rigid rotation", "a closed loop AND a gate-capable dock for every check"),
    "loop without docks": ("the general router", "a closed loop of gate-capable traps that hold four ions, so two ions meet on the loop itself, and room enough to pass"),
    "lattice": ("none yet", "route diversity; the router does not finish this round on it yet"),
    "conveyor sandwich": ("its own router: a loop turns, then its ancillas hop",
                          "a conveyor loop for each kind of check, a slot of it facing every data trap"),
}


def _make_sandwich(entry: dict) -> None:
    """Write a conveyor sandwich's files where `build_one` reads an entry's: the device entered
    on the release, the release's circuit, the router's programme and certificate, and the
    programme with cooling inserted.  Nothing here needs qccdc, and the router is
    deterministic, so the files are made again on every build."""
    from qccd.arch.device import Architecture
    from qccd.arch.sandwich import conveyor_sandwich
    from qccd.compile.conveyor_router import COMPILER_NAME, compile_conveyor
    from qccd.compile.cooling import insert_cooling
    from qccd.workspace.tasks import board_device, find_release
    rel = find_release(BB_RELEASE)
    make = entry["make"]
    machine = conveyor_sandwich(make["positions"], layers=make["layers"], capacity=3)
    doc = board_device(machine.arch.to_json(expanded=True), rel)
    arch = Architecture.from_json(doc)
    prefix = str(entry["prefix"])
    Path(prefix).parent.mkdir(parents=True, exist_ok=True)
    Path(entry["arch"]).write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8", newline="\n")
    Path(prefix + ".qasm").write_text(rel.circuit_text(), encoding="utf-8", newline="\n")
    prog, cert = compile_conveyor(rel.circuit_text(), arch, 144, name="bb144")
    table = rel.manifest.get("rank_table", "qccdsim_jones")
    cooled = insert_cooling(prog, arch, corrected_model(table)).program
    cooled.meta.update(prog.meta)
    prog.save(prefix + ".tsir.json", indent=None)
    cooled.save(prefix + ".cooled.tsir.json", indent=None)
    Path(prefix + ".qcert.json").write_text(json.dumps(cert), encoding="utf-8")
    Path(prefix + ".stages.json").write_text(json.dumps({"engine": "conveyor", "compiler": COMPILER_NAME}),
                                             encoding="utf-8")


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
        by = c.get("by") or f"qccdc {c['compiler']}"
        out["compiled"] = f"compiled {c['date']} with {by}" + (f" ({c['note']})" if c.get("note") else "")
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
    out.update(_engine_and_order(entry))
    return out


def _engine_and_order(entry: dict) -> dict:
    """Which of qccdc's engines wrote the programme, and whether each qubit's operations keep
    the circuit's order -- both READ off the files, never typed.

    The engine is `<prefix>.stages.json`'s own `engine` (rotate, general, ...).  The order is
    `check_cert.check_program_order` on the certified programme and its certificate, the check
    the grader's semantics stage reports as `order`: for each qubit, no operation may start
    before the qubit's previous one in the circuit has finished, even where the two commute.
    Its count is the whole number of such operations (check_cert's JSON keeps six lines).
    The certificate's copy of the circuit is the release's circuit op for op (R10's binding).
    Empty for an entry with no compiled programme."""
    prefix = entry.get("prefix")
    if not prefix or not Path(str(prefix) + ".qcert.json").is_file():
        return {}
    import importlib.util
    import re
    out: dict = {}
    stages = Path(str(prefix) + ".stages.json")
    if stages.is_file():
        out["engine"] = json.loads(stages.read_text(encoding="utf-8")).get("engine")
    spec = importlib.util.spec_from_file_location("bb_studio_check_cert", BRIDGE / "check_cert.py")
    cc = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cc)
    probs = cc.check_program_order(json.loads(Path(str(prefix) + ".qcert.json").read_text(encoding="utf-8")),
                                   TSIR.load(str(prefix) + ".tsir.json"))
    total = next((int(m.group(1)) for p in probs for m in [re.match(r"\.\.\. (\d+) ops in all", p)] if m),
                 len(probs))
    out["order_violations"] = total
    if probs:
        out["order_first"] = probs[0][:240]
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


# --------------------------------------------------------------------------- notes

def study_notes(entry: dict, marks: list[dict], numbers: dict, index_href: str) -> dict:
    """The study's notes on a page, as `render_html(notes=...)` takes them: the facts the
    entry was judged by, its claim, a link back to the index, and the marked steps.  The page
    template draws them (qccd/viz/render.py); nothing is added to a page after it is written."""
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
    if numbers.get("engine"):
        facts.append("engine: " + ENGINE_WORDS.get(numbers["engine"], numbers["engine"]))
    if numbers.get("order_violations") == 0:
        facts.append("gate order kept: no operation starts before an earlier one on its qubit")
    elif numbers.get("order_violations"):
        facts.append(f"GATE ORDER BROKEN: {numbers['order_violations']} operations start before an "
                     "earlier one on their qubit")
    if numbers.get("r10"):
        facts.append("R10: Lean checker + tableau")
    elif numbers.get("r10") is False:
        facts.append((numbers.get("r10_verdict") or "R10 FAILED: the checker rejects the certificate")
                     .lstrip("-> ").strip())
    return {"title": entry["key"], "index": index_href, "facts": facts,
            "claim": entry.get("claim") or entry.get("refusal") or "", "marks": marks}


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
    if entry.get("make"):
        _make_sandwich(entry)
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
                template_stems="*", max_frames=max_frames,
                notes=study_notes(entry, marks, numbers, "index.html"))
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
                subtitle: str | None = None, intro: str | None = None,
                name: str = "index.html") -> Path:
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
    # a family that is on this page only: its colour is written here and nowhere else, so a
    # board without it does not name it
    more = "".join(f"'{f}':'{c}'," for f, c in FAMHEX_MORE.items() if any(r.get("family") == f for r in rows))
    page = page.replace("__FAMHEX_MORE__", more)
    dq = [r for r in rows if r.get("status") == "ok" and (r.get("rules_failed") or r.get("over_budget"))]
    page = page.replace("\n</main>", _disqualified_html(dq) + "\n</main>", 1)
    if title:
        page = page.replace("BB [[144,12,12]] on QCCD — every design, ranked", title)
    if subtitle:
        page = page.replace("One syndrome round of the gross code compiled onto every architecture the study reached.", subtitle)
    if intro:
        # the board's own account of itself, under the subtitle and above the controls (HTML)
        page = page.replace('\n<div class="controls">', f'\n<div class="intro">{intro}</div>\n<div class="controls">', 1)
    p = out_dir / name
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
            f'break{"s" if n == 1 else ""} at least one rule or need{"s" if n == 1 else ""} more hardware than the task '
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
.intro{color:var(--ink2);max-width:80ch;margin:-8px 0 16px;font-size:13.5px}.intro p{margin:0 0 8px}.intro b{color:var(--ink);font-weight:600}
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
var FAMHEX = {'ring + docks':'#2a78d6','lattice':'#eb6834','torus':'#1baf7a','random graph':'#4a3aa7',__FAMHEX_MORE__'two loops':'#8a8985'};
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
  if (ylo===yhi){ if (ylo===0){ yhi=1; } else { ylo*=0.9; yhi*=1.1; } }
  if (useLog && ylo<=0) useLog=false;
  var ypad = useLog ? 1 : (yhi-ylo)*0.1;
  var y0 = useLog ? ylo/1.4 : (ylo-ypad < 0 && ylo>=0 ? 0 : ylo-ypad), y1 = useLog ? yhi*1.4 : yhi+ypad;
  var Y = function(v){ return useLog ? T + ph - (Math.log(v)-Math.log(y0))/(Math.log(y1)-Math.log(y0))*ph : T + ph - (v-y0)/(y1-y0)*ph; };
  var X;
  if (!xm){ X = function(d,i){ return L + 24 + (rows.length>1 ? i/(rows.length-1) : 0.5)*(pw-48); }; }
  else { var xv = rows.map(function(d){ return val(d,xk); }), xlo=Math.min.apply(null,xv), xhi=Math.max.apply(null,xv); if (xlo===xhi){ if (xlo===0){ xhi=1; } else { xlo*=0.9; xhi*=1.1; } }
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
    // which engine wrote the programme, and whether each qubit keeps the circuit's order (check_cert)
    if (n.engine) tb.appendChild(row('compiler engine', n.engine));
    if (n.order_violations != null) tb.appendChild(row('gate order (each qubit, as the circuit writes it)', n.order_violations === 0 ? 'kept' : n.order_violations + ' operations out of order', n.order_violations === 0 ? 'ok' : 'bad'));
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


BB_TITLE = "BB [[144,12,12]] on QCCD — Bravyi's syndrome round, every design ranked"
BB_SUBTITLE_PUBLIC = ("One syndrome round of the [[144,12,12]] bivariate-bicycle code in Bravyi et al.'s "
                      "schedule (release bb144@2), on every design our compiler serves today.")
BB_SUBTITLE = ("One syndrome round of the [[144,12,12]] bivariate-bicycle code in Bravyi et al.'s "
               "schedule (release bb144@2), on every design our compiler serves today, and on one "
               "machine that has a router of its own.")


def board_intro(rows: list[dict]) -> str:
    """The board's account of itself, above the plot: what is compiled, what is ranked, and
    which shapes do not compile yet.  Every count and name is read off the rows."""
    ok = [r for r in rows if r.get("status") == "ok"]
    compiled = [r for r in ok if r.get("prefix")]
    ranked = [r for r in compiled if not r.get("rules_failed") and not r.get("over_budget")]
    order = {e["key"]: i for i, e in enumerate(CATALOG)}
    refused = sorted((r for r in ok if not r.get("prefix")), key=lambda r: order.get(r["key"], 99))
    kept = [r for r in compiled if (r.get("numbers") or {}).get("order_violations") == 0]
    lean = [r for r in compiled if (r.get("numbers") or {}).get("r10") is True]
    fams = sorted({r.get("family") for r in ranked})
    words = {"ring + docks": "rings with a dock for every check, which rigid rotation serves",
             "loop without docks": "loops without docks whose traps hold four ions, which the general "
                                   "router serves",
             "conveyor sandwich": "the conveyor sandwich on three layers, on two and in a plane, which "
                                  "its own router serves"}
    n_ = lambda k: {1: "One", 2: "Two", 3: "Three"}.get(k, str(k))
    own = [r for r in compiled if (r.get("numbers") or {}).get("engine") == "conveyor"]
    theirs = ("" if not own else
              f" {n_(len(own))} of them, the conveyor sandwich's, come from a router written for that one "
              "machine instead (its data ions stand still and its check ions ride two conveyor loops); "
              "it keeps the same order and writes the same certificate, and its machines may be built "
              "in layers, each layer with a broadcast of its own.")
    n = lambda k, one, many: f"{k} {one if k == 1 else many}"
    out = [
        "<p><b>What is compiled.</b> Bravyi et al.'s depth-8 round (Fig. 5 of arXiv:2308.07915) as "
        "published: one ancilla per check, so 144 data ions and 144 check ions, 288 in all, and its 864 "
        f"CNOTs in the schedule's order. Every programme here was compiled from that circuit"
        + (f", {len(compiled) - len(own)} of them" if own else "") + " by the "
        f"released compiler, qccdc {QCCDC}, which keeps each qubit's gates in the circuit's order; the "
        f"checker confirms it on {len(kept)} of {len(compiled)}: no operation starts before the one "
        "before it on the same qubit has finished." + theirs + " Cooling is inserted where the heating model needs "
        "it, the one change the release allows, and R10 is decided by the proved Lean checker and the "
        f"stabilizer tableau (passed on {len(lean)} of {len(compiled)}).</p>",
        f"<p><b>What is ranked.</b> {n(len(ranked), 'design', 'designs')} whose programme passes every "
        "rule on the replay and whose machine fits the task's hardware budget"
        + (": " + "; ".join(html.escape(words.get(f, f)) for f in fams) if fams else "")
        + ". " + _fastest(ranked) + "Pick a measure to rank by, and click a dot for its numbers and the "
        "steps where each claim can be checked."
        + (f" {n(len(compiled) - len(ranked), 'more design runs', 'more designs run')} the round but "
           f"{'breaks a rule or needs' if len(compiled) - len(ranked) == 1 else 'break a rule or need'} "
           "more hardware than the task allows: listed under Disqualified, below, never ranked."
           if len(compiled) > len(ranked) else "") + "</p>",
    ]
    if refused:
        names = ", ".join(html.escape(r.get("short") or SHORT.get(r["key"], r["title"])) for r in refused)
        out.append(f"<p><b>What does not compile yet.</b> {names}: our compiler does not compile "
                   f"{'it' if len(refused) == 1 else 'them'} within the release's 30 minutes, and "
                   f"{'its page gives' if len(refused) == 1 else 'each one&rsquo;s page gives'} the "
                   f"compiler's own reason. {'It is' if len(refused) == 1 else 'They are'} listed as "
                   "refused, at the right of the plot, and never ranked; the router work for these "
                   "shapes is a separate follow-up.</p>")
    return "".join(out)


def _fastest(ranked: list[dict]) -> str:
    """The fastest ranked round on the jones table, and each family's fastest, read off the rows."""
    timed = [r for r in ranked if isinstance((r.get("numbers") or {}).get("T_jones"), (int, float))]
    if not timed:
        return ""
    name = lambda r: html.escape(r.get("short") or SHORT.get(r["key"], r["title"]))
    t = lambda r: f"{r['numbers']['T_jones']:,.1f} ms"
    def fastest(rs):   # every design at the least time: a tie is said as one
        lo = min(r["numbers"]["T_jones"] for r in rs)
        tied = [r for r in rs if abs(r["numbers"]["T_jones"] - lo) < 1e-6]
        return " and ".join(name(r) for r in tied) + ", " + t(tied[0]), tied[0]
    words, best = fastest(timed)
    out = f"The fastest round on the jones table: {words}"
    for fam in sorted({r.get("family") for r in timed} - {best.get("family")}):
        out += f"; among the {html.escape(fam)} designs, " + fastest([r for r in timed if r.get("family") == fam])[0]
    return out + ". "


def write_board_index(rows: list[dict], out_dir: Path) -> Path:
    """The BB board's two indexes (`write_index` with the board's title, subtitle and intro):
    `index.html` with every entry, and `index.public.html` with the entries any visitor may
    see, each with the manifest it is drawn from.  Returns the first."""
    public = [r for r in rows if not r.get("restricted")]
    (out_dir / "manifest.public.json").write_text(json.dumps(to_repo(public), indent=1, default=str),
                                                  encoding="utf-8")
    write_index(public, out_dir, title=BB_TITLE, subtitle=BB_SUBTITLE_PUBLIC, intro=board_intro(public),
                name="index.public.html")
    return write_index(rows, out_dir, title=BB_TITLE, subtitle=BB_SUBTITLE, intro=board_intro(rows))


# --------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=str(ROOT / "BBResults" / "studio"))
    ap.add_argument("--only", default=None, help="one catalogue key")
    ap.add_argument("--drafts", action="store_true", help="also build the entries marked draft")
    ap.add_argument("--max-frames", type=int, default=20000)
    ap.add_argument("--index-only", action="store_true",
                    help="rewrite index.html from manifest.json without re-rendering pages")
    a = ap.parse_args(argv)
    out_dir = Path(a.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    drafts = set() if a.drafts else {e["key"] for e in CATALOG if e.get("draft")}
    if a.index_only:
        rows = [r for r in resolve(json.loads((out_dir / "manifest.json").read_text(encoding="utf-8")))
                if r["key"] not in drafts]
        print(f"-> {write_board_index(rows, out_dir)}")
        return 0
    rows = []
    if a.only and (out_dir / "manifest.json").exists():
        # one page rebuilt, the others kept: the manifest is the index's record
        # (a key the catalogue no longer names is not kept: its design has left the board)
        keys = {e["key"] for e in CATALOG}
        rows = [r for r in resolve(json.loads((out_dir / "manifest.json").read_text(encoding="utf-8")))
                if r["key"] != a.only and r["key"] not in drafts and r["key"] in keys]
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
    print(f"-> {write_board_index(rows, out_dir)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
