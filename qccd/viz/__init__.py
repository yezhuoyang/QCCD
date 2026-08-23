"""Layer 6 -- visualization.  PLAN §9.

One self-contained HTML file per `(architecture, program)`: no server, no CDN, and one
code path for every geometry.
"""

from __future__ import annotations

from .render import build_view_model, render_html

__all__ = ["build_view_model", "render_html"]
