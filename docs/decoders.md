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

Tested: commit `5100dc0520672ed2c5b3801f02e62eeb88bd3613` (2026), Python 3.14, numpy 2.4, ldpc 2.4.1,
Cython 3.3.1, on Windows 11 with MSVC 14.38 (Visual Studio 2022). The library's `setup.py` also
has the Linux and macOS flags (`-std=c++2a -O3`); those builds were not run here.

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
