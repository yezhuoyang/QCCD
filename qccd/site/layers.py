"""Machines built in layers, on the website (`layers/`).

Three Studio pages and the page that introduces them.  Each Studio page is `render_html`
over a board's circuit, compiled onto the conveyor sandwich (`qccd.arch.sandwich`) by its
router (`qccd.compile.conveyor_router`) on the board's own physics.  A layered machine's page
carries the 3-D stage (the template's `stage3d="auto"`); the site links it into the planar one.  Nothing here needs the OCaml compiler, so the site's build still
needs no install.  Every number the index quotes is measured on the programme its page shows.
"""

from __future__ import annotations

import html
from pathlib import Path

#: key, board release, data qubits, a surface code's (rows, cols), data ions a trap, layers,
#: the page's title, one sentence about it
SHOWS = (
    ("surface17_three_layers", "surface17@1", 9, (3, 3), 1, 3,
     "The surface-17 round on three layers",
     "Small enough to follow one ancilla: its loop turns, it rides a riser into a data trap, "
     "plays its gate and rides back."),
    ("bb144_three_layers", "bb144@2", 144, None, 2, 3,
     "The BB [[144,12,12]] round on three layers",
     "288 ions. The X checks ride the loop below the data and the Z checks the loop above; "
     "each layer has a broadcast of its own, so the two loops turn in the same cycle."),
    ("bb144_one_layer", "bb144@2", 144, None, 2, 1,
     "The same round, the same machine, in one plane",
     "One loop inside the ring of data traps and one outside. One broadcast, so the loops "
     "take turns."),
)


def render(key: str, path: Path) -> dict:
    """Write the Studio page `key` of `SHOWS` at `path`; return the facts the index quotes."""
    from ..arch.device import Architecture
    from ..arch.sandwich import conveyor_sandwich
    from ..compile.conveyor_router import compile_conveyor, lattice_rows
    from ..compile.cooling import insert_cooling
    from ..cost.models import corrected_model
    from ..ir.source_map import build as build_source
    from ..verify import verify
    from ..viz.render import render_html
    from ..workspace.tasks import board_device, find_release

    _, release, n_data, lattice, per_trap, layers, title, about = next(s for s in SHOWS if s[0] == key)
    rel = find_release(release)
    positions, data_at = -(-n_data // per_trap), None
    if lattice:
        positions, data_at = lattice_rows(*lattice)
    machine = conveyor_sandwich(positions, layers=layers, capacity=per_trap + 1)
    arch = Architecture.from_json(board_device(machine.arch.to_json(expanded=True), rel))
    model = corrected_model(rel.manifest.get("rank_table", "qccdsim_jones"))
    prog, cert = compile_conveyor(rel.circuit_text(), arch, n_data, name=release.split("@")[0],
                                  data_at=data_at)
    cooled = insert_cooling(prog, arch, model).program
    cooled.meta.update(prog.meta)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    qasm = path.with_suffix(".qasm")
    qasm.write_text(rel.circuit_text(), encoding="utf-8")
    report = verify(cooled, arch, model, check_metrics=False)
    failed = report.rules.summary()["failed"]
    if failed:
        raise RuntimeError(f"layers page {key!r}: the programme breaks {failed}")
    cycles = sum(i.type == "simd" for i in cooled.instructions)
    ms = report.result.total_us / 1000.0
    built = {1: "in one plane", 2: "on two layers", 3: "on three layers"}[layers]
    render_html(arch, cooled, report.result, model, path, kicker="BUILT IN LAYERS" if layers > 1 else "IN ONE PLANE",
                headline=title,
                lede=(f"Data ions that never move, between two conveyor loops of ancillas, {built}: "
                      f"{cycles} transport cycles, {ms:.2f} ms on the board's clock. "
                      + ("Press 3D on the transport bar, then drag to turn it." if layers > 1 else
                         "One broadcast, so the two loops take turns.")),
                source=build_source(cooled, cert, qasm), template_stems="*")
    qasm.unlink()
    d = arch.device
    return {"key": key, "page": f"{key}.html", "title": title, "about": about, "board": release,
            "layers": layers, "ions": len(next(i for i in cooled.instructions if i.type == "init").placement),
            "ms": round(ms, 2), "cycles": cycles, "dacs": machine.resources().dacs,
            "traps": len(d.sites()), "risers": len(d.risers)}


def studio_pages(tmp: Path) -> list[tuple[str, str, dict]]:
    """`(path under the site, page, facts)` for every page of `SHOWS`."""
    out = []
    for key, *_ in SHOWS:
        page = Path(tmp) / f"{key}.html"
        facts = render(key, page)
        out.append((f"layers/{key}.html", page.read_text(encoding="utf-8"), facts))
    return out


CSS = """
.lay{border-collapse:collapse;width:100%;margin:14px 0 6px;font-size:14.5px}
.lay th,.lay td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
.lay th{font:600 11px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink3)}
.lay td.n{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
"""


def page(PAGE: str, STYLE: str, shown: list[dict]) -> str:
    """The page that introduces them: what a layer is, how to look, and the measured rows."""
    e = html.escape
    rows = "".join(
        f'<tr><td><a href="{e(s["page"])}"><b>{e(s["title"])}</b></a><br>{e(s["about"])}</td>'
        f'<td class="n">{s["layers"]}</td><td class="n">{s["ions"]}</td><td class="n">{s["dacs"]}</td>'
        f'<td class="n">{s["cycles"]}</td><td class="n">{s["ms"]:.2f} ms</td></tr>'
        for s in shown)
    by = {s["key"]: s for s in shown}
    flat, tall = by.get("bb144_one_layer"), by.get("bb144_three_layers")
    worth = (f"<p>On the BB round the same machine takes {flat['ms']:.2f} ms in one plane and "
             f"{tall['ms']:.2f} ms on three layers: {flat['cycles']} transport cycles against "
             f"{tall['cycles']}. The difference is the broadcasts, and it is paid for in control "
             f"lines: {flat['dacs']} DACs against {tall['dacs']}.</p>" if flat and tall else "")
    body = f"""<h1>Machines built in layers</h1>
<p class="sub">A machine on this website is drawn in a plane. The machines on this page are
built in layers: planar layers of traps one above another, joined by rails that run straight
up. They are an experiment, and their pages open in the Studio like any other design.</p>

<h2 id="what">What a layer is</h2>
<p>A node of a layered machine has a third coordinate, and a layer is the nodes that share
it. A rail between two layers is a <b>riser</b>. It is priced, driven and heated as any
other shuttle, so a junction may join the rails of its own layer and one riser up and one
down. Every rule is the rule of a planar machine, read a layer at a time: rails lie in a
layer or rise straight to the next one, and <b>each layer has a broadcast of its own</b>, so
one transport cycle carries one waveform a layer where a planar machine carries one.</p>

<h2 id="look">How to look</h2>
<p>Open a page below and press <b>3D</b> on the transport bar. Drag to turn the machine, turn
the wheel to zoom, drag with the right button to move it. <b>Corner</b>, <b>Top</b> and
<b>Front</b> are three fixed views; <b>Layer spacing</b> pulls the layers apart, and is not to
scale; <b>Show</b> leaves one layer on the stage. Press 3D again for the flat picture, one
panel a layer: the ions are in the same places in both, and Play, Step and the Program
column work in either. The Design page's own 3D button shows any machine you build there
the same way.</p>

<h2 id="pages">Three pages</h2>
<p>The machine on all three is one design, the <i>conveyor sandwich</i>: the data ions stand
still in a ring of traps, and the ancillas ride two conveyor loops with a slot facing every
trap. A layer of gates is a turn of a loop and a hop, whatever the number of ancillas.</p>
<table class="lay"><thead><tr><th>page</th><th>layers</th><th>ions</th><th>DACs</th>
<th>transport cycles</th><th>time</th></tr></thead><tbody>{rows}</tbody></table>
{worth}
<p class="note">These programmes come from a router written for this machine, not from the
compiler the Leaderboard's entries use, and they are not entries on a board. Each page's
programme passes the standard rule set, and its time is on its board's clock.</p>
"""
    return PAGE.format(title="Machines built in layers - QCCD studio", style=STYLE, extra_css=CSS, body=body)


def index_entries() -> list[dict]:
    return [{"t": "Machines built in layers", "d": "three designs to turn in 3D: layers of traps joined by risers",
             "u": "layers/", "k": "page"}]
