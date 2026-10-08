"""The papers we reproduce, what we reproduce from each, and against which number.

One record per paper.  A record says where the paper's machine model is written down
(`model`: the profile and duration law in `models.py`), which outputs of its artifact we
replay (`runs`: a file under `Reproduce/<key>/artifact/`, the importer that reads it, the
circuit it must implement), and every number we compare against, with the place in the
paper or the artifact it was read from (`expect`).  `python -m qccd.repro check` turns the
records into `Reproduce/<key>/results.json`; the website reads those files and nothing
else, so a number on the page is always one a command printed.

Labels on `expect`:
    "tool"   the artifact printed it for this exact configuration (an exact match is
             required: same machine model, same arithmetic)
    "paper"  the paper prints it, or plots it with its data in the source; matched to the
             paper's precision, and a miss is reported, never hidden
"""

from __future__ import annotations

__all__ = ["PAPERS", "paper"]

#: verdict words used everywhere the page reports a comparison
EXACT, CLOSE, DIFFERS = "exact", "close", "differs"


_BENCH = {"bv64": "BV-64", "qaoa6420": "QAOA-64 (20 layers)", "adder": "Adder (32-bit)",
          "qft64": "QFT-64", "sup64": "Supremacy-64"}


def _qccdsim_label(name: str) -> str:
    bench, dev, cap, gate, swap, mapper = name.split("_")
    return (f"{_BENCH.get(bench, bench)} on {dev}, capacity {cap[1:]}, "
            f"{'program-order' if mapper == 'PO' else mapper} mapper"
            + (", ion swaps" if swap == "IS" else ""))


#: Muzzle the Shuttle's five benchmarks: (run key, its circuit file, label, the paper's
#: QCCDSim shuttle count, the paper's own) -- Table II
_MUZZLE = [
    ("supremacy", "sup64_new.qasm", "Supremacy-64", 365, 223),
    ("qaoa", "maxcut.qasm", "QAOA MaxCut-64", 1552, 957),
    ("sqrt", "square_root_clean_new.qasm", "SquareRoot-78", 717, 355),
    ("qft", "qft64_trimmed.qasm", "QFT-64", 241, 196),
    ("quadform", "quadratic_form_clean.qasm", "QuadraticForm-64", 228, 164),
]


def _qccdsim_run(name: str, circuit: str, paper: dict | None = None, **extra) -> dict:
    return {"id": name, "label": _qccdsim_label(name), "file": f"{name}.schedule.json.gz",
            "importer": "qccdsim",
            "profile": "QCCDSIM", "law": "qccdsim", "circuit": {"kind": "qasm", "file": circuit},
            "expect": {"tool": {"makespan_us": "metrics.Program Finish",
                                "fidelity": "metrics.Fidelity"},
                       **({"paper": paper} if paper else {})},
            **extra}


PAPERS: list[dict] = [
    # ------------------------------------------------------------------ QCCDSim
    {
        "key": "murali2020",
        "short": "QCCDSim",
        "title": "Architecting Noisy Intermediate-Scale Trapped Ion Quantum Computers",
        "authors": ["Prakash Murali", "Dripto M. Debroy", "Kenneth R. Brown", "Margaret Martonosi"],
        "venue": "ISCA 2020", "year": 2020, "arxiv": "2004.04706",
        "artifact": {"url": "https://github.com/prakashmurali/QCCDSim", "commit": "b01b0b3",
                     "license": None,
                     "note": "No license file: we ran it locally and publish only the schedules it "
                             "produced, never its code."},
        "what": "Two six-trap devices, a line (L6) and a two-by-three grid (G2x3), trap capacity "
                "swept from 14 to 34 ions, six NISQ benchmarks; the simulator every later paper "
                "in this list builds on.",
        "model": {"profile": "QCCDSIM", "law": "qccdsim"},
        "figures": [{"run": "bv64_L6_c14_FM_GS_Greedy", "layout": "qccdsim_linear",
                     "caption": "L6: six traps on a line, a degree-2 junction between neighbours"},
                    {"run": "bv64_G2x3_c14_FM_GS_Greedy", "layout": "qccdsim_g2x3",
                     "caption": "G2x3: six single-port traps on a three-junction bus"}],
        "runs": [
            _qccdsim_run("bv64_L6_c14_FM_GS_Greedy", "bv64.qasm",
                         {"makespan_us": [13270, "Fig. 6(a), plotted (vector data in the source)"],
                          "fidelity": [0.9776, "Fig. 6(c), plotted"]}),
            _qccdsim_run("bv64_L6_c20_FM_GS_Greedy", "bv64.qasm",
                         {"makespan_us": [18300, "Fig. 6(a)"], "fidelity": [0.9697, "Fig. 6(c)"]}),
            _qccdsim_run("bv64_G2x3_c14_FM_GS_Greedy", "bv64.qasm",
                         {"makespan_us": [14350, "Fig. 7(b)"], "fidelity": [0.9775, "Fig. 7(b)"]}),
            _qccdsim_run("qaoa6420_L6_c14_FM_GS_PO", "qaoa6420.qasm",
                         {"makespan_us": [162400, "Fig. 6(a)"], "fidelity": [0.4758, "Fig. 6(d)"]}),
            _qccdsim_run("qaoa6420_L6_c20_FM_GS_PO", "qaoa6420.qasm",
                         {"makespan_us": [254100, "Fig. 6(a)"], "fidelity": [0.3827, "Fig. 6(d)"]}),
            _qccdsim_run("qaoa6420_G2x3_c14_FM_GS_PO", "qaoa6420.qasm",
                         {"makespan_us": [191800, "Fig. 7(d)"], "fidelity": [0.4004, "Fig. 7(d)"]}),
            _qccdsim_run("adder_L6_c14_FM_GS_PO", "adder.qasm",
                         {"makespan_us": [90130, "Fig. 6(a)"], "fidelity": [0.803, "Fig. 6(c)"]}),
            _qccdsim_run("adder_L6_c20_FM_GS_PO", "adder.qasm",
                         {"makespan_us": [126500, "Fig. 6(a)"], "fidelity": [0.7648, "Fig. 6(c)"]}),
            _qccdsim_run("qft64_L6_c14_FM_GS_Greedy", "qft64.qasm",
                         {"makespan_us": [333100, "Fig. 6(a)"], "fidelity": [9.701e-4, "Fig. 6(e)"]}),
            _qccdsim_run("qft64_L6_c20_FM_GS_Greedy", "qft64.qasm",
                         {"makespan_us": [550600, "Fig. 6(a)"], "fidelity": [1.295e-3, "Fig. 6(e)"]}),
            _qccdsim_run("sup64_L6_c20_FM_GS_PO", "sup64.qasm",
                         {"makespan_us": [223300, "Fig. 6(a)"], "fidelity": [0.1563, "Fig. 6(d)"]}),
            _qccdsim_run("bv64_L6_c14_FM_IS_Greedy", "bv64.qasm",
                         {"makespan_us": [21630, "Fig. 8(h), ion swap"], "fidelity": [0.9813, "Fig. 8(b)"]}),
        ],
    },
    # ------------------------------------------------------------------- Muzzle
    {
        "key": "saki2022",
        "short": "Muzzle the Shuttle",
        "title": "Muzzle the Shuttle: Efficient Compilation for Multi-Trap Trapped-Ion Quantum Computers",
        "authors": ["Abdullah Ash Saki", "Rasit Onur Topaloglu", "Swaroop Ghosh"],
        "venue": "DATE 2022", "year": 2022, "arxiv": "2111.07961",
        "artifact": {"url": "https://github.com/ashsaki/MTS-QCCD-Compiler", "commit": "489a097",
                     "license": "Apache-2.0"},
        "what": "QCCDSim's six-trap line (L6) with 15 ions per trap (+2), five benchmarks; the "
                "metric is the number of shuttles (adjacent-trap moves).",
        "model": {"profile": "QCCDSIM_PHYSICAL", "law": "qccdsim"},
        "figures": [{"run": "mts-sqrt-seed0", "layout": "qccdsim_linear",
                     "caption": "L6 with 15 ions per trap (+2 for traffic)"}],
        "runs": [
            *[{"id": f"qccdsim-{b}", "file": f"qccdsim-{b}.schedule.json.gz", "importer": "qccdsim",
               "profile": "QCCDSIM", "law": "qccdsim", "circuit": {"kind": "qasm", "file": f},
               "expect": {"tool": {"moves": "metrics.OPCOUNTS.Move",
                                   "makespan_us": "metrics.Program Finish"},
                          "paper": {"moves": [base, "Table II, [murali-ti] column", "printed"]}},
               "label": f"{lab}: the QCCDSim baseline"}
              for b, f, lab, base, _ in _MUZZLE],
            *[{"id": f"mts-{b}-seed0", "file": f"mts-{b}-seed0.schedule.json.gz", "importer": "qccdsim",
               "profile": "QCCDSIM_PHYSICAL", "law": "qccdsim", "circuit": {"kind": "qasm", "file": f},
               "expect": {"tool": {"moves": "metrics.OPCOUNTS.Move",
                                   "makespan_us": "metrics.Program Finish",
                                   "fidelity": "metrics.Fidelity"},
                          "paper": {"moves": [this, "Table II, This Work column", "printed"]}},
               "label": f"{lab}: Muzzle the Shuttle"}
              for b, f, lab, _, this in _MUZZLE],
        ],
    },
    # ------------------------------------------------------------------ Cyclone
    {
        "key": "khan2026cyclone",
        "short": "Cyclone",
        "title": "Cyclone: Designing Efficient and Highly Parallel QCCD Architectural Codesigns "
                 "for Fault Tolerant Quantum Memory",
        "authors": ["Sahil Khan", "Abhinav Anand", "Kenneth R. Brown", "Jonathan M. Baker"],
        "venue": "HPCA 2026", "year": 2026, "arxiv": "2511.15910",
        "artifact": {"url": "https://github.com/sahilkhan123/Cyclone", "commit": "bb6b551",
                     "license": "MIT"},
        "what": "A ring of traps where every ancilla moves one trap per step past stationary data, "
                "against a QCCDSim grid, for hypergraph-product and bivariate-bicycle codes.",
        "model": {"profile": "CYCLONE", "law": "cyclone"},
        "figures": [{"run": "bb144_cyclone", "layout": "ring",
                     "caption": "Cyclone for BB [[144,12,12]]: 72 traps on a ring, one ancilla each"},
                    {"run": "bb72_baseline_Gx9_c5", "layout": "qccdsim_nxn", "layout_args": {"N": 9},
                     "caption": "the baseline grid for BB [[72,12,6]]: 81 traps between rows of junctions"}],
        "runs": [
            *[{"id": f"{code}_cyclone{fix}", "file": f"{code}_cyclone{fix}.schedule.json.gz",
               "importer": "cyclone", "profile": "CYCLONE", "law": "cyclone",
               "circuit": {"kind": "pairs", "file": f"{code}_true_round.json"},
               "expect": {"tool": {"makespan_us": ["metrics", "FINAL TIMINGS QLDPC", 0]},
                          **({"paper": {"makespan_us": [123140, "Fig. 6, printed", "printed"]}}
                             if (code, fix) == ("hgp225", "") else {})},
               "label": f"{label}, Cyclone{' with the Z checks fixed' if fix else ', as published'}"}
              for code, label in (("hgp225", "HGP [[225,9,6]]"), ("hgp375", "HGP [[375,15,6]]"),
                                  ("hgp625", "HGP [[625,25,8]]"))
              for fix in ("", "_zfix")],
            {"id": "hgp225_baseline_Gx15_c5", "file": "hgp225_baseline_Gx15_c5.schedule.json.gz",
             "importer": "qccdsim", "profile": "QCCDSIM", "law": "qccdsim",
             "circuit": {"kind": "qasm", "file": "QLPDC[[225,9,6]].qasm"},
             "expect": {"tool": {"makespan_us": ["metrics", "Program Finish:"]},
                        "paper": {"makespan_us": [502675, "Fig. 6, printed", "printed"]}},
             "label": "HGP [[225,9,6]], the baseline grid (QCCDSim, 15x15 traps, capacity 5)"},
            *[{"id": f"{code}_cyclone", "file": f"{code}_cyclone.schedule.json.gz",
               "importer": "cyclone", "profile": "CYCLONE", "law": "cyclone",
               "circuit": {"kind": "pairs", "file": f"{code}_true_round.json"},
               "expect": {"tool": {"makespan_us": ["metrics", "TIMINGS"]},
                          "paper": {"makespan_us": [us, "the time behind Fig. 14 (the artifact's result files)"]}},
               "label": f"{label}, Cyclone"}
              for code, label, us in (("bb72", "BB [[72,12,6]]", 42280), ("bb90", "BB [[90,8,10]]", 52450),
                                      ("bb144", "BB [[144,12,12]]", 81860))],
            *[{"id": f"{code}_baseline_Gx{g}_c5", "file": f"{code}_baseline_Gx{g}_c5.schedule.json.gz",
               "importer": "qccdsim", "profile": "QCCDSIM", "law": "qccdsim",
               "circuit": {"kind": "qasm", "file": f"QLPDC[[{nk}]].qasm"},
               "expect": {"tool": {"makespan_us": ["metrics", "Program Finish:"]},
                          "paper": {"makespan_us": [us, "the artifact's recorded output, plotted in Fig. 19"]}},
               "label": f"BB [[{nk}]], the baseline grid ({g}x{g} traps, capacity 5)"}
              for code, g, nk, us in (("bb72", 9, "72,12,6", 164520), ("bb90", 10, "90,8,10", 188595),
                                      ("bb144", 12, "144,12,12", 270145))],
        ],
    },
    # -------------------------------------------------------------------- Jones
    {
        "key": "jones2025",
        "short": "Surface-code QCCD",
        "title": "Architecting Scalable Trapped Ion Quantum Computers using Surface Codes",
        "authors": ["Scott Jones", "Prakash Murali"],
        "venue": "ASPLOS 2026", "year": 2026, "arxiv": "2510.23519",
        "artifact": {"url": "https://github.com/scottjones03/PartIIProject", "commit": "d235a22",
                     "license": None,
                     "note": "The README says MIT but the repository has no license file: we ran it "
                             "locally and publish only the schedules it produced."},
        "what": "A compiler for surface-code rounds on grid, linear and switch QCCD devices, and a "
                "design-space study of trap capacity, wiring and logical error.",
        "model": {"profile": "JONES", "law": "jones"},
        "figures": [{"run": "grid_rot_d3_c2", "layout": "recorded",
                     "caption": "the padded grid for d = 3: a trap on every lattice edge, capacity 2"}],
        "runs": [
            {"id": f"grid_rot_d{d}_c2", "file": f"grid_rot_d{d}_c2.schedule.json.gz",
             "importer": "jones", "profile": "JONES", "law": "jones",
             "circuit": {"kind": "stim", "file": f"rotated_memory_z_d{d}_r1.stim"},
             "expect": {"tool": {"last_start_us": [
                 "metrics", "time for operations (us) = max(parallelOpsMap.keys())*1e6 [start of last step]"]},
                        "paper": {"last_start_us": [paper_us, "Table 2", "printed"]}},
             "label": f"rotated surface code d={d}, one round, grid, capacity 2"}
            for d, paper_us in ((2, 4055), (3, 4085), (6, 4085), (12, 4085))
        ] + [
            {"id": f"linear_rep_d3_c{c}", "file": f"linear_rep_d3_c{c}.schedule.json.gz",
             "importer": "jones", "profile": "JONES", "law": "jones",
             "circuit": {"kind": "stim", "file": f"repetition_memory_d3_c{c}.stim"},
             "expect": {"tool": {"last_start_us": [
                 "metrics", "time for operations (us) = max(parallelOpsMap.keys())*1e6 [start of last step]"]},
                        "paper": {"last_start_us": [us, "Table 2", "printed"]}},
             "label": f"repetition code d=3, linear, capacity {c}"}
            for c, us in ((2, 1535), (3, 1390), (4, 1505))
        ] + [
            {"id": "switch_rot_d3_c2", "file": "switch_rot_d3_c2.schedule.json.gz",
             "importer": "jones", "profile": "JONES", "law": "jones",
             "circuit": {"kind": "stim", "file": "rotated_memory_z_d3_r1.stim"},
             "expect": {"tool": {"last_start_us": [
                 "metrics", "time for operations (us) = max(parallelOpsMap.keys())*1e6 [start of last step]"]},
                        "paper": {"last_start_us": [5325, "Table 2", "printed"]}},
             "label": "rotated surface code d=3, switch, capacity 2 (current code)",
             "no_studio": "the switch joins every pair of junctions by two parallel segments, an all-to-all junction network; our language describes surface traps, which cannot hold it, so it is replayed but not drawn"},
            {"id": "switch_rot_d3_c2_at6d4b467", "file": "switch_rot_d3_c2_at6d4b467.schedule.json.gz",
             "importer": "jones", "profile": "JONES", "law": "jones_6d4b467",
             "circuit": {"kind": "stim", "file": "rotated_memory_z_d3_r1.stim"},
             "expect": {"tool": {"last_start_us": [
                 "metrics", "time for operations (us) = max(parallelOpsMap.keys())*1e6 [start of last step]"]},
                        "paper": {"last_start_us": [5325, "Table 2", "printed"]}},
             "label": "the same, with the code as of 2024 (junction entry and exit 100 µs each)",
             "no_studio": "the switch joins every pair of junctions by two parallel segments, an all-to-all junction network; our language describes surface traps, which cannot hold it, so it is replayed but not drawn"},
        ],
    },
    # -------------------------------------------------------------------- TISCC
    {
        "key": "leblond2023",
        "short": "TISCC",
        "title": "TISCC: A Surface Code Compiler and Resource Estimator for Trapped-Ion Processors",
        "authors": ["Tyler LeBlond", "Justin G. Lietz", "Christopher M. Seck", "Ryan S. Bennink"],
        "venue": "SC-W 2023", "year": 2023, "arxiv": "2311.10687",
        "artifact": {"url": "https://github.com/ORNL-QCI/TISCC", "commit": "1212aec",
                     "license": "UT-Battelle open source license (BSD-style)"},
        "what": "A grid of trapping zones and X-junctions, one ion per zone; surface-code patches "
                "compiled to timed native operations.",
        "model": {"profile": "TISCC", "law": "tiscc"},
        "figures": [{"run": "idle_551", "layout": "tiscc", "layout_args": {"ncols": 6},
                     "caption": "one d = 5 patch: 36 cells of M O M J M O M, one ion per zone"}],
        "runs": [
            {"id": "idle_551", "file": "idle_551.spec", "importer": "tiscc",
             "importer_args": {"nrows": 6, "ncols": 6}, "profile": "TISCC", "law": "tiscc",
             "circuit": {"kind": "pairs", "file": "idle_551.zz.json"},
             "expect": {"tool": {"makespan_us": 9613.0}},
             "label": "one d=5 error-correction round (TISCC -x 5 -z 5 -t 1 -o idle)"},
            {"id": "idle_661", "file": "idle_661.spec", "importer": "tiscc",
             "importer_args": {"nrows": 8, "ncols": 8}, "profile": "TISCC", "law": "tiscc",
             "circuit": {"kind": "pairs", "file": "idle_661.zz.json"},
             "label": "one d=6 round"},
        ],
    },
]


#: Notes on a paper, each one established by running its artifact (or replaying what it
#: produced) -- never by reading alone.  Written to be read by the paper's authors: what we
#: ran, what we saw, where to look.  No adjectives.
NOTES: dict[str, list[dict]] = {
    "murali2020": [
        {"title": "Which mapper and which QFT the figures use",
         "text": "The simulator has two mappers. The BV and QFT curves match its Greedy mapper "
                 "(BV-64 on L6 at capacity 14: 13,274 µs, against 23,796 µs with the other); the "
                 "QAOA, Adder and Supremacy curves match its program-order mapper. The paper "
                 "describes one greedy mapper. The QFT curves match the inverse-QFT gate order "
                 "(each controlled phase as two CNOTs); the textbook forward order gives 360,600 µs "
                 "at capacity 14 against the plotted 333,100. With these choices, 58 of the 58 "
                 "configurations we ran (every benchmark on L6 at capacities 14 to 34, and the "
                 "G2x3 points) agree with the plotted values within 0.05 %."},
        {"title": "Chain order after a gate swap",
         "text": "When a split needs a gate swap, the simulator removes the departing ion from its "
                 "place and keeps the end ion at the end. Physically the swap moves the end ion's "
                 "state into the departing ion's place. Of the figure configurations we replayed, "
                 "only QAOA on G2x3 is affected: under the physical rule 8 of its splits would leave "
                 "from a different place than the one the simulator charged. The difference grows "
                 "with gate swaps: 18 splits in the program-order BV-64 run at capacity 14, and "
                 "hundreds in the simulator's runs of Muzzle the Shuttle's benchmarks."},
    ],
    "saki2022": [
        {"title": "The published code and the paper's table",
         "text": "The compiler's public repository dates from 2023, after the paper. Run on the "
                 "paper's five benchmarks (L6, 15 ions per trap) it reproduces the paper's own "
                 "shuttle counts for QFT (196) and QuadraticForm (164). For Supremacy it gives "
                 "261 against the printed 223 and for SquareRoot 339 against 355, the same for all "
                 "ten seeds we ran; for QAOA its result depends on an unseeded shuffle and ranges "
                 "from 903 to 1,021 across ten seeds, against 957."},
        {"title": "The baseline column",
         "text": "QCCDSim reproduces the paper's baseline column exactly for four benchmarks. For "
                 "SquareRoot it gives 727 shuttles where the table prints 717; the table's own "
                 "reduction (372 shuttles, 51.17 %) implies 727."},
        {"title": "Passing through a full trap",
         "text": "Every one of the compiler's five schedules routes an ion through a trap that is "
                 "already full (its capacity is 15 + 2): once each for Supremacy, SquareRoot and "
                 "QFT, twice for QAOA (seed 0) and three times for QuadraticForm. In SquareRoot, "
                 "for example, trap T1 holds 18 ions for 676 µs while ion 17 passes through; the "
                 "compiler's own record of the chain shows the 18th ion. The shuttle counts are "
                 "not affected."},
    ],
    "khan2026cyclone": [
        {"title": "The Z checks of the hypergraph-product codes",
         "text": "In the artifact's compiler every Z check of a hypergraph-product code is built "
                 "from the last row of the X check matrix: all 108 Z checks of [[225,9,6]] act on "
                 "the same seven qubits. Our checker finds this from outside: in the Z phase, 732 "
                 "of the 756 CX gates are not gates of the code's Z checks. With the Z checks read "
                 "from the code's Z matrix, the same compiler gives 124,140 µs for [[225,9,6]] "
                 "(the paper prints 123,140), 204,800 µs for [[375,15,6]] and 341,500 µs for "
                 "[[625,25,8]]; the speedup over the grid becomes 4.05x instead of 4.08x."},
        {"title": "Grid round times behind the BB error rates",
         "text": "The bivariate-bicycle error rates of Fig. 14 were computed with grid round times "
                 "of 292,540, 187,170 and 619,710 µs (recoverable from the artifact's committed "
                 "result files). The artifact's grid compiler gives 164,520, 188,595 and 270,145 µs "
                 "for the same codes, which are also the times its notebook records; we found no "
                 "setting that produces the Fig. 14 values. With the compiler's own times, Cyclone "
                 "is 3.9x, 3.6x and 3.3x faster than the grid, rather than 6.9x, 3.6x and 7.6x."},
        {"title": "Rebalancing through a full trap",
         "text": "The grid baseline is QCCDSim's scheduler. Its rebalancing shuttles follow a "
                 "min-cost-flow path that can pass through a full trap: in the [[225,9,6]] run, 14 "
                 "merges put an eighth ion into a trap of capacity 5 + 2 until the ion splits out "
                 "again 80 µs later. The round time is not affected."},
    ],
    "jones2025": [
        {"title": "Gate order on the data qubits",
         "text": "The router keeps each ancilla's gates in circuit order but not each data qubit's. "
                 "In the d = 3 schedule, data qubit D70 meets X-check ancilla M80 (routing pass 2) "
                 "before Z-check ancilla M61 (pass 3); the circuit has the opposite order. Rebuilt "
                 "in stim with the CX gates in the router's order, the noiseless circuit has "
                 "non-deterministic Z detectors on the grid at d = 2, 3, 6 and 12, while stim's own "
                 "order, rebuilt the same way, has none; the linear and switch schedules keep the "
                 "circuit's order. The artifact's error-rate simulation follows the circuit's "
                 "original order, so its logical error rates are for the circuit as written; the "
                 "4,085 µs is the time of the reordered schedule."},
        {"title": "Two timing models in Table 2",
         "text": "Table 2's switch row (5,325 µs at d = 3) comes out of the artifact only at its 2024 "
                 "commit 6d4b467, where a junction entry or exit takes 100 µs; the current code, "
                 "with 50 µs, gives 3,690 µs, while its grid rows are reproduced by the current code. "
                 "The routing-operation counts agree under both."},
        {"title": "Table 2 at d = 6",
         "text": "The artifact's own data file and our run both give 4,080 µs; Table 2 prints 4,085."},
    ],
    "leblond2023": [
        {"title": "The time of a junction move",
         "text": "The paper allots a junction move two Junction times (210 µs). The code, and the "
                 "schedules it ships, allot one Move plus one Junction (110.25 µs). We replay the "
                 "shipped schedules, so we use the code's figure."},
    ],
}


PAPERS.append({
    "key": "bach2025",
    "short": "Position Graph",
    "title": "Efficient Compilation for Shuttling Trapped-Ion Machines via the Position Graph "
             "Architectural Abstraction",
    "authors": ["Bao Bach", "Ilya Safro", "Ed Younis"],
    "venue": "arXiv 2025", "year": 2025, "arxiv": "2501.12470",
    "artifact": {"url": "https://arxiv.org/abs/2501.12470", "commit": None, "license": None,
                 "note": "No code or circuits are published (the paper says a link will follow "
                         "acceptance), so this paper is reproduced at the level of its model: its "
                         "device, its timing table and its baseline simulator."},
    "what": "A graph of ion positions as the compiler's view of any shuttling machine, and two "
            "compilers on it (SHAPER, SHAW), compared with QCCDSim on grid devices.",
    "model": {"profile": "QCCDSIM", "law": "qccdsim"},
    "figures": [{"run": "qft20_G2x3_c5_FM_GS_Greedy", "layout": "qccdsim_g2x3",
                 "caption": "G2x3 at capacity 5, the device of the paper's QFT-20 comparison"}],
    "runs": [
        {"id": "qft20_G2x3_c5_FM_GS_Greedy", "label": "QFT-20 on G2x3, capacity 5: QCCDSim",
         "file": "qft20_G2x3_c5_FM_GS_Greedy.schedule.json.gz", "importer": "qccdsim",
         "profile": "QCCDSIM", "law": "qccdsim", "circuit": {"kind": "qasm", "file": "qft20.qasm"},
         "expect": {"tool": {"makespan_us": "metrics.Program Finish", "fidelity": "metrics.Fidelity"},
                    "paper": {"makespan_us": [70134, "Table 3, QCCDSim on the paper's own "
                                                     "transpiled QFT-20 (not published)", "printed"]}}},
    ],
})

_MOVELESS = [("SC9_L8", "rotated surface code d=3 [[9,1,3]] on L8", 1.412308),
             ("SC25_L24", "rotated surface code d=5 [[25,1,5]] on L24", 1.559463),
             ("SC49_L48", "rotated surface code d=7 [[49,1,7]] on L48", 1.776619),
             ("CC91_L90", "color code d=11 [[91,1,11]] on L90", 5.244019)]
_MOVELESS_KIND = {"Moveless_1m": "Moveless, one ancilla", "MAO_1m": "moving the ancilla only",
                  "Baseline_maxm": "QCCDSim, one ancilla per stabilizer"}

PAPERS.append({
    "key": "khan2025moveless",
    "short": "Moveless",
    "title": "Moveless: Minimizing Overhead on QCCDs via Versatile Execution and Low Excess Shuttling",
    "authors": ["Sahil Khan", "Suhas Vittal", "Kenneth Brown", "Jonathan Baker"],
    "venue": "arXiv 2025", "year": 2025, "arxiv": "2508.03914",
    "artifact": {"url": "https://github.com/sahilkhan123/Moveless", "commit": "36c953c",
                 "license": None,
                 "note": "No license file: we ran it locally and publish only the schedules it "
                         "produced and the gate lists of its benchmark circuits."},
    "what": "Syndrome extraction on a line of small traps where data ions stay put and few "
            "ancillas visit them, against QCCDSim moving whichever ion is cheaper.",
    "model": {"profile": "QCCDSIM_GATESET", "law": "qccdsim"},
    "figures": [{"run": "SC9_L8_Moveless_1m", "layout": "qccdsim_linear",
                 "caption": "L8: eight traps of capacity 5 + 2 on a line, for the d = 3 surface code"}],
    "runs": [{"id": f"{code}_{kind}", "label": f"{label}: {_MOVELESS_KIND[kind]}",
              "file": f"{code}_{kind}.schedule.json.gz", "importer": "qccdsim",
              "profile": "QCCDSIM_GATESET", "law": "qccdsim",
              "circuit": {"kind": "pairs", "file": f"{code}_{kind}.cx.json"},
              "expect": {"tool": {"makespan_us": "metrics.Program Finish"}}}
             for code, label, _ in _MOVELESS for kind in ("Moveless_1m", "MAO_1m", "Baseline_maxm")],
    "ratios": [{"label": f"{label}: QCCDSim / Moveless", "num": f"{code}_Baseline_maxm",
                "den": f"{code}_Moveless_1m",
                "paper": [r, "Fig. 13, bar height (vector data in the source)"]}
               for code, label, r in _MOVELESS],
})

NOTES["khan2025moveless"] = [
    {"title": "What the benchmark circuits say",
     "text": "Each benchmark file lists one round's CNOTs for the ancillas it is compiled with, "
             "with no measurement between stabilizers, and the compiler runs them in whatever "
             "order it chooses (with one ancilla it runs some gate numbers twice and others "
             "never, while the CNOTs executed are exactly the file's). So we check that every "
             "CNOT runs exactly once, not their order."},
    {"title": "The baseline passes through full traps",
     "text": "The baseline is QCCDSim's scheduler; for the colour code it routes an ion through a "
             "full trap 4 times (capacity 5 + 2), the same behaviour described under Cyclone. The "
             "time is not affected."},
]

NOTES["bach2025"] = [
    {"title": "Why the QCCDSim number differs",
     "text": "The paper transpiles every circuit to Quantinuum's native gates with BQSKit before "
             "scheduling, and its QCCDSim column (70,134 µs for QFT-20 on G2x3 at capacity 5) is "
             "for that transpiled circuit, which is not published. QCCDSim on the textbook QFT-20 "
             "(380 CNOTs) takes 47,580 µs on the same device. The paper's SHAPER (39,966 µs) and "
             "SHAW (39,871 µs) are therefore not directly comparable with any schedule we can "
             "produce; our comparison is with QCCDSim on the same circuit."},
]

PAPERS.append({
    "key": "moses2023",
    "kind": "design",
    "short": "Quantinuum H2",
    "title": "A Race Track Trapped-Ion Quantum Processor",
    "authors": ["S. A. Moses", "C. H. Baldwin", "M. S. Allman", "R. Ancona", "J. M. Pino"],
    "venue": "Phys. Rev. X 13, 041052 (2023)", "year": 2023, "arxiv": "2305.03828",
    "artifact": {"url": "https://github.com/Quantinuum/quantinuum-hardware-h2-benchmark",
                 "commit": "d422a71", "license": None,
                 "note": "The benchmark circuits are published with all rights reserved: we read and "
                         "ran them locally and publish only counts, never the circuits."},
    "what": "The one real machine here: a race-track trap with one closed RF null and no "
            "junctions, four gate zones that gate and measure, four that only sort, auxiliary "
            "parking zones, and two broadcast conveyor regions of twenty wells each. This is a "
            "design reproduction: the machine in our language, and the circuits it published.",
    "arch": "h2.arch.json",
    "runs": [],
})

#: Our own schedules, per paper: each compared with one of the paper's runs (``against``) on
#: the same device, under that run's law and circuit, every listed profile, strict
#: durations (an operation the paper's law does not price is not allowed).  ``how`` says
#: what made the schedule; ``found`` what the search found that the page should say.
_Q = ("QCCDSIM", "QCCDSIM_PHYSICAL")
OURS: dict[str, dict] = {
    "murali2020": {
        "how": "A list scheduler of our own over QCCDSim's primitives: every candidate step is "
               "timed exactly before it is chosen, with look-ahead; the qubits are spread over all "
               "six traps; a gate swap is used only where the chain bookkeeping of QCCDSim and of "
               "the physical machine agree, so the schedule passes under both.",
        "runs": [{"id": f"{c}_ours", "file": f"ours/{c}_ours.schedule.json.gz", "against": a,
                  "profiles": _Q, "label": lab}
                 for c, a, lab in (
                     ("bv64_L6_c14", "bv64_L6_c14_FM_GS_Greedy", "BV-64 on L6, capacity 14"),
                     ("bv64_L6_c20", "bv64_L6_c20_FM_GS_Greedy", "BV-64 on L6, capacity 20"),
                     ("adder_L6_c14", "adder_L6_c14_FM_GS_PO", "Adder on L6, capacity 14"),
                     ("adder_L6_c20", "adder_L6_c20_FM_GS_PO", "Adder on L6, capacity 20"),
                     ("qaoa6420_L6_c14", "qaoa6420_L6_c14_FM_GS_PO", "QAOA-64 on L6, capacity 14"),
                     ("qaoa6420_L6_c20", "qaoa6420_L6_c20_FM_GS_PO", "QAOA-64 on L6, capacity 20"),
                     ("sup64_L6_c20", "sup64_L6_c20_FM_GS_PO", "Supremacy-64 on L6, capacity 20"),
                     ("qft64_L6_c14", "qft64_L6_c14_FM_GS_Greedy", "QFT-64 on L6, capacity 14"),
                     ("qft64_L6_c20", "qft64_L6_c20_FM_GS_Greedy", "QFT-64 on L6, capacity 20"))],
        "found": "QFT-64 is a loss: every trap crossing costs two gate swaps under both "
                 "bookkeepings, only one travelling ion fits in a trap at a time, and finished "
                 "ions have nowhere to go at these capacities.",
    },
    "saki2022": {
        "how": "The same scheduler as for QCCDSim, on the paper's device. It minimises time, "
               "not shuttles, so it is compared on both.",
        "runs": [{"id": f"{b}_ours", "file": f"ours/{b}_ours.schedule.json.gz",
                  "against": f"qccdsim-{b}", "also": f"mts-{b}-seed0", "profiles": _Q,
                  "label": lab}
                 for b, lab in (("supremacy", "Supremacy"), ("sqrt", "SquareRoot"),
                                ("qaoa", "QAOA"), ("qft", "QFT"), ("quadform", "QuadForm"))],
        "found": "On the paper's own measure, the number of shuttles, Muzzle's compiler stays "
                 "ahead of ours on every benchmark; ours is faster where it is faster.",
    },
    "bach2025": {
        "how": "The same scheduler as for QCCDSim, on the paper's G2x3 device with QCCDSim's "
               "primitives. The paper's own compilers are not public, so they are compared by "
               "the numbers the paper prints, under the paper's model rather than this one.",
        "runs": [{"id": "qft20_G2x3_c5_ours", "file": "ours/qft20_G2x3_c5_ours.schedule.json.gz",
                  "against": "qft20_G2x3_c5_FM_GS_Greedy", "profiles": _Q,
                  "paper": {"SHAW": 39871, "SHAPER": 39966},
                  "label": "QFT-20 on G2x3, capacity 5"}],
    },
    "khan2026cyclone": {
        "how": "The same ring, the same traps, one ancilla per trap, Cyclone's own clock "
               "(a step costs its gate layers, a 300 µs swap and a shuttle). What changes is "
               "where the data sit and which check starts in which trap: chosen by matching, "
               "annealing and, for the BB codes, a SAT search. Every check is one of the true "
               "checks (the Z checks are fixed), each on one ancilla.",
        "runs": [{"id": f"{c}_ours", "file": f"ours/{c}_ours.schedule.json.gz", "against": a,
                  "profiles": ("CYCLONE",), "circuit_relabel": "checks", "label": lab}
                 for c, a, lab in (
                     ("hgp225", "hgp225_cyclone_zfix", "HGP [[225,9,6]]"),
                     ("hgp375", "hgp375_cyclone_zfix", "HGP [[375,15,6]]"),
                     ("hgp625", "hgp625_cyclone_zfix", "HGP [[625,25,8]]"),
                     ("bb72", "bb72_cyclone", "BB [[72,12,6]]"),
                     ("bb90", "bb90_cyclone", "BB [[90,8,10]]"),
                     ("bb144", "bb144_cyclone", "BB [[144,12,12]]"))],
        "found": "Cyclone already ends a phase as soon as every check is done; its full "
                 "rotations come from placing check i on ancilla i. A round of ours does not end "
                 "with each ancilla back where it started; every trap holds one ancilla, so the "
                 "next round starts from whichever is there. A shorter round was not found "
                 "with fewer ions per trap (a search, not a proof).",
    },
    "khan2025moveless": {
        "how": "Every CNOT in these benchmarks uses the one ancilla, so the CNOTs run one after "
               "another whatever the schedule (100 µs each); what a compiler can save is the "
               "ancilla's travel. Ours stops the ancilla in as few traps as the capacity allows "
               "and visits each trap once, from one end of the line to the other: it starts with "
               "4 data in the end trap, and every later trap holds 6 data when it arrives. Every "
               "trap starts with the paper's 5 ions; the extra one is handed leftwards from trap "
               "to trap, from the end of each chain, long before the ancilla gets there. In each "
               "trap the ancilla runs every CNOT of the data it finds, back to back. The CNOTs "
               "are checked as a set, as for the paper's own schedules.",
        "runs": [{"id": f"{c}_ours", "file": f"ours/{c}_ours.schedule.json.gz",
                  "against": f"{c}_Moveless_1m", "same_ops": "counts", "profiles": ("QCCDSIM_GATESET",), "label": lab}
                 for c, lab in (("SC9_L8", "surface code d=3 on L8"),
                                ("SC25_L24", "surface code d=5 on L24"),
                                ("SC49_L48", "surface code d=7 on L48"),
                                ("CC91_L90", "colour code d=11 on L90"))],
        "found": "The time is the CNOTs plus the ancilla's moves: 170 µs for the first and 470 µs "
                 "for each later one. The ancilla enters a trap at one end and must leave by the "
                 "other, past the data it just used, so every move after the first carries a gate "
                 "swap (300 µs). We found no schedule on a line that avoids it: turning back keeps "
                 "the ancilla among data it has finished with. As in the paper's schedules, the "
                 "ancilla ends in the last trap it visits; in ours the data that were handed "
                 "along the line also stay where they were handed (the benchmark is one round of "
                 "CNOTs with no measurement).",
    },
    "jones2025": {
        "how": "The same grid, the same placement of qubits in traps, the paper's operation "
               "times and its rule that an operation holds every component it names (a split or "
               "merge also holds the junction at the far end of its segment, an entry or exit "
               "holds the junction and the segment). Each ancilla visits its data one after "
               "another instead of going home after every gate: first the two around one end of "
               "its trap, then, passing through its own empty trap, the two around the other "
               "end, and it comes home to be measured. It always leaves a data trap by the end it "
               "came in, so no gate swap is needed. The order of each tour follows what each "
               "data qubit needs, so every qubit sees its CNOTs in an order the circuit allows; "
               "there are no barriers, and every ion ends in the trap it started in.",
        "runs": [{"id": f"grid_rot_d{d}_c2_ours", "file": f"ours/grid_rot_d{d}_c2_ours.schedule.json.gz",
                  "against": f"grid_rot_d{d}_c2", "same_ops": "counts", "profiles": ("JONES",),
                  "label": f"rotated surface code d={d}, one round, grid, capacity 2"}
                 for d in (2, 3, 6, 12)],
        "found": "The paper's own grid schedules fail the circuit check (each data qubit must see "
                 "its CNOTs in an order the circuit allows); ours pass it, with the same "
                 "operations. Like the paper's, our round takes about the same time at every "
                 "distance. The table gives the start of the last operation, the time the paper "
                 "reports; for both schedules it is a final reset, 50 µs before the end.",
    },
    "leblond2023": {
        "how": "TISCC's own operations for every ion, with the same partners in the same order "
               "(preparation, rotations, the ZZ gates, measurement), on TISCC's grid and clock. "
               "What changes is the transport: for each ZZ either the measure ion goes to the "
               "data ion or the data ion goes to the measure ion; nothing waits for a layer to "
               "finish; and an ion may go straight from one partner to the next across the same "
               "junction, borrowing the next partner's operation zone for its rotations while "
               "that partner steps aside. Rotations, preparation and measurement stay in "
               "operation zones, one crossing of a junction at a time, and every ion ends the "
               "round in the zone it started in.",
        "runs": [{"id": f"{r}_ours", "file": f"ours/{r}_ours.schedule.json.gz", "against": r,
                  "same_ops": "sequence", "profiles": ("TISCC",), "label": lab}
                 for r, lab in (("idle_551", "one d=5 error-correction round"),
                                ("idle_661", "one d=6 round"))],
        "found": "Each measure ion has four ZZ gates of 2,000 µs in a row, so 8,000 µs of the "
                 "round cannot move. Everything else (preparation, rotations, transport, "
                 "measurement) takes TISCC 1,613 µs and ours about 865 µs, at d = 5 and d = 6 "
                 "alike. What limits it now is the junctions: where two meetings fall in the "
                 "same layer at one junction, their crossings take turns (a search over who "
                 "travels, not a proof that nothing shorter exists).",
    },
}

#: Papers read in full and not reproduced, with the reason; the page lists them.
CONSIDERED: list[dict] = [
    {"short": "S-SYNC", "arxiv": "2505.01316",
     "why": "No code and no results tables; the headline gains cannot be recomputed from the "
            "paper's own figures, and its two baselines need internals it does not publish."},
    {"short": "Ovide et al. (three QCCD resource studies)", "arxiv": "2408.00225",
     "why": "No code or data; timing, heating and fidelity parameters are not stated."},
    {"short": "Kreppel et al. and the Mainz linear-trap compilers", "arxiv": "2207.01964",
     "why": "Results are operation counts from an unpublished shuttling compiler on circuits "
            "that are not published; only one 16-operation reference schedule could be replayed."},
    {"short": "Lee et al., QEC ion-trap chip", "arxiv": "2501.15200",
     "why": "No results tables; the available code is an earlier version with different "
            "constants and emits only logical-level, randomised schedules."},
    {"short": "TILT", "arxiv": "2010.15876",
     "why": "Its machine moves the whole chain under a fixed laser window, a primitive our "
            "language does not express; no code is published."},
    {"short": "WISE wiring", "arxiv": "2305.12773",
     "why": "A hardware wiring paper whose results are closed-form counts and worst-case "
            "reconfiguration times; there is no schedule to replay."},
]


def paper(key: str) -> dict:
    for p in PAPERS:
        if p["key"] == key:
            return p
    raise KeyError(key)


# ------------------------------------------------------------------ TrapSIMD
# No artifact: the worked examples are replayed event for event and Table 3 is compared at
# model level (our schedules, rebuilt circuits).  `python -m qccd.repro.trapsimd
# Reproduce/ruan2025` writes Reproduce/ruan2025/results.json; `check` leaves it alone.
PAPERS.append({
    "key": "ruan2025", "kind": "model", "short": "TrapSIMD",
    "title": "TrapSIMD: SIMD-Aware Compiler Optimization for 2D Trapped-Ion Quantum Machines",
    "authors": ["J. Ruan", "H. Zhang", "X. Fang", "A. Li", "W. C. Campbell", "E. Hudson",
                "D. Hayes", "H. Haeffner", "T. Humble", "J. Palsberg", "Y. Ding"],
    "venue": "arXiv 2504.17886 (2025)", "year": 2025, "arxiv": "2504.17886",
    "artifact": {"url": None, "commit": None, "license": None,
                 "note": "No code, schedules or circuits are published. The two worked examples "
                         "are specified to the microsecond and are replayed exactly; Table 3 is "
                         "compared at model level, on circuits we rebuilt from the paper's "
                         "description."},
    "what": "A grid of X-junctions with a linear trap on every edge, one ion per position. "
            "Transport between traps is broadcast: one waveform moves every participating "
            "junction the same way at once (a JT-SIMD instruction), the paper-side twin of our "
            "own one-waveform rule.",
    "runs": [],
})


# ------------------------------------------------------------------ MQT IonShuttler
# The exact SAT shuttler (ASP-DAC 2024) and the cycle heuristic of the authors' TCAD paper,
# run at one commit, every schedule replayed.  `python -m qccd.repro.ionshuttler
# Reproduce/schoenberger2024` writes its results.json; `check` leaves it alone.
PAPERS.append({
    "key": "schoenberger2024", "kind": "model", "short": "MQT IonShuttler",
    "title": "Using Boolean Satisfiability for Exact Shuttling in Trapped-Ion Quantum Computers",
    "authors": ["D. Schoenberger", "S. Hillmich", "M. Brandl", "R. Wille"],
    "venue": "ASP-DAC 2024", "year": 2024, "arxiv": "2311.03454",
    "also": {"title": "Shuttling for Scalable Trapped-Ion Quantum Computers",
             "venue": "IEEE TCAD", "arxiv": "2402.14065"},
    "artifact": {"url": "https://github.com/munich-quantum-toolkit/ionshuttler", "commit": "bc71e46",
                 "license": "MIT",
                 "note": "Run locally with a patch that only writes out each schedule. The same "
                         "commit holds the cycle heuristic of the authors' TCAD paper, which is "
                         "replayed too."},
    "what": "Ion chains on the edges of a junction grid, one chain to an edge, with a one-way "
            "processing zone where the gates run; time is counted in steps. An exact shuttler "
            "built on a SAT solver and a fast cycle heuristic, by the same authors.",
    "runs": [],
})
NOTES["schoenberger2024"] = [
    {"title": "The heuristic's column",
     "text": "Run on the paper's configurations, the public heuristic gives a mean of 20.8 steps "
             "on Lattice 4 4 1 1 with 12 chains (seeds 0 to 4) and 14.6 on Horizontal Grate 6 2 1 1 "
             "with 8, where the TCAD paper's Table I prints 26.6 and 24.4. The authors' own log in "
             "the repository (qft.txt, added 2024-06-25) records 18, 18, 12, 13, 12 and a mean of "
             "14.6 for the second, which are exactly our runs. Turning off its gate selection "
             "brings the numbers closer (25.0 and 21.2) but not to the table."},
    {"title": "The racetrack row",
     "text": "For Racetrack 2 2 1 5 with 6 chains the exact tool gives a mean of 13.2 states over "
             "seeds 0 to 9, where Table I prints 11.0; the three lattice rows we ran match to the "
             "decimal. Placing the processing zone on the short side instead gives 13.6."},
    {"title": "The full lattice",
     "text": "For Lattice 3 3 1 1 with 12 chains on 12 sites, seed 0 alone needs at least 20 "
             "states (the tool proves 19 impossible) and did not finish in 30 minutes; Table I "
             "prints a 10-run mean of 17.0 and 36 seconds."},
]
