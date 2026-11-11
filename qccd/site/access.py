"""Who may read what on the website.

Three readers (the accounts service, `comments_api.py`, knows which one a request is):

    a visitor            nobody signed in
    a member             any signed-in account; the site is by invitation
    a granted member     a member given a named permission, one account at a time

and what each is refused.  The web server asks the accounts service before it serves a guarded
path (`deploy/nginx-access.conf`, nginx's `auth_request`), so a guarded page is never sent to a
reader it is not for: hiding a link is a courtesy, never the guard.

    for members          the 3-D stage (its two files, `static/3d/`), so no visitor's page shows
                         the 3D switch; the Goal pages (`board/goal1/`, `board/goal3/`) and the
                         two Goal sections of the Leaderboard's index
    for `layers`         everything compiled onto a machine built in layers: `layers/`, which
                         holds the pages, their numbers, and the boards that rank such entries

A page that exists in two forms is written twice, `x.html` and `x.public.html`, and the web
server chooses.  The documents drop what is marked unpublished (`without_unpublished`).

This module holds the two pages the web server answers with instead, and that mark.
"""

from __future__ import annotations

import re

#: guarded prefixes, by what guards them: the web server's snippet says the same
MEMBERS = ("static/3d/", "board/goal1/", "board/goal3/")
GRANTED = {"layers": ("layers/",)}

_UNPUBLISHED = re.compile(r"<!--\s*unpublished:start\s*-->.*?<!--\s*unpublished:end\s*-->\n?", re.S)


def without_unpublished(markdown: str) -> str:
    """A document as the website prints it: without the passages between
    `<!-- unpublished:start -->` and `<!-- unpublished:end -->`."""
    return _UNPUBLISHED.sub("", markdown)


# ---- the rules, as a visitor reads them
#
# Two words of the rule language exist for machines built in layers.  On a machine drawn in a
# plane both are always true, so the Rules page prints each rule in its planar reading: the
# formula without them, and its account without the sentences about layers.  The checker
# evaluates the rule as it is written (qccd/ruleset/library/catalogue.rules).
UNPUBLISHED_WORDS = frozenset({"upright", "same_broadcast"})
_PLANAR = ((re.compile(r"\(for all r in rails: upright\(r\)\)\s*and\s+"), ""),
           (re.compile(r"\s+and same_broadcast\(a, b\)"), ""))


def planar_formula(text: str) -> str:
    """A rule's formula as it reads on a machine drawn in a plane.  Raises if a word that is
    not for visitors is still in it: the catalogue changed, and this list must follow."""
    for pattern, new in _PLANAR:
        text = pattern.sub(new, text)
    left = sorted(w for w in UNPUBLISHED_WORDS if w in text)
    if left:
        raise ValueError(f"the planar reading of a rule still says {left}: {text[:120]!r}")
    return text


def planar_about(text: str) -> str:
    """A rule's account without the sentences about machines built in layers."""
    keep = [s for s in re.split(r"(?<=[.!?])\s+", text) if "built in layers" not in s]
    return " ".join(keep)


_WAIT = """<script>
// once the reader has signed in from the bar, ask for the page again: it is at this address
(function(){
  var asked = false;
  function look(){
    fetch('/api/me', {credentials: 'same-origin'}).then(function(r){ return r.json(); }).then(function(d){
      if(d && d.user && !asked){ asked = true; location.reload(); }
    }).catch(function(){});
  }
  window.addEventListener('qccd:me', function(e){ if(e.detail && !asked){ asked = true; location.reload(); } });
  window.addEventListener('focus', look);
})();
</script>"""


def sign_in_page(PAGE: str, STYLE: str) -> str:
    """What a visitor gets at a guarded address (401): the page is there once they sign in."""
    body = """<h1>For signed-in readers</h1>
<p class="sub">This page is part of work that is not published yet, so it is shown to the
project's readers only. Sign in from the bar above and it opens here.</p>
<p>An account is by invitation. If you are a collaborator without one, ask the person who
sent you this address.</p>""" + _WAIT
    return PAGE.format(title="For signed-in readers - QCCD studio", style=STYLE, extra_css="", body=body)


def not_shared_page(PAGE: str, STYLE: str) -> str:
    """What a member gets at an address their account was not given (403)."""
    body = """<h1>Not shared with this account</h1>
<p class="sub">You are signed in, and this page is shared with a few named readers only. It
holds results that are not published yet.</p>
<p>The rest of the site is as it was: <a href="/">back to the start</a>.</p>"""
    return PAGE.format(title="Not shared with this account - QCCD studio", style=STYLE, extra_css="", body=body)
