# Physics background

Everything on this website is a picture of a real machine: ions held in a trap, moved between traps by voltages on electrodes, entangled by laser pulses, and slowly warmed by the world around them. This page walks from the trap up to the noise model, in the order you need it to read the studio. Each section says which knob in the studio it explains and which of [the rules](../rules/) it stands behind. The numbers quoted are the ones the default device document carries; every one has a source on the [Publications](../publications/) page.

## Trapping a single ion

An ion is a charged atom, so an electric field pushes on it. But a static field cannot hold a charge in place: Earnshaw's theorem says a potential with no charges inside it has no minimum, only saddles. The Paul trap escapes this with an oscillating field. A radio-frequency (RF) voltage at a frequency Ω of tens of megahertz is applied to one set of electrodes, and the ion, too heavy to follow the oscillation, feels only its time average. That average is a **pseudopotential** proportional to the square of the RF field amplitude, `Ψ = q²·|E_rf|² / (4·m·Ω²)`, which is smallest where the RF field vanishes. The line along which the RF field is zero is the **RF null**, and the ion sits on it.

The RF null is a line, not a point, so a second set of electrodes carries static (DC) voltages that shape the potential along that line into wells. Each well is a **trapping site**: a place an ion, or a short chain of ions, can rest. Changing the DC voltages moves the wells, and an ion follows its well. This is the whole mechanism of transport, and it is why the control system in a later section is a story about voltages and wires.

In a **surface-electrode trap** all the electrodes lie in one plane on a chip and the ion floats above it, typically at a height of tens of micrometres. Above a planar electrode the potential has a closed form: each electrode contributes the solid angle it subtends at the ion, divided by 2π. The reference page on [the electrodes a device implies](phys.md) uses exactly that model to work out, from a layout drawn in the studio, where the ions would sit and how much metal it takes.

Inside the well the ion oscillates like a mass on a spring at a **secular frequency** ω of a few megahertz. Quantum mechanically that oscillator has energy levels spaced by `ħω`, and its state is described by the mean number of quanta it holds, written **n̄**. An ion fresh from Doppler cooling has n̄ around ten; resolved-sideband cooling brings it below 0.1, close to the motional ground state. Almost every cost on this website is measured in these quanta, so n̄ is the number to keep in mind.

## The qubit inside the ion

The qubit is two internal electronic states of the ion. In a hyperfine qubit both states belong to the electronic ground level and differ only in the orientation of the nuclear spin, which makes them extraordinarily insensitive to noise: coherence times of minutes are measured. The default document names Ba⁺ as the qubit ion and carries a coherence time `T_coh` of 600 s; a second species, Sr⁺, is loaded alongside it as a **coolant**, for a reason explained under cooling.

Preparing and reading the qubit both use light. Optical pumping drives the ion into one of the two states, which is a **reset** (50 µs, with an error of 0.5% in the document). Reading it out shines a laser that makes one state fluoresce and the other stay dark, and a detector counts the photons; this is a **measurement** (120 µs, 99.84% correct). Together these are the state preparation and measurement (SPAM) errors, and they enter the fidelity estimate directly, once per measurement.

## Gates, and why they need cold ions

A single-qubit gate is a resonant pulse that rotates the qubit; in the document it takes 5 µs and fails once in forty thousand times. The two-qubit gate is the **Mølmer–Sørensen** (MS) gate. Two ions in the same well share a motional mode, a normal mode of the chain. A pair of laser beams, detuned close to a motional sideband, exerts a spin-dependent force on both ions; the shared mode is pushed out and back, and the phase it picks up along the way depends on the product of the two spins. When the pulse ends the motion has returned to where it started and only the entangling phase remains.

Two consequences shape the whole design. The ions must share a mode, so they must be in the same trap, and a trap where the beams can reach is a **gate zone**; rule [R6b](../rules/#R6b) says a two-qubit gate acts only on ions co-located in one. And the motion must be cold: the gate's error grows with the number of quanta in the mode, because a hot mode blurs the spin-dependent displacement. Quantinuum's H2 processor measured a two-qubit infidelity of 1.84e-3 with cold ions, and the document carries that as the gate's error at n̄ = 0.

## Motion as the currency

Every operation on an ion trap is paid for in motional quanta, and the two ways of paying back are the two kinds of **cooling**. Doppler cooling scatters photons from a laser tuned just below an atomic resonance; the ion absorbs photons preferentially when moving towards the beam, and each scattering event removes a little momentum. It is fast and works on the whole trap at once from sheet beams, which is why the document's **cool** primitive is a single 300 µs operation that cools every ion in the machine and does not have to be scheduled per ion. Sideband cooling goes further, to the ground state, one quantum at a time.

Cooling the qubit ion directly would scatter photons off the qubit and destroy its state. This is the job of the second species: a laser tuned to the coolant ion cools it, and since the coolant and the qubit ion sit in the same well they share motional modes, so the qubit's motion is cooled **sympathetically** without its internal state being touched. This is why the species entry names a coolant and why the document keeps half the ions as coolants.

Cooling costs time, and time is not free: the qubit dephases against `T_coh`, and, as the noise section explains, the ion warms up just by waiting. So the compiler does not cool after everything. It cools when it must, and knowing when it must is what rule [R7](../rules/#R7) decides.

## The QCCD idea

A single trap can hold a long chain of ions, but every ion added makes the chain's modes denser and the gates slower and less precise; rule [R13](../rules/#R13) records that two-qubit gate time degrades sharply beyond roughly fifteen ions per trap. The **quantum charge-coupled device** (QCCD), proposed by Kielpinski, Monroe and Wineland in 2002, keeps the chains short by using many small traps and moving ions between them, so that any two qubits can be brought together for a gate. The name borrows from the charge-coupled device in a camera, which also shifts charges from cell to cell.

In the studio a device is a map. A **site** is a trapping well; a **segment** is the stretch of RF null an ion is shuttled along between sites; a **junction** is a place where three or more trap axes meet; a **transport loop** is a closed path along which a whole ring of ions can be rotated in step; and a **zone type** is the label a site carries saying how many ions fit there and whether a gate, a measurement or cooling can happen. Reading the physics above back into the map: a gate needs ions co-located at a gate zone, a shuttle is a well walked along a segment, and everything that moves an ion heats it.

## Transport primitives, and what each one costs

Transport is done by walking the DC voltages. The elementary moves are the **primitives**, and each is a curve of measured points trading speed against motional excitation: run it faster and the ion picks up more quanta. The table below is the default document's `qccdsim_jones` operating point, which comes from Jones and Murali's cost table; the `transport_excitation` table from Munoz and colleagues offers a slower, colder point for several of them, and the studio lets you choose which point a design runs at.

| primitive | what it is | time | quanta |
|---|---|---:|---:|
| shuttle, one segment | the well slides one site along the RF null | 5 µs | 0.1 |
| split or merge | one well is pinched into two, or two joined into one | 80 µs | 6.0 |
| junction crossing | the ion passes a node where three axes meet | 100 µs | 3.0 |
| ion swap | two ions in one well exchange order by rotating the crystal | 18 µs | 1.0 |
| MS gate | the entangling gate | 25 µs | at most 1.0, by rule [R7](../rules/#R7) |
| cool | global Doppler cooling, every ion at once | 300 µs | removes all |

Two of these deserve a word. Splitting a chain is sixty times as hot as a shuttle, because the well must be deformed through a point where the confinement along the axis nearly vanishes. And a junction is not simply a bend: where three or more RF nulls meet, the RF field cannot vanish smoothly, and residual **RF barriers** rise on the approaches; carrying an ion over them without excitation takes a carefully shaped waveform and still heats. A corner of the RF null, where two axes meet, has no such barrier, and rule [R18](../rules/#R18) says a bend is ordinary transport. Measured on an X-junction trap, an ion survived about 65 uncooled round trips at over 98% and was **lost** from the trap beyond about 85, so excitation is not only a fidelity problem but, past a point, a loss of the qubit.

A **docked** site off a loop is reached by a split at the loop and a merge at the dock, so docking is charged for both; a rotation of the whole loop, in which every well moves together, entails neither.

## The control system

Under the chip, each DC electrode is driven by a digital-to-analogue converter (DAC), and a transport is a set of voltage waveforms played on the electrodes along the path. The default document's traps have 24 electrodes each and its junctions 48, so a machine with hundreds of sites has tens of thousands of electrodes, and one DAC per electrode is the first thing that does not scale. This is the **control plane** in the studio, and it is what the DAC count on the report measures, against the budget the document declares: at most so many DACs, junctions and square millimetres of trap.

The scalable answer is **broadcast wiring**: electrodes that play the same role on every trap are wired to the same DAC, so that one waveform moves every ion whose site is wired to it. In the WISE scheme this holds the number of dynamic DACs at about a hundred regardless of the size of the machine, at the price that only one kind of motion can happen in a cycle. Rule [R4](../rules/#R4) is that price: at most one movement class is active per cycle, a class fixes a type and a direction, and any ion whose site is wired to the active waveform takes part unless a per-site switch opts it out. Two neighbouring ions cannot simply pass one another, because the waveform that moves one left moves the other left too; moving them past each other is a sequence of sorts, and a compiler for such a machine is scheduling a SIMD processor. Rule [R4d](../rules/#R4d) checks that each cycle can actually be driven by the declared channels, and the wiring tab of the studio is where the channels and switches are declared.

The optical side is wired the same way: the document uses global beams with a switch per zone, so a gate pulse reaches every gate zone that is switched on. Cooling, being a sheet beam over the whole trap, needs no addressing at all, which is what makes the global cool cheap in control even though it is expensive in time.

## Where the noise comes from

Six things go wrong, and the model prices each of them.

**Anomalous heating.** An ion warms up even when nothing moves it. Electric-field noise from the electrode surfaces, of a magnitude far above what thermal Johnson noise predicts and hence called anomalous, drives the oscillator at its secular frequency. The heating rate is `ṅ = S_E(ω)·e² / (4·m·ħ·ω)` where `S_E` is the spectral density of the field noise; it scales roughly as the inverse fourth power of the ion's distance from the electrodes and drops by about two orders of magnitude when the trap is cooled to cryogenic temperature. The document carries 0.05 quanta per millisecond, and rule [R17](../rules/#R17) accrues it against elapsed time for every ion, moving or not, the gate's own duration included.

**Heating by transport.** Every primitive deposits its quanta into the ions it moves, as in the table above. The document keeps five heating channels separate: shuttling, junction crossings, split and merge, the gate's own duration, and the anomalous heating below. Rule [R15](../rules/#R15) notes that consecutive transports can interfere and the quanta do not strictly add; the additive sum the model uses is an upper bound.

**Gate error growing with heat.** The MS gate's error at gate time is modelled as linear in the quanta the ions bring to it, `ε(n̄) = ε₀ + k·n̄` with `ε₀ = 1.84e-3` and `k = 2e-3` per quantum; this is rule [R16](../rules/#R16). A second term, after Murali and colleagues, grows the error with the length N of the chain in the gate zone as `N / ln N`, because laser-intensity noise couples more strongly to a longer chain; the document is calibrated at a two-ion chain and the term switches on when a design gates in longer ones.

**Dephasing while idle.** A qubit that waits for its turn loses coherence at the rate `1/T_coh`. The model charges each qubit its share of the round's wall-clock time against the 600 s in the document, which is small for a single round and is why running time, not dephasing, is the number the leaderboard ranks by.

**State preparation and measurement.** Each reset and each measurement contributes its own error once, independent of the heat.

**Ion loss.** Past roughly 85 uncooled junction round trips the ion is gone, not merely warm. The cooling pass can be asked to cap accumulated quanta between cools so that no ion approaches that limit, and it names the single instruction to blame when one move alone would exceed the cap.

## Simulating noise, in general

There are three levels at which a noisy quantum computer can be simulated, and it helps to know which one you are looking at.

The most faithful is to evolve the **density matrix** under a master equation that includes every decoherence channel. It is exact, and it is exponential in the number of qubits, so it serves for a gate or two, not a syndrome round of a code with 288 qubits.

The workhorse for error correction is **stochastic Pauli simulation**. Each operation is followed by a random Pauli error drawn with its physical probability; because the circuit is Clifford, the state is tracked as a stabilizer tableau in polynomial time, the syndromes are handed to a decoder, and the logical error rate is estimated by sampling many rounds. This is how a code's threshold is found, and it is where the figure of about 0.7% for the gross code comes from.

The third level is the **error budget**: add the error of every operation, each evaluated at the conditions it actually runs under, and compare the total per round with the threshold the second level found. It is not a simulation of the quantum state at all, but of the machine, and it answers a different question: not "does this code work" but "does this device, running this schedule, stay under the rate at which the code works". That is the level of this website.

## How this website simulates noise

The compiler turns a circuit into a hardware programme, a list of instructions each of which is one concurrent cycle of the machine. The verifier then **replays** that programme on the device document, instruction by instruction, keeping for every ion its position, its accumulated n̄ split by heating channel, and the clock. A shuttle adds its quanta to the ion shuttled; a junction crossing is charged on entry to the node; a split is charged to the ion that leaves; every ion accrues anomalous heating for the cycle's duration; a cool sets every ion's n̄ to zero. When a two-qubit gate is reached the replay reads each ion's n̄, checks it against the gate's budget of 1.0 quanta for rule R7, evaluates `ε(n̄)` with the chain-length term, and adds it to the total. At the end it reports

`-ln F ≈ Σ_gates ε(n̄, N) + n_qubits · T_round / T_coh + Σ ε_SPAM`

together with the wall-clock time by activity (rotation, docking, gates, cooling), the quanta per data ion by channel, the peak n̄ any ion reached and the number of junction transits each ion made. Those are the numbers on an entry's report and on the leaderboard, and because they come from a replay rather than a formula, claiming them is itself a rule: [R9](../rules/#R9) says the claimed steps, cost, duration and quanta must equal the replayed values.

The replay is deterministic, and that buys two things. The **error budget** can be itemized exactly: since the summed gate error is linear in the quanta carried into gates, scaling one heating channel moves the total along a straight line, and the report gives each channel's share and the exact derivative, how much infidelity halving that channel would buy back, with one extra replay per channel and no finite-difference error. And **cooling insertion** is a pass over the same replay: rule [R7c](../rules/#R7c) makes cooling mandatory under broadcast wiring, so the pass walks the programme, and wherever a gate is about to run on an ion above the budget it inserts one global cool before it. A global cool zeroes every ion, so one pass provably converges. The trade the studio exposes as the **operating point** is real: a slower, colder split that stays under the gate budget can save the 300 µs cool that a faster one would force, and be faster overall.

What the model does not do is as important as what it does. It does not evolve a quantum state or run a decoder; the code's threshold is taken from the literature and the device is judged against it. Transport heating is additive, with no interference term. Crosstalk between beams and stray-field errors are not modelled. The anomalous heating rate is a constant in the document rather than derived from the electrode geometry, although the electrode layer already solves for the ion height that would set it. Each of these is recorded on the [Rules](../rules/) page beside the rule it limits, with what the verifier can honestly report instead.

## Where to go next

The [Language](../language/) page lists the twelve statements a programme is written in and what each one costs; [Compilation](../compilation/) shows the pipeline from a QASM circuit to a verified hardware programme, gate by gate; the reference page on [the architecture document](adl.md) describes how curves, heating, species and the control plane are declared; and every entry on the [Leaderboard](../board/) is a full replay you can step through. The [studio](../studio.html#design) is where the primitives, the heating rate, the species and the wiring live as editable fields, with a hover card on each.
