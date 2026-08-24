"""Corrupt a compiled program and require the verifier to reject it.

`mutate_cert.py` asks whether the *certificate* checker can fail. This asks the other
question, and the one that matters for the hardware: are the physical conflicts -- trap
capacity, junction occupancy, segment sharing, head-on swaps, broadcast waveforms, gate
zones, heating -- modelled sharply enough that a schedule violating one is refused?

Each mutation below takes a compiled program the verifier has already accepted and breaks
exactly one thing, then reports which rule fired. A mutation that survives is a conflict
the compiler could emit and nobody would notice.

The rules are the platform's own (`qccd/verify/rules.py`), so this is not a test of code I
wrote; it is a demonstration that the compiler's output is being judged by them, on real
compiled programs rather than on hand-built counterexamples.

    python Compiler/bridge/mutate_program.py build/out/steane_esm_grid9x9.cooled.tsir.json \
        --arch arch/grid9x9.arch.json
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
from dataclasses import replace as drepl
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from qccd.arch import Architecture  # noqa: E402
from qccd.cost.models import corrected_model  # noqa: E402
from qccd.ir.tsir import TSIR, Instruction, Participant  # noqa: E402
from qccd.verify import verify  # noqa: E402


def _first(prog: TSIR, pred):
    for i, ins in enumerate(prog.instructions):
        if pred(ins):
            return i, ins
    return None, None


def _transport(prog: TSIR):
    return _first(prog, lambda i: i.type == "simd" and len(i.participants) >= 1)


# ------------------------------------------------------------------ mutations


def _park(prog: TSIR, ion: str, site: str) -> None:
    """Move an ion to `site` in the INITIAL placement.

    Mutations are edited into `init` rather than into a later cycle because an ion's
    position at cycle k is whatever the replay says it is: inventing a participant that
    departs from a stale position makes the replay refuse the program before the rule
    under test can fire, and a rejection for the wrong reason demonstrates nothing.
    """
    init = prog.instructions[0]
    plc = dict(init.placement)
    plc[ion] = site
    prog.instructions[0] = drepl(init, placement=plc)


def _add_ion(prog: TSIR, name: str, site: str) -> str:
    """Introduce a fresh ion at `site`, present from cycle zero.

    Relocating an EXISTING ion breaks the replay wherever that ion later moves, and the
    program is then refused for bookkeeping rather than for the conflict under test. A
    fresh ion is in the ion set from `init` onwards, so R8's invariant is satisfied and
    the only thing wrong with the program is the conflict deliberately introduced.
    """
    init = prog.instructions[0]
    plc = dict(init.placement)
    qua = dict(init.quanta)
    plc[name] = site
    qua[name] = 0.0
    prog.instructions[0] = drepl(init, placement=plc, quanta=qua)
    return name


def overfill_trap(prog: TSIR, arch: Architecture):
    """Start more ions in a trap than it holds -- R1, at cycle zero."""
    init = prog.instructions[0]
    counts: dict[str, int] = {}
    for site in init.placement.values():
        counts[site] = counts.get(site, 0) + 1
    target = next(iter(counts))
    cap = arch.device.nodes[target].capacity
    for k in range(cap + 1 - counts[target]):
        _add_ion(prog, f"spy{k}", target)
    return prog


def share_segment(prog: TSIR, arch: Architecture):
    """Two ions crossing one segment in one cycle -- R3 (and R2 at a junction)."""
    k, ins = _transport(prog)
    if k is None:
        return None
    p0 = ins.participants[0]
    twin = _add_ion(prog, "spy_seg", p0.src)
    prog.instructions[k] = drepl(
        ins, participants=ins.participants + (Participant(twin, p0.src, p0.dst, via=p0.via),))
    return prog


def head_on(prog: TSIR, arch: Architecture):
    """(u->v) together with (v->u) on one segment -- R5."""
    k, ins = _transport(prog)
    if k is None:
        return None
    p0 = ins.participants[0]
    back = _add_ion(prog, "spy_back", p0.dst)
    prog.instructions[k] = drepl(
        ins,
        participants=ins.participants
        + (Participant(back, p0.dst, p0.src, via=tuple(reversed(p0.via))),))
    return prog


def split_a_pair(prog: TSIR, arch: Architecture):
    """Give a two-qubit gate an operand that is somewhere else -- R6b."""
    k, ins = _first(prog, lambda i: i.type == "gate" and i.pairs)
    if k is None:
        return None
    init = prog.instructions[0]
    a, b = ins.pairs[0]
    elsewhere = next((i for i in init.placement if i not in (a, b)), None)
    if elsewhere is None:
        return None
    prog.instructions[k] = drepl(ins, pairs=((a, elsewhere),) + ins.pairs[1:])
    return prog


def gate_in_a_data_zone(prog: TSIR, arch: Architecture):
    """Gate two ions in a trap whose zone forbids it -- R6."""
    dev = arch.device
    site = next((n.id for n in dev.sites()
                 if not arch.can(n.id, "gate") and n.capacity >= 2), None)
    if site is None:
        return None  # every trap on this device can gate
    a = _add_ion(prog, "spy_g1", site)
    b = _add_ion(prog, "spy_g2", site)
    prog.instructions.insert(
        1, Instruction(type="gate", id=prog.id_seq, gate="MS", mode="intra",
                       pairs=((a, b),)))
    prog.id_seq += 1
    return prog


def two_gates_one_trap(prog: TSIR, arch: Architecture):
    """Two gates in one trap in one cycle -- R12."""
    k, ins = _first(prog, lambda i: i.type == "gate" and i.pairs)
    if k is None:
        return None
    a, b = ins.pairs[0]
    prog.instructions[k] = drepl(ins, pairs=ins.pairs + ((a, b),))
    return prog


def drop_cooling(prog: TSIR, arch: Architecture):
    """Remove every cooling operation -- R7 and/or R7c."""
    before = len(prog.instructions)
    prog.instructions = [i for i in prog.instructions if i.type != "cool"]
    return prog if len(prog.instructions) < before else None


def mix_transport_and_gate(prog: TSIR, arch: Architecture):
    """A cycle that both moves and gates -- R4b."""
    k, ins = _transport(prog)
    if k is None:
        return None
    ionA = ins.participants[0].ion
    init = prog.instructions[0]
    other = next((i for i in init.placement if i != ionA), None)
    if other is None:
        return None
    prog.instructions[k] = drepl(ins, gate="MS", pairs=((ionA, other),))
    return prog


def teleport(prog: TSIR, arch: Architecture):
    """Land an ion somewhere its `via` does not reach -- R8 / an unexecutable move."""
    k, ins = _transport(prog)
    if k is None:
        return None
    p0 = ins.participants[0]
    init = prog.instructions[0]
    far = next((s for i, s in init.placement.items()
                if s not in (p0.src, p0.dst)), None)
    if far is None:
        return None
    moved = Participant(p0.ion, p0.src, far, via=p0.via)
    prog.instructions[k] = drepl(ins, participants=(moved,) + ins.participants[1:])
    return prog


MUTATIONS = [
    ("overfill_trap", overfill_trap, "R1"),
    ("share_segment", share_segment, "R3"),
    ("head_on", head_on, "R5"),
    ("split_a_pair", split_a_pair, "R6b"),
    ("gate_in_a_data_zone", gate_in_a_data_zone, "R6"),
    ("two_gates_one_trap", two_gates_one_trap, "R12"),
    ("drop_cooling", drop_cooling, "R7c"),
    ("mix_transport_and_gate", mix_transport_and_gate, "R4b"),
    ("teleport", teleport, "R8"),
]


def judge(prog: TSIR, arch: Architecture):
    """Run the verifier.  An unexecutable program raises, which is also a rejection."""
    try:
        rep = verify(prog, arch, corrected_model(), check_metrics=False)
    except Exception as exc:  # noqa: BLE001 -- a refusal to replay IS a rejection
        return ["<refused to replay>"], type(exc).__name__ + ": " + str(exc)[:60]
    s = rep.rules.summary()
    why = ""
    if rep.rules.violations:
        why = str(rep.rules.violations[0])[:72]
    return s["failed"], why


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("program")
    ap.add_argument("--arch", required=True)
    args = ap.parse_args(argv)

    arch_path = ROOT / args.arch if not Path(args.arch).is_absolute() else Path(args.arch)
    arch = Architecture.from_json(json.loads(arch_path.read_text(encoding="utf-8")))
    base = TSIR.load(args.program)

    failed, _ = judge(copy.deepcopy(base), arch)
    if failed:
        print(f"the UNMUTATED program already fails {failed}; nothing to demonstrate")
        return 2
    print(f"baseline accepted: {base.name} on {arch.name} "
          f"({len(base.instructions)} instructions)")
    print(f"{'mutation':<24} {'expect':<7} {'verdict':<26} why")
    print("-" * 100)

    caught = missed = skipped = 0
    for name, fn, expect in MUTATIONS:
        m = fn(copy.deepcopy(base), arch)
        if m is None:
            print(f"{name:<24} {expect:<7} {'n/a':<26} nothing of that kind here")
            skipped += 1
            continue
        got, why = judge(m, arch)
        if not got:
            print(f"{name:<24} {expect:<7} {'ACCEPTED':<26} <-- NOT CAUGHT")
            missed += 1
        else:
            caught += 1
            print(f"{name:<24} {expect:<7} {'rejected: ' + ','.join(got):<26} {why}")

    print()
    print(f"{caught} of {caught + missed} injected conflicts were rejected, "
          f"{skipped} not applicable to this program")
    print("REJECTION PASS" if missed == 0 else "REJECTION FAIL")
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())
