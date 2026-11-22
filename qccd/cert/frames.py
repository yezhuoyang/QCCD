"""Event frames of a compiled execution, and what a rule observes on each.

QCCDCert checks every instruction of its target code on one *event frame*

    F = (H, pos⁻, pos⁺, u, t, δ)

-- the device, the placements before and after, the instruction, its start time and its
duration -- and a rule is an assertion over the counts of one frame (paper, Section 2.3):

    N⁻(s), N⁺(s)   ions at site s before / after the event
    Nᵗʳ(s)         ions that use s during it: residents before or after, plus every move of
                   the event whose path passes through s
    M(p), M(c)     moves along path p / on transport channel c;  W(c) distinct waveforms on c
    P(s)           optical actions at s;  n_optical, n_R, n_MS, n_move, n_readout, n_pump, n_latch
    t, δ           start time and duration

This module recomputes those counts *for display*, with the definitions of the compiler
(`QCCD.transitOccupancy`, `Frame.pathUse`, `Frame.channelWaveforms`, `evalBase`).  The
compiler decides the rules itself, in Lean, before it returns any target code; `evaluate`
here only shows a reader the numbers the decision was made on, and `check_all` asserts that
the display agrees with the compiler's acceptance (a disagreement is a bug in this module).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Frame:
    index: int                 # 0 is initialisation
    instruction: dict | None   # the compiler's instruction, None for F0
    text: str                  # the instruction in the paper's syntax
    before: list[int]          # pos⁻: ion -> site
    after: list[int]           # pos⁺
    start: int                 # t
    duration: int              # δ
    obs: dict = field(default_factory=dict)


class Device:
    """The parts of a hardware file a frame reads."""

    def __init__(self, hardware: dict):
        dev = hardware["device"]
        base = dev["base"]
        geo = base["geometry"]
        self.hardware = hardware
        self.sites = geo["sites"]
        self.paths = geo["paths"]
        self.path_duration = base["pathDurations"]
        self.calibrations = base["calibrations"]
        self.profiles = base.get("broadcasts", [])
        self.readouts = dev.get("readouts", [])
        self.pumps = dev.get("pumps", [])
        self.controller = (dev.get("controller") or {}).get("duration", 0)
        chans: list[int] = []
        for p in self.paths:
            if p["channel"] not in chans:
                chans.append(p["channel"])
        self.channels = chans

    # ------------------------------------------------------------------ instruction facts
    def moves(self, ins: dict | None) -> list[dict]:
        if not ins or "transport" not in ins:
            return []
        return ins["transport"]["moves"]

    def duration(self, ins: dict) -> int:
        kind, b = next(iter(ins.items()))
        if kind == "transport":
            return max((self.path_duration[m["path"]] for m in b["moves"]), default=0)
        if kind == "pulse":
            return self.calibrations[b["calibration"]]["duration"]
        if kind == "broadcast":
            return self.profiles[b["profile"]]["duration"]
        if kind == "readout":
            return self.readouts[b["calibration"]]["duration"]
        if kind == "pump":
            return self.pumps[b["calibration"]]["duration"]
        if kind == "latch":
            return self.controller
        if kind == "idle":
            return b["duration"]
        return 0  # barrier

    def pulse_sites(self, ins: dict | None) -> list[int]:
        if not ins:
            return []
        kind, b = next(iter(ins.items()))
        if kind == "pulse":
            return [self.calibrations[b["calibration"]]["site"]]
        if kind == "broadcast":
            return [self.calibrations[c]["site"] for c in b.get("calibrations", [])]
        if kind == "readout":
            return [self.readouts[b["calibration"]]["site"]]
        if kind == "pump":
            return [self.pumps[b["calibration"]]["site"]]
        return []

    def after(self, pos: list[int], ins: dict) -> list[int]:
        new = list(pos)
        for m in self.moves(ins):
            new[m["ion"]] = self.paths[m["path"]]["dst"]
        return new


def counts(dev: Device, f: Frame) -> dict:
    """Every observation of one frame, keyed as the paper writes them."""
    ins = f.instruction
    kind = next(iter(ins)) if ins else None
    ions = range(max(len(f.before), len(f.after)))
    moves = dev.moves(ins)
    n_sites = len(dev.sites)
    o: dict = {"t": f.start, "delta": f.duration, "end": f.start + f.duration}
    o["N-"] = [f.before.count(s) for s in range(n_sites)]
    o["N+"] = [f.after.count(s) for s in range(n_sites)]
    o["Ntr"] = [sum(1 for q in ions if (q < len(f.before) and f.before[q] == s)
                    or (q < len(f.after) and f.after[q] == s))
                + sum(1 for m in moves if s in dev.paths[m["path"]]["via"]) for s in range(n_sites)]
    o["cap"] = [s["capacity"] for s in dev.sites]
    o["M_path"] = [sum(1 for m in moves if m["path"] == p) for p in range(len(dev.paths))]
    waves = {c: [dev.paths[m["path"]]["waveform"] for m in moves if dev.paths[m["path"]]["channel"] == c]
             for c in dev.channels}
    o["M_chan"] = {c: len(w) for c, w in waves.items()}
    o["W"] = {c: len(set(w)) for c, w in waves.items()}
    ps = dev.pulse_sites(ins)
    o["P"] = [ps.count(s) for s in range(n_sites)]
    o["n_move"] = len(moves)
    if kind in ("pulse", "readout", "pump"):
        o["n_optical"] = 1
    elif kind == "broadcast":
        o["n_optical"] = len(ins["broadcast"]["pulses"])
    else:
        o["n_optical"] = 0
    o["n_R"] = (1 if kind == "pulse" and "r" in ins["pulse"]["operation"]["pulse"] else 0) + \
        (len(ins["broadcast"]["pulses"]) if kind == "broadcast" else 0)
    o["n_MS"] = 1 if kind == "pulse" and "ms" in ins["pulse"]["operation"]["pulse"] else 0
    o["n_readout"] = 1 if kind == "readout" else 0
    o["n_pump"] = 1 if kind == "pump" else 0
    o["n_latch"] = 1 if kind == "latch" else 0
    return o


def frames(hardware: dict, execution: dict, starts: list[int] | None = None,
           printer=None) -> list[Frame]:
    """F0 (initialisation, t = δ = 0, no activity) and one frame per instruction.

    `starts` are the timeline's start times (the parallel schedule); without them the frames
    run one after another, as the timing stage checks them.  Placements always follow the
    instructions in program order (the theorem's pos₀ … pos_m)."""
    dev = Device(hardware)
    pos = list(execution["initial"])
    out = [Frame(0, None, printer.initial(pos) if printer else "init", pos, pos, 0, 0)]
    clock = 0
    for i, ins in enumerate(execution["instructions"]):
        d = dev.duration(ins)
        t = starts[i] if starts is not None else clock
        new = dev.after(pos, ins)
        out.append(Frame(i + 1, ins, printer.token(ins) if printer else next(iter(ins)), pos, new, t, d))
        pos = new
        clock += d
    for f in out:
        f.obs = counts(dev, f)
    return out


# -------------------------------------------------------------------------- rule values
class Eval:
    """Evaluates a rule (the compiler's formula JSON) on one frame, as `Formula.denote` does."""

    def __init__(self, dev: Device):
        self.dev = dev

    def domain(self, d: str) -> list[int]:
        return {"sites": list(range(len(self.dev.sites))),
                "paths": list(range(len(self.dev.paths))),
                "channels": list(self.dev.channels)}[d]

    def term(self, t: dict, env: dict, o: dict) -> int:
        kind, b = next(iter(t.items()))
        if kind == "constant":
            return b["value"]
        if kind == "atom":
            e = b["expression"]
            if isinstance(e, dict) and "base" in e:
                e = e["base"]["expression"]
            key = {"start": "t", "duration": "delta", "finish": "end", "moveCount": "n_move",
                   "pulseCount": "n_optical", "rCount": "n_R", "msCount": "n_MS",
                   "readoutCount": "n_readout", "pumpCount": "n_pump", "latchCount": "n_latch"}
            if isinstance(e, str):
                return o[key[e]]
            k, v = next(iter(e.items()))
            if k == "const":
                return v
            raise ValueError(f"unsupported atom {e}")
        if kind == "observe":
            obs, ref = b["observation"], b["reference"]
            r = env[ref["var"]["name"]] if "var" in ref else ref["id"]["value"]
            if isinstance(obs, dict):
                return o[{"before": "N-", "after": "N+", "during": "Ntr"}[obs["occupancy"]["phase"]]][r]
            if obs == "capacity":
                return o["cap"][r]
            if obs == "pulsesAt":
                return o["P"][r]
            if obs == "pathUse":
                return o["M_path"][r]
            if obs == "channelUse":
                return o["M_chan"].get(r, 0)
            if obs == "channelWaves":
                return o["W"].get(r, 0)
        if kind == "add":
            return self.term(b["lhs"], env, o) + self.term(b["rhs"], env, o)
        if kind == "sub":
            return max(0, self.term(b["lhs"], env, o) - self.term(b["rhs"], env, o))
        raise ValueError(f"unknown term {kind}")

    def formula(self, f, env: dict, o: dict) -> bool:
        if isinstance(f, str):
            return f == "truth"
        kind, b = next(iter(f.items()))
        if kind == "le":
            return self.term(b["lhs"], env, o) <= self.term(b["rhs"], env, o)
        if kind == "eq":
            return self.term(b["lhs"], env, o) == self.term(b["rhs"], env, o)
        if kind == "and":
            return self.formula(b["lhs"], env, o) and self.formula(b["rhs"], env, o)
        if kind == "or":
            return self.formula(b["lhs"], env, o) or self.formula(b["rhs"], env, o)
        if kind == "implies":
            return (not self.formula(b["lhs"], env, o)) or self.formula(b["rhs"], env, o)
        if kind == "not":
            return not self.formula(b["body"], env, o)
        if kind in ("all", "any"):
            vals = [self.formula(b["body"], {**env, b["name"]: v}, o) for v in self.domain(b["domain"])]
            return all(vals) if kind == "all" else any(vals)
        if kind in ("truth", "falsity"):
            return kind == "truth"
        raise ValueError(f"unknown formula {kind}")


def evaluate(hardware: dict, rules: list[dict], fs: list[Frame]) -> list[dict[str, bool]]:
    """{rule name: holds} per frame."""
    ev = Eval(Device(hardware))
    return [{r["name"]: ev.formula(r["formula"], {}, f.obs) for r in rules} for f in fs]


def check_all(hardware: dict, rules: list[dict], fs: list[Frame]) -> None:
    """Raise if the display says a rule fails on a frame of target code the compiler accepted."""
    for f, vals in zip(fs, evaluate(hardware, rules, fs)):
        bad = [n for n, ok in vals.items() if not ok]
        if bad:
            raise AssertionError(f"frame F{f.index} ({f.text}): the display evaluates {bad} as false, "
                                 f"but the compiler accepted this target code")
        for s, (n, c) in enumerate(zip(f.obs["Ntr"], f.obs["cap"])):
            if n > c:
                raise AssertionError(f"frame F{f.index}: Ntr({s}) = {n} > cap = {c} (core capacity check)")
