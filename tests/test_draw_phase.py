"""The drawing clamps the animation phase, because everything downstream assumes it.

`phase` is a fraction of one frame.  The clock that produces it measures real time and can
hand the drawing a value outside [0, 1] honestly -- `tick` computes `(now - t0) / dur` from
a `t0` a programme change can leave in the future, and a queued animation frame can carry a
timestamp from before the click that started it.

Left unclamped it is not a cosmetic slip.  The single-hop ramp is `p*p*(3-2p)`, so a phase
of -0.55 becomes t = 1.24; the flier's swell term `4t(1-t)` then goes negative and every
ion is drawn with a NEGATIVE RADIUS -- 144 console errors per rotation on ring144_24v,
reported against the live site on 2026-09-17 -- and the same out-of-range t extrapolates
positions past the ends of the rails the ions are supposed to be on.
"""

from __future__ import annotations

import json
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

#: The prelude hands out the two page internals this asks about, plus a setter for the
#: playhead: `frame` and `phase` are `let` bindings of the page script, so only a closure
#: written INTO that scope can move them.
PRELUDE = (";globalThis.__E=EDITOR;"
           "globalThis.__drawAt=(f,ph)=>{frame=f;phase=ph;draw();return {frame:frame,phase:phase};};"
           "globalThis.__ions=()=>{const o=[];for(const i in IONP){const c=IONP[i].c;"
           "o.push([i,Number(c.attrs?c.attrs.r:c.getAttribute('r'))]);}return o;};")


@pytest.fixture(scope="module")
def page(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("phase") / "studio.html"
    subprocess.run([sys.executable, "-m", "qccd", "studio", "-o", str(out)],
                   cwd=ROOT, capture_output=True, timeout=900, check=True)
    return out


def drive(page: Path, tmp_path: Path, body: str) -> dict:
    js = tmp_path / "p.mjs"
    js.write_text(f"import {{ loadPage }} from '{SHIM}';\n"
                  f"loadPage(process.argv[2], {json.dumps(PRELUDE)});\n"
                  "const E = globalThis.__E, drawAt = globalThis.__drawAt, ions = globalThis.__ions;\n"
                  + body, encoding="utf-8")
    r = subprocess.run([NODE, str(js), str(page)], capture_output=True, text=True,
                       encoding="utf-8", timeout=600, cwd=ROOT)
    assert r.returncode == 0, f"{r.stdout}\n{r.stderr[-2500:]}"
    return json.loads(r.stdout)


#: Values the clock really can produce, and the two that are simply nonsense.
BAD_PHASES = [-0.55, -0.0001, 1.63, 2.0, 12.5]


def test_no_phase_can_make_an_ion_radius_negative(page, tmp_path):
    r = drive(page, tmp_path, """
E.newFromTemplate('ring144_24v');
E.pressTestDrive();
const out = { rows: [], n: 0 };
for (const ph of """ + json.dumps(BAD_PHASES + [0, 0.5, 1]) + """) {
  const st = drawAt(1, ph);
  const rs = ions().map(p => p[1]).filter(v => isFinite(v));
  out.n = Math.max(out.n, rs.length);
  out.rows.push({ asked: ph, clamped: st.phase, min: Math.min.apply(null, rs) });
}
console.log(JSON.stringify(out));
""")
    assert r["n"] > 100, f"the ring's ions were not drawn at all: {r}"
    for row in r["rows"]:
        assert row["min"] >= 0, f"phase {row['asked']} drew a radius of {row['min']}"
        assert 0.0 <= row["clamped"] <= 1.0, (
            f"phase {row['asked']} was left at {row['clamped']}; the drawing must clamp it")


def test_an_out_of_range_phase_draws_the_frame_end_it_is_nearest(page, tmp_path):
    """Clamping is not only about not crashing: -0.2 must draw the frame's start and 1.2
    its end, so a late or early tick shows a real moment of the programme."""
    r = drive(page, tmp_path, """
E.newFromTemplate('ring144_24v');
E.pressTestDrive();
const at = ph => { drawAt(1, ph); return ions().map(p => [p[0], p[1]]); };
const lo = at(-0.2), zero = at(0), hi = at(1.2), one = at(1);
console.log(JSON.stringify({ lo: lo.slice(0, 6), zero: zero.slice(0, 6),
                             hi: hi.slice(0, 6), one: one.slice(0, 6) }));
""")
    assert r["lo"] == r["zero"], "a phase below 0 must draw the start of the frame"
    assert r["hi"] == r["one"], "a phase above 1 must draw the end of the frame"
