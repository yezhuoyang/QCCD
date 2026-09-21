"""Calling the decoder: one `decode` per syndrome window.  docs/tsir.md.

A compiled error-correction programme measures its ancillas and stops there -- the
syndrome bits exist, and nothing in the programme says they go anywhere.  On a real
machine they go to the decoder, and what the decoder decides goes to the classical memory
that holds each block's Pauli frame.  `decode` is the instruction that says so, and this
pass puts it where it belongs: as soon as a window of outcomes is complete.

A WINDOW ENDS where an ion would be measured a second time, or where the programme ends.
That is a whole syndrome round when every check has its own ancilla, and a slice of one
when ancillas are re-used: `ring144_24v a24` measures its 144 checks with 24 ancillas, six
at a time each, so its round is six windows of 24 outcomes and the decoder is called six
times -- as each batch completes, which is how a streaming decoder is fed.  The decode goes
straight after the window's LAST measurement and names every ion measured in it, in the
order they were measured.

It changes no number the machine is judged by.  A decode moves no ion and costs no machine
time (`qccd.verify.replay`): the decoder works alongside the ions, and only something that
reads its answer would wait for it.  `tests/test_decode_instruction.py` checks that every
replayed total is identical to the microsecond with and without the pass.

The pass is idempotent -- a programme that already calls the decoder is returned as it is
-- and new instructions take ids above every existing one, so a certificate or a source
map keyed by instruction id still joins exactly as it did.
"""

from __future__ import annotations

from dataclasses import replace

from ..ir.tsir import TSIR, Instruction

__all__ = ["insert_decodes", "windows"]


def windows(prog: TSIR) -> list[tuple[int, list[str], list[int]]]:
    """`(position of the window's last measurement, ions in order, measure ids)` per window."""
    out: list[tuple[int, list[str], list[int]]] = []
    ions: list[str] = []
    seen: set[str] = set()
    ids: list[int] = []
    last = -1
    for k, instr in enumerate(prog.instructions):
        if instr.type != "measure":
            continue
        if seen & set(instr.ions):
            out.append((last, ions, ids))
            ions, seen, ids = [], set(), []
        for ion in instr.ions:
            if ion not in seen:
                seen.add(ion)
                ions.append(ion)
        ids.append(instr.id)
        last = k
    if ions:
        out.append((last, ions, ids))
    return out


def insert_decodes(prog: TSIR) -> TSIR:
    """The programme with a `decode` after the last measurement of every syndrome window."""
    if any(i.type == "decode" for i in prog.instructions):
        return prog
    found = windows(prog)
    if not found:
        return prog
    after = {pos: (n, ions, ids) for n, (pos, ions, ids) in enumerate(found)}
    # ids are identities, allocated from the programme's own high-water mark and never
    # re-used (`TSIR.id_seq`), so every existing join still points where it did
    next_id = max(prog.id_seq, max((i.id for i in prog.instructions), default=-1) + 1)
    out: list[Instruction] = []
    for k, instr in enumerate(prog.instructions):
        out.append(instr)
        if k in after:
            n, ions, ids = after[k]
            out.append(Instruction(
                type="decode", id=next_id, ions=tuple(ions),
                meta={"kind": "decode", "window": n, "reads": list(ids),
                      "inserted_by": "qccd.compile.decode"}))
            next_id += 1
    return replace(prog, instructions=out, id_seq=next_id)
