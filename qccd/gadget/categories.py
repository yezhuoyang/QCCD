"""What kinds of place a fault-tolerant machine is built from.

A gadget is a place where many ions go to get one job done, the way a town has schools,
hotels and hospitals: a logical block is born at a preparation place, rests in a logical
zone, goes for a check-up at syndrome extraction, meets another block at a lattice-surgery
bridge or a transversal-gate workshop, and ends at a readout.  Ions travel between places
on roads.  Every category has its own colour and its own silhouette, so a drawing of a
whole machine reads at a glance; this table is the one place both are defined (the page
reads it from the library JSON).

`shape` names a silhouette the page knows how to draw:

    vault     rectangle with a double wall               logical zone
    octagon   cut corners                                syndrome extraction
    house     pitched roof                               preparation
    stadium   rounded ends                               readout
    hexagon   pointed left and right                     logical operation
    bridge    arch cut from the bottom edge              lattice surgery
    sawtooth  factory roof                               magic-state factory
    chevron   pointed right, notched left                magic-state injection
    garage    trapezoid                                  ion depot
    circle    a round junction                           roads and junctions
    chip      rectangle with pins down both sides        decoder
    drum      a cylinder seen from the side              classical memory ("archive")
    wire      a dashed link                              classical data bus

Two of these places hold no ions at all.  A **decoder** is the classical processor that
turns syndrome bits into a correction, and a **classical memory** is where the corrections
live -- the Pauli (and Clifford) frame of every logical block, and every logical outcome
measured so far.  They are gadgets like any other: a finite thing with a position, ports,
a latency and a job, wired to the places that measure and to the places whose next
operation depends on a bit (GADGETS.md §10).
"""

from __future__ import annotations

__all__ = ["CATEGORIES", "category", "ORDER", "CLASSICAL"]

CATEGORIES: dict[str, dict] = {
    "zone": {
        "title": "Logical Zone", "place": "Hotel", "shape": "vault", "glyph": "ZONE",
        "stroke": "#3730a3", "fill": "#e0e7ff",
        "job": "Holds logical blocks between operations.  No lasers: blocks check in, "
               "rest, and check out in the order they arrived.",
    },
    "se": {
        "title": "Syndrome Extraction", "place": "Clinic", "shape": "octagon", "glyph": "SE",
        "stroke": "#0f766e", "fill": "#ccfbf1",
        "job": "A block comes in for a check-up: resident ancillas measure every "
               "stabilizer, rounds at a time, and the block leaves with its syndrome taken.",
    },
    "prep": {
        "title": "Logical Preparation", "place": "School", "shape": "house", "glyph": "PREP",
        "stroke": "#b45309", "fill": "#fef3c7",
        "job": "Fresh ions come in; a logical |0̄⟩ or |+̄⟩ block goes out: data reset, "
               "then a syndrome round establishes the code state.",
    },
    "readout": {
        "title": "Logical Readout", "place": "Post office", "shape": "stadium", "glyph": "READ",
        "stroke": "#0369a1", "fill": "#e0f2fe",
        "job": "A block comes in and every data ion is measured in one basis; the logical "
               "outcome is the parity of a logical operator's support.  The ions go back "
               "to a depot.",
    },
    "operation": {
        "title": "Logical Operation", "place": "Workshop", "shape": "hexagon", "glyph": "GATE",
        "stroke": "#c2410c", "fill": "#ffedd5",
        "job": "Two blocks meet and take a transversal gate: ion i of one block with ion i "
               "of the other, so no fault spreads within a block.",
    },
    "surgery": {
        "title": "Lattice Surgery", "place": "Bridge", "shape": "bridge", "glyph": "LS",
        "stroke": "#7e22ce", "fill": "#f3e8ff",
        "job": "Two blocks are merged through a seam of resident ions for d rounds and "
               "split again: the product Z̄Z̄ or X̄X̄ is measured without touching either "
               "logical alone.",
    },
    "factory": {
        "title": "Magic State Factory", "place": "Factory", "shape": "sawtooth", "glyph": "MSF",
        "stroke": "#b91c1c", "fill": "#fee2e2",
        "job": "Fifteen noisy T gates in, one better T state out: the 15-to-1 Reed-Muller "
               "protocol on resident ions.",
    },
    "injection": {
        "title": "Magic State Injection", "place": "Pharmacy", "shape": "chevron",
        "glyph": "INJ", "stroke": "#be185d", "fill": "#fce7f3",
        "job": "A physical magic state and fresh ions come in; a logical block holding that "
               "state goes out.  Not fault-tolerant by design: distillation is what "
               "suppresses its errors.",
    },
    "decoder": {
        "title": "Decoder", "place": "Control room", "shape": "chip", "glyph": "DEC",
        "stroke": "#15803d", "fill": "#dcfce7",
        "job": "The classical processor (FPGA or GPU) wired to every place that measures: "
               "syndrome bits come in, and after a stated latency it says which correction "
               "to keep -- an update to a block's Pauli frame, not a pulse on an ion.",
    },
    "archive": {
        "title": "Classical Memory", "place": "Archive", "shape": "drum", "glyph": "MEM",
        "stroke": "#1e40af", "fill": "#dbeafe",
        "job": "Holds the classical state of the computation: each block's Pauli frame and "
               "Clifford frame, and every logical outcome. Corrections kept in software "
               "are kept HERE, and a conditional operation reads its guard from here.",
    },
    "wire": {
        "title": "Classical Data Bus", "place": "Wire", "shape": "wire", "glyph": "",
        "stroke": "#065f46", "fill": "#ecfdf5",
        "job": "Classical messages: syndromes to the decoder, frame updates to the archive, "
               "guards back to the places. Drawn dashed and dark -- these are bits with a "
               "latency, not ions on a conveyor.",
    },
    "depot": {
        "title": "Ion Depot", "place": "Station", "shape": "garage", "glyph": "DEPOT",
        "stroke": "#57534e", "fill": "#f5f5f4",
        "job": "Supplies bundles of fresh ions and takes used ones back.",
    },
    "road": {
        "title": "Roads and Junctions", "place": "Road", "shape": "circle", "glyph": "",
        "stroke": "#64748b", "fill": "#f1f5f9",
        "job": "Conveyor channels and junctions: ions travel between places on them.",
    },
}

ORDER = ["prep", "se", "zone", "operation", "surgery", "readout", "factory", "injection",
         "decoder", "archive", "depot", "road", "wire"]

#: categories that hold no ions: they run on bits
CLASSICAL = ("decoder", "archive", "wire")


def category(key: str) -> dict:
    return CATEGORIES.get(key) or CATEGORIES["road"]
