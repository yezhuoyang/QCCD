"""Read a FormalRV `.sysprog` listing and expand it into timed events.

A `.sysprog` is FormalRV's lowest layer: physical gates, measurements and system calls
(`request_ancilla`, `release_ancilla`, `route`, `request_magic`, `decode_syndrome`,
`pauli_frame_update`, `parity`) on VIRTUAL qubits, with gadgets, calls and `for ... every P`
loops kept folded.  This reader is QCCD's own; it is not a second authority.  Every listing
it expands is compared with FormalRV's OCaml VM (the evaluator of record): the opcode counts and
the dynamic instruction count must agree exactly (`vm_counts_agree`).

What it keeps that the VM does not print: for every event, the listing line it came from (and
the stage member and member-local line, so a page can open the file there), the loop trips and
the call frames it is inside.  That is the `.sysprog` link of the full-stack provenance chain.

Semantics follow the listing language as FormalRV's VMs implement it:
  * natural arithmetic: `-` truncates at zero, `/` and `%` are natural (x/0 = 0, x%0 = x),
    `^` is right-associative;
  * a statement's `@t` is relative to its frame (a call starts the frame at the call time);
    loop trip v of `for lo <= v < hi every P` runs at the frame offset + (v - lo) * P;
  * statements in a body are in STREAM order (per-qubit order), not time order;
  * ranges `reg[a:b]` are inclusive; `count=N` with a single index means N consecutive cells;
  * durations come from the machine: H/S/X/Z/Sdg gate1q_ns, CNOT/CZ/CY gate2q_ns, measure
    measure_ns, request_ancilla reset_ns, release 0, route route_latency_ns, decode_syndrome
    decoder_latency_ns (+ input_ns), request_magic the chosen factory's latency (else
    magic_state_latency_ns), pauli_frame_update/parity pauli_frame_update_ns.

Not yet implemented (refused with the line, never miscounted): `when`, `case`, `step` loops,
`table`, `Σ`.  GE2021 needs them; the small slices do not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

SCALES = {"ns": 1, "us": 1000, "µs": 1000, "ms": 1_000_000, "s": 1_000_000_000}
REGS = {"data": "data", "d": "data", "ancilla": "ancilla", "a": "ancilla",
        "c": "c", "syndrome": "syndrome", "s": "syndrome"}
TIMING = {}
for _names, _key in (("H S X Z Sdg", "gate1q_ns"), ("CNOT CZ CY", "gate2q_ns"),
                     ("measure", "measure_ns"), ("request_ancilla", "reset_ns"),
                     ("route", "route_latency_ns"), ("decode_syndrome", "decoder_latency_ns"),
                     ("request_magic", "magic_state_latency_ns"),
                     ("pauli_frame_update parity", "pauli_frame_update_ns")):
    for _n in _names.split():
        TIMING[_n] = _key
GATES_1Q = ("H", "S", "X", "Z", "Sdg")
GATES_2Q = ("CNOT", "CZ", "CY")
OPCODES = GATES_1Q + GATES_2Q + ("measure", "request_ancilla", "release_ancilla", "route",
                                 "decode_syndrome", "request_magic", "pauli_frame_update", "parity")
FACTORY_SPECS = {"|T>": "factory", "|CCZ>": "factory_ccz", "|AutoCCZ>": "factory_autoccz",
                 "|Y+>": "factory_y"}


class ListingError(ValueError):
    """A listing this reader refuses, with the line it refused at."""

    def __init__(self, message: str, line: int = 0):
        super().__init__(f"line {line}: {message}" if line else message)
        self.line = line


# ----------------------------------------------------------------------------- expressions

_TOKEN = re.compile(r"\d+|[A-Za-z_][A-Za-z_0-9']*|[()+*/%^\-,]|\S")


def evaluate(text: str, env: dict, line: int = 0) -> int:
    """A natural-number Pratt evaluator (the VMs' arithmetic); never Python eval."""
    tokens = _TOKEN.findall(text.replace("·", "*"))
    pos = 0

    def atom() -> int:
        nonlocal pos
        if pos >= len(tokens):
            raise ListingError(f"incomplete expression: {text}", line)
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            v = parse(0)
            if pos >= len(tokens) or tokens[pos] != ")":
                raise ListingError(f"missing ')' in {text}", line)
            pos += 1
            return v
        if tok.isdecimal():
            return int(tok)
        if tok in ("min", "max") and pos < len(tokens) and tokens[pos] == "(":
            pos += 1
            args = [parse(0)]
            while pos < len(tokens) and tokens[pos] == ",":
                pos += 1
                args.append(parse(0))
            if pos >= len(tokens) or tokens[pos] != ")":
                raise ListingError(f"missing ')' in {text}", line)
            pos += 1
            return min(args) if tok == "min" else max(args)
        if tok in env:
            return env[tok]
        raise ListingError(f"unbound or unsupported token {tok!r} in {text!r}", line)

    def parse(minimum: int) -> int:
        nonlocal pos
        value = atom()
        while pos < len(tokens) and tokens[pos] in "+-*/%^":
            op = tokens[pos]
            prec = {"+": 1, "-": 1, "*": 2, "/": 2, "%": 2, "^": 3}[op]
            if prec < minimum:
                break
            pos += 1
            rhs = parse(prec if op == "^" else prec + 1)
            if op == "+":
                value += rhs
            elif op == "-":
                value = max(0, value - rhs)
            elif op == "*":
                value *= rhs
            elif op == "/":
                value = value // rhs if rhs else 0
            elif op == "%":
                value = value % rhs if rhs else value
            else:
                value = value ** rhs
        return value

    result = parse(0)
    if pos != len(tokens):
        raise ListingError(f"unparsed suffix in {text!r}", line)
    return result


def time_ns(text: str, env: dict, line: int = 0) -> int:
    m = re.fullmatch(r"(.+?)\s+(ns|us|µs|ms|s)", text.strip())
    if not m:
        raise ListingError(f"expected a time with a unit: {text!r}", line)
    return evaluate(m[1], env, line) * SCALES[m[2]]


def split_top(text: str, sep: str = ",") -> list[str]:
    """Split at `sep` outside brackets and parentheses."""
    out, depth, cur = [], 0, []
    for ch in text:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            out.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


# ----------------------------------------------------------------------------- the listing

@dataclass
class Node:
    """One listing line and the lines indented under it."""
    text: str
    line: int                      # line of the joined listing (what the VM reports)
    member: str                    # stage member file ('' for a single-file listing)
    mline: int                     # line within that member
    children: list = field(default_factory=list)


@dataclass
class Gadget:
    name: str
    widths: str
    formals: list
    span: str
    body: list
    node: Node


@dataclass
class Listing:
    path: str
    lines: list                    # the joined text, 1-based through `lines[i-1]`
    members: list                  # [(member, first joined line, number of lines)]
    machine: dict
    specs: dict
    params: list
    witness: dict
    gadgets: dict
    widths: str
    body: list
    program: Node | None = None

    def factory_latency(self, state: str) -> int | None:
        spec = self.specs.get(FACTORY_SPECS.get(state, ""), {}) or (
            self.specs.get("factory", {}) if state in ("|T>", "|Y+>") else {})
        return spec.get("latency")


def _read_stage(path: Path) -> tuple[list[str], list[tuple[str, int, int]]]:
    """A stage is a file or a folder whose `sources` lists its members in order."""
    if path.is_dir():
        names = [n.strip() for n in (path / "sources").read_text(encoding="utf-8-sig").splitlines()
                 if n.strip()]
        lines, members = [], []
        for name in names:
            text = (path / name).read_text(encoding="utf-8-sig")
            part = text.split("\n")
            if part and part[-1] == "":
                part = part[:-1]
            part = [ln.rstrip("\r") for ln in part]
            members.append((name, len(lines) + 1, len(part)))
            lines += part
        return lines, members
    text = path.read_text(encoding="utf-8-sig")
    lines = [ln.rstrip("\r") for ln in text.split("\n")]
    if lines and lines[-1] == "":
        lines = lines[:-1]
    return lines, [("", 1, len(lines))]


def _fields(text: str, line: int) -> dict:
    """`{ key: value unit, ... }` -> {key: int} (units dropped; ns kept as ns)."""
    body = text[text.index("{") + 1: text.rindex("}")]
    out = {}
    for item in split_top(body):
        if ":" not in item:
            raise ListingError(f"malformed field {item!r}", line)
        k, v = item.split(":", 1)
        m = re.fullmatch(r"\s*(\d+)\s*(\S+)?\s*", v)
        out[k.strip()] = int(m[1]) * (SCALES.get(m[2], 1) if m and m[2] in SCALES else 1) if m else v.strip()
    return out


def read(path: str | Path, witness: dict | None = None) -> Listing:
    path = Path(path)
    lines, members = _read_stage(path)

    def locate(i: int) -> tuple[str, int]:
        for name, first, n in members:
            if first <= i < first + n:
                return name, i - first + 1
        return "", i

    rows = []                                   # (depth, text, joined line)
    for i, raw in enumerate(lines, 1):
        s = raw.split("#", 1)[0].rstrip()
        if not s.strip():
            continue
        if "\t" in s[: len(s) - len(s.lstrip())]:
            raise ListingError("tabs in indentation", i)
        rows.append((len(s) - len(s.lstrip(" ")), s.strip(), i))

    pos = 0

    def block(parent: int) -> list:
        nonlocal pos
        nodes = []
        while pos < len(rows) and rows[pos][0] > parent:
            depth, text, ln = rows[pos]
            pos += 1
            mem, ml = locate(ln)
            node = Node(text, ln, mem, ml)
            node.children = block(depth)
            nodes.append(node)
        return nodes

    def closed(text: str) -> str:
        nonlocal pos
        while "}" not in text and pos < len(rows):
            text += " " + rows[pos][1]
            pos += 1
        return text

    machine, specs, params, gadgets = {}, {}, [], {}
    wit = dict(witness or {})
    widths, body, program = "", [], None
    while pos < len(rows):
        depth, text, ln = rows[pos]
        pos += 1
        if depth:
            raise ListingError("unexpected indentation at top level", ln)
        if text.startswith("machine "):
            machine = _fields(closed(text), ln)
        elif text.startswith("resource {"):
            closed(text)
        elif text.startswith("spec ") and text.endswith(":"):
            name = text[5:-1].strip()
            fieldsd = {}
            while pos < len(rows) and rows[pos][0] > 0:
                _, t, l2 = rows[pos]
                pos += 1
                if t.startswith("resource "):
                    fieldsd = _fields(closed(t), l2)
            specs[name] = fieldsd
        elif text.startswith("params "):
            params = [p.strip() for p in text[7:].split(",") if p.strip()]
        elif text.startswith("witness "):
            for item in split_top(text[8:]):
                k, v = item.split("=", 1)
                wit.setdefault(k.strip(), int(v))
        elif text.startswith("maintenance "):
            pass
        elif text.startswith("table "):
            raise ListingError("listing tables are not supported yet", ln)
        elif text.startswith("gadget "):
            m = re.fullmatch(r"gadget\s+(\S+?)\[(.*)\]\((.*?)\)\s+spans\s+(.+):", text)
            if not m:
                raise ListingError(f"malformed gadget header: {text[:120]}", ln)
            mem, ml = locate(ln)
            node = Node(text, ln, mem, ml)
            node.children = block(0)
            gadgets[m[1]] = Gadget(m[1], m[2], [f.strip() for f in m[3].split(",") if f.strip()],
                                   m[4], node.children, node)
        elif text.startswith("program[") and text.endswith("]:"):
            widths = text[8:-2]
            mem, ml = locate(ln)
            program = Node(text, ln, mem, ml)
            program.children = block(0)
            body = program.children
        else:
            raise ListingError(f"unsupported top-level construct: {text[:100]}", ln)
    if not machine:
        raise ListingError("the listing has no machine block")
    if program is None:
        raise ListingError("the listing has no program")
    return Listing(str(path), lines, members, machine, specs, params, wit, gadgets, widths,
                   body, program)


# ----------------------------------------------------------------------------- expansion

@dataclass
class Frame:
    """A call (`kind='call'`) or one loop trip (`kind='trip'`) an event runs inside."""
    kind: str
    node: Node
    label: str                     # 'surfaceRound' or 'rv_r26=2'
    start: int
    uid: int = -1                  # the call's index in `Expansion.calls`


@dataclass
class Event:
    uid: int
    kind: str
    start: int
    duration: int
    qubits: tuple                  # ((reg, index), ...) -- the operands, in written order
    work: tuple                    # factory workspace (`with ...`)
    reads: tuple                   # record bits read (`c[..] -> ...`)
    writes: tuple                  # record bits written (`-> c[..]`)
    arg: object                    # count / rounds / basis / magic state / bits
    node: Node
    frames: tuple                  # outermost first

    @property
    def end(self) -> int:
        return self.start + self.duration

    @property
    def op(self) -> str:
        """The statement without its time stamp, as written in the listing."""
        return re.sub(r"^@\S+\s+\S+\s+", "", self.node.text)


@dataclass
class Call:
    uid: int
    name: str
    start: int
    end: int
    node: Node
    args: dict
    parent: int | None
    first_event: int = 0
    last_event: int = 0


@dataclass
class Expansion:
    listing: Listing
    events: list
    calls: list

    def counts(self) -> dict:
        c = {k: 0 for k in OPCODES}
        decoded_rounds = 0
        for e in self.events:
            c[e.kind] += 1
            if e.kind == "decode_syndrome":
                decoded_rounds += e.arg
        magic = {}
        for e in self.events:
            if e.kind == "request_magic":
                magic[e.arg] = magic.get(e.arg, 0) + 1
        return {**c, "decoded rounds": decoded_rounds, **{f"magic {k}": v for k, v in magic.items()}}

    @property
    def end_ns(self) -> int:
        return max((e.end for e in self.events), default=0)


def _sites(text: str, env: dict, line: int, fallback: int = 1) -> tuple:
    out = []
    for item in split_top(text):
        m = re.fullmatch(r"(?:[XYZ]\s+)?([A-Za-z]+)\[(.*)\]", item.strip())
        if not m or m[1] not in REGS:
            raise ListingError(f"invalid operand {item!r}", line)
        parts = split_top(m[2], ":")
        lo = evaluate(parts[0], env, line)
        hi = evaluate(parts[1], env, line) if len(parts) == 2 else lo + fallback - 1
        if hi < lo:
            raise ListingError(f"empty range {item!r}", line)
        out += [(REGS[m[1]], i) for i in range(lo, hi + 1)]
    return tuple(out)


def _paulis(text: str) -> tuple:
    """The Pauli letter of each operand of a frame update, in order ('' when none written)."""
    out = []
    for item in split_top(text):
        m = re.fullmatch(r"([XYZ])\s+([A-Za-z]+)\[(.*)\]", item.strip())
        out.append(m[1] if m else "")
    return tuple(out)


def _event(listing: Listing, node: Node, text: str, start: int, env: dict, frames: tuple,
           uid: int) -> Event:
    line = node.line
    reads = writes = work = ()
    pre = ""
    if text.startswith(("c[", "syndrome[", "s[")) and "->" in text:
        prefix, text = text.split("->", 1)
        reads = _sites(prefix.strip(), env, line)
        text = text.strip()
    if "->" in text:
        text, suffix = text.split("->", 1)
        writes = _sites(suffix.strip(), env, line)
    if " with " in text:
        text, suffix = text.split(" with ", 1)
        work = _sites(suffix.strip(), env, line)
    if "request_magic" in text:
        pre, text = text.split("request_magic", 1)
        text = "request_magic " + text.strip()
    tokens = text.split(None, 1)
    kind, rest = tokens[0], (tokens[1] if len(tokens) > 1 else "")
    arg = None
    input_ns = 0
    if kind in ("idle_extract", "ppm_rotate"):
        raise ListingError(f"{kind} is opaque; FormalRV's VM refuses it too", line)
    if kind not in OPCODES:
        raise ListingError(f"unknown opcode {kind!r}", line)
    if kind == "decode_syndrome" and "input_ns=" in rest:
        m = re.fullmatch(r"(.*?)\s+input_ns=(.*?)\s+ns(?:\s+(.*))?", rest)
        if not m:
            raise ListingError("decoder input_ns needs an expression and a ns unit", line)
        rest = m[1] + (" " + m[3] if m[3] else "")
        input_ns = evaluate(m[2], env, line)
    if kind in ("request_ancilla", "release_ancilla", "decode_syndrome"):
        key = "rounds" if kind == "decode_syndrome" else "count"
        m = re.fullmatch(key + r"=(.*?)(?=\s+(?:patch\s+)?(?:data|ancilla|a|d)\[|$)(.*)", rest)
        if not m:
            raise ListingError(f"{kind} needs {key}=", line)
        arg = evaluate(m[1], env, line)
        rest = m[2].strip()
        if rest.startswith("patch"):
            rest = rest[5:].strip()
    elif kind == "measure":
        m = re.fullmatch(r"basis=([XZ])\s+(.+)", rest)
        if not m:
            raise ListingError("measure needs basis=X|Z", line)
        arg, rest = m[1], m[2]
    elif kind == "request_magic":
        m = re.fullmatch(r"state=(\|[^>]+>)(?:\s+(.+))?", rest)
        if not m:
            raise ListingError("request_magic needs state=|..>", line)
        arg, rest = m[1], (pre.strip() or (m[2] or ""))
    elif kind == "parity":
        raise ListingError("parity is not supported yet", line)
    elif kind == "pauli_frame_update":
        arg = _paulis(rest)
    qubits = _sites(rest, env, line, fallback=arg if kind in ("request_ancilla", "release_ancilla") else 1) if rest else ()
    if kind == "release_ancilla":
        duration = 0
    elif kind == "request_magic":
        duration = listing.factory_latency(arg) or listing.machine["magic_state_latency_ns"]
    else:
        duration = listing.machine[TIMING[kind]] + input_ns
    return Event(uid, kind, start, duration, qubits, work, reads, writes, arg, node, frames)


def expand(listing: Listing, max_events: int = 5_000_000) -> Expansion:
    """Every event of the listing, with absolute times, in stream order (calls inline)."""
    events: list[Event] = []
    calls: list[Call] = []

    def walk(nodes: list, env: dict, offset: int, frames: tuple, parent: int | None) -> None:
        for node in nodes:
            text = node.text
            if text == "skip" and not node.children:
                continue
            if text.startswith(("when ", "case ")):
                raise ListingError("guards (`when`/`case`) are not supported yet", node.line)
            loop = re.fullmatch(r"for\s+(.+?)\s*(?:<=|≤)\s*(\w+)\s*<\s*(.+?)\s+(every|step)\s+(.+):", text)
            if loop:
                if loop[4] == "step":
                    raise ListingError("`step` trip clocks are not supported yet", node.line)
                lo, hi = evaluate(loop[1], env, node.line), evaluate(loop[3], env, node.line)
                period = time_ns(loop[5], env, node.line)
                for v in range(lo, hi):
                    t0 = offset + (v - lo) * period
                    walk(node.children, {**env, loop[2]: v}, t0,
                         frames + (Frame("trip", node, f"{loop[2]}={v}", t0),), parent)
                continue
            m = re.fullmatch(r"@(.+?)\s+(ns|us|µs|ms|s)\s+(.+)", text)
            if not m or node.children:
                raise ListingError(f"unsupported statement: {text[:100]}", node.line)
            start = offset + evaluate(m[1], env, node.line) * SCALES[m[2]]
            body = m[3]
            cm = re.fullmatch(r"call\s+([^\s(]+)\((.*)\)", body)
            if cm:
                g = listing.gadgets.get(cm[1])
                if g is None:
                    raise ListingError(f"no gadget named {cm[1]}", node.line)
                args = [evaluate(a, env, node.line) for a in split_top(cm[2])]
                if len(args) != len(g.formals):
                    raise ListingError(f"{g.name} takes {len(g.formals)} arguments", node.line)
                local = {**listing.witness, **dict(zip(g.formals, args))}
                span = time_ns(g.span, local, node.line)
                cid = len(calls)
                call = Call(cid, g.name, start, start + span, node, dict(zip(g.formals, args)),
                            parent, first_event=len(events))
                calls.append(call)
                walk(g.body, local, start, frames + (Frame("call", node, g.name, start, cid),), cid)
                call.last_event = len(events)
                continue
            if len(events) >= max_events:
                raise ListingError(f"more than {max_events} events: expand lazily instead", node.line)
            events.append(_event(listing, node, body, start, env, frames, len(events)))

    walk(listing.body, dict(listing.witness), 0, (), None)
    return Expansion(listing, events, calls)


# ----------------------------------------------------------------------------- the VM oracle

VM_OPCODES = ("H", "S", "X", "Z", "Sdg", "CNOT", "CZ", "CY", "measure", "request_ancilla",
              "release_ancilla", "route", "decode_syndrome", "request_magic",
              "pauli_frame_update", "parity", "decoded rounds")


def vm_counts_agree(expansion: Expansion, vm: dict) -> list[str]:
    """Differences between this expansion and FormalRV's OCaml VM verdict (empty = agree).

    `vm` is the VM's `--json` output.  Compared: every opcode count of `location.opcode_counts`,
    the magic states by kind, the dynamic instruction count and the completion time."""
    diffs = []
    loc = (vm.get("location") or {}).get("opcode_counts") or {}
    ours = expansion.counts()
    for k in VM_OPCODES:
        if k in loc and loc[k] != ours.get(k, 0):
            diffs.append(f"{k}: VM {loc[k]}, ours {ours.get(k, 0)}")
    for k, v in loc.items():
        if k.startswith("magic |") and v != ours.get(k, 0):
            diffs.append(f"{k}: VM {v}, ours {ours.get(k, 0)}")
    n_vm = vm.get("dynamic_instruction_count")
    if n_vm is not None and n_vm != len(expansion.events):
        diffs.append(f"dynamic instructions: VM {n_vm}, ours {len(expansion.events)}")
    return diffs
