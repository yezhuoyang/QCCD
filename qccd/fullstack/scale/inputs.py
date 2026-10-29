"""FormalRV's `.inputs` files: the parameter values a paper's program runs at.

    bits=2048                 an integer
    tSynth=[0,0,0,1,92,...]   a table, given
    T=bound[518774784,0,N]    a table given only by its size and value range (data the program
                              reads; QCCD never needs its contents)

`read_inputs` returns `{name: int | list | ("bound", size, lo, hi) | str}`.
"""
from __future__ import annotations

import re
from pathlib import Path

__all__ = ["read_inputs"]


def read_inputs(path) -> dict:
    out: dict = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = (x.strip() for x in line.split("=", 1))
        if re.fullmatch(r"\d+", v):
            out[k] = int(v)
        elif v.startswith("[") and v.endswith("]"):
            out[k] = [int(x) for x in v[1:-1].split(",") if x.strip()]
        elif v.startswith("bound[") and v.endswith("]"):
            n, lo, hi = (int(x) for x in v[len("bound["):-1].split(","))
            out[k] = ("bound", n, lo, hi)
        else:
            out[k] = v
    return out
