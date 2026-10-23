"""Paths in a committed record are relative to the repository, never to a disk.

A board's `manifest.json` and `rows.json` name each entry's artifacts -- `arch`, `prefix`,
`qasm`, `cooled`.  They used to be written as absolute paths, so the files committed to this
public repository spelled out the user folder of the machine that built them, and a second
checkout on that machine silently read the first checkout's artifacts against its own
numbers.  Writers store `to_repo(record)`; readers go through `from_repo(value)` (one path) or
`resolve(record)` (a row or a list of rows), which still accept an absolute path from a
record written before this.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable

__all__ = ["REPO", "PATH_KEYS", "to_repo", "from_repo", "resolve"]

#: the root of this checkout
REPO = Path(__file__).resolve().parents[1]

#: the keys of a board row that name a file or a file prefix
PATH_KEYS = ("arch", "prefix", "qasm", "cooled")

_ABS = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\\\)")


def _relative(text: str, roots: Iterable[Path]) -> str:
    """`text` relative to the first root it lies under, with forward slashes; a path under
    none of them keeps only its file name, as `qccd.site.build.public_paths` publishes it."""
    for root in roots:
        try:
            rel = os.path.relpath(text, root)
        except ValueError:                      # another drive
            continue
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    return re.split(r"[\\/]+", text.rstrip("\\/"))[-1]


def to_repo(obj: Any, *, roots: Iterable[str | Path] = (), keys: Iterable[str] = PATH_KEYS) -> Any:
    """A record with its paths made relative to the repository: every `Path` object, and
    every absolute string under one of `keys` (a string elsewhere may be a URL such as
    `/board/goal1/`, and is left alone).

    `roots` adds checkouts the paths may have been written in besides this one (the shared
    tree and its worktrees name the same files from different folders)."""
    rs = [REPO, *(Path(r) for r in roots)]
    keys = tuple(keys)

    def conv(o: Any, key: str | None) -> Any:
        if isinstance(o, dict):
            return {k: conv(v, k) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [conv(v, key) for v in o]
        if isinstance(o, Path):
            return _relative(str(o), rs) if o.is_absolute() else str(o).replace("\\", "/")
        if isinstance(o, str) and key in keys and _ABS.match(o):
            return _relative(o, rs)
        return o

    return conv(obj, None)


def from_repo(value: str | Path) -> Path:
    """The file a record names: a relative path is under this checkout, an absolute one
    (from an older record) is taken as it is."""
    p = Path(value)
    return p if p.is_absolute() else REPO / p


def resolve(obj: Any, keys: Iterable[str] = PATH_KEYS) -> Any:
    """A row (or a list of rows) with its path keys turned back into absolute paths, as
    strings, so code written against the old absolute records reads the new ones unchanged."""
    keys = tuple(keys)
    if isinstance(obj, list):
        return [resolve(v, keys) for v in obj]
    if isinstance(obj, dict):
        return {k: (str(from_repo(v)) if k in keys and isinstance(v, (str, Path)) and v else
                    resolve(v, keys) if isinstance(v, (dict, list)) else v)
                for k, v in obj.items()}
    return obj
