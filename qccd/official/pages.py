"""The page a leaderboard entry opens: the submitted design and its final programme, played in
the studio, with the task's circuit beside it.

The grader draws it right after grading (worker.draw_page, a limited child of its own, so a page
that cannot be drawn never costs the grade), from the same bundle it graded, so what a reader
watches is what was ranked: the device as submitted, the programme as graded
(`program.tsir.json`, cooling included), timed under the release's ranking table.  The circuit
pane is the release's own circuit, joined to the programme through the certificate's witnesses
(`qccd.ir.source_map`, which refuses a join it cannot check); a bundle whose join fails still
gets a page, without the circuit pane, rather than none.

The page is made from an uploaded bundle, so the service serves it sandboxed (an opaque origin:
service.PAGE_HEADERS), never as a page of qccd.academy.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["render_entry_page"]


def render_entry_page(bundle_dir: Path, release, out: Path, *, title: str = "") -> Path:
    from ..arch.device import Architecture
    from ..cost.models import corrected_model
    from ..ir.source_map import build as build_source
    from ..ir.tsir import TSIR
    from ..phys.tech import load_technology
    from ..verify import verify
    from ..viz.render import render_html
    from ..viz.scale import DEFAULT_TECH
    from ..workspace.bundle import read_bundle

    b = read_bundle(Path(bundle_dir))
    arch = Architecture.from_json(b.docs["device"])
    prog = TSIR.from_json(b.docs["program"])
    model = corrected_model(release.manifest["physics"].get("rank_table", "qccdsim_jones"))
    source = None
    try:
        source = build_source(prog, b.docs["certificate"], str(release.circuit_path))
    except Exception:          # an unjoinable certificate: the page plays without the circuit pane
        source = None
    report = verify(prog, arch, model)
    name = title or (b.docs.get("presentation") or {}).get("title") or arch.name
    return render_html(arch, prog, report.result, model, out,
                       tech=load_technology(DEFAULT_TECH),
                       kicker=release.title.upper(),
                       headline=f"{name} - {release.title}",
                       lede=(f"the submitted design and its graded programme: {len(prog)} hardware instructions"
                             + (f" against {len(source['ops'])} circuit statements" if source else "")),
                       source=source, open_pane="Q" if source else None)
