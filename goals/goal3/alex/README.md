# Alex's builders, as Goal 3 runs them

From `circuits.zip` (sha256 fe93ec72b83fafb462d44cfb753eb51b5113d265a95aa33dbaeebd047b06d53a),
shared by Alex on 2026-10-29. `surface_code.py` and `bb_code.py` are unchanged.
`color_code.py` carries the two fixes in `../CODES.md`, and `color_code.patch` is the exact diff.
`requirements.txt` fixes `igraph=1.0.0` to `igraph==1.0.0`.

Third-party lineage, as the files themselves state:
- `bb_code.py` is adapted from gongaa/SlidingWindowDecoder, MIT, Copyright (c) 2024 Anqi Gong
  ([LICENSE.SlidingWindowDecoder](LICENSE.SlidingWindowDecoder)).
- `color_code.py` is adapted from Classiq's classiq-library, MIT, Copyright (c) 2024 Classiq
  Technologies ([LICENSE.classiq-library](LICENSE.classiq-library)).

Each licence file is the upstream text, fetched on 2026-10-30.

    python -I goals/goal3/make_codes.py --builders goals/goal3/alex
