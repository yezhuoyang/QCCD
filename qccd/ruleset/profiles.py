"""Architecture-profile presentation joined to the executable rule catalogue."""

from __future__ import annotations

import json
from pathlib import Path

from . import catalogue, load

PROFILE_FILE = Path(__file__).resolve().parents[1] / "workspace" / "web" / "rules-profiles.json"


def architecture_profiles() -> list[dict]:
    """Return website profiles whose rule prose comes from the checker itself."""
    profiles = json.loads(PROFILE_FILE.read_text(encoding="utf-8"))
    rules = {rule.id: rule for rule in catalogue().rules}
    for profile in profiles:
        selected = load(profile["ruleset"])
        unknown = set(profile["rule_ids"]) - set(selected.ids)
        if unknown:
            raise ValueError(
                f"architecture profile {profile['id']!r} names rules outside "
                f"{selected.name!r}: {', '.join(sorted(unknown))}")
        profile["ruleset_title"] = selected.title
        profile["rules"] = [
            {"id": rid, "title": rules[rid].title, "statement": rules[rid].about}
            for rid in profile["rule_ids"]
        ]
    return profiles
