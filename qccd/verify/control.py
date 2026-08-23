"""What hardware is executing right now.

The animation shows ions moving.  This says what is *driving* them: which control
channels are carrying a waveform this machine cycle, how many sites each one spans, how
many of those are following it, how many are being held out by their switch, and whether
the wiring can actually produce the cycle at all.

The load-bearing decision is that a `ControlRecord` is derived from the *same*
`CycleView` object `r4_drivable` judges, through the *same* `ControlPlane.drivable()`
call, collected through `replay(on_cycle=...)` in the same replay.  The panel and the
verifier cannot disagree, because there is only one computation.

Three properties keep it cheap enough to ship inside a page:

**It carries no ids.**  Not site ids, not ion names -- the frame already has those and
the page is animating them.  Keeping them out is what makes the dedupe work: with ids in
the key `grid9x9` produced 12 distinct records for 12 cycles; without them, 1.  On the
shipped deck program 1,579 instructions and 3,861 machine cycles collapse to **12
distinct records, 9.4 KB of JSON** (15 once cooling is inserted).

**It is precomputed.**  ~80 us per machine cycle is fine offline and would technically
fit a 16.7 ms frame budget, but there is no reason to pay it: the answer is constant for
the whole duration of a frame.  Ship a table plus one integer per frame; the page does
one array index.

**`feasible` is a tri-state, and silence is not a pass.**  It starts as `None` and only
becomes a bool if at least one named path was actually judged.  Every dock and undock in
the deck program runs on `spur` segments that belong to no loop, so R4d says nothing
about them -- and this record says `not judged`, never `yes`.

What it deliberately does not claim is enumerated per record in `undetermined`, and the
first entry is always PLAN §2's scope boundary: voltages, waveform shape, ramp profile
and DAC bit depth are a field solve over one device's geometry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

from ..arch.device import Architecture
from ..ir.tsir import TSIR, Instruction
from .replay import replay
from .rules import CycleView, action_label, path_actions

__all__ = [
    "ChannelBank", "ControlRecord", "ControlTrace",
    "control_record", "idle_record", "control_trace", "NOTES",
]


# --------------------------------------------------------------------- the notes
#
# Interned: six strings for a whole program, referenced by index.  Each one names
# something this view does NOT determine, because a panel that shows only what it knows
# reads as a claim about everything it does not.

NOTE_SCOPE = ("voltages, waveform shape, ramp profile, DAC bit depth: PLAN 2 puts them "
              "out of scope -- they are a field solve over one device's geometry")
NOTE_ROLES = ("which role's channels carry a given movement: attribution is by name "
              "only -- a 'junction' channel is counted engaged when the cycle crosses a "
              "junction node, and no finer claim is made")
NOTE_OFFPATH = ("drivability of {n} of {m} move(s): they run on a segment that belongs "
                "to no named path, so there is no conveyor direction to compare and R4d "
                "is silent -- which is not a pass")
NOTE_CROSS = ("cross-path drivability: whether one waveform advances an ion on each of "
              "two paths alike depends on this device's electrode layout")
NOTE_OPTICAL = ("the pathway that drives this cycle: the control plane models DC "
                "transport electrodes only, and R4b keeps the two pathways apart")
NOTE_UNDECLARED = ("everything: {arch} declares no control.channels, so no claim is "
                   "made about what drives this cycle")

NOTES = (NOTE_SCOPE, NOTE_ROLES, NOTE_OFFPATH, NOTE_CROSS, NOTE_OPTICAL,
         NOTE_UNDECLARED)


# ------------------------------------------------------------------- the records


@dataclass(frozen=True, slots=True)
class ChannelBank:
    """Channels doing the identical thing this cycle, collapsed to one row.

    Under `broadcast` this turns 32 identical rows into 3; under `direct` it turns 128
    into 3.  The reader wants "12 linear_h channels, each driving 168 sites, 144 of them
    following", not twelve copies of it.
    """

    role: str            # ChannelGroup.role: "linear_h" | "linear_v" | "junction" | ...
    n: int               # how many channels are in this bank
    fanout: int          # sites each one drives
    acting: int          # sites on each one that participate this cycle
    action: str          # the one waveform they all carry ("L0:+1" | "class:dock")
    verified: bool       # True iff the action came from path geometry (R4d judged it)
    ids: tuple[str, ...] = ()     # stable ChannelGroup ids; only when with_ids=True

    def to_json(self) -> list:
        row = [self.role, self.n, self.fanout, self.acting, self.action,
               1 if self.verified else 0]
        if self.ids:
            row.append(list(self.ids))
        return row


@dataclass(frozen=True, slots=True)
class ControlRecord:
    """What the control hardware is doing during one machine cycle."""

    # --- identity / provenance (the future editor's click handles) ---------
    instr_id: int                 # Instruction.id -- the handle, same as listing.Line.id
    hop: int                      # which sub-cycle of a multi-hop template
    hops: int                     # how many sub-cycles this instruction expands to
    type: str                     # Instruction.type
    cls: str | None               # SIMD class id -- the declared action's id
    mode: str | None              # intra | inter (R4b)

    # --- what is driving --------------------------------------------------
    driver: str                   # "transport" | "optical" | "idle" | "undeclared"
    action: str                   # the one action, human form
    action_spec: Mapping          # arch.simd_classes[cls] verbatim -- the DECLARATION
    banks: tuple[ChannelBank, ...]
    channels_engaged: int
    channels_total: int           # plane.n_shared_channels

    # --- the serialization penalty, made countable ------------------------
    sites_acting: int             # sites in motion (transport) or zones lit (optical)
    sites_held: int               # sites on an ENGAGED channel that opt out via switch
    sites_total: int              # plane.n_sites
    switch_closed: int            # sites whose switch closes to follow their channel
    switch_elements: int          # switch_closed * electrodes_per_site * 2 (deck p.19)
    switch_elements_total: int    # plane.n_switches -- ALL electrodes, compensation too

    # --- the verdict, and its limits --------------------------------------
    feasible: bool | None         # None = NOT JUDGED; never conflate with True
    problems: tuple[str, ...] = ()      # verbatim ControlPlane.drivable() strings
    undetermined: tuple[str, ...] = ()  # what this view does not claim

    @property
    def duty(self) -> float:
        """The fraction of the array in motion.  The WISE trade, per cycle."""
        return (self.sites_acting / self.sites_total) if self.sites_total else 0.0

    def shape_key(self) -> str:
        """Everything except the ids and which instruction this was.

        Two cycles with the same key are the same control state, and the trace ships
        one copy.  Excluding site ids is what makes that collapse 3,861 cycles to 12.
        """
        return "|".join([
            self.type, str(self.cls), str(self.mode), self.driver, self.action,
            ";".join(f"{b.role}:{b.n}:{b.fanout}:{b.acting}:{b.action}:{int(b.verified)}"
                     for b in self.banks),
            str(self.channels_engaged), str(self.sites_acting), str(self.sites_held),
            str(self.switch_closed), str(self.feasible),
            "".join(self.problems), "".join(self.undetermined),
        ])

    def to_json(self, notes: list[str] | None = None) -> dict:
        """Compact JSON.  Pass `notes` to intern the `undetermined` strings into it."""
        if notes is None:
            und: list = list(self.undetermined)
        else:
            und = []
            for n in self.undetermined:
                if n not in notes:
                    notes.append(n)
                und.append(notes.index(n))
        d: dict = {
            "instr": self.instr_id, "hop": self.hop, "hops": self.hops,
            "type": self.type, "cls": self.cls, "mode": self.mode,
            "driver": self.driver, "action": self.action,
            "banks": [b.to_json() for b in self.banks],
            "channels": [self.channels_engaged, self.channels_total],
            "sites": [self.sites_acting, self.sites_held, self.sites_total],
            "switch": [self.switch_closed, self.switch_elements,
                       self.switch_elements_total],
            "ok": self.feasible, "und": und,
        }
        if self.action_spec:
            d["spec"] = dict(self.action_spec)
        if self.problems:
            d["problems"] = list(self.problems)
        return d


# ------------------------------------------------------------------- derivation


def _banks(engaged, verified_actions: Mapping[str, str], role_order: Sequence[str],
           with_ids: bool, skip: Iterable[str] = ()) -> tuple[ChannelBank, ...]:
    skip = set(skip)
    buckets: dict[tuple, list[str]] = {}
    for e in engaged:
        if e.group.id in skip:
            continue
        action = e.actions[0] if len(e.actions) == 1 else " / ".join(e.actions)
        key = (e.group.role, len(e.group.sites), e.acting, action,
               action in verified_actions)
        buckets.setdefault(key, []).append(e.group.id)
    order = {r: i for i, r in enumerate(role_order)}
    rows = [ChannelBank(role=k[0], n=len(ids), fanout=k[1], acting=k[2], action=k[3],
                        verified=k[4], ids=tuple(ids) if with_ids else ())
            for k, ids in buckets.items()]
    rows.sort(key=lambda b: (-b.acting, order.get(b.role, 99), b.role, b.action))
    return tuple(rows)


def control_record(view: CycleView, *, hop: int = 0, hops: int = 1,
                   attribute_roles: bool = True,
                   with_ids: bool = False) -> ControlRecord:
    """What the control hardware is doing during this one machine cycle."""
    arch, instr = view.arch, view.instr
    plane, dev = arch.control_plane, arch.device
    spec: Mapping = {}
    if instr.cls:
        try:
            spec = arch.simd_class(instr.cls)
        except KeyError:
            spec = {}

    def base(driver, action, **kw):
        return ControlRecord(
            instr_id=instr.id, hop=hop, hops=hops, type=instr.type, cls=instr.cls,
            mode=instr.mode, driver=driver, action=action, action_spec=spec,
            banks=kw.pop("banks", ()), channels_engaged=kw.pop("channels_engaged", 0),
            channels_total=plane.n_shared_channels,
            sites_acting=kw.pop("sites_acting", 0), sites_held=kw.pop("sites_held", 0),
            sites_total=plane.n_sites, switch_closed=kw.pop("switch_closed", 0),
            switch_elements=kw.pop("switch_elements", 0),
            switch_elements_total=plane.n_switches,
            feasible=kw.pop("feasible", None), problems=kw.pop("problems", ()),
            undetermined=kw.pop("undetermined", ()))

    # ------------------------------------------------- no channel map declared
    if not plane.declared or not plane.groups:
        return base("undeclared", instr.cls or instr.type,
                    undetermined=(NOTE_SCOPE,
                                  NOTE_UNDECLARED.format(arch=arch.name)))

    # ------------------------------------------------- non-transport cycles
    if instr.type != "simd" or not view.moves:
        if instr.type in ("gate", "measure", "reset", "cool"):
            if instr.type == "cool":
                ions = instr.ions or tuple(view.pos_before)
                lit = {view.pos_before[i] for i in ions if i in view.pos_before}
                action = "COOL (broadcast)" if instr.broadcast else "COOL"
            else:
                lit = set(view.gate_sites())
                action = instr.gate or instr.type.upper()
            return base("optical", action, sites_acting=len(lit),
                        undetermined=(NOTE_SCOPE, NOTE_OPTICAL))
        return base("idle", instr.type, undetermined=(NOTE_SCOPE,))

    # ------------------------------------------------- transport cycles
    paths = path_actions(view)
    verified: dict[str, str] = {}
    for loop, acts in paths.items():
        for site, d in acts.items():
            verified[site] = action_label(loop, d)

    actions: dict[str, str] = {}
    offpath = 0
    for m in view.moves:
        if dev.nodes[m.src].kind != "site":
            continue
        if m.src in verified:
            actions[m.src] = verified[m.src]
        else:
            actions[m.src] = f"class:{instr.cls}"
            offpath += 1

    # the verdict, per path, exactly as R4d scopes it.  `feasible` stays None unless a
    # named path was actually judged: a dock is not "drivable: yes", it is unjudged.
    feasible: bool | None = None
    problems: list[str] = []
    for loop, deltas in sorted(paths.items()):
        labels = {s: action_label(loop, d) for s, d in deltas.items()}
        ok, probs = plane.drivable(labels)
        feasible = ok if feasible is None else (feasible and ok)
        problems += [f"on path {loop!r}: {p}" for p in probs]

    engaged = plane.engagement(actions)
    skip: set[str] = set()
    if attribute_roles:
        # the only inference in the record, and it is declared as such: a channel whose
        # role names the junction electrodes is counted engaged only when the cycle
        # actually crosses a node of degree >= 3.  It earns its place -- on ring144 an
        # odd-even sort between rail traps engages 24 of 32 channels where a rotation
        # across the 24 degree-3 rail nodes engages all 32.
        crosses = any(dev.degree(m.dst) >= 3 or dev.degree(m.src) >= 3
                      for m in view.moves)
        if not crosses:
            skip = {e.group.id for e in engaged if "junction" in e.group.role}
    engaged_ids = [e.group.id for e in engaged if e.group.id not in skip]

    acting = len(actions)
    held = max(0, plane.covered_sites(engaged_ids) - acting)
    role_order = []
    for g in plane.groups:
        if g.role not in role_order:
            role_order.append(g.role)

    und = [NOTE_SCOPE]
    if attribute_roles:
        und.append(NOTE_ROLES)
    if offpath:
        und.append(NOTE_OFFPATH.format(n=offpath, m=len(actions)))
    if len(paths) > 1:
        und.append(NOTE_CROSS)

    action = (next(iter(verified.values())) if len(set(verified.values())) == 1
              and not offpath else f"class:{instr.cls}")
    return base("transport", action,
                banks=_banks(engaged, verified, role_order, with_ids, skip),
                channels_engaged=len(engaged_ids), sites_acting=acting,
                sites_held=held, switch_closed=acting,
                switch_elements=acting * plane.electrodes_per_site * 2,
                feasible=feasible, problems=tuple(problems),
                undetermined=tuple(und))


def idle_record(instr: Instruction, arch: Architecture) -> ControlRecord:
    """`init` / `barrier`: the replay builds no `CycleView`, so synthesize one record
    so that every instruction has exactly one and `frames[i].ctl` is always valid."""
    plane = arch.control_plane
    declared = plane.declared and bool(plane.groups)
    return ControlRecord(
        instr_id=instr.id, hop=0, hops=1, type=instr.type, cls=instr.cls,
        mode=instr.mode, driver="idle" if declared else "undeclared",
        action=instr.type, action_spec={}, banks=(), channels_engaged=0,
        channels_total=plane.n_shared_channels, sites_acting=0, sites_held=0,
        sites_total=plane.n_sites, switch_closed=0, switch_elements=0,
        switch_elements_total=plane.n_switches, feasible=None,
        undetermined=(NOTE_SCOPE,) if declared
                     else (NOTE_SCOPE, NOTE_UNDECLARED.format(arch=arch.name)))


# ---------------------------------------------------------------------- trace


@dataclass
class ControlTrace:
    """One record per instruction, plus the deduplicated table the page ships."""

    records: list[ControlRecord] = field(default_factory=list)
    table: list[ControlRecord] = field(default_factory=list)
    index: list[int] = field(default_factory=list)   # instruction position -> table slot
    notes: list[str] = field(default_factory=list)
    varies: list[int] = field(default_factory=list)  # instr ids whose hops disagreed

    def for_instruction(self, instr_id: int) -> ControlRecord | None:
        for r in self.records:
            if r.instr_id == instr_id:
                return r
        return None

    def by_id(self) -> dict[int, ControlRecord]:
        return {r.instr_id: r for r in self.records}

    def to_json(self) -> dict:
        notes: list[str] = []
        table = [r.to_json(notes) for r in self.table]
        return {"records": table, "index": list(self.index), "notes": notes,
                "varies": list(self.varies)}

    def duty_rollup(self) -> dict:
        """Where the machine's cycles go, by how much of the array is in motion.

        This is the WISE argument counted rather than asserted: on the deck program
        69% of cycles move >50% of the array (rigid rotation) and 20% move under 5%
        (dock/undock, 164-167 sites held out by their switch).
        """
        out = {"busy": 0, "sparse": 0, "optical": 0, "idle": 0, "cycles": 0,
               "site_cycles": 0, "moving_site_cycles": 0}
        for r in self.records:
            n = max(1, r.hops)
            out["cycles"] += n
            out["site_cycles"] += n * r.sites_total
            out["moving_site_cycles"] += n * r.sites_acting
            if r.driver == "optical":
                out["optical"] += n
            elif r.driver in ("idle", "undeclared") and not r.sites_acting:
                out["idle"] += n
            elif r.duty > 0.5:
                out["busy"] += n
            else:
                out["sparse"] += n
        return out


def control_trace(prog: TSIR, arch: Architecture, model, *, fold: bool = True,
                  attribute_roles: bool = True) -> ControlTrace:
    """Replay `prog` and build one control record per machine cycle.

    With `fold=True` a multi-hop template collapses to its first hop -- exact on every
    program measured (0 of the deck program's 90 multi-hop instructions had hops with
    differing shapes) and never silent: any instruction whose hops disagreed is listed
    in `ControlTrace.varies`.

    This runs its own replay.  A caller that is about to replay anyway should instead
    pass `on_cycle=` to `replay()` and hand the collected records to `assemble_trace`:
    the records then see byte-identical state to the rule pass and nothing is replayed
    twice.
    """
    per_instr: dict[int, list[ControlRecord]] = {}
    order: list[int] = []

    def collect(view: CycleView) -> None:
        iid = view.instr.id
        bucket = per_instr.get(iid)
        if bucket is None:
            bucket = per_instr[iid] = []
            order.append(iid)
        bucket.append(control_record(view, hop=len(bucket),
                                     attribute_roles=attribute_roles))

    replay(prog, arch, model, check_rules=False, keep_cycles=False, on_cycle=collect)
    return assemble_trace(prog, arch, per_instr, fold=fold)


def assemble_trace(prog: TSIR, arch: Architecture,
                   per_instr: Mapping[int, Sequence[ControlRecord]], *,
                   fold: bool = True) -> ControlTrace:
    """Fold collected per-cycle records into one per instruction, and dedupe."""
    from dataclasses import replace as _replace

    trace = ControlTrace()
    seen: dict[str, int] = {}
    for instr in prog.instructions:
        got = list(per_instr.get(instr.id, ()))
        if not got:
            rec = idle_record(instr, arch)
        else:
            hops = len(got)
            keys = {r.shape_key() for r in got}
            if len(keys) > 1:
                trace.varies.append(instr.id)
            rec = _replace(got[0], hops=hops) if fold else got[0]
        trace.records.append(rec)
        key = rec.shape_key()
        slot = seen.get(key)
        if slot is None:
            slot = seen[key] = len(trace.table)
            trace.table.append(rec)
        trace.index.append(slot)
    notes: list[str] = []
    for r in trace.table:
        for n in r.undetermined:
            if n not in notes:
                notes.append(n)
    trace.notes = notes
    return trace
