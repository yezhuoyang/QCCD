"""Every control that claims a hover card must have one.

`data-hint="k"` on a control and `HINTS.k` in `editor.js` are two halves of one fact: the
attribute is what the pointer, the Explain overlay, the `?` guide and the Ctrl+K search all
look the control up by, and `hintFor` returns `null` for a key the table does not carry.
The failure is silent -- the control simply has no card, on every one of those four
surfaces at once -- so nothing but a test can notice it.

This is the check that was assumed to exist and did not (2026-09-17): the studio has
grown a scale bar, a ruler, a true-scale toggle and a QEC-cycle panel since the convention
was written down, and each of them was one forgotten line away from being undocumented.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

NODE = shutil.which("node")
SHIM = (Path(__file__).parent / "shim.mjs").as_uri()

pytestmark = pytest.mark.skipif(NODE is None, reason="node is not on PATH")

#: Keys a page may carry that name something other than a control: the rule ids and the
#: element/region vocabulary are looked up by the same table but written elsewhere.
DYNAMIC = re.compile(r"^(rule:|el:|region:|m:|c:|p\.)")
#: The page also BUILDS markup at run time, so the source carries `data-hint="' + k + '"`
#: literals that are code, not keys.  Anything with a quote or an operator in it is one.
CODE = re.compile(r"['+${}]")


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("hints") / "studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(out)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return out


def hints_of(page: Path, tmp_path: Path, keys: list[str]) -> dict:
    js = tmp_path / "h.mjs"
    js.write_text(f"import {{ loadPage }} from '{SHIM}';\n"
                  "loadPage(process.argv[2], ';globalThis.__E=EDITOR;');\n"
                  "const E = globalThis.__E;\n"
                  f"const keys = {json.dumps(keys)};\n"
                  "const out = {};\n"
                  "for (const k of keys) { const h = E.hintFor ? E.hintFor(k) : null;\n"
                  "  out[k] = h ? { t: String(h.t || ''), d: String(h.d || '') } : null; }\n"
                  "console.log(JSON.stringify(out));\n",
                  encoding="utf-8")
    # `encoding="utf-8"`, explicitly: the cards carry en dashes and middle dots, and the
    # platform's locale codec turns them into a decode error that looks like a test bug
    r = subprocess.run([NODE, str(js), str(page)], capture_output=True, text=True,
                       encoding="utf-8", timeout=600, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-2000:]}"
    return json.loads(r.stdout)


def test_every_data_hint_in_the_page_has_an_entry(page, tmp_path):
    html = page.read_text(encoding="utf-8")
    keys = sorted({k for k in re.findall(r'data-hint="([^"]+)"', html)
                   if not DYNAMIC.match(k) and not CODE.search(k)})
    assert len(keys) > 15, f"only {len(keys)} hinted controls found; the scan is broken"
    got = hints_of(page, tmp_path, keys)
    missing = sorted(k for k, v in got.items() if not v)
    assert not missing, (
        f"{len(missing)} control(s) claim a hover card that HINTS does not carry: {missing}. "
        "Add one line to the HINTS table in qccd/viz/js/editor.js for each.")


def test_every_entry_says_what_the_thing_is_and_not_just_its_name(page, tmp_path):
    """A card is a sentence, not a restatement of the label: `t` names the control and
    `d` says what it does.  An empty or one-word `d` is a card that helps nobody."""
    html = page.read_text(encoding="utf-8")
    keys = sorted({k for k in re.findall(r'data-hint="([^"]+)"', html)
                   if not DYNAMIC.match(k) and not CODE.search(k)})
    got = hints_of(page, tmp_path, keys)
    thin = sorted(k for k, v in got.items()
                  if v and (len(v["d"].split()) < 4 or not v["t"]))
    assert not thin, f"these hover cards say nothing useful: {thin}"
