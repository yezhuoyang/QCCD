"""Provenance -- which Python call produced which hardware instruction.

An instruction says what the machine does; it does not say *why it exists*.  That "why"
is the line of Python you wrote, and it is what a design tool needs in order to close the
loop: click an instruction in the listing, land on the call that emitted it; edit that
call, know which instructions to rebuild.

The link is stored, not recomputed.  Each `Instruction.meta` carries one integer -- an
index into a table of call records held in `TSIR.meta["prov"]` -- so the per-instruction
cost is one small int in JSON, the file path and source text are stored once however many
instructions a call fans out into, and the whole thing round-trips through
`TSIR.to_json`/`from_json` with no special casing because it is already plain JSON.

Nothing here is read by the replay, the rules or the cost models.  Provenance is
annotation in the strictest sense: removing this module changes no number.  That is
asserted, not asserted-by-hope -- `tests/test_listing.py` replays the same program with
and without it and requires every metric to be identical.

Two measured facts shape the implementation, and a well-meaning refactor would undo both:

* `sys._getframe` costs 0.11 us; `traceback.extract_stack(limit=2)` costs 33 us and
  `inspect.stack()` costs 294 us.  The stdlib's readable spellings are 350x and 15000x
  slower, which on a 2000-instruction program is 66 ms and 588 ms against 0.2 ms.
* the site memo keys on `id(code)`, never on the code object.  `hash(code)` walks the
  whole code object and is not cached: inside a 2000-statement function that is 144 us
  per dict lookup, 90x the cost of everything else here put together.  `_CODE_REFS`
  keeps each keyed code object alive so its id cannot be recycled underneath us.
"""

from __future__ import annotations

import linecache
import os
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

__all__ = [
    "PROV_KEY", "CALL_KEY", "LEVELS", "SCHEMA_VERSION", "REPO_ROOT",
    "CallLog", "capture_site", "invalidate", "attach", "note", "tag", "thin",
    "stamp",
    "log_of", "call_of", "site_of", "resolve", "calls_to_instructions",
    "index_by_line", "listing_rows",
]

#: `TSIR.meta` key holding the whole log.
PROV_KEY = "prov"
#: `Instruction.meta` key holding the index of the call that emitted it.
CALL_KEY = "call"
#: Bump when the on-disk shape changes.
SCHEMA_VERSION = 1
#: How much to record.  "off" is free; "sites" is op + source line; "calls" adds the
#: arguments actually passed; "full" adds a source site per participant in a cycle.
#: Record at "calls" and *export* at "sites": `args` is largely redundant with the
#: rendered source text, and it is 55 B per instruction in the page's JSON blob.
LEVELS = ("off", "sites", "calls", "full")

_MAX_ITEMS = 32       # longer sequences/mappings are summarized, not copied
_MAX_REPR = 120
#: the site memo is a process global; a long-lived server exec()ing generated programs
#: would otherwise leak one entry per call site forever
_CACHE_CAP = 50_000

_SHORT_CACHE: dict[tuple[str, str], str] = {}

#: default display root for logs a generator creates: the repo, so a builder's site
#: reads "qccd/compile/programs.py:66" rather than an absolute path that means nothing
#: on anyone else's machine
REPO_ROOT = str(Path(__file__).resolve().parents[2])


# --------------------------------------------------------------------- capture

#: (id(code object), bytecode offset) -> (file, line, text, func).  A call site inside a
#: loop is one key however many times the loop runs, so the linecache lookup and the
#: `f_lineno` computation are paid once per *site*, not once per instruction.
_SITE_CACHE: dict[tuple[int, int], tuple[str, int, str, str]] = {}
#: strong refs to the code objects the cache keys on, so an id is never reused
_CODE_REFS: dict[int, Any] = {}


def capture_site(depth: int = 0) -> tuple[str, int, str, str]:
    """`(file, line, text, func)` of the frame `depth` levels above our caller.

    `depth=0` is the caller of `capture_site` itself.
    """
    fr = sys._getframe(depth + 1)
    code = fr.f_code
    key = (id(code), fr.f_lasti)
    rec = _SITE_CACHE.get(key)
    if rec is None:
        fn = code.co_filename
        ln = fr.f_lineno
        rec = (fn, ln, linecache.getline(fn, ln).strip(), code.co_name)
        if len(_SITE_CACHE) >= _CACHE_CAP:
            invalidate()
        _SITE_CACHE[key] = rec
        _CODE_REFS[id(code)] = code
    return rec


def invalidate() -> None:
    """Drop the site memo and linecache's copy of every file.

    A design tool that edits a program and rebuilds it *in the same process* MUST call
    this: both this memo and `linecache` would otherwise keep serving the pre-edit
    source text, and the listing would show you code you no longer have.
    """
    _SITE_CACHE.clear()
    _CODE_REFS.clear()
    _SHORT_CACHE.clear()
    linecache.checkcache()


def _jsonable(v: Any, depth: int = 0) -> Any:
    if v is None or v is True or v is False or isinstance(v, (int, float, str)):
        return v
    if isinstance(v, Mapping):
        if depth >= 2 or len(v) > _MAX_ITEMS:
            return f"<{len(v)} entries>"
        return {str(k): _jsonable(x, depth + 1) for k, x in v.items()}
    if isinstance(v, (list, tuple, set, frozenset)):
        if depth >= 2 or len(v) > _MAX_ITEMS:
            return f"<{len(v)} items>"
        return [_jsonable(x, depth + 1) for x in v]
    return repr(v)[:_MAX_REPR]


# ------------------------------------------------------------------------ log


class CallLog:
    """The call records for one program under construction.

    `sites` is deduplicated: a call site inside a loop appears once.  `calls` is one
    entry per Python call that emitted at least one instruction, in emission order, and
    an instruction points at it by index.  Nothing here stores instruction ids -- the
    cooling pass renumbers them, so the only durable direction is instruction -> call.
    """

    __slots__ = ("level", "sites", "calls", "_site_ix", "_call_ix", "current", "root")

    def __init__(self, level: str = "calls", root: str | None = None):
        if level not in LEVELS:
            raise ValueError(f"provenance level must be one of {LEVELS}, not {level!r}")
        self.level = level
        self.root = root
        self.sites: list[dict] = []
        self.calls: list[dict] = []
        self._site_ix: dict[tuple[str, int], int] = {}
        self._call_ix: dict[tuple, int] = {}
        self.current: int | None = None

    # -- recording --------------------------------------------------------

    def site(self, rec: tuple[str, int, str, str]) -> int:
        fn, ln, text, func = rec
        key = (fn, ln)
        ix = self._site_ix.get(key)
        if ix is None:
            ix = self._site_ix[key] = len(self.sites)
            self.sites.append(
                {"file": self._short(fn), "line": ln, "text": text, "func": func})
        return ix

    def record(self, op: str, site: tuple[str, int, str, str],
               names: Sequence[str] = (), a: Sequence = (), kw: Mapping = (),
               *, dedupe: bool = False) -> int:
        """Append one call record and return its index.  Does not open a guard.

        `dedupe` folds a record identical to one already logged -- same op, same line,
        same arguments -- into that one.  A generator that emits inside a loop logs the
        same record every pass; the instructions still say which line built them, and
        the log stays the size of the source rather than the size of the program.
        """
        args: dict[str, Any] = {}
        if self.level != "sites":
            for n, v in zip(names, a):
                args[n] = _jsonable(v)
            for k, v in dict(kw).items():
                args[k] = _jsonable(v)
        six = self.site(site)
        if dedupe:
            key = (op, six, repr(args))
            ix = self._call_ix.get(key)
            if ix is not None:
                return ix
            self._call_ix[key] = len(self.calls)
        self.calls.append({"op": op, "site": six, "args": args})
        return len(self.calls) - 1

    def open(self, op: str, site, names=(), a=(), kw=()) -> int:
        self.current = self.record(op, site, names, a, kw)
        return self.current

    def close(self) -> None:
        self.current = None

    @property
    def active(self) -> bool:
        return self.current is not None

    def _short(self, fn: str) -> str:
        """Display path, relative to `root` when it is under it.

        Memoized on (root, file): `os.path.relpath` costs ~40 us on Windows, and a
        program has a handful of distinct source files however many call sites.
        """
        key = (self.root or "", fn)
        out = _SHORT_CACHE.get(key)
        if out is None:
            out = fn
            if self.root:
                try:
                    rel = os.path.relpath(fn, self.root)
                    if not rel.startswith(".."):
                        out = rel.replace(os.sep, "/")
                except ValueError:
                    pass
            _SHORT_CACHE[key] = out
        return out

    # -- output -----------------------------------------------------------

    def to_json(self) -> dict:
        """The log as JSON.  Returns the LIVE `sites`/`calls` lists, not copies, so a
        program's `meta["prov"]` stays current as the program is built -- there is no
        "don't forget to stamp it at the end" failure mode."""
        return {"version": SCHEMA_VERSION, "level": self.level,
                "root": self.root or "", "sites": self.sites, "calls": self.calls}

    @classmethod
    def adopt(cls, doc: Mapping) -> "CallLog":
        """Wrap a log that already exists as JSON, so a pass can append to it."""
        log = cls(str(doc.get("level", "calls")), str(doc.get("root", "")) or None)
        log.sites = list(doc.get("sites", ()))
        log.calls = list(doc.get("calls", ()))
        log._site_ix = {(s["file"], s["line"]): i for i, s in enumerate(log.sites)}
        log._call_ix = {(c["op"], c["site"], repr(c.get("args", {}))): i
                        for i, c in enumerate(log.calls)}
        return log


# ------------------------------------------------------- the generator surface


def attach(prog, level: str = "calls", root: str | None = None) -> CallLog:
    """The log of `prog`, created and hung off `prog.meta` if it has none.

    The `CallLog` is cached on the program object under a private attribute and holds
    the very lists that `prog.meta["prov"]` exposes, so appending to it updates the
    program in place.  Adopting also breaks the aliasing a pass like `insert_cooling`
    creates when it does `dict(prog.meta)` -- otherwise appending to the derived
    program's log would mutate the original's too.
    """
    log = getattr(prog, "_prov_log", None)
    if log is None:
        doc = prog.meta.get(PROV_KEY)
        log = (CallLog.adopt(doc) if isinstance(doc, Mapping)
               else CallLog(level, REPO_ROOT if root is None else root))
        prog.meta[PROV_KEY] = log.to_json()
        try:
            prog._prov_log = log
        except AttributeError:            # a slotted program-like object
            pass
    return log


def note(prog, op: str, *, depth: int = 0, dedupe: bool = True, **args) -> int:
    """Record that `op(**args)` -- running at the line that called this -- is about to
    emit instructions, and return the call index to stamp into their `meta`.

    This is the generator's counterpart to what `Program` does automatically: a program
    built by `qccd.compile.programs` or the pipeline reports the builder and its
    parameters, and the site is the line of the builder that constructs the instruction,
    so clicking an instruction still lands on the code that produced it.
    """
    return attach(prog).record(op, capture_site(depth + 1), (), (), args,
                               dedupe=dedupe)


def stamp(prog, op: str, *, depth: int = 0, only_untagged: bool = True,
          **args) -> int:
    """Attribute every (still untagged) instruction of `prog` to one generator call.

    A builder in `qccd.compile.programs` has no user frame to point at -- the caller
    said `build(arch, "deck")`, and what emitted the 1,579 instructions was the builder.
    So the record names the builder and its parameters, and the site is the line of the
    builder that ran, which is what a click on a listing row should land on.

    `only_untagged` leaves any instruction that already knows where it came from alone,
    so a builder that delegates (odd_even -> odd_even_sort_program) can stamp what its
    callee did not.
    """
    from dataclasses import replace as _replace

    c = note(prog, op, depth=depth + 1, **args)
    for i, instr in enumerate(prog.instructions):
        meta = instr.meta or {}
        if only_untagged and CALL_KEY in meta:
            continue
        prog.instructions[i] = _replace(instr, meta=dict(meta, **{CALL_KEY: c}))
    return c


def tag(meta: Mapping | None = None, call: int | None = None, **extra) -> dict:
    """`meta` with the call index stamped in.  `tag()` with no call is just a dict."""
    d = dict(meta or {})
    if extra:
        d.update(extra)
    if call is not None:
        d[CALL_KEY] = call
    return d


def thin(log: Mapping | None, level: str = "sites") -> dict | None:
    """A log downgraded for export.

    Two things must not reach a published page: `root`, which is an absolute path on
    the machine that built it, and -- at level "sites" -- `args`, which is 55 bytes per
    instruction of information the rendered source line already shows more readably.
    """
    if not log:
        return None
    if level == "off":
        return None
    sites = [dict(s) for s in log.get("sites", ())]
    calls = []
    for c in log.get("calls", ()):
        d = {"op": c.get("op", ""), "site": c.get("site")}
        if level != "sites" and c.get("args"):
            d["args"] = c["args"]
        calls.append(d)
    return {"version": log.get("version", SCHEMA_VERSION), "level": level,
            "sites": sites, "calls": calls}


# ------------------------------------------------------------------- reading


def log_of(prog) -> dict | None:
    """The provenance log of a program, or None if it was built without one."""
    d = (prog.meta or {}).get(PROV_KEY)
    return d if isinstance(d, Mapping) else None


def call_of(prog, instr) -> dict | None:
    """The call record that emitted `instr`, or None."""
    log = log_of(prog)
    if log is None:
        return None
    ix = (instr.meta or {}).get(CALL_KEY)
    if not isinstance(ix, int) or isinstance(ix, bool):
        return None
    calls = log.get("calls") or []
    return calls[ix] if 0 <= ix < len(calls) else None


def site_of(prog, instr) -> dict | None:
    call = call_of(prog, instr)
    if call is None:
        return None
    sites = (log_of(prog) or {}).get("sites") or []
    ix = call.get("site")
    return sites[ix] if isinstance(ix, int) and 0 <= ix < len(sites) else None


def resolve(prog, instr) -> dict | None:
    """One flat record: `{call, op, args, file, line, text, func}`.

    This is what a listing row and a viz frame want -- no joins on the far side.
    """
    call = call_of(prog, instr)
    if call is None:
        return None
    site = site_of(prog, instr) or {}
    return {"call": instr.meta[CALL_KEY], "op": call.get("op", ""),
            "args": call.get("args", {}), **site}


def calls_to_instructions(prog) -> dict[int, list[int]]:
    """call index -> the instruction ids it produced, in program order.

    The forward index, derived rather than stored, so it stays correct after the
    cooling pass renumbers instructions.
    """
    out: dict[int, list[int]] = {}
    for instr in prog.instructions:
        ix = (instr.meta or {}).get(CALL_KEY)
        if isinstance(ix, int) and not isinstance(ix, bool):
            out.setdefault(ix, []).append(instr.id)
    return out


def index_by_line(prog) -> dict[tuple[str, int], list[int]]:
    """`(file, line)` -> instruction ids.  The reverse index a future editor needs.

    `(file, line)` and `instr.id` are now equally durable, and that is the point of the
    index: `TSIR.next_id` allocates an identity that no pass may reassign, so the ids
    this returns still name the same instructions after `insert_cooling`.  They did not
    used to -- measured on the deck program, 2 of the 1,578 ids reported for
    `import_deck.py:162` survived one pass.  The call index remains stable only within
    one build (`prov.stamp` appends and re-stamps), so it is still the wrong key.
    """
    log = log_of(prog)
    if log is None:
        return {}
    sites, calls = log.get("sites") or [], log.get("calls") or []
    out: dict[tuple[str, int], list[int]] = {}
    for instr in prog.instructions:
        ix = (instr.meta or {}).get(CALL_KEY)
        if not isinstance(ix, int) or isinstance(ix, bool) or not 0 <= ix < len(calls):
            continue
        s = calls[ix].get("site")
        if not isinstance(s, int) or not 0 <= s < len(sites):
            continue
        out.setdefault((sites[s]["file"], sites[s]["line"]), []).append(instr.id)
    return out


def listing_rows(prog) -> list[dict]:
    """One row per instruction: its id, and the call/site that produced it."""
    return [{"id": i.id, "type": i.type, "src": resolve(prog, i)}
            for i in prog.instructions]
