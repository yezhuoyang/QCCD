"""The Rules page's physical, architecture-specific demos."""

from __future__ import annotations

from pathlib import Path

from qccd.site import architecture_compilation as page
from qccd.ruleset import NOT_APPLICABLE


def test_the_gallery_builds_real_runs_with_each_architecture_rule_set(tmp_path):
    from qccd.site.build import device_svg

    written = {}

    def put(rel, html, depth, active, app=False, extra=""):
        written[rel] = {"html": html, "depth": depth, "active": active, "app": app}

    rows = page.build_pages(tmp_path, put, device_svg)
    assert [r["id"] for r in rows] == ["c2lr", "wise", "cyclone", "conveyor", "helios"]
    assert all(r["focus_held"] == r["profile"]["rule_ids"] for r in rows)
    assert all(not any(x["verdict"] == "fails" for x in r["other"]) for r in rows)
    for r in rows:
        rel = f"compilation/architecture/{r['id']}.html"
        assert written[rel]["app"] is True
        assert r["ruleset"] in written[rel]["html"]
        assert "Report" in written[rel]["html"]


def test_the_gallery_explains_passes_non_applicability_and_opens_every_run():
    profiles = page.architecture_profiles()
    rows = []
    for p in profiles:
        rows.append({"id": p["id"], "profile": p, "page": f"architecture/{p['id']}.html",
                     "instructions": 4, "held": list(p["rule_ids"]), "focus_held": list(p["rule_ids"]),
                     "other": [{"id": "R9", "verdict": NOT_APPLICABLE, "reason": "no claim"}],
                     "ruleset": p["ruleset_title"], "poster": "<svg></svg>"})
    html = page.section(rows)
    assert "Architecture-specific rule demos" in html
    assert html.count("Open compiled run and architecture rules") == 5
    assert "not applicable" in html
    for p in profiles:
        assert p["title"] in html
        assert f"../compilation/architecture/{p['id']}.html#step=1" in html
