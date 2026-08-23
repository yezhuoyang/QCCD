"""The demo layer: every shipped device renders, and the page agrees with the verifier.

The viewer replays the program *in the browser* so that a 2672-cycle rotation is one
frame entry rather than 385k move records. That means the page has its own accumulator,
and an accumulator that can silently drift from the verifier is worse than no viewer --
so the page carries a checksum and says out loud when it disagrees. These tests mirror
the page's arithmetic in Python and assert the agreement holds, which is what makes the
in-page self-check meaningful rather than decorative.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd.arch import load  # noqa: E402
from qccd.cost import corrected_model, deck_model  # noqa: E402
from qccd.cost.hardware import deck_unit_cell_report, hardware_report  # noqa: E402
from qccd.verify import replay  # noqa: E402
from qccd.viz import build_view_model, render_html  # noqa: E402

from qccd.compile import build  # noqa: E402
from qccd.compile.programs import walk as walk_program  # noqa: E402

ARCH_DIR = ROOT / "arch"
DEVICES = sorted(p.stem.replace(".arch", "") for p in ARCH_DIR.glob("*.arch.json"))


def _arch(name):
    return load(ARCH_DIR / f"{name}.arch.json")


# --------------------------------------------------------------- device coverage


def test_the_deck_architectures_are_all_present():
    """Every architecture the deck shows, plus PLAN §8's reference set."""
    expected = {
        "ring144_24v",        # the shipped 24-ancilla design
        "cyclone_base",       # Cyclone ring, deck p.9
        "cyclone_dual_loop",  # deck p.12, data loop still + ancilla loop rotating
        "ladder_2x72",        # deck p.5/16/18, rails and highways
        "h2_racetrack",       # deck p.6 IonQ WISE / Quantinuum H2 loop
        "grid9x9",            # deck p.7, baseline grid QCCD
        "deck_unit_cell",     # deck p.2/4/19-20, the unit-cell array
        "stationary_chain",   # the no-transport breakeven baseline
        "chain72",            # the unrolled control
    }
    have = {_arch(d).name for d in DEVICES}
    assert expected <= have, f"missing: {sorted(expected - have)}"


@pytest.mark.parametrize("device", DEVICES)
def test_every_device_runs_a_program_and_renders(device, tmp_path):
    arch = _arch(device)
    prog = build(arch, "walk")
    res = replay(prog, arch, corrected_model(), check_rules=True)
    assert res.rules.ok(), (device, res.rules.summary())
    out = render_html(arch, prog, res, corrected_model(), tmp_path / f"{device}.html")
    assert out.exists() and out.stat().st_size > 4000


@pytest.mark.parametrize("device", DEVICES)
def test_the_page_replay_agrees_with_the_verifier(device):
    """Mirror the page's accumulator in Python and require exact agreement.

    If this drifts, the animation is telling a different story from the numbers.
    """
    arch = _arch(device)
    prog = build(arch, "walk")
    model = corrected_model()
    res = replay(prog, arch, model, check_rules=False)
    view = build_view_model(arch, prog, res, model)
    assert _page_quanta(view) == pytest.approx(view["checksum"], abs=1e-9)


def _page_quanta(view: dict) -> dict:
    """Exactly the arithmetic in the page's `applyFrame`, in Python."""
    ph, arch = view["physics"], view["arch"]
    deg = {n["id"]: n["deg"] for n in arch["nodes"]}
    loops = arch["loops"]
    shuttle_q = ph["shuttle"]["quanta"] if ph["shuttle"] else 0.0
    split_q = ph["split"]["quanta"] if ph["split"] else 0.0
    merge_q = ph["merge"]["quanta"] if ph["merge"] else 0.0

    def junction_q(d):
        e = ph["junction_by_degree"].get(str(d))
        return e["quanta"] if e else 0.0

    pos: dict[str, str] = {}
    q: dict[str, float] = {}
    for f in view["program"]["frames"]:
        if f["type"] == "init":
            for k, v in f["place"].items():
                pos[k], q[k] = v, 0.0
        elif f["type"] == "cool":
            for i in (f.get("ions") or list(pos)):
                q[i] = 0.0
        elif f["type"] == "simd":
            if "shift" in f:
                loop, delta = f["shift"]
                seq = loops.get(loop) or []
                if not seq:
                    continue
                k = len(seq)
                step = 1 if delta >= 0 else -1
                idx = {n: i for i, n in enumerate(seq)}
                for _ in range(abs(delta)):
                    moved = {ion: seq[(idx[p] + step) % k]
                             for ion, p in pos.items() if p in idx}
                    for ion, dst in moved.items():
                        pos[ion] = dst
                        q[ion] = q.get(ion, 0.0) + shuttle_q + junction_q(deg[dst])
            elif "moves" in f:
                ent = f.get("entails") or []
                for ion, path in f["moves"]:
                    pos[ion] = path[-1]
                    dq = sum(shuttle_q + junction_q(deg[n]) for n in path[1:])
                    if "split" in ent:
                        dq += split_q
                    if "merge" in ent:
                        dq += merge_q
                    q[ion] = q.get(ion, 0.0) + dq
    return {k: round(v, 6) for k, v in q.items()}


def test_the_page_agrees_on_the_shipped_schedule_too():
    arch = _arch("ring144_24v")
    prog = build(arch, "deck")
    model = corrected_model()
    res = replay(prog, arch, model, check_rules=False)
    view = build_view_model(arch, prog, res, model)
    assert _page_quanta(view) == pytest.approx(view["checksum"], abs=1e-9)
    # and the frame list stays small: template compression, not a 385k-move trace
    assert len(view["program"]["frames"]) < 2500


def test_a_rendered_page_is_self_contained_and_parses(tmp_path):
    arch = _arch("ring144_24v")
    prog = build(arch, "deck")
    res = replay(prog, arch, deck_model(), check_rules=True)
    out = render_html(arch, prog, res, deck_model(), tmp_path / "p.html")
    txt = out.read_text(encoding="utf-8")
    # no external fetch of any kind: the SVG namespace URI is a name, not a request,
    # so the check is on things that actually load
    for bad in ("<script src=", "<link ", "@import", "fetch(", "XMLHttpRequest",
                "<img src=", 'href="http'):
        assert bad not in txt, f"page is not self-contained: {bad}"
    data = re.search(r'<script id="data" type="application/json">(.*?)</script>',
                     txt, re.S)
    assert data, "no data block"
    d = json.loads(data.group(1))
    assert d["metrics"]["total_cost"] == 397184
    assert d["metrics"]["total_steps"] == 8808


# --------------------------------------------------------------- hardware report


def test_broadcast_wiring_keeps_the_dac_count_flat():
    """The same geometry, two wiring schemes: this is the whole WISE claim."""
    direct = hardware_report(_arch("grid9x9"))
    broadcast = hardware_report(_arch("deck_unit_cell"))
    assert direct.n_traps == broadcast.n_traps
    assert direct.electrodes == broadcast.electrodes
    assert direct.scheme == "direct" and broadcast.scheme == "wise"
    assert broadcast.dacs < direct.dacs / 100, (broadcast.dacs, direct.dacs)
    # direct wiring: one channel per site per role, so channels scale with sites
    assert direct.dacs > direct.n_traps


def test_h2_racetrack_has_no_junctions_at_all():
    """R18 applied to shipped hardware: a continuous RF null has no junction."""
    hw = hardware_report(_arch("h2_racetrack"))
    assert hw.n_junctions == 0
    assert _arch("h2_racetrack").device.summary()["n_corners"] == 4


def test_deck_unit_cell_formulas():
    """Deck p.19-20, verbatim -- the closed form our counts are checked against."""
    r = deck_unit_cell_report(3, 3)
    assert r["unit_cells"] == 9
    assert r["total_electrodes"] == 24 * 9
    assert r["total_switches"] == 48 * 9
    assert r["dacs_linear"] == 24 and r["dacs_junction"] == 8
    assert r["total_dacs"] == 24 + 8 + 1  # ceil(72/100)
    assert r["trapping_zones"] == 9 - 3
    big = deck_unit_cell_report(30, 30)
    assert big["dacs_linear"] == 24 and big["dacs_junction"] == 8
    assert big["total_dacs"] < big["total_electrodes"] / 100


def test_a_device_over_budget_says_so():
    arch = _arch("grid9x9")
    hw = hardware_report(arch)
    assert hw.dacs > int(arch.budget["max_dacs"])
    assert hw.over_budget and not hw.ok()


# --------------------------------------------------------------- walk program


def test_the_walk_never_parks_an_ion_on_a_junction():
    """Junctions have capacity 0 and R2 allows one ion at an instant, so a move goes
    trap to trap and transits whatever is between."""
    arch = _arch("grid9x9")
    prog = walk_program(arch, 4)
    dev = arch.device
    for instr in prog.instructions:
        for p in instr.participants:
            assert dev.nodes[p.src].kind == "site"
            assert dev.nodes[p.dst].kind == "site"
            assert len(p.via) >= 1
    res = replay(prog, arch, corrected_model(), check_rules=True)
    assert res.rules.ok(), res.rules.summary()


def test_the_walk_crosses_the_grids_x_junctions():
    arch = _arch("grid9x9")
    prog = walk_program(arch, 2)
    res = replay(prog, arch, corrected_model(), check_rules=False)
    assert sum(res.junction_transits.values()) > 0
    assert res.quanta_components["junction"] > 0
