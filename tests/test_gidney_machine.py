"""The ion machine for FormalRV's Gidney 2025 listing (tools/gidney_machine.py, docs/FULLSTACK_PLAN.md S10)."""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import gidney_machine as G  # noqa: E402

from qccd.fullstack.scale.lattice import Costs  # noqa: E402


def fields(m: str) -> dict:
    head = m.splitlines()[0][len("machine {"):-1]
    return {k.strip(): int(v.split()[0]) for k, v in (e.split(":", 1) for e in head.split(","))}


def test_formalrv_round_is_ours():
    # FormalRV's routed round: reset, one route, an H, five CX slots, the measurement
    assert Costs().round_us * 1000 == G.ROUND_NS
    assert G.RESET_NS + G.ROUTE_NS + G.GATE1Q + 5 * G.GATE2Q + G.MEASURE_NS == G.ROUND_NS


def test_services_follow_the_papers_rules():
    f = fields(G.machine(800))
    gates = 800 * 120
    assert f["qubits"] == 800 * 288 and f["max_parallel_gates"] == gates
    # each measure ion reset and read out at ceil(1000 / t) per us, as the VM counts it: the paper's
    # 4 x gates at 250 ns and 2 x gates at 550 ns
    assert math.ceil(1000 / 250) == 4 and math.ceil(1000 / 550) == 2
    assert f["ancilla_bandwidth"] == gates and f["classical_data_bandwidth"] == gates
    assert f["decoders"] == 1550 and f["factories"] == 6
    assert f["decoder_latency_ns"] == G.ROUND_NS and f["magic_state_latency_ns"] == 150 * G.ROUND_NS


def test_layout_and_spec():
    assert G.layout().splitlines() == [
        "Cold YokedSurface2D_d5_w16_ws d=20 wires=[0, expBase + m) stream-to=Hot2[accBase - 6, accBase)",
        "Hot2 surface d=11 wires=[expBase + m, top)"]
    spec = G.sysspec("gadget gA[x: 1](b):\ngadget surfaceRound__S[x: 1](b):\n", G.machine())
    assert spec.count("spec ") == 2 and "spec gA:" in spec and "surfaceRound" not in spec
