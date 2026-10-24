"""What a programme asks of a broadcast plane, and what R4d now says about holding still.

The accounting an author of the WISE/WISER line pointed at, made checkable:

* the deck's "12*2 + 4*2" is the two-way switch (p.19) -- a motion bank and a hold bank per
  electrode role, WISE's N_set = 2 (arXiv:2305.12773, Table I) -- not a differential pair;
* a loaded site that HOLDS while its channel carries a motion needs that switch, on a path
  or off it (R4d's hold clause);
* `k` distinct motions need `k` channel SETS, one channel per electrode role (R4d's
  counting clause), not `k` channels;
* the select bits a switched plane loads per cycle, and the time the 1:M shim demultiplexer
  charges (WISE Sec. V B 3: t_sc = M x t_ec), come out of a census of the programme.
"""

from __future__ import annotations

import json
import math
import sys
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from qccd import Machine  # noqa: E402
from qccd.arch import Architecture, load  # noqa: E402
from qccd.cost import corrected_model  # noqa: E402
from qccd.cost.hardware import WIRING_FLOORS, demux_tradeoff, hardware_report  # noqa: E402
from qccd.cost.waveforms import WaveformCensus, waveform_census  # noqa: E402
from qccd.ir.tsir import TSIR, Instruction, Participant, loop_shift  # noqa: E402
from qccd.verify import replay, verify  # noqa: E402
from qccd.verify.rules import motion_signature, motion_signatures  # noqa: E402

ARCH = ROOT / "arch"
HOLD = "a hold needs a switch"


# ------------------------------------------------------------------------ machines

def _ring(switches: bool = True) -> Architecture:
    """A 16-slot ring L0 (S0..S15) with docks A0, A4, A8, A12 on spurs V0..V12; broadcast
    wiring, the deck's roles and banks, a per-site switch or not."""
    doc = Machine.ring(8, 2, verticals=4, name="census_ring").arch.to_json()
    doc["control"]["channels"]["switch_per_site"] = switches
    return Architecture.from_json(doc)


def _prog(*steps) -> TSIR:
    """`steps`: ("init", {ion: site}), ("rotate",), ("move", cls, [(ion, src, dst, seg)]),
    ("gate", a, b), ("measure", ion)."""
    p = TSIR(name="census", arch_spec="census_ring")
    for st in steps:
        kind = st[0]
        if kind == "init":
            p.add(Instruction(type="init", id=p.next_id(), placement=dict(st[1]),
                              quanta={k: 0.0 for k in st[1]}))
        elif kind == "rotate":
            p.add(Instruction(type="simd", id=p.next_id(), cls="rotate_cw", mode="inter",
                              template=loop_shift("L0", 1), holds=("L0",)))
        elif kind == "move":
            p.add(Instruction(type="simd", id=p.next_id(), cls=st[1], mode="inter",
                              participants=tuple(Participant(i, s, d, via=(g,))
                                                 for i, s, d, g in st[2])))
        elif kind == "gate":
            p.add(Instruction(type="gate", id=p.next_id(), gate="MS", pairs=((st[1], st[2]),)))
        elif kind == "measure":
            p.add(Instruction(type="measure", id=p.next_id(), ions=(st[1],)))
    return p


LOOP = {f"d{i}": f"S{i}" for i in range(16)}


def _r4(arch, prog):
    return [v for v in verify(prog, arch, corrected_model(), check_metrics=False).rules.violations
            if v.rule == "R4"]


# ------------------------------------------------------------------- the plane's banks


def test_the_decks_x2_is_two_banks_not_a_differential_pair():
    """32 broadcast channels = 16 electrode roles x 2 banks: one motion and one hold."""
    plane = load(ARCH / "deck_unit_cell.arch.json").control_plane
    assert plane.roles_per_site == 6 + 6 + 4 == 16
    assert plane.pair == 2 and plane.banks == 2
    assert plane.n_shared_channels == plane.roles_per_site * plane.banks == 32
    assert set(plane.bank_class.values()) == {(0, 2)}, "broadcast: one class of sites"


def test_a_plane_that_declares_nothing_has_no_banks():
    m = Machine.ring(8, 2, 0, name="silent")
    ctl = dict(m.arch.control)
    ctl.pop("channels", None)
    m._rebuild(control=ctl)
    plane = m.arch.control_plane
    assert not plane.declared and plane.roles_per_site == 0 and plane.banks == 0


# ---------------------------------------------------------------------- the hold clause


def test_a_docked_ion_cannot_hold_through_a_rotation_without_a_switch():
    """The gap: the rotation is judged on L0 and the dock is off it, so nothing used to ask
    what the docked ancilla does.  On a broadcast plane its electrodes carry the rotation's
    waveform; without a switch it cannot be told to hold."""
    prog = _prog(("init", {**LOOP, "a0": "A0"}), ("rotate",))
    bad = _r4(_ring(switches=False), prog)
    assert len(bad) == 1, bad
    assert "loaded site A0" in bad[0].message and HOLD in bad[0].message
    assert "linear_h.all.0" in bad[0].message, "it names the channel"
    assert _r4(_ring(switches=True), prog) == [], "a switch selects the hold bank"


def test_an_empty_dock_is_not_a_site_holding():
    prog = _prog(("init", LOOP), ("rotate",))
    assert _r4(_ring(switches=False), prog) == []


def test_a_loop_rider_away_from_a_dock_holds_while_its_neighbour_docks():
    """The dock clause judges the riders BESIDE a dock; S1 has no spur, so it is the hold
    clause's: loaded, on an engaged channel, and not moving."""
    prog = _prog(("init", {"d0": "S0", "d1": "S1"}),
                 ("move", "dock", [("d0", "S0", "A0", "V0")]))
    bad = _r4(_ring(switches=False), prog)
    assert [("loaded site S1" in v.message, HOLD in v.message) for v in bad] == [(True, True)], bad
    assert _r4(_ring(switches=True), prog) == []


def test_a_site_the_path_clause_names_is_not_named_twice():
    """S3 is loaded and on L0 when S0 shifts along it: the path clause already reports it,
    in its own words, and the hold clause must not repeat it."""
    prog = _prog(("init", {"d0": "S0", "d3": "S3"}),
                 ("move", "shuttle", [("d0", "S0", "S1", "E0")]))
    bad = _r4(_ring(switches=False), prog)
    about_s3 = [v.message for v in bad if "S3" in v.message]
    assert len(about_s3) == len({v.message for v in bad}) >= 1
    assert all("all-or-nothing" in m for m in about_s3), "the path clause's message, unchanged"
    assert not any(HOLD in m for m in about_s3)
    # ...and the path clause reports per channel, so on 32 channels that is 32 messages
    assert len(bad) == 32


def test_a_merge_into_a_loaded_dock_is_acting_not_holding():
    """A0 holds an ancilla and d0 docks into it: the move ENTERS A0, so A0 acts."""
    prog = _prog(("init", {"d0": "S0", "a0": "A0"}),
                 ("move", "dock", [("d0", "S0", "A0", "V0")]))
    assert _r4(_ring(switches=False), prog) == []


# ------------------------------------------------------------------- the counting clause


def _three_rails(control: dict):
    """Three separate two-trap rails on no named path, pointing +x, -x and +y."""
    from qccd.arch.builder import DeviceBuilder

    b = DeviceBuilder("explicit")
    for sid, (x, y), (dx, dy) in (("E", (0.0, 0.0), (1.0, 0.0)),
                                  ("W", (5.0, 0.0), (-1.0, 0.0)),
                                  ("N", (10.0, 0.0), (0.0, 1.0))):
        b.site(sid + ".o", x, y, zone="trap")
        b.site(sid + ".e", x + dx, y + dy, zone="trap")
        b.segment(sid, sid + ".o", sid + ".e")
    m = Machine.from_device(b.build(), name="three")
    ctl = dict(m.arch.control)
    ctl["channels"] = control
    m._rebuild(control=ctl)
    return m


DECK_CHANNELS = {"grouping": "broadcast", "roles": {"linear_h": 6, "linear_v": 6, "junction": 4},
                 "differential": 2, "switch_per_site": True}


def _counting(m, rails):
    p = m.program("c").init({f"q{r}": f"{r}.o" for r in rails})
    p.simd("shuttle", [[f"q{r}", f"{r}.o", f"{r}.e", [r]] for r in rails])
    return [v for v in m.run(p, check_metrics=False).violations
            if v.rule == "R4" and "channel set" in v.message]


def test_distinct_motions_are_counted_in_channel_sets_not_channels():
    """32 channels are 2 sets of 16 roles: two motions fit, three do not -- where the
    old count let 32 different motions through."""
    m = _three_rails(DECK_CHANNELS)
    assert m.arch.control_plane.banks == 2
    assert _counting(m, ["E", "W"]) == []
    bad = _counting(m, ["E", "W", "N"])
    assert len(bad) == 1, bad
    assert "3 different ways" in bad[0].message and "2 channel set(s) of 16" in bad[0].message


def test_one_bank_fits_one_motion():
    m = _three_rails({**DECK_CHANNELS, "differential": 1})
    assert m.arch.control_plane.banks == 1
    assert _counting(m, ["E"]) == []
    assert len(_counting(m, ["E", "W"])) == 1


def test_row_wiring_counts_each_line_against_its_own_channels():
    """Under row wiring each line has its own bank: two lines may move differently, two
    sites of one line may not.  Counting every mover against one site's banks would fail
    the first case; the count is per class of sites that see the same channels."""
    from qccd.arch.builder import DeviceBuilder

    b = DeviceBuilder("explicit")
    for y in (0.0, 2.0):
        for name, x0, x1 in (("a", 0.0, 1.0), ("b", 3.0, 4.0)):
            b.site(f"{name}{y:g}.0", x0, y, zone="trap")
            b.site(f"{name}{y:g}.1", x1, y, zone="trap")
            b.segment(f"{name}{y:g}", f"{name}{y:g}.0", f"{name}{y:g}.1")
    m = Machine.from_device(b.build(), name="rows")
    ctl = dict(m.arch.control)
    ctl["channels"] = {"grouping": "row", "roles": {"linear": 1}, "switch_per_site": True}
    m._rebuild(control=ctl)
    plane = m.arch.control_plane
    assert plane.banks == 1 and len({c for c, _ in plane.bank_class.values()}) == 2

    def run(moves):
        p = m.program("r").init({ion: src for ion, src, _, _ in moves})
        p.simd("shuttle", [list(mv) for mv in moves])
        return [v for v in m.run(p, check_metrics=False).violations
                if v.rule == "R4" and "channel set" in v.message]

    right0 = ("p", "a0.0", "a0.1", ["a0"])
    left2 = ("q", "a2.1", "a2.0", ["a2"])
    left0 = ("r", "b0.1", "b0.0", ["b0"])
    assert run([right0, left2]) == [], "two lines, two banks"
    assert len(run([right0, left0])) == 1, "one line, one bank, two motions"


# ---------------------------------------------------------------------------- census


def test_motion_signatures_is_motion_signature_for_every_mover():
    arch = _ring()
    prog = _prog(("init", {**LOOP, "a0": "A0"}), ("rotate",),
                 ("move", "shuttle", [("d15", "S0", "S1", "E0"), ("a0", "A0", "S0", "V0")]))
    seen = []
    replay(prog, arch, corrected_model(), check_rules=False, keep_cycles=False,
           on_cycle=lambda v: seen.append(v) if v.moves else None)
    assert seen
    memo: dict = {}
    for v in seen:
        want = {m.ion: motion_signature(v, m.ion) for m in v.moves}
        assert motion_signatures(v) == want
        assert motion_signatures(v, cache=memo) == want
        assert motion_signatures(v, cache=memo) == want, "the memo answers the same"


def test_the_census_counts_motions_holds_and_reconfigurations():
    """rotate (1 motion + the docked a0 holding), dock (1 motion + 15 riders holding), two
    gates back to back (one reconfiguration), undock, measure (a second)."""
    prog = _prog(("init", {**LOOP, "a0": "A0"}), ("rotate",),
                 ("move", "dock", [("d15", "S0", "A0", "V0")]),
                 ("gate", "d15", "a0"), ("gate", "d15", "a0"),
                 ("move", "undock", [("d15", "A0", "S0", "V0")]),
                 ("measure", "a0"))
    c = waveform_census(prog, _ring())
    assert c.n_transport_cycles == 3
    assert c.max_signatures == 1 and c.max_concurrent == 2
    assert dict(c.concurrent_histogram) == {2: 3}
    assert c.n_cycles_with_holds == 3 and c.max_holds == 15
    assert c.n_gate_layers == 3 and c.n_reconfigurations == 2
    assert 0 < c.min_cycle_us <= c.median_cycle_us
    assert c.total_us == replay(prog, _ring(), corrected_model(), check_rules=False).total_us
    d = c.as_dict()
    assert d["concurrent_histogram"] == {"2": 3} and json.dumps(d)


def test_two_motions_and_a_hold_are_three_banks():
    prog = _prog(("init", {"d1": "S1", "a0": "A0", "d5": "S5"}),
                 ("move", "shuttle", [("d1", "S1", "S2", "E1"), ("a0", "A0", "S0", "V0")]))
    c = waveform_census(prog, _ring())
    assert (c.max_signatures, c.max_concurrent, c.max_holds) == (2, 3, 1)


# ------------------------------------------------------------------- hardware_report


BASE_KEYS = {"name", "scheme", "n_traps", "n_junctions", "n_segments", "trapping_zones",
             "total_capacity", "degree_histogram", "electrodes", "switches", "dacs",
             "dacs_broadcast", "dacs_compensation", "dacs_per_trap", "over_budget", "notes"}


@pytest.mark.parametrize("name", sorted(p.name.split(".")[0] for p in ARCH.glob("*.arch.json")))
def test_without_a_census_the_report_is_what_it_always_was(name):
    """The grader calls `hardware_report` without a census: same keys, same counts, same
    notes, whether or not the floors apply."""
    a = load(ARCH / f"{name}.arch.json")
    for kw in ({}, {"floors": WIRING_FLOORS}):
        plain = hardware_report(a, **kw)
        d = plain.as_dict()
        assert set(d) == BASE_KEYS
        assert d == hardware_report(a, census=None, **kw).as_dict()
        assert plain.waveforms is None and plain.select_bits_per_site is None
        assert not any(n.startswith(("waveforms:", "select:", "shims:")) for n in d["notes"])


def _census(**kw) -> WaveformCensus:
    base = dict(arch="x", program="p", n_transport_cycles=10, max_signatures=2,
                max_concurrent=3, concurrent_histogram={3: 10}, signature_histogram={2: 10},
                n_cycles_with_holds=10, max_holds=4, min_cycle_us=5.0, median_cycle_us=40.0,
                n_gate_layers=10, n_reconfigurations=4, total_us=1000.0)
    base.update(kw)
    return WaveformCensus(**base)


def test_a_census_only_adds_to_the_report():
    a = load(ARCH / "ring144_24v.arch.json")
    plain = hardware_report(a, floors=WIRING_FLOORS).as_dict()
    full = hardware_report(a, floors=WIRING_FLOORS, census=_census(max_concurrent=2,
                                                                   max_signatures=1)).as_dict()
    assert set(full) > BASE_KEYS
    assert {k: full[k] for k in BASE_KEYS if k != "notes"} == \
           {k: plain[k] for k in BASE_KEYS if k != "notes"}, "32 channels cover 2 banks"
    assert full["notes"][:len(plain["notes"])] == plain["notes"]


def test_select_bits_banks_and_shims_by_hand():
    """ring144_24v: 168 sites, 16 roles, 32 broadcast channels, 8 shims a trap through 1:100.
    A census of 2 motions + a hold = 3 banks:
      DACs        3 x 16 = 48 > 32 counted -> counted at 48
      select      ceil(log2 3) = 2 bits a site, 2 x 168 = 336 a cycle, / 5 us = 67.2 Mbit/s
      shims       t_sc = 100 x 3 us = 300 us; 4 reconfigurations = 1200 us; 10 layers = 3000 us
    """
    a = load(ARCH / "ring144_24v.arch.json")
    plain = hardware_report(a)
    rep = hardware_report(a, census=_census())
    assert rep.n_traps == 168
    assert rep.roles_per_bank == 16 and rep.dacs_dynamic_required == 48
    assert plain.dacs_broadcast == 32 and rep.dacs_broadcast == 48
    assert rep.dacs == plain.dacs + 16
    assert any("48" in n and "32" in n and n.startswith("waveforms:") for n in rep.notes)
    assert rep.select_bits_per_site == 2 == math.ceil(math.log2(3))
    assert rep.select_bits_per_cycle == 336
    assert rep.select_rate_mbit_s == pytest.approx(67.2)
    assert rep.demux_order == 100
    assert rep.shim_charge_us_per_layer == 300.0
    assert rep.shim_charge_total_us == 1200.0
    assert rep.shim_charge_total_us_every_layer == 3000.0
    assert rep.shared_channels is True
    d = rep.as_dict()
    assert d["select_rate_mbit_s"] == pytest.approx(67.2) and d["waveforms"]["max_concurrent"] == 3


def test_one_motion_and_a_hold_need_one_select_bit_and_fit_the_decks_32():
    rep = hardware_report(load(ARCH / "ring144_24v.arch.json"),
                          census=_census(max_concurrent=2, max_signatures=1))
    assert rep.dacs_dynamic_required == 32 == rep.dacs_broadcast
    assert rep.select_bits_per_site == 1 and rep.select_bits_per_cycle == 168


def test_without_a_switch_there_is_no_select_bit():
    doc = load(ARCH / "ring144_24v.arch.json").to_json(expanded=True)
    doc["control"]["channels"]["switch_per_site"] = False
    rep = hardware_report(Architecture.from_json(doc), census=_census())
    assert rep.select_bits_per_site == 0 and rep.select_bits_per_cycle == 0
    assert any(n.startswith("select:") and "no per-site switch" in n for n in rep.notes)


def test_the_shim_charge_time_is_a_document_field():
    doc = load(ARCH / "ring144_24v.arch.json").to_json(expanded=True)
    doc["control"]["wiring"]["shim_charge_us"] = 2.0
    rep = hardware_report(Architecture.from_json(doc), census=_census())
    assert rep.shim_charge_us_per_layer == 200.0 and rep.shim_charge_total_us == 800.0


def test_a_direct_plane_shares_no_bank():
    rep = hardware_report(load(ARCH / "grid9x9.arch.json"), census=_census())
    assert rep.shared_channels is False and rep.dacs_dynamic_required is None
    assert rep.select_bits_per_site == 0
    assert rep.demux_order == 1 and rep.shim_charge_us_per_layer == 3.0, "every shim its DAC"
    assert rep.dacs == hardware_report(load(ARCH / "grid9x9.arch.json")).dacs


def test_the_demux_trade_by_hand():
    """800 shims: the deck's ceil(n/M) and WISE's n/(M - 1), and t_sc = M x 3 us."""
    rows = {r["M"]: r for r in demux_tradeoff(800, _census())}
    assert [rows[m]["shim_dacs"] for m in (1, 16, 100, 128)] == [800, 50, 8, 7]
    assert [rows[m]["shim_dacs_wise"] for m in (1, 16, 100, 128)] == [800, 54, 9, 7]
    assert [rows[m]["t_sc_us"] for m in (1, 16, 100, 128)] == [3.0, 48.0, 300.0, 384.0]
    assert rows[16]["charge_us_reconfigurations"] == 4 * 48.0
    assert rows[128]["charge_us_every_layer"] == 10 * 384.0
    assert "charge_us_reconfigurations" not in demux_tradeoff(800)[0]


# ------------------------------------------------- the board programme, when it is here

BOARD = ROOT / "Compiler" / "build" / "q06g"
RING = "ring288_h3_v36_o2_noswitch"
RING_PROG = BOARD / f"{RING}_a36_bridge_structured_xz_dockall_transport-fastest.cooled.tsir.json"


@pytest.mark.skipif(not RING_PROG.exists(), reason="the q06g board build is not in this checkout")
def test_the_switchless_board_ring_fails_r4d_and_passes_with_switches():
    """One motion and a hold in every transport cycle; the docked ancillas hold through the
    rotations and the loop riders away from the docks hold through dock and undock.  The
    same programme on the same ring with a per-site switch is drivable."""
    arch = load(BOARD / f"{RING}.arch.json")
    prog = TSIR.load(RING_PROG)
    c = waveform_census(prog, arch)
    assert c.max_signatures == 1 and c.max_concurrent == 2
    held = replay(prog, arch, corrected_model(), check_rules=True, keep_cycles=False,
                  only_rules=["R4d"]).rules.violations
    assert held and all(HOLD in v.message for v in held)
    cls = {i.id: i.cls for i in prog.instructions}
    assert set(Counter(cls[v.instr_id] for v in held)) == {"rotate_cw", "dock", "undock"}
    doc = arch.to_json(expanded=True)
    doc["control"]["channels"]["switch_per_site"] = True
    switched = Architecture.from_json(doc)
    assert replay(prog, switched, corrected_model(), check_rules=True, keep_cycles=False,
                  only_rules=["R4d"]).rules.violations == []
