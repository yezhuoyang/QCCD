"""The design tool's page: one HTML file, plus leaf data loaded when needed.  GADGETS.md §10.

The page carries the library, the GIR and the checks inline; each leaf master's device and
programs sit beside it in `leaf/<master>.js`, loaded with a plain `<script>` tag so the page
works from `file://` as well as over HTTP.  Nothing is fetched from a network.  The palette
is the studio's (`qccd.viz.theme`), so the two tools read as one.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..viz.theme import GEOMETRY, PALETTE, css_vars

__all__ = ["render_page"]

WEB = Path(__file__).parent / "web"
#: The occupancy law, shared with the studio stage rather than copied into `web/`.
TRANSIT_JS = Path(__file__).resolve().parents[1] / "viz" / "js" / "transit.js"


def _blob(obj) -> str:
    """JSON safe inside `<script type="application/json">`."""
    return (json.dumps(obj, separators=(",", ":"))
            .replace("</", "<\\/").replace("<!--", "<\\!--"))


def render_page(path, *, lib, gir, checks, leaf_files: dict, studio_pages: dict | None = None,
                rules: dict | None = None, title: str | None = None,
                inline_leaves: dict | None = None, signoff: dict | None = None,
                categories: dict | None = None, category_order: list | None = None,
                machine_rules: dict | None = None, compositional: dict | None = None,
                traffic: dict | None = None, floorplan: str | None = None,
                focus: str | None = None, stack: dict | None = None,
                macro_files: dict | None = None) -> Path:
    if categories is None:
        from .categories import CATEGORIES, ORDER
        categories, category_order = CATEGORIES, ORDER
    data = {
        "library": lib.to_json(),
        "gir": gir,
        "checks": checks,
        "rules": rules or {},
        "leafFiles": leaf_files,
        "leafInline": inline_leaves or {},
        "studio": studio_pages or {},
        "signoff": signoff,
        # the machine level (docs/ARCHITECTURE.md): every rule of the floorplan and the
        # schedule as the rule language decided it, the compositional sign-off, the traffic,
        # and the floorplan as written
        "machineRules": machine_rules,
        "compositional": compositional,
        "traffic": traffic,
        "floorplan": floorplan,
        # where each place family belongs (rule F9): the editor writes regions with it
        "homeRegion": __import__("qccd.gadget.floorplan", fromlist=["HOME_REGION"]).HOME_REGION,
        # what the page shows first, unless its address says otherwise (`#f=`)
        "focus": focus,
        "categories": categories,
        "categoryOrder": category_order or list(categories),
        # the studio's own palette and shape ratios: a trapping site, a junction, a rail
        # and an ion are drawn here by the same numbers as on studio.html, so the two
        # tools cannot drift apart (qccd/viz/theme.py, qccd/viz/layout.py)
        "palette": dict(PALETTE),
        "geometry": dict(GEOMETRY),
    }
    if stack:
        # a program lowered from FormalRV (qccd/fullstack): the layers above it, linked
        data["stack"] = stack
    if macro_files:
        # a folded run (GIR 0.3, qccd/gadget/fold.py): each macro in a file of its own,
        # loaded when the time on screen needs it
        data["macroFiles"] = macro_files
    html = (WEB / "page.html").read_text(encoding="utf-8")
    html = html.replace("__CSSVARS__", css_vars())
    html = html.replace("__TITLE__", title or f"{gir['program']['name']} — QCCD gadgets")
    # THE SAME BYTES THE STUDIO INLINES (`qccd/viz/render.py::ENGINE_JS`).  Where an ion is
    # drawn while the machine is moving it, and what it does when another ion is in the
    # way, is one rule for both canvases; this page had its own, and it drew 41% of its
    # sampled instants with two ions through each other.  `core.js` needs it loaded first.
    html = html.replace("__TRANSIT__", TRANSIT_JS.read_text(encoding="utf-8"))
    html = html.replace("__CORE__", (WEB / "core.js").read_text(encoding="utf-8"))
    html = html.replace("__FOLD__", (WEB / "fold.js").read_text(encoding="utf-8"))
    html = html.replace("__EDITOR__", (WEB / "editor.js").read_text(encoding="utf-8"))
    html = html.replace("__APP__", (WEB / "app.js").read_text(encoding="utf-8"))
    if stack:
        # every control the stack adds is declared for agents (docs/agent-interface.md)
        hints = {"fullstack-line": {"t": "A line of one layer of the full stack",
                                    "d": "Jump the animation to the first instruction that carries "
                                         "this .prog, .qppm or .qec line."}}
        at = html.rfind("</body>")
        html = (html[:at] + "<script>window.QCCD_HINTS = Object.assign(window.QCCD_HINTS || {}, "
                + json.dumps(hints) + ");</script>\n" + html[at:])
    # the data goes in last: nothing after it is a template marker it could contain
    html = html.replace("__DATA__", _blob(data))
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
