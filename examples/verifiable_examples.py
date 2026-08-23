#!/usr/bin/env python
"""Small enough to check by hand, rendered in the deck's own visual language.

    python examples/verifiable_examples.py        # writes out/verify/index.html

Every example below is small enough that a person can count the ions and segments on
screen and confirm the number the verifier printed. That is the point: the platform's big
results (2672 hops, 1747 quanta, 255-hop compiled schedules) are only worth trusting if
the arithmetic underneath is checkable at a scale a human can hold in their head.

The last one is the deck's own worked example (p.16), which gives a target to hit.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.cost import deck_model  # noqa: E402

OUT = ROOT / "out" / "verify"
CARDS: list[dict] = []


def show(title, kicker, lede, machine, prog, model=None, expect=None, notes=()):
    r = machine.run(prog, model=model or deck_model(), check_metrics=False)
    ok = True
    print(f"\n{title}")
    print("-" * len(title))
    for line in notes:
        print(f"  {line}")
    print(f"  cost {r.cost:,.0f}   steps {r.steps:,}   "
          f"rules {'all pass' if r.ok else r.rules_failed}")
    if expect:
        for k, want in expect.items():
            got = {"cost": r.cost, "steps": r.steps}[k]
            mark = "ok" if got == want else f"MISMATCH (expected {want})"
            if got != want:
                ok = False
            print(f"  expected {k} {want}: {mark}")
    path = machine.render(
        prog, OUT / f"{len(CARDS):02d}_{title.split(' -- ')[0].replace(' ', '_')}.html",
        model=model or deck_model(), kicker=kicker, headline=title, lede=lede)
    CARDS.append({"title": title, "kicker": kicker, "lede": lede, "file": path.name,
                  "cost": r.cost, "steps": r.steps, "ok": r.ok and ok,
                  "notes": list(notes)})
    return r


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    # Card filenames are index-derived, so inserting a card shifts every later index and
    # the previous run's files are orphaned rather than overwritten -- which is how
    # out/verify came to hold 13 pages that index.html linked 7 of.  Start clean.
    for stale in OUT.glob("*.html"):
        stale.unlink()

    # --------------------------------------------------- 0. shuttling, in a line
    line = Machine.chain(12, name="linear_trap")
    p = line.program("shuttle_along_the_axis").init({"d1": "C0"})
    p.shuttle("d1", [f"C{i}" for i in range(12)])
    show("Shuttling along a linear trap", "TRANSPORT PRIMITIVE",
         "The primitive everything else is built from. One ion is carried along the trap "
         "axis by ramping the DC electrodes in sequence, so the axial potential well -- "
         "and the ion sitting in it -- translates. Eleven segments, one hop each. Press "
         "Glide one step and watch the pads light as the well passes over them.",
         line, p, expect={"cost": 11, "steps": 11},
         notes=["12 traps in a line, 11 segments between them",
                "cost = 11 x 1;  steps = 11, because one ion cannot move in parallel "
                "with itself",
                "the RF electrodes hold the ion radially; only the DC electrodes move it"])

    # ------------------------------------------------------------- 1. one hop
    m = Machine.ring(8, 2, 0, name="ring16")
    p = m.program("one_ion_one_hop").init({"d1": "S0"})
    with p.cycle("shuttle") as c:
        c.move("d1", "S0", "S1")
    show("One ion, one hop", "TRANSPORT PRIMITIVE",
         "The smallest thing the machine can do. One ion crosses one straight segment: "
         "one step, cost one.",
         m, p, expect={"cost": 1, "steps": 1},
         notes=["a straight rail segment costs 1 hop and 1 step -- count it on screen"])

    # ------------------------------------------------------- 2. across a bend
    p = m.program("one_ion_around_the_end").init({"d1": "S7"})
    with p.cycle("shuttle") as c:
        c.move("d1", "S7", "S8")
    show("One ion, around the end", "TRANSPORT PRIMITIVE",
         "The same move across the ring's end-cap. The deck charges a corner edge three "
         "primitive hops where a straight edge costs one, and the hop's depth is three "
         "because that is the deepest edge any moving ion used.",
         m, p, expect={"cost": 3, "steps": 3},
         notes=["S7 and S8 are both corners, so the segment between them contains a "
                "whole 180-degree turn"])

    # --------------------------------------------------- 3. rigid rotation x1
    m16 = Machine.ring(8, 2, 0, name="ring16")
    p = m16.program("rotate_by_1").fill()
    p.rotate(+1)
    show("16-ion ring, rotate by 1", "ROUTING SCHEME A",
         "One rigid hop moves every ion at once. Cost is the sum over the 16 segments; "
         "steps is the deepest single edge, because the whole hop waits for it.",
         m16, p, expect={"cost": 20, "steps": 3},
         notes=["16 slots, 16 segments, of which 2 are end-caps",
                "cost  = 14 straight x 1  +  2 end-cap x 3  =  20",
                "steps = max edge depth = 3"])

    # --------------------------------------------------- 4. rigid rotation x2
    p = m16.program("rotate_by_2").fill()
    p.rotate(+2)
    r = show("16-ion ring, rotate by 2", "ROUTING SCHEME A",
             "The deck's own worked example (p.16): a 16-ion ring rotated by two, "
             "totalling cost 40 and steps 20.",
             m16, p, expect={"cost": 40},
             notes=["deck p.16: 'Totals: cost 40 - steps 20'",
                    "cost 40 = 2 hops x 20 -- matches exactly"])
    print(f"  steps: ours {r.steps}, deck 20 -- these count different things.")
    print("    The deck serializes each corner ion's four sub-hops (two 90-degree turns,")
    print("    the highway junction, one rail hop) and batches the 12 rail ions into 4")
    print("    steps: 4 + 4x4 = 20. Our model charges the end-cap as one 3-deep edge and")
    print("    takes the max over the hop, giving 3 per hop. Same cost, different clock.")
    print("    The deck's own totals line also says the corner ions cost '8 hops each',")
    print("    which would make the total 56, not 40; 4 hops each is what reconciles.")

    # ------------------------------------------------------ 5. a full contact
    md = Machine.ring(8, 2, 2, name="ring16_2v")
    p = md.program("one_contact")
    p.init({**{f"d{i + 1}": f"S{i}" for i in range(16)}, "a0": "A0", "a8": "A8"})
    p.rotate(+1)
    with p.cycle("dock") as c:
        c.move("d16", "S0", "A0", via=["V0"])
    p.gate("CX", [("d16", "a0")], sites=["A0"])
    with p.cycle("undock") as c:
        c.move("d16", "A0", "S0", via=["V0"])
    show("One complete contact", "ROUTING SCHEME A",
         "Rotate, dock one ion onto its ancilla, gate, undock. The deck charges a dock "
         "and undock two one-ion hops per contacted member and two batched steps per "
         "batch; the gate itself is free in that model.",
         md, p, expect={"cost": 22, "steps": 5},
         notes=["cost  = 1 rigid hop x 20  +  dock 1  +  undock 1  =  22",
                "steps = 3 (the hop) + 1 (dock) + 1 (undock) = 5",
                "note the two docks are now degree-3 junctions: gold-ringed on screen"])

    # ------------------------------------------- 6. the shipped ring, one hop
    big = Machine.load(ROOT / "arch" / "ring144_24v.arch.json")
    p = big.program("one_rigid_hop").fill()
    p.rotate(+1)
    show("The shipped 2x72 ring, one rigid hop", "ROUTING SCHEME A",
         "The unit the whole 24-ancilla schedule is built from. 2672 of these make its "
         "total cost of 397184.",
         big, p, expect={"cost": 148, "steps": 3},
         notes=["cost  = 142 straight x 1  +  2 end-cap x 3  =  148",
                "steps = 3;  2672 hops x 148 + 864 contacts x 2 = 397184"])

    _write_index()
    print(f"\nopen {OUT / 'index.html'}")
    return 0 if all(c["ok"] for c in CARDS) else 1


def _write_index() -> None:
    cards = "".join(
        f"<a class='card' href='{c['file']}'>"
        f"<div class='kicker'>{c['kicker']}</div><h3>{c['title']}</h3>"
        f"<p>{c['lede']}</p>"
        + "".join(f"<div class='note'>{n}</div>" for n in c["notes"])
        + f"<table><tr><td>cost</td><td>{c['cost']:,.0f}</td></tr>"
        f"<tr><td>steps</td><td>{c['steps']:,}</td></tr>"
        f"<tr><td>rules</td><td class='{'ok' if c['ok'] else 'bad'}'>"
        f"{'all pass' if c['ok'] else 'check'}</td></tr></table></a>"
        for c in CARDS)
    (OUT / "index.html").write_text(
        f"""<!doctype html><html><head><meta charset="utf-8">
<title>Hand-checkable examples</title><style>
body{{margin:0;background:#f7f8fb;color:#182230;
font:14px/1.55 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:30px}}
h1{{margin:0 0 4px;font-size:26px;color:#1a2b4a}}
.sub{{color:#667085;margin-bottom:24px;max-width:76ch}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:14px}}
.card{{display:block;background:#fff;border:1px solid #d0d5dd;border-radius:10px;
padding:16px;text-decoration:none;color:inherit}}
.card:hover{{border-color:#e8552d}}
.kicker{{color:#e8552d;font-size:10.5px;font-weight:700;letter-spacing:.09em;
text-transform:uppercase}}
.card h3{{margin:3px 0 6px;font-size:16px;color:#1a2b4a}}
.card p{{margin:0 0 8px;color:#667085;font-size:12.5px}}
.note{{color:#475467;font-size:12px;font-family:ui-monospace,monospace;
background:#eef2f6;border-radius:4px;padding:3px 7px;margin-bottom:4px}}
table{{width:100%;border-collapse:collapse;font-size:12.5px;margin-top:8px}}
td{{padding:2px 0;border-bottom:1px solid #eef2f6}}
td:last-child{{text-align:right;color:#667085;font-variant-numeric:tabular-nums}}
.ok{{color:#0f766e!important}} .bad{{color:#b42318!important}}
</style></head><body><main>
<h1>Hand-checkable examples</h1>
<div class="sub">Each one is small enough to verify by counting on screen. The arithmetic
is printed on the card and computed by the same verifier that produces the platform's
large results &mdash; if these are right, the large ones are built on something you can
check.</div>
<div class="grid">{cards}</div></main></body></html>""",
        encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
