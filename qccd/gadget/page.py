"""The design tool's page: one HTML file, plus leaf data loaded when needed.  GADGETS.md §10.

The page carries the library, the GIR and the checks inline; each leaf master's device and
programs sit beside it in `leaf/<master>.js`, loaded with a plain `<script>` tag so the page
works from `file://` as well as over HTTP.  Nothing is fetched from a network.  The palette
is the studio's (`qccd.viz.theme`), so the two tools read as one.
"""

from __future__ import annotations

import json
from pathlib import Path

from ..viz.theme import css_vars

__all__ = ["render_page"]

WEB = Path(__file__).parent / "web"


def _blob(obj) -> str:
    """JSON safe inside `<script type="application/json">`."""
    return (json.dumps(obj, separators=(",", ":"))
            .replace("</", "<\\/").replace("<!--", "<\\!--"))


def render_page(path, *, lib, gir, checks, leaf_files: dict, studio_pages: dict | None = None,
                rules: dict | None = None, title: str | None = None,
                inline_leaves: dict | None = None, signoff: dict | None = None,
                categories: dict | None = None, category_order: list | None = None) -> Path:
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
        "categories": categories,
        "categoryOrder": category_order or list(categories),
    }
    html = (WEB / "page.html").read_text(encoding="utf-8")
    html = html.replace("__CSSVARS__", css_vars())
    html = html.replace("__TITLE__", title or f"{gir['program']['name']} — QCCD gadgets")
    html = html.replace("__CORE__", (WEB / "core.js").read_text(encoding="utf-8"))
    html = html.replace("__EDITOR__", (WEB / "editor.js").read_text(encoding="utf-8"))
    html = html.replace("__APP__", (WEB / "app.js").read_text(encoding="utf-8"))
    # the data goes in last: nothing after it is a template marker it could contain
    html = html.replace("__DATA__", _blob(data))
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return out
