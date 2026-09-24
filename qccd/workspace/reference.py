"""Version-matched reference for agents: what THIS workspace's release and evaluator mean.

Every section is derived from the code or release the workspace is pinned to -- the
operation signatures from `operations.OPERATIONS`, the rule statements from
`qccd.verify.rules.RULE_STATEMENTS`, the program verbs from `qccd.api.PROGRAM_METHODS`,
the stages from the evaluator -- so the reference cannot describe a different version
than the one that will grade the work.  Longer prose comes from the skill package's
`references/` and from `docs/`, returned in bounded windows.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

__all__ = ["read_reference", "SECTIONS"]

_REPO = Path(__file__).resolve().parents[2]
_SKILL = Path(__file__).resolve().parent / "skill"
_DOCS = {"adl": "docs/adl.md", "rules": "docs/rules.md", "tsir": "docs/tsir.md", "phys": "docs/phys.md"}
SECTIONS = ("index", "site", "site:studio", "task", "operations", "rules", "evaluator", "program", "anchors",
            "workflow", "docs:adl", "docs:rules", "docs:tsir", "docs:phys")
_WINDOW = 12000


def read_reference(ws, section: str = "index", query: str | None = None) -> dict:
    from . import evaluator_identity
    head = {"task": ws.release.id, "task_digest": ws.release.digest,
            "evaluator": {k: v for k, v in evaluator_identity().items()}, "section": section}
    if section == "index":
        return {**head, "sections": list(SECTIONS),
                "hint": "read `workflow` first; `site` before working on the website's pages; `operations` "
                        "before editing; `evaluator` before citing results"}
    if section in ("site", "site:studio"):
        from .mirror import SiteMirror
        from .site_guide import site_guide
        return {**head, **site_guide(getattr(ws, "site_mirror", None) or SiteMirror(), section, query)}
    if section == "task":
        return {**head, "release": ws.release.summary(), "circuit_qasm": ws.release.circuit_text()[:_WINDOW]}
    if section == "operations":
        from .operations import describe_operations
        return {**head, "operations": describe_operations(),
                "change_set": {"required": ["expected_revision", "request_id", "operations"],
                               "optional": ["branch", "mode ('preview'|'apply', default preview)",
                                            "summary", "origin_prompt_id", "rebase ('never'|'if_disjoint')"]},
                "constraints": [{"type": "protect", "keys": "entity key[]", "level": "'entity'|'neighbourhood'?",
                                 "note": "string?"},
                                {"type": "unprotect", "keys": "entity key[]",
                                 "note": "an agent cannot unprotect what a person protected"}],
                "entity_keys": "node:<id>, segment:<id>, loop:<id>, zone:<name>, block:<primitives|control|...>"}
    if section == "rules":
        from ..verify.rules import RULE_STATEMENTS
        rules = [{"id": k, "statement": v} for k, v in RULE_STATEMENTS.items()]
        if query:
            rules = [r for r in rules if query.lower() in (r["id"] + " " + r["statement"]).lower()]
        return {**head, "rules": rules}
    if section == "evaluator":
        from . import evaluator as ev
        return {**head, "stages_doc": ev.__doc__, "profiles": list(ev.PROFILES),
                "statuses": list(ev.STAGE_STATUSES), "allowed_skips": sorted(ev.ALLOWED_SKIPS),
                "allowed_partial": sorted(ev.ALLOWED_PARTIAL),
                "required_checks": ws.release.required_checks}
    if section == "program":
        from ..api import PROGRAM_METHODS, Program
        verbs = []
        for m in PROGRAM_METHODS:
            fn = getattr(Program, m, None)
            if fn is None:
                continue
            doc = (inspect.getdoc(fn) or "").split("\n\n")[0]
            try:
                sig = str(inspect.signature(fn)).replace("self, ", "").replace("(self)", "()")
            except (TypeError, ValueError):
                sig = "(...)"
            verbs.append({"method": m, "signature": sig, "doc": doc[:600]})
        return {**head, "verbs": verbs,
                "record": "{method, args, kwargs} -- the same records the studio's Write lane holds"}
    if section == "anchors":
        from .collab import ANCHOR_KINDS, INTENTS, PROMPT_MODES
        return {**head, "anchor_kinds": list(ANCHOR_KINDS), "intents": list(INTENTS), "modes": list(PROMPT_MODES),
                "coordinates": "sketch and region points are diagram coordinates (lattice units, the space "
                               "of node pos); viewport is the view box they were drawn in; they are intent, "
                               "never physical constraints"}
    if section == "workflow":
        p = _SKILL / "references" / "workflow.md"
        return {**head, "text": _window(p.read_text(encoding="utf-8") if p.exists() else "", query)}
    if section.startswith("docs:"):
        name = section.split(":", 1)[1]
        rel = _DOCS.get(name)
        if not rel:
            from .core import WorkspaceError
            raise WorkspaceError("unknown_section", f"no docs section {name!r}", status=404)
        p = _REPO / rel
        return {**head, "path": rel, "text": _window(p.read_text(encoding="utf-8") if p.exists() else "", query)}
    from .core import WorkspaceError
    raise WorkspaceError("unknown_section", f"sections: {', '.join(SECTIONS)}", status=404)


def _window(text: str, query: str | None) -> dict:
    """A bounded window: the start, or the part around the first match of `query`."""
    if query:
        m = re.search(re.escape(query), text, re.I)
        if m:
            a = max(0, m.start() - _WINDOW // 4)
            return {"offset": a, "text": text[a:a + _WINDOW], "total": len(text), "matched": True}
        return {"offset": 0, "text": "", "total": len(text), "matched": False}
    return {"offset": 0, "text": text[:_WINDOW], "total": len(text), "truncated": len(text) > _WINDOW}
