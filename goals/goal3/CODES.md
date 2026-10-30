# Goal 3's fixed codes: what we checked in Alex's circuits

Source: `circuits.zip` from Alex (2026-10-29): `surface_code.py` (N-Z schedule), `bb_code.py`
(Bravyi et al.'s depth-8 schedule, after gongaa/SlidingWindowDecoder), `color_code.py` (the
off-the-hook schedule of arXiv:2603.28852, after Classiq's library), `circuits.py`, and
`requirements.txt`. Every circuit below is the builder's own output, with the two fixes listed
under the colour code applied.

## What we checked, for each code and both bases (Z memory, X memory)

1. **Determinism.** stim builds a detector error model with every detector deterministic.
2. **CNOT layers.** No qubit is used twice between two `TICK`s.
3. **Circuit distance.** stim searches for the smallest undetectable logical error under
   uniform circuit noise. This gives an upper bound, and for the surface code it equals d.
4. **Import.** The circuit is read into this platform's form (`qccd.qec.stim_import`: OpenQASM 2
   plus detectors on its classical bits, with the data readout appended by the grader). We
   proved the import changes nothing: under the same two-qubit noise, the source and the import
   have the same fault mechanisms with the same probabilities.

| key | code | rounds | ions | 2q gates | circuit distance | checks |
|---|---|---|---|---|---|---|
| surface3 | rotated surface [[9,1,3]] | 3 | 17 | 72 | 3 | all pass |
| surface5 | rotated surface [[25,1,5]] | 5 | 49 | 400 | 5 | all pass |
| surface7 | rotated surface [[49,1,7]] | 7 | 97 | 1176 | 7 | all pass |
| color3 | 6.6.6 colour [[7,1,3]] | 3 | 10 | 72 | 2 | pass after fixes 1-2 |
| color5 | 6.6.6 colour [[19,1,5]] | 5 | 28 | 420 | 4 | pass after fixes 1-2 |
| color7 | 6.6.6 colour [[37,1,7]] | 7 | 55 | 1260 | 6 | pass after fixes 1-2 |
| bb72 | bivariate bicycle [[72,12,6]] | 6 | 144 | 2592 | 6 | all pass |
| bb108 | bivariate bicycle [[108,8,10]] | 10 | 216 | 6480 | not searched | all pass |
| bb144 | bivariate bicycle [[144,12,12]] | 12 | 288 | 10368 | not searched | all pass |

The colour code's distances, 2, 4 and 6, are what arXiv:2603.28852 reports for a single
auxiliary per plaquette: d − ⌊(d+3)/6⌋, the loss coming from "fractional hook errors" at the
boundary. The paper suggests flags on boundary plaquettes as the remedy.

## Problems found

**Colour code, fix 1 (a real bug): Z-memory detectors after round 1 named only the previous
round.** In `_build_parallel_circuit`, the Z-basis branch builds
`[rec(-x-z+j-num_anc)]`, the previous round's bit, without this round's. The detectors were still
deterministic, so nothing complained, but the circuit distance was **1** at every d. The fix adds
the current bit: `[rec(-x-z+j), rec(-x-z+j-num_anc)]`. Distance is then 2, 4, 6, as the paper
predicts.

**Colour code, fix 2 (the paper's construction): one auxiliary per plaquette, not two.** The file
creates an X ancilla and a Z ancilla per plaquette (37 qubits at d=5), although its comments say
"X ancillas are reused from Z ancillas", the round's docstring says "steps 1-6 (Z), M + RX on
ancillas, steps 7-12 (X), MX", and the paper's title is *a single auxiliary per plaquette*. We
reuse the Z ancilla: measure it after the Z half, re-prepare it in |+>, then run the X half. The
record order is unchanged, so every detector index stays valid. That gives 10, 28 and 55 ions
instead of 13, 37 and 73, with the same circuit distance. On trapped-ion hardware the ion count
sets how large every architecture must be, so this matters for Goal 3.
`build_circuit(..., shared=False)` gives back the two-ancilla circuit.

**Minor issues:**

- `requirements.txt` has `igraph=1.0.0` (one `=`), which pip rejects.
- `circuits.py` prints seven circuits at import time. Put the prints under
  `if __name__ == "__main__":`.
- `color_code.ColorCode.num_checks_x` is computed from `anc_Z`. The value is right, but only by
  symmetry.

**Not a problem:** the colour code's d−1 circuit distance is the published behaviour, not a bug
in the port. We confirmed this by trying every rotation and reflection of the hexagon's position
numbering and every relabelling of the three colours: none of the 72 gives distance d with
sequential Z-then-X halves.

## Still to come

- **HGP:** Alex is still looking for an explicit construction. It also needs a CNOT order,
  which is Keyi's part of the plan.
- **Distances past 7 for surface and colour:** the 10/26 plan sets the target at LER < 10^-5,
  and under qccd-noise@2 a d=7 surface patch is still above it (Goal 1: 3×10^-5 per round with
  both bases). `make_codes.py` takes any odd d.
