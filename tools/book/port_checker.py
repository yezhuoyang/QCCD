#!/usr/bin/env python3
"""Derive the browser copy of the QCCD proved checker from the repository's sources.

usage: python port_checker.py <repo>/Compiler/lean <out>

The browser engine (cauli/lean4 fork, Lean 4.33.0-pre, wasm32) compiles every file as a
`module`, so un-annotated declarations become private to their file.  And its IR interpreter
runs out of JavaScript stack at about 550 nested calls.  This script makes three mechanical
edits and nothing else:

  1. module syntax, in every checker file: `module` after the leading comment, every
     `import X` becomes `public import X`, then `@[expose] public section` (or plain
     `public section` when the file has `private` helpers, which an exposed body may not
     reference) after the imports, and `end` at the end of the file;
  2. Check.lean: tail-recursive `mergeTR` / `snapshotsTR` with `@[csimp]` equality lemmas,
     each inserted directly after the definition it replaces (a csimp lemma only rewrites
     code compiled after it, so `events` and `replay` must come later);
  3. Sound.lean: the `#print axioms` lines move to QCCDC/Cert/Axioms.lean, a non-module file
     (Lean 4.29 refuses `#print axioms` inside a module).

The specification (`Implements`), `check`, and every proof are unchanged.  The script is
idempotent (running it on its own output gives the same output) and fails loudly when an
anchor it relies on is missing, because the checker keeps changing upstream.  It writes
<out>/port.diff (unified, against the repository sources) and <out>/port-sources.json
(sha256 of every source it read).
"""
from __future__ import annotations

import difflib
import hashlib
import json
import re
import sys
from pathlib import Path

MARK = "-- DERIVED by leanweb/tools/port_checker.py from Compiler/lean/{rel}: edit that file, not this one."

# ---- anchors: exact text the tail-recursive versions were written against -----------------
MERGE_DEF = """def merge : List Move → List Rot → List Ev
  | [], rs => rs.map Ev.rot
  | ms, [] => ms.map Ev.mv
  | m :: ms, r :: rs =>
    if m.t ≤ r.t then Ev.mv m :: merge ms (r :: rs)
    else Ev.rot r :: merge (m :: ms) rs
  termination_by ms rs => ms.length + rs.length
"""
SNAPSHOTS_DEF = """def snapshots (loops : List (String × List String)) (st : List (String × String)) :
    List Ev → List (Nat × List (String × String))
  | [] => []
  | e :: es => (e.cyc, st) :: snapshots loops (Ev.apply loops st e) es
"""
# the TR blocks also use these, so they must still look like this
SUPPORT_ANCHORS = [
    "inductive Ev where\n  | mv : Move → Ev\n  | rot : Rot → Ev\n",
    "def Ev.cyc : Ev → Nat\n",
    "def Ev.apply (loops : List (String × List String))\n    (st : List (String × String)) : Ev → List (String × String)\n",
    "def events (inp : Input) : List Ev := merge inp.moves inp.rots\n",
]

MERGE_TR = """
/-! ### `merge`, in constant stack (added for the browser by port_checker.py)

`merge` recurses once per event, not in tail position.  Compiled to C that is harmless; under
the IR interpreter of the WebAssembly Lean the JavaScript stack runs out at about 550 nested
calls, and a BB round has thousands of events.  The `@[csimp]` lemma makes every use of
`merge` compiled after it run `mergeTR` instead.  `merge` itself -- what `Implements`
speaks about -- is unchanged; the lemma is a kernel-checked equality. -/

/-- `merge` with an accumulator (reversed). -/
def mergeTR.go : List Move → List Rot → List Ev → List Ev
  | [], rs, acc => acc.reverseAux (rs.map Ev.rot)
  | m :: ms, [], acc => acc.reverseAux ((m :: ms).map Ev.mv)
  | m :: ms, r :: rs, acc =>
    if m.t ≤ r.t then mergeTR.go ms (r :: rs) (Ev.mv m :: acc)
    else mergeTR.go (m :: ms) rs (Ev.rot r :: acc)
  termination_by ms rs _ => ms.length + rs.length

def mergeTR (ms : List Move) (rs : List Rot) : List Ev := mergeTR.go ms rs []

theorem mergeTR.go_eq (ms : List Move) (rs : List Rot) (acc : List Ev) :
    mergeTR.go ms rs acc = acc.reverse ++ merge ms rs := by
  induction ms, rs using merge.induct generalizing acc with
  | case1 rs => simp [mergeTR.go, merge, List.reverseAux_eq]
  | case2 ms hne =>
    match ms, hne with
    | m :: ms, _ => simp [mergeTR.go, merge, List.reverseAux_eq]
  | case3 m ms r rs h ih => simp [mergeTR.go, merge, h, ih]
  | case4 m ms r rs h ih => simp [mergeTR.go, merge, h, ih]

@[csimp] theorem merge_eq_mergeTR : @merge = @mergeTR := by
  funext ms rs; simp [mergeTR, mergeTR.go_eq]
"""
SNAPSHOTS_TR = """
/-! ### `snapshots`, in constant stack (added for the browser by port_checker.py)

The same reason and the same shape as `mergeTR`: one frame per event otherwise. -/

/-- `snapshots` with an accumulator (reversed). -/
def snapshotsTR.go (loops : List (String × List String)) :
    List (String × String) → List Ev → List (Nat × List (String × String)) →
      List (Nat × List (String × String))
  | _, [], acc => acc.reverse
  | st, e :: es, acc => snapshotsTR.go loops (Ev.apply loops st e) es ((e.cyc, st) :: acc)

def snapshotsTR (loops : List (String × List String)) (st : List (String × String))
    (es : List Ev) : List (Nat × List (String × String)) := snapshotsTR.go loops st es []

theorem snapshotsTR.go_eq (loops : List (String × List String)) (st : List (String × String))
    (es : List Ev) (acc : List (Nat × List (String × String))) :
    snapshotsTR.go loops st es acc = acc.reverse ++ snapshots loops st es := by
  induction es generalizing st acc with
  | nil => simp [snapshotsTR.go, snapshots]
  | cons e es ih => simp [snapshotsTR.go, snapshots, ih]

@[csimp] theorem snapshots_eq_snapshotsTR : @snapshots = @snapshotsTR := by
  funext loops st es; simp [snapshotsTR, snapshotsTR.go_eq]
"""
AXIOMS_HEAD = """/-
# The trusted base, printed

DERIVED by leanweb/tools/port_checker.py from the `#print axioms` lines of
Compiler/lean/QCCDC/Cert/Sound.lean.  Lean 4.29 refuses `#print axioms` inside a `module`,
and the browser copy of the checker is made of modules, so the lines live here, in an
ordinary file outside the library.
-/
import QCCDC.Cert.Sound

namespace QCCDC.Cert

"""
AXIOMS_EXTRA = """
-- added with the browser's tail-recursive replay (port_checker.py): the csimp equalities
#print axioms merge_eq_mergeTR
#print axioms snapshots_eq_snapshotsTR
"""
SOUND_NOTE = "-- (the `#print axioms` lines live in QCCDC/Cert/Axioms.lean: Lean 4.29 refuses them inside a module)"


class PortError(SystemExit):
    def __init__(self, msg: str):
        super().__init__(f"port_checker: {msg}")


def read_lf(p: Path) -> str:
    return p.read_bytes().decode("utf-8").replace("\r\n", "\n")


def port_module_syntax(rel: str, text: str) -> str:
    lines = text.split("\n")
    if not lines or not lines[0].startswith("/-"):
        raise PortError(f"{rel}: expected the file to open with a /- ... -/ comment (anchor for `module`)")
    if "module" in (l.strip() for l in lines):          # already ported
        return text
    close = next((i for i, l in enumerate(lines) if i > 0 and l.strip() == "-/"), None)
    if close is None:
        raise PortError(f"{rel}: the leading comment is never closed by a line '-/'")
    head, rest = lines[: close + 1], lines[close + 1:]
    for l in rest:
        if re.match(r"^\s*(public\s+|meta\s+|private\s+)*import\b", l) and not re.match(r"^import \S+\s*$", l):
            raise PortError(f"{rel}: unexpected import form {l!r}")
    # imports: the contiguous header after the comment (blank lines allowed)
    k = 0
    imports = []
    while k < len(rest) and (rest[k].strip() == "" or rest[k].startswith("import ")):
        if rest[k].startswith("import "):
            imports.append("public " + rest[k].rstrip())
        k += 1
    body = rest[k:]
    if any(l.startswith("import ") for l in body):
        raise PortError(f"{rel}: an `import` after the header; cannot place the section")
    private = any(re.match(r"^\s*private\s", l) for l in body)
    section = "public section" if private else "@[expose] public section"
    while body and body[-1].strip() == "":
        body.pop()
    out = head + [MARK.format(rel=rel), "module"] + imports + ["", section, ""] + body + ["", "end", ""]
    return "\n".join(out)


def port_check_tr(rel: str, text: str) -> str:
    if "mergeTR" in text and "snapshotsTR" in text:          # already ported
        return text
    for a in SUPPORT_ANCHORS + [MERGE_DEF, SNAPSHOTS_DEF]:
        n = text.count(a)
        if n != 1:
            first = a.strip().splitlines()[0]
            raise PortError(f"{rel}: anchor {first!r} found {n} times (expected exactly once); "
                            "the tail-recursive replay was written against it -- update port_checker.py")
    i_merge = text.index(MERGE_DEF) + len(MERGE_DEF)
    if text.index("def events (inp : Input)") < i_merge:
        raise PortError(f"{rel}: `events` precedes `merge`")
    i_snap = text.index(SNAPSHOTS_DEF) + len(SNAPSHOTS_DEF)
    # nothing between a definition and its csimp lemma may use it
    if re.search(r"\bmerge\b", text[i_merge: text.index("def events (inp : Input)")]) :
        raise PortError(f"{rel}: something between `merge` and `events` uses `merge`")
    text = text[:i_snap] + SNAPSHOTS_TR + text[i_snap:]
    text = text[:i_merge] + MERGE_TR + text[i_merge:]
    return text


def split_axioms(rel: str, text: str) -> tuple[str, list[str]]:
    lines = text.split("\n")
    ax = [l for l in lines if l.startswith("#print axioms")]
    if not ax:
        return text, []
    kept = []
    noted = False
    for l in lines:
        if l.startswith("#print axioms"):
            if not noted:
                kept.append(SOUND_NOTE)
                noted = True
            continue
        kept.append(l)
    return "\n".join(kept), ax


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    src, out = Path(argv[1]).resolve(), Path(argv[2]).resolve()
    cert = src / "QCCDC" / "Cert"
    if not (cert / "Check.lean").is_file() or not (src / "Main.lean").is_file():
        raise PortError(f"{src} does not look like Compiler/lean (no QCCDC/Cert/Check.lean or Main.lean)")
    rels = sorted(f"QCCDC/Cert/{p.name}" for p in cert.glob("*.lean")) + ["Main.lean"]
    originals = {rel: read_lf(src / rel) for rel in rels}
    derived: dict[str, str] = {}
    axioms_lines: list[str] = []
    for rel, text in originals.items():
        if rel == "QCCDC/Cert/Axioms.lean":                 # input is already a derived tree
            derived[rel] = text
            continue
        t = text
        if rel == "QCCDC/Cert/Sound.lean":
            t, axioms_lines = split_axioms(rel, t)
        if rel == "QCCDC/Cert/Check.lean":
            t = port_check_tr(rel, t)
        t = port_module_syntax(rel, t)
        derived[rel] = t
    if axioms_lines:
        derived["QCCDC/Cert/Axioms.lean"] = AXIOMS_HEAD + "\n".join(axioms_lines) + "\n" + AXIOMS_EXTRA + "\nend QCCDC.Cert\n"
    elif "QCCDC/Cert/Axioms.lean" not in derived:
        print("port_checker: warning: Sound.lean has no `#print axioms` lines; no Axioms.lean written", file=sys.stderr)
    # write the tree (only the files we own)
    for rel, t in derived.items():
        p = out / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(t.encode("utf-8"))
    diff = []
    for rel in sorted(derived):
        a = originals.get(rel)
        diff += difflib.unified_diff([] if a is None else a.splitlines(keepends=True), derived[rel].splitlines(keepends=True),
                                     fromfile="/dev/null" if a is None else f"a/Compiler/lean/{rel}", tofile=f"b/{rel}")
    (out / "port.diff").write_bytes("".join(diff).encode("utf-8"))
    sources = {rel: hashlib.sha256((src / rel).read_bytes()).hexdigest() for rel in rels}
    # no local path: this ends up in the published manifest
    label = "Compiler/lean" if src.parts[-2:] == ("Compiler", "lean") else src.name
    (out / "port-sources.json").write_text(json.dumps({"source": label, "files": sources}, indent=1) + "\n", encoding="utf-8")
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    print(f"port_checker: {len(derived)} files -> {out} (+{added} -{removed} lines; port.diff)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
