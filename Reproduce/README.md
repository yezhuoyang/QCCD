# Reproducing the literature

Published QCCD papers, rerun and replayed under their own rules, and then our own schedules for
the same circuits on the same machines, under the same rules. The website shows this at
`/reproduce/`; this folder is what it is made from.

For each paper we ran its own published code, wrote out every operation it scheduled, and
replayed the schedule in our checker (`qccd/repro/timed.py`) under that paper's rule profile
and timing law (`qccd/repro/models.py`). Three numbers are then compared: what the paper's code
printed and what our replay measures must be **identical**; what the paper reports is matched to
the paper's precision (exactly where it prints a number, within 0.5 % where it plots one), and a
miss is reported, never hidden.

Our own schedules (`<paper>/ours/`) are judged by the same checker, more strictly: the timing
law and circuit of the paper's run they are compared with, every rule profile listed for them,
the same device and ions, and strict durations -- an operation the paper's law does not price
is refused rather than passed for free.

```
python -m qccd.repro check            # every paper; writes <paper>/results.json
python -m qccd.repro check jones2025  # one paper
```

| folder | paper | artifact | license of the artifact |
|---|---|---|---|
| `murali2020/` | Murali et al., ISCA 2020 (QCCDSim) | github.com/prakashmurali/QCCDSim @ b01b0b3 | none: only the schedules we generated are here |
| `saki2022/` | Saki et al., DATE 2022 (Muzzle the Shuttle) | github.com/ashsaki/MTS-QCCD-Compiler @ 489a097 | Apache-2.0 (`circuits/LICENSE`) |
| `khan2026cyclone/` | Khan et al., HPCA 2026 (Cyclone) | github.com/sahilkhan123/Cyclone @ bb6b551 | MIT (`artifact/LICENSE`) |
| `jones2025/` | Jones and Murali, ASPLOS 2026 | github.com/scottjones03/PartIIProject @ d235a22 (and 6d4b467) | none: only the schedules we generated are here |
| `leblond2023/` | LeBlond et al., SC-W 2023 (TISCC) | github.com/ORNL-QCI/TISCC @ 1212aec | UT-Battelle (`artifact/LICENSE`, `artifact/NOTICE.md`) |
| `bach2025/` | Bach et al., 2025 (Position Graph) | no public code: QCCDSim on the paper's device | -- |
| `khan2025moveless/` | Khan et al., 2025 (Moveless) | github.com/sahilkhan123/Moveless @ 36c953c | none: only the schedules and gate lists we generated are here |
| `ruan2025/` | Ruan et al., 2025 (TrapSIMD) | no public code: worked examples and model level | -- |
| `schoenberger2024/` | Schoenberger et al., ASP-DAC 2024 and IEEE TCAD (MQT IonShuttler) | github.com/munich-quantum-toolkit/ionshuttler @ bc71e46 | MIT (`artifact/LICENSE`) |
| `moses2023/` | Moses et al., PRX 2023 (Quantinuum H2) | github.com/Quantinuum/quantinuum-hardware-h2-benchmark @ d422a71 | all rights reserved: no circuit is here, only counts and hashes |

In each folder:

- `artifact/` -- the artifact's own output, compressed: its schedule dump (every event, its
  start and end, the device, the initial chains, the numbers it printed), or for TISCC its
  shipped regression schedules. Nothing in here was edited, except that local working-directory
  paths in a recorded command line are shortened to `<work>` (each such file says so in
  `source_note`).
- `circuits/` -- the circuit each run must implement, where its license allows us to ship it;
  otherwise a circuit we generated ourselves and checked gate for gate against theirs.
- `ours/` -- our schedules for the paper's circuits on the paper's machine, compressed.
- `results.json` -- written by `python -m qccd.repro check`; the website reads only this.

Three folders are made by their own scripts, and `check` leaves their `results.json` alone:

- `ruan2025/` (TrapSIMD publishes no code): `python -m qccd.repro.trapsimd Reproduce/ruan2025`
  writes the paper's two worked examples event for event (`examples/`), our schedules for its
  results table on its device and timing with circuits rebuilt from its description (`ours/`),
  and `MODEL.md`, which says where every number comes from and every assumption that moves one.
- `schoenberger2024/` (MQT IonShuttler, counted in abstract time steps):
  `python -m qccd.repro.ionshuttler Reproduce/schoenberger2024` replays every schedule its exact
  SAT tool and its cycle heuristic produced (`artifact/`) and ours (`ours/`, in the heuristic's
  model); `MODEL.md` gives the rules and where each comes from.
- `moses2023/` (H2, a design reproduction): `h2.arch.json` is the machine in our language,
  `DESIGN.md` where each of its numbers was read; `run_h2.py` counts the published circuits'
  gates against the paper's Table I and records what our compiler does with them.

`qccd/repro/catalog.py` lists every run, every number compared and where in the paper it is,
our schedules (`OURS`), and the notes on each paper. A note is written only when running the
paper's code shows it.
