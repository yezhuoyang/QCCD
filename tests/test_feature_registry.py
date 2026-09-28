"""THE REGISTRY GATE: every agent capability is declared once (features.py), and nothing drifts.

A route an agent's token can reach, a CLI verb, a job kind or a skill that is not tied to a
Feature -- or explicitly set aside with the reason why -- fails here.  So a change that adds a
capability for people and forgets the agent is refused at test time, not found by an agent
stranded in a live run.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from qccd.workspace import features as F
from qccd.workspace.cli import COMMANDS
from qccd.workspace.results import JOB_KINDS, JOB_RUNNERS

SERVICE = Path(__file__).resolve().parents[1] / "qccd" / "workspace" / "service.py"


def _routes() -> dict[str, bool]:
    """Every `@route(...)` of the workspace service -> whether only a person may call it."""
    out = {}
    for m in re.finditer(r'@route\("(GET|POST|PATCH|PUT|DELETE)",\s*"([^"]+)"([^)]*)\)', SERVICE.read_text("utf-8")):
        out[f"{m.group(1)} {m.group(2)}"] = "human_only=True" in m.group(3)
    assert len(out) > 40, "the route pattern no longer matches service.py"
    return out


def test_every_route_an_agent_can_reach_is_a_feature_or_says_why_not():
    routes = _routes()
    called = {r for f in F.FEATURES for r in f.routes}
    reachable = {r for r, human in routes.items() if not human}
    missing = sorted(reachable - called - set(F.INTERNAL_ROUTES))
    assert not missing, ("routes an agent can reach with no Feature and no reason in INTERNAL_ROUTES: "
                         f"{missing} -- declare the tool in features.py, or say why no agent needs it")
    both = sorted(called & set(F.INTERNAL_ROUTES))
    assert not both, f"a route is both a Feature's and 'internal': {both}"


def test_no_declaration_names_a_route_that_does_not_exist():
    routes = _routes()
    for f in F.FEATURES:
        for r in f.routes:
            assert r in routes, f"{f.name} declares {r}, which service.py does not serve"
            assert not routes[r], f"{f.name} calls {r}, which only a person may call"
    stale = sorted(set(F.INTERNAL_ROUTES) - set(routes))
    assert not stale, f"INTERNAL_ROUTES names routes that are gone: {stale}"


def test_every_cli_verb_is_a_feature_or_local():
    tied = {v for f in F.FEATURES for v in f.cli}
    assert tied <= set(COMMANDS), f"Features name CLI verbs that do not exist: {sorted(tied - set(COMMANDS))}"
    loose = sorted(set(COMMANDS) - tied - set(F.LOCAL_VERBS))
    assert not loose, f"CLI verbs with no Feature and no reason in LOCAL_VERBS: {loose}"
    assert set(F.LOCAL_VERBS) <= set(COMMANDS), "LOCAL_VERBS names verbs that are gone"
    assert not (tied & set(F.LOCAL_VERBS)), "a verb is both a Feature's and local"


def test_every_job_kind_is_started_by_a_tool_and_has_a_runner():
    started = {k for f in F.FEATURES for k in f.job_kinds}
    assert started <= set(JOB_KINDS), f"Features start kinds that do not exist: {sorted(started - set(JOB_KINDS))}"
    assert set(JOB_KINDS) <= started, f"job kinds no agent can start: {sorted(set(JOB_KINDS) - started)}"
    from qccd.workspace.app import Workspace
    for kind, meth in JOB_RUNNERS.items():
        assert callable(getattr(Workspace, meth, None)), f"job kind {kind}: Workspace.{meth} does not exist"


def test_the_declarations_are_well_formed():
    names = [f.name for f in F.FEATURES]
    assert len(names) == len(set(names)), "two Features share a name"
    for f in F.FEATURES:
        assert re.fullmatch(r"qccd_[a-z_]+", f.name), f.name
        assert len(f.about) > 40, f"{f.name}: say what it does, for the agent"
        assert f.schema.get("type") == "object" and f.schema.get("additionalProperties") is False, f.name
        assert set(f.schema.get("required") or []) <= set(f.schema.get("properties") or {}), f.name
        assert f.routes, f"{f.name}: which service call(s) does it make?"


def test_the_mcp_surface_is_derived_from_the_registry():
    from qccd.workspace import mcp_server as M
    assert [t[0] for t in M._tools()] == [f.name for f in F.FEATURES]
    assert M.READ_ONLY == {f.name for f in F.FEATURES if f.read_only}
    with pytest.raises(ValueError):
        M.dispatch(object(), "qccd_no_such_tool", {})


def test_the_manifest_lists_every_tool():
    from qccd.workspace.interface import manifest
    assert [t["name"] for t in manifest()["tools"]] == [f.name for f in F.FEATURES]


def test_the_skill_carries_every_tool():
    from qccd.workspace.installers import _skill_files
    files = _skill_files()
    md = files["references/tools.md"].decode()
    for f in F.FEATURES:
        assert f"`{f.name}`" in md, f"the skill's tool list misses {f.name}"
    assert [t["name"] for t in json.loads(files["references/tools.json"])] == [f.name for f in F.FEATURES]
    assert b"references/tools.md" in files["SKILL.md"], "SKILL.md should point at the generated tool list"


def test_a_skill_follows_its_content_not_a_hand_bumped_version(tmp_path, monkeypatch):
    from qccd.workspace import installers as I
    dest = tmp_path / ".claude" / "skills" / "qccd"
    man = I.build_skill(dest, tmp_path)
    assert man["digest"] == I.skill_digest()
    assert I.refresh_skills(tmp_path) == []                        # current: nothing to do
    # the code gains a tool: the digest moves and the unmodified skill is rebuilt on the next start
    monkeypatch.setattr(F, "tools_markdown", lambda: "# Every QCCD tool\n\n`qccd_new_thing`\n")
    assert I.skill_digest() != man["digest"]
    assert I.refresh_skills(tmp_path) == ["claude"]
    assert "qccd_new_thing" in (dest / "references" / "tools.md").read_text()
    # a skill the person edited is left alone
    monkeypatch.setattr(F, "tools_markdown", lambda: "# Every QCCD tool\n\n`qccd_newer_thing`\n")
    (dest / "SKILL.md").write_text("my own notes")
    assert I.refresh_skills(tmp_path) == []
