# Kickoff prompt for the next session

Copy everything below the line into a fresh Claude Code session in `c:\Users\yezhu\Documents\QCCD`.

---

We are building the QCCD compiler and architecture-exploration platform. The design is already
done — **read `docs/PLAN.md` first, in full.** Do not redesign it; execute it. If you disagree with
something in it, say so in one or two sentences and then proceed, or ask if the disagreement changes
what you'd build.

## Your job this session: M0 + M1 + M2

**M0 — `qccd/arch/`.** JSON schema, validator, and generators for the architecture spec in PLAN §3.
Generators needed now: `ring(width, height, verticals)`, `grid(a, b)`, `chain(n)`. They expand to the
explicit `sites` / `junctions` / `segments` graph that everything downstream consumes. Primitives
carry `(duration, quanta)` **curves**, not scalars (PLAN §3.2). Junction cost is charged by **node
degree computed from the expanded graph** — degree 2 is a bend costing one shuttle, degree ≥ 3 is a
junction (PLAN §0.5, rule R18).

*Acceptance:* `arch/ring144_24v.arch.json`, `arch/cyclone_base.arch.json`, `arch/chain.arch.json`
parse, expand, and round-trip. The shipped ring is a 2×72 loop: 144 slots, 72 labelled `top` and 72
`bottom`, **corners at slots 0, 71, 72, 143**, and **24 dock slots at {0, 6, …, 138}** — 12 on top,
12 on bottom, with slots 0 and 72 being both a corner and a dock. So the expanded graph must report
**24 degree-3 dock nodes** and **4 corners, 2 of which are also docks**.

**M1 — `qccd/ir/tsir.py`, `qccd/verify/replay.py`, and an importer for the shipped schedule.**
This is the project's first external oracle, so it comes before anything clever.

`INLINE_DATA` is a single JSON object on **line 344** of
`visualizer_24_ancillas_24_junctions_standalone.html`, assigned to `const INLINE_DATA = `. Parse it,
convert `geometries[0].operations` (396 batch-ops) into TSIR, and replay it against the state machine.

*Acceptance — reproduce exactly, as a locked test:*

```
total_cost   397184  = 2672 rotate hops × 148 + 864 contacts × 2
total_steps    8808  = 2672 hops × 3 steps/hop + 792
396 batch-ops · 864 contacts · 144 checks × 6 members each  ✓ complete
contact-batch utilization 2.18 of the 24 limit (9.1 %)
```

> Replay this under **the deck's own model as shipped** — `corner_hops = 3`, uniform "+1 step at a
> junction". M1 validates the replay engine against a known oracle; it is not asserting the physics
> is right. The corrected physics is M2.

Rules R1–R9, R11–R14 should pass on this schedule. Rules are in `Knowledge/notes/constraints.yaml`
with the source for each; `python Knowledge/kg/query.py rules` prints them.

**M2 — heating model and cooling insertion.** Add per-ion `n̄` to the replay state, then re-run the
same schedule under the **corrected** model: `corner_hops = 1`, junction cost by degree, quanta
tracked per PLAN §0.3–0.5.

*Acceptance — report all of these, and explain any that differ:*

```
T-junction transits per data ion   445   (= 2672/144 = 18.56 revolutions × 24 verticals)
quanta per data ion per ESM round  ≈1747 (267 shuttling + 1336 junction + 144 dock/undock)
rotation wall clock                ≈267 ms  (every hop pays a junction, since one is always on the path)
                                   vs 13.4 ms if the rotation path had no degree-3 node
```

Then emit the cooling schedule that would make the program legal under R7/R7c, and report cooling
time as a named component. Cooling is **global** (Doppler sheet beams cover the whole trap), so it
costs schedule time but does not serialize per ion.

## How to work

- **Python first**, correctness first. No C++ this session, no `numba` yet. PLAN §8.
- Pure-Python reference implementations are permanent, not scaffolding.
- Write tests as you go: `tests/test_golden_24ancilla.py` is the one that matters.
- Keep `Knowledge/` current: if you learn something the plan doesn't know, add it to
  `Knowledge/notes/` and rebuild. `python Knowledge/kg/query.py unsourced` must stay empty.
- Don't commit unless asked.

## Environment facts you will otherwise rediscover the hard way

- Default `python` is **3.14** at `C:\Python314`. Present: numpy, scipy, networkx, qiskit, stim,
  pymatching, ldpc, matplotlib, numba, cvxpy, z3. Absent: pybind11, duckdb, ortools.
- `kuzu` has **no 3.14 wheel**. `Knowledge/kg/build.py` and `query.py` detect this and re-exec
  themselves under `C:\Users\yezhu\AppData\Local\Programs\Python\Python312\python.exe`. Run them with
  any interpreter; don't "fix" the relaunch.
- Toolchain for later: `g++ 13.2` (MinGW) and `cmake 4.2.1` are installed; there is no MSVC `cl`.
- **arXiv ids must be quoted in YAML.** `2511.15910` unquoted parses as a float and silently becomes
  `2511.1591`, breaking every edge to it.
- `Library/papers/` and `Library/archives/` (~540 MB) and `Knowledge/kg/db` (~124 MB) are gitignored.
  The corpus is reproducible from `Library/seeds.txt` via `Library/tools/fetch_sources.py`.
- The Bash tool mangles backslashes and quotes inside multi-line Python heredocs. Write scripts to a
  file and run the file.

## Where the reasoning lives

- `docs/PLAN.md` — the plan. §0 findings, §1 the thesis, §3–§7 the stack, §10 milestones.
- `Knowledge/` — every literature fact with provenance, as a Kuzu graph over YAML notes.
  `query.py disputes` shows the 9 recorded contradictions; `query.py why` traces each plan decision
  back to the findings and papers behind it; `query.py open` is the question queue.
- `Library/` — the 78-paper corpus. `Knowledge/notes/papers/*.md` are the 8 mined so far.

The one thing to keep in view: the project's thesis (PLAN §1) is that **rigid lockstep rotation
removes WISE's 25× control-serialization penalty, because rotation needs exactly one movement
template where odd–even sort needs many.** M0–M2 exist to make that comparison trustworthy. If you
find yourself building something that doesn't serve it, stop and say so.
