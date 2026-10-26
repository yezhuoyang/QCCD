"""Read FormalRV's upper stages (.prog, .qppm, .qec) as statement trees with source positions.

Only what the provenance chain needs: every program/gadget unit, every statement with its line
(in the stage's joined text and in its member file), indented children under `for`/`when`.
The statements are kept as text; `provenance` interprets the few forms it anchors on.  A stage
may be a folder whose `sources` file lists its members (FormalRV's stage folders).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Stmt:
    text: str
    line: int                      # joined-stage line
    member: str                    # member file within a stage folder ('' for a single file)
    mline: int                     # line within the member
    children: list = field(default_factory=list)

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()


@dataclass
class Unit:
    kind: str                      # 'program' | 'gadget' | 'spec' | 'codes'
    name: str
    header: Stmt
    body: list

    def statements(self):
        for s in self.body:
            yield from s.walk()


@dataclass
class Stage:
    layer: str                     # 'prog' | 'qppm' | 'qec' | 'sysprog'
    path: str
    lines: list
    members: list                  # [(member, first joined line, n lines)]
    units: list

    def unit(self, kind: str, name: str | None = None) -> Unit | None:
        for u in self.units:
            if u.kind == kind and (name is None or u.name == name):
                return u
        return None

    @property
    def program(self) -> Unit | None:
        return self.unit("program")

    def gadgets(self) -> dict:
        return {u.name: u for u in self.units if u.kind == "gadget"}

    def text_at(self, line: int) -> str:
        return self.lines[line - 1] if 0 < line <= len(self.lines) else ""


def read_stage_text(path: str | Path) -> tuple[list, list]:
    """(lines, members) of a stage file or stage folder, with CR stripped."""
    path = Path(path)
    if path.is_dir():
        names = [n.strip() for n in (path / "sources").read_text(encoding="utf-8-sig").splitlines() if n.strip()]
        lines, members = [], []
        for name in names:
            part = (path / name).read_text(encoding="utf-8-sig").split("\n")
            if part and part[-1] == "":
                part = part[:-1]
            members.append((name, len(lines) + 1, len(part)))
            lines += [ln.rstrip("\r") for ln in part]
        return lines, members
    lines = [ln.rstrip("\r") for ln in Path(path).read_text(encoding="utf-8-sig").split("\n")]
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines, [("", 1, len(lines))]


def locate(members: list, line: int) -> tuple[str, int]:
    for name, first, n in members:
        if first <= line < first + n:
            return name, line - first + 1
    return "", line


_HEAD = re.compile(r"(program|gadget|spec)\b\s*([^\s\[(:]*)")


def _with_uses(path: Path, lines: list, members: list, seen: set) -> tuple[list, list]:
    """A `.prog` may `use` another stage (`use ../Primer/primer.prog`): its lines are appended,
    each member named by its path relative to this stage, so every link still names a real
    file and line."""
    for raw in list(lines):
        m = re.match(r"\s*use\s+(\S+)\s*$", raw)
        if not m:
            continue
        other = (path.parent / m[1]).resolve()
        if other in seen or not other.exists():
            continue
        seen.add(other)
        olines, omem = read_stage_text(other)
        olines, omem = _with_uses(other, olines, omem, seen)
        base = len(lines)
        rel = m[1].rstrip("/")
        for name, first, n in omem:
            members.append((f"{rel}/{name}" if name else rel, base + first, n))
        lines = lines + olines
    return lines, members


def read(path: str | Path, layer: str) -> Stage:
    lines, members = read_stage_text(path)
    if layer == "prog":
        lines, members = _with_uses(Path(path), lines, members, {Path(path).resolve()})
    rows = []
    for i, raw in enumerate(lines, 1):
        s = raw.split("#", 1)[0].rstrip() if layer != "prog" else raw.rstrip()
        if layer == "prog" and s.lstrip().startswith("#"):
            continue
        if not s.strip():
            continue
        rows.append((len(s) - len(s.lstrip(" ")), s.strip(), i))
    pos = 0

    def stmt(text: str, ln: int) -> Stmt:
        mem, ml = locate(members, ln)
        return Stmt(text, ln, mem, ml)

    def block(parent: int) -> list:
        nonlocal pos
        out = []
        while pos < len(rows) and rows[pos][0] > parent:
            depth, text, ln = rows[pos]
            pos += 1
            s = stmt(text, ln)
            s.children = block(depth)
            out.append(s)
        return out

    units = []
    while pos < len(rows):
        depth, text, ln = rows[pos]
        pos += 1
        m = _HEAD.match(text)
        if depth == 0 and m and text.endswith(":"):
            kind = m[1]
            name = m[2] or ("program" if kind == "program" else "")
            if kind == "program" and not m[2]:
                name = "program"
            elif kind == "program":
                name = m[2]
            head = stmt(text, ln)
            units.append(Unit(kind, name, head, block(depth)))
        elif depth == 0 and text == "codes:":
            head = stmt(text, ln)
            units.append(Unit("codes", "codes", head, block(depth)))
        else:
            block(depth)            # header lines (code, params, define, noise ...) and their bodies
    return Stage(layer, str(path), lines, members, units)
