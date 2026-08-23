# QCCD architecture design and compilation: literature map and project scope

**Cut-off date:** 19 August 2026  
**Target problem:** finite-capacity, shuttling-based trapped-ion QCCD architectures; hardware/control description; QASM-to-hardware compilation; visualization and verification; QEC-aware architecture search, especially the bivariate-bicycle (BB) `[[144,12,12]]` code.

**Deck note:** `ion_transport_deck_v3.pptx.pdf` and the referenced HTML visualization were not available in the working files, so the mapping to pages 13–15 below uses the two control limitations stated in the request. The primitive transition rules should be reconciled against the actual pages before implementation.

## Executive conclusion

The proposed software platform is useful, but its publishable novelty cannot simply be “describe arbitrary QCCD hardware and optimize ion motion.” Several recent systems already cover large parts of that surface:

- **Position Graph / SHAPER** represents heterogeneous shuttling hardware, positions, capacity, congestion, and native ion instructions.
- **MQT IonShuttler** provides exact SAT schedules on small grid memories and heuristic schedules at larger scale.
- **FluxTrap / TrapSIMD** models the globally synchronized transport constraint almost exactly as described in the deck: one junction-transport type and direction is broadcast per cycle, and inter-trap transport excludes intra-trap transport.
- **Architecting Scalable Trapped Ion Quantum Computers using Surface Codes** explores trap capacity, topology, wiring, logical error, cycle time, and power; its WISE model permits only one movement-primitive type to co-occur.
- **Cyclone** directly studies HGP and BB syndrome extraction on QCCD hardware. It explicitly discusses filled-trap “roadblocks,” replaces a grid by a synchronized ring codesign, and evaluates the `[[144,12,12]]` BB code.

The strongest defensible project is therefore:

> **A formally specified, control-aware and QEC-aware QCCD codesign system that generates executable schedules for parameterized architectures, mechanically verifies movement/resource legality, and optimizes a Pareto frontier while checking the resulting detector/circuit distance.**

For `[[144,12,12]]`, Cyclone is the first architecture that must be reproduced and beaten—not an optional related-work comparison. “Best architecture” is not a single answer until the hardware, control, noise, QEC gadget, and cost objectives are fixed.

## Four papers to read before writing code

| Paper | Why it is indispensable | What remains open |
|---|---|---|
| [TrapSIMD: SIMD-Aware Compiler Optimization for 2D Trapped-Ion Quantum Machines](https://arxiv.org/abs/2504.17886) | FluxTrap defines SIMD-like transport instructions and a SIMD-enriched position graph. It states that all participating junctions use one transport type and global direction per cycle; intra- and inter-trap transport are mutually exclusive. This is the closest match to the deck’s first limitation. | General user-defined control domains rather than one fixed SIMD machine; proof/checking of complete low-level schedules; QEC detector-distance guarantees. |
| [Cyclone: Designing Efficient and Highly Parallel QCCD Architectural Codesigns for Fault Tolerant Quantum Memory](https://arxiv.org/abs/2511.15910) | Directly identifies grid roadblocks, proposes a ring with ancillas moving in lockstep, and reports up to about three orders of magnitude LER improvement for BB `[[144,12,12]]` under its model. | Exact optimization under the deck’s primitives; correlated transport/heating/calibration noise; alternative extraction gadgets; exact detector/circuit distance; a full Pareto comparison including junction/control cost. |
| [Efficient Compilation for Shuttling Trapped-Ion Machines via the Position Graph Architectural Abstraction](https://arxiv.org/abs/2501.12470) | Closest general architecture abstraction and scalable compiler. Explicitly handles capacity, blocked movement, congestion, execution/storage locations, and native instructions. | A typed, executable hardware/control specification with formal transition semantics; global broadcast constraints; QEC-aware correctness. |
| [Architecting Scalable Trapped Ion Quantum Computers using Surface Codes](https://arxiv.org/abs/2510.23519) | A compiler-driven design-space exploration across capacity, topology, wiring, cycle time, LER, data rate, and power. Finds two-ion traps optimal in the studied standard-wiring surface-code model and exposes the WISE power/latency tradeoff. | It is surface-code-specific, not BB-specific; its answer does not transfer automatically to `[[144,12,12]]`; it does not settle arbitrary control-aware QCCD design. |

## Important correction to the SWAP analogy

QCCD shuttling and superconducting SWAP routing share a useful **token-routing** abstraction, but they are not physically or semantically identical:

- A SWAP exchanges two stationary quantum states through gates.
- A QCCD move transports the ion carrying the state, often requiring split, merge, junction crossing, reordering, cooling, and empty physical space.
- Ion order may be preserved by some primitives and changed only by an explicit swap/rotation primitive.
- A junction, segment, control domain, gate zone, or waveform generator can be a shared resource even when two routes are geometrically disjoint.

The closest classical combinatorial models are a mixture of **token swapping**, **pebble motion with vacancies**, **multi-agent path finding**, **job-shop/resource-constrained scheduling**, and **dynamic graph routing**. A compiler that reduces the machine to a static coupling graph will miss the crucial state and resource constraints.

## Direct overlap with the stated project plan

| Proposed item | Closest prior art | Necessary differentiator |
|---|---|---|
| 1. Design plan and scope | QCCDSim; compiler-driven hardware evaluation; surface-code QCCD DSE; Cyclone | State explicit theorem/guarantee and evaluation question, not merely a platform. |
| 2. Architecture syntax | Position Graph; FluxTrap’s SIMD-enriched position graph; MQT IonShuttler graph model | A portable, typed, versioned `HardwareSpec` with physical positions, capacities, roles, directions, and parameterized topology generators. |
| 3. Hardware-control syntax | FluxTrap S3/JT-SIMD ISA; WISE; electrode-broadcast/grid work | A separate `ControlSpec` for broadcast domains, allowed primitive equivalence classes, concurrency/exclusion, controller/DAC budgets, and waveform bindings. |
| 4. Formal rules | Exact SAT shuttling; position-graph executability; backend compiler phases | Small-step operational semantics plus a schedule checker that emits a proof trace or minimal counterexample. |
| 5. Visualization | MQT IonShuttler visual outputs and existing architecture simulators | Visualization driven from the same transition semantics as the checker, with resource/control-domain overlays and explainable conflicts. |
| 6. Existing architectures as examples | QCCDSim, MQT IonShuttler, Position Graph, FluxTrap, Cyclone | A conformance suite translating published architectures without silently weakening their assumptions. |
| 7. Optimized QASM-to-hardware compiler | QCCDSim, SHAPER/SHAW, MQT IonShuttler, S-SYNC, Moveless, FluxTrap, adaptive routing, RL/LLM compilers | QEC-aware compilation with certified legality, pluggable exact/heuristic backends, and detector/circuit-distance validation. |

## Recommended system decomposition

Do not put geometry, control, program, schedule, and noise into one monolithic language. Use five layers.

### 1. `HardwareSpec`

Represent:

- sites/slots, linear segments, traps, turns, T/X/L junctions, loading ports;
- capacity at each trap/site/resource;
- directed or bidirectional transport connectivity;
- functional zones: memory, gate, cooling, measurement/reset, loading;
- legal resident ion species and mixed-species-chain constraints;
- geometric quantities: length, pitch, turn angle, junction type;
- topology generators: linear, racetrack, grid, grate, Cyclone ring, hybrid ring/grid.

### 2. `ControlSpec`

Represent:

- controller and waveform/broadcast domains;
- an operation-signature equivalence relation: which primitive invocations count as “the same operation” for concurrency;
- allowed masks: participating sites/junctions may execute or idle;
- global direction/type restrictions;
- conflicts between operation classes, such as inter-trap vs intra-trap motion;
- limits on simultaneous gates, measurements, cooling operations, laser beams, DACs, and junction traversals;
- setup/switching latency and the power/data cost of each control configuration.

This cleanly captures both the deck’s restrictive SIMD machine and less-restrictive future machines.

### 3. Stateful transport IR

Suggested native primitives:

`prepare`, `measure`, `reset`, `gate1`, `gate2`, `split`, `merge`, `move`, `cross_junction`, `turn`, `swap`, `rotate_or_reorder`, `cool`, `wait`, and `set_control_mode`.

Each instruction should have preconditions, effects, duration, resource claims, and a noise/cost annotation. A hardware instance can disable unsupported primitives or refine one logical primitive into several waveform-level steps.

### 4. Schedule and verifier

At minimum, verify:

1. every ion has exactly one location;
2. all site/trap capacities are respected at all event boundaries and during transit;
3. no two chains occupy the same exclusive segment or junction;
4. split/merge acts on a legal end ion or legal subchain;
5. order is preserved unless an explicit reordering primitive permits a change;
6. gate operands are co-located in a compatible gate zone;
7. control-domain operation signatures are compatible in every time interval;
8. controller, laser, measurement, cooling, and DAC limits are respected;
9. species and cooling/motional-state requirements hold;
10. final logical-to-physical mapping and measurement semantics match the input program.

Return a minimal conflicting set of operations/resources when validation fails. This makes the visualizer useful for human verification rather than decorative playback.

### 5. QEC semantic layer

For a memory experiment, schedule legality is not enough. The compiled syndrome-extraction circuit must be checked for:

- ideal stabilizer-measurement equivalence;
- detector construction across repeated rounds;
- hook-error propagation and the chosen ancilla gadget;
- accepted-undetected logical faults;
- **circuit/detector distance**, not just code distance;
- decoder compatibility and real-time latency;
- transport, cooling, waiting, gate, preparation, and measurement noise.

Shor cat-state, Knill/teleportation, flag, and single-ancilla gadgets are different design points. “Hook suppression” or “a flag catches some faults” is not equivalent to proving circuit distance 12. [High-performance syndrome extraction circuits for quantum codes](https://arxiv.org/abs/2603.05481) proves that a non-interleaved syndrome-extraction circuit cannot attain circuit distance 12 for the Gross `[[144,12,12]]` code. Therefore, the architecture search must either optimize a broader gadget family or state the lower target distance honestly.

## What is currently the best architecture for BB `[[144,12,12]]`?

### Evidence-supported answer

Under the assumptions evaluated in the published paper, **Cyclone’s synchronized ring is the leading direct answer**. It partitions data around a cycle, reuses roughly half the stabilizer count in ancillas, moves the ancillas in lockstep, removes filled-trap roadblocks, and completes an X and a Z pass through two full rotations. The paper reports up to roughly three orders of magnitude better logical error rate for `[[144,12,12]]` than its grid baseline.

That result does **not** prove global optimality. It is conditional on:

- the paper’s allowed schedule/gadget family;
- its base stochastic gate/preparation/measurement noise plus latency-derived Pauli-twirled decoherence model;
- its fixed operation times and cooling assumptions;
- its selected grid baselines and cost definition;
- no exact proof of detector/circuit distance 12;
- no comprehensive correlated motional-heating, junction-history, leakage, crosstalk, calibration, or controller-failure model.

Thus the right near-term question is:

> For BB `[[144,12,12]]`, under the exact pages 13–15 primitive semantics and a parameterized number of vertical broadcast lines, what is the Pareto-optimal architecture/schedule over logical failure rate, cycle time, junctions, controllers, ancillas, capacity, and verified circuit distance, relative to Cyclone and grid baselines?

### Architecture candidates that should be compared

1. The deck’s grid with 24 vertical lines / 24 ancillas.
2. The same grid with a sweep over the number and placement of vertical lines.
3. The industrial-style and alternate grids used in Cyclone.
4. Base Cyclone ring.
5. Compressed Cyclone rings with fewer, larger traps.
6. Racetrack.
7. Horizontal grate, vertical grate, and full grid from MQT IonShuttler work.
8. A hybrid ring with selected vertical express lanes.
9. A topology synthesized from the BB Tanner graph subject to planar/junction/control budgets.

### Optimization objectives

Report a Pareto frontier, not one weighted score:

- QEC-round makespan and worst ion idle time;
- movement distance and counts of split/merge/turn/junction/swap/reorder/cool operations;
- number of junctions, vertical lines, traps, sites, DAC/control channels, gate and measurement zones;
- trap capacity and occupancy slack over time;
- ancilla and coolant-ion counts;
- motional-energy proxy and decoherence exposure;
- physical control power/data rate;
- circuit/detector distance and logical error per round;
- decoder latency and sustainable syndrome throughput.

### Solver plan

- Use SAT/SMT/CP-SAT or time-expanded ILP for small instances and for optimality certificates/lower bounds.
- Use a scalable scheduler for `[[144,12,12]]`: constraint programming with large-neighborhood search, conflict-based MAPF variants, or architecture-aware list scheduling.
- Seed the heuristic with the code symmetry and Cyclone schedule; do not expect a generic QASM gate router to rediscover QEC structure.
- Compile exactly the same syndrome circuit and noise model across all hardware candidates before comparing architecture.
- Separately sweep syndrome-extraction gadgets; otherwise architecture and gadget effects will be conflated.

## Long-term “best code + architecture for breakeven”

Breakeven must be defined before optimization. At least three definitions are used in practice:

1. logical memory lifetime exceeds the best constituent physical-qubit lifetime;
2. logical error per QEC round is lower than physical error over an equivalent elapsed time;
3. an encoded application/logical operation beats the best unencoded implementation at equal resources or wall time.

The 2026 trapped-ion [Breakeven demonstration of quantum low-density parity-check codes](https://arxiv.org/abs/2606.06455) is a crucial baseline, but it used an optical-metastable-ground implementation specifically avoiding ion transport and dedicated coolant ions. It does not establish breakeven for a routed QCCD machine.

The long-term optimizer must jointly choose:

- code family and finite-length instance;
- syndrome-extraction gadget and ordering;
- architecture topology/capacity/control wiring;
- mapping and dynamic schedule;
- cooling and dynamical-decoupling policy;
- decoder and number of rounds;
- logical workload and breakeven definition.

The strongest current full-stack comparator is [Fault-Tolerant Quantum Computing with Trapped Ions: The Walking Cat Architecture](https://arxiv.org/abs/2604.19481), which includes qLDPC codes, QEC protocols, compiler, microarchitecture, decoder, and simulations. Tour de Gross is the key BB logical-architecture comparator; Cyclone is the key BB QCCD-memory comparator.

## Prioritized literature corpus

Priority labels:

- **P0:** directly constrains the proposed contribution or `[[144,12,12]]` experiment.
- **P1:** needed for a credible hardware/compiler/QEC implementation.
- **P2:** adjacent architecture, decoder, theory, or survey useful for context.

### A. QCCD architecture, transport, and control hardware

| Pri. | Paper / source | Relevance |
|---|---|---|
| P0 | Kielpinski, Monroe, Wineland, [Architecture for a large-scale ion-trap quantum computer](https://www.nist.gov/document/wineland-nature-417pdf), Nature 417 (2002) | Original QCCD proposal; no verified arXiv source package. |
| P0 | Pino et al., [Demonstration of the trapped-ion quantum CCD computer architecture](https://arxiv.org/abs/2003.01293) | Integrated experimental QCCD primitives and system operation. |
| P0 | Moses et al., [A Race-Track Trapped-Ion Quantum Processor](https://arxiv.org/abs/2305.03828) | Racetrack topology, broadcast/cowiring motivations, scalable routing/control. |
| P0 | Delaney et al., [Scalable Multispecies Ion Transport in a Grid-Based Surface-Electrode Trap](https://arxiv.org/abs/2403.00756) | Closest grid hardware and scalable electrode-control reference. |
| P0 | Malinowski et al., [How to Wire a 1000-Qubit Trapped Ion Quantum Computer](https://arxiv.org/abs/2305.12773) | WISE wiring and global reconfiguration tradeoffs. |
| P1 | Hensinger et al., [T-junction ion trap array for two-dimensional ion shuttling, storage, and manipulation](https://arxiv.org/abs/quant-ph/0508097) | Early junction transport. |
| P1 | Hucul et al., [On the Transport of Atomic Ions in Linear and Multidimensional Ion Trap Arrays](https://arxiv.org/abs/quant-ph/0702175) | Primitive transport physics and heating. |
| P1 | Blakestad et al., [High-Fidelity Transport of Trapped-Ion Qubits through an X-Junction Trap Array](https://arxiv.org/abs/0901.0533) | X-junction transport fidelity. |
| P1 | Bowler et al., [Coherent Diabatic Ion Transport and Separation in a Multizone Trap Array](https://arxiv.org/abs/1206.0780) | Fast transport/separation primitives. |
| P1 | Walther et al., [Controlling Fast Transport of Cold Trapped Ions](https://arxiv.org/abs/1206.0364) | Control waveform and excitation constraints. |
| P1 | Wright et al., [Reliable Transport through a Microfabricated X-Junction Surface-Electrode Ion Trap](https://arxiv.org/abs/1210.3655) | Junction-crossing reliability. |
| P1 | Kaufmann et al., [Fast ion swapping for quantum-information processing](https://arxiv.org/abs/1607.03734) | Physical reordering/swap primitive. |
| P1 | Kaushal et al., [Shuttling-Based Trapped-Ion Quantum Information Processing](https://arxiv.org/abs/1912.04712) | Essential review of primitives, electrodes, waveform generation, and system requirements. |
| P1 | Bruzewicz et al., [Trapped-ion quantum computing: Progress and challenges](https://arxiv.org/abs/1904.04178) | Broad hardware review. |
| P1 | Lekitsch et al., [Blueprint for a microwave trapped-ion quantum computer](https://arxiv.org/abs/1508.00420) | Large modular, transport-based architecture. |
| P1 | Brown, Kim, Monroe, [Co-designing a scalable quantum computer with trapped atomic ions](https://arxiv.org/abs/1602.02840) | Architecture/QEC codesign. |
| P1 | Monroe et al., [Large-scale modular quantum-computer architecture with atomic memory and photonic interconnects](https://arxiv.org/abs/1208.0391) | Alternative modular interconnect baseline. |
| P1 | Mordini et al., [Multi-zone trapped-ion qubit control in an integrated photonics QCCD device](https://arxiv.org/abs/2401.18056) | Optical-zone resources and scalable control. |
| P1 | Sterk et al., [Multi-junction surface ion trap for quantum computing](https://arxiv.org/abs/2403.00208) | Multi-junction device design, RF power, and heating. |
| P1 | [Scalable, high-fidelity all-electronic control of trapped-ion qubits](https://arxiv.org/abs/2407.07694) | Control-resource architecture. |
| P1 | [Multiplexed Control at Scale for Electrode Arrays in Trapped-Ion Quantum Processors](https://arxiv.org/abs/2504.01815) | DAC/waveform scaling. |
| P1 | [Toolchain for shuttling trapped-ion qubits in segmented traps](https://arxiv.org/abs/2601.08495) | Converts physical trap/electrode models into low-excitation waveforms. |
| P1 | [A framework for the benchmarking of transport-induced errors in trapped-ion quantum processors](https://arxiv.org/abs/2605.25118) | Primitive-level motional/heating error characterization. |
| P1 | [Shuttling in Bidimensional Segmented Ion-Trap Quantum Processors](https://arxiv.org/abs/2606.21899) | 2D shuttling compilation at the primitive layer. |
| P2 | [Ion transport and reordering in a two-dimensional trap array](https://arxiv.org/abs/2003.03520) | Physical 2D reordering. |
| P2 | [Closed-loop optimization of fast trapped-ion shuttling with sub-quanta excitation](https://arxiv.org/abs/2201.07358) | Calibration-aware waveform optimization. |
| P2 | [Vertical ion transport in a surface Paul trap: escalator and elevator concepts](https://arxiv.org/abs/2603.06208) | Non-planar transport option. |
| P2 | [Improving Dynamical Decoupling for Trapped-Ion QCCD Quantum Computers](https://arxiv.org/abs/2607.14441) | Waiting/scheduling errors interact with memory protection. |

### B. QCCD compilation, routing, scheduling, and design-space exploration

| Pri. | Paper / source | Relevance |
|---|---|---|
| P0 | Murali et al., [Architecting Noisy Intermediate-Scale Trapped Ion Quantum Computers](https://arxiv.org/abs/2004.04706) | QCCDSim; trap capacity, topology, gate fidelity, split/move/merge/junction cost. |
| P0 | Schmale et al., [Backend compiler phases for trapped-ion quantum computers](https://arxiv.org/abs/2206.00544) | Native backend phases and legality. |
| P0 | Kreppel et al., [Quantum Circuit Compiler for a Shuttling-Based Trapped-Ion Quantum Computer](https://arxiv.org/abs/2207.01964) | Full circuit-to-shuttling compiler. |
| P0 | Schoenberger et al., [Using Boolean Satisfiability for Exact Shuttling in Trapped-Ion Quantum Computers](https://arxiv.org/abs/2311.03454) | Exact schedules and lower-bound baseline. |
| P0 | Schoenberger et al., [Shuttling for Scalable Trapped-Ion Quantum Computers](https://arxiv.org/abs/2402.14065) | Blocking, cycles, conflict-free schedules, grid/racetrack/grate topologies; MQT IonShuttler. |
| P0 | Ovide et al., [Scaling and assigning resources on ion trap QCCD architectures](https://arxiv.org/abs/2408.00225) | Capacity, excess space, linear/ring resource assignment. |
| P0 | Bach, Safro, Younis, [Efficient Compilation for Shuttling Trapped-Ion Machines via the Position Graph Architectural Abstraction](https://arxiv.org/abs/2501.12470) | General architecture model and SHAPER/SHAW compiler. |
| P0 | Ovide, Almudever, [Exploring the trade-off between operation parallelism and qubit movement in ion trap quantum computing](https://arxiv.org/abs/2502.04181) | Direct movement-vs-parallelism objective. |
| P0 | Ruan et al., [TrapSIMD: SIMD-Aware Compiler Optimization for 2D Trapped-Ion Quantum Machines](https://arxiv.org/abs/2504.17886) | Direct model of “same operation/type/direction” concurrency. |
| P0 | Zhu et al., [S-SYNC: Shuttle and Swap Co-Optimization in Quantum Charge-Coupled Devices](https://arxiv.org/abs/2505.01316) | Joint shuttle/SWAP routing. |
| P0 | Schoenberger et al., [Orchestrating Multi-Zone Shuttling in Trapped-Ion Quantum Computers](https://arxiv.org/abs/2505.07928) | Multiple processing zones and scalable scheduling. |
| P0 | Khan et al., [Moveless: Minimizing Overhead on QCCDs via Versatile Execution and Low Excess Shuttling](https://arxiv.org/abs/2508.03914) | QEC-specialized compilation: move only one partition, reorder checks, reuse ancillas. |
| P0 | Jones, Murali, [Architecting Scalable Trapped Ion Quantum Computers using Surface Codes](https://arxiv.org/abs/2510.23519) | QEC-aware architecture DSE, capacity/topology/wiring/power/LER. |
| P0 | Khan et al., [Cyclone: Designing Efficient and Highly Parallel QCCD Architectural Codesigns for Fault Tolerant Quantum Memory](https://arxiv.org/abs/2511.15910) | Direct BB/HGP QCCD codesign and `[[144,12,12]]` comparator. |
| P0 | Ovide et al., [Adaptive Parallelism-Aware Qubit Routing for Ion Trap QCCD Architectures](https://arxiv.org/abs/2603.19969) | Adaptive routing objective across layouts. |
| P0 | Bach et al., [Scaling Qubit Mapping and Routing With Position Graph for Trapped-Ion Quantum Computers](https://arxiv.org/abs/2605.09237) | Scalable position-graph routing and congestion memoization. |
| P0 | Schier et al., [Reinforcement learning for ion shuttling on trapped-ion quantum computers](https://arxiv.org/abs/2605.22463) | Learned scheduler adaptable to geometry; compare with exact small instances. |
| P0 | Kreppel et al., [Efficient LLM-Generated Shuttling Compilers for Complex Trapped-Ion Quantum Computers](https://arxiv.org/abs/2607.24714) | Arbitrary connected trap graphs and generated compiler baselines. |
| P1 | Wu et al., [TILT: Achieving Higher Fidelity on a Trapped-Ion Linear-Tape Quantum Computing Architecture](https://arxiv.org/abs/2010.15876) | Non-QCCD moving-tape alternative and compiler. |
| P1 | Kreppel et al., [Shuttling Compiler for Trapped-Ion Quantum Computers Based on Large Language Models](https://arxiv.org/abs/2512.18021) | Layout-generalization baseline on small machines. |
| P1 | [MUSS-TI: Multi-Level Scheduling for Scalable Trapped-Ion Quantum Computers](https://arxiv.org/abs/2509.25988) | Scheduling across QCCD modules/photonic links. |
| P1 | [SDQC: Distributed Quantum Computing Architecture Utilizing Entangled Ion Qubit Shuttling](https://arxiv.org/abs/2512.02890) | Alternative local-shuttling/distributed codesign. |
| P1 | [MQT IonShuttler](https://github.com/munich-quantum-toolkit/ionshuttler) | Open-source exact and heuristic scheduler to reuse/benchmark. |
| P1 | [QCCDSim](https://github.com/prakashmurali/QCCDSim) | Open-source architecture simulator/compiler baseline. |
| P1 | Schoenberger et al., [Using Compiler Frameworks for the Evaluation of Hardware Design Choices](https://www.cda.cit.tum.de/files/eda/2024_qce_Using_Compiler_Frameworks_for_the_Evaluation_of_Hardware_Design_Choices.pdf) | Very close platform motivation; no verified arXiv source. |
| P1 | Saki et al., *Muzzle the Shuttle: Efficient Compilation for Multi-Trap Trapped-Ion Quantum Computers*, DATE 2022 | Early multi-trap routing; no verified arXiv source. |
| P1 | Dai et al., *Advanced Shuttle Strategies for Parallel QCCD Architectures*, IEEE TQE 2024, [DOI](https://doi.org/10.1109/TQE.2024.3408757) | Parallel shuttling strategy; no verified arXiv source. |
| P1 | Tseng et al., *SMT-Based Qubit Mapping for Trapped-Ion Quantum Computing*, ISPD 2024 | Exact/formal mapping; no verified arXiv source. |
| P2 | [Quantum Compiler Design for Qubit Mapping and Routing: Evolution and Trends in Trapped-Ion Quantum Computing](https://arxiv.org/abs/2505.16891) | Recent compiler survey and bibliography cross-check. |

### C. QEC, BB/HGP codes, fault-tolerant extraction, and breakeven

| Pri. | Paper / source | Relevance |
|---|---|---|
| P0 | Bravyi et al., [High-threshold and low-overhead fault-tolerant quantum memory](https://arxiv.org/abs/2308.07915) | Introduces the important finite BB instances, including the Gross code context. |
| P0 | Giannisis Manes, Claes, [Distance-preserving stabilizer measurements in hypergraph product codes](https://arxiv.org/abs/2308.15520) | Effective-distance foundation; scope must not be overgeneralized to every circuit model/code. |
| P0 | Strikis, Browne, Beverland, [High-performance syndrome extraction circuits for quantum codes](https://arxiv.org/abs/2603.05481) | Residual-error/circuit-distance analysis; proves non-interleaved Gross-code circuits cannot reach 12. |
| P0 | Derks et al., [Designing fault-tolerant circuits using detector error models](https://arxiv.org/abs/2407.13826) | Detector-level specification and circuit engineering. |
| P0 | Yoder et al., [Tour de Gross: A modular quantum computer based on bivariate bicycle codes](https://arxiv.org/abs/2506.03094) | BB distances 12 and 18, logical instruction set, modular architecture. |
| P0 | Tripier et al., [Fault-Tolerant Quantum Computing with Trapped Ions: The Walking Cat Architecture](https://arxiv.org/abs/2604.19481) | Current full-stack qLDPC/trapped-ion architecture comparator. |
| P0 | Tham et al., [Breakeven demonstration of quantum low-density parity-check codes](https://arxiv.org/abs/2606.06455) | Experimental breakeven baseline; notably avoids ion transport. |
| P0 | Aydin et al., [Cyclic Hypergraph Product Code](https://arxiv.org/abs/2511.09683) | Code symmetry co-designed with a planar, constant-depth QCCD layout. |
| P0 | Chandra et al., [Distributed Quantum Error Correction with Bivariate Bicycle Codes in a Modular Architecture](https://arxiv.org/abs/2605.04663) | Direct `[[144,12,12]]` alternative across 4/6/12 modules. |
| P1 | Delfosse, Beverland, Tremblay, [Bounds on stabilizer measurement circuits and obstructions to local implementations of quantum LDPC codes](https://arxiv.org/abs/2109.14599) | Lower bounds linking Tanner and hardware graphs. |
| P1 | Tremblay, Delfosse, Beverland, [Constant-overhead quantum error correction with thin planar connectivity](https://arxiv.org/abs/2109.14609) | Layered planar layout baseline. |
| P1 | Berthusen et al., [Toward a 2D Local Implementation of Quantum LDPC Codes](https://arxiv.org/abs/2404.17676) | BB routing/locality comparison. |
| P1 | Zhou et al., [Louvre: Relaxing Hardware Requirements of Generalized Bicycle Codes via Routing](https://arxiv.org/abs/2508.20858) | Connectivity/depth tradeoff for BB syndrome extraction. |
| P1 | deMarti iOlius et al., [An almost-linear time decoding algorithm for quantum LDPC codes under circuit-level noise](https://arxiv.org/abs/2409.01440) | BP+OTF detector-model decoder and runtime. |
| P1 | [A matching decoder for bivariate bicycle codes](https://arxiv.org/abs/2602.22770) | Decoder choice for BB evaluation. |
| P1 | Kang et al., [QUITS: A modular Qldpc code circUIT Simulator](https://arxiv.org/abs/2504.02673) | Pluggable code/circuit/noise/decoder simulation. |
| P1 | Wang, Mueller, [Coprime Bivariate Bicycle Codes](https://arxiv.org/abs/2408.10001) | Code-instance search and BB structure. |
| P1 | [Logical operators and fold-transversal gates of bivariate bicycle codes](https://arxiv.org/abs/2407.03973) | BB logical operations. |
| P1 | Geher et al., [Tangling schedules eases hardware connectivity requirements for quantum error correction](https://arxiv.org/abs/2307.10147) | Circuit scheduling can trade against hardware connectivity. |
| P2 | Wolanski, Barber, [Ambiguity Clustering: an accurate and efficient decoder for qLDPC codes](https://arxiv.org/abs/2406.14527) | Reports Gross-code decoding speed relevant to ion timescales. |
| P2 | Ye, Delfosse, [Quantum Error Correction for Long Chains of Trapped Ions](https://arxiv.org/abs/2503.22071) | Alternative to aggressive QCCD partitioning. |
| P2 | [Logical Error Rates for the Surface Code Under a Mixed Coherent and Stochastic Circuit-Level Noise Model Inspired by Trapped Ions](https://arxiv.org/abs/2508.14227) | More realistic trapped-ion QEC noise reference. |
| P2 | [Quantum LDPC codes with design rate 1/5 and good performance at moderate blocklength](https://arxiv.org/abs/2607.27644) | New movable-qubit-oriented code candidates for the long-term code+architecture search. |

## Recommended reading order

1. TrapSIMD, Cyclone, Position Graph, surface-code architecture DSE.
2. MQT IonShuttler SAT paper and scalable cycle-based scheduler.
3. QCCDSim and backend/compiler papers.
4. Pino, race-track, Delaney grid hardware, WISE, shuttling review.
5. Bravyi BB, high-performance extraction circuits, Tour de Gross, detector error models.
6. Walking Cat and the qLDPC breakeven experiment.
7. Remaining transport physics, decoder, and adjacent architecture papers as the concrete model is refined.

## Minimal research milestones

### Milestone 1 — executable semantics

- Translate pages 13–15 of the deck into primitive preconditions/effects.
- Encode the exact “same operation” equivalence relation and vertical-line parameter.
- Implement a trace checker and conflict-explaining visualizer.
- Reproduce every primitive example in the deck as a conformance test.

### Milestone 2 — baselines

- Import grid, racetrack, grate, Position Graph examples, and Cyclone.
- Reproduce at least one published exact small-instance result and one published large heuristic result.
- Add QCCDSim and MQT IonShuttler adapters or honest semantic comparison tests.

### Milestone 3 — BB `[[144,12,12]]`

- Generate identical syndrome-extraction workloads for all candidates.
- Sweep vertical lines, ancillas, capacity, control domains, and junction costs.
- Reproduce Cyclone’s directionally synchronized ring schedule.
- Compute Pareto frontiers and detector/circuit-distance evidence.

### Milestone 4 — breakeven codesign

- Add transport/calibration/cooling-aware noise.
- Include at least BB, cyclic HGP, and one Walking-Cat-style LDPC instance, plus surface-code control.
- Jointly optimize code, extraction gadget, hardware, compiler, and decoder throughput under a stated breakeven definition.

## Reproducibility and claim discipline

- State whether an architecture is a physical electrode layout, a zone graph, or only a routing abstraction.
- State whether a “move” is an individual ion, a fixed chain, or a variable subchain.
- Do not treat code distance, effective distance, circuit distance, and decoder-observed logical slope as interchangeable.
- Separate exact optimum, lower bound, heuristic best-known, and analytically constructed schedule.
- Compare architectures under the same primitive and noise assumptions.
- Preserve negative results: infeasible instances and minimal resource conflicts are valuable architecture evidence.
- Keep non-arXiv, source-unavailable publications in the bibliography rather than silently excluding them from the literature review.

## Companion source-download prompt

The reusable prompt in `qccd_latex_source_download_prompt.md` contains the verified arXiv seed list, citation-snowball instructions, secure extraction requirements, rate limiting, manifests, and a source-unavailable ledger for journal/conference papers.

## Search limits

This is a comprehensive scoped corpus, not a logically provable list of every document ever written. Inclusion required direct relevance to at least one of: QCCD transport/control hardware, capacity-aware shuttling compilation, QEC-aware trapped-ion architecture, BB/HGP syndrome extraction and routing, or code–architecture breakeven. The download prompt is designed to update the corpus through keyword search and backward/forward citation snowballing.
