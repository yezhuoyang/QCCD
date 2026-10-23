"""The code-aware schedule bridge (Codesign q06): generator flags, the ESM circuit check,
the structured placement family, and the phased binder.

Fast: no OCaml, no Lean.  The end-to-end path (schedule -> QASM -> `qccdc rotate
--placement` -> rules -> R10) is exercised by `Codesign/scripts/q06_campaign.py`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "Codesign" / "scripts"))

from qccd.arch import load  # noqa: E402
from qccd.arch.generators import ring  # noqa: E402
from qccd.codes.bb import gross_code  # noqa: E402
from qccd.compile.order import bind_dynamic  # noqa: E402
from qccd.compile.place import interleaved_seed, structured_seeds  # noqa: E402

GEN = ROOT / "Compiler" / "bridge" / "gen_bb144.py"
pytest.importorskip("stim")


def _gen(tmp_path: Path, *args: str) -> Path:
    out = tmp_path / "bb.qasm"
    subprocess.run([sys.executable, str(GEN), "-o", str(out), *args], check=True,
                   capture_output=True, text=True, cwd=str(ROOT))
    return out


def test_default_qasm_is_unchanged_and_measures_the_stabilizers(tmp_path):
    from esm_check import check

    q = _gen(tmp_path, "--ancillas", "24")
    shipped = (ROOT / "Compiler" / "examples" / "bb144_esm.qasm").read_text(encoding="utf-8")
    assert q.read_text(encoding="utf-8").splitlines() == shipped.splitlines()
    r = check(q, verbose=False)
    assert r["ok"] and r["repeatable"] and r["probes_wrong"] == 0
    assert r["n_resets"] == 144 - 24


def test_steady_state_resets_before_every_check(tmp_path):
    from esm_check import check

    q = _gen(tmp_path, "--ancillas", "24", "--steady-state")
    r = check(q, verbose=False)
    assert r["ok"] and r["n_resets"] == 144


def test_esm_check_rejects_an_inconsistent_xz_interleaving(tmp_path):
    """Swap the order of one X check and one Z check that share two data qubits on ONE
    of those qubits only: the circuit still has every gate and passes R10's shape, but
    the Z outcome picks up the X ancilla and the probe test must fail."""
    from esm_check import check

    q = _gen(tmp_path, "--ancillas", "144")
    lines = q.read_text(encoding="utf-8").splitlines()
    code = gross_code()
    x = next(c for c in code.checks if c.type == "X")
    z = next(c for c in code.checks if c.type == "Z" and len(set(c.members) & set(x.members)) == 2)
    d = sorted(set(x.members) & set(z.members))[0]
    kx, kz = code.checks.index(x), code.checks.index(z)
    ix = lines.index(f"cx q[{144 + kx}],q[{d}];")
    iz = lines.index(f"cx q[{d}],q[{144 + kz}];")
    # move the Z contact on `d` before the X contact on `d`, leaving the other shared
    # qubit in the original (X first) order
    line = lines.pop(iz)
    lines.insert(ix, line)
    bad = tmp_path / "bad.qasm"
    bad.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = check(bad, verbose=False)
    assert not r["ok"]


def test_structured_family_contains_the_interleaved_seed_and_is_injective():
    code = gross_code()
    fam = structured_seeds(code, 144)
    assert fam["a12_b2"] == interleaved_seed(code)
    for slot_of in fam.values():
        assert len(set(slot_of.values())) == code.n


def test_phased_binder_puts_every_x_contact_before_every_z_contact():
    from bb_schedule import bind_phased

    code = gross_code()
    docks = list(range(0, 144, 6))
    b = bind_phased(code.checks, interleaved_seed(code), docks, 144)
    last_x = max(off for a in b.assignments if a.check.type == "X" for off, _ in a.contacts)
    first_z = min(off for a in b.assignments if a.check.type == "Z" for off, _ in a.contacts)
    assert last_x < first_z
    assert len(b.assignments) == 144


def test_bind_dynamic_t_start_keeps_alignment():
    """A contact at absolute offset t aligns rider and dock: (slot + t) % cap == dock."""
    code = gross_code()
    docks = list(range(0, 144, 6))
    slot_of = interleaved_seed(code)
    b = bind_dynamic(code.checks, slot_of, docks, 144, t_start=131)
    for a in b.assignments:
        assert a.start >= 131
        for off, q in a.contacts:
            assert (slot_of[q] + off) % 144 == a.dock


def test_ring_dock_offset_moves_every_dock_and_is_validated():
    d = ring(72, 2, 24, dock_offset=3)
    docks = sorted(int(n.id[1:]) for n in d.nodes.values() if n.id.startswith("A"))
    assert docks == [3 + 6 * k for k in range(24)]
    assert d.params["dock_offset"] == 3
    with pytest.raises(Exception):
        ring(72, 2, 24, dock_offset=6)


def test_schedule_export_round_trips_through_the_generator(tmp_path):
    from bb_schedule import schedule, write
    from esm_check import check

    s = schedule(ROOT / "arch" / "ring144_24v.arch.json", 24, placement="interleaved",
                 refine_steps=0)
    sp, pp = write(s, tmp_path / "ring")
    pl = json.loads(pp.read_text(encoding="utf-8"))
    assert len(pl["loop"]) == 144 and len(pl["docks"]) == 24
    assert len(set(pl["loop"].values())) == 144
    q = _gen(tmp_path, "--schedule", str(sp), "--steady-state")
    r = check(q, verbose=False)
    assert r["ok"] and r["n_resets"] == 144 and r["n_ancillas"] == 24
    assert s["stats"]["contacts"] == 864


# --------------------------------------------------------------------- broadcast (q06d)


def _noswitch_arch(tmp_path: Path):
    doc = json.loads((ROOT / "arch" / "ring144_24v.arch.json").read_text(encoding="utf-8"))
    doc["control"]["channels"]["switch_per_site"] = False
    doc["name"] = "ring144_24v_noswitch"
    p = tmp_path / "ring144_24v_noswitch.arch.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return load(str(p))


def _round(arch, dock_sites):
    """A tiny program: rotate the loop by one, dock the riders beside `dock_sites`."""
    from qccd.ir.tsir import TSIR, Instruction, Participant, loop_shift

    prog = TSIR(name="t", arch_spec="arch/ring144_24v.arch.json")
    placement = {f"d{s}": f"S{s}" for s in range(144)}
    placement.update({f"a{s}": f"A{s}" for s in range(0, 144, 6)})
    prog.add(Instruction(type="init", id=prog.next_id(), placement=placement,
                         quanta={k: 0.0 for k in placement}))
    prog.add(Instruction(type="simd", id=prog.next_id(), cls="rotate_cw", mode="inter",
                         template=loop_shift("L0", 1), holds=("L0",)))
    prog.add(Instruction(type="simd", id=prog.next_id(), cls="dock", mode="inter",
                         participants=tuple(Participant(f"d{(s - 1) % 144}", f"S{s}", f"A{s}",
                                                        via=(f"V{s}",)) for s in dock_sites)))
    return prog


def _r4_verdict(arch, prog):
    from qccd.cost import corrected_model
    from qccd.verify import verify

    rep = verify(prog, arch, corrected_model(), check_metrics=False)
    return rep.rules.summary()


def test_partial_dock_is_drivable_with_switches_and_not_without(tmp_path):
    with_sw = load(str(ROOT / "arch" / "ring144_24v.arch.json"))
    without = _noswitch_arch(tmp_path)
    prog = _round(with_sw, [0, 6, 12])              # 3 of 24 docks act
    assert "R4" not in _r4_verdict(with_sw, prog)["failed"]
    assert "R4" in _r4_verdict(without, prog)["failed"]


def test_full_dock_and_full_rotation_are_drivable_without_switches(tmp_path):
    """Every rider beside a dock docks, and every loaded loop site rotates: nothing on the
    path idles, so a switchless channel drives it -- the ancilla sites off the loop get no
    verdict, by the rule's own scope."""
    without = _noswitch_arch(tmp_path)
    prog = _round(without, list(range(0, 144, 6)))  # all 24 docks act
    assert "R4" not in _r4_verdict(without, prog)["failed"]


def test_a_lattice_move_needs_a_channel_per_direction(tmp_path):
    """R4d judged nothing on a device with no named loop, and a grid has none.

    The gap was real -- a lattice under broadcast wiring emitted the SAME programme as
    under direct, 57 DACs against 12,480 -- but the first clause written for it asked the
    wrong question, and this test pins the corrected one (`Codesign/findings/q06i` §7).

    That clause asked, per channel group, whether the sites sharing it were being driven
    two ways at once.  No device document in this repository partitions its sites across
    channels: each declared channel is an all-sites group, because the documents record a
    channel COUNT and a per-site switch and never which sites a channel reaches.  So the
    question always found a conflict as soon as two ions moved differently ANYWHERE, which
    failed the shipped `deck_unit_cell` on a walk that turns a corner.

    What survives is the counting claim, which those documents do support: `k` distinct
    lab-frame directions need `k` channels, whatever the layout and whatever the switch
    assignment.  So east-and-west together is legal on the 32-channel WISE block and
    illegal only where the channels actually run out -- both pinned below.
    """
    from qccd.cost import corrected_model
    from qccd.ir.tsir import TSIR, Instruction, Participant
    from qccd.verify import verify

    doc = json.loads((ROOT / "arch" / "deck_unit_cell.arch.json").read_text(encoding="utf-8"))
    doc["geometry"] = {"generator": "grid", "params": {"a": 5, "b": 5}}
    doc["name"] = "grid5x5_bcast"
    p = tmp_path / "g.arch.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    arch = load(str(p))

    sites = [n for n in arch.device.nodes.values() if n.kind == "site"]
    by_pos = {n.id: n.pos for n in arch.device.nodes.values()}

    def neighbour(site, sign):
        """A trap two hops along x, and the two segments that reach it through the
        junction between them -- `via` names segments, not nodes."""
        for sid in arch.device.incidence[site]:
            j = arch.device.segments[sid].other(site)
            if arch.device.nodes[j].kind != "junction":
                continue
            if by_pos[j][1] != by_pos[site][1] or (by_pos[j][0] - by_pos[site][0]) * sign <= 0:
                continue
            for sid2 in arch.device.incidence[j]:
                t = arch.device.segments[sid2].other(j)
                if t != site and arch.device.nodes[t].kind == "site"                         and by_pos[t][1] == by_pos[site][1]:
                    return (sid, sid2), t
        return None

    east = [(s.id, neighbour(s.id, +1)) for s in sites if neighbour(s.id, +1)][:1]
    west = [(s.id, neighbour(s.id, -1)) for s in sites
            if neighbour(s.id, -1) and s.id != east[0][0]][:1] if east else []
    if not (east and west):
        pytest.skip("no east/west pair on this lattice")

    def prog_with(moves):
        prog = TSIR(name="t", arch_spec="arch/grid5x5_bcast.arch.json")
        placement = {f"q{i}": s for i, (s, _) in enumerate(moves)}
        prog.add(Instruction(type="init", id=prog.next_id(), placement=placement,
                             quanta={k: 0.0 for k in placement}))
        prog.add(Instruction(
            type="simd", id=prog.next_id(), cls="shuttle", mode="inter",
            participants=tuple(Participant(f"q{i}", s, t, via=segs)
                               for i, (s, (segs, t)) in enumerate(moves))))
        return prog

    assert "R4" not in _r4_verdict(arch, prog_with(east))["failed"],         "one direction is one waveform"
    # 32 declared channels, two directions wanted: legal, and the reason the first
    # version of this clause was withdrawn rather than kept
    assert len(arch.control_plane.groups) == 32
    assert "R4" not in _r4_verdict(arch, prog_with(east + west))["failed"],         "two directions fit in 32 channels; which channel drives which site is not judged"

    # the same two moves on a device that declares ONE channel: now the count decides
    doc["control"]["channels"] = {"grouping": "broadcast", "differential": 1,
                                  "roles": {"linear_h": 1}, "switch_per_site": True}
    lean = tmp_path / "lean.arch.json"
    lean.write_text(json.dumps(doc), encoding="utf-8")
    thin = load(str(lean))
    assert len(thin.control_plane.groups) == 1
    assert "R4" not in _r4_verdict(thin, prog_with(east))["failed"],         "one direction still fits one channel"
    assert "R4" in _r4_verdict(thin, prog_with(east + west))["failed"],         "east and west at once want two channels and the device declares one"
