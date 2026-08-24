"""A fixture that exercises every TSIR instruction shape at least once.

The builder corpus (`export_programs.py`) covers `init` and `simd` and nothing else,
because every builder the platform ships is a *transport* builder.  That leaves `gate`,
`measure`, `reset`, `cool` and `barrier` untested by the round-trip -- along with three
spellings that only appear in hand-written programs and are exactly the kind of thing a
reader gets wrong:

  * a gate written as two `ions` rather than as `pairs` (both are legal; `iter_pairs`
    exists precisely because both occur);
  * a broadcast `cool` (`broadcast: true`, no `ions`) versus a per-ion one;
  * a multi-segment `via` list -- the shape that once made R8 false-fire.

This program is not meant to be physically interesting or even efficient.  It is meant
to contain one of everything, so that "the round-trip preserves the format" is a claim
about the format rather than about the two classes the deck happens to use.

    python Compiler/bridge/export_shapes.py -o Compiler/build/fixtures/shapes.tsir.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.ir.tsir import TSIR, Instruction, Participant, loop_shift  # noqa: E402


def build(arch: Architecture) -> TSIR:
    dev = arch.device
    loop_id = next(iter(dev.loops))
    rail = tuple(sg.id for sg in dev.loop_segments(loop_id))
    prog = TSIR(name="shapes", arch_spec="arch/ring144_24v.arch.json")

    prog.add(Instruction(
        type="init", id=prog.next_id(),
        placement={"d0": "S0", "d1": "S1", "a0": "A0", "a6": "A6"},
        quanta={"d0": 0.0, "d1": 0.0, "a0": 0.0, "a6": 0.0},
        meta={"why": "every field of `init`: placement and quanta together"}))

    # a template-form simd, with `holds` -- the rigid-rotation shape
    prog.add(Instruction(
        type="simd", id=prog.next_id(), cls="rotate_cw", mode="inter",
        template=loop_shift(loop_id, 1), holds=(loop_id,) + rail,
        meta={"shape": "template + holds"}))

    # an explicit-participant simd with a `via` list: the dock, off the named loop
    prog.add(Instruction(
        type="simd", id=prog.next_id(), cls="dock", mode="inter",
        participants=(Participant("d1", "S6", "A6", via=("V6",)),),
        meta={"shape": "participants + single-segment via"}))

    # a gate written as `pairs`, with `sites` -- the compiler's normal output
    prog.add(Instruction(
        type="gate", id=prog.next_id(), gate="CX", mode="intra",
        pairs=(("d1", "a6"),), sites=("A6",),
        meta={"shape": "gate as pairs, with sites"}))

    prog.add(Instruction(type="barrier", id=prog.next_id(),
                         meta={"shape": "barrier: costs nothing, synchronises"}))

    # a gate written as two `ions` -- the other legal spelling
    prog.add(Instruction(
        type="gate", id=prog.next_id(), gate="MS", mode="intra",
        ions=("d1", "a6"),
        meta={"shape": "gate as two ions rather than pairs"}))

    prog.add(Instruction(type="measure", id=prog.next_id(), ions=("a6",),
                         meta={"shape": "measure"}))
    prog.add(Instruction(type="reset", id=prog.next_id(), ions=("a6",),
                         meta={"shape": "reset"}))

    # both cooling spellings: global broadcast, and named ions
    prog.add(Instruction(type="cool", id=prog.next_id(), broadcast=True,
                         meta={"shape": "broadcast cool: one op cools every ion"}))
    prog.add(Instruction(type="cool", id=prog.next_id(), ions=("d0", "d1"),
                         meta={"shape": "per-ion cool"}))

    prog.add(Instruction(
        type="simd", id=prog.next_id(), cls="undock", mode="inter",
        participants=(Participant("d1", "A6", "S6", via=("V6",)),),
        meta={"shape": "undock, returning the ion to the rail"}))

    # annotations: claims, not content.  Present here so the round-trip has to preserve
    # `t0`/`t1`/`cost`/`steps`/`quanta_delta`/`operating_point` -- the fields the replay
    # recomputes and therefore never reads.
    prog.instructions[1] = prog.instructions[1].with_annotations(
        t0=0.0, t1=100.0, cost=148, steps=3,
        quanta_delta={"d0": 0.1, "d1": 0.1},
        operating_point={"shuttle_segment": {"us": 5, "quanta": 0.1}})

    prog.metrics = {"total_cost": 148, "note": "claims, checked by R9 not by the reader"}
    prog.meta = {"metrics_model": "deck", "purpose": "TSIR shape coverage"}
    return prog


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", required=True)
    args = ap.parse_args(argv)

    arch = Architecture.from_json(
        json.loads((ROOT / "arch/ring144_24v.arch.json").read_text(encoding="utf-8")))
    prog = build(arch)
    out = Path(args.out)
    prog.save(out, indent=1)
    kinds = sorted({i.type for i in prog.instructions})
    print(f"shapes: {len(prog)} instructions, types={kinds}")
    print(f"  -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
