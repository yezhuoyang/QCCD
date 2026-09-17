"""The two places that hold no ions: the decoder and the classical memory.  GADGETS.md §10.

A fault-tolerant machine is half classical, and the classical half is as finite as the
quantum half: a processor sitting somewhere, wired to the places that measure, with a
latency and a throughput.  This module builds those two places as ordinary leaf gadgets --
a footprint, ports, ops with durations, and a verification report -- except that nothing
they do is a TSIR program, because nothing they do moves an ion.  So they are not
characterized by replay; each op's duration is a number from a measured system in the
literature (`logic.decode.LATENCY`), and its status is `modeled`, which is the reporting
contract's word for "this number comes from a stated model, not from this repository's
verifier" (docs/rules.md).

    dec_d3   decoder     syndrome bits in, a Pauli-frame update out
                         ops: decode
                         verified: a lookup table that corrects every single fault of the
                         syndrome-extraction gadget, built from that gadget's own
                         fault-propagation experiment (`logic.decode.lookup_table`)
    arch1    memory      the classical state of the computation
                         ops: record (an outcome), update (a frame), resolve (a guard)
                         verified: nothing to verify at this level -- what the archive
                         holds is checked where it is used, by the flat sign-off, which
                         reads the frames it declares (`logic.flat`)

The two devices are drawn, not simulated: a chip with pins, and a drum of cells.  Clicking
into one shows what it holds at that moment (the page reads `gir.frames` and `gir.messages`),
not ions on a conveyor.
"""

from __future__ import annotations

from ..logic.decode import LATENCY, lookup_table
from ..model import Master, Op, Port

__all__ = ["decoder", "archive", "CLASSICAL", "classical_data", "is_classical"]


def _dev(nodes, segments) -> dict:
    return {"nodes": nodes, "segments": segments, "loops": []}


def _node(nid, x, y, labels, kind="site") -> dict:
    d = {"id": nid, "pos": [float(x), float(y)], "kind": kind, "degree": 1,
         "labels": list(labels)}
    if kind == "site":
        d.update(capacity=0, zone_type="classical")
    return d


def _seg(sid, a, b) -> dict:
    return {"id": sid, "ends": [a, b], "length": 1.0, "capacity": 0, "loop": None,
            "labels": ["trace"]}


ZONES = {"classical": {"capacity": 0, "gate": False, "spam": False, "cool": False,
                       "note": "classical logic: no ion ever enters this place"}}


def classical_data(master: Master, device: dict) -> dict:
    """Leaf data for a place with no ions: a device to draw and nothing to replay."""
    return {"master": master.name, "device": device, "zones": ZONES,
            "home": {}, "roles": {}, "programs": {}, "times": {}, "classical": True}


def is_classical(master: Master) -> bool:
    from ..categories import CLASSICAL
    return master.family in CLASSICAL


# --------------------------------------------------------------------------- decoder


def decoder(*, name: str = "dec_d3", se_circuit=None, latency: dict | None = None):
    """The decoder place: a chip with syndrome pins in and a frame-update pin out.

    `se_circuit` is the syndrome-extraction gadget's own circuit (from its verified TSIR).
    Given it, the builder derives the decoder's lookup table from that gadget's fault
    experiment and checks it corrects every single fault -- the decoder is verified against
    the very circuit whose syndromes it will read."""
    L = dict(LATENCY, **(latency or {}))
    pins_in = [_node(f"s{i}", 0.0, float(i), ["pin", "syndrome"]) for i in range(4)]
    core = _node("core", 3.0, 1.5, ["core"], kind="junction")
    pins_out = [_node(f"f{i}", 6.0, float(i) + 0.5, ["pin", "frame"]) for i in range(2)]
    segs = ([_seg(f"ts{i}", f"s{i}", "core") for i in range(4)]
            + [_seg(f"tf{i}", "core", f"f{i}") for i in range(2)])
    device = _dev(pins_in + [core] + pins_out, segs)

    check = None
    if se_circuit is not None:
        from ..logic.experiment import Block, LogicalFlow, build_experiment
        from ..surface import surface_code
        code = surface_code(3)
        data = [f"d{q}" for q in range(code.n)]
        blk = Block(code, data, "A")
        exp = build_experiment(
            se_circuit, blocks_in=[blk], blocks_out=[blk],
            flows=[LogicalFlow("X̄", [("A", 0, "X")], [("A", 0, "X")]),
                   LogicalFlow("Z̄", [("A", 0, "Z")], [("A", 0, "Z")])])
        check = lookup_table(exp)

    master = Master(
        name=name, kind="leaf", family="decoder",
        title="Lookup decoder (surface code, d = 3)",
        doc="a classical processor wired to every place that measures: syndrome bits in, "
            "an update to a block's Pauli frame out, after a stated latency",
        size=[6.0, 3.0], device=name,
        ports=[Port("syn", "in", "any", 0, "W", 0.5, kind="classical", signal="syndrome",
                    bits=8, node="s0"),
               Port("frame", "out", "any", 0, "E", 0.5, kind="classical", signal="frame",
                    bits=2, node="f0")],
        inventory={}, capacity=0,
        params={"code": "surface_d3", "window": 3, "commit": 1, "latency_us": L,
                "protocol": "sliding window (W = 3 rounds, commit 1); the output is an "
                            "update to the logical Pauli frame"},
    )
    logic = {"status": "verified" if (check or {}).get("passed") else
             ("failed" if check else "none")}
    if check:
        logic["check"] = check
        if not check["passed"]:
            logic["why"] = [check["claim"]]
    master.ops["decode"] = Op(
        name="decode", title="decode one window and emit a frame update",
        status="modeled", duration_us=float(L["lookup_us"]),
        metrics={"syndrome_bits": (check or {}).get("syndrome_bits", 0),
                 "entries": (check or {}).get("entries", 0)},
        rules={"passed": [], "failed": [], "skipped":
               {"G1-G9": "the place model's rules are about ions; a decoder holds none"},
               "partial": {}, "violations": []},
        notes=[L["lookup_note"],
               f"the wire from a measuring place charges {L['link_us']} µs: {L['link_note']}",
               "the correction is a Pauli-frame update, never a pulse: it is written into "
               "the classical memory place, which the sign-off then reads as a declared frame"],
        params={"latency_us": L["lookup_us"]}, logic=logic)
    return master, classical_data(master, device)


# --------------------------------------------------------------------------- memory


def archive(*, name: str = "arch1", blocks: int = 8, latency: dict | None = None):
    """The classical memory place: where corrections kept in software are kept.

    One row of cells per logical block (its Pauli frame, and its Clifford frame), and a
    column of cells for the logical outcomes.  Three ops: `record` writes an outcome,
    `update` applies a decoder's frame update, `resolve` reads the frame back to answer one
    question -- which basis a readout must use, or whether a guarded operation runs."""
    L = dict(LATENCY, **(latency or {}))
    # two rows of cells -- one per logical block -- with the pins on their own rows, so every
    # trace is a straight line: outcomes come in on the top row, frame updates on the bottom
    rows = (0.0, 1.6)
    nodes = [_node("in", 0.0, rows[0], ["pin", "outcome"]),
             _node("frame", 0.0, rows[1], ["pin", "frame"]),
             _node("ctl", 9.0, rows[0], ["pin", "decision"])]
    segs = []
    for r in range(2):
        for c in range(4):
            nodes.append(_node(f"c{r}{c}", 2.0 + 1.6 * c, rows[r], ["cell"]))
        segs.append(_seg(f"row{r}", f"c{r}0", f"c{r}3"))
    segs += [_seg("wi", "in", "c00"), _seg("wf", "frame", "c10"),
             _seg("wc", "c03", "ctl")]
    device = _dev(nodes, segs)
    master = Master(
        name=name, kind="leaf", family="archive", title="Classical memory (frames and outcomes)",
        doc="holds every logical block's Pauli frame and Clifford frame and every logical "
            "outcome: the corrections this machine keeps in software rather than applying "
            "to ions",
        size=[9.0, 2.0], device=name,
        ports=[Port("in", "in", "any", 0, "W", 0.2, kind="classical", signal="outcome",
                    bits=1, node="in"),
               Port("frame", "in", "any", 0, "W", 0.8, kind="classical", signal="frame",
                    bits=2, node="frame"),
               Port("ctl", "out", "any", 0, "E", 0.5, kind="classical", signal="decision",
                    bits=1, node="ctl")],
        inventory={}, capacity=0,
        params={"blocks": blocks, "latency_us": L,
                "state": "per block: a Pauli frame (px, pz) and a Clifford frame U; per "
                         "measurement: the logical outcome, as a parity of records"},
    )
    skipped = {"G1-G9": "the place model's rules are about ions; an archive holds none"}
    for key, title, dur, note in (
        ("record", "write one logical outcome", L["write_us"],
         "the outcome is kept as the parity of the records the measuring gadget's own flow "
         "report names for it -- the archive stores what was measured, not a guess"),
        ("update", "apply a decoder's frame update", L["write_us"],
         "px ^= a, pz ^= b on one block: the correction is applied to the frame, and the "
         "ions are left alone (Pauli frame tracking)"),
        ("resolve", "read the frame back to answer one question", L["resolve_us"],
         "the answer travels to the place that waits for it on the decision wire "
         f"({L['decision_us']} µs: {L['decision_note']})"),
    ):
        master.ops[key] = Op(
            name=key, title=title, status="modeled", duration_us=float(dur),
            metrics={}, rules={"passed": [], "failed": [], "skipped": skipped,
                               "partial": {}, "violations": []},
            notes=[note, L["write_note"] if key != "resolve" else L["resolve_note"]],
            params={"latency_us": dur},
            logic={"status": "none",
                   "note": "what the archive holds is checked where it is used: the flat "
                           "sign-off reads every frame it declares and requires the "
                           "hardware's flows to match the algorithm up to exactly those"})
    return master, classical_data(master, device)


CLASSICAL = {"dec_d3": decoder, "arch1": archive}
