"""One chain position a laser step (R34): split a compiled programme's laser instructions.

A compiler that knows nothing of chain order (qccdc) plays a layer's pulses, readouts and resets in
one instruction even where they reach ions at different positions of their chains.  A broadcast
laser reaches one position a step (rule R34, the team's feedback F2), so such an instruction is
split here into one instruction per position pattern, played one after another in the order of the
patterns' first operands.  Nothing else changes: the moves, the frame changes (VZ and I are no
pulse) and every operand's angle stay as they were, and the certificate is renumbered to match --
each gate's witness names the part that now plays it, and every cycle index (`t`) moves with the
instructions.  The chain order is kept as `qccd.ruleset.programme` keeps it (`qccd.verify.chains`).

    split_by_position(prog, cert, arch) -> (prog, cert, extra instructions)

Run it on the programme the certificate describes, before cooling (a cooling adds no move).
"""

from __future__ import annotations

import copy
from typing import Mapping

from ..ir.tsir import Instruction, TSIR
from ..verify.chains import Chains

__all__ = ["split_by_position"]

_NO_PULSE = ("VZ", "I")


def _groups(ins: Instruction, chains: Chains) -> list[list[int]] | None:
    """The operand indices of `ins` by the position pattern they reach, first operand's first, or None
    when it needs no split (one pattern, no laser, or nothing to split)."""
    if ins.type == "gate":
        if ins.gate in _NO_PULSE:
            return None
        ops = [tuple(p) for p in ins.pairs] if ins.pairs else [(i,) for i in ins.ions]
    elif ins.type in ("measure", "reset"):
        ops = [(i,) for i in ins.ions]
    else:
        return None
    if len(ops) < 2:
        return None
    by: dict = {}
    for k, o in enumerate(ops):
        by.setdefault(chains.pattern(o), []).append(k)
    return list(by.values()) if len(by) > 1 else None


def _part(ins: Instruction, idx: list[int], new_id: int, pos: Mapping[str, str]) -> Instruction:
    d = ins.to_json()
    d["id"] = new_id
    if ins.type == "gate" and ins.pairs:
        d["pairs"] = [list(ins.pairs[k]) for k in idx]
        ions = [i for k in idx for i in ins.pairs[k]]
    else:
        d["ions"] = [ins.ions[k] for k in idx]
        ions = list(d["ions"])
    if ins.params and len(ins.params) == (len(ins.pairs) if ins.pairs else len(ins.ions)):
        d["params"] = [list(ins.params[k]) for k in idx]
    if ins.sites:
        d["sites"] = sorted({pos[i] for i in ions if i in pos})
    meta = dict(ins.meta or {})
    for key in ("op", "source_op"):
        v = meta.get(key)
        if isinstance(v, list) and len(v) == (len(ins.pairs) if ins.pairs else len(ins.ions)):
            meta[key] = [v[k] for k in idx]
    meta["position_part"] = True
    d["meta"] = meta
    return Instruction.from_json(d)


def split_by_position(prog: TSIR, cert: dict, arch) -> tuple[TSIR, dict, int]:
    """`prog` with every laser instruction that reaches more than one position pattern split into
    one per pattern, and `cert` renumbered to match.  Returns (programme, certificate, the number of
    instructions added)."""
    dev = arch.device
    chains = Chains(dev)
    out = TSIR(name=prog.name, arch_spec=prog.arch_spec, meta=copy.deepcopy(prog.meta))
    next_id = max((i.id for i in prog.instructions), default=0) + 1

    part_of: dict[int, list[tuple[Instruction, set]]] = {}   # old id -> its parts and their ions
    pos: dict[str, str] = {}
    for t, ins in enumerate(prog.instructions):

        if ins.type == "init":
            chains.start(ins.placement)
            pos.update(ins.placement)
            out.instructions.append(ins)
            continue
        groups = _groups(ins, chains)
        if groups is None:
            out.instructions.append(ins)
        else:
            parts = []
            for g, idx in enumerate(groups):
                p = _part(ins, idx, ins.id if g == 0 else next_id, pos)
                if g:
                    next_id += 1
                ions = {i for k in idx for i in (ins.pairs[k] if ins.pairs else (ins.ions[k],))}
                parts.append((p, ions))
                out.instructions.append(p)
            part_of[ins.id] = parts
        if ins.type == "simd" and ins.participants:
            chains.move([(p.ion, p.src, p.dst, *chains.route_rails(p.src, p.dst, p.via)) for p in ins.participants],
                        gate_swaps=bool((ins.meta or {}).get("gate_swaps")))
            for p in ins.participants:
                pos[p.ion] = p.dst
    added = len(out.instructions) - len(prog.instructions)
    if not added:
        return prog, cert, 0
    c = copy.deepcopy(cert)
    # A certificate's `t` is its compiler's cycle clock, not a list index (qccdc counts its own); only
    # the order of moves and gates matters to the checker.  So every `t` after a split instruction
    # moves on by the parts inserted before it, and a split instruction's parts take T, T+1, ...
    at_t: dict[int, int] = {}                  # split instruction id -> its t, from the gates it witnesses
    for g in c.get("gates", []):
        if g.get("instr") in part_of:
            at_t.setdefault(g["instr"], g["t"])
    extra = sorted((t, len(part_of[i]) - 1) for i, t in at_t.items())

    def shift(x: int) -> int:
        return sum(n for t, n in extra if t < x)
    for m in c.get("moves", []):
        m["t"] += shift(m["t"])
    for g in c.get("gates", []):
        parts = part_of.get(g.get("instr"))
        if parts:
            # the witness is an op's last pulse: of the parts, the last one that pulses any of its ions
            ions = set(g.get("ions") or ())
            k = max((j for j, (_, have) in enumerate(parts) if ions & have), default=len(parts) - 1)
            g["instr"] = parts[k][0].id
            g["t"] = g["t"] + shift(g["t"]) + k
        else:
            g["t"] += shift(g["t"])
    claims = c.get("claims")
    if isinstance(claims, dict) and isinstance(claims.get("cycles"), int):
        claims["cycles"] += added
    return out, c, added
