"""The words the WISE and Cyclone rule sets are written in (the transport deck's S-rules).

A WISE step's pairs are the device's own (`qccd.arch.wise.Zones`) and a Cyclone step's
one trap round its ring, so each word reads the programme's step against the device, never a
claim of the programme's.  Each answers for one step, or for the device, as the C rules' words
do; `qccd/ruleset/library/wise.rules` and `cyclone-deck.rules` say what must hold.
"""

from __future__ import annotations

from collections import Counter

from .model import Site, Step
from .vocabulary import Ctx, EvalError, NotApplicable, term

__all__: list[str] = []


def _params(ctx: Ctx) -> dict:
    arch = ctx.hw.arch
    dev = getattr(arch, "device", None)
    return dict(getattr(dev, "params", None) or {})


def _step(ctx: Ctx, k, fn: str) -> dict:
    ctx.need("programme", "steps")
    if not isinstance(k, Step):
        raise EvalError(f"{fn} needs a step, got {k!r}")
    return ctx.exe.steps[k.id]


def _wise_pairs(ctx: Ctx) -> dict[tuple[str, str], set[tuple[str, str]]]:
    """(pair kind, half) -> the moves that kind of step may make, from the device."""
    def make():
        from ..arch.wise import Zones
        out: dict = {}
        for kind, edges in Zones(ctx.hw.arch).edges.items():
            out[(kind, "a")] = {(e.v, e.u) for e in edges}
            out[(kind, "b")] = {(e.u, e.v) for e in edges}
        return out
    return ctx.once("wise_pairs", make)


def _half(cls: str | None) -> tuple[str, str] | None:
    parts = str(cls or "").split("_")
    return (parts[1], parts[2]) if len(parts) == 3 and parts[0] == "wise" and parts[2] in "ab" else None


@term("wise_device()", "true or false", "Whether the device is a WISE chip: its drawing says so, as "
      "the device's generator parameters do.", "hardware")
def _wise_device(ctx: Ctx):
    return _params(ctx).get("family") == "wise"


@term("cyclone_device()", "true or false", "Whether the device is a Cyclone ring: its drawing says "
      "so, as the device's generator parameters do.", "hardware")
def _cyclone_device(ctx: Ctx):
    return _params(ctx).get("family") == "cyclone"


@term("one_parity(k)", "true or false", "Whether WISE step k plays one kind of pair at one parity "
      "in one direction: its class names them, each of its moves is one of the device's pairs of "
      "that kind, and no zone takes part twice.", "execution", kinds=("programme",))
def _one_parity(ctx: Ctx, k):
    ins = _step(ctx, k, "one_parity")["instruction"]
    key = _half(ins.cls)
    if key is None:
        return False
    allowed = _wise_pairs(ctx).get(key, set())
    seen: set[str] = set()
    for p in ins.participants:
        if (p.src, p.dst) not in allowed or p.src in seen or p.dst in seen:
            return False
        seen |= {p.src, p.dst}
    return True


@term("one_kind(k)", "true or false", "Whether step k is one kind of operation across the chip: a "
      "gate step's physical pulses all the same gate at the same angle.  A frame change is not a "
      "pulse.", "execution", kinds=("programme",))
def _one_kind(ctx: Ctx, k):
    ins = _step(ctx, k, "one_kind")["instruction"]
    if ins.type != "gate" or ins.gate in ("VZ", "I"):
        return True
    return len({tuple(p) for p in (ins.params or ())}) <= 1


@term("pairs_only_to_gate_or_split(k)", "true or false", "Whether, once step k is over, a zone that "
      "holds two ions is on its way to a gate or a split: step k is a swap step's half (one parity "
      "merging or splitting while another waits), their two-ion gate, or their cooling, and any "
      "other step leaves every zone holding one.", "execution", kinds=("programme",))
def _pairs_only(ctx: Ctx, k):
    st = _step(ctx, k, "pairs_only_to_gate_or_split")
    if not st["holds_two"]:
        return True
    ins = st["instruction"]
    return _half(ins.cls) is not None or ins.type == "cool" or (ins.type == "gate" and ins.gate == "MS")


@term("shim_opens(k)", "true or false", "Whether swap step k, if it opens a reconfiguration, comes "
      "straight after the layer that charges its shims: a step with no pulse, an identity.  A step "
      "that merges for a gate is that gate's layer, and carries its charge in the gate's price.",
      "execution", kinds=("programme",))
def _shim_opens(ctx: Ctx, k):
    steps = ctx.exe.steps
    if (_step(ctx, k, "shim_opens")["instruction"].meta or {}).get("kind") != "swap_step":
        return True
    for j in range(k.id - 1, -1, -1):
        ins = steps[j]["instruction"]
        if ins.type == "cool":          # the cooling pass may stand between them
            continue
        return (ins.meta or {}).get("kind") in ("swap_step", "shim_charge")
    return False


@term("shims_in_every_layer()", "true or false", "Whether every layer's price on this device carries "
      "its shim charge: the time of 128 shims at 3 us each, before the pulse.", "hardware")
def _shims(ctx: Ctx):
    from ..arch.wise import SHIM_US
    prim = ctx.hw.arch.primitives.scalars
    return all(float(prim[p]["us"]) >= SHIM_US for p in ("1q_gate", "ms_gate", "measure", "reset"))


@term("ring_device()", "true or false", "Whether the device is one closed ring: every site on the "
      "one loop with two rails, and every junction an L at its corners, of degree 2.", "hardware")
def _ring_device(ctx: Ctx):
    hw = ctx.hw
    if len(hw.loops) != 1:
        return False
    on = set(next(iter(hw.loops.values())))
    return all(n in on for n in hw.nodes) and all(hw.degree[n] == 2 for n in hw.nodes) and not hw.junctions


def _data(ctx: Ctx) -> set[str]:
    got = (ctx.exe.facts.get("program_meta") or {}).get("data_ions")
    if got is None:
        raise NotApplicable("the programme does not say which of its ions are data")
    return set(got)


@term("data_stays(k)", "true or false", "Whether step k moves no data qubit: only ancillas travel.",
      "execution", kinds=("programme",))
def _data_stays(ctx: Ctx, k):
    ins = _step(ctx, k, "data_stays")["instruction"]
    return not ({p.ion for p in ins.participants} & _data(ctx))


@term("lockstep(k)", "true or false", "Whether Cyclone step k is part of a rotation in lockstep: "
      "the unbroken run of transport steps it belongs to moves every ancilla the same number of times, "
      "each time one trap on round the ring in the same direction.  A trap's ancillas leave in turn, "
      "so a rotation may take several steps, and a run may hold several rotations.", "execution", kinds=("programme",))
def _lockstep(ctx: Ctx, k):
    _step(ctx, k, "lockstep")
    steps = ctx.exe.steps
    lo = k.id
    while lo > 0 and steps[lo - 1]["type"] == "simd":
        lo -= 1
    hi = k.id
    while hi + 1 < len(steps) and steps[hi + 1]["type"] == "simd":
        hi += 1
    arch = ctx.hw.arch
    lid = next(iter(arch.device.loops))
    slots = list(arch.device.loop_slots(lid))
    nxt = {s: slots[(i + 1) % len(slots)] for i, s in enumerate(slots)}
    moved: list[str] = []
    for j in range(lo, hi + 1):
        for p in steps[j]["instruction"].participants:
            if nxt.get(p.src) != p.dst:
                return False
            moved.append(p.ion)
    times = Counter(moved)
    return set(times) == {i.id for i in ctx.exe.ions} - _data(ctx) and len(set(times.values())) == 1


@term("traps_within_thirty()", "true or false", "Whether no trap of the ring holds more than 30 ions.",
      "hardware")
def _within(ctx: Ctx):
    from ..arch.cyclone import MAX_IONS_PER_TRAP
    return all(ctx.hw.capacity[s] <= MAX_IONS_PER_TRAP for s in ctx.hw.sites if isinstance(s, Site))
