# Hillclimb · agent-designed devices for five architecture boards

Teams of Claude Code agents searched for trapped-ion QCCD **devices** with the lowest `T_jones` on
qccd.academy's architecture boards. A first campaign (v1, 2026-10-18 to 2026-10-19) searched the platform as
it was then. Its agents found six gaps in the platform, which became issues #15-#20. Their fixes, with #21,
landed on `compiler` on 2026-10-21 and changed the physics, and the v1 numbers no longer hold
([below](#what-the-platform-fixes-changed)). This folder is the re-run on the fixed platform (v2), searched on
2026-10-22, for `surface17`, `steane`, `five_qubit` and `rep9`. `bb144` was not re-run, and its folder is
still v1's ([below](#bb144)).

Every v2 number here was graded with QCCD `compiler` at `84e8c5d`, the published Linux compiler
`c774b87b677f` and the published Linux qcheck `e8acd6640fd3`. Production grades these boards with the same
compiler ([toolchain](#the-toolchain)). Nothing has been submitted to the official leaderboard.

| board | v2 best | worst labeling | best id-independent | best live design, graded here | faster by | the device |
|---|---:|---:|---:|---:|---:|---|
| [`surface17@1`](surface17/) | **3.49 ms** | 6.085 ms | 3.585 ms | 11.665 ms (`stationary_chain`) | 3.34× | 6 traps on a path, 15 ions in one of them, shared control, 33 DACs |
| [`steane@1`](steane/) | **2.100 ms** | 2.100 ms | 2.100 ms | 9.785 ms (`stationary_chain`) | 4.66× | one trap holding all 13 ions, 24 DACs: the seed, not beaten |
| [`five_qubit@1`](five_qubit/) | **1.980 ms** | 1.980 ms | 1.980 ms | 9.155 ms (`stationary_chain`) | 4.62× | one trap holding all 9 ions, 24 DACs: the seed, not beaten |
| [`rep9@1`](rep9/) | **2.445 ms** | 5.13 ms | none | 2.91 ms (`stationary_chain`) | 1.19× | 4 traps on a path, 14 ions start in one, 96 DACs; ties the 3-trap seed (worst labeling 5.4 ms) |
| [`bb144@1`](bb144/) | not re-run | | | | | v1's 168-trap cycle: 96.74 ms under the old physics, 2,313.66 ms under the fixes |

- **v2 best** is the best `T_jones` a verifier reproduced, for the device file as the search logged it:
  eligible under all ten reference stages, and passed this climb's audit.
- **worst labeling** is the same device with its trap ids permuted or its positions mirrored: the worst grade
  its verifier found. The compiler breaks placement ties by trap id ([Caveats](#caveats)). Only on `rep9` is
  the worst labeling slower than the best live design (5.13 against 2.91 ms).
- **best id-independent** is the best device that kept its score under every labeling its verifier tried.
  On `surface17` that is a01's 10-trap path, 3.585 ms in 4 graded variants and 17 `hc-fast` relabelings. On
  `rep9` every graded device has a labeling that scores worse.
- **best live design** is the fastest design on the board's qccd.academy page (fetched 2026-10-21 21:15 UTC),
  rebuilt and graded here. On all four boards it is `stationary_chain`, and the page's number reproduces
  exactly.

All four v2 bests win mainly through one long chain, which `T_jones` does not charge
([Caveats](#caveats)): 15 ions in one trap on `surface17`, all 13 on `steane`, all 9 on `five_qubit`, 14 on
`rep9`. On `steane` and `five_qubit` the best is the brief's single-trap seed, unchanged. One agent-hour each
found nothing faster, and the verifiers could not refute the agents' arguments that no device is faster under
qccdc + `insert_cooling`. On `rep9` the best ties the brief's low-motion seed. On `surface17` the agents went
from 4.375 ms, the best device measured before the run (a 2-trap path), to 3.49 ms.

### bb144

`bb144` was not re-run. [`bb144/`](bb144/) is the first campaign's folder: devices found and graded under the
old physics (96.74 ms then). Under the fixes its ten design files are one device written in ten orders,
compile to one program (#20) and grade 2,313.66 ms, eligible, 2,271 ms of it transport. The board's live top
rows, 24.93 ms (marked not buildable) and 55.62 ms, were compiled with a code-aware placement, on circuits of
their own (216 and 180 qubits; the board's has 168). A submission cannot carry a placement: at `84e8c5d`,
`submit_design` compiles with `auto`, `compile` or `rotate` and takes no placement. The page's device-only
rows are `grid13x13_broadcast` at 866.1 ms and `baseline_stride` at 1,419.13 ms (fetched 2026-10-21); the v1
device is 2.67× and 1.63× slower than these. Whether a submission can carry a placement was asked on #10 on
2026-10-21 and is not answered yet. `bb144` waits for that answer.

---

## Layout

```
Codesign/hillclimb/
  README.md         this page
  grade.py          grade device files on a board, as `qccd submit --local` does; prints the toolchain first
  <board>/          surface17, steane, five_qubit, rep9 (v2):
    README.md       the best devices, why they are fast, trap-id dependence, #22 exposure, the live designs
    chart.png       the graded designs beside the live designs, as on the board's page
    designs/        the verified devices, as .arch.json (on surface17, 10 of its 14 distinct devices)
    results.csv     every device the v2 search logged, with its verification, and the v1 PR files re-graded
  bb144/            the first campaign's folder (old physics)
```

## The toolchain

| | version | identity |
|---|---|---|
| QCCD | `compiler` at `84e8c5d` | evaluator source `sha256:e9be78cc47e8dd7d9f20b88ead9802c54f219ccb34eadb0717cb5b459bf8ae24`, bridge `sha256:eafcb423e98b496136ec5aac9fc7603832c8288afc40f70798a853d94a077ed0` |
| qccdc | `c774b87b677f`, the published Linux build | `sha256:ed8838c6a2ea36369157916d7e011e1dc858b613f9a8462e1bae7d55c8a8440a` |
| qcheck | `e8acd6640fd3`, the published Linux build | `sha256:c18484bd7b807ed63edacfe61be87143729ee188fe6cddcbede6bdcec34c050e` (unpacked) |

These are the digests the evaluator writes into every report (`evaluator.source_digest` and
`evaluator.toolchain`), and every one of the 24 grades the v2 search logged carries them. `grade.py` prints
them first. Production's image keeps qccdc `c774b87b677f` (`deploy/official/README.md` on `compiler` at
`fbe4ab1`: the later `b4a003dbb4ae` is byte-identical on the suite and the seeds), and for these boards the
grading code at `84e8c5d` is that of `751d443`, deployed on 2026-10-22. Under this toolchain the 46 v1 PR files
re-grade to everything John posted about them on 2026-10-21: each board's best file, the ten `bb144` files at
2,313.66 ms, and the files that fail R20 or R21.

To grade the designs again, use yezhuoyang/QCCD's `compiler` branch at `84e8c5d`, the base of these PRs, with
this folder copied in. From the root of a checkout that has `Codesign/hillclimb`:

```bash
git fetch https://github.com/yezhuoyang/QCCD compiler
git worktree add ../qccd-84e8c5d 84e8c5d
cp -R Codesign/hillclimb ../qccd-84e8c5d/Codesign/
cd ../qccd-84e8c5d
pip install -e '.[agent]'      # `qccd`, and stim, qiskit and numpy for the semantics stage
qccd toolchain install         # compiler c774b87b677f; qcheck e8acd6640fd3 on Linux and Windows
qccd toolchain status          # which compiler and checker the evaluator will use
python Codesign/hillclimb/grade.py rep9@1 Codesign/hillclimb/rep9/designs/*.arch.json
```

The evaluator takes `QCCD_QCCDC` and `QCCD_QCHECK` if they are set, then a build in the checkout, then what
`qccd toolchain install` installed. `grade.py` prints the paths it resolved and their sha256, and the commit
and digests of the QCCD source it imported (commit "unknown" when that is not a git checkout), then one line
per device: the board's ranked metric, eligibility, and the DACs, traps and junctions as the grader counts
them (DACs with the physics' wiring floors). Without qcheck the required `lean_certificate` stage is
`unsupported`, so every design comes out not eligible. On macOS `qccd toolchain install` fetches the macOS
build of `c774b87b677f`, a different binary (it gives the Linux build's `T_jones` on the 19 v2 design files
and the 46 v1 PR files), and there is no macOS qcheck of `e8acd6640fd3`: build it from the checkout with
`qccd toolchain build-checker`, which needs Lean's `elan` and fetches the pinned
`leanprover/lean4:v4.29.0-rc2`.

## How the v2 search ran

| board | agents | hours (2026-10-22, UTC) | logged grades |
|---|---:|---|---:|
| surface17 | 8 | 2: 20:45-22:45 | 15 |
| steane | 1 | 1: 20:45-21:45 | 2 |
| five_qubit | 1 | 1: 20:45-21:45 | 3 |
| rep9 | 1 | 1: 20:45-21:45 | 4 |

The agents were Claude Code (Opus 5.5) subagents of one workflow. Every command they ran went through a
wrapper to a VM, where it ran as a sandboxed user: firewalled from qccd.academy, with no publishing
credentials, and unable to write the grader or the compiler. Each agent got a brief: the board's circuit, the
physics as `84e8c5d` prices it, the live designs rebuilt and graded on the VM, seeds graded before the run
(the low-motion devices, v1 designs and the live designs), the floor arguments known so far, this climb's
rules, and one strategy. On `surface17` the strategies were a lower bound, 2-trap partitions, 3- and 4-trap
batches, measurement layout, a search engine, designs with no long-chain splits, cross-pollination, and
junctions and conveyors. On the other boards the one agent was to prove the seed is the floor or beat it (on
`rep9`, also to find a design with no long-chain splits).

A design counted only through `hc-grade`. It compiles the board's circuit onto the device with the reference
compiler (qccdc + `insert_cooling`, compiler `auto`, no placement), then freezes and grades it with the
reference evaluator (all ten stages, the Lean certificate included). It then runs this climb's audit and logs
the result on the run's shared board. Probes used `hc-fast` (the same compile, cooling, rules stage and
floored budget, without the workspace or Lean), `hc-chainsplits` or unlogged grades; only logged grades count.
All 24 logged grades are eligible and passed the audit.

After the deadline one adversarial verifier per board re-graded every logged design from scratch, and every
logged score reproduced. It graded copies with permuted trap ids and mirrored positions, counted the #22
exposure with `hc-chainsplits`, and read the agents' notes and scripts. No verifier found an edited program, a
passed placement or a search over ids. On `surface17` a search over capacities and zones still picked lucky
trap-id tie-breaks implicitly, which is why each id-dependent number is reported with its worst labeling.

## Rules of this climb

**What the platform now does itself.** It prices split and merge from what each move does (#16, #17) and
reads a device in canonical order (#20). It judges the budget in the device stage, counted with the physics'
wiring floors (at most 128 DACs and 200 junctions, #15), R22 with the new labels (#18, #19), the geometry rules
R19-R21 (#21) and, as before, the other rules on the cooled program, the physics lock, the correspondence of
the program with the certified one, the certificate binding, the Lean certificate and the semantics. A design
that fails any of these is not eligible.

**What this climb adds.** `hc-grade` rejects a design that breaks a rule marked [audit], even if it is eligible.
The other rules were on each agent's word, and the verifiers checked them:

1. **Devices only.** The program always comes from the reference compiler; no edited programs and no placement
   passed in.
2. **Declared splits kept (#22).** [audit] R14 judges only splits that a class declares. So a class keeps the
   `split` its reference class id declares (dock, undock, sort_split, eject, reinsert), and any class with orbit
   `spurs` declares split and merge. On the agents' word, not audited: no device deletes its dock classes to
   make the compiler fall back to `shuttle` (no counted design has a dock).
3. **Closed loops tagged.** [audit] Every rail between consecutive nodes of a closed loop carries the loop's id,
   and no other rail does. An untagged ring rail or a tagged spur would hide a dock from the path price (#17).
4. **Direct control pays direct wiring.** [audit] `control.model: "direct"` turns R22 off, and the grader
   accepts it on shared wiring. Here it needs at least 24 DACs per trap and 48 per junction in the floored
   count: direct wiring and no channel map.
5. **No zero-length hops.** [audit] No segment joins two nodes at the same position. Such a hop has no R22
   label, and R20 does not see rails shorter than 1e-6.
6. **No id, name or order search.** One fixed id scheme (T0..Tn along a path or loop). No search over ids,
   names or file order, and no rotating or reversing a loop's node list without a physical reason: the compiler
   still breaks ties by id.
7. **No closed loops through junctions to trigger `rotate`** (#23).
8. **Physics unchanged** (`--adopt-physics`).

Anything that lowered `T_jones` by exploiting the grader rather than improving the device was to be written up
under "Possible exploits" and not counted. Each board's README lists what its agents and verifier found.

## What the platform fixes changed

| issue | the gap | under the fix |
|---|---|---|
| #15 | the evaluator enforced neither the DAC budget nor the real cost of wiring | the device stage judges the release's budget and counts the wiring at no less than the physics' floors |
| #16 | a `shuttle` joining or leaving a chain paid no split or merge | a move pays a split (80 µs, +6 quanta) when it leaves ions behind and a merge (80 µs, +6 quanta) when it joins others, whatever its class |
| #17 | deleting `dock`/`undock` dodged split and merge | a hop between a closed loop and a dead-end dock or an exit pays split and merge, whatever its class |
| #18 | a loop declared through junctions gave spur labels that let opposite moves share a waveform | open-path hops and chords take lab-frame direction labels |
| #19 | R22's sign labels let hops at different angles share one waveform | an off-axis hop's label carries its angle in whole degrees (`+x+y@30`) |
| #20 | the order of nodes and segments in the file changed the compiled schedule | the compiler reads a device in canonical order; ids still break ties |
| #21 | the grader did not run the geometry rules | it runs R19-R21: degree at most 4, rails at a node at least 60° apart, no rail through a node or across another |

The first campaign's agents found the gaps of #15-#20. The fixes landed on `compiler` at `dfdf77b` with
compiler `e799ecf5a5cd`. `c774b87b677f` produces the same program, byte for byte, on all 77 programs
`e799ecf5a5cd` produced (`deploy/official/README.md`).

| board | v1 headline (old physics) | best v1 PR file under the fixes | v2 best |
|---|---:|---:|---:|
| surface17 | 1.585 ms | 6.545 ms (`06-a09`) | 3.49 ms |
| steane | 1.44 ms | 7.565 ms (`02-a01`) | 2.100 ms |
| five_qubit | 1.62 ms | 8.71 ms (`05-a02`) | 1.980 ms |
| rep9 | 1.305 ms | 8.665 ms (`01-a01`) | 2.445 ms |
| bb144 | 96.74 ms | 2,313.66 ms (all ten files) | not re-run |

Under the fixes, five_qubit `01`-`04`, steane `05`-`08` and surface17 `04` also fail R20 or R21. The v1
devices moved ions often, and most of their moves paid no split or merge. Now a move that leaves ions behind
and joins others costs 165 µs (a 5 µs hop, an 80 µs split and an 80 µs merge), and the 12 quanta of its split
and merge exceed R7's limit of 1 quantum at an MS gate, so `insert_cooling` puts a 300 µs cool before every
later MS instruction that holds the moved ion. R12 runs one gate per trap per instruction, so one trap
serializes the circuit (1,200 µs of gates on `steane` and on `five_qubit`) but needs no transport and no
inserted cool. That is why the v2 bests are a single trap where the circuit fits in one, and one long chain
with a few moves where it does not: `surface17` and `rep9` have 17 qubits, and R13 allows at most 15 ions at a
gate.

## Caveats

- **Long chains are not charged.** `T_jones` prices an MS instruction at 25 µs for any chain length, and R13
  allows 15 ions at a gate, so few-trap long-chain devices win. Whether such devices should count as
  `T_jones` results, or a chain-length cost will be added, was asked on #10 on 2026-10-21 and is not
  answered yet.
- **Trap ids break ties (#20 residue).** The compiler reads a device in canonical order, but its placer still
  breaks ties by trap id, so one physical device can grade differently under another labeling. The agents
  used one fixed id scheme and did not search ids, but a search over capacities and zones covers both
  orientations of a path and every origin of a ring, so it can pick a lucky labeling without trying to. The
  verifiers graded permuted and mirrored copies. `surface17`'s 3.49 ms device grades 6.085 ms with its trap
  ids reversed, and `rep9`'s 2.445 ms device ranges from 2.445 to 5.13 ms over its 24 labelings. On those two
  boards, mirroring positions with the ids unchanged never changed a program.
- **#22 exposure.** R14 judges a split only when the move's class declares one. The general router's moves are
  all class `shuttle`, so a move that leaves ions behind at a site that held more than 2 ions is charged a
  split but not judged. `hc-chainsplits` counts these moves as `long_chain_splits`, which is what R14 would
  judge if #22 were fixed. The v2 bests make 3 (`surface17`, of 9 moves), 0 (`steane` and `five_qubit`, which
  move nothing) and 1 (`rep9`). The best designs with none are 11.30 ms on `surface17` (a 19-trap ring, 22.615
  ms in its worst labeling; a 34-trap ring at 12.735 ms is the best that keeps its score when the ids are
  rotated or reversed along the ring) and 3.305 ms on `rep9` (3.645 ms, with one such move, in its worst
  labeling). The live `stationary_chain` makes 32, 22, 19 and 2 such moves on `surface17`, `steane`,
  `five_qubit` and `rep9`.
- **`insert_cooling` cools before every later MS instruction of a heated ion.** `qccd/compile/cooling.py`
  replays the compiled program once, without the cools it inserts, and puts a 300 µs global cool before every
  MS instruction that breaks R7 in that replay. An ion heated past 1 quantum (one split or merge, one junction
  entry, or about 10 plain hops) therefore gets a cool before every later MS instruction it is in, not only
  the next one, although a global cool resets every ion to n̄ = 0. The floor arguments on `steane`,
  `five_qubit` and `rep9` use this rule. A pass that counted its own cools would insert fewer after a meeting;
  `steane`'s sketch says its floor would still be at least 2.135 ms, and the other two floors were not
  re-derived for such a pass.
- **`T_jones` only.** It is the modelled execution time under the board's physics table, not a fidelity: a
  15-ion chain's MS gate costs what a 2-ion chain's does, and no gate error is scored on these boards.
- **Small searches on three boards.** `steane`, `five_qubit` and `rep9` had one agent-hour each. Their floor
  arguments are readings of the compiler's code, checked by the verifiers with probes, not machine-checked
  proofs. On `rep9` the argument covers the general router only; the race-track, rotate and rigid-rotation
  modes were measured (8.665 ms and above), not bounded.
- **The audit is this climb's.** The official grader would accept some designs that this audit rejects,
  such as direct control on shared wiring. Every design here passes both.
