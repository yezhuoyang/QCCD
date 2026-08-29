"""The control plane: what can be driven, and what must be driven together.

PLAN §2 puts electrode-level waveform synthesis out of scope, and that boundary is right:
the voltages on a pad are a field solve over one device's geometry, they are not portable
between architectures, and the compiler has no use for them. What every QCCD *does* have,
and what every architectural claim in the corpus actually rests on, is one level up:

    which electrodes share a control channel, and therefore which zones must do the
    same thing at the same time.

That map is general. It is the whole content of "broadcasting" (deck p.4: "DC electrodes
in different zones are controlled by the same channel"), it is what makes Cyclone's O(1)
DAC claim meaningful, and it is where R4's "one class, variadic participation" comes from.
So it is modelled structurally here, and the DAC count is *counted* rather than computed
from a formula.

Two kinds of control, and they behave differently
-------------------------------------------------
**Channels** are analog and scarce. Every electrode on a channel sees the same waveform,
so every zone on that channel does the same thing. Adding zones does not add channels --
that is the scaling win, and the cost is that those zones lose independence.

**Switches** are digital and cheap: one bit per site deciding whether it follows its
channel. The deck gives every DC electrode a two-way switch (p.19, 48N switches), and
arXiv:2403.00756 achieves site-dependent operation from a fixed number of analog signals
plus "one digital input per site". A switch buys *opting out*, not doing something else --
which is exactly why JT-SIMD participation is variadic but a class still fixes one
operation and one direction.

So the drivability question has two halves:

    with switches     zones on one channel may participate or idle, but every
                      participant must do the SAME thing
    without switches  a channel's zones are all-or-nothing: they all move, together

What this module deliberately does not model: voltages, waveform shapes, ramp profiles,
DAC bit depth, sample rate, filter response. Those live behind the `(duration, quanta)`
curves the architecture already declares, which is where PLAN §3.2 put them.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from functools import cached_property
from typing import Iterable, Mapping, Sequence

__all__ = ["ControlPlane", "ChannelGroup", "Engagement", "build_control_plane",
           "GROUPINGS", "FRAMES"]

GROUPINGS = ("direct", "broadcast", "row", "column", "row_column", "explicit")

#: In which frame the conveyor electrodes are tiled -- the one thing a channel map
#: cannot be derived from and no device could previously say.
#:
#: ``"path"``   the tiling FOLLOWS the trap axis.  One waveform means "forward one
#:              slot along this path", and it means that identically where the path
#:              bends: H2's curved end zones are ordinary conveyor regions driven by
#:              the same ``{a,b,c}`` broadcast tiling as the straights (2305.03828,
#:              quoted at ``docs/PLAN.md:132``, implemented at
#:              ``qccd/arch/generators.py:404-406``).  This is what ``path_actions``
#:              already assumes, so it is the default: no shipped device changes
#:              verdict without an edit.
#: ``"lab"``    the tiling is FIXED TO THE CHIP AXES.  "move +x" is one waveform and
#:              "move -x" is a different one, so a path that turns needs a channel per
#:              direction it turns into.  R19.
FRAMES = ("path", "lab")


@dataclass(frozen=True)
class ChannelGroup:
    """One analog channel and the sites whose electrodes it drives."""

    id: str
    role: str
    sites: frozenset[str]

    def __len__(self) -> int:
        return len(self.sites)


@dataclass(frozen=True, slots=True)
class Engagement:
    """One channel carrying a waveform this cycle, and who is following it.

    Counts, not members: the per-site map stays the caller's `actions` and nothing is
    copied.  That is what lets a whole program's worth of engagements be deduplicated
    down to a handful of distinct shapes.
    """

    group: "ChannelGroup"
    acting: int                     # sites on this channel that participate
    actions: tuple[str, ...]        # the DISTINCT action signatures among them

    @property
    def fanout(self) -> int:
        """How many sites this one channel drives, participating or not."""
        return len(self.group.sites)

    @property
    def idle(self) -> int:
        """Sites on this channel held out by their switch while it carries a waveform.

        On a broadcast-wired array this is the number that carries the meaning: moving
        one ion engages every channel and holds 167 of 168 sites out.
        """
        return len(self.group.sites) - self.acting

    @property
    def uniform(self) -> bool:
        return len(self.actions) == 1


@dataclass
class ControlPlane:
    """The wiring, as a structure rather than a count."""

    groups: tuple[ChannelGroup, ...] = ()
    switch_per_site: bool = True
    electrodes_per_site: int = 0
    electrodes_per_junction: int = 0
    compensation_per_site: int = 0
    demux: int = 1
    grouping: str = "direct"
    frame: str = "path"
    n_sites: int = 0
    n_junctions: int = 0
    declared: bool = False
    notes: list[str] = field(default_factory=list)

    # ------------------------------------------------------------- resources

    @property
    def n_shared_channels(self) -> int:
        """Analog channels driving the transport electrodes."""
        return len(self.groups)

    @property
    def n_compensation_channels(self) -> int:
        """Compensation electrodes are individually tuned, so they cannot be broadcast;
        a demultiplexer is the only thing that keeps their count down (deck p.20)."""
        total = self.n_sites * self.compensation_per_site
        return math.ceil(total / self.demux) if self.demux else total

    @property
    def n_channels(self) -> int:
        return self.n_shared_channels + self.n_compensation_channels

    @property
    def n_electrodes(self) -> int:
        return (self.n_sites * (self.electrodes_per_site + self.compensation_per_site)
                + self.n_junctions * self.electrodes_per_junction)

    @property
    def n_switches(self) -> int:
        """Every electrode behind a two-way switch, when the device has them."""
        return self.n_electrodes * 2 if self.switch_per_site else 0

    # --------------------------------------------------------------- indexes
    #
    # Built once per plane and cached.  Before these existed, `drivable` walked every
    # channel against every site on every call -- 32 x 168 = 5376 dict probes for a
    # cycle in which one ion moves -- and R4d alone cost 4.9 s of the deck program's
    # 15.4 s replay.

    @cached_property
    def _by_site(self) -> Mapping[str, tuple[str, ...]]:
        """site -> the ids of every channel that drives it."""
        acc: dict[str, list[str]] = {}
        for g in self.groups:
            for s in g.sites:
                acc.setdefault(s, []).append(g.id)
        return {s: tuple(v) for s, v in acc.items()}

    @cached_property
    def _by_id(self) -> Mapping[str, "ChannelGroup"]:
        return {g.id: g for g in self.groups}

    @cached_property
    def _order(self) -> Mapping[str, int]:
        """channel id -> declaration position, so an engagement list is deterministic
        whichever walk `engagement` picks."""
        return {g.id: i for i, g in enumerate(self.groups)}

    @cached_property
    def max_channels_per_site(self) -> int:
        return max((len(v) for v in self._by_site.values()), default=0)

    def channels_of(self, site: str) -> tuple[str, ...]:
        return self._by_site.get(site, ())

    def sites_sharing_with(self, site: str) -> frozenset[str]:
        """Every site that is forced to follow the same waveform as this one."""
        out: set[str] = set()
        by_id = self._by_id
        for cid in self.channels_of(site):
            out |= by_id[cid].sites
        return frozenset(out - {site})

    def engagement(self, actions: Mapping[str, object]) -> tuple[Engagement, ...]:
        """Every channel with at least one participating site, and what those sites do.

        Picks whichever walk is cheaper -- over the channels, or over the acting sites
        through `_by_site` -- so a 32-channel broadcast plane and a 4608-channel direct
        plane both cost O(min(channels, acting x channels_per_site)).  The frozenset
        intersection runs in C, and the common case (every participant doing the same
        thing) skips building the per-channel action set entirely.
        """
        if not actions or not self.groups:
            return ()
        keys = set(actions)
        vals = {str(a) for a in actions.values()}
        single = (next(iter(vals)),) if len(vals) == 1 else None
        if len(keys) * max(1, self.max_channels_per_site) >= len(self.groups):
            candidates: Iterable[ChannelGroup] = self.groups          # broadcast-ish
        else:
            by_id, order = self._by_id, self._order
            candidates = [by_id[c] for c in sorted(
                {c for s in keys for c in self._by_site.get(s, ())},
                key=lambda c: order.get(c, 0))]
        out: list[Engagement] = []
        for g in candidates:
            hit = keys & g.sites
            if not hit:
                continue
            out.append(Engagement(
                g, len(hit),
                single or tuple(sorted({str(actions[s]) for s in hit}))))
        return tuple(out)

    def covered_sites(self, channel_ids: Iterable[str]) -> int:
        """How many distinct sites sit on at least one of these channels.

        Cached by the id-set: the same set recurs on every cycle of a program, so a
        whole replay pays for it once per distinct engagement shape.
        """
        key = frozenset(channel_ids)
        cache = self.__dict__.setdefault("_covered", {})
        got = cache.get(key)
        if got is None:
            by_id = self._by_id
            seen: set[str] = set()
            for cid in key:
                g = by_id.get(cid)
                if g is not None:
                    seen |= g.sites
            got = cache[key] = len(seen)
        return got

    # ------------------------------------------------------------ drivability

    def drivable(
        self, actions: Mapping[str, object],
        engaged: Sequence[Engagement] | None = None,
    ) -> tuple[bool, list[str]]:
        """Can these sites do these things at the same instant?

        `actions` maps a site to a hashable signature of what it does this cycle. A site
        that does nothing is simply absent.

        Two failures are possible, and they are different machines:
        * two sites on one channel doing *different* things -- impossible either way,
          because one channel carries one waveform;
        * a site idling while a channel-mate moves -- impossible only without switches.
        """
        problems: list[str] = []
        for e in (self.engagement(actions) if engaged is None else engaged):
            g = e.group
            if len(e.actions) > 1:
                a, b = sorted(e.actions)[:2]
                problems.append(
                    f"channel {g.id!r} drives {len(g.sites)} sites with one waveform, but "
                    f"they are asked to do {len(e.actions)} different things "
                    f"({a} and {b}); that needs {len(e.actions)} channels")
            elif not self.switch_per_site and e.acting != len(g.sites):
                idle = sorted(g.sites - set(actions))[:4]
                problems.append(
                    f"channel {g.id!r} has no per-site switch, so its {len(g.sites)} "
                    f"sites are all-or-nothing; {e.idle} would have "
                    f"to idle while the rest move (e.g. {idle})")
        return (not problems), problems

    # ------------------------------------------------------------- reporting

    def summary(self) -> dict:
        sizes = sorted({len(g) for g in self.groups})
        return {
            "grouping": self.grouping,
            "frame": self.frame,
            "declared": self.declared,
            "channels": self.n_channels,
            "shared_channels": self.n_shared_channels,
            "compensation_channels": self.n_compensation_channels,
            "electrodes": self.n_electrodes,
            "switches": self.n_switches,
            "switch_per_site": self.switch_per_site,
            "sites_per_channel": sizes,
            "channels_per_site": (
                self.n_shared_channels / self.n_sites if self.n_sites else 0.0),
        }


def build_control_plane(device, control: Mapping) -> ControlPlane:
    """Expand a `control.channels` block against the device's sites.

    An architecture that declares nothing gets an undeclared plane: counts still come out
    (from the aggregate wiring fields, as before) but drivability is not checked, because
    a device that has not said how it is wired has not earned a verdict on what it can do.
    """
    sites = [n.id for n in device.nodes.values() if n.kind == "site"]
    junctions = [n.id for n in device.nodes.values() if n.kind == "junction"]
    wiring = dict(control.get("wiring", {}) or {})
    spec = dict(control.get("channels", {}) or {})

    plane = ControlPlane(
        switch_per_site=bool(spec.get("switch_per_site", True)),
        electrodes_per_site=int(wiring.get("electrodes_per_trap", 0)),
        electrodes_per_junction=int(wiring.get("electrodes_per_junction", 0)),
        compensation_per_site=int(wiring.get("compensation_electrodes_per_trap", 0)),
        demux=int(wiring.get("shim_per_dac", 1) or 1),
        grouping=str(spec.get("grouping", "direct")),
        frame=str(spec.get("frame", "path")),
        n_sites=len(sites),
        n_junctions=len(junctions),
        declared=bool(spec),
    )
    if plane.frame not in FRAMES:
        raise ValueError(
            f"unknown electrode frame {plane.frame!r}; have: {', '.join(FRAMES)}")
    if not spec:
        return plane

    # `roles` is either a list (one channel each) or a mapping role -> how many channels
    # that role needs.  The deck's array is 6 horizontal + 6 vertical linear plus 4
    # junction, and every electrode is driven as a differential pair, which is where its
    # "12*2 = 24" and "4*2 = 8" come from (p.20).
    raw_roles = spec.get("roles", ("linear",))
    if isinstance(raw_roles, Mapping):
        role_counts = {str(k): int(vv) for k, vv in raw_roles.items()}
    else:
        n = int(spec.get("channels_per_role", 1))
        role_counts = {str(r): n for r in raw_roles}
    pair = int(spec.get("differential", 1))
    groups: list[ChannelGroup] = []

    def add(role: str, key: str, members: Iterable[str]) -> None:
        members = frozenset(members)
        if not members:
            return
        for k in range(role_counts.get(role, 1) * pair):
            groups.append(ChannelGroup(f"{role}.{key}.{k}", role, members))

    roles = list(role_counts)
    if plane.grouping == "broadcast":
        for role in roles:
            add(role, "all", sites)
        plane.notes.append(
            f"broadcast: {sum(role_counts.values()) * pair} channel(s) drive all "
            f"{len(sites)} sites, so the channel count is constant in array size")
    elif plane.grouping == "direct":
        for role in roles:
            for s in sites:
                add(role, s, [s])
        plane.notes.append("direct: one channel per site per role, so channels are O(sites)")
    elif plane.grouping in ("row", "column", "row_column"):
        axes = {"row": (1,), "column": (0,), "row_column": (0, 1)}[plane.grouping]
        for axis in axes:
            buckets: dict[float, list[str]] = {}
            for s in sites:
                buckets.setdefault(round(device.nodes[s].pos[axis], 6), []).append(s)
            tag = "r" if axis == 1 else "c"
            for role in roles:
                for key, members in sorted(buckets.items()):
                    add(role, f"{tag}{key}", members)
        plane.notes.append(
            f"{plane.grouping}: channels shared along an axis, so sites in one line move "
            f"together and lines are independent")
    elif plane.grouping == "explicit":
        for entry in spec.get("explicit", ()):
            groups.append(ChannelGroup(
                str(entry["id"]), str(entry.get("role", "linear")),
                frozenset(entry["drives"])))
        plane.notes.append("explicit: the channel map is given site by site")
    else:
        raise ValueError(
            f"unknown channel grouping {plane.grouping!r}; have: {', '.join(GROUPINGS)}")

    plane.groups = tuple(groups)
    if plane.frame == "lab":
        plane.notes.append(
            "lab frame: the electrode tiling is fixed to the chip axes, so '+x' and "
            "'-x' are different waveforms and a path that turns needs one channel "
            "group per direction it turns into (R19)")
    else:
        plane.notes.append(
            "path frame: the electrode tiling follows the trap axis, so one waveform "
            "means 'forward one slot' everywhere on a path, bends included (2305.03828)")
    if plane.switch_per_site:
        plane.notes.append(
            "every site has a switch, so it may opt out of its channel -- which is what "
            "makes participation variadic (R4) without adding channels")
    else:
        plane.notes.append(
            "no per-site switch: a channel's sites are all-or-nothing")
    return plane
