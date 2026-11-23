# Decoders

A logical error rate is a property of a circuit **and of the decoder that read it**. Every
estimate (`qccd.qec.decode.estimate_ler`, and so every `qccd.ler_report`) records which decoder
ran and with what settings, in `ler.decoder_config`; `qccd.qec.describe_decoder` writes the same
description for an older stored row from the decoder's name and its budget.

## The three decoders

| `decoder` | what it is | chosen by `auto` | settings in the budget |
|---|---|---|---|
| `pymatching` | minimum-weight perfect matching (PyMatching 2) on stim's detector error model with errors decomposed into graph-like pieces | when every error is graph-like and the experiment is matchable (surface, repetition codes) | none |
| `bposd` | BP-OSD (ldpc's `BpOsdDecoder`): product-sum belief propagation, parallel schedule, then combination-sweep OSD (`osd_cs`) of order 7, on the model with hyperedges kept | otherwise (BB, colour, HGP, flagged circuits) | `bp_max_iter` (BP iterations; without it, the detector count, at least 100) |
| `beam` | BP-guided beam search for quantum LDPC codes: IonQ's own code (Ye, Wecker, Delfosse, [arXiv:2512.07057](https://arxiv.org/abs/2512.07057); PRX Quantum 7, 033002), min-sum BP, on the same model as BP-OSD | **never**: a budget asks for it by name | `beam_width` (default 8), and `beam_max_rounds`, `beam_initial_iters`, `beam_iters_per_round`, `beam_num_results` |

The beam decoder's settings default to the paper's for the width (its Table 1); another width
takes the row of the largest tabulated width not above it.

| `beam_width` | `beam_initial_iters` | `beam_iters_per_round` | `beam_max_rounds` | `beam_num_results` | the paper's name |
|---|---|---|---|---|---|
| 8 | 30 | 20 | 10 | 1 | `beam8_230iters` |
| 32 | 40 | 30 | 10 | 1 | `beam32_340iters` |
| 64 | 40 | 30 | 20 | 1 | `beam64_640iters` |

The paper's most accurate variant, `beam64_32res_640iters`, is `beam_width: 64` with
`beam_num_results: 32`: the search goes on until 32 solutions are found and returns the lightest.

A release selects a decoder in its sampling budget (`qec.ler` in the manifest), for example

```json
{"decoder": "bposd", "bp_max_iter": 30, "max_shots": 2000000, "max_errors": 200, "batch": 10000, "seed": 20261029}
{"decoder": "beam", "beam_width": 32, "max_shots": 2000000, "max_errors": 200, "batch": 10000, "seed": 20261029}
```

`bp_max_iter` is BP-OSD's alone and the `beam_*` keys are the beam decoder's alone: a `beam_*` key
with another decoder is an error.

## What an estimate records

```json
"decoder": "bposd",
"decoder_config": {
  "decoder": "bposd", "library": "ldpc", "version": "2.4.1",
  "bp_method": "product_sum", "schedule": "parallel", "max_iter": 30, "max_iter_rule": "bp_max_iter",
  "osd_method": "osd_cs", "osd_order": 7,
  "error_model": "stim detector error model, hyperedges kept",
  "label": "BP-OSD (ldpc 2.4.1: product-sum BP, 30 iterations; OSD-CS order 7)"
}
```

`max_iter` is the number the library ran with, read back from it. For `pymatching` the record is
the library and its version; for `beam` it is the source commit of the checkout, the SHA-256 of
its algorithm file (`decoder/src_cpp/beam_search.hpp`, line endings normalised), the width and
the four other settings, for example

```json
"decoder_config": {
  "decoder": "beam", "library": "BeamSearchDecoder",
  "source": "https://github.com/ionq-publications/BeamSearchDecoder",
  "commit": "5100dc0520672ed2c5b3801f02e62eeb88bd3613",
  "source_sha256": "0412598c5baa4fca98139c085256be1eec405728a0b219ae2863228b95242a87",
  "paper": "arXiv:2512.07057", "license": "CC BY-NC-SA 4.0",
  "beam_width": 8, "max_rounds": 10, "initial_iters": 30, "iters_per_round": 20, "num_results": 1,
  "bp_method": "minimum_sum", "schedule": "parallel",
  "error_model": "stim detector error model, hyperedges kept",
  "label": "Beam search (IonQ BeamSearchDecoder 5100dc0: width 8; min-sum BP, 30 iterations then up to 10 rounds of 20)"
}
```

Two more fields sit beside it: `decode_seconds` (of `seconds`, the part spent in the decoder) and
`unsolved`, the number of shots whose returned correction does not reproduce the syndrome. BP-OSD
and matching always return a correction that does. A beam search that finds no solution within
its rounds returns the hard decision of its first BP pass, which need not; such a shot is counted
in `unsolved` and scored by what was returned, as the paper's own simulations score it.

A row stored before 2026-11-23 has no `decoder_config`. It kept the decoder's name and its
budget, which is enough:

```python
from qccd.qec import describe_decoder
describe_decoder("bposd", {"bp_max_iter": 30}, versions={"ldpc": "2.4.1"})["label"]
# 'BP-OSD (ldpc 2.4.1: product-sum BP, 30 iterations; OSD-CS order 7)'
```

(`versions` is the report's `tools`; `n_detectors`, the third argument, is needed only for a
BP-OSD row whose budget has no `bp_max_iter`.)

## Installing the beam decoder

The beam decoder is IonQ's `BeamSearchDecoder`, a C++ library with a Cython wrapper. It is **not
shipped with this project and not a dependency**: its licence is Creative Commons
Attribution-NonCommercial-ShareAlike 4.0 (CC BY-NC-SA 4.0), this project's is MIT, so the source is
not copied here, and the licence's non-commercial condition is the user's to meet. Without it
everything else works, `auto` never picks it, and `decoder: "beam"` raises
`qccd.qec.decode.DecoderNotInstalled` with these instructions.

Tested: commit `5100dc0520672ed2c5b3801f02e62eeb88bd3613` (2026-11-05), Python 3.14, numpy 2.4, ldpc 2.4.1,
Cython 3.3.1, on Windows 11 with MSVC 14.38 (Visual Studio 2022); the steps below were run as
written from a fresh clone and a fresh virtual environment. The library's `setup.py` also has the
Linux and macOS flags (`-std=c++2a -O3`); those builds were not run here.

```sh
git clone https://github.com/ionq-publications/BeamSearchDecoder
cd BeamSearchDecoder
git checkout 5100dc0520672ed2c5b3801f02e62eeb88bd3613
pip install "Cython>=3.1"            # with the numpy, scipy and ldpc this project already needs
cd decoder
python setup.py build_ext --inplace  # needs a C++20 compiler: MSVC (Build Tools for Visual Studio), gcc or clang
```

Then tell this project where the checkout is, with the environment variable `QCCD_BEAM_DECODER`
(the directory that holds `decoder/`), or put its `decoder` directory on `PYTHONPATH`:

```sh
export QCCD_BEAM_DECODER=/path/to/BeamSearchDecoder        # PowerShell: $env:QCCD_BEAM_DECODER = "C:\path\to\BeamSearchDecoder"
python -m pytest tests/test_qec_decoders.py                # the last test runs the real library when it is found
```

The build must be made with the Python that runs `qccd` (the extension is tied to the Python
version). The commit recorded in `decoder_config` is read from the checkout's `.git`; a copy
without `.git` records the algorithm file's SHA-256 alone.

## Measured on this project's circuits (2026-11-23)

Is the beam decoder a faster, or a better, decoder for the boards BP-OSD decodes today? It was
run on Goal 3 points, against their published rows.

**How.** A point's circuit is extracted as the sweep extracts it (the point's compiled program
under qccd-noise@3) and the shots are the published run's own: the seeded detector sampler (seed
20261029) drawn in the release's batches. Every decoder reads the same shots. The BP-OSD row is
the control, and it reproduces the published row exactly at the three points that finished
(242 / 30,000, 202 / 44,500 and 205 / 90,000 logical errors). The decoders take turns on chunks
of 50 shots and each shot is timed around the decoder call, on one core of a desktop whose other
cores were busy with other jobs: BP-OSD took 940 s here for the 30,000 shots the published row
decoded in 304 s. The times compare the decoders with each other; they are not the machine's
best. "No solution" counts the shots whose correction misses the syndrome. A decoder that reached
its time limit reports the shots it did.

(The shots have to come from the circuit in memory. stim's circuit text rounds probabilities, so
a circuit read back from a `.stim` file samples another stream: 241 logical errors where the
circuit in memory gives the published 242.)

**BB [[72,12,6]], the best C2LR point, `c2lr15x14`** (504 detectors, 4,464 error mechanisms, 33
detection events a shot; published 1.35e-3 per round)

| decoder | logical errors / shots | LER per round (95 % interval) | no solution | decode, s | mean, ms a shot | 99.9th percentile, ms |
|---|---|---|---|---|---|---|
| BP-OSD, as the boards run it | 242 / 30,000 | 1.35e-3 [1.19e-3, 1.53e-3] | 0 | 940 | 31.3 | 748 |
| beam, width 8 | 206 / 30,000 | 1.15e-3 [1.00e-3, 1.32e-3] | 0 | 245 | 8.2 | 153 |
| beam, width 32 | 207 / 30,000 | 1.15e-3 [1.01e-3, 1.32e-3] | 0 | 482 | 16.1 | 189 |
| beam, width 64 | 143 / 19,450 (time limit; BP-OSD 155 on these) | 1.23e-3 [1.04e-3, 1.45e-3] | 0 | 703 | 36.1 | 358 |
| beam, width 64, best of 32 solutions | 8 / 1,275 (time limit; BP-OSD 7 on these) | 1.05e-3 [5.3e-4, 2.07e-3] | 0 | 1,519 | 1,191 | 10,174 |
| BP-OSD, the paper's baseline (min-sum BP, 30 iterations; OSD-CS order 10) | 248 / 30,000 | 1.38e-3 [1.22e-3, 1.57e-3] | 0 | 655 | 21.8 | 676 |

**BB [[72,12,6]], the best Conveyor point, `conveyor96_96d_j`** (the same sizes, 28 detection
events a shot; published 7.6e-4 per round)

| decoder | logical errors / shots | LER per round (95 % interval) | no solution | decode, s | mean, ms a shot | 99.9th percentile, ms |
|---|---|---|---|---|---|---|
| BP-OSD, as the boards run it | 202 / 44,500 | 7.58e-4 [6.60e-4, 8.70e-4] | 0 | 1,118 | 25.1 | 717 |
| beam, width 8 | 187 / 44,500 | 7.02e-4 [6.08e-4, 8.10e-4] | 0 | 325 | 7.3 | 133 |
| beam, width 32 | 181 / 44,500 | 6.79e-4 [5.87e-4, 7.85e-4] | 0 | 687 | 15.4 | 192 |
| beam, width 64 | 73 / 18,150 (time limit; BP-OSD 80 on these) | 6.71e-4 [5.34e-4, 8.44e-4] | 0 | 601 | 33.1 | 258 |

**BB [[72,12,6]] past threshold, the Cyclone ring `cyclone6_n24`** (504 detectors, 18,242 error
mechanisms, 131 detection events a shot; published 0.52 per round, 494 / 500, decoded in 999 s).
Each decoder had ten minutes (width 32 eight, width 64 four) on the first shots of the published
run.

| decoder | logical errors / shots | LER per round (95 % interval) | no solution | decode, s | mean, s a shot | slowest shot, s |
|---|---|---|---|---|---|---|
| BP-OSD, as the boards run it | 145 / 146 | 0.56 [0.42, 0.67] | 0 | 602 | 4.1 | 14.9 |
| beam, width 8 | 46 / 47 (BP-OSD 47 on these, at 5.4 s a shot) | 0.47 [0.31, 0.61] | 46 | 608 | 12.9 | 29.5 |
| beam, width 32 | 11 / 11 | 1 [0.20, 1] | 11 | 499 | 45 | 82 |
| beam, width 64 | 1 / 1 | 1 [0.04, 1] | 1 | 368 | 368 | 368 |

**Colour code d = 5, its best point, `c2lr7x7`** (release `goal3_color5_mem@1`; 108 detectors, 698
error mechanisms, 2 detection events a shot; published 4.56e-4 per round)

| decoder | logical errors / shots | LER per round (95 % interval) | no solution | decode, s | mean, ms a shot | 99.9th percentile, ms |
|---|---|---|---|---|---|---|
| BP-OSD, as the boards run it | 205 / 90,000 | 4.56e-4 [3.98e-4, 5.23e-4] | 0 | 33 | 0.37 | 35 |
| beam, width 8 | 378 / 90,000 | 8.41e-4 [7.61e-4, 9.31e-4] | 0 | 17 | 0.19 | 15 |
| beam, width 32 | 569 / 90,000 | 1.27e-3 [1.17e-3, 1.38e-3] | 0 | 25 | 0.28 | 25 |
| beam, width 64 | 569 / 90,000 | 1.27e-3 [1.17e-3, 1.38e-3] | 0 | 38 | 0.42 | 33 |
| beam, width 64, best of 32 solutions | 144 / 32,700 (time limit; BP-OSD 78 on these) | 8.82e-4 [7.50e-4, 1.04e-3] | 0 | 601 | 18.4 | 207 |
| BP-OSD, the paper's baseline (min-sum BP, 30 iterations; OSD-CS order 10) | 264 / 90,000 | 5.87e-4 [5.21e-4, 6.63e-4] | 0 | 17 | 0.19 | 14 |

**What it says.**

1. *On the BB code, where the design works, width 8 is at least as accurate as BP-OSD and three to
   four times faster.* Over the two points' 74,500 shots it makes 393 logical errors where BP-OSD
   makes 444. On the same shots the difference is significant at the C2LR point (107 shots only
   BP-OSD gets wrong, 71 only the beam decoder; exact two-sided p = 0.009) and not at the Conveyor
   point (94 against 79, p = 0.29). Its mean decode time is 3.8 and 3.4 times lower, and its
   99.9th-percentile time 4.9 and 5.4 times lower.
2. *A wider beam buys nothing on these circuits.* Width 32 gives width 8's logical errors (207
   against 206, 181 against 187) at twice the time; width 64 gives width 32's on the shots it
   reached, at 4.4 times width 8's time, slower than BP-OSD on average. The variant that keeps the
   lightest of 32 solutions made 8 errors in 1,275 shots where BP-OSD and width 8 made 7, at 1.2 s a
   shot.
3. *Past threshold it is slower than BP-OSD, not faster.* On the Cyclone ring nearly every search
   ends without a solution, which is the beam decoder's worst case: width 8 took 12.9 s a shot
   where BP-OSD took 5.4 s on the same shots, width 32 took 45 s, width 64 six minutes for one.
   It is no way out for the rows BP-OSD took hours on.
4. *On the colour code it is worse than BP-OSD*, by 1.8 times at width 8 and 2.8 times at widths 32
   and 64. The cause is the belief propagation that guides the search, not the beam: width 8 with
   width 32's iteration counts gives width 32's 569 errors, and width 32 with width 8's gives 378.
   Asking for more solutions makes it worse (best of 4: 445 in 90,000; best of 16: 427 in 44,350
   where width 8 has 172), because the search does not reach the light corrections at all: for a
   syndrome one fault explains (BP-OSD's correction has weight 5.8) it returns four-fault
   corrections of weight 24 to 25, and the lightest of those is the wrong class.
5. *Decoding the Z half and the X half by themselves changes nothing.* These memories are two
   disconnected decoding problems in one matrix (252 detectors and 12 observables each); decoded
   as two, every decoder misses the same number of shots as decoded as one (first 4,000 shots of
   `c2lr15x14`: 29, 28, 36 and 36 either way).

**Against the paper's claims.** The paper's headline numbers are for the [[144,12,12]] code at
physical error rates of 1e-3 and 5e-4, against BP-OSD with 30 min-sum iterations and OSD-CS of
order 10. Its own Z-memory circuits, run here through the same build, behave as it says: at
p = 1e-3 (3,000 shots, no logical error for any decoder) the 99.9th-percentile time is 821 ms for
that BP-OSD and 34 ms for width 8, 24 times lower (the paper: 26); at p = 4e-3 (1,000 shots) the
failures are 27 for BP-OSD and 21, 10 and 6 for widths 8, 32 and 64. On this project's circuits:

| the paper, on [[144,12,12]] | here, on the BB [[72,12,6]] points |
|---|---|
| width 8 matches BP-OSD's logical error rate | yes: 0.85 and 0.93 of BP-OSD's errors |
| ... with a 26 times lower 99.9th-percentile runtime | 4.9 and 5.4 times lower (mean: 3.8 and 3.4 times) |
| width 32: 5.6 times lower logical error rate | no: 0.86 and 0.90 of BP-OSD's errors, the same as width 8 |
| width 64 (best of 32 solutions): 17 times lower | no: 8 errors where BP-OSD makes 7 (1,275 shots), 55 times slower |

The large accuracy gains are not there on these points, a distance-6 code at ten to twelve
expected faults a shot: more than half of the shots one decoder gets wrong the other gets wrong
too (135 of BP-OSD's 242 and of width 8's 206 at the C2LR point), and BP-OSD as the boards run it
(product-sum BP) already matches or beats the paper's baseline (242 against 248 here, 205 against
264 on the colour code).

**Recommendation.** Width 8 (`{"decoder": "beam", "beam_width": 8}`) as the second, labelled
decoder on the BB boards: the same answer within the interval, three to four times sooner, from an
independent algorithm. Not widths 32 or 64, which cost more and add nothing here. Not on the
colour-code boards, where it reads the same circuit about twice as badly, and not as a rescue for
designs past threshold. BP-OSD stays the decoder the published numbers are ranked by; the beam
decoder's licence (non-commercial) and its build step are reasons to keep it optional.
