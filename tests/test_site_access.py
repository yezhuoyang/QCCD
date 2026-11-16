"""Who reads what on the website (`qccd/site/access.py`).

A visitor, a member and a member granted a permission are three readers.  The accounts
service decides which a request is (`tests/test_comments_api.py` holds that), and the web
server asks it before serving a guarded path.  These tests hold the rest: that what is
guarded is guarded by name in the web server's snippet, and that nothing a visitor can fetch
says what the guarded pages hold.

  * every guarded prefix has an `auth_request` in `deploy/nginx-access.conf`;
  * the bar and the search index name no guarded page;
  * a board entry marked `restricted` is in the board's full manifest and index only, and the
    public index does not so much as name its family;
  * the documents and the Rules page print their planar reading: no passage, word or sentence
    about machines built in layers.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

access = pytest.importorskip("qccd.site.access", reason="the site package is not in this tree")

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "qccd" / "site"
SNIPPET = (SITE / "deploy" / "nginx-access.conf").read_text(encoding="utf-8")
BOARD = ROOT / "BBResults" / "studio"

#: what a public page must not say: the unpublished family, and the words for layered machines
UNPUBLISHED = re.compile(r"sandwich|built in layers|\briser|upright\(|same_broadcast\(")


def _location(prefix: str) -> str:
    """The snippet's block for `/prefix`."""
    m = re.search(r"location \^~ /" + re.escape(prefix) + r" \{(.*?)\n\}", SNIPPET, re.S)
    assert m, f"no location for /{prefix} in nginx-access.conf"
    return m.group(1)


@pytest.mark.parametrize("prefix", access.MEMBERS)
def test_what_is_for_members_asks_the_accounts_service(prefix):
    assert "auth_request /_authz_member;" in _location(prefix)


@pytest.mark.parametrize("perm, prefix", [(p, x) for p, xs in access.GRANTED.items() for x in xs])
def test_what_is_for_a_permission_asks_for_that_permission(perm, prefix):
    block = _location(prefix)
    assert f"auth_request /_authz_{perm};" in block
    assert "error_page 403 /restricted/no/index.html;" in block
    assert f"proxy_pass http://127.0.0.1:8200/api/authz?need={perm};" in SNIPPET


def test_every_permission_the_snippet_asks_for_is_one_the_service_knows():
    from qccd.site import comments_api as capi
    asked = set(re.findall(r"/api/authz\?need=(\w+)", SNIPPET))
    assert asked == {"member"} | set(capi.PERMS) == {"member"} | set(access.GRANTED)
    for perm, link in capi.PERM_LINKS.items():
        assert link["href"].lstrip("/").startswith(access.GRANTED[perm])


def test_the_bar_names_no_guarded_page():
    nav = (SITE / "nav.html").read_text(encoding="utf-8")
    assert not UNPUBLISHED.search(nav)
    for prefix in access.MEMBERS + tuple(p for ps in access.GRANTED.values() for p in ps):
        assert prefix not in nav, prefix
    # a granted reader's links come from the accounts service, with who they are
    bar = (SITE / "comments.html").read_text(encoding="utf-8")
    assert "ME.links" in bar and not UNPUBLISHED.search(bar)


def test_a_restricted_entry_is_on_the_full_board_only():
    full = json.loads((BOARD / "manifest.json").read_text(encoding="utf-8"))
    public = json.loads((BOARD / "manifest.public.json").read_text(encoding="utf-8"))
    kept_back = [r["key"] for r in full if r.get("restricted")]
    assert kept_back and all(r.get("restricted") in access.GRANTED for r in full if r.get("restricted"))
    assert [r["key"] for r in public] == [r["key"] for r in full if not r.get("restricted")]
    index = (BOARD / "index.public.html").read_text(encoding="utf-8")
    assert not UNPUBLISHED.search(index), UNPUBLISHED.search(index).group(0)
    for key in kept_back:
        assert key not in index and key in (BOARD / "index.html").read_text(encoding="utf-8")


def test_no_other_board_names_the_unpublished_family():
    for index in list((ROOT / "SmallCode").glob("*/index.html")) + list((ROOT / "MemoryBoard").glob("*/index.html")):
        assert not UNPUBLISHED.search(index.read_text(encoding="utf-8")), index


@pytest.mark.parametrize("name", ["adl", "rules"])
def test_a_document_is_printed_without_its_unpublished_passages(name):
    text = (ROOT / "docs" / f"{name}.md").read_text(encoding="utf-8")
    assert "<!-- unpublished:start -->" in text, "the passages on layered machines are marked"
    printed = access.without_unpublished(text)
    assert not UNPUBLISHED.search(printed)
    assert "unpublished:" not in printed and len(printed) > 0.8 * len(text)


def test_the_rules_are_printed_in_their_planar_reading():
    from qccd.ruleset import catalogue
    from qccd.site import rulespage
    for r in catalogue().rules:
        formula = access.planar_formula((f"when {r.when_text}:\n" if r.when is not None else "") + r.text)
        assert not UNPUBLISHED.search(formula + " " + access.planar_about(r.about)), r.id
    # a formula that still says such a word is refused, not printed
    with pytest.raises(ValueError, match="still says"):
        access.planar_formula("for all r in rails: upright(r) or lies_on(r, r)")
    for name, blob in rulespage.downloads():
        assert not UNPUBLISHED.search(blob.decode("utf-8")), name


def test_the_planar_reading_is_the_rule_on_a_machine_drawn_in_a_plane():
    """What is left out is always true there, so the reading a visitor gets is not a weaker rule."""
    from qccd import Machine
    from qccd.ruleset.vocabulary import TERMS
    assert {t.name for t in TERMS.values()} >= access.UNPUBLISHED_WORDS
    m = Machine.grid(3, 3)
    assert len(m.arch.device.layers) == 1 and not m.arch.device.risers
