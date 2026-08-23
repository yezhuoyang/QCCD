"""Import the shipped 24-ancilla schedule into TSIR.  Milestone M1, external oracle #1.

`INLINE_DATA` is a single JSON object on line 344 of
`visualizer_24_ancillas_24_junctions_standalone.html`, assigned to `const INLINE_DATA =`.
Its `geometries[0].operations` is 396 batch-operations; each rotates the ring by `hops`
in a direction and then docks 1, 2 or 4 data ions to fixed ancilla sites.

What the importer does *not* do is trust the artifact.  Positions are recomputed by the
replay from the initial order and the rotation history, and every one of the 864 contacts
asserts that the ion the artifact says is at a dock really is there.  That -- not the
totals -- is what makes this an oracle: the totals could match by luck in two places, the
positions cannot.

What the shipped schedule is missing
------------------------------------
There are no ancilla measurement or reset operations in the artifact, so the imported
program is a transport-and-contact schedule rather than a complete syndrome-extraction
round.  `completeness_report` names that rather than papering over it; the SPAM the round
would need is 6 group boundaries x (measure + reset), about 1.0 ms, which does not move
any of M1's or M2's numbers but should not be silently absent either.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping

from ..arch import Architecture
from . import provenance as prov
from .tsir import TSIR, Instruction, Participant, loop_shift

__all__ = [
    "extract_inline_data",
    "import_schedule",
    "completeness_report",
    "DEFAULT_HTML",
    "INLINE_PREFIX",
]

DEFAULT_HTML = "visualizer_24_ancillas_24_junctions_standalone.html"
INLINE_PREFIX = "const INLINE_DATA = "


def extract_inline_data(path: str | Path = DEFAULT_HTML) -> dict:
    """Pull `INLINE_DATA` out of the standalone HTML.

    The assignment is one line, so no JavaScript parsing is needed; the line is located
    by its prefix rather than by its number so that an edit above it cannot silently
    change what gets imported.
    """
    text = Path(path).read_text(encoding="utf-8")
    for lineno, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith(INLINE_PREFIX):
            continue
        payload = stripped[len(INLINE_PREFIX) :].rstrip()
        if payload.endswith(";"):
            payload = payload[:-1]
        data = json.loads(payload)
        data.setdefault("_provenance", {})["line"] = lineno
        data["_provenance"]["file"] = str(path)
        return data
    raise ValueError(f"{path}: no line starting with {INLINE_PREFIX!r}")


def _check_geometry(arch: Architecture, geom: Mapping) -> list[str]:
    """The architecture and the artifact must describe the same ring."""
    problems: list[str] = []
    dev = arch.device
    cap = int(geom["capacity"])
    rail = [n for n in dev.nodes if n.startswith("S")]
    if len(rail) != cap:
        problems.append(f"artifact capacity {cap} but the architecture has {len(rail)} rail slots")
    docks_art = sorted(int(s) for s in geom["dock_slots"])
    docks_arch = sorted(int(n.id[1:]) for n in dev.labelled("dock"))
    if docks_art != docks_arch:
        problems.append(f"dock slots differ: artifact {docks_art[:6]}... arch {docks_arch[:6]}...")
    for s in docks_art:
        if f"A{s}" not in dev.nodes:
            problems.append(f"architecture has no ancilla site A{s} for dock slot {s}")
    if int(geom["empty_slots"]) != 0:
        problems.append("the importer assumes a fully occupied ring (empty_slots == 0)")
    coords = geom.get("slot_coordinates")
    if coords:
        for s, (side, x, y) in enumerate(coords):
            node = dev.nodes.get(f"S{s}")
            if node is None:
                continue
            if not node.has(side):
                problems.append(f"slot {s}: artifact says {side!r}, architecture says {node.labels}")
                break
            if (float(x), float(y)) != tuple(node.pos):
                problems.append(f"slot {s}: artifact pos {(x, y)} != architecture {node.pos}")
                break
    return problems


def import_schedule(
    arch: Architecture,
    data: Mapping | None = None,
    *,
    html_path: str | Path = DEFAULT_HTML,
    geometry_index: int = 0,
    name: str = "deck24_esm_round0",
    arch_spec: str = "arch/ring144_24v.arch.json",
) -> TSIR:
    """Convert `geometries[geometry_index].operations` into a TSIR program."""
    if data is None:
        data = extract_inline_data(html_path)
    geom = data["geometries"][geometry_index]

    problems = _check_geometry(arch, geom)
    if problems:
        raise ValueError(
            "the artifact and the architecture disagree:\n  " + "\n  ".join(problems)
        )

    capacity = int(geom["capacity"])
    order = [str(x) for x in geom["initial_order"]]
    dock_slots = sorted(int(s) for s in geom["dock_slots"])
    loop_id = next(iter(arch.device.loops))

    prog = TSIR(name=name, arch_spec=arch_spec)

    placement: dict[str, str] = {}
    for slot, label in enumerate(order):
        placement[f"d{label}"] = f"S{slot}"
    for s in dock_slots:
        placement[f"a{s}"] = f"A{s}"
    prog.add(
        Instruction(
            type="init",
            id=prog.next_id(),
            placement=placement,
            quanta={ion: 0.0 for ion in placement},
            meta={
                "note": (
                    "144 data ions on the rail in `initial_order`, one resident ancilla "
                    "ion per dock site; the artifact reuses each ancilla site across the "
                    "six waves (ancilla_reuse_rule) rather than adding ions"
                )
            },
        )
    )

    rail_segments = tuple(s.id for s in arch.device.loop_segments(loop_id))

    for batch, op in enumerate(geom["operations"]):
        hops = int(op["hops"])
        direction = str(op["direction"])
        if direction not in ("cw", "ccw"):
            raise ValueError(f"batch {batch}: unknown direction {direction!r}")
        delta = hops if direction == "cw" else -hops
        cls = "rotate_cw" if direction == "cw" else "rotate_ccw"
        common = {"batch": batch, "check": op["check"], "group": op["group_index"]}
        # One provenance record per artifact batch-operation. Stamping the whole program
        # afterwards resolved all 1,579 instructions to the single `prov.stamp(...)` line
        # at the end of the builder -- the boilerplate, not the source. For an IMPORTED
        # program the real source IS the artifact operation, so that is what a listing row
        # resolves to: which batch, which check, which way, how far.
        common[prov.CALL_KEY] = prov.note(
            prog, "deck.batch", dedupe=False, batch=batch, check=op["check"],
            group=op["group_index"], direction=direction, hops=hops)

        if hops:
            prog.add(
                Instruction(
                    type="simd",
                    id=prog.next_id(),
                    cls=cls,
                    mode="inter",
                    template=loop_shift(loop_id, delta),
                    holds=(loop_id,) + rail_segments,
                    cost=float(op["rotation_cost"]),
                    steps=int(op["rotation_steps"]),
                    meta=dict(common, kind="rotate", hops=hops, direction=direction),
                )
            )

        contacts = list(op["contacts"])
        dock_participants = []
        undock_participants = []
        pairs = []
        sites = []
        for c in contacts:
            slot = int(c["dock_slot"])
            ion = f"d{c['member']}"
            spur = f"V{slot}"
            dock_participants.append(
                Participant(ion=ion, src=f"S{slot}", dst=f"A{slot}", via=(spur,))
            )
            undock_participants.append(
                Participant(ion=ion, src=f"A{slot}", dst=f"S{slot}", via=(spur,))
            )
            pairs.append((ion, f"a{slot}"))
            sites.append(f"A{slot}")

        spur_holds = tuple(f"V{int(c['dock_slot'])}" for c in contacts)
        node_holds = tuple(f"S{int(c['dock_slot'])}" for c in contacts)

        # the artifact reports dock and undock jointly ("2 one-ion hops per contacted
        # member and 2 batched steps per contact batch"); its own rule decomposes them
        # into one hop per member and one batched step each way
        half_cost = float(op["dock_cost"]) / 2.0
        half_steps = int(op["dock_steps"]) // 2
        prog.add(
            Instruction(
                type="simd",
                id=prog.next_id(),
                cls="dock",
                mode="inter",
                participants=tuple(dock_participants),
                holds=spur_holds + node_holds,
                cost=half_cost,
                steps=half_steps,
                meta=dict(common, kind="dock", contacts=len(contacts)),
            )
        )
        prog.add(
            Instruction(
                type="gate",
                id=prog.next_id(),
                gate="CX",
                pairs=tuple(pairs),
                sites=tuple(sites),
                mode="intra",
                meta=dict(
                    common,
                    kind="contact",
                    members=[c["member"] for c in contacts],
                    member_indices=[c["member_index"] for c in contacts],
                    ancilla_ids=[c["ancilla_id"] for c in contacts],
                ),
            )
        )
        prog.add(
            Instruction(
                type="simd",
                id=prog.next_id(),
                cls="undock",
                mode="inter",
                participants=tuple(undock_participants),
                holds=spur_holds + node_holds,
                cost=half_cost,
                steps=half_steps,
                meta=dict(common, kind="undock", contacts=len(contacts)),
            )
        )

    prog.metrics = {
        "total_cost": float(geom["total_cost"]),
        "total_steps": int(geom["total_steps"]),
        "total_rotate_hops": int(geom["total_rotate_hops"]),
        "contacts": int(data["model"]["contacts"]),
        "checks": int(data["model"]["checks"]),
        "batches": len(geom["operations"]),
    }
    # PRESERVE the provenance log. Assigning `prog.meta` wholesale discarded the call
    # table written during the batch loop above, leaving every instruction carrying a
    # call index into a table that no longer existed -- provenance that silently
    # resolved to None, or worse, to whatever record a later pass happened to create.
    _prov = (prog.meta or {}).get(prov.PROV_KEY)
    prog.meta = {
        **({prov.PROV_KEY: _prov} if _prov else {}),
        "metrics_model": "deck",
        "source": dict(data.get("_provenance", {})),
        "geometry_label": geom["label"],
        "capacity": capacity,
        "active_contact_limit": int(geom["active_contact_limit"]),
        "model_rules": dict(data["model"]),
        "batch_claims": [
            {
                "batch": i,
                "hops": int(o["hops"]),
                "direction": o["direction"],
                "cost": float(o["total_cost"]),
                "steps": int(o["total_steps"]),
                "rotation_cost": float(o["rotation_cost"]),
                "rotation_steps": int(o["rotation_steps"]),
                "dock_cost": float(o["dock_cost"]),
                "dock_steps": int(o["dock_steps"]),
                "contacts": len(o["contacts"]),
            }
            for i, o in enumerate(geom["operations"])
        ],
        "contact_expectations": [
            {
                "batch": i,
                "ion": f"d{c['member']}",
                "node": f"S{int(c['dock_slot'])}",
                "check": c["check"],
                "member_index": c["member_index"],
            }
            for i, o in enumerate(geom["operations"])
            for c in o["contacts"]
        ],
        "checks": [
            {"name": c["name"], "type": c["type"], "members": list(c["members"])}
            for c in geom["checks"]
        ],
        "omits": ["ancilla measure", "ancilla reset", "cooling"],
    }
    return prog


def completeness_report(prog: TSIR) -> dict:
    """Did every check get all six of *its own* members contacted exactly once?

    Counting six distinct `member_index` values per check is not enough: it is satisfied
    by six contacts on the wrong ions.  The contacted member *set* is compared against the
    check's declared members, so relabelling two contacts between checks -- which leaves
    every count and every replayed position intact -- is caught.
    """
    seen: dict[str, set] = {}
    members: dict[str, list[str]] = {}
    per_ion: dict[str, int] = {}
    for exp in prog.meta.get("contact_expectations", ()):
        seen.setdefault(exp["check"], set()).add(exp["member_index"])
        members.setdefault(exp["check"], []).append(str(exp["ion"])[1:])
        per_ion[exp["ion"]] = per_ion.get(exp["ion"], 0) + 1
    declared = {c["name"]: c for c in prog.meta.get("checks", ())}
    complete = [k for k, v in seen.items() if len(v) == 6]
    incomplete = {k: sorted(v) for k, v in seen.items() if len(v) != 6}
    missing = sorted(set(declared) - set(seen))

    wrong_members: dict[str, dict] = {}
    duplicated: dict[str, list[str]] = {}
    for name, contacted in members.items():
        if len(set(contacted)) != len(contacted):
            duplicated[name] = sorted(
                m for m in set(contacted) if contacted.count(m) > 1
            )
        want = declared.get(name, {}).get("members")
        if want is None:
            continue
        if set(contacted) != set(str(m) for m in want):
            wrong_members[name] = {
                "declared": sorted(str(m) for m in want),
                "contacted": sorted(contacted),
            }

    contacts_per_ion = sorted(set(per_ion.values()))
    return {
        "checks_declared": len(declared),
        "checks_contacted": len(seen),
        "checks_complete_6_of_6": len(complete),
        "checks_incomplete": incomplete,
        "checks_never_contacted": missing,
        "checks_with_wrong_members": wrong_members,
        "checks_with_duplicated_members": duplicated,
        "contacts_total": sum(per_ion.values()),
        "distinct_data_ions": len(per_ion),
        "contacts_per_data_ion": contacts_per_ion,
        "complete": (
            len(complete) == len(declared)
            and not incomplete
            and not missing
            and not wrong_members
            and not duplicated
        ),
        "omitted_operations": list(prog.meta.get("omits", ())),
    }
