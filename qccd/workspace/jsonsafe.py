"""Strict JSON in, canonical JSON out.

Everything that crosses a trust boundary into the workspace -- a browser change set, an
agent's MCP call, a file an optimizer wrote, a submission bundle -- is parsed here, and
everything that is hashed is serialized here.  One parser and one serializer, so a
digest computed by the local service, by the evaluator and by the official server is
the same function of the same bytes.

The parser refuses what `json.loads` quietly accepts:

* duplicate object keys (`{"a": 1, "a": 2}` is two documents at once -- the last one
  wins in Python and the first one in some other readers, so a checker and a grader
  could disagree about what was submitted);
* `NaN`, `Infinity`, `-Infinity` (not JSON, and not a number any metric can carry);
* documents deeper, longer or larger than the caller's limits.

The canonical form sorts object keys, keeps list order, uses the shortest separators and
`ensure_ascii=False`, and encodes as UTF-8.  Floats go through `repr`, which is exact
round-trip on every supported Python; integers stay integers.  This is the same
convention `qccd.arch.edit.canonical` already uses for architecture documents, so an
architecture's digest here equals `arch_fingerprint`'s input.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Limits",
    "DEFAULT_LIMITS",
    "JSONRejected",
    "strict_loads",
    "check_value",
    "canonical_bytes",
    "canonical_text",
    "digest",
]


class JSONRejected(ValueError):
    """Input that is not acceptable JSON under the workspace's rules.

    `code` is stable and machine-readable (`duplicate_key`, `non_finite`, `too_deep`,
    `too_large`, `too_many_items`, `bad_type`, `not_json`); `path` is a JSON pointer to
    the offending value when one is known.
    """

    def __init__(self, code: str, message: str, path: str = ""):
        super().__init__(message)
        self.code = code
        self.path = path

    def to_json(self) -> dict:
        return {"code": self.code, "message": str(self), "path": self.path}


@dataclass(frozen=True)
class Limits:
    """Resource limits for one untrusted document."""

    max_bytes: int = 8 * 1024 * 1024
    max_depth: int = 64
    max_items: int = 500_000          # total container members, summed over the tree
    max_string: int = 1_000_000


DEFAULT_LIMITS = Limits()


def _reject_constant(name: str):
    raise JSONRejected("non_finite", f"{name} is not a JSON number")


def _pairs_hook(pairs):
    seen: dict[str, Any] = {}
    for k, v in pairs:
        if k in seen:
            raise JSONRejected("duplicate_key", f"duplicate object key {k!r}")
        seen[k] = v
    return seen


def strict_loads(data: bytes | str, limits: Limits = DEFAULT_LIMITS) -> Any:
    """Parse untrusted JSON.  Raises `JSONRejected`; never returns a partial value."""
    if isinstance(data, bytes):
        if len(data) > limits.max_bytes:
            raise JSONRejected("too_large", f"document is {len(data)} bytes, limit {limits.max_bytes}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise JSONRejected("not_json", f"not UTF-8: {exc}") from None
    else:
        text = data
        if len(text.encode("utf-8")) > limits.max_bytes:
            raise JSONRejected("too_large", f"document exceeds {limits.max_bytes} bytes")
    if text.startswith("﻿"):
        text = text[1:]
    try:
        value = json.loads(text, object_pairs_hook=_pairs_hook,
                           parse_constant=_reject_constant)
    except JSONRejected:
        raise
    except (ValueError, RecursionError) as exc:
        raise JSONRejected("not_json", f"not JSON: {exc}") from None
    check_value(value, limits)
    return value


def check_value(value: Any, limits: Limits = DEFAULT_LIMITS) -> None:
    """Walk an already-parsed value and enforce type, finiteness, depth and size limits.

    Used on values that arrived through another parser (FastAPI's, the MCP SDK's) so the
    same rules hold whichever door a document came in by.
    """
    count = 0
    stack: list[tuple[Any, int, str]] = [(value, 0, "")]
    while stack:
        v, depth, path = stack.pop()
        if depth > limits.max_depth:
            raise JSONRejected("too_deep", f"nesting deeper than {limits.max_depth}", path)
        if v is None or isinstance(v, bool):
            continue
        if isinstance(v, int):
            continue
        if isinstance(v, float):
            if not math.isfinite(v):
                raise JSONRejected("non_finite", "NaN and infinities are not JSON numbers", path)
            continue
        if isinstance(v, str):
            if len(v) > limits.max_string:
                raise JSONRejected("too_large", f"string longer than {limits.max_string}", path)
            continue
        if isinstance(v, dict):
            count += len(v)
            if count > limits.max_items:
                raise JSONRejected("too_many_items", f"more than {limits.max_items} members", path)
            for k, x in v.items():
                if not isinstance(k, str):
                    raise JSONRejected("bad_type", "object keys must be strings", path)
                stack.append((x, depth + 1, f"{path}/{_escape(k)}"))
            continue
        if isinstance(v, (list, tuple)):
            count += len(v)
            if count > limits.max_items:
                raise JSONRejected("too_many_items", f"more than {limits.max_items} members", path)
            for i, x in enumerate(v):
                stack.append((x, depth + 1, f"{path}/{i}"))
            continue
        raise JSONRejected("bad_type", f"{type(v).__name__} is not a JSON value", path)


def _escape(key: str) -> str:
    return key.replace("~", "~0").replace("/", "~1")


#: Integral floats below this magnitude are written as integers.  JSON has one number
#: type; the browser writes `1` for what Python holds as `1.0`, and a digest that told the
#: two apart would call one design two.
_EXACT_INT = 2 ** 53


def normalize_numbers(value: Any) -> Any:
    """Integral finite floats become ints; everything else is returned unchanged."""
    if isinstance(value, float):
        if value.is_integer() and abs(value) < _EXACT_INT:
            return int(value)
        return value
    if isinstance(value, dict):
        return {k: normalize_numbers(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [normalize_numbers(v) for v in value]
    return value


def canonical_text(value: Any) -> str:
    """Sorted keys, list order kept, no whitespace, UTF-8 characters kept, and one
    spelling per number (`normalize_numbers`)."""
    return json.dumps(normalize_numbers(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False)


def canonical_bytes(value: Any) -> bytes:
    return canonical_text(value).encode("utf-8")


def digest(value: Any) -> str:
    """`sha256:` + hex of the canonical form."""
    return "sha256:" + hashlib.sha256(canonical_bytes(value)).hexdigest()
