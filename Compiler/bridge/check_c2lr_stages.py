"""Re-check the C2LR router's saved native stages (`qccd.compile.c2lr_stages`): the C2LR twin
of `qccdc check-stages`, run by the staged driver for a manifest whose engine is `c2lr-router`."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.compile.c2lr_stages import main

if __name__ == "__main__":
    raise SystemExit(main())
